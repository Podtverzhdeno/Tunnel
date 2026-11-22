#requires -Version 5.1
<#
.SYNOPSIS
  Read-only Edge/Hermes diagnostic with profile-specific process-tree CPU sampling.
.DESCRIPTION
  Does not print browser command lines, URLs, cookies, tokens or chat contents.
  Does not start, close, pause or attach to a browser. Run before and after
  reproducing the window disappearance. It cannot replace a live UI E2E test.
#>
[CmdletBinding()]
param(
  [ValidateRange(1,60)][int]$SampleSeconds = 5
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
  throw 'Windows only.'
}
if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
  throw 'LOCALAPPDATA unavailable.'
}
$profile = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'HermesEdgeWorker'))
function Get-EdgeProcesses {
  return @(Get-CimInstance Win32_Process -Filter "Name = 'msedge.exe'" -ErrorAction Stop)
}
function Get-OwnedIds([object[]]$rows, [string]$targetProfile) {
  $ids = [System.Collections.Generic.HashSet[int]]::new()
  foreach ($r in $rows) {
    if ($r.CommandLine -and
        $r.CommandLine.IndexOf($targetProfile,[StringComparison]::OrdinalIgnoreCase) -ge 0) {
      [void]$ids.Add([int]$r.ProcessId)
    }
  }
  # Child process command lines may not contain --user-data-dir.
  $changed = $true
  while ($changed) {
    $changed = $false
    foreach ($r in $rows) {
      if ($ids.Contains([int]$r.ParentProcessId) -and
          $ids.Add([int]$r.ProcessId)) { $changed = $true }
    }
  }
  return ,@($ids)
}
function Take-Sample([int[]]$pids) {
  $measure = @{}
  foreach ($pidNumber in $pids) {
    try {
      $p = Get-Process -Id $pidNumber -ErrorAction Stop
      if ($null -ne $p.CPU) {
        $measure[[int]$pidNumber] = @{
          cpu_seconds = [double]$p.CPU
          working_set = [double]$p.WorkingSet64
        }
      }
    } catch { }
  }
  return $measure
}
$firstRows = @(Get-EdgeProcesses)
$initialPids = @(Get-OwnedIds $firstRows $profile)
$first = Take-Sample $initialPids
Start-Sleep -Seconds $SampleSeconds
$lastRows = @(Get-EdgeProcesses)
$finalPids = @(Get-OwnedIds $lastRows $profile)
$last = Take-Sample $finalPids
$shared = @($first.Keys | Where-Object { $last.ContainsKey($_) })
$cpuDelta = 0.0
foreach ($key in $shared) {
  $delta = [double]$last[$key].cpu_seconds - [double]$first[$key].cpu_seconds
  if ($delta -gt 0) { $cpuDelta += $delta }
}
$workingSet = 0.0
foreach ($key in $last.Keys) { $workingSet += [double]$last[$key].working_set }
$crashEvents = @()
try {
  $since = (Get-Date).AddHours(-2)
  foreach ($row in @(Get-WinEvent -FilterHashtable @{
      LogName='Application'; Id=1000,1001; StartTime=$since
    } -ErrorAction SilentlyContinue)) {
    if ($row.Message -and $row.Message.IndexOf('msedge.exe',
         [StringComparison]::OrdinalIgnoreCase) -ge 0) {
      $crashEvents += @{time=$row.TimeCreated.ToUniversalTime().ToString('o');
                        event_id=$row.Id; provider=$row.ProviderName}
    }
  }
} catch { }
@{
  schema_version=1
  observed_at_utc=[DateTime]::UtcNow.ToString('o')
  profile_kind='HermesEdgeWorker'
  initial_process_count=$initialPids.Count
  final_process_count=$finalPids.Count
  final_process_ids=@($finalPids)
  browser_disappeared_during_sample=($initialPids.Count -gt 0 -and $finalPids.Count -eq 0)
  sample_seconds=$SampleSeconds
  cpu_percent_of_total_logical_capacity=[math]::Round(
     100.0 * $cpuDelta / ($SampleSeconds * [Environment]::ProcessorCount),2)
  working_set_megabytes=[math]::Round($workingSet/1MB,1)
  matching_windows_crash_events=@($crashEvents)
  limitations='CPU only for identified process tree; PID reuse, process churn, GPU/OS load and externally owned Edge instances require separate verification.'
} | ConvertTo-Json -Depth 7
