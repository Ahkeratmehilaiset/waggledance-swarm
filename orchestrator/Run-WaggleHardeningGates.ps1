#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2A-2 hardening-gate driver. Runs every Phase 2A-1 + Phase 2A-2
    test in deterministic order, prints a concise table, and writes a
    JSON summary to the Phase 2A-2 docs run dir.
.PARAMETER ContinueOnFailure
    If set, continues running the remaining gates after a failure.
    Default: stop on first failure.
.PARAMETER ReportPath
    Output path for the JSON summary. Default:
    docs/runs/orchestrator_phase2a2_review_runner_2026_05_06/hardening_gates.json
#>
[CmdletBinding()]
param(
    [switch] $ContinueOnFailure,
    [string] $ReportPath = ''
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$orchDir  = $PSScriptRoot

if (-not $ReportPath) {
    $ReportPath = Join-Path $repoRoot 'docs/runs/orchestrator_phase2a2_review_runner_2026_05_06/hardening_gates.json'
}
$reportDir = Split-Path -Parent $ReportPath
if (-not (Test-Path -LiteralPath $reportDir)) {
    New-Item -ItemType Directory -Path $reportDir -Force | Out-Null
}

# Ordered list of gates. Gate name -> ps1 path.
# Order matters: cheap structural gates first (Syntax), then
# Phase 2A-1 hardening, then Phase 2A-2 review-runner gates, then
# the Phase 2A-2 integration gate last.
$gates = @(
    @{ name = 'Test-Syntax';         path = Join-Path $orchDir 'Test-Syntax.ps1' }
    @{ name = 'Test-Redaction';      path = Join-Path $orchDir 'Test-Redaction.ps1' }
    @{ name = 'Test-Redactor';       path = Join-Path $orchDir 'Test-Redactor.ps1' }
    @{ name = 'Test-SmokeValidation';path = Join-Path $orchDir 'Test-SmokeValidation.ps1' }
    @{ name = 'Test-ReviewSchema';   path = Join-Path $orchDir 'Test-ReviewSchema.ps1' }
    @{ name = 'Test-ReviewAdapter';  path = Join-Path $orchDir 'Test-ReviewAdapter.ps1' }
    @{ name = 'Test-ReviewRunner';   path = Join-Path $orchDir 'Test-ReviewRunner.ps1' }
    @{ name = 'Test-Phase2A2';       path = Join-Path $orchDir 'Test-Phase2A2.ps1' }
)

$results = New-Object System.Collections.Generic.List[object]
$failed = $false
$startedAt = (Get-Date).ToUniversalTime().ToString('o')

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

# Pad gate name to 25 chars so the table aligns regardless of caller's
# console -- '{0,-25}' is locale-stable.
Write-Host ''
Write-Host ('{0,-25} {1,-6} {2,-9} {3}' -f 'GATE', 'OK', 'SECONDS', 'EXIT') -ForegroundColor Cyan
Write-Host ('{0,-25} {1,-6} {2,-9} {3}' -f ('-' * 25), '------', '-------', '----') -ForegroundColor Cyan
foreach ($r in $results) {
    $line = '{0,-25} {1,-6} {2,-9} {3}' -f $r.gate, $(if ($r.ok) { 'PASS' } else { 'FAIL' }), $r.elapsed_seconds, $r.exit_code
    if ($r.ok) { Write-Host $line -ForegroundColor Green } else { Write-Host $line -ForegroundColor Red }
}

$passedCount = 0
$failedCount = 0
$resultsArr = @()
foreach ($r in $results) {
    if ($r.ok) { $passedCount++ } else { $failedCount++ }
    $resultsArr += $r
}
$summary = [ordered]@{
    started_at_utc       = $startedAt
    finished_at_utc      = (Get-Date).ToUniversalTime().ToString('o')
    continue_on_failure  = [bool]$ContinueOnFailure
    overall_ok           = (-not $failed)
    gates_run            = [int]$resultsArr.Count
    gates_passed         = [int]$passedCount
    gates_failed         = [int]$failedCount
    results              = $resultsArr
}
([pscustomobject]$summary) | ConvertTo-Json -Depth 6 | Set-Content -Path $ReportPath -Encoding UTF8

Write-Host ''
Write-Host ('Report: ' + $ReportPath)
if ($failed) {
    Write-Host 'OVERALL: FAIL' -ForegroundColor Red
    exit 1
} else {
    Write-Host 'OVERALL: PASS' -ForegroundColor Green
    exit 0
}
