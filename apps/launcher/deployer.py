"""Small Windows deployment engine. Only stdlib is needed before installation.

Imported plans are data, never installation commands. Publisher URLs and pins
come exclusively from application-bundled policy files.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from urllib.parse import urlencode
import uuid
import zipfile

from .runtime_manager import inspect_host
from .runtime_manifest import relative_path, sha256_file, version_tuple
from .model_catalog import validate_model

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / 'deploy/environment-tool'
PYTHON_SHA256 = '0eb85c2dfccccf1b17352de4c397f69194035b7d37149eacc16f1147d93de3b8'
PYTHON_URL = 'https://api.nuget.org/v3-flatcontainer/python/3.12.10/python.3.12.10.nupkg'
WARNING = '用户自带模型不保证兼容，有问题可导出诊断信息联系开发者。'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def engine_digest():
    digest = hashlib.sha256()
    for folder in ('shiju', 'apps/gpu_worker'):
        for path in sorted((ROOT / folder).rglob('*.py')):
            digest.update(path.relative_to(ROOT).as_posix().encode())
            digest.update(path.read_bytes())
    for name in ('model_catalog.py', 'model_probe.py', 'environment_probe.py'):
        path = ROOT / 'apps/launcher' / name
        digest.update(name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def wheels():
    return read(ROOT / 'deploy/managed-runtime/windows-qwen35-bnb4-wheels.json')


def official_model():
    return read(POLICY / 'model.json')


def model_bytes(spec):
    return sum(int(item['bytes']) for item in spec.get('files', {}).values())


def discover_environments(extra=()):
    """Discovered paths and independently usable interpreters are distinct."""
    candidates = {str(Path(p).absolute()) for p in extra if p}
    candidates.update((sys.executable, str(ROOT / '.venv/Scripts/python.exe'), str(ROOT / 'venv/Scripts/python.exe')))
    conda = Path.home() / '.conda/environments.txt'
    if conda.is_file():
        candidates.update(str(Path(p.strip()) / 'python.exe') for p in conda.read_text(encoding='utf-8-sig', errors='replace').splitlines() if p.strip())
    for base in (Path(os.getenv('ProgramData', 'C:/ProgramData')) / 'anaconda3',
                 Path.home() / 'anaconda3', Path.home() / 'miniconda3',
                 Path(os.getenv('LOCALAPPDATA', str(Path.home()))) / 'Programs/Python'):
        candidates.add(str(base / 'python.exe'))
        candidates.update(str(p) for p in base.glob('envs/*/python.exe'))
        candidates.update(str(p) for p in base.glob('*/python.exe'))
    for name in ('python.exe', 'python', 'python3'):
        if found := shutil.which(name):
            if 'windowsapps' not in found.lower():
                candidates.add(found)
    result = []
    for candidate in sorted(candidates, key=str.casefold):
        if not Path(candidate).is_file():
            continue
        item = dict(path=candidate, discovered=True, usable=False)
        try:
            process = subprocess.run([candidate, '-I', '-c',
                'import json,sys,platform;print(json.dumps(dict(version=platform.python_version(),prefix=sys.prefix,base_prefix=sys.base_prefix,architecture=platform.machine())))'],
                capture_output=True, text=True, timeout=10, creationflags=_flags())
            if process.returncode:
                raise ValueError('解释器退出码 ' + str(process.returncode))
            item.update(json.loads(process.stdout), usable=True)
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            item['error'] = str(exc)
        result.append(item)
    conda_tools = set()
    if command := shutil.which('conda'):
        conda_tools.add(command)
    for candidate in candidates:
        prefix = Path(candidate).parent
        bases = [prefix]
        if prefix.parent.name.lower() == 'envs':
            bases.append(prefix.parent.parent)
        for base in bases:
            for relative in ('Scripts/conda.exe', 'condabin/conda.bat'):
                if (base / relative).is_file():
                    conda_tools.add(str(base / relative))
    return dict(environments=result, conda=shutil.which('conda'), conda_tools=sorted(conda_tools), default='创建诗矩专用环境')


def _flags():
    return subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


class Cancelled(RuntimeError):
    pass


class Operation:
    def __init__(self, report, cancel=None):
        self.report = Path(report)
        self.cancel = Path(cancel) if cancel else None
        self.stage = '准备'
        self.events = []

    def check(self):
        if self.cancel and self.cancel.exists():
            raise Cancelled('操作已取消；文件和日志保留，点击重试继续')

    def update(self, stage, **details):
        self.check()
        self.stage = stage
        save(self.report, dict(ok=False, state='running', stage=stage, **details))

    def run(self, command, *, env=None, timeout=1800):
        self.check()
        log = self.report.parent / ('process-' + uuid.uuid4().hex + '.log')
        with log.open('w', encoding='utf-8') as stream:
            process = subprocess.Popen(list(map(str, command)), cwd=ROOT, env=env,
                stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, creationflags=_flags())
            start = time.monotonic()
            try:
                while process.poll() is None:
                    self.check()
                    if time.monotonic() - start > timeout:
                        raise RuntimeError('本阶段超时，请查看日志后重试')
                    time.sleep(.2)
            except BaseException:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, creationflags=_flags())
                else:
                    process.kill()
                process.wait(timeout=30)
                raise
        if process.returncode:
            raise RuntimeError(f'阶段“{self.stage}”失败（退出码 {process.returncode}），日志：{log}')
        return log

    def download(self, url, destination, digest, size=None):
        """Hash every reused file. Resume only on a correct Content-Range."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.check()
        if destination.is_file() and (size is None or destination.stat().st_size == size) and sha256_file(destination) == digest:
            return
        part = destination.with_name(destination.name + '.part')
        offset = part.stat().st_size if part.exists() else 0
        if size is not None and offset >= size:
            if offset == size and sha256_file(part) == digest:
                part.replace(destination)
                return
            part.unlink()
            offset = 0
        request = urllib.request.Request(url, headers={'Range': f'bytes={offset}-'} if offset else {})
        with urllib.request.urlopen(request, timeout=30) as source:
            if offset and (source.status != 206 or not source.headers.get('Content-Range', '').startswith(f'bytes {offset}-')):
                if source.status != 200:
                    raise ValueError('服务器返回无效断点范围，请重试')
                offset = 0
            received = offset
            last = 0
            with part.open('ab' if offset else 'wb') as target:
                while True:
                    self.check()
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    target.write(block)
                    received += len(block)
                    if size is not None and received > size:
                        raise ValueError('下载内容超过清单大小')
                    if time.monotonic() - last > .5:
                        self.update(self.stage, file=destination.name, received_bytes=received, total_bytes=size)
                        last = time.monotonic()
        if (size is not None and part.stat().st_size != size) or sha256_file(part) != digest:
            part.unlink()  # corrupt partial data must not be appended on retry
            raise ValueError(f'{destination.name} 下载校验失败，请重试重新获取')
        part.replace(destination)


def check_space(path, required):
    existing = Path(path).resolve()
    while not existing.exists():
        existing = existing.parent
    free = shutil.disk_usage(existing).free
    if free < required:
        raise ValueError(f'磁盘空间不足：{path} 需要约 {required / 1024**3:.1f} GiB，可用 {free / 1024**3:.1f} GiB')


def check_install_space(target, cache, environment_bytes, download_bytes):
    """Add simultaneous allocations when cache and environment share a volume."""
    volumes = {}
    for path, required in ((target, environment_bytes + 1024**3), (cache, download_bytes)):
        existing = Path(path).resolve()
        while not existing.exists():
            existing = existing.parent
        key = existing.stat().st_dev
        entry = volumes.setdefault(key, [existing, 0])
        entry[1] += required
    for path, required in volumes.values():
        check_space(path, required)


def discover_models(data_directory):
    """Bounded discovery of known storage roots; never call a path 'verified'."""
    from .model_catalog import scan
    directory = Path(data_directory)
    paths = []
    config = directory / 'runtime.json'
    if config.is_file():
        try:
            selected = read(config).get('model_path')
            if selected and (Path(selected) / 'config.json').is_file():
                paths.append(str(Path(selected).resolve()))
        except (OSError, ValueError):
            pass
    for root in (directory / 'deployment/models', Path.home() / '.cache/modelscope/hub/models', ROOT / 'models'):
        paths.extend(scan([root]))
    return list(dict.fromkeys(paths))


def inspect_selected_model(directory):
    """Existing official weights keep official identity only after full hashing."""
    try:
        verify_model(directory, official_model())
        return dict(route='official', model=official_model(), label='已核验的官方 Qwen3.5-4B，可直接复用')
    except (OSError, ValueError):
        spec = local_model(directory)
        return dict(route='local', model=spec, label=WARNING)


def verify_model(model, spec):
    root = validate_model(str(Path(model).resolve()))
    for name, item in spec['files'].items():
        relative_path(name)
        path = root / name
        if not path.resolve().is_relative_to(root) or not path.is_file() or path.stat().st_size != item['bytes'] or sha256_file(path) != item['sha256']:
            raise ValueError('模型文件缺失或损坏：' + name)
    inputs = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.suffix in {'.json', '.safetensors', '.bin', '.model', '.txt', '.jinja', '.py'}}
    if inputs - spec['files'].keys():
        raise ValueError('模型存在未登记的输入文件，不能继承原清单：' + ', '.join(sorted(inputs - spec['files'].keys())))
    return root


def download_model(directory, operation):
    spec = official_model()
    directory = Path(directory).resolve()
    missing = sum(v['bytes'] for n, v in spec['files'].items()
                  if not (directory / n).is_file() or (directory / n).stat().st_size != v['bytes']
                  or sha256_file(directory / n) != v['sha256'])
    check_space(directory, missing + 512 * 1024**2)
    for name, item in spec['files'].items():
        relative_path(name)
        operation.update('下载官方模型', file=name)
        url = 'https://modelscope.cn/api/v1/models/Qwen/Qwen3.5-4B/repo?' + urlencode(dict(Revision=spec['revision'], FilePath=name))
        operation.download(url, directory / name, item['sha256'], item['bytes'])
    operation.update('核验完整模型')
    verify_model(directory, spec)
    return spec


def local_model(directory):
    root = validate_model(str(Path(directory).resolve()))
    config = read(root / 'config.json')
    if config.get('auto_map'):
        raise ValueError('不支持需要执行自定义代码的模型（auto_map）')
    if config.get('quantization_config'):
        raise ValueError('不支持预量化模型，请选择原始浮点权重')
    if config.get('model_type') not in {'qwen3', 'qwen3_5'}:
        raise ValueError(f'当前加载器没有适配架构：{config.get("model_type", "未知")}')
    files = {}
    for path in sorted(root.rglob('*')):
        if path.is_file() and '.git' not in path.relative_to(root).parts and path.suffix in {'.json', '.safetensors', '.bin', '.model', '.txt', '.jinja', '.py'}:
            name = relative_path(path.relative_to(root).as_posix())
            if not path.resolve().is_relative_to(root):
                raise ValueError('模型文件不能指向目录外')
            files[name] = dict(sha256=sha256_file(path), bytes=path.stat().st_size)
    revision = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    return dict(model_id=root.name, revision=revision, source_url='local', model_type=config['model_type'], files=files)


def requirements():
    # Until broader target tests exist, only the observed GPU generation is
    # offered as a candidate. Neither driver floor nor VRAM is a release claim.
    return dict(os='windows', arch='x64', min_windows_build=19041,
                min_driver='560.76', compute_capabilities=['8.9'], min_vram_bytes=8_000_000_000,
                scope='开发机候选规则，其他 GPU 尚未验证')


def compatibility(host, rules):
    problems = []
    if host.os != rules['os'] or host.arch != rules['arch']:
        problems.append('目前只支持 Windows x64')
    if host.windows_build < rules['min_windows_build']:
        problems.append('Windows 版本过低')
    devices = [g for g in host.gpus if g.compute_capability in rules['compute_capabilities']
               and version_tuple(g.driver_version) >= version_tuple(rules['min_driver'])
               and g.memory_bytes >= rules['min_vram_bytes']]
    if not devices:
        problems.append('没有符合候选规则的 NVIDIA GPU：需要计算能力 8.9、至少 8GB 显存和 560.76 以上驱动；其他 GPU 尚未验证')
    if problems:
        raise ValueError('；'.join(problems + list(host.errors)))
    return devices[0]


def make_plan(model, route):
    if route not in {'official', 'local'}:
        raise ValueError('未知模型路线')
    spec = official_model() if route == 'official' else local_model(model)
    verify_model(model, spec)
    return dict(schema_version=1, kind='shiju-environment-plan', route=route,
                warning=WARNING if route == 'local' else '官方固定模型；环境仍须在本机真实验证',
                hardware=requirements(), python=dict(version='3.12.10', method='isolated-nuget', sha256=PYTHON_SHA256),
                model=spec, inference=dict(precision='4bit', reason='采用已有 8GB GPU 实测的 BNB NF4 组合'),
                framework=dict(engine_sha256=engine_digest()), dependencies=wheels(),
                creation=dict(method='isolated-python', global_path=False),
                verification=['pip-check', 'exact-versions', 'cuda-fp16', 'bnb-nf4', 'triton-jit', 'model-load', 'short-inference', 'poetry-constraints'],
                download_bytes=sum(w['bytes'] for w in wheels()) + 14515433,
                model_bytes=model_bytes(spec),
                environment_required_bytes=18 * 1024**3)


def validate_plan(plan):
    if not isinstance(plan, dict) or plan.get('schema_version') != 1:
        raise ValueError('不支持的清单版本；请用当前工具重新生成')
    model = plan.get('model')
    if not isinstance(model, dict) or not isinstance(model.get('files'), dict) or not model['files']:
        raise ValueError('模型文件清单无效')
    if plan.get('route') == 'official':
        if model != official_model():
            raise ValueError('官方模型清单与内置固定修订不符')
    elif plan.get('route') == 'local':
        if set(model) != {'model_id', 'revision', 'source_url', 'model_type', 'files'} or model['model_type'] not in {'qwen3', 'qwen3_5'} or model['source_url'] != 'local':
            raise ValueError('自带模型清单不受支持')
        import re
        for name, item in model['files'].items():
            relative_path(name)
            if not isinstance(item, dict) or set(item) != {'sha256', 'bytes'} or not re.fullmatch('[0-9a-f]{64}', str(item['sha256'])) or type(item['bytes']) is not int or item['bytes'] <= 0:
                raise ValueError('模型文件大小或摘要无效')
        if model['revision'] != hashlib.sha256(json.dumps(model['files'], sort_keys=True).encode()).hexdigest():
            raise ValueError('本地模型内容修订与文件摘要不符')
    else:
        raise ValueError('未知模型路线')
    # Construct the complete expected policy without evaluating imported code.
    expected = dict(schema_version=1, kind='shiju-environment-plan', route=plan['route'],
        warning=WARNING if plan['route'] == 'local' else '官方固定模型；环境仍须在本机真实验证',
        hardware=requirements(), python=dict(version='3.12.10', method='isolated-nuget', sha256=PYTHON_SHA256),
        model=model, inference=dict(precision='4bit', reason='采用已有 8GB GPU 实测的 BNB NF4 组合'),
        framework=dict(engine_sha256=engine_digest()), dependencies=wheels(),
        creation=dict(method='isolated-python', global_path=False),
        verification=['pip-check', 'exact-versions', 'cuda-fp16', 'bnb-nf4', 'triton-jit', 'model-load', 'short-inference', 'poetry-constraints'],
        download_bytes=sum(w['bytes'] for w in wheels()) + 14515433,
        model_bytes=model_bytes(model),
        environment_required_bytes=18 * 1024**3)
    if plan != expected:
        raise ValueError('清单的依赖、硬件、框架或安装规则与当前工具不符，请重新生成；不执行外部清单命令')
    return plan


def extract_python(archive, target):
    target = Path(target).resolve()
    with zipfile.ZipFile(archive) as package:
        seen = set()
        entries = []
        for entry in package.infolist():
            if not entry.filename.startswith('tools/') or entry.is_dir():
                continue
            name = relative_path(entry.filename[6:])
            if name.casefold() in seen or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Python 归档包含重复路径或符号链接')
            seen.add(name.casefold())
            dest = target / name
            if not dest.resolve().is_relative_to(target):
                raise ValueError('Python 解压路径越界')
            entries.append((entry, dest))
        if sum(e.file_size for e, _ in entries) > 512 * 1024**2:
            raise ValueError('Python 归档展开大小异常')
        for entry, dest in entries:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with package.open(entry) as source, dest.open('wb') as output:
                shutil.copyfileobj(source, output)
    (target / 'python312._pth').write_text('.\nLib\nDLLs\nLib/site-packages\n', encoding='ascii')


def process_env(environment, report):
    environment = Path(environment)
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(
        ('PYTHON', 'CONDA', 'CUDA', 'TRITON', 'VIRTUAL_ENV', 'PIP_', 'HF_', 'HUGGINGFACE', 'TORCH', 'NVIDIA', 'VCINSTALL', 'VCTOOLS', 'WINDOWSSDK', 'VSINSTALL'))
        and k.upper() not in {'CC', 'CXX', 'INCLUDE', 'LIB', 'LIBPATH', 'CMAKE_PREFIX_PATH', 'CPATH'}}
    windows = Path(os.getenv('SystemRoot', 'C:/Windows'))
    env.update(PATH=os.pathsep.join(map(str, (environment, windows / 'System32', windows))),
               PYTHONNOUSERSITE='1', PYTHONIOENCODING='utf-8', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
               TRITON_CACHE_DIR=str(Path(report).parent / 'jit-cache/triton'),
               TORCHINDUCTOR_CACHE_DIR=str(Path(report).parent / 'jit-cache/inductor'),
               PIP_CONFIG_FILE=os.devnull, PIP_CACHE_DIR=str(Path(report).parent / 'pip-cache'))
    return env


def install(plan, model, target, cache, operation, data_directory):
    validate_plan(plan)
    model = verify_model(model, plan['model'])
    if plan['route'] == 'local' and local_model(model) != plan['model']:
        raise ValueError('自带模型配置或内容已变动，请重新生成清单')
    target, cache = Path(target).resolve(), Path(cache).resolve()
    if target == cache or target in cache.parents or cache in target.parents or target == model or target in model.parents or model in target.parents:
        raise ValueError('环境、模型、缓存目录不得相互包含')
    gpu = compatibility(inspect_host(target), plan['hardware'])
    check_install_space(target, cache, plan['environment_required_bytes'], plan['download_bytes'])
    plan_id = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(target.name + '.shiju-install.lock')
    # Windows OS lock is released by the kernel even after a process crash.
    lock_handle = lock_path.open('a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            lock_handle.seek(0)
            if os.fstat(lock_handle.fileno()).st_size == 0:
                lock_handle.write(b'0')
                lock_handle.flush()
            lock_handle.seek(0)
            try:
                msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ValueError('此目录已有安装任务运行，请等待或取消原任务') from exc
        else:
            import fcntl
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state_path = target / 'shiju-install.json'
        if target.exists():
            if not state_path.is_file() or read(state_path).get('plan_id') != plan_id:
                raise ValueError('目标目录已存在且不是本清单的安装半成品；请选择新的空目录')
            if read(state_path).get('state') == 'verified':
                raise ValueError('此环境已经验证成功，不重复覆盖安装；可直接进入诗矩，或选择新的环境目录')
        else:
            target.mkdir()
        save(state_path, dict(plan_id=plan_id, state='installing', ok=False))
        try:
            operation.update('准备独立 Python')
            package = cache / 'python.3.12.10.nupkg'
            operation.download(PYTHON_URL, package, PYTHON_SHA256, 14515433)
            # Re-extract known base files on retry: python.exe alone does not
            # prove a previously interrupted extraction was complete.
            extract_python(package, target)
            python = target / 'python.exe'
            env = process_env(target, operation.report)
            env['CUDA_VISIBLE_DEVICES'] = gpu.uuid
            wheelhouse = cache / 'wheels'
            for item in wheels():
                operation.update('下载固定依赖', file=item['filename'])
                operation.download(item['source_url'], wheelhouse / item['filename'], item['sha256'], item['bytes'])
            lock = target / 'requirements.lock'
            lock.write_text(''.join(f"{w['name']}=={w['version']} --hash=sha256:{w['sha256']}\n" for w in wheels()), encoding='utf-8')
            operation.update('安装固定依赖')
            operation.run([python, '-m', 'pip', '--isolated', 'install', '--no-index', '--find-links', wheelhouse,
                           '--require-hashes', '--only-binary=:all:', '-r', lock], env=env)
            operation.update('检查依赖一致性')
            operation.run([python, '-m', 'pip', '--isolated', 'check'], env=env)
            actual_path = operation.report.parent / 'actual-dependencies.json'
            operation.run([python, '-I', '-c',
                'import importlib.metadata,json,pathlib,sys;pathlib.Path(sys.argv[1]).write_text(json.dumps({d.metadata["Name"].lower().replace("_","-"):d.version for d in importlib.metadata.distributions()},sort_keys=True),encoding="utf-8")', actual_path], env=env)
            actual = read(actual_path)
            for item in wheels():
                if actual.get(item['name'].lower().replace('_', '-')) != item['version']:
                    raise ValueError('实际依赖版本不符：' + item['name'])
            unexpected = set(actual) - {w['name'].lower().replace('_', '-') for w in wheels()} - {'pip'}
            if unexpected:
                raise ValueError('专用环境中出现未登记依赖：' + ', '.join(sorted(unexpected)))
            save(target / 'actual-lock.json', dict(plan_id=plan_id, packages=actual, wheels=wheels()))
            operation.update('验证 GPU、编译与真实约束生成')
            # Reuse the genuine CUDA/BNB/JIT/model/poetry checks. Copy source,
            # never an existing inference environment or its installed packages.
            app = target / 'app'
            for folder in ('apps', 'shiju', 'Rhyme', 'Songci_Meter', 'web', 'scripts'):
                source = ROOT / folder
                if source.is_dir():
                    shutil.copytree(source, app / folder, dirs_exist_ok=True,
                                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.db', '*.sqlite*', '.env'))
            shutil.copy2(ROOT / 'pyproject.toml', app / 'pyproject.toml')
            shutil.copy2(ROOT / 'launcher.ps1', app / 'launcher.ps1')
            shutil.copy2(ROOT / 'tools/probe_managed_candidate.py', target / 'probe_candidate.py')
            (target / 'python312._pth').write_text('.\nLib\nDLLs\nLib/site-packages\napp\n', encoding='ascii')
            verification = operation.report.parent / 'verification.json'
            model_report = operation.report.parent / 'model-verification.json'
            operation.run([python, '-I', target / 'probe_candidate.py', '--output', verification,
                           '--model', model, '--model-report', model_report], env=env, timeout=1800)
            if not read(verification).get('ok') or not read(model_report).get('ok'):
                raise ValueError('真实运行验证没有全部通过，保留环境供重试')
            operation.update('保存环境选择')
            # Detect model/source changes made during the long installation.
            validate_plan(plan)
            verify_model(model, plan['model'])
            # Cache the genuine result under the target interpreter's own
            # fingerprint, then select it via the existing validation API.
            operation.run([python, '-c',
                'import json,pathlib,sys;from apps.launcher.model_catalog import report_path;p=report_path(sys.argv[1],sys.argv[2],sys.executable);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(pathlib.Path(sys.argv[3]).read_text(encoding="utf-8"),encoding="utf-8")',
                data_directory, model, model_report], env=env)
            # Activation uses the existing launcher memory validation path.
            operation.run([python, '-m', 'apps.api.launcher_memory', '--action', 'remember',
                           '--data-dir', data_directory, '--application-python', python,
                           '--inference-python', python, '--model', model, '--local',
                           '--output', operation.report.parent / 'activation.json'], env=env)
            activation = read(operation.report.parent / 'activation.json')
            if activation.get('ok') is False:
                raise ValueError('启动器环境登记失败')
            operation.run([python, '-m', 'apps.api.launcher_memory', '--action', 'select',
                           '--data-dir', data_directory, '--application-python', python,
                           '--inference-python', python, '--model', model, '--local',
                           '--output', operation.report.parent / 'selection.json'], env=env)
            if not read(operation.report.parent / 'selection.json').get('ok'):
                raise ValueError('启动器环境与模型配对复核失败')
            integrity = {str(target / 'python.exe'): sha256_file(target / 'python.exe')}
            for folder in ('shiju', 'apps/gpu_worker', 'Rhyme', 'Songci_Meter'):
                for path in (app / folder).rglob('*'):
                    if path.is_file() and '__pycache__' not in path.parts:
                        integrity[str(path)] = sha256_file(path)
            for path in (target / 'Lib/site-packages').glob('*.dist-info/METADATA'):
                integrity[str(path)] = sha256_file(path)
            integrity.update({str(model / name): item['sha256'] for name, item in plan['model']['files'].items()})
            save(target / 'verified-inputs.json', integrity)
            directory = Path(data_directory)
            launcher = directory / 'launcher.json'
            settings = read(launcher) if launcher.exists() else {}
            save(launcher, settings | dict(application_python=str(python), inference_python=str(python), local_enabled=True))
            runtime = directory / 'runtime.json'
            settings = read(runtime) if runtime.exists() else {}
            save(runtime, settings | dict(model_path=str(model), quantization='4bit', allow_unverified=False))
            save(state_path, dict(plan_id=plan_id, state='verified', ok=True, python=str(python), model=str(model),
                                 precision='4bit', gpu_uuid=gpu.uuid, engine_sha256=engine_digest(),
                                 verification=str(verification)))
            return dict(python=str(python), model=str(model), verification=str(verification),
                        installed_bytes=sum(p.stat().st_size for p in target.rglob('*') if p.is_file()))
        except BaseException as exc:
            save(state_path, dict(plan_id=plan_id, state='incomplete', ok=False, stage=operation.stage, failure_type=type(exc).__name__))
            raise
    finally:
        lock_handle.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['inspect', 'verify-model', 'download', 'plan', 'import', 'export', 'install', 'diagnostics'])
    parser.add_argument('--model')
    parser.add_argument('--route', choices=['official', 'local'], default='official')
    parser.add_argument('--plan')
    parser.add_argument('--target')
    parser.add_argument('--cache')
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--report', required=True)
    parser.add_argument('--cancel')
    parser.add_argument('--export-dir')
    args = parser.parse_args()
    operation = Operation(args.report, args.cancel)
    try:
        operation.update('准备')
        if args.action == 'inspect':
            result = dict(host=asdict(inspect_host(args.target or args.data_dir)), models=discover_models(args.data_dir), **discover_environments())
        elif args.action == 'verify-model':
            operation.update('检查已有模型文件，请稍候')
            result = inspect_selected_model(args.model)
        elif args.action == 'download':
            result = dict(model=download_model(args.model, operation))
        elif args.action == 'plan':
            operation.update('核验模型并生成清单')
            result = make_plan(args.model, args.route)
            compatibility(inspect_host(args.target or args.data_dir), result['hardware'])
            save(args.plan, result)
        elif args.action == 'import':
            result = validate_plan(read(args.plan))
            compatibility(inspect_host(args.target or args.data_dir), result['hardware'])
            operation.update('核对导入清单与所选模型')
            verify_model(args.model, result['model'])
        elif args.action == 'export':
            from .environment_export import export_environment
            operation.update('导出 Conda 安装文件和精确依赖锁')
            result = export_environment(read(args.plan), args.export_dir)
        elif args.action == 'install':
            result = install(read(args.plan), args.model, args.target, args.cache, operation, args.data_dir)
        else:
            # Whitelist diagnostics; never zip arbitrary runtime logs/configs.
            result = dict(schema_version=1, host=asdict(inspect_host(args.data_dir)),
                          engine_sha256=engine_digest(), scope='deployment-diagnostics')
            if args.target and (Path(args.target) / 'shiju-install.json').is_file():
                state = read(Path(args.target) / 'shiju-install.json')
                result['installation'] = {key: state[key] for key in ('state', 'stage', 'ok', 'plan_id', 'failure_type') if key in state}
                actual = Path(args.target) / 'actual-lock.json'
                if actual.is_file():
                    result['actual_packages'] = read(actual).get('packages', {})
            save(args.plan, result)
        save(args.report, dict(ok=True, state='completed', stage=operation.stage, result=result))
        return 0
    except Exception as exc:
        save(args.report, dict(ok=False, state='cancelled' if isinstance(exc, Cancelled) else 'failed', stage=operation.stage, error=str(exc)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
