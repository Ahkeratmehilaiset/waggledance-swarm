#requires -Version 5.1
<#
    Bridge v2 F8: PowerShell twin of tools/bridge_v2_queue_transactions.py (runtime-root
    identity, mutex name, lock order) and tools/bridge_v2_windows_ports.py (named-mutex
    lifecycle).

    Library only, explicit opt-in, OFF by default: nothing dot-sources it, it defines
    functions and no variables, reads no environment, bootstraps nothing and edits no
    existing writer. Legacy writers do not take this mutex: no mixed-generation safety.

    Identity (byte-identical to Python canonical_root/mutex_name): backslashes become '/',
    a segment ending in '.' or ' ' or holding '~<digit>' is refused, the root must be a
    local drive-letter path (X:/), '..' is refused, every existing component is checked
    for a reparse point, the text is drive + '/' + parts joined by '/', trailing '/'
    removed, it must be ASCII and is lowered; the identity is the lowercase SHA-256 hex of
    those ASCII bytes and the mutex name is Global\WaggleDanceBridgeV2Queue-<first 32 hex>.

    Lock order and lifecycle (as Python): the runtime-root mutex FIRST (bounded WaitOne;
    false = busy, nothing mutated; AbandonedMutexException = previous holder died: release
    without running the body and ask for WAL reconciliation), THEN the exact legacy sibling
    lock "<claim>.json.lock" (FileMode OpenOrCreate, FileAccess ReadWrite, FileShare None,
    retried every 25 ms until the timeout, as Enter-BridgeClaimLock), then the body; release
    in reverse order in finally. Off Windows there is no fallback lock: without an injected
    -MutexFactory (a fixture seam) the call refuses.
    Not runtime-tested: written under the operator's no-runs directive (2026-09-29).
#>

function Get-BridgeV2QueueMutexPrefix { 'Global\WaggleDanceBridgeV2Queue-' }

function ConvertTo-BridgeV2QueueRootIdentity {
    param([Parameter(Mandatory)] [string] $RuntimeRoot)
    $text = $RuntimeRoot.Replace('\', '/')
    foreach ($segment in $text.Split('/')) {
        if ($segment -and $segment -ne '.' -and ($segment -match '[. ]$' -or $segment -match '~[0-9]')) {
            throw 'ambiguous Windows root alias'
        }
    }
    if ($text -cnotmatch '^[A-Za-z]:/') { throw 'a root must be a local drive-letter path' }
    $drive = $text.Substring(0, 2)
    $parts = @($text.Substring(2).Split('/') | Where-Object { $_ -and $_ -ne '.' })
    if ($parts -contains '..') { throw 'resource traversal or alternate stream is forbidden' }
    $walked = $drive
    foreach ($part in $parts) {
        $walked = $walked + '/' + $part
        # As Python's lstat walk: only "not found" is skipped; any other error propagates.
        try { $item = Get-Item -LiteralPath $walked -Force -ErrorAction Stop }
        catch [System.Management.Automation.ItemNotFoundException] { $item = $null }
        if ($null -ne $item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'resource path contains a link/reparse point'
        }
    }
    $normalized = ($drive + '/' + ($parts -join '/')).TrimEnd('/')
    foreach ($char in $normalized.ToCharArray()) {
        if ([int]$char -gt 127) { throw 'scope must be ASCII: both runtimes must normalize it identically' }
    }
    return $normalized.ToLowerInvariant()   # ASCII only here, so exactly Python's str.lower()
}

function Get-BridgeV2QueueRootHash {
    param([Parameter(Mandatory)] [string] $RuntimeRoot)
    $bytes = [Text.Encoding]::ASCII.GetBytes((ConvertTo-BridgeV2QueueRootIdentity -RuntimeRoot $RuntimeRoot))
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return (([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '')).ToLowerInvariant() }
    finally { $sha.Dispose() }
}

function Get-BridgeV2QueueMutexName {
    param([Parameter(Mandatory)] [string] $RuntimeRoot)
    return (Get-BridgeV2QueueMutexPrefix) + (Get-BridgeV2QueueRootHash -RuntimeRoot $RuntimeRoot).Substring(0, 32)
}

function Get-BridgeV2ClaimLockPath {
    param([Parameter(Mandatory)] [string] $ClaimPath)
    return "$ClaimPath.lock"   # the exact Enter-BridgeClaimLock spelling
}

function Invoke-BridgeV2QueueLocked {
    <#
        Runs ScriptBlock holding the runtime-root mutex and then the claim's legacy sibling
        lock. Throws, without running ScriptBlock, when either lock is busy past TimeoutMs,
        the mutex was abandoned, the platform has no named mutex, or the claims directory is
        missing (nothing is created here).
    #>
    param(
        [Parameter(Mandatory)] [string] $RuntimeRoot,
        [Parameter(Mandatory)] [string] $ClaimPath,
        [Parameter(Mandatory)] [scriptblock] $ScriptBlock,
        [int] $TimeoutMs = 4000,
        [scriptblock] $MutexFactory = $null
    )
    if ($TimeoutMs -lt 1 -or $TimeoutMs -gt 60000) { throw 'TimeoutMs must be within 1..60000' }
    $name = Get-BridgeV2QueueMutexName -RuntimeRoot $RuntimeRoot
    if ($null -eq $MutexFactory) {
        if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
            throw 'a Windows named mutex is required; there is no fallback lock'
        }
        . (Join-Path $PSScriptRoot 'BridgeNamedMutex.ps1')   # the bridge DACL creation policy
        $MutexFactory = { param($MutexName) New-BridgeNamedMutex -Name $MutexName }
    }
    $lockPath = Get-BridgeV2ClaimLockPath -ClaimPath $ClaimPath
    if (-not (Test-Path -LiteralPath (Split-Path -Parent $lockPath) -PathType Container)) {
        throw 'the claims directory is missing; nothing is bootstrapped here'
    }
    $mutex = & $MutexFactory $name
    if ($null -eq $mutex) { throw 'runtime-root mutex create/open refused' }
    try {
        try { $acquired = [bool]$mutex.WaitOne($TimeoutMs) }
        catch {
            # A method call can wrap the exception; look through InnerException for the abandon.
            $inner = $_.Exception
            while ($null -ne $inner -and $inner -isnot [System.Threading.AbandonedMutexException]) {
                $inner = $inner.InnerException
            }
            if ($null -eq $inner) { throw }
            try { $mutex.ReleaseMutex() } catch { }   # an abandoned mutex IS owned now: give it back
            throw 'the previous holder died inside a queue transaction; reconcile the WAL first'
        }
        if (-not $acquired) { throw 'runtime-root mutex busy: bounded wait expired, nothing mutated' }
        try {
            $deadline = (Get-Date).AddMilliseconds($TimeoutMs)
            $lock = $null
            while ($null -eq $lock) {
                try {
                    $lock = New-Object System.IO.FileStream($lockPath, [IO.FileMode]::OpenOrCreate,
                        [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
                } catch {
                    if ((Get-Date) -ge $deadline) { throw 'claim lock busy: bounded wait expired, nothing mutated' }
                    Start-Sleep -Milliseconds 25
                }
            }
            try { & $ScriptBlock } finally { $lock.Dispose() }
        } finally {
            $mutex.ReleaseMutex()
        }
    } finally {
        $mutex.Dispose()
    }
}
