from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .constraints import (
    ConstraintProfile,
    HanpaiConstraintProfile,
    RelationalConstraintProfile,
    TemplateConstraintProfile,
)
from .data import MeterTemplateRepository, RhymeLexicon
from .domain import CONTENT_MARKER
from .policies import (
    BoundaryCoherencePolicy,
    HanpaiVerifierPolicy,
    PolicyTier,
    RepetitionPenaltyPolicy,
    TangVerifierPolicy,
)
from .processor import (
    ConstrainedLogitsProcessor,
    NewlineSeparatorPolicy,
    ProcessorConfig,
    RelationalSeparatorPolicy,
    SeparatorPolicy,
    TemplateSeparatorPolicy,
)
from .prompts import (
    build_hanpai_prompt,
    build_pailv_prompt,
    build_relational_prompt,
    build_template_prompt,
)
from .state import FixedLineController, GenerationController, GenerationStateMachine
from .vocab import TokenizerLike, VocabLookup


def _identity_output(text: str) -> str:
    return text


def _strip_hanpai_caesuras(text: str) -> str:
    prefix, marker, content = text.partition(CONTENT_MARKER)
    if not marker:
        return text
    return prefix + marker + content.replace("、", "")


@dataclass(frozen=True)
class HanpaiOptions:
    line_pattern: str = "5-7-5"
    season_word: str | None = None
    season_words: tuple[str, ...] = ()
    season: str | None = None
    forbid_isolated_level: bool = True
    allow_aojiu: bool = True
    forbid_three_same_ending: bool = True
    rhyme_scheme: str | None = "random"

    def __post_init__(self) -> None:
        selections = (
            self.season_word is not None,
            bool(self.season_words),
            self.season is not None,
        )
        if sum(selections) > 1:
            raise ValueError("季语、候选季语和季节只能配置其中一种")
        if self.season_word is not None and not self.season_word.strip():
            raise ValueError("指定季语不能为空")
        if self.season is not None and not self.season.strip():
            raise ValueError("指定季节不能为空")
        if any(not word.strip() for word in self.season_words):
            raise ValueError("候选季语不能包含空值")


@dataclass(frozen=True)
class PailvOptions:
    """排律的专用格律配置。"""

    allow_aojiu: bool = True
    allow_polyphonic_ending: bool = False


@dataclass(frozen=True)
class TangOptions:
    """绝句与律诗的专用格律配置。"""

    allow_aojiu: bool = True
    allow_polyphonic_ending: bool = False


@dataclass(frozen=True)
class TaskRequest:
    meter_type: str
    form_name: str
    theme: str
    rhyme_dict_name: str
    variant_name: str | None = None
    task_type: str = "instruction"
    requirement: str = ""
    use_thinking: bool = True
    strict_polyphonic: bool = False
    cipai_data_path: str = "PoeTone-main/data/cipai_data.json"
    num_lines: int | None = None
    hanpai: HanpaiOptions = field(default_factory=HanpaiOptions)
    tang: TangOptions = field(default_factory=TangOptions)
    pailv: PailvOptions = field(default_factory=PailvOptions)
    rhyme_mode: str = "auto"
    rhyme_parts: dict[str, str] = field(default_factory=dict)
    skip_planning: bool = False


@dataclass(frozen=True)
class TaskContext:
    tokenizer: TokenizerLike
    vocab: VocabLookup
    lexicon: RhymeLexicon
    meter_source: Path
    boundary_coherence_penalty: float


@dataclass(frozen=True)
class TaskRuntime:
    profile: ConstraintProfile
    messages: list[dict[str, str]]
    separator_policy: SeparatorPolicy
    policy_tiers: tuple[PolicyTier, ...]
    processor_config: ProcessorConfig
    output_transform: Callable[[str], str] = _identity_output
    rhyme_mode: str = "auto"
    rhyme_parts: dict[str, str] = field(default_factory=dict)

    def create_processor(
        self,
        vocab: VocabLookup,
        tokenizer: TokenizerLike,
        input_prompt_len: int,
        *,
        controller: GenerationController | None = None,
        activation_marker: str = CONTENT_MARKER,
        eos_ids=None,
        fixed_lines: dict[int, str] | None = None,
        strict_polyphonic: bool | None = None,
    ) -> ConstrainedLogitsProcessor:
        if controller is None:
            session = self.profile.create_session(
                rhyme_mode=self.rhyme_mode,
                rhyme_parts=self.rhyme_parts,
            )
            normalized = dict(fixed_lines or {})
            if normalized:
                session.prime_fixed_lines(
                    normalized,
                    strict_polyphonic=self.processor_config.strict_polyphonic if strict_polyphonic is None else strict_polyphonic,
                )
            state_machine = GenerationStateMachine(self.profile.layout)
            controller = GenerationController(state_machine, session)
            if normalized:
                controller = FixedLineController(controller, normalized)
        return ConstrainedLogitsProcessor(
            vocab=vocab,
            controller=controller,
            tokenizer=tokenizer,
            input_prompt_len=input_prompt_len,
            separator_policy=self.separator_policy,
            policy_tiers=self.policy_tiers,
            config=self.processor_config,
            activation_marker=activation_marker,
            eos_ids=eos_ids,
        )

    def process_output(self, text: str) -> str:
        return self.output_transform(text)


class TaskFactory(Protocol):
    def create(self, request: TaskRequest, context: TaskContext) -> TaskRuntime: ...


class TemplateTaskFactory:
    def create(self, request: TaskRequest, context: TaskContext) -> TaskRuntime:
        template = MeterTemplateRepository(context.meter_source).get(request.form_name, request.variant_name)
        profile = TemplateConstraintProfile(template, context.lexicon)
        rhyming_lines = frozenset(
            index for index, line in enumerate(template.lines) if line.rhyme_group is not None
        )
        separator = TemplateSeparatorPolicy(context.tokenizer, rhyming_lines)
        boundary = BoundaryCoherencePolicy(
            context.vocab.common_bigrams(), context.boundary_coherence_penalty
        )
        messages = build_template_prompt(
            task_type=request.task_type,
            template=template,
            theme=request.theme,
            requirement=request.requirement,
            use_thinking=request.use_thinking,
            rhyme_dict_name=request.rhyme_dict_name,
            cipai_data_path=request.cipai_data_path,
            examples_dir=context.meter_source / "examples",
            skip_planning=request.skip_planning,
        )
        return TaskRuntime(
            profile=profile,
            messages=messages,
            separator_policy=separator,
            policy_tiers=(
                PolicyTier("template", (RepetitionPenaltyPolicy(), boundary)),
            ),
            processor_config=ProcessorConfig(
                strict_polyphonic=request.strict_polyphonic,
                candidate_top_k=64,
            ),
            rhyme_mode=request.rhyme_mode,
            rhyme_parts=dict(request.rhyme_parts),
        )


class RelationalTaskFactory:
    def create(self, request: TaskRequest, context: TaskContext) -> TaskRuntime:
        line_length, num_lines, rhyme_type = parse_tang_format(request.form_name)
        profile = RelationalConstraintProfile(
            line_length=line_length,
            num_lines=num_lines,
            rhyme_type=rhyme_type,
            lexicon=context.lexicon,
            allow_aojiu=request.tang.allow_aojiu,
            strict_polyphonic=request.strict_polyphonic,
        )
        boundary = BoundaryCoherencePolicy(
            context.vocab.common_bigrams(), context.boundary_coherence_penalty
        )
        messages = build_relational_prompt(
            task_type=request.task_type,
            form_name=request.form_name,
            theme=request.theme,
            line_length=line_length,
            num_lines=num_lines,
            requirement=request.requirement,
            use_thinking=request.use_thinking,
            rhyme_dict_name=request.rhyme_dict_name,
            allow_aojiu=request.tang.allow_aojiu,
            skip_planning=request.skip_planning,
        )
        return TaskRuntime(
            profile=profile,
            messages=messages,
            separator_policy=RelationalSeparatorPolicy(context.tokenizer),
            policy_tiers=(
                PolicyTier(
                    "tang-full",
                    (
                        TangVerifierPolicy(
                            context.lexicon,
                            "full",
                            allow_aojiu=request.tang.allow_aojiu,
                            strict_polyphonic=request.strict_polyphonic,
                            allow_polyphonic_ending=request.tang.allow_polyphonic_ending,
                        ),
                        boundary,
                    ),
                ),
                PolicyTier(
                    "tang-critical",
                    (
                        TangVerifierPolicy(
                            context.lexicon,
                            "critical",
                            allow_aojiu=request.tang.allow_aojiu,
                            strict_polyphonic=request.strict_polyphonic,
                            allow_polyphonic_ending=request.tang.allow_polyphonic_ending,
                        ),
                    ),
                ),
            ),
            processor_config=ProcessorConfig(
                relax_rhyme_on_empty=True,
                raw_on_no_candidates=True,
                raw_after_policy_failure=True,
                release_constraints_after_finish=True,
                separator_empty_returns_raw=True,
                strict_polyphonic=request.strict_polyphonic,
            ),
            rhyme_mode=request.rhyme_mode,
            rhyme_parts=dict(request.rhyme_parts),
        )


class HanpaiTaskFactory:
    def create(self, request: TaskRequest, context: TaskContext) -> TaskRuntime:
        options = request.hanpai
        line_lengths = parse_hanpai_format(options.line_pattern)
        profile = HanpaiConstraintProfile(
            line_lengths=line_lengths,
            lexicon=context.lexicon,
            rhyme_scheme=options.rhyme_scheme,
            forbid_isolated_level=options.forbid_isolated_level,
            allow_aojiu=options.allow_aojiu,
            forbid_three_same_ending=options.forbid_three_same_ending,
        )
        boundary = BoundaryCoherencePolicy(
            context.vocab.common_bigrams(), context.boundary_coherence_penalty
        )
        messages = build_hanpai_prompt(
            task_type="one-shot",
            form_name=request.form_name,
            theme=request.theme,
            line_lengths=line_lengths,
            requirement=request.requirement,
            use_thinking=request.use_thinking,
            rhyme_dict_name=request.rhyme_dict_name,
            season_word=options.season_word,
            season_words=options.season_words,
            season=options.season,
            forbid_isolated_level=options.forbid_isolated_level,
            allow_aojiu=options.allow_aojiu,
            forbid_three_same_ending=options.forbid_three_same_ending,
            rhyme_scheme=profile.rhyme_scheme,
            skip_planning=request.skip_planning,
        )
        return TaskRuntime(
            profile=profile,
            messages=messages,
            separator_policy=NewlineSeparatorPolicy(context.tokenizer),
            policy_tiers=(
                PolicyTier(
                    "hanpai",
                    (
                        HanpaiVerifierPolicy(
                            context.lexicon,
                            strict_polyphonic=request.strict_polyphonic,
                        ),
                        RepetitionPenaltyPolicy(),
                        boundary,
                    ),
                ),
            ),
            processor_config=ProcessorConfig(
                strict_polyphonic=request.strict_polyphonic,
            ),
            output_transform=_strip_hanpai_caesuras,
            rhyme_mode=request.rhyme_mode,
            rhyme_parts=dict(request.rhyme_parts),
        )


class PailvTaskFactory:
    def create(self, request: TaskRequest, context: TaskContext) -> TaskRuntime:
        line_length, num_lines = parse_pailv_format(
            request.form_name,
            request.num_lines,
        )
        profile = RelationalConstraintProfile(
            line_length=line_length,
            num_lines=num_lines,
            rhyme_type="平韵",
            lexicon=context.lexicon,
            allow_aojiu=request.pailv.allow_aojiu,
            strict_polyphonic=request.strict_polyphonic,
        )
        boundary = BoundaryCoherencePolicy(
            context.vocab.common_bigrams(), context.boundary_coherence_penalty
        )
        messages = build_pailv_prompt(
            task_type=request.task_type,
            form_name=request.form_name,
            theme=request.theme,
            line_length=line_length,
            num_lines=num_lines,
            requirement=request.requirement,
            use_thinking=request.use_thinking,
            rhyme_dict_name=request.rhyme_dict_name,
            allow_aojiu=request.pailv.allow_aojiu,
            skip_planning=request.skip_planning,
        )
        return TaskRuntime(
            profile=profile,
            messages=messages,
            separator_policy=RelationalSeparatorPolicy(context.tokenizer),
            policy_tiers=(
                PolicyTier(
                    "pailv-strict",
                    (
                        TangVerifierPolicy(
                            context.lexicon,
                            "full",
                            allow_aojiu=request.pailv.allow_aojiu,
                            enforce_style=False,
                            strict_polyphonic=request.strict_polyphonic,
                            allow_polyphonic_ending=request.pailv.allow_polyphonic_ending,
                        ),
                        RepetitionPenaltyPolicy(),
                        boundary,
                    ),
                ),
            ),
            processor_config=ProcessorConfig(
                strict_polyphonic=request.strict_polyphonic,
            ),
            rhyme_mode=request.rhyme_mode,
            rhyme_parts=dict(request.rhyme_parts),
        )


class TaskFactoryRegistry:
    def __init__(self):
        self._factories: dict[str, TaskFactory] = {}

    def register(self, meter_type: str, factory: TaskFactory) -> None:
        self._factories[meter_type] = factory

    def create(self, request: TaskRequest, context: TaskContext) -> TaskRuntime:
        try:
            factory = self._factories[request.meter_type]
        except KeyError as exc:
            raise ValueError(f"不支持的生成类型: {request.meter_type}") from exc
        return factory.create(request, context)


def default_task_registry() -> TaskFactoryRegistry:
    registry = TaskFactoryRegistry()
    registry.register("宋词", TemplateTaskFactory())
    registry.register("唐诗", RelationalTaskFactory())
    registry.register("汉俳", HanpaiTaskFactory())
    registry.register("排律", PailvTaskFactory())
    return registry


def parse_tang_format(form_name: str) -> tuple[int, int, str]:
    name = form_name.strip()
    line_length = 5 if "五" in name else 7
    num_lines = 4 if "绝" in name else 8
    rhyme_type = "仄韵" if "仄" in name and "韵" in name else "平韵"
    return line_length, num_lines, rhyme_type


def parse_pailv_format(
    form_name: str,
    num_lines: int | None,
) -> tuple[int, int]:
    normalized = form_name.strip()
    formats = {"五言排律": 5, "七言排律": 7}
    try:
        line_length = formats[normalized]
    except KeyError as exc:
        raise ValueError(
            f"排律格式仅支持“五言排律”或“七言排律”，收到: {form_name}"
        ) from exc
    if num_lines is None:
        raise ValueError("排律必须通过 TaskRequest.num_lines 单独指定句数")
    if num_lines < 10 or num_lines % 2 != 0:
        raise ValueError(f"排律句数必须是不少于十句的偶数，收到: {num_lines}")
    return line_length, num_lines


def parse_hanpai_format(line_pattern: str) -> tuple[int, int, int]:
    normalized = (
        line_pattern.strip()
        .replace("－", "-")
        .replace("—", "-")
        .replace("×", "-")
        .replace(" ", "")
    )
    formats = {
        "5-7-5": (5, 7, 5),
        "575": (5, 7, 5),
        "五七五": (5, 7, 5),
        "3-5-3": (3, 5, 3),
        "353": (3, 5, 3),
        "三五三": (3, 5, 3),
    }
    try:
        return formats[normalized]
    except KeyError as exc:
        raise ValueError(
            f"汉俳格式仅支持 5-7-5 或 3-5-3，收到: {line_pattern}"
        ) from exc
