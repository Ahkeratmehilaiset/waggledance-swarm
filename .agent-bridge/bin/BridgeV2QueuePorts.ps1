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
    lock "<claim>.json.lock" (FileMode OpenOrCreate, FileAccess ReadWrite, FileShare None),
    then the body. Each lock has its OWN budget (-TimeoutMs for the mutex, -ClaimLockTimeoutMs
    for the claim lock, default the same value) measured on a monotonic Stopwatch; the claim
    lock is retried every 25 ms ONLY on a recognized sharing/lock violation, and any other
    open failure is refused at once with its real type (Tools 8c6066ff F8-WALL-CLOCK).
    Cleanup (F8-CLEANUP): every Dispose/ReleaseMutex is attempted independently; a primary
    claim-lock or body error is rethrown unchanged, with the bounded secondary cleanup
    diagnostic in Exception.Data['bridge_cleanup'] and a warning that can never throw, even
    under WarningPreference/-WarningAction Stop (Tools 51ada W-PRIMARY-WARNING); after a clean
    body any cleanup failure throws: no success on failed cleanup. A factory that throws or
    does not return exactly one mutex is refused before any wait, disposing every returned object.
    Existing-object ACL (F8-DACL-INHERITED): New-BridgeNamedMutex only PRINTS an
    existing-object DACL diagnostic; that is not an admission gate. -AclInspector must return
    exactly 'match' for the created/opened mutex before anything waits on it; anything else
    refuses. No reviewed inspector seam exists yet, so without -AclInspector (a fixture seam,
    never a trust assertion) the call refuses and the adapter stays dormant.
    Claim containment (F8-ROOT-CLAIM): lexical only. The claim must be
    <root>/work_queue/claims/<name>.json, its components pass the same alias and reparse walk
    as the root, and ':' (an alternate stream) is refused. The sibling lock leaf is walked again
    (every existing component, the leaf included; not a directory) right before its open, inside
    the mutex hold (W-LOCK-PATH). No physical alias or open-race claim, and no legacy-writer fence.
    The Python FileClaimLock is owned by the queue module (RCO2), not by this twin.
    Off Windows there is no fallback lock: without an injected -MutexFactory the call refuses.
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

function Invoke-BridgeV2QueueCleanup {
    # Attempts EVERY cleanup action independently; returns the bounded failure text ('' = all ok).
    param([Parameter(Mandatory)] [AllowEmptyCollection()] [object[]] $Actions)
    $failures = [Collections.Generic.List[string]]::new()
    foreach ($action in $Actions) {
        try { & $action } catch { $failures.Add($_.Exception.GetType().Name + ': ' + $_.Exception.Message) }
    }
    $text = $failures -join '; '
    if ($text.Length -gt 300) { $text = $text.Substring(0, 300) }
    return $text
}

function Add-BridgeV2QueueCleanupDiagnostic {
    # Keeps the primary error unchanged; the secondary cleanup failure is visible but bounded, and
    # emitting it can NEVER throw: -WarningAction Continue overrides a caller's WarningPreference
    # or -WarningAction Stop, and each step is guarded (Tools 51ada W-PRIMARY-WARNING).
    param($ErrorRecord, [string] $Cleanup)
    if (-not $Cleanup) { return }
    try { $ErrorRecord.Exception.Data['bridge_cleanup'] = $Cleanup } catch { }
    try { Write-Warning ('bridge v2 queue lock cleanup also failed: ' + $Cleanup) -WarningAction Continue } catch { }
}

function Assert-BridgeV2QueueLockLeaf {
    # The sibling "<claim>.json.lock" is its own filesystem object: every EXISTING component of
    # its path, the leaf included, must pass the same alias/reparse walk as the root, and the leaf
    # must not be a directory. Called right before the open, inside the mutex hold. A swap between
    # this check and the open stays a residual race: there is no physical-alias or open-race
    # guarantee and no fencing of legacy writers (Tools 51ada W-LOCK-PATH).
    param([Parameter(Mandatory)] [string] $LockPath)
    [void](ConvertTo-BridgeV2QueueRootIdentity -RuntimeRoot $LockPath)
    if (Test-Path -LiteralPath $LockPath -PathType Container) { throw 'the claim lock path is a directory' }
}

function Test-BridgeV2LockContention {
    # Only a sharing or lock violation is contention worth retrying (Win32 32/33 in the HResult).
    param($Exception)
    $current = $Exception
    while ($null -ne $current) {
        if ($current -is [IO.IOException] -and (($current.HResult -band 0xFFFF) -in @(32, 33))) { return $true }
        $current = $current.InnerException
    }
    return $false
}

function Assert-BridgeV2QueueClaimPath {
    # Lexical containment only (no physical alias or open-race claim): <root>/work_queue/claims/<name>.json.
    param([Parameter(Mandatory)] [string] $RuntimeRoot, [Parameter(Mandatory)] [string] $ClaimPath)
    if ($RuntimeRoot.Substring([Math]::Min(2, $RuntimeRoot.Length)).Contains(':') -or
        $ClaimPath.Substring([Math]::Min(2, $ClaimPath.Length)).Contains(':')) {
        throw 'resource traversal or alternate stream is forbidden'
    }
    $root = ConvertTo-BridgeV2QueueRootIdentity -RuntimeRoot $RuntimeRoot
    $claim = ConvertTo-BridgeV2QueueRootIdentity -RuntimeRoot $ClaimPath   # same alias and reparse walk
    $prefix = $root + '/work_queue/claims/'
    if (-not $claim.StartsWith($prefix, [StringComparison]::Ordinal) -or
        $claim.Substring($prefix.Length) -cnotmatch '^[a-z0-9._-]{1,200}\.json\z') {
        throw 'the claim must be <root>/work_queue/claims/<name>.json'
    }
}

function Invoke-BridgeV2QueueLocked {
    <#
        Runs ScriptBlock holding the runtime-root mutex and then the claim's legacy sibling
        lock. Throws, without running ScriptBlock, when either lock is busy past its budget,
        the mutex was abandoned, the claim lock cannot be opened, the platform has no named
        mutex, no existing-object ACL evidence matches, the claim is outside the root, or the
        claims directory is missing (nothing is created here).
    #>
    param(
        [Parameter(Mandatory)] [string] $RuntimeRoot,
        [Parameter(Mandatory)] [string] $ClaimPath,
        [Parameter(Mandatory)] [scriptblock] $ScriptBlock,
        [int] $TimeoutMs = 4000,
        [int] $ClaimLockTimeoutMs = 0,
        [scriptblock] $MutexFactory = $null,
        [scriptblock] $AclInspector = $null
    )
    if ($TimeoutMs -lt 1 -or $TimeoutMs -gt 60000) { throw 'TimeoutMs must be within 1..60000' }
    if ($ClaimLockTimeoutMs -eq 0) { $ClaimLockTimeoutMs = $TimeoutMs }   # same value, separate budget
    if ($ClaimLockTimeoutMs -lt 1 -or $ClaimLockTimeoutMs -gt 60000) { throw 'ClaimLockTimeoutMs must be within 1..60000' }
    $name = Get-BridgeV2QueueMutexName -RuntimeRoot $RuntimeRoot
    Assert-BridgeV2QueueClaimPath -RuntimeRoot $RuntimeRoot -ClaimPath $ClaimPath
    if ($null -eq $MutexFactory -and [Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'a Windows named mutex is required; there is no fallback lock'
    }
    if ($null -eq $AclInspector) {
        throw 'existing-object ACL evidence unavailable: no reviewed inspector seam, the F8 adapter stays refused'
    }
    if ($null -eq $MutexFactory) {
        . (Join-Path $PSScriptRoot 'BridgeNamedMutex.ps1')   # the bridge DACL creation policy
        $MutexFactory = { param($MutexName) New-BridgeNamedMutex -Name $MutexName }
    }
    $lockPath = Get-BridgeV2ClaimLockPath -ClaimPath $ClaimPath
    if (-not (Test-Path -LiteralPath (Split-Path -Parent $lockPath) -PathType Container)) {
        throw 'the claims directory is missing; nothing is bootstrapped here'
    }
    try { $created = @(& $MutexFactory $name) }
    catch { throw ('runtime-root mutex create/open refused: ' + $_.Exception.GetType().Name) }
    if ($created.Count -ne 1 -or $null -eq $created[0]) {
        # Exactly one mutex object, or nothing is waited on; EVERY returned object is disposed.
        $failures = [Collections.Generic.List[string]]::new()
        foreach ($item in $created) {
            if ($null -ne $item -and $item.PSObject.Methods['Dispose']) {
                try { $item.Dispose() } catch { $failures.Add($_.Exception.GetType().Name) }
            }
        }
        throw ('runtime-root mutex create/open refused: the factory must return exactly one mutex' +
            $(if ($failures.Count) { '; cleanup also failed: ' + ($failures -join ', ') } else { '' }))
    }
    $mutex = $created[0]
    try { $verdict = & $AclInspector $name $mutex } catch { $verdict = 'unknown: inspector threw ' + $_.Exception.GetType().Name }
    if (-not ($verdict -is [string] -and $verdict -ceq 'match')) {
        $cleanup = Invoke-BridgeV2QueueCleanup -Actions @({ $mutex.Dispose() })
        throw ('existing-object ACL evidence is not a match' + $(if ($cleanup) { '; cleanup also failed: ' + $cleanup } else { '' }))
    }
    $primary = $null
    $owned = $false
    $lock = $null
    try {
        try { $owned = [bool]$mutex.WaitOne($TimeoutMs) }
        catch {
            # A method call can wrap the exception; look through InnerException for the abandon.
            $inner = $_.Exception
            while ($null -ne $inner -and $inner -isnot [System.Threading.AbandonedMutexException]) {
                $inner = $inner.InnerException
            }
            if ($null -eq $inner) { throw }
            # An abandoned mutex IS owned now: give it back without running the body.
            $cleanup = Invoke-BridgeV2QueueCleanup -Actions @({ $mutex.ReleaseMutex() })
            throw ('the previous holder died inside a queue transaction; reconcile the WAL first' +
                $(if ($cleanup) { '; ReleaseMutex also failed' } else { '' }))
        }
        if (-not $owned) { throw 'runtime-root mutex busy: bounded wait expired, nothing mutated' }
        try {
            Assert-BridgeV2QueueLockLeaf -LockPath $lockPath   # the leaf and its ancestors, right before the open
            $clock = [Diagnostics.Stopwatch]::StartNew()   # monotonic, the claim lock's own budget
            while ($null -eq $lock) {
                try {
                    $lock = New-Object System.IO.FileStream($lockPath, [IO.FileMode]::OpenOrCreate,
                        [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
                } catch {
                    if (-not (Test-BridgeV2LockContention $_.Exception)) {
                        $cause = $_.Exception
                        while ($null -ne $cause.InnerException -and $cause -is [Management.Automation.MethodInvocationException]) { $cause = $cause.InnerException }
                        throw ('claim lock open refused: ' + $cause.GetType().Name + ', nothing mutated')
                    }
                    if ($clock.ElapsedMilliseconds -ge $ClaimLockTimeoutMs) { throw 'claim lock busy: bounded wait expired, nothing mutated' }
                    Start-Sleep -Milliseconds 25
                }
            }
            & $ScriptBlock
        } catch {
            $primary = $_
        }
    } catch {
        $primary = $_
    } finally {
        $actions = @()
        if ($null -ne $lock) { $actions += { $lock.Dispose() } }
        if ($owned) { $actions += { $mutex.ReleaseMutex() } }
        $actions += { $mutex.Dispose() }
        $cleanup = Invoke-BridgeV2QueueCleanup -Actions $actions
    }
    if ($null -ne $primary) {
        Add-BridgeV2QueueCleanupDiagnostic -ErrorRecord $primary -Cleanup $cleanup
        throw $primary
    }
    if ($cleanup) { throw ('cleanup failed after a clean body (' + $cleanup + '): a failed ReleaseMutex stays owned until this thread exits') }
}
