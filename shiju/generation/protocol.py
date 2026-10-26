from __future__ import annotations

import re
from dataclasses import dataclass


class GenerationProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedGeneration:
    title: str
    content: str


def parse_generation_protocol(text: str) -> ParsedGeneration:
    value = (text or "").strip()
    title_markers = list(re.finditer(r"\[title\]", value, re.I))
    content_markers = list(re.finditer(r"\[content\]", value, re.I))
    if len(title_markers) != 1 or len(content_markers) != 1:
        raise GenerationProtocolError("生成结果必须包含 [title] 和 [content] 标记")
    title_marker = title_markers[0]
    content_marker = content_markers[0]
    if title_marker.end() > content_marker.start():
        raise GenerationProtocolError("[title] 必须出现在 [content] 之前")
    title = value[title_marker.end():content_marker.start()]
    content = value[content_marker.end():]
    if not title.strip(): raise GenerationProtocolError("诗词标题不能为空")
    if not content.strip(): raise GenerationProtocolError("诗词正文不能为空")
    return ParsedGeneration(title=title.strip(), content=content.strip())
