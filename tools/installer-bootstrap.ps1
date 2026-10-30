param([string]$Request,[string]$Events,[string]$Result,[string]$DataDir,[switch]$SelfTest)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if($SelfTest) {
    if(-not (Test-Path (Join-Path $root 'tools\installer_bootstrap.py'))) { throw '缺少安装编排器。' }
    if(-not (Test-Path (Join-Path $root 'tools\installer_protocol.py'))) { throw '缺少安装协议模块。' }
    exit 0
}
$cache=Join-Path $env:LOCALAPPDATA 'ShiJu\deployment\cache'
$base=Join-Path $env:LOCALAPPDATA 'ShiJu\deployment\base-python-3.12.10'
$python=Join-Path $base 'python.exe'
if(-not (Test-Path $python)) {
    New-Item -ItemType Directory -Force -Path $cache | Out-Null
    $package=Join-Path $cache 'python.3.12.10.nupkg'
    if(-not (Test-Path $package)) {
        Invoke-WebRequest -UseBasicParsing -Uri 'https://api.nuget.org/v3-flatcontainer/python/3.12.10/python.3.12.10.nupkg' -OutFile $package
    }
    $hash=(Get-FileHash -LiteralPath $package -Algorithm SHA256).Hash.ToLowerInvariant()
    if($hash -ne '0eb85c2dfccccf1b17352de4c397f69194035b7d37149eacc16f1147d93de3b8') { throw '基础 Python 下载校验失败，请重试。' }
    $temporary=Join-Path $cache ('bootstrap-'+[Guid]::NewGuid().ToString('N'))
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::ExtractToDirectory($package,$temporary)
    if(Test-Path $base) { Remove-Item -LiteralPath $base -Recurse -Force }
    Move-Item -LiteralPath (Join-Path $temporary 'tools') -Destination $base
    [IO.File]::WriteAllText((Join-Path $base 'python312._pth'),".`nLib`nDLLs`nLib/site-packages`n$root`n",(New-Object Text.UTF8Encoding($false)))
}
$args=@('-m','tools.installer_bootstrap','--request',$Request,'--events',$Events,'--result',$Result,'--data-dir',$DataDir)
& $python $args
exit $LASTEXITCODE
