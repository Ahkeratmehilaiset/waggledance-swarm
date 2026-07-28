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

    The shared runtime-data root remains separate from the canonical source
    repo; only the git working tree is split per agent/task.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $Agent,

    [string] $TaskId = '',
    [string] $SourceRepoRoot = 'C:\Python\project2',
    [string] $WorktreeRoot = 'C:\Python\waggledance-agent-worktrees',
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

$sessionIdentity = Join-Path $PSScriptRoot 'AgentBridgeSessionIdentity.ps1'
. $sessionIdentity
Assert-AgentBridgeSessionIdentity -RequestedAgent $Agent

function Resolve-FullPath {
    param([Parameter(Mandatory)] [string] $Path)
    return [System.IO.Path]::GetFullPath($Path)
}

function New-DefaultTaskId {
    param([Parameter(Mandatory)] [string] $AgentName)
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
    return "$AgentName-session-$stamp"
}

function ConvertTo-WorktreeTaskSlug {
    param([Parameter(Mandatory)] [string] $Value)
    $slug = ($Value.ToLowerInvariant() -replace '[^a-z0-9._-]+', '-').Trim('-')
    if (-not $slug) { throw "TaskId does not produce a safe slug: $Value" }
    if ($slug.Length -gt 80) { $slug = $slug.Substring(0, 80).Trim('-') }
    return $slug
}

function Assert-CompatibleSessionIdentityBundle {
    param(
        [Parameter(Mandatory)] [string] $IdentityText,
        [Parameter(Mandatory)] [string] $SessionText,
        [Parameter(Mandatory)] [string] $Source
    )

    if ($IdentityText.IndexOf(
            "AgentBridgeSessionIdentityContract = 'v1'",
            [System.StringComparison]::Ordinal
        ) -lt 0 -or
        $SessionText.IndexOf(
            'Assert-AgentBridgeSessionIdentity -RequestedAgent $Agent',
            [System.StringComparison]::Ordinal
        ) -lt 0 -or
        $SessionText.IndexOf(
            '$env:AGENT_BRIDGE_AGENT = $Agent',
            [System.StringComparison]::Ordinal
        ) -lt 0) {
        throw "incompatible identity-bound session bundle at $Source"
    }
}

function Read-GitBundleFile {
    param(
        [Parameter(Mandatory)] [string] $Repo,
        [Parameter(Mandatory)] [string] $Ref,
        [Parameter(Mandatory)] [string] $RelativePath
    )

    $spec = '{0}:{1}' -f $Ref, $RelativePath
    $previousEap = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $content = @(& git -C $Repo show $spec 2>$null)
        $gitExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousEap
    }
    if ($gitExitCode -ne 0) {
        throw "identity-bound session bundle is missing $RelativePath at git ref $Ref"
    }
    return ($content -join "`n")
}

if ($RunId -and $RunId -notmatch '^[A-Za-z0-9._:-]{1,128}$') {
    throw 'run_id must match ^[A-Za-z0-9._:-]{1,128}$'
}
if ($Role -and $Role -cnotmatch '^[a-z][a-z0-9_-]{1,32}$') {
    throw 'role must match ^[a-z][a-z0-9_-]{1,32}$'
}
if ($AgentUuid -and $AgentUuid -notmatch '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$') {
    throw 'agent_uuid must be a UUID'
}
if ($AgentUuid) {
    $AgentUuid = $AgentUuid.ToLowerInvariant()
}
$Capabilities = @(
    @($Capabilities) |
        ForEach-Object { [string]$_ -split '[,;]' } |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ }
)
foreach ($capability in @($Capabilities)) {
    if ($capability -cnotmatch '^[a-z][a-z0-9_.:-]{1,64}$') {
        throw 'capability must match ^[a-z][a-z0-9_.:-]{1,64}$'
    }
}

$sourceFull = Resolve-FullPath $SourceRepoRoot
$runtimeFull = Resolve-FullPath $RuntimeRoot

if (-not $TaskId) {
    $TaskId = New-DefaultTaskId -AgentName $Agent
}
$effectiveBranch = if ($Branch) {
    $Branch
} else {
    "waggledance/$Agent/$(ConvertTo-WorktreeTaskSlug -Value $TaskId)"
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

$previousEap = $ErrorActionPreference
$effectiveBranchExists = $false
try {
    # A missing branch is an expected probe result. Keep pwsh's optional
    # native-command error promotion from bypassing the Base preflight.
    $ErrorActionPreference = 'Continue'
    & git -C $sourceFull show-ref --verify --quiet "refs/heads/$effectiveBranch" `
        2>$null
    $effectiveBranchExists = $LASTEXITCODE -eq 0
} finally {
    $ErrorActionPreference = $previousEap
}
$bundleRef = if ($effectiveBranchExists) { $effectiveBranch } else { $Base }
$baseIdentityText = Read-GitBundleFile `
    -Repo $sourceFull `
    -Ref $bundleRef `
    -RelativePath '.agent-bridge/bin/AgentBridgeSessionIdentity.ps1'
$baseSessionText = Read-GitBundleFile `
    -Repo $sourceFull `
    -Ref $bundleRef `
    -RelativePath '.agent-bridge/bin/Start-AgentBridgeSession.ps1'
Assert-CompatibleSessionIdentityBundle `
    -IdentityText $baseIdentityText `
    -SessionText $baseSessionText `
    -Source "git ref $bundleRef"

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
$targetIdentityHelper = Join-Path $worktreePath '.agent-bridge\bin\AgentBridgeSessionIdentity.ps1'
if (-not (Test-Path -LiteralPath $startSession -PathType Leaf)) {
    throw "Start-AgentBridgeSession.ps1 not found in created worktree: $startSession"
}
if (-not (Test-Path -LiteralPath $targetIdentityHelper -PathType Leaf)) {
    throw "identity-bound session bundle is missing its helper: $targetIdentityHelper"
}
$targetIdentityText = Get-Content -Raw -LiteralPath $targetIdentityHelper -Encoding UTF8
$targetSessionText = Get-Content -Raw -LiteralPath $startSession -Encoding UTF8
Assert-CompatibleSessionIdentityBundle `
    -IdentityText $targetIdentityText `
    -SessionText $targetSessionText `
    -Source $worktreePath

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
