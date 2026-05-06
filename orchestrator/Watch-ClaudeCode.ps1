#requires -Version 5.1
<#
.SYNOPSIS
    Phase 1.6 fallback watcher: watches the most recent WaggleDance transcript
    file and tracks the state of an interactive Claude Code session.
.DESCRIPTION
    Use this when you cannot run Claude Code in print mode (executionMode =
    'interactiveTranscriptFallback'). For full automation, prefer
    Invoke-WaggleIteration.ps1.
.PARAMETER ConfigPath
    Path to orchestrator.config.json.
.PARAMETER IterationId
    Optional explicit ID; default is a fresh timestamp.
.PARAMETER ResumeIteration
    Resume an existing iteration folder.
.PARAMETER TranscriptFile
    Optional explicit transcript path.
.PARAMETER Force
    Allow overwriting existing artifacts.
.PARAMETER ForceStaleLock
    Reclaim a stale lock.
.PARAMETER DangerouslyOverrideLiveLock
    Override even a live lock; emergency only.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $ConfigPath,
    [string] $IterationId = '',
    [string] $ResumeIteration = '',
    [string] $TranscriptFile = '',
    [switch] $Force,
    [switch] $ForceStaleLock,
    [switch] $DangerouslyOverrideLiveLock
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$libDir = Join-Path $PSScriptRoot 'lib'
. (Join-Path $libDir 'State.ps1')
. (Join-Path $libDir 'Checkpoint.ps1')
. (Join-Path $libDir 'Detector.ps1')
. (Join-Path $libDir 'Collector.ps1')
. (Join-Path $libDir 'Lockfile.ps1')
. (Join-Path $libDir 'ConfigValidator.ps1')
. (Join-Path $libDir 'Signals.ps1')
. (Join-Path $libDir 'PathValidation.ps1')

if (-not (Test-Path $ConfigPath)) { throw "Config not found: $ConfigPath" }
$cfg = Get-Content -Raw -Path $ConfigPath | ConvertFrom-Json
[void](Assert-WaggleConfig -Config $cfg)

$projectRoot    = $cfg.projectRoot
$transcriptDir  = Join-Path $projectRoot $cfg.transcriptDir
$iterationsRoot = Join-Path $projectRoot $cfg.iterationsDir
$stateDir       = Join-Path $projectRoot $cfg.stateDir
$reportPath     = Join-Path $projectRoot $cfg.reportFile
$lockPath       = Join-Path $stateDir 'orchestrator.lock'

foreach ($d in @($iterationsRoot, $stateDir)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
}

if (-not $TranscriptFile) {
    if (-not (Test-Path $transcriptDir)) {
        throw "Transcript dir does not exist: $transcriptDir."
    }
    $latest = Get-ChildItem -Path $transcriptDir -Filter '*.log' -ErrorAction SilentlyContinue |
              Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $latest) { throw "No .log files in $transcriptDir." }
    $TranscriptFile = $latest.FullName
}
elseif (-not (Test-Path $TranscriptFile)) {
    throw "Transcript file not found: $TranscriptFile"
}

if ($ResumeIteration) {
    Assert-IterationIdValid -Id $ResumeIteration
    $IterationId = $ResumeIteration
    $iterationFolder = Get-SafeIterationFolder -IterationsRoot $iterationsRoot -IterationId $IterationId
    if (-not (Test-Path $iterationFolder)) { throw "Cannot resume: $iterationFolder" }
    Write-Host "Resuming iteration: $IterationId" -ForegroundColor Yellow
} else {
    if (-not $IterationId) { $IterationId = Get-IterationId }
    Assert-IterationIdValid -Id $IterationId
    $iterationFolder = Get-SafeIterationFolder -IterationsRoot $iterationsRoot -IterationId $IterationId
    if (-not (Test-Path $iterationFolder)) { New-Item -ItemType Directory -Path $iterationFolder -Force | Out-Null }
}
$stateFile = Join-Path $iterationFolder 'state.json'

if ($ResumeIteration -and (Test-Path $stateFile) -and -not $Force) {
    $existing = Read-WaggleState -Path $stateFile
    if ($existing -and (Test-IsTerminalState -State $existing.status)) {
        Write-Host "Iteration $IterationId already at terminal state '$($existing.status)'." -ForegroundColor Yellow
        exit 0
    }
}

$lock = Acquire-WaggleLock -Path $lockPath -IterationId $IterationId `
            -ForceStaleLock:$ForceStaleLock `
            -DangerouslyOverrideLiveLock:$DangerouslyOverrideLiveLock

try {
    $git = Get-GitMetadata -RepoPath $projectRoot
    $gitBranch = if ($git.branch) { [string]$git.branch } else { '' }
    $gitCommit = if ($git.commit) { [string]$git.commit } else { '' }
    $state = New-WaggleState `
        -IterationId    $IterationId `
        -TranscriptFile $TranscriptFile `
        -ReportFile     $reportPath `
        -GitBranch      $gitBranch `
        -GitCommit      $gitCommit `
        -ExecutionMode  'interactiveTranscriptFallback'
    $state.transcript_size_bytes     = (Get-Item $TranscriptFile).Length
    $state.transcript_last_growth_at = (Get-Date).ToUniversalTime().ToString('o')

    Save-WaggleState -State $state -Path $stateFile
    Update-CurrentStatePointer -State $state -StateDir $stateDir

    $runStartedUtc = [datetime]::Parse($state.started_at).ToUniversalTime()

    Write-Host ''
    Write-Host '----------------------------------------' -ForegroundColor Cyan
    Write-Host " Watching iteration $IterationId (fallback mode)" -ForegroundColor Cyan
    Write-Host '----------------------------------------' -ForegroundColor Cyan
    Write-Host "  transcript : $TranscriptFile"
    Write-Host "  state      : $stateFile"
    Write-Host "  poll       : $($cfg.pollIntervalSeconds)s"
    Write-Host "  stable     : $($cfg.stableThresholdSeconds)s"
    Write-Host "  timeout    : $($cfg.runTimeoutMinutes) min"
    Write-Host ''

    while ($true) {
        Start-Sleep -Seconds $cfg.pollIntervalSeconds
        $now = [datetime]::UtcNow

        if (-not (Test-Path $TranscriptFile)) {
            $state = Set-WaggleError -State $state -Type 'TranscriptMissing' -Message "Transcript file disappeared: $TranscriptFile" -Hint 'Restart Start-WaggleSession.ps1.'
            $state = Update-WaggleStatus -State $state -NewStatus 'FAILED' -Reason 'Transcript file missing'
            $state.last_check_at = $now.ToString('o')
            Save-WaggleState -State $state -Path $stateFile
            Update-CurrentStatePointer -State $state -StateDir $stateDir
            Write-Warning "FAILED: $($state.error.message)"
            break
        }

        $sizeNow = (Get-Item $TranscriptFile).Length
        if ($sizeNow -ne $state.transcript_size_bytes) {
            $state.transcript_size_bytes     = $sizeNow
            $state.transcript_last_growth_at = $now.ToString('o')
        }

        $lines = Get-TranscriptTail -Path $TranscriptFile -LineCount 250

        $reportUtc = $null
        if (Test-Path $reportPath) {
            $reportUtc = (Get-Item $reportPath).LastWriteTimeUtc
            $state.report_last_modified = $reportUtc.ToString('o')
        }

        $signalCompleted = Test-CompletionSignal -IterationFolder $iterationFolder
        $signalFailed    = Test-FailureSignal    -IterationFolder $iterationFolder

        $exitMarker = if ($cfg.PSObject.Properties.Name -contains 'exitMarker') { [string]$cfg.exitMarker } else { '' }

        $verdict = Get-DetectorVerdict `
            -TranscriptLines             $lines `
            -TranscriptLastWriteUtc      ([datetime]::Parse($state.transcript_last_growth_at).ToUniversalTime()) `
            -NowUtc                      $now `
            -StableThresholdSeconds      $cfg.stableThresholdSeconds `
            -RunTimeoutMinutes           $cfg.runTimeoutMinutes `
            -RunStartedUtc               $runStartedUtc `
            -InteractivePromptPatterns   @($cfg.interactivePromptPatterns) `
            -CompletedPromptPatterns     @($cfg.completedPromptPatterns) `
            -ExitMarker                  $exitMarker `
            -ExecutionMode               'interactiveTranscriptFallback' `
            -CompletionSignalPresent     $signalCompleted `
            -FailureSignalPresent        $signalFailed `
            -ReportLastWriteUtc          $reportUtc

        $state.last_check_at = $now.ToString('o')
        $state = Set-WaggleVerdict -State $state -Verdict $verdict

        if ($verdict.status -ne $state.status) {
            $state = Update-WaggleStatus -State $state -NewStatus $verdict.status -Reason $verdict.reason
            $color = switch ($verdict.status) {
                'COMPLETED'           { 'Green' }
                'FAILED'              { 'Red' }
                'TIMEOUT'             { 'Magenta' }
                'NEEDS_MANUAL_ACTION' { 'Yellow' }
                default               { 'Gray' }
            }
            Write-Host "[$IterationId] $($state.status): $($verdict.reason)" -ForegroundColor $color
        }

        Save-WaggleState -State $state -Path $stateFile
        Update-CurrentStatePointer -State $state -StateDir $stateDir

        if (Test-IsTerminalState -State $state.status) {
            Write-Host "[$IterationId] Terminal state $($state.status). Collecting artifacts..." -ForegroundColor Cyan
            $fullCap = if ($cfg.PSObject.Properties.Name -contains 'fullTranscriptMaxBytes') { [int64]$cfg.fullTranscriptMaxBytes } else { 10MB }
            Save-IterationArtifacts `
                -IterationFolder         $iterationFolder `
                -TranscriptFile          $TranscriptFile `
                -ReportFile              $reportPath `
                -TailLineCount           $cfg.tailLineCount `
                -FullTranscriptMaxBytes  $fullCap `
                -ProjectRoot             $projectRoot `
                -StateObject             $state `
                -Force:$Force
            Write-Host "[$IterationId] Artifacts saved to: $iterationFolder" -ForegroundColor Cyan
            break
        }
    }

    Write-Host ''
    Write-Host "Watcher exited. Final status: $($state.status)" -ForegroundColor Cyan
}
finally {
    [void](Release-WaggleLock -Path $lockPath -LockId $lock.lock_id)
}
