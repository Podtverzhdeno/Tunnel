# Long-lived, isolated, interactive Edge process for Hermes.
# No linkage between the Edge process lifetime and a Hermes model turn.
# Run in the Windows user's foreground session, NOT as a short-lived tool.
[CmdletBinding()]
param([ValidateRange(0,65535)][int]$DebugPort=0)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$Url='https://chatgpt.com/?temporary-chat=true'
if($DebugPort -gt 0 -and $DebugPort -lt 1024){throw 'debug_port_out_of_range'}
$local=[Environment]::GetFolderPath('LocalApplicationData')
if([string]::IsNullOrWhiteSpace($local)){throw 'local_app_data_unavailable'}
$root=Join-Path $local 'Hermes-WebGPT'
$profile=Join-Path $root 'EdgeTemporaryProfile'
if(-not (Test-Path -LiteralPath $profile -PathType Container)){
    [void](New-Item -ItemType Directory -Path $profile -Force)
}
$candidates=@()
$pf=[Environment]::GetEnvironmentVariable('ProgramFiles')
$pf86=[Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
foreach($prefix in @($pf,$pf86,$local)){
    if($prefix){$candidates+=Join-Path $prefix 'Microsoft\Edge\Application\msedge.exe'}
}
$browser=@($candidates | Where-Object {Test-Path -LiteralPath $_ -PathType Leaf} | Select-Object -First 1)
if($browser.Count -ne 1){throw 'edge_binary_not_found'}

# An existing isolated profile may contain a live temporary conversation.
# Do not unexpectedly replace that page or launch a second Edge profile owner.
$all=@(Get-CimInstance -ClassName Win32_Process -Filter "Name = 'msedge.exe'")
$owned=@($all | Where-Object {
    $_.CommandLine -and $_.CommandLine.IndexOf($profile,[StringComparison]::OrdinalIgnoreCase) -ge 0
})
if($owned.Count -gt 0){
    [pscustomobject]@{
        status='already_running'; pid=[int]$owned[0].ProcessId
        new_window=$false; isolated_profile=$true; url=$Url
        temporary_ui_verified=$false
    } | ConvertTo-Json -Compress
    exit 0
}
if($DebugPort -ne 0){
    # Optional, localhost CDP only, blocked if anything else owns this port.
    $listener=New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback,$DebugPort)
    try{$listener.Start()}catch{throw 'cdp_port_unavailable'}
    finally{$listener.Stop()}
}
$args=@('--new-window','--no-first-run','--no-default-browser-check',
        ('--user-data-dir="' + $profile + '"'))
if($DebugPort -ne 0){
    $args+='--remote-debugging-address=127.0.0.1'
    $args+=('--remote-debugging-port=' + $DebugPort)
}
$args+=$Url
# Deliberately do not disable sandbox, site isolation, SmartScreen, or GPU.
# An isolated new profile starts without user's everyday extensions.
$p=Start-Process -FilePath $browser[0] -ArgumentList $args -WorkingDirectory $root -PassThru
[pscustomobject]@{
    status='launch_requested'; pid=[int]$p.Id; isolated_profile=$true
    new_window=$true; url=$Url; remote_debugging=($DebugPort -ne 0)
    temporary_ui_verified=$false; browser_survival_verified=$false
} | ConvertTo-Json -Compress
# Neither a URL parameter nor a launch_requested response proves Temporary UI
# or that the Edge process survived. Check both before sending any messages.
