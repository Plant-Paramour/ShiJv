"""Host inspection and exact release matching (T3, stdlib only).

Installation is intentionally gated on the independent distribution validation
in T1/T2. No development interpreter is registered as a released runtime.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess

from .runtime_manifest import ManifestError, RuntimeCatalog, RuntimeManifest, version_tuple


class RuntimeProblem(ValueError):
    def __init__(self, code, message, **details):
        self.code, self.details = code, details
        super().__init__(message)

    def report(self):
        return {"ok": False, "code": self.code, "message": str(self), "details": self.details}


@dataclass(frozen=True)
class GPU:
    index: int
    uuid: str
    name: str
    driver_version: str
    memory_bytes: int
    compute_capability: str | None


@dataclass(frozen=True)
class HostReport:
    os: str
    arch: str
    windows_build: int
    data_directory: str
    free_bytes: int
    gpus: tuple[GPU, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True)
class RuntimePlan:
    runtime: RuntimeManifest
    model_id: str
    model_directory: str
    device: GPU
    precisions: tuple[str, ...]
    required_bytes: int
    data_directory: str

    @property
    def process_env(self):
        # A UUID remains stable across enumeration changes. CUDA sees only this
        # device as logical device 0 in both the probe and the future Worker.
        return {"CUDA_VISIBLE_DEVICES": self.device.uuid, "CUDA_DEVICE_ORDER": "PCI_BUS_ID"}


def _nvidia_smi():
    command = shutil.which("nvidia-smi")
    if command:
        return command
    for path in (Path(os.getenv("SystemRoot", r"C:\Windows")) / "System32/nvidia-smi.exe",
                 Path(os.getenv("ProgramW6432", r"C:\Program Files")) / "NVIDIA Corporation/NVSMI/nvidia-smi.exe"):
        if path.is_file():
            return str(path)
    return None


def _query(command, capability=True):
    fields = "index,uuid,name,driver_version,memory.total" + (",compute_cap" if capability else "")
    return subprocess.run([command, "--query-gpu=" + fields, "--format=csv,noheader,nounits"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=10, stdin=subprocess.DEVNULL,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def _parse_gpus(output, capability=True):
    gpus, errors, identities, indexes = [], [], set(), set()
    for number, row in enumerate(csv.reader(io.StringIO(output), skipinitialspace=True), 1):
        if not row:
            continue
        row = [value.strip() for value in row]
        try:
            if len(row) != (6 if capability else 5):
                raise ValueError("字段数量不符")
            index, uuid, name, driver, memory = row[:5]
            if not index.isdigit() or not uuid.startswith("GPU-") or not name or name == "N/A":
                raise ValueError("设备标识或名称缺失")
            version_tuple(driver)
            memory_bytes = int(float(memory) * 1024**2)
            if memory_bytes <= 0:
                raise ValueError("显存信息缺失")
            compute = row[5] if capability else None
            if compute:
                import re
                if not re.fullmatch(r"\d+\.\d+", compute):
                    compute = None
            if uuid in identities or int(index) in indexes:
                raise ValueError("重复设备标识")
            identities.add(uuid)
            indexes.add(int(index))
            gpus.append(GPU(int(index), uuid, name, driver, memory_bytes, compute))
        except (ValueError, OverflowError) as exc:
            errors.append(f"GPU 第 {number} 行检测信息无效：{exc}")
    return tuple(gpus), errors


def inspect_host(directory) -> HostReport:
    """No torch, inference Python, Toolkit or API service is needed."""
    root = Path(directory).expanduser().resolve()
    errors = []
    existing = root
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    try:
        free = shutil.disk_usage(existing).free
    except OSError:
        free = 0
        errors.append("无法读取安装磁盘可用空间，请检查数据目录权限")
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = "x64" if machine in {"amd64", "x86_64"} else machine
    build = 0
    if system == "windows":
        try:
            build = int(platform.version().split(".")[-1])
        except ValueError:
            errors.append("无法读取 Windows 内部版本")
    gpus = ()
    command = _nvidia_smi()
    if not command:
        errors.append("未找到 nvidia-smi，请安装受支持的 NVIDIA 显卡驱动；不需要 CUDA Toolkit")
    else:
        try:
            result = _query(command)
            capability = True
            if result.returncode:
                # Older drivers may not recognize compute_cap. Preserve the
                # device report, but never guess its capability from its name.
                result = _query(command, capability=False)
                capability = False
            if result.returncode:
                errors.append(f"NVIDIA 驱动检测失败（退出码 {result.returncode}），请检查驱动")
            else:
                gpus, parse_errors = _parse_gpus(result.stdout, capability)
                errors.extend(parse_errors)
                if not gpus:
                    errors.append("未检测到可用的 NVIDIA GPU")
                elif any(gpu.compute_capability is None for gpu in gpus):
                    errors.append("部分 GPU 缺少计算能力字段，无法确认其是否符合发行清单")
        except subprocess.TimeoutExpired:
            errors.append("NVIDIA 驱动检测超过 10 秒，请检查驱动或重试")
        except OSError:
            errors.append("无法启动 NVIDIA 驱动检测工具，请检查驱动安装")
    return HostReport(system, arch, build, str(root), free, gpus, tuple(errors))


class RuntimeManager:
    def __init__(self, directory, catalog: RuntimeCatalog, *, application_version, engine_revision):
        self.directory = Path(directory).expanduser().resolve()
        self.catalog = catalog
        self.application_version = application_version
        self.engine_revision = engine_revision

    def inspect_host(self):
        return inspect_host(self.directory)

    def resolve_runtime(self, model, host=None, *, device_uuid=None):
        if not self.catalog.runtimes:
            raise RuntimeProblem("RUNTIME_UNAVAILABLE", "尚无经过 T1/T2 验收的官方运行时，暂不能安装；可继续使用高级自备环境")
        host = host or self.inspect_host()
        if Path(host.data_directory).resolve() != self.directory:
            raise RuntimeProblem("HOST_REPORT_INVALID", "宿主检测的数据目录与安装目录不一致，请重新检测")
        compatible = [r for r in self.catalog.runtimes if r.os == host.os and r.arch == host.arch
                      and self.application_version in r.application_versions and r.engine_revision == self.engine_revision
                      and host.windows_build >= r.min_windows_build]
        if not compatible:
            raise RuntimeProblem("RUNTIME_UNAVAILABLE", "没有匹配当前系统、应用和生成引擎版本的官方运行时")
        root = Path(model).expanduser().resolve()
        try:
            config = json.loads((root / "config.json").read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise RuntimeProblem("MODEL_UNSUPPORTED", "请选择含完整 config.json 的本地模型目录") from exc
        model_types = {m.model_type for m in self.catalog.models}
        if not isinstance(config, dict) or config.get("model_type") not in model_types or config.get("auto_map") or config.get("quantization_config"):
            raise RuntimeProblem("MODEL_UNSUPPORTED", "模型架构或格式不符合官方清单；请导入原始完整权重",
                                 supported_models=[m.model_id for m in self.catalog.models])
        candidates = [(r, m) for r in compatible for m in self.catalog.models
                      if m.runtime_id == r.runtime_id and m.model_type == config["model_type"]]
        # Check cheap host constraints before hashing several GB of weights.
        if not host.gpus:
            raise RuntimeProblem("GPU_UNSUPPORTED", "未检测到受支持的 NVIDIA GPU；作品管理仍可使用", diagnostics=host.errors)
        if device_uuid is None:
            gpu = min(host.gpus, key=lambda item: item.index)
        else:
            gpu = next((g for g in host.gpus if g.uuid == device_uuid), None)
            if gpu is None:
                raise RuntimeProblem("GPU_UNSUPPORTED", "指定 GPU 已不可用，请重新选择设备")
        hardware = [(r, m) for r, m in candidates if gpu.compute_capability in r.supported_compute_capabilities]
        if not hardware:
            raise RuntimeProblem("GPU_UNSUPPORTED", f"{gpu.name}（计算能力 {gpu.compute_capability or '未知'}）未在实测支持范围",
                                 gpu=asdict(gpu), supported=sorted({c for r, _ in candidates for c in r.supported_compute_capabilities}))
        driver = [(r, m) for r, m in hardware if version_tuple(gpu.driver_version) >= version_tuple(r.min_driver_version)]
        if not driver:
            raise RuntimeProblem("DRIVER_TOO_OLD", f"检测到驱动 {gpu.driver_version}，请升级 NVIDIA 驱动后再安装",
                                 detected=gpu.driver_version, required=sorted({r.min_driver_version for r, _ in hardware}),
                                 driver_url="https://www.nvidia.com/Download/index.aspx")
        matched = []
        for runtime, entry in driver:
            try:
                entry.verify_directory(root)
            except (ManifestError, ValueError, OSError):
                continue
            matched.append((runtime, entry))
        if not matched:
            raise RuntimeProblem("MODEL_UNSUPPORTED", "模型缺少文件或内容与官方修订不符；本地其他 Qwen3.5 模型需要独立验收",
                                 supported_models=[m.model_id for _, m in driver])
        if len(matched) != 1:
            raise RuntimeProblem("RUNTIME_AMBIGUOUS", "发行清单存在多个匹配项，无法选择唯一运行时，请检查发行配置")
        runtime, entry = matched[0]
        required = runtime.download_bytes + runtime.installed_bytes + 64 * 1024**2
        if host.free_bytes < required:
            raise RuntimeProblem("DISK_SPACE_LOW", "安装空间不足，请释放空间或选择其他数据目录", required_bytes=required,
                                 free_bytes=host.free_bytes, missing_bytes=required - host.free_bytes, directory=str(self.directory))
        return RuntimePlan(runtime, entry.model_id, str(root), gpu, entry.precisions, required, str(self.directory))


def main():
    parser = argparse.ArgumentParser(description="托管运行时宿主检测与发行清单匹配（不执行安装）")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--catalog")
    parser.add_argument("--model")
    parser.add_argument("--application-version")
    parser.add_argument("--engine-revision")
    parser.add_argument("--device-uuid")
    args = parser.parse_args()
    if args.model and not all((args.catalog, args.application_version, args.engine_revision)):
        parser.error("模型匹配需要内置清单、应用版本及生成引擎摘要")
    host = inspect_host(args.data_dir)
    result = {"ok": True, "host": asdict(host), "scope": "host_inspection_only"}
    try:
        if args.model:
            manager = RuntimeManager(args.data_dir, RuntimeCatalog.load(args.catalog),
                                     application_version=args.application_version, engine_revision=args.engine_revision)
            plan = manager.resolve_runtime(args.model, host, device_uuid=args.device_uuid)
            result = {"ok": True, "host": asdict(host), "plan": asdict(plan), "process_env": plan.process_env}
    except RuntimeProblem as exc:
        result = exc.report() | {"host": asdict(host)}
    except (ManifestError, ValueError, OSError):
        result = {"ok": False, "code": "MANIFEST_INVALID", "message": "发行清单无法读取或校验未通过", "host": asdict(host)}
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Single-command report, never launcher.json or the user's database/key.
    from uuid import uuid4
    temporary = output.with_name(output.name + "." + uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
