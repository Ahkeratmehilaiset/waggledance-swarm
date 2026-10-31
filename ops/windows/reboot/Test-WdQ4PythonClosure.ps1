<#[
Inactive, narrow synthetic closure verifier. It does not launch Python, install,
authenticate the external pin, verify ACLs or OS-loaded DLLs, or prevent
same-user races between checking and use. The caller must protect the manifest,
this script, the PowerShell runtime and the exact closure between those steps.
This version supports a zipped stdlib only, not unpacked Lib/ layouts.
]#>
param(
    [Parameter(Mandatory=$true)][string]$ClosureRoot,
    [Parameter(Mandatory=$true)][string]$ManifestPath,
    [Parameter(Mandatory=$true)][string]$ManifestSha256
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Refuse { throw 'closure_refused' }
function Check-PathChain([string]$InputPath) {
    if ($InputPath -notmatch '^[A-Za-z]:\\' -or $InputPath -match '[*?]' -or $InputPath.Substring(2).Contains(':')) { Refuse }
    $full = [IO.Path]::GetFullPath($InputPath)
    if ($full -ne $InputPath.TrimEnd('\') -and $full.TrimEnd('\') -ne $InputPath.TrimEnd('\')) { Refuse }
    $drive = New-Object IO.DriveInfo ([IO.Path]::GetPathRoot($full))
    if ($drive.DriveType -ne [IO.DriveType]::Fixed) { Refuse }
    $node = $full
    while ($true) {
        $item = Get-Item -LiteralPath $node -Force -ErrorAction Stop
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { Refuse }
        $parent = [IO.Path]::GetDirectoryName($node)
        if ([string]::IsNullOrEmpty($parent) -or $parent -eq $node) { break }
        $node = $parent
    }
    return $full
}
function Check-Streams([string]$Path) {
    # PowerShell 5's provider does not enumerate directory streams. The Win32
    # API reports ERROR_HANDLE_EOF for directories with no streams; any other
    # failure is a refusal. Reject preloaded types rather than trusting them.
    if ((Get-Item -LiteralPath $Path -Force -ErrorAction Stop).PSIsContainer) {
        if ($null -eq $script:WdClosureStreamType) {
            if ($null -ne ('WdClosureDirectoryStreamsV1' -as [type])) { Refuse }
            $types = @(Add-Type -PassThru -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class WdClosureDirectoryStreamsV1 {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct StreamData {
        public long Size;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 296)] public string Name;
    }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr FindFirstStreamW(string name, int level, out StreamData data, uint flags);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool FindNextStreamW(IntPtr handle, out StreamData data);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool FindClose(IntPtr handle);
    public static bool NoNamedStreams(string path) {
        StreamData data;
        IntPtr handle = FindFirstStreamW(path, 0, out data, 0);
        if (handle == new IntPtr(-1)) return Marshal.GetLastWin32Error() == 38;
        try {
            while (true) {
                if (!String.Equals(data.Name, "::$DATA", StringComparison.Ordinal)) return false;
                if (!FindNextStreamW(handle, out data)) return Marshal.GetLastWin32Error() == 38;
            }
        } finally { FindClose(handle); }
    }
}
'@)
            # Add-Type may return the private nested StreamData type as well as
            # the entry type. Select the exact entry; keep the identity guard.
            $entryTypes = @($types | Where-Object { $_.FullName -ceq 'WdClosureDirectoryStreamsV1' })
            if ($entryTypes.Count -ne 1 -or
                -not [object]::ReferenceEquals($entryTypes[0], ('WdClosureDirectoryStreamsV1' -as [type]))) { Refuse }
            $script:WdClosureStreamType = $entryTypes[0]
        }
        if (-not $script:WdClosureStreamType::NoNamedStreams($Path)) { Refuse }
        return
    }
    foreach ($stream in @(Get-Item -LiteralPath $Path -Stream '*' -Force -ErrorAction Stop)) {
        if ($stream.Stream -cne ':$DATA') { Refuse }
    }
}
$script:WdClosureStreamType = $null
function RelPath([string]$Root, [string]$Path) {
    if (-not $Path.StartsWith($Root + '\', [StringComparison]::Ordinal)) { Refuse }
    return $Path.Substring($Root.Length + 1).Replace('\', '/')
}
function Hash-File([string]$Path, [long]$ExpectedSize) {
    $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        if ($stream.Length -ne $ExpectedSize) { Refuse }
        $hasher = [Security.Cryptography.SHA256]::Create()
        try { $digest = ([BitConverter]::ToString($hasher.ComputeHash($stream))).Replace('-', '').ToLowerInvariant() }
        finally { $hasher.Dispose() }
        if ($stream.Length -ne $ExpectedSize) { Refuse }
        return $digest
    } finally { $stream.Dispose() }
}

try {
    if ($ManifestSha256 -cnotmatch '^[0-9a-f]{64}$') { Refuse }
    $root = (Check-PathChain $ClosureRoot).TrimEnd('\')
    $manifest = Check-PathChain $ManifestPath
    if (-not (Get-Item -LiteralPath $root -Force).PSIsContainer -or (Get-Item -LiteralPath $manifest -Force).PSIsContainer) { Refuse }
    if ($manifest.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) { Refuse }
    Check-Streams $manifest
    $m = Get-Item -LiteralPath $manifest -Force
    if ($m.Length -gt 2097152) { Refuse }
    $bytes = [IO.File]::ReadAllBytes($manifest)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $digest = ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant() } finally { $sha.Dispose() }
    if ($digest -cne $ManifestSha256 -or $bytes.Length -ne $m.Length) { Refuse }
    if (@($bytes | Where-Object { $_ -gt 127 -or $_ -eq 13 }).Count -ne 0) { Refuse }
    $text = [Text.Encoding]::ASCII.GetString($bytes)
    if (-not $text.EndsWith("`n")) { Refuse }
    $lines = $text.Substring(0, $text.Length - 1).Split("`n")
    if ($lines.Count -lt 6 -or $lines.Count -gt 10002 -or $lines[0] -cne 'wd.q4-python-closure.v1' -or
        $lines[1] -cnotmatch '^version\t(3[0-9]{2})$') { Refuse }
    $version = $Matches[1]
    $expected = [Collections.Generic.Dictionary[string,object]]::new([StringComparer]::OrdinalIgnoreCase)
    $directories = [Collections.Generic.Dictionary[string,string]]::new([StringComparer]::OrdinalIgnoreCase)
    [long]$total = 0
    for ($i = 2; $i -lt $lines.Count; $i++) {
        $parts = $lines[$i].Split("`t")
        if ($parts.Count -ne 3 -or $parts[0] -cnotmatch '^[A-Za-z0-9_.+/-]+$' -or
            $parts[1] -cnotmatch '^(0|[1-9][0-9]{0,9})$' -or $parts[2] -cnotmatch '^[0-9a-f]{64}$') { Refuse }
        $name = $parts[0]
        $segments = $name.Split('/')
        foreach ($segment in $segments) {
            if (-not $segment -or $segment -in @('.', '..') -or $segment.EndsWith('.') -or
                $segment -match '^(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$') { Refuse }
        }
        if ($expected.ContainsKey($name) -or $directories.ContainsKey($name)) { Refuse }
        for ($j = 1; $j -lt $segments.Count; $j++) {
            $directory = ($segments[0..($j-1)] -join '/')
            if ($expected.ContainsKey($directory)) { Refuse }
            if ($directories.ContainsKey($directory) -and $directories[$directory] -cne $directory) { Refuse }
            $directories[$directory] = $directory
        }
        [long]$size = $parts[1]
        $total += $size
        if ($size -gt 1073741824 -or $total -gt 2147483648) { Refuse }
        $expected.Add($name, [pscustomobject]@{ Name=$name; Size=$size; Hash=$parts[2] })
    }
    foreach ($required in @('python.exe', "python$version.dll", "python$version.zip")) {
        if (-not $expected.ContainsKey($required) -or $expected[$required].Name -cne $required) { Refuse }
    }
    if (@($expected.Values | Where-Object { $_.Name -cmatch '^DLLs/[^/]+\.pyd$' }).Count -eq 0) { Refuse }
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $seenDirs = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $pending = [Collections.Generic.Stack[string]]::new()
    $pending.Push($root)
    while ($pending.Count -gt 0) {
        $dir = $pending.Pop()
        Check-Streams $dir
        foreach ($item in @(Get-ChildItem -LiteralPath $dir -Force -ErrorAction Stop)) {
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { Refuse }
            $relative = RelPath $root $item.FullName
            Check-Streams $item.FullName
            if ($item.PSIsContainer) {
                if (-not $directories.ContainsKey($relative) -or $directories[$relative] -cne $relative -or -not $seenDirs.Add($relative)) { Refuse }
                $pending.Push($item.FullName)
            } else {
                if (-not $expected.ContainsKey($relative) -or $expected[$relative].Name -cne $relative -or -not $seen.Add($relative)) { Refuse }
                $spec = $expected[$relative]
                if ($item.Length -ne $spec.Size -or (Hash-File $item.FullName $spec.Size) -cne $spec.Hash) { Refuse }
            }
        }
    }
    if ($seen.Count -ne $expected.Count -or $seenDirs.Count -ne $directories.Count) { Refuse }
    Write-Output 'closure_verified'
    exit 0
} catch {
    [Console]::Error.WriteLine('closure_refused')
    exit 1
}
