import ast
import hashlib
import io
import json
import mimetypes
from pathlib import Path
import sqlite3
import subprocess
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


def test_local_datasets_need_no_version_files_and_disable_caching(local_app):
    assert not hasattr(local_app.state, 'meter_version')
    assert not hasattr(local_app.state, 'rhyme_version')
    with TestClient(local_app) as client:
        for path in ('/Songci_Meter/index.json', '/Rhyme/Pinshui.json'):
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers['cache-control'] == 'no-store'
        for path in ('/meter-version', '/meter-templates'):
            assert client.get(path).status_code == 404


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
            assert client.get(url, headers={'If-None-Match': old_etag, 'If-Modified-Since': response.headers['last-modified']}).status_code == 200
            assert client.get(url, headers={'If-Modified-Since': response.headers['last-modified']}).status_code == 200
            cached = client.get(url, headers={'If-None-Match': response.headers['etag']})
            assert cached.status_code == 304
            assert cached.headers['cache-control'] == 'no-cache'
        assert client.get('/assets/').headers['content-type'].startswith('text/html')
        assert client.get('/assets/missing.js').status_code == 404
        assert client.get('/assets/%2e%2e/outside.js').status_code == 404


def test_entire_page_dependency_graph_over_http(local_app, monkeypatch):
    from apps.launcher.web_assets import check_web_assets
    mimetypes.init()
    for suffix in ('.js', '.mjs', '.css', '.html'):
        monkeypatch.setitem(mimetypes.types_map, suffix, 'text/plain')
    with TestClient(local_app) as client:
        state = client.get('/health/web').json()
        assert state['state'] == 'ready'
        assert set(state['files']) == set(check_web_assets(ROOT))
        for name, digest in state['files'].items():
            response = client.get('/' + name)
            assert response.status_code == 200, name
            assert hashlib.sha256(response.content).hexdigest() == digest, name
            expected = {'.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.html': 'text/html'}[Path(name).suffix]
            assert response.headers['content-type'].split(';')[0] == expected, name
        missing = client.get('/web/prosody-checker/missing.js')
        assert missing.status_code == 404
        assert missing.headers['content-type'].startswith('application/json')
        assert '<html' not in missing.text


def test_build_dependency_validation_rejects_missing_and_case_mismatch():
    from apps.launcher.web_assets import web_assets
    entries = {'web/prosody-checker/index.html': b'<script type="module" src="./app.js?v=1"></script>',
               'web/prosody-checker/app.js': b'import { x } from "./Dependency.js?v=2";',
               'web/prosody-checker/dependency.js': b'export const x=1;'}
    with pytest.raises(ValueError, match='Dependency.js'):
        web_assets(entries)
    entries['web/prosody-checker/Dependency.js'] = entries.pop('web/prosody-checker/dependency.js')
    assert len(web_assets(entries)) == 3
    del entries['web/prosody-checker/app.js']
    with pytest.raises(ValueError, match='app.js'):
        web_assets(entries)


def test_web_assets_ignores_import_like_comments():
    from apps.launcher.web_assets import web_assets
    entries = {
        'web/prosody-checker/index.html': b'<script type="module" src="./app.js"></script>',
        'web/prosody-checker/app.js': b'''/* from "extension" */\n// import "missing.js"\nimport "./real.js";''',
        'web/prosody-checker/real.js': b'export const x = 1;',
    }
    assert len(web_assets(entries)) == 3
    del entries['web/prosody-checker/real.js']
    with pytest.raises(ValueError, match='real.js'):
        web_assets(entries)


def test_web_manifest_rejects_mixed_release(tmp_path):
    from apps.launcher.web_assets import check_web_assets
    page = tmp_path / 'web/prosody-checker/index.html'
    page.parent.mkdir(parents=True)
    page.write_text('<p>release</p>', encoding='utf-8')
    manifest = {'files': {'web/prosody-checker/index.html': hashlib.sha256(page.read_bytes()).hexdigest()}}
    (tmp_path / 'RELEASE-MANIFEST.json').write_text(json.dumps(manifest))
    assert check_web_assets(tmp_path)
    page.write_text('<p>other release</p>', encoding='utf-8')
    with pytest.raises(ValueError, match='发布清单不一致'):
        check_web_assets(tmp_path)


def test_missing_web_assets_keep_diagnostics_live_but_not_ready(tmp_path, monkeypatch):
    import apps.launcher.web_assets as assets
    monkeypatch.setenv('SHIJU_DATA_DIR', str(tmp_path))
    def missing(root):
        raise ValueError('网页资源缺失：web/prosody-checker/core.js')
    monkeypatch.setattr(assets, 'check_web_assets', missing)
    app = create_app()
    with TestClient(app) as client:
        assert client.get('/health/live').status_code == 200
        assert client.get('/health/ready').status_code == 503
        state = client.get('/health/web').json()
        assert state['state'] == 'failed'
        assert 'core.js' in state['error']
        assert Path(state['diagnostics']['log_path']).is_file()


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


def test_uploaded_avatar_is_served_and_rendered_after_replacement(local_app):
    from PIL import Image

    def image_bytes(color):
        output = io.BytesIO()
        Image.new('RGB', (32, 32), color).save(output, format='JPEG')
        return output.getvalue()

    with TestClient(local_app) as client:
        headers = {'Content-Type': 'image/jpeg'}
        first = client.post('/v1/profile/me/avatar', content=image_bytes('red'), headers=headers)
        assert first.status_code == 200, first.text
        avatar_url = first.json()['avatar_url']
        first_image = client.get(avatar_url)
        assert first_image.status_code == 200
        assert 'no-store' in first_image.headers['cache-control']
        second = client.post('/v1/profile/me/avatar', content=image_bytes('blue'), headers=headers)
        assert second.status_code == 200
        assert client.get('/v1/profile/me').json()['avatar_url'] == avatar_url
        assert client.get(avatar_url).content != first_image.content

    script = (ROOT / 'web/prosody-checker/site.js').read_text(encoding='utf-8')
    function = script.split('function setUserAvatar(element, user) {', 1)[1].split('\n}', 1)[0]
    runner = '''
const document = { createElement: () => ({}) };
function setUserAvatar(element, user) { FUNCTION_BODY }
for (const url of [process.argv[1], '/media/avatars/old.jpg']) {
  const element = { replaceChildren() { this.children = []; }, append(child) { this.children.push(child); } };
  setUserAvatar(element, {avatar_url: url});
  if (element.children.length !== 1 || element.children[0].src !== url) throw new Error('avatar not rendered: ' + url);
}
const element = { replaceChildren() { this.children = []; }, append(child) { this.children.push(child); } };
setUserAvatar(element, {avatar_url: 'https://example.com/avatar.jpg'});
if (element.children.length || element.textContent !== '客') throw new Error('unexpected external avatar');
'''.replace('FUNCTION_BODY', function)
    subprocess.run(['node', '-e', runner, avatar_url], check=True)


def test_backup_restore_stages_on_data_volume_and_restores_content(local_app, monkeypatch):
    import apps.api.routes.backup as backup
    data_root = local_app.state.settings.database_path.parent
    original_replace = backup.os.replace

    def same_volume_replace(source, target):
        # Model Windows rejecting a move from the system temp drive to the data drive.
        assert Path(source).is_relative_to(data_root)
        assert Path(target).is_relative_to(data_root)
        return original_replace(source, target)

    with TestClient(local_app) as client:
        client.patch('/v1/local-profile', json={'display_name': '备份时的名字'})
        avatar = data_root / 'avatars' / 'fixture.png'
        avatar.write_bytes(b'original-avatar')
        archive = client.get('/v1/backup').content
        client.patch('/v1/local-profile', json={'display_name': '修改后的名字'})
        avatar.write_bytes(b'changed-avatar')
        monkeypatch.setattr(backup.os, 'replace', same_volume_replace)
        restored = client.post('/v1/backup/restore', content=archive,
                               headers={'Content-Type': 'application/zip'})
        assert restored.status_code == 200, restored.text
        assert local_app.state.users.local_profile()['display_name'] == '备份时的名字'
        assert avatar.read_bytes() == b'original-avatar'
        assert not list(data_root.glob('shiju-restore-*'))


def test_empty_backup_restore_leaves_data_unchanged(local_app):
    with TestClient(local_app) as client:
        client.patch('/v1/local-profile', json={'display_name': '保留数据'})
        response = client.post('/v1/backup/restore', content=b'',
                               headers={'Content-Type': 'application/zip'})
        assert response.status_code == 400
        assert '备份文件为空' in response.json()['detail']
        assert local_app.state.users.local_profile()['display_name'] == '保留数据'


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
    frontend = ROOT / 'web/prosody-checker'
    assert not (frontend / 'register-page.css').exists()
    for name in ('site.js', 'styles.css', 'index.html'):
        source = (frontend / name).read_text(encoding='utf-8')
        for marker in ('forum', 'thread-collections', 'favorite-thread',
                       'register-panel', 'login-dialog', 'login-button',
                       'home-hero', 'drawer-notifications', '#notifications'):
            assert marker not in source, (name, marker)


def test_local_app_has_no_retired_web_pages(local_app):
    with TestClient(local_app) as client:
        for path in ('/main', '/admin', '/forum', '/login', '/register',
                     '/main.html', '/admin.html', '/forum.html'):
            assert client.get(path).status_code == 404, path


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
