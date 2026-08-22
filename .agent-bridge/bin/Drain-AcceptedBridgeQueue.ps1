#requires -Version 5.1
<#
.SYNOPSIS
    Drain complete accepted-v1 ready WALs without touching legacy backlog.

.DESCRIPTION
    Enumerates only spool/accepted-v1/ready and invokes accepted-target replay
    once per strict bridge-wal-v1-<32 lowercase hex>.jsonl leaf. Each leaf is
    hashed immediately before invocation, and one failure does not prevent
    later ready leaves from being attempted.

    It also examines only strict accepted-v1/pending leaves older than the age
    gate. A read lease which denies write access distinguishes an orphan from a
    live writer. Complete strict one-row WALs are published no-replace and
    write-through to ready, then verified before normal targeted replay.
    Younger or sharing-violating files are skipped; malformed and incomplete
    files remain pending and are reported as failures.

.PARAMETER BridgeRoot
    Bridge runtime root. Defaults to AGENT_BRIDGE_RUNTIME_ROOT or the parent of
    this script's directory.

.PARAMETER DryRun
    Validate and report each ready WAL without appending or archiving it.

.PARAMETER ReceiptJson
    Emit one compressed structured summary instead of human-readable output.

.PARAMETER DeferCanonicalProof
    With DryRun and ReceiptJson, validate retained ready WAL and marker bytes
    without launching targeted replay per leaf. The caller must prove every
    reported digest against one stable canonical-history snapshot.

.PARAMETER PendingMinAgeSeconds
    Minimum pending-file age before orphan recovery is attempted. Defaults to
    60 seconds. Tests and an operator-directed recovery may explicitly use 0.
#>
[CmdletBinding()]
param(
    [string] $BridgeRoot = '',
    [switch] $DryRun,
    [switch] $ReceiptJson,
    [switch] $DeferCanonicalProof,
    [ValidateRange(0, 86400)] [int] $PendingMinAgeSeconds = 60
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ($DeferCanonicalProof -and (-not $DryRun -or -not $ReceiptJson)) {
    throw 'DeferCanonicalProof requires DryRun and ReceiptJson'
}

if (-not $BridgeRoot) {
    $BridgeRoot = if ($env:AGENT_BRIDGE_RUNTIME_ROOT) {
        [string]$env:AGENT_BRIDGE_RUNTIME_ROOT
    } else {
        Split-Path -Parent $PSScriptRoot
    }
}

$replayScript = Join-Path $PSScriptRoot 'Restore-BridgeSpool.ps1'
$spoolDir = Join-Path $BridgeRoot 'spool'
$acceptedDir = Join-Path $spoolDir 'accepted-v1'
$readyDir = Join-Path $acceptedDir 'ready'
$pendingDir = Join-Path $acceptedDir 'pending'
$replayedDir = Join-Path $acceptedDir 'replayed'
$results = New-Object 'System.Collections.Generic.List[object]'
$drained = 0
$alreadyDelivered = 0
$failed = 0
$readySeen = 0
$pendingSeen = 0
$pendingPromoted = 0
$pendingSkipped = 0
$pendingFailed = 0
$wouldDrain = 0
$blockedReadyLeaves = New-Object 'System.Collections.Generic.HashSet[string]'
$knownEventTypes = @(
    'status', 'intent', 'claim', 'release', 'message', 'finding',
    'decision', 'test', 'blocked', 'handoff', 'done', 'heartbeat',
    'wake_request', 'liveness'
)

function Initialize-BridgeAcceptedQueueNative {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'accepted pending recovery requires Windows write-through publication'
    }
    if ('WaggleDance.BridgeAcceptedQueueNative' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace WaggleDance {
    [StructLayout(LayoutKind.Sequential)]
    public struct BridgeAcceptedQueueFileInformation {
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
    public struct BridgeAcceptedQueueFileDisposition {
        [MarshalAs(UnmanagedType.Bool)]
        public bool DeleteFile;
    }

    public static class BridgeAcceptedQueueNative {
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
            out BridgeAcceptedQueueFileInformation fileInformation
        );

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool SetFileInformationByHandle(
            IntPtr fileHandle,
            int fileInformationClass,
            ref BridgeAcceptedQueueFileDisposition fileInformation,
            uint bufferSize
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

function Get-BridgeQueueSha256Hex {
    param([Parameter(Mandatory)] [AllowEmptyCollection()] [byte[]] $Bytes)

    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString(
            $sha.ComputeHash($Bytes)
        )).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Test-BridgeQueueBytesEqual {
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

function Open-BridgeQueueRecoveryBlockMarkerLease {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $Leaf,
        [switch] $DeleteAccess
    )

    $lease = $null
    try {
        $lease = Open-BridgeQueuePlainFileLease `
            -Path $Path -Label 'pending recovery block marker' `
            -DeleteAccess:$DeleteAccess.IsPresent -MaxBytes (64 * 1024)
        $strictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
        try { $text = $strictUtf8.GetString($lease.Bytes) }
        catch { throw "pending recovery block marker is not strict UTF-8: $($_.Exception.Message)" }
        try { $marker = $text | ConvertFrom-Json -ErrorAction Stop }
        catch { throw "pending recovery block marker is malformed: $($_.Exception.Message)" }
        if (
            -not ($marker -is [System.Management.Automation.PSCustomObject]) -or
            [string]$marker.schema -cne
                'waggledance.bridge.accepted-pending-block.v1' -or
            [string]$marker.wal_leaf -cne $Leaf -or
            [string]$marker.expected_sha256 -cnotmatch '^[0-9a-f]{64}$'
        ) {
            throw 'pending recovery block marker is not a valid WAL binding'
        }
        $lease | Add-Member -NotePropertyName Marker -NotePropertyValue $marker
        return $lease
    } catch {
        if ($null -ne $lease -and $null -ne $lease.Stream) {
            $lease.Stream.Dispose()
        }
        throw
    }
}

function Test-BridgeQueueSharingViolation {
    param([Parameter(Mandatory)] [System.Exception] $Exception)

    $current = $Exception
    while ($null -ne $current) {
        $nativeCode = ([int64]$current.HResult -band 0xFFFF)
        if ($nativeCode -in @(32, 33)) { return $true }
        $current = $current.InnerException
    }
    return $false
}

function Read-BridgeQueueLeaseBytes {
    param([Parameter(Mandatory)] [System.IO.FileStream] $Stream)

    if ($Stream.Length -gt [int]::MaxValue) {
        throw 'accepted pending WAL exceeds the supported byte length'
    }
    [void]$Stream.Seek(0, [System.IO.SeekOrigin]::Begin)
    [byte[]]$bytes = New-Object byte[] ([int]$Stream.Length)
    $offset = 0
    while ($offset -lt $bytes.Length) {
        $read = $Stream.Read($bytes, $offset, $bytes.Length - $offset)
        if ($read -le 0) { throw 'accepted pending WAL ended during read' }
        $offset += $read
    }
    return ,$bytes
}

function Open-BridgeQueuePlainFileLease {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $Label,
        [switch] $DeleteAccess,
        [ValidateRange(0, [int]::MaxValue)] [int64] $MaxBytes = [int]::MaxValue
    )

    Initialize-BridgeAcceptedQueueNative
    $handle = $null
    $stream = $null
    try {
        # GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING, and
        # FILE_FLAG_OPEN_REPARSE_POINT pin one plain single-link identity and
        # deny write/delete replacement until the caller releases the stream.
        [uint32]$desiredAccess = [uint32]2147483648
        if ($DeleteAccess) {
            $desiredAccess = [uint32]($desiredAccess -bor [uint32]65536)
        }
        $handle = [WaggleDance.BridgeAcceptedQueueNative]::CreateFileW(
            [System.IO.Path]::GetFullPath($Path),
            $desiredAccess,
            [uint32]1,
            [IntPtr]::Zero,
            [uint32]3,
            [uint32]2097152,
            [IntPtr]::Zero
        )
        if ($handle.IsInvalid) {
            $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            throw "$Label lease failed with Win32 error $nativeCode"
        }
        $information = New-Object WaggleDance.BridgeAcceptedQueueFileInformation
        if (-not [WaggleDance.BridgeAcceptedQueueNative]::GetFileInformationByHandle(
            $handle.DangerousGetHandle(),
            [ref]$information
        )) {
            $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            throw "$Label identity failed with Win32 error $nativeCode"
        }
        if (
            $information.NumberOfLinks -ne 1 -or
            ($information.FileAttributes -band
                [uint32][System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
            ($information.FileAttributes -band
                [uint32][System.IO.FileAttributes]::Directory) -ne 0
        ) {
            throw "$Label must be a plain single-link file"
        }
        $stream = New-Object System.IO.FileStream(
            $handle,
            [System.IO.FileAccess]::Read
        )
        $handle = $null
        if ($stream.Length -gt $MaxBytes) {
            throw "$Label exceeds the supported byte length"
        }
        return [pscustomobject]@{
            Stream = $stream
            Bytes = Read-BridgeQueueLeaseBytes -Stream $stream
            DeleteAccess = $DeleteAccess.IsPresent
        }
    } catch {
        if ($null -ne $stream) { $stream.Dispose() }
        if ($null -ne $handle) { $handle.Dispose() }
        throw
    }
}

function Remove-BridgeQueueLeasedFile {
    param(
        [Parameter(Mandatory)] $Lease,
        [Parameter(Mandatory)] [string] $Label
    )

    if (-not $Lease.DeleteAccess -or $null -eq $Lease.Stream) {
        throw "$Label lease does not grant pinned deletion"
    }
    $disposition = New-Object WaggleDance.BridgeAcceptedQueueFileDisposition
    $disposition.DeleteFile = $true
    $size = [Runtime.InteropServices.Marshal]::SizeOf($disposition)
    if (-not [WaggleDance.BridgeAcceptedQueueNative]::SetFileInformationByHandle(
        $Lease.Stream.SafeFileHandle.DangerousGetHandle(),
        4,
        [ref]$disposition,
        [uint32]$size
    )) {
        $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        $nativeError = New-Object System.ComponentModel.Win32Exception($nativeCode)
        throw "$Label pinned deletion failed: $nativeCode ($($nativeError.Message))"
    }
    $Lease.Stream.Dispose()
    $Lease.Stream = $null
}

function Assert-BridgeQueueOneRowWal {
    param(
        [Parameter(Mandatory)] [AllowEmptyCollection()] [byte[]] $Bytes,
        [Parameter(Mandatory)] [string] $Label
    )

    if ($Bytes.Length -eq 0 -or $Bytes[$Bytes.Length - 1] -ne 10) {
        throw "$Label is empty or does not end with LF"
    }
    $strictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
    try { $text = $strictUtf8.GetString($Bytes) }
    catch { throw "$Label is not strict UTF-8: $($_.Exception.Message)" }
    $lines = $text.Split([char]10)
    if ($lines.Count -ne 2 -or $lines[1] -cne '') {
        throw "$Label must contain exactly one complete JSONL row"
    }
    $jsonLine = [string]$lines[0]
    if ($jsonLine.EndsWith([string][char]13)) {
        $jsonLine = $jsonLine.Substring(0, $jsonLine.Length - 1)
    }
    if ([string]::IsNullOrWhiteSpace($jsonLine)) {
        throw "$Label contains a blank row"
    }
    try { $eventObject = $jsonLine | ConvertFrom-Json -ErrorAction Stop }
    catch { throw "$Label has malformed JSON: $($_.Exception.Message)" }
    if (-not ($eventObject -is [System.Management.Automation.PSCustomObject])) {
        throw "$Label row is not a JSON object"
    }
    foreach ($field in @('type', 'agent', 'task_id', 'status')) {
        $property = $eventObject.PSObject.Properties[$field]
        if ($null -eq $property -or -not ($property.Value -is [string])) {
            throw "$Label core field '$field' is missing or not a string"
        }
    }
    if ($knownEventTypes -cnotcontains [string]$eventObject.type) {
        throw "$Label has an unknown event type"
    }
    if ([string]$eventObject.agent -cnotmatch '^[a-z][a-z0-9_-]{1,32}$') {
        throw "$Label has an invalid agent id"
    }
}

function Assert-PlainBridgeQueueDirectory {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $Label
    )

    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (-not $item.PSIsContainer) { throw "$Label must be a directory" }
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "$Label must not be a reparse point"
    }
}

function Open-BridgeQueuePlainDirectoryLease {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $Label
    )

    Initialize-BridgeAcceptedQueueNative
    $handle = [WaggleDance.BridgeAcceptedQueueNative]::CreateFileW(
        [System.IO.Path]::GetFullPath($Path),
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
        $nativeError = New-Object System.ComponentModel.Win32Exception($nativeCode)
        throw "$Label lease failed: $nativeCode ($($nativeError.Message))"
    }
    try {
        $information = New-Object WaggleDance.BridgeAcceptedQueueFileInformation
        if (-not [WaggleDance.BridgeAcceptedQueueNative]::GetFileInformationByHandle(
            $handle.DangerousGetHandle(),
            [ref]$information
        )) {
            $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            $nativeError = New-Object System.ComponentModel.Win32Exception($nativeCode)
            throw "$Label identity failed: $nativeCode ($($nativeError.Message))"
        }
        if (
            ($information.FileAttributes -band
                [uint32][System.IO.FileAttributes]::Directory) -eq 0 -or
            ($information.FileAttributes -band
                [uint32][System.IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            throw "$Label must be a plain directory, not a reparse point"
        }
        return $handle
    } catch {
        $handle.Dispose()
        throw
    }
}

function Add-BridgeQueueDirectoryLease {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] $Leases,
        [Parameter(Mandatory)] $Seen
    )

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    if (-not $Seen.Add($fullPath)) { return }
    if (-not (Test-Path -LiteralPath $fullPath -PathType Container)) {
        throw "accepted queue directory is missing: $fullPath"
    }
    $Leases.Add((Open-BridgeQueuePlainDirectoryLease `
        -Path $fullPath -Label "accepted queue directory $fullPath"))
}

function Add-BridgeQueueDirectoryChainLeases {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] $Leases,
        [Parameter(Mandatory)] $Seen
    )

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $pathRoot = [System.IO.Path]::GetPathRoot($fullPath)
    Add-BridgeQueueDirectoryLease `
        -Path $pathRoot -Leases $Leases -Seen $Seen
    $cursor = $pathRoot
    $relativePath = $fullPath.Substring($pathRoot.Length)
    foreach ($segment in @($relativePath -split '[\\/]' | Where-Object { $_ })) {
        $cursor = Join-Path $cursor $segment
        Add-BridgeQueueDirectoryLease `
            -Path $cursor -Leases $Leases -Seen $Seen
    }
}

function Close-BridgeQueueDirectoryLeases {
    param([AllowNull()] $Leases)

    if ($null -eq $Leases) { return }
    if ($Leases -isnot [System.Collections.IList]) {
        $Leases = @($Leases)
    }
    for ($index = $Leases.Count - 1; $index -ge 0; $index--) {
        if ($null -ne $Leases[$index]) {
            try { $Leases[$index].Dispose() }
            catch {
                Write-Warning -WarningAction Continue -Message (
                    'accepted queue directory lease close failed: ' +
                    $_.Exception.Message
                )
            }
        }
    }
}

function Write-DrainSummary {
    $summary = [pscustomobject][ordered]@{
        schema = 'waggledance.bridge.accepted-queue-drain.v1'
        bridge_root = [System.IO.Path]::GetFullPath($BridgeRoot)
        ready_seen = [int64]$readySeen
        drained = [int64]$drained
        already_delivered = [int64]$alreadyDelivered
        failed = [int64]$failed
        dry_run = $DryRun.IsPresent
        pending_recovery = 'age-gated-v1'
        pending_min_age_seconds = [int64]$PendingMinAgeSeconds
        pending_seen = [int64]$pendingSeen
        pending_promoted = [int64]$pendingPromoted
        pending_skipped = [int64]$pendingSkipped
        pending_failed = [int64]$pendingFailed
        would_drain = [int64]$wouldDrain
        pending_path = [System.IO.Path]::GetFullPath($pendingDir)
        results = @($results.ToArray())
    }
    if ($ReceiptJson) {
        $summary | ConvertTo-Json -Depth 8 -Compress
    } else {
        Write-Output (
            'accepted queue drain complete: ready={0} drained={1} ' +
            'alreadyDelivered={2} failed={3} dryRun={4}; ' +
            'pendingSeen={5} promoted={6} skipped={7} pendingFailed={8}' -f
            $readySeen,
            $drained,
            $alreadyDelivered,
            $failed,
            $DryRun.IsPresent,
            $pendingSeen,
            $pendingPromoted,
            $pendingSkipped,
            $pendingFailed
        )
    }
}

$queueDirectoryLeases = New-Object 'System.Collections.Generic.List[object]'
$leasedDirectoryPaths = New-Object 'System.Collections.Generic.HashSet[string]' `
    ([System.StringComparer]::OrdinalIgnoreCase)
$publicationMutex = $null
$publicationAcquired = $false
$publicationDirtyAbandoned = $false
try {
    if (-not $DryRun) {
        try {
            $publicationMutex = New-Object System.Threading.Mutex(
                $false,
                'Global\WaggleDanceBridgeAcceptedQueuePublicationV1'
            )
            try { $publicationAcquired = $publicationMutex.WaitOne(10000) }
            catch [System.Threading.AbandonedMutexException] {
                $publicationAcquired = $true
                $publicationDirtyAbandoned = $true
            }
        } catch {
            throw (
                'accepted queue publication fence acquisition failed: ' +
                $_.Exception.Message
            )
        }
        if (-not $publicationAcquired) {
            throw 'accepted queue publication fence is busy'
        }
        if ($publicationDirtyAbandoned) {
            throw (
                'accepted queue publication fence was abandoned; refusing queue ' +
                'mutation under dirty ownership'
            )
        }
    }

    if (-not (Test-Path -LiteralPath $replayScript -PathType Leaf)) {
        throw "accepted-target replay script is missing: $replayScript"
    }
    if (-not (Test-Path -LiteralPath $BridgeRoot)) {
        Write-DrainSummary
        return
    }

    Add-BridgeQueueDirectoryChainLeases `
        -Path $BridgeRoot -Leases $queueDirectoryLeases `
        -Seen $leasedDirectoryPaths
    if (Test-Path -LiteralPath $spoolDir) {
        Add-BridgeQueueDirectoryLease `
            -Path $spoolDir -Leases $queueDirectoryLeases `
            -Seen $leasedDirectoryPaths
    }
    if (-not (Test-Path -LiteralPath $acceptedDir)) {
        Write-DrainSummary
        return
    }
    Add-BridgeQueueDirectoryLease `
        -Path $acceptedDir -Leases $queueDirectoryLeases `
        -Seen $leasedDirectoryPaths

    Assert-PlainBridgeQueueDirectory `
        -Path $acceptedDir -Label 'accepted-v1 directory'
    $pendingExists = Test-Path -LiteralPath $pendingDir
    if ($pendingExists) {
        Add-BridgeQueueDirectoryLease `
            -Path $pendingDir -Leases $queueDirectoryLeases `
            -Seen $leasedDirectoryPaths
        Assert-PlainBridgeQueueDirectory `
            -Path $pendingDir -Label 'accepted-v1 pending directory'
    }
    if (Test-Path -LiteralPath $readyDir) {
        Add-BridgeQueueDirectoryLease `
            -Path $readyDir -Leases $queueDirectoryLeases `
            -Seen $leasedDirectoryPaths
        Assert-PlainBridgeQueueDirectory `
            -Path $readyDir -Label 'accepted-v1 ready directory'
    } elseif ($pendingExists -and -not $DryRun) {
        [void](New-Item -ItemType Directory -Path $readyDir -ErrorAction Stop)
        Add-BridgeQueueDirectoryLease `
            -Path $readyDir -Leases $queueDirectoryLeases `
            -Seen $leasedDirectoryPaths
        Assert-PlainBridgeQueueDirectory `
            -Path $readyDir -Label 'accepted-v1 ready directory'
    } elseif (-not $pendingExists) {
        Write-DrainSummary
        return
    }
    if (Test-Path -LiteralPath $replayedDir -PathType Container) {
        Add-BridgeQueueDirectoryLease `
            -Path $replayedDir -Leases $queueDirectoryLeases `
            -Seen $leasedDirectoryPaths
    }

if ($pendingExists) {
    $pendingAllFiles = @(
        Get-ChildItem -LiteralPath $pendingDir -File -Force -ErrorAction Stop |
            Sort-Object Name
    )
    $pendingFiles = @($pendingAllFiles | Where-Object {
        $_.Name -cmatch '^bridge-wal-v1-[0-9a-f]{32}\.jsonl$'
    })
    $pendingAppendMutex = $null
    $pendingAppendAcquired = $false
    $pendingAppendDirty = $false
    if ($pendingFiles.Count -gt 0) {
        try {
            $pendingAppendMutex = New-Object System.Threading.Mutex(
                $false,
                'Global\WaggleDanceBridgeAppendV1'
            )
            try { $pendingAppendAcquired = $pendingAppendMutex.WaitOne(0) }
            catch [System.Threading.AbandonedMutexException] {
                $pendingAppendAcquired = $true
                $pendingAppendDirty = $true
            }
        } catch {
            if ($null -ne $pendingAppendMutex) { $pendingAppendMutex.Dispose() }
            throw "accepted pending AppendV1 coordination failed: $($_.Exception.Message)"
        }
    }
    if (
        $pendingFiles.Count -gt 0 -and
        (-not $pendingAppendAcquired -or $pendingAppendDirty)
    ) {
        $blockedStatus = if ($pendingAppendDirty) {
            'pending_append_dirty'
        } else {
            'pending_append_busy'
        }
        $blockedDetail = if ($pendingAppendDirty) {
            'AppendV1 was abandoned; pending recovery cannot mutate queue state'
        } else {
            'AppendV1 is owned by a writer or replayer'
        }
        foreach ($pendingFile in $pendingFiles) {
            $pendingSeen++
            $pendingSkipped++
            $results.Add([pscustomobject][ordered]@{
                namespace = 'pending'
                leaf = $pendingFile.Name
                sha256 = $null
                status = $blockedStatus
                detail = $blockedDetail
                error = $null
            })
        }
        $pendingFiles = @()
    } elseif ($pendingAppendAcquired) {
        # The writer may have completed or published between the initial cheap
        # discovery and our zero-time mutex acquisition. Re-enumerate under
        # AppendV1 so stale FileInfo objects cannot become false failures.
        $pendingAllFiles = @(
            Get-ChildItem -LiteralPath $pendingDir `
                -File -Force -ErrorAction Stop |
                Sort-Object Name
        )
        $pendingFiles = @($pendingAllFiles | Where-Object {
            $_.Name -cmatch '^bridge-wal-v1-[0-9a-f]{32}\.jsonl$'
        })
    }
    foreach ($invalidPending in @($pendingAllFiles | Where-Object {
        $_.Name -cnotmatch '^bridge-wal-v1-[0-9a-f]{32}\.jsonl$'
    })) {
        $pendingSeen++
        $pendingFailed++
        $failed++
        $results.Add([pscustomobject][ordered]@{
            namespace = 'pending'
            leaf = $invalidPending.Name
            sha256 = $null
            status = 'invalid_pending_leaf'
            detail = ''
            error = 'accepted pending leaf name is outside the strict v1 grammar'
        })
    }
    foreach ($pendingFile in $pendingFiles) {
        $pendingSeen++
        $pendingLease = $null
        $pendingMarkerLease = $null
        $pendingSha256 = $null
        $movedReadyLeaf = ''
        try {
            $age = [DateTime]::UtcNow - $pendingFile.LastWriteTimeUtc
            if ($age.TotalSeconds -lt $PendingMinAgeSeconds) {
                $pendingSkipped++
                $results.Add([pscustomobject][ordered]@{
                    namespace = 'pending'
                    leaf = $pendingFile.Name
                    sha256 = $null
                    status = 'pending_young'
                    detail = "ageSeconds=$($age.TotalSeconds)"
                    error = $null
                })
                continue
            }
            if (
                ($pendingFile.Attributes -band
                    [System.IO.FileAttributes]::ReparsePoint) -ne 0
            ) {
                throw 'accepted pending WAL must not be a reparse point'
            }
            try {
                # Existing writer leases request write access and do not share
                # it. This read lease shares delete for MoveFileExW but denies
                # all new writes through validation and publication.
                $pendingLease = New-Object System.IO.FileStream(
                    $pendingFile.FullName,
                    [System.IO.FileMode]::Open,
                    [System.IO.FileAccess]::Read,
                    ([System.IO.FileShare]::Read -bor
                        [System.IO.FileShare]::Delete)
                )
            } catch {
                if (Test-BridgeQueueSharingViolation -Exception $_.Exception) {
                    $pendingSkipped++
                    $results.Add([pscustomobject][ordered]@{
                        namespace = 'pending'
                        leaf = $pendingFile.Name
                        sha256 = $null
                        status = 'pending_active'
                        detail = 'sharing violation; writer lease may still be active'
                        error = $null
                    })
                    continue
                }
                throw
            }
            [byte[]]$pendingBytes = Read-BridgeQueueLeaseBytes `
                -Stream $pendingLease
            Assert-BridgeQueueOneRowWal `
                -Bytes $pendingBytes -Label "accepted pending WAL $($pendingFile.Name)"
            $pendingSha256 = Get-BridgeQueueSha256Hex -Bytes $pendingBytes
            $readyLeaf = $pendingFile.Name
            $readyPath = Join-Path $readyDir $readyLeaf
            $recoveryBlockPath = Join-Path $readyDir (
                ".${readyLeaf}.pending-recovery-blocked"
            )
            # Only a producer-published digest grants automatic recovery
            # authority. Validate it even in DryRun so the forecast matches
            # the real drain and never blesses markerless or changed bytes.
            if (Test-Path -LiteralPath $recoveryBlockPath -PathType Leaf) {
                if (Test-Path -LiteralPath $readyPath) {
                    throw 'pending recovery found both pending and blocked ready WAL paths'
                }
                $pendingMarkerLease = Open-BridgeQueueRecoveryBlockMarkerLease `
                    -Path $recoveryBlockPath -Leaf $readyLeaf
                if ([string]$pendingMarkerLease.Marker.expected_sha256 `
                    -cne $pendingSha256) {
                    throw 'pending recovery block marker does not bind the leased WAL'
                }
            } else {
                throw (
                    'accepted pending WAL has no durable producer digest marker; ' +
                    'automatic replay is forbidden'
                )
            }
            if ($DryRun) {
                $pendingSkipped++
                $results.Add([pscustomobject][ordered]@{
                    namespace = 'pending'
                    leaf = $pendingFile.Name
                    sha256 = $pendingSha256
                    status = 'pending_would_promote'
                    detail = $readyLeaf
                    error = $null
                })
                continue
            }
            if ([Environment]::GetEnvironmentVariable(
                'AGENT_BRIDGE_TEST_PENDING_MOVE_FAILURE',
                'Process'
            ) -in @('1', $readyLeaf)) {
                throw 'simulated accepted pending publication failure before move'
            }
            Initialize-BridgeAcceptedQueueNative
            if (-not [WaggleDance.BridgeAcceptedQueueNative]::MoveFileExW(
                $pendingFile.FullName,
                $readyPath,
                [uint32]0x00000008
            )) {
                $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
                $nativeError = New-Object System.ComponentModel.Win32Exception(
                    $nativeCode
                )
                throw (
                    'accepted pending WAL publication failed: {0} ({1})' -f
                    $nativeCode,
                    $nativeError.Message
                )
            }
            $movedReadyLeaf = $readyLeaf
            if ([Environment]::GetEnvironmentVariable(
                'AGENT_BRIDGE_TEST_PENDING_VERIFY_FAILURE',
                'Process'
            ) -in @('1', $readyLeaf)) {
                throw 'simulated accepted pending post-move verification failure'
            }
            [byte[]]$leasedAfterMove = Read-BridgeQueueLeaseBytes `
                -Stream $pendingLease
            [byte[]]$readyBytes = [System.IO.File]::ReadAllBytes($readyPath)
            if (
                -not (Test-BridgeQueueBytesEqual `
                    -Left $leasedAfterMove -Right $pendingBytes) -or
                -not (Test-BridgeQueueBytesEqual `
                    -Left $readyBytes -Right $pendingBytes) -or
                (Get-BridgeQueueSha256Hex -Bytes $readyBytes) -cne
                    $pendingSha256
            ) {
                throw 'published accepted pending WAL failed byte/SHA-256 verification'
            }
            # Keep the producer/recovery digest binding beside ready until the
            # exact target is delivered. This closes the crash/tamper window
            # between publication and the ready replay loop below.
            $movedReadyLeaf = ''
            $pendingPromoted++
            $results.Add([pscustomobject][ordered]@{
                namespace = 'pending'
                leaf = $pendingFile.Name
                sha256 = $pendingSha256
                status = 'pending_promoted'
                detail = $readyLeaf
                error = $null
            })
            if (-not $ReceiptJson) {
                Write-Output "promoted accepted pending WAL: $readyLeaf"
            }
        } catch {
            if ($movedReadyLeaf) {
                [void]$blockedReadyLeaves.Add($movedReadyLeaf)
            }
            $pendingFailed++
            $failed++
            $results.Add([pscustomobject][ordered]@{
                namespace = 'pending'
                leaf = $pendingFile.Name
                sha256 = $pendingSha256
                status = 'pending_failed'
                detail = $movedReadyLeaf
                error = $_.Exception.Message
            })
            if (-not $ReceiptJson) {
                $failureLocation = if ($movedReadyLeaf) {
                    "ready leaf blocked for this invocation: $movedReadyLeaf"
                } else {
                    "pending leaf kept: $($pendingFile.Name)"
                }
                Write-Warning -WarningAction Continue -Message `
                    "$failureLocation ($($_.Exception.Message))"
            }
        } finally {
            if ($null -ne $pendingLease) { $pendingLease.Dispose() }
            if ($null -ne $pendingMarkerLease -and
                $null -ne $pendingMarkerLease.Stream) {
                $pendingMarkerLease.Stream.Dispose()
            }
        }
    }
    if ($null -ne $pendingAppendMutex) {
        if ($pendingAppendAcquired) {
            try { $pendingAppendMutex.ReleaseMutex() }
            catch {
                $failed++
                $results.Add([pscustomobject][ordered]@{
                    namespace = 'pending'
                    leaf = $null
                    sha256 = $null
                    status = 'pending_append_release_failed'
                    detail = ''
                    error = $_.Exception.Message
                })
            }
        }
        $pendingAppendMutex.Dispose()
    }
}

$readyFiles = @()
$readyBlockMarkers = @{}
if (Test-Path -LiteralPath $readyDir -PathType Container) {
    $readyAllFiles = @(
        Get-ChildItem -LiteralPath $readyDir -File -Force -ErrorAction Stop |
            Sort-Object Name
    )
    $readyFiles = @($readyAllFiles | Where-Object {
        $_.Name -cmatch '^bridge-wal-v1-[0-9a-f]{32}\.jsonl$' -and
        -not $blockedReadyLeaves.Contains($_.Name)
    })
    foreach ($markerFile in @($readyAllFiles | Where-Object {
        $_.Name -cmatch
            '^\.bridge-wal-v1-[0-9a-f]{32}\.jsonl\.pending-recovery-blocked$'
    })) {
        $boundLeaf = $markerFile.Name.Substring(
            1,
            $markerFile.Name.Length - 1 - '.pending-recovery-blocked'.Length
        )
        $readyBlockMarkers[$boundLeaf] = $markerFile.FullName
        if (-not (Test-Path -LiteralPath (Join-Path $readyDir $boundLeaf) -PathType Leaf)) {
            $replayedLease = $null
            $markerLease = $null
            try {
                $markerLease = Open-BridgeQueueRecoveryBlockMarkerLease `
                    -Path $markerFile.FullName -Leaf $boundLeaf `
                    -DeleteAccess:(-not $DryRun)
                $marker = $markerLease.Marker
                $boundPendingPath = Join-Path $pendingDir $boundLeaf
                if (Test-Path -LiteralPath $boundPendingPath -PathType Leaf) {
                    $pendingAlreadyObserved = @($results | Where-Object {
                        [string]$_.namespace -ceq 'pending' -and
                        [string]$_.leaf -ceq $boundLeaf
                    }).Count -gt 0
                    if (-not $pendingAlreadyObserved) {
                        $pendingSeen++
                        $pendingSkipped++
                    }
                    $results.Add([pscustomobject][ordered]@{
                        namespace = 'ready'
                        leaf = $boundLeaf
                        sha256 = [string]$marker.expected_sha256
                        status = 'digest_marker_waiting_for_pending'
                        detail = $boundPendingPath
                        error = $null
                    })
                    continue
                }
                $replayedPath = Join-Path (Join-Path $acceptedDir 'replayed') $boundLeaf
                $replayedLease = Open-BridgeQueuePlainFileLease `
                    -Path $replayedPath -Label 'replayed accepted WAL'
                if ((Get-BridgeQueueSha256Hex -Bytes $replayedLease.Bytes) `
                    -cne [string]$marker.expected_sha256) {
                    throw 'orphan block marker has no matching replayed WAL'
                }
                if (-not $DryRun) {
                    Remove-BridgeQueueLeasedFile `
                        -Lease $markerLease -Label 'orphan recovery block marker'
                }
                $results.Add([pscustomobject][ordered]@{
                    namespace = 'ready'
                    leaf = $boundLeaf
                    sha256 = [string]$marker.expected_sha256
                    status = if ($DryRun) { 'orphan_block_would_clear' } else { 'orphan_block_cleared' }
                    detail = $replayedPath
                    error = $null
                })
            } catch {
                if (-not (Test-Path -LiteralPath $markerFile.FullName)) {
                    $results.Add([pscustomobject][ordered]@{
                        namespace = 'ready'
                        leaf = $boundLeaf
                        sha256 = $null
                        status = 'digest_marker_resolved_concurrently'
                        detail = ''
                        error = $null
                    })
                    continue
                }
                $failed++
                $results.Add([pscustomobject][ordered]@{
                    namespace = 'ready'
                    leaf = $boundLeaf
                    sha256 = $null
                    status = 'orphan_block_failed'
                    detail = ''
                    error = $_.Exception.Message
                })
            } finally {
                if ($null -ne $replayedLease) {
                    $replayedLease.Stream.Dispose()
                }
                if ($null -ne $markerLease -and
                    $null -ne $markerLease.Stream) {
                    $markerLease.Stream.Dispose()
                }
            }
        }
    }
    foreach ($invalidReady in @($readyAllFiles | Where-Object {
        $_.Name -cnotmatch '^bridge-wal-v1-[0-9a-f]{32}\.jsonl$' -and
        $_.Name -cnotmatch
            '^\.bridge-wal-v1-[0-9a-f]{32}\.jsonl\.pending-recovery-blocked$'
    })) {
        $failed++
        $results.Add([pscustomobject][ordered]@{
            namespace = 'ready'
            leaf = $invalidReady.Name
            sha256 = $null
            status = 'invalid_ready_leaf'
            detail = ''
            error = 'accepted ready leaf name is outside the strict v1 grammar'
        })
    }
}

foreach ($file in $readyFiles) {
    $readySeen++
    $sha256 = $null
    $readyRecoveryBlockPath = $null
    $readyLease = $null
    $readyMarkerLease = $null
    try {
        $readyLease = Open-BridgeQueuePlainFileLease `
            -Path $file.FullName -Label 'accepted ready WAL'
        Assert-BridgeQueueOneRowWal `
            -Bytes $readyLease.Bytes -Label "accepted ready WAL $($file.Name)"
        if (-not $readyBlockMarkers.ContainsKey($file.Name)) {
            throw (
                'accepted ready WAL has no durable producer digest marker; ' +
                'automatic replay is forbidden'
            )
        }
        $readyRecoveryBlockPath = [string]$readyBlockMarkers[$file.Name]
        $readyMarkerLease = Open-BridgeQueueRecoveryBlockMarkerLease `
            -Path $readyRecoveryBlockPath -Leaf $file.Name `
            -DeleteAccess:(-not $DryRun)
        $marker = $readyMarkerLease.Marker
        $blockedReadySha256 = Get-BridgeQueueSha256Hex -Bytes $readyLease.Bytes
        if ($blockedReadySha256 -cne [string]$marker.expected_sha256) {
            throw 'ready WAL does not match its persisted producer digest'
        }
        $sha256 = $blockedReadySha256
        if ($DeferCanonicalProof) {
            $wouldDrain++
            $results.Add([pscustomobject][ordered]@{
                namespace = 'ready'
                leaf = $file.Name
                sha256 = $sha256
                status = 'canonical_proof_deferred'
                detail = "canonical proof deferred to caller: $($file.Name)"
                error = $null
            })
            continue
        }
        $readyLease.Stream.Dispose()
        $readyLease = $null
        $replayOutput = @(
            & $replayScript -BridgeRoot $BridgeRoot `
                -AcceptedWalLeaf $file.Name `
                -ExpectedWalSha256 $sha256 -DryRun:$DryRun.IsPresent 3>&1
        )
        $detail = $replayOutput -join [Environment]::NewLine
        if (
            $detail -notmatch 'spool replay complete' -and
            $detail -notmatch 'accepted WAL already delivered'
        ) {
            throw "accepted-target replay did not complete: $detail"
        }
        if ($null -ne $readyRecoveryBlockPath -and -not $DryRun) {
            Remove-BridgeQueueLeasedFile `
                -Lease $readyMarkerLease -Label 'pending recovery block marker'
            if (Test-Path -LiteralPath $readyRecoveryBlockPath) {
                throw 'pending-recovery block marker remained after target delivery'
            }
        }
        $status = if ($detail -match 'accepted WAL already delivered') {
            $alreadyDelivered++
            'already_delivered'
        } elseif ($DryRun) {
            $wouldDrain++
            'dry_run'
        } else {
            $drained++
            'drained'
        }
        $results.Add([pscustomobject][ordered]@{
            namespace = 'ready'
            leaf = $file.Name
            sha256 = $sha256
            status = $status
            detail = $detail
            error = $null
        })
        if (-not $ReceiptJson -and $detail) { Write-Output $detail }
    } catch {
        $failed++
        $results.Add([pscustomobject][ordered]@{
            namespace = 'ready'
            leaf = $file.Name
            sha256 = $sha256
            status = 'failed'
            detail = ''
            error = $_.Exception.Message
        })
        if (-not $ReceiptJson) {
            Write-Warning -WarningAction Continue -Message (
                "accepted WAL kept after drain failure: $($file.Name) " +
                "($($_.Exception.Message))"
            )
        }
    } finally {
        if ($null -ne $readyLease) {
            $readyLease.Stream.Dispose()
        }
        if ($null -ne $readyMarkerLease -and
            $null -ne $readyMarkerLease.Stream) {
            $readyMarkerLease.Stream.Dispose()
        }
    }
}

    Write-DrainSummary
} finally {
    Close-BridgeQueueDirectoryLeases -Leases $queueDirectoryLeases
    if ($null -ne $publicationMutex) {
        if ($publicationAcquired) {
            try { $publicationMutex.ReleaseMutex() }
            catch {
                Write-Warning -WarningAction Continue -Message (
                    'accepted queue publication fence release failed: ' +
                    $_.Exception.Message
                )
            }
        }
        $publicationMutex.Dispose()
    }
}
