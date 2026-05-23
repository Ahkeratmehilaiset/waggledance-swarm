#requires -Version 5.1
<#
.SYNOPSIS
    Create or reuse a dedicated agent worktree and bootstrap the bridge there.

.DESCRIPTION
    This is the preferred one-command startup for new write-capable Claude and
    Codex sessions. Dot-source it from the primary repo shell so the current
    process keeps AGENT_BRIDGE_RUNTIME_ROOT, AGENT_BRIDGE_RUN_ID, and the new
    worktree location after the script returns:

      . .\.agent-bridge\bin\Start-AgentBridgeWorktreeSession.ps1 -Agent codex

    The shared runtime root remains the primary repo's .agent-bridge directory;
    only the git working tree is split per agent/task.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateScript({ $_ -cmatch '^[a-z][a-z0-9_-]{1,32}$' })]
    [string] $Agent,

    [string] $TaskId = '',
    [string] $SourceRepoRoot = 'C:\Python\project2-master',
    [string] $WorktreeRoot = 'C:\tmp\waggledance-agent-worktrees',
    [string] $Base = 'origin/main',
    [string] $Branch = '',
    [string] $RuntimeRoot = 'C:\Python\project2-master\.agent-bridge',
    [string] $RunId = '',
    [string] $Role = '',
    [string] $AgentUuid = '',
    [string[]] $Capabilities = @(),

    [switch] $Fetch,
    [switch] $SkipBridgeRead,
    [switch] $SkipLiveness,
    [switch] $SkipGitStatus,
    [switch] $SkipWakeWatcher,
    [switch] $SkipHeartbeatJob
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Resolve-FullPath {
    param([Parameter(Mandatory)] [string] $Path)
    return [System.IO.Path]::GetFullPath($Path)
}

function New-DefaultTaskId {
    param([Parameter(Mandatory)] [string] $AgentName)
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
    return "$AgentName-session-$stamp"
}

$sourceFull = Resolve-FullPath $SourceRepoRoot
$runtimeFull = Resolve-FullPath $RuntimeRoot

if (-not $TaskId) {
    $TaskId = New-DefaultTaskId -AgentName $Agent
}

$newWorktree = Join-Path $sourceFull '.agent-bridge\bin\New-AgentBridgeWorktree.ps1'
if (-not (Test-Path -LiteralPath $newWorktree -PathType Leaf)) {
    throw "New-AgentBridgeWorktree.ps1 not found under source repo: $newWorktree"
}

if ($Fetch) {
    & git -C $sourceFull fetch origin main
    if ($LASTEXITCODE -ne 0) {
        throw "git fetch origin main failed in $sourceFull"
    }
}

$worktreeArgs = @{
    Agent = $Agent
    TaskId = $TaskId
    SourceRepoRoot = $sourceFull
    WorktreeRoot = $WorktreeRoot
    Base = $Base
    RuntimeRoot = $runtimeFull
}
if ($Branch) {
    $worktreeArgs.Branch = $Branch
}

$worktree = & $newWorktree @worktreeArgs
$worktreePath = Resolve-FullPath ([string]$worktree.worktree_path)
$startSession = Join-Path $worktreePath '.agent-bridge\bin\Start-AgentBridgeSession.ps1'
if (-not (Test-Path -LiteralPath $startSession -PathType Leaf)) {
    throw "Start-AgentBridgeSession.ps1 not found in created worktree: $startSession"
}

Set-Location -LiteralPath $worktreePath

$sessionArgs = @{
    Agent = $Agent
    RuntimeRoot = $runtimeFull
    RepoRoot = $worktreePath
    PrimaryRepoRoot = $sourceFull
    RequireDedicatedWorktree = $true
}
if ($RunId) {
    $sessionArgs.RunId = $RunId
}
if ($Role) {
    $sessionArgs.Role = $Role
}
if ($AgentUuid) {
    $sessionArgs.AgentUuid = $AgentUuid
}
if (@($Capabilities).Count -gt 0) {
    $sessionArgs.Capabilities = @($Capabilities)
}
if ($SkipBridgeRead) {
    $sessionArgs.SkipBridgeRead = $true
}
if ($SkipLiveness) {
    $sessionArgs.SkipLiveness = $true
}
if ($SkipGitStatus) {
    $sessionArgs.SkipGitStatus = $true
}
if ($SkipWakeWatcher) {
    $sessionArgs.SkipWakeWatcher = $true
}
if ($SkipHeartbeatJob) {
    $sessionArgs.SkipHeartbeatJob = $true
}

$session = . $startSession @sessionArgs

[pscustomobject]@{
    agent = $Agent
    task_id = $TaskId
    source_repo_root = $sourceFull
    worktree_path = $worktreePath
    branch = [string]$worktree.branch
    created = [bool]$worktree.created
    runtime_root = $runtimeFull
    run_id = [string]$session.run_id
    role = [string]$session.role
    agent_uuid = [string]$session.agent_uuid
    capabilities = @($session.capabilities)
    dedicated_worktree = [bool]$session.dedicated_worktree
    git_branch = [string]$session.git_branch
    wake_job_id = [string]$session.wake_job_id
    heartbeat_job_id = [string]$session.heartbeat_job_id
    note = 'Dot-source this script so env vars and Set-Location persist in the agent shell.'
}
