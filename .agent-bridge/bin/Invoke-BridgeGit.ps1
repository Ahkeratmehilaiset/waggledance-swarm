#requires -Version 5.1
<#
.SYNOPSIS
    Bridge-aware wrapper for branch-moving git operations.

.DESCRIPTION
    Closes operator/Codex/GPT consensus on
    bridge-branch-switch-during-active-claim-2026-05-09 (filed
    2026-05-09T11:30Z, hardened per operator spec 2026-05-09T~12:10Z).

    A `git switch / checkout / merge / rebase / pull` changes branch
    context for every active claim in the SAME Git worktree. Claims in a
    separately verified Git worktree are independent and must not serialize
    branch movement here.
    That can cause:
      - wrong-branch commits
      - wrong test context
      - stale --match-head-commit assumptions
      - untracked-file drift across claims

    This wrapper fails closed unless every non-privileged write claim is
    either in a verified different Git worktree, or belongs to this agent
    with an exactly matching cwd. Read-only claims never block. Missing,
    non-Git, or filesystem-alias claim paths are unverifiable and block.

    Wrapped git verbs:
      switch | checkout | merge | rebase | pull

    Pass-through:
      Any other git verb (status, log, diff, add, commit, push, ...)
      runs unchanged.

    Exit codes:
      0 = git command ran (output passed through)
      2 = blocked: a write claim shares this worktree, its worktree cannot
          be verified, OR your own claim was created from a different cwd
      3 = malformed invocation (no git args)

.PARAMETER Agent
    Which agent is invoking - used for ownership filtering. Required
    for any branch-moving verb.

.PARAMETER Force
    Override the safety check. RESTRICTED: only operator/system
    agents may use -Force. Claude/Codex passing -Force is rejected.
    Override emits a pre-execution decision/override_requested audit event.

.EXAMPLE
    .\.agent-bridge\bin\Invoke-BridgeGit.ps1 -Agent claude -- status
    # Pass-through: runs `git status` unconditionally.

.EXAMPLE
    .\.agent-bridge\bin\Invoke-BridgeGit.ps1 -Agent claude -- switch main
    # Guarded: blocks on other-agent writes in this or an unverifiable worktree.

.EXAMPLE
    .\.agent-bridge\bin\Invoke-BridgeGit.ps1 -Agent claude -- checkout -b new-branch
    # Guarded: same checks as switch.
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory)]
    [ValidateScript({ $_ -cmatch '^[a-z][a-z0-9_-]{1,32}$' })]
    [string] $Agent,

    [switch] $Force,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $GitArgs = @()
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Strip a leading "--" sentinel some shells insert before the trailing args.
if ($GitArgs.Count -gt 0 -and $GitArgs[0] -eq '--') {
    $GitArgs = @($GitArgs[1..($GitArgs.Count - 1)])
}

if ($GitArgs.Count -eq 0) {
    Write-Error -Message "Invoke-BridgeGit.ps1: no git args provided. Use ... -Agent claude -- switch main" `
        -Category InvalidArgument -ErrorAction Continue
    exit 3
}

# Verbs that change branch context for the whole worktree.
# Keep this list narrow: anything not here passes through unguarded.
$BranchMovingVerbs = @('switch','checkout','merge','rebase','pull')

$verb = [string]$GitArgs[0]
$isBranchMoving = $BranchMovingVerbs -contains $verb

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
    # with @(...) to always get an array. The earlier `return ,@()`
    # pattern caused PSStrictMode "property 'agent' not found"
    # because the empty single-array wrapper looked like a single
    # claim with no fields. (Codex finding 2026-05-09T12:26Z.)
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
        # An unreadable component cannot prove filesystem identity.
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
        # Git canonicalizes SUBST paths to their backing worktree. Requiring
        # cwd to be inside the returned root rejects that filesystem alias.
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

function Format-ClaimLine {
    param([Parameter(Mandatory)] [object] $Claim)
    $branch = ''
    if ($Claim.PSObject.Properties['git_branch'] -and `
        [string]$Claim.git_branch) {
        $branch = " branch=$([string]$Claim.git_branch)"
    }
    $scope = ''
    if ($Claim.PSObject.Properties['write_scope'] -and `
        @($Claim.write_scope).Count -gt 0) {
        $scope = " scope=$((@($Claim.write_scope)) -join ',')"
    }
    $cwd = ''
    if ($Claim.PSObject.Properties['cwd']) {
        $cwd = " cwd=$([string]$Claim.cwd)"
    }
    return ("  - {0} by {1} [{2}]{3}{4}{5}" -f `
        [string]$Claim.task_id, [string]$Claim.agent,
        [string]$Claim.mode, $branch, $scope, $cwd)
}

function Invoke-GitAndExit {
    param([Parameter(Mandatory)] [string[]] $ArgsToGit)

    # Preserve native git behavior: stdout/stderr pass through and
    # the script exits with git's raw exit code. With
    # $ErrorActionPreference='Stop', native non-zero exits can become
    # terminating NativeCommandError exceptions before we can forward
    # $LASTEXITCODE, which broke smoke tests for expected git failures.
    $previousEAP = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & git @ArgsToGit
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousEAP
    }
    exit $code
}

# ── Pass-through path: non-branch-moving verbs run unchanged ──────
# Codex finding 2026-05-09T12:26Z: do NOT wrap the git call in a
# function that captures output, otherwise pass-through verbs
# (log/diff/etc) print nothing because the function's pipeline
# absorbs git's stdout. Run git at top level and exit with its
# raw $LASTEXITCODE.
if (-not $isBranchMoving) {
    Invoke-GitAndExit -ArgsToGit $GitArgs
}

# ── Branch-moving path: enforce the guard ─────────────────────────
$claims = @(Get-ActiveClaims)
$currentCwd = (Get-Location).Path
$currentContext = Get-VerifiedGitWorktreeContext -Cwd $currentCwd
if ($null -eq $currentContext) {
    Write-Error -Message (
        'BLOCKED: branch-moving git refused because the current cwd is not ' +
        'a verifiable, non-aliased Git worktree.'
    ) -Category PermissionDenied -ErrorAction Continue
    exit 2
}

# Only writes in this worktree can be affected by this branch movement.
# Missing/unresolvable/aliased claim cwd values cannot prove disjointness and
# therefore remain blockers. Read-only claims never own branch state.
$blocking = @()
foreach ($claim in $claims) {
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
    if ($null -eq $claimContext) {
        $blocking += $claim
        continue
    }
    if (-not (Test-BridgePathEqual `
        -Left $currentContext.worktree_root `
        -Right $claimContext.worktree_root)) {
        continue
    }

    if ($claimAgent -ne $Agent) {
        $blocking += $claim
        continue
    }
    # Same-agent writes in this worktree still require the exact claim cwd.
    if (-not (Test-BridgePathEqual `
        -Left $currentContext.cwd `
        -Right $claimContext.cwd)) {
        $blocking += $claim
    }
}

if ($blocking.Count -eq 0) {
    # Safe: run the git command at top level so its stdout passes
    # through unchanged.
    Invoke-GitAndExit -ArgsToGit $GitArgs
}

# ── Blocked: surface the conflict ─────────────────────────────────
$blockedMsg = "BLOCKED: branch-moving git $verb refused - $($blocking.Count) active write claim(s) share this worktree or have unverifiable cwd identity (BRIDGE_PROTOCOL rule 2)."
Write-Error -Message $blockedMsg -Category PermissionDenied -ErrorAction Continue
foreach ($claim in $blocking) {
    Write-Error -Message (Format-ClaimLine -Claim $claim) `
        -Category PermissionDenied -ErrorAction Continue
    Write-Error -Message "    summary: $([string]$claim.summary)" `
        -Category PermissionDenied -ErrorAction Continue
}
Write-Error -Message 'Safe options: (1) use a separate worktree (git worktree add ../wd-temp <branch>) (2) wait for release (3) operator/system may pass -Force (Claude/Codex may NOT)' `
    -Category PermissionDenied -ErrorAction Continue

if (-not $Force) {
    exit 2
}

# ── Force path: restricted to privileged agents ───────────────────
if ($Agent -notin @('operator','system')) {
    $rejectMsg = "REJECTED: -Force is restricted to operator/system. Claude/Codex may not bypass the guard during autonomous bridge-loop work. Use a separate worktree or wait for the conflicting claim to release."
    Write-Error -Message $rejectMsg -Category PermissionDenied -ErrorAction Continue
    exit 2
}

# Operator/system override: record the request canonically, then run. The event
# is deliberately pre-execution truth: a queued copy must never claim that a
# Git mutation happened when this guard rejected it.
Write-Warning (
    'OVERRIDE REQUEST: operator/system -Force requested; validating a ' +
    "canonical pre-execution audit before running $verb."
)

$writeAgentEvent = Join-Path $PSScriptRoot 'Write-AgentEvent.ps1'
if (-not (Test-Path -LiteralPath $writeAgentEvent -PathType Leaf)) {
    Write-Error -Message "REJECTED: canonical override audit writer is missing: $writeAgentEvent" `
        -Category ResourceUnavailable -ErrorAction Continue
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
    override_reason = 'force_by_privileged_agent'
    audit_phase     = 'pre_execution_request'
    action_performed_at_event_time = $false
    canonical_audit_required = $true
    verb            = $verb
    git_args        = @($GitArgs)
    blocked_by      = $blockedBy
}
$payloadJson = ($payload | ConvertTo-Json -Depth 6 -Compress)
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$taskId = "bridge-git-override-$stamp"
$msg = (
    "git $verb override requested by $Agent over " +
    "$($blocking.Count) active claim(s); no Git action had run at audit time"
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
    Write-Error -Message 'REJECTED: privileged git override audit was not canonically durable' `
        -Category WriteError -ErrorAction Continue
    exit 2
}

Invoke-GitAndExit -ArgsToGit $GitArgs
