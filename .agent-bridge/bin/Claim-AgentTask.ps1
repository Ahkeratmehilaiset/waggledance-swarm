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
        if (-not $scope) {
            throw 'write_scope entries must be non-empty paths'
        }
        if ($scope -and $scope -cnotmatch '^[\x20-\x7E]*\z') {
            throw 'write_scope paths must contain printable ASCII characters only'
        }
        $overlapScope = ($scope -replace '\\','/')
        if ($overlapScope.StartsWith('/')) {
            throw 'write_scope paths must be repository-relative'
        }
        if ($overlapScope.Contains(':')) {
            throw "write_scope paths must not contain ':'"
        }
        $scopeSegments = @(
            $overlapScope.Split(
                [char[]]@('/'),
                [System.StringSplitOptions]::None
            )
        )
        $invalidScopeSegments = @(
            $scopeSegments |
                Where-Object {
                    $_ -ceq '' -or $_ -ceq '.' -or $_ -ceq '..'
                }
        )
        if ($overlapScope -and $invalidScopeSegments.Count -gt 0) {
            throw (
                "write_scope paths must not contain empty, '.' or '..' " +
                "segments"
            )
        }
        $aliasedScopeSegments = @(
            $scopeSegments |
                Where-Object {
                    $_.EndsWith('.') -or $_.EndsWith(' ')
                }
        )
        if ($aliasedScopeSegments.Count -gt 0) {
            throw (
                "write_scope path segments must not end in '.' or space"
            )
        }
        if (
            -not $seenWriteScope.Add($scope)
        ) {
            continue
        }
        [void]$normalizedWriteScope.Add($scope)
    }
}
$WriteScope = @($normalizedWriteScope)
$sessionId = [string]$env:AGENT_BRIDGE_SESSION_ID
if ($RunId -and $RunId -notmatch '^[A-Za-z0-9._:-]{1,128}\z') {
    throw "run_id must match ^[A-Za-z0-9._:-]{1,128}$"
}
if ($sessionId -and $sessionId -notmatch '^[A-Za-z0-9._:-]{1,128}\z') {
    throw "session_id must match ^[A-Za-z0-9._:-]{1,128}$"
}
if ($Role -and $Role -cnotmatch '^[a-z][a-z0-9_-]{1,32}\z') {
    throw "role must match ^[a-z][a-z0-9_-]{1,32}$"
}
if ($AgentUuid -and $AgentUuid -notmatch '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\z') {
    throw "agent_uuid must be a UUID"
}
if ($AgentUuid) {
    $AgentUuid = $AgentUuid.ToLowerInvariant()
}
foreach ($capability in @($Capabilities)) {
    if ($capability -cnotmatch '^[a-z][a-z0-9_.:-]{1,64}\z') {
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
$bridgeRoot = Resolve-AgentBridgeRoot `
    -DefaultRoot (Split-Path -Parent $PSScriptRoot)

Ensure-AgentBridgePlainDirectory `
    -LiteralPath $bridgeRoot `
    -Context 'bridge root'
$workQueueDir = Join-Path $bridgeRoot 'work_queue'
Ensure-AgentBridgePlainDirectory `
    -LiteralPath $workQueueDir `
    -Context 'work queue directory'
$claimsDir = Join-Path $workQueueDir 'claims'
Ensure-AgentBridgePlainDirectory `
    -LiteralPath $claimsDir `
    -Context 'active claims directory'

function Normalize-Scope {
    param([string] $Scope)
    if ($Scope -cnotmatch '^[\x20-\x7E]*\z') {
        throw 'write_scope paths must contain printable ASCII characters only'
    }
    if ([string]::IsNullOrWhiteSpace($Scope)) {
        throw 'write_scope entries must be non-empty paths'
    }
    $normalized = ($Scope -replace '\\','/').ToLowerInvariant()
    if ($normalized.StartsWith('/')) {
        throw 'write_scope paths must be repository-relative'
    }
    if ($normalized.Contains(':')) {
        throw "write_scope paths must not contain ':'"
    }
    $scopeSegments = @(
        $normalized.Split(
            [char[]]@('/'),
            [System.StringSplitOptions]::None
        )
    )
    $invalidSegments = @(
        $scopeSegments |
            Where-Object {
                $_ -ceq '' -or $_ -ceq '.' -or $_ -ceq '..'
            }
    )
    if ($normalized -and $invalidSegments.Count -gt 0) {
        throw (
            "write_scope paths must not contain empty, '.' or '..' segments"
        )
    }
    $aliasedSegments = @(
        $scopeSegments |
            Where-Object {
                $_.EndsWith('.') -or $_.EndsWith(' ')
            }
    )
    if ($aliasedSegments.Count -gt 0) {
        throw "write_scope path segments must not end in '.' or space"
    }
    return $normalized
}

function Expand-ScopeList {
    param([object[]] $Scope)

    foreach ($scopeValue in @($Scope)) {
        if ($scopeValue -isnot [string]) {
            throw 'write_scope entries must be strings'
        }
        foreach ($scopePart in ([string]$scopeValue -split ',')) {
            $normalized = Normalize-Scope $scopePart.Trim()
            $normalized
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

function Get-StrictActiveClaimSnapshot {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $ClaimsDirectory
    )

    try {
        # Do not use -File here. A directory, broken link, or other non-file
        # entry ending in .json must block acquisition instead of disappearing
        # from the active-claim view.
        $claimEntries = @(
            Get-ChildItem -LiteralPath $ClaimsDirectory -Filter '*.json' `
                -Force -ErrorAction Stop |
                Sort-Object FullName
        )
    } catch {
        throw (
            "cannot enumerate active claim records under {0}: {1}" -f
            $ClaimsDirectory,
            $_.Exception.Message
        )
    }

    $snapshot = @()
    $taskPaths = [System.Collections.Generic.Dictionary[string,string]]::new(
        [System.StringComparer]::Ordinal
    )
    foreach ($entry in $claimEntries) {
        $claimPath = [string]$entry.FullName
        if (-not (Test-Path -LiteralPath $claimPath -PathType Leaf)) {
            throw "active claim record must be a file: $claimPath"
        }

        try {
            $claimSnapshot = Read-AgentBridgeStrictUtf8JsonSnapshot `
                -LiteralPath $claimPath
            $claimJson = [string]$claimSnapshot.text
        } catch {
            throw (
                "unreadable active claim record {0}: {1}" -f
                $claimPath,
                $_.Exception.Message
            )
        }

        try {
            $claimRecord = ConvertFrom-AgentBridgeJson -Json $claimJson
        } catch {
            throw (
                "malformed active claim JSON {0}: {1}" -f
                $claimPath,
                $_.Exception.Message
            )
        }
        if (
            $null -eq $claimRecord -or
            $claimRecord -isnot
                [System.Management.Automation.PSCustomObject]
        ) {
            throw "active claim record must be a JSON object: $claimPath"
        }
        Assert-AgentBridgeActiveClaimRawAuthorityFields `
            -Record $claimRecord `
            -ClaimPath $claimPath
        $taskIdProperty = Get-AgentBridgeExactProperty `
            -InputObject $claimRecord `
            -Name 'task_id'
        if (
            $null -eq $taskIdProperty -or
            $taskIdProperty.Value -isnot [string] -or
            [string]::IsNullOrEmpty([string]$taskIdProperty.Value)
        ) {
            throw (
                "active claim task_id must be a non-empty string: {0}" -f
                $claimPath
            )
        }

        $storedTaskId = [string]$taskIdProperty.Value
        if ($taskPaths.ContainsKey($storedTaskId)) {
            $storedTaskIdDisplay = Format-AgentBridgeIdentityDisplay `
                -Value $storedTaskId
            throw (
                "duplicate active claim records for exact task_id " +
                "'$storedTaskIdDisplay': " +
                "$($taskPaths[$storedTaskId]), $claimPath"
            )
        }
        $taskPaths.Add($storedTaskId, $claimPath)

        # Do not validate/sanitize the stored task_id against the public input
        # grammar here. Invalid-but-readable identities remain exact strings
        # and stay in place for explicit operator recovery.
        $snapshot += [pscustomobject]@{
            file = $entry
            claim = $claimRecord
            task_id = $storedTaskId
            snapshot_bytes = [byte[]]$claimSnapshot.bytes
            snapshot_sha256 = [string]$claimSnapshot.sha256
            snapshot_length = [long]$claimSnapshot.length
        }
    }
    return $snapshot
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
#
# Validate before the opportunistic sweep so an unreadable active record
# cannot permit archive/event mutations before acquisition ultimately fails.
try {
    [void]@(Get-StrictActiveClaimSnapshot -ClaimsDirectory $claimsDir)
} catch {
    Stop-BridgeClaim -Message ([string]$_.Exception.Message) -Code 3
}

$sweepScript = Join-Path $PSScriptRoot 'Invoke-StaleClaimSweep.ps1'
if (Test-Path -LiteralPath $sweepScript -PathType Leaf) {
    try {
        & $sweepScript -Quiet | Out-Null
    } catch {
        Stop-BridgeClaim `
            -Message (
                "stale-claim sweep before claim acquisition failed: {0}" -f
                $_.Exception.Message
            ) `
            -Code 3
    }
}

$mutationLock = Enter-AgentBridgeMutationLock -BridgeRoot $bridgeRoot
try {
    try {
        # The pre-sweep snapshot protects zero-write failure semantics. Repeat
        # under the shared mutation lock and use only this locked snapshot for
        # exact-task and write-scope decisions.
        $parsedClaims = @(
            Get-StrictActiveClaimSnapshot -ClaimsDirectory $claimsDir
        )
    } catch {
        Stop-BridgeClaim -Message ([string]$_.Exception.Message) -Code 3
    }

    try {
        $preferredClaimBaseName = Get-AgentBridgeClaimBaseName -TaskId $TaskId
        $preferredClaimPath = [System.IO.Path]::GetFullPath(
            (Join-Path $claimsDir ($preferredClaimBaseName + '.json'))
        )
    } catch {
        Stop-BridgeClaim -Message ([string]$_.Exception.Message) -Code 3
    }
    $preferredEntries = @(
        $parsedClaims |
            Where-Object {
                [string]$_.file.FullName -ieq $preferredClaimPath
            }
    )
    if (
        $preferredEntries.Count -eq 1 -and
        [string]$preferredEntries[0].task_id -cne $TaskId
    ) {
        Stop-BridgeClaim `
            -Message ((
                "claim filename collision at preferred path for task_id " +
                "'{0}': stored task_id '{1}' in {2}"
            ) -f
                $TaskId,
                [string]$preferredEntries[0].task_id,
                $preferredClaimPath
            ) `
            -Code 3
    }

$taskMatches = @(
    $parsedClaims |
        Where-Object {
            [string]$_.task_id -ceq $TaskId
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
    $existingAgentProperty = Get-AgentBridgeExactProperty `
        -InputObject $existing `
        -Name 'agent'
    if (
        $null -eq $existingAgentProperty -or
        $existingAgentProperty.Value -isnot [string]
    ) {
        Stop-BridgeClaim `
            -Message ("claim has missing or non-string agent: {0}" -f $claimPath) `
            -Code 3
    }
    $existingAgent = [string]$existingAgentProperty.Value
    if (-not $Force) {
        Stop-BridgeClaim -Message ("task already claimed by {0}: {1}" -f $existingAgent, $claimPath) -Code 2
    }
    if ($existingAgent -cne $Agent) {
        Stop-BridgeClaim -Message ("cannot force-update claim owned by {0}: {1}" -f $existingAgent, $claimPath) -Code 3
    }
    try {
        Assert-AgentBridgeClaimOwner `
            -Claim $existing `
            -OwnerContext $ownerContext `
            -Operation 'force-update'
    } catch {
        Stop-BridgeClaim -Message $_.Exception.Message -Code 3
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
        $existingModeProperty = Get-AgentBridgeExactProperty `
            -InputObject $existing `
            -Name 'mode'
        $existingMode = if (
            $null -ne $existingModeProperty -and
            $existingModeProperty.Value -is [string]
        ) {
            [string]$existingModeProperty.Value
        } else {
            ''
        }
        if ($existingMode -ceq 'read-only') {
            continue
        }
        if ($existingMode -cne 'write') {
            $existingAgentProperty = Get-AgentBridgeExactProperty `
                -InputObject $existing `
                -Name 'agent'
            $existingAgentDisplay = if (
                $null -ne $existingAgentProperty -and
                $existingAgentProperty.Value -is [string]
            ) {
                [string]$existingAgentProperty.Value
            } else {
                '<missing-or-nonstring>'
            }
            Stop-BridgeClaim `
                -Message ((
                    "write-scope conflict with active claim {0} by {1}: " +
                    "stored mode is missing, non-string, or invalid"
                ) -f
                    $entry.task_id,
                    $existingAgentDisplay
                ) `
                -Code 3
        }
        $existingScopeProperty = Get-AgentBridgeExactProperty `
            -InputObject $existing `
            -Name 'write_scope'
        $existingScope = if ($null -ne $existingScopeProperty) {
            @($existingScopeProperty.Value)
        } else {
            @()
        }
        if (Test-ScopeOverlap -A $WriteScope -B $existingScope) {
            $existingAgentProperty = Get-AgentBridgeExactProperty `
                -InputObject $existing `
                -Name 'agent'
            $existingAgentDisplay = if (
                $null -ne $existingAgentProperty -and
                $existingAgentProperty.Value -is [string]
            ) {
                [string]$existingAgentProperty.Value
            } else {
                '<missing-or-nonstring>'
            }
            Stop-BridgeClaim -Message ("write-scope conflict with active claim {0} by {1}: {2}" -f $entry.task_id, $existingAgentDisplay, (($existingScope) -join ', ')) -Code 3
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
    session_id          = if ($sessionId) {
        $sessionId
    } else {
        [string]$ownerContext.session_id
    }
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
$jsonBytes = $encoding.GetBytes($json)
$expectedPublishSha256 = Get-AgentBridgeSha256Hex -Bytes $jsonBytes
$expectedPublishLength = [long]$jsonBytes.Length
if ($forceUpdateExisting) {
    # Update the exact claim record discovered by task_id. In particular, this
    # preserves Python's collision-resistant filename for task IDs containing
    # slashes instead of creating a second sanitized-only claim.
    Update-AgentBridgeFileFromBytes `
        -PublishBytes $jsonBytes `
        -DestinationPath $claimPath `
        -ExpectedSourceBytes ([byte[]]$matchedEntry.snapshot_bytes) `
        -ExpectedSourceSha256 ([string]$matchedEntry.snapshot_sha256) `
        -ExpectedSourceLength ([long]$matchedEntry.snapshot_length) `
        -ExpectedPublishSha256 $expectedPublishSha256 `
        -ExpectedPublishLength $expectedPublishLength
} else {
    try {
        Publish-AgentBridgeNewFileFromBytes `
            -PublishBytes $jsonBytes `
            -DestinationPath $claimPath `
            -ExpectedSha256 $expectedPublishSha256 `
            -ExpectedLength $expectedPublishLength
    } catch {
        $createError = [string]$_.Exception.Message
        if ($createError -clike 'claim_destination_collision:*') {
            Stop-BridgeClaim `
                -Message ("could not create claim, already exists: {0}" -f $claimPath) `
                -Code 2
        }
        Stop-BridgeClaim `
            -Message ("claim integrity publication failed: {0}" -f $createError) `
            -Code 3
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
