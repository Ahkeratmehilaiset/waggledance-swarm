#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2A-5 ARCH-006 tests for orchestrator/Run-WaggleHardeningGates.ps1
    default ReportPath behavior.

    These tests use the driver's `-SelfTest` mode (Phase 2A-5
    addition) so they do not spend ~30s running every gate. -SelfTest
    emits the resolved ReportPath / latest.json paths as JSON and
    exits without running any gate.

    A separate full-gate dry-run is exercised by the rest of the
    hardening-gate driver itself.
#>
[CmdletBinding()] param()

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$driver = Join-Path $PSScriptRoot 'Run-WaggleHardeningGates.ps1'

$Script:Pass = 0
$Script:Fail = 0

function Assert-True {
    param([string] $Name, [bool] $Cond, [string] $Detail = '')
    if ($Cond) {
        Write-Host "PASS  $Name" -ForegroundColor Green
        $Script:Pass++
    } else {
        Write-Host "FAIL  $Name $Detail" -ForegroundColor Red
        $Script:Fail++
    }
}

# ----------------- 1. driver exists + parses -----------------

Assert-True 'driver exists' (Test-Path -LiteralPath $driver)
$err = $null; $tk = $null
[void][System.Management.Automation.Language.Parser]::ParseFile($driver, [ref]$tk, [ref]$err)
Assert-True 'driver parses cleanly' (($null -eq $err) -or ($err.Count -eq 0))

# ----------------- 2. driver source contains the canonical "default ReportPath" anchor + ARCH-006 tag -----------------

$src = Get-Content -Raw -Path $driver -Encoding UTF8
Assert-True 'driver has the canonical "default ReportPath" anchor text' ($src -match 'default ReportPath')
Assert-True 'driver has Phase 2A-5 ARCH-006 anchor' ($src -match 'Phase 2A-5 ARCH-006')
# Find any LIVE assignment to $ReportPath and confirm it does NOT
# reference the old phase-2a2 default path. Doc-comment / explanation
# references inside <# .. #> blocks are OK (kept for historical
# context).
$assignmentHits = [regex]::Matches($src, '(?m)^\s*\$ReportPath\s*=\s*(?<rhs>.+)$')
$badAssign = $false
foreach ($m in $assignmentHits) {
    $rhs = [string]$m.Groups['rhs'].Value
    if ($rhs -match 'orchestrator_phase2a2_review_runner_2026_05_06') {
        $badAssign = $true; break
    }
}
Assert-True 'no live `$ReportPath = ...` assignment references the old Phase 2A-2 default path' (-not $badAssign)

# ----------------- 3. -SelfTest default-path resolution -----------------

$selfTestRaw = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $driver -SelfTest 2>&1
Assert-True '-SelfTest exits 0 (no gates run)' ($LASTEXITCODE -eq 0)

$st = $null
try { $st = ($selfTestRaw -join "`n") | ConvertFrom-Json } catch { $st = $null }
Assert-True '-SelfTest stdout is JSON' ($null -ne $st)

if ($null -ne $st) {
    Assert-True '-SelfTest reports self_test=true' ($st.self_test -eq $true)
    Assert-True '-SelfTest report_path is under docs/runs/hardening_gates/' (
        $st.report_path -match 'docs[\\/]runs[\\/]hardening_gates[\\/]'
    )
    Assert-True '-SelfTest report_path filename is UTC-timestamp shaped (YYYY-MM-DDTHH-MM-SSZ.json)' (
        $st.report_path -match '\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z\.json$'
    )
    Assert-True '-SelfTest report_path is filesystem-safe (no colons in basename)' (
        ((Split-Path -Leaf $st.report_path) -notmatch ':')
    )
    Assert-True '-SelfTest latest_report_path ends with latest.json' (
        $st.latest_report_path -match 'latest\.json$'
    )
    Assert-True '-SelfTest latest_report_path is under docs/runs/hardening_gates/' (
        $st.latest_report_path -match 'docs[\\/]runs[\\/]hardening_gates[\\/]'
    )
    Assert-True '-SelfTest reports default path is phase-agnostic' (
        $st.default_path_is_phase_agnostic -eq $true
    )
    Assert-True '-SelfTest report_path is NOT in the old phase-2a2 dir' (
        $st.report_path -notmatch 'orchestrator_phase2a2_review_runner_2026_05_06'
    )
}

# ----------------- 4. -ReportPath override is honored -----------------

$tmp = Join-Path $env:TEMP ("waggle-gates-override-{0}" -f ([guid]::NewGuid().ToString('N')))
[void](New-Item -ItemType Directory -Path $tmp -Force)
$override = Join-Path $tmp 'custom_report.json'
$selfTestOverride = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $driver -SelfTest -ReportPath $override 2>&1
$st2 = $null
try { $st2 = ($selfTestOverride -join "`n") | ConvertFrom-Json } catch {}
if ($null -ne $st2) {
    Assert-True '-ReportPath override honored exactly' ($st2.report_path -eq $override)
    Assert-True '-ReportPath override still produces a latest.json shortcut' (
        $st2.latest_report_path -match 'latest\.json$'
    )
}

# ----------------- 5. hardening_gates dir + .gitignore + README -----------------

$gatesDir = Join-Path $repoRoot 'docs/runs/hardening_gates'
Assert-True 'hardening_gates dir exists' (Test-Path -LiteralPath $gatesDir)
Assert-True 'hardening_gates README exists' (Test-Path -LiteralPath (Join-Path $gatesDir 'README.md'))
Assert-True 'hardening_gates .gitignore exists' (Test-Path -LiteralPath (Join-Path $gatesDir '.gitignore'))

if (Test-Path -LiteralPath (Join-Path $gatesDir '.gitignore')) {
    $gi = Get-Content -Raw -Path (Join-Path $gatesDir '.gitignore') -Encoding UTF8
    Assert-True 'hardening_gates/.gitignore ignores *.json' ($gi -match '(?m)^\*\.json\s*$')
    Assert-True 'hardening_gates/.gitignore exempts README.md' ($gi -match '(?m)^!README\.md\s*$')
    Assert-True 'hardening_gates/.gitignore exempts .gitignore' ($gi -match '(?m)^!\.gitignore\s*$')
}

# ----------------- 6. generated JSON reports are gitignored -----------------

# Use git check-ignore to verify a hypothetical generated report
# is ignored. Falling back to .gitignore parse if git is missing.
$gitCheck = $null
try {
    $gitCheck = & git -C $repoRoot check-ignore -v 'docs/runs/hardening_gates/2026-05-07T00-30-15Z.json' 2>&1
} catch {}
$gitIgnoresOk = ($gitCheck -and ($gitCheck -match 'hardening_gates'))
if (-not $gitIgnoresOk) {
    # Fallback: look at the .gitignore directly.
    $gi = Get-Content -Raw -Path (Join-Path $gatesDir '.gitignore') -Encoding UTF8
    $gitIgnoresOk = ($gi -match '(?m)^\*\.json\s*$')
}
Assert-True 'generated hardening-gate JSON reports are gitignored' $gitIgnoresOk

# Also assert latest.json is ignored (we treat it as a runtime
# shortcut, NOT a committed file).
$gitCheck2 = $null
try {
    $gitCheck2 = & git -C $repoRoot check-ignore -v 'docs/runs/hardening_gates/latest.json' 2>&1
} catch {}
$latestIgnored = ($gitCheck2 -and ($gitCheck2 -match 'hardening_gates'))
if (-not $latestIgnored) {
    $gi = Get-Content -Raw -Path (Join-Path $gatesDir '.gitignore') -Encoding UTF8
    $latestIgnored = ($gi -match '(?m)^\*\.json\s*$' -and $gi -notmatch '(?m)^!latest\.json\s*$')
}
Assert-True 'latest.json shortcut is also gitignored (local-only)' $latestIgnored

# ----------------- 7. cleanup -----------------

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $tmp

Write-Host ''
Write-Host "Result: $Script:Pass/$($Script:Pass + $Script:Fail) tests passed" -ForegroundColor Cyan
if ($Script:Fail -gt 0) { exit 1 } else { exit 0 }
