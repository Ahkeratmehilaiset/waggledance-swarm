# Detector.ps1
# Pure functions for state detection from a transcript / signal-file snapshot.
# No filesystem polling or sleeping here -- the caller assembles inputs.
#
# Phase 1.6 priority order (first match wins):
#   1. Failure signal file present                  -> FAILED
#   2. Completion signal file present               -> COMPLETED
#   3. Process exit (print mode) with non-zero code -> FAILED
#   4. Process exit (print mode) with code 0        -> COMPLETED
#   5. Exit marker in transcript                    -> COMPLETED
#   6. Interactive prompt at tail                   -> NEEDS_MANUAL_ACTION
#   7. Run timeout exceeded                         -> TIMEOUT
#   8. (Fallback mode only) stable + shell prompt   -> COMPLETED  (logged as fallback)
#   9. default                                      -> RUNNING
#
# The interactive-prompt check runs BEFORE the timeout check so a hanging
# permission prompt is not hidden as a generic timeout.

Set-StrictMode -Version Latest

function Get-TranscriptTail {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $Path,
        [int] $LineCount = 250
    )
    if (-not (Test-Path $Path)) { return @() }
    return @(Get-Content -Path $Path -Tail $LineCount -ErrorAction Stop)
}

function Test-InteractivePrompt {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [AllowEmptyCollection()] [string[]] $Lines,
        [Parameter(Mandatory)] [string[]] $Patterns
    )
    if ($Lines.Count -eq 0) { return $null }
    $startIdx = [Math]::Max(0, $Lines.Count - 25)
    $tail = ($Lines[$startIdx..($Lines.Count - 1)] -join "`n")
    foreach ($p in $Patterns) {
        if ($tail -match $p) { return $p }
    }
    return $null
}

function Test-PromptAtTail {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [AllowEmptyCollection()] [string[]] $Lines,
        [Parameter(Mandatory)] [string[]] $Patterns
    )
    if ($Lines.Count -eq 0) { return $false }
    $nonEmpty = @($Lines | Where-Object { $_ -match '\S' })
    if ($nonEmpty.Count -eq 0) { return $false }
    $lastLine = $nonEmpty[-1]
    foreach ($p in $Patterns) {
        if ($lastLine -match $p) { return $true }
    }
    return $false
}

function Test-ExitMarker {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [AllowEmptyCollection()] [string[]] $Lines,
        [Parameter(Mandatory)] [string] $Marker
    )
    if ([string]::IsNullOrWhiteSpace($Marker)) { return $false }
    $escaped = [regex]::Escape($Marker)
    foreach ($l in $Lines) {
        if ($l -match $escaped) { return $true }
    }
    return $false
}

function Get-DetectorVerdict {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [AllowEmptyCollection()] [string[]] $TranscriptLines,
        [Parameter(Mandatory)] [datetime] $TranscriptLastWriteUtc,
        [Parameter(Mandatory)] [datetime] $NowUtc,
        [Parameter(Mandatory)] [int]      $StableThresholdSeconds,
        [Parameter(Mandatory)] [int]      $RunTimeoutMinutes,
        [Parameter(Mandatory)] [datetime] $RunStartedUtc,
        [Parameter(Mandatory)] [string[]] $InteractivePromptPatterns,
        [Parameter(Mandatory)] [string[]] $CompletedPromptPatterns,

        [string]   $ExitMarker = '',
        [object]   $ReportLastWriteUtc = $null,

        [ValidateSet('print', 'interactiveTranscriptFallback')]
        [string]   $ExecutionMode = 'interactiveTranscriptFallback',
        [bool]     $CompletionSignalPresent = $false,
        [bool]     $FailureSignalPresent    = $false,
        [object]   $ProcessExited     = $null,
        [object]   $ProcessExitCode   = $null
    )

    $signals = [ordered]@{
        line_count                  = $TranscriptLines.Count
        execution_mode              = $ExecutionMode
        completion_signal_present   = $CompletionSignalPresent
        failure_signal_present      = $FailureSignalPresent
        process_exited              = $ProcessExited
        process_exit_code           = $ProcessExitCode
    }

    $elapsedMinutes = ($NowUtc - $RunStartedUtc).TotalMinutes
    $signals.elapsed_minutes = [Math]::Round($elapsedMinutes, 2)

    if ($FailureSignalPresent) {
        return [pscustomobject]@{
            status  = 'FAILED'
            reason  = 'Failure signal file present (claude_failed.json)'
            signals = $signals
        }
    }

    if ($CompletionSignalPresent) {
        return [pscustomobject]@{
            status  = 'COMPLETED'
            reason  = 'Completion signal file present (claude_completed.json)'
            signals = $signals
        }
    }

    if ($ProcessExited -eq $true) {
        if ($ProcessExitCode -eq 0) {
            return [pscustomobject]@{
                status  = 'COMPLETED'
                reason  = 'Child process exited with code 0'
                signals = $signals
            }
        }
        return [pscustomobject]@{
            status  = 'FAILED'
            reason  = "Child process exited with non-zero code $ProcessExitCode"
            signals = $signals
        }
    }

    if (Test-ExitMarker -Lines $TranscriptLines -Marker $ExitMarker) {
        $signals.exit_marker = $true
        return [pscustomobject]@{
            status  = 'COMPLETED'
            reason  = "Exit marker '$ExitMarker' found in transcript"
            signals = $signals
        }
    }
    $signals.exit_marker = $false

    $hit = Test-InteractivePrompt -Lines $TranscriptLines -Patterns $InteractivePromptPatterns
    $signals.interactive_pattern = $hit
    if ($hit) {
        return [pscustomobject]@{
            status  = 'NEEDS_MANUAL_ACTION'
            reason  = "Interactive prompt detected at tail: $hit"
            signals = $signals
        }
    }

    if ($elapsedMinutes -gt $RunTimeoutMinutes) {
        $msg = "Run exceeded " + $RunTimeoutMinutes + " min"
        return [pscustomobject]@{
            status  = 'TIMEOUT'
            reason  = $msg
            signals = $signals
        }
    }

    $secondsSinceGrowth = ($NowUtc - $TranscriptLastWriteUtc).TotalSeconds
    $signals.seconds_since_growth = [Math]::Round($secondsSinceGrowth, 0)
    $isStable = $secondsSinceGrowth -ge $StableThresholdSeconds
    $signals.is_stable = $isStable

    $promptAtTail = Test-PromptAtTail -Lines $TranscriptLines -Patterns $CompletedPromptPatterns
    $signals.prompt_at_tail = $promptAtTail

    $reportRecent = $false
    if ($null -ne $ReportLastWriteUtc -and $ReportLastWriteUtc -ge $RunStartedUtc) {
        $reportRecent = $true
    }
    $signals.report_modified_during_run = $reportRecent

    if ($ExecutionMode -eq 'interactiveTranscriptFallback' -and $isStable -and $promptAtTail) {
        $signals.fallback_completion = $true
        $secs = $signals.seconds_since_growth
        $reasonText = "Fallback completion: stable for " + $secs + "s and shell prompt at tail"
        return [pscustomobject]@{
            status  = 'COMPLETED'
            reason  = $reasonText
            signals = $signals
        }
    }

    return [pscustomobject]@{
        status  = 'RUNNING'
        reason  = 'No completion signal yet'
        signals = $signals
    }
}
