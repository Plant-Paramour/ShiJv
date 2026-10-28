import sys
from types import SimpleNamespace

import pytest

from apps.launcher import deployer, resource_advice


@pytest.mark.parametrize('field', ['model', 'target'])
def test_preview_rejects_chinese_paths_before_any_deployment(tmp_path, monkeypatch, field):
    report = tmp_path / 'report.json'
    paths = {'model': str(tmp_path / 'model'), 'target': str(tmp_path / 'env')}
    paths[field] = str(tmp_path / '中文目录')
    monkeypatch.setattr(sys, 'argv', ['deployer', 'preview', '--data-dir', str(tmp_path),
        '--report', str(report), '--model', paths['model'], '--target', paths['target'],
        '--cache', str(tmp_path / 'cache'), '--plan', str(tmp_path / 'plan.json')])
    monkeypatch.setattr(deployer, 'inspect_host', lambda *_: pytest.fail('中文路径应在检查硬件前拒绝'))
    assert deployer.main() != 0
    assert '不能包含中文' in deployer.read(report)['error']


@pytest.mark.parametrize('name,expected', [
    ('Qwen3.5-2B', 'Qwen/Qwen3.5-2B'),
    ('my-Qwen3.5-4B-copy', 'Qwen/Qwen3.5-4B'),
    ('qwen3.5-0.8b', 'Qwen/Qwen3.5-0.8B'),
])
def test_named_local_model_uses_official_identity_without_pinned_hash(tmp_path, monkeypatch, name, expected):
    model = tmp_path / name
    model.mkdir()
    deployer.save(model / 'config.json', dict(model_type='qwen3_5'))
    (model / 'model.safetensors').write_bytes(b'local revision')
    monkeypatch.setattr(deployer, 'validate_model', lambda _: model)
    monkeypatch.setattr(deployer, 'official_model', lambda *_: pytest.fail('name recognition must not require a pinned revision'))
    selected = deployer.inspect_selected_model(model)
    assert selected['model']['model_id'] == expected
    assert selected['model']['files']['model.safetensors']['bytes'] == len(b'local revision')


@pytest.mark.parametrize('route', ['official', 'local'])
def test_preview_precedes_download_and_preserves_existing_plan(tmp_path, monkeypatch, route):
    report = tmp_path / 'report.json'
    plan = tmp_path / 'plan.json'
    plan.write_text('existing plan', encoding='utf-8')
    model = tmp_path / 'model'
    monkeypatch.setattr(sys, 'argv', ['deployer', 'preview', '--data-dir', str(tmp_path),
        '--report', str(report), '--model', str(model), '--target', str(tmp_path / 'env'),
        '--cache', str(tmp_path / 'cache'), '--plan', str(plan), '--route', route,
        '--model-id', 'Qwen/Qwen3.5-4B'])
    local_spec = deployer.official_model('Qwen/Qwen3.5-2B')
    monkeypatch.setattr(deployer, 'inspect_selected_model', lambda _: dict(route='local', model=local_spec))
    monkeypatch.setattr(deployer, 'inspect_host', lambda *_: None)
    monkeypatch.setattr(resource_advice, 'snapshot', lambda *_: dict(resources={}))
    monkeypatch.setattr(resource_advice, 'assess_selection', lambda *a, **k: dict(title='推荐'))
    monkeypatch.setattr(deployer, 'choose_profile', lambda *a: 'cpu')
    monkeypatch.setattr(deployer, 'check_install_space', lambda *a: None)
    monkeypatch.setattr(deployer, 'download_model', lambda *a: pytest.fail('preview must not download'))
    assert deployer.main() == 0
    result = deployer.read(report)
    assert result['ok'] and result['result']['download_bytes'] > 0
    assert result['result']['model_bytes'] > 0
    if route == 'local':
        assert result['result']['model']['model_id'] == 'Qwen/Qwen3.5-2B'
        assert result['result']['route'] == 'local'
    assert not model.exists()
    assert plan.read_text(encoding='utf-8') == 'existing plan'


def test_space_check_adds_model_cache_and_environment_on_shared_volume(tmp_path, monkeypatch):
    gib = 1024**3
    monkeypatch.setattr(deployer.shutil, 'disk_usage', lambda _: SimpleNamespace(free=100 * gib))
    # Each individual allocation fits, but simultaneous allocations do not.
    with pytest.raises(ValueError, match='111.0 GiB'):
        deployer.check_install_space(tmp_path / 'env', tmp_path / 'cache', 60 * gib, 30 * gib,
                                     tmp_path / 'model', 20 * gib)
    deployer.check_install_space(tmp_path / 'env', tmp_path / 'cache', 60 * gib, 10 * gib,
                                 tmp_path / 'model', 20 * gib)
