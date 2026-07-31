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

    This script scans .agent-bridge/work_queue/claims/*.json.
    Complete owner-bound claims use last_heartbeat_utc (falling back
    to claimed_at_utc) and may extend their lease with a later
    claim_lease_expires_utc. Legacy claims with missing or malformed
    owner identity fields use claimed_at_utc only and ignore mutable
    heartbeat/future-expiry fields.
      1. archives the claim file to work_queue/done/ as
         <task>.<utc-stamp>.stale_lease.json with the allowlisted
         claim fields plus a release_status="stale_lease" stamp;
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

$sessionIdentity = Join-Path $PSScriptRoot 'AgentBridgeSessionIdentity.ps1'
. $sessionIdentity

if ($PSBoundParameters.ContainsKey('StaleSeconds')) {
    if ($StaleSeconds -le 0) {
        throw 'StaleSeconds must be a positive Int32'
    }
} elseif ($StaleSeconds -eq 0) {
    if ($env:AGENT_BRIDGE_STALE_LEASE_SECONDS) {
        $parsed = 0
        $configuredText = (
            [string]$env:AGENT_BRIDGE_STALE_LEASE_SECONDS
        ).Trim()
        if (
            $configuredText -cmatch '^[0-9]+\z' -and
            [int]::TryParse(
                $configuredText,
                [System.Globalization.NumberStyles]::None,
                [System.Globalization.CultureInfo]::InvariantCulture,
                [ref]$parsed
            ) -and
            $parsed -gt 0
        ) {
            $StaleSeconds = $parsed
        }
    }
    if ($StaleSeconds -eq 0) { $StaleSeconds = 300 }
}

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

$claimsDir = Join-Path (Join-Path $bridgeRoot 'work_queue') 'claims'
$doneDir = Join-Path (Join-Path $bridgeRoot 'work_queue') 'done'

function ConvertTo-BridgeUtc {
    param([object] $Value)

    try {
        $parsed = ConvertFrom-AgentBridgeCanonicalUtc -Value $Value
    } catch {
        return $null
    }
    if ($null -eq $parsed) { return $null }
    if ($parsed -is [DateTimeOffset]) {
        return ([DateTimeOffset]$parsed).UtcDateTime
    }
    if ($parsed -is [DateTime]) {
        return ([DateTime]$parsed).ToUniversalTime()
    }
    return $null
}

function Get-BridgeClaimText {
    param(
        [Parameter(Mandatory)] $Claim,
        [Parameter(Mandatory)] [string] $Name
    )

    if (-not $Claim.PSObject.Properties[$Name]) { return '' }
    if ($null -eq $Claim.$Name) { return '' }
    if ($Claim.$Name -isnot [string]) { return '' }
    return [string]$Claim.$Name
}

function ConvertTo-BridgeStringArray {
    param(
        [object] $Value,
        [switch] $SplitComma
    )

    if ($null -eq $Value) { return @() }
    $source = if ($Value -is [string]) { @($Value) } else { @($Value) }
    $result = New-Object System.Collections.Generic.List[string]
    $seen = New-Object 'System.Collections.Generic.HashSet[string]' (
        [System.StringComparer]::Ordinal
    )
    foreach ($entry in $source) {
        if ($null -eq $entry -or $entry -isnot [string]) { continue }
        $parts = if ($SplitComma) {
            @(([string]$entry).Split(','))
        } else {
            @([string]$entry)
        }
        foreach ($part in $parts) {
            $normalized = ([string]$part).Trim()
            if (-not $normalized -or -not $seen.Add($normalized)) { continue }
            [void]$result.Add($normalized)
        }
    }
    return @($result)
}

function ConvertTo-BridgeInvariantUtcText {
    param([object] $Value)

    if ($null -eq $Value) { return '' }
    if ($Value -is [DateTimeOffset]) {
        return ([DateTimeOffset]$Value).ToUniversalTime().ToString(
            'o',
            [System.Globalization.CultureInfo]::InvariantCulture
        )
    }
    if ($Value -is [DateTime]) {
        return ([DateTime]$Value).ToUniversalTime().ToString(
            'o',
            [System.Globalization.CultureInfo]::InvariantCulture
        )
    }
    return [string]$Value
}

# Emit zero or more swept-claim records into the pipeline; caller
# wraps with @(...) to always get an array. Avoid the
# Generic.List + return-comma trick that PSStrictMode's boolean
# coercion can fail on (Codex finding 2026-05-09T12:26Z applied
# to this script too).
if (-not (Test-Path -LiteralPath $claimsDir -PathType Container)) {
    return
}

$mutationLock = Enter-AgentBridgeMutationLock -BridgeRoot $bridgeRoot
try {
$now = (Get-Date).ToUniversalTime()

$parsedClaims = @()
foreach ($file in @(Get-ChildItem -Path $claimsDir -Filter '*.json' -File `
        -ErrorAction SilentlyContinue)) {
    try {
        $claim = ConvertFrom-AgentBridgeJson -Json (
            Get-Content -Raw -Path $file.FullName -Encoding UTF8
        )
    } catch { continue }
    if ($null -eq $claim -or $claim -isnot [pscustomobject]) { continue }
    $storedTaskId = Get-BridgeClaimText -Claim $claim -Name 'task_id'
    if ([string]::IsNullOrEmpty($storedTaskId)) { continue }
    try {
        Assert-AgentBridgeTaskId -TaskId $storedTaskId
    } catch {
        # Public APIs cannot address malformed task IDs. Retain such records
        # instead of deriving a runtime-specific archive filename for them.
        continue
    }
    $parsedClaims += [pscustomobject]@{
        file = $file
        claim = $claim
        task_id = $storedTaskId
    }
}

# Refuse an ambiguous logical queue before creating done/ or archiving any
# record. The nested scan is intentionally ordinal/case-sensitive and keeps
# Windows PowerShell 5.1 compatibility without case-insensitive hashtables.
for ($leftIndex = 0; $leftIndex -lt $parsedClaims.Count; $leftIndex++) {
    for (
        $rightIndex = $leftIndex + 1;
        $rightIndex -lt $parsedClaims.Count;
        $rightIndex++
    ) {
        if (
            [string]$parsedClaims[$leftIndex].task_id -ceq
            [string]$parsedClaims[$rightIndex].task_id
        ) {
            throw (
                "duplicate active claim records for exact task_id '{0}': {1}, {2}" -f
                [string]$parsedClaims[$leftIndex].task_id,
                $parsedClaims[$leftIndex].file.FullName,
                $parsedClaims[$rightIndex].file.FullName
            )
        }
    }
}

# Validate the collision-resistant preferred path for every addressable claim
# before computing or publishing any archive. A legacy claim must not route
# around a preferred filename already occupied by another logical task.
foreach ($entry in $parsedClaims) {
    [void](Assert-AgentBridgePreferredClaimPath `
        -ClaimsDir $claimsDir `
        -TaskId ([string]$entry.task_id))
}

$stalePlans = @()
$stamp = $now.ToString('yyyyMMddTHHmmssZ')
foreach ($entry in $parsedClaims) {
    $file = $entry.file
    $claim = $entry.claim
    $taskId = [string]$entry.task_id
    $agent = Get-BridgeClaimText -Claim $claim -Name 'agent'
    if ($agent -cin @('operator','system')) { continue }

    $legacyTokenless = -not (
        Test-AgentBridgeStoredClaimOwnerComplete -Claim $claim
    )
    $storedHeartbeatValue = if (
        $claim.PSObject.Properties['last_heartbeat_utc']
    ) {
        $claim.last_heartbeat_utc
    } else {
        $null
    }
    $storedHeartbeatUtc = ConvertTo-BridgeInvariantUtcText `
        -Value $storedHeartbeatValue
    $storedClaimedAtValue = if (
        $claim.PSObject.Properties['claimed_at_utc']
    ) {
        $claim.claimed_at_utc
    } else {
        $null
    }
    $storedClaimedAtUtc = ConvertTo-BridgeInvariantUtcText `
        -Value $storedClaimedAtValue

    $leaseAnchorField = if ($legacyTokenless) {
        'claimed_at_utc'
    } else {
        'last_heartbeat_utc'
    }
    $ts = if ($legacyTokenless) {
        ConvertTo-BridgeUtc -Value $storedClaimedAtValue
    } else {
        $ownerAnchor = ConvertTo-BridgeUtc -Value $storedHeartbeatValue
        if ($null -eq $ownerAnchor -and
            $claim.PSObject.Properties['claimed_at_utc']) {
            $ownerAnchor = ConvertTo-BridgeUtc -Value $storedClaimedAtValue
            if ($null -ne $ownerAnchor) {
                $leaseAnchorField = 'claimed_at_utc'
            }
        }
        $ownerAnchor
    }

    $claimLeaseSeconds = $StaleSeconds
    if ($claim.PSObject.Properties['lease_seconds']) {
        $parsedLease = ConvertTo-BridgePositiveInt32 `
            -Value $claim.lease_seconds
        if ($parsedLease -gt 0) {
            $claimLeaseSeconds = $parsedLease
        }
    }

    $effectiveExpiresUtc = if ($null -ne $ts) {
        try {
            $ts.AddSeconds($claimLeaseSeconds)
        } catch [System.ArgumentOutOfRangeException] {
            [DateTime]::SpecifyKind(
                [DateTime]::MaxValue,
                [DateTimeKind]::Utc
            )
        }
    } else {
        $null
    }
    $storedClaimExpiresValue = if (
        $claim.PSObject.Properties['claim_lease_expires_utc']
    ) {
        $claim.claim_lease_expires_utc
    } else {
        $null
    }
    $storedClaimExpiresUtc = ConvertTo-BridgeInvariantUtcText `
        -Value $storedClaimExpiresValue
    if (-not $legacyTokenless -and
        $null -ne $effectiveExpiresUtc -and
        $null -ne $storedClaimExpiresValue) {
        $claimExpiresUtc = ConvertTo-BridgeUtc `
            -Value $storedClaimExpiresValue
        if ($null -ne $claimExpiresUtc -and $claimExpiresUtc -gt $effectiveExpiresUtc) {
            $effectiveExpiresUtc = $claimExpiresUtc
        }
    }
    $effectiveLeaseSeconds = if (
        $null -ne $ts -and $null -ne $effectiveExpiresUtc
    ) {
        [Math]::Max(
            [long]$claimLeaseSeconds,
            [long][Math]::Ceiling(
                ($effectiveExpiresUtc - $ts).TotalSeconds
            )
        )
    } else {
        [long]$claimLeaseSeconds
    }
    if ($effectiveLeaseSeconds -lt 1) { $effectiveLeaseSeconds = [long]1 }
    if ($null -ne $effectiveExpiresUtc -and $now -lt $effectiveExpiresUtc) {
        continue
    }

    $ageSeconds = if ($null -ne $ts) {
        [long][Math]::Floor(($now - $ts).TotalSeconds)
    } else {
        [long]$effectiveLeaseSeconds
    }
    $leaseAnchorUtc = if ($null -ne $ts) { $ts.ToString('o') } else { '' }
    $effectiveExpiresText = if ($null -ne $effectiveExpiresUtc) {
        $effectiveExpiresUtc.ToString('o')
    } else {
        ''
    }
    $releaseReason = if ($legacyTokenless) {
        "legacy tokenless claim $leaseAnchorField was ${ageSeconds}s old; lease threshold ${effectiveLeaseSeconds}s"
    } else {
        "$leaseAnchorField was ${ageSeconds}s old; lease threshold ${effectiveLeaseSeconds}s"
    }

    $safeTask = Get-AgentBridgeClaimBaseName -TaskId $taskId
    $donePath = Join-Path $doneDir ("$safeTask.$stamp.stale_lease.json")
    $stalePlans += [pscustomobject]@{
        file = $file
        claim = $claim
        task_id = $taskId
        agent = $agent
        legacy_tokenless = $legacyTokenless
        stored_heartbeat_utc = $storedHeartbeatUtc
        stored_claimed_at_utc = $storedClaimedAtUtc
        stored_claim_expires_utc = $storedClaimExpiresUtc
        claim_lease_seconds = $claimLeaseSeconds
        effective_expires_text = $effectiveExpiresText
        effective_lease_seconds = $effectiveLeaseSeconds
        age_seconds = $ageSeconds
        lease_anchor_field = $leaseAnchorField
        lease_anchor_utc = $leaseAnchorUtc
        release_reason = $releaseReason
        done_path = $donePath
    }
}

# Preflight the whole Windows archive namespace before publishing the first
# record. Task IDs remain ordinal, but their safe done filenames must be
# treated case-insensitively on every platform for deterministic parity.
$plannedArchiveNames = [System.Collections.Generic.Dictionary[
    string,
    string
]]::new([System.StringComparer]::OrdinalIgnoreCase)
$existingArchiveNames = [System.Collections.Generic.HashSet[string]]::new(
    [System.StringComparer]::OrdinalIgnoreCase
)
if (Test-Path -LiteralPath $doneDir -PathType Container) {
    foreach ($existingDone in @(Get-ChildItem -LiteralPath $doneDir `
            -ErrorAction Stop)) {
        [void]$existingArchiveNames.Add($existingDone.Name)
    }
}
foreach ($plan in $stalePlans) {
    $archiveName = [System.IO.Path]::GetFileName(
        [string]$plan.done_path
    )
    if ($plannedArchiveNames.ContainsKey($archiveName)) {
        throw (
            "stale archive destination collision: {0}, {1}" -f
            $plannedArchiveNames[$archiveName],
            $archiveName
        )
    }
    if ($existingArchiveNames.Contains($archiveName)) {
        throw "stale archive destination already exists: $archiveName"
    }
    $plannedArchiveNames.Add($archiveName, $archiveName)
}

$archiveFailures = New-Object System.Collections.Generic.List[string]
foreach ($plan in $stalePlans) {
    $file = $plan.file
    $claim = $plan.claim
    $taskId = [string]$plan.task_id
    $agent = [string]$plan.agent
    $legacyTokenless = [bool]$plan.legacy_tokenless
    $storedHeartbeatUtc = [string]$plan.stored_heartbeat_utc
    $storedClaimedAtUtc = [string]$plan.stored_claimed_at_utc
    $storedClaimExpiresUtc = [string]$plan.stored_claim_expires_utc
    $claimLeaseSeconds = [int]$plan.claim_lease_seconds
    $effectiveExpiresText = [string]$plan.effective_expires_text
    $effectiveLeaseSeconds = [long]$plan.effective_lease_seconds
    $ageSeconds = [long]$plan.age_seconds
    $leaseAnchorField = [string]$plan.lease_anchor_field
    $leaseAnchorUtc = [string]$plan.lease_anchor_utc
    $releaseReason = [string]$plan.release_reason
    $donePath = [string]$plan.done_path

    # Stale: archive the claim file to done/ with a stale_lease
    # stamp and emit a release/stale_lease event.
    $releasedAtText = $now.ToString('o')
    $storedWriteScope = if (
        $claim.PSObject.Properties['write_scope']
    ) {
        $claim.write_scope
    } else {
        $null
    }
    $storedLeaseValue = if (
        $claim.PSObject.Properties['lease_seconds']
    ) {
        $claim.lease_seconds
    } else {
        $null
    }

    $archiveClaim = [ordered]@{
        agent = $agent
        task_id = $taskId
        summary = Get-BridgeClaimText -Claim $claim -Name 'summary'
        mode = Get-BridgeClaimText -Claim $claim -Name 'mode'
        write_scope = @(
            ConvertTo-BridgeStringArray `
                -Value $storedWriteScope `
                -SplitComma
        )
        run_id = Get-BridgeClaimText -Claim $claim -Name 'run_id'
        claimed_at_utc = $storedClaimedAtUtc
        last_heartbeat_utc = $storedHeartbeatUtc
        lease_seconds = ConvertTo-BridgePositiveInt32 `
            -Value $storedLeaseValue
        claim_lease_expires_utc = $storedClaimExpiresUtc
        released_at_utc = $releasedAtText
        release_status = 'stale_lease'
        release_reason = $releaseReason
    }
    foreach ($optionalTextField in @(
            'session_id',
            'owner_session_id',
            'owner_token_sha256',
            'role',
            'agent_uuid',
            'writer_pid_semantics',
            'cwd',
            'git_branch'
        )) {
        $optionalText = Get-BridgeClaimText `
            -Claim $claim `
            -Name $optionalTextField
        if ($optionalText) {
            $archiveClaim[$optionalTextField] = $optionalText
        }
    }
    $storedOwnerProcessStart = if (
        $claim.PSObject.Properties['owner_process_start_utc']
    ) {
        ConvertTo-BridgeInvariantUtcText `
            -Value $claim.owner_process_start_utc
    } else {
        ''
    }
    if ($storedOwnerProcessStart) {
        $archiveClaim['owner_process_start_utc'] = $storedOwnerProcessStart
    }
    foreach ($optionalIntegerField in @('owner_pid', 'writer_pid')) {
        $optionalInteger = if (
            $claim.PSObject.Properties[$optionalIntegerField]
        ) {
            ConvertTo-BridgePositiveInt32 `
                -Value $claim.$optionalIntegerField
        } else {
            0
        }
        if ($optionalInteger -gt 0) {
            $archiveClaim[$optionalIntegerField] = $optionalInteger
        }
    }
    $archiveCapabilities = @(
        if ($claim.PSObject.Properties['capabilities']) {
            ConvertTo-BridgeStringArray `
                -Value $claim.capabilities
        }
    )
    if ($archiveCapabilities.Count -gt 0) {
        $archiveClaim['capabilities'] = $archiveCapabilities
    }

    $archiveTempPath = "$donePath.tmp.$PID.$([guid]::NewGuid().ToString('N'))"
    $archivePublished = $false
    try {
        if (-not (Test-Path -LiteralPath $doneDir -PathType Container)) {
            [void](New-Item -ItemType Directory -Path $doneDir -Force `
                -ErrorAction Stop)
        }
        $archiveJson = $archiveClaim | ConvertTo-Json -Depth 8
        $archiveEncoding = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText(
            $archiveTempPath,
            $archiveJson,
            $archiveEncoding
        )
        # File.Move has create-new destination semantics on both Windows
        # PowerShell 5.1 and PowerShell 7: an existing archive is never
        # overwritten. The fully-written sibling is atomically published.
        [System.IO.File]::Move($archiveTempPath, $donePath)
        $archivePublished = $true
        Remove-Item -LiteralPath $file.FullName -Force -ErrorAction Stop
    } catch {
        $archiveError = $_.Exception.Message
        if (
            $archivePublished -and
            (Test-Path -LiteralPath $file.FullName -PathType Leaf)
        ) {
            try {
                Remove-Item -LiteralPath $donePath -Force -ErrorAction Stop
                $archivePublished = $false
            } catch {
                Write-Warning (
                    "could not roll back stale archive {0}: {1}" -f
                    $donePath,
                    $_.Exception.Message
                )
            }
        }
        if (
            -not $archivePublished -or
            (Test-Path -LiteralPath $file.FullName -PathType Leaf)
        ) {
            $failureMessage = (
                "could not archive stale claim {0}: {1}" -f
                $file.Name,
                $archiveError
            )
            Write-Warning $failureMessage
            [void]$archiveFailures.Add($failureMessage)
            continue
        }
        Write-Warning (
            "claim removal reported an error after source disappeared; keeping committed stale archive {0}: {1}" -f
            $donePath,
            $archiveError
        )
    } finally {
        if (Test-Path -LiteralPath $archiveTempPath) {
            Remove-Item -LiteralPath $archiveTempPath -Force `
                -ErrorAction SilentlyContinue
        }
    }

    # Emit release event (best-effort; lease sweep must not fail
    # because the bridge writer is momentarily contended).
    try {
        $writeEvent = Join-Path $PSScriptRoot 'Write-AgentEvent.ps1'
        if (Test-Path -LiteralPath $writeEvent -PathType Leaf) {
            $claimClaimedAtUtc = if ($claim.PSObject.Properties['claimed_at_utc']) {
                ConvertTo-BridgeInvariantUtcText -Value $claim.claimed_at_utc
            } else {
                ''
            }
            $claimRunId = if ($claim.PSObject.Properties['run_id']) {
                [string]$claim.run_id
            } else {
                ''
            }
            $payload = [pscustomobject]@{
                task_id            = $taskId
                claim_agent        = $agent
                claim_claimed_at_utc = $claimClaimedAtUtc
                claim_run_id       = $claimRunId
                last_heartbeat_utc = $storedHeartbeatUtc
                age_seconds        = [long]$ageSeconds
                stale_threshold_s  = $effectiveLeaseSeconds
                claim_lease_seconds = $claimLeaseSeconds
                claim_lease_expires_utc = $effectiveExpiresText
                lease_anchor_field = $leaseAnchorField
                lease_anchor_utc   = $leaseAnchorUtc
                legacy_tokenless   = $legacyTokenless
                archive_released_at_utc = ConvertTo-BridgeInvariantUtcText `
                    -Value $releasedAtText
                archived_path      = $donePath
                archive_state_semantics = 'verified_before_event_append'
            }
            $payloadJson = ($payload | ConvertTo-Json -Depth 6 -Compress)
            & $writeEvent `
                -Agent system `
                -Type release `
                -Status stale_lease `
                -Severity medium `
                -TaskId $taskId `
                -Message ("auto-released stale claim by $agent ($leaseAnchorField ${ageSeconds}s old)") `
                -PayloadJson $payloadJson `
                -InternalStaleLeaseArchivePath $donePath | Out-Null
        }
    } catch {
        Write-Warning ("stale-lease release event emit failed: {0}" -f `
            $_.Exception.Message)
    }

    if (-not $Quiet) {
        Write-Host ("STALE LEASE SWEPT: {0} by {1} ({2} {3}s old)" -f `
            $taskId, $agent, $leaseAnchorField, [long]$ageSeconds) `
            -ForegroundColor Yellow
    }

    # Emit into pipeline (caller wraps with @(...)).
    [pscustomobject]@{
        task_id        = $taskId
        agent          = $agent
        age_seconds    = [long]$ageSeconds
        archived_path  = $donePath
    }
}
if ($archiveFailures.Count -gt 0) {
    throw (
        "stale claim sweep incomplete ({0} failure(s)): {1}" -f
        $archiveFailures.Count,
        ($archiveFailures -join '; ')
    )
}
} finally {
    Exit-AgentBridgeMutationLock -Lock $mutationLock
}
