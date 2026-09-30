#requires -Version 5.1
# F8 PowerShell twin of the v2 queue's runtime-root mutex NAME (RCO1 2026-09-30).
#
# tools/bridge_v2_queue_transactions.mutex_name(root) is
#   'Global\WaggleDanceBridgeV2Queue-' + sha256(ascii(canonical_root(root)))[:32]
# with canonical_root = tools/bridge_v2_resource_scope._normalize_absolute on Windows. This file
# derives the same name in PowerShell, so the PowerShell claim scripts can later take the SAME
# root mutex before the claim lock. A different name would silently exclude nothing, so the
# derivation is pinned against Python by tests/tools/test_bridge_v2_queue_mutex_ps.py.
#
# Pure: nothing here creates, opens or waits on a mutex (New-BridgeNamedMutex in
# BridgeNamedMutex.ps1 owns creation); wiring the claim scripts is a later, reviewed slice.

$script:BridgeV2QueueMutexPrefix = 'Global\WaggleDanceBridgeV2Queue-'

function Get-BridgeV2CanonicalRoot {
    # _normalize_absolute on Windows: '/' separators, no alias segment (a trailing dot or space,
    # a ~digit short name), a drive-letter root, no ':' after the drive, no '..', no link or
    # reparse point on any existing component, ASCII only, lowered, trailing '/' removed.
    param([Parameter(Mandatory)] [string] $RuntimeRoot)

    $text = $RuntimeRoot.Replace('\', '/')
    foreach ($part in $text.Split('/')) {
        if ($part -and $part -cne '.' -and $part -cmatch '(?:[. ]$|~[0-9])') { throw 'ambiguous Windows root alias' }
    }
    if ($text -cnotmatch '^[A-Za-z]:/') { throw 'a root must be a local drive-letter path' }
    $drive = $text.Substring(0, 2)
    $rest = $text.Substring(2)
    if ($rest.Contains(':')) { throw 'resource traversal or alternate stream is forbidden' }
    $parts = @($rest.Split('/') | Where-Object { $_ -and $_ -cne '.' })
    if ($parts -ccontains '..') { throw 'resource traversal or alternate stream is forbidden' }
    $walked = $drive
    foreach ($part in $parts) {
        $walked = $walked + '/' + $part
        try {
            $attributes = [IO.File]::GetAttributes($walked)
        } catch [IO.FileNotFoundException], [IO.DirectoryNotFoundException] {
            continue   # as Python: a missing component is not a link
        }
        if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'resource path contains a link/reparse point' }
    }
    $canonical = ($drive + '/' + ($parts -join '/')).TrimEnd('/')
    foreach ($character in $canonical.ToCharArray()) {
        if ([int]$character -gt 127) { throw 'scope must be ASCII: both runtimes must normalize it identically' }
    }
    return $canonical.ToLowerInvariant()   # ASCII only, so this equals Python's str.lower()
}

function Get-BridgeV2QueueMutexName {
    param([Parameter(Mandatory)] [string] $RuntimeRoot)

    $canonical = Get-BridgeV2CanonicalRoot -RuntimeRoot $RuntimeRoot
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $hex = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::ASCII.GetBytes($canonical)))).Replace('-', '').ToLowerInvariant()
    } finally { $sha.Dispose() }
    return $script:BridgeV2QueueMutexPrefix + $hex.Substring(0, 32)
}
