function Invoke-LauncherMemory([string]$Action, [string]$AppPython, [string]$ModelPython, [bool]$Local, [string]$Model = '') {
    $reportFile = Join-Path ([IO.Path]::GetTempPath()) ('shiju-memory-' + [Guid]::NewGuid().ToString('N') + '.json')
    try {
        foreach ($value in @($AppPython,$ModelPython,$Model,$dataRoot)) { if ($value.Contains('"')) { throw '路径包含无效字符' } }
        $arguments = '-m apps.api.launcher_memory --action {0} --data-dir "{1}" --application-python "{2}" --inference-python "{3}" --output "{4}"' -f $Action,$dataRoot,$AppPython,$ModelPython,$reportFile
        if ($Local) { $arguments += ' --local' }
        if ($Model) { $arguments += (' --model "{0}"' -f $Model) }
        $info = New-PythonProcessInfo $AppPython $arguments
        $process = [Diagnostics.Process]::Start($info)
        $process.BeginOutputReadLine(); $process.BeginErrorReadLine()
        $watch = [Diagnostics.Stopwatch]::StartNew()
        while (-not $process.HasExited) {
            if ($script:form) { [Windows.Forms.Application]::DoEvents() }
            if ($watch.Elapsed.TotalSeconds -gt 20 -or $script:cancelled) { $process.Kill(); throw '读取启动记忆超时或已取消' }
            Start-Sleep -Milliseconds 40
        }
        if (-not (Test-Path -LiteralPath $reportFile)) { throw '无法读取启动记忆，请检查软件环境，或重新检测' }
        return Get-Content -LiteralPath $reportFile -Raw -Encoding UTF8 | ConvertFrom-Json
    } finally { Remove-Item -LiteralPath $reportFile -Force -ErrorAction SilentlyContinue }
}

function Update-RememberedPairs {
    $wantedApp = $appBox.Text; $wantedInfer = $modelBox.Text; $wantedModel = $script:selectedPoetryModel
    $pairBox.Items.Clear(); $script:rememberedPairs = @()
    if (-not $appBox.Text -or -not (Test-Path -LiteralPath $appBox.Text)) { return }
    try {
        $memory = Invoke-LauncherMemory 'list' $appBox.Text $modelBox.Text $localCheck.Checked
        $script:rememberedPairs = @($memory.pairs)
        foreach ($pair in $script:rememberedPairs) {
            $name = if ($pair.model_path) { Split-Path $pair.model_path -Leaf } else { '仅对话服务' }
            $envName = Split-Path (Split-Path $pair.inference_python -Parent) -Leaf
            $appName = Split-Path (Split-Path $pair.application_python -Parent) -Leaf
            $status = if ($pair.valid) { '已验证' } else { '需要重新检测' }
            [void]$pairBox.Items.Add("$envName → $name · 软件：$appName · $status")
        }
        if ($pairBox.Items.Count) {
            $index = 0
            for ($i=0; $i -lt $script:rememberedPairs.Count; $i++) {
                $pair = $script:rememberedPairs[$i]
                if ($memory.last_pair -and $pair.application_python -eq $memory.last_pair.application_python -and $pair.inference_python -eq $memory.last_pair.inference_python -and $pair.model_path -eq $memory.last_pair.model_path) { $index = $i; break }
            }
            for ($i=0; $i -lt $script:rememberedPairs.Count; $i++) {
                $pair = $script:rememberedPairs[$i]
                if ($wantedModel -and $pair.application_python -eq $wantedApp -and $pair.inference_python -eq $wantedInfer -and $pair.model_path -eq $wantedModel) { $index = $i; break }
            }
            $pairBox.SelectedIndex = $index
        }
    } catch { $statusBox.Text = $_.Exception.Message }
}
