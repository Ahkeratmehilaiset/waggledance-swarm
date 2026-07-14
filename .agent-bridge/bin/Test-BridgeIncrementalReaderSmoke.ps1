#requires -Version 5.1
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'BridgeIncrementalReader.ps1')

$results = New-Object System.Collections.Generic.List[object]
function Add-Check {
    param(
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [bool] $Passed,
        [string] $Detail = ''
    )
    [void]$results.Add([pscustomobject]@{
        name = $Name
        passed = $Passed
        detail = $Detail
    })
    $marker = if ($Passed) { 'PASS' } else { 'FAIL' }
    $color = if ($Passed) { 'Green' } else { 'Red' }
    Write-Host ("  [{0}] {1}" -f $marker, $Name) -ForegroundColor $color
    if ($Detail) { Write-Host "        $Detail" }
}

$tempRoot = Join-Path $env:TEMP `
    "bridge-incremental-reader-smoke-$([guid]::NewGuid().ToString('N').Substring(0,12))"
$eventsPath = Join-Path $tempRoot 'events.jsonl'
$encoding = New-Object System.Text.UTF8Encoding($false)

try {
    Write-Host 'Bridge incremental reader smoke test' -ForegroundColor Cyan
    Write-Host '===================================='
    [void](New-Item -ItemType Directory -Path $tempRoot -Force)

    $historyLine = '{"agent":"history","type":"heartbeat","to":""}' + [Environment]::NewLine
    $historyBytes = $encoding.GetBytes($historyLine)
    $targetBytes = 8MB
    $stream = [System.IO.File]::Open(
        $eventsPath,
        [System.IO.FileMode]::Create,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::ReadWrite
    )
    try {
        while ($stream.Length -lt $targetBytes) {
            $stream.Write($historyBytes, 0, $historyBytes.Length)
        }
    } finally {
        $stream.Dispose()
    }

    $baseline = Get-BridgeEventFileLength -Path $eventsPath
    $appendedLine = '{"agent":"codex","type":"message","to":"claude","task_id":"delta"}' + `
        [Environment]::NewLine
    [System.IO.File]::AppendAllText($eventsPath, $appendedLine, $encoding)
    $delta = Read-BridgeEventDelta -Path $eventsPath -Offset $baseline
    $appendBytes = $encoding.GetByteCount($appendedLine)
    Add-Check 'large historical prefix is not re-read' `
        ($delta.bytes_read -eq $appendBytes -and $delta.bytes_read -lt 4096) `
        "history=$baseline bytes; delta=$($delta.bytes_read) bytes"
    Add-Check 'appended complete row is returned once' `
        ($delta.lines.Count -eq 1 -and $delta.lines[0] -match '"task_id":"delta"') `
        (($delta.lines -join "`n"))

    $offset = [int64]$delta.next_offset
    $partialLine = '{"agent":"codex","type":"finding","to":"claude","task_id":"partial"}'
    $cut = [int]($partialLine.Length / 2)
    [System.IO.File]::AppendAllText($eventsPath, $partialLine.Substring(0, $cut), $encoding)
    $partial = Read-BridgeEventDelta -Path $eventsPath -Offset $offset
    Add-Check 'unterminated row does not advance the cursor' `
        ($partial.lines.Count -eq 0 -and $partial.next_offset -eq $offset -and
         $partial.bytes_consumed -eq 0) `
        "offset=$offset next=$($partial.next_offset) read=$($partial.bytes_read)"

    [System.IO.File]::AppendAllText(
        $eventsPath,
        $partialLine.Substring($cut) + [Environment]::NewLine,
        $encoding
    )
    $completed = Read-BridgeEventDelta -Path $eventsPath -Offset $offset
    Add-Check 'completed partial row is delivered exactly once' `
        ($completed.lines.Count -eq 1 -and $completed.lines[0] -match '"task_id":"partial"') `
        (($completed.lines -join "`n"))

    $oldOffset = [int64]$completed.next_offset
    $replacement = '{"agent":"codex","type":"message","task_id":"replacement"}' + `
        [Environment]::NewLine
    [System.IO.File]::WriteAllText($eventsPath, $replacement, $encoding)
    $truncated = Read-BridgeEventDelta -Path $eventsPath -Offset $oldOffset
    Add-Check 'length regression is reported as truncation' `
        ($truncated.truncated -eq $true -and
         $truncated.next_offset -eq $truncated.file_length) `
        "old=$oldOffset new=$($truncated.file_length)"

    $line1 = 'first' + [Environment]::NewLine
    $line2 = 'second' + [Environment]::NewLine
    $line3 = 'third' + [Environment]::NewLine
    [System.IO.File]::WriteAllText($eventsPath, $line1 + $line2 + $line3, $encoding)
    $lineTwoOffset = Resolve-BridgeByteOffsetForLineCount -Path $eventsPath -LineCount 2
    Add-Check 'legacy line-count conversion lands after requested LF' `
        ($lineTwoOffset -eq $encoding.GetByteCount($line1 + $line2)) `
        "offset=$lineTwoOffset"

    $writer = [System.IO.File]::Open(
        $eventsPath,
        [System.IO.FileMode]::Append,
        [System.IO.FileAccess]::Write,
        ([System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete)
    )
    try {
        $writerOffset = $writer.Position
        $liveLine = $encoding.GetBytes('live' + [Environment]::NewLine)
        $writer.Write($liveLine, 0, $liveLine.Length)
        $writer.Flush()
        $liveDelta = Read-BridgeEventDelta -Path $eventsPath -Offset $writerOffset
        Add-Check 'reader coexists with an open append writer' `
            ($liveDelta.lines.Count -eq 1 -and $liveDelta.lines[0] -eq 'live') `
            (($liveDelta.lines -join "`n"))
    } finally {
        $writer.Dispose()
    }

    Write-Host ''
    $passed = ($results | Where-Object { $_.passed }).Count
    $total = $results.Count
    $color = if ($passed -eq $total) { 'Green' } else { 'Red' }
    Write-Host ("Result: {0}/{1} checks passed" -f $passed, $total) -ForegroundColor $color
    if ($passed -ne $total) { exit 1 }
    exit 0
} finally {
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
