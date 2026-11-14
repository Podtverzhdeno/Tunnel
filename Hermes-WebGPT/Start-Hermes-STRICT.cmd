@echo off
setlocal
cd /d "%~dp0"
echo [Hermes] Strict startup: production A4 checks enabled.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0quickstart\start.ps1"
set "result=%ERRORLEVEL%"
echo.
if not "%result%"=="0" echo Strict startup stopped with exit code %result%.
pause
exit /b %result%
