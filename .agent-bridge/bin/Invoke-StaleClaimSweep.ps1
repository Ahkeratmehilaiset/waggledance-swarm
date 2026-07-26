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
$transactionScript = Join-Path $PSScriptRoot 'WorkQueueTransaction.ps1'
if (-not (Test-Path -LiteralPath $transactionScript -PathType Leaf)) {
    throw "work-queue transaction helper not found: $transactionScript"
}
. $transactionScript

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
$workQueueTransaction = Enter-WaggleDanceWorkQueueTransaction `
    -BridgeRoot $bridgeRoot
$sweptRecords = New-Object System.Collections.Generic.List[object]

try {
Assert-WaggleDanceWorkQueueClaimSet -ClaimsDirectory $claimsDir
foreach ($file in @(Get-ChildItem -Path $claimsDir -Filter '*.json' -File `
        -ErrorAction SilentlyContinue)) {
    try {
        $claim = Get-Content -Raw -Path $file.FullName -Encoding UTF8 |
            ConvertFrom-Json
    } catch { continue }

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

    $claimJson = ($claim | ConvertTo-Json -Depth 8)
    $encoding = New-Object System.Text.UTF8Encoding($false)
    $tmpDone = "$donePath.tmp.$PID.$([guid]::NewGuid().ToString('N'))"
    $backupDone = "$donePath.bak.$PID.$([guid]::NewGuid().ToString('N'))"
    $movedToDone = $false
    try {
        # Move the original active generation first. A later metadata failure
        # can leave an audit-poor terminal file, but never a terminal-stamped
        # active generation that a heartbeat could resurrect.
        [System.IO.File]::Move($file.FullName, $donePath)
        $movedToDone = $true
        if (
            [Environment]::GetEnvironmentVariable(
                'AGENT_BRIDGE_TEST_WORK_QUEUE_TERMINAL_METADATA_FAILURE',
                'Process'
            ) -eq '1'
        ) {
            throw 'simulated terminal metadata update failure'
        }
        [System.IO.File]::WriteAllText($tmpDone, $claimJson, $encoding)
        [System.IO.File]::Replace($tmpDone, $donePath, $backupDone)
        try { Remove-Item -LiteralPath $backupDone -Force -ErrorAction SilentlyContinue } catch {}
    } catch {
        try { Remove-Item -LiteralPath $tmpDone -Force -ErrorAction SilentlyContinue } catch {}
        try { Remove-Item -LiteralPath $backupDone -Force -ErrorAction SilentlyContinue } catch {}
        if (-not $movedToDone) {
            Write-Warning ("could not archive stale claim {0}: {1}" -f `
                $file.Name, $_.Exception.Message)
            continue
        }
        Write-Warning (
            "claim reached done/ and stale release committed, but metadata update failed for {0}: {1}" -f
            $file.Name,
            $_.Exception.Message
        )
    }

    [void]$sweptRecords.Add([pscustomobject]@{
        task_id        = [string]$claim.task_id
        agent          = $agent
        age_seconds    = [int]$ageSeconds
        archived_path  = $donePath
        last_heartbeat_utc = $tsString
        stale_threshold_s = $effectiveLeaseSeconds
        claim_lease_seconds = $claimLeaseSeconds
        claim_lease_expires_utc = $effectiveExpiresUtc.ToString('o')
    })
}
} finally {
    Exit-WaggleDanceWorkQueueTransaction -Transaction $workQueueTransaction
}

foreach ($record in $sweptRecords) {
    # AppendV1 is intentionally acquired only after WorkQueueV1 is released.
    try {
        $writeEvent = Join-Path $PSScriptRoot 'Write-AgentEvent.ps1'
        if (Test-Path -LiteralPath $writeEvent -PathType Leaf) {
            $payload = [pscustomobject]@{
                task_id            = $record.task_id
                claim_agent        = $record.agent
                last_heartbeat_utc = $record.last_heartbeat_utc
                age_seconds        = $record.age_seconds
                stale_threshold_s  = $record.stale_threshold_s
                claim_lease_seconds = $record.claim_lease_seconds
                claim_lease_expires_utc = $record.claim_lease_expires_utc
                archived_path      = $record.archived_path
                swept_by           = $env:AGENT_BRIDGE_RUN_ID
            }
            $payloadJson = ($payload | ConvertTo-Json -Depth 6 -Compress)
            & $writeEvent `
                -Agent system `
                -Type release `
                -Status stale_lease `
                -Severity medium `
                -TaskId $record.task_id `
                -Message ("auto-released stale claim by $($record.agent) (heartbeat $($record.age_seconds)s old)") `
                -PayloadJson $payloadJson | Out-Null
        }
    } catch {
        Write-Warning ("stale-lease release event emit failed: {0}" -f `
            $_.Exception.Message)
    }

    if (-not $Quiet) {
        Write-Host ("STALE LEASE SWEPT: {0} by {1} (heartbeat {2}s old)" -f `
            $record.task_id, $record.agent, $record.age_seconds) `
            -ForegroundColor Yellow
    }

    [pscustomobject]@{
        task_id       = $record.task_id
        agent         = $record.agent
        age_seconds   = $record.age_seconds
        archived_path = $record.archived_path
    }
}
