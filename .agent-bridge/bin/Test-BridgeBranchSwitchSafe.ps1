#requires -Version 5.1
<#
.SYNOPSIS
    Pre-flight check before a branch-changing git operation.

.DESCRIPTION
    Closes BRIDGE_PROTOCOL rule 2 follow-up - finding
    `bridge-branch-switch-during-active-claim-2026-05-09` filed by
    Codex on 2026-05-09T11:30Z.

    The bridge runs as multiple agents sharing a single worktree
    (C:\Python\project2-master). When one agent has an active write
    claim, another agent doing `git switch / checkout / rebase /
    merge` mutates the first agent's working tree from under them -
    they may stage stale paths or commit on the wrong branch.

    Run this guard immediately before any branch-changing git
    operation:

        .\.agent-bridge\bin\Test-BridgeBranchSwitchSafe.ps1 -Agent claude

    Exit codes:
      0 = safe to switch (no other-agent active write claims)
      2 = UNSAFE - another agent holds an active write claim;
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

$allClaims = @(Get-ActiveClaims)

# Only WRITE claims by OTHER agents block - read-only claims and
# own-agent claims do not affect a branch switch.
$blocking = @(
    $allClaims |
        Where-Object {
            [string]$_.mode -eq 'write' -and
            [string]$_.agent -ne $Agent -and
            $_.agent -notin @('operator','system')
        }
)

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
        blocking_detail   = @($blocking | ForEach-Object {
            [pscustomobject]@{
                task_id     = [string]$_.task_id
                agent       = [string]$_.agent
                summary     = [string]$_.summary
                git_branch  = if ($_.PSObject.Properties['git_branch']) {
                    [string]$_.git_branch
                } else { '' }
                write_scope = @($_.write_scope)
            }
        })
    } | ConvertTo-Json -Depth 6
    if ($blocking.Count -gt 0 -and -not $Force) {
        exit 2
    }
    exit 0
}

if ($blocking.Count -eq 0) {
    Write-Host "BRANCH SWITCH SAFE - no other-agent active write claims." `
        -ForegroundColor Green
    exit 0
}

Write-Host "UNSAFE BRANCH SWITCH - $($blocking.Count) other-agent write claim(s) active:" `
    -ForegroundColor Yellow
foreach ($claim in $blocking) {
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
    Write-Host ("    {0}" -f [string]$claim.summary)
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
                task_id = [string]$_.task_id
                agent   = [string]$_.agent
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
