"""Strict release manifests; importing this module requires only the stdlib.

Expected hashes must ship with the application. This parser deliberately does
not fetch manifests or treat an arbitrary URL plus its hash as a trusted release.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
from urllib.parse import urlsplit


class ManifestError(ValueError):
    pass


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ManifestError(f"清单字段 {name} 必须为非空字符串")
    return value


def _identifier(value, name):
    value = _text(value, name)
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", value) or PureWindowsPath(value).is_reserved():
        raise ManifestError(f"清单字段 {name} 不是有效标识")
    return value


def _hash(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ManifestError(f"清单字段 {name} 必须为 SHA256")
    return value


def _strings(value, name):
    if not isinstance(value, list) or not value:
        raise ManifestError(f"清单字段 {name} 必须为非空数组")
    items = tuple(_text(item, name) for item in value)
    if len(set(items)) != len(items):
        raise ManifestError(f"清单字段 {name} 包含重复值")
    return items


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d+(?:\.\d+){1,3}", value):
        raise ManifestError(f"无效数字版本：{value!r}")
    parts = tuple(int(item) for item in value.split("."))
    return parts + (0,) * (4 - len(parts))


def relative_path(value):
    value = _text(value, "相对路径")
    path = PureWindowsPath(value)
    # ZIP paths are portable slash-separated names. Reject NTFS ADS, device
    # names, trailing dots/spaces and traversal, even when parsing on Linux.
    if ("\\" in value or path.drive or path.is_absolute()
            or any(char in '<>:"|?*' or ord(char) < 32 for char in value)
            or any(part in {"", ".", ".."} or part.endswith((".", " "))
                   or PureWindowsPath(part).is_reserved() for part in value.split("/"))):
        raise ManifestError(f"不安全的相对路径：{value}")
    return value


@dataclass(frozen=True)
class RuntimeManifest:
    schema_version: int
    runtime_id: str
    os: str
    arch: str
    python_version: str
    application_versions: tuple[str, ...]
    engine_revision: str
    torch_version: str
    cuda_build: str
    package_lock_sha256: str
    min_driver_version: str
    supported_compute_capabilities: tuple[str, ...]
    supported_models: tuple[str, ...]
    precisions: tuple[str, ...]
    artifact_url: str
    artifact_sha256: str
    download_bytes: int
    installed_bytes: int
    python_relative_path: str
    probe_command: tuple[str, ...]
    min_windows_build: int

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict):
            raise ManifestError("运行时清单必须为对象")
        required = set(cls.__dataclass_fields__)
        if set(data) != required:
            raise ManifestError(f"运行时清单字段不匹配：缺少 {sorted(required - set(data))}；未知 {sorted(set(data) - required)}")
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise ManifestError("不支持的清单格式版本")
        values = dict(data)
        values["runtime_id"] = _identifier(data["runtime_id"], "runtime_id")
        if data["os"] != "windows" or data["arch"] != "x64":
            raise ManifestError("本轮仅支持 Windows x64 运行时")
        for name in ("python_version", "min_driver_version", "cuda_build"):
            version_tuple(_text(data[name], name))
        _text(data["torch_version"], "torch_version")
        if not data["torch_version"].endswith("+cu" + data["cuda_build"].replace(".", "")):
            raise ManifestError("torch_version 必须明确并匹配 CUDA 构建后缀")
        for name in ("engine_revision", "package_lock_sha256", "artifact_sha256"):
            _hash(data[name], name)
        for name in ("application_versions", "supported_compute_capabilities", "supported_models", "precisions", "probe_command"):
            values[name] = _strings(data[name], name)
        for capability in values["supported_compute_capabilities"]:
            if not re.fullmatch(r"\d+\.\d+", capability):
                raise ManifestError("compute capability 必须形如 8.9")
        if not set(values["precisions"]) <= {"fp16", "bnb4bit"}:
            raise ManifestError("本轮精度仅支持 fp16、bnb4bit")
        for name in ("download_bytes", "installed_bytes", "min_windows_build"):
            if type(data[name]) is not int or data[name] <= 0:
                raise ManifestError(f"{name} 必须为正整数")
        try:
            url = urlsplit(_text(data["artifact_url"], "artifact_url"))
        except ValueError as exc:
            raise ManifestError("无效发行包 URL") from exc
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment or url.query:
            raise ManifestError("发行包必须来自无凭据、无查询参数的 HTTPS URL")
        values["python_relative_path"] = relative_path(data["python_relative_path"])
        if PureWindowsPath(values["python_relative_path"]).name.lower() != "python.exe":
            raise ManifestError("Windows 解释器路径必须指向 python.exe")
        # No shell fragments or arbitrary scripts from a downloaded manifest.
        if values["probe_command"] != ("-m", "apps.launcher.environment_probe", "--mode", "inference"):
            raise ManifestError("probe_command 必须使用应用内置推理检查入口")
        return cls(**values)


@dataclass(frozen=True)
class ModelManifest:
    model_id: str
    revision: str
    source_url: str
    license: str
    model_type: str
    runtime_id: str
    precisions: tuple[str, ...]
    files: dict[str, str]

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict) or set(data) != set(cls.__dataclass_fields__):
            raise ManifestError("模型清单字段不完整或包含未知字段")
        for name in ("model_id", "source_url", "license", "model_type"):
            _text(data[name], name)
        if not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", _text(data["revision"], "revision")):
            raise ManifestError("模型修订必须是固定提交摘要，不能使用 master/main")
        _identifier(data["runtime_id"], "runtime_id")
        if data["model_type"] not in {"qwen3", "qwen3_5"}:
            raise ManifestError("未实现的模型架构")
        precisions = _strings(data["precisions"], "precisions")
        if not set(precisions) <= {"fp16", "bnb4bit"}:
            raise ManifestError("无效模型精度")
        files = data["files"]
        if not isinstance(files, dict) or not files:
            raise ManifestError("模型清单必须列出完整文件摘要")
        for name, digest in files.items():
            relative_path(name)
            _hash(digest, name)
        if len({name.casefold() for name in files}) != len(files):
            raise ManifestError("模型文件名在 Windows 上发生冲突")
        if (not {"config.json", "tokenizer_config.json"} <= files.keys()
                or not ({"tokenizer.json", "tokenizer.model"} & files.keys())
                or not any(name.endswith(".safetensors") for name in files)):
            raise ManifestError("模型清单缺少配置、分词器或 safetensors 权重")
        return cls(**(data | {"precisions": precisions, "files": dict(files)}))

    def verify_directory(self, directory):
        from .model_catalog import validate_model
        root = validate_model(str(Path(directory).resolve()))
        config = json.loads((root / "config.json").read_text(encoding="utf-8"))
        if config.get("auto_map") or config.get("quantization_config") or config.get("model_type") != self.model_type:
            raise ManifestError("MODEL_UNSUPPORTED：模型架构、量化格式或自定义代码不符合官方清单")
        # Extra model inputs must not silently inherit an official result.
        actual = {p.relative_to(root).as_posix() for p in root.rglob("*")
                  if p.is_file() and ".git" not in p.relative_to(root).parts
                  and p.suffix in {".json", ".safetensors", ".bin", ".model", ".txt", ".jinja", ".py"}}
        if actual - self.files.keys():
            raise ManifestError(f"MODEL_UNSUPPORTED：模型包含未登记输入文件 {sorted(actual - self.files.keys())}")
        for name, digest in self.files.items():
            path = root / name
            if not path.resolve().is_relative_to(root) or not path.is_file() or sha256_file(path) != digest:
                raise ManifestError(f"MODEL_UNSUPPORTED：文件缺失或摘要不符：{name}")
        return root


@dataclass(frozen=True)
class RuntimeCatalog:
    runtimes: tuple[RuntimeManifest, ...]
    models: tuple[ModelManifest, ...]

    @classmethod
    def load(cls, path):
        """The caller must supply an application-bundled, trusted local file."""
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or set(data) != {"schema_version", "runtimes", "models"}:
            raise ManifestError("发行目录结构无效")
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise ManifestError("不支持的发行目录版本")
        if not isinstance(data["runtimes"], list) or not isinstance(data["models"], list):
            raise ManifestError("发行目录必须包含运行时及模型数组")
        runtimes = tuple(RuntimeManifest.parse(item) for item in data["runtimes"])
        models = tuple(ModelManifest.parse(item) for item in data["models"])
        by_id = {item.runtime_id: item for item in runtimes}
        if len(by_id) != len(runtimes) or len({m.model_id for m in models}) != len(models):
            raise ManifestError("发行目录包含重复标识")
        for model in models:
            runtime = by_id.get(model.runtime_id)
            if (runtime is None or model.model_id not in runtime.supported_models
                    or not set(model.precisions) <= set(runtime.precisions)):
                raise ManifestError("模型与运行时绑定不一致")
        for runtime in runtimes:
            if set(runtime.supported_models) != {m.model_id for m in models if m.runtime_id == runtime.runtime_id}:
                raise ManifestError("运行时包含未登记模型")
        return cls(runtimes, models)
