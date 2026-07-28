#requires -Version 5.1

function Enter-BridgeSmokeIdentityIsolation {
    [CmdletBinding()]
    param()

    $identityNames = @(
        'AGENT_BRIDGE_AGENT',
        'AGENT_BRIDGE_RUN_ID',
        'AGENT_BRIDGE_SESSION_ID',
        'AGENT_BRIDGE_OWNER_SESSION_ID',
        'AGENT_BRIDGE_OWNER_TOKEN',
        'AGENT_BRIDGE_OWNER_PID',
        'AGENT_BRIDGE_OWNER_PROCESS_START_UTC',
        'AGENT_BRIDGE_ROLE',
        'AGENT_BRIDGE_AGENT_UUID',
        'AGENT_BRIDGE_CAPABILITIES'
    )
    $saved = [ordered]@{}
    foreach ($name in $identityNames) {
        $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $null, 'Process')
    }

    $fixtureTokenBytes = New-Object byte[] 32
    $fixtureRandom = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $fixtureRandom.GetBytes($fixtureTokenBytes)
    } finally {
        $fixtureRandom.Dispose()
    }
    $fixtureToken = [System.BitConverter]::ToString(
        $fixtureTokenBytes
    ).Replace('-', '').ToLowerInvariant()
    $fixtureProcessStartUtc = (
        Get-Process -Id $PID -ErrorAction Stop
    ).StartTime.ToUniversalTime().ToString(
        'o',
        [System.Globalization.CultureInfo]::InvariantCulture
    )
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_OWNER_SESSION_ID',
        "bridge-smoke-$PID",
        'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_OWNER_TOKEN',
        $fixtureToken,
        'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_OWNER_PID',
        [string]$PID,
        'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'AGENT_BRIDGE_OWNER_PROCESS_START_UTC',
        $fixtureProcessStartUtc,
        'Process'
    )
    return [pscustomobject]@{ Values = $saved }
}

function Exit-BridgeSmokeIdentityIsolation {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [object] $Snapshot
    )

    foreach ($entry in $Snapshot.Values.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable(
            [string]$entry.Key,
            $entry.Value,
            'Process'
        )
    }
}
