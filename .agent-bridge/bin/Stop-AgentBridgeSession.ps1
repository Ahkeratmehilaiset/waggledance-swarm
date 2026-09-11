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
    [ValidateScript({ $_ -eq '' -or $_ -cmatch '^[a-z][a-z0-9_-]{1,32}$' })]
    [string] $Agent = ''
)

$ErrorActionPreference = 'Continue'
Set-StrictMode -Version Latest

# B7: the one shared claim-lease / session-heartbeat implementation.
. (Join-Path $PSScriptRoot 'ClaimLeaseHeartbeat.ps1')

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
if (-not $Agent -or $Agent -eq 'claude' -or $Agent -eq 'codex' -or $Agent -eq 'operator' -or $Agent -eq 'system') {
    if ($PSCmdlet.ShouldProcess(
            'AGENT_BRIDGE_WAKE_JOB / AGENT_BRIDGE_HEARTBEAT_JOB',
            'Clear process env vars'
        )) {
        Remove-Item Env:AGENT_BRIDGE_WAKE_JOB -ErrorAction SilentlyContinue
        Remove-Item Env:AGENT_BRIDGE_HEARTBEAT_JOB -ErrorAction SilentlyContinue
    }
}

# B7: retire this session's durable heartbeat and drop the owner token.
# Stopping the jobs is not enough on its own: while the artifact is
# inside its TTL the sweeper would keep treating this session's claims as
# live work, so a clean stop must free them immediately rather than make
# peers wait out the TTL. Dropping the token also means nothing left in
# this process can extend a lease afterwards.
$retiredSessionHeartbeat = $false
$sessionRoot = if ($env:AGENT_BRIDGE_RUNTIME_ROOT) {
    [string]$env:AGENT_BRIDGE_RUNTIME_ROOT
} else {
    Split-Path -Parent $PSScriptRoot
}
$stopSessionId = if ($env:AGENT_BRIDGE_RUN_ID) { [string]$env:AGENT_BRIDGE_RUN_ID } else { '' }
if ($stopSessionId -and $PSCmdlet.ShouldProcess($stopSessionId, 'Retire session heartbeat')) {
    try {
        # Identity-checked removal: stopping this session must not be
        # able to delete a successor session's artifact.
        $stopIdentity = Get-BridgeOwnerIdentity -SessionId $stopSessionId
        $retiredSessionHeartbeat = [bool](Remove-BridgeSessionHeartbeat `
            -Root $sessionRoot -SessionId $stopSessionId -Identity $stopIdentity)
    } catch {
        Write-Warning ("Stop-AgentBridgeSession: could not retire session heartbeat: {0}" -f `
            $_.Exception.Message)
    }
    Remove-Item Env:AGENT_BRIDGE_OWNER_TOKEN -ErrorAction SilentlyContinue
}

[pscustomobject]@{
    pattern = $pattern
    stopped = $stopped
    session_heartbeat_retired = $retiredSessionHeartbeat
    note    = if ($stopped -eq 0) {
        'no agent-bridge jobs found in this host (clean state, or jobs are in another process)'
    } else {
        "$stopped agent-bridge job(s) stopped + removed"
    }
}
