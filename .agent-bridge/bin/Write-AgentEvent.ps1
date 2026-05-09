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

$bridgeRoot = Split-Path -Parent $PSScriptRoot
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
    # Internal review fix A7/S4 (2026-05-09): Set-Content used to clobber
    # last_<agent>.json mid-write — a concurrent reader could observe a
    # partially-written file and crash. Write to a temp sibling and move
    # over the target so readers always see either the old file or the
    # new file in full, never a torn write.
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
            [System.IO.File]::Move($tmp, $Path)
            return
        } catch {
            # Move fails on Windows when destination exists. Replace it.
            try {
                if (Test-Path -LiteralPath $Path) {
                    [System.IO.File]::Replace($tmp, $Path, $null)
                    return
                }
            } catch {
                Start-Sleep -Milliseconds (25 + ($i * 10))
            }
        }
    }
    # Last-resort cleanup: remove the temp file so we don't litter shared/.
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
