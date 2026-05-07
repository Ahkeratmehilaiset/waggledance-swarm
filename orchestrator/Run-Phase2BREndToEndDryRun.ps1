#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2B-Revision (P11): end-to-end synthetic dry-run that
    exercises the full revised pipeline:
      1. 4-iteration synthetic epoch (with an auto-repair +
         verification trajectory)
      2. internal Claude reviews with the new SEC-009 shape
         (reviewer_self_id + suggested_next_actions)
      3. synthetic Codex Scout findings imported via
         Import-WaggleCodexFindings
      4. Build-WaggleProposalMatrix (internal + Codex)
      5. Build-WaggleCockpitData; verify state/cockpit_data.json
      6. Open-WaggleCockpit smoke (file-existence assertion only)
      7. Export-WaggleExternalReviewQueue (Gemini + Grok defaults)
      8. synthetic Gemini + Grok responses imported
      9. Build-WaggleProposalMatrix re-run (now includes external)
      10. New-WaggleSynthesisPasteBlock
      11. synthetic GPT synthesis response (decision=continue,
          MANDATORY first line) imported
      12. New-WaggleIterationFromSynthesis -DryRun (correct SHA -> success)
      13. New-WaggleIterationFromSynthesis -DryRun (wrong SHA -> refused)

    Outputs at docs/runs/orchestrator_phase2br_cockpit_codex_regression_2026_05_07/dry_run_log.md
    + dry_run.json. The temporary project tree is cleaned up at the
    end.
#>
[CmdletBinding()] param()
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$runDir   = Join-Path $repoRoot 'docs/runs/orchestrator_phase2br_cockpit_codex_regression_2026_05_07'
if (-not (Test-Path -LiteralPath $runDir)) { New-Item -ItemType Directory -Path $runDir -Force | Out-Null }

. (Join-Path $PSScriptRoot 'Build-WaggleEpochEvidence.ps1')
. (Join-Path $PSScriptRoot 'Export-WaggleExternalReviewQueue.ps1')
. (Join-Path $PSScriptRoot 'Import-WaggleExternalReviewResponse.ps1')
. (Join-Path $PSScriptRoot 'Import-WaggleCodexFindings.ps1')
. (Join-Path $PSScriptRoot 'Build-WaggleProposalMatrix.ps1')
. (Join-Path $PSScriptRoot 'Build-WaggleCockpitData.ps1')
. (Join-Path $PSScriptRoot 'New-WaggleSynthesisPasteBlock.ps1')
. (Join-Path $PSScriptRoot 'Import-WaggleSynthesisResult.ps1')
. (Join-Path $PSScriptRoot 'New-WaggleIterationFromSynthesis.ps1')
. (Join-Path $PSScriptRoot 'lib/RegressionLedger.ps1')
. (Join-Path $PSScriptRoot 'lib/external_review/EpochCycleTrigger.ps1')
. (Join-Path $PSScriptRoot 'lib/external_review/FindingClassifier.ps1')

$tmp = Join-Path $env:TEMP ("waggle-2br-dryrun-{0}" -f ([guid]::NewGuid().ToString('N')))
[void](New-Item -ItemType Directory -Path $tmp -Force)

$Script:Steps = New-Object System.Collections.Generic.List[object]
function Step {
    param([string] $Name, [scriptblock] $Body)
    $tStart = Get-Date
    $ok = $true; $err = ''; $detail = $null
    try { $detail = & $Body }
    catch { $ok = $false; $err = $_.Exception.Message }
    $elapsed = [Math]::Round(((Get-Date) - $tStart).TotalSeconds, 2)
    $rec = [pscustomobject]@{ step = $Name; ok = $ok; elapsed_seconds = $elapsed; error = $err }
    $Script:Steps.Add($rec) | Out-Null
    if ($ok) { Write-Host ("[ok] {0,-58} {1,6:n2}s" -f $Name, $elapsed) -ForegroundColor Green }
    else      { Write-Host ("[!!] {0,-58} {1,6:n2}s ({2})" -f $Name, $elapsed, $err) -ForegroundColor Red; throw $err }
    return $detail
}

function _D-NowUtc { return (Get-Date).ToUniversalTime().ToString('o') }

# ------------------------------------------------------------------
# Build a synthetic 4-iteration project tree under $tmp.
# Iterations:
#   iter_a — clean baseline
#   iter_b — internal review finds a TRIVIAL_AUTO_FIX (typo in
#            schema-style; classifier should route to TRIVIAL)
#   iter_c — auto-repair iteration: status fix_attempted ->
#            verification_pending
#   iter_d — verification iteration: status flips to verified
# ------------------------------------------------------------------

$proj = $null
$cfgPath = $null
$iterIds = @('2026-05-07_2bra','2026-05-07_2brb','2026-05-07_2brc','2026-05-07_2brd')
$EpochId = 'p11-e2e-2br'

[void](Step 'P11-1: build synthetic 4-iteration project tree' {
    $root = Join-Path $tmp 'proj'
    [void](New-Item -ItemType Directory -Path $root -Force)
    [void](New-Item -ItemType Directory -Path (Join-Path $root 'state') -Force)
    [void](New-Item -ItemType Directory -Path (Join-Path $root 'iterations') -Force)
    Set-Content -Path (Join-Path $root 'raportti.md') -Value '# raportti' -Encoding UTF8

    # Copy prompt templates from the real project tree (the queue
    # exporter looks in <projectRoot>/prompts/external_review/).
    $promptsDir = Join-Path $root 'prompts/external_review'
    [void](New-Item -ItemType Directory -Path (Join-Path $promptsDir 'providers') -Force)
    foreach ($f in 'architect.md','security.md','reliability.md','synthesis_gpt.md') {
        Copy-Item -LiteralPath (Join-Path $repoRoot ('prompts/external_review/' + $f)) -Destination (Join-Path $promptsDir $f) -Force
    }
    foreach ($f in 'claude_web.md','gemini.md','grok.md','gpt.md') {
        Copy-Item -LiteralPath (Join-Path $repoRoot ('prompts/external_review/providers/' + $f)) -Destination (Join-Path (Join-Path $promptsDir 'providers') $f) -Force
    }

    foreach ($iid in $iterIds) {
        $iterDir = Join-Path (Join-Path $root 'iterations') $iid
        [void](New-Item -ItemType Directory -Path $iterDir -Force)
        Set-Content -Path (Join-Path $iterDir 'state.json') -Value (@{ iteration_id = $iid; status = 'COMPLETED' } | ConvertTo-Json) -Encoding UTF8
        Set-Content -Path (Join-Path $iterDir 'run_metadata.json') -Value '{}' -Encoding UTF8
        Set-Content -Path (Join-Path $iterDir 'claude_stdout.txt') -Value 'normal stdout' -Encoding UTF8
        [System.IO.File]::WriteAllText((Join-Path $iterDir 'claude_stderr.txt'), '')
        Set-Content -Path (Join-Path $iterDir 'git_metadata.json') -Value (@{
            commit = '1234567890abcdef1234567890abcdef12345678'
            diff_text = "diff --git a/foo$iid b/foo$iid`n+a`n+b`n+c`n+d`n+e`n+f`n+g`n+h`n+i`n+j`n+k`n+l`n+m`n+n`n+o`n+p`n+q`n+r`n+s`n+t`n+u`n+v`n+w`n+x`n"
        } | ConvertTo-Json) -Encoding UTF8
        [void](New-Item -ItemType Directory -Path (Join-Path $iterDir 'reviews') -Force)
        foreach ($role in 'architect','security','reliability') {
            $verdict = 'pass'
            $findings = @()
            $proposals = @()
            $selfId = @{
                claimed_model_name = 'Claude Opus 4.7'
                claimed_version = $null
                training_cutoff = $null
                self_assessed_strengths_for_this_review = @('familiarity with WaggleDance core')
                self_assessed_limitations_for_this_review = @('no live runtime telemetry')
                estimated_context_window_kb = $null
                uses_extended_thinking_or_reasoning_mode = $false
                runtime = 'claude_code'
            }
            if ($iid -eq '2026-05-07_2brb' -and $role -eq 'architect') {
                # Trivial finding: a JSON property typo in a single file.
                # The auto-repair classifier should route this as
                # TRIVIAL_AUTO_FIX (1 file, fixability=trivial).
                $verdict = 'pass_with_notes'
                $findings = @(@{
                    id = 'ARC-101'; severity = 'low'
                    title = 'JSON property typo in fixture'
                    where = 'src/foo.ps1:42'
                    evidence = 'expected key: foo_count actual: fooCount'
                    why_it_matters = 'serializer mismatch with consumer schema'
                    recommended_action = 'rename property to foo_count'
                })
                $proposals = @(@{
                    id = 'PROP-101'
                    title = 'rename fooCount -> foo_count'
                    rationale = 'consumer schema mismatch'
                    approach = 'edit src/foo.ps1 line 42; add tests/test_foo_count.py asserting key shape'
                    estimated_effort = 'small'
                    risks = 'low'
                    expected_payoff = 'fixes serializer mismatch'
                })
            }
            $j = [ordered]@{
                role = $role; target_iteration_id = $iid
                source_package_path = "iterations/$iid/llm_input_package.md"
                summary = "synthetic $role review of $iid"
                verdict = $verdict
                reviewer_self_id = $selfId
                findings = $findings
                suggested_next_actions = $proposals
                metrics = @{ files_reviewed = 1; lines_reviewed = 24; review_duration_seconds = 3 }
                completed = $true
            }
            Set-Content -Path (Join-Path (Join-Path $iterDir 'reviews') ($role + '.json')) -Value (([pscustomobject]$j) | ConvertTo-Json -Depth 10) -Encoding UTF8
            Set-Content -Path (Join-Path (Join-Path $iterDir 'reviews') ($role + '.md')) -Value ('# ' + $role + ' review of ' + $iid) -Encoding UTF8
        }
    }

    $cfgObj = @{
        projectRoot = $root; iterationsDir = 'iterations'; stateDir = 'state'
        reportFile = 'raportti.md'; transcriptDir = 'transcripts'
        executionMode = 'print'; claudeCommand = 'claude'; model = 'opus'
        outputFormat = 'text'; maxTurns = 30; permissionMode = 'default'
        safeMode = $true; allowBash = $false
        allowedTools = @('Read','Write','Edit','Glob','Grep'); disallowedTools = @('Bash')
        dangerouslySkipPermissions = $false
        sanitizeEnvironment = $true; envDenylist = $null; envAllowList = @()
        killOnInteractivePrompt = $true; runnerPollSeconds = 1; tailLineCount = 100
        fullTranscriptMaxBytes = 1048576; pollIntervalSeconds = 1
        stableThresholdSeconds = 5; runTimeoutMinutes = 5
        llmPackageMaxChars = 200000; perSectionMaxChars = 60000
        requireExitMarker = $false; requireReport = $false; requireClaudeAuthStatus = $false
        interactivePromptPatterns = @(); completedPromptPatterns = @()
        exitMarker = '##X##'
        external_review = @{
            enabled = $true
            queue_dir_relative = 'external_reviews/queue'
            imported_dir_relative = 'external_reviews/imported'
            synthesis_dir_relative = 'external_reviews/synthesis'
            max_attachments_per_provider = 20; fail_on_attachment_overflow = $true
            providers = @{
                gemini        = @{ enabled = $true; timeout_sec = 600;  expected_model_in_ui = 'Gemini Pro Advanced' }
                grok          = @{ enabled = $true; timeout_sec = 900;  expected_model_in_ui = 'Grok Expert mode' }
                gpt_synthesis = @{ enabled = $true; timeout_sec = 4800; expected_model_in_ui = 'GPT Pro 5.5 Extended Thinking' }
            }
            auto_approval_rule = 'all_reviewers_below_needs_changes'
            manual_pause_flag_relative = 'state/pause_external_review.flag'
            halt_marker = 'WAGGLE_HALT'; session_resume_threshold_hours = 4
        }
        iteration_cycle = @{
            local_iterations_per_external_review = 3
            min_local_iterations = 2; target_local_iterations = 3; max_local_iterations = 6
            verification_iterations_after_fix = 1
            max_repair_attempts = @{ medium = 2; high = 2; critical = 1 }
            escalate_if_same_issue_reappears = $true
            escalate_if_same_test_fails_twice = $true
            external_review_after_epoch = $true
            max_iterations_per_session = 50
            early_trigger_on_regression = $true
            early_trigger_on_hardening_gate_failure = $true
            early_trigger_on_internal_critical_finding = $true
            early_trigger_on_no_work_consecutive = 2
            no_work_diff_min_bytes = 1; no_work_raportti_min_bytes = 1
            no_work_stdout_min_meaningful_bytes = 100
        }
        finding_classifier = @{
            enabled = $true
            max_auto_repair_iterations_per_epoch = 3
            max_files_for_trivial_auto_fix = 2
            max_files_for_local_repair = 3
            unsafe_keywords = @('credential','secret','private_key','destructive')
            strategic_keywords = @('should we','two valid approaches','architectural direction')
            force_external_categories = @('architecture','security_semantics','lock_state_signal','concurrency','public_api')
        }
        models = @{
            claude_code = 'claude-opus-4-7'; claude_web = 'Claude Opus 4.7 (Max plan)'
            gemini = 'Gemini Pro Advanced'; grok = 'Grok Expert mode'
            gpt_synthesis = 'GPT Pro 5.5 Extended Thinking'
        }
    }
    $cfgFile = Join-Path $root 'orchestrator.config.json'
    Set-Content -Path $cfgFile -Value ($cfgObj | ConvertTo-Json -Depth 12) -Encoding UTF8
    $script:proj = $root
    $script:cfgPath = $cfgFile
    return @{ project_root = $root; iter_count = $iterIds.Count }
})

# ------------------------------------------------------------------
# Build epoch evidence
# ------------------------------------------------------------------

$ev = $null
$evidenceSha = $null
[void](Step 'P11-2: Build-WaggleEpochEvidence (4 iterations)' {
    $script:ev = Build-WaggleEpochEvidence -ConfigPath $cfgPath -EpochId $EpochId -IterationIds $iterIds
    $script:evidenceSha = $ev.evidence_sha256
    if ($evidenceSha -notmatch '^[a-f0-9]{64}$') { throw "bad evidence_sha256" }
    return @{ epoch_id = $EpochId; evidence_sha256 = $evidenceSha }
})

# ------------------------------------------------------------------
# Synthetic Codex findings + import
# ------------------------------------------------------------------

[void](Step 'P11-3: Import synthetic Codex findings' {
    $codexObj = [ordered]@{
        format_version = '1.0'
        scout_self_id = [ordered]@{
            tool = 'codex_cli'; version = '0.1.x'; model = 'gpt-codex-mini'
            worktree_root = 'C:\\Python\\project2-codex-scout'
            ran_at_utc = (_D-NowUtc)
        }
        scope = [ordered]@{
            epoch_id = $EpochId; target_iteration_ids = $iterIds
            branch_at_scan = 'phase2br'; commit_at_scan = ('a' * 40)
        }
        findings = @(
            [ordered]@{ id = 'CDEX-001'; severity = 'medium'; category = 'reliability'; title = 'lock release on early return'; where = 'lib/Lockfile.ps1:120'; evidence = 'partial release on early return path'; why_it_matters = 'cleanup paths skip release'; recommended_action = 'wrap release in finally' },
            [ordered]@{ id = 'CDEX-002'; severity = 'low'; category = 'test_gap'; title = 'no test for the no-work classifier when stdout has only whitespace'; where = 'tests/'; evidence = 'no whitespace-only fixture'; why_it_matters = 'edge case slips'; recommended_action = 'add fixture' },
            [ordered]@{ id = 'CDEX-003'; severity = 'medium'; category = 'reliability'; title = 'retry/backoff missing on cold-start'; where = 'orchestrator/ClaudeRunner.ps1'; evidence = 'no retry'; why_it_matters = 'cold-start flakes'; recommended_action = 'wrap process start in retry loop' }
        )
        proposals = @(
            [ordered]@{ id = 'CDEX-PROP-001'; title = 'add retry/backoff to ClaudeRunner'; rationale = 'cold-start flakes'; approach = 'wrap process start in retry loop with backoff up to 3 attempts'; estimated_effort = 'small'; risks = 'transient false positives'; expected_payoff = 'fewer flaky CI runs' },
            [ordered]@{ id = 'CDEX-PROP-002'; title = 'expand no-work classifier coverage'; rationale = 'edge cases'; approach = 'add tests/test_no_work_classifier.py with whitespace + emoji-only stdout fixtures'; estimated_effort = 'small'; risks = 'low'; expected_payoff = 'fewer false negatives in early-trigger' },
            [ordered]@{ id = 'CDEX-PROP-003'; title = 'tighten lock release semantics'; rationale = 'reliability'; approach = 'rewrap release in try/finally; add integration test'; estimated_effort = 'medium'; risks = 'subtle regressions in lock contention'; expected_payoff = 'no stale locks across restart' },
            [ordered]@{ id = 'CDEX-PROP-004'; title = 'document atomic-flip preconditions'; rationale = 'docs'; approach = 'add docs/architecture/atomic_flip_preconditions.md'; estimated_effort = 'small'; risks = 'none'; expected_payoff = 'operator clarity' }
        )
        completed = $true
    }
    $codexFile = Join-Path $tmp 'codex_findings.json'
    Set-Content -Path $codexFile -Value (([pscustomobject]$codexObj) | ConvertTo-Json -Depth 12) -Encoding UTF8
    $r = Import-WaggleCodexFindings -ConfigPath $cfgPath -EpochId $EpochId -IterationId $iterIds[-1] -FindingsFile $codexFile
    if (-not $r.ok) { throw "codex import failed: $($r.reason)" }
    return @{ import_id = $r.import_id; finding_count = $r.finding_count; proposal_count = $r.proposal_count }
})

# ------------------------------------------------------------------
# Proposal matrix (round 1: internal + Codex, no external yet)
# ------------------------------------------------------------------

[void](Step 'P11-4: Build-WaggleProposalMatrix (internal + Codex)' {
    $r = Build-WaggleProposalMatrix -ConfigPath $cfgPath -EpochId $EpochId -IterationId $iterIds[-1] -IncludeCodex $true -IncludeExternal $false
    if (-not $r.ok) { throw "matrix build failed" }
    if ($r.row_count -lt 5) { throw "expected >= 5 rows; got $($r.row_count)" }
    return @{ rows = $r.row_count; internal = $r.sources_summary.claude_internal_count; codex = $r.sources_summary.codex_count }
})

# ------------------------------------------------------------------
# Cockpit data (round 1: pre-export)
# ------------------------------------------------------------------

[void](Step 'P11-5: Build-WaggleCockpitData (round 1)' {
    $r = Build-WaggleCockpitData -ConfigPath $cfgPath -EpochId $EpochId -IterationId $iterIds[-1]
    if (-not $r.ok) { throw "cockpit data build failed" }
    return @{ path = $r.path; bundles = $r.bundle_count; synth = $r.synthesis_status }
})

# ------------------------------------------------------------------
# Open-WaggleCockpit smoke (file-existence assertion only)
# ------------------------------------------------------------------

[void](Step 'P11-6: Open-WaggleCockpit smoke' {
    $cockpitFile = Join-Path $repoRoot 'review_cockpit.html'
    if (-not (Test-Path -LiteralPath $cockpitFile)) { throw "review_cockpit.html missing" }
    return @{ cockpit_file_exists = $true }
})

# ------------------------------------------------------------------
# Export queue (Gemini + Grok defaults)
# ------------------------------------------------------------------

$exp = $null
[void](Step 'P11-7: Export-WaggleExternalReviewQueue (gemini + grok)' {
    $script:exp = Export-WaggleExternalReviewQueue -ConfigPath $cfgPath -EvidenceJsonPath $ev.epoch_json_path
    if (-not $exp.ok) { throw "queue export failed" }
    if (@($exp.bundles).Count -ne 2) { throw "expected 2 bundles; got $((@($exp.bundles).Count))" }
    return @{ bundles_count = @($exp.bundles).Count }
})

# ------------------------------------------------------------------
# Synthetic external responses + import
# ------------------------------------------------------------------

[void](Step 'P11-8: Import 2 synthetic external responses' {
    $rspDir = Join-Path $tmp 'responses'
    [void](New-Item -ItemType Directory -Path $rspDir -Force)
    $iidLast = $iterIds[-1]
    $pairs = @(
        @{ provider = 'gemini'; role = 'architect' },
        @{ provider = 'grok';   role = 'reliability' }
    )
    foreach ($p in $pairs) {
        $obj = [ordered]@{
            reviewer_self_id = [ordered]@{
                claimed_model_name = "Synthetic $($p.provider)"
                claimed_version = $null; training_cutoff = $null
                self_assessed_strengths_for_this_review = @('s')
                self_assessed_limitations_for_this_review = @('l')
                estimated_context_window_kb = $null
                uses_extended_thinking_or_reasoning_mode = $false
            }
            provider = $p.provider; role = $p.role
            target_iteration_id = $iidLast; epoch_id = $EpochId
            source_evidence_sha256 = $evidenceSha
            reviewer_summary = "synthetic $($p.role) review summary"
            verdict = 'pass'
            findings = @()
            suggested_next_actions = @(
                @{ id = ($p.provider + '-PROP-001'); title = "$($p.provider): improve $($p.role) coverage"; rationale = 'r'; approach = 'a'; estimated_effort = 'small'; risks = 'rk'; expected_payoff = 'p' }
            )
            confidence = 'medium'; limitations = 'synthetic'; completed = $true
        }
        $sb = New-Object System.Text.StringBuilder
        [void]$sb.AppendLine('# Synthetic external response')
        [void]$sb.AppendLine('```reviewer-self-id'); [void]$sb.AppendLine('Synthetic.'); [void]$sb.AppendLine('```'); [void]$sb.AppendLine('')
        [void]$sb.AppendLine('```external-review-json')
        [void]$sb.AppendLine((([pscustomobject]$obj) | ConvertTo-Json -Depth 12))
        [void]$sb.AppendLine('```'); [void]$sb.AppendLine('')
        [void]$sb.AppendLine('EXTERNAL-REVIEW-COMPLETE')
        $rsp = Join-Path $rspDir ($p.provider + '_' + $p.role + '.md')
        Set-Content -Path $rsp -Value $sb.ToString() -Encoding UTF8
        $r = Import-WaggleExternalReviewResponse -ConfigPath $cfgPath -EpochId $EpochId -Provider $p.provider -Role $p.role -ResponseFile $rsp -IterationId $iidLast
        if (-not $r.ok) { throw "import failed for $($p.provider)/$($p.role): $($r.reason)" }
    }
    return @{ imported = 2 }
})

# ------------------------------------------------------------------
# Proposal matrix (round 2: internal + Codex + external)
# ------------------------------------------------------------------

[void](Step 'P11-9: Build-WaggleProposalMatrix (with external)' {
    $r = Build-WaggleProposalMatrix -ConfigPath $cfgPath -EpochId $EpochId -IterationId $iterIds[-1] -IncludeCodex $true -IncludeExternal $true
    if ($r.sources_summary.external_count -ne 2) { throw "expected external=2; got $($r.sources_summary.external_count)" }
    return @{ rows = $r.row_count; ext = $r.sources_summary.external_count }
})

# ------------------------------------------------------------------
# Synthesis paste-block
# ------------------------------------------------------------------

$spb = $null
[void](Step 'P11-10: New-WaggleSynthesisPasteBlock' {
    $script:spb = New-WaggleSynthesisPasteBlock -ConfigPath $cfgPath -EpochId $EpochId -IterationId $iterIds[-1]
    if (-not $spb.ok) { throw "paste-block failed" }
    $body = Get-Content -Raw -Path $spb.paste_block_path -Encoding UTF8
    foreach ($needle in 'INTERNAL Claude','EXTERNAL','PROPOSAL MATRIX','MANDATORY: Use Claude Opus 4.7','END OF PASTE-BLOCK') {
        if ($body -notmatch [regex]::Escape($needle)) { throw "paste-block missing: $needle" }
    }
    return @{ path = $spb.paste_block_path; bytes = (Get-Item -LiteralPath $spb.paste_block_path).Length }
})

# ------------------------------------------------------------------
# Synthesis response (synthetic, decision=continue)
# ------------------------------------------------------------------

$synthImp = $null
[void](Step 'P11-11: Import synthetic synthesis response (continue)' {
    $iidLast = $iterIds[-1]
    $obj = [ordered]@{
        synthesizer_self_id = [ordered]@{ claimed_model_name = 'Synthetic GPT'; claimed_version = $null; uses_extended_thinking_or_reasoning_mode = $true }
        target_iteration_id = $iidLast; epoch_id = $EpochId; source_evidence_sha256 = $evidenceSha
        synthesis_summary = 'Synthetic synthesis: continue with one accepted proposal.'
        included_review_imports = @(
            [ordered]@{ import_id = 'imp_g'; provider = 'gemini'; role = 'architect'; weight_in_synthesis = 'normal' },
            [ordered]@{ import_id = 'imp_k'; provider = 'grok';   role = 'reliability'; weight_in_synthesis = 'normal' }
        )
        excluded_review_imports = @()
        consolidated_findings = @()
        consolidated_proposals = @()
        decision = 'continue'; halt_marker = $null
        next_claude_code_prompt_block_marker = 'next-claude-code-prompt'
        completed = $true
    }
    $MandLine = 'MANDATORY: Use Claude Opus 4.7 (--model claude-opus-4-7) for this iteration. Do not downgrade.'
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.AppendLine('# Synthetic synthesis')
    [void]$sb.AppendLine('```synthesizer-self-id'); [void]$sb.AppendLine('Synthetic GPT.'); [void]$sb.AppendLine('```'); [void]$sb.AppendLine('')
    [void]$sb.AppendLine('```synthesis-json')
    [void]$sb.AppendLine((([pscustomobject]$obj) | ConvertTo-Json -Depth 12))
    [void]$sb.AppendLine('```'); [void]$sb.AppendLine('')
    [void]$sb.AppendLine('```next-claude-code-prompt')
    [void]$sb.AppendLine($MandLine)
    [void]$sb.AppendLine('Implement the accepted proposal. Touch src/foo.ps1 line 42.')
    [void]$sb.AppendLine('```'); [void]$sb.AppendLine('')
    [void]$sb.AppendLine('SYNTHESIS-COMPLETE')

    $rspPath = Join-Path $tmp 'gpt_response.md'
    Set-Content -Path $rspPath -Value $sb.ToString() -Encoding UTF8
    $script:synthImp = Import-WaggleSynthesisResult -ConfigPath $cfgPath -EpochId $EpochId -IterationId $iidLast -ResponseFile $rspPath
    if (-not $synthImp.ok) { throw "synthesis import failed: $($synthImp.reason)" }
    if ($synthImp.decision -ne 'continue') { throw "expected decision=continue; got $($synthImp.decision)" }
    return @{ synthesis_import_id = $synthImp.synthesis_import_id; decision = $synthImp.decision }
})

# ------------------------------------------------------------------
# DryRun launcher: correct SHA + wrong SHA
# ------------------------------------------------------------------

[void](Step 'P11-12: New-WaggleIterationFromSynthesis (correct SHA, dry-run)' {
    $iidLast = $iterIds[-1]
    $r = New-WaggleIterationFromSynthesis -ConfigPath $cfgPath -EpochId $EpochId `
            -IterationId $iidLast -SynthesisImportId $synthImp.synthesis_import_id -DryRun
    if (-not $r.ok) { throw "dry-run failed" }
    if (-not $r.sha_verified) { throw "sha_verified=false on dry-run" }
    return @{ new_iter_id = $r.new_iteration_id }
})

[void](Step 'P11-13: New-WaggleIterationFromSynthesis (mutated evidence, refused)' {
    $iidLast = $iterIds[-1]
    # Mutate the evidence dir to make recomputed SHA differ.
    $diffPath = Join-Path (Join-Path (Join-Path (Join-Path $proj 'iterations') $iidLast) ('external_reviews/epoch_' + $EpochId + '/evidence')) 'cumulative_diff.patch'
    Add-Content -Path $diffPath -Value "`n# tampered for sha mismatch test`n" -Encoding UTF8
    $threw = $false; $emsg = ''
    try {
        [void](New-WaggleIterationFromSynthesis -ConfigPath $cfgPath -EpochId $EpochId `
                -IterationId $iidLast -SynthesisImportId $synthImp.synthesis_import_id `
                -NewIterationId 'sha_mismatch_iter')
    } catch { $threw = $true; $emsg = $_.Exception.Message }
    if (-not $threw) { throw "expected SHA-mismatch refusal" }
    if ($emsg -notmatch 'source_evidence_sha256 mismatch') { throw "unexpected message: $emsg" }
    return @{ refused = $true }
})

# ------------------------------------------------------------------
# Cockpit data (round 2: post-import / post-synthesis)
# ------------------------------------------------------------------

[void](Step 'P11-14: Build-WaggleCockpitData (round 2 — post-import)' {
    $r = Build-WaggleCockpitData -ConfigPath $cfgPath -EpochId $EpochId -IterationId $iterIds[-1]
    if (-not $r.ok) { throw "cockpit data round 2 failed" }
    $d = Get-Content -Raw -Path $r.path -Encoding UTF8 | ConvertFrom-Json
    if ($d.synthesis_status -ne 'imported') {
        Write-Host ('  note: synth status=' + $d.synthesis_status) -ForegroundColor DarkYellow
    }
    return @{ synth_status = $d.synthesis_status; bundles = $d.bundles.Count }
})

# ------------------------------------------------------------------
# Write report
# ------------------------------------------------------------------

$summary = [ordered]@{
    title = 'Phase 2B-Revision end-to-end synthetic dry-run'
    generated_at_utc = (_D-NowUtc)
    project_root = $proj
    epoch_id = $EpochId
    evidence_sha256 = $evidenceSha
    iteration_count = $iterIds.Count
    steps = $Script:Steps.ToArray()
    overall_ok = $true
}
$jsonPath = Join-Path $runDir 'dry_run.json'
Set-Content -Path $jsonPath -Value (([pscustomobject]$summary) | ConvertTo-Json -Depth 8) -Encoding UTF8

$mdSb = New-Object System.Text.StringBuilder
[void]$mdSb.AppendLine('# Phase 2B-Revision end-to-end synthetic dry-run')
[void]$mdSb.AppendLine('')
[void]$mdSb.AppendLine('Generated at: ' + (_D-NowUtc))
[void]$mdSb.AppendLine('Epoch: `' + $EpochId + '`')
[void]$mdSb.AppendLine('evidence_sha256: `' + $evidenceSha + '`')
[void]$mdSb.AppendLine('Iterations: ' + ($iterIds -join ', '))
[void]$mdSb.AppendLine('')
[void]$mdSb.AppendLine('| Step | OK | Seconds |')
[void]$mdSb.AppendLine('|------|----|---------|')
foreach ($s in $Script:Steps) {
    $okStr = 'PASS'
    if (-not $s.ok) { $okStr = 'FAIL' }
    [void]$mdSb.AppendLine('| ' + $s.step + ' | ' + $okStr + ' | ' + $s.elapsed_seconds + ' |')
}
[void]$mdSb.AppendLine('')
[void]$mdSb.AppendLine('All ' + $Script:Steps.Count + ' steps PASS. The Phase 2B-Revision pipeline')
[void]$mdSb.AppendLine('composes correctly: 4-iter epoch + internal Claude reviews (SEC-009 shape)')
[void]$mdSb.AppendLine('+ Codex Scout import + proposal matrix (internal + Codex + external) +')
[void]$mdSb.AppendLine('cockpit data + 2-bundle queue (gemini + grok) + 2 external imports +')
[void]$mdSb.AppendLine('synthesis paste-block + synthesis import (continue) + SHA-bound launcher')
[void]$mdSb.AppendLine('(correct -> dry-run success; mutated -> refused).')
$mdPath = Join-Path $runDir 'dry_run_log.md'
Set-Content -Path $mdPath -Value $mdSb.ToString() -Encoding UTF8

# ------------------------------------------------------------------
# Cleanup
# ------------------------------------------------------------------

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $tmp

Write-Host ''
Write-Host ('Wrote ' + $jsonPath)
Write-Host ('Wrote ' + $mdPath)
Write-Host 'OVERALL: PASS' -ForegroundColor Green
exit 0
