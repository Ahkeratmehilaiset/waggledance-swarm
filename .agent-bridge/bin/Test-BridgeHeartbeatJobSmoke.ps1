#requires -Version 5.1
<#
.SYNOPSIS
    R23.1 smoke test for Start-BridgeHeartbeat.ps1.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$bridgeBin = $PSScriptRoot
$claimTask = Join-Path $bridgeBin 'Claim-AgentTask.ps1'
$heartbeat = Join-Path $bridgeBin 'Start-BridgeHeartbeat.ps1'

$tempRoot = Join-Path $env:TEMP "bridge-r23-1-heartbeat-$([guid]::NewGuid().ToString('N').Substring(0,12))"
$savedRoot = $env:AGENT_BRIDGE_RUNTIME_ROOT
$savedToggle = $env:WAGGLE_BRIDGE_HEARTBEAT_ENABLED

function Read-Claim {
    param([string] $RuntimeRoot, [string] $TaskId)
    $safe = (($TaskId -replace '[^A-Za-z0-9._-]', '_').Trim('_'))
    $path = Join-Path (Join-Path $RuntimeRoot 'work_queue\claims') ($safe + '.json')
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    return (Get-Content -Raw -Path $path -Encoding UTF8 | ConvertFrom-Json)
}

try {
    $env:AGENT_BRIDGE_RUNTIME_ROOT = $tempRoot
    $env:WAGGLE_BRIDGE_HEARTBEAT_ENABLED = '1'

    Write-Host 'R23.1 heartbeat job smoke test' -ForegroundColor Cyan
    Write-Host '================================'
    Write-Host "Temp runtime root: $tempRoot"

    $taskId = 'r23-1-heartbeat-smoke'
    & $claimTask -Agent codex -TaskId $taskId -Summary 'heartbeat smoke' -Mode write -WriteScope 'waggledance/core' | Out-Null
    $before = Read-Claim -RuntimeRoot $tempRoot -TaskId $taskId
    Start-Sleep -Milliseconds 120

    & $heartbeat -Agent codex -RuntimeRoot $tempRoot -IntervalMs 50 -MaxIterations 1 | Out-Null
    $after = Read-Claim -RuntimeRoot $tempRoot -TaskId $taskId

    $beforeTs = [DateTime]::Parse([string]$before.last_heartbeat_utc).ToUniversalTime()
    $afterTs = [DateTime]::Parse([string]$after.last_heartbeat_utc).ToUniversalTime()
    $passed = ($afterTs -gt $beforeTs)

    if ($passed) {
        Write-Host "  [PASS] heartbeat bumped claim lease" -ForegroundColor Green
        Write-Host "        before=$($beforeTs.ToString('o')) after=$($afterTs.ToString('o'))"
        exit 0
    }

    Write-Host "  [FAIL] heartbeat did not bump claim lease" -ForegroundColor Red
    Write-Host "        before=$($beforeTs.ToString('o')) after=$($afterTs.ToString('o'))"
    exit 1
} finally {
    if ($null -ne $savedRoot) {
        $env:AGENT_BRIDGE_RUNTIME_ROOT = $savedRoot
    } else {
        Remove-Item Env:AGENT_BRIDGE_RUNTIME_ROOT -ErrorAction SilentlyContinue
    }
    if ($null -ne $savedToggle) {
        $env:WAGGLE_BRIDGE_HEARTBEAT_ENABLED = $savedToggle
    } else {
        Remove-Item Env:WAGGLE_BRIDGE_HEARTBEAT_ENABLED -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
