#requires -Version 5.1
[CmdletBinding()]
param(
    [string] $CorpusPath = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'BridgeLogReader.ps1')

if (-not $CorpusPath) {
    $repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    $CorpusPath = Join-Path $repoRoot 'tests\fixtures\bridge_snapshot_delta_v1.json'
}

$corpus = Get-Content -LiteralPath $CorpusPath -Raw -Encoding UTF8 |
    ConvertFrom-Json -ErrorAction Stop
if ([int]$corpus.schema_version -ne 1) {
    throw 'unsupported bridge snapshot/delta corpus version'
}
$requiredAdversarialCases = @(
    'non_finite_overflow_blocks',
    'safe_integer_positive_boundary_is_accepted',
    'safe_integer_negative_boundary_is_accepted',
    'positive_integer_just_outside_blocks',
    'negative_integer_just_outside_blocks',
    'nested_unsafe_integer_blocks',
    'hundred_digit_integer_blocks',
    'exact_duplicate_keys_block',
    'case_colliding_keys_block',
    'single_non_ascii_key_blocks',
    'nested_non_ascii_keys_block',
    'escaped_high_surrogate_blocks',
    'escaped_low_surrogate_blocks',
    'escaped_surrogate_pair_blocks',
    'literal_escaped_backslash_is_accepted',
    'direct_supplementary_utf8_is_accepted',
    'nesting_depth_32_is_accepted',
    'nesting_depth_33_blocks'
)
$caseNames = @($corpus.cases | ForEach-Object { [string]$_.name })
$declaredRequiredCases = @(
    $corpus.required_adversarial_cases | ForEach-Object { [string]$_ }
)
if (@($caseNames | Select-Object -Unique).Count -ne $caseNames.Count) {
    throw 'bridge snapshot/delta corpus case names must be unique'
}
if (@($declaredRequiredCases | Select-Object -Unique).Count -ne $declaredRequiredCases.Count) {
    throw 'bridge snapshot/delta required case names must be unique'
}
$requiredDifference = @(
    Compare-Object -ReferenceObject @($requiredAdversarialCases | Sort-Object) `
        -DifferenceObject @($declaredRequiredCases | Sort-Object) -CaseSensitive
)
if ($requiredDifference.Count -ne 0) {
    throw 'bridge snapshot/delta required adversarial case declaration changed'
}
foreach ($requiredName in $requiredAdversarialCases) {
    if (@($caseNames | Where-Object { $_ -ceq $requiredName }).Count -ne 1) {
        throw "required bridge adversarial case missing or duplicated: $requiredName"
    }
}

$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    'bridge-reader-c0-' + [guid]::NewGuid().ToString('N')
)
$passed = 0
$total = 0
$executedCaseNames = New-Object System.Collections.Generic.List[string]

function Assert-BridgeConformance {
    param(
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [bool] $Condition,
        [string] $Detail = ''
    )
    $script:total++
    if (-not $Condition) {
        throw "conformance failed: $Name $Detail"
    }
    $script:passed++
}

function Get-ActualIdentity {
    param([Parameter(Mandatory)] [string] $Path)
    $stream = Open-BridgeLogReadStream -Path $Path
    try {
        return Get-BridgeLogFileIdentity -Stream $stream
    } finally {
        $stream.Dispose()
    }
}

try {
    [void](New-Item -ItemType Directory -Path $tempRoot)
    foreach ($case in @($corpus.cases)) {
        [void]$executedCaseNames.Add([string]$case.name)
        $caseRoot = Join-Path $tempRoot ([string]$case.name)
        [void](New-Item -ItemType Directory -Path $caseRoot)
        $eventsPath = Join-Path $caseRoot 'events.jsonl'
        [System.IO.File]::WriteAllBytes(
            $eventsPath,
            [Convert]::FromBase64String([string]$case.data_b64)
        )

        $generationPath = ''
        if ($null -ne $case.PSObject.Properties['generation_document_b64']) {
            $generationPath = Join-Path $caseRoot 'events.generation.json'
            [System.IO.File]::WriteAllBytes(
                $generationPath,
                [Convert]::FromBase64String([string]$case.generation_document_b64)
            )
        }

        $cursor = $null
        if ($null -ne $case.PSObject.Properties['cursor_offset']) {
            $identity = if ([string]$case.cursor_identity -ceq 'actual') {
                Get-ActualIdentity -Path $eventsPath
            } else {
                'wrong-v1:identity'
            }
            $cursorGeneration = if ($null -ne $case.PSObject.Properties['cursor_generation']) {
                [string]$case.cursor_generation
            } else {
                $null
            }
            $cursor = [pscustomobject]@{
                offset = [int64]$case.cursor_offset
                file_identity = $identity
                generation = $cursorGeneration
            }
        }

        $result = Read-BridgeLogSnapshotDelta -Path $eventsPath -Cursor $cursor -MaxBytes ([int64]$case.max_bytes) -GenerationPath $generationPath
        $expected = $case.expected
        Assert-BridgeConformance -Name "$($case.name):status" -Condition ($result.status -ceq [string]$expected.status) -Detail "actual=$($result.status)"
        Assert-BridgeConformance -Name "$($case.name):reason" -Condition ($result.reason -ceq [string]$expected.reason) -Detail "actual=$($result.reason)"

        $actualRowsJson = [pscustomobject]@{ rows = @($result.rows) } |
            ConvertTo-Json -Depth 100 -Compress
        $expectedRowsJson = [pscustomobject]@{ rows = @($expected.rows) } |
            ConvertTo-Json -Depth 100 -Compress
        Assert-BridgeConformance -Name "$($case.name):rows" -Condition ($actualRowsJson -ceq $expectedRowsJson) -Detail "actual=$actualRowsJson expected=$expectedRowsJson"
        Assert-BridgeConformance -Name "$($case.name):bytes_read" -Condition ([int64]$result.bytes_read -eq [int64]$expected.bytes_read) -Detail "actual=$($result.bytes_read)"
        Assert-BridgeConformance -Name "$($case.name):bytes_consumed" -Condition ([int64]$result.bytes_consumed -eq [int64]$expected.bytes_consumed) -Detail "actual=$($result.bytes_consumed)"

        $actualCandidateOffset = if ($null -eq $result.candidate_cursor) {
            $null
        } else {
            [int64]$result.candidate_cursor.offset
        }
        $expectedCandidateOffset = if ($null -eq $expected.candidate_offset) {
            $null
        } else {
            [int64]$expected.candidate_offset
        }
        Assert-BridgeConformance -Name "$($case.name):candidate_offset" -Condition (
            ($null -eq $actualCandidateOffset -and $null -eq $expectedCandidateOffset) -or
            ($null -ne $actualCandidateOffset -and
             $null -ne $expectedCandidateOffset -and
             $actualCandidateOffset -eq $expectedCandidateOffset)
        ) -Detail "actual=$actualCandidateOffset expected=$expectedCandidateOffset"

        if ($null -ne $expected.PSObject.Properties['candidate_generation']) {
            Assert-BridgeConformance -Name "$($case.name):candidate_generation" -Condition (
                $null -ne $result.candidate_cursor -and
                $result.candidate_cursor.generation -ceq [string]$expected.candidate_generation
            )
        } elseif ($null -ne $result.candidate_cursor) {
            Assert-BridgeConformance -Name "$($case.name):candidate_generation_null" -Condition (
                $null -eq $result.candidate_cursor.generation
            )
        }
        if ($result.status -in @('RETRY', 'BLOCKED')) {
            Assert-BridgeConformance -Name "$($case.name):failure_cursor_neutral" -Condition (
                $null -eq $result.candidate_cursor -and @($result.rows).Count -eq 0
            )
        }
    }
    foreach ($requiredName in $requiredAdversarialCases) {
        Assert-BridgeConformance -Name "anti_vacuity:$requiredName" -Condition (
            @($executedCaseNames | Where-Object { $_ -ceq $requiredName }).Count -eq 1
        )
    }

    $largePath = Join-Path $tempRoot 'large-events.jsonl'
    $encoding = New-Object System.Text.UTF8Encoding($false)
    $history = $encoding.GetBytes('{"history":true}' + [char]10)
    $writer = [System.IO.File]::Open(
        $largePath,
        [System.IO.FileMode]::Create,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::ReadWrite
    )
    try {
        while ($writer.Length -lt 8MB) {
            $writer.Write($history, 0, $history.Length)
        }
    } finally {
        $writer.Dispose()
    }
    $largeOffset = [int64](Get-Item -LiteralPath $largePath).Length
    $largeCursor = [pscustomobject]@{
        offset = $largeOffset
        file_identity = Get-ActualIdentity -Path $largePath
        generation = $null
    }
    $delta = $encoding.GetBytes('{"task_id":"delta"}' + [char]10)
    $writer = [System.IO.File]::Open(
        $largePath,
        [System.IO.FileMode]::Append,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::ReadWrite
    )
    try {
        $writer.Write($delta, 0, $delta.Length)
    } finally {
        $writer.Dispose()
    }
    $largeResult = Read-BridgeLogSnapshotDelta -Path $largePath -Cursor $largeCursor -MaxBytes 4096
    Assert-BridgeConformance -Name 'large_prefix_o_delta_status' -Condition ($largeResult.status -ceq 'OK')
    Assert-BridgeConformance -Name 'large_prefix_o_delta_bytes' -Condition (
        [int64]$largeResult.bytes_read -eq ([int64]$delta.Length + 1) -and
        [int64]$largeResult.requested_offset -eq $largeOffset -and
        [int]$largeResult.read_calls -ge 2
    ) -Detail "history=$largeOffset bytes_read=$($largeResult.bytes_read)"
    $roundTrip = Read-BridgeLogSnapshotDelta -Path $largePath -Cursor $largeResult.candidate_cursor -MaxBytes 4096
    Assert-BridgeConformance -Name 'candidate_cursor_round_trip' -Condition (
        $roundTrip.status -ceq 'IDLE' -and
        $roundTrip.reason -ceq 'partial_record' -and
        $null -ne $roundTrip.candidate_cursor -and
        [int64]$roundTrip.candidate_cursor.offset -eq [int64]$largeResult.candidate_cursor.offset -and
        $null -eq $roundTrip.candidate_cursor.generation -and
        [int64]$roundTrip.bytes_read -eq 1
    )

    $boundedRowsPath = Join-Path $tempRoot 'bounded-rows.jsonl'
    [System.IO.File]::WriteAllBytes(
        $boundedRowsPath,
        $encoding.GetBytes(
            '{"row":1}' + [char]10 +
            '{"row":2}' + [char]10 +
            '{"row":3}' + [char]10
        )
    )
    $boundedFirst = Read-BridgeLogSnapshotDelta -Path $boundedRowsPath -MaxRows 2
    $boundedSecond = Read-BridgeLogSnapshotDelta -Path $boundedRowsPath `
        -Cursor $boundedFirst.candidate_cursor -MaxRows 2
    Assert-BridgeConformance -Name 'max_rows_bounds_first_batch' -Condition (
        $boundedFirst.status -ceq 'OK' -and
        @($boundedFirst.rows).Count -eq 2 -and
        [int]$boundedFirst.rows[0].row -eq 1 -and
        [int]$boundedFirst.rows[1].row -eq 2
    )
    Assert-BridgeConformance -Name 'max_rows_continues_exactly_once' -Condition (
        $boundedSecond.status -ceq 'OK' -and
        @($boundedSecond.rows).Count -eq 1 -and
        [int]$boundedSecond.rows[0].row -eq 3
    )

    $identityPath = Join-Path $tempRoot 'changing-identity.jsonl'
    [System.IO.File]::WriteAllBytes(
        $identityPath,
        $encoding.GetBytes('{"identity":true}' + [char]10)
    )
    $originalIdentityFunction = ${function:Get-BridgeLogFileIdentity}
    $script:identityReadCount = 0
    try {
        function Get-BridgeLogFileIdentity {
            param([Parameter(Mandatory)] [System.IO.FileStream] $Stream)
            $script:identityReadCount++
            $captured = & $originalIdentityFunction -Stream $Stream
            if ($script:identityReadCount -eq 1) { return $captured }
            return $captured + '-changed'
        }
        $identityResult = Read-BridgeLogSnapshotDelta -Path $identityPath
    } finally {
        Set-Item -LiteralPath Function:\Get-BridgeLogFileIdentity -Value $originalIdentityFunction
    }
    Assert-BridgeConformance -Name 'identity_before_after_checked' -Condition (
        $script:identityReadCount -eq 2 -and
        $identityResult.status -ceq 'RETRY' -and
        $identityResult.reason -ceq 'file_identity_changed_during_read' -and
        $null -eq $identityResult.candidate_cursor -and
        @($identityResult.rows).Count -eq 0
    ) -Detail "reads=$script:identityReadCount status=$($identityResult.status)"

    $changingPath = Join-Path $tempRoot 'changing-generation.jsonl'
    [System.IO.File]::WriteAllBytes($changingPath, $encoding.GetBytes('[]' + [char]10))
    $script:generationReadCount = 0
    function Read-BridgeGenerationToken {
        param([Parameter(Mandatory)] [string] $Path)
        $script:generationReadCount++
        $token = if ($script:generationReadCount -eq 1) { 'g1' } else { 'g2' }
        return [pscustomobject]@{ status = 'OK'; reason = ''; generation = $token }
    }
    $changingResult = Read-BridgeLogSnapshotDelta -Path $changingPath -GenerationPath 'test-hook'
    Assert-BridgeConformance -Name 'generation_before_after_checked' -Condition (
        $script:generationReadCount -eq 2 -and
        $changingResult.status -ceq 'RETRY' -and
        $changingResult.reason -ceq 'generation_changed_during_read' -and
        $null -eq $changingResult.candidate_cursor -and
        @($changingResult.rows).Count -eq 0
    ) -Detail "reads=$script:generationReadCount status=$($changingResult.status)"

    $continuityRoot = Join-Path $tempRoot 'continuity-task-id-collisions'
    [void](New-Item -ItemType Directory -Path (Join-Path $continuityRoot 'shared'))
    $continuityEvents = @(
        [ordered]@{
            ts_utc = '2026-07-23T00:00:00Z'; agent = 'lead'; type = 'message'
            task_id = 'task-1'; status = 'request'; severity = ''; to = 'codex'
            message = 'selected'; paths = @(); write_scope = @(); run_id = ''
            pid = 0; cwd = ''; payload = @{}
        },
        [ordered]@{
            ts_utc = '2026-07-23T00:00:01Z'; agent = 'other'; type = 'done'
            task_id = 'task-10'; status = 'done'; severity = ''; to = ''
            message = 'prefix collision'; paths = @(); write_scope = @(); run_id = ''
            pid = 0; cwd = ''; payload = @{}
        },
        [ordered]@{
            ts_utc = '2026-07-23T00:00:02Z'; agent = 'other'; type = 'done'
            task_id = 'x-task-1'; status = 'done'; severity = ''; to = ''
            message = 'suffix collision'; paths = @(); write_scope = @(); run_id = ''
            pid = 0; cwd = ''; payload = @{}
        },
        [ordered]@{
            ts_utc = '2026-07-23T00:00:03Z'; agent = 'other'; type = 'done'
            task_id = 'TASK-1'; status = 'done'; severity = ''; to = ''
            message = 'case collision'; paths = @(); write_scope = @(); run_id = ''
            pid = 0; cwd = ''; payload = @{}
        },
        [ordered]@{
            ts_utc = '2026-07-23T00:00:04Z'; agent = 'lead'; type = 'message'
            status = 'request'; to = 'codex'; message = 'addressed without task'
        },
        [ordered]@{
            ts_utc = '2026-07-23T00:00:05Z'; message = 'unaddressed without task'
        }
    )
    $continuityJson = (
        ($continuityEvents | ForEach-Object {
            $_ | ConvertTo-Json -Depth 10 -Compress
        }) -join [char]10
    ) + [char]10 + '{"torn":'
    $continuityPath = Join-Path $continuityRoot 'shared\events.jsonl'
    [System.IO.File]::WriteAllText($continuityPath, $continuityJson, $encoding)

    $hadRuntimeRoot = Test-Path Env:\AGENT_BRIDGE_RUNTIME_ROOT
    $previousRuntimeRoot = $env:AGENT_BRIDGE_RUNTIME_ROOT
    try {
        $env:AGENT_BRIDGE_RUNTIME_ROOT = $continuityRoot
        . (Join-Path $PSScriptRoot 'Read-AgentBridge.ps1') `
            -Agent codex -NoAckReceived -Tail 1 -ContinuityTail 0 *> $null
        $continuityRows = @(
            Read-BridgeContinuityEventObjects -Path $continuityPath `
                -AgentName codex -MaxLines 0
        )
    } finally {
        if ($hadRuntimeRoot) {
            $env:AGENT_BRIDGE_RUNTIME_ROOT = $previousRuntimeRoot
        } else {
            Remove-Item Env:\AGENT_BRIDGE_RUNTIME_ROOT -ErrorAction SilentlyContinue
        }
    }
    $continuityTaskIds = @($continuityRows | ForEach-Object { [string]$_.task_id })
    Assert-BridgeConformance -Name 'continuity_exact_task_id_selected' -Condition (
        $continuityTaskIds -ccontains 'task-1'
    )
    Assert-BridgeConformance -Name 'continuity_task_id_prefix_collision_excluded' -Condition (
        $continuityTaskIds -cnotcontains 'task-10'
    )
    Assert-BridgeConformance -Name 'continuity_task_id_suffix_collision_excluded' -Condition (
        $continuityTaskIds -cnotcontains 'x-task-1'
    )
    Assert-BridgeConformance -Name 'continuity_task_id_case_collision_excluded' -Condition (
        $continuityTaskIds -cnotcontains 'TASK-1'
    )
    $missingTaskRows = @(
        $continuityRows |
            Where-Object { [string]$_.message -ceq 'addressed without task' }
    )
    Assert-BridgeConformance `
        -Name 'continuity_missing_task_id_is_normalized_without_crash' `
        -Condition (
            $missingTaskRows.Count -eq 1 -and
            [string]$missingTaskRows[0].task_id -ceq ''
        )
    $normalizedDisplayRows = @(
        Read-BridgeEventObjects -Path $continuityPath -MaxLines 0 |
            Where-Object { [string]$_.message -ceq 'unaddressed without task' }
    )
    Assert-BridgeConformance `
        -Name 'display_missing_core_fields_are_normalized_without_crash' `
        -Condition (
            $normalizedDisplayRows.Count -eq 1 -and
            [string]$normalizedDisplayRows[0].task_id -ceq '' -and
            [string]$normalizedDisplayRows[0].agent -ceq '' -and
            [string]$normalizedDisplayRows[0].type -ceq '' -and
            [string]$normalizedDisplayRows[0].status -ceq ''
        )

    Write-Output ("Bridge snapshot/delta conformance: {0}/{1} checks passed" -f $passed, $total)
    exit 0
} finally {
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
