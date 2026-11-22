# Repeated read-only CPU/RAM benchmark for the isolated Hermes Edge profile.
# Run after arranging the workload manually (idle, 1, 2 or 5 tabs).
# No web calls, no model traffic, no local profile/message access.
[CmdletBinding()]
param(
    [ValidateSet('idle','one_active','two_active','five_active','post_completion')]
    [string]$Scenario='idle',
    [ValidateRange(3,20)][int]$Samples=5,
    [ValidateRange(5,300)][int]$SampleSeconds=15,
    [ValidateRange(0,120)][int]$WarmupSeconds=15
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$probe=Join-Path $PSScriptRoot 'Measure-Hermes-Edge-CPU.ps1'
if(-not (Test-Path -LiteralPath $probe -PathType Leaf)){
    throw 'cpu_probe_script_missing'
}
if($WarmupSeconds -gt 0){Start-Sleep -Seconds $WarmupSeconds}

$readings=New-Object 'System.Collections.Generic.List[object]'
for($i=0;$i -lt $Samples;$i++){
    # The child probe takes its own pre/post process snapshots. Error on missing
    # browser rather than silently producing a misleading near-zero baseline.
    $raw=& $probe -SampleSeconds $SampleSeconds
    $sample=$raw | ConvertFrom-Json
    $readings.Add($sample)
}
$clean=@($readings | Where-Object {$_.comparable_sample -eq $true})
function Percentile([double[]]$values,[double]$fraction){
    if($values.Count -eq 0){return $null}
    $sorted=@($values | Sort-Object)
    $index=[Math]::Max(0,[Math]::Min($sorted.Count-1,[Math]::Ceiling($fraction*$sorted.Count)-1))
    return [Math]::Round([double]$sorted[$index],3)
}
$cpu=@($clean | ForEach-Object {[double]$_.cpu_percent_of_machine})
$mem=@($clean | ForEach-Object {[double]$_.working_set_mib})
$cores=@($clean | ForEach-Object {[double]$_.cpu_equivalent_cores})
[pscustomobject]@{
    schema_version=1
    scenario=$Scenario
    repeats=$Samples
    comparable_repeats=$clean.Count
    validity=if($clean.Count -ge 3){'comparable'}else{'insufficient_stable_samples'}
    sample_seconds=$SampleSeconds
    warmup_seconds=$WarmupSeconds
    cpu_percent_machine_p50=(Percentile $cpu 0.5)
    cpu_percent_machine_p95=(Percentile $cpu 0.95)
    cpu_equivalent_cores_p50=(Percentile $cores 0.5)
    ram_working_set_mib_p50=(Percentile $mem 0.5)
    ram_working_set_mib_p95=(Percentile $mem 0.95)
    process_churn_samples=$Samples-$clean.Count
    note='No benchmark result proves improvement without same-machine before/after controls; high process churn invalidates comparisons. Resident sets may double-count shared pages.'
} | ConvertTo-Json -Compress
