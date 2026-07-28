#requires -Version 5.1
<#
.SYNOPSIS
    Smoke test for New-BridgePrReviewWorktree.ps1.

.DESCRIPTION
    Builds a temporary local repository with a synthetic feature head, creates
    a detached PR review worktree from that head, updates the head, and
    verifies that the source repo stays on main across both review operations.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$bridgeBin = $PSScriptRoot
$wrapper = Join-Path $bridgeBin 'New-BridgePrReviewWorktree.ps1'

$results = New-Object System.Collections.Generic.List[object]
function Add-Check {
    param(
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [bool] $Passed,
        [string] $Detail = ''
    )
    [void]$results.Add([pscustomobject]@{
        name = $Name; passed = $Passed; detail = $Detail
    })
    $marker = if ($Passed) { 'PASS' } else { 'FAIL' }
    $color = if ($Passed) { 'Green' } else { 'Red' }
    Write-Host ("  [{0}] {1}" -f $marker, $Name) -ForegroundColor $color
    if ($Detail) { Write-Host "        $Detail" }
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
        throw "$ErrorMessage`n$detail"
    }
    return $output
}

$tempRoot = Join-Path $env:TEMP `
    "bridge-pr-review-worktree-smoke-$([guid]::NewGuid().ToString('N').Substring(0, 12))"
$tempRootFull = [System.IO.Path]::GetFullPath($tempRoot)
$tempParentFull = [System.IO.Path]::GetFullPath($env:TEMP)
$sourceRepoRoot = Join-Path $tempRootFull 'repo'
$worktreeRoot = Join-Path $tempRootFull 'review-worktrees'
$runtimeRoot = Join-Path $tempRootFull 'runtime'
$identityIsolation = Join-Path $PSScriptRoot 'BridgeSmokeIdentityIsolation.ps1'
. $identityIsolation
$identitySnapshot = Enter-BridgeSmokeIdentityIsolation

try {
    Write-Host 'Bridge PR review worktree smoke test' -ForegroundColor Cyan
    Write-Host '===================================='
    Write-Host "Temp root: $tempRootFull"
    Write-Host ''

    if (Test-Path -LiteralPath $tempRootFull) {
        throw "Pre-condition failed: temp root already exists: $tempRootFull"
    }
    [void](New-Item -ItemType Directory -Path $sourceRepoRoot -Force)

    Invoke-GitChecked -Repo $sourceRepoRoot -ArgsToGit @('init') -ErrorMessage 'git init failed' | Out-Null
    Invoke-GitChecked -Repo $sourceRepoRoot -ArgsToGit @('config', 'user.email', 'bridge-smoke@example.invalid') -ErrorMessage 'git config email failed' | Out-Null
    Invoke-GitChecked -Repo $sourceRepoRoot -ArgsToGit @('config', 'user.name', 'Bridge Smoke') -ErrorMessage 'git config name failed' | Out-Null
    Set-Content -LiteralPath (Join-Path $sourceRepoRoot 'README.md') -Value 'smoke' -Encoding UTF8
    Invoke-GitChecked -Repo $sourceRepoRoot -ArgsToGit @('add', 'README.md') -ErrorMessage 'git add failed' | Out-Null
    Invoke-GitChecked -Repo $sourceRepoRoot -ArgsToGit @('commit', '-m', 'initial smoke commit') -ErrorMessage 'git commit failed' | Out-Null
    Invoke-GitChecked -Repo $sourceRepoRoot -ArgsToGit @('branch', '-M', 'main') -ErrorMessage 'git branch -M main failed' | Out-Null

    Invoke-GitChecked -Repo $sourceRepoRoot -ArgsToGit @('checkout', '-b', 'feature/pr-review') -ErrorMessage 'git checkout feature failed' | Out-Null
    Set-Content -LiteralPath (Join-Path $sourceRepoRoot 'feature.txt') -Value 'first review head' -Encoding UTF8
    Invoke-GitChecked -Repo $sourceRepoRoot -ArgsToGit @('add', 'feature.txt') -ErrorMessage 'git add feature failed' | Out-Null
    Invoke-GitChecked -Repo $sourceRepoRoot -ArgsToGit @('commit', '-m', 'feature head one') -ErrorMessage 'git commit feature one failed' | Out-Null
    $featureCommitOne = [string](@(Invoke-GitChecked -Repo $sourceRepoRoot -ArgsToGit @('rev-parse', 'HEAD') -ErrorMessage 'git rev-parse one failed') -join "`n").Trim()
    Invoke-GitChecked -Repo $sourceRepoRoot -ArgsToGit @('checkout', 'main') -ErrorMessage 'git checkout main failed' | Out-Null

    $sourceBranchBefore = [string](@(Invoke-GitChecked -Repo $sourceRepoRoot -ArgsToGit @('branch', '--show-current') -ErrorMessage 'git branch before failed') -join "`n").Trim()
    $reviewOne = & $wrapper `
        -Agent codex `
        -PullRequest 7 `
        -SourceRepoRoot $sourceRepoRoot `
        -WorktreeRoot $worktreeRoot `
        -RuntimeRoot $runtimeRoot `
        -ReviewRef $featureCommitOne `
        -SkipFetch
    $sourceBranchAfterOne = [string](@(Invoke-GitChecked -Repo $sourceRepoRoot -ArgsToGit @('branch', '--show-current') -ErrorMessage 'git branch after one failed') -join "`n").Trim()
    $reviewHeadOne = [string](@(Invoke-GitChecked -Repo ([string]$reviewOne.worktree_path) -ArgsToGit @('rev-parse', 'HEAD') -ErrorMessage 'review head one failed') -join "`n").Trim()
    $reviewBranchOne = [string](@(Invoke-GitChecked -Repo ([string]$reviewOne.worktree_path) -ArgsToGit @('branch', '--show-current') -ErrorMessage 'review branch one failed') -join "`n").Trim()

    Add-Check 'review worktree created' `
        (Test-Path -LiteralPath (Join-Path ([string]$reviewOne.worktree_path) '.git')) `
        ([string]$reviewOne.worktree_path)
    Add-Check 'review worktree is detached' `
        (-not $reviewBranchOne) `
        "branch=$reviewBranchOne"
    Add-Check 'review worktree points at first head' `
        ($reviewHeadOne -eq $featureCommitOne) `
        $reviewHeadOne
    Add-Check 'source repo stayed on main after first review' `
        (($sourceBranchBefore -eq 'main') -and ($sourceBranchAfterOne -eq 'main')) `
        "before=$sourceBranchBefore after=$sourceBranchAfterOne"
    Add-Check 'review file is not visible in source main worktree' `
        ((Test-Path -LiteralPath (Join-Path ([string]$reviewOne.worktree_path) 'feature.txt')) -and
         (-not (Test-Path -LiteralPath (Join-Path $sourceRepoRoot 'feature.txt')))) `
        'feature.txt isolated to review worktree'

    Invoke-GitChecked -Repo $sourceRepoRoot -ArgsToGit @('checkout', 'feature/pr-review') -ErrorMessage 'git checkout feature for update failed' | Out-Null
    Set-Content -LiteralPath (Join-Path $sourceRepoRoot 'feature.txt') -Value 'second review head' -Encoding UTF8
    Invoke-GitChecked -Repo $sourceRepoRoot -ArgsToGit @('add', 'feature.txt') -ErrorMessage 'git add feature update failed' | Out-Null
    Invoke-GitChecked -Repo $sourceRepoRoot -ArgsToGit @('commit', '-m', 'feature head two') -ErrorMessage 'git commit feature two failed' | Out-Null
    $featureCommitTwo = [string](@(Invoke-GitChecked -Repo $sourceRepoRoot -ArgsToGit @('rev-parse', 'HEAD') -ErrorMessage 'git rev-parse two failed') -join "`n").Trim()
    Invoke-GitChecked -Repo $sourceRepoRoot -ArgsToGit @('checkout', 'main') -ErrorMessage 'git checkout main after update failed' | Out-Null

    $reviewTwo = & $wrapper `
        -Agent codex `
        -PullRequest 7 `
        -SourceRepoRoot $sourceRepoRoot `
        -WorktreeRoot $worktreeRoot `
        -RuntimeRoot $runtimeRoot `
        -ReviewRef $featureCommitTwo `
        -SkipFetch
    $sourceBranchAfterTwo = [string](@(Invoke-GitChecked -Repo $sourceRepoRoot -ArgsToGit @('branch', '--show-current') -ErrorMessage 'git branch after two failed') -join "`n").Trim()
    $reviewHeadTwo = [string](@(Invoke-GitChecked -Repo ([string]$reviewTwo.worktree_path) -ArgsToGit @('rev-parse', 'HEAD') -ErrorMessage 'review head two failed') -join "`n").Trim()

    Add-Check 'existing review worktree reused' `
        ((-not [bool]$reviewTwo.created) -and ([string]$reviewTwo.worktree_path -eq [string]$reviewOne.worktree_path)) `
        "created=$($reviewTwo.created) path=$($reviewTwo.worktree_path)"
    Add-Check 'review worktree updated to second head' `
        (([bool]$reviewTwo.updated) -and ($reviewHeadTwo -eq $featureCommitTwo)) `
        $reviewHeadTwo
    Add-Check 'source repo stayed on main after review update' `
        ($sourceBranchAfterTwo -eq 'main') `
        "after=$sourceBranchAfterTwo"
} finally {
    Exit-BridgeSmokeIdentityIsolation -Snapshot $identitySnapshot
    if (Test-Path -LiteralPath $tempRootFull) {
        $safeTempChild = $tempRootFull.StartsWith(
            $tempParentFull.TrimEnd('\') + '\',
            [System.StringComparison]::OrdinalIgnoreCase
        ) -and ((Split-Path -Leaf $tempRootFull) -like 'bridge-pr-review-worktree-smoke-*')
        if (-not $safeTempChild) {
            throw "Refusing cleanup outside generated temp root: $tempRootFull"
        }
        Remove-Item -LiteralPath $tempRootFull -Recurse -Force
        Write-Host ''
        Write-Host "Cleanup: removed $tempRootFull"
    }
}

Write-Host ''
Write-Host 'Summary' -ForegroundColor Cyan
Write-Host '======='
$failed = @($results | Where-Object { -not $_.passed })
$passed = @($results | Where-Object { $_.passed })
Write-Host ("  passed: {0}" -f $passed.Count) -ForegroundColor Green
if ($failed.Count -gt 0) {
    Write-Host ("  failed: {0}" -f $failed.Count) -ForegroundColor Red
    foreach ($f in $failed) {
        Write-Host ("    - {0}: {1}" -f $f.name, $f.detail) -ForegroundColor Red
    }
    exit 1
}
exit 0
