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
        assert json.loads(z.read('service.json'))['build'] == build_info()
    assert client.post('/v1/startup-diagnostics/browser', content='x' * 50000).status_code == 413
    assert client.post('/v1/startup-diagnostics/browser', json=[]).status_code == 400
    assert client.post('/v1/startup-diagnostics/browser', json={}, headers={'Origin':'https://other.example'}).status_code == 403
    assert client.get('/health/live').json()['build'] == build_info()


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
        assert report['health']['error']
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

def test_inline_recorder_survives_missing_modules_and_old_backend():
    script = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(process.argv[1],'utf8');
const code=html.match(/<script id="shiju-startup-recorder">([\s\S]*?)<\/script>/)[1];
const listeners={},domListeners={},downloads=[];
function element(tag){return {tag,children:[],style:{},appendChild(child){this.children.push(child);},click(){},remove(){},focus(){},select(){}};}
const body=element('body');
function XHR(){this.status=404;this.responseText='Not Found';this.open=function(){};this.setRequestHeader=function(){};this.getResponseHeader=function(){return 'text/plain';};this.send=function(){this.onload();};}
const window={console:{info(){},error(){}},addEventListener(type,fn){listeners[type]=fn;}};
const context={window,console:window.console,document:{body,createElement:element,addEventListener(type,fn){domListeners[type]=fn;},querySelectorAll(){return [];}},location:{origin:'http://127.0.0.1:1',pathname:'/web/prosody-checker/',hash:'#chat'},navigator:{userAgent:'test-browser'},localStorage:{setItem(){}},Date,JSON,String,Blob,XMLHttpRequest:XHR,setTimeout(){return 1;},clearTimeout(){},URL:{createObjectURL(blob){downloads.push(blob);return 'blob:test';},revokeObjectURL(){}}};
vm.runInNewContext(code,context);
listeners.error({target:window,filename:'http://127.0.0.1:1/site.js',message:'module blocked',lineno:1,colno:1,error:{stack:'fixture stack'}});
domListeners.DOMContentLoaded();
assert(window.ShijuStartup.report().events.some(item=>item.message.includes('module blocked')));
const panel=body.children[0];assert(panel.open);
panel.children.find(item=>item.textContent==='导出诊断').onclick();
assert.equal(downloads.length,1);assert.equal(downloads[0].type,'application/json');
downloads[0].text().then(value=>{const report=JSON.parse(value);assert(report.events.some(item=>item.stack==='fixture stack'));console.log('Independent recorder and old-backend JSON fallback passed');});
'''
    result=subprocess.run(['node','-e',script,str(ROOT / 'web/prosody-checker/index.html')],capture_output=True,text=True)
    assert result.returncode == 0, result.stdout + result.stderr
