#requires -Version 5.1
<#
.SYNOPSIS
    Replay durable failed-append-<agent>-<utc>-<pid>-<nonce>.jsonl bridge spools.

.DESCRIPTION
    Write-AgentEvent.ps1 spools an event to <bridgeRoot>/spool/
    failed-append-<agent>-<utc>-<pid>-<nonce>.jsonl when the V1-protected
    shared-log append cannot complete. This script
    closes the loop for one independently audited WAL: it appends only the
    hash-bound SpoolFile rows to shared/events.jsonl using the same named mutex
    the writer uses, and archives that one file on success. Untargeted bulk
    replay is intentionally refused because appending an old authoritative
    event at the current tail can reactivate superseded gate evidence.

    Order note: the original event timestamp is preserved but the recovered
    row appends at the CURRENT physical tail. The caller must therefore audit
    semantic supersession before selecting the WAL; byte-exact deduplication
    alone cannot prove that replaying an old signal is safe.

.PARAMETER BridgeRoot
    Bridge runtime root. Defaults to AGENT_BRIDGE_RUNTIME_ROOT or the
    parent of this script's directory.

.PARAMETER DryRun
    Validate and list the one selected hash-bound WAL without appending or
    archiving it.

.PARAMETER SpoolFile
    Optional exact canonical failed-append leaf name to replay in isolation.
    Targeted replay requires ExpectedSpoolSha256 and never processes pending
    WALs or any other final spool.

.PARAMETER ExpectedSpoolSha256
    Required lowercase SHA-256 of the complete WAL bytes when SpoolFile is
    supplied. This binds targeted recovery to the audited immutable payload.
#>
[CmdletBinding()]
param(
    [string] $BridgeRoot = '',
    [switch] $DryRun,
    [string] $SpoolFile = '',
    [string] $ExpectedSpoolSha256 = ''
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
# One operator-reviewed historical bridge row contains two complete events
# separated by a lone CR after an off-by-one repair removed the matching LF.
# Compatibility is bound to the exact normalized physical-row digest and the
# two event fingerprints. Every other bare-CR row remains fail-closed.
$knownLegacyBareCrRowSha256 = `
    '53f863ac93dd977504346feddc382ccd65bafceb4aeaad2bba1765712190a0d3'
$knownLegacyBareCrFingerprints = @(
    @(
        'codex-lead-1',
        'production-liveness-reactivation-scout-2026-07-01-codex-tools-1-since-20260701t161039z',
        '2026-07-01T16:45:30.4576368Z',
        'test',
        'attention'
    ),
    @(
        'codex-lead-1',
        'production-liveness-reactivation-scout-2026-07-01-codex-tools-1-since-20260701t161039z',
        '2026-07-01T16:46:54.4324612Z',
        'message',
        'bridge_log_repair_note'
    )
)
# A later adversarial writer probe persisted one unknown event type while an
# unmerged test branch had deliberately relaxed writer admission. It is not
# added to the taxonomy and carries no authority; this exact row is admitted
# only so a complete historical stream can be scanned for byte-exact WAL
# deduplication.
$knownLegacyUnknownTypeRowSha256 = `
    '056cbafc328c40441b85cf4f7c46dee0e540f222b94a96d8219025835bb0aa7f'
$knownLegacyUnknownTypeFingerprint = @(
    'claude-rco-2',
    'rco2-v8-typo-type-probe',
    '2026-08-09T23:24:39.1546638Z',
    'totally-bogus-typo-type',
    'test_probe'
)

$spoolFileWasBound = $PSBoundParameters.ContainsKey('SpoolFile')
$spoolDigestWasBound = $PSBoundParameters.ContainsKey('ExpectedSpoolSha256')
if ($spoolFileWasBound -ne $spoolDigestWasBound) {
    throw 'SpoolFile and ExpectedSpoolSha256 must be supplied together'
}
$targetedReplay = $spoolFileWasBound
if (-not $targetedReplay) {
    throw (
        'untargeted bulk replay is disabled; supply exact SpoolFile and ' +
        'ExpectedSpoolSha256 after semantic supersession review'
    )
}
if ($targetedReplay) {
    if (
        $SpoolFile -cnotmatch
        '\Afailed-append-[a-z][a-z0-9_-]{1,32}-[0-9]{8}T[0-9]{9}-[0-9]+-[0-9a-f]{32}\.jsonl\z'
    ) {
        throw 'SpoolFile must be an exact canonical failed-append leaf name'
    }
    if ($ExpectedSpoolSha256 -cnotmatch '\A[0-9a-f]{64}\z') {
        throw 'ExpectedSpoolSha256 must be exactly 64 lowercase hex characters'
    }
}

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
    if ($targetedReplay) { throw 'targeted spool directory does not exist' }
    Write-Output 'no spool directory; nothing to replay'
    return
}
if ($targetedReplay) {
    Assert-BridgeTargetedPlainPath `
        -Path $BridgeRoot -Label 'targeted bridge root' -Directory
    Assert-BridgeTargetedPlainPath `
        -Path $spoolDir -Label 'targeted spool directory' -Directory
    Assert-BridgeTargetedPlainPath `
        -Path (Split-Path -Parent $eventsPath) `
        -Label 'targeted shared directory' -IfExists -Directory
    Assert-BridgeTargetedPlainPath `
        -Path $eventsPath -Label 'targeted canonical log' -IfExists
    Assert-BridgeTargetedPlainPath `
        -Path $archiveDir -Label 'targeted archive directory' `
        -IfExists -Directory
    Assert-BridgeTargetedPlainPath `
        -Path (Join-Path $spoolDir 'quarantine') `
        -Label 'targeted quarantine directory' -IfExists -Directory
    Assert-BridgeTargetedPlainPath `
        -Path "$eventsPath.append-v1-validation.json" `
        -Label 'targeted validation checkpoint' -IfExists
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
    param([Parameter(Mandatory)] [byte[]] $Bytes)
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

function Initialize-BridgeTargetedReplayNative {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'targeted spool replay requires Windows handle-bound filesystem operations'
    }
    if ('WaggleDance.BridgeTargetedReplayNative' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
using System.Text;

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

        [DllImport(
            "kernel32.dll",
            EntryPoint = "SetFileInformationByHandle",
            SetLastError = true
        )]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool SetFileInformationByHandleRaw(
            SafeFileHandle fileHandle,
            int fileInformationClass,
            IntPtr fileInformation,
            uint bufferSize
        );

        public static bool RenameFileByHandle(
            SafeFileHandle fileHandle,
            string destinationPath,
            bool replaceIfExists,
            out int errorCode
        ) {
            byte[] fileName = Encoding.Unicode.GetBytes(destinationPath);
            int rootDirectoryOffset = IntPtr.Size;
            int fileNameLengthOffset = rootDirectoryOffset + IntPtr.Size;
            int fileNameOffset = fileNameLengthOffset + sizeof(uint);
            int nativeHeaderSize = IntPtr.Size == 8 ? 24 : 16;
            int bufferSize = checked(nativeHeaderSize + fileName.Length + 2);
            IntPtr buffer = Marshal.AllocHGlobal(bufferSize);
            try {
                for (int index = 0; index < bufferSize; index++) {
                    Marshal.WriteByte(buffer, index, 0);
                }
                // FILE_RENAME_INFO_EX: REPLACE_IF_EXISTS (0x1) plus
                // POSIX_SEMANTICS (0x2) lets the atomic replacement proceed
                // while the old destination identity remains handle-pinned.
                Marshal.WriteInt32(buffer, 0, replaceIfExists ? 3 : 2);
                Marshal.WriteIntPtr(buffer, rootDirectoryOffset, IntPtr.Zero);
                Marshal.WriteInt32(buffer, fileNameLengthOffset, fileName.Length);
                Marshal.Copy(fileName, 0, IntPtr.Add(buffer, fileNameOffset), fileName.Length);
                bool result = SetFileInformationByHandleRaw(
                    fileHandle,
                    22,
                    buffer,
                    checked((uint)bufferSize)
                );
                errorCode = result ? 0 : Marshal.GetLastWin32Error();
                return result;
            } finally {
                Marshal.FreeHGlobal(buffer);
            }
        }
    }
}
'@
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

function Get-BridgeExactJsonPropertyValue {
    param(
        [Parameter(Mandatory)] [object] $Object,
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [string] $Label
    )
    $matches = @(
        $Object.PSObject.Properties |
            Where-Object { $_.Name -ceq $Name }
    )
    if ($matches.Count -ne 1) {
        throw "$Label must contain exactly one case-exact '$Name' property"
    }
    return $matches[0].Value
}

function ConvertTo-BridgeFingerprintTimestamp {
    param(
        [AllowNull()] $Value,
        [Parameter(Mandatory)] [string] $Label
    )
    if ($Value -is [string]) { return [string]$Value }
    $invariant = [Globalization.CultureInfo]::InvariantCulture
    if ($Value -is [DateTime]) {
        if ($Value.Kind -ne [DateTimeKind]::Utc) {
            throw "$Label timestamp must be UTC"
        }
        return $Value.ToString(
            "yyyy-MM-dd'T'HH:mm:ss.fffffff'Z'",
            $invariant
        )
    }
    if ($Value -is [DateTimeOffset]) {
        if ($Value.Offset -ne [TimeSpan]::Zero) {
            throw "$Label timestamp must have zero UTC offset"
        }
        return $Value.UtcDateTime.ToString(
            "yyyy-MM-dd'T'HH:mm:ss.fffffff'Z'",
            $invariant
        )
    }
    throw "$Label timestamp has an unsupported runtime type"
}

function Add-BridgeKnownLegacyBareCrKeys {
    param(
        [Parameter(Mandatory)] [string] $JsonLine,
        [Parameter(Mandatory)] [string] $Label,
        [Parameter(Mandatory)] [System.Text.UTF8Encoding] $StrictUtf8,
        [Parameter(Mandatory)] [AllowEmptyCollection()]
        [System.Collections.Generic.HashSet[string]] $Keys
    )

    [byte[]]$physicalRowBytes = $StrictUtf8.GetBytes($JsonLine)
    if (
        (Get-BridgeSha256Hex -Bytes $physicalRowBytes) -cne
        $knownLegacyBareCrRowSha256
    ) {
        return $false
    }
    $fragments = @($JsonLine.Split([char]13))
    if (
        $fragments.Count -ne 2 -or
        [string]::IsNullOrEmpty([string]$fragments[0]) -or
        [string]::IsNullOrEmpty([string]$fragments[1])
    ) {
        throw "$Label known historical bare-CR row must contain exactly two non-empty fragments"
    }

    for ($fragmentIndex = 0; $fragmentIndex -lt 2; $fragmentIndex++) {
        $fragmentLabel = "$Label historical fragment $($fragmentIndex + 1)"
        try {
            $eventObject = [string]$fragments[$fragmentIndex] |
                ConvertFrom-Json -ErrorAction Stop
        } catch {
            throw "$fragmentLabel has malformed JSON: $($_.Exception.Message)"
        }
        Assert-BridgeEventObjectShape -Object $eventObject -Label $fragmentLabel
        $actual = @(
            [string](Get-BridgeExactJsonPropertyValue `
                -Object $eventObject -Name 'agent' -Label $fragmentLabel),
            [string](Get-BridgeExactJsonPropertyValue `
                -Object $eventObject -Name 'task_id' -Label $fragmentLabel),
            (ConvertTo-BridgeFingerprintTimestamp `
                -Value (Get-BridgeExactJsonPropertyValue `
                    -Object $eventObject -Name 'ts_utc' -Label $fragmentLabel) `
                -Label $fragmentLabel),
            [string](Get-BridgeExactJsonPropertyValue `
                -Object $eventObject -Name 'type' -Label $fragmentLabel),
            [string](Get-BridgeExactJsonPropertyValue `
                -Object $eventObject -Name 'status' -Label $fragmentLabel)
        )
        for ($fieldIndex = 0; $fieldIndex -lt 5; $fieldIndex++) {
            if (
                $actual[$fieldIndex] -cne
                $knownLegacyBareCrFingerprints[$fragmentIndex][$fieldIndex]
            ) {
                throw "$fragmentLabel event fingerprint does not match"
            }
        }
        [byte[]]$canonicalRowBytes = $StrictUtf8.GetBytes(
            [string]$fragments[$fragmentIndex] + [char]13 + [char]10
        )
        [void]$Keys.Add((Get-BridgeSha256Hex -Bytes $canonicalRowBytes))
    }
    return $true
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
        throw "targeted spool identity query failed with Win32 error $nativeCode"
    }
    return $information
}

function Read-BridgeTargetedLeaseBytes {
    param([Parameter(Mandatory)] [System.IO.FileStream] $Stream)

    if ($Stream.Length -gt [int]::MaxValue) {
        throw 'targeted spool exceeds the supported byte length'
    }
    [void]$Stream.Seek(0, [System.IO.SeekOrigin]::Begin)
    [byte[]]$bytes = New-Object byte[] ([int]$Stream.Length)
    $offset = 0
    while ($offset -lt $bytes.Length) {
        $read = $Stream.Read($bytes, $offset, $bytes.Length - $offset)
        if ($read -le 0) { throw 'targeted spool ended during read' }
        $offset += $read
    }
    return ,$bytes
}

function Open-BridgeTargetedSpoolLease {
    param([Parameter(Mandatory)] [string] $Path)

    Initialize-BridgeTargetedReplayNative
    $handle = $null
    $stream = $null
    try {
        # DELETE access plus the absence of FILE_SHARE_DELETE binds the exact
        # selected file identity through its eventual handle-based disposition.
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
            throw "targeted spool open failed with Win32 error $nativeCode"
        }
        $information = Get-BridgeTargetedHandleInformation -Handle $handle
        if ($information.NumberOfLinks -ne 1) {
            throw 'targeted spool must have exactly one hard-link name'
        }
        if (
            ($information.FileAttributes -band
                [uint32][System.IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            throw 'targeted spool must not be a reparse point'
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

function Open-BridgeTargetedDirectoryLease {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $Label,
        [uint32] $ShareMode = 1
    )

    Initialize-BridgeTargetedReplayNative
    $handle = [WaggleDance.BridgeTargetedReplayNative]::CreateFileW(
        $Path,
        [uint32]2147483648,
        $ShareMode,
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
        return [pscustomobject]@{
            Path = [System.IO.Path]::GetFullPath($Path)
            Handle = $handle
        }
    } catch {
        $handle.Dispose()
        throw
    }
}

function Open-BridgeTargetedCanonicalLease {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [uint32] $DesiredAccess = 2147483648,
        [uint32] $ShareMode = 5
    )

    Initialize-BridgeTargetedReplayNative
    $handle = [WaggleDance.BridgeTargetedReplayNative]::CreateFileW(
        $Path,
        $DesiredAccess,
        $ShareMode,
        [IntPtr]::Zero,
        [uint32]3,
        [uint32]2097152,
        [IntPtr]::Zero
    )
    if ($handle.IsInvalid) {
        $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        $handle.Dispose()
        throw "targeted canonical handle open failed with Win32 error $nativeCode"
    }
    $stream = $null
    try {
        $information = Get-BridgeTargetedHandleInformation -Handle $handle
        if (
            ($information.FileAttributes -band
                [uint32][System.IO.FileAttributes]::Directory) -ne 0 -or
            ($information.FileAttributes -band
                [uint32][System.IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            throw 'targeted canonical log must be one plain file identity'
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
        }
        $stream = $null
        return $lease
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
        if ($null -ne $handle) { $handle.Dispose() }
    }
}

function Assert-BridgeTargetedCanonicalIdentity {
    param([Parameter(Mandatory)] [object] $Lease)

    if ($null -eq $Lease -or $null -eq $Lease.Stream) {
        throw 'targeted canonical lease is not active'
    }
    $information = Get-BridgeTargetedHandleInformation `
        -Handle $Lease.Stream.SafeFileHandle
    if (
        $information.VolumeSerialNumber -ne $Lease.VolumeSerialNumber -or
        $information.FileIndexHigh -ne $Lease.FileIndexHigh -or
        $information.FileIndexLow -ne $Lease.FileIndexLow -or
        ($information.FileAttributes -band
            [uint32][System.IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        throw 'targeted canonical identity changed during replay'
    }
}

function Read-BridgeTargetedCanonicalBytes {
    param([Parameter(Mandatory)] [object] $Lease)

    Assert-BridgeTargetedCanonicalIdentity -Lease $Lease
    if ($Lease.Stream.Length -gt [int]::MaxValue) {
        throw 'targeted canonical log exceeds the supported byte length'
    }
    [void]$Lease.Stream.Seek(0, [System.IO.SeekOrigin]::Begin)
    [byte[]]$bytes = New-Object byte[] ([int]$Lease.Stream.Length)
    $offset = 0
    while ($offset -lt $bytes.Length) {
        $read = $Lease.Stream.Read($bytes, $offset, $bytes.Length - $offset)
        if ($read -le 0) { throw 'targeted canonical log ended during read' }
        $offset += $read
    }
    return ,$bytes
}

function Close-BridgeTargetedCanonicalLease {
    param([AllowNull()] [object] $Lease)

    if ($null -eq $Lease -or $null -eq $Lease.Stream) { return }
    $Lease.Stream.Dispose()
    $Lease.Stream = $null
}

function Test-BridgeTargetedLeaseIdentityEqual {
    param(
        [Parameter(Mandatory)] [object] $Left,
        [Parameter(Mandatory)] [object] $Right
    )

    return (
        $Left.VolumeSerialNumber -eq $Right.VolumeSerialNumber -and
        $Left.FileIndexHigh -eq $Right.FileIndexHigh -and
        $Left.FileIndexLow -eq $Right.FileIndexLow
    )
}

function Assert-BridgeTargetedCanonicalPathMatchesLease {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [object] $ExpectedLease
    )

    $observedLease = $null
    try {
        $observedLease = Open-BridgeTargetedCanonicalLease `
            -Path $Path -ShareMode 5
        if (-not (Test-BridgeTargetedLeaseIdentityEqual `
            -Left $observedLease -Right $ExpectedLease)) {
            throw 'targeted canonical path no longer names the audited identity'
        }
    } finally {
        Close-BridgeTargetedCanonicalLease -Lease $observedLease
    }
}

function Set-BridgeTargetedLeaseDeletePending {
    param(
        [Parameter(Mandatory)] [object] $Lease,
        [Parameter(Mandatory)] [string] $Label
    )

    $disposition = New-Object WaggleDance.BridgeTargetedFileDispositionInformation
    $disposition.DeleteFile = $true
    $size = [uint32][Runtime.InteropServices.Marshal]::SizeOf($disposition)
    if (-not [WaggleDance.BridgeTargetedReplayNative]::SetFileInformationByHandle(
        $Lease.Stream.SafeFileHandle,
        4,
        [ref]$disposition,
        $size
    )) {
        $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        throw "$Label disposition failed with Win32 error $nativeCode"
    }
}

function Publish-BridgeTargetedCanonicalProjection {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [AllowEmptyCollection()] [byte[]] $Bytes,
        [AllowNull()] [object] $OldLease = $null
    )

    Initialize-BridgeTargetedReplayNative
    $parent = Split-Path -Parent $Path
    $leaf = Split-Path -Leaf $Path
    $temporaryPath = Join-Path $parent (
        '.{0}.targeted-replay.{1}.{2}.tmp' -f
        $leaf,
        $PID,
        [guid]::NewGuid().ToString('N')
    )
    $temporaryLease = $null
    $verificationLease = $null
    $finalLease = $null
    $renamed = $false
    try {
        Write-NewBridgeFileDurably -Path $temporaryPath -Bytes $Bytes
        # DELETE access permits an exact handle rename; omitting share-delete
        # blocks leaf replacement while the temporary identity is audited.
        $temporaryLease = Open-BridgeTargetedCanonicalLease `
            -Path $temporaryPath -DesiredAccess 2147549184 -ShareMode 1
        [byte[]]$temporaryBytes = Read-BridgeTargetedCanonicalBytes `
            -Lease $temporaryLease
        if (-not (Test-BridgeBytesEqual -Left $temporaryBytes -Right $Bytes)) {
            throw 'targeted canonical temporary projection verification failed'
        }
        $temporaryInformation = Get-BridgeTargetedHandleInformation `
            -Handle $temporaryLease.Stream.SafeFileHandle
        if ($temporaryInformation.NumberOfLinks -ne 1) {
            throw 'targeted canonical temporary projection acquired another hard-link name'
        }

        if ($null -eq $OldLease) {
            if (Test-Path -LiteralPath $Path) {
                throw 'targeted canonical path appeared after the empty snapshot'
            }
        } else {
            Assert-BridgeTargetedCanonicalPathMatchesLease `
                -Path $Path -ExpectedLease $OldLease
        }
        Invalidate-BridgeAppendValidationCheckpoint `
            -CanonicalPath $Path -Reason 'targeted-spool-copy-on-write'

        # Recheck the exact source identity immediately before the one atomic
        # handle rename. The no-delete source share blocks leaf replacement;
        # this final check rejects an added hard-link name.
        $temporaryInformation = Get-BridgeTargetedHandleInformation `
            -Handle $temporaryLease.Stream.SafeFileHandle
        if ($temporaryInformation.NumberOfLinks -ne 1) {
            throw 'targeted canonical temporary projection acquired another hard-link name'
        }

        $renameError = 0
        if (-not [WaggleDance.BridgeTargetedReplayNative]::RenameFileByHandle(
            $temporaryLease.Stream.SafeFileHandle,
            [System.IO.Path]::GetFullPath($Path),
            ($null -ne $OldLease),
            [ref]$renameError
        )) {
            throw "targeted canonical atomic publish failed with Win32 error $renameError"
        }
        $renamed = $true

        # Pin and verify the published name before releasing either source
        # identity. A crash before the rename leaves the old canonical intact;
        # a crash after it exposes only the already-flushed complete projection.
        $verificationLease = Open-BridgeTargetedCanonicalLease `
            -Path $Path -ShareMode 5
        if (-not (Test-BridgeTargetedLeaseIdentityEqual `
            -Left $verificationLease -Right $temporaryLease)) {
            throw 'published targeted canonical path names a different identity'
        }
        [byte[]]$publishedBytes = Read-BridgeTargetedCanonicalBytes `
            -Lease $verificationLease
        if (-not (Test-BridgeBytesEqual -Left $publishedBytes -Right $Bytes)) {
            throw 'published targeted canonical projection verification failed'
        }

        Close-BridgeTargetedCanonicalLease -Lease $OldLease
        Close-BridgeTargetedCanonicalLease -Lease $temporaryLease
        $temporaryLease = $null
        $finalLease = Open-BridgeTargetedCanonicalLease `
            -Path $Path -ShareMode 1
        if (-not (Test-BridgeTargetedLeaseIdentityEqual `
            -Left $finalLease -Right $verificationLease)) {
            throw 'targeted canonical identity changed before final pin'
        }
        $finalInformation = Get-BridgeTargetedHandleInformation `
            -Handle $finalLease.Stream.SafeFileHandle
        if ($finalInformation.NumberOfLinks -ne 1) {
            throw 'published targeted canonical projection has another hard-link name'
        }
        Close-BridgeTargetedCanonicalLease -Lease $verificationLease
        $verificationLease = $null
        $result = $finalLease
        $finalLease = $null
        return $result
    } finally {
        Close-BridgeTargetedCanonicalLease -Lease $finalLease
        Close-BridgeTargetedCanonicalLease -Lease $verificationLease
        if ($null -ne $temporaryLease) {
            if (-not $renamed) {
                try {
                    Set-BridgeTargetedLeaseDeletePending `
                        -Lease $temporaryLease `
                        -Label 'targeted canonical temporary projection'
                } catch {
                    Write-Warning (
                        'could not remove an unpublished targeted canonical ' +
                        "temporary projection: $($_.Exception.Message)"
                    )
                }
            }
            Close-BridgeTargetedCanonicalLease -Lease $temporaryLease
        }
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
        throw 'targeted spool lease is not active'
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
        throw 'targeted spool identity changed before replay'
    }
    [byte[]]$currentBytes = Read-BridgeTargetedLeaseBytes -Stream $lease.Stream
    if (-not (Test-BridgeBytesEqual -Left $currentBytes -Right $Wal.Bytes)) {
        throw 'targeted spool changed before replay'
    }
    if ((Get-BridgeSha256Hex -Bytes $currentBytes) -cne $ExpectedSha256) {
        throw 'targeted spool SHA-256 changed before replay'
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
        throw "targeted spool disposition failed with Win32 error $nativeCode"
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
        -Path $spoolDir -Label 'targeted spool directory' -Directory
    if (-not (Test-Path -LiteralPath $ArchiveDir)) {
        [void](New-Item -ItemType Directory -Path $ArchiveDir -Force)
    }
    Assert-BridgeTargetedPlainPath `
        -Path $ArchiveDir -Label 'targeted archive directory' -Directory
    $destination = Join-Path $ArchiveDir $Wal.File.Name
    if (Test-Path -LiteralPath $destination) {
        $destination = Join-Path $ArchiveDir (
            '{0}.archive-collision.{1}' -f
            $Wal.File.Name,
            [guid]::NewGuid().ToString('N')
        )
    }
    $archiveStream = $null
    $archiveCommitted = $false
    try {
        # Retain a share-none archive identity from CreateNew through durable
        # verification and exact-source disposition. No path-based reread or
        # cleanup can be redirected to a replacement identity in that window.
        $archiveStream = New-Object System.IO.FileStream(
            $destination,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
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
                throw 'targeted archive ended during verification'
            }
            $archiveOffset += $archiveRead
        }
        if (-not (Test-BridgeBytesEqual -Left $archiveBytes -Right $Wal.Bytes)) {
            throw 'targeted archive bytes do not match the audited WAL'
        }
        if ((Get-BridgeSha256Hex -Bytes $archiveBytes) -cne $ExpectedSha256) {
            throw 'targeted archive SHA-256 does not match the audited WAL'
        }
        Assert-TargetedSpoolUnchanged `
            -Wal $Wal -ExpectedSha256 $ExpectedSha256
        # The archive is an independent durable copy of the audited bytes.
        # Only after it is verified may the no-write/no-delete source lease be
        # released and the active spool leaf consumed.
        Set-BridgeTargetedSpoolDeletePending `
            -Wal $Wal -ExpectedSha256 $ExpectedSha256
        $archiveCommitted = $true
        $archiveStream.Dispose()
        $archiveStream = $null
        return $true
    } finally {
        if ($null -ne $archiveStream) { $archiveStream.Dispose() }
        if (-not $archiveCommitted -and (Test-Path -LiteralPath $destination -PathType Leaf)) {
            Remove-Item -LiteralPath $destination -Force -ErrorAction SilentlyContinue
        }
    }
}

function Add-BridgeKnownLegacyUnknownTypeKey {
    param(
        [Parameter(Mandatory)] [string] $ExactLine,
        [Parameter(Mandatory)] [string] $JsonLine,
        [Parameter(Mandatory)] [string] $Label,
        [Parameter(Mandatory)] [System.Text.UTF8Encoding] $StrictUtf8,
        [Parameter(Mandatory)] [AllowEmptyCollection()]
        [System.Collections.Generic.HashSet[string]] $Keys
    )

    if (
        (Get-BridgeSha256Hex -Bytes $StrictUtf8.GetBytes($JsonLine)) -cne
        $knownLegacyUnknownTypeRowSha256
    ) {
        return $false
    }
    try { $eventObject = $JsonLine | ConvertFrom-Json -ErrorAction Stop }
    catch { throw "$Label known historical row has malformed JSON" }
    if (-not ($eventObject -is [Management.Automation.PSCustomObject])) {
        throw "$Label known historical row is not a JSON object"
    }
    $actual = @(
        [string](Get-BridgeExactJsonPropertyValue `
            -Object $eventObject -Name 'agent' -Label $Label),
        [string](Get-BridgeExactJsonPropertyValue `
            -Object $eventObject -Name 'task_id' -Label $Label),
        (ConvertTo-BridgeFingerprintTimestamp `
            -Value (Get-BridgeExactJsonPropertyValue `
                -Object $eventObject -Name 'ts_utc' -Label $Label) `
            -Label $Label),
        [string](Get-BridgeExactJsonPropertyValue `
            -Object $eventObject -Name 'type' -Label $Label),
        [string](Get-BridgeExactJsonPropertyValue `
            -Object $eventObject -Name 'status' -Label $Label)
    )
    for ($fieldIndex = 0; $fieldIndex -lt 5; $fieldIndex++) {
        if (
            $actual[$fieldIndex] -cne
            $knownLegacyUnknownTypeFingerprint[$fieldIndex]
        ) {
            throw "$Label known historical row fingerprint does not match"
        }
    }
    [byte[]]$canonicalRowBytes = $StrictUtf8.GetBytes(
        $ExactLine + [char]10
    )
    [void]$Keys.Add((Get-BridgeSha256Hex -Bytes $canonicalRowBytes))
    return $true
}

function Read-BridgeWalFile {
    param(
        [Parameter(Mandatory)] [System.IO.FileInfo] $File,
        [AllowNull()] [byte[]] $Bytes = $null
    )

    [byte[]]$bytes = if ($PSBoundParameters.ContainsKey('Bytes')) {
        $Bytes
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

function Join-BridgeByteArrays {
    param(
        [Parameter(Mandatory)] [AllowEmptyCollection()] [byte[]] $Left,
        [Parameter(Mandatory)] [AllowEmptyCollection()] [byte[]] $Right
    )

    $combinedLength = [int64]$Left.Length + [int64]$Right.Length
    if ($combinedLength -gt [int]::MaxValue) {
        throw 'targeted canonical projection exceeds the supported byte length'
    }
    [byte[]]$combined = New-Object byte[] ([int]$combinedLength)
    if ($Left.Length -gt 0) {
        [Array]::Copy($Left, 0, $combined, 0, $Left.Length)
    }
    if ($Right.Length -gt 0) {
        [Array]::Copy($Right, 0, $combined, $Left.Length, $Right.Length)
    }
    return ,$combined
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
        if (
            $jsonLine.Contains([string][char]13) -and
            (Add-BridgeKnownLegacyBareCrKeys `
                -JsonLine $jsonLine -Label "$Label row $($index + 1)" `
                -StrictUtf8 $strictUtf8 -Keys $Keys)
        ) {
            continue
        }
        if (
            $jsonLine.Contains('totally-bogus-typo-type') -and
            (Add-BridgeKnownLegacyUnknownTypeKey `
                -ExactLine $exactLine -JsonLine $jsonLine `
                -Label "$Label row $($index + 1)" `
                -StrictUtf8 $strictUtf8 -Keys $Keys)
        ) {
            continue
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

function Get-BridgeCanonicalKeys {
    param([Parameter(Mandatory)] [string] $Path)

    $keys = New-Object 'System.Collections.Generic.HashSet[string]'
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return ,$keys }
    [byte[]]$bytes = [System.IO.File]::ReadAllBytes($Path)
    Add-BridgeCanonicalKeysFromBytes `
        -Bytes $bytes -Label 'canonical bridge log' -Keys $keys
    return ,$keys
}

function Get-BridgeTargetedCanonicalBase {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [AllowEmptyCollection()] [object[]] $WalRecords,
        [AllowNull()] [object] $CanonicalLease = $null
    )

    [byte[]]$canonicalBytes = if ($null -eq $CanonicalLease) {
        New-Object byte[] 0
    } else {
        Read-BridgeTargetedCanonicalBytes -Lease $CanonicalLease
    }
    if (
        $canonicalBytes.Length -eq 0 -or
        $canonicalBytes[$canonicalBytes.Length - 1] -eq 10
    ) {
        return [pscustomobject]@{
            Bytes = $canonicalBytes
            RequiresPublish = $false
            QuarantinePath = ''
        }
    }

    $lastLf = -1
    for ($index = $canonicalBytes.Length - 1; $index -ge 0; $index--) {
        if ($canonicalBytes[$index] -eq 10) { $lastLf = $index; break }
    }
    $tailStart = $lastLf + 1
    $tailLength = $canonicalBytes.Length - $tailStart
    [byte[]]$tailBytes = New-Object byte[] $tailLength
    [Array]::Copy($canonicalBytes, $tailStart, $tailBytes, 0, $tailLength)
    [byte[]]$prefixBytes = New-Object byte[] $tailStart
    if ($tailStart -gt 0) {
        [Array]::Copy($canonicalBytes, 0, $prefixBytes, 0, $tailStart)
        $prefixKeys = New-Object 'System.Collections.Generic.HashSet[string]'
        Add-BridgeCanonicalKeysFromBytes `
            -Bytes $prefixBytes -Label 'canonical bridge log prefix' `
            -Keys $prefixKeys
    }

    if ($tailBytes.Length -lt 32) {
        throw 'canonical bridge log has an ambiguous short unterminated tail; no changes made'
    }
    $boundCount = 0
    foreach ($wal in $WalRecords) {
        foreach ($row in $wal.Rows) {
            if (Test-BridgeBytesStartWith -Bytes $row.Bytes -Prefix $tailBytes) {
                $boundCount++
            }
        }
    }
    if ($boundCount -ne 1) {
        throw 'canonical bridge log has an unbound unterminated tail; no changes made'
    }
    if ($DryRun) {
        throw 'dry run found a WAL-bound torn tail; no repair was performed'
    }

    $quarantineDir = Join-Path $spoolDir 'quarantine'
    if (-not (Test-Path -LiteralPath $quarantineDir -PathType Container)) {
        [void](New-Item -ItemType Directory -Path $quarantineDir)
    }
    $quarantinePath = Join-Path $quarantineDir (
        'canonical-torn-tail-{0}-{1}-{2}.bin' -f
        [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfff'),
        $PID,
        [guid]::NewGuid().ToString('N')
    )
    Write-NewBridgeFileDurably -Path $quarantinePath -Bytes $tailBytes
    Write-Warning (
        'staged WAL-bound torn canonical tail for copy-on-write repair; ' +
        "quarantine retained: $quarantinePath"
    )
    return [pscustomobject]@{
        Bytes = $prefixBytes
        RequiresPublish = $true
        QuarantinePath = $quarantinePath
    }
}

function Repair-BridgeTornTailIfBound {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [AllowEmptyCollection()] [object[]] $WalRecords
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return '' }
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

    if ($tailBytes.Length -lt 32) {
        throw 'canonical bridge log has an ambiguous short unterminated tail; no changes made'
    }
    $boundCount = 0
    foreach ($wal in $WalRecords) {
        foreach ($row in $wal.Rows) {
            if (Test-BridgeBytesStartWith -Bytes $row.Bytes -Prefix $tailBytes) {
                $boundCount++
            }
        }
    }
    if ($boundCount -ne 1) {
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
$targetedSpoolLease = $null
$targetedCanonicalLease = $null
$targetedCanonicalBase = $null
$targetedDirectoryLeases = New-Object 'System.Collections.Generic.List[object]'
$targetedRootDirectoryLease = $null
$targetedSpoolDirectoryLease = $null
$targetedSharedDirectoryLease = $null
$targetedSharedAnchorLease = $null
$targetedSharedAnchorPath = ''
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

    if ($targetedReplay) {
        $targetedRootDirectoryLease = Open-BridgeTargetedDirectoryLease `
            -Path $BridgeRoot -Label 'targeted bridge root'
        $targetedDirectoryLeases.Add($targetedRootDirectoryLease)
        $targetedSpoolDirectoryLease = Open-BridgeTargetedDirectoryLease `
            -Path $spoolDir -Label 'targeted spool directory'
        $targetedDirectoryLeases.Add($targetedSpoolDirectoryLease)
    }

    # AppendV1 remains owned across WAL discovery/recovery, live-log scan,
    # exact-record dedup, transactional append, and archive.
    $pendingFiles = @(if (-not $targetedReplay) {
        Get-ChildItem -LiteralPath $spoolDir `
            -Filter '.failed-append-*.jsonl.pending' -File -Force `
            -ErrorAction Stop |
            Sort-Object Name
    })
    $discoveredFinalFiles = @(
        Get-ChildItem -LiteralPath $spoolDir `
            -Filter 'failed-append-*.jsonl' -File -Force `
            -ErrorAction Stop |
            Sort-Object Name
    )
    $finalFiles = @(if ($targetedReplay) {
        $discoveredFinalFiles |
            Where-Object { $_.Name -ceq $SpoolFile }
    } else {
        $discoveredFinalFiles
    })
    if ($targetedReplay) {
        if ($finalFiles.Count -ne 1) {
            throw 'targeted spool file was not found by exact leaf name'
        }
        if (
            ($finalFiles[0].Attributes -band
                [System.IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            throw 'targeted spool file must not be a reparse point'
        }
        $targetedPendingPath = Join-Path $spoolDir ('.' + $SpoolFile + '.pending')
        if (Test-Path -LiteralPath $targetedPendingPath) {
            throw 'targeted spool has a pending counterpart'
        }
    }
    if ($pendingFiles.Count -eq 0 -and $finalFiles.Count -eq 0) {
        Write-Output 'spool empty; nothing to replay'
        return
    }

    $finalRecords = New-Object 'System.Collections.Generic.List[object]'
    $finalByPath = @{}
    foreach ($file in $finalFiles) {
        if ($targetedReplay) {
            $targetedSpoolLease = Open-BridgeTargetedSpoolLease `
                -Path $file.FullName
            [byte[]]$auditedSpoolBytes = $targetedSpoolLease.Bytes
            $actualSpoolSha256 = Get-BridgeSha256Hex -Bytes $auditedSpoolBytes
            if ($actualSpoolSha256 -cne $ExpectedSpoolSha256) {
                throw 'targeted spool SHA-256 does not match ExpectedSpoolSha256'
            }
        }
        $record = if ($targetedReplay) {
            Read-BridgeWalFile -File $file -Bytes $auditedSpoolBytes
        } else {
            Read-BridgeWalFile -File $file
        }
        if (
            $targetedReplay -and
            -not (Test-BridgeBytesEqual `
                -Left $auditedSpoolBytes -Right $record.Bytes)
        ) {
            throw 'targeted spool changed during validation'
        }
        if ($targetedReplay) {
            Add-Member -InputObject $record -NotePropertyName TargetedLease `
                -NotePropertyValue $targetedSpoolLease
        }
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

    if ($targetedReplay) {
        $sharedDir = Split-Path -Parent $eventsPath
        if (-not (Test-Path -LiteralPath $sharedDir -PathType Container)) {
            if ($DryRun) {
                throw 'targeted dry run requires an existing shared directory'
            }
            [void](New-Item -ItemType Directory -Path $sharedDir)
        }
        $targetedSharedDirectoryLease = Open-BridgeTargetedDirectoryLease `
            -Path $sharedDir -Label 'targeted shared directory'
        $targetedDirectoryLeases.Add($targetedSharedDirectoryLease)

        if (-not $DryRun) {
            $targetedSharedAnchorPath = Join-Path $sharedDir (
                '.targeted-replay-anchor.{0}.{1}.tmp' -f
                $PID,
                [guid]::NewGuid().ToString('N')
            )
            [byte[]]$anchorBytes = [Text.Encoding]::ASCII.GetBytes(
                "targeted-replay-anchor`n"
            )
            Write-NewBridgeFileDurably `
                -Path $targetedSharedAnchorPath -Bytes $anchorBytes
            $targetedSharedAnchorLease = Open-BridgeTargetedCanonicalLease `
                -Path $targetedSharedAnchorPath `
                -DesiredAccess 2147549184 -ShareMode 1
            $targetedSharedDirectoryLease.Handle.Dispose()
            [void]$targetedDirectoryLeases.Remove($targetedSharedDirectoryLease)
            $targetedSharedDirectoryLease = $null
        }

        if (Test-Path -LiteralPath $eventsPath -PathType Leaf) {
            $targetedCanonicalLease = Open-BridgeTargetedCanonicalLease `
                -Path $eventsPath -ShareMode 5
        }

        if (-not $DryRun) {
            foreach ($directoryPlan in @(
                [pscustomobject]@{
                    Path = $archiveDir
                    Label = 'targeted archive directory'
                },
                [pscustomobject]@{
                    Path = (Join-Path $spoolDir 'quarantine')
                    Label = 'targeted quarantine directory'
                }
            )) {
                if (-not (Test-Path -LiteralPath $directoryPlan.Path -PathType Container)) {
                    [void](New-Item -ItemType Directory -Path $directoryPlan.Path)
                }
                $targetedDirectoryLeases.Add((Open-BridgeTargetedDirectoryLease `
                    -Path $directoryPlan.Path -Label $directoryPlan.Label))
            }
        }

    }

    if ($targetedReplay) {
        Assert-TargetedSpoolUnchanged `
            -Wal $finalRecords[0] -ExpectedSha256 $ExpectedSpoolSha256
        if ($null -ne $targetedCanonicalLease) {
            Assert-BridgeTargetedCanonicalIdentity `
                -Lease $targetedCanonicalLease
        }
        $targetedCanonicalBase = Get-BridgeTargetedCanonicalBase `
            -Path $eventsPath -WalRecords $bindingRecords.ToArray() `
            -CanonicalLease $targetedCanonicalLease
    } else {
        [void](Repair-BridgeTornTailIfBound `
            -Path $eventsPath -WalRecords $bindingRecords.ToArray())
    }
    if ($targetedReplay -and $null -ne $targetedCanonicalLease) {
        Assert-BridgeTargetedCanonicalIdentity -Lease $targetedCanonicalLease
    }

    # Validate the canonical stream before promoting or archiving pending WALs.
    # Strict decoding failures therefore leave every spool path unchanged.
    if ($targetedReplay) {
        $existingKeys = New-Object 'System.Collections.Generic.HashSet[string]'
        [byte[]]$targetedCanonicalBytes = $targetedCanonicalBase.Bytes
        if ($targetedCanonicalBytes.Length -gt 0) {
            Add-BridgeCanonicalKeysFromBytes `
                -Bytes $targetedCanonicalBytes -Label 'canonical bridge log' `
                -Keys $existingKeys
        }
    } else {
        $existingKeys = Get-BridgeCanonicalKeys -Path $eventsPath
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
            if ($targetedReplay) {
                Assert-TargetedSpoolUnchanged `
                    -Wal $wal -ExpectedSha256 $ExpectedSpoolSha256
                if ($targetedCanonicalBase.RequiresPublish -and -not $DryRun) {
                    $replacementLease = Publish-BridgeTargetedCanonicalProjection `
                        -Path $eventsPath -Bytes $targetedCanonicalBase.Bytes `
                        -OldLease $targetedCanonicalLease
                    $targetedCanonicalLease = $replacementLease
                    $targetedCanonicalBase.RequiresPublish = $false
                }
            }
            if ($DryRun) {
                Write-Output "would archive as exact duplicate: $($wal.File.Name)"
            } else {
                if ($targetedReplay) {
                    [void](Copy-TargetedSpoolToArchive `
                        -Wal $wal -ArchiveDir $archiveDir `
                        -ExpectedSha256 $ExpectedSpoolSha256)
                } else {
                    [void](Move-SpoolToArchive `
                        -File $wal.File -ArchiveDir $archiveDir)
                }
            }
            continue
        }
        if ($DryRun) {
            if ($targetedReplay) {
                Assert-TargetedSpoolUnchanged `
                    -Wal $wal -ExpectedSha256 $ExpectedSpoolSha256
            }
            Write-Output "would replay: $($wal.File.Name)"
            $replayed++
            continue
        }
        [byte[]]$appendBytes = Join-BridgeWalRowBytes -Rows $rowsToAppend.ToArray()
        try {
            if ($targetedReplay) {
                Assert-TargetedSpoolUnchanged `
                    -Wal $wal -ExpectedSha256 $ExpectedSpoolSha256
                [byte[]]$projectedBytes = Join-BridgeByteArrays `
                    -Left $targetedCanonicalBase.Bytes -Right $appendBytes
                $replacementLease = Publish-BridgeTargetedCanonicalProjection `
                    -Path $eventsPath -Bytes $projectedBytes `
                    -OldLease $targetedCanonicalLease
                $targetedCanonicalLease = $replacementLease
                $targetedCanonicalBase.Bytes = $projectedBytes
                $targetedCanonicalBase.RequiresPublish = $false
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
        if ($targetedReplay) {
            Assert-TargetedSpoolUnchanged `
                -Wal $wal -ExpectedSha256 $ExpectedSpoolSha256
        }
        foreach ($row in $rowsToAppend) {
            [void]$existingKeys.Add([string]$row.Key)
        }
        if ($targetedReplay) {
            [void](Copy-TargetedSpoolToArchive `
                -Wal $wal -ArchiveDir $archiveDir `
                -ExpectedSha256 $ExpectedSpoolSha256)
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
    Close-BridgeTargetedSpoolLease -Lease $targetedSpoolLease
    Close-BridgeTargetedCanonicalLease -Lease $targetedCanonicalLease
    $targetedCanonicalLease = $null
    if ($null -ne $targetedSharedAnchorLease) {
        try {
            Set-BridgeTargetedLeaseDeletePending `
                -Lease $targetedSharedAnchorLease `
                -Label 'targeted shared-directory anchor'
        } catch {
            Write-Warning (
                'could not remove targeted shared-directory anchor: ' +
                $_.Exception.Message
            )
        }
        Close-BridgeTargetedCanonicalLease -Lease $targetedSharedAnchorLease
        $targetedSharedAnchorLease = $null
    }
    for ($leaseIndex = $targetedDirectoryLeases.Count - 1; $leaseIndex -ge 0; $leaseIndex--) {
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
}
