#requires -Version 5.1
<#
.SYNOPSIS
    R20.4 — bootstrap WaggleDance solver runtime in a chosen
    deployment profile (small / medium / large).

.DESCRIPTION
    Reads the solver profile from solver-profiles/<name>.json, sets
    profile-derived environment variables, and emits a "ready" line
    on stdout. Production use does NOT require Claude Code or Codex —
    the solver runtime is started by this script alone.

    Profile S (small): heuristic-only, no internet, no LLM. Asserts
    that the runtime stays offline by setting WAGGLE_ALLOW_INTERNET=0.

    Profile M (medium): cache + local LLM + heuristic fallback. Sets
    WAGGLE_ALLOW_LOCAL_LLM=1, no cloud.

    Profile L (large): full four-tier fallback chain. Cloud allowed
    only if redaction is enabled (default).

.PARAMETER Profile
    small | medium | large. Defaults to $env:WAGGLE_PROFILE if set,
    otherwise small.

.PARAMETER ProfileDir
    Directory containing <profile>.json files. Defaults to repo-root
    solver-profiles/.

.PARAMETER PrintOnly
    Skip launching the runtime; just print the resolved profile and
    env vars. Used by tests so we can inspect bootstrap behavior
    without spawning a long-lived process.

.EXAMPLE
    .\Start-WaggleDanceSolver.ps1 -Profile small -PrintOnly

.EXAMPLE
    $env:WAGGLE_PROFILE = "medium"
    .\Start-WaggleDanceSolver.ps1
#>
[CmdletBinding()]
param(
    [string] $Profile = '',
    [string] $ProfileDir = '',
    [switch] $PrintOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# ── Resolve profile + path ───────────────────────────────────────

if (-not $Profile) {
    $Profile = if ($env:WAGGLE_PROFILE) { $env:WAGGLE_PROFILE } else { 'small' }
}
$validProfiles = @('small','medium','large')
if ($validProfiles -notcontains $Profile) {
    throw "Invalid profile $Profile. Must be one of: $($validProfiles -join ', ')"
}

$repoRoot = $PSScriptRoot
if (-not $ProfileDir) {
    $ProfileDir = Join-Path $repoRoot 'solver-profiles'
}
$profilePath = Join-Path $ProfileDir "$Profile.json"
if (-not (Test-Path -LiteralPath $profilePath -PathType Leaf)) {
    throw "Profile config not found: $profilePath"
}

# ── Load + validate ──────────────────────────────────────────────

$profileObj = Get-Content -Raw -Path $profilePath -Encoding UTF8 | ConvertFrom-Json
if ($profileObj.name -ne $Profile) {
    throw "Profile name mismatch: file says $($profileObj.name), arg says $Profile"
}

$behaviors = $profileObj.behaviors
$bridge    = $profileObj.bridge_llm_client

# ── Set env vars (consumed by the runtime + tests) ──────────────

$env:WAGGLE_PROFILE                  = $Profile
$env:WAGGLE_PROFILE_PATH             = $profilePath
$env:WAGGLE_ALLOW_INTERNET           = if ($behaviors.allow_internet)         { '1' } else { '0' }
$env:WAGGLE_ALLOW_LOCAL_LLM          = if ($behaviors.allow_local_llm)        { '1' } else { '0' }
$env:WAGGLE_ALLOW_CLOUD_LLM          = if ($behaviors.allow_cloud_llm)        { '1' } else { '0' }
$env:WAGGLE_BRIDGE_LLM_ENABLED       = if ($bridge.enabled)                   { '1' } else { '0' }
$env:WAGGLE_BRIDGE_LLM_REDACTION     = if ($bridge.redaction_required)        { '1' } else { '0' }
$env:WAGGLE_FALLBACK_CHAIN           = ($behaviors.fallback_chain -join ',')

# ── Print resolution ─────────────────────────────────────────────

Write-Host "WaggleDance solver bootstrap" -ForegroundColor Cyan
Write-Host "============================="
Write-Host "profile             = $Profile"
Write-Host "profile_path        = $profilePath"
Write-Host "allow_internet      = $($env:WAGGLE_ALLOW_INTERNET)"
Write-Host "allow_local_llm     = $($env:WAGGLE_ALLOW_LOCAL_LLM)"
Write-Host "allow_cloud_llm     = $($env:WAGGLE_ALLOW_CLOUD_LLM)"
Write-Host "bridge_llm_enabled  = $($env:WAGGLE_BRIDGE_LLM_ENABLED)"
Write-Host "bridge_llm_redaction= $($env:WAGGLE_BRIDGE_LLM_REDACTION)"
Write-Host "fallback_chain      = $($env:WAGGLE_FALLBACK_CHAIN)"
Write-Host "READY"

if ($PrintOnly) { return }

# ── Launch runtime (Stage 1: deferred to follow-up) ──────────────
#
# In Stage 1 the bootstrap script's job is to make the profile
# choice operator-visible and set the env vars. Wiring the runtime
# entry point is an R20.4 follow-up once R20.2 BridgeLLMClient lands
# and the runtime knows how to consume WAGGLE_BRIDGE_LLM_ENABLED.
#
# Until then, treat -PrintOnly as the supported mode. Operators
# wanting to launch the actual runtime should `python -m waggledance ...`
# after the env vars are set.

Write-Host ""
Write-Host "Stage 1 bootstrap: env vars are set; launch the runtime" `
    -ForegroundColor Yellow
Write-Host "manually until R20.2 BridgeLLMClient lands. Re-run with " `
    -ForegroundColor Yellow
Write-Host "-PrintOnly for unit tests of the profile selection path." `
    -ForegroundColor Yellow
