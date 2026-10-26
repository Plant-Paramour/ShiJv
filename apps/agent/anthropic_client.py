from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from .openai_client import ChatModelError
from .transport import JsonTransportError, request_json, request_sse


class AnthropicMessagesChatModel:
    """Anthropic Messages adapter normalized to the Agent's ChatModel contract."""

    def __init__(self, *, base_url: str, api_key: str, model: str, timeout_seconds: float = 90.0):
        self._url = base_url.rstrip("/")
        if not self._url.endswith("/messages"):
            self._url += "/v1/messages" if not self._url.endswith("/v1") else "/messages"
        self._api_key, self._model, self._timeout = api_key, model, timeout_seconds

    @staticmethod
    def _request(messages: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]]):
        system, normalized = [], []
        for message in messages:
            role, content = message.get("role"), message.get("content")
            if role == "system":
                if content:
                    system.append(str(content))
                continue
            if role == "tool":
                block = {"type": "tool_result", "tool_use_id": message.get("tool_call_id", ""), "content": str(content or "")}
                role, content = "user", [block]
            elif role == "assistant" and message.get("tool_calls"):
                blocks = []
                if content:
                    blocks.append({"type": "text", "text": str(content)})
                for call in message["tool_calls"]:
                    function = call.get("function") or {}
                    try:
                        arguments = json.loads(function.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    blocks.append({"type": "tool_use", "id": call.get("id", ""), "name": function.get("name", ""), "input": arguments})
                content = blocks
            elif isinstance(content, list):
                blocks = []
                for part in content:
                    if part.get("type") == "text":
                        blocks.append({"type": "text", "text": part.get("text", "")})
                    elif part.get("type") == "image_url":
                        url = (part.get("image_url") or {}).get("url", "")
                        if "," in url and url.startswith("data:"):
                            media_type, encoded = url[5:].split(";base64,", 1)
                            blocks.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": encoded}})
                content = blocks
            if role not in {"user", "assistant"}:
                continue
            if normalized and normalized[-1]["role"] == role:
                previous = normalized[-1]["content"]
                previous = previous if isinstance(previous, list) else [{"type": "text", "text": str(previous or "")}]
                incoming = content if isinstance(content, list) else [{"type": "text", "text": str(content or "")}]
                previous.extend(incoming)
                normalized[-1]["content"] = previous
            else:
                normalized.append({"role": role, "content": content or ""})
        body: dict[str, Any] = {"model": "", "messages": normalized, "max_tokens": 4096}
        if system:
            body["system"] = "\n\n".join(system)
        if tools:
            body["tools"] = [{"name": (tool.get("function") or {}).get("name", ""),
                              "description": (tool.get("function") or {}).get("description", ""),
                              "input_schema": (tool.get("function") or {}).get("parameters", {"type": "object"})}
                             for tool in tools]
        return body

    def _call(self, body, *, stream=False):
        return request_json("POST", self._url, self._headers(), body, self._timeout)

    def _headers(self):
        return {"x-api-key": self._api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}

    def complete(self, messages: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]]):
        body = self._request(messages, tools)
        body["model"] = self._model
        try:
            response = self._call(body)
        except (JsonTransportError, OSError, ValueError) as exc:
            raise ChatModelError(f"Anthropic API 请求失败: {exc}", status_code=getattr(exc, "status_code", None), retry_after=getattr(exc, "retry_after", None)) from exc
        text, calls = [], []
        for block in response.get("content") or []:
            if block.get("type") == "text":
                text.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                calls.append({"id": block.get("id", ""), "type": "function", "function": {
                    "name": block.get("name", ""), "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False)}})
        return {"role": "assistant", "content": "".join(text) or None, "tool_calls": calls or None}

    def stream(self, messages, tools, cancellation_event=None, resume_token=None):
        body = self._request(messages, tools)
        body.update({"model": self._model, "stream": True})
        def events():
            chunks = request_sse("POST", self._url, self._headers(), body, self._timeout)
            names, ids, arguments = {}, {}, defaultdict(str)
            index = -1
            finished = False
            try:
                data_lines = []
                for raw in chunks:
                    if cancellation_event is not None and cancellation_event.is_set():
                        return
                    line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
                    line = line.rstrip("\r\n")
                    if not line:
                        if not data_lines:
                            continue
                        try:
                            event = json.loads("\n".join(data_lines))
                        except json.JSONDecodeError:
                            data_lines = []
                            continue
                        data_lines = []
                        kind = event.get("type")
                        if kind == "error":
                            detail = (event.get("error") or {}).get("message") or "上游返回流式错误"
                            raise ChatModelError(f"Anthropic API 流式请求失败: {detail}")
                        if kind == "content_block_start":
                            index = event.get("index", index + 1)
                            block = event.get("content_block") or {}
                            if block.get("type") == "tool_use":
                                names[index], ids[index] = block.get("name", ""), block.get("id", "")
                                yield {"type": "tool.name.delta", "index": index, "name": names[index]}
                        elif kind == "content_block_delta":
                            delta = event.get("delta") or {}
                            if delta.get("type") == "text_delta" and delta.get("text"):
                                yield {"type": "assistant.delta", "text": delta["text"]}
                            elif delta.get("type") == "input_json_delta" and delta.get("partial_json"):
                                fragment = delta["partial_json"]
                                arguments[index] += fragment
                                yield {"type": "tool.arguments.delta", "index": index, "arguments": fragment}
                        elif kind == "message_stop":
                            finished = True
                            yield {"type": "turn.finished", "finish_reason": "tool_use" if names else "stop", "tool_calls": [
                                {"id": ids[i], "type": "function", "function": {"name": names[i], "arguments": arguments[i] or "{}"}}
                                for i in sorted(names)]}
                            return
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
            finally:
                close = getattr(chunks, "close", None)
                if callable(close):
                    close()
            if not finished and not (cancellation_event is not None and cancellation_event.is_set()):
                raise ChatModelError("Anthropic API 流式响应未正常结束")
        return events()
