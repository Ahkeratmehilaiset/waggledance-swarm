# Checkpoint.ps1
# Read/write of state.json. Atomic writes. Backup of last good copy.

Set-StrictMode -Version Latest

function New-WaggleState {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $IterationId,
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $TranscriptFile,
        [string] $ReportFile = '',
        [string] $GitBranch = '',
        [string] $GitCommit = '',
        [string] $ExecutionMode = 'interactiveTranscriptFallback'
    )
    $now = (Get-Date).ToUniversalTime().ToString('o')
    return [pscustomobject]@{
        iteration_id              = $IterationId
        execution_mode            = $ExecutionMode
        started_at                = $now
        phase                     = 'claude_code_run'
        status                    = 'RUNNING'
        last_check_at             = $now
        transcript_file           = $TranscriptFile
        transcript_size_bytes     = 0
        transcript_last_growth_at = $now
        report_file               = $ReportFile
        report_last_modified      = $null
        git_branch                = $GitBranch
        git_commit                = $GitCommit
        last_verdict              = $null
        runner_result             = $null
        error                     = $null
        history                   = @()
    }
}

function Save-WaggleState {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] $State,
        [Parameter(Mandatory)] [string] $Path
    )
    $dir = Split-Path -Parent $Path
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }

    $tmp = "$Path.tmp"
    $bak = "$Path.bak"
    $json = $State | ConvertTo-Json -Depth 14
    Set-Content -Path $tmp -Value $json -Encoding UTF8

    if (Test-Path $Path) { Copy-Item -Path $Path -Destination $bak -Force }
    Move-Item -Force $tmp $Path
}

function Read-WaggleState {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [string] $Path)
    if (-not (Test-Path $Path)) { return $null }
    try { return Get-Content -Raw -Path $Path | ConvertFrom-Json }
    catch {
        Write-Warning "state.json corrupted: $($_.Exception.Message). Trying .bak."
        $bak = "$Path.bak"
        if (Test-Path $bak) { return Get-Content -Raw -Path $bak | ConvertFrom-Json }
        throw
    }
}

function Update-WaggleStatus {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] $State,
        [Parameter(Mandatory)] [string] $NewStatus,
        [string] $Reason = ''
    )
    if ($State.status -eq $NewStatus) { return $State }

    $entry = [pscustomobject]@{
        at     = (Get-Date).ToUniversalTime().ToString('o')
        from   = $State.status
        to     = $NewStatus
        reason = $Reason
    }

    $hist = @()
    if ($State.history) { $hist = @($State.history) }
    $hist += $entry
    $State.history = $hist
    $State.status = $NewStatus
    return $State
}

function Set-WaggleVerdict {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] $State,
        [Parameter(Mandatory)] $Verdict
    )
    $State.last_verdict = [pscustomobject]@{
        at      = (Get-Date).ToUniversalTime().ToString('o')
        status  = $Verdict.status
        reason  = $Verdict.reason
        signals = $Verdict.signals
    }
    return $State
}

function Set-WaggleError {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] $State,
        [Parameter(Mandatory)] [string] $Type,
        [string] $Message = '',
        [string] $Hint = ''
    )
    $State.error = [pscustomobject]@{
        type    = $Type
        message = $Message
        hint    = $Hint
        at      = (Get-Date).ToUniversalTime().ToString('o')
    }
    return $State
}
