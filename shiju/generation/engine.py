from __future__ import annotations

from pathlib import Path
from dataclasses import replace
from typing import Any

from ..contracts import GeneratePoemRequest, RewritePoemRequest
from ..data import RhymeLexicon
from ..domain import REWRITE_MARKER
from ..rewriting.parser import PoemParseError, parse_poem
from ..rewriting.planner import RewritePlan
from ..rewriting.prompt import build_rewrite_messages
from ..constraints import ContextConstraintError
from ..rewriting.protocol import (
    RewriteProtocolError,
    build_display_text,
    parse_rewrite_protocol,
)
from ..tasks import (
    HanpaiOptions,
    PailvOptions,
    TangOptions,
    TaskContext,
    TaskRequest,
    default_task_registry,
)
from ..vocab import VocabIndex
from .model_runner import ModelRunner
from .protocol import GenerationProtocolError, parse_generation_protocol
from .result import GenerationCandidate
from .session import ActivationMarkerDeadline, TitleContentDeadline
from .partial import PartialGenerationPlan


class ModelProtocolFailure(RuntimeError):
    code = "MODEL_PROTOCOL_ERROR"


class GenerationEngine:
    """Stateless request facade around a resident model and cached vocab indexes."""

    def __init__(
        self,
        runner: ModelRunner,
        *,
        rhyme_dir: str | Path = "Rhyme",
        meter_source: str | Path = "Songci_Meter",
        boundary_coherence_penalty: float = 50.0,
    ):
        self.runner = runner
        self.rhyme_dir = Path(rhyme_dir)
        self.meter_source = Path(meter_source)
        self.boundary_coherence_penalty = boundary_coherence_penalty
        self._resources: dict[str, tuple[RhymeLexicon, VocabIndex]] = {}

    def _eos_ids(self):
        from ..end_tokens import eos_token_ids
        return eos_token_ids(self.runner.tokenizer, getattr(self.runner, "_model", None))

    def generate_poem(
        self,
        request: GeneratePoemRequest,
        *,
        use_constraints: bool = True,
        on_candidate_event=None,
    ) -> dict[str, Any]:
        runtime, vocab = self._runtime_for(request)
        messages = runtime.messages
        if request.fixed_lines:
            fixed_context = "\n".join(
                f"- 第{number}句：{text}"
                for number, text in sorted(request.fixed_lines.items(), key=lambda item: int(item[0]))
            )
            messages = [*runtime.messages]
            messages[-1] = {
                **messages[-1],
                "content": messages[-1]["content"]
                + "\n\n【部分生成】以下句子是确定内容，必须原样保留；请只生成其余句子，并让全文自然衔接：\n"
                + fixed_context,
            }
        prompt = self.runner.render_chat(
            messages,
            enable_thinking=request.use_thinking,
        )
        prompt_length = self._prompt_length(prompt)
        candidates = []
        for ordinal in range(1, request.candidate_count + 1):
            last_error = None
            for attempt in range(1, 4):
                if on_candidate_event: on_candidate_event("candidate.started", {"ordinal": ordinal, "attempt": attempt})
                processors = None
                if use_constraints:
                    fixed_lines = {}
                    for number, text in request.fixed_lines.items():
                        try:
                            index = int(number) - 1
                        except (TypeError, ValueError) as exc:
                            raise ValueError(f"指定句序号无效: {number}") from exc
                        if index < 0 or index >= len(runtime.profile.layout.lines):
                            raise ValueError(f"指定句序号超出范围: {number}")
                        fixed_lines[index] = str(text).strip()
                    processors = (runtime.create_processor(
                        vocab, self.runner.tokenizer, prompt_length,
                        eos_ids=self._eos_ids(),
                        fixed_lines=fixed_lines,
                        strict_polyphonic=request.strict_polyphonic,
                    ),)
                partial = ""
                def on_text(piece: str) -> None:
                    nonlocal partial
                    partial += piece
                    if on_candidate_event:
                        on_candidate_event("candidate.delta", {"ordinal": ordinal, "attempt": attempt, "delta": piece, "partial_text": partial})
                deadline = TitleContentDeadline(self.runner.tokenizer, prompt_length) if use_constraints else None
                stopping = (deadline,) if deadline is not None else None
                if hasattr(self.runner, "generate_streaming"):
                    raw = self.runner.generate_streaming(prompt, request.sampling, logits_processors=processors,
                                                         stopping_criteria=stopping, on_text=on_text)
                else:
                    raw = self.runner.generate(prompt, request.sampling, logits_processors=processors,
                                               stopping_criteria=stopping)
                    on_text(raw)
                try:
                    if deadline is not None and deadline.reason:
                        raise GenerationProtocolError(deadline.reason)
                    parsed = parse_generation_protocol(raw)
                    if use_constraints and not processors[0].constraints_started:
                        raise GenerationProtocolError("正文标记未以约定形式出现，约束未启动；已驳回本次生成")
                    if use_constraints and request.meter_type == "宋词":
                        self._validate_template_output(runtime, parsed.content, request.strict_polyphonic)
                    if use_constraints and request.meter_type == "汉俳":
                        self._validate_hanpai_output(runtime, parsed.content, request.strict_polyphonic)
                except (GenerationProtocolError, PoemParseError, ContextConstraintError) as exc:
                    last_error = exc
                    if on_candidate_event: on_candidate_event("candidate.retry", {"ordinal": ordinal, "attempt": attempt, "error": str(exc)})
                    continue
                candidate = {"raw_output": raw, "text": runtime.process_output(raw), "title": parsed.title, "content": parsed.content}
                if hasattr(self.runner, "last_generation_seconds"):
                    candidate["telemetry"] = {
                        "generation_seconds": self.runner.last_generation_seconds,
                        "first_token_seconds": getattr(self.runner, "last_first_token_seconds", None),
                        **getattr(self.runner, "last_generation_telemetry", {}),
                    }
                candidates.append(candidate)
                if on_candidate_event: on_candidate_event("candidate.completed", {"ordinal": ordinal, "attempt": attempt, **candidate})
                break
            else:
                if on_candidate_event: on_candidate_event("candidate.failed", {"ordinal": ordinal, "error": str(last_error)})
                raise ModelProtocolFailure(f"候选 {ordinal} 连续三次未遵守生成协议: {last_error}") from last_error
        return {
            "status": "succeeded",
            "candidates": candidates,
            "config": request.to_dict(),
        }

    @staticmethod
    def _validate_hanpai_output(runtime, content: str, strict_polyphonic: bool) -> None:
        poem = parse_poem(content)
        layout = runtime.profile.layout
        if len(poem.lines) != len(layout.lines):
            raise GenerationProtocolError("汉俳正文必须恰好三行")
        session = runtime.profile.create_session(
            rhyme_mode="auto",
            rhyme_parts={key: value for key, value in runtime.rhyme_parts.items()
                         if str(value).lower() != "random"},
        )
        session.prime_fixed_lines(
            {index: line.text for index, line in enumerate(poem.lines)},
            strict_polyphonic=strict_polyphonic,
        )

    @staticmethod
    def _validate_template_output(runtime, content: str, strict_polyphonic: bool) -> None:
        """Do not publish a protocol-correct but off-meter Songci candidate."""
        poem = parse_poem(content)
        layout = runtime.profile.layout
        if len(poem.lines) != len(layout.lines):
            raise GenerationProtocolError(
                f"宋词句数与词谱不符：应为 {len(layout.lines)} 句，实际为 {len(poem.lines)} 句"
            )
        lines = {}
        for index, line in enumerate(poem.lines):
            expected = layout.lines[index].length
            if len(line.text) != expected:
                raise GenerationProtocolError(
                    f"宋词第 {index + 1} 句字数与词谱不符：应为 {expected} 字，实际为 {len(line.text)} 字"
                )
            lines[index] = line.text
        # A fresh automatic rhyme session checks that the generated rhyme
        # group is internally coherent, while explicit user locks stay fixed.
        fixed_parts = {
            key: value for key, value in runtime.rhyme_parts.items()
            if str(value).lower() != "random"
        }
        session = runtime.profile.create_session(rhyme_mode="auto", rhyme_parts=fixed_parts)
        session.prime_fixed_lines(lines, strict_polyphonic=strict_polyphonic)

    def partial_generate_poem(self, request, *, on_candidate_event=None):
        original = parse_poem(request.original_text) if isinstance(request, RewritePoemRequest) else None
        if original and request.meter_type == "排律" and request.num_lines is None:
            from dataclasses import replace
            request = replace(request, num_lines=len(original.lines))
        runtime, vocab = self._runtime_for(request)
        fixed = ({index: line.text for index, line in enumerate(original.lines)
                  if index + 1 not in request.target_line_numbers} if original and request.sentence_inputs is None else
                 {int(number) - 1: text.strip() for number, text in request.fixed_lines.items()})
        layout = runtime.profile.layout
        if not fixed or any(index < 0 or index >= len(layout.lines) or len(text) != layout.lines[index].length
                            for index, text in fixed.items()):
            raise ValueError("部分生成固定句为空、越界或字数不符")
        if original and len(original.lines) != len(layout.lines):
            raise ValueError("原文句数与诗体不符")
        if original and any(number > len(layout.lines) for number in request.target_line_numbers):
            raise ValueError("目标句号超出原文范围")
        inputs = request.sentence_inputs
        if inputs is not None:
            expected_keys = {str(index + 1) for index in range(len(layout.lines))}
            if set(inputs) != expected_keys or any(not isinstance(text, str) for text in inputs.values()):
                raise ValueError("逐句输入必须包含篇式的全部句号")
            if any(inputs[str(index + 1)].strip() != text for index, text in fixed.items()):
                raise ValueError("保留句与逐句输入不一致")
            if original and set(request.target_line_numbers) != {int(key) for key in inputs if int(key) - 1 not in fixed}:
                raise ValueError("生成句号与保留句不一致")
        allow_context_constraint_conflicts = bool(
            request.task_options.get("allow_context_constraint_conflicts")
        )
        plan = PartialGenerationPlan(runtime, fixed, allow_context_constraint_conflicts)
        plan.create_controller(request.strict_polyphonic)
        context = "\n".join(f"第 {index + 1} 句：{fixed.get(index, '【待生成】')}" for index in range(len(layout.lines)))
        rewrite_context = ""
        old_targets = ({int(key): text.strip() for key, text in inputs.items()
                        if int(key) - 1 not in fixed and text.strip()} if inputs is not None else
                       {line.number: line.text for line in original.lines
                        if line.number in request.target_line_numbers} if original else {})
        if old_targets:
            targets = "、".join(f"第{number}句" for number in sorted(old_targets))
            target_text = "\n".join(f"- 第{number}句：{text}" for number, text in sorted(old_targets.items()))
            rewrite_context = (
                f"\n\n【部分生成任务：指定句改写】\n请改写{targets}，逐字保留固定上下文中已确定的句子。"
                "每个目标位置都必须生成与对应原句不同的新句，禁止输出对应原句。"
                "不得原样复述，也不要只做无意义的同义替换；"
                "应根据用户要求生成有实质变化的新句，并与所有保留句在语义和章法上自然衔接。"
                "保留句由程序直接写入，也是续写后文的真实上下文。"
                "遵守既定输出格式，按原句序输出完整诗稿。\n\n"
                "其余待生成位置如无旧句，则根据要求补全。\n\n"
                "【目标句：负面样本】\n以下旧句仅用于理解需要替换的内容，"
                "不是待补全内容，也不是可复制的候选句；禁止在对应目标位置原样输出：\n"
                + target_text
            )
        messages = [*runtime.messages]
        messages[-1] = {**messages[-1], "content": messages[-1]["content"] +
                       "\n固定上下文（程序保留固定句，只开放待生成位置；注意与后文衔接）：\n" + context +
                       rewrite_context}
        prompt = self.runner.render_chat(messages, enable_thinking=False)
        candidates = []
        for ordinal in range(1, request.candidate_count + 1):
            for attempt in range(1, 4):
                def emit(name, **fields):
                    if on_candidate_event:
                        on_candidate_event(name, {"ordinal": ordinal, "attempt": attempt, **fields})
                emit("candidate.started")
                partial = ""
                def on_text(piece):
                    nonlocal partial
                    partial += piece
                    emit("candidate.delta", delta=piece, partial_text=partial)
                processor = runtime.create_processor(vocab, self.runner.tokenizer, self._prompt_length(prompt),
                                                     controller=plan.create_controller(request.strict_polyphonic), eos_ids=self._eos_ids())
                try:
                    raw = self.runner.generate_partial(prompt, request.sampling, processor=processor, plan=plan, on_text=on_text)
                    parsed = parse_generation_protocol(raw)
                    plan.validate(parsed.content, request.strict_polyphonic)
                except RuntimeError as exc:
                    emit("candidate.failed", error=str(exc))
                    raise
                except (ValueError, PoemParseError) as exc:
                    emit("candidate.retry", error=str(exc))
                    if attempt == 3:
                        emit("candidate.failed", error=str(exc))
                        raise ModelProtocolFailure(f"部分生成连续三次失败: {exc}") from exc
                    continue
                candidate = {"raw_output": raw, "text": runtime.process_output(raw), "title": parsed.title,
                             "content": parsed.content}
                if original:
                    replacements = {str(line.number): line.source_text for line in parse_poem(parsed.content).lines
                                    if line.number in request.target_line_numbers}
                    candidate.update(full_text=parsed.content, replacements=replacements, revision_note="",
                                     display_text=parsed.content)
                candidates.append(candidate)
                emit("candidate.completed", **candidate)
                break
        result = {"status": "succeeded", "candidates": candidates, "config": request.to_dict()}
        if original:
            result.update({key: value for key, value in candidates[0].items() if key != "raw_output"})
        return result

    def rewrite_poem(self, request: RewritePoemRequest, *, on_candidate_event=None) -> dict[str, Any]:
        parsed = parse_poem(request.original_text)
        if request.meter_type == "排律" and request.num_lines is None:
            request = RewritePoemRequest(
                **{
                    **request.to_dict(),
                    "target_line_numbers": request.target_line_numbers,
                    "sampling": request.sampling,
                    "num_lines": len(parsed.lines),
                }
            )
        runtime, vocab = self._runtime_for(request)
        plan = RewritePlan.build(request, runtime)
        runtime = plan.runtime_for_rewrite()
        messages = build_rewrite_messages(request, plan.poem)
        prompt = self.runner.render_chat(messages, enable_thinking=False)
        prompt_length = self._prompt_length(prompt)

        candidates: list[dict[str, Any]] = []
        for ordinal in range(1, request.candidate_count + 1):
            last_error: Exception | None = None
            for attempt in range(3):
                attempt_number = attempt + 1
                if on_candidate_event: on_candidate_event("candidate.started", {"ordinal": ordinal, "attempt": attempt_number})
                active_messages = messages
                if attempt or candidates:
                    feedback = ""
                    if candidates:
                        feedback += (
                            f"\n这是本次任务的第{ordinal}版。继续严格遵守用户提出的修改目标、保留项、禁用项和语体要求，"
                            "提供另一种同样符合要求的表达方案，不要为了追求差异而改变用户要求保留的内容。"
                        )
                    if last_error:
                        feedback += f"\n上次失败原因：{last_error}。请重新改写。"
                    active_messages = [
                        messages[0],
                        {
                            "role": "user",
                            "content": messages[1]["content"] + feedback + (
                                "\n\n必须输出 [rewrite]，随后只输出目标连续片段，不得输出完整诗稿、规划或说明。"
                            ),
                        },
                    ]
                    prompt = self.runner.render_chat(active_messages, enable_thinking=False)
                    prompt_length = self._prompt_length(prompt)
                rewrite_runtime = replace(runtime, processor_config=replace(
                    runtime.processor_config, release_constraints_after_finish=False))
                processor = rewrite_runtime.create_processor(
                    vocab,
                    self.runner.tokenizer,
                    prompt_length,
                    controller=plan.create_controller(),
                    activation_marker=REWRITE_MARKER,
                    eos_ids=self._eos_ids(),
                )
                deadline = ActivationMarkerDeadline(
                    self.runner.tokenizer,
                    prompt_length,
                    REWRITE_MARKER,
                    max_tokens=128,
                )
                partial = ""
                def on_text(piece: str) -> None:
                    nonlocal partial
                    partial += piece
                    if on_candidate_event: on_candidate_event("candidate.delta", {"ordinal": ordinal, "attempt": attempt_number, "delta": piece, "partial_text": partial})
                if hasattr(self.runner, "generate_streaming"):
                    raw = self.runner.generate_streaming(prompt, request.sampling, logits_processors=(processor,), stopping_criteria=(deadline,), on_text=on_text)
                else:
                    raw = self.runner.generate(prompt, request.sampling, logits_processors=(processor,), stopping_criteria=(deadline,))
                    on_text(raw)
                try:
                    protocol = parse_rewrite_protocol(raw)
                    replacements = plan.replacements_from_text(protocol.rewrite_text)
                    full_text = plan.apply(replacements)
                    original_lines = {line.number: line.text for line in parse_poem(request.original_text).lines}
                    if any(parse_poem(text).lines[0].text == original_lines[number] for number, text in replacements.items()):
                        raise RewriteProtocolError("生成片段包含原样复制句，请重新生成")
                    if any(full_text == candidate['full_text'] for candidate in candidates):
                        raise RewriteProtocolError("候选结果重复，请重新生成")
                except RewriteProtocolError as exc:
                    last_error = exc
                    if on_candidate_event: on_candidate_event("candidate.retry", {"ordinal": ordinal, "attempt": attempt_number, "error": str(exc)})
                    continue
                except (PoemParseError, ContextConstraintError) as exc:
                    raise exc
                candidate = {
                        "title": request.source_title or "",
                        "revision_note": protocol.revision_note,
                        "replacements": {
                            str(number): text for number, text in replacements.items()
                        },
                        "full_text": full_text,
                        "display_text": build_display_text(
                            protocol.revision_note,
                            full_text,
                        ),
                        "validation": {
                            "scope": "rewritten_lines",
                            "structure": 100,
                            "tonal": 100,
                            "rhyme": 100,
                        },
                        "raw_output": raw,
                    }
                candidates.append(candidate)
                if on_candidate_event: on_candidate_event("candidate.completed", {"ordinal": ordinal, "attempt": attempt_number, "content": full_text, **candidate})
                break
            else:
                if on_candidate_event: on_candidate_event("candidate.failed", {"ordinal": ordinal, "attempt": 3, "error": str(last_error)})
                raise ModelProtocolFailure(
                    f"模型连续三次未遵守重写协议: {last_error}"
                ) from last_error

        primary = candidates[0]
        return {
            "status": "succeeded",
            **{key: value for key, value in primary.items() if key != "raw_output"},
            "candidates": candidates,
            "config": request.to_dict(),
        }


    def _runtime_for(self, request: GeneratePoemRequest | RewritePoemRequest):
        lexicon, vocab = self._resources_for(request.rhyme_dict_name)
        options = dict(request.task_options)
        options.pop("omit_original_target_from_prompt", None)
        options.pop("allow_context_constraint_conflicts", None)
        skip_planning = options.pop("skip_planning", False) is True
        task_request = TaskRequest(
            meter_type=request.meter_type,
            form_name=request.form_name,
            variant_name=request.variant_name,
            theme=request.theme,
            rhyme_dict_name=request.rhyme_dict_name,
            requirement=request.requirement,
            task_type=("one-shot" if request.meter_type == "汉俳" else request.task_type if isinstance(request, GeneratePoemRequest) else "instruction"),
            use_thinking=(request.use_thinking if isinstance(request, GeneratePoemRequest) else False),
            strict_polyphonic=request.strict_polyphonic,
            cipai_data_path=(
                request.cipai_data_path
                if isinstance(request, GeneratePoemRequest)
                else "PoeTone-main/data/cipai_data.json"
            ),
            num_lines=request.num_lines,
            hanpai=HanpaiOptions(**options) if request.meter_type == "汉俳" else HanpaiOptions(),
            tang=(
                TangOptions(allow_aojiu=bool(options.get("allow_aojiu", True)))
                if request.meter_type == "唐诗"
                else TangOptions()
            ),
            pailv=(
                PailvOptions(allow_aojiu=bool(options.get("allow_aojiu", True)))
                if request.meter_type == "排律"
                else PailvOptions()
            ),
            rhyme_mode=request.rhyme_mode,
            rhyme_parts=dict(request.rhyme_parts),
            skip_planning=(
                skip_planning if isinstance(request, GeneratePoemRequest) else False
            ),
        )
        context = TaskContext(
            tokenizer=self.runner.tokenizer,
            vocab=vocab,
            lexicon=lexicon,
            meter_source=self.meter_source,
            boundary_coherence_penalty=self.boundary_coherence_penalty,
        )
        return default_task_registry().create(task_request, context), vocab

    def _resources_for(self, rhyme_dict_name: str) -> tuple[RhymeLexicon, VocabIndex]:
        cached = self._resources.get(rhyme_dict_name)
        if cached is not None:
            return cached
        lexicon = RhymeLexicon(self.rhyme_dir / f"{rhyme_dict_name}.json")
        vocab = VocabIndex(self.runner.tokenizer, lexicon)
        self._resources[rhyme_dict_name] = (lexicon, vocab)
        return lexicon, vocab

    def _prompt_length(self, prompt: str) -> int:
        encoded = self.runner.tokenizer(prompt, return_tensors="pt")
        return encoded.input_ids.shape[1]
