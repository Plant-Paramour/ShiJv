from __future__ import annotations

import random
import threading
import time
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 on Ubuntu 22.04.
    import tomli as tomllib
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .openai_client import ChatModel, ChatModelError, OpenAICompatibleChatModel
from .anthropic_client import AnthropicMessagesChatModel
from .transport import JsonTransportError


@dataclass(frozen=True)
class EndpointConfig:
    provider_id: str
    base_url: str
    model: str
    tier: str = "primary"
    priority: int = 0
    weight: int = 1
    max_concurrency: int = 8
    timeout_seconds: float = 90.0
    supports_tools: bool = True
    supports_stream: bool = True
    cost_level: str = "standard"
    provider: str = "openai-compatible"
    api_key: str = ""


@dataclass
class _EndpointState:
    config: EndpointConfig
    active: int = 0
    failures: int = 0
    successes: int = 0
    latency_ms: deque[float] = field(default_factory=lambda: deque(maxlen=100))
    last_status_code: int | None = None
    cooldown_until: float = 0.0
    semaphore: threading.BoundedSemaphore | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    recovery_probe_in_flight: bool = False

    def available(self, now: float, needs_stream: bool) -> bool:
        return (self.config.supports_stream if needs_stream else self.config.supports_tools) and self.active < self.config.max_concurrency


class _RoutedModel(ChatModel):
    def __init__(self, gateway: "ModelGateway", model: str):
        self._gateway = gateway
        self._model = model

    def complete(self, messages, tools):
        return self._gateway._call("complete", messages, tools, preferred_model=self._model)

    def stream(self, messages, tools, cancellation_event=None, resume_token=None):
        def generate():
            attempted: set[int] = set()
            last_error: Exception | None = None
            while len(attempted) < self._gateway._max_attempts:
                state = self._gateway._select(attempted, needs_stream=True, preferred_model=self._model)
                if state is None:
                    break
                attempted.add(id(state))
                try:
                    yield from self._gateway._invoke(state, "stream", messages, tools, cancellation_event=cancellation_event)
                    return
                except Exception as exc:
                    last_error = exc
                    if "已开始后中断" in str(exc) or not self._gateway._retryable(exc):
                        raise ChatModelError(f"模型流式响应中断: {exc}") from exc
            raise ChatModelError(f"模型网关流式请求无可用端点: {last_error}") from last_error
        return generate()


class ModelGateway(ChatModel):
    """兼容 ChatModel 的端点池，默认保守地只重试可切换的上游故障。"""

    def __init__(self, endpoints: Sequence[EndpointConfig], *, max_attempts: int = 3, clock=time.monotonic):
        if not endpoints:
            raise ValueError("Model Gateway 至少需要一个端点")
        if max_attempts < 1:
            raise ValueError("Model Gateway 的最大尝试次数必须为正数")
        for endpoint in endpoints:
            if not all((endpoint.provider_id.strip(), endpoint.base_url.strip(), endpoint.model.strip())):
                raise ValueError("端点必须配置 provider_id、base_url 和 model")
            if endpoint.provider not in {"openai-compatible", "anthropic"}:
                raise ValueError(f"不支持的模型协议: {endpoint.provider}")
            if endpoint.tier not in {"primary", "secondary", "emergency"}:
                raise ValueError(f"未知端点层级: {endpoint.tier}")
            if endpoint.max_concurrency < 1 or endpoint.weight < 1 or endpoint.timeout_seconds <= 0:
                raise ValueError(f"端点 {endpoint.provider_id} 的并发、权重和超时必须为正数")
        self._states = [_EndpointState(e, semaphore=threading.BoundedSemaphore(e.max_concurrency)) for e in endpoints]
        self._max_attempts = max(1, max_attempts)
        self._clock = clock

    @classmethod
    def from_toml(cls, path: str | Path, *, max_attempts: int = 3) -> "ModelGateway":
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        configs = []
        for item in data.get("endpoints", []):
            config = dict(item)
            config.setdefault("api_key", "")
            models = config.pop("models", None)
            if models is not None:
                if not isinstance(models, list) or not models or not all(isinstance(model, str) and model.strip() for model in models):
                    raise ValueError(f"端点 {config.get('provider_id', '')} 的 models 必须是非空字符串数组")
                for model in models:
                    configs.append(EndpointConfig(**config, model=model.strip()))
            else:
                configs.append(EndpointConfig(**config))
        return cls(configs, max_attempts=max_attempts)

    @classmethod
    def single(cls, *, base_url: str, api_key: str, model: str, timeout_seconds: float = 90.0) -> "ModelGateway":
        return cls([EndpointConfig("local", base_url, model, api_key=api_key, timeout_seconds=timeout_seconds)])

    def complete(self, messages: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return self._call("complete", messages, tools)

    def for_model(self, model: str | None):
        return _RoutedModel(self, model) if model else self

    def _call(self, method: str, messages, tools, preferred_model: str | None = None):
        attempted: set[int] = set()
        last_error: Exception | None = None
        for _ in range(self._max_attempts):
            state = self._select(attempted, needs_stream=method == "stream", preferred_model=preferred_model)
            if state is None:
                break
            attempted.add(id(state))
            try:
                return self._invoke(state, method, messages, tools)
            except Exception as exc:
                last_error = exc
                if not self._retryable(exc):
                    break
        raise ChatModelError(f"模型网关无可用端点: {last_error}") from last_error

    def stream(self, messages: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]], cancellation_event=None, resume_token=None):
        # Generator creation is lazy so errors are delivered to the existing SSE path.
        def generate():
            attempted: set[int] = set()
            last_error: Exception | None = None
            while len(attempted) < self._max_attempts:
                state = self._select(attempted, needs_stream=True)
                if state is None:
                    break
                attempted.add(id(state))
                try:
                    yield from self._invoke(state, "stream", messages, tools, cancellation_event=cancellation_event)
                    return
                except Exception as exc:
                    last_error = exc
                    if "已开始后中断" in str(exc) or not self._retryable(exc):
                        raise ChatModelError(f"模型流式响应中断: {exc}") from exc
            raise ChatModelError(f"模型网关流式请求无可用端点: {last_error}") from last_error
        return generate()

    def _select(self, attempted: set[int], *, needs_stream: bool, preferred_model: str | None = None) -> _EndpointState | None:
        now = self._clock()
        tiers = {"primary": 0, "secondary": 1, "emergency": 2}
        candidates = []
        reserved: list[_EndpointState] = []
        for state in self._states:
            if id(state) in attempted or (preferred_model is not None and state.config.model != preferred_model):
                continue
            with state.lock:
                if not state.available(now, needs_stream) or now < state.cooldown_until:
                    continue
                if state.cooldown_until > 0:
                    if state.recovery_probe_in_flight:
                        continue
                    state.recovery_probe_in_flight = True
                    reserved.append(state)
                candidates.append(state)
        if not candidates:
            return None
        candidates.sort(key=lambda s: (tiers.get(s.config.tier, 9), s.config.priority, -s.config.weight))
        top = [s for s in candidates if (s.config.tier, s.config.priority) == (candidates[0].config.tier, candidates[0].config.priority)]
        weights = [max(1, item.config.weight) for item in top]
        selected = random.choices(top, weights=weights, k=1)[0]
        for state in reserved:
            if state is not selected:
                with state.lock:
                    state.recovery_probe_in_flight = False
        return selected

    def _invoke(self, state: _EndpointState, method: str, messages, tools, cancellation_event=None):
        key = state.config.api_key
        if not key:
            with state.lock:
                state.recovery_probe_in_flight = False
            raise ChatModelError(f"端点 {state.config.provider_id} 的密钥未配置")
        model_type = AnthropicMessagesChatModel if state.config.provider == "anthropic" else OpenAICompatibleChatModel
        model = model_type(base_url=state.config.base_url, api_key=key, model=state.config.model, timeout_seconds=state.config.timeout_seconds)
        started = self._clock()
        assert state.semaphore is not None
        if not state.semaphore.acquire(timeout=state.config.timeout_seconds):
            raise ChatModelError(f"端点 {state.config.provider_id} 并发已满")
        with state.lock:
            state.active += 1
        transferred = False
        try:
            if method == "stream":
                iterator = (model.stream(messages, tools, cancellation_event=cancellation_event)
                            if cancellation_event is not None else model.stream(messages, tools))
                def guarded():
                    emitted = False
                    try:
                        for item in iterator:
                            emitted = True
                            yield item
                        with state.lock:
                            state.successes += 1
                            state.failures = 0
                            state.cooldown_until = 0.0
                            state.recovery_probe_in_flight = False
                            state.latency_ms.append((self._clock() - started) * 1000)
                    except Exception as exc:
                        with state.lock:
                            state.failures += 1
                            self._apply_cooldown(state, exc)
                            state.recovery_probe_in_flight = False
                        if emitted:
                            raise ChatModelError("模型流式响应已开始后中断") from exc
                        raise
                    finally:
                        try:
                            close = getattr(iterator, "close", None)
                            if callable(close):
                                close()
                        finally:
                            with state.lock:
                                state.active -= 1
                            state.semaphore.release()
                # Ownership transfers to the iterator until it is consumed or closed.
                transferred = True
                return guarded()
            result = getattr(model, method)(messages, tools)
            with state.lock:
                state.successes += 1
                state.failures = 0
                state.cooldown_until = 0.0
                state.recovery_probe_in_flight = False
                state.latency_ms.append((self._clock() - started) * 1000)
            return result
        except Exception as exc:
            with state.lock:
                state.failures += 1
                if getattr(exc, "status_code", None) in {401, 403}:
                    state.cooldown_until = self._clock() + 3600
                self._apply_cooldown(state, exc)
                state.recovery_probe_in_flight = False
            raise
        finally:
            if not transferred:
                with state.lock:
                    state.active -= 1
                state.semaphore.release()

    def _apply_cooldown(self, state: _EndpointState, exc: Exception) -> None:
        status_code = getattr(exc, "status_code", None)
        state.last_status_code = status_code
        if status_code in {401, 403}:
            state.cooldown_until = self._clock() + 3600
        elif status_code == 429:
            state.cooldown_until = self._clock() + (getattr(exc, "retry_after", None) or 30)
        elif state.failures >= 3:
            state.cooldown_until = self._clock() + 30

    @staticmethod
    def _retryable(exc: Exception) -> bool:
        if isinstance(exc, (JsonTransportError, ChatModelError)):
            if "密钥未配置" in str(exc):
                return False
            # 4xx compatibility/auth errors should move to another complete
            # URL+key+model combination; never retry the same endpoint blindly.
            return getattr(exc, "status_code", None) is None or getattr(exc, "status_code", None) in {400, 401, 403, 408, 425, 429, 500, 502, 503, 504}
        return isinstance(exc, (OSError, TimeoutError))

    def status(self) -> list[dict[str, Any]]:
        snapshots = []
        for state in self._states:
            with state.lock:
                snapshots.append({
                    "provider_id": state.config.provider_id,
                    "model": state.config.model,
                    "tier": state.config.tier,
                    "active": state.active,
                    "failures": state.failures,
                    "successes": state.successes,
                    "latency_ms": list(state.latency_ms)[-20:],
                    "last_status_code": state.last_status_code,
                    "cooldown_until": state.cooldown_until,
                    "recovery_probe_in_flight": state.recovery_probe_in_flight,
                })
        return snapshots
