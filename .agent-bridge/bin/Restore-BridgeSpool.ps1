#requires -Version 5.1
<#
.SYNOPSIS
    Replay durable failed-append-<agent>-<utc>-<pid>-<nonce>.jsonl bridge spools.

.DESCRIPTION
    Write-AgentEvent.ps1 spools an event to <bridgeRoot>/spool/
    failed-append-<agent>-<utc>-<pid>-<nonce>.jsonl when the V1-protected
    shared-log append cannot complete. This script
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

function New-BridgeV1Mutex {
    param(
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [string] $Purpose
    )

    # A fail-closed test hook: forcing construction failure can only keep a
    # spool in place; it can never authorize an unlocked append.
    $forcedFailure = [Environment]::GetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_MUTEX_CONSTRUCTION_FAILURE',
        'Process'
    )
    if ($forcedFailure -in @('All', $Purpose)) {
        throw "simulated bridge $Purpose mutex construction failure"
    }
    return New-Object System.Threading.Mutex($false, $Name)
}

$replayMutex = $null
$replayAcquired = $false
try {
    $replayMutex = New-BridgeV1Mutex `
        -Name 'Global\WaggleDanceBridgeSpoolReplayV1' -Purpose SpoolReplay
    if ($null -eq $replayMutex) {
        throw 'bridge spool replay mutex construction returned null'
    }
    try { $replayAcquired = $replayMutex.WaitOne(0) }
    catch [System.Threading.AbandonedMutexException] { $replayAcquired = $true }
} catch {
    if ($null -ne $replayMutex) { $replayMutex.Dispose() }
    throw "could not acquire bridge spool replay mutex: $($_.Exception.Message)"
}
if (-not $replayAcquired) {
    if ($null -ne $replayMutex) { $replayMutex.Dispose() }
    Write-Output 'spool replay already running; exiting without changes'
    return
}

function Invoke-BridgeTransactionalAppend {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [byte[]] $Bytes,
        [Parameter(Mandatory)] [bool] $AppendMutexOwned
    )
    if (-not $AppendMutexOwned) {
        throw 'refusing transactional replay append without AppendV1 ownership'
    }
    # Fail closed before creating shared/ or opening/creating events.jsonl.
    Initialize-BridgeAppendV1Native
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        [void](New-Item -ItemType Directory -Path $parent -Force)
    }
    $stream = $null
    $preAppendLength = [int64]0
    try {
        $stream = New-Object System.IO.FileStream(
            $Path,
            [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::Read
        )
        $preAppendLength = [int64]$stream.Length
        # Atomically replace any valid writer checkpoint with a durable invalid
        # marker before canonical mutation. This remains necessary even when a
        # truncate+replay happens to restore the same identity, length, and tail.
        Invalidate-BridgeAppendValidationCheckpoint `
            -CanonicalPath $Path -Reason 'spool-replay-append'
        [void]$stream.Seek($preAppendLength, [System.IO.SeekOrigin]::Begin)
        try {
            $forcedCountText = [Environment]::GetEnvironmentVariable(
                'AGENT_BRIDGE_TEST_APPEND_FAILURE_AFTER_BYTES',
                'Process'
            )
            if ($forcedCountText) {
                $forcedCount = 0
                if (-not [int]::TryParse($forcedCountText, [ref]$forcedCount)) {
                    throw 'invalid test partial-append byte count'
                }
                if ($forcedCount -lt 0 -or $forcedCount -ge $Bytes.Length) {
                    throw 'test partial-append byte count is outside the replay payload'
                }
                if ($forcedCount -gt 0) {
                    $stream.Write($Bytes, 0, $forcedCount)
                }
                throw 'simulated transactional replay failure after partial write'
            }
            $stream.Write($Bytes, 0, $Bytes.Length)
            $stream.Flush($true)
            return [pscustomobject]@{
                PreAppendLength = $preAppendLength
                AppendedLength = [int64]$Bytes.Length
            }
        } catch {
            $appendError = $_.Exception.Message
            try {
                $stream.SetLength($preAppendLength)
                $stream.Flush($true)
            } catch {
                throw (
                    "transactional replay append failed ($appendError); " +
                    "ROLLBACK FAILED: $($_.Exception.Message)"
                )
            }
            throw "transactional replay append failed and rolled back: $appendError"
        }
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
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
    $destination = Join-Path $ArchiveDir $File.Name
    if (Test-Path -LiteralPath $destination) {
        $destination = Join-Path $ArchiveDir (
            '{0}.archive-collision.{1}' -f
            $File.Name,
            [guid]::NewGuid().ToString('N')
        )
    }
    try {
        # File.Move never replaces an existing destination. If an external
        # actor wins the destination race, fail closed with the WAL retained.
        [System.IO.File]::Move($File.FullName, $destination)
        return $true
    } catch {
        if (-not (Test-Path -LiteralPath $File.FullName -PathType Leaf)) {
            Write-Warning "spool file already moved before archive (skipped): $($File.Name)"
            return $false
        }
        throw
    }
}

function Get-BridgeSha256Hex {
    param(
        [Parameter(Mandatory)] [AllowEmptyCollection()] [byte[]] $Bytes
    )
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($Bytes))).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Test-BridgeBytesEqual {
    param(
        [Parameter(Mandatory)] [byte[]] $Left,
        [Parameter(Mandatory)] [byte[]] $Right
    )
    if ($Left.Length -ne $Right.Length) { return $false }
    for ($index = 0; $index -lt $Left.Length; $index++) {
        if ($Left[$index] -ne $Right[$index]) { return $false }
    }
    return $true
}

function Test-BridgeBytesStartWith {
    param(
        [Parameter(Mandatory)] [byte[]] $Bytes,
        [Parameter(Mandatory)] [byte[]] $Prefix
    )
    if ($Prefix.Length -gt $Bytes.Length) { return $false }
    for ($index = 0; $index -lt $Prefix.Length; $index++) {
        if ($Bytes[$index] -ne $Prefix[$index]) { return $false }
    }
    return $true
}

function Test-BridgeSharingViolation {
    param([Parameter(Mandatory)] [System.Exception] $Exception)
    $current = $Exception
    while ($null -ne $current) {
        $nativeCode = ([int64]$current.HResult -band 0xFFFF)
        if ($nativeCode -in @(32, 33)) { return $true }
        $current = $current.InnerException
    }
    return $false
}

function Write-NewBridgeFileDurably {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [byte[]] $Bytes
    )
    $stream = $null
    try {
        $stream = New-Object System.IO.FileStream(
            $Path,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
        $stream.Write($Bytes, 0, $Bytes.Length)
        $stream.Flush($true)
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
    }
}

function Initialize-BridgeAppendV1Native {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw (
            'AppendV1 checkpoint invalidation requires Windows write-through ' +
            'atomic replacement; refusing canonical replay mutation'
        )
    }
    if ('WaggleDance.BridgeAppendV1Native' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

namespace WaggleDance {
    [StructLayout(LayoutKind.Sequential)]
    public struct BridgeByHandleFileInformation {
        public uint FileAttributes;
        public System.Runtime.InteropServices.ComTypes.FILETIME CreationTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastAccessTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWriteTime;
        public uint VolumeSerialNumber;
        public uint FileSizeHigh;
        public uint FileSizeLow;
        public uint NumberOfLinks;
        public uint FileIndexHigh;
        public uint FileIndexLow;
    }

    public static class BridgeAppendV1Native {
        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool GetFileInformationByHandle(
            IntPtr fileHandle,
            out BridgeByHandleFileInformation fileInformation
        );

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool MoveFileExW(
            string existingPath,
            string destinationPath,
            uint flags
        );
    }
}
'@
}

function Get-BridgeOpenFileIdentity {
    param([Parameter(Mandatory)] [System.IO.FileStream] $Stream)

    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        return ''
    }
    Initialize-BridgeAppendV1Native
    $information = New-Object WaggleDance.BridgeByHandleFileInformation
    $handle = $Stream.SafeFileHandle.DangerousGetHandle()
    if (-not [WaggleDance.BridgeAppendV1Native]::GetFileInformationByHandle(
        $handle,
        [ref]$information
    )) {
        $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        $nativeError = New-Object System.ComponentModel.Win32Exception($nativeCode)
        throw "GetFileInformationByHandle failed: $nativeCode ($($nativeError.Message))"
    }
    return ('windows-file-id-v1:{0:x8}:{1:x8}:{2:x8}' -f
        ([uint32]$information.VolumeSerialNumber),
        ([uint32]$information.FileIndexHigh),
        ([uint32]$information.FileIndexLow))
}

function Invalidate-BridgeAppendValidationCheckpoint {
    param(
        [Parameter(Mandatory)] [string] $CanonicalPath,
        [Parameter(Mandatory)] [string] $Reason
    )

    if ([Environment]::GetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_CHECKPOINT_INVALIDATION_FAILURE',
        'Process'
    ) -eq '1') {
        throw 'simulated validation checkpoint invalidation failure'
    }
    Initialize-BridgeAppendV1Native
    $checkpointPath = "$CanonicalPath.append-v1-validation.json"
    $marker = [ordered]@{
        schema = 'waggledance.bridge.append-v1-validation-invalidated'
        version = [int64]1
        reason = $Reason
        nonce = [guid]::NewGuid().ToString('N')
    }
    $strictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
    [byte[]]$markerBytes = $strictUtf8.GetBytes(
        (($marker | ConvertTo-Json -Compress) + [char]10)
    )
    $temporaryPath = "$checkpointPath.tmp.$PID.$([guid]::NewGuid().ToString('N'))"
    try {
        Write-NewBridgeFileDurably -Path $temporaryPath -Bytes $markerBytes
        if (-not [WaggleDance.BridgeAppendV1Native]::MoveFileExW(
            $temporaryPath,
            $checkpointPath,
            [uint32]0x00000009
        )) {
            $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            $nativeError = New-Object System.ComponentModel.Win32Exception($nativeCode)
            throw "write-through checkpoint invalidation failed: $nativeCode ($($nativeError.Message))"
        }
        [byte[]]$publishedBytes = [System.IO.File]::ReadAllBytes($checkpointPath)
        if (-not (Test-BridgeBytesEqual -Left $publishedBytes -Right $markerBytes)) {
            throw 'published validation checkpoint invalidation marker verification failed'
        }
    } finally {
        if (Test-Path -LiteralPath $temporaryPath -PathType Leaf) {
            Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        }
    }
}

function Assert-BridgeEventObjectShape {
    param(
        [AllowNull()] $Object,
        [Parameter(Mandatory)] [string] $Label
    )
    if (-not ($Object -is [System.Management.Automation.PSCustomObject])) {
        throw "$Label is not a JSON object"
    }
    foreach ($field in @('type', 'agent', 'task_id', 'status')) {
        $property = $Object.PSObject.Properties[$field]
        if ($null -eq $property) {
            throw "$Label is missing core field '$field'"
        }
        if (-not ($property.Value -is [string])) {
            throw "$Label core field '$field' is not a string"
        }
    }
    if ($knownEventTypes -cnotcontains [string]$Object.type) {
        throw "$Label has an unknown event type"
    }
    if ([string]$Object.agent -cnotmatch '^[a-z][a-z0-9_-]{1,32}$') {
        throw "$Label has an invalid agent id"
    }
}

function Read-BridgeWalFile {
    param([Parameter(Mandatory)] [System.IO.FileInfo] $File)

    [byte[]]$bytes = [System.IO.File]::ReadAllBytes($File.FullName)
    if ($bytes.Length -eq 0 -or $bytes[$bytes.Length - 1] -ne 10) {
        throw "WAL is empty or does not end with LF: $($File.Name)"
    }
    $strictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
    try { $text = $strictUtf8.GetString($bytes) }
    catch { throw "WAL is not strict UTF-8: $($File.Name) ($($_.Exception.Message))" }
    $lines = $text.Split([char]10)
    if ($lines.Count -lt 2 -or $lines[$lines.Count - 1] -cne '') {
        throw "WAL is not complete JSONL: $($File.Name)"
    }
    $rows = New-Object 'System.Collections.Generic.List[object]'
    for ($index = 0; $index -lt $lines.Count - 1; $index++) {
        $exactLine = [string]$lines[$index]
        $jsonLine = $exactLine
        if ($jsonLine.EndsWith([string][char]13)) {
            $jsonLine = $jsonLine.Substring(0, $jsonLine.Length - 1)
        }
        if ([string]::IsNullOrWhiteSpace($jsonLine)) {
            throw "WAL contains a blank row at line $($index + 1): $($File.Name)"
        }
        try { $eventObject = $jsonLine | ConvertFrom-Json -ErrorAction Stop }
        catch {
            throw "WAL has malformed JSON at line $($index + 1): $($File.Name) ($($_.Exception.Message))"
        }
        Assert-BridgeEventObjectShape `
            -Object $eventObject -Label "WAL row $($index + 1) in $($File.Name)"
        [byte[]]$rowBytes = $strictUtf8.GetBytes($exactLine + [char]10)
        $rows.Add([pscustomobject]@{
            Obj = $eventObject
            Bytes = $rowBytes
            Key = Get-BridgeSha256Hex -Bytes $rowBytes
        })
    }
    if ($rows.Count -eq 0) { throw "WAL has no event rows: $($File.Name)" }
    return [pscustomobject]@{
        File = $File
        Bytes = $bytes
        Rows = $rows.ToArray()
    }
}

function Join-BridgeWalRowBytes {
    param([Parameter(Mandatory)] [object[]] $Rows)
    $memory = New-Object System.IO.MemoryStream
    try {
        foreach ($row in $Rows) {
            [byte[]]$rowBytes = $row.Bytes
            $memory.Write($rowBytes, 0, $rowBytes.Length)
        }
        return ,$memory.ToArray()
    } finally {
        $memory.Dispose()
    }
}

function Add-BridgeCanonicalKeysFromBytes {
    param(
        [Parameter(Mandatory)] [AllowEmptyCollection()] [byte[]] $Bytes,
        [Parameter(Mandatory)] [string] $Label,
        [Parameter(Mandatory)] [AllowEmptyCollection()]
        [System.Collections.Generic.HashSet[string]] $Keys
    )

    if ($Bytes.Length -eq 0) { return }
    if ($Bytes[$Bytes.Length - 1] -ne 10) {
        throw "$Label has an unterminated tail"
    }
    $strictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
    try { $text = $strictUtf8.GetString($Bytes) }
    catch { throw "$Label is not strict UTF-8: $($_.Exception.Message)" }
    $lines = $text.Split([char]10)
    for ($index = 0; $index -lt $lines.Count - 1; $index++) {
        $exactLine = [string]$lines[$index]
        $jsonLine = $exactLine
        if ($jsonLine.EndsWith([string][char]13)) {
            $jsonLine = $jsonLine.Substring(0, $jsonLine.Length - 1)
        }
        if ([string]::IsNullOrWhiteSpace($jsonLine)) {
            throw "$Label has a blank or whitespace-only row at line $($index + 1)"
        }
        try { $eventObject = $jsonLine | ConvertFrom-Json -ErrorAction Stop }
        catch {
            throw "$Label has malformed JSON at line $($index + 1): $($_.Exception.Message)"
        }
        Assert-BridgeEventObjectShape `
            -Object $eventObject -Label "$Label row $($index + 1)"
        [byte[]]$rowBytes = $strictUtf8.GetBytes($exactLine + [char]10)
        [void]$Keys.Add((Get-BridgeSha256Hex -Bytes $rowBytes))
    }
}

function Read-BridgeExactStreamRange {
    param(
        [Parameter(Mandatory)] [System.IO.FileStream] $Stream,
        [Parameter(Mandatory)] [int64] $Offset,
        [Parameter(Mandatory)] [int64] $Length,
        [Parameter(Mandatory)] [string] $Label
    )

    if ($Offset -lt 0 -or $Length -lt 0) {
        throw "$Label has a negative stream range"
    }
    if ($Length -gt [int]::MaxValue) {
        throw "$Label exceeds the supported in-memory validation size"
    }
    [byte[]]$bytes = New-Object byte[] ([int]$Length)
    [void]$Stream.Seek($Offset, [System.IO.SeekOrigin]::Begin)
    $readOffset = 0
    while ($readOffset -lt $bytes.Length) {
        $read = $Stream.Read($bytes, $readOffset, $bytes.Length - $readOffset)
        if ($read -le 0) {
            throw "$Label ended before its captured length"
        }
        $readOffset += $read
    }
    return ,$bytes
}

function Get-BridgeCanonicalTailAnchor {
    param(
        [Parameter(Mandatory)] [System.IO.FileStream] $Stream,
        [Parameter(Mandatory)] [int64] $Length
    )

    if ($Length -lt 0 -or $Length -gt [int64]$Stream.Length) {
        throw 'cannot anchor an inexact canonical bridge prefix'
    }
    $anchorLength = [int][Math]::Min([int64]4096, $Length)
    [byte[]]$anchorBytes = Read-BridgeExactStreamRange `
        -Stream $Stream -Offset ($Length - $anchorLength) `
        -Length $anchorLength -Label 'canonical bridge tail anchor'
    return [pscustomobject]@{
        Length = $anchorLength
        Sha256 = Get-BridgeSha256Hex -Bytes $anchorBytes
    }
}

function Open-BridgeCanonicalSnapshot {
    param([Parameter(Mandatory)] [string] $Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return [pscustomobject]@{
            Exists = $false
            Stream = $null
            Length = [int64]0
            FileIdentity = ''
            TailAnchorLength = 0
            TailAnchorSha256 = Get-BridgeSha256Hex -Bytes ([byte[]]@())
        }
    }
    $stream = $null
    try {
        # Keep this exact file object open without delete sharing while the
        # expensive JSONL scan runs. FileShare.ReadWrite permits cooperating
        # AppendV1 writers to append; the second mutex phase reconciles them.
        $stream = New-Object System.IO.FileStream(
            $Path,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::ReadWrite
        )
        $length = [int64]$stream.Length
        if ($length -gt 0) {
            [byte[]]$tail = Read-BridgeExactStreamRange `
                -Stream $stream -Offset ($length - 1) -Length 1 `
                -Label 'canonical bridge tail'
            if ($tail[0] -ne 10) {
                throw 'canonical bridge snapshot has an unterminated tail'
            }
        }
        $anchor = Get-BridgeCanonicalTailAnchor -Stream $stream -Length $length
        $snapshot = [pscustomobject]@{
            Exists = $true
            Stream = $stream
            Length = $length
            FileIdentity = Get-BridgeOpenFileIdentity -Stream $stream
            TailAnchorLength = [int]$anchor.Length
            TailAnchorSha256 = [string]$anchor.Sha256
        }
        $stream = $null
        return $snapshot
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
    }
}

function Invoke-BridgeCanonicalScanTestHook {
    $readyPath = [Environment]::GetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_CANONICAL_SCAN_READY',
        'Process'
    )
    if (-not $readyPath) { return }
    [System.IO.File]::WriteAllText($readyPath, 'ready')
    for ($attempt = 0; $attempt -lt 400; $attempt++) {
        if (Test-Path -LiteralPath "$readyPath.release" -PathType Leaf) { return }
        Start-Sleep -Milliseconds 25
    }
    throw 'canonical scan test hook timed out waiting for release'
}

function Get-BridgeCanonicalKeys {
    param([Parameter(Mandatory)] $Snapshot)

    $keys = New-Object 'System.Collections.Generic.HashSet[string]'
    if (-not [bool]$Snapshot.Exists) { return ,$keys }
    [byte[]]$bytes = Read-BridgeExactStreamRange `
        -Stream $Snapshot.Stream -Offset 0 -Length ([int64]$Snapshot.Length) `
        -Label 'canonical bridge snapshot'
    Add-BridgeCanonicalKeysFromBytes `
        -Bytes $bytes -Label 'canonical bridge log' -Keys $keys
    return ,$keys
}

function Add-BridgeCanonicalKeysSinceSnapshot {
    param(
        [Parameter(Mandatory)] $Snapshot,
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [AllowEmptyCollection()]
        [System.Collections.Generic.HashSet[string]] $Keys
    )

    if (-not [bool]$Snapshot.Exists) {
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return }
        $lateSnapshot = Open-BridgeCanonicalSnapshot -Path $Path
        try {
            [byte[]]$lateBytes = Read-BridgeExactStreamRange `
                -Stream $lateSnapshot.Stream -Offset 0 `
                -Length ([int64]$lateSnapshot.Length) `
                -Label 'late-created canonical bridge log'
            Add-BridgeCanonicalKeysFromBytes `
                -Bytes $lateBytes -Label 'late-created canonical bridge log' `
                -Keys $Keys
        } finally {
            if ($null -ne $lateSnapshot.Stream) { $lateSnapshot.Stream.Dispose() }
        }
        return
    }

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw 'canonical bridge path disappeared during snapshot scan'
    }
    $pathStream = $null
    try {
        $pathStream = New-Object System.IO.FileStream(
            $Path,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::ReadWrite
        )
        if (
            [string]$Snapshot.FileIdentity -and
            (Get-BridgeOpenFileIdentity -Stream $pathStream) -cne
                [string]$Snapshot.FileIdentity
        ) {
            throw 'canonical bridge file identity changed during snapshot scan'
        }
    } finally {
        if ($null -ne $pathStream) { $pathStream.Dispose() }
    }

    $currentLength = [int64]$Snapshot.Stream.Length
    if ($currentLength -lt [int64]$Snapshot.Length) {
        throw 'canonical bridge log shrank during snapshot scan'
    }
    $actualAnchor = Get-BridgeCanonicalTailAnchor `
        -Stream $Snapshot.Stream -Length ([int64]$Snapshot.Length)
    if (
        [int]$actualAnchor.Length -ne [int]$Snapshot.TailAnchorLength -or
        [string]$actualAnchor.Sha256 -cne [string]$Snapshot.TailAnchorSha256
    ) {
        throw 'canonical bridge snapshot tail anchor changed before reconciliation'
    }

    $deltaLength = $currentLength - [int64]$Snapshot.Length
    if ($deltaLength -eq 0) { return }
    [byte[]]$deltaBytes = Read-BridgeExactStreamRange `
        -Stream $Snapshot.Stream -Offset ([int64]$Snapshot.Length) `
        -Length $deltaLength -Label 'canonical bridge append delta'
    Add-BridgeCanonicalKeysFromBytes `
        -Bytes $deltaBytes -Label 'canonical bridge append delta' -Keys $Keys
}

function Repair-BridgeTornTailIfBound {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [AllowEmptyCollection()] [object[]] $WalRecords
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return '' }
    $tailProbe = $null
    try {
        $tailProbe = New-Object System.IO.FileStream(
            $Path,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::Read
        )
        if ($tailProbe.Length -eq 0) { return '' }
        [byte[]]$lastByte = Read-BridgeExactStreamRange `
            -Stream $tailProbe -Offset ([int64]$tailProbe.Length - 1) `
            -Length 1 -Label 'canonical bridge tail probe'
        if ($lastByte[0] -eq 10) { return '' }
    } finally {
        if ($null -ne $tailProbe) { $tailProbe.Dispose() }
    }
    [byte[]]$canonicalBytes = [System.IO.File]::ReadAllBytes($Path)
    if ($canonicalBytes.Length -eq 0 -or $canonicalBytes[$canonicalBytes.Length - 1] -eq 10) {
        return ''
    }
    $lastLf = -1
    for ($index = $canonicalBytes.Length - 1; $index -ge 0; $index--) {
        if ($canonicalBytes[$index] -eq 10) { $lastLf = $index; break }
    }
    $tailStart = $lastLf + 1
    $tailLength = $canonicalBytes.Length - $tailStart
    $tailBytes = New-Object byte[] $tailLength
    [Array]::Copy($canonicalBytes, $tailStart, $tailBytes, 0, $tailLength)

    # A bound torn tail is recoverable only when every already-terminated row
    # is independently valid. Never hide an interior corruption by repairing
    # a later tail.
    if ($tailStart -gt 0) {
        $prefixBytes = New-Object byte[] $tailStart
        [Array]::Copy($canonicalBytes, 0, $prefixBytes, 0, $tailStart)
        $prefixKeys = New-Object 'System.Collections.Generic.HashSet[string]'
        Add-BridgeCanonicalKeysFromBytes `
            -Bytes $prefixBytes -Label 'canonical bridge log prefix' `
            -Keys $prefixKeys
    }

    $bound = $false
    foreach ($wal in $WalRecords) {
        foreach ($row in $wal.Rows) {
            if (Test-BridgeBytesStartWith -Bytes $row.Bytes -Prefix $tailBytes) {
                $bound = $true
                break
            }
        }
        if ($bound) { break }
    }
    if (-not $bound) {
        throw 'canonical bridge log has an unbound unterminated tail; no changes made'
    }
    if ($DryRun) {
        throw 'dry run found a WAL-bound torn tail; no repair was performed'
    }
    $quarantineDir = Join-Path $spoolDir 'quarantine'
    if (-not (Test-Path -LiteralPath $quarantineDir -PathType Container)) {
        [void](New-Item -ItemType Directory -Path $quarantineDir -Force)
    }
    $quarantinePath = Join-Path $quarantineDir (
        'canonical-torn-tail-{0}-{1}-{2}.bin' -f
        [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfff'),
        $PID,
        [guid]::NewGuid().ToString('N')
    )
    Write-NewBridgeFileDurably -Path $quarantinePath -Bytes $tailBytes
    $stream = $null
    try {
        $stream = New-Object System.IO.FileStream(
            $Path,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::Read
        )
        if ([int64]$stream.Length -ne [int64]$canonicalBytes.Length) {
            throw 'canonical bridge log changed before torn-tail truncation'
        }
        Invalidate-BridgeAppendValidationCheckpoint `
            -CanonicalPath $Path -Reason 'wal-bound-torn-tail-truncate'
        $stream.SetLength([int64]$tailStart)
        $stream.Flush($true)
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
    }
    Write-Warning "repaired WAL-bound torn canonical tail; quarantine retained: $quarantinePath"
    return $quarantinePath
}

function Clear-BridgeHiddenAttribute {
    param([Parameter(Mandatory)] [string] $Path)
    if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
        $attributes = [System.IO.File]::GetAttributes($Path)
        [System.IO.File]::SetAttributes(
            $Path,
            ($attributes -band (-bnot [System.IO.FileAttributes]::Hidden))
        )
    }
}

$appendMutex = $null
$appendAcquired = $false
$appendDirtyAbandoned = $false
$canonicalSnapshot = $null
try {
    try {
        $appendMutex = New-BridgeV1Mutex `
            -Name 'Global\WaggleDanceBridgeAppendV1' -Purpose Append
        if ($null -eq $appendMutex) {
            throw 'bridge append mutex construction returned null'
        }
        try { $appendAcquired = $appendMutex.WaitOne(10000) }
        catch [System.Threading.AbandonedMutexException] {
            $appendAcquired = $true
            $appendDirtyAbandoned = $true
        }
    } catch {
        throw "could not acquire bridge append mutex: $($_.Exception.Message)"
    }
    if (-not $appendAcquired) {
        Write-Warning 'bridge append mutex timeout; all spool files kept'
        Write-Output 'spool replay skipped: append mutex unavailable; no changes'
        return
    }
    if ($appendDirtyAbandoned) {
        Write-Warning 'AppendV1 was abandoned; dirty ownership cannot replay or mutate canonical bytes'
        Write-Output 'spool replay skipped: dirty abandoned AppendV1 ownership; no changes'
        return
    }

    # Initial AppendV1 ownership covers WAL discovery/recovery and canonical
    # snapshot capture. The stable snapshot is scanned after releasing the
    # append fence so a large log cannot starve live writers.
    $pendingFiles = @(
        Get-ChildItem -LiteralPath $spoolDir `
            -Filter '.failed-append-*.jsonl.pending' -File -Force `
            -ErrorAction Stop |
            Sort-Object Name
    )
    $finalFiles = @(
        Get-ChildItem -LiteralPath $spoolDir `
            -Filter 'failed-append-*.jsonl' -File -Force `
            -ErrorAction Stop |
            Sort-Object Name
    )
    if ($pendingFiles.Count -eq 0 -and $finalFiles.Count -eq 0) {
        Write-Output 'spool empty; nothing to replay'
        return
    }

    $finalRecords = New-Object 'System.Collections.Generic.List[object]'
    $finalByPath = @{}
    foreach ($file in $finalFiles) {
        $record = Read-BridgeWalFile -File $file
        $finalRecords.Add($record)
        $finalByPath[$file.FullName.ToUpperInvariant()] = $record
    }

    $pendingPlans = New-Object 'System.Collections.Generic.List[object]'
    $bindingRecords = New-Object 'System.Collections.Generic.List[object]'
    foreach ($record in $finalRecords) { $bindingRecords.Add($record) }
    foreach ($pendingFile in $pendingFiles) {
        try {
            $pendingRecord = Read-BridgeWalFile -File $pendingFile
        } catch {
            if (Test-BridgeSharingViolation -Exception $_.Exception) {
                Write-Output "active pending WAL lease skipped: $($pendingFile.Name)"
                continue
            }
            throw
        }
        $bindingRecords.Add($pendingRecord)
        if (-not $pendingFile.Name.EndsWith('.pending')) {
            throw "pending WAL name is malformed: $($pendingFile.Name)"
        }
        $finalName = $pendingFile.Name.Substring(
            1,
            $pendingFile.Name.Length - 1 - '.pending'.Length
        )
        $finalPath = Join-Path $spoolDir $finalName
        if (Test-Path -LiteralPath $finalPath -PathType Leaf) {
            $key = ([System.IO.Path]::GetFullPath($finalPath)).ToUpperInvariant()
            $finalRecord = $finalByPath[$key]
            if ($null -eq $finalRecord) {
                $finalRecord = Read-BridgeWalFile -File (Get-Item -LiteralPath $finalPath -Force)
            }
            if (-not (Test-BridgeBytesEqual `
                -Left $pendingRecord.Bytes -Right $finalRecord.Bytes)) {
                throw "pending WAL collides with different final spool: $($pendingFile.Name)"
            }
            $pendingPlans.Add([pscustomobject]@{
                Action = 'ExactCollision'
                Record = $pendingRecord
                FinalPath = $finalPath
            })
        } else {
            $pendingPlans.Add([pscustomobject]@{
                Action = 'Promote'
                Record = $pendingRecord
                FinalPath = $finalPath
            })
        }
    }

    [void](Repair-BridgeTornTailIfBound `
        -Path $eventsPath -WalRecords $bindingRecords.ToArray())

    $canonicalSnapshot = Open-BridgeCanonicalSnapshot -Path $eventsPath
    $appendMutex.ReleaseMutex()
    $appendAcquired = $false

    # Validate the captured prefix without AppendV1 ownership. The open
    # no-delete/shared-write handle fixes file identity while allowing live
    # writers to append. Strict decoding failures leave every spool unchanged.
    Invoke-BridgeCanonicalScanTestHook
    $existingKeys = Get-BridgeCanonicalKeys -Snapshot $canonicalSnapshot

    # Re-enter AppendV1 before observing the post-snapshot tail or changing any
    # canonical/spool path. Writers that completed during the scan are folded
    # into exact-record dedupe from the append-only delta.
    $appendDirtyAbandoned = $false
    try {
        try { $appendAcquired = $appendMutex.WaitOne(10000) }
        catch [System.Threading.AbandonedMutexException] {
            $appendAcquired = $true
            $appendDirtyAbandoned = $true
        }
    } catch {
        throw "could not reacquire bridge append mutex: $($_.Exception.Message)"
    }
    if (-not $appendAcquired) {
        Write-Warning 'bridge append mutex timeout after canonical scan; all spool files kept'
        Write-Output 'spool replay skipped: append mutex unavailable after scan; no spool changes'
        return
    }
    if ($appendDirtyAbandoned) {
        Write-Warning 'AppendV1 was abandoned after canonical scan; dirty ownership cannot replay'
        Write-Output 'spool replay skipped: dirty abandoned AppendV1 ownership after scan; no spool changes'
        return
    }
    Add-BridgeCanonicalKeysSinceSnapshot `
        -Snapshot $canonicalSnapshot -Path $eventsPath -Keys $existingKeys

    $recordsToProcess = New-Object 'System.Collections.Generic.List[object]'
    foreach ($record in $finalRecords) { $recordsToProcess.Add($record) }
    foreach ($plan in $pendingPlans) {
        $pendingRecord = $plan.Record
        if ($plan.Action -eq 'ExactCollision') {
            if ($DryRun) {
                Write-Output "would archive exact pending/final collision: $($pendingRecord.File.Name)"
            } else {
                if (-not (Test-Path -LiteralPath $archiveDir -PathType Container)) {
                    [void](New-Item -ItemType Directory -Path $archiveDir -Force)
                }
                $collisionPath = Join-Path $archiveDir (
                    '{0}.exact-duplicate.{1}' -f
                    $pendingRecord.File.Name,
                    [guid]::NewGuid().ToString('N')
                )
                Clear-BridgeHiddenAttribute -Path $pendingRecord.File.FullName
                [System.IO.File]::Move($pendingRecord.File.FullName, $collisionPath)
            }
            continue
        }
        if ($DryRun) {
            Write-Output "would promote pending WAL: $($pendingRecord.File.Name)"
        } else {
            Clear-BridgeHiddenAttribute -Path $pendingRecord.File.FullName
            [System.IO.File]::Move($pendingRecord.File.FullName, $plan.FinalPath)
            $pendingRecord.File = Get-Item -LiteralPath $plan.FinalPath -Force
        }
        $recordsToProcess.Add($pendingRecord)
    }

    $replayed = 0
    $failed = 0
    $deduped = 0
    foreach ($wal in $recordsToProcess) {
        $rowsToAppend = New-Object 'System.Collections.Generic.List[object]'
        $walNewKeys = New-Object 'System.Collections.Generic.HashSet[string]'
        foreach ($row in $wal.Rows) {
            if (
                $existingKeys.Contains([string]$row.Key) -or
                -not $walNewKeys.Add([string]$row.Key)
            ) {
                $deduped++
            } else {
                $rowsToAppend.Add($row)
            }
        }
        if ($rowsToAppend.Count -eq 0) {
            if ($DryRun) {
                Write-Output "would archive as exact duplicate: $($wal.File.Name)"
            } else {
                [void](Move-SpoolToArchive -File $wal.File -ArchiveDir $archiveDir)
            }
            continue
        }
        if ($DryRun) {
            Write-Output "would replay: $($wal.File.Name)"
            $replayed++
            continue
        }
        [byte[]]$appendBytes = Join-BridgeWalRowBytes -Rows $rowsToAppend.ToArray()
        try {
            [void](Invoke-BridgeTransactionalAppend `
                -Path $eventsPath -Bytes $appendBytes `
                -AppendMutexOwned $appendAcquired)
        } catch {
            if ($_.Exception.Message -match 'ROLLBACK FAILED') { throw }
            Write-Warning "append failed and WAL was kept: $($wal.File.Name) ($($_.Exception.Message))"
            $failed++
            continue
        }
        foreach ($row in $rowsToAppend) {
            [void]$existingKeys.Add([string]$row.Key)
        }
        [void](Move-SpoolToArchive -File $wal.File -ArchiveDir $archiveDir)
        $replayed++
    }

    Write-Output (
        'spool replay complete: replayed={0} deduped={1} failed={2} dryRun={3}' -f
        $replayed, $deduped, $failed, $DryRun.IsPresent
    )
} finally {
    if ($null -ne $canonicalSnapshot -and $null -ne $canonicalSnapshot.Stream) {
        $canonicalSnapshot.Stream.Dispose()
    }
    if ($null -ne $appendMutex) {
        if ($appendAcquired) {
            try { $appendMutex.ReleaseMutex() }
            catch {
                Write-Warning -WarningAction Continue -Message (
                    "AppendV1 release failed: $($_.Exception.Message)"
                )
            }
        }
        $appendMutex.Dispose()
    }
    if ($null -ne $replayMutex) {
        if ($replayAcquired) {
            try { $replayMutex.ReleaseMutex() }
            catch {
                Write-Warning -WarningAction Continue -Message (
                    "SpoolReplayV1 release failed: $($_.Exception.Message)"
                )
            }
        }
        $replayMutex.Dispose()
    }
}
