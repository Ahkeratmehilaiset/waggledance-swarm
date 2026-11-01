<#
Default-off interpreter-tree verification primitive. The caller must supply
independently trusted descriptor and executable pins and protect this script,
PowerShell, and the verified files until launch. This does not authenticate the
pins, verify PE/ABI or loaded DLLs, or prevent same-user check/use races.

To avoid PowerShell 5.1's ambiguous duplicate JSON keys, this implementation
accepts a strict JSON subset: ordinary ASCII names/paths, with descriptor and
record keys in the order documented below. Other valid descriptor encodings are
refused, not silently reinterpreted. No process or interpreter is launched.
#>
function Get-WdQ4BoundedHash {
    param([IO.Stream]$Stream, [long]$ExpectedSize)
    try {
        if ($null -eq $Stream -or $ExpectedSize -lt 0 -or $ExpectedSize -gt 536870912) { throw 'refused' }
        $sha = [Security.Cryptography.SHA256]::Create()
        try {
            $buffer = New-Object byte[] 1048576
            [long]$remaining = $ExpectedSize
            while ($remaining -gt 0) {
                $requested = [int][Math]::Min([long]$buffer.Length, $remaining)
                $count = $Stream.Read($buffer, 0, $requested)
                if ($count -le 0 -or $count -gt $requested) { throw 'refused' }
                [void]$sha.TransformBlock($buffer, 0, $count, $buffer, 0)
                $remaining -= $count
            }
            if ($Stream.ReadByte() -ne -1) { throw 'refused' }
            [void]$sha.TransformFinalBlock([byte[]]::new(0), 0, 0)
            return ([BitConverter]::ToString($sha.Hash)).Replace('-', '').ToLowerInvariant()
        } finally { $sha.Dispose() }
    } catch { throw 'interpreter_identity_refused' }
}

function Test-WdQ4InterpreterIdentity {
    [CmdletBinding()]
    param(
        [object]$Enabled = $false,
        [string]$Root,
        [byte[]]$DescriptorBytes,
        [string]$ExpectedSha256,
        [string]$ExpectedInterpreter,
        [string]$ExpectedInterpreterSha256
    )
    if ($Enabled -isnot [bool]) { throw 'interpreter_identity_refused' }
    if (-not $Enabled) { return [pscustomobject]@{ status = 'OFF' } }
    try {
        if ($null -eq $DescriptorBytes -or $DescriptorBytes.Length -gt 1048576 -or
            $ExpectedSha256 -cnotmatch '^[0-9a-f]{64}$' -or
            $ExpectedInterpreterSha256 -cnotmatch '^[0-9a-f]{64}$') { throw 'refused' }
        $hasher = [Security.Cryptography.SHA256]::Create()
        try { $descriptorHash = ([BitConverter]::ToString($hasher.ComputeHash($DescriptorBytes))).Replace('-', '').ToLowerInvariant() }
        finally { $hasher.Dispose() }
        if ($descriptorHash -cne $ExpectedSha256) { throw 'refused' }
        $utf8 = New-Object Text.UTF8Encoding($false, $true)
        $text = $utf8.GetString($DescriptorBytes)
        # A deliberately closed grammar makes duplicate or escaped property
        # names impossible. Validate before calling ConvertFrom-Json.
        $entry = '\{\s*"role"\s*:\s*"(?:interpreter|stdlib|dll)"\s*,\s*"path"\s*:\s*"[A-Za-z0-9_./+\-]+"\s*,\s*"size"\s*:\s*(?:0|[1-9][0-9]*)\s*,\s*"sha256"\s*:\s*"[0-9a-f]{64}"\s*\}'
        $pattern = '^\s*\{\s*"schema"\s*:\s*"wd\.q4-interpreter-identity\.v1"\s*,\s*"files"\s*:\s*\[\s*' + $entry + '(?:\s*,\s*' + $entry + ')*\s*\]\s*\}\s*$'
        if (-not [regex]::IsMatch($text, $pattern, [Text.RegularExpressions.RegexOptions]::CultureInvariant, [TimeSpan]::FromSeconds(2))) { throw 'refused' }
        $doc = ConvertFrom-Json -InputObject $text -ErrorAction Stop
        $records = @($doc.files)
        if ($records.Count -lt 1 -or $records.Count -gt 1024) { throw 'refused' }
        foreach ($absolute in @($Root, $ExpectedInterpreter)) {
            if ($absolute -cnotmatch '^[A-Za-z]:\\' -or $absolute.Substring(2).Contains(':') -or
                $absolute.Contains('/') -or [IO.Path]::GetFullPath($absolute) -cne $absolute.TrimEnd('\')) { throw 'refused' }
            if ($absolute.Length -gt 32767 -or $absolute -match '[\x00-\x1f]') { throw 'refused' }
            foreach ($part in $absolute.Substring(3).Split('\')) {
                if (-not $part -or $part.Length -gt 255 -or $part -in @('.', '..') -or
                    $part.EndsWith('.') -or $part.EndsWith(' ') -or $part -match '[<>"|?*]' -or
                    $part -match '^(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$') { throw 'refused' }
            }
        }
        $rootPath = $Root.TrimEnd('\')
        $expectedFiles = [Collections.Generic.Dictionary[string,object]]::new([StringComparer]::OrdinalIgnoreCase)
        $expectedDirs = [Collections.Generic.Dictionary[string,string]]::new([StringComparer]::OrdinalIgnoreCase)
        [long]$total = 0
        $interpreter = $null; $roleSet = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        foreach ($record in $records) {
            $name = [string]$record.path
            $segments = $name.Split('/')
            if ($segments.Count -gt 63 -or $expectedFiles.ContainsKey($name) -or $expectedDirs.ContainsKey($name)) { throw 'refused' }
            foreach ($segment in $segments) {
                if (-not $segment -or $segment.Length -gt 255 -or $segment -in @('.', '..') -or $segment.EndsWith('.') -or
                    $segment -match '^(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$') { throw 'refused' }
            }
            for ($i = 1; $i -lt $segments.Count; $i++) {
                $dir = $segments[0..($i - 1)] -join '/'
                if ($expectedFiles.ContainsKey($dir) -or ($expectedDirs.ContainsKey($dir) -and $expectedDirs[$dir] -cne $dir)) { throw 'refused' }
                $expectedDirs[$dir] = $dir
            }
            [long]$size = $record.size
            if ($size -lt 0 -or $size -gt 536870912) { throw 'refused' }
            $total += $size
            if ($total -gt 4294967296) { throw 'refused' }
            $expectedFiles.Add($name, [pscustomobject]@{ Name=$name; Size=$size; Hash=[string]$record.sha256 })
            [void]$roleSet.Add([string]$record.role)
            if ($record.role -ceq 'interpreter') {
                if ($null -ne $interpreter) { throw 'refused' }
                $interpreter = $name
            }
        }
        if ($null -eq $interpreter -or -not $roleSet.Contains('stdlib') -or -not $roleSet.Contains('dll')) { throw 'refused' }
        $selected = Join-Path $rootPath ($interpreter.Replace('/', '\'))
        if ($selected -ine $ExpectedInterpreter -or $expectedFiles[$interpreter].Hash -cne $ExpectedInterpreterSha256) { throw 'refused' }

        if ($null -eq $script:WdQ4IdentityNativeType) {
            if ($null -ne ('WdQ4IdentityNativeV1' -as [type])) { throw 'refused' }
            $types = @(Add-Type -PassThru -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class WdQ4IdentityNativeV1 {
  [StructLayout(LayoutKind.Sequential)] public struct Info {
    public uint Attributes, CreationLow, CreationHigh, AccessLow, AccessHigh,
      WriteLow, WriteHigh, Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
  }
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] public struct StreamData {
    public long Size; [MarshalAs(UnmanagedType.ByValTStr, SizeConst=296)] public string Name;
  }
  [DllImport("kernel32.dll", SetLastError=true)] public static extern bool GetFileInformationByHandle(SafeFileHandle h, out Info i);
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern IntPtr FindFirstStreamW(string p, int level, out StreamData d, uint flags);
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool FindNextStreamW(IntPtr h, out StreamData d);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool FindClose(IntPtr h);
  public static bool NoNamedStreams(string path) {
    StreamData d; IntPtr h=FindFirstStreamW(path,0,out d,0);
    if (h == new IntPtr(-1)) return Marshal.GetLastWin32Error()==38;
    try { do { if (!String.Equals(d.Name,"::$DATA",StringComparison.Ordinal)) return false; }
      while (FindNextStreamW(h,out d)); return Marshal.GetLastWin32Error()==38; }
    finally { FindClose(h); }
  }
}
'@)
            $found = @($types | Where-Object { $_.FullName -ceq 'WdQ4IdentityNativeV1' })
            if ($found.Count -ne 1 -or -not [object]::ReferenceEquals($found[0], ('WdQ4IdentityNativeV1' -as [type]))) { throw 'refused' }
            $script:WdQ4IdentityNativeType = $found[0]
        }
        # Check every ancestor before walking; UNC and reparse paths refused.
        $node = $rootPath
        while ($true) {
            $item = Get-Item -LiteralPath $node -Force -ErrorAction Stop
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'refused' }
            $parent = [IO.Path]::GetDirectoryName($node)
            if ([string]::IsNullOrEmpty($parent) -or $parent -eq $node) { break }
            $node = $parent
        }
        if (-not (Get-Item -LiteralPath $rootPath -Force).PSIsContainer) { throw 'refused' }
        # Re-enumerate the complete tree after the first verification pass.
        for ($pass = 0; $pass -lt 2; $pass++) {
          $seenFiles = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
          $seenDirs = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
          $pending = [Collections.Generic.Stack[string]]::new(); $pending.Push($rootPath)
          while ($pending.Count -gt 0) {
            $directory = $pending.Pop()
            if (-not $script:WdQ4IdentityNativeType::NoNamedStreams($directory)) { throw 'refused' }
            foreach ($path in [IO.Directory]::EnumerateFileSystemEntries($directory)) {
                $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
                if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
                    -not $script:WdQ4IdentityNativeType::NoNamedStreams($path)) { throw 'refused' }
                $relative = $path.Substring($rootPath.Length + 1).Replace('\', '/')
                if ($item.PSIsContainer) {
                    if (-not $expectedDirs.ContainsKey($relative) -or $expectedDirs[$relative] -cne $relative -or -not $seenDirs.Add($relative)) { throw 'refused' }
                    $pending.Push($path)
                } else {
                    if (-not $expectedFiles.ContainsKey($relative) -or $expectedFiles[$relative].Name -cne $relative -or -not $seenFiles.Add($relative)) { throw 'refused' }
                    $spec = $expectedFiles[$relative]
                    $stream = [IO.File]::Open($path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
                    try {
                        $info = New-Object WdQ4IdentityNativeV1+Info
                        if (-not $script:WdQ4IdentityNativeType::GetFileInformationByHandle($stream.SafeFileHandle, [ref]$info) -or
                            $info.Links -ne 1 -or ($info.Attributes -band 1024) -ne 0 -or $stream.Length -ne $spec.Size) { throw 'refused' }
                        $actual = Get-WdQ4BoundedHash -Stream $stream -ExpectedSize $spec.Size
                        if ($stream.Length -ne $spec.Size -or $actual -cne $spec.Hash) { throw 'refused' }
                        $after = New-Object WdQ4IdentityNativeV1+Info
                        if (-not $script:WdQ4IdentityNativeType::GetFileInformationByHandle($stream.SafeFileHandle, [ref]$after)) { throw 'refused' }
                        $pathStream = [IO.File]::Open($path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
                        try {
                            $pathInfo = New-Object WdQ4IdentityNativeV1+Info
                            if (-not $script:WdQ4IdentityNativeType::GetFileInformationByHandle($pathStream.SafeFileHandle, [ref]$pathInfo)) { throw 'refused' }
                            foreach ($field in @('Attributes','Volume','SizeHigh','SizeLow','Links','IndexHigh','IndexLow','WriteLow','WriteHigh')) {
                                if ($info.$field -ne $after.$field -or $info.$field -ne $pathInfo.$field) { throw 'refused' }
                            }
                        } finally { $pathStream.Dispose() }
                    } finally { $stream.Dispose() }
                }
            }
        }
          if ($seenFiles.Count -ne $expectedFiles.Count -or $seenDirs.Count -ne $expectedDirs.Count) { throw 'refused' }
        }
        return [pscustomobject]@{ status='VERIFIED'; descriptor_sha256=$descriptorHash; file_count=$seenFiles.Count; total_bytes=$total }
    } catch { throw 'interpreter_identity_refused' }
}
