"""Identify the application source actually loaded by the service."""
import hashlib
from pathlib import Path

RELEASE = '20261027-startup-diagnostics-1'
FILES = ('apps/api/main.py', 'apps/api/static_files.py',
         'apps/api/startup_diagnostics.py', 'web/prosody-checker/index.html',
         'web/prosody-checker/site.js')


def build_info(root=None):
    root = Path(root or Path(__file__).resolve().parents[2])
    hashes = [hashlib.sha256((root / name).read_bytes()).hexdigest()
              if (root / name).is_file() else 'missing' for name in FILES]
    return {'release': RELEASE, 'fingerprint': hashlib.sha256('|'.join(hashes).encode()).hexdigest()}
