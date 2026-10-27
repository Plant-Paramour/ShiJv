@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0ensure-vc-runtime.ps1" -Mode Install -ExitOnFailure -ReportPath "%~dp0vc-runtime-check.json"
set "RUNTIME_EXIT=%ERRORLEVEL%"
pause
exit /b %RUNTIME_EXIT%
