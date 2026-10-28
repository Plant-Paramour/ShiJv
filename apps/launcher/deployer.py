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
import shutil
import subprocess
import sys
import time
from urllib.parse import urlencode
import uuid
import zipfile

from .download_transport import DownloadTransport, Cancelled
from .deployment_profiles import PROFILES, CUDA_ARCHITECTURES, wheels
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


def official_model(model_id='Qwen/Qwen3.5-4B'):
    if model_id == 'Qwen/Qwen3.5-4B':
        return read(POLICY / 'model.json')
    models = read(POLICY / 'small-models.json')
    try:
        return models[model_id]
    except KeyError as exc:
        raise ValueError('请选择工具支持的官方模型') from exc


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


class DeploymentFailure(RuntimeError):
    def __init__(self, message, **details):
        super().__init__(message)
        self.details = details


def process_failure(stage, log, message):
    limit = 64 * 1024
    with Path(log).open('rb') as stream:
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(max(0, size - limit))
        content = stream.read().decode('utf-8-sig', errors='replace')
    return DeploymentFailure(f'阶段“{stage}”失败：{message}，日志：{log}',
                             log_path=str(log), log_content=content, log_truncated=size > limit)


def failure_details(exc):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if getattr(exc, 'details', None):
            return exc.details
        exc = exc.__cause__ or exc.__context__
    return {}


def verification_failure(exc, paths):
    details = dict(failure_details(exc))
    summaries, reports = [], []
    # The model report explains the user-facing failure; the wrapper traceback
    # often only says "Actual model acceptance failed".
    for path in reversed(list(paths)):
        path = Path(path)
        if not path.is_file():
            continue
        try:
            report = read(path)
            summary = dict(path=str(path), ok=report.get('ok'), error=report.get('error'),
                           environment_errors=report.get('environment', {}).get('errors', []),
                           failed_checks=[c for c in report.get('checks', []) if c.get('state') == 'failed'])
            reports.append(summary)
            if summary['error']:
                summaries.append(str(summary['error']))
            summaries.extend(str(error) for error in summary['environment_errors'])
            for check in summary['failed_checks']:
                detail = str(check.get('detail', check))
                if detail not in summaries:
                    summaries.append(detail)
        except (OSError, ValueError, TypeError, AttributeError) as error:
            summaries.append(f'无法读取验证报告 {path}：{error}')
    details['verification_reports'] = reports
    details['verification_error'] = '\n\n'.join(summaries) or '验证进程未留下可读错误报告，请保留整个 reports 目录。'
    message = str(exc)
    if summaries:
        message = '本机验证未通过：' + summaries[0] + '\n' + message
    return DeploymentFailure(message, **details)


class Operation(DownloadTransport):
    def __init__(self, report, cancel=None):
        self.report = Path(report)
        self.cancel = Path(cancel) if cancel else None
        self.stage = '准备'
        self.events = []
        self.context = {}
        self.source_preference = 'auto'
        from .diagnostics import OperationDiagnostic
        self.diagnostic = OperationDiagnostic(self.report.parent, 'deployment')
        self.context['diagnostics'] = self.diagnostic.metadata

    def check(self):
        if self.cancel and self.cancel.exists():
            raise Cancelled('操作已取消；文件和日志保留，点击重试继续')

    def update(self, stage, **details):
        self.check()
        self.stage = stage
        self.diagnostic.event('stage', stage=stage)
        save(self.report, dict(ok=False, state='running', stage=stage, **(self.context | details)))


    def run(self, command, *, env=None, timeout=1800):
        self.check()
        from .diagnostics import OperationDiagnostic
        import threading
        diagnostic = OperationDiagnostic(self.report.parent, 'process-' + uuid.uuid4().hex, self.diagnostic.id)
        log = Path(diagnostic.metadata['log_path']) if diagnostic.root else None
        process = None
        reader = None
        try:
            if log is None:
                raise OSError(diagnostic.describe('无法创建部署进程日志'))
            child_env = (env or os.environ).copy()
            child_env['SHIJU_DIAGNOSTIC_ID'] = diagnostic.id
            process = subprocess.Popen(list(map(str, command)), cwd=ROOT, env=child_env,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=_flags())
            def capture():
                try:
                    for line in iter(process.stdout.readline, b''):
                        diagnostic.logger.info(line.decode('utf-8', errors='replace').rstrip())
                finally:
                    process.stdout.close()
            reader = threading.Thread(target=capture, daemon=True)
            reader.start()
            started = time.monotonic()
            diagnostic.event('process_start', executable=str(command[0]), stage=self.stage)
            while process.poll() is None:
                self.check()
                if time.monotonic() - started > timeout:
                    raise RuntimeError('本阶段超时，请查看日志后重试')
                time.sleep(.2)
            reader.join(timeout=5)
            diagnostic.event('process_exit', exit_code=process.returncode)
            if process.returncode:
                raise process_failure(self.stage, log, f'退出码 {process.returncode}')
            return log
        except BaseException as exc:
            if process is not None and process.poll() is None:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, creationflags=_flags())
                else:
                    process.kill()
                process.wait(timeout=30)
            if reader:
                reader.join(timeout=5)
            diagnostic.fail(exc, 'CANCELLED' if isinstance(exc, Cancelled) else 'DEPLOYMENT_PROCESS_FAILED')
            if isinstance(exc, (Cancelled, DeploymentFailure)):
                raise
            if log is not None:
                raise process_failure(self.stage, log, str(exc)) from exc
            raise
        finally:
            diagnostic.save(dict(completed=True, ok=process is not None and process.returncode == 0, diagnostics=diagnostic.metadata))
            diagnostic.close()


def check_space(path, required):
    existing = Path(path).resolve()
    while not existing.exists():
        existing = existing.parent
    free = shutil.disk_usage(existing).free
    if free < required:
        raise ValueError(f'磁盘空间不足：{path} 需要约 {required / 1024**3:.1f} GiB，可用 {free / 1024**3:.1f} GiB')


def check_install_space(target, cache, environment_bytes, download_bytes, model=None, model_bytes=0):
    """Add simultaneous allocations when cache and environment share a volume."""
    volumes = {}
    allocations = [(target, environment_bytes + 1024**3), (cache, download_bytes)]
    if model is not None:
        allocations.append((model, model_bytes))
    for path, required in allocations:
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
    """Recognize named local Qwen models without requiring a pinned revision."""
    from .resource_advice import named_official_model
    if model_id := named_official_model(Path(directory).name):
        spec = local_model(directory)
        return dict(route='local', model=spec, label=f'已识别官方模型：{model_id}')
    for model_id in ('Qwen/Qwen3.5-4B', 'Qwen/Qwen3.5-2B', 'Qwen/Qwen3.5-0.8B'):
        spec = official_model(model_id)
        try:
            verify_model(directory, spec)
            return dict(route='official', model=spec, label=f'已核验的官方 {model_id.rsplit("-", 1)[-1]}，可直接复用')
        except (OSError, ValueError):
            pass
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


def download_model(directory, operation, model_id='Qwen/Qwen3.5-4B'):
    spec = official_model(model_id)
    directory = Path(directory).resolve()
    missing = sum(v['bytes'] for n, v in spec['files'].items()
                  if not (directory / n).is_file() or (directory / n).stat().st_size != v['bytes']
                  or sha256_file(directory / n) != v['sha256'])
    check_space(directory, missing + 512 * 1024**2)
    for name, item in spec['files'].items():
        relative_path(name)
        operation.update('从 ModelScope 下载推荐模型', file=name)
        url = f"https://modelscope.cn/api/v1/models/{spec['model_id']}/repo?" + urlencode(
            dict(Revision=spec['revision'], FilePath=name))
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
    from .resource_advice import named_official_model
    return dict(model_id=named_official_model(root.name) or root.name, revision=revision, source_url='local', model_type=config['model_type'], files=files)


def requirements(device='cuda'):
    return dict(os='windows', arch='x64', min_windows_build=19041,
                device=device, min_driver='560.76', compute_capabilities=CUDA_ARCHITECTURES,
                min_vram_bytes=2_000_000_000,
                scope='GPU 按本机实测验收；CPU 方案须单独检查可用内存与精度')


def compatibility(host, rules):
    if host.os != rules['os'] or host.arch != rules['arch']:
        raise ValueError('目前只支持 Windows x64')
    if host.windows_build < rules['min_windows_build']:
        raise ValueError('Windows 版本过低')
    if rules['device'] == 'cpu':
        return None
    devices = [g for g in host.gpus if g.compute_capability in rules['compute_capabilities']
               and version_tuple(g.driver_version) >= version_tuple(rules['min_driver'])
               and g.memory_bytes >= rules['min_vram_bytes']]
    if not devices:
        raise ValueError('此 GPU 清单不适用于当前硬件，请重新生成清单以使用 CPU')
    return max(devices, key=lambda g: g.memory_bytes)


def inference_policy(profile):
    selected = PROFILES[profile]
    return dict(precision=selected['precision'], reason=selected['label'] + ('；首次生成验收使用 ' + selected['precision'] + '，可在模型设置中切换量化位数，无需重建环境' if profile in {'cuda-bnb', 'cuda-bnb8'} else '') + '；硬件预算仅供预选，须通过本机真实生成验收')


def verification_policy(profile):
    checks = ['cpu-bf16' if profile == 'cpu-bf16' else 'cpu-fp32'] if PROFILES[profile]['device'] == 'cpu' else ['cuda-fp16']
    if profile in {'cuda-bnb', 'cuda-bnb8'}:
        checks += ['bnb-int8' if profile == 'cuda-bnb8' else 'bnb-nf4', 'triton-jit']
    return ['pip-check', 'exact-versions'] + checks + ['model-load', 'short-inference', 'poetry-constraints']


def plan_policy(spec, route, profile):
    if profile not in PROFILES:
        raise ValueError('未知依赖组合')
    return dict(schema_version=2, kind='shiju-environment-plan', route=route, profile=profile,
                warning=WARNING if route == 'local' else '推荐固定模型；环境仍须在本机真实验证',
                hardware=requirements(PROFILES[profile]['device']), python=dict(version='3.12.10', method='isolated-nuget', sha256=PYTHON_SHA256),
                model=spec, inference=inference_policy(profile),
                framework=dict(engine_sha256=engine_digest()), dependencies=wheels(profile),
                creation=dict(method='isolated-python', global_path=False),
                verification=verification_policy(profile),
                download_bytes=sum(w['bytes'] for w in wheels(profile)) + 14515433,
                model_bytes=model_bytes(spec),
                environment_required_bytes=PROFILES[profile]['disk_gib'] * 1024**3)


def choose_profile(host, spec, requested='auto', resources=None):
    if requested != 'auto' and requested not in PROFILES:
        raise ValueError('未知依赖组合')
    if requested in {'cpu', 'cpu-bf16'}:
        compatibility(host, requirements('cpu'))
        return requested
    from .resource_advice import model_size, auto_profile, collect_resources
    if model_size(spec.get('model_id', '')):
        if requested != 'auto':
            compatibility(host, requirements())
            # Resource risks are presented for explicit user review, not hidden
            # behind the old file-size-only rejection formula.
            return requested
        compatibility(host, requirements('cpu'))
        return auto_profile(resources if resources is not None else collect_resources(host), spec['model_id'])
    try:
        gpu = compatibility(host, requirements())
    except ValueError:
        if requested != 'auto':
            raise
        compatibility(host, requirements('cpu'))
        return 'cpu'
    fp16_fits = gpu.memory_bytes > model_bytes(spec) * 1.3 + 1536 * 1024**2
    bnb_fits = gpu.memory_bytes > model_bytes(spec) * .35 + 1536 * 1024**2
    if requested != 'auto':
        fits = fp16_fits if requested == 'cuda-fp16' else (gpu.memory_bytes > model_bytes(spec) * .65 + 1536 * 1024**2 if requested == 'cuda-bnb8' else bnb_fits)
        if not fits:
            raise ValueError('所选组合的显存预算不足，请选择更小模型或 CPU')
        return requested
    if fp16_fits:
        return 'cuda-fp16'
    if bnb_fits:
        return 'cuda-bnb'
    return 'cpu'


def make_plan(model, route, model_id='Qwen/Qwen3.5-4B', host=None, profile='auto', resources=None):
    if route not in {'official', 'local'}:
        raise ValueError('未知模型路线')
    spec = official_model(model_id) if route == 'official' else local_model(model)
    verify_model(model, spec)
    profile = choose_profile(host or inspect_host(model), spec, profile, resources=resources)
    return plan_policy(spec, route, profile)


def validate_plan(plan):
    if not isinstance(plan, dict) or plan.get('schema_version') != 2:
        raise ValueError('不支持的清单版本；请用当前工具重新生成')
    model = plan.get('model')
    if not isinstance(model, dict) or not isinstance(model.get('files'), dict) or not model['files']:
        raise ValueError('模型文件清单无效')
    if plan.get('route') == 'official':
        if model != official_model(model.get('model_id')):
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
    expected = plan_policy(model, plan['route'], plan.get('profile'))
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
    from .process_environment import isolated_environment
    return isolated_environment(environment, report)


def dependency_versions(python, operation, env):
    actual_path = operation.report.parent / 'actual-dependencies.json'
    operation.run([python, '-I', '-c',
        'import importlib.metadata,json,pathlib,sys;pathlib.Path(sys.argv[1]).write_text(json.dumps({d.metadata["Name"].lower().replace("_","-"):d.version for d in importlib.metadata.distributions()},sort_keys=True),encoding="utf-8")', actual_path], env=env)
    return read(actual_path)


def versions_match(actual, items):
    expected = {w['name'].lower().replace('_', '-'): w['version'] for w in items}
    return all(actual.get(name) == version for name, version in expected.items()) and not (set(actual) - set(expected) - {'pip', 'setuptools', 'wheel'})


def check_vc_runtime(operation, installer=None, digest=None, required=True):
    report = operation.report.parent / 'vc-runtime.json'
    powershell = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    command = [powershell, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
               ROOT / 'apps/launcher/export_templates/ensure-vc-runtime.ps1', '-ReportPath', report]
    if installer:
        command += ['-Mode', 'Install', '-InstallerPath', installer, '-ExpectedSha256', digest]
    operation.run(command, timeout=900 if installer else 60)
    result = read(report)
    if required and (not result.get('ok') or result.get('restart_required')):
        message = ('VC++ 运行库安装要求重启 Windows，请重启后恢复部署。' if result.get('restart_required') else
                   'Microsoft Visual C++ x64 运行库尚不可用。部署时会自动安装；若安装失败或取消，请查看下方详情，并在重试时允许 Windows 权限提示。'
                   '也可在浏览器打开 https://aka.ms/vc14/vc_redist.x64.exe 下载微软安装包，运行后选择“安装”或“修复”。'
                   '安装完成后，如要求重启则先重启，再回到工具点击“创建 / 继续部署”，无需重新下载已有依赖。')
        raise DeploymentFailure(message, prerequisite='vc-runtime', prerequisite_details=result,
                                download_url='https://aka.ms/vc14/vc_redist.x64.exe',
                                instructions=[
                                    '方法一：点击“创建 / 继续部署”，工具会自动补装运行库；允许 Windows 权限提示，等待安装完成。',
                                    '方法二：将下方微软官方下载链接复制到浏览器，下载 vc_redist.x64.exe，运行后选择“安装”或“修复”。',
                                    '如安装程序要求重启，请先重启；然后打开部署工具，必要时点击“恢复上次部署”，再点击“创建 / 继续部署”。',
                                    '若正在 Windows 沙箱或虚拟机中测试，运行库应安装在沙箱或虚拟机内部，宿主机安装不能代替。'],
                                help_url='https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist/')
    return result


def install_vc_runtime(cache, operation):
    spec = read(POLICY / 'vc-runtime.json')
    operation.update('下载 Microsoft Visual C++ x64 运行库')
    installer = Path(cache) / spec['filename']
    operation.download(spec['source_url'], installer, spec['sha256'], spec['bytes'])
    operation.update('核验微软签名并安装 VC++ 运行库（可能出现系统权限提示）')
    return check_vc_runtime(operation, installer, spec['sha256'])


def ensure_vc_runtime(cache, operation):
    operation.update('检查 Microsoft Visual C++ x64 运行库')
    result = check_vc_runtime(operation, required=False)
    if result.get('ok') and not result.get('restart_required'):
        return result
    return install_vc_runtime(cache, operation)


def import_wheels(plan, directory, cache, operation):
    validate_plan(plan)
    root = Path(directory).resolve()
    if not root.is_dir():
        raise ValueError('请选择包含 wheel 文件的目录')
    wheelhouse = Path(cache).resolve() / 'wheels'
    wheelhouse.mkdir(parents=True, exist_ok=True)
    accepted, rejected = [], []
    for item in plan['dependencies']:
        source = root / item['filename']
        if not source.is_file():
            continue
        operation.update('核验本地依赖包', file=item['filename'])
        if source.stat().st_size != item['bytes'] or sha256_file(source) != item['sha256']:
            rejected.append(item['filename'])
            continue
        destination = wheelhouse / item['filename']
        if source.resolve() != destination.resolve():
            temporary = destination.with_name(destination.name + '.import.part')
            shutil.copyfile(source, temporary)
            if temporary.stat().st_size != item['bytes'] or sha256_file(temporary) != item['sha256']:
                temporary.unlink()
                rejected.append(item['filename'])
                continue
            temporary.replace(destination)
        accepted.append(item['filename'])
    return dict(accepted=accepted, rejected=rejected, message='仅接受当前清单中大小与 SHA256 均匹配的包；原文件未改动')


def restore_session(target, data_directory, latest=False):
    session_path = Path(target) / 'deployment-session.json' if target and not latest else Path(data_directory) / 'deployment/last-session.json'
    if not session_path.is_file():
        session_path = Path(data_directory) / 'deployment/last-session.json'
    if not session_path.is_file():
        raise ValueError('没有找到可恢复的部署记录，请先生成清单并创建环境')
    session = read(session_path)
    plan = validate_plan(session['plan'])
    installed = Path(session['target'])
    state = read(installed / 'shiju-install.json')
    if state.get('plan_id') != hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest():
        raise ValueError('部署记录与目标目录不一致，请重新选择对应清单')
    verify_model(session['model'], plan['model'])
    return session | dict(state=state.get('state'), stage=state.get('stage', '准备'))


def inspect_existing_target(plan, target, operation):
    target = Path(target).resolve()
    if not target.exists() or (target.is_dir() and next(target.iterdir(), None) is None):
        return dict(kind='empty', missing=[])
    plan_id = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    state = target / 'shiju-install.json'
    saved = read(state) if state.is_file() else {}
    if saved.get('plan_id') == plan_id and saved.get('state') != 'verified':
        return dict(kind='resume', missing=[], external=saved.get('external', False))
    # Our NuGet Python is independent: it has neither conda-meta nor pyvenv.cfg.
    # A changed model, engine, or path changes plan_id without changing the
    # installed Python/dependencies. Recognize ownership using the old session,
    # then check the actual interpreter and packages against the new plan.
    session_path = target / 'deployment-session.json'
    owned = False
    if saved.get('plan_id') and session_path.is_file():
        previous = read(session_path).get('plan')
        owned = isinstance(previous, dict) and hashlib.sha256(
            json.dumps(previous, sort_keys=True).encode()).hexdigest() == saved['plan_id']
    python = target / 'python.exe'
    if not python.is_file():
        python = target / 'Scripts/python.exe'
    if not python.is_file() or not (owned or (target / 'conda-meta').is_dir() or (target / 'pyvenv.cfg').is_file()):
        raise ValueError('目标目录非空，且不是可识别的虚拟环境；请选择空目录创建环境')
    if not owned and any((target / name).exists() for name in ('app', 'probe_candidate.py', 'requirements.lock', 'verified-inputs.json', 'deployment-session.json')):
        raise ValueError('已有环境包含部署同名文件，无法安全继续；请选择空目录创建环境')
    output = operation.report.parent / 'existing-environment.json'
    operation.run([python, '-I', '-c',
        'import sys,struct,json,pathlib;pathlib.Path(sys.argv[1]).write_text(json.dumps(dict(version=list(sys.version_info[:3]),bits=struct.calcsize("P")*8,prefix=sys.prefix)),encoding="utf-8")', output],
        env=process_env(target, operation.report), timeout=60)
    info = read(output)
    if info['version'][:2] != [3, 12] or info['bits'] != 64 or Path(info['prefix']).resolve() != target:
        raise ValueError('已有环境不符合清单要求：需要独立的 Python 3.12 x64 环境；请选择空目录创建环境')
    actual = dependency_versions(python, operation, process_env(target, operation.report))
    expected = {w['name'].lower().replace('_', '-'): w['version'] for w in plan['dependencies']}
    conflicts = [f'{name}: 已安装 {actual[name]}，需要 {version}' for name, version in expected.items() if name in actual and actual[name] != version]
    extras = sorted(set(actual) - set(expected) - {'pip', 'setuptools', 'wheel'})
    if conflicts or extras:
        raise ValueError('已有环境不符合清单要求；请选择空目录创建环境。' + '\n' + '\n'.join(conflicts + (['清单外依赖：' + ', '.join(extras)] if extras else [])))
    return dict(kind='existing', external=saved.get('external', False) if owned else True,
                python=str(python), missing=[f'{name}=={version}' for name, version in expected.items() if name not in actual])


def install(plan, model, target, cache, operation, data_directory, continue_existing=False):
    validate_plan(plan)
    ensure_vc_runtime(cache, operation)
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
        existing = inspect_existing_target(plan, target, operation)
        external = existing.get('external', False)
        if existing['kind'] == 'existing' and not continue_existing:
            raise ValueError('已有兼容环境需要先确认后才能继续配置')
        target.mkdir(exist_ok=True)
        checkpoint_path = target / 'deployment-checkpoints.json'
        checkpoints = read(checkpoint_path) if checkpoint_path.is_file() else {}
        if checkpoints.get('plan_id') != plan_id:
            checkpoints = dict(plan_id=plan_id)
        session = dict(plan=plan, model=str(model), target=str(target), cache=str(cache))
        save(target / 'deployment-session.json', session)
        save(Path(data_directory) / 'deployment/last-session.json', session)
        save(state_path, dict(plan_id=plan_id, state='installing', ok=False, external=external))
        try:
            operation.update('准备独立 Python')
            python = Path(existing.get('python') or (target / 'Scripts/python.exe' if external and (target / 'Scripts/python.exe').is_file() else target / 'python.exe'))
            reuse_existing = existing['kind'] == 'existing'
            python_ready = python.is_file() and (external or reuse_existing or checkpoints.get('python_sha256') == sha256_file(python))
            if python_ready:
                try:
                    operation.run([python, '-I', '-c', 'import ssl,json,zipfile,importlib.metadata'],
                                  env=process_env(target, operation.report), timeout=30)
                except (OSError, RuntimeError):
                    if external or reuse_existing:
                        raise ValueError('已有环境的 Python 无法正常运行，请选择空目录创建环境')
                    python_ready = False
                    checkpoints.pop('dependencies', None)
            if not python_ready:
                package = cache / 'python.3.12.10.nupkg'
                operation.download(PYTHON_URL, package, PYTHON_SHA256, 14515433)
                extract_python(package, target)
                checkpoints['python_sha256'] = sha256_file(python)
                save(checkpoint_path, checkpoints)
            elif reuse_existing:
                checkpoints['python_sha256'] = sha256_file(python)
                save(checkpoint_path, checkpoints)
            env = process_env(target, operation.report)
            env['CUDA_VISIBLE_DEVICES'] = gpu.uuid if gpu else '-1'
            items = plan['dependencies']
            dependencies_ready = False
            if python_ready and (reuse_existing or checkpoints.get('dependencies')):
                operation.update('复核已安装依赖以继续部署')
                try:
                    dependencies_ready = versions_match(dependency_versions(python, operation, env), items)
                    if dependencies_ready:
                        operation.run([python, '-m', 'pip', '--isolated', 'check'], env=env)
                except RuntimeError:
                    dependencies_ready = False
            if not dependencies_ready:
                checkpoints.pop('dependencies', None)
                save(checkpoint_path, checkpoints)
                wheelhouse = cache / 'wheels'
                for index, item in enumerate(items, 1):
                    operation.context = dict(file_index=index, file_count=len(items))
                    operation.update('下载固定依赖', file=item['filename'])
                    operation.download_item(item, wheelhouse)
                operation.context = {}
                lock = target / 'requirements.lock'
                lock.write_text(''.join(f"{w['name']}=={w['version']} --hash=sha256:{w['sha256']}\n" for w in items), encoding='utf-8')
                operation.update('安装固定依赖')
                operation.run([python, '-m', 'pip', '--isolated', 'install', '--no-index', '--find-links', wheelhouse,
                               '--require-hashes', '--only-binary=:all:', '-r', lock], env=env)
                operation.update('检查依赖一致性')
                operation.run([python, '-m', 'pip', '--isolated', 'check'], env=env)
                actual = dependency_versions(python, operation, env)
                if not versions_match(actual, items):
                    raise ValueError('实际依赖版本与清单不符；保留环境供重试')
                save(target / 'actual-lock.json', dict(plan_id=plan_id, packages=actual, wheels=items))
                checkpoints['dependencies'] = True
                save(checkpoint_path, checkpoints)
            if dependencies_ready:
                checkpoints['dependencies'] = True
                save(checkpoint_path, checkpoints)
                save(target / 'actual-lock.json', dict(plan_id=plan_id, packages=dependency_versions(python, operation, env), wheels=items))
            operation.update('验证 CPU / GPU 与真实约束生成')
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
            if not external:
                (target / 'python312._pth').write_text('.\nLib\nDLLs\nLib/site-packages\napp\n', encoding='ascii')
                # Persist the selected device for later launcher probes and workers.
                (target / 'Lib/site-packages/sitecustomize.py').write_text(
                    'import os\nos.environ["CUDA_VISIBLE_DEVICES"] = ' + repr(env['CUDA_VISIBLE_DEVICES']) + '\n', encoding='utf-8')
                with (target / 'python312._pth').open('a', encoding='ascii') as stream:
                    stream.write('import site\n')
            verification = operation.report.parent / 'verification.json'
            model_report = operation.report.parent / 'model-verification.json'
            try:
                operation.run([python, '-I', target / 'probe_candidate.py', '--output', verification,
                               '--model', model, '--model-report', model_report, '--device', plan['hardware']['device'], '--precision', plan['inference']['precision']], env=env, timeout=7200 if plan['hardware']['device'] == 'cpu' else 1800)
            except Cancelled:
                raise
            except (OSError, RuntimeError) as exc:
                raise verification_failure(exc, [verification, model_report]) from exc
            if not read(verification).get('ok') or not read(model_report).get('ok'):
                raise ValueError('真实运行验证没有全部通过，保留环境供重试')
            operation.update('保存环境选择')
            # Detect model/source changes made during the long installation.
            validate_plan(plan)
            verify_model(model, plan['model'])
            # Cache the genuine result under the target interpreter's own
            # fingerprint, then select it via the existing validation API.
            operation.run([python, '-c',
                'import json,pathlib,sys;sys.path.insert(0,'+repr(str(app))+');from apps.launcher.model_catalog import report_path;p=report_path(sys.argv[1],sys.argv[2],sys.executable);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(pathlib.Path(sys.argv[3]).read_text(encoding="utf-8"),encoding="utf-8")',
                data_directory, model, model_report], env=env)
            # Activation uses the existing launcher memory validation path.
            operation.run([python, app / 'apps/launcher/python_entry.py', '-m', 'apps.api.launcher_memory', '--action', 'remember',
                           '--data-dir', data_directory, '--application-python', python,
                           '--inference-python', python, '--model', model, '--local',
                           '--output', operation.report.parent / 'activation.json'], env=env)
            activation = read(operation.report.parent / 'activation.json')
            if activation.get('ok') is False:
                raise ValueError('启动器环境登记失败')
            operation.run([python, app / 'apps/launcher/python_entry.py', '-m', 'apps.api.launcher_memory', '--action', 'select',
                           '--data-dir', data_directory, '--application-python', python,
                           '--inference-python', python, '--model', model, '--local',
                           '--output', operation.report.parent / 'selection.json'], env=env)
            if not read(operation.report.parent / 'selection.json').get('ok'):
                raise ValueError('启动器环境与模型配对复核失败')
            integrity = {str(python): sha256_file(python)}
            for name in ('python312._pth', 'Lib/site-packages/sitecustomize.py'):
                if (target / name).is_file():
                    integrity[str(target / name)] = sha256_file(target / name)
            for folder in ('shiju', 'apps/gpu_worker', 'Rhyme', 'Songci_Meter'):
                for path in (app / folder).rglob('*'):
                    if path.is_file() and '__pycache__' not in path.parts:
                        integrity[str(path)] = sha256_file(path)
            for path in (target / 'Lib/site-packages').glob('*.dist-info/METADATA'):
                integrity[str(path)] = sha256_file(path)
            # Environment paths are relative so host/sandbox mappings remain
            # valid. Model contents are independently checked for each pair.
            save(target / 'verified-inputs.json', dict(schema_version=2, files={
                Path(name).relative_to(target).as_posix(): digest for name, digest in integrity.items()}))
            directory = Path(data_directory)
            launcher = directory / 'launcher.json'
            settings = read(launcher) if launcher.exists() else {}
            save(launcher, settings | dict(application_python=str(python), inference_python=str(python), local_enabled=True))
            runtime = directory / 'runtime.json'
            settings = read(runtime) if runtime.exists() else {}
            save(runtime, settings | dict(model_path=str(model), quantization=plan['inference']['precision'], allow_unverified=False))
            save(state_path, dict(plan_id=plan_id, state='verified', ok=True, python=str(python), model=str(model),
                                 precision=plan['inference']['precision'], gpu_uuid=gpu.uuid if gpu else None, engine_sha256=engine_digest(),
                                 verification=str(verification)))
            return dict(python=str(python), model=str(model), verification=str(verification),
                        installed_bytes=sum(p.stat().st_size for p in target.rglob('*') if p.is_file()))
        except BaseException as exc:
            save(state_path, dict(plan_id=plan_id, state='incomplete', ok=False, stage=operation.stage, failure_type=type(exc).__name__, profile=plan['profile'], external=external))
            raise
    finally:
        lock_handle.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['inspect', 'preview', 'verify-model', 'download', 'plan', 'import', 'export', 'install', 'install-vc-runtime', 'resume', 'import-wheels', 'inspect-target', 'diagnostics'])
    parser.add_argument('--profile', choices=['auto', *PROFILES], default='auto')
    parser.add_argument('--source', choices=['auto', 'mirror', 'official'], default='auto')
    parser.add_argument('--continue-existing', action='store_true')
    parser.add_argument('--latest', action='store_true')
    parser.add_argument('--wheel-dir')
    parser.add_argument('--model')
    parser.add_argument('--route', choices=['official', 'local'], default='official')
    parser.add_argument('--model-id', default='Qwen/Qwen3.5-4B')
    parser.add_argument('--plan')
    parser.add_argument('--target')
    parser.add_argument('--cache')
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--report', required=True)
    parser.add_argument('--cancel')
    parser.add_argument('--export-dir')
    args = parser.parse_args()
    operation = Operation(args.report, args.cancel)
    operation.source_preference = args.source
    try:
        operation.update('准备')
        advisory = None
        if args.action in {'preview', 'download', 'verify-model', 'plan', 'import', 'inspect-target', 'install'}:
            for label, path in (('模型位置', args.model), ('运行环境位置', args.target)):
                if path and any('\u3400' <= char <= '\u9fff' or '\uf900' <= char <= '\ufaff' for char in path):
                    raise ValueError(f'{label}不能包含中文，请选择或输入不含中文的完整路径。')
        if args.action == 'inspect':
            from .resource_advice import snapshot
            host = inspect_host(args.target or args.data_dir)
            advisory = snapshot(host)
            result = dict(host=asdict(host), models=discover_models(args.data_dir),
                          vc_runtime=check_vc_runtime(operation, required=False), **discover_environments())
        elif args.action == 'verify-model':
            operation.update('检查已有模型文件，请稍候')
            result = inspect_selected_model(args.model)
        elif args.action == 'download':
            result = dict(model=download_model(args.model, operation, args.model_id))
        elif args.action == 'preview':
            operation.update('检查安装方案与磁盘空间')
            from .resource_advice import snapshot
            for label, path in (('模型', args.model), ('运行环境', args.target), ('缓存', args.cache)):
                if not path or not Path(path).is_absolute():
                    raise ValueError(f'请选择{label}的完整目录路径')
                if Path(path).exists() and not Path(path).is_dir():
                    raise ValueError(f'{label}位置是文件，请选择目录：{path}')
            host = inspect_host(args.target or args.data_dir)
            advisory = snapshot(host)
            spec = official_model(args.model_id) if args.route == 'official' else inspect_selected_model(args.model)['model']
            profile = choose_profile(host, spec, args.profile, advisory['resources'])
            result = plan_policy(spec, args.route, profile)
            inspect_existing_target(result, args.target, operation)
            check_install_space(args.target, args.cache, result['environment_required_bytes'], result['download_bytes'],
                                args.model if args.route == 'official' else None, result['model_bytes'])
        elif args.action == 'plan':
            operation.update('核验模型并生成清单')
            from .resource_advice import snapshot
            host = inspect_host(args.target or args.data_dir)
            advisory = snapshot(host)
            result = make_plan(args.model, args.route, args.model_id, profile=args.profile,
                               host=host, resources=advisory['resources'])
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
        elif args.action == 'resume':
            operation.update('恢复上次部署清单与目录')
            result = restore_session(args.target, args.data_dir, args.latest)
            save(args.plan, result['plan'])
        elif args.action == 'import-wheels':
            result = import_wheels(read(args.plan), args.wheel_dir, args.cache, operation)
        elif args.action == 'inspect-target':
            validate_plan(read(args.plan))
            result = inspect_existing_target(read(args.plan), args.target, operation)
        elif args.action == 'install':
            result = install(read(args.plan), args.model, args.target, args.cache, operation, args.data_dir, args.continue_existing)
        elif args.action == 'install-vc-runtime':
            result = install_vc_runtime(args.cache, operation)
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
        if args.action in {'preview', 'plan', 'import', 'resume', 'inspect-target'}:
            from .resource_advice import snapshot, assess_selection, MODEL_IDS
            if advisory is None:
                advisory = snapshot(inspect_host(args.target or args.data_dir))
            selected_plan = result['plan'] if args.action == 'resume' else (read(args.plan) if args.action == 'inspect-target' else result)
            advisory['selection'] = assess_selection(advisory['resources'], selected_plan['model']['model_id'],
                                                     selected_plan['profile'], known=selected_plan['route'] == 'official' or selected_plan['model']['model_id'] in MODEL_IDS,
                                                     weight_bytes=model_bytes(selected_plan['model']))
        operation.diagnostic.event('completed', ok=True)
        operation.diagnostic.save(dict(completed=True, ok=True, diagnostics=operation.diagnostic.metadata))
        save(args.report, dict(ok=True, state='completed', stage=operation.stage, result=result,
                               advisory=advisory, diagnostics=operation.diagnostic.metadata))
        return 0
    except Exception as exc:
        error = operation.diagnostic.fail(exc, 'CANCELLED' if isinstance(exc, Cancelled) else 'DEPLOYMENT_FAILED')
        operation.diagnostic.save(dict(completed=True, ok=False, error=str(exc), diagnostics=operation.diagnostic.metadata))
        save(args.report, dict(ok=False, state='cancelled' if isinstance(exc, Cancelled) else 'failed', stage=operation.stage, error=error, diagnostics=operation.diagnostic.metadata, **failure_details(exc)))
        return 1
    finally:
        operation.diagnostic.close()


if __name__ == '__main__':
    raise SystemExit(main())
