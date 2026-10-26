from __future__ import annotations

import logging
import multiprocessing
import os
import queue
import threading
import time
from typing import Any

from shiju.service.generation_service import GenerationService

from .api_client import WorkerApiClient


LOGGER = logging.getLogger(__name__)


class WorkerCancelled(RuntimeError):
    pass


class GpuWorker:
    def __init__(
        self,
        worker_id: str,
        client: WorkerApiClient,
        service: GenerationService | None,
        capabilities: dict[str, Any],
        *,
        heartbeat_seconds: int = 15,
        process_runner=None,
    ):
        self.worker_id = worker_id
        self.client = client
        self.service = service
        self.capabilities = capabilities
        self.heartbeat_seconds = heartbeat_seconds
        self._stopping = threading.Event()
        self.process_runner = process_runner
        self._jobs_executed = 0
        self._last_job_finished_monotonic = time.monotonic()

    def stop(self) -> None:
        self._stopping.set()

    def run_forever(self) -> None:
        idle_delay = 1.0
        while not self._stopping.is_set():
            try:
                job = self.client.claim(self.worker_id, self.capabilities)
            except Exception:
                LOGGER.exception("领取任务失败")
                self._stopping.wait(5)
                continue
            if not job:
                self._stopping.wait(idle_delay)
                idle_delay = min(idle_delay * 2, 10.0)
                continue
            idle_delay = 1.0
            LOGGER.info("领取任务: job_id=%s kind=%s", job["job_id"], job["kind"])
            self._execute(job)

    def run_resident(self, *, idle_poll_seconds: float = 2.0, on_idle=None, before_job=None) -> None:
        """Poll a durable queue while keeping the loaded model resident."""
        self._before_resident_job = before_job
        while not self._stopping.is_set():
            try:
                job = self.client.claim(self.worker_id, self.capabilities, wait_seconds=0)
            except Exception:
                LOGGER.exception("领取任务失败")
                self._stopping.wait(idle_poll_seconds)
                continue
            if not job:
                if on_idle:
                    on_idle(self.idle_seconds())
                self._stopping.wait(idle_poll_seconds)
                continue
            LOGGER.info("领取任务: job_id=%s kind=%s", job["job_id"], job["kind"])
            self._execute(job)
            self._last_job_finished_monotonic = time.monotonic()

    def idle_seconds(self) -> float:
        """Seconds since the last completed job, for an external lifecycle controller."""
        return max(0.0, time.monotonic() - self._last_job_finished_monotonic)

    def run_once(self, job_id: str) -> bool:
        """Claim and execute one selected local task."""
        claim_started = time.perf_counter()
        job = self.client.claim(
            self.worker_id, self.capabilities, job_id=job_id, wait_seconds=0,
        )
        if not job:
            return False
        job["_claim_seconds"] = time.perf_counter() - claim_started
        LOGGER.info("本地领取任务: job_id=%s kind=%s", job["job_id"], job["kind"])
        self._execute(job)
        return True

    def _execute(self, job: dict[str, Any]) -> None:
        job_id = job["job_id"]
        heartbeat_stop = threading.Event()
        cancel_requested = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            args=(job_id, heartbeat_stop, cancel_requested),
            daemon=True,
        )
        heartbeat.start()
        started_monotonic = time.perf_counter()
        started_wall = time.time()
        self._jobs_executed += 1
        api_before = self.client.timing_snapshot() if hasattr(self.client, "timing_snapshot") else {}
        sample_stop = threading.Event()
        rss_samples = {"process_rss_peak_sampled_bytes": None, "rss_sample_count": 0,
                       "rss_sample_interval_seconds": 0.25}
        def sample_rss():
            try:
                import psutil
                process = psutil.Process(os.getpid())
                while True:
                    value = process.memory_info().rss
                    rss_samples["process_rss_peak_sampled_bytes"] = max(rss_samples["process_rss_peak_sampled_bytes"] or 0, value)
                    rss_samples["rss_sample_count"] += 1
                    if sample_stop.wait(0.25): break
            except Exception:
                return
        sampler = threading.Thread(target=sample_rss, daemon=True)
        sampler.start()
        model_load_seconds = getattr(self.service, "_telemetry_base", {}).get("model_load_seconds") if self.service is not None else None
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats()
        except Exception:
            torch = None
        from shiju.generation.diagnostics import ResourceSampler, resident_resources
        resource_sampler = ResourceSampler(torch).start() if torch is not None else None
        resources_before = resident_resources(torch) if torch is not None else None
        try:
            if getattr(self, "_before_resident_job", None):
                self._before_resident_job()
            if self.service is not None:
                runner = getattr(getattr(self.service, "_engine", None), "runner", None)
                if runner is not None:
                    runner.cancel_requested = cancel_requested.is_set
                    runner.generation_measurements = []
                    runner.last_generation_telemetry = None
                    runner.last_generation_seconds = None
                    runner.last_first_token_seconds = None
                    runner.last_prompt_prepare_seconds = None
                if hasattr(self.service, "_event_callback"):
                    self.service._event_callback = lambda event_type, payload: self._report_candidate_event(
                        job_id, event_type, payload,
                    )
                result = self.service.execute(job["kind"], job["request"])
            elif self.process_runner is not None:
                result = self.process_runner(
                    job["kind"], job["request"],
                    lambda event_type, payload: self._report_candidate_event(job_id, event_type, payload),
                    cancel_requested.is_set,
                )
            else:
                raise RuntimeError("GPU Worker 未配置任务执行子进程")
            if cancel_requested.is_set():
                raise WorkerCancelled("任务已由用户取消")
            telemetry = self._telemetry_snapshot(started_monotonic, started_wall, model_load_seconds, torch)
            sample_stop.set()
            sampler.join(timeout=1)
            telemetry.update(rss_samples)
            telemetry["task_resources"] = resource_sampler.stop() if resource_sampler else None
            telemetry["resources_before_task"] = resources_before
            telemetry["telemetry_version"] = 2
            telemetry["claim_seconds"] = job.get("_claim_seconds")
            telemetry["instance_job_sequence"] = self._jobs_executed
            telemetry["model_reused_in_process"] = getattr(self, "_weights_reused", self.service is not None and self._jobs_executed > 1)
            telemetry["control_api_before_complete"] = {
                route: {key: value - api_before.get(route, {}).get(key, 0) for key, value in counters.items()}
                for route, counters in (self.client.timing_snapshot() if hasattr(self.client, "timing_snapshot") else {}).items()
            }
            telemetry.update(getattr(getattr(self, "service", None), "_telemetry_base", {}))
            if isinstance(result, dict):
                if self.process_runner is not None:
                    telemetry.update(result.get("telemetry") or {})
                runner = getattr(getattr(self.service, "_engine", None), "runner", None)
                if runner is not None:
                    telemetry["generation_seconds"] = getattr(runner, "last_generation_seconds", None)
                    telemetry["first_token_seconds"] = getattr(runner, "last_first_token_seconds", None)
                    telemetry["prompt_prepare_seconds"] = getattr(runner, "last_prompt_prepare_seconds", None)
                    telemetry["last_generation"] = getattr(runner, "last_generation_telemetry", None)
                    telemetry["streaming_generations"] = getattr(runner, "generation_measurements", [])
                result["telemetry"] = telemetry
            complete_started = time.perf_counter()
            self.client.complete(job_id, self.worker_id, result)
            if getattr(self, 'on_success', None):
                try:
                    self.on_success(job, result)
                except Exception:
                    LOGGER.exception('生成成功，但配对记忆保存失败；保留本次成功结果')
            LOGGER.info("任务完成接口耗时: job_id=%s complete_ack_seconds=%.6f worker_until_ack_seconds=%.6f",
                        job_id, time.perf_counter() - complete_started, time.perf_counter() - started_monotonic)
            LOGGER.info("任务完成: job_id=%s", job_id)
        except WorkerCancelled:
            self.client.acknowledge_cancel(job_id, self.worker_id)
            LOGGER.info("任务已取消: job_id=%s", job_id)
        except Exception as exc:
            LOGGER.exception("任务执行失败", extra={"job_id": job_id})
            code = str(getattr(exc, "code", "WORKER_EXECUTION_ERROR"))
            self.client.fail(
                job_id,
                self.worker_id,
                code,
                str(exc),
                retryable=code in {"CUDA_RUNTIME_ERROR", "CONTROL_PLANE_UNAVAILABLE"},
            )
        finally:
            if resource_sampler:
                resource_sampler.stop()
            sample_stop.set()
            sampler.join(timeout=1)
            heartbeat_stop.set()
            heartbeat.join(timeout=1)

    @staticmethod
    def _telemetry_snapshot(started_monotonic, started_wall, model_load_seconds, torch_module):
        try:
            import psutil
            rss_bytes = psutil.Process(os.getpid()).memory_info().rss
        except Exception:
            rss_bytes = None
        gpu_allocated = gpu_reserved = gpu_peak_allocated = gpu_peak_reserved = None
        if torch_module is not None:
            try:
                if torch_module.cuda.is_available():
                    torch_module.cuda.synchronize()
                    gpu_allocated = torch_module.cuda.memory_allocated()
                    gpu_reserved = torch_module.cuda.memory_reserved()
                    gpu_peak_allocated = torch_module.cuda.max_memory_allocated()
                    gpu_peak_reserved = torch_module.cuda.max_memory_reserved()
            except Exception:
                pass
        return {
            "worker_execution_seconds": max(0.0, time.perf_counter() - started_monotonic),
            "worker_execution_includes_model_load": False if model_load_seconds is not None else None,
            "worker_execution_includes_complete_ack": False,
            "model_load_seconds": model_load_seconds,
            "worker_started_at": started_wall,
            "process_rss_bytes": rss_bytes,
            "gpu_allocated_bytes": gpu_allocated,
            "gpu_reserved_bytes": gpu_reserved,
            "gpu_peak_allocated_bytes": gpu_peak_allocated,
            "gpu_peak_reserved_bytes": gpu_peak_reserved,
            "gpu_memory_scope": "pytorch_allocator_only_not_total_device_usage",
        }

    def _report_candidate_event(self, job_id: str, event_type: str, payload: dict[str, Any]) -> None:
        ordinal = int(payload.get("ordinal", 0) or 0)
        if ordinal < 1:
            return
        fields: dict[str, Any] = {"attempt": int(payload.get("attempt", 0) or 0)}
        if event_type == "candidate.started":
            fields.update(status="running", partial_text="", error="", started_at=time.time())
        elif event_type == "candidate.delta":
            fields.update(status="running", partial_text=str(payload.get("partial_text") or ""))
        elif event_type == "candidate.retry":
            fields.update(status="retrying", partial_text="", error=str(payload.get("error") or "协议校验失败"))
        elif event_type == "candidate.completed":
            fields.update(status="succeeded", partial_text=str(payload.get("text") or payload.get("content") or ""),
                          raw_text=str(payload.get("raw_output") or ""), title=payload.get("title"),
                          content=payload.get("content"), error="", finished_at=time.time())
        elif event_type == "candidate.failed":
            fields.update(status="failed", partial_text="", error=str(payload.get("error") or "生成失败"), finished_at=time.time())
        else:
            return
        self.client.candidate_update(job_id, self.worker_id, ordinal, **fields)

    def _heartbeat_loop(self, job_id: str, stop: threading.Event, cancel_requested: threading.Event) -> None:
        while not stop.wait(self.heartbeat_seconds):
            try:
                if self.client.heartbeat(job_id, self.worker_id):
                    cancel_requested.set()
                    return
            except Exception:
                LOGGER.exception("任务心跳失败", extra={"job_id": job_id})
