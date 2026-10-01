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

# F23a (RCO1 2026-10-01): a stale release that did not publish must stay visible.
$script:SweepEmitFailureSchema = 'wd.bridge-sweep-emit-failure.v1'

function Test-StaleSweepCancellation {
    # True when the exception, or any exception it wraps, is a cancellation or a stopped pipeline.
    param([System.Exception] $Exception)

    for ($current = $Exception; $null -ne $current; $current = $current.InnerException) {
        if ($current -is [System.OperationCanceledException] -or
            $current -is [System.Management.Automation.PipelineStoppedException]) {
            return $true
        }
    }
    return $false
}

function Write-StaleSweepEmitFailure {
    # F23a (RCO1 2026-10-01): an archived claim whose release event was not published leaves one
    # bounded receipt in work_queue/sweep_emit_failures. It is written under a temporary name and
    # renamed, so a reader sees the whole receipt or none. Nothing replays the event or restores the
    # claim. No lock is held here. Throws, naming the task and archive, when it cannot be persisted.
    # Crash window (explicit, not closed): a process that ends after the archive and before this
    # rename leaves only the done file, as before.
    param(
        [Parameter(Mandatory)] [string] $BridgeRoot,
        [Parameter(Mandatory)] [object] $Archived,
        [Parameter(Mandatory)] [string] $Phase,
        [Parameter(Mandatory)] [ValidateSet('failed', 'publication_unknown')] [string] $Publication,
        [string] $ErrorText
    )

    $invariant = [System.Globalization.CultureInfo]::InvariantCulture
    $folder = Join-Path (Join-Path $BridgeRoot 'work_queue') 'sweep_emit_failures'
    $observed = (Get-Date).ToUniversalTime()
    $text = if ($ErrorText) { [string]$ErrorText } else { $Phase }
    if ($text.Length -gt 1000) { $text = $text.Substring(0, 1000) }
    $taskId = [string]$Archived.Claim.task_id
    $receipt = [ordered]@{
        schema          = $script:SweepEmitFailureSchema
        task_id         = $taskId
        claim_agent     = [string]$Archived.Agent
        archived_name   = [System.IO.Path]::GetFileName([string]$Archived.DonePath)
        archived_path   = [string]$Archived.DonePath
        observed_at_utc = $observed.ToString('o', $invariant)
        phase           = $Phase
        publication     = $Publication
        error           = $text
        swept_by        = [string]$env:AGENT_BRIDGE_RUN_ID
    }
    $name = $observed.ToString('yyyyMMddTHHmmssfffZ', $invariant) + '-' + $PID + '-' +
        [guid]::NewGuid().ToString('N').Substring(0, 8) + '.json'
    $path = Join-Path $folder $name
    $tmp = $path + '.tmp'
    try {
        if (-not [System.IO.Directory]::Exists($folder)) {
            [void](New-Item -ItemType Directory -Path $folder -ErrorAction Stop)
        }
        [System.IO.File]::WriteAllText($tmp, ($receipt | ConvertTo-Json -Depth 4),
            (New-Object System.Text.UTF8Encoding($false)))
        [System.IO.File]::Move($tmp, $path)
    } catch {
        try { [System.IO.File]::Delete($tmp) } catch {}
        throw ('could not persist the sweep_emit_failures receipt for stale claim {0} (archived as {1}; release event {2}): {3}' -f
            $taskId, [string]$Archived.DonePath, $Publication, $_.Exception.Message)
    }
    return $path
}

function Get-StaleClaimDispatcher {
    # A task-id prefix is not evidence of who assigned the claim. Only an
    # addressed assignment whose responder identity matches the archived
    # claim may add the dispatcher to the release recipients.
    param([object] $Claim, [string] $BridgeRoot)

    if (-not $Claim.PSObject.Properties['agent_uuid'] -or
        -not $Claim.PSObject.Properties['owner_session_id']) { return $null }
    $eventsPath = Join-Path (Join-Path $BridgeRoot 'shared') 'events.jsonl'
    if (-not (Test-Path -LiteralPath $eventsPath -PathType Leaf)) { return $null }
    try {
        . (Join-Path $PSScriptRoot 'BridgeIncrementalReader.ps1')
        $snapshot = Read-BridgeEventTail -Path $eventsPath -MaxLines 5000
        if ($snapshot.status -in @('BLOCKED', 'RETRY')) { return $null }
        $candidateEvents = @()
        foreach ($event in @($snapshot.rows)) {
            if ([string]$event.type -cne 'wake_request' -or
                [string]$event.status -cne 'assigned' -or
                [string]$event.task_id -cne [string]$Claim.task_id) { continue }
            if (@(([string]$event.to -split ',') | ForEach-Object { $_.Trim() }) -cnotcontains
                [string]$Claim.agent) { continue }
            if (-not $event.PSObject.Properties['expected_responders']) { continue }
            $responder = $event.expected_responders.PSObject.Properties[[string]$Claim.agent]
            if ($null -eq $responder) { continue }
            if ([string]$responder.Value.agent_uuid -cne [string]$Claim.agent_uuid -or
                [string]$responder.Value.session_id -cne [string]$Claim.owner_session_id) { continue }
            $sender = [string]$event.agent
            # Legacy envelopes can lack these fields. Do not return one and
            # then dereference its missing members under StrictMode after the
            # claim has already been archived.
            if (-not $event.PSObject.Properties['request_id'] -or
                -not $event.PSObject.Properties['agent_uuid'] -or
                -not $event.PSObject.Properties['session_id']) { continue }
            if ($sender -cmatch '^[a-z][a-z0-9_-]{1,32}$' -and
                $sender -cne [string]$Claim.agent -and
                [string]$event.request_id -cmatch '^[A-Za-z0-9._:-]{1,128}$' -and
                [string]$event.agent_uuid -cmatch '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' -and
                [string]$event.session_id -cmatch '^[A-Za-z0-9._:-]{1,128}$') {
                $candidateEvents += $event
            }
        }
        # Replayed copies of one request are harmless; two distinct requests
        # from even the same sender are ambiguous, not permission to pick last.
        if ($candidateEvents.Count -eq 0) { return $null }
        $first = $candidateEvents[0]
        foreach ($candidate in $candidateEvents) {
            if ([string]$candidate.agent -cne [string]$first.agent -or
                [string]$candidate.request_id -cne [string]$first.request_id -or
                [string]$candidate.agent_uuid -cne [string]$first.agent_uuid -or
                [string]$candidate.session_id -cne [string]$first.session_id) {
                return $null
            }
        }
        return $first
    } catch {
        # Unknown or incomplete history cannot authorize a guessed recipient.
        return $null
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

# S2 (Lead 2026-09-30): the sweep archives claims, so it runs inside the v2
# queue's runtime-root mutex, taken before any claim or beat lock as in the
# Python queue. A busy or abandoned root throws with nothing read or changed;
# the opportunistic callers catch it and sweep on a later round.
# Liveness (RCO2 5416a7ab F1): only the decision and the archive run under
# the root and claim locks. The dispatcher lookup (an event-log tail read)
# and the release event are slow and unbounded, so they run after every lock
# is released, as Claim-AgentTask and Release-AgentTask write their events,
# in archive order; a concurrent claim never waits on them.
$archivedReleases = New-Object System.Collections.Generic.List[object]
try {
$rootMutex = Enter-BridgeQueueRootMutex -Root $bridgeRoot
$rootWorkDone = $false
try {
foreach ($file in @(Get-ChildItem -Path $claimsDir -Filter '*.json' -File `
        -ErrorAction SilentlyContinue)) {
    # B7: take the same per-claim lock the keepalive and release use and
    # re-read under it, so a sweep decision is never made from content
    # another writer is part-way through replacing.
    $claimLock = Enter-BridgeClaimLock -ClaimPath $file.FullName
    if ($null -eq $claimLock) { continue }
    $beatLock = $null
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

    # F8 fence (RCO1 2026-09-30): Write-BridgeSessionHeartbeat takes only the
    # beat's own sibling lock, never this claim lock. Hold that lock from the
    # liveness read through the archive, so a beat being written is awaited
    # and then seen; a beat lock still busy after the timeout decides nothing
    # this round (the claim stays).
    $beatPath = ''
    if ($claim.PSObject.Properties['owner_session_id'] -and
        $claim.PSObject.Properties['owner_token_sha256']) {
        $beatPath = Get-BridgeSessionHeartbeatPath -Root $bridgeRoot `
            -SessionId ([string]$claim.owner_session_id) `
            -TokenSha256 ([string]$claim.owner_token_sha256)
    }
    if ($beatPath) {
        $beatDir = Split-Path -Parent $beatPath
        if (-not (Test-Path -LiteralPath $beatDir -PathType Container)) {
            try {
                [void](New-Item -ItemType Directory -Path $beatDir -Force -ErrorAction Stop)
            } catch { continue }
        }
        $beatLock = Enter-BridgeClaimLock -ClaimPath $beatPath
        if ($null -eq $beatLock) { continue }
    }

    # B7: expiry alone is no longer sufficient. A claim whose owning
    # session is still beating is live work, not a leak. The check binds
    # owner_session_id plus owner_token_sha256; the recorded pid is
    # deliberately never consulted, because pids are recycled.
    # A-F1 (RCO1 2026-09-30): only a PROVEN not-live owner is swept; a beat
    # that exists but cannot be read or evaluated is 'unknown' and the
    # claim stays this round, as in the core sweeper.
    if ((Get-BridgeSessionHeartbeatLiveness -Root $bridgeRoot -Claim $claim `
            -NowUtc $now) -cne 'not_live') {
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
    # Decided and archived: the owner's writer may beat again.
    Exit-BridgeClaimLock -Lock $beatLock
    $beatLock = $null

    # Archived: the release event and the pipeline record follow once every
    # lock is released (below), with the values decided here.
    $archivedReleases.Add([pscustomobject]@{
        Claim = $claim; Agent = $agent; AgeSeconds = $ageSeconds; LastHeartbeat = $tsString
        ThresholdSeconds = $effectiveLeaseSeconds; LeaseSeconds = $claimLeaseSeconds
        ExpiresUtc = $effectiveExpiresUtc; DonePath = $donePath
    })
    } finally {
        Exit-BridgeClaimLock -Lock $beatLock
        Exit-BridgeClaimLock -Lock $claimLock
    }
}
$rootWorkDone = $true
} finally {
    Exit-BridgeQueueRootMutex -Mutex $rootMutex -Completed:$rootWorkDone
}
} finally {
# Every archive above gets its event and record, in archive order, even when
# the loop or the root release failed afterwards; no lock is held here.
# F23a: a release that is not published leaves a receipt (Write-StaleSweepEmitFailure);
# a receipt that cannot be persisted fails the sweep after every archive is handled, and a
# cancellation is recorded, the remaining archives get receipts unpublished, then it propagates.
$receiptFailures = New-Object System.Collections.Generic.List[string]
$cancellation = $null
foreach ($archived in $archivedReleases) {
    $claim = $archived.Claim
    $agent = $archived.Agent
    $ageSeconds = $archived.AgeSeconds
    $tsString = $archived.LastHeartbeat
    $effectiveLeaseSeconds = $archived.ThresholdSeconds
    $claimLeaseSeconds = $archived.LeaseSeconds
    $effectiveExpiresUtc = $archived.ExpiresUtc
    $donePath = $archived.DonePath

    # Emit the release event. A writer that throws, is absent or is cancelled never counts as
    # published: the archive stays and a receipt records the outcome (F23a).
    $publication = 'published'
    $phase = 'prepare'
    $errorText = ''
    $cancelled = $false
    try {
        $writeEvent = Join-Path $PSScriptRoot 'Write-AgentEvent.ps1'
        if ($null -ne $cancellation) {
            $publication = 'failed'
            $phase = 'cancelled'
            $errorText = 'the sweep was cancelled before this release event was published'
        } elseif (-not (Test-Path -LiteralPath $writeEvent -PathType Leaf)) {
            $publication = 'failed'
            $phase = 'writer_absent'
            $errorText = 'Write-AgentEvent.ps1 is not present next to the sweep'
        } else {
            $recipients = @()
            if ($agent -cmatch '^[a-z][a-z0-9_-]{1,32}$') { $recipients += $agent }
            $dispatcher = Get-StaleClaimDispatcher -Claim $claim -BridgeRoot $bridgeRoot
            if ($null -ne $dispatcher -and
                $recipients -cnotcontains [string]$dispatcher.agent) {
                $recipients += [string]$dispatcher.agent
            }
            $payload = [pscustomobject]@{
                task_id            = [string]$claim.task_id
                claim_agent        = $agent
                claim_agent_uuid   = if ($claim.PSObject.Properties['agent_uuid']) { [string]$claim.agent_uuid } else { $null }
                claim_owner_session_id = if ($claim.PSObject.Properties['owner_session_id']) { [string]$claim.owner_session_id } else { $null }
                claim_run_id       = if ($claim.PSObject.Properties['run_id']) { [string]$claim.run_id } else { $null }
                dispatcher_request_id = if ($null -ne $dispatcher) { [string]$dispatcher.request_id } else { $null }
                dispatcher_agent_uuid = if ($null -ne $dispatcher) { [string]$dispatcher.agent_uuid } else { $null }
                dispatcher_session_id = if ($null -ne $dispatcher) { [string]$dispatcher.session_id } else { $null }
                last_heartbeat_utc = $tsString
                age_seconds        = [int]$ageSeconds
                stale_threshold_s  = $effectiveLeaseSeconds
                claim_lease_seconds = $claimLeaseSeconds
                claim_lease_expires_utc = $effectiveExpiresUtc.ToString('o')
                archived_path      = $donePath
                swept_by           = $env:AGENT_BRIDGE_RUN_ID
            }
            $payloadJson = ($payload | ConvertTo-Json -Depth 6 -Compress)
            $phase = 'writer'
            $written = @(& $writeEvent `
                -Agent system `
                -Type release `
                -Status stale_lease `
                -Severity medium `
                -TaskId ([string]$claim.task_id) `
                -To ($recipients -join ',') `
                -Message ("auto-released stale claim by $agent (heartbeat $([int]$ageSeconds)s old)") `
                -PayloadJson $payloadJson)
            # Published means the writer's own delivery receipt says the line is canonical and
            # durable (a real boolean true). Queued, suppressed or unconfirmed is not published.
            $deliveries = @($written | Where-Object {
                $null -ne $_ -and $_.PSObject.Properties['_bridge_delivery'] -and
                $null -ne $_._bridge_delivery })
            if ($deliveries.Count -ne 1) {
                $publication = 'publication_unknown'
                $phase = 'writer_unconfirmed'
                $errorText = 'the writer returned {0} delivery receipts; exactly one is required' -f $deliveries.Count
            } else {
                $delivery = $deliveries[0]._bridge_delivery
                $durable = $null
                $deliveryStatus = ''
                if ($delivery.PSObject.Properties['canonical_durable']) { $durable = $delivery.canonical_durable }
                if ($delivery.PSObject.Properties['delivery_status']) { $deliveryStatus = [string]$delivery.delivery_status }
                if (-not ($durable -is [bool] -and $durable)) {
                    $publication = if ($deliveryStatus -ceq 'suppressed') { 'failed' } else { 'publication_unknown' }
                    $phase = if ($deliveryStatus -cmatch '^[a-z_]{1,32}$') { 'writer_' + $deliveryStatus } else { 'writer_unconfirmed' }
                    $errorText = 'the writer did not report a canonical durable append (delivery_status ' +
                        $deliveryStatus + ')'
                }
            }
        }
    } catch {
        # Before the writer ran nothing was appended (failed); once it ran, a throw cannot prove
        # the line was not appended (publication_unknown).
        $publication = if ($phase -ceq 'writer') { 'publication_unknown' } else { 'failed' }
        $cancelled = Test-StaleSweepCancellation -Exception $_.Exception
        $phase = if ($cancelled) { 'cancelled' } elseif ($phase -ceq 'writer') { 'writer_failed' } else { 'prepare_failed' }
        $errorText = $_.Exception.Message
        if ($cancelled) { $cancellation = $_ }
        # The warning callers already read for a writer that throws, unchanged.
        Write-Warning ("stale-lease release event emit failed: {0}" -f $errorText)
    }
    # The receipt is the durable signal (shown by Get-AgentBridgeStatus); an absent, queued or
    # unconfirmed writer adds no console line, so callers' output stays as it was.
    if ($publication -cne 'published') {
        try {
            [void](Write-StaleSweepEmitFailure -BridgeRoot $bridgeRoot -Archived $archived `
                -Phase $phase -Publication $publication -ErrorText $errorText)
        } catch {
            $receiptFailures.Add($_.Exception.Message)
            Write-Warning $_.Exception.Message
        }
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
}
if ($receiptFailures.Count -gt 0) {
    $summary = 'stale sweep archived claims whose release events were not published and whose ' +
        'sweep_emit_failures receipts could not be persisted: ' + ($receiptFailures -join ' | ')
    if ($null -ne $cancellation) { throw (New-Object System.OperationCanceledException($summary)) }
    throw $summary
}
if ($null -ne $cancellation) { throw $cancellation }
}
