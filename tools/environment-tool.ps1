param([switch]$UiSmokeTest,[string]$AutoInspectReport='')
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.IO.Compression.FileSystem
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
. (Join-Path $root 'scripts\data-directory.ps1')
$data = Get-ShiJuDataDirectory $root
$store = Join-Path $data 'deployment'
$cacheStore = Join-Path (Join-Path $env:LOCALAPPDATA 'ShiJu') 'deployment'
$cache = Join-Path $cacheStore 'cache'
$base = Join-Path $cacheStore 'base-python-3.12.10'
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
$form.ClientSize = New-Object Drawing.Size(1220,720)
$form.MinimumSize = New-Object Drawing.Size(1236,759)
$form.StartPosition = 'CenterScreen'
$form.Font = New-Object Drawing.Font('Microsoft YaHei UI',10)
$leftPanel=New-Object Windows.Forms.Panel
$leftPanel.SetBounds(0,0,620,720)
$leftPanel.Anchor='Top,Bottom,Left'
$leftPanel.AutoScroll=$true
$form.Controls.Add($leftPanel)
$rightPanel=New-Object Windows.Forms.Panel
$rightPanel.SetBounds(620,0,600,720)
$rightPanel.Anchor='Top,Bottom,Left,Right'
$rightPanel.BackColor=[Drawing.Color]::FromArgb(245,247,250)
$form.Controls.Add($rightPanel)
function Add-Control($control,$x,$y,$w,$h) {
    $control.SetBounds($x,$y,$w,$h); $leftPanel.Controls.Add($control); return $control
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
Label '请按顺序完成四步，检测结果与操作详情显示在右侧。' 24 48 570
Label '第一步：检测电脑' 24 84 830 28
$detect=Button '开始检测电脑' 24 116 160
$detectStatus=New-Object Windows.Forms.Label; $detectStatus.Text='尚未检测'; $null=Add-Control $detectStatus 200 119 650 28
Label '第二步：选择模型来源（二选一）' 24 156 900 28
$deployRoute=New-Object Windows.Forms.RadioButton
$deployRoute.Text='下载部署推荐模型'; $deployRoute.Checked=$true
$null=Add-Control $deployRoute 24 187 235 28
$localRoute=New-Object Windows.Forms.RadioButton
$localRoute.Text='加载已有本地模型'
$null=Add-Control $localRoute 290 187 250 28
$route=New-Object Windows.Forms.ComboBox
$route.DropDownStyle='DropDownList'
$route.Items.AddRange(@('Qwen3.5-4B','Qwen3.5-2B','Qwen3.5-0.8B'))
$route.SelectedIndex=0
$null=Add-Control $route 24 219 200 30
$pathCaption=New-Object Windows.Forms.Label
$pathCaption.Text='部署位置（模型文件保存目录）'
$null=Add-Control $pathCaption 240 221 350 28
$model=TextBox (Join-Path $store 'models\Qwen3.5-4B') 24 254 395
$browseModel=Button '选择部署位置' 435 251 160
$officialButton=Button '下载并部署模型' 24 291 190
$existingButton=Button '检查本地模型完整性' 24 291 190
$existingButton.Visible=$false
$modelStatus=New-Object Windows.Forms.Label
$modelStatus.Text='下载到所选目录；已有完整文件会核验后复用。'
$null=Add-Control $modelStatus 230 294 690 28
$script:deployModelPath=$model.Text
$script:localModelPath=''
$script:selectedModelRoute='official'
function Reset-ModelSelection {
    $script:modelReady=$false; $script:planReady=$false; $script:verified=$false
    $create.Enabled=$false; $enter.Enabled=$false; $planButton.Enabled=$false
}
$route.Add_SelectedIndexChanged({
    Reset-ModelSelection
    $model.Text=Join-Path $store ('models\'+$route.SelectedItem)
})
$deployRoute.Add_CheckedChanged({
    if($deployRoute.Checked) {
        $script:localModelPath=$model.Text
        $model.Text=$script:deployModelPath
        $route.Visible=$true
        $pathCaption.SetBounds(240,221,350,28)
        $pathCaption.Text='部署位置（模型文件保存目录）'
        $browseModel.Text='选择部署位置'
        $officialButton.Visible=$true; $existingButton.Visible=$false
        $modelStatus.Text='下载到所选目录；已有完整文件会核验后复用。'
    } else {
        $script:deployModelPath=$model.Text
        $model.Text=$script:localModelPath
        $route.Visible=$false
        $pathCaption.SetBounds(24,221,570,28)
        $pathCaption.Text='已有模型目录（包含 config.json、分词器和完整权重）'
        $browseModel.Text='选择本地模型'
        $officialButton.Visible=$false; $existingButton.Visible=$true
        $modelStatus.Text='只检查已有模型文件完整性；实际推理另行验收。'
    }
    Reset-ModelSelection
})
Label '第三步：生成环境清单' 24 326 830 28
$planButton=Button '生成依赖清单' 24 358 150
$import=Button '导入清单' 190 358 120
$export=Button '导出安装文件' 326 358 130
$planStatus=New-Object Windows.Forms.Label; $planStatus.Text='请先完成模型选择'; $null=Add-Control $planStatus 470 361 420 28
$runtimeProfile=New-Object Windows.Forms.ComboBox
$runtimeProfile.DropDownStyle='DropDownList'
$runtimeProfile.Items.AddRange(@('推理组合：自动选择','CPU FP32','GPU FP16（通用运算）','GPU BNB 4bit + Triton'))
$runtimeProfile.SelectedIndex=0
$null=Add-Control $runtimeProfile 24 399 285 30
$source=New-Object Windows.Forms.ComboBox
$source.DropDownStyle='DropDownList'
$source.Items.AddRange(@('下载来源：自动（国内优先）','国内镜像优先，失败转官方','仅官方源'))
$source.SelectedIndex=0
$null=Add-Control $source 325 399 270 30
$runtimeProfile.Add_SelectedIndexChanged({
    $script:planReady=$false; $script:verified=$false
    $create.Enabled=$false; $enter.Enabled=$false
    $planStatus.Text='推理组合已改变，请重新生成清单'
})
Label '第四步：创建 / 继续部署环境' 24 451 830 28
Label '可选空文件夹；失败可原目录继续。CPU / GPU 占用见右侧清单。' 24 479 570 28
$target=TextBox (Join-Path $store 'environments\qwen35') 24 511 395
$browseTarget=Button '选择新环境目录' 435 508 160
$create=Button '创建 / 继续部署' 24 551 170; $create.Enabled=$false
$cancel=Button '取消当前操作' 210 551 120; $cancel.Enabled=$false
$progress=New-Object Windows.Forms.ProgressBar
$progress.Style='Marquee'; $progress.Visible=$false
$null=Add-Control $progress 24 552 896 12
$status=New-Object Windows.Forms.Label
$status.Text='请选择“检测电脑”开始。自动部署候选仅开放已有证据覆盖的 GPU，实际可用性以本机真实验证为准。'
$null=Add-Control $status 24 574 896 40
$details=New-Object Windows.Forms.TextBox
$details.Multiline=$true; $details.ReadOnly=$true; $details.ScrollBars='Both'; $details.WordWrap=$false
$null=Add-Control $details 24 620 896 100
$resume=Button '恢复上次部署' 24 596 170
$importWheels=Button '导入本地依赖包' 210 596 170
$openCache=Button '打开依赖缓存' 396 596 199
$enter=Button '进入诗矩' 24 650
$diagnostics=Button '导出诊断信息' 170 650 160
$startupDiagnostics=Button '导出启动诊断' 340 650 160
$startupDiagnostics.Add_Click({
    try { & (Join-Path $root 'scripts/export-startup-diagnostics.ps1') -Workspace $root -DataDirectory $data }
    catch { $status.Text='诊断导出失败：'+$_.Exception.Message }
})
$enter.Enabled=$false
# Information occupies the entire right column, independently of the action steps.
foreach($control in @($detectStatus,$modelStatus,$planStatus,$progress,$status,$details)) {
    $rightPanel.Controls.Add($control)
}
$informationTitle=New-Object Windows.Forms.Label
$informationTitle.Text='检测结果与操作信息'
$informationTitle.SetBounds(20,18,550,30)
$rightPanel.Controls.Add($informationTitle)
$detectStatus.SetBounds(20,58,550,42)
$modelStatus.SetBounds(20,104,550,52)
$planStatus.SetBounds(20,160,550,38)
$status.SetBounds(20,208,550,72)
$progress.SetBounds(20,286,550,12)
$details.SetBounds(20,312,550,384)
$details.Anchor='Top,Bottom,Left,Right'
$details.WordWrap=$true
$details.ScrollBars='Vertical'
foreach($control in @($detectStatus,$modelStatus,$planStatus,$progress,$status)) {
    $control.Anchor='Top,Left,Right'
}
$script:planPath=Join-Path $store 'plan.json'
$script:controls=@($detect,$deployRoute,$localRoute,$route,$browseModel,$browseTarget,$model,$target,$officialButton,$existingButton,$planButton,$import,$export,$create,$diagnostics,$runtimeProfile,$source,$resume,$importWheels,$openCache)
function Busy([bool]$value) {
    foreach($control in $script:controls) { $control.Enabled=-not $value }
    $cancel.Enabled=$value; $progress.Visible=$value
    $planButton.Enabled=(-not $value -and $script:detected -and $script:modelReady)
    $create.Enabled=(-not $value -and $script:planReady)
    $enter.Enabled=(-not $value -and $script:verified)
}
function Format-FailureDetails($state) {
    $text=$state | Select-Object * -ExcludeProperty log_content | ConvertTo-Json -Depth 30
    if($state.prerequisite -eq 'vc-runtime') {
        $text="解决方法：`r`n"+($state.instructions -join "`r`n")+"`r`n`r`n微软官方下载（复制到浏览器）：`r`n"+$state.download_url+"`r`n`r`n详细检查结果：`r`n"+$text
    }
    if($state.verification_error) {
        $text="验证失败的具体原因：`r`n"+$state.verification_error+"`r`n`r`n"+$text
    }
    if($state.log_path) {
        $text+="`r`n`r`n日志位置："+$state.log_path+"`r`n日志内容：`r`n"
        if($state.log_truncated) { $text+="（日志较长，以下显示末尾 64 KiB，完整内容保留在日志文件）`r`n" }
        if($state.log_content) { $text+=$state.log_content } else { $text+="（进程日志为空；如有验证报告，具体原因已显示在上方。）" }
    }
    return $text
}
function Restore-DeploymentSession($session) {
    $p=$session.plan
    $localRoute.Checked=$true
    $model.Text=$session.model; $target.Text=$session.target
    $script:cache=$session.cache
    $script:selectedModelRoute=$p.route; $script:selectedModelId=$p.model.model_id
    $runtimeProfile.SelectedIndex=[Array]::IndexOf(@('auto','cpu','cuda-fp16','cuda-bnb'),[string]$p.profile)
    $script:detected=$true; $script:modelReady=$true
    $modelStatus.Text='已恢复：'+$p.model.model_id
    return $p
}
function Show-FolderDialog($dialog) {
    return $dialog.ShowDialog($form)
}
function Pick-Folder($box,[string]$description='选择保存位置；不会覆盖用户已有环境') {
    $dialog=$null
    try {
        $dialog=New-Object Windows.Forms.FolderBrowserDialog
        $dialog.Description=$description
        # Local-model fields start empty. Directory.Exists safely accepts empty,
        # missing and malformed paths, unlike Test-Path -LiteralPath ''.
        if(-not [string]::IsNullOrWhiteSpace($box.Text) -and [IO.Directory]::Exists($box.Text)) {
            $dialog.SelectedPath=$box.Text
        }
        if((Show-FolderDialog $dialog) -eq 'OK' -and -not [string]::IsNullOrWhiteSpace($dialog.SelectedPath)) {
            $box.Text=$dialog.SelectedPath
        }
    } catch {
        $status.Text='无法打开目录选择窗口：'+$_.Exception.Message
    } finally {
        if($dialog) { $dialog.Dispose() }
    }
}
function Begin-Action([string]$action) {
    if($script:job -or $script:download) { return }
    New-Item -ItemType Directory -Force -Path $cache | Out-Null
    $script:action=$action
    $details.Text='正在执行：'+$action+'。本次进度与错误会显示在这里。'
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
    $modelId=switch($route.SelectedIndex){ 1 {'Qwen/Qwen3.5-2B'} 2 {'Qwen/Qwen3.5-0.8B'} default {'Qwen/Qwen3.5-4B'} }
    if(-not $deployRoute.Checked -and $script:selectedModelId) { $modelId=$script:selectedModelId }
    $modelRoute=if($deployRoute.Checked){'official'}else{$script:selectedModelRoute}
    $arguments=@((Join-Path $root 'apps\launcher\python_entry.py'),'-m','apps.launcher.deployer',$script:action,'--data-dir',$data,'--report',$script:report,'--cancel',$script:cancelFile,'--model',$model.Text,'--model-id',$modelId,'--target',$target.Text,'--cache',$cache,'--plan',$script:planPath,'--route',$modelRoute)
    $runtimeProfileId=@('auto','cpu','cuda-fp16','cuda-bnb')[$runtimeProfile.SelectedIndex]
    $sourceId=@('auto','mirror','official')[$source.SelectedIndex]
    $arguments+=@('--profile',$runtimeProfileId,'--source',$sourceId)
    if($script:action -eq 'install' -and $script:continueExisting) { $arguments+='--continue-existing' }
    if($script:action -eq 'resume') { $arguments+='--latest' }
    if($script:action -eq 'import-wheels') { $arguments+=@('--wheel-dir',$script:wheelDirectory) }
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
$browseModel.Add_Click({
    $description=if($deployRoute.Checked){'选择推荐模型下载保存目录'}else{'选择已有模型目录：包含 config.json、分词器和完整权重'}
    Pick-Folder $model $description
})
$browseTarget.Add_Click({ Pick-Folder $target })
$officialButton.Add_Click({ try {
    if(-not $script:detected) { throw '请先完成第一步：检测电脑。' }
    if(-not $deployRoute.Checked) { throw '请先选择下载部署推荐模型。' }
    if(-not $model.Text) { throw '请先选择推荐模型保存目录。' }
    $modelStatus.Text='正在通过 ModelScope 准备所选推荐模型...'
    Begin-Action 'download'
} catch { Busy $false; $status.Text=$_.Exception.Message } })
$existingButton.Add_Click({ try {
    if(-not $script:detected) { throw '请先完成第一步：检测电脑。' }
    if(-not $localRoute.Checked) { throw '请先选择加载已有本地模型。' }
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
    $script:continueExisting=$false
    Begin-Action 'inspect-target'
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
$resume.Add_Click({ try { Begin-Action 'resume' } catch { Busy $false; $status.Text=$_.Exception.Message } })
$importWheels.Add_Click({
    if(-not $script:planReady) { $status.Text='请先生成清单或恢复上次部署'; return }
    $dialog=New-Object Windows.Forms.FolderBrowserDialog
    $dialog.Description='选择包含下载好的 .whl 文件的目录；将核验大小与 SHA256 后复制到缓存'
    try {
        if($dialog.ShowDialog($form) -eq 'OK') {
            $script:wheelDirectory=$dialog.SelectedPath
            Begin-Action 'import-wheels'
        }
    } catch { Busy $false; $status.Text=$_.Exception.Message }
    finally { $dialog.Dispose() }
})
$openCache.Add_Click({ try {
    $wheelhouse=Join-Path $cache 'wheels'
    New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null
    Start-Process explorer.exe -ArgumentList ('"'+$wheelhouse+'"')
} catch { $status.Text=$_.Exception.Message } })
$cancel.Add_Click({
    [IO.File]::WriteAllText($script:cancelFile,'cancel')
    if($script:download) { $script:download.CancelAsync() }
    $status.Text='正在取消；保留已下载文件和安装日志。'
})
$enter.Add_Click({
    $info=New-Object Diagnostics.ProcessStartInfo
    $info.FileName=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    # Run this package's application code, reusing only the installed interpreter.
    $installedLauncher=Join-Path $root 'launcher.ps1'
    $applicationPython=Join-Path $target.Text 'python.exe'
    if(-not(Test-Path -LiteralPath $applicationPython)) { $applicationPython=Join-Path $target.Text 'Scripts\python.exe' }
    if(-not(Test-Path -LiteralPath $applicationPython)) { $status.Text='已安装 Python 不存在，请恢复或重新部署。'; return }
    $info.Arguments='-NoProfile -STA -ExecutionPolicy Bypass -File "'+$installedLauncher+'" -DataDir "'+$data+'" -ApplicationPython "'+$applicationPython+'"'
    $info.UseShellExecute=$false; $info.CreateNoWindow=$true
    [Diagnostics.Process]::Start($info) | Out-Null
})
$model.Add_TextChanged({ Reset-ModelSelection })
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
                    if($state.file_index) { $status.Text+=' · 第 '+$state.file_index+'/'+$state.file_count+' 个' }
                    if($state.source) { $status.Text+=' · '+$state.source }
                    if($state.message) { $status.Text+=' · '+$state.message }
                    if($state.file) { $status.Text+=' · '+$state.file }
                    if($state.speed_bytes_per_second) { $status.Text+=' · '+[Math]::Round($state.speed_bytes_per_second/1MB,1)+' MB/s' }
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
                    if(-not $hostInfo.gpus) { $lines+='未检测到可用 NVIDIA GPU，将使用 CPU FP32；建议选择 0.8B 或 2B，生成速度较慢。' }
                    $lines+=@($hostInfo.errors)
                    $vc=$state.result.vc_runtime
                    if($vc.ok) { $lines+=@('VC++ x64 运行库：可用 '+$vc.version) } else { $lines+=@('VC++ x64 运行库：缺失或不可用，创建环境时将自动补装；请允许系统权限提示。')+@($vc.problems) }
                    $lines+=@('', '已有环境（仅检测，不修改；默认新建专用环境）：')
                    foreach($candidate in $state.result.environments) {
                        $lines+="$($candidate.path) · "+$(if($candidate.usable){"可用 Python $($candidate.version)"}else{"发现路径但不可用：$($candidate.error)"})
                    }
                    $lines+=@('', 'Conda 工具：')+@($state.result.conda_tools)
                    $lines+=@('', '发现的模型目录（只代表找到文件，仍需选择并核验）：')
                    if($state.result.models) { $lines+=@($state.result.models) } else { $lines+='没有发现已知模型目录，请选择推荐模型部署或手动选择已有模型。' }
                    $details.Text=$lines -join "`r`n"
                }
                if($state.ok -and $script:action -in @('download','verify-model')) {
                    $script:selectedModelRoute=if($script:action -eq 'download'){'official'}else{$state.result.route}
                    $script:modelReady=$true
                    $spec=if($state.result.model){$state.result.model}else{$state.result}
                    $script:selectedModelId=$spec.model_id
                    $bytes=0; foreach($item in $spec.files.PSObject.Properties){ $bytes += [int64]$item.Value.bytes }
                    $modelStatus.Text="模型已选定：$($spec.model_id) · 文件约 $([Math]::Round($bytes/1GB,2)) GB · 已完成核验"
                    $status.Text='第二步完成。现在点击“生成依赖清单”。'
                }
                if($state.ok -and $script:action -in @('plan','import','resume')) {
                    $p=$state.result
                    if($script:action -eq 'resume') {
                        $session=$state.result; $p=Restore-DeploymentSession $session
                    }
                    $script:planReady=$true; $create.Enabled=$true; $planStatus.Text='清单已生成，可以创建新环境'
                    $decimalGB=1000000000; $modelGB=[Math]::Round($p.model_bytes/$decimalGB,2); $downloadGB=[Math]::Round($p.download_bytes/$decimalGB,2); $environmentGB=[Math]::Round($p.environment_required_bytes/$decimalGB,1)
                    $lines=@("模型：$($p.model.model_id)","依赖组合：$($p.profile)；共 $($p.dependencies.Count) 个包","模型文件大小：约 $modelGB GB（已有完整文件会复用）","基础 Python + 依赖下载：约 $downloadGB GB","新虚拟环境占用：约 $environmentGB GB","建议至少预留：约 $([Math]::Round($p.environment_required_bytes/1GB + $p.download_bytes/1GB + 1,1)) GB","推理方式：$($p.hardware.device) · $($p.inference.reason)","说明：创建前还会再次检查目标磁盘空间。")
                    if($p.route -eq 'local') { $lines+=@('自带模型：不保证兼容；出错请联系开发者。') }
                    $details.Text=($lines -join "`r`n")+"`r`n`r`n完整清单：`r`n"+($p | ConvertTo-Json -Depth 30)
                }
                if($code -eq 0 -and $state.ok) {
                    $status.Text='操作完成。'+$(if($script:action -eq 'install'){'真实生成验证通过，可进入诗矩。'}elseif($script:action -in @('plan','import','resume')){'请查看清单后创建环境。'}else{''})
                    if($script:action -eq 'install') { $script:verified=$true }
                    if($script:action -eq 'install-vc-runtime') { $status.Text='VC++ x64 运行库检查通过，可点击“创建 / 继续部署”。' }
                    if($script:action -eq 'resume') {
                        $status.Text='已恢复部署记录（上次阶段：'+$session.stage+'）。点击“创建 / 继续部署”复核并继续。'
                        if($session.state -eq 'verified') { $script:verified=$true; $status.Text='该环境已完成验证，可进入诗矩。' }
                    }
                    if($script:action -eq 'import-wheels') {
                        $status.Text='本地包导入：通过 '+$state.result.accepted.Count+' 个，拒绝 '+$state.result.rejected.Count+' 个。可继续部署。'
                    }
                    if($script:action -eq 'export') {
                        $status.Text='安装文件已导出到：'+$state.result.directory
                        $details.Text="已生成 environment.yml、requirements.lock、安装及检查脚本。`r`n有 Conda 可执行 conda env create -f environment.yml。`r`n双击 install-conda.cmd 可分阶段安装并保存日志。`r`n网络失败可按说明恢复；导出目录内含使用说明。"
                    }
                } else {
                    $status.Text='未完成：'+$state.error+'（阶段：'+$state.stage+'）'
                    $details.Text=Format-FailureDetails $state
                }
                if($script:action -eq 'diagnostics' -and $script:savedPlan) { $script:planPath=$script:savedPlan }
                Busy $false
                if($AutoInspectReport) {
                    Copy-Item -LiteralPath $script:report -Destination $AutoInspectReport
                    $form.Close()
                }
                if($state.ok -and $script:action -eq 'inspect-target') {
                    $proceed=$true
                    if($state.result.kind -eq 'existing') {
                        $missing=@($state.result.missing) -join "`r`n"
                        if(-not $missing) { $missing='清单依赖已齐全，将继续实际运行验证。' }
                        $answer=[Windows.Forms.MessageBox]::Show($form, "已有虚拟环境符合清单要求。`r`n待补装依赖：`r`n$missing`r`n`r`n是否在此环境基础上继续配置？", '继续配置已有环境', [Windows.Forms.MessageBoxButtons]::YesNo, [Windows.Forms.MessageBoxIcon]::Question, [Windows.Forms.MessageBoxDefaultButton]::Button2)
                        $proceed=($answer -eq [Windows.Forms.DialogResult]::Yes)
                        $script:continueExisting=$proceed
                    }
                    if($proceed) { Begin-Action 'install' } else { $status.Text='已取消，已有环境未修改。' }
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
if($UiSmokeTest) {
    if($details.Parent -ne $rightPanel -or $status.Parent -ne $rightPanel -or $model.Parent -ne $leftPanel -or $create.Parent -ne $leftPanel) { throw '左右分栏布局检查失败' }
    function Show-FolderDialog($dialog) {
        $script:folderDialogCalls++
        return [Windows.Forms.DialogResult]::Cancel
    }
    $script:folderDialogCalls=0
    $downloadPath=$model.Text
    $savedTarget=$target.Text
    $localRoute.Checked=$true
    $model.Text=''
    $savedStatus=$status.Text
    Pick-Folder $model
    $target.Text=''
    Pick-Folder $target
    if($script:folderDialogCalls -ne 2 -or $model.Text -or $target.Text -or $status.Text -ne $savedStatus) { throw '空目录浏览或取消回归检查失败' }
    $target.Text=$savedTarget
    $deployRoute.Checked=$true
    $localRoute.Checked=$true
    if($deployRoute.Checked -or $model.Text -or $browseModel.Text -ne '选择本地模型') { throw '本地模型路线切换失败' }
    $model.Text='D:\Models\existing-model'
    $deployRoute.Checked=$true
    if($model.Text -ne $downloadPath -or $browseModel.Text -ne '选择部署位置') { throw '部署目录恢复失败' }
    $route.SelectedIndex=2
    if($model.Text -notlike '*Qwen3.5-0.8B') { throw '官方模型切换失败' }
    $localRoute.Checked=$true
    if($model.Text -ne 'D:\Models\existing-model') { throw '本地目录恢复失败' }
    $script:modelReady=$true; $script:planReady=$true
    $model.Text='D:\Models\changed-model'
    if($script:modelReady -or $script:planReady) { throw '模型目录变更未清除旧检查结果' }
    $script:modelReady=$true; $script:planReady=$true
    $runtimeProfile.SelectedIndex=1
    if($script:planReady -or -not $script:modelReady -or $runtimeProfile.Items.Count -ne 4) { throw '组合选择未正确失效清单或错误清除模型状态' }
    $script:planReady=$true
    $source.SelectedIndex=2
    if(-not $script:planReady) { throw '仅切换下载源不应失效清单' }
    $session=[pscustomobject]@{plan=[pscustomobject]@{route='official';profile='cuda-fp16';model=[pscustomobject]@{model_id='Qwen/Qwen3.5-0.8B'}};model='D:\Models\restored';target='D:\Envs\restored';cache=$cache}
    $null=Restore-DeploymentSession $session
    if($runtimeProfile.SelectedIndex -ne 2 -or $model.Text -ne $session.model -or $target.Text -ne $session.target -or -not $script:modelReady -or $script:selectedModelRoute -ne 'official') { throw '恢复部署未还原组合或路径' }
    $failure=[pscustomobject]@{ok=$false;error='failed';log_path='D:\reports\test.log';log_content="DLL load failed`r`nVC++ runtime missing";log_truncated=$false}
    $formatted=Format-FailureDetails $failure
    if(-not $formatted.Contains("日志内容：`r`nDLL load failed`r`nVC++ runtime missing")) { throw '失败详情未显示可读的原始日志' }
    $failure=[pscustomobject]@{ok=$false;error='failed';log_path='D:\reports\empty.log';log_content='';verification_error='当前可用 0.88 GiB，估算需要 5.73 GiB'}
    $formatted=Format-FailureDetails $failure
    if(-not $formatted.StartsWith("验证失败的具体原因：`r`n当前可用 0.88 GiB") -or -not $formatted.Contains('进程日志为空')) { throw '空日志情况下未突出显示验证报告原因' }
    $form.Dispose(); exit 0
}
if($AutoInspectReport) { $form.Add_Shown({ Begin-Action 'inspect' }) }
$timer.Start()
[Windows.Forms.Application]::Run($form)
$timer.Dispose(); $form.Dispose()
