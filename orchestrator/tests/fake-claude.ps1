#requires -Version 5.1
<#
.SYNOPSIS
    Fake Claude Code binary used by integration tests. Reads stdin, simulates
    one of several scenarios via env vars, writes signal files, and exits
    with a configurable code.

.DESCRIPTION
    Configure via env vars on the launcher side:
        WAGGLE_FAKE_SCENARIO  = success | fail | timeout | needs_action | no_signal
        WAGGLE_FAKE_SIGNAL_DIR = absolute path where signal files go
        WAGGLE_FAKE_ITERATION = iteration id

    The wrapper test creates a tiny .ps1 (or .cmd on Windows) that delegates
    to this script; that file is what `claude` is set to in the test config.
#>
[CmdletBinding()] param()
$ErrorActionPreference = 'Continue'

# Drain stdin (the prompt).
$prompt = [Console]::In.ReadToEnd()

# Print a simple banner so stdout has something to inspect.
Write-Host "fake-claude: scenario=$($env:WAGGLE_FAKE_SCENARIO) iteration=$($env:WAGGLE_FAKE_ITERATION)"
Write-Host "fake-claude: received $($prompt.Length) chars of prompt"

$signalDir = $env:WAGGLE_FAKE_SIGNAL_DIR
if (-not $signalDir) { $signalDir = '.' }
if (-not (Test-Path $signalDir)) { New-Item -ItemType Directory -Path $signalDir -Force | Out-Null }

switch ($env:WAGGLE_FAKE_SCENARIO) {
    'success' {
        $obj = @{
            iteration_id = $env:WAGGLE_FAKE_ITERATION
            completed_at = (Get-Date).ToUniversalTime().ToString('o')
            summary      = 'fake success'
        } | ConvertTo-Json
        Set-Content -Path (Join-Path $signalDir 'claude_completed.json') -Value $obj -Encoding UTF8
        Write-Host '##WAGGLE_RUN_COMPLETE##'
        exit 0
    }
    'success_with_smoke_artifact' {
        # Phase 2A-1 P3/P5: honour the SMOKE ARTIFACT CONTRACT injected by
        # Invoke-WaggleIteration.ps1. Parses the artifact path AND signal
        # dir straight from the prompt (relative to projectRoot=cwd) so
        # this scenario works for any iterationsDir name (production
        # `iterations/` or test `iterations_p5_test/`) and survives
        # sanitizeEnvironment=true.
        $artifactRel = $null
        $iterId = $null
        $am = [regex]::Match($prompt, '`([^`]*?/artifacts/smoke_(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\.txt)`')
        if ($am.Success) {
            $artifactRel = $am.Groups[1].Value
            $iterId      = $am.Groups[2].Value
        }
        if (-not $artifactRel -or -not $iterId) {
            Write-Host 'fake-claude: could not parse SMOKE ARTIFACT CONTRACT from prompt'
            exit 2
        }

        $artifactPath = Join-Path (Get-Location).Path $artifactRel
        $artifactDir = Split-Path -Parent $artifactPath
        if (-not (Test-Path $artifactDir)) { New-Item -ItemType Directory -Path $artifactDir -Force | Out-Null }
        $body = "WaggleDance smoke artifact for iteration $iterId"
        Set-Content -Path $artifactPath -Value $body -Encoding UTF8 -NoNewline

        # Derive signal dir from prompt: line "Write a final JSON file to:
        # `<iterationsDir>/<iter>/signals/claude_completed.json`"
        $signalRel = $null
        $sm = [regex]::Match($prompt, '`([^`]*?/signals)/claude_completed\.json`')
        if ($sm.Success) { $signalRel = $sm.Groups[1].Value }
        $effectiveSignalDir = if ($signalRel) { Join-Path (Get-Location).Path $signalRel }
                              elseif ($env:WAGGLE_FAKE_SIGNAL_DIR) { $env:WAGGLE_FAKE_SIGNAL_DIR }
                              else { Split-Path -Parent $artifactDir | ForEach-Object { Join-Path $_ 'signals' } }
        if (-not (Test-Path $effectiveSignalDir)) { New-Item -ItemType Directory -Path $effectiveSignalDir -Force | Out-Null }

        $obj = @{
            iteration_id = $iterId
            completed_at = (Get-Date).ToUniversalTime().ToString('o')
            summary      = 'fake success with smoke artifact'
        } | ConvertTo-Json
        Set-Content -Path (Join-Path $effectiveSignalDir 'claude_completed.json') -Value $obj -Encoding UTF8
        Write-Host '##WAGGLE_RUN_COMPLETE##'
        exit 0
    }
    'review_success' {
        # Phase 2A-2 review fake: parse role + target_iteration_id from
        # the prompt's REVIEW METADATA block and emit a schema-valid
        # ```review-json``` block plus the required REVIEW-COMPLETE marker.
        $role = $null
        $tid  = $null
        $rm = [regex]::Match($prompt, '(?ms)^- role:\s*`([^`]+)`')
        if ($rm.Success) { $role = $rm.Groups[1].Value }
        $im = [regex]::Match($prompt, '(?ms)^- target_iteration_id:\s*`([^`]+)`')
        if ($im.Success) { $tid = $im.Groups[1].Value }
        $sm = [regex]::Match($prompt, '(?ms)^- source_package_path:\s*`([^`]+)`')
        $spp = if ($sm.Success) { $sm.Groups[1].Value } else { 'unknown' }
        if (-not $role) { $role = 'architect' }
        if (-not $tid)  { $tid = 'unknown_iter' }

        $idPrefix = switch ($role) {
            'architect'   { 'ARCH' }
            'security'    { 'SEC' }
            'reliability' { 'REL' }
            default       { 'ANY' }
        }

        $reviewObj = @{
            role                 = $role
            target_iteration_id  = $tid
            source_package_path  = $spp
            summary              = "fake-claude review for $role over $tid -- ok."
            verdict              = 'pass_with_notes'
            findings             = @(
                @{
                    id                  = "$idPrefix-001"
                    severity            = 'low'
                    title               = 'fake finding for test harness'
                    where               = 'fake-claude.ps1'
                    evidence            = 'this is a placeholder finding emitted by the test harness'
                    why_it_matters      = 'the test harness must produce a schema-valid review even with no real reviewer'
                    recommended_action  = 'no action -- this is a fake review scenario'
                }
            )
            metrics              = @{ files_reviewed = 1; lines_reviewed = 1; review_duration_seconds = 0 }
            completed            = $true
        }
        $jsonText = $reviewObj | ConvertTo-Json -Depth 10
        [Console]::Out.WriteLine('fake-claude: review_success scenario active')
        [Console]::Out.WriteLine('```review-json')
        [Console]::Out.WriteLine($jsonText)
        [Console]::Out.WriteLine('```')
        [Console]::Out.WriteLine('')
        [Console]::Out.WriteLine('## Verdict')
        [Console]::Out.WriteLine('fake-claude review verdict: pass_with_notes')
        [Console]::Out.WriteLine('## Critical issues')
        [Console]::Out.WriteLine('_None._')
        [Console]::Out.WriteLine('## Important issues')
        [Console]::Out.WriteLine('_None._')
        [Console]::Out.WriteLine('## Minor issues')
        [Console]::Out.WriteLine("- $idPrefix-001: fake finding for test harness")
        [Console]::Out.WriteLine('## Evidence references')
        [Console]::Out.WriteLine('- fake-claude.ps1 (the test harness itself)')
        [Console]::Out.WriteLine('## Suggested next actions')
        [Console]::Out.WriteLine('1. ignore this review; it is fake')
        [Console]::Out.WriteLine('## Confidence')
        [Console]::Out.WriteLine('low -- this is a test harness, not a real reviewer')
        [Console]::Out.WriteLine('')
        [Console]::Out.WriteLine('REVIEW-COMPLETE')
        exit 0
    }
    'review_no_marker' {
        # Schema-valid JSON but no REVIEW-COMPLETE marker -- runner must fail.
        $reviewObj = @{
            role                 = 'architect'
            target_iteration_id  = 'unknown_iter'
            source_package_path  = 'fake'
            summary              = 'no marker'
            verdict              = 'pass'
            findings             = @()
            metrics              = @{ files_reviewed = 0; lines_reviewed = 0; review_duration_seconds = 0 }
            completed            = $true
        }
        [Console]::Out.WriteLine('```review-json')
        [Console]::Out.WriteLine(($reviewObj | ConvertTo-Json -Depth 10))
        [Console]::Out.WriteLine('```')
        [Console]::Out.WriteLine('## Verdict')
        [Console]::Out.WriteLine('fake')
        # Deliberately do NOT print REVIEW-COMPLETE
        exit 0
    }
    'review_no_json' {
        # No review-json block -- runner must fail.
        [Console]::Out.WriteLine('I forgot to emit a review-json block.')
        [Console]::Out.WriteLine('')
        [Console]::Out.WriteLine('REVIEW-COMPLETE')
        exit 0
    }
    'review_bad_schema' {
        # Has block + marker, but schema-invalid (missing required field).
        $bad = @{
            role                 = 'architect'
            target_iteration_id  = 'unknown_iter'
            source_package_path  = 'fake'
            summary              = 'missing verdict'
            findings             = @()
            metrics              = @{ files_reviewed = 0; lines_reviewed = 0; review_duration_seconds = 0 }
            completed            = $true
        }
        [Console]::Out.WriteLine('```review-json')
        [Console]::Out.WriteLine(($bad | ConvertTo-Json -Depth 10))
        [Console]::Out.WriteLine('```')
        [Console]::Out.WriteLine('')
        [Console]::Out.WriteLine('REVIEW-COMPLETE')
        exit 0
    }
    'fail' {
        $obj = @{
            iteration_id = $env:WAGGLE_FAKE_ITERATION
            failed_at    = (Get-Date).ToUniversalTime().ToString('o')
            reason       = 'fake failure scenario'
        } | ConvertTo-Json
        Set-Content -Path (Join-Path $signalDir 'claude_failed.json') -Value $obj -Encoding UTF8
        Write-Host '##WAGGLE_RUN_FAILED##'
        exit 1
    }
    'no_signal' {
        Write-Host 'completed but did not write signal file'
        exit 0
    }
    'timeout' {
        Write-Host 'sleeping forever to simulate timeout'
        Start-Sleep -Seconds 99999
        exit 0
    }
    'needs_action' {
        Write-Host 'Do you want to proceed? [y/N]'
        Start-Sleep -Seconds 99999
        exit 0
    }
    default {
        Write-Host "Unknown WAGGLE_FAKE_SCENARIO='$($env:WAGGLE_FAKE_SCENARIO)' -- exiting 0"
        exit 0
    }
}
