function Show-ModelSelection {
    param([switch]$Smoke)
    $dialog = New-Object Windows.Forms.Form
    $dialog.Text = '本地模型 · 选择与验收'; $dialog.ClientSize = New-Object Drawing.Size(880, 650)
    $dialog.StartPosition = 'CenterParent'; $dialog.Font = $form.Font; $dialog.BackColor = $form.BackColor
    $caption = New-Object Windows.Forms.Label
    $caption.Text = "推理环境：$($modelBox.Text)`r`n扫描只发现候选；请执行完整测试。测试会真实加载模型、占用显存，结束后释放。"
    $caption.SetBounds(24, 20, 830, 60); $dialog.Controls.Add($caption)
    $choice = New-Object Windows.Forms.ComboBox; $choice.DropDownStyle = 'DropDown'
    $choice.SetBounds(24, 88, 700, 30); $dialog.Controls.Add($choice)
    $runtimeFile = Join-Path $dataRoot 'runtime.json'
    if ($script:selectedPoetryModel) { $choice.Text = $script:selectedPoetryModel }
    elseif (Test-Path -LiteralPath $runtimeFile) { try { $choice.Text = (Get-Content $runtimeFile -Raw -Encoding UTF8 | ConvertFrom-Json).model_path } catch {} }
    $browse = New-Object Windows.Forms.Button; $browse.Text = '选择目录'; $browse.SetBounds(736, 86, 120, 32); $dialog.Controls.Add($browse)
    $scan = New-Object Windows.Forms.Button; $scan.Text = '扫描本地模型'; $scan.SetBounds(24, 136, 160, 36); $dialog.Controls.Add($scan)
    $test = New-Object Windows.Forms.Button; $test.Text = '完整测试此模型'; $test.SetBounds(200, 136, 180, 36); $dialog.Controls.Add($test)
    $use = New-Object Windows.Forms.Button; $use.Text = '使用已通过测试的模型'; $use.SetBounds(590, 586, 266, 42); $dialog.Controls.Add($use); $use.Enabled = $false
    $details = New-Object Windows.Forms.TextBox; $details.Multiline = $true; $details.ReadOnly = $true; $details.ScrollBars = 'Vertical'
    $details.SetBounds(24, 192, 832, 370); $details.BackColor = [Drawing.Color]::FromArgb(255,254,250)
    $details.Text = "测试项目：`r`n1. 所选环境的依赖、CUDA、模型架构和运行方式`r`n2. 权重及分词器加载`r`n3. 实际模型调用与非空有效输出（展示原文）`r`n4. 约束框架真实生成五言绝句，校验协议、句数、字数和格律`r`n`r`n修改模型路径或切换环境后，需要重新测试。扫描默认检查项目及 Hugging Face / ModelScope 缓存。`r`n其他目录可直接浏览选择。"
    $untested = New-Object Windows.Forms.Button; $untested.Text = '选择此模型，暂不验收'; $untested.SetBounds(398, 136, 230, 36); $dialog.Controls.Add($untested)
    $dialog.Controls.Add($details)
    $state = @{ process = $null; cancelled = $false; testedPath = ''; chosen = $false; untested = $false; python = $modelBox.Text.Trim() }
    $browse.Add_Click({ $folder = New-Object Windows.Forms.FolderBrowserDialog; $folder.Description = '选择包含 config.json 的完整模型目录'; if ($folder.ShowDialog($dialog) -eq 'OK') { $choice.Text = $folder.SelectedPath }; $folder.Dispose() })
    $choice.Add_TextChanged({ $use.Enabled = ($state.testedPath -eq $choice.Text.Trim() -and $state.testedPath -ne '') })
    $invoke = {
        param([bool]$Scanning)
        $tempRoot = Join-Path ([IO.Path]::GetTempPath()) ('shiju-model-' + [Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $tempRoot | Out-Null
        $reportFile = Join-Path $tempRoot 'report.json'
        $selected = $choice.Text.Trim()
        $choice.Enabled = $false; $browse.Enabled = $false; $scan.Enabled = $false; $test.Enabled = $false; $untested.Enabled = $false; $use.Enabled = $false
        try {
            if (-not $Scanning) {
                $health = Get-LauncherHealth $script:Port
                if (Test-OurInstance $health) {
                    $live = Invoke-RestMethod -Uri "http://127.0.0.1:$script:Port/v1/local-runtime" -TimeoutSec 3
                    if ($live.worker.state -in @('ready','loading')) { throw '当前生成器正在占用模型。请先在网页停止生成器，再执行独立验收，以免重复加载耗尽显存。' }
                }
            }
            if (-not $Scanning -and (-not [IO.Path]::IsPathRooted($selected) -or $selected.Contains('"'))) { throw '请先选择模型绝对路径' }
            $arguments = '-m apps.launcher.model_probe --output "{0}" --data-dir "{1}"' -f $reportFile, $dataRoot
            if ($Scanning) { $arguments += ' --scan' } else { $arguments += (' --model "{0}"' -f $selected) }
            $info = New-PythonProcessInfo $state.python $arguments
            $state.process = [Diagnostics.Process]::Start($info)
            $state.process.BeginOutputReadLine(); $state.process.BeginErrorReadLine()
            $watch = [Diagnostics.Stopwatch]::StartNew(); $details.Text = '正在检查，请稍候。关闭此窗口可取消测试。'
            while (-not $state.process.HasExited) {
                [Windows.Forms.Application]::DoEvents()
                if ($state.cancelled -or $watch.Elapsed.TotalSeconds -gt 900) { $state.process.Kill(); throw '测试取消或超时（15 分钟）；未验收通过' }
                if (Test-Path -LiteralPath $reportFile) {
                    try { $progress = Get-Content $reportFile -Raw -Encoding UTF8 | ConvertFrom-Json; $details.Text = (($progress.checks | ForEach-Object { "$($_.name)：$($_.state)`r`n$($_.detail)`r`n" }) -join "`r`n") } catch {}
                }
                Start-Sleep -Milliseconds 100
            }
            if (-not (Test-Path -LiteralPath $reportFile)) { throw '检查进程未返回报告，请确认环境依赖可导入' }
            $result = Get-Content $reportFile -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($Scanning) {
                $choice.Items.Clear(); $lines = @()
                foreach ($item in $result.models) { if ($item.supported) { [void]$choice.Items.Add($item.path) }; $lines += "$($item.path)`r`n$($item.reason)`r`n" }
                $details.Text = "扫描结果（候选仍需完整测试）：`r`n" + ($lines -join "`r`n")
                if ($choice.Items.Count) { $choice.SelectedIndex = 0 }
            } else {
                $details.Text = (($result.checks | ForEach-Object { $label = switch ($_.state) { 'passed' {'通过'} 'failed' {'失败'} default {'未完成'} }; "$($_.name)：$label`r`n$($_.detail)`r`n" }) -join "`r`n")
                if ($result.ok) { $state.testedPath = $selected; $use.Enabled = $true; $details.AppendText("`r`n全部通过。测试权重已释放，可应用此模型。") }
                else { $details.AppendText("`r`n未通过：$($result.error)`r`n请更换推理环境或模型后重试。") }
            }
        } catch { if (-not $dialog.IsDisposed) { $details.Text = $_.Exception.Message } }
        finally {
            $state.process = $null
            if (-not $dialog.IsDisposed) { $choice.Enabled = $true; $browse.Enabled = $true; $scan.Enabled = $true; $test.Enabled = $true; $untested.Enabled = $true }
            Remove-Item -LiteralPath $reportFile -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath $tempRoot -ErrorAction SilentlyContinue
        }
    }
    $scan.Add_Click({ & $invoke $true }); $test.Add_Click({ & $invoke $false })
    $untested.Add_Click({
        if (-not [IO.Path]::IsPathRooted($choice.Text.Trim())) { $details.Text = '请先选择模型绝对路径'; return }
        $state.testedPath = $choice.Text.Trim(); $state.chosen = $true; $state.untested = $true; $dialog.Close()
    })
    $use.Add_Click({ $state.chosen = $true; $dialog.Close() })
    $dialog.Add_FormClosing({ $state.cancelled = $true; if ($state.process -and -not $state.process.HasExited) { $state.process.Kill() } })
    if ($Smoke -and $env:SHIJU_LAUNCHER_MODEL_SMOKE_SCAN) {
        & $invoke $true
        if ($choice.Items.Count -lt 1) { throw ("模型窗口扫描自测失败：" + $details.Text) }
    }
    if ($Smoke) { $count = $dialog.Controls.Count; $dialog.Dispose(); return $count }
    [void]$dialog.ShowDialog($form)
    if ($state.chosen) {
        $script:selectedPoetryModel = $state.testedPath
        if ($state.untested) {
            $poetryButton.Text = '未验证模型：' + (Split-Path $state.testedPath -Leaf)
            $statusBox.Text = '已选择未验证模型。点击直接启动时会先提示，再次点击可确认忽略验收。'
            $dialog.Dispose(); return
        }
        $remembered = Invoke-LauncherMemory 'check' $appBox.Text $modelBox.Text $localCheck.Checked $state.testedPath
        if (-not $remembered.ok) {
            $environmentCheck = Test-SelectedEnvironments $appBox.Text $modelBox.Text $localCheck.Checked
            if (-not $environmentCheck.ok) { $statusBox.Text = '软件环境未通过检查，请重新选择软件环境。'; $dialog.Dispose(); return }
        }
        Update-RememberedPairs
        $script:selectedPoetryModel = $state.testedPath
        $poetryButton.Text = '已验证模型：' + (Split-Path $state.testedPath -Leaf) + '（点击可更换）'
    }
    $dialog.Dispose()
}
