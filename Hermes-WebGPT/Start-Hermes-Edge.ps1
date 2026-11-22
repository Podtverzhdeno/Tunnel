#requires -Version 5.1
<#
.SYNOPSIS
  Start a dedicated low-noise Microsoft Edge instance for Hermes Web Companion.
.DESCRIPTION
  Does not stop existing browsers, copy browser profiles, automate login,
  clear sessions, install extensions, change policies, or enable CDP by default.
  PowerShell 5.1+ and Windows 10/11 are supported.
#>
[CmdletBinding()]
param(
    [switch]$EnableLocalCDP,
    [ValidateRange(1024,65535)][int]$CDPPort = 9223,
    [switch]$ConfigureHermesExecutable
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $IsWindows -and $PSVersionTable.PSVersion.Major -ge 6) {
    throw 'Windows-only Edge launcher.'
}
if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    throw 'LOCALAPPDATA is missing; no profile can be isolated.'
}
$roots = @(
    $env:ProgramFiles,
    [Environment]::GetEnvironmentVariable('ProgramFiles(x86)'),
    $env:LOCALAPPDATA
) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
$edge = $null
foreach ($root in $roots) {
    $candidate = Join-Path $root 'Microsoft\Edge\Application\msedge.exe'
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        $edge = (Resolve-Path -LiteralPath $candidate).ProviderPath
        break
    }
}
if (-not $edge) { throw 'Microsoft Edge executable not found in standard installation paths.' }

# Separate Hermes browser data from normal personal Edge profiles.
$profile = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'HermesEdgeWorker'))
$standardProfile = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Microsoft\Edge\User Data'))
if ($profile.TrimEnd('\') -ieq $standardProfile.TrimEnd('\')) {
    throw 'Refusing to automate the default personal Edge profile.'
}
New-Item -ItemType Directory -Path $profile -Force | Out-Null

# Only inspect processes; NEVER kill or change a user's browser process.
try {
    $edgeProcesses = @(Get-CimInstance Win32_Process -Filter "Name = 'msedge.exe'" -ErrorAction Stop)
} catch {
    throw 'Cannot verify Edge process ownership. Browser launch aborted without side effects.'
}
if (@($edgeProcesses | Where-Object { -not $_.CommandLine }).Count -gt 0) {
    throw 'Some Edge command lines are unreadable; cannot safely deduplicate isolated profile.'
}
$owned = @($edgeProcesses | Where-Object {
    $_.CommandLine -and $_.CommandLine.IndexOf($profile,[StringComparison]::OrdinalIgnoreCase) -ge 0
})
if ($owned.Count -gt 0) {
    Write-Output ('Hermes Edge profile already running. Matching processes: ' + $owned.Count)
    Write-Output ('Profile: ' + $profile)
    Write-Output 'No browser relaunch or process termination was attempted.'
    if ($EnableLocalCDP) {
        Write-Warning 'Cannot enable CDP on an already-running profile. Validate its existing settings manually.'
    }
    if ($ConfigureHermesExecutable) { $env:AGENT_BROWSER_EXECUTABLE_PATH = $edge }
    return
}

$argsList = @(
    ('--user-data-dir="' + $profile + '"'),
    '--no-first-run',
    '--no-default-browser-check',
    '--new-window'
)
if ($EnableLocalCDP) {
    $existingPort = Get-NetTCPConnection -State Listen -LocalPort $CDPPort -ErrorAction SilentlyContinue
    if ($existingPort) { throw "CDP port $CDPPort already in use; refusing to attach to an unknown process." }
    Write-Warning 'CDP allows broad control of this dedicated profile. Never forward the debugging port.'
    $argsList += '--remote-debugging-address=127.0.0.1'
    $argsList += "--remote-debugging-port=$CDPPort"
}
$argsList += 'about:blank'
$started = Start-Process -FilePath $edge -ArgumentList ($argsList -join ' ') -PassThru
if ($ConfigureHermesExecutable) { $env:AGENT_BROWSER_EXECUTABLE_PATH = $edge }

Write-Output ('Launched Edge bootstrap PID: ' + $started.Id)
Write-Output ('Dedicated user-data-dir: ' + $profile)
Write-Output 'No task tabs, authentication data or model requests were created by this script.'
Write-Output 'Install Web Companion only in this dedicated Edge profile and log in manually.'
Write-Output 'A returned process PID does NOT prove that the browser stayed open or that CDP is healthy.'
