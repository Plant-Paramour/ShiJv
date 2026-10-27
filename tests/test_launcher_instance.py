import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('scenario, expected', [
    ('same', True), ('package', True), ('data', True),
    ('wrong-token', True), ('wrong-python', False), ('other-process', False),
    ('other-app', False), ('invalid-pid', False), ('missing-record', True),
])
def test_launcher_recognizes_services_without_trusting_other_processes(tmp_path, scenario, expected):
    script = r'''
$ErrorActionPreference = 'Stop'
. '__HELPERS__'
$root = 'D:\new-package'
$dataRoot = 'D:\current-data'
$health = [pscustomobject]@{ application='ShiJu'; workspace=$root; data_root=$dataRoot; pid=21116; python='C:\env\python.exe'; instance_id='token' }
$script:processPath = $health.python
$script:processCommand = 'C:\env\python.exe -I C:\old-package\apps\launcher\python_entry.py -m apps.launcher.serve --port 18763'
function Get-CimInstance { param($ClassName, $Filter, $ErrorAction) return [pscustomobject]@{ ExecutablePath=$script:processPath; CommandLine=$script:processCommand } }
$scenario = '__SCENARIO__'
if ($scenario -ne 'same') { $health.workspace = 'D:\old-package' }
if ($scenario -in @('data','wrong-token','missing-record')) {
    $health.data_root = '__TEMP__'
    if ($scenario -ne 'missing-record') {
        $token = if ($scenario -eq 'wrong-token') { 'another-instance' } else { 'token' }
        @{pid=21116; instance_id=$token; port=18763} | ConvertTo-Json | Set-Content (Join-Path $health.data_root 'instance.json')
    }
}
if ($scenario -eq 'wrong-python') { $script:processPath = 'C:\other\python.exe' }
if ($scenario -eq 'other-process') { $script:processCommand = 'python.exe -m http.server' }
if ($scenario -eq 'other-app') { $health.application = 'Other' }
if ($scenario -eq 'invalid-pid') { $health.pid = 'invalid' }
if ((Test-OurInstance $health) -ne [bool]::Parse('__EXPECTED__')) { throw "Unexpected identity result: $scenario" }
if (-not (Test-LauncherPathEqual 'D:\DATA\' 'd:\data')) { throw 'Path normalization failed' }
'''
    script = (script.replace('__HELPERS__', str(ROOT / 'scripts/launcher-instance.ps1'))
              .replace('__SCENARIO__', scenario).replace('__TEMP__', str(tmp_path))
              .replace('__EXPECTED__', str(expected)))
    result = subprocess.run(['powershell', '-NoProfile', '-Command', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
