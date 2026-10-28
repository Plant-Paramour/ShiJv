"""Load before advertising readiness; used by the single player UI."""
import logging
import os
import json
import uuid
from pathlib import Path


def prepare_model(runner, engine, directory, model, checks, report, check_report):
    from apps.launcher.model_catalog import verified_report
    from apps.launcher.model_probe import check_runner
    saved = verified_report(directory, model)
    if saved and saved.get('ok') and saved.get('plan', {}).get('quantization') == os.environ.get('SHIJU_QUANTIZATION'):
        checks[:] = saved.get('checks', [])
        report('loading', '已读取环境—模型验收记录，正在加载权重；无需重复生成测试诗词')
        runner.load()
    elif os.environ.get('SHIJU_ALLOW_UNVERIFIED') == '1':
        checks[:] = [dict(id='actual_generation', name='网页实际生成', state='pending', detail='未验证；成功完成一次实际生成后自动记录')]
        report('loading', '已选择忽略验收，正在加载权重；此配对尚未验证')
        runner.load()
    else:
        check_runner(runner, engine, check_report)


def main():
    logging.basicConfig(level=logging.INFO)
    from apps.local_network import local_control_url
    control_url = local_control_url(os.environ['SHIJU_CONTROL_URL'])
    from shiju.generation.model_runner import ModelRunner
    from shiju.generation.result import ModelSettings
    from shiju.generation.engine import GenerationEngine
    from shiju.service.generation_service import GenerationService
    from .api_client import WorkerApiClient
    from .worker import GpuWorker

    ready = Path(os.environ["SHIJU_READY_FILE"])
    status = Path(os.environ["SHIJU_WORKER_STATUS_FILE"])
    checks = []
    def report(phase, message):
        logging.getLogger(__name__).info('生成器阶段：%s', phase)
        temporary = status.with_suffix(".tmp")
        temporary.write_text(json.dumps({"phase": phase, "message": message, "checks": checks}, ensure_ascii=False), encoding="utf-8")
        temporary.replace(status)
    def check_report(key, name, state, detail):
        checks[:] = [c for c in checks if c['id'] != key] + [dict(id=key, name=name, state=state, detail=detail)]
        report("testing", f"{name}：{detail}")
    ready.unlink(missing_ok=True)
    report("loading", "正在加载模型权重；尚未确认可用")
    runner = ModelRunner(ModelSettings(os.environ["SHIJU_MODEL_PATH"],
                         quantization=os.environ["SHIJU_QUANTIZATION"], local_files_only=True))
    engine = GenerationEngine(runner, rhyme_dir=Path("Rhyme"), meter_source=Path("Songci_Meter"))
    try:
        from apps.launcher.model_catalog import report_path
        from .pair_verification import GenerationVerification
        verification = GenerationVerification(ready.parent, os.environ['SHIJU_MODEL_PATH'], os.environ.get('SHIJU_APPLICATION_PYTHON', os.sys.executable))
        prepare_model(runner, engine, ready.parent, os.environ['SHIJU_MODEL_PATH'], checks, report, check_report)
        target = report_path(ready.parent, os.environ["SHIJU_MODEL_PATH"])
        target.parent.mkdir(parents=True, exist_ok=True)
        if not any(c.get('state') == 'pending' for c in checks):
            from apps.launcher.model_catalog import verified_report
            previous = verified_report(ready.parent, os.environ['SHIJU_MODEL_PATH']) or {}
            snapshot = os.getenv('SHIJU_ENVIRONMENT_REPORT')
            if snapshot and Path(snapshot).is_file():
                previous['environment'] = json.loads(Path(snapshot).read_text(encoding='utf-8'))
            previous.update(ok=True, model=os.environ['SHIJU_MODEL_PATH'], checks=checks,
                            python=os.sys.executable,
                            plan=(previous.get('plan') or {}) | dict(quantization=os.environ['SHIJU_QUANTIZATION']))
            temporary = target.with_suffix('.' + uuid.uuid4().hex + '.tmp')
            temporary.write_text(json.dumps(previous, ensure_ascii=False), encoding='utf-8')
            temporary.replace(target)
    except Exception as exc:
        for item in checks:
            if item['state'] == 'running':
                item.update(state='failed', detail=str(exc))
        message = "显存不足，请关闭占用显存的程序或选择更小模型" if "out of memory" in str(exc).lower() else f"模型验收失败：{type(exc).__name__}: {exc}"
        report("failed", message)
        raise
    service = GenerationService(engine)
    worker = GpuWorker('local-poetry', WorkerApiClient(control_url), service,
                       capabilities={'model': os.environ['SHIJU_MODEL_PATH'],
                                     'tasks': ['generate', 'partial_generate', 'rewrite'], 'concurrency': 1})
    def on_success(job, result):
        if verification.record(job, result):
            checks[:] = [dict(id='actual_generation', name='网页实际生成', state='passed', detail='已成功生成，环境—模型配对已保存到记忆')]
            report('ready', '实际生成成功，配对已验证并记住')
    worker.on_success = on_success
    def release_when_idle(seconds):
        config_path = Path(os.environ.get("SHIJU_RUNTIME_CONFIG", str(ready.parent / "runtime.json")))
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            timeout = int(config.get("idle_unload_seconds", 300))
        except (OSError, ValueError, TypeError):
            timeout = 300
        if timeout > 0 and seconds >= timeout and runner._model is not None:
            runner.unload()
            report("released", "空闲超时，模型权重已释放；新任务将自动重新加载")
    def ensure_loaded():
        worker._weights_reused = runner._model is not None
        if runner._model is None:
            ready.unlink(missing_ok=True)
            report("loading", "正在为新任务重新加载模型权重")
            try:
                runner.load()
            except Exception as exc:
                runner.unload()
                report("released", f"重新加载失败：{exc}；本次任务失败，下次提交可重试")
                raise
            ready.touch()
            report("ready", "模型已重新加载，正在处理任务")
    report("ready", "已加载；未验证，等待一次实际生成成功" if any(c.get("state") == "pending" for c in checks) else "已加载，已读取有效的模型验收记录")
    ready.touch()
    try:
        worker.run_resident(on_idle=release_when_idle, before_job=ensure_loaded)
    finally:
        ready.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
