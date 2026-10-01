#requires -Version 5.1
<# Append-resumable inventory continuation (opt-in, token v3). A token freezes one complete canonical
   snapshot: canonical generation, file identity, the complete frozen offset, the SHA-256 of the WHOLE
   frozen prefix, the number of indexed rows in it, the exact query hash and the page position. A
   continuation re-reads ONLY the frozen prefix, so appends after it (also an unfinished last row) are
   ignored and never duplicate or reorder the frozen pages; a rewritten or truncated prefix, a rotated
   file, a changed generation and a changed query or page size are refused. Discovery only: a row
   appended after the freeze (a newer request, revision or cancellation) is NOT in these pages, so a
   caller must still read the current request, revision, cancellation and control before acting.
   A token is an unauthenticated discovery handle: a caller-edited in-range position only moves that
   caller's own page within the frozen prefix and never grants authority. The frozen prefix is read in
   bounded chunks (at most 64 MiB per reader call), so logs larger than one reader call continue too.
   Dot-source after BridgeIncrementalReader.ps1 and BridgeReplyIndex.ps1. #>

$script:BridgeInventoryTokenPattern = '^v3\.[0-9A-F]{16}\.[0-9A-F]{16}\.[0-9]{1,12}\.[0-9A-F]{64}\.[0-9]{1,9}\.[0-9A-F]{16}\.[0-9]{1,9}$'

function Get-BridgeInventoryHash16 {
    param([string]$Text)
    return (Get-BridgeReplyTextHash $Text).Substring(0,16)
}

function Get-BridgeInventoryBoundary {
    # Generation and file identity of a canonical candidate cursor, as fixed-width hashes.
    param($Cursor)
    $state=Get-BridgeCursorValidation -Cursor $Cursor
    if ($null -eq $Cursor -or -not $state.valid) { throw 'Inventory continuation boundary is invalid' }
    $generation=if ($null -eq $state.generation) { 'null' } else { ConvertTo-Json -InputObject $state.generation -Depth 8 -Compress }
    return [pscustomobject]@{
        generation=(Get-BridgeInventoryHash16 ('generation:'+$generation))
        identity=(Get-BridgeInventoryHash16 ('identity:'+[string]$state.file_identity))
        offset=[int64]$state.offset
    }
}

function New-BridgeInventoryContinuationToken {
    param($Cursor, [string]$PrefixHash, [int]$RowCount, [string]$QueryHash, [int]$Position)
    $boundary=Get-BridgeInventoryBoundary $Cursor
    if ($PrefixHash -cnotmatch '^[0-9A-F]{64}$' -or $QueryHash -cnotmatch '^[0-9A-F]{16}$' -or $RowCount -lt 0 -or $Position -lt 0) {
        throw 'Inventory continuation token fields are invalid'
    }
    return ('v3.{0}.{1}.{2}.{3}.{4}.{5}.{6}' -f $boundary.generation,$boundary.identity,$boundary.offset,$PrefixHash,$RowCount,$QueryHash,$Position)
}

function Read-BridgeInventoryFrozenPrefix {
    # The indexed rows of exactly the frozen prefix named by $Token, or a throw. Never reads past it.
    param([string]$Path, [string]$Token, [string]$QueryHash)
    if ($Token -cnotmatch $script:BridgeInventoryTokenPattern) { throw 'Inventory continuation token is malformed' }
    $parts=$Token.Split('.')
    [int64]$offset=0; [int]$rowCount=0; [int]$position=0
    if (-not [int64]::TryParse($parts[3],[ref]$offset) -or -not [int]::TryParse($parts[5],[ref]$rowCount) -or
        -not [int]::TryParse($parts[7],[ref]$position) -or $offset -le 0 -or $position -gt $rowCount) {
        throw 'Inventory continuation token is malformed'
    }
    $prefix=$parts[4]
    if ($parts[6] -cne $QueryHash) { throw 'Inventory continuation token does not match this query' }
    # Truncation below the frozen offset throws here; a rewritten prefix fails the hash.
    if ((Get-BridgeReplyPrefixHash $Path $offset) -cne $prefix) { throw 'Inventory continuation prefix changed' }
    # RCO2 c868 R-1: one reader call is bounded to 64 MiB (Read-BridgeLogSnapshotDelta max_bytes_invalid), so the
    # frozen prefix is read in chunks, each at most 64 MiB and never past the frozen offset; every chunk continues
    # from the previous candidate cursor (the reader re-validates its identity and generation) and must strictly
    # advance, and the existing 100000-row bound applies to the whole prefix. Appends are never read.
    $chunkBytes=[int64]67108864
    $maxRows=100000
    $allRows=[Collections.Generic.List[object]]::new()
    $cursor=$null
    $done=[int64]0
    do {
        if ($allRows.Count -ge $maxRows) { throw 'Inventory continuation frozen prefix exceeds the row bound' }
        $delta=Read-BridgeEventDelta -Path $Path -Cursor $cursor -MaxBytes ([Math]::Min($chunkBytes, $offset - $done)) `
            -MaxRows ($maxRows - $allRows.Count)
        if ($delta.status -cin @('BLOCKED','RETRY') -or $null -eq $delta.candidate_cursor) {
            throw ('Inventory continuation prefix is not a complete frozen snapshot: '+$delta.reason)
        }
        $next=[int64]$delta.candidate_cursor.offset
        if ($next -le $done -or $next -gt $offset) {
            throw 'Inventory continuation frozen prefix did not advance within its boundary'
        }
        foreach ($row in @($delta.rows)) { $allRows.Add($row) }
        $cursor=$delta.candidate_cursor
        $done=$next
    } while ($done -lt $offset)
    $boundary=Get-BridgeInventoryBoundary $cursor
    if ($boundary.generation -cne $parts[1] -or $boundary.identity -cne $parts[2]) {
        throw 'Inventory continuation file identity or generation changed'
    }
    $rows=[Collections.Generic.List[object]]::new()
    foreach ($row in $allRows) {
        if ((Get-BridgeContractField $row 'request_id') -or (Get-BridgeContractField $row 'in_reply_to_request_id')) { $rows.Add($row) }
    }
    if ($rows.Count -ne $rowCount) { throw 'Inventory continuation frozen rows changed' }
    # Same handle checks as the reply index: identity, generation and the frozen prefix after the read.
    Assert-BridgeReplySnapshotStable -Path $Path -Cursor $cursor -PrefixHash $prefix
    return [pscustomobject]@{rows=@($rows);candidate_cursor=$cursor;snapshot_length=$offset;
        cache_status='frozen_prefix';cache_path=$null;parsed_rows=$allRows.Count;prefix_sha256=$prefix;position=$position}
}
