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
    foreach ($target in $targetList) {
        if ($target -cnotmatch '^[a-z][a-z0-9_-]{1,32}$') {
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
$grokRequiredFreshnessShaFields = @('remote_main_sha', 'local_origin_main_sha', 'worktree_head')
$grokOptionalFreshnessShaFields = @('pr_head_sha', 'reviewed_head_sha', 'target_head_sha')
$fullGitShaPattern = '^[0-9a-f]{40}$'
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

$payload = $null
try {
    $payload = $PayloadJson | ConvertFrom-Json
} catch {
    $payload = [pscustomobject]@{ raw = $PayloadJson; parse_error = $_.Exception.Message }
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

function Get-BridgeObjectField {
    param(
        [Parameter(Mandatory)] $Object,
        [Parameter(Mandatory)] [string] $Name
    )
    if ($Object -is [hashtable]) {
        if ($Object.ContainsKey($Name)) { return $Object[$Name] }
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
    if ($Object -is [hashtable]) { return $Object.ContainsKey($Name) }
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
    if ($worktreeHead -cne $localOriginMainSha) {
        throw "grok freshness worktree sha mismatch"
    }
    foreach ($fieldName in $grokOptionalFreshnessShaFields) {
        if (Test-BridgeObjectHasField -Object $freshness -Name $fieldName) {
            $value = Get-BridgeObjectField -Object $freshness -Name $fieldName
            if ($null -ne $value -and -not (Test-FullGitSha -Value $value)) {
                throw "grok freshness $fieldName must be lowercase 40-hex sha"
            }
        }
    }
}

Assert-GrokFreshnessPayload -Payload $payload

# R13: honor AGENT_BRIDGE_RUNTIME_ROOT. If env var is SET, USE IT
# (create root if missing, fail loud on malformed path).
$bridgeRoot = if ($env:AGENT_BRIDGE_RUNTIME_ROOT) {
    [string]$env:AGENT_BRIDGE_RUNTIME_ROOT
} else {
    Split-Path -Parent $PSScriptRoot
}
if (-not (Test-Path -LiteralPath $bridgeRoot -PathType Container)) {
    [void](New-Item -ItemType Directory -Path $bridgeRoot -Force -ErrorAction Stop)
}
$sharedDir = Join-Path $bridgeRoot 'shared'
$outboxDir = Join-Path (Join-Path $bridgeRoot 'outbox') $Agent
foreach ($dir in @($sharedDir, $outboxDir)) {
    if (-not (Test-Path -LiteralPath $dir)) {
        [void](New-Item -ItemType Directory -Path $dir -Force)
    }
}

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

$line = (($event | ConvertTo-Json -Depth 12 -Compress) + [Environment]::NewLine)

function Add-LineWithRetry {
    param([Parameter(Mandatory)] [string] $Path, [Parameter(Mandatory)] [string] $Line)
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent)) {
        [void](New-Item -ItemType Directory -Path $parent -Force)
    }
    $encoding = New-Object System.Text.UTF8Encoding($false)
    for ($i = 0; $i -lt 40; $i++) {
        try {
            [System.IO.File]::AppendAllText($Path, $Line, $encoding)
            return
        } catch {
            Start-Sleep -Milliseconds (25 + ($i * 10))
        }
    }
    throw "could not append bridge event after retries: $Path"
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

$eventsPath = Join-Path $sharedDir 'events.jsonl'
$dateName = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd') + '.jsonl'
$outboxPath = Join-Path $outboxDir $dateName
# Internal review fix R6 (2026-05-09): if shared write fails after all
# retries, do NOT write the outbox copy. The shared events.jsonl is the
# canonical bridge stream; an outbox-only event creates a phantom record
# that no reader sees (Read-AgentBridge only consumes shared/events.jsonl)
# and rots into per-agent local-only state. Append-then-throw lets the
# caller surface the failure without leaving asymmetric state behind.
Add-LineWithRetry -Path $eventsPath -Line $line
Add-LineWithRetry -Path $outboxPath -Line $line

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
