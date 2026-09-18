#requires -Version 5.1
# No pointer rebinding. Unknown observations remain null, never guessed.
[CmdletBinding()]
param()
$ErrorActionPreference='Stop'
function Get-BridgeEvidenceHash {
    param([string]$Path)
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    $sha=[Security.Cryptography.SHA256]::Create()
    try {return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','')}
    finally {$sha.Dispose();$stream.Dispose()}
}
$observed=[datetime]::UtcNow.ToString('o')
$native=$null; $launcher=$null; $nativeId=$null; $ancestryError=$null; $observedAgent=$null
try {
    if (Get-Command Get-CimInstance -ErrorAction SilentlyContinue) {
        $child=Get-CimInstance Win32_Process -Filter ('ProcessId='+$PID) -ErrorAction Stop
        $visited=@{}
        for ($i=0;$i -lt 24 -and $null -ne $child;$i++) {
            if ($visited.ContainsKey([string]$child.ProcessId)) { break }
            $visited[[string]$child.ProcessId]=$true
            if ($null -eq $native -and $child.Name -cin @('codex.exe','claude.exe')) { $native=$child }
            if ($child.CommandLine -match '(?i)-File\s+"?[^"\r\n]*start-wd-(agent|tools-consumer)\.ps1(?:"|\s)') { $launcher=$child; break }
            $parent=Get-CimInstance Win32_Process -Filter ('ProcessId='+$child.ParentProcessId) -ErrorAction Stop
            if ($null -ne $parent -and $parent.CreationDate -gt $child.CreationDate) { throw 'Parent process lifetime changed' }
            $child=$parent
        }
    }
} catch { $ancestryError=$_.Exception.Message; $native=$null; $launcher=$null }
if ($null -ne $native -and $native.CommandLine -match '(?i)(?:--resume|\bresume)\s+"?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:"|\s|$)') {
    $nativeId=$Matches[1].ToLowerInvariant()
}
if ($null -ne $launcher) {
    if ($launcher.CommandLine -match '(?i)start-wd-tools-consumer\.ps1') {$observedAgent='codex-tools-1'}
    elseif ($launcher.CommandLine -match '(?i)-Agent\s+"?([a-z][a-z0-9_-]{1,32})(?:"|\s|$)') {$observedAgent=$Matches[1]}
}
$pinStatus='unknown'; $pinError=$null; $generation=$null; $manifestHash=$null
$helperHashes=[ordered]@{}
foreach ($leaf in @('Get-BridgeExecutionEvidence.ps1','Write-BridgeTaskReply.ps1','Write-AgentEvent.ps1','BridgeTaskResult.ps1')) {
    $helperHashes[$leaf]=Get-BridgeEvidenceHash (Join-Path $PSScriptRoot $leaf)
}
if ($env:WD_BRIDGE_BIN) {
    try {
        if ([IO.Path]::GetFullPath($env:WD_BRIDGE_BIN).TrimEnd('\','/') -ine [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\','/')) { throw 'Inherited helper pin differs from executing helper directory' }
        $bundle=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
        $manifestPath=Join-Path $bundle deployment-manifest.json
        $manifestHash=Get-BridgeEvidenceHash $manifestPath
        if (-not $env:WD_REBOOT_EXPECTED_MANIFEST_HASH -or $manifestHash -ine $env:WD_REBOOT_EXPECTED_MANIFEST_HASH) { throw 'Inherited manifest anchor is missing or mismatched' }
        $manifest=Get-Content -LiteralPath $manifestPath -Raw|ConvertFrom-Json
        foreach ($leaf in $helperHashes.Keys) {
            $field=$manifest.files.PSObject.Properties['tools-bootstrap/.agent-bridge/bin/'+$leaf]
            if ($null -eq $field -or $field.Value -cne $helperHashes[$leaf]) { throw ('Packaged helper hash mismatch: '+$leaf) }
        }
        $generation=Split-Path $bundle -Leaf
        if ($null -ne $launcher -and $launcher.CommandLine -notmatch [regex]::Escape($generation)) { throw 'Executing helper generation differs from observed launcher' }
        $pinStatus=if ($null -ne $launcher) {'manifest_and_launcher_verified'} else {'manifest_verified_launcher_unknown'}
    } catch { $pinStatus='mismatch'; $pinError=$_.Exception.Message }
}
[pscustomobject]@{schema='wd.execution-evidence.v1';observed_at_utc=$observed;observation_completed_utc=[datetime]::UtcNow.ToString('o');
    helper_directory=$PSScriptRoot;helper_sha256=$helperHashes;parser_version=$PSVersionTable.PSVersion.ToString();
    inherited_helper_directory=$(if ($env:WD_BRIDGE_BIN) {$env:WD_BRIDGE_BIN} else {$null});
    generation=$generation;manifest_sha256=$manifestHash;pin_status=$pinStatus;pin_error=$pinError;
    observed_agent=$observedAgent;native_conversation_id=$nativeId;native_pid=$(if ($null -ne $native) {$native.ProcessId} else {$null});
    native_process_start_utc=$(if ($null -ne $native) {$native.CreationDate.ToUniversalTime().ToString('o')} else {$null});
    cli_kind=$(if ($null -ne $native) {$native.Name} else {$null});launcher_pid=$(if ($null -ne $launcher) {$launcher.ProcessId} else {$null});
    ancestry_error=$ancestryError;observation_source='helper_runtime_and_process_ancestry';
    semantic_content_verified=$false;task_completion_verified=$false} | ConvertTo-Json -Depth 8
