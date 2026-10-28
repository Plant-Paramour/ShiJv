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
    from apps.launcher.process_environment import python_environment
    return python_environment(python)


def selected_environment(directory):
    path = Path(directory) / "launcher.json"
    config = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    return {"application_python": sys.executable,
            "inference_python": config.get("inference_python") or os.getenv("SHIJU_INFERENCE_PYTHON", sys.executable),
            "local_enabled": config.get("local_enabled", True)}


def check_managed_runtime(python):
    """Check deployment runtime inputs; a model is validated as a separate pair."""
    python = Path(python)
    prefix = python.parent.parent if python.parent.name.lower() == 'scripts' else python.parent
    deployment = prefix / 'shiju-install.json'
    if not deployment.is_file():
        return
    state = json.loads(deployment.read_text(encoding='utf-8'))
    if state.get('state') != 'verified' or not state.get('ok'):
        raise ValueError('诗矩专用环境尚未完成验证，请返回部署工具重试')
    from apps.launcher.runtime_manifest import sha256_file
    integrity = json.loads((prefix / 'verified-inputs.json').read_text(encoding='utf-8'))
    if not isinstance(integrity, dict) or not integrity:
        raise ValueError('专用环境缺少验证指纹，请通过部署工具重新验证')
    from pathlib import PureWindowsPath
    portable = integrity.get('schema_version') == 2
    files = integrity.get('files') if portable else integrity
    if not isinstance(files, dict) or not files:
        raise ValueError('专用环境缺少验证指纹，请通过部署工具重新验证')
    # Legacy deployments stored sandbox/host absolute names. Relocate only
    # entries beneath the interpreter recorded in that same installation.
    old_python = state.get('python')
    path_type = PureWindowsPath if old_python and PureWindowsPath(old_python).drive else Path
    old_prefix = path_type(old_python).parent if old_python else prefix
    if old_prefix.name.lower() == 'scripts':
        old_prefix = old_prefix.parent
    original_model = path_type(state['model']) if state.get('model') else None
    for filename, digest in files.items():
        recorded = path_type(filename)
        # A deployment's original model may have been replaced by another fully
        # tested pair. Its files are not dependencies of the Python environment.
        if not portable and original_model and recorded.is_relative_to(original_model):
            continue
        if portable:
            from apps.launcher.runtime_manifest import relative_path
            relative_path(filename)
            path = prefix / filename
        else:
            try:
                path = prefix.joinpath(*recorded.relative_to(old_prefix).parts)
            except ValueError as exc:
                raise ValueError('部署校验记录包含环境目录之外的文件，请通过部署工具重新验证') from exc
        if not path.resolve().is_relative_to(prefix.resolve()):
            raise ValueError('部署校验文件超出所选环境目录，请通过部署工具重新验证')
        if not path.is_file() or sha256_file(path) != digest:
            raise ValueError('部署环境文件缺失或已变化：' + str(path) + '。请通过部署工具修复；本次模型验收未通过。')


def inspect_environment(directory, *, refresh=False, cancel=None):
    selected = selected_environment(directory)
    def rejected(message):
        from apps.launcher.diagnostics import OperationDiagnostic
        diagnostic = OperationDiagnostic(directory, 'environment-selection')
        diagnostic.error_code = 'ENVIRONMENT_SELECTION_INVALID'
        diagnostic.event('failed', error=message, inference_python=selected['inference_python'])
        result = selected | dict(ok=False, errors=[diagnostic.describe(message)], quantization=[], diagnostics=diagnostic.metadata)
        diagnostic.save(dict(completed=True, **result))
        diagnostic.close()
        return result
    if not selected["local_enabled"]:
        return rejected('请在启动器中勾选本地诗词生成，并选择推理环境后重新启动软件')
    python = Path(selected["inference_python"])
    if not python.is_absolute() or not python.is_file() or python.name.lower() not in {"python.exe", "python"}:
        return rejected(f'所选推理环境的 Python 路径无效：{python}。请在启动器中重新选择该环境实际存在的 python.exe')
    key = (str(python), python.stat().st_mtime_ns)
    with _lock:
        if not refresh and key in _cache and time.monotonic() - _cache[key][0] < 60:
            return selected | _cache[key][1]
        script = Path(__file__).resolve().parents[1] / "launcher" / "environment_probe.py"
        try:
            from apps.launcher.diagnostics import OperationDiagnostic
            diagnostic = OperationDiagnostic(directory, 'environment-check')
            try:
                check_managed_runtime(python)
                if diagnostic.root is None:
                    raise OSError(diagnostic.describe('无法创建检查报告'))
                output = diagnostic.root / 'probe-result.json'
                env = python_process_env(python)
                env['SHIJU_DIAGNOSTIC_ID'] = diagnostic.id
                env['SHIJU_DATA_DIR'] = str(directory)
                code = diagnostic.run([str(python), '-I', str(script), '--mode', 'inference', '--output', str(output)],
                                      env=env, timeout=600, cancel=cancel)
                report = json.loads(output.read_text(encoding='utf-8')) if output.exists() else {
                    'ok': False, 'errors': [f'环境检查未生成报告（退出码 {code}）']}
                if code and report.get('ok'):
                    report.update(ok=False, errors=[f'环境检查异常退出（退出码 {code}）'])
                if not report.get('ok'):
                    diagnostic.error_code = 'ENVIRONMENT_CHECK_FAILED'
                    report['errors'] = [diagnostic.describe(error) for error in report.get('errors', ['环境检查失败'])]
                diagnostic.event('completed', ok=report.get('ok'), exit_code=code)
            except Exception as exc:
                code = ('ENVIRONMENT_CHECK_TIMEOUT' if isinstance(exc, subprocess.TimeoutExpired) else
                        'CANCELLED' if cancel is not None and cancel.is_set() else 'ENVIRONMENT_CHECK_FAILED')
                report = dict(ok=False, errors=[diagnostic.fail(exc, code)])
            finally:
                report['diagnostics'] = diagnostic.metadata
                diagnostic.save(dict(completed=True, **report))
                diagnostic.close()
        except subprocess.TimeoutExpired:
            report = {"ok": False, "errors": ["环境检查超过 10 分钟，已停止检查。请确认所选环境可以正常运行后重试"]}
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
    reserve = 1536 * 1024**2
    options = environment.get("quantization", [])
    if environment.get("cuda") is False:
        free = environment.get("cpu_memory_available_bytes") or 0
        required = size * 2.6 + reserve
        if "none" in options and required < free:
            return {"quantization": "none", "label": "CPU FP32（原始权重）",
                    "estimated": True, "note": "CPU 内存估算仅供参考；必须通过实际加载和短推理后才会就绪"}
        raise ValueError(
            f"系统可用内存不足，暂时无法加载模型。当前可用 {free / 1024**3:.2f} GiB，"
            f"预计需要至少 {required / 1024**3:.2f} GiB（含运行余量，并非实测峰值）。"
            "请释放内存后重试，或选择占用内存更少的模型。"
        )
    free = environment.get("gpu", {}).get("free_bytes", 0)
    if "none" in options and size * 1.3 + reserve < free:
        precision = "none"
    elif "4bit" in options and size * .35 + reserve < free:
        precision = "4bit"
    else:
        extra = environment.get("quantization_note", "")
        raise ValueError(f"当前可用显存与依赖无法提供合适的运行方案。请关闭其他占用显存的程序或选择更小模型。{extra}")
    return {"quantization": precision, "label": "自动选择 FP16" if precision == "none" else "自动选择 4bit",
            "estimated": True, "note": "显存估算仅用于选择方案；必须通过实际加载和短推理后才会就绪"}
