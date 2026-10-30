"""Persist GPU failures only for the hardware and deployment code tested."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def signature(host):
    digest = hashlib.sha256()
    for folder in ('apps/launcher', 'tools'):
        for path in sorted(p for p in (ROOT / folder).iterdir() if p.suffix in ('.py', '.ps1', '.cs')):
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    for path in sorted((ROOT / 'deploy').rglob('*.json')):
        digest.update(path.read_bytes())
    return dict(tool=digest.hexdigest(), windows_build=host.windows_build, gpus=[dict(uuid=g.uuid, driver=g.driver_version)
                                            for g in host.gpus])


def failure(data, host):
    try:
        value = json.loads((Path(data) / 'deployment/gpu-health.json').read_text(encoding='utf-8'))
        return value if value.get('signature') == signature(host) and not value.get('ok') else None
    except (OSError, ValueError):
        return None


def save_health(data, host, result):
    from .deployer import save
    save(Path(data) / 'deployment/gpu-health.json', dict(signature=signature(host), **result))


def advice(host, data):
    from .resource_advice import snapshot, collect_resources
    previous = failure(data, host)
    if not previous:
        result = snapshot(host)
        try:
            old = json.loads((Path(data) / 'deployment/gpu-health.json').read_text(encoding='utf-8'))
            if old.get('signature') != signature(host):
                result['summary'] = '驱动、硬件或部署工具已变化，GPU 需重新验证；安装时会自动复验。\n' + result['summary']
        except (OSError, ValueError):
            pass
        return result
    resources = collect_resources(host)
    if previous:
        resources.update(gpu_uuid=None, gpu_total=None, gpu_available=None)
    result = snapshot(host, resources)
    if previous:
        notice = '此前 GPU 验证失败，当前推荐 CPU。具体原因：' + previous['error']
        result['summary'] = notice + '\n' + result['summary']
        result['gpu_failure'] = previous
        for option in result['options']:
            option['text'] = notice + '\n' + option['text']
            option['notice'] = notice + '\n' + option['notice']
            if option['profile'].startswith('cuda'):
                option.update(notice=notice + '\n继续将重新检测 GPU。', requires_confirmation=True)
    return result


def check(python, env, visible):
    # -S bypasses persisted sitecustomize settings from earlier attempts. Each
    # candidate runs in a fresh process, before CUDA can cache device visibility.
    code = '''import sys,json,traceback,os
from pathlib import Path
p=Path(sys.executable).parent
if p.name.lower()=='scripts': p=p.parent
sys.path.insert(0,str(p/'Lib/site-packages'))
r={'ok':False,'visible':os.environ.get('CUDA_VISIBLE_DEVICES')}
try:
 import torch
 r.update(torch=torch.__version__,cuda_version=torch.version.cuda)
 torch.cuda.init()
 x=torch.ones((32,32),device='cuda')
 y=x@x
 torch.cuda.synchronize()
 assert bool((y==32).all().item()), 'GPU 运算结果不正确'
 r.update(ok=True,count=torch.cuda.device_count(),name=torch.cuda.get_device_name(0))
except Exception:
 r['error']=traceback.format_exc()
print(json.dumps(r))
'''
    child = dict(env)
    child.pop('CUDA_VISIBLE_DEVICES', None)
    if visible is not None:
        child['CUDA_VISIBLE_DEVICES'] = visible
    # Also reset inside the fresh interpreter, before importing torch, so an
    # older owned sitecustomize cannot silently undo this diagnostic attempt.
    code = ('import os\nos.environ.pop("CUDA_VISIBLE_DEVICES", None)\n' if visible is None else
            'import os\nos.environ["CUDA_VISIBLE_DEVICES"]=' + repr(visible) + '\n') + code
    try:
        p = subprocess.run([str(python), '-I', '-S', '-c', code], env=child,
                           capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        result = json.loads(p.stdout.strip().splitlines()[-1])
        result['stderr'] = p.stderr
        return result
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired) as exc:
        return dict(ok=False, error=str(exc), visible=visible)


def validate(python, env, gpu, host):
    attempts = [check(python, env, gpu.uuid)]
    if attempts[-1]['ok']:
        return dict(ok=True, visible=gpu.uuid, attempts=attempts)
    attempts.append(check(python, env, None))
    # Only repair visibility automatically when there is no ambiguity about
    # which physical GPU was exercised, and verify the repaired setting too.
    if attempts[-1]['ok'] and len(host.gpus) == 1 and attempts[-1].get('count') == 1:
        attempts.append(check(python, env, '0'))
        if attempts[-1]['ok']:
            return dict(ok=True, visible='0', repaired=True, attempts=attempts)
    error = '\n\n'.join('显卡设置：' + str(a.get('visible')) + '\n' + a.get('error', 'GPU 可用，但无法安全确认所选显卡身份') for a in attempts)
    return dict(ok=False, error=error, attempts=attempts)
