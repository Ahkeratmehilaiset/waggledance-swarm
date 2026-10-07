#requires -Version 5.1
<#
.SYNOPSIS
    Atomically records one compact, reboot-safe WaggleDance lane checkpoint.

.DESCRIPTION
    The checkpoint is intentionally small and lives under the lane worktree's
    ignored .codex-audit directory.  It is the first durable lane-local input
    after reboot.  Markdown handoffs remain audit history and fallback only.

    This helper never changes Git state, bridge state, scheduled tasks, or
    authority.  Branch and HEAD are derived from the supplied worktree rather
    than trusted from caller input.

.PARAMETER WorkHeld
    Opt-in HOLD of the work itself, for the continuity guard: an exact boolean
    declared together with ReleaseHeld. True means the checkpoint item is held:
    never woken and never deferred. Use it only for a real pause of the work.

.PARAMETER ReleaseHeld
    Opt-in deployment or final-signature HOLD: an exact boolean declared together
    with WorkHeld. True keeps preparation recoverable, and a guard dispatch then
    carries release_held_preparation_only with no authority. The guard still
    scans NextAction and Blockers for control tokens, so state the HOLD here.

    When both are omitted, the valid pair in the previous checkpoint of the same
    agent is carried forward unchanged, so a HOLD is never lost by omission; only
    an explicit declaration changes or clears it. An unreadable, foreign, partial
    or malformed previous declaration refuses an omitted write.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet(
        'codex-lead-1',
        'codex-tools-1',
        'claude-rco-1',
        'claude-rco-2',
        'fable-5'
    )]
    [string] $Agent,

    [Parameter(Mandatory)]
    [string] $Worktree,

    [Parameter(Mandatory)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$')]
    [string] $TaskId,

    [Parameter(Mandatory)]
    [ValidatePattern('^[a-z][a-z0-9_-]{0,63}$')]
    [string] $Status,

    [string[]] $WriteScope = @(),
    [string[]] $DirtyPaths = @(),
    [string[]] $Tests = @(),
    [string[]] $BridgeEvidence = @(),
    [string[]] $Blockers = @(),

    [Parameter(Mandatory)]
    [ValidateLength(1, 2000)]
    [string] $NextAction,

    [AllowEmptyString()]
    [string] $NextWakeupUtc = '',

    # Opt-in structured holds: exact booleans, declared together or not at all.
    [object] $WorkHeld = $null,
    [object] $ReleaseHeld = $null
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Resolve-WdLaneStatePath {
    param([Parameter(Mandatory)] [string] $Path)

    $full = [IO.Path]::GetFullPath($Path)
    if ([IO.Path]::GetPathRoot($full) -cne 'C:\') {
        throw "lane checkpoint worktree must be on persistent C: storage: $full"
    }
    if (-not (Test-Path -LiteralPath $full -PathType Container)) {
        throw "lane checkpoint worktree is missing: $full"
    }
    $item = Get-Item -LiteralPath $full -Force -ErrorAction Stop
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "lane checkpoint worktree cannot be a reparse point: $full"
    }
    return $full.TrimEnd('\')
}

function ConvertTo-WdBoundedList {
    param(
        [AllowEmptyCollection()] [string[]] $Values,
        [Parameter(Mandatory)] [string] $Label
    )

    if (@($Values).Count -gt 64) {
        throw "$Label contains more than 64 entries"
    }
    $result = [Collections.Generic.List[string]]::new()
    foreach ($value in @($Values)) {
        $text = ([string]$value).Trim()
        if (-not $text) { continue }
        if ($text.Length -gt 500) {
            throw "$Label contains an entry longer than 500 characters"
        }
        $result.Add($text)
    }
    return @($result)
}

function Invoke-WdLaneGit {
    param(
        [Parameter(Mandatory)] [string] $Root,
        [Parameter(Mandatory)] [string[]] $Arguments
    )

    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& git -C $Root @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($exitCode -ne 0) {
        throw "lane checkpoint Git probe failed: $(@($output) -join "`n")"
    }
    return (@($output | ForEach-Object { [string]$_ }) -join "`n").Trim()
}

$worktreeFull = Resolve-WdLaneStatePath -Path $Worktree
$inside = Invoke-WdLaneGit -Root $worktreeFull -Arguments @(
    'rev-parse', '--is-inside-work-tree'
)
if ($inside -cne 'true') {
    throw "lane checkpoint target is not a Git worktree: $worktreeFull"
}
$branch = Invoke-WdLaneGit -Root $worktreeFull -Arguments @(
    'branch', '--show-current'
)
$head = Invoke-WdLaneGit -Root $worktreeFull -Arguments @('rev-parse', 'HEAD')
if (-not $branch -or $head -cnotmatch '^[0-9a-f]{40}$') {
    throw 'lane checkpoint refuses a detached or malformed Git state'
}

$auditDirectory = Join-Path $worktreeFull '.codex-audit'
if (-not (Test-Path -LiteralPath $auditDirectory -PathType Container)) {
    [void](New-Item -ItemType Directory -Path $auditDirectory -ErrorAction Stop)
}
$auditItem = Get-Item -LiteralPath $auditDirectory -Force -ErrorAction Stop
if (($auditItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "lane checkpoint directory cannot be a reparse point: $auditDirectory"
}

$parsedWakeup = $null
if ($NextWakeupUtc) {
    $parsed = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse(
            $NextWakeupUtc,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::AssumeUniversal,
            [ref]$parsed
        )) {
        throw 'NextWakeupUtc must be an RFC3339 timestamp or empty'
    }
    $parsedWakeup = $parsed.ToUniversalTime().ToString('o')
}

$statePath = Join-Path $auditDirectory 'wd-current-state.json'
if (Test-Path -LiteralPath $statePath) {
    $stateItem = Get-Item -LiteralPath $statePath -Force -ErrorAction Stop
    if (($stateItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "lane checkpoint cannot replace a reparse point: $statePath"
    }
}

# Opt-in structured holds for the continuity guard: work_held and release_held are exact
# booleans written together or not at all, never defaulted. When both parameters are
# omitted, the previous checkpoint's valid pair is carried forward unchanged, so a HOLD is
# never lost by omission; an undeterminable previous declaration refuses the write.
$holdFields = $null
# A foreach statement, not a pipeline: inside a script block, $PSBoundParameters is that
# block's own empty dictionary.
$declaredHolds = 0
foreach ($holdName in @('WorkHeld', 'ReleaseHeld')) { if ($PSBoundParameters.ContainsKey($holdName)) { $declaredHolds++ } }
if ($declaredHolds -gt 0) {
    if ($declaredHolds -ne 2 -or $WorkHeld -isnot [bool] -or $ReleaseHeld -isnot [bool]) {
        throw 'WorkHeld and ReleaseHeld must be declared together as exact booleans'
    }
    $holdFields = [ordered]@{ work_held = [bool]$WorkHeld; release_held = [bool]$ReleaseHeld }
}
elseif (Test-Path -LiteralPath $statePath -PathType Leaf) {
    $undeterminable = 'previous lane checkpoint hold declaration is undeterminable; declare WorkHeld and ReleaseHeld explicitly'
    try {
        if ((Get-Item -LiteralPath $statePath -Force -ErrorAction Stop).Length -gt 32768) { throw 'oversized' }
        $previous = [IO.File]::ReadAllText($statePath, [Text.Encoding]::UTF8) | ConvertFrom-Json -ErrorAction Stop
    }
    catch { throw $undeterminable }
    if ($previous -isnot [Management.Automation.PSCustomObject]) { throw $undeterminable }
    $previousHolds = @($previous.PSObject.Properties | Where-Object { $_.Name -cin @('work_held', 'release_held') })
    if ($previousHolds.Count -gt 0) {
        $identity = @($previous.PSObject.Properties | Where-Object {
            ($_.Name -ceq 'schema' -and $_.Value -ceq 'wd.lane-current.v1') -or ($_.Name -ceq 'agent' -and $_.Value -ceq $Agent) })
        if ($previousHolds.Count -ne 2 -or $identity.Count -ne 2 -or
            @($previousHolds | Where-Object { $_.Value -isnot [bool] }).Count -gt 0) {
            throw $undeterminable
        }
        $holdFields = [ordered]@{}
        foreach ($name in @('work_held', 'release_held')) {
            $holdFields[$name] = [bool](@($previousHolds | Where-Object { $_.Name -ceq $name })[0].Value)
        }
    }
}

$record = [ordered]@{
    schema = 'wd.lane-current.v1'
    updated_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
    agent = $Agent
    task_id = $TaskId
    status = $Status
    worktree = $worktreeFull
    branch = $branch
    head = $head
    write_scope = @(ConvertTo-WdBoundedList -Values $WriteScope -Label WriteScope)
    dirty_paths = @(ConvertTo-WdBoundedList -Values $DirtyPaths -Label DirtyPaths)
    tests = @(ConvertTo-WdBoundedList -Values $Tests -Label Tests)
    bridge_evidence = @(
        ConvertTo-WdBoundedList -Values $BridgeEvidence -Label BridgeEvidence
    )
    blockers = @(ConvertTo-WdBoundedList -Values $Blockers -Label Blockers)
    next_action = $NextAction.Trim()
    next_wakeup_utc = $parsedWakeup
}
if ($null -ne $holdFields) {
    $record.work_held = $holdFields.work_held
    $record.release_held = $holdFields.release_held
}
$json = ($record | ConvertTo-Json -Depth 5) + [Environment]::NewLine
if ([Text.Encoding]::UTF8.GetByteCount($json) -gt 32768) {
    throw 'lane checkpoint exceeds the 32 KiB compact-state limit'
}

$temporary = Join-Path $auditDirectory (
    '.wd-current-state.{0}.{1}.tmp' -f $PID, [guid]::NewGuid().ToString('N')
)
$utf8 = New-Object Text.UTF8Encoding($false)
try {
    [IO.File]::WriteAllText($temporary, $json, $utf8)
    Move-Item -LiteralPath $temporary -Destination $statePath -Force
}
finally {
    if (Test-Path -LiteralPath $temporary -PathType Leaf) {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}

Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json -ErrorAction Stop
