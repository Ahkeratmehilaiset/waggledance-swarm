#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2B P10 tests for orchestrator/Import-WaggleSynthesisResult.ps1.
    Builds a synthetic 1-iteration epoch and exercises:
      - valid continue-decision import succeeds, writes
        synthesis_result + next_claude_code_prompt.md
      - valid halt-decision import succeeds, writes HALT.md, no
        next-prompt block
      - missing synthesizer-self-id block -> .invalid
      - 0 / multiple synthesis-json blocks -> .invalid
      - missing SYNTHESIS-COMPLETE marker -> .invalid
      - schema-invalid JSON -> .invalid
      - identity-field mismatch -> .invalid
      - SHA mismatch -> .invalid
      - continue + missing next-prompt block -> .invalid
      - continue + missing MANDATORY directive line -> .invalid
      - halt + present next-prompt block -> .invalid
#>
[CmdletBinding()] param()
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'Build-WaggleEpochEvidence.ps1')
. (Join-Path $PSScriptRoot 'Import-WaggleSynthesisResult.ps1')

$Script:Pass = 0; $Script:Fail = 0
function Assert-True {
    param([string] $Name, [bool] $Cond, [string] $Detail = '')
    if ($Cond) { Write-Host "PASS  $Name" -ForegroundColor Green; $Script:Pass++ }
    else        { Write-Host "FAIL  $Name $Detail" -ForegroundColor Red; $Script:Fail++ }
}

$tmp = Join-Path $env:TEMP ("waggle-test-srim-{0}" -f ([guid]::NewGuid().ToString('N')))
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
        diff_text = "diff --git a/foo b/foo`n+a`n+b`n+c`n+d`n+e`n+f`n+g`n+h`n+i`n+j`n+k`n+l`n+m`n+n`n+o`n+p`n+q`n+r`n+s`n+t`n"
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

function New-SynthesisResponseMarkdown {
    [CmdletBinding()]
    param(
        [string] $Decision = 'continue',
        [string] $TargetIterationId,
        [string] $EpochId,
        [string] $Sha,
        [switch] $OmitSelfIdBlock,
        [switch] $OmitJsonBlock,
        [switch] $DuplicateJsonBlock,
        [switch] $OmitMarker,
        [switch] $OmitNextPrompt,
        [switch] $DuplicateNextPrompt,
        [switch] $OmitMandatoryLine,
        [switch] $IncludeNextPromptOnHalt
    )
    $obj = [ordered]@{
        synthesizer_self_id = [ordered]@{
            claimed_model_name = 'Synthetic GPT'
            claimed_version = $null
            uses_extended_thinking_or_reasoning_mode = $true
        }
        target_iteration_id = $TargetIterationId
        epoch_id = $EpochId
        source_evidence_sha256 = $Sha
        synthesis_summary = 'Synthetic synthesis summary.'
        included_review_imports = @(
            [ordered]@{ import_id = 'imp_a'; provider = 'claude_web'; role = 'architect'; weight_in_synthesis = 'normal' }
            [ordered]@{ import_id = 'imp_b'; provider = 'gemini';     role = 'security';  weight_in_synthesis = 'normal' }
            [ordered]@{ import_id = 'imp_c'; provider = 'grok';       role = 'reliability'; weight_in_synthesis = 'normal' }
        )
        excluded_review_imports = @()
        consolidated_findings = @()
        consolidated_proposals = @()
        decision = $Decision
        halt_marker = if ($Decision -eq 'halt') { 'WAGGLE_HALT' } else { $null }
        next_claude_code_prompt_block_marker = 'next-claude-code-prompt'
        completed = $true
    }
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.AppendLine('# Synthesis response (synthetic)')
    if (-not $OmitSelfIdBlock) {
        [void]$sb.AppendLine('```synthesizer-self-id')
        [void]$sb.AppendLine('I am ' + $obj.synthesizer_self_id.claimed_model_name + '.')
        [void]$sb.AppendLine('```')
        [void]$sb.AppendLine('')
    }
    if (-not $OmitJsonBlock) {
        [void]$sb.AppendLine('```synthesis-json')
        [void]$sb.AppendLine(([pscustomobject]$obj | ConvertTo-Json -Depth 16))
        [void]$sb.AppendLine('```')
    }
    if ($DuplicateJsonBlock) {
        [void]$sb.AppendLine('')
        [void]$sb.AppendLine('```synthesis-json')
        [void]$sb.AppendLine(([pscustomobject]$obj | ConvertTo-Json -Depth 16))
        [void]$sb.AppendLine('```')
    }
    $emitNextPrompt = ($Decision -eq 'continue' -and -not $OmitNextPrompt) -or $IncludeNextPromptOnHalt
    if ($emitNextPrompt) {
        [void]$sb.AppendLine('')
        [void]$sb.AppendLine('```next-claude-code-prompt')
        if (-not $OmitMandatoryLine) {
            [void]$sb.AppendLine($MandLine)
        }
        [void]$sb.AppendLine('Implement task X in repo. Touch file foo.py. Add test test_foo.py.')
        [void]$sb.AppendLine('```')
    }
    if ($DuplicateNextPrompt) {
        [void]$sb.AppendLine('')
        [void]$sb.AppendLine('```next-claude-code-prompt')
        [void]$sb.AppendLine($MandLine)
        [void]$sb.AppendLine('Duplicate next-prompt content.')
        [void]$sb.AppendLine('```')
    }
    if (-not $OmitMarker) {
        [void]$sb.AppendLine('')
        [void]$sb.AppendLine('SYNTHESIS-COMPLETE')
    }
    return $sb.ToString()
}

# ---- Build epoch -------------------------------------------------------

$proj = New-FakeProject -Name 'srim1'
$iid = '2026-05-07_07-00-00'
[void](New-FakeIteration -Root $proj.root -Id $iid)
$ev = Build-WaggleEpochEvidence -ConfigPath $proj.cfg -IterationIds @($iid)
$EpochId = $ev.epoch_id
$Sha = $ev.evidence_sha256

$resDir = Join-Path $proj.root 'tmp_synth_responses'
[void](New-Item -ItemType Directory -Path $resDir -Force)
function Write-Response { param([string] $Name, [string] $Body); $p = Join-Path $resDir ($Name + '.md'); Set-Content -Path $p -Value $Body -Encoding UTF8; return $p }

# ---- Test 1: valid continue ------------------------------------------

$bodyOk = New-SynthesisResponseMarkdown -Decision 'continue' -TargetIterationId $iid -EpochId $EpochId -Sha $Sha
$rspOk = Write-Response -Name 'continue_ok' -Body $bodyOk
$r = Import-WaggleSynthesisResult -ConfigPath $proj.cfg -EpochId $EpochId -IterationId $iid -ResponseFile $rspOk
Assert-True 'continue: ok=true'                 ($r.ok -eq $true)
Assert-True 'continue: decision=continue'       ($r.decision -eq 'continue')
Assert-True 'continue: json_path exists'        (Test-Path -LiteralPath $r.json_path)
Assert-True 'continue: next_prompt_path exists' (Test-Path -LiteralPath $r.next_prompt_path)
$nextBody = Get-Content -Raw -Path $r.next_prompt_path -Encoding UTF8
Assert-True 'continue: next_prompt starts with MANDATORY line' ($nextBody.StartsWith($MandLine))

# ---- Test 2: valid halt ----------------------------------------------

$bodyHalt = New-SynthesisResponseMarkdown -Decision 'halt' -TargetIterationId $iid -EpochId $EpochId -Sha $Sha
$rspHalt = Write-Response -Name 'halt_ok' -Body $bodyHalt
$r2 = Import-WaggleSynthesisResult -ConfigPath $proj.cfg -EpochId $EpochId -IterationId $iid -ResponseFile $rspHalt
Assert-True 'halt: ok=true'                      ($r2.ok -eq $true)
Assert-True 'halt: decision=halt'                ($r2.decision -eq 'halt')
Assert-True 'halt: HALT.md path returned'        ([string]$r2.halt_path -ne '')
Assert-True 'halt: HALT.md exists'               (Test-Path -LiteralPath $r2.halt_path)
$haltBody = Get-Content -Raw -Path $r2.halt_path -Encoding UTF8
Assert-True 'halt: HALT.md contains halt_marker WAGGLE_HALT' ($haltBody -match 'WAGGLE_HALT')
Assert-True 'halt: no next_prompt_path'          ([string]$r2.next_prompt_path -eq '')

# ---- Test 3: missing synthesizer-self-id -----------------------------

$body3 = New-SynthesisResponseMarkdown -Decision 'continue' -TargetIterationId $iid -EpochId $EpochId -Sha $Sha -OmitSelfIdBlock
$rsp3 = Write-Response -Name 'no_selfid' -Body $body3
$r3 = Import-WaggleSynthesisResult -ConfigPath $proj.cfg -EpochId $EpochId -IterationId $iid -ResponseFile $rsp3
Assert-True 'no-selfid: ok=false' ($r3.ok -eq $false)
Assert-True 'no-selfid: reason=synthesizer_self_id_block_missing' ($r3.reason -eq 'synthesizer_self_id_block_missing')

# ---- Test 4: missing JSON / multiple JSON ----------------------------

$body4a = New-SynthesisResponseMarkdown -Decision 'continue' -TargetIterationId $iid -EpochId $EpochId -Sha $Sha -OmitJsonBlock
$rsp4a = Write-Response -Name 'no_json' -Body $body4a
$r4a = Import-WaggleSynthesisResult -ConfigPath $proj.cfg -EpochId $EpochId -IterationId $iid -ResponseFile $rsp4a
Assert-True 'no-json: ok=false'                                       ($r4a.ok -eq $false)
Assert-True 'no-json: reason=synthesis_json_block_missing_or_multiple' ($r4a.reason -eq 'synthesis_json_block_missing_or_multiple')

$body4b = New-SynthesisResponseMarkdown -Decision 'continue' -TargetIterationId $iid -EpochId $EpochId -Sha $Sha -DuplicateJsonBlock
$rsp4b = Write-Response -Name 'dup_json' -Body $body4b
$r4b = Import-WaggleSynthesisResult -ConfigPath $proj.cfg -EpochId $EpochId -IterationId $iid -ResponseFile $rsp4b
Assert-True 'dup-json: ok=false'                                       ($r4b.ok -eq $false)
Assert-True 'dup-json: reason=synthesis_json_block_missing_or_multiple' ($r4b.reason -eq 'synthesis_json_block_missing_or_multiple')

# ---- Test 5: missing SYNTHESIS-COMPLETE marker ----------------------

$body5 = New-SynthesisResponseMarkdown -Decision 'continue' -TargetIterationId $iid -EpochId $EpochId -Sha $Sha -OmitMarker
$rsp5 = Write-Response -Name 'no_marker' -Body $body5
$r5 = Import-WaggleSynthesisResult -ConfigPath $proj.cfg -EpochId $EpochId -IterationId $iid -ResponseFile $rsp5
Assert-True 'no-marker: ok=false' ($r5.ok -eq $false)
Assert-True 'no-marker: reason=synthesis_completion_marker_missing' ($r5.reason -eq 'synthesis_completion_marker_missing')

# ---- Test 6: identity-field mismatch -------------------------------

$body6 = New-SynthesisResponseMarkdown -Decision 'continue' -TargetIterationId 'WRONG_ID' -EpochId $EpochId -Sha $Sha
$rsp6 = Write-Response -Name 'id_mismatch' -Body $body6
$r6 = Import-WaggleSynthesisResult -ConfigPath $proj.cfg -EpochId $EpochId -IterationId $iid -ResponseFile $rsp6
Assert-True 'id-mismatch: ok=false' ($r6.ok -eq $false)
Assert-True 'id-mismatch: reason=identity_fields_mismatch' ($r6.reason -eq 'identity_fields_mismatch')

# ---- Test 7: SHA mismatch ------------------------------------------

$bogus = ('e' * 64)
$body7 = New-SynthesisResponseMarkdown -Decision 'continue' -TargetIterationId $iid -EpochId $EpochId -Sha $bogus
$rsp7 = Write-Response -Name 'sha_mismatch' -Body $body7
$r7 = Import-WaggleSynthesisResult -ConfigPath $proj.cfg -EpochId $EpochId -IterationId $iid -ResponseFile $rsp7
Assert-True 'sha-mismatch: ok=false' ($r7.ok -eq $false)
Assert-True 'sha-mismatch: reason=source_evidence_sha256_mismatch' ($r7.reason -eq 'source_evidence_sha256_mismatch')

# ---- Test 8: continue + missing next-prompt block --------------------

$body8 = New-SynthesisResponseMarkdown -Decision 'continue' -TargetIterationId $iid -EpochId $EpochId -Sha $Sha -OmitNextPrompt
$rsp8 = Write-Response -Name 'continue_no_next' -Body $body8
$r8 = Import-WaggleSynthesisResult -ConfigPath $proj.cfg -EpochId $EpochId -IterationId $iid -ResponseFile $rsp8
Assert-True 'continue-no-next: ok=false' ($r8.ok -eq $false)
Assert-True 'continue-no-next: reason=continue_decision_requires_single_next_prompt_block' ($r8.reason -eq 'continue_decision_requires_single_next_prompt_block')

# ---- Test 9: continue + duplicate next-prompt block ------------------

$body9 = New-SynthesisResponseMarkdown -Decision 'continue' -TargetIterationId $iid -EpochId $EpochId -Sha $Sha -DuplicateNextPrompt
$rsp9 = Write-Response -Name 'continue_dup_next' -Body $body9
$r9 = Import-WaggleSynthesisResult -ConfigPath $proj.cfg -EpochId $EpochId -IterationId $iid -ResponseFile $rsp9
Assert-True 'continue-dup-next: ok=false' ($r9.ok -eq $false)
Assert-True 'continue-dup-next: reason=continue_decision_requires_single_next_prompt_block' ($r9.reason -eq 'continue_decision_requires_single_next_prompt_block')

# ---- Test 10: continue + missing MANDATORY line ----------------------

$body10 = New-SynthesisResponseMarkdown -Decision 'continue' -TargetIterationId $iid -EpochId $EpochId -Sha $Sha -OmitMandatoryLine
$rsp10 = Write-Response -Name 'no_mand' -Body $body10
$r10 = Import-WaggleSynthesisResult -ConfigPath $proj.cfg -EpochId $EpochId -IterationId $iid -ResponseFile $rsp10
Assert-True 'no-mand: ok=false' ($r10.ok -eq $false)
Assert-True 'no-mand: reason=next_prompt_mandatory_directive_missing' ($r10.reason -eq 'next_prompt_mandatory_directive_missing')

# ---- Test 11: halt + presence of next-prompt block -------------------

$body11 = New-SynthesisResponseMarkdown -Decision 'halt' -TargetIterationId $iid -EpochId $EpochId -Sha $Sha -IncludeNextPromptOnHalt
$rsp11 = Write-Response -Name 'halt_with_next' -Body $body11
$r11 = Import-WaggleSynthesisResult -ConfigPath $proj.cfg -EpochId $EpochId -IterationId $iid -ResponseFile $rsp11
Assert-True 'halt-with-next: ok=false' ($r11.ok -eq $false)
Assert-True 'halt-with-next: reason=halt_decision_but_prompt_block_present' ($r11.reason -eq 'halt_decision_but_prompt_block_present')

# ---- Cleanup ----------------------------------------------------------

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $tmp

Write-Host ''
Write-Host "Result: $Script:Pass/$($Script:Pass + $Script:Fail) tests passed" -ForegroundColor Cyan
if ($Script:Fail -gt 0) { exit 1 } else { exit 0 }
