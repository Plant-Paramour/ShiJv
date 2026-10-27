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
function Read-Probe([string]$Path) {
    $request=[Net.HttpWebRequest]::Create("http://127.0.0.1:$Port$Path")
    $request.Proxy=$null; $request.Timeout=4000; $request.ReadWriteTimeout=4000
    $request.Headers['Cache-Control']='no-cache'
    $response=$null
    try { $response=$request.GetResponse() } catch {
        $response=$_.Exception.Response
        if (-not $response) { return @{path=$Path; error=$_.Exception.Message} }
    }
    try {
        $stream=$response.GetResponseStream(); $reader=New-Object IO.StreamReader($stream)
        try { $buffer=New-Object char[] 262144; $length=$reader.ReadBlock($buffer,0,$buffer.Length); $body=New-Object string($buffer,0,$length) } finally { $reader.Dispose() }
        return @{path=$Path; status=[int]$response.StatusCode; content_type=$response.ContentType; cache_control=$response.Headers['Cache-Control']; etag=$response.Headers['ETag']; body=$body}
    } finally { $response.Dispose() }
}
$record=@{schema=1; time=(Get-Date).ToString('o'); workspace=$Workspace; data_root=$DataDirectory; port=$Port; package_fingerprint=(Get-LauncherBuildFingerprint $Workspace); probes=@()}
$health=Read-Probe '/health/live'
$record.health=$health
try {
    if ($health.status -eq 200) {
        $service=ConvertFrom-Json $health.body
        if ($service.application -eq 'ShiJu' -and $service.workspace -and [IO.Directory]::Exists($service.workspace)) {
            $record.service_disk_fingerprint=Get-LauncherBuildFingerprint $service.workspace
            $record.running_build=$service.build
            $record.version_matches=($service.build -and $service.build.fingerprint -eq $record.package_fingerprint)
        }
    }
} catch { $record.version_check_error=$_.Exception.Message }
$html=Read-Probe '/web/prosody-checker/?startup-diagnostic=1'
$moduleUrls=@('/web/prosody-checker/site.js','/web/prosody-checker/app.js','/web/prosody-checker/sentence-search.js')
if ($html.body) {
    $record.html_module_urls=@([regex]::Matches($html.body,'<script[^>]+src="([^"]+\.js[^" ]*)"') | ForEach-Object { $_.Groups[1].Value })
    $record.html_release=@([regex]::Matches($html.body,'name="shiju-release" content="([^"]+)"') | ForEach-Object { $_.Groups[1].Value })
}
$html.Remove('body'); $record.probes+=,$html
foreach ($url in $moduleUrls) {
    $probe=Read-Probe ($url+'?startup-diagnostic=1')
    if ($probe.status -ne 200 -and $probe.body) { $probe.error_body=$probe.body.Substring(0,[Math]::Min(400,$probe.body.Length)) }
    $probe.Remove('body'); $record.probes+=,$probe
}
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
        if ($Name.EndsWith('.json')) { $Content=(Protect-DiagnosticObject (ConvertFrom-Json $Content)) | ConvertTo-Json -Depth 30 }
        else { $Content=Protect-DiagnosticText $Content }
        $writer.Write($Content)
    } finally { $writer.Dispose() }
}
try {
    Write-Entry 'launcher.json' ($record | ConvertTo-Json -Depth 12)
    foreach ($name in @('api-startup.log','api-bootstrap.stderr.log','api-bootstrap.stdout.log','startup-diagnostics\latest-browser.json')) {
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
    Write-Entry '说明.txt' '包含应用版本、启动日志和资源响应；未读取数据库、对话或模型密钥配置。日志最多保留各文件末尾 256 KiB。网页独立导出的 JSON 如未同步，可一起发送。'
} finally { $archive.Dispose(); $stream.Dispose() }
Write-Output $OutputPath
