#requires -Version 5.1
<#
.SYNOPSIS
    Bootstrap a Claude/Codex bridge session after reboot.

.DESCRIPTION
    Creates the shared bridge runtime root, sets the process
    AGENT_BRIDGE_RUNTIME_ROOT and AGENT_BRIDGE_RUN_ID variables, moves to
    the repo root, and emits a liveness/active event.

    Dot-source this script from a new agent shell so the environment variables
    remain available to Claude Code or Codex after the script returns:

      . .\.agent-bridge\bin\Start-AgentBridgeSession.ps1 -Agent codex
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateScript({ $_ -cmatch '^[a-z][a-z0-9_-]{1,32}$' })]
    [string] $Agent,

    [string] $RuntimeRoot = 'C:\Python\project2-master\.agent-bridge',
    [string] $RepoRoot = '',
    [string] $RunId = '',

    [switch] $SkipBridgeRead,
    [switch] $SkipLiveness,
    [switch] $SkipGitStatus,
    [switch] $SkipWakeWatcher,
    [switch] $SkipHeartbeatJob,
    [switch] $RequireDedicatedWorktree,
    [string] $PrimaryRepoRoot = 'C:\Python\project2-master'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Resolve-FullPath {
    param([Parameter(Mandatory)] [string] $Path)
    return [System.IO.Path]::GetFullPath($Path)
}

$bridgeRoot = Split-Path -Parent $PSScriptRoot
if (-not $RepoRoot) {
    $RepoRoot = Split-Path -Parent $bridgeRoot
}

$repoFull = Resolve-FullPath $RepoRoot
$runtimeFull = Resolve-FullPath $RuntimeRoot
$primaryRepoFull = Resolve-FullPath $PrimaryRepoRoot

if (-not (Test-Path -LiteralPath $repoFull -PathType Container)) {
    throw "repo root does not exist: $repoFull"
}
if (-not (Test-Path -LiteralPath (Join-Path $repoFull '.git'))) {
    throw "repo root has no .git; refusing bridge bootstrap: $repoFull"
}

$repoDrive = [System.IO.Path]::GetPathRoot($repoFull)
if ($repoDrive -and $repoDrive.TrimEnd('\') -ne 'C:') {
    throw "repo root must be on persistent C: drive: $repoFull"
}

$isDedicatedWorktree = -not $repoFull.TrimEnd('\').Equals(
    $primaryRepoFull.TrimEnd('\'),
    [System.StringComparison]::OrdinalIgnoreCase
)
if ($RequireDedicatedWorktree -and -not $isDedicatedWorktree) {
    throw ((
        "dedicated agent worktree required; refusing to bootstrap in primary shared repo: {0}. " +
        "Create one with .\.agent-bridge\bin\New-AgentBridgeWorktree.ps1 first."
    ) -f $repoFull)
}

$runtimeDirs = @(
    $runtimeFull,
    (Join-Path $runtimeFull 'shared'),
    (Join-Path $runtimeFull 'work_queue'),
    (Join-Path $runtimeFull 'work_queue\claims'),
    (Join-Path $runtimeFull 'work_queue\done'),
    (Join-Path $runtimeFull 'outbox'),
    (Join-Path $runtimeFull "outbox\$Agent"),
    (Join-Path $runtimeFull 'inbox'),
    (Join-Path $runtimeFull "inbox\$Agent")
)
foreach ($dir in $runtimeDirs) {
    if (-not (Test-Path -LiteralPath $dir -PathType Container)) {
        [void](New-Item -ItemType Directory -Path $dir -Force -ErrorAction Stop)
    }
}

if (-not $RunId) {
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
    $RunId = "$Agent-$stamp"
}

$env:AGENT_BRIDGE_RUNTIME_ROOT = $runtimeFull
$env:AGENT_BRIDGE_RUN_ID = $RunId

Set-Location -LiteralPath $repoFull

$gitBranch = ''
if (-not $SkipGitStatus) {
    try {
        $gitBranch = (& git branch --show-current 2>$null)
        if ($LASTEXITCODE -ne 0) { $gitBranch = '' }
    } catch {
        $gitBranch = ''
    }
}

if (-not $SkipLiveness) {
    $sendLiveness = Join-Path $PSScriptRoot 'Send-Liveness.ps1'
    & $sendLiveness `
        -Agent $Agent `
        -State active `
        -TaskId "$Agent-session-bootstrap-$((Get-Date).ToUniversalTime().ToString('yyyy-MM-dd'))" `
        -Message "$Agent session bootstrapped; runtime_root=$runtimeFull; repo_root=$repoFull; dedicated_worktree=$isDedicatedWorktree" |
        Out-Null
}

if (-not $SkipBridgeRead) {
    $readBridge = Join-Path $PSScriptRoot 'Read-AgentBridge.ps1'
    $bridgeStatus = Join-Path $PSScriptRoot 'Get-AgentBridgeStatus.ps1'
    & $readBridge -Agent $Agent -ShowClaims -Tail 80
    & $bridgeStatus -MaxUnresolved 15
}

function Stop-AgentBridgeExistingJob {
    param(
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [string] $EnvName
    )

    $jobsById = @{}
    $envJobId = [Environment]::GetEnvironmentVariable($EnvName, 'Process')
    if ($envJobId -and $envJobId -match '^\d+$') {
        try {
            $job = Get-Job -Id ([int]$envJobId) -ErrorAction SilentlyContinue
            if ($job -and $job.Name -eq $Name) {
                $jobsById[[int]$job.Id] = $job
            }
        } catch {}
    }

    foreach ($job in @(Get-Job -Name $Name -ErrorAction SilentlyContinue)) {
        $jobsById[[int]$job.Id] = $job
    }

    foreach ($job in @($jobsById.Values)) {
        try {
            Stop-Job -Job $job -ErrorAction SilentlyContinue
            Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
        } catch {
            Write-Warning "Start-AgentBridgeSession: could not replace existing job '$($job.Name)' id=$($job.Id): $($_.Exception.Message)"
        }
    }

    if ($jobsById.Count -gt 0) {
        Remove-Item -Path ("Env:{0}" -f $EnvName) -ErrorAction SilentlyContinue
    }
}

# R23.0: launch the wake-on-event watcher as a background job so the
# agent's polling loop can react in <2 s instead of waiting on its own
# cadence. Honors $env:WAGGLE_BRIDGE_WAKE_ENABLED=0 as a kill switch
# (Watch-Bridge.ps1 short-circuits on that). The job is recorded in
# $env:AGENT_BRIDGE_WAKE_JOB so the agent shell can stop it on exit.
$wakeJobId = ''
$wakeEnabled = $env:WAGGLE_BRIDGE_WAKE_ENABLED -ne '0'
if ((-not $SkipWakeWatcher) -and $wakeEnabled) {
    $watchScript = Join-Path $PSScriptRoot 'Watch-Bridge.ps1'
    if (Test-Path -LiteralPath $watchScript) {
        try {
            $wakeJobName = "agent-bridge-watcher-$Agent"
            Stop-AgentBridgeExistingJob -Name $wakeJobName -EnvName 'AGENT_BRIDGE_WAKE_JOB'
            $job = Start-Job -Name $wakeJobName -ScriptBlock {
                param($scriptPath, $agentArg, $runtimeArg)
                & $scriptPath -Agent $agentArg -RuntimeRoot $runtimeArg
            } -ArgumentList $watchScript, $Agent, $runtimeFull
            $wakeJobId = $job.Id
            $env:AGENT_BRIDGE_WAKE_JOB = [string]$wakeJobId
        } catch {
            Write-Warning "Start-AgentBridgeSession: could not start wake watcher: $($_.Exception.Message)"
        }
    }
}

# R23.1: launch an agent heartbeat job so active claims do not expire
# during long model turns or test runs. The job uses Send-Liveness.ps1
# every 60 s, which bumps last_heartbeat_utc for this agent's active
# claims. Operators can opt out via WAGGLE_BRIDGE_HEARTBEAT_ENABLED=0
# or -SkipHeartbeatJob.
$heartbeatJobId = ''
$heartbeatEnabled = $env:WAGGLE_BRIDGE_HEARTBEAT_ENABLED -ne '0'
if ((-not $SkipHeartbeatJob) -and $heartbeatEnabled) {
    $heartbeatScript = Join-Path $PSScriptRoot 'Start-BridgeHeartbeat.ps1'
    if (Test-Path -LiteralPath $heartbeatScript) {
        try {
            $heartbeatJobName = "agent-bridge-heartbeat-$Agent"
            Stop-AgentBridgeExistingJob -Name $heartbeatJobName -EnvName 'AGENT_BRIDGE_HEARTBEAT_JOB'
            $job = Start-Job -Name $heartbeatJobName -ScriptBlock {
                param($scriptPath, $agentArg, $runtimeArg)
                & $scriptPath -Agent $agentArg -RuntimeRoot $runtimeArg
            } -ArgumentList $heartbeatScript, $Agent, $runtimeFull
            $heartbeatJobId = $job.Id
            $env:AGENT_BRIDGE_HEARTBEAT_JOB = [string]$heartbeatJobId
        } catch {
            Write-Warning "Start-AgentBridgeSession: could not start heartbeat job: $($_.Exception.Message)"
        }
    }
}

# R23.1.1: register PowerShell.Exiting handler so the wake-watcher and
# heartbeat background jobs are stopped when the agent shell exits
# normally. Without this, the jobs orphan: a dead Codex/Claude session
# keeps emitting liveness/heartbeat events and bumping last_heartbeat_utc
# on its claims, defeating the stale-lease auto-release contract.
#
# The exit handler covers normal shutdown (`exit`, end-of-script, host
# close); it does NOT fire on Ctrl+C kill or process termination. For
# those cases, the operator runs Stop-AgentBridgeSession.ps1 manually
# (see runbook in BRIDGE_PROTOCOL.md). Idempotent via a session-scoped
# flag: dot-sourcing the same Start-AgentBridgeSession.ps1 twice in one
# shell registers only one cleanup handler.
$cleanupAlreadyRegistered = $null -ne `
    (Get-Variable -Name '__AgentBridgeCleanupRegistered' `
        -Scope Global -ErrorAction SilentlyContinue)
if (-not $cleanupAlreadyRegistered) {
    [void](Register-EngineEvent -SourceIdentifier 'PowerShell.Exiting' `
        -SupportEvent -Action {
        foreach ($envName in @('AGENT_BRIDGE_WAKE_JOB', 'AGENT_BRIDGE_HEARTBEAT_JOB')) {
            $jobId = [Environment]::GetEnvironmentVariable($envName, 'Process')
            if (-not $jobId) { continue }
            try {
                $job = Get-Job -Id $jobId -ErrorAction SilentlyContinue
                if ($job) {
                    Stop-Job -Job $job -ErrorAction SilentlyContinue
                    Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
                }
            } catch {}
        }
    })
    Set-Variable -Name '__AgentBridgeCleanupRegistered' -Scope Global -Value $true
}

[pscustomobject]@{
    agent          = $Agent
    repo_root      = $repoFull
    primary_repo_root = $primaryRepoFull
    dedicated_worktree = $isDedicatedWorktree
    runtime_root   = $runtimeFull
    run_id         = $RunId
    git_branch     = $gitBranch
    wake_job_id    = $wakeJobId
    wake_enabled   = $wakeEnabled
    heartbeat_job_id = $heartbeatJobId
    heartbeat_enabled = $heartbeatEnabled
    note           = 'Dot-source this script so env vars persist in the agent shell.'
}
