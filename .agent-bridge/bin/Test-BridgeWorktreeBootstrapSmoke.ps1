#requires -Version 5.1
<#
.SYNOPSIS
    Smoke test for Start-AgentBridgeWorktreeSession.ps1.

.DESCRIPTION
    Builds a temporary local repo containing the bridge bin scripts, starts a
    dedicated worktree session against a temporary runtime root, and verifies
    that the source repo branch did not move while the session bootstrapped in
    the dedicated worktree.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$bridgeBin = $PSScriptRoot
$wrapper = Join-Path $bridgeBin 'Start-AgentBridgeWorktreeSession.ps1'

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
    "bridge-worktree-bootstrap-smoke-$([guid]::NewGuid().ToString('N').Substring(0, 12))"
$tempRootFull = [System.IO.Path]::GetFullPath($tempRoot)
$tempParentFull = [System.IO.Path]::GetFullPath($env:TEMP)
$sourceRepoRoot = Join-Path $tempRootFull 'repo'
$worktreeRoot = Join-Path $tempRootFull 'worktrees'
$runtimeRoot = Join-Path $tempRootFull 'runtime'

$savedRuntime = $env:AGENT_BRIDGE_RUNTIME_ROOT
$savedRunId = $env:AGENT_BRIDGE_RUN_ID
$savedLocation = (Get-Location).Path
$agentUuid = '11111111-2222-3333-4444-555555555555'

try {
    Write-Host 'Bridge worktree bootstrap smoke test' -ForegroundColor Cyan
    Write-Host '===================================='
    Write-Host "Temp root: $tempRootFull"
    Write-Host ''

    if (Test-Path -LiteralPath $tempRootFull) {
        throw "Pre-condition failed: temp root already exists: $tempRootFull"
    }
    [void](New-Item -ItemType Directory -Path (Join-Path $sourceRepoRoot '.agent-bridge') -Force)
    Copy-Item -LiteralPath $bridgeBin -Destination (Join-Path $sourceRepoRoot '.agent-bridge\bin') -Recurse -Force

    & git -C $sourceRepoRoot init | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'git init failed' }
    & git -C $sourceRepoRoot config user.email 'bridge-smoke@example.invalid'
    & git -C $sourceRepoRoot config user.name 'Bridge Smoke'
    Set-Content -LiteralPath (Join-Path $sourceRepoRoot 'README.md') -Value 'smoke' -Encoding UTF8
    & git -C $sourceRepoRoot add README.md .agent-bridge
    if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
    & git -C $sourceRepoRoot commit -m 'initial smoke commit' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'git commit failed' }
    & git -C $sourceRepoRoot branch -M main
    if ($LASTEXITCODE -ne 0) { throw 'git branch -M main failed' }

    $sourceBranchBefore = (& git -C $sourceRepoRoot branch --show-current)

    $bootstrap = . $wrapper `
        -Agent codex `
        -TaskId 'smoke-codex-bootstrap' `
        -SourceRepoRoot $sourceRepoRoot `
        -WorktreeRoot $worktreeRoot `
        -RuntimeRoot $runtimeRoot `
        -Base HEAD `
        -Branch 'waggledance/codex/smoke-bootstrap' `
        -RunId 'codex-worktree-bootstrap-smoke' `
        -Role impl `
        -AgentUuid $agentUuid `
        -Capabilities @('bridge_event','work_queue') `
        -SkipBridgeRead `
        -SkipGitStatus `
        -SkipWakeWatcher `
        -SkipHeartbeatJob

    $sourceBranchAfter = (& git -C $sourceRepoRoot branch --show-current)
    $currentLocation = (Get-Location).Path
    $worktreePath = [string]$bootstrap.worktree_path
    $worktreeBranch = (& git -C $worktreePath branch --show-current)
    $eventsPath = Join-Path $runtimeRoot 'shared\events.jsonl'

    Add-Check 'bootstrap created dedicated worktree' `
        (Test-Path -LiteralPath (Join-Path $worktreePath '.git')) `
        $worktreePath
    Add-Check 'bootstrap reports dedicated_worktree true' `
        ([bool]$bootstrap.dedicated_worktree) `
        "dedicated_worktree=$($bootstrap.dedicated_worktree)"
    Add-Check 'session location moved to worktree' `
        ($currentLocation.Equals($worktreePath, [System.StringComparison]::OrdinalIgnoreCase)) `
        $currentLocation
    Add-Check 'AGENT_BRIDGE_RUNTIME_ROOT points to shared runtime' `
        ([string]$env:AGENT_BRIDGE_RUNTIME_ROOT -eq $runtimeRoot) `
        $env:AGENT_BRIDGE_RUNTIME_ROOT
    Add-Check 'AGENT_BRIDGE_RUN_ID persisted' `
        ([string]$env:AGENT_BRIDGE_RUN_ID -eq 'codex-worktree-bootstrap-smoke') `
        $env:AGENT_BRIDGE_RUN_ID
    Add-Check 'role metadata persisted' `
        ([string]$bootstrap.role -eq 'impl') `
        "role=$($bootstrap.role)"
    Add-Check 'agent uuid metadata persisted' `
        ([string]$bootstrap.agent_uuid -eq $agentUuid) `
        "agent_uuid=$($bootstrap.agent_uuid)"
    Add-Check 'worktree branch is isolated' `
        ([string]$worktreeBranch -eq 'waggledance/codex/smoke-bootstrap') `
        $worktreeBranch
    Add-Check 'source repo branch did not move' `
        (($sourceBranchBefore -eq 'main') -and ($sourceBranchAfter -eq 'main')) `
        "before=$sourceBranchBefore after=$sourceBranchAfter"
    Add-Check 'liveness event landed in shared runtime' `
        (Test-Path -LiteralPath $eventsPath -PathType Leaf) `
        $eventsPath

    if (Test-Path -LiteralPath $eventsPath -PathType Leaf) {
        $tail = (Get-Content -Path $eventsPath -Tail 5 -Encoding UTF8) -join "`n"
        Add-Check 'liveness event carries run id' `
            ($tail -match 'codex-worktree-bootstrap-smoke') `
            ($tail.Substring(0, [Math]::Min(160, $tail.Length)))
        Add-Check 'liveness event carries role metadata' `
            ($tail -match '"role":"impl"')
        Add-Check 'liveness event carries agent uuid metadata' `
            ($tail -match $agentUuid)
    }
} finally {
    Set-Location -LiteralPath $savedLocation
    $env:AGENT_BRIDGE_RUNTIME_ROOT = $savedRuntime
    $env:AGENT_BRIDGE_RUN_ID = $savedRunId

    if (Test-Path -LiteralPath $tempRootFull) {
        $safeTempChild = $tempRootFull.StartsWith(
            $tempParentFull.TrimEnd('\') + '\',
            [System.StringComparison]::OrdinalIgnoreCase
        ) -and ((Split-Path -Leaf $tempRootFull) -like 'bridge-worktree-bootstrap-smoke-*')
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
