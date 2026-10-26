from __future__ import annotations

from dataclasses import dataclass

from ..domain import REWRITE_MARKER


class RewriteProtocolError(ValueError):
    code = "MODEL_PROTOCOL_ERROR"


@dataclass(frozen=True)
class RewriteProtocolOutput:
    revision_note: str
    rewrite_text: str

    @property
    def display_prefix(self) -> str:
        return f"修改思路：{self.revision_note}" if self.revision_note else ""


def parse_rewrite_protocol(text: str) -> RewriteProtocolOutput:
    text = text.strip()
    marker_index = text.find(REWRITE_MARKER)
    if marker_index < 0:
        raise RewriteProtocolError("重写响应缺少 [rewrite] 标记")
    rewrite_text = text[marker_index + len(REWRITE_MARKER):].strip()
    return RewriteProtocolOutput(revision_note="", rewrite_text=rewrite_text)


def build_display_text(revision_note: str, full_text: str) -> str:
    return f"修改思路：{revision_note}\n\n{full_text}" if revision_note else full_text
