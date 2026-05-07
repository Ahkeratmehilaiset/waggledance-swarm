#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2B P6: Build a cumulative epoch evidence bundle from N
    iterations (1..3). Reads each iteration's artifacts, assembles
    cumulative diff / raportti / logs / internal reviews / supplement
    snapshot, computes a deterministic evidence_sha256, and writes
    epoch_evidence.json matching schemas/epoch_evidence.schema.json.
.PARAMETER ConfigPath
    Path to orchestrator.config.json (or equivalent example config).
.PARAMETER EpochId
    Stable identifier for this epoch (default: utc-timestamp-suffixed).
.PARAMETER IterationIds
    Array of iteration IDs to bundle (1..N, in chronological order).
    The LAST id is the "carrier" iteration whose external_reviews/
    folder hosts the bundle output.
.PARAMETER EarlyTriggerReason
    null OR one of regression / hardening_gate_failure /
    internal_critical / no_work_consecutive / session_resume / manual.
.PARAMETER OutputDir
    Override output directory. Default:
    iterations/<last_iteration_id>/external_reviews/epoch_<epoch_id>/evidence/
.PARAMETER PreviousEpochSynthesisPath
    Optional path to the previous epoch's synthesis.md to include for
    trajectory context.
.PARAMETER DryRun
    Build the in-memory record + manifest without writing disk files.
#>
[CmdletBinding()]
param(
    [string]   $ConfigPath = '',
    [string]   $EpochId = '',
    [string[]] $IterationIds = @(),
    [string]   $EarlyTriggerReason = '',
    [string]   $OutputDir = '',
    [string]   $PreviousEpochSynthesisPath = '',
    [switch]   $DryRun
)

$ErrorActionPreference = 'Stop'

$Script:OrchDir = $PSScriptRoot
$Script:OrchLib = Join-Path $Script:OrchDir 'lib'
$Script:OrchExtLib = Join-Path $Script:OrchLib 'external_review'

. (Join-Path $Script:OrchLib 'PathValidation.ps1')
. (Join-Path $Script:OrchLib 'Redactor.ps1')
. (Join-Path $Script:OrchLib 'review/ReviewAdapter.ps1')
. (Join-Path $Script:OrchLib 'review/ReviewSurface.ps1')
. (Join-Path $Script:OrchExtLib 'ProviderProfiles.ps1')
. (Join-Path $Script:OrchExtLib 'EvidenceBundler.ps1')

function _Bee-NowUtc { return (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH-mm-ssZ') }

function _Bee-FenceFor {
    param([Parameter(Mandatory)] [AllowEmptyString()] [string] $Body)
    $longest = 0
    if ($Body) {
        $matches = [regex]::Matches($Body, '`+')
        foreach ($m in $matches) { if ($m.Length -gt $longest) { $longest = $m.Length } }
    }
    return ('`' * [Math]::Max(3, $longest + 1))
}

function Build-WaggleEpochEvidence {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $ConfigPath,
        [string]   $EpochId = '',
        [Parameter(Mandatory)] [string[]] $IterationIds,
        [string]   $EarlyTriggerReason = '',
        [string]   $OutputDir = '',
        [string]   $PreviousEpochSynthesisPath = '',
        [switch]   $DryRun
    )

    if (-not (Test-Path -LiteralPath $ConfigPath)) { throw "config not found: $ConfigPath" }
    $cfg = Get-Content -Raw -Path $ConfigPath -Encoding UTF8 | ConvertFrom-Json
    $projectRoot = $cfg.projectRoot
    $iterationsDir = if ($cfg.PSObject.Properties['iterationsDir'] -and $cfg.iterationsDir) { [string]$cfg.iterationsDir } else { 'iterations' }
    $iterRoot = Join-Path $projectRoot $iterationsDir

    $ic = Get-WaggleIterationCycleConfig -Config $cfg
    $er = Get-WaggleExternalReviewConfig -Config $cfg

    if ($IterationIds.Count -lt 1 -or $IterationIds.Count -gt 50) {
        throw "iteration_count must be in [1,50]; got $($IterationIds.Count)"
    }
    foreach ($iid in $IterationIds) { Assert-IterationIdValid -Id $iid }

    $first = $IterationIds[0]
    $last  = $IterationIds[$IterationIds.Count - 1]
    if (-not $EpochId) {
        $EpochId = (_Bee-NowUtc) + '_epoch_' + ($IterationIds.Count.ToString())
    }
    if (-not $OutputDir) {
        $OutputDir = Join-Path (Join-Path $iterRoot $last) ('external_reviews/epoch_' + $EpochId + '/evidence')
    }
    if (-not (Test-Path -LiteralPath $OutputDir)) {
        New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
    }

    # ---- Per-iteration scan ------------------------------------------
    $iterRecs = New-Object System.Collections.Generic.List[object]
    $logsPaths = New-Object System.Collections.Generic.List[string]
    $reviewPaths = New-Object System.Collections.Generic.List[string]
    $cumulativeReportsBuilder = New-Object System.Text.StringBuilder
    $cumulativeDiffBuilder = New-Object System.Text.StringBuilder

    foreach ($iid in $IterationIds) {
        $iterFolder = Join-Path $iterRoot $iid
        if (-not (Test-Path -LiteralPath $iterFolder)) {
            throw "iteration folder missing: $iterFolder"
        }

        # Internal review verdicts
        $verdicts = Get-WaggleIterationInternalReviewVerdicts -IterationFolder $iterFolder

        # Status
        $status = 'unknown'
        $stateFile = Join-Path $iterFolder 'state.json'
        if (Test-Path -LiteralPath $stateFile) {
            try {
                $st = Get-Content -Raw -Path $stateFile -Encoding UTF8 | ConvertFrom-Json
                if ($st.PSObject.Properties['status']) { $status = [string]$st.status }
            } catch {}
        }

        # No-work classification
        $nw = Get-WaggleNoWorkClassification -IterationFolder $iterFolder `
                -DiffMinBytes $ic.no_work_diff_min_bytes `
                -RaporttiMinBytes $ic.no_work_raportti_min_bytes `
                -StdoutMinMeaningfulBytes $ic.no_work_stdout_min_meaningful_bytes `
                -RaporttiPath (Join-Path $projectRoot $cfg.reportFile)

        # Diff line count + files touched (cheap proxy via run_metadata)
        $diffLines = 0
        $filesTouched = 0
        $runMeta = Join-Path $iterFolder 'run_metadata.json'
        if (Test-Path -LiteralPath $runMeta) {
            try {
                $rm = Get-Content -Raw -Path $runMeta -Encoding UTF8 | ConvertFrom-Json
                if ($rm.PSObject.Properties['diff_lines_changed']) { $diffLines = [int]$rm.diff_lines_changed }
                if ($rm.PSObject.Properties['files_touched_count']) { $filesTouched = [int]$rm.files_touched_count }
            } catch {}
        }

        $iterRecs.Add([pscustomobject]@{
            iteration_id              = $iid
            status                    = $status
            internal_review_verdicts  = $verdicts
            git_diff_lines_changed    = $diffLines
            files_touched_count       = $filesTouched
            no_work_classification    = [bool]$nw.no_work
        }) | Out-Null

        # ---- raportti per-iter snapshot (project-level) ---------------
        $rep = Join-Path $projectRoot $cfg.reportFile
        $repBody = ''
        if (Test-Path -LiteralPath $rep) {
            try { $repBody = Get-Content -Raw -Path $rep -Encoding UTF8 } catch { $repBody = '' }
        }
        $fence = _Bee-FenceFor -Body $repBody
        [void]$cumulativeReportsBuilder.AppendLine('--- ITERATION ' + $iid + ' raportti.md ---')
        [void]$cumulativeReportsBuilder.AppendLine($fence + 'markdown')
        [void]$cumulativeReportsBuilder.AppendLine($repBody)
        [void]$cumulativeReportsBuilder.AppendLine($fence)
        [void]$cumulativeReportsBuilder.AppendLine('')

        # ---- per-iter combined logs file -----------------------------
        $stdoutFile = Join-Path $iterFolder 'claude_stdout.txt'
        $stderrFile = Join-Path $iterFolder 'claude_stderr.txt'
        $tailFile   = Join-Path $iterFolder 'powershell_tail.txt'
        $sbLogs = New-Object System.Text.StringBuilder
        foreach ($pair in @(@($stdoutFile, 'claude_stdout.txt'), @($stderrFile, 'claude_stderr.txt'), @($tailFile, 'powershell_tail.txt'))) {
            $p = $pair[0]; $name = $pair[1]
            $body = ''
            if (Test-Path -LiteralPath $p) {
                try { $body = Get-Content -Raw -Path $p -Encoding UTF8 } catch { $body = '' }
            }
            $f = _Bee-FenceFor -Body $body
            [void]$sbLogs.AppendLine('## ' + $iid + ' / ' + $name)
            [void]$sbLogs.AppendLine($f + 'text')
            [void]$sbLogs.AppendLine($body)
            [void]$sbLogs.AppendLine($f)
            [void]$sbLogs.AppendLine('')
        }
        # Redact captured logs (they are untrusted-IO).
        $combinedLogs = $sbLogs.ToString()
        $r = Invoke-WaggleRedaction -Text $combinedLogs
        $combinedLogsRed = $r.text
        $iterIdx = $iterRecs.Count
        $logsRel = "iter${iterIdx}_logs_combined.md"
        $logsAbs = Join-Path $OutputDir $logsRel
        if (-not $DryRun) {
            Set-Content -Path $logsAbs -Value $combinedLogsRed -Encoding UTF8
        }
        $logsPaths.Add($logsRel) | Out-Null

        # ---- per-iter internal review combined file -------------------
        $rDir = Join-Path $iterFolder 'reviews'
        $sbR = New-Object System.Text.StringBuilder
        foreach ($role in 'architect','security','reliability') {
            $mdp = Join-Path $rDir ($role + '.md')
            $body = ''
            if (Test-Path -LiteralPath $mdp) {
                try { $body = Get-Content -Raw -Path $mdp -Encoding UTF8 } catch { $body = '' }
            }
            $f = _Bee-FenceFor -Body $body
            [void]$sbR.AppendLine('## ' + $iid + ' / internal review / ' + $role)
            [void]$sbR.AppendLine($f + 'markdown')
            [void]$sbR.AppendLine($body)
            [void]$sbR.AppendLine($f)
            [void]$sbR.AppendLine('')
        }
        $reviewRel = "iter${iterIdx}_internal_review.md"
        $reviewAbs = Join-Path $OutputDir $reviewRel
        if (-not $DryRun) {
            Set-Content -Path $reviewAbs -Value $sbR.ToString() -Encoding UTF8
        }
        $reviewPaths.Add($reviewRel) | Out-Null

        # ---- Diff: read git_metadata.json's diff if present ----------
        $gitMeta = Join-Path $iterFolder 'git_metadata.json'
        if (Test-Path -LiteralPath $gitMeta) {
            try {
                $gm = Get-Content -Raw -Path $gitMeta -Encoding UTF8 | ConvertFrom-Json
                if ($gm.PSObject.Properties['diff_text']) {
                    [void]$cumulativeDiffBuilder.AppendLine('--- ITERATION ' + $iid + ' git diff ---')
                    [void]$cumulativeDiffBuilder.AppendLine([string]$gm.diff_text)
                    [void]$cumulativeDiffBuilder.AppendLine('')
                }
            } catch {}
        }
    }

    # ---- Cumulative artifacts -----------------------------------------
    $diffPath = Join-Path $OutputDir 'cumulative_diff.patch'
    $rapPath  = Join-Path $OutputDir 'cumulative_raportti.md'
    $supPath  = Join-Path $OutputDir 'cumulative_supplement.md'
    if (-not $DryRun) {
        $rDiff = Invoke-WaggleRedaction -Text $cumulativeDiffBuilder.ToString()
        Set-Content -Path $diffPath -Value $rDiff.text -Encoding UTF8
        $rRep = Invoke-WaggleRedaction -Text $cumulativeReportsBuilder.ToString()
        Set-Content -Path $rapPath  -Value $rRep.text -Encoding UTF8
        # Supplement: re-use Phase 2A-3+/2A-4 ReviewSurface generator
        # over the whole repo (small enough for evidence packaging).
        $sup = Get-WaggleReviewSurfaceSupplement -ProjectRoot $projectRoot
        Set-Content -Path $supPath -Value ([string]$sup.markdown) -Encoding UTF8
    }

    # ---- previous_epoch_synthesis_path -------------------------------
    $prevPath = ''
    if ($PreviousEpochSynthesisPath -and (Test-Path -LiteralPath $PreviousEpochSynthesisPath)) {
        $prevAbs = Join-Path $OutputDir 'previous_epoch_synthesis.md'
        if (-not $DryRun) {
            Copy-Item -LiteralPath $PreviousEpochSynthesisPath -Destination $prevAbs -Force
        }
        $prevPath = 'previous_epoch_synthesis.md'
    } else {
        $prevPath = $null
    }

    # ---- Regression state --------------------------------------------
    $regression = [pscustomobject]@{
        hardening_gates_failure_present = $false
        ci_failure_present              = $false
        previously_passing_test_now_failing = @()
    }
    $latestGate = Join-Path $projectRoot 'docs/runs/hardening_gates/latest.json'
    if (Test-Path -LiteralPath $latestGate) {
        try {
            $gj = Get-Content -Raw -Path $latestGate -Encoding UTF8 | ConvertFrom-Json
            if ($gj.PSObject.Properties['overall_ok'] -and -not [bool]$gj.overall_ok) {
                $regression.hardening_gates_failure_present = $true
                if ($gj.PSObject.Properties['results']) {
                    foreach ($r in @($gj.results)) {
                        if (-not [bool]$r.ok) {
                            $regression.previously_passing_test_now_failing += [string]$r.gate
                        }
                    }
                }
            }
        } catch {}
    }
    $regJson = ($regression | ConvertTo-Json -Depth 6)
    if (-not $DryRun) {
        Set-Content -Path (Join-Path $OutputDir 'regression_state.json') -Value $regJson -Encoding UTF8
    }

    # ---- Package quality snapshot -------------------------------------
    # Use cumulative diff + reports as the synthetic "package" for the
    # Phase 2A-3 quality scoring -- write a tiny package file in the
    # evidence dir for that purpose.
    $pkgSynth = Join-Path $OutputDir '_synth_package_for_quality_scoring.md'
    if (-not $DryRun) {
        $pkgBody = "# Synthetic package for quality scoring`n`n## SECURITY PREAMBLE`n`nUNTRUSTED DATA placeholder.`n`n## Source: cumulative_diff.patch`n`n``````text`n" + $cumulativeDiffBuilder.ToString() + "`n``````"
        Set-Content -Path $pkgSynth -Value $pkgBody -Encoding UTF8
    }
    $reviewable_files = 0
    $reviewable_lines = 0
    $review_readiness = 'INSUFFICIENT_EVIDENCE'
    if (Test-Path -LiteralPath $pkgSynth) {
        try {
            $q = Get-WaggleReviewPackageQuality -PackagePath $pkgSynth
            $reviewable_files = [int]$q.reviewable_files_count
            $reviewable_lines = [int]$q.reviewable_lines_count
            if ($cumulativeDiffBuilder.Length -gt 200) {
                $review_readiness = 'REVIEW_READY'
            } elseif ($cumulativeDiffBuilder.Length -gt 0) {
                $review_readiness = 'SUPPLEMENT_ONLY'
            } else {
                $review_readiness = 'INSUFFICIENT_EVIDENCE'
            }
        } catch {}
    }

    # ---- evidence_sha256: hash over the deterministic file set --------
    $relsForSha = @()
    $relsForSha += 'cumulative_diff.patch'
    $relsForSha += 'cumulative_raportti.md'
    $relsForSha += 'cumulative_supplement.md'
    $relsForSha += 'regression_state.json'
    foreach ($p in $logsPaths) { $relsForSha += $p }
    foreach ($p in $reviewPaths) { $relsForSha += $p }
    if ($prevPath) { $relsForSha += $prevPath }

    $evidenceSha = if ($DryRun) { 'd' * 64 } else { Get-WaggleCanonicalEvidenceSha -RootPath $OutputDir -RelativePaths $relsForSha }

    # ---- Final epoch_evidence.json -----------------------------------
    if (-not $EarlyTriggerReason) { $EarlyTriggerReason = $null }

    $manifest = [ordered]@{
        epoch_id                              = $EpochId
        first_iteration_id                    = $first
        last_iteration_id                     = $last
        iteration_count                       = $IterationIds.Count
        early_trigger_reason                  = $EarlyTriggerReason
        iterations                            = $iterRecs.ToArray()
        cumulative_diff_path                  = 'cumulative_diff.patch'
        cumulative_raportti_path              = 'cumulative_raportti.md'
        logs_combined_per_iteration_paths     = $logsPaths.ToArray()
        internal_reviews_per_iteration_paths  = $reviewPaths.ToArray()
        cumulative_supplement_path            = 'cumulative_supplement.md'
        previous_epoch_synthesis_path         = $prevPath
        regression_state                      = $regression
        package_quality_snapshot              = [pscustomobject]@{
            reviewable_files_count   = $reviewable_files
            reviewable_lines_count   = $reviewable_lines
            review_readiness_status  = $review_readiness
        }
        evidence_sha256                       = $evidenceSha
        generated_at_utc                      = (Get-Date).ToUniversalTime().ToString('o')
        format_version                        = '2b.1'
    }

    $jsonOut = ([pscustomobject]$manifest) | ConvertTo-Json -Depth 16
    $epochJsonPath = Join-Path $OutputDir 'epoch_evidence.json'
    if (-not $DryRun) {
        Set-Content -Path $epochJsonPath -Value $jsonOut -Encoding UTF8
    }

    # ---- Per-provider attachment plans -------------------------------
    $attachmentPlans = [ordered]@{}
    $providerNames = @('claude_web','gemini','grok','gpt_synthesis')
    foreach ($pname in $providerNames) {
        $cap = [int]$er.max_attachments_per_provider
        $plan = Get-WaggleAttachmentPlanForProvider -EvidenceDir $OutputDir -MaxAttachments $cap
        if (-not $plan.ok -and [bool]$er.fail_on_attachment_overflow) {
            throw ("attachment_overflow_$pname : " + ($plan.errors -join '; '))
        }
        $attachmentPlans[$pname] = $plan
    }

    return [pscustomobject]@{
        ok                = $true
        epoch_id          = $EpochId
        output_dir        = $OutputDir
        epoch_json_path   = $epochJsonPath
        evidence_sha256   = $evidenceSha
        attachment_plans  = ([pscustomobject]$attachmentPlans)
        manifest          = ([pscustomobject]$manifest)
    }
}

# CLI wrapper -- only fires when the script is invoked with explicit
# args (not when dot-sourced from a test harness).
if ($MyInvocation.InvocationName -ne '.' -and $ConfigPath -and $IterationIds.Count -gt 0) {
    $params = @{
        ConfigPath        = $ConfigPath
        IterationIds      = $IterationIds
        EarlyTriggerReason = $EarlyTriggerReason
        OutputDir         = $OutputDir
        PreviousEpochSynthesisPath = $PreviousEpochSynthesisPath
        DryRun            = [bool]$DryRun
    }
    if ($EpochId) { $params['EpochId'] = $EpochId }
    $r = Build-WaggleEpochEvidence @params
    if ($r.ok) {
        Write-Host ('Epoch evidence built: ' + $r.epoch_id)
        Write-Host ('  output_dir   : ' + $r.output_dir)
        Write-Host ('  epoch_json   : ' + $r.epoch_json_path)
        Write-Host ('  evidence_sha : ' + $r.evidence_sha256)
        exit 0
    } else {
        Write-Host 'Epoch evidence build failed' -ForegroundColor Red
        exit 1
    }
}
