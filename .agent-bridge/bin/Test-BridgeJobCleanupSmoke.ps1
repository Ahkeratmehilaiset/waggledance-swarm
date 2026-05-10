#requires -Version 5.1
<#
.SYNOPSIS
    R23.1.1 smoke test: orphan-job cleanup contract.

.DESCRIPTION
    Verifies that:
      1. Start-AgentBridgeSession.ps1 launches both wake (R23.0) and
         heartbeat (R23.1) jobs as expected.
      2. The PowerShell.Exiting event handler is registered exactly once
         even when Start-AgentBridgeSession.ps1 is dot-sourced twice.
      3. Stop-AgentBridgeSession.ps1 cleans up matching jobs by pattern.
      4. Stop-AgentBridgeSession.ps1 -Agent <name> only stops that
         agent's jobs.
      5. AGENT_BRIDGE_WAKE_JOB / AGENT_BRIDGE_HEARTBEAT_JOB env vars are
         cleared after Stop.

    Exit 0 on all checks PASS, 1 otherwise. Smoke uses a fresh temp
    runtime root and never touches the live bridge.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$bridgeBin = $PSScriptRoot
$startSession = Join-Path $bridgeBin 'Start-AgentBridgeSession.ps1'
$stopSession = Join-Path $bridgeBin 'Stop-AgentBridgeSession.ps1'

$results = New-Object System.Collections.Generic.List[object]
function Add-Check {
    param(
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [bool] $Passed,
        [string] $Detail = ''
    )
    [void]$results.Add([pscustomobject]@{
        name = $Name; passed = $Passed; detail = $Detail
    })
    $marker = if ($Passed) { 'PASS' } else { 'FAIL' }
    $color = if ($Passed) { 'Green' } else { 'Red' }
    Write-Host ("  [{0}] {1}" -f $marker, $Name) -ForegroundColor $color
    if ($Detail) { Write-Host "        $Detail" }
}

$tempRoot = Join-Path $env:TEMP `
    "bridge-r23.1.1-cleanup-smoke-$([guid]::NewGuid().ToString('N').Substring(0,12))"
$savedRoot = $env:AGENT_BRIDGE_RUNTIME_ROOT
$savedRunId = $env:AGENT_BRIDGE_RUN_ID
$savedWakeJob = $env:AGENT_BRIDGE_WAKE_JOB
$savedHbJob = $env:AGENT_BRIDGE_HEARTBEAT_JOB
$savedFlagVar = Get-Variable -Name '__AgentBridgeCleanupRegistered' `
    -Scope Global -ErrorAction SilentlyContinue
$savedFlag = if ($savedFlagVar) { $savedFlagVar.Value } else { $null }

try {
    Write-Host 'R23.1.1 orphan-job cleanup smoke test' -ForegroundColor Cyan
    Write-Host '======================================'
    Write-Host "Temp runtime root: $tempRoot"
    Write-Host ''

    # Pre-clean any leftover jobs from prior smoke runs in this host
    Get-Job -Name 'agent-bridge-*' -ErrorAction SilentlyContinue |
        Remove-Job -Force -ErrorAction SilentlyContinue
    Get-EventSubscriber -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.SourceIdentifier -eq 'PowerShell.Exiting' } |
        Unregister-Event -Force -ErrorAction SilentlyContinue
    Remove-Variable -Name '__AgentBridgeCleanupRegistered' `
        -Scope Global -ErrorAction SilentlyContinue

    # ── 1: bootstrap launches both jobs ───────────────────────────────
    Write-Host '1. Start-AgentBridgeSession launches wake + heartbeat jobs:'
    . $startSession -Agent claude -RuntimeRoot $tempRoot `
        -SkipBridgeRead -SkipLiveness -SkipGitStatus | Out-Null
    Start-Sleep -Milliseconds 500
    $wakeId = $env:AGENT_BRIDGE_WAKE_JOB
    $hbId = $env:AGENT_BRIDGE_HEARTBEAT_JOB
    Add-Check 'wake job env var set' (-not [string]::IsNullOrEmpty($wakeId)) "AGENT_BRIDGE_WAKE_JOB=$wakeId"
    Add-Check 'heartbeat job env var set' (-not [string]::IsNullOrEmpty($hbId)) "AGENT_BRIDGE_HEARTBEAT_JOB=$hbId"
    $wakeJob = Get-Job -Id $wakeId -ErrorAction SilentlyContinue
    $hbJob = Get-Job -Id $hbId -ErrorAction SilentlyContinue
    Add-Check 'wake job is Running' ($null -ne $wakeJob -and $wakeJob.State -eq 'Running')
    Add-Check 'heartbeat job is Running' ($null -ne $hbJob -and $hbJob.State -eq 'Running')

    # ── 2: cleanup handler registered exactly once ────────────────────
    Write-Host '2. PowerShell.Exiting handler registered (idempotent on second source):'
    $flagVar = Get-Variable -Name '__AgentBridgeCleanupRegistered' `
        -Scope Global -ErrorAction SilentlyContinue
    Add-Check 'cleanup-registered flag set' (
        $null -ne $flagVar -and $flagVar.Value -eq $true)
    $beforeSubscribers = @(
        Get-EventSubscriber -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.SourceIdentifier -eq 'PowerShell.Exiting' }
    )
    Add-Check 'cleanup PowerShell.Exiting subscriber exists' (
        $beforeSubscribers.Count -eq 1) "count=$($beforeSubscribers.Count)"
    $beforeCount = $beforeSubscribers.Count
    # Re-source the bootstrap; the flag should prevent double-register
    . $startSession -Agent claude -RuntimeRoot $tempRoot `
        -SkipBridgeRead -SkipLiveness -SkipGitStatus -SkipWakeWatcher -SkipHeartbeatJob | Out-Null
    $afterCount = @(
        Get-EventSubscriber -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.SourceIdentifier -eq 'PowerShell.Exiting' }
    ).Count
    Add-Check 'second dot-source did not duplicate cleanup subscription' `
        ($afterCount -eq $beforeCount) "before=$beforeCount after=$afterCount"

    # ── 3: Stop-AgentBridgeSession by pattern stops both ──────────────
    Write-Host '3. Stop-AgentBridgeSession (no -Agent) stops all matching jobs:'
    $stopOut = & $stopSession 2>&1
    $wakeAfter = Get-Job -Id $wakeId -ErrorAction SilentlyContinue
    $hbAfter = Get-Job -Id $hbId -ErrorAction SilentlyContinue
    Add-Check 'wake job removed' ($null -eq $wakeAfter)
    Add-Check 'heartbeat job removed' ($null -eq $hbAfter)
    Add-Check 'AGENT_BRIDGE_WAKE_JOB env cleared' ([string]::IsNullOrEmpty($env:AGENT_BRIDGE_WAKE_JOB))
    Add-Check 'AGENT_BRIDGE_HEARTBEAT_JOB env cleared' ([string]::IsNullOrEmpty($env:AGENT_BRIDGE_HEARTBEAT_JOB))
    $env:AGENT_BRIDGE_WAKE_JOB = 'whatif-wake'
    $env:AGENT_BRIDGE_HEARTBEAT_JOB = 'whatif-heartbeat'
    & $stopSession -WhatIf 2>&1 | Out-Null
    Add-Check 'Stop-AgentBridgeSession -WhatIf preserves wake env' `
        ($env:AGENT_BRIDGE_WAKE_JOB -eq 'whatif-wake')
    Add-Check 'Stop-AgentBridgeSession -WhatIf preserves heartbeat env' `
        ($env:AGENT_BRIDGE_HEARTBEAT_JOB -eq 'whatif-heartbeat')
    Remove-Item Env:AGENT_BRIDGE_WAKE_JOB -ErrorAction SilentlyContinue
    Remove-Item Env:AGENT_BRIDGE_HEARTBEAT_JOB -ErrorAction SilentlyContinue

    # ── 4: Stop-AgentBridgeSession -Agent stops only that agent ───────
    Write-Host '4. Stop-AgentBridgeSession -Agent <name> filters by agent:'
    # Bootstrap two agents in same host
    . $startSession -Agent claude -RuntimeRoot $tempRoot `
        -SkipBridgeRead -SkipLiveness -SkipGitStatus | Out-Null
    Start-Sleep -Milliseconds 200
    $claudeWakeId = $env:AGENT_BRIDGE_WAKE_JOB
    . $startSession -Agent codex -RuntimeRoot $tempRoot `
        -SkipBridgeRead -SkipLiveness -SkipGitStatus | Out-Null
    Start-Sleep -Milliseconds 200
    $codexWakeId = $env:AGENT_BRIDGE_WAKE_JOB

    & $stopSession -Agent codex 2>&1 | Out-Null

    $claudeWakeStill = Get-Job -Id $claudeWakeId -ErrorAction SilentlyContinue
    $codexWakeStopped = Get-Job -Id $codexWakeId -ErrorAction SilentlyContinue
    Add-Check 'codex jobs removed' ($null -eq $codexWakeStopped)
    Add-Check 'claude jobs survived (different agent)' ($null -ne $claudeWakeStill)

    # Cleanup remaining
    & $stopSession 2>&1 | Out-Null

    Write-Host ''
    $passed = ($results | Where-Object { $_.passed }).Count
    $total = $results.Count
    $color = if ($passed -eq $total) { 'Green' } else { 'Red' }
    Write-Host ("Result: {0}/{1} checks passed" -f $passed, $total) -ForegroundColor $color

    if ($passed -ne $total) {
        $results | Where-Object { -not $_.passed } | ForEach-Object {
            Write-Host ("  FAIL: {0} - {1}" -f $_.name, $_.detail) -ForegroundColor Red
        }
        exit 1
    }
    exit 0
} finally {
    # Last-chance cleanup of any agent-bridge jobs left behind
    Get-Job -Name 'agent-bridge-*' -ErrorAction SilentlyContinue |
        ForEach-Object {
            Stop-Job -Job $_ -ErrorAction SilentlyContinue
            Remove-Job -Job $_ -Force -ErrorAction SilentlyContinue
        }
    Get-EventSubscriber -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.SourceIdentifier -eq 'PowerShell.Exiting' } |
        Unregister-Event -Force -ErrorAction SilentlyContinue
    if ($null -ne $savedRoot) { $env:AGENT_BRIDGE_RUNTIME_ROOT = $savedRoot } else { Remove-Item Env:AGENT_BRIDGE_RUNTIME_ROOT -ErrorAction SilentlyContinue }
    if ($null -ne $savedRunId) { $env:AGENT_BRIDGE_RUN_ID = $savedRunId } else { Remove-Item Env:AGENT_BRIDGE_RUN_ID -ErrorAction SilentlyContinue }
    if ($null -ne $savedWakeJob) { $env:AGENT_BRIDGE_WAKE_JOB = $savedWakeJob } else { Remove-Item Env:AGENT_BRIDGE_WAKE_JOB -ErrorAction SilentlyContinue }
    if ($null -ne $savedHbJob) { $env:AGENT_BRIDGE_HEARTBEAT_JOB = $savedHbJob } else { Remove-Item Env:AGENT_BRIDGE_HEARTBEAT_JOB -ErrorAction SilentlyContinue }
    if ($null -ne $savedFlag) {
        Set-Variable -Name '__AgentBridgeCleanupRegistered' `
            -Scope Global -Value $savedFlag
    } else {
        Remove-Variable -Name '__AgentBridgeCleanupRegistered' `
            -Scope Global -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
