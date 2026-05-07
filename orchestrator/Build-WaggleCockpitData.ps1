#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2B-Revision (ARCH-011): assemble state/cockpit_data.json
    from the current epoch's queue manifest, imported reviewer
    responses (if any), proposal matrix summary, regression-ledger
    excerpt, and synthesis bundle status.

    The cockpit HTML (review_cockpit.html) polls this file every 5
    seconds. When this builder is re-run after an import (or the
    proposal-matrix builder), the cockpit refreshes automatically.

.PARAMETER ConfigPath
    Orchestrator config (live or example).
.PARAMETER EpochId
    Stable identifier for the epoch.
.PARAMETER IterationId
    Carrier iteration ID (last iteration in the epoch).
.PARAMETER OutputPath
    Override the default state/cockpit_data.json path.
#>
[CmdletBinding()]
param(
    [string] $ConfigPath = '',
    [string] $EpochId = '',
    [string] $IterationId = '',
    [string] $OutputPath = ''
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib/external_review/ProviderProfiles.ps1')

function Build-WaggleCockpitData {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $ConfigPath,
        [Parameter(Mandatory)] [string] $EpochId,
        [Parameter(Mandatory)] [string] $IterationId,
        [string] $OutputPath = ''
    )
    if (-not (Test-Path -LiteralPath $ConfigPath)) { throw "config not found: $ConfigPath" }
    $cfg = Get-Content -Raw -Path $ConfigPath -Encoding UTF8 | ConvertFrom-Json
    $er  = Get-WaggleExternalReviewConfig -Config $cfg

    $projectRoot = $cfg.projectRoot
    $iterationsDir = if ($cfg.PSObject.Properties['iterationsDir'] -and $cfg.iterationsDir) { [string]$cfg.iterationsDir } else { 'iterations' }
    $iterFolder = Join-Path (Join-Path $projectRoot $iterationsDir) $IterationId

    # ---- Locate evidence + queue + imports + synthesis dirs
    $evidenceDir = Join-Path $iterFolder ('external_reviews/epoch_' + $EpochId + '/evidence')
    $epochJson   = Join-Path $evidenceDir 'epoch_evidence.json'
    $evidenceSha = ('e' * 64)
    if (Test-Path -LiteralPath $epochJson) {
        try {
            $ev = Get-Content -Raw -Path $epochJson -Encoding UTF8 | ConvertFrom-Json
            if ($ev.PSObject.Properties['evidence_sha256']) { $evidenceSha = [string]$ev.evidence_sha256 }
        } catch {}
    }

    $queueRel = [string]$er.queue_dir_relative
    if (-not $queueRel) { $queueRel = 'external_reviews/queue' }
    $queueDir = Join-Path $iterFolder ($queueRel.TrimEnd('/','\') + '/' + $EpochId)

    $importedRel = [string]$er.imported_dir_relative
    if (-not $importedRel) { $importedRel = 'external_reviews/imported' }
    $importedDir = Join-Path $iterFolder ($importedRel.TrimEnd('/','\'))

    $synthRel = [string]$er.synthesis_dir_relative
    if (-not $synthRel) { $synthRel = 'external_reviews/synthesis' }
    $synthDir = Join-Path $iterFolder ($synthRel.TrimEnd('/','\') + '/' + $EpochId)

    # ---- Read each per-(provider, role) bundle's metadata
    $bundles = New-Object System.Collections.Generic.List[object]
    if (Test-Path -LiteralPath $queueDir) {
        foreach ($d in (Get-ChildItem -LiteralPath $queueDir -Directory -ErrorAction SilentlyContinue)) {
            # Bundle dir name is "<provider>_<role>"
            $name = $d.Name
            $parts = $name -split '_', 2
            if ($parts.Count -lt 2) { continue }
            $prov = $parts[0]
            $role = $parts[1]
            $metaP = Join-Path $d.FullName 'metadata.json'
            $promptP = Join-Path $d.FullName 'prompt.md'
            $expRespP = Join-Path $d.FullName 'expected_response_path.txt'
            $attDir = Join-Path $d.FullName 'attachments'
            $meta = $null
            if (Test-Path -LiteralPath $metaP) {
                try { $meta = Get-Content -Raw -Path $metaP -Encoding UTF8 | ConvertFrom-Json } catch {}
            }
            $promptText = ''
            if (Test-Path -LiteralPath $promptP) {
                try { $promptText = Get-Content -Raw -Path $promptP -Encoding UTF8 } catch {}
            }
            $respPath = ''
            if (Test-Path -LiteralPath $expRespP) {
                try { $respPath = (Get-Content -Raw -Path $expRespP -Encoding UTF8).Trim() } catch {}
            }
            $attCount = 0
            if (Test-Path -LiteralPath $attDir) {
                $attCount = @(Get-ChildItem -LiteralPath $attDir -File -ErrorAction SilentlyContinue).Count
            }
            $expectedModel = ''
            if ($null -ne $meta -and $meta.PSObject.Properties['provider_profile']) {
                $pp = $meta.provider_profile
                if ($null -ne $pp -and $pp.PSObject.Properties['expected_model_in_ui']) {
                    $expectedModel = [string]$pp.expected_model_in_ui
                }
            }
            if (-not $expectedModel) {
                $providerCfg = $er.providers.$prov
                if ($null -ne $providerCfg -and $providerCfg.PSObject.Properties['expected_model_in_ui']) {
                    $expectedModel = [string]$providerCfg.expected_model_in_ui
                }
            }

            $providerEnabled = $true
            $providerCfg = $er.providers.$prov
            if ($null -ne $providerCfg -and $providerCfg.PSObject.Properties['enabled']) {
                $providerEnabled = [bool]$providerCfg.enabled
            }

            # Find any matching valid imported response.
            $importStatus = 'pending'
            $importId = $null
            $importedAt = $null
            if (Test-Path -LiteralPath $importedDir) {
                foreach ($mf in @(Get-ChildItem -LiteralPath $importedDir -Filter '*.metadata.json' -File -ErrorAction SilentlyContinue)) {
                    if ($mf.Name -like '*.invalid.metadata.json') { continue }
                    try {
                        $im = Get-Content -Raw -Path $mf.FullName -Encoding UTF8 | ConvertFrom-Json
                    } catch { continue }
                    if (-not [bool]$im.ok) { continue }
                    if (([string]$im.provider) -ne $prov -or ([string]$im.role) -ne $role) { continue }
                    if ($im.PSObject.Properties['epoch_id'] -and [string]$im.epoch_id -ne $EpochId) { continue }
                    if (-not $importId -or [string]$im.import_id -gt $importId) {
                        $importId = [string]$im.import_id
                        if ($im.PSObject.Properties['applied_at_utc']) { $importedAt = [string]$im.applied_at_utc }
                        $importStatus = 'imported'
                    }
                }
            }

            $status = if (-not $providerEnabled) { 'disabled' } else { $importStatus }

            # Convenience PowerShell command for the operator to copy.
            # Single-line form is robust (no PS line-continuation
            # backtick parsing risk on Windows clipboard paste).
            $importCmd = ('powershell -File "' + $projectRoot + '\orchestrator\Import-WaggleExternalReviewResponse.ps1"' +
                          ' -ConfigPath "' + $ConfigPath + '"' +
                          ' -EpochId "' + $EpochId + '"' +
                          ' -Provider ' + $prov +
                          ' -Role ' + $role +
                          ' -ResponseFile "' + $respPath + '"' +
                          ' -IterationId "' + $IterationId + '"')

            $bundles.Add([pscustomobject]@{
                provider = $prov
                role = $role
                status = $status
                expected_model_in_ui = $expectedModel
                attachment_count = [int]$attCount
                attachments_dir = $attDir
                expected_response_path = $respPath
                prompt_text = $promptText
                import_command = $importCmd
                import_id = $importId
                imported_at_utc = $importedAt
            }) | Out-Null
        }
    }

    # ---- Synthesis status
    $synthStatus = 'not_started'
    if (Test-Path -LiteralPath $synthDir) {
        $resJsons = @(Get-ChildItem -LiteralPath $synthDir -Filter 'result_*.json' -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -notlike '*.metadata.json' -and $_.Name -notlike '*.invalid.*' })
        $halt = Test-Path -LiteralPath (Join-Path $synthDir 'HALT.md')
        if ($halt) { $synthStatus = 'halt' }
        elseif ($resJsons.Count -gt 0) { $synthStatus = 'imported' }
        elseif (Test-Path -LiteralPath (Join-Path $synthDir 'paste_block.md')) { $synthStatus = 'paste_block_ready' }
    }

    # ---- Regression ledger summary (open by severity)
    $regSummary = $null
    $rlPath = Join-Path $projectRoot 'state/regression_ledger.json'
    if (Test-Path -LiteralPath $rlPath) {
        try {
            $rl = Get-Content -Raw -Path $rlPath -Encoding UTF8 | ConvertFrom-Json
            $openExcl = @('verified','fixed','false_positive')
            $crit = @($rl.regressions | Where-Object { $_.severity -eq 'critical' -and $openExcl -notcontains [string]$_.status }).Count
            $high = @($rl.regressions | Where-Object { $_.severity -eq 'high'     -and $openExcl -notcontains [string]$_.status }).Count
            $med  = @($rl.regressions | Where-Object { $_.severity -eq 'medium'   -and $openExcl -notcontains [string]$_.status }).Count
            $regSummary = [pscustomobject]@{ open_critical = $crit; open_high = $high; open_medium = $med }
        } catch {}
    }

    # ---- Proposal matrix summary
    $proposalSummary = $null
    $pmJson = Join-Path $synthDir 'proposal_matrix.json'
    if (Test-Path -LiteralPath $pmJson) {
        try {
            $pm = Get-Content -Raw -Path $pmJson -Encoding UTF8 | ConvertFrom-Json
            $proposalSummary = [pscustomobject]@{
                total                 = [int]$pm.sources_summary.total_proposals
                claude_internal_count = [int]$pm.sources_summary.claude_internal_count
                codex_count           = [int]$pm.sources_summary.codex_count
                external_count        = [int]$pm.sources_summary.external_count
            }
        } catch {}
    }

    $data = [ordered]@{
        format_version    = '1.0'
        generated_at_utc  = (Get-Date).ToUniversalTime().ToString('o')
        epoch_id          = $EpochId
        iteration_id      = $IterationId
        evidence_sha256   = $evidenceSha
        bundles           = $bundles.ToArray()
        synthesis_status  = $synthStatus
        regression_ledger = $regSummary
        proposal_matrix   = $proposalSummary
    }

    if (-not $OutputPath) {
        $OutputPath = Join-Path $projectRoot 'state/cockpit_data.json'
    }
    $outDir = Split-Path -Parent $OutputPath
    if ($outDir -and -not (Test-Path -LiteralPath $outDir)) {
        New-Item -ItemType Directory -Path $outDir -Force | Out-Null
    }
    Set-Content -Path $OutputPath -Value (([pscustomobject]$data) | ConvertTo-Json -Depth 16) -Encoding UTF8

    return [pscustomobject]@{
        ok = $true
        path = $OutputPath
        bundle_count = $bundles.Count
        synthesis_status = $synthStatus
    }
}

# CLI wrapper
if ($MyInvocation.InvocationName -ne '.' -and $ConfigPath -and $EpochId -and $IterationId) {
    $r = Build-WaggleCockpitData -ConfigPath $ConfigPath -EpochId $EpochId -IterationId $IterationId -OutputPath $OutputPath
    if ($r.ok) {
        Write-Host ('Cockpit data written: ' + $r.path)
        Write-Host ('  bundles : ' + $r.bundle_count)
        Write-Host ('  synth   : ' + $r.synthesis_status)
        exit 0
    } else {
        exit 1
    }
}
