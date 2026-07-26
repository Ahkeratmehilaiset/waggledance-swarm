#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [ValidateScript({ $_ -cmatch '^[a-z][a-z0-9_-]{1,32}$' })] [string] $Agent,
    [Parameter(Mandatory)] [string] $TaskId,
    [ValidateSet('done','blocked','abandoned','handoff')] [string] $Status = 'done',
    [string] $Message = '',
    [string] $RunId = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

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
    $safe = (($Name -replace '[^A-Za-z0-9._-]', '_').Trim('_'))
    if (-not $safe) { $safe = 'claim' }
    if ($safe -ceq $Name) { return $safe }

    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Name)
        $hash = $sha.ComputeHash($bytes)
    } finally {
        $sha.Dispose()
    }
    $digest = -join @($hash | ForEach-Object { $_.ToString('x2') })
    return "{0}-{1}" -f $safe, $digest.Substring(0, 12)
}

function Get-ExactTaskClaimFiles {
    param(
        [Parameter(Mandatory)] [string] $Directory,
        [Parameter(Mandatory)] [string] $ExactTaskId
    )

    $matches = @()
    foreach ($file in @(
        Get-ChildItem -LiteralPath $Directory -Filter '*.json' -File `
            -ErrorAction SilentlyContinue |
            Sort-Object FullName
    )) {
        try {
            $existing = Get-Content -Raw -LiteralPath $file.FullName `
                -Encoding UTF8 | ConvertFrom-Json
            $taskProperty = $existing.PSObject.Properties['task_id']
            if ($null -eq $taskProperty) { continue }
            $existingTaskId = [string]$taskProperty.Value
        } catch {
            continue
        }
        if (
            [string]::Equals(
                $existingTaskId,
                $ExactTaskId,
                [System.StringComparison]::Ordinal
            )
        ) {
            $matches += $file
        }
    }
    return $matches
}

function Stop-BridgeRelease {
    param(
        [Parameter(Mandatory)] [string] $ErrorMessage,
        [Parameter(Mandatory)] [int] $Code
    )
    [Console]::Error.WriteLine($ErrorMessage)
    exit $Code
}

$legacySafeTask = (($TaskId -replace '[^A-Za-z0-9._-]', '_').Trim('_'))
if (-not $legacySafeTask) {
    throw 'TaskId does not produce a safe claim filename'
}
$safeTask = ConvertTo-SafeName $TaskId
$matchingClaimFiles = @(
    Get-ExactTaskClaimFiles -Directory $claimsDir -ExactTaskId $TaskId
)
if ($matchingClaimFiles.Count -gt 1) {
    Stop-BridgeRelease -ErrorMessage (
        "multiple active claims found for exact task {0}: {1}" -f
        $TaskId,
        ((@($matchingClaimFiles.FullName) | Sort-Object) -join ', ')
    ) -Code 3
}
if ($matchingClaimFiles.Count -eq 0) {
    Stop-BridgeRelease -ErrorMessage (
        "no active claim found for task: {0}" -f $TaskId
    ) -Code 2
}
$claimPath = $matchingClaimFiles[0].FullName

$claim = Get-Content -Raw -LiteralPath $claimPath -Encoding UTF8 |
    ConvertFrom-Json
if ([string]$claim.agent -ne $Agent) {
    Stop-BridgeRelease -ErrorMessage (
        "claim belongs to {0}, not {1}" -f $claim.agent, $Agent
    ) -Code 3
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

$eventType = if ($Status -eq 'done') { 'done' } elseif ($Status -eq 'handoff') { 'handoff' } elseif ($Status -eq 'blocked') { 'blocked' } else { 'release' }
& (Join-Path $PSScriptRoot 'Write-AgentEvent.ps1') -Agent $Agent -Type $eventType -TaskId $TaskId -Status $Status -Message $Message -RunId $RunId | Out-Null
[pscustomobject]@{ task_id = $TaskId; status = $Status; archived_claim = $donePath }
