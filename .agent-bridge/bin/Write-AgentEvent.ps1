#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $Agent,
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
    [string] $InternalStaleLeaseArchivePath = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$sessionIdentity = Join-Path $PSScriptRoot 'AgentBridgeSessionIdentity.ps1'
. $sessionIdentity

function ConvertTo-BridgeInvariantUtcText {
    param([object] $Value)

    if ($null -eq $Value) { return '' }
    if ($Value -is [DateTimeOffset]) {
        return ([DateTimeOffset]$Value).ToUniversalTime().ToString(
            'o',
            [System.Globalization.CultureInfo]::InvariantCulture
        )
    }
    if ($Value -is [DateTime]) {
        return ([DateTime]$Value).ToUniversalTime().ToString(
            'o',
            [System.Globalization.CultureInfo]::InvariantCulture
        )
    }
    return [string]$Value
}

function Get-BridgeStrictGenerationText {
    param(
        [Parameter(Mandatory)] $Record,
        [Parameter(Mandatory)] [string] $Field,
        [Parameter(Mandatory)] [string] $Context
    )

    $property = Get-AgentBridgeExactProperty `
        -InputObject $Record `
        -Name $Field
    if (-not $property) {
        # Legacy claims may predate one or more generation fields. Missing is
        # a stable value, but an explicitly present non-string value is not.
        return ''
    }
    if ($property.Value -isnot [string]) {
        throw (
            "internal system stale_lease {0} generation field '{1}' must be a string" -f
            $Context,
            $Field
        )
    }
    return [string]$property.Value
}

# Stale-claim sweeping emits the existing internal `system` release event.
# The exception is bound to the canonical sweep caller and to the archived
# claim it just removed; public arguments alone never grant system identity.
$hasInternalStaleLeaseShape = (
    $Agent -ceq 'system' -and
    $Type -ceq 'release' -and
    $Status -ceq 'stale_lease' -and
    -not [string]::IsNullOrWhiteSpace($TaskId)
)
$expectedStaleSweepPath = [System.IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot 'Invoke-StaleClaimSweep.ps1')
)
$callerPath = if ($MyInvocation.PSCommandPath) {
    [System.IO.Path]::GetFullPath([string]$MyInvocation.PSCommandPath)
} else {
    ''
}
$hasCanonicalStaleSweepCaller = (
    $hasInternalStaleLeaseShape -and
    [string]::Equals(
        $callerPath,
        $expectedStaleSweepPath,
        [System.StringComparison]::OrdinalIgnoreCase
    )
)
$isInternalStaleLeaseRelease = $false
$internalStaleLeaseMutationLock = $null
try {
if ($hasCanonicalStaleSweepCaller) {
    if ([string]::IsNullOrWhiteSpace($InternalStaleLeaseArchivePath)) {
        throw 'internal system stale_lease release requires an archived claim proof'
    }

    $proofBridgeRoot = Resolve-AgentBridgeRoot `
        -DefaultRoot (Split-Path -Parent $PSScriptRoot)
    $internalStaleLeaseMutationLock = Enter-AgentBridgeMutationLock `
        -BridgeRoot $proofBridgeRoot
    $proofDoneDir = [System.IO.Path]::GetFullPath(
        (Join-Path $proofBridgeRoot 'work_queue\done')
    )
    $proofArchivePath = [System.IO.Path]::GetFullPath(
        $InternalStaleLeaseArchivePath
    )
    $proofArchiveParent = [System.IO.Path]::GetDirectoryName($proofArchivePath)
    if (-not [string]::Equals(
            $proofArchiveParent,
            $proofDoneDir,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -or
        -not $proofArchivePath.EndsWith(
            '.stale_lease.json',
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw 'internal system stale_lease archive proof must be under work_queue/done'
    }
    if (-not (Test-Path -LiteralPath $proofArchivePath -PathType Leaf)) {
        throw 'internal system stale_lease archive proof does not exist'
    }

    try {
        $proofClaim = ConvertFrom-AgentBridgeJson -Json (
            Read-AgentBridgeStrictUtf8JsonText `
                -LiteralPath $proofArchivePath
        )
    } catch {
        throw 'internal system stale_lease archive proof is not valid JSON'
    }
    if ($null -eq $proofClaim -or $proofClaim -isnot [pscustomobject]) {
        throw 'internal system stale_lease archive proof does not match the released task'
    }
    $proofTaskIdProperty = Get-AgentBridgeExactProperty `
        -InputObject $proofClaim `
        -Name 'task_id'
    $proofReleaseStatusProperty = Get-AgentBridgeExactProperty `
        -InputObject $proofClaim `
        -Name 'release_status'
    $proofReleasedAtProperty = Get-AgentBridgeExactProperty `
        -InputObject $proofClaim `
        -Name 'released_at_utc'
    if (
        $null -eq $proofTaskIdProperty -or
        $proofTaskIdProperty.Value -isnot [string] -or
        [string]$proofTaskIdProperty.Value -cne $TaskId -or
        $null -eq $proofReleaseStatusProperty -or
        $proofReleaseStatusProperty.Value -isnot [string] -or
        [string]$proofReleaseStatusProperty.Value -cne 'stale_lease' -or
        $null -eq $proofReleasedAtProperty -or
        $proofReleasedAtProperty.Value -isnot [string] -or
        [string]::IsNullOrWhiteSpace(
            [string]$proofReleasedAtProperty.Value
        )
    ) {
        throw 'internal system stale_lease archive proof does not match the released task'
    }

    $proofGeneration = [ordered]@{}
    foreach ($generationField in @(
            'claimed_at_utc',
            'run_id',
            'owner_session_id',
            'owner_token_sha256'
        )) {
        $proofGeneration[$generationField] = (
            Get-BridgeStrictGenerationText `
                -Record $proofClaim `
                -Field $generationField `
                -Context 'archive proof'
        )
    }

    try {
        $proofPayload = ConvertFrom-AgentBridgeJson -Json (
            [string]$PayloadJson
        )
    } catch {
        throw 'internal system stale_lease payload must be valid JSON'
    }
    if ($null -eq $proofPayload -or $proofPayload -isnot [pscustomobject]) {
        throw 'internal system stale_lease payload must be valid JSON'
    }
    $payloadArchivePathProperty = Get-AgentBridgeExactProperty `
        -InputObject $proofPayload `
        -Name 'archived_path'
    if (
        $null -eq $payloadArchivePathProperty -or
        $payloadArchivePathProperty.Value -isnot [string] -or
        [string]::IsNullOrWhiteSpace(
            [string]$payloadArchivePathProperty.Value
        )
    ) {
        throw 'internal system stale_lease payload must identify the archived claim proof'
    }
    $payloadArchivePath = [System.IO.Path]::GetFullPath(
        [string]$payloadArchivePathProperty.Value
    )
    if (-not [string]::Equals(
            $payloadArchivePath,
            $proofArchivePath,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw 'internal system stale_lease payload archive does not match its proof'
    }
    foreach ($requiredGenerationField in @(
            'claim_claimed_at_utc',
            'claim_run_id',
            'archive_released_at_utc',
            'archive_state_semantics'
        )) {
        if ($null -eq (
                Get-AgentBridgeExactProperty `
                    -InputObject $proofPayload `
                    -Name $requiredGenerationField
            )) {
            throw 'internal system stale_lease payload must identify the archived claim generation'
        }
    }
    $proofClaimedAtUtc = [string]$proofGeneration['claimed_at_utc']
    $proofRunId = [string]$proofGeneration['run_id']
    $payloadClaimedAtUtc = Get-BridgeStrictGenerationText `
        -Record $proofPayload `
        -Field 'claim_claimed_at_utc' `
        -Context 'payload'
    if ($payloadClaimedAtUtc -cne $proofClaimedAtUtc) {
        throw 'internal system stale_lease payload claimed_at generation does not match its proof'
    }
    $payloadRunId = Get-BridgeStrictGenerationText `
        -Record $proofPayload `
        -Field 'claim_run_id' `
        -Context 'payload'
    if ($payloadRunId -cne $proofRunId) {
        throw 'internal system stale_lease payload run_id generation does not match its proof'
    }
    $proofReleasedAtUtc = ConvertTo-BridgeInvariantUtcText `
        -Value $proofReleasedAtProperty.Value
    $payloadReleasedAtProperty = Get-AgentBridgeExactProperty `
        -InputObject $proofPayload `
        -Name 'archive_released_at_utc'
    if ($payloadReleasedAtProperty.Value -isnot [string]) {
        throw 'internal system stale_lease payload released_at generation must be a string'
    }
    $payloadReleasedAtUtc = ConvertTo-BridgeInvariantUtcText `
        -Value $payloadReleasedAtProperty.Value
    if ($payloadReleasedAtUtc -cne $proofReleasedAtUtc) {
        throw 'internal system stale_lease payload released_at generation does not match its proof'
    }
    $payloadArchiveStateProperty = Get-AgentBridgeExactProperty `
        -InputObject $proofPayload `
        -Name 'archive_state_semantics'
    if (
        $payloadArchiveStateProperty.Value -isnot [string] -or
        [string]$payloadArchiveStateProperty.Value -cne
        'verified_before_event_append') {
        throw 'internal system stale_lease payload semantics do not match its proof'
    }

    $proofClaimsDir = Join-Path $proofBridgeRoot 'work_queue\claims'
    if (-not (Test-Path -LiteralPath $proofClaimsDir -PathType Container)) {
        throw 'internal system stale_lease release cannot verify active claims'
    }
    try {
        $activeClaimFiles = @(
            Get-ChildItem -LiteralPath $proofClaimsDir `
                -Filter '*.json' -Force -ErrorAction Stop |
                Sort-Object FullName
        )
    } catch {
        throw 'internal system stale_lease release cannot verify active claims'
    }
    foreach ($activeClaimFile in $activeClaimFiles) {
        if (-not (
                Test-Path -LiteralPath $activeClaimFile.FullName -PathType Leaf
            )) {
            throw 'internal system stale_lease release cannot verify active claims'
        }
        try {
            $activeClaimJson = Read-AgentBridgeStrictUtf8JsonText `
                -LiteralPath $activeClaimFile.FullName
            $activeClaim = ConvertFrom-AgentBridgeJson -Json (
                $activeClaimJson
            )
        } catch {
            throw 'internal system stale_lease release cannot verify active claims'
        }
        if (
            $null -eq $activeClaim -or
            $activeClaim -isnot [pscustomobject] -or
            $null -eq (
                $activeTaskIdProperty = Get-AgentBridgeExactProperty `
                    -InputObject $activeClaim `
                    -Name 'task_id'
            ) -or
            $activeTaskIdProperty.Value -isnot [string] -or
            [string]::IsNullOrEmpty(
                [string]$activeTaskIdProperty.Value
            )
        ) {
            throw 'internal system stale_lease release cannot verify active claims'
        }
        if ([string]$activeTaskIdProperty.Value -cne $TaskId) {
            continue
        }

        $sameGeneration = $true
        foreach ($generationField in @(
                'claimed_at_utc',
                'run_id',
                'owner_session_id',
                'owner_token_sha256'
            )) {
            $activeGenerationText = Get-BridgeStrictGenerationText `
                -Record $activeClaim `
                -Field $generationField `
                -Context 'active claim'
            if (
                $activeGenerationText -cne
                [string]$proofGeneration[$generationField]
            ) {
                $sameGeneration = $false
            }
        }
        if ($sameGeneration) {
            throw 'internal system stale_lease release conflicts with an active claim'
        }
    }

    $isInternalStaleLeaseRelease = $true
}
if ($Agent -ceq 'system' -and -not $isInternalStaleLeaseRelease) {
    throw 'identity_mismatch: system agent is reserved for the verified stale-claim sweep'
}
Assert-AgentBridgeSessionIdentity `
    -RequestedAgent $Agent `
    -AllowInternalStaleLeaseRelease:$isInternalStaleLeaseRelease

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
        if (($target -cnotmatch '^[a-z][a-z0-9_-]{1,32}\z') -and ($allowedNonAgentTargets -cnotcontains $target)) {
            throw "to contains invalid bridge agent id: $target"
        }
    }
}

if ($isInternalStaleLeaseRelease) {
    if ($RunId -or $Role -or $AgentUuid -or $SessionId -or @($Capabilities).Count -gt 0) {
        throw 'internal system stale_lease release must not carry agent identity metadata'
    }
    $RunId = ''
    $Role = ''
    $AgentUuid = ''
    $SessionId = ''
    $Capabilities = @()
} else {
    if (-not $RunId) {
        $RunId = if ($env:AGENT_BRIDGE_RUN_ID) {
            [string]$env:AGENT_BRIDGE_RUN_ID
        } else {
            ''
        }
    }
    $Role = Resolve-BridgeMetadataString -Explicit $Role -EnvName 'AGENT_BRIDGE_ROLE'
    $AgentUuid = Resolve-BridgeMetadataString -Explicit $AgentUuid -EnvName 'AGENT_BRIDGE_AGENT_UUID'
    $SessionId = Resolve-BridgeMetadataString -Explicit $SessionId -EnvName 'AGENT_BRIDGE_SESSION_ID'
    $Capabilities = @(Resolve-BridgeCapabilities -Explicit $Capabilities)

    if (-not $SessionId -and $RunId) {
        $SessionId = $RunId
    }
}

Assert-NoPrivateMarker -Label 'run_id' -Value $RunId
Assert-NoPrivateMarker -Label 'role' -Value $Role
Assert-NoPrivateMarker -Label 'agent_uuid' -Value $AgentUuid
Assert-NoPrivateMarker -Label 'session_id' -Value $SessionId
Assert-NoPrivateMarker -Label 'capabilities' -Value $Capabilities

if ($RunId -and $RunId -notmatch '^[A-Za-z0-9._:-]{1,128}\z') {
    throw "run_id must match ^[A-Za-z0-9._:-]{1,128}$"
}
if ($Role -and $Role -cnotmatch '^[a-z][a-z0-9_-]{1,32}\z') {
    throw "role must match ^[a-z][a-z0-9_-]{1,32}$"
}
if ($AgentUuid -and $AgentUuid -notmatch '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\z') {
    throw "agent_uuid must be a UUID"
}
if ($AgentUuid) {
    $AgentUuid = $AgentUuid.ToLowerInvariant()
}
if ($SessionId -and $SessionId -notmatch '^[A-Za-z0-9._:-]{1,128}\z') {
    throw "session_id must match ^[A-Za-z0-9._:-]{1,128}$"
}
foreach ($capability in @($Capabilities)) {
    if ($capability -cnotmatch '^[a-z][a-z0-9_.:-]{1,64}\z') {
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
$fullGitShaPattern = '^[0-9a-f]{40}\z'
$bridgeTaskBindingPattern = '^[A-Za-z0-9._/-]{1,180}\z'
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
    $payload = ConvertFrom-AgentBridgeJson -Json $payloadText
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
    if ($expectedUuidText -cnotmatch '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\z') {
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
    if ($expectedUuidText -cnotmatch '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\z') {
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
$bridgeRoot = Resolve-AgentBridgeRoot `
    -DefaultRoot (Split-Path -Parent $PSScriptRoot)
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Assert-AgentUuidMatchesIdentityRegistry -RepoRoot $repoRoot
Ensure-AgentBridgePlainDirectory `
    -LiteralPath $bridgeRoot `
    -Context 'bridge root'
$agentsDir = Join-Path $bridgeRoot 'agents'
if (Test-Path -LiteralPath $agentsDir) {
    Assert-AgentBridgePlainDirectory `
        -LiteralPath $agentsDir `
        -Context 'bridge agent profile directory'
}
Assert-AgentUuidMatchesProfile -BridgeRoot $bridgeRoot
$sharedDir = Join-Path $bridgeRoot 'shared'
$outboxRoot = Join-Path $bridgeRoot 'outbox'
$outboxDir = Join-Path $outboxRoot $Agent
Ensure-AgentBridgePlainDirectory `
    -LiteralPath $sharedDir `
    -Context 'bridge shared event directory'
Ensure-AgentBridgePlainDirectory `
    -LiteralPath $outboxRoot `
    -Context 'bridge outbox directory'
Ensure-AgentBridgePlainDirectory `
    -LiteralPath $outboxDir `
    -Context 'bridge agent outbox directory'

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

$line = (($event | ConvertTo-Json -Depth 12 -Compress) + [Environment]::NewLine)

function Exit-AgentBridgePostCommitMutationLock {
    <#
    Release the internal stale-claim lock after the canonical event append.

    Every resource is finalized independently. Failures are warning-only
    because the shared event is already durably committed and reporting a
    top-level failure would invite an external retry of the same event. The
    ordinary Exit-AgentBridgeMutationLock function remains strict everywhere
    before that commit boundary.
    #>
    [CmdletBinding()]
    param([AllowNull()] $Lock)

    if ($null -eq $Lock) { return }
    $stream = $null
    $parentPin = $null
    $lockFinalizationFailures = @()
    if ($Lock -is [System.IO.FileStream]) {
        $stream = $Lock
    } else {
        try {
            $stream = $Lock.stream
        } catch {
            $lockFinalizationFailures += $_.Exception
        }
        try {
            $parentPin = $Lock.parent_pin
        } catch {
            $lockFinalizationFailures += $_.Exception
        }
    }

    if ($null -ne $stream) {
        try {
            $stream.Unlock(0, 1)
        } catch {
            $lockFinalizationFailures += $_.Exception
        }
        try {
            $stream.Dispose()
        } catch {
            $lockFinalizationFailures += $_.Exception
        }
    }
    try {
        Exit-AgentBridgeParentDirectoryPin -Pin $parentPin
    } catch {
        $lockFinalizationFailures += $_.Exception
    }

    if ($lockFinalizationFailures.Count -gt 0) {
        $lockFinalizationMessages = @(
            $lockFinalizationFailures | ForEach-Object { $_.Message }
        ) -join '; '
        $lockFinalizationWarning = (
            "canonical stale-release event committed, but mutation-lock " +
            "finalization reported: {0}; continuing without event retry"
        ) -f $lockFinalizationMessages
        Write-AgentBridgeNonThrowingWarning -Message $lockFinalizationWarning
    }
}

function Add-LineWithRetry {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $Line,
        [switch] $NoSpool
    )
    $parent = Split-Path -Parent $Path
    $encoding = New-Object System.Text.UTF8Encoding($false)
    $lineBytes = [byte[]]$encoding.GetBytes($Line)
    $appendFailure = $null
    $suppressRetryAndSpool = $false
    $canonicalCommitted = $false
    $mutexFinalizationFailures = @()

    # Reject deterministic path-boundary failures before entering the
    # contention retry loop. The append helper repeats these checks against
    # the open handle so a race cannot turn a preflight into write authority.
    try {
        Assert-AgentBridgePlainDirectory `
            -LiteralPath $parent `
            -Context 'bridge event append directory'
        if (Test-Path -LiteralPath $Path) {
            Assert-AgentBridgeRegularUnlinkedFile `
                -LiteralPath $Path `
                -Context 'bridge event append target'
        }
    } catch {
        $appendFailure = $_.Exception
    }

    # Contention hardening (bridge audit 2026-07-02): serialize appends across
    # processes with a named mutex so the boot-burst / multi-agent case stops
    # exhausting the retry loop. AbandonedMutexException means a holder died
    # while owning the mutex - ownership transfers to us, so treat as
    # acquired. A WaitOne timeout falls back to the unserialized retry loop
    # below (never deadlock the bridge on a hung holder).
    $mutex = $null
    $acquired = $false
    try {
        if ($null -ne $appendFailure) {
            throw $appendFailure
        }
        try {
            $mutex = New-Object System.Threading.Mutex($false, 'Global\WaggleDanceBridgeAppendV1')
            try { $acquired = $mutex.WaitOne(10000) }
            catch [System.Threading.AbandonedMutexException] { $acquired = $true }
        } catch { $mutex = $null }

        for ($i = 0; $i -lt 40; $i++) {
            try {
                Add-AgentBridgeBytesToRegularUnlinkedFile `
                    -LiteralPath $Path `
                    -Bytes $lineBytes `
                    -Context 'bridge event append target'
                $canonicalCommitted = $true
                break
            } catch {
                $appendFailure = $_.Exception
                $suppressRetryAndSpool = [bool](
                    $appendFailure.Data['AgentBridgeAppendRolledBack'] -or
                    $appendFailure.Data['AgentBridgeAppendAmbiguous']
                )
                if ($suppressRetryAndSpool) {
                    break
                }
                Start-Sleep -Milliseconds (25 + ($i * 10))
            }
        }
    } catch {
        $appendFailure = $_.Exception
        $suppressRetryAndSpool = [bool](
            $appendFailure.Data['AgentBridgeAppendRolledBack'] -or
            $appendFailure.Data['AgentBridgeAppendAmbiguous']
        )
    } finally {
        if ($null -ne $mutex) {
            if ($acquired) {
                try {
                    $mutex.ReleaseMutex()
                } catch {
                    $mutexFinalizationFailures += $_.Exception
                }
            }
            try {
                $mutex.Dispose()
            } catch {
                $mutexFinalizationFailures += $_.Exception
            }
        }
    }

    if ($canonicalCommitted) {
        if ($mutexFinalizationFailures.Count -gt 0) {
            $mutexFinalizationMessages = @(
                $mutexFinalizationFailures | ForEach-Object { $_.Message }
            ) -join '; '
            $mutexFinalizationWarning = (
                "canonical bridge event append committed, but append-mutex " +
                "finalization reported: {0}; continuing without retry or spool"
            ) -f $mutexFinalizationMessages
            Write-AgentBridgeNonThrowingWarning -Message $mutexFinalizationWarning
        }
        return
    }

    # A helper-reported rollback means the canonical inode is restored and
    # replay/spooling would manufacture a second commit signal. An ambiguous
    # rollback must likewise stop without creating a misleading replay file.
    if ($suppressRetryAndSpool) {
        throw $appendFailure
    }

    # A failed per-agent outbox append is a degraded mirror after the shared
    # canonical event has committed. Spooling it beneath outbox/ would create
    # an orphan replay candidate (the replayer only consumes <bridge>/spool)
    # and could later duplicate the canonical event. Let the caller report a
    # warning and continue the last-event projection without any spool file.
    if ($NoSpool) {
        throw ((
            "could not append bridge mirror after retries without spool: " +
            "{0} (append error: {1})"
        ) -f
            $Path,
            $appendFailure.Message
        )
    }

    # Durability (bridge audit 2026-07-02): the event used to be LOST when the
    # retry budget ran out (and the outbox copy was skipped because this throw
    # aborts the script). Spool it to a per-event file first so nothing is
    # dropped - a replay can append spooled events later - then still throw
    # loudly so the caller knows the shared log missed it.
    $spoolDir = Join-Path (Split-Path -Parent $parent) 'spool'
    try {
        Ensure-AgentBridgePlainDirectory `
            -LiteralPath $spoolDir `
            -Context 'bridge failed-append spool directory'
        $stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfff')
        $spoolPath = Join-Path $spoolDir (
            "failed-append-{0}-{1}-{2}-{3}.jsonl" -f
            $Agent,
            $stamp,
            $PID,
            [guid]::NewGuid().ToString('N')
        )
        $spoolSha256 = Get-AgentBridgeSha256Hex -Bytes $lineBytes
        $spoolResult = Invoke-AgentBridgeTrustedBytesCreateNew `
            -DestinationPath $spoolPath `
            -PublishBytes $lineBytes `
            -ExpectedSha256 $spoolSha256 `
            -ExpectedLength ([long]$lineBytes.Length) `
            -Context 'failed bridge append spool event'
        if (-not [bool]$spoolResult.succeeded) {
            throw $spoolResult.error
        }
    } catch {
        throw (
            (
                "could not append bridge event after retries: {0} " +
                "(append error: {1}; spool also failed: {2})"
            ) -f
                $Path,
                $appendFailure.Message,
                $_.Exception.Message
        )
    }
    throw (
        (
            "could not append bridge event after retries: {0} " +
            "(append error: {1}; event spooled to {2})"
        ) -f
            $Path,
            $appendFailure.Message,
            $spoolPath
    )
}

function Set-AgentBridgeLastCacheInPlace {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [byte[]] $Bytes,
        [Parameter(Mandatory)] [string] $ExpectedSha256,
        [Parameter(Mandatory)] [long] $ExpectedLength,
        [Parameter(Mandatory)] $ParentPin
    )

    $maxCacheBytes = [long](1MB)
    if (
        $ExpectedLength -lt 0 -or
        $ExpectedLength -gt $maxCacheBytes
    ) {
        throw (
            "bridge last-event update exceeds the safe cache bound " +
            "($ExpectedLength > $maxCacheBytes bytes)"
        )
    }
    Assert-AgentBridgeTrustedBytesIdentity `
        -Bytes $Bytes `
        -ExpectedSha256 $ExpectedSha256 `
        -ExpectedLength $ExpectedLength `
        -Context 'bridge last-event in-place input'

    $stream = $null
    $originalBytes = $null
    $originalSha256 = $null
    $mutationStarted = $false
    try {
        Assert-AgentBridgeParentDirectoryPin -Pin $ParentPin
        Assert-AgentBridgeRegularUnlinkedFile `
            -LiteralPath $Path `
            -Context 'bridge last-event file'
        $stream = [System.IO.File]::Open(
            $Path,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
        Assert-AgentBridgeParentDirectoryPin -Pin $ParentPin
        Assert-AgentBridgeChildHandleParentPin `
            -Pin $ParentPin `
            -ChildHandle $stream.SafeFileHandle
        Assert-AgentBridgeExclusiveHandleIdentity `
            -Stream $stream `
            -Context 'bridge last-event file'
        Assert-AgentBridgeRegularUnlinkedFile `
            -LiteralPath $Path `
            -Context 'bridge last-event file'
        if ([long]$stream.Length -gt $maxCacheBytes) {
            throw (
                "bridge last-event file exceeds the safe cache bound " +
                "($($stream.Length) > $maxCacheBytes bytes)"
            )
        }

        $originalLength = [int]$stream.Length
        $originalBytes = [byte[]]::new($originalLength)
        [void]$stream.Seek(0, [System.IO.SeekOrigin]::Begin)
        $offset = 0
        while ($offset -lt $originalLength) {
            $read = $stream.Read(
                $originalBytes,
                $offset,
                $originalLength - $offset
            )
            if ($read -le 0) {
                throw 'bridge last-event file ended during original capture'
            }
            $offset += $read
        }
        $originalSha256 = Get-AgentBridgeSha256Hex -Bytes $originalBytes

        # LAST CACHE V3 MARKER: update only the already-open default stream.
        # NTFS alternate streams, if any, remain attached and untouched.
        [void]$stream.Seek(0, [System.IO.SeekOrigin]::Begin)
        $mutationStarted = $true
        $stream.Write($Bytes, 0, $Bytes.Length)
        $stream.SetLength($ExpectedLength)
        $stream.Flush($true)

        if ([long]$stream.Length -ne $ExpectedLength) {
            throw (
                "bridge last-event update length changed: expected " +
                "$ExpectedLength; actual $($stream.Length)"
            )
        }
        [void]$stream.Seek(0, [System.IO.SeekOrigin]::Begin)
        $verifiedBytes = [byte[]]::new([int]$ExpectedLength)
        $offset = 0
        while ($offset -lt $verifiedBytes.Length) {
            $read = $stream.Read(
                $verifiedBytes,
                $offset,
                $verifiedBytes.Length - $offset
            )
            if ($read -le 0) {
                throw 'bridge last-event file ended during update verification'
            }
            $offset += $read
        }
        Assert-AgentBridgeTrustedBytesIdentity `
            -Bytes $verifiedBytes `
            -ExpectedSha256 $ExpectedSha256 `
            -ExpectedLength $ExpectedLength `
            -Context 'bridge last-event in-place update'
        Assert-AgentBridgeExclusiveHandleIdentity `
            -Stream $stream `
            -Context 'bridge last-event file'
        Assert-AgentBridgeRegularUnlinkedFile `
            -LiteralPath $Path `
            -Context 'bridge last-event file'
        Assert-AgentBridgeChildHandleParentPin `
            -Pin $ParentPin `
            -ChildHandle $stream.SafeFileHandle
        Assert-AgentBridgeParentDirectoryPin -Pin $ParentPin
    } catch {
        $updateError = $_.Exception
        if (
            $mutationStarted -and
            $null -ne $stream -and
            $null -ne $originalBytes
        ) {
            try {
                # Restore the same open inode. No pathname move, replacement,
                # or delete is used, and every alternate stream is preserved.
                [void]$stream.Seek(0, [System.IO.SeekOrigin]::Begin)
                $stream.Write($originalBytes, 0, $originalBytes.Length)
                $stream.SetLength([long]$originalBytes.Length)
                $stream.Flush($true)
                if ([long]$stream.Length -ne [long]$originalBytes.Length) {
                    throw 'bridge last-event rollback length mismatched'
                }
                [void]$stream.Seek(0, [System.IO.SeekOrigin]::Begin)
                $restoredBytes = [byte[]]::new($originalBytes.Length)
                $offset = 0
                while ($offset -lt $restoredBytes.Length) {
                    $read = $stream.Read(
                        $restoredBytes,
                        $offset,
                        $restoredBytes.Length - $offset
                    )
                    if ($read -le 0) {
                        throw (
                            'bridge last-event file ended during rollback ' +
                            'verification'
                        )
                    }
                    $offset += $read
                }
                Assert-AgentBridgeTrustedBytesIdentity `
                    -Bytes $restoredBytes `
                    -ExpectedSha256 $originalSha256 `
                    -ExpectedLength ([long]$originalBytes.Length) `
                    -Context 'bridge last-event rollback'
                Assert-AgentBridgeExclusiveHandleIdentity `
                    -Stream $stream `
                    -Context 'bridge last-event rollback file'
                Assert-AgentBridgeChildHandleParentPin `
                    -Pin $ParentPin `
                    -ChildHandle $stream.SafeFileHandle
                Assert-AgentBridgeParentDirectoryPin -Pin $ParentPin
            } catch {
                $rollbackError = $_.Exception
                $ambiguous = [System.IO.IOException]::new(
                    ((
                        "bridge last-event in-place update and rollback " +
                        "failed; cache outcome is ambiguous " +
                        "(update_error={0}; rollback_error={1})"
                    ) -f
                        $updateError.Message,
                        $rollbackError.Message
                    ),
                    $updateError
                )
                $ambiguous.Data['AgentBridgeLastCacheAmbiguous'] = $true
                throw $ambiguous
            }
            $rolledBack = [System.IO.IOException]::new(
                ((
                    "bridge last-event in-place update was rejected and " +
                    "durably rolled back: {0}"
                ) -f $updateError.Message
                ),
                $updateError
            )
            $rolledBack.Data['AgentBridgeLastCacheRolledBack'] = $true
            throw $rolledBack
        }
        throw $updateError
    } finally {
        if ($null -ne $stream) {
            try { $stream.Dispose() } catch {}
        }
    }
}

function Write-JsonAtomic {
    # LAST CACHE V3: missing caches are created from trusted in-memory bytes;
    # existing caches are updated through one bound handle with same-handle
    # rollback. No pathname is moved, replaced, or deleted.
    param([Parameter(Mandatory)] [string] $Path, [Parameter(Mandatory)] [string] $Json)
    $parent = Split-Path -Parent $Path
    $encoding = New-Object System.Text.UTF8Encoding($false)
    $jsonBytes = [byte[]]$encoding.GetBytes($Json)
    $maxCacheBytes = [long](1MB)
    if ([long]$jsonBytes.Length -gt $maxCacheBytes) {
        throw (
            "bridge last-event JSON exceeds the safe cache bound " +
            "($($jsonBytes.Length) > $maxCacheBytes bytes)"
        )
    }
    $jsonSha256 = Get-AgentBridgeSha256Hex -Bytes $jsonBytes
    $mutex = $null
    $acquired = $false
    $parentPin = $null
    try {
        try {
            $mutex = New-Object System.Threading.Mutex(
                $false,
                'Global\WaggleDanceBridgeLastEventV2'
            )
            try { $acquired = $mutex.WaitOne(10000) }
            catch [System.Threading.AbandonedMutexException] {
                $acquired = $true
            }
        } catch {
            throw "could not acquire last-event serialization: $($_.Exception.Message)"
        }
        if (-not $acquired) {
            throw "timed out acquiring last-event serialization"
        }

        Assert-AgentBridgePlainDirectory `
            -LiteralPath $parent `
            -Context 'bridge last-event directory'
        try {
            $parentPin = Enter-AgentBridgeParentDirectoryPin `
                -ChildPath $Path `
                -Context 'bridge last-event publication'
        } catch {
            $integrityError = [System.IO.IOException]::new(
                "bridge last-event parent integrity failed: $($_.Exception.Message)",
                $_.Exception
            )
            $integrityError.Data['AgentBridgeLastCacheUntrusted'] = $true
            throw $integrityError
        }
        Assert-AgentBridgeParentDirectoryPin -Pin $parentPin

        if (-not (Test-Path -LiteralPath $Path)) {
            $createResult = Invoke-AgentBridgeTrustedBytesCreateNew `
                -DestinationPath $Path `
                -PublishBytes $jsonBytes `
                -ExpectedSha256 $jsonSha256 `
                -ExpectedLength ([long]$jsonBytes.Length) `
                -Context 'bridge last-event initial publication'
            if (-not [bool]$createResult.succeeded) {
                if ([bool]$createResult.collision) {
                    $collisionError = [System.IO.IOException]::new(
                        "bridge last-event initial publication collided"
                    )
                    $collisionError.Data['AgentBridgeLastCacheUntrusted'] = $true
                    throw $collisionError
                }
                throw $createResult.error
            }
            $null = Assert-AgentBridgeExpectedRegularFileSnapshot `
                -LiteralPath $Path `
                -ExpectedSha256 $jsonSha256 `
                -ExpectedLength ([long]$jsonBytes.Length) `
                -Context 'bridge last-event file'
            return
        }

        Set-AgentBridgeLastCacheInPlace `
            -Path $Path `
            -Bytes $jsonBytes `
            -ExpectedSha256 $jsonSha256 `
            -ExpectedLength ([long]$jsonBytes.Length) `
            -ParentPin $parentPin
    } finally {
        Exit-AgentBridgeParentDirectoryPin -Pin $parentPin
        if ($null -ne $mutex) {
            if ($acquired) { try { $mutex.ReleaseMutex() } catch {} }
            $mutex.Dispose()
        }
    }
}

$dateName = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd') + '.jsonl'
$outboxPath = Join-Path $outboxDir $dateName
# Internal review fix R6 (2026-05-09): if shared write fails after all
# retries, do NOT write the outbox copy. The shared events.jsonl is the
# canonical bridge stream; an outbox-only event creates a phantom record
# that no reader sees (Read-AgentBridge only consumes shared/events.jsonl)
# and rots into per-agent local-only state. Append-then-throw lets the
# caller surface the failure without leaving asymmetric state behind.
Add-LineWithRetry -Path $eventsPath -Line $line
if ($null -ne $internalStaleLeaseMutationLock) {
    $completedStaleLeaseMutationLock = $internalStaleLeaseMutationLock
    $internalStaleLeaseMutationLock = $null
    Exit-AgentBridgePostCommitMutationLock `
        -Lock $completedStaleLeaseMutationLock
}
try {
    Add-LineWithRetry -Path $outboxPath -Line $line -NoSpool
} catch {
    # The shared events.jsonl append above is the canonical commit point.
    # Treat a later per-agent outbox failure as a degraded mirror instead of
    # reporting the already-committed event as failed (which would invite a
    # retry and duplicate the canonical record).
    $outboxWarning = ((
        "canonical event committed but per-agent outbox append failed {0}: " +
        "{1}"
    ) -f
        $outboxPath,
        $_.Exception.Message
    )
    try {
        Write-Warning -Message $outboxWarning -WarningAction Continue
    } catch {
        try {
            [Console]::Error.WriteLine("WARNING: $outboxWarning")
        } catch {}
    }
}

$lastPath = Join-Path $sharedDir ("last_{0}.json" -f $Agent)
try {
    Write-JsonAtomic -Path $lastPath -Json ($event | ConvertTo-Json -Depth 12)
} catch {
    # last_<agent>.json is an optimization for quick status reads; the
    # canonical bridge record is already appended to shared/events.jsonl.
    # The per-agent outbox is also best-effort. Do not fail the event write
    # because Windows had the last-file open during atomic replace.
    $lastFileWarning = [string]$_.Exception.Message
    try {
        Write-Warning -Message $lastFileWarning -WarningAction Continue
    } catch {
        try {
            [Console]::Error.WriteLine("WARNING: $lastFileWarning")
        } catch {}
    }
}

[pscustomobject]$event
} finally {
    $abandonedStaleLeaseMutationLock = $internalStaleLeaseMutationLock
    $internalStaleLeaseMutationLock = $null
    Exit-AgentBridgeMutationLock -Lock $abandonedStaleLeaseMutationLock
}
