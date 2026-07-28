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

function Read-EventCount {
    param([string] $RuntimeRoot)
    $path = Join-Path (Join-Path $RuntimeRoot 'shared') 'events.jsonl'
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return 0 }
    return @((Get-Content -LiteralPath $path -Encoding UTF8 -ErrorAction SilentlyContinue)).Count
}

function Convert-ClaimTimestampUtc {
    param([Parameter(Mandatory)] [object] $Value)

    if ($Value -is [DateTime]) {
        return ([DateTime]$Value).ToUniversalTime()
    }

    $styles = [System.Globalization.DateTimeStyles]::AssumeUniversal -bor
        [System.Globalization.DateTimeStyles]::AdjustToUniversal
    return [DateTime]::Parse(
        [string]$Value,
        [System.Globalization.CultureInfo]::InvariantCulture,
        $styles
    ).ToUniversalTime()
}

$identityIsolation = Join-Path $PSScriptRoot 'BridgeSmokeIdentityIsolation.ps1'
. $identityIsolation
$identitySnapshot = Enter-BridgeSmokeIdentityIsolation

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

    $beforeTs = Convert-ClaimTimestampUtc $before.last_heartbeat_utc
    $afterTs = Convert-ClaimTimestampUtc $after.last_heartbeat_utc
    $passed = ($afterTs -gt $beforeTs)

    if (-not $passed) {
        Write-Host "  [FAIL] heartbeat did not bump claim lease" -ForegroundColor Red
        Write-Host "        before=$($beforeTs.ToString('o')) after=$($afterTs.ToString('o'))"
        exit 1
    }

    Write-Host "  [PASS] heartbeat bumped claim lease" -ForegroundColor Green
    Write-Host "        before=$($beforeTs.ToString('o')) after=$($afterTs.ToString('o'))"

    $claimsDir = Join-Path $tempRoot 'work_queue\claims'
    Get-ChildItem -LiteralPath $claimsDir -Filter '*.json' -File -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction Stop
    $eventCountBeforeNoClaim = Read-EventCount -RuntimeRoot $tempRoot
    $noClaimOutput = & $heartbeat -Agent codex -RuntimeRoot $tempRoot `
        -IntervalMs 50 -MaxIterations 2 -MaxIdleWithoutClaimIterations 1
    $eventCountAfterNoClaim = Read-EventCount -RuntimeRoot $tempRoot
    $noClaimPassed = (
        $eventCountAfterNoClaim -eq $eventCountBeforeNoClaim -and
        ([string]$noClaimOutput) -match 'no active claim'
    )
    if ($noClaimPassed) {
        Write-Host "  [PASS] no-claim heartbeat skipped and exited bounded idle" -ForegroundColor Green
        Write-Host "        events_before=$eventCountBeforeNoClaim events_after=$eventCountAfterNoClaim"
        exit 0
    }

    Write-Host "  [FAIL] no-claim heartbeat wrote an event or did not exit bounded idle" -ForegroundColor Red
    Write-Host "        events_before=$eventCountBeforeNoClaim events_after=$eventCountAfterNoClaim output=$noClaimOutput"
    exit 1
} finally {
    Exit-BridgeSmokeIdentityIsolation -Snapshot $identitySnapshot
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
