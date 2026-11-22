# Read-only process-lifetime observer for a dedicated Hermes Edge profile.
# Intended for live human-triggered reproductions of browser disappearance.
# It does not inspect tabs, websites, cookies, profiles or any message text.
# Sampling has overhead; NEVER use this script as the CPU benchmark.
[CmdletBinding()]
param(
    [ValidateRange(10,600)][int]$Seconds=90,
    [ValidateRange(2,10)][int]$EverySeconds=2
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$local=[Environment]::GetFolderPath('LocalApplicationData')
$profile=Join-Path (Join-Path $local 'Hermes-WebGPT') 'EdgeTemporaryProfile'
$start=[DateTime]::UtcNow
$stopwatch=[Diagnostics.Stopwatch]::StartNew()
$records=New-Object 'System.Collections.Generic.List[object]'
$tracked=New-Object 'System.Collections.Generic.HashSet[int]'
$last=''
$transitions=0
while($stopwatch.Elapsed.TotalSeconds -lt $Seconds){
    try{
        $processes=@(Get-CimInstance -ClassName Win32_Process -Filter "Name = 'msedge.exe'")
    }catch{
        throw 'unable_to_enumerate_edge_processes'
    }
    $roots=@($processes | Where-Object {
        $_.CommandLine -and $_.CommandLine.IndexOf($profile,[StringComparison]::OrdinalIgnoreCase) -ge 0
    })
    $rootIds=@($roots | ForEach-Object {[int]$_.ProcessId} | Sort-Object -Unique)
    # Capture tracked root disappearance even when the profile has no windows.
    foreach($rootPid in $rootIds){[void]$tracked.Add($rootPid)}
    $missing=@($tracked | Where-Object {$rootIds -notcontains $_})
    $signature=($rootIds -join ',')+'|'+($missing -join ',')
    if($signature -ne $last){
        $state=if($rootIds.Count -eq 0){
            if($tracked.Count -eq 0){'not_yet_seen'}else{'profile_process_disappeared'}
        }elseif($missing.Count -gt 0){
            'profile_process_replaced_or_exited'
        }else{'profile_process_visible'}
        $parentPids=@($roots | ForEach-Object {[int]$_.ParentProcessId} | Sort-Object -Unique)
        $records.Add([pscustomobject]@{
            offset_seconds=[Math]::Round($stopwatch.Elapsed.TotalSeconds,3)
            utc=[DateTime]::UtcNow.ToString('o')
            state=$state
            root_pids=$rootIds
            parent_pids=$parentPids
            tracked_root_count=$tracked.Count
            all_edge_process_count=$processes.Count
        })
        $transitions++
        $last=$signature
    }
    Start-Sleep -Seconds $EverySeconds
}
[pscustomobject]@{
    schema_version=1
    duration_seconds=[Math]::Round($stopwatch.Elapsed.TotalSeconds,3)
    started_utc=$start.ToString('o')
    transition_count=$transitions
    profile_label='Hermes isolated Edge (path redacted)'
    note='Only process visibility is observed; cannot distinguish tab close, browser crash or Windows job-object kill without event/PID correlation.'
    transitions=@($records)
} | ConvertTo-Json -Depth 6
