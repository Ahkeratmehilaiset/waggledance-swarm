# ConfigValidator.ps1
# Validates orchestrator.config.json. Returns { valid, errors, warnings }.
# Caller decides whether to throw.
#
# Compatible with PowerShell 5.1 (no null-conditional operator).

Set-StrictMode -Version Latest

function Test-IsValidRegex {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [string] $Pattern)
    try { [void][regex]::new($Pattern); return $true }
    catch { return $false }
}

function _GetTypeName {
    param($v)
    if ($null -eq $v) { return 'null' }
    return $v.GetType().Name
}

function _Has {
    param($obj, [string]$prop)
    if ($null -eq $obj) { return $false }
    if ($obj -is [System.Collections.IDictionary]) { return $obj.Contains($prop) }
    if ($null -eq $obj.PSObject) { return $false }
    return ($obj.PSObject.Properties.Name -contains $prop)
}

function Test-WaggleConfig {
    [CmdletBinding()]
    param([Parameter(Mandatory)] $Config)

    $errors   = @()
    $warnings = @()

    # ---- required string fields --------------------------------------------
    $requiredStrings = @('projectRoot', 'transcriptDir', 'iterationsDir', 'stateDir', 'reportFile')
    foreach ($f in $requiredStrings) {
        if (-not (_Has $Config $f)) {
            $errors += "Missing required field: $f"; continue
        }
        if ([string]::IsNullOrWhiteSpace($Config.$f)) {
            $errors += "Field '$f' is empty"
        }
    }

    # ---- projectRoot must exist --------------------------------------------
    if ((_Has $Config 'projectRoot') -and -not [string]::IsNullOrWhiteSpace($Config.projectRoot)) {
        if (-not (Test-Path $Config.projectRoot)) {
            $errors += "projectRoot does not exist: $($Config.projectRoot)"
        }
    }

    # ---- numeric ranges ----------------------------------------------------
    $numericFields = @{
        tailLineCount          = @{ min = 100;  max = 1000000 }
        pollIntervalSeconds    = @{ min = 1;    max = 600     }
        stableThresholdSeconds = @{ min = 5;    max = 3600    }
        runTimeoutMinutes      = @{ min = 1;    max = 1440    }
        runnerPollSeconds      = @{ min = 1;    max = 60      }
        fullTranscriptMaxBytes = @{ min = 1024; max = (1GB)   }
        llmPackageMaxChars     = @{ min = 1000; max = 5000000 }
        perSectionMaxChars     = @{ min = 500;  max = 2000000 }
        maxTurns               = @{ min = 1;    max = 1000    }
    }
    foreach ($k in $numericFields.Keys) {
        if (-not (_Has $Config $k)) { continue }
        $v = $Config.$k
        if ($null -eq $v -or -not ($v -is [int] -or $v -is [long] -or $v -is [double])) {
            $errors += "Field '$k' must be numeric, got: $(_GetTypeName $v)"
            continue
        }
        $rule = $numericFields[$k]
        if ($v -lt $rule.min -or $v -gt $rule.max) {
            $errors += "Field '$k' = $v out of range [$($rule.min)..$($rule.max)]"
        }
    }

    # ---- regex arrays ------------------------------------------------------
    $regexArrayFields = @('interactivePromptPatterns', 'completedPromptPatterns', 'envDenylist')
    foreach ($f in $regexArrayFields) {
        if (-not (_Has $Config $f)) { continue }
        $arr = $Config.$f
        if ($null -eq $arr) { continue }
        foreach ($p in $arr) {
            if (-not (Test-IsValidRegex -Pattern $p)) {
                $errors += "Invalid regex in '$f': $p"
            }
        }
    }

    # ---- enums --------------------------------------------------------
    if (_Has $Config 'executionMode') {
        $allowed = @('print', 'interactiveTranscriptFallback')
        if ($allowed -notcontains $Config.executionMode) {
            $errors += "executionMode must be one of: $($allowed -join ', '); got '$($Config.executionMode)'"
        }
    }
    if (_Has $Config 'outputFormat') {
        $allowed = @('text', 'json', 'stream-json')
        if ($allowed -notcontains $Config.outputFormat) {
            $errors += "outputFormat must be one of: $($allowed -join ', '); got '$($Config.outputFormat)'"
        }
    }
    if (_Has $Config 'permissionMode') {
        $allowed = @('default', 'acceptEdits', 'plan', 'bypassPermissions')
        if ($allowed -notcontains $Config.permissionMode) {
            $errors += "permissionMode must be one of: $($allowed -join ', '); got '$($Config.permissionMode)'"
        }
    }

    # ---- danger flag warning ---------------------------------------------
    if ((_Has $Config 'dangerouslySkipPermissions') -and $Config.dangerouslySkipPermissions) {
        $warnings += 'dangerouslySkipPermissions is ENABLED. Claude Code will skip permission prompts; only use in isolated, sandboxed environments.'
    }

    # ---- safe-mode interactions ---------------------------------------
    $safeMode = $true
    if (_Has $Config 'safeMode') { $safeMode = [bool]$Config.safeMode }
    $allowBash = $false
    if (_Has $Config 'allowBash') { $allowBash = [bool]$Config.allowBash }
    if ($safeMode -and $allowBash) {
        $warnings += 'safeMode=true but allowBash=true. Bash will be granted; safeMode otherwise restricts tools. Confirm this is intentional.'
    }
    if ($allowBash) {
        $warnings += 'allowBash=true. Use only in an isolated worktree without secrets in the environment.'
    }

    # ---- LLM package limits -----------------------------------------
    if (_Has $Config 'llmPackageMaxChars') {
        $v = $Config.llmPackageMaxChars
        if ($v -lt 1000 -or $v -gt 5000000) {
            $errors += "llmPackageMaxChars out of range [1000..5,000,000]: $v"
        }
    }

    return [pscustomobject]@{
        valid    = ($errors.Count -eq 0)
        errors   = $errors
        warnings = $warnings
    }
}

function Assert-WaggleConfig {
    [CmdletBinding()]
    param([Parameter(Mandatory)] $Config)
    $r = Test-WaggleConfig -Config $Config
    foreach ($w in $r.warnings) { Write-Warning $w }
    if (-not $r.valid) {
        $msg = "Config validation failed:`n  - " + ($r.errors -join "`n  - ")
        throw $msg
    }
    return $r
}
