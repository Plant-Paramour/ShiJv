"""Export portable Conda specifications and auditable pip inputs (stdlib only)."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
from urllib.parse import urlsplit, urlunsplit


def export_environment(plan, destination):
    from .deployer import validate_plan, save, sha256_file
    validate_plan(plan)
    root = Path(destination).resolve()
    if root.exists():
        raise ValueError('导出目录已存在，请选择一个新的目录，避免覆盖文件')
    root.mkdir(parents=True)
    try:
        dependencies = plan['dependencies']
        lines = ['--require-hashes', '--only-binary=:all:']
        for item in dependencies:
            url = urlsplit(item['source_url'])
            source = urlunsplit((url.scheme, url.netloc, url.path, url.query, ''))
            lines.append(f"{item['name']} @ {source} --hash=sha256:{item['sha256']}")
        # requirements.lock uses exact Windows wheels, including the CUDA
        # wheel. No implicit PyPI/PyTorch index selection is needed.
        lock = '\n'.join(lines) + '\n'
        for filename in ('requirements.lock', 'requirements-windows-x64.lock'):
            (root / filename).write_text(lock, encoding='utf-8')
        bootstrap = ('name: shiju-qwen35\nchannels:\n  - conda-forge\n  - nodefaults\n'
                     'dependencies:\n  - python=3.12.10\n  - pip=25.0.1\n')
        (root / 'conda-bootstrap.yml').write_text(bootstrap, encoding='utf-8')
        # Inline pip requirements make `conda env create -f <absolute path>`
        # independent of the caller's working directory.
        yaml = bootstrap + '  - pip:\n' + ''.join('      - ' + json.dumps(line) + '\n' for line in lines)
        (root / 'environment.yml').write_text(yaml, encoding='utf-8')
        summary = 'Python==3.12.10\npip==25.0.1\n' + ''.join(
            f"{item['name']}=={item['version']}\n" for item in dependencies)
        (root / 'dependency-versions.txt').write_text(summary, encoding='utf-8')
        offline = ['--require-hashes', '--only-binary=:all:'] + [f"{i['name']}=={i['version']} --hash=sha256:{i['sha256']}" for i in dependencies]
        (root / 'requirements-offline.lock').write_text('\n'.join(offline) + '\n', encoding='utf-8')
        for name in ('download_transport.py', 'deployment_profiles.py'):
            shutil.copy2(Path(__file__).with_name(name), root / name)
        shutil.copy2(Path(__file__).resolve().parents[2] / 'deploy/environment-tool/vc-runtime.json', root / 'vc-runtime.json')
        save(root / 'plan.json', plan)
        save(root / 'expected-packages.json', {i['name']: i['version'] for i in dependencies})
        templates = Path(__file__).with_name('export_templates')
        for path in templates.iterdir():
            if path.is_file():
                destination_file = root / path.name
                shutil.copy2(path, destination_file)
                if path.suffix == '.ps1':
                    destination_file.write_text(path.read_text(encoding='utf-8-sig'), encoding='utf-8-sig')
        readme = f'''# 诗矩 Windows x64 环境安装文件

模型：{plan['model']['model_id']}。模型不包含在本目录中。
Python：3.12.10；依赖组合：{plan['profile']}；推理方式：{plan['hardware']['device']} / {plan['inference']['precision']}。支持 Windows x64；以本机真实推理验收为准。
CPU 和 GPU 均需要 Microsoft Visual C++ x64 运行库。脚本会先检查，缺失时自动补装；手动修复可双击 install-vc-runtime.cmd，
核验固定摘要与微软签名后安装，可能出现系统权限提示。如要求重启，请重启后继续部署。
模型约 {plan['model_bytes']/10**9:.2f} GB；Python 与 wheel 下载约 {plan['download_bytes']/10**9:.2f} GB；
专用环境空间预算约 {plan['environment_required_bytes']/10**9:.1f} GB，另需下载/Conda/编译缓存。

## 已经安装 Conda / Miniconda / Miniforge

最直接的标准命令（环境名可通过 -n 改名）：

    conda env create -f environment.yml

也可以双击 install-conda.cmd，选择保存父目录。脚本自动创建一个新目录，
分阶段安装并保存日志。已有用户环境不会被覆盖。
命令行指定路径：

    powershell -NoProfile -ExecutionPolicy Bypass -File install-conda.ps1 -TargetPath D:\\ShiJuEnv

网络中断后，保留目录，以相同路径加 -Resume 重试：

    powershell -NoProfile -ExecutionPolicy Bypass -File install-conda.ps1 -TargetPath D:\\ShiJuEnv -Resume

脚本先用 conda-bootstrap.yml 创建 Python，再下载精确 wheel 到 wheels/，通过大小与 SHA256 后离线安装。
下载默认国内镜像优先、失败回退官方；使用 -Source official 可仅走官方。
也可把缺少的精确版本 .whl 手动放入 wheels/，再次运行会校验并复用。
断点按文件保留，Windows HTTPS 后备路径失败后重新下载当前文件。
直接 conda env create -f environment.yml 使用官方直链，不具备脚本的镜像切换和阶段恢复功能。
失败后会保留阶段、日志和本目录的缓存；缺包、版本不符或 pip check 失败不会标记成功。
requirements-windows-x64.lock 是 pip 锁文件，不能传给 conda create --file。

## 检查与盘查

    conda run -n shiju-qwen35 python check-environment.py
    conda run -n shiju-qwen35 python -m pip check

检查结果保存到 check-result.json，实际版本保存到 actual-dependencies.json。
dependency-versions.txt 是便于人工查看的版本列表；实际安装请使用带来源和摘要的 requirements.lock。
检查脚本只核对 Python 与包版本；不会假装已经验证 GPU 或模型。
安装日志位于 logs；installation-state.json 记录是否失败、失败阶段及目标目录。
成功后还会导出 conda-explicit.txt（Conda 引导包的精确来源）和 pip-freeze.txt。
environment.yml 固定 Python/pip 和全部 pip wheel，但 Conda 传递引导包仍由 channel 求解；
它不等于完整 conda-lock。没有把未求解的 Conda 版本伪装成精确锁。

## 没有 Conda

仍可使用诗矩部署窗口第四步，自动准备独立 Python，不需要先装 Conda。
窗口第四步采用独立 Python 路线；Conda 环境使用上面的文件或安装脚本创建。
导出的 Conda 环境需要在诗矩启动器中选择其 python.exe 和模型目录，再执行真实模型验证。
依赖检查通过不代表模型/格律生成通过；也不会自动覆盖当前诗矩环境选择。

文件均不含密钥和本机环境变量。plan.json 可重新导入诗矩部署工具。
{plan['warning']}
'''
        (root / '使用说明.md').write_text(readme, encoding='utf-8')
        inventory = {p.name: sha256_file(p) for p in root.iterdir() if p.is_file()}
        save(root / 'files.sha256.json', inventory)
    except BaseException:
        # Keep partial output for diagnosis; never claim it is complete.
        raise
    return dict(directory=str(root), files=sorted(p.name for p in root.iterdir()),
                scope='windows-x64-conda-and-pip', model=plan['model']['model_id'])
