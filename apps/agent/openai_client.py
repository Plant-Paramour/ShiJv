from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Callable, Protocol

from .transport import JsonTransportError, request_json, request_sse
from .sse import iter_sse_lines, merge_openai_stream


class ChatModel(Protocol):
    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]: ...


class ChatModelError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def chat_completions_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    if normalized.endswith("/v1"):
        return normalized + "/chat/completions"
    return normalized + "/v1/chat/completions"


class OpenAICompatibleChatModel:
    """最小化 OpenAI Chat Completions 客户端。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 90.0,
        transport: Callable[..., dict[str, Any]] = request_json,
        stream_transport: Callable[..., Any] | None = None,
    ):
        self._url = chat_completions_url(base_url)
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._stream_transport = stream_transport

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": list(messages),
            "tools": list(tools),
            "tool_choice": "auto",
            "stream": False,
        }
        try:
            data = self._transport(
                "POST",
                self._url,
                {
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                payload,
                self._timeout_seconds,
            )
        except (JsonTransportError, OSError, ValueError) as exc:
            raise ChatModelError(f"大模型 API 请求失败: {exc}", status_code=getattr(exc, "status_code", None), retry_after=getattr(exc, "retry_after", None)) from exc

        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ChatModelError("大模型 API 响应缺少 choices[0].message") from exc
        if not isinstance(message, dict):
            raise ChatModelError("大模型 API 返回的 message 不是对象")
        return message

    def stream(self, messages: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]], cancellation_event=None, resume_token: str | None = None):
        def generate():
            payload: dict[str, Any] = {
                "model": self._model, "messages": list(messages), "tools": list(tools),
                "tool_choice": "auto", "stream": True,
            }
            emitted = False
            raw_chunks = None
            try:
                stream_transport = self._stream_transport or request_sse
                raw_chunks = stream_transport("POST", self._url, {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}, payload, self._timeout_seconds)
                def cancellable_lines():
                    for line in raw_chunks:
                        if cancellation_event is not None and cancellation_event.is_set():
                            return
                        yield line

                finished = False
                for event in merge_openai_stream(iter_sse_lines(cancellable_lines())):
                    if cancellation_event is not None and cancellation_event.is_set():
                        close = getattr(raw_chunks, "close", None)
                        if callable(close):
                            close()
                        return
                    if event.get("error"):
                        raise ChatModelError("上游流返回错误事件")
                    emitted = True
                    yield event
                    if event.get("type") == "turn.finished":
                        finished = True
                        break
                if not finished and not (cancellation_event is not None and cancellation_event.is_set()):
                    raise ChatModelError("上游流在完成之前关闭")
                return
            except (JsonTransportError, OSError, ValueError) as exc:
                if emitted:
                    raise ChatModelError(f"大模型 SSE 请求失败: {exc}", status_code=getattr(exc, "status_code", None), retry_after=getattr(exc, "retry_after", None)) from exc
            finally:
                close = getattr(raw_chunks, "close", None)
                if callable(close):
                    close()
            # Some compatible providers accept chat completions but reject stream=true.
            # Fall back to one normal response so the existing Agent loop remains usable.
            if cancellation_event is not None and cancellation_event.is_set():
                return
            message = self.complete(messages, tools)
            if message.get("content"):
                yield {"type": "assistant.delta", "text": str(message["content"])}
            if message.get("reasoning_content"):
                yield {"type": "assistant.reasoning.delta", "text": str(message["reasoning_content"])}
            calls = list(message.get("tool_calls") or [])
            yield {"type": "turn.finished", "finish_reason": "tool_calls" if calls else "stop", "tool_calls": calls}
        return generate()

    @property
    def supports_native_resume(self) -> bool:
        return False
