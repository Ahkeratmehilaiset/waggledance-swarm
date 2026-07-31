#requires -Version 5.1

$script:AgentBridgeSessionIdentityContract = 'v1'
$script:AgentBridgeClaimOwnerContract = 'v1'

function Format-AgentBridgeIdentityDisplay {
    [CmdletBinding()]
    param(
        [AllowEmptyString()] [string] $Value
    )

    $builder = New-Object System.Text.StringBuilder
    foreach ($character in $Value.ToCharArray()) {
        $code = [int][char]$character
        if ($code -eq 0x5c) {
            [void]$builder.Append('\\')
            continue
        }
        if ($code -eq 0x27) {
            [void]$builder.Append("\'")
            continue
        }
        if ($code -eq 0x09) {
            [void]$builder.Append('\t')
            continue
        }
        if ($code -eq 0x0a) {
            [void]$builder.Append('\n')
            continue
        }
        if ($code -eq 0x0d) {
            [void]$builder.Append('\r')
            continue
        }
        if ([char]::IsControl([char]$character) -or
            $code -eq 0x0085 -or
            $code -eq 0x2028 -or
            $code -eq 0x2029) {
            [void]$builder.Append(('\u{0:X4}' -f $code))
            continue
        }
        [void]$builder.Append($character)
    }
    return $builder.ToString()
}

function Assert-AgentBridgeSessionIdentity {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $RequestedAgent,
        [switch] $AllowInternalStaleLeaseRelease
    )

    $boundAgent = [Environment]::GetEnvironmentVariable(
        'AGENT_BRIDGE_AGENT',
        'Process'
    )
    $agentPattern = '^[a-z][a-z0-9_-]{1,32}\z'
    if ([string]::IsNullOrWhiteSpace($RequestedAgent) -or
        $RequestedAgent -cnotmatch $agentPattern) {
        $requestedDisplay = Format-AgentBridgeIdentityDisplay -Value $RequestedAgent
        throw "identity_mismatch: requested agent '$requestedDisplay' is malformed"
    }
    if ([string]::IsNullOrWhiteSpace($boundAgent)) {
        if ($AllowInternalStaleLeaseRelease -and $RequestedAgent -ceq 'system') {
            return
        }
        if ($RequestedAgent -cin @('operator', 'system')) {
            throw (
                "identity_mismatch: reserved agent '{0}' requires a verified bound or internal caller" -f
                $RequestedAgent
            )
        }
        return
    }
    if ($boundAgent -cnotmatch $agentPattern) {
        $boundDisplay = Format-AgentBridgeIdentityDisplay -Value $boundAgent
        throw "identity_mismatch: AGENT_BRIDGE_AGENT '$boundDisplay' is malformed"
    }
    if ($AllowInternalStaleLeaseRelease -and $RequestedAgent -ceq 'system') {
        return
    }
    if ($RequestedAgent -ceq 'system') {
        throw 'identity_mismatch: system agent has no public bridge authority'
    }
    if ($boundAgent -cne $RequestedAgent) {
        throw (
            "identity_mismatch: session agent '{0}' cannot act as requested agent '{1}'" -f
            $boundAgent,
            $RequestedAgent
        )
    }
}

function ConvertFrom-AgentBridgeCanonicalUtc {
    [CmdletBinding()]
    param(
        [AllowNull()] [object] $Value
    )

    # PowerShell 7.6+ materializes canonical ISO JSON strings as DateTime
    # values during ConvertFrom-Json. Preserve compatibility with Windows
    # PowerShell (which leaves them as strings) while still rejecting
    # unspecified DateTime values that carry no emitted UTC/offset marker.
    if ($Value -is [DateTimeOffset]) {
        return ([DateTimeOffset]$Value).ToUniversalTime()
    }
    if ($Value -is [DateTime]) {
        $dateTimeValue = [DateTime]$Value
        if ($dateTimeValue.Kind -eq [DateTimeKind]::Unspecified) {
            return $null
        }
        return [DateTimeOffset]::new($dateTimeValue.ToUniversalTime())
    }
    if ($Value -isnot [string]) {
        return $null
    }

    $text = [string]$Value
    if ($text -cnotmatch (
            '^[0-9]{4}-[0-9]{2}-[0-9]{2}T' +
            '[0-9]{2}:[0-9]{2}:[0-9]{2}' +
            '(?:\.[0-9]{1,7})?' +
            '(?:Z|[+-][0-9]{2}:[0-9]{2})\z'
        )) {
        return $null
    }

    $formats = [string[]]@(
        "yyyy-MM-dd'T'HH:mm:ssK",
        "yyyy-MM-dd'T'HH:mm:ss.FFFFFFFK"
    )
    $parsed = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParseExact(
            $text,
            $formats,
            [System.Globalization.CultureInfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::None,
            [ref]$parsed
        )) {
        return $null
    }

    return $parsed.ToUniversalTime()
}

function ConvertFrom-AgentBridgeJson {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $Json
    )

    $parameters = @{}
    if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) {
        # Keep the exact wire text so security validation cannot be weakened
        # by PowerShell 7.6 auto-materializing noncanonical ISO-like strings.
        $parameters['DateKind'] = 'String'
    }
    return $Json | ConvertFrom-Json @parameters
}

function ConvertTo-BridgePositiveInt32 {
    [CmdletBinding()]
    param(
        [AllowNull()] [object] $Value
    )

    if ($null -eq $Value -or $Value -is [bool]) { return 0 }
    $text = ''
    if ($Value -is [string]) {
        $text = ([string]$Value).Trim()
        if ($text -cnotmatch '^[0-9]+\z') { return 0 }
    } elseif (
        $Value -is [byte] -or
        $Value -is [sbyte] -or
        $Value -is [int16] -or
        $Value -is [uint16] -or
        $Value -is [int32] -or
        $Value -is [uint32] -or
        $Value -is [int64]
    ) {
        $text = [Convert]::ToString(
            $Value,
            [System.Globalization.CultureInfo]::InvariantCulture
        )
    } else {
        return 0
    }

    $parsed = 0
    if (-not [int]::TryParse(
            $text,
            [System.Globalization.NumberStyles]::None,
            [System.Globalization.CultureInfo]::InvariantCulture,
            [ref]$parsed
        ) -or $parsed -le 0) {
        return 0
    }
    return $parsed
}

function Enter-AgentBridgeMutationLock {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $BridgeRoot,
        [int] $TimeoutMilliseconds = 30000
    )

    $workQueueDir = Join-Path $BridgeRoot 'work_queue'
    if (-not (Test-Path -LiteralPath $workQueueDir -PathType Container)) {
        [void](New-Item -ItemType Directory -Path $workQueueDir -Force `
            -ErrorAction Stop)
    }
    $lockPath = Join-Path $workQueueDir '.claims.mutation.lock'
    $stream = [System.IO.File]::Open(
        $lockPath,
        [System.IO.FileMode]::OpenOrCreate,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::ReadWrite
    )
    $deadline = [DateTime]::UtcNow.AddMilliseconds($TimeoutMilliseconds)
    try {
        while ($true) {
            try {
                $stream.Lock(0, 1)
                return $stream
            } catch [System.IO.IOException] {
                if ([DateTime]::UtcNow -ge $deadline) {
                    throw "timed out acquiring claim mutation lock: $lockPath"
                }
                Start-Sleep -Milliseconds 25
            }
        }
    } catch {
        $stream.Dispose()
        throw
    }
}

function Exit-AgentBridgeMutationLock {
    [CmdletBinding()]
    param(
        [AllowNull()] [System.IO.FileStream] $Lock
    )

    if ($null -eq $Lock) { return }
    try {
        $Lock.Unlock(0, 1)
    } finally {
        $Lock.Dispose()
    }
}

function Restore-AgentBridgeFileBackup {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $OriginalPath,
        [Parameter(Mandatory)] [string] $BackupPath
    )

    if (-not (Test-Path -LiteralPath $BackupPath -PathType Leaf)) {
        throw "claim backup is missing: $BackupPath"
    }
    if (-not (Test-Path -LiteralPath $OriginalPath -PathType Leaf)) {
        [System.IO.File]::Move($BackupPath, $OriginalPath)
        return
    }

    $displacedPath = (
        "$OriginalPath.rollback-displaced.$PID." +
        "$([guid]::NewGuid().ToString('N')).tmp"
    )
    try {
        # File.Replace requires a non-empty destination-backup path in pwsh.
        # Preserve the original backup on failure; on success it becomes the
        # active file and the released-state file moves to displacedPath.
        [System.IO.File]::Replace(
            $BackupPath,
            $OriginalPath,
            $displacedPath
        )
    } finally {
        if (Test-Path -LiteralPath $displacedPath -PathType Leaf) {
            Remove-Item -LiteralPath $displacedPath -Force `
                -ErrorAction SilentlyContinue
        }
    }
}

function Update-AgentBridgeFileFromTemp {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $TempPath,
        [Parameter(Mandatory)] [string] $DestinationPath
    )

    if (-not (Test-Path -LiteralPath $TempPath -PathType Leaf)) {
        throw "claim update temp file is missing: $TempPath"
    }
    if (-not (Test-Path -LiteralPath $DestinationPath -PathType Leaf)) {
        throw "claim disappeared before atomic update: $DestinationPath"
    }

    $backupPath = (
        "$DestinationPath.bak.$PID." +
        "$([guid]::NewGuid().ToString('N'))"
    )
    $preserveBackup = $false
    try {
        [System.IO.File]::Replace(
            $TempPath,
            $DestinationPath,
            $backupPath
        )
    } catch {
        $replaceError = $_.Exception
        if (Test-Path -LiteralPath $backupPath -PathType Leaf) {
            try {
                Restore-AgentBridgeFileBackup `
                    -OriginalPath $DestinationPath `
                    -BackupPath $backupPath
                $backupPath = $null
            } catch {
                $preserveBackup = $true
                throw (
                    "atomic claim update failed and destination restore " +
                    "failed; original backup retained at {0}: {1}; " +
                    "restore: {2}" -f
                    $backupPath,
                    $replaceError.Message,
                    $_.Exception.Message
                )
            }
        }
        throw $replaceError
    } finally {
        if (Test-Path -LiteralPath $TempPath -PathType Leaf) {
            Remove-Item -LiteralPath $TempPath -Force `
                -ErrorAction SilentlyContinue
        }
        if (
            -not $preserveBackup -and
            $backupPath -and
            (Test-Path -LiteralPath $backupPath -PathType Leaf)
        ) {
            try {
                Remove-Item -LiteralPath $backupPath -Force `
                    -ErrorAction Stop
            } catch {
                Write-Warning (
                    "could not remove atomic claim backup {0}: {1}" -f
                    $backupPath,
                    $_.Exception.Message
                )
            }
        }
    }
}

function Get-AgentBridgeClaimOwnerContext {
    [CmdletBinding()]
    param()

    $sessionId = [string][Environment]::GetEnvironmentVariable(
        'AGENT_BRIDGE_OWNER_SESSION_ID',
        'Process'
    )
    $ownerToken = [string][Environment]::GetEnvironmentVariable(
        'AGENT_BRIDGE_OWNER_TOKEN',
        'Process'
    )
    $ownerPidText = [string][Environment]::GetEnvironmentVariable(
        'AGENT_BRIDGE_OWNER_PID',
        'Process'
    )
    $ownerProcessStartUtc = [string][Environment]::GetEnvironmentVariable(
        'AGENT_BRIDGE_OWNER_PROCESS_START_UTC',
        'Process'
    )

    if ($sessionId -notmatch '^[A-Za-z0-9._:-]{1,128}\z') {
        throw 'claim_owner_mismatch: AGENT_BRIDGE_OWNER_SESSION_ID is missing or malformed'
    }
    if ($ownerToken -cnotmatch '^[0-9a-f]{64}\z') {
        throw 'claim_owner_mismatch: AGENT_BRIDGE_OWNER_TOKEN is missing or malformed'
    }
    $ownerPid = 0
    if (
        $ownerPidText -cnotmatch '^[0-9]+\z' -or
        -not [int]::TryParse(
            $ownerPidText,
            [System.Globalization.NumberStyles]::None,
            [System.Globalization.CultureInfo]::InvariantCulture,
            [ref]$ownerPid
        ) -or
        $ownerPid -le 0
    ) {
        throw 'claim_owner_mismatch: AGENT_BRIDGE_OWNER_PID is missing or malformed'
    }
    $ownerStarted = ConvertFrom-AgentBridgeCanonicalUtc `
        -Value $ownerProcessStartUtc
    if ($null -eq $ownerStarted) {
        throw 'claim_owner_mismatch: AGENT_BRIDGE_OWNER_PROCESS_START_UTC is missing or malformed'
    }

    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $tokenBytes = [System.Text.Encoding]::UTF8.GetBytes($ownerToken)
        $tokenHash = [System.BitConverter]::ToString(
            $sha256.ComputeHash($tokenBytes)
        ).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha256.Dispose()
    }

    return [pscustomobject]@{
        session_id = $sessionId
        token_sha256 = $tokenHash
        owner_pid = $ownerPid
        owner_process_start_utc = $ownerStarted.ToUniversalTime().ToString(
            'o',
            [System.Globalization.CultureInfo]::InvariantCulture
        )
    }
}

function Initialize-AgentBridgeClaimOwnerContext {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $SessionId
    )

    if ($SessionId -notmatch '^[A-Za-z0-9._:-]{1,128}\z') {
        throw 'claim_owner_mismatch: owner session id is malformed'
    }

    $ownerTokenBytes = New-Object byte[] 32
    $ownerRandom = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $ownerRandom.GetBytes($ownerTokenBytes)
    } finally {
        $ownerRandom.Dispose()
    }

    $env:AGENT_BRIDGE_OWNER_SESSION_ID = $SessionId
    $env:AGENT_BRIDGE_OWNER_TOKEN = [System.BitConverter]::ToString(
        $ownerTokenBytes
    ).Replace('-', '').ToLowerInvariant()
    $env:AGENT_BRIDGE_OWNER_PID = [string]$PID
    $env:AGENT_BRIDGE_OWNER_PROCESS_START_UTC = (
        Get-Process -Id $PID -ErrorAction Stop
    ).StartTime.ToUniversalTime().ToString(
        'o',
        [System.Globalization.CultureInfo]::InvariantCulture
    )

    return Get-AgentBridgeClaimOwnerContext
}

function Test-AgentBridgeStoredClaimOwnerComplete {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] $Claim
    )

    foreach ($field in @(
            'owner_session_id',
            'owner_token_sha256',
            'owner_pid',
            'owner_process_start_utc'
        )) {
        if (-not $Claim.PSObject.Properties[$field]) {
            return $false
        }
    }

    if (
        $Claim.owner_session_id -isnot [string] -or
        [string]$Claim.owner_session_id -notmatch
            '^[A-Za-z0-9._:-]{1,128}\z'
    ) {
        return $false
    }
    if (
        $Claim.owner_token_sha256 -isnot [string] -or
        [string]$Claim.owner_token_sha256 -cnotmatch '^[0-9a-f]{64}\z'
    ) {
        return $false
    }

    $storedOwnerPidValue = $Claim.owner_pid
    $storedOwnerPid = 0
    if ($storedOwnerPidValue -is [string]) {
        if (
            [string]$storedOwnerPidValue -cnotmatch '^[0-9]+\z' -or
            -not [int]::TryParse(
                [string]$storedOwnerPidValue,
                [System.Globalization.NumberStyles]::None,
                [System.Globalization.CultureInfo]::InvariantCulture,
                [ref]$storedOwnerPid
            ) -or
            $storedOwnerPid -le 0
        ) {
            return $false
        }
    } elseif (
        $storedOwnerPidValue -is [byte] -or
        $storedOwnerPidValue -is [sbyte] -or
        $storedOwnerPidValue -is [int16] -or
        $storedOwnerPidValue -is [uint16] -or
        $storedOwnerPidValue -is [int32] -or
        $storedOwnerPidValue -is [uint32] -or
        $storedOwnerPidValue -is [int64] -or
        $storedOwnerPidValue -is [uint64]
    ) {
        $numericOwnerPid = [decimal]$storedOwnerPidValue
        if (
            $numericOwnerPid -le 0 -or
            $numericOwnerPid -gt [int]::MaxValue
        ) {
            return $false
        }
        $storedOwnerPid = [int]$numericOwnerPid
    } else {
        return $false
    }

    $storedOwnerStarted = ConvertFrom-AgentBridgeCanonicalUtc `
        -Value $Claim.owner_process_start_utc
    if ($null -eq $storedOwnerStarted) {
        return $false
    }

    return $true
}

function Assert-AgentBridgeTaskId {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $TaskId
    )

    if ($TaskId -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._/-]{1,120}\z') {
        throw "task_id invalid: '$TaskId'"
    }
    foreach ($segment in [regex]::Split($TaskId, '/')) {
        if (
            $segment -ceq '' -or
            $segment -ceq '.' -or
            $segment -ceq '..'
        ) {
            throw "task_id invalid: '$TaskId'"
        }
    }
}

function Get-AgentBridgeClaimBaseName {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $TaskId
    )

    Assert-AgentBridgeTaskId -TaskId $TaskId
    $safeValue = (($TaskId -replace '[^A-Za-z0-9._-]', '_').Trim('_'))
    if (-not $safeValue) {
        throw "task_id does not produce a safe claim filename: '$TaskId'"
    }
    if ($safeValue -ceq $TaskId) {
        return $safeValue
    }

    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $valueBytes = [System.Text.Encoding]::UTF8.GetBytes($TaskId)
        $digest = [System.BitConverter]::ToString(
            $sha256.ComputeHash($valueBytes)
        ).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha256.Dispose()
    }
    return '{0}-{1}' -f $safeValue, $digest.Substring(0, 12)
}

function Assert-AgentBridgePreferredClaimPath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $ClaimsDir,
        [Parameter(Mandatory)] [string] $TaskId
    )

    $baseName = Get-AgentBridgeClaimBaseName -TaskId $TaskId
    $preferredPath = Join-Path $ClaimsDir ($baseName + '.json')
    if (-not (Test-Path -LiteralPath $preferredPath)) {
        return $preferredPath
    }
    if (-not (Test-Path -LiteralPath $preferredPath -PathType Leaf)) {
        throw (
            "claim filename collision at preferred path for task_id " +
            "'{0}': non-file entry {1}" -f $TaskId, $preferredPath
        )
    }

    try {
        $preferredClaim = ConvertFrom-AgentBridgeJson -Json (
            Get-Content -Raw -LiteralPath $preferredPath -Encoding UTF8 `
                -ErrorAction Stop
        )
    } catch {
        throw (
            "claim filename collision at preferred path for task_id " +
            "'{0}': unreadable record {1}" -f $TaskId, $preferredPath
        )
    }
    if (
        $null -eq $preferredClaim -or
        $preferredClaim -isnot [pscustomobject] -or
        -not $preferredClaim.PSObject.Properties['task_id'] -or
        $preferredClaim.task_id -isnot [string] -or
        [string]$preferredClaim.task_id -cne $TaskId
    ) {
        $storedTaskId = if (
            $null -ne $preferredClaim -and
            $preferredClaim -is [pscustomobject] -and
            $preferredClaim.PSObject.Properties['task_id'] -and
            $preferredClaim.task_id -is [string]
        ) {
            "'$([string]$preferredClaim.task_id)'"
        } else {
            '<missing-or-nonstring>'
        }
        throw (
            "claim filename collision at preferred path for task_id " +
            "'{0}': stored task_id {1} in {2}" -f
            $TaskId,
            $storedTaskId,
            $preferredPath
        )
    }
    return $preferredPath
}

function Test-AgentBridgeClaimOwner {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] $Claim,
        [Parameter(Mandatory)] $OwnerContext
    )

    if (-not (Test-AgentBridgeStoredClaimOwnerComplete -Claim $Claim)) {
        return $false
    }

    return (
        [string]$Claim.owner_session_id -ceq [string]$OwnerContext.session_id -and
        [string]$Claim.owner_token_sha256 -ceq [string]$OwnerContext.token_sha256
    )
}

function Assert-AgentBridgeClaimOwner {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] $Claim,
        [Parameter(Mandatory)] $OwnerContext,
        [Parameter(Mandatory)] [string] $Operation
    )

    if (-not (Test-AgentBridgeStoredClaimOwnerComplete -Claim $Claim)) {
        throw (
            "claim_owner_legacy_tokenless: current session cannot {0} a legacy tokenless claim" -f
            $Operation
        )
    }
    if (-not (Test-AgentBridgeClaimOwner -Claim $Claim -OwnerContext $OwnerContext)) {
        throw (
            "claim_owner_wrong_generation: current session cannot {0} claim owned by another generation" -f
            $Operation
        )
    }
}
