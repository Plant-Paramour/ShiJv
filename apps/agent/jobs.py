from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable, Protocol



class PoetryJobs(Protocol):
    def submit_generate(self, payload: Mapping[str, Any], idempotency_key: str) -> dict: ...

    def submit_rewrite(self, payload: Mapping[str, Any], idempotency_key: str) -> dict: ...

    def submit_partial_generate(self, payload: Mapping[str, Any], idempotency_key: str) -> dict: ...

    def get_job(self, job_id: str) -> dict: ...


class PoetryApiError(RuntimeError):
    pass
