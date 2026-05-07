# EvidenceBundler.ps1
#
# Phase 2B P6: helpers for building cumulative epoch evidence.
# Pure functions where possible. The CLI entry point is
# orchestrator/Build-WaggleEpochEvidence.ps1.

$ErrorActionPreference = 'Stop'

function Get-WaggleNoWorkClassification {
    <#
    .SYNOPSIS
    Phase 2B P6. Classify whether an iteration was 'no work' --
    empty raportti, near-empty stdout, no diff. Inputs are pure
    file-byte checks; logic is shared with Test-EpochCycleTrigger.

    Returns @{ no_work = <bool>; reason = <string> }.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $IterationFolder,
        [int] $DiffMinBytes = 1,
        [int] $RaporttiMinBytes = 1,
        [int] $StdoutMinMeaningfulBytes = 100,
        [string] $RaporttiPath = ''
    )
    function _szOr0($p) {
        if (-not $p) { return 0 }
        if (-not (Test-Path -LiteralPath $p)) { return 0 }
        try { return [int64](Get-Item -LiteralPath $p).Length } catch { return 0 }
    }
    $stdout = _szOr0 (Join-Path $IterationFolder 'claude_stdout.txt')
    $stderr = _szOr0 (Join-Path $IterationFolder 'claude_stderr.txt')
    if (-not $RaporttiPath) {
        # Default raportti is at projectRoot; fall back to a per-iter
        # raportti.md if the iter has its own.
        $RaporttiPath = Join-Path $IterationFolder 'raportti.md'
    }
    $raportti = _szOr0 $RaporttiPath
    # Use git_metadata.json as a cheap proxy for diff existence; the
    # caller does the real diff.
    $gitMetaSize = _szOr0 (Join-Path $IterationFolder 'git_metadata.json')

    $stdoutLow   = ($stdout    -lt $StdoutMinMeaningfulBytes)
    $raporttiLow = ($raportti  -lt $RaporttiMinBytes)
    $diffLow     = ($gitMetaSize -lt $DiffMinBytes)

    $noWork = ($stdoutLow -and $raporttiLow -and $diffLow)
    $reason = if ($noWork) {
        "stdout=$stdout < $StdoutMinMeaningfulBytes AND raportti=$raportti < $RaporttiMinBytes AND git_metadata=$gitMetaSize < $DiffMinBytes"
    } else {
        ''
    }
    return [pscustomobject]@{ no_work = $noWork; reason = $reason }
}

function Get-WaggleIterationInternalReviewVerdicts {
    <#
    .SYNOPSIS
    Phase 2B P6. Read internal-review JSONs from
    iterations/<id>/reviews/{architect,security,reliability}.json
    and return their verdicts. Missing review -> 'unknown'.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)] [string] $IterationFolder)
    $rDir = Join-Path $IterationFolder 'reviews'
    $out = [ordered]@{
        architect   = 'unknown'
        security    = 'unknown'
        reliability = 'unknown'
    }
    foreach ($role in 'architect','security','reliability') {
        $jp = Join-Path $rDir ($role + '.json')
        if (Test-Path -LiteralPath $jp) {
            try {
                $j = Get-Content -Raw -Path $jp -Encoding UTF8 | ConvertFrom-Json
                if ($j.PSObject.Properties['verdict']) {
                    $out[$role] = [string]$j.verdict
                }
            } catch {}
        }
    }
    return ([pscustomobject]$out)
}

function Get-WaggleCanonicalEvidenceSha {
    <#
    .SYNOPSIS
    Phase 2B P6. Deterministic content hash over a list of files.
    Files are sorted by their RELATIVE path (forward-slashed,
    case-insensitive ordinal). Each file contributes:
      "<rel_path>\n<sha256_of_bytes>\n"
    The concatenation is then hashed once more to produce the
    final evidence_sha256. Two runs over the same input produce
    the same output.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $RootPath,
        [Parameter(Mandatory)] [string[]] $RelativePaths
    )
    $rootFull = (Resolve-Path -LiteralPath $RootPath).Path
    $sb = New-Object System.Text.StringBuilder
    $sorted = @($RelativePaths | ForEach-Object { ($_ -replace '\\','/') } | Sort-Object -Unique)
    foreach ($rel in $sorted) {
        $abs = Join-Path $rootFull $rel
        if (-not (Test-Path -LiteralPath $abs)) { continue }
        $h = Get-FileHash -Algorithm SHA256 -LiteralPath $abs
        [void]$sb.Append($rel + "`n" + $h.Hash.ToLowerInvariant() + "`n")
    }
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($sb.ToString())
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $hash = $sha.ComputeHash($bytes)
    $sha.Dispose()
    return -join ($hash | ForEach-Object { $_.ToString('x2') })
}

function _Eb-FenceFor {
    <#
    .SYNOPSIS
    Pick a backtick fence longer than the longest backtick run in
    the body. Same idea as Phase 2A-4 ReviewSurface dynamic fence.
    #>
    param([Parameter(Mandatory)] [AllowEmptyString()] [string] $Body)
    $longest = 0
    if ($Body) {
        $matches = [regex]::Matches($Body, '`+')
        foreach ($m in $matches) {
            if ($m.Length -gt $longest) { $longest = $m.Length }
        }
    }
    $needed = [Math]::Max(3, $longest + 1)
    return ('`' * $needed)
}

function Get-WaggleAttachmentPlanForProvider {
    <#
    .SYNOPSIS
    Phase 2B P6. Given an evidence dir and a provider's
    max_attachments cap, return the deterministic per-provider
    attachment plan. Files are listed in priority order (manifest,
    diff, raportti, supplement, per-iteration logs, internal
    reviews). If the cap would be exceeded, lower-priority items
    are consolidated into combined files; if even after
    consolidation the cap is exceeded, return ok=false.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $EvidenceDir,
        [int] $MaxAttachments = 20
    )
    if (-not (Test-Path -LiteralPath $EvidenceDir)) {
        return [pscustomobject]@{
            ok = $false
            attachments = @()
            errors = @("evidence dir does not exist: $EvidenceDir")
        }
    }
    $top = @(
        'epoch_evidence.json',
        'cumulative_diff.patch',
        'cumulative_raportti.md',
        'cumulative_supplement.md',
        'previous_epoch_synthesis.md',
        'regression_state.json'
    )
    $picks = New-Object System.Collections.Generic.List[string]
    foreach ($f in $top) {
        $abs = Join-Path $EvidenceDir $f
        if (Test-Path -LiteralPath $abs) { [void]$picks.Add($f) }
    }
    # Per-iteration logs and internal reviews
    $iterFiles = @(Get-ChildItem -LiteralPath $EvidenceDir -Filter 'iter*_*' -File -ErrorAction SilentlyContinue |
        Sort-Object Name | ForEach-Object { $_.Name })
    foreach ($f in $iterFiles) { [void]$picks.Add($f) }

    if ($picks.Count -le $MaxAttachments) {
        return [pscustomobject]@{
            ok = $true
            attachments = $picks.ToArray()
            consolidated = $false
            errors = @()
        }
    }

    # Consolidate iter*_logs into a single run_logs_combined.md
    # and iter*_internal_review into internal_reviews_combined.md.
    $kept = New-Object System.Collections.Generic.List[string]
    foreach ($f in $top) {
        $abs = Join-Path $EvidenceDir $f
        if (Test-Path -LiteralPath $abs) { [void]$kept.Add($f) }
    }
    # Consolidate logs
    $logs = @($iterFiles | Where-Object { $_ -match '_logs_combined\.md$' })
    if ($logs.Count -gt 0) {
        $combined = Join-Path $EvidenceDir 'run_logs_combined.md'
        $sb = New-Object System.Text.StringBuilder
        foreach ($lf in $logs) {
            $body = Get-Content -Raw -Path (Join-Path $EvidenceDir $lf) -Encoding UTF8
            $fence = _Eb-FenceFor -Body $body
            [void]$sb.AppendLine('## ' + $lf)
            [void]$sb.AppendLine($fence + 'text')
            [void]$sb.AppendLine($body)
            [void]$sb.AppendLine($fence)
            [void]$sb.AppendLine('')
        }
        Set-Content -Path $combined -Value $sb.ToString() -Encoding UTF8
        [void]$kept.Add('run_logs_combined.md')
    }
    # Consolidate internal reviews
    $reviews = @($iterFiles | Where-Object { $_ -match '_internal_review\.md$' })
    if ($reviews.Count -gt 0) {
        $combined = Join-Path $EvidenceDir 'internal_reviews_combined.md'
        $sb = New-Object System.Text.StringBuilder
        foreach ($rf in $reviews) {
            $body = Get-Content -Raw -Path (Join-Path $EvidenceDir $rf) -Encoding UTF8
            $fence = _Eb-FenceFor -Body $body
            [void]$sb.AppendLine('## ' + $rf)
            [void]$sb.AppendLine($fence + 'text')
            [void]$sb.AppendLine($body)
            [void]$sb.AppendLine($fence)
            [void]$sb.AppendLine('')
        }
        Set-Content -Path $combined -Value $sb.ToString() -Encoding UTF8
        [void]$kept.Add('internal_reviews_combined.md')
    }
    if ($kept.Count -le $MaxAttachments) {
        return [pscustomobject]@{
            ok = $true
            attachments = $kept.ToArray()
            consolidated = $true
            errors = @()
        }
    }
    return [pscustomobject]@{
        ok = $false
        attachments = $kept.ToArray()
        consolidated = $true
        errors = @("attachment plan ($($kept.Count)) exceeds cap ($MaxAttachments) even after consolidation")
    }
}
