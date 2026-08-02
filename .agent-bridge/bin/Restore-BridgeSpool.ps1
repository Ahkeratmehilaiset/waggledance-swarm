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

$sessionIdentity = Join-Path $PSScriptRoot 'AgentBridgeSessionIdentity.ps1'
. $sessionIdentity

if (-not $BridgeRoot) {
    $BridgeRoot = if ($env:AGENT_BRIDGE_RUNTIME_ROOT) {
        [string]$env:AGENT_BRIDGE_RUNTIME_ROOT
    } else {
        Split-Path -Parent $PSScriptRoot
    }
}
$BridgeRoot = [System.IO.Path]::GetFullPath($BridgeRoot)

$spoolDir   = Join-Path $BridgeRoot 'spool'
$eventsPath = Join-Path (Join-Path $BridgeRoot 'shared') 'events.jsonl'
$archiveDir = Join-Path $spoolDir 'replayed'

if (-not (Test-Path -LiteralPath $spoolDir -PathType Container)) {
    Write-Output 'no spool directory; nothing to replay'
    return
}
Assert-AgentBridgePlainDirectory `
    -LiteralPath $BridgeRoot `
    -Context 'bridge runtime root'
Assert-AgentBridgePlainDirectory `
    -LiteralPath $spoolDir `
    -Context 'bridge spool directory'

$replayMutex = $null
$replayAcquired = $false
$appendMutex = $null
$appendAcquired = $false
$eventsLease = $null
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

function Read-AgentBridgeHeldReplayUtf8 {
    param(
        [Parameter(Mandatory)] $Lease,
        [string] $Context = 'bridge spool replay source'
    )

    Assert-AgentBridgeParentDirectoryPin -Pin $Lease.source_parent_pin
    Assert-AgentBridgeChildHandleParentPin `
        -Pin $Lease.source_parent_pin `
        -ChildHandle $Lease.stream.SafeFileHandle
    Assert-AgentBridgeExclusiveHandleIdentity `
        -Stream $Lease.stream `
        -Context $Context
    if ([long]$Lease.stream.Length -gt [int]::MaxValue) {
        throw "$Context exceeds the supported replay size"
    }
    [void]$Lease.stream.Seek(0, [System.IO.SeekOrigin]::Begin)
    $bytes = [byte[]]::new([int]$Lease.stream.Length)
    $offset = 0
    while ($offset -lt $bytes.Length) {
        $read = $Lease.stream.Read(
            $bytes,
            $offset,
            $bytes.Length - $offset
        )
        if ($read -le 0) {
            throw "$Context ended before its held length was read"
        }
        $offset += $read
    }
    Assert-AgentBridgeExclusiveHandleIdentity `
        -Stream $Lease.stream `
        -Context $Context
    Assert-AgentBridgeChildHandleParentPin `
        -Pin $Lease.source_parent_pin `
        -ChildHandle $Lease.stream.SafeFileHandle
    Assert-AgentBridgeParentDirectoryPin -Pin $Lease.source_parent_pin
    $strictUtf8 = [System.Text.UTF8Encoding]::new($false, $true)
    $text = $strictUtf8.GetString($bytes)
    if ($text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) {
        $text = $text.Substring(1)
    }
    return $text
}

function Open-AgentBridgeCanonicalEventTransaction {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [switch] $ReadOnlyMissing
    )

    $sharedDir = [System.IO.Path]::GetDirectoryName(
        [System.IO.Path]::GetFullPath($Path)
    )
    if (-not (Test-Path -LiteralPath $sharedDir)) {
        if ($ReadOnlyMissing) { return $null }
        Ensure-AgentBridgePlainDirectory `
            -LiteralPath $sharedDir `
            -Context 'bridge shared directory'
    } else {
        Assert-AgentBridgePlainDirectory `
            -LiteralPath $sharedDir `
            -Context 'bridge shared directory'
    }
    if (-not (Test-Path -LiteralPath $Path) -and $ReadOnlyMissing) {
        return $null
    }
    if (Test-Path -LiteralPath $Path) {
        Assert-AgentBridgeRegularUnlinkedFile `
            -LiteralPath $Path `
            -Context 'bridge canonical event log'
    }

    $parentPin = $null
    $stream = $null
    try {
        $parentPin = Enter-AgentBridgeParentDirectoryPin `
            -ChildPath $Path `
            -Context 'bridge canonical event log'
        Assert-AgentBridgeParentDirectoryPin -Pin $parentPin
        $stream = [System.IO.File]::Open(
            $Path,
            [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::Read
        )
        Assert-AgentBridgeParentDirectoryPin -Pin $parentPin
        Assert-AgentBridgeChildHandleParentPin `
            -Pin $parentPin `
            -ChildHandle $stream.SafeFileHandle
        Assert-AgentBridgeExclusiveHandleIdentity `
            -Stream $stream `
            -Context 'bridge canonical event log'
        Assert-AgentBridgeRegularUnlinkedFile `
            -LiteralPath $Path `
            -Context 'bridge canonical event log'
        Assert-AgentBridgeParentDirectoryPin -Pin $parentPin
        return [pscustomobject]@{
            stream = $stream
            parent_pin = $parentPin
            path = [System.IO.Path]::GetFullPath($Path)
        }
    } catch {
        if ($null -ne $stream) {
            try { $stream.Dispose() } catch {}
        }
        try {
            Exit-AgentBridgeParentDirectoryPin -Pin $parentPin
        } catch {}
        throw
    }
}

function Close-AgentBridgeCanonicalEventTransaction {
    param([AllowNull()] $Lease)
    if ($null -eq $Lease) { return }
    $failures = @()
    if ($null -ne $Lease.stream) {
        try { $Lease.stream.Dispose() } catch { $failures += $_.Exception }
        $Lease.stream = $null
    }
    if ($null -ne $Lease.parent_pin) {
        try {
            Exit-AgentBridgeParentDirectoryPin -Pin $Lease.parent_pin
        } catch {
            $failures += $_.Exception
        }
        $Lease.parent_pin = $null
    }
    if (@($failures).Count -gt 0) {
        Write-AgentBridgeNonThrowingWarning -Message (
            'bridge canonical event transaction finalization reported: ' +
            ((@($failures) | ForEach-Object { $_.Message }) -join '; ')
        )
    }
}

function Get-BridgeExistingEventKeys {
    param([AllowNull()] $Lease)
    $keys = New-Object 'System.Collections.Generic.HashSet[string]'
    if ($null -eq $Lease) { return ,$keys }

    Assert-AgentBridgeParentDirectoryPin -Pin $Lease.parent_pin
    Assert-AgentBridgeChildHandleParentPin `
        -Pin $Lease.parent_pin `
        -ChildHandle $Lease.stream.SafeFileHandle
    Assert-AgentBridgeExclusiveHandleIdentity `
        -Stream $Lease.stream `
        -Context 'bridge canonical event log'
    if ([long]$Lease.stream.Length -gt 0) {
        [void]$Lease.stream.Seek(-1, [System.IO.SeekOrigin]::End)
        if ($Lease.stream.ReadByte() -ne 10) {
            throw 'bridge canonical event log does not end with LF'
        }
    }
    [void]$Lease.stream.Seek(0, [System.IO.SeekOrigin]::Begin)
    $strictUtf8 = [System.Text.UTF8Encoding]::new($false, $true)
    $reader = [System.IO.StreamReader]::new(
        $Lease.stream,
        $strictUtf8,
        $true,
        65536,
        $true
    )
    try {
        while ($null -ne ($line = $reader.ReadLine())) {
            if (-not $line) { continue }
            try {
                $obj = $line | ConvertFrom-Json -ErrorAction Stop
                if (
                    $null -ne $obj -and
                    $null -ne $obj.PSObject.Properties['type']
                ) {
                    [void]$keys.Add(
                        (Get-BridgeEventDedupKey -EventObject $obj)
                    )
                }
            } catch {
                # Preserve the existing behavior: unrelated historical malformed
                # lines do not grant replay authority and do not become keys.
            }
        }
    } finally {
        $reader.Dispose()
    }
    Assert-AgentBridgeExclusiveHandleIdentity `
        -Stream $Lease.stream `
        -Context 'bridge canonical event log'
    Assert-AgentBridgeChildHandleParentPin `
        -Pin $Lease.parent_pin `
        -ChildHandle $Lease.stream.SafeFileHandle
    Assert-AgentBridgeParentDirectoryPin -Pin $Lease.parent_pin
    return ,$keys
}

function Add-BridgeEventBytesInTransaction {
    param(
        [Parameter(Mandatory)] $Lease,
        [Parameter(Mandatory)] [byte[]] $Bytes
    )
    if ($Bytes.Length -eq 0) { return }

    $stream = $Lease.stream
    $originalLength = [long]-1
    $mutationStarted = $false
    try {
        Assert-AgentBridgeParentDirectoryPin -Pin $Lease.parent_pin
        Assert-AgentBridgeChildHandleParentPin `
            -Pin $Lease.parent_pin `
            -ChildHandle $stream.SafeFileHandle
        Assert-AgentBridgeExclusiveHandleIdentity `
            -Stream $stream `
            -Context 'bridge canonical event log'
        $originalLength = [long]$stream.Length
        if ($originalLength -gt 0) {
            [void]$stream.Seek(-1, [System.IO.SeekOrigin]::End)
            if ($stream.ReadByte() -ne 10) {
                throw 'bridge canonical event log does not end with LF'
            }
        }
        [void]$stream.Seek(0, [System.IO.SeekOrigin]::End)
        $mutationStarted = $true
        $stream.Write($Bytes, 0, $Bytes.Length)
        $stream.Flush($true)
        if (
            [long]$stream.Length -ne
            ($originalLength + [long]$Bytes.Length)
        ) {
            throw 'bridge canonical event append length verification failed'
        }
        [void]$stream.Seek($originalLength, [System.IO.SeekOrigin]::Begin)
        $verified = [byte[]]::new($Bytes.Length)
        $offset = 0
        while ($offset -lt $verified.Length) {
            $read = $stream.Read(
                $verified,
                $offset,
                $verified.Length - $offset
            )
            if ($read -le 0) {
                throw 'bridge canonical event append suffix ended early'
            }
            $offset += $read
        }
        for ($index = 0; $index -lt $Bytes.Length; $index++) {
            if ($verified[$index] -ne $Bytes[$index]) {
                throw 'bridge canonical event append suffix mismatched'
            }
        }
        Assert-AgentBridgeExclusiveHandleIdentity `
            -Stream $stream `
            -Context 'bridge canonical event log'
        Assert-AgentBridgeRegularUnlinkedFile `
            -LiteralPath $Lease.path `
            -Context 'bridge canonical event log'
        Assert-AgentBridgeChildHandleParentPin `
            -Pin $Lease.parent_pin `
            -ChildHandle $stream.SafeFileHandle
        Assert-AgentBridgeParentDirectoryPin -Pin $Lease.parent_pin
    } catch {
        $appendError = $_.Exception
        if ($mutationStarted -and $originalLength -ge 0) {
            try {
                Restore-AgentBridgeAppendLength `
                    -Stream $stream `
                    -OriginalLength $originalLength `
                    -Context 'bridge canonical event log'
            } catch {
                $ambiguousMessage = (
                    'bridge canonical event append and rollback failed; ' +
                    'outcome is ambiguous (append_error={0}; ' +
                    'rollback_error={1})'
                ) -f $appendError.Message, $_.Exception.Message
                $ambiguous = [System.IO.IOException]::new(
                    $ambiguousMessage,
                    $appendError
                )
                $ambiguous.Data['AgentBridgeAppendAmbiguous'] = $true
                throw $ambiguous
            }
            $rolledBackMessage = (
                'bridge canonical event append was durably rolled back ' +
                'to length {0}: {1}'
            ) -f $originalLength, $appendError.Message
            $rolledBack = [System.IO.IOException]::new(
                $rolledBackMessage,
                $appendError
            )
            $rolledBack.Data['AgentBridgeAppendRolledBack'] = $true
            throw $rolledBack
        }
        throw $appendError
    }
}

try {
    $spoolFiles = @(
        Get-ChildItem `
            -LiteralPath $spoolDir `
            -Filter 'failed-append-*.jsonl' `
            -File `
            -ErrorAction SilentlyContinue |
            Sort-Object Name
    )
    if (@($spoolFiles).Count -eq 0) {
        Write-Output 'spool empty; nothing to replay'
        return
    }

    try {
        $appendMutex = New-Object System.Threading.Mutex(
            $false,
            'Global\WaggleDanceBridgeAppendV1'
        )
        try {
            $appendAcquired = $appendMutex.WaitOne(10000)
        } catch [System.Threading.AbandonedMutexException] {
            $appendAcquired = $true
        }
        if (-not $appendAcquired) {
            throw 'timed out acquiring Global\WaggleDanceBridgeAppendV1'
        }
    } catch {
        throw (
            'could not acquire canonical bridge append mutex; replay made ' +
            "no changes: $($_.Exception.Message)"
        )
    }

    if (-not $DryRun) {
        Ensure-AgentBridgePlainDirectory `
            -LiteralPath ([System.IO.Path]::GetDirectoryName($eventsPath)) `
            -Context 'bridge shared directory'
        Ensure-AgentBridgePlainDirectory `
            -LiteralPath $archiveDir `
            -Context 'bridge replay archive directory'
    }
    $eventsLease = Open-AgentBridgeCanonicalEventTransaction `
        -Path $eventsPath `
        -ReadOnlyMissing:$DryRun
    $existingKeys = Get-BridgeExistingEventKeys -Lease $eventsLease

    $replayed = 0
    $failed = 0
    $deduped = 0
    foreach ($file in $spoolFiles) {
        $sourceLease = $null
        try {
            $sourceLease = Open-AgentBridgeHeldReplayFile `
                -LiteralPath $file.FullName `
                -Context "bridge spool replay source $($file.Name)"
            $raw = Read-AgentBridgeHeldReplayUtf8 `
                -Lease $sourceLease `
                -Context "bridge spool replay source $($file.Name)"
        } catch {
            if (
                [bool]$_.Exception.Data[
                    'AgentBridgeReplaySourceNotFound'
                ]
            ) {
                Write-Warning "spool file disappeared before replay (skipped): $($file.Name)"
                continue
            }
            Write-Warning (
                "spool source could not be held for replay (kept): " +
                "$($file.Name): $($_.Exception.Message)"
            )
            $failed++
            continue
        }
        try {
            if ([string]::IsNullOrWhiteSpace($raw)) {
                if (-not $DryRun) {
                    [void](
                        Move-AgentBridgeHeldReplayFileToPinnedDirectory `
                            -Lease $sourceLease `
                            -DestinationPath (
                                Join-Path $archiveDir $file.Name
                            ) `
                            -Context 'empty spool replay archive'
                    )
                }
                continue
            }

            # Every line must be a JSON object carrying the writer's core
            # fields. Parsing is against the continuously held source handle.
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
                    ) {
                        $ok = $false
                    } else {
                        $parsedLines += [pscustomobject]@{
                            Line = $line
                            Obj = $obj
                        }
                    }
                } catch {
                    $ok = $false
                }
            }
            if (-not $ok) {
                Write-Warning (
                    'skipping malformed spool file (left in place): ' +
                    $file.Name
                )
                $failed++
                continue
            }

            # Canonical scan, same-file dedup, and append all remain under the
            # one mandatory cross-runtime mutex. Add each pending key
            # immediately so duplicate lines inside one spool file collapse.
            $pendingKeys = (
                [System.Collections.Generic.HashSet[string]]::new()
            )
            $linesToAppend = @()
            foreach ($pair in $parsedLines) {
                $key = Get-BridgeEventDedupKey -EventObject $pair.Obj
                if (
                    $existingKeys.Contains($key) -or
                    $pendingKeys.Contains($key)
                ) {
                    $deduped++
                } else {
                    [void]$pendingKeys.Add($key)
                    $linesToAppend += $pair
                }
            }
            if (@($linesToAppend).Count -eq 0) {
                if (-not $DryRun) {
                    [void](
                        Move-AgentBridgeHeldReplayFileToPinnedDirectory `
                            -Lease $sourceLease `
                            -DestinationPath (
                                Join-Path $archiveDir $file.Name
                            ) `
                            -Context 'duplicate spool replay archive'
                    )
                } else {
                    Write-Output (
                        "would archive as duplicate: $($file.Name)"
                    )
                }
                continue
            }

            if ($DryRun) {
                Write-Output "would replay: $($file.Name)"
                foreach ($key in $pendingKeys) {
                    [void]$existingKeys.Add($key)
                }
                $replayed++
                continue
            }

            $joined = (
                @($linesToAppend | ForEach-Object { $_.Line }) -join
                [Environment]::NewLine
            ) + [Environment]::NewLine
            $encoding = [System.Text.UTF8Encoding]::new($false, $true)
            $appendBytes = $encoding.GetBytes($joined)
            Add-BridgeEventBytesInTransaction `
                -Lease $eventsLease `
                -Bytes $appendBytes
            foreach ($pair in $linesToAppend) {
                [void]$existingKeys.Add(
                    (Get-BridgeEventDedupKey -EventObject $pair.Obj)
                )
            }
            [void](
                Move-AgentBridgeHeldReplayFileToPinnedDirectory `
                    -Lease $sourceLease `
                    -DestinationPath (Join-Path $archiveDir $file.Name) `
                    -Context 'committed spool replay archive'
            )
            $replayed++
        } catch {
            $failureStack = [string]$_.ScriptStackTrace
            Write-Warning (
                "spool replay failed; held evidence retained: " +
                "$($file.Name): $($_.Exception.Message)" +
                $(if ($failureStack) { "; stack=$failureStack" } else { '' })
            )
            $failed++
        } finally {
            Close-AgentBridgeHeldReplayFile `
                -Lease $sourceLease `
                -Context "bridge spool replay source $($file.Name)"
        }
    }

    Write-Output ("spool replay complete: replayed={0} deduped={1} failed={2} dryRun={3}" -f $replayed, $deduped, $failed, $DryRun.IsPresent)
} finally {
    Close-AgentBridgeCanonicalEventTransaction -Lease $eventsLease
    if ($null -ne $appendMutex) {
        if ($appendAcquired) {
            try {
                $appendMutex.ReleaseMutex()
            } catch {
                Write-AgentBridgeNonThrowingWarning -Message (
                    'canonical append mutex release reported after replay: ' +
                    $_.Exception.Message
                )
            }
        }
        try {
            $appendMutex.Dispose()
        } catch {
            Write-AgentBridgeNonThrowingWarning -Message (
                'canonical append mutex disposal reported after replay: ' +
                $_.Exception.Message
            )
        }
    }
    if ($null -ne $replayMutex) {
        if ($replayAcquired) {
            try {
                $replayMutex.ReleaseMutex()
            } catch {
                Write-AgentBridgeNonThrowingWarning -Message (
                    'spool replay mutex release reported: ' +
                    $_.Exception.Message
                )
            }
        }
        try {
            $replayMutex.Dispose()
        } catch {
            Write-AgentBridgeNonThrowingWarning -Message (
                'spool replay mutex disposal reported: ' +
                $_.Exception.Message
            )
        }
    }
}
