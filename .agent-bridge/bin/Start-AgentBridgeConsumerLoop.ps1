#requires -Version 5.1
<#
.SYNOPSIS
    Consume bridge wake events and run one bounded Codex bridge tick.

.DESCRIPTION
    Watch-Bridge.ps1 is intentionally a wake sentinel writer only: it creates
    wake_<agent> when targeted bridge traffic arrives. This script is the
    companion consumer loop that checks and consumes that sentinel, then invokes
    `codex exec` with a short bridge prompt.

    By default the loop is bounded to ten minutes but it still runs every poll
    interval even when no wake file exists. That default keeps bridge work
    moving instead of waiting forever for the next sentinel. Use -WakeOnly when
    a caller wants event-only execution.

    The Codex CLI arguments are held in a PowerShell array so long values such
    as `danger-full-access` and worktree paths cannot be split by line wrapping.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateScript({ $_ -cmatch '^[a-z][a-z0-9_-]{1,32}$' })]
    [string] $Agent,

    [string] $AgentUuid = '',
    [string] $Role = '',
    [string[]] $Capabilities = @(),

    [string] $RuntimeRoot = 'C:\Python\project2-master\.agent-bridge',
    [string] $Worktree = '',
    [string] $WorktreeBase = 'C:\Python\waggledance-agent-worktrees',
    [string] $WorktreeFilter = '',

    [string] $Model = '',
    [ValidateSet('read-only','workspace-write','danger-full-access')]
    [string] $Sandbox = 'danger-full-access',
    [ValidateSet('untrusted','on-failure','on-request','never')]
    [string] $ApprovalPolicy = 'never',
    [string] $CodexCommand = '',

    [int] $PollSeconds = 60,
    [int] $DurationMinutes = 10,
    [int] $MaxIterations = 0,
    [switch] $Forever,
    [switch] $WakeOnly,
    [switch] $DryRun,

    [string] $Prompt = '',
    [string] $LogDir = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Resolve-FullPath {
    param([Parameter(Mandatory)] [string] $Path)
    return [System.IO.Path]::GetFullPath($Path)
}

function Normalize-Capabilities {
    param([string[]] $Values)
    return @(
        @($Values) |
            ForEach-Object { [string]$_ -split '[,;]' } |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ }
    )
}

function Resolve-AgentWorktree {
    param(
        [string] $ExplicitWorktree,
        [string] $Base,
        [string] $Filter,
        [string] $AgentName
    )

    if ($ExplicitWorktree) {
        $full = Resolve-FullPath $ExplicitWorktree
        if (-not (Test-Path -LiteralPath $full -PathType Container)) {
            throw "worktree does not exist: $full"
        }
        return $full
    }

    $baseFull = Resolve-FullPath $Base
    if (-not (Test-Path -LiteralPath $baseFull -PathType Container)) {
        throw "worktree base does not exist: $baseFull"
    }

    $filters = if ($Filter) { @($Filter) } else { @("$AgentName-*session*", "$AgentName-*") }
    foreach ($effectiveFilter in $filters) {
        $dirs = @(
            Get-ChildItem -LiteralPath $baseFull -Directory -Filter $effectiveFilter -ErrorAction Stop |
                Sort-Object LastWriteTime -Descending
        )
        if (@($dirs).Count -gt 0) {
            return $dirs[0].FullName
        }
    }
    throw "no worktree under $baseFull matching filters $($filters -join ', ')"
}

function Resolve-CodexCommand {
    param([string] $Command)

    if ($Command) {
        $resolved = Get-Command $Command -ErrorAction SilentlyContinue
        if ($resolved) { return $resolved.Source }
        return $Command
    }

    $isWindowsHost = [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT
    $candidates = if ($isWindowsHost) {
        @('codex.cmd', 'codex.exe', 'codex')
    } else {
        @('codex')
    }

    foreach ($candidate in $candidates) {
        $resolved = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($resolved) { return $resolved.Source }
    }
    throw 'could not resolve Codex CLI command'
}

if ($PollSeconds -lt 0) { throw 'PollSeconds must be >= 0' }
if ($DurationMinutes -lt 0) { throw 'DurationMinutes must be >= 0' }
if ((-not $Forever) -and $DurationMinutes -eq 0 -and $MaxIterations -le 0) {
    throw 'Use -Forever or set -MaxIterations when DurationMinutes is 0'
}
if ($Role -and $Role -notmatch '^[a-z][a-z0-9_-]{1,32}$') {
    throw 'role must match ^[a-z][a-z0-9_-]{1,32}$'
}
if ($AgentUuid -and $AgentUuid -notmatch '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$') {
    throw 'agent_uuid must be a UUID'
}

if (-not $AgentUuid -and $env:AGENT_BRIDGE_AGENT_UUID) {
    $AgentUuid = [string]$env:AGENT_BRIDGE_AGENT_UUID
}
if (-not $Role -and $env:AGENT_BRIDGE_ROLE) {
    $Role = [string]$env:AGENT_BRIDGE_ROLE
}
if (@($Capabilities).Count -eq 0 -and $env:AGENT_BRIDGE_CAPABILITIES) {
    $Capabilities = @([string]$env:AGENT_BRIDGE_CAPABILITIES)
}
$Capabilities = Normalize-Capabilities -Values $Capabilities
foreach ($capability in @($Capabilities)) {
    if ($capability -notmatch '^[a-z][a-z0-9_.:-]{1,64}$') {
        throw "capability must match ^[a-z][a-z0-9_.:-]{1,64}$"
    }
}

$runtimeFull = Resolve-FullPath $RuntimeRoot
if (-not (Test-Path -LiteralPath $runtimeFull -PathType Container)) {
    [void](New-Item -ItemType Directory -Path $runtimeFull -Force -ErrorAction Stop)
}

$worktreeFull = Resolve-AgentWorktree `
    -ExplicitWorktree $Worktree `
    -Base $WorktreeBase `
    -Filter $WorktreeFilter `
    -AgentName $Agent

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $LogDir) {
    $LogDir = Join-Path $worktreeFull '.codex-audit\bridge-consumer'
}
$logFull = Resolve-FullPath $LogDir
if (-not (Test-Path -LiteralPath $logFull -PathType Container)) {
    [void](New-Item -ItemType Directory -Path $logFull -Force -ErrorAction Stop)
}

if (-not $Prompt) {
    $Prompt = @(
        'Read the bridge via tools\bridge_next_action.py.'
        'Handle any open incoming request first.'
        'If there is no incoming request, run tools\agent_next_task.py and claim the highest-value unblocked non-overlapping task you can safely advance.'
        'Do one bounded useful slice: implement, test, review, or write bridge evidence as appropriate.'
        'Write bridge events/status/tests as needed, release any completed claim, then stop.'
        'Do not wait for operator input unless the bridge or repo evidence shows a protected-path, secret, destructive, external-payment, legal/security-sensitive, or unresolved write-scope conflict stop condition.'
    ) -join ' '
}

$testWake = Join-Path $PSScriptRoot 'Test-BridgeWake.ps1'
if (-not (Test-Path -LiteralPath $testWake -PathType Leaf)) {
    throw "missing wake consumer helper: $testWake"
}

$env:AGENT_BRIDGE_RUNTIME_ROOT = $runtimeFull
$env:AGENT_BRIDGE_AGENT = $Agent
if ($AgentUuid) { $env:AGENT_BRIDGE_AGENT_UUID = $AgentUuid }
if ($Role) { $env:AGENT_BRIDGE_ROLE = $Role }
if (@($Capabilities).Count -gt 0) {
    $env:AGENT_BRIDGE_CAPABILITIES = (@($Capabilities) -join ',')
}
if (-not $Model -and $env:AGENT_BRIDGE_CODEX_MODEL) {
    $Model = [string]$env:AGENT_BRIDGE_CODEX_MODEL
}

$codexArgs = @(
    '--ask-for-approval'
    $ApprovalPolicy
    'exec'
    '-C'
    $worktreeFull
)
if ($Model) {
    $codexArgs += @('--model', $Model)
}
$codexArgs += @('--sandbox', $Sandbox, '-')
$codexCommandResolved = if ($DryRun) {
    if ($CodexCommand) { $CodexCommand } else { 'codex.cmd' }
} else {
    Resolve-CodexCommand -Command $CodexCommand
}

$endTime = $null
if ((-not $Forever) -and $DurationMinutes -gt 0) {
    $endTime = (Get-Date).AddMinutes($DurationMinutes)
}

$iteration = 0
while ($true) {
    if ($MaxIterations -gt 0 -and $iteration -ge $MaxIterations) { break }
    if ($endTime -and (Get-Date) -ge $endTime) { break }

    $iteration += 1
    $wakeRaw = @(& $testWake -Agent $Agent -RuntimeRoot $runtimeFull)
    $wakeConsumed = $false
    foreach ($item in $wakeRaw) {
        if ($item -is [bool]) {
            $wakeConsumed = $wakeConsumed -or [bool]$item
        } elseif ([string]$item -match '^(?i:true)$') {
            $wakeConsumed = $true
        }
    }

    $shouldRun = (-not $WakeOnly) -or $wakeConsumed
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
    $logPath = Join-Path $logFull ("{0}-{1:000}-{2}.log" -f $Agent, $iteration, $stamp)
    $exitCode = $null
    $errorText = ''

    if ($shouldRun -and -not $DryRun) {
        try {
            $Prompt | & $codexCommandResolved @codexArgs *> $logPath
            $exitCode = $LASTEXITCODE
        } catch {
            $exitCode = 1
            $errorText = $_.Exception.Message
            $errorText | Out-File -LiteralPath $logPath -Encoding UTF8 -Append
        }
    }

    [pscustomobject]@{
        timestamp_utc     = (Get-Date).ToUniversalTime().ToString('o')
        agent             = $Agent
        iteration         = $iteration
        worktree          = $worktreeFull
        runtime_root      = $runtimeFull
        wake_consumed     = $wakeConsumed
        wake_only         = [bool]$WakeOnly
        dry_run           = [bool]$DryRun
        would_run_codex   = $shouldRun
        ran_codex         = ($shouldRun -and -not $DryRun)
        codex_command     = $codexCommandResolved
        codex_args        = @($codexArgs)
        log_path          = $logPath
        exit_code         = $exitCode
        error             = $errorText
    }

    if ($MaxIterations -gt 0 -and $iteration -ge $MaxIterations) { break }
    if ($endTime -and (Get-Date) -ge $endTime) { break }
    if ($PollSeconds -gt 0) {
        Start-Sleep -Seconds $PollSeconds
    }
}
