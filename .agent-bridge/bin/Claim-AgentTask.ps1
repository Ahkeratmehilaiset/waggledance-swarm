#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $Agent,
    [Parameter(Mandatory)] [string] $TaskId,
    [Parameter(Mandatory)] [string] $Summary,
    [ValidateSet('read-only','write')] [string] $Mode = 'read-only',
    [string[]] $WriteScope = @(),
    [string] $RunId = '',
    [string] $Role = '',
    [string] $AgentUuid = '',
    [string[]] $Capabilities = @(),
    [int] $LeaseSeconds = 0,
    [switch] $Force
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$sessionIdentity = Join-Path $PSScriptRoot 'AgentBridgeSessionIdentity.ps1'
. $sessionIdentity
Assert-AgentBridgeSessionIdentity -RequestedAgent $Agent
Assert-AgentBridgeTaskId -TaskId $TaskId
$Mode = $Mode.ToLowerInvariant()

function Assert-NoBridgePrivateMarker {
    param(
        [Parameter(Mandatory)] [string] $Label,
        [AllowNull()] $Value
    )

    foreach ($item in @($Value)) {
        $text = [string]$item
        foreach ($marker in @('PRIVATE_MARKER', '_DO_NOT_LEAK')) {
            if ($text.IndexOf(
                    $marker,
                    [System.StringComparison]::OrdinalIgnoreCase
                ) -ge 0) {
                throw "Bridge claim $Label contains a private marker"
            }
        }
    }
}

if (-not $RunId) {
    $RunId = if ($env:AGENT_BRIDGE_RUN_ID) {
        [string]$env:AGENT_BRIDGE_RUN_ID
    } else {
        ''
    }
}
if (-not $Role -and $env:AGENT_BRIDGE_ROLE) {
    $Role = [string]$env:AGENT_BRIDGE_ROLE
}
if (-not $AgentUuid -and $env:AGENT_BRIDGE_AGENT_UUID) {
    $AgentUuid = [string]$env:AGENT_BRIDGE_AGENT_UUID
}
if (@($Capabilities).Count -eq 0 -and $env:AGENT_BRIDGE_CAPABILITIES) {
    $Capabilities = @([string]$env:AGENT_BRIDGE_CAPABILITIES)
}
$Capabilities = @(
    @($Capabilities) |
        ForEach-Object { [string]$_ -split '[,;]' } |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ }
)
$normalizedWriteScope = New-Object System.Collections.Generic.List[string]
$seenWriteScope = New-Object 'System.Collections.Generic.HashSet[string]' (
    [System.StringComparer]::Ordinal
)
foreach ($scopeValue in @($WriteScope)) {
    foreach ($scopePart in ([string]$scopeValue -split ',')) {
        $scope = $scopePart.Trim()
        $overlapScope = (($scope -replace '\\','/').Trim('/'))
        if (
            -not $scope -or
            -not $overlapScope -or
            -not $seenWriteScope.Add($scope)
        ) {
            continue
        }
        [void]$normalizedWriteScope.Add($scope)
    }
}
$WriteScope = @($normalizedWriteScope)
$sessionId = [string]$env:AGENT_BRIDGE_SESSION_ID
if ($RunId -and $RunId -notmatch '^[A-Za-z0-9._:-]{1,128}$') {
    throw "run_id must match ^[A-Za-z0-9._:-]{1,128}$"
}
if ($sessionId -and $sessionId -notmatch '^[A-Za-z0-9._:-]{1,128}$') {
    throw "session_id must match ^[A-Za-z0-9._:-]{1,128}$"
}
if ($Role -and $Role -cnotmatch '^[a-z][a-z0-9_-]{1,32}$') {
    throw "role must match ^[a-z][a-z0-9_-]{1,32}$"
}
if ($AgentUuid -and $AgentUuid -notmatch '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$') {
    throw "agent_uuid must be a UUID"
}
if ($AgentUuid) {
    $AgentUuid = $AgentUuid.ToLowerInvariant()
}
foreach ($capability in @($Capabilities)) {
    if ($capability -cnotmatch '^[a-z][a-z0-9_.:-]{1,64}$') {
        throw "capability must match ^[a-z][a-z0-9_.:-]{1,64}$"
    }
}
if ([string]::IsNullOrWhiteSpace($TaskId)) {
    throw 'Bridge event type=claim requires non-empty -TaskId before writing'
}
if ($Mode -eq 'write' -and $WriteScope.Count -eq 0) {
    throw 'write claims require at least one -WriteScope path'
}
if ($PSBoundParameters.ContainsKey('LeaseSeconds')) {
    if ($LeaseSeconds -le 0) {
        throw 'lease_seconds must be a positive Int32'
    }
} else {
    $LeaseSeconds = 900
}

Assert-NoBridgePrivateMarker -Label 'task_id' -Value $TaskId
Assert-NoBridgePrivateMarker -Label 'summary' -Value $Summary
Assert-NoBridgePrivateMarker -Label 'write_scope' -Value $WriteScope
Assert-NoBridgePrivateMarker -Label 'run_id' -Value $RunId
Assert-NoBridgePrivateMarker -Label 'role' -Value $Role
Assert-NoBridgePrivateMarker -Label 'agent_uuid' -Value $AgentUuid
Assert-NoBridgePrivateMarker -Label 'session_id' -Value $sessionId
Assert-NoBridgePrivateMarker -Label 'capabilities' -Value $Capabilities
$ownerContext = Get-AgentBridgeClaimOwnerContext

# R13 (Codex scout 2026-05-09): honor AGENT_BRIDGE_RUNTIME_ROOT so
# per-agent worktrees can share one runtime state directory. Codex
# blocker 2026-05-09T13:11Z: if the env var is SET, USE IT - do not
# silently fall back to per-worktree state, that would split-brain
# the agents on first-run / typo / new-root paths. We create the
# directory if missing (first-run bootstrap) and fail loudly on
# malformed paths via -ErrorAction Stop.
$bridgeRoot = if ($env:AGENT_BRIDGE_RUNTIME_ROOT) {
    [string]$env:AGENT_BRIDGE_RUNTIME_ROOT
} else {
    Split-Path -Parent $PSScriptRoot
}
if (-not (Test-Path -LiteralPath $bridgeRoot -PathType Container)) {
    [void](New-Item -ItemType Directory -Path $bridgeRoot -Force -ErrorAction Stop)
}
$claimsDir = Join-Path (Join-Path $bridgeRoot 'work_queue') 'claims'
if (-not (Test-Path -LiteralPath $claimsDir)) {
    [void](New-Item -ItemType Directory -Path $claimsDir -Force)
}

function Normalize-Scope {
    param([string] $Scope)
    return (($Scope -replace '\\','/').Trim('/')).ToLowerInvariant()
}

function Expand-ScopeList {
    param([object[]] $Scope)

    foreach ($scopeValue in @($Scope)) {
        if ($scopeValue -isnot [string]) { continue }
        foreach ($scopePart in ([string]$scopeValue -split ',')) {
            $normalized = Normalize-Scope $scopePart.Trim()
            if ($normalized) { $normalized }
        }
    }
}

function Test-ScopeOverlap {
    param([object[]] $A, [object[]] $B)
    $normalizedA = @(Expand-ScopeList -Scope $A)
    $normalizedB = @(Expand-ScopeList -Scope $B)
    if ($normalizedA.Count -gt 0 -and $normalizedB.Count -eq 0) {
        # Historical writers could persist write claims without a usable
        # scope. Treat them as a wildcard instead of allowing unsafe overlap.
        return $true
    }
    foreach ($a in $normalizedA) {
        foreach ($b in $normalizedB) {
            if ($a -eq '*' -or $b -eq '*') { return $true }
            if ($a -eq $b) { return $true }
            if ($a.StartsWith($b + '/') -or $b.StartsWith($a + '/')) { return $true }
        }
    }
    return $false
}

function Stop-BridgeClaim {
    param([Parameter(Mandatory)] [string] $Message, [Parameter(Mandatory)] [int] $Code)
    [Console]::Error.WriteLine($Message)
    exit $Code
}

function Get-CurrentGitBranch {
    try {
        $branch = (& git branch --show-current 2>$null)
        if ($LASTEXITCODE -eq 0) { return [string]$branch }
    } catch {}
    return ''
}

# R15 follow-up (Codex review 2026-05-09): claim acquisition is the
# path that most needs stale-lease continuity. Status/read helpers
# sweep opportunistically too, but a claim-first agent must not be
# blocked forever by an expired conflicting write claim.
$sweepScript = Join-Path $PSScriptRoot 'Invoke-StaleClaimSweep.ps1'
if (Test-Path -LiteralPath $sweepScript -PathType Leaf) {
    try {
        & $sweepScript -Quiet | Out-Null
    } catch {
        Write-Warning ("stale-claim sweep before claim acquisition failed: {0}" -f $_.Exception.Message)
    }
}

$mutationLock = Enter-AgentBridgeMutationLock -BridgeRoot $bridgeRoot
try {
$activeClaims = @(Get-ChildItem -Path $claimsDir -Filter '*.json' -File -ErrorAction SilentlyContinue)
$parsedClaims = @()
foreach ($file in $activeClaims) {
    try {
        $existing = ConvertFrom-AgentBridgeJson -Json (
            Get-Content -Raw -Path $file.FullName -Encoding UTF8
        )
    } catch {
        continue
    }
    $parsedClaims += [pscustomobject]@{
        file = $file
        claim = $existing
    }
}

try {
    $preferredClaimPath = Assert-AgentBridgePreferredClaimPath `
        -ClaimsDir $claimsDir `
        -TaskId $TaskId
} catch {
    Stop-BridgeClaim -Message ([string]$_.Exception.Message) -Code 3
}

$taskMatches = @(
    $parsedClaims |
        Where-Object {
            $_.claim.PSObject.Properties['task_id'] -and
            $_.claim.task_id -is [string] -and
            [string]$_.claim.task_id -ceq $TaskId
        }
)
if ($taskMatches.Count -gt 1) {
    $duplicatePaths = @($taskMatches | ForEach-Object { $_.file.FullName })
    Stop-BridgeClaim `
        -Message ("duplicate active claim records for exact task_id '{0}': {1}" -f $TaskId, ($duplicatePaths -join ', ')) `
        -Code 3
}

$forceUpdateExisting = $false
if ($taskMatches.Count -eq 1) {
    $matchedEntry = $taskMatches[0]
    $existing = $matchedEntry.claim
    $claimPath = $matchedEntry.file.FullName
    if (
        -not $existing.PSObject.Properties['agent'] -or
        $existing.agent -isnot [string]
    ) {
        Stop-BridgeClaim `
            -Message ("claim has missing or non-string agent: {0}" -f $claimPath) `
            -Code 3
    }
    $existingAgent = [string]$existing.agent
    if (-not $Force) {
        Stop-BridgeClaim -Message ("task already claimed by {0}: {1}" -f $existingAgent, $claimPath) -Code 2
    }
    if ($existingAgent -cne $Agent -and $Agent -notin @('operator','system')) {
        Stop-BridgeClaim -Message ("cannot force-update claim owned by {0}: {1}" -f $existingAgent, $claimPath) -Code 3
    }
    if ($existingAgent -ceq $Agent) {
        try {
            Assert-AgentBridgeClaimOwner `
                -Claim $existing `
                -OwnerContext $ownerContext `
                -Operation 'force-update'
        } catch {
            Stop-BridgeClaim -Message $_.Exception.Message -Code 3
        }
    }
    $forceUpdateExisting = $true
} else {
    $claimPath = $preferredClaimPath
}

foreach ($entry in $parsedClaims) {
    if ($forceUpdateExisting -and
        $entry.file.FullName -ceq $claimPath) {
        continue
    }
    $existing = $entry.claim
    if ($Mode -eq 'write') {
        $existingMode = if (
            $existing.PSObject.Properties['mode'] -and
            $existing.mode -is [string]
        ) {
            [string]$existing.mode
        } else {
            ''
        }
        if ($existingMode -ceq 'read-only') {
            continue
        }
        if ($existingMode -cne 'write') {
            Stop-BridgeClaim `
                -Message (
                    "write-scope conflict with active claim {0} by {1}: " +
                    "stored mode is missing, non-string, or invalid" -f
                    $existing.task_id,
                    $existing.agent
                ) `
                -Code 3
        }
        $existingScope = if (
            $existing.PSObject.Properties['write_scope']
        ) {
            @($existing.write_scope)
        } else {
            @()
        }
        if (Test-ScopeOverlap -A $WriteScope -B $existingScope) {
            Stop-BridgeClaim -Message ("write-scope conflict with active claim {0} by {1}: {2}" -f $existing.task_id, $existing.agent, (($existingScope) -join ', ')) -Code 3
        }
    }
}

$nowUtc = (Get-Date).ToUniversalTime().ToString('o')
$leaseExpiresUtc = ([DateTime]::Parse($nowUtc).ToUniversalTime()).AddSeconds($LeaseSeconds).ToString('o')
$claim = [ordered]@{
    claimed_at_utc      = $nowUtc
    # R15: stale-claim-lease. last_heartbeat_utc is bumped by
    # Send-Liveness.ps1 on heartbeat/liveness-active events for
    # this agent; Invoke-StaleClaimSweep.ps1 archives claims whose
    # effective stored lease (900s by default, matching the Python work
    # queue). On creation it equals claimed_at_utc so a
    # claim that's never heart-beated still has a finite lease.
    last_heartbeat_utc  = $nowUtc
    agent               = $Agent
    task_id             = $TaskId
    summary             = $Summary
    mode                = $Mode
    write_scope         = @($WriteScope)
    run_id              = $RunId
    lease_seconds       = $LeaseSeconds
    claim_lease_expires_utc = $leaseExpiresUtc
    owner_session_id    = $ownerContext.session_id
    owner_token_sha256  = $ownerContext.token_sha256
    owner_pid           = $ownerContext.owner_pid
    owner_process_start_utc = $ownerContext.owner_process_start_utc
    # This is the short-lived PowerShell writer process, not the owning
    # agent session and never an ownership or liveness signal.
    writer_pid          = $PID
    writer_pid_semantics = 'diagnostic_only'
    cwd                 = (Get-Location).Path
    git_branch          = Get-CurrentGitBranch
}
if ($Role) { $claim['role'] = $Role }
if ($AgentUuid) { $claim['agent_uuid'] = $AgentUuid }
if (@($Capabilities).Count -gt 0) { $claim['capabilities'] = @($Capabilities) }
$json = ($claim | ConvertTo-Json -Depth 8)

$encoding = New-Object System.Text.UTF8Encoding($false)
if ($forceUpdateExisting) {
    # Update the exact claim record discovered by task_id. In particular, this
    # preserves Python's collision-resistant filename for task IDs containing
    # slashes instead of creating a second sanitized-only claim.
    $tmpClaim = "$claimPath.tmp.$PID.$([guid]::NewGuid().ToString('N'))"
    try {
        [System.IO.File]::WriteAllText($tmpClaim, $json, $encoding)
        if (-not (Test-Path -LiteralPath $claimPath -PathType Leaf)) {
            throw "claim disappeared before force-update: $claimPath"
        }
        Update-AgentBridgeFileFromTemp `
            -TempPath $tmpClaim `
            -DestinationPath $claimPath
    } catch {
        try { Remove-Item -LiteralPath $tmpClaim -Force -ErrorAction SilentlyContinue } catch {}
        throw
    }
} else {
    $tmpClaim = "$claimPath.tmp.$PID.$([guid]::NewGuid().ToString('N'))"
    try {
        [System.IO.File]::WriteAllText($tmpClaim, $json, $encoding)
        # Publish only a fully closed sibling. File.Move is atomic within the
        # claims directory and refuses an existing destination.
        [System.IO.File]::Move($tmpClaim, $claimPath)
    } catch {
        Stop-BridgeClaim -Message ("could not create claim, likely already exists: {0}" -f $claimPath) -Code 2
    } finally {
        if (Test-Path -LiteralPath $tmpClaim -PathType Leaf) {
            Remove-Item -LiteralPath $tmpClaim -Force `
                -ErrorAction SilentlyContinue
        }
    }
}
} finally {
    Exit-AgentBridgeMutationLock -Lock $mutationLock
}

& (Join-Path $PSScriptRoot 'Write-AgentEvent.ps1') `
    -Agent $Agent `
    -Type claim `
    -TaskId $TaskId `
    -Status active `
    -Message $Summary `
    -WriteScope $WriteScope `
    -RunId $RunId `
    -Role $Role `
    -AgentUuid $AgentUuid `
    -Capabilities $Capabilities | Out-Null
[pscustomobject]$claim
