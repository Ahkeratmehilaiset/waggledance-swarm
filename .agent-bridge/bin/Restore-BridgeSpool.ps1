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

function Add-LineWithMutex {
    param([Parameter(Mandatory)] [string] $Path, [Parameter(Mandatory)] [string] $Line)
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent)) {
        [void](New-Item -ItemType Directory -Path $parent -Force)
    }
    $encoding = New-Object System.Text.UTF8Encoding($false)
    if (-not $Line.EndsWith("`n")) { $Line = $Line + [Environment]::NewLine }

    $mutex = $null
    $acquired = $false
    try {
        try {
            $mutex = New-Object System.Threading.Mutex($false, 'Global\WaggleDanceBridgeAppendV1')
            try { $acquired = $mutex.WaitOne(10000) }
            catch [System.Threading.AbandonedMutexException] { $acquired = $true }
        } catch { $mutex = $null }

        for ($i = 0; $i -lt 40; $i++) {
            try {
                [System.IO.File]::AppendAllText($Path, $Line, $encoding)
                return $true
            } catch {
                Start-Sleep -Milliseconds (25 + ($i * 10))
            }
        }
        return $false
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

try {
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
        # Fail-closed shape check (rco-2 finding 2: mirror the reader guard) -
        # every line must be a JSON object carrying the writer's core fields.
        $ok = $true
        $parsedLines = @()
        foreach ($line in ($raw -split "`r?`n" | Where-Object { $_ })) {
            try {
                $obj = $line | ConvertFrom-Json -ErrorAction Stop
                if (
                    $null -eq $obj -or
                    $null -eq $obj.PSObject.Properties['type'] -or
                    $null -eq $obj.PSObject.Properties['agent'] -or
                    $null -eq $obj.PSObject.Properties['task_id'] -or
                    $null -eq $obj.PSObject.Properties['status']
                ) { $ok = $false } else { $parsedLines += [pscustomobject]@{ Line = $line; Obj = $obj } }
            } catch { $ok = $false }
        }
        if (-not $ok) {
            Write-Warning "skipping malformed spool file (left in place): $($file.Name)"
            $failed++
            continue
        }

        # Dedup (rco-2 finding 1): drop lines whose signal is already live in
        # events.jsonl (the common case: the caller retried after spooling and
        # the retry succeeded). Archive-without-append when everything deduped.
        $linesToAppend = @()
        foreach ($pair in $parsedLines) {
            $key = Get-BridgeEventDedupKey -EventObject $pair.Obj
            if ($existingKeys.Contains($key)) { $deduped++ } else { $linesToAppend += $pair }
        }
        if ($linesToAppend.Count -eq 0) {
            if (-not $DryRun) {
                [void](Move-SpoolToArchive -File $file -ArchiveDir $archiveDir)
            } else {
                Write-Output "would archive as duplicate: $($file.Name)"
            }
            continue
        }

        if ($DryRun) {
            Write-Output "would replay: $($file.Name)"
            $replayed++
            continue
        }

        $joined = (@($linesToAppend | ForEach-Object { $_.Line }) -join [Environment]::NewLine)
        if (Add-LineWithMutex -Path $eventsPath -Line $joined) {
            foreach ($pair in $linesToAppend) {
                [void]$existingKeys.Add((Get-BridgeEventDedupKey -EventObject $pair.Obj))
            }
            [void](Move-SpoolToArchive -File $file -ArchiveDir $archiveDir)
            $replayed++
        } else {
            Write-Warning "append still failing; spool file kept: $($file.Name)"
            $failed++
        }
    }

    Write-Output ("spool replay complete: replayed={0} deduped={1} failed={2} dryRun={3}" -f $replayed, $deduped, $failed, $DryRun.IsPresent)
} finally {
    if ($null -ne $replayMutex) {
        if ($replayAcquired) { try { $replayMutex.ReleaseMutex() } catch {} }
        $replayMutex.Dispose()
    }
}
