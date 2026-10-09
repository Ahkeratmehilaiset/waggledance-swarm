#requires -Version 5.1
# A local audit checkpoint is physical; source claims remain repository-logical.
function Resolve-BridgeUnaliasedPath {
    param([string]$Path)
    foreach ($segment in @($Path.Replace('\','/') -split '/')) {
        if ($segment -and $segment -ne '.' -and ($segment -match '[. ]$' -or $segment -match '~[0-9]')) { throw 'ambiguous Windows root alias' }
    }
    $full = [IO.Path]::GetFullPath($Path)
    $part = $full
    while ($part) {
        $item = $null
        try { $item = Get-Item -LiteralPath $part -Force -ErrorAction Stop }
        catch [System.Management.Automation.ItemNotFoundException] {}
        if ($null -ne $item) {
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "resource path contains a reparse point: $part" }
        }
        $parent = Split-Path -Parent $part
        if ($parent -eq $part) { break }
        $part = $parent
    }
    return $full.Replace('\','/').TrimEnd('/').ToLowerInvariant()
}

function Get-BridgeGitPointer {
    param([string]$Path)
    $bytes = [IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -gt 4096) { throw 'git pointer file is oversized' }
    $lines = @(([Text.Encoding]::UTF8.GetString($bytes)) -split "`r?`n")
    return $lines[0].Trim()
}

# RS7: scopes are repository-relative, so the cwd must be a git top level by git's own discovery test, read from the
# file system only: <cwd>/.git is a directory or a "gitdir: <dir>" file; that git dir holds HEAD and its common dir
# (commondir, else itself) holds objects and refs. An empty marker or a dangling pointer is refused; nothing is guessed.
# RS7-D (Tools 9758A39B): a valid top level nested below ANOTHER valid top level names one file two ways too, so it is
# refused as well. Only an ancestor that passes the same test counts (one that cannot be validated is no claim cwd).
function Assert-BridgeRepositoryTopLevel {
    param([string]$Worktree)
    $top = $Worktree.TrimEnd('/','\')
    if (-not (Test-BridgeRepositoryTopLevel $top)) { throw 'the claim cwd is not a repository top level (no valid .git there): every scope except * is refused' }
    # The ancestors of the full path ("." and ".." resolved), never the lexical ones; a UNC path stops at its share.
    $normal = [IO.Path]::GetFullPath($top).Replace('\','/').TrimEnd('/')
    $parts = @($normal -split '/')
    $floor = if ($normal.StartsWith('//')) { 4 } else { 1 }
    for ($depth = $parts.Count - 1; $depth -ge $floor; $depth--) {
        $ancestor = $parts[0..($depth - 1)] -join '/'
        if (Test-BridgeRepositoryTopLevel $ancestor) {
            throw "the claim cwd is a repository nested inside another repository ($ancestor): one file has two repository paths, so every scope except * is refused"
        }
    }
}

function Test-BridgeRepositoryTopLevel {
    param([string]$Top)
    $top = $Top
    $valid = $false
    try {
        $marker = $top + '/.git'
        $gitDir = $null
        if (Test-Path -LiteralPath $marker -PathType Container) { $gitDir = $marker }
        elseif (Test-Path -LiteralPath $marker -PathType Leaf) {
            $line = Get-BridgeGitPointer $marker
            if ($line.StartsWith('gitdir:') -and $line.Substring(7).Trim()) {
                $target = $line.Substring(7).Trim()
                $gitDir = if ($target -match '^(?:[A-Za-z]:)?[/\\]') { $target } else { $top + '/' + $target }
                # RS7-L2 (RCO1): a .git FILE counts only when git's worktree bookkeeping agrees: the admin dir has
                # commondir and its gitdir back-link names this cwd's .git (not "gitdir: ../.git" from a subdirectory).
                $back = Get-BridgeGitPointer ($gitDir + '/gitdir')
                if ($back -notmatch '^(?:[A-Za-z]:)?[/\\]') { $back = $gitDir + '/' + $back }
                if (-not (Test-Path -LiteralPath ($gitDir + '/commondir') -PathType Leaf) -or
                    -not ([IO.Path]::GetFullPath($back).TrimEnd('\','/') -ieq [IO.Path]::GetFullPath($top + '/.git').TrimEnd('\','/'))) {
                    $gitDir = $null
                }
            }
        }
        if ($gitDir) {
            $common = $gitDir
            if (Test-Path -LiteralPath ($gitDir + '/commondir') -PathType Leaf) {
                $target = Get-BridgeGitPointer ($gitDir + '/commondir')
                $common = if ($target -match '^(?:[A-Za-z]:)?[/\\]') { $target } else { $gitDir + '/' + $target }
            }
            $valid = (Test-Path -LiteralPath ($gitDir + '/HEAD') -PathType Leaf) -and
                (Test-Path -LiteralPath ($common + '/objects') -PathType Container) -and
                (Test-Path -LiteralPath ($common + '/refs') -PathType Container)
        }
    } catch { $valid = $false }
    return $valid
}

function Resolve-BridgeResourceScopes {
    param([string[]]$Scopes, [string]$Worktree, [string]$BridgeRoot)
    foreach ($scope in @($Scopes)) {
        foreach ($entry in @($scope -split ',')) {
            $raw = $entry.Replace('\','/').Trim()
            if ($Worktree -and $raw -ne '*') { Assert-BridgeRepositoryTopLevel $Worktree }
            $kind = 'repo'
            if ($raw -match '^([a-z_-]+):' -and $raw -notmatch '^[A-Za-z]:/') {
                $pair = $raw -split ':',2
                $kind = $pair[0].ToLowerInvariant(); $raw = $pair[1]
                if ($kind -notin @('repo','worktree','shared')) { throw 'unknown resource kind' }
            } elseif ($raw.Trim('/').ToLowerInvariant() -ceq '.codex-audit/wd-current-state.json' -and $Worktree) { $kind = 'worktree' }
            if (($raw -split '/') -contains '..' -or ($raw.Contains(':') -and $raw -notmatch '^[A-Za-z]:/')) { throw 'resource traversal or alternate stream is forbidden' }
            foreach ($segment in @($raw -split '/')) {
                if ($segment -and $segment -ne '.' -and ($segment -match '[. ]$' -or $segment -match '~[0-9]')) { throw 'ambiguous Windows path alias' }
            }
            if ([IO.Path]::IsPathRooted($raw)) {
                $full = Resolve-BridgeUnaliasedPath $raw
                $base = if ($Worktree) { Resolve-BridgeUnaliasedPath $Worktree } else { '' }
                $shared = Resolve-BridgeUnaliasedPath $BridgeRoot
                if ($base -and $full.StartsWith($base+'/')) {
                    $raw = $full.Substring($base.Length+1)
                    if ($raw -ceq '.codex-audit/wd-current-state.json') { $kind='worktree' }
                } elseif ($full.StartsWith($shared+'/')) { $kind='shared';$raw=$full.Substring($shared.Length+1) }
                else { throw 'absolute scope is outside the worktree/shared root' }
            }
            $raw = (@($raw -split '/' | Where-Object {$_ -and $_ -ne '.'}) -join '/').ToLowerInvariant()
            if (-not $raw -or ($raw.Contains('*') -and $raw -ne '*') -or $raw.Contains('?')) { throw 'scope must name a path or the whole repository (*)' }
            $root = ''
            if ($kind -eq 'worktree') {
                if (-not $Worktree -or ($raw -ne '.codex-audit' -and -not $raw.StartsWith('.codex-audit/'))) { throw 'worktree resources require cwd and must be under .codex-audit' }
                $root = Resolve-BridgeUnaliasedPath $Worktree
                [void](Resolve-BridgeUnaliasedPath (Join-Path $Worktree $raw))
            } elseif ($kind -eq 'shared') {
                $root = Resolve-BridgeUnaliasedPath $BridgeRoot
                [void](Resolve-BridgeUnaliasedPath (Join-Path $BridgeRoot $raw))
            } elseif ($Worktree -and $raw -ne '*') { [void](Resolve-BridgeUnaliasedPath (Join-Path $Worktree $raw)) }
            [pscustomobject]@{kind=$kind;path=$raw;root=$root}
        }
    }
}

function Test-BridgeResourceOverlap {
    param([object[]]$A, [object[]]$B)
    foreach ($left in @($A)) { foreach ($right in @($B)) {
        if ($left.path -eq '*' -or $right.path -eq '*') { return $true }
        if ($left.kind -eq 'repo' -or $right.kind -eq 'repo') { $aPath=$left.path; $bPath=$right.path }
        else { $aPath=$left.root+'/'+$left.path; $bPath=$right.root+'/'+$right.path }
        if ($aPath -ceq $bPath -or $aPath.StartsWith($bPath+'/') -or $bPath.StartsWith($aPath+'/')) { return $true }
    }}
    return $false
}
