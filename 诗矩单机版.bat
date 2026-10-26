@echo off
setlocal
powershell.exe -NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0launcher.ps1"
if errorlevel 1 pause
