#requires -Version 5.1
<#
.SYNOPSIS
    R23.0 smoke test: bridge wake-on-event substrate.

.DESCRIPTION
    Verifies Watch-Bridge.ps1 + Test-BridgeWake.ps1 together close the
    pull-only deadlock window. The current pull protocol can take 270 s+
    to react when both agents are idle. With the watcher, a wake file
    appears in <2 s and the agent's polling loop consumes it on next
    iteration.

    Tests:
      1. targeted event creates wake file
      2. own-emission echo does NOT wake
      3. comma-separated `to` list including watched agent wakes
      4. non-targeted event does NOT wake
      5. Test-BridgeWake consumes the wake file on first read
      6. end-to-end: background Start-Job sees event in <2 s
      7. replacement fails closed instead of skipping replacement rows
      8. wake write failure terminates before committing the targeted row
      9. WAGGLE_BRIDGE_WAKE_ENABLED=0 short-circuits the watcher

    All test state lives under a fresh temp runtime root so the live
    bridge state is never touched.

    Exit 0 on all checks PASS, 1 otherwise.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$bridgeBin = $PSScriptRoot
$watch = Join-Path $bridgeBin 'Watch-Bridge.ps1'
$testWake = Join-Path $bridgeBin 'Test-BridgeWake.ps1'
$writeEvent = Join-Path $bridgeBin 'Write-AgentEvent.ps1'

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
    "bridge-r23-wake-smoke-$([guid]::NewGuid().ToString('N').Substring(0,12))"
$savedEnv = $env:AGENT_BRIDGE_RUNTIME_ROOT
$savedWakeEnabled = $env:WAGGLE_BRIDGE_WAKE_ENABLED
$identityIsolation = Join-Path $PSScriptRoot 'BridgeSmokeIdentityIsolation.ps1'
. $identityIsolation
$identitySnapshot = Enter-BridgeSmokeIdentityIsolation

function Reset-State {
    # Ensure a clean events.jsonl + no leftover wake files between tests.
    $shared = Join-Path $tempRoot 'shared'
    if (-not (Test-Path -LiteralPath $shared)) {
        [void](New-Item -ItemType Directory -Path $shared -Force)
    }
    $eventsPath = Join-Path $shared 'events.jsonl'
    # Append-AllText from prior Write-AgentEvent may briefly hold a
    # share-mode lock; retry a few times to absorb that transient.
    $encoding = New-Object System.Text.UTF8Encoding($false)
    for ($i = 0; $i -lt 20; $i++) {
        try {
            [System.IO.File]::WriteAllText($eventsPath, '', $encoding)
            break
        } catch {
            Start-Sleep -Milliseconds (25 + $i * 10)
        }
    }
    Get-ChildItem -LiteralPath $tempRoot -Filter 'wake_*' -File `
        -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
}

try {
    Write-Host 'R23.0 wake-on-event smoke test' -ForegroundColor Cyan
    Write-Host '=============================='
    Write-Host "Temp runtime root: $tempRoot"
    Write-Host ''

    $env:AGENT_BRIDGE_RUNTIME_ROOT = $tempRoot
    $env:WAGGLE_BRIDGE_WAKE_ENABLED = '1'

    # ── 0: supervisor-style restart preserves possible offline traffic ──
    Write-Host '0. default restart sets durable dirty bit before baselining:'
    Reset-State
    & $writeEvent -Agent codex -To claude -Type message -Status open `
        -TaskId 'r23-smoke-0' -Message 'arrived while watcher was absent' | Out-Null
    $wakeClaude = Join-Path $tempRoot 'wake_claude'
    & $watch -Agent claude -MaxIterations 1 `
        -PollIntervalMs 1 -DebounceMs 0 | Out-Null
    Add-Check 'restart dirty bit covers offline interval' `
        (Test-Path -LiteralPath $wakeClaude) ''

    # ── 1: targeted event creates wake file ────────────────────────────
    Write-Host '1. targeted event (codex -> claude) creates wake_claude:'
    Reset-State
    & $writeEvent -Agent codex -To claude -Type message -Status open `
        -TaskId 'r23-smoke-1' -Message 'targeted ping' | Out-Null
    & $watch -Agent claude -MaxIterations 2 `
        -PollIntervalMs 50 -DebounceMs 0 -StartLineCount 0 | Out-Null
    Add-Check 'targeted event woke claude' (Test-Path -LiteralPath $wakeClaude) $wakeClaude

    # ── 2: own-emission does NOT wake ─────────────────────────────────
    Write-Host '2. claude self-emission does NOT wake claude:'
    Reset-State
    & $writeEvent -Agent claude -To claude -Type message -Status open `
        -TaskId 'r23-smoke-2' -Message 'self echo' | Out-Null
    & $watch -Agent claude -MaxIterations 2 `
        -PollIntervalMs 50 -DebounceMs 0 -StartLineCount 0 | Out-Null
    Add-Check 'self-echo ignored' (-not (Test-Path -LiteralPath $wakeClaude)) ''

    # ── 3: comma-separated to wakes claude ────────────────────────────
    Write-Host '3. to="claude,operator" still wakes claude:'
    Reset-State
    & $writeEvent -Agent codex -To 'claude,operator' -Type message -Status open `
        -TaskId 'r23-smoke-3' -Message 'fanout' | Out-Null
    & $watch -Agent claude -MaxIterations 2 `
        -PollIntervalMs 50 -DebounceMs 0 -StartLineCount 0 | Out-Null
    Add-Check 'comma-separated to woke claude' (Test-Path -LiteralPath $wakeClaude) ''

    # ── 4: non-targeted event does NOT wake ───────────────────────────
    Write-Host '4. to=operator does NOT wake claude:'
    Reset-State
    & $writeEvent -Agent codex -To operator -Type message -Status open `
        -TaskId 'r23-smoke-4' -Message 'operator-only' | Out-Null
    & $watch -Agent claude -MaxIterations 2 `
        -PollIntervalMs 50 -DebounceMs 0 -StartLineCount 0 | Out-Null
    Add-Check 'non-targeted event ignored' (-not (Test-Path -LiteralPath $wakeClaude)) ''

    # ── 5: Test-BridgeWake consumes on read ───────────────────────────
    Write-Host '5. Test-BridgeWake returns true once then false:'
    Reset-State
    Set-Content -LiteralPath $wakeClaude -Value 'fake' -Encoding UTF8 -NoNewline
    $first = & $testWake -Agent claude
    $second = & $testWake -Agent claude
    Add-Check 'first Test-BridgeWake = true' ($first -eq $true) ''
    Add-Check 'second Test-BridgeWake = false (consumed)' ($second -eq $false) ''
    Add-Check 'wake file deleted after consume' `
        (-not (Test-Path -LiteralPath $wakeClaude)) ''

    # ── 6: end-to-end <2 s with Start-Job ─────────────────────────────
    Write-Host '6. background watcher reacts to event in < 2 s:'
    Reset-State
    $readyPath = Join-Path $tempRoot 'watch_ready_6'
    $job = Start-Job -Name 'r23-smoke-watcher' -ScriptBlock {
        param($s, $a, $r, $ready)
        & $s -Agent $a -RuntimeRoot $r -PollIntervalMs 50 -DebounceMs 0 `
            -ReadyPath $ready
    } -ArgumentList $watch, 'claude', $tempRoot, $readyPath
    try {
        $readyDeadline = [datetime]::UtcNow.AddSeconds(5)
        while (-not (Test-Path -LiteralPath $readyPath) -and
               [datetime]::UtcNow -lt $readyDeadline) {
            Start-Sleep -Milliseconds 50
        }
        if (-not (Test-Path -LiteralPath $readyPath)) {
            throw "background watcher did not initialize: $readyPath"
        }
        $startupDirtyBit = Test-Path -LiteralPath $wakeClaude
        if ($startupDirtyBit) {
            Remove-Item -LiteralPath $wakeClaude -Force -ErrorAction Stop
        }
        Add-Check 'background watcher published startup dirty bit' `
            $startupDirtyBit ''
        $emitTime = [datetime]::UtcNow
        & $writeEvent -Agent codex -To claude -Type message -Status open `
            -TaskId 'r23-smoke-6' -Message 'live ping' | Out-Null
        $deadline = $emitTime.AddSeconds(2)
        $detected = $false
        while ([datetime]::UtcNow -lt $deadline) {
            if (Test-Path -LiteralPath $wakeClaude) { $detected = $true; break }
            Start-Sleep -Milliseconds 50
        }
        $latencyMs = ([datetime]::UtcNow - $emitTime).TotalMilliseconds
        Add-Check ("live event detected in {0:N0} ms (< 2000)" -f $latencyMs) `
            $detected ''
    } finally {
        Stop-Job -Job $job -ErrorAction SilentlyContinue | Out-Null
        Remove-Job -Job $job -Force -ErrorAction SilentlyContinue | Out-Null
    }

    # ── 7: replacement fails closed ────────────────────────────────────
    Write-Host '7. replacement fails closed after setting durable wake:'
    Reset-State
    & $writeEvent -Agent codex -To operator -Type message -Status open `
        -TaskId 'r23-smoke-7-history-a' -Message 'history a' | Out-Null
    & $writeEvent -Agent codex -To operator -Type message -Status open `
        -TaskId 'r23-smoke-7-history-b' -Message 'history b' | Out-Null
    $eventsPath = Join-Path $tempRoot 'shared\events.jsonl'
    $readyPath = Join-Path $tempRoot 'watch_ready_7'
    $job = Start-Job -Name 'r23-smoke-replacement-watcher' -ScriptBlock {
        param($s, $a, $r, $ready)
        & $s -Agent $a -RuntimeRoot $r -PollIntervalMs 50 -DebounceMs 0 `
            -MaxIterations 80 -ReadyPath $ready
    } -ArgumentList $watch, 'claude', $tempRoot, $readyPath
    try {
        $readyDeadline = [datetime]::UtcNow.AddSeconds(5)
        while (-not (Test-Path -LiteralPath $readyPath) -and
               [datetime]::UtcNow -lt $readyDeadline) {
            Start-Sleep -Milliseconds 50
        }
        if (-not (Test-Path -LiteralPath $readyPath)) {
            throw "replacement watcher did not initialize: $readyPath"
        }
        $encoding = New-Object System.Text.UTF8Encoding($false)
        $replacement = Join-Path $tempRoot 'shared\events.replacement.jsonl'
        $oldBytes = [System.IO.File]::ReadAllBytes($eventsPath)
        $newRow = $encoding.GetBytes(
            '{"agent":"codex","to":"claude","type":"message",' +
            '"task_id":"r23-smoke-7-replacement","status":"open"}' +
            [char]10
        )
        $replacementBytes = New-Object byte[] ($oldBytes.Length + $newRow.Length)
        [System.Buffer]::BlockCopy($oldBytes, 0, $replacementBytes, 0, $oldBytes.Length)
        [System.Buffer]::BlockCopy(
            $newRow,
            0,
            $replacementBytes,
            $oldBytes.Length,
            $newRow.Length
        )
        [System.IO.File]::WriteAllBytes($replacement, $replacementBytes)
        Move-Item -LiteralPath $replacement -Destination $eventsPath -Force
        [void](Wait-Job -Job $job -Timeout 3)
        $replacementWakeCreated = Test-Path -LiteralPath $wakeClaude
        & $watch -Agent claude -MaxIterations 1 `
            -PollIntervalMs 1 -DebounceMs 0 | Out-Null
        Add-Check 'replacement terminates watcher after durable wake' (
            $job.State -eq 'Failed' -and
            $replacementWakeCreated -and
            (Test-Path -LiteralPath $wakeClaude)
        ) ([string]$job.State)
    } finally {
        Stop-Job -Job $job -ErrorAction SilentlyContinue | Out-Null
        Remove-Job -Job $job -Force -ErrorAction SilentlyContinue | Out-Null
    }

    # ── 8: wake write failure is fatal ─────────────────────────────────
    Write-Host '8. wake write failure terminates before cursor commit:'
    Reset-State
    [void](New-Item -ItemType Directory -Path $wakeClaude -Force)
    & $writeEvent -Agent codex -To claude -Type message -Status open `
        -TaskId 'r23-smoke-8' -Message 'wake write must succeed' | Out-Null
    $wakeWriteBlocked = $false
    try {
        & $watch -Agent claude -MaxIterations 1 `
            -PollIntervalMs 1 -DebounceMs 0 -StartLineCount 0 | Out-Null
    } catch {
        $wakeWriteBlocked = $true
    }
    Add-Check 'wake write failure is terminating' $wakeWriteBlocked ''
    Remove-Item -LiteralPath $wakeClaude -Recurse -Force

    # ── 9: WAGGLE_BRIDGE_WAKE_ENABLED=0 short-circuits ────────────────
    Write-Host '9. WAGGLE_BRIDGE_WAKE_ENABLED=0 makes watcher exit immediately:'
    Reset-State
    $env:WAGGLE_BRIDGE_WAKE_ENABLED = '0'
    & $writeEvent -Agent codex -To claude -Type message -Status open `
        -TaskId 'r23-smoke-9' -Message 'should be ignored' | Out-Null
    & $watch -Agent claude -MaxIterations 5 `
        -PollIntervalMs 50 -DebounceMs 0 | Out-Null
    Add-Check 'no wake file created when disabled' `
        (-not (Test-Path -LiteralPath $wakeClaude)) ''
    $env:WAGGLE_BRIDGE_WAKE_ENABLED = '1'

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
    Exit-BridgeSmokeIdentityIsolation -Snapshot $identitySnapshot
    if ($null -ne $savedEnv) {
        $env:AGENT_BRIDGE_RUNTIME_ROOT = $savedEnv
    } else {
        Remove-Item Env:AGENT_BRIDGE_RUNTIME_ROOT -ErrorAction SilentlyContinue
    }
    if ($null -ne $savedWakeEnabled) {
        $env:WAGGLE_BRIDGE_WAKE_ENABLED = $savedWakeEnabled
    } else {
        Remove-Item Env:WAGGLE_BRIDGE_WAKE_ENABLED -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
