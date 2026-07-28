#requires -Version 5.1
<#
.SYNOPSIS
    Bridge-aware wrapper for branch-moving git operations.

.DESCRIPTION
    Closes operator/Codex/GPT consensus on
    bridge-branch-switch-during-active-claim-2026-05-09 (filed
    2026-05-09T11:30Z, hardened per operator spec 2026-05-09T~12:10Z).

    When multiple agents share the canonical source worktree at
    C:\Python\project2, active claims protect file PATHS, but
    a `git switch / checkout / merge / rebase / pull` changes the
    branch context for every active claim's working tree at once.
    That can cause:
      - wrong-branch commits
      - wrong test context
      - stale --match-head-commit assumptions
      - untracked-file drift across claims

    This wrapper enforces the rule: branch-moving git operations
    are only allowed when EITHER no active claim exists OR all
    active claims belong to the same agent AND the cwd matches the
    claim's recorded cwd.

    Wrapped git verbs:
      switch | checkout | merge | rebase | pull

    Pass-through:
      Any other git verb (status, log, diff, add, commit, push, ...)
      runs unchanged.

    Exit codes:
      0 = git command ran (output passed through)
      2 = blocked: another agent holds an active claim, OR your
          own claim was created from a different cwd
      3 = malformed invocation (no git args)

.PARAMETER Agent
    Which agent is invoking - used for ownership filtering. Required
    for any branch-moving verb.

.PARAMETER Force
    Override the safety check. RESTRICTED: only a bound operator
    session may use -Force. Agent/system callers are rejected.
    Override emits a decision/override audit event.

.EXAMPLE
    .\.agent-bridge\bin\Invoke-BridgeGit.ps1 -Agent claude -- status
    # Pass-through: runs `git status` unconditionally.

.EXAMPLE
    .\.agent-bridge\bin\Invoke-BridgeGit.ps1 -Agent claude -- switch main
    # Guarded: blocks if any other-agent active claim exists.

.EXAMPLE
    .\.agent-bridge\bin\Invoke-BridgeGit.ps1 -Agent claude -- checkout -b new-branch
    # Guarded: same checks as switch.
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory)]
    [string] $Agent,

    [switch] $Force,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $GitArgs = @()
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$sessionIdentity = Join-Path $PSScriptRoot 'AgentBridgeSessionIdentity.ps1'
. $sessionIdentity
Assert-AgentBridgeSessionIdentity -RequestedAgent $Agent

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


# Spec invariant: allow only when (no claims) OR (all claims belong
# to this agent AND cwd matches each claim's recorded cwd).
$blocking = @()
foreach ($claim in $claims) {
    $claimAgent = [string]$claim.agent
    $claimCwd = if ($claim.PSObject.Properties['cwd']) {
        [string]$claim.cwd
    } else { '' }

    if ($claimAgent -ne $Agent) {
        # Different agent's claim - always blocks (except for
        # privileged operator/system claims).
        if ($claimAgent -in @('operator','system')) { continue }
        $blocking += $claim
        continue
    }
    # Same-agent claim: must also have matching cwd.
    if ($claimCwd -and $claimCwd -ne $currentCwd) {
        $blocking += $claim
    }
}

if ($blocking.Count -eq 0) {
    # Safe: run the git command at top level so its stdout passes
    # through unchanged.
    Invoke-GitAndExit -ArgsToGit $GitArgs
}

# ── Blocked: surface the conflict ─────────────────────────────────
$blockedMsg = "BLOCKED: branch-moving git $verb refused - $($blocking.Count) active claim(s) make this unsafe under the shared-worktree rule (BRIDGE_PROTOCOL rule 2)."
Write-Error -Message $blockedMsg -Category PermissionDenied -ErrorAction Continue
foreach ($claim in $blocking) {
    Write-Error -Message (Format-ClaimLine -Claim $claim) `
        -Category PermissionDenied -ErrorAction Continue
    Write-Error -Message "    summary: $([string]$claim.summary)" `
        -Category PermissionDenied -ErrorAction Continue
}
Write-Error -Message 'Safe options: (1) use a separate worktree (git worktree add ../wd-temp <branch>) (2) wait for release (3) a bound operator may pass -Force (agents/system may NOT)' `
    -Category PermissionDenied -ErrorAction Continue

if (-not $Force) {
    exit 2
}

# ── Force path: restricted to the bound operator ──────────────────
if ($Agent -cne 'operator') {
    $rejectMsg = "REJECTED: -Force is restricted to a bound operator session. Agents/system may not bypass the guard during autonomous bridge-loop work. Use a separate worktree or wait for the conflicting claim to release."
    Write-Error -Message $rejectMsg -Category PermissionDenied -ErrorAction Continue
    exit 2
}

# Operator override: emit audit event then run.
Write-Warning "OVERRIDE: bound operator -Force engaged. Running $verb anyway."

$writeAgentEvent = Join-Path $PSScriptRoot 'Write-AgentEvent.ps1'
if (-not (Test-Path -LiteralPath $writeAgentEvent -PathType Leaf)) {
    throw 'cannot authorize git override without Write-AgentEvent.ps1'
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
    verb            = $verb
    git_args        = @($GitArgs)
    blocked_by      = $blockedBy
}
$payloadJson = ($payload | ConvertTo-Json -Depth 6 -Compress)
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$taskId = "bridge-git-override-$stamp"
$msg = "git $verb override by $Agent over $($blocking.Count) active claim(s)"
& $writeAgentEvent `
    -Agent $Agent `
    -Type decision `
    -Status override `
    -Severity medium `
    -TaskId $taskId `
    -Message $msg `
    -PayloadJson $payloadJson |
    Out-Null

Invoke-GitAndExit -ArgsToGit $GitArgs
