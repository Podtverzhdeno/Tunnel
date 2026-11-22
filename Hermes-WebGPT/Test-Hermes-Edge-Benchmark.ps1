# Runs only against synthetic CPU samples in a disposable test directory.
# Never launches Edge or accesses the user's ChatGPT profile.
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$temp=Join-Path ([IO.Path]::GetTempPath()) ('HermesEdgeCpuFixture-'+[Guid]::NewGuid().ToString('N'))
[void](New-Item -Path $temp -ItemType Directory)
try {
    $real=Join-Path $PSScriptRoot 'Benchmark-Hermes-Edge.ps1'
    $copy=Join-Path $temp 'Benchmark-Hermes-Edge.ps1'
    Copy-Item -LiteralPath $real -Destination $copy
    $fixture=@'
param([int]$SampleSeconds)
$counterFile=Join-Path $PSScriptRoot 'fixture-counter.txt'
$i=if(Test-Path -LiteralPath $counterFile){[int](Get-Content -LiteralPath $counterFile -Raw)}else{0}
Set-Content -LiteralPath $counterFile -Value ($i+1)
$values=@(10.0,20.0,50.0,90.0,110.0)
$number=$values[$i%$values.Count]
$invalid=Test-Path -LiteralPath (Join-Path $PSScriptRoot 'fixture-invalid.txt')
[pscustomobject]@{
    comparable_sample=(-not $invalid)
    cpu_percent_of_machine=$number
    cpu_equivalent_cores=($number/20)
    working_set_mib=($number+200)
} | ConvertTo-Json -Compress
'@
    $probe=Join-Path $temp 'Measure-Hermes-Edge-CPU.ps1'
    Set-Content -LiteralPath $probe -Value $fixture -Encoding UTF8
    $report=(& $copy -Scenario one_active -Samples 5 -SampleSeconds 5 -WarmupSeconds 0) | ConvertFrom-Json
    if($report.validity -ne 'comparable' -or $report.comparable_repeats -ne 5){
        throw 'fixture_unexpected_validity'
    }
    if($report.cpu_percent_machine_p50 -ne 50 -or $report.cpu_percent_machine_p95 -ne 110){
        throw 'fixture_bad_cpu_percentiles'
    }
    if($report.ram_working_set_mib_p50 -ne 250 -or $report.ram_working_set_mib_p95 -ne 310){
        throw 'fixture_bad_ram_percentiles'
    }
    [void](New-Item -Path (Join-Path $temp 'fixture-invalid.txt') -ItemType File)
    $notComparable=(& $copy -Scenario idle -Samples 3 -SampleSeconds 5 -WarmupSeconds 0) | ConvertFrom-Json
    if($notComparable.validity -ne 'insufficient_stable_samples' -or
       $notComparable.comparable_repeats -ne 0 -or
       $notComparable.process_churn_samples -ne 3){
        throw 'fixture_failed_to_reject_unstable_samples'
    }
    Write-Output 'PASS: five-sample p50/p95 and unstable-sample fail-closed benchmark'
} finally {
    if(Test-Path -LiteralPath $temp){
        Remove-Item -LiteralPath $temp -Recurse -Force
    }
}
