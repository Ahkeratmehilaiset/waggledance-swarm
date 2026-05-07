#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2B P12 tests for orchestrator/New-WaggleIterationFromSynthesis.ps1.
    Builds a 1-iter epoch, imports a synthetic synthesis result, then
    exercises:
      - DryRun mode: ok=true, no folder written
      - Real launch: new iteration folder + iteration_prompt.md + state.json
      - SHA mismatch (evidence mutated post-import) -> throws
      - Decision != continue (halt) -> throws
      - Existing new_iteration_id collision -> throws
#>
[CmdletBinding()] param()
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'Build-WaggleEpochEvidence.ps1')
. (Join-Path $PSScriptRoot 'Import-WaggleSynthesisResult.ps1')
. (Join-Path $PSScriptRoot 'New-WaggleIterationFromSynthesis.ps1')

$Script:Pass = 0; $Script:Fail = 0
function Assert-True {
    param([string] $Name, [bool] $Cond, [string] $Detail = '')
    if ($Cond) { Write-Host "PASS  $Name" -ForegroundColor Green; $Script:Pass++ }
    else        { Write-Host "FAIL  $Name $Detail" -ForegroundColor Red; $Script:Fail++ }
}

$tmp = Join-Path $env:TEMP ("waggle-test-ifs-{0}" -f ([guid]::NewGuid().ToString('N')))
[void](New-Item -ItemType Directory -Path $tmp -Force)

function New-FakeIteration {
    param([string] $Root, [string] $Id)
    $iterDir = Join-Path (Join-Path $Root 'iterations') $Id
    [void](New-Item -ItemType Directory -Path $iterDir -Force)
    Set-Content -Path (Join-Path $iterDir 'state.json') -Value (@{ iteration_id = $Id; status = 'COMPLETED' } | ConvertTo-Json) -Encoding UTF8
    Set-Content -Path (Join-Path $iterDir 'run_metadata.json') -Value '{}' -Encoding UTF8
    Set-Content -Path (Join-Path $iterDir 'claude_stdout.txt') -Value 'normal stdout' -Encoding UTF8
    [System.IO.File]::WriteAllText((Join-Path $iterDir 'claude_stderr.txt'), '')
    Set-Content -Path (Join-Path $iterDir 'git_metadata.json') -Value (@{
        commit = '1234567890abcdef1234567890abcdef12345678'
        diff_text = "diff --git a/foo b/foo`n+a`n+b`n+c`n+d`n+e`n+f`n+g`n+h`n+i`n+j`n+k`n+l`n+m`n"
    } | ConvertTo-Json) -Encoding UTF8
    [void](New-Item -ItemType Directory -Path (Join-Path $iterDir 'reviews') -Force)
    foreach ($role in 'architect','security','reliability') {
        $j = @{
            role = $role; target_iteration_id = $Id; verdict = 'pass'
            findings = @(); summary = "fake $role"
            metrics = @{ files_reviewed = 1; lines_reviewed = 1; review_duration_seconds = 1 }
            completed = $true; source_package_path = "iterations/$Id/llm_input_package.md"
        }
        Set-Content -Path (Join-Path (Join-Path $iterDir 'reviews') ($role + '.json')) -Value ($j | ConvertTo-Json -Depth 6) -Encoding UTF8
        Set-Content -Path (Join-Path (Join-Path $iterDir 'reviews') ($role + '.md')) -Value "# $role review of $Id" -Encoding UTF8
    }
    return $iterDir
}

function New-FakeProject {
    param([string] $Name)
    $root = Join-Path $tmp $Name
    [void](New-Item -ItemType Directory -Path $root -Force)
    [void](New-Item -ItemType Directory -Path (Join-Path $root 'state') -Force)
    [void](New-Item -ItemType Directory -Path (Join-Path $root 'iterations') -Force)
    Set-Content -Path (Join-Path $root 'raportti.md') -Value '# raportti' -Encoding UTF8
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
    $cfgPath = Join-Path $root 'orchestrator.config.json'
    Set-Content -Path $cfgPath -Value ($cfg | ConvertTo-Json -Depth 10) -Encoding UTF8
    return [pscustomobject]@{ root = $root; cfg = $cfgPath }
}

$MandLine = 'MANDATORY: Use Claude Opus 4.7 (--model claude-opus-4-7) for this iteration. Do not downgrade.'
function New-SynthesisResponse {
    param([string] $Decision, [string] $TargetIterationId, [string] $EpochId, [string] $Sha)
    $obj = [ordered]@{
        synthesizer_self_id = [ordered]@{ claimed_model_name = 'Synthetic GPT'; claimed_version = $null; uses_extended_thinking_or_reasoning_mode = $true }
        target_iteration_id = $TargetIterationId; epoch_id = $EpochId; source_evidence_sha256 = $Sha
        synthesis_summary = 'Summary.'
        included_review_imports = @(
            [ordered]@{ import_id = 'imp_a'; provider = 'claude_web'; role = 'architect'; weight_in_synthesis = 'normal' }
        )
        excluded_review_imports = @()
        consolidated_findings = @(); consolidated_proposals = @()
        decision = $Decision; halt_marker = if ($Decision -eq 'halt') { 'WAGGLE_HALT' } else { $null }
        next_claude_code_prompt_block_marker = 'next-claude-code-prompt'
        completed = $true
    }
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.AppendLine('# Synthesis')
    [void]$sb.AppendLine('```synthesizer-self-id'); [void]$sb.AppendLine('Synthetic GPT.'); [void]$sb.AppendLine('```'); [void]$sb.AppendLine('')
    [void]$sb.AppendLine('```synthesis-json'); [void]$sb.AppendLine(([pscustomobject]$obj | ConvertTo-Json -Depth 16)); [void]$sb.AppendLine('```'); [void]$sb.AppendLine('')
    if ($Decision -eq 'continue') {
        [void]$sb.AppendLine('```next-claude-code-prompt')
        [void]$sb.AppendLine($MandLine)
        [void]$sb.AppendLine('Implement task X. Touch foo.py. Add test test_foo.py.')
        [void]$sb.AppendLine('```')
        [void]$sb.AppendLine('')
    }
    [void]$sb.AppendLine('SYNTHESIS-COMPLETE')
    return $sb.ToString()
}

# ---- Setup: build epoch + import synthesis ---------------------------

$proj = New-FakeProject -Name 'ifs1'
$iid = '2026-05-07_08-00-00'
[void](New-FakeIteration -Root $proj.root -Id $iid)
$ev = Build-WaggleEpochEvidence -ConfigPath $proj.cfg -IterationIds @($iid)
$EpochId = $ev.epoch_id
$Sha = $ev.evidence_sha256

$rspDir = Join-Path $proj.root 'tmp_synth_responses'
[void](New-Item -ItemType Directory -Path $rspDir -Force)
$rspBody = New-SynthesisResponse -Decision 'continue' -TargetIterationId $iid -EpochId $EpochId -Sha $Sha
$rspPath = Join-Path $rspDir 'continue.md'
Set-Content -Path $rspPath -Value $rspBody -Encoding UTF8
$imp = Import-WaggleSynthesisResult -ConfigPath $proj.cfg -EpochId $EpochId -IterationId $iid -ResponseFile $rspPath
$synthId = $imp.synthesis_import_id
Assert-True 'setup: synthesis import ok' ($imp.ok -eq $true)

# ---- Test 1: DryRun mode ---------------------------------------------

$dr = New-WaggleIterationFromSynthesis -ConfigPath $proj.cfg -EpochId $EpochId `
        -IterationId $iid -SynthesisImportId $synthId -DryRun
Assert-True 'dryrun: ok=true'                          ($dr.ok -eq $true)
Assert-True 'dryrun: dry_run=true'                     ($dr.dry_run -eq $true)
Assert-True 'dryrun: sha_verified=true'                ($dr.sha_verified -eq $true)
Assert-True 'dryrun: new_iteration_folder is planned but not on disk' (-not (Test-Path -LiteralPath $dr.new_iteration_folder))

# ---- Test 2: real launch --------------------------------------------

$r = New-WaggleIterationFromSynthesis -ConfigPath $proj.cfg -EpochId $EpochId `
        -IterationId $iid -SynthesisImportId $synthId
Assert-True 'real: ok=true'                                ($r.ok -eq $true)
Assert-True 'real: new iteration folder exists'            (Test-Path -LiteralPath $r.new_iteration_folder)
Assert-True 'real: iteration_prompt.md exists'             (Test-Path -LiteralPath $r.prompt_path)
$promptStored = Get-Content -Raw -Path $r.prompt_path -Encoding UTF8
Assert-True 'real: prompt starts with MANDATORY line'      ($promptStored.StartsWith($MandLine))
$state = Get-Content -Raw -Path (Join-Path $r.new_iteration_folder 'state.json') -Encoding UTF8 | ConvertFrom-Json
Assert-True 'real: state.iteration_id matches'             ([string]$state.iteration_id -eq $r.new_iteration_id)
Assert-True 'real: state.parent.iteration_id correct'      ([string]$state.parent.iteration_id -eq $iid)
Assert-True 'real: state.parent.epoch_id correct'          ([string]$state.parent.epoch_id -eq $EpochId)
Assert-True 'real: state.parent.synthesis_import_id correct' ([string]$state.parent.synthesis_import_id -eq $synthId)
Assert-True 'real: state.parent.evidence_sha256 correct'   ([string]$state.parent.evidence_sha256 -eq $Sha)
Assert-True 'real: state.status PENDING'                   ([string]$state.status -eq 'PENDING')

# ---- Test 3: collision on existing new_iteration_id ------------------

$threw3 = $false; $emsg3 = ''
try {
    [void](New-WaggleIterationFromSynthesis -ConfigPath $proj.cfg -EpochId $EpochId `
            -IterationId $iid -SynthesisImportId $synthId -NewIterationId $r.new_iteration_id)
} catch { $threw3 = $true; $emsg3 = $_.Exception.Message }
Assert-True 'collision: refused' $threw3
Assert-True 'collision: message cites already exists' ($emsg3 -match 'already exists')

# ---- Test 4: SHA mismatch (mutate evidence after synthesis) ----------

$evDir = Join-Path (Join-Path (Join-Path $proj.root 'iterations') $iid) ('external_reviews/epoch_' + $EpochId + '/evidence')
$diffPath = Join-Path $evDir 'cumulative_diff.patch'
Add-Content -Path $diffPath -Value "`n# tampered`n" -Encoding UTF8
$threw4 = $false; $emsg4 = ''
try {
    [void](New-WaggleIterationFromSynthesis -ConfigPath $proj.cfg -EpochId $EpochId `
            -IterationId $iid -SynthesisImportId $synthId)
} catch { $threw4 = $true; $emsg4 = $_.Exception.Message }
Assert-True 'sha-mismatch: refused' $threw4
Assert-True 'sha-mismatch: message cites source_evidence_sha256' ($emsg4 -match 'source_evidence_sha256 mismatch')

# ---- Test 5: halt decision -> refuse --------------------------------

$proj5 = New-FakeProject -Name 'ifs5'
$iid5 = '2026-05-07_08-30-00'
[void](New-FakeIteration -Root $proj5.root -Id $iid5)
$ev5 = Build-WaggleEpochEvidence -ConfigPath $proj5.cfg -IterationIds @($iid5)
$rspDir5 = Join-Path $proj5.root 'tmp_synth_responses'
[void](New-Item -ItemType Directory -Path $rspDir5 -Force)
$haltBody = New-SynthesisResponse -Decision 'halt' -TargetIterationId $iid5 -EpochId $ev5.epoch_id -Sha $ev5.evidence_sha256
$haltPath = Join-Path $rspDir5 'halt.md'
Set-Content -Path $haltPath -Value $haltBody -Encoding UTF8
$haltImp = Import-WaggleSynthesisResult -ConfigPath $proj5.cfg -EpochId $ev5.epoch_id -IterationId $iid5 -ResponseFile $haltPath
Assert-True 'setup: halt synthesis imported' ($haltImp.ok -eq $true -and $haltImp.decision -eq 'halt')
$threw5 = $false; $emsg5 = ''
try {
    [void](New-WaggleIterationFromSynthesis -ConfigPath $proj5.cfg -EpochId $ev5.epoch_id `
            -IterationId $iid5 -SynthesisImportId $haltImp.synthesis_import_id)
} catch { $threw5 = $true; $emsg5 = $_.Exception.Message }
Assert-True 'halt: refused' $threw5
Assert-True "halt: message cites decision != 'continue'" ($emsg5 -match "decision != 'continue'")

# ---- Cleanup ---------------------------------------------------------

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $tmp

Write-Host ''
Write-Host "Result: $Script:Pass/$($Script:Pass + $Script:Fail) tests passed" -ForegroundColor Cyan
if ($Script:Fail -gt 0) { exit 1 } else { exit 0 }
