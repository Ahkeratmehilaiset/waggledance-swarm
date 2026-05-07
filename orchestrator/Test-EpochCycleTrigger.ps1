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
Assert-True '3 healthy iters: reason cites N threshold' (($d.reasons -join ' ') -match 'local_iterations_per_external_review')

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
Assert-True '2 consec no-work: trigger' ($d.decision -eq 'trigger')
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

# --- Cleanup --------------------------------------------------------

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $tmp

Write-Host ''
Write-Host "Result: $Script:Pass/$($Script:Pass + $Script:Fail) tests passed" -ForegroundColor Cyan
if ($Script:Fail -gt 0) { exit 1 } else { exit 0 }
