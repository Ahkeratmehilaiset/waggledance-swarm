#requires -Version 5.1
<#
.SYNOPSIS
    B7: the single shared claim-lease / session-heartbeat helper.

.DESCRIPTION
    Dot-source this file; it defines functions and performs no work on
    load. Every writer that keeps a claim lease alive MUST go through
    Update-BridgeClaimLease here rather than editing claim JSON itself,
    so there is exactly one compare-and-swap implementation to review.

    Design (strongest-Grok focused review, 2026-08-26, approved revision):

    * Identity is owner_session_id PLUS owner_token_sha256. Never the
      agent name, run id, or pid alone: agent names are shared across
      sessions, run ids are guessable and appear in plaintext events, and
      pids are recycled by the OS. The raw owner token lives only in the
      session process environment and is never written to disk - claims
      and heartbeats carry its SHA-256 only.
    * There is no fallback identity. A caller without a resolvable
      owner identity cannot extend any lease; it does not silently
      degrade to "same agent name is good enough".
    * Every claim mutation (keepalive, sweep, release) takes the same
      per-claim sibling lock, then RE-READS the claim under that lock
      before deciding - the compare half of compare-and-swap.
    * Writes use [System.IO.File]::Replace, which requires the target to
      exist. A claim archived by a concurrent sweep can therefore never
      be recreated by an in-flight keepalive, and a successor claim is
      never overwritten because the identity recheck happens after the
      re-read.
    * Session heartbeats are TTL-bound and identity-bound, and are
      retired by Stop-AgentBridgeSession.ps1. Absent, unreadable,
      malformed, expired, or mismatched heartbeats all evaluate to "not
      live", so the sweeper's pre-B7 behavior is the fail-closed default.
#>

Set-StrictMode -Version Latest

$script:BridgeOwnerTokenEnvName = 'AGENT_BRIDGE_OWNER_TOKEN'
$script:BridgeSessionHeartbeatTtlDefault = 180
$script:BridgeSessionHeartbeatTtlMax = 900
$script:BridgeClaimLockTimeoutMs = 4000

function Get-BridgeSafeName {
    param([Parameter(Mandatory)] [AllowEmptyString()] [string] $Name)
    return (($Name -replace '[^A-Za-z0-9._-]', '_').Trim('_'))
}

function Get-BridgeSha256Hex {
    param([Parameter(Mandatory)] [AllowEmptyString()] [string] $Value)

    if (-not $Value) { return '' }
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
        return ([System.BitConverter]::ToString($sha.ComputeHash($bytes)) `
            -replace '-', '').ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function New-BridgeOwnerToken {
    <# 256 bits of CSPRNG entropy; caller exports it, never persists it. #>
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return ([System.BitConverter]::ToString($bytes) -replace '-', '').ToLowerInvariant()
}

function Get-BridgeOwnerIdentity {
    <#
        Resolve THIS process's owner identity, or $null when it cannot be
        established. $null means "cannot extend a lease" - callers must
        not invent a weaker identity to keep working.
    #>
    param(
        [string] $SessionId = '',
        [string] $OwnerToken = ''
    )

    $sessionId = if ($SessionId) {
        $SessionId
    } elseif ($env:AGENT_BRIDGE_RUN_ID) {
        [string]$env:AGENT_BRIDGE_RUN_ID
    } else {
        ''
    }
    # Read the env var directly and guard the unset case: under
    # Set-StrictMode, dereferencing .Value on a missing Env: item throws
    # rather than yielding $null, which would turn "no identity" into a
    # crash in every caller.
    $token = if ($OwnerToken) {
        $OwnerToken
    } elseif ($env:AGENT_BRIDGE_OWNER_TOKEN) {
        [string]$env:AGENT_BRIDGE_OWNER_TOKEN
    } else {
        ''
    }
    if (-not $sessionId -or -not $token) { return $null }
    return [pscustomobject]@{
        owner_session_id   = $sessionId
        owner_token_sha256 = (Get-BridgeSha256Hex -Value $token)
    }
}

function Get-BridgeClaimsDir {
    param([Parameter(Mandatory)] [string] $Root)
    return (Join-Path (Join-Path $Root 'work_queue') 'claims')
}

function Get-BridgeHeartbeatsDir {
    param([Parameter(Mandatory)] [string] $Root)
    return (Join-Path (Join-Path $Root 'work_queue') 'heartbeats')
}

function Get-BridgeSessionHeartbeatPath {
    <#
        Canonical, collision-resistant artifact path for one session.

        The filename is the SHA-256 of the session id, NOT a sanitized
        form of it: sanitizing is lossy, so two distinct sessions such as
        'wd/alpha' and 'wd_alpha' would map to the same file and could
        overwrite or delete each other's liveness proof.
    #>
    param(
        [Parameter(Mandatory)] [string] $Root,
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $SessionId
    )

    if (-not $SessionId) { return '' }
    $digest = Get-BridgeSha256Hex -Value $SessionId
    if (-not $digest) { return '' }
    return (Join-Path (Get-BridgeHeartbeatsDir -Root $Root) ($digest + '.json'))
}

function Enter-BridgeClaimLock {
    <#
        Exclusive sibling lock for one claim file. Returns a FileStream
        to pass to Exit-BridgeClaimLock, or $null when the lock could not
        be taken within the timeout - callers then skip that claim this
        round rather than racing it.
    #>
    param(
        [Parameter(Mandatory)] [string] $ClaimPath,
        [int] $TimeoutMs = 0
    )

    if ($TimeoutMs -le 0) { $TimeoutMs = $script:BridgeClaimLockTimeoutMs }
    $lockPath = "$ClaimPath.lock"
    $deadline = (Get-Date).AddMilliseconds($TimeoutMs)
    while ($true) {
        try {
            return (New-Object System.IO.FileStream(
                $lockPath,
                [System.IO.FileMode]::OpenOrCreate,
                [System.IO.FileAccess]::ReadWrite,
                [System.IO.FileShare]::None))
        } catch {
            if ((Get-Date) -ge $deadline) { return $null }
            Start-Sleep -Milliseconds 25
        }
    }
}

function Exit-BridgeClaimLock {
    param($Lock)

    if ($null -eq $Lock) { return }
    $lockName = $null
    try { $lockName = $Lock.Name } catch {}
    try { $Lock.Dispose() } catch {}
    if ($lockName) {
        try { Remove-Item -LiteralPath $lockName -Force -ErrorAction SilentlyContinue } catch {}
    }
}

function ConvertTo-BridgeIsoTimestamps {
    <#
        PowerShell's ConvertFrom-Json turns ISO-8601 strings into
        [DateTime] objects. Re-serializing those depends on the host's
        culture and JSON date handling, which is how a claim file can
        come back holding something like '08/26/2026 09:04:40' that no
        invariant parser accepts. Normalize every DateTime-valued
        property back to a round-trip 'o' string before writing, so the
        on-disk shape never depends on the writer's locale.
    #>
    param([Parameter(Mandatory)] $Object)

    foreach ($property in @($Object.PSObject.Properties)) {
        if ($property.Value -is [DateTime]) {
            $property.Value = ([DateTime]$property.Value).ToUniversalTime().ToString('o')
        }
    }
    return $Object
}

function Test-BridgeClaimOwner {
    <#
        Identity half of the compare-and-swap. Both fields must be
        present on the claim AND match the caller. A claim that predates
        B7 (no owner fields) is deliberately NOT ownable: it keeps its
        original lease and ages out normally instead of being adopted by
        whoever asks first.
    #>
    param(
        [Parameter(Mandatory)] $Claim,
        $Identity
    )

    if ($null -eq $Identity) { return $false }
    if (-not $Claim.PSObject.Properties['owner_session_id']) { return $false }
    if (-not $Claim.PSObject.Properties['owner_token_sha256']) { return $false }
    $claimSession = [string]$Claim.owner_session_id
    $claimToken = [string]$Claim.owner_token_sha256
    if (-not $claimSession -or -not $claimToken) { return $false }
    return ($claimSession -ceq [string]$Identity.owner_session_id -and
            $claimToken -ceq [string]$Identity.owner_token_sha256)
}

function Update-BridgeClaimLease {
    <#
        THE claim keepalive. Extends the lease of every claim owned by
        this exact session, and nothing else.

        Returns the number of claims actually extended. Callers treat a
        zero as informational, never as a reason to weaken identity.
    #>
    param(
        [Parameter(Mandatory)] [string] $Root,
        [Parameter(Mandatory)] [string] $AgentName,
        $Identity = $null
    )

    if ($null -eq $Identity) { $Identity = Get-BridgeOwnerIdentity }
    if ($null -eq $Identity) { return 0 }

    $claimsDir = Get-BridgeClaimsDir -Root $Root
    if (-not (Test-Path -LiteralPath $claimsDir -PathType Container)) { return 0 }

    $updated = 0
    $encoding = New-Object System.Text.UTF8Encoding($false)
    foreach ($file in @(Get-ChildItem -LiteralPath $claimsDir -Filter '*.json' `
                -File -ErrorAction SilentlyContinue)) {
        $lock = Enter-BridgeClaimLock -ClaimPath $file.FullName
        if ($null -eq $lock) { continue }
        try {
            # Re-read UNDER the lock: the compare half. Anything decided
            # from a pre-lock read could already be stale.
            if (-not (Test-Path -LiteralPath $file.FullName -PathType Leaf)) { continue }
            try {
                $claim = Get-Content -Raw -LiteralPath $file.FullName -Encoding UTF8 |
                    ConvertFrom-Json -ErrorAction Stop
            } catch { continue }
            if (-not $claim.PSObject.Properties['agent']) { continue }
            if ([string]$claim.agent -ne $AgentName) { continue }
            if (-not (Test-BridgeClaimOwner -Claim $claim -Identity $Identity)) { continue }

            $nowUtc = (Get-Date).ToUniversalTime()
            $claim | Add-Member -NotePropertyName last_heartbeat_utc `
                -NotePropertyValue $nowUtc.ToString('o') -Force
            $leaseSeconds = 0
            if ($claim.PSObject.Properties['lease_seconds'] -and
                [int]::TryParse([string]$claim.lease_seconds, [ref]$leaseSeconds) -and
                $leaseSeconds -gt 0) {
                $claim | Add-Member -NotePropertyName claim_lease_expires_utc `
                    -NotePropertyValue $nowUtc.AddSeconds($leaseSeconds).ToString('o') `
                    -Force
            }

            $tmp = "$($file.FullName).tmp.$PID.$([guid]::NewGuid().ToString('N'))"
            # A real backup path, not $null: PowerShell marshals $null to
            # an empty string and File.Replace rejects that outright.
            $backup = "$($file.FullName).bak.$PID.$([guid]::NewGuid().ToString('N'))"
            try {
                [System.IO.File]::WriteAllText(
                    $tmp,
                    ((ConvertTo-BridgeIsoTimestamps -Object $claim) |
                        ConvertTo-Json -Depth 8),
                    $encoding)
                # Replace REQUIRES the destination to exist: a claim the
                # sweeper archived meanwhile is never recreated, and no
                # successor file is clobbered because the identity
                # recheck above ran on the current content.
                [System.IO.File]::Replace($tmp, $file.FullName, $backup)
                $updated++
            } catch [System.IO.FileNotFoundException] {
                # Designed path: the claim was archived while we worked.
                # Stay silent and, above all, do not recreate it.
            } catch {
                # Anything else is unexpected and must not be silent, or a
                # lease could stop being extended with no signal at all -
                # exactly the failure mode B7 exists to remove.
                Write-Warning ("claim lease bump failed for {0}: {1}" -f `
                    $file.Name, $_.Exception.Message)
            } finally {
                try { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue } catch {}
                try { Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue } catch {}
            }
        } finally {
            Exit-BridgeClaimLock -Lock $lock
        }
    }
    return $updated
}

function Write-BridgeSessionHeartbeat {
    <#
        Durable, TTL-bound, identity-bound proof that a session is alive.
        The sweeper reads this to distinguish "agent is working" from
        "claim leaked". Written atomically and independently of any
        bridge event emit.
    #>
    param(
        [Parameter(Mandatory)] [string] $Root,
        [Parameter(Mandatory)] [string] $AgentName,
        $Identity = $null,
        [int] $TtlSeconds = 0
    )

    if ($null -eq $Identity) { $Identity = Get-BridgeOwnerIdentity }
    if ($null -eq $Identity) { return $false }
    if ($TtlSeconds -le 0) { $TtlSeconds = $script:BridgeSessionHeartbeatTtlDefault }
    if ($TtlSeconds -gt $script:BridgeSessionHeartbeatTtlMax) {
        $TtlSeconds = $script:BridgeSessionHeartbeatTtlMax
    }

    $dir = Get-BridgeHeartbeatsDir -Root $Root
    if (-not (Test-Path -LiteralPath $dir -PathType Container)) {
        try {
            [void](New-Item -ItemType Directory -Path $dir -Force -ErrorAction Stop)
        } catch { return $false }
    }
    $path = Get-BridgeSessionHeartbeatPath -Root $Root `
        -SessionId ([string]$Identity.owner_session_id)
    if (-not $path) { return $false }

    $lock = Enter-BridgeClaimLock -ClaimPath $path
    if ($null -eq $lock) { return $false }
    try {
        # Identity recheck under the lock: never overwrite an artifact
        # belonging to a different session or token.
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            $existing = $null
            try {
                $existing = Get-Content -Raw -LiteralPath $path -Encoding UTF8 |
                    ConvertFrom-Json -ErrorAction Stop
            } catch { $existing = $null }
            if ($null -ne $existing -and
                $existing.PSObject.Properties['owner_session_id'] -and
                $existing.PSObject.Properties['owner_token_sha256']) {
                if (([string]$existing.owner_session_id -cne [string]$Identity.owner_session_id) -or
                    ([string]$existing.owner_token_sha256 -cne [string]$Identity.owner_token_sha256)) {
                    return $false
                }
            }
        }

        $payload = [ordered]@{
            agent              = $AgentName
            owner_session_id   = [string]$Identity.owner_session_id
            owner_token_sha256 = [string]$Identity.owner_token_sha256
            last_beat_utc      = (Get-Date).ToUniversalTime().ToString('o')
            ttl_seconds        = $TtlSeconds
        }
        $tmp = "$path.tmp.$PID.$([guid]::NewGuid().ToString('N'))"
        $backup = "$path.bak.$PID.$([guid]::NewGuid().ToString('N'))"
        $encoding = New-Object System.Text.UTF8Encoding($false)
        try {
            [System.IO.File]::WriteAllText(
                $tmp, ($payload | ConvertTo-Json -Depth 4), $encoding)
            if (Test-Path -LiteralPath $path -PathType Leaf) {
                [System.IO.File]::Replace($tmp, $path, $backup)
            } else {
                [System.IO.File]::Move($tmp, $path)
            }
            return $true
        } catch {
            Write-Warning ("session heartbeat write failed: {0}" -f $_.Exception.Message)
            return $false
        } finally {
            try { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue } catch {}
            try { Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue } catch {}
        }
    } finally {
        Exit-BridgeClaimLock -Lock $lock
    }
}

function Remove-BridgeSessionHeartbeat {
    <# Retire the artifact on session stop so a dead session stops
       protecting its claims immediately, without waiting out the TTL. #>
    param(
        [Parameter(Mandatory)] [string] $Root,
        [string] $SessionId = '',
        $Identity = $null
    )

    if ($null -eq $Identity) { $Identity = Get-BridgeOwnerIdentity -SessionId $SessionId }
    if ($null -eq $Identity) { return $false }
    $path = Get-BridgeSessionHeartbeatPath -Root $Root `
        -SessionId ([string]$Identity.owner_session_id)
    if (-not $path) { return $false }

    $lock = Enter-BridgeClaimLock -ClaimPath $path
    if ($null -eq $lock) { return $false }
    try {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $false }
        $existing = $null
        try {
            $existing = Get-Content -Raw -LiteralPath $path -Encoding UTF8 |
                ConvertFrom-Json -ErrorAction Stop
        } catch { $existing = $null }
        # Exact identity match required: stopping one session must never
        # delete a successor session's liveness proof and thereby hand
        # its live claims to the sweeper.
        if ($null -eq $existing -or
            -not $existing.PSObject.Properties['owner_session_id'] -or
            -not $existing.PSObject.Properties['owner_token_sha256'] -or
            ([string]$existing.owner_session_id -cne [string]$Identity.owner_session_id) -or
            ([string]$existing.owner_token_sha256 -cne [string]$Identity.owner_token_sha256)) {
            return $false
        }
        try {
            Remove-Item -LiteralPath $path -Force -ErrorAction Stop
            return $true
        } catch {
            return $false
        }
    } finally {
        Exit-BridgeClaimLock -Lock $lock
    }
}

function Test-BridgeSessionHeartbeatLive {
    <#
        Is the session that owns this claim still beating?

        Fail-closed in every ambiguous direction: no owner fields on the
        claim, no artifact, unreadable artifact, a missing or malformed
        timestamp, identity mismatch, or a beat older than the recorded
        TTL all return $false, which means the sweeper behaves exactly as
        it did before B7.
    #>
    param(
        [Parameter(Mandatory)] [string] $Root,
        [Parameter(Mandatory)] $Claim,
        [Parameter(Mandatory)] [DateTime] $NowUtc,
        [int] $MaxTtlSeconds = 0
    )

    if ($MaxTtlSeconds -le 0) { $MaxTtlSeconds = $script:BridgeSessionHeartbeatTtlMax }
    if (-not $Claim.PSObject.Properties['owner_session_id']) { return $false }
    if (-not $Claim.PSObject.Properties['owner_token_sha256']) { return $false }
    $claimSession = [string]$Claim.owner_session_id
    $claimToken = [string]$Claim.owner_token_sha256
    if (-not $claimSession -or -not $claimToken) { return $false }

    $path = Get-BridgeSessionHeartbeatPath -Root $Root -SessionId $claimSession
    if (-not $path) { return $false }
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $false }
    try {
        $beat = Get-Content -Raw -LiteralPath $path -Encoding UTF8 |
            ConvertFrom-Json -ErrorAction Stop
    } catch { return $false }

    foreach ($field in @('owner_session_id', 'owner_token_sha256', 'last_beat_utc')) {
        if (-not $beat.PSObject.Properties[$field]) { return $false }
    }
    if ([string]$beat.owner_session_id -cne $claimSession) { return $false }
    if ([string]$beat.owner_token_sha256 -cne $claimToken) { return $false }

    $ttl = $script:BridgeSessionHeartbeatTtlDefault
    if ($beat.PSObject.Properties['ttl_seconds']) {
        $parsedTtl = 0
        if ([int]::TryParse([string]$beat.ttl_seconds, [ref]$parsedTtl) -and $parsedTtl -gt 0) {
            $ttl = $parsedTtl
        }
    }
    # A heartbeat cannot buy itself unlimited protection by declaring a
    # huge TTL.
    if ($ttl -gt $MaxTtlSeconds) { $ttl = $MaxTtlSeconds }

    $beatUtc = $null
    try {
        $beatUtc = [DateTime]::Parse(
            [string]$beat.last_beat_utc,
            [System.Globalization.CultureInfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::AssumeUniversal -bor
                [System.Globalization.DateTimeStyles]::AdjustToUniversal
        ).ToUniversalTime()
    } catch { return $false }
    if ($null -eq $beatUtc) { return $false }
    # A future-dated beat is treated as not live: clock skew must not be
    # a way to pin a claim open forever.
    if ($beatUtc -gt $NowUtc.AddSeconds(60)) { return $false }
    return ((($NowUtc - $beatUtc).TotalSeconds) -le $ttl)
}
