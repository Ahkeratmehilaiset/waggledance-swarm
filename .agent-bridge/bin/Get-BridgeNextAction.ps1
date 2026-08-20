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
    foreach ($line in $lines) {
        if (-not $line) { continue }
        try {
            $obj = $line | ConvertFrom-Json -ErrorAction Stop
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
                # Record the append order of the read window so closure
                # decisions can compare log order, not only timestamps.
                # -Force so a same-named field inside the JSON payload can
                # never shadow the real read order.
                $obj | Add-Member -NotePropertyName '__append_index' `
                    -NotePropertyValue $items.Count -Force
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
        } |
        Sort-Object ts_utc
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

function Get-BridgeEventIdentityField {
    param(
        [Parameter(Mandatory)] [object] $Event,
        [Parameter(Mandatory)] [string] $Name
    )
    $prop = $Event.PSObject.Properties[$Name]
    if ($null -eq $prop -or $null -eq $prop.Value) { return '' }
    return ([string]$prop.Value).Trim()
}

function Test-BridgeClosureOccursAfterRequest {
    param(
        [Parameter(Mandatory)] [object] $Closure,
        [Parameter(Mandatory)] [object] $Request
    )
    # Mirror of tools/bridge_next_action.py::_closure_occurs_after_request:
    # a closure counts only when it follows the request in BOTH append order
    # and timestamp order. Timestamp order alone (the previous raw string
    # ts_utc comparison here) lets a post-dated closure appended before the
    # request suppress it; append order alone lets a stale WAL/spool-replayed
    # closure at the log tail close a request renewed after it. When either
    # side has no parseable timestamp the comparison falls back to append
    # order, preserving the legacy behavior for malformed records.
    $closureIndex = [int]$Closure.__append_index
    $requestIndex = [int]$Request.__append_index
    if ($closureIndex -le $requestIndex) { return $false }
    $closureTs = ConvertTo-BridgeUtcDateTime -Value ([string]$Closure.ts_utc)
    $requestTs = ConvertTo-BridgeUtcDateTime -Value ([string]$Request.ts_utc)
    if ($null -eq $closureTs -or $null -eq $requestTs) { return $true }
    return ($closureTs -ge $requestTs)
}

function Test-BridgeRequesterIdentityMatch {
    param(
        [Parameter(Mandatory)] [object] $Request,
        [Parameter(Mandatory)] [object] $Closure
    )
    # Mirror of tools/bridge_next_action.py::_requester_identity_matches: a
    # requester closeout binds to every identity field present on the
    # request, so a writer that only shares the agent name cannot close a
    # request bound to a specific agent_uuid/session_id. Fields absent on
    # the request stay wildcards so legacy events keep closing.
    $requestUuid = (Get-BridgeEventIdentityField -Event $Request -Name 'agent_uuid').ToLowerInvariant()
    if ($requestUuid) {
        $closureUuid = (Get-BridgeEventIdentityField -Event $Closure -Name 'agent_uuid').ToLowerInvariant()
        if ($closureUuid -cne $requestUuid) { return $false }
    }
    $requestSession = Get-BridgeEventIdentityField -Event $Request -Name 'session_id'
    if ($requestSession) {
        $closureSession = Get-BridgeEventIdentityField -Event $Closure -Name 'session_id'
        if ($closureSession -cne $requestSession) { return $false }
    }
    return $true
}

function Test-BridgeRequestStillOpen {
    param([Parameter(Mandatory)] [object] $Request)

    $answer = @(
        $events |
            Where-Object {
                [string]$_.agent -eq $Agent -and
                [string]$_.task_id -eq [string]$Request.task_id -and
                (Test-BridgeClosureOccursAfterRequest -Closure $_ -Request $Request) -and
                (Test-BridgeAnswerEvent -Event $_)
            } |
            Select-Object -First 1
    )
    if ($answer.Count -gt 0) { return $false }
    $requesterClosure = @(
        $events |
            Where-Object {
                [string]$_.agent -eq [string]$Request.agent -and
                [string]$_.task_id -eq [string]$Request.task_id -and
                (Test-BridgeRequesterIdentityMatch -Request $Request -Closure $_) -and
                (Test-BridgeClosureOccursAfterRequest -Closure $_ -Request $Request) -and
                (Test-BridgeRequesterClosureEvent -Event $_)
            } |
            Select-Object -First 1
    )
    return ($requesterClosure.Count -eq 0)
}

$candidateOpenRequests = New-Object System.Collections.Generic.List[object]
foreach ($req in $freshRequestsForAgent) {
    if (Test-BridgeRequestStillOpen -Request $req) {
        [void]$candidateOpenRequests.Add($req)
    }
}

# Stale bucket: previously counted raw (no dedup, no answered check), which
# inflated stale_incoming_count with every repeated poke ever received and
# produced false dark-agent alarms. Dedup by requester+task (latest poke per
# pair) FIRST, then apply the same answered/closure filter as the fresh path.
$staleOpenRequests = New-Object System.Collections.Generic.List[object]
$staleByKey = @{}
foreach ($req in $staleRequests) {
    $key = "$([string]$req.agent)|$([string]$req.task_id)"
    $staleByKey[$key] = $req  # requests are ts-sorted; last wins
}
foreach ($req in @($staleByKey.Values)) {
    if (Test-BridgeRequestStillOpen -Request $req) {
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

$result = [pscustomobject]@{
    agent = $Agent
    action = $kind
    task_id = $taskId
    safe_mode = $safeMode
    summary = $summary
    active_claim_count = $claims.Count
    open_incoming_count = $openRequests.Count
    stale_incoming_count = $staleOpenRequests.Count
    foreign_write_claim_count = $foreignWriteClaims.Count
}
if ($suppressionReason) {
    $result | Add-Member -NotePropertyName suppression_reason -NotePropertyValue $suppressionReason
}

if ($Json) {
    $result | ConvertTo-Json -Depth 8
} else {
    $result | Format-List
}
