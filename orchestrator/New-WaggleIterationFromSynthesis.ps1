#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2B P12: SHA-bound launcher that creates a new local
    iteration from a successfully-imported synthesis result. Reads
    the synthesis metadata + next_claude_code_prompt.md, verifies
    the recomputed-from-disk evidence_sha256 still matches the
    synthesis JSON's source_evidence_sha256, and writes a new
    iteration folder with the next-prompt as its input.

    Refuses on:
      - synthesis decision != 'continue' (halt / pause / requires_attention)
      - missing or empty next_claude_code_prompt.md
      - SHA mismatch (evidence regenerated since synthesis was imported)
      - new_id collision with an existing iteration folder

    -DryRun does all checks but writes nothing.
#>
[CmdletBinding()]
param(
    [string] $ConfigPath = '',
    [string] $EpochId = '',
    [string] $IterationId = '',
    [string] $SynthesisImportId = '',
    [string] $NewIterationId = '',
    [switch] $DryRun
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib/external_review/ProviderProfiles.ps1')
. (Join-Path $PSScriptRoot 'lib/external_review/EvidenceBundler.ps1')

function _Nis-NowUtc { return (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd_HH-mm-ss') }

function New-WaggleIterationFromSynthesis {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $ConfigPath,
        [Parameter(Mandatory)] [string] $EpochId,
        [Parameter(Mandatory)] [string] $IterationId,
        [Parameter(Mandatory)] [string] $SynthesisImportId,
        [string] $NewIterationId = '',
        [switch] $DryRun
    )

    if (-not (Test-Path -LiteralPath $ConfigPath)) { throw "config not found: $ConfigPath" }
    $cfg = Get-Content -Raw -Path $ConfigPath -Encoding UTF8 | ConvertFrom-Json
    $er = Get-WaggleExternalReviewConfig -Config $cfg
    $projectRoot = $cfg.projectRoot
    $iterationsDir = if ($cfg.PSObject.Properties['iterationsDir'] -and $cfg.iterationsDir) { [string]$cfg.iterationsDir } else { 'iterations' }
    $iterRoot = Join-Path $projectRoot $iterationsDir
    $iterFolder = Join-Path $iterRoot $IterationId
    $synthRel = [string]$er.synthesis_dir_relative
    if (-not $synthRel) { $synthRel = 'external_reviews/synthesis' }
    $synthDir = Join-Path $iterFolder ($synthRel.TrimEnd('/','\') + '/' + $EpochId)
    $resultJson = Join-Path $synthDir ('result_' + $SynthesisImportId + '.json')
    $resultMeta = Join-Path $synthDir ('result_' + $SynthesisImportId + '.metadata.json')
    $nextPromptPath = Join-Path $synthDir 'next_claude_code_prompt.md'

    if (-not (Test-Path -LiteralPath $resultJson)) { throw "synthesis result JSON missing: $resultJson" }
    if (-not (Test-Path -LiteralPath $resultMeta)) { throw "synthesis metadata missing: $resultMeta" }

    $obj = Get-Content -Raw -Path $resultJson -Encoding UTF8 | ConvertFrom-Json
    $meta = Get-Content -Raw -Path $resultMeta -Encoding UTF8 | ConvertFrom-Json

    if ([string]$obj.decision -ne 'continue') {
        throw ("refusing: synthesis decision != 'continue' (got '" + [string]$obj.decision + "')")
    }
    if (-not (Test-Path -LiteralPath $nextPromptPath)) {
        throw "next_claude_code_prompt.md missing: $nextPromptPath"
    }
    $promptText = Get-Content -Raw -Path $nextPromptPath -Encoding UTF8
    if ([string]::IsNullOrWhiteSpace($promptText)) {
        throw "next_claude_code_prompt.md is empty"
    }

    # Recompute evidence_sha256 from disk and verify it still matches.
    $evidenceDir = Join-Path $iterFolder ('external_reviews/epoch_' + $EpochId + '/evidence')
    if (-not (Test-Path -LiteralPath $evidenceDir)) { throw "evidence dir missing: $evidenceDir" }
    $epochJsonPath = Join-Path $evidenceDir 'epoch_evidence.json'
    if (-not (Test-Path -LiteralPath $epochJsonPath)) { throw "epoch_evidence.json missing: $epochJsonPath" }
    $ev = Get-Content -Raw -Path $epochJsonPath -Encoding UTF8 | ConvertFrom-Json
    $rels = New-Object System.Collections.Generic.List[string]
    [void]$rels.Add([string]$ev.cumulative_diff_path)
    [void]$rels.Add([string]$ev.cumulative_raportti_path)
    [void]$rels.Add([string]$ev.cumulative_supplement_path)
    [void]$rels.Add('regression_state.json')
    foreach ($p in @($ev.logs_combined_per_iteration_paths))   { [void]$rels.Add([string]$p) }
    foreach ($p in @($ev.internal_reviews_per_iteration_paths)) { [void]$rels.Add([string]$p) }
    if ($ev.PSObject.Properties['previous_epoch_synthesis_path'] -and $ev.previous_epoch_synthesis_path) {
        [void]$rels.Add([string]$ev.previous_epoch_synthesis_path)
    }
    $recomputed = Get-WaggleCanonicalEvidenceSha -RootPath $evidenceDir -RelativePaths $rels.ToArray()
    $synthesisSha = [string]$obj.source_evidence_sha256
    if ($recomputed -ne $synthesisSha) {
        throw ("refusing: source_evidence_sha256 mismatch (synthesis='$synthesisSha' recomputed='$recomputed')")
    }

    if (-not $NewIterationId) {
        $NewIterationId = (_Nis-NowUtc) + '_from_' + $SynthesisImportId.Substring(0, [Math]::Min(8, $SynthesisImportId.Length))
    }
    $newIterFolder = Join-Path $iterRoot $NewIterationId
    if (Test-Path -LiteralPath $newIterFolder) {
        throw "refusing: new iteration folder already exists: $newIterFolder"
    }

    if ($DryRun) {
        return [pscustomobject]@{
            ok = $true
            dry_run = $true
            new_iteration_id = $NewIterationId
            new_iteration_folder = $newIterFolder
            parent_iteration_id = $IterationId
            parent_epoch_id = $EpochId
            synthesis_import_id = $SynthesisImportId
            evidence_sha256 = $recomputed
            sha_verified = $true
            next_prompt_bytes = $promptText.Length
        }
    }

    New-Item -ItemType Directory -Path $newIterFolder -Force | Out-Null
    $promptOut = Join-Path $newIterFolder 'iteration_prompt.md'
    Set-Content -Path $promptOut -Value $promptText -Encoding UTF8
    $state = [ordered]@{
        iteration_id = $NewIterationId
        status = 'PENDING'
        created_at_utc = (Get-Date).ToUniversalTime().ToString('o')
        parent = [ordered]@{
            iteration_id = $IterationId
            epoch_id = $EpochId
            synthesis_import_id = $SynthesisImportId
            evidence_sha256 = $recomputed
        }
        prompt_source = 'synthesis_next_claude_code_prompt'
        prompt_path = $promptOut
    }
    Set-Content -Path (Join-Path $newIterFolder 'state.json') -Value (([pscustomobject]$state) | ConvertTo-Json -Depth 16) -Encoding UTF8

    return [pscustomobject]@{
        ok = $true
        dry_run = $false
        new_iteration_id = $NewIterationId
        new_iteration_folder = $newIterFolder
        prompt_path = $promptOut
        parent_iteration_id = $IterationId
        parent_epoch_id = $EpochId
        synthesis_import_id = $SynthesisImportId
        evidence_sha256 = $recomputed
        sha_verified = $true
    }
}

# CLI wrapper
if ($MyInvocation.InvocationName -ne '.' -and $ConfigPath -and $EpochId -and $IterationId -and $SynthesisImportId) {
    $r = New-WaggleIterationFromSynthesis -ConfigPath $ConfigPath -EpochId $EpochId `
            -IterationId $IterationId -SynthesisImportId $SynthesisImportId `
            -NewIterationId $NewIterationId -DryRun:$DryRun
    if ($r.ok) {
        Write-Host ('New iteration prepared:')
        Write-Host ('  new_iteration_id     : ' + $r.new_iteration_id)
        Write-Host ('  new_iteration_folder : ' + $r.new_iteration_folder)
        if ($r.dry_run) { Write-Host '  (dry-run, nothing written)' }
        exit 0
    } else {
        Write-Host 'New iteration failed' -ForegroundColor Red
        exit 1
    }
}
