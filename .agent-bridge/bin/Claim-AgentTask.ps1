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
    $safe = (($Name -replace '[^A-Za-z0-9._-]', '_').Trim('_'))
    if (-not $safe) { $safe = 'claim' }
    if ($safe -ceq $Name) { return $safe }

    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Name)
        $hash = $sha.ComputeHash($bytes)
    } finally {
        $sha.Dispose()
    }
    $digest = -join @($hash | ForEach-Object { $_.ToString('x2') })
    return "{0}-{1}" -f $safe, $digest.Substring(0, 12)
}

function Get-ExactTaskClaimFiles {
    param(
        [Parameter(Mandatory)] [string] $Directory,
        [Parameter(Mandatory)] [string] $ExactTaskId
    )

    $matches = @()
    foreach ($file in @(
        Get-ChildItem -LiteralPath $Directory -Filter '*.json' -File `
            -ErrorAction SilentlyContinue |
            Sort-Object FullName
    )) {
        try {
            $existing = Get-Content -Raw -LiteralPath $file.FullName `
                -Encoding UTF8 | ConvertFrom-Json
            $taskProperty = $existing.PSObject.Properties['task_id']
            if ($null -eq $taskProperty) { continue }
            $existingTaskId = [string]$taskProperty.Value
        } catch {
            continue
        }
        if (
            [string]::Equals(
                $existingTaskId,
                $ExactTaskId,
                [System.StringComparison]::Ordinal
            )
        ) {
            $matches += $file
        }
    }
    return $matches
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

$legacySafeTask = (($TaskId -replace '[^A-Za-z0-9._-]', '_').Trim('_'))
if (-not $legacySafeTask) {
    throw 'TaskId does not produce a safe claim filename'
}
$safeTask = ConvertTo-SafeName $TaskId
$preferredClaimPath = Join-Path $claimsDir ($safeTask + '.json')
$claimPath = $preferredClaimPath
$forceReplaceAllowed = $false

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

$activeClaims = @(
    Get-ChildItem -LiteralPath $claimsDir -Filter '*.json' -File `
        -ErrorAction SilentlyContinue
)
$matchingClaimFiles = @(
    Get-ExactTaskClaimFiles -Directory $claimsDir -ExactTaskId $TaskId
)
if ($matchingClaimFiles.Count -gt 1) {
    Stop-BridgeClaim -Message (
        "multiple active claims found for exact task {0}: {1}" -f
        $TaskId,
        ((@($matchingClaimFiles.FullName) | Sort-Object) -join ', ')
    ) -Code 3
}
if ($matchingClaimFiles.Count -eq 1) {
    $matchingFile = $matchingClaimFiles[0]
    $existing = Get-Content -Raw -LiteralPath $matchingFile.FullName `
        -Encoding UTF8 | ConvertFrom-Json
    if (-not $Force) {
        Stop-BridgeClaim -Message (
            "task already claimed by {0}: {1}" -f
            $existing.agent,
            $matchingFile.FullName
        ) -Code 2
    }
    if (
        [string]$existing.agent -ne $Agent -and
        $Agent -notin @('operator','system')
    ) {
        Stop-BridgeClaim -Message (
            "cannot force-update claim owned by {0}: {1}" -f
            $existing.agent,
            $matchingFile.FullName
        ) -Code 3
    }
    $claimPath = $matchingFile.FullName
    $forceReplaceAllowed = $true
} elseif (Test-Path -LiteralPath $preferredClaimPath -PathType Leaf) {
    Stop-BridgeClaim -Message (
        "claim path collision for task {0}: {1}" -f
        $TaskId,
        $preferredClaimPath
    ) -Code 3
}

foreach ($file in $activeClaims) {
    try {
        $existing = Get-Content -Raw -LiteralPath $file.FullName `
            -Encoding UTF8 | ConvertFrom-Json
        $taskProperty = $existing.PSObject.Properties['task_id']
        if ($null -eq $taskProperty) { continue }
        $existingTaskId = [string]$taskProperty.Value
    } catch {
        continue
    }
    if (
        [string]::Equals(
            $existingTaskId,
            $TaskId,
            [System.StringComparison]::Ordinal
        )
    ) {
        continue
    }
    if ($Mode -eq 'write' -and [string]$existing.mode -eq 'write') {
        if (Test-ScopeOverlap -A $WriteScope -B @($existing.write_scope)) {
            Stop-BridgeClaim -Message ("write-scope conflict with active claim {0} by {1}: {2}" -f $existing.task_id, $existing.agent, ((@($existing.write_scope)) -join ', ')) -Code 3
        }
    }
}

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
if ($LeaseSeconds -le 0) { $LeaseSeconds = 300 }

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
    run_id              = $RunId
    lease_seconds       = $LeaseSeconds
    claim_lease_expires_utc = $leaseExpiresUtc
    pid                 = $PID
    cwd                 = (Get-Location).Path
    git_branch          = Get-CurrentGitBranch
}
if ($Role) { $claim['role'] = $Role }
if ($AgentUuid) { $claim['agent_uuid'] = $AgentUuid }
if (@($Capabilities).Count -gt 0) { $claim['capabilities'] = @($Capabilities) }
$json = ($claim | ConvertTo-Json -Depth 8)

$encoding = New-Object System.Text.UTF8Encoding($false)
try {
    $fs = New-Object System.IO.FileStream($claimPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::Read)
    try {
        $bytes = $encoding.GetBytes($json)
        $fs.Write($bytes, 0, $bytes.Length)
    } finally {
        $fs.Dispose()
    }
} catch {
    if (-not $Force -or -not $forceReplaceAllowed) {
        Stop-BridgeClaim -Message ("could not create claim, likely already exists: {0}" -f $claimPath) -Code 2
    }
    # Internal review fix R7 (2026-05-09): the -Force fallback used
    # Set-Content, which is non-atomic; a concurrent reader could
    # observe a partially-written claim and treat it as malformed.
    # Write to a temp sibling and Replace() so readers always see the
    # old or the new claim, never a torn write.
    $tmpClaim = "$claimPath.tmp.$PID.$([guid]::NewGuid().ToString('N'))"
    [System.IO.File]::WriteAllText($tmpClaim, $json, $encoding)
    $backupClaim = $null
    try {
        if (Test-Path -LiteralPath $claimPath) {
            $backupClaim = "$claimPath.bak.$PID.$([guid]::NewGuid().ToString('N'))"
            [System.IO.File]::Replace($tmpClaim, $claimPath, $backupClaim)
            try { Remove-Item -LiteralPath $backupClaim -Force -ErrorAction SilentlyContinue } catch {}
        } else {
            [System.IO.File]::Move($tmpClaim, $claimPath)
        }
    } catch {
        try {
            if ($backupClaim -and (Test-Path -LiteralPath $backupClaim)) {
                Remove-Item -LiteralPath $backupClaim -Force -ErrorAction SilentlyContinue
            }
        } catch {}
        try { Remove-Item -LiteralPath $tmpClaim -Force -ErrorAction SilentlyContinue } catch {}
        throw
    }
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
