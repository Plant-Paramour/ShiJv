"""安装器无界面编排器：把安装请求转换为部署核心动作并输出 JSONL 事件。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

try:
    from tools.installer_protocol import InstallEvent, InstallRequest, read_request, write_result
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.installer_protocol import InstallEvent, InstallRequest, read_request, write_result


def classify_failure(message: str) -> tuple[str, str]:
    """Return a stable error code and user-facing explanation."""
    if "sys.path leaks outside runtime" in message:
        return ("ENVIRONMENT_NOT_ISOLATED", "本地 Python 环境引用了环境目录之外的路径，不能安全导入；请选择补齐依赖或重新部署环境。")
    if "CUDA" in message or "GPU" in message:
        return ("GPU_UNAVAILABLE", message)
    return ("DEPLOYMENT_FAILED", message)


def run(request_path: Path, events_path: Path, result_path: Path, data_dir: Path) -> int:
    request = read_request(request_path)
    events_path.unlink(missing_ok=True)
    report = data_dir / "installer-report.json"
    plan = data_dir / "installer-plan.json"
    cache = data_dir / "cache"
    data_dir.mkdir(parents=True, exist_ok=True)

    def event(stage: str, state: str, message: str, **kwargs):
        InstallEvent(stage, state, message, **kwargs).write(events_path)

    common = [sys.executable, "-m", "apps.launcher.deployer", "--data-dir", str(data_dir),
              "--report", str(report), "--plan", str(plan), "--target", request.runtime_path,
              "--cache", str(cache), "--profile", request.profile]
    if request.model_source == "local":
        common += ["--route", "local", "--model", request.model_path]
    else:
        common += ["--route", "official", "--model-id", request.model_id]

    try:
        if request.runtime_source == "local":
            event("inspect_environment", "running", "正在检查本地运行环境")
            _inspect_local_runtime(request, report, data_dir)
            event("inspect_environment", "completed", "本地运行环境检查完成", progress=20)
        else:
            event("prepare_environment", "running", "正在准备新的运行环境")

        event("prepare_model", "running", "正在检查模型来源", progress=25)
        _run(common + ["preview"], report)
        event("prepare_model", "completed", "模型方案检查完成", progress=35)
        event("install", "running", "正在安装运行环境和模型", progress=40)
        _run(common + ["install", "--continue-existing"], report)
        event("verify", "completed", "环境和模型验证通过", progress=100)
        write_result(result_path, ok=True, message="安装完成", report=str(report), plan=str(plan))
        return 0
    except subprocess.CalledProcessError:
        details = {}
        if report.is_file():
            try:
                details = json.loads(report.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        message = details.get("error") or details.get("verification_error") or "部署阶段失败，请查看诊断报告。"
        raw = str(message)
        error_code, classified_message = classify_failure(raw)
        if error_code == "ENVIRONMENT_NOT_ISOLATED":
            message = classified_message
        event("failed", "failed", str(message), error_code=error_code, details=str(details), can_retry=True,
              can_fallback_to_cpu="GPU" in raw or "CUDA" in raw)
        write_result(result_path, ok=False, message=str(message), report=str(report), details=details)
        return 1


def _inspect_local_runtime(request: InstallRequest, report: Path, data_dir: Path) -> None:
    """Inspect an arbitrary imported Python environment, not a managed target."""
    runtime = Path(request.runtime_path)
    python = runtime / "python.exe"
    if not python.is_file():
        raise subprocess.CalledProcessError(1, [str(python), "probe_managed_candidate.py"])
    if request.model_source != "local":
        command = [str(python), "-I", "-c", "import torch, transformers; print(torch.__version__)" ]
    else:
        probe = Path(__file__).resolve().parent / "probe_managed_candidate.py"
        probe_report = data_dir / "local-environment-probe.json"
        command = [str(python), str(probe), "--output", str(probe_report), "--model", request.model_path,
                   "--device", "cpu", "--precision", "4bit"]
    process = subprocess.run(command, text=True, capture_output=True)
    if process.returncode:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(process.stdout[-65536:] or process.stderr[-65536:], encoding="utf-8")
        raise subprocess.CalledProcessError(process.returncode, command)


def _run(command: list[str], report: Path) -> None:
    process = subprocess.run(command, text=True, capture_output=True)
    if process.returncode:
        if process.stdout:
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(process.stdout[-65536:], encoding="utf-8")
        raise subprocess.CalledProcessError(process.returncode, command)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.request, args.events, args.result, args.data_dir))
