# Port and process ownership helpers. Never terminate an unidentified listener.
function Get-LauncherHealth([int]$ServicePort) {
    try { return Invoke-RestMethod "http://127.0.0.1:$ServicePort/health/live" -TimeoutSec 2 } catch { return $null }
}
function Test-OurInstance($Health) {
    return ($null -ne $Health -and $Health.application -eq 'ShiJu' -and $Health.workspace -ieq $root -and $Health.data_root -ieq $dataRoot)
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
    for ($attempt = 0; $attempt -lt 100; $attempt++) {
        if (Test-PortAvailable $ServicePort) { return }
        if ($script:form) { [Windows.Forms.Application]::DoEvents() }
        Start-Sleep -Milliseconds 100
    }
    throw '已发出退出请求，服务仍在关闭。请稍后再试；没有强制结束其他进程。'
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
        $sameEnvironment = ($health.python -ieq $AppPython -and $runtime.environment.local_enabled -eq $Local -and ((-not $Local) -or $runtime.environment.inference_python -ieq $ModelPython))
        if ($sameEnvironment) { return $health }
        if ($Headless) { throw '已有诗矩实例使用其他环境。请在启动器中选择“停止并重新启动”。' }
        $choice = Show-LauncherInstanceChoice $true "诗矩已在运行，使用的环境与当前选择不同。`r`n$($health.python)`r`n地址：http://127.0.0.1:$script:Port`r`n打开已有软件，或等待任务完成后停止并使用当前环境重新启动。"
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
