"""Bounded diagnostic collection, without database or model configuration."""
import json
import os
import tempfile
from pathlib import Path
from .diagnostics import redact


def entries(directory):
    root = Path(directory)
    sources = [('operations', root / 'diagnostics')]
    sources.append(('fallback', Path(tempfile.gettempdir()) / 'ShiJu/diagnostics'))
    local = Path(os.getenv('LOCALAPPDATA', str(root))) / 'ShiJu/deployment/reports'
    sources.append(('deployment', local))
    manifest = []
    budget = 32 * 1024**2
    for label, base in sources:
        if not base.is_dir():
            continue
        files = sorted((p for p in base.rglob('*') if p.is_file() and p.suffix in {'.log', '.json', '.1', '.2', '.3'}
                        and p.resolve().is_relative_to(base.resolve())), key=lambda p: p.stat().st_mtime, reverse=True)[:600]
        for path in files:
            size = path.stat().st_size
            take = min(size, 256 * 1024, budget)
            if take <= 0:
                break
            with path.open('rb') as stream:
                stream.seek(max(0, size - take))
                data = redact(stream.read().decode('utf-8', errors='replace'))
            name = label + '/' + path.relative_to(base).as_posix()
            manifest.append(dict(name=name, path=str(path.resolve()), original_bytes=size, truncated=take < size))
            budget -= take
            yield name, data
    yield 'diagnostic-export-index.json', json.dumps(dict(files=manifest, byte_limit=32 * 1024**2, file_limit_per_source=600), ensure_ascii=False, indent=2)
