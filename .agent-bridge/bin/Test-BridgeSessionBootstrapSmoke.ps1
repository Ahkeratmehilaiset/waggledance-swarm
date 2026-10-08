#requires -Version 5.1
<#
.SYNOPSIS
    Smoke test for Start-AgentBridgeSession.ps1.

.DESCRIPTION
    Verifies the reboot bootstrap path without touching the production bridge
    runtime root. The test uses fresh temp runtime roots, runs the session
    bootstrap in isolated modes, confirms directories and liveness events land
    under the selected root, and then removes only those generated directories.
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
$quietRoot = Join-Path $env:TEMP `
    "bridge-v15e-bootstrap-quiet-$([guid]::NewGuid().ToString('N').Substring(0, 12))"
$quietRootFull = [System.IO.Path]::GetFullPath($quietRoot)
$readerRoot = Join-Path $env:TEMP `
    "bridge-v15e-bootstrap-reader-$([guid]::NewGuid().ToString('N').Substring(0, 12))"
$readerRootFull = [System.IO.Path]::GetFullPath($readerRoot)
$tempParentFull = [System.IO.Path]::GetFullPath($env:TEMP)
$generatedRoots = @($quietRootFull, $readerRootFull, $tempRootFull)

$savedRuntime = $env:AGENT_BRIDGE_RUNTIME_ROOT
$savedRunId = $env:AGENT_BRIDGE_RUN_ID
$savedLocation = (Get-Location).Path
$agentUuid = '11111111-2222-3333-4444-555555555555'

# Checked before the cleanup-owned region: a root that already exists is not
# this run's, so the finally block below must never see it.
foreach ($generatedRoot in $generatedRoots) {
    if (Test-Path -LiteralPath $generatedRoot) {
        throw "Pre-condition failed: temp root already exists: $generatedRoot"
    }
}

try {
    Write-Host 'Bridge session bootstrap smoke test' -ForegroundColor Cyan
    Write-Host '====================================='
    Write-Host "Quiet runtime root: $quietRootFull"
    Write-Host "Reader runtime root: $readerRootFull"
    Write-Host "Normal runtime root: $tempRootFull"
    Write-Host ''

    $quietBootstrap = & $startSession `
        -Agent codex `
        -RuntimeRoot $quietRootFull `
        -RunId 'codex-bootstrap-smoke-quiet' `
        -SkipBridgeRead `
        -SkipLiveness `
        -SkipGitStatus `
        -SkipWakeWatcher `
        -SkipHeartbeatJob

    Add-Check -Name 'quiet bootstrap created runtime root' `
        -Passed (Test-Path -LiteralPath $quietRootFull -PathType Container) `
        -Detail $quietRootFull
    foreach ($relative in @(
        'shared',
        'work_queue',
        'outbox',
        'outbox\codex',
        'inbox',
        'inbox\codex'
    )) {
        $dir = Join-Path $quietRootFull $relative
        Add-Check -Name "quiet bootstrap created $relative" `
            -Passed (Test-Path -LiteralPath $dir -PathType Container) `
            -Detail $dir
    }
    foreach ($relative in @(
        'work_queue\claims',
        'work_queue\done',
        'work_queue\.claims.mutation.lock',
        'shared\events.jsonl'
    )) {
        $path = Join-Path $quietRootFull $relative
        Add-Check -Name "quiet bootstrap left $relative absent" `
            -Passed (-not (Test-Path -LiteralPath $path)) `
            -Detail $path
    }
    Add-Check -Name 'quiet bootstrap skipped wake and heartbeat jobs' `
        -Passed (
            [string]::IsNullOrEmpty([string]$quietBootstrap.wake_job_id) -and
            [string]::IsNullOrEmpty([string]$quietBootstrap.heartbeat_job_id)
        )

    $readerBootstrapError = ''
    try {
        & $startSession `
            -Agent codex `
            -RuntimeRoot $readerRootFull `
            -RunId 'codex-bootstrap-smoke-reader' `
            -SkipLiveness `
            -SkipGitStatus `
            -SkipWakeWatcher `
            -SkipHeartbeatJob |
            Out-Null
    } catch {
        $readerBootstrapError = $_.Exception.Message
    }
    Add-Check -Name 'reader-enabled bootstrap completed without error' `
        -Passed (-not $readerBootstrapError) `
        -Detail $readerBootstrapError
    foreach ($relative in @('work_queue\claims', 'work_queue\done')) {
        $path = Join-Path $readerRootFull $relative
        Add-Check -Name "reader-enabled bootstrap left $relative absent" `
            -Passed (-not (Test-Path -LiteralPath $path)) `
            -Detail $path
    }

    $bootstrap = & $startSession `
        -Agent codex `
        -RuntimeRoot $tempRootFull `
        -RunId 'codex-bootstrap-smoke' `
        -Role impl `
        -AgentUuid $agentUuid `
        -Capabilities @('bridge_event','work_queue') `
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
    Add-Check -Name 'bootstrap returned role metadata' `
        -Passed ([string]$bootstrap.role -eq 'impl') `
        -Detail "role=$($bootstrap.role)"
    Add-Check -Name 'bootstrap returned agent uuid metadata' `
        -Passed ([string]$bootstrap.agent_uuid -eq $agentUuid) `
        -Detail "agent_uuid=$($bootstrap.agent_uuid)"

    foreach ($relative in @(
        'shared',
        'work_queue',
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
    foreach ($relative in @('work_queue\claims', 'work_queue\done')) {
        $path = Join-Path $tempRootFull $relative
        Add-Check -Name "normal bootstrap left $relative absent" `
            -Passed (-not (Test-Path -LiteralPath $path)) `
            -Detail $path
    }

    $eventsPath = Join-Path $tempRootFull 'shared\events.jsonl'
    Add-Check -Name 'liveness event file created under temp root' `
        -Passed (Test-Path -LiteralPath $eventsPath -PathType Leaf) `
        -Detail $eventsPath

    if (Test-Path -LiteralPath $eventsPath -PathType Leaf) {
        $tail = Get-Content -Path $eventsPath -Tail 5 -Encoding UTF8
        $hasRunId = (($tail -join "`n") -match 'codex-bootstrap-smoke')
        $hasRole = (($tail -join "`n") -match '"role":"impl"')
        $hasUuid = (($tail -join "`n") -match $agentUuid)
        $hasActive = (($tail -join "`n") -match '"type":"liveness"' -and
                      ($tail -join "`n") -match '"status":"active"')
        Add-Check -Name 'liveness event carries run id' `
            -Passed $hasRunId `
            -Detail (($tail -join "`n") | ForEach-Object { $_.Substring(0, [Math]::Min(160, $_.Length)) })
        Add-Check -Name 'liveness event carries role metadata' `
            -Passed $hasRole
        Add-Check -Name 'liveness event carries agent uuid metadata' `
            -Passed $hasUuid
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
    $env:AGENT_BRIDGE_RUNTIME_ROOT = $savedRuntime
    $env:AGENT_BRIDGE_RUN_ID = $savedRunId

    foreach ($generatedRoot in $generatedRoots) {
        if (Test-Path -LiteralPath $generatedRoot) {
            $generatedLeaf = Split-Path -Leaf $generatedRoot
            $safeTempChild = $generatedRoot.StartsWith(
                $tempParentFull.TrimEnd('\') + '\',
                [System.StringComparison]::OrdinalIgnoreCase
            ) -and $generatedLeaf -cmatch `
                '^bridge-(?:r13-5-bootstrap-smoke|v15e-bootstrap-(?:quiet|reader))-[0-9a-f]{12}$'
            if (-not $safeTempChild) {
                throw "Refusing cleanup outside generated temp root: $generatedRoot"
            }
            Remove-Item -LiteralPath $generatedRoot -Recurse -Force
            Write-Host ''
            Write-Host "Cleanup: removed $generatedRoot"
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
