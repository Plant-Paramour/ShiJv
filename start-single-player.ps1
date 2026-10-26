param([switch]$AllowUnverified, [switch]$DirectStart, [switch]$CheckOnly, [switch]$Headless, [switch]$UiSmokeTest, [switch]$EmptyEnvironments, [string]$ModelPath, [string]$ApplicationPython, [string]$InferencePython, [switch]$EnableLocal, [string]$DataDir, [int]$Port = 8000)
& (Join-Path $PSScriptRoot "launcher.ps1") @PSBoundParameters
