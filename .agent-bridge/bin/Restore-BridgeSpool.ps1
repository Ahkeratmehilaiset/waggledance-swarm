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

$replayed = 0
$failed = 0
foreach ($file in $spoolFiles) {
    $raw = (Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8)
    if ([string]::IsNullOrWhiteSpace($raw)) {
        # Empty spool file: archive without appending.
        if (-not $DryRun) {
            if (-not (Test-Path -LiteralPath $archiveDir)) {
                [void](New-Item -ItemType Directory -Path $archiveDir -Force)
            }
            Move-Item -LiteralPath $file.FullName -Destination (Join-Path $archiveDir $file.Name) -Force
        }
        continue
    }
    # Fail-closed shape check: only replay parseable JSON object lines.
    $ok = $true
    foreach ($line in ($raw -split "`r?`n" | Where-Object { $_ })) {
        try {
            $obj = $line | ConvertFrom-Json -ErrorAction Stop
            if ($null -eq $obj -or $null -eq $obj.PSObject.Properties['type']) { $ok = $false }
        } catch { $ok = $false }
    }
    if (-not $ok) {
        Write-Warning "skipping malformed spool file (left in place): $($file.Name)"
        $failed++
        continue
    }

    if ($DryRun) {
        Write-Output "would replay: $($file.Name)"
        $replayed++
        continue
    }

    if (Add-LineWithMutex -Path $eventsPath -Line $raw.TrimEnd()) {
        if (-not (Test-Path -LiteralPath $archiveDir)) {
            [void](New-Item -ItemType Directory -Path $archiveDir -Force)
        }
        Move-Item -LiteralPath $file.FullName -Destination (Join-Path $archiveDir $file.Name) -Force
        $replayed++
    } else {
        Write-Warning "append still failing; spool file kept: $($file.Name)"
        $failed++
    }
}

Write-Output ("spool replay complete: replayed={0} failed={1} dryRun={2}" -f $replayed, $failed, $DryRun.IsPresent)
