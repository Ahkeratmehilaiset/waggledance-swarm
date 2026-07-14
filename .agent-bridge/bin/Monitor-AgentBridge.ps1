#requires -Version 5.1
<#
.SYNOPSIS
    Cursor-based bridge event monitor for active agent sessions.

.DESCRIPTION
    Tails shared/events.jsonl by durable byte-offset cursor and prints only new
    substantive events. This is the chat/terminal-facing companion to the
    wake-file substrate: Watch-Bridge.ps1 tells an agent that something
    arrived, while this script shows exactly what arrived without replaying
    old history on every poll.

    By default, a new state file initializes to the current end of the
    event stream. That avoids historical floods after a restart. Pass
    -ReplayExisting only for audits where historical output is intended.

    Substantive excludes infrastructure traffic and ACK-only messages:
    heartbeat, liveness, wake_request, and message/received|seen|acknowledged.

.PARAMETER Agent
    The local agent running the monitor. Own emissions are ignored.

.PARAMETER FromAgent
    Optional sender filter. For Codex watching Claude, use -FromAgent claude.

.PARAMETER TargetedOnly
    If set, emit only events whose `to` field targets -Agent. By default the
    monitor shows all substantive events from -FromAgent so claims/done events
    without a `to` still surface.

.PARAMETER StatePath
    Optional cursor file path. Defaults to
    <bridgeRoot>/shared/monitor_<agent>[_from_<from>].cursor.json.

.PARAMETER MaxIterations
    0 means run forever. A positive value makes the monitor testable.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateScript({ $_ -cmatch '^[a-z][a-z0-9_-]{1,32}$' })]
    [string] $Agent,

    [ValidateScript({ $_ -eq '' -or $_ -cmatch '^[a-z][a-z0-9_-]{1,32}$' })]
    [string] $FromAgent = '',

    [int] $PollIntervalMs = 10000,
    [int] $MaxIterations = 0,
    [string] $StatePath = '',
    [string] $RuntimeRoot = '',

    [switch] $ReplayExisting,
    [switch] $TargetedOnly,
    [switch] $Json
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$bridgeRoot = if ($RuntimeRoot) {
    $RuntimeRoot
} elseif ($env:AGENT_BRIDGE_RUNTIME_ROOT) {
    [string]$env:AGENT_BRIDGE_RUNTIME_ROOT
} else {
    Split-Path -Parent $PSScriptRoot
}
if (-not (Test-Path -LiteralPath $bridgeRoot -PathType Container)) {
    [void](New-Item -ItemType Directory -Path $bridgeRoot -Force -ErrorAction Stop)
}

$sharedDir = Join-Path $bridgeRoot 'shared'
if (-not (Test-Path -LiteralPath $sharedDir -PathType Container)) {
    [void](New-Item -ItemType Directory -Path $sharedDir -Force -ErrorAction Stop)
}
$eventsPath = Join-Path $sharedDir 'events.jsonl'
$incrementalReader = Join-Path $PSScriptRoot 'BridgeIncrementalReader.ps1'
if (-not (Test-Path -LiteralPath $incrementalReader -PathType Leaf)) {
    throw "incremental bridge reader missing: $incrementalReader"
}
. $incrementalReader

if (-not $StatePath) {
    $suffix = if ($FromAgent) { "_from_$FromAgent" } else { '' }
    $StatePath = Join-Path $sharedDir ("monitor_{0}{1}.cursor.json" -f $Agent, $suffix)
}

$classifier = Join-Path $PSScriptRoot 'BridgeEventClassifier.ps1'
if (Test-Path -LiteralPath $classifier -PathType Leaf) {
    . $classifier
}

function Get-EventTargetsLocal {
    param([Parameter(Mandatory)] [object] $Event)

    if (Get-Command Get-BridgeEventTargets -ErrorAction SilentlyContinue) {
        return @(Get-BridgeEventTargets -Event $Event)
    }
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

function Test-SubstantiveMonitorEvent {
    param(
        [Parameter(Mandatory)] [object] $Event,
        [Parameter(Mandatory)] [string] $LocalAgent,
        [string] $SenderFilter = '',
        [bool] $OnlyTargeted = $false
    )

    $eventAgent = if ($Event.PSObject.Properties['agent']) { [string]$Event.agent } else { '' }
    if (-not $eventAgent) { return $false }
    if ($eventAgent -eq $LocalAgent) { return $false }
    if ($SenderFilter -and $eventAgent -ne $SenderFilter) { return $false }

    if ($OnlyTargeted) {
        if (@(Get-EventTargetsLocal -Event $Event) -notcontains $LocalAgent) {
            return $false
        }
    }

    $type = if ($Event.PSObject.Properties['type']) { [string]$Event.type } else { '' }
    $status = if ($Event.PSObject.Properties['status']) { [string]$Event.status } else { '' }

    if ($type -in @('heartbeat','liveness','wake_request')) { return $false }
    if ($type -eq 'message' -and $status -in @('received','seen','acknowledged')) {
        return $false
    }

    return $true
}

function Format-MonitorLine {
    param([Parameter(Mandatory)] [object] $Event)

    $ts = if ($Event.PSObject.Properties['ts_utc']) { [string]$Event.ts_utc } else { '' }
    $from = if ($Event.PSObject.Properties['agent']) { [string]$Event.agent } else { '' }
    $to = if ($Event.PSObject.Properties['to'] -and [string]$Event.to) {
        " -> $([string]$Event.to)"
    } else {
        ''
    }
    $type = if ($Event.PSObject.Properties['type']) { [string]$Event.type } else { '' }
    $status = if ($Event.PSObject.Properties['status']) { [string]$Event.status } else { '' }
    $taskId = if ($Event.PSObject.Properties['task_id']) { [string]$Event.task_id } else { '' }
    $message = if ($Event.PSObject.Properties['message']) { [string]$Event.message } else { '' }
    $message = (($message -replace '\s+', ' ').Trim())
    if ($message.Length -gt 220) {
        $message = $message.Substring(0, 217) + '...'
    }
    return "{0} [{1}{2}] {3}/{4} {5}: {6}" -f `
        $ts, $from, $to, $type, $status, $taskId, $message
}

function Get-InitialCursor {
    if (Test-Path -LiteralPath $StatePath -PathType Leaf) {
        try {
            $state = Get-Content -Raw -LiteralPath $StatePath -Encoding UTF8 | ConvertFrom-Json
            if ($state.PSObject.Properties['byte_offset']) {
                return [int64]$state.byte_offset
            }
            # One-time migration from the legacy line-count cursor. This may
            # scan historical bytes once, but every steady-state poll after
            # migration reads only the append delta.
            if ($state.PSObject.Properties['line_count']) {
                return Resolve-BridgeByteOffsetForLineCount `
                    -Path $eventsPath -LineCount ([int]$state.line_count)
            }
        } catch {
            Write-Warning "Monitor-AgentBridge: cursor state unreadable; starting from a safe baseline: $($_.Exception.Message)"
        }
    }
    if ($ReplayExisting) { return [int64]0 }
    return Get-BridgeEventFileLength -Path $eventsPath
}

function Save-Cursor {
    param([Parameter(Mandatory)] [int64] $ByteOffset)

    $parent = Split-Path -Parent $StatePath
    if ($parent -and -not (Test-Path -LiteralPath $parent -PathType Container)) {
        [void](New-Item -ItemType Directory -Path $parent -Force -ErrorAction Stop)
    }
    $state = [ordered]@{
        cursor_version  = 'byte_offset_v1'
        byte_offset     = $ByteOffset
        updated_at_utc  = (Get-Date).ToUniversalTime().ToString('o')
        agent           = $Agent
        from_agent      = $FromAgent
        targeted_only   = [bool]$TargetedOnly
        replay_existing = [bool]$ReplayExisting
    }
    $json = $state | ConvertTo-Json -Depth 6
    $tmp = "$StatePath.tmp.$PID.$([guid]::NewGuid().ToString('N'))"
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($tmp, $json, $encoding)
    Move-Item -LiteralPath $tmp -Destination $StatePath -Force -ErrorAction Stop
}

$cursor = Get-InitialCursor
Save-Cursor -ByteOffset $cursor

$iteration = 0
while ($MaxIterations -le 0 -or $iteration -lt $MaxIterations) {
    $iteration++

    $delta = Read-BridgeEventDelta -Path $eventsPath -Offset $cursor
    if (-not $delta.exists) {
        if ($MaxIterations -gt 0 -and $iteration -ge $MaxIterations) { break }
        Start-Sleep -Milliseconds $PollIntervalMs
        continue
    }
    if ($delta.truncated) {
        # events.jsonl was truncated or rotated. Avoid flooding replay on
        # the replacement file unless the caller explicitly asked for replay.
        $cursor = if ($ReplayExisting) { [int64]0 } else { [int64]$delta.file_length }
        Save-Cursor -ByteOffset $cursor
        if ($ReplayExisting) {
            $delta = Read-BridgeEventDelta -Path $eventsPath -Offset $cursor
        } else {
            if ($MaxIterations -gt 0 -and $iteration -ge $MaxIterations) { break }
            Start-Sleep -Milliseconds $PollIntervalMs
            continue
        }
    }

    if ([int64]$delta.next_offset -gt $cursor) {
        foreach ($line in @($delta.lines)) {
            if (-not $line) { continue }
            try {
                $event = $line | ConvertFrom-Json -ErrorAction Stop
            } catch {
                continue
            }
            if (-not (Test-SubstantiveMonitorEvent `
                    -Event $event `
                    -LocalAgent $Agent `
                    -SenderFilter $FromAgent `
                    -OnlyTargeted ([bool]$TargetedOnly))) {
                continue
            }
            if ($Json) {
                Write-Output ($event | ConvertTo-Json -Depth 12 -Compress)
            } else {
                Write-Output (Format-MonitorLine -Event $event)
            }
        }
        $cursor = [int64]$delta.next_offset
        Save-Cursor -ByteOffset $cursor
    }

    if ($MaxIterations -gt 0 -and $iteration -ge $MaxIterations) { break }
    Start-Sleep -Milliseconds $PollIntervalMs
}
