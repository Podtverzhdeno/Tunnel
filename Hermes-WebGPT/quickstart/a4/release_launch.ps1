# Stable A4 entrypoint. An active release must verify before any execution.
# Fallback to the bundled source GUI only when there is no update pointer.
[CmdletBinding()]
param([switch]$SmokeTest,[switch]$ResolveOnly)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$project=(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$defaultScript=Join-Path $PSScriptRoot 'setup.ps1'
$state=Join-Path $env:LOCALAPPDATA 'Hermes-OneClick'
$pointer=Join-Path $state 'a4-active-release.json'
$releaseRoot=$null
$selected=$defaultScript
$releaseActive=$false
try {
    if (Test-Path -LiteralPath $pointer) {
        # Never silently fall back to an older/unsigned code path after a
        # corrupted release. The owner must reconcile the active pointer.
        if (-not (Test-Path -LiteralPath $pointer -PathType Leaf)) {
            throw 'a4_release_pointer_not_file'
        }
        $python=Get-Command python.exe -ErrorAction SilentlyContinue
        if ($null -eq $python) {
            throw 'a4_release_python_verifier_missing'
        }
        Push-Location $project
        try {
            $resultLines=@(& $python.Source -m quickstart.a4.release_manager resolve --state $state 2>$null)
            $exit=$LASTEXITCODE
        } finally { Pop-Location }
        if ($exit -ne 0 -or $resultLines.Count -lt 1) {
            throw 'a4_release_integrity_verification_failed'
        }
        $result=$resultLines[-1] | ConvertFrom-Json
        if ($result.ok -ne $true -or $result.release_active -ne $true) {
            throw 'a4_release_pointer_unresolved'
        }
        if ($result.version -notmatch '^v[0-9]+\.[0-9]+\.[0-9]+([-.][A-Za-z0-9_-]{1,24})?$') {
            throw 'a4_release_invalid_version'
        }
        $root=Join-Path $state ('a4-releases\'+[string]$result.version)
        $expected=Join-Path $root 'quickstart\a4\setup.ps1'
        $actualRoot=(Resolve-Path -LiteralPath $root -ErrorAction Stop).Path
        $actualScript=(Resolve-Path -LiteralPath $expected -ErrorAction Stop).Path
        if ($actualRoot -ne [string]$result.release_root -or
            $actualScript -ne [string]$result.script) {
            throw 'a4_release_path_mismatch'
        }
        $selected=$actualScript
        $releaseRoot=$actualRoot
        $releaseActive=$true
    }
    if ($ResolveOnly) {
        # Testable, read-only selection; no GUI, no network and no service.
        [pscustomobject]@{
            ok=$true; release_active=$releaseActive
            selected=$selected; source_project=$project
        } | ConvertTo-Json -Compress
        exit 0
    }
    $prior=$env:HERMES_A4_PROJECT_ROOT
    try {
        if ($releaseActive) {
            # This release channel updates the GUI while the existing vetted
            # Python/Relay runtime remains in the checkout. Never import an
            # incomplete or unapproved runtime package from the GUI bundle.
            $env:HERMES_A4_PROJECT_ROOT=$project
        }
        Push-Location $project
        try {
            $argsList=@('-NoLogo','-NoProfile','-STA','-ExecutionPolicy','Bypass','-File',$selected)
            if ($SmokeTest) { $argsList+= '-SmokeTest' }
            & powershell.exe @argsList
            if ($LASTEXITCODE -ne 0) { throw 'a4_gui_process_failed' }
        } finally { Pop-Location }
    } finally {
        if ($null -eq $prior) {
            Remove-Item Env:HERMES_A4_PROJECT_ROOT -ErrorAction SilentlyContinue
        } else {
            $env:HERMES_A4_PROJECT_ROOT=$prior
        }
    }
    exit 0
} catch {
    [Console]::Error.WriteLine('Hermes A4: release verification failed; GUI not launched.')
    exit 2
}
