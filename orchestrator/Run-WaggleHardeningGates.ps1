#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2A-2 hardening-gate driver. Runs every Phase 2A-1 + Phase
    2A-2 + Phase 2A-3 + Phase 2A-4 + Phase 2A-5 test in deterministic
    order, prints a concise table, and writes a JSON summary to a
    phase-agnostic location.

.PARAMETER ContinueOnFailure
    If set, continues running the remaining gates after a failure.
    Default: stop on first failure.

.PARAMETER ReportPath
    Output path for the JSON summary.

    Phase 2A-5 ARCH-006: default ReportPath is now phase-agnostic --
    `docs/runs/hardening_gates/<utc_timestamp>.json` -- with a
    sibling `latest.json` shortcut. The previous default
    (`docs/runs/orchestrator_phase2a2_review_runner_2026_05_06/hardening_gates.json`)
    was wrong for any phase after 2A-2; runtime gate reports are
    now treated as runtime artifacts, gitignored under
    `docs/runs/hardening_gates/`.

    Both `latest.json` and the timestamped reports are local-only
    runtime artifacts -- they are NOT committed. Final phase reports
    can quote summaries from them, but the raw JSON does not need
    to land in main.

.PARAMETER SelfTest
    Phase 2A-5: skip running gates and just emit the resolved
    ReportPath / latest.json path on stdout (so
    Test-HardeningGatesReportPath can verify path-generation
    behavior without spending 30s on real gate runs).
#>
[CmdletBinding()]
param(
    [switch] $ContinueOnFailure,
    [string] $ReportPath = '',
    [switch] $SelfTest
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$orchDir  = $PSScriptRoot

# --------------------------------------------------------------
# Phase 2A-5 ARCH-006: phase-agnostic default ReportPath.
# Previously hardcoded to a phase-2A-2-specific docs run dir; now
# writes to docs/runs/hardening_gates/<utc>.json. The UTC timestamp
# is filesystem-safe (no `:` characters): YYYY-MM-DDTHH-MM-SSZ.
#
# `latest.json` is a Copy-Item of the most recent report so callers
# can find "the last gate run" without scanning the directory.
# Symlinks are avoided because Windows symlinks may need elevated
# permissions.
# --------------------------------------------------------------
function Get-WaggleHardeningGatesDefaultReportPath {
    param([Parameter(Mandatory)] [string] $RepoRoot)
    $utc = (Get-Date).ToUniversalTime()
    $ts  = $utc.ToString('yyyy-MM-ddTHH-mm-ssZ')
    return (Join-Path (Join-Path $RepoRoot 'docs/runs/hardening_gates') ($ts + '.json'))
}
function Get-WaggleHardeningGatesLatestPath {
    param([Parameter(Mandatory)] [string] $RepoRoot)
    return (Join-Path (Join-Path $RepoRoot 'docs/runs/hardening_gates') 'latest.json')
}

if (-not $ReportPath) {
    $ReportPath = Get-WaggleHardeningGatesDefaultReportPath -RepoRoot $repoRoot
}
$reportDir = Split-Path -Parent $ReportPath
if (-not (Test-Path -LiteralPath $reportDir)) {
    New-Item -ItemType Directory -Path $reportDir -Force | Out-Null
}
$latestPath = Get-WaggleHardeningGatesLatestPath -RepoRoot $repoRoot

if ($SelfTest) {
    # Phase 2A-5 self-test mode: report the resolved paths and exit.
    # Emit a tiny JSON to stdout so callers can parse without running
    # the real gates.
    $info = [ordered]@{
        self_test                = $true
        report_path              = $ReportPath
        latest_report_path       = $latestPath
        report_dir               = $reportDir
        repo_root                = $repoRoot
        default_path_is_phase_agnostic = ($ReportPath -notmatch 'orchestrator_phase2a2_review_runner_2026_05_06')
    }
    ([pscustomobject]$info) | ConvertTo-Json -Depth 4
    exit 0
}

# --------------------------------------------------------------
# Gate list. Order matters: cheap structural gates first (Syntax),
# then Phase 2A-1 hardening, then Phase 2A-2 review-runner gates,
# then Phase 2A-3/4 review surface + safety + integrity gates,
# then Phase 2A-5 ledger + report-path gates, then the Phase 2A-2
# integration gate last.
# --------------------------------------------------------------
$gates = @(
    @{ name = 'Test-Syntax';                  path = Join-Path $orchDir 'Test-Syntax.ps1' }
    @{ name = 'Test-Redaction';               path = Join-Path $orchDir 'Test-Redaction.ps1' }
    @{ name = 'Test-Redactor';                path = Join-Path $orchDir 'Test-Redactor.ps1' }
    @{ name = 'Test-SmokeValidation';         path = Join-Path $orchDir 'Test-SmokeValidation.ps1' }
    @{ name = 'Test-ArtifactValidator';       path = Join-Path $orchDir 'Test-ArtifactValidator.ps1' }
    @{ name = 'Test-Lockfile';                path = Join-Path $orchDir 'Test-Lockfile.ps1' }
    @{ name = 'Test-CompletionVerifier';      path = Join-Path $orchDir 'Test-CompletionVerifier.ps1' }
    @{ name = 'Test-ReviewSchema';            path = Join-Path $orchDir 'Test-ReviewSchema.ps1' }
    @{ name = 'Test-ReviewAdapter';           path = Join-Path $orchDir 'Test-ReviewAdapter.ps1' }
    @{ name = 'Test-ReviewRunner';            path = Join-Path $orchDir 'Test-ReviewRunner.ps1' }
    @{ name = 'Test-ReviewSafety';            path = Join-Path $orchDir 'Test-ReviewSafety.ps1' }
    @{ name = 'Test-ReviewSurface';           path = Join-Path $orchDir 'Test-ReviewSurface.ps1' }
    @{ name = 'Test-ReviewIntegrity';         path = Join-Path $orchDir 'Test-ReviewIntegrity.ps1' }
    @{ name = 'Test-ReviewSubprocessTimeout'; path = Join-Path $orchDir 'Test-ReviewSubprocessTimeout.ps1' }
    @{ name = 'Test-PhaseFixLedger';          path = Join-Path $orchDir 'Test-PhaseFixLedger.ps1' }
    @{ name = 'Test-HardeningGatesReportPath';path = Join-Path $orchDir 'Test-HardeningGatesReportPath.ps1' }
    @{ name = 'Test-Phase2A2';                path = Join-Path $orchDir 'Test-Phase2A2.ps1' }
)

$results = New-Object System.Collections.Generic.List[object]
$failed = $false
$startedAtUtc = (Get-Date).ToUniversalTime()
$startedAt = $startedAtUtc.ToString('o')

foreach ($g in $gates) {
    $name = [string]$g['name']
    $path = [string]$g['path']
    if (-not (Test-Path -LiteralPath $path)) {
        $results.Add([pscustomobject]@{
            gate = $name; path = $path; ok = $false
            exit_code = -1; elapsed_seconds = 0; error = 'gate file not found'
        }) | Out-Null
        $failed = $true
        Write-Host "MISS  $name (file not found: $path)" -ForegroundColor Red
        if (-not $ContinueOnFailure) { break }
        continue
    }
    Write-Host ('---- running ' + $name + ' ----') -ForegroundColor Cyan
    $tStart = Get-Date
    $ok = $true
    $exitCode = 0
    $errMsg = ''
    try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $path
        $exitCode = $LASTEXITCODE
        if ($null -eq $exitCode) { $exitCode = 0 }
        if ($exitCode -ne 0) { $ok = $false; $errMsg = "exit $exitCode" }
    } catch {
        $ok = $false
        $errMsg = $_.Exception.Message
        $exitCode = -1
    }
    $elapsed = [Math]::Round(((Get-Date) - $tStart).TotalSeconds, 2)
    $results.Add([pscustomobject]@{
        gate = $name; path = $path; ok = $ok
        exit_code = $exitCode; elapsed_seconds = $elapsed; error = $errMsg
    }) | Out-Null
    if (-not $ok) {
        $failed = $true
        if (-not $ContinueOnFailure) { break }
    }
}

# Pad gate name to 28 chars so longer Phase 2A-5 gate names align.
Write-Host ''
Write-Host ('{0,-30} {1,-6} {2,-9} {3}' -f 'GATE', 'OK', 'SECONDS', 'EXIT') -ForegroundColor Cyan
Write-Host ('{0,-30} {1,-6} {2,-9} {3}' -f ('-' * 30), '------', '-------', '----') -ForegroundColor Cyan
foreach ($r in $results) {
    $line = '{0,-30} {1,-6} {2,-9} {3}' -f $r.gate, $(if ($r.ok) { 'PASS' } else { 'FAIL' }), $r.elapsed_seconds, $r.exit_code
    if ($r.ok) { Write-Host $line -ForegroundColor Green } else { Write-Host $line -ForegroundColor Red }
}

$passedCount = 0
$failedCount = 0
$resultsArr = @()
foreach ($r in $results) {
    if ($r.ok) { $passedCount++ } else { $failedCount++ }
    $resultsArr += $r
}

# Phase 2A-5 P3 / P4: rich JSON metadata.
$gitBranch = ''
$gitHead   = ''
$gitDirty  = $false
try {
    $gitBranch = (& git -C $repoRoot rev-parse --abbrev-ref HEAD 2>$null).Trim()
    $gitHead   = (& git -C $repoRoot rev-parse HEAD 2>$null).Trim()
    $gitStatus = (& git -C $repoRoot status --porcelain 2>$null)
    $gitDirty  = (-not [string]::IsNullOrEmpty($gitStatus))
} catch {}

$psVersion = ''
try { $psVersion = $PSVersionTable.PSVersion.ToString() } catch {}
$osDescription = ''
try {
    if ($IsWindows -or $env:OS -eq 'Windows_NT') {
        $osDescription = ('Windows / ' + [System.Environment]::OSVersion.VersionString)
    } else {
        $osDescription = [System.Environment]::OSVersion.VersionString
    }
} catch {}

$summary = [ordered]@{
    report_format_version = 1
    report_path           = $ReportPath
    latest_report_path    = $latestPath
    started_at_utc        = $startedAt
    finished_at_utc       = (Get-Date).ToUniversalTime().ToString('o')
    continue_on_failure   = [bool]$ContinueOnFailure
    overall_ok            = (-not $failed)
    gates_run             = [int]$resultsArr.Count
    gates_passed          = [int]$passedCount
    gates_failed          = [int]$failedCount
    git_branch            = $gitBranch
    git_head_sha          = $gitHead
    git_is_dirty          = [bool]$gitDirty
    powershell_version    = $psVersion
    os                    = $osDescription
    results               = $resultsArr
}
$json = ([pscustomobject]$summary) | ConvertTo-Json -Depth 8
Set-Content -Path $ReportPath -Value $json -Encoding UTF8

# Update latest.json (Copy-Item, not symlink).
try {
    Copy-Item -LiteralPath $ReportPath -Destination $latestPath -Force
} catch {
    Write-Warning ("could not update latest.json: " + $_.Exception.Message)
}

Write-Host ''
Write-Host ('Report : ' + $ReportPath)
Write-Host ('Latest : ' + $latestPath)
if ($failed) {
    Write-Host 'OVERALL: FAIL' -ForegroundColor Red
    exit 1
} else {
    Write-Host 'OVERALL: PASS' -ForegroundColor Green
    exit 0
}
