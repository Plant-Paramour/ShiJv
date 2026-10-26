from __future__ import annotations
import json
import time
import threading
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request
from apps.http_transport import local_urlopen

class WorkerApiClient:

    def __init__(self, base_url, *, timeout=30):
        from apps.local_network import local_control_url
        self.base_url = local_control_url(base_url)
        self.timeout = timeout
        self._timing_lock = threading.Lock()
        self._timing = {}

    def timing_snapshot(self):
        with self._timing_lock:
            return {key: dict(value) for key, value in self._timing.items()}

    def claim(self, worker_id: str, capabilities: dict[str, Any], *, job_id: str | None=None, wait_seconds: int=15):
        payload = {'worker_id': worker_id, 'capabilities': capabilities, 'wait_seconds': wait_seconds}
        if job_id is not None:
            payload['job_id'] = job_id
        return self._post('/local/v1/workers/claim', payload)

    def heartbeat(self, job_id: str, worker_id: str) -> bool:
        result = self._post(f'/local/v1/jobs/{job_id}/heartbeat', {'worker_id': worker_id})
        return bool(result and result.get('cancel_requested'))

    def acknowledge_cancel(self, job_id: str, worker_id: str) -> None:
        self._post(f'/local/v1/jobs/{job_id}/cancelled', {'worker_id': worker_id})

    def complete(self, job_id: str, worker_id: str, result: dict[str, Any]) -> None:
        self._post(f'/local/v1/jobs/{job_id}/complete', {'worker_id': worker_id, 'result': result})

    def fail(self, job_id: str, worker_id: str, code: str, message: str, *, retryable: bool) -> None:
        self._post(f'/local/v1/jobs/{job_id}/fail', {'worker_id': worker_id, 'code': code, 'message': message, 'retryable': retryable})

    def candidate_update(self, job_id: str, worker_id: str, ordinal: int, **fields) -> None:
        self._post(f'/local/v1/jobs/{job_id}/candidates', {'worker_id': worker_id, 'ordinal': ordinal, **fields})

    def _post(self, path: str, payload: dict[str, Any]):
        category = path.rsplit('/', 1)[-1]
        started = time.perf_counter()
        failed = False
        try:
            return self._post_impl(path, payload)
        except Exception:
            failed = True
            raise
        finally:
            elapsed = time.perf_counter() - started
            with self._timing_lock:
                counter = self._timing.setdefault(category, {'calls': 0, 'seconds': 0.0, 'failures': 0})
                counter['calls'] += 1
                counter['seconds'] += elapsed
                counter['failures'] += int(failed)

    def _post_impl(self, path, payload):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        request = Request(self.base_url + path, data=body, method='POST', headers={'Content-Type': 'application/json; charset=utf-8'})
        try:
            with local_urlopen(request, timeout=self.timeout) as response:
                content = response.read()
                return json.loads(content.decode('utf-8')) if content else None
        except HTTPError as exc:
            exc.close()
            raise RuntimeError(f'本地任务请求失败：HTTP {exc.code}') from exc
