param([switch]$AllowUnverified, [switch]$DirectStart, [switch]$CheckOnly, [switch]$Headless, [switch]$UiSmokeTest, [switch]$EmptyEnvironments, [string]$ModelPath = '', [string]$ApplicationPython = '', [string]$InferencePython = '', [switch]$EnableLocal, [string]$DataDir = '', [int]$Port = 18763)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $root 'scripts\data-directory.ps1')
if (-not $DataDir) {
    $DataDir = Get-ShiJuDataDirectory $root
}
$dataRoot = [IO.Path]::GetFullPath($DataDir)
$script:cancelled = $false
$script:activeProbe = $null
$script:form = $null
$script:selectedPoetryModel = $ModelPath
$script:allowUnverifiedLaunch = [bool]$AllowUnverified
$script:lastUnverifiedAttempt = ''
Set-Location -LiteralPath $root
. (Join-Path $root "scripts\launcher-instance.ps1")
. (Join-Path $root "scripts\launcher-memory.ps1")

function Get-PythonCandidates {
    if ($EmptyEnvironments) { return @() }
    $items = New-Object 'System.Collections.Generic.List[string]'
    $items.Add('C:\ProgramData\anaconda3\envs\ShiJu_new\python.exe')
    $saved = Join-Path $dataRoot 'launcher.json'
    if (Test-Path -LiteralPath $saved) {
        try { $c = Get-Content -LiteralPath $saved -Raw -Encoding UTF8 | ConvertFrom-Json; foreach ($p in @($c.application_python, $c.inference_python)) { if ($p) { $items.Add($p) } } } catch { }
    }
    $condaFile = Join-Path $env:USERPROFILE '.conda\environments.txt'
    if (Test-Path -LiteralPath $condaFile) {
        foreach ($directory in (Get-Content -LiteralPath $condaFile)) { if ($directory.Trim()) { $items.Add((Join-Path $directory.Trim() 'python.exe')) } }
    }
    foreach ($base in @('C:\ProgramData\anaconda3', (Join-Path $env:USERPROFILE 'anaconda3'), (Join-Path $env:USERPROFILE 'miniconda3'), (Join-Path $env:LOCALAPPDATA 'miniconda3'))) {
        $items.Add((Join-Path $base 'python.exe'))
        $envs = Join-Path $base 'envs'
        if (Test-Path -LiteralPath $envs) { Get-ChildItem -LiteralPath $envs -Directory | ForEach-Object { $items.Add((Join-Path $_.FullName 'python.exe')) } }
    }
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($command -and $command.Source -notlike '*WindowsApps*') { $items.Add($command.Source) }
    return @($items | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -Unique)
}

function Resolve-PythonPath([string]$Value) {
    $Value = $Value.Trim().Trim('"')
    if (-not $Value) { throw '请填写虚拟环境目录或 python.exe 的完整路径' }
    if ([IO.Path]::GetFileName($Value) -ine 'python.exe') {
        $candidate = Join-Path $Value 'python.exe'
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { throw "当前路径下不存在 python.exe：$Value" }
        return $candidate
    }
    if (-not (Test-Path -LiteralPath $Value -PathType Leaf)) { throw "当前路径下不存在 python.exe：$Value" }
    return $Value
}

function New-PythonProcessInfo([string]$Python, [string]$Arguments) {
    $Python = Resolve-PythonPath $Python
    if (-not $Python -or -not [IO.Path]::IsPathRooted($Python) -or [IO.Path]::GetFileName($Python) -ine 'python.exe' -or -not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw "所选环境的 Python 路径无效：$Python。请重新选择该环境中实际存在的 python.exe。"
    }
    if ($Python.Contains('"')) { throw '环境路径包含无效字符' }
    $info = New-Object Diagnostics.ProcessStartInfo
    if ($Arguments.StartsWith('-m ')) {
        $Arguments = '"{0}" {1}' -f (Join-Path $root 'apps\launcher\python_entry.py'), $Arguments
    }
    $info.FileName = $Python; $info.Arguments = $Arguments; $info.WorkingDirectory = $root
    $info.UseShellExecute = $false; $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true; $info.RedirectStandardError = $true
    $prefix = Split-Path -Parent $Python
    $info.EnvironmentVariables['PATH'] = "$prefix;$(Join-Path $prefix 'Library\bin');$(Join-Path $prefix 'Scripts');$env:PATH"
    $info.EnvironmentVariables.Remove('PYTHONHOME'); $info.EnvironmentVariables.Remove('PYTHONPATH')
    $info.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'
    return $info
}

function Invoke-EnvironmentProbe([string]$Python, [string]$Mode) {
    $tempRoot = Join-Path ([IO.Path]::GetTempPath()) ('shiju-launcher-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tempRoot | Out-Null
    $reportFile = Join-Path $tempRoot 'report.json'
    try {
        $probeScript = Join-Path $root 'apps\launcher\environment_probe.py'
        $info = New-PythonProcessInfo $Python ('"{0}" --mode {1} --output "{2}"' -f $probeScript, $Mode, $reportFile)
        $script:activeProbe = [Diagnostics.Process]::Start($info)
        $script:activeProbe.BeginOutputReadLine(); $script:activeProbe.BeginErrorReadLine()
        $watch = [Diagnostics.Stopwatch]::StartNew()
        while (-not $script:activeProbe.HasExited) {
            if ($script:form) { [Windows.Forms.Application]::DoEvents() }
            if ($script:cancelled -or $watch.Elapsed.TotalSeconds -gt 600) { $script:activeProbe.Kill(); throw '环境检查已取消或超时（10 分钟）' }
            Start-Sleep -Milliseconds 60
        }
        if (-not (Test-Path -LiteralPath $reportFile)) { throw "检查进程未生成报告，退出码 $($script:activeProbe.ExitCode)。请检查 Python 或 DLL 依赖。" }
        return (Get-Content -LiteralPath $reportFile -Raw -Encoding UTF8 | ConvertFrom-Json)
    } finally {
        $script:activeProbe = $null
        # Remove only the exact temporary file and empty directory created here.
        Remove-Item -LiteralPath $reportFile -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $tempRoot -ErrorAction SilentlyContinue
    }
}

function Test-SelectedEnvironments([string]$AppPython, [string]$ModelPython, [bool]$Local) {
    $core = Invoke-EnvironmentProbe $AppPython 'core'
    $inference = $null
    if ($Local) { $inference = Invoke-EnvironmentProbe $ModelPython 'inference' }
    if ($core.ok -and ((-not $Local) -or $inference.ok)) {
        $null = Invoke-LauncherMemory 'remember' $AppPython $ModelPython $Local
    }
    return [pscustomobject]@{ ok = ($core.ok -and ((-not $Local) -or $inference.ok)); core = $core; inference = $inference }
}

function Start-DetachedPython($Info) {
    # ShellExecute creates a detached GUI-launched service without inheriting
    # console/pipe handles from the launcher (including third-party launchers).
    $keys = @('PATH','PYTHONHOME','PYTHONPATH','PYTHONIOENCODING','SHIJU_ENV','SHIJU_LAUNCHER_WORKSPACE_ROOT','SHIJU_DATA_DIR','SHIJU_CONTROL_URL','SHIJU_POETRY_API_BASE_URL','DATABASE_URL','SHIJU_DATABASE_PATH')
    $previous = @{}
    try {
        foreach ($key in $keys) { $previous[$key] = [Environment]::GetEnvironmentVariable($key, 'Process'); [Environment]::SetEnvironmentVariable($key, $Info.EnvironmentVariables[$key], 'Process') }
        return Start-Process -FilePath $Info.FileName -ArgumentList $Info.Arguments -WorkingDirectory $Info.WorkingDirectory -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $dataRoot 'api-bootstrap.stdout.log') -RedirectStandardError (Join-Path $dataRoot 'api-bootstrap.stderr.log')
    } finally { foreach ($key in $keys) { [Environment]::SetEnvironmentVariable($key, $previous[$key], 'Process') } }
}
function Start-ShiJu([string]$AppPython, [string]$ModelPython, [bool]$Local) {
    $hash = [Security.Cryptography.SHA256]::Create()
    try { $key = [BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($dataRoot.ToLowerInvariant()))).Replace('-', '') } finally { $hash.Dispose() }
    $mutex = New-Object Threading.Mutex($false, ('Local\ShiJuLauncher-' + $key))
    $owned = $false
    try {
        try { $owned = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $owned = $true }
        if (-not $owned) { throw '另一个启动器正在启动此软件，请稍候再试。' }
        return Start-ShiJuCore $AppPython $ModelPython $Local
    } finally { if ($owned) { $mutex.ReleaseMutex() }; $mutex.Dispose() }
}
function Start-ShiJuCore([string]$AppPython, [string]$ModelPython, [bool]$Local) {
    New-Item -ItemType Directory -Path $dataRoot -Force | Out-Null
    $existing = Resolve-LauncherInstance $AppPython $ModelPython $Local
    if ($existing) {
        if ($script:selectedPoetryModel) {
            $current = Invoke-RestMethod -Uri "http://127.0.0.1:$script:Port/v1/local-runtime" -TimeoutSec 3
            if ($current.config.model_path -ne $script:selectedPoetryModel) { throw '已选择新模型；请点击“重新启动软件”应用，或在网页停止生成器后切换。' }
        }
        return $existing
    }
    if ($Local -and $script:selectedPoetryModel) {
        $runtimeFile = Join-Path $dataRoot 'runtime.json'
        $runtime = @{}
        if (Test-Path -LiteralPath $runtimeFile) { $old = Get-Content -LiteralPath $runtimeFile -Raw -Encoding UTF8 | ConvertFrom-Json; foreach ($property in $old.PSObject.Properties) { $runtime[$property.Name] = $property.Value } }
        $runtime['allow_unverified'] = $script:allowUnverifiedLaunch
        $runtime['model_path'] = $script:selectedPoetryModel; $runtime['auto_start'] = $true; $runtime['quantization'] = 'auto'
        if (-not $runtime.ContainsKey('idle_unload_seconds')) { $runtime['idle_unload_seconds'] = 300 }
        [IO.File]::WriteAllText($runtimeFile, ($runtime | ConvertTo-Json -Depth 8), (New-Object Text.UTF8Encoding($false)))
    }
    $healthUrl = "http://127.0.0.1:$script:Port/health/live"
    @{ application_python = $AppPython; inference_python = $ModelPython; local_enabled = $Local; port = $script:Port } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $dataRoot 'launcher.json') -Encoding UTF8
    $info = New-PythonProcessInfo $AppPython ("-m apps.launcher.serve --port $script:Port")
    $info.EnvironmentVariables['SHIJU_ENV'] = 'development'
    $info.EnvironmentVariables['SHIJU_LAUNCHER_WORKSPACE_ROOT'] = $root
    $info.EnvironmentVariables['SHIJU_DATA_DIR'] = $dataRoot
    $info.EnvironmentVariables['SHIJU_CONTROL_URL'] = "http://127.0.0.1:$script:Port"
    $info.EnvironmentVariables['SHIJU_POETRY_API_BASE_URL'] = "http://127.0.0.1:$script:Port"
    foreach ($key in @('DATABASE_URL', 'SHIJU_DATABASE_PATH')) { $info.EnvironmentVariables.Remove($key) }
    $service = Start-DetachedPython $info
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        if ($script:form) { [Windows.Forms.Application]::DoEvents() }
        if ($service.HasExited) {
            $failure = "服务已退出（$($service.ExitCode)）。日志：$dataRoot\api-startup.log"
            foreach ($name in @('api-bootstrap.stderr.log', 'api-bootstrap.stdout.log', 'api-startup.log')) {
                $logFile = Join-Path $dataRoot $name
                if (Test-Path -LiteralPath $logFile) {
                    $tail = (Get-Content -LiteralPath $logFile -Tail 40 -Encoding UTF8) -join "`r`n"
                    if ($tail) { $failure += "`r`n${name}：`r`n$tail" }
                }
            }
            throw $failure
        }
        try { $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 1; if ($health.application -eq 'ShiJu' -and $health.pid -eq $service.Id) { return $health } } catch { }
        Start-Sleep -Milliseconds 500
    }
    $service.Kill(); throw '软件启动超时，请检查 api-startup.log'
}

if ($CheckOnly -or $Headless) {
    if (-not $ApplicationPython) { $ApplicationPython = 'C:\ProgramData\anaconda3\envs\ShiJu_new\python.exe' }
    if (-not $InferencePython) { $InferencePython = $ApplicationPython }
    if ($DirectStart) {
        $report = Invoke-LauncherMemory 'select' $ApplicationPython $InferencePython ([bool]$EnableLocal) $ModelPath
        if (-not $report.ok) {
            if (-not $AllowUnverified) { throw $report.reason }
            $report = Invoke-LauncherMemory 'select-unverified' $ApplicationPython $InferencePython ([bool]$EnableLocal) $ModelPath
            if (-not $report.ok) { throw $report.reason }
        }
    } else { $report = Test-SelectedEnvironments $ApplicationPython $InferencePython ([bool]$EnableLocal) }
    $report | ConvertTo-Json -Depth 8
    if (-not $report.ok) { exit 1 }
    if ($Headless) { Start-ShiJu $ApplicationPython $InferencePython ([bool]$EnableLocal) | ConvertTo-Json -Depth 5 }
    exit 0
}

# This window opens before Python, Conda or any pip package exists.
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Windows.Forms.Application]::EnableVisualStyles()
$script:form = New-Object Windows.Forms.Form
$form.Text = '诗矩 · 启动器'; $form.ClientSize = New-Object Drawing.Size(760, 710)
$form.MinimumSize = New-Object Drawing.Size(776, 749); $form.StartPosition = 'CenterScreen'
$form.BackColor = [Drawing.Color]::FromArgb(247, 245, 239); $form.Font = New-Object Drawing.Font('Microsoft YaHei UI', 10)
function Add-Label([string]$Text, [int]$Y, [int]$Height) {
    $label = New-Object Windows.Forms.Label; $label.Text = $Text
    $label.Location = New-Object Drawing.Point(30, $Y); $label.Size = New-Object Drawing.Size(700, $Height)
    $form.Controls.Add($label); return $label
}
function Add-EnvironmentBox([int]$Y) {
    $box = New-Object Windows.Forms.ComboBox; $box.Location = New-Object Drawing.Point(30, $Y)
    $box.Size = New-Object Drawing.Size(600, 30); $box.DropDownStyle = 'DropDown'
    foreach ($candidate in (Get-PythonCandidates)) { [void]$box.Items.Add($candidate) }
    if ($box.Items.Count) { $box.SelectedIndex = 0 }; $form.Controls.Add($box)
    $button = New-Object Windows.Forms.Button; $button.Text = '浏览…'; $button.Tag = $box
    $button.Location = New-Object Drawing.Point(642, $Y); $button.Size = New-Object Drawing.Size(88, 30)
    $button.Add_Click({
        $dialog = New-Object Windows.Forms.OpenFileDialog; $dialog.Title = '选择虚拟环境中的 python.exe'; $dialog.Filter = 'Python 解释器 (python.exe)|python.exe'
        if ($dialog.ShowDialog() -eq 'OK') { $this.Tag.Text = $dialog.FileName }; $dialog.Dispose()
    }); $form.Controls.Add($button); return $box
}
$title = Add-Label '诗矩' 24 45; $title.Font = New-Object Drawing.Font('Microsoft YaHei UI', 24)
$null = Add-Label '选择环境，检查兼容性，再开始创作。启动器无需 Python 即可打开。' 76 30
$null = Add-Label '软件环境 · 用于界面、对话与作品管理' 124 28
$appBox = Add-EnvironmentBox 156
$localCheck = New-Object Windows.Forms.CheckBox; $localCheck.Text = '启用本地诗词生成（可选，需要兼容的 NVIDIA CUDA 环境）'
$localCheck.Location = New-Object Drawing.Point(30, 212); $localCheck.Size = New-Object Drawing.Size(700, 30); $form.Controls.Add($localCheck)
$null = Add-Label '推理环境 · 可与软件环境相同，也可单独选择' 252 28
$modelBox = Add-EnvironmentBox 284
$poetryButton = New-Object Windows.Forms.Button; $poetryButton.Text = '选择本地模型 / 分项测试（实际调用、输出、约束生成）'
$poetryButton.Location = New-Object Drawing.Point(30, 326); $poetryButton.Size = New-Object Drawing.Size(700, 32)
$form.Controls.Add($poetryButton)
. (Join-Path $root 'scripts\launcher-models.ps1')
$poetryButton.Add_Click({ try { $appBox.Text = Resolve-PythonPath $appBox.Text; $modelBox.Text = Resolve-PythonPath $modelBox.Text; Show-ModelSelection } catch { $statusBox.Text = $_.Exception.Message } })
$statusBox = New-Object Windows.Forms.TextBox; $statusBox.Multiline = $true; $statusBox.ReadOnly = $true; $statusBox.ScrollBars = 'Vertical'
$statusBox.Location = New-Object Drawing.Point(30, 368); $statusBox.Size = New-Object Drawing.Size(700, 112)
$statusBox.BackColor = [Drawing.Color]::FromArgb(255, 254, 250)
$statusBox.Text = "尚未检查。`r`n依赖不足时会列出缺失项；不启用本地生成，也可使用远程或本机模型服务对话。"; $form.Controls.Add($statusBox)
$pairLabel = Add-Label '已记住的环境—模型配对（选择后直接启动）' 484 24
$pairBox = New-Object Windows.Forms.ComboBox; $pairBox.DropDownStyle = 'DropDownList'; $pairBox.SetBounds(30, 512, 700, 30); $form.Controls.Add($pairBox)
$script:rememberedPairs = @()
$checkButton = New-Object Windows.Forms.Button; $checkButton.Text = '重新检测'; $checkButton.Location = New-Object Drawing.Point(30, 636); $checkButton.Size = New-Object Drawing.Size(130, 42)
$startButton = New-Object Windows.Forms.Button; $startButton.Text = '直接启动'; $startButton.Location = New-Object Drawing.Point(580, 636); $startButton.Size = New-Object Drawing.Size(150, 42)
$stopButton = New-Object Windows.Forms.Button; $stopButton.Text = '停止软件服务'; $stopButton.Location = New-Object Drawing.Point(180, 636); $stopButton.Size = New-Object Drawing.Size(150, 42)
$form.Controls.AddRange(@($checkButton, $startButton, $stopButton))
$helpButton = New-Object Windows.Forms.Button; $helpButton.Text = '环境准备说明'
$helpButton.Location = New-Object Drawing.Point(350, 636); $helpButton.Size = New-Object Drawing.Size(180, 42)
$helpButton.Add_Click({ Start-Process (Join-Path $root 'docs\single-player-launcher.html') }); $form.Controls.Add($helpButton)
$openButton = New-Object Windows.Forms.Button; $openButton.Text = '打开已有软件'; $openButton.Location = New-Object Drawing.Point(30, 568); $openButton.Size = New-Object Drawing.Size(174, 40)
$restartButton = New-Object Windows.Forms.Button; $restartButton.Text = '重新启动软件'; $restartButton.Location = New-Object Drawing.Point(220, 568); $restartButton.Size = New-Object Drawing.Size(174, 40)
$form.Controls.AddRange(@($openButton, $restartButton))
$startupDiagnosticsButton = New-Object Windows.Forms.Button
$startupDiagnosticsButton.Text = '导出启动诊断'
$startupDiagnosticsButton.Location = New-Object Drawing.Point(410, 568)
$startupDiagnosticsButton.Size = New-Object Drawing.Size(174, 40)
$startupDiagnosticsButton.Add_Click({
    try {
        & (Join-Path $root 'scripts/export-startup-diagnostics.ps1') -Workspace $root -DataDirectory $dataRoot -Port $script:Port
    } catch { $statusBox.Text = '诊断导出失败：' + $_.Exception.Message }
})
$form.Controls.Add($startupDiagnosticsButton)
$savedPath = Join-Path $dataRoot 'launcher.json'
if (Test-Path -LiteralPath $savedPath) {
    try { $c = Get-Content -LiteralPath $savedPath -Raw -Encoding UTF8 | ConvertFrom-Json; $appBox.Text = $c.application_python; $modelBox.Text = $c.inference_python; $localCheck.Checked = [bool]$c.local_enabled; if (-not $PSBoundParameters.ContainsKey("Port") -and $c.port) { if ([int]$c.port -eq 8000) { $script:Port = 18763 } else { $script:Port = [int]$c.port } } } catch { }
}
# With local generation disabled there is no independent inference choice yet.
# Follow the software environment until the user explicitly chooses a different one.
if (-not $localCheck.Checked) { $modelBox.Text = $appBox.Text }
$modelBox.Add_TextChanged({ $script:selectedPoetryModel = ''; $poetryButton.Text = '选择本地模型 / 分项测试（实际调用、输出、约束生成）' })
$script:lastAppPython = $appBox.Text
$appBox.Add_TextChanged({
    if (-not $localCheck.Checked -or $modelBox.Text -eq $script:lastAppPython) { $modelBox.Text = $appBox.Text }
    $script:lastAppPython = $appBox.Text
})
$addressLabel = New-Object Windows.Forms.Label
$addressLabel.Text = "服务地址：127.0.0.1:$script:Port"
$addressLabel.Location = New-Object Drawing.Point(414, 578); $addressLabel.Size = New-Object Drawing.Size(316, 28)
$form.Controls.Add($addressLabel)
function Set-EnvironmentControls([bool]$Enabled) {
    $appBox.Enabled = $Enabled; $localCheck.Enabled = $Enabled
    $pairBox.Enabled = $Enabled
    $poetryButton.Enabled = ($Enabled -and $localCheck.Checked)
    $modelBox.Enabled = ($Enabled -and $localCheck.Checked)
    foreach ($control in $form.Controls) {
        if ([object]::ReferenceEquals($control.Tag, $appBox)) { $control.Enabled = $Enabled }
        if ([object]::ReferenceEquals($control.Tag, $modelBox)) { $control.Enabled = ($Enabled -and $localCheck.Checked) }
    }
}
$localCheck.Add_CheckedChanged({ Set-EnvironmentControls $true })
Set-EnvironmentControls $true
$run = {
    param([bool]$Start, [bool]$Restart = $false)
    $checkButton.Enabled = $false; $startButton.Enabled = $false; $stopButton.Enabled = $false; $openButton.Enabled = $false; $restartButton.Enabled = $false
    $selectedApp = $appBox.Text.Trim(); $selectedModel = $modelBox.Text.Trim(); $selectedLocal = $localCheck.Checked
    Set-EnvironmentControls $false
    $statusBox.Text = '正在读取已验证配对…'
    try {
        $selectedApp = Resolve-PythonPath $selectedApp
        if ($selectedLocal) { $selectedModel = Resolve-PythonPath $selectedModel }
        $appBox.Text = $selectedApp; $modelBox.Text = $selectedModel
        $lines = @()
        if ($Start) {
            $report = Invoke-LauncherMemory 'select' $selectedApp $selectedModel $selectedLocal $script:selectedPoetryModel
            $script:allowUnverifiedLaunch = $false
            if (-not $report.ok) {
                $attemptKey = "$selectedApp|$selectedModel|$selectedLocal|$script:selectedPoetryModel"
                if ($script:lastUnverifiedAttempt -ne $attemptKey) {
                    $script:lastUnverifiedAttempt = $attemptKey
                    throw ($report.reason + "`r`n再次点击「直接启动」可选择忽略验收并尝试启动。")
                }
                $answer = [Windows.Forms.MessageBox]::Show($form, ($report.reason + "`r`n`r`n是否忽略此错误并直接启动？`r`n只会尝试加载，不会标记为已验证；网页实际生成成功一次后才记入已验证配对。"), '配对尚未验证', [Windows.Forms.MessageBoxButtons]::YesNo, [Windows.Forms.MessageBoxIcon]::Warning, [Windows.Forms.MessageBoxDefaultButton]::Button2)
                if ($answer -ne [Windows.Forms.DialogResult]::Yes) { return }
                $override = Invoke-LauncherMemory 'select-unverified' $selectedApp $selectedModel $selectedLocal $script:selectedPoetryModel
                if (-not $override.ok) { throw $override.reason }
                $script:allowUnverifiedLaunch = $true
                $lines += '已选择忽略验收；配对仍为未验证，等待网页实际生成成功。'
            } else {
                $script:lastUnverifiedAttempt = ''
                $lines += '已读取有效检测记录，无需重复检测。'
            }
            if ($Restart -and (Test-OurInstance (Get-LauncherHealth $script:Port))) { Stop-LauncherInstance $script:Port }
            $health = Start-ShiJu $selectedApp $selectedModel $selectedLocal
            $addressLabel.Text = "服务地址：127.0.0.1:$script:Port"
            $lines += "软件已启动：http://127.0.0.1:$script:Port；实际环境：$($health.python)。"
            Start-Process "http://127.0.0.1:$script:Port/web/prosody-checker/#settings"
        } else {
            $statusBox.Text = '正在重新检测环境，请稍候…'
            $report = Test-SelectedEnvironments $selectedApp $selectedModel $selectedLocal
            if (-not $report.ok) { throw (($report.core.errors + $report.inference.errors) -join "`r`n") }
            Update-RememberedPairs
            $lines += '环境检测通过并已记住。已通过测试的模型配对可直接启动；新模型请先完整测试。'
        }
        $statusBox.Text = $lines -join "`r`n"
    } catch { if (-not $form.IsDisposed) { $statusBox.Text = $_.Exception.Message } }
    finally { if (-not $form.IsDisposed) { $checkButton.Enabled = $true; $startButton.Enabled = $true; $stopButton.Enabled = $true; $openButton.Enabled = $true; $restartButton.Enabled = $true; Set-EnvironmentControls $true } }
}
$checkButton.Add_Click({ & $run $false }); $startButton.Add_Click({ & $run $true })
$openButton.Add_Click({
    $health = Get-LauncherHealth $script:Port
    if (Test-OurInstance $health) { Start-Process "http://127.0.0.1:$script:Port/web/prosody-checker/#settings"; $statusBox.Text = "已打开正在运行的诗矩：http://127.0.0.1:$script:Port" }
    else { $statusBox.Text = '当前没有可识别的诗矩服务，请点击“直接启动”。' }
})
$restartButton.Add_Click({ & $run $true $true })
$stopButton.Add_Click({
    foreach ($button in @($checkButton,$startButton,$stopButton,$openButton,$restartButton)) { $button.Enabled = $false }
    Set-EnvironmentControls $false
    $statusBox.Text = '正在退出软件并释放端口，请稍候…'
    [Windows.Forms.Application]::DoEvents()
    try { Stop-LauncherInstance $script:Port; if (-not $form.IsDisposed) { $statusBox.Text = '软件已退出，端口已释放。可以重新选择环境并启动。' } }
    catch { if (-not $form.IsDisposed) { $statusBox.Text = $_.Exception.Message } }
    finally { if (-not $form.IsDisposed) { foreach ($button in @($checkButton,$startButton,$stopButton,$openButton,$restartButton)) { $button.Enabled = $true }; Set-EnvironmentControls $true } }
})
$pairBox.Add_SelectedIndexChanged({
    $index = $pairBox.SelectedIndex
    if ($index -ge 0 -and $index -lt $script:rememberedPairs.Count) {
        $pair = $script:rememberedPairs[$index]
        $appBox.Text = $pair.application_python
        $localCheck.Checked = [bool]$pair.local_enabled
        $modelBox.Text = $pair.inference_python
        $script:selectedPoetryModel = $pair.model_path
        $poetryButton.Text = if ($pair.model_path) { $(if ($pair.valid) { '已验证模型：' } else { '未验证模型：' }) + (Split-Path $pair.model_path -Leaf) + '（点击可测试或更换）' } else { '选择本地模型 / 分项测试' }
        $statusBox.Text = if ($pair.valid) { '已选择已验证配对，可以直接启动。需要主动复查时点击“重新检测”。' } else { '此配对已保存，但文件或环境已变化，请重新检测。' }
    }
})
if (-not $EmptyEnvironments) { Update-RememberedPairs }
$form.Add_FormClosing({ $script:cancelled = $true; if ($script:activeProbe -and -not $script:activeProbe.HasExited) { $script:activeProbe.Kill() } })
if ($UiSmokeTest) {
    [pscustomobject]@{ window = $form.Text; controls = $form.Controls.Count; modelDialogControls = (Show-ModelSelection -Smoke); rememberedPairs = $script:rememberedPairs.Count; selectedModel = $script:selectedPoetryModel; softwareCandidates = $appBox.Items.Count; inferenceCandidates = $modelBox.Items.Count; pythonRequiredToOpen = $false } | ConvertTo-Json
    $form.Dispose(); exit 0
}
[void]$form.ShowDialog(); $form.Dispose()
