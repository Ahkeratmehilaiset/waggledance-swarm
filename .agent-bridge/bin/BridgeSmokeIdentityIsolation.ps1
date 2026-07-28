#requires -Version 5.1

function Enter-BridgeSmokeIdentityIsolation {
    [CmdletBinding()]
    param()

    $identityNames = @(
        'AGENT_BRIDGE_AGENT',
        'AGENT_BRIDGE_RUN_ID',
        'AGENT_BRIDGE_SESSION_ID',
        'AGENT_BRIDGE_ROLE',
        'AGENT_BRIDGE_AGENT_UUID',
        'AGENT_BRIDGE_CAPABILITIES'
    )
    $saved = [ordered]@{}
    foreach ($name in $identityNames) {
        $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $null, 'Process')
    }
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
