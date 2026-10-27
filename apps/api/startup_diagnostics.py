"""Bounded, local-only startup reports. Never collect user configuration or databases."""
import io
import json
import re
import zipfile
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

router = APIRouter(prefix='/v1/startup-diagnostics', tags=['diagnostics'])
MAX_REPORT = 48 * 1024


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
        report = {key: report[key] for key in ('schema', 'release', 'page', 'browser', 'started_at', 'events', 'resources', 'health', 'truncated') if key in report}
    except (ValueError, TypeError):
        raise HTTPException(400, '诊断记录格式错误')
    target = report_path(request)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(clean_report(report), ensure_ascii=False), encoding='utf-8')
    temporary.replace(target)
    return {'saved': True}


@router.get('/export')
def export_report(request: Request):
    data = io.BytesIO()
    root = request.app.state.settings.database_path.parent
    with zipfile.ZipFile(data, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('service.json', json.dumps({
            'build': request.app.state.build_info,
            'workspace': str(request.app.state.project_root), 'data_root': str(root),
        }, ensure_ascii=False, indent=2))
        for name, path in [('browser.json', report_path(request))] + [
            (name, root / name) for name in ('api-startup.log', 'api-bootstrap.stderr.log', 'api-bootstrap.stdout.log')
        ]:
            if path.is_file():
                with path.open('rb') as stream:
                    size = stream.seek(0, 2)
                    stream.seek(max(0, size - 256 * 1024))
                    content = stream.read().decode('utf-8', errors='replace')
                if name == 'browser.json':
                    content = json.dumps(clean_report(json.loads(content)), ensure_ascii=False)
                else:
                    content = redact(content)
                archive.writestr(name, content)
    return Response(data.getvalue(), media_type='application/zip', headers={
        'Content-Disposition': 'attachment; filename="shiju-startup-diagnostics.zip"',
        'Cache-Control': 'no-store',
    })
