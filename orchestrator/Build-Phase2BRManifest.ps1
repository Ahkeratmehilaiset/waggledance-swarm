#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2B-Revision (P13): build a manifest of every file Phase
    2B-Revision added or modified, with SHA-256 + size, and a
    self-check that re-verifies every entry. Output:
      docs/runs/orchestrator_phase2br_cockpit_codex_regression_2026_05_07/manifest.json
      docs/runs/orchestrator_phase2br_cockpit_codex_regression_2026_05_07/manifest_self_check.json
#>
[CmdletBinding()] param()
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$runDir = Join-Path $repoRoot 'docs/runs/orchestrator_phase2br_cockpit_codex_regression_2026_05_07'
if (-not (Test-Path -LiteralPath $runDir)) { New-Item -ItemType Directory -Path $runDir -Force | Out-Null }

$files = @(
    # Schemas (new in Phase 2B-Revision)
    'schemas/codex_findings.schema.json'
    'schemas/cockpit_data.schema.json'
    'schemas/proposal_matrix.schema.json'
    'schemas/regression_ledger.schema.json'

    # Schemas (modified)
    'schemas/review.schema.json'

    # Library modules (new)
    'orchestrator/lib/RegressionLedger.ps1'
    'orchestrator/lib/external_review/FindingClassifier.ps1'

    # Library modules (modified)
    'orchestrator/lib/external_review/EpochCycleTrigger.ps1'
    'orchestrator/lib/review/ReviewSchema.ps1'

    # Orchestrator scripts (new)
    'orchestrator/Build-Phase2BRManifest.ps1'
    'orchestrator/Build-WaggleAutoRepairPrompt.ps1'
    'orchestrator/Build-WaggleCockpitData.ps1'
    'orchestrator/Build-WaggleProposalMatrix.ps1'
    'orchestrator/Import-WaggleCodexFindings.ps1'
    'orchestrator/Open-WaggleCockpit.ps1'
    'orchestrator/Run-Phase2BREndToEndDryRun.ps1'

    # Orchestrator scripts (modified)
    'orchestrator/Export-WaggleExternalReviewQueue.ps1'
    'orchestrator/New-WaggleSynthesisPasteBlock.ps1'
    'orchestrator/Run-WaggleHardeningGates.ps1'

    # Tests (new)
    'orchestrator/Test-CockpitData.ps1'
    'orchestrator/Test-CodexImport.ps1'
    'orchestrator/Test-FindingClassifier.ps1'
    'orchestrator/Test-ProposalMatrix.ps1'
    'orchestrator/Test-RegressionLedger.ps1'

    # Tests (modified)
    'orchestrator/Test-EpochCycleTrigger.ps1'
    'orchestrator/Test-ExternalReviewQueue.ps1'
    'orchestrator/Test-ReviewAdapter.ps1'
    'orchestrator/Test-ReviewSchema.ps1'
    'orchestrator/Test-SynthesisPasteBlock.ps1'

    # Prompts (new)
    'prompts/codex_scout.md'

    # Prompts (modified)
    'prompts/external_review/providers/claude_web.md'
    'prompts/external_review/synthesis_gpt.md'
    'prompts/review/architect.md'
    'prompts/review/security.md'
    'prompts/review/reliability.md'

    # HTML cockpit (new)
    'review_cockpit.html'

    # Config (modified)
    'orchestrator.config.example.json'

    # Ledger (modified)
    'docs/design/phase_fix_ledger.json'
    'docs/design/phase_fix_ledger.md'

    # Quality docs (new)
    'docs/quality/regression_ledger.md'

    # Run dir docs
    'docs/runs/orchestrator_phase2br_cockpit_codex_regression_2026_05_07/baseline.md'
    'docs/runs/orchestrator_phase2br_cockpit_codex_regression_2026_05_07/cockpit_setup.md'
    'docs/runs/orchestrator_phase2br_cockpit_codex_regression_2026_05_07/codex_setup.md'
    'docs/runs/orchestrator_phase2br_cockpit_codex_regression_2026_05_07/core_findings.md'
    'docs/runs/orchestrator_phase2br_cockpit_codex_regression_2026_05_07/dry_run.json'
    'docs/runs/orchestrator_phase2br_cockpit_codex_regression_2026_05_07/dry_run_log.md'
    'docs/runs/orchestrator_phase2br_cockpit_codex_regression_2026_05_07/failures.md'
    'docs/runs/orchestrator_phase2br_cockpit_codex_regression_2026_05_07/final_report.md'

    # Phase 2B retroactive note (the Phase 2B cowork_handoff was
    # superseded by ARCH-010 / ARCH-011 in Phase 2B-Revision)
    'docs/runs/orchestrator_phase2b_cross_vendor_2026_05_06/cowork_handoff.md'
)

function _Sha256File {
    param([string] $Path)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.IO.File]::ReadAllBytes($Path)
        $hash = $sha.ComputeHash($bytes)
        return ([System.BitConverter]::ToString($hash) -replace '-','').ToLower()
    } finally {
        $sha.Dispose()
    }
}

$entries = New-Object System.Collections.Generic.List[object]
$missing = New-Object System.Collections.Generic.List[string]
foreach ($rel in $files) {
    $abs = Join-Path $repoRoot $rel
    if (-not (Test-Path -LiteralPath $abs)) {
        $missing.Add($rel) | Out-Null
        $entries.Add([pscustomobject]@{
            relative_path = $rel; ok = $false; reason = 'file missing'
            sha256 = ''; size_bytes = 0
        }) | Out-Null
        continue
    }
    $size = (Get-Item -LiteralPath $abs).Length
    $sha = _Sha256File -Path $abs
    $entries.Add([pscustomobject]@{
        relative_path = $rel; ok = $true; reason = ''
        sha256 = $sha; size_bytes = [int64]$size
    }) | Out-Null
}

$manifest = [ordered]@{
    title = 'Phase 2B-Revision file manifest'
    phase = 'Phase 2B-Revision'
    generated_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    repo_root = $repoRoot
    files_count = $entries.Count
    missing_count = $missing.Count
    entries = $entries.ToArray()
}
$manifestPath = Join-Path $runDir 'manifest.json'
Set-Content -Path $manifestPath -Value (([pscustomobject]$manifest) | ConvertTo-Json -Depth 4) -Encoding UTF8

# Self-check: re-read manifest, verify each entry's SHA + size still match.
$loaded = Get-Content -Raw -Path $manifestPath -Encoding UTF8 | ConvertFrom-Json
$selfChecks = New-Object System.Collections.Generic.List[object]
$selfFail = 0
foreach ($e in @($loaded.entries)) {
    $abs = Join-Path $repoRoot $e.relative_path
    $rec = [ordered]@{
        relative_path = [string]$e.relative_path
        manifest_sha256 = [string]$e.sha256
        manifest_size_bytes = [int64]$e.size_bytes
        recomputed_sha256 = ''
        recomputed_size_bytes = 0
        sha_match = $false
        size_match = $false
        ok = $false
        reason = ''
    }
    if (-not (Test-Path -LiteralPath $abs)) {
        $rec.reason = 'file missing'
        $selfFail++
    } else {
        $rec.recomputed_size_bytes = [int64]((Get-Item -LiteralPath $abs).Length)
        $rec.recomputed_sha256 = _Sha256File -Path $abs
        $rec.sha_match = ($rec.manifest_sha256 -eq $rec.recomputed_sha256)
        $rec.size_match = ($rec.manifest_size_bytes -eq $rec.recomputed_size_bytes)
        $rec.ok = ($rec.sha_match -and $rec.size_match -and $e.ok)
        if (-not $rec.ok) {
            $selfFail++
            if (-not $rec.sha_match) { $rec.reason = 'sha256 mismatch' }
            elseif (-not $rec.size_match) { $rec.reason = 'size mismatch' }
            else { $rec.reason = 'manifest entry not ok' }
        }
    }
    $selfChecks.Add(([pscustomobject]$rec)) | Out-Null
}

$selfCheck = [ordered]@{
    title = 'Phase 2B-Revision manifest self-check'
    phase = 'Phase 2B-Revision'
    generated_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    manifest_path = $manifestPath
    files_count = $selfChecks.Count
    failed_count = $selfFail
    overall_ok = ($selfFail -eq 0)
    checks = $selfChecks.ToArray()
}
$selfCheckPath = Join-Path $runDir 'manifest_self_check.json'
Set-Content -Path $selfCheckPath -Value (([pscustomobject]$selfCheck) | ConvertTo-Json -Depth 4) -Encoding UTF8

Write-Host ('Wrote ' + $manifestPath)
Write-Host ('Wrote ' + $selfCheckPath)
Write-Host ('files_count   : ' + $entries.Count)
Write-Host ('missing       : ' + $missing.Count)
Write-Host ('self-check failed: ' + $selfFail)
if ($missing.Count -gt 0 -or $selfFail -gt 0) {
    Write-Host 'OVERALL: FAIL' -ForegroundColor Red
    foreach ($m in $missing) { Write-Host ('  missing: ' + $m) -ForegroundColor Red }
    exit 1
}
Write-Host 'OVERALL: PASS' -ForegroundColor Green
exit 0
