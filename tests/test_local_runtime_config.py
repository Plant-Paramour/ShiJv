import json
import time
import threading
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.api.local_runtime import validate_model


@pytest.fixture
def runtime_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIJU_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SHIJU_ENV", "development")
    for key in ("DATABASE_URL", "SHIJU_DATABASE_PATH"):
        monkeypatch.delenv(key, raising=False)
    from apps.api import local_runtime
    from apps.api.routes import local_runtime as runtime_routes
    report = {"ok": True, "errors": [], "inference_python": "C:\\ProgramData\\anaconda3\\envs\\ShiJu\\python.exe",
              "supported_model_types": ["qwen3"], "quantization": ["none", "4bit"], "gpu": {"free_bytes": 8 * 1024**3}}
    monkeypatch.setattr(local_runtime, "inspect_environment", lambda *a, **kw: report)
    monkeypatch.setattr(runtime_routes, "inspect_environment", lambda *a, **kw: report)
    app = create_app()
    with TestClient(app) as client:
        client.post("/v1/local-profile", json={"display_name": "本机验收"})
        yield client, app, tmp_path


def test_agent_preferences_and_local_keyless_endpoint(runtime_client):
    client, app, _ = runtime_client
    assert client.post("/v1/agent/chat/stream", json={"message": "写诗"}).status_code == 409
    assert client.put("/v1/model-config", json={"endpoints": [{"provider_id": "local", "provider": "openai-compatible",
        "base_url": "http://127.0.0.1:1234/v1", "model": "local-chat"}]}).status_code == 200
    response = client.put("/v1/local-runtime", json={"default_model": "local-chat", "instructions": "清淡自然", "max_tool_rounds": 5})
    assert response.status_code == 200, response.text
    assert response.json()["agent_ready"]
    assert app.state.agent_service._instructions == "清淡自然"
    assert app.state.agent_service._max_tool_rounds == 5
    assert client.get("/v1/local-runtime").json()["config"]["default_model"] == "local-chat"
    assert client.put("/v1/local-runtime", json={"default_model": "missing"}).status_code == 422


def test_unready_generation_is_rejected_before_queueing(runtime_client):
    client, app, _ = runtime_client
    response = client.post("/v1/poetry/jobs/generate", json={"requirement": "春山", "meter_type": "唐诗", "form_name": "五言绝句"})
    assert response.status_code == 409, response.text
    with app.state.database.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_scan_validates_weights_and_save_worker_config(runtime_client):
    client, _, tmp_path = runtime_client
    model = tmp_path / "models" / "demo"
    model.mkdir(parents=True)
    (model / "config.json").write_text('{"model_type":"qwen3"}')
    (model / "tokenizer.json").write_text("{}")
    assert client.put("/v1/local-runtime", json={"model_path": str(model)}).status_code == 422
    (model / "model.safetensors").write_bytes(b"test fixture")
    assert validate_model(str(model)) == model.resolve()
    found = client.get("/v1/local-runtime/models", params={"directory": str(tmp_path / "models")}).json()
    assert found["models"][0]["path"] == str(model.resolve())
    response = client.put("/v1/local-runtime", json={"model_path": str(model), "quantization": "4bit", "auto_start": True})
    assert response.status_code == 200
    assert client.get("/v1/local-runtime").json()["config"]["auto_start"]
    (model / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"x": "missing.safetensors"}}))
    with pytest.raises(ValueError, match="权重不完整"):
        validate_model(str(model))


def test_owned_worker_readiness_stop_and_no_stale_heartbeat(runtime_client, monkeypatch):
    client, app, tmp_path = runtime_client
    from apps.api import local_runtime
    model = tmp_path / "model"
    model.mkdir()
    for name in ("config.json", "tokenizer.json"):
        (model / name).write_text('{"model_type":"qwen3"}' if name == "config.json" else "{}")
    (model / "model.safetensors").write_bytes(b"fixture")
    assert client.put("/v1/local-runtime", json={"model_path": str(model)}).status_code == 200
    launched = {}

    class Process:
        code = None
        def __init__(self, args, **kwargs):
            self.stdout = io.BytesIO()
            launched.update(args=args, **kwargs)
        def poll(self):
            return self.code
        def terminate(self):
            self.code = 0
        def wait(self, timeout):
            return self.code

    monkeypatch.setattr(local_runtime.subprocess, "Popen", Process)
    response = client.post("/v1/local-runtime/start")
    assert response.status_code == 200 and response.json()["state"] == "loading"
    deadline = time.monotonic() + 5
    while 'env' not in launched and time.monotonic() < deadline:
        time.sleep(.01)
    assert launched["env"]["SHIJU_CONTROL_URL"] == "http://127.0.0.1:18763"
    assert launched["env"]["SHIJU_MODEL_PATH"] == str(model)
    app.state.local_runtime.ready.touch()
    assert client.get("/v1/local-runtime").json()["worker"]["ready"]
    with app.state.database.connect() as db:
        db.execute("INSERT INTO workers(id,capabilities_json,last_seen_at) VALUES (?,?,?)", ("local-poetry", "{}", time.time()))
    assert client.post("/v1/local-runtime/stop").json()["state"] == "stopped"
    assert not client.get("/v1/local-runtime").json()["worker"]["external_worker"]


def test_scan_lists_models_even_when_inference_is_disabled(runtime_client, monkeypatch):
    client, _, tmp_path = runtime_client
    from apps.api.routes import local_runtime as routes
    monkeypatch.setattr(routes, "inspect_environment", lambda *a, **kw: {
        "ok": False, "local_enabled": False, "errors": ["请在启动器启用本地生成"]})
    model = tmp_path / "Qwen3.5"
    model.mkdir()
    (model / "config.json").write_text('{"model_type":"qwen3_5","architectures":["Qwen3_5ForConditionalGeneration"]}')
    (model / "tokenizer.json").write_text("{}")
    (model / "model.safetensors").write_bytes(b"fixture")
    result = client.get("/v1/local-runtime/models", params={"directory": str(model)}).json()
    assert len(result["models"]) == 1
    assert not result["models"][0]["supported"]
    assert "启用本地生成" in result["models"][0]["reason"]
    assert result["scanned_roots"] == [str(model.resolve())]


def test_managed_environment_can_start_a_different_verified_model(runtime_client, monkeypatch):
    client, app, tmp_path = runtime_client
    from apps.api import local_runtime
    from apps.launcher import model_catalog
    model = tmp_path / 'new-model'
    model.mkdir()
    (model / 'config.json').write_text('{"model_type":"qwen3"}')
    (model / 'tokenizer.json').write_text('{}')
    (model / 'model.safetensors').write_bytes(b'fixture')
    python = tmp_path / 'python.exe'
    python.write_bytes(b'fixture interpreter')
    (tmp_path / 'shiju-install.json').write_text(json.dumps(dict(model=str(tmp_path / 'old-model'), precision='4bit')))
    monkeypatch.setattr(local_runtime, 'inspect_environment', lambda *a, **kw: dict(ok=True, errors=[],
        inference_python=str(python), cuda=False, cpu_memory_available_bytes=8 * 1024**3,
        supported_model_types=['qwen3'], quantization=['none']))
    monkeypatch.setattr(model_catalog, 'verified_report', lambda *a: dict(ok=True, plan=dict(quantization='none')))
    launched = {}
    class Process:
        code = None
        def __init__(self, args, **kw):
            self.stdout = io.BytesIO()
            launched.update(args=args, **kw)
        def poll(self): return self.code
        def terminate(self): self.code = 0
        def wait(self, timeout): return self.code
    monkeypatch.setattr(local_runtime.subprocess, 'Popen', Process)
    assert client.put('/v1/local-runtime', json=dict(model_path=str(model))).status_code == 200
    response = client.post('/v1/local-runtime/start')
    assert response.status_code == 200, response.text
    deadline = time.monotonic() + 5
    while 'args' not in launched and time.monotonic() < deadline:
        time.sleep(.01)
    assert launched['args'][0] == str(python)
    assert launched['args'][1] == '-I'
    assert Path(launched['args'][2]).name == 'python_entry.py'
    assert launched['env']['SHIJU_MODEL_PATH'] == str(model)
    assert launched['env']['SHIJU_QUANTIZATION'] == 'none'


def test_local_worker_exit_after_claim_fails_running_task(runtime_client):
    client, app, _ = runtime_client
    runtime = app.state.local_runtime
    class Process:
        code = None
        def poll(self): return self.code
    process = Process()
    runtime.process = process
    runtime.ready.touch()
    runtime.log.write_text('native failure details', encoding='utf-8')
    user = app.state.users.local_profile()
    job = app.state.jobs.submit('generate', dict(meter_type='唐诗',form_name='五言绝句',theme='春'),user_id=user['id'])
    app.state.jobs.claim('local-poetry', dict(tasks=['generate']))
    runtime.watch_submission(job.id)
    assert job.id in runtime.watched_jobs
    process.code = -1073741819
    deadline = time.monotonic()+4
    while time.monotonic()<deadline and app.state.jobs.get(job.id).status != 'failed':
        time.sleep(.05)
    result=app.state.jobs.get(job.id).to_dict()
    assert result['status'] == 'failed'
    assert result['error']['code'] == 'LOCAL_WORKER_EXITED'
    assert '-1073741819' in result['error']['message']
    assert 'native failure details' in result['error']['message']
    assert str(runtime.log) in result['error']['message']
    runtime.process = None


def test_local_worker_watch_does_not_overwrite_completed_task(runtime_client):
    client, app, _ = runtime_client
    runtime = app.state.local_runtime
    class Process:
        code = None
        def poll(self): return self.code
    process=Process()
    runtime.process=process
    runtime.ready.touch()
    user=app.state.users.local_profile()
    job=app.state.jobs.submit('generate',dict(meter_type='唐诗',form_name='五言绝句',theme='春'),user_id=user['id'])
    app.state.jobs.claim('local-poetry',dict(tasks=['generate']))
    runtime.watch_submission(job.id)
    app.state.jobs.complete(job.id,'local-poetry',dict(candidates=[]))
    process.code=1
    deadline=time.monotonic()+3
    while job.id in runtime.watched_jobs and time.monotonic()<deadline: time.sleep(.05)
    assert app.state.jobs.get(job.id).status == 'succeeded'
    runtime.process=None
