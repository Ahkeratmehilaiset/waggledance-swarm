#requires -Version 5.1
<#
.SYNOPSIS
    Integration tests using synthetic transcripts and the fake-claude binary.
    Verifies the detector + collector + signals end-to-end without invoking
    the real Claude Code CLI.

    These tests do NOT spawn fake-claude as a subprocess (Start-Process for
    .ps1 files requires extra wiring). Instead they exercise the verdict and
    artifact paths with prepared inputs.
#>
[CmdletBinding()] param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$libDir = Join-Path $PSScriptRoot 'lib'
. (Join-Path $libDir 'State.ps1')
. (Join-Path $libDir 'Checkpoint.ps1')
. (Join-Path $libDir 'Detector.ps1')
. (Join-Path $libDir 'Collector.ps1')
. (Join-Path $libDir 'Signals.ps1')
. (Join-Path $libDir 'Packager.ps1')
. (Join-Path $libDir 'Redactor.ps1')

$tmpRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("waggle-int-" + [Guid]::NewGuid().ToString('N'))
$projectRoot = Join-Path $tmpRoot 'project'
$iterRoot   = Join-Path $projectRoot 'iterations'
$transcriptDir = Join-Path $projectRoot 'transcripts'
foreach ($d in @($projectRoot, $iterRoot, $transcriptDir)) {
    New-Item -ItemType Directory -Path $d -Force | Out-Null
}

$script:tests = 0; $script:passes = 0; $script:fails = @()
function Run([string]$name, [scriptblock]$body) {
    $script:tests++
    try { & $body; $script:passes++; Write-Host "PASS  $name" -ForegroundColor Green }
    catch { Write-Host "FAIL  $name : $($_.Exception.Message)" -ForegroundColor Red; $script:fails += $name }
}

try {
    # ---- Scenario 1: success path with completion signal -----------------
    Run 'Scenario: success with completion signal' {
        $iter = '2026-05-06_10-00-00'
        $folder = New-IterationFolder -IterationsRoot $iterRoot -IterationId $iter
        $sigDir = Initialize-SignalsDir -IterationFolder $folder

        # Drop a completion signal as if Claude wrote it
        @{ iteration_id = $iter; completed_at = (Get-Date).ToUniversalTime().ToString('o'); summary = 'ok' } |
            ConvertTo-Json | Set-Content -Path (Join-Path $sigDir 'claude_completed.json') -Encoding UTF8

        $verdict = Get-DetectorVerdict `
            -TranscriptLines @('done') -TranscriptLastWriteUtc ([datetime]::UtcNow) -NowUtc ([datetime]::UtcNow) `
            -StableThresholdSeconds 25 -RunTimeoutMinutes 60 -RunStartedUtc ([datetime]::UtcNow.AddMinutes(-1)) `
            -InteractivePromptPatterns @('Continue\?') -CompletedPromptPatterns @('PS [A-Z]:.*> *$') `
            -ExecutionMode 'print' `
            -CompletionSignalPresent (Test-CompletionSignal -IterationFolder $folder) `
            -FailureSignalPresent    (Test-FailureSignal -IterationFolder $folder) `
            -ProcessExited $true -ProcessExitCode 0

        if ($verdict.status -ne 'COMPLETED') { throw "expected COMPLETED, got $($verdict.status)" }
    }

    # ---- Scenario 2: failure signal ------------------------------------
    Run 'Scenario: failure signal beats exit-0' {
        $iter = '2026-05-06_10-05-00'
        $folder = New-IterationFolder -IterationsRoot $iterRoot -IterationId $iter
        $sigDir = Initialize-SignalsDir -IterationFolder $folder
        @{ reason = 'broken' } | ConvertTo-Json | Set-Content -Path (Join-Path $sigDir 'claude_failed.json') -Encoding UTF8

        $v = Get-DetectorVerdict `
            -TranscriptLines @() -TranscriptLastWriteUtc ([datetime]::UtcNow) -NowUtc ([datetime]::UtcNow) `
            -StableThresholdSeconds 25 -RunTimeoutMinutes 60 -RunStartedUtc ([datetime]::UtcNow.AddMinutes(-1)) `
            -InteractivePromptPatterns @() -CompletedPromptPatterns @() `
            -ExecutionMode 'print' -CompletionSignalPresent $false -FailureSignalPresent $true `
            -ProcessExited $true -ProcessExitCode 0
        if ($v.status -ne 'FAILED') { throw "expected FAILED, got $($v.status)" }
    }

    # ---- Scenario 3: timeout / killed process --------------------------
    Run 'Scenario: process exit -1 (killed for timeout) ends as FAILED via detector, TIMEOUT via runner override' {
        # The detector itself sees exit -1 as FAILED. The runner translates timeouts
        # into TIMEOUT separately. Here we just verify the detector branch.
        $v = Get-DetectorVerdict `
            -TranscriptLines @() -TranscriptLastWriteUtc ([datetime]::UtcNow) -NowUtc ([datetime]::UtcNow) `
            -StableThresholdSeconds 25 -RunTimeoutMinutes 60 -RunStartedUtc ([datetime]::UtcNow.AddMinutes(-1)) `
            -InteractivePromptPatterns @() -CompletedPromptPatterns @() `
            -ExecutionMode 'print' -CompletionSignalPresent $false -FailureSignalPresent $false `
            -ProcessExited $true -ProcessExitCode -1
        if ($v.status -ne 'FAILED') { throw "expected FAILED, got $($v.status)" }
    }

    # ---- Scenario 4: missing raportti.md but successful run ------------
    Run 'Scenario: collector handles missing raportti.md' {
        $iter = '2026-05-06_10-10-00'
        $folder = New-IterationFolder -IterationsRoot $iterRoot -IterationId $iter
        $log = Join-Path $transcriptDir 'demo.log'
        Set-Content -Path $log -Value @('line 1', 'line 2', 'line 3', '##WAGGLE_RUN_COMPLETE##') -Encoding UTF8

        # No raportti.md. Should still produce tail and not throw.
        Save-IterationArtifacts `
            -IterationFolder $folder -TranscriptFile $log -ReportFile (Join-Path $projectRoot 'raportti.md') `
            -TailLineCount 100 -ProjectRoot $projectRoot -StateObject $null
        if (-not (Test-Path (Join-Path $folder 'powershell_tail.txt'))) { throw 'tail missing' }
    }

    # ---- Scenario 5: packager redacts secrets --------------------------
    Run 'Scenario: packager produces redacted llm_input_package' {
        $iter = '2026-05-06_10-15-00'
        $folder = New-IterationFolder -IterationsRoot $iterRoot -IterationId $iter
        $report = Join-Path $folder 'raportti.md'
        Set-Content -Path $report -Value @"
# raportti
API_KEY=verysecretvalue123abc
ok
"@ -Encoding UTF8

        $pkg = Build-LlmInputPackage `
            -IterationFolder $folder -IterationId $iter `
            -ReportPath $report -MaxChars 100000

        $body = Get-Content -Raw -Path $pkg.truncated_path
        if ($body -match 'verysecretvalue123abc') { throw 'secret leaked into package!' }
        if (-not ($body -match 'REDACTED')) { throw 'no redaction marker present' }
        if (-not (Test-Path $pkg.redaction_report)) { throw 'redaction report missing' }
    }
}
finally {
    Remove-Item -Path $tmpRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ''
Write-Host ("Result: {0}/{1} tests passed" -f $script:passes, $script:tests) -ForegroundColor Cyan
if ($script:fails.Count -gt 0) { exit 1 }
exit 0
