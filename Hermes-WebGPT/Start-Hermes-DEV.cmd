@echo off
setlocal
cd /d "%~dp0"
echo [Hermes DEV] Local development launch.
echo [Hermes DEV] A4 installer acceptance gate is skipped for this run.
echo [Hermes DEV] Existing MCP permissions, keys and Windows access are unchanged.
echo.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0quickstart\start.ps1" -DevMode
set "result=%ERRORLEVEL%"
echo.
if not "%result%"=="0" echo DEV launch stopped with exit code %result%.
echo Close this window when finished reading startup status.
pause
exit /b %result%
