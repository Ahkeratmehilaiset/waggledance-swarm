#requires -Version 5.1

Set-StrictMode -Version Latest

function Open-BridgeEventReadStream {
    param([Parameter(Mandatory)] [string] $Path)

    return [System.IO.File]::Open(
        $Path,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        ([System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete)
    )
}

function Get-BridgeEventFileLength {
    param([Parameter(Mandatory)] [string] $Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return [int64]0
    }

    $stream = $null
    try {
        $stream = Open-BridgeEventReadStream -Path $Path
        return [int64]$stream.Length
    } catch [System.IO.FileNotFoundException] {
        return [int64]0
    } catch [System.IO.DirectoryNotFoundException] {
        return [int64]0
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
    }
}

function Read-BridgeEventDelta {
    <#
    .SYNOPSIS
        Reads only complete JSONL rows after a durable byte offset.

    .DESCRIPTION
        The offset advances only through the final LF observed in a bounded
        snapshot. A concurrently appended partial UTF-8 row is therefore read
        again and delivered only after its terminating LF arrives.
    #>
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [ValidateRange(0, [long]::MaxValue)] [int64] $Offset,
        [ValidateRange(1024, 67108864)] [int] $MaxBytes = 4194304
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return [pscustomobject]@{
            exists         = $false
            truncated      = ($Offset -gt 0)
            file_length    = [int64]0
            next_offset    = [int64]0
            bytes_read     = [int64]0
            bytes_consumed = [int64]0
            lines           = @()
        }
    }

    $stream = $null
    try {
        $stream = Open-BridgeEventReadStream -Path $Path
        $snapshotLength = [int64]$stream.Length
        if ($Offset -gt $snapshotLength) {
            return [pscustomobject]@{
                exists         = $true
                truncated      = $true
                file_length    = $snapshotLength
                next_offset    = $snapshotLength
                bytes_read     = [int64]0
                bytes_consumed = [int64]0
                lines           = @()
            }
        }

        $remaining = [int64]($snapshotLength - $Offset)
        if ($remaining -le 0) {
            return [pscustomobject]@{
                exists         = $true
                truncated      = $false
                file_length    = $snapshotLength
                next_offset    = $Offset
                bytes_read     = [int64]0
                bytes_consumed = [int64]0
                lines           = @()
            }
        }

        $requested = [int][Math]::Min($remaining, [int64]$MaxBytes)
        $bytes = New-Object byte[] $requested
        [void]$stream.Seek($Offset, [System.IO.SeekOrigin]::Begin)

        $read = 0
        while ($read -lt $requested) {
            $count = $stream.Read($bytes, $read, $requested - $read)
            if ($count -le 0) { break }
            $read += $count
        }

        $lastLf = -1
        for ($index = $read - 1; $index -ge 0; $index--) {
            if ($bytes[$index] -eq 10) {
                $lastLf = $index
                break
            }
        }

        if ($lastLf -lt 0) {
            if ($read -ge $MaxBytes -and $remaining -gt $read) {
                throw "bridge JSONL row exceeds incremental read bound of $MaxBytes bytes at offset $Offset"
            }
            return [pscustomobject]@{
                exists         = $true
                truncated      = $false
                file_length    = $snapshotLength
                next_offset    = $Offset
                bytes_read     = [int64]$read
                bytes_consumed = [int64]0
                lines           = @()
            }
        }

        $consumed = $lastLf + 1
        $encoding = New-Object System.Text.UTF8Encoding($false, $false)
        $text = $encoding.GetString($bytes, 0, $consumed)
        if ($Offset -eq 0 -and $text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) {
            $text = $text.Substring(1)
        }
        $lines = @($text -split "`r?`n")
        if ($lines.Count -gt 0 -and $lines[-1] -eq '') {
            if ($lines.Count -eq 1) {
                $lines = @()
            } else {
                $lines = @($lines[0..($lines.Count - 2)])
            }
        }

        return [pscustomobject]@{
            exists         = $true
            truncated      = $false
            file_length    = $snapshotLength
            next_offset    = [int64]($Offset + $consumed)
            bytes_read     = [int64]$read
            bytes_consumed = [int64]$consumed
            lines           = @($lines)
        }
    } catch [System.IO.FileNotFoundException] {
        return [pscustomobject]@{
            exists         = $false
            truncated      = ($Offset -gt 0)
            file_length    = [int64]0
            next_offset    = [int64]0
            bytes_read     = [int64]0
            bytes_consumed = [int64]0
            lines           = @()
        }
    } catch [System.IO.DirectoryNotFoundException] {
        return [pscustomobject]@{
            exists         = $false
            truncated      = ($Offset -gt 0)
            file_length    = [int64]0
            next_offset    = [int64]0
            bytes_read     = [int64]0
            bytes_consumed = [int64]0
            lines           = @()
        }
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
    }
}

function Resolve-BridgeByteOffsetForLineCount {
    <#
    .SYNOPSIS
        Converts the legacy Watch-Bridge test hook to a byte offset once.
    #>
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [ValidateRange(0, [int]::MaxValue)] [int] $LineCount
    )

    if ($LineCount -eq 0 -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return [int64]0
    }

    $stream = $null
    try {
        $stream = Open-BridgeEventReadStream -Path $Path
        $buffer = New-Object byte[] 65536
        $offset = [int64]0
        $seen = 0
        while ($true) {
            $read = $stream.Read($buffer, 0, $buffer.Length)
            if ($read -le 0) { return [int64]$stream.Length }
            for ($index = 0; $index -lt $read; $index++) {
                $offset++
                if ($buffer[$index] -eq 10) {
                    $seen++
                    if ($seen -ge $LineCount) { return $offset }
                }
            }
        }
    } catch [System.IO.FileNotFoundException] {
        return [int64]0
    } catch [System.IO.DirectoryNotFoundException] {
        return [int64]0
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
    }
}
