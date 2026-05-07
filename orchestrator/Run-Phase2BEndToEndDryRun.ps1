#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2B P16: end-to-end synthetic dry-run that exercises the
    whole cross-vendor iteration cycle from epoch evidence to
    next-iteration launch, using a synthetic project under $env:TEMP
    so it has no side effects on the real repo. Produces a Markdown
    report at docs/runs/orchestrator_phase2b_cross_vendor_2026_05_06/
    e2e_dry_run.md plus a sibling .json record.

    The purpose is to prove the surface composes end-to-end on a
    clean machine: bundler -> queue exporter -> 3 reviewer imports
    -> synthesis paste-block -> synthesis import -> next iteration
    launch. Halt-decision and SHA-mismatch sub-paths are also
    exercised.
#>
[CmdletBinding()] param()
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$runDir = Join-Path $repoRoot 'docs/runs/orchestrator_phase2b_cross_vendor_2026_05_06'
if (-not (Test-Path -LiteralPath $runDir)) {
    New-Item -ItemType Directory -Path $runDir -Force | Out-Null
}

. (Join-Path $PSScriptRoot 'Build-WaggleEpochEvidence.ps1')
. (Join-Path $PSScriptRoot 'Export-WaggleExternalReviewQueue.ps1')
. (Join-Path $PSScriptRoot 'Import-WaggleExternalReviewResponse.ps1')
. (Join-Path $PSScriptRoot 'New-WaggleSynthesisPasteBlock.ps1')
. (Join-Path $PSScriptRoot 'Import-WaggleSynthesisResult.ps1')
. (Join-Path $PSScriptRoot 'New-WaggleIterationFromSynthesis.ps1')
. (Join-Path $PSScriptRoot 'lib/external_review/EpochCycleTrigger.ps1')

$tmp = Join-Path $env:TEMP ("waggle-e2e-{0}" -f ([guid]::NewGuid().ToString('N')))
[void](New-Item -ItemType Directory -Path $tmp -Force)

$Steps = New-Object System.Collections.Generic.List[object]
function Step {
    param([string] $Name, [scriptblock] $Body)
    $tStart = Get-Date
    $ok = $true; $err = ''; $detail = $null
    try {
        $detail = & $Body
    } catch {
        $ok = $false; $err = $_.Exception.Message
    }
    $elapsed = [Math]::Round(((Get-Date) - $tStart).TotalSeconds, 2)
    $rec = [pscustomobject]@{
        step = $Name; ok = $ok; elapsed_seconds = $elapsed; error = $err
    }
    $Steps.Add($rec) | Out-Null
    if ($ok) { Write-Host ("[ok] {0,-50} {1,6:n2}s" -f $Name, $elapsed) -ForegroundColor Green }
    else      { Write-Host ("[!!] {0,-50} {1,6:n2}s ({2})" -f $Name, $elapsed, $err) -ForegroundColor Red; throw $err }
    return $detail
}

# ---- Synthetic project + 3 iterations ----------------------------------

$proj = $null
$iids = @('2026-05-07_e2e-01','2026-05-07_e2e-02','2026-05-07_e2e-03')
$cfgPath = $null

[void](Step 'P16-1: build synthetic project + 3 iterations' {
    $root = Join-Path $tmp 'proj'
    [void](New-Item -ItemType Directory -Path (Join-Path $root 'state') -Force)
    [void](New-Item -ItemType Directory -Path (Join-Path $root 'iterations') -Force)
    Set-Content -Path (Join-Path $root 'raportti.md') -Value '# raportti' -Encoding UTF8
    foreach ($iid in $iids) {
        $iterDir = Join-Path (Join-Path $root 'iterations') $iid
        [void](New-Item -ItemType Directory -Path $iterDir -Force)
        Set-Content -Path (Join-Path $iterDir 'state.json') -Value (@{ iteration_id = $iid; status = 'COMPLETED' } | ConvertTo-Json) -Encoding UTF8
        Set-Content -Path (Join-Path $iterDir 'run_metadata.json') -Value '{}' -Encoding UTF8
        Set-Content -Path (Join-Path $iterDir 'claude_stdout.txt') -Value 'normal stdout' -Encoding UTF8
        [System.IO.File]::WriteAllText((Join-Path $iterDir 'claude_stderr.txt'), '')
        Set-Content -Path (Join-Path $iterDir 'git_metadata.json') -Value (@{
            commit = '1234567890abcdef1234567890abcdef12345678'
            diff_text = "diff --git a/foo$iid b/foo$iid`n+a`n+b`n+c`n+d`n+e`n+f`n+g`n+h`n+i`n+j`n+k`n+l`n+m`n+n`n+o`n+p`n"
        } | ConvertTo-Json) -Encoding UTF8
        [void](New-Item -ItemType Directory -Path (Join-Path $iterDir 'reviews') -Force)
        foreach ($role in 'architect','security','reliability') {
            $j = @{
                role = $role; target_iteration_id = $iid; verdict = 'pass'
                findings = @(); summary = "fake $role"
                metrics = @{ files_reviewed = 1; lines_reviewed = 1; review_duration_seconds = 1 }
                completed = $true; source_package_path = "iterations/$iid/llm_input_package.md"
            }
            Set-Content -Path (Join-Path (Join-Path $iterDir 'reviews') ($role + '.json')) -Value ($j | ConvertTo-Json -Depth 6) -Encoding UTF8
            Set-Content -Path (Join-Path (Join-Path $iterDir 'reviews') ($role + '.md')) -Value "# $role review of $iid" -Encoding UTF8
        }
    }
    # Copy prompt templates into the project (queue exporter looks in projectRoot first).
    $promptsDir = Join-Path $root 'prompts/external_review'
    [void](New-Item -ItemType Directory -Path (Join-Path $promptsDir 'providers') -Force)
    foreach ($f in 'architect.md','security.md','reliability.md','synthesis_gpt.md') {
        Copy-Item -LiteralPath (Join-Path $repoRoot ('prompts/external_review/' + $f)) -Destination (Join-Path $promptsDir $f) -Force
    }
    foreach ($f in 'claude_web.md','gemini.md','grok.md','gpt.md') {
        Copy-Item -LiteralPath (Join-Path $repoRoot ('prompts/external_review/providers/' + $f)) -Destination (Join-Path (Join-Path $promptsDir 'providers') $f) -Force
    }
    $cfg = @{
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
                claude_web    = @{ enabled = $true;  timeout_sec = 600;  expected_model_in_ui = 'Claude Opus 4.7 (Max plan)' }
                gemini        = @{ enabled = $true;  timeout_sec = 600;  expected_model_in_ui = 'Gemini Pro Advanced' }
                grok          = @{ enabled = $true;  timeout_sec = 900;  expected_model_in_ui = 'Grok Expert mode' }
                gpt_synthesis = @{ enabled = $true;  timeout_sec = 4800; expected_model_in_ui = 'GPT Pro 5.5 Extended Thinking' }
            }
            auto_approval_rule = 'all_reviewers_below_needs_changes'
            manual_pause_flag_relative = 'state/pause_external_review.flag'
            halt_marker = 'WAGGLE_HALT'; session_resume_threshold_hours = 4
        }
        iteration_cycle = @{
            local_iterations_per_external_review = 3; max_iterations_per_session = 50
            early_trigger_on_regression = $true; early_trigger_on_hardening_gate_failure = $true
            early_trigger_on_internal_critical_finding = $true
            early_trigger_on_no_work_consecutive = 2
            no_work_diff_min_bytes = 1; no_work_raportti_min_bytes = 1
            no_work_stdout_min_meaningful_bytes = 100
        }
        models = @{
            claude_code = 'claude-opus-4-7'; claude_web = 'Claude Opus 4.7 (Max plan)'
            gemini = 'Gemini Pro Advanced'; grok = 'Grok Expert mode'
            gpt_synthesis = 'GPT Pro 5.5 Extended Thinking'
        }
    }
    $cfgPathL = Join-Path $root 'orchestrator.config.json'
    Set-Content -Path $cfgPathL -Value ($cfg | ConvertTo-Json -Depth 10) -Encoding UTF8
    $script:proj = $root
    $script:cfgPath = $cfgPathL
    return @{ project_root = $root }
})

# ---- Trigger library decides 'trigger' on 3 healthy iterations --------

[void](Step 'P16-2: trigger library returns ''trigger'' for 3-iter window' {
    $records = @()
    foreach ($iid in $iids) {
        $records += [pscustomobject]@{
            iteration_id = $iid; status = 'COMPLETED'; no_work_classification = $false
            hardening_gates_failure_present = $false
            internal_review_verdicts = [pscustomobject]@{ architect = 'pass'; security = 'pass'; reliability = 'pass' }
            internal_findings_severities = @()
        }
    }
    . (Join-Path $PSScriptRoot 'lib/external_review/ProviderProfiles.ps1')
    $cfg = Get-Content -Raw -Path $cfgPath -Encoding UTF8 | ConvertFrom-Json
    $ic = Get-WaggleIterationCycleConfig -Config $cfg
    $er = Get-WaggleExternalReviewConfig -Config $cfg
    $d = Get-WaggleEpochCycleDecision -IterationsSinceLastTrigger $records `
            -IterationCycle $ic -ExternalReview $er -ProjectRoot $proj
    if ($d.decision -ne 'trigger') { throw "expected trigger, got $($d.decision)" }
    return @{ decision = $d.decision; reasons = $d.reasons }
})

# ---- Build epoch evidence ---------------------------------------------

$ev = $null; $EpochId = $null; $Sha = $null
[void](Step 'P16-3: Build-WaggleEpochEvidence on 3 iterations' {
    $script:ev = Build-WaggleEpochEvidence -ConfigPath $cfgPath -IterationIds $iids
    if ($ev.evidence_sha256 -notmatch '^[a-f0-9]{64}$') { throw "bad evidence_sha256" }
    $script:EpochId = $ev.epoch_id
    $script:Sha = $ev.evidence_sha256
    return @{ epoch_id = $EpochId; evidence_sha256 = $Sha }
})

# ---- Export per-provider queue ----------------------------------------

$exp = $null
[void](Step 'P16-4: Export-WaggleExternalReviewQueue produces 3 bundles' {
    $script:exp = Export-WaggleExternalReviewQueue -ConfigPath $cfgPath -EvidenceJsonPath $ev.epoch_json_path
    if (-not $exp.ok) { throw "queue export failed" }
    if (@($exp.bundles).Count -lt 3) { throw "expected 3 bundles, got $((@($exp.bundles).Count))" }
    return @{ bundles_count = @($exp.bundles).Count; queue_dir = $exp.queue_manifest_path }
})

# ---- Compose 3 synthetic reviewer responses + import each --------------

$importedIds = @()
[void](Step 'P16-5: import 3 synthetic reviewer responses' {
    $rspDir = Join-Path $tmp 'responses'
    [void](New-Item -ItemType Directory -Path $rspDir -Force)
    $iidLast = $iids[$iids.Count - 1]
    $pairs = @(
        @{ provider = 'claude_web'; role = 'architect' }
        @{ provider = 'gemini';     role = 'security' }
        @{ provider = 'grok';       role = 'reliability' }
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
            source_evidence_sha256 = $Sha
            reviewer_summary = "synthetic $($p.role) summary"
            verdict = 'pass'
            findings = @(); suggested_next_actions = @()
            confidence = 'medium'; limitations = 'synthetic'; completed = $true
        }
        $sb = New-Object System.Text.StringBuilder
        [void]$sb.AppendLine('# Synthetic reviewer response')
        [void]$sb.AppendLine('```reviewer-self-id'); [void]$sb.AppendLine('Synthetic.'); [void]$sb.AppendLine('```'); [void]$sb.AppendLine('')
        [void]$sb.AppendLine('```external-review-json')
        [void]$sb.AppendLine(([pscustomobject]$obj | ConvertTo-Json -Depth 16))
        [void]$sb.AppendLine('```'); [void]$sb.AppendLine('')
        [void]$sb.AppendLine('EXTERNAL-REVIEW-COMPLETE')
        $rsp = Join-Path $rspDir ($p.provider + '_' + $p.role + '.md')
        Set-Content -Path $rsp -Value $sb.ToString() -Encoding UTF8
        $r = Import-WaggleExternalReviewResponse -ConfigPath $cfgPath -EpochId $EpochId `
                -Provider $p.provider -Role $p.role -ResponseFile $rsp -IterationId $iidLast
        if (-not $r.ok) { throw "import failed for $($p.provider)/$($p.role): $($r.reason)" }
        $script:importedIds += $r.import_id
    }
    return @{ imported_count = $importedIds.Count }
})

# ---- Build synthesis paste-block ---------------------------------------

$spb = $null
[void](Step 'P16-6: New-WaggleSynthesisPasteBlock' {
    $iidLast = $iids[$iids.Count - 1]
    $script:spb = New-WaggleSynthesisPasteBlock -ConfigPath $cfgPath -EpochId $EpochId -IterationId $iidLast
    if (-not $spb.ok) { throw "paste-block failed" }
    return @{ paste_block_path = $spb.paste_block_path; attachments = $spb.attachments.Count }
})

# ---- Compose synthetic GPT synthesis (continue) + import --------------

$MandLine = 'MANDATORY: Use Claude Opus 4.7 (--model claude-opus-4-7) for this iteration. Do not downgrade.'
$synthImp = $null
[void](Step 'P16-7: import synthetic GPT synthesis (decision=continue)' {
    $iidLast = $iids[$iids.Count - 1]
    $obj = [ordered]@{
        synthesizer_self_id = [ordered]@{ claimed_model_name = 'Synthetic GPT'; claimed_version = $null; uses_extended_thinking_or_reasoning_mode = $true }
        target_iteration_id = $iidLast; epoch_id = $EpochId; source_evidence_sha256 = $Sha
        synthesis_summary = 'Synthetic synthesis summary.'
        included_review_imports = @($importedIds | ForEach-Object {
            [ordered]@{ import_id = $_; provider = 'claude_web'; role = 'architect'; weight_in_synthesis = 'normal' }
        })
        excluded_review_imports = @()
        consolidated_findings = @(); consolidated_proposals = @()
        decision = 'continue'; halt_marker = $null
        next_claude_code_prompt_block_marker = 'next-claude-code-prompt'
        completed = $true
    }
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.AppendLine('# Synthetic synthesis')
    [void]$sb.AppendLine('```synthesizer-self-id'); [void]$sb.AppendLine('Synthetic GPT.'); [void]$sb.AppendLine('```'); [void]$sb.AppendLine('')
    [void]$sb.AppendLine('```synthesis-json')
    [void]$sb.AppendLine(([pscustomobject]$obj | ConvertTo-Json -Depth 16))
    [void]$sb.AppendLine('```'); [void]$sb.AppendLine('')
    [void]$sb.AppendLine('```next-claude-code-prompt')
    [void]$sb.AppendLine($MandLine)
    [void]$sb.AppendLine('Implement task X. Touch foo.py. Add test test_foo.py.')
    [void]$sb.AppendLine('```'); [void]$sb.AppendLine('')
    [void]$sb.AppendLine('SYNTHESIS-COMPLETE')

    $rspPath = Join-Path $tmp 'gpt_response.md'
    Set-Content -Path $rspPath -Value $sb.ToString() -Encoding UTF8
    $script:synthImp = Import-WaggleSynthesisResult -ConfigPath $cfgPath -EpochId $EpochId -IterationId $iidLast -ResponseFile $rspPath
    if (-not $synthImp.ok) { throw "synthesis import failed: $($synthImp.reason)" }
    return @{ synthesis_import_id = $synthImp.synthesis_import_id; decision = $synthImp.decision }
})

# ---- Launch new iteration (DryRun then real) ---------------------------

$newIter = $null
[void](Step 'P16-8: New-WaggleIterationFromSynthesis (dry-run + real)' {
    $iidLast = $iids[$iids.Count - 1]
    $dr = New-WaggleIterationFromSynthesis -ConfigPath $cfgPath -EpochId $EpochId `
            -IterationId $iidLast -SynthesisImportId $synthImp.synthesis_import_id -DryRun
    if (-not $dr.ok -or -not $dr.dry_run) { throw "dry-run failed" }
    $script:newIter = New-WaggleIterationFromSynthesis -ConfigPath $cfgPath -EpochId $EpochId `
            -IterationId $iidLast -SynthesisImportId $synthImp.synthesis_import_id
    if (-not $newIter.ok) { throw "real launch failed" }
    if (-not (Test-Path -LiteralPath $newIter.prompt_path)) { throw "prompt_path missing" }
    return @{ new_iteration_id = $newIter.new_iteration_id }
})

# ---- HALT path: synth says halt, launcher refuses ---------------------

[void](Step 'P16-9: halt path produces HALT.md and refuses launcher' {
    $iidLast = $iids[$iids.Count - 1]
    $obj = [ordered]@{
        synthesizer_self_id = [ordered]@{ claimed_model_name = 'Synthetic GPT'; claimed_version = $null; uses_extended_thinking_or_reasoning_mode = $true }
        target_iteration_id = $iidLast; epoch_id = $EpochId; source_evidence_sha256 = $Sha
        synthesis_summary = 'work complete'
        included_review_imports = @(); excluded_review_imports = @()
        consolidated_findings = @(); consolidated_proposals = @()
        decision = 'halt'; halt_marker = 'WAGGLE_HALT'
        next_claude_code_prompt_block_marker = 'next-claude-code-prompt'
        completed = $true
    }
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.AppendLine('# Halt')
    [void]$sb.AppendLine('```synthesizer-self-id'); [void]$sb.AppendLine('Synthetic GPT.'); [void]$sb.AppendLine('```'); [void]$sb.AppendLine('')
    [void]$sb.AppendLine('```synthesis-json'); [void]$sb.AppendLine(([pscustomobject]$obj | ConvertTo-Json -Depth 16)); [void]$sb.AppendLine('```'); [void]$sb.AppendLine('')
    [void]$sb.AppendLine('SYNTHESIS-COMPLETE')
    $rsp = Join-Path $tmp 'gpt_halt.md'
    Set-Content -Path $rsp -Value $sb.ToString() -Encoding UTF8
    $imp = Import-WaggleSynthesisResult -ConfigPath $cfgPath -EpochId $EpochId -IterationId $iidLast -ResponseFile $rsp
    if (-not $imp.ok) { throw "halt import failed: $($imp.reason)" }
    if (-not (Test-Path -LiteralPath $imp.halt_path)) { throw "HALT.md not written" }
    $threw = $false
    try {
        [void](New-WaggleIterationFromSynthesis -ConfigPath $cfgPath -EpochId $EpochId `
                -IterationId $iidLast -SynthesisImportId $imp.synthesis_import_id)
    } catch { $threw = $true }
    if (-not $threw) { throw "halt launcher should refuse" }
    return @{ halt_path = $imp.halt_path }
})

# ---- SHA mismatch path: mutate evidence; launcher refuses --------------

[void](Step 'P16-10: SHA mismatch refuses launcher' {
    $iidLast = $iids[$iids.Count - 1]
    $evDir = Join-Path (Join-Path (Join-Path $proj 'iterations') $iidLast) ('external_reviews/epoch_' + $EpochId + '/evidence')
    $diff = Join-Path $evDir 'cumulative_diff.patch'
    Add-Content -Path $diff -Value "`n# tampered to break SHA`n" -Encoding UTF8
    $threw = $false; $msg = ''
    try {
        [void](New-WaggleIterationFromSynthesis -ConfigPath $cfgPath -EpochId $EpochId `
                -IterationId $iidLast -SynthesisImportId $synthImp.synthesis_import_id `
                -NewIterationId 'sha_mismatch_iter')
    } catch { $threw = $true; $msg = $_.Exception.Message }
    if (-not $threw) { throw "expected SHA-mismatch refusal" }
    if ($msg -notmatch 'source_evidence_sha256 mismatch') { throw "unexpected message: $msg" }
    return @{ refused = $true }
})

# ---- Write report ------------------------------------------------------

$summary = [ordered]@{
    title = 'Phase 2B end-to-end synthetic dry-run'
    generated_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    project_root = $proj
    epoch_id = $EpochId
    evidence_sha256 = $Sha
    new_iteration_id = if ($newIter) { $newIter.new_iteration_id } else { '' }
    steps = $Steps.ToArray()
    overall_ok = $true
}
$jsonPath = Join-Path $runDir 'e2e_dry_run.json'
Set-Content -Path $jsonPath -Value (([pscustomobject]$summary) | ConvertTo-Json -Depth 8) -Encoding UTF8

$mdSb = New-Object System.Text.StringBuilder
[void]$mdSb.AppendLine('# Phase 2B end-to-end synthetic dry-run')
[void]$mdSb.AppendLine('')
[void]$mdSb.AppendLine('Generated at: ' + (Get-Date).ToUniversalTime().ToString('o'))
[void]$mdSb.AppendLine('Epoch: `' + $EpochId + '`')
[void]$mdSb.AppendLine('evidence_sha256: `' + $Sha + '`')
[void]$mdSb.AppendLine('')
[void]$mdSb.AppendLine('| Step | OK | Seconds |')
[void]$mdSb.AppendLine('|------|----|---------|')
foreach ($s in $Steps) {
    $okStr = if ($s.ok) { 'PASS' } else { 'FAIL' }
    [void]$mdSb.AppendLine('| ' + $s.step + ' | ' + $okStr + ' | ' + $s.elapsed_seconds + ' |')
}
[void]$mdSb.AppendLine('')
[void]$mdSb.AppendLine('All ' + $Steps.Count + ' steps PASS. The end-to-end pipeline composes correctly.')
$mdPath = Join-Path $runDir 'e2e_dry_run.md'
Set-Content -Path $mdPath -Value $mdSb.ToString() -Encoding UTF8

# ---- Cleanup -----------------------------------------------------------

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $tmp

Write-Host ''
Write-Host ('Wrote ' + $jsonPath)
Write-Host ('Wrote ' + $mdPath)
Write-Host 'OVERALL: PASS' -ForegroundColor Green
exit 0
