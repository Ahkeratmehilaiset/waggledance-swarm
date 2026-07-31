#requires -Version 5.1
<#
.SYNOPSIS
    Smoke test for R15 stale-claim-lease auto-release.

.DESCRIPTION
    Operator gate 2026-05-09T~14:50Z: claim leases must survive a
    "5 min without heartbeat -> automatic release" test. This smoke
    proves the contract by:

      1. Creating an isolated AGENT_BRIDGE_RUNTIME_ROOT (no
         interference with the production runtime).
      2. Creating an active write claim for a foreign agent.
      3. Manually backdating the claim's last_heartbeat_utc to
         simulate "5+ min ago".
      4. Running Invoke-StaleClaimSweep.ps1 with -StaleSeconds 1
         and verifying:
           - the claim file is removed from work_queue/claims/.
           - a stale_lease archive lands in work_queue/done/.
           - a release/stale_lease event lands in shared/events.jsonl.
      5. Creating a FRESH claim (last_heartbeat_utc = now) and
         confirming the sweep does NOT touch it.
      6. Heartbeat-extension test: claim, sleep, heartbeat, run
         sweep with a threshold that would have killed the claim
         if heartbeat hadn't extended it. Verifies Send-Liveness
         actually bumps last_heartbeat_utc.
      7. operator/system claims are immune (privileged).

    Exit 0 on all expectations met, 1 otherwise. Cleanup is always
    attempted.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$bridgeBin = $PSScriptRoot
$claimTask = Join-Path $bridgeBin 'Claim-AgentTask.ps1'
$sweep = Join-Path $bridgeBin 'Invoke-StaleClaimSweep.ps1'
$sendLiveness = Join-Path $bridgeBin 'Send-Liveness.ps1'

$results = New-Object System.Collections.Generic.List[object]
function Add-Check {
    param(
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [bool] $Passed,
        [string] $Detail = ''
    )
    [void]$results.Add([pscustomobject]@{
        name = $Name; passed = $Passed; detail = $Detail
    })
    $marker = if ($Passed) { 'PASS' } else { 'FAIL' }
    $color  = if ($Passed) { 'Green' } else { 'Red' }
    Write-Host ("  [{0}] {1}" -f $marker, $Name) -ForegroundColor $color
    if ($Detail) { Write-Host "        $Detail" }
}

function Convert-SmokeTimestampUtc {
    param([Parameter(Mandatory)] [object] $Value)
    if ($Value -is [DateTime]) {
        return ([DateTime]$Value).ToUniversalTime()
    }
    if ($Value -is [DateTimeOffset]) {
        return ([DateTimeOffset]$Value).UtcDateTime
    }
    $styles = (
        [System.Globalization.DateTimeStyles]::AssumeUniversal -bor
        [System.Globalization.DateTimeStyles]::AdjustToUniversal
    )
    return ([DateTimeOffset]::Parse(
        [string]$Value,
        [System.Globalization.CultureInfo]::InvariantCulture,
        $styles
    )).UtcDateTime
}

function New-SmokeRawClaim {
    param(
        [Parameter(Mandatory)] [string] $TaskId,
        [string] $Agent = 'codex',
        [Parameter(Mandatory)] [string] $ClaimedAtUtc,
        [Parameter(Mandatory)] [string] $HeartbeatUtc,
        [object] $LeaseSeconds = 300,
        [string] $ExpiresUtc = '',
        [object] $OwnerPid = 1234,
        [string] $OwnerProcessStartUtc = '2026-07-28T00:00:00Z'
    )

    return [ordered]@{
        claimed_at_utc = $ClaimedAtUtc
        last_heartbeat_utc = $HeartbeatUtc
        agent = $Agent
        task_id = $TaskId
        summary = "R15 parity fixture: $TaskId"
        mode = 'read-only'
        write_scope = @()
        run_id = 'r15-parity-smoke'
        lease_seconds = $LeaseSeconds
        claim_lease_expires_utc = $ExpiresUtc
        owner_session_id = 'r15-parity-owner'
        owner_token_sha256 = ('a' * 64)
        owner_pid = $OwnerPid
        owner_process_start_utc = $OwnerProcessStartUtc
    }
}

function Write-SmokeRawClaim {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] $Claim
    )

    $Claim | ConvertTo-Json -Depth 8 |
        Set-Content -LiteralPath $Path -Encoding UTF8
}

$tempRoot = Join-Path $env:TEMP `
    "bridge-r15-stale-lease-$([guid]::NewGuid().ToString('N').Substring(0, 12))"
$savedEnv = $env:AGENT_BRIDGE_RUNTIME_ROOT
$identityIsolation = Join-Path $PSScriptRoot 'BridgeSmokeIdentityIsolation.ps1'
. $identityIsolation
$identitySnapshot = Enter-BridgeSmokeIdentityIsolation

try {
    Write-Host 'Bridge stale-lease smoke test' -ForegroundColor Cyan
    Write-Host '============================='
    Write-Host "Temp runtime root: $tempRoot"
    Write-Host ''

    $env:AGENT_BRIDGE_RUNTIME_ROOT = $tempRoot
    $claimsDir = Join-Path $tempRoot 'work_queue\claims'
    $doneDir = Join-Path $tempRoot 'work_queue\done'
    $eventsPath = Join-Path $tempRoot 'shared\events.jsonl'

    # ── 1: Create stale claim and verify sweep archives it ─────
    Write-Host '1. Stale claim auto-release:'
    & $claimTask -Agent codex -TaskId 'r15-smoke-stale' `
        -Summary 'R15 smoke: stale claim' -Mode write `
        -WriteScope 'tests/smoke/stale' | Out-Null
    $claimPath = Join-Path $claimsDir 'r15-smoke-stale.json'

    # Backdate beyond the stored 900-second lease to simulate stale.
    $obj = Get-Content -Raw -Path $claimPath -Encoding UTF8 |
        ConvertFrom-Json
    $past = (Get-Date).AddMinutes(-20).ToUniversalTime().ToString('o')
    $pastLeaseExpiry = ([DateTime]::Parse($past).ToUniversalTime()).AddSeconds(300).ToString('o')
    $obj.last_heartbeat_utc = $past
    $obj.claim_lease_expires_utc = $pastLeaseExpiry
    ($obj | ConvertTo-Json -Depth 8) |
        Set-Content -Path $claimPath -Encoding UTF8

    $swept = & $sweep -StaleSeconds 1 -Quiet
    Add-Check -Name 'stale claim removed from claims/' `
        -Passed (-not (Test-Path -LiteralPath $claimPath)) `
        -Detail "claim file no longer in work_queue/claims/"

    $doneFiles = @(Get-ChildItem -Path $doneDir -Filter 'r15-smoke-stale*.stale_lease.json' `
                                 -File -ErrorAction SilentlyContinue)
    Add-Check -Name 'stale claim archived to done/ as stale_lease' `
        -Passed ($doneFiles.Count -ge 1) `
        -Detail "found $($doneFiles.Count) stale_lease archive(s)"

    # Sweep return list non-empty
    $sweptCount = @($swept).Count
    Add-Check -Name 'sweep returned the swept claim' `
        -Passed ($sweptCount -ge 1) `
        -Detail "sweep returned $sweptCount item(s)"

    # release/stale_lease event landed
    $eventsText = if (Test-Path -LiteralPath $eventsPath) {
        (Get-Content -Path $eventsPath -Tail 30 -Encoding UTF8) -join "`n"
    } else { '' }
    $sawReleaseEvent = ($eventsText -match '"type":"release","task_id":"r15-smoke-stale","status":"stale_lease"' -or
                          $eventsText -match 'release.*stale_lease.*r15-smoke-stale' -or
                          $eventsText -match 'r15-smoke-stale.*stale_lease')
    Add-Check -Name 'release/stale_lease event emitted to events.jsonl' `
        -Passed $sawReleaseEvent `
        -Detail "marker present in events.jsonl tail"

    # Direct claim acquisition must also sweep stale blockers before
    # conflict checks. This is the continuity path used when an agent
    # wakes and immediately claims the next write task.
    Write-Host ''
    Write-Host '1b. Direct claim acquisition clears stale conflicting claims:'
    & $claimTask -Agent claude -TaskId 'r15-smoke-direct-stale-owner' `
        -Summary 'R15 smoke: stale conflicting owner' -Mode write `
        -WriteScope 'tests/smoke/direct' | Out-Null
    $directOwnerPath = Join-Path $claimsDir 'r15-smoke-direct-stale-owner.json'
    $directOwner = Get-Content -Raw -Path $directOwnerPath -Encoding UTF8 |
        ConvertFrom-Json
    $directOwner.last_heartbeat_utc = $past
    $directOwner.claim_lease_expires_utc = $pastLeaseExpiry
    ($directOwner | ConvertTo-Json -Depth 8) |
        Set-Content -Path $directOwnerPath -Encoding UTF8

    & $claimTask -Agent codex -TaskId 'r15-smoke-direct-successor' `
        -Summary 'R15 smoke: successor claim after stale blocker' `
        -Mode write -WriteScope 'tests/smoke/direct' | Out-Null
    $directSuccessorPath = Join-Path $claimsDir 'r15-smoke-direct-successor.json'
    $directArchives = @(Get-ChildItem -Path $doneDir `
        -Filter 'r15-smoke-direct-stale-owner*.stale_lease.json' `
        -File -ErrorAction SilentlyContinue)
    Add-Check -Name 'claim acquisition sweeps stale conflicting write claim' `
        -Passed ((-not (Test-Path -LiteralPath $directOwnerPath)) -and `
                 (Test-Path -LiteralPath $directSuccessorPath) -and `
                 ($directArchives.Count -ge 1)) `
        -Detail "stale owner removed, successor claim present, archives=$($directArchives.Count)"

    # ── 2: Fresh claim NOT swept ────────────────────────────────
    Write-Host ''
    Write-Host '2. Fresh claim is NOT auto-released:'
    & $claimTask -Agent codex -TaskId 'r15-smoke-fresh' `
        -Summary 'R15 smoke: fresh claim' -Mode write `
        -WriteScope 'tests/smoke/fresh' | Out-Null
    $freshClaimPath = Join-Path $claimsDir 'r15-smoke-fresh.json'

    $sweptFresh = & $sweep -StaleSeconds 60 -Quiet
    $sweptFreshCount = @($sweptFresh).Count
    Add-Check -Name 'fresh claim survives 60s threshold' `
        -Passed ((Test-Path -LiteralPath $freshClaimPath) -and ($sweptFreshCount -eq 0)) `
        -Detail "claim file present + 0 swept"

    # ── 3: Heartbeat extends the lease ─────────────────────────
    Write-Host ''
    Write-Host '3. Heartbeat extends the lease:'
    # Backdate fresh claim slightly so a sweep with stale=2s would
    # kill it, then heartbeat to bump, then sweep again.
    $obj2 = Get-Content -Raw -Path $freshClaimPath -Encoding UTF8 |
        ConvertFrom-Json
    $oldTs = (Get-Date).AddSeconds(-30).ToUniversalTime().ToString('o')
    $obj2.last_heartbeat_utc = $oldTs
    ($obj2 | ConvertTo-Json -Depth 8) |
        Set-Content -Path $freshClaimPath -Encoding UTF8

    # Heartbeat as codex (the claim owner) - this should bump
    # last_heartbeat_utc on r15-smoke-fresh.
    & $sendLiveness -Agent codex -Heartbeat `
        -Message 'R15 smoke: heartbeat to extend lease' | Out-Null

    # Re-read claim and confirm last_heartbeat_utc was bumped.
    $obj3 = Get-Content -Raw -Path $freshClaimPath -Encoding UTF8 |
        ConvertFrom-Json
    $bumpedTs = Convert-SmokeTimestampUtc -Value $obj3.last_heartbeat_utc
    $oldTsParsed = Convert-SmokeTimestampUtc -Value $oldTs
    Add-Check -Name 'heartbeat bumped last_heartbeat_utc on own claim' `
        -Passed ($bumpedTs -gt $oldTsParsed) `
        -Detail "old=$oldTs new=$([string]$obj3.last_heartbeat_utc)"

    # Now sweep with stale=2s; the bumped claim must survive.
    $sweptAfterHb = & $sweep -StaleSeconds 2 -Quiet
    $sweptAfterHbCount = @($sweptAfterHb).Count
    Add-Check -Name 'heartbeat-extended claim survives next sweep' `
        -Passed ((Test-Path -LiteralPath $freshClaimPath) -and ($sweptAfterHbCount -eq 0)) `
        -Detail "after heartbeat + sweep stale=2s; 0 swept"

    # ── 4: operator/system claims are immune ───────────────────
    Write-Host ''
    Write-Host '4. operator/system claims immune from sweep:'
    $opClaimPath = Join-Path $claimsDir 'r15-smoke-operator.json'
    $operatorFixtureNow = (Get-Date).ToUniversalTime()
    [ordered]@{
        claimed_at_utc = $operatorFixtureNow.ToString('o')
        last_heartbeat_utc = $operatorFixtureNow.ToString('o')
        agent = 'operator'
        task_id = 'r15-smoke-operator'
        summary = 'R15 smoke: operator claim fixture'
        mode = 'read-only'
        write_scope = @()
        run_id = ''
        lease_seconds = 300
        claim_lease_expires_utc = $operatorFixtureNow.AddSeconds(300).ToString('o')
    } | ConvertTo-Json -Depth 8 |
        Set-Content -LiteralPath $opClaimPath -Encoding UTF8

    # Backdate it
    $opObj = Get-Content -Raw -Path $opClaimPath -Encoding UTF8 |
        ConvertFrom-Json
    $opObj.last_heartbeat_utc = $past  # 20 min ago
    $opObj.claim_lease_expires_utc = $pastLeaseExpiry
    ($opObj | ConvertTo-Json -Depth 8) |
        Set-Content -Path $opClaimPath -Encoding UTF8

    $sweptOp = & $sweep -StaleSeconds 1 -Quiet
    Add-Check -Name 'operator claim survives even when stale' `
        -Passed (Test-Path -LiteralPath $opClaimPath) `
        -Detail "operator/system claims are privileged"

    # ── 5: Per-claim lease overrides default threshold ─────────
    Write-Host ''
    Write-Host '5. Per-claim lease fields override the default threshold:'
    & $claimTask -Agent codex -TaskId 'r15-smoke-per-claim-lease' `
        -Summary 'R15 smoke: per-claim long lease' -Mode write `
        -WriteScope 'tests/smoke/per-claim-lease' -LeaseSeconds 1000 | Out-Null
    $perClaimPath = Join-Path $claimsDir 'r15-smoke-per-claim-lease.json'
    $perClaim = Get-Content -Raw -Path $perClaimPath -Encoding UTF8 |
        ConvertFrom-Json
    $perClaim.last_heartbeat_utc = (Get-Date).AddSeconds(-500).ToUniversalTime().ToString('o')
    $perClaim.PSObject.Properties.Remove('claim_lease_expires_utc')
    ($perClaim | ConvertTo-Json -Depth 8) |
        Set-Content -Path $perClaimPath -Encoding UTF8

    $sweptPerClaimLease = & $sweep -Quiet
    $sweptPerClaimLeaseCount = @($sweptPerClaimLease).Count
    Add-Check -Name 'per-claim lease_seconds spares a claim past global default' `
        -Passed ((Test-Path -LiteralPath $perClaimPath) -and
                 ($sweptPerClaimLeaseCount -eq 0)) `
        -Detail "lease_seconds=1000 + 500s-old claim => 0 swept"

    & $claimTask -Agent codex -TaskId 'r15-smoke-per-claim-expires' `
        -Summary 'R15 smoke: per-claim future expiry' -Mode write `
        -WriteScope 'tests/smoke/per-claim-expires' -LeaseSeconds 100 | Out-Null
    $perExpiresPath = Join-Path $claimsDir 'r15-smoke-per-claim-expires.json'
    $perExpires = Get-Content -Raw -Path $perExpiresPath -Encoding UTF8 |
        ConvertFrom-Json
    $perExpires.last_heartbeat_utc = (Get-Date).AddSeconds(-500).ToUniversalTime().ToString('o')
    $perExpires.claim_lease_expires_utc = (Get-Date).AddSeconds(500).ToUniversalTime().ToString('o')
    ($perExpires | ConvertTo-Json -Depth 8) |
        Set-Content -Path $perExpiresPath -Encoding UTF8

    $sweptPerClaimExpires = & $sweep -Quiet
    $sweptPerClaimExpiresCount = @($sweptPerClaimExpires).Count
    Add-Check -Name 'future claim_lease_expires_utc spares a claim past lease_seconds' `
        -Passed ((Test-Path -LiteralPath $perExpiresPath) -and
                 ($sweptPerClaimExpiresCount -eq 0)) `
        -Detail "lease_seconds=100 + future expires_utc => 0 swept"

    # ── 6: Default threshold from env var for legacy claims ─────
    Write-Host ''
    Write-Host '6. Default lease threshold honors AGENT_BRIDGE_STALE_LEASE_SECONDS env for legacy claims:'
    & $claimTask -Agent codex -TaskId 'r15-smoke-legacy-env' `
        -Summary 'R15 smoke: legacy env threshold claim' -Mode write `
        -WriteScope 'tests/smoke/legacy-env' | Out-Null
    $legacyEnvPath = Join-Path $claimsDir 'r15-smoke-legacy-env.json'
    $legacyEnv = Get-Content -Raw -Path $legacyEnvPath -Encoding UTF8 |
        ConvertFrom-Json
    $legacyEnv.PSObject.Properties.Remove('lease_seconds')
    $legacyEnv.owner_pid = 'malformed'
    $legacyEnv.claimed_at_utc = (Get-Date).AddSeconds(-500).ToUniversalTime().ToString('o')
    # An incomplete/malformed owner record is legacy: neither of these
    # unauthenticated future fields may extend its lease.
    $legacyEnv.last_heartbeat_utc = (Get-Date).AddDays(1).ToUniversalTime().ToString('o')
    $legacyEnv.claim_lease_expires_utc = (Get-Date).AddDays(1).ToUniversalTime().ToString('o')
    ($legacyEnv | ConvertTo-Json -Depth 8) |
        Set-Content -Path $legacyEnvPath -Encoding UTF8

    # Set env to a very small value, claim won't be swept yet
    # (legacy claim is 500s old).
    $env:AGENT_BRIDGE_STALE_LEASE_SECONDS = '1000'

    # With env=1000, a claim created 500s ago should NOT trigger sweep.
    $sweptEnvHigh = & $sweep -Quiet
    $sweptEnvHighCount = @($sweptEnvHigh).Count
    Add-Check -Name 'env threshold 1000s spares a 500s-old claim' `
        -Passed ((Test-Path -LiteralPath $legacyEnvPath) -and
                 ($sweptEnvHighCount -eq 0)) `
        -Detail "legacy claimed_at=-500s + env=1000 => 0 swept"

    # Now lower env to 100s; claimed_at controls even though the mutable
    # heartbeat and explicit-expiry fields are both in the future.
    $env:AGENT_BRIDGE_STALE_LEASE_SECONDS = '100'
    $sweptEnvLow = & $sweep -Quiet
    $sweptEnvLowCount = @($sweptEnvLow).Count
    Add-Check -Name 'env threshold 100s sweeps a 500s-old claim' `
        -Passed ((-not (Test-Path -LiteralPath $legacyEnvPath)) -and
                 ($sweptEnvLowCount -ge 1)) `
        -Detail "legacy claimed_at=-500s + future heartbeat/expiry + env=100 => >=1 swept"

    # Reset env for cleanup
    Remove-Item Env:AGENT_BRIDGE_STALE_LEASE_SECONDS `
        -ErrorAction SilentlyContinue

    # ── 7: Cross-runtime lease and classification parity ─────────
    Write-Host ''
    Write-Host '7. Cross-runtime lease and owner classification parity:'
    $future = (Get-Date).AddDays(1).ToUniversalTime().ToString('o')

    $farExpiryPath = Join-Path $claimsDir 'r15-smoke-far-expiry.json'
    Write-SmokeRawClaim -Path $farExpiryPath -Claim (
        New-SmokeRawClaim `
            -TaskId 'r15-smoke-far-expiry' `
            -ClaimedAtUtc $past `
            -HeartbeatUtc $past `
            -LeaseSeconds 300 `
            -ExpiresUtc '2099-01-01T00:00:00Z'
    )

    $largeLeasePath = Join-Path $claimsDir 'r15-smoke-large-lease.json'
    Write-SmokeRawClaim -Path $largeLeasePath -Claim (
        New-SmokeRawClaim `
            -TaskId 'r15-smoke-large-lease' `
            -ClaimedAtUtc $past `
            -HeartbeatUtc $past `
            -LeaseSeconds ([long][int]::MaxValue + 1)
    )

    $looseOwnerPath = Join-Path $claimsDir 'r15-smoke-loose-owner.json'
    Write-SmokeRawClaim -Path $looseOwnerPath -Claim (
        New-SmokeRawClaim `
            -TaskId 'r15-smoke-loose-owner' `
            -ClaimedAtUtc $past `
            -HeartbeatUtc $future `
            -ExpiresUtc $future `
            -OwnerProcessStartUtc 'July 30, 2026 12:00:00'
    )

    $floatPidPath = Join-Path $claimsDir 'r15-smoke-float-pid.json'
    Write-SmokeRawClaim -Path $floatPidPath -Claim (
        New-SmokeRawClaim `
            -TaskId 'r15-smoke-float-pid' `
            -ClaimedAtUtc $past `
            -HeartbeatUtc $future `
            -ExpiresUtc $future `
            -OwnerPid ([double]1)
    )

    $mixedOperatorPath = Join-Path $claimsDir `
        'r15-smoke-mixed-operator.json'
    Write-SmokeRawClaim -Path $mixedOperatorPath -Claim (
        New-SmokeRawClaim `
            -TaskId 'r15-smoke-mixed-operator' `
            -Agent 'Operator' `
            -ClaimedAtUtc $past `
            -HeartbeatUtc $past `
            -LeaseSeconds 1
    )

    $fallbackAnchorPath = Join-Path $claimsDir `
        'r15-smoke-fallback-anchor.json'
    Write-SmokeRawClaim -Path $fallbackAnchorPath -Claim (
        New-SmokeRawClaim `
            -TaskId 'r15-smoke-fallback-anchor' `
            -ClaimedAtUtc $past `
            -HeartbeatUtc 'not-a-canonical-time' `
            -LeaseSeconds 300
    )

    $payloadFilterPath = Join-Path $claimsDir `
        'r15-smoke-payload-filter.json'
    $payloadFilter = New-SmokeRawClaim `
        -TaskId 'r15-smoke-payload-filter' `
        -ClaimedAtUtc $past `
        -HeartbeatUtc $past `
        -LeaseSeconds 1
    $payloadFilter['write_scope'] = $null
    $payloadFilter['capabilities'] = $null
    $payloadFilter['owner_token'] = 'raw-owner-secret'
    $payloadFilter['unknown_field'] = 'must-not-persist'
    Write-SmokeRawClaim -Path $payloadFilterPath -Claim $payloadFilter

    $paritySweep = @(& $sweep -StaleSeconds 300 -Quiet)
    Add-Check -Name 'far-future explicit expiry survives without Int32 failure' `
        -Passed (Test-Path -LiteralPath $farExpiryPath) `
        -Detail '2099 expiry retained and sweep completed'
    Add-Check -Name 'over-Int32 stored lease falls back and is swept' `
        -Passed (-not (Test-Path -LiteralPath $largeLeasePath)) `
        -Detail '2147483648 is malformed, so the 300s fallback applies'
    Add-Check -Name 'loose owner timestamp is legacy and cannot extend lease' `
        -Passed (-not (Test-Path -LiteralPath $looseOwnerPath)) `
        -Detail 'future heartbeat/expiry ignored for noncanonical owner'
    Add-Check -Name 'floating owner pid is legacy and cannot extend lease' `
        -Passed (-not (Test-Path -LiteralPath $floatPidPath)) `
        -Detail 'future heartbeat/expiry ignored for floating owner_pid'
    Add-Check -Name 'mixed-case Operator spelling is not privileged' `
        -Passed (-not (Test-Path -LiteralPath $mixedOperatorPath)) `
        -Detail 'only exact lowercase operator/system are privileged'

    $fallbackArchives = @(Get-ChildItem -Path $doneDir `
        -Filter 'r15-smoke-fallback-anchor*.stale_lease.json' `
        -File -ErrorAction SilentlyContinue)
    $fallbackReason = if ($fallbackArchives.Count -eq 1) {
        [string](
            Get-Content -Raw -Path $fallbackArchives[0].FullName `
                -Encoding UTF8 |
                ConvertFrom-Json
        ).release_reason
    } else {
        ''
    }
    Add-Check -Name 'fallback release reason reports claimed_at_utc anchor' `
        -Passed ($fallbackReason -match '^claimed_at_utc was [0-9]+s old;') `
        -Detail "reason=$fallbackReason"

    $payloadArchives = @(Get-ChildItem -Path $doneDir `
        -Filter 'r15-smoke-payload-filter*.stale_lease.json' `
        -File -ErrorAction SilentlyContinue)
    $payloadArchive = if ($payloadArchives.Count -eq 1) {
        Get-Content -Raw -Path $payloadArchives[0].FullName -Encoding UTF8 |
            ConvertFrom-Json
    } else {
        $null
    }
    $payloadFiltered = (
        $null -ne $payloadArchive -and
        -not $payloadArchive.PSObject.Properties['owner_token'] -and
        -not $payloadArchive.PSObject.Properties['unknown_field'] -and
        -not $payloadArchive.PSObject.Properties['capabilities'] -and
        @($payloadArchive.write_scope).Count -eq 0
    )
    Add-Check -Name 'archive allowlist strips secret and malformed metadata' `
        -Passed $payloadFiltered `
        -Detail 'owner_token/unknown/capabilities absent; write_scope=[]'

    # ── 8: Collision-resistant archive names ────────────────────
    Write-Host ''
    Write-Host '8. Collision-resistant create-new archive names:'
    $slashTaskId = 'r15/smoke-slash'
    $slashClaimPath = Join-Path $claimsDir 'legacy-r15-smoke-slash.json'
    Write-SmokeRawClaim -Path $slashClaimPath -Claim (
        New-SmokeRawClaim `
            -TaskId $slashTaskId `
            -ClaimedAtUtc $past `
            -HeartbeatUtc $past `
            -LeaseSeconds 1
    )
    $collisionOnePath = Join-Path $claimsDir 'collide+a.json'
    $collisionTwoPath = Join-Path $claimsDir 'collide=a.json'
    Write-SmokeRawClaim -Path $collisionOnePath -Claim (
        New-SmokeRawClaim `
            -TaskId 'r15-collision-one' `
            -ClaimedAtUtc $past `
            -HeartbeatUtc $past `
            -LeaseSeconds 1
    )
    Write-SmokeRawClaim -Path $collisionTwoPath -Claim (
        New-SmokeRawClaim `
            -TaskId 'r15-collision-two' `
            -ClaimedAtUtc $past `
            -HeartbeatUtc $past `
            -LeaseSeconds 1
    )
    $collisionSweep = @(& $sweep -StaleSeconds 1 -Quiet)

    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $slashDigest = [System.BitConverter]::ToString(
            $sha256.ComputeHash(
                [System.Text.Encoding]::UTF8.GetBytes($slashTaskId)
            )
        ).Replace('-', '').ToLowerInvariant().Substring(0, 12)
    } finally {
        $sha256.Dispose()
    }
    $slashArchives = @(Get-ChildItem -Path $doneDir `
        -Filter "r15_smoke-slash-$slashDigest.*.stale_lease.json" `
        -File -ErrorAction SilentlyContinue)
    Add-Check -Name 'slash task archive uses canonical hashed basename' `
        -Passed (
            (-not (Test-Path -LiteralPath $slashClaimPath)) -and
            $slashArchives.Count -eq 1
        ) `
        -Detail "expected digest=$slashDigest archives=$($slashArchives.Count)"

    $collisionOneArchives = @(Get-ChildItem -Path $doneDir `
        -Filter 'r15-collision-one.*.stale_lease.json' `
        -File -ErrorAction SilentlyContinue)
    $collisionTwoArchives = @(Get-ChildItem -Path $doneDir `
        -Filter 'r15-collision-two.*.stale_lease.json' `
        -File -ErrorAction SilentlyContinue)
    Add-Check -Name 'source-basename collision preserves both archives' `
        -Passed (
            (-not (Test-Path -LiteralPath $collisionOnePath)) -and
            (-not (Test-Path -LiteralPath $collisionTwoPath)) -and
            $collisionOneArchives.Count -eq 1 -and
            $collisionTwoArchives.Count -eq 1
        ) `
        -Detail 'collide+a.json and collide=a.json archived independently'

    $createNewTaskId = 'r15-create-new-existing-archive'
    $createNewClaimPath = Join-Path $claimsDir "$createNewTaskId.json"
    Write-SmokeRawClaim -Path $createNewClaimPath -Claim (
        New-SmokeRawClaim `
            -TaskId $createNewTaskId `
            -ClaimedAtUtc $past `
            -HeartbeatUtc $past `
            -LeaseSeconds 1
    )
    $sentinelText = 'existing-archive-must-not-be-overwritten'
    $sentinelPaths = New-Object System.Collections.Generic.List[string]
    $sentinelStart = (Get-Date).ToUniversalTime()
    foreach ($offset in 0..5) {
        $sentinelStamp = $sentinelStart.AddSeconds($offset).ToString(
            'yyyyMMddTHHmmssZ'
        )
        $sentinelPath = Join-Path $doneDir `
            "$createNewTaskId.$sentinelStamp.stale_lease.json"
        Set-Content -LiteralPath $sentinelPath -Value $sentinelText `
            -Encoding UTF8
        [void]$sentinelPaths.Add($sentinelPath)
    }
    $existingArchiveRefused = $false
    try {
        & $sweep -StaleSeconds 1 -Quiet | Out-Null
    } catch {
        $existingArchiveRefused = (
            $_.Exception.Message -match
            'stale archive destination already exists'
        )
    }
    $sentinelsUnchanged = @(
        $sentinelPaths |
            Where-Object {
                (Get-Content -Raw -LiteralPath $_ -Encoding UTF8).Trim() -ceq
                    $sentinelText
            }
    ).Count -eq $sentinelPaths.Count
    Add-Check -Name 'existing archive is create-new and leaves claim active' `
        -Passed (
            $existingArchiveRefused -and
            (Test-Path -LiteralPath $createNewClaimPath) -and
            $sentinelsUnchanged
        ) `
        -Detail 'pre-existing archive bytes unchanged; active claim retained'

    # ── 9: Exact-task duplicate preflight ───────────────────────
    Write-Host ''
    Write-Host '9. Exact-task duplicate preflight is zero-write:'
    $duplicateOnePath = Join-Path $claimsDir 'r15-duplicate-one.json'
    $duplicateTwoPath = Join-Path $claimsDir 'r15-duplicate-two.json'
    $duplicateClaim = New-SmokeRawClaim `
        -TaskId 'r15-duplicate-exact-task' `
        -ClaimedAtUtc $past `
        -HeartbeatUtc $past `
        -LeaseSeconds 1
    Write-SmokeRawClaim -Path $duplicateOnePath -Claim $duplicateClaim
    Write-SmokeRawClaim -Path $duplicateTwoPath -Claim $duplicateClaim
    $doneCountBeforeDuplicate = @(
        Get-ChildItem -Path $doneDir -Filter '*.json' -File `
            -ErrorAction SilentlyContinue
    ).Count
    $duplicateRefused = $false
    try {
        & $sweep -StaleSeconds 1 -Quiet | Out-Null
    } catch {
        $duplicateRefused = (
            $_.Exception.Message -match
            'duplicate active claim records for exact task_id'
        )
    }
    $doneCountAfterDuplicate = @(
        Get-ChildItem -Path $doneDir -Filter '*.json' -File `
            -ErrorAction SilentlyContinue
    ).Count
    Add-Check -Name 'duplicate exact tasks refuse before archive mutation' `
        -Passed (
            $duplicateRefused -and
            (Test-Path -LiteralPath $duplicateOnePath) -and
            (Test-Path -LiteralPath $duplicateTwoPath) -and
            $doneCountAfterDuplicate -eq $doneCountBeforeDuplicate
        ) `
        -Detail 'both claims remain and done/ count is unchanged'

} finally {
    Exit-BridgeSmokeIdentityIsolation -Snapshot $identitySnapshot
    $env:AGENT_BRIDGE_RUNTIME_ROOT = $savedEnv
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force `
            -ErrorAction SilentlyContinue
        Write-Host ''
        Write-Host "Cleanup: removed $tempRoot"
    }
}

Write-Host ''
Write-Host 'Summary' -ForegroundColor Cyan
Write-Host '======='
$failed = @($results | Where-Object { -not $_.passed })
$passed = @($results | Where-Object { $_.passed })
Write-Host ("  passed: {0}" -f $passed.Count) -ForegroundColor Green
if ($failed.Count -gt 0) {
    Write-Host ("  failed: {0}" -f $failed.Count) -ForegroundColor Red
    foreach ($f in $failed) {
        Write-Host ("    - {0}: {1}" -f $f.name, $f.detail) -ForegroundColor Red
    }
    exit 1
}
exit 0
