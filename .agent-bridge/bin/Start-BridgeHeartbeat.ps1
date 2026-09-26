#requires -Version 5.1
<#
.SYNOPSIS
    R23.1 background heartbeat loop for active bridge sessions.

.DESCRIPTION
    Periodically calls Send-Liveness.ps1 -Heartbeat for one agent so
    long-running turns/tests do not accidentally let active write claims
    expire under the stale-lease sweeper.

    Heartbeats are claim keepalives, not proof that the agent's model loop is
    reading the bridge. If the agent has no active claim, the helper skips the
    heartbeat and exits after a bounded number of idle iterations. This prevents
    orphaned helper processes from making a stopped agent look alive forever.

    Toggle: respects $env:WAGGLE_BRIDGE_HEARTBEAT_ENABLED. The loop exits
    immediately if set to '0'.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateScript({ $_ -cmatch '^[a-z][a-z0-9_-]{1,32}$' })]
    [string] $Agent,

    [int] $IntervalSeconds = 60,
    [int] $IntervalMs = 0,
    [int] $MaxIterations = 0,
    [int] $MaxIdleWithoutClaimIterations = 5,
    [string] $RuntimeRoot = '',
    [string] $SessionId = '',
    [string] $Role = '',
    [string] $AgentUuid = '',
    [string[]] $Capabilities = @()
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# B7: the one shared claim-lease / session-heartbeat implementation.
# Every lease writer goes through it, so there is a single CAS to review.
. (Join-Path $PSScriptRoot 'ClaimLeaseHeartbeat.ps1')

if ($env:WAGGLE_BRIDGE_HEARTBEAT_ENABLED -eq '0') {
    Write-Output "Start-BridgeHeartbeat: disabled via WAGGLE_BRIDGE_HEARTBEAT_ENABLED=0; exiting."
    return
}

$bridgeRoot = if ($RuntimeRoot) {
    $RuntimeRoot
} elseif ($env:AGENT_BRIDGE_RUNTIME_ROOT) {
    [string]$env:AGENT_BRIDGE_RUNTIME_ROOT
} else {
    Split-Path -Parent $PSScriptRoot
}
if (-not (Test-Path -LiteralPath $bridgeRoot -PathType Container)) {
    [void](New-Item -ItemType Directory -Path $bridgeRoot -Force -ErrorAction Stop)
}
$env:AGENT_BRIDGE_RUNTIME_ROOT = $bridgeRoot

$sendLiveness = Join-Path $PSScriptRoot 'Send-Liveness.ps1'
if (-not (Test-Path -LiteralPath $sendLiveness -PathType Leaf)) {
    throw "Send-Liveness.ps1 not found at $sendLiveness"
}

function Get-AgentActiveClaimCount {
    param(
        [Parameter(Mandatory)] [string] $Root,
        [Parameter(Mandatory)] [string] $AgentName
    )

    $claimsDir = Join-Path $Root 'work_queue\claims'
    if (-not (Test-Path -LiteralPath $claimsDir -PathType Container)) {
        return 0
    }

    $count = 0
    foreach ($file in @(Get-ChildItem -LiteralPath $claimsDir -Filter '*.json' -File -ErrorAction SilentlyContinue)) {
        try {
            $claim = Get-Content -Raw -LiteralPath $file.FullName -Encoding UTF8 |
                ConvertFrom-Json -ErrorAction Stop
        } catch {
            continue
        }
        if ($claim.PSObject.Properties['agent'] -and [string]$claim.agent -eq $AgentName) {
            $count++
        }
    }
    return $count
}

$sleepMs = if ($IntervalMs -gt 0) { $IntervalMs } else { [Math]::Max(1, $IntervalSeconds) * 1000 }
$beatIntervalSeconds = [Math]::Max(1, [int][Math]::Round($sleepMs / 1000.0))
# B7: a session heartbeat must survive a few missed beats but not a dead
# session, so the TTL is a small multiple of the beat interval.
$sessionTtlSeconds = [Math]::Max(60, $beatIntervalSeconds * 3)
$ownerIdentity = Get-BridgeOwnerIdentity -SessionId $SessionId
if ($null -eq $ownerIdentity) {
    Write-Warning (
        'Start-BridgeHeartbeat: no owner identity (session id plus ' +
        'AGENT_BRIDGE_OWNER_TOKEN); claims cannot be kept alive by this ' +
        'helper. Start the session via Start-AgentBridgeSession.ps1.')
}
$iteration = 0
$idleWithoutClaimIterations = 0
while ($MaxIterations -le 0 -or $iteration -lt $MaxIterations) {
    $iteration++
    # B7: beat immediately on the first iteration. Sleeping first left a
    # freshly created claim unprotected for a whole interval.
    if ($iteration -gt 1) {
        Start-Sleep -Milliseconds $sleepMs
    }
    $activeClaimCount = Get-AgentActiveClaimCount -Root $bridgeRoot -AgentName $Agent
    if ($activeClaimCount -le 0) {
        $idleWithoutClaimIterations++
        if ($MaxIdleWithoutClaimIterations -gt 0 -and
            $idleWithoutClaimIterations -ge $MaxIdleWithoutClaimIterations) {
            Write-Output (
                "Start-BridgeHeartbeat: exiting after {0} idle iteration(s) with no active claim for {1}." -f
                $idleWithoutClaimIterations, $Agent
            )
            break
        }
        continue
    }

    $idleWithoutClaimIterations = 0
    # B7: durable session heartbeat first, then the claim lease, both
    # atomic and independent of the emit below. Send-Liveness bumps the
    # lease through the same shared helper, so a failing event writer
    # cannot stop the keepalive.
    if ($null -ne $ownerIdentity) {
        [void](Write-BridgeSessionHeartbeat -Root $bridgeRoot -AgentName $Agent `
            -Identity $ownerIdentity -TtlSeconds $sessionTtlSeconds)
        [void](Update-BridgeClaimLease -Root $bridgeRoot -AgentName $Agent `
            -Identity $ownerIdentity)
    }
    try {
        & $sendLiveness `
            -Agent $Agent `
            -Heartbeat `
            -TaskId "$Agent-heartbeat-$((Get-Date).ToUniversalTime().ToString('yyyy-MM-dd'))" `
            -Message "$Agent background heartbeat" `
            -Role $Role `
            -AgentUuid $AgentUuid `
            -Capabilities $Capabilities | Out-Null
    } catch {
        Write-Warning "Start-BridgeHeartbeat: heartbeat failed: $($_.Exception.Message)"
    }
}
