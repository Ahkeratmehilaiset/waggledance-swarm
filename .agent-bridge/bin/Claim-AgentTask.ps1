#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [ValidateScript({ $_ -cmatch '^[a-z][a-z0-9_-]{1,32}$' })] [string] $Agent,
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
. (Join-Path $PSScriptRoot 'BridgeResourceScope.ps1')

# B7: the one shared claim-lease / session-heartbeat implementation.
# Every lease writer goes through it, so there is a single CAS to review.
. (Join-Path $PSScriptRoot 'ClaimLeaseHeartbeat.ps1')

# A session bound to one agent label may act only as that agent; the
# reserved operator/system labels need a session bound to them.
. (Join-Path $PSScriptRoot 'AgentBridgeSessionIdentity.ps1')
Assert-AgentBridgeSessionIdentity -RequestedAgent $Agent

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

function ConvertTo-SafeName {
    param([string] $Name)
    return (($Name -replace '[^A-Za-z0-9._-]', '_').Trim('_'))
}

function Normalize-Scope {
    param([string] $Scope)
    return (($Scope -replace '\\','/').Trim('/')).ToLowerInvariant()
}

function Test-ScopeOverlap {
    param([string[]] $A, [string[]] $B)
    foreach ($a0 in @($A)) {
        $a = Normalize-Scope $a0
        if (-not $a) { continue }
        foreach ($b0 in @($B)) {
            $b = Normalize-Scope $b0
            if (-not $b) { continue }
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

if ($Mode -eq 'write' -and @($WriteScope).Count -eq 0) {
    throw 'write claims require at least one -WriteScope path'
}

$safeTask = ConvertTo-SafeName $TaskId
if (-not $safeTask) { throw 'TaskId does not produce a safe claim filename' }
$ownerIdentity = Get-BridgeOwnerIdentity

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

# S2 (Lead 2026-09-30): the listing, the conflict checks and the create or
# refresh run inside the v2 queue's runtime-root mutex, taken before the
# claim lock as in the Python queue, so a v2 transaction or claims snapshot
# never lists claims in the middle of this change. The sweep above takes and
# releases it on its own, so it is never held twice. The claim event is
# written after the release.
$rootMutex = Enter-BridgeQueueRootMutex -Root $bridgeRoot
$rootWorkDone = $false
try {
$activeClaims = @(Get-ChildItem -Path $claimsDir -Filter '*.json' -File -ErrorAction SilentlyContinue)
$resources = @(Resolve-BridgeResourceScopes -Scopes $WriteScope -Worktree (Get-Location).Path -BridgeRoot $bridgeRoot)
$existingClaimPath = ''
foreach ($file in $activeClaims) {
    try {
        $existing = Get-Content -Raw -Path $file.FullName -Encoding UTF8 | ConvertFrom-Json
    } catch {
        Stop-BridgeClaim -Message "unreadable active claim blocks acquisition: $($file.FullName)" -Code 3
    }
    if ([string]$existing.task_id -eq $TaskId) {
        if (-not $Force) {
            Stop-BridgeClaim -Message ("task already claimed by {0}: {1}" -f $existing.agent, $file.FullName) -Code 2
        }
        # B7: the agent label is not authority, and there is no takeover of
        # a live owner - not by the same label, not by operator or system.
        # -Force only lets the OWNING session refresh its own claim (or an
        # identity-less caller refresh its own owner_identity=none claim).
        # A claim held by another session frees only through its owner's
        # release or the stale sweep.
        if ([string]$existing.agent -cne $Agent) {
            Stop-BridgeClaim -Message ("cannot force-update claim owned by {0}: {1}" -f $existing.agent, $file.FullName) -Code 3
        }
        if ([string]$existing.task_id -cne $TaskId -or
            -not ((Test-BridgeClaimOwner -Claim $existing -Identity $ownerIdentity) -or
                  (Test-BridgeIdentitylessClaimPair -Claim $existing -Identity $ownerIdentity))) {
            Stop-BridgeClaim -Message ("cannot force-update a claim held by another session of {0}: {1}" -f $existing.agent, $file.FullName) -Code 3
        }
        $existingClaimPath = $file.FullName
        continue
    }
    if ($Mode -eq 'write' -and [string]$existing.mode -eq 'write') {
        $existingCwd = if ($existing.PSObject.Properties['cwd']) { [string]$existing.cwd } else { '' }
        # RS7-M1 (RCO1): an unresolvable stored scope still fails closed, but names the claim that blocks, not the caller.
        try {
            $existingResources = @(Resolve-BridgeResourceScopes -Scopes @($existing.write_scope) -Worktree $existingCwd -BridgeRoot $bridgeRoot)
        } catch {
            $reason = [string]$_.Exception.Message
            Stop-BridgeClaim -Message ("an active claim has an unresolvable write scope; overlap unknown (claim {0} by {1}, cwd {2}: {3})" -f $existing.task_id, $existing.agent, $existingCwd, $reason.Substring(0, [Math]::Min(200, $reason.Length))) -Code 3
        }
        if (Test-BridgeResourceOverlap -A $resources -B $existingResources) {
            Stop-BridgeClaim -Message ("write-scope conflict with active claim {0} by {1}: {2}" -f $existing.task_id, $existing.agent, ((@($existing.write_scope)) -join ', ')) -Code 3
        }
    }
}

# Tools BFE7F4A1 (PS-PENDING-WAL-ADMISSION): a v2 transaction that stopped between its WAL record and its claim write
# is applied later by recovery without re-checking this claim (B-F2), so a WRITE claim compares the claim each
# unfinished record would write, as the Python queue does, under the root mutex held above. Bounded and fail-closed:
# a linked, oversized, undecodable or unknown-state record makes overlap unknown; finished records are skipped. This
# writer does not re-verify record digests or root binding, so it may refuse or admit such a record differently from
# Python (Grok 21006e13 #9); recovery never applies a record Python calls corrupt, so it cannot create the overlap.
if ($Mode -eq 'write') {
    $walDir = Join-Path $bridgeRoot 'work_queue/v2/wal'
    $walFiles = @()
    if (Test-Path -LiteralPath $walDir) {
        if (-not (Test-Path -LiteralPath $walDir -PathType Container) -or
            ([IO.File]::GetAttributes($walDir) -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            Stop-BridgeClaim -Message "the v2 WAL directory is linked or not a directory; overlap unknown: $walDir" -Code 3
        }
        $walFiles = @(Get-ChildItem -LiteralPath $walDir -Filter '*.json' -File -Force -ErrorAction Stop)
    }
    if ($walFiles.Count -gt 1024) {
        Stop-BridgeClaim -Message ("{0} unfinished transaction records exceed the 1024 bound; overlap unknown" -f $walFiles.Count) -Code 3
    }
    foreach ($walFile in $walFiles) {
        $txn = $null
        try {
            if (($walFile.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $walFile.Length -gt 262144) { throw 'linked or oversized' }
            $txn = [IO.File]::ReadAllText($walFile.FullName, [Text.UTF8Encoding]::new($false, $true)) | ConvertFrom-Json -ErrorAction Stop
        } catch { $txn = $null }
        $state = if ($null -ne $txn -and $txn.PSObject.Properties['state']) { [string]$txn.state } else { '' }
        if ($null -eq $txn -or $state -cnotin @('prepared', 'applied', 'outboxed', 'aborted', 'diverged') -or
            -not $txn.PSObject.Properties['after']) {
            Stop-BridgeClaim -Message ("an unfinished transaction record is unreadable; overlap unknown: {0}" -f $walFile.Name) -Code 3
        }
        if ($state -cin @('outboxed', 'aborted', 'diverged')) { continue }
        $planned = $txn.after
        if ($null -eq $planned) { continue }   # a release or an archive writes no claim
        if ($planned.GetType().FullName -cne 'System.Management.Automation.PSCustomObject') {
            Stop-BridgeClaim -Message ("an unfinished transaction record is unreadable; overlap unknown: {0}" -f $walFile.Name) -Code 3
        }
        # 'write' compares case-insensitively, as the active-claim check above does (Grok 21006e13 #1: a 'Write' plan was skipped)
        if ([string]$planned.task_id -ceq $TaskId -or [string]$planned.mode -ne 'write') { continue }
        $plannedCwd = if ($planned.PSObject.Properties['cwd']) { [string]$planned.cwd } else { '' }
        try {
            $plannedResources = @(Resolve-BridgeResourceScopes -Scopes @($planned.write_scope) -Worktree $plannedCwd -BridgeRoot $bridgeRoot)
        } catch {
            $reason = [string]$_.Exception.Message
            Stop-BridgeClaim -Message ("an unfinished claim has an unresolvable write scope; overlap unknown (claim {0} by {1}, cwd {2}: {3})" -f $planned.task_id, $planned.agent, $plannedCwd, $reason.Substring(0, [Math]::Min(200, $reason.Length))) -Code 3
        }
        if (Test-BridgeResourceOverlap -A $resources -B $plannedResources) {
            Stop-BridgeClaim -Message ("write-scope conflict with an unfinished claim of {0}" -f $planned.task_id) -Code 3
        }
    }
}

# A refresh rewrites exactly the file that holds this task's claim; a new
# claim goes to a name no other task's claim occupies. The sanitized name
# is lossy, so it is never assumed to belong to this task.
$claimPath = if ($existingClaimPath) {
    $existingClaimPath
} else {
    New-BridgeClaimPath -ClaimsDir $claimsDir -TaskId $TaskId
}
if (-not $claimPath) { throw 'TaskId does not produce a safe claim filename' }

if (-not $RunId) {
    $RunId = if ($env:AGENT_BRIDGE_RUN_ID) { [string]$env:AGENT_BRIDGE_RUN_ID } else { '' }
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
if ($Role -and $Role -notmatch '^[a-z][a-z0-9_-]{1,32}$') {
    throw "role must match ^[a-z][a-z0-9_-]{1,32}$"
}
if ($AgentUuid -and $AgentUuid -notmatch '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$') {
    throw "agent_uuid must be a UUID"
}
foreach ($capability in @($Capabilities)) {
    if ($capability -notmatch '^[a-z][a-z0-9_.:-]{1,64}$') {
        throw "capability must match ^[a-z][a-z0-9_.:-]{1,64}$"
    }
}
if ($LeaseSeconds -le 0 -and $env:AGENT_BRIDGE_STALE_LEASE_SECONDS) {
    $parsedLease = 0
    if ([int]::TryParse([string]$env:AGENT_BRIDGE_STALE_LEASE_SECONDS, [ref]$parsedLease) -and $parsedLease -gt 0) {
        $LeaseSeconds = $parsedLease
    }
}
$readOnlyLeaseCapSeconds = 120
if ($LeaseSeconds -le 0) {
    # B7: split the default by mode. Read-only claims are cheap to
    # re-acquire and must free fast; write claims are the ones a long
    # legitimate turn used to lose mid-work. The longer write default is
    # only safe because the sweeper now also requires the owning session
    # to have stopped beating, and the keepalive no longer depends on
    # bridge event traffic succeeding.
    $LeaseSeconds = if ($Mode -eq 'read-only') { $readOnlyLeaseCapSeconds } else { 1800 }
}
if ($Mode -eq 'read-only' -and $LeaseSeconds -gt $readOnlyLeaseCapSeconds) {
    # Hard cap: neither AGENT_BRIDGE_STALE_LEASE_SECONDS nor an explicit
    # -LeaseSeconds may lengthen a read-only lease. A read-only claim
    # that outlives its reader blocks write work for no benefit.
    $LeaseSeconds = $readOnlyLeaseCapSeconds
}

$nowUtc = (Get-Date).ToUniversalTime().ToString('o')
$leaseExpiresUtc = ([DateTime]::Parse($nowUtc).ToUniversalTime()).AddSeconds($LeaseSeconds).ToString('o')
$claim = [ordered]@{
    claimed_at_utc      = $nowUtc
    # R15: stale-claim-lease. last_heartbeat_utc is bumped by
    # Send-Liveness.ps1 on heartbeat/liveness-active events for
    # this agent; Invoke-StaleClaimSweep.ps1 archives claims whose
    # heartbeat is older than AGENT_BRIDGE_STALE_LEASE_SECONDS
    # (default 300s). On creation it equals claimed_at_utc so a
    # claim that's never heart-beated still has a finite lease.
    last_heartbeat_utc  = $nowUtc
    agent               = $Agent
    task_id             = $TaskId
    summary             = $Summary
    mode                = $Mode
    write_scope         = @($WriteScope)
    resources           = @($resources)
    run_id              = $RunId
    lease_seconds       = $LeaseSeconds
    claim_lease_expires_utc = $leaseExpiresUtc
    pid                 = $PID
    cwd                 = (Get-Location).Path
    git_branch          = Get-CurrentGitBranch
}
# B7: bind the claim to this session's identity. owner_token_sha256 is
# the SHA-256 of a per-session secret that never touches disk, so only
# the owning process can extend this lease; pid and agent name are
# recorded for humans and are deliberately not authority. A session with
# no identity still gets a claim - it simply cannot be kept alive by a
# heartbeat, and ages out normally.
if ($null -ne $ownerIdentity) {
    $claim['owner_session_id'] = [string]$ownerIdentity.owner_session_id
    $claim['owner_token_sha256'] = [string]$ownerIdentity.owner_token_sha256
    # When the process owner context (AgentBridgeSessionIdentity.ps1)
    # describes this same owner, record its pid and process start too, so
    # Test-AgentBridgeClaimOwner and Test-BridgeClaimOwner agree on the
    # claim. Informational only: authority stays session plus token hash.
    try {
        $ownerContext = Get-AgentBridgeClaimOwnerContext
        if ([string]$ownerContext.session_id -ceq [string]$ownerIdentity.owner_session_id -and
            [string]$ownerContext.token_sha256 -ceq [string]$ownerIdentity.owner_token_sha256) {
            $claim['owner_pid'] = [int]$ownerContext.owner_pid
            $claim['owner_process_start_utc'] = [string]$ownerContext.owner_process_start_utc
        }
    } catch { }
} else {
    # Marks a B7-era claim made without an identity, so Release can tell
    # it apart from a pre-B7 claim and let an identity-less caller release
    # it by agent label, as before B7.
    $claim['owner_identity'] = 'none'
}
if ($Role) { $claim['role'] = $Role }
if ($AgentUuid) { $claim['agent_uuid'] = $AgentUuid }
if (@($Capabilities).Count -gt 0) { $claim['capabilities'] = @($Capabilities) }
$json = ($claim | ConvertTo-Json -Depth 8)

$encoding = New-Object System.Text.UTF8Encoding($false)
if (-not $existingClaimPath) {
    # A new claim is only ever created. Losing a race to another creator
    # is a refusal, never an overwrite of whatever now holds the name.
    try {
        $fs = New-Object System.IO.FileStream($claimPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::Read)
        try {
            $bytes = $encoding.GetBytes($json)
            $fs.Write($bytes, 0, $bytes.Length)
        } finally {
            $fs.Dispose()
        }
    } catch {
        Stop-BridgeClaim -Message ("could not create claim, likely already exists: {0}" -f $claimPath) -Code 2
    }
} else {
    # A refresh by the owning session is a compare-and-swap under the same
    # per-claim lock the keepalive, release and sweep hold: re-read the
    # file under the lock and replace it only if it still holds this task,
    # this agent and this owner. A claim archived meanwhile is never
    # recreated (Replace requires the destination to exist).
    $refreshLock = Enter-BridgeClaimLock -ClaimPath $claimPath
    if ($null -eq $refreshLock) {
        Stop-BridgeClaim -Message ("could not lock claim for refresh: {0}" -f $claimPath) -Code 4
    }
    try {
        $current = $null
        try {
            $current = Get-Content -Raw -LiteralPath $claimPath -Encoding UTF8 |
                ConvertFrom-Json -ErrorAction Stop
        } catch { $current = $null }
        if ($null -eq $current -or
            -not $current.PSObject.Properties['task_id'] -or
            [string]$current.task_id -cne $TaskId -or
            -not $current.PSObject.Properties['agent'] -or
            [string]$current.agent -cne $Agent -or
            -not ((Test-BridgeClaimOwner -Claim $current -Identity $ownerIdentity) -or
                  (Test-BridgeIdentitylessClaimPair -Claim $current -Identity $ownerIdentity))) {
            Stop-BridgeClaim -Message ("claim changed before refresh: {0}" -f $claimPath) -Code 3
        }
        # B-F3 (RCO1 2026-09-30; Fable review 99897de5): this writer has no dispatch_key and rebuilds
        # the claim, so a refresh here would erase the key a v2 claim stores as immutable dispatch
        # evidence, and a later claim with the same key would pass the v2 duplicate check. As in the
        # v2 queue, a refresh that cannot present the stored key is refused; the claim is untouched.
        if ($current.PSObject.Properties['dispatch_key']) {
            Stop-BridgeClaim -Message ("refusing to refresh a keyed claim without its dispatch_key: {0}" -f $claimPath) -Code 3
        }
        # Internal review fix R7 (2026-05-09): write to a temp sibling and
        # Replace() so readers always see the old or the new claim, never a
        # torn write.
        $tmpClaim = "$claimPath.tmp.$PID.$([guid]::NewGuid().ToString('N'))"
        $backupClaim = "$claimPath.bak.$PID.$([guid]::NewGuid().ToString('N'))"
        [System.IO.File]::WriteAllText($tmpClaim, $json, $encoding)
        try {
            [System.IO.File]::Replace($tmpClaim, $claimPath, $backupClaim)
        } catch [System.IO.FileNotFoundException] {
            Stop-BridgeClaim -Message ("claim disappeared before refresh: {0}" -f $claimPath) -Code 2
        } finally {
            try { Remove-Item -LiteralPath $tmpClaim -Force -ErrorAction SilentlyContinue } catch {}
            try { Remove-Item -LiteralPath $backupClaim -Force -ErrorAction SilentlyContinue } catch {}
        }
    } finally {
        Exit-BridgeClaimLock -Lock $refreshLock
    }
}
$rootWorkDone = $true
} finally {
    Exit-BridgeQueueRootMutex -Mutex $rootMutex -Completed:$rootWorkDone
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
