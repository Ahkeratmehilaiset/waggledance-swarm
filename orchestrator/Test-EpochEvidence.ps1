#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2B P6 tests for orchestrator/Build-WaggleEpochEvidence.ps1.
    Builds synthetic 1- and 3-iteration epochs in TEMP and asserts
    schema validity, deterministic SHA, no-work classification, and
    attachment-plan caps.
#>
[CmdletBinding()] param()
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'Build-WaggleEpochEvidence.ps1')

$Script:Pass = 0; $Script:Fail = 0
function Assert-True {
    param([string] $Name, [bool] $Cond, [string] $Detail = '')
    if ($Cond) { Write-Host "PASS  $Name" -ForegroundColor Green; $Script:Pass++ }
    else        { Write-Host "FAIL  $Name $Detail" -ForegroundColor Red; $Script:Fail++ }
}

$tmp = Join-Path $env:TEMP ("waggle-test-epoch-evidence-{0}" -f ([guid]::NewGuid().ToString('N')))
[void](New-Item -ItemType Directory -Path $tmp -Force)

function New-FakeIteration {
    param(
        [string] $Root,
        [string] $Id,
        [string] $Status = 'COMPLETED',
        [string] $Stdout = 'normal stdout content',
        [string] $RaporttiBody = '# raportti for iteration',
        [bool]   $WithDiff = $true,
        [string] $ArchVerdict = 'pass',
        [string] $SecVerdict  = 'pass_with_notes',
        [string] $RelVerdict  = 'pass'
    )
    $iterDir = Join-Path (Join-Path $Root 'iterations') $Id
    [void](New-Item -ItemType Directory -Path $iterDir -Force)
    Set-Content -Path (Join-Path $iterDir 'state.json') -Value (@{ iteration_id = $Id; status = $Status } | ConvertTo-Json) -Encoding UTF8
    Set-Content -Path (Join-Path $iterDir 'run_metadata.json') -Value '{}' -Encoding UTF8
    if ([string]::IsNullOrEmpty($Stdout)) {
        [System.IO.File]::WriteAllText((Join-Path $iterDir 'claude_stdout.txt'), '')
    } else {
        Set-Content -Path (Join-Path $iterDir 'claude_stdout.txt') -Value $Stdout -Encoding UTF8
    }
    [System.IO.File]::WriteAllText((Join-Path $iterDir 'claude_stderr.txt'), '')
    if ($WithDiff) {
        $gitMeta = @{
            commit = '1234567890abcdef1234567890abcdef12345678'
            diff_text = "diff --git a/foo.txt b/foo.txt`n+added line $Id`n"
        }
        Set-Content -Path (Join-Path $iterDir 'git_metadata.json') -Value ($gitMeta | ConvertTo-Json) -Encoding UTF8
    }
    [void](New-Item -ItemType Directory -Path (Join-Path $iterDir 'reviews') -Force)
    foreach ($pair in @(@('architect',$ArchVerdict), @('security',$SecVerdict), @('reliability',$RelVerdict))) {
        $role = $pair[0]; $verdict = $pair[1]
        $j = @{
            role = $role; target_iteration_id = $Id; verdict = $verdict
            findings = @(); summary = "fake $role summary"; metrics = @{ files_reviewed = 1; lines_reviewed = 1; review_duration_seconds = 1 }
            completed = $true; source_package_path = "iterations/$Id/llm_input_package.md"
        }
        Set-Content -Path (Join-Path (Join-Path $iterDir 'reviews') ($role + '.json')) -Value ($j | ConvertTo-Json -Depth 6) -Encoding UTF8
        Set-Content -Path (Join-Path (Join-Path $iterDir 'reviews') ($role + '.md')) -Value "# $role review of $Id`n$verdict" -Encoding UTF8
    }
    return $iterDir
}

function New-FakeProject {
    param([string] $Name, [bool] $Empty = $false)
    $root = Join-Path $tmp $Name
    [void](New-Item -ItemType Directory -Path $root -Force)
    [void](New-Item -ItemType Directory -Path (Join-Path $root 'state') -Force)
    [void](New-Item -ItemType Directory -Path (Join-Path $root 'iterations') -Force)
    if ($Empty) {
        [System.IO.File]::WriteAllText((Join-Path $root 'raportti.md'), '')
    } else {
        Set-Content -Path (Join-Path $root 'raportti.md') -Value '# raportti — synthetic project' -Encoding UTF8
    }
    # Minimal Phase-2A-aware config
    $cfg = @{
        projectRoot = $root
        transcriptDir = 'transcripts'
        iterationsDir = 'iterations'
        stateDir = 'state'
        reportFile = 'raportti.md'
        executionMode = 'print'
        claudeCommand = 'claude'
        model = 'opus'
        outputFormat = 'text'
        maxTurns = 30
        permissionMode = 'default'
        safeMode = $true
        allowBash = $false
        allowedTools = @('Read','Write','Edit','Glob','Grep')
        disallowedTools = @('Bash')
        dangerouslySkipPermissions = $false
        sanitizeEnvironment = $true
        envDenylist = $null
        envAllowList = @()
        killOnInteractivePrompt = $true
        runnerPollSeconds = 1
        tailLineCount = 100
        fullTranscriptMaxBytes = 1048576
        pollIntervalSeconds = 1
        stableThresholdSeconds = 5
        runTimeoutMinutes = 5
        llmPackageMaxChars = 200000
        perSectionMaxChars = 60000
        requireExitMarker = $false
        requireReport = $false
        requireClaudeAuthStatus = $false
        interactivePromptPatterns = @()
        completedPromptPatterns = @()
        exitMarker = '##X##'
        external_review = @{
            enabled = $true
            queue_dir_relative = 'external_reviews/queue'
            imported_dir_relative = 'external_reviews/imported'
            synthesis_dir_relative = 'external_reviews/synthesis'
            max_attachments_per_provider = 20
            fail_on_attachment_overflow = $true
            providers = @{}
            auto_approval_rule = 'all_reviewers_below_needs_changes'
            manual_pause_flag_relative = 'state/pause_external_review.flag'
            halt_marker = 'WAGGLE_HALT'
            session_resume_threshold_hours = 4
        }
        iteration_cycle = @{
            local_iterations_per_external_review = 3
            max_iterations_per_session = 50
            early_trigger_on_regression = $true
            early_trigger_on_hardening_gate_failure = $true
            early_trigger_on_internal_critical_finding = $true
            early_trigger_on_no_work_consecutive = 2
            no_work_diff_min_bytes = 1
            no_work_raportti_min_bytes = 1
            no_work_stdout_min_meaningful_bytes = 100
        }
        models = @{
            claude_code = 'claude-opus-4-7'
            claude_web = 'Claude Opus 4.7 (Max plan)'
            gemini = 'Gemini Pro Advanced'
            grok = 'Grok Expert mode'
            gpt_synthesis = 'GPT Pro 5.5 Extended Thinking'
        }
    }
    $cfgPath = Join-Path $root 'orchestrator.config.json'
    Set-Content -Path $cfgPath -Value ($cfg | ConvertTo-Json -Depth 10) -Encoding UTF8
    return [pscustomobject]@{ root = $root; cfg = $cfgPath }
}

# ---- 3-iteration epoch ------------------------------------------------

$proj = New-FakeProject -Name 'p3'
$ids = @()
foreach ($n in 1..3) {
    $iid = '2026-05-07_00-00-' + ('0' + ($n + 9).ToString()).Substring(0,2)
    [void](New-FakeIteration -Root $proj.root -Id $iid)
    $ids += $iid
}
$r = Build-WaggleEpochEvidence -ConfigPath $proj.cfg -IterationIds $ids
Assert-True '3-iter: ok=true' ($r.ok -eq $true)
Assert-True '3-iter: iteration_count = 3' ($r.manifest.iteration_count -eq 3)
Assert-True '3-iter: epoch_evidence.json exists' (Test-Path -LiteralPath $r.epoch_json_path)
Assert-True '3-iter: format_version = 2b.1' ($r.manifest.format_version -eq '2b.1')
Assert-True '3-iter: evidence_sha256 is 64 hex' ($r.evidence_sha256 -match '^[a-f0-9]{64}$')

# Schema validity (loose: parse as JSON + check required fields)
$mj = Get-Content -Raw -Path $r.epoch_json_path -Encoding UTF8 | ConvertFrom-Json
Assert-True '3-iter: schema has all required fields' (
    $null -ne $mj.epoch_id -and $null -ne $mj.iterations -and
    $null -ne $mj.regression_state -and $null -ne $mj.package_quality_snapshot
)

# Deterministic SHA across two builds on the same input
$r2 = Build-WaggleEpochEvidence -ConfigPath $proj.cfg -IterationIds $ids -OutputDir (Join-Path $tmp 'p3-second')
Assert-True '3-iter: evidence_sha256 deterministic across two runs' ($r.evidence_sha256 -eq $r2.evidence_sha256)

# Attachment plan within cap
foreach ($pname in 'claude_web','gemini','grok','gpt_synthesis') {
    $plan = $r.attachment_plans.$pname
    Assert-True ("3-iter: $pname attachment plan ok") ([bool]$plan.ok)
    Assert-True ("3-iter: $pname attachments <= 20") (@($plan.attachments).Count -le 20)
}

# ---- 1-iteration early-trigger epoch ----------------------------------

$proj1 = New-FakeProject -Name 'p1'
$id1 = '2026-05-07_00-30-00'
[void](New-FakeIteration -Root $proj1.root -Id $id1)
$r1 = Build-WaggleEpochEvidence -ConfigPath $proj1.cfg -IterationIds @($id1) -EarlyTriggerReason 'regression'
Assert-True '1-iter: ok=true' ($r1.ok -eq $true)
Assert-True '1-iter: iteration_count = 1' ($r1.manifest.iteration_count -eq 1)
Assert-True '1-iter: early_trigger_reason recorded' ($r1.manifest.early_trigger_reason -eq 'regression')

# ---- No-work classification ------------------------------------------

$projNw = New-FakeProject -Name 'pnw' -Empty $true
$idNw = '2026-05-07_00-45-00'
# Empty iteration: no diff, near-empty stdout, empty raportti
[void](New-FakeIteration -Root $projNw.root -Id $idNw -Stdout '' -WithDiff $false)
$rNw = Build-WaggleEpochEvidence -ConfigPath $projNw.cfg -IterationIds @($idNw)
$nwIter = $rNw.manifest.iterations[0]
Assert-True 'no-work: classified as no_work=true' ($nwIter.no_work_classification -eq $true)

# ---- Cumulative diff non-empty when iterations have diffs ------------

$diffPath = Join-Path $r.output_dir 'cumulative_diff.patch'
$diffContent = Get-Content -Raw -Path $diffPath -Encoding UTF8
Assert-True '3-iter: cumulative_diff non-empty' ($diffContent.Length -gt 0)

# ---- Regression presence flips the flag ------------------------------

$projReg = New-FakeProject -Name 'preg'
$idReg = '2026-05-07_01-00-00'
[void](New-FakeIteration -Root $projReg.root -Id $idReg)
# Inject a failing latest.json gate report
$gd = Join-Path $projReg.root 'docs/runs/hardening_gates'
[void](New-Item -ItemType Directory -Path $gd -Force)
$failGate = @{
    overall_ok = $false
    gates_run = 1
    gates_passed = 0
    gates_failed = 1
    results = @(@{ gate = 'Test-Synthetic'; ok = $false; exit_code = 1; elapsed_seconds = 1; error = 'simulated failure' })
}
Set-Content -Path (Join-Path $gd 'latest.json') -Value ($failGate | ConvertTo-Json -Depth 6) -Encoding UTF8
$rReg = Build-WaggleEpochEvidence -ConfigPath $projReg.cfg -IterationIds @($idReg)
Assert-True 'regression: hardening_gates_failure_present=true' ($rReg.manifest.regression_state.hardening_gates_failure_present -eq $true)
Assert-True 'regression: previously_passing_test_now_failing has Test-Synthetic' (
    @($rReg.manifest.regression_state.previously_passing_test_now_failing) -contains 'Test-Synthetic'
)

# ---- Attachment overflow throws --------------------------------------

# Force a tiny cap to trigger overflow
$projOf = New-FakeProject -Name 'pof'
$idOf = '2026-05-07_01-30-00'
[void](New-FakeIteration -Root $projOf.root -Id $idOf)
# Modify config to set max_attachments_per_provider = 1 (impossible
# given canonical files: epoch_evidence.json, cumulative_diff,
# cumulative_raportti, etc.)
$cfgOf = Get-Content -Raw -Path $projOf.cfg -Encoding UTF8 | ConvertFrom-Json
$cfgOf.external_review.max_attachments_per_provider = 1
Set-Content -Path $projOf.cfg -Value ($cfgOf | ConvertTo-Json -Depth 10) -Encoding UTF8
$threw = $false; $emsg = ''
try { Build-WaggleEpochEvidence -ConfigPath $projOf.cfg -IterationIds @($idOf) | Out-Null }
catch { $threw = $true; $emsg = $_.Exception.Message }
Assert-True 'attachment-overflow: throws with explicit error' ($threw -and ($emsg -match 'attachment_overflow'))

# ---- cleanup ---------------------------------------------------------

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $tmp

Write-Host ''
Write-Host "Result: $Script:Pass/$($Script:Pass + $Script:Fail) tests passed" -ForegroundColor Cyan
if ($Script:Fail -gt 0) { exit 1 } else { exit 0 }
