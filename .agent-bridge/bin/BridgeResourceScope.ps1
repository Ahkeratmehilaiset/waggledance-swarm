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

function Resolve-BridgeResourceScopes {
    param([string[]]$Scopes, [string]$Worktree, [string]$BridgeRoot)
    foreach ($scope in @($Scopes)) {
        foreach ($entry in @($scope -split ',')) {
            $raw = $entry.Replace('\','/').Trim()
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
