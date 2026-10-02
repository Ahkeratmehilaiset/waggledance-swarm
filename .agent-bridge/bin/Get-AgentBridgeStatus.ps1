#requires -Version 5.1
[CmdletBinding()]
param(
    [int] $Tail = 1000,
    [int] $Recent = 12,
    [int] $MaxUnresolved = 25,
    [switch] $Json
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# R13: honor AGENT_BRIDGE_RUNTIME_ROOT. If env var is SET, USE IT
# (create root if missing, fail loud on malformed path).
$bridgeRoot = if ($env:AGENT_BRIDGE_RUNTIME_ROOT) {
    [string]$env:AGENT_BRIDGE_RUNTIME_ROOT
} else {
    Split-Path -Parent $PSScriptRoot
}
if (-not (Test-Path -LiteralPath $bridgeRoot -PathType Container)) {
    [void](New-Item -ItemType Directory -Path $bridgeRoot -Force -ErrorAction Stop)
}

# R15: opportunistic stale-claim sweep before showing status. It stays best-effort for the rest
# of the status, but a sweep that fails is reported (F23a), never swallowed.
$staleSweep = Join-Path $PSScriptRoot 'Invoke-StaleClaimSweep.ps1'
$sweepError = $null
if (Test-Path -LiteralPath $staleSweep -PathType Leaf) {
    try {
        # -Json keeps stdout a single JSON document: the sweep's warnings are not written there;
        # what they describe is in sweep_emit_failures (receipts and sweep_error).
        if ($Json) {
            & $staleSweep -Quiet -WarningAction SilentlyContinue | Out-Null
        } else {
            & $staleSweep -Quiet | Out-Null
        }
    } catch {
        $sweepError = [string]$_.Exception.Message
    }
}
$eventsPath = Join-Path (Join-Path $bridgeRoot 'shared') 'events.jsonl'
$claimsDir = Join-Path (Join-Path $bridgeRoot 'work_queue') 'claims'
$classifier = Join-Path $PSScriptRoot 'BridgeEventClassifier.ps1'
. (Join-Path $PSScriptRoot 'BridgeRequestContract.ps1')
. (Join-Path $PSScriptRoot 'BridgeRoster.ps1')
if (Test-Path -LiteralPath $classifier -PathType Leaf) {
    . $classifier
}

function Read-EventObjects {
    param([Parameter(Mandatory)] [string] $Path, [int] $MaxLines = 50000)
    $items = New-Object System.Collections.Generic.List[object]
    if (-not (Test-Path -LiteralPath $Path)) { return $items }
    $lines = @(if ($MaxLines -le 0) {
        Get-Content -Path $Path -Encoding UTF8
    } else {
        Get-Content -Path $Path -Tail $MaxLines -Encoding UTF8
    })
    foreach ($line in $lines) {
        if (-not $line) { continue }
        try { [void]$items.Add(($line | ConvertFrom-Json)) } catch {}
    }
    return $items
}

function Read-SweepEmitFailures {
    <#
        F23a (RCO1 2026-10-01): stale releases the sweep archived but could not publish, read
        from work_queue/sweep_emit_failures without changing anything. A receipt that does not
        parse or lacks a field is listed as malformed, never dropped and never read as published.
        At most $Limit receipts (newest first by name) are listed; count is the full total.
    #>
    param([Parameter(Mandatory)] [string] $Root, [int] $Limit = 50)

    $folder = Join-Path (Join-Path $Root 'work_queue') 'sweep_emit_failures'
    $view = [ordered]@{
        readable = $true; count = 0; truncated = $false
        receipts = @(); malformed = @(); read_error = $null; sweep_error = $null
    }
    if (-not (Test-Path -LiteralPath $folder)) { return $view }
    if (-not [System.IO.Directory]::Exists($folder)) {
        $view.readable = $false
        $view.read_error = 'work_queue/sweep_emit_failures exists but is not a directory'
        return $view
    }
    $files = @()
    try {
        $files = @(Get-ChildItem -LiteralPath $folder -Filter '*.json' -File -ErrorAction Stop |
            Sort-Object Name -Descending)
    } catch {
        $view.readable = $false
        $view.read_error = [string]$_.Exception.Message
        return $view
    }
    $receipts = New-Object System.Collections.Generic.List[object]
    $malformed = New-Object System.Collections.Generic.List[object]
    foreach ($file in $files) {
        $reason = $null
        $item = $null
        try {
            $item = [System.IO.File]::ReadAllText($file.FullName) | ConvertFrom-Json -ErrorAction Stop
        } catch { $reason = 'unreadable_json' }
        $fields = @{}
        if ($null -eq $reason) {
            foreach ($name in @('schema', 'task_id', 'claim_agent', 'archived_name', 'archived_path',
                    'observed_at_utc', 'phase', 'publication', 'error')) {
                $value = $null
                if ($item -is [psobject] -and $item.PSObject.Properties[$name]) { $value = $item.$name }
                # pwsh 7 turns an ISO timestamp string into a DateTime; give the writer's text back.
                if ($value -is [DateTime]) {
                    $value = $value.ToUniversalTime().ToString('o', [System.Globalization.CultureInfo]::InvariantCulture)
                }
                if ($value -isnot [string] -or -not $value) { $reason = 'missing_' + $name; break }
                $fields[$name] = $value
            }
        }
        if ($null -eq $reason -and $fields['schema'] -cne 'wd.bridge-sweep-emit-failure.v1') { $reason = 'unknown_schema' }
        if ($null -eq $reason -and $fields['publication'] -cnotin @('failed', 'publication_unknown')) {
            $reason = 'invalid_publication'
        }
        if ($null -ne $reason) {
            $malformed.Add([pscustomobject][ordered]@{ name = $file.Name; reason = $reason })
            continue
        }
        $receipts.Add([pscustomobject][ordered]@{
            task_id = $fields['task_id']; claim_agent = $fields['claim_agent']
            archived_name = $fields['archived_name']; archived_path = $fields['archived_path']
            observed_at_utc = $fields['observed_at_utc']; phase = $fields['phase']
            publication = $fields['publication']; error = $fields['error']; receipt = $file.Name
        })
    }
    # Indexer, not .count: an ordered dictionary's own Count property hides that key.
    $view['count'] = $receipts.Count
    $view.truncated = $receipts.Count -gt $Limit
    # ToArray(): @() over an empty generic List throws 'Argument types do not match' here.
    $view.receipts = [object[]]@($receipts.ToArray() | Select-Object -First $Limit)
    $view.malformed = [object[]]$malformed.ToArray()
    return $view
}

function Read-ClaimObjects {
    $items = New-Object System.Collections.Generic.List[object]
    if (-not (Test-Path -LiteralPath $claimsDir)) { return $items }
    foreach ($file in @(Get-ChildItem -Path $claimsDir -Filter '*.json' -File -ErrorAction SilentlyContinue)) {
        try { [void]$items.Add((Get-Content -Raw -Path $file.FullName -Encoding UTF8 | ConvertFrom-Json)) } catch {}
    }
    return $items
}

function Format-BridgeText {
    param([object] $Value, [int] $MaxLength = 180)
    $text = ([string]$Value) -replace '\s+', ' '
    $text = $text.Trim()
    if ($text.Length -le $MaxLength) { return $text }
    return ($text.Substring(0, [Math]::Max(0, $MaxLength - 3)) + '...')
}

$events = @(Read-EventObjects -Path $eventsPath -MaxLines $Tail)
$claims = @(Read-ClaimObjects)
$agents = @(
    @($events | ForEach-Object { [string]$_.agent })
    @($claims | ForEach-Object { [string]$_.agent })
    'claude'
    'codex'
) | Where-Object { $_ } | Sort-Object -Unique

$contributions = @()
foreach ($agent in $agents) {
    $agentEvents = @($events | Where-Object { [string]$_.agent -eq $agent })
    $agentClaims = @($claims | Where-Object { [string]$_.agent -eq $agent })
    $lastEvent = @($agentEvents | Sort-Object ts_utc | Select-Object -Last 1)
    $contributions += [pscustomobject]@{
        agent          = $agent
        worker_class   = Get-BridgeWorkerClass $agent
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

$latestRequests = [System.Collections.Generic.Dictionary[string,object]]::new([StringComparer]::Ordinal)
foreach ($event in @($events | Where-Object { Test-BridgeRequestLikeEvent -Event $_ } | Sort-Object ts_utc)) {
    foreach ($target in @(Get-BridgeEventTargets -Event $event)) {
        $key = Get-BridgeRequestViewKey $event $target
        if ((Get-BridgeContractField $event 'request_id') -and $latestRequests.ContainsKey($key)) {
            # Ordinal, as Test-BridgeRequestEntryDiffers: culture-aware -cne ignored U+00AD (and U+200B in pwsh 7).
            if (-not [string]::Equals((Get-BridgeRequestContent $latestRequests[$key].event), (Get-BridgeRequestContent $event), [StringComparison]::Ordinal) -or
                -not [string]::Equals((ConvertTo-BridgeContractJson (Get-BridgeContractField $latestRequests[$key].event 'request_digest')),
                    (ConvertTo-BridgeContractJson (Get-BridgeContractField $event 'request_digest')), [StringComparison]::Ordinal)) {
                $latestRequests[$key].event | Add-Member -Force NoteProperty request_binding_conflict $true
            }
            continue
        }
        $latestRequests[$key] = [pscustomobject]@{
            target = $target
            event = $event
        }
    }
}

$requestStates = @()
foreach ($key in ($latestRequests.Keys | Sort-Object)) {
    $requestInfo = $latestRequests[$key]
    $request = $requestInfo.event
    $target = [string]$requestInfo.target
    $taskId = [string]$request.task_id
    $ambiguous = @($events | Where-Object { $_.agent -ceq $request.agent -and $_.task_id -ceq $taskId -and (Test-BridgeRequestLikeEvent $_) } | Select-Object -ExpandProperty ts_utc -Unique).Count -gt 1
    $answer = @(
        $events |
            Where-Object {
                [string]$_.agent -eq $target -and
                [string]$_.task_id -eq $taskId -and
                [string]$_.ts_utc -gt [string]$request.ts_utc -and
                (Test-BridgeAnswerEvent -Event $_) -and
                (Test-BridgeReplyBinding $request $_ $target -AmbiguousLegacy $ambiguous)
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
                (Test-BridgeReplyBinding $request $_ $target -AmbiguousLegacy $ambiguous)
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
                (Test-BridgeRequesterClosureEvent -Event $_) -and
                (Test-BridgeReplyBinding $request $_ $target -RequesterClosure $true -AmbiguousLegacy $ambiguous)
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
        request_id = Get-BridgeContractField $request 'request_id'
        age_seconds = [math]::Max(0, ([datetime]::UtcNow - (ConvertTo-BridgeContractTime $request.ts_utc)).TotalSeconds)
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
    $pendingForAgent = @(
        $requestStates |
            Where-Object { $_.to -eq $agent -and $_.state -notin @('answered','closed') }
    )
    $state = 'active'
    $next = 'continue active claim'
    if ($claimCount -eq 0 -and $pendingForAgent.Count -gt 0) {
        $state = 'needs-work'
        $next = ("process {0}" -f $pendingForAgent[0].task_id)
    } elseif ($claimCount -eq 0) {
        $state = 'idle'
        $next = 'claim scout, review, or unblocked implementation task'
    }
    $workerClass = Get-BridgeWorkerClass $agent
    if ($workerClass -eq 'on_demand' -and $pendingForAgent.Count -eq 0) {
        $state = 'on-demand-idle'; $next = 'Use only the existing hourly-budgeted Grok helper when requested'
    } elseif ($workerClass -eq 'historical') {
        $state = 'historical'; $next = 'Not an active fleet worker; inspect explicit outstanding requests separately'
    }
    $idleSignals += [pscustomobject]@{
        agent = $agent
        worker_class = $workerClass
        state = $state
        next  = $next
    }
}

$sweepEmitFailures = Read-SweepEmitFailures -Root $bridgeRoot
$sweepEmitFailures.sweep_error = $sweepError
$result = [ordered]@{
    generated_utc      = (Get-Date).ToUniversalTime().ToString('o')
    active_claims      = @($claims)
    contribution_count = @($contributions)
    unresolved_requests = @($requestStates | Where-Object { $_.state -notin @('answered','closed') })
    recent_events      = @($recentSubstantive)
    suggested_next_reviewer = $nextSuggested
    idle_signals       = @($idleSignals)
    sweep_emit_failures = $sweepEmitFailures
}

if ($Json) {
    $result | ConvertTo-Json -Depth 12
    exit 0
}

$sweepView = $sweepEmitFailures
if ($sweepView['count'] -gt 0 -or @($sweepView.malformed).Count -gt 0 -or
    -not $sweepView.readable -or $sweepView.sweep_error) {
    Write-Host 'STALE-SWEEP RELEASE EVENTS NOT PUBLISHED' -ForegroundColor Yellow
    if ($sweepView.sweep_error) { Write-Host ("  sweep failed: {0}" -f $sweepView.sweep_error) }
    if (-not $sweepView.readable) { Write-Host ("  receipts unreadable: {0}" -f $sweepView.read_error) }
    foreach ($receipt in @($sweepView.receipts)) {
        Write-Host ("  {0} {1} by {2} [{3}/{4}] archived {5}: {6}" -f `
            $receipt.observed_at_utc, $receipt.task_id, $receipt.claim_agent, $receipt.publication,
            $receipt.phase, $receipt.archived_name, (Format-BridgeText $receipt.error))
    }
    if ($sweepView.truncated) {
        Write-Host ("  ... {0} receipt(s) in total; use -Json for the listed ones." -f $sweepView['count'])
    }
    foreach ($bad in @($sweepView.malformed)) {
        Write-Host ("  malformed receipt {0}: {1}" -f $bad.name, $bad.reason)
    }
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
