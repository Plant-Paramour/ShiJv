from __future__ import annotations

from dataclasses import dataclass
from itertools import product
import random
from typing import Mapping, Protocol, Sequence

from .data import MeterTemplate, RhymeLookup
from .candidates import has_viable_tang_completion, tang_line_requires_cross_rescue, first_line_reading_is_viable
from .domain import (
    AllowedPattern,
    GenerationLayout,
    GenerationState,
    LineLayout,
    RhymeConstraint,
)


class ConstraintProfile(Protocol):
    @property
    def layout(self) -> GenerationLayout: ...

    def create_session(self, *, rhyme_mode: str = "auto", rhyme_parts: Mapping[str, str] | None = None) -> "BaseConstraintSession": ...


class BaseConstraintSession:
    """将位置级平仄规则统一展开为 token 级候选模式。"""

    def allowed_patterns(
        self,
        state: GenerationState,
        max_length: int,
    ) -> Sequence[AllowedPattern]:
        if state.line is None or state.is_finished:
            return ()
        remains = state.line.length - state.char_index
        allowed: list[AllowedPattern] = []
        for length in range(1, min(max_length, remains) + 1):
            tone_options = [
                self.allowed_tones_at(state, state.char_index + offset)
                for offset in range(length)
            ]
            for tones in product(*tone_options):
                pattern = "".join(tones)
                rhyme = (
                    self.rhyme_constraint(state, pattern)
                    if length == remains
                    else RhymeConstraint.none()
                )
                if rhyme is not None:
                    required = self.required_text_at(state, length)
                    if required is not False:
                        allowed.append(AllowedPattern(length=length, tones=pattern, rhyme=rhyme, required_text=required))
        return allowed

    def allowed_tones_at(self, state: GenerationState, position: int) -> tuple[str, ...]:
        raise NotImplementedError

    def rhyme_constraint(
        self,
        state: GenerationState,
        pattern: str,
    ) -> RhymeConstraint | None:
        return RhymeConstraint.none()

    def observe_text(self, state: GenerationState, text: str) -> None:
        return None

    def required_text_at(self, state: GenerationState, length: int) -> str | None | bool:
        return None

    def candidate_context(self, state: GenerationState):
        return None

    def prime_fixed_lines(
        self,
        fixed_lines: Mapping[int, str],
        *,
        strict_polyphonic: bool = True,
    ) -> None:
        """Preload constraints derived from immutable rewrite context."""
        return None


class ContextConstraintError(ValueError):
    code = "CONTEXT_CONSTRAINT_CONFLICT"


def _tone_matches(
    tones: Sequence[str],
    allowed: set[str] | frozenset[str],
    strict: bool,
) -> bool:
    choices = set(tones)
    if not choices:
        return False
    return choices.issubset(allowed) if strict else bool(choices.intersection(allowed))


class TemplateConstraintProfile:
    def __init__(self, template: MeterTemplate, lexicon: RhymeLookup):
        self.template = template
        self.lexicon = lexicon

    @property
    def layout(self) -> GenerationLayout:
        return self.template.layout

    def create_session(self, *, rhyme_mode: str = "auto", rhyme_parts: Mapping[str, str] | None = None) -> "TemplateConstraintSession":
        return TemplateConstraintSession(self.template, self.lexicon, rhyme_mode=rhyme_mode, rhyme_parts=rhyme_parts)


class TemplateConstraintSession(BaseConstraintSession):
    def __init__(self, template: MeterTemplate, lexicon: RhymeLookup, *, rhyme_mode: str = "auto", rhyme_parts: Mapping[str, str] | None = None):
        self._template = template
        self._lexicon = lexicon
        self._lines = template.lines
        self._repeat_source: dict[tuple[int, int], tuple[int, int]] = {}
        self._repeat_chars: dict[tuple[int, int], str] = {}
        self._build_repetition_map(template.repetition_groups)
        self._locked_rhyme_parts: dict[int, str] = {}
        requested = {str(key): str(value) for key, value in (rhyme_parts or {}).items()}
        groups = sorted({line.rhyme_group for line in self._lines if line.rhyme_group is not None})
        for group in groups:
            value = requested.get(str(group))
            if value and value.lower() != "random":
                tone = "平" if "平" in self._template.rhyme_type else "仄"
                if not any(part == value and item_tone == tone for _, part, item_tone in self._lexicon.iter_rhyme_entries()):
                    raise ValueError(f"韵部 {value} 没有可用于{tone}韵的字")
                self._locked_rhyme_parts[group] = value
            elif rhyme_mode == "random" or value:
                tone = "平" if "平" in self._template.rhyme_type else "仄"
                parts = sorted({part for _, part, item_tone in self._lexicon.iter_rhyme_entries() if item_tone == tone})
                if parts:
                    self._locked_rhyme_parts[group] = random.choice(parts)

    def _build_repetition_map(self, groups: Sequence[Mapping[str, object]]) -> None:
        for group in groups:
            ranges = []
            for item in group.get("positions", ()) or ():
                if not isinstance(item, Mapping):
                    continue
                stanza = int(item.get("stanza", 0))
                start, end = item.get("start") or (), item.get("end") or ()
                if len(start) < 2 or len(end) < 2:
                    continue
                coords = []
                for line_no in range(int(start[0]), int(end[0]) + 1):
                    first = int(start[1]) if line_no == int(start[0]) else 1
                    last = int(end[1]) if line_no == int(end[0]) else self._line_length(stanza, line_no)
                    coords.extend((self._global_line_index(stanza, line_no), char_no) for char_no in range(first, last + 1))
                if coords:
                    ranges.append(coords)
            if not ranges:
                continue
            # 以最早出现的范围作为来源，避免词谱位置顺序变化时目标先于来源。
            source = min(ranges, key=lambda item: item[0])
            for target in ranges:
                if target is source:
                    continue
                if len(target) == len(source):
                    self._repeat_source.update(zip(target, source))

    def _line_length(self, stanza: int, line: int) -> int:
        return self._lines[self._global_line_index(stanza, line)].layout.length

    def _global_line_index(self, stanza: int, line: int) -> int:
        return sum(len(item.lines) for item in self._template.stanzas[: stanza - 1]) + line - 1

    def required_text_at(self, state: GenerationState, length: int) -> str | None | bool:
        required = []
        for offset in range(length):
            source = self._repeat_source.get((state.line_index, state.char_index + offset + 1))
            if source is None:
                required.append(None)
            elif source not in self._repeat_chars:
                return False
            else:
                required.append(self._repeat_chars[source])
        if any(value is not None for value in required):
            return "".join(required) if all(value is not None for value in required) else False
        return None

    @property
    def locked_rhyme_parts(self) -> dict[int, str]:
        return dict(self._locked_rhyme_parts)

    def allowed_tones_at(self, state: GenerationState, position: int) -> tuple[str, ...]:
        return tuple(sorted(self._lines[state.line_index].tone_options[position]))

    def rhyme_constraint(
        self,
        state: GenerationState,
        pattern: str,
    ) -> RhymeConstraint | None:
        group = self._lines[state.line_index].rhyme_group
        if group is None:
            return RhymeConstraint.none()
        tone = pattern[-1]
        if group == 1:
            if self._template.rhyme_type == "平韵" and tone != "平":
                return None
            if self._template.rhyme_type == "仄韵" and tone != "仄":
                return None
        locked = self._locked_rhyme_parts.get(group)
        if locked is None:
            return RhymeConstraint.any(tone)
        return RhymeConstraint.specific(tone, (locked,))

    def observe_text(self, state: GenerationState, text: str) -> None:
        if state.line is not None:
            for offset, char in enumerate(text):
                key = (state.line_index, state.char_index + offset + 1)
                source = self._repeat_source.get(key)
                if source is not None and source in self._repeat_chars and self._repeat_chars[source] != char:
                    raise ContextConstraintError("重复位置生成内容不一致")
                self._repeat_chars.setdefault(key, char)
                if source is not None:
                    self._repeat_chars.setdefault(source, char)
        line = self._lines[state.line_index]
        if line.rhyme_group is None or state.char_index + len(text) != line.layout.length:
            return
        group = line.rhyme_group
        if group in self._locked_rhyme_parts:
            return
        if group == 1 and self._template.rhyme_type in {"平韵", "仄韵"}:
            expected_tone = "平" if "平" in self._template.rhyme_type else "仄"
        elif group == 1:
            # 通押按当前韵脚实际可用的声调取韵部。
            tones = set(line.tone_options[-1]).intersection(self._lexicon.get_pingze(text[-1]))
            parts = sorted({part for tone in tones
                            for part in self._lexicon.get_rhyme_part_by_tone(text[-1], tone)})
            if parts:
                self._locked_rhyme_parts[group] = parts[0]
            return
        else:
            last_options = line.tone_options[-1]
            if len(last_options) == 1:
                expected_tone = next(iter(last_options))
            else:
                expected_tone = "仄" if "平" in self._template.rhyme_type else "平"
        parts = self._lexicon.get_rhyme_part_by_tone(text[-1], expected_tone)
        if parts:
            self._locked_rhyme_parts[group] = parts[0]

    def prime_fixed_lines(
        self,
        fixed_lines: Mapping[int, str],
        *,
        strict_polyphonic: bool = True,
    ) -> None:
        group_parts: dict[int, set[str]] = {}
        for index, text in fixed_lines.items():
            line = self._lines[index]
            if len(text) != line.layout.length:
                raise ContextConstraintError(
                    f"第 {index + 1} 句应为 {line.layout.length} 字，实际为 {len(text)} 字"
                )
            for position, char in enumerate(text, start=1):
                key = (index, position)
                source = self._repeat_source.get(key)
                if source is not None and source in self._repeat_chars and self._repeat_chars[source] != char:
                    raise ContextConstraintError("固定句与叠字/叠句/叠韵要求冲突")
                self._repeat_chars.setdefault(key, char)
                if source is not None:
                    self._repeat_chars.setdefault(source, char)
            for position, char in enumerate(text):
                if not _tone_matches(
                    self._lexicon.get_pingze(char),
                    line.tone_options[position],
                    strict_polyphonic,
                ):
                    raise ContextConstraintError(
                        f"第 {index + 1} 句第 {position + 1} 字与词牌格律冲突"
                    )
            if line.rhyme_group is None:
                continue
            allowed_tones = set(line.tone_options[-1])
            if line.rhyme_group == 1 and self._template.rhyme_type in {"平韵", "仄韵"}:
                allowed_tones = {
                    "平" if "平" in self._template.rhyme_type else "仄"
                }
            parts = {
                part
                for tone in allowed_tones
                for part in self._lexicon.get_rhyme_part_by_tone(text[-1], tone)
            }
            if not parts:
                raise ContextConstraintError(f"第 {index + 1} 句韵脚无法确定韵部")
            previous = group_parts.get(line.rhyme_group)
            narrowed = parts if previous is None else previous.intersection(parts)
            if not narrowed:
                raise ContextConstraintError(
                    f"第 {index + 1} 句与同组固定韵脚不属于同一韵部"
                )
            group_parts[line.rhyme_group] = narrowed
        self._locked_rhyme_parts.update(
            {group: sorted(parts)[0] for group, parts in group_parts.items()}
        )


@dataclass(frozen=True)
class HanpaiConstraintContext:
    target_length: int
    forbid_isolated_level: bool
    allow_aojiu: bool
    forbid_three_same_ending: bool
    allowed_end_tones: tuple[str, ...] = ("平", "仄")


class HanpaiConstraintProfile:
    """汉俳的变长三行布局与可选声韵规则。"""

    _BREAKS = {
        3: frozenset(),
        5: frozenset({2}),
        7: frozenset({2, 4}),
    }
    _RHYME_LINES = {
        "AAA": frozenset({0, 1, 2}),
        "ABA": frozenset({0, 2}),
        "BAA": frozenset({1, 2}),
    }

    def __init__(
        self,
        line_lengths: tuple[int, int, int],
        lexicon: RhymeLookup,
        rhyme_scheme: str | None = None,
        forbid_isolated_level: bool = True,
        allow_aojiu: bool = True,
        forbid_three_same_ending: bool = True,
    ):
        if line_lengths not in {(5, 7, 5), (3, 5, 3)}:
            raise ValueError(f"汉俳仅支持 5-7-5 或 3-5-3，收到: {line_lengths}")
        normalized_scheme = rhyme_scheme.upper().strip() if rhyme_scheme else None
        if normalized_scheme in {"NONE", "不押韵"}:
            normalized_scheme = None
        if normalized_scheme == "RANDOM":
            normalized_scheme = random.choice(tuple(self._RHYME_LINES))
        if normalized_scheme is not None and normalized_scheme not in self._RHYME_LINES:
            raise ValueError(
                f"汉俳押韵格式仅支持 AAA、ABA、BAA 或不押韵，收到: {rhyme_scheme}"
            )

        self.line_lengths = line_lengths
        self.lexicon = lexicon
        self.rhyme_scheme = normalized_scheme
        self.forbid_isolated_level = forbid_isolated_level
        self.allow_aojiu = allow_aojiu
        self.forbid_three_same_ending = forbid_three_same_ending
        self._rhyme_lines = self._RHYME_LINES.get(normalized_scheme, frozenset())
        self._layout = GenerationLayout(
            tuple(
                LineLayout(
                    length=length,
                    break_positions=self._BREAKS[length],
                    # 节奏切分只用于限制 token 不跨界，不生成句内顿号。
                    caesura_positions=frozenset(),
                    stanza_index=index,
                    line_in_stanza=0,
                    stanza_end=True,
                )
                for index, length in enumerate(line_lengths)
            )
        )

    @property
    def layout(self) -> GenerationLayout:
        return self._layout

    @property
    def rhyme_lines(self) -> frozenset[int]:
        return self._rhyme_lines

    def create_session(self, *, rhyme_mode: str = "auto", rhyme_parts: Mapping[str, str] | None = None) -> "HanpaiConstraintSession":
        return HanpaiConstraintSession(
            line_lengths=self.line_lengths,
            lexicon=self.lexicon,
            rhyme_lines=self._rhyme_lines,
            forbid_isolated_level=self.forbid_isolated_level,
            allow_aojiu=self.allow_aojiu,
            forbid_three_same_ending=self.forbid_three_same_ending,
            rhyme_mode=rhyme_mode,
            rhyme_parts=rhyme_parts,
        )


class HanpaiConstraintSession(BaseConstraintSession):
    def __init__(
        self,
        line_lengths: tuple[int, int, int],
        lexicon: RhymeLookup,
        rhyme_lines: frozenset[int],
        forbid_isolated_level: bool,
        allow_aojiu: bool,
        forbid_three_same_ending: bool,
        rhyme_mode: str = "auto",
        rhyme_parts: Mapping[str, str] | None = None,
    ):
        self._line_lengths = line_lengths
        self._lexicon = lexicon
        self._rhyme_lines = rhyme_lines
        self._forbid_isolated_level = forbid_isolated_level
        self._allow_aojiu = allow_aojiu
        self._forbid_three_same_ending = forbid_three_same_ending
        self._locked_rhyme_parts: dict[str, set[str]] | None = None
        requested = {str(key): str(value) for key, value in (rhyme_parts or {}).items()}
        requested_value = requested.get("1") or requested.get("0")
        if requested_value:
            if requested_value.lower() == "random":
                candidates = sorted({part for _, part, _ in self._lexicon.iter_rhyme_entries()})
                requested_value = random.choice(candidates) if candidates else None
            if requested_value:
                self._locked_rhyme_parts = {"平": {requested_value}, "仄": {requested_value}}
        elif rhyme_mode == "random":
            candidates = sorted({part for _, part, _ in self._lexicon.iter_rhyme_entries()})
            if candidates:
                selected = random.choice(candidates)
                self._locked_rhyme_parts = {"平": {selected}, "仄": {selected}}

    @property
    def locked_rhyme_parts(self) -> dict[str, frozenset[str]]:
        return {
            tone: frozenset(parts)
            for tone, parts in (self._locked_rhyme_parts or {}).items()
        }

    def allowed_tones_at(self, state: GenerationState, position: int) -> tuple[str, ...]:
        return ("平", "仄")

    def rhyme_constraint(
        self,
        state: GenerationState,
        pattern: str,
    ) -> RhymeConstraint | None:
        if state.line_index not in self._rhyme_lines:
            return RhymeConstraint.none()
        tone = pattern[-1]
        if self._locked_rhyme_parts is None:
            return RhymeConstraint.any(tone)
        parts = self._locked_rhyme_parts.get(tone)
        if not parts:
            return None
        return RhymeConstraint.specific(tone, parts)

    def observe_text(self, state: GenerationState, text: str) -> None:
        if state.line_index not in self._rhyme_lines:
            return
        if state.char_index + len(text) != self._line_lengths[state.line_index]:
            return

        last_char = text[-1]
        found = {
            tone: set(self._lexicon.get_rhyme_part_by_tone(last_char, tone))
            for tone in self._lexicon.get_pingze(last_char)
        }
        found = {tone: parts for tone, parts in found.items() if parts}
        if not found:
            return
        if self._locked_rhyme_parts is None:
            self._locked_rhyme_parts = found
            return

        narrowed = {
            tone: self._locked_rhyme_parts[tone].intersection(parts)
            for tone, parts in found.items()
            if tone in self._locked_rhyme_parts
        }
        narrowed = {tone: parts for tone, parts in narrowed.items() if parts}
        if narrowed:
            self._locked_rhyme_parts = narrowed

    def candidate_context(self, state: GenerationState) -> HanpaiConstraintContext:
        allowed_end_tones = ("平", "仄")
        if state.line_index in self._rhyme_lines and self._locked_rhyme_parts is not None:
            allowed_end_tones = tuple(
                tone
                for tone in ("平", "仄")
                if self._locked_rhyme_parts.get(tone)
            )
        return HanpaiConstraintContext(
            target_length=self._line_lengths[state.line_index],
            forbid_isolated_level=self._forbid_isolated_level,
            allow_aojiu=self._allow_aojiu,
            forbid_three_same_ending=self._forbid_three_same_ending,
            allowed_end_tones=allowed_end_tones,
        )

    def prime_fixed_lines(
        self,
        fixed_lines: Mapping[int, str],
        *,
        strict_polyphonic: bool = True,
    ) -> None:
        locked: dict[str, set[str]] | None = None
        for index, text in fixed_lines.items():
            if len(text) != self._line_lengths[index]:
                raise ContextConstraintError(
                    f"第 {index + 1} 行应为 {self._line_lengths[index]} 字，实际为 {len(text)} 字"
                )
            # Fixed text and final output must obey the same rules as sampled tokens.
            from .candidates import iter_tone_combinations
            from .tone_rules import HanpaiIsolatedLevelRule, RejectThreeSameEndingRule, ToneRuleSet

            rules = []
            if self._forbid_three_same_ending:
                rules.append(RejectThreeSameEndingRule())
            if self._forbid_isolated_level:
                rules.append(HanpaiIsolatedLevelRule(self._allow_aojiu))
            if rules and not ToneRuleSet(
                reject_any=tuple(rules), strict_polyphonic=strict_polyphonic,
            ).validate(iter_tone_combinations(text, self._lexicon)):
                raise ContextConstraintError(
                    f"汉俳第 {index + 1} 行违反孤平或句尾三连平仄约束"
                )
            if index not in self._rhyme_lines:
                continue
            found = {
                tone: set(self._lexicon.get_rhyme_part_by_tone(text[-1], tone))
                for tone in self._lexicon.get_pingze(text[-1])
            }
            found = {tone: parts for tone, parts in found.items() if parts}
            if not found:
                raise ContextConstraintError(f"第 {index + 1} 行韵脚无法确定韵部")
            if locked is None:
                locked = found
                continue
            locked = {
                tone: locked[tone].intersection(parts)
                for tone, parts in found.items()
                if tone in locked and locked[tone].intersection(parts)
            }
            if not locked:
                raise ContextConstraintError("固定汉俳韵脚不存在共同韵部")
        if locked is not None:
            self._locked_rhyme_parts = locked


@dataclass(frozen=True)
class RelationalConstraintContext:
    current_line: int
    current_char_idx: int
    target_length: int
    is_rhyming: bool
    locked_rhyme_parts: frozenset[str] | None
    excluded_rhyme_parts: frozenset[str] | None
    rhyme_type: str
    base_tone: int
    global_base_tone: int
    line0_rhymes: bool
    next_fixed_line: str | None = None


class RelationalConstraintProfile:
    """关系型诗体的字间规则配置。"""

    def __init__(
        self,
        line_length: int,
        num_lines: int,
        rhyme_type: str,
        lexicon: RhymeLookup,
        allow_aojiu: bool = False,
        strict_polyphonic: bool = True,
    ):
        if line_length not in (5, 7):
            raise ValueError(f"唐诗仅支持五言(5)或七言(7)，收到: {line_length}")
        if num_lines not in (4, 8) and not (num_lines >= 10 and num_lines % 2 == 0):
            raise ValueError(
                "关系型诗体句数必须为 4、8，或排律的至少十句偶数，"
                f"收到: {num_lines}"
            )
        self.line_length = line_length
        self.num_lines = num_lines
        self.rhyme_type = rhyme_type.strip()
        self.lexicon = lexicon
        self.allow_aojiu = allow_aojiu
        self.strict_polyphonic = strict_polyphonic
        breaks = frozenset({2} if line_length == 5 else {2, 4})
        self._layout = GenerationLayout(
            tuple(
                LineLayout(
                    length=line_length,
                    break_positions=breaks,
                    stanza_index=0,
                    line_in_stanza=index,
                    stanza_end=False,
                )
                for index in range(num_lines)
            )
        )

    @property
    def layout(self) -> GenerationLayout:
        return self._layout

    def create_session(self, *, rhyme_mode: str = "auto", rhyme_parts: Mapping[str, str] | None = None) -> "RelationalConstraintSession":
        return RelationalConstraintSession(
            line_length=self.line_length,
            num_lines=self.num_lines,
            rhyme_type=self.rhyme_type,
            lexicon=self.lexicon,
            allow_aojiu=self.allow_aojiu,
            strict_polyphonic=self.strict_polyphonic,
            rhyme_mode=rhyme_mode,
            rhyme_parts=rhyme_parts,
        )


class RelationalConstraintSession(BaseConstraintSession):
    def __init__(
        self,
        line_length: int,
        num_lines: int,
        rhyme_type: str,
        lexicon: RhymeLookup,
        allow_aojiu: bool = False,
        strict_polyphonic: bool = True,
        rhyme_mode: str = "auto",
        rhyme_parts: Mapping[str, str] | None = None,
    ):
        self._line_length = line_length
        self._num_lines = num_lines
        self._rhyme_type = rhyme_type
        self._lexicon = lexicon
        self._allow_aojiu = allow_aojiu
        self._strict_polyphonic = strict_polyphonic
        self._locked_rhyme_parts: set[str] | None = None
        self._global_base_tone = 2
        self._line0_rhymes = False
        self._fixed_lines: dict[int, str] = {}
        self._active_line = 0
        self._current_base_tone = 2
        requested = {str(key): str(value) for key, value in (rhyme_parts or {}).items()}
        selected = requested.get("1") or requested.get("0")
        if selected and selected.lower() == "random":
            selected = None
        if selected is None and rhyme_mode == "random":
            expected = "平" if "平" in self._rhyme_type else "仄"
            candidates = sorted({part for _, part, tone in self._lexicon.iter_rhyme_entries() if tone == expected})
            selected = random.choice(candidates) if candidates else None
        if selected:
            expected = "平" if "平" in self._rhyme_type else "仄"
            if not any(part == selected and tone == expected for _, part, tone in self._lexicon.iter_rhyme_entries()):
                raise ValueError(f"韵部 {selected} 没有可用于{expected}韵的字")
            self._locked_rhyme_parts = {selected}

    def _ensure_line(self, line_index: int) -> None:
        if line_index == self._active_line:
            return
        self._active_line = line_index
        if self._global_base_tone == 2:
            self._current_base_tone = 2
        elif line_index % 4 in (1, 2):
            self._current_base_tone = 1 - self._global_base_tone
        else:
            self._current_base_tone = self._global_base_tone

    def _is_rhyming_line(self, line_index: int) -> bool:
        if line_index == 0:
            return self._line0_rhymes
        return (line_index + 1) % 2 == 0

    def allowed_tones_at(self, state: GenerationState, position: int) -> tuple[str, ...]:
        self._ensure_line(state.line_index)
        base = self._current_base_tone
        if base == 2:
            return ("平", "仄")
        if (
            self._allow_aojiu
            and "平" in self._rhyme_type
            and not self._is_rhyming_line(state.line_index)
            and position == self._line_length - 2
        ):
            return ("平", "仄")
        if position == 1 or (position == 5 and self._line_length >= 7):
            return ("平",) if base == 0 else ("仄",)
        if position == 3:
            return ("仄",) if base == 0 else ("平",)
        return ("平", "仄")

    def rhyme_constraint(
        self,
        state: GenerationState,
        pattern: str,
    ) -> RhymeConstraint | None:
        self._ensure_line(state.line_index)
        expected = "平" if "平" in self._rhyme_type else "仄"
        if state.line_index == 0 and not self._line0_rhymes:
            if pattern[-1] != expected:
                return RhymeConstraint.none()
            if self._locked_rhyme_parts:
                return RhymeConstraint.specific(expected, self._locked_rhyme_parts)
            return RhymeConstraint.any(expected)
        is_rhyming = self._is_rhyming_line(state.line_index)
        if is_rhyming:
            if pattern[-1] != expected:
                return None
            if self._locked_rhyme_parts:
                return RhymeConstraint.specific(expected, self._locked_rhyme_parts)
            return RhymeConstraint.any(expected)
        opposite = "仄" if expected == "平" else "平"
        if pattern[-1] != opposite:
            return None
        return RhymeConstraint.none()

    def observe_text(self, state: GenerationState, text: str) -> None:
        self._ensure_line(state.line_index)
        simulated = state.current_line_text + text
        if self._current_base_tone == 2:
            tone = self._infer_base_tone(simulated)
            if tone != 2:
                self._current_base_tone = tone
                if state.line_index == 0 and self._global_base_tone == 2:
                    self._global_base_tone = tone

        if state.char_index + len(text) != self._line_length:
            return
        last_char = text[-1]
        if self._is_rhyming_line(state.line_index):
            expected = "平" if "平" in self._rhyme_type else "仄"
            parts = set(self._lexicon.get_rhyme_part_by_tone(last_char, expected))
            if parts:
                if self._locked_rhyme_parts is None:
                    self._locked_rhyme_parts = parts
                else:
                    intersection = self._locked_rhyme_parts.intersection(parts)
                    if intersection:
                        self._locked_rhyme_parts = intersection
        elif state.line_index == 0 and not self._line0_rhymes:
            tones = tuple(dict.fromkeys(self._lexicon.get_pingze(last_char)))
            expected = "平" if "平" in self._rhyme_type else "仄"
            line0_rhymes = (
                bool(tones) and all(tone == expected for tone in tones)
                if self._strict_polyphonic
                else expected in tones
            )
            if line0_rhymes:
                parts = set(self._lexicon.get_rhyme_part_by_tone(last_char, expected))
                line0_rhymes = bool(parts) and (not self._locked_rhyme_parts or bool(parts & self._locked_rhyme_parts))
                if line0_rhymes and len(tones) > 1:
                    rhyme_tone = 0 if expected == "平" else 1
                    rhyme_viable = first_line_reading_is_viable(
                        simulated, self._line_length, self._current_base_tone,
                        rhyme_tone, self._lexicon, expected,
                        allow_aojiu=self._allow_aojiu, strict_polyphonic=self._strict_polyphonic,
                    )
                    opposite = "仄" if expected == "平" else "平"
                    other_viable = first_line_reading_is_viable(
                        simulated, self._line_length, self._current_base_tone,
                        rhyme_tone, self._lexicon, opposite,
                        allow_aojiu=self._allow_aojiu, strict_polyphonic=self._strict_polyphonic,
                    )
                    if not rhyme_viable and other_viable:
                        line0_rhymes = False
            if line0_rhymes:
                self._line0_rhymes = True
                parts = self._lexicon.get_rhyme_part_by_tone(last_char, expected)
                if parts:
                    self._locked_rhyme_parts = set(parts)

    def _infer_base_tone(self, line_text: str) -> int:
        if len(line_text) >= 2:
            tones = self._lexicon.get_pingze(line_text[1])
            if len(tones) == 1:
                return 0 if tones[0] == "平" else 1
        if len(line_text) >= 4:
            tones = self._lexicon.get_pingze(line_text[3])
            if len(tones) == 1:
                return 1 if tones[0] == "平" else 0
        if self._line_length >= 7 and len(line_text) >= 6:
            tones = self._lexicon.get_pingze(line_text[5])
            if len(tones) == 1:
                return 0 if tones[0] == "平" else 1
        return 2

    def candidate_context(self, state: GenerationState) -> RelationalConstraintContext:
        self._ensure_line(state.line_index)
        return RelationalConstraintContext(
            current_line=state.line_index,
            current_char_idx=state.char_index,
            target_length=self._line_length,
            is_rhyming=self._is_rhyming_line(state.line_index),
            locked_rhyme_parts=(
                frozenset(self._locked_rhyme_parts)
                if self._locked_rhyme_parts is not None
                else None
            ),
            excluded_rhyme_parts=None,
            rhyme_type=self._rhyme_type,
            base_tone=self._current_base_tone,
            global_base_tone=self._global_base_tone,
            line0_rhymes=self._line0_rhymes,
            next_fixed_line=self._fixed_lines.get(state.line_index + 1),
        )

    def prime_fixed_lines(
        self,
        fixed_lines: Mapping[int, str],
        *,
        strict_polyphonic: bool = True,
    ) -> None:
        for index, text in fixed_lines.items():
            if not 0 <= index < self._num_lines:
                raise ContextConstraintError(f"指定句序号超出范围: {index + 1}")
            if len(text) != self._line_length:
                raise ContextConstraintError(
                    f"第 {index + 1} 句应为 {self._line_length} 字，实际为 {len(text)} 字"
                )

        expected_rhyme_tone = "平" if "平" in self._rhyme_type else "仄"
        def first_line_rhymes(text):
            tones = self._lexicon.get_pingze(text[-1])
            if not _tone_matches(tones, {expected_rhyme_tone}, strict_polyphonic):
                return False
            if len(set(tones)) < 2:
                return True
            rhyme_tone = 0 if expected_rhyme_tone == "平" else 1
            base = self._infer_base_tone(text)
            rhyme_ok = first_line_reading_is_viable(
                text, self._line_length, base, rhyme_tone, self._lexicon,
                expected_rhyme_tone, allow_aojiu=self._allow_aojiu,
                strict_polyphonic=strict_polyphonic,
            )
            parts = set(self._lexicon.get_rhyme_part_by_tone(text[-1], expected_rhyme_tone))
            rhyme_ok = rhyme_ok and bool(parts) and (not self._locked_rhyme_parts or bool(parts & self._locked_rhyme_parts))
            if rhyme_ok:
                return True
            opposite = "仄" if expected_rhyme_tone == "平" else "平"
            return not first_line_reading_is_viable(
                text, self._line_length, base, rhyme_tone, self._lexicon, opposite,
                allow_aojiu=self._allow_aojiu, strict_polyphonic=strict_polyphonic,
            )
        if 0 in fixed_lines:
            self._line0_rhymes = first_line_rhymes(fixed_lines[0])

        possible_bases = []
        for global_base in (0, 1):
            valid = True
            for index, text in fixed_lines.items():
                base = 1 - global_base if index % 4 in (1, 2) else global_base
                rhyme_tone = 0 if expected_rhyme_tone == "平" else 1
                require_rescue = False
                if self._allow_aojiu and index % 2 == 1 and index - 1 in fixed_lines:
                    previous_index = index - 1
                    previous_base = 1 - global_base if previous_index % 4 in (1, 2) else global_base
                    require_rescue = tang_line_requires_cross_rescue(
                        fixed_lines[previous_index], self._line_length,
                        previous_base, rhyme_tone, self._lexicon,
                        is_first_line=previous_index == 0,
                        strict_polyphonic=strict_polyphonic,
                    )
                if not has_viable_tang_completion(
                    text, self._line_length, base,
                    2 if index == 0 else rhyme_tone if self._is_rhyming_line(index) else 1 - rhyme_tone,
                    self._lexicon, allow_aojiu=self._allow_aojiu,
                    strict_polyphonic=strict_polyphonic,
                    is_first_line=index == 0, rhyme_tone=rhyme_tone,
                    require_cross_rescue=require_rescue,
                ):
                    valid = False
                    break
                if not valid:
                    break
            if valid:
                possible_bases.append(global_base)
        if not possible_bases:
            raise ContextConstraintError("固定诗句无法共同确定合法的替、对、粘格式")
        if len(possible_bases) == 1:
            self._global_base_tone = possible_bases[0]
            self._current_base_tone = possible_bases[0]

        opposite = "仄" if expected_rhyme_tone == "平" else "平"
        rhyme_parts: set[str] | None = None
        for index, text in fixed_lines.items():
            end_tones = self._lexicon.get_pingze(text[-1])
            is_rhyming = index > 0 and (index + 1) % 2 == 0
            if index == 0:
                self._line0_rhymes = first_line_rhymes(text)
                is_rhyming = self._line0_rhymes
            elif not is_rhyming and not _tone_matches(
                end_tones, {opposite}, strict_polyphonic
            ):
                raise ContextConstraintError(f"第 {index + 1} 句句尾声调与诗式冲突")
            if not is_rhyming:
                continue
            if not _tone_matches(end_tones, {expected_rhyme_tone}, strict_polyphonic):
                raise ContextConstraintError(f"第 {index + 1} 句韵脚声调与诗式冲突")
            parts = set(
                self._lexicon.get_rhyme_part_by_tone(text[-1], expected_rhyme_tone)
            )
            if strict_polyphonic and len(parts) != 1:
                raise ContextConstraintError(f"第 {index + 1} 句韵脚存在歧义")
            if not parts:
                raise ContextConstraintError(f"第 {index + 1} 句韵脚无法确定韵部")
            rhyme_parts = parts if rhyme_parts is None else rhyme_parts.intersection(parts)
            if not rhyme_parts:
                raise ContextConstraintError("固定偶数句不存在共同韵部")
        if rhyme_parts is not None:
            self._locked_rhyme_parts = rhyme_parts
        self._fixed_lines = dict(fixed_lines)
