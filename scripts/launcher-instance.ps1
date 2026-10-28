# Port and process ownership helpers. Never terminate an unidentified listener.
function Get-LauncherBuildFingerprint([string]$Workspace) {
    $manifest = Join-Path $Workspace 'RELEASE-MANIFEST.json'
    if (Test-Path -LiteralPath $manifest) {
        $value = Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 | ConvertFrom-Json
        [string[]]$names = @($value.files.PSObject.Properties.Name)
    } else {
        $all = @()
        foreach ($folder in @('apps','shiju','Rhyme','Songci_Meter','web','scripts','tools','deploy')) {
            $base = Join-Path $Workspace $folder
            if (Test-Path -LiteralPath $base) {
                $all += @(Get-ChildItem -LiteralPath $base -Recurse -File | Where-Object { $_.Extension -cin @('.py','.ps1','.cs','.js','.mjs','.cjs','.html','.css','.json','.sql','.toml','.csv') -and $_.FullName -notmatch '[\\/](__pycache__|node_modules|\.git)[\\/]' } | ForEach-Object { $_.FullName.Substring($Workspace.TrimEnd('\','/').Length + 1).Replace('\','/') })
            }
        }
        foreach ($name in @('launcher.ps1','start-single-player.ps1','pyproject.toml')) { if (Test-Path -LiteralPath (Join-Path $Workspace $name)) { $all += $name } }
        [string[]]$names = @($all)
    }
    [Array]::Sort($names, [StringComparer]::Ordinal)
    $hashes = foreach ($name in $names) {
        if ([IO.Path]::IsPathRooted($name) -or $name.Split('/') -contains '..') { throw '发布清单包含非法路径' }
        $path = Join-Path $Workspace $name
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            $fileSha = [Security.Cryptography.SHA256]::Create()
            try { $name + ':' + ([BitConverter]::ToString($fileSha.ComputeHash([IO.File]::ReadAllBytes($path)))).Replace('-','').ToLowerInvariant() } finally { $fileSha.Dispose() }
        } else { $name + ':missing' }
    }
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes(($hashes -join '|'))))).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() }
}
function Get-LauncherHealth([int]$ServicePort) {
    try { return Invoke-RestMethod "http://127.0.0.1:$ServicePort/health/live" -TimeoutSec 2 } catch { return $null }
}
function Test-LauncherPathEqual([string]$Left, [string]$Right) {
    if (-not $Left -or -not $Right) { return $false }
    try {
        return [IO.Path]::GetFullPath($Left).TrimEnd('\', '/') -ieq [IO.Path]::GetFullPath($Right).TrimEnd('\', '/')
    } catch { return $false }
}
function Test-OurInstance($Health) {
    if ($null -eq $Health -or $Health.application -ne 'ShiJu') { return $false }
    $sameData = Test-LauncherPathEqual $Health.data_root $dataRoot
    if ($sameData -and (Test-LauncherPathEqual $Health.workspace $root)) { return $true }
    # Updated/extracted packages share the same data directory. Verify the running
    # service rather than requiring its previous package location to match ours.
    try {
        $servicePid = 0
        if (-not [int]::TryParse([string]$Health.pid, [ref]$servicePid) -or $servicePid -le 0) { return $false }
        if (-not $Health.data_root -or -not $Health.workspace) { return $false }
        # instance.json may have been overwritten by a later failed startup.
        # The live endpoint and OS process identity are authoritative here.
        $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$servicePid" -ErrorAction Stop
        return ($null -ne $processInfo -and (Test-LauncherPathEqual $processInfo.ExecutablePath $Health.python) -and $processInfo.CommandLine -match '(?i)(?:^|\s)-m\s+apps\.(?:launcher\.serve|api\.main)(?:\s|$)')
    } catch { return $false }
}
function Test-PortAvailable([int]$ServicePort) {
    $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, $ServicePort)
    try { $listener.Server.ExclusiveAddressUse = $true; $listener.Start(); return $true } catch { return $false } finally { $listener.Stop() }
}
function Find-AvailableLauncherPort([int]$StartPort) {
    if ($StartPort -gt 65535) { $StartPort = 18763 }
    foreach ($candidate in $StartPort..([Math]::Min(65535, $StartPort + 100))) { if (Test-PortAvailable $candidate) { return $candidate } }
    throw '未找到可用端口，请关闭不需要的本地服务后重试。'
}
function Get-PortOwnerDescription([int]$ServicePort) {
    try {
        $listeners = @(Get-NetTCPConnection -LocalPort $ServicePort -State Listen -ErrorAction Stop)
        $ownerId = $listeners[0].OwningProcess
        $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$ownerId"
        return "端口 $ServicePort · $($processInfo.Name) · PID $ownerId`r`n$($processInfo.ExecutablePath)"
    } catch { return "端口 $ServicePort 已被占用，无法读取进程详情。" }
}
function Show-LauncherInstanceChoice([bool]$Owned, [string]$Description) {
    $dialog = New-Object Windows.Forms.Form
    $dialog.Text = '诗矩 · 已有服务正在运行'; $dialog.ClientSize = New-Object Drawing.Size(620, 245)
    $dialog.StartPosition = 'CenterParent'; $dialog.FormBorderStyle = 'FixedDialog'; $dialog.MaximizeBox = $false; $dialog.MinimizeBox = $false
    $dialog.Font = New-Object Drawing.Font('Microsoft YaHei UI', 10)
    $label = New-Object Windows.Forms.Label; $label.Location = New-Object Drawing.Point(24, 22); $label.Size = New-Object Drawing.Size(572, 140)
    $label.Text = $Description; $dialog.Controls.Add($label)
    $dialog.Tag = 'cancel'
    $options = if ($Owned) { @(@('打开已有软件','open'), @('停止并重新启动','restart'), @('取消','cancel')) } else { @(@('使用空闲端口','alternate'), @('取消','cancel')) }
    $x = 24
    foreach ($option in $options) {
        $button = New-Object Windows.Forms.Button; $button.Text = $option[0]; $button.Tag = $option[1]
        $button.Location = New-Object Drawing.Point($x, 182); $button.Size = New-Object Drawing.Size(174, 40)
        $button.Add_Click({ $this.FindForm().Tag = $this.Tag; $this.FindForm().Close() })
        $dialog.Controls.Add($button); $x += 188
    }
    [void]$dialog.ShowDialog($form); $choice = $dialog.Tag; $dialog.Dispose(); return $choice
}
function Stop-LauncherInstance([int]$ServicePort) {
    $health = Get-LauncherHealth $ServicePort
    if (-not (Test-OurInstance $health)) {
        $instanceFile = Join-Path $dataRoot 'instance.json'
        if (Test-Path -LiteralPath $instanceFile) {
            $saved = Get-Content -LiteralPath $instanceFile -Raw -Encoding UTF8 | ConvertFrom-Json
            $candidate = Get-LauncherHealth ([int]$saved.port)
            if ((Test-OurInstance $candidate) -and $candidate.pid -eq $saved.pid -and $candidate.instance_id -eq $saved.instance_id) {
                $ServicePort = [int]$saved.port; $script:Port = $ServicePort; $health = $candidate
            }
        }
    }
    if (-not (Test-OurInstance $health)) { throw '此端口未识别为当前诗矩的软件服务，未关闭任何其他程序。' }
    $before = Get-CimInstance Win32_Process -Filter "ProcessId=$($health.pid)"
    if (-not $before -or $before.ExecutablePath -ine $health.python -or $before.CommandLine -notmatch 'apps\.(launcher\.serve|api\.main)') { throw '无法核对服务进程身份，未停止任何进程。' }
    $runtime = Invoke-RestMethod "http://127.0.0.1:$ServicePort/v1/local-runtime" -TimeoutSec 3
    if ($runtime.busy) { throw '软件仍有任务正在运行。请先完成或取消任务，再重新启动。' }
    $graceful = $false
    try {
        $headers = @{}
        $null = Invoke-RestMethod "http://127.0.0.1:$ServicePort/v1/local-runtime/shutdown" -Method Post -Headers $headers -TimeoutSec 15
        $graceful = $true
    } catch {
        $code = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 0 }
        if ($code -notin @(404,405)) { throw }
    }
    if (-not $graceful) {
        if ($runtime.worker.state -in @('ready','loading')) { $null = Invoke-RestMethod "http://127.0.0.1:$ServicePort/v1/local-runtime/stop" -Method Post -TimeoutSec 15 }
        $after = Get-CimInstance Win32_Process -Filter "ProcessId=$($health.pid)"
        if (-not $after -or $after.CreationDate -ne $before.CreationDate -or $after.ExecutablePath -ine $before.ExecutablePath) { throw '进程身份已改变，未停止服务。' }
        Stop-Process -Id $health.pid -ErrorAction Stop
    }
    for ($attempt = 0; $attempt -lt 300; $attempt++) {
        if (Test-PortAvailable $ServicePort) { return }
        if ($script:form) { [Windows.Forms.Application]::DoEvents() }
        Start-Sleep -Milliseconds 100
    }
    # An open streaming connection can delay the HTTP server's graceful exit.
    # Only terminate the same process whose identity was checked before shutdown.
    $after = Get-CimInstance Win32_Process -Filter "ProcessId=$($health.pid)"
    if ($after -and $after.CreationDate -eq $before.CreationDate -and $after.ExecutablePath -ieq $before.ExecutablePath -and $after.CommandLine -eq $before.CommandLine) {
        Stop-Process -Id $health.pid -ErrorAction Stop
        for ($attempt = 0; $attempt -lt 50; $attempt++) {
            if (Test-PortAvailable $ServicePort) { return }
            Start-Sleep -Milliseconds 100
        }
    }
    throw '服务尚未释放端口，请查看启动日志中的退出信息。'
}
function Resolve-LauncherInstance([string]$AppPython, [string]$ModelPython, [bool]$Local) {
    # Find an owned service on its last saved port before considering a second instance.
    $instanceFile = Join-Path $dataRoot 'instance.json'
    if (Test-Path -LiteralPath $instanceFile) {
        try {
            $savedInstance = Get-Content -LiteralPath $instanceFile -Raw -Encoding UTF8 | ConvertFrom-Json
            $savedHealth = Get-LauncherHealth ([int]$savedInstance.port)
            if ((Test-OurInstance $savedHealth) -and $savedHealth.pid -eq $savedInstance.pid -and $savedHealth.instance_id -eq $savedInstance.instance_id) { $script:Port = [int]$savedInstance.port }
        } catch { }
    }
    $health = Get-LauncherHealth $script:Port
    if (Test-OurInstance $health) {
        $runtime = Invoke-RestMethod "http://127.0.0.1:$script:Port/v1/local-runtime" -TimeoutSec 3
        $sameBuild = ($health.build -and $health.build.fingerprint -eq (Get-LauncherBuildFingerprint $root))
        $sameEnvironment = ($sameBuild -and (Test-LauncherPathEqual $health.data_root $dataRoot) -and $health.python -ieq $AppPython -and $runtime.environment.local_enabled -eq $Local -and ((-not $Local) -or $runtime.environment.inference_python -ieq $ModelPython))
        if ($sameEnvironment) { return $health }
        if ($Headless) { throw '已有诗矩实例的应用版本、环境或数据目录不同。请在启动器中选择“停止并重新启动”。' }
        $versionNote = if ($sameBuild) { '应用版本一致；运行配置不同' } else { '旧版或不同版本服务：重新启动后才会应用本包修复' }
        $choice = Show-LauncherInstanceChoice $true "$versionNote`r`n$($health.python)`r`n数据：$($health.data_root)`r`n地址：http://127.0.0.1:$script:Port`r`n打开已有软件，或等待任务完成后停止并使用当前配置重新启动。"
        if ($choice -eq 'open') { return $health }
        if ($choice -ne 'restart') { throw '已取消，原服务继续运行。' }
        Stop-LauncherInstance $script:Port
        return $null
    }
    if (-not (Test-PortAvailable $script:Port)) {
        $description = Get-PortOwnerDescription $script:Port
        if (-not $Headless) {
            $choice = Show-LauncherInstanceChoice $false "$description`r`n这不是可确认归属的诗矩实例。可以使用空闲端口启动，无需结束其他程序。"
            if ($choice -ne 'alternate') { throw '已取消，未关闭任何其他程序。' }
        }
        $script:Port = Find-AvailableLauncherPort ($script:Port + 1)
    }
    return $null
}
