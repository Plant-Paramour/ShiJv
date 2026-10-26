from __future__ import annotations

from pathlib import Path

from ..contracts import RewritePoemRequest
from .parser import parse_poem


def validate_rewrite_context(request: RewritePoemRequest, project_root: str | Path) -> None:
    poem = parse_poem(request.original_text)
    expected: int | None = None
    if request.meter_type == "唐诗":
        if "绝句" in request.form_name:
            expected = 4
        elif "律诗" in request.form_name:
            expected = 8
    elif request.meter_type == "汉俳":
        expected = 3
    elif request.meter_type == "排律":
        expected = request.num_lines
    elif request.meter_type == "宋词":
        from ..data import MeterTemplateRepository
        meter = MeterTemplateRepository(Path(project_root) / "Songci_Meter").get(request.form_name, request.variant_name)
        expected = len(meter.lines)
    if expected is not None and len(poem.lines) != expected:
        raise ValueError(f"原诗解析为 {len(poem.lines)} 句，但 {request.form_name} 要求 {expected} 句")
    if request.target_line_numbers and max(request.target_line_numbers) > len(poem.lines):
        raise ValueError(f"目标句 {max(request.target_line_numbers)} 超出原诗的 {len(poem.lines)} 句")
