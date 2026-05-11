#requires -Version 5.1
<#
.SYNOPSIS
    Smoke test for polymorphic bridge continuity classification.

.DESCRIPTION
    Reproduces the live failure where a substantive custom event
    (ownership_proposal/open) answered an earlier request, but polling still
    showed the request as waiting because readers only accepted a narrow set
    of event types/statuses.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$bridgeBin = $PSScriptRoot
$readScript = Join-Path $bridgeBin 'Read-AgentBridge.ps1'
$statusScript = Join-Path $bridgeBin 'Get-AgentBridgeStatus.ps1'
$nextActionScript = Join-Path $bridgeBin 'Get-BridgeNextAction.ps1'

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
    $color  = if ($Passed) { 'Green' } else { 'Red' }
    Write-Host ("  [{0}] {1}" -f $marker, $Name) -ForegroundColor $color
    if ($Detail) { Write-Host "        $Detail" }
}

function Add-RawEvent {
    param(
        [Parameter(Mandatory)] [string] $Root,
        [Parameter(Mandatory)] [string] $TsUtc,
        [Parameter(Mandatory)] [string] $Agent,
        [Parameter(Mandatory)] [string] $Type,
        [Parameter(Mandatory)] [string] $TaskId,
        [Parameter(Mandatory)] [string] $Status,
        [string] $To = '',
        [string] $Message = ''
    )

    $shared = Join-Path $Root 'shared'
    if (-not (Test-Path -LiteralPath $shared -PathType Container)) {
        [void](New-Item -ItemType Directory -Path $shared -Force)
    }
    $event = [ordered]@{
        ts_utc = $TsUtc
        agent = $Agent
        type = $Type
        task_id = $TaskId
        status = $Status
        severity = ''
        to = $To
        message = $Message
        paths = @()
        write_scope = @()
        run_id = ''
        pid = 0
        cwd = ''
        payload = [pscustomobject]@{}
    }
    $line = (($event | ConvertTo-Json -Depth 12 -Compress) + [Environment]::NewLine)
    [System.IO.File]::AppendAllText(
        (Join-Path $shared 'events.jsonl'),
        $line,
        (New-Object System.Text.UTF8Encoding($false))
    )
}

$tempRoot = Join-Path $env:TEMP "bridge-polymorphic-continuity-$([guid]::NewGuid().ToString('N').Substring(0, 12))"
$savedEnv = $env:AGENT_BRIDGE_RUNTIME_ROOT

try {
    Write-Host 'Bridge polymorphic continuity smoke test' -ForegroundColor Cyan
    Write-Host '========================================='
    Write-Host "Temp runtime root: $tempRoot"
    Write-Host ''

    $env:AGENT_BRIDGE_RUNTIME_ROOT = $tempRoot

    Add-RawEvent -Root $tempRoot -TsUtc '2026-05-11T17:46:32.3008252Z' `
        -Agent codex -Type message -TaskId eig2-m0-ownership-split-2026-05-11 `
        -Status request -To claude -Message 'ownership split request'
    Add-RawEvent -Root $tempRoot -TsUtc '2026-05-11T17:50:28.3785440Z' `
        -Agent claude -Type ownership_proposal -TaskId eig2-m0-ownership-split-2026-05-11 `
        -Status open -To codex -Message 'custom substantive ownership proposal'
    Add-RawEvent -Root $tempRoot -TsUtc '2026-05-11T17:52:00.0000000Z' `
        -Agent codex -Type message -TaskId eig2-m0-ownership-split-2026-05-11 `
        -Status answered_plus_reminder -To claude -Message 'Codex accepted and answered'

    Add-RawEvent -Root $tempRoot -TsUtc '2026-05-11T17:55:00.0000000Z' `
        -Agent claude -Type message -TaskId claude-codex-postchat-2026-05-11 `
        -Status open -To codex -Message 'postchat request'
    Add-RawEvent -Root $tempRoot -TsUtc '2026-05-11T17:56:00.0000000Z' `
        -Agent codex -Type message -TaskId claude-codex-postchat-2026-05-11 `
        -Status answered_plus_reminder -To claude -Message 'non-exact answered status still answers'

    $status = (& $statusScript -Json -Tail 100 | ConvertFrom-Json)
    $waitingOwnershipForClaude = @(
        $status.unresolved_requests |
            Where-Object {
                [string]$_.task_id -eq 'eig2-m0-ownership-split-2026-05-11' -and
                [string]$_.to -eq 'claude' -and
                [string]$_.from -eq 'codex'
            }
    )
    Add-Check -Name 'custom ownership_proposal/open counts as answer to earlier request' `
        -Passed ($waitingOwnershipForClaude.Count -eq 0) `
        -Detail "unresolved_to_claude=$($waitingOwnershipForClaude.Count)"

    $waitingPostchatForCodex = @(
        $status.unresolved_requests |
            Where-Object {
                [string]$_.task_id -eq 'claude-codex-postchat-2026-05-11' -and
                [string]$_.to -eq 'codex'
            }
    )
    Add-Check -Name 'message/answered_plus_reminder counts as a message answer' `
        -Passed ($waitingPostchatForCodex.Count -eq 0) `
        -Detail "unresolved_postchat_to_codex=$($waitingPostchatForCodex.Count)"

    $readerOutput = (& $readScript -Agent codex -NoAckReceived -Tail 20 6>&1) | Out-String
    Add-Check -Name 'Read-AgentBridge outgoing view sees custom reply type' `
        -Passed ($readerOutput -match 'answered-by-claude eig2-m0-ownership-split-2026-05-11: request message/request -> ownership_proposal/open') `
        -Detail (($readerOutput -split "`r?`n" | Where-Object { $_ -match 'eig2-m0-ownership-split-2026-05-11' } | Select-Object -First 2) -join ' | ')

    Add-Check -Name 'Read-AgentBridge incoming view accepts answered_plus_reminder' `
        -Passed ($readerOutput -match 'answered claude-codex-postchat-2026-05-11: request message/open -> message/answered_plus_reminder') `
        -Detail (($readerOutput -split "`r?`n" | Where-Object { $_ -match 'claude-codex-postchat-2026-05-11' } | Select-Object -First 2) -join ' | ')

    $next = (& $nextActionScript -Agent codex -Json -Tail 100 | ConvertFrom-Json)
    Add-Check -Name 'next-action no longer asks Codex to answer already-answered postchat' `
        -Passed (-not ([string]$next.task_id -eq 'claude-codex-postchat-2026-05-11' -and [string]$next.action -eq 'answer_incoming')) `
        -Detail "action=$($next.action), task=$($next.task_id)"

} finally {
    $env:AGENT_BRIDGE_RUNTIME_ROOT = $savedEnv
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host ''
        Write-Host "Cleanup: removed $tempRoot"
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
