# ExternalReviewSchema.ps1
#
# Phase 2B: validate a parsed external-reviewer output object against
# `schemas/external_review.schema.json`. Same pattern as
# orchestrator/lib/review/ReviewSchema.ps1 -- pure-function, PS 5.1
# compatible, no external dependencies.
#
# Returns @{ ok = <bool>; errors = <string[]> }.

$ErrorActionPreference = 'Stop'

$Script:ExtRoleAllowed     = @('architect','security','reliability','performance','ux','maintainer','synthesis')
$Script:ExtProviderAllowed = @('claude_web','gemini','grok','gpt','other')
$Script:ExtVerdictAllowed  = @('pass','pass_with_notes','needs_attention','needs_changes','fail','insufficient_evidence')
$Script:ExtSeverityAllowed = @('critical','high','medium','low','info')
$Script:ExtEffortAllowed   = @('small','medium','large')

$Script:ExtTopRequired = @(
    'reviewer_self_id','provider','role','target_iteration_id','epoch_id',
    'source_evidence_sha256','reviewer_summary','verdict','findings',
    'suggested_next_actions','confidence','limitations','completed'
)
$Script:ExtSelfIdRequired = @(
    'claimed_model_name','self_assessed_strengths_for_this_review',
    'self_assessed_limitations_for_this_review','uses_extended_thinking_or_reasoning_mode'
)
$Script:ExtFindingRequired = @('id','severity','title','where','evidence','why_it_matters','recommended_action')
$Script:ExtProposalRequired = @('id','title','rationale','approach','estimated_effort','risks','expected_payoff')

function _Ext-HasField {
    param($Obj, [string] $Name)
    if ($null -eq $Obj) { return $false }
    if ($Obj -is [System.Collections.IDictionary]) { return $Obj.Contains($Name) }
    if ($Obj -is [pscustomobject]) {
        $p = $Obj.PSObject.Properties[$Name]
        return ($null -ne $p)
    }
    return $false
}
function _Ext-GetField {
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
function _Ext-IsNonEmptyString {
    param($v)
    return ($null -ne $v -and $v -is [string] -and $v.Length -gt 0)
}
function _Ext-IsBool { param($v); return ($null -ne $v -and $v -is [bool]) }
function _Ext-IsInt {
    param($v)
    if ($null -eq $v) { return $false }
    if ($v -is [int] -or $v -is [long]) { return $true }
    if ($v -is [double] -or $v -is [decimal]) {
        return ([math]::Floor([double]$v) -eq [double]$v)
    }
    return $false
}

function Test-ExternalReviewObject {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [AllowNull()] $Object
    )
    $errors = New-Object System.Collections.Generic.List[string]

    if ($null -eq $Object) {
        $errors.Add('external review object is null') | Out-Null
        return [pscustomobject]@{ ok = $false; errors = $errors.ToArray() }
    }

    foreach ($k in $Script:ExtTopRequired) {
        if (-not (_Ext-HasField -Obj $Object -Name $k)) {
            $errors.Add("missing top-level field: $k") | Out-Null
        }
    }
    if ($errors.Count -gt 0) {
        return [pscustomobject]@{ ok = $false; errors = $errors.ToArray() }
    }

    # reviewer_self_id sub-object
    $self = _Ext-GetField -Obj $Object -Name 'reviewer_self_id'
    if ($null -eq $self) {
        $errors.Add('reviewer_self_id is null') | Out-Null
    } else {
        foreach ($k in $Script:ExtSelfIdRequired) {
            if (-not (_Ext-HasField -Obj $self -Name $k)) {
                $errors.Add("reviewer_self_id missing: $k") | Out-Null
            }
        }
        if (_Ext-HasField -Obj $self -Name 'claimed_model_name') {
            $cm = _Ext-GetField -Obj $self -Name 'claimed_model_name'
            if (-not (_Ext-IsNonEmptyString -v $cm)) {
                $errors.Add('reviewer_self_id.claimed_model_name must be a non-empty string') | Out-Null
            }
        }
        if (_Ext-HasField -Obj $self -Name 'uses_extended_thinking_or_reasoning_mode') {
            $u = _Ext-GetField -Obj $self -Name 'uses_extended_thinking_or_reasoning_mode'
            if (-not (_Ext-IsBool -v $u)) {
                $errors.Add('reviewer_self_id.uses_extended_thinking_or_reasoning_mode must be boolean') | Out-Null
            }
        }
    }

    $provider = _Ext-GetField -Obj $Object -Name 'provider'
    if (-not (_Ext-IsNonEmptyString -v $provider)) {
        $errors.Add('provider must be a non-empty string') | Out-Null
    } elseif ($Script:ExtProviderAllowed -notcontains $provider) {
        $errors.Add("provider must be one of: $($Script:ExtProviderAllowed -join ',')") | Out-Null
    }
    $role = _Ext-GetField -Obj $Object -Name 'role'
    if (-not (_Ext-IsNonEmptyString -v $role)) {
        $errors.Add('role must be a non-empty string') | Out-Null
    } elseif ($Script:ExtRoleAllowed -notcontains $role) {
        $errors.Add("role must be one of: $($Script:ExtRoleAllowed -join ',')") | Out-Null
    }
    $tid = _Ext-GetField -Obj $Object -Name 'target_iteration_id'
    if (-not (_Ext-IsNonEmptyString -v $tid)) {
        $errors.Add('target_iteration_id must be a non-empty string') | Out-Null
    }
    $eid = _Ext-GetField -Obj $Object -Name 'epoch_id'
    if (-not (_Ext-IsNonEmptyString -v $eid)) {
        $errors.Add('epoch_id must be a non-empty string') | Out-Null
    }
    $esha = _Ext-GetField -Obj $Object -Name 'source_evidence_sha256'
    if (-not (_Ext-IsNonEmptyString -v $esha)) {
        $errors.Add('source_evidence_sha256 must be a non-empty string') | Out-Null
    } elseif ($esha -notmatch '^[a-f0-9]{64}$') {
        $errors.Add('source_evidence_sha256 must be 64 lowercase hex chars') | Out-Null
    }
    $rsum = _Ext-GetField -Obj $Object -Name 'reviewer_summary'
    if (-not (_Ext-IsNonEmptyString -v $rsum)) {
        $errors.Add('reviewer_summary must be a non-empty string') | Out-Null
    } elseif ($rsum.Length -gt 4000) {
        $errors.Add('reviewer_summary must be <= 4000 chars') | Out-Null
    }
    $verdict = _Ext-GetField -Obj $Object -Name 'verdict'
    if (-not (_Ext-IsNonEmptyString -v $verdict)) {
        $errors.Add('verdict must be a non-empty string') | Out-Null
    } elseif ($Script:ExtVerdictAllowed -notcontains $verdict) {
        $errors.Add("verdict must be one of: $($Script:ExtVerdictAllowed -join ',')") | Out-Null
    }

    $findings = _Ext-GetField -Obj $Object -Name 'findings'
    if ($null -eq $findings) {
        # ok, may be empty array
    } elseif (-not ($findings -is [string])) {
        $idx = 0
        foreach ($f in @($findings)) {
            $fp = "findings[$idx]"
            if ($null -eq $f) { $errors.Add("$fp is null") | Out-Null; $idx++; continue }
            foreach ($k in $Script:ExtFindingRequired) {
                if (-not (_Ext-HasField -Obj $f -Name $k)) {
                    $errors.Add("$fp missing field: $k") | Out-Null
                }
            }
            $sev = _Ext-GetField -Obj $f -Name 'severity'
            if ($null -ne $sev -and $Script:ExtSeverityAllowed -notcontains $sev) {
                $errors.Add("$fp.severity must be one of: $($Script:ExtSeverityAllowed -join ',')") | Out-Null
            }
            $idx++
        }
    } else {
        $errors.Add('findings must be an array') | Out-Null
    }

    $proposals = _Ext-GetField -Obj $Object -Name 'suggested_next_actions'
    if ($null -ne $proposals -and -not ($proposals -is [string])) {
        $idx = 0
        foreach ($p in @($proposals)) {
            $pp = "suggested_next_actions[$idx]"
            if ($null -eq $p) { $errors.Add("$pp is null") | Out-Null; $idx++; continue }
            foreach ($k in $Script:ExtProposalRequired) {
                if (-not (_Ext-HasField -Obj $p -Name $k)) {
                    $errors.Add("$pp missing field: $k") | Out-Null
                }
            }
            $eff = _Ext-GetField -Obj $p -Name 'estimated_effort'
            if ($null -ne $eff -and $Script:ExtEffortAllowed -notcontains $eff) {
                $errors.Add("$pp.estimated_effort must be one of: $($Script:ExtEffortAllowed -join ',')") | Out-Null
            }
            $idx++
        }
    }

    $completed = _Ext-GetField -Obj $Object -Name 'completed'
    if (-not (_Ext-IsBool -v $completed) -or -not $completed) {
        $errors.Add('completed must be the boolean true') | Out-Null
    }

    return [pscustomobject]@{
        ok = ($errors.Count -eq 0)
        errors = $errors.ToArray()
    }
}

function ConvertFrom-ExternalReviewJsonText {
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

function Find-ExternalReviewBlock {
    <#
    .SYNOPSIS
    Locate the unique fenced ```external-review-json``` block in a
    response. Multiple blocks -> fail. Missing -> fail.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)] [AllowEmptyString()] [string] $Text)
    if ([string]::IsNullOrEmpty($Text)) {
        return [pscustomobject]@{ ok = $false; text = ''; errors = @('response is empty') }
    }
    $pat = '(?s)```external-review-json\s*\r?\n(.*?)\r?\n```'
    $matches = [regex]::Matches($Text, $pat)
    if ($matches.Count -eq 0) {
        return [pscustomobject]@{ ok = $false; text = ''; errors = @('no fenced ```external-review-json``` block found') }
    }
    if ($matches.Count -gt 1) {
        return [pscustomobject]@{ ok = $false; text = ''; errors = @("multiple fenced external-review-json blocks ($($matches.Count)); only one is allowed") }
    }
    return [pscustomobject]@{ ok = $true; text = $matches[0].Groups[1].Value; errors = @() }
}

function Find-ReviewerSelfIdBlock {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [AllowEmptyString()] [string] $Text)
    if ([string]::IsNullOrEmpty($Text)) {
        return [pscustomobject]@{ ok = $false; text = ''; errors = @('response is empty') }
    }
    $pat = '(?s)```reviewer-self-id\s*\r?\n(.*?)\r?\n```'
    $m = [regex]::Match($Text, $pat)
    if (-not $m.Success) {
        return [pscustomobject]@{ ok = $false; text = ''; errors = @('no fenced ```reviewer-self-id``` block found') }
    }
    return [pscustomobject]@{ ok = $true; text = $m.Groups[1].Value; errors = @() }
}

function Test-ExternalReviewCompletionMarker {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [AllowEmptyString()] [string] $Text)
    if ([string]::IsNullOrEmpty($Text)) { return $false }
    return [regex]::IsMatch($Text, '(?m)^EXTERNAL-REVIEW-COMPLETE\s*$')
}
