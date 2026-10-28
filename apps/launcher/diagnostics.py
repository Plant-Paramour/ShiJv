"""Persistent operation diagnostics shared by launchers and inference processes."""
from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import traceback
import uuid

_prune_times = {}


def redact(text):
    text = re.sub(r'(?i)(Bearer\s+)[\w.\-]+', r'\1[redacted]', str(text))
    text = re.sub(r'\bsk-[A-Za-z0-9_-]+', '[redacted]', text)
    text = re.sub(r'(?i)((?:api[_-]?key|authorization|access[_-]?token|password|secret)["\s]*[:=]["\s]*)[^\s,";}]+', r'\1[redacted]', text)
    return re.sub(r'(https?://[^\s?"<>]+)\?[^\s"<>]+', r'\1?[redacted]', text)


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        return redact(super().format(record))


class OperationDiagnostic:
    def __init__(self, directory, stage, diagnostic_id=None):
        self.id = diagnostic_id or uuid.uuid4().hex
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', self.id):
            self.id = uuid.uuid4().hex
        self.stage, self.started = stage, time.monotonic()
        self.write_error = ''
        self.root = None
        self.logger = logging.getLogger('shiju.diagnostic.' + self.id + '.' + stage)
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        self.handler = None
        for base in (Path(directory) / 'diagnostics', Path(tempfile.gettempdir()) / 'ShiJu/diagnostics'):
            try:
                root = base / self.id
                root.mkdir(parents=True, exist_ok=True)
                handler = RotatingFileHandler(root / (stage + '.log'), maxBytes=10 * 1024**2,
                                              backupCount=3, encoding='utf-8')
                handler.setFormatter(RedactingFormatter('%(asctime)s %(levelname)s %(message)s'))
                self.logger.addHandler(handler)
                self.handler, self.root = handler, root.resolve()
                break
            except OSError as exc:
                self.write_error += f'{base}: {exc}\n'
        self.event('started', python=sys.executable, cwd=os.getcwd(), sys_path=list(sys.path))
        self.save(dict(completed=False, pid=os.getpid(), diagnostics=self.metadata))
        retention_key = str(Path(directory).absolute())
        if time.monotonic() - _prune_times.get(retention_key, -86401) > 86400:
            _prune_times[retention_key] = time.monotonic()
            try:
                prune(directory)
            except OSError as exc:
                self.event('retention_warning', error=str(exc))

    @property
    def metadata(self):
        return dict(diagnostic_id=self.id, stage=self.stage, error_code=getattr(self, 'error_code', ''),
                    log_path=str(self.root / (self.stage + '.log')) if self.root else None,
                    report_path=str(self.root / (self.stage + '.json')) if self.root else None,
                    log_write_error=self.write_error)

    def event(self, event, **fields):
        self.logger.info(json.dumps(dict(event=event, elapsed_seconds=time.monotonic()-self.started, **fields), ensure_ascii=False))

    def save(self, report):
        if not self.root:
            return
        try:
            target = self.root / (self.stage + '.json')
            temporary = target.with_suffix('.' + uuid.uuid4().hex + '.tmp')
            temporary.write_text(redact(json.dumps(report, ensure_ascii=False, indent=2)), encoding='utf-8')
            temporary.replace(target)
        except OSError as exc:
            self.write_error += f'报告写入失败：{exc}\n'

    def fail(self, exc, code='OPERATION_FAILED'):
        self.error_code = code
        self.event('failed', error=str(exc), error_code=code, traceback=''.join(traceback.format_exception(exc)))
        return self.describe(str(exc))

    def describe(self, message):
        if self.root:
            return f'{message}\n阶段：{self.stage}\n日志：{self.metadata["log_path"]}\n诊断编号：{self.id}'
        return f'{message}\n阶段：{self.stage}\n日志写入失败：{self.write_error}'

    def close(self):
        if self.handler:
            self.logger.removeHandler(self.handler)
            self.handler.close()
            self.handler = None

    def run(self, command, *, env=None, timeout=600, cwd=None, cancel=None):
        import subprocess
        import threading
        started = time.monotonic()
        self.event('process_start', executable=str(command[0]), cwd=str(cwd or os.getcwd()))
        process = subprocess.Popen(list(map(str, command)), env=env, cwd=cwd,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        def drain():
            try:
                for line in iter(process.stdout.readline, b''):
                    self.logger.info(line.decode('utf-8', errors='replace').rstrip())
            finally:
                process.stdout.close()
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        try:
            while process.poll() is None:
                if cancel is not None and cancel.is_set():
                    raise RuntimeError('操作已取消')
                if time.monotonic() - started > timeout:
                    raise subprocess.TimeoutExpired(command, timeout)
                time.sleep(.1)
            code = process.returncode
        except BaseException:
            terminate_process(process)
            process.wait()
            raise
        finally:
            reader.join(timeout=5)
            self.event('process_exit', exit_code=process.returncode, elapsed_seconds_total=time.monotonic()-started)
        return code


def terminate_process(process):
    import subprocess
    if process.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
    if process.poll() is None:
        process.kill()


def prune(directory):
    """Remove only completed owned reports older than 30 days, protecting latest failure."""
    import shutil
    base = Path(directory) / 'diagnostics'
    completed = []
    if not base.is_dir():
        return
    for root in base.iterdir():
        if not root.is_dir() or root.is_symlink() or not re.fullmatch(r'[0-9a-f]{32}', root.name):
            continue
        reports = list(root.glob('*.json'))
        try:
            values = [value for p in reports
                      if isinstance(value := json.loads(p.read_text(encoding='utf-8')), dict) and 'completed' in value]
            if values and all(v.get('completed') for v in values):
                completed.append((root.stat().st_mtime, root, any(v.get('ok') is False for v in values)))
        except (OSError, ValueError):
            continue
    failures = [item for item in completed if item[2]]
    protected = max(failures)[1] if failures else None
    for modified, root, _ in completed:
        if root != protected and modified < time.time() - 30 * 86400 and root.resolve().parent == base.resolve():
            shutil.rmtree(root)
