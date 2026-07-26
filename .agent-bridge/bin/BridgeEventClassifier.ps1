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

Set-StrictMode -Version Latest

function Get-BridgeEventTargets {
    param([Parameter(Mandatory)] [object] $Event)

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

    return @(Get-BridgeEventTargets -Event $Event) -contains $TargetAgent
}

function Test-BridgeAckEvent {
    param([Parameter(Mandatory)] [object] $Event)

    return @('received','seen','acknowledged') -contains [string]$Event.status
}

function Test-BridgeInfrastructureEvent {
    param([Parameter(Mandatory)] [object] $Event)

    # Pure background noise only. `wake_request` is NOT infrastructure: it is a
    # directed, actionable nudge (operator/peer "read the bridge / review this"),
    # so it must reach the request-like classifier instead of being dropped here.
    # It is kept out of the answer/closure path separately (see Test-BridgeAnswerEvent),
    # matching the Python REQUEST_TYPES parity merged in #1101.
    return @('heartbeat','liveness') -contains [string]$Event.type
}

function Test-BridgeMessageAnswerStatus {
    param([AllowEmptyString()] [string] $Status)

    return @(
        'answered',
        'answered_plus_reminder',
        'answered_after_recovery'
    ) -contains $Status
}

function Test-BridgeRequesterClosureStatus {
    param([AllowEmptyString()] [string] $Status)

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

    $status = [string]$Event.status
    $type = [string]$Event.type
    # A requester can explicitly close a previously emitted wake without
    # turning the wake into an answer from the target. Keep this pairing exact:
    # broader terminal-looking wake statuses remain non-authoritative.
    if ($type -eq 'wake_request') {
        return $status -eq 'closed'
    }
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

function Test-BridgeRequesterClosureForRequest {
    param(
        [Parameter(Mandatory)] [object] $Request,
        [Parameter(Mandatory)] [object] $Event
    )

    if (-not (Test-BridgeRequesterClosureEvent -Event $Event)) {
        return $false
    }
    # wake_request/closed withdraws an earlier wake only. Other established
    # requester closeouts remain task-wide for backward compatibility.
    if ([string]$Event.type -eq 'wake_request') {
        return [string]$Request.type -eq 'wake_request'
    }
    return $true
}

function Test-BridgeRequestLikeEvent {
    param([Parameter(Mandatory)] [object] $Event)

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

    $requestTypes = @('message','handoff','blocked','finding','decision','done','wake_request')
    $requestStatuses = @(
        'request','ready','blocked','open','proposal',
        'fix-pushed','fix-branch-pushed','pushed',
        'ready_for_implementation',
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

    # `wake_request` is never a target answer. Exact wake_request/closed is
    # handled separately as a requester-authored closure.
    if (@('status','intent','wake_request') -contains $type) { return $false }
    return $true
}
