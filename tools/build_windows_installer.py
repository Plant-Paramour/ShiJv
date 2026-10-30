"""Stage a verified online-install payload and optionally compile the Inno Setup installer."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'dist'
INSTALLER = ROOT / 'installer/windows/shiJu.iss'


def find_iscc(explicit: str | None) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f'找不到 Inno Setup 编译器：{path}')
        return path
    command = shutil.which('ISCC.exe') or shutil.which('ISCC')
    if command:
        return Path(command)
    roots = [Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')),
             Path(os.environ.get('ProgramFiles', r'C:\Program Files'))]
    for root in roots:
        for version in ('Inno Setup 7', 'Inno Setup 6'):
            candidate = root / version / 'ISCC.exe'
            if candidate.is_file():
                return candidate
    return None


def prepare_payload(output: Path) -> dict:
    output = output.resolve()
    if not output.is_relative_to(DIST.resolve()) or output == DIST.resolve() or output.exists():
        raise ValueError('载荷目录必须是 dist 下一个尚不存在的新目录')
    if __package__:
        from tools.build_single_player_source import selected_files, export_content, inspect_content
    else:
        from build_single_player_source import selected_files, export_content, inspect_content

    entries: dict[str, bytes] = {}
    findings = []
    for name, path in selected_files(ROOT):
        content = export_content(name, path.read_bytes())
        findings.extend(inspect_content(name, content))
        entries[name] = content
    if findings:
        raise ValueError('发布内容检查未通过（不显示敏感原文）：' + json.dumps(findings, ensure_ascii=False))

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from apps.launcher.web_assets import web_assets
    web_assets(entries)

    output.mkdir(parents=True)
    for name, content in entries.items():
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    for path in output.rglob('*.ps1'):
        path.write_text(path.read_text(encoding='utf-8-sig'), encoding='utf-8-sig')

    compiler = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    launcher = output / '诗矩环境部署.exe'
    subprocess.run([str(compiler), '/nologo', '/target:winexe', '/codepage:65001',
                    '/reference:System.Windows.Forms.dll', '/out:' + str(launcher),
                    str(ROOT / 'tools/EnvironmentDeployer.cs')], check=True)
    subprocess.run([str(launcher), '--self-test'], check=True, timeout=30)
    bootstrap = output / 'ShiJuSetupBootstrap.exe'
    subprocess.run([str(compiler), '/nologo', '/target:winexe', '/codepage:65001',
                    '/reference:System.Windows.Forms.dll', '/out:' + str(bootstrap),
                    str(ROOT / 'tools/SetupBootstrap.cs')], check=True)
    subprocess.run([str(bootstrap), '--self-test'], check=True, timeout=30)
    single_player = output / '诗矩单机版.exe'
    subprocess.run([str(compiler), '/nologo', '/target:winexe', '/codepage:65001',
                    '/reference:System.Windows.Forms.dll', '/out:' + str(single_player),
                    str(ROOT / 'tools/SinglePlayerLauncher.cs')], check=True)

    hashes = {path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted(output.rglob('*')) if path.is_file()}
    manifest = dict(schema_version=1, product='ShiJu', source='reviewed single-player file selection', files=hashes)
    (output / 'INSTALLER-PAYLOAD.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return dict(payload=str(output), files=len(hashes), bytes=sum((output / name).stat().st_size for name in hashes),
                launcher_self_test='passed')


def compile_installer(payload: Path, output: Path, version: str, iscc: Path) -> Path:
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('版本号必须使用数字主版本.次版本.修订号格式，例如 0.1.0')
    output = output.resolve()
    if not output.is_relative_to(DIST.resolve()) or output == DIST.resolve():
        raise ValueError('安装程序输出目录必须位于 dist 下')
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f'安装程序输出目录非空：{output}')
    output.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(iscc), '/Qp', '/DAppVersion=' + version,
                    '/DPayloadDir=' + str(payload.resolve()), '/DOutputDir=' + str(output),
                    str(INSTALLER)], check=True, cwd=ROOT)
    artifact = output / '诗矩安装程序.exe'
    if not artifact.is_file() or artifact.stat().st_size == 0:
        raise RuntimeError('Inno Setup 未生成预期的诗矩安装程序.exe')
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', type=Path, default=DIST / 'windows-installer-payload')
    parser.add_argument('--output', type=Path, default=DIST / 'windows-installer')
    parser.add_argument('--version', default='0.1.0')
    parser.add_argument('--iscc', help='ISCC.exe 的完整路径；未指定时自动查找 Inno Setup 6/7')
    parser.add_argument('--stage-only', action='store_true', help='只生成并验证载荷，不编译安装程序')
    args = parser.parse_args()
    payload_manifest = args.payload / 'INSTALLER-PAYLOAD.json'
    if payload_manifest.is_file() and not args.stage_only:
        result = dict(payload=str(args.payload.resolve()),
                      files=len(json.loads(payload_manifest.read_text(encoding='utf-8')).get('files', {})),
                      launcher_self_test='skipped (existing verified payload)')
    else:
        result = prepare_payload(args.payload)
    if not args.stage_only:
        iscc = find_iscc(args.iscc)
        if iscc is None:
            raise FileNotFoundError('安装载荷已准备。编译安装程序还需要 Inno Setup 6/7 的 ISCC.exe；可用 --stage-only 单独验证载荷。')
        result['installer'] = str(compile_installer(args.payload, args.output, args.version, iscc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
