#requires -Version 5.1
<#
.SYNOPSIS
    Smoke test for Monitor-AgentBridge.ps1 cursor semantics.

.DESCRIPTION
    Uses a temporary bridge runtime root. Verifies that the monitor starts at
    "now" by default, emits each new substantive event once, advances its
    cursor across skipped ACK/infrastructure events, and can run as a warm
    background monitor that catches a live append without historical replay.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$bridgeBin = $PSScriptRoot
$monitor = Join-Path $bridgeBin 'Monitor-AgentBridge.ps1'
$writeEvent = Join-Path $bridgeBin 'Write-AgentEvent.ps1'

$results = New-Object System.Collections.Generic.List[object]
function Add-Check {
    param(
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [bool] $Passed,
        [string] $Detail = ''
    )
    [void]$results.Add([pscustomobject]@{
        name = $Name
        passed = $Passed
        detail = $Detail
    })
    $marker = if ($Passed) { 'PASS' } else { 'FAIL' }
    $color = if ($Passed) { 'Green' } else { 'Red' }
    Write-Host ("  [{0}] {1}" -f $marker, $Name) -ForegroundColor $color
    if ($Detail) { Write-Host "        $Detail" }
}

$tempRoot = Join-Path $env:TEMP `
    "bridge-monitor-cursor-smoke-$([guid]::NewGuid().ToString('N').Substring(0,12))"
$savedRoot = $env:AGENT_BRIDGE_RUNTIME_ROOT

try {
    Write-Host 'Bridge monitor cursor smoke test' -ForegroundColor Cyan
    Write-Host '================================'
    Write-Host "Temp runtime root: $tempRoot"
    Write-Host ''

    $env:AGENT_BRIDGE_RUNTIME_ROOT = $tempRoot
    [void](New-Item -ItemType Directory -Path (Join-Path $tempRoot 'shared') -Force)
    $state = Join-Path $tempRoot 'shared\monitor_codex_from_claude.cursor.json'

    & $writeEvent -Agent claude -To codex -Type message -Status open `
        -TaskId 'monitor-smoke-history' -Message 'historical message' | Out-Null
    $out = @(& $monitor -Agent codex -FromAgent claude -StatePath $state `
        -MaxIterations 1 -PollIntervalMs 1)
    Add-Check 'default startup suppresses historical replay' `
        ($out.Count -eq 0) (($out -join "`n"))

    & $writeEvent -Agent claude -To codex -Type decision -Status ready `
        -TaskId 'monitor-smoke-new' -Message 'new substantive event' | Out-Null
    $out = @(& $monitor -Agent codex -FromAgent claude -StatePath $state `
        -MaxIterations 1 -PollIntervalMs 1)
    Add-Check 'new substantive event emitted once' `
        ($out.Count -eq 1 -and $out[0] -match 'monitor-smoke-new') `
        (($out -join "`n"))

    $out = @(& $monitor -Agent codex -FromAgent claude -StatePath $state `
        -MaxIterations 1 -PollIntervalMs 1)
    Add-Check 'second read does not replay same event' `
        ($out.Count -eq 0) (($out -join "`n"))

    & $writeEvent -Agent claude -To codex -Type message -Status received `
        -TaskId 'monitor-smoke-ack' -Message 'ack only' | Out-Null
    & $writeEvent -Agent claude -To codex -Type heartbeat -Status active `
        -TaskId 'monitor-smoke-heartbeat' -Message 'heartbeat' | Out-Null
    & $writeEvent -Agent claude -To codex -Type liveness -Status active `
        -TaskId 'monitor-smoke-liveness' -Message 'liveness' | Out-Null
    $out = @(& $monitor -Agent codex -FromAgent claude -StatePath $state `
        -MaxIterations 1 -PollIntervalMs 1)
    Add-Check 'ACK and infrastructure events are skipped' `
        ($out.Count -eq 0) (($out -join "`n"))

    & $writeEvent -Agent claude -To operator -Type finding -Status open `
        -TaskId 'monitor-smoke-operator-only' -Message 'operator only' | Out-Null
    $out = @(& $monitor -Agent codex -FromAgent claude -TargetedOnly `
        -StatePath $state -MaxIterations 1 -PollIntervalMs 1)
    Add-Check 'TargetedOnly skips events not addressed to local agent' `
        ($out.Count -eq 0) (($out -join "`n"))

    & $writeEvent -Agent claude -To 'codex,operator' -Type finding -Status open `
        -TaskId 'monitor-smoke-fanout' -Message 'fanout event' | Out-Null
    $out = @(& $monitor -Agent codex -FromAgent claude -TargetedOnly `
        -StatePath $state -MaxIterations 1 -PollIntervalMs 1)
    Add-Check 'TargetedOnly accepts comma-separated local target' `
        ($out.Count -eq 1 -and $out[0] -match 'monitor-smoke-fanout') `
        (($out -join "`n"))

    $liveState = Join-Path $tempRoot 'shared\monitor_live.cursor.json'
    & $writeEvent -Agent claude -To codex -Type message -Status open `
        -TaskId 'monitor-smoke-live-history' -Message 'live historical baseline' | Out-Null
    $job = Start-Job -Name 'bridge-monitor-cursor-live-smoke' -ScriptBlock {
        param($scriptPath, $root, $statePath)
        $env:AGENT_BRIDGE_RUNTIME_ROOT = $root
        & $scriptPath -Agent codex -FromAgent claude -StatePath $statePath `
            -MaxIterations 25 -PollIntervalMs 50
    } -ArgumentList $monitor, $tempRoot, $liveState
    try {
        $readyDeadline = (Get-Date).AddSeconds(4)
        while (-not (Test-Path -LiteralPath $liveState) -and
               (Get-Date) -lt $readyDeadline) {
            Start-Sleep -Milliseconds 50
        }
        if (-not (Test-Path -LiteralPath $liveState)) {
            throw "live monitor did not initialize cursor state: $liveState"
        }
        & $writeEvent -Agent claude -To codex -Type done -Status ready `
            -TaskId 'monitor-smoke-live-new' -Message 'live append' | Out-Null
        [void](Wait-Job -Job $job -Timeout 4)
        $out = @(Receive-Job -Job $job -ErrorAction SilentlyContinue)
        Add-Check 'warm monitor catches live append without replaying baseline' `
            (($out -join "`n") -match 'monitor-smoke-live-new' -and
             ($out -join "`n") -notmatch 'monitor-smoke-live-history') `
            (($out -join "`n"))
    } finally {
        Stop-Job -Job $job -ErrorAction SilentlyContinue | Out-Null
        Remove-Job -Job $job -Force -ErrorAction SilentlyContinue | Out-Null
    }

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
    if ($null -ne $savedRoot) {
        $env:AGENT_BRIDGE_RUNTIME_ROOT = $savedRoot
    } else {
        Remove-Item Env:AGENT_BRIDGE_RUNTIME_ROOT -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
