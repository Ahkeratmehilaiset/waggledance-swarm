# Signals.ps1
# Read/write the signal files Claude Code is asked to drop in
#   iterations/<id>/signals/
# claude_started.json | claude_completed.json | claude_failed.json

Set-StrictMode -Version Latest

function Get-SignalsDir {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [string] $IterationFolder)
    return (Join-Path $IterationFolder 'signals')
}

function Initialize-SignalsDir {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [string] $IterationFolder)
    $d = Get-SignalsDir -IterationFolder $IterationFolder
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
    return $d
}

function Write-StartedSignal {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $IterationFolder,
        [Parameter(Mandatory)] [string] $IterationId,
        [string] $CommandLine = ''
    )
    $d = Initialize-SignalsDir -IterationFolder $IterationFolder
    $obj = [pscustomobject]@{
        iteration_id = $IterationId
        started_at   = (Get-Date).ToUniversalTime().ToString('o')
        command_line = $CommandLine
        pid          = $PID
    }
    $path = Join-Path $d 'claude_started.json'
    $obj | ConvertTo-Json | Set-Content -Path $path -Encoding UTF8
    return $path
}

function Test-CompletionSignal {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [string] $IterationFolder)
    return (Test-Path (Join-Path (Get-SignalsDir $IterationFolder) 'claude_completed.json'))
}

function Test-FailureSignal {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [string] $IterationFolder)
    return (Test-Path (Join-Path (Get-SignalsDir $IterationFolder) 'claude_failed.json'))
}

function Read-CompletionSignal {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [string] $IterationFolder)
    $p = Join-Path (Get-SignalsDir $IterationFolder) 'claude_completed.json'
    if (-not (Test-Path $p)) { return $null }
    try { return Get-Content -Raw -Path $p | ConvertFrom-Json }
    catch { return $null }
}

function Read-FailureSignal {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [string] $IterationFolder)
    $p = Join-Path (Get-SignalsDir $IterationFolder) 'claude_failed.json'
    if (-not (Test-Path $p)) { return $null }
    try { return Get-Content -Raw -Path $p | ConvertFrom-Json }
    catch { return $null }
}

function Write-FailureSignal {
    <#
    .SYNOPSIS
    The orchestrator itself can write a failure signal when it determines
    failure (e.g., process exited non-zero but Claude didn't write its own).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $IterationFolder,
        [Parameter(Mandatory)] [string] $Reason,
        [hashtable] $Extra = @{}
    )
    $d = Initialize-SignalsDir -IterationFolder $IterationFolder
    $obj = @{
        failed_at = (Get-Date).ToUniversalTime().ToString('o')
        reason    = $Reason
        source    = 'orchestrator'
    }
    foreach ($k in $Extra.Keys) { $obj[$k] = $Extra[$k] }
    $path = Join-Path $d 'claude_failed.json'
    [pscustomobject]$obj | ConvertTo-Json | Set-Content -Path $path -Encoding UTF8
    return $path
}
