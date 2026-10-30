"""Export reviewed local-edition source files, without copying private workspace data."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import zipfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
TREES = {
    'apps': {'.py', '.sql', '.ps1', '.cmd'},
    'shiju': {'.py', '.csv'},
    'Rhyme': {'.json'},
    'Songci_Meter': {'.json'},
    'web/prosody-checker': {'.js', '.mjs', '.cjs', '.html', '.css', '.svg', '.json', '.md', '.woff2'},
}
FILES = {
    'README.md', '.gitignore', 'pyproject.toml', 'requirements-api.txt', 'requirements-gpu.txt',
    'launcher.ps1', 'start-single-player.ps1', '使用说明.md', '推荐配置.md',
    'scripts/launcher-instance.ps1', 'scripts/launcher-memory.ps1', 'scripts/launcher-models.ps1',
    'scripts/data-directory.ps1',
    'scripts/export-startup-diagnostics.ps1',
    'tools/environment-tool.ps1', 'tools/installer-bootstrap.ps1', 'tools/installer_bootstrap.py', 'tools/installer_protocol.py', 'tools/SetupBootstrap.cs', 'tools/probe_managed_candidate.py',
    'tools/build_environment_tool.py', 'tools/EnvironmentDeployer.cs',
    'tools/build_single_player_source.py',
    'deploy/environment-tool/model.json', 'deploy/environment-tool/recommended-plan.json',
    'deploy/environment-tool/small-models.json', 'deploy/environment-tool/cpu-torch.json', 'deploy/environment-tool/vc-runtime.json', 'deploy/managed-runtime/windows-qwen35-bnb4-wheels.json',
    'docs/single-player-launcher.html', 'docs/environment-tool-user.md',
}
EXCLUDED = {'Songci_Meter/display-layout-report.json', 'web/prosody-checker/index.intent.json'}
PUBLIC_HOSTS = {
    'ojs.aaai.org', 'aclanthology.org', 'doi.org',
    'www.apache.org', 'mozilla.org', 'developer.mozilla.org', 'w3c.github.io',
    'html.spec.whatwg.org', 'stackoverflow.com', 'url.spec.whatwg.org',
    'datatracker.ietf.org', 'tex.stackexchange.com', 'en.wikibooks.org',
    'www.unicode.org', 'scripts.sil.org', 'www.tug.org', 'www.math.lsu.edu',
    'www.w3schools.com', 'mirrors.concertpass.com', 'en.wikipedia.org',
    'ctan.math.illinois.edu', 'ctan.math.washington.edu', 'daringfireball.net',
    '127.0.0.1', 'localhost', '::1', 'testserver', 'www.w3.org', 'www.nvidia.com',
    'modelscope.cn', 'www.modelscope.cn', 'api.nuget.org', 'aka.ms', 'download.visualstudio.microsoft.com', 'learn.microsoft.com', 'pypi.org',
    'mirror.sjtu.edu.cn', 'mirrors.tuna.tsinghua.edu.cn', 'files.pythonhosted.org', 'download.pytorch.org', 'download-r2.pytorch.org',
    'docs.conda.io', 'pytorch.org', 'github.com', 'www.python.org', 'huggingface.co',
}


def selected_files(root: Path):
    paths = set(FILES)
    for folder, suffixes in TREES.items():
        for path in (root / folder).rglob('*'):
            relative = path.relative_to(root)
            if any(part.startswith('.') or part in {'__pycache__', 'node_modules', 'tests'} for part in relative.parts):
                continue
            if path.is_file() and path.suffix in suffixes:
                paths.add(relative.as_posix())
    for name in sorted(paths - EXCLUDED):
        path = root / name
        if not path.is_file():
            raise ValueError(f'发布白名单中的文件不存在：{name}')
        # Reparse points may point at private data outside the project.
        if path.is_symlink() or any(parent.is_symlink() or getattr(parent, 'is_junction', lambda: False)() for parent in path.parents if parent != root):
            raise ValueError(f'发布文件不能使用链接或目录联接：{name}')
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f'发布文件超出项目目录：{name}')
        yield name, path


def export_content(name: str, data: bytes) -> bytes:
    return data


def inspect_content(name: str, data: bytes) -> list[dict]:
    """Fail closed on unreviewed destinations; report locations without secret values."""
    if Path(name).suffix == '.woff2':
        if not data.startswith(b'wOF2'):
            raise ValueError(f'无效的 WOFF2 字体文件：{name}')
        return []
    text = data.decode('utf-8-sig')
    findings = []
    if name.startswith('apps/') and name.endswith('.py'):
        tree = ast.parse(text, filename=name)
        forbidden = {'cryptography', 'hmac', 'bcrypt', 'argon2', 'psycopg', 'jwt'}
        for node in ast.walk(tree):
            imported = ([node.module or ''] if isinstance(node, ast.ImportFrom) else
                        [item.name for item in node.names] if isinstance(node, ast.Import) else [])
            if any(module.split('.')[0] in forbidden for module in imported):
                findings.append({'path': name, 'line': node.lineno, 'kind': 'removed_server_dependency'})
    markers = ('LTAI' + r'[A-Za-z0-9]{12,}', 'AKIA' + r'[A-Z0-9]{16}',
               r'\bsk-' + r'[A-Za-z0-9_-]{20,}',
               '-----BEGIN ' + r'(?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
               ':'.join(('acs', 'oss', '')), r'\blavm-' + r'[a-z0-9]+',
               r'\bshiju-model-' + r'\d+', r'\bTASK-' + r'\d{5,}')
    for number, line in enumerate(text.splitlines(), 1):
        if any(re.search(pattern, line) for pattern in markers):
            findings.append({'path': name, 'line': number, 'kind': 'credential_or_infrastructure'})
        for raw in re.findall(r'https?://[^\s\"\'<>`);\u3000-\u303f\uff00-\uffef]+', line):
            try:
                parsed = urlsplit(raw)
                host = parsed.hostname or ''
                safe = (host in PUBLIC_HOSTS or host == 'example.com'
                        or host.endswith(('.example', '.example.com', '.invalid', '.test')))
                if parsed.username or parsed.password or not safe:
                    findings.append({'path': name, 'line': number, 'kind': 'unreviewed_url'})
            except ValueError:
                findings.append({'path': name, 'line': number, 'kind': 'invalid_url'})
    return findings


def build(output: Path, root: Path = ROOT):
    output = output.resolve()
    release_root = (root / 'dist').resolve()
    if output.parent != release_root or output.suffix != '.zip' or output.exists():
        raise ValueError('输出必须为项目 dist 下尚不存在的 .zip 文件')
    entries, findings = {}, []
    for name, path in selected_files(root):
        data = export_content(name, path.read_bytes())
        findings.extend(inspect_content(name, data))
        entries[name] = data
    if findings:
        raise ValueError('发布检查未通过（不显示敏感原文）：' + json.dumps(findings, ensure_ascii=False))
    import sys
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from apps.launcher.web_assets import web_assets
    web_assets(entries)
    manifest = {'edition': 'single-player', 'files': {name: hashlib.sha256(data).hexdigest() for name, data in entries.items()},
                'excluded': ['server deployment and handover notes', 'user data and backups', 'existing archives and screenshots', 'Git history'],
                'checks': ['explicit file selection', 'reviewed URL hosts', 'known credential and infrastructure patterns'],
                'limitations': 'Pattern checks cannot prove the absence of all secrets. Review changed files before publishing.'}
    entries['SOURCE-MANIFEST.json'] = json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8')
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as stream, zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return {'archive': str(output), 'files': len(entries), 'bytes': output.stat().st_size,
            'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/single-player-source.zip')
    print(json.dumps(build(parser.parse_args().output), ensure_ascii=False))
