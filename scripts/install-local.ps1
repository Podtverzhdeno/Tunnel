#requires -Version 5.1
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$ProjectsPath,[switch]$AllowWrite)
$ErrorActionPreference="Stop"
Set-StrictMode -Version Latest
$repo=(Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$hostRoot=Join-Path $repo "Local-Workspace-Extensions\mcp-host-source"
$bridgeRoot=Join-Path $repo "chat-launch-smoke-desktop-test\web-companion"
$cfg=Join-Path $hostRoot "config.json"
if (!(Test-Path (Join-Path $hostRoot "manage.py")) -or !(Test-Path (Join-Path $bridgeRoot "setup_bridge.py"))) { throw "Incomplete clone" }
& py -3 --version
if ($LASTEXITCODE -ne 0) { throw "Install Python 3.10+ first" }
$folder=(Resolve-Path -LiteralPath $ProjectsPath -ErrorAction Stop).Path
if (!(Test-Path -LiteralPath $folder -PathType Container)) { throw "ProjectsPath must be existing directory" }
Push-Location $hostRoot
try {
  if (!(Test-Path -LiteralPath $cfg)) {
    & py -3 manage.py init
    if ($LASTEXITCODE -ne 0) { throw "init failed" }
  }
  $raw=(& py -3 manage.py list-roots) -join [Environment]::NewLine
  if ($LASTEXITCODE -ne 0) { throw "list-roots failed" }
  $roots=@($raw | ConvertFrom-Json)
  $current=@($roots | Where-Object { $_.name -eq "projects" })
  if ($current.Count -gt 0) {
    if ([IO.Path]::GetFullPath([string]$current[0].path) -ne [IO.Path]::GetFullPath($folder)) {
      throw "projects alias already points to another directory; inspect config.json"
    }
    Write-Host "Existing projects alias unchanged"
  } else {
    $argList=@("-3","manage.py","add-root","--name","projects","--path",$folder)
    if ($AllowWrite) { $argList+=@("--write","--execute","--git") }
    & py @argList
    if ($LASTEXITCODE -ne 0) { throw "add-root failed" }
  }
  & py -3 manage.py doctor
  if ($LASTEXITCODE -ne 0) { throw "MCP doctor failed" }
} finally { Pop-Location }
Push-Location $bridgeRoot
try {
  & py -3 setup_bridge.py
  if ($LASTEXITCODE -ne 0) { throw "bridge setup failed" }
  & py -3 build_clean_extension.py
  if ($LASTEXITCODE -ne 0) { throw "extension build failed" }
} finally { Pop-Location }
Write-Host "Done. No keys were printed."
Write-Host "Start MCP: cd Local-Workspace-Extensions\mcp-host-source; py -3 manage.py serve --profile observer --port 8765"
Write-Host "Start bridge: cd chat-launch-smoke-desktop-test\web-companion; py -3 start_bridge.py"
Write-Host "Install unpacked extension folder: chat-launch-smoke-desktop-test\web-extension"
Write-Host "Create your own Secure MCP Tunnel and ChatGPT custom plugin; see README.md"
