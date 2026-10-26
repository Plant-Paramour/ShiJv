from __future__ import annotations

from dataclasses import dataclass, replace

from ..constraints import ContextConstraintError
from ..contracts import RewritePoemRequest
from ..domain import CONTENT_MARKER
from ..policies import (
    PolicyTier,
    RepetitionPenaltyPolicy,
    RewriteRepetitionPenaltyPolicy,
    TangVerifierPolicy,
)
from ..state import GenerationController, GenerationStateMachine
from ..tasks import TaskRuntime
from .parser import ParsedPoem, PoemParseError, parse_poem
from .session import RewriteController


@dataclass(frozen=True)
class RewritePlan:
    request: RewritePoemRequest
    poem: ParsedPoem
    runtime: TaskRuntime
    target_indices: tuple[int, ...]
    fixed_lines: dict[int, str]

    @classmethod
    def build(
        cls,
        request: RewritePoemRequest,
        runtime: TaskRuntime,
    ) -> "RewritePlan":
        poem = parse_poem(request.original_text)
        numbers = request.target_line_numbers
        if numbers[-1] - numbers[0] + 1 != len(numbers):
            raise ContextConstraintError("rewrite 接口只能连续指定待改写句子")
        layout = runtime.profile.layout
        if len(poem.lines) != len(layout.lines):
            raise ContextConstraintError(
                f"原诗解析为 {len(poem.lines)} 句，但 {request.form_name} 要求 {len(layout.lines)} 句"
            )
        if request.target_line_numbers[-1] > len(poem.lines):
            raise PoemParseError(
                f"目标句 {request.target_line_numbers[-1]} 超出原诗的 {len(poem.lines)} 句"
            )
        target_indices = tuple(number - 1 for number in request.target_line_numbers)
        fixed_lines = {
            index: line.text
            for index, line in enumerate(poem.lines)
            if index not in target_indices
        }
        session = runtime.profile.create_session(
            rhyme_mode=runtime.rhyme_mode,
            rhyme_parts=runtime.rhyme_parts,
        )
        if not request.task_options.get("allow_context_constraint_conflicts"):
            session.prime_fixed_lines(
                fixed_lines,
                strict_polyphonic=request.strict_polyphonic,
            )
        return cls(
            request=request,
            poem=poem,
            runtime=runtime,
            target_indices=target_indices,
            fixed_lines=fixed_lines,
        )

    def create_controller(self) -> RewriteController:
        session = self.runtime.profile.create_session(
            rhyme_mode=self.runtime.rhyme_mode,
            rhyme_parts=self.runtime.rhyme_parts,
        )
        if not self.request.task_options.get("allow_context_constraint_conflicts"):
            session.prime_fixed_lines(
                self.fixed_lines,
                strict_polyphonic=self.request.strict_polyphonic,
            )
        base = GenerationController(
            GenerationStateMachine(self.runtime.profile.layout),
            session,
        )
        for line in self.poem.lines[:self.target_indices[0]]:
            base.advance(line.source_text + line.separator)
        return RewriteController(base, self.fixed_lines, self.target_indices)

    def runtime_for_rewrite(self) -> TaskRuntime:
        """Apply a poem-wide repeat penalty only while decoding replacement lines."""
        lengths = [line.length for line in self.runtime.profile.layout.lines]
        first_future = max(self.target_indices) + 1
        future_positions: dict[str, list[int]] = {}
        position = sum(lengths[:first_future])
        for index in range(first_future, len(lengths)):
            for offset, char in enumerate(self.fixed_lines[index]):
                future_positions.setdefault(char, []).append(position + offset)
            position += lengths[index]
        immutable_positions = {
            char: tuple(indices) for char, indices in future_positions.items()
        }
        fixed_penalty = 30.0 if self.request.meter_type in {"唐诗", "汉俳"} else None
        tiers = []
        for tier in self.runtime.policy_tiers:
            policies = tuple(
                policy.without_repetition_penalty()
                if isinstance(policy, TangVerifierPolicy)
                else policy
                for policy in tier.policies
                if not isinstance(policy, RepetitionPenaltyPolicy)
            )
            tiers.append(PolicyTier(tier.name, (
                *policies,
                RewriteRepetitionPenaltyPolicy(
                    immutable_positions, fixed_penalty=fixed_penalty,
                ),
            )))
        return replace(self.runtime, policy_tiers=tuple(tiers))

    def replacements_from_text(self, rewrite_text: str) -> dict[int, str]:
        transformed = self.runtime.process_output(CONTENT_MARKER + rewrite_text)
        _, marker, content = transformed.partition(CONTENT_MARKER)
        generated = parse_poem(content if marker else transformed)
        if len(generated.lines) != len(self.target_indices):
            raise PoemParseError(f"模型应只输出目标片段的 {len(self.target_indices)} 句，实际输出 {len(generated.lines)} 句")
        replacements: dict[int, str] = {}
        for offset, generated_line in enumerate(generated.lines):
            target_index = self.target_indices[0] + offset
            expected = self.runtime.profile.layout.lines[target_index].length
            if len(generated_line.text) != expected:
                raise PoemParseError(
                    f"第 {target_index + 1} 句应为 {expected} 字，实际为 {len(generated_line.text)} 字"
                )
            replacements[target_index + 1] = generated_line.source_text
        return replacements

    def validate_replacements(self, replacements: dict[int, str]) -> None:
        """Recheck sampled text independently of the logits processor."""
        from .parser import parse_poem

        lines = dict(self.fixed_lines)
        lines.update({number - 1: parse_poem(text).lines[0].text
                      for number, text in replacements.items()})
        if self.request.task_options.get("allow_context_constraint_conflicts"):
            return
        session = self.runtime.profile.create_session(
            rhyme_mode=self.runtime.rhyme_mode, rhyme_parts=self.runtime.rhyme_parts,
        )
        session.prime_fixed_lines(lines, strict_polyphonic=self.request.strict_polyphonic)

    def apply(self, replacements: dict[int, str]) -> str:
        return self.poem.replace_lines(replacements)
