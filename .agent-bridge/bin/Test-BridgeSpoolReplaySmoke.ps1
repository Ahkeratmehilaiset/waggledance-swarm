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
$writerScript = Join-Path $PSScriptRoot 'Write-AgentEvent.ps1'
$previousRuntimeRoot = [Environment]::GetEnvironmentVariable(
    'AGENT_BRIDGE_RUNTIME_ROOT',
    'Process'
)
$previousForcedFailure = [Environment]::GetEnvironmentVariable(
    'AGENT_BRIDGE_TEST_MUTEX_CONSTRUCTION_FAILURE',
    'Process'
)
$previousPartialFailure = [Environment]::GetEnvironmentVariable(
    'AGENT_BRIDGE_TEST_APPEND_FAILURE_AFTER_BYTES',
    'Process'
)
$previousBeforeAppendReady = [Environment]::GetEnvironmentVariable(
    'AGENT_BRIDGE_TEST_BEFORE_APPEND_READY',
    'Process'
)
$previousValidationTrace = [Environment]::GetEnvironmentVariable(
    'AGENT_BRIDGE_TEST_VALIDATION_TRACE',
    'Process'
)
$previousFailOnFullValidation = [Environment]::GetEnvironmentVariable(
    'AGENT_BRIDGE_TEST_FAIL_ON_FULL_VALIDATION',
    'Process'
)
$previousCheckpointUpdateFailure = [Environment]::GetEnvironmentVariable(
    'AGENT_BRIDGE_TEST_CHECKPOINT_UPDATE_FAILURE',
    'Process'
)
$previousCheckpointInvalidationFailure = [Environment]::GetEnvironmentVariable(
    'AGENT_BRIDGE_TEST_CHECKPOINT_INVALIDATION_FAILURE',
    'Process'
)
$previousAfterCanonicalReady = [Environment]::GetEnvironmentVariable(
    'AGENT_BRIDGE_TEST_AFTER_CANONICAL_BEFORE_CHECKPOINT',
    'Process'
)
$previousWalCleanupFailure = [Environment]::GetEnvironmentVariable(
    'AGENT_BRIDGE_TEST_WAL_CLEANUP_FAILURE',
    'Process'
)
$previousAuxiliaryAppendFailure = [Environment]::GetEnvironmentVariable(
    'AGENT_BRIDGE_TEST_AUXILIARY_APPEND_FAILURE_AFTER_BYTES',
    'Process'
)

function New-TestBridgeRoot {
    param([Parameter(Mandatory)] [string] $Name)
    $root = Join-Path $tempRoot $Name
    [void](New-Item -ItemType Directory -Path (Join-Path $root 'shared') -Force)
    [void](New-Item -ItemType Directory -Path (Join-Path $root 'spool') -Force)
    return $root
}

function Get-BridgeTestFileLength {
    param([Parameter(Mandatory)] [string] $Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return [int64]0 }
    return [int64](Get-Item -LiteralPath $Path).Length
}

function Get-BridgeTestSha256Hex {
    param([Parameter(Mandatory)] [byte[]] $Bytes)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($Bytes))).Replace(
            '-', ''
        ).ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Write-TestWal {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $Text
    )
    $encoding = New-Object System.Text.UTF8Encoding($false, $true)
    [System.IO.File]::WriteAllText($Path, ($Text + [char]10), $encoding)
}

function Stop-ProcessAfterMutexAcquisition {
    param(
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [string] $HelperPath,
        [Parameter(Mandatory)] [string] $ReadyPath
    )

    $enginePath = (Get-Process -Id $PID).Path
    $process = Start-Process -FilePath $enginePath -ArgumentList @(
        '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', "`"$HelperPath`"", '-Name', "`"$Name`"",
        '-ReadyPath', "`"$ReadyPath`""
    ) -PassThru -WindowStyle Hidden
    try {
        for ($attempt = 0; $attempt -lt 200; $attempt++) {
            if (Test-Path -LiteralPath $ReadyPath -PathType Leaf) { break }
            if ($process.HasExited) { break }
            Start-Sleep -Milliseconds 25
        }
        if (-not (Test-Path -LiteralPath $ReadyPath -PathType Leaf)) {
            return $false
        }
        Stop-Process -Id $process.Id -Force
        $process.WaitForExit()
        return $true
    } finally {
        if (-not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            $process.WaitForExit()
        }
    }
}

try {
    Write-Host 'Bridge spool replay smoke test' -ForegroundColor Cyan
    [void](New-Item -ItemType Directory -Path (Join-Path $tempRoot 'shared') -Force)
    [void](New-Item -ItemType Directory -Path (Join-Path $tempRoot 'spool') -Force)
    $eventsPath = Join-Path (Join-Path $tempRoot 'shared') 'events.jsonl'

    # Long-hold and abandonment cases use exact script copies whose V1 names
    # carry a unique Local namespace suffix. This exercises the same fence
    # without blocking the live machine-wide bridge mutex for ten seconds.
    $isolationId = [guid]::NewGuid().ToString('N')
    $isolatedBin = Join-Path $tempRoot 'isolated-bin'
    [void](New-Item -ItemType Directory -Path $isolatedBin -Force)
    $isolatedAppendName = "Local\WaggleDanceBridgeAppendV1-$isolationId"
    $isolatedReplayName = "Local\WaggleDanceBridgeSpoolReplayV1-$isolationId"
    $isolatedWriter = Join-Path $isolatedBin 'Write-AgentEvent.ps1'
    $isolatedReplay = Join-Path $isolatedBin 'Restore-BridgeSpool.ps1'
    $raceReplay = Join-Path $isolatedBin 'Restore-BridgeSpool-Race.ps1'
    $enumerationReplay = Join-Path $isolatedBin 'Restore-BridgeSpool-Enumeration.ps1'
    $leaseReplay = Join-Path $isolatedBin 'Restore-BridgeSpool-Lease.ps1'
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $writerSource = [System.IO.File]::ReadAllText($writerScript).Replace(
        'Global\WaggleDanceBridgeAppendV1',
        $isolatedAppendName
    )
    $replaySource = [System.IO.File]::ReadAllText($replayScript)
    $bulkGuardNeedle = 'if (-not $targetedReplay) {'
    $bulkGuardIndex = $replaySource.IndexOf(
        $bulkGuardNeedle,
        [StringComparison]::Ordinal
    )
    if ($bulkGuardIndex -lt 0) {
        throw 'test setup could not locate the production bulk-replay guard'
    }
    # Legacy recovery mechanics still need isolated coverage. Only this copied
    # test script bypasses the production entry-point refusal; the tracked
    # script itself is exercised below and must fail closed without a target.
    $replaySource = $replaySource.Remove(
        $bulkGuardIndex,
        $bulkGuardNeedle.Length
    ).Insert($bulkGuardIndex, 'if ($false) {')
    $replaySource = $replaySource.Replace(
        'Global\WaggleDanceBridgeAppendV1',
        $isolatedAppendName
    )
    $replaySource = $replaySource.Replace(
        'Global\WaggleDanceBridgeSpoolReplayV1',
        $isolatedReplayName
    )
    [System.IO.File]::WriteAllText($isolatedWriter, $writerSource, $utf8)
    [System.IO.File]::WriteAllText($isolatedReplay, $replaySource, $utf8)

    $bulkGuardRoot = New-TestBridgeRoot -Name 'bulk-replay-disabled'
    $bulkGuardSpool = Join-Path (Join-Path $bulkGuardRoot 'spool') `
        'failed-append-smoke-1-20260813T115900000-1233-00000000000000000000000000000000.jsonl'
    $bulkGuardEvent = '{"ts_utc":"2026-08-13T11:59:00Z","agent":"smoke-1","type":"message","task_id":"bulk-guard","status":"info","message":"must-not-replay"}'
    Write-TestWal -Path $bulkGuardSpool -Text $bulkGuardEvent
    $bulkGuardError = ''
    try { & $replayScript -BridgeRoot $bulkGuardRoot | Out-Null }
    catch { $bulkGuardError = $_.Exception.Message }
    Add-Check -Name 'untargeted bulk replay is disabled fail closed' -Passed (
        ($bulkGuardError -match 'untargeted bulk replay is disabled') -and
        (Test-Path -LiteralPath $bulkGuardSpool -PathType Leaf) -and
        (-not (Test-Path -LiteralPath (
            Join-Path $bulkGuardRoot 'shared/events.jsonl')))
    ) -Detail "error=$bulkGuardError"
    $appendWaitNeedle = 'try { $appendAcquired = $appendMutex.WaitOne(10000) }'
    if (-not $replaySource.Contains($appendWaitNeedle)) {
        throw 'race smoke could not locate the outer append mutex wait'
    }
    $appendWaitSignal = (
        "[System.IO.File]::WriteAllText(" +
        "[Environment]::GetEnvironmentVariable(" +
        "'AGENT_BRIDGE_TEST_APPEND_WAIT_READY', 'Process'), 'ready')"
    )
    $raceReplaySource = $replaySource.Replace(
        $appendWaitNeedle,
        ($appendWaitSignal + [Environment]::NewLine + '        ' + $appendWaitNeedle)
    )
    [System.IO.File]::WriteAllText($raceReplay, $raceReplaySource, $utf8)
    $enumerationNeedle = '    $pendingFiles = @(if (-not $targetedReplay) {'
    if (-not $replaySource.Contains($enumerationNeedle)) {
        throw 'enumeration smoke could not locate fail-closed spool enumeration'
    }
    $enumerationReplaySource = $replaySource.Replace(
        $enumerationNeedle,
        (
            "    Write-Error 'simulated spool enumeration failure' " +
            "-ErrorAction Stop" + [Environment]::NewLine +
            $enumerationNeedle
        )
    )
    [System.IO.File]::WriteAllText(
        $enumerationReplay,
        $enumerationReplaySource,
        $utf8
    )
    $leaseBarrierNeedle = `
        '    # AppendV1 remains owned across WAL discovery/recovery, live-log scan,'
    if (-not $replaySource.Contains($leaseBarrierNeedle)) {
        throw 'lease smoke could not locate the post-AppendV1 acquisition point'
    }
    $leaseBarrier = @'
    $leaseReadyPath = [Environment]::GetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_AFTER_APPEND_READY', 'Process'
    )
    if ($leaseReadyPath) {
        [System.IO.File]::WriteAllText($leaseReadyPath, 'ready')
        $leaseReleased = $false
        for ($leaseAttempt = 0; $leaseAttempt -lt 400; $leaseAttempt++) {
            if (Test-Path -LiteralPath "$leaseReadyPath.release" -PathType Leaf) {
                $leaseReleased = $true
                break
            }
            Start-Sleep -Milliseconds 25
        }
        if (-not $leaseReleased) {
            throw 'lease smoke timed out while owning AppendV1'
        }
    }
'@
    $leaseReplaySource = $replaySource.Replace(
        $leaseBarrierNeedle,
        ($leaseBarrier + [Environment]::NewLine + $leaseBarrierNeedle)
    )
    [System.IO.File]::WriteAllText($leaseReplay, $leaseReplaySource, $utf8)
    $abandonHelper = Join-Path $isolatedBin 'Hold-Mutex.ps1'
    $abandonSource = @'
param([string] $Name, [string] $ReadyPath)
$ErrorActionPreference = 'Stop'
$mutex = New-Object System.Threading.Mutex($false, $Name)
if (-not $mutex.WaitOne(10000)) { exit 2 }
[System.IO.File]::WriteAllText($ReadyPath, 'ready')
Start-Sleep -Seconds 60
'@
    [System.IO.File]::WriteAllText($abandonHelper, $abandonSource, $utf8)

    # 1. Empty spool -> no-op
    $out = & $isolatedReplay -BridgeRoot $tempRoot
    Add-Check -Name 'empty spool is a no-op' -Passed ($out -match 'nothing to replay')

    # 2. A valid spooled event replays into the shared log and archives
    $event = '{"ts_utc":"2026-07-02T10:00:00Z","agent":"fable-5","type":"message","task_id":"spool-replay-smoke","status":"info","message":"recovered"}'
    $spoolFile = Join-Path (Join-Path $tempRoot 'spool') 'failed-append-fable-5-20260702T100000000-1234.jsonl'
    Write-TestWal -Path $spoolFile -Text $event

    $out = & $isolatedReplay -BridgeRoot $tempRoot
    Add-Check -Name 'replay reports one replayed' -Passed ($out -match 'replayed=1 deduped=0 failed=0')
    $logged = Get-Content -LiteralPath $eventsPath -Raw -Encoding UTF8
    Add-Check -Name 'event appended to shared log' -Passed ($logged -match 'spool-replay-smoke')
    Add-Check -Name 'spool file archived' -Passed (
        (-not (Test-Path -LiteralPath $spoolFile)) -and
        (Test-Path -LiteralPath (Join-Path (Join-Path (Join-Path $tempRoot 'spool') 'replayed') (Split-Path -Leaf $spoolFile)))
    )

    # 3. Idempotent rerun -> nothing to replay
    $out = & $isolatedReplay -BridgeRoot $tempRoot
    Add-Check -Name 'rerun is a no-op' -Passed ($out -match 'nothing to replay')

    # An existing same-name archive is immutable. Preserve both records under
    # distinct names instead of allowing Move-Item -Force to replace history.
    $archiveCollisionRoot = New-TestBridgeRoot -Name 'archive-name-collision'
    $archiveCollisionSpoolDir = Join-Path $archiveCollisionRoot 'spool'
    $archiveCollisionSpool = Join-Path $archiveCollisionSpoolDir `
        'failed-append-smoke-1-archive-collision.jsonl'
    $archiveCollisionEvent = '{"ts_utc":"2026-07-02T10:30:00Z","agent":"smoke-1","type":"message","task_id":"archive-collision","status":"info","message":"new-record"}'
    Write-TestWal -Path $archiveCollisionSpool -Text $archiveCollisionEvent
    [byte[]]$archiveCollisionWalBytes = [System.IO.File]::ReadAllBytes(
        $archiveCollisionSpool
    )
    $archiveCollisionDir = Join-Path $archiveCollisionSpoolDir 'replayed'
    [void](New-Item -ItemType Directory -Path $archiveCollisionDir -Force)
    $archiveCollisionExisting = Join-Path $archiveCollisionDir `
        (Split-Path -Leaf $archiveCollisionSpool)
    [byte[]]$archiveCollisionOldBytes = $utf8.GetBytes('immutable-old-archive')
    [System.IO.File]::WriteAllBytes(
        $archiveCollisionExisting,
        $archiveCollisionOldBytes
    )
    $archiveCollisionOldHash = (Get-FileHash `
        -LiteralPath $archiveCollisionExisting -Algorithm SHA256).Hash
    $archiveCollisionOut = & $isolatedReplay -BridgeRoot $archiveCollisionRoot
    $archiveCollisionOldHashAfter = (Get-FileHash `
        -LiteralPath $archiveCollisionExisting -Algorithm SHA256).Hash
    $archiveCollisionCopies = @(
        Get-ChildItem -LiteralPath $archiveCollisionDir -File |
            Where-Object {
                $_.Name -like (
                    (Split-Path -Leaf $archiveCollisionSpool) +
                    '.archive-collision.*'
                )
            }
    )
    $archiveCollisionNewPreserved = (
        $archiveCollisionCopies.Count -eq 1 -and
        [Convert]::ToBase64String(
            [System.IO.File]::ReadAllBytes($archiveCollisionCopies[0].FullName)
        ) -ceq [Convert]::ToBase64String($archiveCollisionWalBytes)
    )
    Add-Check -Name 'same-name archive collision preserves old and new WAL bytes' -Passed (
        ($archiveCollisionOut -match 'replayed=1') -and
        (-not (Test-Path -LiteralPath $archiveCollisionSpool)) -and
        $archiveCollisionOldHash -ceq $archiveCollisionOldHashAfter -and
        [Convert]::ToBase64String(
            [System.IO.File]::ReadAllBytes($archiveCollisionExisting)
        ) -ceq [Convert]::ToBase64String($archiveCollisionOldBytes) -and
        $archiveCollisionNewPreserved -and
        ([System.IO.File]::ReadAllText(
            (Join-Path $archiveCollisionRoot 'shared/events.jsonl')
        ) -match 'archive-collision')
    ) -Detail (
        "out=$archiveCollisionOut oldHash=$archiveCollisionOldHashAfter " +
        "copies=$($archiveCollisionCopies.Count)"
    )

    # 4. Malformed spool file fails loud, stays in place, and never appends.
    $badFile = Join-Path (Join-Path $tempRoot 'spool') 'failed-append-fable-5-20260702T110000000-9.jsonl'
    Write-TestWal -Path $badFile -Text '{not json'
    $before = [Convert]::ToBase64String([System.IO.File]::ReadAllBytes($eventsPath))
    $badError = ''
    try { & $isolatedReplay -BridgeRoot $tempRoot 3>$null | Out-Null }
    catch { $badError = $_.Exception.Message }
    $after = [Convert]::ToBase64String([System.IO.File]::ReadAllBytes($eventsPath))
    Add-Check -Name 'malformed file fails loud and is kept' -Passed (
        ($badError -match 'malformed JSON') -and
        (Test-Path -LiteralPath $badFile) -and ($before -ceq $after)
    ) -Detail "error=$badError"

    # 5. Same-semantic records with distinct identity bytes both survive.
    Remove-Item -LiteralPath $badFile -Force
    $retryCopy = '{"ts_utc":"2026-07-02T10:00:05Z","agent":"fable-5","type":"message","task_id":"spool-replay-smoke","status":"info","message":"dup-signal"}'
    [System.IO.File]::AppendAllText(
        $eventsPath,
        ($retryCopy + [char]10),
        $utf8
    )
    $dupSpool = Join-Path (Join-Path $tempRoot 'spool') 'failed-append-fable-5-20260702T100001000-77.jsonl'
    # The spooled FAILED attempt: same signal, OLDER ts + different pid.
    Write-TestWal -Path $dupSpool -Text '{"ts_utc":"2026-07-02T10:00:01Z","agent":"fable-5","type":"message","task_id":"spool-replay-smoke","status":"info","message":"dup-signal"}'
    $before = (Get-Content -LiteralPath $eventsPath -Encoding UTF8).Count
    $out = & $isolatedReplay -BridgeRoot $tempRoot
    $after = (Get-Content -LiteralPath $eventsPath -Encoding UTF8).Count
    Add-Check -Name 'distinct same-semantic records both survive' -Passed (
        ($out -match 'replayed=1 deduped=0') -and ($after -eq ($before + 1)) -and
        (-not (Test-Path -LiteralPath $dupSpool))
    ) -Detail "before=$before after=$after out=$out"

    $exactSpool = Join-Path (Join-Path $tempRoot 'spool') `
        'failed-append-fable-5-exact-duplicate.jsonl'
    Write-TestWal -Path $exactSpool -Text $retryCopy
    $before = @(Get-Content -LiteralPath $eventsPath -Encoding UTF8).Count
    $out = & $isolatedReplay -BridgeRoot $tempRoot
    $after = @(Get-Content -LiteralPath $eventsPath -Encoding UTF8).Count
    Add-Check -Name 'exact WAL record dedups idempotently' -Passed (
        ($out -match 'replayed=0 deduped=1') -and
        $after -eq $before -and (-not (Test-Path -LiteralPath $exactSpool))
    ) -Detail "before=$before after=$after out=$out"

    $withinWalEvent = '{"ts_utc":"2026-07-02T10:00:06Z","agent":"fable-5","type":"message","task_id":"within-wal-dedup","status":"info","message":"exact-row-twice"}'
    $withinWalSpool = Join-Path (Join-Path $tempRoot 'spool') `
        'failed-append-fable-5-within-wal-duplicate.jsonl'
    [System.IO.File]::WriteAllText(
        $withinWalSpool,
        ($withinWalEvent + [char]10 + $withinWalEvent + [char]10),
        $utf8
    )
    $before = @(Get-Content -LiteralPath $eventsPath -Encoding UTF8).Count
    $out = & $isolatedReplay -BridgeRoot $tempRoot
    $after = @(Get-Content -LiteralPath $eventsPath -Encoding UTF8).Count
    Add-Check -Name 'exact duplicate rows inside one WAL append once' -Passed (
        ($out -match 'replayed=1 deduped=1') -and
        $after -eq ($before + 1) -and
        (-not (Test-Path -LiteralPath $withinWalSpool))
    ) -Detail "before=$before after=$after out=$out"

    # 6. Spool line missing core fields (no agent) is skipped and kept
    $noAgent = Join-Path (Join-Path $tempRoot 'spool') 'failed-append-x-20260702T120000000-5.jsonl'
    Write-TestWal -Path $noAgent -Text '{"ts_utc":"2026-07-02T12:00:00Z","type":"message","task_id":"t","status":"info"}'
    $noAgentError = ''
    try { & $isolatedReplay -BridgeRoot $tempRoot 3>$null | Out-Null }
    catch { $noAgentError = $_.Exception.Message }
    Add-Check -Name 'missing-core-field WAL fails loud and is kept' -Passed (
        ($noAgentError -match 'missing core field') -and
        (Test-Path -LiteralPath $noAgent)
    ) -Detail "error=$noAgentError"
    Remove-Item -LiteralPath $noAgent -Force

    # 7. Concurrent replay guard exits without consuming spool files.
    $guardFile = Join-Path (Join-Path $tempRoot 'spool') 'failed-append-guard-20260702T130000000-6.jsonl'
    Write-TestWal -Path $guardFile -Text $event
    $guardMutex = $null
    $guardAcquired = $false
    try {
        $guardMutex = New-Object System.Threading.Mutex(
            $false,
            $isolatedReplayName
        )
        $guardAcquired = $guardMutex.WaitOne(0)
        if (-not $guardAcquired) {
            Add-Check -Name 'concurrent replay guard setup' -Passed $false -Detail 'could not acquire replay mutex'
        } else {
            $out = & powershell -NoProfile -ExecutionPolicy Bypass `
                -File $isolatedReplay -BridgeRoot $tempRoot
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
    Write-TestWal -Path $spoolFile -Text $event
    $out = & $isolatedReplay -BridgeRoot $tempRoot -DryRun
    Add-Check -Name 'dry run lists but keeps file' -Passed (
        (($out -match 'would archive as exact duplicate') -or ($out -match 'would replay')) -and (Test-Path -LiteralPath $spoolFile)
    )

    # 8b. Targeted replay is bound to one exact writer leaf and immutable WAL
    # bytes. Unrelated final/pending files are never parsed or mutated.
    $targetRoot = New-TestBridgeRoot -Name 'targeted-replay'
    $targetName = `
        'failed-append-smoke-1-20260813T120000000-1234-11111111111111111111111111111111.jsonl'
    $otherName = `
        'failed-append-smoke-2-20260813T120000001-1235-22222222222222222222222222222222.jsonl'
    $malformedOtherName = `
        'failed-append-smoke-3-20260813T120000002-1236-33333333333333333333333333333333.jsonl'
    $targetPath = Join-Path (Join-Path $targetRoot 'spool') $targetName
    $otherPath = Join-Path (Join-Path $targetRoot 'spool') $otherName
    $malformedOtherPath = Join-Path `
        (Join-Path $targetRoot 'spool') $malformedOtherName
    $targetEvent = $event.Replace('"message":"recovered"', '"message":"targeted"')
    $otherEvent = $event.Replace('"message":"recovered"', '"message":"unrelated"')
    Write-TestWal -Path $targetPath -Text $targetEvent
    Write-TestWal -Path $otherPath -Text $otherEvent
    [System.IO.File]::WriteAllText($malformedOtherPath, "{broken}`n", $utf8)
    $targetHash = Get-BridgeTestSha256Hex -Bytes (
        [System.IO.File]::ReadAllBytes($targetPath)
    )
    $targetDryBefore = [Convert]::ToBase64String(
        [System.IO.File]::ReadAllBytes($targetPath)
    )
    $otherDryBefore = [Convert]::ToBase64String(
        [System.IO.File]::ReadAllBytes($otherPath)
    )
    $malformedDryBefore = [Convert]::ToBase64String(
        [System.IO.File]::ReadAllBytes($malformedOtherPath)
    )
    $targetDryOut = & $isolatedReplay -BridgeRoot $targetRoot -DryRun `
        -SpoolFile $targetName -ExpectedSpoolSha256 $targetHash
    $targetEventsPath = Join-Path $targetRoot 'shared/events.jsonl'
    Add-Check -Name 'targeted dry run is byte-inert and names only target' `
        -Passed (
            ($targetDryOut -match [regex]::Escape($targetName)) -and
            ($targetDryOut -notmatch [regex]::Escape($otherName)) -and
            ($targetDryBefore -ceq [Convert]::ToBase64String(
                [System.IO.File]::ReadAllBytes($targetPath))) -and
            ($otherDryBefore -ceq [Convert]::ToBase64String(
                [System.IO.File]::ReadAllBytes($otherPath))) -and
            ($malformedDryBefore -ceq [Convert]::ToBase64String(
                [System.IO.File]::ReadAllBytes($malformedOtherPath))) -and
            (-not (Test-Path -LiteralPath $targetEventsPath))
        ) -Detail "out=$targetDryOut"

    $targetOut = & $isolatedReplay -BridgeRoot $targetRoot `
        -SpoolFile $targetName -ExpectedSpoolSha256 $targetHash
    $targetArchive = Join-Path `
        (Join-Path (Join-Path $targetRoot 'spool') 'replayed') $targetName
    Add-Check -Name 'targeted replay ignores unrelated valid and malformed WALs' `
        -Passed (
            ($targetOut -match 'replayed=1 deduped=0 failed=0') -and
            (-not (Test-Path -LiteralPath $targetPath)) -and
            (Test-Path -LiteralPath $targetArchive) -and
            (Test-Path -LiteralPath $otherPath) -and
            (Test-Path -LiteralPath $malformedOtherPath) -and
            ([System.IO.File]::ReadAllText($targetEventsPath) -ceq
                ($targetEvent + [char]10))
        ) -Detail "out=$targetOut"
    $targetAfterFirst = [Convert]::ToBase64String(
        [System.IO.File]::ReadAllBytes($targetEventsPath)
    )
    $targetSecondError = ''
    try {
        & $isolatedReplay -BridgeRoot $targetRoot `
            -SpoolFile $targetName -ExpectedSpoolSha256 $targetHash | Out-Null
    } catch { $targetSecondError = $_.Exception.Message }
    Add-Check -Name 'targeted replay never falls back after target archive' `
        -Passed (
            ($targetSecondError -match 'not found by exact leaf name') -and
            ($targetAfterFirst -ceq [Convert]::ToBase64String(
                [System.IO.File]::ReadAllBytes($targetEventsPath))) -and
            (Test-Path -LiteralPath $otherPath) -and
            (Test-Path -LiteralPath $malformedOtherPath)
        ) -Detail "error=$targetSecondError"

    $validationRoot = New-TestBridgeRoot -Name 'targeted-validation'
    $validationName = `
        'failed-append-smoke-1-20260813T121000000-2234-44444444444444444444444444444444.jsonl'
    $validationPath = Join-Path `
        (Join-Path $validationRoot 'spool') $validationName
    Write-TestWal -Path $validationPath -Text $targetEvent
    $validationHash = Get-BridgeTestSha256Hex -Bytes (
        [System.IO.File]::ReadAllBytes($validationPath)
    )
    $validationBefore = [Convert]::ToBase64String(
        [System.IO.File]::ReadAllBytes($validationPath)
    )
    $targetParameterCases = @(
        [pscustomobject]@{
            Name = 'name-without-hash'
            Parameters = @{ SpoolFile = $validationName }
            Error = 'must be supplied together'
        },
        [pscustomobject]@{
            Name = 'hash-without-name'
            Parameters = @{ ExpectedSpoolSha256 = $validationHash }
            Error = 'must be supplied together'
        },
        [pscustomobject]@{
            Name = 'wrong-hash'
            Parameters = @{
                SpoolFile = $validationName
                ExpectedSpoolSha256 = ('0' * 64)
            }
            Error = 'does not match'
        },
        [pscustomobject]@{
            Name = 'uppercase-hash'
            Parameters = @{
                SpoolFile = $validationName
                ExpectedSpoolSha256 = $validationHash.ToUpperInvariant()
            }
            Error = '64 lowercase hex'
        },
        [pscustomobject]@{
            Name = 'rooted-name'
            Parameters = @{
                SpoolFile = ('C:\outside\' + $validationName)
                ExpectedSpoolSha256 = $validationHash
            }
            Error = 'exact canonical failed-append leaf'
        },
        [pscustomobject]@{
            Name = 'separator-name'
            Parameters = @{
                SpoolFile = ('..\' + $validationName)
                ExpectedSpoolSha256 = $validationHash
            }
            Error = 'exact canonical failed-append leaf'
        },
        [pscustomobject]@{
            Name = 'case-drift-name'
            Parameters = @{
                SpoolFile = $validationName.Replace(
                    'failed-append', 'Failed-append')
                ExpectedSpoolSha256 = $validationHash
            }
            Error = 'exact canonical failed-append leaf'
        },
        [pscustomobject]@{
            Name = 'legacy-name'
            Parameters = @{
                SpoolFile = `
                    'failed-append-smoke-1-20260813T121000000-2234.jsonl'
                ExpectedSpoolSha256 = $validationHash
            }
            Error = 'exact canonical failed-append leaf'
        },
        [pscustomobject]@{
            Name = 'missing-target'
            Parameters = @{
                SpoolFile = $validationName.Replace(
                    '44444444444444444444444444444444',
                    '55555555555555555555555555555555')
                ExpectedSpoolSha256 = $validationHash
            }
            Error = 'not found by exact leaf name'
        }
    )
    $targetParameterPassed = $true
    $targetParameterDetails = New-Object 'System.Collections.Generic.List[string]'
    foreach ($case in $targetParameterCases) {
        $caseError = ''
        $caseParameters = @{} + $case.Parameters
        try {
            & $isolatedReplay -BridgeRoot $validationRoot @caseParameters |
                Out-Null
        } catch { $caseError = $_.Exception.Message }
        $casePassed = (
            ($caseError -match [regex]::Escape([string]$case.Error)) -and
            ($validationBefore -ceq [Convert]::ToBase64String(
                [System.IO.File]::ReadAllBytes($validationPath))) -and
            (-not (Test-Path -LiteralPath (
                Join-Path $validationRoot 'shared/events.jsonl')))
        )
        if (-not $casePassed) { $targetParameterPassed = $false }
        $targetParameterDetails.Add(
            "$($case.Name):passed=$casePassed error=$caseError"
        )
    }
    Add-Check -Name 'targeted selector and digest inputs fail closed' `
        -Passed $targetParameterPassed `
        -Detail ($targetParameterDetails -join ' | ')

    $pendingTargetRoot = New-TestBridgeRoot -Name 'targeted-pending-counterpart'
    $pendingTargetPath = Join-Path `
        (Join-Path $pendingTargetRoot 'spool') $validationName
    $pendingCounterpart = Join-Path `
        (Join-Path $pendingTargetRoot 'spool') ('.' + $validationName + '.pending')
    Write-TestWal -Path $pendingTargetPath -Text $targetEvent
    Write-TestWal -Path $pendingCounterpart -Text $targetEvent
    $pendingTargetHash = Get-BridgeTestSha256Hex -Bytes (
        [System.IO.File]::ReadAllBytes($pendingTargetPath)
    )
    $pendingTargetError = ''
    try {
        & $isolatedReplay -BridgeRoot $pendingTargetRoot `
            -SpoolFile $validationName `
            -ExpectedSpoolSha256 $pendingTargetHash | Out-Null
    } catch { $pendingTargetError = $_.Exception.Message }
    Add-Check -Name 'targeted pending counterpart blocks without mutation' `
        -Passed (
            ($pendingTargetError -match 'pending counterpart') -and
            (Test-Path -LiteralPath $pendingTargetPath) -and
            (Test-Path -LiteralPath $pendingCounterpart) -and
            (-not (Test-Path -LiteralPath (
                Join-Path $pendingTargetRoot 'shared/events.jsonl')))
        ) -Detail "error=$pendingTargetError"

    $pendingDirectoryRoot = New-TestBridgeRoot `
        -Name 'targeted-pending-directory-counterpart'
    $pendingDirectoryTarget = Join-Path `
        (Join-Path $pendingDirectoryRoot 'spool') $validationName
    $pendingDirectoryCounterpart = Join-Path `
        (Join-Path $pendingDirectoryRoot 'spool') `
        ('.' + $validationName + '.pending')
    Write-TestWal -Path $pendingDirectoryTarget -Text $targetEvent
    [void](New-Item -ItemType Directory -Path $pendingDirectoryCounterpart)
    $pendingDirectoryHash = Get-BridgeTestSha256Hex -Bytes (
        [System.IO.File]::ReadAllBytes($pendingDirectoryTarget)
    )
    $pendingDirectoryError = ''
    try {
        & $isolatedReplay -BridgeRoot $pendingDirectoryRoot `
            -SpoolFile $validationName `
            -ExpectedSpoolSha256 $pendingDirectoryHash | Out-Null
    } catch { $pendingDirectoryError = $_.Exception.Message }
    Add-Check -Name 'targeted pending directory counterpart blocks' -Passed (
        ($pendingDirectoryError -match 'pending counterpart') -and
        (Test-Path -LiteralPath $pendingDirectoryTarget -PathType Leaf) -and
        (Test-Path -LiteralPath $pendingDirectoryCounterpart -PathType Container) -and
        (-not (Test-Path -LiteralPath (
            Join-Path $pendingDirectoryRoot 'shared/events.jsonl')))
    ) -Detail "error=$pendingDirectoryError"

    $hardLinkRoot = New-TestBridgeRoot -Name 'targeted-hard-link'
    $hardLinkTarget = Join-Path (Join-Path $hardLinkRoot 'spool') $validationName
    $hardLinkAlias = Join-Path $hardLinkRoot 'outside-hard-link.jsonl'
    Write-TestWal -Path $hardLinkTarget -Text $targetEvent
    [void](New-Item -ItemType HardLink -Path $hardLinkAlias -Target $hardLinkTarget)
    $hardLinkHash = Get-BridgeTestSha256Hex -Bytes (
        [System.IO.File]::ReadAllBytes($hardLinkTarget)
    )
    $hardLinkError = ''
    try {
        & $isolatedReplay -BridgeRoot $hardLinkRoot `
            -SpoolFile $validationName `
            -ExpectedSpoolSha256 $hardLinkHash | Out-Null
    } catch { $hardLinkError = $_.Exception.Message }
    Add-Check -Name 'targeted hard-linked source fails closed' -Passed (
        ($hardLinkError -match 'exactly one hard-link name') -and
        (Test-Path -LiteralPath $hardLinkTarget -PathType Leaf) -and
        (Test-Path -LiteralPath $hardLinkAlias -PathType Leaf) -and
        (-not (Test-Path -LiteralPath (
            Join-Path $hardLinkRoot 'shared/events.jsonl')))
    ) -Detail "error=$hardLinkError"

    $archiveJunctionRoot = New-TestBridgeRoot -Name 'targeted-archive-junction'
    $archiveJunctionOutside = Join-Path $archiveJunctionRoot 'outside-archive'
    [void](New-Item -ItemType Directory -Path $archiveJunctionOutside)
    $archiveJunctionPath = Join-Path (Join-Path $archiveJunctionRoot 'spool') 'replayed'
    [void](New-Item -ItemType Junction -Path $archiveJunctionPath `
        -Target $archiveJunctionOutside)
    $archiveJunctionTarget = Join-Path `
        (Join-Path $archiveJunctionRoot 'spool') $validationName
    Write-TestWal -Path $archiveJunctionTarget -Text $targetEvent
    $archiveJunctionHash = Get-BridgeTestSha256Hex -Bytes (
        [System.IO.File]::ReadAllBytes($archiveJunctionTarget)
    )
    $archiveJunctionError = ''
    try {
        & $isolatedReplay -BridgeRoot $archiveJunctionRoot `
            -SpoolFile $validationName `
            -ExpectedSpoolSha256 $archiveJunctionHash | Out-Null
    } catch { $archiveJunctionError = $_.Exception.Message }
    Add-Check -Name 'targeted archive junction fails closed' -Passed (
        ($archiveJunctionError -match 'must not be a reparse point') -and
        (Test-Path -LiteralPath $archiveJunctionTarget -PathType Leaf) -and
        (@(Get-ChildItem -LiteralPath $archiveJunctionOutside -Force).Count -eq 0)
    ) -Detail "error=$archiveJunctionError"

    $sharedJunctionRoot = New-TestBridgeRoot -Name 'targeted-shared-junction'
    $sharedJunctionPath = Join-Path $sharedJunctionRoot 'shared'
    $sharedJunctionOutside = Join-Path $sharedJunctionRoot 'outside-shared'
    [System.IO.Directory]::Delete($sharedJunctionPath)
    [void](New-Item -ItemType Directory -Path $sharedJunctionOutside)
    [void](New-Item -ItemType Junction -Path $sharedJunctionPath `
        -Target $sharedJunctionOutside)
    $sharedJunctionTarget = Join-Path `
        (Join-Path $sharedJunctionRoot 'spool') $validationName
    Write-TestWal -Path $sharedJunctionTarget -Text $targetEvent
    $sharedJunctionHash = Get-BridgeTestSha256Hex -Bytes (
        [System.IO.File]::ReadAllBytes($sharedJunctionTarget)
    )
    $sharedJunctionError = ''
    try {
        & $isolatedReplay -BridgeRoot $sharedJunctionRoot `
            -SpoolFile $validationName `
            -ExpectedSpoolSha256 $sharedJunctionHash | Out-Null
    } catch { $sharedJunctionError = $_.Exception.Message }
    Add-Check -Name 'targeted shared junction fails closed' -Passed (
        ($sharedJunctionError -match 'must not be a reparse point') -and
        (Test-Path -LiteralPath $sharedJunctionTarget -PathType Leaf) -and
        (@(Get-ChildItem -LiteralPath $sharedJunctionOutside -Force).Count -eq 0)
    ) -Detail "error=$sharedJunctionError"

    $malformedTargetRoot = New-TestBridgeRoot -Name 'targeted-malformed'
    $malformedTargetPath = Join-Path `
        (Join-Path $malformedTargetRoot 'spool') $validationName
    [System.IO.File]::WriteAllText($malformedTargetPath, "{broken}`n", $utf8)
    $malformedTargetHash = Get-BridgeTestSha256Hex -Bytes (
        [System.IO.File]::ReadAllBytes($malformedTargetPath)
    )
    $malformedTargetBefore = [Convert]::ToBase64String(
        [System.IO.File]::ReadAllBytes($malformedTargetPath)
    )
    $malformedTargetError = ''
    try {
        & $isolatedReplay -BridgeRoot $malformedTargetRoot `
            -SpoolFile $validationName `
            -ExpectedSpoolSha256 $malformedTargetHash | Out-Null
    } catch { $malformedTargetError = $_.Exception.Message }
    Add-Check -Name 'selected malformed WAL fails closed' -Passed (
        ($malformedTargetError -match 'malformed JSON') -and
        ($malformedTargetBefore -ceq [Convert]::ToBase64String(
            [System.IO.File]::ReadAllBytes($malformedTargetPath))) -and
        (-not (Test-Path -LiteralPath (
            Join-Path $malformedTargetRoot 'shared/events.jsonl')))
    ) -Detail "error=$malformedTargetError"

    $partialTargetRoot = New-TestBridgeRoot -Name 'targeted-partial-dedup'
    $partialTargetPath = Join-Path `
        (Join-Path $partialTargetRoot 'spool') $validationName
    $partialEventsPath = Join-Path $partialTargetRoot 'shared/events.jsonl'
    $partialSecondEvent = $targetEvent.Replace(
        '"message":"targeted"', '"message":"targeted-second"'
    )
    [System.IO.File]::WriteAllText(
        $partialTargetPath,
        $targetEvent + [char]10 + $partialSecondEvent + [char]10,
        $utf8
    )
    [System.IO.File]::WriteAllText(
        $partialEventsPath, $targetEvent + [char]10, $utf8
    )
    $partialTargetHash = Get-BridgeTestSha256Hex -Bytes (
        [System.IO.File]::ReadAllBytes($partialTargetPath)
    )
    $partialTargetOut = & $isolatedReplay -BridgeRoot $partialTargetRoot `
        -SpoolFile $validationName `
        -ExpectedSpoolSha256 $partialTargetHash
    Add-Check -Name 'targeted multirow replay dedups and appends only missing rows' `
        -Passed (
            ($partialTargetOut -match 'replayed=1 deduped=1 failed=0') -and
            ([System.IO.File]::ReadAllText($partialEventsPath) -ceq
                ($targetEvent + [char]10 + $partialSecondEvent + [char]10)) -and
            (-not (Test-Path -LiteralPath $partialTargetPath))
        ) -Detail "out=$partialTargetOut"

    $canonicalLinkRoot = New-TestBridgeRoot -Name 'targeted-canonical-hard-link'
    $canonicalLinkTarget = Join-Path `
        (Join-Path $canonicalLinkRoot 'spool') $validationName
    $canonicalLinkEvents = Join-Path $canonicalLinkRoot 'shared/events.jsonl'
    $canonicalLinkAlias = Join-Path $canonicalLinkRoot 'old-canonical-alias.jsonl'
    $canonicalLinkSeed = $targetEvent.Replace(
        '"message":"targeted"', '"message":"canonical-link-seed"'
    )
    Write-TestWal -Path $canonicalLinkTarget -Text $targetEvent
    [System.IO.File]::WriteAllText(
        $canonicalLinkEvents, $canonicalLinkSeed + [char]10, $utf8
    )
    [void](New-Item -ItemType HardLink -Path $canonicalLinkAlias `
        -Target $canonicalLinkEvents)
    $canonicalLinkHash = Get-BridgeTestSha256Hex -Bytes (
        [System.IO.File]::ReadAllBytes($canonicalLinkTarget)
    )
    $canonicalLinkOut = & $isolatedReplay -BridgeRoot $canonicalLinkRoot `
        -SpoolFile $validationName `
        -ExpectedSpoolSha256 $canonicalLinkHash
    Add-Check -Name 'targeted canonical publish leaves old hard-link identity unchanged' `
        -Passed (
            ($canonicalLinkOut -match 'replayed=1 deduped=0 failed=0') -and
            ([System.IO.File]::ReadAllText($canonicalLinkEvents) -ceq
                ($canonicalLinkSeed + [char]10 + $targetEvent + [char]10)) -and
            ([System.IO.File]::ReadAllText($canonicalLinkAlias) -ceq
                ($canonicalLinkSeed + [char]10)) -and
            (-not (Test-Path -LiteralPath $canonicalLinkTarget))
        ) -Detail "out=$canonicalLinkOut"

    # 9. Mutex construction failures are hard failures. The writer durably
    #    publishes a spool; the replayer leaves its existing spool untouched.
    $constructionWriterRoot = New-TestBridgeRoot -Name 'construction-writer'
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_RUNTIME_ROOT', $constructionWriterRoot, 'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_MUTEX_CONSTRUCTION_FAILURE', 'Append', 'Process'
    )
    $writerConstructionError = ''
    try {
        & $isolatedWriter -Agent 'smoke-1' -Type status -Status open `
            -Message 'construction-failure-writer' -PayloadJson '{}' | Out-Null
    } catch {
        $writerConstructionError = $_.Exception.Message
    }
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_MUTEX_CONSTRUCTION_FAILURE', $null, 'Process'
    )
    $constructionWriterEvents = Join-Path $constructionWriterRoot 'shared/events.jsonl'
    $constructionWriterSpools = @(
        Get-ChildItem -LiteralPath (Join-Path $constructionWriterRoot 'spool') `
            -Filter 'failed-append-*.jsonl' -File -ErrorAction SilentlyContinue
    )
    $constructionPending = @(
        Get-ChildItem -LiteralPath (Join-Path $constructionWriterRoot 'spool') `
            -Filter '.*.pending' -File -Force -ErrorAction SilentlyContinue
    )
    $constructionSpoolText = if ($constructionWriterSpools.Count -eq 1) {
        [System.IO.File]::ReadAllText($constructionWriterSpools[0].FullName)
    } else { '' }
    Add-Check -Name 'writer construction failure spools without canonical append' -Passed (
        ($writerConstructionError -match 'construction failure') -and
        (Get-BridgeTestFileLength -Path $constructionWriterEvents) -eq 0 -and
        $constructionWriterSpools.Count -eq 1 -and
        $constructionPending.Count -eq 0 -and
        $constructionSpoolText -match 'construction-failure-writer'
    ) -Detail "error=$writerConstructionError spools=$($constructionWriterSpools.Count)"

    $constructionReplayRoot = New-TestBridgeRoot -Name 'construction-replay'
    $constructionReplaySpool = Join-Path `
        (Join-Path $constructionReplayRoot 'spool') `
        'failed-append-smoke-1-construction-1.jsonl'
    Write-TestWal -Path $constructionReplaySpool -Text $event
    $constructionReplayEvents = Join-Path $constructionReplayRoot 'shared/events.jsonl'
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_MUTEX_CONSTRUCTION_FAILURE', 'SpoolReplay', 'Process'
    )
    $replayConstructionError = ''
    try { & $isolatedReplay -BridgeRoot $constructionReplayRoot | Out-Null }
    catch { $replayConstructionError = $_.Exception.Message }
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_MUTEX_CONSTRUCTION_FAILURE', $null, 'Process'
    )
    Add-Check -Name 'replay mutex construction failure keeps spool' -Passed (
        ($replayConstructionError -match 'construction failure') -and
        (Test-Path -LiteralPath $constructionReplaySpool) -and
        (Get-BridgeTestFileLength -Path $constructionReplayEvents) -eq 0
    ) -Detail "error=$replayConstructionError"

    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_MUTEX_CONSTRUCTION_FAILURE', 'Append', 'Process'
    )
    $innerConstructionError = ''
    try { & $isolatedReplay -BridgeRoot $constructionReplayRoot 3>$null | Out-Null }
    catch { $innerConstructionError = $_.Exception.Message }
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_MUTEX_CONSTRUCTION_FAILURE', $null, 'Process'
    )
    Add-Check -Name 'replayer append construction failure keeps spool' -Passed (
        ($innerConstructionError -match 'construction failure') -and
        (Test-Path -LiteralPath $constructionReplaySpool) -and
        (Get-BridgeTestFileLength -Path $constructionReplayEvents) -eq 0
    ) -Detail "error=$innerConstructionError"

    # 10. Enumeration and live-log validation failures are loud and leave both
    #     canonical bytes and every spool untouched.
    $enumerationRoot = New-TestBridgeRoot -Name 'enumeration-failure'
    $enumerationEvents = Join-Path $enumerationRoot 'shared/events.jsonl'
    $enumerationSpool = Join-Path `
        (Join-Path $enumerationRoot 'spool') `
        'failed-append-smoke-1-enumeration.jsonl'
    [System.IO.File]::WriteAllText(
        $enumerationEvents,
        ($event + [Environment]::NewLine),
        $utf8
    )
    Write-TestWal -Path $enumerationSpool -Text $event
    $enumerationBefore = [Convert]::ToBase64String(
        [System.IO.File]::ReadAllBytes($enumerationEvents)
    )
    $enumerationError = ''
    try { & $enumerationReplay -BridgeRoot $enumerationRoot | Out-Null }
    catch { $enumerationError = $_.Exception.Message }
    $enumerationAfter = [Convert]::ToBase64String(
        [System.IO.File]::ReadAllBytes($enumerationEvents)
    )
    Add-Check -Name 'spool enumeration failure is loud and fail closed' -Passed (
        ($enumerationError -match 'simulated spool enumeration failure') -and
        $enumerationBefore -ceq $enumerationAfter -and
        (Test-Path -LiteralPath $enumerationSpool)
    ) -Detail "error=$enumerationError"

    $liveLogCases = @(
        [pscustomobject]@{ Name = 'malformed'; Row = '{not-json}' },
        [pscustomobject]@{ Name = 'non-object'; Row = '[]' },
        [pscustomobject]@{ Name = 'missing-core'; Row = '{"type":"message"}' },
        [pscustomobject]@{
            Name = 'unrecognized-type'
            Row = $event.Replace(
                '"type":"message"',
                '"type":"other-unknown-type"'
            )
        },
        [pscustomobject]@{
            Name = 'unrecognized-bare-cr'
            Row = ($event + [char]13 + $event)
        }
    )
    $liveLogValidationPassed = $true
    $liveLogValidationDetails = New-Object 'System.Collections.Generic.List[string]'
    foreach ($liveCase in $liveLogCases) {
        $caseRoot = New-TestBridgeRoot -Name ("live-log-" + $liveCase.Name)
        $caseEvents = Join-Path $caseRoot 'shared/events.jsonl'
        $caseSpool = Join-Path `
            (Join-Path $caseRoot 'spool') `
            ("failed-append-smoke-1-live-log-$($liveCase.Name).jsonl")
        [System.IO.File]::WriteAllText(
            $caseEvents,
            ([string]$liveCase.Row + [Environment]::NewLine),
            $utf8
        )
        Write-TestWal -Path $caseSpool -Text $event
        $caseBefore = [Convert]::ToBase64String(
            [System.IO.File]::ReadAllBytes($caseEvents)
        )
        $caseError = ''
        try { & $isolatedReplay -BridgeRoot $caseRoot | Out-Null }
        catch { $caseError = $_.Exception.Message }
        $caseAfter = [Convert]::ToBase64String(
            [System.IO.File]::ReadAllBytes($caseEvents)
        )
        $caseArchive = Join-Path `
            (Join-Path (Join-Path $caseRoot 'spool') 'replayed') `
            (Split-Path -Leaf $caseSpool)
        $casePassed = (
            ($caseError -match 'canonical bridge log') -and
            $caseBefore -ceq $caseAfter -and
            (Test-Path -LiteralPath $caseSpool) -and
            (-not (Test-Path -LiteralPath $caseArchive))
        )
        if (-not $casePassed) { $liveLogValidationPassed = $false }
        $liveLogValidationDetails.Add(
            "$($liveCase.Name):passed=$casePassed error=$caseError"
        )
    }
    Add-Check -Name 'invalid canonical rows retain exact bytes and all spools' `
        -Passed $liveLogValidationPassed `
        -Detail ($liveLogValidationDetails -join ' | ')

    # 10b. The one historical bare-CR compatibility row is accepted only when
    # its normalized row hash and both event fingerprints match exactly. The
    # append-only canonical bytes remain untouched; reconstructed CRLF row keys
    # are used solely for exact WAL deduplication.
    $compatRoot = New-TestBridgeRoot -Name 'known-bare-cr-compat'
    $compatEvents = Join-Path $compatRoot 'shared/events.jsonl'
    $compatSpool = Join-Path $compatRoot 'spool/failed-append-compat.jsonl'
    $compatTask = 'production-liveness-reactivation-scout-2026-07-01-codex-tools-1-since-20260701t161039z'
    $compatFirst = (
        '{"ts_utc":"2026-07-01T16:45:30.4576368Z",' +
        '"agent":"codex-lead-1","type":"test","task_id":"' +
        $compatTask + '","status":"attention","message":"first"}'
    )
    $compatSecond = (
        '{"ts_utc":"2026-07-01T16:46:54.4324612Z",' +
        '"agent":"codex-lead-1","type":"message","task_id":"' +
        $compatTask + '","status":"bridge_log_repair_note",' +
        '"message":"second"}'
    )
    $compatRow = $compatFirst + [char]13 + $compatSecond
    [byte[]]$compatNormalizedBytes = $utf8.GetBytes($compatRow)
    $compatRowHash = Get-BridgeTestSha256Hex -Bytes $compatNormalizedBytes
    $productionCompatHash = `
        '53f863ac93dd977504346feddc382ccd65bafceb4aeaad2bba1765712190a0d3'
    $compatReplay = Join-Path $isolatedBin 'Restore-BridgeSpool-Compat.ps1'
    $compatReplaySource = $replaySource.Replace(
        $productionCompatHash,
        $compatRowHash
    )
    [System.IO.File]::WriteAllText($compatReplay, $compatReplaySource, $utf8)
    [System.IO.File]::WriteAllBytes(
        $compatEvents,
        $utf8.GetBytes($compatRow + [char]13 + [char]10)
    )
    [System.IO.File]::WriteAllBytes(
        $compatSpool,
        $utf8.GetBytes($compatFirst + [char]13 + [char]10)
    )
    $compatBefore = [Convert]::ToBase64String(
        [System.IO.File]::ReadAllBytes($compatEvents)
    )
    $compatOut = & $compatReplay -BridgeRoot $compatRoot
    $compatAfter = [Convert]::ToBase64String(
        [System.IO.File]::ReadAllBytes($compatEvents)
    )
    Add-Check -Name 'known bare-CR row expands only for exact dedup keys' -Passed (
        ($compatOut -match 'replayed=0 deduped=1 failed=0') -and
        $compatBefore -ceq $compatAfter -and
        (-not (Test-Path -LiteralPath $compatSpool))
    ) -Detail "out=$compatOut"

    $fingerprintRoot = New-TestBridgeRoot -Name 'bare-cr-fingerprint-mismatch'
    $fingerprintEvents = Join-Path $fingerprintRoot 'shared/events.jsonl'
    $fingerprintSpool = Join-Path `
        $fingerprintRoot 'spool/failed-append-fingerprint.jsonl'
    $wrongSecond = $compatSecond.Replace(
        '"bridge_log_repair_note"',
        '"bridge_log_repair_note_changed"'
    )
    $wrongRow = $compatFirst + [char]13 + $wrongSecond
    $wrongHash = Get-BridgeTestSha256Hex -Bytes ($utf8.GetBytes($wrongRow))
    $wrongReplay = Join-Path $isolatedBin `
        'Restore-BridgeSpool-FingerprintMismatch.ps1'
    [System.IO.File]::WriteAllText(
        $wrongReplay,
        $replaySource.Replace($productionCompatHash, $wrongHash),
        $utf8
    )
    [System.IO.File]::WriteAllBytes(
        $fingerprintEvents,
        $utf8.GetBytes($wrongRow + [char]13 + [char]10)
    )
    Write-TestWal -Path $fingerprintSpool -Text $event
    $fingerprintBefore = [Convert]::ToBase64String(
        [System.IO.File]::ReadAllBytes($fingerprintEvents)
    )
    $fingerprintError = ''
    try { & $wrongReplay -BridgeRoot $fingerprintRoot | Out-Null }
    catch { $fingerprintError = $_.Exception.Message }
    $fingerprintAfter = [Convert]::ToBase64String(
        [System.IO.File]::ReadAllBytes($fingerprintEvents)
    )
    Add-Check -Name 'hash-matched bare-CR fingerprint drift fails closed' -Passed (
        ($fingerprintError -match 'fingerprint does not match') -and
        $fingerprintBefore -ceq $fingerprintAfter -and
        (Test-Path -LiteralPath $fingerprintSpool)
    ) -Detail "error=$fingerprintError"

    $unknownTypeRoot = New-TestBridgeRoot -Name 'known-unknown-type-compat'
    $unknownTypeEvents = Join-Path $unknownTypeRoot 'shared/events.jsonl'
    $unknownTypeSpool = Join-Path `
        $unknownTypeRoot 'spool/failed-append-known-unknown.jsonl'
    $unknownTypeRow = '{"ts_utc":"2026-08-09T23:24:39.1546638Z","agent":"claude-rco-2","type":"totally-bogus-typo-type","task_id":"rco2-v8-typo-type-probe","status":"test_probe","severity":"","to":"","message":"adversarial probe against v8 patched writer","paths":[],"write_scope":[],"run_id":"wd-reboot-20260808T135923Z","pid":41920,"cwd":"C:\\Python\\project2\\.agent-bridge\\bin","payload":{},"role":"rco-security","agent_uuid":"76739997-0058-41a2-8514-78ff295537aa","session_id":"wd-reboot-20260808T135923Z","capabilities":["rco_review","security_review","adversarial_review","bridge_event","work_queue"]}'
    $productionUnknownTypeHash = `
        '056cbafc328c40441b85cf4f7c46dee0e540f222b94a96d8219025835bb0aa7f'
    $unknownTypeHash = Get-BridgeTestSha256Hex -Bytes (
        $utf8.GetBytes($unknownTypeRow)
    )
    if ($unknownTypeHash -cne $productionUnknownTypeHash) {
        throw 'known unknown-type production fixture digest drifted'
    }
    $unknownTypeReplay = $isolatedReplay
    [System.IO.File]::WriteAllBytes(
        $unknownTypeEvents,
        $utf8.GetBytes($unknownTypeRow + [char]13 + [char]10)
    )
    Write-TestWal -Path $unknownTypeSpool -Text $event
    $unknownTypeBefore = [Convert]::ToBase64String(
        [System.IO.File]::ReadAllBytes($unknownTypeEvents)
    )
    $unknownTypeOut = & $unknownTypeReplay `
        -BridgeRoot $unknownTypeRoot -DryRun
    $unknownTypeAfter = [Convert]::ToBase64String(
        [System.IO.File]::ReadAllBytes($unknownTypeEvents)
    )
    Add-Check -Name 'known unknown-type probe is scan-only compatible' -Passed (
        ($unknownTypeOut -match 'would replay') -and
        $unknownTypeBefore -ceq $unknownTypeAfter -and
        (Test-Path -LiteralPath $unknownTypeSpool)
    ) -Detail "out=$unknownTypeOut"

    Write-TestWal -Path $unknownTypeSpool -Text $unknownTypeRow
    $unknownTypeWalBefore = [Convert]::ToBase64String(
        [System.IO.File]::ReadAllBytes($unknownTypeSpool)
    )
    $unknownTypeWalError = ''
    try { & $unknownTypeReplay -BridgeRoot $unknownTypeRoot | Out-Null }
    catch { $unknownTypeWalError = $_.Exception.Message }
    $unknownTypeWalAfter = [Convert]::ToBase64String(
        [System.IO.File]::ReadAllBytes($unknownTypeSpool)
    )
    $unknownTypeCanonicalAfterWal = [Convert]::ToBase64String(
        [System.IO.File]::ReadAllBytes($unknownTypeEvents)
    )
    Add-Check -Name 'known unknown-type probe remains invalid as WAL input' `
        -Passed (
            ($unknownTypeWalError -match 'unknown event type') -and
            $unknownTypeWalBefore -ceq $unknownTypeWalAfter -and
            $unknownTypeBefore -ceq $unknownTypeCanonicalAfterWal -and
            (Test-Path -LiteralPath $unknownTypeSpool)
        ) -Detail "error=$unknownTypeWalError"

    $unknownFingerprintRoot = New-TestBridgeRoot -Name `
        'known-unknown-fingerprint-mismatch'
    $unknownFingerprintEvents = Join-Path `
        $unknownFingerprintRoot 'shared/events.jsonl'
    $unknownFingerprintSpool = Join-Path `
        $unknownFingerprintRoot 'spool/failed-append-unknown-fingerprint.jsonl'
    $wrongUnknownTypeRow = $unknownTypeRow.Replace(
        '"test_probe"',
        '"test_probe_changed"'
    )
    $wrongUnknownTypeHash = Get-BridgeTestSha256Hex -Bytes (
        $utf8.GetBytes($wrongUnknownTypeRow)
    )
    $wrongUnknownTypeReplay = Join-Path $isolatedBin `
        'Restore-BridgeSpool-KnownUnknownMismatch.ps1'
    [System.IO.File]::WriteAllText(
        $wrongUnknownTypeReplay,
        $replaySource.Replace(
            $productionUnknownTypeHash,
            $wrongUnknownTypeHash
        ),
        $utf8
    )
    [System.IO.File]::WriteAllBytes(
        $unknownFingerprintEvents,
        $utf8.GetBytes($wrongUnknownTypeRow + [char]13 + [char]10)
    )
    Write-TestWal -Path $unknownFingerprintSpool -Text $event
    $unknownFingerprintBefore = [Convert]::ToBase64String(
        [System.IO.File]::ReadAllBytes($unknownFingerprintEvents)
    )
    $unknownFingerprintError = ''
    try {
        & $wrongUnknownTypeReplay `
            -BridgeRoot $unknownFingerprintRoot -DryRun | Out-Null
    } catch { $unknownFingerprintError = $_.Exception.Message }
    $unknownFingerprintAfter = [Convert]::ToBase64String(
        [System.IO.File]::ReadAllBytes($unknownFingerprintEvents)
    )
    Add-Check -Name 'known unknown-type fingerprint drift fails closed' -Passed (
        ($unknownFingerprintError -match 'fingerprint does not match') -and
        $unknownFingerprintBefore -ceq $unknownFingerprintAfter -and
        (Test-Path -LiteralPath $unknownFingerprintSpool)
    ) -Detail "error=$unknownFingerprintError"

    # 11. Hold the isolated append V1 mutex beyond the production ten-second
    #     budget. Writer and replayer run concurrently and both fail closed.
    $timeoutWriterRoot = New-TestBridgeRoot -Name 'timeout-writer'
    $timeoutReplayRoot = New-TestBridgeRoot -Name 'timeout-replay'
    $timeoutReplaySpool = Join-Path `
        (Join-Path $timeoutReplayRoot 'spool') `
        'failed-append-smoke-1-timeout-1.jsonl'
    $timeoutEvent = '{"ts_utc":"2026-07-02T14:00:00Z","agent":"smoke-1","type":"message","task_id":"timeout-fence","status":"info","message":"timeout-replay"}'
    Write-TestWal -Path $timeoutReplaySpool -Text $timeoutEvent
    $holdMutex = New-Object System.Threading.Mutex($false, $isolatedAppendName)
    $holdAcquired = $false
    $writerJob = $null
    $replayJob = $null
    $timeoutElapsed = [TimeSpan]::Zero
    $timeoutWriterOutput = ''
    $timeoutReplayOutput = ''
    try {
        $holdAcquired = $holdMutex.WaitOne(0)
        if ($holdAcquired) {
            $clock = [Diagnostics.Stopwatch]::StartNew()
            $writerJob = Start-Job -ScriptBlock {
                param($ScriptPath, $Root)
                $env:AGENT_BRIDGE_RUNTIME_ROOT = $Root
                Remove-Item Env:AGENT_BRIDGE_TEST_MUTEX_CONSTRUCTION_FAILURE `
                    -ErrorAction SilentlyContinue
                try {
                    & $ScriptPath -Agent 'smoke-1' -Type status -Status open `
                        -Message 'timeout-writer' -PayloadJson '{}' | Out-Null
                    'unexpected-success'
                } catch { $_.Exception.Message }
            } -ArgumentList $isolatedWriter, $timeoutWriterRoot
            $replayJob = Start-Job -ScriptBlock {
                param($ScriptPath, $Root)
                Remove-Item Env:AGENT_BRIDGE_TEST_MUTEX_CONSTRUCTION_FAILURE `
                    -ErrorAction SilentlyContinue
                try { & $ScriptPath -BridgeRoot $Root 3>$null | Out-String }
                catch { $_.Exception.Message }
            } -ArgumentList $isolatedReplay, $timeoutReplayRoot
            @($writerJob, $replayJob) | Wait-Job | Out-Null
            $clock.Stop()
            $timeoutElapsed = $clock.Elapsed
            $timeoutWriterOutput = @(Receive-Job -Job $writerJob) -join ' '
            $timeoutReplayOutput = @(Receive-Job -Job $replayJob) -join ' '
        }
    } finally {
        if ($holdAcquired) { try { $holdMutex.ReleaseMutex() } catch {} }
        $holdMutex.Dispose()
        foreach ($job in @($writerJob, $replayJob)) {
            if ($null -ne $job) { Remove-Job -Job $job -Force -ErrorAction SilentlyContinue }
        }
    }
    $timeoutWriterSpools = @(
        Get-ChildItem -LiteralPath (Join-Path $timeoutWriterRoot 'spool') `
            -Filter 'failed-append-*.jsonl' -File -ErrorAction SilentlyContinue
    )
    Add-Check -Name 'append mutex held over ten seconds is fail closed' -Passed (
        $holdAcquired -and $timeoutElapsed.TotalSeconds -ge 9.5 -and
        (Get-BridgeTestFileLength -Path (Join-Path $timeoutWriterRoot 'shared/events.jsonl')) -eq 0 -and
        $timeoutWriterSpools.Count -eq 1 -and
        $timeoutWriterOutput -match 'mutex timeout' -and
        (Get-BridgeTestFileLength -Path (Join-Path $timeoutReplayRoot 'shared/events.jsonl')) -eq 0 -and
        (Test-Path -LiteralPath $timeoutReplaySpool) -and
        $timeoutReplayOutput -match 'append mutex unavailable'
    ) -Detail (
        "elapsed=$($timeoutElapsed.TotalSeconds) writer=$timeoutWriterOutput " +
        "replay=$timeoutReplayOutput"
    )

    # 12. Deterministic TOCTOU regression: the parent owns AppendV1, the
    #     replayer reaches its outer wait, and a same-semantic live event is
    #     appended before ownership transfers. The scan must occur afterward.
    $raceRoot = New-TestBridgeRoot -Name 'scan-dedup-race'
    $raceEvents = Join-Path $raceRoot 'shared/events.jsonl'
    $raceSpool = Join-Path `
        (Join-Path $raceRoot 'spool') `
        'failed-append-smoke-1-scan-dedup-race.jsonl'
    $raceSpooledEvent = '{"ts_utc":"2026-07-02T17:00:00Z","agent":"smoke-1","type":"message","task_id":"scan-dedup-race","status":"info","message":"same-semantic-event","pid":101}'
    $raceLiveEvent = $raceSpooledEvent
    Write-TestWal -Path $raceSpool -Text $raceSpooledEvent
    $raceReady = Join-Path $tempRoot 'scan-dedup-race.ready'
    $raceMutex = New-Object System.Threading.Mutex($false, $isolatedAppendName)
    $raceMutexAcquired = $false
    $raceMutexReleased = $false
    $raceJob = $null
    $raceOutput = ''
    $raceReachedWait = $false
    try {
        $raceMutexAcquired = $raceMutex.WaitOne(0)
        if ($raceMutexAcquired) {
            $raceJob = Start-Job -ScriptBlock {
                param($ScriptPath, $Root, $ReadyPath)
                $env:AGENT_BRIDGE_TEST_APPEND_WAIT_READY = $ReadyPath
                Remove-Item Env:AGENT_BRIDGE_TEST_MUTEX_CONSTRUCTION_FAILURE `
                    -ErrorAction SilentlyContinue
                try { & $ScriptPath -BridgeRoot $Root 3>$null | Out-String }
                catch { $_.Exception.Message }
            } -ArgumentList $raceReplay, $raceRoot, $raceReady
            for ($attempt = 0; $attempt -lt 400; $attempt++) {
                if (Test-Path -LiteralPath $raceReady -PathType Leaf) {
                    $raceReachedWait = $true
                    break
                }
                if ($raceJob.State -in @('Completed', 'Failed', 'Stopped')) { break }
                Start-Sleep -Milliseconds 25
            }
            if ($raceReachedWait) {
                [System.IO.File]::AppendAllText(
                    $raceEvents,
                    ($raceLiveEvent + [char]10),
                    $utf8
                )
            }
            $raceMutex.ReleaseMutex()
            $raceMutexReleased = $true
            $raceJob | Wait-Job | Out-Null
            $raceOutput = @(Receive-Job -Job $raceJob) -join ' '
        }
    } finally {
        if ($raceMutexAcquired -and -not $raceMutexReleased) {
            try { $raceMutex.ReleaseMutex() } catch {}
        }
        $raceMutex.Dispose()
        if ($null -ne $raceJob) {
            Remove-Job -Job $raceJob -Force -ErrorAction SilentlyContinue
        }
    }
    $raceLines = @()
    if (Test-Path -LiteralPath $raceEvents -PathType Leaf) {
        $raceLines = @(Get-Content -LiteralPath $raceEvents -Encoding UTF8)
    }
    $raceArchived = Join-Path `
        (Join-Path (Join-Path $raceRoot 'spool') 'replayed') `
        (Split-Path -Leaf $raceSpool)
    Add-Check -Name 'append lock covers scan dedup append and archive' -Passed (
        $raceMutexAcquired -and $raceReachedWait -and
        ($raceOutput -match 'replayed=0 deduped=1 failed=0') -and
        $raceLines.Count -eq 1 -and
        ($raceLines[0] -match 'same-semantic-event') -and
        (-not (Test-Path -LiteralPath $raceSpool)) -and
        (Test-Path -LiteralPath $raceArchived)
    ) -Detail (
        "setup=$raceMutexAcquired wait=$raceReachedWait lines=$($raceLines.Count) " +
        "out=$raceOutput"
    )

    # 13. Abandoned AppendV1 ownership is dirty. The writer publishes its WAL
    #     and the replayer skips without mutation; the next clean owner recovers.
    $appendReady = Join-Path $tempRoot 'abandoned-append-writer.ready'
    $abandonedWriterRoot = New-TestBridgeRoot -Name 'abandoned-writer'
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_RUNTIME_ROOT', $abandonedWriterRoot, 'Process'
    )
    $abandonedWriterError = ''
    $appendSentinel = New-Object System.Threading.Mutex($false, $isolatedAppendName)
    try {
        $appendAbandoned = Stop-ProcessAfterMutexAcquisition `
            -Name $isolatedAppendName -HelperPath $abandonHelper `
            -ReadyPath $appendReady
        if ($appendAbandoned) {
            try {
                & $isolatedWriter -Agent 'smoke-1' -Type status -Status open `
                    -Message 'abandoned-writer' -PayloadJson '{}' | Out-Null
            } catch { $abandonedWriterError = $_.Exception.Message }
        }
    } finally {
        $appendSentinel.Dispose()
    }
    $abandonedWriterEvents = Join-Path $abandonedWriterRoot 'shared/events.jsonl'
    $abandonedWriterSpools = @(
        Get-ChildItem -LiteralPath (Join-Path $abandonedWriterRoot 'spool') `
            -Filter 'failed-append-*.jsonl' -File -ErrorAction SilentlyContinue
    )
    $abandonedWriterDirtyCheckpoint = Test-Path -LiteralPath `
        "$abandonedWriterEvents.append-v1-validation.json" -PathType Leaf
    $abandonedWriterRecovered = ''
    if ($abandonedWriterSpools.Count -eq 1) {
        $abandonedWriterRecovered = & $isolatedReplay -BridgeRoot $abandonedWriterRoot
    }
    Add-Check -Name 'writer rejects dirty abandoned append ownership then recovers' -Passed (
        $appendAbandoned -and
        ($abandonedWriterError -match 'dirty ownership') -and
        (-not $abandonedWriterDirtyCheckpoint) -and
        $abandonedWriterSpools.Count -eq 1 -and
        ($abandonedWriterRecovered -match 'replayed=1') -and
        (Get-BridgeTestFileLength -Path $abandonedWriterEvents) -gt 0 -and
        ([System.IO.File]::ReadAllText($abandonedWriterEvents) -match 'abandoned-writer')
    ) -Detail (
        "setup=$appendAbandoned error=$abandonedWriterError " +
        "spools=$($abandonedWriterSpools.Count) recovered=$abandonedWriterRecovered"
    )

    $replayAppendReady = Join-Path $tempRoot 'abandoned-append-replay.ready'
    $abandonedAppendReplayRoot = New-TestBridgeRoot -Name 'abandoned-append-replay'
    $abandonedAppendSpool = Join-Path `
        (Join-Path $abandonedAppendReplayRoot 'spool') `
        'failed-append-smoke-1-abandoned-append.jsonl'
    $abandonedAppendEvent = '{"ts_utc":"2026-07-02T15:00:00Z","agent":"smoke-1","type":"message","task_id":"abandoned-append","status":"info","message":"abandoned-append-replay"}'
    Write-TestWal -Path $abandonedAppendSpool -Text $abandonedAppendEvent
    $replayAppendSentinel = New-Object System.Threading.Mutex($false, $isolatedAppendName)
    try {
        $replayAppendAbandoned = Stop-ProcessAfterMutexAcquisition `
            -Name $isolatedAppendName -HelperPath $abandonHelper `
            -ReadyPath $replayAppendReady
        $abandonedAppendDirtyOut = if ($replayAppendAbandoned) {
            & $isolatedReplay -BridgeRoot $abandonedAppendReplayRoot
        } else { '' }
    } finally {
        $replayAppendSentinel.Dispose()
    }
    $abandonedAppendDirtyBytes = Get-BridgeTestFileLength -Path `
        (Join-Path $abandonedAppendReplayRoot 'shared/events.jsonl')
    $abandonedAppendDirtyCheckpoint = Test-Path -LiteralPath `
        ((Join-Path $abandonedAppendReplayRoot 'shared/events.jsonl') +
            '.append-v1-validation.json') -PathType Leaf
    $abandonedAppendCleanOut = if ($replayAppendAbandoned) {
        & $isolatedReplay -BridgeRoot $abandonedAppendReplayRoot
    } else { '' }
    Add-Check -Name 'replayer rejects dirty abandoned append ownership then recovers' -Passed (
        $replayAppendAbandoned -and
        ($abandonedAppendDirtyOut -match 'dirty abandoned') -and
        $abandonedAppendDirtyBytes -eq 0 -and
        (-not $abandonedAppendDirtyCheckpoint) -and
        ($abandonedAppendCleanOut -match 'replayed=1') -and
        (-not (Test-Path -LiteralPath $abandonedAppendSpool)) -and
        (Get-BridgeTestFileLength -Path (Join-Path $abandonedAppendReplayRoot 'shared/events.jsonl')) -gt 0
    ) -Detail (
        "setup=$replayAppendAbandoned dirty=$abandonedAppendDirtyOut " +
        "clean=$abandonedAppendCleanOut"
    )

    $replayReady = Join-Path $tempRoot 'abandoned-replay.ready'
    $abandonedReplayRoot = New-TestBridgeRoot -Name 'abandoned-replay'
    $abandonedReplaySpool = Join-Path `
        (Join-Path $abandonedReplayRoot 'spool') `
        'failed-append-smoke-1-abandoned-replay.jsonl'
    $abandonedReplayEvent = '{"ts_utc":"2026-07-02T16:00:00Z","agent":"smoke-1","type":"message","task_id":"abandoned-replay","status":"info","message":"abandoned-replay"}'
    Write-TestWal -Path $abandonedReplaySpool -Text $abandonedReplayEvent
    $replaySentinel = New-Object System.Threading.Mutex($false, $isolatedReplayName)
    try {
        $replayAbandoned = Stop-ProcessAfterMutexAcquisition `
            -Name $isolatedReplayName -HelperPath $abandonHelper `
            -ReadyPath $replayReady
        $abandonedReplayOut = if ($replayAbandoned) {
            & $isolatedReplay -BridgeRoot $abandonedReplayRoot
        } else { '' }
    } finally {
        $replaySentinel.Dispose()
    }
    Add-Check -Name 'replayer accepts abandoned replay mutex ownership' -Passed (
        $replayAbandoned -and ($abandonedReplayOut -match 'replayed=1') -and
        (-not (Test-Path -LiteralPath $abandonedReplaySpool)) -and
        (Get-BridgeTestFileLength -Path (Join-Path $abandonedReplayRoot 'shared/events.jsonl')) -gt 0
    ) -Detail "setup=$replayAbandoned out=$abandonedReplayOut"

    # 14. Orphan pending WALs are recovered only after strict validation.
    $pendingRoot = New-TestBridgeRoot -Name 'pending-recovery'
    $pendingSpoolDir = Join-Path $pendingRoot 'spool'
    $pendingPath = Join-Path $pendingSpoolDir `
        '.failed-append-smoke-1-pending-recovery.jsonl.pending'
    $pendingFinal = Join-Path $pendingSpoolDir `
        'failed-append-smoke-1-pending-recovery.jsonl'
    $pendingEvent = '{"ts_utc":"2026-07-02T18:00:00Z","agent":"smoke-1","type":"message","task_id":"pending-recovery","status":"info","message":"orphan-pending"}'
    Write-TestWal -Path $pendingPath -Text $pendingEvent
    if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
        [System.IO.File]::SetAttributes(
            $pendingPath,
            ([System.IO.File]::GetAttributes($pendingPath) -bor
                [System.IO.FileAttributes]::Hidden)
        )
    }
    $pendingOut = & $isolatedReplay -BridgeRoot $pendingRoot
    $pendingArchive = Join-Path `
        (Join-Path $pendingSpoolDir 'replayed') `
        (Split-Path -Leaf $pendingFinal)
    Add-Check -Name 'valid orphan pending WAL is promoted replayed and archived' -Passed (
        ($pendingOut -match 'replayed=1 deduped=0 failed=0') -and
        (-not (Test-Path -LiteralPath $pendingPath)) -and
        (-not (Test-Path -LiteralPath $pendingFinal)) -and
        (Test-Path -LiteralPath $pendingArchive) -and
        ([System.IO.File]::ReadAllText(
            (Join-Path $pendingRoot 'shared/events.jsonl')
        ) -match 'orphan-pending')
    ) -Detail "out=$pendingOut"

    $partialPendingRoot = New-TestBridgeRoot -Name 'partial-pending'
    $partialPendingPath = Join-Path (Join-Path $partialPendingRoot 'spool') `
        '.failed-append-smoke-1-partial-pending.jsonl.pending'
    [System.IO.File]::WriteAllBytes(
        $partialPendingPath,
        $utf8.GetBytes('{"agent":"smoke-1"')
    )
    $partialPendingError = ''
    try { & $isolatedReplay -BridgeRoot $partialPendingRoot | Out-Null }
    catch { $partialPendingError = $_.Exception.Message }
    Add-Check -Name 'partial orphan pending WAL fails closed unchanged' -Passed (
        ($partialPendingError -match 'does not end with LF') -and
        (Test-Path -LiteralPath $partialPendingPath) -and
        (Get-BridgeTestFileLength -Path `
            (Join-Path $partialPendingRoot 'shared/events.jsonl')) -eq 0
    ) -Detail "error=$partialPendingError"

    $exactCollisionRoot = New-TestBridgeRoot -Name 'pending-exact-collision'
    $exactCollisionSpoolDir = Join-Path $exactCollisionRoot 'spool'
    $exactCollisionFinal = Join-Path $exactCollisionSpoolDir `
        'failed-append-smoke-1-pending-collision.jsonl'
    $exactCollisionPending = Join-Path $exactCollisionSpoolDir `
        '.failed-append-smoke-1-pending-collision.jsonl.pending'
    $collisionEvent = '{"ts_utc":"2026-07-02T18:10:00Z","agent":"smoke-1","type":"message","task_id":"pending-collision","status":"info","message":"exact-collision"}'
    Write-TestWal -Path $exactCollisionFinal -Text $collisionEvent
    Write-TestWal -Path $exactCollisionPending -Text $collisionEvent
    $exactCollisionOut = & $isolatedReplay -BridgeRoot $exactCollisionRoot
    $exactCollisionArchives = @(
        Get-ChildItem -LiteralPath `
            (Join-Path $exactCollisionSpoolDir 'replayed') -File -Force |
            Where-Object { $_.Name -match 'pending-collision' }
    )
    Add-Check -Name 'exact pending final collision is archived safely' -Passed (
        ($exactCollisionOut -match 'replayed=1') -and
        (-not (Test-Path -LiteralPath $exactCollisionFinal)) -and
        (-not (Test-Path -LiteralPath $exactCollisionPending)) -and
        $exactCollisionArchives.Count -eq 2 -and
        @(Get-Content -LiteralPath `
            (Join-Path $exactCollisionRoot 'shared/events.jsonl')).Count -eq 1
    ) -Detail (
        "out=$exactCollisionOut archives=$($exactCollisionArchives.Count)"
    )

    $differentCollisionRoot = New-TestBridgeRoot -Name 'pending-different-collision'
    $differentCollisionSpoolDir = Join-Path $differentCollisionRoot 'spool'
    $differentCollisionFinal = Join-Path $differentCollisionSpoolDir `
        'failed-append-smoke-1-different-collision.jsonl'
    $differentCollisionPending = Join-Path $differentCollisionSpoolDir `
        '.failed-append-smoke-1-different-collision.jsonl.pending'
    Write-TestWal -Path $differentCollisionFinal -Text $collisionEvent
    Write-TestWal -Path $differentCollisionPending -Text `
        $collisionEvent.Replace('exact-collision', 'different-collision')
    $differentCollisionError = ''
    try { & $isolatedReplay -BridgeRoot $differentCollisionRoot | Out-Null }
    catch { $differentCollisionError = $_.Exception.Message }
    Add-Check -Name 'different pending final collision is a hard failure' -Passed (
        ($differentCollisionError -match 'collides with different final') -and
        (Test-Path -LiteralPath $differentCollisionFinal) -and
        (Test-Path -LiteralPath $differentCollisionPending) -and
        (Get-BridgeTestFileLength -Path `
            (Join-Path $differentCollisionRoot 'shared/events.jsonl')) -eq 0
    ) -Detail "error=$differentCollisionError"

    # 15. A durable hidden pending WAL exists before canonical append. The
    #     bounded hook pauses after clean ownership and before FileStream write.
    $beforeAppendRoot = New-TestBridgeRoot -Name 'wal-before-canonical'
    $beforeAppendReady = Join-Path $tempRoot 'wal-before-canonical.ready'
    $beforeAppendRelease = "$beforeAppendReady.release"
    $beforeAppendJob = Start-Job -ScriptBlock {
        param($ScriptPath, $Root, $ReadyPath)
        $env:AGENT_BRIDGE_RUNTIME_ROOT = $Root
        $env:AGENT_BRIDGE_TEST_BEFORE_APPEND_READY = $ReadyPath
        Remove-Item Env:AGENT_BRIDGE_TEST_APPEND_FAILURE_AFTER_BYTES `
            -ErrorAction SilentlyContinue
        try {
            & $ScriptPath -Agent 'smoke-1' -Type status -Status open `
                -Message 'wal-before-canonical' -PayloadJson '{}' | Out-Null
            'success'
        } catch { "ERROR: $($_.Exception.Message)" }
    } -ArgumentList $isolatedWriter, $beforeAppendRoot, $beforeAppendReady
    $beforeAppendReached = $false
    $beforeAppendPending = ''
    $beforeAppendHidden = $false
    $beforeAppendBytesValid = $false
    $beforeAppendOutput = ''
    try {
        for ($attempt = 0; $attempt -lt 400; $attempt++) {
            if (Test-Path -LiteralPath $beforeAppendReady -PathType Leaf) {
                $beforeAppendReached = $true
                break
            }
            if ($beforeAppendJob.State -in @('Completed', 'Failed', 'Stopped')) { break }
            Start-Sleep -Milliseconds 25
        }
        if ($beforeAppendReached) {
            $beforeAppendPending = [System.IO.File]::ReadAllText($beforeAppendReady)
            if (Test-Path -LiteralPath $beforeAppendPending -PathType Leaf) {
                $beforeAppendAttributes = [System.IO.File]::GetAttributes(
                    $beforeAppendPending
                )
                $beforeAppendHidden = (
                    [Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or
                    (($beforeAppendAttributes -band [System.IO.FileAttributes]::Hidden) -ne 0)
                )
                [byte[]]$beforeAppendBytes = [System.IO.File]::ReadAllBytes(
                    $beforeAppendPending
                )
                $beforeAppendBytesValid = (
                    $beforeAppendBytes.Length -gt 0 -and
                    $beforeAppendBytes[$beforeAppendBytes.Length - 1] -eq 10
                )
            }
        }
        [System.IO.File]::WriteAllText($beforeAppendRelease, 'release')
        $beforeAppendJob | Wait-Job | Out-Null
        $beforeAppendOutput = @(Receive-Job -Job $beforeAppendJob) -join ' '
    } finally {
        if (-not (Test-Path -LiteralPath $beforeAppendRelease)) {
            [System.IO.File]::WriteAllText($beforeAppendRelease, 'release')
        }
        Remove-Job -Job $beforeAppendJob -Force -ErrorAction SilentlyContinue
    }
    $beforeAppendSpools = @(
        Get-ChildItem -LiteralPath (Join-Path $beforeAppendRoot 'spool') `
            -File -Force -ErrorAction SilentlyContinue
    )
    Add-Check -Name 'writer has durable hidden WAL before canonical append' -Passed (
        $beforeAppendReached -and $beforeAppendHidden -and
        $beforeAppendBytesValid -and
        ($beforeAppendOutput -match 'success') -and
        (Get-BridgeTestFileLength -Path `
            (Join-Path $beforeAppendRoot 'shared/events.jsonl')) -gt 0 -and
        $beforeAppendSpools.Count -eq 0
    ) -Detail (
        "reached=$beforeAppendReached hidden=$beforeAppendHidden " +
        "bytes=$beforeAppendBytesValid out=$beforeAppendOutput " +
        "spools=$($beforeAppendSpools.Count)"
    )

    # 16. Writer and replayer both roll a partial FileStream append back to the
    #     exact pre-length, flush, retain the WAL, and recover on a clean retry.
    $writerRollbackRoot = New-TestBridgeRoot -Name 'writer-rollback'
    $writerRollbackEvents = Join-Path $writerRollbackRoot 'shared/events.jsonl'
    $writerRollbackSeed = '{"ts_utc":"2026-07-02T18:20:00Z","agent":"seed-1","type":"message","task_id":"writer-rollback-seed","status":"info","message":"seed"}'
    Write-TestWal -Path $writerRollbackEvents -Text $writerRollbackSeed
    $writerRollbackBefore = [Convert]::ToBase64String(
        [System.IO.File]::ReadAllBytes($writerRollbackEvents)
    )
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_RUNTIME_ROOT', $writerRollbackRoot, 'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_APPEND_FAILURE_AFTER_BYTES', '11', 'Process'
    )
    $writerRollbackError = ''
    try {
        & $isolatedWriter -Agent 'smoke-1' -Type status -Status open `
            -Message 'writer-partial-rollback' -PayloadJson '{}' | Out-Null
    } catch { $writerRollbackError = $_.Exception.Message }
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_APPEND_FAILURE_AFTER_BYTES', $null, 'Process'
    )
    $writerRollbackAfter = [Convert]::ToBase64String(
        [System.IO.File]::ReadAllBytes($writerRollbackEvents)
    )
    $writerRollbackSpools = @(
        Get-ChildItem -LiteralPath (Join-Path $writerRollbackRoot 'spool') `
            -Filter 'failed-append-*.jsonl' -File -Force
    )
    $writerRollbackPending = @(
        Get-ChildItem -LiteralPath (Join-Path $writerRollbackRoot 'spool') `
            -Filter '.*.pending' -File -Force
    )
    $writerRollbackRecovery = if ($writerRollbackSpools.Count -eq 1) {
        & $isolatedReplay -BridgeRoot $writerRollbackRoot
    } else { '' }
    Add-Check -Name 'writer partial append rolls back exactly and retains WAL' -Passed (
        ($writerRollbackError -match 'rolled back') -and
        $writerRollbackBefore -ceq $writerRollbackAfter -and
        $writerRollbackSpools.Count -eq 1 -and
        $writerRollbackPending.Count -eq 0 -and
        ($writerRollbackRecovery -match 'replayed=1')
    ) -Detail (
        "error=$writerRollbackError spools=$($writerRollbackSpools.Count) " +
        "pending=$($writerRollbackPending.Count) recovery=$writerRollbackRecovery"
    )

    $replayRollbackRoot = New-TestBridgeRoot -Name 'replay-rollback'
    $replayRollbackEvents = Join-Path $replayRollbackRoot 'shared/events.jsonl'
    Write-TestWal -Path $replayRollbackEvents -Text $writerRollbackSeed
    $replayRollbackBefore = [Convert]::ToBase64String(
        [System.IO.File]::ReadAllBytes($replayRollbackEvents)
    )
    $replayRollbackSpool = Join-Path (Join-Path $replayRollbackRoot 'spool') `
        'failed-append-smoke-1-replay-rollback.jsonl'
    Write-TestWal -Path $replayRollbackSpool -Text `
        $writerRollbackSeed.Replace('writer-rollback-seed', 'replay-rollback')
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_APPEND_FAILURE_AFTER_BYTES', '13', 'Process'
    )
    $replayRollbackDirtyOut = & $isolatedReplay `
        -BridgeRoot $replayRollbackRoot 3>$null
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_APPEND_FAILURE_AFTER_BYTES', $null, 'Process'
    )
    $replayRollbackAfter = [Convert]::ToBase64String(
        [System.IO.File]::ReadAllBytes($replayRollbackEvents)
    )
    $replayRollbackCleanOut = & $isolatedReplay -BridgeRoot $replayRollbackRoot
    Add-Check -Name 'replayer partial append rolls back exactly and retains WAL' -Passed (
        ($replayRollbackDirtyOut -match 'failed=1') -and
        $replayRollbackBefore -ceq $replayRollbackAfter -and
        ($replayRollbackCleanOut -match 'replayed=1') -and
        (-not (Test-Path -LiteralPath $replayRollbackSpool))
    ) -Detail (
        "dirty=$replayRollbackDirtyOut clean=$replayRollbackCleanOut"
    )

    # 17. Only a valid WAL-bound canonical suffix may be quarantined and
    #     truncated. Unbound or interior corruption is byte-for-byte untouched.
    $strictTestUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
    $tornRoot = New-TestBridgeRoot -Name 'bound-torn-tail'
    $tornEvents = Join-Path $tornRoot 'shared/events.jsonl'
    $tornSpool = Join-Path (Join-Path $tornRoot 'spool') `
        'failed-append-smoke-1-bound-torn.jsonl'
    $tornPrefixEvent = '{"ts_utc":"2026-07-02T18:30:00Z","agent":"seed-1","type":"message","task_id":"bound-torn-seed","status":"info","message":"seed"}'
    $tornWalFirstEvent = '{"ts_utc":"2026-07-02T18:30:30Z","agent":"smoke-1","type":"message","task_id":"bound-torn-first","status":"info","message":"complete-before-torn"}'
    $tornWalEvent = '{"ts_utc":"2026-07-02T18:31:00Z","agent":"smoke-1","type":"message","task_id":"bound-torn","status":"info","message":"recover-torn"}'
    [System.IO.File]::WriteAllText(
        $tornSpool,
        ($tornWalFirstEvent + [char]10 + $tornWalEvent + [char]10),
        $strictTestUtf8
    )
    [byte[]]$tornPrefixBytes = $strictTestUtf8.GetBytes(
        $tornPrefixEvent + [char]10
    )
    [byte[]]$tornFirstRowBytes = $strictTestUtf8.GetBytes(
        $tornWalFirstEvent + [char]10
    )
    [byte[]]$tornSecondRowBytes = $strictTestUtf8.GetBytes(
        $tornWalEvent + [char]10
    )
    [byte[]]$tornWalBytes = [System.IO.File]::ReadAllBytes($tornSpool)
    $tornFragmentLength = [Math]::Min(47, $tornSecondRowBytes.Length - 1)
    $tornFragment = New-Object byte[] $tornFragmentLength
    [Array]::Copy(
        $tornSecondRowBytes, 0, $tornFragment, 0, $tornFragmentLength
    )
    [byte[]]$tornCanonicalBytes = [byte[]](
        $tornPrefixBytes + $tornFirstRowBytes + $tornFragment
    )
    [System.IO.File]::WriteAllBytes($tornEvents, $tornCanonicalBytes)
    $tornOut = & $isolatedReplay -BridgeRoot $tornRoot 3>$null
    [byte[]]$tornExpected = [byte[]]($tornPrefixBytes + $tornWalBytes)
    $tornQuarantine = @(
        Get-ChildItem -LiteralPath `
            (Join-Path (Join-Path $tornRoot 'spool') 'quarantine') `
            -File -ErrorAction SilentlyContinue
    )
    $tornQuarantineExact = (
        $tornQuarantine.Count -eq 1 -and
        [Convert]::ToBase64String(
            [System.IO.File]::ReadAllBytes($tornQuarantine[0].FullName)
        ) -ceq [Convert]::ToBase64String($tornFragment)
    )
    Add-Check -Name 'WAL-bound torn tail is quarantined truncated and replayed' -Passed (
        ($tornOut -match 'replayed=1 deduped=1') -and
        $tornQuarantineExact -and
        [Convert]::ToBase64String(
            [System.IO.File]::ReadAllBytes($tornEvents)
        ) -ceq [Convert]::ToBase64String($tornExpected) -and
        (-not (Test-Path -LiteralPath $tornSpool))
    ) -Detail "out=$tornOut quarantine=$($tornQuarantine.Count)"

    $unboundRoot = New-TestBridgeRoot -Name 'unbound-torn-tail'
    $unboundEvents = Join-Path $unboundRoot 'shared/events.jsonl'
    $unboundSpool = Join-Path (Join-Path $unboundRoot 'spool') `
        'failed-append-smoke-1-unbound-torn.jsonl'
    Write-TestWal -Path $unboundSpool -Text $tornWalEvent
    [byte[]]$unboundBytes = [byte[]](
        $tornPrefixBytes + $strictTestUtf8.GetBytes('{"unbound":true')
    )
    [System.IO.File]::WriteAllBytes($unboundEvents, $unboundBytes)
    $unboundBefore = [Convert]::ToBase64String($unboundBytes)
    $unboundError = ''
    try { & $isolatedReplay -BridgeRoot $unboundRoot 3>$null | Out-Null }
    catch { $unboundError = $_.Exception.Message }
    $unboundQuarantine = @(
        Get-ChildItem -LiteralPath `
            (Join-Path (Join-Path $unboundRoot 'spool') 'quarantine') `
            -File -ErrorAction SilentlyContinue
    )
    Add-Check -Name 'unbound torn tail fails closed without quarantine' -Passed (
        ($unboundError -match '(unbound|ambiguous short) unterminated tail') -and
        $unboundBefore -ceq [Convert]::ToBase64String(
            [System.IO.File]::ReadAllBytes($unboundEvents)
        ) -and
        (Test-Path -LiteralPath $unboundSpool) -and
        $unboundQuarantine.Count -eq 0
    ) -Detail "error=$unboundError quarantine=$($unboundQuarantine.Count)"

    $shortTailRoot = New-TestBridgeRoot -Name 'ambiguous-short-torn-tail'
    $shortTailEvents = Join-Path $shortTailRoot 'shared/events.jsonl'
    $shortTailSpool = Join-Path (Join-Path $shortTailRoot 'spool') `
        'failed-append-smoke-1-short-torn.jsonl'
    Write-TestWal -Path $shortTailSpool -Text $tornWalEvent
    [byte[]]$shortTailBytes = [byte[]]($tornPrefixBytes + @([byte][char]'{'))
    [System.IO.File]::WriteAllBytes($shortTailEvents, $shortTailBytes)
    $shortTailBefore = [Convert]::ToBase64String($shortTailBytes)
    $shortTailError = ''
    try { & $isolatedReplay -BridgeRoot $shortTailRoot 3>$null | Out-Null }
    catch { $shortTailError = $_.Exception.Message }
    $shortTailQuarantine = @(
        Get-ChildItem -LiteralPath `
            (Join-Path (Join-Path $shortTailRoot 'spool') 'quarantine') `
            -File -ErrorAction SilentlyContinue
    )
    Add-Check -Name 'one-byte torn tail is never WAL-bound' -Passed (
        ($shortTailError -match 'ambiguous short unterminated tail') -and
        ($shortTailBefore -ceq [Convert]::ToBase64String(
            [System.IO.File]::ReadAllBytes($shortTailEvents))) -and
        (Test-Path -LiteralPath $shortTailSpool -PathType Leaf) -and
        $shortTailQuarantine.Count -eq 0
    ) -Detail "error=$shortTailError quarantine=$($shortTailQuarantine.Count)"

    $interiorRoot = New-TestBridgeRoot -Name 'interior-malformed-torn'
    $interiorEvents = Join-Path $interiorRoot 'shared/events.jsonl'
    $interiorSpool = Join-Path (Join-Path $interiorRoot 'spool') `
        'failed-append-smoke-1-interior-torn.jsonl'
    Write-TestWal -Path $interiorSpool -Text $tornWalEvent
    [byte[]]$interiorBytes = [byte[]](
        $strictTestUtf8.GetBytes('{not-json}' + [char]10) + $tornFragment
    )
    [System.IO.File]::WriteAllBytes($interiorEvents, $interiorBytes)
    $interiorBefore = [Convert]::ToBase64String($interiorBytes)
    $interiorError = ''
    try { & $isolatedReplay -BridgeRoot $interiorRoot 3>$null | Out-Null }
    catch { $interiorError = $_.Exception.Message }
    $interiorQuarantine = @(
        Get-ChildItem -LiteralPath `
            (Join-Path (Join-Path $interiorRoot 'spool') 'quarantine') `
            -File -ErrorAction SilentlyContinue
    )
    Add-Check -Name 'interior malformed canonical row is never tail repaired' -Passed (
        ($interiorError -match 'prefix has malformed JSON') -and
        $interiorBefore -ceq [Convert]::ToBase64String(
            [System.IO.File]::ReadAllBytes($interiorEvents)
        ) -and
        (Test-Path -LiteralPath $interiorSpool) -and
        $interiorQuarantine.Count -eq 0
    ) -Detail "error=$interiorError quarantine=$($interiorQuarantine.Count)"

    $blankTornRoot = New-TestBridgeRoot -Name 'interior-blank-torn'
    $blankTornEvents = Join-Path $blankTornRoot 'shared/events.jsonl'
    $blankTornSpool = Join-Path (Join-Path $blankTornRoot 'spool') `
        'failed-append-smoke-1-interior-blank-torn.jsonl'
    Write-TestWal -Path $blankTornSpool -Text $tornWalEvent
    [byte[]]$blankTornBytes = [byte[]](
        $tornPrefixBytes +
        $strictTestUtf8.GetBytes("  `t`r`n") +
        $tornFragment
    )
    [System.IO.File]::WriteAllBytes($blankTornEvents, $blankTornBytes)
    $blankTornBefore = [Convert]::ToBase64String($blankTornBytes)
    $blankTornError = ''
    try { & $isolatedReplay -BridgeRoot $blankTornRoot 3>$null | Out-Null }
    catch { $blankTornError = $_.Exception.Message }
    $blankTornArchiveFiles = @(
        Get-ChildItem -LiteralPath (Join-Path $blankTornRoot 'spool') `
            -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -ne $blankTornSpool }
    )
    Add-Check -Name 'interior blank row blocks WAL-bound torn-tail repair' -Passed (
        ($blankTornError -match 'blank or whitespace-only row') -and
        $blankTornBefore -ceq [Convert]::ToBase64String(
            [System.IO.File]::ReadAllBytes($blankTornEvents)
        ) -and
        (Test-Path -LiteralPath $blankTornSpool) -and
        $blankTornArchiveFiles.Count -eq 0
    ) -Detail (
        "error=$blankTornError extraFiles=$($blankTornArchiveFiles.Count)"
    )

    $plainBlankRoot = New-TestBridgeRoot -Name 'plain-blank-canonical'
    $plainBlankEvents = Join-Path $plainBlankRoot 'shared/events.jsonl'
    $plainBlankSpool = Join-Path (Join-Path $plainBlankRoot 'spool') `
        'failed-append-smoke-1-plain-blank.jsonl'
    Write-TestWal -Path $plainBlankSpool -Text $tornWalEvent
    [byte[]]$plainBlankBytes = $strictTestUtf8.GetBytes(" `t`r`n")
    [System.IO.File]::WriteAllBytes($plainBlankEvents, $plainBlankBytes)
    $plainBlankBefore = [Convert]::ToBase64String($plainBlankBytes)
    $plainBlankError = ''
    try { & $isolatedReplay -BridgeRoot $plainBlankRoot 3>$null | Out-Null }
    catch { $plainBlankError = $_.Exception.Message }
    $plainBlankArchiveFiles = @(
        Get-ChildItem -LiteralPath (Join-Path $plainBlankRoot 'spool') `
            -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -ne $plainBlankSpool }
    )
    Add-Check -Name 'plain blank canonical row fails closed unchanged' -Passed (
        ($plainBlankError -match 'blank or whitespace-only row') -and
        $plainBlankBefore -ceq [Convert]::ToBase64String(
            [System.IO.File]::ReadAllBytes($plainBlankEvents)
        ) -and
        (Test-Path -LiteralPath $plainBlankSpool) -and
        $plainBlankArchiveFiles.Count -eq 0
    ) -Detail (
        "error=$plainBlankError extraFiles=$($plainBlankArchiveFiles.Count)"
    )

    # 18. Invalid UTF-8 in canonical, final WAL, or pending WAL fails closed.
    $invalidCanonicalRoot = New-TestBridgeRoot -Name 'invalid-utf8-canonical'
    $invalidCanonicalEvents = Join-Path $invalidCanonicalRoot 'shared/events.jsonl'
    $invalidCanonicalSpool = Join-Path `
        (Join-Path $invalidCanonicalRoot 'spool') `
        'failed-append-smoke-1-invalid-canonical.jsonl'
    [byte[]]$invalidUtf8Row = [byte[]]@(0xFF, 0x0A)
    [System.IO.File]::WriteAllBytes($invalidCanonicalEvents, $invalidUtf8Row)
    Write-TestWal -Path $invalidCanonicalSpool -Text $event
    $invalidCanonicalError = ''
    try { & $isolatedReplay -BridgeRoot $invalidCanonicalRoot | Out-Null }
    catch { $invalidCanonicalError = $_.Exception.Message }
    $invalidCanonicalPassed = (
        ($invalidCanonicalError -match 'not strict UTF-8') -and
        [Convert]::ToBase64String(
            [System.IO.File]::ReadAllBytes($invalidCanonicalEvents)
        ) -ceq [Convert]::ToBase64String($invalidUtf8Row) -and
        (Test-Path -LiteralPath $invalidCanonicalSpool)
    )

    $invalidFinalRoot = New-TestBridgeRoot -Name 'invalid-utf8-final'
    $invalidFinalPath = Join-Path (Join-Path $invalidFinalRoot 'spool') `
        'failed-append-smoke-1-invalid-final.jsonl'
    [System.IO.File]::WriteAllBytes($invalidFinalPath, $invalidUtf8Row)
    $invalidFinalError = ''
    try { & $isolatedReplay -BridgeRoot $invalidFinalRoot | Out-Null }
    catch { $invalidFinalError = $_.Exception.Message }
    $invalidFinalPassed = (
        ($invalidFinalError -match 'WAL is not strict UTF-8') -and
        (Test-Path -LiteralPath $invalidFinalPath) -and
        (Get-BridgeTestFileLength -Path `
            (Join-Path $invalidFinalRoot 'shared/events.jsonl')) -eq 0
    )

    $invalidPendingRoot = New-TestBridgeRoot -Name 'invalid-utf8-pending'
    $invalidPendingPath = Join-Path (Join-Path $invalidPendingRoot 'spool') `
        '.failed-append-smoke-1-invalid-pending.jsonl.pending'
    [System.IO.File]::WriteAllBytes($invalidPendingPath, $invalidUtf8Row)
    $invalidPendingError = ''
    try { & $isolatedReplay -BridgeRoot $invalidPendingRoot | Out-Null }
    catch { $invalidPendingError = $_.Exception.Message }
    $invalidPendingPassed = (
        ($invalidPendingError -match 'WAL is not strict UTF-8') -and
        (Test-Path -LiteralPath $invalidPendingPath) -and
        (Get-BridgeTestFileLength -Path `
            (Join-Path $invalidPendingRoot 'shared/events.jsonl')) -eq 0
    )

    $invalidWriterRoot = New-TestBridgeRoot -Name 'invalid-utf8-writer'
    $invalidWriterEvents = Join-Path $invalidWriterRoot 'shared/events.jsonl'
    [System.IO.File]::WriteAllBytes($invalidWriterEvents, $invalidUtf8Row)
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_RUNTIME_ROOT', $invalidWriterRoot, 'Process'
    )
    $invalidWriterError = ''
    try {
        & $isolatedWriter -Agent 'smoke-1' -Type status -Status open `
            -Message 'invalid-utf8-writer' -PayloadJson '{}' | Out-Null
    } catch { $invalidWriterError = $_.Exception.Message }
    $invalidWriterSpools = @(
        Get-ChildItem -LiteralPath (Join-Path $invalidWriterRoot 'spool') `
            -Filter 'failed-append-*.jsonl' -File -Force
    )
    $invalidWriterPassed = (
        ($invalidWriterError -match 'not strict UTF-8') -and
        [Convert]::ToBase64String(
            [System.IO.File]::ReadAllBytes($invalidWriterEvents)
        ) -ceq [Convert]::ToBase64String($invalidUtf8Row) -and
        $invalidWriterSpools.Count -eq 1
    )
    Add-Check -Name 'invalid UTF-8 canonical and WAL inputs fail closed unchanged' -Passed (
        $invalidCanonicalPassed -and $invalidFinalPassed -and
        $invalidPendingPassed -and $invalidWriterPassed
    ) -Detail (
        "canonical=$invalidCanonicalPassed/$invalidCanonicalError " +
        "final=$invalidFinalPassed/$invalidFinalError " +
        "pending=$invalidPendingPassed/$invalidPendingError " +
        "writer=$invalidWriterPassed/$invalidWriterError"
    )

    # Decoder.Convert must carry a split multi-byte sequence across the 8192
    # byte validation buffer. The euro bytes occupy offsets 8191/8192/8193.
    $splitUtf8Root = New-TestBridgeRoot -Name 'split-utf8-writer'
    $splitUtf8Events = Join-Path $splitUtf8Root 'shared/events.jsonl'
    $splitUtf8Prefix = '{"ts_utc":"2026-07-02T19:00:00Z","agent":"seed-1","type":"message","task_id":"split-utf8","status":"info","message":"'
    $splitUtf8Suffix = ([char]0x20ac) + '"}' + [char]10
    $splitUtf8PaddingLength = 8191 - $strictTestUtf8.GetByteCount($splitUtf8Prefix)
    $splitUtf8Seed = $splitUtf8Prefix + ('a' * $splitUtf8PaddingLength) + `
        $splitUtf8Suffix
    [byte[]]$splitUtf8SeedBytes = $strictTestUtf8.GetBytes($splitUtf8Seed)
    $splitUtf8OffsetsExact = (
        $splitUtf8SeedBytes[8191] -eq 0xE2 -and
        $splitUtf8SeedBytes[8192] -eq 0x82 -and
        $splitUtf8SeedBytes[8193] -eq 0xAC
    )
    [System.IO.File]::WriteAllBytes($splitUtf8Events, $splitUtf8SeedBytes)
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_RUNTIME_ROOT', $splitUtf8Root, 'Process'
    )
    $splitUtf8WriterError = ''
    try {
        & $isolatedWriter -Agent 'smoke-1' -Type status -Status open `
            -Message 'split-utf8-append' -PayloadJson '{}' | Out-Null
    } catch { $splitUtf8WriterError = $_.Exception.Message }
    $splitUtf8Spools = @(
        Get-ChildItem -LiteralPath (Join-Path $splitUtf8Root 'spool') `
            -File -Force -ErrorAction SilentlyContinue
    )
    $splitUtf8Lines = @(
        Get-Content -LiteralPath $splitUtf8Events -Encoding UTF8
    )
    Add-Check -Name 'writer accepts UTF-8 sequence split at 8192-byte boundary' -Passed (
        $splitUtf8OffsetsExact -and
        [string]::IsNullOrEmpty($splitUtf8WriterError) -and
        $splitUtf8Spools.Count -eq 0 -and
        $splitUtf8Lines.Count -eq 2 -and
        ($splitUtf8Lines[1] -match 'split-utf8-append')
    ) -Detail (
        "offsets=$splitUtf8OffsetsExact error=$splitUtf8WriterError " +
        "spools=$($splitUtf8Spools.Count) lines=$($splitUtf8Lines.Count)"
    )

    # 19. A replayer that already owns AppendV1 skips a writer's actively
    #     leased pending WAL. The writer then acquires cleanly and emits once.
    $activeLeaseRoot = New-TestBridgeRoot -Name 'active-pending-lease'
    $activeLeaseReady = Join-Path $tempRoot 'active-pending-lease.ready'
    $activeLeaseRelease = "$activeLeaseReady.release"
    $activeReplayJob = Start-Job -ScriptBlock {
        param($ScriptPath, $Root, $ReadyPath)
        $env:AGENT_BRIDGE_TEST_AFTER_APPEND_READY = $ReadyPath
        Remove-Item Env:AGENT_BRIDGE_TEST_APPEND_FAILURE_AFTER_BYTES `
            -ErrorAction SilentlyContinue
        try { & $ScriptPath -BridgeRoot $Root 3>$null | Out-String }
        catch { "ERROR: $($_.Exception.Message)" }
    } -ArgumentList $leaseReplay, $activeLeaseRoot, $activeLeaseReady
    $activeReplayReady = $false
    $activeWriterJob = $null
    $activePendingPath = ''
    $activePendingHidden = $false
    $activePendingLocked = $false
    $activeReplayOutput = ''
    $activeWriterOutput = ''
    try {
        for ($attempt = 0; $attempt -lt 400; $attempt++) {
            if (Test-Path -LiteralPath $activeLeaseReady -PathType Leaf) {
                $activeReplayReady = $true
                break
            }
            if ($activeReplayJob.State -in @('Completed', 'Failed', 'Stopped')) { break }
            Start-Sleep -Milliseconds 25
        }
        if ($activeReplayReady) {
            $activeWriterJob = Start-Job -ScriptBlock {
                param($ScriptPath, $Root)
                $env:AGENT_BRIDGE_RUNTIME_ROOT = $Root
                Remove-Item Env:AGENT_BRIDGE_TEST_BEFORE_APPEND_READY `
                    -ErrorAction SilentlyContinue
                Remove-Item Env:AGENT_BRIDGE_TEST_APPEND_FAILURE_AFTER_BYTES `
                    -ErrorAction SilentlyContinue
                try {
                    & $ScriptPath -Agent 'smoke-1' -Type status -Status open `
                        -Message 'active-pending-once' -PayloadJson '{}' | Out-Null
                    'success'
                } catch { "ERROR: $($_.Exception.Message)" }
            } -ArgumentList $isolatedWriter, $activeLeaseRoot
            for ($attempt = 0; $attempt -lt 400; $attempt++) {
                $activePendingFiles = @(
                    Get-ChildItem -LiteralPath (Join-Path $activeLeaseRoot 'spool') `
                        -Filter '.*.pending' -File -Force -ErrorAction SilentlyContinue
                )
                if ($activePendingFiles.Count -eq 1) {
                    $activePendingPath = $activePendingFiles[0].FullName
                    break
                }
                if ($activeWriterJob.State -in @('Completed', 'Failed', 'Stopped')) { break }
                Start-Sleep -Milliseconds 25
            }
            if ($activePendingPath) {
                if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
                    $activePendingHidden = $true
                } else {
                    for ($attributeAttempt = 0; $attributeAttempt -lt 100; $attributeAttempt++) {
                        $activeAttributes = [System.IO.File]::GetAttributes(
                            $activePendingPath
                        )
                        if (
                            ($activeAttributes -band [System.IO.FileAttributes]::Hidden) -ne 0
                        ) {
                            $activePendingHidden = $true
                            break
                        }
                        Start-Sleep -Milliseconds 10
                    }
                }
                $probe = $null
                try {
                    $probe = [System.IO.File]::Open(
                        $activePendingPath,
                        [System.IO.FileMode]::Open,
                        [System.IO.FileAccess]::Read,
                        [System.IO.FileShare]::ReadWrite
                    )
                } catch {
                    $activePendingLocked = $true
                } finally {
                    if ($null -ne $probe) { $probe.Dispose() }
                }
            }
        }
        [System.IO.File]::WriteAllText($activeLeaseRelease, 'release')
        $activeReplayJob | Wait-Job | Out-Null
        $activeReplayOutput = @(Receive-Job -Job $activeReplayJob) -join ' '
        if ($null -ne $activeWriterJob) {
            $activeWriterJob | Wait-Job | Out-Null
            $activeWriterOutput = @(Receive-Job -Job $activeWriterJob) -join ' '
        }
    } finally {
        if (-not (Test-Path -LiteralPath $activeLeaseRelease)) {
            [System.IO.File]::WriteAllText($activeLeaseRelease, 'release')
        }
        foreach ($job in @($activeReplayJob, $activeWriterJob)) {
            if ($null -ne $job) {
                Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
            }
        }
    }
    $activeLeaseEvents = Join-Path $activeLeaseRoot 'shared/events.jsonl'
    $activeLeaseLines = @(
        if (Test-Path -LiteralPath $activeLeaseEvents) {
            Get-Content -LiteralPath $activeLeaseEvents -Encoding UTF8
        }
    )
    $activeLeaseSpools = @(
        Get-ChildItem -LiteralPath (Join-Path $activeLeaseRoot 'spool') `
            -File -Force -ErrorAction SilentlyContinue
    )
    Add-Check -Name 'replayer skips active pending lease and writer emits once' -Passed (
        $activeReplayReady -and $activePendingHidden -and
        $activePendingLocked -and
        ($activeReplayOutput -match 'active pending WAL lease skipped') -and
        ($activeWriterOutput -match 'success') -and
        $activeLeaseLines.Count -eq 1 -and
        ($activeLeaseLines[0] -match 'active-pending-once') -and
        $activeLeaseSpools.Count -eq 0
    ) -Detail (
        "ready=$activeReplayReady hidden=$activePendingHidden " +
        "locked=$activePendingLocked lines=$($activeLeaseLines.Count) " +
        "replay=$activeReplayOutput writer=$activeWriterOutput " +
        "spools=$($activeLeaseSpools.Count)"
    )

    # 20. The validation checkpoint is a bounded, non-authoritative cache.
    #     Bootstrap scans once; a matching identity/length/tail takes the fast
    #     path. Every malformed or stale binding forces full strict validation.
    foreach ($testEnvironmentName in @(
        'AGENT_BRIDGE_TEST_FAIL_ON_FULL_VALIDATION',
        'AGENT_BRIDGE_TEST_CHECKPOINT_UPDATE_FAILURE',
        'AGENT_BRIDGE_TEST_CHECKPOINT_INVALIDATION_FAILURE',
        'AGENT_BRIDGE_TEST_AFTER_CANONICAL_BEFORE_CHECKPOINT',
        'AGENT_BRIDGE_TEST_WAL_CLEANUP_FAILURE',
        'AGENT_BRIDGE_TEST_AUXILIARY_APPEND_FAILURE_AFTER_BYTES'
    )) {
        [Environment]::SetEnvironmentVariable($testEnvironmentName, $null, 'Process')
    }
    $checkpointRoot = New-TestBridgeRoot -Name 'validation-checkpoint'
    $checkpointEvents = Join-Path $checkpointRoot 'shared/events.jsonl'
    $checkpointPath = "$checkpointEvents.append-v1-validation.json"
    $checkpointTrace = Join-Path $checkpointRoot 'validation.trace'
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_RUNTIME_ROOT', $checkpointRoot, 'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_VALIDATION_TRACE', $checkpointTrace, 'Process'
    )
    & $isolatedWriter -Agent 'smoke-1' -Type message -Status info `
        -TaskId 'checkpoint-bootstrap' -Message 'checkpoint-bootstrap' `
        -PayloadJson '{}' | Out-Null
    $bootstrapModes = @([System.IO.File]::ReadAllLines($checkpointTrace))
    $checkpointObject = Get-Content -LiteralPath $checkpointPath -Raw |
        ConvertFrom-Json
    [System.IO.File]::WriteAllText($checkpointTrace, '')
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_FAIL_ON_FULL_VALIDATION', '1', 'Process'
    )
    $fastPathError = ''
    try {
        & $isolatedWriter -Agent 'smoke-1' -Type message -Status info `
            -TaskId 'checkpoint-fast' -Message 'checkpoint-fast' `
            -PayloadJson '{}' | Out-Null
    } catch { $fastPathError = $_.Exception.Message }
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_FAIL_ON_FULL_VALIDATION', $null, 'Process'
    )
    $fastModes = @([System.IO.File]::ReadAllLines($checkpointTrace))
    Add-Check -Name 'checkpoint bootstraps then proves bounded fast path' -Passed (
        $bootstrapModes.Count -eq 1 -and
        @($bootstrapModes | Where-Object { $_ -ceq 'full' }).Count -eq 1 -and
        (Test-Path -LiteralPath $checkpointPath -PathType Leaf) -and
        [string]$checkpointObject.schema -ceq `
            'waggledance.bridge.append-v1-validation' -and
        (-not $fastPathError) -and
        $fastModes.Count -eq 1 -and
        @($fastModes | Where-Object { $_ -ceq 'checkpoint' }).Count -eq 1
    ) -Detail (
        "bootstrap=$($bootstrapModes -join ',') fast=$($fastModes -join ',') " +
        "error=$fastPathError"
    )

    # A missing checkpoint forces full validation and is recreated.
    Remove-Item -LiteralPath $checkpointPath -Force
    [System.IO.File]::WriteAllText($checkpointTrace, '')
    & $isolatedWriter -Agent 'smoke-1' -Type message -Status info `
        -TaskId 'checkpoint-missing' -Message 'checkpoint-missing' `
        -PayloadJson '{}' | Out-Null
    $missingModes = @([System.IO.File]::ReadAllLines($checkpointTrace))

    # A malformed checkpoint is never authoritative.
    [System.IO.File]::WriteAllText($checkpointPath, "not-json`n")
    [System.IO.File]::WriteAllText($checkpointTrace, '')
    & $isolatedWriter -Agent 'smoke-1' -Type message -Status info `
        -TaskId 'checkpoint-corrupt' -Message 'checkpoint-corrupt' `
        -PayloadJson '{}' | Out-Null
    $corruptModes = @([System.IO.File]::ReadAllLines($checkpointTrace))

    # Exact checkpoint bytes are part of the cache contract. Parse-equivalent
    # duplicate, type, trailing-space, reordered, and case-variant encodings
    # must all miss identically under Windows PowerShell 5.1 and PowerShell 7.
    $checkpointTestEncoding = New-Object System.Text.UTF8Encoding($false, $true)
    $validCheckpointText = [System.IO.File]::ReadAllText($checkpointPath)
    $duplicateCheckpointText = (
        '{"schema":"waggledance.bridge.append-v1-validation",' +
        $validCheckpointText.Substring(1)
    )
    [System.IO.File]::WriteAllText(
        $checkpointPath, $duplicateCheckpointText, $checkpointTestEncoding
    )
    [System.IO.File]::WriteAllText($checkpointTrace, '')
    & $isolatedWriter -Agent 'smoke-1' -Type message -Status info `
        -TaskId 'checkpoint-duplicate-member' `
        -Message 'checkpoint-duplicate-member' -PayloadJson '{}' | Out-Null
    $duplicateModes = @([System.IO.File]::ReadAllLines($checkpointTrace))

    $validCheckpointText = [System.IO.File]::ReadAllText($checkpointPath)
    $typeCheckpointText = $validCheckpointText.Replace(
        '"version":1',
        '"version":"1"'
    )
    [System.IO.File]::WriteAllText(
        $checkpointPath, $typeCheckpointText, $checkpointTestEncoding
    )
    [System.IO.File]::WriteAllText($checkpointTrace, '')
    & $isolatedWriter -Agent 'smoke-1' -Type message -Status info `
        -TaskId 'checkpoint-wrong-type' -Message 'checkpoint-wrong-type' `
        -PayloadJson '{}' | Out-Null
    $typeModes = @([System.IO.File]::ReadAllLines($checkpointTrace))

    $validCheckpointText = [System.IO.File]::ReadAllText($checkpointPath)
    $trailingCheckpointText = (
        $validCheckpointText.Substring(0, $validCheckpointText.Length - 1) +
        ' ' + [char]10
    )
    [System.IO.File]::WriteAllText(
        $checkpointPath, $trailingCheckpointText, $checkpointTestEncoding
    )
    [System.IO.File]::WriteAllText($checkpointTrace, '')
    & $isolatedWriter -Agent 'smoke-1' -Type message -Status info `
        -TaskId 'checkpoint-trailing-space' `
        -Message 'checkpoint-trailing-space' -PayloadJson '{}' | Out-Null
    $trailingModes = @([System.IO.File]::ReadAllLines($checkpointTrace))

    $validCheckpointText = [System.IO.File]::ReadAllText($checkpointPath)
    $orderedPrefix = (
        '{"schema":"waggledance.bridge.append-v1-validation","version":1,'
    )
    $reorderedPrefix = (
        '{"version":1,"schema":"waggledance.bridge.append-v1-validation",'
    )
    $reorderedCheckpointText = $validCheckpointText.Replace(
        $orderedPrefix,
        $reorderedPrefix
    )
    [System.IO.File]::WriteAllText(
        $checkpointPath, $reorderedCheckpointText, $checkpointTestEncoding
    )
    [System.IO.File]::WriteAllText($checkpointTrace, '')
    & $isolatedWriter -Agent 'smoke-1' -Type message -Status info `
        -TaskId 'checkpoint-reordered' -Message 'checkpoint-reordered' `
        -PayloadJson '{}' | Out-Null
    $reorderedModes = @([System.IO.File]::ReadAllLines($checkpointTrace))

    $validCheckpointText = [System.IO.File]::ReadAllText($checkpointPath)
    $caseCheckpointText = $validCheckpointText.Replace('"schema":', '"Schema":')
    [System.IO.File]::WriteAllText(
        $checkpointPath, $caseCheckpointText, $checkpointTestEncoding
    )
    [System.IO.File]::WriteAllText($checkpointTrace, '')
    & $isolatedWriter -Agent 'smoke-1' -Type message -Status info `
        -TaskId 'checkpoint-case-variant' `
        -Message 'checkpoint-case-variant' -PayloadJson '{}' | Out-Null
    $caseModes = @([System.IO.File]::ReadAllLines($checkpointTrace))
    Add-Check -Name 'checkpoint acceptance is exact duplicate-safe canonical bytes' -Passed (
        $duplicateModes.Count -eq 1 -and $duplicateModes[0] -ceq 'full' -and
        $typeModes.Count -eq 1 -and $typeModes[0] -ceq 'full' -and
        $trailingModes.Count -eq 1 -and $trailingModes[0] -ceq 'full' -and
        $reorderedModes.Count -eq 1 -and $reorderedModes[0] -ceq 'full' -and
        $caseModes.Count -eq 1 -and $caseModes[0] -ceq 'full'
    ) -Detail (
        "duplicate=$($duplicateModes -join ',') type=$($typeModes -join ',') " +
        "trailing=$($trailingModes -join ',') " +
        "reordered=$($reorderedModes -join ',') case=$($caseModes -join ',')"
    )

    # A valid external append creates an exact-length miss and is preserved.
    $externalEvent = '{"ts_utc":"2026-07-20T10:00:00Z","agent":"smoke-1","type":"message","task_id":"checkpoint-external","status":"info","message":"valid-external-append"}'
    [System.IO.File]::AppendAllText(
        $checkpointEvents,
        ($externalEvent + [char]10),
        (New-Object System.Text.UTF8Encoding($false, $true))
    )
    [System.IO.File]::WriteAllText($checkpointTrace, '')
    & $isolatedWriter -Agent 'smoke-1' -Type message -Status info `
        -TaskId 'checkpoint-stale-length' `
        -Message 'checkpoint-stale-length-recover' -PayloadJson '{}' | Out-Null
    $lengthModes = @([System.IO.File]::ReadAllLines($checkpointTrace))

    # Same-identity/same-length tail changes miss through the strong anchor.
    $tailText = [System.IO.File]::ReadAllText($checkpointEvents)
    $tailChangedText = $tailText.Replace(
        'checkpoint-stale-length-recover',
        'checkpoint-stale-length-RecoveR'
    )
    [System.IO.File]::WriteAllText(
        $checkpointEvents,
        $tailChangedText,
        (New-Object System.Text.UTF8Encoding($false, $true))
    )
    [System.IO.File]::WriteAllText($checkpointTrace, '')
    & $isolatedWriter -Agent 'smoke-1' -Type message -Status info `
        -TaskId 'checkpoint-stale-tail' -Message 'checkpoint-stale-tail' `
        -PayloadJson '{}' | Out-Null
    $tailModes = @([System.IO.File]::ReadAllLines($checkpointTrace))

    # A same-byte replacement has the same length and anchor but a new file ID.
    [byte[]]$replacementBytes = [System.IO.File]::ReadAllBytes($checkpointEvents)
    $replacementPath = "$checkpointEvents.replacement"
    [System.IO.File]::WriteAllBytes($replacementPath, $replacementBytes)
    [System.IO.File]::Delete($checkpointEvents)
    [System.IO.File]::Move($replacementPath, $checkpointEvents)
    [System.IO.File]::WriteAllText($checkpointTrace, '')
    & $isolatedWriter -Agent 'smoke-1' -Type message -Status info `
        -TaskId 'checkpoint-replaced-identity' `
        -Message 'checkpoint-replaced-identity' -PayloadJson '{}' | Out-Null
    $identityModes = @([System.IO.File]::ReadAllLines($checkpointTrace))
    Add-Check -Name 'checkpoint misses on missing corrupt length tail and identity changes' -Passed (
        $missingModes[0] -ceq 'full' -and
        $corruptModes[0] -ceq 'full' -and
        $lengthModes[0] -ceq 'full' -and
        $tailChangedText -cne $tailText -and
        $tailModes[0] -ceq 'full' -and
        $identityModes[0] -ceq 'full' -and
        ([System.IO.File]::ReadAllText($checkpointEvents) -match
            'valid-external-append')
    ) -Detail (
        "missing=$($missingModes -join ',') corrupt=$($corruptModes -join ',') " +
        "length=$($lengthModes -join ',') tail=$($tailModes -join ',') " +
        "identity=$($identityModes -join ',')"
    )

    # 21. A post-Flush checkpoint failure is durable success, not a blind-retry
    #     error. Keep one redundant WAL, then exact-dedup it on replay.
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_VALIDATION_TRACE', $null, 'Process'
    )
    $updateFailureRoot = New-TestBridgeRoot -Name 'checkpoint-update-failure'
    $updateFailureEvents = Join-Path $updateFailureRoot 'shared/events.jsonl'
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_RUNTIME_ROOT', $updateFailureRoot, 'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_CHECKPOINT_UPDATE_FAILURE',
        'AfterCanonicalOnce',
        'Process'
    )
    $updateFailureError = ''
    try {
        & $isolatedWriter -Agent 'smoke-1' -Type message -Status info `
            -TaskId 'checkpoint-update-failure' `
            -Message 'checkpoint-update-failure-once' -PayloadJson '{}' `
            3>$null | Out-Null
    } catch { $updateFailureError = $_.Exception.Message }
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_CHECKPOINT_UPDATE_FAILURE', $null, 'Process'
    )
    $updateFailureLines = @([System.IO.File]::ReadAllLines($updateFailureEvents))
    $updateFailureSpools = @(
        Get-ChildItem -LiteralPath (Join-Path $updateFailureRoot 'spool') `
            -Filter 'failed-append-*.jsonl' -File -Force
    )
    $updateFailureReplay = & $isolatedReplay -BridgeRoot $updateFailureRoot
    Add-Check -Name 'checkpoint update failure returns success retains WAL and exact-dedups' -Passed (
        (-not $updateFailureError) -and
        @($updateFailureLines | Where-Object {
            $_ -match 'checkpoint-update-failure-once'
        }).Count -eq 1 -and
        $updateFailureSpools.Count -eq 1 -and
        ($updateFailureReplay -match 'replayed=0 deduped=1 failed=0')
    ) -Detail (
        "error=$updateFailureError lines=$($updateFailureLines.Count) " +
        "spools=$($updateFailureSpools.Count) replay=$updateFailureReplay"
    )

    # WAL cleanup also happens after durable canonical/checkpoint publication.
    # A cleanup failure retains redundant recovery state but is caller success.
    $cleanupFailureRoot = New-TestBridgeRoot -Name 'wal-cleanup-failure'
    $cleanupFailureEvents = Join-Path $cleanupFailureRoot 'shared/events.jsonl'
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_RUNTIME_ROOT', $cleanupFailureRoot, 'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_WAL_CLEANUP_FAILURE', 'Once', 'Process'
    )
    $cleanupFailureError = ''
    try {
        & $isolatedWriter -Agent 'smoke-1' -Type message -Status info `
            -TaskId 'wal-cleanup-failure' -Message 'wal-cleanup-failure-once' `
            -PayloadJson '{}' 3>$null | Out-Null
    } catch { $cleanupFailureError = $_.Exception.Message }
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_WAL_CLEANUP_FAILURE', $null, 'Process'
    )
    $cleanupFailureLines = @([System.IO.File]::ReadAllLines($cleanupFailureEvents))
    $cleanupFailureSpools = @(
        Get-ChildItem -LiteralPath (Join-Path $cleanupFailureRoot 'spool') `
            -Filter 'failed-append-*.jsonl' -File -Force
    )
    $cleanupFailureReplay = & $isolatedReplay -BridgeRoot $cleanupFailureRoot
    Add-Check -Name 'post-flush WAL cleanup failure returns success and exact-dedups' -Passed (
        (-not $cleanupFailureError) -and
        @($cleanupFailureLines | Where-Object {
            $_ -match 'wal-cleanup-failure-once'
        }).Count -eq 1 -and
        $cleanupFailureSpools.Count -eq 1 -and
        ($cleanupFailureReplay -match 'replayed=0 deduped=1 failed=0')
    ) -Detail (
        "error=$cleanupFailureError lines=$($cleanupFailureLines.Count) " +
        "spools=$($cleanupFailureSpools.Count) replay=$cleanupFailureReplay"
    )

    # The outbox is derived auxiliary state. A transactional auxiliary failure
    # rolls back best-effort without creating canonical replay WAL or changing
    # the caller outcome after shared/events.jsonl is already durable.
    $auxiliaryFailureRoot = New-TestBridgeRoot -Name 'auxiliary-outbox-failure'
    $auxiliaryFailureEvents = Join-Path $auxiliaryFailureRoot 'shared/events.jsonl'
    $auxiliaryFailureLast = Join-Path `
        (Join-Path $auxiliaryFailureRoot 'shared') 'last_smoke-1.json'
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_RUNTIME_ROOT', $auxiliaryFailureRoot, 'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_AUXILIARY_APPEND_FAILURE_AFTER_BYTES',
        '17',
        'Process'
    )
    $auxiliaryFailureError = ''
    $auxiliaryFailureOutput = @()
    try {
        $auxiliaryFailureOutput = @(
            & $isolatedWriter -Agent 'smoke-1' -Type message -Status info `
                -TaskId 'auxiliary-outbox-failure' `
                -Message 'auxiliary-outbox-failure-once' -PayloadJson '{}' 3>&1
        )
    } catch { $auxiliaryFailureError = $_.Exception.Message }
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_AUXILIARY_APPEND_FAILURE_AFTER_BYTES',
        $null,
        'Process'
    )
    $auxiliaryCanonicalLines = @(
        [System.IO.File]::ReadAllLines($auxiliaryFailureEvents) |
            Where-Object { $_ -match 'auxiliary-outbox-failure-once' }
    )
    $auxiliarySpools = @(
        Get-ChildItem -LiteralPath (Join-Path $auxiliaryFailureRoot 'spool') `
            -File -Force -ErrorAction SilentlyContinue
    )
    $auxiliaryOutboxLines = @(
        Get-ChildItem -LiteralPath (Join-Path $auxiliaryFailureRoot 'outbox') `
            -Filter '*.jsonl' -File -Recurse -ErrorAction SilentlyContinue |
            ForEach-Object { [System.IO.File]::ReadAllLines($_.FullName) } |
            Where-Object { $_ -match 'auxiliary-outbox-failure-once' }
    )
    $auxiliaryLastText = if (Test-Path -LiteralPath $auxiliaryFailureLast) {
        [System.IO.File]::ReadAllText($auxiliaryFailureLast)
    } else { '' }
    $auxiliaryBeforeReplay = [Convert]::ToBase64String(
        [System.IO.File]::ReadAllBytes($auxiliaryFailureEvents)
    )
    $auxiliaryReplayOutput = & $isolatedReplay -BridgeRoot $auxiliaryFailureRoot
    $auxiliaryAfterReplay = [Convert]::ToBase64String(
        [System.IO.File]::ReadAllBytes($auxiliaryFailureEvents)
    )
    $auxiliaryCallerEvent = @(
        $auxiliaryFailureOutput | Where-Object {
            $_ -is [System.Management.Automation.PSCustomObject] -and
            $_.PSObject.Properties['task_id'] -and
            [string]$_.task_id -ceq 'auxiliary-outbox-failure'
        }
    )
    Add-Check -Name 'auxiliary failure keeps canonical once without replay WAL' -Passed (
        (-not $auxiliaryFailureError) -and
        $auxiliaryCallerEvent.Count -eq 1 -and
        $auxiliaryCanonicalLines.Count -eq 1 -and
        $auxiliarySpools.Count -eq 0 -and
        $auxiliaryOutboxLines.Count -eq 0 -and
        $auxiliaryLastText -match 'auxiliary-outbox-failure-once' -and
        ($auxiliaryReplayOutput -match 'nothing to replay') -and
        $auxiliaryBeforeReplay -ceq $auxiliaryAfterReplay
    ) -Detail (
        "error=$auxiliaryFailureError caller=$($auxiliaryCallerEvent.Count) " +
        "canonical=$($auxiliaryCanonicalLines.Count) spool=$($auxiliarySpools.Count) " +
        "outbox=$($auxiliaryOutboxLines.Count) replay=$auxiliaryReplayOutput"
    )

    # Unsupported-platform refusal must occur before either implementation can
    # create shared/ or OpenOrCreate the canonical file. This Windows-hosted
    # static ordering assertion covers the otherwise unreachable platform path.
    $writerGateSource = [System.IO.File]::ReadAllText($writerScript)
    $writerCanonicalStart = $writerGateSource.IndexOf(
        'function Invoke-BridgeCanonicalTransactionalAppend'
    )
    $writerNativeGate = $writerGateSource.IndexOf(
        '    Initialize-BridgeAppendV1Native',
        $writerCanonicalStart
    )
    $writerParentCreate = $writerGateSource.IndexOf(
        '    $parent = Split-Path -Parent $Path',
        $writerCanonicalStart
    )
    $writerOpenCreate = $writerGateSource.IndexOf(
        '[System.IO.FileMode]::OpenOrCreate',
        $writerCanonicalStart
    )
    $replayGateSource = [System.IO.File]::ReadAllText($replayScript)
    $replayAppendStart = $replayGateSource.IndexOf(
        'function Invoke-BridgeTransactionalAppend'
    )
    $replayNativeGate = $replayGateSource.IndexOf(
        '    Initialize-BridgeAppendV1Native',
        $replayAppendStart
    )
    $replayParentCreate = $replayGateSource.IndexOf(
        '    $parent = Split-Path -Parent $Path',
        $replayAppendStart
    )
    $replayOpenCreate = $replayGateSource.IndexOf(
        '[System.IO.FileMode]::OpenOrCreate',
        $replayAppendStart
    )
    Add-Check -Name 'native gate precedes canonical parent and file creation' -Passed (
        $writerCanonicalStart -ge 0 -and
        $writerNativeGate -gt $writerCanonicalStart -and
        $writerNativeGate -lt $writerParentCreate -and
        $writerParentCreate -lt $writerOpenCreate -and
        $replayAppendStart -ge 0 -and
        $replayNativeGate -gt $replayAppendStart -and
        $replayNativeGate -lt $replayParentCreate -and
        $replayParentCreate -lt $replayOpenCreate -and
        -not $writerGateSource.Contains(
            'foreach ($dir in @($sharedDir, $outboxDir))'
        ) -and
        $writerGateSource.Contains('Add-CanonicalLineWithWal -Line $line') -and
        $writerGateSource.Contains(
            'Add-AuxiliaryLineBestEffort -Path $outboxPath -Line $line'
        )
    ) -Detail (
        "writer=$writerCanonicalStart/$writerNativeGate/" +
        "$writerParentCreate/$writerOpenCreate replay=$replayAppendStart/" +
        "$replayNativeGate/$replayParentCreate/$replayOpenCreate"
    )

    # 22. Kill a writer after canonical Flush(true) but before checkpoint
    #     advance. The hidden pending WAL survives and replay exact-dedups it.
    $crashRoot = New-TestBridgeRoot -Name 'crash-before-checkpoint'
    $crashEvents = Join-Path $crashRoot 'shared/events.jsonl'
    $crashReady = Join-Path $tempRoot 'crash-before-checkpoint.ready'
    $crashHelper = Join-Path $isolatedBin 'Crash-Before-Checkpoint.ps1'
    $crashHelperSource = @'
param([string] $WriterPath, [string] $Root, [string] $ReadyPath)
$ErrorActionPreference = 'Stop'
$env:AGENT_BRIDGE_RUNTIME_ROOT = $Root
$env:AGENT_BRIDGE_TEST_AFTER_CANONICAL_BEFORE_CHECKPOINT = $ReadyPath
& $WriterPath -Agent 'smoke-1' -Type message -Status info `
    -TaskId 'crash-before-checkpoint' -Message 'crash-before-checkpoint-once' `
    -PayloadJson '{}' | Out-Null
'@
    [System.IO.File]::WriteAllText($crashHelper, $crashHelperSource, $utf8)
    $crashProcess = Start-Process -FilePath (Get-Process -Id $PID).Path `
        -ArgumentList @(
            '-NoLogo', '-NoProfile', '-NonInteractive',
            '-ExecutionPolicy', 'Bypass', '-File', "`"$crashHelper`"",
            '-WriterPath', "`"$isolatedWriter`"", '-Root', "`"$crashRoot`"",
            '-ReadyPath', "`"$crashReady`""
        ) -PassThru -WindowStyle Hidden
    $crashReached = $false
    try {
        for ($attempt = 0; $attempt -lt 400; $attempt++) {
            if (Test-Path -LiteralPath $crashReady -PathType Leaf) {
                $crashReached = $true
                break
            }
            if ($crashProcess.HasExited) { break }
            Start-Sleep -Milliseconds 25
        }
        if ($crashReached -and -not $crashProcess.HasExited) {
            Stop-Process -Id $crashProcess.Id -Force
            $crashProcess.WaitForExit()
        }
    } finally {
        if (-not $crashProcess.HasExited) {
            Stop-Process -Id $crashProcess.Id -Force -ErrorAction SilentlyContinue
            $crashProcess.WaitForExit()
        }
    }
    $crashPending = @(
        Get-ChildItem -LiteralPath (Join-Path $crashRoot 'spool') `
            -Filter '.*.pending' -File -Force -ErrorAction SilentlyContinue
    )
    $crashLinesBeforeReplay = if (Test-Path -LiteralPath $crashEvents) {
        @([System.IO.File]::ReadAllLines($crashEvents))
    } else { @() }
    $crashFirstReplay = & $isolatedReplay -BridgeRoot $crashRoot
    $crashSecondReplay = & $isolatedReplay -BridgeRoot $crashRoot
    $crashLinesAfterReplay = @([System.IO.File]::ReadAllLines($crashEvents))
    Add-Check -Name 'crash after canonical flush retains pending WAL and exact-dedups' -Passed (
        $crashReached -and
        $crashPending.Count -eq 1 -and
        @($crashLinesBeforeReplay | Where-Object {
            $_ -match 'crash-before-checkpoint-once'
        }).Count -eq 1 -and
        (
            ($crashFirstReplay -match 'replayed=0 deduped=1 failed=0') -or
            (
                ($crashFirstReplay -match 'dirty abandoned') -and
                ($crashSecondReplay -match 'replayed=0 deduped=1 failed=0')
            )
        ) -and
        @($crashLinesAfterReplay | Where-Object {
            $_ -match 'crash-before-checkpoint-once'
        }).Count -eq 1
    ) -Detail (
        "reached=$crashReached pending=$($crashPending.Count) " +
        "first=$crashFirstReplay second=$crashSecondReplay"
    )

    # 23. A truncate+replay can restore the exact prior identity, bytes,
    #     length, and tail. Durable invalidation still prevents stale trust.
    $equalReplayRoot = New-TestBridgeRoot -Name 'equal-truncate-replay'
    $equalReplayEvents = Join-Path $equalReplayRoot 'shared/events.jsonl'
    $equalReplayCheckpoint = "$equalReplayEvents.append-v1-validation.json"
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_RUNTIME_ROOT', $equalReplayRoot, 'Process'
    )
    & $isolatedWriter -Agent 'smoke-1' -Type message -Status info `
        -TaskId 'equal-truncate-replay' -Message 'equal-truncate-replay' `
        -PayloadJson '{}' | Out-Null
    [byte[]]$equalOriginalBytes = [System.IO.File]::ReadAllBytes($equalReplayEvents)
    $equalStream = New-Object System.IO.FileStream(
        $equalReplayEvents,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::Read
    )
    try {
        $equalStream.SetLength($equalOriginalBytes.Length - 1)
        $equalStream.Flush($true)
    } finally { $equalStream.Dispose() }
    $equalReplaySpool = Join-Path (Join-Path $equalReplayRoot 'spool') `
        'failed-append-smoke-1-equal-truncate-replay.jsonl'
    $equalEventText = (New-Object System.Text.UTF8Encoding($false, $true)).GetString(
        $equalOriginalBytes,
        0,
        $equalOriginalBytes.Length - 1
    )
    Write-TestWal -Path $equalReplaySpool -Text $equalEventText
    $equalReplayOutput = & $isolatedReplay -BridgeRoot $equalReplayRoot 3>$null
    [byte[]]$equalFinalBytes = [System.IO.File]::ReadAllBytes($equalReplayEvents)
    $equalCheckpointObject = Get-Content -LiteralPath $equalReplayCheckpoint -Raw |
        ConvertFrom-Json
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_RUNTIME_ROOT', $equalReplayRoot, 'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_FAIL_ON_FULL_VALIDATION', '1', 'Process'
    )
    $equalCheckpointMissError = ''
    try {
        & $isolatedWriter -Agent 'smoke-1' -Type message -Status info `
            -TaskId 'equal-checkpoint-miss' -Message 'equal-checkpoint-miss' `
            -PayloadJson '{}' | Out-Null
    } catch { $equalCheckpointMissError = $_.Exception.Message }
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_FAIL_ON_FULL_VALIDATION', $null, 'Process'
    )
    Add-Check -Name 'equal-byte truncate replay durably invalidates checkpoint' -Passed (
        ($equalReplayOutput -match 'replayed=1 deduped=0 failed=0') -and
        [Convert]::ToBase64String($equalOriginalBytes) -ceq
            [Convert]::ToBase64String($equalFinalBytes) -and
        [string]$equalCheckpointObject.schema -ceq `
            'waggledance.bridge.append-v1-validation-invalidated' -and
        ($equalCheckpointMissError -match 'full canonical validation')
    ) -Detail (
        "replay=$equalReplayOutput schema=$($equalCheckpointObject.schema) " +
        "miss=$equalCheckpointMissError"
    )

    # Invalidation failure occurs before replayer mutation and retains the WAL.
    $invalidateFailureRoot = New-TestBridgeRoot -Name 'invalidation-failure'
    $invalidateFailureEvents = Join-Path $invalidateFailureRoot 'shared/events.jsonl'
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_RUNTIME_ROOT', $invalidateFailureRoot, 'Process'
    )
    & $isolatedWriter -Agent 'smoke-1' -Type message -Status info `
        -TaskId 'invalidation-seed' -Message 'invalidation-seed' `
        -PayloadJson '{}' | Out-Null
    [byte[]]$invalidateBefore = [System.IO.File]::ReadAllBytes($invalidateFailureEvents)
    $invalidateFailureSpool = Join-Path `
        (Join-Path $invalidateFailureRoot 'spool') `
        'failed-append-smoke-1-invalidation-failure.jsonl'
    $invalidateEvent = '{"ts_utc":"2026-07-20T10:10:00Z","agent":"smoke-1","type":"message","task_id":"invalidation-failure","status":"info","message":"invalidation-failure"}'
    Write-TestWal -Path $invalidateFailureSpool -Text $invalidateEvent
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_CHECKPOINT_INVALIDATION_FAILURE', '1', 'Process'
    )
    $invalidateFailureOutput = & $isolatedReplay `
        -BridgeRoot $invalidateFailureRoot 3>$null
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_CHECKPOINT_INVALIDATION_FAILURE', $null, 'Process'
    )
    [byte[]]$invalidateAfter = [System.IO.File]::ReadAllBytes($invalidateFailureEvents)
    Add-Check -Name 'checkpoint invalidation failure retains WAL before mutation' -Passed (
        ($invalidateFailureOutput -match 'failed=1') -and
        (Test-Path -LiteralPath $invalidateFailureSpool -PathType Leaf) -and
        [Convert]::ToBase64String($invalidateBefore) -ceq
            [Convert]::ToBase64String($invalidateAfter)
    ) -Detail "out=$invalidateFailureOutput"
} finally {
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_RUNTIME_ROOT', $previousRuntimeRoot, 'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_MUTEX_CONSTRUCTION_FAILURE',
        $previousForcedFailure,
        'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_APPEND_FAILURE_AFTER_BYTES',
        $previousPartialFailure,
        'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_BEFORE_APPEND_READY',
        $previousBeforeAppendReady,
        'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_VALIDATION_TRACE',
        $previousValidationTrace,
        'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_FAIL_ON_FULL_VALIDATION',
        $previousFailOnFullValidation,
        'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_CHECKPOINT_UPDATE_FAILURE',
        $previousCheckpointUpdateFailure,
        'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_CHECKPOINT_INVALIDATION_FAILURE',
        $previousCheckpointInvalidationFailure,
        'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_AFTER_CANONICAL_BEFORE_CHECKPOINT',
        $previousAfterCanonicalReady,
        'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_WAL_CLEANUP_FAILURE',
        $previousWalCleanupFailure,
        'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_AUXILIARY_APPEND_FAILURE_AFTER_BYTES',
        $previousAuxiliaryAppendFailure,
        'Process'
    )
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
