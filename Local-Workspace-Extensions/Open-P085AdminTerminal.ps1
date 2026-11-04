# P085 UAC handoff: opens a CLEAN administrator PowerShell only.
# NEVER run elevated code from IdeaProjects: broad Local Workspace MCP can
# mutate any script there. User must independently install a trusted,
# immutable bundle before authorizing elevated provisioning operations.
[CmdletBinding()]
param()
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest

$principal=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host 'You are already elevated. Do NOT execute unreviewed scripts from the MCP workspace as administrator.'
    return
}
if ([Console]::IsInputRedirected -or [Console]::IsOutputRedirected) {
    throw 'P085_OWNER_INTERACTIVE_DESKTOP_REQUIRED'
}
$ps=Join-Path $PSHOME 'powershell.exe'
if (-not (Test-Path -LiteralPath $ps -PathType Leaf)) {
    throw 'P085_POWERSHELL_NOT_FOUND'
}
try {
    # No -File pointing to the mutable project, no auto-install, no credentials.
    Start-Process -FilePath $ps -Verb RunAs -ArgumentList '-NoLogo -NoProfile -NoExit' -ErrorAction Stop | Out-Null
} catch {
    throw 'P085_UAC_DECLINED_OR_UNAVAILABLE'
}
Write-Host 'Elevated console opened via Windows UAC; no setup script has run.'
