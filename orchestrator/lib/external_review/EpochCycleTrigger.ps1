# EpochCycleTrigger.ps1
#
# Phase 2B P11: pure decision function that, given an iteration
# history + config, returns one of:
#   - continue           : run the next local iteration normally
#   - trigger            : freeze further local iterations and emit
#                          the external-review queue
#   - halt               : the prior synthesis decided to stop the
#                          cycle (HALT.md present)
#   - pause              : operator manually requested to pause
#                          (pause_external_review.flag present)
#
# Behavior is deterministic. The function does no I/O beyond reading
# files at paths the caller supplies; all configuration is taken from
# the resolved iteration_cycle profile.

$ErrorActionPreference = 'Stop'

function Get-WaggleEpochCycleDecision {
    [CmdletBinding()]
    param(
        # Iterations completed since the last external-review trigger,
        # in chronological order. Each item should be a [pscustomobject]
        # with: iteration_id, status, no_work_classification (bool),
        # hardening_gates_failure_present (bool, optional),
        # internal_review_verdicts (object with architect/security/reliability),
        # internal_findings_severities (string[], optional, e.g. ('critical','high','medium')).
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
        [bool] $RequiresAttention = $false
    )

    $reasons = New-Object System.Collections.Generic.List[string]
    $decision = 'continue'

    $pauseFlag = ''
    if ($ExternalReview.PSObject.Properties['manual_pause_flag_relative']) {
        $rel = [string]$ExternalReview.manual_pause_flag_relative
        if ($rel) { $pauseFlag = Join-Path $ProjectRoot $rel }
    }
    if ($pauseFlag -and (Test-Path -LiteralPath $pauseFlag)) {
        $reasons.Add('manual_pause_flag_present:' + $pauseFlag) | Out-Null
        return [pscustomobject]@{
            decision = 'pause'
            reasons = $reasons.ToArray()
            iterations_count = @($IterationsSinceLastTrigger).Count
        }
    }

    if ($HaltMarkerPath -and (Test-Path -LiteralPath $HaltMarkerPath)) {
        $reasons.Add('halt_marker_present:' + $HaltMarkerPath) | Out-Null
        return [pscustomobject]@{
            decision = 'halt'
            reasons = $reasons.ToArray()
            iterations_count = @($IterationsSinceLastTrigger).Count
        }
    }

    $iters = @($IterationsSinceLastTrigger)
    $count = $iters.Count

    if ($count -eq 0) {
        return [pscustomobject]@{
            decision = 'continue'
            reasons = @('no iterations completed since last trigger')
            iterations_count = 0
        }
    }

    # ---- Early triggers -------------------------------------------------

    # Regression: any iteration's hardening_gates_failure_present == true
    if ([bool]$IterationCycle.early_trigger_on_hardening_gate_failure) {
        foreach ($it in $iters) {
            if ($it.PSObject.Properties['hardening_gates_failure_present'] -and [bool]$it.hardening_gates_failure_present) {
                $reasons.Add('hardening_gate_failure_in_iteration:' + [string]$it.iteration_id) | Out-Null
                $decision = 'trigger'
                break
            }
        }
    }

    if ($decision -eq 'continue' -and [bool]$IterationCycle.early_trigger_on_internal_critical_finding) {
        foreach ($it in $iters) {
            $sevs = @()
            if ($it.PSObject.Properties['internal_findings_severities']) {
                $sevs = @($it.internal_findings_severities)
            }
            $hasCritical = $false
            foreach ($s in $sevs) { if ([string]$s -eq 'critical') { $hasCritical = $true; break } }
            if ($hasCritical) {
                $reasons.Add('internal_critical_finding_in_iteration:' + [string]$it.iteration_id) | Out-Null
                $decision = 'trigger'
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
                $reasons.Add('internal_review_regression_in_iteration:' + [string]$it.iteration_id) | Out-Null
                $decision = 'trigger'
                break
            }
        }
    }

    if ($decision -eq 'continue' -and [int]$IterationCycle.early_trigger_on_no_work_consecutive -gt 0) {
        $threshold = [int]$IterationCycle.early_trigger_on_no_work_consecutive
        $consecutive = 0
        for ($i = $iters.Count - 1; $i -ge 0; $i--) {
            $it = $iters[$i]
            if ([bool]$it.no_work_classification) { $consecutive++ } else { break }
        }
        if ($consecutive -ge $threshold) {
            $reasons.Add("no_work_consecutive_threshold_met: $consecutive >= $threshold") | Out-Null
            $decision = 'trigger'
        }
    }

    if ($decision -eq 'continue' -and $RequiresAttention) {
        $reasons.Add('prior_synthesis_requires_attention') | Out-Null
        $decision = 'trigger'
    }

    # ---- Cumulative N-iteration trigger ---------------------------------
    if ($decision -eq 'continue') {
        $N = [int]$IterationCycle.local_iterations_per_external_review
        if ($count -ge $N) {
            $reasons.Add("count $count >= local_iterations_per_external_review $N") | Out-Null
            $decision = 'trigger'
        } else {
            $reasons.Add("count $count < local_iterations_per_external_review $N") | Out-Null
        }
    }

    return [pscustomobject]@{
        decision = $decision
        reasons = $reasons.ToArray()
        iterations_count = $count
    }
}
