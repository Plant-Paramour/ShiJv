"""One environment policy for deployment, launcher probes and workers."""
import json
import os
from pathlib import Path
import tempfile


def isolated_environment(environment, report):
    environment = Path(environment)
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(
        ('PYTHON', 'CONDA', 'CUDA', 'TRITON', 'VIRTUAL_ENV', 'PIP_', 'HF_', 'HUGGINGFACE', 'TORCH', 'NVIDIA', 'VCINSTALL', 'VCTOOLS', 'WINDOWSSDK', 'VSINSTALL'))
        and k.upper() not in {'CC', 'CXX', 'INCLUDE', 'LIB', 'LIBPATH', 'CMAKE_PREFIX_PATH', 'CPATH'}}
    windows = Path(os.getenv('SystemRoot', 'C:/Windows'))
    env.update(PATH=os.pathsep.join(map(str, (environment, environment / 'Scripts', environment / 'Library/bin', windows / 'System32', windows))),
               PYTHONNOUSERSITE='1', PYTHONIOENCODING='utf-8', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
               TRITON_CACHE_DIR=str(Path(report).parent / 'jit-cache/triton'),
               TORCHINDUCTOR_CACHE_DIR=str(Path(report).parent / 'jit-cache/inductor'),
               PIP_CONFIG_FILE=os.devnull, PIP_CACHE_DIR=str(Path(report).parent / 'pip-cache'))
    return env


def python_environment(python):
    executable = Path(python)
    prefix = executable.parent.parent if executable.parent.name.lower() == 'scripts' else executable.parent
    env = os.environ.copy()
    env['PATH'] = os.pathsep.join(map(str, (prefix, prefix / 'Library/bin', prefix / 'Scripts'))) + os.pathsep + env.get('PATH', '')
    for name in ('PYTHONHOME', 'PYTHONPATH'):
        env.pop(name, None)
    env.update(PYTHONIOENCODING='utf-8', PYTHONNOUSERSITE='1')
    deployment = prefix / 'shiju-install.json'
    if deployment.is_file():
        state = json.loads(deployment.read_text(encoding='utf-8'))
        if state.get('state') == 'verified' and state.get('ok'):
            cache = Path(os.getenv('LOCALAPPDATA') or tempfile.gettempdir()) / 'ShiJu/runtime-cache'
            env = isolated_environment(prefix, cache / 'runtime.json')
            env.update(CUDA_VISIBLE_DEVICES=state.get('gpu_uuid') or '-1', CUDA_DEVICE_ORDER='PCI_BUS_ID')
            backend = prefix / 'Lib/site-packages/triton/backends/nvidia'
            env.update(CC=str(prefix / 'Lib/site-packages/triton/runtime/tcc/tcc.exe'),
                       CUDA_PATH=str(backend), TRITON_PTXAS_PATH=str(backend / 'bin/ptxas.exe'))
    return env
