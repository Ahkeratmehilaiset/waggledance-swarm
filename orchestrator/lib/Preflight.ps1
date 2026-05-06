# Preflight.ps1
# Phase 1.6: configurable, non-fatal auth check; env-secret warning;
# git check-ignore for sensitive paths; safe-mode aware.

Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'ConfigValidator.ps1')
. (Join-Path $PSScriptRoot 'Lockfile.ps1')
. (Join-Path $PSScriptRoot 'EnvSanitize.ps1')

function _AddCheck { param($accum, [string]$name, [bool]$ok, [string]$detail, [bool]$warning = $false)
    $accum.checks  += [pscustomobject]@{ name = $name; ok = $ok; detail = $detail; warning = $warning }
    if (-not $ok) {
        if ($warning) { $accum.warnings += "${name}: $detail" }
        else          { $accum.errors   += "${name}: $detail" }
    }
}

function Invoke-PreflightChecks {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] $Config,
        [Parameter(Mandatory)] [string] $LockFilePath,
        [string] $ClaudeCommand = ''
    )

    $accum = [pscustomobject]@{ checks = @(); errors = @(); warnings = @() }

    # ---- A: claude in PATH -------------------------------------------
    $cmd = if ($ClaudeCommand) { $ClaudeCommand }
           elseif (($Config.PSObject.Properties.Name -contains 'claudeCommand') -and $Config.claudeCommand) { $Config.claudeCommand }
           else { 'claude' }
    $resolved = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($resolved) { _AddCheck $accum 'claude_in_path' $true "Found at $($resolved.Source)" }
    else           { _AddCheck $accum 'claude_in_path' $false "'$cmd' not found in PATH" }

    # ---- B: claude --version ----------------------------------------
    if ($resolved) {
        try {
            $verOut = & $cmd --version 2>&1 | Out-String
            _AddCheck $accum 'claude_version' $true ($verOut.Trim())
        } catch {
            _AddCheck $accum 'claude_version' $false "claude --version failed: $($_.Exception.Message)" $true
        }
    } else {
        _AddCheck $accum 'claude_version' $false 'skipped: claude not in PATH' $true
    }

    # ---- C: claude auth status (configurable) ----------------------
    $requireAuth = $false
    if ($Config.PSObject.Properties.Name -contains 'requireClaudeAuthStatus') {
        $requireAuth = [bool]$Config.requireClaudeAuthStatus
    }
    if ($resolved) {
        try {
            $authOut = & $cmd auth status 2>&1 | Out-String
            $text = $authOut.Trim()
            $looksLoggedIn = ($text -match '(logged in|authenticated|signed in)') -and -not ($text -match '(not logged in|not authenticated|please log in)')
            _AddCheck $accum 'claude_auth' $looksLoggedIn $text (-not $requireAuth)
        } catch {
            _AddCheck $accum 'claude_auth' $false "claude auth status not available: $($_.Exception.Message)" (-not $requireAuth)
        }
    } else {
        _AddCheck $accum 'claude_auth' $false 'skipped: claude not in PATH' (-not $requireAuth)
    }

    # ---- D: project root ---------------------------------------------
    if (Test-Path $Config.projectRoot) { _AddCheck $accum 'project_root' $true $Config.projectRoot }
    else { _AddCheck $accum 'project_root' $false "Does not exist: $($Config.projectRoot)" }

    # ---- E: git -------------------------------------------------------
    $gitCmd = Get-Command git -ErrorAction SilentlyContinue
    if ($gitCmd) { _AddCheck $accum 'git_available' $true $gitCmd.Source }
    else         { _AddCheck $accum 'git_available' $false 'git not found in PATH' $true }

    # ---- F: raportti.md (warn if missing) -----------------------------
    $reportPath = Join-Path $Config.projectRoot $Config.reportFile
    if (Test-Path $reportPath) { _AddCheck $accum 'report_file' $true $reportPath }
    else { _AddCheck $accum 'report_file' $true "missing (will be created by run): $reportPath" $true }

    # ---- G: config validation ----------------------------------------
    $cfgRes = Test-WaggleConfig -Config $Config
    if ($cfgRes.valid) { _AddCheck $accum 'config_valid' $true 'all fields ok' }
    else { _AddCheck $accum 'config_valid' $false ($cfgRes.errors -join '; ') }
    foreach ($w in $cfgRes.warnings) { _AddCheck $accum 'config_warning' $false $w $true }

    # ---- H: lock free -------------------------------------------------
    $existing = Read-WaggleLock -Path $LockFilePath
    if (-not $existing)                                                  { _AddCheck $accum 'lock_free' $true 'no lock present' }
    elseif (-not (Test-LockHolderAlive -Lock $existing))                 { _AddCheck $accum 'lock_free' $true "stale lock will be reclaimed (pid=$($existing.pid))" $true }
    else { _AddCheck $accum 'lock_free' $false "live lock by pid=$($existing.pid) on $($existing.hostname) (iteration=$($existing.iteration_id))" }

    # ---- I: parent-process secrets warning --------------------------
    $parentSecrets = Get-ParentSecretsPresent
    if ($parentSecrets.Count -gt 0) {
        _AddCheck $accum 'parent_env_secrets' $false ("Parent process has potential secrets: " + ($parentSecrets -join ', ') + ". They will be stripped if sanitizeEnvironment=true.") $true
    } else {
        _AddCheck $accum 'parent_env_secrets' $true 'no obvious secret env vars in parent'
    }

    # ---- J: git check-ignore for sensitive paths --------------------
    if ($gitCmd -and (Test-Path (Join-Path $Config.projectRoot '.git'))) {
        $relTargets = @(
            (Join-Path $Config.transcriptDir 'sample.log'),
            (Join-Path $Config.iterationsDir 'sample/state.json'),
            (Join-Path $Config.stateDir      'current.json'),
            'orchestrator.config.json'
        )
        $missing = @()
        Push-Location $Config.projectRoot
        try {
            foreach ($rel in $relTargets) {
                & git check-ignore --quiet $rel 2>$null
                if ($LASTEXITCODE -ne 0) { $missing += $rel }
            }
        } finally { Pop-Location }
        if ($missing.Count -eq 0) {
            _AddCheck $accum 'gitignore_sensitive' $true 'transcripts/iterations/state/config covered by .gitignore'
        } else {
            _AddCheck $accum 'gitignore_sensitive' $false ("Not gitignored: " + ($missing -join ', ')) $true
        }
    } else {
        _AddCheck $accum 'gitignore_sensitive' $true 'skipped: not a git repo' $true
    }

    return [pscustomobject]@{
        ok       = ($accum.errors.Count -eq 0)
        checks   = $accum.checks
        errors   = $accum.errors
        warnings = $accum.warnings
    }
}

function Write-PreflightSummary {
    [CmdletBinding()]
    param([Parameter(Mandatory)] $Result)
    foreach ($c in $Result.checks) {
        if ($c.ok)            { $tag = 'OK   '; $color = 'Green' }
        elseif ($c.warning)   { $tag = 'WARN '; $color = 'Yellow' }
        else                  { $tag = 'FAIL '; $color = 'Red'   }
        Write-Host ("{0} {1,-22} {2}" -f $tag, $c.name, $c.detail) -ForegroundColor $color
    }
}
