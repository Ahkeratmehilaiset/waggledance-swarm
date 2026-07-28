#requires -Version 5.1

$script:AgentBridgeSessionIdentityContract = 'v1'

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
