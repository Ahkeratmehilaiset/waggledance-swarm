#requires -Version 5.1
<#
.SYNOPSIS
    F28 dry plan: the persistent release worktree on a named, pushed branch with its upstream.

.DESCRIPTION
    Plans, never performs, the release worktree that Deploy-WdRebootBundle.ps1 requires: a
    named branch whose upstream is set and whose upstream head equals the release commit
    (Deploy-WdRebootBundle.ps1, the '@{u}' checks). It validates the repository, the commit,
    the branch name (absent locally AND on the remote), and a persistent C: target path
    (not a RAM disk, not TEMP/TMP, absent or an empty directory), then prints ONE JSON plan
    with the exact argv of every command a later, separately signed activation would run.
    The source repository's actual top level must be persistent C: outside TEMP/TMP too, and
    neither it, the target nor any existing ancestor of either may be a reparse point
    (junction, symlink or mount point), which could redirect a C: path to a RAM disk or TEMP.

    Nothing is created, fetched, pushed or changed. -Apply refuses (exit 3): this wave
    prepares source only, and executing the plan needs a separately signed activation.
    The only network read is 'git ls-remote --heads' against the named remote; an
    unreachable remote refuses (fail closed).

    Exit codes: 0 plan, 2 refuse (reasons in the JSON), 3 -Apply refused.
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory)] [string] $RepositoryPath,
    [Parameter(Mandatory)] [string] $Branch,
    [Parameter(Mandatory)] [string] $Commit,
    [Parameter(Mandatory)] [string] $WorktreePath,
    [string] $Remote = 'origin',
    [string] $GitExecutable = 'git',
    [switch] $Apply
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Write-Plan {
    param([System.Collections.Specialized.OrderedDictionary] $Plan, [int] $Code)
    Write-Output ($Plan | ConvertTo-Json -Depth 6 -Compress)
    exit $Code
}

$plan = [ordered]@{
    schema = 'wd.release-worktree-plan.v1'
    authority_effect = 'none'
    applied = $false
    verdict = 'refuse'
    reasons = @()
    branch = $Branch
    commit = $Commit
    remote = $Remote
    worktree = $WorktreePath
    commands = @()
    verification = @()
}
if ($Apply) {
    $plan['reasons'] = @('apply_requires_signed_activation')
    Write-Plan $plan 3
}

$reasons = New-Object System.Collections.Generic.List[string]
$volatile = @($env:TEMP, $env:TMP) | Where-Object { $_ } | ForEach-Object { [IO.Path]::GetFullPath($_).TrimEnd('\') + '\' }
function Test-WdVolatilePath {
    param([string] $FullPath)
    foreach ($root in $volatile) {
        if (($FullPath.TrimEnd('\') + '\').StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { return $true }
    }
    return $false
}
function Test-WdReparseOnPath {
    # True when the path or any EXISTING ancestor is a reparse point; a missing leaf is not one.
    param([string] $FullPath)
    $current = $FullPath
    while ($current) {
        $item = Get-Item -LiteralPath $current -Force -ErrorAction SilentlyContinue
        if ($null -ne $item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { return $true }
        $parent = [IO.Path]::GetDirectoryName($current)
        if ($parent -eq $current) { break }
        $current = $parent
    }
    return $false
}
function Invoke-Git {
    # Windows PowerShell 5.1 turns redirected native stderr into a terminating error under
    # ErrorActionPreference Stop, so this read-only call relaxes it locally. A Git that
    # cannot start is exit code -1, never a crash without a JSON verdict.
    param([string[]] $Arguments)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $GitExecutable @Arguments 2>$null
        $code = $LASTEXITCODE
    } catch {
        $output = @(); $code = -1
    } finally {
        $ErrorActionPreference = $previous
    }
    return [pscustomobject]@{ Code = $code; Text = ((@($output) -join "`n").Trim()) }
}

# 1. Inputs that need no Git.
if ($Commit -cnotmatch '^[0-9a-f]{40}$') { $reasons.Add('commit_invalid') }
if ($Branch -cnotmatch '^[a-z0-9][a-z0-9._/-]{2,120}$' -or $Branch.Contains('..') -or $Branch.Contains('//') -or
    $Branch.EndsWith('/') -or $Branch.EndsWith('.') -or $Branch.EndsWith('.lock')) {
    $reasons.Add('branch_invalid')
}
if ($Remote -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$') { $reasons.Add('remote_invalid') }
$fullPath = $null
try {
    if ($WorktreePath -match '^[A-Za-z]:[\\/]' -and $WorktreePath.IndexOf([char]0) -lt 0) {
        $fullPath = [IO.Path]::GetFullPath($WorktreePath)
    }
} catch { $fullPath = $null }
if ($null -eq $fullPath -or -not $fullPath.StartsWith('C:\', [StringComparison]::OrdinalIgnoreCase)) {
    $reasons.Add('path_not_persistent_c_drive')
} else {
    if (Test-WdVolatilePath $fullPath) { $reasons.Add('path_volatile') }
    if (Test-WdReparseOnPath $fullPath) { $reasons.Add('path_reparse') }
    if (Test-Path -LiteralPath $fullPath -PathType Leaf) { $reasons.Add('path_is_a_file') }
    elseif (Test-Path -LiteralPath $fullPath -PathType Container) {
        if (@(Get-ChildItem -LiteralPath $fullPath -Force).Count -gt 0) { $reasons.Add('path_not_empty') }
    }
}

# 2. The repository, the commit and the branch, read-only.
$inside = Invoke-Git @('-C', $RepositoryPath, 'rev-parse', '--is-inside-work-tree')
if ($inside.Code -ne 0 -or $inside.Text -cne 'true') {
    $reasons.Add('repository_invalid')
} else {
    # The source of truth is the repository's ACTUAL top level, and the path it was reached by.
    $top = Invoke-Git @('-C', $RepositoryPath, 'rev-parse', '--show-toplevel')
    $sources = @()
    try {
        if ($top.Code -eq 0 -and $top.Text) { $sources += [IO.Path]::GetFullPath($top.Text) }
        $sources += [IO.Path]::GetFullPath($RepositoryPath)
    } catch { $sources = @() }
    if ($sources.Count -lt 2) {
        $reasons.Add('repository_invalid')
    } else {
        if (@($sources | Where-Object { -not $_.StartsWith('C:\', [StringComparison]::OrdinalIgnoreCase) }).Count) {
            $reasons.Add('repository_not_persistent_c_drive')
        }
        if (@($sources | Where-Object { Test-WdVolatilePath $_ }).Count) { $reasons.Add('repository_volatile') }
        if (@($sources | Where-Object { Test-WdReparseOnPath $_ }).Count) { $reasons.Add('repository_reparse') }
    }
    if (-not $reasons.Contains('commit_invalid')) {
        $kind = Invoke-Git @('-C', $RepositoryPath, 'cat-file', '-t', $Commit)
        if ($kind.Code -ne 0 -or $kind.Text -cne 'commit') { $reasons.Add('commit_absent') }
    }
    if (-not $reasons.Contains('branch_invalid')) {
        $local = Invoke-Git @('-C', $RepositoryPath, 'show-ref', '--verify', '--quiet', ('refs/heads/' + $Branch))
        if ($local.Code -eq 0) { $reasons.Add('branch_exists_locally') }
        if (-not $reasons.Contains('remote_invalid')) {
            $remoteHeads = Invoke-Git @('-C', $RepositoryPath, 'ls-remote', '--heads', $Remote, ('refs/heads/' + $Branch))
            if ($remoteHeads.Code -ne 0) { $reasons.Add('remote_unreachable') }
            elseif ($remoteHeads.Text) { $reasons.Add('branch_exists_on_remote') }
        }
    }
}

$plan['reasons'] = @($reasons)
if ($reasons.Count -gt 0) { Write-Plan $plan 2 }

$plan['verdict'] = 'plan'
$plan['worktree'] = $fullPath
$plan['commands'] = @(
    ,@($GitExecutable, '-C', $RepositoryPath, 'worktree', 'add', '-b', $Branch, $fullPath, $Commit)
    ,@($GitExecutable, '-C', $fullPath, 'push', '-u', $Remote, $Branch)
)
$plan['verification'] = @(
    ('git -C <worktree> rev-parse --abbrev-ref HEAD equals ' + $Branch),
    ('git -C <worktree> rev-parse --abbrev-ref --symbolic-full-name @{u} equals ' + $Remote + '/' + $Branch),
    ('git -C <worktree> rev-parse @{u} equals ' + $Commit + ' (remote tip checked with ls-remote, up to 180 s after the push)')
)
Write-Plan $plan 0
