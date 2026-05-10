#requires -Version 5.1
<#
.SYNOPSIS
    Smoke test for Start-AgentBridgeSession.ps1.

.DESCRIPTION
    Verifies the reboot bootstrap path without touching the production bridge
    runtime root. The test uses a fresh temp runtime root, runs the session
    bootstrap, confirms directories and liveness events land there, and then
    removes only that generated temp directory.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$bridgeBin = $PSScriptRoot
$startSession = Join-Path $bridgeBin 'Start-AgentBridgeSession.ps1'
$bridgeStatus = Join-Path $bridgeBin 'Get-AgentBridgeStatus.ps1'
$readBridge = Join-Path $bridgeBin 'Read-AgentBridge.ps1'

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
    "bridge-r13-5-bootstrap-smoke-$([guid]::NewGuid().ToString('N').Substring(0, 12))"
$tempRootFull = [System.IO.Path]::GetFullPath($tempRoot)
$tempParentFull = [System.IO.Path]::GetFullPath($env:TEMP)

$savedRuntime = $env:AGENT_BRIDGE_RUNTIME_ROOT
$savedRunId = $env:AGENT_BRIDGE_RUN_ID
$savedCleanupEvent = $env:AGENT_BRIDGE_CLEANUP_EVENT
$savedLocation = (Get-Location).Path
$bootstrap = $null

try {
    Write-Host 'Bridge session bootstrap smoke test' -ForegroundColor Cyan
    Write-Host '====================================='
    Write-Host "Temp runtime root: $tempRootFull"
    Write-Host ''

    if (Test-Path -LiteralPath $tempRootFull) {
        throw "Pre-condition failed: temp root already exists: $tempRootFull"
    }

    $bootstrap = & $startSession `
        -Agent codex `
        -RuntimeRoot $tempRootFull `
        -RunId 'codex-bootstrap-smoke' `
        -SkipBridgeRead `
        -SkipGitStatus

    Add-Check -Name 'bootstrap returned codex agent' `
        -Passed ([string]$bootstrap.agent -eq 'codex') `
        -Detail "agent=$($bootstrap.agent)"
    Add-Check -Name 'AGENT_BRIDGE_RUNTIME_ROOT set in process' `
        -Passed ([string]$env:AGENT_BRIDGE_RUNTIME_ROOT -eq $tempRootFull) `
        -Detail $env:AGENT_BRIDGE_RUNTIME_ROOT
    Add-Check -Name 'AGENT_BRIDGE_RUN_ID set in process' `
        -Passed ([string]$env:AGENT_BRIDGE_RUN_ID -eq 'codex-bootstrap-smoke') `
        -Detail $env:AGENT_BRIDGE_RUN_ID
    Add-Check -Name 'wake watcher job id recorded' `
        -Passed ([int]$bootstrap.wake_job_id -gt 0) `
        -Detail "wake_job_id=$($bootstrap.wake_job_id)"
    Add-Check -Name 'heartbeat job id recorded' `
        -Passed ([int]$bootstrap.heartbeat_job_id -gt 0) `
        -Detail "heartbeat_job_id=$($bootstrap.heartbeat_job_id)"
    Add-Check -Name 'cleanup event registered for background jobs' `
        -Passed (-not [string]::IsNullOrWhiteSpace([string]$bootstrap.cleanup_event_id)) `
        -Detail "cleanup_event_id=$($bootstrap.cleanup_event_id)"
    if ($bootstrap.cleanup_event_id) {
        $subscriber = Get-EventSubscriber -SourceIdentifier $bootstrap.cleanup_event_id `
            -Force -ErrorAction SilentlyContinue
        Add-Check -Name 'cleanup event subscriber exists' `
            -Passed ($null -ne $subscriber) `
            -Detail "subscriber=$($bootstrap.cleanup_event_id)"
    }

    foreach ($relative in @(
        'shared',
        'work_queue',
        'work_queue\claims',
        'work_queue\done',
        'outbox',
        'outbox\codex',
        'inbox',
        'inbox\codex'
    )) {
        $dir = Join-Path $tempRootFull $relative
        Add-Check -Name "created $relative" `
            -Passed (Test-Path -LiteralPath $dir -PathType Container) `
            -Detail $dir
    }

    $eventsPath = Join-Path $tempRootFull 'shared\events.jsonl'
    Add-Check -Name 'liveness event file created under temp root' `
        -Passed (Test-Path -LiteralPath $eventsPath -PathType Leaf) `
        -Detail $eventsPath

    if (Test-Path -LiteralPath $eventsPath -PathType Leaf) {
        $tail = Get-Content -Path $eventsPath -Tail 5 -Encoding UTF8
        $hasRunId = (($tail -join "`n") -match 'codex-bootstrap-smoke')
        $hasActive = (($tail -join "`n") -match '"type":"liveness"' -and
                      ($tail -join "`n") -match '"status":"active"')
        Add-Check -Name 'liveness event carries run id' `
            -Passed $hasRunId `
            -Detail (($tail -join "`n") | ForEach-Object { $_.Substring(0, [Math]::Min(160, $_.Length)) })
        Add-Check -Name 'liveness/active was emitted' `
            -Passed $hasActive
    }

    $statusThrew = $false
    try {
        & $bridgeStatus -MaxUnresolved 3 -Tail 100 | Out-Null
    } catch {
        $statusThrew = $true
    }
    Add-Check -Name 'Get-AgentBridgeStatus runs against bootstrap root' `
        -Passed (-not $statusThrew)

    $readThrew = $false
    try {
        & $readBridge -Agent codex -NoContinuity -NoAckReceived -Tail 5 | Out-Null
    } catch {
        $readThrew = $true
    }
    Add-Check -Name 'Read-AgentBridge runs against bootstrap root' `
        -Passed (-not $readThrew)

} finally {
    Set-Location -LiteralPath $savedLocation
    if ($bootstrap) {
        foreach ($jobId in @($bootstrap.wake_job_id, $bootstrap.heartbeat_job_id)) {
            if ($jobId) {
                try { Stop-Job -Id ([int]$jobId) -ErrorAction SilentlyContinue | Out-Null } catch {}
                try { Remove-Job -Id ([int]$jobId) -Force -ErrorAction SilentlyContinue | Out-Null } catch {}
            }
        }
        if ($bootstrap.cleanup_event_id) {
            try {
                Unregister-Event -SourceIdentifier $bootstrap.cleanup_event_id `
                    -ErrorAction SilentlyContinue
            } catch {}
        }
    }
    $env:AGENT_BRIDGE_RUNTIME_ROOT = $savedRuntime
    $env:AGENT_BRIDGE_RUN_ID = $savedRunId
    $env:AGENT_BRIDGE_CLEANUP_EVENT = $savedCleanupEvent

    if (Test-Path -LiteralPath $tempRootFull) {
        $safeTempChild = $tempRootFull.StartsWith(
            $tempParentFull.TrimEnd('\') + '\',
            [System.StringComparison]::OrdinalIgnoreCase
        ) -and ((Split-Path -Leaf $tempRootFull) -like 'bridge-r13-5-bootstrap-smoke-*')
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
