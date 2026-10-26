from __future__ import annotations

import csv
import re
from collections import OrderedDict
import sys
from pathlib import Path
from typing import Dict, Iterable, Protocol, Set, Tuple

from tqdm import tqdm

from .data import RhymeLookup
from .domain import AllowedPattern, RhymeMode


class TokenizerLike(Protocol):
    eos_token_id: int

    def get_vocab(self) -> dict[str, int]: ...

    def decode(self, token_ids, **kwargs) -> str: ...

    def encode(self, text: str, **kwargs) -> list[int]: ...


class VocabLookup(Protocol):
    def text_for_token(self, token_id: int) -> str: ...

    def common_bigrams(self) -> frozenset[str]: ...

    def resolve_patterns(
        self,
        patterns: Iterable[AllowedPattern],
        ignore_rhyme: bool = False,
        strict_polyphonic: bool = True,
    ) -> Set[int]: ...


class VocabIndex:
    """封装平仄与韵部 token 索引，不向上层暴露内部存储。"""

    _VALID_TEXT = re.compile(r"^[\u4e00-\u9fa5A-Za-z]+$")
    _SEGMENT_CACHE_MAX_BYTES = 8 * 1024 * 1024
    _SEGMENT_CACHE_MAX_ENTRIES = 256

    def __init__(
        self,
        tokenizer: TokenizerLike,
        rhyme_lexicon: RhymeLookup,
        classical_lexicon_path: str | Path | None = None,
    ):
        self._tokenizer = tokenizer
        self._lexicon = rhyme_lexicon
        self._token_to_text: Dict[int, str] = {}
        self._token_to_raw_text: Dict[int, str] = {}
        self._decoded_candidate_cache: dict[int, object] = {}
        self._token_patterns: Dict[int, frozenset[str]] = {}
        self._pattern_tokens: Dict[Tuple[int, str], Set[int]] = {}
        self._rhyme_tokens: Dict[Tuple[str, str], Set[int]] = {}
        # Immutable, ordered results of the original resolver + segment filter.
        # Bound both entry count and tuple storage; vocabulary owns all int IDs.
        self._segment_candidates = OrderedDict()
        self._segment_cache_bytes = 0
        self._segment_cache_hits = 0
        self._segment_cache_misses = 0
        self._common_bigrams = self._load_common_bigrams(classical_lexicon_path)
        self._build_index()

    @staticmethod
    def _load_common_bigrams(path: str | Path | None) -> frozenset[str]:
        source = Path(path) if path is not None else Path(__file__).with_name("ClassicalLexicon.csv")
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            return frozenset(
                row["word"].strip()
                for row in reader
                if row.get("word", "").strip() and row.get("length", "").strip() == "2"
            )

    def _build_index(self) -> None:
        print("Building offline vocab index (Constraint rules)...")
        for _, token_id in tqdm(self._tokenizer.get_vocab().items()):
            raw_text = self._tokenizer.decode([token_id])
            clean_text = raw_text.replace(" ", "")
            if not clean_text or not self._VALID_TEXT.fullmatch(clean_text):
                continue
            self._token_to_text[token_id] = clean_text
            self._token_to_raw_text[token_id] = raw_text
            patterns = self._all_pingze_patterns(clean_text)
            if not patterns:
                continue
            self._token_patterns[token_id] = frozenset(patterns)
            for pattern in patterns:
                self._pattern_tokens.setdefault((len(clean_text), pattern), set()).add(token_id)
            last_char = clean_text[-1]
            for tone in self._lexicon.get_pingze(last_char):
                for part in self._lexicon.get_rhyme_part_by_tone(last_char, tone):
                    self._rhyme_tokens.setdefault((tone, part), set()).add(token_id)
        print(f"Indexed {len(self._token_to_text)} purely Chinese constraint tokens.")

    def _all_pingze_patterns(self, text: str) -> Set[str]:
        patterns = {""}
        for char in text:
            tones = self._lexicon.get_pingze(char)
            if not tones:
                return set()
            patterns = {base + tone for base in patterns for tone in tones}
        return patterns

    def text_for_token(self, token_id: int) -> str:
        return self._token_to_text.get(token_id, "")

    def raw_text_for_token(self, token_id: int) -> str:
        return self._token_to_raw_text.get(token_id, "")

    def common_bigrams(self) -> frozenset[str]:
        return self._common_bigrams

    def resolve_segment_candidates(self, patterns, *, max_characters: int,
                                   vocab_size: int, strict_polyphonic: bool,
                                   ignore_rhyme: bool = False) -> tuple[int, ...]:
        patterns = tuple(patterns)
        # Equal frozensets can have different iteration orders. The legacy
        # resolver iterates rhyme.parts, so retain that observable order too.
        part_order = tuple(tuple(item.rhyme.parts) for item in patterns)
        key = (patterns, max_characters, vocab_size, strict_polyphonic, ignore_rhyme, part_order)
        cached = self._segment_candidates.get(key)
        if cached is not None:
            self._segment_candidates.move_to_end(key)
            self._segment_cache_hits += 1
            return cached
        self._segment_cache_misses += 1
        allowed = self.resolve_patterns(key[0], ignore_rhyme=ignore_rhyme,
                                        strict_polyphonic=strict_polyphonic)
        # Keep precisely the original set construction and iteration order:
        # topk tie handling depends on the input candidate order.
        limited = {
            token_id for token_id in allowed
            if 0 <= token_id < vocab_size
            and 0 < len(self.text_for_token(token_id)) <= max_characters
        } if max_characters > 0 else set()
        result = tuple(limited)
        size = sys.getsizeof(result)
        if size <= self._SEGMENT_CACHE_MAX_BYTES:
            while self._segment_candidates and (
                len(self._segment_candidates) >= self._SEGMENT_CACHE_MAX_ENTRIES
                or self._segment_cache_bytes + size > self._SEGMENT_CACHE_MAX_BYTES
            ):
                _, removed = self._segment_candidates.popitem(last=False)
                self._segment_cache_bytes -= sys.getsizeof(removed)
            self._segment_candidates[key] = result
            self._segment_cache_bytes += size
        return result

    def resolve_patterns(
        self,
        patterns: Iterable[AllowedPattern],
        ignore_rhyme: bool = False,
        strict_polyphonic: bool = True,
    ) -> Set[int]:
        patterns = tuple(patterns)
        allowed: Set[int] = set()
        for item in patterns:
            base = set(self._pattern_tokens.get((item.length, item.tones), ()))
            if item.required_text is not None:
                base.intersection_update(token_id for token_id in base if self._token_to_text.get(token_id) == item.required_text)
            rhyme = item.rhyme
            if not ignore_rhyme and rhyme.mode is not RhymeMode.NONE:
                rhyme_tokens: Set[int] = set()
                if rhyme.mode is RhymeMode.ANY:
                    for (tone, part), token_ids in self._rhyme_tokens.items():
                        if tone == rhyme.tone and part not in rhyme.excluded_parts:
                            rhyme_tokens.update(token_ids)
                else:
                    for part in rhyme.parts:
                        rhyme_tokens.update(self._rhyme_tokens.get((rhyme.tone or "", part), ()))
                base.intersection_update(rhyme_tokens)
            allowed.update(base)
        if strict_polyphonic:
            allowed = {
                token_id
                for token_id in allowed
                if all(
                    any(
                        item.length == len(self._token_to_text[token_id])
                        and item.tones == token_pattern
                        and (item.required_text is None or self._token_to_text[token_id] == item.required_text)
                        and self._matches_rhyme(
                            token_id,
                            item,
                            ignore_rhyme,
                            strict_polyphonic=True,
                        )
                        for item in patterns
                    )
                    for token_pattern in self._token_patterns[token_id]
                )
            }
        return allowed

    def _matches_rhyme(
        self,
        token_id: int,
        pattern: AllowedPattern,
        ignore_rhyme: bool,
        strict_polyphonic: bool = False,
    ) -> bool:
        rhyme = pattern.rhyme
        if ignore_rhyme or rhyme.mode is RhymeMode.NONE:
            return True
        last_char = self._token_to_text[token_id][-1]
        parts = set(
            self._lexicon.get_rhyme_part_by_tone(last_char, rhyme.tone or "")
        )
        if rhyme.mode is RhymeMode.ANY:
            if strict_polyphonic:
                return bool(parts) and not parts.intersection(rhyme.excluded_parts)
            return bool(parts.difference(rhyme.excluded_parts))
        if strict_polyphonic:
            return bool(parts) and parts.issubset(rhyme.parts)
        return bool(parts.intersection(rhyme.parts))
