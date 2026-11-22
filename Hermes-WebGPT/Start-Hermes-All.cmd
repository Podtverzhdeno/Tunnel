@echo off
setlocal
cd /d "%~dp0"
rem A4 temporary development-first workflow: startup without production gate.
rem Use HERMES_A4_STRICT=1 to run the original strict-mode startup.
rem Never modifies Windows ACLs, MCP policy, tunnels or secrets.
if /I "%HERMES_A4_STRICT%"=="1" (
  echo [Hermes] STRICT mode: A4 production safeguards required.
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0quickstart\start.ps1"
) else (
  echo [Hermes] DEV mode: production A4 acceptance gate bypassed.
  echo [Hermes] Existing MCP privileges and Tunnel authorization stay unchanged.
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0quickstart\start.ps1" -DevMode
)
set "result=%ERRORLEVEL%"
echo.
if not "%result%"=="0" echo Launcher stopped with exit code %result%.
echo Close this window when finished reading startup status.
pause
exit /b %result%
