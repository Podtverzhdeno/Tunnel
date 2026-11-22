# Redacted read-only extension inspector synthetic fixture.
# Runs only in a disposable directory, never touches real Edge profiles.
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('HermesExtensionFixture-'+[Guid]::NewGuid().ToString('N'))
[void](New-Item -ItemType Directory -Path $fixture)
$inspector=Join-Path $PSScriptRoot 'Inspect-Hermes-Extension.ps1'
try{
    $manifest='{"manifest_version":3,"version":"0.9.7","permissions":["tabs","storage"],"background":{"service_worker":"hermes_relay_bg.js"}}'
    Set-Content -LiteralPath (Join-Path $fixture 'manifest.json') -Encoding UTF8 -Value $manifest
    $legacy='const s="https://chatgpt.com/#hermes-relay=";chrome.tabs.remove(tab.id);'
    Set-Content -LiteralPath (Join-Path $fixture 'hermes_relay_bg.js') -Encoding UTF8 -Value $legacy
    $result=(& $inspector -ExtensionDir $fixture) | ConvertFrom-Json
    if($result.schema_version -ne 1 -or $result.read_only -ne $true -or
       $result.path_in_output -ne $false -or
       $result.secret_or_transcript_content_in_output -ne $false){
        throw 'fixture_redaction_contract_failed'
    }
    if($result.source_snapshot.extension_version -ne '0.9.7' -or
       $result.source_snapshot.manifest_version -ne 3 -or
       $result.source_snapshot.background_service_worker -ne 'hermes_relay_bg.js'){
        throw 'fixture_manifest_contract_failed'
    }
    if($result.source_snapshot.bg_uses_legacy_chat_url -ne $true -or
       $result.source_snapshot.bg_mentions_exact_temporary_url -ne $false -or
       $result.source_snapshot.bg_mentions_tab_remove -ne $true){
        throw 'fixture_legacy_detection_failed'
    }
    $src=@($result.files | Where-Object {$_.name -eq 'hermes_relay_bg.js'})
    if($src.Count -ne 1 -or $src[0].sha256 -notmatch '^[0-9a-f]{64}$'){
        throw 'fixture_sha256_failed'
    }
    # Simulate a newer extension source without leaking its entire content.
    $temporary='const url="https://chatgpt.com/?temporary-chat=true";'
    Set-Content -LiteralPath (Join-Path $fixture 'hermes_relay_bg.js') -Encoding UTF8 -Value $temporary
    $newer=(& $inspector -ExtensionDir $fixture) | ConvertFrom-Json
    if($newer.source_snapshot.bg_uses_legacy_chat_url -ne $false -or
       $newer.source_snapshot.bg_mentions_exact_temporary_url -ne $true -or
       $newer.source_snapshot.bg_mentions_tab_remove -ne $false){
        throw 'fixture_temporary_detection_failed'
    }
    # A partial manifest should not crash on absent optional fields.
    Set-Content -LiteralPath (Join-Path $fixture 'manifest.json') -Encoding UTF8 -Value '{"manifest_version":3,"version":"1.0"}'
    $partial=(& $inspector -ExtensionDir $fixture) | ConvertFrom-Json
    if($partial.source_snapshot.extension_version -ne '1.0'){
        throw 'fixture_optional_manifest_failed'
    }
    Write-Output 'PASS: extension SHA provenance, redaction, legacy-vs-temporary and partial manifest'
}finally{
    if(Test-Path -LiteralPath $fixture){
        Remove-Item -LiteralPath $fixture -Recurse -Force
    }
}
