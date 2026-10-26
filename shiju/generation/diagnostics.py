"""Small, allowlisted diagnostics; never reads environment values or model weights."""
from __future__ import annotations

from collections import Counter
from importlib import metadata
import shutil


class ResourceSampler:
    """Single-request sampled process/device resources; no environment inspection."""
    def __init__(self, torch_module, interval=0.25):
        import threading
        self.torch = torch_module
        self.interval = interval
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.values = {"sample_count": 0, "interval_seconds": interval,
                       "rss_peak_bytes": 0, "cpu_peak_cores": 0.0,
                       "device_memory_peak_used_bytes": 0,
                       "gpu_utilization_peak_percent": 0.0,
                       "gpu_utilization_samples": []}

    def start(self):
        self.thread.start()
        return self

    def _run(self):
        import psutil
        import subprocess
        import time
        process = psutil.Process()
        process.cpu_percent()
        next_gpu = 0.0
        while True:
            try:
                self.values["sample_count"] += 1
                self.values["rss_peak_bytes"] = max(self.values["rss_peak_bytes"], process.memory_info().rss)
                self.values["cpu_peak_cores"] = max(self.values["cpu_peak_cores"], process.cpu_percent() / 100)
                if self.torch.cuda.is_initialized():
                    free, total = self.torch.cuda.mem_get_info()
                    self.values["device_memory_peak_used_bytes"] = max(self.values["device_memory_peak_used_bytes"], total - free)
                if time.monotonic() >= next_gpu:
                    result = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                                            capture_output=True, text=True, timeout=2, check=False)
                    if result.returncode == 0:
                        value = float(result.stdout.strip().splitlines()[0])
                        self.values["gpu_utilization_samples"].append(value)
                        self.values["gpu_utilization_peak_percent"] = max(self.values["gpu_utilization_peak_percent"], value)
                    next_gpu = time.monotonic() + 1
            except Exception as exc:
                self.values["last_sample_error_type"] = type(exc).__name__
            if self.stop_event.wait(self.interval):
                break

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=3)
        result = dict(self.values)
        samples = result.pop("gpu_utilization_samples")
        result["gpu_utilization_mean_percent"] = sum(samples) / len(samples) if samples else None
        result["gpu_utilization_sample_count"] = len(samples)
        result["peaks_are_sampled_not_absolute"] = True
        return result


def resident_resources(torch_module):
    import psutil
    result = {"rss_bytes": psutil.Process().memory_info().rss}
    if torch_module.cuda.is_available():
        free, total = torch_module.cuda.mem_get_info()
        result.update(device_memory_used_bytes=total-free,
                      gpu_allocated_bytes=torch_module.cuda.memory_allocated(),
                      gpu_reserved_bytes=torch_module.cuda.memory_reserved())
    return result


def runtime_diagnostics(model, torch_module):
    packages = {}
    for name in ("torch", "transformers", "bitsandbytes", "accelerate", "triton", "triton-windows",
                 "flash-linear-attention", "causal-conv1d", "safetensors"):
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    parameters = Counter()
    for parameter in model.parameters():
        parameters[f"{parameter.device}/{parameter.dtype}"] += parameter.numel()
    kernels = Counter()
    quantized_modules = Counter()
    quantized_weights = Counter()
    offload_hooks = Counter()
    for module in model.modules():
        name = type(module).__name__
        if name in {"Linear8bitLt", "Linear4bit"}:
            quantized_modules[name] += 1
            weight = getattr(module, "weight", None)
            if weight is not None:
                quantized_weights[f"{type(weight).__name__}/{weight.device}/{weight.dtype}"] += 1
        hook = getattr(module, "_hf_hook", None)
        if hook is not None and getattr(hook, "offload", False):
            offload_hooks[str(getattr(hook, "execution_device", None))] += 1
        if "GatedDeltaNet" in type(module).__name__:
            for attr in ("chunk_gated_delta_rule", "recurrent_gated_delta_rule", "causal_conv1d_fn", "causal_conv1d_update"):
                fn = getattr(module, attr, None)
                if fn is not None:
                    kernels[f"{attr}:{getattr(fn, '__module__', '?')}.{getattr(fn, '__name__', type(fn).__name__)}"] += 1
    config = getattr(model, "config", None)
    text_config = getattr(config, "text_config", config)
    result = {
        "packages": packages,
        "model_class": type(model).__name__,
        "parameter_elements_by_device_dtype": dict(parameters),
        "hf_device_map": {str(k): str(v) for k, v in getattr(model, "hf_device_map", {}).items()},
        "attention_implementation": getattr(text_config, "_attn_implementation", None),
        "linear_attention_callables": dict(kernels),
        "is_loaded_in_8bit": bool(getattr(model, "is_loaded_in_8bit", False)),
        "is_loaded_in_4bit": bool(getattr(model, "is_loaded_in_4bit", False)),
        "quantized_module_counts": dict(quantized_modules),
        "quantized_weight_counts": dict(quantized_weights),
        "offloading_hook_counts": dict(offload_hooks),
        "cuda_runtime": torch_module.version.cuda,
        "cuda_available": torch_module.cuda.is_available(),
    }
    if torch_module.cuda.is_available():
        props = torch_module.cuda.get_device_properties(0)
        result.update(gpu_device=props.name, gpu_compute_capability=[props.major, props.minor],
                      gpu_total_bytes=props.total_memory)
    return result


def storage_diagnostics(path):
    """Stat immediate files only; no full weight reads or bandwidth benchmark."""
    files = [entry for entry in path.iterdir() if entry.is_file()]
    usage = shutil.disk_usage(path)
    return {"file_count": len(files),
            "weight_file_bytes": sum(p.stat().st_size for p in files if p.suffix == ".safetensors"),
            "filesystem_free_bytes": usage.free,
            "filesystem_total_bytes": usage.total,
            "weight_read_seconds": None,
            "weight_read_seconds_note": "not isolated from from_pretrained; no IO benchmark performed"}


class TimedCallable:
    """Wall time includes synchronizations performed inside the wrapped callable."""
    def __init__(self, function):
        self.function = function
        self.calls = 0
        self.seconds = 0.0
        self.max_seconds = 0.0

    def __call__(self, input_ids, scores):
        import time
        started = time.perf_counter()
        try:
            return self.function(input_ids, scores)
        finally:
            elapsed = time.perf_counter() - started
            self.calls += 1
            self.seconds += elapsed
            self.max_seconds = max(self.max_seconds, elapsed)
