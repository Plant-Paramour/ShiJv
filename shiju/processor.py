from __future__ import annotations

from dataclasses import dataclass
from collections import OrderedDict
from typing import Protocol, Sequence

try:
    import torch
except ImportError:  # PyTorch is an optional dependency for local model inference.
    torch = None

try:
    from transformers import LogitsProcessor
except ImportError:  # The control plane can run without the local inference stack.
    class LogitsProcessor:  # type: ignore[no-redef]
        pass

from .domain import CONTENT_MARKER, GenerationState, StepKind
from .policies import CandidateContext, PolicyTier
from .state import GenerationController
from .tang_batch import tier_batch
from .vocab import TokenizerLike, VocabLookup


class SeparatorPolicy(Protocol):
    @property
    def newline_tokens(self) -> set[int]: ...

    def allowed_tokens(self, state: GenerationState, constraint_context) -> set[int]: ...


@dataclass(frozen=True)
class ProcessorConfig:
    relax_rhyme_on_empty: bool = False
    raw_on_no_candidates: bool = False
    raw_after_policy_failure: bool = False
    release_constraints_after_finish: bool = False
    separator_empty_returns_raw: bool = False
    strict_polyphonic: bool = True
    candidate_top_k: int | None = None


def _exact_token_ids(tokenizer: TokenizerLike, values: Sequence[str]) -> set[int]:
    wanted = set(values)
    result = set()
    for _, token_id in tokenizer.get_vocab().items():
        clean = tokenizer.decode([token_id]).replace(" ", "")
        if clean in wanted:
            result.add(token_id)
    for value in values:
        ids = tokenizer.encode(value, add_special_tokens=False)
        if ids:
            result.add(ids[0])
    return result


class TemplateSeparatorPolicy:
    def __init__(self, tokenizer: TokenizerLike, rhyming_lines: frozenset[int]):
        self._rhyming_lines = rhyming_lines
        # 宋词句末标点只由是否押韵决定：非押韵句收逗号，押韵句收终止标点。
        self._punct_non_rhyming = _exact_token_ids(tokenizer, ("，", "，\n"))
        self._newline_odd = _exact_token_ids(tokenizer, ("\n", "，\n"))
        self._newline_even = _exact_token_ids(tokenizer, ("\n",))
        self._caesura = _exact_token_ids(tokenizer, ("、",))
        self._terminal_punct = _exact_token_ids(tokenizer, ("。", "？", "！", "。\n"))
        self._terminal_newline = _exact_token_ids(tokenizer, ("\n", "。\n"))

    @property
    def newline_tokens(self) -> set[int]:
        return set(self._terminal_newline)

    def allowed_tokens(self, state: GenerationState, constraint_context) -> set[int]:
        if state.step is StepKind.CAESURA:
            return set(self._caesura)
        if state.line is None:
            return set()
        is_rhyming = state.line_index in self._rhyming_lines
        if state.step is StepKind.NEWLINE:
            if state.terminal_punctuation_pending:
                return set(self._terminal_punct)
            if is_rhyming:
                return set(self._terminal_newline)
            return set(self._newline_odd)
        if state.step is StepKind.PUNCTUATION:
            if state.line.stanza_end:
                return set(self._terminal_punct)
            if is_rhyming:
                return set(self._terminal_punct)
            return set(self._punct_non_rhyming)
        return set()


class RelationalSeparatorPolicy:
    def __init__(self, tokenizer: TokenizerLike):
        self._comma = self._encode_all(tokenizer, ("，", ","))
        self._period = self._encode_all(tokenizer, ("。", "."))
        self._question = self._encode_all(tokenizer, ("？", "?"))
        self._exclamation = self._encode_all(tokenizer, ("！", "!"))
        self._newline = self._encode_all(tokenizer, ("\n",))

    @staticmethod
    def _encode_all(tokenizer: TokenizerLike, values: Sequence[str]) -> set[int]:
        result = set()
        for value in values:
            result.update(tokenizer.encode(value, add_special_tokens=False))
        return result

    @property
    def newline_tokens(self) -> set[int]:
        return set(self._newline)

    def allowed_tokens(self, state: GenerationState, constraint_context) -> set[int]:
        if state.step is not StepKind.PUNCTUATION:
            return set()
        if (state.line_index + 1) % 2 == 0:
            return set(self._period | self._question | self._exclamation)
        return set(self._comma)


class NewlineSeparatorPolicy:
    """用于行间只换行、句内不生成标点的诗体。"""

    def __init__(self, tokenizer: TokenizerLike):
        self._newline = _exact_token_ids(tokenizer, ("\n",))

    @property
    def newline_tokens(self) -> set[int]:
        return set(self._newline)

    def allowed_tokens(self, state: GenerationState, constraint_context) -> set[int]:
        if state.step is StepKind.NEWLINE:
            return set(self._newline)
        return set()


class ConstrainedLogitsProcessor(LogitsProcessor):
    """诗体无关的约束解码管线。"""

    _CANDIDATE_INDEX_MAX_BYTES = 4 * 1024 * 1024
    _CANDIDATE_INDEX_MAX_ENTRIES = 8

    def __init__(
        self,
        vocab: VocabLookup,
        controller: GenerationController,
        tokenizer: TokenizerLike,
        input_prompt_len: int,
        separator_policy: SeparatorPolicy,
        policy_tiers: Sequence[PolicyTier],
        config: ProcessorConfig,
        activation_marker: str = CONTENT_MARKER,
        eos_ids=None,
    ):
        if not activation_marker:
            raise ValueError("约束启动标记不能为空")
        self._vocab = vocab
        self._controller = controller
        self._tokenizer = tokenizer
        self._input_prompt_len = input_prompt_len
        self._separator_policy = separator_policy
        self._policy_tiers = tuple(policy_tiers)
        self._config = config
        self._activation_marker = activation_marker
        self._last_decoded_text = ""
        self._has_started_content = False
        from .end_tokens import eos_token_ids
        self._eos_token_ids = set(eos_ids or eos_token_ids(tokenizer))
        self._completion_waiting = False
        self._constraints_released = False
        self.buffer_incomplete_tokens = False
        self._candidate_indices = OrderedDict()
        self._candidate_index_bytes = 0
        self._semicolon_token_ids = set(
            tokenizer.encode("；", add_special_tokens=False)
        )

    def __call__(
        self,
        input_ids: torch.LongTensor,
        scores: torch.FloatTensor,
    ) -> torch.FloatTensor:
        if torch is None:
            raise RuntimeError("PyTorch is required for local model inference")
        for token_id in self._semicolon_token_ids:
            if 0 <= token_id < scores.shape[1]:
                scores[:, token_id] = -float("inf")

        generated_ids = input_ids[0][self._input_prompt_len :].tolist()
        raw_text = self._tokenizer.decode(generated_ids, skip_special_tokens=True)
        raw_text = raw_text.replace(" ", "").replace("\r", "")
        if self.buffer_incomplete_tokens:
            raw_text = raw_text.rstrip("\ufffd")
        if not self._track_constrained_content(raw_text):
            return scores

        if self._constraints_released:
            return scores
        if self._completion_waiting:
            if generated_ids and "\n" in self._tokenizer.decode([generated_ids[-1]]):
                self._constraints_released = True
                return scores
            return self._completion_mask(scores)

        state = self._controller.snapshot()
        if state.is_finished:
            if self._config.release_constraints_after_finish:
                self._completion_waiting = True
                return self._completion_mask(scores)
            return self._only_tokens(scores, self._eos_token_ids)

        forced_prefix = getattr(self._controller, "forced_prefix", lambda: None)()
        if forced_prefix:
            allowed = self._forced_token_ids(forced_prefix, scores.shape[1])
            if not allowed:
                raise RuntimeError(
                    f"分词器无法注入固定文本前缀: {forced_prefix[:12]}"
                )
            return self._only_tokens(scores, allowed)

        if state.step is not StepKind.TEXT:
            allowed = self._separator_policy.allowed_tokens(
                state,
                self._controller.candidate_context(),
            )
            if not allowed and self._config.separator_empty_returns_raw:
                return scores
            if not allowed:
                allowed = self._eos_token_ids
            return self._only_tokens(scores, allowed)

        patterns = self._controller.allowed_patterns()
        vocab_size = scores.shape[1]
        allowed_ids = self._resolve_segment_candidates(patterns, vocab_size)
        if not allowed_ids and self._config.relax_rhyme_on_empty:
            allowed_ids = self._resolve_segment_candidates(patterns, vocab_size, ignore_rhyme=True)
        if not allowed_ids:
            if self._config.raw_on_no_candidates:
                result = scores.clone()
                result[:, sorted(i for i in self._eos_token_ids if i < scores.shape[1])] = -float("inf")
                return result
            return self._only_tokens(scores, self._eos_token_ids)

        state = self._controller.snapshot()
        candidate_context = CandidateContext(
            state=state,
            constraint=self._controller.candidate_context(),
            tokenizer=self._tokenizer,
            vocab=self._vocab,
            cache={},
        )
        if self._config.candidate_top_k is not None:
            limit = max(1, min(self._config.candidate_top_k, len(allowed_ids)))
            ranked = torch.topk(
                scores[0, self._indices_for_candidates(allowed_ids, scores.device)],
                k=limit, largest=True
            ).indices
            # A single device-to-host transfer instead of one per scalar ID.
            candidate_ids = tuple(allowed_ids[index] for index in ranked.tolist())
        else:
            candidate_ids = tuple(allowed_ids)
        sorted_ids = sorted(candidate_ids)
        for tier in self._policy_tiers or (PolicyTier("base", ()),):
            result = torch.full_like(scores, -float("inf"))
            accepted_ids = []
            penalties = []
            batched = tier_batch(tier, sorted_ids, candidate_context)
            for token_id in sorted_ids:
                if batched is not None and batched[0][token_id]:
                    penalty = batched[1].get(token_id)
                else:
                    penalty = tier.evaluate(token_id, candidate_context)
                if penalty is None:
                    continue
                accepted_ids.append(token_id)
                penalties.append(penalty)
            if accepted_ids:
                indices = torch.tensor(accepted_ids, device=scores.device, dtype=torch.long)
                offsets = scores.new_tensor(penalties)
                result[0, indices] = scores[0, indices] - offsets
                return result

        if self._config.raw_after_policy_failure:
            return scores
        return self._only_tokens(scores, self._eos_token_ids)

    def _resolve_segment_candidates(self, patterns, vocab_size, ignore_rhyme=False):
        resolver = getattr(self._vocab, "resolve_segment_candidates", None)
        if resolver is not None:
            return resolver(patterns, max_characters=self._controller.remaining_before_boundary(),
                            vocab_size=vocab_size, strict_polyphonic=self._config.strict_polyphonic,
                            ignore_rhyme=ignore_rhyme)
        allowed = self._vocab.resolve_patterns(patterns, ignore_rhyme=ignore_rhyme,
                                               strict_polyphonic=self._config.strict_polyphonic)
        return tuple(self._limit_to_current_segment(allowed, vocab_size))

    def _indices_for_candidates(self, candidates, device):
        # Hold the source tuple with the tensor, so its identity cannot be
        # recycled while this bounded, request-local device cache references it.
        key = (id(candidates), device)
        cached = self._candidate_indices.get(key)
        if cached is not None:
            self._candidate_indices.move_to_end(key)
            return cached[1]
        indices = torch.tensor(candidates, device=device, dtype=torch.long)
        size = indices.numel() * indices.element_size()
        if size <= self._CANDIDATE_INDEX_MAX_BYTES:
            while self._candidate_indices and (
                len(self._candidate_indices) >= self._CANDIDATE_INDEX_MAX_ENTRIES
                or self._candidate_index_bytes + size > self._CANDIDATE_INDEX_MAX_BYTES
            ):
                _, (_, removed) = self._candidate_indices.popitem(last=False)
                self._candidate_index_bytes -= removed.numel() * removed.element_size()
            self._candidate_indices[key] = (candidates, indices)
            self._candidate_index_bytes += size
        return indices

    def _limit_to_current_segment(
        self,
        token_ids: set[int],
        vocab_size: int,
    ) -> set[int]:
        max_characters = self._controller.remaining_before_boundary()
        if max_characters <= 0:
            return set()
        return {
            token_id
            for token_id in token_ids
            if 0 <= token_id < vocab_size
            and 0 < len(self._vocab.text_for_token(token_id)) <= max_characters
        }

    @property
    def constraints_started(self) -> bool:
        return self._has_started_content

    @property
    def controller(self):
        return self._controller

    def sync_output(self, text: str) -> None:
        self._track_constrained_content(text.replace(" ", "").replace("\r", ""))

    def acknowledge_output(self, text: str) -> None:
        self._last_decoded_text = text.split(self._activation_marker, 1)[1].replace(" ", "").replace("\r", "")

    def _track_constrained_content(self, raw_text: str) -> bool:
        if not self._has_started_content:
            if self._activation_marker not in raw_text:
                return False
            self._has_started_content = True
            content = raw_text.split(self._activation_marker, 1)[1]
            self._controller.advance(content)
            self._last_decoded_text = content
            return True
        content = (
            raw_text.split(self._activation_marker, 1)[1]
            if self._activation_marker in raw_text
            else raw_text
        )
        if len(content) > len(self._last_decoded_text):
            latest = content[len(self._last_decoded_text) :]
            self._controller.advance(latest)
            self._last_decoded_text = content
        return True

    def _completion_mask(self, scores: torch.FloatTensor) -> torch.FloatTensor:
        return self._only_tokens(
            scores,
            self._separator_policy.newline_tokens | self._eos_token_ids,
        )

    def _forced_token_ids(self, prefix: str, vocab_size: int) -> set[int]:
        """Find tokens whose decoded text is an exact non-empty prefix."""
        allowed: set[int] = set()
        for end in range(1, len(prefix) + 1):
            ids = self._tokenizer.encode(prefix[:end], add_special_tokens=False)
            if len(ids) != 1:
                continue
            token_id = int(ids[0])
            decoded = self._tokenizer.decode([token_id], skip_special_tokens=True)
            decoded = decoded.replace(" ", "").replace("\r", "")
            if 0 <= token_id < vocab_size and decoded and prefix.startswith(decoded):
                allowed.add(token_id)
        if allowed:
            return allowed
        ids = self._tokenizer.encode(prefix, add_special_tokens=False)
        if ids:
            token_id = int(ids[0])
            decoded = self._tokenizer.decode([token_id], skip_special_tokens=True)
            decoded = decoded.replace(" ", "").replace("\r", "")
            if 0 <= token_id < vocab_size and decoded and prefix.startswith(decoded):
                allowed.add(token_id)
        return allowed

    @staticmethod
    def _only_tokens(scores: torch.FloatTensor, token_ids: set[int]) -> torch.FloatTensor:
        result = torch.full_like(scores, -float("inf"))
        for token_id in token_ids:
            if 0 <= token_id < scores.shape[1]:
                result[:, token_id] = scores[:, token_id]
        return result
