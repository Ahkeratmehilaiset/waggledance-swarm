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
    [ValidateScript({ $_ -cmatch '^[a-z][a-z0-9_-]{1,32}$' })]
    [string] $Agent,

    [int] $PollIntervalMs = 1000,
    [int] $DebounceMs = 250,
    [int] $MaxIterations = 0,

    # Test/diagnostic hook: force the initial legacy line-count baseline.
    # -1 (default) = auto-detect from the current events.jsonl size, so
    # the watcher fires only on appends made AFTER it starts. >=0 lets
    # the smoke test inject events before the watcher and still observe
    # detection by setting the baseline below those rows. Conversion to a
    # byte offset happens once at startup; the poll loop is incremental.
    [int] $StartLineCount = -1,

    # Test/diagnostic hook: write this sentinel after the initial cursor is
    # established so background-job tests can measure polling latency without
    # including PowerShell host startup time.
    [string] $ReadyPath = '',

    [string] $RuntimeRoot = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

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
$incrementalReader = Join-Path $PSScriptRoot 'BridgeIncrementalReader.ps1'
if (-not (Test-Path -LiteralPath $incrementalReader -PathType Leaf)) {
    throw "incremental bridge reader missing: $incrementalReader"
}
. $incrementalReader

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

# Establish an O(1) baseline so we don't wake for pre-existing history.
if ($StartLineCount -ge 0) {
    $byteOffset = Resolve-BridgeByteOffsetForLineCount `
        -Path $eventsPath -LineCount $StartLineCount
} else {
    $byteOffset = Get-BridgeEventFileLength -Path $eventsPath
}

if ($ReadyPath) {
    Set-Content -LiteralPath $ReadyPath -Value 'ready' -Encoding UTF8 `
        -NoNewline -Force -ErrorAction Stop
}

$iteration = 0
while ($MaxIterations -le 0 -or $iteration -lt $MaxIterations) {
    $iteration++
    Start-Sleep -Milliseconds $PollIntervalMs

    $delta = Read-BridgeEventDelta -Path $eventsPath -Offset $byteOffset
    if (-not $delta.exists) {
        # A rename/rotation can make the path disappear for one poll. Keep
        # the prior cursor until a concrete replacement file is observable.
        continue
    }
    if ($delta.truncated) {
        # Preserve the prior no-replay rotation policy. New rows appended
        # after this replacement baseline will be observed normally.
        $byteOffset = [int64]$delta.file_length
        continue
    }
    $byteOffset = [int64]$delta.next_offset

    $shouldWake = $false
    foreach ($line in @($delta.lines)) {
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
