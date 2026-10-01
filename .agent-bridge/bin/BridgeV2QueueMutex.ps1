#requires -Version 5.1
# F8 PowerShell twin of the v2 queue's runtime-root mutex NAME (RCO1 2026-09-30).
#
# tools/bridge_v2_queue_transactions.mutex_name(root) is
#   'Global\WaggleDanceBridgeV2Queue-' + sha256(ascii(canonical_root(root)))[:32]
# with canonical_root = tools/bridge_v2_resource_scope._normalize_absolute on Windows. This file
# derives the same name in PowerShell, so the PowerShell claim scripts take the SAME root mutex
# before the claim lock. A different name would silently exclude nothing, so the
# derivation is pinned against Python by tests/tools/test_bridge_v2_queue_mutex_ps.py.
#
# Get-BridgeV2QueueMutexName is pure. Enter-BridgeV2QueueMutex takes that SAME kernel object
# through New-BridgeNamedMutex (BridgeNamedMutex.ps1: the bridge's creation policy) and
# Exit-BridgeV2QueueMutex releases it. The legacy claim writers (Claim-AgentTask, Release-AgentTask,
# Invoke-StaleClaimSweep and the lease refresh) take it through Enter-BridgeQueueRootMutex in
# ClaimLeaseHeartbeat.ps1, this mutex FIRST, then the claim lock, as the Python queue (S2), and
# release it in Exit-BridgeQueueRootMutex. Off Windows that function takes nothing and returns
# $null, as tools/work_queue.py _root_mutex takes nothing there.

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

function Enter-BridgeV2QueueMutex {
    # Take the runtime-root queue mutex within TimeoutMs, or throw; a returned mutex is always held.
    # Abandoned ownership (a holder that ended inside a transaction) is released at once and refused,
    # as the Python NamedMutexPort does: nothing was read or changed, and the next attempt acquires
    # normally. A Windows mutex is owned by a thread: enter and exit on the same thread.
    param(
        [Parameter(Mandatory)] [string] $RuntimeRoot,
        [ValidateRange(1, 60000)] [int] $TimeoutMs = 4000
    )

    $name = Get-BridgeV2QueueMutexName -RuntimeRoot $RuntimeRoot
    . (Join-Path $PSScriptRoot 'BridgeNamedMutex.ps1')
    $mutex = New-BridgeNamedMutex -Name $name
    $acquired = $false
    $abandoned = $false
    try {
        try { $acquired = $mutex.WaitOne($TimeoutMs) }
        catch [System.Threading.AbandonedMutexException] { $acquired = $true; $abandoned = $true }
    } catch { $mutex.Dispose(); throw }
    if (-not $acquired) {
        $mutex.Dispose()
        throw ('runtime-root queue mutex busy: ' + $name)
    }
    if ($abandoned) {
        try { $mutex.ReleaseMutex() } finally { $mutex.Dispose() }
        throw ('runtime-root queue mutex was abandoned by a holder that ended inside a transaction; ' +
            'nothing was read or changed, retry: ' + $name)
    }
    return $mutex
}

function Exit-BridgeV2QueueMutex {
    # Release a mutex Enter-BridgeV2QueueMutex returned; the handle is always disposed.
    param([Parameter(Mandatory)] $Mutex)
    try { $Mutex.ReleaseMutex() } finally { $Mutex.Dispose() }
}
