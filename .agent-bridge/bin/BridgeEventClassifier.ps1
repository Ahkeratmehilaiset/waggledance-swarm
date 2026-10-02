#requires -Version 5.1
<#
.SYNOPSIS
    Shared continuity classifiers for bridge readers.

.DESCRIPTION
    Bridge event type/status values are intentionally loose enough for
    agents to introduce richer domain events. Readers must therefore treat
    unknown, addressed event types as substantive instead of silently
    dropping them from polling state. The only events that are never
    substantive replies are ACKs and infrastructure liveness traffic.
#>



function Get-BridgeEventTargets {
    param([Parameter(Mandatory)] [object] $Event)
    Set-StrictMode -Version Latest

    if (-not $Event.PSObject.Properties['to']) { return @() }
    $to = [string]$Event.to
    if (-not $to) { return @() }

    return @(
        ($to -split ',') |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ } |
            Sort-Object -Unique
    )
}

function Test-BridgeAddressedTo {
    param(
        [Parameter(Mandatory)] [object] $Event,
        [Parameter(Mandatory)] [string] $TargetAgent
    )
    Set-StrictMode -Version Latest

    return @(Get-BridgeEventTargets -Event $Event) -contains $TargetAgent
}

function Get-BridgeEventStatusText {
    param([Parameter(Mandatory)] [object] $Event)
    Set-StrictMode -Version Latest

    # A row without a top-level status (StrictMode would throw on $Event.status and deny ALL routing) or with a
    # non-string status (list, bool, object, number) reads as an empty status: it never becomes a request,
    # answer, ACK or closure status by [string] coercion (Fable 48631d99). Exact strings are unchanged.
    $property = $Event.PSObject.Properties['status']
    if ($null -eq $property -or $property.Value -isnot [string]) { return '' }
    return [string]$property.Value
}

function Test-BridgeAckEvent {
    param([Parameter(Mandatory)] [object] $Event)
    Set-StrictMode -Version Latest

    return @('received','seen','acknowledged') -contains (Get-BridgeEventStatusText -Event $Event)
}

function Test-BridgeInfrastructureEvent {
    param([Parameter(Mandatory)] [object] $Event)
    Set-StrictMode -Version Latest

    # Pure background noise only. `wake_request` is NOT infrastructure: it is a
    # directed, actionable nudge (operator/peer "read the bridge / review this"),
    # so it must reach the request-like classifier instead of being dropped here.
    # It is kept out of the answer/closure path separately (see Test-BridgeAnswerEvent),
    # matching the Python REQUEST_TYPES parity merged in #1101.
    return @('heartbeat','liveness','consumer_tick') -contains [string]$Event.type
}

function Test-BridgeMessageAnswerStatus {
    param([AllowEmptyString()] [string] $Status)
    Set-StrictMode -Version Latest

    return @(
        'answered',
        'answered_plus_reminder',
        'answered_after_recovery'
    ) -contains $Status
}

function Test-BridgeInterimMessageStatus {
    param([AllowEmptyString()] [string] $Status)
    Set-StrictMode -Version Latest

    # Queue admission and progress receipts are not task completion. Keep
    # unknown custom result statuses eligible for exact-bound closure.
    return @(
        'queued','queued_for_processing','queue_accepted',
        'accepted','accepted_for_processing','pending','started',
        'in_progress','processing','running','request','requested',
        'open','proposal','waiting_for_result'
    ) -contains $Status
}

function Test-BridgeRequesterClosureStatus {
    param([AllowEmptyString()] [string] $Status)
    Set-StrictMode -Version Latest

    if (@(
        'done','closed','superseded','merged','abandoned',
        'completed','approved','cancelled','canceled'
    ) -contains $Status) {
        return $true
    }

    foreach ($prefix in @(
        'done_','closed_','superseded_','merged_','abandoned_',
        'completed_','approved_','cancelled_','canceled_'
    )) {
        if ($Status.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }

    return $false
}

function Test-BridgeRequesterClosureEvent {
    param([Parameter(Mandatory)] [object] $Event)
    Set-StrictMode -Version Latest

    $status = Get-BridgeEventStatusText -Event $Event
    $type = [string]$Event.type
    if ($type -in @('message','wake_request')) {
        return @('closed','superseded','cancelled','canceled','withdrawn') -contains $status -or
            $status.StartsWith('withdrawn_', [System.StringComparison]::OrdinalIgnoreCase) -or
            $status.StartsWith('closed_', [System.StringComparison]::OrdinalIgnoreCase) -or
            $status.StartsWith('superseded_', [System.StringComparison]::OrdinalIgnoreCase) -or
            $status.StartsWith('cancelled_', [System.StringComparison]::OrdinalIgnoreCase) -or
            $status.StartsWith('canceled_', [System.StringComparison]::OrdinalIgnoreCase)
    }
    if (@('done','release','decision') -notcontains $type) { return $false }
    return (Test-BridgeRequesterClosureStatus -Status $status)
}

function Test-BridgeRequestLikeEvent {
    param([Parameter(Mandatory)] [object] $Event)
    Set-StrictMode -Version Latest

    if (-not [string]$Event.task_id) { return $false }
    if (@(Get-BridgeEventTargets -Event $Event).Count -eq 0) { return $false }
    if (Test-BridgeAckEvent -Event $Event) { return $false }
    if (Test-BridgeInfrastructureEvent -Event $Event) { return $false }

    $type = [string]$Event.type
    $status = Get-BridgeEventStatusText -Event $Event

    if ($type -eq 'message' -and (Test-BridgeMessageAnswerStatus -Status $status)) {
        return $false
    }
    if (Test-BridgeRequesterClosureEvent -Event $Event) { return $false }
    # Exact presence, not truthiness: a present non-null request_id other than "" is an explicit (possibly invalid)
    # id, so false/0/0.0/[] stay visible like {} or [7]. Visibility is not binding: reply binding still refuses any
    # non-string id. null, "" and an absent id keep the legacy status rule below.
    $ridProperty = $Event.PSObject.Properties['request_id']
    if ($null -ne $ridProperty -and $null -ne $ridProperty.Value -and
        -not ($ridProperty.Value -is [string] -and $ridProperty.Value.Length -eq 0)) {
        if (Test-BridgeRequesterClosureStatus $status) { return $false }
        return $true
    }

    # A negative review remains a substantive reply / gate signal, not an
    # implicit new assignment. An explicit new request_id above takes priority.
    if ($Event.PSObject.Properties['in_reply_to_request_id'] -and
        $null -ne $Event.in_reply_to_request_id) { return $false }
    if ($Event.PSObject.Properties['payload'] -and $null -ne $Event.payload) {
        $payload = $Event.payload
        if ($payload.PSObject.Properties['in_reply_to_request_id'] -and
            $null -ne $payload.in_reply_to_request_id) { return $false }
    }

    $requestTypes = @('message','handoff','blocked','finding','decision','done','wake_request')
    $requestStatuses = @(
        'request','ready','blocked','open','proposal',
        'fix-pushed','fix-branch-pushed','pushed',
        'ready_for_implementation','handoff_ready',
        'rco_requested','review_requested','changes_requested'
    )

    if ($requestTypes -contains $type -and $requestStatuses -contains $status) {
        return $true
    }

    # Custom domain events are request-like when they are explicitly
    # addressed and use an open/proposal status. Example seen live:
    # ownership_proposal/open.
    if ($status -in @('request','open','proposal','ready','blocked')) {
        return $true
    }
    if ($status -like '*proposal*') { return $true }

    return $false
}

function Test-BridgeAnswerEvent {
    param([Parameter(Mandatory)] [object] $Event)
    Set-StrictMode -Version Latest

    if (-not [string]$Event.task_id) { return $false }
    if (Test-BridgeAckEvent -Event $Event) { return $false }
    if (Test-BridgeInfrastructureEvent -Event $Event) { return $false }

    $type = [string]$Event.type
    $status = Get-BridgeEventStatusText -Event $Event

    if ($type -eq 'done' -and $status -match '(^|[^a-z0-9])(not|no|undone|incomplete|unfinished|unresolved|unverified|unmerged|failed|pending|queued|running|processing)([^a-z0-9]|$)') { return $false }
    if ($type -eq 'message') {
        if (Test-BridgeInterimMessageStatus -Status $status) { return $false }
        if (Test-BridgeMessageAnswerStatus -Status $status) { return $true }
        if (Test-BridgeRequestLikeEvent -Event $Event) { return $false }
        return $true
    }

    # `wake_request` is request-like, never a closure/answer: a nudge must not
    # mark another agent's open request as answered.
    if (@('status','intent','wake_request','triage_disposition') -contains $type) { return $false }
    return $true
}

function Test-BridgeWakeEligible {
    param([Parameter(Mandatory)] [object] $Event)
    Set-StrictMode -Version Latest
    # Never suppress late answers/corrections merely because work was reported
    # or a request was closed. Unknown addressed traffic remains actionable.
    $status=$Event.PSObject.Properties['status']
    $type=$Event.PSObject.Properties['type']
    if (($null -ne $status -and $status.Value -is [string] -and $status.Value -cin @('received','seen','acknowledged')) -or
        ($null -ne $type -and $type.Value -cin @('heartbeat','liveness'))) { return $false }
    foreach ($key in @('in_reply_to_request_id','request_id')) {
        $p=$Event.PSObject.Properties[$key]
        if ($null -ne $p -and $p.Value) { return $true }
    }
    if ($null -ne $status -and $null -ne $type -and $null -ne $Event.PSObject.Properties['task_id'] -and
        (Test-BridgeRequestLikeEvent $Event)) { return $true }
    $payload=$Event.PSObject.Properties['payload']
    if ($null -ne $payload -and $null -ne $payload.Value) {
        $notification=$payload.Value.PSObject.Properties['notification']
        # A hint cannot silence outcomes or unfamiliar control traffic. In the
        # 2026-09-28 incident full_suite_result was accidentally marked FYI and
        # left waiting lanes asleep. Only this closed benign envelope is quiet.
        # Wake eligibility is NOT reply binding, acceptance or task completion.
        if ($null -ne $notification -and $notification.Value -ceq 'informational' -and
            $null -ne $type -and $type.Value -ceq 'message' -and
            $null -ne $status -and $status.Value -is [string] -and $status.Value -cin @('notice','informational')) { return $false }
    }
    return $true
}
