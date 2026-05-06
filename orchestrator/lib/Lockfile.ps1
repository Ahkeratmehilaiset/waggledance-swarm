# Lockfile.ps1
# Atomic single-instance lock for the orchestrator.
#
# Phase 1.6 changes:
#   - Atomic create via FileMode.CreateNew (errors if file exists).
#   - Lock content includes a per-acquisition lock_id GUID; release only
#     succeeds if the lock_id matches.
#   - Two distinct override flags:
#       -ForceStaleLock              -> only valid when local pid is dead
#       -DangerouslyOverrideLiveLock -> overrides any lock; logs prominently
#
# Compatible with PowerShell 5.1.

Set-StrictMode -Version Latest

function Read-WaggleLock {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [string] $Path)
    if (-not (Test-Path $Path)) { return $null }
    try { return Get-Content -Raw -Path $Path | ConvertFrom-Json }
    catch { return $null }
}

function Test-LockHolderAlive {
    [CmdletBinding()]
    param([Parameter(Mandatory)] $Lock)

    if (-not $Lock) { return $false }
    $hasPid  = ($Lock.PSObject.Properties.Name -contains 'pid')
    $hasHost = ($Lock.PSObject.Properties.Name -contains 'hostname')
    if (-not ($hasPid -and $hasHost)) { return $false }

    if ($Lock.hostname -ne [System.Net.Dns]::GetHostName()) {
        return $true   # different host: be conservative, treat as alive
    }
    try {
        $proc = Get-Process -Id $Lock.pid -ErrorAction Stop
        return ($null -ne $proc)
    } catch {
        return $false
    }
}

function _WriteLockAtomic {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [string] $Path, [Parameter(Mandatory)] [string] $Json)

    $dir = Split-Path -Parent $Path
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }

    $fs = $null
    try {
        $fs = [System.IO.File]::Open(
            $Path,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None)
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Json)
        $fs.Write($bytes, 0, $bytes.Length)
    } finally {
        if ($fs) { $fs.Dispose() }
    }
}

function Acquire-WaggleLock {
    <#
    .SYNOPSIS
    Atomically write a lock file. Returns the lock object on success.
    Throws if a live lock exists, unless overridden.

    .PARAMETER ForceStaleLock
    Allow reclaiming a stale (dead local pid) lock. Cross-host locks are
    NOT considered stale by this flag.

    .PARAMETER DangerouslyOverrideLiveLock
    Override even a live lock. Use only if you are certain the holder is
    dead but appears alive (e.g., a hung process you killed externally).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $IterationId,
        [switch] $ForceStaleLock,
        [switch] $DangerouslyOverrideLiveLock
    )

    if (Test-Path $Path) {
        $existing = Read-WaggleLock -Path $Path
        $alive = $false
        if ($existing) { $alive = Test-LockHolderAlive -Lock $existing }

        if ($DangerouslyOverrideLiveLock) {
            Write-Warning "Overriding live lock at $Path (DangerouslyOverrideLiveLock). Existing: $($existing | ConvertTo-Json -Compress)"
            Remove-Item -Force $Path
        }
        elseif (-not $alive) {
            if ($ForceStaleLock) {
                Write-Warning "Reclaiming stale lock at $Path (pid=$($existing.pid))."
                Remove-Item -Force $Path
            } else {
                throw "Stale lock present at $Path. Re-run with -ForceStaleLock to reclaim."
            }
        }
        else {
            $compact = if ($existing) { ($existing | ConvertTo-Json -Compress) } else { '<unreadable>' }
            throw "Live orchestrator lock at $Path. Holder: $compact. Refuse to start."
        }
    }

    $lock = [pscustomobject]@{
        lock_id      = ([Guid]::NewGuid().ToString())
        pid          = $PID
        hostname     = [System.Net.Dns]::GetHostName()
        started_at   = (Get-Date).ToUniversalTime().ToString('o')
        iteration_id = $IterationId
    }
    $json = $lock | ConvertTo-Json
    try {
        _WriteLockAtomic -Path $Path -Json $json
    } catch {
        throw "Atomic lock create failed at $Path : $($_.Exception.Message)"
    }
    return $lock
}

function Release-WaggleLock {
    <#
    .SYNOPSIS
    Releases the lock at $Path only if its lock_id matches the supplied id.
    Without -LockId, the function refuses to delete (safer default).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $Path,
        [string] $LockId = ''
    )
    if (-not (Test-Path $Path)) { return $false }
    $current = Read-WaggleLock -Path $Path
    if (-not $current) {
        Write-Warning "Lock file present but unparsable at $Path. Not removing."
        return $false
    }
    if (-not $LockId) {
        Write-Warning "Release-WaggleLock called without LockId. Refusing to remove $Path."
        return $false
    }
    if ($current.lock_id -ne $LockId) {
        Write-Warning "LockId mismatch: own=$LockId, on disk=$($current.lock_id). Not removing."
        return $false
    }
    Remove-Item -Force $Path
    return $true
}
