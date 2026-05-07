#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2B P8: import a manually-saved external reviewer response,
    apply Phase 2A-1 redactor, validate against
    schemas/external_review.schema.json, verify SHA contract,
    and store the validated import.
#>
[CmdletBinding()]
param(
    [string] $ConfigPath = '',
    [string] $EpochId = '',
    [string] $Provider = '',
    [string] $Role = '',
    [string] $ResponseFile = '',
    [string] $IterationId = ''
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib/Redactor.ps1')
. (Join-Path $PSScriptRoot 'lib/external_review/ProviderProfiles.ps1')
. (Join-Path $PSScriptRoot 'lib/external_review/EvidenceBundler.ps1')
. (Join-Path $PSScriptRoot 'lib/external_review/ExternalReviewSchema.ps1')

function _Erri-NowUtc { return (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH-mm-ssZ') }
function _Erri-ShortId { return [guid]::NewGuid().ToString('N').Substring(0, 8) }

function Import-WaggleExternalReviewResponse {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $ConfigPath,
        [Parameter(Mandatory)] [string] $EpochId,
        [Parameter(Mandatory)] [string] $Provider,
        [Parameter(Mandatory)] [string] $Role,
        [Parameter(Mandatory)] [string] $ResponseFile,
        [Parameter(Mandatory)] [string] $IterationId
    )

    if (-not (Test-Path -LiteralPath $ConfigPath)) { throw "config not found: $ConfigPath" }
    if (-not (Test-Path -LiteralPath $ResponseFile)) { throw "response file not found: $ResponseFile" }

    $cfg = Get-Content -Raw -Path $ConfigPath -Encoding UTF8 | ConvertFrom-Json
    $er = Get-WaggleExternalReviewConfig -Config $cfg
    $projectRoot = $cfg.projectRoot
    $iterationsDir = if ($cfg.PSObject.Properties['iterationsDir'] -and $cfg.iterationsDir) { [string]$cfg.iterationsDir } else { 'iterations' }
    $iterFolder = Join-Path (Join-Path $projectRoot $iterationsDir) $IterationId
    $importedRel = [string]$er.imported_dir_relative
    if (-not $importedRel) { $importedRel = 'external_reviews/imported' }
    $importDir = Join-Path $iterFolder ($importedRel.TrimEnd('/','\'))
    if (-not (Test-Path -LiteralPath $importDir)) {
        New-Item -ItemType Directory -Path $importDir -Force | Out-Null
    }

    $importId = (_Erri-NowUtc) + '_' + $Provider + '_' + $Role + '_' + (_Erri-ShortId)
    $okBase = Join-Path $importDir $importId
    $invalidBase = $okBase + '.invalid'

    $rawText = Get-Content -Raw -Path $ResponseFile -Encoding UTF8
    if ($null -eq $rawText) { $rawText = '' }

    # 1. Apply Phase 2A-1 redactor up-front (untrusted-data policy).
    $red = Invoke-WaggleRedaction -Text $rawText
    $redactedText = $red.text
    $redactionReportPath = $okBase + '.redaction_report.json'
    $redactionReport = [pscustomobject]@{
        import_id = $importId
        provider = $Provider; role = $Role
        epoch_id = $EpochId; target_iteration_id = $IterationId
        report = $red.report
        applied_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    }

    # 2. Find reviewer-self-id block + external-review-json block + marker
    $selfBlk = Find-ReviewerSelfIdBlock -Text $redactedText
    $jsonBlk = Find-ExternalReviewBlock  -Text $redactedText
    $markerOk = Test-ExternalReviewCompletionMarker -Text $redactedText

    function _writeInvalid {
        param([string] $reason, $self, $json, $extras = @())
        $invalidMd = $invalidBase + '.md'
        $invalidMeta = $invalidBase + '.metadata.json'
        Set-Content -Path $invalidMd -Value $redactedText -Encoding UTF8
        $meta = [ordered]@{
            import_id = $importId
            ok = $false
            reason = $reason
            extras = $extras
            provider = $Provider; role = $Role
            epoch_id = $EpochId; target_iteration_id = $IterationId
            response_file = $ResponseFile
            redaction_report = $redactionReport
            self_id_text = if ($null -ne $self -and $self.ok) { $self.text } else { '' }
            json_text    = if ($null -ne $json -and $json.ok) { $json.text } else { '' }
            applied_at_utc = (Get-Date).ToUniversalTime().ToString('o')
        }
        Set-Content -Path $invalidMeta -Value (([pscustomobject]$meta) | ConvertTo-Json -Depth 16) -Encoding UTF8
        Set-Content -Path $redactionReportPath -Value (([pscustomobject]$redactionReport) | ConvertTo-Json -Depth 16) -Encoding UTF8
    }

    if (-not $selfBlk.ok) {
        _writeInvalid -reason ('reviewer_self_id_block_missing: ' + ($selfBlk.errors -join '; ')) -self $selfBlk -json $jsonBlk
        return [pscustomobject]@{ ok = $false; import_id = $importId; reason = 'reviewer_self_id_block_missing'; errors = $selfBlk.errors }
    }
    if (-not $jsonBlk.ok) {
        _writeInvalid -reason ('external_review_block_missing_or_multiple: ' + ($jsonBlk.errors -join '; ')) -self $selfBlk -json $jsonBlk
        return [pscustomobject]@{ ok = $false; import_id = $importId; reason = 'external_review_block_missing_or_multiple'; errors = $jsonBlk.errors }
    }
    if (-not $markerOk) {
        _writeInvalid -reason 'completion_marker_missing' -self $selfBlk -json $jsonBlk
        return [pscustomobject]@{ ok = $false; import_id = $importId; reason = 'completion_marker_missing'; errors = @('EXTERNAL-REVIEW-COMPLETE marker not found') }
    }

    # 3. Parse + validate JSON
    $parsed = ConvertFrom-ExternalReviewJsonText -Text $jsonBlk.text
    if (-not $parsed.ok) {
        _writeInvalid -reason 'json_parse_failed' -self $selfBlk -json $jsonBlk -extras $parsed.errors
        return [pscustomobject]@{ ok = $false; import_id = $importId; reason = 'json_parse_failed'; errors = $parsed.errors }
    }
    $obj = $parsed.obj
    $schema = Test-ExternalReviewObject -Object $obj
    if (-not $schema.ok) {
        _writeInvalid -reason 'json_schema_invalid' -self $selfBlk -json $jsonBlk -extras $schema.errors
        return [pscustomobject]@{ ok = $false; import_id = $importId; reason = 'json_schema_invalid'; errors = $schema.errors }
    }

    # 4. Cross-check identity fields
    $errors = New-Object System.Collections.Generic.List[string]
    if ([string]$obj.provider -ne $Provider) {
        $errors.Add("provider mismatch: response='$([string]$obj.provider)' expected='$Provider'") | Out-Null
    }
    if ([string]$obj.role -ne $Role) {
        $errors.Add("role mismatch: response='$([string]$obj.role)' expected='$Role'") | Out-Null
    }
    if ([string]$obj.target_iteration_id -ne $IterationId) {
        $errors.Add("target_iteration_id mismatch: response='$([string]$obj.target_iteration_id)' expected='$IterationId'") | Out-Null
    }
    if ([string]$obj.epoch_id -ne $EpochId) {
        $errors.Add("epoch_id mismatch: response='$([string]$obj.epoch_id)' expected='$EpochId'") | Out-Null
    }
    if (-not [bool]$obj.completed) {
        $errors.Add('completed != true') | Out-Null
    }
    if ($errors.Count -gt 0) {
        _writeInvalid -reason 'identity_fields_mismatch' -self $selfBlk -json $jsonBlk -extras $errors.ToArray()
        return [pscustomobject]@{ ok = $false; import_id = $importId; reason = 'identity_fields_mismatch'; errors = $errors.ToArray() }
    }

    # 5. SHA verification: recompute evidence_sha256 from epoch's
    # evidence dir and compare with response.
    $evidenceDir = Join-Path $iterFolder ('external_reviews/epoch_' + $EpochId + '/evidence')
    $evJsonPath = Join-Path $evidenceDir 'epoch_evidence.json'
    $epochSha = ''
    if (Test-Path -LiteralPath $evJsonPath) {
        try {
            $ej = Get-Content -Raw -Path $evJsonPath -Encoding UTF8 | ConvertFrom-Json
            $epochSha = [string]$ej.evidence_sha256
        } catch {}
    }
    $responseSha = [string]$obj.source_evidence_sha256
    $shaOk = ($epochSha -and ($responseSha -eq $epochSha))
    if (-not $shaOk) {
        _writeInvalid -reason 'source_evidence_sha256_mismatch' -self $selfBlk -json $jsonBlk -extras @("response_sha=$responseSha", "epoch_sha=$epochSha")
        return [pscustomobject]@{ ok = $false; import_id = $importId; reason = 'source_evidence_sha256_mismatch'; errors = @("response_sha=$responseSha epoch_sha=$epochSha") }
    }

    # 6. Write valid records
    $jsonOut = $okBase + '.json'
    $mdOut   = $okBase + '.md'
    $metaOut = $okBase + '.metadata.json'
    $jsonText = ($obj | ConvertTo-Json -Depth 16)
    Set-Content -Path $jsonOut -Value $jsonText -Encoding UTF8
    Set-Content -Path $mdOut -Value $redactedText -Encoding UTF8
    $meta = [ordered]@{
        import_id              = $importId
        ok                     = $true
        provider               = $Provider
        role                   = $Role
        epoch_id               = $EpochId
        target_iteration_id    = $IterationId
        response_file          = $ResponseFile
        json_path              = $jsonOut
        md_path                = $mdOut
        redaction_report_path  = $redactionReportPath
        reviewer_self_id_text  = $selfBlk.text
        sha_verified           = $true
        applied_at_utc         = (Get-Date).ToUniversalTime().ToString('o')
    }
    Set-Content -Path $metaOut -Value (([pscustomobject]$meta) | ConvertTo-Json -Depth 16) -Encoding UTF8
    Set-Content -Path $redactionReportPath -Value (([pscustomobject]$redactionReport) | ConvertTo-Json -Depth 16) -Encoding UTF8

    return [pscustomobject]@{
        ok = $true
        import_id = $importId
        provider = $Provider
        role = $Role
        json_path = $jsonOut
        md_path = $mdOut
        metadata_path = $metaOut
    }
}

# CLI wrapper
if ($MyInvocation.InvocationName -ne '.' -and $ConfigPath -and $ResponseFile -and $EpochId -and $Provider -and $Role -and $IterationId) {
    $r = Import-WaggleExternalReviewResponse -ConfigPath $ConfigPath -EpochId $EpochId `
            -Provider $Provider -Role $Role -ResponseFile $ResponseFile -IterationId $IterationId
    if ($r.ok) {
        Write-Host ('Imported: ' + $r.import_id)
        Write-Host ('  json_path : ' + $r.json_path)
        exit 0
    } else {
        Write-Host ('Import failed: ' + $r.reason) -ForegroundColor Red
        foreach ($e in $r.errors) { Write-Host ('  ' + $e) -ForegroundColor Red }
        exit 1
    }
}
