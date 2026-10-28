import io
import json
import subprocess
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from apps.api.main import create_app
from apps.launcher.build_info import build_info

ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIJU_DATA_DIR', str(tmp_path))
    for name in ('DATABASE_URL','SHIJU_DATABASE_PATH'):
        monkeypatch.delenv(name, raising=False)
    with TestClient(create_app()) as value:
        yield value


def test_startup_diagnostics_export_is_bounded_and_redacted(client, tmp_path):
    report = {'schema':1, 'release':'fixture', 'events':[{'message':'api_key="secret-value" Bearer sensitive-token sk-abcdef', 'stack':'trace'}], 'configuration':{'secret':'DO-NOT-COLLECT'}}
    assert client.post('/v1/startup-diagnostics/browser', json=report).status_code == 200
    (tmp_path / 'api-startup.log').write_text('x' * (300 * 1024) + '\npassword="private-value"\n', encoding='utf-8')
    (tmp_path / 'models.toml').write_text('DO-NOT-COLLECT', encoding='utf-8')
    response = client.get('/v1/startup-diagnostics/export')
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        assert 'models.toml' not in z.namelist() and 'shiju.db' not in z.namelist()
        browser = z.read('browser.json').decode()
        json.loads(browser)
        assert 'secret-value' not in browser and 'sensitive-token' not in browser and 'sk-abcdef' not in browser and 'DO-NOT-COLLECT' not in browser
        assert 'private-value' not in z.read('api-startup.log').decode()
        assert len(z.read('api-startup.log')) <= 262144
        assert json.loads(z.read('service.json'))['record_missing'] is True
    assert client.post('/v1/startup-diagnostics/browser', content='x' * 50000).status_code == 413
    assert client.post('/v1/startup-diagnostics/browser', json=[]).status_code == 400
    assert client.post('/v1/startup-diagnostics/browser', json={}, headers={'Origin':'https://other.example'}).status_code == 403
    assert client.get('/health/live').json()['build'] == build_info()


def test_browser_errors_are_files_per_session_and_retry_does_not_erase_history(client):
    session = 'a' * 32
    report = dict(schema=2, session_id=session, revision=1,
                  events=[dict(sequence=1, kind='error', message='first frontend failure', stack='traceback one')])
    receipt = client.post('/v1/startup-diagnostics/browser', json=report).json()
    assert receipt['saved'] and receipt['session_id'] == session and receipt['revision'] == 1
    path, log = Path(receipt['report_path']), Path(receipt['log_path'])
    assert path.is_file() and 'traceback one' in log.read_text(encoding='utf-8')
    client.post('/v1/startup-diagnostics/browser', json=report)
    assert log.read_text(encoding='utf-8').count('"message": "first frontend failure"') == 1
    report['revision'] = 2
    report['events'] = [dict(sequence=2, kind='error', message='second runtime failure')]
    client.post('/v1/startup-diagnostics/browser', json=report)
    stale = dict(schema=2, session_id=session, revision=1, events=[])
    client.post('/v1/startup-diagnostics/browser', json=stale)
    persisted = json.loads(path.read_text(encoding='utf-8'))
    assert persisted['revision'] == 2
    assert [e['sequence'] for e in persisted['events']] == [1, 2]
    other = client.post('/v1/startup-diagnostics/browser', json=dict(schema=2, session_id='b'*32, revision=1,
        events=[dict(sequence=1, kind='error', message='another page failed')])).json()
    assert other['report_path'] != str(path)
    assert 'second runtime failure' in path.read_text(encoding='utf-8')


def test_browser_report_is_not_acknowledged_when_file_write_fails(client, monkeypatch):
    from apps.api import startup_diagnostics
    def fail(*args):
        raise OSError('fixture: disk write failed')
    monkeypatch.setattr(startup_diagnostics, 'write_report', fail)
    response = client.post('/v1/startup-diagnostics/browser', json=dict(schema=2, session_id='c'*32, revision=1,
        events=[dict(sequence=1, kind='error', message='browser failed')]))
    assert response.status_code == 500
    assert response.json().get('saved') is not True
    assert response.json()['diagnostics']['log_path']


def test_browser_report_rejects_path_traversal(client):
    assert client.post('/v1/startup-diagnostics/browser', json=dict(session_id='../outside', events=[])).status_code == 400


def test_launcher_export_reads_recorded_files_without_http_requests(tmp_path):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(503)
            self.end_headers()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    directory = tmp_path / 'startup-diagnostics'
    directory.mkdir()
    saved = dict(state='failed', error='saved startup import failure', build=dict(fingerprint='recorded-build'))
    (directory / 'service.json').write_text(json.dumps(saved))
    output = tmp_path / 'offline.zip'
    try:
        result = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            str(ROOT / 'scripts/export-startup-diagnostics.ps1'), '-Workspace', str(ROOT),
            '-DataDirectory', str(tmp_path), '-Port', str(server.server_port), '-OutputPath', str(output)], capture_output=True)
        assert result.returncode == 0, result.stderr
        assert not requests
        with zipfile.ZipFile(output) as archive:
            record = json.loads(archive.read('launcher.json').decode('utf-8-sig'))
            assert record['source'] == 'persisted-files'
            assert record['service_record'] == saved
            assert json.loads(archive.read('service.json')) == saved
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_powershell_and_python_build_fingerprint_agree():
    command = ". '" + str(ROOT / 'scripts/launcher-instance.ps1') + "'; Get-LauncherBuildFingerprint '" + str(ROOT) + "'"
    result = subprocess.run(['powershell','-NoProfile','-Command',command], capture_output=True,text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == build_info()['fingerprint']


def test_launcher_export_works_without_server(tmp_path):
    output = tmp_path / 'report.zip'
    browser_dir = tmp_path / 'startup-diagnostics'
    browser_dir.mkdir()
    (browser_dir / 'latest-browser.json').write_text(json.dumps({'events': [{'message': 'api_key="sensitive-value"'}]}), encoding='utf-8')
    result = subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT / 'scripts/export-startup-diagnostics.ps1'),'-Workspace',str(ROOT),'-DataDirectory',str(tmp_path),'-Port','1','-OutputPath',str(output)],capture_output=True,text=True)
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(output) as z:
        report = json.loads(z.read('launcher.json').decode('utf-8-sig'))
        assert report['source'] == 'persisted-files'
        assert report['service_record_error']
        assert report['package_fingerprint'] == build_info()['fingerprint']
        browser = json.loads(z.read('latest-browser.json'))
        assert 'sensitive-value' not in browser['events'][0]['message']

@pytest.mark.parametrize('version, reused', [('current', True), ('old', False), ('missing', False)])
def test_launcher_never_silently_reuses_different_application_version(tmp_path, version, reused):
    command = r'''
$ErrorActionPreference='Stop'
. '__HELPERS__'
$root='__ROOT__'; $dataRoot='__DATA__'; $script:Port=18763; $Headless=$true
$script:health=[pscustomobject]@{application='ShiJu';workspace=$root;data_root=$dataRoot;python='C:\env\python.exe';pid=123;build=$null}
if ('__VERSION__' -eq 'current') { $script:health.build=@{fingerprint=(Get-LauncherBuildFingerprint $root)} }
if ('__VERSION__' -eq 'old') { $script:health.build=@{fingerprint='old'} }
function Get-LauncherHealth { param($ServicePort) return $script:health }
function Invoke-RestMethod { param($Uri,$TimeoutSec) return @{environment=@{local_enabled=$false}} }
try {
    $value=Resolve-LauncherInstance 'C:\env\python.exe' '' $false
    if ('__VERSION__' -ne 'current') { throw 'Wrongly reused an old service' }
    if ($value.pid -ne 123) { throw 'Current service should be reused' }
} catch {
    if ('__VERSION__' -eq 'current' -or $_.Exception.Message -notmatch '应用版本') { throw }
}
exit 0
'''
    command = command.replace('__HELPERS__', str(ROOT / 'scripts/launcher-instance.ps1')).replace('__ROOT__',str(ROOT)).replace('__DATA__',str(tmp_path)).replace('__VERSION__',version)
    result=subprocess.run(['powershell','-NoProfile','-Command',command],capture_output=True,text=True)
    assert result.returncode == 0, result.stdout + result.stderr

def test_inline_recorder_is_silent_retries_and_replays_across_reload():
    script = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(process.argv[1],'utf8');
const code=html.match(/<script id="shiju-startup-recorder">([\s\S]*?)<\/script>/)[1];
const saved=new Map(),requests=[];let online=false;
const storage={get length(){return saved.size;},key(i){return [...saved.keys()][i];},getItem(k){return saved.get(k)||null;},setItem(k,v){saved.set(k,v);},removeItem(k){saved.delete(k);}};
function page(id){
  const listeners={},timers=new Map();let next=0;
  function XHR(){this.open=(method,url)=>{this.method=method;this.url=url;};this.setRequestHeader=()=>{};this.send=body=>{const report=JSON.parse(body);requests.push({method:this.method,url:this.url,report});if(!online){this.onerror();return;}this.status=200;this.responseText=JSON.stringify({saved:true,session_id:report.session_id,revision:report.revision});this.onload();};}
  const window={crypto:{randomUUID:()=>id},addEventListener(type,fn){listeners[type]=fn;}};
  const context={window,document:{createElement(){throw new Error('No diagnostic UI may be created');}},location:{origin:'http://127.0.0.1:1',pathname:'/web/prosody-checker/'},navigator:{userAgent:'test-browser'},localStorage:storage,Date,Math,JSON,String,Blob,XMLHttpRequest:XHR,setTimeout(fn){timers.set(++next,fn);return next;},clearTimeout(id){timers.delete(id);}};
  vm.runInNewContext(code,context);
  return {window,listeners,tick(){const key=timers.keys().next().value;assert(key);const fn=timers.get(key);timers.delete(key);fn();}};
}
const first=page('11111111-1111-1111-1111-111111111111');
first.listeners.error({target:first.window,filename:'http://127.0.0.1:1/site.js',message:'module blocked',lineno:1,colno:1,error:{stack:'fixture stack'}});
assert(saved.size===1);first.tick();assert(saved.size===1,'Failed delivery must not erase pending errors');
assert([...saved.values()].some(value=>value.includes('fixture stack')));
const second=page('22222222-2222-2222-2222-222222222222');online=true;
second.tick();second.tick();assert.equal(saved.size,0,'Only persisted acknowledgments clear pending reports');
assert(requests.some(item=>item.report.events.some(event=>event.message.includes('module blocked'))));
second.window.ShijuStartup.ready();second.listeners.unhandledrejection({reason:new Error('runtime failure after ready')});second.tick();
assert(requests.at(-1).report.events.some(event=>event.message.includes('runtime failure after ready')));
assert(requests.every(item=>item.method==='POST'&&item.url==='/v1/startup-diagnostics/browser'));
assert(!code.includes('createElement')&&!code.includes('/export'));
console.log('Silent browser logger: pre-module failure, retry, reload replay and runtime error passed');
"""
    result=subprocess.run(['node','-e',script,str(ROOT / 'web/prosody-checker/index.html')],capture_output=True,text=True)
    assert result.returncode == 0, result.stdout + result.stderr
