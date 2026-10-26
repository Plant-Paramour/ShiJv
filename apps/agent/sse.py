from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Iterable, Iterator


def iter_sse_lines(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    """解析 OpenAI SSE，忽略注释和空事件。"""
    data: list[str] = []
    for raw in lines:
        line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        line = line.rstrip("\r\n")
        if not line:
            if data:
                joined = "\n".join(data); data = []
                if joined == "[DONE]":
                    yield {"done": True}; continue
                try: yield json.loads(joined)
                except json.JSONDecodeError: yield {"error": "invalid SSE JSON", "data": joined}
            continue
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        joined = "\n".join(data)
        if joined == "[DONE]": yield {"done": True}
        else:
            try: yield json.loads(joined)
            except json.JSONDecodeError: yield {"error": "invalid SSE JSON", "data": joined}


def merge_openai_stream(chunks: Iterable[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """把增量 chunk 归并成可持久化的文本/工具调用事件。"""
    tool_args: dict[int, str] = defaultdict(str)
    tool_names: dict[int, str] = {}
    tool_ids: dict[int, str] = {}
    for chunk in chunks:
        if chunk.get("done"): yield chunk; continue
        if chunk.get("error"): yield chunk; continue
        choice = (chunk.get("choices") or [{}])[0]
        delta = choice.get("delta") or {}
        if delta.get("reasoning_content"):
            yield {"type": "assistant.reasoning.delta", "text": delta["reasoning_content"]}
        if delta.get("content"):
            yield {"type": "assistant.delta", "text": delta["content"]}
        for call in delta.get("tool_calls") or []:
            index = int(call.get("index", 0)); function = call.get("function") or {}
            if call.get("id"):
                tool_ids[index] = tool_ids.get(index, "") + str(call["id"])
            if function.get("name"):
                tool_names[index] = tool_names.get(index, "") + str(function["name"])
                yield {"type": "tool.name.delta", "index": index, "name": function["name"]}
            if function.get("arguments"):
                tool_args[index] += str(function["arguments"])
                yield {"type": "tool.arguments.delta", "index": index, "arguments": function["arguments"]}
        if choice.get("finish_reason") is not None:
            indexes = sorted(set(tool_names) | set(tool_args) | set(tool_ids))
            yield {"type": "turn.finished", "finish_reason": choice["finish_reason"], "tool_calls": [
                {"id": tool_ids.get(i) or f"call-{i}", "type": "function", "function": {"name": tool_names.get(i, ""), "arguments": tool_args.get(i, "")}}
                for i in indexes
            ]}
