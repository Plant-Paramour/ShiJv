@echo off
setlocal
cd /d "%~dp0"
title ShiJu Environment Setup
if not exist "%~dp0tools\environment-tool-v2.ps1" (
    echo [ERROR] Setup files are missing. Please extract the complete ShiJu package first.
    pause
    exit /b 2
)
set "SHIJU_SETUP_ENTRY=%~dp0tools\environment-tool-v2.ps1"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -WindowStyle Hidden -Command "$exe=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'; Start-Process -FilePath $exe -WindowStyle Hidden -ArgumentList @('-NoProfile','-STA','-ExecutionPolicy','Bypass','-File',('"'+$env:SHIJU_SETUP_ENTRY+'"'))"
exit /b %errorlevel%
