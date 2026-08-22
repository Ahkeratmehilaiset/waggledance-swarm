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
    [string] $PayloadJson = '{}',
    [switch] $ReceiptJson
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

function Write-BridgeWarning {
    param([Parameter(Mandatory)] [string] $Message)
    # -ReceiptJson is a machine-readable stdout contract. Diagnostics that are
    # material to delivery are carried inside warning_messages instead.
    if (-not $ReceiptJson) {
        Write-Warning -WarningAction Continue -Message $Message
    }
}

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

# R13: honor AGENT_BRIDGE_RUNTIME_ROOT. If env var is SET, USE IT and fail
# loudly on malformed paths. The accepted-WAL directory lease chain creates a
# missing root component-by-component so an ancestor junction cannot redirect
# eager recursive creation.
$bridgeRoot = if ($env:AGENT_BRIDGE_RUNTIME_ROOT) {
    [string]$env:AGENT_BRIDGE_RUNTIME_ROOT
} else {
    Split-Path -Parent $PSScriptRoot
}
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Assert-AgentUuidMatchesIdentityRegistry -RepoRoot $repoRoot
Assert-AgentUuidMatchesProfile -BridgeRoot $bridgeRoot
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

function New-BridgeDeliveryReceipt {
    param(
        [Parameter(Mandatory)] [ValidateSet('canonical','queued','suppressed')]
        [string] $DeliveryStatus,
        [Parameter(Mandatory)] [bool] $CanonicalDurable,
        [bool] $CheckpointAdvanced = $false,
        [string] $WalId = '',
        [string] $WalLeaf = '',
        [string] $RetainedWalPath = '',
        [string] $RetainedWalSha256 = '',
        [string[]] $WarningMessages = @()
    )

    return [pscustomobject][ordered]@{
        schema = 'waggledance.bridge.delivery-receipt.v1'
        accepted = $true
        delivery_status = $DeliveryStatus
        canonical_durable = $CanonicalDurable
        events_path = $eventsPath
        checkpoint_advanced = $CheckpointAdvanced
        wal_id = if ($WalId) { $WalId } else { $null }
        wal_leaf = if ($WalLeaf) { $WalLeaf } else { $null }
        retained_wal_path = if ($RetainedWalPath) { $RetainedWalPath } else { $null }
        retained_wal_sha256 = if ($RetainedWalSha256) { $RetainedWalSha256 } else { $null }
        outbox_written = $false
        last_file_written = $false
        warning_messages = @($WarningMessages)
    }
}

function Write-BridgeEventResult {
    param([Parameter(Mandatory)] $Delivery)

    $event['_bridge_delivery'] = $Delivery
    if ($ReceiptJson) {
        $event | ConvertTo-Json -Depth 16 -Compress
    } else {
        [pscustomobject]$event
    }
}

if (Test-OpenOperatorBridgeFollowNudgeDuplicate -Path $eventsPath -Candidate $event) {
    $event['suppressed_duplicate'] = $true
    $event['suppressed_reason'] = 'open_operator_bridge_follow_nudge_without_target_activity'
    $suppressedDelivery = New-BridgeDeliveryReceipt `
        -DeliveryStatus suppressed -CanonicalDurable $false
    Write-BridgeEventResult -Delivery $suppressedDelivery
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

function Open-BridgeAcceptedQueueDirectoryLease {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $Label
    )

    Initialize-BridgeAppendV1Native
    $handle = [WaggleDance.BridgeAppendV1Native]::CreateFileW(
        [System.IO.Path]::GetFullPath($Path),
        [uint32]2147483648,
        [uint32]3,
        [IntPtr]::Zero,
        [uint32]3,
        [uint32]35651584,
        [IntPtr]::Zero
    )
    if ($handle.IsInvalid) {
        $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        $handle.Dispose()
        $nativeError = New-Object System.ComponentModel.Win32Exception($nativeCode)
        throw "$Label lease failed: $nativeCode ($($nativeError.Message))"
    }
    try {
        $information = New-Object WaggleDance.BridgeByHandleFileInformation
        if (-not [WaggleDance.BridgeAppendV1Native]::GetFileInformationByHandle(
            $handle.DangerousGetHandle(),
            [ref]$information
        )) {
            $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            $nativeError = New-Object System.ComponentModel.Win32Exception($nativeCode)
            throw "$Label identity failed: $nativeCode ($($nativeError.Message))"
        }
        if (
            ($information.FileAttributes -band
                [uint32][System.IO.FileAttributes]::Directory) -eq 0 -or
            ($information.FileAttributes -band
                [uint32][System.IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            throw "$Label must be a plain directory, not a reparse point"
        }
        return $handle
    } catch {
        $handle.Dispose()
        throw
    }
}

function Close-BridgeAcceptedQueueDirectoryLeases {
    param([AllowNull()] $Leases)

    if ($null -eq $Leases) { return }
    if ($Leases -isnot [System.Collections.IList]) {
        $Leases = @($Leases)
    }
    for ($index = $Leases.Count - 1; $index -ge 0; $index--) {
        if ($null -ne $Leases[$index]) {
            try { $Leases[$index].Dispose() }
            catch {
                Write-BridgeWarning -Message (
                    "bridge directory lease close failed: " +
                    $_.Exception.Message
                )
            }
        }
    }
}

function Open-BridgePlainDirectoryChain {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $Label
    )

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $pathRoot = [System.IO.Path]::GetPathRoot($fullPath)
    if (-not $pathRoot) {
        throw "$Label path is not rooted: $Path"
    }
    $directoryPaths = New-Object 'System.Collections.Generic.List[string]'
    $directoryPaths.Add($pathRoot)
    $cursor = $pathRoot
    $relativePath = $fullPath.Substring($pathRoot.Length)
    foreach ($segment in @($relativePath -split '[\\/]' | Where-Object { $_ })) {
        $cursor = Join-Path $cursor $segment
        $directoryPaths.Add($cursor)
    }
    $directoryLeases = New-Object 'System.Collections.Generic.List[object]'
    try {
        foreach ($directory in $directoryPaths) {
            if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
                [void](New-Item -ItemType Directory -Path $directory -ErrorAction Stop)
            }
            $directoryLeases.Add((Open-BridgeAcceptedQueueDirectoryLease `
                -Path $directory -Label "$Label $directory"))
        }
        return @($directoryLeases.ToArray())
    } catch {
        Close-BridgeAcceptedQueueDirectoryLeases -Leases $directoryLeases
        throw
    }
}

function New-BridgeCanonicalSpoolPaths {
    $spoolDir = Join-Path $bridgeRoot 'spool'
    $acceptedDir = Join-Path $spoolDir 'accepted-v1'
    $pendingDir = Join-Path $acceptedDir 'pending'
    $readyDir = Join-Path $acceptedDir 'ready'
    $quarantineDir = Join-Path $acceptedDir 'quarantine'
    $replayedDir = Join-Path $acceptedDir 'replayed'
    $fullBridgeRoot = [System.IO.Path]::GetFullPath($bridgeRoot)
    $pathRoot = [System.IO.Path]::GetPathRoot($fullBridgeRoot)
    $directoryPaths = New-Object 'System.Collections.Generic.List[string]'
    $directoryPaths.Add($pathRoot)
    $cursor = $pathRoot
    $relativeRoot = $fullBridgeRoot.Substring($pathRoot.Length)
    foreach ($segment in @($relativeRoot -split '[\\/]' | Where-Object { $_ })) {
        $cursor = Join-Path $cursor $segment
        $directoryPaths.Add($cursor)
    }
    foreach ($directory in @(
        $spoolDir,
        $acceptedDir,
        $pendingDir,
        $readyDir,
        $quarantineDir,
        $replayedDir
    )) {
        $directoryPaths.Add([System.IO.Path]::GetFullPath($directory))
    }
    $directoryLeases = New-Object 'System.Collections.Generic.List[object]'
    $seenDirectories = New-Object 'System.Collections.Generic.HashSet[string]' `
        ([System.StringComparer]::OrdinalIgnoreCase)
    try {
        foreach ($directory in $directoryPaths) {
            $fullDirectory = [System.IO.Path]::GetFullPath($directory)
            if (-not $seenDirectories.Add($fullDirectory)) { continue }
            if (-not (Test-Path -LiteralPath $fullDirectory -PathType Container)) {
                [void](New-Item -ItemType Directory `
                    -Path $fullDirectory -ErrorAction Stop)
            }
            $directoryLeases.Add((Open-BridgeAcceptedQueueDirectoryLease `
                -Path $fullDirectory -Label "accepted queue directory $fullDirectory"))
        }
    } catch {
        Close-BridgeAcceptedQueueDirectoryLeases -Leases $directoryLeases
        throw
    }
    $walId = [guid]::NewGuid().ToString('N')
    $finalName = "bridge-wal-v1-$walId.jsonl"
    return [pscustomobject]@{
        SpoolDir = $spoolDir
        AcceptedDir = $acceptedDir
        PendingDir = $pendingDir
        ReadyDir = $readyDir
        QuarantineDir = $quarantineDir
        ReplayedDir = $replayedDir
        DirectoryLeases = @($directoryLeases.ToArray())
        WalId = $walId
        WalLeaf = $finalName
        FinalPath = Join-Path $readyDir $finalName
        PendingPath = Join-Path $pendingDir $finalName
    }
}

function Open-PendingCanonicalWalLease {
    param([Parameter(Mandatory)] [byte[]] $Bytes)

    $paths = New-BridgeCanonicalSpoolPaths
    $lease = $null
    $keepDirectoryLeases = $false
    try {
        $lease = New-Object System.IO.FileStream(
            $paths.PendingPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
        $lease.Write($Bytes, 0, $Bytes.Length)
        $lease.Flush($true)
        if ([int64]$lease.Length -ne [int64]$Bytes.Length) {
            throw 'durable pending WAL length changed during writeback verification'
        }
        [void]$lease.Seek(0, [System.IO.SeekOrigin]::Begin)
        [byte[]]$verifiedBytes = New-Object byte[] $Bytes.Length
        $verifiedOffset = 0
        while ($verifiedOffset -lt $verifiedBytes.Length) {
            $verifiedRead = $lease.Read(
                $verifiedBytes,
                $verifiedOffset,
                $verifiedBytes.Length - $verifiedOffset
            )
            if ($verifiedRead -le 0) {
                throw 'durable pending WAL ended during writeback verification'
            }
            $verifiedOffset += $verifiedRead
        }
        if ([Environment]::GetEnvironmentVariable(
            'AGENT_BRIDGE_TEST_PENDING_WRITEBACK_FAILURE',
            'Process'
        ) -eq '1') {
            throw 'simulated durable pending WAL writeback verification failure'
        }
        if (-not (Test-BridgeExactBytesEqual -Left $verifiedBytes -Right $Bytes)) {
            throw 'durable pending WAL writeback verification mismatch'
        }
        $paths | Add-Member -NotePropertyName Bytes -NotePropertyValue $Bytes
        $paths | Add-Member -NotePropertyName Sha256 -NotePropertyValue (
            Get-BridgeSha256Hex -Bytes $Bytes
        )
        $paths | Add-Member -NotePropertyName Lease -NotePropertyValue $lease
        $paths | Add-Member -NotePropertyName MarkerPath -NotePropertyValue (
            Join-Path $paths.ReadyDir (
                ".{0}.pending-recovery-blocked" -f $paths.WalLeaf
            )
        )
        [void](Ensure-BridgeAcceptedWalDigestMarker -Wal $paths)
        $lease = $null
        $keepDirectoryLeases = $true
        return $paths
    } catch {
        $openError = $_.Exception.Message
        if ($null -ne $lease) {
            try { $lease.Dispose() } catch {}
            $lease = $null
        }
        $excludedPath = ''
        if (Test-Path -LiteralPath $paths.PendingPath -PathType Leaf) {
            try {
                if (-not (Test-Path -LiteralPath $paths.QuarantineDir -PathType Container)) {
                    [void](New-Item -ItemType Directory `
                        -Path $paths.QuarantineDir -Force -ErrorAction Stop)
                }
                $excludedPath = Join-Path $paths.QuarantineDir `
                    ("unaccepted-{0}" -f $paths.WalLeaf)
                $forcedExclusion = [Environment]::GetEnvironmentVariable(
                    'AGENT_BRIDGE_TEST_UNACCEPTED_EXCLUSION_FAILURE',
                    'Process'
                )
                $excluded = if ($forcedExclusion -in @('All', 'Move')) {
                    $false
                } else {
                    [WaggleDance.BridgeAppendV1Native]::MoveFileExW(
                        $paths.PendingPath,
                        $excludedPath,
                        [uint32]0x00000008
                    )
                }
                if (-not $excluded) {
                    $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
                    throw "write-through exclusion failed with Win32 error $nativeCode"
                }
            } catch {
                $exclusionError = $_.Exception.Message
                try {
                    if ([Environment]::GetEnvironmentVariable(
                        'AGENT_BRIDGE_TEST_UNACCEPTED_EXCLUSION_FAILURE',
                        'Process'
                    ) -in @('All', 'Delete')) {
                        throw 'simulated unaccepted pending delete failure'
                    }
                    Remove-Item -LiteralPath $paths.PendingPath `
                        -Force -ErrorAction Stop
                } catch {}
                if (Test-Path -LiteralPath $paths.PendingPath) {
                    throw (
                        'pending WAL acceptance is unknown and blind retry is unsafe at ' +
                        "$($paths.PendingPath): $openError; exclusion failed: " +
                        $exclusionError
                    )
                }
            }
        }
        throw (
            'could not establish an accepted pending bridge WAL; candidate was ' +
            "excluded from automatic replay at ${excludedPath}: $openError"
        )
    } finally {
        if ($null -ne $lease) { $lease.Dispose() }
        if (-not $keepDirectoryLeases) {
            Close-BridgeAcceptedQueueDirectoryLeases `
                -Leases $paths.DirectoryLeases
        }
    }
}

function Close-PendingCanonicalWalLease {
    param([Parameter(Mandatory)] $Wal)
    if ($null -ne $Wal.Lease) {
        $Wal.Lease.Dispose()
        $Wal.Lease = $null
    }
}

function Test-BridgeAcceptedWalDigestMarker {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] $Wal
    )

    try {
        $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
        if (
            $item.PSIsContainer -or
            ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            return $false
        }
        [byte[]]$markerBytes = [System.IO.File]::ReadAllBytes($Path)
        $strictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
        $markerText = $strictUtf8.GetString($markerBytes)
        $marker = $markerText | ConvertFrom-Json -ErrorAction Stop
        return (
            $marker -is [System.Management.Automation.PSCustomObject] -and
            [string]$marker.schema -ceq
                'waggledance.bridge.accepted-pending-block.v1' -and
            [string]$marker.wal_leaf -ceq [string]$Wal.WalLeaf -and
            [string]$marker.expected_sha256 -ceq [string]$Wal.Sha256
        )
    } catch {
        return $false
    }
}

function Ensure-BridgeAcceptedWalDigestMarker {
    param([Parameter(Mandatory)] $Wal)

    Initialize-BridgeAppendV1Native
    $markerPath = Join-Path $Wal.ReadyDir (
        ".{0}.pending-recovery-blocked" -f $Wal.WalLeaf
    )
    if (Test-Path -LiteralPath $markerPath) {
        if (Test-BridgeAcceptedWalDigestMarker -Path $markerPath -Wal $Wal) {
            return $markerPath
        }
        throw 'accepted WAL digest marker collides with different authority'
    }
    if (-not (Test-Path -LiteralPath $Wal.QuarantineDir -PathType Container)) {
        [void](New-Item -ItemType Directory `
            -Path $Wal.QuarantineDir -Force -ErrorAction Stop)
    }
    $temporaryPath = Join-Path $Wal.QuarantineDir (
        'digest-marker-{0}.tmp' -f [guid]::NewGuid().ToString('N')
    )
    $marker = [ordered]@{
        schema = 'waggledance.bridge.accepted-pending-block.v1'
        wal_leaf = [string]$Wal.WalLeaf
        expected_sha256 = [string]$Wal.Sha256
        created_at_utc = [DateTime]::UtcNow.ToString('o')
    } | ConvertTo-Json -Compress
    [byte[]]$markerBytes = (
        New-Object System.Text.UTF8Encoding($false, $true)
    ).GetBytes($marker + [string][char]10)
    $stream = $null
    try {
        $stream = New-Object System.IO.FileStream(
            $temporaryPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
        $stream.Write($markerBytes, 0, $markerBytes.Length)
        $stream.Flush($true)
        $stream.Dispose()
        $stream = $null
        $markerPublished = [WaggleDance.BridgeAppendV1Native]::MoveFileExW(
            $temporaryPath,
            $markerPath,
            [uint32]0x00000008
        )
        if (-not $markerPublished) {
            # Last-error state is thread-local and may be overwritten by any
            # filesystem probe. Preserve the MoveFileExW failure before
            # checking whether a concurrent writer published the same marker.
            $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            if (Test-BridgeAcceptedWalDigestMarker -Path $markerPath -Wal $Wal) {
                return $markerPath
            }
            throw "write-through accepted WAL digest publication failed with Win32 error $nativeCode"
        }
        return $markerPath
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
        if (Test-Path -LiteralPath $temporaryPath -PathType Leaf) {
            Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        }
    }
}

function Promote-PendingCanonicalWal {
    param([Parameter(Mandatory)] $Wal)

    Initialize-BridgeAppendV1Native
    try {
        [void](Ensure-BridgeAcceptedWalDigestMarker -Wal $Wal)
    } catch {
        throw (
            'accepted WAL digest authority is unavailable; queued success is ' +
            "unsafe even if pending bytes remain: $($_.Exception.Message)"
        )
    }
    $forcedPublicationFailure = [Environment]::GetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_ACCEPTED_PUBLICATION_FAILURE',
        'Process'
    ) -in @('1', $Wal.WalLeaf)
    if ([Environment]::GetEnvironmentVariable(
        'AGENT_BRIDGE_TEST_ACCEPTED_PUBLICATION_COLLISION',
        'Process'
    ) -in @('1', $Wal.WalLeaf)) {
        [System.IO.File]::WriteAllBytes(
            $Wal.FinalPath,
            [byte[]](98, 97, 100, 10)
        )
    }
    $published = if ($forcedPublicationFailure) {
        $false
    } else {
        [WaggleDance.BridgeAppendV1Native]::MoveFileExW(
            $Wal.PendingPath,
            $Wal.FinalPath,
            [uint32]0x00000008
        )
    }
    if (-not $published) {
        $nativeCode = if ($forcedPublicationFailure) {
            5
        } else {
            [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        }
        $recoveryPath = Get-BridgeExactWalRecoveryPath -Wal $Wal
        if ($recoveryPath) {
            return $recoveryPath
        }
        $nativeError = New-Object System.ComponentModel.Win32Exception($nativeCode)
        throw "write-through accepted WAL publication lost recovery state: $nativeCode ($($nativeError.Message))"
    }
    # The exact bytes and digest were verified through the exclusive pending
    # lease before it closed. The write-through, no-replace rename is the
    # publication/acceptance point; a consumer may archive ready immediately.
    return $Wal.FinalPath
}

function Get-BridgeWalRecoveryPath {
    param([Parameter(Mandatory)] $Wal)

    if (Test-Path -LiteralPath $Wal.FinalPath -PathType Leaf) {
        return $Wal.FinalPath
    }
    if (Test-Path -LiteralPath $Wal.PendingPath -PathType Leaf) {
        return $Wal.PendingPath
    }
    $replayedPath = Join-Path $Wal.ReplayedDir $Wal.WalLeaf
    if (Test-Path -LiteralPath $replayedPath -PathType Leaf) {
        return $replayedPath
    }
    return ''
}

function Test-BridgeExactWalRecoveryCandidate {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] $Wal
    )

    Initialize-BridgeAppendV1Native
    $handle = [WaggleDance.BridgeAppendV1Native]::CreateFileW(
        $Path,
        [uint32]2147483648,
        [uint32]1,
        [IntPtr]::Zero,
        [uint32]3,
        [uint32]2097152,
        [IntPtr]::Zero
    )
    if ($handle.IsInvalid) {
        $handle.Dispose()
        return $false
    }
    $stream = $null
    try {
        $information = New-Object WaggleDance.BridgeByHandleFileInformation
        if (-not [WaggleDance.BridgeAppendV1Native]::GetFileInformationByHandle(
            $handle.DangerousGetHandle(),
            [ref]$information
        )) {
            return $false
        }
        if (
            $information.NumberOfLinks -ne 1 -or
            ($information.FileAttributes -band
                [uint32][System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
            ($information.FileAttributes -band
                [uint32][System.IO.FileAttributes]::Directory) -ne 0
        ) {
            return $false
        }
        $stream = New-Object System.IO.FileStream(
            $handle,
            [System.IO.FileAccess]::Read
        )
        $handle = $null
        if ([int64]$stream.Length -ne [int64]$Wal.Bytes.Length) {
            return $false
        }
        [byte[]]$candidateBytes = New-Object byte[] $Wal.Bytes.Length
        $offset = 0
        while ($offset -lt $candidateBytes.Length) {
            $read = $stream.Read(
                $candidateBytes,
                $offset,
                $candidateBytes.Length - $offset
            )
            if ($read -le 0) { return $false }
            $offset += $read
        }
        return (
            (Test-BridgeExactBytesEqual -Left $candidateBytes -Right $Wal.Bytes) -and
            (Get-BridgeSha256Hex -Bytes $candidateBytes) -ceq [string]$Wal.Sha256
        )
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
        if ($null -ne $handle) { $handle.Dispose() }
    }
}

function Get-BridgeExactWalRecoveryPath {
    param([Parameter(Mandatory)] $Wal)

    $replayedPath = Join-Path $Wal.ReplayedDir $Wal.WalLeaf
    foreach ($candidatePath in @(
        $Wal.PendingPath,
        $Wal.FinalPath,
        $replayedPath
    )) {
        if (Test-BridgeExactWalRecoveryCandidate -Path $candidatePath -Wal $Wal) {
            return $candidatePath
        }
    }
    return ''
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
using Microsoft.Win32.SafeHandles;

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
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern SafeFileHandle CreateFileW(
            string fileName,
            uint desiredAccess,
            uint shareMode,
            IntPtr securityAttributes,
            uint creationDisposition,
            uint flagsAndAttributes,
            IntPtr templateFile
        );

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

function Open-BridgePlainSingleLinkMutationStream {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $Label
    )

    Initialize-BridgeAppendV1Native
    $handle = [WaggleDance.BridgeAppendV1Native]::CreateFileW(
        $Path,
        [uint32]3221225472,
        [uint32]1,
        [IntPtr]::Zero,
        [uint32]4,
        [uint32]2097280,
        [IntPtr]::Zero
    )
    if ($handle.IsInvalid) {
        $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        $handle.Dispose()
        $nativeError = New-Object System.ComponentModel.Win32Exception($nativeCode)
        throw "$Label open failed: $nativeCode ($($nativeError.Message))"
    }
    try {
        $information = New-Object WaggleDance.BridgeByHandleFileInformation
        if (-not [WaggleDance.BridgeAppendV1Native]::GetFileInformationByHandle(
            $handle.DangerousGetHandle(),
            [ref]$information
        )) {
            $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            $nativeError = New-Object System.ComponentModel.Win32Exception($nativeCode)
            throw "$Label identity failed: $nativeCode ($($nativeError.Message))"
        }
        if (
            $information.NumberOfLinks -ne 1 -or
            ($information.FileAttributes -band
                [uint32][System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
            ($information.FileAttributes -band
                [uint32][System.IO.FileAttributes]::Directory) -ne 0
        ) {
            throw "$Label must be a plain single-link file"
        }
        $stream = New-Object System.IO.FileStream(
            $handle,
            [System.IO.FileAccess]::ReadWrite
        )
        $handle = $null
        return $stream
    } finally {
        if ($null -ne $handle) { $handle.Dispose() }
    }
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
    # Gate the platform before opening or creating the canonical leaf. The
    # caller already holds the plain canonical-parent chain acquired after its
    # pre-acceptance native gate and clean AppendV1 ownership.
    Initialize-BridgeAppendV1Native
    $stream = $null
    $preAppendLength = [int64]0
    try {
        $stream = Open-BridgePlainSingleLinkMutationStream `
            -Path $Path -Label 'canonical bridge append target'
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
    # Platform capability is a pre-acceptance gate. An unsupported runtime
    # must not leave a WAL that no local accepted-v1 drainer can consume.
    Initialize-BridgeAppendV1Native
    $publicationMutex = $null
    $publicationAcquired = $false
    $publicationDirtyAbandoned = $false
    $mutex = $null
    $acquired = $false
    $dirtyAbandoned = $false
    $Path = $eventsPath
    $wal = $null
    try {
        try {
            $publicationMutex = New-BridgeV1Mutex `
                -Name 'Global\WaggleDanceBridgeAcceptedQueuePublicationV1' `
                -Purpose QueuePublication
            if ($null -eq $publicationMutex) {
                throw 'accepted queue publication mutex construction returned null'
            }
            try { $publicationAcquired = $publicationMutex.WaitOne(10000) }
            catch [System.Threading.AbandonedMutexException] {
                $publicationAcquired = $true
                $publicationDirtyAbandoned = $true
            }
        } catch {
            throw (
                'could not acquire accepted queue publication fence before WAL ' +
                "creation: $($_.Exception.Message)"
            )
        }
        if (-not $publicationAcquired) {
            throw 'accepted queue publication fence timed out before WAL creation'
        }
        if ($publicationDirtyAbandoned) {
            throw (
                'accepted queue publication fence was abandoned; refusing WAL ' +
                'creation under dirty ownership'
            )
        }

        # Publication ownership begins before the producer creates accepted-v1
        # state and remains held until canonical delivery or queue publication
        # and cleanup have reached a settled durable outcome.
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
                $recoveryPath = Get-BridgeWalRecoveryPath -Wal $wal
                throw (
                    "could not acquire clean AppendV1 ($mutexError); accepted WAL " +
                    "publication was not verified; recovery_path=${recoveryPath}: " +
                    "$($_.Exception.Message)"
                )
            }
            $warning = (
                "clean AppendV1 unavailable for bridge event; accepted WAL " +
                "queued at $spoolPath (reason: $mutexError)"
            )
            Write-BridgeWarning -Message $warning
            return New-BridgeDeliveryReceipt `
                -DeliveryStatus queued -CanonicalDurable $false `
                -WalId $wal.WalId -WalLeaf $wal.WalLeaf `
                -RetainedWalPath $spoolPath -RetainedWalSha256 $wal.Sha256 `
                -WarningMessages @($warning)
        }

        try {
            Close-PendingCanonicalWalLease -Wal $wal
            $canonicalDirectoryLeases = @(Open-BridgePlainDirectoryChain `
                -Path (Split-Path -Parent $Path) `
                -Label 'canonical bridge directory')
            $wal.DirectoryLeases = @($wal.DirectoryLeases) + $canonicalDirectoryLeases
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
                $recoveryPath = Get-BridgeWalRecoveryPath -Wal $wal
                throw (
                    "bridge append failed ($appendError); WAL promotion failed " +
                    "or could not be verified; recovery_path=${recoveryPath}: " +
                    "$($_.Exception.Message)"
                )
            }
            $warning = "bridge append failed; accepted WAL queued at $spoolPath ($appendError)"
            Write-BridgeWarning -Message $warning
            return New-BridgeDeliveryReceipt `
                -DeliveryStatus queued -CanonicalDurable $false `
                -WalId $wal.WalId -WalLeaf $wal.WalLeaf `
                -RetainedWalPath $spoolPath -RetainedWalSha256 $wal.Sha256 `
                -WarningMessages @($warning)
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
                $retainedPath = Get-BridgeExactWalRecoveryPath -Wal $wal
            }
            Write-BridgeWarning -Message (
                'canonical bridge append is durable; validation checkpoint ' +
                "advance failed and redundant WAL was retained at $retainedPath " +
                "($($appendResult.CheckpointError))"
            )
            return New-BridgeDeliveryReceipt `
                -DeliveryStatus canonical -CanonicalDurable $true `
                -CheckpointAdvanced $false -WalId $wal.WalId `
                -WalLeaf $wal.WalLeaf -RetainedWalPath $retainedPath `
                -RetainedWalSha256 $wal.Sha256 `
                -WarningMessages @($appendResult.CheckpointError)
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
            [System.IO.File]::Delete($wal.MarkerPath)
            if (Test-Path -LiteralPath $wal.MarkerPath) {
                throw 'accepted WAL digest marker still exists after removal'
            }
            [System.IO.File]::Delete($wal.PendingPath)
            if (Test-Path -LiteralPath $wal.PendingPath) {
                throw 'pending WAL still exists after removal'
            }
        } catch {
            $cleanupError = $_.Exception.Message
            $spoolPath = Get-BridgeExactWalRecoveryPath -Wal $wal
            try { $spoolPath = Promote-PendingCanonicalWal -Wal $wal }
            catch {
                $spoolPath = Get-BridgeExactWalRecoveryPath -Wal $wal
            }
            Write-BridgeWarning -Message (
                "bridge append is durable but WAL cleanup failed; retained at " +
                "$spoolPath ($cleanupError)"
            )
            return New-BridgeDeliveryReceipt `
                -DeliveryStatus canonical -CanonicalDurable $true `
                -CheckpointAdvanced $true -WalId $wal.WalId `
                -WalLeaf $wal.WalLeaf -RetainedWalPath $spoolPath `
                -RetainedWalSha256 $wal.Sha256 `
                -WarningMessages @($cleanupError)
        }
        return New-BridgeDeliveryReceipt `
            -DeliveryStatus canonical -CanonicalDurable $true `
            -CheckpointAdvanced $true
        } finally {
            if ($null -ne $wal -and $null -ne $wal.Lease) {
                $wal.Lease.Dispose()
                $wal.Lease = $null
            }
            if ($null -ne $mutex) {
                if ($acquired) {
                    try { $mutex.ReleaseMutex() }
                    catch {
                        Write-BridgeWarning -Message (
                            "canonical AppendV1 release failed: $($_.Exception.Message)"
                        )
                    }
                }
                try { $mutex.Dispose() }
                catch {
                    Write-BridgeWarning -Message (
                        "canonical AppendV1 dispose failed: $($_.Exception.Message)"
                    )
                }
            }
            if ($null -ne $wal -and $wal.PSObject.Properties['DirectoryLeases']) {
                Close-BridgeAcceptedQueueDirectoryLeases `
                    -Leases $wal.DirectoryLeases
                $wal.DirectoryLeases = @()
            }
        }
    } finally {
        if ($null -ne $publicationMutex) {
            if ($publicationAcquired) {
                try { $publicationMutex.ReleaseMutex() }
                catch {
                    Write-BridgeWarning -Message (
                        'accepted queue publication fence release failed: ' +
                        $_.Exception.Message
                    )
                }
            }
            try { $publicationMutex.Dispose() }
            catch {
                Write-BridgeWarning -Message (
                    'accepted queue publication fence dispose failed: ' +
                    $_.Exception.Message
                )
            }
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
        $stream = Open-BridgePlainSingleLinkMutationStream `
            -Path $Path -Label 'auxiliary bridge append target'
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
        Write-BridgeWarning -Message (
            "auxiliary outbox row was skipped: $($_.Exception.Message)"
        )
        return $false
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
            Write-BridgeWarning -Message (
                "auxiliary outbox append was skipped: $mutexError"
            )
            return $false
        }
        try {
            Invoke-BridgeAuxiliaryTransactionalAppend `
                -Path $Path -Bytes $lineBytes -AppendMutexOwned $acquired
        } catch {
            Write-BridgeWarning -Message (
                'canonical bridge event is durable; auxiliary outbox append ' +
                "was skipped: $($_.Exception.Message)"
            )
            return $false
        }
    } finally {
        if ($null -ne $mutex) {
            if ($acquired) {
                try { $mutex.ReleaseMutex() }
                catch {
                    Write-BridgeWarning -Message (
                        "auxiliary AppendV1 release failed: $($_.Exception.Message)"
                    )
                }
            }
            try { $mutex.Dispose() }
            catch {
                Write-BridgeWarning -Message (
                    "auxiliary AppendV1 dispose failed: $($_.Exception.Message)"
                )
            }
        }
    }
    return $true
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
$delivery = Add-CanonicalLineWithWal -Line $line
if ($delivery.delivery_status -ceq 'canonical') {
    $delivery.outbox_written = [bool](
        Add-AuxiliaryLineBestEffort -Path $outboxPath -Line $line
    )

    $lastPath = Join-Path $sharedDir ("last_{0}.json" -f $Agent)
    try {
        Write-JsonAtomic -Path $lastPath -Json ($event | ConvertTo-Json -Depth 12)
        $delivery.last_file_written = $true
    } catch {
        # last_<agent>.json is an optimization for quick status reads; the
        # canonical bridge record is already appended to shared/events.jsonl
        # and the per-agent outbox above. Do not fail the event write because
        # Windows had the last-file open during atomic replace.
        Write-BridgeWarning -Message $_.Exception.Message
    }
}

Write-BridgeEventResult -Delivery $delivery
