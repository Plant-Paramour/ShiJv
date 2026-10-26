from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ModelSettings:
    model_name: str
    quantization: str = "8bit"
    revision: str | None = None
    local_files_only: bool = False


@dataclass(frozen=True)
class GenerationCandidate:
    raw_output: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

