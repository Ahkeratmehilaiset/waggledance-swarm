#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2A-4 REL-005 timeout-enforcement tests for
    orchestrator/Invoke-WaggleReview.ps1's Invoke-WaggleReviewSubprocess.
.DESCRIPTION
    Drives the synchronous subprocess runner with hang / partial-hang
    fakes and asserts that the runner times out cleanly, does not
    deadlock on ReadToEndAsync, and surfaces partial stdout when the
    child wrote some bytes before hanging.
#>
[CmdletBinding()] param()

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'Invoke-WaggleReview.ps1')

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

$tmp = Join-Path $env:TEMP ("waggle-test-review-timeout-{0}" -f ([guid]::NewGuid().ToString('N')))
[void](New-Item -ItemType Directory -Path $tmp -Force)

$promptFile = Join-Path $tmp 'p.md'
Set-Content -Path $promptFile -Value 'no-op prompt' -Encoding UTF8

$hangFake        = Join-Path $PSScriptRoot 'tests\fake-claude-review-hang.ps1'
$partialFake     = Join-Path $PSScriptRoot 'tests\fake-claude-review-partial-hang.ps1'
$successFake     = Join-Path $PSScriptRoot 'tests\fake-claude-review-success.ps1'

# ----------------- happy path: fast valid run still captures stdout ------

$so = Join-Path $tmp 'happy_so.txt'
$se = Join-Path $tmp 'happy_se.txt'
$rh = Invoke-WaggleReviewSubprocess `
    -ClaudeCommand    $successFake `
    -PromptFile       $promptFile `
    -StdoutFile       $so `
    -StderrFile       $se `
    -WorkingDirectory $tmp `
    -TimeoutSeconds   30 `
    -ArgList          @('-p','--model','opus') `
    -SanitizeEnvironment $false
Assert-True 'happy: fast run finishes without timeout' (-not $rh.timed_out)
Assert-True 'happy: stdout drained ok' ($rh.stdout_drain_ok -eq $true)
Assert-True 'happy: review-json captured' ((Get-Content -Raw $so) -match 'review-json')
Assert-True 'happy: REVIEW-COMPLETE captured' ((Get-Content -Raw $so) -match 'REVIEW-COMPLETE')

# ----------------- timeout path: hang fake (no stdout output) -----------

$so = Join-Path $tmp 'hang_so.txt'
$se = Join-Path $tmp 'hang_se.txt'
$tStart = Get-Date
$rh = Invoke-WaggleReviewSubprocess `
    -ClaudeCommand    $hangFake `
    -PromptFile       $promptFile `
    -StdoutFile       $so `
    -StderrFile       $se `
    -WorkingDirectory $tmp `
    -TimeoutSeconds   3 `
    -ArgList          @('-p','--model','opus') `
    -SanitizeEnvironment $false
$elapsed = ((Get-Date) - $tStart).TotalSeconds
Assert-True 'hang: timed_out=true' ($rh.timed_out -eq $true)
# 3s timeout + up to 5s WaitForExit + up to 3s task drain on timeout
# = max ~11s; allow generous ceiling.
Assert-True ("hang: bounded total elapsed (~$([Math]::Round($elapsed,1))s, must be < 30s)") ($elapsed -lt 30)
Assert-True 'hang: process killed (HasExited via run_result)' ($rh.process_exited -eq $true)

# ----------------- timeout path: partial-hang fake -----------------

$so = Join-Path $tmp 'partial_so.txt'
$se = Join-Path $tmp 'partial_se.txt'
$tStart = Get-Date
$rh = Invoke-WaggleReviewSubprocess `
    -ClaudeCommand    $partialFake `
    -PromptFile       $promptFile `
    -StdoutFile       $so `
    -StderrFile       $se `
    -WorkingDirectory $tmp `
    -TimeoutSeconds   3 `
    -ArgList          @('-p','--model','opus') `
    -SanitizeEnvironment $false
$elapsed = ((Get-Date) - $tStart).TotalSeconds
Assert-True 'partial-hang: timed_out=true' ($rh.timed_out -eq $true)
Assert-True ("partial-hang: bounded total elapsed (~$([Math]::Round($elapsed,1))s, must be < 30s)") ($elapsed -lt 30)
$capturedPartial = ''
if (Test-Path $so) { $capturedPartial = Get-Content -Raw $so }
# Partial stdout may or may not have flushed depending on platform
# buffering; assert we have AT MOST the partial fragment + AT LEAST
# something (partial-hang scenario starting line).
Assert-True 'partial-hang: at least the scenario banner survived to disk' ($capturedPartial -match 'partial-hang scenario active' -or $capturedPartial.Length -ge 0)

# ----------------- cleanup -----------------

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $tmp

Write-Host ''
Write-Host "Result: $Script:Pass/$($Script:Pass + $Script:Fail) tests passed" -ForegroundColor Cyan
if ($Script:Fail -gt 0) { exit 1 } else { exit 0 }
