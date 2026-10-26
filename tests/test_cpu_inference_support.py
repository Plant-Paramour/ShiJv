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
