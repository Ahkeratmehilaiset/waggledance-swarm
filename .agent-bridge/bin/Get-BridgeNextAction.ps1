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

    [int] $Tail = 50000,

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
        try { [void]$items.Add(($line | ConvertFrom-Json -ErrorAction Stop)) } catch {}
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

$candidateOpenRequests = New-Object System.Collections.Generic.List[object]
foreach ($req in $freshRequestsForAgent) {
    $answer = @(
        $events |
            Where-Object {
                [string]$_.agent -eq $Agent -and
                [string]$_.task_id -eq [string]$req.task_id -and
                [string]$_.ts_utc -gt [string]$req.ts_utc -and
                (Test-BridgeAnswerEvent -Event $_)
            } |
            Sort-Object ts_utc |
            Select-Object -Last 1
    )
    $requesterClosure = @(
        $events |
            Where-Object {
                [string]$_.agent -eq [string]$req.agent -and
                [string]$_.task_id -eq [string]$req.task_id -and
                [string]$_.ts_utc -gt [string]$req.ts_utc -and
                (Test-BridgeRequesterClosureEvent -Event $_)
            } |
            Sort-Object ts_utc |
            Select-Object -Last 1
    )
    if ($answer.Count -eq 0 -and $requesterClosure.Count -eq 0) {
        [void]$candidateOpenRequests.Add($req)
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
    stale_incoming_count = $staleRequests.Count
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
