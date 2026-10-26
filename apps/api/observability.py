from __future__ import annotations

import contextvars
import re
import time
from dataclasses import dataclass, field


@dataclass
class RequestDbStats:
    db_duration_ms: float = 0.0
    db_query_count: int = 0
    pool_wait_ms: float = 0.0
    slow_queries: list[dict] = field(default_factory=list)


_current: contextvars.ContextVar[RequestDbStats | None] = contextvars.ContextVar("shiju_db_stats", default=None)


def begin() -> contextvars.Token:
    return _current.set(RequestDbStats())


def current() -> RequestDbStats | None:
    return _current.get()


def end(token: contextvars.Token) -> RequestDbStats | None:
    stats = _current.get()
    _current.reset(token)
    return stats


def record_query(sql: str, duration_ms: float, *, slow_ms: float = 100.0) -> None:
    stats = _current.get()
    if stats is None:
        return
    stats.db_duration_ms += duration_ms
    stats.db_query_count += 1
    if duration_ms >= slow_ms:
        normalized = re.sub(r"\s+", " ", sql).strip()[:240]
        stats.slow_queries.append({"sql": normalized, "duration_ms": round(duration_ms, 2)})


class TimedCursor:
    def __init__(self, cursor, *, slow_ms: float = 100.0):
        self._cursor = cursor
        self._slow_ms = slow_ms

    def execute(self, sql, params=()):
        started = time.perf_counter()
        try:
            return self._cursor.execute(sql, params)
        finally:
            record_query(sql, (time.perf_counter() - started) * 1000, slow_ms=self._slow_ms)

    def __getattr__(self, name):
        return getattr(self._cursor, name)
