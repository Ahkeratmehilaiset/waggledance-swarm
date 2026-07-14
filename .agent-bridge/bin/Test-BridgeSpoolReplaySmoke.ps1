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

    # 6. Spool line missing core fields (no agent) is skipped and kept
    $noAgent = Join-Path (Join-Path $tempRoot 'spool') 'failed-append-x-20260702T120000000-5.jsonl'
    Set-Content -LiteralPath $noAgent -Value '{"ts_utc":"2026-07-02T12:00:00Z","type":"message","task_id":"t","status":"info"}' -Encoding UTF8 -NoNewline
    $out = & $replayScript -BridgeRoot $tempRoot 3>$null
    Add-Check -Name 'missing-core-field line skipped and kept' -Passed (
        ($out -match 'failed=1') -and (Test-Path -LiteralPath $noAgent)
    )
    Remove-Item -LiteralPath $noAgent -Force

    # 7. Concurrent replay guard exits without consuming spool files.
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

    # 8. DryRun neither appends nor archives
    Set-Content -LiteralPath $spoolFile -Value $event -Encoding UTF8 -NoNewline
    $out = & $replayScript -BridgeRoot $tempRoot -DryRun
    Add-Check -Name 'dry run lists but keeps file' -Passed (
        (($out -match 'would archive as duplicate') -or ($out -match 'would replay')) -and (Test-Path -LiteralPath $spoolFile)
    )

    # 8. A large dedup scan must not deny concurrent bridge writers. The
    # production events log is large enough for a File.ReadLines scan to hold
    # a deny-write handle for many seconds, spooling every live event.
    Remove-Item -LiteralPath $spoolFile -Force
    $bulkEvent = '{"ts_utc":"2026-07-02T10:00:00Z","agent":"fable-5","type":"message","task_id":"bulk","status":"info","message":"existing"}'
    $bulkWriter = New-Object System.IO.StreamWriter($eventsPath, $false, (New-Object System.Text.UTF8Encoding($false)))
    try {
        for ($i = 0; $i -lt 25000; $i++) { $bulkWriter.WriteLine($bulkEvent) }
    } finally {
        $bulkWriter.Dispose()
    }
    $concurrentSpool = Join-Path (Join-Path $tempRoot 'spool') 'failed-append-codex-tools-1-20260702T130000000-8.jsonl'
    Set-Content -LiteralPath $concurrentSpool -Value '{"ts_utc":"2026-07-02T13:00:00Z","agent":"codex-tools-1","type":"message","task_id":"concurrent-replay","status":"info","message":"replay"}' -Encoding UTF8 -NoNewline

    $job = Start-Job -ScriptBlock {
        param($ScriptPath, $Root)
        & $ScriptPath -BridgeRoot $Root
    } -ArgumentList $replayScript, $tempRoot
    $readerObserved = $false
    for ($i = 0; $i -lt 500 -and $job.State -eq 'Running'; $i++) {
        $exclusive = $null
        try {
            $exclusive = New-Object System.IO.FileStream(
                $eventsPath,
                [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::ReadWrite,
                [System.IO.FileShare]::None
            )
        } catch [System.IO.IOException] {
            $readerObserved = $true
            break
        } finally {
            if ($null -ne $exclusive) { $exclusive.Dispose() }
        }
        Start-Sleep -Milliseconds 10
    }

    $concurrentAppendOk = $false
    if ($readerObserved) {
        try {
            $liveEvent = '{"ts_utc":"2026-07-02T13:00:01Z","agent":"fable-5","type":"message","task_id":"concurrent-live","status":"info","message":"live"}' + [Environment]::NewLine
            [System.IO.File]::AppendAllText($eventsPath, $liveEvent, (New-Object System.Text.UTF8Encoding($false)))
            $concurrentAppendOk = $true
        } catch [System.IO.IOException] {}
    }
    [void](Wait-Job -Job $job -Timeout 60)
    $jobOut = @(Receive-Job -Job $job -ErrorAction SilentlyContinue)
    Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
    Add-Check -Name 'dedup scan permits concurrent append' -Passed (
        $readerObserved -and $concurrentAppendOk -and
        ($jobOut -match 'replayed=1 deduped=0 failed=0') -and
        (-not (Test-Path -LiteralPath $concurrentSpool))
    ) -Detail "readerObserved=$readerObserved appendOk=$concurrentAppendOk out=$jobOut"

    # 9. A live retry that lands after the historical scan but before its
    # spool file is processed must still dedup atomically (post-scan TOCTOU).
    $replayedDir = Join-Path (Join-Path $tempRoot 'spool') 'replayed'
    $archiveCountBefore = @(Get-ChildItem -LiteralPath $replayedDir -File).Count
    for ($i = 0; $i -lt 150; $i++) {
        $fillerPath = Join-Path (Join-Path $tempRoot 'spool') ('failed-append-race-a-{0:D4}-1.jsonl' -f $i)
        $filler = ('{{"ts_utc":"2026-07-02T14:00:00Z","agent":"fable-5","type":"message","task_id":"race-filler-{0:D4}","status":"info","message":"filler"}}' -f $i)
        [System.IO.File]::WriteAllText($fillerPath, $filler, (New-Object System.Text.UTF8Encoding($false)))
    }
    $raceTarget = '{"ts_utc":"2026-07-02T14:00:01Z","agent":"fable-5","type":"decision","task_id":"postscan-race-target","status":"rco_pass","message":"same semantic signal"}'
    $raceSpool = Join-Path (Join-Path $tempRoot 'spool') 'failed-append-race-z-target-1.jsonl'
    [System.IO.File]::WriteAllText($raceSpool, $raceTarget, (New-Object System.Text.UTF8Encoding($false)))

    $raceJob = Start-Job -ScriptBlock {
        param($ScriptPath, $Root)
        & $ScriptPath -BridgeRoot $Root
    } -ArgumentList $replayScript, $tempRoot
    $processingObserved = $false
    for ($i = 0; $i -lt 1000 -and $raceJob.State -eq 'Running'; $i++) {
        $archiveCount = @(Get-ChildItem -LiteralPath $replayedDir -File -ErrorAction SilentlyContinue).Count
        if ($archiveCount -gt $archiveCountBefore) { $processingObserved = $true; break }
        Start-Sleep -Milliseconds 2
    }

    $liveRetryAppended = $false
    $appendMutex = $null
    $appendAcquired = $false
    try {
        $appendMutex = New-Object System.Threading.Mutex($false, 'Global\WaggleDanceBridgeAppendV1')
        try { $appendAcquired = $appendMutex.WaitOne(10000) }
        catch [System.Threading.AbandonedMutexException] { $appendAcquired = $true }
        if ($processingObserved -and $appendAcquired) {
            [System.IO.File]::AppendAllText(
                $eventsPath,
                $raceTarget + [Environment]::NewLine,
                (New-Object System.Text.UTF8Encoding($false))
            )
            $liveRetryAppended = $true
        }
    } finally {
        if ($null -ne $appendMutex) {
            if ($appendAcquired) { try { $appendMutex.ReleaseMutex() } catch {} }
            $appendMutex.Dispose()
        }
    }
    [void](Wait-Job -Job $raceJob -Timeout 120)
    $raceOut = @(Receive-Job -Job $raceJob -ErrorAction SilentlyContinue)
    Remove-Job -Job $raceJob -Force -ErrorAction SilentlyContinue
    $targetCount = @(
        Get-Content -LiteralPath $eventsPath -Encoding UTF8 |
            Where-Object { $_ -match '"task_id":"postscan-race-target"' }
    ).Count
    Add-Check -Name 'post-scan live retry dedup is atomic' -Passed (
        $processingObserved -and $liveRetryAppended -and $targetCount -eq 1 -and
        ($raceOut -match 'replayed=150 deduped=1 failed=0') -and
        (-not (Test-Path -LiteralPath $raceSpool))
    ) -Detail "processingObserved=$processingObserved liveRetry=$liveRetryAppended targetCount=$targetCount out=$raceOut"
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
