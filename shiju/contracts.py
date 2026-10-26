from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping


class JobKind(str, Enum):
    GENERATE = "generate"
    PARTIAL_GENERATE = "partial_generate"
    # Legacy wire name kept for already queued jobs and old clients.
    REWRITE = "rewrite"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ContractError(ValueError):
    """Raised when a public request does not satisfy the tool contract."""


def validate_sentence_inputs(inputs, fixed, targets=None):
    if inputs is None:
        return
    if not isinstance(inputs, dict) or not inputs or len(inputs) > 128:
        raise ContractError("逐句输入必须是非空句号映射")
    expected = {str(number) for number in range(1, len(inputs) + 1)}
    if set(inputs) != expected or any(not isinstance(text, str) for text in inputs.values()):
        raise ContractError("逐句输入必须使用从1开始的连续句号")
    if not fixed or len(fixed) >= len(inputs):
        raise ContractError("请至少保留一句，并至少留出一句用于生成")
    if any(key not in inputs or not str(text).strip() or inputs[key].strip() != str(text).strip()
           for key, text in fixed.items()):
        raise ContractError("保留句与逐句输入不一致或为空")
    if targets is not None and set(targets) != {int(key) for key in inputs if key not in fixed}:
        raise ContractError("生成句号与保留句不一致")


@dataclass(frozen=True)
class SamplingOptions:
    max_new_tokens: int = 4096
    temperature: float = 0.6
    top_p: float = 0.95
    top_k: int = 20
    min_p: float = 0.0

    def __post_init__(self) -> None:
        if self.max_new_tokens <= 0:
            raise ContractError("max_new_tokens 必须大于 0")
        if self.temperature <= 0:
            raise ContractError("temperature 必须大于 0")
        if not 0 < self.top_p <= 1:
            raise ContractError("top_p 必须在 (0, 1] 范围内")
        if self.top_k < 0:
            raise ContractError("top_k 不能小于 0")
        if not 0 <= self.min_p <= 1:
            raise ContractError("min_p 必须在 [0, 1] 范围内")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "SamplingOptions":
        return cls(**dict(value or {}))


@dataclass(frozen=True)
class GeneratePoemRequest:
    meter_type: str
    form_name: str
    theme: str
    variant_name: str | None = None
    rhyme_dict_name: str = "Xinyun"
    requirement: str = ""
    task_type: str = "instruction"
    use_thinking: bool = False
    cipai_data_path: str = "PoeTone-main/data/cipai_data.json"
    num_lines: int | None = None
    strict_polyphonic: bool = False
    candidate_count: int = 1
    task_options: dict[str, Any] = field(default_factory=dict)
    rhyme_mode: str = "auto"
    rhyme_parts: dict[str, str] = field(default_factory=dict)
    # Compatibility only. New callers must use PartialGeneratePoemRequest.
    fixed_lines: dict[str, str] = field(default_factory=dict)
    sentence_inputs: dict[str, str] | None = None
    sampling: SamplingOptions = field(default_factory=SamplingOptions)

    def __post_init__(self) -> None:
        if self.meter_type == "汉俳" and self.task_type != "one-shot":
            object.__setattr__(self, "task_type", "one-shot")
        validate_sentence_inputs(self.sentence_inputs, self.fixed_lines)
        if "omit_original_target_from_prompt" in self.task_options:
            raise ContractError("omit_original_target_from_prompt 仅适用于 rewrite")
        for name in ("meter_type", "form_name", "rhyme_dict_name"):
            if not str(getattr(self, name)).strip():
                raise ContractError(f"{name} 不能为空")
        if not 1 <= self.candidate_count <= 5:
            raise ContractError("candidate_count 必须在 1 到 5 之间")
        if self.rhyme_mode not in {"auto", "fixed", "random"}:
            raise ContractError("rhyme_mode 必须是 auto、fixed 或 random")
        if self.rhyme_mode == "fixed" and not self.rhyme_parts:
            raise ContractError("fixed 模式至少要指定一个 rhyme_parts 韵组")
        if any(not str(key).strip() or not str(value).strip() for key, value in self.rhyme_parts.items()):
            raise ContractError("rhyme_parts 的韵组和韵部不能为空")
        for number, text in self.fixed_lines.items():
            try:
                if int(number) <= 0:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise ContractError("fixed_lines 的句号必须是正整数") from exc
            if not str(text).strip():
                raise ContractError("fixed_lines 的句子不能为空")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "GeneratePoemRequest":
        data = dict(value)
        data.setdefault("theme", "")
        data["sampling"] = SamplingOptions.from_mapping(data.get("sampling"))
        data["task_options"] = dict(data.get("task_options") or {})
        data["fixed_lines"] = {str(k): str(v) for k, v in (data.get("fixed_lines") or {}).items()}
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RewritePoemRequest:
    original_text: str
    target_line_numbers: tuple[int, ...]
    meter_type: str
    form_name: str
    source_poem_id: str | None = None
    source_version: str | None = None
    source_title: str | None = None
    variant_name: str | None = None
    rhyme_dict_name: str = "Xinyun"
    requirement: str = ""
    theme: str = "局部改写"
    num_lines: int | None = None
    strict_polyphonic: bool = False
    strict_context: bool = True
    candidate_count: int = 1
    task_options: dict[str, Any] = field(default_factory=dict)
    rhyme_mode: str = "auto"
    rhyme_parts: dict[str, str] = field(default_factory=dict)
    fixed_lines: dict[str, str] = field(default_factory=dict)
    sentence_inputs: dict[str, str] | None = None
    sampling: SamplingOptions = field(
        default_factory=lambda: SamplingOptions(max_new_tokens=512, temperature=0.8)
    )

    def __post_init__(self) -> None:
        validate_sentence_inputs(self.sentence_inputs, self.fixed_lines, self.target_line_numbers)
        if not self.original_text.strip():
            raise ContractError("original_text 不能为空")
        if "omit_original_target_from_prompt" in self.task_options and type(
            self.task_options["omit_original_target_from_prompt"]
        ) is not bool:
            raise ContractError("task_options.omit_original_target_from_prompt 必须是布尔值")
        if not self.meter_type.strip() or not self.form_name.strip():
            raise ContractError("meter_type 和 form_name 不能为空")
        normalized = tuple(sorted(set(self.target_line_numbers)))
        if normalized != self.target_line_numbers:
            raise ContractError("target_line_numbers 必须升序且不能重复")
        if not 1 <= len(normalized) <= 64:
            raise ContractError("每次必须指定一至六十四句")
        if any(number <= 0 for number in normalized):
            raise ContractError("target_line_numbers 使用从 1 开始的正整数")
        if not 1 <= self.candidate_count <= 20:
            raise ContractError("rewrite 的 candidate_count 必须在 1 到 20 之间")
        if self.rhyme_mode not in {"auto", "fixed", "random"}:
            raise ContractError("rhyme_mode 必须是 auto、fixed 或 random")
        if self.rhyme_mode == "fixed" and not self.rhyme_parts:
            raise ContractError("fixed 模式至少要指定一个 rhyme_parts 韵组")
        if not self.strict_context:
            raise ContractError("MVP 的指定句重写只支持 strict_context=true")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RewritePoemRequest":
        data = dict(value)
        data["target_line_numbers"] = tuple(data.get("target_line_numbers") or ())
        sampling = data.get("sampling")
        data["sampling"] = SamplingOptions.from_mapping(
            {"max_new_tokens": 512, "temperature": 0.8, **dict(sampling or {})}
        )
        data["task_options"] = dict(data.get("task_options") or {})
        data["fixed_lines"] = {str(k): str(v) for k, v in (data.get("fixed_lines") or {}).items()}
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PartialGeneratePoemRequest(RewritePoemRequest):
    """Partial generation keeps its own candidate limit with or without an original."""

    def __post_init__(self) -> None:
        super().__post_init__()
        if "omit_original_target_from_prompt" in self.task_options:
            raise ContractError("omit_original_target_from_prompt 仅适用于 rewrite，不适用于部分生成")
        if self.candidate_count > 5:
            raise ContractError("部分生成的 candidate_count 必须在 1 到 5 之间")


@dataclass(frozen=True)
class JobSubmission:
    job_id: str
    status: str
    waiting_for_worker: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
