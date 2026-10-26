from __future__ import annotations

from transformers import StoppingCriteria


class GenerationCancelled(RuntimeError):
    code = "GENERATION_CANCELLED"


class CancellationCriteria(StoppingCriteria):
    def __init__(self, check):
        self.check = check

    def __call__(self, input_ids, scores, **kwargs):
        if self.check():
            raise GenerationCancelled("任务已由用户取消")
        return False


class ActivationMarkerDeadline(StoppingCriteria):
    """Stop an attempt when the model never enters its constrained phase."""

    def __init__(self, tokenizer, prompt_length: int, marker: str, max_tokens: int = 128):
        self._tokenizer = tokenizer
        self._prompt_length = prompt_length
        self._marker = marker
        self._max_tokens = max_tokens

    def __call__(self, input_ids, scores, **kwargs) -> bool:
        generated = input_ids[0][self._prompt_length :]
        if generated.shape[0] < self._max_tokens:
            return False
        text = self._tokenizer.decode(generated.tolist(), skip_special_tokens=True)
        return self._marker not in text


class TitleContentDeadline(StoppingCriteria):
    """Reject a malformed attempt shortly after its first title marker."""

    def __init__(self, tokenizer, prompt_length: int, max_tokens_after_title: int = 20):
        self._tokenizer = tokenizer
        self._prompt_length = prompt_length
        self._max_tokens_after_title = max_tokens_after_title
        self._title_end_tokens: int | None = None
        self._content_seen = False
        self.reason: str | None = None

    def __call__(self, input_ids, scores, **kwargs) -> bool:
        if self._content_seen:
            return False
        generated = input_ids[0][self._prompt_length:].tolist()
        text = self._tokenizer.decode(generated, skip_special_tokens=True)
        marker = "[title]"
        normalized = text.lower()
        first = normalized.find(marker)
        if first < 0:
            return False
        after_title = normalized[first + len(marker):]
        if "[content]" in after_title:
            self._content_seen = True
            return False
        if marker in after_title:
            self.reason = "标题前缀重复出现 [title]，已驳回本次生成"
            return True
        if self._title_end_tokens is None:
            # The marker can span several tokens; count from the token which
            # completes it, not from the beginning of the generated sequence.
            for count in range(1, len(generated) + 1):
                prefix = self._tokenizer.decode(generated[:count], skip_special_tokens=True)
                if marker in prefix.lower():
                    self._title_end_tokens = count
                    break
        if self._title_end_tokens is not None and len(generated) - self._title_end_tokens >= self._max_tokens_after_title:
            self.reason = f"生成 [title] 后 {self._max_tokens_after_title} 个 token 内未出现 [content]，已驳回本次生成"
            return True
        return False

