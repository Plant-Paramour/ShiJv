from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import FrozenSet, Optional, Tuple


CONTENT_MARKER = "[content]"
PLAN_MARKER = "[plan]"
REWRITE_MARKER = "[rewrite]"
TITLE_MARKER = "[title]"


class StepKind(str, Enum):
    TEXT = "text"
    CAESURA = "caesura"
    PUNCTUATION = "punctuation"
    NEWLINE = "newline"
    FINISHED = "finished"


class RhymeMode(str, Enum):
    NONE = "none"
    ANY = "any"
    PARTS = "parts"


@dataclass(frozen=True)
class RhymeConstraint:
    mode: RhymeMode = RhymeMode.NONE
    tone: Optional[str] = None
    parts: FrozenSet[str] = field(default_factory=frozenset)
    excluded_parts: FrozenSet[str] = field(default_factory=frozenset)

    @classmethod
    def none(cls) -> "RhymeConstraint":
        return cls()

    @classmethod
    def any(
        cls,
        tone: str,
        excluded_parts: FrozenSet[str] | set[str] | None = None,
    ) -> "RhymeConstraint":
        return cls(
            mode=RhymeMode.ANY,
            tone=tone,
            excluded_parts=frozenset(excluded_parts or ()),
        )

    @classmethod
    def specific(
        cls,
        tone: str,
        parts: FrozenSet[str] | set[str] | tuple[str, ...],
    ) -> "RhymeConstraint":
        return cls(mode=RhymeMode.PARTS, tone=tone, parts=frozenset(parts))


@dataclass(frozen=True)
class AllowedPattern:
    length: int
    tones: str
    rhyme: RhymeConstraint = field(default_factory=RhymeConstraint.none)
    required_text: Optional[str] = None


@dataclass(frozen=True)
class LineLayout:
    length: int
    break_positions: FrozenSet[int] = field(default_factory=frozenset)
    caesura_positions: FrozenSet[int] = field(default_factory=frozenset)
    stanza_index: int = 0
    line_in_stanza: int = 0
    stanza_end: bool = False

    def __post_init__(self) -> None:
        if self.length <= 0:
            raise ValueError("行字数必须大于 0")
        invalid = {
            pos
            for pos in self.break_positions | self.caesura_positions
            if pos <= 0 or pos >= self.length
        }
        if invalid:
            raise ValueError(f"句读位置越界: {sorted(invalid)}")


@dataclass(frozen=True)
class GenerationLayout:
    lines: Tuple[LineLayout, ...]

    def __post_init__(self) -> None:
        if not self.lines:
            raise ValueError("生成布局至少需要一行")


@dataclass(frozen=True)
class GenerationState:
    line_index: int
    char_index: int
    current_line_text: str
    all_text: str
    step: StepKind
    line: Optional[LineLayout]
    terminal_punctuation_pending: bool = False

    @property
    def is_finished(self) -> bool:
        return self.step is StepKind.FINISHED
