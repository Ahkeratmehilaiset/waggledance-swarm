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
if ($RunId -and $RunId -notmatch '^[A-Za-z0-9._:-]{1,128}\z') {
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
if ($eventRole -and $eventRole -cnotmatch '^[a-z][a-z0-9_-]{1,32}\z') {
    throw "role must match ^[a-z][a-z0-9_-]{1,32}$"
}
if ($eventAgentUuid -and $eventAgentUuid -notmatch '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\z') {
    throw "agent_uuid must be a UUID"
}
if ($eventSessionId -and
    $eventSessionId -notmatch '^[A-Za-z0-9._:-]{1,128}\z') {
    throw "session_id must match ^[A-Za-z0-9._:-]{1,128}$"
}
foreach ($eventCapability in $eventCapabilities) {
    if ($eventCapability -cnotmatch '^[a-z][a-z0-9_.:-]{1,64}\z') {
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
$bridgeRoot = Resolve-AgentBridgeRoot `
    -DefaultRoot (Split-Path -Parent $PSScriptRoot)
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

$claimFiles = try {
    @(
        Get-ChildItem -Path $claimsDir -Filter '*.json' -File -Force `
            -ErrorAction Stop |
            Sort-Object FullName
    )
} catch {
    Stop-BridgeRelease `
        -Message (
            "cannot enumerate active claim records under {0}: {1}" -f
            $claimsDir,
            $_.Exception.Message
        ) `
        -Code 3
}
$parsedClaims = @()
$taskPaths = [System.Collections.Generic.Dictionary[string,string]]::new(
    [System.StringComparer]::Ordinal
)
foreach ($file in $claimFiles) {
    $claimPath = [System.IO.Path]::GetFullPath([string]$file.FullName)
    try {
        $candidateSnapshot = Read-AgentBridgeStrictUtf8JsonSnapshot `
            -LiteralPath $claimPath
        $candidate = ConvertFrom-AgentBridgeJson `
            -Json ([string]$candidateSnapshot.text)
    } catch {
        Stop-BridgeRelease `
            -Message (
                "malformed active claim JSON {0}: {1}" -f
                $claimPath,
                $_.Exception.Message
            ) `
            -Code 3
    }
    if (
        $null -eq $candidate -or
        $candidate -isnot
            [System.Management.Automation.PSCustomObject]
    ) {
        continue
    }
    $candidateTaskProperty = Get-AgentBridgeExactProperty `
        -InputObject $candidate `
        -Name 'task_id'
    if (
        $null -ne $candidateTaskProperty -and
        $candidateTaskProperty.Value -is [string] -and
        -not [string]::IsNullOrEmpty(
            [string]$candidateTaskProperty.Value
        )
    ) {
        $candidateTaskId = [string]$candidateTaskProperty.Value
        if ($taskPaths.ContainsKey($candidateTaskId)) {
            $candidateTaskDisplay = Format-AgentBridgeIdentityDisplay `
                -Value $candidateTaskId
            Stop-BridgeRelease `
                -Message ((
                    "duplicate active claim records for exact task_id " +
                    "'{0}': {1}, {2}"
                ) -f
                    $candidateTaskDisplay,
                    $taskPaths[$candidateTaskId],
                    $claimPath) `
                -Code 3
        }
        $taskPaths.Add($candidateTaskId, $claimPath)
        $parsedClaims += [pscustomobject]@{
            file = $file
            claim = $candidate
            task_id = $candidateTaskId
            snapshot_bytes = [byte[]]$candidateSnapshot.bytes
            snapshot_sha256 = [string]$candidateSnapshot.sha256
            snapshot_length = [long]$candidateSnapshot.length
        }
    }
}
$taskMatches = @(
    $parsedClaims |
        Where-Object { [string]$_.task_id -ceq $TaskId }
)
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
try {
    Assert-AgentBridgeActiveClaimRawAuthorityFields `
        -Record $claim `
        -ClaimPath $claimPath
} catch {
    Stop-BridgeRelease -Message ([string]$_.Exception.Message) -Code 3
}
$claimAgentProperty = Get-AgentBridgeExactProperty `
    -InputObject $claim `
    -Name 'agent'
if (
    $null -eq $claimAgentProperty -or
    $claimAgentProperty.Value -isnot [string]
) {
    Stop-BridgeRelease `
        -Message ("claim has missing or non-string agent: {0}" -f $claimPath) `
        -Code 3
}
$claimAgent = [string]$claimAgentProperty.Value
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

$canonicalClaim = ConvertTo-AgentBridgeCanonicalClaim -Claim $claim
Ensure-AgentBridgePlainDirectory `
    -LiteralPath $doneDir `
    -Context 'claim archive directory'

$canonicalClaim | Add-Member -NotePropertyName released_at_utc -NotePropertyValue ((Get-Date).ToUniversalTime().ToString('o')) -Force
$canonicalClaim | Add-Member -NotePropertyName release_status -NotePropertyValue $Status -Force
$canonicalClaim | Add-Member -NotePropertyName release_message -NotePropertyValue $Message -Force

$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$donePath = Join-Path $doneDir ($claimFile.BaseName + '.' + $stamp + '.' + $Status + '.json')
if (Test-Path -LiteralPath $donePath) {
    Stop-BridgeRelease `
        -Message ("release archive destination already exists: {0}" -f $donePath) `
        -Code 3
}

# Bind release to the exact raw bytes that passed identity and owner checks.
# The shared snapshot transaction quarantines and verifies those bytes before
# publishing the final release record directly into done/. A fresh active
# generation that appears after quarantine is retained and is never overwritten.
$encoding = New-Object System.Text.UTF8Encoding($false)
$claimJson = ($canonicalClaim | ConvertTo-Json -Depth 8)
$claimJsonBytes = $encoding.GetBytes($claimJson)
$expectedPublishSha256 = Get-AgentBridgeSha256Hex -Bytes $claimJsonBytes
$expectedPublishLength = [long]$claimJsonBytes.Length
Publish-AgentBridgeFileFromSnapshot `
    -PublishBytes $claimJsonBytes `
    -SourcePath $claimPath `
    -PublishPath $donePath `
    -ExpectedSourceBytes ([byte[]]$matchedEntry.snapshot_bytes) `
    -ExpectedSourceSha256 ([string]$matchedEntry.snapshot_sha256) `
    -ExpectedSourceLength ([long]$matchedEntry.snapshot_length) `
    -ExpectedPublishSha256 $expectedPublishSha256 `
    -ExpectedPublishLength $expectedPublishLength
} finally {
    Exit-AgentBridgeMutationLock -Lock $mutationLock
}

$eventType = if ($Status -eq 'done') { 'done' } elseif ($Status -eq 'handoff') { 'handoff' } elseif ($Status -eq 'blocked') { 'blocked' } else { 'release' }
& (Join-Path $PSScriptRoot 'Write-AgentEvent.ps1') -Agent $Agent -Type $eventType -TaskId $TaskId -Status $Status -Message $Message -RunId $RunId | Out-Null
[pscustomobject]@{ task_id = $TaskId; status = $Status; archived_claim = $donePath }
