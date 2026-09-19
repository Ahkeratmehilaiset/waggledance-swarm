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
$releaseTask = Join-Path $bridgeBin 'Release-AgentTask.ps1'

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

function ConvertTo-SmokeUtc {
    <#
        Read a claim timestamp without depending on host culture.
        ConvertFrom-Json returns [DateTime] for ISO-8601 strings, and
        casting one of those to [string] produces a US-formatted value
        that [DateTime]::Parse rejects under, for example, fi-FI.
    #>
    param([Parameter(Mandatory)] [AllowNull()] $Value)

    if ($null -eq $Value) { return $null }
    if ($Value -is [DateTime]) { return ([DateTime]$Value).ToUniversalTime() }
    $styles = [System.Globalization.DateTimeStyles]::AssumeUniversal -bor
        [System.Globalization.DateTimeStyles]::AdjustToUniversal
    return [DateTime]::Parse(
        [string]$Value,
        [System.Globalization.CultureInfo]::InvariantCulture,
        $styles
    ).ToUniversalTime()
}

$tempRoot = Join-Path $env:TEMP `
    "bridge-r15-stale-lease-$([guid]::NewGuid().ToString('N').Substring(0, 12))"
$savedEnv = $env:AGENT_BRIDGE_RUNTIME_ROOT

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

    # Backdate last_heartbeat_utc to 10 min ago to simulate stale.
    $obj = Get-Content -Raw -Path $claimPath -Encoding UTF8 |
        ConvertFrom-Json
    $past = (Get-Date).AddMinutes(-10).ToUniversalTime().ToString('o')
    $pastLeaseExpiry = ([DateTime]::Parse($past).ToUniversalTime()).AddSeconds(300).ToString('o')
    $obj.last_heartbeat_utc = $past
    $obj.claim_lease_expires_utc = $pastLeaseExpiry
    # B7: this check is about SWEEP behavior, not about the default
    # lease, so pin the lease explicitly. The write default is now 1800s
    # (see the dedicated lease-default checks below), which would
    # otherwise leave a 10-minute-old heartbeat comfortably fresh.
    $obj.lease_seconds = 300
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
    # B7: pin the lease - this check is about sweep-before-claim, not
    # about the new 1800s write default.
    $directOwner.lease_seconds = 300
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
    $bumpedTs = ConvertTo-SmokeUtc -Value $obj3.last_heartbeat_utc
    $oldTsParsed = ConvertTo-SmokeUtc -Value $oldTs
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
    & $claimTask -Agent operator -TaskId 'r15-smoke-operator' `
        -Summary 'R15 smoke: operator claim' -Mode read-only | Out-Null
    $opClaimPath = Join-Path $claimsDir 'r15-smoke-operator.json'

    # Backdate it
    $opObj = Get-Content -Raw -Path $opClaimPath -Encoding UTF8 |
        ConvertFrom-Json
    $opObj.last_heartbeat_utc = $past  # 10 min ago
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
    $legacyEnv.PSObject.Properties.Remove('claim_lease_expires_utc')
    $legacyEnv.last_heartbeat_utc = (Get-Date).AddSeconds(-500).ToUniversalTime().ToString('o')
    ($legacyEnv | ConvertTo-Json -Depth 8) |
        Set-Content -Path $legacyEnvPath -Encoding UTF8

    # Set env to a very small value, claim won't be swept yet
    # (legacy claim is 500s old).
    $env:AGENT_BRIDGE_STALE_LEASE_SECONDS = '1000'

    # With env=1000, 500s old heartbeat should NOT trigger sweep.
    $sweptEnvHigh = & $sweep -Quiet
    $sweptEnvHighCount = @($sweptEnvHigh).Count
    Add-Check -Name 'env threshold 1000s spares a 500s-old claim' `
        -Passed ((Test-Path -LiteralPath $legacyEnvPath) -and
                 ($sweptEnvHighCount -eq 0)) `
        -Detail "env=1000 + 500s-old claim => 0 swept"

    # Now lower env to 100s; 500s-old claim should be swept.
    $env:AGENT_BRIDGE_STALE_LEASE_SECONDS = '100'
    $sweptEnvLow = & $sweep -Quiet
    $sweptEnvLowCount = @($sweptEnvLow).Count
    Add-Check -Name 'env threshold 100s sweeps a 500s-old claim' `
        -Passed ((-not (Test-Path -LiteralPath $legacyEnvPath)) -and
                 ($sweptEnvLowCount -ge 1)) `
        -Detail "env=100 + 500s-old claim => >=1 swept"

    # Reset env for cleanup
    Remove-Item Env:AGENT_BRIDGE_STALE_LEASE_SECONDS `
        -ErrorAction SilentlyContinue

    # -- B7: expiry alone must not sweep a live session ------------
    Write-Host ''
    Write-Host '7. B7 session-bound liveness, identity and quarantine:'
    . (Join-Path $PSScriptRoot 'ClaimLeaseHeartbeat.ps1')

    $env:AGENT_BRIDGE_RUN_ID = 'b7-live-session'
    $env:AGENT_BRIDGE_OWNER_TOKEN = 'b7-live-token'
    & $claimTask -Agent codex -TaskId 'b7-live' -Summary 'B7 live session' `
        -Mode write -WriteScope 'tests/smoke/b7-live' -LeaseSeconds 60 | Out-Null
    $livePath = Join-Path $claimsDir 'b7-live.json'
    $liveObj = Get-Content -Raw -Path $livePath -Encoding UTF8 | ConvertFrom-Json
    $liveObj.last_heartbeat_utc = (Get-Date).AddSeconds(-600).ToUniversalTime().ToString('o')
    $liveObj.claim_lease_expires_utc = (Get-Date).AddSeconds(-540).ToUniversalTime().ToString('o')
    ($liveObj | ConvertTo-Json -Depth 8) | Set-Content -Path $livePath -Encoding UTF8

    $liveIdentity = Get-BridgeOwnerIdentity -SessionId 'b7-live-session'
    [void](Write-BridgeSessionHeartbeat -Root $tempRoot -AgentName codex `
        -Identity $liveIdentity -TtlSeconds 300)
    $sweptLive = @(& $sweep -StaleSeconds 1 -Quiet)
    Add-Check -Name 'expired claim survives while its session still beats' `
        -Passed ((Test-Path -LiteralPath $livePath) -and ($sweptLive.Count -eq 0)) `
        -Detail 'lease long expired, session heartbeat fresh => 0 swept'

    # Retiring the heartbeat (what Stop-AgentBridgeSession does) must
    # free the claim immediately on the next sweep.
    [void](Remove-BridgeSessionHeartbeat -Root $tempRoot -SessionId 'b7-live-session' `
        -Identity $liveIdentity)
    $sweptDead = @(& $sweep -StaleSeconds 1 -Quiet)
    Add-Check -Name 'same claim is swept once the session heartbeat is retired' `
        -Passed ((-not (Test-Path -LiteralPath $livePath)) -and ($sweptDead.Count -ge 1)) `
        -Detail 'heartbeat retired => claim archived'

    # A stale heartbeat (past its TTL) must not protect anything.
    & $claimTask -Agent codex -TaskId 'b7-stalebeat' -Summary 'B7 stale beat' `
        -Mode write -WriteScope 'tests/smoke/b7-stalebeat' -LeaseSeconds 60 | Out-Null
    $stalebeatPath = Join-Path $claimsDir 'b7-stalebeat.json'
    $stalebeatObj = Get-Content -Raw -Path $stalebeatPath -Encoding UTF8 | ConvertFrom-Json
    $stalebeatObj.last_heartbeat_utc = (Get-Date).AddSeconds(-600).ToUniversalTime().ToString('o')
    $stalebeatObj.claim_lease_expires_utc = (Get-Date).AddSeconds(-540).ToUniversalTime().ToString('o')
    ($stalebeatObj | ConvertTo-Json -Depth 8) | Set-Content -Path $stalebeatPath -Encoding UTF8
    $beatPath = Get-BridgeSessionHeartbeatPath -Root $tempRoot -SessionId 'b7-live-session'
    $stalePayload = [ordered]@{
        agent              = 'codex'
        owner_session_id   = 'b7-live-session'
        owner_token_sha256 = [string]$liveIdentity.owner_token_sha256
        last_beat_utc      = (Get-Date).AddSeconds(-600).ToUniversalTime().ToString('o')
        ttl_seconds        = 60
    }
    ($stalePayload | ConvertTo-Json -Depth 4) | Set-Content -Path $beatPath -Encoding UTF8
    $sweptStaleBeat = @(& $sweep -StaleSeconds 1 -Quiet)
    Add-Check -Name 'a session heartbeat past its TTL protects nothing' `
        -Passed ((-not (Test-Path -LiteralPath $stalebeatPath)) -and ($sweptStaleBeat.Count -ge 1)) `
        -Detail 'beat 600s old with ttl=60 => claim swept'
    Remove-Item -LiteralPath $beatPath -Force -ErrorAction SilentlyContinue

    # -- B7: unreadable claims are quarantined, not immortal --------
    $corruptPath = Join-Path $claimsDir 'b7-corrupt.json'
    Set-Content -Path $corruptPath -Value '{ this is not json' -Encoding UTF8
    $sweptCorrupt = @(& $sweep -StaleSeconds 1 -Quiet)
    $corruptArchives = @(Get-ChildItem -Path $doneDir -Filter 'b7-corrupt*.unreadable.json' `
        -File -ErrorAction SilentlyContinue)
    Add-Check -Name 'unreadable claim is quarantined instead of blocking forever' `
        -Passed ((-not (Test-Path -LiteralPath $corruptPath)) -and ($corruptArchives.Count -ge 1)) `
        -Detail "quarantined=$($corruptArchives.Count)"

    # -- B7: release is owner-bound, not agent-label-bound ----------
    & $claimTask -Agent codex -TaskId 'b7-release' -Summary 'B7 release owner' `
        -Mode write -WriteScope 'tests/smoke/b7-release' | Out-Null
    $releasePath = Join-Path $claimsDir 'b7-release.json'
    $env:AGENT_BRIDGE_OWNER_TOKEN = 'a-totally-different-session-token'
    $env:AGENT_BRIDGE_RUN_ID = 'b7-other-session'
    # The refusal is written with Write-Error, which the caller's
    # ErrorActionPreference would otherwise turn into a terminating
    # error; the refusal itself is the expected outcome here.
    try {
        & $releaseTask -Agent codex -TaskId 'b7-release' -Status done `
            -Message 'cross-session release attempt' 2>&1 | Out-Null
    } catch { }
    Add-Check -Name 'same agent, different session cannot release the claim' `
        -Passed (Test-Path -LiteralPath $releasePath) `
        -Detail 'owner_session_id/owner_token mismatch is refused'

    $env:AGENT_BRIDGE_OWNER_TOKEN = 'b7-live-token'
    $env:AGENT_BRIDGE_RUN_ID = 'b7-live-session'
    & $releaseTask -Agent codex -TaskId 'b7-release' -Status done `
        -Message 'owning session release' 2>&1 | Out-Null
    Add-Check -Name 'the owning session can release its own claim' `
        -Passed (-not (Test-Path -LiteralPath $releasePath)) `
        -Detail 'exact owner identity accepted'

    # Legacy (pre-B7) claims carry no owner identity: releasing one is
    # refused unless the caller says so explicitly.
    & $claimTask -Agent codex -TaskId 'b7-legacy' -Summary 'B7 legacy claim' `
        -Mode write -WriteScope 'tests/smoke/b7-legacy' | Out-Null
    $legacyPath = Join-Path $claimsDir 'b7-legacy.json'
    $legacyObj = Get-Content -Raw -Path $legacyPath -Encoding UTF8 | ConvertFrom-Json
    $legacyObj.PSObject.Properties.Remove('owner_session_id')
    $legacyObj.PSObject.Properties.Remove('owner_token_sha256')
    ($legacyObj | ConvertTo-Json -Depth 8) | Set-Content -Path $legacyPath -Encoding UTF8
    try {
        & $releaseTask -Agent codex -TaskId 'b7-legacy' -Status done `
            -Message 'legacy without switch' 2>&1 | Out-Null
    } catch { }
    Add-Check -Name 'unowned legacy claim is not released without the explicit switch' `
        -Passed (Test-Path -LiteralPath $legacyPath) `
        -Detail 'fail closed; no silent label-only adoption'
    & $releaseTask -Agent codex -TaskId 'b7-legacy' -Status done `
        -Message 'legacy with explicit switch' -AllowLegacyUnownedClaim 2>&1 | Out-Null
    Add-Check -Name 'unowned legacy claim releases with -AllowLegacyUnownedClaim' `
        -Passed (-not (Test-Path -LiteralPath $legacyPath)) `
        -Detail 'explicit adoption is allowed and visible'

    # -- B7: lease defaults --------------------------------------
    $writeLease = (& $claimTask -Agent codex -TaskId 'b7-lease-write' `
        -Summary 'B7 write lease' -Mode write -WriteScope 'tests/smoke/b7-lease').lease_seconds
    Add-Check -Name 'write claims default to the 1800s lease' `
        -Passed ([int]$writeLease -eq 1800) `
        -Detail "write lease_seconds=$writeLease"

    $roLease = (& $claimTask -Agent codex -TaskId 'b7-lease-ro' `
        -Summary 'B7 read-only lease' -Mode 'read-only').lease_seconds
    Add-Check -Name 'read-only claims get the short 120s lease' `
        -Passed ([int]$roLease -eq 120) `
        -Detail "read-only lease_seconds=$roLease"

    $env:AGENT_BRIDGE_STALE_LEASE_SECONDS = '3600'
    $roEnvLease = (& $claimTask -Agent codex -TaskId 'b7-lease-ro-env' `
        -Summary 'B7 read-only env' -Mode 'read-only').lease_seconds
    $roExplicitLease = (& $claimTask -Agent codex -TaskId 'b7-lease-ro-explicit' `
        -Summary 'B7 read-only explicit' -Mode 'read-only' -LeaseSeconds 3600).lease_seconds
    Remove-Item Env:AGENT_BRIDGE_STALE_LEASE_SECONDS -ErrorAction SilentlyContinue
    Add-Check -Name 'read-only lease cannot be lengthened by env or argument' `
        -Passed (([int]$roEnvLease -eq 120) -and ([int]$roExplicitLease -eq 120)) `
        -Detail "env=$roEnvLease explicit=$roExplicitLease (cap 120)"

    Remove-Item Env:AGENT_BRIDGE_OWNER_TOKEN -ErrorAction SilentlyContinue
    Remove-Item Env:AGENT_BRIDGE_RUN_ID -ErrorAction SilentlyContinue

} finally {
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
