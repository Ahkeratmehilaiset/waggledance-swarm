#requires -Version 5.1
<#
.SYNOPSIS
    Pre-flight check before a branch-changing git operation.

.DESCRIPTION
    Closes BRIDGE_PROTOCOL rule 2 follow-up - finding
    `bridge-branch-switch-during-active-claim-2026-05-09` filed by
    Codex on 2026-05-09T11:30Z.

    Branch movement can invalidate another active write claim only when both
    operations share the same Git worktree. A separately verified worktree is
    independent; missing, non-Git, or filesystem-alias cwd evidence fails
    closed because disjointness cannot be proved.

    Run this guard immediately before any branch-changing git
    operation:

        .\.agent-bridge\bin\Test-BridgeBranchSwitchSafe.ps1 -Agent claude

    Exit codes:
      0 = safe to switch (no blocking write claim in this worktree)
      2 = UNSAFE - a write claim shares this worktree or has an
          unverifiable cwd;
          use a separate worktree, ask the other agent to release,
          or pass -Force to request an override (logs a pre-execution event).

    The check is read-only by default. With -Force it emits a
    pre-execution bridge event recording the override request. The event does
    not claim that a later branch switch was completed.

.PARAMETER Agent
    Which agent is about to switch - used to filter "other-agent"
    claims (own-agent claims do not block; they're your work).

.PARAMETER Force
    Override the safety check and emit a `decision/override_requested` event
    before permission is returned.

.PARAMETER Json
    Emit the active-claim state as JSON instead of human-readable
    text. Useful for chaining into other guards. `-Json -Force` is rejected so
    formatting mode cannot bypass the canonical override audit path.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateScript({ $_ -cmatch '^[a-z][a-z0-9_-]{1,32}$' })]
    [string] $Agent,

    [switch] $Force,
    [switch] $Json
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# R13: honor AGENT_BRIDGE_RUNTIME_ROOT. If env var is SET, USE IT
# (create root if missing, fail loud on malformed path).
$bridgeRoot = if ($env:AGENT_BRIDGE_RUNTIME_ROOT) {
    [string]$env:AGENT_BRIDGE_RUNTIME_ROOT
} else {
    Split-Path -Parent $PSScriptRoot
}
if (-not (Test-Path -LiteralPath $bridgeRoot -PathType Container)) {
    [void](New-Item -ItemType Directory -Path $bridgeRoot -Force -ErrorAction Stop)
}
$claimsDir = Join-Path (Join-Path $bridgeRoot 'work_queue') 'claims'

function Get-ActiveClaims {
    # Emit zero or more claim objects into the pipeline; caller wraps
    # with @(...). The earlier `return ,@()` / `return ,$out` pattern
    # caused PSStrictMode to surface "property 'agent' not found"
    # because the inner array was wrapped as a single object that
    # happened to look like a claim missing its fields.
    # (Codex finding 2026-05-09T12:26Z.)
    if (-not (Test-Path -LiteralPath $claimsDir)) { return }
    foreach ($file in @(Get-ChildItem -Path $claimsDir -Filter '*.json' -File `
                                 -ErrorAction SilentlyContinue)) {
        try {
            Get-Content -Raw -Path $file.FullName -Encoding UTF8 |
                ConvertFrom-Json
        } catch {}
    }
}

function Get-NormalizedLiteralDirectoryPath {
    param([Parameter(Mandatory)] [string] $Path)

    if ([string]::IsNullOrWhiteSpace($Path)) { return $null }
    try {
        $resolved = Resolve-Path -LiteralPath $Path -ErrorAction Stop
        if ([string]$resolved.Provider.Name -cne 'FileSystem') { return $null }
        $full = [System.IO.Path]::GetFullPath([string]$resolved.ProviderPath)
        $root = [System.IO.Path]::GetPathRoot($full)
        if ($full.Length -gt $root.Length) {
            $full = $full.TrimEnd([char[]]@(
                [System.IO.Path]::DirectorySeparatorChar,
                [System.IO.Path]::AltDirectorySeparatorChar
            ))
        }
        return $full
    } catch {
        return $null
    }
}

function Test-PathContainsReparsePoint {
    param([Parameter(Mandatory)] [string] $Path)

    try {
        $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
        while ($null -ne $item) {
            if (
                ($item.Attributes -band
                    [System.IO.FileAttributes]::ReparsePoint) -ne 0
            ) {
                return $true
            }
            $item = $item.Parent
        }
        return $false
    } catch {
        return $true
    }
}

function Get-VerifiedGitWorktreeContext {
    param([Parameter(Mandatory)] [string] $Cwd)

    $normalizedCwd = Get-NormalizedLiteralDirectoryPath -Path $Cwd
    if (
        -not $normalizedCwd -or
        (Test-PathContainsReparsePoint -Path $normalizedCwd)
    ) {
        return $null
    }
    try {
        $previousEAP = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            $gitOutput = @(
                & git -C $normalizedCwd rev-parse --show-toplevel 2>$null
            )
            $gitExit = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previousEAP
        }
    } catch {
        return $null
    }
    if ($gitExit -ne 0 -or $gitOutput.Count -ne 1) { return $null }

    $worktreeRoot = Get-NormalizedLiteralDirectoryPath `
        -Path ([string]$gitOutput[0])
    if (
        -not $worktreeRoot -or
        (Test-PathContainsReparsePoint -Path $worktreeRoot)
    ) {
        return $null
    }
    $worktreePrefix = $worktreeRoot
    if (
        -not $worktreePrefix.EndsWith(
            [string][System.IO.Path]::DirectorySeparatorChar
        ) -and
        -not $worktreePrefix.EndsWith(
            [string][System.IO.Path]::AltDirectorySeparatorChar
        )
    ) {
        $worktreePrefix += [System.IO.Path]::DirectorySeparatorChar
    }
    if (
        -not [string]::Equals(
            $normalizedCwd,
            $worktreeRoot,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -and
        -not $normalizedCwd.StartsWith(
            $worktreePrefix,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        return $null
    }
    return [pscustomobject]@{
        cwd           = $normalizedCwd
        worktree_root = $worktreeRoot
    }
}

function Test-BridgePathEqual {
    param(
        [Parameter(Mandatory)] [string] $Left,
        [Parameter(Mandatory)] [string] $Right
    )
    return [string]::Equals(
        $Left,
        $Right,
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

$allClaims = @(Get-ActiveClaims)
$currentContext = Get-VerifiedGitWorktreeContext -Cwd (Get-Location).Path
if ($null -eq $currentContext) {
    if ($Json) {
        [pscustomobject]@{
            agent           = $Agent
            active_claims   = $allClaims.Count
            blocking_claims = 0
            safe            = $false
            context_error   = 'current_cwd_not_verified_git_worktree'
            blocking_detail = @()
        } | ConvertTo-Json -Depth 6
    } else {
        Write-Host (
            'UNSAFE BRANCH SWITCH - current cwd is not a verifiable, ' +
            'non-aliased Git worktree.'
        ) -ForegroundColor Yellow
    }
    exit 2
}

$blocking = @()
foreach ($claim in $allClaims) {
    $claimAgent = [string]$claim.agent
    if ($claimAgent -in @('operator','system')) { continue }
    $claimMode = if ($claim.PSObject.Properties['mode']) {
        [string]$claim.mode
    } else { '' }
    if ($claimMode -ceq 'read-only') { continue }
    $claimCwd = if ($claim.PSObject.Properties['cwd']) {
        [string]$claim.cwd
    } else { '' }
    $claimContext = Get-VerifiedGitWorktreeContext -Cwd $claimCwd
    $reason = ''
    if ($null -eq $claimContext) {
        $reason = 'claim_cwd_not_verified_git_worktree'
    } elseif (-not (Test-BridgePathEqual `
        -Left $currentContext.worktree_root `
        -Right $claimContext.worktree_root)) {
        continue
    } elseif ($claimAgent -ne $Agent) {
        $reason = 'foreign_write_claim_in_current_worktree'
    } elseif (-not (Test-BridgePathEqual `
        -Left $currentContext.cwd `
        -Right $claimContext.cwd)) {
        $reason = 'same_agent_claim_cwd_mismatch'
    } else {
        continue
    }
    $blocking += [pscustomobject]@{
        claim         = $claim
        reason        = $reason
        worktree_root = if ($null -eq $claimContext) {
            ''
        } else {
            [string]$claimContext.worktree_root
        }
    }
}

if ($Json -and $Force) {
    Write-Error `
        '-Json -Force is unsupported; canonical override audit cannot be bypassed' `
        -ErrorAction Continue
    exit 2
}

if ($Json) {
    [pscustomobject]@{
        agent             = $Agent
        active_claims     = $allClaims.Count
        blocking_claims   = $blocking.Count
        safe              = ($blocking.Count -eq 0)
        current_worktree_root = [string]$currentContext.worktree_root
        blocking_detail   = @($blocking | ForEach-Object {
            $claim = $_.claim
            [pscustomobject]@{
                task_id     = [string]$claim.task_id
                agent       = [string]$claim.agent
                summary     = [string]$claim.summary
                git_branch  = if ($claim.PSObject.Properties['git_branch']) {
                    [string]$claim.git_branch
                } else { '' }
                write_scope = @($claim.write_scope)
                cwd          = if ($claim.PSObject.Properties['cwd']) {
                    [string]$claim.cwd
                } else { '' }
                worktree_root = [string]$_.worktree_root
                reason        = [string]$_.reason
            }
        })
    } | ConvertTo-Json -Depth 6
    if ($blocking.Count -gt 0 -and -not $Force) {
        exit 2
    }
    exit 0
}

if ($blocking.Count -eq 0) {
    Write-Host "BRANCH SWITCH SAFE - no blocking write claims in this worktree." `
        -ForegroundColor Green
    exit 0
}

Write-Host "UNSAFE BRANCH SWITCH - $($blocking.Count) other-agent write claim(s) active:" `
    -ForegroundColor Yellow
foreach ($entry in $blocking) {
    $claim = $entry.claim
    $branch = ''
    if ($claim.PSObject.Properties['git_branch'] -and `
        [string]$claim.git_branch) {
        $branch = " on branch=$([string]$claim.git_branch)"
    }
    $scope = ''
    if ($claim.PSObject.Properties['write_scope'] -and `
        @($claim.write_scope).Count -gt 0) {
        $scope = " scope=$((@($claim.write_scope)) -join ',')"
    }
    Write-Host ("  - {0} by {1}{2}{3}" -f `
        [string]$claim.task_id, [string]$claim.agent, $branch, $scope) `
        -ForegroundColor Yellow
    Write-Host ("    {0} ({1})" -f `
        [string]$claim.summary, [string]$entry.reason)
}

Write-Host ''
Write-Host "Safe options:" -ForegroundColor Cyan
Write-Host "  1. Use a separate worktree: git worktree add ../wd-temp <branch>"
Write-Host "  2. Wait for the other agent to release the claim"
Write-Host "  3. Override with -Force (logs decision/override_requested event)"

if ($Force) {
    Write-Host ''
    Write-Host "OVERRIDE REQUEST - canonical audit required before permission." `
        -ForegroundColor Red
    $writeAgentEvent = Join-Path $PSScriptRoot 'Write-AgentEvent.ps1'
    if (-not (Test-Path -LiteralPath $writeAgentEvent -PathType Leaf)) {
        Write-Error `
            "REJECTED: canonical override audit writer is missing: $writeAgentEvent" `
            -ErrorAction Continue
        exit 2
    }
    $blockedBy = @(
        $blocking | ForEach-Object {
            [pscustomobject]@{
                task_id = [string]$_.claim.task_id
                agent   = [string]$_.claim.agent
            }
        }
    )
    $payload = [pscustomobject]@{
        override_reason = 'force'
        audit_phase     = 'pre_execution_request'
        action_performed_at_event_time = $false
        canonical_audit_required = $true
        blocked_by      = $blockedBy
    }
    $payloadJson = ($payload | ConvertTo-Json -Depth 6 -Compress)
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
    $taskId = "bridge-branch-switch-override-$stamp"
    $msg = (
        "Branch-switch override requested with $($blocking.Count) active " +
        'write claim(s); no branch switch had run at audit time.'
    )
    $auditOutput = @(
        & $writeAgentEvent `
            -Agent $Agent `
            -Type decision `
            -Status override_requested `
            -Severity medium `
            -TaskId $taskId `
            -Message $msg `
            -PayloadJson $payloadJson
    )
    $auditEvents = @($auditOutput | Where-Object {
        $_ -is [psobject] -and [string]$_.task_id -ceq $taskId
    })
    $auditDelivery = $null
    if ($auditEvents.Count -eq 1) {
        $auditProperty = $auditEvents[0].PSObject.Properties['_bridge_delivery']
        if ($null -ne $auditProperty) { $auditDelivery = $auditProperty.Value }
    }
    if (
        $auditEvents.Count -ne 1 -or
        $null -eq $auditDelivery -or
        [string]$auditDelivery.delivery_status -cne 'canonical' -or
        $auditDelivery.canonical_durable -isnot [bool] -or
        $auditDelivery.canonical_durable -ne $true
    ) {
        Write-Error `
            'REJECTED: branch-switch override audit was not canonically durable' `
            -ErrorAction Continue
        exit 2
    }
    Write-Host 'OVERRIDE AUTHORIZED - canonical pre-execution audit is durable.' `
        -ForegroundColor Red
    exit 0
}

exit 2
