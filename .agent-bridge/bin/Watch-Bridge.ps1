#requires -Version 5.1
<#
.SYNOPSIS
    R23.0 bridge wake-on-event watcher.

.DESCRIPTION
    Long-running watcher that polls shared/events.jsonl. When a new
    event arrives whose `to` field targets the watched agent, the
    watcher creates a sentinel file at `<bridgeRoot>/wake_<agent>`.

    The agent's own polling loop should call Test-BridgeWake.ps1 each
    iteration; finding the wake file is a "dirty bit" telling the
    agent there is unread incoming bridge traffic. The wake file is
    consumed (deleted) on read.

    Default cadence is 1 s polling + 250 ms debounce after writing a
    wake file so multiple near-simultaneous events (e.g. Invoke-RoleReview's
    three subprocess emissions) collapse into one wake.

    Per BRIDGE_PROTOCOL: this is push-style, but built on file-poll
    rather than FileSystemWatcher Register-ObjectEvent so it survives
    PowerShell 5.1's job/runspace boundaries cleanly. Background-job
    integration lives in Start-AgentBridgeSession.ps1.

    Toggle: respects $env:WAGGLE_BRIDGE_WAKE_ENABLED. Watcher exits
    immediately if set to '0'.

.PARAMETER Agent
    The agent whose inbox we watch. Wake file is wake_<Agent>.

.PARAMETER PollIntervalMs
    Polling cadence on shared/events.jsonl. Default 1000 ms.

.PARAMETER DebounceMs
    Pause after writing the wake file. Default 250 ms.

.PARAMETER MaxIterations
    Test hook: stop after N polls. 0 means run forever.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $Agent,

    [int] $PollIntervalMs = 1000,
    [int] $DebounceMs = 250,
    [int] $MaxIterations = 0,

    # Test/diagnostic hook: force the initial line-count baseline.
    # -1 (default) = auto-detect from the current events.jsonl size, so
    # the watcher fires only on appends made AFTER it starts. >=0 lets
    # the smoke test inject events before the watcher and still observe
    # detection by setting the baseline below those rows.
    [int] $StartLineCount = -1,

    [string] $RuntimeRoot = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$sessionIdentity = Join-Path $PSScriptRoot 'AgentBridgeSessionIdentity.ps1'
. $sessionIdentity
Assert-AgentBridgeSessionIdentity -RequestedAgent $Agent

if ($env:WAGGLE_BRIDGE_WAKE_ENABLED -eq '0') {
    Write-Output "Watch-Bridge: disabled via WAGGLE_BRIDGE_WAKE_ENABLED=0; exiting."
    return
}

# Mirror bridge-root resolution from Write-AgentEvent.ps1.
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

$eventsPath = Join-Path $bridgeRoot 'shared\events.jsonl'
$wakePath = Join-Path $bridgeRoot ("wake_{0}" -f $Agent)

function Test-IsTargeted {
    param(
        [Parameter(Mandatory)] [psobject] $Event,
        [Parameter(Mandatory)] [string] $WatchedAgent
    )

    # Don't wake on our own emissions; the agent already knows what it sent.
    if ($Event.PSObject.Properties.Name -contains 'agent' -and
        $Event.agent -eq $WatchedAgent) {
        return $false
    }

    if (-not ($Event.PSObject.Properties.Name -contains 'to')) {
        return $false
    }

    $to = $Event.to
    if ($null -eq $to -or $to -eq '') { return $false }

    # 'to' is a string; may be single agent or comma-separated list.
    $targets = ($to -split ',') | ForEach-Object { $_.Trim() }
    return $targets -contains $WatchedAgent
}

# Establish baseline so we don't wake for pre-existing history.
$lastLineCount = 0
if ($StartLineCount -ge 0) {
    $lastLineCount = $StartLineCount
} elseif (Test-Path -LiteralPath $eventsPath) {
    $lastLineCount = @(Get-Content -LiteralPath $eventsPath -ErrorAction SilentlyContinue).Count
}

$iteration = 0
while ($MaxIterations -le 0 -or $iteration -lt $MaxIterations) {
    $iteration++
    Start-Sleep -Milliseconds $PollIntervalMs

    if (-not (Test-Path -LiteralPath $eventsPath)) { continue }

    $lines = @(Get-Content -LiteralPath $eventsPath -ErrorAction SilentlyContinue)
    $count = $lines.Count
    if ($count -le $lastLineCount) {
        # File may have been rotated/truncated; resync baseline so we
        # don't miss future appends and don't double-fire on rotation.
        if ($count -lt $lastLineCount) { $lastLineCount = $count }
        continue
    }

    $newLines = $lines | Select-Object -Skip $lastLineCount
    $lastLineCount = $count

    $shouldWake = $false
    foreach ($line in $newLines) {
        if (-not $line) { continue }
        try {
            $ev = $line | ConvertFrom-Json -ErrorAction Stop
        } catch {
            continue
        }
        if (Test-IsTargeted -Event $ev -WatchedAgent $Agent) {
            $shouldWake = $true
            break
        }
    }

    if ($shouldWake) {
        $stamp = (Get-Date).ToUniversalTime().ToString('o')
        try {
            Set-Content -LiteralPath $wakePath -Value $stamp `
                -Encoding UTF8 -NoNewline -Force -ErrorAction Stop
        } catch {
            Write-Warning "Watch-Bridge: failed to write wake file '$wakePath': $($_.Exception.Message)"
        }
        Start-Sleep -Milliseconds $DebounceMs
    }
}
