# Opens a dedicated, manually authenticated Yandex Chromium profile for MCP/CDP.
# No Web Companion, no cookie copying, no Relay/Gateway restart.
[CmdletBinding()]
param([int]$Port = 9223)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($Port -lt 1024 -or $Port -gt 65535) { throw 'invalid_cdp_port' }
$browser=Join-Path $env:LOCALAPPDATA 'Yandex\YandexBrowser\Application\browser.exe'
if (-not (Test-Path -LiteralPath $browser -PathType Leaf)) {
    throw 'yandex_browser_executable_missing'
}
$root=Join-Path $env:LOCALAPPDATA 'Hermes-WebCDP'
$profile=Join-Path $root 'BrowserProfile'
if (-not (Test-Path -LiteralPath $root)) {
    [void](New-Item -ItemType Directory -Path $root)
}
if (-not (Test-Path -LiteralPath $profile)) {
    [void](New-Item -ItemType Directory -Path $profile)
}
$client=New-Object Net.Sockets.TcpClient
try {
    $task=$client.ConnectAsync('127.0.0.1',$Port)
    $occupied=$false
    try { $occupied=$task.Wait(250) -and $client.Connected }
    catch [AggregateException] { $occupied=$false }
    if ($occupied) { throw 'cdp_port_already_in_use_or_unknown_browser' }
} finally {
    $client.Dispose()
}
$args=@(
    "--remote-debugging-address=127.0.0.1",
    "--remote-debugging-port=$Port",
    "--user-data-dir=`"$profile`"",
    '--disable-extensions',
    '--no-first-run',
    'https://chatgpt.com/'
)
Start-Process -FilePath $browser -ArgumentList $args -WorkingDirectory $root
Write-Output 'CDP browser launched. Sign in manually, then open one https://chatgpt.com/c/... conversation.'
Write-Output 'No message has been sent; Start-Hermes-MCP-Web.ps1 starts the separate Relay worker.'
