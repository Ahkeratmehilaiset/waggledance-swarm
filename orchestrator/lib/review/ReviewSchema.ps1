# ReviewSchema.ps1
#
# Phase 2A-2: validate a parsed review object against the contract in
# schemas/review.schema.json. PS 5.1 compatible. No external libraries.
#
# We don't load json-schema-validator; Phase 2A-2 keeps zero new
# dependencies. Instead this file hardcodes the same rules and returns
# @{ ok = <bool>; errors = <string[]> }.

$ErrorActionPreference = 'Stop'

$Script:ReviewRoleAllowed     = @('architect', 'security', 'reliability')
$Script:ReviewVerdictAllowed  = @('pass', 'pass_with_notes', 'needs_attention', 'fail')
$Script:ReviewSeverityAllowed = @('critical', 'high', 'medium', 'low', 'info')

$Script:ReviewTopRequired = @(
    'role',
    'target_iteration_id',
    'source_package_path',
    'summary',
    'verdict',
    'findings',
    'metrics',
    'completed'
)

$Script:ReviewFindingRequired = @(
    'id',
    'severity',
    'title',
    'where',
    'evidence',
    'why_it_matters',
    'recommended_action'
)

$Script:ReviewMetricsRequired = @(
    'files_reviewed',
    'lines_reviewed',
    'review_duration_seconds'
)

function Get-ReviewSchemaConstants {
    return [pscustomobject]@{
        roles      = $Script:ReviewRoleAllowed
        verdicts   = $Script:ReviewVerdictAllowed
        severities = $Script:ReviewSeverityAllowed
    }
}

function Test-ReviewObjectField {
    param(
        $Obj,
        [string] $Name
    )
    if ($null -eq $Obj) { return $false }
    if ($Obj -is [System.Collections.IDictionary]) {
        return $Obj.Contains($Name)
    }
    if ($Obj -is [pscustomobject]) {
        $props = $Obj.PSObject.Properties
        if ($null -eq $props) { return $false }
        foreach ($p in $props) {
            if ($p.Name -eq $Name) { return $true }
        }
        return $false
    }
    return $false
}

function Get-ReviewObjectField {
    param(
        $Obj,
        [string] $Name
    )
    if ($null -eq $Obj) { return $null }
    if ($Obj -is [System.Collections.IDictionary]) {
        if ($Obj.Contains($Name)) { return $Obj[$Name] } else { return $null }
    }
    if ($Obj -is [pscustomobject]) {
        $p = $Obj.PSObject.Properties[$Name]
        if ($p) { return $p.Value }
        return $null
    }
    return $null
}

function Test-ReviewIsString {
    param($Value)
    if ($null -eq $Value) { return $false }
    return ($Value -is [string])
}

function Test-ReviewIsNonEmptyString {
    param($Value)
    if (-not (Test-ReviewIsString -Value $Value)) { return $false }
    return ($Value.Length -gt 0)
}

function Test-ReviewIsNonNegativeInteger {
    param($Value)
    if ($null -eq $Value) { return $false }
    if ($Value -is [int] -or $Value -is [long]) { return ($Value -ge 0) }
    if ($Value -is [double] -or $Value -is [decimal]) {
        if ([math]::Floor([double]$Value) -ne [double]$Value) { return $false }
        return ([double]$Value -ge 0)
    }
    return $false
}

function Test-ReviewIsBool {
    param($Value)
    if ($null -eq $Value) { return $false }
    return ($Value -is [bool])
}

function Test-ReviewObject {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] $Object
    )
    $errors = New-Object System.Collections.Generic.List[string]

    if ($null -eq $Object) {
        $errors.Add('review object is null') | Out-Null
        return [pscustomobject]@{ ok = $false; errors = $errors.ToArray() }
    }

    foreach ($k in $Script:ReviewTopRequired) {
        if (-not (Test-ReviewObjectField -Obj $Object -Name $k)) {
            $errors.Add("missing top-level field: $k") | Out-Null
        }
    }

    if ($errors.Count -gt 0) {
        return [pscustomobject]@{ ok = $false; errors = $errors.ToArray() }
    }

    $role = Get-ReviewObjectField -Obj $Object -Name 'role'
    if (-not (Test-ReviewIsNonEmptyString -Value $role)) {
        $errors.Add('role must be a non-empty string') | Out-Null
    } elseif ($Script:ReviewRoleAllowed -notcontains $role) {
        $errors.Add("role must be one of $($Script:ReviewRoleAllowed -join ',')") | Out-Null
    }

    $tid = Get-ReviewObjectField -Obj $Object -Name 'target_iteration_id'
    if (-not (Test-ReviewIsNonEmptyString -Value $tid)) {
        $errors.Add('target_iteration_id must be a non-empty string') | Out-Null
    }

    $spp = Get-ReviewObjectField -Obj $Object -Name 'source_package_path'
    if (-not (Test-ReviewIsNonEmptyString -Value $spp)) {
        $errors.Add('source_package_path must be a non-empty string') | Out-Null
    }

    $sum = Get-ReviewObjectField -Obj $Object -Name 'summary'
    if (-not (Test-ReviewIsNonEmptyString -Value $sum)) {
        $errors.Add('summary must be a non-empty string') | Out-Null
    }

    $verdict = Get-ReviewObjectField -Obj $Object -Name 'verdict'
    if (-not (Test-ReviewIsNonEmptyString -Value $verdict)) {
        $errors.Add('verdict must be a non-empty string') | Out-Null
    } elseif ($Script:ReviewVerdictAllowed -notcontains $verdict) {
        $errors.Add("verdict must be one of $($Script:ReviewVerdictAllowed -join ',')") | Out-Null
    }

    $completed = Get-ReviewObjectField -Obj $Object -Name 'completed'
    if (-not (Test-ReviewIsBool -Value $completed)) {
        $errors.Add('completed must be a boolean') | Out-Null
    } elseif (-not $completed) {
        $errors.Add('completed must be true') | Out-Null
    }

    $hasFindingsField = Test-ReviewObjectField -Obj $Object -Name 'findings'
    $findings = Get-ReviewObjectField -Obj $Object -Name 'findings'
    if (-not $hasFindingsField) {
        # already reported as missing top-level above
    } elseif ($findings -is [string]) {
        $errors.Add('findings must be an array') | Out-Null
    } else {
        # PS ConvertFrom-Json: [] becomes $null, [single] becomes a single
        # non-array object. Coerce defensively.
        $findingsArr = @()
        if ($null -ne $findings) { $findingsArr = @($findings) }
        $idx = 0
        foreach ($f in $findingsArr) {
            $fp = "findings[$idx]"
            if ($null -eq $f) {
                $errors.Add("$fp is null") | Out-Null
            } else {
                foreach ($k in $Script:ReviewFindingRequired) {
                    if (-not (Test-ReviewObjectField -Obj $f -Name $k)) {
                        $errors.Add("$fp missing field: $k") | Out-Null
                    }
                }
                $sev = Get-ReviewObjectField -Obj $f -Name 'severity'
                if ($null -ne $sev) {
                    if (-not (Test-ReviewIsNonEmptyString -Value $sev)) {
                        $errors.Add("$fp.severity must be a non-empty string") | Out-Null
                    } elseif ($Script:ReviewSeverityAllowed -notcontains $sev) {
                        $errors.Add("$fp.severity must be one of $($Script:ReviewSeverityAllowed -join ',')") | Out-Null
                    }
                }
                foreach ($strField in @('id','title','where','evidence','why_it_matters','recommended_action')) {
                    $v = Get-ReviewObjectField -Obj $f -Name $strField
                    if ($null -ne $v -and -not (Test-ReviewIsNonEmptyString -Value $v)) {
                        $errors.Add("$fp.$strField must be a non-empty string") | Out-Null
                    }
                }
            }
            $idx++
        }
    }

    $metrics = Get-ReviewObjectField -Obj $Object -Name 'metrics'
    if ($null -eq $metrics) {
        $errors.Add('metrics must be an object') | Out-Null
    } else {
        foreach ($k in $Script:ReviewMetricsRequired) {
            if (-not (Test-ReviewObjectField -Obj $metrics -Name $k)) {
                $errors.Add("metrics missing field: $k") | Out-Null
            } else {
                $v = Get-ReviewObjectField -Obj $metrics -Name $k
                if (-not (Test-ReviewIsNonNegativeInteger -Value $v)) {
                    $errors.Add("metrics.$k must be a non-negative integer") | Out-Null
                }
            }
        }
    }

    return [pscustomobject]@{
        ok     = ($errors.Count -eq 0)
        errors = $errors.ToArray()
    }
}

function ConvertFrom-ReviewJsonText {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $Text
    )
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
