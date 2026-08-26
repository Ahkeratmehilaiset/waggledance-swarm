#requires -Version 5.1
<#
.SYNOPSIS
    Auto-release agent-bridge claims whose heartbeat is older than
    the lease threshold.

.DESCRIPTION
    R15 (operator gate 2026-05-09T~14:50Z): claim leases must
    survive a 5-minute stale test - if an agent stops heart-beating
    on its claim, the claim must auto-release so the other agent
    can move forward without waiting for an operator paste-relay.

    This script scans .agent-bridge/work_queue/claims/*.json,
    compares each claim's last_heartbeat_utc to "now", and for
    claims older than -StaleSeconds:
      1. archives the claim file to work_queue/done/ as
         <task>.<utc-stamp>.stale_lease.json with the original
         claim plus a release_status="stale_lease" stamp;
      2. emits a release/stale_lease bridge event so audit
         consumers see the auto-release, not a silent drop.

    operator/system claims are never swept (those are privileged
    and may legitimately outlive the lease).

    Returns the list of swept claims as objects with task_id,
    agent, age_seconds, archived_path. An empty array means
    nothing was stale.

.PARAMETER StaleSeconds
    Lease threshold. Defaults to AGENT_BRIDGE_STALE_LEASE_SECONDS
    env var, then 300s (5 min). Pass a smaller value (e.g. 1) for
    smoke tests.

.PARAMETER Quiet
    Suppress per-claim Write-Host output. The returned object list
    is unaffected.

.EXAMPLE
    .\.agent-bridge\bin\Invoke-StaleClaimSweep.ps1
    # Default 5-min threshold; emits release events for any
    # claim with last_heartbeat_utc older than 5 min.

.EXAMPLE
    .\.agent-bridge\bin\Invoke-StaleClaimSweep.ps1 -StaleSeconds 1
    # Aggressive sweep; useful for smoke tests where we need
    # immediate auto-release.
#>
[CmdletBinding()]
param(
    [int] $StaleSeconds = 0,
    [switch] $Quiet
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# B7: the one shared claim-lease / session-heartbeat implementation.
# Every lease writer goes through it, so there is a single CAS to review.
. (Join-Path $PSScriptRoot 'ClaimLeaseHeartbeat.ps1')

# R13: honor AGENT_BRIDGE_RUNTIME_ROOT.
$bridgeRoot = if ($env:AGENT_BRIDGE_RUNTIME_ROOT) {
    [string]$env:AGENT_BRIDGE_RUNTIME_ROOT
} else {
    Split-Path -Parent $PSScriptRoot
}
if (-not (Test-Path -LiteralPath $bridgeRoot -PathType Container)) {
    [void](New-Item -ItemType Directory -Path $bridgeRoot -Force `
        -ErrorAction Stop)
}

if ($StaleSeconds -le 0) {
    if ($env:AGENT_BRIDGE_STALE_LEASE_SECONDS) {
        $parsed = 0
        if ([int]::TryParse(
                [string]$env:AGENT_BRIDGE_STALE_LEASE_SECONDS,
                [ref]$parsed) -and $parsed -gt 0) {
            $StaleSeconds = $parsed
        }
    }
    if ($StaleSeconds -le 0) { $StaleSeconds = 300 }
}

$claimsDir = Join-Path (Join-Path $bridgeRoot 'work_queue') 'claims'
$doneDir = Join-Path (Join-Path $bridgeRoot 'work_queue') 'done'

function ConvertTo-BridgeUtc {
    param([object] $Value)

    if ($null -eq $Value) { return $null }
    if ($Value -is [DateTime]) {
        return ([DateTime]$Value).ToUniversalTime()
    }

    $text = [string]$Value
    if (-not $text) { return $null }

    try {
        return [DateTime]::Parse(
            $text,
            [System.Globalization.CultureInfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::AssumeUniversal -bor
                [System.Globalization.DateTimeStyles]::AdjustToUniversal
        ).ToUniversalTime()
    } catch {
        try {
            return [DateTime]::Parse($text).ToUniversalTime()
        } catch {
            return $null
        }
    }
}

# Emit zero or more swept-claim records into the pipeline; caller
# wraps with @(...) to always get an array. Avoid the
# Generic.List + return-comma trick that PSStrictMode's boolean
# coercion can fail on (Codex finding 2026-05-09T12:26Z applied
# to this script too).
if (-not (Test-Path -LiteralPath $claimsDir -PathType Container)) {
    return
}
if (-not (Test-Path -LiteralPath $doneDir -PathType Container)) {
    [void](New-Item -ItemType Directory -Path $doneDir -Force -ErrorAction Stop)
}

$now = (Get-Date).ToUniversalTime()

foreach ($file in @(Get-ChildItem -Path $claimsDir -Filter '*.json' -File `
        -ErrorAction SilentlyContinue)) {
    # B7: take the same per-claim lock the keepalive and release use and
    # re-read under it, so a sweep decision is never made from content
    # another writer is part-way through replacing.
    $claimLock = Enter-BridgeClaimLock -ClaimPath $file.FullName
    if ($null -eq $claimLock) { continue }
    try {
    if (-not (Test-Path -LiteralPath $file.FullName -PathType Leaf)) { continue }
    $claim = $null
    try {
        $claim = Get-Content -Raw -Path $file.FullName -Encoding UTF8 |
            ConvertFrom-Json -ErrorAction Stop
    } catch { $claim = $null }
    if ($null -eq $claim) {
        # B7: an unreadable claim used to be skipped forever, so one
        # corrupt file could hold a write scope hostage indefinitely.
        # Quarantine it instead: the scope frees and the bytes are kept
        # for forensics rather than deleted.
        $badStamp = $now.ToString('yyyyMMddTHHmmssZ')
        $badSafe = ($file.BaseName -replace '[^A-Za-z0-9._-]', '_').Trim('_')
        $badPath = Join-Path $doneDir ("$badSafe.$badStamp.unreadable.json")
        try {
            [System.IO.File]::Move($file.FullName, $badPath)
            if (-not $Quiet) {
                Write-Host ("UNREADABLE CLAIM QUARANTINED: {0}" -f $file.Name) `
                    -ForegroundColor Yellow
            }
            [pscustomobject]@{
                task_id       = ''
                agent         = ''
                age_seconds   = 0
                archived_path = $badPath
            }
        } catch {
            Write-Warning ("could not quarantine unreadable claim {0}: {1}" -f `
                $file.Name, $_.Exception.Message)
        }
        continue
    }

    $agent = [string]$claim.agent
    if ($agent -in @('operator','system')) { continue }

    # Resolve last_heartbeat_utc with fallback to claimed_at_utc
    # for backward compat with claims created before R15.
    $tsString = ''
    if ($claim.PSObject.Properties['last_heartbeat_utc'] -and `
        [string]$claim.last_heartbeat_utc) {
        $tsString = [string]$claim.last_heartbeat_utc
    } elseif ($claim.PSObject.Properties['claimed_at_utc']) {
        $tsString = [string]$claim.claimed_at_utc
    }
    if (-not $tsString) { continue }

    $ts = ConvertTo-BridgeUtc -Value $claim.last_heartbeat_utc
    if ($null -eq $ts -and $claim.PSObject.Properties['claimed_at_utc']) {
        $ts = ConvertTo-BridgeUtc -Value $claim.claimed_at_utc
    }
    if ($null -eq $ts) { continue }
    $tsString = $ts.ToString('o')

    $ageSeconds = ($now - $ts).TotalSeconds
    $claimLeaseSeconds = $StaleSeconds
    if ($claim.PSObject.Properties['lease_seconds']) {
        $parsedLease = 0
        if ([int]::TryParse([string]$claim.lease_seconds, [ref]$parsedLease) -and
            $parsedLease -gt 0) {
            $claimLeaseSeconds = $parsedLease
        }
    }

    $effectiveExpiresUtc = $ts.AddSeconds($claimLeaseSeconds)
    if ($claim.PSObject.Properties['claim_lease_expires_utc'] -and
        [string]$claim.claim_lease_expires_utc) {
        $claimExpiresUtc = ConvertTo-BridgeUtc -Value $claim.claim_lease_expires_utc
        if ($null -ne $claimExpiresUtc -and $claimExpiresUtc -gt $effectiveExpiresUtc) {
            $effectiveExpiresUtc = $claimExpiresUtc
        }
    }
    $effectiveLeaseSeconds = [int][Math]::Ceiling(($effectiveExpiresUtc - $ts).TotalSeconds)
    if ($effectiveLeaseSeconds -lt 1) { $effectiveLeaseSeconds = 1 }
    if ($now -lt $effectiveExpiresUtc) { continue }

    # B7: expiry alone is no longer sufficient. A claim whose owning
    # session is still beating is live work, not a leak. The check binds
    # owner_session_id plus owner_token_sha256; the recorded pid is
    # deliberately never consulted, because pids are recycled.
    if (Test-BridgeSessionHeartbeatLive -Root $bridgeRoot -Claim $claim `
            -NowUtc $now) {
        continue
    }

    # Stale: archive the claim file to done/ with a stale_lease
    # stamp and emit a release/stale_lease event.
    $stamp = $now.ToString('yyyyMMddTHHmmssZ')
    $safeTask = ($file.BaseName -replace '[^A-Za-z0-9._-]', '_').Trim('_')
    $donePath = Join-Path $doneDir ("$safeTask.$stamp.stale_lease.json")

    $claim | Add-Member -NotePropertyName released_at_utc `
        -NotePropertyValue $now.ToString('o') -Force
    $claim | Add-Member -NotePropertyName release_status `
        -NotePropertyValue 'stale_lease' -Force
    $claim | Add-Member -NotePropertyName release_reason `
        -NotePropertyValue ("last_heartbeat_utc was $([int]$ageSeconds)s old; lease threshold $effectiveLeaseSeconds s") `
        -Force

    # B7: the same Replace-then-Move recipe Release-AgentTask.ps1 uses -
    # stamp the claim in place, then move it. There is never a moment
    # where both files exist or neither does, and the claim file is not
    # recreated afterwards.
    try {
        $sweepEncoding = New-Object System.Text.UTF8Encoding($false)
        $sweepTmp = "$($file.FullName).tmp.$PID.$([guid]::NewGuid().ToString('N'))"
        # Real backup path: PowerShell marshals $null to an empty string
        # and File.Replace rejects that.
        $sweepBackup = "$($file.FullName).bak.$PID.$([guid]::NewGuid().ToString('N'))"
        [System.IO.File]::WriteAllText(
            $sweepTmp,
            ((ConvertTo-BridgeIsoTimestamps -Object $claim) |
                ConvertTo-Json -Depth 8),
            $sweepEncoding)
        [System.IO.File]::Replace($sweepTmp, $file.FullName, $sweepBackup)
        try { Remove-Item -LiteralPath $sweepBackup -Force -ErrorAction SilentlyContinue } catch {}
        [System.IO.File]::Move($file.FullName, $donePath)
    } catch {
        Write-Warning ("could not archive stale claim {0}: {1}" -f `
            $file.Name, $_.Exception.Message)
        continue
    }

    # Emit release event (best-effort; lease sweep must not fail
    # because the bridge writer is momentarily contended).
    try {
        $writeEvent = Join-Path $PSScriptRoot 'Write-AgentEvent.ps1'
        if (Test-Path -LiteralPath $writeEvent -PathType Leaf) {
            $payload = [pscustomobject]@{
                task_id            = [string]$claim.task_id
                claim_agent        = $agent
                last_heartbeat_utc = $tsString
                age_seconds        = [int]$ageSeconds
                stale_threshold_s  = $effectiveLeaseSeconds
                claim_lease_seconds = $claimLeaseSeconds
                claim_lease_expires_utc = $effectiveExpiresUtc.ToString('o')
                archived_path      = $donePath
                swept_by           = $env:AGENT_BRIDGE_RUN_ID
            }
            $payloadJson = ($payload | ConvertTo-Json -Depth 6 -Compress)
            & $writeEvent `
                -Agent system `
                -Type release `
                -Status stale_lease `
                -Severity medium `
                -TaskId ([string]$claim.task_id) `
                -Message ("auto-released stale claim by $agent (heartbeat $([int]$ageSeconds)s old)") `
                -PayloadJson $payloadJson | Out-Null
        }
    } catch {
        Write-Warning ("stale-lease release event emit failed: {0}" -f `
            $_.Exception.Message)
    }

    if (-not $Quiet) {
        Write-Host ("STALE LEASE SWEPT: {0} by {1} (heartbeat {2}s old)" -f `
            [string]$claim.task_id, $agent, [int]$ageSeconds) `
            -ForegroundColor Yellow
    }

    # Emit into pipeline (caller wraps with @(...)).
    [pscustomobject]@{
        task_id        = [string]$claim.task_id
        agent          = $agent
        age_seconds    = [int]$ageSeconds
        archived_path  = $donePath
    }
    } finally {
        Exit-BridgeClaimLock -Lock $claimLock
    }
}
