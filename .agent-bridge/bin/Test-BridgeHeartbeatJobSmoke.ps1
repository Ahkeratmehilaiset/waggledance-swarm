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
    $beatPath = Get-BridgeSessionHeartbeatPath -Root $tempRoot -SessionId 'r23-1-smoke-session'
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
    $pathA = Get-BridgeSessionHeartbeatPath -Root $tempRoot -SessionId $collideA
    $pathB = Get-BridgeSessionHeartbeatPath -Root $tempRoot -SessionId $collideB
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
    $succPath = Get-BridgeSessionHeartbeatPath -Root $tempRoot -SessionId 'successor-session'
    $foreignIdentity = Get-BridgeOwnerIdentity -SessionId 'successor-session' `
        -OwnerToken 'a-different-token-dddd'
    $removedForeign = Remove-BridgeSessionHeartbeat -Root $tempRoot `
        -SessionId 'successor-session' -Identity $foreignIdentity
    if ($removedForeign -or -not (Test-Path -LiteralPath $succPath -PathType Leaf)) {
        Write-Host "  [FAIL] a foreign token retired another session heartbeat" -ForegroundColor Red
        exit 1
    }
    Write-Host "  [PASS] stop cannot retire a successor session heartbeat" -ForegroundColor Green

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
    if ($null -ne $savedToggle) {
        $env:WAGGLE_BRIDGE_HEARTBEAT_ENABLED = $savedToggle
    } else {
        Remove-Item Env:WAGGLE_BRIDGE_HEARTBEAT_ENABLED -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
