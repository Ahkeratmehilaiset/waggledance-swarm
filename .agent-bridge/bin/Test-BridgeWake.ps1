#requires -Version 5.1
<#
.SYNOPSIS
    R23.0 helper: check & consume the agent's wake-on-event sentinel.

.DESCRIPTION
    Returns $true if `<bridgeRoot>/wake_<Agent>` exists, deleting the
    file as a side effect (consume-on-read). Returns $false otherwise.

    Designed to be cheap enough to call every poll iteration. Pair it
    with Watch-Bridge.ps1, which writes the wake file when a new event
    arrives whose `to` targets the agent.

.PARAMETER Agent
    The agent whose wake file we test for.

.PARAMETER NoConsume
    If set, do not delete the wake file even if found. Useful for
    diagnostic probes.

.PARAMETER RuntimeRoot
    Optional override for the bridge runtime root.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet('codex','claude','operator','system')]
    [string] $Agent,

    [switch] $NoConsume,
    [string] $RuntimeRoot = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$bridgeRoot = if ($RuntimeRoot) {
    $RuntimeRoot
} elseif ($env:AGENT_BRIDGE_RUNTIME_ROOT) {
    [string]$env:AGENT_BRIDGE_RUNTIME_ROOT
} else {
    Split-Path -Parent $PSScriptRoot
}

$wakePath = Join-Path $bridgeRoot ("wake_{0}" -f $Agent)

if (-not (Test-Path -LiteralPath $wakePath)) {
    return $false
}

if (-not $NoConsume) {
    try {
        Remove-Item -LiteralPath $wakePath -Force -ErrorAction Stop
    } catch {
        Write-Warning "Test-BridgeWake: could not consume wake file '$wakePath': $($_.Exception.Message)"
    }
}

return $true
