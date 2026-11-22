# Runs the extension-free MCP/CDP worker against the EXISTING Hermes Relay.
# Requires a dedicated Yandex CDP browser already signed in to one ChatGPT chat.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ChatUrl,
    [int]$CdpPort=9223,
    [int]$RelayPort=18765,
    [int]$TimeoutSeconds=300
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if ($ChatUrl -cnotmatch '^https://chatgpt[.]com/c/[a-z0-9-]+$') { throw 'invalid_chat_url' }
if ($CdpPort -lt 1024 -or $CdpPort -gt 65535 -or
    $RelayPort -lt 1024 -or $RelayPort -gt 65535) { throw 'invalid_port' }
if ($TimeoutSeconds -lt 30 -or $TimeoutSeconds -gt 900) { throw 'invalid_timeout' }
$root=$PSScriptRoot
$python=Join-Path $root '.venv-cdp\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'cdp_python_missing; install isolated Playwright dependency first'
}
$auth=Join-Path $root 'data\relay-auth.json'
$workerAuth=Join-Path $root 'data\playwright-worker-auth.json'
if (-not (Test-Path -LiteralPath $auth -PathType Leaf) -or
    -not (Test-Path -LiteralPath $workerAuth -PathType Leaf)) {
    throw 'existing_relay_playwright_authorization_missing'
}
# Read-only route verification; never restart an active Relay.
$clientKey=(Get-Content -LiteralPath $auth -Raw -Encoding UTF8 | ConvertFrom-Json).key
$workerKey=(Get-Content -LiteralPath $workerAuth -Raw -Encoding UTF8 | ConvertFrom-Json).key
if ([string]$clientKey -eq '' -or [string]$workerKey -eq '') { throw 'relay_auth_invalid' }
$models=Invoke-RestMethod -Method Get -Uri ("http://127.0.0.1:"+$RelayPort+"/v1/models") `
    -Headers @{Authorization=('Bearer '+$clientKey)} -TimeoutSec 4
$cdpModel=@($models.data | Where-Object { $_.id -eq 'web-chatgpt-high-cdp' })
if ($cdpModel.Count -ne 1) { throw 'existing_relay_playwright_route_not_enabled' }
$version=Invoke-RestMethod -Method Get -Uri ("http://127.0.0.1:"+$CdpPort+"/json/version") -TimeoutSec 4
if (-not $version.Browser) { throw 'cdp_browser_not_ready' }
$base=Join-Path $env:LOCALAPPDATA 'Hermes-WebCDP'
$journal=Join-Path $base 'journal'
if (-not (Test-Path -LiteralPath $base)) { [void](New-Item -ItemType Directory -Path $base) }
if (-not (Test-Path -LiteralPath $journal)) { [void](New-Item -ItemType Directory -Path $journal) }
$priorEnable=$env:HERMES_CDP_LIVE_ENABLE
$priorKey=$env:HERMES_CDP_LIVE_WORKER_KEY
try {
    $env:HERMES_CDP_LIVE_ENABLE='1'
    $env:HERMES_CDP_LIVE_WORKER_KEY=[string]$workerKey
    Push-Location -LiteralPath $root
    try {
        & $python -m hermes_cdp_mcp_sidecar --relay ("http://127.0.0.1:"+$RelayPort) `
            --cdp ("http://127.0.0.1:"+$CdpPort) --chat $ChatUrl `
            --journal $journal --timeout-seconds $TimeoutSeconds
        if ($LASTEXITCODE -ne 0) { throw 'live_cdp_worker_stopped_with_error' }
    } finally { Pop-Location }
} finally {
    $env:HERMES_CDP_LIVE_ENABLE=$priorEnable
    $env:HERMES_CDP_LIVE_WORKER_KEY=$priorKey
}
