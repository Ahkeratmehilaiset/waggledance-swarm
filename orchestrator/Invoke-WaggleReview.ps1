#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2A-2 review-runner. Runs Claude Code in a read-only review
    role over an existing iteration package and writes a structured
    review (review.json + review.md + metadata) under the source
    iteration's reviews/ folder.
.DESCRIPTION
    Reuses Phase 1.6 / Phase 2A-1 primitives (Lockfile, Detector,
    Signals, Preflight, State, ClaudeRunner, Redactor) and the new
    Phase 2A-2 ReviewAdapter. The review child runs with the safe
    profile from Get-WaggleReviewSafeProfile -- Bash, Write, and Edit
    are NOT in the effective allowed-tools and the runner enforces it
    regardless of what the loaded config says.
.PARAMETER ConfigPath
    Path to the live orchestrator.config.json (used for projectRoot,
    iterationsDir, stateDir, claudeCommand, model, etc.).
.PARAMETER ReviewConfigPath
    Path to the safe review-mode config example (or a derivative).
    Optional: if not given, the runner uses the safe profile constants
    from ReviewAdapter and the live config's claudeCommand / model.
.PARAMETER SourceIterationId
    Iteration id whose llm_input_package.md is being reviewed.
.PARAMETER PackagePath
    Explicit package path (optional, must agree with SourceIterationId
    if both given).
.PARAMETER Role
    architect | security | reliability.
.PARAMETER MaxTurns
    Override max turns. Default 60 for review.
.PARAMETER OutputDir
    Override review output dir. Default:
    iterations/<SourceIterationId>/reviews
.PARAMETER DryRun
    Render the prompt and metadata-skeleton, then exit without spawning
    Claude. Used by Test-Phase2A2.
.PARAMETER Strict
    Reserved.
.PARAMETER NoLock
    Skip lock acquisition (only the caller's harness should set this --
    used by Test-ReviewRunner with fake-claude).
.PARAMETER ClaudeCommandOverride
    Override the resolved claude command (used by tests to point at a
    fake-claude wrapper).
#>
[CmdletBinding()]
param(
    [string] $ConfigPath = '',
    [string] $ReviewConfigPath = '',
    [string] $SourceIterationId = '',
    [string] $PackagePath = '',
    [string] $Role = '',
    [int]    $MaxTurns = 0,
    [string] $OutputDir = '',
    [switch] $DryRun,
    [switch] $Strict,
    [switch] $NoLock,
    [string] $ClaudeCommandOverride = ''
)

$ErrorActionPreference = 'Stop'

$Script:ReviewRunnerRoot = $PSScriptRoot
$Script:ReviewLibDir = Join-Path $PSScriptRoot 'lib\review'
$Script:OrchestratorLibDir = Join-Path $PSScriptRoot 'lib'

# Always available (re-loaded under -DotSource as well).
. (Join-Path $Script:OrchestratorLibDir 'State.ps1')
. (Join-Path $Script:OrchestratorLibDir 'PathValidation.ps1')
. (Join-Path $Script:OrchestratorLibDir 'Lockfile.ps1')
. (Join-Path $Script:OrchestratorLibDir 'ConfigValidator.ps1')
. (Join-Path $Script:OrchestratorLibDir 'Preflight.ps1')
. (Join-Path $Script:OrchestratorLibDir 'Signals.ps1')
. (Join-Path $Script:OrchestratorLibDir 'ClaudeRunner.ps1')
. (Join-Path $Script:OrchestratorLibDir 'EnvSanitize.ps1')
. (Join-Path $Script:OrchestratorLibDir 'Redactor.ps1')
. (Join-Path $Script:ReviewLibDir 'ReviewAdapter.ps1')
. (Join-Path $Script:ReviewLibDir 'ReviewSurface.ps1')

function Get-Iso8601Utc {
    return (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffffffZ')
}

function Invoke-WaggleReviewSubprocess {
    <#
    .SYNOPSIS
    Synchronous subprocess runner for review-mode child Claude calls.

    Why a separate runner: orchestrator/lib/ClaudeRunner.ps1 captures
    stdout via BeginOutputReadLine + Register-ObjectEvent. On PS 5.1
    the events are dispatched on a separate runspace and only delivered
    when the engine yields; for fast-exit children the events fire
    AFTER the StreamWriter is already disposed, leaving the stdout file
    truncated. The review runner depends on the COMPLETE stdout to
    extract the fenced ```review-json``` block, so it uses synchronous
    StandardOutput.ReadToEnd() instead. This same path is used for the
    real-Claude review runs in P9 -- ReadToEnd waits until the child
    closes stdout, so it works for slow children too.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $ClaudeCommand,
        [Parameter(Mandatory)] [string] $PromptFile,
        [Parameter(Mandatory)] [string] $StdoutFile,
        [Parameter(Mandatory)] [string] $StderrFile,
        [Parameter(Mandatory)] [string] $WorkingDirectory,
        [Parameter(Mandatory)] [int]    $TimeoutSeconds,
        [string[]] $ArgList = @(),
        [bool]     $SanitizeEnvironment = $true,
        [string[]] $EnvDenylistPatterns = $null,
        [string[]] $EnvAllowList        = @()
    )
    if (-not (Test-Path -LiteralPath $PromptFile))      { throw "Prompt file not found: $PromptFile" }
    if (-not (Test-Path -LiteralPath $WorkingDirectory)){ throw "Working directory not found: $WorkingDirectory" }
    foreach ($p in @($StdoutFile, $StderrFile)) {
        $d = Split-Path -Parent $p
        if (-not (Test-Path -LiteralPath $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
    }

    $cmdLine = Format-CommandLine -Executable $ClaudeCommand -ArgList $ArgList

    $resolved = Get-Command $ClaudeCommand -ErrorAction SilentlyContinue
    if (-not $resolved) { throw "Cannot resolve Claude executable: $ClaudeCommand" }
    $resolvedSource = [string]$resolved.Source
    $launchExe = $resolvedSource
    $launchArgs = @()
    $ext = [System.IO.Path]::GetExtension($resolvedSource).ToLowerInvariant()
    if ($ext -eq '.ps1') {
        $folder = [System.IO.Path]::GetDirectoryName($resolvedSource)
        $base   = [System.IO.Path]::GetFileNameWithoutExtension($resolvedSource)
        $sibCmd = Join-Path $folder ($base + '.cmd')
        $sibBat = Join-Path $folder ($base + '.bat')
        if (Test-Path -LiteralPath $sibCmd) {
            $launchExe = $sibCmd
        } elseif (Test-Path -LiteralPath $sibBat) {
            $launchExe = $sibBat
        } else {
            $psExe = (Get-Command powershell.exe -ErrorAction SilentlyContinue).Source
            if (-not $psExe) { $psExe = 'powershell.exe' }
            $launchExe  = $psExe
            $launchArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $resolvedSource)
        }
    }

    $envInfo = [pscustomobject]@{ environment = $null; stripped = @() }
    if ($SanitizeEnvironment) {
        $envInfo = Get-SanitizedEnvironment -DenylistPatterns $EnvDenylistPatterns -AllowList $EnvAllowList
    }

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $launchExe
    $allArgs = @() + $launchArgs + $ArgList
    $psi.Arguments = ($allArgs | ForEach-Object {
        if ($_ -match '\s') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
    }) -join ' '
    $psi.UseShellExecute        = $false
    $psi.CreateNoWindow         = $true
    $psi.WorkingDirectory       = $WorkingDirectory
    $psi.RedirectStandardInput  = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError  = $true

    if ($SanitizeEnvironment) {
        $psi.EnvironmentVariables.Clear()
        foreach ($k in $envInfo.environment.Keys) {
            $psi.EnvironmentVariables[$k] = [string]$envInfo.environment[$k]
        }
    }

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi

    $startedAt = Get-Date
    [void]$proc.Start()

    # Async tasks pulling stdout/stderr to memory (sync-style waiters).
    $outTask = $proc.StandardOutput.ReadToEndAsync()
    $errTask = $proc.StandardError.ReadToEndAsync()

    # Pipe the prompt to stdin and close.
    try {
        $promptBytes = [System.IO.File]::ReadAllBytes($PromptFile)
        $proc.StandardInput.BaseStream.Write($promptBytes, 0, $promptBytes.Length)
        $proc.StandardInput.BaseStream.Flush()
        $proc.StandardInput.Close()
    } catch {
        # If stdin write fails, just continue; the child will see EOF.
    }

    $timedOut = $false
    if (-not $proc.WaitForExit([int]($TimeoutSeconds * 1000))) {
        $timedOut = $true
        try { Stop-ProcessTree -ProcessId $proc.Id } catch {}
        try { $null = $proc.WaitForExit(5000) } catch {}
    } else {
        # Final synchronous drain to ensure async streams complete.
        try { $proc.WaitForExit() } catch {}
    }

    # Phase 2A-4 REL-005: bounded wait on the stdout/stderr tasks.
    # GetAwaiter().GetResult() can block forever if Stop-ProcessTree
    # somehow failed to close the child's stdout/stderr handles. We
    # bound the wait so a runaway grandchild can never hang the
    # orchestrator. After the bounded wait, fall back to whatever
    # was already on disk (or empty), set timed_out=true, and return.
    $taskWaitMs = if ($timedOut) { 3000 } else { 10000 }
    $stdoutText = ''
    $stderrText = ''
    $stdoutDrainOk = $true
    $stderrDrainOk = $true
    try {
        if ($outTask.Wait($taskWaitMs)) {
            $stdoutText = [string]$outTask.Result
        } else {
            $stdoutDrainOk = $false
            try { Stop-ProcessTree -ProcessId $proc.Id } catch {}
            $timedOut = $true
        }
    } catch {
        $stdoutDrainOk = $false
        $stdoutText = ''
    }
    try {
        if ($errTask.Wait($taskWaitMs)) {
            $stderrText = [string]$errTask.Result
        } else {
            $stderrDrainOk = $false
            try { Stop-ProcessTree -ProcessId $proc.Id } catch {}
            $timedOut = $true
        }
    } catch {
        $stderrDrainOk = $false
        $stderrText = ''
    }
    if ($null -eq $stdoutText) { $stdoutText = '' }
    if ($null -eq $stderrText) { $stderrText = '' }

    [System.IO.File]::WriteAllText($StdoutFile, $stdoutText, [System.Text.Encoding]::UTF8)
    [System.IO.File]::WriteAllText($StderrFile, $stderrText, [System.Text.Encoding]::UTF8)

    $elapsed = ((Get-Date) - $startedAt).TotalSeconds
    $exitCode = if ($proc.HasExited) { $proc.ExitCode } else { -1 }

    return [pscustomobject]@{
        early_status            = $null
        early_status_reason     = $null
        early_status_match      = $null
        process_exited          = $proc.HasExited
        exit_code               = $exitCode
        timed_out               = $timedOut
        killed_for_interactive  = $false
        elapsed_seconds         = [Math]::Round($elapsed, 2)
        command_line            = $cmdLine
        stdout_path             = $StdoutFile
        stderr_path             = $StderrFile
        pid                     = $proc.Id
        started_at              = $startedAt.ToUniversalTime().ToString('o')
        ended_at                = (Get-Date).ToUniversalTime().ToString('o')
        env_stripped            = @($envInfo.stripped)
        sanitize_environment    = [bool]$SanitizeEnvironment
        stdout_text_bytes       = $stdoutText.Length
        stderr_text_bytes       = $stderrText.Length
        stdout_drain_ok         = [bool]$stdoutDrainOk
        stderr_drain_ok         = [bool]$stderrDrainOk
    }
}

function Get-FileSha256 {
    param([string] $Path)
    if (-not (Test-Path -LiteralPath $Path)) { return '' }
    $h = Get-FileHash -Algorithm SHA256 -LiteralPath $Path
    return $h.Hash.ToLowerInvariant()
}

function Resolve-WaggleReviewEffectiveProfile {
    [CmdletBinding()]
    param(
        $LiveConfig,
        $ReviewConfig
    )
    $safe = Get-WaggleReviewSafeProfile

    # Start from safe defaults; allow ReviewConfig to OVERRIDE only the
    # keys we explicitly let it touch. Live config NEVER bleeds in.
    $effective = [pscustomobject]@{
        safeMode                   = [bool]$safe.safeMode
        allowBash                  = [bool]$safe.allowBash
        dangerouslySkipPermissions = [bool]$safe.dangerouslySkipPermissions
        requireUniqueArtifact      = [bool]$safe.requireUniqueArtifact
        sanitizeEnvironment        = [bool]$safe.sanitizeEnvironment
        allowedTools               = @($safe.allowedTools)
        disallowedTools            = @($safe.disallowedTools)
        exitMarker                 = [string]$safe.exitMarker
        model                      = 'opus'
        outputFormat               = 'text'
        maxTurns                   = 60
        permissionMode             = 'default'
        runTimeoutMinutes          = 60
        runnerPollSeconds          = 3
        pollIntervalSeconds        = 5
        stableThresholdSeconds     = 25
        llmPackageMaxChars         = 200000
        envDenylist                = $null
        envAllowList               = @()
        killOnInteractivePrompt    = $true
        interactivePromptPatterns  = @()
        completedPromptPatterns    = @()
    }

    if ($null -ne $LiveConfig) {
        if ($LiveConfig.PSObject.Properties['model'] -and $LiveConfig.model) {
            $effective.model = [string]$LiveConfig.model
        }
        if ($LiveConfig.PSObject.Properties['outputFormat'] -and $LiveConfig.outputFormat) {
            $effective.outputFormat = [string]$LiveConfig.outputFormat
        }
        if ($LiveConfig.PSObject.Properties['interactivePromptPatterns']) {
            $effective.interactivePromptPatterns = @($LiveConfig.interactivePromptPatterns)
        }
        if ($LiveConfig.PSObject.Properties['completedPromptPatterns']) {
            $effective.completedPromptPatterns = @($LiveConfig.completedPromptPatterns)
        }
    }

    if ($null -ne $ReviewConfig) {
        # Review config can adjust model, maxTurns, runTimeoutMinutes,
        # llmPackageMaxChars, exit marker, and the patterns. It cannot
        # turn allowBash back on or weaken the tool boundary.
        foreach ($k in 'model','outputFormat','permissionMode','exitMarker') {
            if ($ReviewConfig.PSObject.Properties[$k] -and $null -ne $ReviewConfig.$k) {
                $effective.$k = [string]$ReviewConfig.$k
            }
        }
        foreach ($k in 'maxTurns','runTimeoutMinutes','runnerPollSeconds','pollIntervalSeconds','stableThresholdSeconds','llmPackageMaxChars') {
            if ($ReviewConfig.PSObject.Properties[$k] -and $null -ne $ReviewConfig.$k) {
                $effective.$k = [int]$ReviewConfig.$k
            }
        }
        foreach ($k in 'interactivePromptPatterns','completedPromptPatterns','envAllowList') {
            if ($ReviewConfig.PSObject.Properties[$k] -and $null -ne $ReviewConfig.$k) {
                $effective.$k = @($ReviewConfig.$k)
            }
        }
        if ($ReviewConfig.PSObject.Properties['envDenylist']) {
            $effective.envDenylist = $ReviewConfig.envDenylist
        }
        if ($ReviewConfig.PSObject.Properties['killOnInteractivePrompt']) {
            $effective.killOnInteractivePrompt = [bool]$ReviewConfig.killOnInteractivePrompt
        }
    }

    # Hard-clamp the safety-critical fields. Even if a malicious review
    # config tried to set allowBash=true, we override here.
    $effective.safeMode                   = $true
    $effective.allowBash                  = $false
    $effective.dangerouslySkipPermissions = $false
    $effective.requireUniqueArtifact      = $false
    $effective.sanitizeEnvironment        = $true
    # Defensive list rebuilds:
    $cleanedAllowed = @()
    foreach ($t in $effective.allowedTools) {
        if ($t -ne 'Bash' -and $t -ne 'Write' -and $t -ne 'Edit') { $cleanedAllowed += $t }
    }
    if ($cleanedAllowed.Count -eq 0) { $cleanedAllowed = @('Read','Glob','Grep') }
    $effective.allowedTools = $cleanedAllowed
    $disallow = @()
    foreach ($t in @('Bash','Write','Edit')) {
        if ($effective.disallowedTools -notcontains $t) { $disallow += $t }
    }
    $effective.disallowedTools = @($effective.disallowedTools) + $disallow

    return $effective
}

function Invoke-WaggleReview {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $ConfigPath,
        [string] $ReviewConfigPath = '',
        [string] $SourceIterationId = '',
        [string] $PackagePath = '',
        [Parameter(Mandatory)] [ValidateSet('architect','security','reliability')] [string] $Role,
        [int]    $MaxTurns = 0,
        [string] $OutputDir = '',
        [switch] $DryRun,
        [switch] $NoLock,
        [string] $ClaudeCommandOverride = ''
    )

    if (-not (Test-Path -LiteralPath $ConfigPath)) {
        throw "Config not found: $ConfigPath"
    }
    $liveCfg = Get-Content -Raw -Path $ConfigPath -Encoding UTF8 | ConvertFrom-Json
    [void](Assert-WaggleConfig -Config $liveCfg)

    $reviewCfg = $null
    if ($ReviewConfigPath) {
        if (-not (Test-Path -LiteralPath $ReviewConfigPath)) {
            throw "Review config not found: $ReviewConfigPath"
        }
        $reviewCfg = Get-Content -Raw -Path $ReviewConfigPath -Encoding UTF8 | ConvertFrom-Json
    }

    $projectRoot   = $liveCfg.projectRoot
    $iterationsDir = if ($liveCfg.PSObject.Properties['iterationsDir'] -and $liveCfg.iterationsDir) { [string]$liveCfg.iterationsDir } else { 'iterations' }
    $stateDir      = if ($liveCfg.PSObject.Properties['stateDir'] -and $liveCfg.stateDir) { [string]$liveCfg.stateDir } else { 'state' }
    $claudeCommand = if ($ClaudeCommandOverride) {
        $ClaudeCommandOverride
    } elseif ($liveCfg.PSObject.Properties['claudeCommand'] -and $liveCfg.claudeCommand) {
        [string]$liveCfg.claudeCommand
    } else { 'claude' }

    $effective = Resolve-WaggleReviewEffectiveProfile -LiveConfig $liveCfg -ReviewConfig $reviewCfg
    if ($MaxTurns -gt 0) { $effective.maxTurns = $MaxTurns }

    # Hard refusal: if effective.allowBash is true, requireUniqueArtifact
    # is true, dangerouslySkipPermissions is true, or Bash is in the
    # allowed-tools list -- something tried to widen the trust boundary.
    if ($effective.allowBash)                     { throw 'review-mode safety violation: allowBash must be false' }
    if ($effective.requireUniqueArtifact)         { throw 'review-mode safety violation: requireUniqueArtifact must be false' }
    if ($effective.dangerouslySkipPermissions)    { throw 'review-mode safety violation: dangerouslySkipPermissions must be false' }
    if ($effective.allowedTools -contains 'Bash') { throw 'review-mode safety violation: Bash in allowedTools' }
    if ($effective.allowedTools -contains 'Write'){ throw 'review-mode safety violation: Write in allowedTools' }
    if ($effective.allowedTools -contains 'Edit') { throw 'review-mode safety violation: Edit in allowedTools' }

    # Resolve package
    $resolvedPkgPath = Resolve-WaggleReviewPackagePath `
        -ProjectRoot $projectRoot -IterationsDir $iterationsDir `
        -SourceIterationId $SourceIterationId -PackagePath $PackagePath
    if (-not (Test-Path -LiteralPath $resolvedPkgPath)) {
        throw "Source package not found: $resolvedPkgPath"
    }
    if (-not $SourceIterationId) {
        # Derive iteration id from the path: ...\iterations\<id>\llm_input_package.md
        $parent = Split-Path -Parent $resolvedPkgPath
        $SourceIterationId = Split-Path -Leaf $parent
    }
    Assert-IterationIdValid -Id $SourceIterationId

    $iterFolderRoot = Join-Path $projectRoot $iterationsDir
    $iterFolder = Get-SafeIterationFolder -IterationsRoot $iterFolderRoot -IterationId $SourceIterationId

    # Output dir
    if (-not $OutputDir) {
        $OutputDir = Join-Path $iterFolder 'reviews'
    }
    if (-not (Test-Path -LiteralPath $OutputDir)) {
        New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
    }

    # Read + truncate + redact package
    $pkgRead = Read-WaggleReviewPackage -Path $resolvedPkgPath -MaxChars $effective.llmPackageMaxChars
    $pkgRed  = Invoke-WaggleReviewPackageRedaction -Text $pkgRead.text

    # Resolve role + template
    $spec = Get-WaggleReviewRoleSpec -Role $Role
    $tplPath = Join-Path $projectRoot ('prompts/review/' + $spec.templateFile)
    if (-not (Test-Path -LiteralPath $tplPath)) {
        throw "Review template not found: $tplPath"
    }
    $tpl = Get-Content -Raw -Path $tplPath -Encoding UTF8

    $relPkg = $resolvedPkgPath
    try {
        # Best-effort relative form
        $rootFull = (Resolve-Path -LiteralPath $projectRoot).Path
        $pkgFull  = (Resolve-Path -LiteralPath $resolvedPkgPath).Path
        if ($pkgFull.StartsWith($rootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
            $relPkg = $pkgFull.Substring($rootFull.Length).TrimStart('\','/')
        }
    } catch {}

    # Phase 2A-3: compute package quality + (if sparse) build a
    # redacted, capped, dynamically-fenced review surface supplement.
    $packageQualityInfo = Get-WaggleReviewPackageQualityWithSupplementInfo `
        -ProjectRoot $projectRoot -PackagePath $resolvedPkgPath
    $supplementMd       = ''
    $supplementUsed     = $false
    $sparseReason       = $packageQualityInfo.sparse_reason
    $supplementFiles    = @()
    $supplementTruncated = @()
    if ($packageQualityInfo.sparse -and $null -ne $packageQualityInfo.supplement) {
        $supplementMd        = [string]$packageQualityInfo.supplement.markdown
        $supplementUsed      = $true
        $supplementFiles     = @($packageQualityInfo.supplement.included_files | ForEach-Object { $_.path })
        $supplementTruncated = @($packageQualityInfo.supplement.truncated_files)
    }

    $reviewPrompt = Build-WaggleReviewPrompt `
        -Role $Role `
        -TemplateText $tpl `
        -TargetIterationId $SourceIterationId `
        -SourcePackageRel $relPkg `
        -RedactedPackageText $pkgRed.text `
        -Truncated:$pkgRead.truncated `
        -OriginalChars $pkgRead.original_chars `
        -SupplementMarkdown $supplementMd `
        -SupplementUsed $supplementUsed `
        -SparseReason $sparseReason

    $reviewIterationId = (Get-IterationId) + '_review_' + $Role

    # Where do we stage the prompt + child stdout/stderr / debug?
    $stagingRoot = Join-Path (Join-Path $iterFolder 'reviews') ('_staging_' + $reviewIterationId)
    if (-not (Test-Path -LiteralPath $stagingRoot)) {
        New-Item -ItemType Directory -Path $stagingRoot -Force | Out-Null
    }
    $promptOnDisk = Join-Path $stagingRoot 'review_prompt.md'
    $stdoutPath   = Join-Path $stagingRoot 'review_stdout.txt'
    $stderrPath   = Join-Path $stagingRoot 'review_stderr.txt'
    $debugFile    = Join-Path $stagingRoot 'review_debug.log'
    Set-Content -Path $promptOnDisk -Value $reviewPrompt -Encoding UTF8

    # Persist the redaction report alongside the staging.
    $redReportPath = Join-Path $stagingRoot 'redaction_report.json'
    @{
        review_iteration_id = $reviewIterationId
        target_iteration_id = $SourceIterationId
        role                = $Role
        report              = $pkgRed.report
        applied_at_utc      = Get-Iso8601Utc
    } | ConvertTo-Json -Depth 6 | Set-Content -Path $redReportPath -Encoding UTF8

    # Persist package_quality.json (Phase 2A-3 + Phase 2A-4 P9 fields).
    $packageQualityPath = Join-Path $stagingRoot 'package_quality.json'
    $iterContent = $packageQualityInfo.iteration_content
    $pqOut = [ordered]@{
        target_iteration_id          = $SourceIterationId
        review_iteration_id          = $reviewIterationId
        role                         = $Role
        package_path                 = $relPkg
        reviewable_files_count       = [int]$packageQualityInfo.quality.reviewable_files_count
        reviewable_lines_count       = [int]$packageQualityInfo.quality.reviewable_lines_count
        source_section_count         = [int]$packageQualityInfo.quality.source_section_count
        sparse                       = [bool]$packageQualityInfo.sparse
        sparse_reason                = [string]$packageQualityInfo.sparse_reason
        source_supplement_used       = [bool]$supplementUsed
        source_supplement_files      = $supplementFiles
        truncated_files_count        = [int]$supplementTruncated.Count
        redaction_report_path        = $redReportPath
        # Phase 2A-4 P9: independent review-readiness axis.
        # execution_status (from CompletionVerifier) and
        # review_readiness_status are SEPARATE: a smoke run can be
        # execution-wise COMPLETED while still being supplement_only
        # or insufficient for review.
        review_readiness_status      = [string]$packageQualityInfo.review_readiness_status
        review_readiness_reason      = [string]$packageQualityInfo.review_readiness_reason
        evidence_surface_kind        = [string]$iterContent.evidence_surface_kind
        empty_captured_channels      = [bool]$iterContent.empty_captured_channels
        has_stdout_content           = [bool]$iterContent.has_stdout_content
        has_stderr_content           = [bool]$iterContent.has_stderr_content
        has_report_content           = [bool]$iterContent.has_report_content
        has_transcript_content       = [bool]$iterContent.has_transcript_content
        has_signal_content           = [bool]$iterContent.has_signal_content
        has_unique_artifact_content  = [bool]$iterContent.has_unique_artifact_content
        has_artifact_manifest        = [bool]$iterContent.has_artifact_manifest
    }
    ([pscustomobject]$pqOut) | ConvertTo-Json -Depth 6 | Set-Content -Path $packageQualityPath -Encoding UTF8

    $startedAt = Get-Iso8601Utc

    # Phase 2A-4 P9: refuse the run only on INSUFFICIENT_EVIDENCE
    # (sparse package AND no usable supplement). SUPPLEMENT_ONLY is
    # allowed because the supplement disclosure rule in the prompt
    # tells the reviewer to flag it. REVIEW_READY proceeds normally.
    $needsReviewSurface = ([string]$packageQualityInfo.review_readiness_status -eq 'INSUFFICIENT_EVIDENCE')

    if ($DryRun) {
        # Phase 2B-R2 (REL-019): the DryRun return object MUST include
        # the same identity fields ($Role, $SourceIterationId) that the
        # non-DryRun branch at the bottom of this function returns,
        # because the top-level CLI wrapper does
        # `Write-Host ('Review {0} for {1} -> {2}' -f $r.role, $r.target_iteration_id, $r.status)`
        # in BOTH the success and failure paths. Under Set-StrictMode,
        # accessing a missing property on a pscustomobject throws
        # PropertyNotFoundStrict, exiting 1 even when the dry-run
        # actually succeeded and staged the prompt + package_quality.
        return [pscustomobject]@{
            ok                  = (-not $needsReviewSurface)
            dry_run             = $true
            role                = $Role
            target_iteration_id = $SourceIterationId
            review_iteration_id = $reviewIterationId
            prompt_path         = $promptOnDisk
            staging_root        = $stagingRoot
            effective_profile   = $effective
            package_quality     = $pqOut
            status              = if ($needsReviewSurface) { 'NEEDS_REVIEW_SURFACE' } else { 'DRY_RUN_OK' }
            errors              = if ($needsReviewSurface) { @('package is sparse and no supplement could be assembled') } else { @() }
        }
    }

    if ($needsReviewSurface) {
        # Write a failure metadata + return without spawning Claude.
        $jsonOut = Join-Path $OutputDir ($Role + '.json')
        $mdOut   = Join-Path $OutputDir ($Role + '.md')
        $metaOut = Join-Path $OutputDir ($Role + '.metadata.json')
        $endedAt = Get-Iso8601Utc
        $meta = [ordered]@{
            source_iteration_id          = $SourceIterationId
            review_iteration_id          = $reviewIterationId
            role                         = $Role
            status                       = 'NEEDS_REVIEW_SURFACE'
            package_path                 = $relPkg
            started_at_utc               = $startedAt
            completed_at_utc             = $endedAt
            safe_mode                    = $effective.safeMode
            allow_bash                   = $effective.allowBash
            dangerously_skip_permissions = $effective.dangerouslySkipPermissions
            require_unique_artifact      = $effective.requireUniqueArtifact
            sanitize_environment         = $effective.sanitizeEnvironment
            allowed_tools                = $effective.allowedTools
            disallowed_tools             = $effective.disallowedTools
            review_json_path             = ''
            review_md_path               = ''
            review_json_sha256           = ''
            review_md_sha256             = ''
            redaction_report_path        = $redReportPath
            package_quality_path         = $packageQualityPath
            staging_root                 = $stagingRoot
            errors                       = @('NEEDS_REVIEW_SURFACE: ' + $sparseReason + '; the working tree did not provide source files for a supplement either.')
            run_result                   = $null
        }
        $meta | ConvertTo-Json -Depth 16 | Set-Content -Path $metaOut -Encoding UTF8
        return [pscustomobject]@{
            ok                  = $false
            review_iteration_id = $reviewIterationId
            target_iteration_id = $SourceIterationId
            role                = $Role
            status              = 'NEEDS_REVIEW_SURFACE'
            review_json_path    = ''
            review_md_path      = ''
            metadata_path       = $metaOut
            staging_root        = $stagingRoot
            errors              = @($meta.errors)
            effective_profile   = $effective
            package_quality     = $pqOut
        }
    }

    # Acquire the orchestrator lock so we don't race a smoke/iteration.
    # Pass -ForceStaleLock so a dead-pid lock from a crashed prior run
    # is reclaimed automatically; live locks are still refused.
    $lockPath = Join-Path (Join-Path $projectRoot $stateDir) 'orchestrator.lock'
    $lock = $null
    if (-not $NoLock) {
        $stateDirAbs = Join-Path $projectRoot $stateDir
        if (-not (Test-Path -LiteralPath $stateDirAbs)) {
            New-Item -ItemType Directory -Path $stateDirAbs -Force | Out-Null
        }
        $lock = Acquire-WaggleLock -Path $lockPath -IterationId $reviewIterationId -ForceStaleLock
    }

    $runResult = $null
    $reviewStatus = 'UNKNOWN'
    $errors = New-Object System.Collections.Generic.List[string]

    try {
        # Build claude args via Build-ClaudeArgs.
        $argList = Build-ClaudeArgs `
            -Model           $effective.model `
            -OutputFormat    $effective.outputFormat `
            -MaxTurns        $effective.maxTurns `
            -PermissionMode  $effective.permissionMode `
            -AllowedTools    $effective.allowedTools `
            -DisallowedTools $effective.disallowedTools `
            -DebugFile       $debugFile `
            -DangerouslySkipPermissions $false

        # Phase 2A-3 belt-and-braces: runtime safety gate. If anything
        # in the config-resolution / arg-build chain ever drifts unsafe,
        # this throws BEFORE we spawn the child process. Defense in
        # depth on top of Resolve-WaggleReviewEffectiveProfile's
        # hard-clamp.
        [void](Assert-WaggleReviewSafeProfile -EffectiveProfile $effective -ArgList $argList)

        $timeoutSec = $effective.runTimeoutMinutes * 60

        $runResult = Invoke-WaggleReviewSubprocess `
            -ClaudeCommand    $claudeCommand `
            -PromptFile       $promptOnDisk `
            -StdoutFile       $stdoutPath `
            -StderrFile       $stderrPath `
            -WorkingDirectory $projectRoot `
            -TimeoutSeconds   $timeoutSec `
            -ArgList          $argList `
            -SanitizeEnvironment       $true `
            -EnvDenylistPatterns       $effective.envDenylist `
            -EnvAllowList              $effective.envAllowList

        $stdoutText = ''
        if (Test-Path -LiteralPath $stdoutPath) { $stdoutText = Get-Content -Raw -Path $stdoutPath -Encoding UTF8 }
        if ($null -eq $stdoutText) { $stdoutText = '' }

        if ($runResult.exit_code -ne 0) {
            $errors.Add("claude exited non-zero: $($runResult.exit_code)") | Out-Null
        }
        if ($runResult.timed_out) {
            $errors.Add('claude run timed out') | Out-Null
        }
        if ($runResult.killed_for_interactive) {
            $errors.Add('claude run killed for interactive prompt') | Out-Null
        }

        $parse = Invoke-WaggleReviewParseAndValidate `
            -StdoutText $stdoutText `
            -ExpectedRole $Role `
            -ExpectedIterationId $SourceIterationId

        if (-not $parse.ok) {
            foreach ($e in $parse.errors) { $errors.Add($e) | Out-Null }
        }

        if ($errors.Count -eq 0) {
            $reviewStatus = 'COMPLETED'
        } else {
            $reviewStatus = 'FAILED'
        }

        # Write outputs (review.json + review.md) only if parse ok.
        $jsonOut = Join-Path $OutputDir ($Role + '.json')
        $mdOut   = Join-Path $OutputDir ($Role + '.md')
        $metaOut = Join-Path $OutputDir ($Role + '.metadata.json')

        if ($parse.ok) {
            $json = $parse.object | ConvertTo-Json -Depth 16
            Set-Content -Path $jsonOut -Value $json -Encoding UTF8

            # Phase 2B-R2 (P5c): regression-ledger auto-update hook.
            # Append a regression entry for every critical / high
            # security or reliability finding the reviewer surfaced,
            # de-duplicated by (iteration_id + finding_id) inside the
            # helper. Failure of the hook MUST NOT break review
            # completion — Add-WaggleRegressionsFromReviewObject is
            # itself try/catch-shielded.
            try {
                . (Join-Path $Script:OrchestratorLibDir 'RegressionLedger.ps1')
                $stateDirForLedger = if ([System.IO.Path]::IsPathRooted($stateDir)) { $stateDir } else { (Join-Path $projectRoot $stateDir) }
                if (-not (Test-Path -LiteralPath $stateDirForLedger)) {
                    New-Item -ItemType Directory -Path $stateDirForLedger -Force | Out-Null
                }
                $ledgerPath = Join-Path $stateDirForLedger 'regression_ledger.json'
                $appended = Add-WaggleRegressionsFromReviewObject -LedgerPath $ledgerPath -ReviewObject $parse.object -IterationId $SourceIterationId -Role $Role
                if ($appended -gt 0) {
                    Write-Host ("review hook: appended/updated {0} regression entries from {1} review" -f $appended, $Role)
                }
            } catch {
                Write-Warning ("review regression-ledger hook skipped: " + $_.Exception.Message)
            }

            $md = ConvertTo-WaggleReviewMarkdown -ReviewObject $parse.object
            Set-Content -Path $mdOut -Value $md -Encoding UTF8
        }

        $endedAt = Get-Iso8601Utc

        $meta = [ordered]@{
            source_iteration_id          = $SourceIterationId
            review_iteration_id          = $reviewIterationId
            role                         = $Role
            status                       = $reviewStatus
            package_path                 = $relPkg
            started_at_utc               = $startedAt
            completed_at_utc             = $endedAt
            safe_mode                    = $effective.safeMode
            allow_bash                   = $effective.allowBash
            dangerously_skip_permissions = $effective.dangerouslySkipPermissions
            require_unique_artifact      = $effective.requireUniqueArtifact
            sanitize_environment         = $effective.sanitizeEnvironment
            allowed_tools                = $effective.allowedTools
            disallowed_tools             = $effective.disallowedTools
            review_json_path             = $jsonOut
            review_md_path               = $mdOut
            review_json_sha256           = (Get-FileSha256 -Path $jsonOut)
            review_md_sha256             = (Get-FileSha256 -Path $mdOut)
            redaction_report_path        = $redReportPath
            package_quality_path         = $packageQualityPath
            package_quality              = $pqOut
            staging_root                 = $stagingRoot
            errors                       = @($errors)
            run_result                   = $runResult
        }
        $meta | ConvertTo-Json -Depth 16 | Set-Content -Path $metaOut -Encoding UTF8

        return [pscustomobject]@{
            ok                  = ($reviewStatus -eq 'COMPLETED')
            review_iteration_id = $reviewIterationId
            target_iteration_id = $SourceIterationId
            role                = $Role
            status              = $reviewStatus
            review_json_path    = $jsonOut
            review_md_path      = $mdOut
            metadata_path       = $metaOut
            staging_root        = $stagingRoot
            errors              = @($errors)
            effective_profile   = $effective
            package_quality     = $pqOut
        }
    }
    finally {
        if ($null -ne $lock) {
            try { Release-WaggleLock -Path $lockPath -LockId $lock.lock_id | Out-Null } catch {}
        }
    }
}

# ---- CLI wrapper (only when invoked as a script, not dot-sourced) -------

if ($MyInvocation.InvocationName -ne '.') {
    if (-not $ConfigPath -and -not $Role) {
        # If neither was passed, still allow dot-source style usage
        # to define Invoke-WaggleReview without doing anything.
        return
    }
    if (-not $ConfigPath) { throw '-ConfigPath is required' }
    if (-not $Role)       { throw '-Role is required (architect|security|reliability)' }

    $params = @{
        ConfigPath        = $ConfigPath
        ReviewConfigPath  = $ReviewConfigPath
        SourceIterationId = $SourceIterationId
        PackagePath       = $PackagePath
        Role              = $Role
        MaxTurns          = $MaxTurns
        OutputDir         = $OutputDir
        DryRun            = [bool]$DryRun
        NoLock            = [bool]$NoLock
    }
    if ($ClaudeCommandOverride) { $params['ClaudeCommandOverride'] = $ClaudeCommandOverride }

    $r = Invoke-WaggleReview @params
    if ($r.ok) {
        Write-Host ''
        Write-Host ('Review {0} for {1} -> {2}' -f $r.role, $r.target_iteration_id, $r.status) -ForegroundColor Green
        Write-Host ('  review.json : {0}' -f $r.review_json_path)
        Write-Host ('  review.md   : {0}' -f $r.review_md_path)
        Write-Host ('  metadata    : {0}' -f $r.metadata_path)
        exit 0
    } else {
        Write-Host ''
        Write-Host ('Review {0} for {1} -> {2}' -f $r.role, $r.target_iteration_id, $r.status) -ForegroundColor Red
        if ($r.metadata_path) {
            Write-Host ('  metadata    : {0}' -f $r.metadata_path)
        }
        if ($r.errors) {
            foreach ($e in $r.errors) { Write-Host ('  error: {0}' -f $e) -ForegroundColor Red }
        }
        exit 1
    }
}
