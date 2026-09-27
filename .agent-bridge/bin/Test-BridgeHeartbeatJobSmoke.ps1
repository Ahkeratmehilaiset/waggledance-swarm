#requires -Version 5.1
<#
.SYNOPSIS
    R23.1 smoke test for Start-BridgeHeartbeat.ps1.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$bridgeBin = $PSScriptRoot
$claimTask = Join-Path $bridgeBin 'Claim-AgentTask.ps1'
$heartbeat = Join-Path $bridgeBin 'Start-BridgeHeartbeat.ps1'

$tempRoot = Join-Path $env:TEMP "bridge-r23-1-heartbeat-$([guid]::NewGuid().ToString('N').Substring(0,12))"
$savedRoot = $env:AGENT_BRIDGE_RUNTIME_ROOT
$savedToggle = $env:WAGGLE_BRIDGE_HEARTBEAT_ENABLED
# A lane shell carries its own owner context; the smoke sets identities itself.
$savedOwnerSession = $env:AGENT_BRIDGE_OWNER_SESSION_ID
Remove-Item Env:AGENT_BRIDGE_OWNER_SESSION_ID -ErrorAction SilentlyContinue

function Read-Claim {
    param([string] $RuntimeRoot, [string] $TaskId)
    $safe = (($TaskId -replace '[^A-Za-z0-9._-]', '_').Trim('_'))
    $path = Join-Path (Join-Path $RuntimeRoot 'work_queue\claims') ($safe + '.json')
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    return (Get-Content -Raw -Path $path -Encoding UTF8 | ConvertFrom-Json)
}

function Read-EventCount {
    param([string] $RuntimeRoot)
    $path = Join-Path (Join-Path $RuntimeRoot 'shared') 'events.jsonl'
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return 0 }
    return @((Get-Content -LiteralPath $path -Encoding UTF8 -ErrorAction SilentlyContinue)).Count
}

function Convert-ClaimTimestampUtc {
    param([Parameter(Mandatory)] [object] $Value)

    if ($Value -is [DateTime]) {
        return ([DateTime]$Value).ToUniversalTime()
    }

    $styles = [System.Globalization.DateTimeStyles]::AssumeUniversal -bor
        [System.Globalization.DateTimeStyles]::AdjustToUniversal
    return [DateTime]::Parse(
        [string]$Value,
        [System.Globalization.CultureInfo]::InvariantCulture,
        $styles
    ).ToUniversalTime()
}

function Get-SmokeClaimPath {
    param(
        [Parameter(Mandatory)] [string] $RuntimeRoot,
        [Parameter(Mandatory)] [string] $TaskId
    )
    $safe = ($TaskId -replace '[^A-Za-z0-9._-]', '_').Trim('_')
    return (Join-Path (Join-Path (Join-Path $RuntimeRoot 'work_queue') 'claims') ($safe + '.json'))
}

try {
    $env:AGENT_BRIDGE_RUNTIME_ROOT = $tempRoot
    $env:WAGGLE_BRIDGE_HEARTBEAT_ENABLED = '1'

    Write-Host 'R23.1 heartbeat job smoke test' -ForegroundColor Cyan
    Write-Host '================================'
    Write-Host "Temp runtime root: $tempRoot"

    # B7 proof 1: a claim taken WITHOUT a session identity cannot be kept
    # alive. There is no fallback identity, so a bare agent name is not
    # enough to extend someone's lease.
    Remove-Item Env:AGENT_BRIDGE_OWNER_TOKEN -ErrorAction SilentlyContinue
    Remove-Item Env:AGENT_BRIDGE_RUN_ID -ErrorAction SilentlyContinue
    $anonTask = 'r23-1-heartbeat-smoke-anon'
    & $claimTask -Agent codex -TaskId $anonTask -Summary 'anon' -Mode write `
        -WriteScope 'waggledance/anon' | Out-Null
    $anonBefore = Read-Claim -RuntimeRoot $tempRoot -TaskId $anonTask
    Start-Sleep -Milliseconds 60
    & $heartbeat -Agent codex -RuntimeRoot $tempRoot -IntervalMs 50 -MaxIterations 1 2>&1 | Out-Null
    $anonAfter = Read-Claim -RuntimeRoot $tempRoot -TaskId $anonTask
    if ((Convert-ClaimTimestampUtc $anonAfter.last_heartbeat_utc) -ne `
        (Convert-ClaimTimestampUtc $anonBefore.last_heartbeat_utc)) {
        Write-Host "  [FAIL] identity-less claim was extended" -ForegroundColor Red
        exit 1
    }
    Write-Host "  [PASS] identity-less claim is not extendable (no fallback identity)" -ForegroundColor Green
    Remove-Item -LiteralPath (Get-SmokeClaimPath -RuntimeRoot $tempRoot -TaskId $anonTask) -Force -ErrorAction SilentlyContinue

    # A real session mints an owner token; only its SHA-256 is persisted.
    $env:AGENT_BRIDGE_RUN_ID = 'r23-1-smoke-session'
    $env:AGENT_BRIDGE_OWNER_TOKEN = 'smoke-owner-token-aaaa'

    $taskId = 'r23-1-heartbeat-smoke'
    & $claimTask -Agent codex -TaskId $taskId -Summary 'heartbeat smoke' -Mode write -WriteScope 'waggledance/core' | Out-Null
    $before = Read-Claim -RuntimeRoot $tempRoot -TaskId $taskId
    if (-not $before.PSObject.Properties['owner_token_sha256'] -or
        -not [string]$before.owner_token_sha256) {
        Write-Host "  [FAIL] claim did not record owner identity" -ForegroundColor Red
        exit 1
    }
    if (([string]$before.owner_token_sha256) -eq 'smoke-owner-token-aaaa') {
        Write-Host "  [FAIL] raw owner token was persisted to the claim" -ForegroundColor Red
        exit 1
    }
    Write-Host "  [PASS] claim records owner identity as a hash, not the raw token" -ForegroundColor Green
    Start-Sleep -Milliseconds 120

    & $heartbeat -Agent codex -RuntimeRoot $tempRoot -SessionId 'r23-1-smoke-session' `
        -IntervalMs 50 -MaxIterations 1 | Out-Null
    $after = Read-Claim -RuntimeRoot $tempRoot -TaskId $taskId

    # B7 proof 2: a DIFFERENT session of the same agent must not be able
    # to extend this claim - session identity, not agent name, is the
    # authority.
    $foreignBefore = Read-Claim -RuntimeRoot $tempRoot -TaskId $taskId
    Start-Sleep -Milliseconds 60
    $env:AGENT_BRIDGE_OWNER_TOKEN = 'different-session-token-bbbb'
    & $heartbeat -Agent codex -RuntimeRoot $tempRoot -SessionId 'r23-1-other-session' `
        -IntervalMs 50 -MaxIterations 1 2>&1 | Out-Null
    $foreignAfter = Read-Claim -RuntimeRoot $tempRoot -TaskId $taskId
    if ((Convert-ClaimTimestampUtc $foreignAfter.last_heartbeat_utc) -ne `
        (Convert-ClaimTimestampUtc $foreignBefore.last_heartbeat_utc)) {
        Write-Host "  [FAIL] a foreign session extended another session's claim" -ForegroundColor Red
        exit 1
    }
    Write-Host "  [PASS] foreign session cannot extend this session's claim" -ForegroundColor Green
    $env:AGENT_BRIDGE_OWNER_TOKEN = 'smoke-owner-token-aaaa'

    # B7 proof 3: an archived claim is never resurrected by a late bump.
    $reviveTask = 'r23-1-heartbeat-smoke-revive'
    & $claimTask -Agent codex -TaskId $reviveTask -Summary 'revive' -Mode write `
        -WriteScope 'waggledance/revive' | Out-Null
    $revivePath = Get-SmokeClaimPath -RuntimeRoot $tempRoot -TaskId $reviveTask
    $reviveArchive = Join-Path (Join-Path (Join-Path $tempRoot 'work_queue') 'done') 'revived.json'
    Move-Item -LiteralPath $revivePath -Destination $reviveArchive -Force
    & $heartbeat -Agent codex -RuntimeRoot $tempRoot -SessionId 'r23-1-smoke-session' `
        -IntervalMs 50 -MaxIterations 1 | Out-Null
    if (Test-Path -LiteralPath $revivePath) {
        Write-Host "  [FAIL] archived claim was recreated by a heartbeat" -ForegroundColor Red
        exit 1
    }
    Write-Host "  [PASS] archived claim is not recreated by a late bump" -ForegroundColor Green

    # B7 proof 4: the durable session heartbeat exists and is identity-bound.
    . (Join-Path $PSScriptRoot 'ClaimLeaseHeartbeat.ps1')
    $beatPath = Get-BridgeSessionHeartbeatPath -Root $tempRoot -SessionId 'r23-1-smoke-session' `
        -TokenSha256 ([string]$before.owner_token_sha256)
    if (-not (Test-Path -LiteralPath $beatPath -PathType Leaf)) {
        Write-Host "  [FAIL] session heartbeat artifact missing" -ForegroundColor Red
        exit 1
    }
    $beatObj = Get-Content -Raw -LiteralPath $beatPath -Encoding UTF8 | ConvertFrom-Json
    if (([string]$beatObj.owner_token_sha256) -ne ([string]$before.owner_token_sha256) -or
        [int]$beatObj.ttl_seconds -le 0) {
        Write-Host "  [FAIL] session heartbeat is not identity/TTL bound" -ForegroundColor Red
        exit 1
    }
    Write-Host "  [PASS] session heartbeat is identity-bound and TTL-bound" -ForegroundColor Green

    # B7 proof 5: two session ids that sanitize to the SAME name must
    # not share one artifact. Lossy safe-names would collide here and
    # let one session overwrite or delete the other's liveness proof.
    $collideA = 'wd/alpha'
    $collideB = 'wd_alpha'
    if ((Get-BridgeSafeName -Name $collideA) -ne (Get-BridgeSafeName -Name $collideB)) {
        Write-Host "  [FAIL] collision fixture is not actually colliding" -ForegroundColor Red
        exit 1
    }
    $sameToken = Get-BridgeSha256Hex -Value 'collision-token'
    $pathA = Get-BridgeSessionHeartbeatPath -Root $tempRoot -SessionId $collideA -TokenSha256 $sameToken
    $pathB = Get-BridgeSessionHeartbeatPath -Root $tempRoot -SessionId $collideB -TokenSha256 $sameToken
    if ($pathA -eq $pathB) {
        Write-Host "  [FAIL] sanitized session ids collide on one artifact" -ForegroundColor Red
        exit 1
    }
    Write-Host "  [PASS] sanitize-colliding session ids get distinct artifacts" -ForegroundColor Green

    # B7 proof 6: a stop must not retire a DIFFERENT session's artifact,
    # even for the same agent and same session id string.
    $succIdentity = Get-BridgeOwnerIdentity -SessionId 'successor-session' `
        -OwnerToken 'successor-token-cccc'
    [void](Write-BridgeSessionHeartbeat -Root $tempRoot -AgentName codex `
        -Identity $succIdentity)
    $succPath = Get-BridgeSessionHeartbeatPath -Root $tempRoot -SessionId 'successor-session' `
        -TokenSha256 ([string]$succIdentity.owner_token_sha256)
    $foreignIdentity = Get-BridgeOwnerIdentity -SessionId 'successor-session' `
        -OwnerToken 'a-different-token-dddd'
    $removedForeign = Remove-BridgeSessionHeartbeat -Root $tempRoot `
        -SessionId 'successor-session' -Identity $foreignIdentity
    if ($removedForeign -or -not (Test-Path -LiteralPath $succPath -PathType Leaf)) {
        Write-Host "  [FAIL] a foreign token retired another session heartbeat" -ForegroundColor Red
        exit 1
    }
    Write-Host "  [PASS] stop cannot retire a successor session heartbeat" -ForegroundColor Green

    # B7 proof 7: one session id, several owners. Every lane of one reboot
    # run shares the run id, and a lane restarted after a crash (no Stop)
    # gets it again. Each owner must still get its own live heartbeat;
    # keyed by the session alone, only the first writer ever did.
    $nowUtc = (Get-Date).ToUniversalTime()
    $sharedSession = 'wd-reboot-shared-run'
    $owners = @(
        (Get-BridgeOwnerIdentity -SessionId $sharedSession -OwnerToken 'lane-one-token'),
        (Get-BridgeOwnerIdentity -SessionId $sharedSession -OwnerToken 'lane-two-token'),
        (Get-BridgeOwnerIdentity -SessionId $sharedSession -OwnerToken 'restarted-lane-token')
    )
    foreach ($owner in $owners) {
        $written = Write-BridgeSessionHeartbeat -Root $tempRoot -AgentName codex `
            -Identity $owner -WarningVariable ownerWarnings -WarningAction SilentlyContinue
        $ownerClaim = [pscustomobject]@{
            owner_session_id = $owner.owner_session_id
            owner_token_sha256 = $owner.owner_token_sha256
        }
        if (-not $written -or @($ownerWarnings).Count -gt 0 -or
            -not (Test-BridgeSessionHeartbeatLive -Root $tempRoot -Claim $ownerClaim -NowUtc $nowUtc)) {
            Write-Host "  [FAIL] an owner sharing a session id got no live heartbeat" -ForegroundColor Red
            exit 1
        }
    }
    Write-Host "  [PASS] owners sharing one session id each keep a live heartbeat" -ForegroundColor Green

    # B7 proof 8: a file at an owner's own path that names someone else is
    # refused, and the refusal is not silent.
    $damagedOwner = Get-BridgeOwnerIdentity -SessionId 'damaged-session' -OwnerToken 'damaged-token'
    $damagedPath = Get-BridgeSessionHeartbeatPath -Root $tempRoot -SessionId 'damaged-session' `
        -TokenSha256 ([string]$damagedOwner.owner_token_sha256)
    [System.IO.File]::WriteAllText($damagedPath, (@{
        owner_session_id = 'damaged-session'
        owner_token_sha256 = (Get-BridgeSha256Hex -Value 'someone-else')
        last_beat_utc = $nowUtc.ToString('o')
        ttl_seconds = 180
    } | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
    $damagedWritten = Write-BridgeSessionHeartbeat -Root $tempRoot -AgentName codex `
        -Identity $damagedOwner -WarningVariable damagedWarnings -WarningAction SilentlyContinue
    if ($damagedWritten -or @($damagedWarnings).Count -ne 1) {
        Write-Host "  [FAIL] a foreign heartbeat file was overwritten or refused silently" -ForegroundColor Red
        exit 1
    }
    Write-Host "  [PASS] a foreign file at an owner's path is refused with a warning" -ForegroundColor Green

    # B7 proof 9: the process owner context is the identity. The consumer
    # loop removes AGENT_BRIDGE_RUN_ID and keeps AGENT_BRIDGE_OWNER_SESSION_ID;
    # a run id that disagrees with the owner session is no identity.
    $savedRunId = $env:AGENT_BRIDGE_RUN_ID
    try {
        Remove-Item Env:AGENT_BRIDGE_RUN_ID -ErrorAction SilentlyContinue
        $env:AGENT_BRIDGE_OWNER_SESSION_ID = 'consumer-codex-smoke'
        $consumerIdentity = Get-BridgeOwnerIdentity
        $env:AGENT_BRIDGE_RUN_ID = 'consumer-codex-smoke'
        $agreeingIdentity = Get-BridgeOwnerIdentity
        $env:AGENT_BRIDGE_RUN_ID = 'some-other-run'
        $disagreeingIdentity = Get-BridgeOwnerIdentity
    } finally {
        Remove-Item Env:AGENT_BRIDGE_OWNER_SESSION_ID -ErrorAction SilentlyContinue
        if ($null -ne $savedRunId) { $env:AGENT_BRIDGE_RUN_ID = $savedRunId }
        else { Remove-Item Env:AGENT_BRIDGE_RUN_ID -ErrorAction SilentlyContinue }
    }
    if ($null -eq $consumerIdentity -or
        [string]$consumerIdentity.owner_session_id -cne 'consumer-codex-smoke' -or
        $null -eq $agreeingIdentity -or
        [string]$agreeingIdentity.owner_session_id -cne 'consumer-codex-smoke' -or
        $null -ne $disagreeingIdentity) {
        Write-Host "  [FAIL] owner session precedence is wrong" -ForegroundColor Red
        exit 1
    }
    Write-Host "  [PASS] the owner session is the identity; a disagreeing run id is none" -ForegroundColor Green

    $beforeTs = Convert-ClaimTimestampUtc $before.last_heartbeat_utc
    $afterTs = Convert-ClaimTimestampUtc $after.last_heartbeat_utc
    $passed = ($afterTs -gt $beforeTs)

    if (-not $passed) {
        Write-Host "  [FAIL] heartbeat did not bump claim lease" -ForegroundColor Red
        Write-Host "        before=$($beforeTs.ToString('o')) after=$($afterTs.ToString('o'))"
        exit 1
    }

    Write-Host "  [PASS] heartbeat bumped claim lease" -ForegroundColor Green
    Write-Host "        before=$($beforeTs.ToString('o')) after=$($afterTs.ToString('o'))"

    $claimsDir = Join-Path $tempRoot 'work_queue\claims'
    Get-ChildItem -LiteralPath $claimsDir -Filter '*.json' -File -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction Stop
    $eventCountBeforeNoClaim = Read-EventCount -RuntimeRoot $tempRoot
    $noClaimOutput = & $heartbeat -Agent codex -RuntimeRoot $tempRoot `
        -IntervalMs 50 -MaxIterations 2 -MaxIdleWithoutClaimIterations 1
    $eventCountAfterNoClaim = Read-EventCount -RuntimeRoot $tempRoot
    $noClaimPassed = (
        $eventCountAfterNoClaim -eq $eventCountBeforeNoClaim -and
        ([string]$noClaimOutput) -match 'no active claim'
    )
    if ($noClaimPassed) {
        Write-Host "  [PASS] no-claim heartbeat skipped and exited bounded idle" -ForegroundColor Green
        Write-Host "        events_before=$eventCountBeforeNoClaim events_after=$eventCountAfterNoClaim"
        exit 0
    }

    Write-Host "  [FAIL] no-claim heartbeat wrote an event or did not exit bounded idle" -ForegroundColor Red
    Write-Host "        events_before=$eventCountBeforeNoClaim events_after=$eventCountAfterNoClaim output=$noClaimOutput"
    exit 1
} finally {
    if ($null -ne $savedRoot) {
        $env:AGENT_BRIDGE_RUNTIME_ROOT = $savedRoot
    } else {
        Remove-Item Env:AGENT_BRIDGE_RUNTIME_ROOT -ErrorAction SilentlyContinue
    }
    Remove-Item Env:AGENT_BRIDGE_OWNER_TOKEN -ErrorAction SilentlyContinue
    Remove-Item Env:AGENT_BRIDGE_RUN_ID -ErrorAction SilentlyContinue
    if ($null -ne $savedOwnerSession) {
        $env:AGENT_BRIDGE_OWNER_SESSION_ID = $savedOwnerSession
    }
    if ($null -ne $savedToggle) {
        $env:WAGGLE_BRIDGE_HEARTBEAT_ENABLED = $savedToggle
    } else {
        Remove-Item Env:WAGGLE_BRIDGE_HEARTBEAT_ENABLED -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
