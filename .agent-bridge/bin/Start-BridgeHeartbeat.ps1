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
    [string] $Agent,

    [int] $IntervalSeconds = 60,
    [int] $IntervalMs = 0,
    [int] $MaxIterations = 0,
    [int] $MaxIdleWithoutClaimIterations = 5,
    [string] $RuntimeRoot = '',
    [string] $Role = '',
    [string] $AgentUuid = '',
    [string[]] $Capabilities = @()
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$sessionIdentity = Join-Path $PSScriptRoot 'AgentBridgeSessionIdentity.ps1'
. $sessionIdentity
Assert-AgentBridgeSessionIdentity -RequestedAgent $Agent
$ownerContext = Get-AgentBridgeClaimOwnerContext

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
        [Parameter(Mandatory)] [string] $AgentName,
        [Parameter(Mandatory)] $OwnerContext
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
        if ($claim.PSObject.Properties['agent'] -and
            [string]$claim.agent -eq $AgentName -and
            (Test-AgentBridgeClaimOwner `
                -Claim $claim `
                -OwnerContext $OwnerContext)) {
            $count++
        }
    }
    return $count
}

$sleepMs = if ($IntervalMs -gt 0) { $IntervalMs } else { [Math]::Max(1, $IntervalSeconds) * 1000 }
$iteration = 0
$idleWithoutClaimIterations = 0
while ($MaxIterations -le 0 -or $iteration -lt $MaxIterations) {
    $iteration++
    Start-Sleep -Milliseconds $sleepMs
    $activeClaimCount = Get-AgentActiveClaimCount `
        -Root $bridgeRoot `
        -AgentName $Agent `
        -OwnerContext $ownerContext
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
