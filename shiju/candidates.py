from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from itertools import product
from typing import Iterable

from .data import RhymeLookup
from .domain import GenerationState
from .tone_rules import RejectThreeSameEndingRule
from .vocab import TokenizerLike, VocabLookup


_TEXT_CHAR = re.compile(r"[\u4e00-\u9fa5A-Za-z]")
_FORBIDDEN_TOKEN_TEXT = re.compile(r"[\s　，。、？！；：\n\r]")


@dataclass(frozen=True)
class CandidateText:
    raw: str
    text: str


def decode_candidate(
    token_id: int,
    tokenizer: TokenizerLike,
    vocab: VocabLookup,
) -> CandidateText | None:
    """解码一个候选 token，并拒绝含标点或非诗文字符的 token。"""
    cache = getattr(vocab, "_decoded_candidate_cache", None)
    # Only indexed tokens are immutable properties of this vocab instance.
    # Unknown IDs can depend on the supplied tokenizer and are never retained.
    indexed = cache is not None and token_id in vocab._token_to_raw_text
    if indexed and token_id in cache:
        return cache[token_id]
    candidate = _decode_candidate_uncached(token_id, tokenizer, vocab)
    if indexed:
        cache[token_id] = candidate
    return candidate


def _decode_candidate_uncached(token_id, tokenizer, vocab):
    raw_text_for_token = getattr(vocab, "raw_text_for_token", None)
    raw = raw_text_for_token(token_id) if raw_text_for_token else ""
    if not raw:
        raw = tokenizer.decode([token_id])
    text = vocab.text_for_token(token_id)
    if not text:
        text = raw.replace(" ", "").replace("\r", "")
    text = "".join(char for char in text if _TEXT_CHAR.fullmatch(char))
    if _FORBIDDEN_TOKEN_TEXT.search(raw) or not text:
        return None
    return CandidateText(raw=raw, text=text)


def crosses_line_break(state: GenerationState, text: str) -> bool:
    if state.line is None:
        return True
    new_position = state.char_index + len(text)
    boundaries = (
        state.line.break_positions
        | state.line.caesura_positions
        | frozenset({state.line.length})
    )
    return any(state.char_index < point < new_position for point in boundaries)


def decode_position_candidate(
    token_id: int,
    tokenizer: TokenizerLike,
    vocab: VocabLookup,
    state: GenerationState,
) -> CandidateText | None:
    candidate = decode_candidate(token_id, tokenizer, vocab)
    if candidate is None or crosses_line_break(state, candidate.text):
        return None
    return candidate


def iter_tone_combinations(
    text: str,
    lexicon: RhymeLookup,
) -> Iterable[tuple[str, ...]]:
    options = [lexicon.get_pingze(char) for char in text]
    if any(not tones for tones in options):
        return ()
    return product(*options)


def has_three_same_ending(
    text: str,
    lexicon: RhymeLookup,
) -> bool:
    """只要存在一种末三字全平或全仄的解释，就返回 True。"""
    if len(text) < 3:
        return False
    rule = RejectThreeSameEndingRule()
    return any(
        not rule.validate(tones)
        for tones in iter_tone_combinations(text, lexicon)
    )


def would_force_three_same_ending(
    text: str,
    target_length: int,
    allowed_end_tones: Iterable[str],
    lexicon: RhymeLookup,
    *,
    reject_ambiguous: bool = False,
) -> bool:
    """倒数第二字生成后，判断所有可用末字声调是否都会形成三连同。"""
    if len(text) != target_length - 1 or len(text) < 2:
        return False

    end_tones = tuple(dict.fromkeys(allowed_end_tones))
    if not end_tones:
        return False
    third_tones = tuple(lexicon.get_pingze(text[-2]))
    second_tones = tuple(lexicon.get_pingze(text[-1]))
    if not third_tones or not second_tones:
        return False

    def forces_tone(end_tone: str) -> bool:
        if reject_ambiguous:
            return end_tone in third_tones and end_tone in second_tones
        return third_tones == (end_tone,) and second_tones == (end_tone,)

    return all(forces_tone(end_tone) for end_tone in end_tones)


def has_viable_tang_completion(
    text: str,
    target_length: int,
    line_tone: int,
    end_tone: int,
    lexicon: RhymeLookup,
    *,
    allow_aojiu: bool = False,
    strict_polyphonic: bool = True,
    is_first_line: bool = False,
    rhyme_tone: int = 0,
    require_cross_rescue: bool = False,
) -> bool:
    """判断关系型诗句的当前前缀能否补成合法的完整平仄序列。"""
    tone_options = tuple(
        tuple(dict.fromkeys(lexicon.get_pingze(char))) for char in text
    )
    if any(not tones for tones in tone_options):
        return False
    return _has_viable_tang_tones(
        tone_options,
        target_length,
        line_tone,
        end_tone,
        allow_aojiu,
        strict_polyphonic,
        is_first_line,
        rhyme_tone,
        require_cross_rescue,
    )


def has_viable_tang_completion_from_options(
    tone_options: tuple[tuple[str, ...], ...],
    target_length: int,
    line_tone: int,
    end_tone: int,
    *,
    allow_aojiu: bool = False,
    strict_polyphonic: bool = True,
    is_first_line: bool = False,
    rhyme_tone: int = 0,
    require_cross_rescue: bool = False,
) -> bool:
    """Use a precomputed finite pattern table to test prefix viability."""
    if len(tone_options) > target_length or any(not tones for tones in tone_options):
        return False
    if is_first_line and end_tone == 2:
        return _has_viable_tang_tones(
            tone_options, target_length, line_tone, end_tone,
            allow_aojiu, strict_polyphonic, is_first_line,
            rhyme_tone, require_cross_rescue,
        )
    patterns = _valid_tang_patterns(
        target_length,
        line_tone,
        end_tone,
        allow_aojiu,
        strict_polyphonic,
        is_first_line,
        rhyme_tone,
        require_cross_rescue,
    )
    prefixes = tuple(product(*tone_options))
    if strict_polyphonic:
        return all(
            any(pattern[:len(prefix)] == prefix for pattern in patterns)
            for prefix in prefixes
        )
    return any(
        any(pattern[:len(prefix)] == prefix for pattern in patterns)
        for prefix in prefixes
    )


@lru_cache(maxsize=256)
def _valid_tang_patterns(
    target_length: int,
    line_tone: int,
    end_tone: int,
    allow_aojiu: bool,
    strict_polyphonic: bool,
    is_first_line: bool,
    rhyme_tone: int,
    require_cross_rescue: bool,
) -> tuple[tuple[str, ...], ...]:
    """Enumerate the small binary tone space once per rule configuration."""
    patterns = []
    for tones in product(("平", "仄"), repeat=target_length):
        rhyme_value = "平" if rhyme_tone == 0 else "仄"
        # When the first-line ending is still unconstrained (end_tone=2),
        # the original prefix algorithm treats an ambiguous ending as non-rhyming.
        first_line_rhymes = (
            False if end_tone == 2 else tones[-1] == rhyme_value
        ) if is_first_line else None
        resolved = _resolve_tang_line_tone(tones, line_tone)
        if _tang_tones_are_valid(
            tones,
            target_length,
            resolved,
            end_tone,
            allow_aojiu,
            is_first_line,
            rhyme_tone,
            require_cross_rescue,
            first_line_rhymes,
        ):
            patterns.append(tones)
    return tuple(patterns)


def tang_line_requires_cross_rescue(
    text: str,
    target_length: int,
    line_tone: int,
    rhyme_tone: int,
    lexicon: RhymeLookup,
    *,
    is_first_line: bool = False,
    strict_polyphonic: bool = True,
) -> bool:
    """判断已完成的出句是否把四/六拗的救位留给下一句。"""
    tone_options = tuple(
        tuple(dict.fromkeys(lexicon.get_pingze(char))) for char in text
    )
    if len(tone_options) != target_length or any(not tones for tones in tone_options):
        return False

    pending: list[bool] = []
    first_line_rhymes = _first_line_rhymes(
        tone_options,
        rhyme_tone,
        strict_polyphonic,
    ) if is_first_line else None
    for tones in product(*tone_options):
        resolved = _resolve_tang_line_tone(tones, line_tone)
        if not _tang_tones_are_valid(
            tones,
            target_length,
            resolved,
            2 if is_first_line else 1 - rhyme_tone,
            True,
            is_first_line,
            rhyme_tone,
            False,
            first_line_rhymes,
        ):
            continue
        pending.append(_has_unresolved_major_ao(tones, target_length, resolved))

    if not pending:
        return False
    return any(pending) if strict_polyphonic else all(pending)


def _resolve_tang_line_tone(tones: tuple[str, ...], line_tone: int) -> int:
    if line_tone != 2:
        return line_tone
    return 0 if tones[1] == "平" else 1


def _expected_tone_at(position: int, target_length: int, line_tone: int) -> str | None:
    if position == 1 or (target_length == 7 and position == 5):
        return "平" if line_tone == 0 else "仄"
    if position == 3:
        return "仄" if line_tone == 0 else "平"
    return None


def _has_unresolved_major_ao(
    tones: tuple[str, ...],
    target_length: int,
    line_tone: int,
) -> bool:
    key_position = target_length - 2
    rescue_position = key_position - 1
    expected = _expected_tone_at(key_position, target_length, line_tone)
    return (
        expected == "平"
        and tones[key_position] == "仄"
        and tones[rescue_position] != "平"
    )


def _first_line_rhymes(
    tone_options: tuple[tuple[str, ...], ...],
    rhyme_tone: int,
    strict_polyphonic: bool,
) -> bool:
    rhyme_value = "平" if rhyme_tone == 0 else "仄"
    last_options = tone_options[-1]
    if strict_polyphonic:
        return bool(last_options) and all(tone == rhyme_value for tone in last_options)
    return rhyme_value in last_options


def _tang_tones_are_valid(
    tones: tuple[str, ...],
    target_length: int,
    line_tone: int,
    end_tone: int,
    allow_aojiu: bool,
    is_first_line: bool,
    rhyme_tone: int,
    require_cross_rescue: bool,
    first_line_rhymes: bool | None = None,
) -> bool:
    if end_tone != 2 and tones[-1] != ("平" if end_tone == 0 else "仄"):
        return False

    rhyme_value = "平" if rhyme_tone == 0 else "仄"
    is_rhyming = end_tone == rhyme_tone or (
        is_first_line
        and (
            first_line_rhymes
            if first_line_rhymes is not None
            else tones[-1] == rhyme_value
        )
    )
    key_position = target_length - 2
    rescue_position = key_position - 1

    for position in (1, 3, 5):
        if position >= target_length:
            continue
        expected = _expected_tone_at(position, target_length, line_tone)
        if tones[position] == expected:
            continue
        if not allow_aojiu or rhyme_tone != 0 or is_rhyming or position != key_position:
            return False

        # 出句只允许倒数第二个关键偶数位的两种定式：
        # 该平而仄时由前一奇数位本句救或留待对句救；该仄而平时必须构成特拗交换。
        if expected == "平" and tones[position] == "仄":
            continue
        if (
            expected == "仄"
            and tones[position] == "平"
            and tones[rescue_position] == "仄"
            and tones[rescue_position - 2] == "平"
        ):
            continue
        return False

    if require_cross_rescue and tones[rescue_position] != "平":
        return False

    if is_rhyming:
        if tones[-3:] == (rhyme_value, rhyme_value, rhyme_value):
            return False
        if rhyme_tone == 0:
            non_rhyme_ping_positions = [
                position
                for position, tone in enumerate(tones[:-1])
                if tone == "平"
            ]
            if line_tone == 0 and non_rhyme_ping_positions == [1]:
                return False
            if (
                line_tone == 1
                and target_length == 7
                and non_rhyme_ping_positions == [3]
            ):
                return False
    return True


@lru_cache(maxsize=256)
def _tang_suffix_masks(target_length, line_tone, end_tone, allow_aojiu,
                       is_first_line, rhyme_tone, require_cross_rescue):
    """For each concrete prefix, bits identify the SAME possible suffixes.

    Intersecting these masks preserves exists-suffix/forall-readings semantics.
    The first-line rhyme decision depends on the original options, so retain
    separate tables for None/False/True rather than inferring it per reading.
    """
    tables = {}
    for first_rhymes in (None, False, True):
        levels = [dict() for _ in range(target_length + 1)]
        for tones in product(("平", "仄"), repeat=target_length):
            if not _tang_tones_are_valid(
                tones, target_length, _resolve_tang_line_tone(tones, line_tone),
                end_tone, allow_aojiu, is_first_line, rhyme_tone,
                require_cross_rescue, first_rhymes,
            ):
                continue
            for length, level in enumerate(levels):
                suffix_index = 0
                for tone in tones[length:]:
                    suffix_index = suffix_index * 2 + (tone == "仄")
                prefix = tones[:length]
                level[prefix] = level.get(prefix, 0) | (1 << suffix_index)
        tables[first_rhymes] = levels
    return tables


@lru_cache(maxsize=8192)
def _has_viable_tang_tones(tone_options, target_length, line_tone, end_tone,
                          allow_aojiu, strict_polyphonic, is_first_line,
                          rhyme_tone, require_cross_rescue):
    if target_length not in (5, 7):
        return _has_viable_tang_tones_recursive(
            tone_options, target_length, line_tone, end_tone, allow_aojiu,
            strict_polyphonic, is_first_line, rhyme_tone, require_cross_rescue,
        )
    if len(tone_options) > target_length:
        return False
    first_rhymes = (
        _first_line_rhymes(tone_options, rhyme_tone, strict_polyphonic)
        if is_first_line and len(tone_options) == target_length else None
    )
    level = _tang_suffix_masks(
        target_length, line_tone, end_tone, allow_aojiu,
        is_first_line, rhyme_tone, require_cross_rescue,
    )[first_rhymes][len(tone_options)]
    # all([]) in the original recursive helper is True in strict mode.
    mask = (1 << (1 << (target_length - len(tone_options)))) - 1 if strict_polyphonic else 0
    for prefix in product(*tone_options):
        if strict_polyphonic:
            mask &= level.get(prefix, 0)
            if not mask:
                return False
        else:
            mask |= level.get(prefix, 0)
            if mask:
                return True
    return bool(mask)


@lru_cache(maxsize=8192)
def _has_viable_tang_tones_recursive(
    tone_options: tuple[tuple[str, ...], ...],
    target_length: int,
    line_tone: int,
    end_tone: int,
    allow_aojiu: bool,
    strict_polyphonic: bool,
    is_first_line: bool,
    rhyme_tone: int,
    require_cross_rescue: bool,
) -> bool:
    if len(tone_options) > target_length:
        return False

    first_line_rhymes = (
        _first_line_rhymes(tone_options, rhyme_tone, strict_polyphonic)
        if is_first_line and len(tone_options) == target_length
        else None
    )

    def full_line_is_valid(tones: tuple[str, ...]) -> bool:
        resolved_line_tone = _resolve_tang_line_tone(tones, line_tone)
        return _tang_tones_are_valid(
            tones,
            target_length,
            resolved_line_tone,
            end_tone,
            allow_aojiu,
            is_first_line,
            rhyme_tone,
            require_cross_rescue,
            first_line_rhymes,
        )

    prefixes = tuple(tuple(tones) for tones in product(*tone_options))

    @lru_cache(maxsize=None)
    def can_finish(suffix: tuple[str, ...]) -> bool:
        if len(tone_options) + len(suffix) == target_length:
            results = (
                full_line_is_valid(prefix + suffix)
                for prefix in prefixes
            )
            return all(results) if strict_polyphonic else any(results)
        return can_finish(suffix + ("平",)) or can_finish(suffix + ("仄",))

    return can_finish(())
