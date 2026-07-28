#requires -Version 5.1
<#
.SYNOPSIS
    Smoke test for Read-AgentBridge.ps1 liveness timestamp parsing.

.DESCRIPTION
    PowerShell 7+ ConvertFrom-Json may convert ISO 8601 JSON strings into
    DateTime objects. Read-AgentBridge.ps1 must preserve those values instead
    of string-casting them through the current UI culture before parsing.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$bridgeBin = $PSScriptRoot
$reader = Join-Path $bridgeBin 'Read-AgentBridge.ps1'
$writeEvent = Join-Path $bridgeBin 'Write-AgentEvent.ps1'

$results = New-Object System.Collections.Generic.List[object]
function Add-Check {
    param(
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [bool] $Passed,
        [string] $Detail = ''
    )
    [void]$results.Add([pscustomobject]@{
        name = $Name
        passed = $Passed
        detail = $Detail
    })
    $marker = if ($Passed) { 'PASS' } else { 'FAIL' }
    $color = if ($Passed) { 'Green' } else { 'Red' }
    Write-Host ("  [{0}] {1}" -f $marker, $Name) -ForegroundColor $color
    if ($Detail) { Write-Host "        $Detail" }
}

$tempRoot = Join-Path $env:TEMP `
    "bridge-reader-ts-smoke-$([guid]::NewGuid().ToString('N').Substring(0,12))"
$savedRoot = $env:AGENT_BRIDGE_RUNTIME_ROOT
$identityIsolation = Join-Path $PSScriptRoot 'BridgeSmokeIdentityIsolation.ps1'
. $identityIsolation
$identitySnapshot = Enter-BridgeSmokeIdentityIsolation

try {
    Write-Host 'Bridge reader timestamp parse smoke test' -ForegroundColor Cyan
    Write-Host '========================================'
    Write-Host "Temp runtime root: $tempRoot"
    Write-Host ''

    $env:AGENT_BRIDGE_RUNTIME_ROOT = $tempRoot
    [void](New-Item -ItemType Directory -Path (Join-Path $tempRoot 'shared') -Force)

    & $writeEvent -Agent codex -Type liveness -Status active `
        -TaskId 'reader-ts-smoke' -Message 'timestamp parse smoke' | Out-Null

    $eventsPath = Join-Path $tempRoot 'shared\events.jsonl'
    $event = Get-Content -Raw -LiteralPath $eventsPath | ConvertFrom-Json
    Add-Check 'ConvertFrom-Json exposes ts_utc value' `
        ($null -ne $event.ts_utc) `
        ("type={0}" -f $event.ts_utc.GetType().FullName)

    $out = @(
        & $reader -Agent codex -Tail 5 -ShowLiveness -NoAckReceived 6>&1 |
            ForEach-Object { [string]$_ }
    )
    Add-Check 'ShowLiveness does not report malformed timestamp' `
        (-not (($out -join "`n") -match 'malformed timestamp')) `
        (($out -join "`n"))
    Add-Check 'ShowLiveness reports liveness age' `
        (($out -join "`n") -match 'codex\s+liveness\s+\d+s ago') `
        (($out -join "`n"))
} finally {
    Exit-BridgeSmokeIdentityIsolation -Snapshot $identitySnapshot
    if ($null -eq $savedRoot) {
        Remove-Item Env:\AGENT_BRIDGE_RUNTIME_ROOT -ErrorAction SilentlyContinue
    } else {
        $env:AGENT_BRIDGE_RUNTIME_ROOT = $savedRoot
    }
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

$failed = @($results | Where-Object { -not $_.passed })
Write-Host ''
if ($failed.Count -eq 0) {
    Write-Host ("Bridge reader timestamp parse smoke PASS ({0}/{0})" -f $results.Count) `
        -ForegroundColor Green
    exit 0
}

Write-Host ("Bridge reader timestamp parse smoke FAIL ({0} failed of {1})" -f `
    $failed.Count, $results.Count) -ForegroundColor Red
exit 1
