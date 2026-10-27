"""Build the small deployment application without installing build packages."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def build(output):
    if __package__:
        from tools.build_single_player_source import selected_files, export_content, inspect_content
    else:
        from build_single_player_source import selected_files, export_content, inspect_content
    output = Path(output).resolve()
    if not output.is_relative_to(ROOT / 'dist') or output == ROOT / 'dist' or output.exists():
        raise ValueError('请使用仓库 dist 下一个不存在的新目录')
    entries = {}
    for name, path in selected_files(ROOT):
        if name.startswith('tests/'):
            continue
        content = export_content(name, path.read_bytes())
        findings = inspect_content(name, content)
        if findings:
            raise ValueError('发布检查未通过：' + json.dumps(findings, ensure_ascii=False))
        entries[name] = content
    output.mkdir(parents=True)
    for name, content in entries.items():
        dest = output / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
    # Windows PowerShell 5.1 requires BOM for Chinese source text.
    for path in output.rglob('*.ps1'):
        path.write_text(path.read_text(encoding='utf-8-sig'), encoding='utf-8-sig')
    compiler = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    subprocess.run([str(compiler), '/nologo', '/target:winexe', '/codepage:65001',
                    '/reference:System.Windows.Forms.dll', '/out:' + str(output / '诗矩环境部署.exe'),
                    str(ROOT / 'tools/EnvironmentDeployer.cs')], check=True)
    shutil.copy2(ROOT / 'docs/environment-tool-user.md', output / '使用说明.md')
    archive = output.with_suffix('.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as stream:
        for path in sorted(output.rglob('*')):
            if path.is_file():
                stream.write(path, path.relative_to(output))
    wheels = json.loads((ROOT / 'deploy/managed-runtime/windows-qwen35-bnb4-wheels.json').read_text())
    report = dict(tool_zip_bytes=archive.stat().st_size, tool_expanded_bytes=sum(p.stat().st_size for p in output.rglob('*') if p.is_file()),
                  python_download_bytes=14515433, wheels_download_bytes=sum(w['bytes'] for w in wheels),
                  model_download_bytes=sum(w['bytes'] for w in json.loads((ROOT / 'deploy/environment-tool/model.json').read_text())['files'].values()),
                  clean_windows_gpu_accepted=False)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from apps.launcher.deployment_profiles import PROFILES, wheels as profile_wheels
    report['profiles'] = {name: dict(wheel_count=len(profile_wheels(name)),
                                    download_bytes=sum(w['bytes'] for w in profile_wheels(name)))
                          for name in PROFILES}
    output.with_suffix('.build.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default=str(ROOT / 'dist/诗矩环境部署'))
    build(parser.parse_args().output)
