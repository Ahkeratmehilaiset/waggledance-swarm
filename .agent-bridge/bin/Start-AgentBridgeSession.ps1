#requires -Version 5.1
<#
.SYNOPSIS
    Bootstrap a Claude/Codex bridge session after reboot.

.DESCRIPTION
    Creates the shared bridge runtime root, sets the process
    AGENT_BRIDGE_RUNTIME_ROOT and AGENT_BRIDGE_RUN_ID variables, moves to
    the repo root, and emits a liveness/active event.

    Dot-source this script from a new agent shell so the environment variables
    remain available to Claude Code or Codex after the script returns:

      . .\.agent-bridge\bin\Start-AgentBridgeSession.ps1 -Agent codex
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet('codex','claude','operator','system')]
    [string] $Agent,

    [string] $RuntimeRoot = 'C:\Python\project2-bridge-runtime',
    [string] $RepoRoot = '',
    [string] $RunId = '',

    [switch] $SkipBridgeRead,
    [switch] $SkipLiveness,
    [switch] $SkipGitStatus
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Resolve-FullPath {
    param([Parameter(Mandatory)] [string] $Path)
    return [System.IO.Path]::GetFullPath($Path)
}

$bridgeRoot = Split-Path -Parent $PSScriptRoot
if (-not $RepoRoot) {
    $RepoRoot = Split-Path -Parent $bridgeRoot
}

$repoFull = Resolve-FullPath $RepoRoot
$runtimeFull = Resolve-FullPath $RuntimeRoot

if (-not (Test-Path -LiteralPath $repoFull -PathType Container)) {
    throw "repo root does not exist: $repoFull"
}
if (-not (Test-Path -LiteralPath (Join-Path $repoFull '.git'))) {
    throw "repo root has no .git; refusing bridge bootstrap: $repoFull"
}

$repoDrive = [System.IO.Path]::GetPathRoot($repoFull)
if ($repoDrive -and $repoDrive.TrimEnd('\') -ne 'C:') {
    throw "repo root must be on persistent C: drive: $repoFull"
}

$runtimeDirs = @(
    $runtimeFull,
    (Join-Path $runtimeFull 'shared'),
    (Join-Path $runtimeFull 'work_queue'),
    (Join-Path $runtimeFull 'work_queue\claims'),
    (Join-Path $runtimeFull 'work_queue\done'),
    (Join-Path $runtimeFull 'outbox'),
    (Join-Path $runtimeFull "outbox\$Agent"),
    (Join-Path $runtimeFull 'inbox'),
    (Join-Path $runtimeFull "inbox\$Agent")
)
foreach ($dir in $runtimeDirs) {
    if (-not (Test-Path -LiteralPath $dir -PathType Container)) {
        [void](New-Item -ItemType Directory -Path $dir -Force -ErrorAction Stop)
    }
}

if (-not $RunId) {
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
    $RunId = "$Agent-$stamp"
}

$env:AGENT_BRIDGE_RUNTIME_ROOT = $runtimeFull
$env:AGENT_BRIDGE_RUN_ID = $RunId

Set-Location -LiteralPath $repoFull

$gitBranch = ''
if (-not $SkipGitStatus) {
    try {
        $gitBranch = (& git branch --show-current 2>$null)
        if ($LASTEXITCODE -ne 0) { $gitBranch = '' }
    } catch {
        $gitBranch = ''
    }
}

if (-not $SkipLiveness) {
    $sendLiveness = Join-Path $PSScriptRoot 'Send-Liveness.ps1'
    & $sendLiveness `
        -Agent $Agent `
        -State active `
        -TaskId "$Agent-session-bootstrap-$((Get-Date).ToUniversalTime().ToString('yyyy-MM-dd'))" `
        -Message "$Agent session bootstrapped; runtime_root=$runtimeFull; repo_root=$repoFull" |
        Out-Null
}

if (-not $SkipBridgeRead) {
    $readBridge = Join-Path $PSScriptRoot 'Read-AgentBridge.ps1'
    $bridgeStatus = Join-Path $PSScriptRoot 'Get-AgentBridgeStatus.ps1'
    & $readBridge -Agent $Agent -ShowClaims -Tail 80
    & $bridgeStatus -MaxUnresolved 15
}

[pscustomobject]@{
    agent        = $Agent
    repo_root    = $repoFull
    runtime_root = $runtimeFull
    run_id       = $RunId
    git_branch   = $gitBranch
    note         = 'Dot-source this script so env vars persist in the agent shell.'
}
