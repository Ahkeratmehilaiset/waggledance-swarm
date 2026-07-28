#requires -Version 5.1
<#
.SYNOPSIS
    Focused smoke test for Invoke-BridgeGit.ps1 + Test-BridgeBranchSwitchSafe.ps1.

.DESCRIPTION
    Exercises the bridge branch-guard contract with a temporary
    foreign-agent claim. Avoids destructive git operations: uses
    `git status` (pass-through) and `git switch --no-guess` against
    a deliberately nonexistent branch to verify guard pass-through
    without changing the current branch.

    Exit code 0 on all expectations met, 1 otherwise. Cleanup is
    always attempted (the temporary claim is released even on
    early failure).
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$bridgeBin = $PSScriptRoot
$invokeGit = Join-Path $bridgeBin 'Invoke-BridgeGit.ps1'
$claimTask = Join-Path $bridgeBin 'Claim-AgentTask.ps1'
$releaseTask = Join-Path $bridgeBin 'Release-AgentTask.ps1'
$bridgeStatus = Join-Path $bridgeBin 'Get-AgentBridgeStatus.ps1'

$results = New-Object System.Collections.Generic.List[object]

function Add-Check {
    param(
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [bool] $Passed,
        [string] $Detail = ''
    )
    [void]$results.Add([pscustomobject]@{
        name    = $Name
        passed  = $Passed
        detail  = $Detail
    })
    $marker = if ($Passed) { 'PASS' } else { 'FAIL' }
    $color  = if ($Passed) { 'Green' } else { 'Red' }
    Write-Host ("  [{0}] {1}" -f $marker, $Name) -ForegroundColor $color
    if ($Detail) { Write-Host "        $Detail" }
}

$tempTaskId = "bridge-guard-smoke-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
$cleanupRequired = $false
$identityIsolation = Join-Path $PSScriptRoot 'BridgeSmokeIdentityIsolation.ps1'
. $identityIsolation
$identitySnapshot = Enter-BridgeSmokeIdentityIsolation
$savedRuntimeRoot = [Environment]::GetEnvironmentVariable(
    'AGENT_BRIDGE_RUNTIME_ROOT',
    'Process'
)
$tempRuntimeRoot = ''
try {
    $tempBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    $tempRuntimeRoot = [System.IO.Path]::GetFullPath(
        (Join-Path $tempBase "waggledance-bridge-guard-$([guid]::NewGuid().ToString('N'))")
    )
    if (-not $tempRuntimeRoot.StartsWith(
            $tempBase,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw "temporary guard runtime escaped the system temp root: $tempRuntimeRoot"
    }
    [void](New-Item -ItemType Directory -Path $tempRuntimeRoot -Force)
    $env:AGENT_BRIDGE_RUNTIME_ROOT = $tempRuntimeRoot

    Write-Host 'Bridge guard smoke test' -ForegroundColor Cyan
    Write-Host '======================='
    Write-Host ''

    # ── 1: pass-through verb (status) succeeds without checks ────
    Write-Host '1. Pass-through (git status) runs unconditionally:'
    & $invokeGit -Agent claude -- status --short | Out-Null
    Add-Check -Name 'pass-through git status' -Passed ($LASTEXITCODE -eq 0) `
        -Detail "expected exit 0, got $LASTEXITCODE"

    # ── 2: no claims => branch-moving verb permitted ─────────────
    Write-Host ''
    Write-Host '2. Branch-moving verb with no active claims:'
    # We cannot safely run a real branch-changing command in a smoke
    # test, so we verify the guard ALLOWS the call through by asking
    # git to switch to a deliberately impossible branch. Git should
    # fail quickly, but importantly the wrapper must not produce the
    # BLOCKED branch-guard message. Avoid `git switch --help` here:
    # on some Windows environments help can invoke a pager and hang.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $output = (& $invokeGit -Agent claude -- switch --no-guess __bridge_guard_nonexistent_branch__ 2>&1) -join "`n"
    $exit = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP
    $blockedByGuard = ($output -match 'BLOCKED|branch-moving git')
    Add-Check -Name 'no-claim => guard passes through to git' `
        -Passed ($exit -ne 2 -and -not $blockedByGuard) `
        -Detail "exit=$exit, guard-blocked: $blockedByGuard"

    # ── 3: foreign-agent active claim => block branch-move ───────
    Write-Host ''
    Write-Host '3. Setting up foreign-agent (codex) write claim...'
    & $claimTask -Agent codex -TaskId $tempTaskId `
        -Summary 'Bridge guard smoke test (codex temp claim)' `
        -Mode write -WriteScope 'tests/smoke_only' | Out-Null
    $cleanupRequired = $true

    Write-Host '   Branch-moving verb under foreign claim:'
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $rawLines = & $invokeGit -Agent claude -- switch main 2>&1
    $exit = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP
    $output = ($rawLines | ForEach-Object { [string]$_ }) -join "`n"
    $sawBlock = ($output -match 'BLOCKED')
    Add-Check -Name 'foreign claim => switch blocked exit 2' `
        -Passed ($exit -eq 2 -and $sawBlock) `
        -Detail "exit=$exit, blocked-message present: $sawBlock"

    # ── 4: foreign claim => pass-through still works ─────────────
    Write-Host '   Pass-through under foreign claim still works:'
    & $invokeGit -Agent claude -- status --short | Out-Null
    Add-Check -Name 'foreign claim => pass-through unaffected' `
        -Passed ($LASTEXITCODE -eq 0) `
        -Detail "git status exit=$LASTEXITCODE"

    # ── 5: claude -Force is REJECTED ─────────────────────────────
    Write-Host '   Claude -Force is rejected (only a bound operator may force):'
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $rawLines = & $invokeGit -Agent claude -Force -- switch main 2>&1
    $exit = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP
    $output = ($rawLines | ForEach-Object { [string]$_ }) -join "`n"
    $sawReject = ($output -match 'REJECTED|restricted')
    Add-Check -Name 'claude -Force rejected' `
        -Passed ($exit -eq 2 -and $sawReject) `
        -Detail "exit=$exit, rejection message present: $sawReject"

    # ── 6: Test-BridgeBranchSwitchSafe agrees with the guard ─────
    Write-Host ''
    Write-Host '4. Test-BridgeBranchSwitchSafe (passive check) agrees:'
    $checkScript = Join-Path $bridgeBin 'Test-BridgeBranchSwitchSafe.ps1'
    & $checkScript -Agent claude | Out-Null
    Add-Check -Name 'passive check exit 2 under foreign claim' `
        -Passed ($LASTEXITCODE -eq 2) `
        -Detail "passive check exit=$LASTEXITCODE"

    # ── 7: Get-AgentBridgeStatus does not crash with 1 claim ─────
    Write-Host ''
    Write-Host '5. Get-AgentBridgeStatus -MaxUnresolved 10 does not crash:'
    $global:LASTEXITCODE = 0
    try {
        & $bridgeStatus -MaxUnresolved 10 | Out-Null
        $statusOk = $true
    } catch {
        $statusOk = $false
    }
    # Get-AgentBridgeStatus prints its summary and exits 0; if it
    # threw above, we already caught. Some upstream LASTEXITCODE
    # values can leak; we treat "did not throw" as the success
    # signal here.
    Add-Check -Name 'bridge status survives single-claim case' `
        -Passed $statusOk `
        -Detail "did_not_throw=$statusOk"

} finally {
    try {
        if ($cleanupRequired) {
            Write-Host ''
            Write-Host 'Cleanup: releasing temporary codex claim...'
            & $releaseTask -Agent codex -TaskId $tempTaskId `
                -Status done -Message 'smoke-test cleanup' | Out-Null
        }
    } finally {
        try {
            if ($null -ne $savedRuntimeRoot) {
                $env:AGENT_BRIDGE_RUNTIME_ROOT = $savedRuntimeRoot
            } else {
                Remove-Item Env:AGENT_BRIDGE_RUNTIME_ROOT `
                    -ErrorAction SilentlyContinue
            }
            Exit-BridgeSmokeIdentityIsolation -Snapshot $identitySnapshot
        } finally {
            if ($tempRuntimeRoot -and
                (Test-Path -LiteralPath $tempRuntimeRoot -PathType Container)) {
                Remove-Item -LiteralPath $tempRuntimeRoot -Recurse -Force `
                    -ErrorAction SilentlyContinue
            }
        }
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
