#requires -Version 5.1
# Derived parse cache only. The canonical prefix is rehashed on every query.
# Deleting this file's cache is safe; no cursor or authoritative event is changed.
. (Join-Path $PSScriptRoot 'BridgeIncrementalReader.ps1')
. (Join-Path $PSScriptRoot 'BridgeRequestContract.ps1')

function Get-BridgeReplyTextHash {
    param([string]$Text)
    $sha=[Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text))).Replace('-','') }
    finally { $sha.Dispose() }
}

function Get-BridgeReplyPrefixHash {
    param([string]$Path,[int64]$Length)
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
    try { return Get-BridgeReplyStreamPrefixHash -Stream $stream -Length $Length }
    finally { $stream.Dispose() }
}

function Get-BridgeReplyStreamPrefixHash {
    param([IO.Stream]$Stream,[int64]$Length)
    if ($Length -lt 0 -or $Length -gt 268435456) { throw 'Reply index prefix exceeds bounds' }
    $sha=[Security.Cryptography.SHA256]::Create()
    try {
        [void]$Stream.Seek(0,[IO.SeekOrigin]::Begin)
        $buffer=New-Object byte[] 1048576
        $remaining=$Length
        while ($remaining -gt 0) {
            $read=$stream.Read($buffer,0,[int][Math]::Min($remaining,$buffer.Length))
            if ($read -eq 0) { throw 'Canonical prefix truncated during index verification' }
            [void]$sha.TransformBlock($buffer,0,$read,$null,0)
            $remaining-=$read
        }
        [void]$sha.TransformFinalBlock($buffer,0,0)
        return [BitConverter]::ToString($sha.Hash).Replace('-','')
    } finally { $sha.Dispose() }
}

function Assert-BridgeReplySnapshotStable {
    param([string]$Path,$Cursor,[string]$PrefixHash)
    $state=Get-BridgeCursorValidation -Cursor $Cursor
    if ($null -eq $Cursor -or -not $state.valid) { throw 'Invalid reply snapshot cursor' }
    $generationPath=Get-BridgeEventGenerationPath -Path $Path
    if ($generationPath) {
        $generation=Read-BridgeGenerationToken -Path $generationPath
        if ($generation.status -cne 'OK' -or $generation.generation -cne $state.generation) {
            throw 'Canonical generation changed after reply snapshot'
        }
    } elseif ($null -ne $state.generation) { throw 'Canonical generation sidecar disappeared' }
    $stream=Open-BridgeLogReadStream -Path $Path
    try {
        if ((Get-BridgeLogFileIdentity -Stream $stream) -cne $state.file_identity -or $stream.Length -lt $state.offset) {
            throw 'Canonical identity changed or prefix truncated after reply snapshot'
        }
        # Verify only the frozen prefix on this same handle. Appends, including
        # an unfinished next row, belong to the next query and are not parsed.
        if ((Get-BridgeReplyStreamPrefixHash -Stream $stream -Length $state.offset) -cne $PrefixHash) {
            throw 'Canonical prefix changed after reply snapshot'
        }
        $current=Open-BridgeLogReadStream -Path $Path
        try {
            if ((Get-BridgeLogFileIdentity -Stream $current) -cne $state.file_identity -or $current.Length -lt $state.offset) {
                throw 'Canonical identity changed during reply snapshot verification'
            }
        } finally { $current.Dispose() }
        if ((Get-BridgeEventGenerationPath -Path $Path) -cne $generationPath) { throw 'Canonical generation configuration changed' }
        if ($generationPath) {
            $after=Read-BridgeGenerationToken -Path $generationPath
            if ($after.status -cne 'OK' -or $after.generation -cne $state.generation) { throw 'Canonical generation changed during reply snapshot verification' }
        }
    } finally { $stream.Dispose() }
}

function ConvertFrom-BridgeReplyCacheJson {
    param([string]$Text)
    $arguments=@{ErrorAction='Stop'}
    if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) { $arguments.DateKind='String' }
    $Text | ConvertFrom-Json @arguments
}

function Read-BridgeReplyIndex {
    param([string]$Path,[string]$CachePath,[switch]$NoCache)
    $lock=$null
    $cache=$null
    $cacheStatus='disabled'
    $parserStamp='date-strings-v1:'+ $PSVersionTable.PSVersion.ToString()
    if (-not $NoCache) {
        # A different JSON parser must neither overwrite nor lock our cache.
        # Keep the stamp check inside the envelope as a second independent guard.
        $parserKey=(Get-BridgeReplyTextHash $parserStamp).Substring(0,24).ToLowerInvariant()
        $CachePath=Join-Path (Split-Path -Parent $CachePath) ([IO.Path]::GetFileNameWithoutExtension($CachePath)+'.'+$parserKey+'.json')
    }
    try {
        if (-not $NoCache) {
            try {
                # Keep cache files in an ordinary directory, never follow aliases.
                . (Join-Path $PSScriptRoot 'BridgeResourceScope.ps1')
                [void](Resolve-BridgeUnaliasedPath $CachePath)
                [void][IO.Directory]::CreateDirectory((Split-Path -Parent $CachePath))
                $lock=[IO.File]::Open(($CachePath+'.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
                $cacheStatus='rebuilt'
                if (Test-Path -LiteralPath $CachePath -PathType Leaf) {
                    if ((Get-Item -LiteralPath $CachePath).Length -gt 33554432) { throw 'oversized derived index' }
                    $envelope=ConvertFrom-BridgeReplyCacheJson ([IO.File]::ReadAllText($CachePath))
                    if ($envelope.schema -cne 'wd.reply-index-envelope.v1' -or
                        (Get-BridgeReplyTextHash $envelope.content) -cne $envelope.sha256) { throw 'damaged derived index' }
                    $candidate=ConvertFrom-BridgeReplyCacheJson $envelope.content
                    if ($candidate.schema -cne 'wd.reply-index.v1' -or $candidate.parser_stamp -cne $parserStamp -or $candidate.complete -ne $true -or
                        $candidate.path -cne [IO.Path]::GetFullPath($Path) -or
                        $candidate.row_count -ne @($candidate.rows).Count -or
                        $candidate.cursor.offset -ne $candidate.snapshot_length -or
                        (Get-BridgeReplyPrefixHash $Path $candidate.snapshot_length) -cne $candidate.prefix_sha256) {
                        throw 'derived index does not cover the canonical prefix'
                    }
                    $cache=$candidate
                    $cacheStatus='incremental'
                }
            } catch {
                # Cache availability/integrity is never evidence of no answer.
                $cache=$null
                $cacheStatus='rebuilt'
                if ($null -eq $lock) { $cacheStatus='uncached' }
            }
        }
        $rows=[Collections.Generic.List[object]]::new()
        $parsedRows=0
        if ($null -ne $cache) {
            foreach ($row in @($cache.rows)) { $rows.Add($row) }
            $snapshot=Read-BridgeEventDelta -Path $Path -Cursor $cache.cursor -MaxBytes 67108864 -MaxRows 100000
            # Rotation/truncation/generation changes invalidate the derived index.
            if ($snapshot.status -cin @('BLOCKED','RETRY')) { $cache=$null; $rows.Clear(); $cacheStatus='rebuilt' }
        }
        if ($null -eq $cache) {
            $length=(Get-Item -LiteralPath $Path).Length
            $beforeHash=Get-BridgeReplyPrefixHash $Path $length
            $snapshot=Read-BridgeEventSnapshot -Path $Path -MaxBytes 268435456
            if ((Get-BridgeReplyPrefixHash $Path $length) -cne $beforeHash) { throw 'Canonical prefix changed while rebuilding reply index' }
        }
        if ($snapshot.status -cin @('BLOCKED','RETRY') -or $null -eq $snapshot.candidate_cursor -or
            $snapshot.candidate_cursor.offset -ne $snapshot.snapshot_length) {
            throw ('Reply snapshot is incomplete; pending/answered cannot be inferred: '+$snapshot.reason)
        }
        foreach ($row in @($snapshot.rows)) {
            $parsedRows++
            if ((Get-BridgeContractField $row 'request_id') -or (Get-BridgeContractField $row 'in_reply_to_request_id')) {
                $rows.Add($row)
            }
        }
        $verifiedOldHash=$null
        if ($null -ne $cache) {
            $verifiedOldHash=Get-BridgeReplyPrefixHash $Path $cache.snapshot_length
            if ($verifiedOldHash -cne $cache.prefix_sha256) { throw 'Canonical indexed prefix changed during delta read' }
        }
        $unchanged=$null -ne $cache -and $cache.snapshot_length -eq $snapshot.snapshot_length
        $prefixHash=if ($unchanged) { $verifiedOldHash } else { Get-BridgeReplyPrefixHash $Path $snapshot.snapshot_length }
        # Recheck identity/generation after hashing, without reading later appends.
        Assert-BridgeReplySnapshotStable -Path $Path -Cursor $snapshot.candidate_cursor -PrefixHash $prefixHash
        if ($null -ne $lock -and -not $unchanged) {
            $content=[ordered]@{schema='wd.reply-index.v1';parser_stamp=$parserStamp;path=[IO.Path]::GetFullPath($Path);complete=$true;
                cursor=$snapshot.candidate_cursor;snapshot_length=$snapshot.snapshot_length;prefix_sha256=$prefixHash;
                row_count=$rows.Count;rows=@($rows)} | ConvertTo-Json -Depth 64 -Compress
            $envelope=@{schema='wd.reply-index-envelope.v1';content=$content;sha256=(Get-BridgeReplyTextHash $content)} | ConvertTo-Json -Compress
            $temporary=$CachePath+'.'+[guid]::NewGuid().ToString('N')+'.tmp'
            try {
                [IO.File]::WriteAllText($temporary,$envelope,(New-Object Text.UTF8Encoding($false)))
                if (Test-Path -LiteralPath $CachePath) { [IO.File]::Replace($temporary,$CachePath,[NullString]::Value) }
                else { [IO.File]::Move($temporary,$CachePath) }
            } catch {
                $cacheStatus='write_failed_snapshot_valid'
            } finally { if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary } }
        }
        [pscustomobject]@{rows=@($rows);candidate_cursor=$snapshot.candidate_cursor;snapshot_length=$snapshot.snapshot_length;
            cache_status=$cacheStatus;cache_path=$(if ($NoCache) {$null} else {$CachePath});parsed_rows=$parsedRows;prefix_sha256=$prefixHash}
    } finally { if ($null -ne $lock) { $lock.Dispose() } }
}
