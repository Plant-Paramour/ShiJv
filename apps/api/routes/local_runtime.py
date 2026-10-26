from pathlib import Path
import json
import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..local_runtime import read_config, validate_model
from apps.launcher.model_catalog import verified_report
from ..environment import selected_environment, inspect_environment, assess_model
from .model_config import _read, _config_path

router = APIRouter(prefix="/v1/local-runtime", tags=["local-runtime"])


class RuntimeConfig(BaseModel):
    default_model: str = Field(default="", max_length=200)
    instructions: str = Field(default="", max_length=8000)
    max_tool_rounds: int = Field(default=8, ge=1, le=20)
    model_path: str = Field(default="", max_length=2048)
    quantization: str = Field(default="auto", pattern="^(auto|4bit|8bit|none)$")
    auto_start: bool = False
    allow_unverified: bool = False
    idle_unload_seconds: int = Field(default=300, ge=0, le=86400)


def require_profile(request):
    if request.app.state.users.local_profile() is None:
        raise HTTPException(428, "请先创建本地用户档案")


@router.get("")
def get_runtime(request: Request):
    runtime = request.app.state.local_runtime
    with request.app.state.database.connect() as db:
        busy = db.execute("SELECT 1 FROM jobs WHERE status='running' OR (status='queued' AND kind='agent_turn') LIMIT 1").fetchone() is not None
    return {"config": read_config(runtime.directory), "worker": runtime.status(),
            "environment": selected_environment(runtime.directory), "busy": busy,
            "agent_ready": request.app.state.agent_service is not None}


@router.post("/check")
def check_runtime(request: Request):
    require_profile(request)
    runtime = request.app.state.local_runtime
    environment = inspect_environment(runtime.directory, refresh=True)
    result = {"environment": environment, "plan": None, "error": ""}
    model = read_config(runtime.directory)["model_path"]
    if model:
        try:
            current = runtime.status()
            result["plan"] = current["plan"] if current["state"] in {"ready", "loading", "released"} and current["plan"] else assess_model(validate_model(model), environment)
        except (ValueError, OSError) as exc:
            result["error"] = str(exc)
    return result


@router.put("")
def save_runtime(body: RuntimeConfig, request: Request):
    require_profile(request)
    runtime = request.app.state.local_runtime
    if body.default_model and body.default_model not in {e["model"] for e in _read(_config_path(request))}:
        raise HTTPException(422, "请先保存所选 Agent 模型的服务端点")
    try:
        if body.model_path:
            body.model_path = str(validate_model(body.model_path))
        elif body.auto_start:
            raise ValueError("自动启动需要先选择本地模型目录")
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc
    previous = read_config(runtime.directory)
    if body.model_path != previous["model_path"]:
        body.allow_unverified = False
    body.quantization = "auto"
    if runtime.status()["state"] in {"loading", "ready", "released"} and any(previous[k] != getattr(body, k) for k in ("model_path", "quantization")):
        raise HTTPException(409, "请先等待任务完成并停止生成器，再更换模型或量化方式")
    path = runtime.directory / "runtime.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(body.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    request.app.state.reload_agent_service()
    return get_runtime(request)


@router.get("/models")
def discover_models(request: Request, directory: str = ""):
    require_profile(request)
    root = request.app.state.project_root
    roots = [Path(directory)] if directory else [root, Path.home() / ".cache/huggingface/hub", Path.home() / ".cache/modelscope/hub"]
    found = {}
    runtime = request.app.state.local_runtime
    environment = inspect_environment(runtime.directory, refresh=True)
    current = runtime.status()
    current_path = read_config(runtime.directory)["model_path"]
    # Bounded scan of selected locations, never recursively scan a disk.
    for base in roots:
        if not base.is_dir():
            if directory:
                raise HTTPException(422, "扫描目录不存在")
            continue
        for parent, dirs, files in os.walk(base):
            depth = len(Path(parent).relative_to(base).parts)
            dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__", ".idea"}][:100]
            if depth >= 4:
                dirs[:] = []
            if "config.json" in files:
                try:
                    model = validate_model(parent)
                    plan = current["plan"] if str(model) == current_path and current["state"] in {"ready", "loading", "released"} and current["plan"] else assess_model(model, environment)
                    found[str(model)] = {"path": str(model), "name": model.name, "supported": True, "reason": plan["label"]}
                except (ValueError, OSError) as exc:
                    found[str(Path(parent).resolve())] = {"path": str(Path(parent).resolve()), "name": Path(parent).name, "supported": False, "reason": str(exc)}
            if len(found) >= 100:
                break
    for item in found.values():
        report = verified_report(runtime.directory, item['path'], environment.get('inference_python'))
        item['verified'] = bool(report and report.get('ok'))
        if item['verified'] and item['supported']:
            item['reason'] = '完整验收通过 · ' + item['reason']
    return {"models": list(found.values()), "environment": environment,
            "scanned_roots": [str(p.resolve()) for p in roots if p.is_dir()]}


@router.get("/verified-models")
def verified_models(request: Request):
    require_profile(request)
    runtime = request.app.state.local_runtime
    environment = selected_environment(runtime.directory)
    models = {}
    for path in (runtime.directory / "model-checks").glob("*.json"):
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            model = saved.get("model", "")
            if not model:
                continue
            current = verified_report(runtime.directory, model, environment["inference_python"])
            if current and current.get("ok"):
                models[model] = {"path": model, "name": Path(model).name, "verified": True}
        except (ValueError, OSError):
            continue
    return {"models": list(models.values())}


@router.post("/start")
def start_worker(request: Request):
    require_profile(request)
    try:
        return request.app.state.local_runtime.start(f"http://127.0.0.1:{request.url.port or 18763}")
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/stop")
def stop_worker(request: Request):
    require_profile(request)
    with request.app.state.database.connect() as db:
        if db.execute("SELECT 1 FROM jobs WHERE kind IN ('generate','rewrite','partial_generate') AND status IN ('queued','running') LIMIT 1").fetchone():
            raise HTTPException(409, "仍有诗词任务排队或运行，请完成或取消后再停止生成器")
    request.app.state.local_runtime.stop()
    return request.app.state.local_runtime.status()


@router.post("/shutdown")
def shutdown_application(request: Request):
    from starlette.background import BackgroundTask
    from fastapi.responses import JSONResponse
    callback = getattr(request.app.state, "launcher_shutdown", None)
    if callback is None:
        raise HTTPException(404, "当前实例未由新启动器管理")
    runtime = request.app.state.local_runtime
    with request.app.state.database.connect() as db:
        if db.execute("SELECT 1 FROM jobs WHERE status='running' OR (status='queued' AND kind='agent_turn') LIMIT 1").fetchone():
            raise HTTPException(409, "仍有任务运行，请完成或取消后再退出")
        if runtime.status()["state"] in {"ready", "loading", "released"} and db.execute("SELECT 1 FROM jobs WHERE status='queued' LIMIT 1").fetchone():
            raise HTTPException(409, "生成器还有排队任务，请完成或取消后再退出")
    runtime.stop()
    return JSONResponse({"status": "stopping"}, background=BackgroundTask(callback))
