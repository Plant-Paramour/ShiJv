import ast
import hashlib
import io
import json
import mimetypes
from pathlib import Path
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient

from apps.api.database import Database
from apps.api.main import create_app
from apps.api.settings import ApiSettings
from apps.gpu_worker.api_client import WorkerApiClient
from tools.build_single_player_source import ROOT, build


@pytest.fixture
def local_app(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIJU_DATA_DIR', str(tmp_path))
    for name in ('DATABASE_URL', 'SHIJU_DATABASE_PATH', 'SHIJU_CONTROL_URL'):
        monkeypatch.delenv(name, raising=False)
    return create_app()


def test_web_modules_ignore_system_plain_text_association(local_app, monkeypatch):
    mimetypes.init()
    monkeypatch.setitem(mimetypes.types_map, '.js', 'text/plain')
    with TestClient(local_app) as client:
        for name in ('site.js', 'app.js', 'sentence-search.js', 'core.js',
                     'prosody-evaluation.js', 'dataset-cache.js', 'chat-markdown.js',
                     'stream-text.js', 'local-rhyme.js'):
            response = client.get('/web/prosody-checker/' + name + '?v=mime-test')
            assert response.status_code == 200, name
            assert response.headers['content-type'].split(';')[0] == 'text/javascript', name
            assert response.headers['cache-control'] == 'no-cache'
        assert client.get('/', follow_redirects=False).headers['location'].endswith('#chat')


def test_static_types_cache_and_missing_files(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from apps.api.static_files import ApplicationStaticFiles
    from starlette.responses import FileResponse
    mimetypes.init()
    app = FastAPI()
    for suffix in ApplicationStaticFiles.MEDIA_TYPES:
        monkeypatch.setitem(mimetypes.types_map, suffix, 'text/plain')
        (tmp_path / ('asset' + suffix)).write_bytes(b'fixture')
    (tmp_path / 'index.html').write_text('<p>fixture</p>', encoding='utf-8')
    app.mount('/assets', ApplicationStaticFiles(directory=tmp_path, html=True))
    with TestClient(app) as client:
        for suffix, expected in ApplicationStaticFiles.MEDIA_TYPES.items():
            url = '/assets/asset' + suffix
            response = client.get(url)
            assert response.status_code == 200
            assert response.headers['content-type'].split(';')[0] == expected
            head = client.head(url)
            assert head.content == b''
            assert head.headers['content-type'] == response.headers['content-type']
            old_etag = FileResponse(tmp_path / ('asset' + suffix), stat_result=(tmp_path / ('asset' + suffix)).stat()).headers['etag']
            assert client.get(url, headers={'If-None-Match': old_etag}).status_code == 200
            cached = client.get(url, headers={'If-None-Match': response.headers['etag']})
            assert cached.status_code == 304
            assert cached.headers['cache-control'] == 'no-cache'
        assert client.get('/assets/').headers['content-type'].startswith('text/html')
        assert client.get('/assets/missing.js').status_code == 404
        assert client.get('/assets/%2e%2e/outside.js').status_code == 404


def test_local_worker_plain_transport_preserves_jobs_without_authentication(local_app):
    with TestClient(local_app) as client:
        user = local_app.state.users.local_profile()
        job = local_app.state.jobs.submit('generate', {'requirement': '春山', 'meter_type': '唐诗', 'form_name': '五言绝句'}, user_id=user['id'])
        claimed = client.post('/local/v1/workers/claim', json={'worker_id': 'local-poetry', 'capabilities': {}, 'wait_seconds': 0})
        assert claimed.status_code == 200, claimed.text
        assert claimed.json()['job_id'] == job.id
        result = {'worker_id': 'local-poetry', 'result': {'candidates': []}}
        assert client.post(f'/local/v1/jobs/{job.id}/complete', json=result).status_code == 204
        assert local_app.state.jobs.get(job.id).status == 'succeeded'
        with local_app.state.database.connect() as db:
            row = db.execute('SELECT request_stored,result_stored FROM jobs WHERE id=?', (job.id,)).fetchone()
        assert json.loads(row['request_stored'])['requirement'] == '春山'
        assert json.loads(row['result_stored']) == {'candidates': []}


def test_backup_is_plain_and_contains_no_key_file(local_app):
    with TestClient(local_app) as client:
        client.patch('/v1/local-profile', json={'display_name': '明文档案'})
        response = client.get('/v1/backup')
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert 'shiju.db' in archive.namelist()
            assert not any(name.endswith(('.key', '.toml')) for name in archive.namelist())
            assert '明文档案'.encode() in archive.read('shiju.db')
        response = client.post('/v1/backup/restore', content=response.content, headers={'Content-Type': 'application/zip'})
        assert response.status_code == 200, response.text
        assert response.json()['restart_required']


def test_local_boundary_remains_loopback_and_same_origin(local_app):
    with TestClient(local_app) as client:
        for headers in ({'Host': 'attacker.test'}, {'Origin': 'https' + '://attacker.test'},
                        {'Origin': 'null'}, {'Sec-Fetch-Site': 'cross-site'}):
            assert client.get('/v1/backup', headers=headers).status_code == 403
        for path in ('/v1/auth/login', '/v1/admin/stats', '/v1/forum/sections', '/internal/v1/workers/claim'):
            assert client.post(path).status_code == 404


def test_worker_client_uses_only_local_url_and_sends_no_auth_headers():
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            received.append((self.path, dict(self.headers), json.loads(body)))
            self.send_response(204)
            self.end_headers()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        worker = WorkerApiClient(f'http://127.0.0.1:{server.server_port}')
        assert worker.claim('fixture', {}, wait_seconds=0) is None
        route, headers, body = received[0]
        assert route == '/local/v1/workers/claim'
        assert 'Authorization' not in headers
        assert not any(name.lower().startswith('x-worker-') for name in headers)
        assert body['worker_id'] == 'fixture'
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)
    with pytest.raises(ValueError):
        WorkerApiClient('https' + '://remote.example:443')


def test_old_database_is_rejected_without_modification(tmp_path):
    path = tmp_path / 'old.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE users(id TEXT)')
        db.execute("INSERT INTO users VALUES ('original')")
    before = path.read_bytes()
    with pytest.raises(ValueError, match='旧版数据目录'):
        Database('sqlite:///' + path.as_posix()).initialize()
    assert path.read_bytes() == before


def test_default_legacy_database_starts_with_separate_persistent_local_database(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIJU_DATA_DIR', str(tmp_path))
    for name in ('DATABASE_URL', 'SHIJU_DATABASE_PATH'):
        monkeypatch.delenv(name, raising=False)
    legacy = tmp_path / 'shiju.db'
    with sqlite3.connect(legacy) as db:
        db.execute('CREATE TABLE users(id TEXT)')
        db.execute("INSERT INTO users VALUES ('legacy-user')")
    before = legacy.read_bytes()
    app = create_app()
    assert app.state.settings.database_path == tmp_path / 'shiju-local.db'
    with TestClient(app) as client:
        assert client.get('/health/live').json()['data_root'] == str(tmp_path)
        assert client.patch('/v1/local-profile', json={'display_name': '新档案'}).status_code == 200
    monkeypatch.delenv('SHIJU_DATABASE_PATH', raising=False)
    reopened = create_app()
    assert reopened.state.users.local_profile()['display_name'] == '新档案'
    assert legacy.read_bytes() == before


def test_explicit_legacy_database_still_requires_conversion(tmp_path, monkeypatch):
    legacy = tmp_path / 'shiju.db'
    with sqlite3.connect(legacy) as db:
        db.execute('CREATE TABLE users(id TEXT)')
    monkeypatch.setenv('SHIJU_DATABASE_PATH', str(legacy))
    monkeypatch.delenv('DATABASE_URL', raising=False)
    with pytest.raises(ValueError, match='旧版数据目录'):
        create_app()


def test_source_is_plain_and_has_no_management_implementation():
    forbidden_imports = {'cryptography', 'hmac', 'bcrypt', 'argon2'}
    for path in (ROOT / 'apps').rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        for node in ast.walk(tree):
            names = ([node.module or ''] if isinstance(node, ast.ImportFrom) else
                     [item.name for item in node.names] if isinstance(node, ast.Import) else [])
            assert not any(name.split('.')[0] in forbidden_imports for name in names), path
    script = (ROOT / 'web/prosody-checker/site.js').read_text(encoding='utf-8')
    html = (ROOT / 'web/prosody-checker/index.html').read_text(encoding='utf-8')
    assert 'developer' not in script and '/v1/admin/' not in script
    assert 'allMode' not in script and 'allConversationMode' not in script
    assert 'data-view="admin"' not in html
    assert not (ROOT / 'apps/api/routes/admin.py').exists()
    assert not (ROOT / 'apps/api/routes/forum.py').exists()


def test_plain_source_export_is_self_contained():
    path = ROOT / 'dist' / f'plain-source-test-{uuid.uuid4().hex}.zip'
    try:
        build(path)
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            assert 'apps/api/schema.sql' in names
            assert not any(name.startswith(('output/', 'var/', 'experiments/', 'deploy/fc', 'deploy/systemd')) for name in names)
            assert not any(name.endswith(('.key', '.db', '.pem', '.png', '.7z')) for name in names)
            manifest = json.loads(archive.read('SOURCE-MANIFEST.json'))
            for name, digest in manifest['files'].items():
                content = archive.read(name)
                assert hashlib.sha256(content).hexdigest() == digest
                if name.endswith('.py'):
                    ast.parse(content.decode('utf-8-sig'), filename=name)
    finally:
        path.unlink(missing_ok=True)
