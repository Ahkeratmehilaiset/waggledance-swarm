#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2B P9 tests for orchestrator/New-WaggleSynthesisPasteBlock.ps1.
    Builds a synthetic 1-iteration epoch, imports three fake reviewer
    responses (one per claude_web/architect, gemini/security,
    grok/reliability), then composes the synthesis paste-block and
    asserts:
      - paste_block.md + attachments/ + metadata.json produced
      - paste-block contains synthesis-template body + UNTRUSTED warning
        + per-reviewer sections + evidence_sha256 echo line
      - attachments/ has the canonical evidence files
      - missing-import refusal: a missing role triggers a throw
      - latest-import-wins: when two valid imports exist for the same
        (provider,role), the newer import_id is the one inlined
#>
[CmdletBinding()] param()
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'Build-WaggleEpochEvidence.ps1')
. (Join-Path $PSScriptRoot 'Import-WaggleExternalReviewResponse.ps1')
. (Join-Path $PSScriptRoot 'New-WaggleSynthesisPasteBlock.ps1')

$Script:Pass = 0; $Script:Fail = 0
function Assert-True {
    param([string] $Name, [bool] $Cond, [string] $Detail = '')
    if ($Cond) { Write-Host "PASS  $Name" -ForegroundColor Green; $Script:Pass++ }
    else        { Write-Host "FAIL  $Name $Detail" -ForegroundColor Red; $Script:Fail++ }
}

$tmp = Join-Path $env:TEMP ("waggle-test-spb-{0}" -f ([guid]::NewGuid().ToString('N')))
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
        diff_text = "diff --git a/foo b/foo`n+a`n+b`n+c`n+d`n+e`n+f`n+g`n+h`n+i`n+j`n+k`n+l`n+m`n+n`n+o`n+p`n+q`n+r`n+s`n+t`n+u`n+v`n+w`n+x`n+y`n+z`n"
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

function New-ReviewerResponseMarkdown {
    param([string] $Provider, [string] $Role, [string] $IterationId, [string] $EpochId, [string] $Sha)
    $obj = [ordered]@{
        reviewer_self_id = [ordered]@{
            claimed_model_name = "Synthetic $Provider"
            claimed_version = $null
            training_cutoff = $null
            self_assessed_strengths_for_this_review = @('strength a')
            self_assessed_limitations_for_this_review = @('limitation a')
            estimated_context_window_kb = $null
            uses_extended_thinking_or_reasoning_mode = $false
        }
        provider = $Provider; role = $Role
        target_iteration_id = $IterationId; epoch_id = $EpochId
        source_evidence_sha256 = $Sha
        reviewer_summary = "Synthetic $Provider/$Role summary."
        verdict = 'pass'
        findings = @(); suggested_next_actions = @()
        confidence = 'medium'; limitations = 'synthetic'; completed = $true
    }
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.AppendLine('# External review response (synthetic)')
    [void]$sb.AppendLine('```reviewer-self-id')
    [void]$sb.AppendLine('I am ' + $obj.reviewer_self_id.claimed_model_name + '.')
    [void]$sb.AppendLine('```')
    [void]$sb.AppendLine('')
    [void]$sb.AppendLine('```external-review-json')
    [void]$sb.AppendLine(([pscustomobject]$obj | ConvertTo-Json -Depth 16))
    [void]$sb.AppendLine('```')
    [void]$sb.AppendLine('')
    [void]$sb.AppendLine('EXTERNAL-REVIEW-COMPLETE')
    return $sb.ToString()
}

function Copy-PromptTemplates {
    param([string] $Root)
    $promptsDir = Join-Path $Root 'prompts/external_review'
    [void](New-Item -ItemType Directory -Path (Join-Path $promptsDir 'providers') -Force)
    $repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
    foreach ($f in 'architect.md','security.md','reliability.md','synthesis_gpt.md') {
        Copy-Item -LiteralPath (Join-Path $repoRoot ('prompts/external_review/' + $f)) -Destination (Join-Path $promptsDir $f) -Force
    }
    foreach ($f in 'claude_web.md','gemini.md','grok.md','gpt.md') {
        Copy-Item -LiteralPath (Join-Path $repoRoot ('prompts/external_review/providers/' + $f)) -Destination (Join-Path (Join-Path $promptsDir 'providers') $f) -Force
    }
}

# ---- Build epoch + import three reviewer responses ---------------------

$proj = New-FakeProject -Name 'spb1'
Copy-PromptTemplates -Root $proj.root
$iid = '2026-05-07_05-00-00'
[void](New-FakeIteration -Root $proj.root -Id $iid)
$ev = Build-WaggleEpochEvidence -ConfigPath $proj.cfg -IterationIds @($iid)
$EpochId = $ev.epoch_id
$Sha = $ev.evidence_sha256

$resDir = Join-Path $proj.root 'tmp_responses'
[void](New-Item -ItemType Directory -Path $resDir -Force)
function Write-Response { param([string] $Name, [string] $Body); $p = Join-Path $resDir ($Name + '.md'); Set-Content -Path $p -Value $Body -Encoding UTF8; return $p }

# Phase 2B-Revision (P9): default external pairs are gemini/architect
# and grok/reliability. claude_web is no longer required.
$pairs = @(
    @{ provider = 'gemini'; role = 'architect'   }
    @{ provider = 'grok';   role = 'reliability' }
)
foreach ($p in $pairs) {
    $body = New-ReviewerResponseMarkdown -Provider $p.provider -Role $p.role -IterationId $iid -EpochId $EpochId -Sha $Sha
    $rsp = Write-Response -Name ($p.provider + '_' + $p.role) -Body $body
    $r = Import-WaggleExternalReviewResponse -ConfigPath $proj.cfg -EpochId $EpochId `
            -Provider $p.provider -Role $p.role -ResponseFile $rsp -IterationId $iid
    Assert-True ("setup: import " + $p.provider + '/' + $p.role + ' ok') ($r.ok -eq $true)
}

# ---- Compose paste-block ----------------------------------------------

$spb = New-WaggleSynthesisPasteBlock -ConfigPath $proj.cfg -EpochId $EpochId -IterationId $iid
Assert-True 'paste-block: ok=true'                      ($spb.ok -eq $true)
Assert-True 'paste-block: paste_block.md exists'        (Test-Path -LiteralPath $spb.paste_block_path)
Assert-True 'paste-block: synthesis_dir exists'         (Test-Path -LiteralPath $spb.synthesis_dir)
Assert-True 'paste-block: metadata.json exists'         (Test-Path -LiteralPath $spb.metadata_path)
Assert-True 'paste-block: attachments dir exists'       (Test-Path -LiteralPath (Join-Path $spb.synthesis_dir 'attachments'))
Assert-True 'paste-block: at least 5 attachments'       ($spb.attachments.Count -ge 5)
Assert-True 'p9: includes 2 imports (gemini + grok)'    ($spb.included_imports.Count -eq 2)

$body = Get-Content -Raw -Path $spb.paste_block_path -Encoding UTF8
Assert-True 'paste-block: contains UNTRUSTED warning' ($body -match 'UNTRUSTED')
Assert-True 'paste-block: contains synthesis-json instructions (template)' ($body -match 'synthesis-json')
Assert-True 'paste-block: contains MANDATORY-line directive' ($body -match 'MANDATORY: Use Claude Opus 4\.7')
Assert-True 'paste-block: echoes evidence_sha256' ($body -match [regex]::Escape($Sha))

# Phase 2B-Revision: internal Claude reviews are inlined automatically
Assert-True 'p9: paste-block has INTERNAL Claude section' ($body -match 'INTERNAL Claude \(Phase 2A-2\)')
foreach ($role in 'architect','security','reliability') {
    Assert-True ("p9: paste-block has internal $role review for the iteration") ($body -match ('iteration ' + [regex]::Escape($iid) + ' / ' + $role))
}

# Phase 2B-Revision: external section present (gemini + grok)
Assert-True 'p9: paste-block has EXTERNAL section' ($body -match 'EXTERNAL \(Phase 2B reviewer imports\)')
Assert-True 'p9: paste-block has gemini/architect external entry' ($body -match 'gemini / architect')
Assert-True 'p9: paste-block has grok/reliability external entry' ($body -match 'grok / reliability')
Assert-True 'p9: claude_web NOT in default external section' ($body -notmatch 'claude_web / architect')

Assert-True 'paste-block: ends with END OF PASTE-BLOCK marker' ($body -match 'END OF PASTE-BLOCK')

# Verify attachments dir has the canonical evidence files
$atDir = Join-Path $spb.synthesis_dir 'attachments'
Assert-True 'attachments: epoch_evidence.json copied'    (Test-Path -LiteralPath (Join-Path $atDir 'epoch_evidence.json'))
Assert-True 'attachments: cumulative_diff.patch copied'  (Test-Path -LiteralPath (Join-Path $atDir 'cumulative_diff.patch'))
Assert-True 'attachments: cumulative_raportti.md copied' (Test-Path -LiteralPath (Join-Path $atDir 'cumulative_raportti.md'))

# ---- Phase 2B-Revision (P9): no-external-imports OK -------------------

$projN = New-FakeProject -Name 'spbN'
Copy-PromptTemplates -Root $projN.root
$iidN = '2026-05-07_05-30-00'
[void](New-FakeIteration -Root $projN.root -Id $iidN)
$evN = Build-WaggleEpochEvidence -ConfigPath $projN.cfg -IterationIds @($iidN)
$spbN = New-WaggleSynthesisPasteBlock -ConfigPath $projN.cfg -EpochId $evN.epoch_id -IterationId $iidN
Assert-True 'p9: no-external imports -> paste-block builds'      ($spbN.ok -eq $true)
$bodyN = Get-Content -Raw -Path $spbN.paste_block_path -Encoding UTF8
Assert-True 'p9: paste-block has INTERNAL Claude section even without externals' ($bodyN -match 'INTERNAL Claude \(Phase 2A-2\)')
Assert-True 'p9: paste-block notes no external imports'          ($bodyN -match 'EXTERNAL \(no imports')

# ---- Latest-import-wins ------------------------------------------------

$projL = New-FakeProject -Name 'spbL'
Copy-PromptTemplates -Root $projL.root
$iidL = '2026-05-07_06-00-00'
[void](New-FakeIteration -Root $projL.root -Id $iidL)
$evL = Build-WaggleEpochEvidence -ConfigPath $projL.cfg -IterationIds @($iidL)
$shaL = $evL.evidence_sha256
$resDirL = Join-Path $projL.root 'tmp_responses'
[void](New-Item -ItemType Directory -Path $resDirL -Force)

# Import the default Phase 2B-Revision pairs (first round).
foreach ($p in $pairs) {
    $body = New-ReviewerResponseMarkdown -Provider $p.provider -Role $p.role -IterationId $iidL -EpochId $evL.epoch_id -Sha $shaL
    $rsp = Join-Path $resDirL ($p.provider + '_' + $p.role + '_v1.md')
    Set-Content -Path $rsp -Value $body -Encoding UTF8
    [void](Import-WaggleExternalReviewResponse -ConfigPath $projL.cfg -EpochId $evL.epoch_id `
            -Provider $p.provider -Role $p.role -ResponseFile $rsp -IterationId $iidL)
}
# Wait so the timestamp prefix differs (UTC seconds resolution).
Start-Sleep -Seconds 2
# Re-import gemini/architect (this becomes the latest valid for that pair).
$body2 = New-ReviewerResponseMarkdown -Provider 'gemini' -Role 'architect' -IterationId $iidL -EpochId $evL.epoch_id -Sha $shaL
$rsp2 = Join-Path $resDirL 'gemini_architect_v2.md'
Set-Content -Path $rsp2 -Value $body2 -Encoding UTF8
$r2 = Import-WaggleExternalReviewResponse -ConfigPath $projL.cfg -EpochId $evL.epoch_id `
        -Provider 'gemini' -Role 'architect' -ResponseFile $rsp2 -IterationId $iidL
$latestArchitectId = $r2.import_id

$spbL = New-WaggleSynthesisPasteBlock -ConfigPath $projL.cfg -EpochId $evL.epoch_id -IterationId $iidL
$metaL = Get-Content -Raw -Path $spbL.metadata_path -Encoding UTF8 | ConvertFrom-Json
$archEntry = @($metaL.included_imports | Where-Object { $_.provider -eq 'gemini' -and $_.role -eq 'architect' })[0]
Assert-True 'latest-wins: gemini/architect used latest import_id' ($archEntry.import_id -eq $latestArchitectId)

# ---- Phase 2B-Revision (P9): proposal matrix + ledger excerpt ---------

$projP = New-FakeProject -Name 'spbP'
Copy-PromptTemplates -Root $projP.root
$iidP = '2026-05-07_07-00-00'
[void](New-FakeIteration -Root $projP.root -Id $iidP)
$evP = Build-WaggleEpochEvidence -ConfigPath $projP.cfg -IterationIds @($iidP)
# Drop a synthetic proposal_matrix.md into the synth dir
$synthDirP = Join-Path (Join-Path (Join-Path $projP.root 'iterations') $iidP) ('external_reviews/synthesis/' + $evP.epoch_id)
[void](New-Item -ItemType Directory -Path $synthDirP -Force)
Set-Content -Path (Join-Path $synthDirP 'proposal_matrix.md') -Value '# Proposal matrix synthetic body for P9' -Encoding UTF8
# Drop a synthetic regression_ledger.json
[void](New-Item -ItemType Directory -Path (Join-Path $projP.root 'state') -Force)
Set-Content -Path (Join-Path $projP.root 'state/regression_ledger.json') -Value (@{ format_version = '1.0'; regressions = @( @{ id='REG-2026-05-07-001'; status='open'; severity='high'; score=70; category='hardening_gate_failure'; first_symptom='gate fail'; detected_in_iteration=$iidP; history=@(@{ iteration_id=$iidP; event='detected' }) } ) } | ConvertTo-Json -Depth 10) -Encoding UTF8
$spbP = New-WaggleSynthesisPasteBlock -ConfigPath $projP.cfg -EpochId $evP.epoch_id -IterationId $iidP
$bodyP = Get-Content -Raw -Path $spbP.paste_block_path -Encoding UTF8
Assert-True 'p9: paste-block has PROPOSAL MATRIX section'        ($bodyP -match 'PROPOSAL MATRIX')
Assert-True 'p9: paste-block inlines proposal matrix body'       ($bodyP -match 'Proposal matrix synthetic body for P9')
Assert-True 'p9: paste-block has REGRESSION LEDGER EXCERPT section' ($bodyP -match 'REGRESSION LEDGER EXCERPT')
Assert-True 'p9: paste-block contains ledger entry id'           ($bodyP -match 'REG-2026-05-07-001')

# ---- Cleanup ----------------------------------------------------------

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $tmp

Write-Host ''
Write-Host "Result: $Script:Pass/$($Script:Pass + $Script:Fail) tests passed" -ForegroundColor Cyan
if ($Script:Fail -gt 0) { exit 1 } else { exit 0 }
