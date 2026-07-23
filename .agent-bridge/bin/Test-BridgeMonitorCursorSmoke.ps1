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

    $eventsPath = Join-Path $tempRoot 'shared\events.jsonl'
    $oldLength = (Get-Item -LiteralPath $eventsPath).Length
    $replacementPath = Join-Path $tempRoot 'shared\events.replacement.jsonl'
    $encoding = New-Object System.Text.UTF8Encoding($false)
    $padding = 'x' * ([int]$oldLength + 128)
    [System.IO.File]::WriteAllText(
        $replacementPath,
        (
            '{"agent":"claude","to":"codex","type":"message",' +
            '"status":"open","task_id":"monitor-replacement",' +
            '"message":"' + $padding + '"}' + [char]10
        ),
        $encoding
    )
    Move-Item -LiteralPath $replacementPath -Destination $eventsPath -Force
    $beforeReplacement = [System.IO.File]::ReadAllBytes($state)
    $replacementBlocked = $false
    try {
        & $monitor -Agent codex -FromAgent claude -StatePath $state `
            -MaxIterations 1 -PollIntervalMs 1 | Out-Null
    } catch {
        $replacementBlocked = $true
    }
    $afterReplacement = [System.IO.File]::ReadAllBytes($state)
    Add-Check 'larger replacement fails closed with cursor byte-identical' (
        $replacementBlocked -and
        (Get-Item -LiteralPath $eventsPath).Length -gt $oldLength -and
        [Convert]::ToBase64String($beforeReplacement) -ceq
            [Convert]::ToBase64String($afterReplacement)
    )

    $replayState = Join-Path $tempRoot 'shared\monitor_replay.cursor.json'
    [System.IO.File]::WriteAllText(
        $eventsPath,
        (
            '{"agent":"claude","to":"codex","type":"message",' +
            '"status":"open","task_id":"replay-baseline",' +
            '"message":"baseline"}' + [char]10
        ),
        $encoding
    )
    & $monitor -Agent codex -FromAgent claude -StatePath $replayState `
        -MaxIterations 1 -PollIntervalMs 1 | Out-Null
    $beforeReplayReplacement = [System.IO.File]::ReadAllBytes($replayState)
    $replayReplacement = Join-Path $tempRoot 'shared\events.replay-replacement.jsonl'
    [System.IO.File]::WriteAllText(
        $replayReplacement,
        (
            '{"agent":"claude","to":"codex","type":"message",' +
            '"status":"open","task_id":"replay-replacement",' +
            '"message":"replacement"}' + [char]10
        ),
        $encoding
    )
    Move-Item -LiteralPath $replayReplacement -Destination $eventsPath -Force
    $replayBlocked = $false
    try {
        & $monitor -Agent codex -FromAgent claude -StatePath $replayState `
            -ReplayExisting -MaxIterations 1 -PollIntervalMs 1 | Out-Null
    } catch {
        $replayBlocked = $true
    }
    $afterReplayReplacement = [System.IO.File]::ReadAllBytes($replayState)
    Add-Check 'ReplayExisting does not reset across replacement' (
        $replayBlocked -and
        [Convert]::ToBase64String($beforeReplayReplacement) -ceq
            [Convert]::ToBase64String($afterReplayReplacement)
    )

    $generationState = Join-Path $tempRoot 'shared\monitor_generation.cursor.json'
    [System.IO.File]::WriteAllText(
        $eventsPath,
        (
            '{"agent":"claude","to":"codex","type":"message",' +
            '"status":"open","task_id":"generation-baseline",' +
            '"message":"baseline"}' + [char]10
        ),
        $encoding
    )
    & $monitor -Agent codex -FromAgent claude -StatePath $generationState `
        -MaxIterations 1 -PollIntervalMs 1 | Out-Null
    $beforeGeneration = [System.IO.File]::ReadAllBytes($generationState)
    $generationPath = Join-Path $tempRoot 'shared\events.generation.json'
    [System.IO.File]::WriteAllText(
        $generationPath, '{"generation":"g1"}', $encoding
    )
    $generationBlocked = $false
    try {
        & $monitor -Agent codex -FromAgent claude -StatePath $generationState `
            -MaxIterations 1 -PollIntervalMs 1 | Out-Null
    } catch {
        $generationBlocked = $true
    }
    $afterGeneration = [System.IO.File]::ReadAllBytes($generationState)
    Add-Check 'generation discontinuity leaves cursor byte-identical' (
        $generationBlocked -and
        [Convert]::ToBase64String($beforeGeneration) -ceq
            [Convert]::ToBase64String($afterGeneration)
    )

    $malformedState = Join-Path $tempRoot 'shared\monitor_malformed.cursor.json'
    [System.IO.File]::WriteAllText($malformedState, '{"cursor":', $encoding)
    $beforeMalformed = [System.IO.File]::ReadAllBytes($malformedState)
    $blocked = $false
    try {
        & $monitor -Agent codex -FromAgent claude -StatePath $malformedState `
            -MaxIterations 1 -PollIntervalMs 1 | Out-Null
    } catch {
        $blocked = $true
    }
    $afterMalformed = [System.IO.File]::ReadAllBytes($malformedState)
    Add-Check 'malformed cursor state fails closed without mutation' (
        $blocked -and
        [Convert]::ToBase64String($beforeMalformed) -ceq
            [Convert]::ToBase64String($afterMalformed)
    )

    $nonLeafState = Join-Path $tempRoot 'shared\monitor_nonleaf.cursor.json'
    [void](New-Item -ItemType Directory -Path $nonLeafState)
    $nonLeafBlocked = $false
    try {
        & $monitor -Agent codex -FromAgent claude -StatePath $nonLeafState `
            -MaxIterations 1 -PollIntervalMs 1 | Out-Null
    } catch {
        $nonLeafBlocked = $true
    }
    Add-Check 'non-leaf cursor state blocks monitor baseline fallback' (
        $nonLeafBlocked -and
        (Test-Path -LiteralPath $nonLeafState -PathType Container) -and
        @(Get-ChildItem -LiteralPath $nonLeafState -Force).Count -eq 0
    )

    $invalidLogState = Join-Path $tempRoot 'shared\monitor_invalid_log.cursor.json'
    [System.IO.File]::WriteAllText(
        $eventsPath,
        (
            '{"agent":"claude","to":"codex","type":"message",' +
            '"status":"open","task_id":"valid-baseline",' +
            '"message":"baseline"}' + [char]10
        ),
        $encoding
    )
    & $monitor -Agent codex -FromAgent claude -StatePath $invalidLogState `
        -MaxIterations 1 -PollIntervalMs 1 | Out-Null
    $beforeInvalidLog = [System.IO.File]::ReadAllBytes($invalidLogState)
    $badBytes = [byte[]](123,34,120,34,58,34,255,34,125,10)
    $appendStream = [System.IO.File]::Open(
        $eventsPath,
        [System.IO.FileMode]::Append,
        [System.IO.FileAccess]::Write,
        ([System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete)
    )
    try {
        $appendStream.Write($badBytes, 0, $badBytes.Length)
    } finally {
        $appendStream.Dispose()
    }
    $invalidLogBlocked = $false
    try {
        & $monitor -Agent codex -FromAgent claude -StatePath $invalidLogState `
            -MaxIterations 1 -PollIntervalMs 1 | Out-Null
    } catch {
        $invalidLogBlocked = $true
    }
    $afterInvalidLog = [System.IO.File]::ReadAllBytes($invalidLogState)
    Add-Check 'invalid log bytes leave monitor cursor byte-identical' (
        $invalidLogBlocked -and
        [Convert]::ToBase64String($beforeInvalidLog) -ceq
            [Convert]::ToBase64String($afterInvalidLog)
    )

    $invalidJsonState = Join-Path $tempRoot 'shared\monitor_invalid_json.cursor.json'
    [System.IO.File]::WriteAllText(
        $eventsPath,
        (
            '{"agent":"claude","to":"codex","type":"message",' +
            '"status":"open","task_id":"json-baseline",' +
            '"message":"baseline"}' + [char]10
        ),
        $encoding
    )
    & $monitor -Agent codex -FromAgent claude -StatePath $invalidJsonState `
        -MaxIterations 1 -PollIntervalMs 1 | Out-Null
    $beforeInvalidJson = [System.IO.File]::ReadAllBytes($invalidJsonState)
    [System.IO.File]::AppendAllText($eventsPath, ('[]' + [char]10), $encoding)
    $invalidJsonBlocked = $false
    try {
        & $monitor -Agent codex -FromAgent claude -StatePath $invalidJsonState `
            -MaxIterations 1 -PollIntervalMs 1 | Out-Null
    } catch {
        $invalidJsonBlocked = $true
    }
    $afterInvalidJson = [System.IO.File]::ReadAllBytes($invalidJsonState)
    Add-Check 'invalid JSON leaves monitor cursor byte-identical' (
        $invalidJsonBlocked -and
        [Convert]::ToBase64String($beforeInvalidJson) -ceq
            [Convert]::ToBase64String($afterInvalidJson)
    )

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
