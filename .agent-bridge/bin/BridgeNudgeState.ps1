#requires -Version 5.1
<#
.SYNOPSIS
    Pure task-keyed state reduction for the bridge nudge watcher.

.DESCRIPTION
    Reduces bridge events for one target agent without writing files, emitting
    events, selecting windows, or sending keystrokes. Open request identity is
    preserved by target, requester, task id, and request type, then aggregated
    by target and task id for nudge accounting.
#>

Set-StrictMode -Version Latest

$classifier = Join-Path $PSScriptRoot 'BridgeEventClassifier.ps1'
if (-not (Test-Path -LiteralPath $classifier -PathType Leaf)) {
    throw "BridgeNudgeState requires BridgeEventClassifier.ps1 at $classifier"
}
. $classifier

function ConvertTo-BridgeNudgeUtc {
    param([object] $Value)

    if ($null -eq $Value) { return $null }
    if ($Value -is [DateTime]) { return $Value.ToUniversalTime() }
    $text = [string]$Value
    if (-not $text) { return $null }
    $styles = (
        [System.Globalization.DateTimeStyles]::AssumeUniversal -bor
        [System.Globalization.DateTimeStyles]::AdjustToUniversal
    )
    try {
        return [DateTime]::Parse(
            $text,
            [System.Globalization.CultureInfo]::InvariantCulture,
            $styles
        ).ToUniversalTime()
    } catch {
        return $null
    }
}

function New-BridgeNudgeOrdinalDictionary {
    return ,(
        [System.Collections.Generic.Dictionary[string,object]]::new(
            [System.StringComparer]::Ordinal
        )
    )
}

function Test-BridgeNudgeFollowTask {
    param([Parameter(Mandatory)] [object] $Event)

    return ([string]$Event.task_id).StartsWith(
        'bridge-follow-nudge',
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

function Get-BridgeNudgeAgentState {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [object[]] $Events,
        [Parameter(Mandatory)] [string] $Agent,
        [DateTime] $NowUtc = [DateTime]::UtcNow,
        [ValidateRange(0, [int]::MaxValue)]
        [int] $UnansweredSeconds = 900,
        [string[]] $RcoTriggerAgents = @(
            'claude-rco-1',
            'claude-rco-2',
            'operator'
        ),
        [switch] $SummaryOnly
    )

    $target = $Agent.ToLowerInvariant()
    $triggerAgents = New-BridgeNudgeOrdinalDictionary
    foreach ($triggerAgent in @($RcoTriggerAgents)) {
        if ([string]$triggerAgent) {
            $triggerAgents[([string]$triggerAgent).ToLowerInvariant()] = $true
        }
    }

    $records = New-Object System.Collections.Generic.List[object]
    $index = 0
    foreach ($event in @($Events)) {
        $ts = ConvertTo-BridgeNudgeUtc -Value $event.ts_utc
        if ($null -ne $ts) {
            [void]$records.Add([pscustomobject]@{
                event = $event
                ts = $ts
                index = $index
            })
        }
        $index++
    }
    $ordered = @($records | Sort-Object ts, index)
    # Nested exact-key index:
    #   task_id (Ordinal) -> requester -> request type -> open request.
    # It avoids ambiguous delimiter-composite keys and restricts resolution
    # work to the current event's task instead of scanning every open request.
    $openByTask = New-BridgeNudgeOrdinalDictionary

    foreach ($record in $ordered) {
        $event = $record.event
        $eventTs = [DateTime]$record.ts
        $taskId = [string]$event.task_id
        $eventAgent = ([string]$event.agent).ToLowerInvariant()

        # Resolve only the matching task, and require strict timestamp order.
        # An unrelated target-authored event must never clear another task.
        if ($taskId -and $openByTask.ContainsKey($taskId)) {
            $requestersForTask = $openByTask[$taskId]
            foreach ($requesterKey in @($requestersForTask.Keys)) {
                $typesForRequester = $requestersForTask[$requesterKey]
                foreach ($typeKey in @($typesForRequester.Keys)) {
                    $open = $typesForRequester[$typeKey]
                    $request = $open.request
                    if ($eventTs -le [DateTime]$open.opened_utc) {
                        continue
                    }
                    $targetAnswer = (
                        $eventAgent -eq $target -and
                        (Test-BridgeAnswerEvent -Event $event)
                    )
                    $requesterClosure = (
                        $eventAgent -eq $requesterKey -and
                        (Test-BridgeRequesterClosureForRequest `
                            -Request $request `
                            -Event $event)
                    )
                    if ($targetAnswer -or $requesterClosure) {
                        [void]$typesForRequester.Remove($typeKey)
                    }
                }
                if ($typesForRequester.Count -eq 0) {
                    [void]$requestersForTask.Remove($requesterKey)
                }
            }
            if ($requestersForTask.Count -eq 0) {
                [void]$openByTask.Remove($taskId)
            }
        }

        if (
            -not $taskId -or
            $eventAgent -eq $target -or
            (Test-BridgeNudgeFollowTask -Event $event) -or
            -not (Test-BridgeAddressedTo -Event $event -TargetAgent $target) -or
            -not (Test-BridgeRequestLikeEvent -Event $event)
        ) {
            continue
        }

        if (-not $openByTask.ContainsKey($taskId)) {
            $openByTask[$taskId] = New-BridgeNudgeOrdinalDictionary
        }
        $requestersForTask = $openByTask[$taskId]
        $requesterKey = $eventAgent
        if (-not $requestersForTask.ContainsKey($requesterKey)) {
            $requestersForTask[$requesterKey] = New-BridgeNudgeOrdinalDictionary
        }
        $typesForRequester = $requestersForTask[$requesterKey]
        $typeKey = ([string]$event.type).ToLowerInvariant()
        if (-not $typesForRequester.ContainsKey($typeKey)) {
            # Preserve the first still-open timestamp. Repeated pokes for the
            # same request must not reset its age or inflate the task count.
            $typesForRequester[$typeKey] = [pscustomobject]@{
                request = $event
                opened_utc = $eventTs
            }
        }
    }

    $openRequests = New-Object System.Collections.Generic.List[object]
    $openTaskIds = New-Object System.Collections.Generic.List[string]
    $overdueTaskIds = New-Object System.Collections.Generic.List[string]
    $rcoWakeTaskIds = New-Object System.Collections.Generic.List[string]
    $openRequestCount = 0
    $overdueTaskCount = 0
    $freshRcoWake = $false
    foreach ($taskId in @($openByTask.Keys)) {
        if (-not $SummaryOnly) {
            [void]$openTaskIds.Add($taskId)
        }
        $oldestAgeSeconds = -1.0
        $taskHasRcoWake = $false
        $requestersForTask = $openByTask[$taskId]
        foreach ($requesterKey in @($requestersForTask.Keys)) {
            $typesForRequester = $requestersForTask[$requesterKey]
            foreach ($typeKey in @($typesForRequester.Keys)) {
                $openRequestCount++
                $open = $typesForRequester[$typeKey]
                $request = $open.request
                $openedUtc = [DateTime]$open.opened_utc
                $ageSeconds = [Math]::Max(
                    0.0,
                    ($NowUtc.ToUniversalTime() - $openedUtc).TotalSeconds
                )
                if ($ageSeconds -gt $oldestAgeSeconds) {
                    $oldestAgeSeconds = $ageSeconds
                }
                if (-not $SummaryOnly) {
                    [void]$openRequests.Add([pscustomobject]@{
                        target = $target
                        requester = $requesterKey
                        task_id = $taskId
                        request_type = $typeKey
                        request_status = ([string]$request.status).ToLowerInvariant()
                        opened_utc = $openedUtc.ToString('o')
                        age_seconds = $ageSeconds
                    })
                }
                if (
                    $typeKey -eq 'wake_request' -and
                    $triggerAgents.ContainsKey($requesterKey)
                ) {
                    $taskHasRcoWake = $true
                }
            }
        }
        if ($oldestAgeSeconds -gt $UnansweredSeconds) {
            $overdueTaskCount++
            if (-not $SummaryOnly) {
                [void]$overdueTaskIds.Add($taskId)
            }
        }
        if ($taskHasRcoWake) {
            $freshRcoWake = $true
            if (-not $SummaryOnly) {
                [void]$rcoWakeTaskIds.Add($taskId)
            }
        }
    }

    $sortedOpenTaskIds = @($openTaskIds | Sort-Object)
    $sortedOverdueTaskIds = @($overdueTaskIds | Sort-Object)
    $sortedRcoWakeTaskIds = @($rcoWakeTaskIds | Sort-Object)

    return [pscustomobject]@{
        agent = $target
        open_incoming_count = $overdueTaskCount
        open_task_count = $openByTask.Count
        open_task_ids = $sortedOpenTaskIds
        overdue_task_ids = $sortedOverdueTaskIds
        open_request_count = $openRequestCount
        open_requests = @($openRequests | Sort-Object task_id, requester, request_type)
        fresh_rco_wake = $freshRcoWake
        rco_wake_task_ids = $sortedRcoWakeTaskIds
    }
}
