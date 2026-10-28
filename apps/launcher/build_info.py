"""Identify the application source actually loaded by the service."""
import hashlib
from pathlib import Path

RELEASE = '20261027-file-diagnostics-3'
FILES = ('apps/api/main.py', 'apps/api/static_files.py',
         'apps/api/startup_diagnostics.py', 'web/prosody-checker/index.html',
         'web/prosody-checker/site.js')


def build_info(root=None):
    root = Path(root or Path(__file__).resolve().parents[2])
    names = runtime_files(root)
    hashes = [name + ':' + (hashlib.sha256((root / name).read_bytes()).hexdigest()
              if (root / name).is_file() else 'missing') for name in names]
    return {'release': RELEASE, 'fingerprint': hashlib.sha256('|'.join(hashes).encode()).hexdigest()}


def runtime_files(root):
    import json
    manifest = root / 'RELEASE-MANIFEST.json'
    if manifest.is_file():
        names = json.loads(manifest.read_text(encoding='utf-8'))['files']
        if any(Path(n).is_absolute() or '..' in Path(n).parts for n in names):
            raise ValueError('发布清单包含非法路径')
        return sorted(names)
    names = set()
    for folder in ('apps', 'shiju', 'Rhyme', 'Songci_Meter', 'web', 'scripts', 'tools', 'deploy'):
        for path in (root / folder).rglob('*'):
            if path.is_file() and path.suffix in {'.py', '.ps1', '.cs', '.js', '.mjs', '.cjs', '.html', '.css', '.json', '.sql', '.toml', '.csv'} and not any(p in {'__pycache__', 'node_modules', '.git'} for p in path.parts):
                names.add(path.relative_to(root).as_posix())
    names.update(n for n in ('launcher.ps1', 'start-single-player.ps1', 'pyproject.toml') if (root / n).is_file())
    return sorted(names)
