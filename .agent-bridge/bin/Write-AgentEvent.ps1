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

$eventsPath = Join-Path $sharedDir 'events.jsonl'
$dateName = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd') + '.jsonl'
$outboxPath = Join-Path $outboxDir $dateName
Add-LineWithRetry -Path $eventsPath -Line $line
Add-LineWithRetry -Path $outboxPath -Line $line

$lastPath = Join-Path $sharedDir ("last_{0}.json" -f $Agent)
($event | ConvertTo-Json -Depth 12) | Set-Content -Path $lastPath -Encoding UTF8

[pscustomobject]$event
