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
Ensure-AgentBridgePlainDirectory `
    -LiteralPath $bridgeRoot `
    -Context 'bridge root'

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

function Get-BridgeSha256Hex {
    param(
        [Parameter(Mandatory)] [byte[]] $Bytes
    )

    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $digest = $sha256.ComputeHash($Bytes)
    } finally {
        $sha256.Dispose()
    }
    return (
        [System.BitConverter]::ToString($digest).Replace(
            '-',
            ''
        ).ToLowerInvariant()
    )
}

function Get-BridgeFileSha256Hex {
    param(
        [Parameter(Mandatory)] [string] $Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "archive ownership proof is not a file: $Path"
    }
    return Get-BridgeSha256Hex -Bytes (
        [System.IO.File]::ReadAllBytes($Path)
    )
}

function Get-BridgeStaleBatchState {
    param(
        [Parameter(Mandatory)] [object[]] $Plans
    )

    $states = New-Object System.Collections.Generic.List[string]
    foreach ($plan in @($Plans)) {
        $sourceState = if (
            Test-Path -LiteralPath $plan.file.FullName -PathType Leaf
        ) {
            'retained'
        } else {
            'missing'
        }
        $archiveState = if (Test-Path -LiteralPath $plan.done_path) {
            'retained'
        } else {
            'missing'
        }
        $backupState = if (
            Test-Path -LiteralPath $plan.source_backup_path
        ) {
            'retained'
        } else {
            'missing'
        }
        $tempState = if (
            Test-Path -LiteralPath $plan.archive_temp_path
        ) {
            'retained'
        } else {
            'missing'
        }
        $quarantineState = if (
            Test-Path -LiteralPath $plan.source_quarantine_path
        ) {
            'retained'
        } else {
            'missing'
        }
        [void]$states.Add(
            (
                (
                    "task='{0}' source={1}:{2} source_verified={3} " +
                    "archive={4}:{5} backup={6}:{7} quarantine={8}:{9} " +
                    "temp={10}:{11}"
                ) -f
                [string]$plan.task_id,
                $sourceState,
                $plan.file.FullName,
                [bool]$plan.rollback_source_verified,
                $archiveState,
                [string]$plan.done_path,
                $backupState,
                [string]$plan.source_backup_path,
                $quarantineState,
                [string]$plan.source_quarantine_path,
                $tempState,
                [string]$plan.archive_temp_path
            )
        )
    }
    return ($states -join ' | ')
}

function Invoke-BridgeStaleBatchRollback {
    param(
        [Parameter(Mandatory)] [object[]] $Plans,
        [Parameter(Mandatory)] [string] $DoneDir,
        [Parameter(Mandatory)] [bool] $RemoveDoneDir,
        [Parameter(Mandatory)] [AllowEmptyCollection()]
        [System.Collections.Generic.List[string]] $Failures
    )

    # Restore every removed source before deleting any archive. Each archive
    # is deleted only after its own active source is verified retained or
    # restored. If rollback is interrupted or restore fails, the visible
    # archive and exact-byte recovery backup are retained.
    for ($index = $Plans.Count - 1; $index -ge 0; $index--) {
        $plan = $Plans[$index]
        $plan.rollback_source_verified = $false
        if (-not [bool]$plan.source_backup_prepared) {
            # Commit cannot start before every trusted byte backup is ready.
            # Therefore this plan's active source was never removed by this
            # batch and no rollback publication is authorized.
            continue
        }

        if (Test-Path -LiteralPath $plan.file.FullName -PathType Leaf) {
            try {
                $null = Assert-AgentBridgeExpectedRegularFileSnapshot `
                    -LiteralPath ([string]$plan.file.FullName) `
                    -ExpectedSha256 `
                        ([string]$plan.rollback_source_sha256) `
                    -ExpectedLength ([long]$plan.rollback_source_length) `
                    -Context 'rollback active stale-claim source'
                $plan.rollback_source_verified = $true
            } catch {
                [void]$Failures.Add(
                    (
                        "active source ownership hash mismatched {0}; " +
                        "trusted recovery bytes and artifacts retained: {1}" -f
                        $plan.file.FullName,
                        $_.Exception.Message
                    )
                )
            }
            continue
        }

        if (-not [bool]$plan.rollback_source_restore_allowed) {
            [void]$Failures.Add(
                (
                    (
                        "active source restore suppressed for {0}; no " +
                        "complete trusted post-Move generation was captured; " +
                        "stale eligibility bytes were not republished; " +
                        "recovery artifacts retained"
                    ) -f $plan.file.FullName
                )
            )
            continue
        }

        # STALE V2 MARKER: restore active source from captured trusted bytes.
        $restoreResult = Invoke-AgentBridgeTrustedBytesCreateNew `
            -DestinationPath ([string]$plan.file.FullName) `
            -PublishBytes ([byte[]]$plan.rollback_source_bytes) `
            -ExpectedSha256 ([string]$plan.rollback_source_sha256) `
            -ExpectedLength ([long]$plan.rollback_source_length) `
            -Context 'rollback restored stale-claim source'
        if ([bool]$restoreResult.succeeded) {
            $plan.source_removed = $false
            $plan.rollback_source_verified = $true
            # STALE V2 MARKER: restored source verified before recovery retention.
        } else {
            [void]$Failures.Add(
                (
                    "source restore from captured trusted bytes failed {0}: " +
                    "{1}; recovery artifacts retained" -f
                    $plan.file.FullName,
                    $restoreResult.error.Message
                )
            )
        }
    }

    for ($index = $Plans.Count - 1; $index -ge 0; $index--) {
        $plan = $Plans[$index]
        if (-not [bool]$plan.archive_published) { continue }
        if (-not (Test-Path -LiteralPath $plan.done_path)) {
            $plan.archive_published = $false
            continue
        }
        if (-not [bool]$plan.rollback_source_verified) {
            $backupState = if (
                Test-Path `
                    -LiteralPath $plan.source_backup_path `
                    -PathType Leaf
            ) {
                'retained'
            } else {
                'missing'
            }
            [void]$Failures.Add(
                (
                    "archive rollback retained {0} because active source " +
                    "identity was not verified; source={1}; " +
                    "recovery backup={2}:{3}" -f
                    [string]$plan.done_path,
                    $plan.file.FullName,
                    $backupState,
                    [string]$plan.source_backup_path
                )
            )
            continue
        }
        try {
            # STALE V2 MARKER: exact-handle archive rollback cleanup.
            Remove-AgentBridgeExactFile `
                -LiteralPath ([string]$plan.done_path) `
                -ExpectedSha256 ([string]$plan.archive_sha256) `
                -ExpectedLength ([long]$plan.archive_length) `
                -Context 'archive rollback cleanup'
            $plan.archive_published = $false
        } catch {
            [void]$Failures.Add(
                (
                    "archive rollback retained {0} because batch ownership " +
                    "could not be deleted by verified open handle: {1}" -f
                    [string]$plan.done_path,
                    $_.Exception.Message
                )
            )
        }
    }

    for ($index = $Plans.Count - 1; $index -ge 0; $index--) {
        $plan = $Plans[$index]
        # Rollback deliberately retains source quarantine and source backup.
        # A boolean produced by an earlier source check must never authorize
        # deleting the last exact recovery generation after a later active-path
        # replacement. Committed batches use exact-handle cleanup below.
        if (Test-Path -LiteralPath $plan.archive_temp_path) {
            try {
                if ([bool]$plan.archive_temp_consumed) {
                    throw (
                        "path reappeared after the batch archive temp was " +
                        "moved; retained unowned artifact"
                    )
                }
                Remove-AgentBridgeExactFile `
                    -LiteralPath ([string]$plan.archive_temp_path) `
                    -ExpectedSha256 ([string]$plan.archive_sha256) `
                    -ExpectedLength ([long]$plan.archive_length) `
                    -Context 'archive temp rollback cleanup'
            } catch {
                [void]$Failures.Add(
                    (
                        "archive temp cleanup failed {0}: {1}" -f
                        [string]$plan.archive_temp_path,
                        $_.Exception.Message
                    )
                )
            }
        }
    }

    # Never remove DoneDir by pathname during rollback. An emptiness check
    # cannot bind the directory generation through a later Remove-Item, so a
    # concurrently swapped foreign empty directory must be retained.
}

function New-BridgeStaleBatchFailureMessage {
    param(
        [Parameter(Mandatory)] [System.Exception] $PrimaryError,
        [Parameter(Mandatory)] [AllowEmptyCollection()]
        [System.Collections.Generic.List[string]] $RollbackFailures,
        [Parameter(Mandatory)] [object[]] $Plans
    )

    $rollbackSummary = if ($RollbackFailures.Count -gt 0) {
        $RollbackFailures -join ' | '
    } else {
        '<none>'
    }
    return (
        (
            "stale claim sweep incomplete: stale claim batch apply failed; " +
            "primary: {0}: {1}; " +
            "rollback failures: {2}; state: {3}"
        ) -f
        $PrimaryError.GetType().Name,
        $PrimaryError.Message,
        $rollbackSummary,
        (Get-BridgeStaleBatchState -Plans $Plans)
    )
}

# Emit zero or more swept-claim records into the pipeline; caller
# wraps with @(...) to always get an array. Avoid the
# Generic.List + return-comma trick that PSStrictMode's boolean
# coercion can fail on (Codex finding 2026-05-09T12:26Z applied
# to this script too).
if (-not (Test-Path -LiteralPath $claimsDir -PathType Container)) {
    return
}

$committedPlans = @()
$mutationLock = Enter-AgentBridgeMutationLock -BridgeRoot $bridgeRoot
try {
$now = (Get-Date).ToUniversalTime()

$parsedClaims = @()
$claimFiles = @(
    Get-ChildItem -Path $claimsDir -Filter '*.json' -File `
        -ErrorAction SilentlyContinue |
        Sort-Object -Property FullName
)
foreach ($file in $claimFiles) {
    try {
        $claimSnapshot = Read-AgentBridgeStrictUtf8JsonSnapshot `
            -LiteralPath $file.FullName
        $claim = ConvertFrom-AgentBridgeJson -Json (
            [string]$claimSnapshot.text
        )
    } catch { continue }
    if ($null -eq $claim -or $claim -isnot [pscustomobject]) { continue }
    $storedTaskId = Get-AgentBridgeClaimText -Claim $claim -Name 'task_id'
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
        source_snapshot_bytes = [byte[]]$claimSnapshot.bytes
        source_snapshot_sha256 = [string]$claimSnapshot.sha256
        source_snapshot_length = [long]$claimSnapshot.length
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
    $agent = Get-AgentBridgeClaimText -Claim $claim -Name 'agent'
    if ($agent -cin @('operator','system')) { continue }

    $legacyTokenless = -not (
        Test-AgentBridgeStoredClaimOwnerComplete -Claim $claim
    )
    $storedHeartbeatProperty = Get-AgentBridgeExactProperty `
        -InputObject $claim `
        -Name 'last_heartbeat_utc'
    $storedHeartbeatValue = if ($null -ne $storedHeartbeatProperty) {
        $storedHeartbeatProperty.Value
    } else {
        $null
    }
    $storedClaimedAtProperty = Get-AgentBridgeExactProperty `
        -InputObject $claim `
        -Name 'claimed_at_utc'
    $storedClaimedAtValue = if ($null -ne $storedClaimedAtProperty) {
        $storedClaimedAtProperty.Value
    } else {
        $null
    }

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
            $null -ne $storedClaimedAtProperty) {
            $ownerAnchor = ConvertTo-BridgeUtc -Value $storedClaimedAtValue
            if ($null -ne $ownerAnchor) {
                $leaseAnchorField = 'claimed_at_utc'
            }
        }
        $ownerAnchor
    }

    $claimLeaseSeconds = $StaleSeconds
    $storedLeaseProperty = Get-AgentBridgeExactProperty `
        -InputObject $claim `
        -Name 'lease_seconds'
    if (-not $legacyTokenless -and $null -ne $storedLeaseProperty) {
        $parsedLease = ConvertTo-BridgePositiveInt32 `
            -Value $storedLeaseProperty.Value
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
    $storedClaimExpiresProperty = Get-AgentBridgeExactProperty `
        -InputObject $claim `
        -Name 'claim_lease_expires_utc'
    $storedClaimExpiresValue = if (
        $null -ne $storedClaimExpiresProperty
    ) {
        $storedClaimExpiresProperty.Value
    } else {
        $null
    }
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
        claim_lease_seconds = $claimLeaseSeconds
        effective_expires_text = $effectiveExpiresText
        effective_lease_seconds = $effectiveLeaseSeconds
        age_seconds = $ageSeconds
        lease_anchor_field = $leaseAnchorField
        lease_anchor_utc = $leaseAnchorUtc
        release_reason = $releaseReason
        done_path = $donePath
        source_snapshot_bytes = [byte[]]$entry.source_snapshot_bytes
        source_snapshot_sha256 = [string]$entry.source_snapshot_sha256
        source_snapshot_length = [long]$entry.source_snapshot_length
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

$preparedPlans = @()
$releasedAtText = $now.ToString('o')
$archiveEncoding = New-Object System.Text.UTF8Encoding($false)
if (
    $stalePlans.Count -gt 0 -and
    (Test-Path -LiteralPath $doneDir) -and
    -not (Test-Path -LiteralPath $doneDir -PathType Container)
) {
    throw "stale archive destination parent is not a directory: $doneDir"
}
foreach ($plan in $stalePlans) {
    if (-not (
            Test-Path -LiteralPath $plan.file.FullName -PathType Leaf
        )) {
        throw (
            "stale claim source disappeared before batch preparation: {0}" -f
            $plan.file.FullName
        )
    }

    $file = $plan.file
    $claim = $plan.claim
    $taskId = [string]$plan.task_id
    $agent = [string]$plan.agent
    $releaseReason = [string]$plan.release_reason
    $donePath = [string]$plan.done_path
    # All raw identity, privilege, owner-completeness, and stale-eligibility
    # checks are complete before this persistence-only projection.
    $canonicalClaim = ConvertTo-AgentBridgeCanonicalClaim `
        -Claim $claim `
        -SparseOptionalFields
    $archiveClaim = [ordered]@{}
    foreach ($property in $canonicalClaim.PSObject.Properties) {
        $archiveClaim[$property.Name] = $property.Value
    }
    $archiveClaim['released_at_utc'] = $releasedAtText
    $archiveClaim['release_status'] = 'stale_lease'
    $archiveClaim['release_reason'] = $releaseReason

    $storedHeartbeatUtc = [string]$canonicalClaim.last_heartbeat_utc
    $storedClaimedAtUtc = [string]$canonicalClaim.claimed_at_utc
    $claimRunId = [string]$canonicalClaim.run_id
    $archiveTempPath = (
        "$donePath.tmp.$PID.$([guid]::NewGuid().ToString('N'))"
    )
    $sourceBackupPath = (
        "$($file.FullName).stale-backup.$PID." +
        "$([guid]::NewGuid().ToString('N'))"
    )
    $sourceQuarantinePath = (
        "$($file.FullName).stale-quarantine.$PID." +
        "$([guid]::NewGuid().ToString('N'))"
    )
    $archiveJson = ($archiveClaim | ConvertTo-Json -Depth 8)
    $archiveBytes = [byte[]]$archiveEncoding.GetBytes($archiveJson)
    $archiveSha256 = Get-BridgeSha256Hex -Bytes $archiveBytes
    $archiveLength = [long]$archiveBytes.Length
    $preparedPlans += [pscustomobject]@{
        file = $file
        claim = $claim
        task_id = $taskId
        agent = $agent
        legacy_tokenless = [bool]$plan.legacy_tokenless
        stored_heartbeat_utc = $storedHeartbeatUtc
        stored_claimed_at_utc = $storedClaimedAtUtc
        claim_run_id = $claimRunId
        claim_lease_seconds = [int]$plan.claim_lease_seconds
        effective_expires_text = [string]$plan.effective_expires_text
        effective_lease_seconds = [long]$plan.effective_lease_seconds
        age_seconds = [long]$plan.age_seconds
        lease_anchor_field = [string]$plan.lease_anchor_field
        lease_anchor_utc = [string]$plan.lease_anchor_utc
        done_path = $donePath
        released_at_text = $releasedAtText
        archive_json = $archiveJson
        archive_bytes = $archiveBytes
        archive_sha256 = $archiveSha256
        archive_length = $archiveLength
        archive_temp_path = $archiveTempPath
        archive_temp_consumed = $false
        source_backup_path = $sourceBackupPath
        source_snapshot_bytes = [byte[]]$plan.source_snapshot_bytes
        source_backup_sha256 = [string]$plan.source_snapshot_sha256
        source_backup_length = [long]$plan.source_snapshot_length
        source_backup_prepared = $false
        rollback_source_bytes = [byte[]]$plan.source_snapshot_bytes
        rollback_source_sha256 = [string]$plan.source_snapshot_sha256
        rollback_source_length = [long]$plan.source_snapshot_length
        # Eligibility bytes are evidence/recovery only. They never authorize
        # recreating an active pathname that this transaction did not move.
        rollback_source_restore_allowed = $false
        rollback_source_generation = 'eligibility-evidence-only'
        source_quarantine_path = $sourceQuarantinePath
        source_quarantine_capture_completed = $false
        source_quarantine_verified = $false
        archive_published = $false
        source_removed = $false
        rollback_source_verified = $false
    }
}

$doneDirCreated = $false
if ($preparedPlans.Count -gt 0) {
    try {
        $doneDirExisted = Test-Path -LiteralPath $doneDir -PathType Container
        Ensure-AgentBridgePlainDirectory `
            -LiteralPath $doneDir `
            -Context 'claim archive directory'
        $doneDirCreated = -not $doneDirExisted
        foreach ($plan in $preparedPlans) {
            # STALE V2 MARKER: create archive temp from trusted bytes.
            $archiveTempResult = Invoke-AgentBridgeTrustedBytesCreateNew `
                -DestinationPath ([string]$plan.archive_temp_path) `
                -PublishBytes ([byte[]]$plan.archive_bytes) `
                -ExpectedSha256 ([string]$plan.archive_sha256) `
                -ExpectedLength ([long]$plan.archive_length) `
                -Context 'stale claim archive temp'
            if (-not [bool]$archiveTempResult.succeeded) {
                throw (
                    "stale claim archive temp create-new failed {0}: {1}" -f
                    [string]$plan.archive_temp_path,
                    $archiveTempResult.error.Message
                )
            }
            # STALE V2 MARKER: verify active source before trusted backup.
            $null = Assert-AgentBridgeExpectedRegularFileSnapshot `
                -LiteralPath ([string]$plan.file.FullName) `
                -ExpectedSha256 ([string]$plan.source_backup_sha256) `
                -ExpectedLength ([long]$plan.source_backup_length) `
                -Context 'stale claim eligibility source'
            $backupResult = Invoke-AgentBridgeTrustedBytesCreateNew `
                -DestinationPath ([string]$plan.source_backup_path) `
                -PublishBytes ([byte[]]$plan.source_snapshot_bytes) `
                -ExpectedSha256 ([string]$plan.source_backup_sha256) `
                -ExpectedLength ([long]$plan.source_backup_length) `
                -Context 'stale claim exact recovery backup'
            if (-not [bool]$backupResult.succeeded) {
                throw (
                    "stale claim exact recovery backup failed {0}: {1}" -f
                    [string]$plan.source_backup_path,
                    $backupResult.error.Message
                )
            }
            $plan.source_backup_prepared = $true
        }
    } catch {
        $primaryError = $_.Exception
        $rollbackFailures = New-Object `
            System.Collections.Generic.List[string]
        Invoke-BridgeStaleBatchRollback `
            -Plans $preparedPlans `
            -DoneDir $doneDir `
            -RemoveDoneDir $doneDirCreated `
            -Failures $rollbackFailures
        throw (
            New-BridgeStaleBatchFailureMessage `
                -PrimaryError $primaryError `
                -RollbackFailures $rollbackFailures `
                -Plans $preparedPlans
        )
    }

    try {
        foreach ($plan in $preparedPlans) {
            # File.Move has create-new destination semantics on Windows
            # PowerShell 5.1 and PowerShell 7.
            [System.IO.File]::Move(
                [string]$plan.archive_temp_path,
                [string]$plan.done_path
            )
            $plan.archive_temp_consumed = $true
            $plan.archive_published = $true
            [System.IO.File]::Move(
                [string]$plan.file.FullName,
                [string]$plan.source_quarantine_path
            )
            $plan.source_removed = $true
            # A successful Move revokes the eligibility snapshot's automatic
            # restore authority. Only a complete same-handle Q capture may
            # authorize any later rollback publication.
            $plan.rollback_source_restore_allowed = $false
            $plan.rollback_source_generation = 'post-move-capture-unavailable'
            try {
                $quarantineSnapshot = `
                    Get-AgentBridgeExclusiveRawFileCapture `
                        -LiteralPath `
                            ([string]$plan.source_quarantine_path) `
                        -Context 'stale claim quarantined source'
            } catch {
                throw (
                    "stale claim post-Move quarantine capture failed {0}; " +
                    "eligibility restore suppressed: {1}" -f
                    [string]$plan.source_quarantine_path,
                    $_.Exception.Message
                )
            }
            $quarantineSha256 = [string]$quarantineSnapshot.sha256
            $quarantineLength = [long]$quarantineSnapshot.length
            $plan.source_quarantine_capture_completed = $true
            $plan.rollback_source_bytes = `
                [byte[]]$quarantineSnapshot.bytes
            $plan.rollback_source_sha256 = $quarantineSha256
            $plan.rollback_source_length = $quarantineLength
            $plan.rollback_source_restore_allowed = $true
            $plan.rollback_source_generation = 'post-move-capture'
            $captureMatchesEligibility = [bool](-not (
                $quarantineLength -ne
                    [long]$plan.source_backup_length -or
                $quarantineSha256 -cne
                    [string]$plan.source_backup_sha256
            ))
            if (
                -not [bool]$quarantineSnapshot.identity_verified -or
                -not $captureMatchesEligibility
            ) {
                $restoreDetail = ''
                if (
                    Test-Path `
                        -LiteralPath $plan.file.FullName `
                        -PathType Leaf
                ) {
                    $restoreDetail = (
                        "active source path was concurrently recreated; " +
                        "foreign quarantine retained"
                    )
                } else {
                    try {
                        $freshRestore = `
                            Invoke-AgentBridgeTrustedBytesCreateNew `
                                -DestinationPath `
                                    ([string]$plan.file.FullName) `
                                -PublishBytes `
                                    ([byte[]]$quarantineSnapshot.bytes) `
                                -ExpectedSha256 $quarantineSha256 `
                                -ExpectedLength $quarantineLength `
                                -Context `
                                    'restored fresh stale-claim generation'
                        if (-not [bool]$freshRestore.succeeded) {
                            throw $freshRestore.error
                        }
                        $plan.source_removed = $false
                        $restoreDetail = (
                            'captured fresh active source bytes restored'
                        )
                    } catch {
                        $restoreDetail = (
                            "foreign active source restore failed {0} from " +
                            "{1}: {2}" -f
                            $plan.file.FullName,
                            [string]$plan.source_quarantine_path,
                            $_.Exception.Message
                        )
                    }
                }
                if (-not $captureMatchesEligibility) {
                    throw (
                        (
                            "quarantined active source identity mismatched " +
                            "{0}; expected={1}:{2}; actual={3}:{4}; {5}"
                        ) -f
                            [string]$plan.source_quarantine_path,
                            [string]$plan.source_backup_sha256,
                            [long]$plan.source_backup_length,
                            [string]$quarantineSha256,
                            [long]$quarantineLength,
                            $restoreDetail
                    )
                }
                throw (
                    "quarantined active source identity rejected {0} after " +
                    "complete capture: {1}; {2}" -f
                    [string]$plan.source_quarantine_path,
                    $quarantineSnapshot.identity_error.Message,
                    $restoreDetail
                )
            }
            $plan.source_quarantine_verified = $true
        }
    } catch {
        $primaryError = $_.Exception
        $rollbackFailures = New-Object `
            System.Collections.Generic.List[string]
        Invoke-BridgeStaleBatchRollback `
            -Plans $preparedPlans `
            -DoneDir $doneDir `
            -RemoveDoneDir $doneDirCreated `
            -Failures $rollbackFailures
        throw (
            New-BridgeStaleBatchFailureMessage `
                -PrimaryError $primaryError `
                -RollbackFailures $rollbackFailures `
                -Plans $preparedPlans
        )
    }

    $cleanupFailures = New-Object System.Collections.Generic.List[string]
    foreach ($plan in $preparedPlans) {
        foreach ($cleanupEntry in @(
                [pscustomobject]@{
                    label = 'archive temp'
                    path = [string]$plan.archive_temp_path
                },
                [pscustomobject]@{
                    label = 'source quarantine'
                    path = [string]$plan.source_quarantine_path
                },
                [pscustomobject]@{
                    label = 'source backup'
                    path = [string]$plan.source_backup_path
                }
            )) {
            if (-not (Test-Path -LiteralPath $cleanupEntry.path)) {
                continue
            }
            try {
                if ([string]$cleanupEntry.label -ceq 'archive temp') {
                    if ([bool]$plan.archive_temp_consumed) {
                        throw (
                            "path reappeared after the batch archive temp " +
                            "was moved; retained unowned artifact"
                        )
                    }
                    Remove-AgentBridgeExactFile `
                        -LiteralPath ([string]$cleanupEntry.path) `
                        -ExpectedSha256 ([string]$plan.archive_sha256) `
                        -ExpectedLength ([long]$plan.archive_length) `
                        -Context 'committed archive temp cleanup'
                } elseif (
                    [string]$cleanupEntry.label -ceq 'source backup'
                ) {
                    if (
                        Test-Path `
                            -LiteralPath $plan.source_quarantine_path
                    ) {
                        throw (
                            "source quarantine cleanup is incomplete; " +
                            "retained recovery backup"
                        )
                    }
                    if (-not [bool]$plan.source_backup_prepared) {
                        throw 'exact backup identity was not prepared'
                    }
                    # STALE V2 MARKER: exact-handle committed backup cleanup.
                    Remove-AgentBridgeExactFile `
                        -LiteralPath ([string]$cleanupEntry.path) `
                        -ExpectedSha256 `
                            ([string]$plan.source_backup_sha256) `
                        -ExpectedLength `
                            ([long]$plan.source_backup_length) `
                        -Context 'committed source backup cleanup'
                } elseif (
                    [string]$cleanupEntry.label -ceq 'source quarantine'
                ) {
                    if (-not [bool]$plan.source_quarantine_verified) {
                        throw 'batch ownership was not verified'
                    }
                    # STALE V2 MARKER: exact-handle committed quarantine cleanup.
                    Remove-AgentBridgeExactFile `
                        -LiteralPath ([string]$cleanupEntry.path) `
                        -ExpectedSha256 `
                            ([string]$plan.source_backup_sha256) `
                        -ExpectedLength `
                            ([long]$plan.source_backup_length) `
                        -Context 'committed source quarantine cleanup'
                }
            } catch {
                [void]$cleanupFailures.Add(
                    (
                        "{0} cleanup failed {1}: {2}" -f
                        [string]$cleanupEntry.label,
                        [string]$cleanupEntry.path,
                        $_.Exception.Message
                    )
                )
            }
        }
    }
    $committedPlans = @($preparedPlans)
    if ($cleanupFailures.Count -gt 0) {
        Write-Warning `
            -Message (
                "stale claim batch committed but ancillary cleanup failed; " +
                "required events and results will continue; " +
                "cleanup failures: {0}; state: {1}" -f
                ($cleanupFailures -join ' | '),
                (Get-BridgeStaleBatchState -Plans $preparedPlans)
            ) `
            -WarningAction Continue
    }
}
} finally {
    Exit-AgentBridgeMutationLock -Lock $mutationLock
}

foreach ($plan in $committedPlans) {
    $taskId = [string]$plan.task_id
    $agent = [string]$plan.agent
    $legacyTokenless = [bool]$plan.legacy_tokenless
    $storedHeartbeatUtc = [string]$plan.stored_heartbeat_utc
    $claimLeaseSeconds = [int]$plan.claim_lease_seconds
    $effectiveExpiresText = [string]$plan.effective_expires_text
    $effectiveLeaseSeconds = [long]$plan.effective_lease_seconds
    $ageSeconds = [long]$plan.age_seconds
    $leaseAnchorField = [string]$plan.lease_anchor_field
    $leaseAnchorUtc = [string]$plan.lease_anchor_utc
    $donePath = [string]$plan.done_path

    # Emit release event only after the whole stale batch committed.
    try {
        $writeEvent = Join-Path $PSScriptRoot 'Write-AgentEvent.ps1'
        if (Test-Path -LiteralPath $writeEvent -PathType Leaf) {
            $payload = [pscustomobject]@{
                task_id            = $taskId
                claim_agent        = $agent
                claim_claimed_at_utc = [string]$plan.stored_claimed_at_utc
                claim_run_id       = [string]$plan.claim_run_id
                last_heartbeat_utc = $storedHeartbeatUtc
                age_seconds        = [long]$ageSeconds
                stale_threshold_s  = $effectiveLeaseSeconds
                claim_lease_seconds = $claimLeaseSeconds
                claim_lease_expires_utc = $effectiveExpiresText
                lease_anchor_field = $leaseAnchorField
                lease_anchor_utc   = $leaseAnchorUtc
                legacy_tokenless   = $legacyTokenless
                archive_released_at_utc = [string]$plan.released_at_text
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
        Write-Warning `
            -Message (
                "stale-lease release event emit failed: {0}" -f
                $_.Exception.Message
            ) `
            -WarningAction Continue
    }

    if (-not $Quiet) {
        Write-Host ("STALE LEASE SWEPT: {0} by {1} ({2} {3}s old)" -f `
            $taskId, $agent, $leaseAnchorField, [long]$ageSeconds) `
            -ForegroundColor Yellow
    }

    [pscustomobject]@{
        task_id        = $taskId
        agent          = $agent
        age_seconds    = [long]$ageSeconds
        archived_path  = $donePath
    }
}
