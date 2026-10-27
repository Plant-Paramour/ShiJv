"""Executed only by the independent candidate Python, not host Conda."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import sys
import traceback


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--model")
    parser.add_argument("--model-report")
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cuda")
    parser.add_argument("--precision", choices=["none", "4bit"], default="4bit")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root / "app"))
    report = dict(ok=False, python=sys.executable, prefix=sys.prefix, sys_path=sys.path,
                  external_tools={n: shutil.which(n) for n in ("python", "nvcc", "cl", "gcc")},
                  release_approved=False)
    try:
        if any(Path(p).is_absolute() and not any(Path(p).resolve().is_relative_to(base) for base in (root, Path(sys.base_prefix).resolve())) for p in sys.path):
            raise RuntimeError("Candidate sys.path leaks outside runtime")
        from apps.launcher.environment_probe import probe
        report["environment"] = probe("inference")
        if not report["environment"].get("ok"):
            raise RuntimeError("Inference dependency/operation probe failed")
        if report["environment"]["cuda"] != (args.device == "cuda"):
            raise RuntimeError("Selected device does not match actual inference device")
        if args.device == "cuda" and args.precision == "4bit":
            backend = root / "Lib/site-packages/triton/backends/nvidia"
            compiler = root / "Lib/site-packages/triton/runtime/tcc/tcc.exe"
            for required in (compiler, backend / "bin/ptxas.exe", backend / "include/cuda.h", backend / "lib/x64/cuda.lib"):
                if not required.is_file():
                    raise RuntimeError(f"Bundled compiler component missing: {required}")
            os.environ["CC"] = str(compiler)
            os.environ["CUDA_PATH"] = str(backend)
            os.environ["TRITON_PTXAS_PATH"] = str(backend / "bin/ptxas.exe")
            import triton
            from triton.runtime.build import get_cc
            from triton.windows_utils import find_cuda, find_python
            report["compiler_resolution"] = dict(cc=get_cc(), cuda=find_cuda(), python_libraries=find_python())
            resolved = [get_cc(), *find_cuda()[1], *find_cuda()[2], *find_python()]
            if any(not any(Path(p).resolve().is_relative_to(base) for base in (root, Path(sys.base_prefix).resolve())) for p in resolved):
                raise RuntimeError("Compiler paths escape candidate runtime")
            triton_root = Path(triton.__file__).parent
            report["triton_bundled_tools"] = [str(p.relative_to(root)) for p in triton_root.rglob("*")
                                              if p.is_file() and p.name.lower() in {"tcc.exe", "ptxas.exe", "cuda.h", "cuda.lib"}]
            report["packages"] = {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()}
            # A genuine fresh JIT kernel ensures headers, Python import libs and
            # compiler are exercised, not just a precompiled CUDA tensor operation.
            import torch
            import triton.language as tl

            @triton.jit
            def add_kernel(x, y, z, N: tl.constexpr, BLOCK: tl.constexpr):
                i = tl.program_id(0) * BLOCK + tl.arange(0, BLOCK)
                tl.store(z + i, tl.load(x + i, i < N, 0) + tl.load(y + i, i < N, 0), i < N)

            x, y = torch.ones(1024, device="cuda"), torch.ones(1024, device="cuda")
            z = torch.empty_like(x)
            add_kernel[(1,)](x, y, z, 1024, 1024)
            torch.cuda.synchronize()
            if not torch.equal(z, x + y):
                raise RuntimeError("Fresh Triton JIT result mismatch")
            report["triton_fresh_jit"] = True
            del x, y, z
        if args.model:
            if not args.model_report:
                raise ValueError("--model-report required")
            from apps.launcher.model_probe import main as model_main
            sys.argv = ["model_probe", "--model", args.model, "--output", args.model_report,
                        "--data-dir", str(Path(args.output).parent / "candidate-model-checks"), "--precision", args.precision]
            os.chdir(root / "app")
            if model_main() != 0:
                raise RuntimeError("Actual model acceptance failed")
        import psutil
        report["loaded_modules"] = sorted({m.path for m in psutil.Process().memory_maps() if m.path})
        windows = Path(os.environ.get("SystemRoot", r"C:\Windows")).resolve()
        # Windows loads Defender inspection and language resource modules from
        # outside SystemRoot. Record these narrowly identified OS components.
        defender = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Microsoft/Windows Defender/Platform"
        windowsapps = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "WindowsApps"
        def os_component(path):
            p = Path(path).resolve()
            if p.name.casefold() == "mpoav.dll" and p.is_relative_to(defender):
                return True
            if p.suffix.casefold() == ".mui" and p.is_relative_to(windowsapps):
                return p.relative_to(windowsapps).parts[0].startswith("Microsoft.LanguageExperiencePack")
            return False
        report["additional_os_modules"] = [p for p in report["loaded_modules"] if os_component(p)]
        report["external_loaded_modules"] = [p for p in report["loaded_modules"]
            if Path(p).is_absolute() and not any(Path(p).resolve().is_relative_to(base) for base in (root, Path(sys.base_prefix).resolve()))
            and not Path(p).resolve().is_relative_to(windows)
            and not Path(p).resolve().is_relative_to(Path(os.environ["TRITON_CACHE_DIR"]).resolve().parent)
            and not os_component(p)]
        if report["external_loaded_modules"]:
            raise RuntimeError("Unexpected loaded modules outside runtime/Windows/cache")
        report["ok"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if not report["ok"]:
            print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stderr, flush=True)
            if args.model_report and Path(args.model_report).is_file():
                print(Path(args.model_report).read_text(encoding="utf-8"), file=sys.stderr, flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
