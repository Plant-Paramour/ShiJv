from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from dataclasses import replace

from ..domain import AllowedPattern, GenerationState, StepKind
from ..state import GenerationController


class RewriteController:
    """Generate only the selected contiguous rewrite span."""

    def __init__(
        self,
        controller: GenerationController,
        fixed_lines: Mapping[int, str],
        target_indices: Sequence[int],
    ):
        self._controller = controller
        self._fixed_lines = dict(fixed_lines)
        self._target_indices = frozenset(target_indices)

    @property
    def layout(self):
        return self._controller.layout

    def snapshot(self) -> GenerationState:
        state = self._controller.snapshot()
        if state.line_index > max(self._target_indices):
            return replace(state, step=StepKind.FINISHED, line=None)
        return state

    def advance(self, text: str) -> None:
        self._controller.advance(text)

    def allowed_patterns(self, max_length: int = 4) -> Sequence[AllowedPattern]:
        return self._controller.allowed_patterns(max_length)

    def remaining_before_boundary(self) -> int:
        return self._controller.remaining_before_boundary()

    def candidate_context(self) -> Any:
        return self._controller.candidate_context()

    def forced_prefix(self) -> str | None:
        """Return the exact fixed text that must be decoded at this position."""
        state = self._controller.snapshot()
        if state.is_finished or state.line_index in self._target_indices or state.step not in {StepKind.TEXT, StepKind.CAESURA}:
            return None
        try:
            text = self._fixed_lines[state.line_index]
        except KeyError as exc:
            raise RuntimeError(f"缺少第 {state.line_index + 1} 句固定上下文") from exc
        layout = self._controller.layout.lines[state.line_index]
        result: list[str] = []
        for index in range(state.char_index, len(text)):
            if index in layout.caesura_positions:
                result.append("、")
            result.append(text[index])
        return "".join(result)
