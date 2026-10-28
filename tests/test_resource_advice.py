import json

import pytest

from apps.launcher import resource_advice as advice
from apps.launcher import deployer
from apps.launcher.runtime_manager import HostReport, GPU


def resources(vram=8, free_vram=7, ram=32, free_ram=20):
    return dict(ram_total=int(ram * advice.GIB), ram_available=int(free_ram * advice.GIB),
                gpu_total=int(vram * advice.GIB) if vram else None,
                gpu_available=int(free_vram * advice.GIB) if vram else None,
                gpu_uuid='GPU-test' if vram else None, gpu_name='test' if vram else None,
                errors=[])


@pytest.mark.parametrize('vram,free,model,profile', [
    (4, 3.5, '2B', 'cuda-bnb'), (6, 5.5, '4B', 'cuda-bnb'),
    (8, 7, '4B', 'cuda-fp16'), (24, 22, '4B', 'cuda-fp16'),
    (0, 0, '0.8B', 'cpu'),
])
def test_recommendation_tiers(vram, free, model, profile):
    state = advice.recommended(resources(vram, free))
    assert state == dict(model_id='Qwen/Qwen3.5-' + model, profile=profile)


def test_current_usage_warns_without_downgrading_hardware_tier():
    r = resources(8, 7, ram=16, free_ram=7.6)
    assert advice.recommended(r) == dict(model_id='Qwen/Qwen3.5-4B', profile='cuda-fp16')
    assert advice.assess_selection(r, 'Qwen/Qwen3.5-4B', 'cuda-fp16')['state'] == 'recommended'
    r['gpu_available'] = advice.GIB
    assert advice.recommended(r)['profile'] == 'cuda-fp16'
    result = advice.assess_selection(r, 'Qwen/Qwen3.5-4B', 'cuda-fp16')
    assert result['state'] == 'tight'
    assert '不因此改变本机推荐档位' in result['text']
    r['gpu_available'] = None
    r['ram_available'] = None
    assert advice.recommended(r)['profile'] == 'cuda-fp16'


def test_total_capacity_and_actual_usage_have_separate_meanings():
    r = resources(0, 0, ram=12, free_ram=4)
    assert advice.recommended(r)['profile'] == 'cpu'
    result = advice.assess_selection(r, 'Qwen/Qwen3.5-0.8B', 'cpu')
    assert result['state'] == 'tight'
    assert '5.18' in result['text']
    assert '固定 8 GiB' not in result['text']
    assert result['requires_confirmation']
    assert advice.recommended(resources(0, 0, ram=8, free_ram=6)) is None


def test_recommended_loose_tight_and_risk_have_distinct_messages():
    r = resources()
    assert advice.assess_selection(r, 'Qwen/Qwen3.5-4B', 'cuda-fp16')['state'] == 'recommended'
    loose = advice.assess_selection(r, 'Qwen/Qwen3.5-2B', 'cuda-bnb')
    assert loose['state'] == 'usable' and '更大的模型' in loose['text']
    tight = advice.assess_selection(resources(6, 4, free_ram=5), 'Qwen/Qwen3.5-2B', 'cuda-fp16')
    assert tight['state'] == 'tight'
    risk = advice.assess_selection(resources(4, 3.5), 'Qwen/Qwen3.5-4B', 'cuda-fp16')
    assert risk['state'] == 'risk' and '8 GB 显存' in risk['text']


def test_4b_fp16_matches_user_product_baseline():
    for capacity in [8, 12, 24]:
        r = resources(capacity, 7, ram=16, free_ram=7)
        result = advice.assess_selection(r, 'Qwen/Qwen3.5-4B', 'cuda-fp16')
        assert result['state'] == 'recommended'
        assert '10 GiB' not in result['text']
        assert '16GB 系统内存＋8GB 显存' in result['text']


def test_bf16_is_not_a_default_recommendation():
    r = resources(0, 0)
    result = advice.assess_selection(r, 'Qwen/Qwen3.5-0.8B', 'cpu-bf16')
    assert result['state'] == 'usable'
    assert '0.08–0.12' in result['text']
    assert advice.recommended(r)['profile'] == 'cpu'
    # CPU defaults favor responsiveness; a larger manual model must not be
    # described as smaller than the recommended 0.8B.
    larger = advice.assess_selection(r, 'Qwen/Qwen3.5-2B', 'cpu')
    assert '以使用更大的模型' not in larger['text']


def test_local_model_name_does_not_borrow_official_measurements():
    result = advice.assess_selection(resources(), 'Qwen/Qwen3.5-4B', 'cuda-bnb', known=False,
                                    weight_bytes=20 * advice.GIB)
    assert result['state'] == 'unknown'
    assert '3.20' not in result['text']
    assert result['budget'] is None


def test_auto_preserves_selected_model_but_uses_its_best_fitting_profile():
    assert advice.auto_profile(resources(), 'Qwen/Qwen3.5-4B') == 'cuda-fp16'
    assert advice.auto_profile(resources(), 'Qwen/Qwen3.5-2B') == 'cuda-fp16'
    assert advice.auto_profile(resources(0, 0), 'Qwen/Qwen3.5-4B') == 'cpu'
    options = advice.snapshot(None, resources())['options']
    auto = next(x for x in options if x['model_id'].endswith('4B') and x['requested_profile'] == 'auto')
    assert auto['profile'] == 'cuda-fp16'


def test_profile_selection_uses_the_same_resource_snapshot_as_advice():
    host = HostReport('windows', 'x64', 19045, 'D:/', 100 * advice.GIB,
                      (GPU(0, 'GPU-test', 'test', '570.0', 8 * advice.GIB, '8.9'),), ())
    spec = deployer.official_model()
    assert deployer.choose_profile(host, spec, resources=resources()) == 'cuda-fp16'
    # Explicit selection is reviewable even when over budget; actual verification remains mandatory.
    assert deployer.choose_profile(host, spec, 'cuda-fp16', resources=resources()) == 'cuda-fp16'
    assert 'bnb-int8' in deployer.verification_policy('cuda-bnb8')


def test_exact_8bit_assessment_never_silently_uses_4bit(tmp_path):
    from apps.api.environment import assess_model
    (tmp_path / 'config.json').write_text(json.dumps({'model_type': 'qwen3_5'}))
    (tmp_path / 'model.safetensors.index.json').write_text(json.dumps(
        {'metadata': {'total_size': advice.WEIGHT_BYTES['4B']}}))
    environment = dict(ok=True, cuda=True, supported_model_types=['qwen3_5'],
                       quantization=['none', '4bit', '8bit'], gpu=dict(free_bytes=7 * advice.GIB))
    assert assess_model(tmp_path, environment, precision='8bit')['quantization'] == '8bit'
    environment['gpu']['free_bytes'] = int(4.5 * advice.GIB)
    with pytest.raises(ValueError, match='4.88 GiB'):
        assess_model(tmp_path, environment, precision='8bit')
    assert assess_model(tmp_path, environment)['quantization'] == '4bit'


def test_2b_fp16_uses_calibrated_budget(tmp_path):
    from apps.api.environment import assess_model
    (tmp_path / 'config.json').write_text(json.dumps({'model_type': 'qwen3_5'}))
    (tmp_path / 'model.safetensors.index.json').write_text(json.dumps(
        {'metadata': {'total_size': advice.WEIGHT_BYTES['2B']}}))
    environment = dict(ok=True, cuda=True, supported_model_types=['qwen3_5'],
                       quantization=['none'], gpu=dict(free_bytes=int(5.5 * advice.GIB)))
    assert assess_model(tmp_path, environment)['quantization'] == 'none'


def test_inspect_reports_advice_without_changing_plan_schema(tmp_path, monkeypatch):
    host = HostReport('windows', 'x64', 19045, 'D:/', 100 * advice.GIB, (), ())
    monkeypatch.setattr(deployer, 'inspect_host', lambda *args: host)
    monkeypatch.setattr(advice, 'collect_resources', lambda *args: resources(0, 0))
    monkeypatch.setattr(deployer, 'discover_models', lambda *args: [])
    monkeypatch.setattr(deployer, 'discover_environments', lambda: {})
    monkeypatch.setattr(deployer, 'check_vc_runtime', lambda *args, **kwargs: {'ok': True})
    report = tmp_path / 'report.json'
    monkeypatch.setattr('sys.argv', ['deployer', 'inspect', '--data-dir', str(tmp_path), '--report', str(report)])
    assert deployer.main() == 0
    result = json.loads(report.read_text(encoding='utf-8'))
    assert result['advisory']['recommendation']['profile'] == 'cpu'
    assert result['result']['host']['arch'] == 'x64'


def test_manual_risky_plan_is_saved_with_separate_review(tmp_path, monkeypatch):
    host = HostReport('windows', 'x64', 19045, 'D:/', 100 * advice.GIB,
                      (GPU(0, 'GPU-test', 'test', '570.0', 4 * advice.GIB, '8.6'),), ())
    monkeypatch.setattr(deployer, 'inspect_host', lambda *args: host)
    monkeypatch.setattr(advice, 'collect_resources', lambda *args: resources(4, 3.5))
    monkeypatch.setattr(deployer, 'verify_model', lambda *args: None)
    report, plan = tmp_path / 'report.json', tmp_path / 'plan.json'
    monkeypatch.setattr('sys.argv', ['deployer', 'plan', '--data-dir', str(tmp_path),
                                   '--report', str(report), '--plan', str(plan),
                                   '--model', str(tmp_path), '--profile', 'cuda-fp16'])
    assert deployer.main() == 0
    result = json.loads(report.read_text(encoding='utf-8'))
    assert result['advisory']['selection']['state'] == 'risk'
    assert '8 GB 显存' in result['advisory']['selection']['text']
    assert deployer.validate_plan(json.loads(plan.read_text(encoding='utf-8'))) == result['result']
    assert 'advisory' not in result['result']


def test_gpu_free_query_failure_never_uses_total_capacity(monkeypatch):
    from apps.launcher import runtime_manager
    host = HostReport('windows', 'x64', 19045, 'D:/', 100 * advice.GIB,
                      (GPU(0, 'GPU-test', 'test', '570.0', 8 * advice.GIB, '8.9'),), ())
    monkeypatch.setattr(runtime_manager, '_nvidia_smi', lambda: 'nvidia-smi')
    def fail(*args, **kwargs):
        raise OSError('query unavailable')
    monkeypatch.setattr(advice.subprocess, 'run', fail)
    result = advice.collect_resources(host)
    assert result['gpu_total'] == 8 * advice.GIB
    assert result['gpu_available'] is None
    assert result['errors']
