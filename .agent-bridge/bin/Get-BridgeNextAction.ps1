#requires -Version 5.1
<#
.SYNOPSIS
    R23.1 idle helper: choose the next safe bridge action for one agent.

.DESCRIPTION
    Reads active claims and unresolved incoming bridge requests. It emits a
    small machine-readable recommendation so an autonomy loop can avoid
    silently idling while the other agent owns unrelated work.

    This script does not mutate bridge state.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateScript({ $_ -cmatch '^[a-z][a-z0-9_-]{1,32}$' })]
    [string] $Agent,

    [int] $Tail = 5000,

    [double] $OpenRequestMaxAgeHours = 12.0,

    [string] $Now = '',

    [switch] $Json
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$bridgeRoot = if ($env:AGENT_BRIDGE_RUNTIME_ROOT) {
    [string]$env:AGENT_BRIDGE_RUNTIME_ROOT
} else {
    Split-Path -Parent $PSScriptRoot
}
if (-not (Test-Path -LiteralPath $bridgeRoot -PathType Container)) {
    [void](New-Item -ItemType Directory -Path $bridgeRoot -Force -ErrorAction Stop)
}
$eventsPath = Join-Path (Join-Path $bridgeRoot 'shared') 'events.jsonl'
$claimsDir = Join-Path (Join-Path $bridgeRoot 'work_queue') 'claims'
$classifier = Join-Path $PSScriptRoot 'BridgeEventClassifier.ps1'
. (Join-Path $PSScriptRoot 'BridgeRequestContract.ps1')
. (Join-Path $PSScriptRoot 'BridgeRoster.ps1')
if (Test-Path -LiteralPath $classifier -PathType Leaf) {
    . $classifier
}

if (
    [double]::IsNaN($OpenRequestMaxAgeHours) -or
    [double]::IsInfinity($OpenRequestMaxAgeHours) -or
    $OpenRequestMaxAgeHours -le 0
) {
    throw 'OpenRequestMaxAgeHours must be positive'
}

function ConvertTo-BridgeUtcDateTime {
    param([string] $Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $null }
    $styles = (
        [System.Globalization.DateTimeStyles]::AssumeUniversal -bor
        [System.Globalization.DateTimeStyles]::AdjustToUniversal
    )
    try {
        return ([System.DateTimeOffset]::Parse(
            $Value,
            [System.Globalization.CultureInfo]::InvariantCulture,
            $styles
        )).UtcDateTime
    } catch {
        return $null
    }
}

$nowUtc = if ($Now) {
    $parsedNow = ConvertTo-BridgeUtcDateTime -Value $Now
    if ($null -eq $parsedNow) { throw 'Now must be an ISO-8601 timestamp' }
    $parsedNow
} else {
    (Get-Date).ToUniversalTime()
}
$openRequestCutoffUtc = $nowUtc.AddHours(-1 * $OpenRequestMaxAgeHours)
$bridgeFollowNudgeTaskPrefix = 'bridge-follow-nudge-'

function Test-BridgeFollowNudgeRequest {
    param([Parameter(Mandatory)] [object] $Event)

    return [string]$Event.type -eq 'wake_request' -and
        [string]$Event.task_id -and
        ([string]$Event.task_id).StartsWith(
            $bridgeFollowNudgeTaskPrefix,
            [System.StringComparison]::OrdinalIgnoreCase
        )
}

function Read-BridgeEventObjects {
    param([string] $Path, [int] $MaxLines)
    $items = New-Object System.Collections.Generic.List[object]
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $items }
    $lines = if ($MaxLines -le 0) {
        @(Get-Content -Path $Path -Encoding UTF8)
    } else {
        @(Get-Content -Path $Path -Tail $MaxLines -Encoding UTF8)
    }
    $jsonArguments = @{ ErrorAction = 'Stop' }
    if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) {
        $jsonArguments.DateKind = 'String'
    }
    foreach ($line in $lines) {
        if (-not $line) { continue }
        try {
            $obj = $line | ConvertFrom-Json @jsonArguments
            # Shape guard: a transient partial read of the shared log can
            # yield bare null / scalar / array lines. NOTE: `-is
            # [pscustomobject]` is NOT a valid shape test here - PowerShell
            # wraps scalars in PSObject so `42 -is [pscustomobject]` is true.
            # Require the core event members every writer emits, so StrictMode
            # consumers can never throw on a missing property.
            if (
                $null -ne $obj -and
                $null -ne $obj.PSObject -and
                $null -ne $obj.PSObject.Properties['type'] -and
                $null -ne $obj.PSObject.Properties['task_id'] -and
                $null -ne $obj.PSObject.Properties['agent']
            ) {
                [void]$items.Add($obj)
            }
        } catch {}
    }
    return $items
}

function Read-ClaimObjects {
    $items = New-Object System.Collections.Generic.List[object]
    if (-not (Test-Path -LiteralPath $claimsDir -PathType Container)) { return $items }
    foreach ($file in @(Get-ChildItem -Path $claimsDir -Filter '*.json' -File -ErrorAction SilentlyContinue)) {
        try { [void]$items.Add((Get-Content -Raw -Path $file.FullName -Encoding UTF8 | ConvertFrom-Json)) } catch {}
    }
    return $items
}

function Get-BridgeSuppressedAgentReason {
    param([Parameter(Mandatory)] [string] $AgentName)

    $path = Join-Path (Join-Path $bridgeRoot 'shared') 'production_liveness_suppression.json'
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return '' }

    $config = Get-Content -Raw -Path $path -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
    if (-not $config.PSObject.Properties['suppressed_agents']) { return '' }

    $agents = $config.suppressed_agents
    $entry = $agents.PSObject.Properties[$AgentName]
    if ($null -eq $entry) { return '' }
    $value = $entry.Value
    if ($null -eq $value) { return '' }
    if ($value.PSObject.Properties['reason']) {
        return [string]$value.reason
    }
    return [string]$value
}

$events = @(Read-BridgeEventObjects -Path $eventsPath -MaxLines $Tail)
$requestIndex = New-BridgeRequestIndex $events
$claims = @(Read-ClaimObjects)
$suppressionReason = Get-BridgeSuppressedAgentReason -AgentName $Agent
$ownClaims = @($claims | Where-Object { [string]$_.agent -eq $Agent })
$foreignWriteClaims = @($claims | Where-Object { [string]$_.agent -ne $Agent -and [string]$_.mode -eq 'write' })

$requestsForAgent = @(
    $events |
        Where-Object {
            (Test-BridgeRequestLikeEvent -Event $_) -and
            -not (Test-BridgeFollowNudgeRequest -Event $_) -and
            (Test-BridgeAddressedTo -Event $_ -TargetAgent $Agent)
        }
)

$freshRequestsForAgent = New-Object System.Collections.Generic.List[object]
$staleRequests = New-Object System.Collections.Generic.List[object]
foreach ($req in $requestsForAgent) {
    $requestTs = ConvertTo-BridgeUtcDateTime -Value ([string]$req.ts_utc)
    if ($null -ne $requestTs -and $requestTs -lt $openRequestCutoffUtc) {
        [void]$staleRequests.Add($req)
    } else {
        [void]$freshRequestsForAgent.Add($req)
    }
}

function Test-BridgeRequestStillOpen {
    param([Parameter(Mandatory)] [object] $Request)
    $ambiguous = Test-BridgeAmbiguousLegacy $requestIndex $Request
    foreach ($answer in $requestIndex.by_task[[string]$Request.task_id]) {
        $closure = $answer.agent -ceq $Request.agent -and (Test-BridgeRequesterClosureEvent $answer)
        if (($closure -or (Test-BridgeAnswerEvent $answer)) -and
            (Test-BridgeReplyBinding -Request $Request -Reply $answer -Target $Agent -RequesterClosure $closure -AmbiguousLegacy $ambiguous -RequestPosition $requestIndex.positions[$Request] -ReplyPosition $requestIndex.positions[$answer])) {
            return $false
        }
    }
    return $true
}

function Get-BridgeExactStringField {
    param([AllowNull()] [object] $Record, [Parameter(Mandatory)] [string] $Name)
    # Top-level, case-exact, string-only read: no payload fallback and no case-insensitive PSObject match.
    if ($null -eq $Record -or $Record.GetType() -ne [System.Management.Automation.PSCustomObject]) { return $null }
    $property = $Record.PSObject.Properties[$Name]
    if ($null -eq $property -or $property.Name -cne $Name -or $property.Value -isnot [string]) { return $null }
    return [string]$property.Value
}

function Test-BridgeRequestCancelledWithheld {
    <# ROUTING WITHHOLD only, never an answer, completion or permission: a LATER event on the same task by the
       exact requester agent AND agent_uuid (any session of that identity), status exactly 'cancelled', whose
       payload is exactly the closed wd.request-cancellation.v1 {schema, cancelled_request_id, cancelled_request_digest,
       scope: whole_request} naming this request_id and its stored request_digest. Anything else (another id,
       digest, task, position, label, uuid, blank or non-string identity, legacy or extra keys) leaves the request
       open; the shared Test-BridgeReplyBinding contract is unchanged. #>
    param([Parameter(Mandatory)] [object] $Request)
    $rid = Get-BridgeExactStringField $Request 'request_id'
    $digest = Get-BridgeExactStringField $Request 'request_digest'
    $requester = Get-BridgeExactStringField $Request 'agent'
    $uuid = Get-BridgeExactStringField $Request 'agent_uuid'
    $task = Get-BridgeExactStringField $Request 'task_id'
    if (-not $rid -or -not $requester -or -not $task -or -not $uuid -or $uuid -cnotmatch '^[A-Za-z0-9._:-]{1,128}$' -or
        $digest -cnotmatch '^[0-9a-f]{64}$' -or -not $requestIndex.positions.ContainsKey($Request) -or
        -not $requestIndex.by_task.ContainsKey($task)) {
        return $false
    }
    $requestPosition = $requestIndex.positions[$Request]
    foreach ($event in $requestIndex.by_task[$task]) {
        if ($requestIndex.positions[$event] -le $requestPosition) { continue }
        if ((Get-BridgeExactStringField $event 'agent') -cne $requester -or
            (Get-BridgeExactStringField $event 'agent_uuid') -cne $uuid -or
            (Get-BridgeExactStringField $event 'task_id') -cne $task -or
            (Get-BridgeExactStringField $event 'status') -cne 'cancelled') {
            continue
        }
        $payloadProperty = $event.PSObject.Properties['payload']
        if ($null -eq $payloadProperty -or $payloadProperty.Name -cne 'payload') { continue }
        $payload = $payloadProperty.Value
        if ($null -eq $payload -or $payload.GetType() -ne [System.Management.Automation.PSCustomObject]) { continue }
        $names = @($payload.PSObject.Properties | ForEach-Object { $_.Name })
        if ($names.Count -ne 4 -or
            (Get-BridgeExactStringField $payload 'schema') -cne 'wd.request-cancellation.v1' -or
            (Get-BridgeExactStringField $payload 'scope') -cne 'whole_request' -or
            (Get-BridgeExactStringField $payload 'cancelled_request_id') -cne $rid -or
            (Get-BridgeExactStringField $payload 'cancelled_request_digest') -cne $digest) {
            continue
        }
        return $true
    }
    return $false
}

$cancelledWithheld = New-Object System.Collections.Generic.List[object]
$candidateOpenRequests = New-Object System.Collections.Generic.List[object]
$freshByKey = [System.Collections.Generic.Dictionary[string,object]]::new([StringComparer]::Ordinal)
foreach ($req in $freshRequestsForAgent) {
    $rid = Get-BridgeContractField $req 'request_id'
    $key = if ($rid) { "id|$($req.agent)|$rid" } elseif ($req.type -ceq 'wake_request') { "wake|$($req.agent)|$($req.task_id)|$($req.status)" } else { "event|$($freshByKey.Count)" }
    if ($rid -and $freshByKey.ContainsKey($key)) {
        if ((Get-BridgeRequestContent $freshByKey[$key]) -cne (Get-BridgeRequestContent $req) -or
            (Get-BridgeContractField $freshByKey[$key] 'request_digest') -cne (Get-BridgeContractField $req 'request_digest')) {
            $freshByKey[$key] | Add-Member -Force NoteProperty request_binding_conflict $true
        }
    } elseif ($freshByKey.ContainsKey($key) -and
        [string]$freshByKey[$key].ts_utc -ceq [string]$req.ts_utc -and
        (Get-BridgeRequestContent $freshByKey[$key]) -ceq (Get-BridgeRequestContent $req)) {
        # An identical replay does not reset the request's append position.
        continue
    } else { $freshByKey[$key] = $req }
}
$openEventCount = 0
foreach ($req in @($freshByKey.Values | Sort-Object ts_utc)) {
    if ((Test-BridgeRequestStillOpen -Request $req) -and (Test-BridgeRequestCancelledWithheld -Request $req)) {
        [void]$cancelledWithheld.Add($req)
    } elseif (Test-BridgeRequestStillOpen -Request $req) {
        [void]$candidateOpenRequests.Add($req)
        $rid = Get-BridgeContractField $req 'request_id'
        $openEventCount += @($freshRequestsForAgent | Where-Object {
            if ($rid) { (Get-BridgeContractField $_ 'request_id') -ceq $rid -and $_.agent -ceq $req.agent }
            elseif ($req.type -ceq 'wake_request') { $_.type -ceq $req.type -and $_.agent -ceq $req.agent -and $_.task_id -ceq $req.task_id -and $_.status -ceq $req.status }
            else { $_ -eq $req }
        }).Count
    }
}

# Stale bucket: previously counted raw (no dedup, no answered check), which
# inflated stale_incoming_count with every repeated poke ever received and
# produced false dark-agent alarms. Dedup by requester+task (latest poke per
# pair) FIRST, then apply the same answered/closure filter as the fresh path.
$staleOpenRequests = New-Object System.Collections.Generic.List[object]
$staleByKey = [System.Collections.Generic.Dictionary[string,object]]::new([StringComparer]::Ordinal)
foreach ($req in $staleRequests) {
    $key = Get-BridgeRequestViewKey $req
    if (-not (Get-BridgeContractField $req 'request_id') -and $req.type -ceq 'wake_request') { $key += '|' + [string]$req.status }
    Set-BridgeRequestViewEntry $staleByKey $key $req
}
foreach ($req in @($staleByKey.Values)) {
    if ((Test-BridgeRequestStillOpen -Request $req) -and (Test-BridgeRequestCancelledWithheld -Request $req)) {
        [void]$cancelledWithheld.Add($req)
    } elseif (Test-BridgeRequestStillOpen -Request $req) {
        [void]$staleOpenRequests.Add($req)
    }
}

$openRequests = $candidateOpenRequests

$kind = ''
$taskId = ''
$summary = ''
$safeMode = 'read-only'

if ($ownClaims.Count -gt 0) {
    $kind = 'continue_claim'
    $taskId = [string]$ownClaims[0].task_id
    $summary = "continue active claim $taskId"
    $safeMode = [string]$ownClaims[0].mode
} elseif ($suppressionReason) {
    $kind = 'agent_suppressed_unavailable'
    $taskId = 'agent-suppressed-unavailable'
    $summary = "agent $Agent is suppressed unavailable: $suppressionReason"
    $safeMode = 'read-only'
} elseif ($openRequests.Count -gt 0) {
    $req = @($openRequests | Select-Object -Last 1)[0]
    $kind = 'answer_incoming'
    $taskId = [string]$req.task_id
    $summary = "answer incoming $([string]$req.type)/$([string]$req.status) from $([string]$req.agent)"
    $safeMode = 'read-only'
} elseif ($foreignWriteClaims.Count -gt 0) {
    $kind = 'parallel_read_only'
    $taskId = 'bridge-review-or-scout'
    $summary = "foreign write claim active; take read-only review/scout outside scope: $((@($foreignWriteClaims[0].write_scope)) -join ',')"
    $safeMode = 'read-only'
} else {
    $kind = 'claim_unblocked_work'
    $taskId = 'next-unclaimed-scout-or-implementation'
    $summary = 'no active claim or incoming blocker; claim the highest-value unblocked scout/review/implementation'
    $safeMode = 'write-or-read-only'
}

$knownRequestAges = @($openRequests | ForEach-Object {
    $stamp = ConvertTo-BridgeContractTime $_.ts_utc
    if ($null -ne $stamp) { [math]::Max(0, ($nowUtc - $stamp).TotalSeconds) }
})
$result = [pscustomobject]@{
    agent = $Agent
    worker_class = Get-BridgeWorkerClass $Agent
    action = $kind
    task_id = $taskId
    safe_mode = $safeMode
    summary = $summary
    active_claim_count = $claims.Count
    open_incoming_count = $openRequests.Count
    open_incoming_event_count = $openEventCount
    open_incoming_task_count = @($openRequests | Select-Object -ExpandProperty task_id -Unique).Count
    oldest_open_request_age_seconds = if ($openRequests.Count -and $knownRequestAges.Count -eq $openRequests.Count) {
        ($knownRequestAges | Measure-Object -Maximum).Maximum
    } else { $null }
    stale_incoming_count = @($staleOpenRequests | Select-Object -ExpandProperty task_id -Unique).Count
    stale_incoming_request_count = $staleOpenRequests.Count
    foreign_write_claim_count = $foreignWriteClaims.Count
    # Withheld from routing by an exact v1 whole_request cancellation: NOT answered, completed or accepted.
    cancelled_withheld_count = $cancelledWithheld.Count
    cancelled_withheld_request_ids = @($cancelledWithheld | ForEach-Object { [string]$_.request_id } | Sort-Object -Unique)
}
if ($kind -eq 'answer_incoming') {
    $result | Add-Member -NotePropertyName incoming -NotePropertyValue $req
}
if ($suppressionReason) {
    $result | Add-Member -NotePropertyName suppression_reason -NotePropertyValue $suppressionReason
}

if ($Json) {
    $result | ConvertTo-Json -Depth 8
} else {
    $result | Format-List
}
