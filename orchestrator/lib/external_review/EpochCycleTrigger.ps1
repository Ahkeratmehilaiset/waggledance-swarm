# EpochCycleTrigger.ps1
#
# Phase 2B P11 + Phase 2B-Revision (REL-013, partly REL-014):
# pure decision function for the dynamic epoch controller.
#
# Returns one of the following decisions (string):
#
#   continue                     — run the next local iteration normally
#                                  (backwards-compat alias for CONTINUE_LOCAL)
#   continue_for_repair          — open issue, repair attempts not exhausted
#   continue_for_verification    — fix just landed, must verify before external
#   continue_for_auto_repair     — auto-repair classifier triggered (P7B)
#   trigger                      — local work clean OR jammed; hand to GPT
#                                  (backwards-compat alias for EXTERNAL_REVIEW)
#   strategic_external_review    — no_work x2: ask "complete? stuck? pivot?"
#   halt                         — synthesis emitted WAGGLE_HALT
#   pause                        — operator's manual pause flag present
#   needs_manual_action          — infra failure / lock corruption / login
#
# The decision is taken by walking a fixed priority tree. The branch
# letter (A..M) is recorded in the audit object for tooling.
#
# Behavior is deterministic. The function does no I/O except to test
# whether the manual-pause flag and the halt marker exist.

$ErrorActionPreference = 'Stop'

# Verification iteration prompt prefix (REL-013). The iteration
# runner injects this BEFORE the synthesizer-supplied prompt body
# whenever the controller's previous decision was
# 'continue_for_verification'.
function Get-WaggleVerificationIterationPrefix {
    [CmdletBinding()]
    param(
        [string[]] $IssueTitles = @(),
        [string[]] $IssueIds    = @()
    )
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.AppendLine('PREVIOUS ITERATION FIX ANNOUNCEMENT - VERIFICATION REQUIRED')
    [void]$sb.AppendLine('')
    [void]$sb.AppendLine('The previous iteration attempted to fix:')
    if ($IssueIds.Count -eq 0 -and $IssueTitles.Count -eq 0) {
        [void]$sb.AppendLine('- (no issue ids supplied)')
    } else {
        for ($i = 0; $i -lt [Math]::Max($IssueIds.Count, $IssueTitles.Count); $i++) {
            $id    = if ($i -lt $IssueIds.Count)    { $IssueIds[$i] }    else { '' }
            $title = if ($i -lt $IssueTitles.Count) { $IssueTitles[$i] } else { '' }
            [void]$sb.AppendLine('- ' + $id + ': ' + $title)
        }
    }
    [void]$sb.AppendLine('')
    [void]$sb.AppendLine('This iteration''s primary purpose is verification:')
    [void]$sb.AppendLine('1. Re-run the failing test(s) the previous iteration claimed to fix')
    [void]$sb.AppendLine('2. Re-run the hardening gates if a gate-level regression was attempted')
    [void]$sb.AppendLine('3. Re-check the redaction/lock/signal/state surface if those were touched')
    [void]$sb.AppendLine('4. Confirm via internal review that the issue is genuinely resolved, not papered over')
    [void]$sb.AppendLine('')
    [void]$sb.AppendLine('If verification fails:')
    [void]$sb.AppendLine('- DO NOT attempt another fix in this same iteration')
    [void]$sb.AppendLine('- Mark the regression entry status as ''still_failing''')
    [void]$sb.AppendLine('- The orchestrator will route to external review automatically')
    [void]$sb.AppendLine('')
    [void]$sb.AppendLine('If verification is clean:')
    [void]$sb.AppendLine('- Mark the regression entry status as ''verified'' via Update-WaggleRegressionEntry')
    [void]$sb.AppendLine('- Add a brief note in raportti.md describing the verification result')
    return $sb.ToString()
}

function Save-WaggleEpochControllerDecision {
    <#
    .SYNOPSIS
    Persist the controller's audit record at
    iterations/<id>/epoch_controller_decision.json.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $IterationFolder,
        [Parameter(Mandatory)] $DecisionRecord
    )
    if (-not (Test-Path -LiteralPath $IterationFolder)) {
        New-Item -ItemType Directory -Path $IterationFolder -Force | Out-Null
    }
    $path = Join-Path $IterationFolder 'epoch_controller_decision.json'
    Set-Content -Path $path -Value (([pscustomobject]$DecisionRecord) | ConvertTo-Json -Depth 12) -Encoding UTF8
    return $path
}

function _Ect-RepairCap {
    param($IterationCycle, [string] $Severity)
    $defaults = @{ medium = 2; high = 2; critical = 1 }
    if ($null -ne $IterationCycle -and $IterationCycle.PSObject.Properties['max_repair_attempts']) {
        $cap = $IterationCycle.max_repair_attempts
        if ($null -ne $cap -and $cap.PSObject.Properties[$Severity]) {
            return [int]$cap.$Severity
        }
    }
    if ($defaults.ContainsKey($Severity)) { return [int]$defaults[$Severity] }
    return 0
}

function _Ect-ResolveBounds {
    <#
    .SYNOPSIS
    Resolve (min, target, max) from the IterationCycle config.
    Falls back to the legacy local_iterations_per_external_review
    when the new keys are absent.
    #>
    param($IterationCycle)
    $legacy = if ($null -ne $IterationCycle -and $IterationCycle.PSObject.Properties['local_iterations_per_external_review']) {
        [int]$IterationCycle.local_iterations_per_external_review
    } else { 3 }
    $minV    = if ($IterationCycle.PSObject.Properties['min_local_iterations']) { [int]$IterationCycle.min_local_iterations } else { 2 }
    $tgtV    = if ($IterationCycle.PSObject.Properties['target_local_iterations']) { [int]$IterationCycle.target_local_iterations } else { $legacy }
    $maxV    = if ($IterationCycle.PSObject.Properties['max_local_iterations']) { [int]$IterationCycle.max_local_iterations } else { [Math]::Max($legacy, 6) }
    if ($maxV -lt $tgtV) { $maxV = $tgtV }
    if ($minV -gt $tgtV) { $minV = $tgtV }
    return [pscustomobject]@{ min = $minV; target = $tgtV; max = $maxV; legacy = $legacy }
}

function Get-WaggleEpochCycleDecision {
    [CmdletBinding()]
    param(
        # Iterations completed since the last external-review trigger,
        # in chronological order. Each item should be a [pscustomobject]
        # with: iteration_id, status, no_work_classification (bool),
        # hardening_gates_failure_present (bool, optional),
        # internal_review_verdicts (architect/security/reliability),
        # internal_findings_severities (string[], optional),
        # failing_tests (string[], optional).
        [Parameter(Mandatory)] [AllowEmptyCollection()] [object[]] $IterationsSinceLastTrigger,

        # Resolved iteration_cycle config (Get-WaggleIterationCycleConfig output).
        [Parameter(Mandatory)] $IterationCycle,

        # Resolved external_review config (Get-WaggleExternalReviewConfig output).
        [Parameter(Mandatory)] $ExternalReview,

        # Path to the project root. Used to resolve manual-pause flag.
        [Parameter(Mandatory)] [string] $ProjectRoot,

        # Path to the carrier iteration's external_reviews/synthesis/<latest_epoch>/HALT.md, if any.
        [string] $HaltMarkerPath = '',

        # If a prior epoch's synthesis said decision=requires_attention, set this true.
        [bool] $RequiresAttention = $false,

        # Phase 2B-Revision (REL-013): regression-ledger entries
        # currently OPEN within this epoch. Each item is a
        # [pscustomobject] with: id, status, severity, score,
        # repair_attempts (int), issue_signature, failing_tests
        # (string[], optional), introduced_in_iteration,
        # verified_by (string[], optional).
        [object[]] $OpenIssues = @(),

        # Phase 2B-Revision: a path that exists indicates the previous
        # iteration ended in NEEDS_MANUAL_ACTION (lock corruption,
        # login_required_*.txt, etc.). When supplied + present,
        # decision is forced to needs_manual_action.
        [string] $NeedsManualActionMarkerPath = '',

        # Phase 2B-Revision: when classifier already routed an open
        # finding to TRIVIAL_AUTO_FIX or LOCAL_REPAIR (status =
        # repair_prompt_generated), set this true to make the
        # controller emit continue_for_auto_repair instead of
        # continue_for_repair / external_review.
        [bool] $AutoRepairPromptReady = $false,

        # Phase 2B-Revision: epoch budget cap from
        # finding_classifier.max_auto_repair_iterations_per_epoch.
        # When auto-repair count this epoch >= cap, the classifier's
        # output is no longer honored and findings escalate.
        [int] $AutoRepairIterationsThisEpoch = 0,
        [int] $MaxAutoRepairIterationsPerEpoch = 3
    )

    $reasons = New-Object System.Collections.Generic.List[string]
    $branch = '?'
    $decision = 'continue'
    $iters = @($IterationsSinceLastTrigger)
    $count = $iters.Count
    $bounds = _Ect-ResolveBounds -IterationCycle $IterationCycle

    # NOTE: _Decide is intentionally NOT a nested function — those
    # cannot mutate the outer scope's $decision/$branch in PS 5.1.
    # We inline the updates with [ref] semantics through script-block
    # variable refs.

    # ---- Helpers over OpenIssues ---------------------------------------
    $openIssuesArr = @($OpenIssues)
    $maxScoreOpen  = 0
    foreach ($oi in $openIssuesArr) {
        if ($oi.PSObject.Properties['score']) {
            $s = [int]$oi.score
            if ($s -gt $maxScoreOpen) { $maxScoreOpen = $s }
        }
    }
    $issuesInRepair = @($openIssuesArr | Where-Object { @('open','classified_local_repair','classified_trivial','repair_prompt_generated','repair_iteration_in_progress','fix_attempted','still_failing') -contains [string]$_.status })
    $issuesPendingVerification = @($openIssuesArr | Where-Object { [string]$_.status -eq 'verification_pending' })
    $hasManualClassified = @($openIssuesArr | Where-Object { [string]$_.status -eq 'classified_manual' }).Count -gt 0

    # ---- Branch A: NEEDS_MANUAL_ACTION ---------------------------------
    if ($NeedsManualActionMarkerPath -and (Test-Path -LiteralPath $NeedsManualActionMarkerPath)) {
        $decision = 'needs_manual_action'; $branch = 'A'
        $reasons.Add('needs_manual_action_marker_present:' + $NeedsManualActionMarkerPath) | Out-Null
    } elseif ($hasManualClassified) {
        $decision = 'needs_manual_action'; $branch = 'A'
        $reasons.Add('open issue classified_manual present') | Out-Null
    }

    # ---- Branch (pause): manual pause flag --------------------------
    if ($decision -eq 'continue') {
        $pauseFlag = ''
        if ($ExternalReview.PSObject.Properties['manual_pause_flag_relative']) {
            $rel = [string]$ExternalReview.manual_pause_flag_relative
            if ($rel) { $pauseFlag = Join-Path $ProjectRoot $rel }
        }
        if ($pauseFlag -and (Test-Path -LiteralPath $pauseFlag)) {
            $decision = 'pause'; $branch = 'PAUSE'
            $reasons.Add('manual_pause_flag_present:' + $pauseFlag) | Out-Null
        }
    }

    # ---- Branch B: HALT ------------------------------------------------
    if ($decision -eq 'continue') {
        if ($HaltMarkerPath -and (Test-Path -LiteralPath $HaltMarkerPath)) {
            $decision = 'halt'; $branch = 'B'
            $reasons.Add('halt_marker_present:' + $HaltMarkerPath) | Out-Null
        }
    }

    # ---- Branch C: STRATEGIC_EXTERNAL_REVIEW (no_work x2) -------------
    if ($decision -eq 'continue' -and $count -ge 1 -and [int]$IterationCycle.early_trigger_on_no_work_consecutive -gt 0) {
        $threshold = [int]$IterationCycle.early_trigger_on_no_work_consecutive
        $consecutive = 0
        for ($i = $iters.Count - 1; $i -ge 0; $i--) {
            $it = $iters[$i]
            if ([bool]$it.no_work_classification) { $consecutive++ } else { break }
        }
        if ($consecutive -ge $threshold) {
            $decision = 'strategic_external_review'; $branch = 'C'
            $reasons.Add("no_work_consecutive_threshold_met: $consecutive >= $threshold") | Out-Null
        }
    }

    # ---- Branch D: ESCALATE on same-issue resurrection ----------------
    if ($decision -eq 'continue' -and [bool]$IterationCycle.early_trigger_on_no_work_consecutive -ne $false) {
        # NOTE: same-issue resurrection escalation is keyed on the
        # ledger entries' history, expressed through
        # `verified_by`/`status`. If an open entry was previously
        # verified and is now back to open / still_failing, we
        # escalate.
        if ($null -ne $IterationCycle -and (
            (-not $IterationCycle.PSObject.Properties['escalate_if_same_issue_reappears']) -or
            [bool]$IterationCycle.escalate_if_same_issue_reappears
        )) {
            foreach ($oi in $openIssuesArr) {
                $verifiedBy = @()
                if ($oi.PSObject.Properties['verified_by']) { $verifiedBy = @($oi.verified_by) }
                if ($verifiedBy.Count -gt 0 -and (@('open','still_failing','reopened') -contains [string]$oi.status)) {
                    $decision = 'trigger'; $branch = 'D'
                    $reasons.Add('same_issue_resurrection_escalation:' + [string]$oi.id) | Out-Null
                    break
                }
            }
        }
    }

    # ---- Branch E: ESCALATE on repair-attempt cap reached -------------
    if ($decision -eq 'continue') {
        foreach ($oi in $openIssuesArr) {
            $sev = if ($oi.PSObject.Properties['severity']) { [string]$oi.severity } else { 'medium' }
            $cap = _Ect-RepairCap -IterationCycle $IterationCycle -Severity $sev
            $att = if ($oi.PSObject.Properties['repair_attempts']) { [int]$oi.repair_attempts } else { 0 }
            if ($cap -gt 0 -and $att -ge $cap -and (@('still_failing','fix_attempted','open','classified_local_repair','classified_trivial','repair_prompt_generated','repair_iteration_in_progress') -contains [string]$oi.status)) {
                $decision = 'trigger'; $branch = 'E'
                $reasons.Add('repair_attempt_cap_reached:' + [string]$oi.id + " sev=$sev attempts=$att cap=$cap") | Out-Null
                break
            }
        }
    }

    # ---- Branch F: ESCALATE on same-test-failed-twice ------------------
    if ($decision -eq 'continue' -and (
        $null -eq $IterationCycle -or
        (-not $IterationCycle.PSObject.Properties['escalate_if_same_test_fails_twice']) -or
        [bool]$IterationCycle.escalate_if_same_test_fails_twice
    )) {
        $testCount = @{}
        foreach ($it in $iters) {
            $tests = @()
            if ($it.PSObject.Properties['failing_tests']) { $tests = @($it.failing_tests) }
            $unique = @($tests | Select-Object -Unique)
            foreach ($t in $unique) {
                $key = [string]$t
                if (-not $testCount.ContainsKey($key)) { $testCount[$key] = 0 }
                $testCount[$key] += 1
            }
        }
        foreach ($k in $testCount.Keys) {
            if ($testCount[$k] -ge 2) {
                $decision = 'trigger'; $branch = 'F'
                $reasons.Add("same_test_failed_twice: $k") | Out-Null
                break
            }
        }
    }

    # ---- Branch G: severity-driven remaining-iter cap ------------------
    $effectiveMax = $bounds.max
    if ($maxScoreOpen -ge 80 -and ($bounds.max - $count) -gt 1) {
        $effectiveMax = $count + 1
        $reasons.Add("severity_cap: max_score_open=$maxScoreOpen >= 80 -> effectiveMax=$effectiveMax") | Out-Null
    } elseif ($maxScoreOpen -ge 60 -and ($bounds.max - $count) -gt 2) {
        $effectiveMax = $count + 2
        $reasons.Add("severity_cap: max_score_open=$maxScoreOpen >= 60 -> effectiveMax=$effectiveMax") | Out-Null
    }

    # ---- Branch H: VERIFICATION required -------------------------------
    if ($decision -eq 'continue' -and $issuesPendingVerification.Count -gt 0) {
        $decision = 'continue_for_verification'; $branch = 'H'
        $reasons.Add('verification_pending:' + (($issuesPendingVerification | ForEach-Object { [string]$_.id }) -join ',')) | Out-Null
    }

    # ---- Branch I: open repair work ------------------------------------
    # Hard ceiling beats repair work — once max_local_iterations is hit,
    # the loop must hand off to external review regardless of remaining
    # repair budget. (Per REL-013 example: "6 iterations even with
    # active repair work -> EXTERNAL_REVIEW".)
    if ($decision -eq 'continue' -and $count -lt $effectiveMax) {
        $openRepairable = @($openIssuesArr | Where-Object {
            $sev = if ($_.PSObject.Properties['severity']) { [string]$_.severity } else { 'medium' }
            $cap = _Ect-RepairCap -IterationCycle $IterationCycle -Severity $sev
            $att = if ($_.PSObject.Properties['repair_attempts']) { [int]$_.repair_attempts } else { 0 }
            (@('open','fix_attempted','still_failing','classified_local_repair','classified_trivial','repair_prompt_generated','repair_iteration_in_progress') -contains [string]$_.status) -and ($cap -le 0 -or $att -lt $cap)
        })
        if ($openRepairable.Count -gt 0) {
            if ($AutoRepairPromptReady -and $AutoRepairIterationsThisEpoch -lt $MaxAutoRepairIterationsPerEpoch) {
                $decision = 'continue_for_auto_repair'; $branch = 'I-AR'
                $reasons.Add('auto_repair_prompt_ready; budget ' + $AutoRepairIterationsThisEpoch + '/' + $MaxAutoRepairIterationsPerEpoch) | Out-Null
            } else {
                $decision = 'continue_for_repair'; $branch = 'I'
                $reasons.Add('open_repair_work:' + (($openRepairable | ForEach-Object { [string]$_.id }) -join ',')) | Out-Null
            }
        }
    }

    # ---- Branch (legacy): hardening_gate_failure / internal_critical / regression --
    # These are existing Phase 2B early-triggers; they emit 'trigger'.
    if ($decision -eq 'continue' -and [bool]$IterationCycle.early_trigger_on_hardening_gate_failure) {
        foreach ($it in $iters) {
            if ($it.PSObject.Properties['hardening_gates_failure_present'] -and [bool]$it.hardening_gates_failure_present) {
                $decision = 'trigger'; $branch = 'EG-H'
                $reasons.Add('hardening_gate_failure_in_iteration:' + [string]$it.iteration_id) | Out-Null
                break
            }
        }
    }
    if ($decision -eq 'continue' -and [bool]$IterationCycle.early_trigger_on_internal_critical_finding) {
        foreach ($it in $iters) {
            $sevs = @()
            if ($it.PSObject.Properties['internal_findings_severities']) { $sevs = @($it.internal_findings_severities) }
            $hasCritical = $false
            foreach ($s in $sevs) { if ([string]$s -eq 'critical') { $hasCritical = $true; break } }
            if ($hasCritical) {
                $decision = 'trigger'; $branch = 'EG-C'
                $reasons.Add('internal_critical_finding_in_iteration:' + [string]$it.iteration_id) | Out-Null
                break
            }
        }
    }
    if ($decision -eq 'continue' -and [bool]$IterationCycle.early_trigger_on_regression) {
        foreach ($it in $iters) {
            $verd = $null
            if ($it.PSObject.Properties['internal_review_verdicts']) { $verd = $it.internal_review_verdicts }
            $bad = $false
            if ($null -ne $verd) {
                foreach ($k in 'architect','security','reliability') {
                    $vp = $null
                    if ($verd.PSObject.Properties[$k]) { $vp = [string]$verd.$k }
                    if ($vp -eq 'fail' -or $vp -eq 'needs_changes' -or $vp -eq 'insufficient_evidence') { $bad = $true; break }
                }
            }
            if ($bad) {
                $decision = 'trigger'; $branch = 'EG-R'
                $reasons.Add('internal_review_regression_in_iteration:' + [string]$it.iteration_id) | Out-Null
                break
            }
        }
    }
    if ($decision -eq 'continue' -and $RequiresAttention) {
        $decision = 'trigger'; $branch = 'EG-A'
        $reasons.Add('prior_synthesis_requires_attention') | Out-Null
    }

    # ---- Branch J: hard ceiling (or severity cap) ----------------------
    if ($decision -eq 'continue' -and $count -ge $effectiveMax) {
        $decision = 'trigger'; $branch = 'J'
        $reasons.Add("hard_ceiling_reached: count $count >= effectiveMax $effectiveMax (designMax $($bounds.max))") | Out-Null
    }

    # ---- Branch K: below minimum -> CONTINUE_LOCAL --------------------
    if ($decision -eq 'continue' -and $count -lt $bounds.min) {
        $branch = 'K'
        $reasons.Add("below_minimum: count $count < min $($bounds.min)") | Out-Null
    }

    # ---- Branch L: target reached AND clean ---------------------------
    if ($decision -eq 'continue' -and $count -ge $bounds.target) {
        $hasOpenMediumOrHigher = @($openIssuesArr | Where-Object { (@('medium','high','critical') -contains [string]$_.severity) -and (@('verified','fixed','false_positive') -notcontains [string]$_.status) }).Count -gt 0
        if (-not $hasOpenMediumOrHigher) {
            $decision = 'trigger'; $branch = 'L'
            $reasons.Add("target_reached_and_clean: count $count >= target $($bounds.target)") | Out-Null
        }
    }

    # ---- Branch M: default (CONTINUE_LOCAL) ---------------------------
    if ($decision -eq 'continue' -and $branch -eq '?') {
        $branch = 'M'
        $reasons.Add('default_continue_local') | Out-Null
    }

    # Empty iteration list short-circuits everything to continue.
    if ($count -eq 0) {
        $decision = 'continue'
        $branch = 'M'
        $reasons.Clear()
        $reasons.Add('no iterations completed since last trigger') | Out-Null
    }

    # ---- Cumulative N-iteration trigger (legacy compat) ---------------
    # When the new bounds aren't supplied, this matches the original
    # Phase 2B behavior. Only fires when no other branch claimed.
    if ($decision -eq 'continue' -and $branch -eq 'M' -and -not $IterationCycle.PSObject.Properties['target_local_iterations']) {
        $N = [int]$IterationCycle.local_iterations_per_external_review
        if ($count -ge $N) {
            $decision = 'trigger'; $branch = 'N'
            $reasons.Add("count $count >= local_iterations_per_external_review $N") | Out-Null
        }
    }

    return [pscustomobject]@{
        decision                              = $decision
        decision_priority_branch              = $branch
        reasons                               = $reasons.ToArray()
        iterations_count                      = $count
        open_regressions_count                = $openIssuesArr.Count
        max_regression_score                  = $maxScoreOpen
        issues_in_repair                      = @($issuesInRepair | ForEach-Object { [string]$_.id })
        issues_pending_verification           = @($issuesPendingVerification | ForEach-Object { [string]$_.id })
        remaining_iterations_cap_for_this_epoch = $effectiveMax
        decided_at_utc                        = (Get-Date).ToUniversalTime().ToString('o')
    }
}
