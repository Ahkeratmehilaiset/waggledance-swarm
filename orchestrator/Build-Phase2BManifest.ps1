#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2B P18: build a manifest of every file Phase 2B added or
    modified, with SHA-256 + size, and a self-check that re-verifies
    every entry. Output:
      docs/runs/orchestrator_phase2b_cross_vendor_2026_05_06/manifest.json
      docs/runs/orchestrator_phase2b_cross_vendor_2026_05_06/manifest_self_check.json
#>
[CmdletBinding()] param()
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$runDir = Join-Path $repoRoot 'docs/runs/orchestrator_phase2b_cross_vendor_2026_05_06'
if (-not (Test-Path -LiteralPath $runDir)) { New-Item -ItemType Directory -Path $runDir -Force | Out-Null }

$files = @(
    # Schemas
    'schemas/external_review.schema.json'
    'schemas/review_synthesis.schema.json'
    'schemas/epoch_evidence.schema.json'

    # Library modules (new)
    'orchestrator/lib/external_review/EvidenceBundler.ps1'
    'orchestrator/lib/external_review/EpochCycleTrigger.ps1'
    'orchestrator/lib/external_review/ExternalReviewSchema.ps1'
    'orchestrator/lib/external_review/ProviderProfiles.ps1'
    'orchestrator/lib/external_review/SynthesisSchema.ps1'

    # Orchestrator scripts (new)
    'orchestrator/Build-WaggleEpochEvidence.ps1'
    'orchestrator/Export-WaggleExternalReviewQueue.ps1'
    'orchestrator/Import-WaggleExternalReviewResponse.ps1'
    'orchestrator/New-WaggleSynthesisPasteBlock.ps1'
    'orchestrator/Import-WaggleSynthesisResult.ps1'
    'orchestrator/Test-WaggleEpochCycleTrigger.ps1'
    'orchestrator/New-WaggleIterationFromSynthesis.ps1'
    'orchestrator/Run-Phase2BEndToEndDryRun.ps1'
    'orchestrator/Build-Phase2BManifest.ps1'

    # Tests (new)
    'orchestrator/Test-EpochEvidence.ps1'
    'orchestrator/Test-ExternalReviewQueue.ps1'
    'orchestrator/Test-ExternalReviewImport.ps1'
    'orchestrator/Test-SynthesisPasteBlock.ps1'
    'orchestrator/Test-SynthesisResultImport.ps1'
    'orchestrator/Test-EpochCycleTrigger.ps1'
    'orchestrator/Test-IterationFromSynthesis.ps1'

    # Modified
    'orchestrator/Run-WaggleHardeningGates.ps1'
    'orchestrator.config.example.json'
    'docs/design/phase_fix_ledger.json'
    'docs/design/phase_fix_ledger.md'

    # Prompts
    'prompts/external_review/architect.md'
    'prompts/external_review/security.md'
    'prompts/external_review/reliability.md'
    'prompts/external_review/synthesis_gpt.md'
    'prompts/external_review/providers/claude_web.md'
    'prompts/external_review/providers/gemini.md'
    'prompts/external_review/providers/grok.md'
    'prompts/external_review/providers/gpt.md'

    # Run dir docs (added or updated this phase)
    'docs/runs/orchestrator_phase2b_cross_vendor_2026_05_06/baseline.md'
    'docs/runs/orchestrator_phase2b_cross_vendor_2026_05_06/progress.log'
    'docs/runs/orchestrator_phase2b_cross_vendor_2026_05_06/failures.md'
    'docs/runs/orchestrator_phase2b_cross_vendor_2026_05_06/core_findings.md'
    'docs/runs/orchestrator_phase2b_cross_vendor_2026_05_06/cowork_handoff.md'
    'docs/runs/orchestrator_phase2b_cross_vendor_2026_05_06/e2e_dry_run.md'
    'docs/runs/orchestrator_phase2b_cross_vendor_2026_05_06/e2e_dry_run.json'
    'docs/runs/orchestrator_phase2b_cross_vendor_2026_05_06/final_report.md'

    # Design doc
    'docs/design/phase2b_cross_vendor_iteration_cycle.md'
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
    title = 'Phase 2B file manifest'
    phase = 'Phase 2B'
    generated_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    repo_root = $repoRoot
    files_count = $entries.Count
    missing_count = $missing.Count
    entries = $entries.ToArray()
}
$manifestPath = Join-Path $runDir 'manifest.json'
Set-Content -Path $manifestPath -Value (([pscustomobject]$manifest) | ConvertTo-Json -Depth 8) -Encoding UTF8

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
    title = 'Phase 2B manifest self-check'
    phase = 'Phase 2B'
    generated_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    manifest_path = $manifestPath
    files_count = $selfChecks.Count
    failed_count = $selfFail
    overall_ok = ($selfFail -eq 0)
    checks = $selfChecks.ToArray()
}
$selfCheckPath = Join-Path $runDir 'manifest_self_check.json'
Set-Content -Path $selfCheckPath -Value (([pscustomobject]$selfCheck) | ConvertTo-Json -Depth 8) -Encoding UTF8

Write-Host ('Wrote ' + $manifestPath)
Write-Host ('Wrote ' + $selfCheckPath)
Write-Host ('files_count   : ' + $entries.Count)
Write-Host ('missing       : ' + $missing.Count)
Write-Host ('self-check failed: ' + $selfFail)
if ($missing.Count -gt 0 -or $selfFail -gt 0) {
    Write-Host 'OVERALL: FAIL' -ForegroundColor Red
    exit 1
}
Write-Host 'OVERALL: PASS' -ForegroundColor Green
exit 0
