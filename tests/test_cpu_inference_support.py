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
    assert '5.73 GiB' in str(caught.value)
    assert '并非实测峰值' in str(caught.value)
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
    plan = deployer.make_plan(tmp_path, "official", spec["model_id"], host=host)
    assert plan["hardware"]["device"] == device
    assert plan["inference"]["precision"] == "none"
    assert plan["profile"] == ("cpu" if device == "cpu" else "cuda-fp16")
    assert "triton-jit" not in plan["verification"]
    assert deployer.validate_plan(plan) == plan
