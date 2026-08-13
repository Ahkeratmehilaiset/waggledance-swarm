#requires -Version 5.1

Set-StrictMode -Version Latest

if (-not ('WaggleDance.BridgeFileIdentityV1.NativeMethods' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace WaggleDance.BridgeFileIdentityV1
{
    [StructLayout(LayoutKind.Sequential)]
    public struct FileTime
    {
        public uint Low;
        public uint High;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct ByHandleFileInformation
    {
        public uint Attributes;
        public FileTime CreationTime;
        public FileTime LastAccessTime;
        public FileTime LastWriteTime;
        public uint VolumeSerialNumber;
        public uint FileSizeHigh;
        public uint FileSizeLow;
        public uint NumberOfLinks;
        public uint FileIndexHigh;
        public uint FileIndexLow;
    }

    public static class NativeMethods
    {
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GetFileInformationByHandle(
            SafeFileHandle file,
            out ByHandleFileInformation information);

        [DllImport(
            "libc",
            EntryPoint = "fstat",
            SetLastError = true,
            CallingConvention = CallingConvention.Cdecl)]
        private static extern int FStat(int fileDescriptor, IntPtr buffer);

        public static string Identity(SafeFileHandle file)
        {
            if (file == null || file.IsInvalid || file.IsClosed)
            {
                throw new IOException("bridge file handle is not open");
            }
            if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
            {
                return WindowsIdentity(file);
            }
            if (RuntimeInformation.IsOSPlatform(OSPlatform.Linux) &&
                RuntimeInformation.ProcessArchitecture == Architecture.X64)
            {
                return LinuxIdentity(file);
            }
            throw new PlatformNotSupportedException(
                "bridge file identity is supported only on Windows and Linux x64");
        }

        private static string WindowsIdentity(SafeFileHandle file)
        {
            ByHandleFileInformation information;
            if (!GetFileInformationByHandle(file, out information))
            {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            return string.Format(
                CultureInfo.InvariantCulture,
                "windows-v1:{0:x8}:{1:x8}{2:x8}",
                information.VolumeSerialNumber,
                information.FileIndexHigh,
                information.FileIndexLow);
        }

        private static string LinuxIdentity(SafeFileHandle file)
        {
            bool addedReference = false;
            IntPtr buffer = IntPtr.Zero;
            try
            {
                file.DangerousAddRef(ref addedReference);
                int fileDescriptor = file.DangerousGetHandle().ToInt32();
                // The supported Linux x64 stat ABI starts with 64-bit st_dev
                // and st_ino fields. A conservative buffer avoids coupling the
                // managed declaration to the remaining native structure.
                buffer = Marshal.AllocHGlobal(256);
                if (FStat(fileDescriptor, buffer) != 0)
                {
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                }
                ulong device = unchecked((ulong)Marshal.ReadInt64(buffer, 0));
                ulong inode = unchecked((ulong)Marshal.ReadInt64(buffer, 8));
                if (inode == 0)
                {
                    throw new IOException("fstat returned an empty inode");
                }
                return string.Format(
                    CultureInfo.InvariantCulture,
                    "posix-v1:{0:x}:{1:x}",
                    device,
                    inode);
            }
            finally
            {
                if (buffer != IntPtr.Zero)
                {
                    Marshal.FreeHGlobal(buffer);
                }
                if (addedReference)
                {
                    file.DangerousRelease();
                }
            }
        }
    }
}
'@
}

if (-not ('WaggleDance.BridgeSnapshotDeltaV1.JsonContractValidator' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace WaggleDance.BridgeSnapshotDeltaV1
{
    public static class JsonContractValidator
    {
        public const int MaxDepth = 32;
        public const long MaxSafeInteger = 9007199254740991L;

        public static bool IsValid(string text)
        {
            try
            {
                new Parser(text).ParseDocument();
                return true;
            }
            catch (FormatException)
            {
                return false;
            }
        }

        private sealed class Parser
        {
            private readonly string _text;
            private int _index;

            public Parser(string text)
            {
                if (text == null)
                {
                    throw new ArgumentNullException("text");
                }
                _text = text;
            }

            public void ParseDocument()
            {
                SkipWhitespace();
                ParseValue(0);
                SkipWhitespace();
                if (_index != _text.Length)
                {
                    Fail();
                }
            }

            private void ParseValue(int containerDepth)
            {
                if (_index >= _text.Length)
                {
                    Fail();
                }
                char current = _text[_index];
                if (current == '{')
                {
                    ParseObject(containerDepth + 1);
                }
                else if (current == '[')
                {
                    ParseArray(containerDepth + 1);
                }
                else if (current == '"')
                {
                    ParseString();
                }
                else if (current == 't')
                {
                    ParseLiteral("true");
                }
                else if (current == 'f')
                {
                    ParseLiteral("false");
                }
                else if (current == 'n')
                {
                    ParseLiteral("null");
                }
                else if (current == '-' || (current >= '0' && current <= '9'))
                {
                    ParseNumber();
                }
                else
                {
                    Fail();
                }
            }

            private void ParseObject(int depth)
            {
                CheckDepth(depth);
                _index++;
                SkipWhitespace();
                HashSet<string> keys = new HashSet<string>(StringComparer.Ordinal);
                if (Consume('}'))
                {
                    return;
                }
                while (true)
                {
                    if (_index >= _text.Length || _text[_index] != '"')
                    {
                        Fail();
                    }
                    string key = ParseString();
                    for (int keyIndex = 0; keyIndex < key.Length; keyIndex++)
                    {
                        if (key[keyIndex] > 0x7F)
                        {
                            Fail();
                        }
                    }
                    if (!keys.Add(FoldAscii(key)))
                    {
                        Fail();
                    }
                    SkipWhitespace();
                    Expect(':');
                    SkipWhitespace();
                    ParseValue(depth);
                    SkipWhitespace();
                    if (Consume('}'))
                    {
                        return;
                    }
                    Expect(',');
                    SkipWhitespace();
                }
            }

            private void ParseArray(int depth)
            {
                CheckDepth(depth);
                _index++;
                SkipWhitespace();
                if (Consume(']'))
                {
                    return;
                }
                while (true)
                {
                    ParseValue(depth);
                    SkipWhitespace();
                    if (Consume(']'))
                    {
                        return;
                    }
                    Expect(',');
                    SkipWhitespace();
                }
            }

            private string ParseString()
            {
                Expect('"');
                StringBuilder value = new StringBuilder();
                while (_index < _text.Length)
                {
                    char current = _text[_index++];
                    if (current == '"')
                    {
                        return value.ToString();
                    }
                    if (current < 0x20)
                    {
                        Fail();
                    }
                    if (current == '\\')
                    {
                        if (_index >= _text.Length)
                        {
                            Fail();
                        }
                        char escape = _text[_index++];
                        switch (escape)
                        {
                            case '"': value.Append('"'); break;
                            case '\\': value.Append('\\'); break;
                            case '/': value.Append('/'); break;
                            case 'b': value.Append('\b'); break;
                            case 'f': value.Append('\f'); break;
                            case 'n': value.Append('\n'); break;
                            case 'r': value.Append('\r'); break;
                            case 't': value.Append('\t'); break;
                            case 'u':
                                int codeUnit = ParseHexCodeUnit();
                                if (codeUnit >= 0xD800 && codeUnit <= 0xDFFF)
                                {
                                    Fail();
                                }
                                value.Append((char)codeUnit);
                                break;
                            default:
                                Fail();
                                break;
                        }
                    }
                    else if (char.IsHighSurrogate(current))
                    {
                        if (_index >= _text.Length || !char.IsLowSurrogate(_text[_index]))
                        {
                            Fail();
                        }
                        value.Append(current);
                        value.Append(_text[_index++]);
                    }
                    else if (char.IsLowSurrogate(current))
                    {
                        Fail();
                    }
                    else
                    {
                        value.Append(current);
                    }
                }
                Fail();
                return null;
            }

            private int ParseHexCodeUnit()
            {
                if (_index + 4 > _text.Length)
                {
                    Fail();
                }
                int value = 0;
                for (int count = 0; count < 4; count++)
                {
                    char digit = _text[_index++];
                    int part;
                    if (digit >= '0' && digit <= '9') part = digit - '0';
                    else if (digit >= 'a' && digit <= 'f') part = digit - 'a' + 10;
                    else if (digit >= 'A' && digit <= 'F') part = digit - 'A' + 10;
                    else
                    {
                        Fail();
                        part = 0;
                    }
                    value = (value << 4) | part;
                }
                return value;
            }

            private void ParseNumber()
            {
                int start = _index;
                bool integerToken = true;
                Consume('-');
                if (Consume('0'))
                {
                    if (_index < _text.Length && IsDigit(_text[_index]))
                    {
                        Fail();
                    }
                }
                else
                {
                    if (_index >= _text.Length || _text[_index] < '1' || _text[_index] > '9')
                    {
                        Fail();
                    }
                    while (_index < _text.Length && IsDigit(_text[_index])) _index++;
                }
                if (Consume('.'))
                {
                    integerToken = false;
                    if (_index >= _text.Length || !IsDigit(_text[_index])) Fail();
                    while (_index < _text.Length && IsDigit(_text[_index])) _index++;
                }
                if (_index < _text.Length && (_text[_index] == 'e' || _text[_index] == 'E'))
                {
                    integerToken = false;
                    _index++;
                    if (_index < _text.Length && (_text[_index] == '+' || _text[_index] == '-')) _index++;
                    if (_index >= _text.Length || !IsDigit(_text[_index])) Fail();
                    while (_index < _text.Length && IsDigit(_text[_index])) _index++;
                }
                string token = _text.Substring(start, _index - start);
                if (integerToken)
                {
                    long parsedInteger;
                    if (!long.TryParse(
                            token,
                            NumberStyles.AllowLeadingSign,
                            CultureInfo.InvariantCulture,
                            out parsedInteger) ||
                        parsedInteger < -MaxSafeInteger || parsedInteger > MaxSafeInteger)
                    {
                        Fail();
                    }
                }
                else
                {
                    double parsed;
                    if (!double.TryParse(
                            token,
                            NumberStyles.Float,
                            CultureInfo.InvariantCulture,
                            out parsed) ||
                        double.IsNaN(parsed) || double.IsInfinity(parsed))
                    {
                        Fail();
                    }
                }
            }

            private void ParseLiteral(string literal)
            {
                if (_index + literal.Length > _text.Length ||
                    string.CompareOrdinal(_text, _index, literal, 0, literal.Length) != 0)
                {
                    Fail();
                }
                _index += literal.Length;
            }

            private static string FoldAscii(string key)
            {
                char[] chars = key.ToCharArray();
                for (int index = 0; index < chars.Length; index++)
                {
                    if (chars[index] >= 'A' && chars[index] <= 'Z')
                    {
                        chars[index] = (char)(chars[index] + ('a' - 'A'));
                    }
                }
                return new string(chars);
            }

            private void SkipWhitespace()
            {
                while (_index < _text.Length)
                {
                    char current = _text[_index];
                    if (current != ' ' && current != '\t' && current != '\r' && current != '\n') break;
                    _index++;
                }
            }

            private bool Consume(char expected)
            {
                if (_index < _text.Length && _text[_index] == expected)
                {
                    _index++;
                    return true;
                }
                return false;
            }

            private void Expect(char expected)
            {
                if (!Consume(expected)) Fail();
            }

            private static bool IsDigit(char value)
            {
                return value >= '0' && value <= '9';
            }

            private static void CheckDepth(int depth)
            {
                if (depth > MaxDepth) Fail();
            }

            private static void Fail()
            {
                throw new FormatException("JSON violates bridge reader contract");
            }
        }
    }
}
'@
}

$script:BridgeGenerationPattern = '^[A-Za-z0-9._:-]{1,128}$'

function Open-BridgeLogReadStream {
    param([Parameter(Mandatory)] [string] $Path)

    return [System.IO.File]::Open(
        $Path,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        ([System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete)
    )
}

function Get-BridgeLogFileIdentity {
    param([Parameter(Mandatory)] [System.IO.FileStream] $Stream)

    try {
        return [WaggleDance.BridgeFileIdentityV1.NativeMethods]::Identity(
            $Stream.SafeFileHandle
        )
    } catch {
        throw "bridge file identity unavailable: $($_.Exception.Message)"
    }
}

function New-BridgeLogCandidateCursor {
    param(
        [Parameter(Mandatory)] [int64] $Offset,
        [Parameter(Mandatory)] [string] $FileIdentity,
        [AllowNull()] $Generation
    )

    return [pscustomobject]@{
        offset = $Offset
        file_identity = $FileIdentity
        generation = $Generation
    }
}

function New-BridgeLogReadResult {
    param(
        [Parameter(Mandatory)] [ValidateSet('OK','IDLE','RETRY','BLOCKED')]
        [string] $Status,
        [Parameter(Mandatory)] [string] $Reason,
        [Parameter(Mandatory)] [int64] $RequestedOffset,
        [AllowEmptyCollection()] [object[]] $Rows = @(),
        [AllowNull()] $CandidateCursor = $null,
        [int64] $BytesRead = 0,
        [int64] $BytesConsumed = 0,
        [AllowNull()] $SnapshotLength = $null,
        [int] $ReadCalls = 0
    )

    return [pscustomobject]@{
        status = $Status
        reason = $Reason
        rows = @($Rows)
        candidate_cursor = $CandidateCursor
        bytes_read = $BytesRead
        bytes_consumed = $BytesConsumed
        snapshot_length = $SnapshotLength
        read_calls = $ReadCalls
        requested_offset = $RequestedOffset
    }
}

function Test-BridgeJsonObject {
    param([AllowNull()] $Value)
    return (
        $null -ne $Value -and
        (
            $Value -is [System.Management.Automation.PSCustomObject] -or
            $Value -is [System.Collections.IDictionary]
        )
    )
}

function Test-BridgeJsonFiniteNumbers {
    param([AllowNull()] $Value)

    if ($null -eq $Value) { return $true }
    if ($Value -is [double]) {
        return (-not [double]::IsNaN($Value) -and -not [double]::IsInfinity($Value))
    }
    if ($Value -is [single]) {
        return (-not [single]::IsNaN($Value) -and -not [single]::IsInfinity($Value))
    }
    if ($Value -is [System.Collections.IDictionary]) {
        foreach ($item in $Value.Values) {
            if (-not (Test-BridgeJsonFiniteNumbers -Value $item)) { return $false }
        }
        return $true
    }
    if ($Value -is [System.Management.Automation.PSCustomObject]) {
        foreach ($property in $Value.PSObject.Properties) {
            if (-not (Test-BridgeJsonFiniteNumbers -Value $property.Value)) { return $false }
        }
        return $true
    }
    if ($Value -is [System.Collections.IEnumerable] -and -not ($Value -is [string])) {
        foreach ($item in $Value) {
            if (-not (Test-BridgeJsonFiniteNumbers -Value $item)) { return $false }
        }
    }
    return $true
}

function Read-BridgeGenerationToken {
    param([Parameter(Mandatory)] [string] $Path)

    $generationStream = $null
    try {
        $generationStream = [System.IO.File]::Open(
            $Path,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            ([System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete)
        )
        $raw = New-Object byte[] 513
        $rawCount = 0
        while ($rawCount -lt $raw.Length) {
            $count = $generationStream.Read($raw, $rawCount, $raw.Length - $rawCount)
            if ($count -le 0) { break }
            $rawCount += $count
        }
    } catch {
        return [pscustomobject]@{
            status = 'RETRY'
            reason = 'generation_unavailable'
            generation = $null
        }
    } finally {
        if ($null -ne $generationStream) {
            try { $generationStream.Dispose() } catch {}
        }
    }
    if ($rawCount -gt 512) {
        return [pscustomobject]@{
            status = 'BLOCKED'
            reason = 'generation_invalid'
            generation = $null
        }
    }
    if (
        $rawCount -ge 3 -and
        $raw[0] -eq 239 -and
        $raw[1] -eq 187 -and
        $raw[2] -eq 191
    ) {
        return [pscustomobject]@{
            status = 'BLOCKED'
            reason = 'generation_bom'
            generation = $null
        }
    }

    $encoding = New-Object System.Text.UTF8Encoding($false, $true)
    try {
        $text = $encoding.GetString($raw, 0, $rawCount)
        if (-not [WaggleDance.BridgeSnapshotDeltaV1.JsonContractValidator]::IsValid($text)) {
            throw 'generation JSON violates bridge lexical contract'
        }
        $document = $text | ConvertFrom-Json -ErrorAction Stop
    } catch {
        return [pscustomobject]@{
            status = 'BLOCKED'
            reason = 'generation_invalid'
            generation = $null
        }
    }
    if (-not (Test-BridgeJsonObject -Value $document)) {
        return [pscustomobject]@{
            status = 'BLOCKED'
            reason = 'generation_invalid'
            generation = $null
        }
    }
    $propertyNames = @($document.PSObject.Properties.Name)
    if ($propertyNames.Count -ne 1 -or $propertyNames[0] -cne 'generation') {
        return [pscustomobject]@{
            status = 'BLOCKED'
            reason = 'generation_invalid'
            generation = $null
        }
    }
    $generation = $document.generation
    if (-not ($generation -is [string]) -or $generation -cnotmatch $script:BridgeGenerationPattern) {
        return [pscustomobject]@{
            status = 'BLOCKED'
            reason = 'generation_invalid'
            generation = $null
        }
    }
    return [pscustomobject]@{
        status = 'OK'
        reason = ''
        generation = [string]$generation
    }
}

function Get-BridgeCursorValidation {
    param([AllowNull()] $Cursor)

    if ($null -eq $Cursor) {
        return [pscustomobject]@{
            valid = $true
            offset = [int64]0
            file_identity = ''
            generation = $null
        }
    }
    if (-not (Test-BridgeJsonObject -Value $Cursor)) {
        return [pscustomobject]@{ valid = $false; offset = [int64]0 }
    }
    $offsetProperty = $Cursor.PSObject.Properties['offset']
    $identityProperty = $Cursor.PSObject.Properties['file_identity']
    if ($null -eq $offsetProperty -or $null -eq $identityProperty) {
        return [pscustomobject]@{ valid = $false; offset = [int64]0 }
    }
    $offsetValue = $offsetProperty.Value
    $integerTypes = @(
        [byte], [sbyte], [int16], [uint16], [int32], [uint32], [int64]
    )
    $isInteger = $false
    foreach ($integerType in $integerTypes) {
        if ($offsetValue -is $integerType) {
            $isInteger = $true
            break
        }
    }
    if (-not $isInteger) {
        return [pscustomobject]@{ valid = $false; offset = [int64]0 }
    }
    $offset = [int64]$offsetValue
    $identity = $identityProperty.Value
    if ($offset -lt 0 -or -not ($identity -is [string]) -or -not $identity) {
        return [pscustomobject]@{ valid = $false; offset = $offset }
    }
    $generationProperty = $Cursor.PSObject.Properties['generation']
    $generation = if ($null -eq $generationProperty) {
        $null
    } else {
        $generationProperty.Value
    }
    if (
        $null -ne $generation -and
        (-not ($generation -is [string]) -or $generation -cnotmatch $script:BridgeGenerationPattern)
    ) {
        return [pscustomobject]@{ valid = $false; offset = $offset }
    }
    return [pscustomobject]@{
        valid = $true
        offset = $offset
        file_identity = [string]$identity
        generation = $generation
    }
}

function Read-BridgeLogSnapshotDelta {
    <#
    .SYNOPSIS
        Reads complete JSON-object rows without committing a cursor.

    .DESCRIPTION
        With no Cursor, starts a snapshot at zero. With Cursor, seeks directly
        to its offset. The log is opened exactly once and the returned cursor
        is only a candidate for the caller to commit after processing.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $Path,
        [AllowNull()] $Cursor = $null,
        [int64] $MaxBytes = 4194304,
        [int] $MaxRows = 10000,
        [AllowEmptyString()] [string] $GenerationPath = ''
    )

    $cursorState = Get-BridgeCursorValidation -Cursor $Cursor
    $offset = [int64]$cursorState.offset
    if (-not $cursorState.valid) {
        return New-BridgeLogReadResult -Status 'BLOCKED' -Reason 'cursor_invalid' -RequestedOffset $offset
    }
    if ($MaxBytes -lt 1 -or $MaxBytes -gt 67108864) {
        return New-BridgeLogReadResult -Status 'BLOCKED' -Reason 'max_bytes_invalid' -RequestedOffset $offset
    }
    if ($MaxRows -lt 1 -or $MaxRows -gt 100000) {
        return New-BridgeLogReadResult -Status 'BLOCKED' -Reason 'max_rows_invalid' -RequestedOffset $offset
    }
    if (-not $GenerationPath -and $null -ne $Cursor -and $null -ne $cursorState.generation) {
        return New-BridgeLogReadResult -Status 'BLOCKED' -Reason 'generation_configuration_changed' -RequestedOffset $offset
    }

    $generationBefore = $null
    if ($GenerationPath) {
        $generationResult = Read-BridgeGenerationToken -Path $GenerationPath
        if ($generationResult.status -cne 'OK') {
            return New-BridgeLogReadResult -Status $generationResult.status -Reason $generationResult.reason -RequestedOffset $offset
        }
        $generationBefore = [string]$generationResult.generation
        if ($null -ne $Cursor -and $cursorState.generation -cne $generationBefore) {
            return New-BridgeLogReadResult -Status 'RETRY' -Reason 'generation_changed' -RequestedOffset $offset
        }
    }

    $stream = $null
    try {
        try {
            $stream = Open-BridgeLogReadStream -Path $Path
        } catch [System.IO.FileNotFoundException] {
            if ($null -eq $Cursor) {
                return New-BridgeLogReadResult -Status 'IDLE' -Reason 'log_missing' -RequestedOffset $offset
            }
            return New-BridgeLogReadResult -Status 'RETRY' -Reason 'log_disappeared' -RequestedOffset $offset
        } catch [System.IO.DirectoryNotFoundException] {
            if ($null -eq $Cursor) {
                return New-BridgeLogReadResult -Status 'IDLE' -Reason 'log_missing' -RequestedOffset $offset
            }
            return New-BridgeLogReadResult -Status 'RETRY' -Reason 'log_disappeared' -RequestedOffset $offset
        } catch {
            return New-BridgeLogReadResult -Status 'RETRY' -Reason 'log_unavailable' -RequestedOffset $offset
        }

        try {
            $identity = Get-BridgeLogFileIdentity -Stream $stream
            $snapshotLength = [int64]$stream.Length
        } catch {
            return New-BridgeLogReadResult -Status 'BLOCKED' -Reason 'identity_unavailable' -RequestedOffset $offset
        }
        if ($null -ne $Cursor -and $cursorState.file_identity -cne $identity) {
            return New-BridgeLogReadResult -Status 'RETRY' -Reason 'file_identity_changed' -RequestedOffset $offset -SnapshotLength $snapshotLength
        }
        if ($offset -gt $snapshotLength) {
            return New-BridgeLogReadResult -Status 'RETRY' -Reason 'log_truncated' -RequestedOffset $offset -SnapshotLength $snapshotLength
        }

        $validationBytesRead = 0
        $readCalls = 0
        if ($offset -gt 0) {
            $preceding = New-Object byte[] 1
            try {
                [void]$stream.Seek($offset - 1, [System.IO.SeekOrigin]::Begin)
                $readCalls++
                $precedingRead = $stream.Read($preceding, 0, 1)
            } catch {
                return New-BridgeLogReadResult -Status 'RETRY' -Reason 'log_io_error' -RequestedOffset $offset -SnapshotLength $snapshotLength -ReadCalls $readCalls
            }
            $validationBytesRead = $precedingRead
            if ($precedingRead -ne 1) {
                return New-BridgeLogReadResult -Status 'RETRY' -Reason 'snapshot_changed' -RequestedOffset $offset -BytesRead $validationBytesRead -SnapshotLength $snapshotLength -ReadCalls $readCalls
            }
            if ($preceding[0] -ne 10) {
                return New-BridgeLogReadResult -Status 'BLOCKED' -Reason 'cursor_not_lf_boundary' -RequestedOffset $offset -BytesRead $validationBytesRead -SnapshotLength $snapshotLength -ReadCalls $readCalls
            }
        }

        $remaining = [int64]($snapshotLength - $offset)
        $requested = [int][Math]::Min($remaining, $MaxBytes)
        $bytes = New-Object byte[] $requested
        $read = 0
        if ($requested -gt 0) {
            try {
                [void]$stream.Seek($offset, [System.IO.SeekOrigin]::Begin)
                while ($read -lt $requested) {
                    $readCalls++
                    $count = $stream.Read($bytes, $read, $requested - $read)
                    if ($count -le 0) {
                        break
                    }
                    $read += $count
                }
            } catch {
                return New-BridgeLogReadResult -Status 'RETRY' -Reason 'log_io_error' -RequestedOffset $offset -BytesRead ($validationBytesRead + $read) -SnapshotLength $snapshotLength -ReadCalls $readCalls
            }
            if ($read -ne $requested) {
                return New-BridgeLogReadResult -Status 'RETRY' -Reason 'snapshot_changed' -RequestedOffset $offset -BytesRead ($validationBytesRead + $read) -SnapshotLength $snapshotLength -ReadCalls $readCalls
            }
        }
        $totalBytesRead = [int64]($validationBytesRead + $read)

        $lastLf = -1
        $rowCount = 0
        for ($index = 0; $index -lt $read; $index++) {
            if ($bytes[$index] -eq 10) {
                $lastLf = $index
                $rowCount++
                if ($rowCount -ge $MaxRows) { break }
            }
        }

        if ($lastLf -lt 0) {
            if ($read -ge $MaxBytes) {
                $result = New-BridgeLogReadResult -Status 'BLOCKED' -Reason 'record_exceeds_max_bytes' -RequestedOffset $offset -BytesRead $totalBytesRead -SnapshotLength $snapshotLength -ReadCalls $readCalls
            } else {
                $candidate = New-BridgeLogCandidateCursor -Offset $offset -FileIdentity $identity -Generation $generationBefore
                $result = New-BridgeLogReadResult -Status 'IDLE' -Reason 'partial_record' -RequestedOffset $offset -CandidateCursor $candidate -BytesRead $totalBytesRead -SnapshotLength $snapshotLength -ReadCalls $readCalls
            }
        } else {
            $consumed = [int]($lastLf + 1)
            $validationReason = ''
            if (
                $offset -eq 0 -and
                $consumed -ge 3 -and
                $bytes[0] -eq 239 -and
                $bytes[1] -eq 187 -and
                $bytes[2] -eq 191
            ) {
                $validationReason = 'log_bom'
            } else {
                $encoding = New-Object System.Text.UTF8Encoding($false, $true)
                try {
                    $text = $encoding.GetString($bytes, 0, $consumed)
                } catch {
                    $validationReason = 'invalid_utf8'
                }
            }

            $parsedRows = New-Object System.Collections.Generic.List[object]
            if (-not $validationReason) {
                $lines = $text.Split([char]10)
                for ($lineIndex = 0; $lineIndex -lt ($lines.Length - 1); $lineIndex++) {
                    $line = [string]$lines[$lineIndex]
                    if ($line.EndsWith([string][char]13, [System.StringComparison]::Ordinal)) {
                        $line = $line.Substring(0, $line.Length - 1)
                    }
                    if (-not $line) {
                        $validationReason = 'json_not_object'
                        break
                    }
                    try {
                        if (-not [WaggleDance.BridgeSnapshotDeltaV1.JsonContractValidator]::IsValid($line)) {
                            throw 'row JSON violates bridge lexical contract'
                        }
                        $row = $line | ConvertFrom-Json -ErrorAction Stop
                    } catch {
                        $validationReason = 'invalid_json'
                        break
                    }
                    if (-not (Test-BridgeJsonObject -Value $row)) {
                        $validationReason = 'json_not_object'
                        break
                    }
                    if (-not (Test-BridgeJsonFiniteNumbers -Value $row)) {
                        $validationReason = 'invalid_json'
                        break
                    }
                    [void]$parsedRows.Add($row)
                }
            }
            if ($validationReason) {
                $result = New-BridgeLogReadResult -Status 'BLOCKED' -Reason $validationReason -RequestedOffset $offset -BytesRead $totalBytesRead -SnapshotLength $snapshotLength -ReadCalls $readCalls
            } else {
                $candidate = New-BridgeLogCandidateCursor -Offset ([int64]($offset + $consumed)) -FileIdentity $identity -Generation $generationBefore
                $result = New-BridgeLogReadResult -Status 'OK' -Reason 'rows_available' -RequestedOffset $offset -Rows $parsedRows.ToArray() -CandidateCursor $candidate -BytesRead $totalBytesRead -BytesConsumed $consumed -SnapshotLength $snapshotLength -ReadCalls $readCalls
            }
        }

        try {
            $afterIdentity = Get-BridgeLogFileIdentity -Stream $stream
            $afterLength = [int64]$stream.Length
        } catch {
                return New-BridgeLogReadResult -Status 'RETRY' -Reason 'snapshot_changed' -RequestedOffset $offset -BytesRead $result.bytes_read -SnapshotLength $snapshotLength -ReadCalls $result.read_calls
        }
        if ($afterIdentity -cne $identity) {
            return New-BridgeLogReadResult -Status 'RETRY' -Reason 'file_identity_changed_during_read' -RequestedOffset $offset -BytesRead $result.bytes_read -SnapshotLength $snapshotLength -ReadCalls $result.read_calls
        }
        if ($afterLength -lt $snapshotLength) {
            return New-BridgeLogReadResult -Status 'RETRY' -Reason 'snapshot_changed' -RequestedOffset $offset -BytesRead $result.bytes_read -SnapshotLength $snapshotLength -ReadCalls $result.read_calls
        }

        if ($GenerationPath) {
            $generationResult = Read-BridgeGenerationToken -Path $GenerationPath
            if ($generationResult.status -cne 'OK') {
                return New-BridgeLogReadResult -Status $generationResult.status -Reason $generationResult.reason -RequestedOffset $offset -BytesRead $result.bytes_read -SnapshotLength $snapshotLength -ReadCalls $result.read_calls
            }
            if ($generationResult.generation -cne $generationBefore) {
                return New-BridgeLogReadResult -Status 'RETRY' -Reason 'generation_changed_during_read' -RequestedOffset $offset -BytesRead $result.bytes_read -SnapshotLength $snapshotLength -ReadCalls $result.read_calls
            }
        }
        return $result
    } finally {
        if ($null -ne $stream) {
            try { $stream.Dispose() } catch {}
        }
    }
}
