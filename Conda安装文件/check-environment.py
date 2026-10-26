"""Run with the environment being checked. Does not import inference packages."""
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys


def check(expected, actual, version):
    normalize = lambda value: value.lower().replace('_', '-').replace('.', '-')
    installed = {normalize(k): v for k, v in actual.items()}
    missing, mismatched = [], {}
    for name, wanted in expected.items():
        found = installed.get(normalize(name))
        if found is None:
            missing.append(name)
        elif found != wanted:
            mismatched[name] = dict(expected=wanted, actual=found)
    return dict(ok=not missing and not mismatched and version == '3.12.10',
                python=version, expected_python='3.12.10', missing=missing, mismatched=mismatched)


if __name__ == '__main__':
    root = Path(__file__).resolve().parent
    expected = json.loads((root / 'expected-packages.json').read_text(encoding='utf-8'))
    actual = {d.metadata['Name']: d.version for d in importlib.metadata.distributions()}
    result = check(expected, actual, platform.python_version())
    result['interpreter'] = sys.executable
    process = subprocess.run([sys.executable, '-m', 'pip', '--isolated', 'check'],
                             capture_output=True, text=True, errors='replace')
    result['pip_check'] = dict(ok=process.returncode == 0, output=process.stdout + process.stderr)
    result['ok'] = result['ok'] and result['pip_check']['ok']
    (root / 'check-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    (root / 'actual-dependencies.json').write_text(json.dumps(actual, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True, indent=2))
    raise SystemExit(0 if result['ok'] else 1)
