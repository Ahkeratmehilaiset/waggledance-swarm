#requires -Version 5.1
<#
.SYNOPSIS
    Consume bridge wake events and run one bounded Codex bridge tick.

.DESCRIPTION
    Watch-Bridge.ps1 is intentionally a wake sentinel writer only: it creates
    wake_<agent> when targeted bridge traffic arrives. This script is the
    companion consumer loop that checks and consumes that sentinel, then invokes
    `codex exec` with a short bridge prompt. Real Codex ticks are wrapped with
    Start-BridgeHeartbeat.ps1 so active bridge claims do not stale-release while
    the model is still implementing or running tests.

    By default the loop is bounded to ten minutes but it still runs every poll
    interval even when no wake file exists. That default keeps bridge work
    moving instead of waiting forever for the next sentinel. Use -WakeOnly when
    a caller wants event-only execution.

    The default Codex sandbox is workspace-write with on-request approvals.
    Operators can still opt into broader authority explicitly at launch time.
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
    [string] $Sandbox = 'workspace-write',
    [ValidateSet('untrusted','on-failure','on-request','never')]
    [string] $ApprovalPolicy = 'on-request',
    [string] $CodexCommand = '',

    [int] $PollSeconds = 60,
    [int] $DurationMinutes = 10,
    [int] $MaxIterations = 0,
    [int] $CodexTimeoutSeconds = 600,
    [int] $HeartbeatIntervalSeconds = 60,
    [int] $HeartbeatMaxIdleWithoutClaimIterations = 10,
    [switch] $Forever,
    [switch] $WakeOnly,
    [switch] $SkipHeartbeatDuringCodex,
    [switch] $DryRun,

    [string] $Prompt = '',
    [string] $ImagePath = '',
    [string] $LogDir = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$sessionIdentity = Join-Path $PSScriptRoot 'AgentBridgeSessionIdentity.ps1'
. $sessionIdentity
Assert-AgentBridgeSessionIdentity -RequestedAgent $Agent

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

function Start-ConsumerHeartbeatJob {
    param(
        [Parameter(Mandatory)] [string] $ScriptPath,
        [Parameter(Mandatory)] [string] $AgentName,
        [Parameter(Mandatory)] [string] $RuntimeRootPath,
        [string] $RoleName,
        [string] $AgentUuidValue,
        [string] $CapabilityText,
        [int] $IntervalSeconds,
        [int] $MaxIdleWithoutClaimIterations,
        [int] $Iteration
    )

    if (-not (Test-Path -LiteralPath $ScriptPath -PathType Leaf)) {
        throw "missing heartbeat helper: $ScriptPath"
    }

    $jobName = "agent-bridge-consumer-heartbeat-$AgentName-$PID-$Iteration"
    return Start-Job -Name $jobName -ScriptBlock {
        param(
            $scriptPathArg,
            $agentArg,
            $runtimeArg,
            $roleArg,
            $agentUuidArg,
            $capabilitiesTextArg,
            $intervalSecondsArg,
            $maxIdleArg
        )
        & $scriptPathArg `
            -Agent $agentArg `
            -RuntimeRoot $runtimeArg `
            -Role $roleArg `
            -AgentUuid $agentUuidArg `
            -Capabilities @($capabilitiesTextArg) `
            -IntervalSeconds $intervalSecondsArg `
            -MaxIdleWithoutClaimIterations $maxIdleArg
    } -ArgumentList `
        $ScriptPath,
        $AgentName,
        $RuntimeRootPath,
        $RoleName,
        $AgentUuidValue,
        $CapabilityText,
        $IntervalSeconds,
        $MaxIdleWithoutClaimIterations
}

function Stop-ConsumerHeartbeatJob {
    param(
        [AllowNull()] $Job,
        [Parameter(Mandatory)] [string] $LogPath
    )

    if ($null -eq $Job) { return }

    try {
        Stop-Job -Job $Job -ErrorAction SilentlyContinue | Out-Null
        $heartbeatOutput = Receive-Job -Job $Job -ErrorAction SilentlyContinue | Out-String
        if ($heartbeatOutput.Trim()) {
            @(
                ''
                '--- bridge heartbeat job output ---'
                $heartbeatOutput.TrimEnd()
            ) | Out-File -LiteralPath $LogPath -Encoding UTF8 -Append
        }
    } finally {
        Remove-Job -Job $Job -Force -ErrorAction SilentlyContinue | Out-Null
    }
}

function Stop-ProcessTree {
    param([int] $ProcessId)

    if ($ProcessId -le 0 -or $ProcessId -eq $PID) { return }

    $children = @()
    try {
        $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" -ErrorAction Stop)
    } catch {
        $children = @()
    }
    foreach ($child in @($children)) {
        Stop-ProcessTree -ProcessId ([int]$child.ProcessId)
    }

    try {
        $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
        if ($process) {
            Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
        }
    } catch {
        # Best-effort cleanup; the caller still records the timeout.
    }
}

function Resolve-PowerShellHostCommand {
    try {
        $currentProcess = Get-Process -Id $PID -ErrorAction Stop
        if ($currentProcess.Path) {
            return $currentProcess.Path
        }
    } catch {
    }

    $candidate = if ($PSVersionTable.PSEdition -eq 'Core') { 'pwsh' } else { 'powershell' }
    $resolved = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($resolved) { return $resolved.Source }
    return $candidate
}

function Invoke-CodexTick {
    param(
        [Parameter(Mandatory)] [string] $Command,
        [Parameter(Mandatory)] [string[]] $Arguments,
        [Parameter(Mandatory)] [string] $PromptText,
        [Parameter(Mandatory)] [string] $LogPath,
        [int] $TimeoutSeconds
    )

    $timedOut = $false
    $exitCode = $null
    $errorText = ''
    $process = $null
    $specPath = "$LogPath.invoke.json"
    $wrapperStdout = ''
    $wrapperStderr = ''

    try {
        $encoding = New-Object System.Text.UTF8Encoding($false)
        $spec = [ordered]@{
            command = $Command
            arguments = @($Arguments)
            log_path = $LogPath
        }
        [System.IO.File]::WriteAllText($specPath, ($spec | ConvertTo-Json -Depth 8), $encoding)

        $wrapperCommand = @'
$ErrorActionPreference = 'Stop'
$specPath = [Environment]::GetEnvironmentVariable('BRIDGE_CONSUMER_SPEC', 'Process')
if (-not $specPath) { throw 'missing BRIDGE_CONSUMER_SPEC' }
$spec = Get-Content -Raw -LiteralPath $specPath -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
$command = [string]$spec.command
$arguments = @()
if ($null -ne $spec.arguments) {
    $arguments = @($spec.arguments) | ForEach-Object { [string]$_ }
}
$logPath = [string]$spec.log_path
$prompt = [Console]::In.ReadToEnd()
$prompt | & $command @arguments *> $logPath
if ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE }
if ($?) { exit 0 }
exit 1
'@
        $encodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($wrapperCommand))
        $hostCommand = Resolve-PowerShellHostCommand
        $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
        $startInfo.FileName = $hostCommand
        $startInfo.Arguments = "-NoProfile -ExecutionPolicy Bypass -EncodedCommand $encodedCommand"
        $startInfo.UseShellExecute = $false
        $startInfo.RedirectStandardInput = $true
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        $startInfo.CreateNoWindow = $true
        $startInfo.WorkingDirectory = (Get-Location).Path
        $startInfo.EnvironmentVariables['BRIDGE_CONSUMER_SPEC'] = $specPath

        $process = [System.Diagnostics.Process]::Start($startInfo)
        $process.StandardInput.Write($PromptText)
        $process.StandardInput.Close()

        $completed = $process.WaitForExit($TimeoutSeconds * 1000)
        if (-not $completed) {
            $timedOut = $true
            $exitCode = 124
            $errorText = "codex exec timed out after $TimeoutSeconds seconds"
            Stop-ProcessTree -ProcessId $process.Id
            if (-not $process.HasExited) {
                try { $process.Kill() } catch {}
                try { [void]$process.WaitForExit(5000) } catch {}
            }
        } else {
            $exitCode = [int]$process.ExitCode
        }
        if ($process.HasExited) {
            $wrapperStdout = $process.StandardOutput.ReadToEnd()
            $wrapperStderr = $process.StandardError.ReadToEnd()
        }
    } catch {
        $exitCode = 1
        $errorText = $_.Exception.Message
    } finally {
        if ($wrapperStdout.Trim()) {
            $wrapperStdout | Out-File -LiteralPath $LogPath -Encoding UTF8 -Append
        }
        if ($wrapperStderr.Trim()) {
            $wrapperStderr | Out-File -LiteralPath $LogPath -Encoding UTF8 -Append
        }
        if ($errorText) {
            $errorText | Out-File -LiteralPath $LogPath -Encoding UTF8 -Append
        }
        if ($null -ne $process) {
            $process.Dispose()
        }
        Remove-Item -LiteralPath $specPath -Force -ErrorAction SilentlyContinue
    }

    [pscustomobject]@{
        exit_code = $exitCode
        timed_out = $timedOut
        error = $errorText
    }
}

function Write-ConsumerStatusEvent {
    param(
        [Parameter(Mandatory)] [string] $AgentName,
        [Parameter(Mandatory)] [string] $Status,
        [Parameter(Mandatory)] [string] $Message,
        [int] $Iteration,
        [bool] $WakeConsumed,
        [bool] $HeartbeatEnabled,
        [string] $LogPath,
        [int] $TimeoutSeconds,
        [AllowNull()] $ExitCode,
        [bool] $TimedOut,
        [string] $ErrorText,
        [string] $RoleName,
        [string] $AgentUuidValue,
        [string[]] $CapabilityValues
    )

    $writer = Join-Path $PSScriptRoot 'Write-AgentEvent.ps1'
    if (-not (Test-Path -LiteralPath $writer -PathType Leaf)) {
        return "missing bridge event writer: $writer"
    }

    $payload = [ordered]@{
        schema = 'bridge.consumer_tick_status.v1'
        iteration = $Iteration
        wake_consumed = $WakeConsumed
        heartbeat_enabled = $HeartbeatEnabled
        log_path = $LogPath
        codex_timeout_seconds = $TimeoutSeconds
        exit_code = $ExitCode
        codex_timed_out = $TimedOut
    }
    if ($ErrorText) {
        $payload['error'] = $ErrorText
    }

    try {
        & $writer `
            -Agent $AgentName `
            -Type status `
            -TaskId "$AgentName/bridge-consumer-loop" `
            -Status $Status `
            -Message $Message `
            -Paths @('.agent-bridge/bin/Start-AgentBridgeConsumerLoop.ps1') `
            -Role $RoleName `
            -AgentUuid $AgentUuidValue `
            -Capabilities $CapabilityValues `
            -PayloadJson ($payload | ConvertTo-Json -Depth 8 -Compress) | Out-Null
    } catch {
        return $_.Exception.Message
    }
    return ''
}

if ($PollSeconds -lt 0) { throw 'PollSeconds must be >= 0' }
if ($DurationMinutes -lt 0) { throw 'DurationMinutes must be >= 0' }
if ($CodexTimeoutSeconds -lt 1) { throw 'CodexTimeoutSeconds must be >= 1' }
if ($HeartbeatIntervalSeconds -lt 1) { throw 'HeartbeatIntervalSeconds must be >= 1' }
if ($HeartbeatMaxIdleWithoutClaimIterations -lt 1) {
    throw 'HeartbeatMaxIdleWithoutClaimIterations must be >= 1'
}
if ((-not $Forever) -and $DurationMinutes -eq 0 -and $MaxIterations -le 0) {
    throw 'Use -Forever or set -MaxIterations when DurationMinutes is 0'
}
if ($Role -and $Role -notmatch '^[a-z][a-z0-9_-]{1,32}$') {
    throw 'role must match ^[a-z][a-z0-9_-]{1,32}$'
}
if ($AgentUuid -and $AgentUuid -notmatch '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$') {
    throw 'agent_uuid must be a UUID'
}

$imageFull = ''
if (-not [string]::IsNullOrWhiteSpace($ImagePath)) {
    if (-not [IO.Path]::IsPathRooted($ImagePath)) {
        throw 'ImagePath must be absolute'
    }
    $imageFull = Resolve-FullPath $ImagePath
    if ([IO.Path]::GetExtension($imageFull) -cne '.png') {
        throw 'ImagePath must name one PNG file'
    }
    if (-not (Test-Path -LiteralPath $imageFull -PathType Leaf)) {
        throw "ImagePath is missing: $imageFull"
    }
    $imageItem = Get-Item -LiteralPath $imageFull -Force -ErrorAction Stop
    if (($imageItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "ImagePath must not be a reparse point: $imageFull"
    }
    if ($imageItem.Length -lt 1 -or $imageItem.Length -gt 10MB) {
        throw 'ImagePath size must be between 1 byte and 10 MiB'
    }
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
$heartbeatScript = Join-Path $PSScriptRoot 'Start-BridgeHeartbeat.ps1'
$heartbeatDuringCodex = (
    (-not $SkipHeartbeatDuringCodex) -and
    $env:WAGGLE_BRIDGE_HEARTBEAT_ENABLED -ne '0'
)

$env:AGENT_BRIDGE_RUNTIME_ROOT = $runtimeFull
$env:AGENT_BRIDGE_AGENT = $Agent
if ([string]::IsNullOrWhiteSpace([string]$env:AGENT_BRIDGE_OWNER_SESSION_ID)) {
    Remove-Item Env:AGENT_BRIDGE_RUN_ID -ErrorAction SilentlyContinue
    Remove-Item Env:AGENT_BRIDGE_SESSION_ID -ErrorAction SilentlyContinue
    $ownerStamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ')
    [void](Initialize-AgentBridgeClaimOwnerContext `
        -SessionId "consumer-$Agent-$ownerStamp-$PID")
} else {
    # A consumer must never launch Codex under a tokenless or malformed
    # session. The child inherits this exact process-bound owner context.
    [void](Get-AgentBridgeClaimOwnerContext)
}
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
if ($imageFull) {
    $codexArgs += @('--image', $imageFull)
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
    $timedOut = $false
    $heartbeatJob = $null
    $heartbeatError = ''
    $statusEventError = ''

    if ($shouldRun -and -not $DryRun) {
        try {
            $statusEventError = Write-ConsumerStatusEvent `
                -AgentName $Agent `
                -Status 'consumer_tick_started' `
                -Message "Bridge consumer tick $iteration started for $Agent." `
                -Iteration $iteration `
                -WakeConsumed $wakeConsumed `
                -HeartbeatEnabled $heartbeatDuringCodex `
                -LogPath $logPath `
                -TimeoutSeconds $CodexTimeoutSeconds `
                -ExitCode $null `
                -TimedOut $false `
                -ErrorText '' `
                -RoleName $Role `
                -AgentUuidValue $AgentUuid `
                -CapabilityValues $Capabilities
            if ($heartbeatDuringCodex) {
                $heartbeatJob = Start-ConsumerHeartbeatJob `
                    -ScriptPath $heartbeatScript `
                    -AgentName $Agent `
                    -RuntimeRootPath $runtimeFull `
                    -RoleName $Role `
                    -AgentUuidValue $AgentUuid `
                    -CapabilityText (@($Capabilities) -join ',') `
                    -IntervalSeconds $HeartbeatIntervalSeconds `
                    -MaxIdleWithoutClaimIterations $HeartbeatMaxIdleWithoutClaimIterations `
                    -Iteration $iteration
            }
            $codexResult = Invoke-CodexTick `
                -Command $codexCommandResolved `
                -Arguments @($codexArgs) `
                -PromptText $Prompt `
                -LogPath $logPath `
                -TimeoutSeconds $CodexTimeoutSeconds
            $exitCode = $codexResult.exit_code
            $timedOut = [bool]$codexResult.timed_out
            $errorText = [string]$codexResult.error
        } catch {
            $exitCode = 1
            $errorText = $_.Exception.Message
            if ($null -eq $heartbeatJob -and $heartbeatDuringCodex) {
                $heartbeatError = $errorText
            }
            $errorText | Out-File -LiteralPath $logPath -Encoding UTF8 -Append
        } finally {
            Stop-ConsumerHeartbeatJob -Job $heartbeatJob -LogPath $logPath
            $finishStatus = if ($timedOut) {
                'consumer_tick_timed_out'
            } elseif ($exitCode -eq 0) {
                'consumer_tick_finished'
            } else {
                'consumer_tick_failed'
            }
            $finishError = Write-ConsumerStatusEvent `
                -AgentName $Agent `
                -Status $finishStatus `
                -Message "Bridge consumer tick $iteration finished for $Agent with exit_code=$exitCode." `
                -Iteration $iteration `
                -WakeConsumed $wakeConsumed `
                -HeartbeatEnabled $heartbeatDuringCodex `
                -LogPath $logPath `
                -TimeoutSeconds $CodexTimeoutSeconds `
                -ExitCode $exitCode `
                -TimedOut $timedOut `
                -ErrorText $errorText `
                -RoleName $Role `
                -AgentUuidValue $AgentUuid `
                -CapabilityValues $Capabilities
            if ($finishError) {
                $statusEventError = (@($statusEventError, $finishError) | Where-Object { $_ }) -join '; '
            }
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
        codex_timeout_seconds = $CodexTimeoutSeconds
        codex_timed_out  = $timedOut
        log_path          = $logPath
        exit_code         = $exitCode
        error             = $errorText
        heartbeat_enabled = [bool]$heartbeatDuringCodex
        heartbeat_job_id  = if ($null -ne $heartbeatJob) { [string]$heartbeatJob.Id } else { '' }
        heartbeat_error   = $heartbeatError
        status_event_error = $statusEventError
    }

    if ($MaxIterations -gt 0 -and $iteration -ge $MaxIterations) { break }
    if ($endTime -and (Get-Date) -ge $endTime) { break }
    if ($PollSeconds -gt 0) {
        Start-Sleep -Seconds $PollSeconds
    }
}
