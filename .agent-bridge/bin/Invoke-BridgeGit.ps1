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
      Leading --no-pager, --no-optional-locks and --no-replace-objects
      are supported, as are -C <dir> (the guard checks the EFFECTIVE
      directory Git will use) and -c <key>[=<value>] for display-only keys
      (color.ui, core.quotepath, advice.detachedHead). Every other leading
      option is refused: repository/config redirection would make the guard
      inspect a different Git context. Branch-moving verbs also refuse
      inherited GIT_DIR/WORK_TREE/NAMESPACE/CONFIG* redirection variables.

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
    agents may use -Force. Claude/Codex passing -Force is rejected. On the
    branch-moving path -Agent is bound to the pinned session identity
    (Assert-AgentBridgeSessionIdentity), so a lane cannot name another agent.
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
    $GitArgs = @($GitArgs | Select-Object -Skip 1)
}

if ($GitArgs.Count -eq 0) {
    Write-Error -Message "Invoke-BridgeGit.ps1: no git args provided. Use ... -Agent claude -- switch main" `
        -Category InvalidArgument -ErrorAction Continue
    exit 3
}

# Verbs that change branch context for the whole worktree.
# Keep this list narrow: anything not here passes through unguarded.
$BranchMovingVerbs = @('switch','checkout','merge','rebase','pull')

# F9: parse EVERY leading Git global option before the verb. Only these are
# accepted (anything else, including --git-dir, --work-tree, --namespace,
# --config-env, --exec-path, --bare and attached -C<dir>/-c<k=v> forms, is
# refused before Git runs, for every verb):
#   --no-pager, --no-optional-locks, --no-replace-objects (context-preserving);
#   -C <dir>  as a separate argument. It is resolved exactly as Git does
#             (relative to the previous effective directory; an empty value
#             keeps it), and the branch guard below checks claims against that
#             EFFECTIVE directory, not the caller's cwd;
#   -c <key>[=<value>] only for the display-only keys in $allowedConfigKeys,
#             with a short plain value. Keys that can move the worktree, run
#             code or change the object/ref store are refused.
$verbIndex = 0
$safeGlobalFlags = @('--no-pager', '--no-optional-locks', '--no-replace-objects')
$allowedConfigKeys = @('color.ui', 'core.quotepath', 'advice.detachedhead')
$location = Get-Location
$effectiveDirectory = $null
if ([string]$location.Provider.Name -ceq 'FileSystem') {
    $effectiveDirectory = [string]$location.ProviderPath
}

function Stop-BridgeGitOption {
    param([Parameter(Mandatory)] [string] $Reason)
    Write-Error -Message ('BLOCKED: ' + $Reason + ' Repository/config redirection is not allowed by the branch guard.') `
        -Category PermissionDenied -ErrorAction Continue
    exit 2
}

function Test-BridgeGitFullyQualifiedPath {
    param([string] $Path)
    # Non-Windows hosts (Linux CI): an absolute POSIX path is fully qualified.
    if ([IO.Path]::DirectorySeparatorChar -eq '/') { return $Path.StartsWith('/') }
    return ($Path -match '^[A-Za-z]:[\\/]' -or $Path -match '^[\\/]{2}[^\\/]+[\\/]+[^\\/]+')
}

while ($verbIndex -lt $GitArgs.Count -and $GitArgs[$verbIndex].StartsWith('-')) {
    $option = [string]$GitArgs[$verbIndex]
    if ($safeGlobalFlags -ccontains $option) {
        $verbIndex++
        continue
    }
    if ($option -ceq '-C') {
        if ($verbIndex + 1 -ge $GitArgs.Count) { Stop-BridgeGitOption -Reason '-C requires a directory argument.' }
        $directory = [string]$GitArgs[$verbIndex + 1]
        if ($directory.IndexOf([char]0) -ge 0) { Stop-BridgeGitOption -Reason '-C directory contains NUL.' }
        if ($directory.Length -gt 0) {
            if ($null -eq $effectiveDirectory) { Stop-BridgeGitOption -Reason '-C needs a FileSystem current location.' }
            $rootPart = ''
            try { $rootPart = [IO.Path]::GetPathRoot($directory) } catch { Stop-BridgeGitOption -Reason '-C directory is not a valid path.' }
            if (-not [string]::IsNullOrEmpty($rootPart) -and -not (Test-BridgeGitFullyQualifiedPath -Path $directory)) {
                # Drive-relative (C:dir) and root-relative (\dir) forms depend on
                # per-drive process state that the guard cannot bind exactly.
                Stop-BridgeGitOption -Reason '-C directory must be relative or fully qualified.'
            }
            try {
                $effectiveDirectory = [IO.Path]::GetFullPath([IO.Path]::Combine($effectiveDirectory, $directory))
            } catch {
                Stop-BridgeGitOption -Reason '-C directory cannot be resolved.'
            }
        }
        $verbIndex += 2
        continue
    }
    if ($option -ceq '-c') {
        if ($verbIndex + 1 -ge $GitArgs.Count) { Stop-BridgeGitOption -Reason '-c requires a key[=value] argument.' }
        $setting = [string]$GitArgs[$verbIndex + 1]
        $separator = $setting.IndexOf('=')
        $key = if ($separator -ge 0) { $setting.Substring(0, $separator) } else { $setting }
        $value = if ($separator -ge 0) { $setting.Substring($separator + 1) } else { '' }
        if ($allowedConfigKeys -cnotcontains $key.ToLowerInvariant() -or $value -cnotmatch '^[A-Za-z0-9_-]{0,32}$') {
            Stop-BridgeGitOption -Reason ('-c key is not on the display-only allowlist: ' + $key.Substring(0, [Math]::Min(64, $key.Length)) + '.')
        }
        $verbIndex += 2
        continue
    }
    Stop-BridgeGitOption -Reason 'unsupported leading Git option.'
}
if ($verbIndex -ge $GitArgs.Count -or [string]::IsNullOrWhiteSpace($GitArgs[$verbIndex])) {
    Write-Error -Message 'Invoke-BridgeGit.ps1: no git command provided.' `
        -Category InvalidArgument -ErrorAction Continue
    exit 3
}
$verb = [string]$GitArgs[$verbIndex]
$isBranchMoving = $BranchMovingVerbs -contains $verb

# A hook's inherited GIT_DIR can make rev-parse report cwd as the worktree
# while the actual command still changes another worktree's HEAD. Refuse the
# override rather than silently changing the caller's intended Git target.
# F9: the same applies to namespace and injected-configuration variables
# (GIT_CONFIG_PARAMETERS is how -c reaches child processes and can set
# core.worktree; GIT_CONFIG_COUNT/KEY_n/VALUE_n and GIT_CONFIG[_GLOBAL|_SYSTEM]
# inject config files or values). Names are compared case-insensitively.
if ($isBranchMoving) {
    $refusedNames = @('GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE',
        'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_NAMESPACE',
        'GIT_CONFIG', 'GIT_CONFIG_PARAMETERS', 'GIT_CONFIG_COUNT', 'GIT_CONFIG_GLOBAL',
        'GIT_CONFIG_SYSTEM')
    $processEnvironment = [Environment]::GetEnvironmentVariables('Process')
    # .PSBase: an environment entry literally named "Keys" must not shadow the member.
    $gitOverrides = @(@($processEnvironment.PSBase.Keys) | ForEach-Object { [string]$_ } | Where-Object {
        $upper = $_.ToUpperInvariant()
        ($refusedNames -ccontains $upper) -or ($upper -cmatch '^GIT_CONFIG_(KEY|VALUE)_[0-9]+$')
    } | Sort-Object)
    if (@($gitOverrides).Count -gt 0) {
        Write-Error -Message (
            'BLOCKED: branch-moving git refuses inherited repository overrides: ' +
            ($gitOverrides -join ', ')
        ) -Category PermissionDenied -ErrorAction Continue
        exit 2
    }

    # F9: -Agent carries authority only on this path (claim ownership and
    # -Force). Bind it to the pinned session identity: a bound lane cannot act
    # as another agent, and operator/system need a verified bound caller.
    $identityHelper = Join-Path $PSScriptRoot 'AgentBridgeSessionIdentity.ps1'
    try {
        if (-not (Test-Path -LiteralPath $identityHelper -PathType Leaf)) {
            throw 'session identity helper is missing'
        }
        . $identityHelper
        Assert-AgentBridgeSessionIdentity -RequestedAgent $Agent
    } catch {
        Write-Error -Message ('BLOCKED: branch-moving git refused: ' + $_.Exception.Message) `
            -Category PermissionDenied -ErrorAction Continue
        exit 2
    }
}

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
# F9: the guard binds the EFFECTIVE Git directory (after every -C), which is
# exactly where Git will run the branch-moving command.
if ($null -eq $effectiveDirectory) {
    Write-Error -Message 'BLOCKED: branch-moving git needs a FileSystem current location.' `
        -Category PermissionDenied -ErrorAction Continue
    exit 2
}
$currentContext = Get-VerifiedGitWorktreeContext -Cwd $effectiveDirectory
if ($null -eq $currentContext) {
    Write-Error -Message (
        'BLOCKED: branch-moving git refused because the effective Git directory is not ' +
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
# -Agent was already bound to the session identity above, so a lane cannot
# self-grant operator; public 'system' is refused by the identity helper.
if ($Agent -cnotin @('operator','system')) {
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
