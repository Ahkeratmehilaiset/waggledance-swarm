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

function Test-BridgeAckEvent {
    param([Parameter(Mandatory)] [object] $Event)
    Set-StrictMode -Version Latest

    return @('received','seen','acknowledged') -contains [string]$Event.status
}

function Test-BridgeInfrastructureEvent {
    param([Parameter(Mandatory)] [object] $Event)
    Set-StrictMode -Version Latest

    # Pure background noise only. `wake_request` is NOT infrastructure: it is a
    # directed, actionable nudge (operator/peer "read the bridge / review this"),
    # so it must reach the request-like classifier instead of being dropped here.
    # It is kept out of the answer/closure path separately (see Test-BridgeAnswerEvent),
    # matching the Python REQUEST_TYPES parity merged in #1101.
    return @('heartbeat','liveness') -contains [string]$Event.type
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

    $status = [string]$Event.status
    $type = [string]$Event.type
    if ($type -eq 'message') {
        return @('closed','superseded','cancelled','canceled') -contains $status -or
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
    $status = [string]$Event.status

    if ($type -eq 'message' -and (Test-BridgeMessageAnswerStatus -Status $status)) {
        return $false
    }
    if (Test-BridgeRequesterClosureEvent -Event $Event) { return $false }
    if ($Event.PSObject.Properties['request_id'] -and $Event.request_id) {
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
    $status = [string]$Event.status

    if ($type -eq 'message') {
        if (Test-BridgeMessageAnswerStatus -Status $status) { return $true }
        if (Test-BridgeRequestLikeEvent -Event $Event) { return $false }
        return $true
    }

    # `wake_request` is request-like, never a closure/answer: a nudge must not
    # mark another agent's open request as answered.
    if (@('status','intent','wake_request') -contains $type) { return $false }
    return $true
}

function Test-BridgeWakeEligible {
    param([Parameter(Mandatory)] [object] $Event)
    Set-StrictMode -Version Latest
    # Never suppress late answers/corrections merely because work was reported
    # or a request was closed. Unknown addressed traffic remains actionable.
    $status=$Event.PSObject.Properties['status']
    $type=$Event.PSObject.Properties['type']
    if (($null -ne $status -and $status.Value -cin @('received','seen','acknowledged')) -or
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
        if ($null -ne $notification -and $notification.Value -ceq 'informational') { return $false }
    }
    return $true
}
