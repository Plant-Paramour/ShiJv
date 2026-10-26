@echo off
setlocal
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0install-conda.ps1" %*
set "INSTALL_EXIT=%ERRORLEVEL%"
if not "%INSTALL_EXIT%"=="0" echo Installation failed. See logs and installation-state.json in this folder.
pause
exit /b %INSTALL_EXIT%
