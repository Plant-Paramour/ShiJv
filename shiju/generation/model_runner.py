from __future__ import annotations

import inspect
import time
from collections.abc import Sequence

import torch
from threading import Thread
from transformers import (
    AutoConfig,
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    LogitsProcessorList,
    StoppingCriteriaList,
    TextIteratorStreamer,
    GenerationConfig,
)

from ..contracts import SamplingOptions
from ..end_tokens import eos_token_ids, pad_token_id
from .result import ModelSettings
from .diagnostics import TimedCallable, runtime_diagnostics
from .session import CancellationCriteria, GenerationCancelled


class ModelRunner:
    """Own the tokenizer/model lifecycle and execute model sampling."""

    def __init__(self, settings: ModelSettings):
        self.settings = settings
        self._tokenizer = None
        self._model = None
        self.cancel_requested = None

    def _check_cancelled(self):
        if self.cancel_requested is not None and self.cancel_requested():
            raise GenerationCancelled("任务已由用户取消")

    def _stopping_criteria(self, criteria):
        combined = list(criteria or ())
        if self.cancel_requested is not None:
            combined.append(CancellationCriteria(self.cancel_requested))
        return StoppingCriteriaList(combined) if combined else None

    @property
    def tokenizer(self):
        self.load()
        return self._tokenizer

    @property
    def model(self):
        self.load()
        return self._model

    def load(self) -> None:
        if self._model is not None:
            return
        load_started = time.perf_counter()
        stage_started = load_started
        self.load_telemetry = {}
        name = self.settings.model_name
        quantization = self.settings.quantization.lower().strip()
        config_kwargs = {
            "trust_remote_code": True,
            "local_files_only": self.settings.local_files_only,
        }
        if self.settings.revision:
            config_kwargs["revision"] = self.settings.revision
        config = AutoConfig.from_pretrained(name, **config_kwargs)
        from .windows_kernels import enable_windows_qwen35_kernels
        enable_windows_qwen35_kernels(getattr(config, "model_type", ""))
        self.load_telemetry["config_load_seconds"] = time.perf_counter() - stage_started
        if getattr(config, "model_type", "") == "qwen3_5":
            # Qwen3.5 checkpoints use a multimodal top-level config. The
            # generic causal-LM class expects its nested text config instead;
            # the image-text-to-text class also supports text-only input_ids.
            from transformers import AutoModelForImageTextToText

            model_class = AutoModelForImageTextToText
        else:
            model_class = AutoModelForCausalLM
        common = {
            "device_map": "auto",
            "trust_remote_code": True,
            "local_files_only": self.settings.local_files_only,
        }
        if self.settings.revision:
            common["revision"] = self.settings.revision
        stage_started = time.perf_counter()
        self._tokenizer = AutoTokenizer.from_pretrained(name, **{k: v for k, v in common.items() if k not in {"device_map"}})
        self.load_telemetry["tokenizer_load_seconds"] = time.perf_counter() - stage_started
        stage_started = time.perf_counter()
        dtype = torch.float16 if torch.cuda.is_available() else torch.float32
        if quantization in {"none", "fp16", "float16", ""}:
            self._model = model_class.from_pretrained(
                name,
                torch_dtype=dtype,
                **common,
            )
        elif quantization in {"8bit", "int8", "8"}:
            self._model = model_class.from_pretrained(
                name,
                quantization_config=BitsAndBytesConfig(load_in_8bit=True),
                **common,
            )
        elif quantization in {"4bit", "int4", "nf4", "4"}:
            self._model = model_class.from_pretrained(
                name,
                quantization_config=BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_compute_dtype=torch.float16,
                    bnb_4bit_use_double_quant=True,
                ),
                **common,
            )
        else:
            raise ValueError(
                f"不支持的量化参数: {self.settings.quantization!r}，可选 none、8bit、4bit"
            )
        self._model.eval()
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        self.load_telemetry["weights_construct_quantize_transfer_seconds"] = time.perf_counter() - stage_started
        self.load_seconds = time.perf_counter() - load_started
        self.runtime_diagnostics = runtime_diagnostics(self._model, torch)

    def unload(self):
        """Release weights and CUDA cache only when the caller is idle."""
        import gc
        self._model = None
        self.cancel_requested = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def render_chat(
        self,
        messages: list[dict[str, str]],
        *,
        enable_thinking: bool | None = None,
    ) -> str:
        tokenizer = self.tokenizer
        kwargs = {"tokenize": False, "add_generation_prompt": True}
        if enable_thinking is not None:
            kwargs["enable_thinking"] = enable_thinking
        try:
            return tokenizer.apply_chat_template(messages, **kwargs)
        except TypeError:
            kwargs.pop("enable_thinking", None)
            return tokenizer.apply_chat_template(messages, **kwargs)

    @staticmethod
    def _generation_eos_token_ids(tokenizer, model):
        """Stop on the tokenizer EOS used by constrained decoding as well as model EOS."""
        return eos_token_ids(tokenizer, model)

    def generate(
        self,
        prompt: str,
        sampling: SamplingOptions,
        *,
        logits_processors: Sequence | None = None,
        stopping_criteria: Sequence | None = None,
    ) -> str:
        tokenizer = self.tokenizer
        model = self.model
        self._check_cancelled()
        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
        prompt_length = inputs.input_ids.shape[1]
        with torch.no_grad():
            output_ids = model.generate(
                **inputs,
                max_new_tokens=sampling.max_new_tokens,
                eos_token_id=self._generation_eos_token_ids(tokenizer, model),
                logits_processor=(
                    LogitsProcessorList(list(logits_processors))
                    if logits_processors
                    else None
                ),
                stopping_criteria=self._stopping_criteria(stopping_criteria),
                pad_token_id=pad_token_id(tokenizer, model),
                do_sample=True,
                temperature=sampling.temperature,
                top_p=sampling.top_p,
                top_k=sampling.top_k,
                min_p=sampling.min_p,
            )
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        return tokenizer.decode(
            output_ids[0][prompt_length:],
            skip_special_tokens=True,
        )

    def generate_streaming(self, prompt: str, sampling: SamplingOptions, *, logits_processors=None, stopping_criteria=None, on_text=None) -> str:
        """在线程中生成并通过 Transformers streamer 提供增量文本。"""
        tokenizer = self.tokenizer; model = self.model
        self._check_cancelled()
        prompt_started = time.perf_counter()
        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
        timings = {"input_tokens": int(inputs["input_ids"].shape[1]), "output_tokens": 0,
                   "first_generated_token_seconds": None, "first_forward_seconds": None,
                   "first_forward_started_seconds": None, "text_callback_seconds": 0.0,
                   "text_callback_calls": 0, "first_text_produced_seconds": None,
                   "last_generated_token_seconds": None, "model_generate_seconds": None}
        class MeasuredStreamer(TextIteratorStreamer):
            def put(self, value):
                if self.next_tokens_are_prompt and self.skip_prompt:
                    return super().put(value)
                count = int(value.numel())
                if count and timings["first_generated_token_seconds"] is None:
                    # First generated token handed to the streamer, including special tokens.
                    if torch.cuda.is_available():
                        torch.cuda.synchronize()
                    timings["first_generated_token_seconds"] = time.perf_counter() - generation_started
                timings["output_tokens"] += count
                if count:
                    timings["last_generated_token_seconds"] = time.perf_counter() - generation_started
                return super().put(value)
            def on_finalized_text(self, text, stream_end=False):
                if text and timings["first_text_produced_seconds"] is None:
                    timings["first_text_produced_seconds"] = time.perf_counter() - generation_started
                return super().on_finalized_text(text, stream_end=stream_end)
        streamer = MeasuredStreamer(tokenizer, skip_prompt=True, skip_special_tokens=True)
        measured_processors = [TimedCallable(p) for p in (logits_processors or ())]
        kwargs = dict(inputs, streamer=streamer, max_new_tokens=sampling.max_new_tokens,
                      eos_token_id=self._generation_eos_token_ids(tokenizer, model),
                      logits_processor=LogitsProcessorList(measured_processors) if measured_processors else None,
                      stopping_criteria=self._stopping_criteria(stopping_criteria),
                      pad_token_id=pad_token_id(tokenizer, model), do_sample=True, temperature=sampling.temperature,
                      top_p=sampling.top_p, top_k=sampling.top_k, min_p=sampling.min_p)
        self.last_prompt_prepare_seconds = time.perf_counter() - prompt_started
        errors = []
        first_token_seconds = None
        first_forward_started = None
        forward_count = 0
        def before_forward(module, args):
            nonlocal first_forward_started, forward_count
            forward_count += 1
            if forward_count == 1:
                if torch.cuda.is_available(): torch.cuda.synchronize()
                first_forward_started = time.perf_counter()
                timings["first_forward_started_seconds"] = first_forward_started - generation_started
        def after_forward(module, args, output):
            if forward_count == 1 and first_forward_started is not None:
                if torch.cuda.is_available(): torch.cuda.synchronize()
                timings["first_forward_seconds"] = time.perf_counter() - first_forward_started
        before_hook = model.register_forward_pre_hook(before_forward) if hasattr(model, "register_forward_pre_hook") else None
        after_hook = model.register_forward_hook(after_forward) if hasattr(model, "register_forward_hook") else None
        def run():
            try:
                with torch.inference_mode():
                    model.generate(**kwargs)
            except Exception as exc:
                errors.append(exc)
                # TextIteratorStreamer only terminates when it receives its
                # end signal. If generation fails before emitting a token,
                # omitting this leaves the consumer blocked forever.
                streamer.on_finalized_text("", stream_end=True)
            finally:
                if torch.cuda.is_available(): torch.cuda.synchronize()
                timings["model_generate_seconds"] = time.perf_counter() - generation_started
        generation_started = time.perf_counter()
        thread = Thread(target=run, daemon=True); thread.start()
        chunks = []
        try:
            for piece in streamer:
                if first_token_seconds is None and piece:
                    first_token_seconds = time.perf_counter() - generation_started
                chunks.append(piece)
                if on_text:
                    callback_started = time.perf_counter()
                    try:
                        on_text(piece)
                    finally:
                        timings["text_callback_seconds"] += time.perf_counter() - callback_started
                        timings["text_callback_calls"] += 1
        finally:
            thread.join()
            if before_hook: before_hook.remove()
            if after_hook: after_hook.remove()
        if errors: raise errors[0]
        if torch.cuda.is_available(): torch.cuda.synchronize()
        self.last_generation_seconds = time.perf_counter() - generation_started
        self.last_first_token_seconds = first_token_seconds
        timings.update(generation_seconds=self.last_generation_seconds,
                       first_nonempty_text_seconds=first_token_seconds,
                       prompt_prepare_seconds=self.last_prompt_prepare_seconds,
                       forward_calls=forward_count,
                       constraint_processor_seconds=sum(p.seconds for p in measured_processors),
                       constraint_processor_calls=sum(p.calls for p in measured_processors),
                       constraint_processor_max_seconds=max((p.max_seconds for p in measured_processors), default=0.0),
                       throughput_tokens_per_second=timings["output_tokens"] / self.last_generation_seconds,
                       first_forward_includes_jit_and_cuda_initialization=True)
        self.last_generation_telemetry = timings
        if not hasattr(self, "generation_measurements"):
            self.generation_measurements = []
        self.generation_measurements.append(dict(timings))
        return "".join(chunks)

    def generate_partial(self, prompt, sampling, *, processor, plan, on_text=None):
        """Decode editable spans and batch-prefill immutable spans with one cache."""
        tokenizer, model = self.tokenizer, self.model
        processor.buffer_incomplete_tokens = True
        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
        ids, mask = inputs.input_ids, inputs.attention_mask
        prompt_length = ids.shape[1]
        cache, cached_length = None, 0
        output = ""
        config = GenerationConfig.from_model_config(model.config)
        config.update(do_sample=True, temperature=sampling.temperature,
                      top_p=sampling.top_p, top_k=sampling.top_k, min_p=sampling.min_p)
        config._eos_token_tensor = torch.tensor(eos_token_ids(tokenizer, model), device=ids.device)
        warpers = model._get_logits_processor(
            generation_config=config, input_ids_seq_length=prompt_length,
            encoder_input_ids=None, prefix_allowed_tokens_fn=None,
            logits_processor=LogitsProcessorList(), device=ids.device,
        )
        forward_parameters = inspect.signature(model.forward).parameters
        def publish():
            nonlocal output
            # A sampled token can end in an incomplete UTF-8 character.
            decoded = tokenizer.decode(ids[0, prompt_length:].tolist(), skip_special_tokens=True).rstrip("\ufffd")
            if not decoded.startswith(output):
                raise RuntimeError("部分生成的分词边界改变了已输出文本")
            piece = decoded[len(output):]
            output = decoded
            if piece and on_text:
                on_text(piece)

        with torch.inference_mode():
            while True:
                self._check_cancelled()
                processor.sync_output(output)
                if processor.constraints_started:
                    block = plan.take_fixed_block(processor)
                    if block:
                        token_ids = tokenizer.encode(block, add_special_tokens=False)
                        if tokenizer.decode(token_ids, skip_special_tokens=True) != block:
                            raise RuntimeError("固定段不能精确分词往返")
                        if ids.shape[1] - prompt_length + len(token_ids) > sampling.max_new_tokens:
                            raise ValueError("部分生成超出 max_new_tokens 预算")
                        ids = torch.cat((ids, ids.new_tensor([token_ids])), dim=1)
                        mask = torch.cat((mask, mask.new_ones((1, len(token_ids)))), dim=1)
                        publish()
                        processor.acknowledge_output(output)
                    if processor.controller.snapshot().is_finished:
                        return output
                if ids.shape[1] - prompt_length >= sampling.max_new_tokens:
                    raise ValueError("部分生成预算耗尽，正文未完成")
                positions = torch.arange(cached_length, ids.shape[1], device=ids.device)
                prepare_parameters = inspect.signature(model.prepare_inputs_for_generation).parameters
                prepare_kwargs = {
                    "past_key_values": cache,
                    "attention_mask": mask,
                    "use_cache": True,
                }
                if "next_sequence_length" in prepare_parameters:
                    # Transformers 5 replaced cache_position-based slicing with
                    # next_sequence_length in prepare_inputs_for_generation.
                    prepare_kwargs["next_sequence_length"] = len(positions)
                else:
                    prepare_kwargs["cache_position"] = positions
                prepared = model.prepare_inputs_for_generation(
                    ids, **prepare_kwargs,
                )
                if prepared.get("input_ids") is None or prepared["input_ids"].shape[1] != len(positions):
                    raise RuntimeError("模型无法按未缓存区间准备部分生成输入")
                if "logits_to_keep" in forward_parameters:
                    prepared["logits_to_keep"] = 1
                result = model(**prepared, return_dict=True)
                cache = result.past_key_values
                if cache is None:
                    raise RuntimeError("模型未返回部分生成所需的 KV cache")
                cached_length = ids.shape[1]
                scores = processor(ids, result.logits[:, -1, :].float())
                scores = warpers(ids, scores)
                probabilities = torch.softmax(scores, dim=-1)
                if not torch.isfinite(probabilities).all():
                    raise ValueError("部分生成没有合法的采样候选")
                token = torch.multinomial(probabilities, num_samples=1)
                if token.item() in eos_token_ids(tokenizer, model):
                    raise ValueError("部分生成提前结束，正文未完成")
                ids = torch.cat((ids, token), dim=1)
                mask = torch.cat((mask, mask.new_ones((1, 1))), dim=1)
                publish()
