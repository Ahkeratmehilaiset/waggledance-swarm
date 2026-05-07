#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2A-2 unit tests for orchestrator/lib/review/ReviewSchema.ps1.
.DESCRIPTION
    Covers happy path for each role + every required schema failure
    mode. PS 5.1 compatible. No external dependencies.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$libDir = Join-Path $PSScriptRoot 'lib\review'
. (Join-Path $libDir 'ReviewSchema.ps1')

$Script:Pass = 0
$Script:Fail = 0

function Assert-True {
    param([string] $Name, [bool] $Cond, [string] $Detail = '')
    if ($Cond) {
        Write-Host "PASS  $Name" -ForegroundColor Green
        $Script:Pass++
    } else {
        Write-Host "FAIL  $Name $Detail" -ForegroundColor Red
        $Script:Fail++
    }
}

function New-GoodReviewObject {
    param([string] $Role = 'architect', [string] $IterationId = '2026-05-06_19-45-54')
    $j = @"
{
  "role": "$Role",
  "target_iteration_id": "$IterationId",
  "source_package_path": "iterations/$IterationId/llm_input_package.md",
  "summary": "Looks fine.",
  "verdict": "pass",
  "findings": [],
  "metrics": { "files_reviewed": 1, "lines_reviewed": 100, "review_duration_seconds": 5 },
  "completed": true
}
"@
    return ($j | ConvertFrom-Json)
}

# ----------------- happy path: each role -----------------

$res = Test-ReviewObject -Object (New-GoodReviewObject -Role 'architect')
Assert-True 'happy: architect valid' ($res.ok -and $res.errors.Count -eq 0) ($res.errors -join '; ')

$res = Test-ReviewObject -Object (New-GoodReviewObject -Role 'security')
Assert-True 'happy: security valid' ($res.ok -and $res.errors.Count -eq 0) ($res.errors -join '; ')

$res = Test-ReviewObject -Object (New-GoodReviewObject -Role 'reliability')
Assert-True 'happy: reliability valid' ($res.ok -and $res.errors.Count -eq 0) ($res.errors -join '; ')

# ----------------- happy path: with findings -----------------

$j = @'
{
  "role": "security",
  "target_iteration_id": "2026-05-06_19-45-54",
  "source_package_path": "iterations/2026-05-06_19-45-54/llm_input_package.md",
  "summary": "One medium finding.",
  "verdict": "needs_attention",
  "findings": [
    {
      "id": "SEC-001",
      "severity": "medium",
      "title": "Unredacted email in package metadata",
      "where": "git_metadata.json",
      "evidence": "an email at line 3",
      "why_it_matters": "PII leak surface",
      "recommended_action": "Enable optional EMAIL redaction rule"
    }
  ],
  "metrics": { "files_reviewed": 5, "lines_reviewed": 1200, "review_duration_seconds": 60 },
  "completed": true
}
'@
$obj = $j | ConvertFrom-Json
$res = Test-ReviewObject -Object $obj
Assert-True 'happy: with one medium finding' ($res.ok) ($res.errors -join '; ')

# ----------------- failure modes -----------------

# Invalid role
$bad = New-GoodReviewObject
$bad.role = 'random'
$res = Test-ReviewObject -Object $bad
Assert-True 'fail: invalid role rejected' ((-not $res.ok) -and (($res.errors -join ' ') -match 'role must be one of'))

# Missing summary
$bad = New-GoodReviewObject
$bad = $bad | Select-Object -Property * -ExcludeProperty summary
$res = Test-ReviewObject -Object $bad
Assert-True 'fail: missing summary rejected' ((-not $res.ok) -and (($res.errors -join ' ') -match 'missing top-level field: summary'))

# Missing findings
$bad = New-GoodReviewObject
$bad = $bad | Select-Object -Property * -ExcludeProperty findings
$res = Test-ReviewObject -Object $bad
Assert-True 'fail: missing findings rejected' ((-not $res.ok) -and (($res.errors -join ' ') -match 'missing top-level field: findings'))

# Invalid severity
$j = @'
{
  "role": "architect",
  "target_iteration_id": "X",
  "source_package_path": "Y",
  "summary": "S",
  "verdict": "pass",
  "findings": [
    {
      "id": "ARCH-001",
      "severity": "blocker",
      "title": "T",
      "where": "W",
      "evidence": "E",
      "why_it_matters": "M",
      "recommended_action": "A"
    }
  ],
  "metrics": { "files_reviewed": 0, "lines_reviewed": 0, "review_duration_seconds": 0 },
  "completed": true
}
'@
$bad = $j | ConvertFrom-Json
$res = Test-ReviewObject -Object $bad
Assert-True 'fail: invalid severity rejected' ((-not $res.ok) -and (($res.errors -join ' ') -match 'severity must be one of'))

# Invalid metrics shape (missing field)
$bad = New-GoodReviewObject
$bad.metrics = ([pscustomobject]@{ files_reviewed = 1; lines_reviewed = 1 })
$res = Test-ReviewObject -Object $bad
Assert-True 'fail: missing metrics.review_duration_seconds rejected' ((-not $res.ok) -and (($res.errors -join ' ') -match 'metrics missing field: review_duration_seconds'))

# Invalid metrics value (negative)
$bad = New-GoodReviewObject
$bad.metrics.files_reviewed = -1
$res = Test-ReviewObject -Object $bad
Assert-True 'fail: negative metric rejected' ((-not $res.ok) -and (($res.errors -join ' ') -match 'metrics.files_reviewed must be a non-negative integer'))

# Invalid verdict
$bad = New-GoodReviewObject
$bad.verdict = 'maybe'
$res = Test-ReviewObject -Object $bad
Assert-True 'fail: invalid verdict rejected' ((-not $res.ok) -and (($res.errors -join ' ') -match 'verdict must be one of'))

# Completed false
$bad = New-GoodReviewObject
$bad.completed = $false
$res = Test-ReviewObject -Object $bad
Assert-True 'fail: completed=false rejected' ((-not $res.ok) -and (($res.errors -join ' ') -match 'completed must be true'))

# Empty summary
$bad = New-GoodReviewObject
$bad.summary = ''
$res = Test-ReviewObject -Object $bad
Assert-True 'fail: empty summary rejected' ((-not $res.ok) -and (($res.errors -join ' ') -match 'summary must be a non-empty string'))

# Malformed JSON
$res = ConvertFrom-ReviewJsonText -Text "{ this is not json"
Assert-True 'fail: malformed json rejected' ((-not $res.ok) -and (($res.errors -join ' ') -match 'json parse failed'))

# Empty json
$res = ConvertFrom-ReviewJsonText -Text ""
Assert-True 'fail: empty json text rejected' ((-not $res.ok) -and (($res.errors -join ' ') -match 'empty json text'))

# Finding missing field
$j = @'
{
  "role": "architect",
  "target_iteration_id": "X",
  "source_package_path": "Y",
  "summary": "S",
  "verdict": "pass_with_notes",
  "findings": [
    {
      "id": "ARCH-001",
      "severity": "low",
      "title": "T",
      "where": "W",
      "evidence": "E",
      "why_it_matters": "M"
    }
  ],
  "metrics": { "files_reviewed": 0, "lines_reviewed": 0, "review_duration_seconds": 0 },
  "completed": true
}
'@
$bad = $j | ConvertFrom-Json
$res = Test-ReviewObject -Object $bad
Assert-True 'fail: finding missing recommended_action rejected' ((-not $res.ok) -and (($res.errors -join ' ') -match 'findings\[0\] missing field: recommended_action'))

# ----------------- Phase 2B-Revision (SEC-009) --------------------
# Optional reviewer_self_id and suggested_next_actions[] fields.
# Old-shape reviews still validate. New-shape (well-formed) validates.
# Malformed shapes fail with explicit reasons.

# Old shape (no self-id, no proposals): the New-GoodReviewObject test
# above already exercises this — confirm explicitly here.
$old = New-GoodReviewObject -Role 'architect'
$res = Test-ReviewObject -Object $old
Assert-True 'sec-009: old-shape review (no self-id, no proposals) still validates' $res.ok

# New shape with both self-id and proposals.
$newReview = @{
    role = 'architect'
    target_iteration_id = 'iid'
    source_package_path = 'pkg.md'
    summary = 's'
    verdict = 'pass'
    reviewer_self_id = @{
        claimed_model_name = 'Claude Opus 4.7'
        claimed_version = $null
        training_cutoff = $null
        self_assessed_strengths_for_this_review = @('strength a')
        self_assessed_limitations_for_this_review = @('limitation a')
        estimated_context_window_kb = $null
        uses_extended_thinking_or_reasoning_mode = $false
        runtime = 'claude_code'
    }
    findings = @()
    suggested_next_actions = @(
        @{ id = 'PROP-001'; title = 't'; rationale = 'r'; approach = 'a';
           estimated_effort = 'small'; risks = 'rk'; expected_payoff = 'p' }
    )
    metrics = @{ files_reviewed = 0; lines_reviewed = 0; review_duration_seconds = 0 }
    completed = $true
}
# Convert to a real pscustomobject (the validator expects either dict or pscustomobject).
$newReviewObj = ([pscustomobject]$newReview)
# Need to also wrap nested hashtables as pscustomobjects for the validator's field probe.
$newReviewObj.reviewer_self_id = ([pscustomobject]$newReviewObj.reviewer_self_id)
$newReviewObj.suggested_next_actions = @($newReviewObj.suggested_next_actions | ForEach-Object { [pscustomobject]$_ })
$newReviewObj.metrics = ([pscustomobject]$newReviewObj.metrics)
$res = Test-ReviewObject -Object $newReviewObj
Assert-True 'sec-009: new-shape review (with self-id + proposals) validates' $res.ok ('errors=' + ($res.errors -join '; '))

# Malformed self-id: missing claimed_model_name.
$badSelfId = $newReviewObj | Select-Object * -ExcludeProperty reviewer_self_id
$badSelfId = [pscustomobject]@{}
foreach ($p in $newReviewObj.PSObject.Properties) { $badSelfId | Add-Member -NotePropertyName $p.Name -NotePropertyValue $p.Value -Force }
$badSelfId.reviewer_self_id = ([pscustomobject]@{
    claimed_version = $null
    self_assessed_strengths_for_this_review = @('s')
    self_assessed_limitations_for_this_review = @('l')
    uses_extended_thinking_or_reasoning_mode = $false
})
$res = Test-ReviewObject -Object $badSelfId
Assert-True 'sec-009: self-id missing claimed_model_name rejected' ((-not $res.ok) -and (($res.errors -join ' ') -match 'reviewer_self_id missing: claimed_model_name'))

# Malformed self-id: uses_extended_thinking_or_reasoning_mode wrong type.
$badType = [pscustomobject]@{}
foreach ($p in $newReviewObj.PSObject.Properties) { $badType | Add-Member -NotePropertyName $p.Name -NotePropertyValue $p.Value -Force }
$badType.reviewer_self_id = ([pscustomobject]@{
    claimed_model_name = 'X'
    self_assessed_strengths_for_this_review = @('s')
    self_assessed_limitations_for_this_review = @('l')
    uses_extended_thinking_or_reasoning_mode = 'not-a-bool'
})
$res = Test-ReviewObject -Object $badType
Assert-True 'sec-009: self-id non-boolean reasoning_mode rejected' ((-not $res.ok) -and (($res.errors -join ' ') -match 'uses_extended_thinking_or_reasoning_mode must be boolean'))

# Malformed proposal: missing rationale.
$badProposal = [pscustomobject]@{}
foreach ($p in $newReviewObj.PSObject.Properties) { $badProposal | Add-Member -NotePropertyName $p.Name -NotePropertyValue $p.Value -Force }
$badProposal.suggested_next_actions = @(([pscustomobject]@{
    id = 'PROP-001'; title = 't'; approach = 'a'
    estimated_effort = 'small'; risks = 'rk'; expected_payoff = 'p'
}))
$res = Test-ReviewObject -Object $badProposal
Assert-True 'sec-009: proposal missing rationale rejected' ((-not $res.ok) -and (($res.errors -join ' ') -match 'suggested_next_actions\[0\] missing field: rationale'))

# Malformed proposal: estimated_effort not in enum.
$badEffort = [pscustomobject]@{}
foreach ($p in $newReviewObj.PSObject.Properties) { $badEffort | Add-Member -NotePropertyName $p.Name -NotePropertyValue $p.Value -Force }
$badEffort.suggested_next_actions = @(([pscustomobject]@{
    id = 'PROP-001'; title = 't'; rationale = 'r'; approach = 'a'
    estimated_effort = 'gigantic'; risks = 'rk'; expected_payoff = 'p'
}))
$res = Test-ReviewObject -Object $badEffort
Assert-True 'sec-009: proposal estimated_effort out of enum rejected' ((-not $res.ok) -and (($res.errors -join ' ') -match 'estimated_effort must be one of'))

# Malformed self-id.runtime: must be claude_code if present.
$badRuntime = [pscustomobject]@{}
foreach ($p in $newReviewObj.PSObject.Properties) { $badRuntime | Add-Member -NotePropertyName $p.Name -NotePropertyValue $p.Value -Force }
$badRuntime.reviewer_self_id = ([pscustomobject]@{
    claimed_model_name = 'X'
    self_assessed_strengths_for_this_review = @('s')
    self_assessed_limitations_for_this_review = @('l')
    uses_extended_thinking_or_reasoning_mode = $false
    runtime = 'claude_web'
})
$res = Test-ReviewObject -Object $badRuntime
Assert-True 'sec-009: self-id runtime must be claude_code for internal reviews' ((-not $res.ok) -and (($res.errors -join ' ') -match "runtime must be 'claude_code'"))

# ----------------- summary -----------------

Write-Host ''
Write-Host "Result: $Script:Pass/$($Script:Pass + $Script:Fail) tests passed" -ForegroundColor Cyan
if ($Script:Fail -gt 0) { exit 1 } else { exit 0 }
