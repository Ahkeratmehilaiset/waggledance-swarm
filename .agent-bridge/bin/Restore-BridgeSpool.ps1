#requires -Version 5.1
<#
.SYNOPSIS
    Replay one accepted-v1 WAL, or explicitly replay the legacy spool backlog.

.DESCRIPTION
    Accepted-target mode replays exactly one caller-named WAL from
    spool/accepted-v1/ready/, verifies its caller-supplied SHA-256, and archives
    it under spool/accepted-v1/replayed/. It never enumerates the legacy spool
    root. Legacy bulk replay remains available only with -LegacyBulk.

    Order note: replayed events append at the CURRENT tail, so their ts_utc
    is older than surrounding events. Readers in this repo select by
    task_id/latest-signal, not by file position alone; the original
    timestamp inside the event is preserved.

.PARAMETER BridgeRoot
    Bridge runtime root. Defaults to AGENT_BRIDGE_RUNTIME_ROOT or the
    parent of this script's directory.

.PARAMETER LegacyBulk
    Explicitly enumerate and replay the historical failed-append backlog.

.PARAMETER AcceptedWalLeaf
    Exact accepted-v1 ready leaf. Must match
    bridge-wal-v1-<32 lowercase hex>.jsonl.

.PARAMETER ExpectedWalSha256
    Lowercase SHA-256 of the exact accepted WAL bytes.

.PARAMETER DryRun
    List what would be replayed without appending or archiving.
#>
[CmdletBinding(DefaultParameterSetName = 'NoMode')]
param(
    [Parameter(ParameterSetName = 'LegacyBulk')]
    [switch] $LegacyBulk,
    [Parameter(ParameterSetName = 'AcceptedTarget')]
    [string] $AcceptedWalLeaf = '',
    [Parameter(ParameterSetName = 'AcceptedTarget')]
    [string] $ExpectedWalSha256 = '',
    [string] $BridgeRoot = '',
    [switch] $DryRun
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$targetedReplay = $PSCmdlet.ParameterSetName -ceq 'AcceptedTarget'
if ($PSCmdlet.ParameterSetName -ceq 'NoMode') {
    throw 'replay mode is required: use -AcceptedWalLeaf with -ExpectedWalSha256, or -LegacyBulk'
}
if ($PSCmdlet.ParameterSetName -ceq 'LegacyBulk' -and -not $LegacyBulk.IsPresent) {
    throw 'legacy bulk replay requires explicit -LegacyBulk'
}
if ($targetedReplay) {
    if (
        $AcceptedWalLeaf -cnotmatch '^bridge-wal-v1-[0-9a-f]{32}\.jsonl$'
    ) {
        throw 'accepted WAL leaf must match bridge-wal-v1-<32 lowercase hex>.jsonl'
    }
    if ($ExpectedWalSha256 -cnotmatch '^[0-9a-f]{64}$') {
        throw 'expected accepted WAL SHA-256 must be 64 lowercase hexadecimal characters'
    }
}

if (-not $BridgeRoot) {
    $BridgeRoot = if ($env:AGENT_BRIDGE_RUNTIME_ROOT) {
        [string]$env:AGENT_BRIDGE_RUNTIME_ROOT
    } else {
        Split-Path -Parent $PSScriptRoot
    }
}

$spoolDir   = Join-Path $BridgeRoot 'spool'
$eventsPath = Join-Path (Join-Path $BridgeRoot 'shared') 'events.jsonl'
$acceptedDir = Join-Path $spoolDir 'accepted-v1'
$acceptedReadyDir = Join-Path $acceptedDir 'ready'
$archiveDir = if ($targetedReplay) {
    Join-Path $acceptedDir 'replayed'
} else {
    Join-Path $spoolDir 'replayed'
}
$quarantineDir = if ($targetedReplay) {
    Join-Path $acceptedDir 'quarantine'
} else {
    Join-Path $spoolDir 'quarantine'
}
$knownEventTypes = @(
    'status', 'intent', 'claim', 'release', 'message', 'finding',
    'decision', 'test', 'blocked', 'handoff', 'done', 'heartbeat',
    'wake_request', 'liveness'
)

function Assert-BridgeTargetedPlainPath {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $Label,
        [switch] $IfExists,
        [switch] $Directory
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        if ($IfExists) { return }
        throw "$Label does not exist"
    }
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "$Label must not be a reparse point"
    }
    if ($Directory -and -not $item.PSIsContainer) {
        throw "$Label must be a directory"
    }
    if (-not $Directory -and $item.PSIsContainer) {
        throw "$Label must be a file"
    }
}

if (-not (Test-Path -LiteralPath $spoolDir -PathType Container)) {
    if ($targetedReplay) { throw 'accepted-v1 spool directory does not exist' }
    Write-Output 'no spool directory; nothing to replay'
    return
}
if ($targetedReplay) {
    Assert-BridgeTargetedPlainPath `
        -Path $BridgeRoot -Label 'targeted bridge root' -Directory
    Assert-BridgeTargetedPlainPath `
        -Path $spoolDir -Label 'targeted spool directory' -Directory
    Assert-BridgeTargetedPlainPath `
        -Path $acceptedDir -Label 'accepted-v1 directory' -Directory
    Assert-BridgeTargetedPlainPath `
        -Path $acceptedReadyDir -Label 'accepted-v1 ready directory' -Directory
    Assert-BridgeTargetedPlainPath `
        -Path $archiveDir -Label 'accepted-v1 replayed directory' `
        -IfExists -Directory
    Assert-BridgeTargetedPlainPath `
        -Path $quarantineDir -Label 'accepted-v1 quarantine directory' `
        -IfExists -Directory
    # The shared entry may be the documented per-worktree junction. Its entry,
    # followed target, and canonical child are handle-bound later under AppendV1.
    Assert-BridgeTargetedPlainPath `
        -Path $eventsPath -Label 'targeted canonical log' -IfExists
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

function Close-BridgeQueuePublicationFence {
    param(
        [AllowNull()] $Mutex,
        [Parameter(Mandatory)] [bool] $Acquired
    )

    if ($null -eq $Mutex) { return }
    if ($Acquired) {
        try { $Mutex.ReleaseMutex() }
        catch {
            Write-Warning -WarningAction Continue -Message (
                'accepted queue publication fence release failed: ' +
                $_.Exception.Message
            )
        }
    }
    $Mutex.Dispose()
}

$publicationMutex = $null
$publicationAcquired = $false
$publicationDirtyAbandoned = $false
if ($targetedReplay -and -not $DryRun) {
    try {
        $publicationMutex = New-BridgeV1Mutex `
            -Name 'Global\WaggleDanceBridgeAcceptedQueuePublicationV1' `
            -Purpose QueuePublication
        if ($null -eq $publicationMutex) {
            throw 'accepted queue publication mutex construction returned null'
        }
        try { $publicationAcquired = $publicationMutex.WaitOne(10000) }
        catch [System.Threading.AbandonedMutexException] {
            $publicationAcquired = $true
            $publicationDirtyAbandoned = $true
        }
    } catch {
        if ($null -ne $publicationMutex) { $publicationMutex.Dispose() }
        throw "could not acquire accepted queue publication fence: $($_.Exception.Message)"
    }
    if (-not $publicationAcquired -or $publicationDirtyAbandoned) {
        Close-BridgeQueuePublicationFence `
            -Mutex $publicationMutex -Acquired $publicationAcquired
        $publicationMutex = $null
        if ($publicationDirtyAbandoned) {
            throw 'accepted-target replay deferred: queue publication ownership was abandoned'
        }
        throw 'accepted-target replay deferred: queue publication fence is busy'
    }
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
    Close-BridgeQueuePublicationFence `
        -Mutex $publicationMutex -Acquired $publicationAcquired
    $publicationMutex = $null
    throw "could not acquire bridge spool replay mutex: $($_.Exception.Message)"
}
if (-not $replayAcquired) {
    if ($null -ne $replayMutex) { $replayMutex.Dispose() }
    Close-BridgeQueuePublicationFence `
        -Mutex $publicationMutex -Acquired $publicationAcquired
    $publicationMutex = $null
    if ($targetedReplay) {
        throw 'accepted-target replay deferred: spool replay mutex is busy'
    }
    Write-Output 'spool replay already running; exiting without changes'
    return
}

function Invoke-BridgeTransactionalAppend {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [byte[]] $Bytes,
        [Parameter(Mandatory)] [bool] $AppendMutexOwned,
        [switch] $RequirePlainCanonical,
        [AllowNull()] $TargetedCanonicalLease
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
        if ($RequirePlainCanonical) {
            $stream = Open-BridgeTargetedCanonicalMutationStream `
                -Path $Path -ExpectedLease $TargetedCanonicalLease -Create
        } else {
            $stream = New-Object System.IO.FileStream(
                $Path,
                [System.IO.FileMode]::OpenOrCreate,
                [System.IO.FileAccess]::ReadWrite,
                [System.IO.FileShare]::Read
            )
        }
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
            if ($RequirePlainCanonical) {
                Assert-BridgeTargetedCanonicalMutationStream `
                    -Stream $stream -ExpectedLease $TargetedCanonicalLease
            }
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
using Microsoft.Win32.SafeHandles;

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
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern SafeFileHandle CreateFileW(
            string fileName,
            uint desiredAccess,
            uint shareMode,
            IntPtr securityAttributes,
            uint creationDisposition,
            uint flagsAndAttributes,
            IntPtr templateFile
        );

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

function Initialize-BridgeTargetedReplayNative {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'accepted-target replay requires Windows handle-bound filesystem operations'
    }
    if ('WaggleDance.BridgeTargetedReplayNative' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

namespace WaggleDance {
    [StructLayout(LayoutKind.Sequential)]
    public struct BridgeTargetedByHandleFileInformation {
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

    [StructLayout(LayoutKind.Sequential)]
    public struct BridgeTargetedFileDispositionInformation {
        [MarshalAs(UnmanagedType.Bool)]
        public bool DeleteFile;
    }

    public static class BridgeTargetedReplayNative {
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern SafeFileHandle CreateFileW(
            string fileName,
            uint desiredAccess,
            uint shareMode,
            IntPtr securityAttributes,
            uint creationDisposition,
            uint flagsAndAttributes,
            IntPtr templateFile
        );

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool GetFileInformationByHandle(
            SafeFileHandle fileHandle,
            out BridgeTargetedByHandleFileInformation fileInformation
        );

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool SetFileInformationByHandle(
            SafeFileHandle fileHandle,
            int fileInformationClass,
            ref BridgeTargetedFileDispositionInformation fileInformation,
            uint bufferSize
        );

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern uint GetFinalPathNameByHandleW(
            SafeFileHandle fileHandle,
            StringBuilder filePath,
            uint filePathSize,
            uint flags
        );
    }
}
'@
}

function Get-BridgeTargetedHandleInformation {
    param(
        [Parameter(Mandatory)]
        [Microsoft.Win32.SafeHandles.SafeFileHandle] $Handle
    )

    $information = New-Object WaggleDance.BridgeTargetedByHandleFileInformation
    if (-not [WaggleDance.BridgeTargetedReplayNative]::GetFileInformationByHandle(
        $Handle,
        [ref]$information
    )) {
        $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        throw "accepted-target identity query failed with Win32 error $nativeCode"
    }
    return $information
}

function Get-BridgeTargetedFinalPath {
    param(
        [Parameter(Mandatory)]
        [Microsoft.Win32.SafeHandles.SafeFileHandle] $Handle
    )

    $capacity = 32768
    $builder = New-Object System.Text.StringBuilder($capacity)
    $length = [WaggleDance.BridgeTargetedReplayNative]::GetFinalPathNameByHandleW(
        $Handle,
        $builder,
        [uint32]$builder.Capacity,
        [uint32]0
    )
    if ($length -eq 0 -or $length -ge [uint32]$builder.Capacity) {
        $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        throw "accepted-target final-path query failed with Win32 error $nativeCode"
    }
    $resolved = $builder.ToString()
    if ($resolved.StartsWith('\\?\UNC\', [StringComparison]::OrdinalIgnoreCase)) {
        return '\\' + $resolved.Substring(8)
    }
    if ($resolved.StartsWith('\\?\', [StringComparison]::OrdinalIgnoreCase)) {
        return $resolved.Substring(4)
    }
    return $resolved
}

function Read-BridgeTargetedLeaseBytes {
    param([Parameter(Mandatory)] [System.IO.FileStream] $Stream)

    if ($Stream.Length -gt [int]::MaxValue) {
        throw 'accepted WAL exceeds the supported byte length'
    }
    [void]$Stream.Seek(0, [System.IO.SeekOrigin]::Begin)
    [byte[]]$bytes = New-Object byte[] ([int]$Stream.Length)
    $offset = 0
    while ($offset -lt $bytes.Length) {
        $read = $Stream.Read($bytes, $offset, $bytes.Length - $offset)
        if ($read -le 0) { throw 'accepted WAL ended during read' }
        $offset += $read
    }
    return ,$bytes
}

function Open-BridgeTargetedDirectoryLease {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $Label
    )

    Initialize-BridgeTargetedReplayNative
    $handle = [WaggleDance.BridgeTargetedReplayNative]::CreateFileW(
        $Path,
        [uint32]2147483648,
        [uint32]3,
        [IntPtr]::Zero,
        [uint32]3,
        [uint32]35651584,
        [IntPtr]::Zero
    )
    if ($handle.IsInvalid) {
        $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        $handle.Dispose()
        throw "$Label handle open failed with Win32 error $nativeCode"
    }
    try {
        $information = Get-BridgeTargetedHandleInformation -Handle $handle
        if (
            ($information.FileAttributes -band
                [uint32][System.IO.FileAttributes]::Directory) -eq 0
        ) {
            throw "$Label must be a directory"
        }
        if (
            ($information.FileAttributes -band
                [uint32][System.IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            throw "$Label must not be a reparse point"
        }
        $lease = [pscustomobject]@{
            Path = [System.IO.Path]::GetFullPath($Path)
            Handle = $handle
        }
        $handle = $null
        return $lease
    } finally {
        if ($null -ne $handle) { $handle.Dispose() }
    }
}

function Add-BridgeTargetedDirectoryChainLeases {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] $Leases
    )

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $pathRoot = [System.IO.Path]::GetPathRoot($fullPath)
    $cursor = $pathRoot
    $Leases.Add((Open-BridgeTargetedDirectoryLease `
        -Path $cursor -Label "targeted bridge path component $cursor"))
    $relativePath = $fullPath.Substring($pathRoot.Length)
    foreach ($segment in @(
        $relativePath -split '[\\/]' | Where-Object { $_ }
    )) {
        $cursor = Join-Path $cursor $segment
        $Leases.Add((Open-BridgeTargetedDirectoryLease `
            -Path $cursor -Label "targeted bridge path component $cursor"))
    }
}

function Open-BridgeTargetedSharedDirectoryLeaseSet {
    param([Parameter(Mandatory)] [string] $Path)

    Initialize-BridgeTargetedReplayNative
    $entryHandle = $null
    $targetHandle = $null
    $entryLease = $null
    $targetLease = $null
    try {
        # Pin the configured shared entry itself without permitting reparse
        # retargeting or rename/delete while targeted replay is active.
        $entryHandle = [WaggleDance.BridgeTargetedReplayNative]::CreateFileW(
            $Path,
            [uint32]2147483648,
            [uint32]1,
            [IntPtr]::Zero,
            [uint32]3,
            [uint32]35651584,
            [IntPtr]::Zero
        )
        if ($entryHandle.IsInvalid) {
            $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            throw "targeted shared entry open failed with Win32 error $nativeCode"
        }
        $entryInformation = Get-BridgeTargetedHandleInformation -Handle $entryHandle
        if (
            ($entryInformation.FileAttributes -band
                [uint32][System.IO.FileAttributes]::Directory) -eq 0
        ) {
            throw 'targeted shared entry must be a directory'
        }
        if (
            ($entryInformation.FileAttributes -band
                [uint32][System.IO.FileAttributes]::ReparsePoint) -eq 0
        ) {
            $entryHandle.Dispose()
            $entryHandle = $null
            $entryLease = Open-BridgeTargetedDirectoryLease `
                -Path $Path -Label 'targeted shared directory'
            return [pscustomobject]@{
                Leases = @($entryLease)
                ResolvedPath = Get-BridgeTargetedFinalPath -Handle $entryLease.Handle
            }
        }
        $entryLease = [pscustomobject]@{
            Path = [System.IO.Path]::GetFullPath($Path)
            Handle = $entryHandle
            Kind = 'shared-entry'
        }
        $entryHandle = $null

        # The documented per-worktree junction layout is safe only while both
        # the junction entry and its followed target are pinned. The canonical
        # child lease is later required to resolve directly under this target.
        $targetHandle = [WaggleDance.BridgeTargetedReplayNative]::CreateFileW(
            $Path,
            [uint32]2147483648,
            [uint32]3,
            [IntPtr]::Zero,
            [uint32]3,
            [uint32]33554432,
            [IntPtr]::Zero
        )
        if ($targetHandle.IsInvalid) {
            $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            throw "targeted shared junction target open failed with Win32 error $nativeCode"
        }
        $targetInformation = Get-BridgeTargetedHandleInformation -Handle $targetHandle
        if (
            ($targetInformation.FileAttributes -band
                [uint32][System.IO.FileAttributes]::Directory) -eq 0 -or
            ($targetInformation.FileAttributes -band
                [uint32][System.IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            throw 'targeted shared junction must resolve to a plain directory'
        }
        $targetLease = [pscustomobject]@{
            Path = [System.IO.Path]::GetFullPath($Path)
            Handle = $targetHandle
            Kind = 'shared-target'
        }
        $targetHandle = $null
        return [pscustomobject]@{
            Leases = @($entryLease, $targetLease)
            ResolvedPath = Get-BridgeTargetedFinalPath -Handle $targetLease.Handle
        }
    } catch {
        if ($null -ne $targetLease -and $null -ne $targetLease.Handle) {
            $targetLease.Handle.Dispose()
        }
        if ($null -ne $entryLease -and $null -ne $entryLease.Handle) {
            $entryLease.Handle.Dispose()
        }
        throw
    } finally {
        if ($null -ne $entryHandle) { $entryHandle.Dispose() }
        if ($null -ne $targetHandle) { $targetHandle.Dispose() }
    }
}

function Open-BridgeTargetedSpoolLease {
    param([Parameter(Mandatory)] [string] $Path)

    Initialize-BridgeTargetedReplayNative
    $handle = $null
    $stream = $null
    try {
        # GENERIC_READ | DELETE, FILE_SHARE_READ, OPEN_EXISTING, and
        # FILE_FLAG_OPEN_REPARSE_POINT bind the selected ready identity and
        # prevent path replacement through the final handle disposition.
        $handle = [WaggleDance.BridgeTargetedReplayNative]::CreateFileW(
            $Path,
            [uint32]2147549184,
            [uint32]1,
            [IntPtr]::Zero,
            [uint32]3,
            [uint32]2097152,
            [IntPtr]::Zero
        )
        if ($handle.IsInvalid) {
            $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            throw "accepted WAL open failed with Win32 error $nativeCode"
        }
        $information = Get-BridgeTargetedHandleInformation -Handle $handle
        if ($information.NumberOfLinks -ne 1) {
            throw 'accepted WAL must have exactly one hard-link name'
        }
        if (
            ($information.FileAttributes -band
                [uint32][System.IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            throw 'accepted WAL must not be a reparse point'
        }
        if (
            ($information.FileAttributes -band
                [uint32][System.IO.FileAttributes]::Directory) -ne 0
        ) {
            throw 'accepted WAL must be a file'
        }
        $stream = New-Object System.IO.FileStream(
            $handle,
            [System.IO.FileAccess]::Read
        )
        $handle = $null
        [byte[]]$bytes = Read-BridgeTargetedLeaseBytes -Stream $stream
        $lease = [pscustomobject]@{
            Path = [System.IO.Path]::GetFullPath($Path)
            Stream = $stream
            Bytes = $bytes
            VolumeSerialNumber = [uint32]$information.VolumeSerialNumber
            FileIndexHigh = [uint32]$information.FileIndexHigh
            FileIndexLow = [uint32]$information.FileIndexLow
        }
        $stream = $null
        return $lease
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
        if ($null -ne $handle) { $handle.Dispose() }
    }
}

function Close-BridgeTargetedSpoolLease {
    param([AllowNull()] [object] $Lease)

    if ($null -ne $Lease -and $null -ne $Lease.Stream) {
        $Lease.Stream.Dispose()
        $Lease.Stream = $null
    }
}

function Assert-TargetedSpoolUnchanged {
    param(
        [Parameter(Mandatory)] [object] $Wal,
        [Parameter(Mandatory)] [string] $ExpectedSha256
    )

    $lease = $Wal.TargetedLease
    if ($null -eq $lease -or $null -eq $lease.Stream) {
        throw 'accepted WAL lease is not active'
    }
    $information = Get-BridgeTargetedHandleInformation `
        -Handle $lease.Stream.SafeFileHandle
    if (
        $information.NumberOfLinks -ne 1 -or
        $information.VolumeSerialNumber -ne $lease.VolumeSerialNumber -or
        $information.FileIndexHigh -ne $lease.FileIndexHigh -or
        $information.FileIndexLow -ne $lease.FileIndexLow -or
        ($information.FileAttributes -band
            [uint32][System.IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        throw 'accepted WAL identity changed before replay'
    }
    [byte[]]$currentBytes = Read-BridgeTargetedLeaseBytes -Stream $lease.Stream
    if (-not (Test-BridgeBytesEqual -Left $currentBytes -Right $Wal.Bytes)) {
        throw 'accepted WAL changed before replay'
    }
    if ((Get-BridgeSha256Hex -Bytes $currentBytes) -cne $ExpectedSha256) {
        throw 'accepted WAL SHA-256 changed before replay'
    }
}

function Assert-BridgeTargetedCanonicalInformation {
    param(
        [Parameter(Mandatory)] $Information,
        [Parameter(Mandatory)] [string] $Label
    )

    if ($Information.NumberOfLinks -ne 1) {
        throw "$Label must have exactly one hard-link name"
    }
    if (
        ($Information.FileAttributes -band
            [uint32][System.IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        throw "$Label must not be a reparse point"
    }
    if (
        ($Information.FileAttributes -band
            [uint32][System.IO.FileAttributes]::Directory) -ne 0
    ) {
        throw "$Label must be a file"
    }
}

function Open-BridgeTargetedCanonicalLease {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [switch] $AllowMissing,
        [string] $ExpectedParentResolvedPath = ''
    )

    Initialize-BridgeTargetedReplayNative
    $handle = [WaggleDance.BridgeTargetedReplayNative]::CreateFileW(
        $Path,
        [uint32]2147483648,
        [uint32]3,
        [IntPtr]::Zero,
        [uint32]3,
        [uint32]2097152,
        [IntPtr]::Zero
    )
    if ($handle.IsInvalid) {
        $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        $handle.Dispose()
        if ($AllowMissing -and $nativeCode -in @(2, 3)) { return $null }
        throw "targeted canonical lease open failed with Win32 error $nativeCode"
    }
    $stream = $null
    try {
        $information = Get-BridgeTargetedHandleInformation -Handle $handle
        Assert-BridgeTargetedCanonicalInformation `
            -Information $information -Label 'targeted canonical log'
        $resolvedPath = Get-BridgeTargetedFinalPath -Handle $handle
        if ($ExpectedParentResolvedPath) {
            $actualParent = [System.IO.Path]::GetFullPath(
                [System.IO.Path]::GetDirectoryName($resolvedPath)
            ).TrimEnd([char]'\')
            $expectedParent = [System.IO.Path]::GetFullPath(
                $ExpectedParentResolvedPath
            ).TrimEnd([char]'\')
            if (-not [string]::Equals(
                $actualParent,
                $expectedParent,
                [StringComparison]::OrdinalIgnoreCase
            )) {
                throw 'targeted canonical log resolved outside the pinned shared directory'
            }
        }
        $stream = New-Object System.IO.FileStream(
            $handle,
            [System.IO.FileAccess]::Read
        )
        $handle = $null
        $lease = [pscustomobject]@{
            Path = [System.IO.Path]::GetFullPath($Path)
            Stream = $stream
            VolumeSerialNumber = [uint32]$information.VolumeSerialNumber
            FileIndexHigh = [uint32]$information.FileIndexHigh
            FileIndexLow = [uint32]$information.FileIndexLow
            ResolvedPath = $resolvedPath
        }
        $stream = $null
        return $lease
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
        if ($null -ne $handle) { $handle.Dispose() }
    }
}

function Assert-BridgeTargetedCanonicalLeaseUnchanged {
    param([Parameter(Mandatory)] $Lease)

    if ($null -eq $Lease.Stream) {
        throw 'targeted canonical lease is not active'
    }
    $information = Get-BridgeTargetedHandleInformation `
        -Handle $Lease.Stream.SafeFileHandle
    Assert-BridgeTargetedCanonicalInformation `
        -Information $information -Label 'targeted canonical log'
    if (
        $information.VolumeSerialNumber -ne $Lease.VolumeSerialNumber -or
        $information.FileIndexHigh -ne $Lease.FileIndexHigh -or
        $information.FileIndexLow -ne $Lease.FileIndexLow
    ) {
        throw 'targeted canonical identity changed during replay'
    }
}

function Assert-BridgeTargetedCanonicalMutationStream {
    param(
        [Parameter(Mandatory)] [System.IO.FileStream] $Stream,
        [AllowNull()] $ExpectedLease
    )

    $information = Get-BridgeTargetedHandleInformation `
        -Handle $Stream.SafeFileHandle
    Assert-BridgeTargetedCanonicalInformation `
        -Information $information -Label 'targeted canonical mutation handle'
    if ($null -ne $ExpectedLease) {
        Assert-BridgeTargetedCanonicalLeaseUnchanged -Lease $ExpectedLease
        if (
            $information.VolumeSerialNumber -ne $ExpectedLease.VolumeSerialNumber -or
            $information.FileIndexHigh -ne $ExpectedLease.FileIndexHigh -or
            $information.FileIndexLow -ne $ExpectedLease.FileIndexLow
        ) {
            throw 'targeted canonical mutation handle does not match its lease'
        }
    }
}

function Open-BridgeTargetedCanonicalMutationStream {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [AllowNull()] $ExpectedLease,
        [switch] $Create
    )

    Initialize-BridgeTargetedReplayNative
    if ($null -ne $ExpectedLease) {
        Assert-BridgeTargetedCanonicalLeaseUnchanged -Lease $ExpectedLease
    }
    $creationDisposition = if ($Create) { [uint32]4 } else { [uint32]3 }
    $handle = [WaggleDance.BridgeTargetedReplayNative]::CreateFileW(
        $Path,
        [uint32]3221225472,
        [uint32]1,
        [IntPtr]::Zero,
        $creationDisposition,
        [uint32]2097152,
        [IntPtr]::Zero
    )
    if ($handle.IsInvalid) {
        $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        $handle.Dispose()
        throw "targeted canonical mutation open failed with Win32 error $nativeCode"
    }
    $stream = $null
    try {
        $stream = New-Object System.IO.FileStream(
            $handle,
            [System.IO.FileAccess]::ReadWrite
        )
        $handle = $null
        Assert-BridgeTargetedCanonicalMutationStream `
            -Stream $stream -ExpectedLease $ExpectedLease
        $result = $stream
        $stream = $null
        return $result
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
        if ($null -ne $handle) { $handle.Dispose() }
    }
}

function Set-BridgeTargetedSpoolDeletePending {
    param(
        [Parameter(Mandatory)] [object] $Wal,
        [Parameter(Mandatory)] [string] $ExpectedSha256
    )

    Assert-TargetedSpoolUnchanged -Wal $Wal -ExpectedSha256 $ExpectedSha256
    $disposition = New-Object WaggleDance.BridgeTargetedFileDispositionInformation
    $disposition.DeleteFile = $true
    $size = [uint32][Runtime.InteropServices.Marshal]::SizeOf($disposition)
    if (-not [WaggleDance.BridgeTargetedReplayNative]::SetFileInformationByHandle(
        $Wal.TargetedLease.Stream.SafeFileHandle,
        4,
        [ref]$disposition,
        $size
    )) {
        $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        throw "accepted WAL disposition failed with Win32 error $nativeCode"
    }
    Close-BridgeTargetedSpoolLease -Lease $Wal.TargetedLease
}

function Copy-TargetedSpoolToArchive {
    param(
        [Parameter(Mandatory)] [object] $Wal,
        [Parameter(Mandatory)] [string] $ArchiveDir,
        [Parameter(Mandatory)] [string] $ExpectedSha256
    )

    Assert-TargetedSpoolUnchanged -Wal $Wal -ExpectedSha256 $ExpectedSha256
    Assert-BridgeTargetedPlainPath `
        -Path $acceptedDir -Label 'accepted-v1 directory' -Directory
    if (-not (Test-Path -LiteralPath $ArchiveDir)) {
        [void](New-Item -ItemType Directory -Path $ArchiveDir -Force)
    }
    Assert-BridgeTargetedPlainPath `
        -Path $ArchiveDir -Label 'accepted-v1 replayed directory' -Directory
    $archiveDirectoryLease = Open-BridgeTargetedDirectoryLease `
        -Path $ArchiveDir -Label 'accepted-v1 replayed directory'
    $destination = Join-Path $ArchiveDir $Wal.File.Name
    if (Test-Path -LiteralPath $destination) {
        $existingArchiveLease = $null
        try {
            $existingArchiveLease = Open-BridgeTargetedSpoolLease `
                -Path $destination
            $existingArchiveSha256 = Get-BridgeSha256Hex `
                -Bytes $existingArchiveLease.Bytes
            if (
                $existingArchiveSha256 -cne $ExpectedSha256 -or
                -not (Test-BridgeBytesEqual `
                    -Left $existingArchiveLease.Bytes -Right $Wal.Bytes)
            ) {
                throw 'accepted WAL archive leaf collides with different bytes'
            }
            Assert-TargetedSpoolUnchanged `
                -Wal $Wal -ExpectedSha256 $ExpectedSha256
            Set-BridgeTargetedSpoolDeletePending `
                -Wal $Wal -ExpectedSha256 $ExpectedSha256
            return $true
        } finally {
            Close-BridgeTargetedSpoolLease -Lease $existingArchiveLease
            if ($null -ne $archiveDirectoryLease) {
                $archiveDirectoryLease.Handle.Dispose()
                $archiveDirectoryLease = $null
            }
        }
    }
    $archiveStream = $null
    $archiveCommitted = $false
    $archiveCreated = $false
    try {
        $archiveStream = New-Object System.IO.FileStream(
            $destination,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
        $archiveCreated = $true
        $archiveStream.Write($Wal.Bytes, 0, $Wal.Bytes.Length)
        $archiveStream.Flush($true)
        [void]$archiveStream.Seek(0, [System.IO.SeekOrigin]::Begin)
        [byte[]]$archiveBytes = New-Object byte[] ([int]$Wal.Bytes.Length)
        $archiveOffset = 0
        while ($archiveOffset -lt $archiveBytes.Length) {
            $archiveRead = $archiveStream.Read(
                $archiveBytes,
                $archiveOffset,
                $archiveBytes.Length - $archiveOffset
            )
            if ($archiveRead -le 0) {
                throw 'accepted WAL archive ended during verification'
            }
            $archiveOffset += $archiveRead
        }
        if (-not (Test-BridgeBytesEqual -Left $archiveBytes -Right $Wal.Bytes)) {
            throw 'accepted WAL archive bytes do not match the audited WAL'
        }
        if ((Get-BridgeSha256Hex -Bytes $archiveBytes) -cne $ExpectedSha256) {
            throw 'accepted WAL archive SHA-256 does not match the audited WAL'
        }
        # The exact archive is durable evidence as soon as its flushed bytes
        # verify. Keep it across any later source-disposition or handle-close
        # failure; a retry reconciles this same leaf and digest idempotently.
        $archiveCommitted = $true
        Assert-TargetedSpoolUnchanged `
            -Wal $Wal -ExpectedSha256 $ExpectedSha256
        Set-BridgeTargetedSpoolDeletePending `
            -Wal $Wal -ExpectedSha256 $ExpectedSha256
        $archiveStream.Dispose()
        $archiveStream = $null
        return $true
    } finally {
        if ($null -ne $archiveStream) { $archiveStream.Dispose() }
        if ($null -ne $archiveDirectoryLease) {
            $archiveDirectoryLease.Handle.Dispose()
        }
        if (
            -not $archiveCommitted -and
            $archiveCreated -and
            (Test-Path -LiteralPath $destination -PathType Leaf)
        ) {
            Remove-Item -LiteralPath $destination -Force -ErrorAction SilentlyContinue
        }
    }
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
    param(
        [Parameter(Mandatory)] [System.IO.FileInfo] $File,
        [AllowEmptyCollection()] [byte[]] $AuditedBytes
    )

    [byte[]]$bytes = if ($PSBoundParameters.ContainsKey('AuditedBytes')) {
        $AuditedBytes
    } else {
        [System.IO.File]::ReadAllBytes($File.FullName)
    }
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
        [Parameter(Mandatory)] [AllowEmptyCollection()] [object[]] $WalRecords,
        [Parameter(Mandatory)] [string] $QuarantineDir,
        [switch] $RequirePlainQuarantine,
        [AllowNull()] $TargetedCanonicalLease
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return '' }
    $tailProbe = $null
    $disposeTailProbe = $false
    try {
        if ($RequirePlainQuarantine) {
            if ($null -eq $TargetedCanonicalLease) {
                throw 'targeted canonical log exists without an identity lease'
            }
            Assert-BridgeTargetedCanonicalLeaseUnchanged `
                -Lease $TargetedCanonicalLease
            $tailProbe = $TargetedCanonicalLease.Stream
        } else {
            $tailProbe = New-Object System.IO.FileStream(
                $Path,
                [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::Read,
                [System.IO.FileShare]::Read
            )
            $disposeTailProbe = $true
        }
        if ($tailProbe.Length -eq 0) { return '' }
        [byte[]]$lastByte = Read-BridgeExactStreamRange `
            -Stream $tailProbe -Offset ([int64]$tailProbe.Length - 1) `
            -Length 1 -Label 'canonical bridge tail probe'
        if ($lastByte[0] -eq 10) { return '' }
    } finally {
        if ($disposeTailProbe -and $null -ne $tailProbe) { $tailProbe.Dispose() }
    }
    [byte[]]$canonicalBytes = if ($null -ne $TargetedCanonicalLease) {
        Assert-BridgeTargetedCanonicalLeaseUnchanged `
            -Lease $TargetedCanonicalLease
        Read-BridgeExactStreamRange `
            -Stream $TargetedCanonicalLease.Stream -Offset 0 `
            -Length ([int64]$TargetedCanonicalLease.Stream.Length) `
            -Label 'targeted canonical bridge log'
    } else {
        [System.IO.File]::ReadAllBytes($Path)
    }
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
    if ($RequirePlainQuarantine) {
        Assert-BridgeTargetedPlainPath `
            -Path $QuarantineDir -Label 'accepted-v1 quarantine directory' `
            -IfExists -Directory
    }
    if (-not (Test-Path -LiteralPath $QuarantineDir -PathType Container)) {
        [void](New-Item -ItemType Directory -Path $QuarantineDir -Force)
    }
    if ($RequirePlainQuarantine) {
        Assert-BridgeTargetedPlainPath `
            -Path $QuarantineDir -Label 'accepted-v1 quarantine directory' `
            -Directory
    }
    $quarantineDirectoryLease = $null
    if ($RequirePlainQuarantine) {
        $quarantineDirectoryLease = Open-BridgeTargetedDirectoryLease `
            -Path $QuarantineDir -Label 'accepted-v1 quarantine directory'
    }
    $quarantinePath = Join-Path $QuarantineDir (
        'canonical-torn-tail-{0}-{1}-{2}.bin' -f
        [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfff'),
        $PID,
        [guid]::NewGuid().ToString('N')
    )
    try {
        Write-NewBridgeFileDurably -Path $quarantinePath -Bytes $tailBytes
        $stream = $null
        try {
            if ($RequirePlainQuarantine) {
                $stream = Open-BridgeTargetedCanonicalMutationStream `
                    -Path $Path -ExpectedLease $TargetedCanonicalLease
            } else {
                $stream = New-Object System.IO.FileStream(
                    $Path,
                    [System.IO.FileMode]::Open,
                    [System.IO.FileAccess]::ReadWrite,
                    [System.IO.FileShare]::Read
                )
            }
            if ([int64]$stream.Length -ne [int64]$canonicalBytes.Length) {
                throw 'canonical bridge log changed before torn-tail truncation'
            }
            Invalidate-BridgeAppendValidationCheckpoint `
                -CanonicalPath $Path -Reason 'wal-bound-torn-tail-truncate'
            $stream.SetLength([int64]$tailStart)
            $stream.Flush($true)
            if ($RequirePlainQuarantine) {
                Assert-BridgeTargetedCanonicalMutationStream `
                    -Stream $stream -ExpectedLease $TargetedCanonicalLease
            }
        } finally {
            if ($null -ne $stream) { $stream.Dispose() }
        }
    } finally {
        if ($null -ne $quarantineDirectoryLease) {
            $quarantineDirectoryLease.Handle.Dispose()
        }
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
$targetedSpoolLease = $null
$targetedCanonicalLease = $null
$targetedSharedResolvedPath = ''
$targetedDirectoryLeases = New-Object 'System.Collections.Generic.List[object]'
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
        if ($targetedReplay) {
            throw 'accepted-target replay deferred: append mutex is unavailable'
        }
        Write-Warning 'bridge append mutex timeout; all spool files kept'
        Write-Output 'spool replay skipped: append mutex unavailable; no changes'
        return
    }
    if ($appendDirtyAbandoned) {
        if ($targetedReplay) {
            throw 'accepted-target replay deferred: AppendV1 ownership was abandoned'
        }
        Write-Warning 'AppendV1 was abandoned; dirty ownership cannot replay or mutate canonical bytes'
        Write-Output 'spool replay skipped: dirty abandoned AppendV1 ownership; no changes'
        return
    }

    # Initial AppendV1 ownership covers exact accepted-target selection or
    # explicit legacy discovery/recovery plus canonical snapshot capture. The
    # accepted branch contains no spool enumeration.
    $pendingFiles = @()
    $finalFiles = @()
    $finalRecords = New-Object 'System.Collections.Generic.List[object]'
    $finalByPath = @{}
    if ($targetedReplay) {
        # OPEN_REPARSE_POINT applies only to the final path component. Walk and
        # pin the entire configured root so a plain BridgeRoot reached through
        # an ancestor junction cannot redirect accepted replay outside it.
        Add-BridgeTargetedDirectoryChainLeases `
            -Path $BridgeRoot -Leases $targetedDirectoryLeases
        foreach ($directoryPlan in @(
            [pscustomobject]@{ Path = $spoolDir; Label = 'targeted spool directory' },
            [pscustomobject]@{ Path = $acceptedDir; Label = 'accepted-v1 directory' },
            [pscustomobject]@{
                Path = $acceptedReadyDir
                Label = 'accepted-v1 ready directory'
            }
        )) {
            $targetedDirectoryLeases.Add((Open-BridgeTargetedDirectoryLease `
                -Path $directoryPlan.Path -Label $directoryPlan.Label))
        }

        $targetPath = Join-Path $acceptedReadyDir $AcceptedWalLeaf
        $targetFullPath = [System.IO.Path]::GetFullPath($targetPath)
        $readyFullPath = [System.IO.Path]::GetFullPath($acceptedReadyDir)
        if (-not [string]::Equals(
            [System.IO.Path]::GetDirectoryName($targetFullPath),
            $readyFullPath,
            [StringComparison]::OrdinalIgnoreCase
        )) {
            throw 'accepted WAL path escapes the fixed ready directory'
        }
        if (-not (Test-Path -LiteralPath $targetFullPath -PathType Leaf)) {
            $archivedPath = Join-Path $archiveDir $AcceptedWalLeaf
            if (-not (Test-Path -LiteralPath $archivedPath -PathType Leaf)) {
                throw 'accepted WAL is absent from both ready and replayed'
            }
            Assert-BridgeTargetedPlainPath `
                -Path $archiveDir -Label 'accepted-v1 replayed directory' `
                -Directory
            $targetedDirectoryLeases.Add((Open-BridgeTargetedDirectoryLease `
                -Path $archiveDir -Label 'accepted-v1 replayed directory'))
            Assert-BridgeTargetedPlainPath `
                -Path $archivedPath -Label 'archived accepted WAL'
            $archivedFile = Get-Item `
                -LiteralPath $archivedPath -Force -ErrorAction Stop
            if ($archivedFile.Name -cne $AcceptedWalLeaf) {
                throw 'archived accepted WAL did not resolve to the requested case-exact leaf'
            }
            $targetedSpoolLease = Open-BridgeTargetedSpoolLease `
                -Path $archivedPath
            $archivedSha256 = Get-BridgeSha256Hex `
                -Bytes $targetedSpoolLease.Bytes
            if ($archivedSha256 -cne $ExpectedWalSha256) {
                throw (
                    'archived accepted WAL SHA-256 mismatch: expected {0}, actual {1}' -f
                    $ExpectedWalSha256,
                    $archivedSha256
                )
            }
            $archivedRecord = Read-BridgeWalFile `
                -File $archivedFile -AuditedBytes $targetedSpoolLease.Bytes
            if ($archivedRecord.Rows.Count -ne 1) {
                throw 'archived accepted-v1 WAL must contain exactly one event row'
            }
            Write-Output "accepted WAL already delivered: $AcceptedWalLeaf"
            return
        }
        Assert-BridgeTargetedPlainPath `
            -Path $targetFullPath -Label 'accepted WAL'
        $targetFile = Get-Item -LiteralPath $targetFullPath -Force -ErrorAction Stop
        if ($targetFile.Name -cne $AcceptedWalLeaf) {
            throw 'accepted WAL path did not resolve to the requested case-exact leaf'
        }
        $targetedSpoolLease = Open-BridgeTargetedSpoolLease -Path $targetFullPath
        $actualWalSha256 = Get-BridgeSha256Hex -Bytes $targetedSpoolLease.Bytes
        if ($actualWalSha256 -cne $ExpectedWalSha256) {
            throw (
                'accepted WAL SHA-256 mismatch: expected {0}, actual {1}' -f
                $ExpectedWalSha256,
                $actualWalSha256
            )
        }
        $targetRecord = Read-BridgeWalFile `
            -File $targetFile -AuditedBytes $targetedSpoolLease.Bytes
        if ($targetRecord.Rows.Count -ne 1) {
            throw 'accepted-v1 WAL must contain exactly one event row'
        }
        $targetRecord | Add-Member `
            -NotePropertyName TargetedLease `
            -NotePropertyValue $targetedSpoolLease
        $finalRecords.Add($targetRecord)

        $preexistingArchivePath = Join-Path $archiveDir $AcceptedWalLeaf
        if (Test-Path -LiteralPath $preexistingArchivePath -PathType Leaf) {
            Assert-BridgeTargetedPlainPath `
                -Path $archiveDir -Label 'accepted-v1 replayed directory' -Directory
            $targetedDirectoryLeases.Add((Open-BridgeTargetedDirectoryLease `
                -Path $archiveDir -Label 'accepted-v1 replayed directory'))
            $preexistingArchiveLease = $null
            try {
                $preexistingArchiveLease = Open-BridgeTargetedSpoolLease `
                    -Path $preexistingArchivePath
                if (
                    (Get-BridgeSha256Hex -Bytes $preexistingArchiveLease.Bytes) -cne
                        $ExpectedWalSha256 -or
                    -not (Test-BridgeBytesEqual `
                        -Left $preexistingArchiveLease.Bytes `
                        -Right $targetedSpoolLease.Bytes)
                ) {
                    throw 'accepted WAL archive leaf collides with different bytes'
                }
            } finally {
                Close-BridgeTargetedSpoolLease -Lease $preexistingArchiveLease
            }
        }

        $sharedDir = Split-Path -Parent $eventsPath
        if (
            -not $DryRun -and
            -not (Test-Path -LiteralPath $sharedDir -PathType Container)
        ) {
            [void](New-Item -ItemType Directory -Path $sharedDir)
        }
        if (Test-Path -LiteralPath $sharedDir -PathType Container) {
            $sharedLeaseSet = Open-BridgeTargetedSharedDirectoryLeaseSet `
                -Path $sharedDir
            foreach ($sharedLease in @($sharedLeaseSet.Leases)) {
                $targetedDirectoryLeases.Add($sharedLease)
            }
            $targetedSharedResolvedPath = [string]$sharedLeaseSet.ResolvedPath
        }
        if (-not $DryRun -and -not (Test-Path -LiteralPath $eventsPath)) {
            $canonicalCreateStream = $null
            try {
                $canonicalCreateStream = Open-BridgeTargetedCanonicalMutationStream `
                    -Path $eventsPath -ExpectedLease $null -Create
                $canonicalCreateStream.Flush($true)
            } finally {
                if ($null -ne $canonicalCreateStream) {
                    $canonicalCreateStream.Dispose()
                }
            }
        }
        $targetedCanonicalLease = Open-BridgeTargetedCanonicalLease `
            -Path $eventsPath -AllowMissing:$DryRun.IsPresent `
            -ExpectedParentResolvedPath $targetedSharedResolvedPath
        if (-not $DryRun -and $null -eq $targetedCanonicalLease) {
            throw 'targeted canonical log could not be established'
        }
    } else {
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
        foreach ($file in $finalFiles) {
            $record = Read-BridgeWalFile -File $file
            $finalRecords.Add($record)
            $finalByPath[$file.FullName.ToUpperInvariant()] = $record
        }
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
        -Path $eventsPath -WalRecords $bindingRecords.ToArray() `
        -QuarantineDir $quarantineDir `
        -RequirePlainQuarantine:$targetedReplay `
        -TargetedCanonicalLease $targetedCanonicalLease)

    if ($targetedReplay) {
        Assert-TargetedSpoolUnchanged `
            -Wal $targetRecord -ExpectedSha256 $ExpectedWalSha256
        if ($null -ne $targetedCanonicalLease) {
            Assert-BridgeTargetedCanonicalLeaseUnchanged `
                -Lease $targetedCanonicalLease
        }
    }

    $canonicalSnapshot = Open-BridgeCanonicalSnapshot -Path $eventsPath
    if ($targetedReplay -and $null -ne $targetedCanonicalLease) {
        Assert-BridgeTargetedCanonicalLeaseUnchanged `
            -Lease $targetedCanonicalLease
    }
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
        if ($targetedReplay) {
            throw 'accepted-target replay deferred: append mutex is unavailable after scan'
        }
        Write-Warning 'bridge append mutex timeout after canonical scan; all spool files kept'
        Write-Output 'spool replay skipped: append mutex unavailable after scan; no spool changes'
        return
    }
    if ($appendDirtyAbandoned) {
        if ($targetedReplay) {
            throw 'accepted-target replay deferred: AppendV1 ownership was abandoned after scan'
        }
        Write-Warning 'AppendV1 was abandoned after canonical scan; dirty ownership cannot replay'
        Write-Output 'spool replay skipped: dirty abandoned AppendV1 ownership after scan; no spool changes'
        return
    }
    if ($targetedReplay) {
        Assert-TargetedSpoolUnchanged `
            -Wal $targetRecord -ExpectedSha256 $ExpectedWalSha256
        if ($null -ne $targetedCanonicalLease) {
            Assert-BridgeTargetedCanonicalLeaseUnchanged `
                -Lease $targetedCanonicalLease
        }
    }
    Add-BridgeCanonicalKeysSinceSnapshot `
        -Snapshot $canonicalSnapshot -Path $eventsPath -Keys $existingKeys
    if ($targetedReplay -and $null -ne $targetedCanonicalLease) {
        Assert-BridgeTargetedCanonicalLeaseUnchanged `
            -Lease $targetedCanonicalLease
    }

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
            } elseif ($targetedReplay) {
                [void](Copy-TargetedSpoolToArchive `
                    -Wal $wal -ArchiveDir $archiveDir `
                    -ExpectedSha256 $ExpectedWalSha256)
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
            if ($targetedReplay) {
                Assert-TargetedSpoolUnchanged `
                    -Wal $wal -ExpectedSha256 $ExpectedWalSha256
            }
            if ($targetedReplay) {
                [void](Invoke-BridgeTransactionalAppend `
                    -Path $eventsPath -Bytes $appendBytes `
                    -AppendMutexOwned $appendAcquired `
                    -RequirePlainCanonical `
                    -TargetedCanonicalLease $targetedCanonicalLease)
                Assert-BridgeTargetedCanonicalLeaseUnchanged `
                    -Lease $targetedCanonicalLease
            } else {
                [void](Invoke-BridgeTransactionalAppend `
                    -Path $eventsPath -Bytes $appendBytes `
                    -AppendMutexOwned $appendAcquired)
            }
        } catch {
            if ($_.Exception.Message -match 'ROLLBACK FAILED') { throw }
            if ($targetedReplay) { throw }
            Write-Warning "append failed and WAL was kept: $($wal.File.Name) ($($_.Exception.Message))"
            $failed++
            continue
        }
        foreach ($row in $rowsToAppend) {
            [void]$existingKeys.Add([string]$row.Key)
        }
        if ($targetedReplay) {
            [void](Copy-TargetedSpoolToArchive `
                -Wal $wal -ArchiveDir $archiveDir `
                -ExpectedSha256 $ExpectedWalSha256)
        } else {
            [void](Move-SpoolToArchive -File $wal.File -ArchiveDir $archiveDir)
        }
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
    if ($null -ne $targetedCanonicalLease -and $null -ne $targetedCanonicalLease.Stream) {
        $targetedCanonicalLease.Stream.Dispose()
        $targetedCanonicalLease.Stream = $null
    }
    Close-BridgeTargetedSpoolLease -Lease $targetedSpoolLease
    for (
        $leaseIndex = $targetedDirectoryLeases.Count - 1;
        $leaseIndex -ge 0;
        $leaseIndex--
    ) {
        $targetedDirectoryLeases[$leaseIndex].Handle.Dispose()
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
    Close-BridgeQueuePublicationFence `
        -Mutex $publicationMutex -Acquired $publicationAcquired
}
