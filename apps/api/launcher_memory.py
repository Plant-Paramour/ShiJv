"""Persistent launcher choices. Validation uses file metadata, never imports GPU libraries."""
import argparse
import hashlib
import json
import time
from pathlib import Path

from apps.launcher.model_catalog import verified_report


def environment_stamp(python):
    executable = Path(python).resolve()
    if not executable.is_file():
        raise ValueError('虚拟环境已不存在，请重新选择环境')
    files = [executable]
    files += list(executable.parent.glob('python*._pth'))
    custom = executable.parent / 'Lib/site-packages/sitecustomize.py'
    if custom.is_file():
        files.append(custom)
    packages = executable.parent / 'Lib' / 'site-packages'
    files += list(packages.glob('*.dist-info/METADATA'))
    project = Path(__file__).resolve().parents[2]
    records = [(str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(set(files)) if p.is_file()]
    sources = list((project / 'apps/api').rglob('*.py'))
    sources += [project / 'pyproject.toml', project / 'apps/launcher/environment_probe.py']
    records += [(p.relative_to(project).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest())
                for p in sorted(sources) if p.is_file()]
    return hashlib.sha256(json.dumps(records).encode()).hexdigest()


class LauncherMemory:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / 'launcher-memory.json'

    def read(self):
        try:
            return json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {'version': 1, 'environments': [], 'last_pair': None}

    def save(self, data):
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)

    def remember(self, app, inference, local):
        data = self.read()
        entry = dict(application_python=str(Path(app).resolve()), inference_python=str(Path(inference).resolve()),
                     local_enabled=local, app_stamp=environment_stamp(app),
                     inference_stamp=environment_stamp(inference) if local else '', checked_at=time.time())
        previous = next((e for e in data['environments'] if self.key(e) == self.key(entry)), {})
        entry['models'] = previous.get('models', [])
        data['environments'] = [e for e in data['environments'] if self.key(e) != self.key(entry)][-31:] + [entry]
        self.save(data)

    @staticmethod
    def key(entry):
        return (entry['application_python'].lower(), entry['inference_python'].lower(), entry['local_enabled'])

    def valid_environment(self, entry):
        try:
            return (environment_stamp(entry['application_python']) == entry['app_stamp'] and
                    (not entry['local_enabled'] or environment_stamp(entry['inference_python']) == entry['inference_stamp']))
        except (OSError, ValueError, KeyError):
            return False

    def check(self, app, inference, local, model=''):
        target = dict(application_python=str(Path(app).resolve()), inference_python=str(Path(inference).resolve()), local_enabled=local)
        entry = next((e for e in self.read()['environments'] if self.key(e) == self.key(target)), None)
        if not entry or not self.valid_environment(entry):
            return {'ok': False, 'reason': '此环境组合尚未检测，或环境依赖／程序发生变化，请先点击“重新检测”'}
        if local:
            report = verified_report(self.directory, model, inference) if model else None
            if not report or not report.get('ok'):
                return {'ok': False, 'reason': '请选择已通过完整验收的环境—模型配对；文件变更后需重新测试模型'}
        return {'ok': True, 'entry': target | {'model_path': model}}

    def adopt_verified_pair(self, app, inference, model):
        """Register both environments from one completed, current model test."""
        report = verified_report(self.directory, model, inference)
        core = (report or {}).get('application_environment', {})
        if (not report or not report.get('ok') or not report.get('environment', {}).get('ok')
                or not core.get('ok') or Path(core.get('python', '')).resolve() != Path(app).resolve()
                or Path(report.get('python', '')).resolve() != Path(inference).resolve()
                or report.get('application_stamp') != environment_stamp(app)
                or report.get('inference_stamp') != environment_stamp(inference)):
            raise ValueError('模型或环境在验收后发生变化，未保存配对；请重新完整测试模型')
        self.remember(app, inference, True)
        result = self.check(app, inference, True, model)
        if not result['ok']:
            raise ValueError(result['reason'])
        data = self.read()
        data['last_pair'] = result['entry']
        entry = next(e for e in data['environments'] if self.key(e) == self.key(result['entry']))
        if model not in entry['models']:
            entry['models'].append(model)
        self.save(data)

    def select_unverified(self, app, inference, local, model):
        if not Path(app).is_file() or (local and not Path(inference).is_file()):
            raise ValueError('虚拟环境不存在，无法启动')
        if local and (not model or not Path(model).is_absolute() or not (Path(model) / 'config.json').is_file()):
            raise ValueError('请先选择包含 config.json 的本地模型目录')
        data = self.read()
        target = dict(application_python=str(Path(app).resolve()), inference_python=str(Path(inference).resolve()), local_enabled=local)
        entry = next((e for e in data['environments'] if self.key(e) == self.key(target)), None)
        if entry is None:
            entry = target | dict(app_stamp='', inference_stamp='', checked_at=0, models=[])
            data['environments'].append(entry)
        if model and model not in entry.setdefault('models', []):
            entry['models'].append(model)
        data['last_pair'] = target | {'model_path': model}
        self.save(data)
        return {'ok': True, 'entry': data['last_pair'], 'verified': False}

    def pairs(self):
        paths = set()
        for file in (self.directory / 'model-checks').glob('*.json'):
            try:
                report = json.loads(file.read_text(encoding='utf-8'))
                if report.get('ok') and report.get('model'):
                    paths.add(report['model'])
            except (OSError, ValueError):
                continue
        result = []
        data = self.read()
        changed = False
        for entry in data['environments']:
            valid = self.valid_environment(entry)
            models = sorted(paths | set(entry.get('models', []))) if entry['local_enabled'] else ['']
            for model in models:
                report = verified_report(self.directory, model, entry['inference_python']) if valid and model else None
                passed = bool(report and report.get('ok'))
                known = entry.setdefault('models', [])
                if entry['local_enabled'] and not passed and model not in known:
                    continue
                if passed and model not in known:
                    known.append(model); changed = True
                result.append({k: entry[k] for k in ('application_python', 'inference_python', 'local_enabled')} |
                              {'model_path': model, 'valid': valid and (not entry['local_enabled'] or passed), 'checked_at': entry['checked_at']})
        if changed:
            self.save(data)
        return {'pairs': result, 'last_pair': data.get('last_pair')}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--action', choices=['list', 'remember', 'check', 'select', 'select-unverified'], required=True)
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--application-python', required=True)
    parser.add_argument('--inference-python', required=True)
    parser.add_argument('--local', action='store_true')
    parser.add_argument('--model', default='')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    memory = LauncherMemory(args.data_dir)
    try:
        if args.action == 'remember':
            memory.remember(args.application_python, args.inference_python, args.local)
        if args.action == 'select-unverified':
            result = memory.select_unverified(args.application_python, args.inference_python, args.local, args.model)
        else:
            result = memory.pairs() if args.action in {'list', 'remember'} else memory.check(args.application_python, args.inference_python, args.local, args.model)
        if args.action == 'select' and result['ok']:
            data = memory.read(); data['last_pair'] = result['entry']; memory.save(data)
    except (OSError, ValueError) as exc:
        result = {'ok': False, 'reason': str(exc), 'pairs': []}
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
