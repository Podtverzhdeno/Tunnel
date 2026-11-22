# READ-ONLY SHA + manifest audit of an EXISTING unpacked Hermes extension.
# Does not inspect Chrome/Edge profiles, auth secrets, messages, user data,
# directory content outside explicit known source filenames, or write files.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$ExtensionDir)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$resolved=Resolve-Path -LiteralPath $ExtensionDir -ErrorAction Stop
if($resolved.Count -ne 1){throw 'ambiguous_extension_directory'}
$root=[string]$resolved[0].Path
if(-not (Test-Path -LiteralPath $root -PathType Container)){
    throw 'extension_directory_not_found'
}
$names=@('manifest.json','hermes_relay_bg.js','hermes_relay_content.js',
         'core.js','popup.js','popup.html')
$files=New-Object 'System.Collections.Generic.List[object]'
foreach($name in $names){
    $path=Join-Path $root $name
    if(Test-Path -LiteralPath $path -PathType Leaf){
        $hash=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        $size=(Get-Item -LiteralPath $path).Length
        $files.Add([pscustomobject]@{
            name=$name
            present=$true
            sha256=$hash
            bytes=[long]$size
        })
    }else{
        $files.Add([pscustomobject]@{
            name=$name;present=$false;sha256=$null;bytes=$null
        })
    }
}
$manifestPath=Join-Path $root 'manifest.json'
$version=$null;$manifestVersion=$null;$permissions=@();$background=$null
if(Test-Path -LiteralPath $manifestPath -PathType Leaf){
    try{
        $manifest=Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 |
            ConvertFrom-Json
        $version=[string]$manifest.version
        $manifestVersion=$manifest.manifest_version
        $permissions=@($manifest.permissions | Where-Object {$_ -ne $null} |
                       ForEach-Object {[string]$_})
        $background=[string]$manifest.background.service_worker
    }catch{
        throw 'extension_manifest_malformed'
    }
}
$bg=Join-Path $root 'hermes_relay_bg.js'
$bgText=if(Test-Path -LiteralPath $bg -PathType Leaf){
    Get-Content -LiteralPath $bg -Raw -Encoding UTF8
}else{''}
$summary=[pscustomobject]@{
    manifest_version=$manifestVersion
    extension_version=$version
    background_service_worker=$background
    declared_permissions=$permissions
    background_source_present=([bool]$bgText)
    bg_uses_legacy_chat_url=($bgText.Contains('https://chatgpt.com/#hermes-relay='))
    bg_mentions_exact_temporary_url=($bgText.Contains('https://chatgpt.com/?temporary-chat=true'))
    bg_mentions_tab_remove=($bgText.Contains('chrome.tabs.remove('))
}
[pscustomobject]@{
    schema_version=1
    scope='explicit_unpacked_extension_directory_only'
    read_only=$true
    path_in_output=$false
    secret_or_transcript_content_in_output=$false
    files=@($files)
    source_snapshot=$summary
    note='SHA256 is a file fingerprint, NOT a signed provenance assertion. The caller must compare with authorized source and check real loaded browser extension.'
} | ConvertTo-Json -Depth 6 -Compress
