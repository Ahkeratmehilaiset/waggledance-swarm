# SynthesisSchema.ps1
#
# Phase 2B: validate a parsed GPT-Synthesis output object against
# `schemas/review_synthesis.schema.json`. Same pattern as the
# ExternalReviewSchema validator. PS 5.1 compatible, no dependencies.

$ErrorActionPreference = 'Stop'

$Script:SynDecisionAllowed = @('continue','halt','requires_attention')
$Script:SynPriorityAllowed = @('high','medium','low','skip')
$Script:SynSeverityAllowed = @('critical','high','medium','low','info')

$Script:SynTopRequired = @(
    'synthesizer_self_id','target_iteration_id','epoch_id','source_evidence_sha256',
    'included_review_imports','excluded_review_imports','consolidated_findings',
    'consolidated_proposals','decision','next_claude_code_prompt_block_marker','completed'
)
$Script:SynSelfIdRequired = @('claimed_model_name','uses_extended_thinking_or_reasoning_mode')
$Script:SynFindingRequired = @('id','severity','title','where','why_it_matters','sources')
$Script:SynProposalRequired = @('id','title','rationale','merged_from_proposals','synthesizer_refinements','execution_priority')

function _Syn-HasField {
    param($Obj, [string] $Name)
    if ($null -eq $Obj) { return $false }
    if ($Obj -is [System.Collections.IDictionary]) { return $Obj.Contains($Name) }
    if ($Obj -is [pscustomobject]) {
        $p = $Obj.PSObject.Properties[$Name]
        return ($null -ne $p)
    }
    return $false
}
function _Syn-GetField {
    param($Obj, [string] $Name)
    if ($null -eq $Obj) { return $null }
    if ($Obj -is [System.Collections.IDictionary]) {
        if ($Obj.Contains($Name)) { return $Obj[$Name] }
        return $null
    }
    if ($Obj -is [pscustomobject]) {
        $p = $Obj.PSObject.Properties[$Name]
        if ($p) { return $p.Value }
        return $null
    }
    return $null
}
function _Syn-IsNonEmptyString { param($v); return ($null -ne $v -and $v -is [string] -and $v.Length -gt 0) }
function _Syn-IsBool { param($v); return ($null -ne $v -and $v -is [bool]) }

function Test-SynthesisObject {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [AllowNull()] $Object
    )
    $errors = New-Object System.Collections.Generic.List[string]

    if ($null -eq $Object) {
        $errors.Add('synthesis object is null') | Out-Null
        return [pscustomobject]@{ ok = $false; errors = $errors.ToArray() }
    }

    foreach ($k in $Script:SynTopRequired) {
        if (-not (_Syn-HasField -Obj $Object -Name $k)) {
            $errors.Add("missing top-level field: $k") | Out-Null
        }
    }
    if ($errors.Count -gt 0) {
        return [pscustomobject]@{ ok = $false; errors = $errors.ToArray() }
    }

    $self = _Syn-GetField -Obj $Object -Name 'synthesizer_self_id'
    if ($null -eq $self) {
        $errors.Add('synthesizer_self_id is null') | Out-Null
    } else {
        foreach ($k in $Script:SynSelfIdRequired) {
            if (-not (_Syn-HasField -Obj $self -Name $k)) {
                $errors.Add("synthesizer_self_id missing: $k") | Out-Null
            }
        }
        $cm = _Syn-GetField -Obj $self -Name 'claimed_model_name'
        if (-not (_Syn-IsNonEmptyString -v $cm)) {
            $errors.Add('synthesizer_self_id.claimed_model_name must be a non-empty string') | Out-Null
        }
        $u = _Syn-GetField -Obj $self -Name 'uses_extended_thinking_or_reasoning_mode'
        if ($null -ne $u -and -not (_Syn-IsBool -v $u)) {
            $errors.Add('synthesizer_self_id.uses_extended_thinking_or_reasoning_mode must be boolean') | Out-Null
        }
    }

    $tid = _Syn-GetField -Obj $Object -Name 'target_iteration_id'
    if (-not (_Syn-IsNonEmptyString -v $tid)) {
        $errors.Add('target_iteration_id must be a non-empty string') | Out-Null
    }
    $eid = _Syn-GetField -Obj $Object -Name 'epoch_id'
    if (-not (_Syn-IsNonEmptyString -v $eid)) {
        $errors.Add('epoch_id must be a non-empty string') | Out-Null
    }
    $esha = _Syn-GetField -Obj $Object -Name 'source_evidence_sha256'
    if (-not (_Syn-IsNonEmptyString -v $esha)) {
        $errors.Add('source_evidence_sha256 must be a non-empty string') | Out-Null
    } elseif ($esha -notmatch '^[a-f0-9]{64}$') {
        $errors.Add('source_evidence_sha256 must be 64 lowercase hex chars') | Out-Null
    }

    $decision = _Syn-GetField -Obj $Object -Name 'decision'
    if (-not (_Syn-IsNonEmptyString -v $decision)) {
        $errors.Add('decision must be a non-empty string') | Out-Null
    } elseif ($Script:SynDecisionAllowed -notcontains $decision) {
        $errors.Add("decision must be one of: $($Script:SynDecisionAllowed -join ',')") | Out-Null
    }

    $nextMark = _Syn-GetField -Obj $Object -Name 'next_claude_code_prompt_block_marker'
    if ($nextMark -ne 'next-claude-code-prompt') {
        $errors.Add("next_claude_code_prompt_block_marker must be 'next-claude-code-prompt' (got '$nextMark')") | Out-Null
    }

    $completed = _Syn-GetField -Obj $Object -Name 'completed'
    if (-not (_Syn-IsBool -v $completed) -or -not $completed) {
        $errors.Add('completed must be the boolean true') | Out-Null
    }

    # Findings: each must have severity in enum + at least one source
    $findings = _Syn-GetField -Obj $Object -Name 'consolidated_findings'
    if ($null -ne $findings -and -not ($findings -is [string])) {
        $idx = 0
        foreach ($f in @($findings)) {
            $fp = "consolidated_findings[$idx]"
            if ($null -eq $f) { $errors.Add("$fp is null") | Out-Null; $idx++; continue }
            foreach ($k in $Script:SynFindingRequired) {
                if (-not (_Syn-HasField -Obj $f -Name $k)) {
                    $errors.Add("$fp missing field: $k") | Out-Null
                }
            }
            $sev = _Syn-GetField -Obj $f -Name 'severity'
            if ($null -ne $sev -and $Script:SynSeverityAllowed -notcontains $sev) {
                $errors.Add("$fp.severity must be one of: $($Script:SynSeverityAllowed -join ',')") | Out-Null
            }
            $sources = _Syn-GetField -Obj $f -Name 'sources'
            $sourceCount = 0
            if ($null -ne $sources -and -not ($sources -is [string])) {
                $sourceCount = @($sources).Count
            }
            if ($sourceCount -lt 1) {
                $errors.Add("$fp.sources must contain at least one provenance entry") | Out-Null
            }
            $idx++
        }
    }

    # Proposals: high/medium priority MUST have either merged sources
    # or a non-empty synthesizer_refinements.
    $proposals = _Syn-GetField -Obj $Object -Name 'consolidated_proposals'
    if ($null -ne $proposals -and -not ($proposals -is [string])) {
        $idx = 0
        foreach ($p in @($proposals)) {
            $pp = "consolidated_proposals[$idx]"
            if ($null -eq $p) { $errors.Add("$pp is null") | Out-Null; $idx++; continue }
            foreach ($k in $Script:SynProposalRequired) {
                if (-not (_Syn-HasField -Obj $p -Name $k)) {
                    $errors.Add("$pp missing field: $k") | Out-Null
                }
            }
            $prio = _Syn-GetField -Obj $p -Name 'execution_priority'
            if ($null -ne $prio -and $Script:SynPriorityAllowed -notcontains $prio) {
                $errors.Add("$pp.execution_priority must be one of: $($Script:SynPriorityAllowed -join ',')") | Out-Null
            }
            if ($prio -eq 'high' -or $prio -eq 'medium') {
                $merged = _Syn-GetField -Obj $p -Name 'merged_from_proposals'
                $refine = _Syn-GetField -Obj $p -Name 'synthesizer_refinements'
                $mergedCount = 0
                if ($null -ne $merged -and -not ($merged -is [string])) {
                    $mergedCount = @($merged).Count
                }
                $refineOk = ($null -ne $refine -and $refine -is [string] -and $refine.Length -gt 0)
                if ($mergedCount -eq 0 -and -not $refineOk) {
                    $errors.Add("$pp has execution_priority='$prio' but neither merged_from_proposals nor synthesizer_refinements provide provenance") | Out-Null
                }
            }
            $idx++
        }
    }

    return [pscustomobject]@{
        ok = ($errors.Count -eq 0)
        errors = $errors.ToArray()
    }
}

function ConvertFrom-SynthesisJsonText {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [AllowEmptyString()] [string] $Text)
    if ([string]::IsNullOrWhiteSpace($Text)) {
        return [pscustomobject]@{ ok = $false; obj = $null; errors = @('empty json text') }
    }
    try {
        $obj = $Text | ConvertFrom-Json -ErrorAction Stop
        return [pscustomobject]@{ ok = $true; obj = $obj; errors = @() }
    } catch {
        return [pscustomobject]@{ ok = $false; obj = $null; errors = @("json parse failed: $($_.Exception.Message)") }
    }
}

function Find-SynthesisJsonBlock {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [AllowEmptyString()] [string] $Text)
    if ([string]::IsNullOrEmpty($Text)) {
        return [pscustomobject]@{ ok = $false; text = ''; errors = @('response is empty') }
    }
    $pat = '(?s)```synthesis-json\s*\r?\n(.*?)\r?\n```'
    $matches = [regex]::Matches($Text, $pat)
    if ($matches.Count -eq 0) {
        return [pscustomobject]@{ ok = $false; text = ''; errors = @('no fenced ```synthesis-json``` block found') }
    }
    if ($matches.Count -gt 1) {
        return [pscustomobject]@{ ok = $false; text = ''; errors = @("multiple synthesis-json blocks ($($matches.Count))") }
    }
    return [pscustomobject]@{ ok = $true; text = $matches[0].Groups[1].Value; errors = @() }
}

function Find-NextClaudeCodePromptBlock {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [AllowEmptyString()] [string] $Text)
    if ([string]::IsNullOrEmpty($Text)) {
        return [pscustomobject]@{ ok = $false; text = ''; errors = @('response is empty') }
    }
    $pat = '(?s)```next-claude-code-prompt\s*\r?\n(.*?)\r?\n```'
    $matches = [regex]::Matches($Text, $pat)
    if ($matches.Count -eq 0) {
        return [pscustomobject]@{ ok = $false; text = ''; errors = @('no fenced ```next-claude-code-prompt``` block found') }
    }
    if ($matches.Count -gt 1) {
        return [pscustomobject]@{ ok = $false; text = ''; errors = @("multiple next-claude-code-prompt blocks ($($matches.Count))") }
    }
    return [pscustomobject]@{ ok = $true; text = $matches[0].Groups[1].Value; errors = @() }
}

function Find-SynthesizerSelfIdBlock {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [AllowEmptyString()] [string] $Text)
    if ([string]::IsNullOrEmpty($Text)) {
        return [pscustomobject]@{ ok = $false; text = ''; errors = @('response is empty') }
    }
    $pat = '(?s)```synthesizer-self-id\s*\r?\n(.*?)\r?\n```'
    $m = [regex]::Match($Text, $pat)
    if (-not $m.Success) {
        return [pscustomobject]@{ ok = $false; text = ''; errors = @('no fenced ```synthesizer-self-id``` block found') }
    }
    return [pscustomobject]@{ ok = $true; text = $m.Groups[1].Value; errors = @() }
}

function Test-SynthesisCompletionMarker {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [AllowEmptyString()] [string] $Text)
    if ([string]::IsNullOrEmpty($Text)) { return $false }
    return [regex]::IsMatch($Text, '(?m)^SYNTHESIS-COMPLETE\s*$')
}
