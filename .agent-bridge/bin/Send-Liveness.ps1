#requires -Version 5.1
<#
.SYNOPSIS
    Send a liveness or heartbeat event to the bridge.

.DESCRIPTION
    Continuity-protocol helper added 2026-05-09. Wraps Write-AgentEvent
    with the liveness / heartbeat event types so an agent can declare
    "I am awake / I just sent a turn / I am about to sleep" without
    rebuilding the parameter set every time.

    Intended use:

      # at session start (right after Read-AgentBridge.ps1)
      .\bin\Send-Liveness.ps1 -Agent claude -State active

      # while running long work, every 60s
      .\bin\Send-Liveness.ps1 -Agent claude -State active `
                              -Message "fix-impl in progress, 30 of 50 pass"

      # at session end
      .\bin\Send-Liveness.ps1 -Agent claude -State sleeping `
                              -Message "Claude turn done; PR #124 pushed"

      # wake another agent
      .\bin\Send-Liveness.ps1 -Agent claude -Wake -To codex `
                              -Severity high `
                              -Message "PR #124 fix branch ready for re-review" `
                              -TaskId "wake-codex-for-pr124-rereview"

    The -Wake switch emits a `wake_request` event instead of a plain
    liveness event. Severity defaults to "medium".
#>
[CmdletBinding(DefaultParameterSetName = 'Liveness')]
param(
    [Parameter(Mandatory)] [string] $Agent,

    [Parameter(ParameterSetName = 'Liveness')]
    [ValidateSet('active','sleeping')] [string] $State = 'active',

    [Parameter(ParameterSetName = 'Heartbeat')]
    [switch] $Heartbeat,

    [Parameter(ParameterSetName = 'Wake')]
    [switch] $Wake,

    [string] $Message = '',
    [string] $TaskId = '',
    [string] $To = '',
    [string] $Severity = '',
    [string[]] $Paths = @(),
    [string] $Role = '',
    [string] $AgentUuid = '',
    [string[]] $Capabilities = @()
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$sessionIdentity = Join-Path $PSScriptRoot 'AgentBridgeSessionIdentity.ps1'
. $sessionIdentity
Assert-AgentBridgeSessionIdentity -RequestedAgent $Agent

$writeEventScript = Join-Path $PSScriptRoot 'Write-AgentEvent.ps1'

$type = 'liveness'
$status = $State
if ($Heartbeat) {
    $type = 'heartbeat'
    $status = 'active'
    if (-not $Severity) { $Severity = '' }
}
if ($Wake) {
    $type = 'wake_request'
    if (-not $Severity) { $Severity = 'medium' }
    $status = 'open'
    if (-not $To) {
        throw "wake_request requires -To <agent>"
    }
}

$ownerContext = $null
if ($type -in @('liveness','heartbeat') -and $status -eq 'active') {
    # Resolve and validate before emitting an active event. The event may
    # describe this session, but it must never refresh another generation's
    # claim merely because both sessions use the same public agent lane.
    $ownerContext = Get-AgentBridgeClaimOwnerContext
}

# Default heartbeat / liveness messages so the bridge is readable
# even when the caller passes no -Message.
if (-not $Message) {
    $Message = switch ($type) {
        'liveness'     { "$Agent $State" }
        'heartbeat'    { "$Agent active heartbeat" }
        'wake_request' { "$Agent requesting wake of $To" }
        default        { "$Agent $type" }
    }
}

# Default TaskId so the bridge is groupable per session.
if (-not $TaskId) {
    $TaskId = switch ($type) {
        'liveness'     { "{0}-liveness-{1:yyyy-MM-dd}" -f $Agent, (Get-Date) }
        'heartbeat'    { "{0}-heartbeat-{1:yyyy-MM-dd}" -f $Agent, (Get-Date) }
        'wake_request' { "{0}-wake-{1}-{2:yyyy-MM-dd-HH-mm-ss}" -f `
                            $Agent, $To, (Get-Date) }
        default        { "{0}-{1}-{2:yyyy-MM-dd}" -f $Agent, $type, (Get-Date) }
    }
}

# R15: bump last_heartbeat_utc on this agent's active claims so
# Invoke-StaleClaimSweep.ps1 doesn't auto-release them. Only
# liveness/active and heartbeat/active extend the lease — a
# liveness/sleeping or wake_request does NOT keep the claim alive
# (that would defeat the whole point of the lease).
$operationFailures = New-Object System.Collections.Generic.List[string]
$leaseRefreshRequested = (
    $type -in @('liveness','heartbeat') -and
    $status -eq 'active'
)

function Test-BridgeCurrentOwnerClaim {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] $Claim,
        [Parameter(Mandatory)] $CurrentOwner,
        [Parameter(Mandatory)] [string] $CurrentAgent
    )

    $claimAgentProperty = Get-AgentBridgeExactProperty `
        -InputObject $Claim `
        -Name 'agent'
    return (
        $null -ne $claimAgentProperty -and
        $claimAgentProperty.Value -is [string] -and
        [string]$claimAgentProperty.Value -ceq $CurrentAgent -and
        (Test-AgentBridgeStoredClaimOwnerComplete -Claim $Claim) -and
        (Test-AgentBridgeClaimOwner `
            -Claim $Claim `
            -OwnerContext $CurrentOwner)
    )
}

if ($leaseRefreshRequested) {
    # Resolve runtime root the same way other bridge scripts do
    # (R13 AGENT_BRIDGE_RUNTIME_ROOT support). Inlined here to
    # keep Send-Liveness self-contained.
    $bridgeRoot = Resolve-AgentBridgeRoot `
        -DefaultRoot (Split-Path -Parent $PSScriptRoot)
    $claimsDir = Join-Path (Join-Path $bridgeRoot 'work_queue') 'claims'
    $claimMutationLock = $null
    try {
        $claimMutationLock = Enter-AgentBridgeMutationLock `
            -BridgeRoot $bridgeRoot
        if (Test-Path -LiteralPath $claimsDir -PathType Container) {
        $heartbeatNow = (Get-Date).ToUniversalTime()
        $heartbeatTs = $heartbeatNow.ToString('o')
        $claimGroups = [System.Collections.Generic.Dictionary[
            string,
            System.Collections.Generic.List[object]
        ]]::new([System.StringComparer]::Ordinal)
        foreach ($file in @(Get-ChildItem -Path $claimsDir -Filter '*.json' `
                                           -File -ErrorAction SilentlyContinue)) {
            try {
                $claimSnapshot = Read-AgentBridgeStrictUtf8JsonSnapshot `
                    -LiteralPath $file.FullName
                $obj = ConvertFrom-AgentBridgeJson `
                    -Json ([string]$claimSnapshot.text)
            } catch {
                throw ((
                    "unreadable active claim blocks lease refresh: " +
                    "{0}: {1}"
                ) -f
                    $file.FullName,
                    $_.Exception.Message
                )
            }

            if (
                $null -eq $obj -or
                $obj -isnot
                    [System.Management.Automation.PSCustomObject]
            ) {
                continue
            }
            $claimTaskProperty = Get-AgentBridgeExactProperty `
                -InputObject $obj `
                -Name 'task_id'
            if (
                $null -eq $claimTaskProperty -or
                $claimTaskProperty.Value -isnot [string]
            ) {
                if (Test-BridgeCurrentOwnerClaim `
                        -Claim $obj `
                        -CurrentOwner $ownerContext `
                        -CurrentAgent $Agent) {
                    [void]$operationFailures.Add(
                        "owned active claim has missing or non-string " +
                        "task_id; lease refresh skipped: $($file.FullName)"
                    )
                }
                continue
            }
            $claimTaskId = [string]$claimTaskProperty.Value
            if ([string]::IsNullOrEmpty($claimTaskId)) {
                if (Test-BridgeCurrentOwnerClaim `
                        -Claim $obj `
                        -CurrentOwner $ownerContext `
                        -CurrentAgent $Agent) {
                    [void]$operationFailures.Add(
                        "owned active claim has empty task_id; lease " +
                        "refresh skipped: $($file.FullName)"
                    )
                }
                continue
            }
            if (-not $claimGroups.ContainsKey($claimTaskId)) {
                $claimGroups.Add(
                    $claimTaskId,
                    [System.Collections.Generic.List[object]]::new()
                )
            }
            $claimGroups[$claimTaskId].Add(
                [pscustomobject]@{
                    file = $file
                    claim = $obj
                    task_id = $claimTaskId
                    snapshot_bytes = [byte[]]$claimSnapshot.bytes
                    snapshot_sha256 = [string]$claimSnapshot.sha256
                    snapshot_length = [long]$claimSnapshot.length
                }
            )
        }

        # The refresh is a batch mutation. Any duplicate exact logical task
        # makes the whole captured queue ambiguous, including duplicates for
        # another agent or for a task this heartbeat would not otherwise
        # touch. Complete this preflight before the first claim write.
        $duplicateFailures = New-Object System.Collections.Generic.List[string]
        foreach ($claimTaskId in @($claimGroups.Keys)) {
            $entries = $claimGroups[$claimTaskId]
            if ($entries.Count -le 1) { continue }
            $duplicatePaths = @(
                $entries | ForEach-Object { $_.file.FullName }
            )
            $claimTaskDisplay = Format-AgentBridgeIdentityDisplay `
                -Value $claimTaskId
            [void]$duplicateFailures.Add((
                "duplicate active claim records for exact task_id " +
                "'$claimTaskDisplay': " +
                ($duplicatePaths -join ', ')
            ))
        }
        if ($duplicateFailures.Count -gt 0) {
            throw ($duplicateFailures -join '; ')
        }

        $eligibleEntries = New-Object System.Collections.Generic.List[object]
        foreach ($claimTaskId in @($claimGroups.Keys)) {
            $entries = $claimGroups[$claimTaskId]
            $entry = $entries[0]
            $file = $entry.file
            $obj = $entry.claim
            try {
                Assert-AgentBridgeTaskId -TaskId $claimTaskId
            } catch {
                Write-Warning ((
                    "invalid active claim task_id skipped during lease " +
                    "refresh: {0}: {1}"
                ) -f
                    $file.FullName,
                    $_.Exception.Message
                )
                if (Test-BridgeCurrentOwnerClaim `
                        -Claim $obj `
                        -CurrentOwner $ownerContext `
                        -CurrentAgent $Agent) {
                    [void]$operationFailures.Add(
                        "owned active claim has invalid task_id; lease " +
                        "refresh skipped: $($file.FullName)"
                    )
                }
                continue
            }
            $claimAgentProperty = Get-AgentBridgeExactProperty `
                -InputObject $obj `
                -Name 'agent'
            if (
                $null -eq $claimAgentProperty -or
                $claimAgentProperty.Value -isnot [string] -or
                [string]$claimAgentProperty.Value -cne $Agent
            ) {
                continue
            }
            if (-not (Test-AgentBridgeStoredClaimOwnerComplete `
                    -Claim $obj)) {
                continue
            }
            if (-not (Test-AgentBridgeClaimOwner `
                    -Claim $obj `
                    -OwnerContext $ownerContext)) {
                continue
            }
            Assert-AgentBridgeActiveClaimRawAuthorityFields `
                -Record $obj `
                -ClaimPath ([string]$file.FullName)
            [void]$eligibleEntries.Add($entry)
        }

        # Validate every owned claim before the first write. A legacy file
        # must not route around a preferred path occupied by another task.
        foreach ($entry in $eligibleEntries) {
            [void](Assert-AgentBridgePreferredClaimPath `
                -ClaimsDir $claimsDir `
                -TaskId ([string]$entry.task_id))
        }

        foreach ($entry in $eligibleEntries) {
            $file = $entry.file
            $obj = $entry.claim
            # Authorization and preferred-path validation above use the raw
            # claim. Only the persisted copy is projected to the shared,
            # allowlisted Claim schema.
            $canonicalClaim = ConvertTo-AgentBridgeCanonicalClaim -Claim $obj
            $canonicalClaim.last_heartbeat_utc = $heartbeatTs
            $leaseSeconds = [int]$canonicalClaim.lease_seconds
            if ($leaseSeconds -le 0) { $leaseSeconds = 900 }
            $canonicalClaim.lease_seconds = $leaseSeconds
            $expiresAt = try {
                $heartbeatNow.AddSeconds($leaseSeconds)
            } catch [System.ArgumentOutOfRangeException] {
                [DateTime]::SpecifyKind(
                    [DateTime]::MaxValue,
                    [DateTimeKind]::Utc
                )
            }
            $canonicalClaim.claim_lease_expires_utc = $expiresAt.ToString('o')
            try {
                $json = ($canonicalClaim | ConvertTo-Json -Depth 8)
                $encoding = New-Object System.Text.UTF8Encoding($false)
                $jsonBytes = $encoding.GetBytes($json)
                $expectedPublishSha256 = Get-AgentBridgeSha256Hex `
                    -Bytes $jsonBytes
                $expectedPublishLength = [long]$jsonBytes.Length
                Update-AgentBridgeFileFromBytes `
                    -PublishBytes $jsonBytes `
                    -DestinationPath $file.FullName `
                    -ExpectedSourceBytes ([byte[]]$entry.snapshot_bytes) `
                    -ExpectedSourceSha256 ([string]$entry.snapshot_sha256) `
                    -ExpectedSourceLength ([long]$entry.snapshot_length) `
                    -ExpectedPublishSha256 $expectedPublishSha256 `
                    -ExpectedPublishLength $expectedPublishLength
            } catch {
                $leaseFailure = (
                    'could not bump lease for ' +
                    $file.Name +
                    ': ' +
                    $_.Exception.Message
                )
                [void]$operationFailures.Add($leaseFailure)
            }
        }
        }
    } catch {
        [void]$operationFailures.Add(
            'lease refresh failed: ' + $_.Exception.Message
        )
    } finally {
        Exit-AgentBridgeMutationLock -Lock $claimMutationLock
    }
}

# Event append is deliberately outside the global claim mutation lock. A slow
# or failed event writer must neither block unrelated claim mutations nor stop
# an owned lease from being refreshed first. Do not publish an active event
# when an owned lease refresh failed: that event would falsely attest that the
# claim heartbeat was persisted.
if (-not ($leaseRefreshRequested -and $operationFailures.Count -gt 0)) {
    try {
        & $writeEventScript `
            -Agent $Agent `
            -Type $type `
            -Status $status `
            -Severity $Severity `
            -To $To `
            -Message $Message `
            -TaskId $TaskId `
            -Paths $Paths `
            -Role $Role `
            -AgentUuid $AgentUuid `
            -Capabilities $Capabilities
    } catch {
        [void]$operationFailures.Add(
            'event emit failed: ' + $_.Exception.Message
        )
    }
}

if ($operationFailures.Count -gt 0) {
    $failureSummary = $operationFailures -join '; '
    throw ('liveness operation did not fully succeed: ' + $failureSummary)
}
