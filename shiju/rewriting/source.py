from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .parser import parse_poem


def poem_body(content: str) -> str:
    return content.split("[content]", 1)[-1].strip()


def numbered_source(content: str, context: Mapping[str, Any], project_root: str | Path,
                    *, poem_id: str | None = None, title: str = "") -> dict:
    content = poem_body(content)
    poem = parse_poem(content)
    metadata = {key: context.get(key) for key in (
        "meter_type", "form_name", "variant_name", "rhyme_dict_name", "num_lines",
    )}
    positions = []
    if metadata["meter_type"] == "宋词":
        from shiju.data import MeterTemplateRepository
        meter = MeterTemplateRepository(Path(project_root) / "Songci_Meter").get(
            metadata["form_name"], metadata["variant_name"],
        )
        metadata["variant_name"] = meter.variant_name
        positions = [(line.layout.stanza_index + 1, line.layout.line_in_stanza + 1)
                     for line in meter.lines]
        if len(positions) != len(poem.lines):
            raise ValueError(f"原文为 {len(poem.lines)} 句，词谱要求 {len(positions)} 句，不能安全定位重写句")
    version = hashlib.sha256(json.dumps(
        {"content": content, "metadata": metadata}, ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")).hexdigest()
    lines = []
    for line in poem.lines:
        stanza, local = positions[line.number - 1] if positions else (1, line.number)
        lines.append({
            "line_id": f"s{stanza}-l{local}", "number": line.number,
            "stanza_number": stanza, "sentence_number": local,
            "text": line.text, "source_text": line.source_text,
        })
    return {"source_poem_id": poem_id, "source_version": version, "title": title,
            "original_text": content, "lines": lines, **metadata}


def select_rewrite_lines(source: Mapping[str, Any], version: str, line_ids: list[str],
                         expected_texts: Mapping[str, str]) -> dict:
    if version != source["source_version"]:
        raise ValueError("来源版本不一致，请重新读取带编号原作")
    by_id = {line["line_id"]: line for line in source["lines"]}
    if not line_ids or len(line_ids) != len(set(line_ids)):
        raise ValueError("必须选择不重复的重写句")
    if set(line_ids) != set(expected_texts):
        raise ValueError("每个目标句必须提供对应原句用于核对")
    for line_id in line_ids:
        if line_id not in by_id:
            raise ValueError(f"目标句不存在：{line_id}")
        if expected_texts[line_id] not in {by_id[line_id]["text"], by_id[line_id]["source_text"]}:
            raise ValueError(f"{line_id} 的编号与原句不一致，请重新核对")
    targets = sorted(by_id[line_id]["number"] for line_id in line_ids)
    if len(targets) == len(by_id):
        raise ValueError("部分重写必须至少保留一句原文")
    return {"target_line_numbers": targets,
            "fixed_lines": {str(line["number"]): line["text"] for line in source["lines"]
                            if line["number"] not in targets}}


def validate_rewrite_result(request: Mapping[str, Any], content: str) -> str:
    """Keep the exact original punctuation and spacing outside replaced sentences."""
    original = parse_poem(request["original_text"])
    generated = parse_poem(poem_body(content))
    if len(original.lines) != len(generated.lines):
        raise ValueError("改写成稿句数与原作不一致")
    targets = set(request["target_line_numbers"])
    if request.get("sentence_inputs") is not None:
        fixed = request.get("fixed_lines") or {}
        for line in generated.lines:
            if line.number not in targets and line.text != fixed.get(str(line.number)):
                raise ValueError(f"生成结果修改了保留的第 {line.number} 句")
        result = original.replace_lines({line.number: line.source_text for line in generated.lines})
    else:
        for old, new in zip(original.lines, generated.lines):
            if old.number not in targets and old.text != new.text:
                raise ValueError(f"生成结果修改了保留的第 {old.number} 句")
        result = original.replace_lines({line.number: line.source_text for line in generated.lines
                                         if line.number in targets})

    if request.get("meter_type") == "宋词" and request.get("form_name"):
        from pathlib import Path
        from shiju.data import MeterTemplateRepository

        project_root = Path(__file__).resolve().parents[2]
        meter = MeterTemplateRepository(project_root / "Songci_Meter").get(
            request["form_name"], request.get("variant_name"),
        )
        result = ensure_stanza_line_breaks(result, meter.lines)
    return result


def ensure_stanza_line_breaks(content: str, layout_lines) -> str:
    """Ensure a newline after each Song Ci stanza while preserving its text."""
    poem = parse_poem(content)
    boundaries = {
        index + 1
        for index, line in enumerate(layout_lines[:-1])
        if line.layout.stanza_index != layout_lines[index + 1].layout.stanza_index
    }
    result = content
    for line_number in sorted(boundaries, reverse=True):
        if line_number >= len(poem.lines):
            continue
        line = poem.lines[line_number - 1]
        next_line = poem.lines[line_number]
        between = result[line.end:next_line.start]
        if "\n" in between or "\r" in between:
            continue
        separator_end = line.end
        while separator_end < len(result) and result[separator_end] in "，,。.？！；;!? \t":
            separator_end += 1
        result = result[:separator_end] + "\n" + result[separator_end:]
    return result
