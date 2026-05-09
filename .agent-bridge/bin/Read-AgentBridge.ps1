#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('codex','claude','operator','system','')] [string] $Agent = '',
    [int] $Tail = 40,
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

function Read-BridgeEventObjects {
    # Internal review fix R1/A1 (2026-05-09): default 5000 was too low.
    # Heartbeat traffic at 60s * 2 agents is about 2880 events/day, so the
    # tail-truncation silently dropped continuity-section events older
    # than ~2 days. 50000 covers about a month; pass -MaxLines 0 for
    # unlimited (use sparingly).
    param([Parameter(Mandatory)] [string] $Path, [int] $MaxLines = 50000)
    $items = New-Object System.Collections.Generic.List[object]
    if (-not (Test-Path -LiteralPath $Path)) { return $items }
    $allLines = if ($MaxLines -le 0) {
        @(Get-Content -Path $Path -Encoding UTF8)
    } else {
        @(Get-Content -Path $Path -Tail $MaxLines -Encoding UTF8)
    }
    foreach ($line in $allLines) {
        if (-not $line) { continue }
        try {
            [void]$items.Add(($line | ConvertFrom-Json))
        } catch {}
    }
    return $items
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
    param([string] $TsUtc)
    if (-not $TsUtc) { return $null }
    try {
        return [DateTime]::Parse($TsUtc).ToUniversalTime()
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
        $allEvents = Read-BridgeEventObjects -Path $eventsPath
        $requestTypes = @('message','handoff','blocked','finding','decision','done')
        $requestStatuses = @(
            'request','ready','blocked','open','proposal',
            'fix-pushed','fix-branch-pushed','pushed',
            'ready_for_implementation'
        )
        $requests = @(
            $allEvents |
                Where-Object {
                    $_.PSObject.Properties['to'] -and
                    [string]$_.to -eq $Agent -and
                    [string]$_.agent -ne $Agent -and
                    $requestTypes -contains [string]$_.type -and
                    $requestStatuses -contains [string]$_.status -and
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
                $reply = @(
                    $allEvents |
                        Where-Object {
                            [string]$_.agent -eq $Agent -and
                            [string]$_.task_id -eq $taskId -and
                            [string]$_.ts_utc -gt [string]$req.ts_utc -and
                            (Test-IsAnswerEvent -Event $_)
                        } |
                        Sort-Object ts_utc |
                        Select-Object -Last 1
                )
                if ($reply.Count -gt 0) {
                    $last = $reply[-1]
                    Write-Host ("  answered {0}: request {1}/{2} -> {3}/{4}" -f `
                        $taskId, $req.type, $req.status, $last.type, $last.status)
                } else {
                    $closure = @(
                        $allEvents |
                            Where-Object {
                                [string]$_.agent -eq [string]$req.agent -and
                                [string]$_.task_id -eq $taskId -and
                                [string]$_.ts_utc -gt [string]$req.ts_utc -and
                                (Test-IsRequesterClosureEvent -Event $_)
                            } |
                            Sort-Object ts_utc |
                            Select-Object -Last 1
                    )
                    if ($closure.Count -gt 0) {
                        $last = $closure[-1]
                        Write-Host ("  closed-by-requester {0}: request {1}/{2} -> {3}/{4}" -f `
                            $taskId, $req.type, $req.status, $last.type, $last.status)
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
        }

        $sentRequests = @(
            $allEvents |
                Where-Object {
                    $_.PSObject.Properties['to'] -and
                    [string]$_.agent -eq $Agent -and
                    [string]$_.to -and
                    [string]$_.to -ne $Agent -and
                    $requestTypes -contains [string]$_.type -and
                    $requestStatuses -contains [string]$_.status -and
                    [string]$_.task_id
                } |
                Sort-Object ts_utc
        )
        if ($sentRequests.Count -eq 0) {
            Write-Host '  outgoing: (none)'
        } else {
            Write-Host '  outgoing:'
            $sentLatestByTask = @{}
            foreach ($r in $sentRequests) {
                $sentLatestByTask[[string]$r.task_id] = $r
            }
            foreach ($taskId in ($sentLatestByTask.Keys | Sort-Object)) {
                $req = $sentLatestByTask[$taskId]
                $reply = @(
                    $allEvents |
                        Where-Object {
                            [string]$_.agent -eq [string]$req.to -and
                            [string]$_.task_id -eq $taskId -and
                            [string]$_.ts_utc -gt [string]$req.ts_utc -and
                            (Test-IsAnswerEvent -Event $_)
                        } |
                        Sort-Object ts_utc |
                        Select-Object -Last 1
                )
                if ($reply.Count -gt 0) {
                    $last = $reply[-1]
                    Write-Host ("  answered-by-{0} {1}: request {2}/{3} -> {4}/{5}" -f `
                        $req.to, $taskId, $req.type, $req.status, $last.type, $last.status)
                } else {
                    $closure = @(
                        $allEvents |
                            Where-Object {
                                [string]$_.agent -eq $Agent -and
                                [string]$_.task_id -eq $taskId -and
                                [string]$_.ts_utc -gt [string]$req.ts_utc -and
                                (Test-IsRequesterClosureEvent -Event $_)
                            } |
                            Sort-Object ts_utc |
                            Select-Object -Last 1
                    )
                    if ($closure.Count -gt 0) {
                        $last = $closure[-1]
                        Write-Host ("  closed-by-{0} {1}: request {2}/{3} -> {4}/{5}" -f `
                            $Agent, $taskId, $req.type, $req.status, $last.type, $last.status)
                        continue
                    }
                    $received = @(
                        $allEvents |
                            Where-Object {
                                [string]$_.agent -eq [string]$req.to -and
                                [string]$_.task_id -eq $taskId -and
                                [string]$_.type -eq 'message' -and
                                [string]$_.status -eq 'received' -and
                                [string]$_.ts_utc -gt [string]$req.ts_utc
                            } |
                            Sort-Object ts_utc |
                            Select-Object -Last 1
                    )
                    if ($received.Count -gt 0) {
                        Write-Host ("  RECEIVED-BY-{0} {1}: waiting for answer to {2}/{3}: {4}" -f `
                            $req.to, $taskId, $req.type, $req.status, $req.message) `
                            -ForegroundColor Yellow
                    } else {
                        Write-Host ("  WAITING-FOR-{0} {1}: {2}/{3}: {4}" -f `
                            $req.to, $taskId, $req.type, $req.status, $req.message) `
                            -ForegroundColor Yellow
                    }
                }
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
                    $ts = Get-BridgeEventTimestampSafe -TsUtc ([string]$e.ts_utc)
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
    $target = ''
    if ($e.PSObject.Properties['to'] -and [string]$e.to) {
        $target = ' -> ' + [string]$e.to
    }
    Write-Host ("{0} [{1}{2}] {3}/{4}: {5}{6}" -f $e.ts_utc, $e.agent, $target, $e.type, $e.status, $e.message, $scope)
}
