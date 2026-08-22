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
$drainScript = Join-Path $PSScriptRoot 'Drain-AcceptedBridgeQueue.ps1'
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
$previousCanonicalScanReady = [Environment]::GetEnvironmentVariable(
    'AGENT_BRIDGE_TEST_CANONICAL_SCAN_READY',
    'Process'
)
$previousPendingVerifyFailure = [Environment]::GetEnvironmentVariable(
    'AGENT_BRIDGE_TEST_PENDING_VERIFY_FAILURE',
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

function Write-TestWal {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $Text
    )
    $encoding = New-Object System.Text.UTF8Encoding($false, $true)
    [System.IO.File]::WriteAllText($Path, ($Text + [char]10), $encoding)
}

function Write-TestAcceptedWal {
    param(
        [Parameter(Mandatory)] [string] $Root,
        [Parameter(Mandatory)] [string] $WalId,
        [Parameter(Mandatory)] [string] $Text,
        [switch] $Pending
    )

    $acceptedDir = Join-Path (Join-Path $Root 'spool') 'accepted-v1'
    foreach ($name in @('pending', 'ready', 'replayed', 'quarantine')) {
        [void](New-Item -ItemType Directory `
            -Path (Join-Path $acceptedDir $name) -Force)
    }
    $leaf = "bridge-wal-v1-$WalId.jsonl"
    $path = if ($Pending) {
        Join-Path (Join-Path $acceptedDir 'pending') $leaf
    } else {
        Join-Path (Join-Path $acceptedDir 'ready') $leaf
    }
    Write-TestWal -Path $path -Text $Text
    $sha256 = (Get-FileHash `
        -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    $markerPath = Join-Path (Join-Path $acceptedDir 'ready') `
        ".${leaf}.pending-recovery-blocked"
    $marker = [ordered]@{
        schema = 'waggledance.bridge.accepted-pending-block.v1'
        wal_leaf = $leaf
        expected_sha256 = $sha256
        created_at_utc = [DateTime]::UtcNow.ToString('o')
    } | ConvertTo-Json -Compress
    Write-TestWal -Path $markerPath -Text $marker
    return [pscustomobject]@{
        Leaf = $leaf
        Path = $path
        Sha256 = $sha256
        MarkerPath = $markerPath
        ReplayedPath = Join-Path (Join-Path $acceptedDir 'replayed') $leaf
    }
}

function Get-TestAcceptedWalFiles {
    param(
        [Parameter(Mandatory)] [string] $Root,
        [Parameter(Mandatory)] [ValidateSet('pending', 'ready', 'replayed')]
        [string] $State
    )

    $directory = Join-Path `
        (Join-Path (Join-Path $Root 'spool') 'accepted-v1') $State
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
        return
    }
    Get-ChildItem -LiteralPath $directory `
        -Filter 'bridge-wal-v1-*.jsonl' -File -Force `
        -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -cmatch '^bridge-wal-v1-[0-9a-f]{32}\.jsonl$'
        } |
        Sort-Object Name
}

function Invoke-TestAcceptedReceiptReplay {
    param(
        [Parameter(Mandatory)] [string] $ReplayScript,
        [Parameter(Mandatory)] [string] $Root,
        [Parameter(Mandatory)] $Delivery
    )

    if (
        -not [string]$Delivery.wal_leaf -or
        -not [string]$Delivery.retained_wal_sha256
    ) {
        throw 'test delivery receipt does not identify a retained accepted WAL'
    }
    & $ReplayScript -BridgeRoot $Root `
        -AcceptedWalLeaf ([string]$Delivery.wal_leaf) `
        -ExpectedWalSha256 ([string]$Delivery.retained_wal_sha256)
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

    # Runtime cases use exact script copies whose V1 names carry a unique Local
    # namespace suffix. This exercises the same fence without either colliding
    # with or blocking the live machine-wide bridge mutex.
    $isolationId = [guid]::NewGuid().ToString('N')
    $isolatedBin = Join-Path $tempRoot 'isolated-bin'
    [void](New-Item -ItemType Directory -Path $isolatedBin -Force)
    $isolatedPublicationName = (
        "Local\WaggleDanceBridgeAcceptedQueuePublicationV1-$isolationId"
    )
    $isolatedAppendName = "Local\WaggleDanceBridgeAppendV1-$isolationId"
    $isolatedReplayName = "Local\WaggleDanceBridgeSpoolReplayV1-$isolationId"
    $isolatedWriter = Join-Path $isolatedBin 'Write-AgentEvent.ps1'
    $isolatedReplay = Join-Path $isolatedBin 'Restore-BridgeSpool.ps1'
    $isolatedDrain = Join-Path $isolatedBin 'Drain-AcceptedBridgeQueue.ps1'
    $raceReplay = Join-Path $isolatedBin 'Restore-BridgeSpool-Race.ps1'
    $enumerationReplay = Join-Path $isolatedBin 'Restore-BridgeSpool-Enumeration.ps1'
    $leaseReplay = Join-Path $isolatedBin 'Restore-BridgeSpool-Lease.ps1'
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $writerSource = [System.IO.File]::ReadAllText($writerScript).Replace(
        'Global\WaggleDanceBridgeAcceptedQueuePublicationV1',
        $isolatedPublicationName
    ).Replace(
        'Global\WaggleDanceBridgeAppendV1',
        $isolatedAppendName
    )
    $replaySource = [System.IO.File]::ReadAllText($replayScript)
    $replaySource = $replaySource.Replace(
        'Global\WaggleDanceBridgeAcceptedQueuePublicationV1',
        $isolatedPublicationName
    )
    $replaySource = $replaySource.Replace(
        'Global\WaggleDanceBridgeAppendV1',
        $isolatedAppendName
    )
    $replaySource = $replaySource.Replace(
        'Global\WaggleDanceBridgeSpoolReplayV1',
        $isolatedReplayName
    )
    $drainSource = [System.IO.File]::ReadAllText($drainScript)
    if (-not $drainSource.Contains(
        'Global\WaggleDanceBridgeAcceptedQueuePublicationV1'
    )) {
        throw 'drain smoke could not locate the queue publication fence'
    }
    $drainSource = $drainSource.Replace(
        'Global\WaggleDanceBridgeAcceptedQueuePublicationV1',
        $isolatedPublicationName
    )
    if (-not $drainSource.Contains('Global\WaggleDanceBridgeAppendV1')) {
        throw 'drain smoke could not locate the AppendV1 coordination mutex'
    }
    $drainSource = $drainSource.Replace(
        'Global\WaggleDanceBridgeAppendV1',
        $isolatedAppendName
    )
    [System.IO.File]::WriteAllText($isolatedWriter, $writerSource, $utf8)
    [System.IO.File]::WriteAllText($isolatedReplay, $replaySource, $utf8)
    [System.IO.File]::WriteAllText(
        $isolatedDrain,
        $drainSource,
        $utf8
    )
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
    $enumerationNeedle = '    $pendingFiles = @('
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
        '    # Initial AppendV1 ownership covers exact accepted-target selection or'
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

    # Accepted-target replay is opt-in, exact-leaf only, and independent from
    # malformed historical backlog in the legacy spool root.
    $noModeRoot = New-TestBridgeRoot -Name 'accepted-no-mode'
    $noModeLegacy = Join-Path (Join-Path $noModeRoot 'spool') `
        'failed-append-malformed-history.jsonl'
    Write-TestWal -Path $noModeLegacy -Text '{not-json'
    $noModeLegacyHash = (Get-FileHash `
        -LiteralPath $noModeLegacy -Algorithm SHA256).Hash
    $noModeError = ''
    try { & $isolatedReplay -BridgeRoot $noModeRoot | Out-Null }
    catch { $noModeError = $_.Exception.Message }
    Add-Check -Name 'replay mode is required and no mode never scans legacy' -Passed (
        ($noModeError -match 'replay mode is required') -and
        (Test-Path -LiteralPath $noModeLegacy -PathType Leaf) -and
        $noModeLegacyHash -ceq (Get-FileHash `
            -LiteralPath $noModeLegacy -Algorithm SHA256).Hash
    ) -Detail "error=$noModeError"

    $acceptedRoot = New-TestBridgeRoot -Name 'accepted-target-only'
    $acceptedLegacy = Join-Path (Join-Path $acceptedRoot 'spool') `
        'failed-append-unrelated-malformed.jsonl'
    Write-TestWal -Path $acceptedLegacy -Text '{still-not-json'
    $acceptedLegacyHash = (Get-FileHash `
        -LiteralPath $acceptedLegacy -Algorithm SHA256).Hash
    $acceptedEvent = '{"ts_utc":"2026-08-20T10:00:00Z","agent":"smoke-1","type":"message","task_id":"accepted-target-only","status":"info","message":"accepted-target-only"}'
    $acceptedWal = Write-TestAcceptedWal -Root $acceptedRoot `
        -WalId '11111111111111111111111111111111' -Text $acceptedEvent
    $acceptedOut = & $isolatedReplay -BridgeRoot $acceptedRoot `
        -AcceptedWalLeaf $acceptedWal.Leaf `
        -ExpectedWalSha256 $acceptedWal.Sha256
    $acceptedEvents = Join-Path $acceptedRoot 'shared/events.jsonl'
    Add-Check -Name 'target replay ignores unrelated malformed legacy backlog' -Passed (
        ($acceptedOut -match 'replayed=1 deduped=0 failed=0') -and
        (Test-Path -LiteralPath $acceptedLegacy -PathType Leaf) -and
        $acceptedLegacyHash -ceq (Get-FileHash `
            -LiteralPath $acceptedLegacy -Algorithm SHA256).Hash -and
        (-not (Test-Path -LiteralPath $acceptedWal.Path)) -and
        (Test-Path -LiteralPath $acceptedWal.ReplayedPath -PathType Leaf) -and
        [System.IO.File]::ReadAllText($acceptedEvents) -ceq
            ($acceptedEvent + [char]10)
    ) -Detail "out=$acceptedOut"

    $acceptedCanonicalHash = (Get-FileHash `
        -LiteralPath $acceptedEvents -Algorithm SHA256).Hash
    $alreadyDeliveredOut = & $isolatedReplay -BridgeRoot $acceptedRoot `
        -AcceptedWalLeaf $acceptedWal.Leaf `
        -ExpectedWalSha256 $acceptedWal.Sha256
    Add-Check -Name 'matching replayed leaf is explicit already-delivered no-op' -Passed (
        ($alreadyDeliveredOut -match 'already delivered') -and
        $acceptedCanonicalHash -ceq (Get-FileHash `
            -LiteralPath $acceptedEvents -Algorithm SHA256).Hash
    ) -Detail "out=$alreadyDeliveredOut"
    $archivedMismatchError = ''
    try {
        & $isolatedReplay -BridgeRoot $acceptedRoot `
            -AcceptedWalLeaf $acceptedWal.Leaf `
            -ExpectedWalSha256 ('0' * 64) | Out-Null
    } catch { $archivedMismatchError = $_.Exception.Message }
    Add-Check -Name 'replayed-leaf hash mismatch fails closed' -Passed (
        ($archivedMismatchError -match 'archived accepted WAL SHA-256 mismatch') -and
        $acceptedCanonicalHash -ceq (Get-FileHash `
            -LiteralPath $acceptedEvents -Algorithm SHA256).Hash
    ) -Detail "error=$archivedMismatchError"

    $acceptedExactRoot = New-TestBridgeRoot -Name 'accepted-exact-dedup'
    $acceptedExactEvent = '{"ts_utc":"2026-08-20T10:01:00Z","agent":"smoke-1","type":"message","task_id":"accepted-exact","status":"info","message":"accepted-exact"}'
    $acceptedExactEvents = Join-Path $acceptedExactRoot 'shared/events.jsonl'
    Write-TestWal -Path $acceptedExactEvents -Text $acceptedExactEvent
    $acceptedExactBefore = (Get-FileHash `
        -LiteralPath $acceptedExactEvents -Algorithm SHA256).Hash
    $acceptedExactWal = Write-TestAcceptedWal -Root $acceptedExactRoot `
        -WalId '22222222222222222222222222222222' `
        -Text $acceptedExactEvent
    $acceptedExactOut = & $isolatedReplay -BridgeRoot $acceptedExactRoot `
        -AcceptedWalLeaf $acceptedExactWal.Leaf `
        -ExpectedWalSha256 $acceptedExactWal.Sha256
    Add-Check -Name 'accepted target exact bytes deduplicate and archive' -Passed (
        ($acceptedExactOut -match 'replayed=0 deduped=1 failed=0') -and
        $acceptedExactBefore -ceq (Get-FileHash `
            -LiteralPath $acceptedExactEvents -Algorithm SHA256).Hash -and
        (-not (Test-Path -LiteralPath $acceptedExactWal.Path)) -and
        (Test-Path -LiteralPath $acceptedExactWal.ReplayedPath -PathType Leaf)
    ) -Detail "out=$acceptedExactOut"

    $acceptedDistinctRoot = New-TestBridgeRoot -Name 'accepted-byte-distinct'
    $acceptedDistinctEvents = Join-Path $acceptedDistinctRoot 'shared/events.jsonl'
    $acceptedDistinctFirst = '{"ts_utc":"2026-08-20T10:02:00Z","pid":101,"agent":"smoke-1","type":"message","task_id":"accepted-distinct","status":"info","message":"same-signal"}'
    $acceptedDistinctSecond = '{"ts_utc":"2026-08-20T10:03:00Z","pid":202,"agent":"smoke-1","type":"message","task_id":"accepted-distinct","status":"info","message":"same-signal"}'
    Write-TestWal -Path $acceptedDistinctEvents -Text $acceptedDistinctFirst
    $acceptedDistinctWal = Write-TestAcceptedWal `
        -Root $acceptedDistinctRoot `
        -WalId '33333333333333333333333333333333' `
        -Text $acceptedDistinctSecond
    $acceptedDistinctOut = & $isolatedReplay `
        -BridgeRoot $acceptedDistinctRoot `
        -AcceptedWalLeaf $acceptedDistinctWal.Leaf `
        -ExpectedWalSha256 $acceptedDistinctWal.Sha256
    $acceptedDistinctLines = @(
        [System.IO.File]::ReadAllLines($acceptedDistinctEvents)
    )
    Add-Check -Name 'byte-distinct same-semantic accepted event survives' -Passed (
        ($acceptedDistinctOut -match 'replayed=1 deduped=0 failed=0') -and
        $acceptedDistinctLines.Count -eq 2 -and
        $acceptedDistinctLines[0] -ceq $acceptedDistinctFirst -and
        $acceptedDistinctLines[1] -ceq $acceptedDistinctSecond
    ) -Detail "out=$acceptedDistinctOut lines=$($acceptedDistinctLines.Count)"

    $acceptedHashRoot = New-TestBridgeRoot -Name 'accepted-hash-fail-closed'
    $acceptedHashWal = Write-TestAcceptedWal -Root $acceptedHashRoot `
        -WalId '44444444444444444444444444444444' `
        -Text $acceptedEvent.Replace('accepted-target-only', 'accepted-hash')
    $acceptedHashError = ''
    try {
        & $isolatedReplay -BridgeRoot $acceptedHashRoot `
            -AcceptedWalLeaf $acceptedHashWal.Leaf `
            -ExpectedWalSha256 ('f' * 64) | Out-Null
    } catch { $acceptedHashError = $_.Exception.Message }
    $acceptedHashEvents = Join-Path $acceptedHashRoot 'shared/events.jsonl'
    Add-Check -Name 'ready target hash mismatch is fail closed' -Passed (
        ($acceptedHashError -match 'accepted WAL SHA-256 mismatch') -and
        (Test-Path -LiteralPath $acceptedHashWal.Path -PathType Leaf) -and
        (Get-BridgeTestFileLength -Path $acceptedHashEvents) -eq 0
    ) -Detail "error=$acceptedHashError"

    $acceptedPathError = ''
    try {
        & $isolatedReplay -BridgeRoot $acceptedHashRoot `
            -AcceptedWalLeaf '../bridge-wal-v1-44444444444444444444444444444444.jsonl' `
            -ExpectedWalSha256 $acceptedHashWal.Sha256 | Out-Null
    } catch { $acceptedPathError = $_.Exception.Message }
    Add-Check -Name 'accepted target path input is anchored and fail closed' -Passed (
        ($acceptedPathError -match 'accepted WAL leaf must match') -and
        (Test-Path -LiteralPath $acceptedHashWal.Path -PathType Leaf) -and
        (Get-BridgeTestFileLength -Path $acceptedHashEvents) -eq 0
    ) -Detail "error=$acceptedPathError"

    $acceptedArchiveCollisionRoot = New-TestBridgeRoot `
        -Name 'accepted-archive-collision'
    $acceptedArchiveCollisionWal = Write-TestAcceptedWal `
        -Root $acceptedArchiveCollisionRoot `
        -WalId '45454545454545454545454545454545' `
        -Text $acceptedEvent.Replace(
            'accepted-target-only',
            'accepted-archive-collision'
        )
    Write-TestWal -Path $acceptedArchiveCollisionWal.ReplayedPath `
        -Text $acceptedEvent.Replace(
            'accepted-target-only',
            'different-archive-authority'
        )
    $acceptedArchiveCollisionBefore = (Get-FileHash `
        -LiteralPath $acceptedArchiveCollisionWal.ReplayedPath `
        -Algorithm SHA256).Hash
    $acceptedArchiveCollisionError = ''
    try {
        & $isolatedReplay -BridgeRoot $acceptedArchiveCollisionRoot `
            -AcceptedWalLeaf $acceptedArchiveCollisionWal.Leaf `
            -ExpectedWalSha256 $acceptedArchiveCollisionWal.Sha256 | Out-Null
    } catch { $acceptedArchiveCollisionError = $_.Exception.Message }
    Add-Check -Name 'different accepted archive authority fails before append' -Passed (
        ($acceptedArchiveCollisionError -match
            'archive leaf collides with different bytes') -and
        (Test-Path -LiteralPath $acceptedArchiveCollisionWal.Path -PathType Leaf) -and
        $acceptedArchiveCollisionBefore -ceq (Get-FileHash `
            -LiteralPath $acceptedArchiveCollisionWal.ReplayedPath `
            -Algorithm SHA256).Hash -and
        (Get-BridgeTestFileLength -Path (Join-Path `
            $acceptedArchiveCollisionRoot 'shared/events.jsonl')) -eq 0
    ) -Detail "error=$acceptedArchiveCollisionError"

    $canonicalHardlinkRoot = New-TestBridgeRoot -Name 'canonical-hardlink'
    $canonicalHardlinkEvents = Join-Path `
        $canonicalHardlinkRoot 'shared/events.jsonl'
    $canonicalHardlinkSentinel = Join-Path `
        $tempRoot 'canonical-hardlink-outside-sentinel.jsonl'
    $canonicalHardlinkSeed = '{"ts_utc":"2026-08-20T10:04:00Z","agent":"sentinel","type":"status","status":"info","message":"outside-sentinel"}'
    Write-TestWal -Path $canonicalHardlinkSentinel -Text $canonicalHardlinkSeed
    [void](New-Item -ItemType HardLink -Path $canonicalHardlinkEvents `
        -Target $canonicalHardlinkSentinel)
    $canonicalHardlinkBefore = (Get-FileHash `
        -LiteralPath $canonicalHardlinkSentinel -Algorithm SHA256).Hash
    $canonicalHardlinkWal = Write-TestAcceptedWal `
        -Root $canonicalHardlinkRoot `
        -WalId '55555555555555555555555555555555' `
        -Text $acceptedEvent.Replace(
            'accepted-target-only',
            'canonical-hardlink'
        )
    $canonicalHardlinkError = ''
    try {
        & $isolatedReplay -BridgeRoot $canonicalHardlinkRoot `
            -AcceptedWalLeaf $canonicalHardlinkWal.Leaf `
            -ExpectedWalSha256 $canonicalHardlinkWal.Sha256 | Out-Null
    } catch { $canonicalHardlinkError = $_.Exception.Message }
    Add-Check -Name 'target replay rejects hard-linked canonical destination' -Passed (
        ($canonicalHardlinkError -match 'exactly one hard-link name') -and
        (Test-Path -LiteralPath $canonicalHardlinkWal.Path -PathType Leaf) -and
        (-not (Test-Path -LiteralPath $canonicalHardlinkWal.ReplayedPath)) -and
        (-not (Test-Path -LiteralPath `
            "$canonicalHardlinkEvents.append-v1-validation.json")) -and
        $canonicalHardlinkBefore -ceq (Get-FileHash `
            -LiteralPath $canonicalHardlinkSentinel -Algorithm SHA256).Hash -and
        $canonicalHardlinkBefore -ceq (Get-FileHash `
            -LiteralPath $canonicalHardlinkEvents -Algorithm SHA256).Hash
    ) -Detail "error=$canonicalHardlinkError"

    $directHardlinkRoot = New-TestBridgeRoot -Name 'writer-canonical-hardlink'
    $directHardlinkEvents = Join-Path $directHardlinkRoot 'shared/events.jsonl'
    $directHardlinkSentinel = Join-Path `
        $tempRoot 'writer-hardlink-outside-sentinel.jsonl'
    Write-TestWal -Path $directHardlinkSentinel -Text $canonicalHardlinkSeed
    [void](New-Item -ItemType HardLink -Path $directHardlinkEvents `
        -Target $directHardlinkSentinel)
    $directHardlinkBefore = (Get-FileHash `
        -LiteralPath $directHardlinkSentinel -Algorithm SHA256).Hash
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_RUNTIME_ROOT', $directHardlinkRoot, 'Process'
    )
    $directHardlinkResult = & $isolatedWriter `
        -Agent 'smoke-1' -Type status -Status open `
        -Message 'writer-hardlink-guard' -PayloadJson '{}'
    $directHardlinkDelivery = $directHardlinkResult._bridge_delivery
    Add-Check -Name 'writer queues without mutating hard-linked canonical' -Passed (
        [string]$directHardlinkDelivery.delivery_status -ceq 'queued' -and
        $directHardlinkDelivery.canonical_durable -eq $false -and
        (@($directHardlinkDelivery.warning_messages) -join ' ') -match
            'plain single-link' -and
        $directHardlinkBefore -ceq (Get-FileHash `
            -LiteralPath $directHardlinkSentinel -Algorithm SHA256).Hash -and
        $directHardlinkBefore -ceq (Get-FileHash `
            -LiteralPath $directHardlinkEvents -Algorithm SHA256).Hash -and
        (Test-Path -LiteralPath `
            ([string]$directHardlinkDelivery.retained_wal_path) -PathType Leaf)
    ) -Detail "delivery=$($directHardlinkDelivery | ConvertTo-Json -Compress)"

    $canonicalJunctionRoot = New-TestBridgeRoot -Name 'canonical-shared-junction'
    $canonicalJunctionPath = Join-Path $canonicalJunctionRoot 'shared'
    $canonicalJunctionTarget = Join-Path $tempRoot 'canonical-junction-target'
    Remove-Item -LiteralPath $canonicalJunctionPath -Force
    [void](New-Item -ItemType Directory -Path $canonicalJunctionTarget -Force)
    [void](New-Item -ItemType Junction -Path $canonicalJunctionPath `
        -Target $canonicalJunctionTarget)
    $canonicalJunctionWal = Write-TestAcceptedWal `
        -Root $canonicalJunctionRoot `
        -WalId '56565656565656565656565656565656' `
        -Text $acceptedEvent.Replace(
            'accepted-target-only',
            'canonical-shared-junction'
        )
    $canonicalJunctionOut = & $isolatedReplay `
        -BridgeRoot $canonicalJunctionRoot `
        -AcceptedWalLeaf $canonicalJunctionWal.Leaf `
        -ExpectedWalSha256 $canonicalJunctionWal.Sha256
    $canonicalJunctionEvents = Join-Path $canonicalJunctionTarget 'events.jsonl'
    Add-Check -Name 'target replay safely follows pinned shared junction' -Passed (
        ($canonicalJunctionOut -match 'replayed=1 deduped=0 failed=0') -and
        (Test-Path -LiteralPath $canonicalJunctionEvents -PathType Leaf) -and
        ([System.IO.File]::ReadAllText($canonicalJunctionEvents) -match
            'canonical-shared-junction') -and
        (-not (Test-Path -LiteralPath $canonicalJunctionWal.Path)) -and
        (Test-Path -LiteralPath $canonicalJunctionWal.ReplayedPath -PathType Leaf)
    ) -Detail "out=$canonicalJunctionOut"

    $targetAncestorOutsideParent = Join-Path $tempRoot `
        'target-replay-ancestor-outside'
    $targetAncestorOutsideRoot = New-TestBridgeRoot `
        -Name 'target-replay-ancestor-outside\bridge'
    $targetAncestorWal = Write-TestAcceptedWal `
        -Root $targetAncestorOutsideRoot `
        -WalId '57575757575757575757575757575757' `
        -Text $acceptedEvent.Replace(
            'accepted-target-only',
            'target-replay-ancestor-junction'
        )
    $targetAncestorWalHash = (Get-FileHash `
        -LiteralPath $targetAncestorWal.Path -Algorithm SHA256).Hash
    $targetAncestorMarkerHash = (Get-FileHash `
        -LiteralPath $targetAncestorWal.MarkerPath -Algorithm SHA256).Hash
    $targetAncestorContainer = Join-Path $tempRoot `
        'target-replay-ancestor-container'
    [void](New-Item -ItemType Directory -Path $targetAncestorContainer -Force)
    $targetAncestorLink = Join-Path $targetAncestorContainer 'parent-link'
    [void](New-Item -ItemType Junction -Path $targetAncestorLink `
        -Target $targetAncestorOutsideParent)
    $targetAncestorLexicalRoot = Join-Path $targetAncestorLink 'bridge'
    $targetAncestorError = ''
    try {
        & $isolatedReplay -BridgeRoot $targetAncestorLexicalRoot `
            -AcceptedWalLeaf $targetAncestorWal.Leaf `
            -ExpectedWalSha256 $targetAncestorWal.Sha256 | Out-Null
    } catch { $targetAncestorError = $_.Exception.Message }
    Add-Check -Name 'target replay rejects a BridgeRoot ancestor junction' `
        -Passed (
            ($targetAncestorError -match 'must not be a reparse point') -and
            (Test-Path -LiteralPath $targetAncestorWal.Path -PathType Leaf) -and
            $targetAncestorWalHash -ceq (Get-FileHash `
                -LiteralPath $targetAncestorWal.Path -Algorithm SHA256).Hash -and
            $targetAncestorMarkerHash -ceq (Get-FileHash `
                -LiteralPath $targetAncestorWal.MarkerPath `
                -Algorithm SHA256).Hash -and
            (-not (Test-Path -LiteralPath $targetAncestorWal.ReplayedPath)) -and
            (Get-BridgeTestFileLength -Path (Join-Path `
                $targetAncestorOutsideRoot 'shared/events.jsonl')) -eq 0
        ) -Detail "error=$targetAncestorError"

    # Accepted queue producers and the drainer reject a reparse ancestor before
    # creating, moving, deleting, or replaying anything through it.
    $queueJunctionRoot = Join-Path $tempRoot 'accepted-queue-junction-root'
    [void](New-Item -ItemType Directory `
        -Path (Join-Path $queueJunctionRoot 'shared') -Force)
    $queueJunctionOutside = New-TestBridgeRoot `
        -Name 'accepted-queue-junction-outside'
    $queueJunctionWal = Write-TestAcceptedWal `
        -Root $queueJunctionOutside `
        -WalId 'adadadadadadadadadadadadadadadad' `
        -Text $acceptedEvent.Replace(
            'accepted-target-only',
            'accepted-queue-junction'
        )
    $queueJunctionMarkerHash = (Get-FileHash `
        -LiteralPath $queueJunctionWal.MarkerPath -Algorithm SHA256).Hash
    $queueJunctionWalHash = (Get-FileHash `
        -LiteralPath $queueJunctionWal.Path -Algorithm SHA256).Hash
    [void](New-Item -ItemType Junction `
        -Path (Join-Path $queueJunctionRoot 'spool') `
        -Target (Join-Path $queueJunctionOutside 'spool'))
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_RUNTIME_ROOT', $queueJunctionRoot, 'Process'
    )
    $queueJunctionWriterError = ''
    try {
        & $isolatedWriter -Agent 'smoke-1' -Type status -Status open `
            -Message 'accepted-queue-junction-writer' -PayloadJson '{}' |
            Out-Null
    } catch { $queueJunctionWriterError = $_.Exception.Message }
    $queueJunctionDrainError = ''
    try {
        & $isolatedDrain -BridgeRoot $queueJunctionRoot `
            -PendingMinAgeSeconds 0 -ReceiptJson | Out-Null
    } catch { $queueJunctionDrainError = $_.Exception.Message }
    $queueEmptyJunctionRoot = Join-Path $tempRoot `
        'accepted-queue-empty-junction-root'
    $queueEmptyJunctionOutside = Join-Path $tempRoot `
        'accepted-queue-empty-junction-outside'
    [void](New-Item -ItemType Directory -Path $queueEmptyJunctionRoot -Force)
    [void](New-Item -ItemType Directory -Path $queueEmptyJunctionOutside -Force)
    [void](New-Item -ItemType Junction `
        -Path (Join-Path $queueEmptyJunctionRoot 'spool') `
        -Target $queueEmptyJunctionOutside)
    $queueEmptyJunctionDrainError = ''
    try {
        & $isolatedDrain -BridgeRoot $queueEmptyJunctionRoot `
            -ReceiptJson | Out-Null
    } catch { $queueEmptyJunctionDrainError = $_.Exception.Message }
    $queueAncestorContainer = Join-Path $tempRoot `
        'accepted-queue-ancestor-container'
    $queueAncestorOutside = Join-Path $tempRoot `
        'accepted-queue-ancestor-outside'
    [void](New-Item -ItemType Directory -Path $queueAncestorContainer -Force)
    [void](New-Item -ItemType Directory -Path $queueAncestorOutside -Force)
    $queueAncestorLink = Join-Path $queueAncestorContainer 'parent-link'
    [void](New-Item -ItemType Junction -Path $queueAncestorLink `
        -Target $queueAncestorOutside)
    $queueMissingBridgeRoot = Join-Path $queueAncestorLink 'missing-bridge'
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_RUNTIME_ROOT', $queueMissingBridgeRoot, 'Process'
    )
    $queueMissingRootWriterError = ''
    try {
        & $isolatedWriter -Agent 'smoke-1' -Type status -Status open `
            -Message 'accepted-queue-missing-root-junction' -PayloadJson '{}' |
            Out-Null
    } catch { $queueMissingRootWriterError = $_.Exception.Message }
    Add-Check -Name 'accepted queue rejects a spool junction before mutation' -Passed (
        ($queueJunctionWriterError -match 'reparse point') -and
        ($queueJunctionDrainError -match 'reparse point') -and
        ($queueEmptyJunctionDrainError -match 'reparse point') -and
        ($queueMissingRootWriterError -match 'reparse point') -and
        (-not (Test-Path -LiteralPath `
            (Join-Path $queueAncestorOutside 'missing-bridge'))) -and
        (Get-FileHash -LiteralPath $queueJunctionWal.Path `
            -Algorithm SHA256).Hash -ceq $queueJunctionWalHash -and
        (Get-FileHash -LiteralPath $queueJunctionWal.MarkerPath `
            -Algorithm SHA256).Hash -ceq $queueJunctionMarkerHash -and
        (Get-BridgeTestFileLength -Path (Join-Path `
            $queueJunctionRoot 'shared/events.jsonl')) -eq 0 -and
        @(Get-TestAcceptedWalFiles `
            -Root $queueJunctionOutside -State ready).Count -eq 1 -and
        @(Get-TestAcceptedWalFiles `
            -Root $queueJunctionOutside -State pending).Count -eq 0
    ) -Detail (
        "writer=$queueJunctionWriterError drain=$queueJunctionDrainError " +
        "empty=$queueEmptyJunctionDrainError missing=$queueMissingRootWriterError"
    )

    # The queue drainer isolates a malformed ready leaf, drains later valid
    # leaves, and intentionally leaves accepted pending plus legacy backlog.
    $drainRoot = New-TestBridgeRoot -Name 'accepted-drain-isolation'
    $drainLegacy = Join-Path (Join-Path $drainRoot 'spool') `
        'failed-append-drain-malformed.jsonl'
    Write-TestWal -Path $drainLegacy -Text '{legacy-malformed'
    $drainFirst = Write-TestAcceptedWal -Root $drainRoot `
        -WalId '66666666666666666666666666666666' `
        -Text $acceptedEvent.Replace('accepted-target-only', 'drain-first')
    $drainMalformed = Write-TestAcceptedWal -Root $drainRoot `
        -WalId '77777777777777777777777777777777' -Text '{ready-malformed'
    $drainLast = Write-TestAcceptedWal -Root $drainRoot `
        -WalId '88888888888888888888888888888888' `
        -Text $acceptedEvent.Replace('accepted-target-only', 'drain-last')
    $drainPending = Write-TestAcceptedWal -Root $drainRoot `
        -WalId '99999999999999999999999999999999' `
        -Text $acceptedEvent.Replace('accepted-target-only', 'drain-pending') `
        -Pending
    $drainReceiptText = & $isolatedDrain -BridgeRoot $drainRoot -ReceiptJson
    $drainReceipt = $drainReceiptText | ConvertFrom-Json
    $drainLines = @([System.IO.File]::ReadAllLines(
        (Join-Path $drainRoot 'shared/events.jsonl')
    ))
    Add-Check -Name 'accepted queue drain isolates failures and ignores pending and legacy' -Passed (
        [string]$drainReceipt.schema -ceq
            'waggledance.bridge.accepted-queue-drain.v1' -and
        [int]$drainReceipt.ready_seen -eq 3 -and
        [int]$drainReceipt.drained -eq 2 -and
        [int]$drainReceipt.failed -eq 1 -and
        $drainLines.Count -eq 2 -and
        (-not (Test-Path -LiteralPath $drainFirst.Path)) -and
        (Test-Path -LiteralPath $drainMalformed.Path -PathType Leaf) -and
        (-not (Test-Path -LiteralPath $drainLast.Path)) -and
        (Test-Path -LiteralPath $drainPending.Path -PathType Leaf) -and
        (Test-Path -LiteralPath $drainLegacy -PathType Leaf)
    ) -Detail "receipt=$drainReceiptText"

    $invalidLeafRoot = New-TestBridgeRoot -Name 'accepted-invalid-leaves'
    $invalidPendingDir = Join-Path `
        (Join-Path (Join-Path $invalidLeafRoot 'spool') 'accepted-v1') 'pending'
    $invalidReadyDir = Join-Path `
        (Join-Path (Join-Path $invalidLeafRoot 'spool') 'accepted-v1') 'ready'
    [void](New-Item -ItemType Directory -Path $invalidPendingDir -Force)
    [void](New-Item -ItemType Directory -Path $invalidReadyDir -Force)
    $invalidPendingLeaf = Join-Path $invalidPendingDir `
        'bridge-wal-v1-DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD.jsonl'
    $invalidReadyLeaf = Join-Path $invalidReadyDir 'unexpected-ready.tmp'
    Write-TestWal -Path $invalidPendingLeaf -Text $acceptedEvent
    Write-TestWal -Path $invalidReadyLeaf -Text $acceptedEvent
    $invalidLeafText = & $isolatedDrain -BridgeRoot $invalidLeafRoot -ReceiptJson
    $invalidLeafReceipt = $invalidLeafText | ConvertFrom-Json
    Add-Check -Name 'malformed accepted leaves fail visibly and remain untouched' -Passed (
        [int]$invalidLeafReceipt.failed -eq 2 -and
        [int]$invalidLeafReceipt.pending_failed -eq 1 -and
        (Test-Path -LiteralPath $invalidPendingLeaf -PathType Leaf) -and
        (Test-Path -LiteralPath $invalidReadyLeaf -PathType Leaf) -and
        (Get-BridgeTestFileLength -Path `
            (Join-Path $invalidLeafRoot 'shared/events.jsonl')) -eq 0
    ) -Detail "receipt=$invalidLeafText"

    # A pre-acceptance crash/failure may leave a strict pending-shaped row but
    # no durable producer digest. The drainer must never mint authority for it.
    $pendingOnlyRoot = New-TestBridgeRoot -Name 'accepted-pending-only'
    $pendingOnlyAccepted = Join-Path `
        (Join-Path $pendingOnlyRoot 'spool') 'accepted-v1'
    $pendingOnlyDir = Join-Path $pendingOnlyAccepted 'pending'
    [void](New-Item -ItemType Directory -Path $pendingOnlyDir -Force)
    $pendingOnlyLeaf = `
        'bridge-wal-v1-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.jsonl'
    $pendingOnlyPath = Join-Path $pendingOnlyDir $pendingOnlyLeaf
    $pendingOnlyEvent = $acceptedEvent.Replace(
        'accepted-target-only',
        'pending-only-python-crash'
    )
    Write-TestWal -Path $pendingOnlyPath -Text $pendingOnlyEvent
    $pendingOnlyDryRunText = & $isolatedDrain -BridgeRoot $pendingOnlyRoot `
        -PendingMinAgeSeconds 0 -DryRun -ReceiptJson
    $pendingOnlyDryRun = $pendingOnlyDryRunText | ConvertFrom-Json
    $pendingOnlyText = & $isolatedDrain -BridgeRoot $pendingOnlyRoot `
        -PendingMinAgeSeconds 0 -ReceiptJson
    $pendingOnlyReceipt = $pendingOnlyText | ConvertFrom-Json
    Add-Check -Name 'markerless pending WAL never gains replay authority' -Passed (
        [int]$pendingOnlyDryRun.pending_promoted -eq 0 -and
        [int]$pendingOnlyDryRun.pending_failed -eq 1 -and
        [int]$pendingOnlyDryRun.failed -eq 1 -and
        @($pendingOnlyDryRun.results | Where-Object {
            [string]$_.status -ceq 'pending_would_promote'
        }).Count -eq 0 -and
        [int]$pendingOnlyReceipt.pending_seen -eq 1 -and
        [int]$pendingOnlyReceipt.pending_promoted -eq 0 -and
        [int]$pendingOnlyReceipt.pending_failed -eq 1 -and
        [int]$pendingOnlyReceipt.drained -eq 0 -and
        [int]$pendingOnlyReceipt.failed -eq 1 -and
        (Test-Path -LiteralPath `
            (Join-Path $pendingOnlyAccepted 'ready') -PathType Container) -and
        (Test-Path -LiteralPath $pendingOnlyPath -PathType Leaf) -and
        (Get-BridgeTestFileLength -Path `
            (Join-Path $pendingOnlyRoot 'shared/events.jsonl')) -eq 0
    ) -Detail "dryRun=$pendingOnlyDryRunText receipt=$pendingOnlyText"

    $pendingMismatchRoot = New-TestBridgeRoot `
        -Name 'accepted-pending-marker-mismatch'
    $pendingMismatchWal = Write-TestAcceptedWal `
        -Root $pendingMismatchRoot `
        -WalId 'abababababababababababababababab' `
        -Text $acceptedEvent.Replace(
            'accepted-target-only',
            'pending-marker-mismatch'
        ) -Pending
    $pendingMismatchMarker = [ordered]@{
        schema = 'waggledance.bridge.accepted-pending-block.v1'
        wal_leaf = $pendingMismatchWal.Leaf
        expected_sha256 = ('0' * 64)
        created_at_utc = [DateTime]::UtcNow.ToString('o')
    } | ConvertTo-Json -Compress
    Write-TestWal -Path $pendingMismatchWal.MarkerPath `
        -Text $pendingMismatchMarker
    $pendingMismatchDryRunText = & $isolatedDrain `
        -BridgeRoot $pendingMismatchRoot `
        -PendingMinAgeSeconds 0 -DryRun -ReceiptJson
    $pendingMismatchDryRun = $pendingMismatchDryRunText | ConvertFrom-Json
    Add-Check -Name 'dry run rejects a pending digest mismatch' -Passed (
        [int]$pendingMismatchDryRun.pending_promoted -eq 0 -and
        [int]$pendingMismatchDryRun.pending_failed -eq 1 -and
        [int]$pendingMismatchDryRun.failed -eq 1 -and
        @($pendingMismatchDryRun.results | Where-Object {
            [string]$_.status -ceq 'pending_would_promote'
        }).Count -eq 0 -and
        (Test-Path -LiteralPath $pendingMismatchWal.Path -PathType Leaf) -and
        (Test-Path -LiteralPath `
            $pendingMismatchWal.MarkerPath -PathType Leaf) -and
        (Get-BridgeTestFileLength -Path (Join-Path `
            $pendingMismatchRoot 'shared/events.jsonl')) -eq 0
    ) -Detail "dryRun=$pendingMismatchDryRunText"

    $markerlessReadyRoot = New-TestBridgeRoot -Name 'accepted-ready-markerless'
    $markerlessReadyDir = Join-Path (Join-Path `
        (Join-Path $markerlessReadyRoot 'spool') 'accepted-v1') 'ready'
    [void](New-Item -ItemType Directory -Path $markerlessReadyDir -Force)
    $markerlessReadyPath = Join-Path $markerlessReadyDir `
        'bridge-wal-v1-acacacacacacacacacacacacacacacac.jsonl'
    Write-TestWal -Path $markerlessReadyPath -Text $acceptedEvent.Replace(
        'accepted-target-only',
        'markerless-ready'
    )
    $markerlessReadyText = & $isolatedDrain `
        -BridgeRoot $markerlessReadyRoot -ReceiptJson
    $markerlessReadyReceipt = $markerlessReadyText | ConvertFrom-Json
    Add-Check -Name 'markerless ready WAL never gains replay authority' -Passed (
        [int]$markerlessReadyReceipt.ready_seen -eq 1 -and
        [int]$markerlessReadyReceipt.drained -eq 0 -and
        [int]$markerlessReadyReceipt.failed -eq 1 -and
        (Test-Path -LiteralPath $markerlessReadyPath -PathType Leaf) -and
        (Get-BridgeTestFileLength -Path (Join-Path `
            $markerlessReadyRoot 'shared/events.jsonl')) -eq 0
    ) -Detail "receipt=$markerlessReadyText"

    # A durable recovery marker is resumable while the exact pending leaf is
    # still present and no ready path exists.
    $moveRetryRoot = New-TestBridgeRoot -Name 'accepted-pending-move-retry'
    $moveRetryWal = Write-TestAcceptedWal -Root $moveRetryRoot `
        -WalId 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee' `
        -Text $acceptedEvent.Replace(
            'accepted-target-only',
            'pending-move-retry'
        ) -Pending
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_PENDING_MOVE_FAILURE', '1', 'Process'
    )
    try {
        $moveRetryFirstText = & $isolatedDrain -BridgeRoot $moveRetryRoot `
            -PendingMinAgeSeconds 0 -ReceiptJson
    } finally {
        [Environment]::SetEnvironmentVariable(
            'AGENT_BRIDGE_TEST_PENDING_MOVE_FAILURE', $null, 'Process'
        )
    }
    $moveRetryFirst = $moveRetryFirstText | ConvertFrom-Json
    $moveRetryMarker = Join-Path `
        (Join-Path (Join-Path (Join-Path $moveRetryRoot 'spool') `
        'accepted-v1') 'ready') `
        ".$($moveRetryWal.Leaf).pending-recovery-blocked"
    $moveRetryPendingAndMarkerBefore = (
        (Test-Path -LiteralPath $moveRetryWal.Path -PathType Leaf) -and
        (Test-Path -LiteralPath $moveRetryMarker -PathType Leaf)
    )
    $moveRetrySecondText = & $isolatedDrain -BridgeRoot $moveRetryRoot `
        -PendingMinAgeSeconds 0 -ReceiptJson
    $moveRetrySecond = $moveRetrySecondText | ConvertFrom-Json
    Add-Check -Name 'pending publication failure resumes from bound marker' -Passed (
        [int]$moveRetryFirst.pending_failed -eq 1 -and
        $moveRetryPendingAndMarkerBefore -and
        (-not (Test-Path -LiteralPath $moveRetryWal.Path)) -and
        [int]$moveRetrySecond.pending_promoted -eq 1 -and
        [int]$moveRetrySecond.drained -eq 1 -and
        [int]$moveRetrySecond.failed -eq 0 -and
        (-not (Test-Path -LiteralPath $moveRetryMarker)) -and
        ([System.IO.File]::ReadAllText(
            (Join-Path $moveRetryRoot 'shared/events.jsonl')
        ) -match 'pending-move-retry')
    ) -Detail "first=$moveRetryFirstText second=$moveRetrySecondText"

    # A post-move verification failure leaves a durable expected digest. A
    # later drain can resume only when the ready bytes match that prior digest.
    $blockedReadyRoot = New-TestBridgeRoot -Name 'accepted-blocked-ready'
    $blockedReadyWal = Write-TestAcceptedWal -Root $blockedReadyRoot `
        -WalId 'cccccccccccccccccccccccccccccccc' `
        -Text $acceptedEvent.Replace(
            'accepted-target-only',
            'blocked-post-move-verification'
        ) -Pending
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_PENDING_VERIFY_FAILURE', '1', 'Process'
    )
    try {
        $blockedFirstText = & $isolatedDrain -BridgeRoot $blockedReadyRoot `
            -PendingMinAgeSeconds 0 -ReceiptJson
    } finally {
        [Environment]::SetEnvironmentVariable(
            'AGENT_BRIDGE_TEST_PENDING_VERIFY_FAILURE', $null, 'Process'
        )
    }
    $blockedFirst = $blockedFirstText | ConvertFrom-Json
    $blockedCanonicalBefore = Get-BridgeTestFileLength -Path `
        (Join-Path $blockedReadyRoot 'shared/events.jsonl')
    $blockedReadyPath = Join-Path `
        (Join-Path (Join-Path `
            (Join-Path $blockedReadyRoot 'spool') 'accepted-v1') 'ready') `
        $blockedReadyWal.Leaf
    $blockedReadyExistsBefore = Test-Path `
        -LiteralPath $blockedReadyPath -PathType Leaf
    $blockedMarkerPath = Join-Path (Split-Path -Parent $blockedReadyPath) (
        ".$($blockedReadyWal.Leaf).pending-recovery-blocked"
    )
    $blockedMarkerExistsBefore = Test-Path `
        -LiteralPath $blockedMarkerPath -PathType Leaf
    $blockedSecondText = & $isolatedDrain -BridgeRoot $blockedReadyRoot `
        -PendingMinAgeSeconds 0 -ReceiptJson
    $blockedSecond = $blockedSecondText | ConvertFrom-Json
    Add-Check -Name 'failed pending verification resumes by persisted digest' -Passed (
        [int]$blockedFirst.pending_failed -eq 1 -and
        [int]$blockedFirst.ready_seen -eq 0 -and
        [int]$blockedFirst.drained -eq 0 -and
        $blockedCanonicalBefore -eq 0 -and
        $blockedReadyExistsBefore -and
        $blockedMarkerExistsBefore -and
        [int]$blockedSecond.ready_seen -eq 1 -and
        [int]$blockedSecond.drained -eq 1 -and
        [int]$blockedSecond.failed -eq 0 -and
        (-not (Test-Path -LiteralPath $blockedReadyPath)) -and
        (-not (Test-Path -LiteralPath $blockedMarkerPath)) -and
        (Test-Path -LiteralPath $blockedReadyWal.ReplayedPath -PathType Leaf) -and
        ([System.IO.File]::ReadAllText(
            (Join-Path $blockedReadyRoot 'shared/events.jsonl')
        ) -match 'blocked-post-move-verification')
    ) -Detail "first=$blockedFirstText second=$blockedSecondText"

    $blockedMismatchRoot = New-TestBridgeRoot -Name 'accepted-blocked-mismatch'
    $blockedMismatchWal = Write-TestAcceptedWal -Root $blockedMismatchRoot `
        -WalId 'ffffffffffffffffffffffffffffffff' `
        -Text $acceptedEvent.Replace(
            'accepted-target-only',
            'blocked-digest-mismatch'
        ) -Pending
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_PENDING_VERIFY_FAILURE', '1', 'Process'
    )
    try {
        $blockedMismatchFirstText = & $isolatedDrain `
            -BridgeRoot $blockedMismatchRoot -PendingMinAgeSeconds 0 -ReceiptJson
    } finally {
        [Environment]::SetEnvironmentVariable(
            'AGENT_BRIDGE_TEST_PENDING_VERIFY_FAILURE', $null, 'Process'
        )
    }
    $blockedMismatchReady = Join-Path `
        (Join-Path (Join-Path (Join-Path $blockedMismatchRoot 'spool') `
            'accepted-v1') 'ready') $blockedMismatchWal.Leaf
    [System.IO.File]::AppendAllText($blockedMismatchReady, 'tampered')
    $blockedMismatchSecondText = & $isolatedDrain `
        -BridgeRoot $blockedMismatchRoot -PendingMinAgeSeconds 0 -ReceiptJson
    $blockedMismatchSecond = $blockedMismatchSecondText | ConvertFrom-Json
    Add-Check -Name 'persisted pending digest blocks changed ready bytes' -Passed (
        [int]$blockedMismatchSecond.drained -eq 0 -and
        [int]$blockedMismatchSecond.failed -eq 1 -and
        (Test-Path -LiteralPath $blockedMismatchReady -PathType Leaf) -and
        (Get-BridgeTestFileLength -Path `
            (Join-Path $blockedMismatchRoot 'shared/events.jsonl')) -eq 0
    ) -Detail (
        "first=$blockedMismatchFirstText second=$blockedMismatchSecondText"
    )

    # 1. Empty spool -> no-op
    $out = & $isolatedReplay -BridgeRoot $tempRoot -LegacyBulk
    Add-Check -Name 'empty spool is a no-op' -Passed ($out -match 'nothing to replay')

    # 2. A valid spooled event replays into the shared log and archives
    $event = '{"ts_utc":"2026-07-02T10:00:00Z","agent":"fable-5","type":"message","task_id":"spool-replay-smoke","status":"info","message":"recovered"}'
    $spoolFile = Join-Path (Join-Path $tempRoot 'spool') 'failed-append-fable-5-20260702T100000000-1234.jsonl'
    Write-TestWal -Path $spoolFile -Text $event

    $out = & $isolatedReplay -BridgeRoot $tempRoot -LegacyBulk
    Add-Check -Name 'replay reports one replayed' -Passed ($out -match 'replayed=1 deduped=0 failed=0')
    $logged = Get-Content -LiteralPath $eventsPath -Raw -Encoding UTF8
    Add-Check -Name 'event appended to shared log' -Passed ($logged -match 'spool-replay-smoke')
    Add-Check -Name 'spool file archived' -Passed (
        (-not (Test-Path -LiteralPath $spoolFile)) -and
        (Test-Path -LiteralPath (Join-Path (Join-Path (Join-Path $tempRoot 'spool') 'replayed') (Split-Path -Leaf $spoolFile)))
    )

    # 3. Idempotent rerun -> nothing to replay
    $out = & $isolatedReplay -BridgeRoot $tempRoot -LegacyBulk
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
    $archiveCollisionOut = & $isolatedReplay -BridgeRoot $archiveCollisionRoot -LegacyBulk
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
    try { & $isolatedReplay -BridgeRoot $tempRoot -LegacyBulk 3>$null | Out-Null }
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
    $out = & $isolatedReplay -BridgeRoot $tempRoot -LegacyBulk
    $after = (Get-Content -LiteralPath $eventsPath -Encoding UTF8).Count
    Add-Check -Name 'distinct same-semantic records both survive' -Passed (
        ($out -match 'replayed=1 deduped=0') -and ($after -eq ($before + 1)) -and
        (-not (Test-Path -LiteralPath $dupSpool))
    ) -Detail "before=$before after=$after out=$out"

    $exactSpool = Join-Path (Join-Path $tempRoot 'spool') `
        'failed-append-fable-5-exact-duplicate.jsonl'
    Write-TestWal -Path $exactSpool -Text $retryCopy
    $before = @(Get-Content -LiteralPath $eventsPath -Encoding UTF8).Count
    $out = & $isolatedReplay -BridgeRoot $tempRoot -LegacyBulk
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
    $out = & $isolatedReplay -BridgeRoot $tempRoot -LegacyBulk
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
    try { & $isolatedReplay -BridgeRoot $tempRoot -LegacyBulk 3>$null | Out-Null }
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
        $guardMutex = New-Object System.Threading.Mutex($false, $isolatedReplayName)
        $guardAcquired = $guardMutex.WaitOne(0)
        if (-not $guardAcquired) {
            Add-Check -Name 'concurrent replay guard setup' -Passed $false -Detail 'could not acquire replay mutex'
        } else {
            $out = & powershell -NoProfile -ExecutionPolicy Bypass -File $isolatedReplay -BridgeRoot $tempRoot -LegacyBulk
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
    $out = & $isolatedReplay -BridgeRoot $tempRoot -LegacyBulk -DryRun
    Add-Check -Name 'dry run lists but keeps file' -Passed (
        (($out -match 'would archive as exact duplicate') -or ($out -match 'would replay')) -and (Test-Path -LiteralPath $spoolFile)
    )

    # 9. Mutex construction failure is accepted only after a verified ready
    #    WAL publication. The queued receipt drives exact targeted recovery.
    $constructionWriterRoot = New-TestBridgeRoot -Name 'construction-writer'
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_RUNTIME_ROOT', $constructionWriterRoot, 'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_MUTEX_CONSTRUCTION_FAILURE', 'Append', 'Process'
    )
    $writerConstructionError = ''
    $writerConstructionResult = $null
    try {
        $writerConstructionResult = & $isolatedWriter `
            -Agent 'smoke-1' -Type status -Status open `
            -Message 'construction-failure-writer' -PayloadJson '{}' 3>$null
    } catch {
        $writerConstructionError = $_.Exception.Message
    }
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_MUTEX_CONSTRUCTION_FAILURE', $null, 'Process'
    )
    $constructionWriterEvents = Join-Path $constructionWriterRoot 'shared/events.jsonl'
    $constructionDelivery = $writerConstructionResult._bridge_delivery
    $constructionWriterSpools = @(Get-TestAcceptedWalFiles `
        -Root $constructionWriterRoot -State ready)
    $constructionPending = @(Get-TestAcceptedWalFiles `
        -Root $constructionWriterRoot -State pending)
    $constructionSpoolText = if ($constructionWriterSpools.Count -eq 1) {
        [System.IO.File]::ReadAllText($constructionWriterSpools[0].FullName)
    } else { '' }
    $constructionReadyHash = if ($constructionWriterSpools.Count -eq 1) {
        (Get-FileHash -LiteralPath $constructionWriterSpools[0].FullName `
            -Algorithm SHA256).Hash.ToLowerInvariant()
    } else { '' }
    $constructionCanonicalBefore = Get-BridgeTestFileLength `
        -Path $constructionWriterEvents
    $constructionRecovery = if ($null -ne $constructionDelivery) {
        Invoke-TestAcceptedReceiptReplay -ReplayScript $isolatedReplay `
            -Root $constructionWriterRoot -Delivery $constructionDelivery
    } else { '' }
    Add-Check -Name 'writer construction failure returns queued receipt and recovers' -Passed (
        (-not $writerConstructionError) -and
        [string]$constructionDelivery.delivery_status -ceq 'queued' -and
        -not [bool]$constructionDelivery.canonical_durable -and
        -not [bool]$constructionDelivery.outbox_written -and
        -not [bool]$constructionDelivery.last_file_written -and
        $constructionCanonicalBefore -eq 0 -and
        $constructionWriterSpools.Count -eq 1 -and
        $constructionPending.Count -eq 0 -and
        [string]$constructionDelivery.retained_wal_path -ceq
            $constructionWriterSpools[0].FullName -and
        [string]$constructionDelivery.retained_wal_sha256 -ceq
            $constructionReadyHash -and
        $constructionSpoolText -match 'construction-failure-writer' -and
        ($constructionRecovery -match 'replayed=1 deduped=0 failed=0') -and
        ([System.IO.File]::ReadAllText($constructionWriterEvents) -match
            'construction-failure-writer')
    ) -Detail (
        "error=$writerConstructionError ready=$($constructionWriterSpools.Count) " +
        "delivery=$($constructionDelivery.delivery_status) recovery=$constructionRecovery"
    )

    $deferredPublishRoot = New-TestBridgeRoot -Name 'deferred-ready-publication'
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_RUNTIME_ROOT', $deferredPublishRoot, 'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_MUTEX_CONSTRUCTION_FAILURE', 'Append', 'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_ACCEPTED_PUBLICATION_FAILURE', '1', 'Process'
    )
    try {
        $deferredPublishResult = & $isolatedWriter `
            -Agent 'smoke-1' -Type status -Status open `
            -Message 'deferred-ready-publication' -PayloadJson '{}' 3>$null
    } finally {
        [Environment]::SetEnvironmentVariable(
            'AGENT_BRIDGE_TEST_MUTEX_CONSTRUCTION_FAILURE', $null, 'Process'
        )
        [Environment]::SetEnvironmentVariable(
            'AGENT_BRIDGE_TEST_ACCEPTED_PUBLICATION_FAILURE', $null, 'Process'
        )
    }
    $deferredPublishDelivery = $deferredPublishResult._bridge_delivery
    $deferredPendingBefore = @(Get-TestAcceptedWalFiles `
        -Root $deferredPublishRoot -State pending)
    $deferredDrainText = & $isolatedDrain -BridgeRoot $deferredPublishRoot `
        -PendingMinAgeSeconds 0 -ReceiptJson
    $deferredDrain = $deferredDrainText | ConvertFrom-Json
    Add-Check -Name 'ready publication failure remains accepted pending and drains' -Passed (
        [string]$deferredPublishDelivery.delivery_status -ceq 'queued' -and
        -not [bool]$deferredPublishDelivery.canonical_durable -and
        $deferredPendingBefore.Count -eq 1 -and
        [string]$deferredPublishDelivery.retained_wal_path -ceq
            $deferredPendingBefore[0].FullName -and
        [int]$deferredDrain.pending_promoted -eq 1 -and
        [int]$deferredDrain.drained -eq 1 -and
        [int]$deferredDrain.failed -eq 0 -and
        ([System.IO.File]::ReadAllText(
            (Join-Path $deferredPublishRoot 'shared/events.jsonl')
        ) -match 'deferred-ready-publication')
    ) -Detail "delivery=$deferredPublishDelivery drain=$deferredDrainText"

    $collisionPublishRoot = New-TestBridgeRoot -Name 'wrong-ready-collision'
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_RUNTIME_ROOT', $collisionPublishRoot, 'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_MUTEX_CONSTRUCTION_FAILURE', 'Append', 'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_ACCEPTED_PUBLICATION_COLLISION', '1', 'Process'
    )
    try {
        $collisionPublishResult = & $isolatedWriter `
            -Agent 'smoke-1' -Type status -Status open `
            -Message 'wrong-ready-collision' -PayloadJson '{}' 3>$null
    } finally {
        [Environment]::SetEnvironmentVariable(
            'AGENT_BRIDGE_TEST_MUTEX_CONSTRUCTION_FAILURE', $null, 'Process'
        )
        [Environment]::SetEnvironmentVariable(
            'AGENT_BRIDGE_TEST_ACCEPTED_PUBLICATION_COLLISION', $null, 'Process'
        )
    }
    $collisionPublishDelivery = $collisionPublishResult._bridge_delivery
    $collisionPending = @(Get-TestAcceptedWalFiles `
        -Root $collisionPublishRoot -State pending)
    $collisionReady = @(Get-TestAcceptedWalFiles `
        -Root $collisionPublishRoot -State ready)
    $collisionPendingHash = if ($collisionPending.Count -eq 1) {
        (Get-FileHash -LiteralPath $collisionPending[0].FullName `
            -Algorithm SHA256).Hash.ToLowerInvariant()
    } else { '' }
    $collisionReadyHash = if ($collisionReady.Count -eq 1) {
        (Get-FileHash -LiteralPath $collisionReady[0].FullName `
            -Algorithm SHA256).Hash.ToLowerInvariant()
    } else { '' }
    if ($collisionReady.Count -eq 1) {
        Remove-Item -LiteralPath $collisionReady[0].FullName -Force
    }
    $collisionDrainText = & $isolatedDrain -BridgeRoot $collisionPublishRoot `
        -PendingMinAgeSeconds 0 -ReceiptJson
    $collisionDrain = $collisionDrainText | ConvertFrom-Json
    Add-Check -Name 'wrong ready collision never authorizes queued receipt' -Passed (
        [string]$collisionPublishDelivery.delivery_status -ceq 'queued' -and
        $collisionPending.Count -eq 1 -and
        $collisionReady.Count -eq 1 -and
        [string]$collisionPublishDelivery.retained_wal_path -ceq
            $collisionPending[0].FullName -and
        [string]$collisionPublishDelivery.retained_wal_sha256 -ceq
            $collisionPendingHash -and
        $collisionReadyHash -cne $collisionPendingHash -and
        [int]$collisionDrain.pending_promoted -eq 1 -and
        [int]$collisionDrain.drained -eq 1 -and
        [int]$collisionDrain.failed -eq 0 -and
        ([System.IO.File]::ReadAllText(
            (Join-Path $collisionPublishRoot 'shared/events.jsonl')
        ) -match 'wrong-ready-collision')
    ) -Detail (
        "delivery=$collisionPublishDelivery readyHash=$collisionReadyHash " +
        "pendingHash=$collisionPendingHash drain=$collisionDrainText"
    )

    $unacceptedRoot = New-TestBridgeRoot -Name 'unaccepted-pending-exclusion'
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_RUNTIME_ROOT', $unacceptedRoot, 'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_PENDING_WRITEBACK_FAILURE', '1', 'Process'
    )
    $unacceptedError = ''
    try {
        & $isolatedWriter -Agent 'smoke-1' -Type status -Status open `
            -Message 'unaccepted-pending-exclusion' -PayloadJson '{}' | Out-Null
    } catch { $unacceptedError = $_.Exception.Message }
    finally {
        [Environment]::SetEnvironmentVariable(
            'AGENT_BRIDGE_TEST_PENDING_WRITEBACK_FAILURE', $null, 'Process'
        )
    }
    $unacceptedPending = @(Get-TestAcceptedWalFiles `
        -Root $unacceptedRoot -State pending)
    $unacceptedReady = @(Get-TestAcceptedWalFiles `
        -Root $unacceptedRoot -State ready)
    $unacceptedQuarantine = @(
        Get-ChildItem -LiteralPath (Join-Path `
            (Join-Path (Join-Path $unacceptedRoot 'spool') 'accepted-v1') `
            'quarantine') -File -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -cmatch '^unaccepted-bridge-wal-v1-' }
    )
    & $isolatedWriter -Agent 'smoke-1' -Type status -Status open `
        -Message 'unaccepted-pending-exclusion' -PayloadJson '{}' | Out-Null
    $unacceptedDrainText = & $isolatedDrain -BridgeRoot $unacceptedRoot `
        -PendingMinAgeSeconds 0 -ReceiptJson
    $unacceptedLines = @(
        Get-Content -LiteralPath (Join-Path $unacceptedRoot 'shared/events.jsonl') |
            Where-Object { $_ -match 'unaccepted-pending-exclusion' }
    )
    Add-Check -Name 'unverified pending candidate is excluded before retry' -Passed (
        ($unacceptedError -match 'excluded from automatic replay') -and
        $unacceptedPending.Count -eq 0 -and
        $unacceptedReady.Count -eq 0 -and
        $unacceptedQuarantine.Count -eq 1 -and
        $unacceptedLines.Count -eq 1 -and
        (($unacceptedDrainText | ConvertFrom-Json).drained -eq 0)
    ) -Detail (
        "error=$unacceptedError quarantine=$($unacceptedQuarantine.Count) " +
        "drain=$unacceptedDrainText lines=$($unacceptedLines.Count)"
    )

    $unknownAcceptanceRoot = New-TestBridgeRoot `
        -Name 'unknown-acceptance-markerless'
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_RUNTIME_ROOT', $unknownAcceptanceRoot, 'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_PENDING_WRITEBACK_FAILURE', '1', 'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_UNACCEPTED_EXCLUSION_FAILURE', 'All', 'Process'
    )
    $unknownAcceptanceError = ''
    try {
        & $isolatedWriter -Agent 'smoke-1' -Type status -Status open `
            -Message 'unknown-acceptance-markerless' -PayloadJson '{}' |
            Out-Null
    } catch { $unknownAcceptanceError = $_.Exception.Message }
    finally {
        [Environment]::SetEnvironmentVariable(
            'AGENT_BRIDGE_TEST_PENDING_WRITEBACK_FAILURE', $null, 'Process'
        )
        [Environment]::SetEnvironmentVariable(
            'AGENT_BRIDGE_TEST_UNACCEPTED_EXCLUSION_FAILURE', $null, 'Process'
        )
    }
    $unknownAcceptancePending = @(Get-TestAcceptedWalFiles `
        -Root $unknownAcceptanceRoot -State pending)
    $unknownAcceptanceReady = @(Get-TestAcceptedWalFiles `
        -Root $unknownAcceptanceRoot -State ready)
    $unknownAcceptanceDrainText = & $isolatedDrain `
        -BridgeRoot $unknownAcceptanceRoot `
        -PendingMinAgeSeconds 0 -ReceiptJson
    $unknownAcceptanceDrain = $unknownAcceptanceDrainText | ConvertFrom-Json
    Add-Check -Name 'markerless acceptance-unknown WAL never auto-replays' -Passed (
        ($unknownAcceptanceError -match 'acceptance is unknown') -and
        $unknownAcceptancePending.Count -eq 1 -and
        $unknownAcceptanceReady.Count -eq 0 -and
        [int]$unknownAcceptanceDrain.pending_promoted -eq 0 -and
        [int]$unknownAcceptanceDrain.pending_failed -eq 1 -and
        [int]$unknownAcceptanceDrain.drained -eq 0 -and
        [int]$unknownAcceptanceDrain.failed -eq 1 -and
        (Test-Path -LiteralPath `
            $unknownAcceptancePending[0].FullName -PathType Leaf) -and
        (Get-BridgeTestFileLength -Path (Join-Path `
            $unknownAcceptanceRoot 'shared/events.jsonl')) -eq 0
    ) -Detail (
        "error=$unknownAcceptanceError drain=$unknownAcceptanceDrainText"
    )

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
    try { & $isolatedReplay -BridgeRoot $constructionReplayRoot -LegacyBulk | Out-Null }
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
    try { & $isolatedReplay -BridgeRoot $constructionReplayRoot -LegacyBulk 3>$null | Out-Null }
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
    try { & $enumerationReplay -BridgeRoot $enumerationRoot -LegacyBulk | Out-Null }
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
        [pscustomobject]@{ Name = 'missing-core'; Row = '{"type":"message"}' }
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
        try { & $isolatedReplay -BridgeRoot $caseRoot -LegacyBulk | Out-Null }
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
                        -Message 'timeout-writer' -PayloadJson '{}' `
                        -ReceiptJson
                } catch { "ERROR: $($_.Exception.Message)" }
            } -ArgumentList $isolatedWriter, $timeoutWriterRoot
            $replayJob = Start-Job -ScriptBlock {
                param($ScriptPath, $Root)
                Remove-Item Env:AGENT_BRIDGE_TEST_MUTEX_CONSTRUCTION_FAILURE `
                    -ErrorAction SilentlyContinue
                try { & $ScriptPath -BridgeRoot $Root -LegacyBulk 3>$null | Out-String }
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
    $timeoutWriterReceipt = $null
    try { $timeoutWriterReceipt = $timeoutWriterOutput | ConvertFrom-Json }
    catch {}
    $timeoutWriterDelivery = if ($null -ne $timeoutWriterReceipt) {
        $timeoutWriterReceipt._bridge_delivery
    } else { $null }
    $timeoutWriterSpools = @(Get-TestAcceptedWalFiles `
        -Root $timeoutWriterRoot -State ready)
    $timeoutWriterCanonicalBefore = Get-BridgeTestFileLength `
        -Path (Join-Path $timeoutWriterRoot 'shared/events.jsonl')
    $timeoutWriterRecovery = if ($null -ne $timeoutWriterDelivery) {
        Invoke-TestAcceptedReceiptReplay -ReplayScript $isolatedReplay `
            -Root $timeoutWriterRoot -Delivery $timeoutWriterDelivery
    } else { '' }
    Add-Check -Name 'append mutex timeout queues durably and target-recovers' -Passed (
        $holdAcquired -and $timeoutElapsed.TotalSeconds -ge 9.5 -and
        $timeoutWriterCanonicalBefore -eq 0 -and
        $timeoutWriterSpools.Count -eq 1 -and
        [string]$timeoutWriterDelivery.delivery_status -ceq 'queued' -and
        -not [bool]$timeoutWriterDelivery.canonical_durable -and
        -not [bool]$timeoutWriterDelivery.outbox_written -and
        -not [bool]$timeoutWriterDelivery.last_file_written -and
        ([string]$timeoutWriterDelivery.warning_messages -match 'mutex timeout') -and
        ($timeoutWriterRecovery -match 'replayed=1 deduped=0 failed=0') -and
        ([System.IO.File]::ReadAllText(
            (Join-Path $timeoutWriterRoot 'shared/events.jsonl')
        ) -match 'timeout-writer') -and
        (Get-BridgeTestFileLength -Path (Join-Path $timeoutReplayRoot 'shared/events.jsonl')) -eq 0 -and
        (Test-Path -LiteralPath $timeoutReplaySpool) -and
        $timeoutReplayOutput -match 'append mutex unavailable'
    ) -Detail (
        "elapsed=$($timeoutElapsed.TotalSeconds) writer=$timeoutWriterOutput " +
        "writerRecovery=$timeoutWriterRecovery replay=$timeoutReplayOutput"
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
                try { & $ScriptPath -BridgeRoot $Root -LegacyBulk 3>$null | Out-String }
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
    $abandonedWriterResult = $null
    $appendSentinel = New-Object System.Threading.Mutex($false, $isolatedAppendName)
    try {
        $appendAbandoned = Stop-ProcessAfterMutexAcquisition `
            -Name $isolatedAppendName -HelperPath $abandonHelper `
            -ReadyPath $appendReady
        if ($appendAbandoned) {
            try {
                $abandonedWriterResult = & $isolatedWriter `
                    -Agent 'smoke-1' -Type status -Status open `
                    -Message 'abandoned-writer' -PayloadJson '{}' 3>$null
            } catch { $abandonedWriterError = $_.Exception.Message }
        }
    } finally {
        $appendSentinel.Dispose()
    }
    $abandonedWriterEvents = Join-Path $abandonedWriterRoot 'shared/events.jsonl'
    $abandonedWriterDelivery = $abandonedWriterResult._bridge_delivery
    $abandonedWriterSpools = @(Get-TestAcceptedWalFiles `
        -Root $abandonedWriterRoot -State ready)
    $abandonedWriterDirtyCheckpoint = Test-Path -LiteralPath `
        "$abandonedWriterEvents.append-v1-validation.json" -PathType Leaf
    $abandonedWriterRecovered = ''
    if ($null -ne $abandonedWriterDelivery) {
        $abandonedWriterRecovered = Invoke-TestAcceptedReceiptReplay `
            -ReplayScript $isolatedReplay -Root $abandonedWriterRoot `
            -Delivery $abandonedWriterDelivery
    }
    Add-Check -Name 'writer queues on dirty abandoned ownership then recovers' -Passed (
        $appendAbandoned -and
        (-not $abandonedWriterError) -and
        [string]$abandonedWriterDelivery.delivery_status -ceq 'queued' -and
        -not [bool]$abandonedWriterDelivery.canonical_durable -and
        ([string]$abandonedWriterDelivery.warning_messages -match
            'dirty ownership') -and
        (-not $abandonedWriterDirtyCheckpoint) -and
        $abandonedWriterSpools.Count -eq 1 -and
        ($abandonedWriterRecovered -match 'replayed=1 deduped=0 failed=0') -and
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
            & $isolatedReplay -BridgeRoot $abandonedAppendReplayRoot -LegacyBulk
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
        & $isolatedReplay -BridgeRoot $abandonedAppendReplayRoot -LegacyBulk
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
            & $isolatedReplay -BridgeRoot $abandonedReplayRoot -LegacyBulk
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
    $pendingOut = & $isolatedReplay -BridgeRoot $pendingRoot -LegacyBulk
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
    try { & $isolatedReplay -BridgeRoot $partialPendingRoot -LegacyBulk | Out-Null }
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
    $exactCollisionOut = & $isolatedReplay -BridgeRoot $exactCollisionRoot -LegacyBulk
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
    try { & $isolatedReplay -BridgeRoot $differentCollisionRoot -LegacyBulk | Out-Null }
    catch { $differentCollisionError = $_.Exception.Message }
    Add-Check -Name 'different pending final collision is a hard failure' -Passed (
        ($differentCollisionError -match 'collides with different final') -and
        (Test-Path -LiteralPath $differentCollisionFinal) -and
        (Test-Path -LiteralPath $differentCollisionPending) -and
        (Get-BridgeTestFileLength -Path `
            (Join-Path $differentCollisionRoot 'shared/events.jsonl')) -eq 0
    ) -Detail "error=$differentCollisionError"

    # 15. A durable accepted-v1 pending WAL exists before canonical append. The
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
    $beforeAppendInAcceptedPending = $false
    $beforeAppendBytesValid = $false
    $beforeAppendOutput = ''
    $beforeAppendDrainText = ''
    $beforeAppendDrain = $null
    $beforeAppendSharedRenameBlocked = $false
    $beforeAppendSharedRenameError = ''
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
            $beforeAppendShared = Join-Path $beforeAppendRoot 'shared'
            $beforeAppendSharedSwap = Join-Path $beforeAppendRoot 'shared.swap'
            try {
                [System.IO.Directory]::Move(
                    $beforeAppendShared,
                    $beforeAppendSharedSwap
                )
            } catch {
                $beforeAppendSharedRenameBlocked = $true
                $beforeAppendSharedRenameError = $_.Exception.Message
            } finally {
                if (
                    (Test-Path -LiteralPath $beforeAppendSharedSwap -PathType Container) -and
                    -not (Test-Path -LiteralPath $beforeAppendShared)
                ) {
                    [System.IO.Directory]::Move(
                        $beforeAppendSharedSwap,
                        $beforeAppendShared
                    )
                }
            }
            $beforeAppendPending = [System.IO.File]::ReadAllText($beforeAppendReady)
            if (Test-Path -LiteralPath $beforeAppendPending -PathType Leaf) {
                $beforeAppendInAcceptedPending = [string]::Equals(
                    [System.IO.Path]::GetDirectoryName($beforeAppendPending),
                    [System.IO.Path]::GetFullPath((Join-Path `
                        (Join-Path (Join-Path $beforeAppendRoot 'spool') `
                            'accepted-v1') 'pending')),
                    [StringComparison]::OrdinalIgnoreCase
                )
                [byte[]]$beforeAppendBytes = [System.IO.File]::ReadAllBytes(
                    $beforeAppendPending
                )
                $beforeAppendBytesValid = (
                    $beforeAppendBytes.Length -gt 0 -and
                    $beforeAppendBytes[$beforeAppendBytes.Length - 1] -eq 10
                )
                $beforeAppendDrainText = & $isolatedDrain `
                    -BridgeRoot $beforeAppendRoot `
                    -PendingMinAgeSeconds 0 -DryRun -ReceiptJson
                $beforeAppendDrain = $beforeAppendDrainText | ConvertFrom-Json
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
    $beforeAppendPendingAfter = @(Get-TestAcceptedWalFiles `
        -Root $beforeAppendRoot -State pending)
    $beforeAppendReadyAfter = @(Get-TestAcceptedWalFiles `
        -Root $beforeAppendRoot -State ready)
    Add-Check -Name 'writer has durable accepted WAL before canonical append' -Passed (
        $beforeAppendReached -and $beforeAppendInAcceptedPending -and
        $beforeAppendBytesValid -and $beforeAppendSharedRenameBlocked -and
        $null -ne $beforeAppendDrain -and
        [int]$beforeAppendDrain.pending_skipped -eq 1 -and
        [string]$beforeAppendDrain.results[0].status -ceq 'pending_append_busy' -and
        ($beforeAppendOutput -match 'success') -and
        (Get-BridgeTestFileLength -Path `
            (Join-Path $beforeAppendRoot 'shared/events.jsonl')) -gt 0 -and
        $beforeAppendPendingAfter.Count -eq 0 -and
        $beforeAppendReadyAfter.Count -eq 0
    ) -Detail (
        "reached=$beforeAppendReached acceptedPending=$beforeAppendInAcceptedPending " +
        "bytes=$beforeAppendBytesValid renameBlocked=$beforeAppendSharedRenameBlocked " +
        "renameError=$beforeAppendSharedRenameError out=$beforeAppendOutput " +
        "drain=$beforeAppendDrainText pending=$($beforeAppendPendingAfter.Count) " +
        "ready=$($beforeAppendReadyAfter.Count)"
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
    $writerRollbackResult = $null
    try {
        $writerRollbackResult = & $isolatedWriter `
            -Agent 'smoke-1' -Type status -Status open `
            -Message 'writer-partial-rollback' -PayloadJson '{}' 3>$null
    } catch { $writerRollbackError = $_.Exception.Message }
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_APPEND_FAILURE_AFTER_BYTES', $null, 'Process'
    )
    $writerRollbackAfter = [Convert]::ToBase64String(
        [System.IO.File]::ReadAllBytes($writerRollbackEvents)
    )
    $writerRollbackDelivery = $writerRollbackResult._bridge_delivery
    $writerRollbackSpools = @(Get-TestAcceptedWalFiles `
        -Root $writerRollbackRoot -State ready)
    $writerRollbackPending = @(Get-TestAcceptedWalFiles `
        -Root $writerRollbackRoot -State pending)
    $writerRollbackRecovery = if ($null -ne $writerRollbackDelivery) {
        Invoke-TestAcceptedReceiptReplay -ReplayScript $isolatedReplay `
            -Root $writerRollbackRoot -Delivery $writerRollbackDelivery
    } else { '' }
    Add-Check -Name 'writer partial append rolls back, queues, and recovers' -Passed (
        (-not $writerRollbackError) -and
        [string]$writerRollbackDelivery.delivery_status -ceq 'queued' -and
        -not [bool]$writerRollbackDelivery.canonical_durable -and
        ([string]$writerRollbackDelivery.warning_messages -match 'rolled back') -and
        $writerRollbackBefore -ceq $writerRollbackAfter -and
        $writerRollbackSpools.Count -eq 1 -and
        $writerRollbackPending.Count -eq 0 -and
        ($writerRollbackRecovery -match 'replayed=1 deduped=0 failed=0')
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
        -BridgeRoot $replayRollbackRoot -LegacyBulk 3>$null
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_APPEND_FAILURE_AFTER_BYTES', $null, 'Process'
    )
    $replayRollbackAfter = [Convert]::ToBase64String(
        [System.IO.File]::ReadAllBytes($replayRollbackEvents)
    )
    $replayRollbackCleanOut = & $isolatedReplay -BridgeRoot $replayRollbackRoot -LegacyBulk
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
    $tornOut = & $isolatedReplay -BridgeRoot $tornRoot -LegacyBulk 3>$null
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
    try { & $isolatedReplay -BridgeRoot $unboundRoot -LegacyBulk 3>$null | Out-Null }
    catch { $unboundError = $_.Exception.Message }
    $unboundQuarantine = @(
        Get-ChildItem -LiteralPath `
            (Join-Path (Join-Path $unboundRoot 'spool') 'quarantine') `
            -File -ErrorAction SilentlyContinue
    )
    Add-Check -Name 'unbound torn tail fails closed without quarantine' -Passed (
        ($unboundError -match 'unbound unterminated tail') -and
        $unboundBefore -ceq [Convert]::ToBase64String(
            [System.IO.File]::ReadAllBytes($unboundEvents)
        ) -and
        (Test-Path -LiteralPath $unboundSpool) -and
        $unboundQuarantine.Count -eq 0
    ) -Detail "error=$unboundError quarantine=$($unboundQuarantine.Count)"

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
    try { & $isolatedReplay -BridgeRoot $interiorRoot -LegacyBulk 3>$null | Out-Null }
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
    try { & $isolatedReplay -BridgeRoot $blankTornRoot -LegacyBulk 3>$null | Out-Null }
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
    try { & $isolatedReplay -BridgeRoot $plainBlankRoot -LegacyBulk 3>$null | Out-Null }
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
    try { & $isolatedReplay -BridgeRoot $invalidCanonicalRoot -LegacyBulk | Out-Null }
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
    try { & $isolatedReplay -BridgeRoot $invalidFinalRoot -LegacyBulk | Out-Null }
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
    try { & $isolatedReplay -BridgeRoot $invalidPendingRoot -LegacyBulk | Out-Null }
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
    $invalidWriterResult = $null
    try {
        $invalidWriterResult = & $isolatedWriter `
            -Agent 'smoke-1' -Type status -Status open `
            -Message 'invalid-utf8-writer' -PayloadJson '{}' 3>$null
    } catch { $invalidWriterError = $_.Exception.Message }
    $invalidWriterDelivery = $invalidWriterResult._bridge_delivery
    $invalidWriterSpools = @(Get-TestAcceptedWalFiles `
        -Root $invalidWriterRoot -State ready)
    $invalidWriterPassed = (
        (-not $invalidWriterError) -and
        [string]$invalidWriterDelivery.delivery_status -ceq 'queued' -and
        -not [bool]$invalidWriterDelivery.canonical_durable -and
        ([string]$invalidWriterDelivery.warning_messages -match
            'not strict UTF-8') -and
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

    # 19. The age-gated pending sweeper skips a live writer lease. Once the
    #     lease is gone, it publishes write-through to ready and target-drains.
    $activeLeaseRoot = New-TestBridgeRoot -Name 'active-pending-lease'
    $activePendingEvent = '{"ts_utc":"2026-08-20T12:00:00Z","agent":"smoke-1","type":"message","task_id":"active-pending-once","status":"info","message":"active-pending-once"}'
    $activePendingWal = Write-TestAcceptedWal -Root $activeLeaseRoot `
        -WalId 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' `
        -Text $activePendingEvent -Pending
    $activePendingLease = New-Object System.IO.FileStream(
        $activePendingWal.Path,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::None
    )
    try {
        $activeDrainText = & $isolatedDrain -BridgeRoot $activeLeaseRoot `
            -PendingMinAgeSeconds 0 -ReceiptJson
    } finally {
        $activePendingLease.Dispose()
    }
    $activeDrain = $activeDrainText | ConvertFrom-Json
    $orphanDrainText = & $isolatedDrain -BridgeRoot $activeLeaseRoot `
        -PendingMinAgeSeconds 0 -ReceiptJson
    $orphanDrain = $orphanDrainText | ConvertFrom-Json
    $activeLeaseEvents = Join-Path $activeLeaseRoot 'shared/events.jsonl'
    $activeLeaseLines = @([System.IO.File]::ReadAllLines($activeLeaseEvents))
    Add-Check -Name 'pending sweeper skips live lease then recovers orphan once' -Passed (
        [int]$activeDrain.pending_seen -eq 1 -and
        [int]$activeDrain.pending_skipped -eq 1 -and
        [int]$activeDrain.pending_promoted -eq 0 -and
        [string]$activeDrain.results[0].status -ceq 'pending_active' -and
        [int]$orphanDrain.pending_seen -eq 1 -and
        [int]$orphanDrain.pending_promoted -eq 1 -and
        [int]$orphanDrain.drained -eq 1 -and
        [int]$orphanDrain.failed -eq 0 -and
        $activeLeaseLines.Count -eq 1 -and
        $activeLeaseLines[0] -ceq $activePendingEvent -and
        (-not (Test-Path -LiteralPath $activePendingWal.Path)) -and
        (Test-Path -LiteralPath $activePendingWal.ReplayedPath -PathType Leaf)
    ) -Detail "active=$activeDrainText orphan=$orphanDrainText"

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
    $updateFailureResult = $null
    try {
        $updateFailureResult = & $isolatedWriter `
            -Agent 'smoke-1' -Type message -Status info `
            -TaskId 'checkpoint-update-failure' `
            -Message 'checkpoint-update-failure-once' -PayloadJson '{}' `
            3>$null
    } catch { $updateFailureError = $_.Exception.Message }
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_CHECKPOINT_UPDATE_FAILURE', $null, 'Process'
    )
    $updateFailureLines = @([System.IO.File]::ReadAllLines($updateFailureEvents))
    $updateFailureDelivery = $updateFailureResult._bridge_delivery
    $updateFailureSpools = @(Get-TestAcceptedWalFiles `
        -Root $updateFailureRoot -State ready)
    $updateFailureReplay = if ($null -ne $updateFailureDelivery) {
        Invoke-TestAcceptedReceiptReplay -ReplayScript $isolatedReplay `
            -Root $updateFailureRoot -Delivery $updateFailureDelivery
    } else { '' }
    Add-Check -Name 'checkpoint update failure returns success retains WAL and exact-dedups' -Passed (
        (-not $updateFailureError) -and
        [string]$updateFailureDelivery.delivery_status -ceq 'canonical' -and
        [bool]$updateFailureDelivery.canonical_durable -and
        -not [bool]$updateFailureDelivery.checkpoint_advanced -and
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
    $cleanupFailureResult = $null
    try {
        $cleanupFailureResult = & $isolatedWriter `
            -Agent 'smoke-1' -Type message -Status info `
            -TaskId 'wal-cleanup-failure' -Message 'wal-cleanup-failure-once' `
            -PayloadJson '{}' 3>$null
    } catch { $cleanupFailureError = $_.Exception.Message }
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_WAL_CLEANUP_FAILURE', $null, 'Process'
    )
    $cleanupFailureLines = @([System.IO.File]::ReadAllLines($cleanupFailureEvents))
    $cleanupFailureDelivery = $cleanupFailureResult._bridge_delivery
    $cleanupFailureSpools = @(Get-TestAcceptedWalFiles `
        -Root $cleanupFailureRoot -State ready)
    $cleanupFailureReplay = if ($null -ne $cleanupFailureDelivery) {
        Invoke-TestAcceptedReceiptReplay -ReplayScript $isolatedReplay `
            -Root $cleanupFailureRoot -Delivery $cleanupFailureDelivery
    } else { '' }
    Add-Check -Name 'post-flush WAL cleanup failure returns success and exact-dedups' -Passed (
        (-not $cleanupFailureError) -and
        [string]$cleanupFailureDelivery.delivery_status -ceq 'canonical' -and
        [bool]$cleanupFailureDelivery.canonical_durable -and
        [bool]$cleanupFailureDelivery.checkpoint_advanced -and
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
    $auxiliaryReplayOutput = & $isolatedReplay -BridgeRoot $auxiliaryFailureRoot -LegacyBulk
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

    # Unsupported-platform refusal must precede accepted WAL creation. A clean
    # writer must then lease the canonical parent before the test hook and leaf
    # open. This Windows-hosted static assertion covers the otherwise
    # unreachable unsupported-platform path and guards the call ordering.
    $writerGateSource = [System.IO.File]::ReadAllText($writerScript)
    $writerAddStart = $writerGateSource.IndexOf(
        'function Add-CanonicalLineWithWal'
    )
    $writerAddNativeGate = $writerGateSource.IndexOf(
        '    Initialize-BridgeAppendV1Native',
        $writerAddStart
    )
    $writerWalOpen = $writerGateSource.IndexOf(
        '    $wal = Open-PendingCanonicalWalLease -Bytes $lineBytes',
        $writerAddStart
    )
    $writerCanonicalLease = $writerGateSource.IndexOf(
        '            $canonicalDirectoryLeases = @(Open-BridgePlainDirectoryChain',
        $writerAddStart
    )
    $writerBeforeAppendHook = $writerGateSource.IndexOf(
        '            Invoke-BridgeBeforeAppendTestHook',
        $writerAddStart
    )
    $writerCanonicalInvoke = $writerGateSource.IndexOf(
        '            $appendResult = Invoke-BridgeCanonicalTransactionalAppend',
        $writerAddStart
    )
    $writerCanonicalStart = $writerGateSource.IndexOf(
        'function Invoke-BridgeCanonicalTransactionalAppend'
    )
    $writerNativeGate = $writerGateSource.IndexOf(
        '    Initialize-BridgeAppendV1Native',
        $writerCanonicalStart
    )
    $writerOpenCreate = $writerGateSource.IndexOf(
        '$stream = Open-BridgePlainSingleLinkMutationStream',
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
    Add-Check -Name 'native gate and parent lease precede canonical file creation' -Passed (
        $writerAddStart -ge 0 -and
        $writerAddNativeGate -gt $writerAddStart -and
        $writerAddNativeGate -lt $writerWalOpen -and
        $writerWalOpen -lt $writerCanonicalLease -and
        $writerCanonicalLease -lt $writerBeforeAppendHook -and
        $writerBeforeAppendHook -lt $writerCanonicalInvoke -and
        $writerCanonicalStart -ge 0 -and
        $writerNativeGate -gt $writerCanonicalStart -and
        $writerNativeGate -lt $writerOpenCreate -and
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
        "writerAdd=$writerAddStart/$writerAddNativeGate/$writerWalOpen/" +
        "$writerCanonicalLease/$writerBeforeAppendHook/$writerCanonicalInvoke " +
        "writerLeaf=$writerCanonicalStart/$writerNativeGate/$writerOpenCreate " +
        "replay=$replayAppendStart/" +
        "$replayNativeGate/$replayParentCreate/$replayOpenCreate"
    )

    # 22. Kill a writer after canonical Flush(true) but before checkpoint
    #     advance. The accepted pending orphan is swept and exact-deduped.
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
    $crashPending = @(Get-TestAcceptedWalFiles `
        -Root $crashRoot -State pending)
    $crashLinesBeforeReplay = if (Test-Path -LiteralPath $crashEvents) {
        @([System.IO.File]::ReadAllLines($crashEvents))
    } else { @() }
    $crashFirstReplay = & $isolatedDrain -BridgeRoot $crashRoot `
        -PendingMinAgeSeconds 0 -ReceiptJson
    $crashSecondReplay = & $isolatedDrain -BridgeRoot $crashRoot `
        -PendingMinAgeSeconds 0 -ReceiptJson
    $crashFirstReceipt = $crashFirstReplay | ConvertFrom-Json
    $crashSecondReceipt = $crashSecondReplay | ConvertFrom-Json
    $crashLinesAfterReplay = @([System.IO.File]::ReadAllLines($crashEvents))
    Add-Check -Name 'crash after canonical flush sweeps orphan and exact-dedups' -Passed (
        $crashReached -and
        $crashPending.Count -eq 1 -and
        @($crashLinesBeforeReplay | Where-Object {
            $_ -match 'crash-before-checkpoint-once'
        }).Count -eq 1 -and
        [int]$crashFirstReceipt.pending_promoted -eq 1 -and
        (($crashFirstReplay + $crashSecondReplay) -match
            'replayed=0 deduped=1 failed=0') -and
        ([int]$crashFirstReceipt.drained -eq 1 -or
            [int]$crashSecondReceipt.drained -eq 1) -and
        @(Get-TestAcceptedWalFiles -Root $crashRoot -State pending).Count -eq 0 -and
        @(Get-TestAcceptedWalFiles -Root $crashRoot -State ready).Count -eq 0 -and
        @(Get-TestAcceptedWalFiles -Root $crashRoot -State replayed).Count -eq 1 -and
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
    $equalReplayOutput = & $isolatedReplay -BridgeRoot $equalReplayRoot -LegacyBulk 3>$null
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
    $equalCheckpointMissResult = $null
    try {
        $equalCheckpointMissResult = & $isolatedWriter `
            -Agent 'smoke-1' -Type message -Status info `
            -TaskId 'equal-checkpoint-miss' -Message 'equal-checkpoint-miss' `
            -PayloadJson '{}' 3>$null
    } catch { $equalCheckpointMissError = $_.Exception.Message }
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_FAIL_ON_FULL_VALIDATION', $null, 'Process'
    )
    $equalCheckpointMissDelivery = $equalCheckpointMissResult._bridge_delivery
    $equalCheckpointMissRecovery = if (
        $null -ne $equalCheckpointMissDelivery
    ) {
        Invoke-TestAcceptedReceiptReplay -ReplayScript $isolatedReplay `
            -Root $equalReplayRoot -Delivery $equalCheckpointMissDelivery
    } else { '' }
    Add-Check -Name 'equal-byte truncate replay durably invalidates checkpoint' -Passed (
        ($equalReplayOutput -match 'replayed=1 deduped=0 failed=0') -and
        [Convert]::ToBase64String($equalOriginalBytes) -ceq
            [Convert]::ToBase64String($equalFinalBytes) -and
        [string]$equalCheckpointObject.schema -ceq `
            'waggledance.bridge.append-v1-validation-invalidated' -and
        (-not $equalCheckpointMissError) -and
        [string]$equalCheckpointMissDelivery.delivery_status -ceq 'queued' -and
        -not [bool]$equalCheckpointMissDelivery.canonical_durable -and
        -not [bool]$equalCheckpointMissDelivery.outbox_written -and
        -not [bool]$equalCheckpointMissDelivery.last_file_written -and
        ([string]$equalCheckpointMissDelivery.warning_messages -match
            'full canonical validation') -and
        ($equalCheckpointMissRecovery -match 'replayed=1 deduped=0 failed=0')
    ) -Detail (
        "replay=$equalReplayOutput schema=$($equalCheckpointObject.schema) " +
        "miss=$equalCheckpointMissError recovery=$equalCheckpointMissRecovery"
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
        -BridgeRoot $invalidateFailureRoot -LegacyBulk 3>$null
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

    # 24. The expensive canonical JSONL scan runs without AppendV1 ownership
    #     while its open snapshot shares writes. A live writer completes without
    #     spooling, then an exact row appended during the scan is reconciled from
    #     the delta and prevents duplicate replay.
    $scanRoot = New-TestBridgeRoot -Name 'writer-during-canonical-scan'
    $scanEvents = Join-Path $scanRoot 'shared/events.jsonl'
    $scanBaseline = '{"ts_utc":"2026-07-20T11:00:00Z","agent":"smoke-1","type":"message","task_id":"scan-baseline","status":"info","message":"scan-baseline"}'
    $scanExact = '{"ts_utc":"2026-07-20T11:00:01Z","agent":"smoke-1","type":"message","task_id":"scan-exact-delta","status":"info","message":"scan-exact-delta"}'
    Write-TestWal -Path $scanEvents -Text $scanBaseline
    $scanSpool = Join-Path (Join-Path $scanRoot 'spool') `
        'failed-append-smoke-1-scan-exact-delta.jsonl'
    Write-TestWal -Path $scanSpool -Text $scanExact
    $scanReady = Join-Path $tempRoot 'writer-during-canonical-scan.ready'
    $scanRelease = "$scanReady.release"
    $scanReplayJob = Start-Job -ScriptBlock {
        param($ScriptPath, $Root, $ReadyPath)
        $env:AGENT_BRIDGE_TEST_CANONICAL_SCAN_READY = $ReadyPath
        Remove-Item Env:AGENT_BRIDGE_TEST_MUTEX_CONSTRUCTION_FAILURE `
            -ErrorAction SilentlyContinue
        try { & $ScriptPath -BridgeRoot $Root -LegacyBulk 3>$null | Out-String }
        catch { "ERROR: $($_.Exception.Message)" }
    } -ArgumentList $isolatedReplay, $scanRoot, $scanReady
    $scanReached = $false
    $scanWriterError = ''
    $scanWriterElapsed = [TimeSpan]::Zero
    $scanDirectAcquired = $false
    $scanIdentityReplacementBlocked = $false
    $scanReplayOutput = ''
    $scanMutex = New-Object System.Threading.Mutex($false, $isolatedAppendName)
    try {
        for ($attempt = 0; $attempt -lt 400; $attempt++) {
            if (Test-Path -LiteralPath $scanReady -PathType Leaf) {
                $scanReached = $true
                break
            }
            if ($scanReplayJob.State -in @('Completed', 'Failed', 'Stopped')) { break }
            Start-Sleep -Milliseconds 25
        }
        if ($scanReached) {
            try {
                [System.IO.File]::Move(
                    $scanEvents,
                    "$scanEvents.identity-replacement"
                )
            } catch {
                $scanIdentityReplacementBlocked = $true
            }
            [Environment]::SetEnvironmentVariable(
                'AGENT_BRIDGE_RUNTIME_ROOT', $scanRoot, 'Process'
            )
            $scanClock = [Diagnostics.Stopwatch]::StartNew()
            try {
                & $isolatedWriter -Agent 'smoke-1' -Type message -Status info `
                    -TaskId 'scan-live-writer' -Message 'scan-live-writer-once' `
                    -PayloadJson '{}' | Out-Null
            } catch { $scanWriterError = $_.Exception.Message }
            $scanClock.Stop()
            $scanWriterElapsed = $scanClock.Elapsed

            $scanDirectAcquired = $scanMutex.WaitOne(2000)
            if ($scanDirectAcquired) {
                [System.IO.File]::AppendAllText(
                    $scanEvents,
                    ($scanExact + [char]10),
                    $utf8
                )
                $scanMutex.ReleaseMutex()
                $scanDirectAcquired = $false
            }
        }
        [System.IO.File]::WriteAllText($scanRelease, 'release')
        $scanReplayJob | Wait-Job | Out-Null
        $scanReplayOutput = @(Receive-Job -Job $scanReplayJob) -join ' '
    } finally {
        if ($scanDirectAcquired) {
            try { $scanMutex.ReleaseMutex() } catch {}
        }
        $scanMutex.Dispose()
        if (-not (Test-Path -LiteralPath $scanRelease)) {
            [System.IO.File]::WriteAllText($scanRelease, 'release')
        }
        Remove-Job -Job $scanReplayJob -Force -ErrorAction SilentlyContinue
    }
    $scanLines = @([System.IO.File]::ReadAllLines($scanEvents))
    $scanRemainingSpools = @(
        Get-ChildItem -LiteralPath (Join-Path $scanRoot 'spool') `
            -Filter 'failed-append-*.jsonl' -File -Force `
            -ErrorAction SilentlyContinue
    )
    $scanArchived = Join-Path `
        (Join-Path (Join-Path $scanRoot 'spool') 'replayed') `
        (Split-Path -Leaf $scanSpool)
    Add-Check -Name 'canonical scan permits writers and reconciles exact delta' -Passed (
        $scanReached -and
        $scanIdentityReplacementBlocked -and
        [string]::IsNullOrEmpty($scanWriterError) -and
        ($scanReplayOutput -match 'replayed=0 deduped=1 failed=0') -and
        $scanLines.Count -eq 3 -and
        @($scanLines | Where-Object { $_ -match 'scan-live-writer-once' }).Count -eq 1 -and
        @($scanLines | Where-Object { $_ -match 'scan-exact-delta' }).Count -eq 1 -and
        $scanRemainingSpools.Count -eq 0 -and
        (Test-Path -LiteralPath $scanArchived -PathType Leaf)
    ) -Detail (
        "ready=$scanReached identityBlocked=$scanIdentityReplacementBlocked " +
        "writerSeconds=$($scanWriterElapsed.TotalSeconds) " +
        "writerError=$scanWriterError lines=$($scanLines.Count) " +
        "spools=$($scanRemainingSpools.Count) replay=$scanReplayOutput"
    )
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
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_CANONICAL_SCAN_READY',
        $previousCanonicalScanReady,
        'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_PENDING_VERIFY_FAILURE',
        $previousPendingVerifyFailure,
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
