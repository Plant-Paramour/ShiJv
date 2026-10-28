import json
import threading
import subprocess
import sys
import time
import socket
import urllib.request
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.api.settings import ApiSettings


def test_launcher_service_starts_and_shuts_down_with_workspace_identity(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIJU_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('SHIJU_LAUNCHER_WORKSPACE_ROOT', str(tmp_path / 'launcher-workspace'))
    for key in ('DATABASE_URL', 'SHIJU_DATABASE_PATH', 'SHIJU_CONTROL_URL'):
        monkeypatch.delenv(key, raising=False)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    root = Path(__file__).resolve().parents[1]
    process = subprocess.Popen([sys.executable, '-I', str(root / 'apps/launcher/python_entry.py'),
                                '-m', 'apps.launcher.serve', '--port', str(port)], cwd=tmp_path)
    base = f'http://127.0.0.1:{port}'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        for _ in range(150):
            try:
                with opener.open(base + '/health/live', timeout=1) as response:
                    health = json.load(response)
                break
            except OSError:
                assert process.poll() is None, (tmp_path / 'api-startup.log').read_text(encoding='utf-8')
                time.sleep(.1)
        else:
            raise AssertionError('service did not become ready')
        assert health['pid'] == process.pid
        assert health['workspace'] == str(tmp_path / 'launcher-workspace')
        saved_identity = json.loads((tmp_path / 'startup-diagnostics/service.json').read_text(encoding='utf-8'))
        assert saved_identity['state'] == 'running'
        assert saved_identity['build'] == health['build']
        request = urllib.request.Request(base + '/v1/local-runtime/shutdown', method='POST')
        with opener.open(request, timeout=15) as response:
            assert json.load(response)['status'] == 'stopping'
        assert process.wait(timeout=20) == 0
        assert json.loads((tmp_path / 'startup-diagnostics/service.json').read_text(encoding='utf-8'))['state'] == 'stopped'
        with socket.socket() as check:
            assert check.connect_ex(('127.0.0.1', port)) != 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


def test_successful_model_report_is_reused_by_launcher_memory(tmp_path):
    from apps.api.launcher_memory import LauncherMemory
    from apps.launcher.model_catalog import report_path
    model = tmp_path / 'model'
    model.mkdir()
    (model / 'config.json').write_text('{}', encoding='utf-8')
    memory = LauncherMemory(tmp_path)
    memory.remember(sys.executable, sys.executable, True)
    path = report_path(tmp_path, model)
    path.parent.mkdir()
    path.write_text(json.dumps(dict(ok=True, model=str(model))), encoding='utf-8')
    assert memory.check(sys.executable, sys.executable, True, str(model))['ok']
    assert memory.pairs()['pairs'][0]['valid']
    (model / 'config.json').write_text('{"changed":true}', encoding='utf-8')
    assert not memory.check(sys.executable, sys.executable, True, str(model))['ok']


def test_candidate_limits_match_across_api_and_contracts():
    import pytest
    from pydantic import ValidationError
    from apps.api.schemas import GenerateJobModel, PartialGenerateJobModel, RewriteJobModel
    from shiju.contracts import GeneratePoemRequest, PartialGeneratePoemRequest, RewritePoemRequest, ContractError
    base = dict(meter_type='唐诗', form_name='五言绝句', theme='春山')
    rewrite = base | dict(original_text='空山新雨后，天气晚来秋。明月松间照，清泉石上流。', target_line_numbers=(2,))
    for api, contract, payload, limit in (
        (GenerateJobModel, GeneratePoemRequest, base, 20),
        (PartialGenerateJobModel, PartialGeneratePoemRequest, rewrite, 20),
        (RewriteJobModel, RewritePoemRequest, rewrite, 50),
        (PartialGenerateJobModel, PartialGeneratePoemRequest, rewrite | dict(source_poem_id='saved-poem'), 50),
    ):
        assert api(**payload, candidate_count=limit).candidate_count == limit
        assert contract(**payload, candidate_count=limit).candidate_count == limit
        with pytest.raises(ValidationError):
            api(**payload, candidate_count=limit + 1)
        with pytest.raises(ContractError):
            contract(**payload, candidate_count=limit + 1)


def test_model_acceptance_saves_pair_without_prior_environment_check(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from apps.launcher import model_probe, environment_probe
    from apps.api.launcher_memory import LauncherMemory
    model = tmp_path / 'model'
    model.mkdir()
    (model / 'config.json').write_text('{"model_type":"qwen3_5"}')
    (model / 'tokenizer.json').write_text('{}')
    (model / 'model.safetensors').write_bytes(b'fixture')
    monkeypatch.setattr(environment_probe, 'probe', lambda mode: dict(ok=True, python=sys.executable,
        errors=[], cuda=False, quantization=['none'], supported_model_types=['qwen3_5'],
        cpu_memory_available_bytes=8 * 1024**3))
    monkeypatch.setitem(sys.modules, 'shiju.generation.model_runner',
                        SimpleNamespace(ModelRunner=lambda settings: SimpleNamespace(unload=lambda: None)))
    monkeypatch.setitem(sys.modules, 'shiju.generation.engine', SimpleNamespace(GenerationEngine=lambda runner: runner))
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False)))
    def accepted(runner, engine, report):
        for key in ('load', 'output', 'constraints'):
            report(key, key, 'passed', 'fixture acceptance')
    monkeypatch.setattr(model_probe, 'check_runner', accepted)
    output = tmp_path / 'result.json'
    monkeypatch.setattr(sys, 'argv', ['model_probe', '--model', str(model), '--data-dir', str(tmp_path),
        '--output', str(output), '--application-python', sys.executable])
    assert not (tmp_path / 'launcher-memory.json').exists()
    assert model_probe.main() == 0
    assert json.loads(output.read_text(encoding='utf-8'))['pair_saved']
    memory = LauncherMemory(tmp_path)
    assert memory.check(sys.executable, sys.executable, True, str(model))['ok']
    assert memory.pairs()['last_pair']['model_path'] == str(model)


def test_identical_source_copies_share_acceptance_but_actual_edits_invalidate(tmp_path, monkeypatch):
    from apps.launcher import model_catalog
    from apps.api import launcher_memory
    first, second = tmp_path / 'first', tmp_path / 'second'
    for root in (first, second):
        for name in ('apps/launcher/model_catalog.py', 'apps/launcher/environment_probe.py',
                     'apps/api/launcher_memory.py', 'shiju/decoder.py', 'apps/gpu_worker/local_main.py'):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# identical source')
        (root / 'pyproject.toml').write_text('# identical configuration')
    model = tmp_path / 'model'
    model.mkdir()
    monkeypatch.setattr(model_catalog, '__file__', str(first / 'apps/launcher/model_catalog.py'))
    monkeypatch.setattr(launcher_memory, '__file__', str(first / 'apps/api/launcher_memory.py'))
    fingerprint = model_catalog.fingerprint(model)
    stamp = launcher_memory.environment_stamp(sys.executable)
    monkeypatch.setattr(model_catalog, '__file__', str(second / 'apps/launcher/model_catalog.py'))
    monkeypatch.setattr(launcher_memory, '__file__', str(second / 'apps/api/launcher_memory.py'))
    assert model_catalog.fingerprint(model) == fingerprint
    assert launcher_memory.environment_stamp(sys.executable) == stamp
    (second / 'shiju/decoder.py').write_text('# actual edit')
    assert model_catalog.fingerprint(model) != fingerprint


def test_managed_runtime_checks_runtime_files_independently_of_old_model(tmp_path):
    import hashlib
    import pytest
    from apps.api.environment import check_managed_runtime
    python = tmp_path / 'python.exe'
    python.write_bytes(b'interpreter')
    old_model = tmp_path / 'old-model'
    old_model.mkdir()
    (tmp_path / 'shiju-install.json').write_text(json.dumps(dict(state='verified', ok=True, model=str(old_model))))
    (tmp_path / 'verified-inputs.json').write_text(json.dumps({
        str(python): hashlib.sha256(python.read_bytes()).hexdigest(),
        str(old_model / 'deleted-weight.safetensors'): 'outdated-model-hash'}))
    check_managed_runtime(python)
    python.write_bytes(b'changed interpreter')
    with pytest.raises(ValueError, match='部署环境文件缺失或已变化'):
        check_managed_runtime(python)


def test_explicit_module_entry_works_in_isolated_python(tmp_path):
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / 'memory.json'
    subprocess.run([sys.executable, '-I', str(root / 'apps/launcher/python_entry.py'),
                    '-m', 'apps.api.launcher_memory', '--action', 'list', '--data-dir', str(tmp_path),
                    '--application-python', sys.executable, '--inference-python', sys.executable,
                    '--output', str(output)], cwd=tmp_path, check=True, timeout=30)
    assert json.loads(output.read_text(encoding='utf-8')) == dict(pairs=[], last_pair=None)


def test_model_test_rejects_broken_deployment_before_loading(tmp_path, monkeypatch):
    from apps.launcher import model_probe
    from apps.api import environment
    output = tmp_path / 'result.json'
    monkeypatch.setattr(sys, 'argv', ['model_probe', '--model', str(tmp_path), '--data-dir', str(tmp_path),
        '--output', str(output), '--application-python', sys.executable])
    def broken(python):
        raise ValueError('部署环境文件缺失或已变化：runtime.dll')
    monkeypatch.setattr(environment, 'check_managed_runtime', broken)
    monkeypatch.setattr(model_probe, 'check_runner', lambda *a: (_ for _ in ()).throw(AssertionError('must not load')))
    assert model_probe.main() == 1
    result = json.loads(output.read_text(encoding='utf-8'))
    assert not result['ok'] and 'runtime.dll' in result['error']
    assert not (tmp_path / 'launcher-memory.json').exists()


def test_workspace_data_selection_does_not_leak_to_other_machine(tmp_path, monkeypatch):
    from apps.api import data_directory
    selection = tmp_path / 'var/local-data.json'
    selection.parent.mkdir()
    selection.write_text(json.dumps(dict(directory='host-data', machine='host-pc', user_profile='host-user')))
    monkeypatch.setattr(data_directory, '__file__', str(tmp_path / 'apps/api/data_directory.py'))
    monkeypatch.setenv('COMPUTERNAME', 'host-pc')
    monkeypatch.setenv('USERPROFILE', 'host-user')
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    assert data_directory.default_data_directory() == (tmp_path / 'var/host-data').resolve()
    monkeypatch.setenv('COMPUTERNAME', 'other-pc')
    assert data_directory.default_data_directory() == tmp_path / 'local/ShiJu'
    monkeypatch.setenv('COMPUTERNAME', 'host-pc')
    monkeypatch.setenv('USERPROFILE', 'other-user')
    assert data_directory.default_data_directory() == tmp_path / 'local/ShiJu'


def test_disabled_inference_does_not_autostart_a_saved_foreign_model(tmp_path, monkeypatch):
    from apps.api.local_runtime import LocalRuntime
    monkeypatch.setenv('SHIJU_DATA_DIR', str(tmp_path))
    for key in ('DATABASE_URL', 'SHIJU_DATABASE_PATH'):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / 'launcher.json').write_text(json.dumps(dict(local_enabled=False)))
    (tmp_path / 'runtime.json').write_text(json.dumps(dict(auto_start=True, model_path='foreign-user/model')))
    calls = []
    def unexpected(*args, **kwargs):
        calls.append(True)
        raise AssertionError('disabled inference must not start')
    monkeypatch.setattr(LocalRuntime, 'start', unexpected)
    app = create_app()
    with TestClient(app) as client:
        assert client.get('/health/live').status_code == 200
        assert app.state.local_runtime.start_error == ''
        assert not calls


def test_local_start_profile_model_and_persisted_stream(tmp_path, monkeypatch):
    """Exercise the actual Agent queue and plain SQLite message storage."""
    monkeypatch.setenv("SHIJU_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SHIJU_ENV", "development")
    (tmp_path / 'launcher.json').write_text(json.dumps(dict(local_enabled=False)))
    for name in ("DATABASE_URL", "SHIJU_DATABASE_PATH"):
        monkeypatch.delenv(name, raising=False)

    class ModelHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            if body.get("stream"):
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for delta, finish in (({"content": "本机运行验收通过。"}, None), ({}, "stop")):
                    chunk = {"choices": [{"delta": delta, "finish_reason": finish}]}
                    self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                self.wfile.write(b"data: [DONE]\n\n")
            else:
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"choices": [{"message": {"role": "assistant", "content": "验收对话"}}]}).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        settings = ApiSettings.from_env()
        app = create_app(settings)
        with TestClient(app) as client:
            assert client.get("/health/ready").status_code == 200
            assert client.get("/v1/local-profile").json()["created"]
            assert client.get("/v1/local-profile").json()["profile"]["display_name"] == "请输入用户名"
            assert client.patch("/v1/local-profile", json={"display_name": "验收用户"}).status_code == 200
            endpoint = {"provider_id": "test", "provider": "openai-compatible",
                        "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                        "model": "test-model", "api_key": "disposable-test-key"}
            assert client.put("/v1/model-config", json={"endpoints": [endpoint]}).status_code == 200
            response = client.post("/v1/agent/chat/stream", json={"message": "运行验收", "model": "test-model"})
            assert response.status_code == 200
            assert "event: turn.completed" in response.text, response.text
            conversations = client.get("/v1/conversations").json()["items"]
            assert conversations
            with app.state.database.connect() as connection:
                row = connection.execute("SELECT content_stored FROM messages WHERE role='assistant' AND status='completed' LIMIT 1").fetchone()
                assert row and row[0] == "本机运行验收通过。"
            for path in ("/v1/auth/login", "/v1/forum/sections", "/v1/admin/stats"):
                assert client.get(path).status_code == 404
        with TestClient(create_app(settings)) as client:
            assert client.get("/v1/local-profile").json()["profile"]["display_name"] == "验收用户"
            assert client.get("/v1/conversations").json()["items"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_legacy_sandbox_runtime_integrity_relocates_and_still_checks_content(tmp_path):
    import hashlib
    import pytest
    from apps.api.environment import check_managed_runtime
    python = tmp_path / 'python.exe'
    python.write_bytes(b'interpreter')
    package = tmp_path / 'Lib/site-packages/example.dist-info/METADATA'
    package.parent.mkdir(parents=True)
    package.write_bytes(b'pinned package')
    old = 'C:/Users/WDAGUtilityAccount/Desktop/qwen35'
    (tmp_path / 'shiju-install.json').write_text(json.dumps(dict(state='verified', ok=True, python=old+'/python.exe', model='C:/old-model')))
    inputs = {old+'/python.exe': hashlib.sha256(python.read_bytes()).hexdigest(), old+'/Lib/site-packages/example.dist-info/METADATA': hashlib.sha256(package.read_bytes()).hexdigest(), 'C:/old-model/model.safetensors': 'ignored-model'}
    (tmp_path / 'verified-inputs.json').write_text(json.dumps(inputs))
    check_managed_runtime(python)
    package.write_bytes(b'changed')
    with pytest.raises(ValueError, match='部署环境文件缺失或已变化') as error:
        check_managed_runtime(python)
    assert str(package) in str(error.value)
    assert 'WDAGUtilityAccount' not in str(error.value)


def test_portable_runtime_integrity_rejects_changed_files_and_escape(tmp_path):
    import hashlib
    import pytest
    from apps.api.environment import check_managed_runtime
    python = tmp_path / 'python.exe'
    python.write_bytes(b'interpreter')
    (tmp_path / 'shiju-install.json').write_text(json.dumps(dict(state='verified', ok=True, python='C:/old/python.exe')))
    manifest = tmp_path / 'verified-inputs.json'
    manifest.write_text(json.dumps(dict(schema_version=2, files={'python.exe': hashlib.sha256(python.read_bytes()).hexdigest()})))
    check_managed_runtime(python)
    python.write_bytes(b'tampered')
    with pytest.raises(ValueError, match='部署环境文件缺失或已变化'):
        check_managed_runtime(python)
    manifest.write_text(json.dumps(dict(schema_version=2, files={'../outside': 'digest'})))
    with pytest.raises(ValueError):
        check_managed_runtime(python)


def test_slow_inference_check_does_not_block_api_start_or_shutdown(tmp_path, monkeypatch):
    from apps.api import local_runtime
    monkeypatch.setenv('SHIJU_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('SHIJU_ENV', 'development')
    for name in ('DATABASE_URL', 'SHIJU_DATABASE_PATH', 'SHIJU_CONTROL_URL'): monkeypatch.delenv(name, raising=False)
    (tmp_path/'launcher.json').write_text(json.dumps(dict(local_enabled=True)))
    (tmp_path/'runtime.json').write_text(json.dumps(dict(auto_start=True,model_path='slow-model')))
    entered=threading.Event();release=threading.Event();finished=threading.Event()
    calls=[];spawned=[]
    monkeypatch.setattr(local_runtime,'validate_model',lambda *args: None)
    def slow(*args,**kwargs):
        calls.append(True);entered.set()
        try:
            assert release.wait(8), 'test check was not released'
            return dict(inference_python=str(tmp_path/'python.exe'))
        finally:finished.set()
    monkeypatch.setattr(local_runtime,'inspect_environment',slow)
    monkeypatch.setattr(local_runtime,'assess_model',lambda *args: dict(quantization='none'))
    monkeypatch.setattr(local_runtime.subprocess,'Popen',lambda *args,**kwargs: spawned.append(True))
    app=create_app()
    try:
        with TestClient(app) as client:
            assert entered.wait(1)
            assert not release.is_set()
            started=time.monotonic()
            assert client.get('/health/live').status_code==200
            status=client.get('/v1/local-runtime').json()['worker']
            assert time.monotonic()-started<1
            assert status['state']=='loading' and status['phase']=='checking'
            app.state.local_runtime.start(background=True)
            assert len(calls)==1
            # Stop while the check is blocked. It must not create a worker later.
            started=time.monotonic()
            app.state.local_runtime.stop()
            assert time.monotonic()-started<1
            release.set();assert finished.wait(2)
    finally:
        release.set()
    assert not spawned
    assert app.state.local_runtime.status()['state']=='stopped'


def test_background_check_failure_is_visible_and_fails_queued_task(tmp_path, monkeypatch):
    from apps.api import local_runtime
    monkeypatch.setenv('SHIJU_DATA_DIR',str(tmp_path))
    monkeypatch.setenv('SHIJU_ENV','development')
    for name in ('DATABASE_URL','SHIJU_DATABASE_PATH','SHIJU_CONTROL_URL'):monkeypatch.delenv(name,raising=False)
    (tmp_path/'launcher.json').write_text(json.dumps(dict(local_enabled=True)))
    (tmp_path/'runtime.json').write_text(json.dumps(dict(auto_start=True,model_path='broken-model')))
    entered=threading.Event();release=threading.Event()
    monkeypatch.setattr(local_runtime,'validate_model',lambda *args:None)
    def slow_failure(*args,**kwargs):
        entered.set();assert release.wait(8)
        raise ValueError('specific dependency failure')
    monkeypatch.setattr(local_runtime,'inspect_environment',slow_failure)
    app=create_app()
    try:
        with TestClient(app) as client:
            assert entered.wait(1)
            user=app.state.users.local_profile()
            job=app.state.jobs.submit('generate',dict(meter_type='唐诗',form_name='五言绝句',theme='春'),user_id=user['id'])
            app.state.local_runtime.watch_submission(job.id)
            release.set()
            deadline=time.monotonic()+3
            while time.monotonic()<deadline and app.state.jobs.get(job.id).status!='failed':time.sleep(.05)
            assert 'specific dependency failure' in app.state.jobs.get(job.id).to_dict()['error']['message']
            state=client.get('/v1/local-runtime').json()['worker']
            assert state['state']=='failed'
            assert 'specific dependency failure' in state['error']
    finally:release.set()
