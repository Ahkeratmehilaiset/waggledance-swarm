#requires -Version 5.1
<#
.SYNOPSIS
    Send a liveness or heartbeat event to the bridge.

.DESCRIPTION
    Continuity-protocol helper added 2026-05-09. Wraps Write-AgentEvent
    with the liveness / heartbeat event types so an agent can declare
    "I am awake / I just sent a turn / I am about to sleep" without
    rebuilding the parameter set every time.

    Intended use:

      # at session start (right after Read-AgentBridge.ps1)
      .\bin\Send-Liveness.ps1 -Agent claude -State active

      # while running long work, every 60s
      .\bin\Send-Liveness.ps1 -Agent claude -State active `
                              -Message "fix-impl in progress, 30 of 50 pass"

      # at session end
      .\bin\Send-Liveness.ps1 -Agent claude -State sleeping `
                              -Message "Claude turn done; PR #124 pushed"

      # wake another agent
      .\bin\Send-Liveness.ps1 -Agent claude -Wake -To codex `
                              -Severity high `
                              -Message "PR #124 fix branch ready for re-review" `
                              -TaskId "wake-codex-for-pr124-rereview"

    The -Wake switch emits a `wake_request` event instead of a plain
    liveness event. Severity defaults to "medium".
#>
[CmdletBinding(DefaultParameterSetName = 'Liveness')]
param(
    [Parameter(Mandatory)] [string] $Agent,

    [Parameter(ParameterSetName = 'Liveness')]
    [ValidateSet('active','sleeping')] [string] $State = 'active',

    [Parameter(ParameterSetName = 'Heartbeat')]
    [switch] $Heartbeat,

    [Parameter(ParameterSetName = 'Wake')]
    [switch] $Wake,

    [string] $Message = '',
    [string] $TaskId = '',
    [string] $To = '',
    [string] $Severity = '',
    [string[]] $Paths = @(),
    [string] $Role = '',
    [string] $AgentUuid = '',
    [string[]] $Capabilities = @()
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$sessionIdentity = Join-Path $PSScriptRoot 'AgentBridgeSessionIdentity.ps1'
. $sessionIdentity
Assert-AgentBridgeSessionIdentity -RequestedAgent $Agent

$writeEventScript = Join-Path $PSScriptRoot 'Write-AgentEvent.ps1'

$type = 'liveness'
$status = $State
if ($Heartbeat) {
    $type = 'heartbeat'
    $status = 'active'
    if (-not $Severity) { $Severity = '' }
}
if ($Wake) {
    $type = 'wake_request'
    if (-not $Severity) { $Severity = 'medium' }
    $status = 'open'
    if (-not $To) {
        throw "wake_request requires -To <agent>"
    }
}

$ownerContext = $null
if ($type -in @('liveness','heartbeat') -and $status -eq 'active') {
    # Resolve and validate before emitting an active event. The event may
    # describe this session, but it must never refresh another generation's
    # claim merely because both sessions use the same public agent lane.
    $ownerContext = Get-AgentBridgeClaimOwnerContext
}

# Default heartbeat / liveness messages so the bridge is readable
# even when the caller passes no -Message.
if (-not $Message) {
    $Message = switch ($type) {
        'liveness'     { "$Agent $State" }
        'heartbeat'    { "$Agent active heartbeat" }
        'wake_request' { "$Agent requesting wake of $To" }
        default        { "$Agent $type" }
    }
}

# Default TaskId so the bridge is groupable per session.
if (-not $TaskId) {
    $TaskId = switch ($type) {
        'liveness'     { "{0}-liveness-{1:yyyy-MM-dd}" -f $Agent, (Get-Date) }
        'heartbeat'    { "{0}-heartbeat-{1:yyyy-MM-dd}" -f $Agent, (Get-Date) }
        'wake_request' { "{0}-wake-{1}-{2:yyyy-MM-dd-HH-mm-ss}" -f `
                            $Agent, $To, (Get-Date) }
        default        { "{0}-{1}-{2:yyyy-MM-dd}" -f $Agent, $type, (Get-Date) }
    }
}

$claimMutationLock = $null
if ($type -in @('liveness','heartbeat') -and $status -eq 'active') {
    $activeBridgeRoot = if ($env:AGENT_BRIDGE_RUNTIME_ROOT) {
        [string]$env:AGENT_BRIDGE_RUNTIME_ROOT
    } else {
        Split-Path -Parent $PSScriptRoot
    }
    $activeClaimsDir = Join-Path (
        Join-Path $activeBridgeRoot 'work_queue'
    ) 'claims'
    $claimMutationLock = Enter-AgentBridgeMutationLock `
        -BridgeRoot $activeBridgeRoot
}
try {
& $writeEventScript `
    -Agent $Agent `
    -Type $type `
    -Status $status `
    -Severity $Severity `
    -To $To `
    -Message $Message `
    -TaskId $TaskId `
    -Paths $Paths `
    -Role $Role `
    -AgentUuid $AgentUuid `
    -Capabilities $Capabilities

# R15: bump last_heartbeat_utc on this agent's active claims so
# Invoke-StaleClaimSweep.ps1 doesn't auto-release them. Only
# liveness/active and heartbeat/active extend the lease — a
# liveness/sleeping or wake_request does NOT keep the claim alive
# (that would defeat the whole point of the lease).
if ($type -in @('liveness','heartbeat') -and $status -eq 'active') {
    # Resolve runtime root the same way other bridge scripts do
    # (R13 AGENT_BRIDGE_RUNTIME_ROOT support). Inlined here to
    # keep Send-Liveness self-contained.
    $bridgeRoot = if ($env:AGENT_BRIDGE_RUNTIME_ROOT) {
        [string]$env:AGENT_BRIDGE_RUNTIME_ROOT
    } else {
        Split-Path -Parent $PSScriptRoot
    }
    $claimsDir = Join-Path (Join-Path $bridgeRoot 'work_queue') 'claims'
    if (Test-Path -LiteralPath $claimsDir -PathType Container) {
        $heartbeatNow = (Get-Date).ToUniversalTime()
        $heartbeatTs = $heartbeatNow.ToString('o')
        $claimGroups = [System.Collections.Generic.Dictionary[
            string,
            System.Collections.Generic.List[object]
        ]]::new([System.StringComparer]::Ordinal)
        foreach ($file in @(Get-ChildItem -Path $claimsDir -Filter '*.json' `
                                           -File -ErrorAction SilentlyContinue)) {
            try {
                $obj = ConvertFrom-AgentBridgeJson -Json (
                    Get-Content -Raw -Path $file.FullName -Encoding UTF8
                )
            } catch { continue }

            if (
                -not $obj.PSObject.Properties['task_id'] -or
                $obj.task_id -isnot [string]
            ) { continue }
            $claimTaskId = [string]$obj.task_id
            if ([string]::IsNullOrEmpty($claimTaskId)) { continue }
            if (-not $claimGroups.ContainsKey($claimTaskId)) {
                $claimGroups.Add(
                    $claimTaskId,
                    [System.Collections.Generic.List[object]]::new()
                )
            }
            $claimGroups[$claimTaskId].Add(
                [pscustomobject]@{
                    file = $file
                    claim = $obj
                }
            )
        }

        foreach ($claimTaskId in @($claimGroups.Keys)) {
            $entries = $claimGroups[$claimTaskId]
            if ($entries.Count -ne 1) {
                $duplicatePaths = @(
                    $entries | ForEach-Object { $_.file.FullName }
                )
                Write-Warning (
                    "duplicate active claim records for exact task_id '{0}'; lease refresh skipped: {1}" -f
                    $claimTaskId,
                    ($duplicatePaths -join ', ')
                )
                continue
            }

            $entry = $entries[0]
            $file = $entry.file
            $obj = $entry.claim
            if ([string]$obj.agent -cne $Agent) { continue }
            if (-not (Test-AgentBridgeClaimOwner `
                    -Claim $obj `
                    -OwnerContext $ownerContext)) {
                continue
            }
            # Bump field, preserving claim shape.
            if ($obj.PSObject.Properties['last_heartbeat_utc']) {
                $obj.last_heartbeat_utc = $heartbeatTs
            } else {
                $obj | Add-Member -NotePropertyName last_heartbeat_utc `
                    -NotePropertyValue $heartbeatTs -Force
            }
            $leaseSeconds = if (
                $obj.PSObject.Properties['lease_seconds']
            ) {
                ConvertTo-BridgePositiveInt32 -Value $obj.lease_seconds
            } else {
                0
            }
            if ($leaseSeconds -le 0) { $leaseSeconds = 900 }
            $obj | Add-Member -NotePropertyName lease_seconds `
                -NotePropertyValue $leaseSeconds -Force
            $expiresAt = try {
                $heartbeatNow.AddSeconds($leaseSeconds)
            } catch [System.ArgumentOutOfRangeException] {
                [DateTime]::SpecifyKind(
                    [DateTime]::MaxValue,
                    [DateTimeKind]::Utc
                )
            }
            $obj | Add-Member -NotePropertyName claim_lease_expires_utc `
                -NotePropertyValue $expiresAt.ToString('o') -Force
            try {
                $json = ($obj | ConvertTo-Json -Depth 8)
                $tmp = "$($file.FullName).tmp.$PID.$([guid]::NewGuid().ToString('N'))"
                $encoding = New-Object System.Text.UTF8Encoding($false)
                [System.IO.File]::WriteAllText($tmp, $json, $encoding)
                Move-Item -LiteralPath $tmp -Destination $file.FullName `
                    -Force -ErrorAction Stop
            } catch {
                # Lease bump is best-effort; failure here just
                # means the next heartbeat will retry. Don't fail
                # the liveness emit because of a lease-update glitch.
                Write-Warning ("could not bump lease for {0}: {1}" -f `
                    $file.Name, $_.Exception.Message)
            }
        }
    }
}
} finally {
    Exit-AgentBridgeMutationLock -Lock $claimMutationLock
}
