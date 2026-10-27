param([string]$TargetPath='', [string]$CondaPath='', [switch]$Resume, [ValidateSet('auto','mirror','official')][string]$Source='auto')
$ErrorActionPreference='Stop'
$env:PYTHONIOENCODING='utf-8'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
$root=$PSScriptRoot
$logRoot=Join-Path $root ('logs\'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'-'+[Guid]::NewGuid().ToString('N').Substring(0,8))
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$stage='准备'
$verified=$false
$installLock=$null
$exitCode=1
function Save-State([string]$state,[string]$message='') {
    $record=@{state=$state;ok=($state -eq 'dependencies-checked');stage=$script:stage;target=$script:TargetPath;message=$message;logs=$script:logRoot;updated_at=[DateTimeOffset]::Now.ToString('o')}
    [IO.File]::WriteAllText((Join-Path $root 'installation-state.json'),($record | ConvertTo-Json),(New-Object Text.UTF8Encoding($false)))
}
function Run-Stage([string]$name,[string]$command,[string[]]$commandArgs) {
    $script:stage=$name
    Save-State 'running'
    Write-Host $name
    if(-not(Test-Path -LiteralPath $command)) { throw "找不到程序：$command" }
    $ErrorActionPreference='Continue'
    $writer=New-Object IO.StreamWriter((Join-Path $logRoot ($name+'.log')),$false,(New-Object Text.UTF8Encoding($false)))
    $writer.AutoFlush=$true
    try {
        & $command @commandArgs 2>&1 | ForEach-Object { $line=$_.ToString(); $writer.WriteLine($line); Write-Host $line }
    } finally { $writer.Dispose() }
    if($LASTEXITCODE -ne 0) { throw "$name 失败（退出码 $LASTEXITCODE），请查看 logs。可保留环境后使用 -Resume 重试。" }
}
try {
    if([Environment]::Is64BitOperatingSystem -ne $true) { throw '仅支持 Windows x64。' }
    $stage='00-vc-runtime-check'
    $vcReport=Join-Path $logRoot 'vc-runtime-check.json'
    & (Join-Path $root 'ensure-vc-runtime.ps1') -ReportPath $vcReport | Out-Null
    $vc=Get-Content -LiteralPath $vcReport -Raw -Encoding UTF8 | ConvertFrom-Json
    if(-not $vc.ok) {
        Write-Host '缺少 VC++ x64 运行库，正在自动安装；请允许系统权限提示。'
        & (Join-Path $root 'ensure-vc-runtime.ps1') -Mode Install -ReportPath $vcReport | Out-Null
        $vc=Get-Content -LiteralPath $vcReport -Raw -Encoding UTF8 | ConvertFrom-Json
        if($vc.restart_required) { throw 'VC++ 运行库要求重启 Windows，请重启后重试部署。' }
        if(-not $vc.ok) { throw ('VC++ x64 运行库自动安装未完成；可重试并允许权限提示，或双击 install-vc-runtime.cmd 手动安装。详情：'+($vc.problems -join '; ')) }
    }
    if(-not $TargetPath) {
        Add-Type -AssemblyName System.Windows.Forms
        $picker=New-Object Windows.Forms.FolderBrowserDialog
        $picker.Description='选择保存父目录；将在其中新建 ShiJu-conda 环境（至少预留 30 GiB）'
        if($picker.ShowDialog() -ne 'OK') { throw '用户已取消' }
        $TargetPath=Join-Path $picker.SelectedPath 'ShiJu-conda'
        $picker.Dispose()
    }
    if(-not [IO.Path]::IsPathRooted($TargetPath)) { throw '环境路径必须是绝对路径。' }
    $TargetPath=[IO.Path]::GetFullPath($TargetPath).TrimEnd('\')
    if($TargetPath.Length -lt 4) { throw '不能把磁盘根目录用作环境。' }
    foreach($value in @($TargetPath,$root)) { if($value.Contains('"') -or $value.Contains("`n")) { throw '路径无效' } }
    if($root.StartsWith($TargetPath+'\',[StringComparison]::OrdinalIgnoreCase)) { throw '环境不能包含导出目录。' }
    if(-not $CondaPath) {
        $found=Get-Command conda.exe -ErrorAction SilentlyContinue
        if($found) { $CondaPath=$found.Source }
        foreach($candidate in @($env:CONDA_EXE,"$env:ProgramData\anaconda3\Scripts\conda.exe","$env:USERPROFILE\anaconda3\Scripts\conda.exe","$env:USERPROFILE\miniconda3\Scripts\conda.exe","$env:USERPROFILE\miniforge3\Scripts\conda.exe")) {
            if(-not $CondaPath -and $candidate -and (Test-Path -LiteralPath $candidate)) { $CondaPath=$candidate }
        }
    }
    if(-not $CondaPath -or -not(Test-Path -LiteralPath $CondaPath)) { throw '未发现 Conda。可安装 Conda 后重试，或使用诗矩部署窗口第四步，无需安装 Conda。' }
    $parent=Split-Path -Parent $TargetPath
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $installLock=[IO.File]::Open(($TargetPath+'.shiju-conda.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
    $marker=$TargetPath+'.shiju-conda.json'
    $planHash=(Get-FileHash -LiteralPath (Join-Path $root 'plan.json') -Algorithm SHA256).Hash
    if(Test-Path -LiteralPath $TargetPath) {
        if(-not $Resume -or -not(Test-Path -LiteralPath $marker)) { throw '环境目录已存在；只有本脚本的失败环境可以加 -Resume 恢复。' }
        $previous=Get-Content -LiteralPath $marker -Raw -Encoding UTF8 | ConvertFrom-Json
        if($previous.plan_hash -ne $planHash) { throw '半成品属于另一份清单，请选择新目录。' }
    }
    # A conservative budget includes Conda bootstrap, pip cache and extraction.
    foreach($path in @($parent,$root)) {
        $drive=New-Object IO.DriveInfo([IO.Path]::GetPathRoot($path))
        if($drive.AvailableFreeSpace -lt 30GB) { throw "磁盘空间不足：$($drive.Name) 当前可用 $([Math]::Round($drive.AvailableFreeSpace/1GB,1)) GiB，建议至少 30 GiB。" }
    }
    [IO.File]::WriteAllText($marker,(@{plan_hash=$planHash;target=$TargetPath} | ConvertTo-Json))
    $env:PIP_CACHE_DIR=Join-Path $root 'pip-cache'
    $env:CONDA_PKGS_DIRS=Join-Path $root 'conda-cache'
    $env:PYTHONNOUSERSITE='1'
    Remove-Item Env:PYTHONHOME,Env:PYTHONPATH -ErrorAction SilentlyContinue
    $python=Join-Path $TargetPath 'python.exe'
    if(-not(Test-Path -LiteralPath (Join-Path $TargetPath 'conda-meta\history'))) {
        if(Test-Path -LiteralPath $TargetPath) { throw 'Conda 引导未完成且目录已存在；请保留日志，选择新的空目录。' }
        Run-Stage '01-conda-create' $CondaPath @('env','create','--yes','--prefix',$TargetPath,'--file',(Join-Path $root 'conda-bootstrap.yml'))
    }
    Run-Stage '02-download-locked-wheels' $CondaPath @('run','--no-capture-output','--prefix',$TargetPath,'python',(Join-Path $root 'download-dependencies.py'),'--source',$Source)
    Run-Stage '02-install-locked-wheels' $CondaPath @('run','--no-capture-output','--prefix',$TargetPath,'python','-m','pip','--isolated','install','--no-index','--find-links',(Join-Path $root 'wheels'),'--require-hashes','--only-binary=:all:','-r',(Join-Path $root 'requirements-offline.lock'),'--report',(Join-Path $root 'pip-install-report.json'))
    Run-Stage '03-check-dependencies' $CondaPath @('run','--no-capture-output','--prefix',$TargetPath,'python',(Join-Path $root 'check-environment.py'))
    Run-Stage '04-freeze-conda' $CondaPath @('list','--prefix',$TargetPath,'--explicit')
    Copy-Item -LiteralPath (Join-Path $logRoot '04-freeze-conda.log') -Destination (Join-Path $root 'conda-explicit.txt')
    Run-Stage '05-freeze-pip' $CondaPath @('run','--no-capture-output','--prefix',$TargetPath,'python','-m','pip','freeze','--all')
    Copy-Item -LiteralPath (Join-Path $logRoot '05-freeze-pip.log') -Destination (Join-Path $root 'pip-freeze.txt')
    $verified=$true
    Save-State 'dependencies-checked' '依赖安装及一致性检查通过；尚需在诗矩中验证 GPU 和真实模型生成。'
    Write-Host '依赖环境已创建。请在诗矩启动器选择此环境的 python.exe，再验证模型。'
    $exitCode=0
} catch {
    Save-State 'failed' $_.Exception.Message
    Write-Host $_.Exception.Message -ForegroundColor Red
} finally {
    if($installLock) { $installLock.Dispose() }
}
exit $exitCode
