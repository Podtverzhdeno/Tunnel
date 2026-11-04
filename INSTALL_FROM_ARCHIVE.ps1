#requires -Version 5.1
<#
.SYNOPSIS
  Prepare the portable Tunnel distribution on your own Windows PC.
.DESCRIPTION
  Offline preflight + local setup. No account login, key transfer,
  tunnel registration, automatic chat messages or Windows elevation.
#>
[CmdletBinding()]
param(
    [string]$ProjectsPath = (Join-Path $env:USERPROFILE "AgentProjects"),
    [switch]$AllowWrite,
    [switch]$SkipPreflight
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$root = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$verify = Join-Path $root "scripts\verify-portable-install.py"
$installer = Join-Path $root "scripts\install-local.ps1"
$doctor = Join-Path $root "scripts\status-stack.py"

if (!(Test-Path -LiteralPath $verify -PathType Leaf) -or
    !(Test-Path -LiteralPath $installer -PathType Leaf) -or
    !(Test-Path -LiteralPath $doctor -PathType Leaf)) {
    throw "This ZIP is incomplete. Download a fresh official distribution from the project repository."
}
& py -3 --version
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.10+ and the py launcher are required."
}
if (!$SkipPreflight) {
    Write-Host "Step 1/3: offline integrity and clean-install checks..."
    & py -3 $verify --test-powershell
    if ($LASTEXITCODE -ne 0) { throw "Portable preflight did not pass." }
}
Write-Host "Step 2/3: creating user workspace if absent..."
if (!(Test-Path -LiteralPath $ProjectsPath)) {
    New-Item -ItemType Directory -Force -Path $ProjectsPath | Out-Null
}
$actual = (Resolve-Path -LiteralPath $ProjectsPath).Path
if (!(Test-Path -LiteralPath $actual -PathType Container)) {
    throw "ProjectsPath is not a directory."
}
$params = @{
    ProjectsPath = $actual
}
if ($AllowWrite) { $params.AllowWrite = $true }

Write-Host "Step 3/3: setting up personal, local-only secrets..."
& $installer @params
if ($LASTEXITCODE -ne 0) {
    throw "Local installation failed."
}
Write-Host ""
Write-Host "Local setup complete. Run the following FROM THE ROOT OF YOUR EXTRACTED TUNNEL FOLDER:"
Write-Host "  py -3 scripts\observer_auth.py serve --port 8765"
Write-Host "  cd chat-launch-smoke-desktop-test\web-companion; py -3 start_bridge.py"
Write-Host "  py -3 scripts\status-stack.py"
Write-Host "Then load the UNPACKED extension folder and create your own MCP Tunnel/App."
Write-Host "Get the API-key/Tunnel-ID instructions from README.md and docs\ARCHIVE_INSTALL_RU.md"
Write-Host "This installer has not connected any ChatGPT account, Platform project or browser session."
