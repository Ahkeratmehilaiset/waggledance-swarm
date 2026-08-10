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

    $status = ([string]$Event.status).ToLowerInvariant()
    $tokens = @(
        ($status -split '[^a-z0-9]+') |
            Where-Object { $_ }
    )
    $ackTokens = @('ack','acknowledged','received','seen')
    return @($tokens | Where-Object { $ackTokens -contains $_ }).Count -gt 0
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
    param(
        [AllowEmptyString()] [string] $Status,
        [switch] $Message
    )

    $normalized = $Status.ToLowerInvariant()
    $tokens = @(
        ($normalized -split '[^a-z0-9]+') |
            Where-Object { $_ }
    )
    $nonterminalTokens = @(
        'ack','acknowledged','cannot','failed','failure','incomplete',
        'missing','needed','never','not','notyet','open','pending',
        'progress','received','request','requested','required','seen',
        'unresolved','working'
    )
    $stems = if ($Message) {
        @('closed','superseded','cancelled','canceled')
    } else {
        @(
            'done','closed','superseded','merged','abandoned',
            'completed','approved','cancelled','canceled'
        )
    }
    $explicitStatuses = @(
        'changes_requested_retracted','changes_requested_resolved',
        'changes_requested_withdrawn','finding_retracted',
        'finding_withdrawn','rco_closed_postmerge',
        'rco_finding_retracted','rco_finding_withdrawn'
    )
    if (-not $Message -and $explicitStatuses -contains $normalized) {
        return $true
    }

    if (@($tokens | Where-Object { $nonterminalTokens -contains $_ }).Count -gt 0) {
        return $false
    }

    if ($stems -contains $normalized) {
        return $true
    }

    foreach ($stem in $stems) {
        if ($normalized.StartsWith("${stem}_", [System.StringComparison]::Ordinal)) {
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
        return (Test-BridgeRequesterClosureStatus -Status $status -Message)
    }
    if (@('status','done','release','decision') -notcontains $type) { return $false }
    return (Test-BridgeRequesterClosureStatus -Status $status)
}

function Get-BridgeOptionalIdentityValue {
    param(
        [Parameter(Mandatory)] [object] $Event,
        [Parameter(Mandatory)] [string] $Name
    )

    if (-not $Event.PSObject.Properties[$Name]) { return '' }
    return ([string]$Event.$Name).Trim()
}

function Test-BridgeRequesterIdentityMatch {
    param(
        [Parameter(Mandatory)] [object] $Request,
        [Parameter(Mandatory)] [object] $Closure
    )

    if (-not [string]::Equals(
        [string]$Request.agent,
        [string]$Closure.agent,
        [System.StringComparison]::OrdinalIgnoreCase
    )) { return $false }

    $requestUuid = Get-BridgeOptionalIdentityValue -Event $Request -Name 'agent_uuid'
    if ($requestUuid) {
        $closureUuid = Get-BridgeOptionalIdentityValue -Event $Closure -Name 'agent_uuid'
        if (-not $closureUuid -or -not [string]::Equals(
            $requestUuid,
            $closureUuid,
            [System.StringComparison]::OrdinalIgnoreCase
        )) { return $false }
    }

    $requestSession = Get-BridgeOptionalIdentityValue -Event $Request -Name 'session_id'
    if ($requestSession) {
        $closureSession = Get-BridgeOptionalIdentityValue -Event $Closure -Name 'session_id'
        if (-not $closureSession -or -not [string]::Equals(
            $requestSession,
            $closureSession,
            [System.StringComparison]::Ordinal
        )) { return $false }
    }

    return $true
}

function ConvertTo-BridgeEventUtcDateTime {
    param([AllowNull()] [object] $Value)

    if ($null -eq $Value) { return $null }
    if ($Value -is [System.DateTime]) {
        return ([System.DateTime]$Value).ToUniversalTime()
    }
    if ($Value -is [System.DateTimeOffset]) {
        return ([System.DateTimeOffset]$Value).UtcDateTime
    }
    $text = [string]$Value
    if ([string]::IsNullOrWhiteSpace($text)) { return $null }
    $styles = (
        [System.Globalization.DateTimeStyles]::AssumeUniversal -bor
        [System.Globalization.DateTimeStyles]::AdjustToUniversal
    )
    try {
        return ([System.DateTimeOffset]::Parse(
            $text,
            [System.Globalization.CultureInfo]::InvariantCulture,
            $styles
        )).UtcDateTime
    } catch {
        return $null
    }
}

function Get-BridgeEventTimestampValue {
    param([Parameter(Mandatory)] [object] $Event)

    if (-not $Event.PSObject.Properties['ts_utc']) { return $null }
    return $Event.ts_utc
}

function Get-BridgeEventAppendIndex {
    param([Parameter(Mandatory)] [object] $Event)

    foreach ($name in @('_bridge_append_index', '_line_no')) {
        $property = $Event.PSObject.Properties[$name]
        if ($null -eq $property) { continue }
        $parsed = 0L
        if (
            [long]::TryParse(
                [string]$property.Value,
                [ref]$parsed
            ) -and
            $parsed -ge 0
        ) {
            return $parsed
        }
    }
    return $null
}

function Get-BridgeEventUtcSortKey {
    param([Parameter(Mandatory)] [object] $Event)

    $parsed = ConvertTo-BridgeEventUtcDateTime -Value (
        Get-BridgeEventTimestampValue -Event $Event
    )
    $ticks = if ($null -eq $parsed) {
        [System.DateTime]::MaxValue.Ticks
    } else {
        $parsed.Ticks
    }
    $appendIndex = Get-BridgeEventAppendIndex -Event $Event
    if ($null -eq $appendIndex) { $appendIndex = [long]::MaxValue }
    return ('{0:D19}:{1:D19}' -f $ticks, $appendIndex)
}

function Test-BridgeEventAfter {
    param(
        [Parameter(Mandatory)] [object] $Event,
        [Parameter(Mandatory)] [object] $Reference
    )

    $eventTs = ConvertTo-BridgeEventUtcDateTime -Value (
        Get-BridgeEventTimestampValue -Event $Event
    )
    $referenceTs = ConvertTo-BridgeEventUtcDateTime -Value (
        Get-BridgeEventTimestampValue -Event $Reference
    )
    if ($null -eq $eventTs -or $null -eq $referenceTs) { return $false }
    if ($eventTs -gt $referenceTs) { return $true }
    if ($eventTs -lt $referenceTs) { return $false }
    $eventIndex = Get-BridgeEventAppendIndex -Event $Event
    $referenceIndex = Get-BridgeEventAppendIndex -Event $Reference
    return $null -ne $eventIndex -and
        $null -ne $referenceIndex -and
        $eventIndex -gt $referenceIndex
}

function Test-BridgeRequestLikeEvent {
    param([Parameter(Mandatory)] [object] $Event)

    if (@(Get-BridgeEventTargets -Event $Event).Count -eq 0) { return $false }
    if (Test-BridgeAckEvent -Event $Event) { return $false }
    if (Test-BridgeInfrastructureEvent -Event $Event) { return $false }

    $type = ([string]$Event.type).ToLowerInvariant()
    $status = ([string]$Event.status).ToLowerInvariant()

    if ($type -eq 'message' -and (Test-BridgeMessageAnswerStatus -Status $status)) {
        return $false
    }
    if (Test-BridgeRequesterClosureEvent -Event $Event) { return $false }

    $tokens = @(
        ($status -split '[^a-z0-9]+') |
            Where-Object { $_ }
    )
    $openTokens = @(
        'open','proposal','request','requested','required','needed',
        'missing','ready','pushed','active','blocked'
    )
    if (@($tokens | Where-Object { $openTokens -contains $_ }).Count -eq 0) {
        return $false
    }

    $responseTokens = @(
        'accepted','ack','acknowledged','answered','approved','closed','done',
        'merged','observed','pass','received','reported','resolved','seen',
        'superseded','validated','verified'
    )
    if (
        ($tokens -contains 'not') -or
        @($tokens | Where-Object { @('required','needed','missing') -contains $_ }).Count -gt 0
    ) {
        $responseOnly = $false
    } elseif (
        @($tokens | Where-Object { @('request','requested') -contains $_ }).Count -gt 0 -and
        @($tokens | Where-Object {
            $_ -ne 'pass' -and $responseTokens -contains $_
        }).Count -eq 0
    ) {
        $responseOnly = $false
    } else {
        $responseOnly = @(
            $tokens | Where-Object { $responseTokens -contains $_ }
        ).Count -gt 0
    }
    if ($responseOnly) { return $false }

    $requestTypes = @(
        'message','done','finding','handoff','wake_request',
        'peer_review_request','simulation_open','sandbox_drop','decision'
    )
    if ($requestTypes -contains $type) {
        return $true
    }

    # Python parity: custom directed open events require a task binding, while
    # standard request types retain the historical taskless routing contract.
    return -not [string]::IsNullOrWhiteSpace([string]$Event.task_id)
}

function Test-BridgeAnswerEvent {
    param([Parameter(Mandatory)] [object] $Event)

    if (-not [string]$Event.task_id) { return $false }
    if (Test-BridgeAckEvent -Event $Event) { return $false }
    if (Test-BridgeInfrastructureEvent -Event $Event) { return $false }

    $type = ([string]$Event.type).ToLowerInvariant()
    $status = ([string]$Event.status).ToLowerInvariant()

    $requestLike = Test-BridgeRequestLikeEvent -Event $Event
    $standardRequestTypes = @(
        'blocked','claim','decision','done','finding','handoff','heartbeat',
        'intent','liveness','message','release','status','test','wake_request',
        'peer_review_request','simulation_open','sandbox_drop'
    )
    if ($requestLike -and $standardRequestTypes -contains $type) {
        return $false
    }

    if ($type -eq 'message') {
        if (Test-BridgeMessageAnswerStatus -Status $status) { return $true }
        return $true
    }

    # `wake_request` is request-like, never a closure/answer: a nudge must not
    # mark another agent's open request as answered.
    if (@('status','intent','wake_request') -contains $type) { return $false }
    if ($requestLike) {
        # ADR 020: a custom directed open event can answer prior work while
        # opening reciprocal work for its addressee.
        return $true
    }
    return $true
}
