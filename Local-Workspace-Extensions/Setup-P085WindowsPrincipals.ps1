# P085 optional local account provisioning, OWNER-FOREGROUND ONLY.
# Dry-run by default. -Apply creates MISSING local non-admin identities
# and disables them pending separately supervised cutover. Never modifies
# current user's permissions, current MCP, tunnel, queue or service processes.
# Credentials are typed into secure Windows prompts, never parameters/logs.
[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$ServiceName = 'p085svc',
    [string]$ReviewerName = 'p085review',
    [string]$WorkerName = 'p085worker',
    [string]$QASupervisorName = 'p085qasup',
    [string]$QAChildName = 'p085qachild'
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest

$roles=[ordered]@{
    service = $ServiceName
    reviewer = $ReviewerName
    untrusted_worker = $WorkerName
    qa_supervisor = $QASupervisorName
    qa_test_child = $QAChildName
}
$names=@($roles.Values)
foreach ($value in $names) {
    if ($value -cnotmatch '^[a-zA-Z][a-zA-Z0-9_-]{3,23}$') {
        throw 'INVALID_LOCAL_ACCOUNT_NAME'
    }
}
if ((@($names | Select-Object -Unique)).Count -ne 5) {
    throw 'ACCOUNT_IDENTITIES_MUST_BE_DISTINCT'
}
if (-not (Get-Command Get-LocalUser -ErrorAction SilentlyContinue)) {
    throw 'WINDOWS_LOCALACCOUNTS_MODULE_REQUIRED'
}
$adminIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$adminPrincipal = [Security.Principal.WindowsPrincipal]$adminIdentity
$administrators = @(Get-LocalGroupMember -SID 'S-1-5-32-544' -ErrorAction Stop |
    ForEach-Object { $_.SID.Value })
$entries=@()
foreach ($role in $roles.Keys) {
    $name=$roles[$role]
    $found=Get-LocalUser -Name $name -ErrorAction SilentlyContinue
    if ($null -ne $found -and $administrators -contains $found.SID.Value) {
        throw 'EXISTING_ACCOUNT_IS_LOCAL_ADMIN'
    }
    $entries+= [pscustomobject]@{
        role=$role
        account=$name
        state=$(if ($null -eq $found) {'MISSING'} else {'EXISTS'})
        sid=$(if ($null -eq $found) {$null} else {$found.SID.Value})
        disabled=$(if ($null -eq $found) {$null} else {!$found.Enabled})
    }
}
$known=@($entries | Where-Object {$null -ne $_.sid} | ForEach-Object {$_.sid})
if ((@($known | Select-Object -Unique)).Count -ne $known.Count) {
    throw 'ACCOUNT_SID_COLLISION'
}
if (-not $Apply) {
    [pscustomobject]@{
        schema='p085-local-principals-plan-v1'
        dry_run=$true
        creates_only_missing_accounts=$true
        all_new_accounts_disabled_until_owner_cutover=$true
        does_not_start_services_or_change_project_acls=$true
        effective_os_token_isolation_verified=$false
        accounts=$entries
    } | ConvertTo-Json -Depth 6
    return
}
if (-not $adminPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'ELEVATED_WINDOWS_TERMINAL_REQUIRED'
}
if ([Console]::IsInputRedirected -or [Console]::IsOutputRedirected) {
    throw 'FOREGROUND_INTERACTIVE_OWNER_REQUIRED'
}
Write-Host 'This creates ONLY missing DISABLED local non-admin accounts.' -ForegroundColor Yellow
Write-Host 'It does not modify your existing MCP account, project ACL or service.'
foreach ($item in $entries) {
    Write-Host ('  '+$item.role+': '+$item.account+' ('+$item.state+')')
}
$ack=Read-Host 'Type exactly CREATE-P085-DISABLED-ACCOUNTS to continue'
if ($ack -cne 'CREATE-P085-DISABLED-ACCOUNTS') { throw 'OWNER_DECLINED' }
foreach ($item in $entries) {
    if ($item.state -eq 'EXISTS') { continue }
    $password=Read-Host -Prompt ('New password for '+$item.account) -AsSecureString
    try {
        New-LocalUser -Name $item.account -Password $password -Disabled -Description ('P085 isolated '+$item.role+' identity') -ErrorAction Stop | Out-Null
    } finally {
        # No plaintext password ever stored, printed, sent to a process
        # or written to the project.
        $password=$null
    }
    Disable-LocalUser -Name $item.account -ErrorAction Stop
    $created=Get-LocalUser -Name $item.account -ErrorAction Stop
    if ($created.Enabled) { throw 'NEW_ACCOUNT_NOT_DISABLED' }
}
$final=@()
foreach ($item in $entries) {
    $current=Get-LocalUser -Name $item.account -ErrorAction Stop
    $final+= [pscustomobject]@{
        role=$item.role; account=$item.account
        sid=$current.SID.Value; disabled=!$current.Enabled
    }
}
if ((@($final | ForEach-Object {$_.sid} | Select-Object -Unique)).Count -ne 5) {
    throw 'POST_CREATE_SID_COLLISION'
}
[pscustomobject]@{
    schema='p085-local-principals-created-v1'
    no_service_started=$true
    no_main_project_acl_changed=$true
    os_isolation_tests_completed=$false
    requires_owner_to_enable_and_provision_logon_rights=$true
    accounts=$final
} | ConvertTo-Json -Depth 6
Write-Host 'Accounts prepared. Do not enable P085 until real OS cross-principal access and QA isolation tests pass.' -ForegroundColor Yellow
