# P085 protected Windows reviewer package — OWNER-ONLY INSTALL STAGING.
# Dry run by default. -Apply ONLY creates a protected, SHA-pinned code stage.
# NO USERS, SERVICE, KEYS, LIVE MCP CHANGES, OR MERGE ACTIVATION.
# Run in a genuine elevated foreground Windows PowerShell terminal, NOT MCP.
[CmdletBinding()]
param(
  [switch]$Apply,
  [string]$ExpectedPlanSha256,
  [Parameter(Mandatory=$true)][string]$ServiceAccount,
  [Parameter(Mandatory=$true)][string]$ReviewerAccount,
  [Parameter(Mandatory=$true)][string]$WorkerAccount,
  [string]$Destination = (Join-Path $env:ProgramData 'P085ProtectedReviewer')
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Resolve-PrincipalSid([string]$Account) {
  try {
    $identity = New-Object System.Security.Principal.NTAccount($Account)
    return $identity.Translate([System.Security.Principal.SecurityIdentifier]).Value
  } catch { throw 'ACCOUNT_NOT_FOUND_OR_UNRESOLVED' }
}
function Test-Admin {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = [Security.Principal.WindowsPrincipal]$identity
  return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
function Protected-DirectoryAcl([string]$ServiceSid, [switch]$PrivateState) {
  # Immutable code: SYSTEM-only write. Private state: SYSTEM and the
  # distinct broker service SID may write. Reviewer/worker are pipe peers.
  $acl = New-Object System.Security.AccessControl.DirectorySecurity
  $acl.SetAccessRuleProtection($true,$false)
  $system = New-Object System.Security.Principal.SecurityIdentifier('S-1-5-18')
  $acl.SetOwner($system)
  $grants = @(
    [pscustomobject]@{sid='S-1-5-18';rights=[System.Security.AccessControl.FileSystemRights]::FullControl},
    [pscustomobject]@{sid='S-1-5-32-544';rights=[System.Security.AccessControl.FileSystemRights]::ReadAndExecute}
  )
  if ($PrivateState) {
    $grants += [pscustomobject]@{sid=$ServiceSid;rights=[System.Security.AccessControl.FileSystemRights]::Modify}
  } else {
    $grants += [pscustomobject]@{sid=$ServiceSid;rights=[System.Security.AccessControl.FileSystemRights]::ReadAndExecute}
  }
  foreach ($grant in $grants) {
    $user = New-Object System.Security.Principal.SecurityIdentifier($grant.sid)
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
      $user,$grant.rights,
      [System.Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit',
      [System.Security.AccessControl.PropagationFlags]::None,
      [System.Security.AccessControl.AccessControlType]::Allow)
    $acl.AddAccessRule($rule)
  }
  return $acl
}
function Protected-FileAcl([string]$ServiceSid) {
  $acl = New-Object System.Security.AccessControl.FileSecurity
  $acl.SetAccessRuleProtection($true,$false)
  $system = New-Object System.Security.Principal.SecurityIdentifier('S-1-5-18')
  $acl.SetOwner($system)
  foreach ($grant in @(
    [pscustomobject]@{sid='S-1-5-18';rights=[System.Security.AccessControl.FileSystemRights]::FullControl},
    [pscustomobject]@{sid='S-1-5-32-544';rights=[System.Security.AccessControl.FileSystemRights]::ReadAndExecute},
    [pscustomobject]@{sid=$ServiceSid;rights=[System.Security.AccessControl.FileSystemRights]::ReadAndExecute}
  )) {
    $identity=New-Object System.Security.Principal.SecurityIdentifier($grant.sid)
    $rule=New-Object System.Security.AccessControl.FileSystemAccessRule(
      $identity,$grant.rights,
      [System.Security.AccessControl.AccessControlType]::Allow)
    $acl.AddAccessRule($rule)
  }
  return $acl
}
function Assert-DirectoryAcl([string]$Path,[string]$ServiceSid,[bool]$PrivateState) {
  $acl=Get-Acl -LiteralPath $Path
  if ($acl.AreAccessRulesProtected -ne $true) { throw 'PROTECTED_DACL_REQUIRED' }
  if ($acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -cne 'S-1-5-18') {
    throw 'SYSTEM_CODE_OWNER_REQUIRED'
  }
  $aces=$acl.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier])
  $writeMask=[System.Security.AccessControl.FileSystemRights](
    [int][System.Security.AccessControl.FileSystemRights]::Write -bor
    [int][System.Security.AccessControl.FileSystemRights]::Delete -bor
    [int][System.Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor
    [int][System.Security.AccessControl.FileSystemRights]::ChangePermissions -bor
    [int][System.Security.AccessControl.FileSystemRights]::TakeOwnership)
  $serviceWrite=$false
  foreach ($ace in $aces) {
    if ($ace.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow) {
      throw 'UNEXPECTED_ACL_DENY_ACE'
    }
    $sid=$ace.IdentityReference.Value
    $writable=(($ace.FileSystemRights -band $writeMask) -ne 0)
    if ($sid -notin @('S-1-5-18','S-1-5-32-544',$ServiceSid)) { throw 'UNEXPECTED_ACL_PRINCIPAL' }
    if ($sid -eq 'S-1-5-32-544' -and $writable) { throw 'ADMIN_GROUP_CODE_WRITE_DENIED' }
    if ($sid -eq $ServiceSid -and $writable) { $serviceWrite=$true }
    if (-not $PrivateState -and $sid -ne 'S-1-5-18' -and $writable) {
      throw 'IMMUTABLE_CODE_WRITABLE_BY_SERVICE'
    }
  }
  if ($serviceWrite -ne $PrivateState) { throw 'SERVICE_WRITE_SCOPE_MISMATCH' }
}
function Sha256String([string]$Value) {
  $sha=[Security.Cryptography.SHA256]::Create()
  try { return [BitConverter]::ToString($sha.ComputeHash(
    [Text.Encoding]::UTF8.GetBytes($Value))).Replace('-','').ToLowerInvariant() }
  finally { $sha.Dispose() }
}

$service = Resolve-PrincipalSid $ServiceAccount
$reviewer = Resolve-PrincipalSid $ReviewerAccount
$worker = Resolve-PrincipalSid $WorkerAccount
if ((@($service,$reviewer,$worker) | Select-Object -Unique | Measure-Object).Count -ne 3) {
  throw 'SERVICE_REVIEWER_WORKER_MUST_HAVE_DISTINCT_SIDS'
}
$sourceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'mcp-host-source'))
$repoRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$sharedRoot = [IO.Path]::GetFullPath((Split-Path $repoRoot -Parent))
$dest = [IO.Path]::GetFullPath($Destination)
$programData = [IO.Path]::GetFullPath($env:ProgramData)
$programPrefix=$programData.TrimEnd('\') + '\'
if (-not $dest.StartsWith($programPrefix,[StringComparison]::OrdinalIgnoreCase)) {
  throw 'DESTINATION_MUST_BE_UNDER_PROGRAMDATA'
}
# Every ancestor up to ProgramData must be an existing regular directory.
# A junction could redirect an elevated source stage into agent-writable land.
$ancestor=[IO.Path]::GetDirectoryName($dest)
while ($ancestor -and $ancestor.StartsWith($programPrefix,[StringComparison]::OrdinalIgnoreCase)) {
  if (-not [IO.Directory]::Exists($ancestor) -or
      ([IO.File]::GetAttributes($ancestor) -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'DESTINATION_ANCESTOR_UNSAFE'
  }
  $ancestor=[IO.Path]::GetDirectoryName($ancestor)
}
$sharedPrefix = $sharedRoot.TrimEnd('\') + '\'
if ($dest.Equals($sharedRoot,[StringComparison]::OrdinalIgnoreCase) -or
    $dest.StartsWith($sharedPrefix,[StringComparison]::OrdinalIgnoreCase) -or
    $sharedRoot.StartsWith($dest.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)) {
  throw 'DESTINATION_INSIDE_SHARED_MCP_ROOT'
}
if ([IO.Directory]::Exists($dest) -or [IO.File]::Exists($dest)) {
  throw 'INSTALL_WILL_NOT_OVERWRITE_EXISTING_DESTINATION'
}
$sourceModules = @(
  '__init__.py',
  'host_pipe_peer.py',
  'review_grants.py',
  'review_qa_attestation.py',
  'review_target_snapshot.py',
  'review_grant_broker.py',
  'review_finalizer.py',
  'trusted_journal_loader.py',
  'owner_cutover_gate.py',
  'review_pipe_server.py',
  'review_pipe_client.py'
)
$manifest = [ordered]@{
  schema='p085-code-stage-plan-v2'
  execute=$false
  plan_sha256=$null
  code_layout='code'
  state_layout='private_state'
  code_writable_by_service=$false
  state_writable_by_service=$true
  destination=$dest
  service_sid=$service
  reviewer_sid=$reviewer
  worker_sid=$worker
  protected_copy_only=$true
  real_os_acl_e2e_verified=$false
  live_worker_isolation_verified=$false
  qa_supervisor_key_configured=$false
  privileged_review_enabled=$false
  files=@()
}
$entries = @()
foreach ($module in $sourceModules) {
  $source = Join-Path $sourceRoot ('workspace_mcp\' + $module)
  if (-not [IO.File]::Exists($source) -or ([IO.File]::GetAttributes($source) -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'MISSING_OR_LINKED_AUDITED_SOURCE'
  }
  $entries += [pscustomobject]@{ source=$source; relative=('code\workspace_mcp\'+$module); sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $source).Hash.ToLowerInvariant() }
}
$entrypoint = Join-Path $sourceRoot 'p085_reviewer_service.py'
$canonical = Join-Path $repoRoot 'review_merge.py'
foreach ($s in @($entrypoint,$canonical)) {
  if (-not [IO.File]::Exists($s) -or ([IO.File]::GetAttributes($s) -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'MISSING_OR_LINKED_AUDITED_SOURCE'
  }
}
$entries += [pscustomobject]@{source=$entrypoint;relative='code\p085_reviewer_service.py';sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $entrypoint).Hash.ToLowerInvariant()}
$entries += [pscustomobject]@{source=$canonical;relative='code\p085_trusted\review_merge.py';sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $canonical).Hash.ToLowerInvariant()}
$manifest.files = @($entries | ForEach-Object { [ordered]@{relative=$_.relative;sha256=$_.sha256} })
# Bind the exact destination, three real Windows SIDs, source SHA list
# and split-ACL layout to the reviewed plan. A checksum is NOT a
# signature/owner credential; the terminal must be independently trusted.
$planJson=([ordered]@{
  schema=$manifest.schema
  destination=$dest
  service_sid=$service
  reviewer_sid=$reviewer
  worker_sid=$worker
  code_layout=$manifest.code_layout
  state_layout=$manifest.state_layout
  files=$manifest.files
} | ConvertTo-Json -Depth 7 -Compress)
$manifest.plan_sha256=Sha256String $planJson
if (-not $Apply) {
  [pscustomobject]$manifest | ConvertTo-Json -Depth 6
  return
}
if (-not (Test-Admin)) { throw 'ELEVATED_WINDOWS_TERMINAL_REQUIRED' }
if ([Console]::IsInputRedirected -or [Console]::IsOutputRedirected) {
  throw 'FOREGROUND_INTERACTIVE_OWNER_REQUIRED'
}
if ([string]::IsNullOrWhiteSpace($ExpectedPlanSha256) -or
    $ExpectedPlanSha256 -cne $manifest.plan_sha256) {
  throw 'OWNER_REVIEWED_PLAN_SHA_REQUIRED'
}
Write-Host 'Source-only staged copy. No service, credentials, keys, live MCP or merge.' -ForegroundColor Yellow
Write-Host ('Target: ' + $dest)
Write-Host ('Reviewed plan SHA-256: ' + $manifest.plan_sha256)
$ack = Read-Host 'Type exactly STAGE-P085-PROTECTED-CODE to continue'
if ($ack -cne 'STAGE-P085-PROTECTED-CODE') { throw 'OWNER_DECLINED' }
if ([IO.Directory]::Exists($dest) -or [IO.File]::Exists($dest)) {
  throw 'INSTALL_WILL_NOT_OVERWRITE_EXISTING_DESTINATION'
}
$codeAcl=Protected-DirectoryAcl $service
$stateAcl=Protected-DirectoryAcl $service -PrivateState
$stage=Join-Path $programData ('.P085-staging-' + [Guid]::NewGuid().ToString('N'))
$sealed=$false
$published=$false
try {
  # Fresh randomized staging location; never execute or import this code.
  [IO.Directory]::CreateDirectory($stage) | Out-Null
  foreach ($sub in @('code','code\workspace_mcp','code\p085_trusted','private_state')) {
    [IO.Directory]::CreateDirectory((Join-Path $stage $sub)) | Out-Null
  }
  foreach ($entry in $entries) {
    $target=Join-Path $stage $entry.relative
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $entry.source).Hash.ToLowerInvariant() -cne $entry.sha256) {
      throw 'SOURCE_CHANGED_AFTER_PLAN_REVIEW'
    }
    [IO.File]::Copy($entry.source,$target,$false)
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $target).Hash.ToLowerInvariant() -cne $entry.sha256) {
      throw 'COPIED_CODE_SHA_MISMATCH_INSPECT_INSTALL'
    }
  }
  # A bounded, NON-AUTHORIZING install receipt permits periodic read-only
  # verification after staging. This is not an issuer-signed owner approval.
  $receipt=[ordered]@{
    schema='p085-protected-code-receipt-v1'
    plan_sha256=$manifest.plan_sha256
    service_sid=$service
    reviewer_sid=$reviewer
    worker_sid=$worker
    files=$manifest.files
    release_approved=$false
    live_mcp_modified=$false
  }
  $receiptJson=$receipt | ConvertTo-Json -Depth 8 -Compress
  [IO.File]::WriteAllText(
    (Join-Path $stage 'STAGE_RECEIPT.json'),
    $receiptJson,
    (New-Object System.Text.UTF8Encoding($false)))
  # Publish to a never-before-used stage-only destination BEFORE sealing.
  # A sealed SYSTEM-owned directory would not be renameable by an elevated
  # administrator without bypass privileges. No service or handler uses
  # this path yet; failure never activates anything.
  if ([IO.Directory]::Exists($dest) -or [IO.File]::Exists($dest)) {
    throw 'INSTALL_WILL_NOT_OVERWRITE_EXISTING_DESTINATION'
  }
  [IO.Directory]::Move($stage,$dest)
  $published=$true
  $stage=$dest
  # Seal code at the file level, then code subdirectories, then the root.
  # A directory ACL alone is insufficient: inherited write ACEs on files
  # could survive if propagation differs across Windows versions.
  $fileAcl=Protected-FileAcl $service
  Set-Acl -LiteralPath (Join-Path $stage 'STAGE_RECEIPT.json') -AclObject $fileAcl
  foreach ($entry in $entries) {
    Set-Acl -LiteralPath (Join-Path $stage $entry.relative) -AclObject $fileAcl
  }
  foreach ($sub in @('code\workspace_mcp','code\p085_trusted','code')) {
    Set-Acl -LiteralPath (Join-Path $stage $sub) -AclObject $codeAcl
  }
  Set-Acl -LiteralPath (Join-Path $stage 'private_state') -AclObject $stateAcl
  Set-Acl -LiteralPath $stage -AclObject $codeAcl
  $sealed=$true
  Assert-DirectoryAcl $stage $service $false
  Assert-DirectoryAcl (Join-Path $stage 'STAGE_RECEIPT.json') $service $false
  Assert-DirectoryAcl (Join-Path $stage 'code') $service $false
  Assert-DirectoryAcl (Join-Path $stage 'code\workspace_mcp') $service $false
  Assert-DirectoryAcl (Join-Path $stage 'code\p085_trusted') $service $false
  Assert-DirectoryAcl (Join-Path $stage 'private_state') $service $true
  if (@(Get-ChildItem -LiteralPath (Join-Path $stage 'private_state') -Force).Count -ne 0) {
    throw 'PRIVATE_STATE_MUST_START_EMPTY'
  }
  foreach ($entry in $entries) {
    $file=Join-Path $stage $entry.relative
    Assert-DirectoryAcl $file $service $false
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $file).Hash.ToLowerInvariant() -cne $entry.sha256) {
      throw 'SEALED_SOURCE_DIGEST_MISMATCH'
    }
  }
  $files=@(Get-ChildItem -LiteralPath (Join-Path $stage 'code') -File -Recurse -Force)
  if ($files.Count -ne $entries.Count) { throw 'UNEXPECTED_STAGED_CODE_FILES' }
  $folders=@(Get-ChildItem -LiteralPath (Join-Path $stage 'code') -Directory -Recurse -Force)
  if ($folders.Count -ne 2) { throw 'UNEXPECTED_STAGED_CODE_DIRECTORIES' }
  if (@($files | Where-Object {
      $_.Attributes -band [IO.FileAttributes]::ReparsePoint
  }).Count -gt 0) { throw 'STAGED_CODE_REPARSE_POINT_DENIED' }
} catch {
  if ($published) {
    # Destination is a staged CODE COPY only; an incomplete stage never
    # confers runtime authority. Never weaken DACLs for automatic cleanup.
    Write-Warning 'INCOMPLETE_P085_STAGING_NEEDS_OWNER_INSPECTION_NO_SERVICE_ENABLED'
  } elseif ([IO.Directory]::Exists($stage)) {
    try { [IO.Directory]::Delete($stage,$true) }
    catch { Write-Warning 'UNSEALED_PARTIAL_STAGE_NEEDS_OWNER_INSPECTION' }
  }
  throw
}
# Source-only stage. No service creation, signed grant issuance or cutover.
$manifest.execute=$true
$manifest | ConvertTo-Json -Depth 7
Write-Host 'STAGED ONLY. Real two-SID E2E, protected issuer, QA supervisor and owner cutover remain blocked.' -ForegroundColor Yellow
