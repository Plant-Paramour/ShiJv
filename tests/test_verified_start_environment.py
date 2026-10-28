import json
from types import SimpleNamespace

import pytest

from apps.api import environment, launcher_memory
from apps.launcher import model_catalog, runtime_manager


@pytest.fixture
def verified_pair(tmp_path, monkeypatch):
    python = tmp_path / 'python.exe'
    python.write_bytes(b'python')
    (tmp_path / 'launcher.json').write_text(json.dumps({
        'local_enabled': True, 'inference_python': str(python),
    }))
    report = {'ok': True, 'python': str(python), 'inference_stamp': 'current',
              'device_uuid': 'GPU-123',
              'environment': {'ok': True, 'cuda': True,
                  'gpu': {'name': 'Test GPU', 'free_bytes': 1, 'capability': [8, 9]},
                  'quantization': ['none'], 'supported_model_types': ['qwen3']}}
    monkeypatch.setattr(launcher_memory, 'environment_stamp', lambda _: 'current')
    monkeypatch.setattr(model_catalog, 'verified_report', lambda *args: report)
    monkeypatch.setattr(environment, 'check_managed_runtime', lambda _: None)
    monkeypatch.setattr(environment, 'python_process_env', lambda _: {'CUDA_VISIBLE_DEVICES': 'GPU-123'})
    monkeypatch.setattr(runtime_manager, '_nvidia_smi', lambda: 'nvidia-smi')
    monkeypatch.setattr(environment.subprocess, 'run', lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout='0, GPU-123, Test GPU, 4096, 8192\n'))
    return tmp_path, report


def test_reuses_verified_capabilities_with_current_memory(verified_pair):
    directory, _ = verified_pair
    result = environment.verified_start_environment(directory, 'model')
    assert result['reused_verification'] is True
    assert result['gpu']['free_bytes'] == 4096 * 1024**2
    assert result['supported_model_types'] == ['qwen3']


@pytest.mark.parametrize('change', ['stamp', 'device', 'failed', 'missing_stamp'])
def test_untrusted_or_changed_verification_falls_back(verified_pair, change):
    directory, report = verified_pair
    if change == 'stamp':
        report['inference_stamp'] = 'old'
    elif change == 'device':
        report['device_uuid'] = 'GPU-other'
    elif change == 'failed':
        report['ok'] = False
    else:
        del report['inference_stamp']
    assert environment.verified_start_environment(directory, 'model') is None


def test_unavailable_live_gpu_info_falls_back(verified_pair, monkeypatch):
    directory, _ = verified_pair
    monkeypatch.setattr(runtime_manager, '_nvidia_smi', lambda: None)
    assert environment.verified_start_environment(directory, 'model') is None


def test_cpu_refreshes_available_memory(verified_pair, monkeypatch):
    directory, report = verified_pair
    report['environment'] = {'ok': True, 'cuda': False, 'cpu_memory_available_bytes': 1}
    import psutil
    monkeypatch.setattr(psutil, 'virtual_memory', lambda: SimpleNamespace(available=12345))
    assert environment.verified_start_environment(directory, 'model')['cpu_memory_available_bytes'] == 12345


def test_successful_full_recheck_refreshes_only_existing_verified_report(verified_pair, monkeypatch):
    directory, report = verified_pair
    path = directory / 'report.json'
    monkeypatch.setattr(model_catalog, 'report_path', lambda *args: path)
    current = {'ok': True, 'inference_python': report['python'], 'cuda': False,
               'cpu_memory_available_bytes': 12345}
    environment.refresh_verified_environment(directory, 'model', current)
    saved = json.loads(path.read_text(encoding='utf-8'))
    assert saved['inference_stamp'] == 'current'
    assert saved['environment']['cpu_memory_available_bytes'] == 12345
    path.unlink()
    report['ok'] = False
    environment.refresh_verified_environment(directory, 'model', current)
    assert not path.exists()
