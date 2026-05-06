# ArtifactValidator.ps1
# Verifies that an iteration's artifact set is internally consistent and present.
# Detector + ArtifactValidator together drive the auto-proceed decision; the
# detector alone never authorises COMPLETED.

Set-StrictMode -Version Latest

function Test-IsValidJsonFile {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [string] $Path)
    if (-not (Test-Path $Path)) { return $false }
    try { [void](Get-Content -Raw -Path $Path | ConvertFrom-Json); return $true }
    catch { return $false }
}

function Read-IterationSignal {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [string] $Path)
    if (-not (Test-Path $Path)) { return $null }
    try { return (Get-Content -Raw -Path $Path | ConvertFrom-Json) }
    catch { return $null }
}

function Test-IterationArtifacts {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $IterationFolder,
        [Parameter(Mandatory)] [string] $IterationId,
        [ValidateSet('print','interactiveTranscriptFallback')] [string] $ExecutionMode = 'print',
        [bool]   $RequireReport = $false,
        [bool]   $RequirePackage = $true,
        [object] $RunStartedUtc = $null,
        [object] $RunEndedUtc = $null
    )

    $script:checks  = @()
    $script:errors  = @()
    $script:warnings = @()

    function _add([string]$name, [bool]$ok, [string]$detail, [bool]$isWarning = $false) {
        $script:checks += [pscustomobject]@{ name = $name; ok = $ok; detail = $detail }
        if (-not $ok) {
            if ($isWarning) { $script:warnings += "${name}: $detail" }
            else            { $script:errors   += "${name}: $detail" }
        }
    }

    $stateP = Join-Path $IterationFolder 'state.json'
    _add 'state_json' (Test-IsValidJsonFile -Path $stateP) "expected: $stateP"

    if ($ExecutionMode -eq 'print') {
        $rmP = Join-Path $IterationFolder 'run_metadata.json'
        _add 'run_metadata_json' (Test-IsValidJsonFile -Path $rmP) "expected: $rmP"
        _add 'stdout_present' (Test-Path (Join-Path $IterationFolder 'claude_stdout.txt')) 'claude_stdout.txt missing'
        _add 'stderr_present' (Test-Path (Join-Path $IterationFolder 'claude_stderr.txt')) 'claude_stderr.txt missing'
    } else {
        _add 'tail_present' (Test-Path (Join-Path $IterationFolder 'powershell_tail.txt')) 'powershell_tail.txt missing'
    }

    $rep = Join-Path $IterationFolder 'raportti.md'
    if (Test-Path $rep) {
        _add 'report_present' $true $rep
    } else {
        _add 'report_present' (-not $RequireReport) "$rep missing" (-not $RequireReport)
    }

    $pkg = Join-Path $IterationFolder 'llm_input_package.md'
    $rpt = Join-Path $IterationFolder 'redaction_report.json'
    if ($RequirePackage) {
        _add 'package_present' (Test-Path $pkg) "$pkg missing"
        _add 'redaction_report_present' (Test-Path $rpt) "$rpt missing"
        if (Test-Path $pkg) {
            $pkgText = Get-Content -Raw -Path $pkg
            $hasUntrustedMarker = ($pkgText -match 'UNTRUSTED DATA' -or $pkgText -match 'untrusted')
            _add 'package_has_untrusted_marker' $hasUntrustedMarker 'no UNTRUSTED DATA marker found' (-not $hasUntrustedMarker)
        }
    }

    $sigDir = Join-Path $IterationFolder 'signals'
    $cP = Join-Path $sigDir 'claude_completed.json'
    $fP = Join-Path $sigDir 'claude_failed.json'

    $cExists = Test-Path $cP
    $fExists = Test-Path $fP

    if ($cExists -and $fExists) {
        _add 'signals_no_conflict' $false 'both completed and failed signals are present'
    }

    if ($cExists) {
        $sig = Read-IterationSignal -Path $cP
        if ($null -eq $sig) {
            _add 'completed_signal_valid_json' $false 'claude_completed.json is not valid JSON'
        } else {
            $hasIter = ($sig.PSObject.Properties.Name -contains 'iteration_id')
            if (-not $hasIter) {
                _add 'completed_signal_iteration_id' $false 'completed signal has no iteration_id'
            } elseif ([string]$sig.iteration_id -ne $IterationId) {
                _add 'completed_signal_iteration_id' $false "iteration_id mismatch: signal=$($sig.iteration_id) expected=$IterationId"
            } else {
                _add 'completed_signal_iteration_id' $true 'matches'
            }

            if ($null -ne $RunStartedUtc -and $sig.PSObject.Properties.Name -contains 'completed_at') {
                $ts = $null
                $tsParsed = $true
                try { $ts = [datetime]::Parse([string]$sig.completed_at) } catch { $tsParsed = $false }
                if (-not $tsParsed) {
                    _add 'completed_signal_timestamp' $false 'completed_at is not parseable'
                } else {
                    $tsUtc = $ts.ToUniversalTime()
                    if ($null -ne $RunEndedUtc) { $endBound = ([datetime]$RunEndedUtc).AddHours(24) }
                    else { $endBound = (Get-Date).ToUniversalTime().AddHours(24) }
                    $startBound = ([datetime]$RunStartedUtc).AddHours(-24)
                    if ($tsUtc -lt $startBound -or $tsUtc -gt $endBound) {
                        _add 'completed_signal_timestamp' $false "timestamp >24h outside run window: $tsUtc (run $startBound..$endBound)" $true
                    } else {
                        _add 'completed_signal_timestamp' $true 'within 24h window'
                    }
                }
            }
        }
    }

    if ($fExists) {
        $sig = Read-IterationSignal -Path $fP
        if ($null -eq $sig) {
            _add 'failed_signal_valid_json' $false 'claude_failed.json is not valid JSON'
        }
    }

    return [pscustomobject]@{
        ok       = ($script:errors.Count -eq 0)
        checks   = $script:checks
        errors   = $script:errors
        warnings = $script:warnings
    }
}

function Test-UniqueIterationArtifact {
    <#
    .SYNOPSIS
    Phase 2A-1 P3: validate a per-iteration unique artifact written by
    Claude. The path embeds the iteration_id, the content embeds the
    iteration_id, and the file is required to be fresh (mtime >= run start),
    UTF-8 readable, free of NUL padding, and within a small size band.
    A stale identically-named file from a previous iteration cannot pass.

    .PARAMETER ExpectedAbsolutePath
    Absolute path the artifact must live at.
    .PARAMETER ExpectedContent
    Exact content the file must contain. Trailing newline tolerance is
    allowed (we strip a single CRLF / LF / CR from the actual file's tail
    before comparing).
    .PARAMETER RunStartedUtc
    [datetime] when the iteration started. The file mtime must be at or
    after this minus one second of clock skew tolerance.
    .PARAMETER MaxBytes
    Hard upper bound on the file size; rejects accidental large writes.
    .PARAMETER MinBytes
    Lower bound (default 1 byte; empty file is failure).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $ExpectedAbsolutePath,
        [Parameter(Mandatory)] [string] $ExpectedContent,
        [Parameter(Mandatory)] [datetime] $RunStartedUtc,
        [int] $MaxBytes = 4096,
        [int] $MinBytes = 1
    )

    $checks = @()
    $errors = @()

    function _add([string]$name, [bool]$ok, [string]$detail) {
        $script:_uia_checks += [pscustomobject]@{ name = $name; ok = $ok; detail = $detail }
        if (-not $ok) { $script:_uia_errors += "${name}: $detail" }
    }

    $script:_uia_checks = @()
    $script:_uia_errors = @()

    if (-not (Test-Path -LiteralPath $ExpectedAbsolutePath)) {
        _add 'unique_artifact_present' $false "expected file missing: $ExpectedAbsolutePath"
        return [pscustomobject]@{
            ok = $false; checks = $script:_uia_checks; errors = $script:_uia_errors
        }
    }
    _add 'unique_artifact_present' $true $ExpectedAbsolutePath

    $fi = Get-Item -LiteralPath $ExpectedAbsolutePath
    $size = [int64]$fi.Length
    _add 'unique_artifact_size_min' ($size -ge $MinBytes) "file size $size < min $MinBytes"
    _add 'unique_artifact_size_max' ($size -le $MaxBytes) "file size $size > max $MaxBytes"

    $mtimeUtc = $fi.LastWriteTimeUtc
    $skewWindowStart = $RunStartedUtc.AddSeconds(-1)
    $isFresh = ($mtimeUtc -ge $skewWindowStart)
    _add 'unique_artifact_fresh' $isFresh "mtime $mtimeUtc < run start $RunStartedUtc"

    $bytes = [System.IO.File]::ReadAllBytes($ExpectedAbsolutePath)
    $hasNul = $false
    foreach ($b in $bytes) { if ($b -eq 0) { $hasNul = $true; break } }
    _add 'unique_artifact_no_nul' (-not $hasNul) 'file contains NUL byte'

    $text = $null
    $utf8Ok = $true
    try {
        $strict = New-Object System.Text.UTF8Encoding($false, $true)
        $text = $strict.GetString($bytes)
    } catch { $utf8Ok = $false }
    _add 'unique_artifact_utf8' $utf8Ok 'file is not strict UTF-8'

    if ($null -ne $text) {
        # Strip a single trailing CRLF/LF/CR and an optional leading UTF-8
        # BOM (U+FEFF). Then use ordinal .Equals(): PowerShell's `-eq`
        # culture-folds U+FEFF to nothing and gives false positives on any
        # text whose only difference is a stray BOM, so we cannot rely on
        # `-eq` for exact-content checks.
        $normActual = $text
        if ($normActual.Length -gt 0 -and [int][char]$normActual[0] -eq 0xFEFF) {
            $normActual = $normActual.Substring(1)
        }
        $normActual = ($normActual -replace "(`r`n|`n|`r)$", '')
        $normExpect = ($ExpectedContent -replace "(`r`n|`n|`r)$", '')
        $contentOk = [string]::Equals($normActual, $normExpect, [System.StringComparison]::Ordinal)
        _add 'unique_artifact_content_exact' $contentOk `
            "content mismatch (length actual=$($normActual.Length) expected=$($normExpect.Length))"
    }

    return [pscustomobject]@{
        ok       = ($script:_uia_errors.Count -eq 0)
        checks   = $script:_uia_checks
        errors   = $script:_uia_errors
    }
}
