#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [ValidateSet('codex','claude','operator','system')] [string] $Agent,
    [Parameter(Mandatory)] [ValidateSet('status','intent','claim','release','message','finding','decision','test','blocked','handoff','done','heartbeat','wake_request','liveness')] [string] $Type,
    [string] $TaskId = '',
    [string] $Status = '',
    [string] $Message = '',
    [string] $To = '',
    [string[]] $Paths = @(),
    [string[]] $WriteScope = @(),
    [string] $Severity = '',
    [string] $RunId = '',
    [string] $PayloadJson = '{}'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$taskIdRequiredTypes = @('claim', 'release', 'done', 'handoff', 'blocked')
$ackStatuses = @('acknowledged', 'received', 'seen')
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

$payload = $null
try {
    $payload = $PayloadJson | ConvertFrom-Json
} catch {
    $payload = [pscustomobject]@{ raw = $PayloadJson; parse_error = $_.Exception.Message }
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
