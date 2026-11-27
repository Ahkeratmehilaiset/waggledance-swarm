#requires -Version 5.1
<#
.SYNOPSIS
    Installs the committed WaggleDance reboot bundle on persistent C: storage.

.DESCRIPTION
    The Git repository remains the source of truth.  This installer refuses a
    dirty or unpushed reboot bundle, copies the exact committed files into a
    commit-addressed directory, verifies every copied hash, and only then
    replaces the small machine-local entry-point wrappers.

    Existing machine-local launchers are copied to a timestamped backup
    directory before replacement.  Runtime bridge state is never copied,
    deleted, reset, or migrated by this script.
#>
[CmdletBinding()]
param(
    [string] $MachineRoot = 'C:\Python',
    [string] $BundleStore = 'C:\Python\wd-reboot-bundles',
    [switch] $SkipTaskRegistration,
    [switch] $SkipGrokResolve,
    # Optional offline wheel directory; every wheel is still hash-verified.
    [string] $WheelSource = '',
    # Explicit operator-approved cold-manifest input, never an activation sidecar.
    # Omission preserves the default (manual execution disabled) manifest.
    [string] $ManualExecutionApprovalRecordPath = '',
    # Optional operator attestation for a previously observed fresh RCO3 session.
    # Does not launch/register a lane; omission leaves enrollment absent.
    [string] $RcoEnrollmentRecordPath = '',
    # Optional, source-bound runtime descriptors; omission keeps the bundle unchanged.
    [string] $MindRuntimeDescriptorSetPath = '',
    # Operator-supplied private policy stays outside the bundle. Only its hash
    # and the independently supplied approval reference enter the manifest.
    [string] $MindIdentifierPolicyPath = '',
    [string] $MindApprovalReferenceSha256 = '',
    # Verify and stage the commit-addressed bundle only: no machine wrapper,
    # data copy, state pointer, Grok resolve or task registration is touched.
    [switch] $StageOnly,
    [switch] $DryRun,
    # The ONE final operator signature stays out of band. An actual install (neither -StageOnly nor -DryRun)
    # must be given the exact pair that -StageOnly printed; otherwise it refuses before any machine file changes.
    [string] $ExpectedFinalCommit = '',
    [string] $ExpectedFinalManifestHash = '',
    # Required by this version's unconditional Git member discovery. These are
    # caller inputs, not authentication or an additional operator signature.
    # A reviewed external native-Git anchor is still required for real use.
    [string] $GitExecutable = '',
    [string] $ExpectedGitSha256 = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Resolve-FullPath {
    param([Parameter(Mandatory)] [string] $Path)
    return [System.IO.Path]::GetFullPath($Path)
}

function Assert-WdPathWithoutReparse {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $TrustedRoot,
        [ValidateSet('Any', 'Directory', 'Leaf')]
        [string] $ExpectedType = 'Any',
        [switch] $AllowMissing
    )

    $candidate = Resolve-FullPath $Path
    $root = Resolve-FullPath $TrustedRoot
    $rootPrefix = $root.TrimEnd('\') + '\'
    if (
        -not $candidate.Equals($root, [StringComparison]::OrdinalIgnoreCase) -and
        -not $candidate.StartsWith(
            $rootPrefix,
            [StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "path escaped its trusted root: $candidate"
    }
    $relative = if ($candidate.Equals(
            $root,
            [StringComparison]::OrdinalIgnoreCase
        )) { '' } else { $candidate.Substring($rootPrefix.Length) }
    if (-not (Test-Path -LiteralPath $root)) {
        if ($AllowMissing) { return $candidate }
        throw "required trusted path component is missing: $root"
    }
    $rootItem = Get-Item -LiteralPath $root -Force -ErrorAction Stop
    if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "trusted path component cannot be a reparse point: $root"
    }
    $current = $root
    foreach ($segment in @($relative -split '[\\/]')) {
        if (-not $segment) { continue }
        $current = Join-Path $current $segment
        if (-not (Test-Path -LiteralPath $current)) {
            if ($AllowMissing) { return $candidate }
            throw "required trusted path component is missing: $current"
        }
        $item = Get-Item -LiteralPath $current -Force -ErrorAction Stop
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "trusted path component cannot be a reparse point: $current"
        }
    }
    if (
        $ExpectedType -ceq 'Directory' -and
        -not (Test-Path -LiteralPath $candidate -PathType Container)
    ) {
        throw "trusted path is not a directory: $candidate"
    }
    if (
        $ExpectedType -ceq 'Leaf' -and
        -not (Test-Path -LiteralPath $candidate -PathType Leaf)
    ) {
        throw "trusted path is not a file: $candidate"
    }
    return $candidate
}

function Read-WdMindRuntimeFileSnapshot {
    param([string] $Path, [int] $Limit)
    if ($Path -cnotmatch '^[Cc]:\\' -or $Limit -lt 1 -or $Limit -gt 8388608) {
        throw 'Mind descriptor requires a bounded absolute persistent C: path'
    }
    foreach ($part in $Path.Substring(3).Split('\')) {
        if (-not $part -or $part -in @('.', '..') -or $part -match '[<>:"|?*\x00-\x1f]' -or
            $part.EndsWith('.') -or $part.EndsWith(' ') -or
            $part -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$') {
            throw 'Mind descriptor path is not canonical'
        }
    }
    $full = Assert-WdPathWithoutReparse -Path $Path -TrustedRoot 'C:\' -ExpectedType Leaf
    $stream = [IO.File]::Open($full, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        if (-not ('WaggleDance.WdMindDescriptorFileV1' -as [type])) {
            Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;
namespace WaggleDance {
    public static class WdMindDescriptorFileV1 {
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        private static extern uint GetFinalPathNameByHandleW(SafeFileHandle handle, StringBuilder path, uint length, uint flags);
        public static string FinalPath(SafeFileHandle handle) {
            var path = new StringBuilder(32768);
            uint length = GetFinalPathNameByHandleW(handle, path, (uint)path.Capacity, 0);
            if (length == 0 || length >= path.Capacity) throw new IOException("Mind descriptor handle unavailable");
            return path.ToString();
        }
    }
}
'@
        }
        $opened = [WaggleDance.WdMindDescriptorFileV1]::FinalPath($stream.SafeFileHandle)
        if (-not $opened.Equals(('\\?\' + $full), [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Mind descriptor opened handle differs from its checked path'
        }
        $buffer = New-Object byte[] ($Limit + 1)
        $count = 0
        while ($count -lt $buffer.Length) {
            $read = $stream.Read($buffer, $count, $buffer.Length - $count)
            if ($read -eq 0) { break }
            $count += $read
        }
        if ($count -eq 0 -or $count -gt $Limit) { throw 'Mind descriptor byte budget exceeded' }
        $bytes = New-Object byte[] $count
        [Array]::Copy($buffer, $bytes, $count)
        $text = (New-Object Text.UTF8Encoding($false, $true)).GetString($bytes)
        if (-not [WaggleDance.BridgeSnapshotDeltaV1.JsonContractValidator]::IsValid($text)) {
            throw 'Mind descriptor violates strict UTF-8 JSON contract'
        }
        [void](Assert-WdPathWithoutReparse -Path $full -TrustedRoot 'C:\' -ExpectedType Leaf)
        $sha = [Security.Cryptography.SHA256]::Create()
        try { $hash = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '').ToLowerInvariant() }
        finally { $sha.Dispose() }
        return [pscustomobject]@{ Path = $full; Limit = $Limit; Bytes = $bytes; Hash = $hash; Text = $text }
    } finally { $stream.Dispose() }
}

function Assert-WdMindRuntimeKeys {
    param($Value, [string[]] $Keys)
    if ($null -eq $Value -or $Value -isnot [pscustomobject]) { throw 'Mind descriptor object required' }
    $names = @($Value.PSObject.Properties.Name)
    if ($names.Count -ne $Keys.Count) { throw 'Mind descriptor field set differs' }
    foreach ($key in $Keys) {
        if ($key -cnotin $names) { throw 'Mind descriptor field set differs' }
    }
}

function Read-WdMindRuntimeDirectorySnapshot {
    param($Path)
    if ($Path -isnot [string] -or $Path.Length -gt 32767 -or $Path -cnotmatch '^[Cc]:\\') {
        throw 'Mind runtime location requires an absolute persistent C: directory'
    }
    foreach ($part in $Path.Substring(3).Split('\')) {
        if (-not $part -or $part.Length -gt 255 -or $part -in @('.', '..') -or
            $part -match '[<>:"|?*\x00-\x1f/]' -or $part.EndsWith('.') -or $part.EndsWith(' ') -or
            $part -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$') {
            throw 'Mind runtime location path is not canonical'
        }
    }
    $full = Assert-WdPathWithoutReparse -Path $Path -TrustedRoot 'C:\' -ExpectedType Directory
    if (-not ('WaggleDance.WdMindLocationV1' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
namespace WaggleDance {
    [StructLayout(LayoutKind.Sequential)]
    public struct WdMindLocationInformation {
        public uint Attributes;
        public System.Runtime.InteropServices.ComTypes.FILETIME CreationTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastAccessTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWriteTime;
        public uint VolumeSerialNumber, FileSizeHigh, FileSizeLow, NumberOfLinks, FileIndexHigh, FileIndexLow;
    }
    public static class WdMindLocationV1 {
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        private static extern uint GetFinalPathNameByHandleW(SafeFileHandle handle, System.Text.StringBuilder path, uint length, uint flags);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        private static extern SafeFileHandle CreateFileW(string name, uint access, uint share, IntPtr security,
            uint disposition, uint flags, IntPtr template);
        [DllImport("kernel32.dll", SetLastError=true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool GetFileInformationByHandle(SafeFileHandle handle, out WdMindLocationInformation info);
        public static string[] Snapshot(string path) {
            // OPEN_EXISTING; BACKUP_SEMANTICS | OPEN_REPARSE_POINT. Metadata only.
            using (var handle = CreateFileW(path, 0, 7, IntPtr.Zero, 3, 0x02200000, IntPtr.Zero)) {
                WdMindLocationInformation info;
                if (handle.IsInvalid || !GetFileInformationByHandle(handle, out info) ||
                    (info.Attributes & 0x10) == 0 || (info.Attributes & 0x400) != 0)
                    throw new IOException("Mind runtime directory handle refused");
                var final = new System.Text.StringBuilder(32768);
                uint length = GetFinalPathNameByHandleW(handle, final, (uint)final.Capacity, 0);
                if (length == 0 || length >= final.Capacity) throw new IOException("Mind runtime directory path unavailable");
                return new string[] { final.ToString(),
                    info.VolumeSerialNumber.ToString("x8") + info.FileIndexHigh.ToString("x8") + info.FileIndexLow.ToString("x8") };
            }
        }
    }
}
'@
    }
    $opened = [WaggleDance.WdMindLocationV1]::Snapshot($full)
    if (-not $opened[0].Equals(('\\?\' + $full), [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Mind runtime directory handle differs from its checked path'
    }
    [void](Assert-WdPathWithoutReparse -Path $full -TrustedRoot 'C:\' -ExpectedType Directory)
    return [pscustomobject]@{ Path = $full; Identity = $opened[1] }
}

function Assert-WdMindRuntimeMessagePolicy {
    param($Policy)
    $keys = @('schema', 'lanes', 'types', 'task_scope', 'removed_source_refs', 'max_message_chars', 'max_chunk_chars')
    if ($null -ne $Policy -and $null -ne $Policy.PSObject.Properties['approved_source_refs']) { $keys += 'approved_source_refs' }
    Assert-WdMindRuntimeKeys $Policy $keys
    if ($Policy.schema -isnot [string] -or $Policy.schema -cne 'wd.mind-content-policy.v3' -or
        $Policy.task_scope -isnot [string] -or $Policy.task_scope -cne 'all_tasks' -or
        $Policy.max_message_chars -isnot [int] -or $Policy.max_message_chars -lt 1 -or $Policy.max_message_chars -gt 4096 -or
        $Policy.max_chunk_chars -isnot [int] -or $Policy.max_chunk_chars -lt 16 -or $Policy.max_chunk_chars -gt 300) {
        throw 'Mind message policy schema or budget differs'
    }
    foreach ($name in @('lanes', 'types', 'removed_source_refs', 'approved_source_refs')) {
        $property = $Policy.PSObject.Properties[$name]
        if ($null -eq $property) { continue }
        $values = $property.Value
        if ($values -isnot [array] -or $values.Count -gt 4096) { throw 'Mind message policy set type differs' }
        $seen = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
        foreach ($value in $values) {
            if ($value -isnot [string] -or -not $seen.Add($value)) { throw 'Mind message policy duplicate or invalid item' }
            if ($name -ceq 'lanes') {
                if ($value -cnotin @('codex-lead-1', 'codex-tools-1', 'fable-5')) { throw 'Mind message policy lane differs' }
            } elseif ($name -ceq 'types') {
                if ($value -cnotin @('message', 'wake_request', 'handoff', 'test', 'claim', 'release', 'done', 'milestone', 'status', 'progress', 'decision')) {
                    throw 'Mind message policy event type differs'
                }
            } elseif ($value -cnotmatch '^[0-9a-f]{64}\z') { throw 'Mind message policy source ref differs' }
        }
        if ($name -ceq 'lanes' -and $values.Count -ne 3) { throw 'Mind full runtime policy requires all three allowed lanes' }
        if ($name -ceq 'types' -and ($values.Count -lt 1 -or $values.Count -gt 11)) { throw 'Mind message policy type set differs' }
    }
}

function Assert-WdMindRuntimeRecords {
    param($Records, [switch] $Interpreter)
    $maxCount = if ($Interpreter) { 1024 } else { 50000 }
    $maxItem = if ($Interpreter) { 536870912L } else { 8589934592L }
    $maxTotal = if ($Interpreter) { 4294967296L } else { 17179869184L }
    if ($Records -isnot [array] -or $Records.Count -lt 1 -or $Records.Count -gt $maxCount) {
        throw 'Mind descriptor record budget differs'
    }
    $files = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
    $dirs = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
    $cases = New-Object 'Collections.Generic.Dictionary[string,string]' ([StringComparer]::OrdinalIgnoreCase)
    $roles = @(); $total = 0L
    foreach ($record in $Records) {
        $keys = @('path', 'size', 'sha256')
        if ($Interpreter) { $keys += 'role' }
        Assert-WdMindRuntimeKeys $record $keys
        if ($record.path -isnot [string] -or $record.sha256 -isnot [string] -or
            $record.sha256 -cnotmatch '^[0-9a-f]{64}\z' -or
            ($record.size -isnot [int] -and $record.size -isnot [long]) -or
            $record.size -lt 0 -or $record.size -gt $maxItem) { throw 'Mind descriptor record type differs' }
        $path = $record.path
        # Interpreter parsing applies runtime._path_parts to "assets/" + path.
        $pathBudget = if ($Interpreter) { 4089 } else { 4096 }
        if ($path.Length -gt $pathBudget -or $path.Contains('\')) { throw 'Mind descriptor relative path differs' }
        $parts = $path.Split('/')
        if (-not $Interpreter -and ($parts.Count -lt 2 -or $parts[0] -cnotin @('site', 'model', 'assets'))) {
            throw 'Mind runtime namespace differs'
        }
        $spaceException = -not $Interpreter -and $path -cin @(
            'site/scipy/io/tests/data/Transparent Busy.ani', 'site/setuptools/launcher manifest.xml',
            'site/setuptools/script (dev).tmpl', 'site/setuptools/_vendor/jaraco/text/Lorem ipsum.txt')
        foreach ($part in $parts) {
            if (-not $part -or $part.Length -gt 255 -or $part -in @('.', '..') -or
                $part.StartsWith(' ') -or $part.EndsWith(' ') -or $part.EndsWith('.') -or
                $part -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$' -or
                (-not $spaceException -and $part -cnotmatch '^[A-Za-z0-9_.+\-]+\z')) {
                throw 'Mind descriptor relative segment differs'
            }
        }
        if (-not $files.Add($path)) { throw 'Mind descriptor duplicate path' }
        for ($i = 0; $i -lt $parts.Count; $i++) {
            $name = ($parts[0..$i] -join '/')
            if ($cases.ContainsKey($name) -and $cases[$name] -cne $name) { throw 'Mind descriptor case collision' }
            $cases[$name] = $name
            if ($i -lt $parts.Count - 1) { [void]$dirs.Add($name) }
        }
        if ($dirs.Count -gt 100000) { throw 'Mind descriptor directory budget exceeded' }
        $total += $record.size
        if ($total -gt $maxTotal) { throw 'Mind descriptor total budget exceeded' }
        if ($Interpreter) {
            if ($record.role -isnot [string] -or $record.role -cnotin @('interpreter', 'stdlib', 'dll')) {
                throw 'Mind interpreter role differs'
            }
            $roles += $record.role
        }
    }
    foreach ($file in $files) { if ($dirs.Contains($file)) { throw 'Mind descriptor file-directory collision' } }
    if ($Interpreter -and (@($roles | Where-Object { $_ -ceq 'interpreter' }).Count -ne 1 -or
        'stdlib' -cnotin $roles -or 'dll' -cnotin $roles)) { throw 'Mind interpreter role set differs' }
}

function Read-WdMindRuntimeDescriptorSet {
    param([string] $Path, [string] $SourceCommit, [string] $WorkerSha256)
    if ($Path -ceq '') { return $null }
    if ($SourceCommit -cnotmatch '^[0-9a-f]{40}\z' -or $WorkerSha256 -cnotmatch '^[0-9a-f]{64}\z') {
        throw 'Mind descriptors require exact archived source pins'
    }
    $outer = Read-WdMindRuntimeFileSnapshot -Path $Path -Limit 16384
    $document = ConvertFrom-Json -InputObject $outer.Text -ErrorAction Stop
    Assert-WdMindRuntimeKeys $document @('schema', 'source_commit', 'files')
    if ($document.schema -isnot [string] -or $document.source_commit -isnot [string] -or
        $document.schema -cnotin @('wd.mind-runtime-descriptor-set.v1', 'wd.mind-runtime-descriptor-set.v2') -or
        $document.source_commit -cne $SourceCommit) {
        throw 'Mind descriptor set source binding differs'
    }
    $schemas = [ordered]@{
        'wd-mind-runtime-manifest.json' = 'wd.q4-runtime-manifest.v1'
        'wd-mind-interpreter-identity.json' = 'wd.q4-interpreter-identity.v1'
        'wd-mind-e5-encoder-binding.json' = 'wd.mind-memory-encoder-binding.v1'
    }
    $limits = @(8388608, 1048576, 65536)
    $v2 = $document.schema -ceq 'wd.mind-runtime-descriptor-set.v2'
    if ($v2) {
        $schemas['wd-mind-runtime-location.json'] = 'wd.mind-runtime-location.v1'
        $schemas['wd-mind-message-policy.json'] = 'wd.mind-content-policy.v3'
        $limits += @(16384, 65536)
    }
    Assert-WdMindRuntimeKeys $document.files @($schemas.Keys)
    $snapshots = [ordered]@{}; $parsed = @{}; $index = 0
    foreach ($name in $schemas.Keys) {
        $pin = $document.files.PSObject.Properties[$name].Value
        Assert-WdMindRuntimeKeys $pin @('sha256', 'schema')
        if ($pin.sha256 -isnot [string] -or $pin.sha256 -cnotmatch '^[0-9a-f]{64}\z' -or
            $pin.schema -isnot [string] -or $pin.schema -cne $schemas[$name]) {
            throw 'Mind descriptor pin or schema differs'
        }
        $snapshot = Read-WdMindRuntimeFileSnapshot -Path (Join-Path (Split-Path -Parent $outer.Path) $name) -Limit $limits[$index]
        $index++
        if ($snapshot.Hash -cne $pin.sha256) { throw 'Mind descriptor hash mismatch' }
        $value = ConvertFrom-Json -InputObject $snapshot.Text -ErrorAction Stop
        if ($value.schema -isnot [string] -or $value.schema -cne $schemas[$name]) { throw 'Mind descriptor payload schema differs' }
        $snapshots[$name] = $snapshot; $parsed[$name] = $value
    }
    $runtime = $parsed['wd-mind-runtime-manifest.json']
    Assert-WdMindRuntimeKeys $runtime @('schema', 'generation', 'revision', 'files')
    if ($runtime.generation -isnot [string] -or $runtime.revision -isnot [string] -or
        $runtime.generation -cnotmatch '^(?:[0-9a-f]{40}|[0-9a-f]{64})\z' -or
        $runtime.revision -cnotmatch '^(?:[0-9a-f]{40}|[0-9a-f]{64})\z') { throw 'Mind runtime revision differs' }
    Assert-WdMindRuntimeRecords $runtime.files
    $identity = $parsed['wd-mind-interpreter-identity.json']
    Assert-WdMindRuntimeKeys $identity @('schema', 'files')
    Assert-WdMindRuntimeRecords $identity.files -Interpreter
    $binding = $parsed['wd-mind-e5-encoder-binding.json']
    Assert-WdMindRuntimeKeys $binding @('schema', 'encoder', 'code_sha256', 'passage_policy', 'query_policy')
    Assert-WdMindRuntimeKeys $binding.encoder @('model', 'revision', 'weights_sha256')
    $weights = @($runtime.files | Where-Object { $_.path -ceq 'model/model.safetensors' })
    if ($weights.Count -ne 1 -or $binding.code_sha256 -isnot [string] -or
        $binding.encoder.model -isnot [string] -or $binding.encoder.revision -isnot [string] -or
        $binding.encoder.weights_sha256 -isnot [string] -or $binding.code_sha256 -cne $WorkerSha256 -or
        $binding.encoder.model -cne 'intfloat/multilingual-e5-small' -or
        $binding.encoder.revision -cne $runtime.revision -or $binding.encoder.weights_sha256 -cne $weights[0].sha256) {
        throw 'Mind encoder source/runtime binding differs'
    }
    foreach ($name in @('passage', 'query')) {
        $policy = $binding.PSObject.Properties[$name + '_policy'].Value
        Assert-WdMindRuntimeKeys $policy @('prefix', 'truncate_chars', 'normalize', 'dtype', 'dim')
        if ($policy.prefix -isnot [string] -or $policy.dtype -isnot [string] -or
            $policy.prefix -cne ($name + ': ') -or $policy.dtype -cne 'float32' -or
            $policy.normalize -isnot [bool] -or -not $policy.normalize -or
            $policy.truncate_chars -isnot [int] -or $policy.truncate_chars -ne 2000 -or
            $policy.dim -isnot [int] -or $policy.dim -ne 384) { throw 'Mind encoder policy differs' }
    }
    $result = [pscustomobject]@{ Outer = $outer; Files = $snapshots }
    if ($v2) {
        $location = $parsed['wd-mind-runtime-location.json']
        Assert-WdMindRuntimeKeys $location @('schema', 'source_commit', 'runtime_root', 'interpreter_root',
            'runtime_manifest_sha256', 'interpreter_identity_sha256', 'encoder_binding_sha256')
        if ($location.source_commit -isnot [string] -or $location.source_commit -cne $SourceCommit) {
            throw 'Mind runtime location source commit differs'
        }
        foreach ($pair in @(@('runtime_manifest_sha256', 'wd-mind-runtime-manifest.json'),
                             @('interpreter_identity_sha256', 'wd-mind-interpreter-identity.json'),
                             @('encoder_binding_sha256', 'wd-mind-e5-encoder-binding.json'))) {
            $pin = $location.PSObject.Properties[$pair[0]].Value
            if ($pin -isnot [string] -or $pin -cne $snapshots[$pair[1]].Hash) { throw 'Mind runtime location descriptor pin differs' }
        }
        $runtimeRoot = Read-WdMindRuntimeDirectorySnapshot $location.runtime_root
        $interpreterRoot = Read-WdMindRuntimeDirectorySnapshot $location.interpreter_root
        $rootPrefix = $runtimeRoot.Path.TrimEnd('\') + '\'
        $interpreterPrefix = $interpreterRoot.Path.TrimEnd('\') + '\'
        if ($rootPrefix.StartsWith($interpreterPrefix, [StringComparison]::OrdinalIgnoreCase) -or
            $interpreterPrefix.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Mind runtime and interpreter roots must be disjoint'
        }
        Assert-WdMindRuntimeMessagePolicy $parsed['wd-mind-message-policy.json']
        $result | Add-Member -NotePropertyName LocationRoots -NotePropertyValue @($runtimeRoot, $interpreterRoot)
    }
    return $result
}

function Assert-WdMindRuntimeDescriptorInputs {
    param($Set)
    if ($null -eq $Set) { return }
    foreach ($snapshot in @($Set.Outer) + @($Set.Files.Values)) {
        $current = Read-WdMindRuntimeFileSnapshot -Path $snapshot.Path -Limit $snapshot.Limit
        if ($current.Hash -cne $snapshot.Hash) { throw 'Mind descriptor input changed after validation' }
    }
    $locations = $Set.PSObject.Properties['LocationRoots']
    if ($null -ne $locations) {
        foreach ($snapshot in $locations.Value) {
            $current = Read-WdMindRuntimeDirectorySnapshot $snapshot.Path
            if ($current.Identity -cne $snapshot.Identity) { throw 'Mind runtime directory changed after validation' }
        }
    }
}

function Add-WdMindRuntimeDescriptorFiles {
    param($Set, $SourceHashes, $SourcePaths, [string] $StagingRoot)
    if ($null -eq $Set) { return }
    Assert-WdMindRuntimeDescriptorInputs $Set
    foreach ($name in $Set.Files.Keys) {
        if ($SourceHashes.Contains($name) -or $SourcePaths.Contains($name)) { throw 'Mind descriptor name already exists in source commit' }
    }
    [void](Assert-WdPathWithoutReparse -Path $StagingRoot -TrustedRoot 'C:\' -AllowMissing)
    if (Test-Path -LiteralPath $StagingRoot) { throw 'Mind descriptor staging directory already exists' }
    [void](New-Item -ItemType Directory -Path $StagingRoot)
    [void](Assert-WdPathWithoutReparse -Path $StagingRoot -TrustedRoot 'C:\' -ExpectedType Directory)
    foreach ($name in $Set.Files.Keys) {
        $snapshot = $Set.Files[$name]
        $destination = Join-Path $StagingRoot $name
        $output = [IO.File]::Open($destination, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try { $output.Write($snapshot.Bytes, 0, $snapshot.Bytes.Length) } finally { $output.Dispose() }
        $staged = Read-WdMindRuntimeFileSnapshot -Path $destination -Limit $snapshot.Limit
        if ($staged.Hash -cne $snapshot.Hash) { throw 'Mind descriptor staging hash differs' }
        $SourceHashes[$name] = $snapshot.Hash.ToUpperInvariant()
        $SourcePaths[$name] = $destination
    }
    # These are caller-selected descriptor pins, not authentication of assets,
    # model/interpreter execution, operator approval, or a same-user ABA defense.
    Assert-WdMindRuntimeDescriptorInputs $Set
}

function New-WdMindPolicyApprovalDescriptor {
    param([string] $PolicyPath, [string] $ApprovalReferenceSha256)
    if ($PolicyPath -ceq '' -and $ApprovalReferenceSha256 -ceq '') { return $null }
    if ($PolicyPath -ceq '' -or $ApprovalReferenceSha256 -cnotmatch '\A[0-9a-fA-F]{64}\z') {
        throw 'Mind policy path and approval reference must both be supplied'
    }
    $snapshot = Read-WdMindRuntimeFileSnapshot -Path $PolicyPath -Limit 65536
    if ($snapshot.Bytes.Length -ge 3 -and $snapshot.Bytes[0] -eq 0xef -and
        $snapshot.Bytes[1] -eq 0xbb -and $snapshot.Bytes[2] -eq 0xbf) { throw 'Mind policy encoding differs' }
    # Schema and identifier semantics are checked by the pinned reader. This
    # staging step binds bytes; it does not assert that the policy is usable.
    $reference = $ApprovalReferenceSha256.ToLowerInvariant()
    $text = '{"approval_reference_sha256":"' + $reference + '","identifier_policy_sha256":"' +
        $snapshot.Hash + '","schema":"wd.mind-identifier-policy-approval.v1"}'
    $bytes = (New-Object Text.UTF8Encoding($false, $true)).GetBytes($text)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $hash = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
    # Do not retain the policy text or bytes in the returned object.
    return [pscustomobject]@{ PolicyPath=$snapshot.Path; PolicyHash=$snapshot.Hash;
        DescriptorBytes=$bytes; DescriptorHash=$hash }
}

function Assert-WdMindPolicyApprovalInput {
    param($Approval)
    if ($null -eq $Approval) { return }
    $snapshot = Read-WdMindRuntimeFileSnapshot -Path $Approval.PolicyPath -Limit 65536
    if ($snapshot.Hash -cne $Approval.PolicyHash) { throw 'Mind policy input changed after validation' }
}

function Add-WdMindPolicyApprovalDescriptor {
    param($Approval, $SourceHashes, $SourcePaths, [string] $StagingRoot)
    if ($null -eq $Approval) { return }
    Assert-WdMindPolicyApprovalInput $Approval
    $name = 'wd-mind-identifier-policy-approval.json'
    if ($SourceHashes.Contains($name) -or $SourcePaths.Contains($name)) { throw 'Mind approval descriptor already exists' }
    [void](Assert-WdPathWithoutReparse -Path $StagingRoot -TrustedRoot 'C:\' -ExpectedType Directory)
    $path = Join-Path $StagingRoot $name
    $stream = [IO.File]::Open($path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.Write($Approval.DescriptorBytes, 0, $Approval.DescriptorBytes.Length) } finally { $stream.Dispose() }
    $staged = Read-WdMindRuntimeFileSnapshot -Path $path -Limit 65536
    if ($staged.Hash -cne $Approval.DescriptorHash) { throw 'Mind approval descriptor staging hash differs' }
    $SourceHashes[$name] = $Approval.DescriptorHash.ToUpperInvariant()
    $SourcePaths[$name] = $path
    Assert-WdMindPolicyApprovalInput $Approval
}

function Assert-ChildPath {
    param(
        [Parameter(Mandatory)] [string] $Parent,
        [Parameter(Mandatory)] [string] $Child
    )
    $parentFull = (Resolve-FullPath $Parent).TrimEnd('\') + '\'
    $childFull = Resolve-FullPath $Child
    if (-not $childFull.StartsWith(
            $parentFull,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw "unsafe deployment path outside '$parentFull': $childFull"
    }
    return $childFull
}

function Write-Utf8NoBomAtomic {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $Content
    )
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        [void](New-Item -ItemType Directory -Path $parent -Force)
    }
    $temporary = Join-Path $parent (
        '.{0}.{1}.tmp' -f ([System.IO.Path]::GetFileName($Path)), ([guid]::NewGuid().ToString('N'))
    )
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    try {
        [System.IO.File]::WriteAllText($temporary, $Content, $utf8)
        Move-Item -LiteralPath $temporary -Destination $Path -Force
    }
    finally {
        if (Test-Path -LiteralPath $temporary -PathType Leaf) {
            Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
        }
    }
}

function Read-Utf8DeploymentSnapshot {
    param([Parameter(Mandatory)] [string] $Path)

    $bytes = [IO.File]::ReadAllBytes($Path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $hash = [BitConverter]::ToString(
            $sha.ComputeHash($bytes)
        ).Replace('-', '')
    }
    finally {
        $sha.Dispose()
    }
    $text = [Text.Encoding]::UTF8.GetString($bytes)
    if ($text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) {
        $text = $text.Substring(1)
    }
    return [pscustomobject]@{ Hash = $hash; Text = $text }
}

function Read-WdManualExecutionApprovalText {
    param([Parameter(Mandatory)] [string] $Path)
    if ($Path -cnotmatch '^[Cc]:[\\/]') {
        throw 'manual execution approval record must use an absolute persistent C: path'
    }
    $full = Assert-WdPathWithoutReparse -Path $Path -TrustedRoot 'C:\' -ExpectedType Leaf
    $stream = [IO.File]::Open($full, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        # Bind the read to the opened file, not a separately checked path that
        # an ancestor rename/junction can redirect before File.Open.
        if (-not ('WaggleDance.WdManualApprovalFileV1' -as [type])) {
            Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;
namespace WaggleDance {
    public static class WdManualApprovalFileV1 {
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        private static extern uint GetFinalPathNameByHandleW(
            SafeFileHandle handle, StringBuilder path, uint length, uint flags);
        public static string FinalPath(SafeFileHandle handle) {
            var path = new StringBuilder(32768);
            uint length = GetFinalPathNameByHandleW(handle, path, (uint)path.Capacity, 0);
            if (length == 0 || length >= path.Capacity)
                throw new IOException("manual execution approval handle path is unavailable");
            return path.ToString();
        }
    }
}
'@
        }
        $openedPath = [WaggleDance.WdManualApprovalFileV1]::FinalPath($stream.SafeFileHandle)
        if (-not $openedPath.StartsWith('\\?\', [StringComparison]::Ordinal) -or
            -not $openedPath.Substring(4).Equals($full, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'manual execution approval opened handle differs from its checked path'
        }
        # Bound the actual read, not just a racy pre-read file length.
        $bytes = New-Object byte[] 16385
        $count = 0
        while ($count -lt $bytes.Length) {
            $read = $stream.Read($bytes, $count, $bytes.Length - $count)
            if ($read -eq 0) { break }
            $count += $read
        }
        if ($count -eq 0 -or $count -gt 16384) {
            throw 'manual execution approval record must contain 1 to 16384 UTF-8 bytes'
        }
        $utf8 = New-Object Text.UTF8Encoding($false, $true)
        $text = $utf8.GetString($bytes, 0, $count)
        if ($text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) { $text = $text.Substring(1) }
        return $text
    } finally {
        $stream.Dispose()
    }
}

function ConvertFrom-WdManualExecutionApprovalRecord {
    param(
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $Text,
        [Parameter(Mandatory)] [string] $SourceCommit,
        [Parameter(Mandatory)] [string] $DefinitionHash,
        [Parameter(Mandatory)] $Definition,
        [Parameter(Mandatory)] [string[]] $FleetAgentNames
    )
    # Reuse the exact archived reader's strict duplicate/ASCII JSON contract;
    # ConvertFrom-Json alone loses duplicate keys and permits type coercion.
    if (-not ('WaggleDance.BridgeSnapshotDeltaV1.JsonContractValidator' -as [type]) -or
        $Text.Length -gt 16384 -or -not $Text.TrimStart().StartsWith('{') -or
        -not [WaggleDance.BridgeSnapshotDeltaV1.JsonContractValidator]::IsValid($Text)) {
        throw 'manual execution approval record violates the strict JSON contract'
    }
    $record = ConvertFrom-Json -InputObject $Text -ErrorAction Stop
    $names = @($record.PSObject.Properties.Name | Sort-Object)
    if ($record -isnot [pscustomobject] -or
        ($names -join ',') -cne 'agent,approval_reference_sha256,definition_sha256,enabled,schema,source_commit,tools') {
        throw 'manual execution approval record must have exactly the seven canonical fields'
    }
    foreach ($name in @('schema', 'agent', 'source_commit', 'definition_sha256', 'approval_reference_sha256')) {
        if ($record.$name -isnot [string]) { throw 'manual execution approval string field has the wrong type' }
    }
    $agents = @('codex-lead-1', 'codex-tools-1', 'fable-5', 'claude-rco-1', 'claude-rco-2', 'claude-rco-3')
    $manualPaths = @('tools/merge_with_bridge_receipt.py', 'tools/write_bridge_consensus_merge_receipt.py')
    if ($record.schema -cne 'wd.bridge-manual-execution-activation.v1' -or
        $record.enabled -isnot [bool] -or $record.enabled -ne $true -or
        $SourceCommit -cnotmatch '^[0-9a-f]{40}$' -or $record.source_commit -cne $SourceCommit -or
        $DefinitionHash -cnotmatch '^[0-9A-F]{64}$' -or $record.definition_sha256 -cne $DefinitionHash -or
        $record.approval_reference_sha256 -cnotmatch '\A[0-9A-Fa-f]{64}\z' -or
        $record.agent -cnotin $agents -or $record.agent -cnotin $FleetAgentNames -or
        $record.tools -isnot [Array] -or $record.tools.Count -ne 2) {
        throw 'manual execution approval binding or field type is invalid'
    }
    foreach ($tool in $record.tools) {
        if ($tool -isnot [string]) { throw 'manual execution approval tool has the wrong type' }
    }
    if ((@($record.tools | Sort-Object) -join ',') -cne ($manualPaths -join ',')) {
        throw 'manual execution approval must name exactly the two fixed tools'
    }
    foreach ($tool in $manualPaths) {
        if ($tool -cnotin @($Definition.python_files)) {
            throw 'manual execution approval driver is absent from the selected pinned package'
        }
    }
    $capability = $Definition.PSObject.Properties['manual_execution_capability']
    if ($null -eq $capability -or
        [string]$capability.Value.schema -cne 'wd.bridge-manual-execution.v1' -or
        [string]$capability.Value.invocation_marker -cne '--wd-manual-execution' -or
        [string]$capability.Value.entrypoints.merge -cne $manualPaths[0] -or
        [string]$capability.Value.entrypoints.receipt -cne $manualPaths[1]) {
        throw 'manual execution approval does not match the selected package capability'
    }
    # Derive these bindings from the selected package; a hash is not a signature.
    return [ordered]@{
        schema = 'wd.bridge-manual-execution-activation.v1'
        enabled = $true
        agent = $record.agent
        source_commit = $SourceCommit
        definition_sha256 = $DefinitionHash
        approval_reference_sha256 = $record.approval_reference_sha256
        tools = $manualPaths
    }
}

function Assert-WdManualExecutionManifestBinding {
    param(
        [Parameter(Mandatory)] $Deployment,
        [AllowNull()] $ExpectedActivation,
        [Parameter(Mandatory)] [string] $SourceCommit,
        [Parameter(Mandatory)] [string] $DefinitionHash,
        [Parameter(Mandatory)] $Definition,
        [Parameter(Mandatory)] [AllowEmptyCollection()] [string[]] $FleetAgentNames
    )
    $property = $Deployment.PSObject.Properties['manual_execution']
    if ($null -eq $ExpectedActivation) {
        if ($null -ne $property) { throw 'existing manifest has an unrequested manual execution activation' }
        return
    }
    if ($null -eq $property) { throw 'existing manifest is missing the requested manual execution activation' }
    $actual = ConvertFrom-WdManualExecutionApprovalRecord -Text ($property.Value | ConvertTo-Json -Depth 8 -Compress) `
        -SourceCommit $SourceCommit -DefinitionHash $DefinitionHash -Definition $Definition -FleetAgentNames $FleetAgentNames
    if (($actual | ConvertTo-Json -Depth 8 -Compress) -cne ($ExpectedActivation | ConvertTo-Json -Depth 8 -Compress)) {
        throw 'existing manifest manual execution activation differs from the explicit approval record'
    }
}

function ConvertFrom-WdRcoEnrollmentRecord {
    param(
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $Text,
        [Parameter(Mandatory)] [string] $SourceCommit
    )
    if (-not ('WaggleDance.BridgeSnapshotDeltaV1.JsonContractValidator' -as [type]) -or
        $Text.Length -gt 16384 -or -not $Text.TrimStart().StartsWith('{') -or
        -not [WaggleDance.BridgeSnapshotDeltaV1.JsonContractValidator]::IsValid($Text)) {
        throw 'RCO enrollment violates the strict bounded JSON contract'
    }
    $jsonArguments = @{ InputObject = $Text; ErrorAction = 'Stop' }
    if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) {
        $jsonArguments['DateKind'] = 'String'
    }
    $record = ConvertFrom-Json @jsonArguments
    $keys = @('schema','agent','agent_uuid','generation','session_id','run_id',
        'native_conversation_id','cli_kind','native_pid','launcher_pid',
        'native_process_start_utc','observed_at_utc','token_integrity','elevated',
        'fresh_history_free','no_mind_authorship','no_measurement_case_access')
    if ($record -isnot [pscustomobject] -or
        (@($record.PSObject.Properties.Name | Sort-Object) -join ',') -cne (@($keys | Sort-Object) -join ',')) {
        throw 'RCO enrollment requires exactly the seventeen v1 fields'
    }
    foreach ($key in @('schema','agent','agent_uuid','generation','session_id','run_id',
        'native_conversation_id','cli_kind','native_process_start_utc','observed_at_utc','token_integrity')) {
        if ($record.$key -isnot [string] -or [string]::IsNullOrWhiteSpace($record.$key) -or
            $record.$key -cne $record.$key.Trim()) {
            throw 'RCO enrollment string field has invalid type or whitespace'
        }
    }
    $roleUuid = '3c7f49d1-5ac4-4ca3-8ff5-6c97c07bcd50'
    if ($record.schema -cne 'wd.rco-enrollment.v1' -or $record.agent -cne 'claude-rco-3' -or
        $record.agent_uuid -cne $roleUuid -or $SourceCommit -cnotmatch '\A[0-9a-f]{40}\z' -or
        $record.generation.ToLowerInvariant() -cne $SourceCommit -or
        $record.native_conversation_id -cnotmatch '\A[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\z' -or
        $record.native_conversation_id.ToLowerInvariant() -ceq $roleUuid -or
        $record.cli_kind -cne 'claude.exe' -or $record.token_integrity -cne 'Medium') {
        throw 'RCO enrollment identity, generation or medium-token attestation is invalid'
    }
    foreach ($key in @('native_pid','launcher_pid')) {
        if (($record.$key -isnot [int] -and $record.$key -isnot [long]) -or $record.$key -le 0) {
            throw 'RCO enrollment process IDs must be positive native JSON integers'
        }
    }
    foreach ($key in @('fresh_history_free','no_mind_authorship','no_measurement_case_access')) {
        if ($record.$key -isnot [bool] -or $record.$key -ne $true) {
            throw 'RCO enrollment requires explicit operator attestations'
        }
    }
    if ($record.elevated -isnot [bool] -or $record.elevated -ne $false) {
        throw 'RCO enrollment refuses elevated or unknown token state'
    }
    $times = @()
    foreach ($key in @('native_process_start_utc','observed_at_utc')) {
        $match = [regex]::Match($record.$key, '\A([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(?:\.([0-9]{1,9}))?(?:Z|\+00:00)\z')
        if (-not $match.Success) { throw 'RCO enrollment requires an explicit UTC observation' }
        $second = [datetime]::ParseExact($match.Groups[1].Value, "yyyy-MM-dd'T'HH:mm:ss", [Globalization.CultureInfo]::InvariantCulture)
        # Preserve fractional precision beyond DateTime ticks, like the Python reader.
        $times += $second.ToString('yyyyMMddHHmmss', [Globalization.CultureInfo]::InvariantCulture) + $match.Groups[2].Value.PadRight(9, '0')
    }
    if ([string]::CompareOrdinal($times[0], $times[1]) -gt 0) {
        throw 'RCO enrollment observation precedes process start'
    }
    # This is an operator-attested prior observation, NOT a future PID pin or
    # a cryptographic certificate. Runtime adoption remains a separate decision.
    $result = [ordered]@{}
    foreach ($key in $keys) { $result[$key] = $record.$key }
    return $result
}

function Add-WdRcoEnrollmentToManifest {
    param([Parameter(Mandatory)] $Manifest, [AllowNull()] $Enrollment)
    if ($null -ne $Enrollment) {
        $Manifest['rco_enrollments'] = [ordered]@{ 'claude-rco-3' = $Enrollment }
    }
}

function Assert-WdRcoEnrollmentManifestBinding {
    param([Parameter(Mandatory)] $Deployment, [AllowNull()] $ExpectedEnrollment,
        [Parameter(Mandatory)] [string] $SourceCommit)
    $property = $Deployment.PSObject.Properties['rco_enrollments']
    if ($null -eq $ExpectedEnrollment) {
        if ($null -ne $property) { throw 'existing manifest contains unrequested RCO enrollment' }
        return
    }
    if ($null -eq $property -or $property.Value -isnot [pscustomobject] -or
        (@($property.Value.PSObject.Properties.Name) -join ',') -cne 'claude-rco-3') {
        throw 'existing manifest is missing or changes the requested RCO enrollment'
    }
    $actual = ConvertFrom-WdRcoEnrollmentRecord -Text ($property.Value.'claude-rco-3' | ConvertTo-Json -Depth 8 -Compress) -SourceCommit $SourceCommit
    if (($actual | ConvertTo-Json -Depth 8 -Compress) -cne ($ExpectedEnrollment | ConvertTo-Json -Depth 8 -Compress)) {
        throw 'existing manifest RCO enrollment differs from the explicit attestation'
    }
}

function Get-RelativeFileHashMap {
    param([Parameter(Mandatory)] [string] $Root)
    $result = [ordered]@{}
    foreach ($file in @(
            Get-ChildItem -LiteralPath $Root -File |
                Where-Object { $_.Name -ne 'deployment-manifest.json' } |
                Sort-Object Name
        )) {
        $result[$file.Name] = (
            Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256
        ).Hash.ToUpperInvariant()
    }
    return $result
}

function Assert-WdGitAclRules {
    param([string]$OwnerSid, [object[]]$Rules, [switch]$VolumeRoot)
    $trusted = @('S-1-5-18', 'S-1-5-32-544',
        'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
    if ($OwnerSid -cnotin $trusted) { throw 'Mind Git: untrusted path owner' }
    [long]$mutation = 0x500D0156
    # Creating a different child of C:\ does not replace an existing protected
    # Program Files path. Delete-child/ACL/owner/attribute rights remain checked.
    if ($VolumeRoot) { $mutation = $mutation -band (-bnot 4) }
    foreach ($rule in $Rules) {
        if ($rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
            ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly)) { continue }
        if ($rule.IdentityReference.Value -cnotin $trusted -and
            (([long]$rule.FileSystemRights -band 0xffffffffL) -band $mutation)) {
            throw 'Mind Git: untrusted path mutation permission'
        }
    }
}

function Assert-WdNativeGitPath {
    param([string]$Path)
    # This slice supports one native installation layout. In particular the
    # fleet's cmd/git.exe launcher is NOT an anchor for this executable.
    if ($Path -cne 'C:\Program Files\Git\mingw64\bin\git.exe') {
        throw 'Mind Git: unsupported native executable path'
    }
    [void](Assert-WdPathWithoutReparse -Path $Path -TrustedRoot 'C:\' -ExpectedType Leaf)
    $current = $Path
    while ($current) {
        $acl = Get-Acl -LiteralPath $current -ErrorAction Stop
        $descriptor = New-Object Security.AccessControl.RawSecurityDescriptor ($acl.GetSecurityDescriptorBinaryForm(), 0)
        if ($null -eq $descriptor.DiscretionaryAcl) { throw 'Mind Git: null path DACL' }
        Assert-WdGitAclRules -OwnerSid $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value `
            -Rules @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) `
            -VolumeRoot:($current -ceq 'C:\')
        $current = [IO.Path]::GetDirectoryName($current)
    }
}

function Assert-WdPinnedGit {
    param($GitContext)
    if ($null -eq $GitContext -or $GitContext.ExpectedSha256 -cnotmatch '^[0-9A-F]{64}$' -or
        $GitContext.Handle -isnot [IO.FileStream] -or -not $GitContext.Handle.CanRead -or
        $GitContext.Handle.Name -cne $GitContext.Path) { throw 'Mind Git: pin descriptor invalid' }
    Assert-WdNativeGitPath -Path $GitContext.Path
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $GitContext.Handle.Position = 0
        $heldHash = [BitConverter]::ToString($sha.ComputeHash($GitContext.Handle)).Replace('-', '')
    }
    finally { $sha.Dispose() }
    if ($heldHash -cne $GitContext.ExpectedSha256 -or
        (Get-FileHash -LiteralPath $GitContext.Path -Algorithm SHA256).Hash -cne $GitContext.ExpectedSha256) {
        throw 'Mind Git: pin mismatch'
    }
}

function Open-WdPinnedGit {
    param([string]$GitExecutable, [string]$ExpectedGitSha256)
    if ([string]::IsNullOrEmpty($GitExecutable) -or $ExpectedGitSha256 -cnotmatch '^[0-9A-Fa-f]{64}$') {
        throw 'Mind Git: pin inputs missing or invalid'
    }
    Assert-WdNativeGitPath -Path $GitExecutable
    $handle = [IO.File]::Open($GitExecutable, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        $context = [pscustomobject]@{Path=$GitExecutable;ExpectedSha256=$ExpectedGitSha256.ToUpperInvariant();Handle=$handle}
        Assert-WdPinnedGit -GitContext $context
        return $context
    }
    catch { $handle.Dispose(); throw }
}

function Close-WdPinnedGit {
    param($GitContext)
    if ($null -ne $GitContext -and $null -ne $GitContext.Handle) {
        $GitContext.Handle.Dispose()
        $GitContext.Handle = $null
    }
}

function ConvertTo-WdGitArgument {
    param([AllowEmptyString()][string]$Value)
    # Windows CommandLineToArgvW quoting, including embedded quotes and trailing
    # backslashes. The process starts a literal executable, never a shell.
    return '"' + [regex]::Replace([regex]::Replace($Value, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
}

function New-WdGitProcessInfo {
    param($GitContext, [string]$Worktree, [string[]]$ArgumentList)
    Assert-WdPinnedGit -GitContext $GitContext
    if (@(Get-ChildItem Env: | Where-Object { $_.Name -like 'GIT_*' }).Count) {
        throw 'Mind Git: inherited Git environment override'
    }
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $GitContext.Path
    $info.Arguments = (@('--no-replace-objects', '-c', 'core.fsmonitor=false', '-C', $Worktree) + $ArgumentList |
        ForEach-Object { ConvertTo-WdGitArgument -Value $_ }) -join ' '
    $info.WorkingDirectory = [IO.Path]::GetDirectoryName($GitContext.Path)
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.EnvironmentVariables['GIT_CONFIG_NOSYSTEM'] = '1'
    $info.EnvironmentVariables['GIT_CONFIG_GLOBAL'] = 'NUL'
    return $info
}

function Invoke-GitCapture {
    param(
        [Parameter(Mandatory)] [string] $Worktree,
        [Parameter(Mandatory)] [string[]] $ArgumentList,
        $GitContext
    )
    $info = New-WdGitProcessInfo -GitContext $GitContext -Worktree $Worktree -ArgumentList $ArgumentList
    $child = New-Object Diagnostics.Process
    $child.StartInfo = $info
    try {
        if (-not $child.Start()) { throw 'Mind Git: process did not start' }
        $stdout = $child.StandardOutput.ReadToEndAsync()
        $stderr = $child.StandardError.ReadToEndAsync()
        $child.WaitForExit()
        $text = $stdout.GetAwaiter().GetResult() + $stderr.GetAwaiter().GetResult()
        Assert-WdPinnedGit -GitContext $GitContext
        $output = @()
        if ($text.Length) { $output = @([regex]::Split($text.TrimEnd([char[]]"`r`n"), '\r?\n')) }
        return [pscustomobject]@{ExitCode=$child.ExitCode;Output=$output;Text=$text.Trim()}
    }
    finally { $child.Dispose() }
}

function Assert-WdMindPackageRoot {
    param([Parameter(Mandatory)][string]$Path)
    if ($Path -cnotmatch '^[Cc]:[\\/]' -or $Path -match '(^|[\\/])(\.|\.\.)([\\/]|$)' -or
        $Path -match '(^|[\\/])[^\\/]*[. ]([\\/]|$)' -or $Path -match '~[0-9]' -or $Path.Contains('"')) {
        throw 'Mind package: unsafe root spelling'
    }
    return Assert-WdPathWithoutReparse -Path $Path -TrustedRoot 'C:\' -ExpectedType Directory
}

function Get-WdMindGitBlobSha256 {
    param([string]$Worktree, [string]$BlobOid, [long]$ExpectedSize, $GitContext)
    # Raw Git objects, without working-tree filters or archive substitutions.
    $info = New-WdGitProcessInfo -GitContext $GitContext -Worktree $Worktree -ArgumentList @('cat-file','blob',$BlobOid)
    $child = New-Object Diagnostics.Process
    $child.StartInfo = $info
    $bytes = New-Object IO.MemoryStream
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        if (-not $child.Start()) { throw 'Mind package: Git blob read did not start' }
        $stderr = $child.StandardError.ReadToEndAsync()
        $child.StandardOutput.BaseStream.CopyTo($bytes)
        $child.WaitForExit()
        Assert-WdPinnedGit -GitContext $GitContext
        if ($child.ExitCode -ne 0 -or $bytes.Length -ne $ExpectedSize) {
            throw ('Mind package: Git blob read failed or changed size: ' + $stderr.GetAwaiter().GetResult())
        }
        $bytes.Position = 0
        return [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '')
    }
    finally { $sha.Dispose(); $bytes.Dispose(); $child.Dispose() }
}

function Get-WdMindPackageMembers {
    param([Parameter(Mandatory)][string]$Worktree, [Parameter(Mandatory)][string]$Commit, $GitContext)
    try {
        Assert-WdPinnedGit -GitContext $GitContext
        $root = Assert-WdMindPackageRoot -Path $Worktree
        if ($Commit -cnotmatch '^[0-9a-f]{40}$') { throw 'invalid frozen commit' }
        foreach ($key in @('GIT_DIR','GIT_WORK_TREE','GIT_INDEX_FILE','GIT_OBJECT_DIRECTORY','GIT_ALTERNATE_OBJECT_DIRECTORIES')) {
            if ([Environment]::GetEnvironmentVariable($key, 'Process')) { throw 'inherited Git root override' }
        }
        $head = Invoke-GitCapture -GitContext $GitContext -Worktree $root -ArgumentList @('--no-replace-objects','rev-parse','HEAD')
        if ($head.ExitCode -ne 0 -or $head.Text -cne $Commit) { throw 'HEAD differs from the frozen commit' }
        $top = Invoke-GitCapture -GitContext $GitContext -Worktree $root -ArgumentList @('--no-replace-objects','rev-parse','--show-toplevel')
        if ($top.ExitCode -ne 0 -or (Resolve-FullPath $top.Text) -cne $root) { throw 'source is not the exact repository root' }
        $tree = Invoke-GitCapture -GitContext $GitContext -Worktree $root -ArgumentList @('--no-replace-objects','ls-tree','--full-tree',$Commit)
        if ($tree.ExitCode -ne 0) { throw 'cannot read frozen root tree' }
        foreach ($line in $tree.Output) {
            $pair = ([string]$line) -split '\t', 2
            if ($pair.Count -eq 2 -and $pair[1] -ieq 'tools' -and $pair[1] -cne 'tools') { throw 'case alias of tools root' }
        }
        $tree = Invoke-GitCapture -GitContext $GitContext -Worktree $root -ArgumentList @('--no-replace-objects','-c','core.quotepath=false','ls-tree','-r','--full-tree',$Commit,'--','tools')
        if ($tree.ExitCode -ne 0) { throw 'cannot enumerate frozen tools tree' }
        $rows = @{}
        $names = New-Object 'System.Collections.Generic.List[string]'
        foreach ($line in $tree.Output) {
            $pair = ([string]$line) -split '\t', 2
            if ($pair.Count -ne 2 -or $pair[1] -inotmatch '^"?tools/wd_mind_') { continue }
            $relative = [string]$pair[1]
            if ($relative -cnotmatch '^tools/wd_mind_[a-z0-9_]+\.py$' -or
                $pair[0] -cnotmatch '^(100644|100755) blob ([0-9a-f]{40})$' -or $rows.ContainsKey($relative)) {
                throw ('unsafe, aliased or non-blob Mind member: ' + $relative)
            }
            $oid = $Matches[2]
            $rows[$relative] = $oid
            $names.Add($relative)
            if ($names.Count -gt 256) { throw 'member count exceeds 256' }
        }
        $names.Sort([StringComparer]::Ordinal)
        $dirty = Invoke-GitCapture -GitContext $GitContext -Worktree $root -ArgumentList @('--no-replace-objects','-c','core.fsmonitor=false','-c','core.excludesFile=','status','--porcelain','--untracked-files=all','--ignored=matching','--',':(icase,glob)tools/wd_mind_*')
        if ($dirty.ExitCode -ne 0 -or $dirty.Output.Count -ne 0) { throw 'dirty, missing or untracked Mind source' }
        $tools = Join-Path $root 'tools'
        if (Test-Path -LiteralPath $tools) {
            [void](Assert-WdPathWithoutReparse -Path $tools -TrustedRoot 'C:\' -ExpectedType Directory)
            foreach ($item in @(Get-ChildItem -LiteralPath $tools -Force -ErrorAction Stop | Where-Object { $_.Name -imatch '^wd_mind_' })) {
                $relative = 'tools/' + $item.Name
                if (-not $rows.ContainsKey($relative) -or $relative -cnotmatch '^tools/wd_mind_[a-z0-9_]+\.py$') { throw 'extra or aliased physical Mind source' }
            }
        }
        $files = New-Object 'System.Collections.Generic.List[object]'
        [long]$total = 0
        foreach ($relative in $names) {
            $oid = [string]$rows[$relative]
            $size = Invoke-GitCapture -GitContext $GitContext -Worktree $root -ArgumentList @('--no-replace-objects','cat-file','-s',$oid)
            if ($size.ExitCode -ne 0 -or $size.Text -cnotmatch '^[0-9]+$') { throw 'cannot read blob size' }
            [long]$length = $size.Text
            $total += $length
            if ($length -gt 8388608 -or $total -gt 67108864) { throw 'Mind blob byte budget exceeded' }
            $hash = Get-WdMindGitBlobSha256 -GitContext $GitContext -Worktree $root -BlobOid $oid -ExpectedSize $length
            $path = Assert-WdPathWithoutReparse -Path (Join-Path $root $relative) -TrustedRoot 'C:\' -ExpectedType Leaf
            if ((Get-Item -LiteralPath $path).Length -ne $length -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -cne $hash) {
                throw ('source bytes differ from the frozen blob: ' + $relative)
            }
            $files.Add([pscustomobject]@{relative_path=$relative;blob_oid=$oid;sha256=$hash;bytes=$length})
        }
        return [pscustomobject]@{schema='wd.mind-code-package.v1';source_commit=$Commit;files=@($files.ToArray())}
    }
    catch { throw ('Mind package: ' + $_.Exception.Message) }
}

function Assert-WdMindPackageArchive {
    param([Parameter(Mandatory)][string]$ArchiveRoot, [Parameter(Mandatory)]$Package)
    try {
        $root = Assert-WdMindPackageRoot -Path $ArchiveRoot
        if ($Package.schema -cne 'wd.mind-code-package.v1' -or $Package.source_commit -cnotmatch '^[0-9a-f]{40}$' -or
            $Package.files -isnot [array] -or @($Package.files).Count -gt 256) { throw 'invalid member snapshot' }
        $seen = @{}
        foreach ($member in $Package.files) {
            $relative = [string]$member.relative_path
            if ($relative -cnotmatch '^tools/wd_mind_[a-z0-9_]+\.py$' -or $seen.ContainsKey($relative)) { throw 'invalid archive member path' }
            $seen[$relative] = $true
            $path = Assert-WdPathWithoutReparse -Path (Join-Path $root $relative) -TrustedRoot 'C:\' -ExpectedType Leaf
            if ((Get-Item -LiteralPath $path).Length -ne $member.bytes -or
                (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -cne $member.sha256) { throw ('archive bytes differ from Git blob: ' + $relative) }
        }
        $tools = Join-Path $root 'tools'
        if (Test-Path -LiteralPath $tools) {
            [void](Assert-WdPathWithoutReparse -Path $tools -TrustedRoot 'C:\' -ExpectedType Directory)
            foreach ($item in @(Get-ChildItem -LiteralPath $tools -Force -ErrorAction Stop | Where-Object { $_.Name -imatch '^wd_mind_' })) {
                if (-not $seen.ContainsKey('tools/' + $item.Name) -or $item.PSIsContainer -or
                    ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'extra, aliased or linked archive member' }
            }
        }
    }
    catch { throw ('Mind package: ' + $_.Exception.Message) }
}

function New-ForwardingWrapper {
    param(
        [Parameter(Mandatory)] [string] $Target,
        [Parameter(Mandatory)] [string] $ExpectedHash,
        [Parameter(Mandatory)] [string] $ExpectedManifestHash,
        [Parameter(Mandatory)]
        [ValidateSet('fleet', 'agent', 'tools', 'supervisor', 'grok', 'library')]
        [string] $WrapperKind,
        [string] $FixedAgent = '',
        [string] $ExpectedFinalCommit = '',
        [string] $ExpectedFinalManifestHash = ''
    )

    $parameterBlock = switch ($WrapperKind) {
        'library' { 'param()' }
        'grok' {
@'
param([string] $PromptPath = '', [string] $TaskId = '', [switch] $Status,
    [string] $ExceptionPath = '', [string] $ExceptionSha256 = '',
    [switch] $ReadOnly, [switch] $Inventory, [string] $RepositoryPath = '', [string] $Commit = '',
    [ValidateRange(2, 8)] [int] $MaxRounds = 6, [string] $AcknowledgeInheritedSurface = '', [string] $RequestedBy = '')
'@
        }
        'fleet' {
@'
param(
    [string] $ManifestPath = '',
    [string] $RunId = '',
    [string] $ExternalSessionsPath = '',
    [string] $ExternalSessionsHash = '',
    [ValidateRange(10, 300)]
    [int] $HandshakeTimeoutSeconds = 90,
    [switch] $SkipCliUpdate,
    [switch] $NoBridgeConversation,
    [switch] $SupervisorOff,
    [switch] $Auto,
    [switch] $Apply,
    [switch] $DryRun
)
'@
        }
        'agent' {
            if ($FixedAgent) {
@'
param(
    [string] $RunId = '',
    [string] $ManifestPath = '',
    [string] $HandshakeDirectory = '',
    [string] $ExpectedManifestHash = '',
    [string] $ExternalSessionsPath = '',
    [string] $ExternalSessionsHash = '',
    [switch] $DryRun,
    [switch] $FreshSession,
    [string] $SupersededThreadId = '',
    [string] $FreshSessionReason = ''
)
'@
            }
            else {
@'
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^[a-z][a-z0-9_-]{1,32}$')]
    [string] $Agent,
    [string] $RunId = '',
    [string] $ManifestPath = '',
    [string] $HandshakeDirectory = '',
    [string] $ExpectedManifestHash = '',
    [string] $ExternalSessionsPath = '',
    [string] $ExternalSessionsHash = '',
    [switch] $DryRun,
    [switch] $FreshSession,
    [string] $SupersededThreadId = '',
    [string] $FreshSessionReason = ''
)
'@
            }
        }
        'tools' {
@'
param(
    [string] $ConfigPath = '',
    [Parameter(Mandatory)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string] $Generation,
    [switch] $ValidateOnly
)
'@
        }
        'supervisor' {
@'
param(
    [switch] $Apply,
    [switch] $BridgeWorkersOnly,
    [string] $ConfigPath = '',
    [string] $LogPath = ''
)
'@
        }
    }

    $targetInvocation = if ($WrapperKind -ceq 'fleet') {
@'
if ($Auto -and ($Apply -or $DryRun)) {
    throw 'Auto cannot be combined with Apply or DryRun'
}
$targetParameters = @{}
foreach ($key in $PSBoundParameters.Keys) {
    if ($key -notin @('Auto', 'Apply', 'DryRun')) {
        $targetParameters[$key] = $PSBoundParameters[$key]
    }
}
function Test-WdWrapperAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    try {
        $principal = New-Object Security.Principal.WindowsPrincipal($identity)
        return $principal.IsInRole(
            [Security.Principal.WindowsBuiltInRole]::Administrator
        )
    }
    finally {
        $identity.Dispose()
    }
}
function ConvertTo-WdSingleQuotedLiteral {
    param([AllowEmptyString()] [string] $Value)
    return "'" + $Value.Replace("'", "''") + "'"
}
if ($Auto -and -not (Test-WdWrapperAdministrator)) {
    # Supervisor OFF is standing: WD-Supervisor stays exactly Disabled, so after the one elevated
    # restore below, this non-elevated process starts four supervised watchers and Tools through
    # one run of the installed wd_supervisor.ps1 (WdBridgeLimitedBootstrap.ps1). Verify and load
    # that helper first, so a bad bundle refuses before any elevation.
    $wdBridgeBundleRoot = Split-Path -Parent $manifestPath
    $wdBridgeHelper = Join-Path $wdBridgeBundleRoot 'WdBridgeLimitedBootstrap.ps1'
    $wdBridgeManifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (
        -not (Test-Path -LiteralPath $wdBridgeHelper -PathType Leaf) -or
        (Get-FileHash -LiteralPath $wdBridgeHelper -Algorithm SHA256).Hash -cne
            [string]$wdBridgeManifest.files.'WdBridgeLimitedBootstrap.ps1'
    ) {
        throw "WD reboot bundle integrity mismatch for $wdBridgeHelper"
    }
    . $wdBridgeHelper
    $elevationHost = [IO.Path]::Combine(
        [Environment]::SystemDirectory,
        'WindowsPowerShell',
        'v1.0',
        'powershell.exe'
    )
    if (-not (Test-Path -LiteralPath $elevationHost -PathType Leaf)) {
        throw "stable Windows PowerShell elevation host is missing: $elevationHost"
    }
    $commandParts = New-Object 'System.Collections.Generic.List[string]'
    [void]$commandParts.Add('&')
    [void]$commandParts.Add((ConvertTo-WdSingleQuotedLiteral -Value $PSCommandPath))
    foreach ($name in @('ManifestPath', 'RunId', 'ExternalSessionsPath', 'ExternalSessionsHash')) {
        if ($targetParameters.ContainsKey($name)) {
            [void]$commandParts.Add("-$name")
            [void]$commandParts.Add((ConvertTo-WdSingleQuotedLiteral -Value ([string]$targetParameters[$name])))
        }
    }
    if ($targetParameters.ContainsKey('HandshakeTimeoutSeconds')) {
        [void]$commandParts.Add('-HandshakeTimeoutSeconds')
        [void]$commandParts.Add(([int]$targetParameters['HandshakeTimeoutSeconds']).ToString(
            [Globalization.CultureInfo]::InvariantCulture
        ))
    }
    if ([bool]$targetParameters['SkipCliUpdate']) {
        [void]$commandParts.Add('-SkipCliUpdate')
    }
    if ([bool]$targetParameters['NoBridgeConversation']) {
        [void]$commandParts.Add('-NoBridgeConversation')
    }
    if ([bool]$targetParameters['SupervisorOff']) {
        [void]$commandParts.Add('-SupervisorOff')
    }
    $elevationLogRoot = Join-Path (
        Split-Path -Parent $PSCommandPath
    ) 'wd-reboot-runtime\elevated-auto'
    if (-not (Test-Path -LiteralPath $elevationLogRoot)) {
        [void](New-Item -ItemType Directory -Path $elevationLogRoot -Force)
    }
    $elevationLogRootItem = Get-Item -LiteralPath $elevationLogRoot -Force
    if (
        -not $elevationLogRootItem.PSIsContainer -or
        ($elevationLogRootItem.Attributes -band [IO.FileAttributes]::ReparsePoint)
    ) {
        throw "automatic restore log root is not a trusted directory: $elevationLogRoot"
    }
    $elevationLogPath = Join-Path $elevationLogRoot (
        'auto-{0}-{1}-{2}.log' -f
            [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ'),
            $PID,
            [Guid]::NewGuid().ToString('N')
    )
    $restoreCommand = $commandParts -join ' '
    $loggedCommand = @(
        '$ErrorActionPreference = ''Stop''',
        '$restoreExitCode = 0',
        '$transcriptStarted = $false',
        'try {',
        ("  Start-Transcript -LiteralPath {0} -Force | Out-Null" -f
            (ConvertTo-WdSingleQuotedLiteral -Value $elevationLogPath)),
        '  $transcriptStarted = $true',
        "  Write-Host 'Running byte-inert fleet preflight before automatic restore...'",
        ("  {0} -DryRun" -f $restoreCommand),
        "  Write-Host 'Preflight passed; applying the verified fleet restore...'",
        ("  {0} -Apply" -f $restoreCommand),
        '}',
        'catch {',
        '  $restoreExitCode = 1',
        '  [Console]::Error.WriteLine(($_ | Out-String))',
        '}',
        'finally {',
        '  if ($transcriptStarted) { Stop-Transcript | Out-Null }',
        '}',
        'exit $restoreExitCode'
    ) -join [Environment]::NewLine
    $encodedCommand = [Convert]::ToBase64String(
        [Text.Encoding]::Unicode.GetBytes($loggedCommand)
    )
    Write-Host 'Automatic restore needs Administrator rights for Task Scheduler; requesting one UAC elevation...' -ForegroundColor Yellow
    try {
        $elevated = Start-Process `
            -FilePath $elevationHost `
            -Verb RunAs `
            -ArgumentList @(
                '-NoLogo',
                '-NoProfile',
                '-ExecutionPolicy', 'Bypass',
                '-EncodedCommand', $encodedCommand
            ) `
            -WindowStyle Hidden `
            -PassThru `
            -ErrorAction Stop
        # Wait for restore itself, not its long-lived conversation-viewer child.
        # Retain a process handle so exit status remains available after exit.
        $null = $elevated.Handle
        Write-Host "Administrator restore started; progress log: $elevationLogPath"
        $restoreWatch = [Diagnostics.Stopwatch]::StartNew()
        while (-not $elevated.WaitForExit(30000)) {
            Write-Host ("Restore still running; elapsed={0}s; log={1}" -f
                [int]$restoreWatch.Elapsed.TotalSeconds, $elevationLogPath)
            $lastProgress = @(Get-Content -LiteralPath $elevationLogPath -Tail 8 -ErrorAction SilentlyContinue |
                Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) } | Select-Object -Last 1)
            if ($lastProgress.Count) {
                $line = [string]$lastProgress[0]
                Write-Host ('  ' + $line.Substring(0, [Math]::Min(500, $line.Length)))
            }
        }
        $elevated.WaitForExit()
        $elevated.Refresh()
    }
    catch {
        throw "automatic Administrator elevation was declined or failed: $($_.Exception.Message)"
    }
    if ([int]$elevated.ExitCode -ne 0) {
        Write-Host "Elevated restore failure log: $elevationLogPath" -ForegroundColor Yellow
        if (Test-Path -LiteralPath $elevationLogPath -PathType Leaf) {
            Get-Content -LiteralPath $elevationLogPath -Tail 120 |
                ForEach-Object { Write-Host ([string]$_) }
        }
        throw (
            "elevated automatic restore failed with exit code {0}; log={1}" -f
                [int]$elevated.ExitCode,
                $elevationLogPath
        )
    }
    Write-Host "Elevated restore log: $elevationLogPath"
    $wdBridgeFleetManifest = if ([string]$targetParameters['ManifestPath']) {
        [string]$targetParameters['ManifestPath']
    } else {
        Join-Path $wdBridgeBundleRoot 'wd-fleet.json'
    }
    Write-Host 'Starting four supervised bridge watchers and Tools from this non-elevated wrapper (Supervisor OFF); native Lead owns its session producer...'
    $wdBridgeResult = Invoke-WdBridgeWrapperBootstrap `
        -BundleRoot $wdBridgeBundleRoot `
        -FleetManifestPath $wdBridgeFleetManifest `
        -SupervisorScript (Join-Path (Split-Path -Parent $PSCommandPath) 'wd_supervisor.ps1') `
        -HostPath $elevationHost
    Write-Host ($wdBridgeResult | ConvertTo-Json -Depth 6 -Compress)
    if (-not [bool]$wdBridgeResult.ok) {
        throw ('Bridge Limited bootstrap refused at stage {0}: {1}' -f
            [string]$wdBridgeResult.stage, (@($wdBridgeResult.reasons) -join '; '))
    }
    Write-Host 'Fleet restore complete: four supervised watchers and Tools are process-current (native Lead producer and responsiveness not certified); WD-Supervisor untouched (Disabled).' -ForegroundColor Green
    return
}
if ($Auto) {
    # Supervisor OFF is standing: an elevated -Auto has no Limited context in which to start the
    # four supervised watchers and Tools, and it never starts WD-Supervisor for them.
    throw ('Supervisor OFF: an elevated -Auto cannot start the four supervised watchers and Tools. Run -Auto ' +
        'from a non-elevated PowerShell: it elevates once for the fleet restore, then starts them from ' +
        'that Limited wrapper. Nothing was changed.')
}
else {
    if ($Apply) { $targetParameters['Apply'] = $true }
    if ($DryRun) { $targetParameters['DryRun'] = $true }
    & $target @targetParameters
}
'@
    } elseif ($WrapperKind -ceq 'library') {
        '. $target'
    } elseif ($WrapperKind -ceq 'grok') {
@'
& $target @PSBoundParameters
# The bundle script exits only when IT is the process's -File target, so through this launcher
# a failed or deferred consultation used to exit 0. When this launcher is the -File target, it
# exits with the bundle's code the same way: 0 answered or a successful status, 1 failed,
# 2 deferred or blocked. Any other caller (&, a dot-source, -Command) is never exited; the
# code stays in $LASTEXITCODE.
$wdGrokExitCode = $global:LASTEXITCODE
$wdProcessArguments = [Environment]::GetCommandLineArgs()
$wdIsFileTarget = $false
for ($wdIndex = 1; $wdIndex -lt $wdProcessArguments.Count - 1; $wdIndex++) {
    if ($wdProcessArguments[$wdIndex] -match '^[-/](?i:f|fi|fil|file)$') {
        try {
            $wdIsFileTarget = [IO.Path]::GetFullPath($wdProcessArguments[$wdIndex + 1]) -ieq [IO.Path]::GetFullPath($PSCommandPath)
        } catch {
            $wdIsFileTarget = $false
        }
        break
    }
}
if ($wdIsFileTarget) { exit $wdGrokExitCode }
'@
    } elseif ($FixedAgent) {
        $escapedAgent = $FixedAgent.Replace("'", "''")
@"
`$targetParameters = @{}
foreach (`$key in `$PSBoundParameters.Keys) {
    `$targetParameters[`$key] = `$PSBoundParameters[`$key]
}
`$targetParameters['Agent'] = '$escapedAgent'
& `$target @targetParameters
"@
    } else {
        '& $target @PSBoundParameters'
    }
    $escapedTarget = $Target.Replace("'", "''")
    # A grok launcher reports 1 unless the bundle publishes a code: a check that refuses before
    # the bundle runs must not leave an in-process caller's earlier 0 in $LASTEXITCODE.
    $wrapperPreamble = if ($WrapperKind -ceq 'grok') {
@'
$global:LASTEXITCODE = 1

'@
    } else { '' }

    return @"
#requires -Version 5.1
# Generated by Deploy-WdRebootBundle.ps1 from a committed Git bundle.
[CmdletBinding()]
$parameterBlock
$wrapperPreamble`$ErrorActionPreference = 'Stop'
function Set-WdWrapperWindowsPowerShellModulePath {
    if (`$PSVersionTable.PSEdition -cne 'Desktop') { return }
    `$roots = [Collections.Generic.List[string]]::new()
    `$documents = [Environment]::GetFolderPath('MyDocuments')
    if (-not [string]::IsNullOrWhiteSpace(`$documents)) {
        [void]`$roots.Add([IO.Path]::Combine(`$documents, 'WindowsPowerShell', 'Modules'))
    }
    `$programFiles = [string]`$env:ProgramFiles
    if (-not [string]::IsNullOrWhiteSpace(`$programFiles)) {
        [void]`$roots.Add([IO.Path]::Combine(`$programFiles, 'WindowsPowerShell', 'Modules'))
    }
    [void]`$roots.Add([IO.Path]::Combine(`$PSHOME, 'Modules'))
    `$env:PSModulePath = @(`$roots) -join [IO.Path]::PathSeparator
}
Set-WdWrapperWindowsPowerShellModulePath
`$target = '$escapedTarget'
`$manifestPath = Join-Path (Split-Path -Parent `$target) 'deployment-manifest.json'
if (-not (Test-Path -LiteralPath `$target -PathType Leaf)) {
    throw "WD reboot bundle entry point is missing: `$target"
}
if (
    -not (Test-Path -LiteralPath `$manifestPath -PathType Leaf) -or
    (Get-FileHash -LiteralPath `$manifestPath -Algorithm SHA256).Hash -cne
        '$ExpectedManifestHash'
) {
    throw "WD reboot deployment manifest integrity mismatch for `$target"
}
`$actualHash = (Get-FileHash -LiteralPath `$target -Algorithm SHA256).Hash
if (`$actualHash -cne '$ExpectedHash') {
    throw "WD reboot bundle integrity mismatch for `$target"
}
if ('$WrapperKind' -cne 'library') { `$env:WD_REBOOT_EXPECTED_MANIFEST_HASH = '$ExpectedManifestHash' }
if ('$WrapperKind' -cne 'library') {
    # The pinned final pair must match the installer's state pointer next to this launcher, or it refuses
    # loudly; no older bundle is ever tried. The pointer is a recorded operator input, not authentication.
    `$statePath = Join-Path (Split-Path -Parent `$PSCommandPath) 'WD_REBOOT_STATE_CURRENT.json'
    `$state = `$null
    try { `$state = Get-Content -LiteralPath `$statePath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop } catch { `$state = `$null }
    if (
        `$null -eq `$state -or
        [string]`$state.source_commit -cne '$ExpectedFinalCommit' -or
        [string]`$state.final_commit -cne '$ExpectedFinalCommit' -or
        [string]`$state.manifest_sha256 -cne '$ExpectedManifestHash' -or
        [string]`$state.final_manifest_sha256 -cne '$ExpectedFinalManifestHash' -or
        -not ([string]`$state.active_bundle).Equals((Split-Path -Parent `$target), [StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "WD reboot state pointer `$statePath does not match this launcher's pinned final package (commit $ExpectedFinalCommit); refusing to start, and no older bundle is tried"
    }
}
$targetInvocation
"@
}

$sourceRoot = Resolve-FullPath $PSScriptRoot
$machineFull = Resolve-FullPath $MachineRoot
$storeFull = Resolve-FullPath $BundleStore
foreach ($persistentPath in @($sourceRoot, $machineFull, $storeFull)) {
    if ([System.IO.Path]::GetPathRoot($persistentPath).TrimEnd('\') -ne 'C:') {
        throw "reboot assets must stay on persistent C: storage: $persistentPath"
    }
}
$persistentDriveRoot = [IO.Path]::GetPathRoot($machineFull)
[void](Assert-WdPathWithoutReparse `
    -Path $sourceRoot `
    -TrustedRoot $persistentDriveRoot `
    -ExpectedType Directory)
[void](Assert-WdPathWithoutReparse `
    -Path $machineFull `
    -TrustedRoot $persistentDriveRoot `
    -ExpectedType Directory `
    -AllowMissing)
[void](Assert-WdPathWithoutReparse `
    -Path $storeFull `
    -TrustedRoot $persistentDriveRoot `
    -ExpectedType Directory `
    -AllowMissing)

# Every new deployment in this version uses Git member discovery. Legacy
# installed runtimes and extracted pure wrapper functions do not open this pin.
$GitContext = $null
try {
$GitContext = Open-WdPinnedGit -GitExecutable $GitExecutable -ExpectedGitSha256 $ExpectedGitSha256
$gitRootProbe = Invoke-GitCapture `
    -GitContext $GitContext `
    -Worktree $sourceRoot `
    -ArgumentList @('rev-parse', '--show-toplevel')
$gitRoot = [string]$gitRootProbe.Text
if ($gitRootProbe.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($gitRoot)) {
    throw "reboot bundle is not inside the source-of-truth Git repository: $sourceRoot"
}
$gitRoot = Resolve-FullPath ([string]$gitRoot)
$commonGitProbe = Invoke-GitCapture `
    -GitContext $GitContext `
    -Worktree $gitRoot `
    -ArgumentList @('rev-parse', '--path-format=absolute', '--git-common-dir')
$commonGit = [string]$commonGitProbe.Text
if ($commonGitProbe.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($commonGit)) {
    throw "could not resolve Git common directory for reboot source: $gitRoot"
}
$commonGit = Resolve-FullPath $commonGit
$expectedCommonGit = Resolve-FullPath 'C:\Python\project2\.git'
if (-not $commonGit.Equals(
        $expectedCommonGit,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
    throw "reboot source is not a worktree of C:\Python\project2: $gitRoot"
}

# The pinned bridge code package definition is data only here; its files
# join the dirty-tree gate and the exact-commit archive below.
$bridgeCodeDefinitionSourcePath = Join-Path $sourceRoot 'bridge-code-files.json'
if (-not (Test-Path -LiteralPath $bridgeCodeDefinitionSourcePath -PathType Leaf)) {
    throw "bridge code package definition is missing: $bridgeCodeDefinitionSourcePath"
}
$bridgeCodeSourceFiles = @(
    (Get-Content -LiteralPath $bridgeCodeDefinitionSourcePath -Raw |
        ConvertFrom-Json -ErrorAction Stop).python_files |
        ForEach-Object { [string]$_ }
)
if ($bridgeCodeSourceFiles.Count -lt 1) {
    throw 'bridge code package definition lists no python files'
}
$headProbe = Invoke-GitCapture -GitContext $GitContext -Worktree $gitRoot -ArgumentList @('rev-parse', 'HEAD')
$head = [string]$headProbe.Text
if ($headProbe.ExitCode -ne 0 -or $head -cnotmatch '^[0-9a-f]{40}$') {
    throw 'could not resolve the exact reboot-bundle commit'
}
$mindPackage = Get-WdMindPackageMembers -GitContext $GitContext -Worktree $gitRoot -Commit $head
$bridgeCodeSourceFiles = @($bridgeCodeSourceFiles + @($mindPackage.files | ForEach-Object { [string]$_.relative_path }) | Select-Object -Unique)
$statusProbe = Invoke-GitCapture `
    -GitContext $GitContext `
    -Worktree $gitRoot `
    -ArgumentList (@(
        'status',
        '--porcelain',
        '--',
        'ops/windows/reboot',
        '.agent-bridge/bin',
        'tools/CheckedPowerShellResult.ps1',
        'configs/bridge_identity_registry.json'
    ) + $bridgeCodeSourceFiles)
if ($statusProbe.ExitCode -ne 0) {
    throw 'git status failed for reboot sources'
}
$relativeSource = @($statusProbe.Output)
if (@($relativeSource).Count -gt 0) {
    throw 'refusing to deploy uncommitted reboot or Tools bootstrap sources'
}

$headProbe = Invoke-GitCapture -GitContext $GitContext -Worktree $gitRoot -ArgumentList @('rev-parse', 'HEAD')
if ($headProbe.ExitCode -ne 0 -or [string]$headProbe.Text -cne $head) {
    throw 'reboot-bundle HEAD changed after freezing its Mind members'
}
$branchProbe = Invoke-GitCapture `
    -GitContext $GitContext `
    -Worktree $gitRoot `
    -ArgumentList @('branch', '--show-current')
if ($branchProbe.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($branchProbe.Text)) {
    throw 'could not resolve the reboot-bundle branch'
}
$branch = [string]$branchProbe.Text
$upstreamProbe = Invoke-GitCapture `
    -GitContext $GitContext `
    -Worktree $gitRoot `
    -ArgumentList @('rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}')
$upstream = [string]$upstreamProbe.Text
if ($upstreamProbe.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($upstream)) {
    throw 'reboot-bundle branch has no upstream; push it before deployment'
}
$upstreamHeadProbe = Invoke-GitCapture `
    -GitContext $GitContext `
    -Worktree $gitRoot `
    -ArgumentList @('rev-parse', '@{u}')
$upstreamHead = [string]$upstreamHeadProbe.Text
if ($upstreamHeadProbe.ExitCode -ne 0 -or $upstreamHead -cne $head) {
    throw "reboot-bundle commit is not pushed to its upstream ($upstream)"
}

$manualApprovalText = $null
if ($ManualExecutionApprovalRecordPath -cne '') {
    $manualApprovalText = Read-WdManualExecutionApprovalText -Path $ManualExecutionApprovalRecordPath
}
$rcoEnrollmentText = $null
if ($RcoEnrollmentRecordPath -cne '') {
    # Reuse the bounded C-drive, no-reparse, opened-handle-bound reader.
    $rcoEnrollmentText = Read-WdManualExecutionApprovalText -Path $RcoEnrollmentRecordPath
}

$temporaryParent = Resolve-FullPath ([IO.Path]::GetTempPath())
$materializationRoot = Assert-ChildPath `
    -Parent $temporaryParent `
    -Child (Join-Path $temporaryParent (
        'wd-reboot-head-' + [guid]::NewGuid().ToString('N')
    ))
[void](New-Item -ItemType Directory -Path $materializationRoot -ErrorAction Stop)
try {
$archivePath = Join-Path $materializationRoot 'head.zip'
$archiveProbe = Invoke-GitCapture `
    -GitContext $GitContext `
    -Worktree $gitRoot `
    -ArgumentList (@(
        '-c', 'core.autocrlf=false',
        'archive',
        '--format=zip',
        "--output=$archivePath",
        $head,
        '--',
        'ops/windows/reboot',
        '.agent-bridge/bin',
        'tools/CheckedPowerShellResult.ps1',
        'configs/bridge_identity_registry.json'
    ) + $bridgeCodeSourceFiles)
if (
    $archiveProbe.ExitCode -ne 0 -or
    -not (Test-Path -LiteralPath $archivePath -PathType Leaf)
) {
    throw "could not materialize exact reboot inputs from commit $head"
}
$archiveRoot = Join-Path $materializationRoot 'tree'
Expand-Archive `
    -LiteralPath $archivePath `
    -DestinationPath $archiveRoot `
    -Force

$materializedRebootRoot = Join-Path $archiveRoot 'ops\windows\reboot'
$toolsBootstrapSource = Join-Path $archiveRoot '.agent-bridge\bin'
$identityRegistrySource = Join-Path (
    Join-Path $archiveRoot 'configs'
) 'bridge_identity_registry.json'
foreach ($materializedPath in @(
        $materializedRebootRoot,
        $toolsBootstrapSource
    )) {
    if (-not (Test-Path -LiteralPath $materializedPath -PathType Container)) {
        throw "commit archive is missing required directory: $materializedPath"
    }
}
if (-not (Test-Path -LiteralPath $identityRegistrySource -PathType Leaf)) {
    throw "commit archive is missing bridge identity registry: $identityRegistrySource"
}

Assert-WdMindPackageArchive -ArchiveRoot $archiveRoot -Package $mindPackage
$sourceHashes = Get-RelativeFileHashMap -Root $materializedRebootRoot
$sourcePaths = @{}
foreach ($name in $sourceHashes.Keys) {
    $sourcePaths[[string]$name] = Join-Path $materializedRebootRoot ([string]$name)
}
foreach ($file in @(
        Get-ChildItem -LiteralPath $toolsBootstrapSource -File |
            Sort-Object Name
    )) {
    $relativeName = 'tools-bootstrap/.agent-bridge/bin/{0}' -f $file.Name
    $sourceHashes[$relativeName] = (
        Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256
    ).Hash.ToUpperInvariant()
    $sourcePaths[$relativeName] = $file.FullName
}
$checkedResultSource = Join-Path $archiveRoot 'tools/CheckedPowerShellResult.ps1'
$checkedResultRelative = 'tools-bootstrap/.agent-bridge/bin/CheckedPowerShellResult.ps1'
$sourceHashes[$checkedResultRelative] = (Get-FileHash -LiteralPath $checkedResultSource -Algorithm SHA256).Hash.ToUpperInvariant()
$sourcePaths[$checkedResultRelative] = $checkedResultSource
$sourceHashes['CheckedPowerShellResult.ps1'] = $sourceHashes[$checkedResultRelative]
$sourcePaths['CheckedPowerShellResult.ps1'] = $checkedResultSource
$identityRegistryRelative = 'tools-bootstrap/configs/bridge_identity_registry.json'
$sourceHashes[$identityRegistryRelative] = (
    Get-FileHash -LiteralPath $identityRegistrySource -Algorithm SHA256
).Hash.ToUpperInvariant()
$sourcePaths[$identityRegistryRelative] = $identityRegistrySource

# Pinned bridge communication-code package: exact-commit Python files plus
# the hash-pinned wheel closure and its extracted site, all manifest-hashed.
. (Join-Path $materializedRebootRoot 'BridgeCodeContext.ps1')
$bridgeCodePackage = Get-WdBridgeCodePackageDefinition `
    -Path (Join-Path $materializedRebootRoot 'bridge-code-files.json')
$bridgeCodeDefinition = $bridgeCodePackage.Definition
foreach ($relativeCodeFile in @(@($bridgeCodeDefinition.python_files | ForEach-Object { [string]$_ }) + @($mindPackage.files | ForEach-Object { [string]$_.relative_path }) | Select-Object -Unique)) {
    $codeSource = Join-Path $archiveRoot ($relativeCodeFile.Replace('/', '\'))
    if (-not (Test-Path -LiteralPath $codeSource -PathType Leaf)) {
        throw "commit archive is missing pinned bridge code file: $relativeCodeFile"
    }
    $relativeName = 'tools-bootstrap/' + $relativeCodeFile
    $sourceHashes[$relativeName] = (
        Get-FileHash -LiteralPath $codeSource -Algorithm SHA256
    ).Hash.ToUpperInvariant()
    $sourcePaths[$relativeName] = $codeSource
}
$materializedFleet = Get-Content -LiteralPath (
    Join-Path $materializedRebootRoot 'wd-fleet.json'
) -Raw | ConvertFrom-Json -ErrorAction Stop
$rcoEnrollment = $null
if ($null -ne $rcoEnrollmentText) {
    . (Join-Path $toolsBootstrapSource 'BridgeLogReader.ps1')
    $rcoEnrollment = ConvertFrom-WdRcoEnrollmentRecord -Text $rcoEnrollmentText -SourceCommit $head
}
$mindRuntimeDescriptors = $null
if ($MindRuntimeDescriptorSetPath -cne '') {
    . (Join-Path $toolsBootstrapSource 'BridgeLogReader.ps1')
    $workerName = 'tools-bootstrap/tools/wd_mind_e5_worker.py'
    if (-not $sourceHashes.Contains($workerName)) { throw 'Mind runtime worker is absent from exact source package' }
    $mindRuntimeDescriptors = Read-WdMindRuntimeDescriptorSet -Path $MindRuntimeDescriptorSetPath `
        -SourceCommit $head -WorkerSha256 ([string]$sourceHashes[$workerName]).ToLowerInvariant()
}
$mindPolicyApproval = New-WdMindPolicyApprovalDescriptor -PolicyPath $MindIdentifierPolicyPath `
    -ApprovalReferenceSha256 $MindApprovalReferenceSha256
if ($null -ne $mindPolicyApproval -and ($null -eq $mindRuntimeDescriptors -or
    -not $mindRuntimeDescriptors.Files.Contains('wd-mind-runtime-location.json') -or
    -not $mindRuntimeDescriptors.Files.Contains('wd-mind-message-policy.json'))) {
    throw 'Mind policy approval requires the complete source-bound runtime descriptor set'
}
$manualFleetAgents = @()
$manualActivation = $null
if ($null -ne $manualApprovalText) {
    $manualFleetAgents = @($materializedFleet.lanes | ForEach-Object { [string]$_.agent }) + @([string]$materializedFleet.tools_supervisor.agent)
    . (Join-Path $toolsBootstrapSource 'BridgeLogReader.ps1')
    $manualActivation = ConvertFrom-WdManualExecutionApprovalRecord -Text $manualApprovalText `
        -SourceCommit $head -DefinitionHash $bridgeCodePackage.Hash -Definition $bridgeCodeDefinition -FleetAgentNames $manualFleetAgents
}
$bridgePythonProperty = $materializedFleet.PSObject.Properties['bridge_python']
if (
    $null -eq $bridgePythonProperty -or
    [string]::IsNullOrWhiteSpace([string]$bridgePythonProperty.Value.executable)
) {
    throw 'fleet manifest is missing bridge_python.executable'
}
$bridgePython = Resolve-WdBridgePythonExecutable `
    -ConfiguredPath ([string]$bridgePythonProperty.Value.executable)
$bridgeWheelStaging = Join-Path $materializationRoot ([string]$bridgeCodeDefinition.wheel_store_relative)
$bridgeSiteStaging = Join-Path $materializationRoot ([string]$bridgeCodeDefinition.python_site_relative)
Write-Host 'Staging the hash-pinned bridge Python dependency closure...'
$bridgePythonStage = Install-WdBridgePythonSite `
    -Definition $bridgeCodeDefinition `
    -PythonExecutable $bridgePython.Path `
    -WheelDirectory $bridgeWheelStaging `
    -SiteDirectory $bridgeSiteStaging `
    -WorkDirectory (Join-Path $materializationRoot 'python-work') `
    -WheelSource $WheelSource
Add-WdMindRuntimeDescriptorFiles -Set $mindRuntimeDescriptors -SourceHashes $sourceHashes -SourcePaths $sourcePaths `
    -StagingRoot (Join-Path $materializationRoot 'mind-runtime-descriptors')
Add-WdMindPolicyApprovalDescriptor -Approval $mindPolicyApproval -SourceHashes $sourceHashes -SourcePaths $sourcePaths `
    -StagingRoot (Join-Path $materializationRoot 'mind-runtime-descriptors')
foreach ($stagedName in @($bridgePythonStage.Wheels.Keys)) {
    $sourceHashes['tools-bootstrap/' + $stagedName] = [string]$bridgePythonStage.Wheels[$stagedName]
    $sourcePaths['tools-bootstrap/' + $stagedName] = Join-Path $materializationRoot (
        ([string]$stagedName).Replace('/', '\')
    )
}
foreach ($stagedName in @($bridgePythonStage.Site.Keys)) {
    $sourceHashes['tools-bootstrap/' + $stagedName] = [string]$bridgePythonStage.Site[$stagedName]
    $sourcePaths['tools-bootstrap/' + $stagedName] = Join-Path $materializationRoot (
        ([string]$stagedName).Replace('/', '\')
    )
}
foreach ($required in @(
        'start-wd-all.ps1',
        'start-wd-agent.ps1',
        'Get-WdMindReadConfig.ps1',
        'Invoke-WdMindStartupRead.ps1',
        'Invoke-WdLaneTurnLoop.ps1',
        'Invoke-WdCodexConversationLoop.ps1',
        'Show-WdOperatorConversation.ps1',
        'Get-WdSwarmParallelStatus.ps1',
        'Write-WdLaneCurrentState.ps1',
        'Watch-CodexPrompts.ps1',
        'start-wd-tools-consumer.ps1',
        'Get-WdNativeWakePrompt.ps1',
        'Resolve-WdNativeWakeRejection.ps1',
        'WAKE_PROCEDURE_LEAD.md',
        'WAKE_PROCEDURE_TOOLS.md',
        'Invoke-WdToolsCodex.ps1',
        'wd-fleet.json',
        'wd-claude-event-driven-settings.json',
        'wd_supervisor.ps1',
        'wd_supervisor_loop.json',
        'WdBridgeLimitedBootstrap.ps1',
        'Resolve-WdGrokModel.ps1',
        'Register-WdScheduledTasks.ps1',
        'Set-WdTaskConsoleContainment.ps1',
        'BOOT_AFTER_REBOOT.md',
        'BridgeCodeContext.ps1',
        'Invoke-WdBridgePython.ps1',
        'bridge-code-files.json',
        'WD_LOCAL_GPU_GUIDE.md',
        'WD_SWARM_PARALLEL_POLICY_V1.md',
        'WD_SWARM_TARGET_STATE_V1.md',
        'WaggleDanceSwarmAi.png',
        'tools-bootstrap/.agent-bridge/bin/AgentBridgeSessionIdentity.ps1',
            'tools-bootstrap/.agent-bridge/bin/ClaimLeaseHeartbeat.ps1',
            'tools-bootstrap/.agent-bridge/bin/BridgeNamedMutex.ps1',
            'tools-bootstrap/.agent-bridge/bin/BridgeV2QueueMutex.ps1',
        'tools-bootstrap/.agent-bridge/bin/BridgeIncrementalReader.ps1',
        'tools-bootstrap/.agent-bridge/bin/BridgeLogReader.ps1',
        'tools-bootstrap/.agent-bridge/bin/Drain-AcceptedBridgeQueue.ps1',
        'tools-bootstrap/.agent-bridge/bin/Restore-BridgeSpool.ps1',
        'tools-bootstrap/.agent-bridge/bin/Send-Liveness.ps1',
        'tools-bootstrap/.agent-bridge/bin/Start-AgentBridgeConsumerLoop.ps1',
        'tools-bootstrap/.agent-bridge/bin/Start-AgentBridgeSession.ps1',
        'tools-bootstrap/.agent-bridge/bin/Start-BridgeHeartbeat.ps1',
        'tools-bootstrap/.agent-bridge/bin/Test-BridgeWake.ps1',
        'tools-bootstrap/.agent-bridge/bin/Watch-Bridge.ps1',
        'tools-bootstrap/.agent-bridge/bin/Write-AgentEvent.ps1',
        'tools-bootstrap/configs/bridge_identity_registry.json'
    )) {
    if (-not $sourceHashes.Contains($required)) {
        throw "required reboot bundle file is missing: $required"
    }
}

$targetRoot = Assert-ChildPath -Parent $storeFull -Child (Join-Path $storeFull $head)
$manifestObject = [ordered]@{
    schema_version = 1
    source_commit = $head
    source_branch = $branch
    source_upstream = $upstream
    installed_at_utc = [DateTime]::UtcNow.ToString('o')
    bridge_code_package = [ordered]@{
        schema = [string]$bridgeCodeDefinition.schema
        definition_sha256 = $bridgeCodePackage.Hash
        python_executable = $bridgePython.Path
        python_executable_sha256 = $bridgePython.Sha256
        wheel_count = @($bridgePythonStage.Wheels.Keys).Count
        site_file_count = @($bridgePythonStage.Site.Keys).Count
        isolation_scope = 'per_tool_call_only'
        mind_modules = $mindPackage
        git_provenance = [ordered]@{
            schema = 'wd.native-git-input.v1'
            executable = $GitContext.Path
            expected_sha256 = $GitContext.ExpectedSha256
            pin_origin = 'caller_supplied_not_authenticated'
            policy = 'protected_native_path_sha256_read_handle_v1'
        }
    }
    files = $sourceHashes
}
if ($null -ne $manualActivation) {
    $manifestObject['manual_execution'] = $manualActivation
}
Add-WdRcoEnrollmentToManifest -Manifest $manifestObject -Enrollment $rcoEnrollment

Write-Host "WD reboot bundle source: $branch@$head"
Write-Host "Install target: $targetRoot"

Write-Host 'Running mutation-free deployment preflight...'
if ($StageOnly) {
    Write-Host '  stage-only: Grok resolution and task registration preflight skipped by the installer itself'
}
# Supervisor OFF is a standing install precondition, independent of -SkipTaskRegistration: the
# installer never disables WD-Supervisor itself, and Register-WdScheduledTasks -Apply only
# preserves whatever enabled state it finds. A missing task is admitted only together with
# -SkipTaskRegistration (nothing to re-point, nothing that can start); it is reported, never silent.
# Read-only. The actual install repeats it right before the first machine write and right before
# Register-WdScheduledTasks -Apply (-Recheck): each recheck narrows the window after the preflight, it is
# not an atomic lock against a task enabled from outside in between. Task names match case-insensitively,
# as the scheduler and Register-WdScheduledTasks -TaskName look them up.
function Assert-WdSupervisorTaskOff {
    param([switch] $Recheck)
    $supervisorTasks = @(Get-ScheduledTask | Where-Object { [string]$_.TaskName -eq 'WD-Supervisor' })
    if ($supervisorTasks.Count -gt 1) {
        throw 'refusing to deploy: more than one WD-Supervisor scheduled task exists'
    }
    if ($supervisorTasks.Count -eq 0) {
        if (-not $SkipTaskRegistration) {
            throw 'refusing to deploy: the WD-Supervisor scheduled task is missing; pass -SkipTaskRegistration to deploy without it'
        }
        if (-not $Recheck) {
            Write-Warning 'WD-Supervisor scheduled task is missing; -SkipTaskRegistration given, so no task is registered or changed.'
        }
        return
    }
    if ([bool]$supervisorTasks[0].Settings.Enabled -or [string]$supervisorTasks[0].State -ceq 'Running') {
        throw (
            'refusing to deploy: WD-Supervisor must be Disabled and not Running (enabled={0}, state={1}); ' +
            'this installer never disables it itself'
        ) -f [bool]$supervisorTasks[0].Settings.Enabled, [string]$supervisorTasks[0].State
    }
}
if (-not $StageOnly) {
    Assert-WdSupervisorTaskOff
    # The same read-only (no -Apply) Grok recovery readiness the actual install asserts before any write.
    $grokRecoveryReadiness = Join-Path $materializedRebootRoot 'Initialize-WdGrokRecovery.ps1'
    & $grokRecoveryReadiness | Out-Host
}
if ($DryRun -and -not $SkipGrokResolve) {
    Write-Host '  dry-run: the live Grok model probe is skipped; a dry run never contacts the provider'
}
if (-not $SkipGrokResolve -and -not $StageOnly -and -not $DryRun) {
    & (Join-Path $materializedRebootRoot 'Resolve-WdGrokModel.ps1') `
        -DryRun `
        -OutputDirectory $machineFull |
        Out-Host
}
if (-not $SkipTaskRegistration -and -not $StageOnly) {
    & (Join-Path $materializedRebootRoot 'Register-WdScheduledTasks.ps1') `
        -SupervisorScript (Join-Path $materializedRebootRoot 'wd_supervisor.ps1')
}

if ($DryRun) {
    Write-Host 'DRY RUN: no files, tasks, caches, or processes will be changed.'
    foreach ($name in $sourceHashes.Keys) {
        Write-Host ("  {0}  {1}" -f $sourceHashes[$name], $name)
    }
    return
}

Assert-WdMindRuntimeDescriptorInputs $mindRuntimeDescriptors
Assert-WdMindPolicyApprovalInput $mindPolicyApproval
if (-not (Test-Path -LiteralPath $storeFull -PathType Container)) {
    [void](New-Item -ItemType Directory -Path $storeFull -Force)
}
[void](Assert-WdPathWithoutReparse `
    -Path $storeFull `
    -TrustedRoot $persistentDriveRoot `
    -ExpectedType Directory)
if (Test-Path -LiteralPath $targetRoot) {
    [void](Assert-WdPathWithoutReparse `
        -Path $targetRoot `
        -TrustedRoot $storeFull `
        -ExpectedType Directory)
    $existingManifest = Join-Path $targetRoot 'deployment-manifest.json'
    if (-not (Test-Path -LiteralPath $existingManifest -PathType Leaf)) {
        throw "existing commit directory is incomplete: $targetRoot"
    }
} else {
    $stage = Assert-ChildPath -Parent $storeFull -Child (
        Join-Path $storeFull ('.stage-' + [guid]::NewGuid().ToString('N'))
    )
    [void](New-Item -ItemType Directory -Path $stage)
    [void](Assert-WdPathWithoutReparse `
        -Path $stage `
        -TrustedRoot $storeFull `
        -ExpectedType Directory)
    try {
        foreach ($name in $sourceHashes.Keys) {
            $destination = Join-Path $stage $name
            $destinationParent = Split-Path -Parent $destination
            if (-not (Test-Path -LiteralPath $destinationParent -PathType Container)) {
                [void](New-Item -ItemType Directory -Path $destinationParent -Force)
            }
            Copy-Item -LiteralPath $sourcePaths[[string]$name] -Destination $destination
            [void](Assert-WdPathWithoutReparse `
                -Path $destination `
                -TrustedRoot $stage `
                -ExpectedType Leaf)
            $copiedHash = (
                Get-FileHash -LiteralPath $destination -Algorithm SHA256
            ).Hash.ToUpperInvariant()
            if ($copiedHash -cne $sourceHashes[$name]) {
                throw "copied reboot bundle hash mismatch: $name"
            }
        }
        Write-Utf8NoBomAtomic `
            -Path (Join-Path $stage 'deployment-manifest.json') `
            -Content (($manifestObject | ConvertTo-Json -Depth 8) + [Environment]::NewLine)
        Move-Item -LiteralPath $stage -Destination $targetRoot
        [void](Assert-WdPathWithoutReparse `
            -Path $targetRoot `
            -TrustedRoot $storeFull `
            -ExpectedType Directory)
    } catch {
        if (Test-Path -LiteralPath $stage) {
            Remove-Item -LiteralPath $stage -Recurse -Force
        }
        throw
    }
}

$installedManifestPath = Join-Path $targetRoot 'deployment-manifest.json'
[void](Assert-WdPathWithoutReparse `
    -Path $installedManifestPath `
    -TrustedRoot $targetRoot `
    -ExpectedType Leaf)
$installedManifestSnapshot = Read-Utf8DeploymentSnapshot `
    -Path $installedManifestPath
if (($null -ne $manualActivation -or $null -ne $rcoEnrollment -or $null -ne $mindRuntimeDescriptors -or $null -ne $mindPolicyApproval) -and
    -not [WaggleDance.BridgeSnapshotDeltaV1.JsonContractValidator]::IsValid([string]$installedManifestSnapshot.Text)) {
    throw 'installed manual execution manifest violates the strict JSON contract'
}
$installedJsonArguments = @{ InputObject = [string]$installedManifestSnapshot.Text; ErrorAction = 'Stop' }
if ($null -ne $rcoEnrollment -and (Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) {
    $installedJsonArguments['DateKind'] = 'String'
}
$installedManifest = ConvertFrom-Json @installedJsonArguments
$installedManifestHash = ([string]$installedManifestSnapshot.Hash).ToUpperInvariant()
if (
    [int]$installedManifest.schema_version -ne 1 -or
    [string]$installedManifest.source_commit -cne $head
) {
    throw "installed reboot bundle manifest does not match commit $head"
}
Assert-WdManualExecutionManifestBinding -Deployment $installedManifest -ExpectedActivation $manualActivation `
    -SourceCommit $head -DefinitionHash $bridgeCodePackage.Hash -Definition $bridgeCodeDefinition -FleetAgentNames $manualFleetAgents
Assert-WdRcoEnrollmentManifestBinding -Deployment $installedManifest -ExpectedEnrollment $rcoEnrollment -SourceCommit $head
$installedHashProperties = @($installedManifest.files.PSObject.Properties)
if ($installedHashProperties.Count -ne $sourceHashes.Count) {
    throw "installed reboot bundle file set does not match commit $head"
}
foreach ($name in $sourceHashes.Keys) {
    $installedProperty = $installedManifest.files.PSObject.Properties[$name]
    if (
        $null -eq $installedProperty -or
        [string]$installedProperty.Value -cne [string]$sourceHashes[$name]
    ) {
        throw "installed reboot bundle manifest hash differs from source commit: $name"
    }
}
foreach ($property in $installedManifest.files.PSObject.Properties) {
    $installedFile = Join-Path $targetRoot $property.Name
    [void](Assert-WdPathWithoutReparse `
        -Path $installedFile `
        -TrustedRoot $targetRoot `
        -ExpectedType Leaf)
    $actual = (Get-FileHash -LiteralPath $installedFile -Algorithm SHA256).Hash
    if ($actual -cne [string]$property.Value) {
        throw "installed reboot bundle integrity mismatch: $($property.Name)"
    }
}
$expectedInstalledSet = @{ 'deployment-manifest.json' = $true }
foreach ($property in $installedManifest.files.PSObject.Properties) {
    $expectedInstalledSet[
        ([string]$property.Name).Replace('\', '/').ToLowerInvariant()
    ] = $true
}
$targetPrefix = (Resolve-FullPath $targetRoot).TrimEnd('\') + '\'
$actualInstalledFiles = @(
    Get-ChildItem -LiteralPath $targetRoot -Recurse -File -ErrorAction Stop
)
if ($actualInstalledFiles.Count -ne $expectedInstalledSet.Count) {
    throw "installed reboot bundle contains an unexpected recursive file set"
}
foreach ($file in $actualInstalledFiles) {
    $fullName = Resolve-FullPath $file.FullName
    if (-not $fullName.StartsWith(
            $targetPrefix,
            [StringComparison]::OrdinalIgnoreCase
        )) {
        throw "installed reboot bundle enumeration escaped target root: $fullName"
    }
    $relativeName = $fullName.Substring($targetPrefix.Length).Replace('\', '/')
    if (-not $expectedInstalledSet.ContainsKey($relativeName.ToLowerInvariant())) {
        throw "installed reboot bundle contains unexpected file: $relativeName"
    }
}

if ($StageOnly) {
    Write-Host ''
    Write-Host 'STAGE ONLY: commit-addressed bundle verified and left staged.' -ForegroundColor Green
    Write-Host ('  staged bundle:              {0}' -f $targetRoot)
    Write-Host ('  deployment manifest sha256: {0}' -f $installedManifestHash)
    Write-Host ('  final pair to pin (an operator input, not a signature): -ExpectedFinalCommit {0} -ExpectedFinalManifestHash {1}' -f $head, $installedManifestHash)
    Write-Host '  untouched: machine wrappers, data copies, reboot state pointers, integrity files, Grok resolution, scheduled tasks, live supervisor, watchers and sessions.'
    Write-Host '  activation is a separate cold switch: run this installer without -StageOnly only after the supervisor task is disabled and its invocation has exited.'
    return
}

# The actual install binds to the exact operator-pinned pair BEFORE the migration or any machine wrapper,
# data file or pointer changes. A mismatch leaves the verified bundle staged and changes nothing else. The
# pair is recorded operator input only: it is not a signature and is not cryptographic authentication.
if (
    $ExpectedFinalCommit -cnotmatch '^[0-9a-f]{40}$' -or
    $ExpectedFinalManifestHash -cnotmatch '^[0-9A-F]{64}$' -or
    $ExpectedFinalCommit -cne $head -or
    $ExpectedFinalManifestHash -cne $installedManifestHash
) {
    throw ((
        'refusing to activate: the operator-pinned final pair (commit {0}, manifest sha256 {1}) does not ' +
        'match this install (commit {2}, manifest sha256 {3}); nothing on the machine was changed'
    ) -f $ExpectedFinalCommit, $ExpectedFinalManifestHash, $head, $installedManifestHash)
}
# Migration is explicit and must finish before changing the fleet's pointers.
& (Join-Path $targetRoot 'Initialize-WdGrokRecovery.ps1') | Out-Host
$wrapperSpecs = @(
    [pscustomobject]@{ Name = 'CheckedPowerShellResult.ps1'; Target = 'CheckedPowerShellResult.ps1'; Kind = 'library'; Agent = '' },
    [pscustomobject]@{ Name = 'Invoke-WdGrok.ps1'; Target = 'Invoke-WdGrok.ps1'; Kind = 'grok'; Agent = '' },
    [pscustomobject]@{ Name = 'start-wd-all.ps1'; Target = 'start-wd-all.ps1'; Kind = 'fleet'; Agent = '' },
    [pscustomobject]@{ Name = 'start-wd-agent.ps1'; Target = 'start-wd-agent.ps1'; Kind = 'agent'; Agent = '' },
    [pscustomobject]@{ Name = 'start-wd-codex-lead.ps1'; Target = 'start-wd-agent.ps1'; Kind = 'agent'; Agent = 'codex-lead-1' },
    [pscustomobject]@{ Name = 'start-wd-codex-tools.ps1'; Target = 'start-wd-tools-consumer.ps1'; Kind = 'tools'; Agent = '' },
    [pscustomobject]@{ Name = 'start-wd-claude-rco.ps1'; Target = 'start-wd-agent.ps1'; Kind = 'agent'; Agent = 'claude-rco-1' },
    [pscustomobject]@{ Name = 'start-wd-claude-rco2.ps1'; Target = 'start-wd-agent.ps1'; Kind = 'agent'; Agent = 'claude-rco-2' },
    [pscustomobject]@{ Name = 'start-wd-fable-5.ps1'; Target = 'start-wd-agent.ps1'; Kind = 'agent'; Agent = 'fable-5' },
    [pscustomobject]@{ Name = 'wd_supervisor.ps1'; Target = 'wd_supervisor.ps1'; Kind = 'supervisor'; Agent = '' },
    [pscustomobject]@{ Name = 'start-wd-tools-consumer.ps1'; Target = 'start-wd-tools-consumer.ps1'; Kind = 'tools'; Agent = '' }
)
$dataSpecs = @(
    [pscustomobject]@{ Name = 'wd_supervisor_loop.json'; Target = 'wd_supervisor_loop.json' },
    # Replace the legacy opaque-command snapshot too, so no machine-local
    # reboot artifact retains a dead, versioned WindowsApps executable.
    [pscustomobject]@{ Name = 'wd_supervisor_loop_snapshot.json'; Target = 'wd_supervisor_loop.json' },
    [pscustomobject]@{ Name = 'BOOT_AFTER_REBOOT.md'; Target = 'BOOT_AFTER_REBOOT.md' },
    [pscustomobject]@{ Name = 'Set-WdTaskConsoleContainment.ps1'; Target = 'Set-WdTaskConsoleContainment.ps1' },
    [pscustomobject]@{ Name = 'WD_LOCAL_GPU_GUIDE.md'; Target = 'WD_LOCAL_GPU_GUIDE.md' },
    [pscustomobject]@{ Name = 'WD_SWARM_PARALLEL_POLICY_V1.md'; Target = 'WD_SWARM_PARALLEL_POLICY_V1.md' },
    [pscustomobject]@{ Name = 'Write-WdLaneCurrentState.ps1'; Target = 'Write-WdLaneCurrentState.ps1' },
    [pscustomobject]@{ Name = 'Get-WdSwarmParallelStatus.ps1'; Target = 'Get-WdSwarmParallelStatus.ps1' }
)

# Supervisor OFF again right before the first machine write: it may have been enabled since the preflight.
Assert-WdSupervisorTaskOff -Recheck
if (-not (Test-Path -LiteralPath $machineFull -PathType Container)) {
    [void](New-Item -ItemType Directory -Path $machineFull -Force)
}
[void](Assert-WdPathWithoutReparse `
    -Path $machineFull `
    -TrustedRoot $persistentDriveRoot `
    -ExpectedType Directory)

$backupRoot = Join-Path $machineFull (
    'wd-reboot-backups\{0}-{1}-{2}' -f
        [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ'),
        $PID,
        [guid]::NewGuid().ToString('N').Substring(0, 8)
)
$transactionNames = @(
    @($wrapperSpecs | ForEach-Object { [string]$_.Name })
    @($dataSpecs | ForEach-Object { [string]$_.Name })
    @(
        'WD_REBOOT_STATE_CURRENT.json',
        'WD_REBOOT_STATE_CURRENT.md',
        'WD_REBOOT_INTEGRITY_CURRENT.sha256',
        'WD_REBOOT_INTEGRITY_CURRENT.json',
        'WD_GROK_MODEL_CURRENT.json',
        'WD_GROK_MODEL_CURRENT.md'
    )
) | Select-Object -Unique
$backedUp = $false
$originallyPresent = @{}
foreach ($name in $transactionNames) {
    $machinePath = Assert-ChildPath -Parent $machineFull -Child (
        Join-Path $machineFull $name
    )
    $present = Test-Path -LiteralPath $machinePath -PathType Leaf
    $originallyPresent[$name] = $present
    if ($present) {
        if (-not $backedUp) {
            [void](New-Item -ItemType Directory -Path $backupRoot -Force)
            $backedUp = $true
        }
        Copy-Item -LiteralPath $machinePath -Destination (Join-Path $backupRoot $name)
    }
}

$machineMutationStarted = $false
try {
$machineMutationStarted = $true
foreach ($spec in $wrapperSpecs) {
    $machinePath = Join-Path $machineFull $spec.Name
    $target = Join-Path $targetRoot $spec.Target
    $hash = [string]$installedManifest.files.($spec.Target)
    $wrapper = New-ForwardingWrapper `
        -Target $target `
        -ExpectedHash $hash `
        -ExpectedManifestHash $installedManifestHash `
        -WrapperKind $spec.Kind `
        -FixedAgent $spec.Agent `
        -ExpectedFinalCommit $ExpectedFinalCommit `
        -ExpectedFinalManifestHash $ExpectedFinalManifestHash
    Write-Utf8NoBomAtomic -Path $machinePath -Content $wrapper
}

foreach ($spec in $dataSpecs) {
    $machinePath = Join-Path $machineFull $spec.Name
    $content = Get-Content -LiteralPath (Join-Path $targetRoot $spec.Target) -Raw
    Write-Utf8NoBomAtomic -Path $machinePath -Content $content
}

$state = [ordered]@{
    schema_version = 1
    source_commit = $head
    source_branch = $branch
    active_bundle = $targetRoot
    fleet_manifest = (Join-Path $targetRoot 'wd-fleet.json')
    manifest_sha256 = $installedManifestHash
    final_commit = $ExpectedFinalCommit
    final_manifest_sha256 = $ExpectedFinalManifestHash
    final_pair_note = 'operator-pinned pair recorded by the installer; not a signature or cryptographic authentication'
    installed_at_utc = [DateTime]::UtcNow.ToString('o')
    precedence = @(
        'live bridge state',
        'valid compact per-lane checkpoint',
        'WD_REBOOT_STATE_CURRENT',
        'newer per-agent and fleet Markdown handoffs as fallback',
        'dated historical reboot snapshots'
    )
}
Write-Utf8NoBomAtomic `
    -Path (Join-Path $machineFull 'WD_REBOOT_STATE_CURRENT.json') `
    -Content (($state | ConvertTo-Json -Depth 6) + [Environment]::NewLine)
$stateMarkdown = @"
# WaggleDance reboot state — current pointer

Generated from pushed Git commit ``$head`` on branch ``$branch``.

Active bundle: ``$targetRoot``

Precedence after restart:

1. Live bridge state read without acknowledging stale events.
2. A valid compact per-lane checkpoint.
3. This current bundle pointer, fleet roles, lane prompt, and parallel policy.
4. Newer fleet and per-agent Markdown handoffs as fallback.
5. Dated reboot snapshots as historical evidence only.

The reboot path grants no merge, deploy, signature, canary, runtime-authority,
or ``claim_safe`` permission.  The merge-driver StandingOneShot remains in
deliberate HOLD.
"@
Write-Utf8NoBomAtomic `
    -Path (Join-Path $machineFull 'WD_REBOOT_STATE_CURRENT.md') `
    -Content ($stateMarkdown + [Environment]::NewLine)

$integrityLines = New-Object System.Collections.Generic.List[string]
foreach ($spec in $wrapperSpecs) {
    $machinePath = Join-Path $machineFull $spec.Name
    $integrityLines.Add((
        '{0} *{1}' -f (
            Get-FileHash -LiteralPath $machinePath -Algorithm SHA256
        ).Hash.ToUpperInvariant(),
        $machinePath
    ))
}
foreach ($spec in $dataSpecs) {
    $machinePath = Join-Path $machineFull $spec.Name
    $integrityLines.Add((
        '{0} *{1}' -f (
            Get-FileHash -LiteralPath $machinePath -Algorithm SHA256
        ).Hash.ToUpperInvariant(),
        $machinePath
    ))
}
foreach ($property in $installedManifest.files.PSObject.Properties) {
    $integrityLines.Add((
        '{0} *{1}' -f [string]$property.Value, (Join-Path $targetRoot $property.Name)
    ))
}
Write-Utf8NoBomAtomic `
    -Path (Join-Path $machineFull 'WD_REBOOT_INTEGRITY_CURRENT.sha256') `
    -Content (($integrityLines -join [Environment]::NewLine) + [Environment]::NewLine)
Write-Utf8NoBomAtomic `
    -Path (Join-Path $machineFull 'WD_REBOOT_INTEGRITY_CURRENT.json') `
    -Content (Get-Content -LiteralPath $installedManifestPath -Raw)

if (-not $SkipGrokResolve) {
    & (Join-Path $targetRoot 'Resolve-WdGrokModel.ps1') -OutputDirectory $machineFull | Out-Host
}
if (-not $SkipTaskRegistration) {
    # And right before re-pointing the task; a refusal here rolls the machine files back.
    Assert-WdSupervisorTaskOff -Recheck
    & (Join-Path $targetRoot 'Register-WdScheduledTasks.ps1') `
        -Apply `
        -SupervisorScript (Join-Path $machineFull 'wd_supervisor.ps1')
}
}
catch {
    $deploymentFailure = $_
    $rollbackErrors = New-Object System.Collections.Generic.List[string]
    if ($machineMutationStarted) {
        foreach ($name in $transactionNames) {
            $machinePath = Join-Path $machineFull $name
            try {
                if ([bool]$originallyPresent[$name]) {
                    Copy-Item `
                        -LiteralPath (Join-Path $backupRoot $name) `
                        -Destination $machinePath `
                        -Force
                }
                elseif (Test-Path -LiteralPath $machinePath -PathType Leaf) {
                    Remove-Item -LiteralPath $machinePath -Force
                }
            }
            catch {
                $rollbackErrors.Add("${name}: $($_.Exception.Message)")
            }
        }
    }
    if ($rollbackErrors.Count -gt 0) {
        throw (
            "deployment failed: {0}; rollback also failed: {1}" -f
                $deploymentFailure.Exception.Message,
                ($rollbackErrors -join '; ')
        )
    }
    throw $deploymentFailure
}

Write-Host ''
Write-Host 'WD reboot bundle installed and verified.' -ForegroundColor Green
Write-Host 'One-line restore:'
Write-Host '  powershell -NoProfile -ExecutionPolicy Bypass -File C:\Python\start-wd-all.ps1 -Auto'
if ($backedUp) {
    Write-Host "Previous machine-local launchers were backed up to: $backupRoot"
}
}
finally {
    if (Test-Path -LiteralPath $materializationRoot -PathType Container) {
        $verifiedTemporary = Assert-ChildPath `
            -Parent $temporaryParent `
            -Child $materializationRoot
        Remove-Item `
            -LiteralPath $verifiedTemporary `
            -Recurse `
            -Force `
            -ErrorAction SilentlyContinue
    }
}

}
finally { Close-WdPinnedGit -GitContext $GitContext }
