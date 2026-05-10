#requires -Version 5.1
<#
.SYNOPSIS
    Smoke test for per-agent bridge worktree isolation.

.DESCRIPTION
    Creates a temporary local git repository, then uses
    New-AgentBridgeWorktree.ps1 to create separate codex and claude
    worktrees. Verifies branch isolation and that a marker written in one
    worktree is not visible in the other.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$bridgeBin = $PSScriptRoot
$newWorktree = Join-Path $bridgeBin 'New-AgentBridgeWorktree.ps1'

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

$tempRoot = Join-Path $env:TEMP `
    "bridge-r23-2-worktree-smoke-$([guid]::NewGuid().ToString('N').Substring(0, 12))"
$tempRootFull = [System.IO.Path]::GetFullPath($tempRoot)
$tempParentFull = [System.IO.Path]::GetFullPath($env:TEMP)
$repoRoot = Join-Path $tempRootFull 'repo'
$worktreeRoot = Join-Path $tempRootFull 'worktrees'
$runtimeRoot = Join-Path $tempRootFull 'runtime'

try {
    Write-Host 'Bridge worktree isolation smoke test' -ForegroundColor Cyan
    Write-Host '===================================='
    Write-Host "Temp root: $tempRootFull"
    Write-Host ''

    if (Test-Path -LiteralPath $tempRootFull) {
        throw "Pre-condition failed: temp root already exists: $tempRootFull"
    }
    [void](New-Item -ItemType Directory -Path $repoRoot -Force)

    & git -C $repoRoot init | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'git init failed' }
    & git -C $repoRoot config user.email 'bridge-smoke@example.invalid'
    & git -C $repoRoot config user.name 'Bridge Smoke'
    Set-Content -LiteralPath (Join-Path $repoRoot 'README.md') -Value 'smoke' -Encoding UTF8
    & git -C $repoRoot add README.md
    if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
    & git -C $repoRoot commit -m 'initial smoke commit' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'git commit failed' }
    & git -C $repoRoot branch -M main
    if ($LASTEXITCODE -ne 0) { throw 'git branch -M main failed' }

    $sourceBranchBefore = (& git -C $repoRoot branch --show-current)

    $codex = & $newWorktree `
        -Agent codex `
        -TaskId 'smoke-codex' `
        -SourceRepoRoot $repoRoot `
        -WorktreeRoot $worktreeRoot `
        -RuntimeRoot $runtimeRoot `
        -Base HEAD `
        -Branch 'waggledance/codex/smoke-codex'
    $claude = & $newWorktree `
        -Agent claude `
        -TaskId 'smoke-claude' `
        -SourceRepoRoot $repoRoot `
        -WorktreeRoot $worktreeRoot `
        -RuntimeRoot $runtimeRoot `
        -Base HEAD `
        -Branch 'waggledance/claude/smoke-claude'

    $sourceBranchAfter = (& git -C $repoRoot branch --show-current)
    $codexBranch = (& git -C $codex.worktree_path branch --show-current)
    $claudeBranch = (& git -C $claude.worktree_path branch --show-current)

    Add-Check 'codex worktree created' `
        (Test-Path -LiteralPath (Join-Path $codex.worktree_path '.git')) `
        $codex.worktree_path
    Add-Check 'claude worktree created' `
        (Test-Path -LiteralPath (Join-Path $claude.worktree_path '.git')) `
        $claude.worktree_path
    Add-Check 'worktree paths are distinct' `
        (-not ([string]$codex.worktree_path).Equals([string]$claude.worktree_path, [System.StringComparison]::OrdinalIgnoreCase)) `
        "$($codex.worktree_path) vs $($claude.worktree_path)"
    Add-Check 'codex branch is isolated' `
        ([string]$codexBranch -eq 'waggledance/codex/smoke-codex') `
        $codexBranch
    Add-Check 'claude branch is isolated' `
        ([string]$claudeBranch -eq 'waggledance/claude/smoke-claude') `
        $claudeBranch
    Add-Check 'source repo branch did not move' `
        (($sourceBranchBefore -eq 'main') -and ($sourceBranchAfter -eq 'main')) `
        "before=$sourceBranchBefore after=$sourceBranchAfter"

    Set-Content -LiteralPath (Join-Path $codex.worktree_path 'codex-marker.txt') `
        -Value 'codex-only' -Encoding UTF8
    Add-Check 'codex marker is local to codex worktree' `
        ((Test-Path -LiteralPath (Join-Path $codex.worktree_path 'codex-marker.txt')) -and
         (-not (Test-Path -LiteralPath (Join-Path $claude.worktree_path 'codex-marker.txt'))) -and
         (-not (Test-Path -LiteralPath (Join-Path $repoRoot 'codex-marker.txt')))) `
        'marker not visible in claude/source worktrees'

    $codexAgain = & $newWorktree `
        -Agent codex `
        -TaskId 'smoke-codex' `
        -SourceRepoRoot $repoRoot `
        -WorktreeRoot $worktreeRoot `
        -RuntimeRoot $runtimeRoot `
        -Base HEAD `
        -Branch 'waggledance/codex/smoke-codex'
    Add-Check 'existing worktree reuse is idempotent' `
        ((-not [bool]$codexAgain.created) -and
         ([string]$codexAgain.worktree_path -eq [string]$codex.worktree_path)) `
        "created=$($codexAgain.created)"

} finally {
    if (Test-Path -LiteralPath $tempRootFull) {
        $safeTempChild = $tempRootFull.StartsWith(
            $tempParentFull.TrimEnd('\') + '\',
            [System.StringComparison]::OrdinalIgnoreCase
        ) -and ((Split-Path -Leaf $tempRootFull) -like 'bridge-r23-2-worktree-smoke-*')
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
