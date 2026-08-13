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

.PARAMETER ReadyPath
    Test/diagnostic hook: write a marker after the initial baseline is ready.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateScript({ $_ -cmatch '^[a-z][a-z0-9_-]{1,32}$' })]
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

    [string] $RuntimeRoot = '',

    [string] $ReadyPath = ''
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
. (Join-Path $PSScriptRoot 'BridgeIncrementalReader.ps1')

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

function Set-BridgeWakeDirtyBit {
    $stamp = (Get-Date).ToUniversalTime().ToString('o')
    Set-Content -LiteralPath $wakePath -Value $stamp `
        -Encoding UTF8 -NoNewline -Force -ErrorAction Stop
}

# Establish a stable identity-bound baseline so replacement cannot masquerade
# as an append with a coincidentally larger byte length.
$cursor = $null
if ($StartLineCount -ge 0) {
    $cursor = Resolve-BridgeCursorForLineCount -Path $eventsPath `
        -LineCount ([int64]$StartLineCount)
} else {
    $baseline = Read-BridgeEventTail -Path $eventsPath -MaxLines 1
    if ($baseline.status -in @('BLOCKED','RETRY')) {
        throw "Watch-Bridge: baseline unavailable: $($baseline.reason)"
    }
    $cursor = $baseline.candidate_cursor
    # A supervisor relaunch cannot know whether targeted rows arrived while
    # the watcher was absent. Set the level-triggered dirty bit after the
    # identity-bound baseline; later appends are covered by the delta loop.
    Set-BridgeWakeDirtyBit
}

if ($ReadyPath) {
    Set-Content -LiteralPath $ReadyPath `
        -Value ([datetime]::UtcNow.ToString('o')) `
        -Encoding UTF8 -NoNewline -Force -ErrorAction Stop
}

$iteration = 0
while ($MaxIterations -le 0 -or $iteration -lt $MaxIterations) {
    $iteration++
    Start-Sleep -Milliseconds $PollIntervalMs

    $result = Read-BridgeEventDelta -Path $eventsPath -Cursor $cursor
    if ($result.status -ceq 'RETRY') {
        # The replacement may contain a targeted row that cannot be safely
        # classified from the old identity-bound cursor. Preserve liveness by
        # setting the dirty bit before the supervisor relaunches at a new
        # baseline; the agent's normal read path decides what needs action.
        Set-BridgeWakeDirtyBit
        throw "Watch-Bridge: bridge read retry required: $($result.reason)"
    }
    if ($result.status -ceq 'BLOCKED') {
        throw "Watch-Bridge: bridge read blocked: $($result.reason)"
    }
    if ($result.status -ne 'OK') {
        if ($null -eq $cursor -and $null -ne $result.candidate_cursor) {
            $cursor = $result.candidate_cursor
        }
        continue
    }

    $shouldWake = $false
    foreach ($ev in @($result.rows)) {
        if (Test-IsTargeted -Event $ev -WatchedAgent $Agent) {
            $shouldWake = $true
            break
        }
    }
    if ($shouldWake) {
        Set-BridgeWakeDirtyBit
        $cursor = $result.candidate_cursor
        Start-Sleep -Milliseconds $DebounceMs
    } else {
        $cursor = $result.candidate_cursor
    }
}
