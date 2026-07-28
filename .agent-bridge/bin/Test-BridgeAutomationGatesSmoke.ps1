#requires -Version 5.1
<#
.SYNOPSIS
    R23.1 smoke test for role-review gate + next-action helper.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$bridgeBin = $PSScriptRoot
$writeEvent = Join-Path $bridgeBin 'Write-AgentEvent.ps1'
$invokeRoleReview = Join-Path $bridgeBin 'Invoke-RoleReview.ps1'
$roleGate = Join-Path $bridgeBin 'Test-BridgeRoleReviewGate.ps1'
$nextAction = Join-Path $bridgeBin 'Get-BridgeNextAction.ps1'
$claimTask = Join-Path $bridgeBin 'Claim-AgentTask.ps1'

$results = New-Object System.Collections.Generic.List[object]
function Add-Check {
    param([string] $Name, [bool] $Passed, [string] $Detail = '')
    [void]$results.Add([pscustomobject]@{ name = $Name; passed = $Passed; detail = $Detail })
    $marker = if ($Passed) { 'PASS' } else { 'FAIL' }
    $color = if ($Passed) { 'Green' } else { 'Red' }
    Write-Host ("  [{0}] {1}" -f $marker, $Name) -ForegroundColor $color
    if ($Detail) { Write-Host "        $Detail" }
}

$tempRoot = Join-Path $env:TEMP "bridge-r23-1-smoke-$([guid]::NewGuid().ToString('N').Substring(0,12))"
$savedRoot = $env:AGENT_BRIDGE_RUNTIME_ROOT
$identityIsolation = Join-Path $PSScriptRoot 'BridgeSmokeIdentityIsolation.ps1'
. $identityIsolation
$identitySnapshot = Enter-BridgeSmokeIdentityIsolation

try {
    $env:AGENT_BRIDGE_RUNTIME_ROOT = $tempRoot
    Write-Host 'R23.1 automation gate smoke test' -ForegroundColor Cyan
    Write-Host '================================='
    Write-Host "Temp runtime root: $tempRoot"

    Write-Host ''
    Write-Host '1. role-review gate does not require docs-only changes:'
    & $roleGate -Target 'PR-docs' -ChangedPath 'docs/readme.md' | Out-Null
    Add-Check 'docs-only role gate passes without review' ($LASTEXITCODE -eq 0) ''

    Write-Host '2. role-review gate fails risky change before synthesis:'
    & $roleGate -Target 'PR-risky' -ChangedPath '.agent-bridge/bin/Foo.ps1' *>$null
    Add-Check 'risky change blocked before role review' ($LASTEXITCODE -eq 2) "exit=$LASTEXITCODE"

    Write-Host '3. role-review gate passes after process-isolated dry-run synthesis:'
    & $invokeRoleReview -Target 'PR-risky' -DryRun | Out-Null
    & $roleGate -Target 'PR-risky' -ChangedPath '.agent-bridge/bin/Foo.ps1' | Out-Null
    Add-Check 'risky change passes after role synthesis' ($LASTEXITCODE -eq 0) "exit=$LASTEXITCODE"

    Write-Host '4. next-action helper prioritizes incoming work:'
    & $writeEvent -Agent claude -To codex -Type handoff -Status ready -TaskId 'r23-1-smoke-incoming' -Message 'please review' | Out-Null
    $na = & $nextAction -Agent codex -Json | ConvertFrom-Json
    Add-Check 'next action = answer_incoming' ([string]$na.action -eq 'answer_incoming') ([string]$na.summary)

    Write-Host '5. next-action helper recommends read-only work under foreign write claim:'
    & $writeEvent -Agent codex -To claude -Type message -Status answered -TaskId 'r23-1-smoke-incoming' -Message 'answered' | Out-Null
    & $claimTask -Agent claude -TaskId 'r23-1-foreign-write' -Summary 'foreign write' -Mode write -WriteScope 'waggledance/core' | Out-Null
    $na2 = & $nextAction -Agent codex -Json | ConvertFrom-Json
    Add-Check 'next action = parallel_read_only' ([string]$na2.action -eq 'parallel_read_only') ([string]$na2.summary)

    Write-Host ''
    $passed = @($results | Where-Object { $_.passed }).Count
    $failed = @($results | Where-Object { -not $_.passed }).Count
    $color = if ($failed -eq 0) { 'Green' } else { 'Red' }
    Write-Host ("Result: {0}/{1} checks passed" -f $passed, $results.Count) -ForegroundColor $color
    if ($failed -gt 0) { exit 1 } else { exit 0 }
} finally {
    Exit-BridgeSmokeIdentityIsolation -Snapshot $identitySnapshot
    if ($null -ne $savedRoot) {
        $env:AGENT_BRIDGE_RUNTIME_ROOT = $savedRoot
    } else {
        Remove-Item Env:AGENT_BRIDGE_RUNTIME_ROOT -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
