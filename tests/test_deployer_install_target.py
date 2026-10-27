import hashlib
import json
from pathlib import Path

import pytest

from apps.launcher import deployer


@pytest.mark.parametrize('kind,allowed', [('missing', True), ('empty', True), ('resume', True), ('nonempty', False), ('foreign', False), ('verified', False), ('file', False)])
def test_install_target_acceptance_preserves_existing_content(tmp_path, monkeypatch, kind, allowed):
    plan = dict(profile='cpu', route='official', model={}, hardware={}, environment_required_bytes=0, download_bytes=0)
    plan_id = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    target = tmp_path / 'environment'
    model = tmp_path / 'model'
    model.mkdir()
    if kind == 'file':
        target.write_bytes(b'user file')
    elif kind != 'missing':
        target.mkdir()
        if kind in {'resume', 'foreign', 'verified'}:
            deployer.save(target / 'shiju-install.json', dict(plan_id='other' if kind == 'foreign' else plan_id,
                state='verified' if kind == 'verified' else 'incomplete'))
        if kind not in {'empty'}:
            (target / 'user.txt').write_bytes(b'keep me')
    before = {p.name: p.read_bytes() for p in target.iterdir()} if target.is_dir() else {}
    monkeypatch.setattr(deployer, 'validate_plan', lambda plan: plan)
    monkeypatch.setattr(deployer, 'check_vc_runtime', lambda *args, **kwargs: dict(ok=True))
    monkeypatch.setattr(deployer, 'verify_model', lambda *args: model)
    monkeypatch.setattr(deployer, 'inspect_host', lambda *args: None)
    monkeypatch.setattr(deployer, 'compatibility', lambda *args: None)
    monkeypatch.setattr(deployer, 'check_install_space', lambda *args: None)
    operation = deployer.Operation(tmp_path / 'report.json')
    reached_download = []
    def stop_download(*args):
        reached_download.append(True)
        raise deployer.Cancelled('test stops before network/install')
    monkeypatch.setattr(operation, 'download', stop_download)
    with pytest.raises(deployer.Cancelled if allowed else ValueError):
        deployer.install(plan, model, target, tmp_path / 'cache', operation, tmp_path / 'data')
    assert bool(reached_download) == allowed
    if allowed:
        assert deployer.read(target / 'shiju-install.json')['state'] == 'incomplete'
    elif target.is_dir():
        assert {p.name: p.read_bytes() for p in target.iterdir()} == before
    else:
        assert target.read_bytes() == b'user file'
    if 'user.txt' in before:
        assert (target / 'user.txt').read_bytes() == b'keep me'


@pytest.mark.parametrize('packages,version,accepted', [({}, [3,12,10], True), ({'torch':'2.8.0+cpu'}, [3,12,10], True), ({'torch':'2.7.0'}, [3,12,10], False), ({}, [3,11,9], False), ({'foreign':'1'}, [3,12,10], False)])
def test_existing_environment_preflight(tmp_path, monkeypatch, packages, version, accepted):
    target = tmp_path / 'env'
    target.mkdir()
    (target / 'python.exe').write_bytes(b'preserve interpreter')
    (target / 'conda-meta').mkdir()
    operation = deployer.Operation(tmp_path / 'report.json')
    def run(args, **kwargs):
        deployer.save(args[-1], dict(version=version, bits=64, prefix=str(target)))
    monkeypatch.setattr(operation, 'run', run)
    monkeypatch.setattr(deployer, 'dependency_versions', lambda *args: packages)
    plan = dict(dependencies=[dict(name='torch', version='2.8.0+cpu')])
    if accepted:
        result = deployer.inspect_existing_target(plan, target, operation)
        assert result['kind'] == 'existing'
        assert result['missing'] == ([] if packages else ['torch==2.8.0+cpu'])
    else:
        with pytest.raises(ValueError, match='空目录'):
            deployer.inspect_existing_target(plan, target, operation)
    assert (target / 'python.exe').read_bytes() == b'preserve interpreter'
    assert not (target / 'shiju-install.json').exists()


@pytest.mark.parametrize('missing,conflict', [(False, False), (True, False), (False, True)])
def test_managed_cpu_environment_recognized_after_plan_changes(tmp_path, monkeypatch, missing, conflict):
    target = tmp_path / 'mapped-qwen35'
    target.mkdir()
    (target / 'python.exe').write_bytes(b'existing interpreter')
    (target / 'python312._pth').write_text('.\nLib/site-packages\napp\nimport site\n')
    (target / 'app').mkdir()
    old = dict(profile='cpu', model='old-location', framework='previous-engine')
    old_id = hashlib.sha256(json.dumps(old, sort_keys=True).encode()).hexdigest()
    deployer.save(target / 'shiju-install.json', dict(plan_id=old_id, state='verified', ok=True))
    deployer.save(target / 'deployment-session.json', dict(plan=old, target='C:/Users/WDAGUtilityAccount/Desktop/qwen35'))
    operation = deployer.Operation(tmp_path / 'report.json')
    def run(args, **kwargs):
        deployer.save(args[-1], dict(version=[3,12,10], bits=64, prefix=str(target)))
    monkeypatch.setattr(operation, 'run', run)
    packages = {} if missing else {'torch': '2.7.0' if conflict else '2.8.0+cpu'}
    monkeypatch.setattr(deployer, 'dependency_versions', lambda *args: packages)
    plan = dict(profile='cpu', dependencies=[dict(name='torch', version='2.8.0+cpu')])
    before = (target / 'shiju-install.json').read_bytes()
    if conflict:
        with pytest.raises(ValueError, match='torch:'):
            deployer.inspect_existing_target(plan, target, operation)
    else:
        result = deployer.inspect_existing_target(plan, target, operation)
        assert result['kind'] == 'existing'
        assert result['external'] is False
        assert result['missing'] == (['torch==2.8.0+cpu'] if missing else [])
    assert (target / 'shiju-install.json').read_bytes() == before


def test_reuse_managed_environment_does_not_download_python_or_complete_dependencies(tmp_path, monkeypatch):
    target = tmp_path / 'env'
    target.mkdir()
    python = target / 'python.exe'
    python.write_bytes(b'keep existing interpreter')
    model = tmp_path / 'model'
    model.mkdir()
    plan = dict(profile='cpu', route='official', model={}, hardware={}, dependencies=[dict(name='torch', version='2.8.0+cpu')], environment_required_bytes=0, download_bytes=0)
    monkeypatch.setattr(deployer, 'validate_plan', lambda value: value)
    monkeypatch.setattr(deployer, 'ensure_vc_runtime', lambda *args: None)
    monkeypatch.setattr(deployer, 'verify_model', lambda *args: model)
    monkeypatch.setattr(deployer, 'inspect_host', lambda *args: None)
    monkeypatch.setattr(deployer, 'compatibility', lambda *args: None)
    monkeypatch.setattr(deployer, 'check_install_space', lambda *args: None)
    monkeypatch.setattr(deployer, 'inspect_existing_target', lambda *args: dict(kind='existing', external=False, python=str(python), missing=[]))
    monkeypatch.setattr(deployer, 'dependency_versions', lambda *args: {'torch':'2.8.0+cpu'})
    operation = deployer.Operation(tmp_path / 'report.json')
    commands = []
    monkeypatch.setattr(operation, 'run', lambda args, **kwargs: commands.append(list(map(str,args))))
    def forbidden(*args):
        pytest.fail('complete existing environment must not download Python or wheels')
    monkeypatch.setattr(operation, 'download', forbidden)
    monkeypatch.setattr(operation, 'download_item', forbidden)
    def update(stage, **kwargs):
        if stage == '验证 CPU / GPU 与真实约束生成':
            raise deployer.Cancelled('stop before model loading')
    monkeypatch.setattr(operation, 'update', update)
    with pytest.raises(ValueError, match='确认'):
        deployer.install(plan, model, target, tmp_path / 'cache', operation, tmp_path / 'data')
    assert not (target / 'shiju-install.json').exists()
    with pytest.raises(deployer.Cancelled):
        deployer.install(plan, model, target, tmp_path / 'cache', operation, tmp_path / 'data', continue_existing=True)
    assert python.read_bytes() == b'keep existing interpreter'
    assert any(command[-1] == 'check' for command in commands)
    assert not any('install' in command for command in commands)
    checkpoints = deployer.read(target / 'deployment-checkpoints.json')
    assert checkpoints['dependencies'] is True
    assert checkpoints['python_sha256'] == hashlib.sha256(python.read_bytes()).hexdigest()
