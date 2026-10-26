from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

from .candidates import (
    decode_position_candidate,
    has_viable_tang_completion,
    _has_viable_tang_tones,
    iter_tone_combinations,
    tang_line_requires_cross_rescue,
    would_force_three_same_ending,
)
from .constraints import HanpaiConstraintContext, RelationalConstraintContext
from .data import RhymeLookup
from .domain import GenerationState
from .tone_rules import HanpaiIsolatedLevelRule, RejectThreeSameEndingRule, ToneRuleSet
from .vocab import TokenizerLike, VocabLookup


@dataclass(frozen=True)
class CandidateContext:
    state: GenerationState
    constraint: Any
    tokenizer: TokenizerLike
    vocab: VocabLookup
    cache: dict[object, Any] | None = None
    # This namespace is owned by this immutable context, never by a caller's
    # potentially reused cache. State and relational parameters are implicit
    # in its lifetime; policy objects separate lexicons and rule options.
    _memo: dict[object, Any] = field(default_factory=dict, init=False, repr=False, compare=False)


class CandidatePolicy(Protocol):
    def evaluate(self, token_id: int, context: CandidateContext) -> float | None: ...


@dataclass(frozen=True)
class PolicyTier:
    name: str
    policies: Sequence[CandidatePolicy]

    def evaluate(self, token_id: int, context: CandidateContext) -> float | None:
        penalty = 0.0
        for policy in self.policies:
            item_penalty = policy.evaluate(token_id, context)
            if item_penalty is None:
                return None
            penalty += item_penalty
        return penalty


class BoundaryCoherencePolicy:
    """统一的句读粘连惩罚。"""

    def __init__(self, common_bigrams: frozenset[str], penalty: float):
        if penalty < 0:
            raise ValueError(f"句读粘连惩罚不能为负数: {penalty}")
        self._common_bigrams = common_bigrams
        self._penalty = penalty

    def evaluate(self, token_id: int, context: CandidateContext) -> float | None:
        state = context.state
        if state.line is None or state.char_index not in state.line.break_positions:
            return 0.0
        token_text = context.vocab.text_for_token(token_id)
        if not token_text or not state.current_line_text:
            return 0.0
        bigram = state.current_line_text[-1] + token_text[0]
        return self._penalty if bigram in self._common_bigrams else 0.0


class RepetitionPenaltyPolicy:
    def __init__(
        self,
        base_penalty: float = 20.0,
        decay_rate: float = 0.05,
        min_penalty: float = 8.0,
    ):
        self._base_penalty = base_penalty
        self._decay_rate = decay_rate
        self._min_penalty = min_penalty
        self._cached_text: str | None = None
        self._cached_last_positions: dict[str, int] = {}

    def evaluate(self, token_id: int, context: CandidateContext) -> float | None:
        all_text = context.state.all_text
        last_positions = self._last_positions_for(all_text)
        if not last_positions:
            return 0.0
        token_text = context.vocab.text_for_token(token_id)
        current_position = len(all_text)
        penalty = 0.0
        for char in token_text:
            if char not in last_positions:
                continue
            distance = max(1, current_position - last_positions[char])
            decayed = self._base_penalty * math.exp(-self._decay_rate * distance)
            penalty += max(self._min_penalty, decayed)
        return penalty

    def _last_positions_for(self, text: str) -> dict[str, int]:
        if text != self._cached_text:
            self._cached_last_positions = {
                char: position for position, char in enumerate(text)
            }
            self._cached_text = text
        return self._cached_last_positions


class RewriteRepetitionPenaltyPolicy:
    """Score repeats against generated text and immutable lines after the rewrite span."""

    def __init__(
        self,
        future_positions: dict[str, tuple[int, ...]],
        *,
        fixed_penalty: float | None = None,
        base_penalty: float = 20.0,
        decay_rate: float = 0.05,
        min_penalty: float = 8.0,
    ):
        self._future_positions = future_positions
        self._fixed_penalty = fixed_penalty
        self._base_penalty = base_penalty
        self._decay_rate = decay_rate
        self._min_penalty = min_penalty

    def evaluate(self, token_id: int, context: CandidateContext) -> float | None:
        text = context.state.all_text
        token_text = context.vocab.text_for_token(token_id)
        positions = {char: index for index, char in enumerate(text)}
        penalty = 0.0
        for offset, char in enumerate(token_text):
            current = len(text) + offset
            previous = positions.get(char)
            future = self._future_positions.get(char, ())
            nearest_future = next((position for position in future if position > current), None)
            if previous is not None or nearest_future is not None:
                if self._fixed_penalty is not None:
                    penalty += self._fixed_penalty
                else:
                    distances = []
                    if previous is not None:
                        distances.append(current - previous)
                    if nearest_future is not None:
                        distances.append(nearest_future - current)
                    distance = max(1, min(distances))
                    penalty += max(
                        self._min_penalty,
                        self._base_penalty * math.exp(-self._decay_rate * distance),
                    )
            positions[char] = current
        return penalty


class HanpaiVerifierPolicy:
    """校验汉俳可选的孤平、拗救和句尾三连同规则。"""

    def __init__(self, lexicon: RhymeLookup, strict_polyphonic: bool = True):
        self._lexicon = lexicon
        self._strict_polyphonic = strict_polyphonic

    def evaluate(self, token_id: int, context: CandidateContext) -> float | None:
        info = context.constraint
        if not isinstance(info, HanpaiConstraintContext):
            raise TypeError("HanpaiVerifierPolicy 需要 HanpaiConstraintContext")

        candidate = decode_position_candidate(
            token_id, context.tokenizer, context.vocab, context.state
        )
        if candidate is None:
            return None
        token_chars = candidate.text

        state = context.state
        if state.line is None:
            return None
        simulated = context.state.current_line_text + token_chars
        if len(simulated) > info.target_length:
            return None
        if info.forbid_three_same_ending and would_force_three_same_ending(
            simulated,
            info.target_length,
            info.allowed_end_tones,
            self._lexicon,
            reject_ambiguous=self._strict_polyphonic,
        ):
            return None
        if len(simulated) < info.target_length:
            return 0.0
        if not info.forbid_isolated_level and not info.forbid_three_same_ending:
            return 0.0

        reject_rules = (
            (RejectThreeSameEndingRule(),)
            if info.forbid_three_same_ending
            else ()
        )
        accept_rules = (
            (HanpaiIsolatedLevelRule(info.allow_aojiu),)
            if info.forbid_isolated_level
            else ()
        )
        tone_rules = ToneRuleSet(
            reject_any=reject_rules,
            accept_any=accept_rules,
            strict_polyphonic=self._strict_polyphonic,
        )
        if not tone_rules.validate(iter_tone_combinations(simulated, self._lexicon)):
            return None
        return 0.0


class TangVerifierPolicy:
    """关系型诗体的格律校验；mode=critical 时只保留核心格律底线。"""

    def __init__(
        self,
        lexicon: RhymeLookup,
        mode: str = "full",
        allow_aojiu: bool = False,
        enforce_style: bool = True,
        strict_polyphonic: bool = True,
        repetition_penalty_enabled: bool = True,
    ):
        if mode not in {"full", "critical"}:
            raise ValueError(f"未知唐诗校验模式: {mode}")
        self._lexicon = lexicon
        self._mode = mode
        self._allow_aojiu = allow_aojiu
        self._enforce_style = enforce_style
        self._strict_polyphonic = strict_polyphonic
        self._repetition_penalty_enabled = repetition_penalty_enabled

    def _candidate(self, token_id, context):
        key = ("tang-candidate", token_id)
        if key not in context._memo:
            context._memo[key] = decode_position_candidate(
                token_id, context.tokenizer, context.vocab, context.state)
        return context._memo[key]

    def _tones(self, text):
        lookup = getattr(self._lexicon, "pingze_options", None)
        if lookup is not None:
            return tuple(map(lookup, text))
        return tuple(tuple(dict.fromkeys(self._lexicon.get_pingze(char))) for char in text)

    def _step(self, state, info, context):
        key = ("tang-step", self)
        if key in context._memo:
            return context._memo[key]
        value = (self._tones(state.current_line_text),
                 self._requires_cross_rescue(state, info), Counter(state.all_text),
                 {size: (
                     frozenset(state.current_line_text[i:i + size] for i in range(len(state.current_line_text) - size + 1))
                     | frozenset(state.all_text[i:i + size] for i in range(len(state.all_text) - size + 1)),
                     frozenset(state.all_text[i:i + size] for i in range(len(state.all_text) - size + 1)),
                 ) for size in (2, 3)})
        context._memo[key] = value
        return value

    def without_repetition_penalty(self) -> "TangVerifierPolicy":
        return TangVerifierPolicy(
            self._lexicon,
            self._mode,
            allow_aojiu=self._allow_aojiu,
            enforce_style=self._enforce_style,
            strict_polyphonic=self._strict_polyphonic,
            repetition_penalty_enabled=False,
        )

    def evaluate(self, token_id: int, context: CandidateContext) -> float | None:
        relational = context.constraint
        if not isinstance(relational, RelationalConstraintContext):
            raise TypeError("TangVerifierPolicy 需要 RelationalConstraintContext")
        if self._mode == "critical":
            return self._critical(token_id, context, relational)
        return self._full(token_id, context, relational)

    def _full(
        self,
        token_id: int,
        context: CandidateContext,
        info: RelationalConstraintContext,
    ) -> float | None:
        candidate = self._candidate(token_id, context)
        if candidate is None:
            return None
        token_chars = candidate.text
        if self._enforce_style and any(
            char in {"的", "些", "么", "了"} for char in token_chars
        ):
            return None

        state = context.state
        if state.line is None:
            return None

        simulated = state.current_line_text + token_chars
        target_length = info.target_length
        if len(simulated) > target_length:
            return None

        if not self._has_viable_completion(simulated, state, info, context):
            return None
        if len(simulated) == target_length:
            if self._enforce_style and not self._check_end_character(
                simulated[-1], state, target_length
            ):
                return None
            if not self._check_rhyme(simulated[-1], info):
                return None

        penalty = 0.0
        if self._enforce_style:
            if self._repetition_penalty_enabled:
                counts = self._step(state, info, context)[2]
                repetition_penalty = self._repetition_penalty(token_chars, state, counts)
                if repetition_penalty is None:
                    return None
                penalty = repetition_penalty
            if not self._check_ngrams(token_chars, state, self._step(state, info, context)[3]):
                return None
        # Viability already rejects every empty reading, including token chars.
        return penalty

    def _has_viable_completion(self, simulated, state, info, context):
        prefix_tones, cross_rescue, _, _ = self._step(state, info, context)
        options = prefix_tones + self._tones(simulated[len(state.current_line_text):])
        # Viability (including first-line resolution and fixed-line rescue)
        # depends on reading OPTIONS, not the literal spelling. Text/rhyme/
        # repetition checks remain outside this shared result.
        cache = context._memo
        key = ("tang-completion", self, options)
        if key in cache:
            return cache[key]
        result = bool(all(options)) and _has_viable_tang_tones(
            options,
            info.target_length,
            self._resolve_line_tone(info.base_tone, simulated, state.current_line_text),
            self._end_tone(info),
            allow_aojiu=self._allow_aojiu,
            strict_polyphonic=self._strict_polyphonic,
            is_first_line=info.current_line == 0,
            rhyme_tone=0 if "平" in info.rhyme_type else 1,
            require_cross_rescue=cross_rescue,
        )
        if (
            result and self._allow_aojiu and info.next_fixed_line is not None
            and info.current_line % 2 == 0 and len(simulated) == info.target_length
        ):
            rhyme_tone = 0 if "平" in info.rhyme_type else 1
            line_tone = self._resolve_line_tone(info.base_tone, simulated, state.current_line_text)
            if tang_line_requires_cross_rescue(
                simulated, info.target_length, line_tone, rhyme_tone, self._lexicon,
                is_first_line=info.current_line == 0,
                strict_polyphonic=self._strict_polyphonic,
            ):
                result = has_viable_tang_completion(
                    info.next_fixed_line, info.target_length,
                    self._line_tone_at(info.global_base_tone, info.current_line + 1),
                    rhyme_tone, self._lexicon, allow_aojiu=True,
                    strict_polyphonic=self._strict_polyphonic, rhyme_tone=rhyme_tone,
                    require_cross_rescue=True,
                )
        cache[key] = result
        return result

    def _critical(
        self,
        token_id: int,
        context: CandidateContext,
        info: RelationalConstraintContext,
    ) -> float | None:
        candidate = self._candidate(token_id, context)
        if candidate is None:
            return None
        token_chars = candidate.text
        state = context.state
        if state.line is None:
            return None
        simulated = state.current_line_text + token_chars
        if len(simulated) > info.target_length:
            return None
        if not self._has_viable_completion(simulated, state, info, context):
            return None
        if len(simulated) == info.target_length:
            if not self._check_rhyme(simulated[-1], info):
                return None
        return 0.0

    def _resolve_line_tone(self, line_tone: int, simulated: str, previous: str) -> int:
        if line_tone == 2 and len(simulated) >= 2 and len(previous) < 2:
            tones = self._lexicon.get_pingze(simulated[1])
            if len(tones) == 1:
                return 0 if tones[0] == "平" else 1
        return line_tone

    @staticmethod
    def _end_tone(info: RelationalConstraintContext) -> int:
        if info.is_rhyming:
            return 0 if "平" in info.rhyme_type else 1
        if info.current_line == 0:
            return 2
        return 1 if "平" in info.rhyme_type else 0

    def _requires_cross_rescue(
        self,
        state: GenerationState,
        info: RelationalConstraintContext,
    ) -> bool:
        if not self._allow_aojiu or info.current_line == 0 or info.current_line % 2 == 0:
            return False
        target = info.target_length
        start = (info.current_line - 1) * target
        previous = state.all_text[start : start + target]
        previous_index = info.current_line - 1
        previous_tone = self._line_tone_at(info.global_base_tone, previous_index)
        rhyme_tone = 0 if "平" in info.rhyme_type else 1
        return tang_line_requires_cross_rescue(
            previous,
            target,
            previous_tone,
            rhyme_tone,
            self._lexicon,
            is_first_line=previous_index == 0,
            strict_polyphonic=self._strict_polyphonic,
        )

    @staticmethod
    def _line_tone_at(global_tone: int, line_index: int) -> int:
        if global_tone == 2:
            return 2
        if line_index % 4 in (1, 2):
            return 1 - global_tone
        return global_tone

    @staticmethod
    def _check_end_character(char: str, state: GenerationState, target_length: int) -> bool:
        if char == "不":
            return False
        for previous_line in range(state.line_index):
            end_position = (previous_line + 1) * target_length - 1
            if end_position < len(state.all_text) and state.all_text[end_position] == char:
                return False
        return True

    def _check_rhyme(self, char: str, info: RelationalConstraintContext) -> bool:
        expected = "平" if "平" in info.rhyme_type else "仄"
        is_rhyming = info.is_rhyming
        if info.current_line == 0:
            tones = tuple(dict.fromkeys(self._lexicon.get_pingze(char)))
            is_rhyming = (
                bool(tones) and all(tone == expected for tone in tones)
                if self._strict_polyphonic
                else expected in tones
            )
        if not is_rhyming:
            return True
        parts = set(self._lexicon.get_rhyme_part_by_tone(char, expected))
        if not parts or (self._strict_polyphonic and len(parts) != 1):
            return False
        if info.locked_rhyme_parts:
            if self._strict_polyphonic:
                if not parts.issubset(info.locked_rhyme_parts):
                    return False
            elif not info.locked_rhyme_parts.intersection(parts):
                return False
        return True

    @staticmethod
    def _repetition_penalty(token_chars: str, state: GenerationState, counts=None) -> float | None:
        penalty = 0.0
        for char in token_chars:
            if char in state.current_line_text:
                penalty += 30.0
            count = state.all_text.count(char) if counts is None else counts[char]
            if count >= 4:
                return None
            if count >= 2:
                penalty += count * 15.0
            elif count == 1:
                penalty += 5.0
        return penalty

    @staticmethod
    def _check_ngrams(token_chars: str, state: GenerationState, grams=None) -> bool:
        line = state.current_line_text
        all_text = state.all_text
        for size in (3, 2):
            if len(token_chars) >= size:
                for index in range(len(token_chars) - size + 1):
                    gram = token_chars[index : index + size]
                    seen = (gram in line or gram in all_text) if grams is None else gram in grams[size][0]
                    if seen:
                        return False
        all_pairs = all_text if grams is None else grams[2][1]
        all_triples = all_text if grams is None else grams[3][1]
        if len(line) >= 2 and token_chars and line[-2:] + token_chars[0] in all_triples:
            return False
        if line and len(token_chars) >= 2 and line[-1] + token_chars[:2] in all_triples:
            return False
        if line and token_chars and line[-1] + token_chars[0] in all_pairs:
            return False
        return True
