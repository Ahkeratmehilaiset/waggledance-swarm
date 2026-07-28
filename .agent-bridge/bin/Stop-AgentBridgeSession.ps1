#requires -Version 5.1
<#
.SYNOPSIS
    R23.1.1 manual cleanup for orphaned wake-watcher and heartbeat jobs.

.DESCRIPTION
    Start-AgentBridgeSession.ps1 registers a PowerShell.Exiting event
    handler that stops the wake (R23.0) and heartbeat (R23.1) background
    jobs on normal shutdown. The handler does NOT fire on hard kills
    (Ctrl+C close on host crash, OOM, BSOD). After such a kill, the next
    PowerShell session can run THIS script to clean up any orphaned jobs
    by name pattern.

    Targets jobs named `agent-bridge-watcher-*` and
    `agent-bridge-heartbeat-*` from any prior session in the same
    PowerShell host. Cross-process orphans (jobs whose parent shell is
    truly dead) cannot be reached from a separate process because PS
    background jobs live in the parent runspace; this script only helps
    when the same host is reused.

    Also clears AGENT_BRIDGE_WAKE_JOB and AGENT_BRIDGE_HEARTBEAT_JOB
    process env vars so callers that depend on them see a clean slate.

.PARAMETER Agent
    Optional. If specified, only stops jobs matching `agent-bridge-*-<Agent>`.

.PARAMETER WhatIf
    Standard: report what would be stopped without doing it.

.EXAMPLE
    PS> .\.agent-bridge\bin\Stop-AgentBridgeSession.ps1
    Stops all agent-bridge-* jobs in the current host.

.EXAMPLE
    PS> .\.agent-bridge\bin\Stop-AgentBridgeSession.ps1 -Agent claude
    Stops only the claude wake/heartbeat jobs.
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [string] $Agent = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$sessionIdentity = Join-Path $PSScriptRoot 'AgentBridgeSessionIdentity.ps1'
. $sessionIdentity
$boundSessionAgent = [Environment]::GetEnvironmentVariable(
    'AGENT_BRIDGE_AGENT',
    'Process'
)
$effectiveAgent = $Agent
if ([string]::IsNullOrWhiteSpace($effectiveAgent) -and
    -not [string]::IsNullOrWhiteSpace($boundSessionAgent)) {
    $effectiveAgent = [string]$boundSessionAgent
}
if (-not [string]::IsNullOrWhiteSpace($effectiveAgent)) {
    Assert-AgentBridgeSessionIdentity -RequestedAgent $effectiveAgent
    $Agent = $effectiveAgent
}
$ErrorActionPreference = 'Continue'

$pattern = if ($Agent) {
    "agent-bridge-*-$Agent"
} else {
    'agent-bridge-*'
}

$stopped = 0
$jobs = @(Get-Job -Name $pattern -ErrorAction SilentlyContinue)
foreach ($job in $jobs) {
    if ($PSCmdlet.ShouldProcess($job.Name, 'Stop + Remove agent-bridge job')) {
        try {
            Stop-Job -Job $job -ErrorAction SilentlyContinue
            Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
            $stopped++
            Write-Host "  stopped: $($job.Name) (id=$($job.Id))" -ForegroundColor Yellow
        } catch {
            Write-Warning "Stop-AgentBridgeSession: could not stop '$($job.Name)': $($_.Exception.Message)"
        }
    }
}

# Clear the env vars regardless of whether jobs were stopped — they may
# point at jobs in a now-dead parent process which we cannot touch, but
# clearing them prevents downstream tooling from waving a stale id around.
$isBoundSessionAgent = (
    -not [string]::IsNullOrWhiteSpace($boundSessionAgent) -and
    $Agent -ceq $boundSessionAgent
)
if (-not $Agent -or $isBoundSessionAgent -or $Agent -eq 'claude' -or $Agent -eq 'codex' -or $Agent -eq 'operator' -or $Agent -eq 'system') {
    if ($PSCmdlet.ShouldProcess(
            'AGENT_BRIDGE_WAKE_JOB / AGENT_BRIDGE_HEARTBEAT_JOB',
            'Clear process env vars'
        )) {
        Remove-Item Env:AGENT_BRIDGE_WAKE_JOB -ErrorAction SilentlyContinue
        Remove-Item Env:AGENT_BRIDGE_HEARTBEAT_JOB -ErrorAction SilentlyContinue
    }
}

[pscustomobject]@{
    pattern = $pattern
    stopped = $stopped
    note    = if ($stopped -eq 0) {
        'no agent-bridge jobs found in this host (clean state, or jobs are in another process)'
    } else {
        "$stopped agent-bridge job(s) stopped + removed"
    }
}
