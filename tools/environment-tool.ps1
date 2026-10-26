param([switch]$UiSmokeTest,[string]$AutoInspectReport='')
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.IO.Compression.FileSystem
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$data = Join-Path $env:LOCALAPPDATA 'ShiJu'
$store = Join-Path $data 'deployment'
$cache = Join-Path $store 'cache'
$base = Join-Path $store 'base-python-3.12.10'
$script:python = Join-Path $base 'python.exe'
$script:job = $null
$script:download = $null
$script:downloadError = $null
$script:action = ''
$script:report = ''
$script:cancelFile = ''
$script:verified = $false
$script:closing = $false
$script:savedPlan = ''
$script:detected = $false
$script:modelReady = $false
$script:planReady = $false
$form = New-Object Windows.Forms.Form
$form.Text = '诗矩 · 环境部署'
$form.ClientSize = New-Object Drawing.Size(960,780)
$form.MinimumSize = New-Object Drawing.Size(976,819)
$form.StartPosition = 'CenterScreen'
$form.Font = New-Object Drawing.Font('Microsoft YaHei UI',10)
function Add-Control($control,$x,$y,$w,$h) {
    $control.SetBounds($x,$y,$w,$h); $form.Controls.Add($control); return $control
}
function Label([string]$text,$x,$y,$w=830,$h=28) {
    $c=New-Object Windows.Forms.Label; $c.Text=$text; $null=Add-Control $c $x $y $w $h
}
function Button([string]$text,$x,$y,$w=130) {
    $c=New-Object Windows.Forms.Button; $c.Text=$text; return Add-Control $c $x $y $w 34
}
function TextBox($text,$x,$y,$w=650) {
    $c=New-Object Windows.Forms.TextBox; $c.Text=$text; return Add-Control $c $x $y $w 28
}
Label '诗矩环境准备' 24 15 400 32
Label '请按顺序完成下面四步。每一步完成后，下一步才会解锁。' 24 48
Label '第一步：检测电脑' 24 84 830 28
$detect=Button '开始检测电脑' 24 116 160
$detectStatus=New-Object Windows.Forms.Label; $detectStatus.Text='尚未检测'; $null=Add-Control $detectStatus 200 119 650 28
Label '第二步：选择模型' 24 164 830 28
$route=New-Object Windows.Forms.ComboBox
$route.DropDownStyle='DropDownList'
$route.Items.AddRange(@('官方 Qwen3.5-4B','自带本地模型（不保证兼容）'))
$route.SelectedIndex=0
$null=Add-Control $route 170 160 420 30
Label '官方模型文件约 9.34 GB。选择一个空目录；如果已有完整 Qwen3.5-4B 文件，工具会核验后复用，不重复下载。' 24 204 900 28
$model=TextBox (Join-Path $store 'models\Qwen3.5-4B') 24 236 650
$browseModel=Button '选择模型目录' 700 233 170
$officialButton=Button '部署官方 Qwen3.5-4B' 24 274 190
$existingButton=Button '使用已有模型' 230 274 150
$modelStatus=New-Object Windows.Forms.Label; $modelStatus.Text='还没有选定模型'; $null=Add-Control $modelStatus 400 277 520 28
Label '第三步：生成环境清单' 24 326 830 28
$planButton=Button '生成依赖清单' 24 358 150
$import=Button '导入清单' 190 358 120
$export=Button '导出安装文件' 326 358 130
$planStatus=New-Object Windows.Forms.Label; $planStatus.Text='请先完成模型选择'; $null=Add-Control $planStatus 470 361 420 28
Label '第四步：创建新环境' 24 410 830 28
Label '新虚拟环境预计占用约 18 GB，另需依赖下载缓存。不会修改已有 Python / Conda，请预留清单显示的空间。' 24 438 900 28
$target=TextBox (Join-Path $store 'environments\qwen35') 24 470 650
$browseTarget=Button '选择新环境目录' 700 467 170
$create=Button '根据清单创建环境' 24 510 170; $create.Enabled=$false
$cancel=Button '取消当前操作' 210 510 120; $cancel.Enabled=$false
$progress=New-Object Windows.Forms.ProgressBar
$progress.Style='Marquee'; $progress.Visible=$false
$null=Add-Control $progress 24 552 896 12
$status=New-Object Windows.Forms.Label
$status.Text='请选择“检测电脑”开始。官方候选仅开放已有证据覆盖的 GPU，实际可用性以本机真实验证为准。'
$null=Add-Control $status 24 574 896 40
$details=New-Object Windows.Forms.TextBox
$details.Multiline=$true; $details.ReadOnly=$true; $details.ScrollBars='Both'; $details.WordWrap=$false
$null=Add-Control $details 24 620 896 100
$enter=Button '进入诗矩' 24 735
$diagnostics=Button '导出诊断信息' 170 735 160
$enter.Enabled=$false
$script:planPath=Join-Path $store 'plan.json'
$script:controls=@($detect,$route,$browseModel,$browseTarget,$model,$target,$officialButton,$existingButton,$planButton,$import,$export,$create,$diagnostics)
function Busy([bool]$value) {
    foreach($control in $script:controls) { $control.Enabled=-not $value }
    $cancel.Enabled=$value; $progress.Visible=$value
    $planButton.Enabled=(-not $value -and $script:detected -and $script:modelReady)
    $create.Enabled=(-not $value -and $script:planReady)
    $enter.Enabled=(-not $value -and $script:verified)
}
function Pick-Folder($box) {
    $dialog=New-Object Windows.Forms.FolderBrowserDialog
    $dialog.Description='选择保存位置；不会覆盖用户已有环境'
    if(Test-Path -LiteralPath $box.Text) { $dialog.SelectedPath=$box.Text }
    if($dialog.ShowDialog($form) -eq 'OK') { $box.Text=$dialog.SelectedPath }
    $dialog.Dispose()
}
function Begin-Action([string]$action) {
    if($script:job -or $script:download) { return }
    New-Item -ItemType Directory -Force -Path $cache | Out-Null
    $script:action=$action
    $reports=Join-Path $store ('reports\'+[Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $reports | Out-Null
    $script:report=Join-Path $reports 'status.json'
    $script:cancelFile=Join-Path $reports 'cancel'
    Busy $true
    if(-not(Test-Path -LiteralPath $script:python)) {
        $status.Text='准备基础 Python：下载官方 NuGet CPython 3.12.10，不修改全局 PATH。'
        $package=Join-Path $cache 'python.3.12.10.nupkg'
        if(Test-Path -LiteralPath $package) {
            try { Complete-Bootstrap; Start-Backend; return } catch {
                $script:downloadError=$_.Exception.Message
                $details.Text=$script:downloadError
                $status.Text='基础 Python 准备失败，请检查网络或缓存后重试。'
                Busy $false
                return
            }
        }
        $script:download=New-Object Net.WebClient
        $script:downloadError=$null
        $script:download.add_DownloadFileCompleted({ param($sender,$event)
            if($event.Error) { $script:downloadError=$event.Error.Message }
            elseif($event.Cancelled) { $script:downloadError='下载已取消' }
        })
        $status.Text='正在下载基础 Python（约 14.5 MB），请稍候；窗口会在完成后继续检测。'
        $script:download.DownloadFileAsync([Uri]'https://api.nuget.org/v3-flatcontainer/python/3.12.10/python.3.12.10.nupkg', $package)
    } else { Start-Backend }
}
function Complete-Bootstrap {
    $package=Join-Path $cache 'python.3.12.10.nupkg'
    if((Get-FileHash -LiteralPath $package -Algorithm SHA256).Hash.ToLowerInvariant() -ne '0eb85c2dfccccf1b17352de4c397f69194035b7d37149eacc16f1147d93de3b8') { throw '基础 Python 下载摘要不符，请点击原操作重试。' }
    $temporary=Join-Path $store ('bootstrap-'+[Guid]::NewGuid().ToString('N'))
    [IO.Compression.ZipFile]::ExtractToDirectory($package,$temporary)
    if(Test-Path -LiteralPath $base) { throw '基础工具目录不完整，请保留该目录并联系开发者，不覆盖已有目录。' }
    Move-Item -LiteralPath (Join-Path $temporary 'tools') -Destination $base
    [IO.File]::WriteAllText((Join-Path $base 'python312._pth'),".`nLib`nDLLs`nLib/site-packages`n$root`n",(New-Object Text.UTF8Encoding($false)))
}
function Ensure-BasePython {
    $pth=Join-Path $base 'python312._pth'
    $expected=".`nLib`nDLLs`nLib/site-packages`n$root`n"
    if(-not(Test-Path -LiteralPath (Join-Path $base 'python.exe'))) { return }
    $current=if(Test-Path -LiteralPath $pth){[IO.File]::ReadAllText($pth)}else{''}
    if($current -ne $expected) {
        [IO.File]::WriteAllText($pth,$expected,(New-Object Text.UTF8Encoding($false)))
    }
}
function Start-Backend {
    Ensure-BasePython
    $arguments=@('-m','apps.launcher.deployer',$script:action,'--data-dir',$data,'--report',$script:report,'--cancel',$script:cancelFile,'--model',$model.Text,'--target',$target.Text,'--cache',$cache,'--plan',$script:planPath,'--route',$(if($route.SelectedIndex -eq 0){'official'}else{'local'}))
    if($script:action -eq 'export') { $arguments+=@('--export-dir',$script:exportDirectory) }
    foreach($arg in $arguments) { if($arg.Contains('"') -or $arg.Contains("`n")) { throw '路径包含无效字符' } }
    $info=New-Object Diagnostics.ProcessStartInfo
    $info.FileName=$script:python
    $info.Arguments=($arguments | ForEach-Object {
        $trailing=$_.Length-$_.TrimEnd('\').Length
        '"'+$_+('\' * $trailing)+'"'
    }) -join ' '
    $info.WorkingDirectory=$root; $info.UseShellExecute=$false; $info.CreateNoWindow=$true
    $info.EnvironmentVariables.Remove('PYTHONHOME'); $info.EnvironmentVariables.Remove('PYTHONPATH')
    $info.EnvironmentVariables['PYTHONIOENCODING']='utf-8'
    $script:job=[Diagnostics.Process]::Start($info)
    $status.Text='正在执行：'+$script:action+'。首次下载和编译可能需要较长时间。'
}
$detect.Add_Click({ try { Begin-Action 'inspect' } catch { Busy $false; $status.Text=$_.Exception.Message } })
$browseModel.Add_Click({ Pick-Folder $model })
$browseTarget.Add_Click({ Pick-Folder $target })
$officialButton.Add_Click({ try {
    if(-not $script:detected) { throw '请先完成第一步：检测电脑。' }
    $route.SelectedIndex=0
    if(-not $model.Text) { throw '请先选择官方模型保存目录。' }
    $modelStatus.Text='正在准备官方 Qwen3.5-4B...'
    Begin-Action 'download'
} catch { Busy $false; $status.Text=$_.Exception.Message } })
$existingButton.Add_Click({ try {
    if(-not $script:detected) { throw '请先完成第一步：检测电脑。' }
    $route.SelectedIndex=1
    if(-not (Test-Path -LiteralPath (Join-Path $model.Text 'config.json'))) { throw '请在上方选择已有模型目录；目录中必须有 config.json。' }
    Begin-Action 'verify-model'
} catch { Busy $false; $status.Text=$_.Exception.Message } })
$planButton.Add_Click({ try {
    if(-not $script:detected) { throw '请先完成第一步：检测电脑。' }
    if(-not $script:modelReady) { throw '请先完成第二步：选择或部署模型。' }
    Begin-Action 'plan'
} catch { Busy $false; $status.Text=$_.Exception.Message } })
$create.Add_Click({ try {
    if(-not(Test-Path -LiteralPath $script:planPath)) { throw '请先生成或导入环境清单。' }
    $script:verified=$false
    Begin-Action 'install'
} catch { Busy $false; $status.Text=$_.Exception.Message } })
$import.Add_Click({
    $dialog=New-Object Windows.Forms.OpenFileDialog; $dialog.Filter='环境清单 (*.json)|*.json'
    if($dialog.ShowDialog($form) -eq 'OK') {
        $script:planPath=$dialog.FileName
        try { Begin-Action 'import' } catch { Busy $false; $status.Text=$_.Exception.Message }
    }; $dialog.Dispose()
})
$export.Add_Click({
    if(-not $script:planReady -or -not(Test-Path -LiteralPath $script:planPath)) { $status.Text='请先生成或校验导入清单'; return }
    $dialog=New-Object Windows.Forms.FolderBrowserDialog
    $dialog.Description='选择导出父目录，工具将在其中新建 Conda 安装文件目录'
    if($dialog.ShowDialog($form) -eq 'OK') {
        $script:exportDirectory=Join-Path $dialog.SelectedPath ('诗矩安装文件-'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
        try { Begin-Action 'export' } catch { Busy $false; $status.Text=$_.Exception.Message }
    }
    $dialog.Dispose()
})
$diagnostics.Add_Click({
    $dialog=New-Object Windows.Forms.SaveFileDialog; $dialog.Filter='诊断信息 (*.json)|*.json'; $dialog.FileName='诗矩部署诊断.json'
    if($dialog.ShowDialog($form) -eq 'OK') {
        $script:savedPlan=$script:planPath; $script:planPath=$dialog.FileName
        try { Begin-Action 'diagnostics' } catch { Busy $false; $status.Text=$_.Exception.Message }
    }; $dialog.Dispose()
})
$cancel.Add_Click({
    [IO.File]::WriteAllText($script:cancelFile,'cancel')
    if($script:download) { $script:download.CancelAsync() }
    $status.Text='正在取消；保留已下载文件和安装日志。'
})
$enter.Add_Click({
    $info=New-Object Diagnostics.ProcessStartInfo
    $info.FileName=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $installedLauncher=Join-Path $target.Text 'app\launcher.ps1'
    if(-not(Test-Path -LiteralPath $installedLauncher)) { $status.Text='已安装软件入口不存在，请重新部署。'; return }
    $info.Arguments='-NoProfile -STA -ExecutionPolicy Bypass -File "'+$installedLauncher+'"'
    $info.UseShellExecute=$false; $info.CreateNoWindow=$true
    [Diagnostics.Process]::Start($info) | Out-Null
})
$model.Add_TextChanged({ $script:verified=$false; $enter.Enabled=$false })
$target.Add_TextChanged({ $script:verified=$false; $enter.Enabled=$false })
$timer=New-Object Windows.Forms.Timer; $timer.Interval=400
$timer.Add_Tick({
    try {
        if($script:download -and -not $script:download.IsBusy) {
            $script:download.Dispose(); $script:download=$null
            if($script:downloadError) { Busy $false; $status.Text='基础 Python 下载失败：'+$script:downloadError; $details.Text='请检查网络后重试。已下载的临时文件会保留但不会当作可用。'; if($script:closing){$form.Close()}; return }
            if(Test-Path -LiteralPath $script:cancelFile) { Busy $false; $status.Text='已取消基础 Python 下载，可重试。'; if($script:closing){$form.Close()}; return }
            Complete-Bootstrap; Start-Backend
        }
        if($script:job) {
            $state=$null
            if(Test-Path -LiteralPath $script:report) {
                try { $state=Get-Content -LiteralPath $script:report -Raw -Encoding UTF8 | ConvertFrom-Json } catch { }
                if($state) {
                    $status.Text=$state.stage
                    if($state.file) { $status.Text+=' · '+$state.file }
                    if($state.total_bytes) { $status.Text+=' · '+[Math]::Round(100*$state.received_bytes/$state.total_bytes,1)+'%' }
                    if($state.state -eq 'running' -and $state.stage -like '*真实约束*') {
                        $live=Join-Path (Split-Path -Parent $script:report) 'model-verification.json'
                        if(Test-Path -LiteralPath $live) { $details.Text=Get-Content -LiteralPath $live -Raw -Encoding UTF8 }
                    }
                }
            }
            if($script:job.HasExited) {
                $code=$script:job.ExitCode; $script:job.Dispose(); $script:job=$null
                if($state) { $details.Text=$state | ConvertTo-Json -Depth 30 }
                if($state.ok -and $script:action -eq 'inspect') {
                    $script:detected=$true; $detectStatus.Text='检测完成，可以选择模型'
                    $hostInfo=$state.result.host
                    $lines=@("系统：Windows $($hostInfo.windows_build) / $($hostInfo.arch)","环境磁盘可用：$([Math]::Round($hostInfo.free_bytes/1GB,1)) GiB",'', '显卡：')
                    foreach($gpu in $hostInfo.gpus) { $lines+="$($gpu.name) · $([Math]::Round($gpu.memory_bytes/1GB,1)) GiB · 驱动 $($gpu.driver_version) · 计算能力 $($gpu.compute_capability)" }
                    if(-not $hostInfo.gpus) { $lines+='未检测到可用 NVIDIA GPU，暂时不能创建本地推理环境。' }
                    $lines+=@($hostInfo.errors)
                    $lines+=@('', '已有环境（仅检测，不修改；默认新建专用环境）：')
                    foreach($candidate in $state.result.environments) {
                        $lines+="$($candidate.path) · "+$(if($candidate.usable){"可用 Python $($candidate.version)"}else{"发现路径但不可用：$($candidate.error)"})
                    }
                    $lines+=@('', 'Conda 工具：')+@($state.result.conda_tools)
                    $lines+=@('', '发现的模型目录（只代表找到文件，仍需选择并核验）：')
                    if($state.result.models) { $lines+=@($state.result.models) } else { $lines+='没有发现已知模型目录，请选择官方部署或手动选择已有模型。' }
                    $details.Text=$lines -join "`r`n"
                }
                if($state.ok -and $script:action -in @('download','verify-model')) {
                    if($script:action -eq 'verify-model') {
                        if($state.result.route -eq 'official') { $route.SelectedIndex=0 }
                        else { $route.SelectedIndex=1 }
                    }
                    $script:modelReady=$true
                    $spec=if($state.result.model){$state.result.model}else{$state.result}
                    $bytes=0; foreach($item in $spec.files.PSObject.Properties){ $bytes += [int64]$item.Value.bytes }
                    $modelStatus.Text="模型已选定：$($spec.model_id) · 文件约 $([Math]::Round($bytes/1GB,2)) GB · 已完成核验"
                    $status.Text='第二步完成。现在点击“生成依赖清单”。'
                }
                if($state.ok -and $script:action -in @('plan','import')) {
                    $p=$state.result
                    $script:planReady=$true; $create.Enabled=$true; $planStatus.Text='清单已生成，可以创建新环境'
                    $decimalGB=1000000000; $modelGB=[Math]::Round($p.model_bytes/$decimalGB,2); $downloadGB=[Math]::Round($p.download_bytes/$decimalGB,2); $environmentGB=[Math]::Round($p.environment_required_bytes/$decimalGB,1)
                    $lines=@("模型：$($p.model.model_id)","模型文件大小：约 $modelGB GB（已有完整文件会复用）","基础 Python + 依赖下载：约 $downloadGB GB","新虚拟环境占用：约 $environmentGB GB","建议至少预留：约 $([Math]::Round($p.environment_required_bytes/1GB + $p.download_bytes/1GB + 1,1)) GB","推理方式：BNB 4bit（NF4）","说明：创建前还会再次检查目标磁盘空间。")
                    if($p.route -eq 'local') { $lines+=@('自带模型：不保证兼容；出错请联系开发者。') }
                    $details.Text=($lines -join "`r`n")+"`r`n`r`n完整清单：`r`n"+($p | ConvertTo-Json -Depth 30)
                }
                if($code -eq 0 -and $state.ok) {
                    $status.Text='操作完成。'+$(if($script:action -eq 'install'){'真实生成验证通过，可进入诗矩。'}elseif($script:action -in @('plan','import')){'请查看清单后创建环境。'}else{''})
                    if($script:action -eq 'install') { $script:verified=$true }
                    if($script:action -eq 'export') {
                        $status.Text='安装文件已导出到：'+$state.result.directory
                        $details.Text="已生成 environment.yml、requirements.lock、安装及检查脚本。`r`n有 Conda 可执行 conda env create -f environment.yml。`r`n双击 install-conda.cmd 可分阶段安装并保存日志。`r`n网络失败可按说明恢复；导出目录内含使用说明。"
                    }
                } else { $status.Text='未完成：'+$state.error+'（阶段：'+$state.stage+'）' }
                if($script:action -eq 'diagnostics' -and $script:savedPlan) { $script:planPath=$script:savedPlan }
                Busy $false
                if($AutoInspectReport) {
                    Copy-Item -LiteralPath $script:report -Destination $AutoInspectReport
                    $form.Close()
                }
                if($script:closing) { $form.Close() }
            }
        }
    } catch {
        $status.Text=$_.Exception.Message; $script:download=$null; Busy $false
        if($AutoInspectReport) {
            @{ok=$false;error=$_.Exception.Message} | ConvertTo-Json | Set-Content -LiteralPath $AutoInspectReport -Encoding UTF8
            $form.Close()
        }
    }
})
$form.Add_FormClosing({param($sender,$event)
    if($script:job -or $script:download) {
        $event.Cancel=$true; $script:closing=$true; $cancel.PerformClick()
    }
})
if($UiSmokeTest) { $form.Dispose(); exit 0 }
if($AutoInspectReport) { $form.Add_Shown({ Begin-Action 'inspect' }) }
$timer.Start()
[Windows.Forms.Application]::Run($form)
$timer.Dispose(); $form.Dispose()
