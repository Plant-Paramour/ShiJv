import json
from pathlib import Path
import subprocess
import sys
import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from apps.api.main import create_app
    monkeypatch.setenv('SHIJU_DATA_DIR', str(tmp_path))
    for key in ('DATABASE_URL', 'SHIJU_DATABASE_PATH'):
        monkeypatch.delenv(key, raising=False)
    with TestClient(create_app()) as session:
        yield session

from apps.launcher.diagnostics import OperationDiagnostic


def test_process_output_and_full_exception_are_persistent_and_redacted(tmp_path):
    diagnostic = OperationDiagnostic(tmp_path, 'probe')
    code = diagnostic.run([sys.executable, '-I', '-c',
        "import sys;print('password=private-value');print('DLL load failed',file=sys.stderr);sys.exit(7)"])
    assert code == 7
    try:
        try:
            raise OSError('missing runtime.dll')
        except OSError as cause:
            raise ModuleNotFoundError('Qwen import failed') from cause
    except Exception as exc:
        message = diagnostic.fail(exc)
    metadata = diagnostic.metadata
    diagnostic.close()
    text = Path(metadata['log_path']).read_text(encoding='utf-8')
    assert 'private-value' not in text
    assert 'DLL load failed' in text and 'missing runtime.dll' in text and 'Qwen import failed' in text
    assert metadata['log_path'] in message
    assert 'exit_code' in text


def test_diagnostics_fallback_and_both_locations_unwritable(tmp_path, monkeypatch):
    primary = tmp_path / 'unwritable'
    primary.write_text('file instead of directory')
    monkeypatch.setattr('tempfile.gettempdir', lambda: str(tmp_path / 'fallback'))
    diagnostic = OperationDiagnostic(primary, 'probe')
    assert 'fallback' in diagnostic.metadata['log_path']
    diagnostic.close()
    original = Path.mkdir
    def fail(path, *args, **kwargs):
        raise PermissionError('fixture denied')
    monkeypatch.setattr(Path, 'mkdir', fail)
    diagnostic = OperationDiagnostic(primary, 'probe')
    assert diagnostic.metadata['log_path'] is None
    assert '日志写入失败' in diagnostic.describe('original failure')
    diagnostic.close()


def test_diagnostics_rotation_and_unique_operations(tmp_path):
    first, second = OperationDiagnostic(tmp_path, 'probe'), OperationDiagnostic(tmp_path, 'probe')
    assert first.id != second.id
    first.handler.maxBytes = 128
    for _ in range(10):
        first.event('fixture', value='x' * 100)
    path = first.metadata['log_path']
    first.close()
    second.close()
    assert Path(path + '.1').is_file() and Path(path + '.3').is_file()
    assert not Path(path + '.4').exists()


def test_runtime_code_changes_invalidate_build(tmp_path):
    from apps.launcher.build_info import build_info
    source = tmp_path / 'apps/api/environment.py'
    source.parent.mkdir(parents=True)
    source.write_text('# first')
    previous = build_info(tmp_path)
    source.write_text('# second')
    assert build_info(tmp_path) != previous


def test_readiness_reports_failed_generator_but_keeps_service_alive(client, tmp_path):
    runtime = client.app.state.local_runtime
    (runtime.directory / 'launcher.json').write_text(json.dumps(dict(local_enabled=True)))
    runtime.start_error = 'fixture model import failure'
    assert client.get('/health/live').status_code == 200
    result = client.get('/health/ready')
    assert result.status_code == 503
    assert result.json()['generator']['state'] == 'failed'
    (runtime.directory / 'launcher.json').write_text(json.dumps(dict(local_enabled=False)))
    assert client.get('/health/ready').status_code == 200


def test_export_contains_diagnostics_and_http_error_location(client):
    import io
    import zipfile
    result = client.get('/missing-diagnostic-route')
    metadata = result.json()['diagnostics']
    assert result.status_code == 404
    assert result.headers['X-ShiJu-Diagnostic-Id'] == metadata['diagnostic_id']
    assert Path(metadata['log_path']).is_file()
    result = client.get('/v1/startup-diagnostics/export')
    with zipfile.ZipFile(io.BytesIO(result.content)) as archive:
        assert any(metadata['diagnostic_id'] in name for name in archive.namelist())
        assert 'diagnostic-export-index.json' in archive.namelist()


def test_cancel_and_timeout_kill_owned_process_and_keep_log(tmp_path):
    import threading
    diagnostic = OperationDiagnostic(tmp_path, 'cancel')
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(RuntimeError, match='取消'):
        diagnostic.run([sys.executable, '-I', '-c', 'import time;time.sleep(30)'], cancel=cancel)
    text = Path(diagnostic.metadata['log_path']).read_text(encoding='utf-8')
    assert 'process_exit' in text
    diagnostic.close()
    diagnostic = OperationDiagnostic(tmp_path, 'timeout')
    with pytest.raises(subprocess.TimeoutExpired):
        diagnostic.run([sys.executable, '-I', '-c', 'import time;time.sleep(30)'], timeout=.1)
    diagnostic.close()


def test_probe_missing_report_is_persistent(tmp_path, monkeypatch):
    from apps.api.environment import inspect_environment
    python = tmp_path / 'python.exe'
    python.touch()
    (tmp_path / 'launcher.json').write_text(json.dumps(dict(inference_python=str(python))))
    monkeypatch.setattr(OperationDiagnostic, 'run', lambda *a, **kw: 9)
    report = inspect_environment(tmp_path, refresh=True)
    assert not report['ok']
    assert '9' in report['errors'][0]
    assert Path(report['diagnostics']['report_path']).is_file()
    assert report['diagnostics']['log_path'] in report['errors'][0]


def test_json_explicitly_declares_utf8_for_windows_powershell(client):
    response = client.get('/health/live')
    assert response.headers['content-type'].lower() == 'application/json; charset=utf-8'


def test_invalid_interpreter_reports_actual_log_path(tmp_path):
    from apps.api.environment import inspect_environment
    (tmp_path / 'launcher.json').write_text(json.dumps(dict(inference_python=str(tmp_path / 'missing/python.exe'))))
    result = inspect_environment(tmp_path, refresh=True)
    assert not result['ok']
    assert Path(result['diagnostics']['log_path']).is_file()
    assert result['diagnostics']['log_path'] in result['errors'][0]


def test_export_survives_corrupt_browser_report(client):
    import zipfile
    import io
    root = client.app.state.settings.database_path.parent
    target = root / 'startup-diagnostics/latest-browser.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{incomplete json password=private-value', encoding='utf-8')
    response = client.get('/v1/startup-diagnostics/export')
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert 'private-value' not in archive.read('browser.json').decode('utf-8')


def test_retention_protects_active_operation_and_latest_failure(tmp_path):
    import os
    import time
    from apps.launcher.diagnostics import prune
    old = time.time() - 40 * 86400
    roots = {}
    for index, (name, completed, ok) in enumerate([
        ('success', True, True), ('older_failure', True, False),
        ('latest_failure', True, False), ('active', False, False)]):
        root = tmp_path / 'diagnostics' / (str(index) * 32)
        root.mkdir(parents=True)
        (root / 'operation.json').write_text(json.dumps(dict(completed=completed, ok=ok)))
        (root / 'probe-result.json').write_text(json.dumps(dict(ok=ok)))
        os.utime(root, (old + index, old + index))
        roots[name] = root
    prune(tmp_path)
    assert not roots['success'].exists() and not roots['older_failure'].exists()
    assert roots['latest_failure'].exists() and roots['active'].exists()
