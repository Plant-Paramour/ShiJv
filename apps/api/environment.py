"""Inspect a chosen inference environment without importing its libraries here."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

_cache = {}
_lock = threading.Lock()


def python_process_env(python):
    env = os.environ.copy()
    prefix = Path(python).parent
    env["PATH"] = os.pathsep.join(map(str, [prefix, prefix / "Library" / "bin", prefix / "Scripts"])) + os.pathsep + env.get("PATH", "")
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    env["PYTHONIOENCODING"] = "utf-8"
    deployment = prefix / 'shiju-install.json'
    if deployment.is_file():
        state = json.loads(deployment.read_text(encoding='utf-8'))
        if state.get('state') == 'verified' and state.get('ok'):
            env['CUDA_VISIBLE_DEVICES'] = state['gpu_uuid']
            backend = prefix / 'Lib/site-packages/triton/backends/nvidia'
            env['CC'] = str(prefix / 'Lib/site-packages/triton/runtime/tcc/tcc.exe')
            env['CUDA_PATH'] = str(backend)
            env['TRITON_PTXAS_PATH'] = str(backend / 'bin/ptxas.exe')
    return env


def selected_environment(directory):
    path = Path(directory) / "launcher.json"
    config = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    return {"application_python": sys.executable,
            "inference_python": config.get("inference_python") or os.getenv("SHIJU_INFERENCE_PYTHON", sys.executable),
            "local_enabled": config.get("local_enabled", True)}


def inspect_environment(directory, *, refresh=False):
    selected = selected_environment(directory)
    if not selected["local_enabled"]:
        return selected | {"ok": False, "errors": ["请在启动器中勾选本地诗词生成，并选择推理环境后重新启动软件"], "quantization": []}
    python = Path(selected["inference_python"])
    if not python.is_absolute() or not python.is_file() or python.name.lower() not in {"python.exe", "python"}:
        return selected | {"ok": False, "errors": ["所选推理环境的 Python 不存在，请在启动器中重新选择"], "quantization": []}
    key = (str(python), python.stat().st_mtime_ns)
    with _lock:
        if not refresh and key in _cache and time.monotonic() - _cache[key][0] < 60:
            return selected | _cache[key][1]
        script = Path(__file__).resolve().parents[1] / "launcher" / "environment_probe.py"
        try:
            deployment = python.parent / 'shiju-install.json'
            if deployment.is_file():
                state = json.loads(deployment.read_text(encoding='utf-8'))
                if state.get('state') != 'verified' or not state.get('ok'):
                    raise ValueError('诗矩专用环境尚未完成验证，请返回部署工具重试')
                from apps.launcher.runtime_manifest import sha256_file
                integrity = json.loads((python.parent / 'verified-inputs.json').read_text(encoding='utf-8'))
                if not isinstance(integrity, dict) or not integrity:
                    raise ValueError('专用环境缺少验证指纹，请重新验证')
                for filename, digest in integrity.items():
                    if not Path(filename).is_file() or sha256_file(filename) != digest:
                        raise ValueError('模型、依赖或生成代码已变化，需要重新验证：' + Path(filename).name)
            with tempfile.TemporaryDirectory(prefix="shiju-probe-") as temp:
                output = Path(temp) / "result.json"
                result = subprocess.run([str(python), str(script), "--mode", "inference", "--output", str(output)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90,
                    env=python_process_env(python),
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                report = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {
                    "ok": False, "errors": [f"环境检查进程退出，退出码 {result.returncode}，请检查 Python 或 DLL 依赖"]}
        except subprocess.TimeoutExpired:
            report = {"ok": False, "errors": ["环境检查超过 90 秒，请检查虚拟环境是否能正常运行"]}
        except (OSError, ValueError) as exc:
            report = {"ok": False, "errors": [f"环境检查失败：{exc}"]}
        _cache[key] = (time.monotonic(), report)
        return selected | report


def assess_model(model, environment):
    """Conservative support scope. No guessed options are exposed to users."""
    if not environment.get("ok"):
        raise ValueError("；".join(environment.get("errors") or ["推理环境检查未通过"]))
    path = Path(model)
    configuration = json.loads((path / "config.json").read_text(encoding="utf-8"))
    if configuration.get("quantization_config"):
        raise ValueError("当前版本不接收预量化模型（如 AWQ、GPTQ、预量化 BNB），请使用原始浮点权重")
    if configuration.get("auto_map"):
        raise ValueError("当前版本只支持 Transformers 内置实现，不支持自定义模型代码")
    model_type = configuration.get("model_type")
    if model_type not in environment.get("supported_model_types", []):
        detail = environment.get("model_errors", {}).get(model_type, "程序尚未实现此架构的加载适配")
        raise ValueError(f"所选推理环境无法加载架构 {model_type or '未知'}，不在支持范围：{detail}")
    # Use index metadata when available; otherwise the actual weight files.
    indexes = list(path.glob("*.safetensors.index.json")) + list(path.glob("pytorch_model*.index.json"))
    size = max((json.loads(p.read_text(encoding="utf-8")).get("metadata", {}).get("total_size", 0) for p in indexes), default=0)
    if not size:
        size = sum(p.stat().st_size for p in path.glob("*.safetensors")) or sum(p.stat().st_size for p in path.glob("pytorch_model*.bin"))
    free = environment.get("gpu", {}).get("free_bytes", 0)
    reserve = 1536 * 1024**2
    options = environment.get("quantization", [])
    if "none" in options and size * 1.3 + reserve < free:
        precision = "none"
    elif "4bit" in options and size * .35 + reserve < free:
        precision = "4bit"
    else:
        extra = environment.get("quantization_note", "")
        raise ValueError(f"当前可用显存与依赖无法提供合适的运行方案。请关闭其他占用显存的程序或选择更小模型。{extra}")
    return {"quantization": precision, "label": "自动选择 FP16" if precision == "none" else "自动选择 4bit",
            "estimated": True, "note": "显存估算仅用于选择方案；必须通过实际加载和短推理后才会就绪"}
