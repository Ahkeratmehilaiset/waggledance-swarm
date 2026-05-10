#requires -Version 5.1
<#
.SYNOPSIS
    Create or reuse a dedicated per-agent git worktree.

.DESCRIPTION
    R23.2 helper for the structural fix to shared-worktree branch drift.
    It creates a physical worktree per agent/task so Claude and Codex can
    switch branches, commit, and run tests without moving each other's
    working directory.

    The bridge runtime root stays shared through AGENT_BRIDGE_RUNTIME_ROOT;
    only the git working tree is split.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet('codex','claude','operator','system')]
    [string] $Agent,

    [Parameter(Mandatory)]
    [string] $TaskId,

    [string] $SourceRepoRoot = 'C:\Python\project2-master',
    [string] $WorktreeRoot = 'C:\tmp\waggledance-agent-worktrees',
    [string] $Base = 'origin/main',
    [string] $Branch = '',
    [string] $RuntimeRoot = 'C:\Python\project2-bridge-runtime'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Resolve-FullPath {
    param([Parameter(Mandatory)] [string] $Path)
    return [System.IO.Path]::GetFullPath($Path)
}

function ConvertTo-SafeSlug {
    param([Parameter(Mandatory)] [string] $Value)
    $slug = ($Value.ToLowerInvariant() -replace '[^a-z0-9._-]+', '-').Trim('-')
    if (-not $slug) { throw "TaskId does not produce a safe slug: $Value" }
    if ($slug.Length -gt 80) { $slug = $slug.Substring(0, 80).Trim('-') }
    return $slug
}

$sourceFull = Resolve-FullPath $SourceRepoRoot
$rootFull = Resolve-FullPath $WorktreeRoot
$runtimeFull = Resolve-FullPath $RuntimeRoot

if (-not (Test-Path -LiteralPath $sourceFull -PathType Container)) {
    throw "source repo root does not exist: $sourceFull"
}
if (-not (Test-Path -LiteralPath (Join-Path $sourceFull '.git'))) {
    throw "source repo root has no .git: $sourceFull"
}

$sourceDrive = [System.IO.Path]::GetPathRoot($sourceFull)
$worktreeDrive = [System.IO.Path]::GetPathRoot($rootFull)
if ($sourceDrive -and $sourceDrive.TrimEnd('\') -ne 'C:') {
    throw "source repo root must be on persistent C: drive: $sourceFull"
}
if ($worktreeDrive -and ($worktreeDrive.TrimEnd('\') -notin @('C:', [System.IO.Path]::GetPathRoot($env:TEMP).TrimEnd('\')))) {
    throw "worktree root must be on persistent C: drive or TEMP: $rootFull"
}

$safeTask = ConvertTo-SafeSlug $TaskId
if (-not $Branch) {
    $Branch = "waggledance/$Agent/$safeTask"
}

$safePathBranch = ConvertTo-SafeSlug ($Branch -replace '/', '-')
$worktreePath = Join-Path $rootFull "$Agent-$safePathBranch"

if (-not (Test-Path -LiteralPath $rootFull -PathType Container)) {
    [void](New-Item -ItemType Directory -Path $rootFull -Force)
}

if (Test-Path -LiteralPath $worktreePath -PathType Container) {
    $gitFile = Join-Path $worktreePath '.git'
    if (-not (Test-Path -LiteralPath $gitFile)) {
        throw "target path exists but is not a git worktree: $worktreePath"
    }
    $currentBranch = (& git -C $worktreePath branch --show-current 2>$null)
    if ($LASTEXITCODE -ne 0) {
        throw "could not inspect existing worktree branch: $worktreePath"
    }
    [pscustomobject]@{
        agent = $Agent
        task_id = $TaskId
        worktree_path = $worktreePath
        branch = $currentBranch
        base = $Base
        runtime_root = $runtimeFull
        created = $false
        bootstrap = ". .\.agent-bridge\bin\Start-AgentBridgeSession.ps1 -Agent $Agent -RequireDedicatedWorktree"
    }
    exit 0
}

& git -C $sourceFull show-ref --verify --quiet "refs/heads/$Branch"
$branchExists = ($LASTEXITCODE -eq 0)

if ($branchExists) {
    & git -C $sourceFull worktree add $worktreePath $Branch | Out-Null
} else {
    & git -C $sourceFull worktree add $worktreePath -b $Branch $Base | Out-Null
}
if ($LASTEXITCODE -ne 0) {
    throw "git worktree add failed for $worktreePath"
}

$createdBranch = (& git -C $worktreePath branch --show-current 2>$null)
if ($LASTEXITCODE -ne 0) {
    throw "could not inspect created worktree branch: $worktreePath"
}

[pscustomobject]@{
    agent = $Agent
    task_id = $TaskId
    worktree_path = $worktreePath
    branch = $createdBranch
    base = $Base
    runtime_root = $runtimeFull
    created = $true
    bootstrap = ". .\.agent-bridge\bin\Start-AgentBridgeSession.ps1 -Agent $Agent -RequireDedicatedWorktree"
}
