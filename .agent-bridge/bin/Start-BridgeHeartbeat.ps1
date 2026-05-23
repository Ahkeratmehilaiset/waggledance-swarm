#requires -Version 5.1
<#
.SYNOPSIS
    R23.1 background heartbeat loop for active bridge sessions.

.DESCRIPTION
    Periodically calls Send-Liveness.ps1 -Heartbeat for one agent so
    long-running turns/tests do not accidentally let active write claims
    expire under the stale-lease sweeper.

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
    [string] $RuntimeRoot = '',
    [string] $Role = '',
    [string] $AgentUuid = '',
    [string[]] $Capabilities = @()
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

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

$sleepMs = if ($IntervalMs -gt 0) { $IntervalMs } else { [Math]::Max(1, $IntervalSeconds) * 1000 }
$iteration = 0
while ($MaxIterations -le 0 -or $iteration -lt $MaxIterations) {
    $iteration++
    Start-Sleep -Milliseconds $sleepMs
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
