#requires -Version 5.1
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'BridgeIncrementalReader.ps1')

function Get-ActualIdentity {
    param([Parameter(Mandatory)] [string] $Path)

    $stream = Open-BridgeLogReadStream -Path $Path
    try {
        return Get-BridgeLogFileIdentity -Stream $stream
    } finally {
        $stream.Dispose()
    }
}

$results = New-Object System.Collections.Generic.List[object]
function Add-Check {
    param(
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [bool] $Passed,
        [string] $Detail = ''
    )
    [void]$results.Add([pscustomobject]@{
        name = $Name; passed = $Passed; detail = $Detail
    })
    $marker = if ($Passed) { 'PASS' } else { 'FAIL' }
    Write-Host ("  [{0}] {1}" -f $marker, $Name)
    if ($Detail) { Write-Host "        $Detail" }
}

$tempRoot = Join-Path $env:TEMP `
    "bridge-incremental-safe-smoke-$([guid]::NewGuid().ToString('N').Substring(0,12))"
$eventsPath = Join-Path $tempRoot 'events.jsonl'
$statePath = Join-Path $tempRoot 'reader.cursor.json'
$encoding = New-Object System.Text.UTF8Encoding($false)

try {
    Write-Host 'Bridge safe incremental reader smoke test'
    Write-Host '========================================='
    [void](New-Item -ItemType Directory -Path $tempRoot -Force)

    $history = $encoding.GetBytes('{"history":true}' + [char]10)
    $stream = [System.IO.File]::Open(
        $eventsPath,
        [System.IO.FileMode]::Create,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::ReadWrite
    )
    try {
        while ($stream.Length -lt 8MB) {
            $stream.Write($history, 0, $history.Length)
        }
    } finally {
        $stream.Dispose()
    }
    $baseline = Read-BridgeEventTail -Path $eventsPath -MaxLines 1
    $deltaLine = $encoding.GetBytes('{"task_id":"delta"}' + [char]10)
    $stream = [System.IO.File]::Open(
        $eventsPath,
        [System.IO.FileMode]::Append,
        [System.IO.FileAccess]::Write,
        ([System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete)
    )
    try {
        $stream.Write($deltaLine, 0, $deltaLine.Length)
    } finally {
        $stream.Dispose()
    }
    $delta = Read-BridgeEventDelta -Path $eventsPath `
        -Cursor $baseline.candidate_cursor -MaxBytes 4096
    Add-Check 'steady-state read is O(delta)' (
        $delta.status -ceq 'OK' -and
        $delta.bytes_read -eq ($deltaLine.Length + 1) -and
        $delta.bytes_read -lt 4096
    ) "bytes_read=$($delta.bytes_read)"

    $delivered = $delta.candidate_cursor
    $again = Read-BridgeEventDelta -Path $eventsPath -Cursor $delivered
    Add-Check 'complete append is delivered exactly once' (
        @($delta.rows).Count -eq 1 -and
        [string]$delta.rows[0].task_id -ceq 'delta' -and
        $again.status -ceq 'IDLE' -and
        @($again.rows).Count -eq 0
    )

    $oldLength = [int64]$delivered.offset
    $replacementPath = Join-Path $tempRoot 'replacement.jsonl'
    $largeValue = 'x' * ([int]$oldLength + 128)
    [System.IO.File]::WriteAllText(
        $replacementPath,
        ('{"task_id":"replacement","value":"' + $largeValue + '"}' + [char]10),
        $encoding
    )
    Move-Item -LiteralPath $replacementPath -Destination $eventsPath -Force
    $replacementResult = Read-BridgeEventDelta -Path $eventsPath -Cursor $delivered
    Add-Check 'larger replacement with fewer rows is retry, not append' (
        (Get-Item -LiteralPath $eventsPath).Length -gt $oldLength -and
        $replacementResult.status -ceq 'RETRY' -and
        $replacementResult.reason -ceq 'file_identity_changed' -and
        $null -eq $replacementResult.candidate_cursor -and
        @($replacementResult.rows).Count -eq 0
    ) "status=$($replacementResult.status) reason=$($replacementResult.reason)"

    $openReader = Open-BridgeLogReadStream -Path $eventsPath
    try {
        $rotatedPath = Join-Path $tempRoot 'events.rotated.jsonl'
        Move-Item -LiteralPath $eventsPath -Destination $rotatedPath
        [System.IO.File]::WriteAllText(
            $eventsPath,
            ('{"task_id":"after-rotation"}' + [char]10),
            $encoding
        )
        $rotationWorked = (
            (Test-Path -LiteralPath $rotatedPath -PathType Leaf) -and
            (Test-Path -LiteralPath $eventsPath -PathType Leaf)
        )
    } finally {
        $openReader.Dispose()
    }
    Add-Check 'reader FileShare Delete permits writer rotation' $rotationWorked

    $badCursor = [pscustomobject]@{
        offset = 0
        file_identity = Get-ActualIdentity -Path $eventsPath
        generation = $null
    }
    [System.IO.File]::WriteAllBytes(
        $eventsPath,
        [byte[]](123,34,120,34,58,34,255,34,125,10)
    )
    $invalidUtf8 = Read-BridgeEventDelta -Path $eventsPath -Cursor $badCursor
    Add-Check 'invalid UTF-8 is fail closed and cursor neutral' (
        $invalidUtf8.status -ceq 'BLOCKED' -and
        $invalidUtf8.reason -ceq 'invalid_utf8' -and
        $null -eq $invalidUtf8.candidate_cursor -and
        @($invalidUtf8.rows).Count -eq 0
    )

    [System.IO.File]::WriteAllText($statePath, '{"cursor":', $encoding)
    $beforeState = [System.IO.File]::ReadAllBytes($statePath)
    $invalidState = Read-BridgeIncrementalState -StatePath $statePath
    $afterState = [System.IO.File]::ReadAllBytes($statePath)
    Add-Check 'truncated cursor state is rejected without mutation' (
        $invalidState.status -ceq 'BLOCKED' -and
        [Convert]::ToBase64String($beforeState) -ceq
            [Convert]::ToBase64String($afterState)
    )

    [System.IO.File]::WriteAllText(
        $eventsPath,
        (
            '{"row":1}' + [char]10 +
            '{"row":2}' + [char]10 +
            '{"row":3}' + [char]10
        ),
        $encoding
    )
    $firstReader = Read-BridgeEventSnapshot -Path $eventsPath
    $secondReader = Read-BridgeEventSnapshot -Path $eventsPath
    [System.IO.File]::AppendAllText(
        $eventsPath, ('{"row":4}' + [char]10), $encoding
    )
    $firstDelta = Read-BridgeEventDelta -Path $eventsPath `
        -Cursor $firstReader.candidate_cursor
    $secondDelta = Read-BridgeEventDelta -Path $eventsPath `
        -Cursor $secondReader.candidate_cursor
    Add-Check 'independent readers do not share cursor state' (
        @($firstDelta.rows).Count -eq 1 -and
        @($secondDelta.rows).Count -eq 1 -and
        [int]$firstDelta.rows[0].row -eq 4 -and
        [int]$secondDelta.rows[0].row -eq 4
    )

    $bounded = Read-BridgeEventDelta -Path $eventsPath -MaxRows 2
    Add-Check 'row count is bounded per call' (
        $bounded.status -ceq 'OK' -and @($bounded.rows).Count -eq 2
    )

    $exactTail = '{"row":2}' + [char]10 + '{"row":3}' + [char]10
    [System.IO.File]::WriteAllText(
        $eventsPath,
        ('{"row":1}' + [char]10 + $exactTail),
        $encoding
    )
    $exactResult = Read-BridgeEventTail -Path $eventsPath -MaxLines 2 `
        -MaxBytes ($encoding.GetByteCount($exactTail))
    Add-Check 'exact LF-aligned tail window is accepted' (
        $exactResult.status -ceq 'OK' -and
        @($exactResult.rows).Count -eq 2 -and
        [int]$exactResult.rows[0].row -eq 2 -and
        [int]$exactResult.rows[1].row -eq 3
    ) "status=$($exactResult.status) reason=$($exactResult.reason)"

    $generationDirectory = Join-Path $tempRoot 'events.generation.json'
    [void](New-Item -ItemType Directory -Path $generationDirectory)
    $nonLeafDelta = Read-BridgeEventDelta -Path $eventsPath
    $nonLeafTail = Read-BridgeEventTail -Path $eventsPath -MaxLines 1
    Add-Check 'non-leaf generation sidecar fails closed for delta' (
        $nonLeafDelta.status -ceq 'RETRY' -and
        $nonLeafDelta.reason -ceq 'generation_unavailable' -and
        @($nonLeafDelta.rows).Count -eq 0 -and
        $null -eq $nonLeafDelta.candidate_cursor
    ) "status=$($nonLeafDelta.status) reason=$($nonLeafDelta.reason)"
    Add-Check 'non-leaf generation sidecar fails closed for tail' (
        $nonLeafTail.status -ceq 'RETRY' -and
        $nonLeafTail.reason -ceq 'generation_unavailable' -and
        @($nonLeafTail.rows).Count -eq 0 -and
        $null -eq $nonLeafTail.candidate_cursor
    ) "status=$($nonLeafTail.status) reason=$($nonLeafTail.reason)"
    function Test-Path {
        return $false
    }
    try {
        $probeErrorDelta = Read-BridgeEventDelta -Path $eventsPath
    } finally {
        Remove-Item -LiteralPath Function:\Test-Path -Force
    }
    Add-Check 'generation probe failure cannot disguise an occupied sidecar' (
        $probeErrorDelta.status -ceq 'RETRY' -and
        $probeErrorDelta.reason -ceq 'generation_unavailable' -and
        @($probeErrorDelta.rows).Count -eq 0 -and
        $null -eq $probeErrorDelta.candidate_cursor
    ) "status=$($probeErrorDelta.status) reason=$($probeErrorDelta.reason)"
    Remove-Item -LiteralPath $generationDirectory -Recurse -Force

    [System.IO.File]::WriteAllText($eventsPath, '{"partial":true}', $encoding)
    $partialOnlyTail = Read-BridgeEventTail -Path $eventsPath -MaxLines 1 `
        -MaxBytes 64
    Add-Check 'byte-zero partial-only tail is idle and cursor-neutral' (
        $partialOnlyTail.status -ceq 'IDLE' -and
        $partialOnlyTail.reason -ceq 'no_rows' -and
        @($partialOnlyTail.rows).Count -eq 0 -and
        $null -eq $partialOnlyTail.candidate_cursor
    ) "status=$($partialOnlyTail.status) reason=$($partialOnlyTail.reason)"
    [System.IO.File]::AppendAllText($eventsPath, [string][char]10, $encoding)
    $completedPartialTail = Read-BridgeEventTail -Path $eventsPath -MaxLines 1 `
        -MaxBytes 64
    $afterCompletedPartial = Read-BridgeEventDelta -Path $eventsPath `
        -Cursor $completedPartialTail.candidate_cursor
    Add-Check 'byte-zero partial row is delivered exactly once after LF' (
        $completedPartialTail.status -ceq 'OK' -and
        @($completedPartialTail.rows).Count -eq 1 -and
        [bool]$completedPartialTail.rows[0].partial -and
        $afterCompletedPartial.status -ceq 'IDLE' -and
        @($afterCompletedPartial.rows).Count -eq 0
    ) (
        "tail_status=$($completedPartialTail.status) " +
        "tail_rows=$(@($completedPartialTail.rows).Count) " +
        "after_status=$($afterCompletedPartial.status)"
    )

    foreach ($earlyReturnCase in @(
        [pscustomobject]@{ name = 'empty'; content = '' },
        [pscustomobject]@{ name = 'partial-only'; content = '{"partial":true}' }
    )) {
        [System.IO.File]::WriteAllText(
            $eventsPath, [string]$earlyReturnCase.content, $encoding
        )
        $script:generationRacePath = Join-Path $tempRoot 'events.generation.json'
        $script:smokeEncoding = $encoding
        $script:originalIdentityFunction = ${function:Get-BridgeLogFileIdentity}
        $script:generationRaceIdentityCalls = 0
        try {
            function Get-BridgeLogFileIdentity {
                param([Parameter(Mandatory)] [System.IO.FileStream] $Stream)

                $script:generationRaceIdentityCalls++
                $captured = & $script:originalIdentityFunction -Stream $Stream
                if ($script:generationRaceIdentityCalls -eq 1) {
                    [System.IO.File]::WriteAllText(
                        $script:generationRacePath,
                        '{"generation":"g1"}',
                        $script:smokeEncoding
                    )
                }
                return $captured
            }
            $earlyRaceTail = Read-BridgeEventTail -Path $eventsPath -MaxLines 1
        } finally {
            Set-Item -LiteralPath Function:\Get-BridgeLogFileIdentity `
                -Value $script:originalIdentityFunction
        }
        Add-Check "$($earlyReturnCase.name) tail generation configuration race fails closed" (
            $earlyRaceTail.status -ceq 'RETRY' -and
            $earlyRaceTail.reason -ceq 'generation_configuration_changed' -and
            @($earlyRaceTail.rows).Count -eq 0 -and
            $null -eq $earlyRaceTail.candidate_cursor
        ) (
            "status=$($earlyRaceTail.status) " +
            "reason=$($earlyRaceTail.reason) " +
            "identity_calls=$($script:generationRaceIdentityCalls)"
        )
        Remove-Item -LiteralPath $script:generationRacePath -Force
    }

    [System.IO.File]::WriteAllText(
        $eventsPath, ('{"phase":1}' + [char]10), $encoding
    )
    $script:generationRacePath = Join-Path $tempRoot 'events.generation.json'
    $script:smokeEncoding = $encoding
    $script:originalIdentityFunction = ${function:Get-BridgeLogFileIdentity}
    $script:generationRaceIdentityCalls = 0
    try {
        function Get-BridgeLogFileIdentity {
            param([Parameter(Mandatory)] [System.IO.FileStream] $Stream)

            $script:generationRaceIdentityCalls++
            $captured = & $script:originalIdentityFunction -Stream $Stream
            if ($script:generationRaceIdentityCalls -eq 1) {
                [System.IO.File]::WriteAllText(
                    $script:generationRacePath,
                    '{"generation":"g1"}',
                    $script:smokeEncoding
                )
            }
            return $captured
        }
        $generationRaceTail = Read-BridgeEventTail -Path $eventsPath -MaxLines 1
    } finally {
        Set-Item -LiteralPath Function:\Get-BridgeLogFileIdentity `
            -Value $script:originalIdentityFunction
    }
    Add-Check 'generation configuration race fails closed' (
        $generationRaceTail.status -ceq 'RETRY' -and
        $generationRaceTail.reason -ceq 'generation_configuration_changed' -and
        @($generationRaceTail.rows).Count -eq 0 -and
        $null -eq $generationRaceTail.candidate_cursor
    ) (
        "status=$($generationRaceTail.status) " +
        "reason=$($generationRaceTail.reason) " +
        "identity_calls=$($script:generationRaceIdentityCalls)"
    )
    Remove-Item -LiteralPath $script:generationRacePath -Force

    [System.IO.File]::WriteAllText(
        $eventsPath, ('{"phase":1}' + [char]10), $encoding
    )
    $script:originalOpenFunction = ${function:Open-BridgeLogReadStream}
    $script:tailReopenCalls = 0
    try {
        function Open-BridgeLogReadStream {
            param([Parameter(Mandatory)] [string] $Path)

            $script:tailReopenCalls++
            if ($script:tailReopenCalls -eq 2) {
                [System.IO.File]::AppendAllText(
                    $Path, ('{"phase":2}' + [char]10), $script:smokeEncoding
                )
            }
            return & $script:originalOpenFunction -Path $Path
        }
        $stableTail = Read-BridgeEventTail -Path $eventsPath -MaxLines 2
    } finally {
        Set-Item -LiteralPath Function:\Open-BridgeLogReadStream `
            -Value $script:originalOpenFunction
    }
    $afterStableTail = Read-BridgeEventDelta -Path $eventsPath `
        -Cursor $stableTail.candidate_cursor
    Add-Check 'tail second phase cannot exceed first stable snapshot' (
        $stableTail.status -ceq 'OK' -and
        @($stableTail.rows).Count -eq 1 -and
        [int]$stableTail.rows[0].phase -eq 1 -and
        $afterStableTail.status -ceq 'OK' -and
        @($afterStableTail.rows).Count -eq 1 -and
        [int]$afterStableTail.rows[0].phase -eq 2
    ) (
        "first_rows=$(@($stableTail.rows).Count) " +
        "next_rows=$(@($afterStableTail.rows).Count) " +
        "opens=$($script:tailReopenCalls)"
    )

    [System.IO.File]::WriteAllText(
        $eventsPath,
        ('{"row":1}' + [char]10 + '{"row":2}'),
        $encoding
    )
    $tornTail = Read-BridgeEventTail -Path $eventsPath -MaxLines 2
    Add-Check 'unterminated final row is cursor-neutral' (
        $tornTail.status -ceq 'OK' -and
        @($tornTail.rows).Count -eq 1 -and
        [int]$tornTail.rows[0].row -eq 1 -and
        $tornTail.candidate_cursor.offset -eq
             $encoding.GetByteCount('{"row":1}' + [char]10)
    )
    $tornSnapshot = Read-BridgeEventSnapshot -Path $eventsPath `
        -MaxBytes 4096 -MaxRows 2
    Add-Check 'full snapshot retains complete prefix before torn suffix' (
        $tornSnapshot.status -ceq 'OK' -and
        @($tornSnapshot.rows).Count -eq 1 -and
        [int]$tornSnapshot.rows[0].row -eq 1 -and
        $tornSnapshot.candidate_cursor.offset -eq
            $encoding.GetByteCount('{"row":1}' + [char]10)
    )

    [System.IO.File]::WriteAllText(
        $eventsPath,
        ('{"row":1}' + [char]10 + '{"row":2}' + [char]10 +
            '{"row":3}' + [char]10),
        $encoding
    )
    $legacyStatePath = Join-Path $tempRoot 'legacy.cursor.json'
    [System.IO.File]::WriteAllText(
        $legacyStatePath, '{"line_count":2}', $encoding
    )
    $legacyState = Read-BridgeIncrementalState -StatePath $legacyStatePath
    $legacyCursor = Resolve-BridgeCursorForLineCount -Path $eventsPath `
        -LineCount $legacyState.line_count
    $legacyDelta = Read-BridgeEventDelta -Path $eventsPath -Cursor $legacyCursor
    Add-Check 'valid legacy integer cursor migrates at the requested LF' (
        $legacyState.status -ceq 'LEGACY' -and
        @($legacyDelta.rows).Count -eq 1 -and
        [int]$legacyDelta.rows[0].row -eq 3
    )

    $invalidStateDocuments = @(
        '{"cursor":null}',
        '{"line_count":true}',
        '{"line_count":1.5}',
        '{"line_count":2147483648}'
    )
    $allInvalidStatesBlocked = $true
    foreach ($document in $invalidStateDocuments) {
        [System.IO.File]::WriteAllText($statePath, $document, $encoding)
        if ((Read-BridgeIncrementalState -StatePath $statePath).status -cne 'BLOCKED') {
            $allInvalidStatesBlocked = $false
        }
    }
    Add-Check 'null and non-exact legacy cursors fail closed' `
        $allInvalidStatesBlocked

    Remove-Item -LiteralPath $statePath -Force
    [void](New-Item -ItemType Directory -Path $statePath)
    $nonLeafState = Read-BridgeIncrementalState -StatePath $statePath
    Add-Check 'non-leaf cursor state fails closed instead of baselining' (
        $nonLeafState.status -ceq 'BLOCKED' -and
        $nonLeafState.reason -ceq 'cursor_state_invalid'
    )
    $nonLeafWriteBlocked = $false
    try {
        Write-BridgeIncrementalState -StatePath $statePath `
            -Cursor $exactResult.candidate_cursor
    } catch {
        $nonLeafWriteBlocked = $true
    }
    $nonLeafChildren = @(Get-ChildItem -LiteralPath $statePath -Force)
    Add-Check 'writer refuses a non-leaf cursor state without temp children' (
        $nonLeafWriteBlocked -and
        (Test-Path -LiteralPath $statePath -PathType Container) -and
        $nonLeafChildren.Count -eq 0
    )
    Remove-Item -LiteralPath $statePath -Recurse -Force

    Rename-Item -LiteralPath Function:\Commit-BridgeIncrementalStateFile `
        -NewName Commit-BridgeIncrementalStateFileOriginal -ErrorAction Stop
    function Commit-BridgeIncrementalStateFile {
        param(
            [Parameter(Mandatory)] [string] $TemporaryPath,
            [Parameter(Mandatory)] [string] $StatePath,
            [Parameter(Mandatory)] [bool] $StateExisted,
            [Parameter(Mandatory)] [string] $BackupPath
        )

        [void](New-Item -ItemType Directory -Path $StatePath -ErrorAction Stop)
        Commit-BridgeIncrementalStateFileOriginal `
            -TemporaryPath $TemporaryPath -StatePath $StatePath `
            -StateExisted $StateExisted -BackupPath $BackupPath
    }
    $racedWriteBlocked = $false
    try {
        Write-BridgeIncrementalState -StatePath $statePath `
            -Cursor $exactResult.candidate_cursor
    } catch {
        $racedWriteBlocked = $true
    } finally {
        Remove-Item -LiteralPath Function:\Commit-BridgeIncrementalStateFile `
            -Force -ErrorAction SilentlyContinue
        Rename-Item `
            -LiteralPath Function:\Commit-BridgeIncrementalStateFileOriginal `
            -NewName Commit-BridgeIncrementalStateFile -ErrorAction Stop
    }
    $racedChildren = @(Get-ChildItem -LiteralPath $statePath -Force)
    $racedSiblingLeaks = @(Get-ChildItem -LiteralPath $tempRoot -Force |
        Where-Object {
            $_.Name -like 'reader.cursor.json.tmp.*' -or
            $_.Name -like 'reader.cursor.json.backup.*'
        })
    Add-Check 'writer destination-swap race is leaf-exact and leak-free' (
        $racedWriteBlocked -and
        (Test-Path -LiteralPath $statePath -PathType Container) -and
        $racedChildren.Count -eq 0 -and
        $racedSiblingLeaks.Count -eq 0
    )
    Remove-Item -LiteralPath $statePath -Recurse -Force

    $exactBoundaryPrefix = '{"line_count":0}'
    $exactBoundaryState = $exactBoundaryPrefix +
        (' ' * (65536 - $encoding.GetByteCount($exactBoundaryPrefix)))
    [System.IO.File]::WriteAllText($statePath, $exactBoundaryState, $encoding)
    $exactBoundaryResult = Read-BridgeIncrementalState -StatePath $statePath
    Add-Check 'cursor state accepts an exact 64 KiB valid document' (
        $exactBoundaryResult.status -ceq 'LEGACY' -and
        [int64]$exactBoundaryResult.line_count -eq 0
    )

    [System.IO.File]::WriteAllBytes(
        $statePath,
        (New-Object byte[] 65537)
    )
    $oversizedState = Read-BridgeIncrementalState -StatePath $statePath
    Add-Check 'cursor state read is bounded at 64 KiB plus one byte' (
        $oversizedState.status -ceq 'BLOCKED' -and
        $oversizedState.reason -ceq 'cursor_state_invalid'
    )

    [System.IO.File]::WriteAllText($statePath, '{"line_count":0}', $encoding)
    $stateLock = [System.IO.File]::Open(
        $statePath,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::None
    )
    try {
        $unavailableState = Read-BridgeIncrementalState -StatePath $statePath
    } finally {
        $stateLock.Dispose()
    }
    Add-Check 'unavailable cursor state fails closed instead of baselining' (
        $unavailableState.status -ceq 'BLOCKED' -and
        $unavailableState.reason -ceq 'cursor_state_invalid'
    )

    [System.IO.File]::WriteAllText(
        $eventsPath, ('{"row":1}' + [char]10), $encoding
    )
    $shortLegacyBlocked = $false
    try {
        [void](Resolve-BridgeCursorForLineCount -Path $eventsPath -LineCount 2)
    } catch {
        $shortLegacyBlocked = $true
    }
    [System.IO.File]::WriteAllBytes($eventsPath, [byte[]]@())
    $emptyLegacyBlocked = $false
    try {
        [void](Resolve-BridgeCursorForLineCount -Path $eventsPath -LineCount 1)
    } catch {
        $emptyLegacyBlocked = $true
    }
    Add-Check 'legacy migration requires the exact requested LF row' (
        $shortLegacyBlocked -and $emptyLegacyBlocked
    )

    [System.IO.File]::WriteAllText(
        $statePath, '{"line_count":100001}', $encoding
    )
    $overCapState = Read-BridgeIncrementalState -StatePath $statePath
    $overCapResolverBlocked = $false
    try {
        [void](Resolve-BridgeCursorForLineCount -Path $eventsPath `
            -LineCount 100001)
    } catch {
        $overCapResolverBlocked = $true
    }
    Add-Check 'legacy migration work is capped at 100000 rows' (
        $overCapState.status -ceq 'BLOCKED' -and $overCapResolverBlocked
    )

    $passed = @($results | Where-Object { $_.passed }).Count
    $total = $results.Count
    Write-Host ''
    Write-Host ("Result: {0}/{1} checks passed" -f $passed, $total)
    if ($passed -ne $total) { exit 1 }
    exit 0
} finally {
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
