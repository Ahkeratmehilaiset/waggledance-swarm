#requires -Version 5.1
<#
.SYNOPSIS
    Replay spooled bridge events into the shared log (durability completion).

.DESCRIPTION
    Write-AgentEvent.ps1 spools an event to <bridgeRoot>/spool/
    failed-append-<agent>-<utc>-<pid>.jsonl when the shared-log append
    exhausts its retry budget (bridge audit item E, PR #1479). This script
    closes the loop: it re-appends every spooled event line to
    shared/events.jsonl using the same named mutex the writer uses, and
    archives the spool file to spool/replayed/ on success. Idempotent and
    safe to run on a schedule (no spool files -> exit 0, no writes).

    Order note: replayed events append at the CURRENT tail, so their ts_utc
    is older than surrounding events. Readers in this repo select by
    task_id/latest-signal, not by file position alone; the original
    timestamp inside the event is preserved.

.PARAMETER BridgeRoot
    Bridge runtime root. Defaults to AGENT_BRIDGE_RUNTIME_ROOT or the
    parent of this script's directory.

.PARAMETER DryRun
    List what would be replayed without appending or archiving.
#>
[CmdletBinding()]
param(
    [string] $BridgeRoot = '',
    [switch] $DryRun
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if (-not $BridgeRoot) {
    $BridgeRoot = if ($env:AGENT_BRIDGE_RUNTIME_ROOT) {
        [string]$env:AGENT_BRIDGE_RUNTIME_ROOT
    } else {
        Split-Path -Parent $PSScriptRoot
    }
}

$spoolDir   = Join-Path $BridgeRoot 'spool'
$eventsPath = Join-Path (Join-Path $BridgeRoot 'shared') 'events.jsonl'
$archiveDir = Join-Path $spoolDir 'replayed'
$knownEventTypes = @(
    'status', 'intent', 'claim', 'release', 'message', 'finding',
    'decision', 'test', 'blocked', 'handoff', 'done', 'heartbeat',
    'wake_request', 'liveness'
)

if (-not (Test-Path -LiteralPath $spoolDir -PathType Container)) {
    Write-Output 'no spool directory; nothing to replay'
    return
}

$spoolFiles = @(
    Get-ChildItem -Path $spoolDir -Filter 'failed-append-*.jsonl' -File -ErrorAction SilentlyContinue |
        Sort-Object Name
)
if ($spoolFiles.Count -eq 0) {
    Write-Output 'spool empty; nothing to replay'
    return
}

$replayMutex = $null
$replayAcquired = $false
try {
    $replayMutex = New-Object System.Threading.Mutex($false, 'Global\WaggleDanceBridgeSpoolReplayV1')
    try { $replayAcquired = $replayMutex.WaitOne(0) }
    catch [System.Threading.AbandonedMutexException] { $replayAcquired = $true }
} catch {
    throw "could not acquire bridge spool replay mutex: $($_.Exception.Message)"
}
if (-not $replayAcquired) {
    if ($null -ne $replayMutex) { $replayMutex.Dispose() }
    Write-Output 'spool replay already running; exiting without changes'
    return
}

function Get-BridgeLogSnapshotLength {
    param([Parameter(Mandatory)] [string] $Path)
    $mutex = $null
    $acquired = $false
    try {
        $mutex = New-Object System.Threading.Mutex($false, 'Global\WaggleDanceBridgeAppendV1')
        try { $acquired = $mutex.WaitOne(10000) }
        catch [System.Threading.AbandonedMutexException] { $acquired = $true }
        if (-not $acquired) {
            throw 'could not acquire bridge append mutex for dedup snapshot'
        }
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return [int64]0 }
        return [int64](Get-Item -LiteralPath $Path).Length
    } finally {
        if ($null -ne $mutex) {
            if ($acquired) { try { $mutex.ReleaseMutex() } catch {} }
            $mutex.Dispose()
        }
    }
}

function Move-SpoolToArchive {
    param(
        [Parameter(Mandatory)] [System.IO.FileInfo] $File,
        [Parameter(Mandatory)] [string] $ArchiveDir
    )
    if (-not (Test-Path -LiteralPath $File.FullName -PathType Leaf)) {
        Write-Warning "spool file already moved before archive (skipped): $($File.Name)"
        return $false
    }
    if (-not (Test-Path -LiteralPath $ArchiveDir)) {
        [void](New-Item -ItemType Directory -Path $ArchiveDir -Force)
    }
    try {
        Move-Item -LiteralPath $File.FullName -Destination (Join-Path $ArchiveDir $File.Name) -Force
        return $true
    } catch {
        if (-not (Test-Path -LiteralPath $File.FullName -PathType Leaf)) {
            Write-Warning "spool file already moved before archive (skipped): $($File.Name)"
            return $false
        }
        throw
    }
}

function Get-BridgeEventDedupKey {
    param([Parameter(Mandatory)] $EventObject)
    # Semantic duplicate key (rco-2 #1483 finding 1): a caller that retried
    # after spooling produced a LIVE copy with a NEW ts_utc/pid, so identity
    # fields alone or byte-equality would miss it. Same agent+task+type+
    # status+message = the same signal; replaying it would double count-based
    # logic and could resurrect an old signal as latest-by-position.
    # Total under StrictMode: events in the live log may lack fields.
    # Select-Object projects missing properties as $null without throwing.
    $proj = $EventObject | Select-Object agent, task_id, type, status, message
    return (@(
        [string]$proj.agent,
        [string]$proj.task_id,
        [string]$proj.type,
        [string]$proj.status,
        [string]$proj.message
    ) -join "`u{1}")
}

function Add-UniqueLinesWithMutex {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [object[]] $Pairs,
        [Parameter(Mandatory)] $ExistingKeys,
        [Parameter(Mandatory)] [int64] $KnownLength,
        [switch] $DryRun
    )

    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent)) {
        [void](New-Item -ItemType Directory -Path $parent -Force)
    }
    $encoding = New-Object System.Text.UTF8Encoding($false)
    $mutex = $null
    $acquired = $false
    try {
        $mutex = New-Object System.Threading.Mutex($false, 'Global\WaggleDanceBridgeAppendV1')
        try { $acquired = $mutex.WaitOne(10000) }
        catch [System.Threading.AbandonedMutexException] { $acquired = $true }
        if (-not $acquired) {
            return [pscustomobject]@{
                Succeeded = $false; WouldAppend = $false; DuplicateCount = 0
                NewLength = $KnownLength; Error = 'bridge append mutex timeout'
            }
        }

        $currentLength = if (Test-Path -LiteralPath $Path -PathType Leaf) {
            [int64](Get-Item -LiteralPath $Path).Length
        } else { [int64]0 }
        if ($KnownLength -lt 0 -or $KnownLength -gt $currentLength) {
            # A rotation/truncation invalidates the cursor. Fail closed by
            # rebuilding keys from the replacement log while writers wait.
            $ExistingKeys.Clear()
            $KnownLength = 0
        }

        if ($currentLength -gt $KnownLength) {
            $stream = New-Object System.IO.FileStream(
                $Path,
                [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::Read,
                [System.IO.FileShare]::ReadWrite
            )
            [void]$stream.Seek($KnownLength, [System.IO.SeekOrigin]::Begin)
            $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8, ($KnownLength -eq 0))
            try {
                while ($null -ne ($line = $reader.ReadLine())) {
                    if (-not $line) { continue }
                    try {
                        $obj = $line | ConvertFrom-Json -ErrorAction Stop
                        if ($null -ne $obj -and $null -ne $obj.PSObject.Properties['type']) {
                            [void]$ExistingKeys.Add((Get-BridgeEventDedupKey -EventObject $obj))
                        }
                    } catch {}
                }
            } finally {
                $reader.Dispose()
                $stream.Dispose()
            }
        }

        $currentLength = if (Test-Path -LiteralPath $Path -PathType Leaf) {
            [int64](Get-Item -LiteralPath $Path).Length
        } else { [int64]0 }
        $linesToAppend = @()
        $duplicateCount = 0
        $pendingKeys = New-Object 'System.Collections.Generic.HashSet[string]'
        foreach ($pair in $Pairs) {
            $key = Get-BridgeEventDedupKey -EventObject $pair.Obj
            if ($ExistingKeys.Contains($key) -or -not $pendingKeys.Add($key)) {
                $duplicateCount++
            } else {
                $linesToAppend += $pair
            }
        }

        if ($linesToAppend.Count -eq 0 -or $DryRun) {
            return [pscustomobject]@{
                Succeeded = $true; WouldAppend = ($linesToAppend.Count -gt 0)
                DuplicateCount = $duplicateCount; NewLength = $currentLength; Error = ''
            }
        }

        $joined = (@($linesToAppend | ForEach-Object { $_.Line }) -join [Environment]::NewLine)
        if (-not $joined.EndsWith("`n")) { $joined += [Environment]::NewLine }
        $appended = $false
        for ($i = 0; $i -lt 40; $i++) {
            try {
                [System.IO.File]::AppendAllText($Path, $joined, $encoding)
                $appended = $true
                break
            } catch {
                Start-Sleep -Milliseconds (25 + ($i * 10))
            }
        }
        if ($appended) {
            foreach ($pair in $linesToAppend) {
                [void]$ExistingKeys.Add((Get-BridgeEventDedupKey -EventObject $pair.Obj))
            }
            $currentLength = [int64](Get-Item -LiteralPath $Path).Length
        }
        return [pscustomobject]@{
            Succeeded = $appended; WouldAppend = $true; DuplicateCount = $duplicateCount
            NewLength = $currentLength; Error = $(if ($appended) { '' } else { 'append retry budget exhausted' })
        }
    } finally {
        if ($null -ne $mutex) {
            if ($acquired) { try { $mutex.ReleaseMutex() } catch {} }
            $mutex.Dispose()
        }
    }
}

function Test-BridgeObject {
    param([AllowNull()] $Value)
    return (
        $null -ne $Value -and
        (
            $Value -is [System.Management.Automation.PSCustomObject] -or
            $Value -is [System.Collections.IDictionary]
        )
    )
}

function ConvertFrom-BridgeJson {
    param([Parameter(Mandatory)] [string] $Json)
    # PowerShell 7.5+ otherwise converts ISO timestamps to DateTime using the
    # local timezone, which destroys the original offset before validation.
    if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) {
        return ($Json | ConvertFrom-Json -DateKind String -ErrorAction Stop)
    }
    return ($Json | ConvertFrom-Json -ErrorAction Stop)
}

function Test-BridgeStringArray {
    param([AllowNull()] $Value)
    if (-not ($Value -is [array])) { return $false }
    foreach ($item in @($Value)) {
        if (-not ($item -is [string])) { return $false }
    }
    return $true
}

function Test-BridgeEventShape {
    param([AllowNull()] $EventObject)

    if (-not (Test-BridgeObject -Value $EventObject)) { return $false }

    # These fields are unconditional in Write-AgentEvent.ps1. Requiring them
    # here prevents replay from becoming a weaker alternate writer path.
    foreach ($fieldName in @(
        'ts_utc', 'agent', 'type', 'task_id', 'status', 'severity', 'to',
        'message', 'run_id', 'cwd'
    )) {
        $property = $EventObject.PSObject.Properties[$fieldName]
        if ($null -eq $property -or -not ($property.Value -is [string])) {
            return $false
        }
    }

    $timestamp = [string]$EventObject.ts_utc
    if ($timestamp -cnotmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?(?:Z|\+00:00)$') {
        return $false
    }
    $parsedTimestamp = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse(
        $timestamp,
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::RoundtripKind,
        [ref]$parsedTimestamp
    )) { return $false }
    if ($parsedTimestamp.Offset -ne [TimeSpan]::Zero) { return $false }

    if ($knownEventTypes -cnotcontains [string]$EventObject.type) { return $false }

    foreach ($fieldName in @('role', 'agent_uuid', 'session_id')) {
        $property = $EventObject.PSObject.Properties[$fieldName]
        if ($null -ne $property -and -not ($property.Value -is [string])) {
            return $false
        }
    }

    foreach ($fieldName in @('paths', 'write_scope')) {
        $property = $EventObject.PSObject.Properties[$fieldName]
        if ($null -eq $property -or -not (Test-BridgeStringArray -Value $property.Value)) {
            return $false
        }
    }

    $capabilitiesProperty = $EventObject.PSObject.Properties['capabilities']
    if (
        $null -ne $capabilitiesProperty -and
        -not (Test-BridgeStringArray -Value $capabilitiesProperty.Value)
    ) { return $false }

    $payloadProperty = $EventObject.PSObject.Properties['payload']
    if ($null -eq $payloadProperty -or -not (Test-BridgeObject -Value $payloadProperty.Value)) {
        return $false
    }

    $pidProperty = $EventObject.PSObject.Properties['pid']
    if ($null -eq $pidProperty -or -not (
        $pidProperty.Value -is [byte] -or
        $pidProperty.Value -is [int16] -or
        $pidProperty.Value -is [int32] -or
        $pidProperty.Value -is [int64]
    )) { return $false }

    return $true
}

try {
    # Capture a newline-aligned cursor while canonical writers are excluded.
    # The long historical scan remains shared; each later append refreshes only
    # the post-snapshot delta while holding this same append mutex.
    $dedupCursor = Get-BridgeLogSnapshotLength -Path $eventsPath
    $existingKeys = New-Object 'System.Collections.Generic.HashSet[string]'
    if (Test-Path -LiteralPath $eventsPath -PathType Leaf) {
        # The production log can be tens of megabytes. File.ReadLines opens it
        # with FileShare.Read, which blocks every bridge append for the duration
        # of this full scan. Use an explicit shared reader so replay never turns
        # routine deduplication into a global writer outage.
        $stream = New-Object System.IO.FileStream(
            $eventsPath,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::ReadWrite
        )
        $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8, $true)
        try {
            while ($null -ne ($line = $reader.ReadLine())) {
                if (-not $line) { continue }
                try {
                    $obj = $line | ConvertFrom-Json -ErrorAction Stop
                    if ($null -ne $obj -and $null -ne $obj.PSObject.Properties['type']) {
                        [void]$existingKeys.Add((Get-BridgeEventDedupKey -EventObject $obj))
                    }
                } catch {}
            }
        } finally {
            $reader.Dispose()
            $stream.Dispose()
        }
    }

    $replayed = 0
    $failed = 0
    $deduped = 0
    foreach ($file in $spoolFiles) {
        try {
            $raw = (Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 -ErrorAction Stop)
        } catch {
            if (-not (Test-Path -LiteralPath $file.FullName -PathType Leaf)) {
                Write-Warning "spool file disappeared before replay (skipped): $($file.Name)"
                continue
            }
            throw
        }
        if ([string]::IsNullOrWhiteSpace($raw)) {
            # Empty spool file: archive without appending.
            if (-not $DryRun) {
                [void](Move-SpoolToArchive -File $file -ArchiveDir $archiveDir)
            }
            continue
        }
        # Fail closed before a spooled row can bypass the canonical writer's
        # timestamp, event-type, payload, and scalar/array shape guarantees.
        $ok = $true
        $parsedLines = @()
        foreach ($line in ($raw -split "`r?`n" | Where-Object { $_ })) {
            try {
                $obj = ConvertFrom-BridgeJson -Json $line
                if (-not (Test-BridgeEventShape -EventObject $obj)) {
                    $ok = $false
                } else {
                    $parsedLines += [pscustomobject]@{ Line = $line; Obj = $obj }
                }
            } catch { $ok = $false }
        }
        if (-not $ok) {
            Write-Warning "skipping malformed spool file (left in place): $($file.Name)"
            $failed++
            continue
        }

        # Refresh post-snapshot live writes, decide deduplication, and append
        # under one mutex. This closes the scan-to-append TOCTOU window without
        # making the long historical scan deny concurrent writers.
        $result = Add-UniqueLinesWithMutex -Path $eventsPath -Pairs $parsedLines `
            -ExistingKeys $existingKeys -KnownLength $dedupCursor -DryRun:$DryRun
        $dedupCursor = [int64]$result.NewLength
        $deduped += [int]$result.DuplicateCount
        if (-not $result.Succeeded) {
            Write-Warning "append still failing; spool file kept: $($file.Name) ($($result.Error))"
            $failed++
            continue
        }

        if ($DryRun) {
            if ($result.WouldAppend) {
                Write-Output "would replay: $($file.Name)"
                $replayed++
            } else {
                Write-Output "would archive as duplicate: $($file.Name)"
            }
            continue
        }

        [void](Move-SpoolToArchive -File $file -ArchiveDir $archiveDir)
        if ($result.WouldAppend) {
            $replayed++
        }
    }

    Write-Output ("spool replay complete: replayed={0} deduped={1} failed={2} dryRun={3}" -f $replayed, $deduped, $failed, $DryRun.IsPresent)
} finally {
    if ($null -ne $replayMutex) {
        if ($replayAcquired) { try { $replayMutex.ReleaseMutex() } catch {} }
        $replayMutex.Dispose()
    }
}
