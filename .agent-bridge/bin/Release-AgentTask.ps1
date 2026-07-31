#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $Agent,
    [Parameter(Mandatory)] [string] $TaskId,
    [ValidateSet('done','blocked','abandoned','handoff')] [string] $Status = 'done',
    [string] $Message = '',
    [string] $RunId = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$sessionIdentity = Join-Path $PSScriptRoot 'AgentBridgeSessionIdentity.ps1'
. $sessionIdentity
Assert-AgentBridgeSessionIdentity -RequestedAgent $Agent
Assert-AgentBridgeTaskId -TaskId $TaskId

function Assert-NoBridgePrivateMarker {
    param(
        [Parameter(Mandatory)] [string] $Label,
        [AllowNull()] $Value
    )

    foreach ($item in @($Value)) {
        $text = [string]$item
        foreach ($marker in @('PRIVATE_MARKER', '_DO_NOT_LEAK')) {
            if ($text.IndexOf(
                    $marker,
                    [System.StringComparison]::OrdinalIgnoreCase
                ) -ge 0) {
                throw "Bridge release $Label contains a private marker"
            }
        }
    }
}

if (-not $RunId) {
    $RunId = if ($env:AGENT_BRIDGE_RUN_ID) {
        [string]$env:AGENT_BRIDGE_RUN_ID
    } else {
        ''
    }
}
if ($RunId -and $RunId -notmatch '^[A-Za-z0-9._:-]{1,128}$') {
    throw "run_id must match ^[A-Za-z0-9._:-]{1,128}$"
}
$eventRole = [string]$env:AGENT_BRIDGE_ROLE
$eventAgentUuid = [string]$env:AGENT_BRIDGE_AGENT_UUID
$eventSessionId = [string]$env:AGENT_BRIDGE_SESSION_ID
$eventCapabilities = @(
    [string]$env:AGENT_BRIDGE_CAPABILITIES -split '[,;]' |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ }
)
if ($eventRole -and $eventRole -cnotmatch '^[a-z][a-z0-9_-]{1,32}$') {
    throw "role must match ^[a-z][a-z0-9_-]{1,32}$"
}
if ($eventAgentUuid -and $eventAgentUuid -notmatch '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$') {
    throw "agent_uuid must be a UUID"
}
if ($eventSessionId -and
    $eventSessionId -notmatch '^[A-Za-z0-9._:-]{1,128}$') {
    throw "session_id must match ^[A-Za-z0-9._:-]{1,128}$"
}
foreach ($eventCapability in $eventCapabilities) {
    if ($eventCapability -cnotmatch '^[a-z][a-z0-9_.:-]{1,64}$') {
        throw "capability must match ^[a-z][a-z0-9_.:-]{1,64}$"
    }
}
Assert-NoBridgePrivateMarker -Label 'task_id' -Value $TaskId
Assert-NoBridgePrivateMarker -Label 'message' -Value $Message
Assert-NoBridgePrivateMarker -Label 'run_id' -Value $RunId
Assert-NoBridgePrivateMarker -Label 'role' -Value $eventRole
Assert-NoBridgePrivateMarker -Label 'agent_uuid' -Value $eventAgentUuid
Assert-NoBridgePrivateMarker -Label 'session_id' -Value $eventSessionId
Assert-NoBridgePrivateMarker -Label 'capabilities' -Value $eventCapabilities

function Stop-BridgeRelease {
    param(
        [Parameter(Mandatory)] [string] $Message,
        [Parameter(Mandatory)] [int] $Code
    )

    [Console]::Error.WriteLine($Message)
    exit $Code
}

try {
    $ownerContext = Get-AgentBridgeClaimOwnerContext
} catch {
    Stop-BridgeRelease -Message ([string]$_.Exception.Message) -Code 3
}

# R13: honor AGENT_BRIDGE_RUNTIME_ROOT. If env var is SET, USE IT.
# A release is mutation-only: a missing runtime means there cannot be an
# active claim, so refuse without creating a new bridge tree.
$bridgeRoot = if ($env:AGENT_BRIDGE_RUNTIME_ROOT) {
    [string]$env:AGENT_BRIDGE_RUNTIME_ROOT
} else {
    Split-Path -Parent $PSScriptRoot
}
if (-not (Test-Path -LiteralPath $bridgeRoot -PathType Container)) {
    Stop-BridgeRelease -Message ("no active claim found for task: {0}" -f $TaskId) -Code 2
}
$claimsDir = Join-Path (Join-Path $bridgeRoot 'work_queue') 'claims'
$doneDir = Join-Path (Join-Path $bridgeRoot 'work_queue') 'done'

if (-not (Test-Path -LiteralPath $claimsDir -PathType Container)) {
    Stop-BridgeRelease -Message ("no active claim found for task: {0}" -f $TaskId) -Code 2
}

$mutationLock = Enter-AgentBridgeMutationLock -BridgeRoot $bridgeRoot
try {
try {
    [void](Assert-AgentBridgePreferredClaimPath `
        -ClaimsDir $claimsDir `
        -TaskId $TaskId)
} catch {
    Stop-BridgeRelease -Message ([string]$_.Exception.Message) -Code 3
}

$taskMatches = @()
foreach ($file in @(Get-ChildItem -Path $claimsDir -Filter '*.json' -File `
        -ErrorAction SilentlyContinue)) {
    try {
        $candidate = ConvertFrom-AgentBridgeJson -Json (
            Get-Content -Raw -Path $file.FullName -Encoding UTF8
        )
    } catch {
        continue
    }
    if ($candidate.PSObject.Properties['task_id'] -and
        $candidate.task_id -is [string] -and
        [string]$candidate.task_id -ceq $TaskId) {
        $taskMatches += [pscustomobject]@{
            file = $file
            claim = $candidate
        }
    }
}
if ($taskMatches.Count -eq 0) {
    Stop-BridgeRelease -Message ("no active claim found for task: {0}" -f $TaskId) -Code 2
}
if ($taskMatches.Count -gt 1) {
    $duplicatePaths = @($taskMatches | ForEach-Object { $_.file.FullName })
    Stop-BridgeRelease `
        -Message ("duplicate active claim records for exact task_id '{0}': {1}" -f $TaskId, ($duplicatePaths -join ', ')) `
        -Code 3
}

$matchedEntry = $taskMatches[0]
$claimFile = $matchedEntry.file
$claimPath = $claimFile.FullName
$claim = $matchedEntry.claim
if (
    -not $claim.PSObject.Properties['agent'] -or
    $claim.agent -isnot [string]
) {
    Stop-BridgeRelease `
        -Message ("claim has missing or non-string agent: {0}" -f $claimPath) `
        -Code 3
}
$claimAgent = [string]$claim.agent
if ($claimAgent -cne $Agent) {
    Stop-BridgeRelease -Message ("claim belongs to {0}, not {1}" -f $claimAgent, $Agent) -Code 3
}
try {
    Assert-AgentBridgeClaimOwner `
        -Claim $claim `
        -OwnerContext $ownerContext `
        -Operation 'release'
} catch {
    Stop-BridgeRelease -Message ([string]$_.Exception.Message) -Code 3
}

if (-not (Test-Path -LiteralPath $doneDir)) {
    [void](New-Item -ItemType Directory -Path $doneDir -Force)
}

$claim | Add-Member -NotePropertyName released_at_utc -NotePropertyValue ((Get-Date).ToUniversalTime().ToString('o')) -Force
$claim | Add-Member -NotePropertyName release_status -NotePropertyValue $Status -Force
$claim | Add-Member -NotePropertyName release_message -NotePropertyValue $Message -Force

$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$donePath = Join-Path $doneDir ($claimFile.BaseName + '.' + $stamp + '.' + $Status + '.json')
if (Test-Path -LiteralPath $donePath) {
    Stop-BridgeRelease `
        -Message ("release archive destination already exists: {0}" -f $donePath) `
        -Code 3
}

# Internal review fix R9 (2026-05-09): the previous "Set-Content done +
# Remove-Item claim" had a race window where a concurrent reader could
# observe both files simultaneously, or where a Remove-Item failure
# would leave the claim "active" even though the agent had archived
# it as done. Two-step atomic recipe:
#   1. Update the claim content in place via temp+Replace (so the claim
#      file already carries the released_at_utc fields).
#   2. Atomically Move() the claim into the done dir: single FS op, no
#      window where both files exist or neither exists.
$encoding = New-Object System.Text.UTF8Encoding($false)
$claimJson = ($claim | ConvertTo-Json -Depth 8)
$tmpClaim = "$claimPath.tmp.$PID.$([guid]::NewGuid().ToString('N'))"
$backupClaim = $null
try {
    [System.IO.File]::WriteAllText($tmpClaim, $claimJson, $encoding)
    $backupClaim = "$claimPath.bak.$PID.$([guid]::NewGuid().ToString('N'))"
    [System.IO.File]::Replace($tmpClaim, $claimPath, $backupClaim)
} catch {
    try {
        if ($backupClaim -and (Test-Path -LiteralPath $backupClaim)) {
            Remove-Item -LiteralPath $backupClaim -Force -ErrorAction SilentlyContinue
        }
    } catch {}
    try { Remove-Item -LiteralPath $tmpClaim -Force -ErrorAction SilentlyContinue } catch {}
    throw
}
$preserveBackup = $false
try {
    [System.IO.File]::Move($claimPath, $donePath)
} catch {
    $moveError = $_.Exception
    if (
        $backupClaim -and
        (Test-Path -LiteralPath $backupClaim -PathType Leaf)
    ) {
        try {
            Restore-AgentBridgeFileBackup `
                -OriginalPath $claimPath `
                -BackupPath $backupClaim
            $backupClaim = $null
        } catch {
            $preserveBackup = $true
            throw (
                "release archive move failed and active claim restore failed; original backup retained at {0}: {1}; restore: {2}" -f
                $backupClaim,
                $moveError.Message,
                $_.Exception.Message
            )
        }
    }
    throw $moveError
} finally {
    try {
        if (
            -not $preserveBackup -and
            $backupClaim -and
            (Test-Path -LiteralPath $backupClaim)
        ) {
            Remove-Item -LiteralPath $backupClaim -Force `
                -ErrorAction SilentlyContinue
        }
    } catch {}
}
} finally {
    Exit-AgentBridgeMutationLock -Lock $mutationLock
}

$eventType = if ($Status -eq 'done') { 'done' } elseif ($Status -eq 'handoff') { 'handoff' } elseif ($Status -eq 'blocked') { 'blocked' } else { 'release' }
& (Join-Path $PSScriptRoot 'Write-AgentEvent.ps1') -Agent $Agent -Type $eventType -TaskId $TaskId -Status $Status -Message $Message -RunId $RunId | Out-Null
[pscustomobject]@{ task_id = $TaskId; status = $Status; archived_claim = $donePath }
