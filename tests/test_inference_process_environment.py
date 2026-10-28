import json
import os

from apps.api.environment import python_process_env
from apps.launcher.environment_probe import exception_chain


def test_managed_runtime_uses_acceptance_environment(tmp_path, monkeypatch):
    prefix = tmp_path / '中文环境'
    prefix.mkdir()
    (prefix / 'shiju-install.json').write_text(json.dumps(dict(state='verified', ok=True, gpu_uuid='GPU-test')))
    for key in ('PYTHONHOME', 'PYTHONPATH', 'CONDA_PREFIX', 'CUDA_PATH', 'TRITON_CACHE_DIR', 'CC'):
        monkeypatch.setenv(key, 'old-broken-directory')
    monkeypatch.setenv('PATH', 'old-broken-directory')
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    env = python_process_env(prefix / 'python.exe')
    assert 'old-broken-directory' not in env.values()
    assert 'old-broken-directory' not in env['PATH']
    assert env['CUDA_VISIBLE_DEVICES'] == 'GPU-test'
    assert env['PYTHONNOUSERSITE'] == '1'
    assert env['HF_HUB_OFFLINE'] == '1'
    assert env['TRITON_CACHE_DIR'].startswith(str(tmp_path / 'local'))
    assert str(prefix) in env['CC']
    assert 'PYTHONPATH' not in env
    assert 'CONDA_PREFIX' not in env


def test_model_import_error_includes_underlying_dependency():
    try:
        try:
            raise OSError('DLL load failed: missing dependency.dll')
        except OSError as cause:
            raise ModuleNotFoundError('Could not import Qwen3ForCausalLM') from cause
    except ModuleNotFoundError as exc:
        detail = exception_chain(exc)
    assert 'Qwen3ForCausalLM' in detail
    assert 'missing dependency.dll' in detail


def test_unmanaged_environment_keeps_conda_path(tmp_path, monkeypatch):
    monkeypatch.setenv('PATH', 'user-path')
    env = python_process_env(tmp_path / 'python.exe')
    assert env['PATH'].endswith(os.pathsep + 'user-path')


def test_frontend_probe_uses_isolated_selected_python(tmp_path, monkeypatch):
    from apps.api import environment
    import subprocess

    python = tmp_path / 'python.exe'
    python.touch()
    (tmp_path / 'launcher.json').write_text(json.dumps(dict(inference_python=str(python))))
    seen = []

    def run(self, command, **kwargs):
        seen.append((command, kwargs))
        output = command[command.index('--output') + 1]
        from pathlib import Path
        Path(output).write_text(json.dumps(dict(ok=True, supported_model_types=['qwen3_5'])))
        return 0

    from apps.launcher.diagnostics import OperationDiagnostic
    monkeypatch.setattr(OperationDiagnostic, 'run', run)
    result = environment.inspect_environment(tmp_path, refresh=True)
    assert result['ok']
    assert seen[0][0][:2] == [str(python), '-I']
    assert 'PYTHONPATH' not in seen[0][1]['env']
