#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [ValidateScript({ $_ -cmatch '^[a-z][a-z0-9_-]{1,32}$' })] [string] $Agent,
    [Parameter(Mandatory)] [string] $TaskId,
    [ValidateSet('done','blocked','abandoned','handoff')] [string] $Status = 'done',
    [string] $Message = '',
    [string] $RunId = '',
    # B7: explicit, documented compatibility rule for pre-B7 claims that
    # carry no owner identity. Without this switch such a claim cannot be
    # released at all (fail closed); with it, the caller states out loud
    # that it is adopting an unowned legacy claim. Label-only adoption is
    # never silent.
    [switch] $AllowLegacyUnownedClaim
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# B7: the one shared claim-lease / session-heartbeat implementation.
# Every lease writer goes through it, so there is a single CAS to review.
. (Join-Path $PSScriptRoot 'ClaimLeaseHeartbeat.ps1')

# R13: honor AGENT_BRIDGE_RUNTIME_ROOT. If env var is SET, USE IT
# (create root if missing, fail loud on malformed path). Codex
# blocker 2026-05-09T13:11Z: silent fallback when env points to a
# non-existing dir would split-brain agents.
$bridgeRoot = if ($env:AGENT_BRIDGE_RUNTIME_ROOT) {
    [string]$env:AGENT_BRIDGE_RUNTIME_ROOT
} else {
    Split-Path -Parent $PSScriptRoot
}
if (-not (Test-Path -LiteralPath $bridgeRoot -PathType Container)) {
    [void](New-Item -ItemType Directory -Path $bridgeRoot -Force -ErrorAction Stop)
}
$claimsDir = Join-Path (Join-Path $bridgeRoot 'work_queue') 'claims'
$doneDir = Join-Path (Join-Path $bridgeRoot 'work_queue') 'done'
if (-not (Test-Path -LiteralPath $doneDir)) {
    [void](New-Item -ItemType Directory -Path $doneDir -Force)
}

function ConvertTo-SafeName {
    param([string] $Name)
    return (($Name -replace '[^A-Za-z0-9._-]', '_').Trim('_'))
}

$safeTask = ConvertTo-SafeName $TaskId
$claimPath = Join-Path $claimsDir ($safeTask + '.json')
if (-not (Test-Path -LiteralPath $claimPath)) {
    Write-Error ("no active claim found for task: {0}" -f $TaskId)
    exit 2
}

# B7: hold the same per-claim lock the keepalive and sweep use, and
# re-read the claim under it, so a release can never interleave with an
# in-flight lease bump or a concurrent sweep of the same file.
$releaseLock = Enter-BridgeClaimLock -ClaimPath $claimPath
if ($null -eq $releaseLock) {
    Write-Error ("could not lock claim for release: {0}" -f $claimPath)
    exit 4
}
try {
if (-not (Test-Path -LiteralPath $claimPath -PathType Leaf)) {
    Write-Error ("claim disappeared before release: {0}" -f $TaskId)
    exit 2
}
$claim = Get-Content -Raw -Path $claimPath -Encoding UTF8 | ConvertFrom-Json
if ([string]$claim.agent -ne $Agent) {
    Write-Error ("claim belongs to {0}, not {1}" -f $claim.agent, $Agent)
    exit 3
}

# B7: the agent LABEL is not authority. Two sessions of the same agent
# are different owners, and a successor claim created after this one was
# archived must not be releasable by the previous session. Require the
# exact owner identity that Claim-AgentTask.ps1 recorded.
$claimHasOwner = (
    $claim.PSObject.Properties['owner_session_id'] -and
    $claim.PSObject.Properties['owner_token_sha256'] -and
    [string]$claim.owner_session_id -and
    [string]$claim.owner_token_sha256
)
if ($claimHasOwner) {
    $releaseIdentity = Get-BridgeOwnerIdentity -SessionId $RunId
    if (-not (Test-BridgeClaimOwner -Claim $claim -Identity $releaseIdentity)) {
        Write-Error (
            "claim is owned by another session (owner_session_id/owner_token " +
            "mismatch); only the owning session can release it")
        exit 5
    }
} elseif (-not $AllowLegacyUnownedClaim) {
    Write-Error (
        "claim carries no owner identity (pre-B7 claim); re-run with " +
        "-AllowLegacyUnownedClaim to adopt and release it explicitly")
    exit 6
}

if (-not $RunId) {
    $RunId = if ($env:AGENT_BRIDGE_RUN_ID) { [string]$env:AGENT_BRIDGE_RUN_ID } else { '' }
}

$claim | Add-Member -NotePropertyName released_at_utc -NotePropertyValue ((Get-Date).ToUniversalTime().ToString('o')) -Force
$claim | Add-Member -NotePropertyName release_status -NotePropertyValue $Status -Force
$claim | Add-Member -NotePropertyName release_message -NotePropertyValue $Message -Force

$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$donePath = Join-Path $doneDir ($safeTask + '.' + $stamp + '.' + $Status + '.json')

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
[System.IO.File]::WriteAllText($tmpClaim, $claimJson, $encoding)
$backupClaim = $null
try {
    $backupClaim = "$claimPath.bak.$PID.$([guid]::NewGuid().ToString('N'))"
    [System.IO.File]::Replace($tmpClaim, $claimPath, $backupClaim)
    try { Remove-Item -LiteralPath $backupClaim -Force -ErrorAction SilentlyContinue } catch {}
} catch {
    try {
        if ($backupClaim -and (Test-Path -LiteralPath $backupClaim)) {
            Remove-Item -LiteralPath $backupClaim -Force -ErrorAction SilentlyContinue
        }
    } catch {}
    try { Remove-Item -LiteralPath $tmpClaim -Force -ErrorAction SilentlyContinue } catch {}
    throw
}
[System.IO.File]::Move($claimPath, $donePath)
} finally {
    Exit-BridgeClaimLock -Lock $releaseLock
}

$eventType = if ($Status -eq 'done') { 'done' } elseif ($Status -eq 'handoff') { 'handoff' } elseif ($Status -eq 'blocked') { 'blocked' } else { 'release' }
& (Join-Path $PSScriptRoot 'Write-AgentEvent.ps1') -Agent $Agent -Type $eventType -TaskId $TaskId -Status $Status -Message $Message -RunId $RunId | Out-Null
[pscustomobject]@{ task_id = $TaskId; status = $Status; archived_claim = $donePath }
