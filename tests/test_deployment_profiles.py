import hashlib
import json

import pytest

from apps.launcher import deployer
from apps.launcher.deployment_profiles import wheels, download_sources
from apps.launcher.runtime_manager import GPU, HostReport


def host(capability='8.9', vram=8, driver='570.0'):
    return HostReport('windows', 'x64', 19045, 'D:/data', 100 * 1024**3,
                      (GPU(0, 'test-gpu', 'test', driver, vram * 1024**3, capability),), ())


@pytest.mark.parametrize('capability,vram,driver,size,expected', [
    ('8.9', 8, '570.0', 9_000_000_000, 'cuda-bnb'),
    ('7.5', 4, '570.0', 1_600_000_000, 'cuda-fp16'),
    ('8.9', 8, '550.0', 1_600_000_000, 'cpu'),
    ('6.1', 8, '570.0', 1_600_000_000, 'cpu'),
    ('12.0', 32, '590.0', 1_600_000_000, 'cpu'),
    ('8.9', 2, '570.0', 9_000_000_000, 'cpu'),
])
def test_combination_selection(capability, vram, driver, size, expected):
    spec = dict(files={'weights': dict(bytes=size)})
    assert deployer.choose_profile(host(capability, vram, driver), spec) == expected
    assert deployer.choose_profile(host(), spec, 'cpu') == 'cpu'


def test_profiles_have_distinct_pinned_packages():
    cpu = {w['name']: w for w in wheels('cpu')}
    fp16 = {w['name']: w for w in wheels('cuda-fp16')}
    bnb = {w['name']: w for w in wheels('cuda-bnb')}
    assert cpu['torch']['version'] == '2.8.0+cpu'
    assert fp16['torch']['version'] == '2.8.0+cu126'
    assert cpu['torch']['bytes'] < fp16['torch']['bytes']
    for name in ('bitsandbytes', 'fla-core', 'flash-linear-attention', 'triton-windows'):
        assert name not in cpu and name not in fp16 and name in bnb


def test_sources_do_not_change_package_identity():
    item = next(w for w in wheels('cpu') if w['name'] == 'torch')
    before = item.copy()
    assert 'mirror.sjtu.edu.cn' in download_sources(item)[0]
    assert download_sources(item, 'official') == [item['source_url']]
    assert item == before


def test_plan_tampering_is_rejected(monkeypatch):
    spec = dict(model_id='Qwen/Qwen3.5-0.8B', files={'weights': dict(bytes=100)})
    monkeypatch.setattr(deployer, 'official_model', lambda model_id: spec)
    plan = deployer.plan_policy(spec, 'official', 'cpu')
    assert deployer.validate_plan(plan) == plan
    plan['dependencies'][0]['source_url'] = 'https://example.com/injected.whl'
    with pytest.raises(ValueError):
        deployer.validate_plan(plan)


def test_import_local_wheels_hashes_before_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(deployer, 'validate_plan', lambda plan: plan)
    monkeypatch.setattr(deployer, 'check_vc_runtime', lambda *args, **kwargs: dict(ok=True))
    source = tmp_path / 'local'
    source.mkdir()
    (source / 'good.whl').write_bytes(b'good')
    (source / 'bad.whl').write_bytes(b'bad!')
    items = [dict(filename=name, bytes=4, sha256=hashlib.sha256(b'good').hexdigest()) for name in ('good.whl', 'bad.whl')]
    cache = tmp_path / 'cache'
    result = deployer.import_wheels(dict(dependencies=items), source, cache, deployer.Operation(tmp_path / 'report.json'))
    assert result['accepted'] == ['good.whl'] and result['rejected'] == ['bad.whl']
    assert (cache / 'wheels/good.whl').read_bytes() == b'good'
    assert not (cache / 'wheels/bad.whl').exists()
    assert (source / 'bad.whl').read_bytes() == b'bad!'


@pytest.mark.parametrize('intact', [True, False])
def test_restart_rechecks_dependencies_before_skipping_install(tmp_path, monkeypatch, intact):
    target = tmp_path / 'environment'
    target.mkdir()
    model = tmp_path / 'model'
    model.mkdir()
    plan = dict(profile='cpu', route='official', model={}, hardware={},
                environment_required_bytes=0, download_bytes=0,
                dependencies=[dict(name='torch', version='2.8.0+cpu', filename='torch.whl')])
    plan_id = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    python = target / 'python.exe'
    python.write_bytes(b'fake interpreter')
    deployer.save(target / 'shiju-install.json', dict(plan_id=plan_id, state='incomplete'))
    deployer.save(target / 'deployment-checkpoints.json', dict(plan_id=plan_id,
        python_sha256=deployer.sha256_file(python), dependencies=True))
    monkeypatch.setattr(deployer, 'validate_plan', lambda plan: plan)
    monkeypatch.setattr(deployer, 'check_vc_runtime', lambda *args, **kwargs: dict(ok=True))
    monkeypatch.setattr(deployer, 'verify_model', lambda *args: model)
    monkeypatch.setattr(deployer, 'inspect_host', lambda *args: None)
    monkeypatch.setattr(deployer, 'compatibility', lambda *args: None)
    monkeypatch.setattr(deployer, 'check_install_space', lambda *args: None)
    monkeypatch.setattr(deployer, 'dependency_versions', lambda *args: {'torch': '2.8.0+cpu' if intact else 'wrong'})
    operation = deployer.Operation(tmp_path / 'report.json')
    downloaded, commands = [], []
    monkeypatch.setattr(operation, 'run', lambda cmd, **kwargs: commands.append(list(map(str, cmd))))
    def download(*args):
        downloaded.append(True)
        raise deployer.Cancelled('stop before downloading')
    monkeypatch.setattr(operation, 'download', download)
    monkeypatch.setattr(operation, 'download_item', download)
    original = operation.update
    def update(stage, **details):
        original(stage, **details)
        if stage == '验证 CPU / GPU 与真实约束生成':
            raise deployer.Cancelled('stop before model verification')
    monkeypatch.setattr(operation, 'update', update)
    with pytest.raises(deployer.Cancelled):
        deployer.install(plan, model, target, tmp_path / 'cache', operation, tmp_path / 'data')
    assert bool(downloaded) != intact
    assert any(cmd[-1] == 'check' for cmd in commands) == intact
    restored = deployer.restore_session(target, tmp_path / 'data')
    assert restored['plan'] == plan and restored['target'] == str(target)


@pytest.mark.parametrize('profile', ['cpu', 'cuda-fp16', 'cuda-bnb'])
def test_conda_export_is_standalone_and_matches_profile(tmp_path, profile):
    import subprocess
    import sys
    from apps.launcher.environment_export import export_environment
    plan = deployer.plan_policy(deployer.official_model('Qwen/Qwen3.5-0.8B'), 'official', profile)
    directory = tmp_path / 'export'
    export_environment(plan, directory)
    lock = (directory / 'requirements-offline.lock').read_text(encoding='utf-8')
    assert ('torch==2.8.0+cpu' in lock) == (profile == 'cpu')
    assert ('triton-windows==' in lock) == (profile == 'cuda-bnb')
    result = subprocess.run([sys.executable, str(directory / 'download-dependencies.py'), '--help'],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert '--source' in result.stdout
    assert '--no-index' in (directory / 'install-conda.ps1').read_text(encoding='utf-8-sig')
