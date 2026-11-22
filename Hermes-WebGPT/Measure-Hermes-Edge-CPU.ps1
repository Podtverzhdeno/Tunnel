# Read-only bounded CPU sampler for the isolated Hermes Edge process family.
# No remote debugging, admin rights or browser profile content access.
[CmdletBinding()]
param([ValidateRange(5,300)][int]$SampleSeconds=15)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$local=[Environment]::GetFolderPath('LocalApplicationData')
$profile=Join-Path (Join-Path $local 'Hermes-WebGPT') 'EdgeTemporaryProfile'
function Snapshot {
    $all=@(Get-CimInstance -ClassName Win32_Process -Filter "Name = 'msedge.exe'")
    $roots=@($all | Where-Object {
        $_.CommandLine -and $_.CommandLine.IndexOf($profile,[StringComparison]::OrdinalIgnoreCase) -ge 0
    } | ForEach-Object {[int]$_.ProcessId})
    if($roots.Count -eq 0){throw 'hermes_edge_profile_not_running'}
    $ids=New-Object 'System.Collections.Generic.HashSet[int]'
    foreach($rootPid in $roots){[void]$ids.Add($rootPid)}
    # Renderer/GPU workers often omit --user-data-dir; include descendants.
    $changed=$true
    while($changed){
        $changed=$false
        foreach($item in $all){
            if($ids.Contains([int]$item.ParentProcessId) -and $ids.Add([int]$item.ProcessId)){
                $changed=$true
            }
        }
    }
    $result=@()
    foreach($pidValue in $ids){
        # A renderer may terminate between the CIM snapshot and process read.
        try {
            $proc=Get-Process -Id $pidValue -ErrorAction SilentlyContinue
            if($proc){
                $result += [pscustomobject]@{
                    pid=$pidValue; cpu_seconds=[double]$proc.CPU
                    start_ticks=[long]$proc.StartTime.Ticks
                    working_set_bytes=[long]$proc.WorkingSet64
                }
            }
        } catch [System.Exception] { continue }
    }
    return $result
}
$before=@(Snapshot)
$timer=[Diagnostics.Stopwatch]::StartNew()
Start-Sleep -Seconds $SampleSeconds
$timer.Stop()
$after=@(Snapshot)
$lookup=@{}
foreach($row in $before){$lookup[[string]$row.pid]=$row}
$delta=0.0
$newCount=0
foreach($row in $after){
    $old=$lookup[[string]$row.pid]
    if($old -and $old.start_ticks -eq $row.start_ticks){
        $delta += [Math]::Max(0,$row.cpu_seconds-$old.cpu_seconds)
    } else {$newCount++}
}
$elapsed=[Math]::Max(0.001,$timer.Elapsed.TotalSeconds)
$cores=[Math]::Max(1,[Environment]::ProcessorCount)
$memory=0.0
foreach($row in $after){$memory += $row.working_set_bytes}
[pscustomobject]@{
    sample_seconds=[Math]::Round($elapsed,3); process_count=$after.Count
    cpu_equivalent_cores=[Math]::Round($delta/$elapsed,3)
    cpu_percent_of_machine=[Math]::Round($delta/$elapsed/$cores*100,2)
    working_set_mib=[Math]::Round($memory/1MB,1)
    new_or_restarted_processes=$newCount
    note='Approximation: CPU used by processes created during sample omitted.'
} | ConvertTo-Json -Compress
