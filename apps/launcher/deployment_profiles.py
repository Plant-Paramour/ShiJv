"""Bundled, pinned Windows runtime combinations. No external policy execution."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent.parent
PROFILES = {
    'cpu': dict(device='cpu', precision='none', label='CPU FP32', disk_gib=6),
    'cpu-bf16': dict(device='cpu', precision='bf16', label='CPU BF16（待本机验收）', disk_gib=6),
    'cuda-fp16': dict(device='cuda', precision='none', label='GPU FP16（通用运算）', disk_gib=18),
    'cuda-bnb': dict(device='cuda', precision='4bit', label='GPU BNB + Triton', disk_gib=18),
}
# Compatibility alias for old deployment plans; quantization is a runtime choice.
PROFILES['cuda-bnb8'] = dict(PROFILES['cuda-bnb'], precision='8bit')
# New architectures require another wheel combination, not an unbounded >= test.
CUDA_ARCHITECTURES = ['7.5', '8.0', '8.6', '8.9', '9.0']
GPU_ONLY = {'bitsandbytes', 'fla-core', 'flash-linear-attention', 'triton-windows'}


def wheels(profile='cuda-bnb'):
    if profile not in PROFILES:
        raise ValueError('未知依赖组合，请重新生成清单')
    items = json.loads((ROOT / 'deploy/managed-runtime/windows-qwen35-bnb4-wheels.json').read_text(encoding='utf-8'))
    if profile not in {'cuda-bnb', 'cuda-bnb8'}:
        items = [item for item in items if item['name'] not in GPU_ONLY]
    if PROFILES[profile]['device'] == 'cpu':
        pin = json.loads((ROOT / 'deploy/environment-tool/cpu-torch.json').read_text(encoding='utf-8'))
        items = [pin if item['name'] == 'torch' else item for item in items]
    return items


def download_sources(item, preference='auto'):
    """Mirrors change transport only; package identity remains the official hash."""
    official = item['source_url']
    parsed = urlsplit(official)
    mirror = None
    if parsed.hostname in {'download.pytorch.org', 'download-r2.pytorch.org'} and parsed.path.startswith('/whl/'):
        mirror = urlunsplit(('https', 'mirror.sjtu.edu.cn', '/pytorch-wheels/' + parsed.path[5:], '', ''))
    elif parsed.hostname == 'files.pythonhosted.org' and parsed.path.startswith('/packages/'):
        mirror = urlunsplit(('https', 'mirrors.tuna.tsinghua.edu.cn', '/pypi/web' + parsed.path, '', ''))
    if preference == 'official' or not mirror:
        return [official]
    return [mirror, official]
