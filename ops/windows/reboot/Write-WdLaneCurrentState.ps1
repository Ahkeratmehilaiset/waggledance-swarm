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
    [string] $NextWakeupUtc = ''
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
$json = ($record | ConvertTo-Json -Depth 5) + [Environment]::NewLine
if ([Text.Encoding]::UTF8.GetByteCount($json) -gt 32768) {
    throw 'lane checkpoint exceeds the 32 KiB compact-state limit'
}

$statePath = Join-Path $auditDirectory 'wd-current-state.json'
if (Test-Path -LiteralPath $statePath) {
    $stateItem = Get-Item -LiteralPath $statePath -Force -ErrorAction Stop
    if (($stateItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "lane checkpoint cannot replace a reparse point: $statePath"
    }
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
