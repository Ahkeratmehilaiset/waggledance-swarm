#requires -Version 5.1

Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'BridgeLogReader.ps1')

function Get-BridgeEventGenerationPath {
    param([Parameter(Mandatory)] [string] $Path)

    $candidate = Join-Path (Split-Path -Parent $Path) 'events.generation.json'
    $stream = $null
    try {
        $stream = [System.IO.File]::Open(
            $candidate,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            ([System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete)
        )
        return $candidate
    } catch [System.IO.FileNotFoundException] {
        return ''
    } catch [System.IO.DirectoryNotFoundException] {
        return ''
    } catch {
        # An occupied but unreadable/non-leaf sidecar is configured. Return its
        # path so the canonical token reader emits a structured fail-closed result.
        return $candidate
    } finally {
        if ($null -ne $stream) {
            try { $stream.Dispose() } catch {}
        }
    }
}

function Read-BridgeEventDelta {
    <#
    .SYNOPSIS
        Reads a bounded stable delta through the canonical C0 reader.
    #>
    param(
        [Parameter(Mandatory)] [string] $Path,
        [AllowNull()] $Cursor = $null,
        [int64] $MaxBytes = 4194304,
        [int] $MaxRows = 10000
    )

    $generationPath = Get-BridgeEventGenerationPath -Path $Path
    $result = Read-BridgeLogSnapshotDelta -Path $Path -Cursor $Cursor `
        -MaxBytes $MaxBytes -MaxRows $MaxRows -GenerationPath $generationPath
    $generationPathAfter = Get-BridgeEventGenerationPath -Path $Path
    if ($generationPathAfter -cne $generationPath) {
        return New-BridgeLogReadResult -Status 'RETRY' `
            -Reason 'generation_configuration_changed' `
            -RequestedOffset $result.requested_offset `
            -BytesRead $result.bytes_read `
            -SnapshotLength $result.snapshot_length `
            -ReadCalls $result.read_calls
    }
    return $result
}

function Read-BridgeEventSnapshot {
    <#
    .SYNOPSIS
        Reads one complete full snapshot within explicit byte and row bounds.
    #>
    param(
        [Parameter(Mandatory)] [string] $Path,
        [ValidateRange(1, 67108864)] [int64] $MaxBytes = 67108864,
        [ValidateRange(1, 100000)] [int] $MaxRows = 100000
    )

    $result = Read-BridgeEventDelta -Path $Path -Cursor $null `
        -MaxBytes $MaxBytes -MaxRows $MaxRows
    if ($result.status -ceq 'OK' -and
        $result.candidate_cursor.offset -lt $result.snapshot_length -and
        (
            $result.bytes_read -lt $result.snapshot_length -or
            @($result.rows).Count -ge $MaxRows
        )) {
        return [pscustomobject]@{
            status = 'BLOCKED'
            reason = 'snapshot_exceeds_bounds'
            rows = @()
            candidate_cursor = $null
        }
    }
    return $result
}

function Read-BridgeEventTail {
    <#
    .SYNOPSIS
        Returns at most MaxLines stable rows without reading an unbounded prefix.
    #>
    param(
        [Parameter(Mandatory)] [string] $Path,
        [ValidateRange(1, 100000)] [int] $MaxLines,
        [ValidateRange(1, 67108864)] [int64] $MaxBytes = 67108864
    )

    $generationPath = Get-BridgeEventGenerationPath -Path $Path
    $result = & {
        $generation = $null
        if ($generationPath) {
            $generationResult = Read-BridgeGenerationToken -Path $generationPath
            if ($generationResult.status -cne 'OK') {
                return [pscustomobject]@{
                    status = $generationResult.status
                    reason = $generationResult.reason
                    rows = @()
                    candidate_cursor = $null
                }
            }
            $generation = [string]$generationResult.generation
        }

        $stream = $null
        try {
            try {
                $stream = Open-BridgeLogReadStream -Path $Path
            } catch [System.IO.FileNotFoundException] {
                return [pscustomobject]@{
                    status = 'IDLE'; reason = 'log_missing'; rows = @()
                    candidate_cursor = $null
                }
            } catch [System.IO.DirectoryNotFoundException] {
                return [pscustomobject]@{
                    status = 'IDLE'; reason = 'log_missing'; rows = @()
                    candidate_cursor = $null
                }
            } catch {
                return [pscustomobject]@{
                    status = 'RETRY'; reason = 'log_unavailable'; rows = @()
                    candidate_cursor = $null
                }
            }

            try {
                $identity = Get-BridgeLogFileIdentity -Stream $stream
                $snapshotLength = [int64]$stream.Length
            } catch {
                return [pscustomobject]@{
                    status = 'BLOCKED'; reason = 'identity_unavailable'; rows = @()
                    candidate_cursor = $null
                }
            }

            $requested = [int][Math]::Min($snapshotLength, $MaxBytes)
            $windowStart = [int64]($snapshotLength - $requested)
            $precedingIsLf = $false
            $bytes = New-Object byte[] $requested
            $read = 0
            try {
                if ($windowStart -gt 0) {
                    $preceding = New-Object byte[] 1
                    [void]$stream.Seek($windowStart - 1, [System.IO.SeekOrigin]::Begin)
                    $precedingRead = $stream.Read($preceding, 0, 1)
                    if ($precedingRead -ne 1) {
                        return [pscustomobject]@{
                            status = 'RETRY'; reason = 'snapshot_changed'; rows = @()
                            candidate_cursor = $null
                        }
                    }
                    $precedingIsLf = $preceding[0] -eq 10
                }
                [void]$stream.Seek($windowStart, [System.IO.SeekOrigin]::Begin)
                while ($read -lt $requested) {
                    $count = $stream.Read($bytes, $read, $requested - $read)
                    if ($count -le 0) { break }
                    $read += $count
                }
            } catch {
                return [pscustomobject]@{
                    status = 'RETRY'; reason = 'log_io_error'; rows = @()
                    candidate_cursor = $null
                }
            }
            if ($read -ne $requested) {
                return [pscustomobject]@{
                    status = 'RETRY'; reason = 'snapshot_changed'; rows = @()
                    candidate_cursor = $null
                }
            }

            try {
                $afterIdentity = Get-BridgeLogFileIdentity -Stream $stream
                $afterLength = [int64]$stream.Length
            } catch {
                return [pscustomobject]@{
                    status = 'RETRY'; reason = 'snapshot_changed'; rows = @()
                    candidate_cursor = $null
                }
            }
            if ($afterIdentity -cne $identity -or $afterLength -lt $snapshotLength) {
                return [pscustomobject]@{
                    status = 'RETRY'; reason = 'snapshot_changed'; rows = @()
                    candidate_cursor = $null
                }
            }

            if ($generationPath) {
                $afterGeneration = Read-BridgeGenerationToken -Path $generationPath
                if ($afterGeneration.status -cne 'OK') {
                    return [pscustomobject]@{
                        status = $afterGeneration.status
                        reason = $afterGeneration.reason
                        rows = @()
                        candidate_cursor = $null
                    }
                }
                if ($afterGeneration.generation -cne $generation) {
                    return [pscustomobject]@{
                        status = 'RETRY'
                        reason = 'generation_changed_during_read'
                        rows = @()
                        candidate_cursor = $null
                    }
                }
            }

            $selectedStart = -1
            $seenLf = 0
            for ($index = $read - 1; $index -ge 0; $index--) {
                if ($bytes[$index] -ne 10) { continue }
                $seenLf++
                if ($seenLf -ge ($MaxLines + 1)) {
                    $selectedStart = $index + 1
                    break
                }
            }
            if ($seenLf -eq 0) {
                if ($snapshotLength -eq 0) {
                    $cursor = New-BridgeLogCandidateCursor -Offset 0 `
                        -FileIdentity $identity -Generation $generation
                    return [pscustomobject]@{
                        status = 'IDLE'; reason = 'no_rows'; rows = @()
                        candidate_cursor = $cursor
                    }
                }
                if ($windowStart -eq 0) {
                    return [pscustomobject]@{
                        status = 'IDLE'; reason = 'no_rows'; rows = @()
                        candidate_cursor = $null
                    }
                }
                return [pscustomobject]@{
                    status = 'BLOCKED'; reason = 'tail_exceeds_max_bytes'; rows = @()
                    candidate_cursor = $null
                }
            }
            if ($selectedStart -lt 0) {
                if ($windowStart -eq 0) {
                    $selectedStart = 0
                } elseif ($precedingIsLf -and $seenLf -ge $MaxLines) {
                    $selectedStart = 0
                } else {
                    return [pscustomobject]@{
                        status = 'BLOCKED'; reason = 'tail_exceeds_max_bytes'; rows = @()
                        candidate_cursor = $null
                    }
                }
            }

            $offset = [int64]($windowStart + $selectedStart)
            $cursor = New-BridgeLogCandidateCursor -Offset $offset `
                -FileIdentity $identity -Generation $generation
        } finally {
            if ($null -ne $stream) {
                try { $stream.Dispose() } catch {}
            }
        }

        $snapshotBytes = [int64]($snapshotLength - $offset)
        $boundedBytes = [int64][Math]::Min($MaxBytes, $snapshotBytes)
        return Read-BridgeEventDelta -Path $Path -Cursor $cursor `
            -MaxBytes $boundedBytes -MaxRows $MaxLines
    }

    $generationPathAfter = Get-BridgeEventGenerationPath -Path $Path
    if ($generationPathAfter -cne $generationPath) {
        return [pscustomobject]@{
            status = 'RETRY'
            reason = 'generation_configuration_changed'
            rows = @()
            candidate_cursor = $null
        }
    }
    return $result
}

function Read-BridgeIncrementalState {
    param([Parameter(Mandatory)] [string] $StatePath)

    try {
        $stream = $null
        try {
            $stream = [System.IO.File]::Open(
                $StatePath,
                [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::Read,
                ([System.IO.FileShare]::Read -bor [System.IO.FileShare]::Delete)
            )
            $boundedBytes = New-Object byte[] 65537
            $byteCount = 0
            while ($byteCount -lt $boundedBytes.Length) {
                $read = $stream.Read(
                    $boundedBytes,
                    $byteCount,
                    $boundedBytes.Length - $byteCount
                )
                if ($read -eq 0) { break }
                $byteCount += $read
            }
        } finally {
            if ($null -ne $stream) { $stream.Dispose() }
        }
        if ($byteCount -gt 65536) { throw 'cursor state exceeds 64 KiB' }
        if ($byteCount -eq 0) {
            $bytes = [byte[]]@()
        } else {
            $bytes = [byte[]]$boundedBytes[0..($byteCount - 1)]
        }
        if ($bytes.Length -ge 3 -and
            $bytes[0] -eq 239 -and $bytes[1] -eq 187 -and $bytes[2] -eq 191) {
            throw 'cursor state has a UTF-8 BOM'
        }
        $encoding = New-Object System.Text.UTF8Encoding($false, $true)
        $text = $encoding.GetString($bytes)
        if (-not [WaggleDance.BridgeSnapshotDeltaV1.JsonContractValidator]::IsValid($text)) {
            throw 'cursor state violates the JSON contract'
        }
        $state = $text | ConvertFrom-Json -ErrorAction Stop
        if (-not (Test-BridgeJsonObject -Value $state)) {
            throw 'cursor state must be an object'
        }
        $cursorProperty = $state.PSObject.Properties['cursor']
        if ($null -ne $cursorProperty) {
            if ($null -eq $cursorProperty.Value) {
                throw 'cursor state contains a null cursor'
            }
            $validation = Get-BridgeCursorValidation -Cursor $cursorProperty.Value
            if (-not $validation.valid) { throw 'cursor state contains an invalid cursor' }
            return [pscustomobject]@{
                status = 'OK'; reason = 'cursor_available'
                cursor = $cursorProperty.Value; line_count = $null
            }
        }
        $lineProperty = $state.PSObject.Properties['line_count']
        if ($null -ne $lineProperty -and
            ($lineProperty.Value -is [int] -or $lineProperty.Value -is [long])) {
            $lineCount = [int64]$lineProperty.Value
            if ($lineCount -lt 0 -or $lineCount -gt 100000) {
                throw 'legacy line cursor is outside the supported range'
            }
            return [pscustomobject]@{
                status = 'LEGACY'; reason = 'legacy_line_cursor'
                cursor = $null; line_count = $lineCount
            }
        }
        throw 'cursor state has no supported cursor'
    } catch [System.IO.FileNotFoundException] {
        return [pscustomobject]@{
            status = 'MISSING'; reason = 'state_missing'; cursor = $null
            line_count = $null
        }
    } catch [System.IO.DirectoryNotFoundException] {
        return [pscustomobject]@{
            status = 'MISSING'; reason = 'state_missing'; cursor = $null
            line_count = $null
        }
    } catch {
        return [pscustomobject]@{
            status = 'BLOCKED'; reason = 'cursor_state_invalid'; cursor = $null
            line_count = $null
        }
    }
}

function Commit-BridgeIncrementalStateFile {
    param(
        [Parameter(Mandatory)] [string] $TemporaryPath,
        [Parameter(Mandatory)] [string] $StatePath,
        [Parameter(Mandatory)] [bool] $StateExisted,
        [Parameter(Mandatory)] [string] $BackupPath
    )

    if ($StateExisted) {
        [System.IO.File]::Replace($TemporaryPath, $StatePath, $BackupPath)
        return
    }
    [System.IO.File]::Move($TemporaryPath, $StatePath)
}

function Write-BridgeIncrementalState {
    param(
        [Parameter(Mandatory)] [string] $StatePath,
        [Parameter(Mandatory)] $Cursor,
        [hashtable] $Metadata = @{}
    )

    if ($null -eq $Cursor) { throw 'refusing to persist a null bridge cursor' }
    $validation = Get-BridgeCursorValidation -Cursor $Cursor
    if (-not $validation.valid) { throw 'refusing to persist an invalid bridge cursor' }
    $stateExisted = $false
    try {
        $stateAttributes = [System.IO.File]::GetAttributes($StatePath)
        if (($stateAttributes -band [System.IO.FileAttributes]::Directory) -ne 0) {
            throw 'refusing to persist a bridge cursor to a non-leaf state path'
        }
        $stateExisted = $true
    } catch [System.IO.FileNotFoundException] {
        # An absent state file is the normal first-write case.
    } catch [System.IO.DirectoryNotFoundException] {
        # A missing parent is created below.
    }
    $parent = Split-Path -Parent $StatePath
    if ($parent -and -not (Test-Path -LiteralPath $parent -PathType Container)) {
        [void](New-Item -ItemType Directory -Path $parent -Force -ErrorAction Stop)
    }
    $document = [ordered]@{
        schema_version = 2
        cursor = $Cursor
        updated_at_utc = (Get-Date).ToUniversalTime().ToString('o')
        metadata = $Metadata
    }
    $json = $document | ConvertTo-Json -Depth 12 -Compress
    $tmp = "$StatePath.tmp.$PID.$([guid]::NewGuid().ToString('N'))"
    $backup = "$StatePath.backup.$PID.$([guid]::NewGuid().ToString('N'))"
    $encoding = New-Object System.Text.UTF8Encoding($false)
    try {
        [System.IO.File]::WriteAllText($tmp, $json, $encoding)
        Commit-BridgeIncrementalStateFile -TemporaryPath $tmp `
            -StatePath $StatePath -StateExisted $stateExisted `
            -BackupPath $backup
    } finally {
        if (Test-Path -LiteralPath $tmp -PathType Leaf) {
            Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        }
        if (Test-Path -LiteralPath $backup -PathType Leaf) {
            Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
        }
    }
}

function Resolve-BridgeCursorForLineCount {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [ValidateRange(0, 100000)]
        [int64] $LineCount
    )

    $cursor = $null
    $remaining = $LineCount
    if ($remaining -eq 0) { return $null }
    while ($remaining -gt 0) {
        $rows = [int][Math]::Min($remaining, 100000)
        $result = Read-BridgeEventDelta -Path $Path -Cursor $cursor `
            -MaxBytes 67108864 -MaxRows $rows
        if ($result.status -cne 'OK') {
            throw "cannot migrate legacy bridge cursor: $($result.reason)"
        }
        $cursor = $result.candidate_cursor
        $remaining -= @($result.rows).Count
        if ($remaining -gt 0 -and
            $result.candidate_cursor.offset -ge $result.snapshot_length) {
            throw 'cannot migrate legacy bridge cursor: requested line does not exist'
        }
    }
    return $cursor
}
