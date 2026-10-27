param(
    [ValidateSet('Check','Install')][string]$Mode='Check',
    [string]$ReportPath='',
    [string]$InstallerPath='',
    [string]$ExpectedSha256='',
    [switch]$ExitOnFailure
)
$ErrorActionPreference='Stop'
function Test-VCRuntime {
    $problems=@(); $version=''
    $registry=[Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine,[Microsoft.Win32.RegistryView]::Registry64)
    try {
        $key=$registry.OpenSubKey('SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64')
        if($key) {
            try { $version=[string]$key.GetValue('Version',''); $installed=$key.GetValue('Installed',0) }
            finally { $key.Dispose() }
            if($installed -ne 1 -or -not $version -or [version]$version.TrimStart('v') -lt [version]'14.40') { $problems+='x64 VC++ v14 runtime missing or older than 14.40' }
        } else { $problems+='x64 VC++ v14 runtime registration missing' }
    } finally { $registry.Dispose() }
    if(-not ('ShiJu.NativeRuntime' -as [type])) {
        Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; namespace ShiJu { public static class NativeRuntime { [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] public static extern IntPtr LoadLibraryEx(string path, IntPtr file, uint flags); [DllImport("kernel32.dll")] public static extern bool FreeLibrary(IntPtr handle); } }'
    }
    foreach($name in @('vcruntime140.dll','vcruntime140_1.dll','msvcp140.dll')) {
        $path=Join-Path ([Environment]::GetFolderPath('System')) $name
        $handle=[ShiJu.NativeRuntime]::LoadLibraryEx($path,[IntPtr]::Zero,0x00000800)
        if($handle -eq [IntPtr]::Zero) { $problems+=$name+' load failed (Win32 '+[Runtime.InteropServices.Marshal]::GetLastWin32Error()+')' }
        else { $null=[ShiJu.NativeRuntime]::FreeLibrary($handle) }
    }
    return @{ok=($problems.Count -eq 0); version=$version; problems=@($problems); restart_required=$false}
}
try {
    $result=Test-VCRuntime
    if($Mode -eq 'Install') {
        if(-not $InstallerPath) {
            $pin=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'vc-runtime.json') -Raw -Encoding UTF8 | ConvertFrom-Json
            $InstallerPath=Join-Path $PSScriptRoot $pin.filename
            $ExpectedSha256=$pin.sha256
            if(-not (Test-Path -LiteralPath $InstallerPath) -or (Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash -ne $ExpectedSha256) {
                [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
                $client=New-Object Net.WebClient
                try { $client.DownloadFile($pin.source_url,($InstallerPath+'.part')) }
                finally { $client.Dispose() }
                if((Get-FileHash -LiteralPath ($InstallerPath+'.part') -Algorithm SHA256).Hash -ne $ExpectedSha256) { throw 'Downloaded VC++ installer SHA256 mismatch' }
                Move-Item -LiteralPath ($InstallerPath+'.part') -Destination $InstallerPath -Force
            }
        }
        if(-not $InstallerPath -or -not $ExpectedSha256) { throw 'Installer path and pinned SHA256 are required' }
        if((Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash -ne $ExpectedSha256) { throw 'VC++ installer SHA256 mismatch' }
        $signature=Get-AuthenticodeSignature -LiteralPath $InstallerPath
        if($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '(?:^|,\s*)CN=Microsoft Corporation(?:,|$)') { throw 'VC++ installer must have a valid Microsoft Corporation signature' }
        $process=Start-Process -FilePath $InstallerPath -ArgumentList '/install /passive /norestart' -Verb RunAs -WindowStyle Hidden -Wait -PassThru
        $code=$process.ExitCode
        if($code -notin @(0,1638,3010)) { throw "VC++ installer failed: exit code $code" }
        $result=Test-VCRuntime
        $result.restart_required=($code -eq 3010)
        $result.installer_exit_code=$code
    }
} catch { $result=@{ok=$false;problems=@($_.Exception.Message);restart_required=$false} }
$json=$result | ConvertTo-Json -Depth 5
if($ReportPath) { [IO.File]::WriteAllText($ReportPath,$json,(New-Object Text.UTF8Encoding($false))) }
Write-Output $json
if($ExitOnFailure -and (-not $result.ok -or $result.restart_required)) { exit 1 }
