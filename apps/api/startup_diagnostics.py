"""Bounded, local-only startup reports. Never collect user configuration or databases."""
import io
import json
import re
import zipfile
import uuid
import os
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

router = APIRouter(prefix='/v1/startup-diagnostics', tags=['diagnostics'])
MAX_REPORT = 48 * 1024
_browser_lock = threading.Lock()


def write_report(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def redact(text):
    text = re.sub(r'(?i)(Bearer\s+)[\w.\-]+', r'\1[redacted]', text)
    text = re.sub(r'(?i)((?:api[_-]?key|authorization|access[_-]?token|password|secret)["\s]*[:=]["\s]*)[^\s,";}]+', r'\1[redacted]', text)
    text = re.sub(r'\bsk-[A-Za-z0-9_-]+', '[redacted]', text)
    text = re.sub(r'(https?://[^\s?"<>]+)\?[^\s"<>]+', r'\1?[redacted]', text)
    return text


def report_path(request):
    return request.app.state.settings.database_path.parent / 'startup-diagnostics' / 'latest-browser.json'


def clean_report(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [clean_report(item) for item in value]
    if isinstance(value, dict):
        return {key: clean_report(item) for key, item in value.items()}
    return value


@router.post('/browser')
async def browser_report(request: Request):
    raw = await request.body()
    if len(raw) > MAX_REPORT:
        raise HTTPException(413, '诊断记录过大')
    try:
        report = json.loads(raw)
        if not isinstance(report, dict):
            raise ValueError()
        # Only the startup recorder's envelope is accepted, never arbitrary files.
        report = {key: report[key] for key in ('schema', 'session_id', 'revision', 'release', 'page', 'browser', 'started_at', 'closed', 'events', 'resources', 'health', 'truncated') if key in report}
        session = report.get('session_id') or uuid.uuid4().hex
        if not isinstance(session, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', session):
            raise ValueError()
        revision = report.get('revision', 0)
        if type(revision) is not int or not 0 <= revision <= 10**12 or not isinstance(report.get('events', []), list):
            raise ValueError()
        report.update(session_id=session, revision=revision)
        events = []
        for event in report.get('events', []):
            if not isinstance(event, dict):
                raise ValueError()
            sequence = event.get('sequence', len(events) + 1)
            if type(sequence) is not int or not 0 < sequence <= 10**12:
                raise ValueError()
            if report.get('schema') == 2 and sequence > revision:
                raise ValueError()
            events.append({'sequence': sequence, **{key: str(event[key])[:4000] for key in ('time', 'kind', 'message', 'stack') if key in event}})
        report['events'] = events
    except (ValueError, TypeError):
        raise HTTPException(400, '诊断记录格式错误')
    from apps.launcher.diagnostics import OperationDiagnostic
    with _browser_lock:
        diagnostic = OperationDiagnostic(request.app.state.settings.database_path.parent, 'browser', session)
        try:
            if not diagnostic.root:
                raise OSError(diagnostic.describe('浏览器错误日志无法落盘'))
            snapshot = diagnostic.root / 'browser-snapshot.json'
            previous = {}
            if snapshot.is_file():
                try:
                    previous = json.loads(snapshot.read_text(encoding='utf-8'))
                    if (not isinstance(previous, dict) or type(previous.get('revision')) is not int
                            or type(previous.get('highest_sequence')) is not int
                            or not isinstance(previous.get('events'), list)
                            or any(not isinstance(e, dict) or type(e.get('sequence')) is not int for e in previous['events'])):
                        raise ValueError('已保存的浏览器日志索引格式损坏')
                except (ValueError, OSError) as exc:
                    diagnostic.event('snapshot_unreadable', error=str(exc))
                    previous = {}
            report = clean_report(report)
            high_water = previous.get('highest_sequence', 0)
            for event in report['events']:
                if event['sequence'] > high_water:
                    diagnostic.event('browser_event', session_id=session, **event)
            if revision >= previous.get('revision', 0):
                history = {event['sequence']: event for event in previous.get('events', [])}
                history.update({event['sequence']: event for event in report['events']})
                report['events'] = [history[key] for key in sorted(history)[-200:]]
                report['history_truncated'] = len(history) > 200 or previous.get('history_truncated', False)
                report['highest_sequence'] = max([high_water] + list(history))
                if any(e.get('kind') == 'error' for e in report['events']):
                    diagnostic.error_code = 'BROWSER_FRONTEND_ERROR'
                report['diagnostics'] = diagnostic.metadata
                write_report(snapshot, report)
                try:
                    write_report(report_path(request), report)
                except OSError as exc:
                    diagnostic.event('latest_index_unwritable', error=str(exc))
            effective = report if revision >= previous.get('revision', 0) else previous
            diagnostic.save(dict(completed=True, ok=not any(e.get('kind') == 'error' for e in effective.get('events', [])),
                                 snapshot_path=str(snapshot), diagnostics=diagnostic.metadata))
            request.state.diagnostics = diagnostic.metadata
            return {'saved': True, 'session_id': session, 'revision': revision, 'log_path': diagnostic.metadata['log_path'], 'report_path': str(snapshot)}
        finally:
            diagnostic.close()


@router.get('/export')
def export_report(request: Request):
    data = io.BytesIO()
    root = request.app.state.settings.database_path.parent
    with zipfile.ZipFile(data, 'w', zipfile.ZIP_DEFLATED) as archive:
        service_snapshot = root / 'startup-diagnostics/service.json'
        if service_snapshot.is_file():
            archive.writestr('service.json', redact(service_snapshot.read_text(encoding='utf-8')[:256 * 1024]))
        else:
            archive.writestr('service.json', json.dumps({'record_missing': True, 'expected_path': str(service_snapshot)}, ensure_ascii=False))
        for name, path in [('browser.json', report_path(request))] + [
            (name, root / name) for name in ('api-startup.log', 'api-bootstrap.stderr.log', 'api-bootstrap.stdout.log')
        ]:
            if path.is_file():
                with path.open('rb') as stream:
                    size = stream.seek(0, 2)
                    stream.seek(max(0, size - 256 * 1024))
                    content = stream.read().decode('utf-8', errors='replace')
                if name == 'browser.json':
                    try:
                        content = json.dumps(clean_report(json.loads(content)), ensure_ascii=False)
                    except ValueError:
                        content = redact(content)
                else:
                    content = redact(content)
                archive.writestr(name, content)
        from apps.launcher.diagnostic_export import entries
        for name, content in entries(root):
            archive.writestr(name, content)
    return Response(data.getvalue(), media_type='application/zip', headers={
        'Content-Disposition': 'attachment; filename="shiju-startup-diagnostics.zip"',
        'Cache-Control': 'no-store',
    })
