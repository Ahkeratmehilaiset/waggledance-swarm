# CompletionVerifier.ps1
# Phase 1.6: combine runner result + signal-file content + ArtifactValidator
# output into a single FINAL verdict. The detector's preliminary verdict
# never alone authorises COMPLETED in print mode.
#
# Compatible with PowerShell 5.1.

Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'ArtifactValidator.ps1')

function Resolve-PrintModeVerdict {
    <#
    .SYNOPSIS
    Final verdict for print-mode runs. Inputs:
      - RunnerResult: from Invoke-ClaudeCodePrint
      - IterationFolder: where signals and artifacts live
      - IterationId: must match signal.iteration_id
      - RequireExitMarker: if true, stdout must contain ExitMarker
      - RequireReport: if true, missing raportti.md downgrades verdict
    Output:
      pscustomobject @{ status; reason; checks; signals }
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] $RunnerResult,
        [Parameter(Mandatory)] [string] $IterationFolder,
        [Parameter(Mandatory)] [string] $IterationId,
        [string] $ExitMarker = '',
        [bool]   $RequireExitMarker = $true,
        [bool]   $RequireReport = $false,
        [string] $UniqueArtifactPath = '',
        [string] $UniqueArtifactBody = '',
        [int]    $UniqueArtifactMaxBytes = 4096
    )

    $checks = [ordered]@{}
    $reasons = @()

    # 1. early-exit signals from the runner
    if ($RunnerResult.early_status -eq 'NEEDS_MANUAL_ACTION') {
        return [pscustomobject]@{
            status = 'NEEDS_MANUAL_ACTION'
            reason = $RunnerResult.early_status_reason
            checks = @{ early_status = $RunnerResult.early_status }
            signals = @{}
        }
    }
    if ($RunnerResult.timed_out) {
        return [pscustomobject]@{
            status = 'TIMEOUT'
            reason = "Process killed after $($RunnerResult.elapsed_seconds)s (timeout)"
            checks = @{ timed_out = $true }
            signals = @{}
        }
    }

    # 2. read signals from disk
    $sigDir = Join-Path $IterationFolder 'signals'
    $cPath  = Join-Path $sigDir 'claude_completed.json'
    $fPath  = Join-Path $sigDir 'claude_failed.json'

    $cExists = Test-Path $cPath
    $fExists = Test-Path $fPath

    $checks.completion_signal_present = $cExists
    $checks.failure_signal_present    = $fExists
    $checks.exit_code                 = $RunnerResult.exit_code
    $checks.process_exited            = $RunnerResult.process_exited

    # 3. CONFLICT: both signals present -> never auto-proceed
    if ($cExists -and $fExists) {
        return [pscustomobject]@{
            status = 'NEEDS_REVIEW_CONFLICT'
            reason = 'Both claude_completed.json and claude_failed.json are present'
            checks = $checks
            signals = @{}
        }
    }

    # 4. Failure path
    if ($fExists -and ($RunnerResult.exit_code -ne 0 -or $RunnerResult.exit_code -eq 0)) {
        # Failure signal is itself a strong negative; even if exit code 0
        # was reported, the signal is the source of truth for failure.
        $reason = 'Failure signal present'
        if ($RunnerResult.exit_code -eq 0) {
            $reason = "$reason (exit code 0 ignored due to explicit failure signal)"
        } else {
            $reason = "$reason and exit code $($RunnerResult.exit_code)"
        }
        return [pscustomobject]@{
            status = 'FAILED'
            reason = $reason
            checks = $checks
            signals = @{}
        }
    }

    # 5. Non-zero exit without explicit completion -> FAILED
    if (-not $cExists -and $RunnerResult.exit_code -ne 0) {
        return [pscustomobject]@{
            status = 'FAILED'
            reason = "Process exited $($RunnerResult.exit_code) and no signal file produced"
            checks = $checks
            signals = @{}
        }
    }

    # 6. Non-zero exit WITH completion signal -> conflict, do not auto-proceed
    if ($cExists -and $RunnerResult.exit_code -ne 0) {
        return [pscustomobject]@{
            status = 'NEEDS_REVIEW_CONFLICT'
            reason = "Completion signal present but exit code $($RunnerResult.exit_code)"
            checks = $checks
            signals = @{}
        }
    }

    # 7. Exit 0 but no completion signal -> COMPLETED_UNVERIFIED
    if ($RunnerResult.exit_code -eq 0 -and -not $cExists) {
        return [pscustomobject]@{
            status = 'COMPLETED_UNVERIFIED'
            reason = 'Exit code 0 but no claude_completed.json was written'
            checks = $checks
            signals = @{}
        }
    }

    # 8. Exit 0 + completion signal: validate signal contract
    $sig = $null
    try { $sig = Get-Content -Raw -Path $cPath | ConvertFrom-Json }
    catch {
        return [pscustomobject]@{
            status = 'NEEDS_REVIEW_CONFLICT'
            reason = "claude_completed.json present but not parseable: $($_.Exception.Message)"
            checks = $checks
            signals = @{}
        }
    }

    if (-not ($sig.PSObject.Properties.Name -contains 'iteration_id')) {
        return [pscustomobject]@{
            status = 'COMPLETED_UNVERIFIED'
            reason = 'completion signal has no iteration_id'
            checks = $checks
            signals = @{ signal = $sig }
        }
    }
    if ([string]$sig.iteration_id -ne $IterationId) {
        return [pscustomobject]@{
            status = 'NEEDS_REVIEW_CONFLICT'
            reason = "completion signal iteration_id mismatch (signal=$($sig.iteration_id) expected=$IterationId)"
            checks = $checks
            signals = @{ signal = $sig }
        }
    }

    # 9. timestamp window
    $startedUtc = $null
    $endedUtc   = $null
    try {
        $startedUtc = ([datetime]::Parse([string]$RunnerResult.started_at)).ToUniversalTime()
        $endedUtc   = ([datetime]::Parse([string]$RunnerResult.ended_at)).ToUniversalTime()
    } catch { }

    if ($sig.PSObject.Properties.Name -contains 'completed_at' -and $startedUtc -and $endedUtc) {
        $ts = $null
        $ok = $true
        try { $ts = [datetime]::Parse([string]$sig.completed_at) } catch { $ok = $false }
        if (-not $ok) {
            return [pscustomobject]@{
                status = 'COMPLETED_UNVERIFIED'
                reason = 'completed_at not parseable'
                checks = $checks
                signals = @{ signal = $sig }
            }
        }
        $tsUtc = $ts.ToUniversalTime()
        # Wide window (24h on either side) tolerates timezone misinterpretation in
        # the signal file. iteration_id matching is the strong identity check.
        $startBound = $startedUtc.AddHours(-24)
        $endBound   = $endedUtc.AddHours(24)
        $checks.timestamp_in_24h_window = ($tsUtc -ge $startBound -and $tsUtc -le $endBound)
        if (-not $checks.timestamp_in_24h_window) {
            return [pscustomobject]@{
                status = 'NEEDS_REVIEW_CONFLICT'
                reason = "completed_at $tsUtc more than 24h outside run window $startBound..$endBound"
                checks = $checks
                signals = @{ signal = $sig }
            }
        }
    }

    # 10. exit marker (optional)
    if ($RequireExitMarker -and $ExitMarker) {
        $stdoutPath = $RunnerResult.stdout_path
        $present = $false
        if (Test-Path $stdoutPath) {
            $stdoutText = Get-Content -Raw -Path $stdoutPath -ErrorAction SilentlyContinue
            $present = ($stdoutText -and ($stdoutText.IndexOf($ExitMarker, [System.StringComparison]::Ordinal) -ge 0))
        }
        $checks.exit_marker_present = $present
        if (-not $present) {
            return [pscustomobject]@{
                status = 'COMPLETED_UNVERIFIED'
                reason = "exit marker '$ExitMarker' not found in stdout"
                checks = $checks
                signals = @{ signal = $sig }
            }
        }
    }

    # 11. ArtifactValidator
    $art = Test-IterationArtifacts -IterationFolder $IterationFolder -IterationId $IterationId `
              -ExecutionMode 'print' -RequireReport:$RequireReport -RequirePackage:$true `
              -RunStartedUtc $startedUtc -RunEndedUtc $endedUtc
    $checks.artifact_validator = @{
        ok       = $art.ok
        errors   = $art.errors
        warnings = $art.warnings
    }
    if (-not $art.ok) {
        return [pscustomobject]@{
            status = 'COMPLETED_UNVERIFIED'
            reason = 'ArtifactValidator failed: ' + ($art.errors -join '; ')
            checks = $checks
            signals = @{ signal = $sig }
        }
    }

    # 12. Phase 2A-1 P3: unique-per-iteration artifact validation
    if ($UniqueArtifactPath -and $startedUtc) {
        $unique = Test-UniqueIterationArtifact `
            -ExpectedAbsolutePath $UniqueArtifactPath `
            -ExpectedContent      $UniqueArtifactBody `
            -RunStartedUtc        $startedUtc `
            -MaxBytes             $UniqueArtifactMaxBytes
        $checks.unique_artifact = @{
            ok     = $unique.ok
            errors = $unique.errors
        }
        if (-not $unique.ok) {
            return [pscustomobject]@{
                status = 'COMPLETED_UNVERIFIED'
                reason = 'Unique iteration artifact failed: ' + ($unique.errors -join '; ')
                checks = $checks
                signals = @{ signal = $sig }
            }
        }
    }

    return [pscustomobject]@{
        status = 'COMPLETED'
        reason = 'exit 0 + valid completion signal + artifact validation passed'
        checks = $checks
        signals = @{ signal = $sig }
    }
}
