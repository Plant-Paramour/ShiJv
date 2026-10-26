"""A serial, persistent model process shared by consecutive local jobs."""
from __future__ import annotations

import multiprocessing
import logging
import os
import queue
import time

from .worker import WorkerCancelled


LOGGER = logging.getLogger(__name__)


class WorkerProcessError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _resident_entry(requests, events, cancelled, settings):
    logging.basicConfig(level=os.getenv("SHIJU_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from shiju.generation.engine import GenerationEngine
    from shiju.generation.model_runner import ModelRunner
    from shiju.generation.result import ModelSettings
    from shiju.service.generation_service import GenerationService

    runner = ModelRunner(ModelSettings(**settings["model"]))
    runner.cancel_requested = cancelled.is_set
    service = GenerationService(
        GenerationEngine(runner, rhyme_dir=settings["rhyme_dir"], meter_source=settings["meter_dir"]),
        event_callback=lambda name, payload: events.put(("event", name, payload)),
    )
    sequence = 0
    while True:
        command = requests.get()
        if command is None:
            return
        kind, payload = command
        sequence += 1
        runner.generation_measurements = []
        try:
            result = service.execute(kind, payload)
            result.setdefault("telemetry", {}).update(
                model_reused_in_process=sequence > 1,
                model_load_seconds=runner.load_seconds,
                model_process_job_sequence=sequence,
                model_process_pid=os.getpid(),
            )
            try:
                import torch
                from shiju.generation.diagnostics import resident_resources
                result["telemetry"].update(
                    runtime_diagnostics=runner.runtime_diagnostics,
                    model_process_resources=resident_resources(torch),
                )
            except Exception:
                LOGGER.exception("模型子进程诊断采集失败")
            events.put(("result", True, result))
        except Exception as exc:
            # Protocol errors and cancelled attempts discard their KV cache,
            # while the model weights and vocabulary remain resident.
            events.put(("result", False, (str(getattr(exc, "code", "WORKER_EXECUTION_ERROR")),
                                         type(exc).__name__, str(exc))))


class ResidentProcessRunner:
    def __init__(self, settings, *, context=None, target=None):
        self.settings = settings
        self.context = context or multiprocessing.get_context("spawn")
        self.target = target or _resident_entry
        self.process = None

    def _start(self):
        if self.process is not None and self.process.is_alive():
            return
        if self.process is not None:
            self.close()
        self.requests = self.context.Queue()
        self.events = self.context.Queue()
        self.cancelled = self.context.Event()
        self.process = self.context.Process(
            target=self.target, args=(self.requests, self.events, self.cancelled, self.settings),
            daemon=True,
        )
        self.process.start()
        LOGGER.info("常驻模型进程已启动: pid=%s；后续任务及协议重试复用此进程", self.process.pid)

    def __call__(self, kind, payload, on_event=None, should_cancel=None):
        self._start()
        self.cancelled.clear()
        self.requests.put((kind, payload))
        cancel_started = None
        callback_error = None
        while True:
            if should_cancel and should_cancel():
                self.cancelled.set()
                if cancel_started is None:
                    cancel_started = time.monotonic()
            if cancel_started is not None and time.monotonic() - cancel_started > 30:
                self.close()
                if callback_error is not None:
                    raise callback_error
                raise WorkerCancelled("任务取消后模型进程未响应，已关闭进程")
            try:
                message = self.events.get(timeout=0.25)
            except queue.Empty:
                if not self.process.is_alive():
                    raise WorkerProcessError("WORKER_PROCESS_FAILED", f"GPU 子进程异常退出，退出码 {self.process.exitcode}")
                continue
            if message[0] == "event":
                if on_event and callback_error is None:
                    try:
                        on_event(message[1], message[2])
                    except Exception as exc:
                        # Finish draining this task before accepting another;
                        # otherwise its result could be attributed to a new job.
                        callback_error = exc
                        self.cancelled.set()
                        cancel_started = time.monotonic()
                continue
            if callback_error is not None:
                raise callback_error
            if cancel_started is not None or (not message[1] and message[2][0] == "GENERATION_CANCELLED"):
                raise WorkerCancelled("任务已由用户取消")
            if not message[1]:
                code, name, detail = message[2]
                raise WorkerProcessError(code, f"{name}: {detail}")
            return message[2]

    def close(self):
        if self.process is None:
            return
        if self.process.is_alive():
            self.requests.put(None)
            self.process.join(timeout=5)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(timeout=5)
        for channel in (self.requests, self.events):
            channel.close()
            channel.cancel_join_thread()
        self.process = None
