#requires -Version 5.1

$script:AgentBridgeClaimOwnerContract = 'v1'

function Assert-AgentBridgeSessionIdentity {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $RequestedAgent
    )

    $boundAgent = [string][Environment]::GetEnvironmentVariable(
        'AGENT_BRIDGE_AGENT',
        'Process'
    )
    $agentPattern = '^[a-z][a-z0-9_-]{1,32}$'
    if ($RequestedAgent -cnotmatch $agentPattern) {
        throw 'claim_owner_agent_mismatch: requested agent is malformed'
    }
    if ($boundAgent -and $boundAgent -cnotmatch $agentPattern) {
        throw 'claim_owner_agent_mismatch: AGENT_BRIDGE_AGENT is malformed'
    }
    if (-not $boundAgent -and $RequestedAgent -cin @('operator', 'system')) {
        throw (
            "claim_owner_agent_mismatch: reserved agent '{0}' requires a bound session" -f
            $RequestedAgent
        )
    }
    if ($RequestedAgent -ceq 'system') {
        throw 'claim_owner_agent_mismatch: system has no public mutation authority'
    }
    if ($boundAgent -and $boundAgent -cne $RequestedAgent) {
        throw (
            "claim_owner_agent_mismatch: session agent '{0}' cannot act as '{1}'" -f
            $boundAgent,
            $RequestedAgent
        )
    }
}

function Get-AgentBridgeClaimOwnerContext {
    [CmdletBinding()]
    param()

    $ownerSessionId = [string][Environment]::GetEnvironmentVariable(
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
    $ownerProcessStartText = [string][Environment]::GetEnvironmentVariable(
        'AGENT_BRIDGE_OWNER_PROCESS_START_UTC',
        'Process'
    )

    if ($ownerSessionId -cnotmatch '^[A-Za-z0-9._:-]{1,128}$') {
        throw (
            'claim_owner_context_invalid: ' +
            'AGENT_BRIDGE_OWNER_SESSION_ID is missing or malformed'
        )
    }
    if ($ownerToken -cnotmatch '^[0-9a-f]{64}$') {
        throw (
            'claim_owner_context_invalid: ' +
            'AGENT_BRIDGE_OWNER_TOKEN is missing or malformed'
        )
    }
    $ownerPid = 0
    if (-not [int]::TryParse($ownerPidText, [ref]$ownerPid) -or $ownerPid -le 0) {
        throw (
            'claim_owner_context_invalid: ' +
            'AGENT_BRIDGE_OWNER_PID is missing or malformed'
        )
    }
    $ownerProcessStart = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse(
            $ownerProcessStartText,
            [System.Globalization.CultureInfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::AssumeUniversal -bor
                [System.Globalization.DateTimeStyles]::AdjustToUniversal,
            [ref]$ownerProcessStart
        )) {
        throw (
            'claim_owner_context_invalid: ' +
            'AGENT_BRIDGE_OWNER_PROCESS_START_UTC is missing or malformed'
        )
    }

    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $tokenBytes = [System.Text.Encoding]::UTF8.GetBytes($ownerToken)
        $tokenDigest = [System.BitConverter]::ToString(
            $sha256.ComputeHash($tokenBytes)
        ).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha256.Dispose()
    }

    return [pscustomobject]@{
        owner_session_id = $ownerSessionId
        owner_token_sha256 = $tokenDigest
        owner_pid = $ownerPid
        owner_process_start_utc = $ownerProcessStart.ToUniversalTime().ToString(
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

    if ($SessionId -cnotmatch '^[A-Za-z0-9._:-]{1,128}$') {
        throw 'claim_owner_context_invalid: owner session id is malformed'
    }

    $ownerTokenBytes = New-Object byte[] 32
    $random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $random.GetBytes($ownerTokenBytes)
    } finally {
        $random.Dispose()
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
    if ([string]$Claim.owner_session_id -cnotmatch '^[A-Za-z0-9._:-]{1,128}$') {
        return $false
    }
    if ([string]$Claim.owner_token_sha256 -cnotmatch '^[0-9a-f]{64}$') {
        return $false
    }
    $ownerPid = 0
    if (-not [int]::TryParse([string]$Claim.owner_pid, [ref]$ownerPid) -or
        $ownerPid -le 0) {
        return $false
    }
    $ownerProcessStart = [DateTimeOffset]::MinValue
    return [DateTimeOffset]::TryParse(
        [string]$Claim.owner_process_start_utc,
        [System.Globalization.CultureInfo]::InvariantCulture,
        [System.Globalization.DateTimeStyles]::AssumeUniversal -bor
            [System.Globalization.DateTimeStyles]::AdjustToUniversal,
        [ref]$ownerProcessStart
    )
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
        [string]$Claim.owner_session_id -ceq
            [string]$OwnerContext.owner_session_id -and
        [string]$Claim.owner_token_sha256 -ceq
            [string]$OwnerContext.owner_token_sha256
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
            'claim_owner_legacy_tokenless: current session cannot {0} ' +
            'a legacy tokenless claim' -f $Operation
        )
    }
    if (-not (Test-AgentBridgeClaimOwner `
            -Claim $Claim `
            -OwnerContext $OwnerContext)) {
        throw (
            'claim_owner_wrong_generation: current session cannot {0} ' +
            'a claim owned by another generation' -f $Operation
        )
    }
}
