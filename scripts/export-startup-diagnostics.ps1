param([Parameter(Mandatory=$true)][string]$Workspace,[Parameter(Mandatory=$true)][string]$DataDirectory,[int]$Port=18763,[string]$OutputPath='')
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.IO.Compression
. (Join-Path $Workspace 'scripts\launcher-instance.ps1')
if (-not $OutputPath) {
    Add-Type -AssemblyName System.Windows.Forms
    $picker=New-Object Windows.Forms.SaveFileDialog
    $picker.Filter='启动诊断 (*.zip)|*.zip'; $picker.FileName='诗矩启动诊断.zip'
    if ($picker.ShowDialog() -ne 'OK') { $picker.Dispose(); return }
    $OutputPath=$picker.FileName; $picker.Dispose()
}
function Protect-DiagnosticText([string]$Value) {
    $Value=[regex]::Replace($Value,'(?i)(Bearer\s+)[\w.\-]+','$1[redacted]')
    $Value=[regex]::Replace($Value,'\bsk-[A-Za-z0-9_-]+','[redacted]')
    $Value=[regex]::Replace($Value,'(?i)((?:api[_-]?key|authorization|access[_-]?token|password|secret)["\s]*[:=]["\s]*)[^\s,";}]+','$1[redacted]')
    return [regex]::Replace($Value,'(https?://[^\s?"<>]+)\?[^\s"<>]+','$1?[redacted]')
}
$record=@{schema=2; time=(Get-Date).ToString('o'); workspace=$Workspace; data_root=$DataDirectory; port=$Port; package_fingerprint=(Get-LauncherBuildFingerprint $Workspace); source='persisted-files'; service_record=$null}
$serviceFile=Join-Path $DataDirectory 'startup-diagnostics\service.json'
if (Test-Path -LiteralPath $serviceFile -PathType Leaf) {
    try {
        $service=Get-Content -LiteralPath $serviceFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $record.service_record=$service
        $record.version_matches=($service.build -and $service.build.fingerprint -eq $record.package_fingerprint)
    } catch { $record.service_record_error=$_.Exception.Message }
} else { $record.service_record_error='服务未留下启动身份记录；请查看启动器及进程日志。' }
$stream=[IO.File]::Open($OutputPath,[IO.FileMode]::Create,[IO.FileAccess]::Write,[IO.FileShare]::None)
$archive=New-Object IO.Compression.ZipArchive($stream,[IO.Compression.ZipArchiveMode]::Create,$false)
function Protect-DiagnosticObject($Value) {
    if ($null -eq $Value) { return $null }
    if ($Value -is [string]) { return Protect-DiagnosticText $Value }
    if ($Value -is [System.Collections.IDictionary]) {
        $clean=@{}; foreach($key in $Value.Keys) { $clean[$key]=Protect-DiagnosticObject $Value[$key] }; return $clean
    }
    if ($Value -is [pscustomobject]) {
        $clean=@{}; foreach($property in $Value.PSObject.Properties) { $clean[$property.Name]=Protect-DiagnosticObject $property.Value }; return $clean
    }
    if ($Value -is [array]) { return ,@($Value | ForEach-Object { Protect-DiagnosticObject $_ }) }
    return $Value
}
function Write-Entry([string]$Name,[string]$Content) {
    $entry=$archive.CreateEntry($Name)
    $writer=New-Object IO.StreamWriter($entry.Open(),(New-Object Text.UTF8Encoding($false)))
    try {
        if ($Name.EndsWith('.json')) {
            try { $Content=(Protect-DiagnosticObject (ConvertFrom-Json $Content)) | ConvertTo-Json -Depth 30 }
            catch { $Content=Protect-DiagnosticText $Content }
        }
        else { $Content=Protect-DiagnosticText $Content }
        $writer.Write($Content)
    } finally { $writer.Dispose() }
}
try {
    Write-Entry 'launcher.json' ($record | ConvertTo-Json -Depth 12)
    foreach ($name in @('api-startup.log','api-bootstrap.stderr.log','api-bootstrap.stdout.log','startup-diagnostics\latest-browser.json','startup-diagnostics\service.json')) {
        $path=Join-Path $DataDirectory $name
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            $file=[IO.File]::Open($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::ReadWrite)
            try {
                $null=$file.Seek([Math]::Max(0,$file.Length-262144),[IO.SeekOrigin]::Begin)
                $reader=New-Object IO.StreamReader($file)
                try { $content=$reader.ReadToEnd() } finally { $reader.Dispose() }
                Write-Entry ([IO.Path]::GetFileName($path)) $content
            } finally { $file.Dispose() }
        }
    }
    $exportIndex = @()
    $budget = 32MB
    foreach ($base in @((Join-Path $DataDirectory 'diagnostics'), (Join-Path $env:LOCALAPPDATA 'ShiJu\deployment\reports'), (Join-Path ([IO.Path]::GetTempPath()) 'ShiJu\diagnostics'))) {
        if (Test-Path -LiteralPath $base -PathType Container) {
            foreach ($item in @(Get-ChildItem -LiteralPath $base -Recurse -File | Where-Object { $_.Extension -in @('.json','.log','.1','.2','.3') -and -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) } | Sort-Object LastWriteTime -Descending | Select-Object -First 600)) {
                if ($budget -le 0) { break }
                $take = [Math]::Min([Math]::Min($item.Length,262144),$budget)
                $file = [IO.File]::Open($item.FullName,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::ReadWrite)
                try {
                    $null = $file.Seek([Math]::Max(0,$file.Length-$take),[IO.SeekOrigin]::Begin)
                    $reader = New-Object IO.StreamReader($file)
                    try { $content=$reader.ReadToEnd() } finally { $reader.Dispose() }
                } finally { $file.Dispose() }
                $entryName = 'operations/' + [IO.Path]::GetFileName($base) + '/' + $item.FullName.Substring($base.TrimEnd('\').Length+1).Replace('\','/')
                # A truncated JSON tail is text, not a standalone JSON document.
                if ($item.Length -gt $take) { $entryName += '.tail.txt' }
                Write-Entry $entryName $content
                $exportIndex += @{name=$entryName; path=$item.FullName; truncated=($item.Length -gt $take)}
                $budget -= $take
            }
        }
    }
    Write-Entry 'diagnostic-export-index.json' (@{files=$exportIndex; byte_limit=33554432} | ConvertTo-Json -Depth 10)
    Write-Entry '说明.txt' '包含版本、启动、环境检查、生成器及部署日志；未读取数据库、对话或模型密钥配置。单文件保留末尾 256 KiB，截断信息见 diagnostic-export-index.json。'
} finally { $archive.Dispose(); $stream.Dispose() }
Write-Output $OutputPath
