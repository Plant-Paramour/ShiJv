from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from .contracts import GeneratePoemRequest, SamplingOptions
from .generation.engine import GenerationEngine
from .generation.model_runner import ModelRunner
from .generation.result import ModelSettings
from .tasks import TaskRequest


@dataclass(frozen=True)
class ModelConfig:
    model_name: str
    quantization: str = "8bit"


@dataclass(frozen=True)
class SamplingConfig:
    max_new_tokens: int = 4096
    temperature: float = 0.6
    top_p: float = 0.95
    top_k: int = 20
    min_p: float = 0.0


@dataclass(frozen=True)
class AppConfig:
    model: ModelConfig
    task: TaskRequest
    sampling: SamplingConfig = field(default_factory=SamplingConfig)
    use_constraints: bool = True
    num_generations: int = 1
    save_output: bool = True
    rhyme_dir: Path = Path("Rhyme")
    meter_source: Path = Path("Songci_Meter")
    output_dir: Path = Path("output")
    boundary_coherence_penalty: float = 50.0


def _runner(model_config: ModelConfig) -> ModelRunner:
    return ModelRunner(
        ModelSettings(
            model_name=model_config.model_name,
            quantization=model_config.quantization,
        )
    )


def _load_model(model_config: ModelConfig):
    """Compatibility helper retained for existing local callers."""
    runner = _runner(model_config)
    runner.load()
    return runner.tokenizer, runner.model


def _task_options(task: TaskRequest) -> dict:
    options = {"skip_planning": task.skip_planning}
    if task.meter_type == "汉俳":
        options.update(asdict(task.hanpai))
    elif task.meter_type == "唐诗":
        options.update(asdict(task.tang))
    elif task.meter_type == "排律":
        options.update(asdict(task.pailv))
    return options


def run(config: AppConfig) -> None:
    engine = GenerationEngine(
        _runner(config.model),
        rhyme_dir=config.rhyme_dir,
        meter_source=config.meter_source,
        boundary_coherence_penalty=config.boundary_coherence_penalty,
    )
    request = GeneratePoemRequest(
        meter_type=config.task.meter_type,
        form_name=config.task.form_name,
        theme=config.task.theme,
        rhyme_dict_name=config.task.rhyme_dict_name,
        requirement=config.task.requirement,
        task_type=config.task.task_type,
        use_thinking=config.task.use_thinking,
        cipai_data_path=config.task.cipai_data_path,
        num_lines=config.task.num_lines,
        strict_polyphonic=config.task.strict_polyphonic,
        candidate_count=config.num_generations,
        task_options=_task_options(config.task),
        sampling=SamplingOptions(**asdict(config.sampling)),
    )
    result = engine.generate_poem(request, use_constraints=config.use_constraints)
    output_file = config.output_dir / config.task.form_name / f"{config.task.form_name}.txt"
    if config.save_output:
        output_file.parent.mkdir(parents=True, exist_ok=True)
    for index, candidate in enumerate(result["candidates"], start=1):
        output = candidate["text"]
        print(f"\n=== [Generation {index}/{len(result['candidates'])}] ===")
        print(output)
        if config.save_output:
            with output_file.open("a", encoding="utf-8") as stream:
                stream.write(f"=== 作品 {index} ===\n")
                stream.write(output.strip())
                stream.write("\n\n")
    if config.save_output:
        print(f"已将作品存入 {output_file}")
