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
    if ($type -eq 'message') {
        return @('closed','superseded','cancelled','canceled') -contains $status -or
            $status.StartsWith('closed_', [System.StringComparison]::OrdinalIgnoreCase) -or
            $status.StartsWith('superseded_', [System.StringComparison]::OrdinalIgnoreCase) -or
            $status.StartsWith('cancelled_', [System.StringComparison]::OrdinalIgnoreCase) -or
            $status.StartsWith('canceled_', [System.StringComparison]::OrdinalIgnoreCase)
    }
    if (@('done','release','decision') -notcontains $type) { return $false }
    if (
        $type -eq 'done' -and
        (Test-BridgeNegatedAnswerStatus -Status $status)
    ) {
        return $false
    }
    return (Test-BridgeRequesterClosureStatus -Status $status)
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
    if ($type -eq 'done') { return $status -eq 'request' }

    $requestTypes = @('message','handoff','blocked','finding','decision','wake_request')
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

function Test-BridgeNegatedAnswerStatus {
    param([AllowEmptyString()] [string] $Status)

    $tokens = @(
        $Status.ToLowerInvariant() -split '[^a-z0-9]+' |
            Where-Object { $_ }
    )
    $answerTokens = @(
        'abandoned','accepted','ack','acknowledged','answered','approved',
        'block','blocked','canceled','cancelled','changes','closed',
        'completed','done','merged',
        'observed','pass','passed','received','reported','resolved',
        'retracted','seen','superseded','validated','verified','withdrawn'
    )
    for ($index = 0; $index -lt ($tokens.Count - 1); $index++) {
        if ($tokens[$index] -eq 'not' -and $answerTokens -contains $tokens[$index + 1]) {
            return $true
        }
    }
    return $false
}

function Test-BridgeAnswerEvent {
    param([Parameter(Mandatory)] [object] $Event)

    if (-not [string]$Event.task_id) { return $false }
    if (Test-BridgeAckEvent -Event $Event) { return $false }
    if (Test-BridgeInfrastructureEvent -Event $Event) { return $false }

    $type = [string]$Event.type
    $status = [string]$Event.status

    if (
        $type -eq 'done' -and (
            $status -eq 'request' -or
            (Test-BridgeNegatedAnswerStatus -Status $status)
        )
    ) {
        return $false
    }

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
