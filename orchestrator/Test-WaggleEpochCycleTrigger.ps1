#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2B P11: CLI wrapper that decides whether the orchestrator
    should run another local iteration, trigger an external review
    epoch, halt, or pause. Returns the decision in a small JSON
    record on stdout and exits 0 (decision != 'halt') or 0 (always).

    The verb-noun convention is borrowed from PowerShell built-ins:
    'Test-' returns a value, not a side-effect.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $ConfigPath,
    [Parameter(Mandatory)] [string[]] $IterationIds,
    [string] $LatestSynthesisHaltPath = '',
    [bool] $RequiresAttention = $false
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib/external_review/ProviderProfiles.ps1')
. (Join-Path $PSScriptRoot 'lib/external_review/EvidenceBundler.ps1')
. (Join-Path $PSScriptRoot 'lib/external_review/EpochCycleTrigger.ps1')

if (-not (Test-Path -LiteralPath $ConfigPath)) { throw "config not found: $ConfigPath" }
$cfg = Get-Content -Raw -Path $ConfigPath -Encoding UTF8 | ConvertFrom-Json
$projectRoot = $cfg.projectRoot
$iterationsDir = if ($cfg.PSObject.Properties['iterationsDir'] -and $cfg.iterationsDir) { [string]$cfg.iterationsDir } else { 'iterations' }
$iterRoot = Join-Path $projectRoot $iterationsDir

$ic = Get-WaggleIterationCycleConfig -Config $cfg
$er = Get-WaggleExternalReviewConfig -Config $cfg

# Build per-iteration metadata records the library expects.
$records = New-Object System.Collections.Generic.List[object]
foreach ($iid in $IterationIds) {
    $iterFolder = Join-Path $iterRoot $iid
    if (-not (Test-Path -LiteralPath $iterFolder)) {
        throw "iteration folder missing: $iterFolder"
    }
    $verdicts = Get-WaggleIterationInternalReviewVerdicts -IterationFolder $iterFolder
    $nw = Get-WaggleNoWorkClassification -IterationFolder $iterFolder `
            -DiffMinBytes $ic.no_work_diff_min_bytes `
            -RaporttiMinBytes $ic.no_work_raportti_min_bytes `
            -StdoutMinMeaningfulBytes $ic.no_work_stdout_min_meaningful_bytes `
            -RaporttiPath (Join-Path $projectRoot $cfg.reportFile)

    # Internal findings severities: pull from each role's findings[].severity
    $severities = New-Object System.Collections.Generic.List[string]
    foreach ($role in 'architect','security','reliability') {
        $jp = Join-Path $iterFolder ('reviews/' + $role + '.json')
        if (Test-Path -LiteralPath $jp) {
            try {
                $j = Get-Content -Raw -Path $jp -Encoding UTF8 | ConvertFrom-Json
                if ($j.PSObject.Properties['findings']) {
                    foreach ($f in @($j.findings)) {
                        if ($f -and $f.PSObject.Properties['severity']) {
                            $severities.Add([string]$f.severity) | Out-Null
                        }
                    }
                }
            } catch {}
        }
    }

    # Hardening-gates regression: read from latest run, if present.
    $gateFail = $false
    $latestGate = Join-Path $projectRoot 'docs/runs/hardening_gates/latest.json'
    if (Test-Path -LiteralPath $latestGate) {
        try {
            $gj = Get-Content -Raw -Path $latestGate -Encoding UTF8 | ConvertFrom-Json
            if ($gj.PSObject.Properties['overall_ok'] -and -not [bool]$gj.overall_ok) {
                $gateFail = $true
            }
        } catch {}
    }

    $records.Add([pscustomobject]@{
        iteration_id = $iid
        status = 'COMPLETED'
        no_work_classification = [bool]$nw.no_work
        hardening_gates_failure_present = $gateFail
        internal_review_verdicts = $verdicts
        internal_findings_severities = $severities.ToArray()
    }) | Out-Null
}

$decision = Get-WaggleEpochCycleDecision `
    -IterationsSinceLastTrigger $records.ToArray() `
    -IterationCycle $ic `
    -ExternalReview $er `
    -ProjectRoot $projectRoot `
    -HaltMarkerPath $LatestSynthesisHaltPath `
    -RequiresAttention $RequiresAttention

$out = [ordered]@{
    decision = $decision.decision
    reasons = $decision.reasons
    iterations_count = $decision.iterations_count
    iteration_ids = $IterationIds
    decided_at_utc = (Get-Date).ToUniversalTime().ToString('o')
}
Write-Output (([pscustomobject]$out) | ConvertTo-Json -Depth 6)
exit 0
