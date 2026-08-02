#requires -Version 5.1
<#
.SYNOPSIS
    Smoke test for Restore-BridgeSpool.ps1 in an isolated temp bridge root.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$script:failures = 0
function Add-Check {
    param([string]$Name, [bool]$Passed, [string]$Detail = '')
    if ($Passed) { Write-Host "  PASS $Name" -ForegroundColor Green }
    else { Write-Host "  FAIL $Name :: $Detail" -ForegroundColor Red; $script:failures++ }
}

$tempRoot = Join-Path $env:TEMP "bridge-spool-replay-$([guid]::NewGuid().ToString('N').Substring(0, 12))"
$replayScript = Join-Path $PSScriptRoot 'Restore-BridgeSpool.ps1'

try {
    Write-Host 'Bridge spool replay smoke test' -ForegroundColor Cyan
    [void](New-Item -ItemType Directory -Path (Join-Path $tempRoot 'shared') -Force)
    [void](New-Item -ItemType Directory -Path (Join-Path $tempRoot 'spool') -Force)
    $eventsPath = Join-Path (Join-Path $tempRoot 'shared') 'events.jsonl'

    # 1. Empty spool -> no-op
    $out = & $replayScript -BridgeRoot $tempRoot
    Add-Check -Name 'empty spool is a no-op' -Passed ($out -match 'nothing to replay')

    # 2. A valid spooled event replays into the shared log and archives
    $event = '{"ts_utc":"2026-07-02T10:00:00Z","agent":"fable-5","type":"message","task_id":"spool-replay-smoke","status":"info","message":"recovered"}'
    $spoolFile = Join-Path (Join-Path $tempRoot 'spool') 'failed-append-fable-5-20260702T100000000-1234.jsonl'
    Set-Content -LiteralPath $spoolFile -Value $event -Encoding UTF8 -NoNewline

    $out = & $replayScript -BridgeRoot $tempRoot
    Add-Check -Name 'replay reports one replayed' -Passed ($out -match 'replayed=1 deduped=0 failed=0')
    $logged = Get-Content -LiteralPath $eventsPath -Raw -Encoding UTF8
    Add-Check -Name 'event appended to shared log' -Passed ($logged -match 'spool-replay-smoke')
    Add-Check -Name 'spool file archived' -Passed (
        (-not (Test-Path -LiteralPath $spoolFile)) -and
        (Test-Path -LiteralPath (Join-Path (Join-Path (Join-Path $tempRoot 'spool') 'replayed') (Split-Path -Leaf $spoolFile)))
    )

    # 3. Idempotent rerun -> nothing to replay
    $out = & $replayScript -BridgeRoot $tempRoot
    Add-Check -Name 'rerun is a no-op' -Passed ($out -match 'nothing to replay')

    # 4. Malformed spool file is SKIPPED (kept in place), never appended
    $badFile = Join-Path (Join-Path $tempRoot 'spool') 'failed-append-fable-5-20260702T110000000-9.jsonl'
    Set-Content -LiteralPath $badFile -Value '{not json' -Encoding UTF8 -NoNewline
    $before = (Get-Content -LiteralPath $eventsPath -Raw -Encoding UTF8)
    $out = & $replayScript -BridgeRoot $tempRoot 3>$null  # suppress warning stream
    $after = (Get-Content -LiteralPath $eventsPath -Raw -Encoding UTF8)
    Add-Check -Name 'malformed file skipped and kept' -Passed (
        ($out -match 'failed=1') -and (Test-Path -LiteralPath $badFile) -and ($before -eq $after)
    )

    # 5. Duplicate already live in events.jsonl -> archived WITHOUT second
    #    append (rco-2 #1483 finding 1: the caller-retried-and-succeeded case;
    #    retry copies differ by ts_utc, so the dedup key is semantic).
    Remove-Item -LiteralPath $badFile -Force
    $retryCopy = '{"ts_utc":"2026-07-02T10:00:05Z","agent":"fable-5","type":"message","task_id":"spool-replay-smoke","status":"info","message":"dup-signal"}'
    Add-Content -LiteralPath $eventsPath -Value $retryCopy -Encoding UTF8
    $dupSpool = Join-Path (Join-Path $tempRoot 'spool') 'failed-append-fable-5-20260702T100001000-77.jsonl'
    # The spooled FAILED attempt: same signal, OLDER ts + different pid.
    Set-Content -LiteralPath $dupSpool -Value '{"ts_utc":"2026-07-02T10:00:01Z","agent":"fable-5","type":"message","task_id":"spool-replay-smoke","status":"info","message":"dup-signal"}' -Encoding UTF8 -NoNewline
    $before = (Get-Content -LiteralPath $eventsPath -Encoding UTF8).Count
    $out = & $replayScript -BridgeRoot $tempRoot
    $after = (Get-Content -LiteralPath $eventsPath -Encoding UTF8).Count
    Add-Check -Name 'live duplicate archived without second append' -Passed (
        ($out -match 'deduped=1') -and ($after -eq $before) -and
        (-not (Test-Path -LiteralPath $dupSpool))
    ) -Detail "before=$before after=$after out=$out"

    # 6. A semantic duplicate repeated inside one spool file appends once.
    $inlineDupFile = Join-Path (Join-Path $tempRoot 'spool') 'failed-append-inline-20260702T113000000-10.jsonl'
    $inlineDupOne = '{"ts_utc":"2026-07-02T11:30:00Z","agent":"fable-5","type":"message","task_id":"spool-inline-duplicate","status":"info","message":"once"}'
    $inlineDupTwo = '{"ts_utc":"2026-07-02T11:30:01Z","agent":"fable-5","type":"message","task_id":"spool-inline-duplicate","status":"info","message":"once"}'
    Set-Content `
        -LiteralPath $inlineDupFile `
        -Value ($inlineDupOne + [Environment]::NewLine + $inlineDupTwo) `
        -Encoding UTF8 `
        -NoNewline
    $out = & $replayScript -BridgeRoot $tempRoot
    $inlineRows = @(
        Get-Content -LiteralPath $eventsPath -Encoding UTF8 |
            Where-Object { $_ -match '"task_id":"spool-inline-duplicate"' }
    )
    Add-Check -Name 'same-file semantic duplicate appends once' -Passed (
        ($out -match 'replayed=1 deduped=1 failed=0') -and
        ($inlineRows.Count -eq 1) -and
        (-not (Test-Path -LiteralPath $inlineDupFile))
    ) -Detail "rows=$($inlineRows.Count) out=$out"

    # 7. An existing archive leaf is never overwritten. The canonical append
    #    remains committed, while the exact held source stays for a later
    #    dedupe/archive retry.
    $collisionFile = Join-Path (Join-Path $tempRoot 'spool') 'failed-append-collision-20260702T114000000-11.jsonl'
    $collisionEvent = '{"ts_utc":"2026-07-02T11:40:00Z","agent":"fable-5","type":"message","task_id":"spool-archive-collision","status":"info","message":"retain-evidence"}'
    Set-Content -LiteralPath $collisionFile -Value $collisionEvent -Encoding UTF8 -NoNewline
    $archiveRoot = Join-Path (Join-Path $tempRoot 'spool') 'replayed'
    [void](New-Item -ItemType Directory -Path $archiveRoot -Force)
    $collisionDestination = Join-Path $archiveRoot (Split-Path -Leaf $collisionFile)
    Set-Content -LiteralPath $collisionDestination -Value 'archive-sentinel' -Encoding UTF8 -NoNewline
    Set-Content -LiteralPath $collisionDestination -Stream 'proof' -Value 'preserve-stream' -NoNewline
    $out = & $replayScript -BridgeRoot $tempRoot 3>$null
    $collisionRows = @(
        Get-Content -LiteralPath $eventsPath -Encoding UTF8 |
            Where-Object { $_ -match '"task_id":"spool-archive-collision"' }
    )
    $collisionSentinel = Get-Content -LiteralPath $collisionDestination -Raw -Encoding UTF8
    $collisionStream = Get-Content -LiteralPath $collisionDestination -Stream 'proof' -Raw
    Add-Check -Name 'archive collision preserves both generations' -Passed (
        ($out -match 'failed=1') -and
        (Test-Path -LiteralPath $collisionFile) -and
        ($collisionSentinel -eq 'archive-sentinel') -and
        ($collisionStream -eq 'preserve-stream') -and
        ($collisionRows.Count -eq 1)
    ) -Detail "rows=$($collisionRows.Count) out=$out"

    $out = & $replayScript -BridgeRoot $tempRoot 3>$null
    $collisionRows = @(
        Get-Content -LiteralPath $eventsPath -Encoding UTF8 |
            Where-Object { $_ -match '"task_id":"spool-archive-collision"' }
    )
    Add-Check -Name 'archive collision retry does not duplicate canonical row' -Passed (
        ($out -match 'deduped=1') -and
        ($out -match 'failed=1') -and
        ($collisionRows.Count -eq 1) -and
        (Test-Path -LiteralPath $collisionFile)
    ) -Detail "rows=$($collisionRows.Count) out=$out"
    Remove-Item -LiteralPath $collisionFile -Force
    Remove-Item -LiteralPath $collisionDestination -Force

    # 8. A successful archive renames the same held NTFS generation, including
    #    its alternate data streams.
    $adsFile = Join-Path (Join-Path $tempRoot 'spool') 'failed-append-ads-20260702T115000000-12.jsonl'
    $adsEvent = '{"ts_utc":"2026-07-02T11:50:00Z","agent":"fable-5","type":"message","task_id":"spool-held-ads","status":"info","message":"same-generation"}'
    Set-Content -LiteralPath $adsFile -Value $adsEvent -Encoding UTF8 -NoNewline
    Set-Content -LiteralPath $adsFile -Stream 'proof' -Value 'held-stream' -NoNewline
    $out = & $replayScript -BridgeRoot $tempRoot
    $adsDestination = Join-Path $archiveRoot (Split-Path -Leaf $adsFile)
    $adsStream = if (Test-Path -LiteralPath $adsDestination) {
        Get-Content -LiteralPath $adsDestination -Stream 'proof' -Raw
    } else {
        ''
    }
    Add-Check -Name 'held-handle archive preserves alternate stream' -Passed (
        ($out -match 'replayed=1') -and
        (-not (Test-Path -LiteralPath $adsFile)) -and
        (Test-Path -LiteralPath $adsDestination) -and
        ($adsStream -eq 'held-stream')
    ) -Detail "stream=$adsStream out=$out"

    # 9. Spool line missing core fields (no agent) is skipped and kept
    $noAgent = Join-Path (Join-Path $tempRoot 'spool') 'failed-append-x-20260702T120000000-5.jsonl'
    Set-Content -LiteralPath $noAgent -Value '{"ts_utc":"2026-07-02T12:00:00Z","type":"message","task_id":"t","status":"info"}' -Encoding UTF8 -NoNewline
    $out = & $replayScript -BridgeRoot $tempRoot 3>$null
    Add-Check -Name 'missing-core-field line skipped and kept' -Passed (
        ($out -match 'failed=1') -and (Test-Path -LiteralPath $noAgent)
    )
    Remove-Item -LiteralPath $noAgent -Force

    # 10. Concurrent replay guard exits without consuming spool files.
    $guardFile = Join-Path (Join-Path $tempRoot 'spool') 'failed-append-guard-20260702T130000000-6.jsonl'
    Set-Content -LiteralPath $guardFile -Value $event -Encoding UTF8 -NoNewline
    $guardMutex = $null
    $guardAcquired = $false
    try {
        $guardMutex = New-Object System.Threading.Mutex($false, 'Global\WaggleDanceBridgeSpoolReplayV1')
        $guardAcquired = $guardMutex.WaitOne(0)
        if (-not $guardAcquired) {
            Add-Check -Name 'concurrent replay guard setup' -Passed $false -Detail 'could not acquire replay mutex'
        } else {
            $out = & powershell -NoProfile -ExecutionPolicy Bypass -File $replayScript -BridgeRoot $tempRoot
            Add-Check -Name 'concurrent replay guard keeps file' -Passed (
                ($out -match 'already running') -and (Test-Path -LiteralPath $guardFile)
            ) -Detail "out=$out"
        }
    } finally {
        if ($null -ne $guardMutex) {
            if ($guardAcquired) { try { $guardMutex.ReleaseMutex() } catch {} }
            $guardMutex.Dispose()
        }
    }
    Remove-Item -LiteralPath $guardFile -Force -ErrorAction SilentlyContinue

    # 11. DryRun neither appends nor archives
    Set-Content -LiteralPath $spoolFile -Value $event -Encoding UTF8 -NoNewline
    $out = & $replayScript -BridgeRoot $tempRoot -DryRun
    Add-Check -Name 'dry run lists but keeps file' -Passed (
        (($out -match 'would archive as duplicate') -or ($out -match 'would replay')) -and (Test-Path -LiteralPath $spoolFile)
    )
} finally {
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if ($script:failures -gt 0) {
    Write-Host "SMOKE FAILED: $script:failures check(s)" -ForegroundColor Red
    exit 1
}
Write-Host 'SMOKE PASSED' -ForegroundColor Green
exit 0
