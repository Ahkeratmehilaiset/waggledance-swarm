#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2B P10: import a manually-saved GPT-Synthesis response file,
    apply Phase 2A-1 redactor, validate against
    schemas/review_synthesis.schema.json, verify SHA contract +
    MANDATORY-first-line directive, and store the validated synthesis
    result. On decision=halt writes HALT.md.

    Failure modes recorded as .invalid records:
      - missing synthesizer-self-id block
      - 0 or >1 synthesis-json blocks
      - JSON parse / schema invalid
      - identity field mismatch (target_iteration_id, epoch_id)
      - source_evidence_sha256 mismatch with recomputed-from-disk SHA
      - missing SYNTHESIS-COMPLETE marker
      - decision=continue but missing or multiple next-claude-code-prompt block
      - decision=halt but next-claude-code-prompt block present
      - missing MANDATORY first line in next-prompt body (continue only)
#>
[CmdletBinding()]
param(
    [string] $ConfigPath = '',
    [string] $EpochId = '',
    [string] $IterationId = '',
    [string] $ResponseFile = ''
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib/Redactor.ps1')
. (Join-Path $PSScriptRoot 'lib/external_review/ProviderProfiles.ps1')
. (Join-Path $PSScriptRoot 'lib/external_review/EvidenceBundler.ps1')
. (Join-Path $PSScriptRoot 'lib/external_review/SynthesisSchema.ps1')

$Script:SynthesisMandatoryFirstLine = 'MANDATORY: Use Claude Opus 4.7 (--model claude-opus-4-7) for this iteration. Do not downgrade.'

function _Isr-NowUtc { return (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH-mm-ssZ') }
function _Isr-ShortId { return [guid]::NewGuid().ToString('N').Substring(0, 8) }

function _Isr-FirstNonBlankLine {
    param([string] $Text)
    if ([string]::IsNullOrEmpty($Text)) { return '' }
    $lines = $Text -split "(`r`n|`n)"
    foreach ($l in $lines) {
        if ($l -eq "`r`n" -or $l -eq "`n") { continue }
        if ([string]::IsNullOrWhiteSpace($l)) { continue }
        return $l.TrimEnd()
    }
    return ''
}

function Import-WaggleSynthesisResult {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $ConfigPath,
        [Parameter(Mandatory)] [string] $EpochId,
        [Parameter(Mandatory)] [string] $IterationId,
        [Parameter(Mandatory)] [string] $ResponseFile
    )

    if (-not (Test-Path -LiteralPath $ConfigPath)) { throw "config not found: $ConfigPath" }
    if (-not (Test-Path -LiteralPath $ResponseFile)) { throw "response file not found: $ResponseFile" }

    $cfg = Get-Content -Raw -Path $ConfigPath -Encoding UTF8 | ConvertFrom-Json
    $er = Get-WaggleExternalReviewConfig -Config $cfg
    $projectRoot = $cfg.projectRoot
    $iterationsDir = if ($cfg.PSObject.Properties['iterationsDir'] -and $cfg.iterationsDir) { [string]$cfg.iterationsDir } else { 'iterations' }
    $iterFolder = Join-Path (Join-Path $projectRoot $iterationsDir) $IterationId
    $synthRel = [string]$er.synthesis_dir_relative
    if (-not $synthRel) { $synthRel = 'external_reviews/synthesis' }
    $synthDir = Join-Path $iterFolder ($synthRel.TrimEnd('/','\') + '/' + $EpochId)
    if (-not (Test-Path -LiteralPath $synthDir)) {
        New-Item -ItemType Directory -Path $synthDir -Force | Out-Null
    }

    $importId = (_Isr-NowUtc) + '_synthesis_' + (_Isr-ShortId)
    $okBase = Join-Path $synthDir ('result_' + $importId)
    $invalidBase = $okBase + '.invalid'

    $rawText = Get-Content -Raw -Path $ResponseFile -Encoding UTF8
    if ($null -eq $rawText) { $rawText = '' }
    $red = Invoke-WaggleRedaction -Text $rawText
    $redactedText = $red.text
    $redactionReport = [pscustomobject]@{
        synthesis_import_id = $importId
        epoch_id = $EpochId; target_iteration_id = $IterationId
        report = $red.report
        applied_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    }
    $redactionPath = $okBase + '.redaction_report.json'

    $selfBlk = Find-SynthesizerSelfIdBlock -Text $redactedText
    $jsonBlk = Find-SynthesisJsonBlock     -Text $redactedText
    $promptBlk = Find-NextClaudeCodePromptBlock -Text $redactedText
    $promptCount = ([regex]::Matches($redactedText, '(?s)```next-claude-code-prompt\s*\r?\n')).Count
    $markerOk = Test-SynthesisCompletionMarker -Text $redactedText

    function _writeInvalid {
        param([string] $reason, $extras = @())
        $invalidMd = $invalidBase + '.md'
        $invalidMeta = $invalidBase + '.metadata.json'
        Set-Content -Path $invalidMd -Value $redactedText -Encoding UTF8
        $meta = [ordered]@{
            synthesis_import_id = $importId
            ok = $false
            reason = $reason
            extras = $extras
            epoch_id = $EpochId; target_iteration_id = $IterationId
            response_file = $ResponseFile
            redaction_report = $redactionReport
            applied_at_utc = (Get-Date).ToUniversalTime().ToString('o')
        }
        Set-Content -Path $invalidMeta -Value (([pscustomobject]$meta) | ConvertTo-Json -Depth 16) -Encoding UTF8
        Set-Content -Path $redactionPath -Value (([pscustomobject]$redactionReport) | ConvertTo-Json -Depth 16) -Encoding UTF8
    }

    if (-not $selfBlk.ok) {
        _writeInvalid -reason 'synthesizer_self_id_block_missing' -extras $selfBlk.errors
        return [pscustomobject]@{ ok = $false; synthesis_import_id = $importId; reason = 'synthesizer_self_id_block_missing'; errors = $selfBlk.errors }
    }
    if (-not $jsonBlk.ok) {
        _writeInvalid -reason 'synthesis_json_block_missing_or_multiple' -extras $jsonBlk.errors
        return [pscustomobject]@{ ok = $false; synthesis_import_id = $importId; reason = 'synthesis_json_block_missing_or_multiple'; errors = $jsonBlk.errors }
    }
    if (-not $markerOk) {
        _writeInvalid -reason 'synthesis_completion_marker_missing'
        return [pscustomobject]@{ ok = $false; synthesis_import_id = $importId; reason = 'synthesis_completion_marker_missing'; errors = @('SYNTHESIS-COMPLETE marker not found') }
    }

    $parsed = ConvertFrom-SynthesisJsonText -Text $jsonBlk.text
    if (-not $parsed.ok) {
        _writeInvalid -reason 'synthesis_json_parse_failed' -extras $parsed.errors
        return [pscustomobject]@{ ok = $false; synthesis_import_id = $importId; reason = 'synthesis_json_parse_failed'; errors = $parsed.errors }
    }
    $obj = $parsed.obj
    $schema = Test-SynthesisObject -Object $obj
    if (-not $schema.ok) {
        _writeInvalid -reason 'synthesis_json_schema_invalid' -extras $schema.errors
        return [pscustomobject]@{ ok = $false; synthesis_import_id = $importId; reason = 'synthesis_json_schema_invalid'; errors = $schema.errors }
    }

    # Identity cross-checks
    $errs = New-Object System.Collections.Generic.List[string]
    if ([string]$obj.target_iteration_id -ne $IterationId) {
        $errs.Add("target_iteration_id mismatch: response='$([string]$obj.target_iteration_id)' expected='$IterationId'") | Out-Null
    }
    if ([string]$obj.epoch_id -ne $EpochId) {
        $errs.Add("epoch_id mismatch: response='$([string]$obj.epoch_id)' expected='$EpochId'") | Out-Null
    }
    if (-not [bool]$obj.completed) { $errs.Add('completed != true') | Out-Null }
    if ($errs.Count -gt 0) {
        _writeInvalid -reason 'identity_fields_mismatch' -extras $errs.ToArray()
        return [pscustomobject]@{ ok = $false; synthesis_import_id = $importId; reason = 'identity_fields_mismatch'; errors = $errs.ToArray() }
    }

    # SHA verification
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
    if (-not ($epochSha -and ($responseSha -eq $epochSha))) {
        _writeInvalid -reason 'source_evidence_sha256_mismatch' -extras @("response_sha=$responseSha", "epoch_sha=$epochSha")
        return [pscustomobject]@{ ok = $false; synthesis_import_id = $importId; reason = 'source_evidence_sha256_mismatch'; errors = @("response_sha=$responseSha epoch_sha=$epochSha") }
    }

    $decision = [string]$obj.decision

    # next-claude-code-prompt block discipline
    $promptText = ''
    $haltMarker = $null
    switch ($decision) {
        'halt' {
            if ($promptCount -gt 0) {
                _writeInvalid -reason 'halt_decision_but_prompt_block_present' -extras @("prompt_block_count=$promptCount")
                return [pscustomobject]@{ ok = $false; synthesis_import_id = $importId; reason = 'halt_decision_but_prompt_block_present'; errors = @("prompt_block_count=$promptCount") }
            }
            $haltMarker = if ($obj.PSObject.Properties['halt_marker'] -and $obj.halt_marker) { [string]$obj.halt_marker } else { 'WAGGLE_HALT' }
        }
        'continue' {
            if ($promptCount -ne 1 -or -not $promptBlk.ok) {
                _writeInvalid -reason 'continue_decision_requires_single_next_prompt_block' -extras @("prompt_block_count=$promptCount")
                return [pscustomobject]@{ ok = $false; synthesis_import_id = $importId; reason = 'continue_decision_requires_single_next_prompt_block'; errors = @("prompt_block_count=$promptCount") }
            }
            $first = _Isr-FirstNonBlankLine -Text $promptBlk.text
            if ($first -ne $Script:SynthesisMandatoryFirstLine) {
                _writeInvalid -reason 'next_prompt_mandatory_directive_missing' -extras @("first_line='$first'")
                return [pscustomobject]@{ ok = $false; synthesis_import_id = $importId; reason = 'next_prompt_mandatory_directive_missing'; errors = @("first_line='$first'") }
            }
            $promptText = $promptBlk.text
        }
        'requires_attention' {
            if ($promptCount -gt 1) {
                _writeInvalid -reason 'requires_attention_multiple_prompt_blocks' -extras @("prompt_block_count=$promptCount")
                return [pscustomobject]@{ ok = $false; synthesis_import_id = $importId; reason = 'requires_attention_multiple_prompt_blocks'; errors = @("prompt_block_count=$promptCount") }
            }
            if ($promptCount -eq 1 -and $promptBlk.ok) {
                $first = _Isr-FirstNonBlankLine -Text $promptBlk.text
                if ($first -ne $Script:SynthesisMandatoryFirstLine) {
                    _writeInvalid -reason 'next_prompt_mandatory_directive_missing' -extras @("first_line='$first'")
                    return [pscustomobject]@{ ok = $false; synthesis_import_id = $importId; reason = 'next_prompt_mandatory_directive_missing'; errors = @("first_line='$first'") }
                }
                $promptText = $promptBlk.text
            }
        }
        default {
            _writeInvalid -reason 'unknown_decision' -extras @("decision='$decision'")
            return [pscustomobject]@{ ok = $false; synthesis_import_id = $importId; reason = 'unknown_decision'; errors = @("decision='$decision'") }
        }
    }

    # ---- Write valid records ------------------------------------------
    $jsonOut = $okBase + '.json'
    $mdOut   = $okBase + '.md'
    $metaOut = $okBase + '.metadata.json'
    $jsonText = ($obj | ConvertTo-Json -Depth 16)
    Set-Content -Path $jsonOut -Value $jsonText -Encoding UTF8
    Set-Content -Path $mdOut -Value $redactedText -Encoding UTF8
    Set-Content -Path $redactionPath -Value (([pscustomobject]$redactionReport) | ConvertTo-Json -Depth 16) -Encoding UTF8

    $nextPromptPath = ''
    if ($promptText) {
        $nextPromptPath = Join-Path $synthDir 'next_claude_code_prompt.md'
        Set-Content -Path $nextPromptPath -Value $promptText -Encoding UTF8
    }

    $haltPath = ''
    if ($decision -eq 'halt') {
        $haltPath = Join-Path $synthDir 'HALT.md'
        $sb = New-Object System.Text.StringBuilder
        [void]$sb.AppendLine('# WaggleDance epoch HALT')
        [void]$sb.AppendLine('')
        [void]$sb.AppendLine('halt_marker: ' + $haltMarker)
        [void]$sb.AppendLine('synthesis_import_id: ' + $importId)
        [void]$sb.AppendLine('epoch_id: ' + $EpochId)
        [void]$sb.AppendLine('target_iteration_id: ' + $IterationId)
        [void]$sb.AppendLine('decided_at_utc: ' + (Get-Date).ToUniversalTime().ToString('o'))
        [void]$sb.AppendLine('')
        if ($obj.PSObject.Properties['synthesis_summary'] -and $obj.synthesis_summary) {
            [void]$sb.AppendLine('## synthesis_summary')
            [void]$sb.AppendLine('')
            [void]$sb.AppendLine([string]$obj.synthesis_summary)
        }
        Set-Content -Path $haltPath -Value $sb.ToString() -Encoding UTF8
    }

    $meta = [ordered]@{
        synthesis_import_id   = $importId
        ok                    = $true
        epoch_id              = $EpochId
        target_iteration_id   = $IterationId
        response_file         = $ResponseFile
        json_path             = $jsonOut
        md_path               = $mdOut
        redaction_report_path = $redactionPath
        decision              = $decision
        next_prompt_path      = $nextPromptPath
        halt_marker           = $haltMarker
        halt_path             = $haltPath
        sha_verified          = $true
        synthesizer_self_id_text = $selfBlk.text
        applied_at_utc        = (Get-Date).ToUniversalTime().ToString('o')
    }
    Set-Content -Path $metaOut -Value (([pscustomobject]$meta) | ConvertTo-Json -Depth 16) -Encoding UTF8

    return [pscustomobject]@{
        ok = $true
        synthesis_import_id = $importId
        decision = $decision
        json_path = $jsonOut
        md_path = $mdOut
        next_prompt_path = $nextPromptPath
        halt_path = $haltPath
        halt_marker = $haltMarker
        metadata_path = $metaOut
    }
}

# CLI wrapper
if ($MyInvocation.InvocationName -ne '.' -and $ConfigPath -and $EpochId -and $IterationId -and $ResponseFile) {
    $r = Import-WaggleSynthesisResult -ConfigPath $ConfigPath -EpochId $EpochId -IterationId $IterationId -ResponseFile $ResponseFile
    if ($r.ok) {
        Write-Host ('Synthesis imported: ' + $r.synthesis_import_id)
        Write-Host ('  decision     : ' + $r.decision)
        if ($r.next_prompt_path) { Write-Host ('  next_prompt  : ' + $r.next_prompt_path) }
        if ($r.halt_path)        { Write-Host ('  halt_path    : ' + $r.halt_path) }
        exit 0
    } else {
        Write-Host ('Synthesis import failed: ' + $r.reason) -ForegroundColor Red
        foreach ($e in $r.errors) { Write-Host ('  ' + $e) -ForegroundColor Red }
        exit 1
    }
}
