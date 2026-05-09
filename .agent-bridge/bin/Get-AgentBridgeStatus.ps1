#requires -Version 5.1
[CmdletBinding()]
param(
    [int] $Tail = 50000,
    [int] $Recent = 12,
    [int] $MaxUnresolved = 25,
    [switch] $Json
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$bridgeRoot = Split-Path -Parent $PSScriptRoot
$eventsPath = Join-Path (Join-Path $bridgeRoot 'shared') 'events.jsonl'
$claimsDir = Join-Path (Join-Path $bridgeRoot 'work_queue') 'claims'

function Read-EventObjects {
    param([Parameter(Mandatory)] [string] $Path, [int] $MaxLines = 50000)
    $items = New-Object System.Collections.Generic.List[object]
    if (-not (Test-Path -LiteralPath $Path)) { return $items }
    $lines = if ($MaxLines -le 0) {
        @(Get-Content -Path $Path -Encoding UTF8)
    } else {
        @(Get-Content -Path $Path -Tail $MaxLines -Encoding UTF8)
    }
    foreach ($line in $lines) {
        if (-not $line) { continue }
        try { [void]$items.Add(($line | ConvertFrom-Json)) } catch {}
    }
    return $items
}

function Read-ClaimObjects {
    $items = New-Object System.Collections.Generic.List[object]
    if (-not (Test-Path -LiteralPath $claimsDir)) { return $items }
    foreach ($file in @(Get-ChildItem -Path $claimsDir -Filter '*.json' -File -ErrorAction SilentlyContinue)) {
        try { [void]$items.Add((Get-Content -Raw -Path $file.FullName -Encoding UTF8 | ConvertFrom-Json)) } catch {}
    }
    return $items
}

function Test-IsRequestLike {
    param([Parameter(Mandatory)] [object] $Event)
    $requestTypes = @('message','handoff','blocked','finding','decision','done')
    $requestStatuses = @(
        'request','ready','blocked','open','proposal',
        'fix-pushed','fix-branch-pushed','pushed',
        'ready_for_implementation'
    )
    return (
        $Event.PSObject.Properties['to'] -and
        [string]$Event.to -and
        [string]$Event.task_id -and
        $requestTypes -contains [string]$Event.type -and
        $requestStatuses -contains [string]$Event.status
    )
}

function Test-IsAnswerEvent {
    param([Parameter(Mandatory)] [object] $Event)
    $status = [string]$Event.status
    $type = [string]$Event.type
    if (@('received','seen','acknowledged') -contains $status) { return $false }
    if ($type -eq 'message') { return $status -eq 'answered' }
    return @('done','finding','decision','blocked','handoff','test','claim','release') -contains $type
}

function Test-IsRequesterClosureEvent {
    param([Parameter(Mandatory)] [object] $Event)
    $status = [string]$Event.status
    $type = [string]$Event.type
    if ($type -eq 'message') {
        return @('closed','superseded','cancelled','canceled') -contains $status
    }
    if (@('done','release','decision') -notcontains $type) { return $false }
    return @(
        'done','closed','superseded','merged','abandoned',
        'completed','approved','cancelled','canceled'
    ) -contains $status
}

function Format-BridgeText {
    param([object] $Value, [int] $MaxLength = 180)
    $text = ([string]$Value) -replace '\s+', ' '
    $text = $text.Trim()
    if ($text.Length -le $MaxLength) { return $text }
    return ($text.Substring(0, [Math]::Max(0, $MaxLength - 3)) + '...')
}

$events = Read-EventObjects -Path $eventsPath -MaxLines $Tail
$claims = Read-ClaimObjects
$agents = @('claude','codex')

$contributions = @()
foreach ($agent in $agents) {
    $agentEvents = @($events | Where-Object { [string]$_.agent -eq $agent })
    $agentClaims = @($claims | Where-Object { [string]$_.agent -eq $agent })
    $lastEvent = @($agentEvents | Sort-Object ts_utc | Select-Object -Last 1)
    $contributions += [pscustomobject]@{
        agent          = $agent
        events         = $agentEvents.Count
        active_claims  = $agentClaims.Count
        done_events    = @($agentEvents | Where-Object { [string]$_.type -eq 'done' }).Count
        merged         = @($agentEvents | Where-Object { [string]$_.status -eq 'merged' }).Count
        pushed         = @($agentEvents | Where-Object { [string]$_.status -in @('pushed','fix-pushed','fix-branch-pushed') }).Count
        tests_pass     = @($agentEvents | Where-Object { [string]$_.type -eq 'test' -and [string]$_.status -eq 'pass' }).Count
        tests_fail     = @($agentEvents | Where-Object { [string]$_.type -eq 'test' -and [string]$_.status -in @('fail','failed') }).Count
        findings_open  = @($agentEvents | Where-Object { [string]$_.type -eq 'finding' -and [string]$_.status -eq 'open' }).Count
        received_acks  = @($agentEvents | Where-Object { [string]$_.type -eq 'message' -and [string]$_.status -eq 'received' }).Count
        last_event_utc = if ($lastEvent.Count -gt 0) { [string]$lastEvent[-1].ts_utc } else { '' }
    }
}

$latestRequests = @{}
foreach ($event in @($events | Where-Object { Test-IsRequestLike -Event $_ } | Sort-Object ts_utc)) {
    $key = "{0}|{1}" -f [string]$event.to, [string]$event.task_id
    $latestRequests[$key] = $event
}

$requestStates = @()
foreach ($key in ($latestRequests.Keys | Sort-Object)) {
    $request = $latestRequests[$key]
    $target = [string]$request.to
    $taskId = [string]$request.task_id
    $answer = @(
        $events |
            Where-Object {
                [string]$_.agent -eq $target -and
                [string]$_.task_id -eq $taskId -and
                [string]$_.ts_utc -gt [string]$request.ts_utc -and
                (Test-IsAnswerEvent -Event $_)
            } |
            Sort-Object ts_utc |
            Select-Object -Last 1
    )
    $received = @(
        $events |
            Where-Object {
                [string]$_.agent -eq $target -and
                [string]$_.task_id -eq $taskId -and
                [string]$_.type -eq 'message' -and
                [string]$_.status -eq 'received' -and
                [string]$_.ts_utc -gt [string]$request.ts_utc
            } |
            Sort-Object ts_utc |
            Select-Object -Last 1
    )
    $closure = @(
        $events |
            Where-Object {
                [string]$_.agent -eq [string]$request.agent -and
                [string]$_.task_id -eq $taskId -and
                [string]$_.ts_utc -gt [string]$request.ts_utc -and
                (Test-IsRequesterClosureEvent -Event $_)
            } |
            Sort-Object ts_utc |
            Select-Object -Last 1
    )
    $state = 'waiting'
    if ($answer.Count -gt 0) {
        $state = 'answered'
    } elseif ($closure.Count -gt 0) {
        $state = 'closed'
    } elseif ($received.Count -gt 0) {
        $state = 'received'
    }
    $requestStates += [pscustomobject]@{
        state     = $state
        to        = $target
        from      = [string]$request.agent
        task_id   = $taskId
        request   = ("{0}/{1}" -f [string]$request.type, [string]$request.status)
        ts_utc    = [string]$request.ts_utc
        message   = [string]$request.message
    }
}

$recentSubstantive = @(
    $events |
        Where-Object {
            -not ([string]$_.type -eq 'message' -and [string]$_.status -eq 'received') -and
            [string]$_.type -notin @('heartbeat','liveness')
        } |
        Sort-Object ts_utc |
        Select-Object -Last $Recent
)

$lastSubstantive = @($recentSubstantive | Select-Object -Last 1)
$nextSuggested = ''
if ($lastSubstantive.Count -gt 0) {
    $lastAgent = [string]$lastSubstantive[-1].agent
    if ($lastAgent -eq 'claude') { $nextSuggested = 'codex' }
    elseif ($lastAgent -eq 'codex') { $nextSuggested = 'claude' }
}

$idleSignals = @()
foreach ($agent in $agents) {
    $claimCount = @($claims | Where-Object { [string]$_.agent -eq $agent }).Count
    $pendingForAgent = @($requestStates | Where-Object { $_.to -eq $agent -and $_.state -ne 'answered' })
    $state = 'active'
    $next = 'continue active claim'
    if ($claimCount -eq 0 -and $pendingForAgent.Count -gt 0) {
        $state = 'needs-work'
        $next = ("process {0}" -f $pendingForAgent[0].task_id)
    } elseif ($claimCount -eq 0) {
        $state = 'idle'
        $next = 'claim scout, review, or unblocked implementation task'
    }
    $idleSignals += [pscustomobject]@{
        agent = $agent
        state = $state
        next  = $next
    }
}

$result = [ordered]@{
    generated_utc      = (Get-Date).ToUniversalTime().ToString('o')
    active_claims      = @($claims)
    contribution_count = @($contributions)
    unresolved_requests = @($requestStates | Where-Object { $_.state -notin @('answered','closed') })
    recent_events      = @($recentSubstantive)
    suggested_next_reviewer = $nextSuggested
    idle_signals       = @($idleSignals)
}

if ($Json) {
    $result | ConvertTo-Json -Depth 12
    exit 0
}

Write-Host 'ACTIVE CLAIMS' -ForegroundColor Cyan
if ($claims.Count -eq 0) {
    Write-Host '  (none)'
} else {
    foreach ($claim in $claims) {
        $branch = ''
        if ($claim.PSObject.Properties['git_branch'] -and [string]$claim.git_branch) {
            $branch = " branch=$([string]$claim.git_branch)"
        }
        $scope = ''
        if ($claim.PSObject.Properties['write_scope'] -and @($claim.write_scope).Count -gt 0) {
            $scope = " scope=$((@($claim.write_scope)) -join ',')"
        }
        Write-Host ("  {0} {1} [{2}]{3}: {4}{5}" -f `
            [string]$claim.agent, [string]$claim.task_id, [string]$claim.mode, $branch, (Format-BridgeText $claim.summary), $scope)
    }
}

Write-Host 'AGENT CONTRIBUTIONS' -ForegroundColor Cyan
$contributions | Format-Table -AutoSize

Write-Host 'UNRESOLVED REQUESTS' -ForegroundColor Cyan
$unresolved = @($requestStates | Where-Object { $_.state -notin @('answered','closed') })
if ($unresolved.Count -eq 0) {
    Write-Host '  (none)'
} else {
    $unresolvedForDisplay = @($unresolved | Sort-Object ts_utc -Descending | Select-Object -First $MaxUnresolved)
    foreach ($item in $unresolvedForDisplay) {
        Write-Host ("  {0} {1} <- {2} {3} [{4}] {5}: {6}" -f `
            $item.state, $item.to, $item.from, $item.task_id, $item.request, $item.ts_utc, (Format-BridgeText $item.message))
    }
    if ($unresolved.Count -gt $unresolvedForDisplay.Count) {
        Write-Host ("  ... {0} older unresolved request(s) hidden; use -Json for the full list or raise -MaxUnresolved." -f ($unresolved.Count - $unresolvedForDisplay.Count))
    }
}

Write-Host 'IDLE / NEXT ACTION SIGNALS' -ForegroundColor Cyan
$idleSignals | Format-Table -AutoSize

Write-Host 'RECENT SUBSTANTIVE EVENTS' -ForegroundColor Cyan
if ($recentSubstantive.Count -eq 0) {
    Write-Host '  (none)'
} else {
    foreach ($event in $recentSubstantive) {
        $target = if ([string]$event.to) { " -> $([string]$event.to)" } else { '' }
        Write-Host ("  {0} [{1}{2}] {3}/{4} {5}: {6}" -f `
            [string]$event.ts_utc, [string]$event.agent, $target, [string]$event.type, [string]$event.status, [string]$event.task_id, (Format-BridgeText $event.message))
    }
}

if ($nextSuggested) {
    Write-Host ("SUGGESTED NEXT REVIEWER: {0}" -f $nextSuggested) -ForegroundColor Cyan
}
