from __future__ import annotations

from dataclasses import dataclass

from ..domain import StepKind
from ..rewriting.parser import parse_poem
from ..state import GenerationController, GenerationStateMachine


@dataclass
class PartialGenerationPlan:
    runtime: object
    fixed_lines: dict[int, str]
    allow_context_constraint_conflicts: bool = False

    def create_controller(self, strict_polyphonic):
        session = self.runtime.profile.create_session(
            rhyme_mode=self.runtime.rhyme_mode, rhyme_parts=self.runtime.rhyme_parts,
        )
        if not self.allow_context_constraint_conflicts:
            session.prime_fixed_lines(self.fixed_lines, strict_polyphonic=strict_polyphonic)
        return GenerationController(GenerationStateMachine(self.runtime.profile.layout), session)

    def take_fixed_block(self, processor):
        controller = processor.controller
        pieces = []
        while not controller.snapshot().is_finished:
            state = controller.snapshot()
            if state.line_index not in self.fixed_lines:
                break
            fixed = self.fixed_lines[state.line_index]
            if state.current_line_text != fixed[:state.char_index]:
                raise ValueError("模型跨越正文边界并修改了固定句")
            if state.step is StepKind.TEXT:
                end = state.char_index + controller.remaining_before_boundary()
                text = fixed[state.char_index:end]
            elif state.step is StepKind.CAESURA:
                text = "、"
            else:
                allowed = self.runtime.separator_policy.allowed_tokens(state, controller.candidate_context())
                choices = [processor._tokenizer.decode([token], skip_special_tokens=True) for token in allowed]
                # Prefer canonical punctuation, then a standalone newline.
                preferred = (("\n", "。\n", "，\n") if state.step is StepKind.NEWLINE and not state.terminal_punctuation_pending
                             else ("。", "，", "。\n", "，\n", "？", "！", "\n"))
                text = next((value for value in preferred if value in choices), None)
                if text is None:
                    raise ValueError("固定句没有合法的句末分隔符")
            if not text:
                raise ValueError("固定段状态未能推进")
            controller.advance(text)
            if controller.snapshot() == state:
                raise ValueError("固定段状态未能推进")
            pieces.append(text)
        return "".join(pieces)

    def validate(self, content, strict_polyphonic=False):
        poem = parse_poem(content)
        layout = self.runtime.profile.layout
        if len(poem.lines) != len(layout.lines):
            raise ValueError("部分生成句数与诗体不一致")
        for index, line in enumerate(poem.lines):
            if len(line.text) != layout.lines[index].length:
                raise ValueError(f"第 {index + 1} 句字数不正确")
            if index in self.fixed_lines and line.text != self.fixed_lines[index]:
                raise ValueError(f"第 {index + 1} 句固定正文被修改")
        session = self.runtime.profile.create_session(
            rhyme_mode=self.runtime.rhyme_mode, rhyme_parts=self.runtime.rhyme_parts,
        )
        if not self.allow_context_constraint_conflicts:
            session.prime_fixed_lines({i: line.text for i, line in enumerate(poem.lines)},
                                     strict_polyphonic=strict_polyphonic)
