import json

import pytest

from apps.api.environment import assess_model


def _model(tmp_path, size):
    (tmp_path / "config.json").write_text(json.dumps({"model_type": "qwen3_5"}), encoding="utf-8")
    (tmp_path / "weights.safetensors").write_bytes(b"x" * size)
    return tmp_path


def test_cpu_model_assessment_selects_fp32_when_ram_is_sufficient(tmp_path):
    model = _model(tmp_path, 1024)
    environment = {
        "ok": True,
        "cuda": False,
        "cpu_memory_available_bytes": 8 * 1024**3,
        "quantization": ["none"],
        "supported_model_types": ["qwen3_5"],
    }

    plan = assess_model(model, environment)

    assert plan["quantization"] == "none"
    assert plan["label"] == "CPU FP32（原始权重）"


def test_bf16_uses_its_own_memory_budget_and_requires_operation_support(tmp_path):
    (tmp_path / 'config.json').write_text(json.dumps({'model_type': 'qwen3_5'}), encoding='utf-8')
    (tmp_path / 'model.safetensors.index.json').write_text(
        json.dumps({'metadata': {'total_size': 2 * 1024**3}}), encoding='utf-8')
    environment = dict(ok=True, cuda=False, cpu_memory_available_bytes=5 * 1024**3,
                       quantization=['none', 'bf16'], supported_model_types=['qwen3_5'])
    with pytest.raises(ValueError, match='系统可用内存不足'):
        assess_model(tmp_path, environment)
    assert assess_model(tmp_path, environment, precision='bf16')['quantization'] == 'bf16'
    environment['quantization'] = ['none']
    with pytest.raises(ValueError, match='CPU BF16 运算检查'):
        assess_model(tmp_path, environment, precision='bf16')
    environment.update(cuda=True, quantization=['none', 'bf16'])
    with pytest.raises(ValueError, match='CPU BF16 运算检查'):
        assess_model(tmp_path, environment, precision='bf16')


def test_cpu_bf16_plan_keeps_cpu_wheels_and_explicit_precision():
    from apps.launcher import deployer
    from apps.launcher.deployment_profiles import wheels
    from apps.launcher.runtime_manager import HostReport
    host = HostReport('windows', 'x64', 19045, 'D:/data', 100 * 1024**3, (), ())
    spec = deployer.official_model('Qwen/Qwen3.5-0.8B')
    assert deployer.choose_profile(host, spec, 'cpu-bf16') == 'cpu-bf16'
    plan = deployer.plan_policy(spec, 'official', 'cpu-bf16')
    assert plan['hardware']['device'] == 'cpu'
    assert plan['inference']['precision'] == 'bf16'
    assert 'cpu-bf16' in plan['verification']
    assert deployer.validate_plan(plan) == plan
    assert wheels('cpu-bf16') == wheels('cpu')


def test_cpu_model_assessment_rejects_when_ram_estimate_is_too_low(tmp_path):
    model = _model(tmp_path, 1024)
    environment = {
        "ok": True,
        "cuda": False,
        "cpu_memory_available_bytes": 1024,
        "quantization": ["none"],
        "supported_model_types": ["qwen3_5"],
    }

    with pytest.raises(ValueError, match="系统可用内存不足"):
        assess_model(model, environment)


def test_cpu_memory_error_shows_available_and_required_memory(tmp_path):
    (tmp_path / 'config.json').write_text(json.dumps(dict(model_type='qwen3_5')), encoding='utf-8')
    (tmp_path / 'model.safetensors.index.json').write_text(
        json.dumps(dict(metadata=dict(total_size=1746942600))), encoding='utf-8')
    environment = dict(ok=True, cuda=False, cpu_memory_available_bytes=944721920,
                       quantization=['none'], supported_model_types=['qwen3_5'])
    with pytest.raises(ValueError) as caught:
        assess_model(tmp_path, environment)
    assert '0.88 GiB' in str(caught.value)
    assert '5.18 GiB' in str(caught.value)
    assert '实测参考占用' in str(caught.value)
    assert '沙箱' not in str(caught.value)
    assert '虚拟机' not in str(caught.value)


@pytest.mark.parametrize("capability,vram,device", [(None, 0, "cpu"), ("6.1", 4, "cpu"), ("7.5", 4, "cuda"), ("8.9", 8, "cuda")])
def test_deployment_plan_selects_hardware_route(tmp_path, monkeypatch, capability, vram, device):
    from apps.launcher import deployer
    from apps.launcher.runtime_manager import GPU, HostReport
    spec = {"model_id": "Qwen/Qwen3.5-0.8B", "files": {"weights.safetensors": {"bytes": 1_600_000_000}}}
    monkeypatch.setattr(deployer, "official_model", lambda model_id: spec)
    monkeypatch.setattr(deployer, "verify_model", lambda *args: tmp_path)
    host = HostReport("windows", "x64", 19045, str(tmp_path), 100 * 1024**3,
        () if capability is None else (GPU(0, "gpu-test", "test", "570.0", vram * 1024**3, capability),),
        ("未找到 nvidia-smi",) if capability is None else ())
    from apps.launcher.resource_advice import eligible_gpu
    gpu = eligible_gpu(host)
    resources = dict(ram_total=32 * 1024**3, ram_available=20 * 1024**3,
                     gpu_uuid=gpu.uuid if gpu else None,
                     gpu_total=gpu.memory_bytes if gpu else None,
                     gpu_available=gpu.memory_bytes if gpu else None)
    plan = deployer.make_plan(tmp_path, "official", spec["model_id"], host=host, resources=resources)
    assert plan["hardware"]["device"] == device
    assert plan["inference"]["precision"] == "none"
    assert plan["profile"] == ("cpu" if device == "cpu" else "cuda-fp16")
    assert "triton-jit" not in plan["verification"]
    assert deployer.validate_plan(plan) == plan
