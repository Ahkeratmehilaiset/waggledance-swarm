#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2B P7: read an epoch_evidence.json, generate per-provider
    per-role queue bundles (prompt + attachments + manifest +
    cowork_handoff.md). Browser automation is OUT of scope; this
    writes to disk only. The operator (or a future Phase 2C
    Selenium adapter) sends the bundles to the provider web UIs.
#>
[CmdletBinding()]
param(
    [string]   $ConfigPath = '',
    [string]   $EvidenceJsonPath = '',
    [string[]] $Providers = @('gemini','grok'),
    [string[]] $Roles     = @('architect','reliability'),
    [string]   $OutputBaseDir = '',
    [switch]   $DryRun
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib/Redactor.ps1')
. (Join-Path $PSScriptRoot 'lib/external_review/ProviderProfiles.ps1')
. (Join-Path $PSScriptRoot 'lib/external_review/EvidenceBundler.ps1')

function _Erq-NowUtc { return (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH-mm-ssZ') }
function _Erq-CleanseRel { param([string] $p); return ($p -replace '\\', '/') }

function Export-WaggleExternalReviewQueue {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $ConfigPath,
        [Parameter(Mandatory)] [string] $EvidenceJsonPath,
        [string[]] $Providers = @('gemini','grok'),
        [string[]] $Roles     = @('architect','reliability'),
        [string]   $OutputBaseDir = '',
        [switch]   $DryRun
    )
    if (-not (Test-Path -LiteralPath $ConfigPath)) { throw "config not found: $ConfigPath" }
    if (-not (Test-Path -LiteralPath $EvidenceJsonPath)) { throw "evidence not found: $EvidenceJsonPath" }
    if ($Providers.Count -ne $Roles.Count) {
        throw "Providers and Roles must be the same length (got $($Providers.Count) vs $($Roles.Count))"
    }

    $cfg = Get-Content -Raw -Path $ConfigPath -Encoding UTF8 | ConvertFrom-Json
    $er  = Get-WaggleExternalReviewConfig -Config $cfg
    $models = Get-WaggleModelConfig -Config $cfg
    $projectRoot = $cfg.projectRoot
    $iterationsDir = if ($cfg.PSObject.Properties['iterationsDir'] -and $cfg.iterationsDir) { [string]$cfg.iterationsDir } else { 'iterations' }

    $evidence = Get-Content -Raw -Path $EvidenceJsonPath -Encoding UTF8 | ConvertFrom-Json

    # Refusal: insufficient evidence
    if ($evidence.package_quality_snapshot.review_readiness_status -eq 'INSUFFICIENT_EVIDENCE') {
        throw "refusing to export: evidence package_quality_snapshot.review_readiness_status = INSUFFICIENT_EVIDENCE for epoch $($evidence.epoch_id)"
    }

    # Output base
    $lastIter = [string]$evidence.last_iteration_id
    if (-not $OutputBaseDir) {
        $queueRel = [string]$er.queue_dir_relative
        if (-not $queueRel) { $queueRel = 'external_reviews/queue' }
        $OutputBaseDir = Join-Path (Join-Path (Join-Path $projectRoot $iterationsDir) $lastIter) ($queueRel.TrimEnd('/','\') + '/' + $evidence.epoch_id)
    }
    if (-not (Test-Path -LiteralPath $OutputBaseDir)) {
        New-Item -ItemType Directory -Path $OutputBaseDir -Force | Out-Null
    }

    # Evidence-dir resolution: epoch_evidence.json sits in the
    # evidence dir; attachments come from there.
    $evidenceDir = Split-Path -Parent $EvidenceJsonPath

    $providerBundles = New-Object System.Collections.Generic.List[object]
    for ($i = 0; $i -lt $Providers.Count; $i++) {
        $provider = $Providers[$i]
        $role = $Roles[$i]

        $providerCfg = $er.providers.$provider
        if ($null -eq $providerCfg -or -not [bool]$providerCfg.enabled) {
            $providerBundles.Add([pscustomobject]@{
                provider = $provider; role = $role
                ok = $false; reason = "$provider is not enabled in config"
            }) | Out-Null
            continue
        }

        $bundleDir = Join-Path $OutputBaseDir ($provider + '_' + $role)
        $attDir = Join-Path $bundleDir 'attachments'
        if (-not $DryRun) {
            New-Item -ItemType Directory -Path $bundleDir -Force | Out-Null
            New-Item -ItemType Directory -Path $attDir -Force | Out-Null
        }

        # Render prompt: provider hint + role template + metadata block
        $providerHintPath = Join-Path $projectRoot ('prompts/external_review/providers/' + $provider + '.md')
        $roleTemplatePath = Join-Path $projectRoot ('prompts/external_review/' + $role + '.md')
        if (-not (Test-Path -LiteralPath $roleTemplatePath)) {
            throw "role template not found: $roleTemplatePath"
        }
        $providerHint = if (Test-Path -LiteralPath $providerHintPath) { Get-Content -Raw -Path $providerHintPath -Encoding UTF8 } else { '' }
        $roleTemplate = Get-Content -Raw -Path $roleTemplatePath -Encoding UTF8

        $expectedModel = [string]$providerCfg.expected_model_in_ui
        $metadataBlock = @"

---

# REVIEW METADATA (epoch $($evidence.epoch_id))

- target_iteration_id: ``$($evidence.last_iteration_id)``
- epoch_id: ``$($evidence.epoch_id)``
- source_evidence_sha256: ``$($evidence.evidence_sha256)``
- expected_model_in_ui: ``$expectedModel``
- provider: ``$provider``
- role: ``$role``

You MUST echo ``source_evidence_sha256`` value verbatim in your
output JSON's ``source_evidence_sha256`` field. The orchestrator's
importer recomputes the SHA from the attached files and refuses
the import on mismatch.

---

"@

        $promptText = $providerHint + "`n`n" + $roleTemplate + $metadataBlock

        # Defense-in-depth: redact the prompt text before writing.
        $rPrompt = Invoke-WaggleRedaction -Text $promptText
        $promptOnDisk = Join-Path $bundleDir 'prompt.md'
        if (-not $DryRun) {
            Set-Content -Path $promptOnDisk -Value $rPrompt.text -Encoding UTF8
        }

        # Attachment plan
        $cap = [int]$er.max_attachments_per_provider
        $plan = Get-WaggleAttachmentPlanForProvider -EvidenceDir $evidenceDir -MaxAttachments $cap
        if (-not $plan.ok) {
            if ([bool]$er.fail_on_attachment_overflow) {
                throw "attachment_overflow_$provider : $($plan.errors -join '; ')"
            } else {
                $providerBundles.Add([pscustomobject]@{
                    provider = $provider; role = $role
                    ok = $false; reason = ($plan.errors -join '; ')
                }) | Out-Null
                continue
            }
        }

        # Copy attachments (already redacted by P6; double-check none
        # contain a token-shaped secret post-copy).
        $copied = New-Object System.Collections.Generic.List[string]
        foreach ($att in $plan.attachments) {
            $src = Join-Path $evidenceDir $att
            if (-not (Test-Path -LiteralPath $src)) { continue }
            $dst = Join-Path $attDir $att
            if (-not $DryRun) {
                $body = Get-Content -Raw -Path $src -Encoding UTF8
                # Choose redactor by extension: source-y excerpts via
                # syntax-preserving redactor; logs/text via full.
                if ($att -match '_supplement\.md$|_internal_review\.md$') {
                    $rr = Invoke-WaggleSourceSupplementRedaction -Text $body
                } else {
                    $rr = Invoke-WaggleRedaction -Text $body
                }
                Set-Content -Path $dst -Value $rr.text -Encoding UTF8
            }
            [void]$copied.Add($att)
        }

        # Expected response path the operator should save to
        $importedRel = [string]$er.imported_dir_relative
        if (-not $importedRel) { $importedRel = 'external_reviews/imported' }
        $importedRel = $importedRel.TrimEnd('/','\')
        $respDir = Join-Path (Join-Path (Join-Path $projectRoot $iterationsDir) $lastIter) $importedRel
        if (-not $DryRun -and -not (Test-Path -LiteralPath $respDir)) {
            New-Item -ItemType Directory -Path $respDir -Force | Out-Null
        }
        $expectedResponseRel = ($importedRel + '/response_' + (_Erq-NowUtc) + '_' + $provider + '_' + $role + '.md')
        $expectedResponseAbs = Join-Path (Join-Path (Join-Path $projectRoot $iterationsDir) $lastIter) $expectedResponseRel
        if (-not $DryRun) {
            Set-Content -Path (Join-Path $bundleDir 'expected_response_path.txt') -Value $expectedResponseAbs -Encoding UTF8
        }

        # Bundle metadata
        $bundleMeta = [ordered]@{
            provider                = $provider
            role                    = $role
            expected_model_in_ui    = $expectedModel
            timeout_sec             = [int]$providerCfg.timeout_sec
            epoch_id                = [string]$evidence.epoch_id
            target_iteration_id     = [string]$evidence.last_iteration_id
            source_evidence_sha256  = [string]$evidence.evidence_sha256
            prompt_path             = $promptOnDisk
            attachments_dir         = $attDir
            attachments             = $copied.ToArray()
            attachments_consolidated = [bool]$plan.consolidated
            expected_response_path  = $expectedResponseAbs
        }
        if (-not $DryRun) {
            Set-Content -Path (Join-Path $bundleDir 'metadata.json') -Value (([pscustomobject]$bundleMeta) | ConvertTo-Json -Depth 16) -Encoding UTF8
        }

        $providerBundles.Add([pscustomobject]@{
            provider = $provider
            role = $role
            ok = $true
            bundle_dir = $bundleDir
            metadata = ([pscustomobject]$bundleMeta)
        }) | Out-Null
    }

    # Top-level queue manifest
    $queueManifest = [ordered]@{
        epoch_id              = [string]$evidence.epoch_id
        target_iteration_id   = [string]$evidence.last_iteration_id
        source_evidence_sha256 = [string]$evidence.evidence_sha256
        generated_at_utc      = (Get-Date).ToUniversalTime().ToString('o')
        bundles               = $providerBundles.ToArray()
    }
    $queueManifestPath = Join-Path $OutputBaseDir 'queue_manifest.json'
    if (-not $DryRun) {
        Set-Content -Path $queueManifestPath -Value (([pscustomobject]$queueManifest) | ConvertTo-Json -Depth 16) -Encoding UTF8
    }

    # cowork_handoff.md operator brief
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.AppendLine('# Cowork operator handoff for epoch ' + $evidence.epoch_id)
    [void]$sb.AppendLine('')
    [void]$sb.AppendLine('Per-bundle workflow (parallel reviewers OK):')
    [void]$sb.AppendLine('')
    foreach ($b in $providerBundles) {
        if (-not $b.ok) { continue }
        [void]$sb.AppendLine("## $($b.provider) / $($b.role)")
        [void]$sb.AppendLine('')
        [void]$sb.AppendLine("- bundle_dir: ``$($b.bundle_dir)``")
        [void]$sb.AppendLine("- expected_model_in_ui: **$($b.metadata.expected_model_in_ui)** -- if the UI shows a different model, switch and verify before sending")
        [void]$sb.AppendLine('- attach all files from `attachments/` via the chat UI''s attach button')
        [void]$sb.AppendLine("- paste `prompt.md` content into the message body and submit")
        [void]$sb.AppendLine("- save the full response to ``$($b.metadata.expected_response_path)``")
        [void]$sb.AppendLine('- then run:')
        [void]$sb.AppendLine('')
        [void]$sb.AppendLine('  ```powershell')
        [void]$sb.AppendLine("  powershell -NoProfile -ExecutionPolicy Bypass -File .\orchestrator\Import-WaggleExternalReviewResponse.ps1 ``")
        [void]$sb.AppendLine("    -ConfigPath .\orchestrator.config.json ``")
        [void]$sb.AppendLine("    -EpochId '$($evidence.epoch_id)' ``")
        [void]$sb.AppendLine("    -Provider '$($b.provider)' ``")
        [void]$sb.AppendLine("    -Role '$($b.role)' ``")
        [void]$sb.AppendLine("    -ResponseFile '$($b.metadata.expected_response_path)' ``")
        [void]$sb.AppendLine("    -IterationId '$($evidence.last_iteration_id)'")
        [void]$sb.AppendLine('  ```')
        [void]$sb.AppendLine('')
    }
    [void]$sb.AppendLine('Once all three reviewer responses are imported successfully, run:')
    [void]$sb.AppendLine('')
    [void]$sb.AppendLine('```powershell')
    [void]$sb.AppendLine("powershell -NoProfile -ExecutionPolicy Bypass -File .\orchestrator\New-WaggleSynthesisPasteBlock.ps1 ``")
    [void]$sb.AppendLine("  -ConfigPath .\orchestrator.config.json ``")
    [void]$sb.AppendLine("  -EpochId '$($evidence.epoch_id)' ``")
    [void]$sb.AppendLine("  -IterationId '$($evidence.last_iteration_id)'")
    [void]$sb.AppendLine('```')
    [void]$sb.AppendLine('')
    [void]$sb.AppendLine('## Reminder')
    [void]$sb.AppendLine('')
    [void]$sb.AppendLine('Pasting these prompts into external LLM web UIs may be subject to those services'' terms of service. The orchestrator does NOT automate the paste. Keep request volume reasonable. Do not paste secrets manually.')

    $cowork = $sb.ToString()
    $coworkPath = Join-Path $OutputBaseDir 'cowork_handoff.md'
    if (-not $DryRun) {
        Set-Content -Path $coworkPath -Value $cowork -Encoding UTF8
    }

    return [pscustomobject]@{
        ok                  = $true
        epoch_id            = [string]$evidence.epoch_id
        output_base_dir     = $OutputBaseDir
        queue_manifest_path = $queueManifestPath
        cowork_handoff_path = $coworkPath
        bundles             = $providerBundles.ToArray()
    }
}

# CLI wrapper
if ($MyInvocation.InvocationName -ne '.' -and $ConfigPath -and $EvidenceJsonPath) {
    $r = Export-WaggleExternalReviewQueue -ConfigPath $ConfigPath -EvidenceJsonPath $EvidenceJsonPath `
            -Providers $Providers -Roles $Roles -OutputBaseDir $OutputBaseDir -DryRun:$DryRun
    if ($r.ok) {
        Write-Host ('Queue exported: ' + $r.epoch_id)
        Write-Host ('  output_base_dir : ' + $r.output_base_dir)
        Write-Host ('  queue_manifest  : ' + $r.queue_manifest_path)
        Write-Host ('  cowork_handoff  : ' + $r.cowork_handoff_path)
        exit 0
    } else {
        exit 1
    }
}
