#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2B P11 tests for the EpochCycleTrigger library + the
    Test-WaggleEpochCycleTrigger CLI wrapper.
#>
[CmdletBinding()] param()
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib/external_review/ProviderProfiles.ps1')
. (Join-Path $PSScriptRoot 'lib/external_review/EpochCycleTrigger.ps1')

$Script:Pass = 0; $Script:Fail = 0
function Assert-True {
    param([string] $Name, [bool] $Cond, [string] $Detail = '')
    if ($Cond) { Write-Host "PASS  $Name" -ForegroundColor Green; $Script:Pass++ }
    else        { Write-Host "FAIL  $Name $Detail" -ForegroundColor Red; $Script:Fail++ }
}

$tmp = Join-Path $env:TEMP ("waggle-test-ect-{0}" -f ([guid]::NewGuid().ToString('N')))
[void](New-Item -ItemType Directory -Path $tmp -Force)

# Common config values for the library
$cfgStub = [pscustomobject]@{
    iteration_cycle = $null
    external_review = $null
}
$ic = Get-WaggleIterationCycleConfig -Config $cfgStub
$er = Get-WaggleExternalReviewConfig -Config $cfgStub

function New-IterRecord {
    param(
        [string] $Id,
        [bool] $NoWork = $false,
        [bool] $HardeningFail = $false,
        [string] $Architect = 'pass',
        [string] $Security = 'pass',
        [string] $Reliability = 'pass',
        [string[]] $Severities = @()
    )
    return [pscustomobject]@{
        iteration_id = $Id
        status = 'COMPLETED'
        no_work_classification = $NoWork
        hardening_gates_failure_present = $HardeningFail
        internal_review_verdicts = [pscustomobject]@{
            architect = $Architect; security = $Security; reliability = $Reliability
        }
        internal_findings_severities = $Severities
    }
}

# --- Test 1: 0 iterations -> continue --------------------------------

$d = Get-WaggleEpochCycleDecision -IterationsSinceLastTrigger @() `
        -IterationCycle $ic -ExternalReview $er -ProjectRoot $tmp
Assert-True 'empty: continue' ($d.decision -eq 'continue')

# --- Test 2: 1 healthy iteration < N=3 -> continue -------------------

$d = Get-WaggleEpochCycleDecision -IterationsSinceLastTrigger @(New-IterRecord -Id 'a') `
        -IterationCycle $ic -ExternalReview $er -ProjectRoot $tmp
Assert-True '1 healthy iter: continue' ($d.decision -eq 'continue')

# --- Test 3: 3 healthy iterations -> trigger (cumulative N) ----------

$d = Get-WaggleEpochCycleDecision -IterationsSinceLastTrigger @(
    (New-IterRecord -Id 'a'),
    (New-IterRecord -Id 'b'),
    (New-IterRecord -Id 'c')
) -IterationCycle $ic -ExternalReview $er -ProjectRoot $tmp
Assert-True '3 healthy iters: trigger' ($d.decision -eq 'trigger')
# Phase 2B-Revision: target_reached_and_clean is the new branch L
# semantics; the legacy "local_iterations_per_external_review" reason
# only fires when the legacy fallback (branch N) wins.
Assert-True '3 healthy iters: reason cites target_reached_and_clean OR legacy N' (($d.reasons -join ' ') -match 'target_reached_and_clean|local_iterations_per_external_review')

# --- Test 4: 1 hardening_fail iter -> trigger ------------------------

$d = Get-WaggleEpochCycleDecision -IterationsSinceLastTrigger @(
    (New-IterRecord -Id 'a' -HardeningFail $true)
) -IterationCycle $ic -ExternalReview $er -ProjectRoot $tmp
Assert-True 'hardening fail: trigger' ($d.decision -eq 'trigger')
Assert-True 'hardening fail: reason cites hardening' (($d.reasons -join ' ') -match 'hardening_gate_failure')

# --- Test 5: critical internal finding -> trigger --------------------

$d = Get-WaggleEpochCycleDecision -IterationsSinceLastTrigger @(
    (New-IterRecord -Id 'a' -Severities @('critical'))
) -IterationCycle $ic -ExternalReview $er -ProjectRoot $tmp
Assert-True 'critical finding: trigger' ($d.decision -eq 'trigger')
Assert-True 'critical finding: reason cites internal_critical' (($d.reasons -join ' ') -match 'internal_critical')

# --- Test 6: regression in internal review verdict -> trigger --------

$d = Get-WaggleEpochCycleDecision -IterationsSinceLastTrigger @(
    (New-IterRecord -Id 'a' -Security 'fail')
) -IterationCycle $ic -ExternalReview $er -ProjectRoot $tmp
Assert-True 'regression verdict: trigger' ($d.decision -eq 'trigger')
Assert-True 'regression verdict: reason cites regression' (($d.reasons -join ' ') -match 'regression')

# --- Test 7: 2 consecutive no-work -> trigger (default threshold 2) --

$d = Get-WaggleEpochCycleDecision -IterationsSinceLastTrigger @(
    (New-IterRecord -Id 'a' -NoWork $true),
    (New-IterRecord -Id 'b' -NoWork $true)
) -IterationCycle $ic -ExternalReview $er -ProjectRoot $tmp
# Phase 2B-Revision (REL-013): no_work_consecutive now emits the
# more specific 'strategic_external_review' decision so the
# operator/synthesis sees the "complete? stuck? pivot?" signal.
Assert-True '2 consec no-work: strategic_external_review' ($d.decision -eq 'strategic_external_review')
Assert-True '2 consec no-work: reason cites no_work_consecutive' (($d.reasons -join ' ') -match 'no_work_consecutive')

# --- Test 8: 1 no-work then 1 healthy -> continue (consecutive resets) -

$d = Get-WaggleEpochCycleDecision -IterationsSinceLastTrigger @(
    (New-IterRecord -Id 'a' -NoWork $true),
    (New-IterRecord -Id 'b' -NoWork $false)
) -IterationCycle $ic -ExternalReview $er -ProjectRoot $tmp
Assert-True '1 nowork + 1 healthy: continue (consecutive reset)' ($d.decision -eq 'continue')

# --- Test 9: pause flag present -> pause -----------------------------

$projP = Join-Path $tmp 'projP'
[void](New-Item -ItemType Directory -Path (Join-Path $projP 'state') -Force)
$pauseFlag = Join-Path $projP 'state/pause_external_review.flag'
Set-Content -Path $pauseFlag -Value 'paused at 2026-05-07' -Encoding UTF8
$d = Get-WaggleEpochCycleDecision -IterationsSinceLastTrigger @(New-IterRecord -Id 'a') `
        -IterationCycle $ic -ExternalReview $er -ProjectRoot $projP
Assert-True 'pause flag: pause' ($d.decision -eq 'pause')
Assert-True 'pause flag: reason cites manual_pause_flag' (($d.reasons -join ' ') -match 'manual_pause_flag')

# --- Test 10: HALT marker present -> halt ----------------------------

$haltPath = Join-Path $tmp 'HALT.md'
Set-Content -Path $haltPath -Value 'halted' -Encoding UTF8
$d = Get-WaggleEpochCycleDecision -IterationsSinceLastTrigger @(New-IterRecord -Id 'a') `
        -IterationCycle $ic -ExternalReview $er -ProjectRoot $tmp -HaltMarkerPath $haltPath
Assert-True 'halt marker: halt' ($d.decision -eq 'halt')
Assert-True 'halt marker: reason cites halt_marker' (($d.reasons -join ' ') -match 'halt_marker')

# --- Test 11: requires_attention flag -> trigger ---------------------

$d = Get-WaggleEpochCycleDecision -IterationsSinceLastTrigger @(New-IterRecord -Id 'a') `
        -IterationCycle $ic -ExternalReview $er -ProjectRoot $tmp -RequiresAttention $true
Assert-True 'requires_attention: trigger' ($d.decision -eq 'trigger')
Assert-True 'requires_attention: reason cites prior_synthesis' (($d.reasons -join ' ') -match 'requires_attention')

# --- Test 12: pause beats halt beats early-trigger ordering ---------

$d = Get-WaggleEpochCycleDecision -IterationsSinceLastTrigger @(
    (New-IterRecord -Id 'a' -HardeningFail $true)
) -IterationCycle $ic -ExternalReview $er -ProjectRoot $projP `
  -HaltMarkerPath $haltPath
Assert-True 'pause beats halt + early-trigger' ($d.decision -eq 'pause')

# Remove pause flag, now halt should win over hardening fail
Remove-Item -LiteralPath $pauseFlag -Force
$d = Get-WaggleEpochCycleDecision -IterationsSinceLastTrigger @(
    (New-IterRecord -Id 'a' -HardeningFail $true)
) -IterationCycle $ic -ExternalReview $er -ProjectRoot $projP `
  -HaltMarkerPath $haltPath
Assert-True 'halt beats early-trigger when pause absent' ($d.decision -eq 'halt')

# ---------------------------------------------------------------------
# Phase 2B-Revision (REL-013) — dynamic epoch controller cases
# ---------------------------------------------------------------------

# Build a Phase 2B-Revision config with the new bounds + repair caps.
$cfgRevStub = [pscustomobject]@{
    iteration_cycle = [pscustomobject]@{
        local_iterations_per_external_review = 3
        min_local_iterations = 2
        target_local_iterations = 3
        max_local_iterations = 6
        verification_iterations_after_fix = 1
        max_repair_attempts = [pscustomobject]@{ medium = 2; high = 2; critical = 1 }
        escalate_if_same_issue_reappears = $true
        escalate_if_same_test_fails_twice = $true
        early_trigger_on_regression = $true
        early_trigger_on_hardening_gate_failure = $true
        early_trigger_on_internal_critical_finding = $true
        early_trigger_on_no_work_consecutive = 2
        no_work_diff_min_bytes = 1; no_work_raportti_min_bytes = 1; no_work_stdout_min_meaningful_bytes = 100
    }
    external_review = $null
}
$icR = Get-WaggleIterationCycleConfig -Config $cfgRevStub
$erR = Get-WaggleExternalReviewConfig -Config $cfgRevStub

function New-OpenIssue {
    param(
        [string] $Id, [string] $Status = 'open', [string] $Severity = 'medium',
        [int] $Score = 50, [int] $RepairAttempts = 0,
        [string[]] $FailingTests = @(), [string[]] $VerifiedBy = @()
    )
    return [pscustomobject]@{
        id = $Id
        status = $Status
        severity = $Severity
        score = $Score
        repair_attempts = $RepairAttempts
        failing_tests = $FailingTests
        verified_by = $VerifiedBy
    }
}

# --- TBR-1: below-min count -> continue_for_repair when repair work open
$d = Get-WaggleEpochCycleDecision -IterationsSinceLastTrigger @(
    (New-IterRecord -Id 'a')
) -IterationCycle $icR -ExternalReview $erR -ProjectRoot $tmp `
  -OpenIssues @( (New-OpenIssue -Id 'REG-1' -Status 'open' -Severity 'medium' -Score 50 -RepairAttempts 0) )
Assert-True 'rev: open repair work -> continue_for_repair'   ($d.decision -eq 'continue_for_repair')
Assert-True 'rev: branch I'                                  ($d.decision_priority_branch -eq 'I')

# --- TBR-2: verification_pending issue -> continue_for_verification
$d = Get-WaggleEpochCycleDecision -IterationsSinceLastTrigger @(
    (New-IterRecord -Id 'a'), (New-IterRecord -Id 'b')
) -IterationCycle $icR -ExternalReview $erR -ProjectRoot $tmp `
  -OpenIssues @( (New-OpenIssue -Id 'REG-2' -Status 'verification_pending' -Severity 'high' -Score 70 -RepairAttempts 1) )
Assert-True 'rev: verification_pending -> continue_for_verification' ($d.decision -eq 'continue_for_verification')
Assert-True 'rev: branch H'                                          ($d.decision_priority_branch -eq 'H')

# --- TBR-3: repair-attempt cap reached (high, attempts=2 cap=2) -> trigger
$d = Get-WaggleEpochCycleDecision -IterationsSinceLastTrigger @(
    (New-IterRecord -Id 'a')
) -IterationCycle $icR -ExternalReview $erR -ProjectRoot $tmp `
  -OpenIssues @( (New-OpenIssue -Id 'REG-3' -Status 'still_failing' -Severity 'high' -Score 70 -RepairAttempts 2) )
Assert-True 'rev: repair cap reached (high) -> trigger'  ($d.decision -eq 'trigger')
Assert-True 'rev: branch E'                              ($d.decision_priority_branch -eq 'E')

# --- TBR-4: critical attempts=1 cap=1 -> trigger
$d = Get-WaggleEpochCycleDecision -IterationsSinceLastTrigger @(
    (New-IterRecord -Id 'a')
) -IterationCycle $icR -ExternalReview $erR -ProjectRoot $tmp `
  -OpenIssues @( (New-OpenIssue -Id 'REG-4' -Status 'still_failing' -Severity 'critical' -Score 90 -RepairAttempts 1) )
Assert-True 'rev: repair cap reached (critical) -> trigger' ($d.decision -eq 'trigger')

# --- TBR-5: same-issue resurrection -> trigger (D)
$d = Get-WaggleEpochCycleDecision -IterationsSinceLastTrigger @(
    (New-IterRecord -Id 'a'), (New-IterRecord -Id 'b')
) -IterationCycle $icR -ExternalReview $erR -ProjectRoot $tmp `
  -OpenIssues @( (New-OpenIssue -Id 'REG-5' -Status 'open' -Severity 'medium' -Score 50 -VerifiedBy @('iter-prev')) )
Assert-True 'rev: same-issue resurrection -> trigger'  ($d.decision -eq 'trigger')
Assert-True 'rev: branch D (resurrection)'             ($d.decision_priority_branch -eq 'D')

# --- TBR-6: same-test-failed-twice -> trigger (F)
$d = Get-WaggleEpochCycleDecision -IterationsSinceLastTrigger @(
    [pscustomobject]@{ iteration_id = 'a'; status = 'COMPLETED'; no_work_classification = $false; failing_tests = @('Test-Foo') }
    [pscustomobject]@{ iteration_id = 'b'; status = 'COMPLETED'; no_work_classification = $false; failing_tests = @('Test-Foo') }
) -IterationCycle $icR -ExternalReview $erR -ProjectRoot $tmp
Assert-True 'rev: same test failed twice -> trigger'   ($d.decision -eq 'trigger')
Assert-True 'rev: branch F (same-test-twice)'          ($d.decision_priority_branch -eq 'F')

# --- TBR-7: severity-cap critical -> effectiveMax = current+1
$d = Get-WaggleEpochCycleDecision -IterationsSinceLastTrigger @(
    (New-IterRecord -Id 'a'), (New-IterRecord -Id 'b')
) -IterationCycle $icR -ExternalReview $erR -ProjectRoot $tmp `
  -OpenIssues @( (New-OpenIssue -Id 'REG-7' -Status 'open' -Severity 'critical' -Score 85) )
Assert-True 'rev: severity 85 -> remaining cap = current + 1' ($d.remaining_iterations_cap_for_this_epoch -eq 3)

# --- TBR-8: severity-cap high -> effectiveMax = current+2
$d = Get-WaggleEpochCycleDecision -IterationsSinceLastTrigger @(
    (New-IterRecord -Id 'a'), (New-IterRecord -Id 'b')
) -IterationCycle $icR -ExternalReview $erR -ProjectRoot $tmp `
  -OpenIssues @( (New-OpenIssue -Id 'REG-8' -Status 'open' -Severity 'high' -Score 70) )
Assert-True 'rev: severity 70 -> remaining cap = current + 2' ($d.remaining_iterations_cap_for_this_epoch -eq 4)

# --- TBR-9: hard ceiling reached -> trigger (J)
$d = Get-WaggleEpochCycleDecision -IterationsSinceLastTrigger @(
    (New-IterRecord -Id 'a'), (New-IterRecord -Id 'b'), (New-IterRecord -Id 'c'),
    (New-IterRecord -Id 'd'), (New-IterRecord -Id 'e'), (New-IterRecord -Id 'f')
) -IterationCycle $icR -ExternalReview $erR -ProjectRoot $tmp `
  -OpenIssues @( (New-OpenIssue -Id 'REG-9' -Status 'open' -Severity 'medium' -Score 50 -RepairAttempts 0) )
Assert-True 'rev: hard ceiling at max -> trigger'      ($d.decision -eq 'trigger')
Assert-True 'rev: branch J (hard ceiling)'             ($d.decision_priority_branch -eq 'J')

# --- TBR-10: needs_manual_action marker -> needs_manual_action (A)
$manualMarker = Join-Path $tmp 'login_required.txt'
Set-Content -Path $manualMarker -Value 'login required' -Encoding UTF8
$d = Get-WaggleEpochCycleDecision -IterationsSinceLastTrigger @(New-IterRecord -Id 'a') `
        -IterationCycle $icR -ExternalReview $erR -ProjectRoot $tmp `
        -NeedsManualActionMarkerPath $manualMarker
Assert-True 'rev: manual marker -> needs_manual_action' ($d.decision -eq 'needs_manual_action')
Assert-True 'rev: branch A (manual marker)'             ($d.decision_priority_branch -eq 'A')
Remove-Item -LiteralPath $manualMarker -Force

# --- TBR-11: classified_manual issue -> needs_manual_action (A)
$d = Get-WaggleEpochCycleDecision -IterationsSinceLastTrigger @(New-IterRecord -Id 'a') `
        -IterationCycle $icR -ExternalReview $erR -ProjectRoot $tmp `
        -OpenIssues @( (New-OpenIssue -Id 'REG-11' -Status 'classified_manual' -Severity 'high' -Score 70) )
Assert-True 'rev: classified_manual -> needs_manual_action' ($d.decision -eq 'needs_manual_action')

# --- TBR-12: AutoRepairPromptReady + budget left -> continue_for_auto_repair (I-AR)
$d = Get-WaggleEpochCycleDecision -IterationsSinceLastTrigger @(New-IterRecord -Id 'a') `
        -IterationCycle $icR -ExternalReview $erR -ProjectRoot $tmp `
        -OpenIssues @( (New-OpenIssue -Id 'REG-12' -Status 'classified_trivial' -Severity 'medium' -Score 50 -RepairAttempts 0) ) `
        -AutoRepairPromptReady $true -AutoRepairIterationsThisEpoch 0 -MaxAutoRepairIterationsPerEpoch 3
Assert-True 'rev: auto-repair ready -> continue_for_auto_repair' ($d.decision -eq 'continue_for_auto_repair')
Assert-True 'rev: branch I-AR'                                   ($d.decision_priority_branch -eq 'I-AR')

# --- TBR-13: AutoRepair budget exhausted -> falls through to continue_for_repair (I)
$d = Get-WaggleEpochCycleDecision -IterationsSinceLastTrigger @(New-IterRecord -Id 'a') `
        -IterationCycle $icR -ExternalReview $erR -ProjectRoot $tmp `
        -OpenIssues @( (New-OpenIssue -Id 'REG-13' -Status 'classified_trivial' -Severity 'medium' -Score 50 -RepairAttempts 0) ) `
        -AutoRepairPromptReady $true -AutoRepairIterationsThisEpoch 3 -MaxAutoRepairIterationsPerEpoch 3
Assert-True 'rev: auto-repair budget exhausted -> continue_for_repair' ($d.decision -eq 'continue_for_repair')

# --- TBR-14: target reached + clean -> trigger (L) with new reason text
$d = Get-WaggleEpochCycleDecision -IterationsSinceLastTrigger @(
    (New-IterRecord -Id 'a'), (New-IterRecord -Id 'b'), (New-IterRecord -Id 'c')
) -IterationCycle $icR -ExternalReview $erR -ProjectRoot $tmp
Assert-True 'rev: target reached + clean -> trigger'   ($d.decision -eq 'trigger')
Assert-True 'rev: branch L (target_reached_and_clean)' ($d.decision_priority_branch -eq 'L')
Assert-True 'rev: reason cites target_reached_and_clean' (($d.reasons -join ' ') -match 'target_reached_and_clean')

# --- TBR-15: count below min -> CONTINUE_LOCAL (M default), branch K reason
$d = Get-WaggleEpochCycleDecision -IterationsSinceLastTrigger @(
    (New-IterRecord -Id 'a')
) -IterationCycle $icR -ExternalReview $erR -ProjectRoot $tmp
Assert-True 'rev: below min -> continue'        ($d.decision -eq 'continue')
Assert-True 'rev: reason cites below_minimum'   (($d.reasons -join ' ') -match 'below_minimum')

# --- TBR-16: audit-record fields populated
$d = Get-WaggleEpochCycleDecision -IterationsSinceLastTrigger @(
    (New-IterRecord -Id 'a'), (New-IterRecord -Id 'b')
) -IterationCycle $icR -ExternalReview $erR -ProjectRoot $tmp `
  -OpenIssues @( (New-OpenIssue -Id 'REG-16a' -Status 'verification_pending' -Severity 'medium' -Score 50),
                 (New-OpenIssue -Id 'REG-16b' -Status 'open'                 -Severity 'high'   -Score 75 -RepairAttempts 1) )
Assert-True 'rev audit: open_regressions_count=2'           ($d.open_regressions_count -eq 2)
Assert-True 'rev audit: max_regression_score=75'            ($d.max_regression_score -eq 75)
Assert-True 'rev audit: issues_pending_verification has REG-16a' ($d.issues_pending_verification -contains 'REG-16a')
Assert-True 'rev audit: issues_in_repair has REG-16b'       ($d.issues_in_repair -contains 'REG-16b')
Assert-True 'rev audit: decided_at_utc populated'           ([string]$d.decided_at_utc -ne '')

# --- TBR-17: verification prefix builder ---------------------------
$prefix = Get-WaggleVerificationIterationPrefix -IssueIds @('REG-X') -IssueTitles @('lock release ordering')
Assert-True 'rev prefix: contains VERIFICATION REQUIRED' ($prefix -match 'VERIFICATION REQUIRED')
Assert-True 'rev prefix: contains issue ids'             ($prefix -match 'REG-X')
Assert-True 'rev prefix: contains lock release ordering' ($prefix -match 'lock release ordering')
Assert-True 'rev prefix: instructs not to fix in this iteration' ($prefix -match 'DO NOT attempt another fix')

# --- TBR-18: Save-WaggleEpochControllerDecision writes the file
$decisionPath = Join-Path $tmp 'audit_iter_dir'
[void](New-Item -ItemType Directory -Path $decisionPath -Force)
$saved = Save-WaggleEpochControllerDecision -IterationFolder $decisionPath -DecisionRecord $d
Assert-True 'rev save: decision JSON written'  (Test-Path -LiteralPath $saved)
Assert-True 'rev save: contains decision key'  (((Get-Content -Raw -Path $saved -Encoding UTF8) -match '"decision":'))

# --- Cleanup --------------------------------------------------------

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $tmp

Write-Host ''
Write-Host "Result: $Script:Pass/$($Script:Pass + $Script:Fail) tests passed" -ForegroundColor Cyan
if ($Script:Fail -gt 0) { exit 1 } else { exit 0 }
