"""Exact two-character Tang policy evaluation over pre-encoded vocabulary data.

Only VocabIndex and the built-in Tang policy tiers use this path. Other
policies and tokens retain the ordinary per-token evaluation.
"""
from __future__ import annotations

import math
import numpy as np

from .candidates import decode_candidate
from .policies import BoundaryCoherencePolicy, TangVerifierPolicy
from .vocab import VocabIndex


def _pair_codes(text):
    return (ord(text[0]) << 16) | ord(text[1])


def _triple_codes(text):
    return (ord(text[0]) << 32) | (ord(text[1]) << 16) | ord(text[2])


class TangTwoCharIndex:
    def __init__(self, vocab: VocabIndex, tokenizer):
        size = max(vocab._token_to_text, default=-1) + 1
        self.two_char = np.zeros(size, dtype=np.bool_)
        self.valid = np.zeros(size, dtype=np.bool_)
        self.first = np.zeros(size, dtype=np.int32)
        self.second = np.zeros(size, dtype=np.int32)
        self.tones = np.zeros(size, dtype=np.uint8)
        self.internal_pair = np.zeros(size, dtype=np.int64)
        options = getattr(vocab._lexicon, 'pingze_options', vocab._lexicon.get_pingze)
        for token_id, text in vocab._token_to_text.items():
            if len(text) != 2:
                continue
            self.two_char[token_id] = True
            candidate = decode_candidate(token_id, tokenizer, vocab)
            if candidate is None or len(candidate.text) != 2:
                continue
            self.valid[token_id] = True
            first, second = map(ord, candidate.text)
            self.first[token_id], self.second[token_id] = first, second
            self.internal_pair[token_id] = (first << 16) | second
            def code(char):
                tones = options(char)
                return (1 if '平' in tones else 0) | (2 if '仄' in tones else 0)
            self.tones[token_id] = code(candidate.text[0]) * 4 + code(candidate.text[1])


def tier_batch(tier, token_ids, context):
    """Return (covered IDs, accepted penalties), or None for unsupported tiers."""
    vocab = context.vocab
    policies = tier.policies
    if type(vocab) is not VocabIndex or not policies or type(policies[0]) is not TangVerifierPolicy:
        return None
    policy = policies[0]
    if policy._lexicon is not vocab._lexicon:
        return None
    boundary = None
    if len(policies) == 2 and type(policies[1]) is BoundaryCoherencePolicy:
        boundary = policies[1]
        if (type(boundary._common_bigrams) is not frozenset
                or not isinstance(boundary._penalty, (int, float))
                or not math.isfinite(boundary._penalty)):
            return None
    elif len(policies) != 1:
        return None
    from .constraints import RelationalConstraintContext
    if not isinstance(context.constraint, RelationalConstraintContext) or context.state.line is None:
        return None
    # The fast representation uses 16 bits per character. Fixed text may be
    # supplied by callers, so leave unusual Unicode and inconsistent synthetic
    # states to the original implementation.
    if (len(context.state.current_line_text) != context.state.char_index
            or any(ord(char) > 0xffff for char in context.state.all_text)):
        return None
    index = getattr(vocab, '_tang_two_char_index', None)
    if index is None:
        index = TangTwoCharIndex(vocab, context.tokenizer)
        vocab._tang_two_char_index = index
    ids = np.asarray(token_ids, dtype=np.int64)
    if len(ids) and (ids[0] < 0 or ids[-1] >= len(index.two_char)):
        return None
    two = ids[index.two_char[ids]]
    if not len(two):
        return index.two_char, {}
    state, info = context.state, context.constraint
    good = index.valid[two].copy()
    # All two-character candidates cross a position boundary together.
    if any(state.char_index < point < state.char_index + 2 for point in
           state.line.break_positions | state.line.caesura_positions | {state.line.length}):
        return index.two_char, {}
    first, second = index.first[two], index.second[two]
    if policy._mode == 'full' and policy._enforce_style:
        forbidden = np.asarray([ord(c) for c in '的些么了'], dtype=np.int32)
        good &= ~np.isin(first, forbidden) & ~np.isin(second, forbidden)
    if not good.any():
        return index.two_char, {}

    # Completion is invariant for the same two reading-option sets. Literal
    # text, rhyme, repetition and boundary checks remain separate below.
    signatures = index.tones[two]
    for code in np.unique(signatures[good]):
        representative_id = int(two[np.flatnonzero(good & (signatures == code))[0]])
        candidate = decode_candidate(representative_id, context.tokenizer, vocab)
        simulated = state.current_line_text + candidate.text
        if len(simulated) > info.target_length or not policy._has_viable_completion(simulated, state, info, context):
            good &= signatures != code
    if not good.any():
        return index.two_char, {}

    if state.char_index + 2 == info.target_length:
        valid_last = np.zeros(65536, dtype=np.bool_)
        for code in np.unique(second[good]):
            char = chr(int(code))
            valid_last[code] = (
                (policy._mode != 'full' or not policy._enforce_style or
                 policy._check_end_character(char, state, info.target_length))
                and policy._check_rhyme(char, info)
            )
        good &= valid_last[second]
    if not good.any():
        return index.two_char, {}
    penalties = np.zeros(len(two), dtype=np.float64)
    if policy._mode == 'full' and policy._enforce_style:
        if policy._repetition_penalty_enabled:
            counts = policy._step(state, info, context)[2]
            count_table = np.zeros(65536, dtype=np.int32)
            line_table = np.zeros(65536, dtype=np.bool_)
            for char, count in counts.items():
                count_table[ord(char)] = count
            for char in state.current_line_text:
                line_table[ord(char)] = True
            first_count, second_count = count_table[first], count_table[second]
            good &= (first_count < 4) & (second_count < 4)
            penalties += line_table[first] * 30.0
            penalties += np.where(first_count >= 2, first_count * 15.0,
                                  np.where(first_count == 1, 5.0, 0.0))
            penalties += line_table[second] * 30.0
            penalties += np.where(second_count >= 2, second_count * 15.0,
                                  np.where(second_count == 1, 5.0, 0.0))
        if good.any():
            grams = policy._step(state, info, context)[3]
            def seen(values, keys):
                return np.isin(values, np.fromiter(keys, dtype=np.int64))
            good &= ~seen(index.internal_pair[two], (_pair_codes(x) for x in grams[2][0]))
            if state.current_line_text:
                last = ord(state.current_line_text[-1])
                good &= ~seen((last << 16) | first, (_pair_codes(x) for x in grams[2][1]))
                good &= ~seen((last << 32) | (first.astype(np.int64) << 16) | second,
                              (_triple_codes(x) for x in grams[3][1]))
            if len(state.current_line_text) >= 2:
                prefix = (ord(state.current_line_text[-2]) << 32) | (ord(state.current_line_text[-1]) << 16)
                good &= ~seen(prefix | first, (_triple_codes(x) for x in grams[3][1]))
    if boundary is not None and state.line is not None and state.char_index in state.line.break_positions and state.current_line_text:
        prefix = ord(state.current_line_text[-1]) << 16
        codes = getattr(boundary, '_batch_pair_codes', None)
        if codes is None or getattr(boundary, '_batch_bigrams_source', None) is not boundary._common_bigrams:
            shared_bigrams = getattr(vocab, '_common_bigrams', None)
            if boundary._common_bigrams is shared_bigrams:
                codes = getattr(vocab, '_tang_common_bigram_codes', None)
            if codes is None:
                codes = np.fromiter(
                    (_pair_codes(x) for x in boundary._common_bigrams
                     if len(x) == 2 and all(ord(char) <= 0xffff for char in x)),
                    dtype=np.int64,
                )
                if boundary._common_bigrams is shared_bigrams:
                    vocab._tang_common_bigram_codes = codes
            boundary._batch_pair_codes = codes
            boundary._batch_bigrams_source = boundary._common_bigrams
        penalty_mask = np.isin(prefix | first, codes)
        penalties += penalty_mask * boundary._penalty
    accepted = {int(token_id): float(penalty) for token_id, penalty, valid in zip(two, penalties, good) if valid}
    return index.two_char, accepted
