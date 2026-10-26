from __future__ import annotations

import re
from typing import Any, Protocol, Sequence

from .domain import AllowedPattern, GenerationLayout, GenerationState, StepKind


class ConstraintSession(Protocol):
    def allowed_patterns(self, state: GenerationState, max_length: int) -> Sequence[AllowedPattern]: ...

    def observe_text(self, state: GenerationState, text: str) -> None: ...

    def candidate_context(self, state: GenerationState) -> Any: ...


class GenerationStateMachine:
    """诗体无关的正文位置状态机。"""

    _TEXT_CHAR = re.compile(r"[\u4e00-\u9fa5A-Za-z]")
    _PUNCTUATION = re.compile(r"[，,。.？！；;!?]")

    def __init__(self, layout: GenerationLayout):
        self._layout = layout
        self._line_index = 0
        self._char_index = 0
        self._current_line_text = ""
        self._all_text = ""
        self._step = StepKind.TEXT
        self._terminal_punctuation_pending = False

    @property
    def layout(self) -> GenerationLayout:
        return self._layout

    def snapshot(self) -> GenerationState:
        line = None if self._step is StepKind.FINISHED else self._layout.lines[self._line_index]
        return GenerationState(
            line_index=self._line_index,
            char_index=self._char_index,
            current_line_text=self._current_line_text,
            all_text=self._all_text,
            step=self._step,
            line=line,
            terminal_punctuation_pending=self._terminal_punctuation_pending,
        )

    def consume(self, char: str) -> bool:
        if self._step is StepKind.FINISHED:
            return False
        if self._step is StepKind.TEXT:
            if not self._TEXT_CHAR.fullmatch(char):
                return False
            self._current_line_text += char
            self._all_text += char
            self._char_index += 1
            self._refresh_text_step()
            return True
        if self._step is StepKind.CAESURA:
            if char != "、":
                return False
            self._step = StepKind.TEXT
            return True
        if self._step is StepKind.PUNCTUATION:
            if not self._PUNCTUATION.fullmatch(char):
                return False
            if self._layout.lines[self._line_index].stanza_end:
                self._step = StepKind.NEWLINE
            else:
                self._move_to_next_line()
            return True
        if self._step is StepKind.NEWLINE:
            if self._terminal_punctuation_pending:
                if self._PUNCTUATION.fullmatch(char):
                    self._terminal_punctuation_pending = False
                    return True
                if char != "\n":
                    return False
                # 兼容没有终止标点的旧输出，同时允许约束解码优先生成标点。
                self._terminal_punctuation_pending = False
                self._move_to_next_line()
                return True
            if char != "\n":
                return False
            self._move_to_next_line()
            return True
        return False

    def _refresh_text_step(self) -> None:
        line = self._layout.lines[self._line_index]
        if self._char_index in line.caesura_positions:
            self._step = StepKind.CAESURA
        elif self._char_index >= line.length:
            self._step = StepKind.NEWLINE if line.stanza_end else StepKind.PUNCTUATION
            self._terminal_punctuation_pending = line.stanza_end

    def _move_to_next_line(self) -> None:
        self._line_index += 1
        self._char_index = 0
        self._current_line_text = ""
        self._terminal_punctuation_pending = False
        if self._line_index >= len(self._layout.lines):
            self._step = StepKind.FINISHED
            return
        self._step = StepKind.TEXT

    def remaining_before_boundary(self) -> int:
        state = self.snapshot()
        if state.line is None or state.step is not StepKind.TEXT:
            return 0
        boundaries = {
            position
            for position in state.line.break_positions | state.line.caesura_positions
            if position > state.char_index
        }
        next_boundary = min(boundaries, default=state.line.length)
        return next_boundary - state.char_index


class GenerationController:
    """组合通用状态机与诗体约束会话。"""

    def __init__(self, state_machine: GenerationStateMachine, session: ConstraintSession):
        self._state_machine = state_machine
        self._session = session

    @property
    def layout(self) -> GenerationLayout:
        return self._state_machine.layout

    def snapshot(self) -> GenerationState:
        return self._state_machine.snapshot()

    def advance(self, text: str) -> None:
        for char in text:
            before = self._state_machine.snapshot()
            if before.step is StepKind.TEXT and GenerationStateMachine._TEXT_CHAR.fullmatch(char):
                self._session.observe_text(before, char)
            self._state_machine.consume(char)

    def allowed_patterns(self, max_length: int = 4) -> Sequence[AllowedPattern]:
        state = self.snapshot()
        limit = min(max_length, self.remaining_before_boundary())
        if limit <= 0:
            return ()
        return self._session.allowed_patterns(state, limit)

    def remaining_before_boundary(self) -> int:
        """返回当前位置到下一句读、顿号或行尾之间可生成的汉字数。"""
        return self._state_machine.remaining_before_boundary()

    def candidate_context(self) -> Any:
        return self._session.candidate_context(self.snapshot())


class FixedLineController:
    """在指定句位置提供硬性文本前缀，同时保留底层格律状态机。"""

    def __init__(self, controller: GenerationController, fixed_lines: dict[int, str]):
        self._controller = controller
        self._fixed_lines = dict(fixed_lines)

    @property
    def layout(self):
        return self._controller.layout

    def snapshot(self):
        return self._controller.snapshot()

    def advance(self, text: str) -> None:
        self._controller.advance(text)

    def allowed_patterns(self, max_length: int = 4):
        return self._controller.allowed_patterns(max_length)

    def remaining_before_boundary(self) -> int:
        return self._controller.remaining_before_boundary()

    def candidate_context(self) -> Any:
        return self._controller.candidate_context()

    def forced_prefix(self) -> str | None:
        state = self.snapshot()
        if state.is_finished or state.line_index not in self._fixed_lines:
            return None
        if state.step not in {StepKind.TEXT, StepKind.CAESURA}:
            return None
        text = self._fixed_lines[state.line_index]
        line = self.layout.lines[state.line_index]
        result: list[str] = []
        for index in range(state.char_index, len(text)):
            if index in line.caesura_positions:
                result.append("、")
            result.append(text[index])
        return "".join(result)
