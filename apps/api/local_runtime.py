"""Local Agent preferences and the owned poetry worker lifecycle."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from .environment import inspect_environment, assess_model, python_process_env
from apps.local_network import local_control_url

from fastapi import HTTPException


DEFAULTS = {"default_model": "", "instructions": "", "max_tool_rounds": 8,
            "model_path": "", "quantization": "auto", "auto_start": False, "idle_unload_seconds": 300, "allow_unverified": False}


def read_config(directory: Path) -> dict:
    path = directory / "runtime.json"
    config = DEFAULTS | (json.loads(path.read_text(encoding="utf-8")) if path.exists() else {})
    config["quantization"] = "auto"
    return config


from apps.launcher.model_catalog import validate_model


class LocalRuntime:
    def __init__(self, directory, project_root, settings, jobs):
        self.directory = Path(directory)
        self.root = Path(project_root)
        self.settings = settings
        self.jobs = jobs
        self.process = None
        self.lock = threading.RLock()
        self.ready = self.directory / "local-worker.ready"
        self.log = self.directory / "local-worker.log"
        self.phase_file = self.directory / "local-worker-status.json"
        self.control_url = local_control_url(os.getenv("SHIJU_CONTROL_URL", "http://127.0.0.1:18763"))
        self.watched_jobs = set()
        self.plan = None
        self.start_error = ""

    def status(self):
        with self.lock:
            running = self.process is not None and self.process.poll() is None
            state = "ready" if running and self.ready.exists() else "loading" if running else "failed" if self.process or self.start_error else "stopped"
            error = self.start_error
            phase = {}
            if self.phase_file.exists():
                try:
                    phase = json.loads(self.phase_file.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    pass
            if running and phase.get("phase") == "released":
                state = "released"
            if state == "failed" and not self.start_error and self.log.exists():
                with self.log.open("rb") as handle:
                    handle.seek(max(0, self.log.stat().st_size - 6000))
                    error = handle.read().decode("utf-8", errors="replace")[-3000:]
            with self.jobs.database.connect() as db:
                external = db.execute("SELECT 1 FROM workers WHERE id <> ? AND last_seen_at >= ? LIMIT 1", ("local-poetry", time.time() - 45)).fetchone() is not None
            return {"state": state, "ready": state == "ready", "error": error,
                    "checks": phase.get("checks", []), "phase": phase.get("phase") if running else None, "message": self.start_error or phase.get("message", ""),
                    "plan": self.plan,
                    "external_worker": external}

    def start(self, control_url=None, *, for_submission=False):
        with self.lock:
            if self.process and self.process.poll() is None:
                return self.status()
            control_url = local_control_url(control_url or self.control_url)
            self.control_url = control_url
            config = read_config(self.directory)
            self.start_error = ""
            try:
                validate_model(config["model_path"])
                environment = inspect_environment(self.directory, refresh=True)
                self.plan = assess_model(config["model_path"], environment)
                deployed = Path(environment['inference_python']).parent / 'shiju-install.json'
                if deployed.is_file():
                    selected = json.loads(deployed.read_text(encoding='utf-8'))
                    if Path(selected.get('model', '')).resolve() != Path(config['model_path']).resolve():
                        raise ValueError('专用环境绑定的模型已变更，请通过部署工具重新生成清单并验证')
                    precision = selected.get('precision')
                    if precision not in environment.get('quantization', []):
                        raise ValueError('部署清单指定的量化方式未通过当前环境检测')
                    self.plan.update(quantization=precision, label='采用部署清单已验证的量化方式')
            except (ValueError, OSError) as exc:
                self.start_error = str(exc)
                raise
            python = environment["inference_python"]
            self.ready.unlink(missing_ok=True)
            self.phase_file.unlink(missing_ok=True)
            env = python_process_env(python)
            env.update(SHIJU_ALLOW_UNVERIFIED="1" if config.get("allow_unverified") or for_submission else "0", SHIJU_APPLICATION_PYTHON=sys.executable, SHIJU_RUNTIME_CONFIG=str(self.directory / "runtime.json"), SHIJU_MODEL_PATH=config["model_path"], SHIJU_QUANTIZATION=self.plan["quantization"],
                       SHIJU_CONTROL_URL=control_url,

                       SHIJU_READY_FILE=str(self.ready), SHIJU_WORKER_STATUS_FILE=str(self.phase_file), PYTHONIOENCODING="utf-8", SHIJU_ENV="development")
            with self.log.open("wb") as output:
                self.process = subprocess.Popen([python, "-m", "apps.gpu_worker.local_main"],
                    cwd=self.root, env=env, stdout=output, stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            return self.status()

    def stop(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
            self.process = None
            self.start_error = ""
            self.ready.unlink(missing_ok=True)

    def require_ready(self, control_url=None):
        """Ensure a consumer exists; jobs may queue while its model loads."""
        with self.lock:
            if control_url:
                self.control_url = control_url
            state = self.status()
            if state['ready'] or state['external_worker'] or state['state'] in {'released', 'loading'}:
                return
            if not read_config(self.directory)['model_path']:
                raise HTTPException(409, '尚未配置本地诗词生成模型，请在个人中心选择模型目录并保存；提交时会自动启动')
            try:
                self.start(for_submission=True)
            except (ValueError, OSError) as exc:
                raise HTTPException(409, f'本地生成器自动启动失败：{exc}') from exc

    def watch_submission(self, job_id):
        with self.lock:
            state = self.status()
            if state['external_worker'] or state['ready'] or state['state'] == 'released' or job_id in self.watched_jobs:
                return
            process = self.process
            self.watched_jobs.add(job_id)
        def monitor():
            try:
                deadline = time.monotonic() + 900
                while time.monotonic() < deadline:
                    if self.process is not process:
                        return
                    job = self.jobs.get(job_id)
                    if job.status != 'queued':
                        return
                    status = self.status()
                    if status['ready'] or status['state'] == 'released':
                        return
                    if status['state'] in {'failed', 'stopped'}:
                        self.jobs.fail_pending(job_id, 'LOCAL_WORKER_START_FAILED', status['message'] or status['error'] or '本地生成器启动失败，请检查模型与推理环境')
                        return
                    time.sleep(0.5)
                self.jobs.fail_pending(job_id, 'LOCAL_WORKER_START_TIMEOUT', '模型加载超过 15 分钟，请检查生成器日志后重试')
            finally:
                with self.lock:
                    self.watched_jobs.discard(job_id)
        threading.Thread(target=monitor, name='local-worker-start-monitor', daemon=True).start()


class LocalPoetryProvider:
    def __init__(self, runtime):
        self.runtime = runtime

    def ensure_running(self, job_id=None):
        try:
            self.runtime.require_ready()
        except HTTPException as exc:
            if job_id:
                self.runtime.jobs.fail_pending(job_id, 'LOCAL_WORKER_START_FAILED', str(exc.detail))
            raise
        if job_id:
            self.runtime.watch_submission(job_id)
        return True

    def status(self):
        return 'local'

    def request_stop(self):
        return False
