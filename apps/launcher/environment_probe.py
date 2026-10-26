"""Run with the selected interpreter. This file itself requires only stdlib."""
from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
from pathlib import Path
import platform
import sys
import traceback


def probe(mode="core"):
    result = {"python": sys.executable, "version": platform.python_version(), "mode": mode,
              "ok": False, "errors": [], "packages": {}, "cuda": False, "gpu": None,
              "quantization": [], "supported_model_types": [], "model_errors": {}}
    if sys.version_info < (3, 10):
        result["errors"].append("需要 Python 3.10 或以上版本")
        return result
    modules = {"fastapi": "fastapi", "pydantic": "pydantic", "uvicorn": "uvicorn",
               "PIL": "Pillow", "pypinyin": "pypinyin",
               "opencc": "opencc-python-reimplemented", "tqdm": "tqdm"} if mode == "core" else {
               "torch": "torch", "transformers": "transformers", "accelerate": "accelerate",
               "numpy": "numpy", "psutil": "psutil", "tqdm": "tqdm", "pypinyin": "pypinyin"}
    if mode == "core" and sys.version_info < (3, 11):
        modules["tomli"] = "tomli"
    for name, distribution in modules.items():
        try:
            importlib.import_module(name)
            result["packages"][distribution] = importlib.metadata.version(distribution)
        except Exception as exc:
            result["errors"].append(f"{distribution} 无法使用：{type(exc).__name__}: {exc}")
    if mode == "core" and not result["errors"]:
        try:
            from pydantic import BaseModel
            if not hasattr(BaseModel, "model_dump"):
                raise RuntimeError("需要 Pydantic 2.x")
            sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
            for name in ("apps.api.main", "apps.api.routes.public", "apps.api.routes.profile", "apps.api.routes.agent"):
                importlib.import_module(name)
        except Exception as exc:
            result["errors"].append(f"软件依赖检查失败：{type(exc).__name__}: {exc}")
    if mode == "inference" and not result["errors"]:
        try:
            import torch
            result["cuda"] = torch.cuda.is_available()
            if not result["cuda"]:
                result["errors"].append("当前本地生成引擎需要 NVIDIA CUDA；未检测到可用 CUDA，仍可使用对话服务")
            else:
                free, total = torch.cuda.mem_get_info()
                result["gpu"] = {"name": torch.cuda.get_device_name(0), "free_bytes": free,
                                 "total_bytes": total, "capability": list(torch.cuda.get_device_capability(0))}
                # Import success and driver discovery do not prove that the
                # wheel contains working kernels for this GPU.
                with torch.inference_mode():
                    operand = torch.ones((32, 32), device="cuda", dtype=torch.float16)
                    product = operand @ operand
                    torch.cuda.synchronize()
                    if not bool(torch.all(product == 32).item()):
                        raise RuntimeError("CUDA FP16 矩阵运算结果不正确")
                    del operand, product
                result["cuda_operation"] = "fp16_matmul_passed"
                result["quantization"] = ["none"]
                try:
                    import bitsandbytes
                    from bitsandbytes.cextension import lib
                    if lib is None or not getattr(lib, "compiled_with_cuda", False):
                        raise RuntimeError("bitsandbytes 未加载 CUDA 后端")
                    result["packages"]["bitsandbytes"] = importlib.metadata.version("bitsandbytes")
                    if result["gpu"]["capability"][0] >= 6:
                        with torch.inference_mode():
                            layer = bitsandbytes.nn.Linear4bit(64, 64, bias=False,
                                compute_dtype=torch.float16, quant_type="nf4").to("cuda")
                            sample = torch.ones((1, 64), device="cuda", dtype=torch.float16)
                            output = layer(sample)
                            torch.cuda.synchronize()
                            if not bool(torch.isfinite(output).all().item()):
                                raise RuntimeError("BNB 4bit 运算结果包含无效数值")
                            del layer, sample, output
                        result["quantization"].append("4bit")
                        result["bnb_operation"] = "nf4_linear_passed"
                except Exception as exc:
                    result["quantization_note"] = f"4bit 不可用：{type(exc).__name__}: {exc}"
            from transformers import AutoConfig, AutoModelForCausalLM, AutoModelForImageTextToText
            from transformers.models.auto.configuration_auto import CONFIG_MAPPING_NAMES
            # These are the two loading paths implemented by ModelRunner. Check
            # the selected interpreter's actual model classes, not an old allowlist.
            for kind, factory in (("qwen3", AutoModelForCausalLM), ("qwen3_5", AutoModelForImageTextToText)):
                try:
                    if kind not in CONFIG_MAPPING_NAMES:
                        raise RuntimeError("当前 Transformers 未注册此架构")
                    configuration = AutoConfig.for_model(kind)
                    factory._model_mapping[type(configuration)]
                    result["supported_model_types"].append(kind)
                except Exception as exc:
                    result["model_errors"][kind] = f"{type(exc).__name__}: {exc}"
            if not result["supported_model_types"]:
                result["errors"].append("所选环境无法加载程序已实现的 Qwen3 / Qwen3.5 架构：" + str(result["model_errors"]))
        except Exception as exc:
            result["errors"].append(f"推理后端检查失败：{type(exc).__name__}: {exc}")
    result["ok"] = not result["errors"]
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["core", "inference"], default="core")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        result = probe(args.mode)
    except Exception:
        result = {"ok": False, "errors": [traceback.format_exc()]}
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
