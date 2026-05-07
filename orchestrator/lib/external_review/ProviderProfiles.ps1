# ProviderProfiles.ps1
#
# Phase 2B P2: small helpers that read the Phase 2B config blocks
# (external_review.*, iteration_cycle.*, models.*) and return
# default-filled profile objects. Callers do not handle missing keys.

$ErrorActionPreference = 'Stop'

$Script:DefaultExternalReviewConfig = [ordered]@{
    enabled                          = $true
    queue_dir_relative               = 'external_reviews/queue'
    imported_dir_relative            = 'external_reviews/imported'
    synthesis_dir_relative           = 'external_reviews/synthesis'
    max_attachments_per_provider     = 20
    fail_on_attachment_overflow      = $true
    auto_approval_rule               = 'all_reviewers_below_needs_changes'
    manual_pause_flag_relative       = 'state/pause_external_review.flag'
    halt_marker                      = 'WAGGLE_HALT'
    session_resume_threshold_hours   = 4
}

$Script:DefaultProviderProfiles = [ordered]@{
    claude_web    = @{ enabled = $true; timeout_sec = 600;  expected_model_in_ui = 'Claude Opus 4.7 (Max plan)' }
    gemini        = @{ enabled = $true; timeout_sec = 600;  expected_model_in_ui = 'Gemini Pro Advanced' }
    grok          = @{ enabled = $true; timeout_sec = 900;  expected_model_in_ui = 'Grok Expert mode' }
    gpt_synthesis = @{ enabled = $true; timeout_sec = 4800; expected_model_in_ui = 'GPT Pro 5.5 Extended Thinking' }
}

$Script:DefaultIterationCycleConfig = [ordered]@{
    local_iterations_per_external_review     = 3
    max_iterations_per_session               = 50
    early_trigger_on_regression              = $true
    early_trigger_on_hardening_gate_failure  = $true
    early_trigger_on_internal_critical_finding = $true
    early_trigger_on_no_work_consecutive     = 2
    no_work_diff_min_bytes                   = 1
    no_work_raportti_min_bytes               = 1
    no_work_stdout_min_meaningful_bytes      = 100
}

$Script:DefaultModelConfig = [ordered]@{
    claude_code   = 'claude-opus-4-7'
    claude_web    = 'Claude Opus 4.7 (Max plan)'
    gemini        = 'Gemini Pro Advanced'
    grok          = 'Grok Expert mode'
    gpt_synthesis = 'GPT Pro 5.5 Extended Thinking'
}

function _Pp-FieldOr {
    param($Obj, [string] $Name, $Default)
    if ($null -eq $Obj) { return $Default }
    if ($Obj -is [System.Collections.IDictionary]) {
        if ($Obj.Contains($Name)) {
            $v = $Obj[$Name]
            if ($null -eq $v) { return $Default }
            return $v
        }
        return $Default
    }
    if ($Obj -is [pscustomobject]) {
        $p = $Obj.PSObject.Properties[$Name]
        if ($p -and ($null -ne $p.Value)) { return $p.Value }
        return $Default
    }
    return $Default
}

function Get-WaggleExternalReviewConfig {
    [CmdletBinding()]
    param([Parameter(Mandatory)] $Config)
    $er = _Pp-FieldOr -Obj $Config -Name 'external_review' -Default $null
    $providers = _Pp-FieldOr -Obj $er -Name 'providers' -Default $null

    $resolved = [ordered]@{}
    foreach ($k in $Script:DefaultExternalReviewConfig.Keys) {
        $resolved[$k] = _Pp-FieldOr -Obj $er -Name $k -Default $Script:DefaultExternalReviewConfig[$k]
    }
    $resolvedProviders = [ordered]@{}
    foreach ($name in $Script:DefaultProviderProfiles.Keys) {
        $defaults = $Script:DefaultProviderProfiles[$name]
        $live = _Pp-FieldOr -Obj $providers -Name $name -Default $null
        $merged = [ordered]@{}
        foreach ($k in $defaults.Keys) {
            $merged[$k] = _Pp-FieldOr -Obj $live -Name $k -Default $defaults[$k]
        }
        $resolvedProviders[$name] = ([pscustomobject]$merged)
    }
    $resolved['providers'] = ([pscustomobject]$resolvedProviders)
    return ([pscustomobject]$resolved)
}

function Get-WaggleIterationCycleConfig {
    [CmdletBinding()]
    param([Parameter(Mandatory)] $Config)
    $ic = _Pp-FieldOr -Obj $Config -Name 'iteration_cycle' -Default $null
    $resolved = [ordered]@{}
    foreach ($k in $Script:DefaultIterationCycleConfig.Keys) {
        $resolved[$k] = _Pp-FieldOr -Obj $ic -Name $k -Default $Script:DefaultIterationCycleConfig[$k]
    }
    return ([pscustomobject]$resolved)
}

function Get-WaggleModelConfig {
    [CmdletBinding()]
    param([Parameter(Mandatory)] $Config)
    $m = _Pp-FieldOr -Obj $Config -Name 'models' -Default $null
    $resolved = [ordered]@{}
    foreach ($k in $Script:DefaultModelConfig.Keys) {
        $resolved[$k] = _Pp-FieldOr -Obj $m -Name $k -Default $Script:DefaultModelConfig[$k]
    }
    return ([pscustomobject]$resolved)
}
