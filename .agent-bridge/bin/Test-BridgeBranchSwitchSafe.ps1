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
          or pass -Force to override (logs a warning event).

    The check is read-only by default. With -Force it emits a
    bridge event recording the override so audit trails see the
    branch switch was knowingly done over an active claim.

.PARAMETER Agent
    Which agent is about to switch - used to filter "other-agent"
    claims (own-agent claims do not block; they're your work).

.PARAMETER Force
    Override the safety check and emit a `decision/override` event
    so the audit trail captures the unsafe switch.

.PARAMETER Json
    Emit the active-claim state as JSON instead of human-readable
    text. Useful for chaining into other guards.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet('codex','claude','operator','system')]
    [string] $Agent,

    [switch] $Force,
    [switch] $Json
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$bridgeRoot = Split-Path -Parent $PSScriptRoot
$claimsDir = Join-Path (Join-Path $bridgeRoot 'work_queue') 'claims'

function Get-ActiveClaims {
    if (-not (Test-Path -LiteralPath $claimsDir)) {
        return ,@()
    }
    $out = @()
    foreach ($file in @(Get-ChildItem -Path $claimsDir -Filter '*.json' -File `
                                 -ErrorAction SilentlyContinue)) {
        try {
            $obj = Get-Content -Raw -Path $file.FullName -Encoding UTF8 |
                ConvertFrom-Json
            $out += $obj
        } catch {}
    }
    return ,$out
}

$allClaims = Get-ActiveClaims

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
Write-Host "  3. Override with -Force (logs decision/override event)"

if ($Force) {
    Write-Host ''
    Write-Host "OVERRIDE - proceeding with branch switch despite active claims." `
        -ForegroundColor Red
    $writeAgentEvent = Join-Path $PSScriptRoot 'Write-AgentEvent.ps1'
    if (Test-Path -LiteralPath $writeAgentEvent) {
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
            blocked_by      = $blockedBy
        }
        $payloadJson = ($payload | ConvertTo-Json -Depth 6 -Compress)
        $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
        $taskId = "bridge-branch-switch-override-$stamp"
        $msg = "Branch-switch override: $($blocking.Count) active write claim(s) by other agent(s) at switch time."
        & $writeAgentEvent `
            -Agent $Agent `
            -Type decision `
            -Status override `
            -Severity medium `
            -TaskId $taskId `
            -Message $msg `
            -PayloadJson $payloadJson `
            | Out-Null
    }
    exit 0
}

exit 2
