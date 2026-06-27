#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateScript({ $_ -eq '' -or $_ -cmatch '^[a-z][a-z0-9_-]{1,32}$' })] [string] $Agent = '',
    [int] $Tail = 40,
    # Keep the interactive continuity view responsive on large bridge logs.
    # Use 0 only for deep audits that intentionally scan all history.
    [int] $ContinuityTail = 5000,
    [switch] $OtherOnly,
    [switch] $ShowClaims,
    [switch] $ShowLiveness,
    [switch] $ShowScoreboard,
    [switch] $NoContinuity,
    [switch] $NoAckReceived,
    [switch] $Raw
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
$eventsPath = Join-Path (Join-Path $bridgeRoot 'shared') 'events.jsonl'
$classifier = Join-Path $PSScriptRoot 'BridgeEventClassifier.ps1'
if (Test-Path -LiteralPath $classifier -PathType Leaf) {
    . $classifier
}

# R15: opportunistic stale-claim sweep on every read. Cheap (only
# walks the small active-claims dir). The sweep emits its own
# release/stale_lease events that downstream readers see.
$staleSweep = Join-Path $PSScriptRoot 'Invoke-StaleClaimSweep.ps1'
if (Test-Path -LiteralPath $staleSweep -PathType Leaf) {
    try {
        & $staleSweep -Quiet | Out-Null
    } catch {
        # Sweep is best-effort: a sweep failure must NOT prevent
        # the read from showing the rest of the bridge state.
    }
}

function Read-BridgeEventObjects {
    # Internal review fix R1/A1 (2026-05-09): default 5000 was too low.
    # Heartbeat traffic at 60s * 2 agents is about 2880 events/day, so the
    # tail-truncation silently dropped continuity-section events older
    # than ~2 days. 50000 covers about a month; pass -MaxLines 0 for
    # unlimited (use sparingly).
    param([Parameter(Mandatory)] [string] $Path, [int] $MaxLines = 50000)
    $items = New-Object System.Collections.Generic.List[object]
    if (-not (Test-Path -LiteralPath $Path)) { return $items }
    $allLines = @(if ($MaxLines -le 0) {
        Get-Content -Path $Path -Encoding UTF8
    } else {
        Get-Content -Path $Path -Tail $MaxLines -Encoding UTF8
    })
    foreach ($line in $allLines) {
        if (-not $line) { continue }
        try {
            [void]$items.Add(($line | ConvertFrom-Json))
        } catch {}
    }
    return $items
}

function Read-BridgeContinuityEventObjects {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $AgentName,
        [int] $MaxLines = 5000
    )

    $items = New-Object System.Collections.Generic.List[object]
    if (-not (Test-Path -LiteralPath $Path)) { return $items }

    $allLines = @(if ($MaxLines -le 0) {
        Get-Content -Path $Path -Encoding UTF8
    } else {
        Get-Content -Path $Path -Tail $MaxLines -Encoding UTF8
    })

    $selectedIndexes = New-Object 'System.Collections.Generic.HashSet[int]'
    $agentFieldNeedle = '"agent":"' + $AgentName + '"'
    $toFieldRegex = [regex]::new(
        '"to":"[^"]*' + [regex]::Escape($AgentName) + '[^"]*"',
        [System.Text.RegularExpressions.RegexOptions]::Compiled -bor
            [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
    )
    $requestLikeStatusRegex = [regex]::new(
        '"status":"(?:request|ready|blocked|open|proposal|fix-pushed|fix-branch-pushed|pushed|ready_for_implementation|rco_requested|review_requested|changes_requested|proposal_ready|[^"]*proposal[^"]*)"',
        [System.Text.RegularExpressions.RegexOptions]::Compiled -bor
            [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
    )

    for ($i = 0; $i -lt $allLines.Count; $i++) {
        $line = [string]$allLines[$i]
        if (-not $line) { continue }
        if ($line.IndexOf('"type":"heartbeat"', [System.StringComparison]::Ordinal) -ge 0 -or
            $line.IndexOf('"type":"liveness"', [System.StringComparison]::Ordinal) -ge 0) {
            continue
        }
        $isAddressedToAgent = $toFieldRegex.IsMatch($line)
        $isOwnRequestLike = (
            $line.IndexOf($agentFieldNeedle, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and
            $requestLikeStatusRegex.IsMatch($line)
        )
        if (-not $isAddressedToAgent -and -not $isOwnRequestLike) {
            continue
        }
        try {
            $event = $line | ConvertFrom-Json
        } catch {
            continue
        }
        [void]$selectedIndexes.Add($i)
        [void]$items.Add($event)
    }

    $taskIds = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach ($event in $items) {
        $taskId = [string]$event.task_id
        if ($taskId -and
            (Test-BridgeRequestLikeEvent -Event $event) -and
            ((Test-BridgeAddressedTo -Event $event -TargetAgent $AgentName) -or
                [string]$event.agent -eq $AgentName)) {
            [void]$taskIds.Add($taskId)
        }
    }

    if ($taskIds.Count -gt 0) {
        $taskIdPattern = (@($taskIds) | ForEach-Object { [regex]::Escape([string]$_) }) -join '|'
        $taskIdRegex = [regex]::new(
            $taskIdPattern,
            [System.Text.RegularExpressions.RegexOptions]::Compiled
        )
        for ($i = 0; $i -lt $allLines.Count; $i++) {
            if ($selectedIndexes.Contains($i)) { continue }
            $line = [string]$allLines[$i]
            if (-not $line) { continue }
            if (-not $taskIdRegex.IsMatch($line)) { continue }
            try {
                [void]$items.Add(($line | ConvertFrom-Json))
            } catch {}
        }
    }

    return @($items | Sort-Object ts_utc)
}

function Send-ReceivedAck {
    param(
        [Parameter(Mandatory)] [string] $AgentName,
        [Parameter(Mandatory)] [object] $RequestEvent,
        [Parameter(Mandatory)] [System.Collections.Generic.List[object]] $AllEvents
    )

    $taskId = [string]$RequestEvent.task_id
    $requestTs = [string]$RequestEvent.ts_utc
    if (-not $taskId -or -not $requestTs) { return $false }

    $alreadyAcked = @(
        $AllEvents |
            Where-Object {
                [string]$_.agent -eq $AgentName -and
                [string]$_.task_id -eq $taskId -and
                [string]$_.type -eq 'message' -and
                [string]$_.status -eq 'received' -and
                $_.PSObject.Properties['payload'] -and
                $_.payload.PSObject.Properties['request_ts_utc'] -and
                [string]$_.payload.request_ts_utc -eq $requestTs
            } |
            Select-Object -First 1
    )
    if ($alreadyAcked.Count -gt 0) { return $true }

    $payloadJson = ([ordered]@{
        request_ts_utc = $requestTs
        request_agent  = [string]$RequestEvent.agent
        request_type   = [string]$RequestEvent.type
        request_status = [string]$RequestEvent.status
    } | ConvertTo-Json -Depth 6 -Compress)

    $message = "received {0}/{1} from {2}" -f `
        [string]$RequestEvent.type, [string]$RequestEvent.status, [string]$RequestEvent.agent

    & (Join-Path $PSScriptRoot 'Write-AgentEvent.ps1') `
        -Agent $AgentName `
        -To ([string]$RequestEvent.agent) `
        -Type message `
        -Status received `
        -TaskId $taskId `
        -Message $message `
        -PayloadJson $payloadJson | Out-Null

    return $true
}

function Get-BridgeEventTimestampSafe {
    # Internal review fix R4 (2026-05-09): bare [DateTime]::Parse on an
    # empty or malformed ts_utc crashes the whole reader. This wrapper
    # Returns $null on any parse failure so callers can fall back to
    # "no event" handling instead of an unhandled exception.
    param([object] $TsUtc)
    if ($null -eq $TsUtc) { return $null }
    if ($TsUtc -is [DateTime]) {
        return $TsUtc.ToUniversalTime()
    }
    $text = [string]$TsUtc
    if (-not $text) { return $null }
    try {
        return [DateTime]::Parse(
            $text,
            [System.Globalization.CultureInfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::AssumeUniversal -bor
                [System.Globalization.DateTimeStyles]::AdjustToUniversal
        ).ToUniversalTime()
    } catch {
        return $null
    }
}

if ($ShowClaims) {
    $claimsDir = Join-Path (Join-Path $bridgeRoot 'work_queue') 'claims'
    Write-Host 'ACTIVE CLAIMS' -ForegroundColor Cyan
    if (Test-Path -LiteralPath $claimsDir) {
        $claims = @(Get-ChildItem -Path $claimsDir -Filter '*.json' -File | Sort-Object LastWriteTime)
        if ($claims.Count -eq 0) {
            Write-Host '  (none)'
        } else {
            foreach ($c in $claims) {
                try {
                    $obj = Get-Content -Raw -Path $c.FullName -Encoding UTF8 | ConvertFrom-Json
                    Write-Host ("  {0} [{1}] by {2}: {3}" -f $obj.task_id, $obj.mode, $obj.agent, $obj.summary)
                    if ($obj.PSObject.Properties['role'] -and [string]$obj.role) {
                        Write-Host ("    role: {0}" -f [string]$obj.role)
                    }
                    if ($obj.PSObject.Properties['agent_uuid'] -and [string]$obj.agent_uuid) {
                        Write-Host ("    agent_uuid: {0}" -f [string]$obj.agent_uuid)
                    }
                    if ($obj.PSObject.Properties['claim_lease_expires_utc'] -and [string]$obj.claim_lease_expires_utc) {
                        Write-Host ("    lease_expires: {0}" -f [string]$obj.claim_lease_expires_utc)
                    }
                    if ($obj.write_scope -and @($obj.write_scope).Count -gt 0) {
                        Write-Host ("    scope: {0}" -f ((@($obj.write_scope)) -join ', '))
                    }
                } catch {
                    Write-Host ("  unreadable claim: {0}" -f $c.Name) -ForegroundColor Yellow
                }
            }
        }
    } else {
        Write-Host '  (none)'
    }
    Write-Host ''
}

if ($Agent -and -not $NoContinuity) {
    Write-Host ("CONTINUITY FOR {0}" -f $Agent) -ForegroundColor Cyan
    if (-not (Test-Path -LiteralPath $eventsPath)) {
        Write-Host '  (no events.jsonl yet)'
        Write-Host ''
    } else {
        $allEvents = @(Read-BridgeContinuityEventObjects -Path $eventsPath -AgentName $Agent -MaxLines $ContinuityTail)
        $displayEvents = @(Read-BridgeEventObjects -Path $eventsPath -MaxLines $Tail)
        $displayTaskIds = @{}
        foreach ($displayEvent in $displayEvents) {
            $displayTaskId = [string]$displayEvent.task_id
            if ($displayTaskId) {
                $displayTaskIds[$displayTaskId] = $true
            }
        }
        $requests = @(
            $allEvents |
                Where-Object {
                    (Test-BridgeRequestLikeEvent -Event $_) -and
                    (Test-BridgeAddressedTo -Event $_ -TargetAgent $Agent) -and
                    [string]$_.agent -ne $Agent -and
                    [string]$_.task_id
                } |
                Sort-Object ts_utc
        )

        if ($requests.Count -eq 0) {
            Write-Host '  incoming: (none)'
        } else {
            Write-Host '  incoming:'
            $latestByTask = @{}
            foreach ($r in $requests) {
                $latestByTask[[string]$r.task_id] = $r
            }
            $receivedByTask = @{}
            $hiddenResolvedCount = 0
            $replyByTask = @{}
            $closureByTask = @{}
            foreach ($event in $allEvents) {
                $eventTaskId = [string]$event.task_id
                if (-not $eventTaskId -or -not $latestByTask.ContainsKey($eventTaskId)) {
                    continue
                }
                $requestForTask = $latestByTask[$eventTaskId]
                if ([string]$event.ts_utc -le [string]$requestForTask.ts_utc) {
                    continue
                }
                if ([string]$event.agent -eq $Agent -and (Test-BridgeAnswerEvent -Event $event)) {
                    $replyByTask[$eventTaskId] = $event
                    continue
                }
                if ([string]$event.agent -eq [string]$requestForTask.agent -and (Test-BridgeRequesterClosureEvent -Event $event)) {
                    $closureByTask[$eventTaskId] = $event
                }
            }
            if (-not $NoAckReceived -and -not $Raw) {
                foreach ($taskId in $latestByTask.Keys) {
                    $receivedByTask[$taskId] = Send-ReceivedAck `
                        -AgentName $Agent `
                        -RequestEvent $latestByTask[$taskId] `
                        -AllEvents $allEvents
                }
            }
            foreach ($taskId in ($latestByTask.Keys | Sort-Object)) {
                $req = $latestByTask[$taskId]
                $reply = @()
                if ($replyByTask.ContainsKey($taskId)) {
                    $reply = @($replyByTask[$taskId])
                }
                if ($reply.Count -gt 0) {
                    $last = $reply[-1]
                    if ($displayTaskIds.ContainsKey($taskId)) {
                        Write-Host ("  answered {0}: request {1}/{2} -> {3}/{4}" -f `
                            $taskId, $req.type, $req.status, $last.type, $last.status)
                    } else {
                        $hiddenResolvedCount++
                    }
                } else {
                    $closure = @()
                    if ($closureByTask.ContainsKey($taskId)) {
                        $closure = @($closureByTask[$taskId])
                    }
                    if ($closure.Count -gt 0) {
                        $last = $closure[-1]
                        if ($displayTaskIds.ContainsKey($taskId)) {
                            Write-Host ("  closed-by-requester {0}: request {1}/{2} -> {3}/{4}" -f `
                                $taskId, $req.type, $req.status, $last.type, $last.status)
                        } else {
                            $hiddenResolvedCount++
                        }
                        continue
                    }
                    $receivedSuffix = ''
                    if ($receivedByTask.ContainsKey($taskId) -and $receivedByTask[$taskId]) {
                        $receivedSuffix = ' (received)'
                    }
                    Write-Host ("  OPEN {0}{1}: {2}/{3} from {4}: {5}" -f `
                        $taskId, $receivedSuffix, $req.type, $req.status, $req.agent, $req.message) `
                        -ForegroundColor Yellow
                }
            }
            if ($hiddenResolvedCount -gt 0) {
                Write-Host ("  ({0} answered/closed item(s) outside -Tail hidden)" -f $hiddenResolvedCount)
            }
        }

        $sentRequests = New-Object System.Collections.Generic.List[object]
        foreach ($r in @($allEvents | Where-Object {
                    [string]$_.agent -eq $Agent -and
                    (Test-BridgeRequestLikeEvent -Event $_)
                } | Sort-Object ts_utc)) {
            foreach ($target in @(Get-BridgeEventTargets -Event $r)) {
                if ($target -and $target -ne $Agent) {
                    [void]$sentRequests.Add([pscustomobject]@{
                        target = $target
                        event = $r
                    })
                }
            }
        }
        if ($sentRequests.Count -eq 0) {
            Write-Host '  outgoing: (none)'
        } else {
            Write-Host '  outgoing:'
            $sentLatestByTask = @{}
            foreach ($r in $sentRequests) {
                $sentKey = "{0}|{1}" -f [string]$r.target, [string]$r.event.task_id
                $sentLatestByTask[$sentKey] = $r
            }
            $sentKeysByTask = @{}
            foreach ($sentKey in $sentLatestByTask.Keys) {
                $sentTaskId = [string]$sentLatestByTask[$sentKey].event.task_id
                if (-not $sentKeysByTask.ContainsKey($sentTaskId)) {
                    $sentKeysByTask[$sentTaskId] = New-Object System.Collections.Generic.List[string]
                }
                [void]$sentKeysByTask[$sentTaskId].Add([string]$sentKey)
            }
            $sentReplyByKey = @{}
            $sentClosureByKey = @{}
            $sentReceivedByKey = @{}
            foreach ($event in $allEvents) {
                $eventTaskId = [string]$event.task_id
                if (-not $eventTaskId -or -not $sentKeysByTask.ContainsKey($eventTaskId)) {
                    continue
                }
                foreach ($sentKey in @($sentKeysByTask[$eventTaskId])) {
                    $reqInfoForKey = $sentLatestByTask[$sentKey]
                    $requestForKey = $reqInfoForKey.event
                    $targetForKey = [string]$reqInfoForKey.target
                    if ([string]$event.ts_utc -le [string]$requestForKey.ts_utc) {
                        continue
                    }
                    if ([string]$event.agent -eq $targetForKey -and (Test-BridgeAnswerEvent -Event $event)) {
                        $sentReplyByKey[$sentKey] = $event
                        continue
                    }
                    if ([string]$event.agent -eq $Agent -and (Test-BridgeRequesterClosureEvent -Event $event)) {
                        $sentClosureByKey[$sentKey] = $event
                        continue
                    }
                    if ([string]$event.agent -eq $targetForKey -and
                        [string]$event.type -eq 'message' -and
                        [string]$event.status -eq 'received') {
                        $sentReceivedByKey[$sentKey] = $event
                    }
                }
            }
            $sentHiddenResolvedCount = 0
            foreach ($taskId in ($sentLatestByTask.Keys | Sort-Object)) {
                $reqInfo = $sentLatestByTask[$taskId]
                $req = $reqInfo.event
                $target = [string]$reqInfo.target
                $reply = @()
                if ($sentReplyByKey.ContainsKey($taskId)) {
                    $reply = @($sentReplyByKey[$taskId])
                }
                if ($reply.Count -gt 0) {
                    $last = $reply[-1]
                    if ($displayTaskIds.ContainsKey([string]$req.task_id)) {
                        Write-Host ("  answered-by-{0} {1}: request {2}/{3} -> {4}/{5}" -f `
                            $target, $req.task_id, $req.type, $req.status, $last.type, $last.status)
                    } else {
                        $sentHiddenResolvedCount++
                    }
                } else {
                    $closure = @()
                    if ($sentClosureByKey.ContainsKey($taskId)) {
                        $closure = @($sentClosureByKey[$taskId])
                    }
                    if ($closure.Count -gt 0) {
                        $last = $closure[-1]
                        if ($displayTaskIds.ContainsKey([string]$req.task_id)) {
                            Write-Host ("  closed-by-{0} {1}: request {2}/{3} -> {4}/{5}" -f `
                                $Agent, $req.task_id, $req.type, $req.status, $last.type, $last.status)
                        } else {
                            $sentHiddenResolvedCount++
                        }
                        continue
                    }
                    $received = @()
                    if ($sentReceivedByKey.ContainsKey($taskId)) {
                        $received = @($sentReceivedByKey[$taskId])
                    }
                    if ($received.Count -gt 0) {
                        Write-Host ("  RECEIVED-BY-{0} {1}: waiting for answer to {2}/{3}: {4}" -f `
                            $target, $req.task_id, $req.type, $req.status, $req.message) `
                            -ForegroundColor Yellow
                    } else {
                        Write-Host ("  WAITING-FOR-{0} {1}: {2}/{3}: {4}" -f `
                            $target, $req.task_id, $req.type, $req.status, $req.message) `
                            -ForegroundColor Yellow
                    }
                }
            }
            if ($sentHiddenResolvedCount -gt 0) {
                Write-Host ("  ({0} answered/closed item(s) outside -Tail hidden)" -f $sentHiddenResolvedCount)
            }
        }
        Write-Host ''
    }
}

if ($ShowLiveness -and -not $NoContinuity) {
    Write-Host 'AGENT LIVENESS' -ForegroundColor Cyan
    if (Test-Path -LiteralPath $eventsPath) {
        $latest = @{}
        $allLivenessEvents = New-Object System.Collections.Generic.List[object]
        $wakeRequests = New-Object System.Collections.Generic.List[object]
        $opens = New-Object System.Collections.Generic.List[object]
        $allLines = Get-Content -Path $eventsPath -Encoding UTF8
        foreach ($line in $allLines) {
            if (-not $line) { continue }
            try {
                $e = $line | ConvertFrom-Json
            } catch { continue }
            [void]$allLivenessEvents.Add($e)
            if ($e.type -eq 'liveness' -or $e.type -eq 'heartbeat') {
                $key = "{0}/{1}" -f $e.agent, $e.type
                $latest[$key] = $e
            }
            if ($e.type -eq 'wake_request' -and $e.status -ne 'closed') {
                $wakeRequests.Add($e) | Out-Null
            }
        }
        $activityTypes = @(
            'liveness','heartbeat','message','done','finding','test',
            'decision','handoff','blocked','claim','release'
        )
        foreach ($wake in $wakeRequests) {
            $target = [string]$wake.to
            $wakeTs = [string]$wake.ts_utc
            $closed = @(
                $allLivenessEvents |
                    Where-Object {
                        [string]$_.task_id -eq [string]$wake.task_id -and
                        [string]$_.type -eq 'wake_request' -and
                        [string]$_.status -eq 'closed' -and
                        [string]$_.ts_utc -gt $wakeTs
                    } |
                    Select-Object -First 1
            )
            $targetActivity = @()
            if ($target) {
                $targetActivity = @(
                    $allLivenessEvents |
                        Where-Object {
                            [string]$_.agent -eq $target -and
                            [string]$_.ts_utc -gt $wakeTs -and
                            $activityTypes -contains [string]$_.type
                        } |
                        Select-Object -First 1
                )
            }
            if ($closed.Count -eq 0 -and $targetActivity.Count -eq 0) {
                $opens.Add($wake) | Out-Null
            }
        }
        $now = (Get-Date).ToUniversalTime()
        # Internal review fix A5 (2026-05-09): hardcoded @('claude','codex')
        # meant any new bridge participant (gpt, operator, system, future
        # peers) was invisible in the liveness section even when they were
        # actively heart-beating. Derive the list from observed
        # liveness/heartbeat keys; fall back to claude+codex when empty so
        # an empty bridge still prints the canonical pair.
        $observedAgents = @(
            $latest.Keys |
                ForEach-Object { ($_ -split '/')[0] } |
                Where-Object { $_ } |
                Sort-Object -Unique
        )
        if ($observedAgents.Count -eq 0) {
            $observedAgents = @('claude','codex')
        }
        foreach ($agent in $observedAgents) {
            foreach ($k in @('liveness','heartbeat')) {
                $key = "{0}/{1}" -f $agent, $k
                if ($latest.ContainsKey($key)) {
                    $e = $latest[$key]
                    $ts = Get-BridgeEventTimestampSafe -TsUtc $e.ts_utc
                    if ($null -eq $ts) {
                        Write-Host ("  {0,-7} {1,-10} malformed timestamp: {2}/{3}" -f `
                            $agent, $k, $e.type, $e.status) -ForegroundColor Yellow
                    } else {
                        $age = ($now - $ts).TotalSeconds
                        $stale = if ($age -gt 300) { ' (STALE)' } else { '' }
                        Write-Host ("  {0,-7} {1,-10} {2}s ago{3}: {4}/{5}" -f `
                            $agent, $k, [int]$age, $stale, $e.type, $e.status)
                    }
                } else {
                    Write-Host ("  {0,-7} {1,-10} (no event)" -f $agent, $k)
                }
            }
        }
        Write-Host ''
        Write-Host 'OPEN WAKE_REQUESTS' -ForegroundColor Cyan
        if ($opens.Count -eq 0) {
            Write-Host '  (none)'
        } else {
            foreach ($e in $opens) {
                Write-Host ("  {0} [{1} -> {2}] sev={3}: {4}" -f `
                    $e.ts_utc, $e.agent, $e.to, $e.severity, $e.message)
            }
        }
        Write-Host ''
    } else {
        Write-Host '  (no events.jsonl yet)'
        Write-Host ''
    }
}

if ($ShowScoreboard) {
    # Internal review additive (2026-05-09): real-time attribution view —
    # who has done what, and how much, since the bridge started. Counts
    # the durable bridge-event types per agent so both Claude and Codex
    # (and the operator) can see at a glance how the iteration loop has
    # shared the load. No filtering by date here; for a windowed view
    # add a later -SinceUtc parameter.
    Write-Host 'AGENT SCOREBOARD' -ForegroundColor Cyan
    if (-not (Test-Path -LiteralPath $eventsPath)) {
        Write-Host '  (no events.jsonl yet)'
        Write-Host ''
    } else {
        $allScoreEvents = Read-BridgeEventObjects -Path $eventsPath -MaxLines 0
        $byAgent = @{}
        foreach ($e in $allScoreEvents) {
            $a = [string]$e.agent
            if (-not $a) { continue }
            if (-not $byAgent.ContainsKey($a)) {
                $byAgent[$a] = [ordered]@{
                    findings_raised  = 0
                    fixes_done       = 0
                    decisions        = 0
                    handoffs         = 0
                    claims           = 0
                    releases         = 0
                    messages_sent    = 0
                    blocked_raised   = 0
                    last_activity    = ''
                    last_message     = ''
                    last_task_id     = ''
                }
            }
            $row = $byAgent[$a]
            switch ([string]$e.type) {
                'finding'  { $row.findings_raised++ }
                'done'     { $row.fixes_done++ }
                'decision' { $row.decisions++ }
                'handoff'  { $row.handoffs++ }
                'claim'    { $row.claims++ }
                'release'  { $row.releases++ }
                'blocked'  { $row.blocked_raised++ }
                'message'  {
                    if ([string]$e.status -ne 'received') { $row.messages_sent++ }
                }
                default    { }
            }
            $tsRaw = [string]$e.ts_utc
            if ($tsRaw -and $tsRaw -gt [string]$row.last_activity) {
                $row.last_activity = $tsRaw
                $row.last_message  = [string]$e.message
                $row.last_task_id  = [string]$e.task_id
            }
        }
        if ($byAgent.Keys.Count -eq 0) {
            Write-Host '  (no agents)'
        } else {
            Write-Host ('  {0,-9} {1,7} {2,5} {3,5} {4,5} {5,5} {6,5} {7,5} {8,5}  last' -f `
                'agent','findings','fixes','decis','hand','claim','rel','msgs','blk')
            foreach ($a in ($byAgent.Keys | Sort-Object)) {
                $row = $byAgent[$a]
                $lastTask = $row.last_task_id
                if (-not $lastTask) { $lastTask = '-' }
                $lastMsg = $row.last_message
                if (-not $lastMsg) { $lastMsg = '-' }
                if ($lastMsg.Length -gt 60) { $lastMsg = $lastMsg.Substring(0,57) + '...' }
                Write-Host ('  {0,-9} {1,7} {2,5} {3,5} {4,5} {5,5} {6,5} {7,5} {8,5}  [{9}] {10}' -f `
                    $a, $row.findings_raised, $row.fixes_done, $row.decisions,
                    $row.handoffs, $row.claims, $row.releases, $row.messages_sent,
                    $row.blocked_raised, $lastTask, $lastMsg)
            }
            # Recent done/finding events — what got fixed, by whom.
            Write-Host ''
            Write-Host '  RECENT FIXES (done + finding/closed)' -ForegroundColor DarkCyan
            $recentFixes = @(
                $allScoreEvents |
                    Where-Object {
                        ([string]$_.type -eq 'done') -or
                        ([string]$_.type -eq 'finding' -and [string]$_.status -eq 'closed')
                    } |
                    Sort-Object ts_utc |
                    Select-Object -Last 12
            )
            if ($recentFixes.Count -eq 0) {
                Write-Host '    (none yet)'
            } else {
                foreach ($f in $recentFixes) {
                    $msg = [string]$f.message
                    if ($msg.Length -gt 70) { $msg = $msg.Substring(0,67) + '...' }
                    Write-Host ('    {0}  {1,-7} {2}/{3} [{4}] {5}' -f `
                        ([string]$f.ts_utc), [string]$f.agent, [string]$f.type,
                        [string]$f.status, [string]$f.task_id, $msg)
                }
            }
        }
        Write-Host ''
    }
}

Write-Host 'RECENT EVENTS' -ForegroundColor Cyan
if (-not (Test-Path -LiteralPath $eventsPath)) {
    Write-Host '  (none)'
    exit 0
}

$lines = @(Get-Content -Path $eventsPath -Tail $Tail -Encoding UTF8)
$events = New-Object System.Collections.Generic.List[object]
foreach ($line in $lines) {
    if (-not $line) { continue }
    try {
        $e = $line | ConvertFrom-Json
        if ($OtherOnly -and $Agent -and [string]$e.agent -eq $Agent) { continue }
        [void]$events.Add($e)
    } catch {}
}

if ($Raw) {
    $events | ConvertTo-Json -Depth 12
    exit 0
}

foreach ($e in $events) {
    $scope = ''
    if ($e.PSObject.Properties['write_scope'] -and @($e.write_scope).Count -gt 0) {
        $scope = ' scope=' + ((@($e.write_scope)) -join ',')
    }
    $role = ''
    if ($e.PSObject.Properties['role'] -and [string]$e.role) {
        $role = ' role=' + [string]$e.role
    }
    $uuid = ''
    if ($e.PSObject.Properties['agent_uuid'] -and [string]$e.agent_uuid) {
        $uuid = ' uuid=' + [string]$e.agent_uuid
    }
    $target = ''
    if ($e.PSObject.Properties['to'] -and [string]$e.to) {
        $target = ' -> ' + [string]$e.to
    }
    Write-Host ("{0} [{1}{2}] {3}/{4}: {5}{6}{7}{8}" -f $e.ts_utc, $e.agent, $target, $e.type, $e.status, $e.message, $scope, $role, $uuid)
}
