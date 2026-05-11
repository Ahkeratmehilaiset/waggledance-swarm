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

    return @('heartbeat','liveness','wake_request') -contains [string]$Event.type
}

function Test-BridgeMessageAnswerStatus {
    param([Parameter(Mandatory)] [string] $Status)

    return @(
        'answered',
        'answered_plus_reminder',
        'answered_after_recovery'
    ) -contains $Status
}

function Test-BridgeRequesterClosureEvent {
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

    $requestTypes = @('message','handoff','blocked','finding','decision','done')
    $requestStatuses = @(
        'request','ready','blocked','open','proposal',
        'fix-pushed','fix-branch-pushed','pushed',
        'ready_for_implementation'
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

    if (@('status','intent') -contains $type) { return $false }
    return $true
}
