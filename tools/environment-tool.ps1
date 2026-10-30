param([switch]$UiSmokeTest,[string]$AutoInspectReport='')
$ErrorActionPreference = 'Stop'
$UiPreviewDirectory=$env:SHIJU_UI_PREVIEW_DIRECTORY
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
$script:advisory = $null
$script:activePlan = $null
$script:currentAdvice = $null
$script:profileIds = @('auto','cpu','cuda-fp16','cuda-bnb','cpu-bf16','cuda-bnb8')
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
$deployRoute.Text='选择并下载模型'; $deployRoute.Checked=$true
$null=Add-Control $deployRoute 24 187 235 28
$localRoute=New-Object Windows.Forms.RadioButton
$localRoute.Text='加载已有本地模型'
$null=Add-Control $localRoute 290 187 250 28
$route=New-Object Windows.Forms.ComboBox
$route.DropDownStyle='DropDownList'
$route.Items.AddRange(@('Qwen3.5-4B','Qwen3.5-2B','Qwen3.5-0.8B'))
$route.SelectedIndex=0
$route.FormattingEnabled=$true
$route.Add_Format({ param($sender,$event)
    if($script:advisory.recommendation -and $event.ListItem -eq ($script:advisory.recommendation.model_id -replace '^Qwen/','')) {
        $event.Value=[string]$event.ListItem+'（推荐）'
    }
})
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
    $script:selectedModelId=$null; $script:selectedModelRoute=if($deployRoute.Checked){'official'}else{'local'}
    $script:activePlan=$null
    $create.Enabled=$false; $enter.Enabled=$false; $planButton.Enabled=$false
    if($script:advisory) { Show-SelectionAdvice }
    if($wizardMain) { Invalidate-WizardPlan }
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
$runtimeProfile.Items.AddRange(@('推理组合：自动选择','CPU FP32','GPU FP16（通用运算）','GPU BNB 4bit + Triton','CPU BF16（待本机验收）','GPU BNB 8bit + Triton'))
$runtimeProfile.SelectedIndex=0
$null=Add-Control $runtimeProfile 24 399 285 30
$source=New-Object Windows.Forms.ComboBox
$source.DropDownStyle='DropDownList'
$source.Items.AddRange(@('下载来源：自动（国内优先）','国内镜像优先，失败转官方','仅官方源'))
$source.SelectedIndex=0
$null=Add-Control $source 325 399 270 30
$runtimeProfile.Add_SelectedIndexChanged({
    $script:planReady=$false; $script:verified=$false
    $script:activePlan=$null
    $create.Enabled=$false; $enter.Enabled=$false
    $planStatus.Text='推理组合已改变，请重新生成清单'
    if($script:advisory) { Show-SelectionAdvice }
    if($wizardMain) { Invalidate-WizardPlan }
})
Label '第四步：创建 / 继续部署环境' 24 451 830 28
Label '可选空文件夹；失败可原目录继续。运行资源与磁盘空间见右侧。' 24 479 570 28
$target=TextBox (Join-Path $store 'environments\qwen35') 24 511 395
foreach($pathInput in @($model,$target)) {
    $pathInput.Add_KeyDown({ param($sender,$event)
        if($event.Control -and $event.KeyCode -eq [Windows.Forms.Keys]::A) {
            $sender.SelectAll(); $event.SuppressKeyPress=$true
        }
    })
}
$browseTarget=Button '选择新环境目录' 435 508 160
$create=Button '创建 / 继续部署' 24 551 170; $create.Enabled=$false
$cancel=Button '取消当前操作' 210 551 120; $cancel.Enabled=$false
$progress=New-Object Windows.Forms.ProgressBar
$progress.Style='Marquee'; $progress.Visible=$false
$null=Add-Control $progress 24 552 896 12
$status=New-Object Windows.Forms.TextBox
$status.Multiline=$true; $status.ReadOnly=$true; $status.WordWrap=$true
$status.ScrollBars='Vertical'; $status.BorderStyle='FixedSingle'
$status.BackColor=[Drawing.Color]::FromArgb(239,237,229)
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
$resourceAdvice=New-Object Windows.Forms.TextBox
$resourceAdvice.Multiline=$true; $resourceAdvice.ReadOnly=$true
$resourceAdvice.WordWrap=$true; $resourceAdvice.ScrollBars='Vertical'
$resourceAdvice.Text='完成第一步后，此处将显示本机推荐配置与资源预算。'
$resourceAdvice.SetBounds(20,204,550,210)
$resourceAdvice.Anchor='Top,Left,Right'
$rightPanel.Controls.Add($resourceAdvice)
$status.SetBounds(20,422,550,52)
$progress.SetBounds(20,479,550,12)
$details.SetBounds(20,503,550,193)
$details.Anchor='Top,Bottom,Left,Right'
$details.WordWrap=$true
$details.ScrollBars='Vertical'
foreach($control in @($detectStatus,$modelStatus,$planStatus,$progress,$status)) {
    $control.Anchor='Top,Left,Right'
}
$script:planPath=Join-Path $store 'plan.json'
function Show-SelectionAdvice {
    if(-not $script:advisory) { return }
    if(($script:activePlan -or $script:wizardPreview) -and $script:advisory.selection) {
        $script:currentAdvice=$script:advisory.selection
    } elseif($deployRoute.Checked) {
        $id=switch($route.SelectedIndex){1 {'Qwen/Qwen3.5-2B'} 2 {'Qwen/Qwen3.5-0.8B'} default {'Qwen/Qwen3.5-4B'}}
        $profile=$script:profileIds[$runtimeProfile.SelectedIndex]
        $script:currentAdvice=@($script:advisory.options | Where-Object { $_.model_id -eq $id -and $_.requested_profile -eq $profile }) | Select-Object -First 1
    } else {
        $script:currentAdvice=[pscustomobject]@{state='unknown';title='待评估：已有本地模型';notice='自带模型的兼容性尚未确认，安装后将通过实际加载和生成检查。';requires_confirmation=$true;text=($script:advisory.summary+"`r`n已有本地模型不能仅凭目录名称套用官方模型实测。请选择模型并生成清单，核对资源预算后再创建环境。")}
        if(-not [string]::IsNullOrWhiteSpace($model.Text) -and (Split-Path -Leaf $model.Text) -match '(?i)Qwen3\.5-(4B|2B|0\.8B)') {
            $id='Qwen/Qwen3.5-'+$Matches[1].ToUpperInvariant()
            $profile=$script:profileIds[$runtimeProfile.SelectedIndex]
            $script:currentAdvice=@($script:advisory.options | Where-Object { $_.model_id -eq $id -and $_.requested_profile -eq $profile }) | Select-Object -First 1
        }
    }
    if($script:currentAdvice) {
        $resourceAdvice.Text=($script:currentAdvice.text -replace "`n","`r`n")+"`r`n`r`n当前显示上次检测结果；创建环境前会重新检测资源。"
        $resourceAdvice.ForeColor=switch($script:currentAdvice.state){'recommended' {[Drawing.Color]::DarkGreen} 'risk' {[Drawing.Color]::DarkRed} default {[Drawing.Color]::FromArgb(100,65,10)}}
    } else { $resourceAdvice.Text=$script:advisory.summary }
}
function Show-ResourceConfirmation($message) {
    $dialog=New-Object Windows.Forms.Form
    $dialog.Text='确认安装当前方案'; $dialog.ClientSize=New-Object Drawing.Size(560,240)
    $dialog.StartPosition='CenterParent'; $dialog.FormBorderStyle='FixedDialog'
    $dialog.MaximizeBox=$false; $dialog.MinimizeBox=$false
    $label=New-Object Windows.Forms.Label
    $label.SetBounds(20,16,520,160); $label.Text=$message; $label.AutoSize=$false
    $dialog.Controls.Add($label)
    $continue=New-Object Windows.Forms.Button
    $continue.Text='继续安装当前方案'; $continue.SetBounds(190,190,170,34)
    $continue.DialogResult=[Windows.Forms.DialogResult]::OK; $dialog.Controls.Add($continue)
    $back=New-Object Windows.Forms.Button
    $back.Text='返回调整方案'; $back.SetBounds(372,190,168,34)
    $back.DialogResult=[Windows.Forms.DialogResult]::Cancel; $dialog.Controls.Add($back)
    $dialog.AcceptButton=$back; $dialog.CancelButton=$back
    try { return ($dialog.ShowDialog($form) -eq [Windows.Forms.DialogResult]::OK) }
    finally { $dialog.Dispose() }
}
function Return-ToInstallationPlan {
    $script:wizardAuto=$false; $script:wizardPrepareOnly=$false
    $script:wizardFailed=$false
    $script:wizardPlanNotice='尚未开始创建环境。你可以调整方案，也可以继续安装当前方案。'
    $status.Text=$script:wizardPlanNotice
    if($wizardMain) { $script:wizardPage=1; Update-Wizard }
}
function Confirm-ResourceAdvice {
    if(-not $script:currentAdvice) { throw '请先检测电脑并查看配置建议。' }
    if(-not $script:currentAdvice.requires_confirmation) { return $true }
    $message=$script:currentAdvice.notice
    if(-not $message) { $message=$script:currentAdvice.title }
    # Ignore normal free-memory fluctuations; changed risk, model or execution
    # profile must be reviewed again. Path/selection edits clear this approval.
    $key=@($script:currentAdvice.model_id,$script:currentAdvice.profile,$script:currentAdvice.state,$message) -join '|'
    if($script:acceptedResourceAdvice -eq $key) { return $true }
    if(Show-ResourceConfirmation ($message+"`r`n`r`n继续后将安装当前方案，并通过实际加载和生成检查。")) {
        $script:acceptedResourceAdvice=$key
        return $true
    }
    Return-ToInstallationPlan
    return $false
}
$script:controls=@($detect,$deployRoute,$localRoute,$route,$browseModel,$browseTarget,$model,$target,$officialButton,$existingButton,$planButton,$import,$export,$create,$diagnostics,$runtimeProfile,$source,$resume,$importWheels,$openCache)
function Busy([bool]$value) {
    $script:wizardBusy=$value
    if($value -and $wizardMain) {
        $script:wizardStartedAt=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        $script:wizardPage=2; $script:wizardRetry=$script:action; $script:wizardFailed=$false
        $wizardChecks.Text="程序会自动继续，无需重复点击。`r`n取消后会保留已下载文件，下次核验后复用。"
        $wizardStage.Text=switch($script:action){inspect {'检查电脑配置'} preview {'检查安装方案与空间'} download {'下载并核验模型'} verify-model {'检查本地模型'} plan {'自动准备运行清单'} install {'安装运行环境并验证生成'} inspect-target {'检查目标目录'} resume {'恢复部署记录'} default {'正在处理所选操作'}}
    }
    foreach($control in $script:controls) { $control.Enabled=-not $value }
    $cancel.Enabled=$value; $progress.Visible=$value
    if($value) { $progress.Style='Marquee' }
    $planButton.Enabled=(-not $value -and $script:detected -and $script:modelReady)
    $create.Enabled=(-not $value -and $script:planReady)
    $enter.Enabled=(-not $value -and $script:verified)
    if($wizardMain) { Update-Wizard }
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
    if ($state.diagnostics.log_path) { $text += "`r`n阶段日志：$($state.diagnostics.log_path)`r`n诊断编号：$($state.diagnostics.diagnostic_id)" }
    return $text
}
function Restore-DeploymentSession($session) {
    $p=$session.plan
    $localRoute.Checked=$true
    $model.Text=$session.model; $target.Text=$session.target
    $script:cache=$session.cache
    $script:selectedModelRoute=$p.route; $script:selectedModelId=$p.model.model_id
    $restoredProfile=[string]$p.profile
    $runtimeProfile.SelectedIndex=[Array]::IndexOf($script:profileIds,$restoredProfile)
    $script:detected=$true; $script:modelReady=$true
    $modelStatus.Text='已恢复：'+$p.model.model_id
    return $p
}
function Test-DeploymentPaths {
    foreach($entry in @(@('模型位置',$model.Text),@('运行环境位置',$target.Text))) {
        if($entry[1] -match '[\p{IsCJKUnifiedIdeographs}\p{IsCJKUnifiedIdeographsExtensionA}\p{IsCJKCompatibilityIdeographs}]') {
            throw "$($entry[0])不能包含中文，请选择或输入不含中文的完整路径。"
        }
    }
}
function Show-FolderDialog($dialog) {
    return $dialog.ShowDialog($form)
}
function Pick-Folder($box,[string]$description='选择保存位置；不会覆盖用户已有环境') {
    $dialog=$null
    try {
        # OpenFileDialog uses the modern Windows shell dialog, including its
        # editable address bar. A sentinel filename selects the current folder.
        $dialog=New-Object Windows.Forms.OpenFileDialog
        $dialog.Title=$description
        $dialog.CheckFileExists=$false; $dialog.CheckPathExists=$true
        $dialog.ValidateNames=$false; $dialog.FileName='选择当前文件夹'
        $dialog.Filter='文件夹|*.shiju-folder-selection'
        if(-not [string]::IsNullOrWhiteSpace($box.Text) -and [IO.Directory]::Exists($box.Text)) {
            $dialog.InitialDirectory=$box.Text
        }
        if((Show-FolderDialog $dialog) -eq 'OK' -and -not [string]::IsNullOrWhiteSpace($dialog.FileName)) {
            $selected=if([IO.Directory]::Exists($dialog.FileName)){$dialog.FileName}else{[IO.Path]::GetDirectoryName($dialog.FileName)}
            if(-not [IO.Directory]::Exists($selected)) { throw '请选择已有目录，或先在窗口中新建目录。' }
            $box.Text=$selected
        }
    } catch {
        $status.Text='无法打开目录选择窗口：'+$_.Exception.Message
    } finally {
        if($dialog) { $dialog.Dispose() }
    }
}
function Begin-Action([string]$action) {
    if($script:job -or $script:download) { return }
    if($action -in @('preview','download','verify-model','plan','import','inspect-target','install')) { Test-DeploymentPaths }
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
                if($wizardMain){ Complete-WizardAction $null 1 }
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
function Get-BackendModelSelection {
    if($deployRoute.Checked) {
        $id=switch($route.SelectedIndex){ 1 {'Qwen/Qwen3.5-2B'} 2 {'Qwen/Qwen3.5-0.8B'} default {'Qwen/Qwen3.5-4B'} }
        return @{route='official';id=$id}
    }
    $id='local'
    if(-not [string]::IsNullOrWhiteSpace($model.Text) -and (Split-Path -Leaf $model.Text) -match '(?i)Qwen3\.5-(4B|2B|0\.8B)') {
        $id='Qwen/Qwen3.5-'+$Matches[1].ToUpperInvariant()
    }
    return @{route='local';id=$id}
}
function Start-Backend {
    Ensure-BasePython
    $selection=Get-BackendModelSelection
    $modelId=$selection.id; $modelRoute=$selection.route
    $arguments=@((Join-Path $root 'apps\launcher\python_entry.py'),'-m','apps.launcher.deployer',$script:action,'--data-dir',$data,'--report',$script:report,'--cancel',$script:cancelFile,'--model',$model.Text,'--model-id',$modelId,'--target',$target.Text,'--cache',$cache,'--plan',$script:planPath,'--route',$modelRoute)
    $runtimeProfileId=$script:profileIds[$runtimeProfile.SelectedIndex]
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
    $status.Text=if($wizardMain){$wizardStage.Text+'，请稍候。首次下载和编译可能需要较长时间。'}else{'正在执行：'+$script:action}
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
    Show-SelectionAdvice
    if(-not (Confirm-ResourceAdvice)) { $status.Text='已取消下载。请根据资源建议调整模型或运行方式。'; return }
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
function Request-Install { try {
    if(-not(Test-Path -LiteralPath $script:planPath)) { throw '请先生成或导入环境清单。' }
    $script:verified=$false
    $script:continueExisting=$false
    Begin-Action 'inspect-target'
} catch { Busy $false; $status.Text=$_.Exception.Message; if($wizardMain){$script:wizardFailed=$true; Update-Wizard} } }
$create.Add_Click({ Request-Install })
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
function Open-ShiJu {
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
}
$enter.Add_Click({ Open-ShiJu })
$model.Add_TextChanged({ Reset-ModelSelection })
$target.Add_TextChanged({ $script:verified=$false; $enter.Enabled=$false; if($wizardMain) { Invalidate-WizardPlan } })
$timer=New-Object Windows.Forms.Timer; $timer.Interval=400
$timer.Add_Tick({
    try {
        if($script:download -and -not $script:download.IsBusy) {
            $script:download.Dispose(); $script:download=$null
            if($script:downloadError) { Busy $false; $status.Text='基础 Python 下载失败：'+$script:downloadError; $details.Text='请检查网络后重试。已下载的临时文件会保留但不会当作可用。'; if($wizardMain){ Complete-WizardAction $null 1 }; if($script:closing){$form.Close()}; return }
            if(Test-Path -LiteralPath $script:cancelFile) { Busy $false; $status.Text='已取消基础 Python 下载，可重试。'; if($wizardMain){ Complete-WizardAction $null 1 }; if($script:closing){$form.Close()}; return }
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
                    if($state.total_bytes) {
                        $percent=[Math]::Min(100,[Math]::Max(0,100*$state.received_bytes/$state.total_bytes))
                        $status.Text+=' · '+[Math]::Round($state.received_bytes/1MB,1)+' / '+[Math]::Round($state.total_bytes/1MB,1)+' MB（'+[Math]::Round($percent,1)+'%）'
                        $progress.Style='Continuous'; $progress.Value=[int]$percent
                    } else { $progress.Style='Marquee' }
                    if($wizardMain -and $script:action -eq 'install') { $wizardStage.Text=$state.stage }
                    if($state.state -eq 'running' -and $state.stage -like '*真实约束*') {
                        $live=Join-Path (Split-Path -Parent $script:report) 'model-verification.json'
                        if(Test-Path -LiteralPath $live) {
                            try {
                                $liveText=Get-Content -LiteralPath $live -Raw -Encoding UTF8
                                $liveReport=$liveText | ConvertFrom-Json
                                $details.Text=$liveText
                                if($wizardMain) { Show-VerificationProgress $liveReport }
                            } catch { } # A report replacement can race this polling tick.
                        } elseif($wizardMain) {
                            $wizardStage.Text='正在检查依赖与 CPU / GPU 运算'
                            $wizardChecks.Text="[进行中] 检查运行环境`r`n[待进行] 加载模型`r`n[待进行] 自由生成测试`r`n[待进行] 约束生成测试"
                        }
                    }
                    if($wizardMain -and $script:wizardBusy) {
                        $elapsed=[Math]::Max(0,[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()-$script:wizardStartedAt)
                        $status.Text+=' · 本步已用时 '+[TimeSpan]::FromSeconds($elapsed).ToString('hh\:mm\:ss')
                    }
                }
            }
            if($script:job.HasExited) {
                $completedAction=$script:action
                $code=$script:job.ExitCode; $script:job.Dispose(); $script:job=$null
                if($state) { $details.Text=$state | ConvertTo-Json -Depth 30 }
                if($state.ok -and $state.advisory) { $script:advisory=$state.advisory }
                if($state.ok -and $script:action -eq 'inspect') {
                    $firstDetection=-not $script:detected
                    $script:detected=$true; $detectStatus.Text='检测完成，可以选择模型'
                    if($firstDetection -and $deployRoute.Checked -and -not $script:modelReady -and $script:advisory.recommendation) {
                        $recommendedId=$script:advisory.recommendation.model_id
                        $route.SelectedIndex=switch($recommendedId){'Qwen/Qwen3.5-2B' {1} 'Qwen/Qwen3.5-0.8B' {2} default {0}}
                        $runtimeProfile.SelectedIndex=0
                    }
                    Show-SelectionAdvice
                    $hostInfo=$state.result.host
                    $lines=@("系统：Windows $($hostInfo.windows_build) / $($hostInfo.arch)","环境磁盘可用：$([Math]::Round($hostInfo.free_bytes/1GB,1)) GiB",'', '显卡：')
                    foreach($gpu in $hostInfo.gpus) { $lines+="$($gpu.name) · $([Math]::Round($gpu.memory_bytes/1GB,1)) GiB · 驱动 $($gpu.driver_version) · 计算能力 $($gpu.compute_capability)" }
                    if(-not $hostInfo.gpus) { $lines+='未检测到 NVIDIA GPU。CPU 默认候选为 Qwen3.5-0.8B FP32；仍需检查当前可用内存。' }
                    $lines+=@($script:advisory.resources.errors)
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
                    $script:activePlan=$p
                    Show-SelectionAdvice
                    $script:planReady=$true; $create.Enabled=$true; $planStatus.Text='清单已生成，可以创建新环境'
                    $decimalGB=1000000000; $modelGB=[Math]::Round($p.model_bytes/$decimalGB,2); $downloadGB=[Math]::Round($p.download_bytes/$decimalGB,2); $environmentGB=[Math]::Round($p.environment_required_bytes/$decimalGB,1)
                    $lines=@("模型：$($p.model.model_id)","依赖组合：$($p.profile)；共 $($p.dependencies.Count) 个包","模型文件大小：约 $modelGB GB（已有完整文件会复用）","基础 Python + 依赖下载：约 $downloadGB GB","新虚拟环境磁盘占用：约 $environmentGB GB","目标磁盘建议至少预留：约 $([Math]::Round($p.environment_required_bytes/1GB + $p.download_bytes/1GB + 1,1)) GiB","推理方式：$($p.hardware.device) · $($p.inference.reason)","说明：创建前还会再次检查目标磁盘空间。运行内存与显存请查看上方资源评估。")
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
                    $script:activePlan=Get-Content -LiteralPath $script:planPath -Raw -Encoding UTF8 | ConvertFrom-Json
                    Show-SelectionAdvice
                    $proceed=Confirm-ResourceAdvice
                    if($proceed -and $state.result.kind -eq 'existing') {
                        $missing=@($state.result.missing) -join "`r`n"
                        if(-not $missing) { $missing='清单依赖已齐全，将继续实际运行验证。' }
                        $answer=[Windows.Forms.MessageBox]::Show($form, "已有虚拟环境符合清单要求。`r`n待补装依赖：`r`n$missing`r`n`r`n是否在此环境基础上继续配置？", '继续配置已有环境', [Windows.Forms.MessageBoxButtons]::YesNo, [Windows.Forms.MessageBoxIcon]::Question, [Windows.Forms.MessageBoxDefaultButton]::Button2)
                        $proceed=($answer -eq [Windows.Forms.DialogResult]::Yes)
                        $script:continueExisting=$proceed
                    }
                    if($proceed) { Begin-Action 'install' } else { Return-ToInstallationPlan }
                }
                if($wizardMain -and -not $script:closing) { Complete-WizardAction $state $code $completedAction }
                if($script:closing) { $form.Close() }
            }
        }
    } catch {
        $status.Text=$_.Exception.Message; $script:download=$null; Busy $false
        if($wizardMain) { Complete-WizardAction $null 1 }
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
# The original controls keep their event handlers; the wizard gives each a single,
# contextual home. Technical output remains available in a separate detail window.
$leftPanel.Visible=$false; $rightPanel.Visible=$false
$form.ClientSize=New-Object Drawing.Size(960,680)
$form.MinimumSize=New-Object Drawing.Size(976,719)
$form.BackColor=[Drawing.Color]::FromArgb(239,237,229)
$form.ForeColor=[Drawing.Color]::FromArgb(35,35,30)
$form.Font=New-Object Drawing.Font('Microsoft YaHei UI',10)
$form.Text='诗矩 · 环境准备'
$script:wizardPage=0; $script:wizardBusy=$false; $script:wizardAuto=$false
$script:wizardPreview=$null; $script:wizardAdvanced=$false; $script:wizardFailed=$false
function Wizard-Label($parent,$text,$x,$y,$w,$h) {
    $c=New-Object Windows.Forms.Label; $c.Text=$text; $c.SetBounds($x,$y,$w,$h)
    $parent.Controls.Add($c); return $c
}
function Place-WizardControl($parent,$control,$x,$y,$w,$h) {
    $parent.Controls.Add($control); $control.Anchor='Top,Left'; $control.SetBounds($x,$y,$w,$h)
    $control.Visible=$true
}
function Set-WizardButtonStyle($button) {
    if($button.Enabled) {
        $button.BackColor=[Drawing.Color]::FromArgb(233,230,219)
        $button.ForeColor=[Drawing.Color]::FromArgb(35,35,30)
        $button.FlatAppearance.BorderColor=[Drawing.Color]::FromArgb(96,94,82)
    } else {
        $button.BackColor=[Drawing.Color]::FromArgb(216,216,212)
        $button.ForeColor=[Drawing.Color]::FromArgb(145,145,140)
        $button.FlatAppearance.BorderColor=[Drawing.Color]::FromArgb(185,185,180)
    }
}
function Show-VerificationProgress($report) {
    $labels=[ordered]@{environment='检查运行环境';load='加载模型';output='自由生成测试';constraints='约束生成测试'}
    $running=@($report.checks | Where-Object { $_.state -eq 'running' }) | Select-Object -Last 1
    $lines=@()
    foreach($key in $labels.Keys) {
        $check=@($report.checks | Where-Object { $_.id -eq $key }) | Select-Object -Last 1
        $mark=switch($check.state){'passed' {'完成'} 'running' {'进行中'} 'failed' {'失败'} default {'待进行'}}
        $lines+='['+$mark+'] '+$labels[$key]
    }
    $wizardChecks.Text=$lines -join "`r`n"
    if($running) {
        $label=$labels[$running.id]
        if(-not $label) { $label=$running.name }
        $wizardStage.Text='正在'+$label
        $seconds=[Math]::Max(0,[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()-[double]$running.started_at)
        $status.Text=$running.detail+"`r`n当前测试已用时 "+[TimeSpan]::FromSeconds($seconds).ToString('hh\:mm\:ss')
    } elseif($report.ok) {
        $wizardStage.Text='生成测试通过，正在完成最终检查'
        $status.Text='正在核对运行模块并保存环境配置，完成后即可打开诗矩。'
    }
}
$wizardSteps=Wizard-Label $form '' 28 22 900 32
$wizardSteps.Font=New-Object Drawing.Font('Microsoft YaHei UI',11,[Drawing.FontStyle]::Bold)
$wizardHeading=Wizard-Label $form '' 28 74 900 38
$wizardHeading.Font=New-Object Drawing.Font('Microsoft YaHei UI',18,[Drawing.FontStyle]::Bold)
$wizardHint=Wizard-Label $form '' 28 119 900 50
$wizardBody=New-Object Windows.Forms.Panel; $wizardBody.SetBounds(28,176,904,388)
$wizardBody.Anchor='Top,Bottom,Left,Right'; $form.Controls.Add($wizardBody)
$wizardPages=@()
foreach($n in 0..4) {
    $panel=New-Object Windows.Forms.Panel; $panel.Dock='Fill'; $panel.AutoScroll=$true
    $wizardBody.Controls.Add($panel); $wizardPages+=,$panel
}
$null=Wizard-Label $wizardPages[0] "工具会检查电脑配置，并推荐适合的模型。`r`n无需预先安装 Python 或 Conda。`r`n`r`n首次检测可能下载约 14.5 MB 的基础工具，请保持联网。" 8 16 850 130
Place-WizardControl $wizardPages[0] $resume 8 180 190 34
$null=Wizard-Label $wizardPages[0] '上次安装未完成？可恢复记录后继续；已完成的环境可直接打开。' 8 225 850 50
Place-WizardControl $wizardPages[1] $deployRoute 8 4 240 30
Place-WizardControl $wizardPages[1] $localRoute 260 4 240 30
$wizardModel=Wizard-Label $wizardPages[1] '' 8 43 535 26
Place-WizardControl $wizardPages[1] $route 8 71 535 28
Place-WizardControl $wizardPages[1] $pathCaption 8 102 535 28
Place-WizardControl $wizardPages[1] $model 8 134 390 28
Place-WizardControl $wizardPages[1] $browseModel 410 132 130 34
$null=Wizard-Label $wizardPages[1] '运行环境位置（保存 Python 和运行组件）' 8 181 535 28
Place-WizardControl $wizardPages[1] $target 8 213 390 28
Place-WizardControl $wizardPages[1] $browseTarget 410 211 130 34
$browseTarget.Text='更改位置'
$pathWarning=Wizard-Label $wizardPages[1] '' 8 250 535 30
$pathWarning.ForeColor=[Drawing.Color]::Firebrick
$null=Wizard-Label $wizardPages[1] '模型与运行环境路径不能包含中文；下载缓存使用系统盘。' 8 280 535 36
$wizardAdvancedButton=New-Object Windows.Forms.Button; $wizardAdvancedButton.Text='高级设置 / 离线工具'
Place-WizardControl $wizardPages[1] $wizardAdvancedButton 8 318 220 34
$wizardSummary=New-Object Windows.Forms.TextBox
$wizardSummary.Multiline=$true; $wizardSummary.ReadOnly=$true; $wizardSummary.ScrollBars='Vertical'
Place-WizardControl $wizardPages[1] $wizardSummary 575 8 320 344
$wizardSummary.Text='确认模型和位置后，点击“检查安装方案”。'
$wizardSummary.BorderStyle='FixedSingle'; $wizardSummary.Padding=New-Object Windows.Forms.Padding(14)
$wizardStage=Wizard-Label $wizardPages[2] '' 8 8 870 52
$wizardStage.Font=New-Object Drawing.Font('Microsoft YaHei UI',12,[Drawing.FontStyle]::Bold)
Place-WizardControl $wizardPages[2] $status 8 58 870 230
$status.Anchor='Top,Left,Right'
Place-WizardControl $wizardPages[2] $progress 8 297 870 10
$wizardChecks=Wizard-Label $wizardPages[2] '' 8 316 870 70
$wizardChecks.Font=New-Object Drawing.Font('Microsoft YaHei UI',9)
$wizardCpu=New-Object Windows.Forms.Button
$wizardCpu.Text='改用 CPU 方案'
Place-WizardControl $wizardPages[2] $wizardCpu 8 390 200 34
$wizardCpu.Visible=$false
$wizardCpu.Add_Click({
    $runtimeProfile.SelectedIndex=[Array]::IndexOf($script:profileIds,'cpu')
    # Keep the failed GPU environment intact; CPU wheels need their own target.
    if($target.Text -notlike '*-cpu') { $target.Text=$target.Text+'-cpu' }
    $script:activePlan=$null; $script:planReady=$false
    $script:wizardPage=1; Invalidate-WizardPlan
    $script:wizardPlanNotice='已切换 CPU。模型文件保留复用；请重新检查方案与内存预算。CPU 环境将使用独立目录。'
    Update-Wizard
})
$null=Wizard-Label $wizardPages[3] "运行环境与模型已通过本机生成测试。`r`n`r`n点击下方按钮打开诗矩。以后可从原解压目录的诗矩启动入口打开。`r`n请保留完整解压目录，以及当前模型和运行环境目录。" 8 20 870 150
$wizardSuccess=Wizard-Label $wizardPages[3] '' 8 190 870 150
# Advanced settings retain all maintenance capabilities without crowding first use.
$null=Wizard-Label $wizardPages[4] '运行方式（不确定时保留自动选择）' 8 4 870 28
Place-WizardControl $wizardPages[4] $runtimeProfile 8 40 535 30
Place-WizardControl $wizardPages[4] $source 8 90 400 30
$null=Wizard-Label $wizardPages[4] '离线与维护工具' 8 145 870 28
Place-WizardControl $wizardPages[4] $import 8 183 150 34
Place-WizardControl $wizardPages[4] $export 176 183 170 34
Place-WizardControl $wizardPages[4] $importWheels 364 183 180 34
Place-WizardControl $wizardPages[4] $openCache 560 183 160 34
$null=Wizard-Label $wizardPages[4] '导出安装文件和导入依赖包需要先准备模型、生成清单。普通安装会自动完成。' 8 235 870 45
$wizardPrepare=New-Object Windows.Forms.Button; $wizardPrepare.Text='准备模型与清单'
Place-WizardControl $wizardPages[4] $wizardPrepare 8 298 200 34
$wizardBack=New-Object Windows.Forms.Button; $wizardBack.Text='上一步'
Place-WizardControl $form $wizardBack 622 610 130 38
$wizardMain=New-Object Windows.Forms.Button
Place-WizardControl $form $wizardMain 770 610 162 38
Place-WizardControl $form $cancel 622 610 130 38
$cancel.Visible=$false
$wizardDetails=New-Object Windows.Forms.Button; $wizardDetails.Text='查看详情'
Place-WizardControl $form $wizardDetails 28 610 120 38
Place-WizardControl $form $startupDiagnostics 164 610 150 38
$startupDiagnostics.Text='导出诊断'
$script:controls+=@($wizardBack,$wizardAdvancedButton,$wizardPrepare,$startupDiagnostics)
foreach($c in @($wizardMain,$wizardBack,$wizardDetails,$wizardAdvancedButton,$wizardPrepare,$cancel,$resume,$browseModel,$browseTarget,$import,$export,$importWheels,$openCache,$startupDiagnostics)) {
    $c.FlatStyle='Flat'; $c.BackColor=[Drawing.Color]::FromArgb(233,230,219)
    $c.FlatAppearance.BorderColor=[Drawing.Color]::FromArgb(96,94,82)
    $c.Add_EnabledChanged({param($sender,$event) Set-WizardButtonStyle $sender })
    Set-WizardButtonStyle $c
}
$wizardMain.Font=New-Object Drawing.Font('Microsoft YaHei UI',10,[Drawing.FontStyle]::Bold)
$detailForm=New-Object Windows.Forms.Form; $detailForm.Text='诗矩 · 技术详情'
$detailForm.ClientSize=New-Object Drawing.Size(850,620); $detailForm.StartPosition='CenterParent'
$detailTabs=New-Object Windows.Forms.TabControl; $detailTabs.Dock='Fill'; $detailForm.Controls.Add($detailTabs)
foreach($entry in @(@('操作日志',$details),@('资源依据',$resourceAdvice))) {
    $tab=New-Object Windows.Forms.TabPage; $tab.Text=$entry[0]; $detailTabs.TabPages.Add($tab)
    $tab.Controls.Add($entry[1]); $entry[1].Dock='Fill'; $entry[1].Visible=$true
}
$detailForm.Add_FormClosing({param($sender,$event) if(-not $script:closingDetails) { $event.Cancel=$true; $detailForm.Hide() }})
$wizardDetails.Add_Click({ $detailForm.Show($form); $detailForm.BringToFront() })
function Invalidate-WizardPlan {
    $script:acceptedResourceAdvice=$null; $script:wizardPlanNotice=''
    $script:wizardPreview=$null; $script:wizardFailed=$false
    if($script:wizardPage -eq 3) { $script:wizardPage=1 }
    Update-Wizard
}
function Update-Wizard {
    if($script:wizardPage -eq 3 -and ($script:wizardBusy -or -not $script:verified)) { $script:wizardPage=2 }
    $page=$script:wizardPage
    $wizardCpu.Visible=($page -eq 2 -and $script:wizardFailed -and $status.Text -like '*当前 GPU 不可用*')
    foreach($n in 0..4) { $wizardPages[$n].Visible=($page -eq $n) }
    $wizardPages[$page].BringToFront()
    $step=if($page -eq 4){1}else{$page}
    $names=@('检测电脑','确认方案','安装与验证','完成')
    $wizardSteps.Text=((0..3 | ForEach-Object { if($_ -lt $step){'[完成] '+$names[$_]}elseif($_ -eq $step){'[当前] '+$names[$_]}else{$names[$_]} }) -join '    /    ')
    $wizardHeading.Text=@('先了解这台电脑','确认安装方案','正在准备诗矩','已准备就绪','高级设置与离线工具')[$page]
    $wizardHint.Text=@('点击下方“检测电脑”开始。已有部署可恢复记录。','确认模型和保存位置，检查方案后即可开始自动安装。','安装会自动进行，当前阶段与需要处理的问题显示在下方。','环境检查和真实生成验证均已完成。','默认设置适合大多数用户；修改模型或运行方式后需要重新检查方案。')[$page]
    $wizardMain.Text=switch($page){0 {'检测电脑'} 1 {if($script:wizardPreview){'开始安装'}else{'检查安装方案'}} 2 {if($script:wizardFailed){'重试当前步骤'}else{'正在处理…'}} 3 {'打开诗矩'} 4 {'返回安装方案'}}
    $wizardMain.Enabled=(-not $script:wizardBusy -and ($page -ne 2 -or $script:wizardFailed))
    $wizardBack.Visible=($page -in @(1,2,4) -and -not $script:wizardBusy)
    $wizardBack.Enabled=-not $script:wizardBusy
    $cancel.Visible=$script:wizardBusy
    $wizardModel.Text=if($deployRoute.Checked){'选择模型'}else{'请选择包含 config.json 和完整权重的本地目录。'}
    $route.Visible=$deployRoute.Checked
    $route.Refresh()
    $pathCaption.SetBounds(8,102,535,28)
    $pathCaption.Text=if($deployRoute.Checked){'模型保存位置（已有完整文件会核验后复用）'}else{'已有模型目录（不移动原文件）'}
    try { Test-DeploymentPaths; $pathWarning.Text='' } catch { $pathWarning.Text=$_.Exception.Message }
    if($script:wizardPreview) {
        $p=$script:wizardPreview
        $profileLabel=switch($p.profile){'cpu' {'CPU FP32'} 'cpu-bf16' {'CPU BF16'} 'cuda-fp16' {'GPU FP16'} 'cuda-bnb' {'GPU 4bit'} 'cuda-bnb8' {'GPU 8bit'} default {$p.profile}}
        $modelDownload=if($deployRoute.Checked){$p.model_bytes}else{0}
        $wizardSummary.Text="安装前确认`r`n`r`n模型：$($p.model.model_id)`r`n方案：$profileLabel`r`n$($script:currentAdvice.title)`r`n`r`n下载预算：约 $([Math]::Round(($p.download_bytes+$modelDownload)/1GB,1)) GiB`r`n（已有文件会复用）`r`n环境预留：约 $([Math]::Round($p.environment_required_bytes/1GB,1)) GiB`r`n`r`n相关磁盘空间检查已通过。"
    } else {
        $advice=if($script:currentAdvice){$script:currentAdvice.title}else{'请先检测电脑'}
        $wizardSummary.Text="配置建议`r`n`r`n$advice`r`n`r`n点击下方按钮检查安装方案，计算下载量并检查模型、缓存和环境所在磁盘。"
    }
    if($script:currentAdvice.notice) { $wizardSummary.Text+="`r`n`r`n"+$script:currentAdvice.notice }
    if($script:wizardPlanNotice) { $wizardHint.Text=$script:wizardPlanNotice }
    $wizardSuccess.Text="模型：$($model.Text)`r`n环境：$($target.Text)"
}
function Start-WizardAction($action) {
    $script:wizardFailed=$false; $script:wizardPage=2
    $script:wizardRetry=$action
    $wizardStage.Text=switch($action){inspect {'检查电脑配置'} preview {'检查安装方案与空间'} download {'下载并核验模型'} verify-model {'检查本地模型'} plan {'自动准备运行清单'} default {'安装与验证'}}
    try { Begin-Action $action } catch { Busy $false; $status.Text=$_.Exception.Message; $script:wizardFailed=$true }
    Update-Wizard
}
function Complete-WizardAction($state,$code,$completedAction=$script:action) {
    if($code -ne 0 -or -not $state.ok) {
        $script:wizardFailed=$true; $script:wizardAuto=$false; $script:wizardPage=2
        $wizardHeading.Text='此步骤尚未完成'
        Update-Wizard; $wizardHeading.Text='此步骤尚未完成'; return
    }
    switch($completedAction) {
        'inspect' { $script:wizardPage=1 }
        'preview' { $script:wizardPreview=$state.result; $script:wizardPage=1; Show-SelectionAdvice }
        {$_ -in @('download','verify-model')} { if($script:wizardAuto) { Start-WizardAction 'plan'; return } }
        'plan' {
            if($script:wizardAuto) {
                $script:wizardAuto=$false
                if($script:wizardPrepareOnly) { $script:wizardPrepareOnly=$false; $script:wizardPage=4 }
                else { $script:wizardRetry='install'; Request-Install; return }
            }
        }
        'install' { $script:wizardPage=3; $script:wizardAuto=$false }
        'resume' { $script:wizardPage=if($script:verified){3}else{1}; $script:wizardPreview=$null }
        'import' {
            $p=$state.result
            $importedModelPath=$model.Text
            $localRoute.Checked=$true; $model.Text=$importedModelPath
            $runtimeProfile.SelectedIndex=[Array]::IndexOf($script:profileIds,[string]$p.profile)
            $script:activePlan=$p; $script:planReady=$true; $script:modelReady=$true; $script:detected=$true
            $script:selectedModelId=$p.model.model_id; $script:selectedModelRoute=$p.route
            $script:wizardPage=1; $script:wizardPreview=$null
        }
        {$_ -in @('export','import-wheels','diagnostics')} { $script:wizardPage=4 }
    }
    Update-Wizard
}
$wizardMain.Add_Click({
    switch($script:wizardPage) {
        0 { Start-WizardAction 'inspect' }
        1 {
            if(-not $script:wizardPreview) { Start-WizardAction 'preview' }
            else {
                Show-SelectionAdvice
                if(Confirm-ResourceAdvice) {
                    $script:wizardAuto=$true; $script:wizardPrepareOnly=$false
                    Start-WizardAction $(if($deployRoute.Checked){'download'}else{'verify-model'})
                }
            }
        }
        2 {
            if($script:wizardRetry -in @('install','inspect-target')) { $script:wizardFailed=$false; Request-Install }
            else {
                $script:wizardAuto=($script:wizardRetry -in @('download','verify-model','plan'))
                Start-WizardAction $script:wizardRetry
            }
        }
        3 { Open-ShiJu }
        4 { $script:wizardPage=1; Update-Wizard }
    }
})
$wizardBack.Add_Click({ $script:wizardAuto=$false; $script:wizardPrepareOnly=$false; $script:wizardPage=if($script:wizardPage -eq 1 -or -not $script:detected){0}else{1}; Update-Wizard })
$wizardAdvancedButton.Add_Click({ $script:wizardPage=4; Update-Wizard })
$wizardPrepare.Add_Click({
    Show-SelectionAdvice
    if(Confirm-ResourceAdvice) {
        $script:wizardAuto=$true; $script:wizardPrepareOnly=$true
        Start-WizardAction $(if($deployRoute.Checked){'download'}else{'verify-model'})
    }
})
$deployRoute.Add_CheckedChanged({ Update-Wizard })
$source.Add_SelectedIndexChanged({ Invalidate-WizardPlan })
Update-Wizard
if($UiSmokeTest) {
    if($status -isnot [Windows.Forms.TextBox] -or $status.ScrollBars -ne 'Vertical' -or $status.Height -le $wizardChecks.Height) { throw '错误信息滚动区或紧凑测试列表布局不正确' }
    if($status.Parent -ne $wizardPages[2] -or $model.Parent -ne $wizardPages[1] -or $wizardPages.Count -ne 5) { throw '向导布局检查失败' }
    if($route.Parent -ne $wizardPages[1]) { throw '模型选择不在主面板' }
    $savedAdvisory=$script:advisory
    $script:advisory=[pscustomobject]@{recommendation=[pscustomobject]@{model_id='Qwen/Qwen3.5-2B'}}
    if($route.GetItemText($route.Items[1]) -ne 'Qwen3.5-2B（推荐）' -or $route.Items[1] -ne 'Qwen3.5-2B') { throw '推荐标记污染了模型身份或未显示' }
    # Keep detection advice present: switching routes triggers TextChanged before
    # the user has chosen a local directory, unlike the initial empty-advice UI.
    $beforeLocalSwitch=$model.Text
    $localRoute.Checked=$true
    $model.Text='C:\Users\26051\.cache\modelscope\hub\models\Qwen\Qwen3.5-2B'
    $script:selectedModelId='Qwen/Qwen3.5-4B'; $script:selectedModelRoute='official'
    $selection=Get-BackendModelSelection
    if($selection.route -ne 'local' -or $selection.id -ne 'Qwen/Qwen3.5-2B') { throw '本地模型错误继承下载选项或旧模型身份' }
    foreach($emptyPath in @('', '   ')) {
        $model.Text=$emptyPath
        Show-SelectionAdvice
        if($script:currentAdvice.state -ne 'unknown') { throw '空本地路径不应识别为已知模型' }
    }
    $deployRoute.Checked=$true; $model.Text=$beforeLocalSwitch
    $script:advisory=$savedAdvisory
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
    if($script:planReady -or -not $script:modelReady -or $runtimeProfile.Items.Count -ne $script:profileIds.Count) { throw '组合选择未正确失效清单或错误清除模型状态' }
    $script:planReady=$true
    $source.SelectedIndex=2
    if(-not $script:planReady) { throw '仅切换下载源不应失效清单' }
    $session=[pscustomobject]@{plan=[pscustomobject]@{route='official';profile='cuda-fp16';model=[pscustomobject]@{model_id='Qwen/Qwen3.5-0.8B'}};model='D:\Models\restored';target='D:\Envs\restored';cache=$cache}
    $null=Restore-DeploymentSession $session
    if($runtimeProfile.SelectedIndex -ne 2 -or $model.Text -ne $session.model -or $target.Text -ne $session.target -or -not $script:modelReady -or $script:selectedModelRoute -ne 'official') { throw '恢复部署未还原组合或路径' }
    $session.plan.profile='cuda-bnb8'
    $null=Restore-DeploymentSession $session
    if($runtimeProfile.SelectedIndex -ne 5 -or $runtimeProfile.Items.Count -ne 6 -or $runtimeProfile.Items[5] -ne 'GPU BNB 8bit + Triton') { throw 'BNB 8bit 记录未正确恢复' }
    $failure=[pscustomobject]@{ok=$false;error='failed';log_path='D:\reports\test.log';log_content="DLL load failed`r`nVC++ runtime missing";log_truncated=$false}
    $formatted=Format-FailureDetails $failure
    if(-not $formatted.Contains("日志内容：`r`nDLL load failed`r`nVC++ runtime missing")) { throw '失败详情未显示可读的原始日志' }
    $failure=[pscustomobject]@{ok=$false;error='failed';log_path='D:\reports\empty.log';log_content='';verification_error='当前可用 0.88 GiB，估算需要 5.73 GiB'}
    $formatted=Format-FailureDetails $failure
    if(-not $formatted.StartsWith("验证失败的具体原因：`r`n当前可用 0.88 GiB") -or -not $formatted.Contains('进程日志为空')) { throw '空日志情况下未突出显示验证报告原因' }
    # Review risk only once; returning must leave an actionable plan page.
    $script:confirmationCalls=0
    function Show-ResourceConfirmation($message) { $script:confirmationCalls++; return $script:confirmationResult }
    $script:confirmationResult=$true
    $script:currentAdvice=[pscustomobject]@{requires_confirmation=$true;model_id='test';profile='cpu';state='tight';notice='资源偏紧'}
    $script:acceptedResourceAdvice=$null
    if(-not (Confirm-ResourceAdvice) -or -not (Confirm-ResourceAdvice) -or $script:confirmationCalls -ne 1) { throw '同一风险重复确认' }
    $script:currentAdvice.state='risk'; $script:confirmationResult=$false
    $script:wizardBusy=$false
    if(Confirm-ResourceAdvice) { throw '返回操作仍继续安装' }
    if($script:wizardPage -ne 1 -or -not $wizardMain.Enabled -or -not $wizardHint.Text.Contains('尚未开始创建环境')) { throw '返回后缺少继续安装入口或说明' }
    Invalidate-WizardPlan
    if($script:acceptedResourceAdvice) { throw '方案变更未清除确认记录' }
    # Exercise the automatic chain with a fake backend; never download or install.
    function Begin-Action([string]$action) { $script:action=$action; $script:recordedAction=$action; Busy $true }
    $script:wizardAuto=$true; $script:wizardPrepareOnly=$false
    $script:action='download'; Complete-WizardAction ([pscustomobject]@{ok=$true}) 0
    if($script:recordedAction -ne 'plan') { throw '模型完成后未自动准备清单' }
    function Request-Install { $script:recordedAction='inspect-target' }
    Complete-WizardAction ([pscustomobject]@{ok=$true}) 0
    if($script:recordedAction -ne 'inspect-target' -or $script:wizardAuto) { throw '清单完成后未自动检查安装目录' }
    Complete-WizardAction ([pscustomobject]@{ok=$false}) 1
    if(-not $script:wizardFailed -or $script:wizardAuto) { throw '失败后未停止自动流程' }
    # A completed target check may already have started the install process.
    $script:wizardBusy=$true; $script:verified=$false; $script:action='install'
    Complete-WizardAction ([pscustomobject]@{ok=$true}) 0 'inspect-target'
    if($script:wizardPage -eq 3 -or $wizardMain.Enabled -or $wizardMain.Text -eq '打开诗矩') { throw '目标检查成功被误判为安装完成' }
    if($wizardMain.BackColor -ne [Drawing.Color]::FromArgb(216,216,212)) { throw '禁用按钮未显示灰色' }
    $checkTime=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()-12
    foreach($key in @('load','output','constraints')) {
        Show-VerificationProgress ([pscustomobject]@{checks=@([pscustomobject]@{id=$key;state='running';detail='正在执行测试';started_at=$checkTime})})
        $expected=switch($key){load {'加载模型'} output {'自由生成测试'} constraints {'约束生成测试'}}
        if($wizardStage.Text -notlike "*$expected*" -or $status.Text -notlike '*已用时*') { throw '未显示当前测试项目及耗时' }
    }
    $script:wizardBusy=$false; $script:verified=$true; $script:action='install'
    Complete-WizardAction ([pscustomobject]@{ok=$true}) 0
    if($script:wizardPage -ne 3 -or $wizardMain.Text -ne '打开诗矩') { throw '安装完成后未进入完成页' }
    Invalidate-WizardPlan
    if($script:wizardPage -ne 1 -or $script:wizardPreview) { throw '修改配置未撤销完成状态' }
    if($UiPreviewDirectory) {
        $null=New-Item -ItemType Directory -Force -Path $UiPreviewDirectory
        $deployRoute.Checked=$true; $route.SelectedIndex=0; $runtimeProfile.SelectedIndex=0
        $script:currentAdvice=[pscustomobject]@{title='推荐配置：符合本机硬件档位'}
        $script:wizardPreview=[pscustomobject]@{model=[pscustomobject]@{model_id='Qwen/Qwen3.5-4B'};inference=[pscustomobject]@{precision='FP16'};model_bytes=8500000000;download_bytes=4000000000;environment_required_bytes=15000000000}
        $status.Text='正在下载运行组件 · 第 3/24 个文件 · 12.4 MB/s'
        $wizardStage.Text='下载运行组件（示意）'
        $form.Show()
        foreach($page in 0..4) {
            $script:wizardPage=$page; Update-Wizard; $form.PerformLayout(); [Windows.Forms.Application]::DoEvents()
            $bitmap=New-Object Drawing.Bitmap($form.Width,$form.Height)
            $form.DrawToBitmap($bitmap,(New-Object Drawing.Rectangle(0,0,$form.Width,$form.Height)))
            $bitmap.Save((Join-Path $UiPreviewDirectory ("step-$page.png")))
            $bitmap.Dispose()
        }
    }
    $detailForm.Dispose(); $form.Dispose(); exit 0
}
if($AutoInspectReport) { $form.Add_Shown({ Begin-Action 'inspect' }) }
$timer.Start()
[Windows.Forms.Application]::Run($form)
$timer.Dispose(); $script:closingDetails=$true; $detailForm.Dispose(); $form.Dispose()
