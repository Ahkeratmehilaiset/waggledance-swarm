#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [ValidateSet('codex','claude','operator','system')] [string] $Agent,
    [Parameter(Mandatory)] [string] $TaskId,
    [Parameter(Mandatory)] [string] $Summary,
    [ValidateSet('read-only','write')] [string] $Mode = 'read-only',
    [string[]] $WriteScope = @(),
    [string] $RunId = '',
    [switch] $Force
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# R13 (Codex scout 2026-05-09): honor AGENT_BRIDGE_RUNTIME_ROOT so
# per-agent worktrees can share one runtime state directory. Codex
# blocker 2026-05-09T13:11Z: if the env var is SET, USE IT - do not
# silently fall back to per-worktree state, that would split-brain
# the agents on first-run / typo / new-root paths. We create the
# directory if missing (first-run bootstrap) and fail loudly on
# malformed paths via -ErrorAction Stop.
$bridgeRoot = if ($env:AGENT_BRIDGE_RUNTIME_ROOT) {
    [string]$env:AGENT_BRIDGE_RUNTIME_ROOT
} else {
    Split-Path -Parent $PSScriptRoot
}
if (-not (Test-Path -LiteralPath $bridgeRoot -PathType Container)) {
    [void](New-Item -ItemType Directory -Path $bridgeRoot -Force -ErrorAction Stop)
}
$claimsDir = Join-Path (Join-Path $bridgeRoot 'work_queue') 'claims'
if (-not (Test-Path -LiteralPath $claimsDir)) {
    [void](New-Item -ItemType Directory -Path $claimsDir -Force)
}

function ConvertTo-SafeName {
    param([string] $Name)
    return (($Name -replace '[^A-Za-z0-9._-]', '_').Trim('_'))
}

function Normalize-Scope {
    param([string] $Scope)
    return (($Scope -replace '\\','/').Trim('/')).ToLowerInvariant()
}

function Test-ScopeOverlap {
    param([string[]] $A, [string[]] $B)
    foreach ($a0 in @($A)) {
        $a = Normalize-Scope $a0
        if (-not $a) { continue }
        foreach ($b0 in @($B)) {
            $b = Normalize-Scope $b0
            if (-not $b) { continue }
            if ($a -eq '*' -or $b -eq '*') { return $true }
            if ($a -eq $b) { return $true }
            if ($a.StartsWith($b + '/') -or $b.StartsWith($a + '/')) { return $true }
        }
    }
    return $false
}

function Stop-BridgeClaim {
    param([Parameter(Mandatory)] [string] $Message, [Parameter(Mandatory)] [int] $Code)
    [Console]::Error.WriteLine($Message)
    exit $Code
}

function Get-CurrentGitBranch {
    try {
        $branch = (& git branch --show-current 2>$null)
        if ($LASTEXITCODE -eq 0) { return [string]$branch }
    } catch {}
    return ''
}

if ($Mode -eq 'write' -and @($WriteScope).Count -eq 0) {
    throw 'write claims require at least one -WriteScope path'
}

$safeTask = ConvertTo-SafeName $TaskId
if (-not $safeTask) { throw 'TaskId does not produce a safe claim filename' }
$claimPath = Join-Path $claimsDir ($safeTask + '.json')

$activeClaims = @(Get-ChildItem -Path $claimsDir -Filter '*.json' -File -ErrorAction SilentlyContinue)
foreach ($file in $activeClaims) {
    try {
        $existing = Get-Content -Raw -Path $file.FullName -Encoding UTF8 | ConvertFrom-Json
    } catch {
        continue
    }
    if ([string]$existing.task_id -eq $TaskId) {
        if (-not $Force) {
            Stop-BridgeClaim -Message ("task already claimed by {0}: {1}" -f $existing.agent, $file.FullName) -Code 2
        }
        if ([string]$existing.agent -ne $Agent -and $Agent -notin @('operator','system')) {
            Stop-BridgeClaim -Message ("cannot force-update claim owned by {0}: {1}" -f $existing.agent, $file.FullName) -Code 3
        }
        continue
    }
    if ($Mode -eq 'write' -and [string]$existing.mode -eq 'write') {
        if (Test-ScopeOverlap -A $WriteScope -B @($existing.write_scope)) {
            Stop-BridgeClaim -Message ("write-scope conflict with active claim {0} by {1}: {2}" -f $existing.task_id, $existing.agent, ((@($existing.write_scope)) -join ', ')) -Code 3
        }
    }
}

if (-not $RunId) {
    $RunId = if ($env:AGENT_BRIDGE_RUN_ID) { [string]$env:AGENT_BRIDGE_RUN_ID } else { '' }
}

$claim = [ordered]@{
    claimed_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    agent          = $Agent
    task_id        = $TaskId
    summary        = $Summary
    mode           = $Mode
    write_scope    = @($WriteScope)
    run_id         = $RunId
    pid            = $PID
    cwd            = (Get-Location).Path
    git_branch     = Get-CurrentGitBranch
}
$json = ($claim | ConvertTo-Json -Depth 8)

$encoding = New-Object System.Text.UTF8Encoding($false)
try {
    $fs = New-Object System.IO.FileStream($claimPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::Read)
    try {
        $bytes = $encoding.GetBytes($json)
        $fs.Write($bytes, 0, $bytes.Length)
    } finally {
        $fs.Dispose()
    }
} catch {
    if (-not $Force) {
        Stop-BridgeClaim -Message ("could not create claim, likely already exists: {0}" -f $claimPath) -Code 2
    }
    # Internal review fix R7 (2026-05-09): the -Force fallback used
    # Set-Content, which is non-atomic; a concurrent reader could
    # observe a partially-written claim and treat it as malformed.
    # Write to a temp sibling and Replace() so readers always see the
    # old or the new claim, never a torn write.
    $tmpClaim = "$claimPath.tmp.$PID.$([guid]::NewGuid().ToString('N'))"
    [System.IO.File]::WriteAllText($tmpClaim, $json, $encoding)
    $backupClaim = $null
    try {
        if (Test-Path -LiteralPath $claimPath) {
            $backupClaim = "$claimPath.bak.$PID.$([guid]::NewGuid().ToString('N'))"
            [System.IO.File]::Replace($tmpClaim, $claimPath, $backupClaim)
            try { Remove-Item -LiteralPath $backupClaim -Force -ErrorAction SilentlyContinue } catch {}
        } else {
            [System.IO.File]::Move($tmpClaim, $claimPath)
        }
    } catch {
        try {
            if ($backupClaim -and (Test-Path -LiteralPath $backupClaim)) {
                Remove-Item -LiteralPath $backupClaim -Force -ErrorAction SilentlyContinue
            }
        } catch {}
        try { Remove-Item -LiteralPath $tmpClaim -Force -ErrorAction SilentlyContinue } catch {}
        throw
    }
}

& (Join-Path $PSScriptRoot 'Write-AgentEvent.ps1') -Agent $Agent -Type claim -TaskId $TaskId -Status active -Message $Summary -WriteScope $WriteScope -RunId $RunId | Out-Null
[pscustomobject]$claim
