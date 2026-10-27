"""Shared model file validation, scanning and compatibility reports (stdlib only)."""
import hashlib
import json
import os
import sys
from pathlib import Path


def fingerprint(model, python=None):
    root = Path(model).resolve()
    interpreter = Path(python or sys.executable).resolve()
    files = [interpreter, *root.glob('*.json'), *root.glob('*.safetensors'), *root.glob('*.bin'), *root.glob('*.model')]
    files.extend(interpreter.parent.glob('python*._pth'))
    custom = interpreter.parent / 'Lib/site-packages/sitecustomize.py'
    if custom.is_file():
        files.append(custom)
    packages = interpreter.parent / 'Lib' / 'site-packages'
    for name in ('transformers', 'torch', 'accelerate', 'bitsandbytes', 'tokenizers'):
        files.extend(packages.glob(name + '-*.dist-info/METADATA'))
    # Changes to the decoding implementation invalidate earlier acceptance.
    project = Path(__file__).resolve().parents[2]
    entries = [(os.path.normcase(str(p.resolve())), p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(set(files)) if p.is_file()]
    # An identical deployment copy is the same code, regardless of its path or
    # copy timestamps. Actual source edits still invalidate acceptance.
    for folder in ('shiju', 'apps/launcher', 'apps/gpu_worker'):
        for path in sorted((project / folder).rglob('*.py')):
            entries.append((path.relative_to(project).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
    return hashlib.sha256(json.dumps(entries).encode()).hexdigest()


def report_path(directory, model, python=None):
    return Path(directory) / 'model-checks' / (fingerprint(model, python) + '.json')


def verified_report(directory, model, python=None):
    try:
        return json.loads(report_path(directory, model, python).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def scan(roots):
    found = []
    for base in map(Path, roots):
        if not base.is_dir():
            continue
        for parent, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in {'.git', 'node_modules', '__pycache__'}][:100]
            if len(Path(parent).relative_to(base).parts) >= 4:
                dirs[:] = []
            if 'config.json' in files:
                found.append(str(Path(parent).resolve()))
            if len(found) >= 100:
                return list(dict.fromkeys(found))
    return list(dict.fromkeys(found))

def validate_model(path: str) -> Path:
    model = Path(path)
    if not model.is_absolute() or not (model / "config.json").is_file():
        raise ValueError("请选择含 config.json 的本地模型绝对路径")
    configuration = json.loads((model / "config.json").read_text(encoding="utf-8"))
    if not isinstance(configuration, dict):
        raise ValueError("模型 config.json 必须为对象")
    architectures = configuration.get("architectures", [])
    if not isinstance(architectures, list) or any(not isinstance(name, str) for name in architectures):
        raise ValueError("模型 architectures 字段无效")
    if architectures and not any("CausalLM" in name or "ConditionalGeneration" in name for name in architectures):
        raise ValueError("该模型不是可用于诗词生成的文本生成模型")
    if not ((model / "tokenizer.json").is_file() or (model / "tokenizer.model").is_file()):
        raise ValueError("模型目录缺少 tokenizer.json 或 tokenizer.model")
    indexes = list(model.glob("*.index.json"))
    weights = list(model.glob("*.safetensors")) + list(model.glob("pytorch_model*.bin"))
    if not weights:
        raise ValueError("模型目录缺少权重文件，请先下载完整模型")
    for index in indexes:
        data = json.loads(index.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("weight_map", {}), dict):
            raise ValueError(f"模型权重索引无效：{index.name}")
        shards = data.get("weight_map", {}).values()
        if any(not isinstance(shard, str) for shard in shards):
            raise ValueError(f"模型权重分片路径无效：{index.name}")
        for shard in set(shards):
            from .runtime_manifest import relative_path
            relative_path(shard)
            if not (model / shard).resolve().is_relative_to(model.resolve()):
                raise ValueError(f"模型权重分片超出模型目录：{shard}")
            if not (model / shard).is_file():
                raise ValueError(f"模型权重不完整，缺少 {shard}")
    return model.resolve()

