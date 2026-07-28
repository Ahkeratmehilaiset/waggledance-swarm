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
    $agentPattern = '^[a-z][a-z0-9_-]{1,32}$'
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

    if ($sessionId -notmatch '^[A-Za-z0-9._:-]{1,128}$') {
        throw 'claim_owner_mismatch: AGENT_BRIDGE_OWNER_SESSION_ID is missing or malformed'
    }
    if ($ownerToken -cnotmatch '^[0-9a-f]{64}$') {
        throw 'claim_owner_mismatch: AGENT_BRIDGE_OWNER_TOKEN is missing or malformed'
    }
    $ownerPid = 0
    if (-not [int]::TryParse($ownerPidText, [ref]$ownerPid) -or $ownerPid -le 0) {
        throw 'claim_owner_mismatch: AGENT_BRIDGE_OWNER_PID is missing or malformed'
    }
    $ownerStarted = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse(
            $ownerProcessStartUtc,
            [System.Globalization.CultureInfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::AssumeUniversal -bor
                [System.Globalization.DateTimeStyles]::AdjustToUniversal,
            [ref]$ownerStarted
        )) {
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

    if ($SessionId -notmatch '^[A-Za-z0-9._:-]{1,128}$') {
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

function Test-AgentBridgeClaimOwner {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] $Claim,
        [Parameter(Mandatory)] $OwnerContext
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

    if (-not (Test-AgentBridgeClaimOwner -Claim $Claim -OwnerContext $OwnerContext)) {
        throw (
            "claim_owner_mismatch: current session cannot {0} claim owned by another generation or a legacy tokenless claim" -f
            $Operation
        )
    }
}
