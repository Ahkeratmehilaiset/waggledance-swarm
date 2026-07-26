#requires -Version 5.1
<#
.SYNOPSIS
    Shared Python/PowerShell transaction lock for work-queue mutations.

.DESCRIPTION
    Every writer that resolves, authorizes, or mutates
    work_queue/claims/*.json must hold this machine-wide Windows mutex for
    the complete operation. The mutex suffix is the first 24 hex characters
    of SHA-256 over the absolute, slash-normalized, trailing-slash-trimmed
    bridge-root path after ASCII a-z characters are mapped to A-Z. Non-ASCII
    characters are left unchanged so Python, Windows PowerShell 5.1, and
    PowerShell 7 derive the exact same UTF-8 input. Independent runtime roots
    remain isolated.

    A Windows kernel mutex releases ownership when its process exits. An
    abandoned acquisition is surfaced to the caller. Every mutator validates
    the complete canonical claim set immediately after acquisition, so a
    clean atomic generation can continue while corrupt state fails closed.

    Never hold this mutex while calling Write-AgentEvent.ps1. Bridge events
    use AppendV1, and keeping the two locks disjoint avoids lock-order cycles.
#>

$script:WaggleDanceWorkQueueMutexPrefix = 'Global\WaggleDanceBridgeWorkQueueV1'
$script:WaggleDanceWorkQueueMutexTimeoutMs = 10000

function Get-WaggleDanceWorkQueueMutexName {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $BridgeRoot
    )

    $fullPath = [System.IO.Path]::GetFullPath($BridgeRoot)
    $pathText = $fullPath.Replace([char]'/', [char]'\').TrimEnd(
        [char[]]@([char]'\', [char]'/')
    )
    $builder = New-Object System.Text.StringBuilder
    foreach ($character in $pathText.ToCharArray()) {
        $codePoint = [int][char]$character
        if ($codePoint -ge 97 -and $codePoint -le 122) {
            [void]$builder.Append([char]($codePoint - 32))
        } else {
            [void]$builder.Append($character)
        }
    }
    $normalized = $builder.ToString()
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($normalized)
        $hash = $sha.ComputeHash($bytes)
    } finally {
        $sha.Dispose()
    }
    $digest = -join @($hash | ForEach-Object { $_.ToString('x2') })
    return ("{0}-{1}" -f $script:WaggleDanceWorkQueueMutexPrefix, $digest.Substring(0, 24))
}

function Enter-WaggleDanceWorkQueueTransaction {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $BridgeRoot,
        [int] $TimeoutMilliseconds = $script:WaggleDanceWorkQueueMutexTimeoutMs
    )

    if ($TimeoutMilliseconds -le 0) {
        throw 'work-queue mutex timeout must be positive'
    }

    $mutex = $null
    $acquired = $false
    $abandoned = $false
    try {
        if (
            [Environment]::GetEnvironmentVariable(
                'AGENT_BRIDGE_TEST_WORK_QUEUE_MUTEX_CONSTRUCTION_FAILURE',
                'Process'
            ) -eq '1'
        ) {
            throw 'simulated WorkQueueV1 mutex construction failure'
        }
        $mutexName = Get-WaggleDanceWorkQueueMutexName -BridgeRoot $BridgeRoot
        $mutex = New-Object System.Threading.Mutex($false, $mutexName)
        if ($null -eq $mutex) {
            throw 'WorkQueueV1 mutex construction returned null'
        }
        try {
            $acquired = $mutex.WaitOne($TimeoutMilliseconds)
        } catch [System.Threading.AbandonedMutexException] {
            $acquired = $true
            $abandoned = $true
        }

        if (-not $acquired) {
            throw "WorkQueueV1 mutex timeout after $TimeoutMilliseconds ms"
        }
        return [pscustomobject]@{
            Mutex = $mutex
            Acquired = $true
            Abandoned = $abandoned
        }
    } catch {
        if ($acquired -and $null -ne $mutex) {
            try { $mutex.ReleaseMutex() } catch {}
        }
        if ($null -ne $mutex) {
            try { $mutex.Dispose() } catch {}
        }
        throw
    }
}

function Exit-WaggleDanceWorkQueueTransaction {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [object] $Transaction
    )

    $mutex = $Transaction.Mutex
    try {
        if ($Transaction.Acquired) {
            $mutex.ReleaseMutex()
        }
    } finally {
        $mutex.Dispose()
    }
}

function Assert-WaggleDanceWorkQueueClaimSet {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $ClaimsDirectory
    )

    if (-not (Test-Path -LiteralPath $ClaimsDirectory -PathType Container)) {
        return
    }

    foreach ($file in @(
        Get-ChildItem -LiteralPath $ClaimsDirectory -Filter '*.json' -File `
            -ErrorAction Stop |
            Sort-Object FullName
    )) {
        try {
            $claim = Get-Content -Raw -LiteralPath $file.FullName `
                -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
        } catch {
            throw "unreadable canonical claim file: $($file.FullName)"
        }
        if ($null -eq $claim -or $claim -is [System.Array]) {
            throw "canonical claim file must be a JSON object: $($file.FullName)"
        }
        if (
            $null -ne $claim.PSObject.Properties['release_status'] -or
            $null -ne $claim.PSObject.Properties['released_at_utc']
        ) {
            throw "active claim file contains terminal markers: $($file.FullName)"
        }

        $taskProperty = $claim.PSObject.Properties['task_id']
        $agentProperty = $claim.PSObject.Properties['agent']
        $modeProperty = $claim.PSObject.Properties['mode']
        if (
            $null -eq $taskProperty -or
            [string]::IsNullOrWhiteSpace([string]$taskProperty.Value)
        ) {
            throw "canonical claim file has no task_id: $($file.FullName)"
        }
        if (
            $null -eq $agentProperty -or
            [string]::IsNullOrWhiteSpace([string]$agentProperty.Value)
        ) {
            throw "canonical claim file has no agent: $($file.FullName)"
        }
        if (
            $null -eq $modeProperty -or
            [string]$modeProperty.Value -notin @('read-only', 'write')
        ) {
            throw "canonical claim file has invalid mode: $($file.FullName)"
        }
        if ([string]$modeProperty.Value -eq 'write') {
            $scopeProperty = $claim.PSObject.Properties['write_scope']
            $scope = if ($null -eq $scopeProperty) {
                @()
            } else {
                @($scopeProperty.Value) |
                    ForEach-Object { [string]$_ -split ',' } |
                    ForEach-Object { $_.Trim() } |
                    Where-Object { $_ }
            }
            if (@($scope).Count -eq 0) {
                throw "canonical write claim has no write_scope: $($file.FullName)"
            }
        }

        $leaseProperty = $claim.PSObject.Properties['lease_seconds']
        if ($null -ne $leaseProperty) {
            $parsedLease = 0
            if (
                -not [int]::TryParse(
                    [string]$leaseProperty.Value,
                    [ref]$parsedLease
                ) -or
                $parsedLease -lt 0
            ) {
                throw "canonical claim has invalid lease_seconds: $($file.FullName)"
            }
        }

        $capabilitiesProperty = $claim.PSObject.Properties['capabilities']
        if (
            $null -ne $capabilitiesProperty -and
            $capabilitiesProperty.Value -isnot [string] -and
            $capabilitiesProperty.Value -isnot [System.Array]
        ) {
            throw "canonical claim has invalid capabilities: $($file.FullName)"
        }
    }
}
