from __future__ import annotations

from pathlib import Path

from ..constraints import HanpaiConstraintProfile, RelationalConstraintProfile, TemplateConstraintProfile
from ..contracts import GeneratePoemRequest
from ..data import MeterTemplateRepository, RhymeLexicon
from ..tasks import parse_hanpai_format, parse_pailv_format, parse_tang_format


def validate_partial_generation_request(
    request: GeneratePoemRequest,
    project_root: str | Path,
) -> None:
    """Ensure partial generation contains fixed user lines and real blanks to fill."""
    if request.meter_type == "唐诗":
        _, line_count, _ = parse_tang_format(request.form_name)
    elif request.meter_type == "排律":
        _, line_count = parse_pailv_format(request.form_name, request.num_lines)
    elif request.meter_type == "汉俳":
        line_count = len(parse_hanpai_format(str(request.task_options.get("line_pattern", "5-7-5"))))
    elif request.meter_type == "宋词":
        template = MeterTemplateRepository(Path(project_root) / "Songci_Meter").get(
            request.form_name, request.variant_name
        )
        line_count = len(template.lines)
    else:
        raise ValueError(f"不支持的生成类型: {request.meter_type}")

    fixed_numbers = {int(number) for number in request.fixed_lines}
    if not fixed_numbers:
        raise ValueError("部分生成必须至少指定一句需要原样保留的句子")
    if request.sentence_inputs is not None and any(
        str(text).strip()
        for number, text in request.sentence_inputs.items()
        if int(number) not in fixed_numbers
    ):
        raise ValueError("部分生成只补完空缺句；已有正文必须作为 fixed_lines 固定保留，不能在此重写")
    if any(number > line_count for number in fixed_numbers):
        raise ValueError(f"固定句号超出篇式范围；该篇式共 {line_count} 句")
    if len(fixed_numbers) >= line_count:
        raise ValueError("诗词补完至少需要留出一句空缺供生成")


def validate_fixed_line_constraints(
    request: GeneratePoemRequest,
    project_root: str | Path,
) -> dict:
    if not request.fixed_lines:
        return {}

    lexicon = RhymeLexicon(Path(project_root) / "Rhyme" / f"{request.rhyme_dict_name}.json")
    if request.meter_type == "宋词":
        template = MeterTemplateRepository(Path(project_root) / "Songci_Meter").get(
            request.form_name, request.variant_name
        )
        profile = TemplateConstraintProfile(template, lexicon)
        session = profile.create_session(rhyme_mode=request.rhyme_mode, rhyme_parts=request.rhyme_parts)
        session.prime_fixed_lines(
            {int(number) - 1: text for number, text in request.fixed_lines.items()},
            strict_polyphonic=request.strict_polyphonic,
        )
        return {"checked": "template"}

    if request.meter_type == "汉俳":
        options = request.task_options
        profile = HanpaiConstraintProfile(
            line_lengths=parse_hanpai_format(str(options.get("line_pattern", "5-7-5"))),
            lexicon=lexicon,
            rhyme_scheme=options.get("rhyme_scheme"),
            forbid_isolated_level=bool(options.get("forbid_isolated_level", True)),
            allow_aojiu=bool(options.get("allow_aojiu", True)),
            forbid_three_same_ending=bool(options.get("forbid_three_same_ending", True)),
        )
        session = profile.create_session(rhyme_mode=request.rhyme_mode, rhyme_parts=request.rhyme_parts)
        session.prime_fixed_lines(
            {int(number) - 1: text for number, text in request.fixed_lines.items()},
            strict_polyphonic=request.strict_polyphonic,
        )
        return {"checked": "hanpai"}

    if request.meter_type not in {"唐诗", "排律"}:
        return {}

    if request.meter_type == "唐诗":
        line_length, num_lines, rhyme_type = parse_tang_format(request.form_name)
    else:
        line_length, num_lines = parse_pailv_format(request.form_name, request.num_lines)
        rhyme_type = "平韵"

    allow_aojiu = bool(request.task_options.get("allow_aojiu", True))
    profile = RelationalConstraintProfile(
        line_length=line_length,
        num_lines=num_lines,
        rhyme_type=rhyme_type,
        lexicon=lexicon,
        allow_aojiu=allow_aojiu,
        strict_polyphonic=request.strict_polyphonic,
    )
    session = profile.create_session(
        rhyme_mode=request.rhyme_mode,
        rhyme_parts=request.rhyme_parts,
    )
    session.prime_fixed_lines(
        {int(number) - 1: text for number, text in request.fixed_lines.items()},
        strict_polyphonic=request.strict_polyphonic,
    )
    return {
        "base_tone": session._global_base_tone,
        "first_line_rhymes": session._line0_rhymes,
        "rhyme_parts": sorted(session._locked_rhyme_parts or []),
    }
