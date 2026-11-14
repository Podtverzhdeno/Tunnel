@echo off
setlocal
cd /d "%~dp0"
REM A4 candidate: opens the Windows GUI, does not bypass MCP permission enforcement.
powershell.exe -NoLogo -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0quickstart\a4\release_launch.ps1"
if errorlevel 1 (
  echo Hermes A4 setup encountered an error.
  pause
)
endlocal
