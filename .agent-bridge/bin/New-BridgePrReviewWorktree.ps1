#requires -Version 5.1
<#
.SYNOPSIS
    Create or reuse a detached worktree for reviewing a pull request.

.DESCRIPTION
    PR reviews often need to inspect a foreign head. Doing that in the
    primary bridge repo can move the branch under another agent. This helper
    fetches the PR head into a local review ref, creates a detached review
    worktree, and verifies that the source repo branch did not move.

    The review worktree is intentionally detached. It is for inspection,
    tests, and review comments, not for committing product changes.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $Agent,

    [Parameter(Mandatory)]
    [ValidateRange(1, 2147483647)]
    [int] $PullRequest,

    [string] $SourceRepoRoot = 'C:\Python\project2',
    [string] $WorktreeRoot = 'C:\Python\waggledance-pr-review-worktrees',
    [string] $RuntimeRoot = 'C:\Python\project2-master\.agent-bridge',
    [string] $Remote = 'origin',
    [string] $ReviewRef = '',

    [switch] $SkipFetch
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

function ConvertTo-SafeSlug {
    param([Parameter(Mandatory)] [string] $Value)
    $slug = ($Value.ToLowerInvariant() -replace '[^a-z0-9._-]+', '-').Trim('-')
    if (-not $slug) { throw "value does not produce a safe slug: $Value" }
    if ($slug.Length -gt 96) { $slug = $slug.Substring(0, 96).Trim('-') }
    return $slug
}

function Invoke-GitChecked {
    param(
        [Parameter(Mandatory)] [string] $Repo,
        [Parameter(Mandatory)] [string[]] $ArgsToGit,
        [Parameter(Mandatory)] [string] $ErrorMessage
    )

    $previousEAP = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = & git -C $Repo @ArgsToGit 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousEAP
    }
    if ($code -ne 0) {
        $detail = (($output | ForEach-Object { [string]$_ }) -join "`n")
        if ($detail) {
            throw "$ErrorMessage`n$detail"
        }
        throw $ErrorMessage
    }
    return $output
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
$tempDrive = [System.IO.Path]::GetPathRoot($env:TEMP)
if ($sourceDrive -and $sourceDrive.TrimEnd('\') -ne 'C:') {
    throw "source repo root must be on persistent C: drive: $sourceFull"
}
if ($worktreeDrive -and ($worktreeDrive.TrimEnd('\') -notin @('C:', $tempDrive.TrimEnd('\')))) {
    throw "worktree root must be on persistent C: drive or TEMP: $rootFull"
}

if (-not $ReviewRef) {
    $ReviewRef = "refs/remotes/$Remote/pr-$PullRequest"
}

$safeAgent = ConvertTo-SafeSlug $Agent
$worktreePath = Join-Path $rootFull "$safeAgent-pr-$PullRequest-review"

if (-not (Test-Path -LiteralPath $rootFull -PathType Container)) {
    [void](New-Item -ItemType Directory -Path $rootFull -Force)
}

$sourceBranchBefore = [string](@(Invoke-GitChecked `
    -Repo $sourceFull `
    -ArgsToGit @('branch', '--show-current') `
    -ErrorMessage 'could not inspect source repo branch before review worktree operation') -join "`n").Trim()

try {
    if (-not $SkipFetch) {
        $fetchSpec = "+refs/pull/$PullRequest/head:$ReviewRef"
        Invoke-GitChecked `
            -Repo $sourceFull `
            -ArgsToGit @('fetch', $Remote, $fetchSpec) `
            -ErrorMessage "git fetch failed for PR #$PullRequest from $Remote" | Out-Null
    }

    $targetCommit = [string](@(Invoke-GitChecked `
        -Repo $sourceFull `
        -ArgsToGit @('rev-parse', '--verify', "${ReviewRef}^{commit}") `
        -ErrorMessage "review ref does not resolve to a commit: $ReviewRef") -join "`n").Trim()

    $created = $false
    $updated = $false
    if (Test-Path -LiteralPath $worktreePath -PathType Container) {
        $gitFile = Join-Path $worktreePath '.git'
        if (-not (Test-Path -LiteralPath $gitFile)) {
            throw "target path exists but is not a git worktree: $worktreePath"
        }

        $dirty = @(Invoke-GitChecked `
            -Repo $worktreePath `
            -ArgsToGit @('status', '--porcelain') `
            -ErrorMessage "could not inspect review worktree status: $worktreePath")
        if ($dirty.Count -gt 0) {
            throw "review worktree has local changes; refusing to move detached HEAD: $worktreePath"
        }

        $currentHead = [string](@(Invoke-GitChecked `
            -Repo $worktreePath `
            -ArgsToGit @('rev-parse', 'HEAD') `
            -ErrorMessage "could not inspect review worktree HEAD: $worktreePath") -join "`n").Trim()
        if ($currentHead -ne $targetCommit) {
            Invoke-GitChecked `
                -Repo $worktreePath `
                -ArgsToGit @('checkout', '--detach', $targetCommit) `
                -ErrorMessage "could not update detached review worktree: $worktreePath" | Out-Null
            $updated = $true
        }
    } else {
        Invoke-GitChecked `
            -Repo $sourceFull `
            -ArgsToGit @('worktree', 'add', '--detach', $worktreePath, $targetCommit) `
            -ErrorMessage "git worktree add failed for review path: $worktreePath" | Out-Null
        $created = $true
    }
} finally {
    $sourceBranchAfter = [string](@(Invoke-GitChecked `
        -Repo $sourceFull `
        -ArgsToGit @('branch', '--show-current') `
        -ErrorMessage 'could not inspect source repo branch after review worktree operation') -join "`n").Trim()
}

if ($sourceBranchAfter -ne $sourceBranchBefore) {
    throw "source repo branch moved during PR review worktree operation: before=$sourceBranchBefore after=$sourceBranchAfter"
}

$headCommit = [string](@(Invoke-GitChecked `
    -Repo $worktreePath `
    -ArgsToGit @('rev-parse', 'HEAD') `
    -ErrorMessage "could not inspect final review worktree HEAD: $worktreePath") -join "`n").Trim()
$reviewBranch = [string](@(Invoke-GitChecked `
    -Repo $worktreePath `
    -ArgsToGit @('branch', '--show-current') `
    -ErrorMessage "could not inspect final review worktree branch: $worktreePath") -join "`n").Trim()

[pscustomobject]@{
    agent                = $Agent
    pull_request         = $PullRequest
    source_repo_root     = $sourceFull
    worktree_path        = $worktreePath
    review_ref           = $ReviewRef
    head_commit          = $headCommit
    detached             = (-not $reviewBranch)
    created              = $created
    updated              = $updated
    source_branch_before = $sourceBranchBefore
    source_branch_after  = $sourceBranchAfter
    runtime_root         = $runtimeFull
    review_command       = "Set-Location -LiteralPath '$worktreePath'"
    note                 = 'Review worktree is detached at the PR head; primary repo branch was unchanged.'
}
