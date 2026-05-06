#requires -Version 5.1
<#
.SYNOPSIS
    Real subprocess integration test using tests/fake-claude.ps1 as the
    "claude" binary. Validates each scenario end-to-end through
    Invoke-ClaudeCodePrint + Resolve-PrintModeVerdict.
#>
[CmdletBinding()] param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$libDir = Join-Path $PSScriptRoot 'lib'
. (Join-Path $libDir 'ClaudeRunner.ps1')
. (Join-Path $libDir 'Signals.ps1')
. (Join-Path $libDir 'CompletionVerifier.ps1')
. (Join-Path $libDir 'ArtifactValidator.ps1')
. (Join-Path $libDir 'Packager.ps1')
. (Join-Path $libDir 'Redactor.ps1')

$tmpRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("waggle-runner-" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmpRoot -Force | Out-Null

# Build a wrapper that forwards stdin and args to the fake-claude.ps1 script
# but presents itself as a normal executable.
$pwshExe = (Get-Command pwsh -ErrorAction SilentlyContinue)
if (-not $pwshExe) { $pwshExe = (Get-Command powershell -ErrorAction SilentlyContinue) }
if (-not $pwshExe) { throw 'No pwsh/powershell available for the test' }

$fakeScript = Join-Path $PSScriptRoot 'tests/fake-claude.ps1'
$wrapperPath = Join-Path $tmpRoot 'fake-claude.cmd'
@"
@echo off
"$($pwshExe.Source)" -NoProfile -File "$fakeScript" %*
"@ | Set-Content -Path $wrapperPath -Encoding ASCII

# Sanity: a "real" exe-like wrapper. On Windows .cmd resolves via Get-Command.
$fakeCmd = Get-Command $wrapperPath -ErrorAction SilentlyContinue
if (-not $fakeCmd) { throw "fake-claude wrapper not resolvable: $wrapperPath" }

$script:tests = 0; $script:passes = 0; $script:fails = @()
function Pass($n) { $script:tests++; $script:passes++; Write-Host "PASS  $n" -ForegroundColor Green }
function Fail($n, $detail) { $script:tests++; Write-Host "FAIL  $n : $detail" -ForegroundColor Red; $script:fails += $n }

function New-PromptFile([string]$txt) {
    $p = Join-Path $tmpRoot ([Guid]::NewGuid().ToString('N') + '.md')
    Set-Content -Path $p -Value $txt -Encoding UTF8
    return $p
}

function Run-Scenario([string]$scenario, [string]$iterId, [int]$timeout = 30) {
    $iterFolder = Join-Path $tmpRoot $iterId
    New-Item -ItemType Directory -Path $iterFolder -Force | Out-Null
    $sigDir = Join-Path $iterFolder 'signals'
    New-Item -ItemType Directory -Path $sigDir -Force | Out-Null

    $env:WAGGLE_FAKE_SCENARIO   = $scenario
    $env:WAGGLE_FAKE_ITERATION  = $iterId
    $env:WAGGLE_FAKE_SIGNAL_DIR = $sigDir

    try {
        $promptFile = New-PromptFile ("Scenario: $scenario`nIteration: $iterId")
        $stdoutFile = Join-Path $iterFolder 'claude_stdout.txt'
        $stderrFile = Join-Path $iterFolder 'claude_stderr.txt'

        $result = Invoke-ClaudeCodePrint `
            -ClaudeCommand    $wrapperPath `
            -PromptFile       $promptFile `
            -StdoutFile       $stdoutFile `
            -StderrFile       $stderrFile `
            -WorkingDirectory $tmpRoot `
            -TimeoutSeconds   $timeout `
            -ArgList          @() `
            -InteractivePromptPatterns @('Do you want to proceed', '\[y/N\]') `
            -PollIntervalSeconds       1 `
            -SanitizeEnvironment       $false `
            -KillOnInteractivePrompt   $true

        return @{ result = $result; folder = $iterFolder }
    }
    finally {
        Remove-Item Env:WAGGLE_FAKE_SCENARIO   -ErrorAction SilentlyContinue
        Remove-Item Env:WAGGLE_FAKE_ITERATION  -ErrorAction SilentlyContinue
        Remove-Item Env:WAGGLE_FAKE_SIGNAL_DIR -ErrorAction SilentlyContinue
    }
}

function Get-FinalStatus($r, $iterId) {
    return (Resolve-PrintModeVerdict `
        -RunnerResult     $r.result `
        -IterationFolder  $r.folder `
        -IterationId      $iterId `
        -ExitMarker       '##WAGGLE_RUN_COMPLETE##' `
        -RequireExitMarker $false `
        -RequireReport    $false).status
}

try {
    # 1) success: completion signal + exit 0 + valid signal -> COMPLETED
    $r = Run-Scenario 'success' 'iter-success' 15
    # need state.json + run_metadata + package for ArtifactValidator
    Set-Content -Path (Join-Path $r.folder 'state.json')         -Value '{"x":1}'
    Set-Content -Path (Join-Path $r.folder 'run_metadata.json')  -Value '{"x":1}'
    Set-Content -Path (Join-Path $r.folder 'llm_input_package.md') -Value "UNTRUSTED DATA: marker present"
    Set-Content -Path (Join-Path $r.folder 'redaction_report.json') -Value '{"counts":{}}'
    $s = Get-FinalStatus $r 'iter-success'
    if ($s -eq 'COMPLETED') { Pass 'success scenario -> COMPLETED' } else { Fail 'success scenario' "got $s" }

    # 2) no_signal: exit 0 + no completion signal -> COMPLETED_UNVERIFIED
    $r = Run-Scenario 'no_signal' 'iter-nosig' 15
    $s = Get-FinalStatus $r 'iter-nosig'
    if ($s -eq 'COMPLETED_UNVERIFIED') { Pass 'no_signal -> COMPLETED_UNVERIFIED' } else { Fail 'no_signal' "got $s" }

    # 3) fail: failure signal + exit 1 -> FAILED
    $r = Run-Scenario 'fail' 'iter-fail' 15
    $s = Get-FinalStatus $r 'iter-fail'
    if ($s -eq 'FAILED') { Pass 'fail scenario -> FAILED' } else { Fail 'fail scenario' "got $s" }

    # 4) needs_action: prompt printed, runner kills -> NEEDS_MANUAL_ACTION
    $r = Run-Scenario 'needs_action' 'iter-action' 15
    $s = Get-FinalStatus $r 'iter-action'
    if ($s -eq 'NEEDS_MANUAL_ACTION') { Pass 'needs_action -> NEEDS_MANUAL_ACTION' } else { Fail 'needs_action' "got $s" }

    # 5) timeout: very short timeout vs. fake timeout scenario
    $r = Run-Scenario 'timeout' 'iter-timeout' 5
    $s = Get-FinalStatus $r 'iter-timeout'
    if ($s -eq 'TIMEOUT') { Pass 'timeout -> TIMEOUT' } else { Fail 'timeout' "got $s" }
}
finally {
    Remove-Item -Path $tmpRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host ("Result: {0}/{1} tests passed" -f $script:passes, $script:tests) -ForegroundColor Cyan
if ($script:fails.Count -gt 0) { exit 1 }
exit 0
