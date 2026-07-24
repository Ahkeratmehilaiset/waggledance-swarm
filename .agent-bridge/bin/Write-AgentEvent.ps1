#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [ValidateScript({ $_ -cmatch '^[a-z][a-z0-9_-]{1,32}$' })] [string] $Agent,
    [Parameter(Mandatory)] [ValidateSet('status','intent','claim','release','message','finding','decision','test','blocked','handoff','done','heartbeat','wake_request','liveness')] [string] $Type,
    [string] $TaskId = '',
    [string] $Status = '',
    [string] $Message = '',
    [string] $To = '',
    [string[]] $Paths = @(),
    [string[]] $WriteScope = @(),
    [string] $Severity = '',
    [string] $RunId = '',
    [string] $Role = '',
    [string] $AgentUuid = '',
    [string] $SessionId = '',
    [string[]] $Capabilities = @(),
    [string] $PayloadJson = '{}'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$privateMarkers = @('PRIVATE_MARKER', '_DO_NOT_LEAK')
function Assert-NoPrivateMarker {
    param(
        [Parameter(Mandatory)] [string] $Label,
        [AllowNull()] $Value
    )
    if ($null -eq $Value) {
        return
    }
    $items = if ($Value -is [array]) { @($Value) } else { @($Value) }
    foreach ($item in $items) {
        $text = [string]$item
        foreach ($marker in $privateMarkers) {
            if ($text.IndexOf($marker, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
                throw "Bridge event $Label contains a private marker"
            }
        }
    }
}

Assert-NoPrivateMarker -Label 'task_id' -Value $TaskId
Assert-NoPrivateMarker -Label 'status' -Value $Status
Assert-NoPrivateMarker -Label 'severity' -Value $Severity
Assert-NoPrivateMarker -Label 'message' -Value $Message
Assert-NoPrivateMarker -Label 'to' -Value $To
Assert-NoPrivateMarker -Label 'paths' -Value $Paths
Assert-NoPrivateMarker -Label 'write_scope' -Value $WriteScope
Assert-NoPrivateMarker -Label 'run_id' -Value $RunId
Assert-NoPrivateMarker -Label 'role' -Value $Role
Assert-NoPrivateMarker -Label 'agent_uuid' -Value $AgentUuid
Assert-NoPrivateMarker -Label 'session_id' -Value $SessionId
Assert-NoPrivateMarker -Label 'capabilities' -Value $Capabilities
Assert-NoPrivateMarker -Label 'payload' -Value $PayloadJson

function Resolve-BridgeMetadataString {
    param([string] $Explicit, [string] $EnvName)
    if ($Explicit) { return $Explicit }
    $value = [Environment]::GetEnvironmentVariable($EnvName, 'Process')
    if ($value) { return [string]$value }
    return ''
}

function Resolve-BridgeCapabilities {
    param([string[]] $Explicit)
    $source = @($Explicit)
    if ($source.Count -eq 0) {
        $value = [Environment]::GetEnvironmentVariable('AGENT_BRIDGE_CAPABILITIES', 'Process')
        if ($value) { $source = @([string]$value) }
    }
    return @(
        $source |
            ForEach-Object { [string]$_ -split '[,;]' } |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ }
    )
}

function Assert-BridgeAgentTargets {
    param([AllowNull()] [string] $Targets)
    if ([string]::IsNullOrEmpty($Targets)) {
        return
    }
    $targetList = @(
        $Targets -split ',' |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ }
    )
    if ($targetList.Count -eq 0) {
        throw "to must be empty or comma-separated agents"
    }
    $allowedNonAgentTargets = @('github/main')
    foreach ($target in $targetList) {
        if (($target -cnotmatch '^[a-z][a-z0-9_-]{1,32}$') -and ($allowedNonAgentTargets -cnotcontains $target)) {
            throw "to contains invalid bridge agent id: $target"
        }
    }
}

$Role = Resolve-BridgeMetadataString -Explicit $Role -EnvName 'AGENT_BRIDGE_ROLE'
$AgentUuid = Resolve-BridgeMetadataString -Explicit $AgentUuid -EnvName 'AGENT_BRIDGE_AGENT_UUID'
$SessionId = Resolve-BridgeMetadataString -Explicit $SessionId -EnvName 'AGENT_BRIDGE_SESSION_ID'
$Capabilities = @(Resolve-BridgeCapabilities -Explicit $Capabilities)

if (-not $SessionId -and $RunId) {
    $SessionId = $RunId
}

Assert-NoPrivateMarker -Label 'role' -Value $Role
Assert-NoPrivateMarker -Label 'agent_uuid' -Value $AgentUuid
Assert-NoPrivateMarker -Label 'session_id' -Value $SessionId
Assert-NoPrivateMarker -Label 'capabilities' -Value $Capabilities

if ($Role -and $Role -notmatch '^[a-z][a-z0-9_-]{1,32}$') {
    throw "role must match ^[a-z][a-z0-9_-]{1,32}$"
}
if ($AgentUuid -and $AgentUuid -notmatch '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$') {
    throw "agent_uuid must be a UUID"
}
if ($SessionId -and $SessionId -notmatch '^[A-Za-z0-9._:-]{1,128}$') {
    throw "session_id must match ^[A-Za-z0-9._:-]{1,128}$"
}
foreach ($capability in @($Capabilities)) {
    if ($capability -notmatch '^[a-z][a-z0-9_.:-]{1,64}$') {
        throw "capability must match ^[a-z][a-z0-9_.:-]{1,64}$"
    }
}
Assert-BridgeAgentTargets -Targets $To

$taskIdRequiredTypes = @('claim', 'release', 'done', 'handoff', 'blocked')
$ackStatuses = @('acknowledged', 'received', 'seen')
$grokReviewAgents = @('grok-1', 'grok-scout-1')
$grokReviewStatuses = @('grok_response')
$rcoReviewAgents = @('claude-rco-1', 'claude-rco-2')
$rcoPassAuthorAliasAgents = @('codex-lead-1', 'codex-tools-1', 'fable-5', 'grok-scout-1')
$rcoPassTaskBindingFields = @('canonical_task_id', 'branch', 'headRefName', 'head_ref_name', 'branch_name')
$grokPrWorktreeStrictEpochUtc = '2026-06-04T08:32:00Z'
$grokRequiredFreshnessShaFields = @('remote_main_sha', 'local_origin_main_sha', 'worktree_head')
$grokOptionalFreshnessShaFields = @('pr_head_sha', 'reviewed_head_sha', 'target_head_sha')
$fullGitShaPattern = '^[0-9a-f]{40}$'
$bridgeTaskBindingPattern = '^[A-Za-z0-9._/-]{1,180}$'
# Keep this guard in lock-step with waggledance/core/bridge_event_schema.py.
# It must run before any bridge file I/O so invalid events fail closed.
$requiresTaskId = (
    ($taskIdRequiredTypes -contains $Type) -or
    (($Type -eq 'message') -and ($ackStatuses -contains $Status))
)
if ($requiresTaskId -and [string]::IsNullOrWhiteSpace($TaskId)) {
    $reason = if ($Type -eq 'message') {
        "type=message status=$Status"
    } else {
        "type=$Type"
    }
    throw "Bridge event $reason requires non-empty -TaskId before writing"
}
if (($Type -eq 'wake_request') -and [string]::IsNullOrWhiteSpace($To)) {
    throw "Bridge event type=wake_request requires non-empty -To before writing"
}

$payloadText = [string]$PayloadJson
if ([string]::IsNullOrWhiteSpace($payloadText)) {
    throw "Bridge event payload must be valid JSON before writing"
}

$payload = $null
try {
    $payload = $payloadText | ConvertFrom-Json -ErrorAction Stop
} catch {
    throw "Bridge event payload must be valid JSON before writing"
}
if ($null -eq $payload) {
    if ($payloadText.Trim() -cne 'null') {
        throw "Bridge event payload must be valid JSON before writing"
    }
    $payload = [pscustomobject]@{}
}
Assert-NoPrivateMarker -Label 'payload' -Value ($payload | ConvertTo-Json -Depth 12 -Compress)

function Test-BridgeObject {
    param([AllowNull()] $Value)
    return (
        $null -ne $Value -and
        (
            $Value -is [System.Management.Automation.PSCustomObject] -or
            $Value -is [hashtable]
        )
    )
}

function Test-FullGitSha {
    param([AllowNull()] $Value)
    return ($Value -is [string] -and $Value -cmatch $fullGitShaPattern)
}

function Test-RcoTaskBindingText {
    param([AllowNull()] $Value)
    if (-not ($Value -is [string])) {
        return $false
    }
    $text = [string]$Value
    if (-not $text) {
        return $false
    }
    if ($text -cnotmatch $bridgeTaskBindingPattern) {
        return $false
    }
    if (
        $text.Contains('\') -or
        $text.Contains(':') -or
        $text.Contains('..') -or
        $text.Contains('//') -or
        $text.StartsWith('/') -or
        $text.EndsWith('/')
    ) {
        return $false
    }
    return $true
}

function Add-RcoTaskBindingCandidate {
    param(
        [Parameter(Mandatory)] [AllowEmptyCollection()] [System.Collections.ArrayList] $Bindings,
        [AllowNull()] $Value
    )
    if ($null -eq $Value) {
        return
    }
    if ($Value -is [array]) {
        foreach ($item in @($Value)) {
            Add-RcoTaskBindingCandidate -Bindings $Bindings -Value $item
        }
        return
    }
    $text = ([string]$Value).Trim()
    if (-not $text) {
        return
    }
    if (-not (Test-RcoTaskBindingText -Value $text)) {
        throw "rco_pass task binding contains unsafe task id"
    }
    if (-not $Bindings.Contains($text)) {
        [void]$Bindings.Add($text)
    }
}

function Add-RcoTaskBindingAliases {
    param(
        [Parameter(Mandatory)] [AllowEmptyCollection()] [System.Collections.ArrayList] $Allowed,
        [Parameter(Mandatory)] [string] $Task
    )
    Add-RcoTaskBindingCandidate -Bindings $Allowed -Value $Task
    foreach ($authorAgent in $rcoPassAuthorAliasAgents) {
        $slashPrefix = "$authorAgent/"
        $hyphenPrefix = "$authorAgent-"
        if ($Task.StartsWith($slashPrefix, [System.StringComparison]::Ordinal)) {
            $rest = $Task.Substring($slashPrefix.Length)
            if ($rest) {
                Add-RcoTaskBindingCandidate -Bindings $Allowed -Value "$authorAgent-$rest"
            }
        } elseif ($Task.StartsWith($hyphenPrefix, [System.StringComparison]::Ordinal)) {
            $rest = $Task.Substring($hyphenPrefix.Length)
            if ($rest) {
                Add-RcoTaskBindingCandidate -Bindings $Allowed -Value "$authorAgent/$rest"
            }
        }
    }
}

function Test-BridgeNowAtOrAfter {
    param([Parameter(Mandatory)] [string] $EpochUtc)
    $epoch = [DateTimeOffset]::Parse(
        $EpochUtc,
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::AssumeUniversal
    ).ToUniversalTime()
    return ([DateTimeOffset]::UtcNow -ge $epoch)
}

function Get-BridgeObjectField {
    param(
        [Parameter(Mandatory)] $Object,
        [Parameter(Mandatory)] [string] $Name
    )
    if ($Object -is [System.Collections.IDictionary]) {
        if ($Object.Contains($Name)) { return $Object[$Name] }
        return $null
    }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -ne $property) { return $property.Value }
    return $null
}

function Test-BridgeObjectHasField {
    param(
        [Parameter(Mandatory)] $Object,
        [Parameter(Mandatory)] [string] $Name
    )
    if ($Object -is [System.Collections.IDictionary]) { return $Object.Contains($Name) }
    return ($null -ne $Object.PSObject.Properties[$Name])
}

function Assert-GrokFreshnessPayload {
    param([AllowNull()] $Payload)
    if (-not (
        ($grokReviewAgents -contains $Agent) -and
        ($Type -eq 'message') -and
        ($grokReviewStatuses -contains $Status)
    )) {
        return
    }
    if (-not (Test-BridgeObject -Value $Payload)) {
        throw "grok freshness proof requires payload object"
    }
    $freshness = Get-BridgeObjectField -Object $Payload -Name 'freshness'
    if (-not (Test-BridgeObject -Value $freshness)) {
        throw "grok freshness proof required"
    }
    $freshnessOk = Get-BridgeObjectField -Object $freshness -Name 'freshness_ok'
    if (-not ($freshnessOk -is [bool]) -or $freshnessOk -ne $true) {
        throw "grok freshness_ok must be true"
    }
    foreach ($fieldName in $grokRequiredFreshnessShaFields) {
        $value = Get-BridgeObjectField -Object $freshness -Name $fieldName
        if (-not (Test-FullGitSha -Value $value)) {
            throw "grok freshness $fieldName must be lowercase 40-hex sha"
        }
    }
    $remoteMainSha = Get-BridgeObjectField -Object $freshness -Name 'remote_main_sha'
    $localOriginMainSha = Get-BridgeObjectField -Object $freshness -Name 'local_origin_main_sha'
    if ($remoteMainSha -cne $localOriginMainSha) {
        throw "grok freshness main sha mismatch"
    }
    $worktreeHead = Get-BridgeObjectField -Object $freshness -Name 'worktree_head'
    $expectedPrReviewWorktreeHeads = @()
    foreach ($fieldName in $grokOptionalFreshnessShaFields) {
        if (Test-BridgeObjectHasField -Object $freshness -Name $fieldName) {
            $value = Get-BridgeObjectField -Object $freshness -Name $fieldName
            if ($null -ne $value -and -not (Test-FullGitSha -Value $value)) {
                throw "grok freshness $fieldName must be lowercase 40-hex sha"
            }
            if ($null -ne $value) {
                $expectedPrReviewWorktreeHeads += $value
            }
        }
    }
    $expectedWorktreeHeads = if (Test-BridgeNowAtOrAfter -EpochUtc $grokPrWorktreeStrictEpochUtc) {
        @($localOriginMainSha)
    } elseif ($expectedPrReviewWorktreeHeads.Count -gt 0) {
        $expectedPrReviewWorktreeHeads + @($localOriginMainSha)
    } else {
        @($localOriginMainSha)
    }
    if ($expectedWorktreeHeads -cnotcontains $worktreeHead) {
        throw "grok freshness worktree sha mismatch"
    }
}

function Assert-RcoPassTaskBinding {
    param([AllowNull()] $Payload)
    if (-not (
        ($rcoReviewAgents -contains $Agent) -and
        ($Type -eq 'decision') -and
        ($Status -eq 'rco_pass')
    )) {
        return
    }
    if ([string]::IsNullOrWhiteSpace($TaskId)) {
        throw "rco_pass requires non-empty -TaskId"
    }
    if (-not (Test-RcoTaskBindingText -Value $TaskId)) {
        throw "rco_pass task_id contains unsafe task id"
    }
    if (-not (Test-BridgeObject -Value $Payload)) {
        throw "rco_pass canonical task binding requires payload object"
    }
    $head = Get-BridgeObjectField -Object $Payload -Name 'head'
    if (-not (Test-FullGitSha -Value $head)) {
        throw "rco_pass head must be lowercase 40-hex sha"
    }
    if ($Message.IndexOf([string]$head, [System.StringComparison]::Ordinal) -lt 0) {
        throw "rco_pass message must contain exact head"
    }

    $bindings = [System.Collections.ArrayList]::new()
    foreach ($fieldName in $rcoPassTaskBindingFields) {
        if (Test-BridgeObjectHasField -Object $Payload -Name $fieldName) {
            Add-RcoTaskBindingCandidate -Bindings $bindings -Value (Get-BridgeObjectField -Object $Payload -Name $fieldName)
        }
    }
    if (Test-BridgeObjectHasField -Object $Payload -Name 'accepted_task_ids') {
        Add-RcoTaskBindingCandidate -Bindings $bindings -Value (Get-BridgeObjectField -Object $Payload -Name 'accepted_task_ids')
    }
    if ($bindings.Count -eq 0) {
        # Existing RCO writers bind by -TaskId and carry payload {head,
        # operator_gated}. Keep that path working while enforcing any explicit
        # canonical binding when a caller supplies one.
        Add-RcoTaskBindingCandidate -Bindings $bindings -Value $TaskId
    }

    $allowedTaskIds = [System.Collections.ArrayList]::new()
    foreach ($binding in @($bindings)) {
        Add-RcoTaskBindingAliases -Allowed $allowedTaskIds -Task ([string]$binding)
    }
    if (-not $allowedTaskIds.Contains($TaskId)) {
        throw "rco_pass task_id does not match canonical task binding"
    }
}

Assert-GrokFreshnessPayload -Payload $payload
Assert-RcoPassTaskBinding -Payload $payload

function Assert-AgentUuidMatchesProfile {
    param([Parameter(Mandatory)] [string] $BridgeRoot)
    $agentsDir = Join-Path $BridgeRoot 'agents'
    $profilePath = Join-Path $agentsDir ($Agent + '.json')
    if (-not (Test-Path -LiteralPath $profilePath -PathType Leaf)) {
        return
    }
    try {
        $profile = Get-Content -Raw -Path $profilePath -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "bridge agent profile unreadable for agent: $Agent"
    }
    $profileAgent = Get-BridgeObjectField -Object $profile -Name 'agent_id'
    if ($profileAgent -and ([string]$profileAgent -cne $Agent)) {
        throw "bridge agent profile agent_id mismatch for agent: $Agent"
    }
    $expectedUuid = Get-BridgeObjectField -Object $profile -Name 'agent_uuid'
    if (-not $expectedUuid) {
        return
    }
    $expectedUuidText = [string]$expectedUuid
    if ($expectedUuidText -cnotmatch '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$') {
        throw "bridge agent profile agent_uuid must be a UUID for agent: $Agent"
    }
    if (-not $AgentUuid) {
        throw "agent_uuid required by bridge agent profile for agent: $Agent"
    }
    if ([string]$AgentUuid -cne $expectedUuidText) {
        throw "agent_uuid does not match bridge agent profile for agent: $Agent"
    }
}

function Assert-AgentUuidMatchesIdentityRegistry {
    param([Parameter(Mandatory)] [string] $RepoRoot)
    $registryPath = Join-Path (Join-Path $RepoRoot 'configs') 'bridge_identity_registry.json'
    if (-not (Test-Path -LiteralPath $registryPath -PathType Leaf)) {
        return
    }
    try {
        $registry = Get-Content -Raw -Path $registryPath -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "bridge identity registry unreadable"
    }
    $identities = Get-BridgeObjectField -Object $registry -Name 'identities'
    if (-not (Test-BridgeObject -Value $identities)) {
        throw "bridge identity registry identities must be an object"
    }
    $expectedUuid = Get-BridgeObjectField -Object $identities -Name $Agent
    if (-not $expectedUuid) {
        if ($AgentUuid) {
            foreach ($identity in @($identities.PSObject.Properties)) {
                $registeredAgent = [string]$identity.Name
                $registeredUuid = [string]$identity.Value
                if ([string]::Equals(
                    [string]$AgentUuid,
                    $registeredUuid,
                    [System.StringComparison]::OrdinalIgnoreCase
                )) {
                    throw "agent_uuid belongs to bridge identity registry agent: $registeredAgent"
                }
            }
        }
        return
    }
    $expectedUuidText = [string]$expectedUuid
    if ($expectedUuidText -cnotmatch '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$') {
        throw "bridge identity registry agent_uuid must be a UUID for agent: $Agent"
    }
    if (-not $AgentUuid) {
        throw "agent_uuid required by bridge identity registry for agent: $Agent"
    }
    if ([string]$AgentUuid -cne $expectedUuidText) {
        throw "agent_uuid does not match bridge identity registry for agent: $Agent"
    }
}

# R13: honor AGENT_BRIDGE_RUNTIME_ROOT. If env var is SET, USE IT
# (create root if missing, fail loud on malformed path).
$bridgeRoot = if ($env:AGENT_BRIDGE_RUNTIME_ROOT) {
    [string]$env:AGENT_BRIDGE_RUNTIME_ROOT
} else {
    Split-Path -Parent $PSScriptRoot
}
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Assert-AgentUuidMatchesIdentityRegistry -RepoRoot $repoRoot
Assert-AgentUuidMatchesProfile -BridgeRoot $bridgeRoot
if (-not (Test-Path -LiteralPath $bridgeRoot -PathType Container)) {
    [void](New-Item -ItemType Directory -Path $bridgeRoot -Force -ErrorAction Stop)
}
$sharedDir = Join-Path $bridgeRoot 'shared'
$outboxDir = Join-Path (Join-Path $bridgeRoot 'outbox') $Agent

if (-not $RunId) {
    $RunId = if ($env:AGENT_BRIDGE_RUN_ID) { [string]$env:AGENT_BRIDGE_RUN_ID } else { '' }
}

$event = [ordered]@{
    ts_utc      = (Get-Date).ToUniversalTime().ToString('o')
    agent       = $Agent
    type        = $Type
    task_id     = $TaskId
    status      = $Status
    severity    = $Severity
    to          = $To
    message     = $Message
    paths       = @($Paths)
    write_scope = @($WriteScope)
    run_id      = $RunId
    pid         = $PID
    cwd         = (Get-Location).Path
    payload     = $payload
}
if ($Role) { $event['role'] = $Role }
if ($AgentUuid) { $event['agent_uuid'] = $AgentUuid }
if ($SessionId) { $event['session_id'] = $SessionId }
if (@($Capabilities).Count -gt 0) { $event['capabilities'] = @($Capabilities) }

function Get-BridgeTargetKey {
    param([AllowNull()] [string] $Targets)
    $targetList = @(Get-BridgeTargetList -Targets $Targets)
    return ($targetList -join ',')
}

function Get-BridgeTargetList {
    param([AllowNull()] [string] $Targets)
    if ([string]::IsNullOrWhiteSpace($Targets)) {
        return @()
    }
    return @(
        $Targets -split ',' |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ } |
            Sort-Object
    )
}

function Test-BridgeSubstantiveTargetActivity {
    param(
        [Parameter(Mandatory)] $SeenEvent,
        [Parameter(Mandatory)] [string] $TargetKey
    )
    $seenAgentKey = Get-BridgeTargetKey -Targets ([string](Get-BridgeObjectField -Object $SeenEvent -Name 'agent'))
    $targetMembers = @(Get-BridgeTargetList -Targets $TargetKey)
    if ((-not $seenAgentKey) -or ($targetMembers -cnotcontains $seenAgentKey)) {
        return $false
    }
    $seenType = [string](Get-BridgeObjectField -Object $SeenEvent -Name 'type')
    $seenStatus = [string](Get-BridgeObjectField -Object $SeenEvent -Name 'status')
    if ($seenType -in @('heartbeat', 'liveness', 'wake_request')) {
        return $false
    }
    if (($seenType -eq 'message') -and ($ackStatuses -contains $seenStatus)) {
        return $false
    }
    return $true
}

function Test-OpenOperatorBridgeFollowNudgeDuplicate {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] $Candidate
    )
    if ([Environment]::GetEnvironmentVariable('AGENT_BRIDGE_ALLOW_DUPLICATE_WAKE_REQUESTS', 'Process') -eq '1') {
        return $false
    }
    if ([string](Get-BridgeObjectField -Object $Candidate -Name 'agent') -cne 'operator') {
        return $false
    }
    if ([string](Get-BridgeObjectField -Object $Candidate -Name 'type') -cne 'wake_request') {
        return $false
    }
    if ([string](Get-BridgeObjectField -Object $Candidate -Name 'status') -cne 'open') {
        return $false
    }
    $candidateTaskId = [string](Get-BridgeObjectField -Object $Candidate -Name 'task_id')
    if (-not $candidateTaskId.StartsWith('bridge-follow-nudge-', [System.StringComparison]::Ordinal)) {
        return $false
    }
    $candidateTargetKey = Get-BridgeTargetKey -Targets ([string](Get-BridgeObjectField -Object $Candidate -Name 'to'))
    if (-not $candidateTargetKey) {
        return $false
    }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }

    try {
        $recentLines = @(Get-Content -LiteralPath $Path -Tail 1000 -Encoding UTF8 -ErrorAction Stop)
    } catch {
        return $false
    }
    for ($i = $recentLines.Count - 1; $i -ge 0; $i--) {
        $text = [string]$recentLines[$i]
        if ([string]::IsNullOrWhiteSpace($text)) { continue }
        try {
            $seen = $text | ConvertFrom-Json -ErrorAction Stop
        } catch {
            continue
        }
        if (Test-BridgeSubstantiveTargetActivity -SeenEvent $seen -TargetKey $candidateTargetKey) {
            return $false
        }
        $seenTargetKey = Get-BridgeTargetKey -Targets ([string](Get-BridgeObjectField -Object $seen -Name 'to'))
        if (
            ([string](Get-BridgeObjectField -Object $seen -Name 'agent') -cne 'operator') -or
            ([string](Get-BridgeObjectField -Object $seen -Name 'type') -cne 'wake_request') -or
            ([string](Get-BridgeObjectField -Object $seen -Name 'status') -cne 'open') -or
            ([string](Get-BridgeObjectField -Object $seen -Name 'task_id') -cne $candidateTaskId) -or
            ($seenTargetKey -cne $candidateTargetKey)
        ) {
            continue
        }
        return $true
    }
    return $false
}

$eventsPath = Join-Path $sharedDir 'events.jsonl'
if (Test-OpenOperatorBridgeFollowNudgeDuplicate -Path $eventsPath -Candidate $event) {
    $event['suppressed_duplicate'] = $true
    $event['suppressed_reason'] = 'open_operator_bridge_follow_nudge_without_target_activity'
    [pscustomobject]$event
    return
}

$line = (($event | ConvertTo-Json -Depth 12 -Compress) + [char]10)

function New-BridgeV1Mutex {
    param(
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [string] $Purpose
    )

    # A fail-closed test hook: forcing construction failure can only deny a
    # write; it can never authorize an unlocked append.
    $forcedFailure = [Environment]::GetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_MUTEX_CONSTRUCTION_FAILURE',
        'Process'
    )
    if ($forcedFailure -in @('All', $Purpose)) {
        throw "simulated bridge $Purpose mutex construction failure"
    }
    return New-Object System.Threading.Mutex($false, $Name)
}

function New-BridgeCanonicalSpoolPaths {
    $spoolDir = Join-Path $bridgeRoot 'spool'
    if (-not (Test-Path -LiteralPath $spoolDir -PathType Container)) {
        [void](New-Item -ItemType Directory -Path $spoolDir -Force)
    }
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfff')
    $nonce = [guid]::NewGuid().ToString('N')
    $finalName = "failed-append-$Agent-$stamp-$PID-$nonce.jsonl"
    return [pscustomobject]@{
        SpoolDir = $spoolDir
        FinalPath = Join-Path $spoolDir $finalName
        PendingPath = Join-Path $spoolDir (".$finalName.pending")
    }
}

function Open-PendingCanonicalWalLease {
    param([Parameter(Mandatory)] [byte[]] $Bytes)

    $paths = New-BridgeCanonicalSpoolPaths
    $lease = $null
    try {
        $lease = New-Object System.IO.FileStream(
            $paths.PendingPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
        if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
            $attributes = [System.IO.File]::GetAttributes($paths.PendingPath)
            [System.IO.File]::SetAttributes(
                $paths.PendingPath,
                ($attributes -bor [System.IO.FileAttributes]::Hidden)
            )
        }
        $lease.Write($Bytes, 0, $Bytes.Length)
        $lease.Flush($true)
        $paths | Add-Member -NotePropertyName Lease -NotePropertyValue $lease
        $lease = $null
        return $paths
    } finally {
        if ($null -ne $lease) { $lease.Dispose() }
    }
}

function Close-PendingCanonicalWalLease {
    param([Parameter(Mandatory)] $Wal)
    if ($null -ne $Wal.Lease) {
        $Wal.Lease.Dispose()
        $Wal.Lease = $null
    }
}

function Promote-PendingCanonicalWal {
    param([Parameter(Mandatory)] $Wal)

    if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
        $attributes = [System.IO.File]::GetAttributes($Wal.PendingPath)
        [System.IO.File]::SetAttributes(
            $Wal.PendingPath,
            ($attributes -band (-bnot [System.IO.FileAttributes]::Hidden))
        )
    }
    [System.IO.File]::Move($Wal.PendingPath, $Wal.FinalPath)
    return $Wal.FinalPath
}

function Invoke-BridgeBeforeAppendTestHook {
    param([Parameter(Mandatory)] [string] $PendingPath)

    $readyPath = [Environment]::GetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_BEFORE_APPEND_READY',
        'Process'
    )
    if (-not $readyPath) { return }
    [System.IO.File]::WriteAllText($readyPath, $PendingPath)
    $releasePath = "$readyPath.release"
    for ($attempt = 0; $attempt -lt 400; $attempt++) {
        if (Test-Path -LiteralPath $releasePath -PathType Leaf) { return }
        Start-Sleep -Milliseconds 25
    }
    throw 'test hook timed out before transactional bridge append'
}

function Initialize-BridgeAppendV1Native {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw (
            'AppendV1 validation checkpoints require Windows file identity ' +
            'and write-through atomic replacement; refusing an unfenced append'
        )
    }
    if ('WaggleDance.BridgeAppendV1Native' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

namespace WaggleDance {
    [StructLayout(LayoutKind.Sequential)]
    public struct BridgeByHandleFileInformation {
        public uint FileAttributes;
        public System.Runtime.InteropServices.ComTypes.FILETIME CreationTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastAccessTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWriteTime;
        public uint VolumeSerialNumber;
        public uint FileSizeHigh;
        public uint FileSizeLow;
        public uint NumberOfLinks;
        public uint FileIndexHigh;
        public uint FileIndexLow;
    }

    public static class BridgeAppendV1Native {
        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool GetFileInformationByHandle(
            IntPtr fileHandle,
            out BridgeByHandleFileInformation fileInformation
        );

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool MoveFileExW(
            string existingPath,
            string destinationPath,
            uint flags
        );
    }
}
'@
}

function Get-BridgeAppendCheckpointPath {
    param([Parameter(Mandatory)] [string] $Path)
    return "$Path.append-v1-validation.json"
}

function Get-BridgeOpenFileIdentity {
    param([Parameter(Mandatory)] [System.IO.FileStream] $Stream)

    Initialize-BridgeAppendV1Native
    $information = New-Object WaggleDance.BridgeByHandleFileInformation
    $handle = $Stream.SafeFileHandle.DangerousGetHandle()
    if (-not [WaggleDance.BridgeAppendV1Native]::GetFileInformationByHandle(
        $handle,
        [ref]$information
    )) {
        $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        $nativeError = New-Object System.ComponentModel.Win32Exception($nativeCode)
        throw "GetFileInformationByHandle failed: $nativeCode ($($nativeError.Message))"
    }
    return ('windows-file-id-v1:{0:x8}:{1:x8}:{2:x8}' -f
        ([uint32]$information.VolumeSerialNumber),
        ([uint32]$information.FileIndexHigh),
        ([uint32]$information.FileIndexLow))
}

function Get-BridgeSha256Hex {
    param(
        [Parameter(Mandatory)] [AllowEmptyCollection()] [byte[]] $Bytes
    )
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($Bytes))).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Test-BridgeExactBytesEqual {
    param(
        [Parameter(Mandatory)] [AllowEmptyCollection()] [byte[]] $Left,
        [Parameter(Mandatory)] [AllowEmptyCollection()] [byte[]] $Right
    )
    if ($Left.Length -ne $Right.Length) { return $false }
    for ($index = 0; $index -lt $Left.Length; $index++) {
        if ($Left[$index] -ne $Right[$index]) { return $false }
    }
    return $true
}

function New-BridgeAppendValidationCheckpointBytes {
    param(
        [Parameter(Mandatory)] [string] $FileIdentity,
        [Parameter(Mandatory)] [int64] $Length,
        [Parameter(Mandatory)] [int64] $AnchorLength,
        [Parameter(Mandatory)] [string] $AnchorSha256
    )

    $checkpoint = [ordered]@{
        schema = 'waggledance.bridge.append-v1-validation'
        version = [int64]1
        file_identity = $FileIdentity
        validated_length = $Length.ToString([Globalization.CultureInfo]::InvariantCulture)
        tail_anchor_length = $AnchorLength.ToString([Globalization.CultureInfo]::InvariantCulture)
        tail_anchor_sha256 = $AnchorSha256
    }
    $checkpointJson = $checkpoint | ConvertTo-Json -Compress
    $strictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
    return ,$strictUtf8.GetBytes($checkpointJson + [char]10)
}

function Get-BridgeAppendTailAnchor {
    param(
        [Parameter(Mandatory)] [System.IO.FileStream] $Stream,
        [Parameter(Mandatory)] [int64] $Length
    )

    if ($Length -lt 0 -or $Length -ne [int64]$Stream.Length) {
        throw 'cannot anchor an inexact canonical bridge length'
    }
    $anchorLength = [int][Math]::Min([int64]4096, $Length)
    [byte[]]$anchorBytes = New-Object byte[] $anchorLength
    if ($anchorLength -gt 0) {
        [void]$Stream.Seek($Length - $anchorLength, [System.IO.SeekOrigin]::Begin)
        $offset = 0
        while ($offset -lt $anchorLength) {
            $read = $Stream.Read($anchorBytes, $offset, $anchorLength - $offset)
            if ($read -le 0) { throw 'canonical bridge tail anchor read ended early' }
            $offset += $read
        }
    }
    return [pscustomobject]@{
        Length = $anchorLength
        Sha256 = Get-BridgeSha256Hex -Bytes $anchorBytes
    }
}

function Write-BridgeValidationTrace {
    param([Parameter(Mandatory)] [string] $Mode)
    $tracePath = [Environment]::GetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_VALIDATION_TRACE',
        'Process'
    )
    if (-not $tracePath) { return }
    $traceEncoding = New-Object System.Text.UTF8Encoding($false, $true)
    [System.IO.File]::AppendAllText($tracePath, ($Mode + [char]10), $traceEncoding)
}

function Read-BridgeAppendValidationCheckpoint {
    param(
        [Parameter(Mandatory)] [string] $CheckpointPath,
        [Parameter(Mandatory)] [System.IO.FileStream] $Stream,
        [Parameter(Mandatory)] [string] $FileIdentity,
        [Parameter(Mandatory)] [int64] $Length
    )

    try {
        if (-not (Test-Path -LiteralPath $CheckpointPath -PathType Leaf)) { return $false }
        $checkpointFile = Get-Item -LiteralPath $CheckpointPath -Force
        if ($checkpointFile.Length -le 0 -or $checkpointFile.Length -gt 8192) { return $false }
        [byte[]]$bytes = [System.IO.File]::ReadAllBytes($CheckpointPath)
        if (
            $bytes.Length -eq 0 -or $bytes[$bytes.Length - 1] -ne 10 -or
            ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and
                $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
        ) { return $false }
        for ($index = 0; $index -lt $bytes.Length - 1; $index++) {
            if ($bytes[$index] -eq 10 -or $bytes[$index] -eq 13) { return $false }
        }
        $strictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
        $text = $strictUtf8.GetString($bytes, 0, $bytes.Length - 1)
        $checkpoint = $text | ConvertFrom-Json -ErrorAction Stop
        if (-not ($checkpoint -is [System.Management.Automation.PSCustomObject])) { return $false }
        $expectedProperties = @(
            'schema', 'version', 'file_identity', 'validated_length',
            'tail_anchor_length', 'tail_anchor_sha256'
        )
        $actualProperties = @($checkpoint.PSObject.Properties)
        if ($actualProperties.Count -ne $expectedProperties.Count) { return $false }
        foreach ($expectedProperty in $expectedProperties) {
            if (@($actualProperties | Where-Object { $_.Name -ceq $expectedProperty }).Count -ne 1) {
                return $false
            }
        }
        if (
            -not ($checkpoint.schema -is [string]) -or
            [string]$checkpoint.schema -cne 'waggledance.bridge.append-v1-validation' -or
            -not (($checkpoint.version -is [int]) -or
                ($checkpoint.version -is [long])) -or
            [int64]$checkpoint.version -ne 1 -or
            -not ($checkpoint.file_identity -is [string]) -or
            [string]$checkpoint.file_identity -cne $FileIdentity -or
            -not ($checkpoint.validated_length -is [string]) -or
            -not ($checkpoint.tail_anchor_length -is [string]) -or
            -not ($checkpoint.tail_anchor_sha256 -is [string])
        ) { return $false }
        $validatedLengthText = [string]$checkpoint.validated_length
        $anchorLengthText = [string]$checkpoint.tail_anchor_length
        if (
            $validatedLengthText -cnotmatch '^(0|[1-9][0-9]*)$' -or
            $anchorLengthText -cnotmatch '^(0|[1-9][0-9]*)$'
        ) { return $false }
        $validatedLength = [int64]0
        $anchorLength = [int64]0
        if (
            -not [int64]::TryParse($validatedLengthText, [ref]$validatedLength) -or
            -not [int64]::TryParse($anchorLengthText, [ref]$anchorLength) -or
            $validatedLength -ne $Length -or
            $anchorLength -ne [Math]::Min([int64]4096, $Length)
        ) { return $false }
        $anchorHash = [string]$checkpoint.tail_anchor_sha256
        if ($anchorHash -cnotmatch '^[0-9a-f]{64}$') { return $false }
        [byte[]]$canonicalCheckpointBytes = `
            New-BridgeAppendValidationCheckpointBytes `
                -FileIdentity $FileIdentity -Length $validatedLength `
                -AnchorLength $anchorLength -AnchorSha256 $anchorHash
        if (-not (Test-BridgeExactBytesEqual `
            -Left $bytes -Right $canonicalCheckpointBytes)) {
            return $false
        }
        $actualAnchor = Get-BridgeAppendTailAnchor -Stream $Stream -Length $Length
        return (
            [int64]$actualAnchor.Length -eq $anchorLength -and
            [string]$actualAnchor.Sha256 -ceq $anchorHash
        )
    } catch {
        # The checkpoint is only a cache. Any read, parse, identity, or anchor
        # problem must miss and force the authoritative full validation path.
        return $false
    }
}

function Write-BridgeAppendValidationCheckpoint {
    param(
        [Parameter(Mandatory)] [string] $CheckpointPath,
        [Parameter(Mandatory)] [System.IO.FileStream] $Stream,
        [Parameter(Mandatory)] [string] $FileIdentity,
        [Parameter(Mandatory)] [int64] $Length,
        [Parameter(Mandatory)] [ValidateSet('Bootstrap','AfterCanonical')]
        [string] $Phase
    )

    $forcedFailure = [Environment]::GetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_CHECKPOINT_UPDATE_FAILURE',
        'Process'
    )
    if ($forcedFailure -in @('All', $Phase)) {
        throw "simulated validation checkpoint update failure during $Phase"
    }
    if (
        $forcedFailure -ceq "${Phase}Once" -and
        -not $script:bridgeCheckpointFailureInjected
    ) {
        $script:bridgeCheckpointFailureInjected = $true
        throw "simulated one-shot validation checkpoint update failure during $Phase"
    }
    if ($Length -ne [int64]$Stream.Length) {
        throw 'canonical bridge length changed before checkpoint advance'
    }
    $currentIdentity = Get-BridgeOpenFileIdentity -Stream $Stream
    if ($currentIdentity -cne $FileIdentity) {
        throw 'canonical bridge file identity changed before checkpoint advance'
    }
    $anchor = Get-BridgeAppendTailAnchor -Stream $Stream -Length $Length
    [byte[]]$checkpointBytes = New-BridgeAppendValidationCheckpointBytes `
        -FileIdentity $FileIdentity -Length $Length `
        -AnchorLength ([int64]$anchor.Length) `
        -AnchorSha256 ([string]$anchor.Sha256)
    $temporaryPath = "$CheckpointPath.tmp.$PID.$([guid]::NewGuid().ToString('N'))"
    $temporaryStream = $null
    try {
        $temporaryStream = New-Object System.IO.FileStream(
            $temporaryPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
        $temporaryStream.Write($checkpointBytes, 0, $checkpointBytes.Length)
        $temporaryStream.Flush($true)
        $temporaryStream.Dispose()
        $temporaryStream = $null

        Initialize-BridgeAppendV1Native
        if (-not [WaggleDance.BridgeAppendV1Native]::MoveFileExW(
            $temporaryPath,
            $CheckpointPath,
            [uint32]0x00000009
        )) {
            $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            $nativeError = New-Object System.ComponentModel.Win32Exception($nativeCode)
            throw "write-through checkpoint replacement failed: $nativeCode ($($nativeError.Message))"
        }
        [byte[]]$publishedBytes = [System.IO.File]::ReadAllBytes($CheckpointPath)
        if (
            $publishedBytes.Length -ne $checkpointBytes.Length -or
            (Get-BridgeSha256Hex -Bytes $publishedBytes) -cne
                (Get-BridgeSha256Hex -Bytes $checkpointBytes)
        ) {
            throw 'published validation checkpoint verification failed'
        }
    } finally {
        if ($null -ne $temporaryStream) { $temporaryStream.Dispose() }
        if (Test-Path -LiteralPath $temporaryPath -PathType Leaf) {
            Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        }
    }
}

function Assert-BridgeAppendTargetStrictUtf8 {
    param(
        [Parameter(Mandatory)] [System.IO.FileStream] $Stream,
        [Parameter(Mandatory)] [string] $Path
    )

    if ($Stream.Length -eq 0) { return }
    [void]$Stream.Seek(-1, [System.IO.SeekOrigin]::End)
    if ($Stream.ReadByte() -ne 10) {
        throw "bridge append target has an unterminated row: $Path"
    }

    [void]$Stream.Seek(0, [System.IO.SeekOrigin]::Begin)
    $decoder = (New-Object System.Text.UTF8Encoding($false, $true)).GetDecoder()
    $buffer = New-Object byte[] 8192
    $characters = New-Object char[] 8192
    try {
        while ($true) {
            $read = $Stream.Read($buffer, 0, $buffer.Length)
            if ($read -eq 0) { break }
            $flush = ($Stream.Position -eq $Stream.Length)
            $offset = 0
            do {
                $bytesUsed = 0
                $charactersUsed = 0
                $completed = $false
                $decoder.Convert(
                    $buffer,
                    $offset,
                    $read - $offset,
                    $characters,
                    0,
                    $characters.Length,
                    $flush,
                    [ref]$bytesUsed,
                    [ref]$charactersUsed,
                    [ref]$completed
                )
                if ($bytesUsed -eq 0 -and -not $completed) {
                    throw 'strict UTF-8 decoder made no progress'
                }
                $offset += $bytesUsed
            } while ($offset -lt $read -or ($flush -and -not $completed))
        }
    } catch {
        throw "bridge append target is not strict UTF-8: $Path ($($_.Exception.Message))"
    }
}

function Assert-BridgeAppendTargetValidated {
    param(
        [Parameter(Mandatory)] [System.IO.FileStream] $Stream,
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $CheckpointPath,
        [Parameter(Mandatory)] [string] $FileIdentity,
        [Parameter(Mandatory)] [int64] $Length
    )

    if (Read-BridgeAppendValidationCheckpoint `
        -CheckpointPath $CheckpointPath -Stream $Stream `
        -FileIdentity $FileIdentity -Length $Length) {
        Write-BridgeValidationTrace -Mode 'checkpoint'
        return
    }
    if ([Environment]::GetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_FAIL_ON_FULL_VALIDATION',
        'Process'
    ) -eq '1') {
        throw 'simulated refusal of full canonical validation'
    }
    Write-BridgeValidationTrace -Mode 'full'
    Assert-BridgeAppendTargetStrictUtf8 -Stream $Stream -Path $Path
    Write-BridgeAppendValidationCheckpoint `
        -CheckpointPath $CheckpointPath -Stream $Stream `
        -FileIdentity $FileIdentity -Length $Length -Phase Bootstrap
}

function Invoke-BridgeAfterCanonicalTestHook {
    $readyPath = [Environment]::GetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_AFTER_CANONICAL_BEFORE_CHECKPOINT',
        'Process'
    )
    if (-not $readyPath) { return }
    [System.IO.File]::WriteAllText($readyPath, 'ready')
    $releasePath = "$readyPath.release"
    for ($attempt = 0; $attempt -lt 400; $attempt++) {
        if (Test-Path -LiteralPath $releasePath -PathType Leaf) { return }
        Start-Sleep -Milliseconds 25
    }
    throw 'test hook timed out after canonical flush before checkpoint advance'
}

function Invoke-BridgeCanonicalTransactionalAppend {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [byte[]] $Bytes,
        [Parameter(Mandatory)] [bool] $AppendMutexOwned
    )

    if (-not $AppendMutexOwned) {
        throw 'refusing transactional bridge append without AppendV1 ownership'
    }
    # Gate the platform before creating shared/ or opening/creating the
    # canonical path. On unsupported platforms the already-durable pending WAL
    # may be retained, but no canonical path or parent is touched.
    Initialize-BridgeAppendV1Native
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        [void](New-Item -ItemType Directory -Path $parent -Force)
    }
    $stream = $null
    $preAppendLength = [int64]0
    try {
        $stream = New-Object System.IO.FileStream(
            $Path,
            [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::Read
        )
        $preAppendLength = [int64]$stream.Length
        $checkpointPath = Get-BridgeAppendCheckpointPath -Path $Path
        $fileIdentity = Get-BridgeOpenFileIdentity -Stream $stream
        Assert-BridgeAppendTargetValidated `
            -Stream $stream -Path $Path -CheckpointPath $checkpointPath `
            -FileIdentity $fileIdentity -Length $preAppendLength
        [void]$stream.Seek($preAppendLength, [System.IO.SeekOrigin]::Begin)
        try {
            $forcedCountText = [Environment]::GetEnvironmentVariable(
                'AGENT_BRIDGE_TEST_APPEND_FAILURE_AFTER_BYTES',
                'Process'
            )
            if ($forcedCountText) {
                $forcedCount = 0
                if (-not [int]::TryParse($forcedCountText, [ref]$forcedCount)) {
                    throw 'invalid test partial-append byte count'
                }
                if ($forcedCount -lt 0 -or $forcedCount -ge $Bytes.Length) {
                    throw 'test partial-append byte count is outside the row'
                }
                if ($forcedCount -gt 0) {
                    $stream.Write($Bytes, 0, $forcedCount)
                }
                throw 'simulated transactional append failure after partial write'
            }
            $stream.Write($Bytes, 0, $Bytes.Length)
            $stream.Flush($true)
        } catch {
            $appendError = $_.Exception.Message
            try {
                $stream.SetLength($preAppendLength)
                $stream.Flush($true)
            } catch {
                throw (
                    "transactional bridge append failed ($appendError); " +
                    "ROLLBACK FAILED: $($_.Exception.Message)"
                )
            }
            throw "transactional bridge append failed and rolled back: $appendError"
        }
        # The canonical bytes are durable before the checkpoint can advance.
        # If this process dies or the checkpoint write fails now, the stale
        # exact-length binding misses on the next attempt and the pending WAL
        # is retained for exact-row replay dedup. Never roll durable bytes back
        # merely because this non-authoritative cache could not advance.
        $checkpointError = ''
        try {
            Invoke-BridgeAfterCanonicalTestHook
            Write-BridgeAppendValidationCheckpoint `
                -CheckpointPath $checkpointPath -Stream $stream `
                -FileIdentity $fileIdentity `
                -Length ([int64]($preAppendLength + $Bytes.Length)) `
                -Phase AfterCanonical
        } catch {
            $checkpointError = $_.Exception.Message
        }
        return [pscustomobject]@{
            PreAppendLength = $preAppendLength
            AppendedLength = [int64]$Bytes.Length
            CanonicalDurable = $true
            CheckpointAdvanced = (-not $checkpointError)
            CheckpointError = $checkpointError
        }
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
    }
}

$script:bridgeCheckpointFailureInjected = $false
$script:bridgeWalCleanupFailureInjected = $false

function Add-CanonicalLineWithWal {
    param([Parameter(Mandatory)] [string] $Line)

    # DEPLOYMENT FENCE: every direct canonical writer must acquire AppendV1 and
    # use this pending-WAL/checkpoint protocol before this path is enabled in
    # production. A bypass writer can invalidate the cache, but cannot provide
    # the same crash/ordering guarantees while racing this critical section.
    $strictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
    [byte[]]$lineBytes = $strictUtf8.GetBytes($Line)
    if ($lineBytes.Length -eq 0 -or $lineBytes[$lineBytes.Length - 1] -ne 10) {
        throw 'bridge WAL row must be non-empty strict UTF-8 ending in LF'
    }
    $mutex = $null
    $acquired = $false
    $dirtyAbandoned = $false
    $Path = $eventsPath
    $wal = Open-PendingCanonicalWalLease -Bytes $lineBytes
    $mutexError = ''
    try {
        try {
            $mutex = New-BridgeV1Mutex `
                -Name 'Global\WaggleDanceBridgeAppendV1' -Purpose Append
            if ($null -eq $mutex) { throw 'bridge append mutex construction returned null' }
            try { $acquired = $mutex.WaitOne(10000) }
            catch [System.Threading.AbandonedMutexException] {
                $acquired = $true
                $dirtyAbandoned = $true
            }
        } catch {
            $mutexError = $_.Exception.Message
        }

        if (-not $acquired -or $dirtyAbandoned) {
            if (-not $mutexError) { $mutexError = 'bridge append mutex timeout' }
            if ($dirtyAbandoned) {
                $mutexError = 'AppendV1 was abandoned; dirty ownership cannot mutate canonical bytes'
            }
            Close-PendingCanonicalWalLease -Wal $wal
            try { $spoolPath = Promote-PendingCanonicalWal -Wal $wal }
            catch {
                throw (
                    "could not acquire clean AppendV1 ($mutexError); pending WAL " +
                    "retained at $($wal.PendingPath): $($_.Exception.Message)"
                )
            }
            throw (
                "could not acquire clean AppendV1 for bridge event: $Path " +
                "(reason: $mutexError; event durably spooled to $spoolPath)"
            )
        }

        try {
            Close-PendingCanonicalWalLease -Wal $wal
            Invoke-BridgeBeforeAppendTestHook -PendingPath $wal.PendingPath
            $appendResult = Invoke-BridgeCanonicalTransactionalAppend `
                -Path $Path -Bytes $lineBytes -AppendMutexOwned $acquired
        } catch {
            $appendError = $_.Exception.Message
            try { Close-PendingCanonicalWalLease -Wal $wal }
            catch {
                throw (
                    "bridge append failed ($appendError); WAL lease could not " +
                    "close and pending WAL was retained at $($wal.PendingPath): " +
                    "$($_.Exception.Message)"
                )
            }
            try {
                $spoolPath = Promote-PendingCanonicalWal -Wal $wal
            } catch {
                throw (
                    "bridge append failed ($appendError); WAL promotion failed " +
                    "and pending WAL was retained at $($wal.PendingPath): " +
                    "$($_.Exception.Message)"
                )
            }
            throw "bridge append failed; WAL promoted to $spoolPath ($appendError)"
        }

        if (-not $appendResult.CheckpointAdvanced) {
            # Flush(true) already made the canonical row durable. Preserve the
            # redundant WAL for exact replay dedup, but do not misreport the
            # event as failed and invite a blind caller retry/duplicate.
            $retainedPath = $wal.PendingPath
            try { $retainedPath = Promote-PendingCanonicalWal -Wal $wal }
            catch {
                # The closed pending WAL is still durable and discoverable by
                # the replayer. Promotion is a liveness aid, not authorization
                # to claim the already-durable canonical append failed.
                $retainedPath = $wal.PendingPath
            }
            Write-Warning -WarningAction Continue -Message (
                'canonical bridge append is durable; validation checkpoint ' +
                "advance failed and redundant WAL was retained at $retainedPath " +
                "($($appendResult.CheckpointError))"
            )
            return
        }

        try {
            $cleanupFailureMode = [Environment]::GetEnvironmentVariable(
                'AGENT_BRIDGE_TEST_WAL_CLEANUP_FAILURE',
                'Process'
            )
            if (
                $cleanupFailureMode -eq '1' -or
                ($cleanupFailureMode -ceq 'Once' -and
                    -not $script:bridgeWalCleanupFailureInjected)
            ) {
                $script:bridgeWalCleanupFailureInjected = $true
                throw 'simulated durable WAL cleanup failure'
            }
            [System.IO.File]::Delete($wal.PendingPath)
            if (Test-Path -LiteralPath $wal.PendingPath) {
                throw 'pending WAL still exists after removal'
            }
        } catch {
            $cleanupError = $_.Exception.Message
            $spoolPath = $wal.PendingPath
            try { $spoolPath = Promote-PendingCanonicalWal -Wal $wal } catch {}
            Write-Warning -WarningAction Continue -Message (
                "bridge append is durable but WAL cleanup failed; retained at " +
                "$spoolPath ($cleanupError)"
            )
            return
        }
        return
    } finally {
        if ($null -ne $wal -and $null -ne $wal.Lease) {
            $wal.Lease.Dispose()
            $wal.Lease = $null
        }
        if ($null -ne $mutex) {
            if ($acquired) {
                try { $mutex.ReleaseMutex() }
                catch {
                    Write-Warning -WarningAction Continue -Message (
                        "canonical AppendV1 release failed: $($_.Exception.Message)"
                    )
                }
            }
            $mutex.Dispose()
        }
    }
}

function Invoke-BridgeAuxiliaryTransactionalAppend {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [byte[]] $Bytes,
        [Parameter(Mandatory)] [bool] $AppendMutexOwned
    )

    if (-not $AppendMutexOwned) {
        throw 'refusing auxiliary bridge append without AppendV1 ownership'
    }
    if ($Bytes.Length -eq 0 -or $Bytes[$Bytes.Length - 1] -ne 10) {
        throw 'auxiliary bridge row must be non-empty strict UTF-8 ending in LF'
    }
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        [void](New-Item -ItemType Directory -Path $parent -Force)
    }
    $stream = $null
    $preAppendLength = [int64]0
    try {
        $stream = New-Object System.IO.FileStream(
            $Path,
            [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::Read
        )
        $preAppendLength = [int64]$stream.Length
        if ($preAppendLength -gt 0) {
            [void]$stream.Seek(-1, [System.IO.SeekOrigin]::End)
            if ($stream.ReadByte() -ne 10) {
                throw 'auxiliary bridge append target has an unterminated row'
            }
        }
        [void]$stream.Seek($preAppendLength, [System.IO.SeekOrigin]::Begin)
        try {
            $forcedCountText = [Environment]::GetEnvironmentVariable(
                'AGENT_BRIDGE_TEST_AUXILIARY_APPEND_FAILURE_AFTER_BYTES',
                'Process'
            )
            if ($forcedCountText) {
                $forcedCount = 0
                if (-not [int]::TryParse($forcedCountText, [ref]$forcedCount)) {
                    throw 'invalid test auxiliary partial-append byte count'
                }
                if ($forcedCount -lt 0 -or $forcedCount -ge $Bytes.Length) {
                    throw 'test auxiliary partial-append byte count is outside the row'
                }
                if ($forcedCount -gt 0) {
                    $stream.Write($Bytes, 0, $forcedCount)
                }
                throw 'simulated auxiliary append failure after partial write'
            }
            $stream.Write($Bytes, 0, $Bytes.Length)
            $stream.Flush($true)
        } catch {
            $appendError = $_.Exception.Message
            try {
                $stream.SetLength($preAppendLength)
                $stream.Flush($true)
            } catch {
                throw (
                    "auxiliary append failed ($appendError); rollback failed: " +
                    $_.Exception.Message
                )
            }
            throw "auxiliary append failed and rolled back: $appendError"
        }
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
    }
}

function Add-AuxiliaryLineBestEffort {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $Line
    )

    try {
        $strictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
        [byte[]]$lineBytes = $strictUtf8.GetBytes($Line)
        if ($lineBytes.Length -eq 0 -or $lineBytes[$lineBytes.Length - 1] -ne 10) {
            throw 'auxiliary bridge row must be non-empty strict UTF-8 ending in LF'
        }
    } catch {
        Write-Warning -WarningAction Continue -Message (
            "auxiliary outbox row was skipped: $($_.Exception.Message)"
        )
        return
    }

    $mutex = $null
    $acquired = $false
    $dirtyAbandoned = $false
    $mutexError = ''
    try {
        try {
            $mutex = New-BridgeV1Mutex `
                -Name 'Global\WaggleDanceBridgeAppendV1' -Purpose AppendAuxiliary
            if ($null -eq $mutex) { throw 'auxiliary append mutex construction returned null' }
            try { $acquired = $mutex.WaitOne(10000) }
            catch [System.Threading.AbandonedMutexException] {
                $acquired = $true
                $dirtyAbandoned = $true
            }
        } catch {
            $mutexError = $_.Exception.Message
        }
        if (-not $acquired -or $dirtyAbandoned) {
            if (-not $mutexError) { $mutexError = 'AppendV1 timeout' }
            if ($dirtyAbandoned) {
                $mutexError = 'AppendV1 was abandoned; dirty ownership cannot mutate the outbox'
            }
            Write-Warning -WarningAction Continue -Message (
                "auxiliary outbox append was skipped: $mutexError"
            )
            return
        }
        try {
            Invoke-BridgeAuxiliaryTransactionalAppend `
                -Path $Path -Bytes $lineBytes -AppendMutexOwned $acquired
        } catch {
            Write-Warning -WarningAction Continue -Message (
                'canonical bridge event is durable; auxiliary outbox append ' +
                "was skipped: $($_.Exception.Message)"
            )
            return
        }
    } finally {
        if ($null -ne $mutex) {
            if ($acquired) {
                try { $mutex.ReleaseMutex() }
                catch {
                    Write-Warning -WarningAction Continue -Message (
                        "auxiliary AppendV1 release failed: $($_.Exception.Message)"
                    )
                }
            }
            try { $mutex.Dispose() }
            catch {
                Write-Warning -WarningAction Continue -Message (
                    "auxiliary AppendV1 dispose failed: $($_.Exception.Message)"
                )
            }
        }
    }
}

function Write-JsonAtomic {
    # Internal review fix A7/S4 (2026-05-09, simplified 2026-05-09):
    # Earlier File.Move + File.Replace dance failed reliably under
    # write contention with the WARNING surfacing on every event.
    # Move-Item -Force on Windows uses MoveFileEx with
    # MOVEFILE_REPLACE_EXISTING which is atomic on NTFS same-volume
    # and handles both the create and the replace path in one call.
    # Set-Content was the original problem (truncate-then-write was
    # non-atomic); Move-Item over a written-in-full temp keeps the
    # "reader sees old or new, never torn" property.
    param([Parameter(Mandatory)] [string] $Path, [Parameter(Mandatory)] [string] $Json)
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent)) {
        [void](New-Item -ItemType Directory -Path $parent -Force)
    }
    $tmp = "$Path.tmp.$PID.$([guid]::NewGuid().ToString('N'))"
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($tmp, $Json, $encoding)
    for ($i = 0; $i -lt 20; $i++) {
        try {
            Move-Item -LiteralPath $tmp -Destination $Path -Force -ErrorAction Stop
            return
        } catch {
            Start-Sleep -Milliseconds (25 + ($i * 10))
        }
    }
    try { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue } catch {}
    throw "could not atomically replace last-event file: $Path"
}

$dateName = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd') + '.jsonl'
$outboxPath = Join-Path $outboxDir $dateName
# Only shared/events.jsonl owns replay WAL and validation-checkpoint state.
# Once it is durable, the per-agent outbox is an auxiliary best-effort copy:
# its failure must not invite a blind retry of the canonical event.
Add-CanonicalLineWithWal -Line $line
Add-AuxiliaryLineBestEffort -Path $outboxPath -Line $line

$lastPath = Join-Path $sharedDir ("last_{0}.json" -f $Agent)
try {
    Write-JsonAtomic -Path $lastPath -Json ($event | ConvertTo-Json -Depth 12)
} catch {
    # last_<agent>.json is an optimization for quick status reads; the
    # canonical bridge record is already appended to shared/events.jsonl
    # and the per-agent outbox above. Do not fail the event write because
    # Windows had the last-file open during atomic replace.
    Write-Warning $_.Exception.Message
}

[pscustomobject]$event
