#requires -Version 5.1
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Assert-True {
    param(
        [Parameter(Mandatory)] [bool] $Condition,
        [Parameter(Mandatory)] [string] $Message
    )
    if (-not $Condition) { throw $Message }
}

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$auditRoot = Join-Path $repoRoot '.codex-audit'
$tmpRoot = Join-Path $auditRoot ("bridge-consumer-smoke-{0}" -f ([guid]::NewGuid().ToString('N')))
$runtimeRoot = Join-Path $tmpRoot '.agent-bridge'
$worktree = Join-Path $tmpRoot 'codex-tools-1-worktree'
$wakePath = Join-Path $runtimeRoot 'wake_codex-tools-1'
$fakeCodex = Join-Path $tmpRoot 'fake-codex.ps1'
    $slowCodex = Join-Path $tmpRoot 'slow-codex.ps1'
    $slowChildPid = Join-Path $tmpRoot 'slow-child.pid'
    $promptCapture = Join-Path $tmpRoot 'consumer-prompt.txt'
    $argumentsCapture = Join-Path $tmpRoot 'consumer-arguments.json'
    $invokeSpecCountCapture = Join-Path $tmpRoot 'consumer-invoke-spec-count.txt'
    $transportEnvironmentCapture = Join-Path $tmpRoot 'consumer-transport-environment.txt'
    $initialHeartbeatCapture = Join-Path $tmpRoot 'consumer-initial-heartbeat.txt'
    $identityIsolation = Join-Path $PSScriptRoot 'BridgeSmokeIdentityIsolation.ps1'
. $identityIsolation
$identitySnapshot = Enter-BridgeSmokeIdentityIsolation

try {
    [void](New-Item -ItemType Directory -Path $runtimeRoot -Force -ErrorAction Stop)
    [void](New-Item -ItemType Directory -Path $worktree -Force -ErrorAction Stop)
    [System.IO.File]::WriteAllText($wakePath, 'wake')
    @'
$ErrorActionPreference = 'Stop'
$root = [string]$env:AGENT_BRIDGE_RUNTIME_ROOT
$agent = [string]$env:AGENT_BRIDGE_AGENT
$promptPath = [string]$env:BRIDGE_CONSUMER_PROMPT_PATH
$argumentsPath = [string]$env:BRIDGE_CONSUMER_ARGUMENTS_PATH
$invokeSpecCountPath = [string]$env:BRIDGE_CONSUMER_INVOKE_SPEC_COUNT_PATH
$transportEnvironmentPath = [string]$env:BRIDGE_CONSUMER_TRANSPORT_ENVIRONMENT_PATH
$consumerLogDir = [string]$env:BRIDGE_CONSUMER_TEST_LOG_DIR
$claimScript = [string]$env:BRIDGE_CONSUMER_CLAIM_SCRIPT
$initialHeartbeatPath = [string]$env:BRIDGE_CONSUMER_INITIAL_HEARTBEAT_PATH
$promptText = (@($input) -join [Environment]::NewLine)
if (-not $root) { throw 'missing AGENT_BRIDGE_RUNTIME_ROOT' }
if (-not $agent) { throw 'missing AGENT_BRIDGE_AGENT' }
if (-not $consumerLogDir) { throw 'missing BRIDGE_CONSUMER_TEST_LOG_DIR' }
if (-not $claimScript) { throw 'missing BRIDGE_CONSUMER_CLAIM_SCRIPT' }
if (-not $initialHeartbeatPath) { throw 'missing BRIDGE_CONSUMER_INITIAL_HEARTBEAT_PATH' }
if ($promptPath) {
    [System.IO.File]::WriteAllText($promptPath, $promptText)
}
if ($argumentsPath) {
    [System.IO.File]::WriteAllText(
        $argumentsPath,
        (@($args) | ConvertTo-Json -Depth 8 -Compress)
    )
}
if ($invokeSpecCountPath) {
    $invokeSpecCount = @(
        Get-ChildItem `
            -LiteralPath $consumerLogDir `
            -Filter '*.invoke.json' `
            -File `
            -ErrorAction SilentlyContinue
    ).Count
    [System.IO.File]::WriteAllText($invokeSpecCountPath, [string]$invokeSpecCount)
}
if ($transportEnvironmentPath) {
    $transportEnvironment = @(
        [string]$env:BRIDGE_CONSUMER_SPEC
        [string]$env:BRIDGE_CONSUMER_SPEC_B64
    ) -join ''
    [System.IO.File]::WriteAllText(
        $transportEnvironmentPath,
        $transportEnvironment
    )
}
& $claimScript `
    -Agent $agent `
    -TaskId 'consumer-heartbeat-smoke' `
    -Summary 'consumer heartbeat smoke' `
    -Mode write `
    -WriteScope @('smoke') `
    -LeaseSeconds 300 | Out-Null
$claimsDir = Join-Path $root 'work_queue\claims'
$matchingClaims = @(
    Get-ChildItem -LiteralPath $claimsDir -Filter '*.json' -File -ErrorAction Stop |
        ForEach-Object {
            Get-Content -Raw -LiteralPath $_.FullName -Encoding UTF8 |
                ConvertFrom-Json -ErrorAction Stop
        } |
        Where-Object { [string]$_.task_id -eq 'consumer-heartbeat-smoke' }
)
if (@($matchingClaims).Count -ne 1) {
    throw 'expected one owner-bound consumer heartbeat claim after claiming'
}
[System.IO.File]::WriteAllText(
    $initialHeartbeatPath,
    [string]$matchingClaims[0].last_heartbeat_utc
)
Start-Sleep -Milliseconds 2200
exit 0
'@ | Set-Content -LiteralPath $fakeCodex -Encoding UTF8
    @'
$pidPath = [string]$env:BRIDGE_CONSUMER_SLOW_CHILD_PID_PATH
$child = Start-Process -FilePath powershell -ArgumentList @('-NoProfile','-Command','Start-Sleep -Seconds 30') -WindowStyle Hidden -PassThru
if ($pidPath) {
    [System.IO.File]::WriteAllText($pidPath, [string]$child.Id)
}
Wait-Process -Id $child.Id
exit 0
'@ | Set-Content -LiteralPath $slowCodex -Encoding UTF8
    $env:BRIDGE_CONSUMER_SLOW_CHILD_PID_PATH = $slowChildPid
    $env:BRIDGE_CONSUMER_PROMPT_PATH = $promptCapture
    $env:BRIDGE_CONSUMER_ARGUMENTS_PATH = $argumentsCapture
    $env:BRIDGE_CONSUMER_INVOKE_SPEC_COUNT_PATH = $invokeSpecCountCapture
    $env:BRIDGE_CONSUMER_TRANSPORT_ENVIRONMENT_PATH = $transportEnvironmentCapture
    $env:BRIDGE_CONSUMER_TEST_LOG_DIR = Join-Path $worktree '.codex-audit\bridge-consumer'
    $env:BRIDGE_CONSUMER_CLAIM_SCRIPT = Join-Path $PSScriptRoot 'Claim-AgentTask.ps1'
    $env:BRIDGE_CONSUMER_INITIAL_HEARTBEAT_PATH = $initialHeartbeatCapture
    $unicodeModel = "m$([char]0x00F8)del-$([char]0x96EA)"

    $script = Join-Path $PSScriptRoot 'Start-AgentBridgeConsumerLoop.ps1'
    $result = @(& $script `
        -Agent codex-tools-1 `
        -AgentUuid '7a8af68d-20bc-4598-9953-23c5dd98b102' `
        -Role tools-tests `
        -Capabilities 'tools,tests,bridge_loop' `
        -RuntimeRoot $runtimeRoot `
        -Worktree $worktree `
        -Model 'test-model' `
        -Sandbox 'danger-full-access' `
        -ApprovalPolicy 'never' `
        -MaxIterations 1 `
        -DurationMinutes 0 `
        -PollSeconds 0 `
        -DryRun)

    Assert-True (@($result).Count -eq 1) 'expected exactly one dry-run iteration'
    $tick = $result[0]
    Assert-True ([bool]$tick.wake_consumed) 'expected wake file to be consumed'
    Assert-True (-not (Test-Path -LiteralPath $wakePath)) 'wake file still exists after consume'
    Assert-True ([bool]$tick.would_run_codex) 'expected default loop to run codex even in dry-run'
    Assert-True (-not [bool]$tick.ran_codex) 'dry-run must not invoke codex'
    Assert-True ([bool]$tick.heartbeat_enabled) 'heartbeat should be enabled by default for real codex ticks'
    Assert-True (-not [string]$tick.heartbeat_job_id) 'dry-run must not start heartbeat job'

    $args = @($tick.codex_args)
    Assert-True ($args[0] -eq '--ask-for-approval') 'approval flag must be its own argument'
    Assert-True ($args[1] -eq 'never') 'approval policy must be its own argument'
    Assert-True ($args[2] -eq 'exec') 'codex exec subcommand missing'
    Assert-True ($args[3] -eq '-C') 'worktree flag missing'
    Assert-True ($args[4] -eq $worktree) 'worktree path must be a single argument'
    Assert-True (($args -contains '--sandbox') -and ($args -contains 'danger-full-access')) 'sandbox value must stay intact'
    Assert-True (($args -contains '--model') -and ($args -contains 'test-model')) 'model argument missing'

    $wakeOnly = @(& $script `
        -Agent codex-tools-1 `
        -RuntimeRoot $runtimeRoot `
        -Worktree $worktree `
        -MaxIterations 1 `
        -DurationMinutes 0 `
        -PollSeconds 0 `
        -WakeOnly `
        -DryRun)
    Assert-True (@($wakeOnly).Count -eq 1) 'expected one wake-only dry-run iteration'
    Assert-True (-not [bool]$wakeOnly[0].wake_consumed) 'unexpected wake consumed in no-wake check'
    Assert-True (-not [bool]$wakeOnly[0].would_run_codex) 'WakeOnly must skip codex when no wake exists'
    Assert-True (-not (@($wakeOnly[0].codex_args) -contains '--model')) 'default loop must use Codex config model unless a model is explicit'
    Assert-True ((@($wakeOnly[0].codex_args) -contains 'workspace-write')) 'default sandbox must be workspace-write'
    Assert-True (@($wakeOnly[0].codex_args)[1] -eq 'on-request') 'default approval policy must be on-request'

    [System.IO.File]::WriteAllText($wakePath, 'wake')
    $liveRun = @(& $script `
        -Agent codex-tools-1 `
        -RuntimeRoot $runtimeRoot `
        -Worktree $worktree `
        -CodexCommand $fakeCodex `
        -Model $unicodeModel `
        -HeartbeatIntervalSeconds 1 `
        -HeartbeatMaxIdleWithoutClaimIterations 5 `
        -MaxIterations 1 `
        -DurationMinutes 0 `
        -PollSeconds 0)
    Assert-True (@($liveRun).Count -eq 1) 'expected one live fake-codex iteration'
    Assert-True ([bool]$liveRun[0].ran_codex) 'fake codex should have run'
    Assert-True ($liveRun[0].exit_code -eq 0) 'fake codex should exit 0'
    Assert-True (-not [bool]$liveRun[0].codex_timed_out) 'fake codex should not time out'
    Assert-True ($liveRun[0].codex_timeout_seconds -eq 600) 'default codex timeout should be 600 seconds'
    Assert-True (-not [string]$liveRun[0].status_event_error) 'status event write should succeed'
    Assert-True ([string]$liveRun[0].heartbeat_job_id -ne '') 'real codex tick should start heartbeat job'
    $capturedPrompt = @(
        Get-Content -Raw -LiteralPath $promptCapture -Encoding UTF8
    ) -join "`n"
    Assert-True `
        -Condition $capturedPrompt.Contains('bridge_next_action.py --agent codex-tools-1 --json') `
        -Message 'prompt missing bound bridge_next_action command'
    Assert-True `
        -Condition $capturedPrompt.Contains('agent_next_task.py --agent codex-tools-1 --json') `
        -Message 'prompt missing bound agent_next_task command'
    Assert-True `
        -Condition (-not $capturedPrompt.Contains('--agent codex-lead-1')) `
        -Message 'tools prompt must not infer the lead lane'
    Assert-True `
        -Condition ((Get-Content -Raw -LiteralPath $invokeSpecCountCapture -Encoding UTF8) -eq '0') `
        -Message 'consumer exposed a writable *.invoke.json while the child was running'
    $transportEnvironmentText = [string](
        Get-Content -Raw -LiteralPath $transportEnvironmentCapture -Encoding UTF8
    )
    Assert-True `
        -Condition ([string]::IsNullOrEmpty($transportEnvironmentText)) `
        -Message 'consumer transport environment leaked into the Codex command'
    $capturedArgumentsPayload = (
        Get-Content -Raw -LiteralPath $argumentsCapture -Encoding UTF8 |
            ConvertFrom-Json -ErrorAction Stop
    )
    $capturedArguments = @($capturedArgumentsPayload)
    if ($capturedArguments.Count -eq 1 -and
        $capturedArguments[0] -is [System.Array]) {
        $capturedArguments = @(
            $capturedArguments[0] | ForEach-Object { $_ }
        )
    }
    Assert-True `
        -Condition (@($capturedArguments) -contains $unicodeModel) `
        -Message (
            "UTF-8/base64 transport did not preserve the Unicode model argument; " +
            "expected=$unicodeModel actual=$(@($capturedArguments) -join '|')"
        )

    $claimsDir = Join-Path $runtimeRoot 'work_queue\claims'
    $matchingClaims = @(
        Get-ChildItem -LiteralPath $claimsDir -Filter '*.json' -File -ErrorAction Stop |
            ForEach-Object {
                Get-Content -Raw -LiteralPath $_.FullName -Encoding UTF8 |
                    ConvertFrom-Json -ErrorAction Stop
            } |
            Where-Object { [string]$_.task_id -eq 'consumer-heartbeat-smoke' }
    )
    Assert-True (@($matchingClaims).Count -eq 1) 'expected one owner-bound consumer heartbeat claim'
    $claim = $matchingClaims[0]
    $initialHeartbeatText = Get-Content -Raw -LiteralPath $initialHeartbeatCapture -Encoding UTF8
    $finalHeartbeatText = [string]$claim.last_heartbeat_utc
    $dateStyles = (
        [System.Globalization.DateTimeStyles]::AssumeUniversal -bor
        [System.Globalization.DateTimeStyles]::AdjustToUniversal
    )
    $initialHeartbeat = [DateTimeOffset]::MinValue
    $finalHeartbeat = [DateTimeOffset]::MinValue
    Assert-True `
        -Condition ([DateTimeOffset]::TryParse(
            $initialHeartbeatText,
            [System.Globalization.CultureInfo]::InvariantCulture,
            $dateStyles,
            [ref]$initialHeartbeat
        )) `
        -Message "initial claim heartbeat is not a valid timestamp: $initialHeartbeatText"
    Assert-True `
        -Condition ([DateTimeOffset]::TryParse(
            $finalHeartbeatText,
            [System.Globalization.CultureInfo]::InvariantCulture,
            $dateStyles,
            [ref]$finalHeartbeat
        )) `
        -Message "final claim heartbeat is not a valid timestamp: $finalHeartbeatText"
    Assert-True `
        -Condition ($finalHeartbeat -gt $initialHeartbeat) `
        -Message (
            'heartbeat did not advance claim lease during codex tick; ' +
            "initial=$initialHeartbeatText final=$finalHeartbeatText"
        )
    $eventsPath = Join-Path $runtimeRoot 'shared\events.jsonl'
    $events = @(Get-Content -LiteralPath $eventsPath -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop)
    Assert-True (@($events | Where-Object { $_.status -eq 'consumer_tick_started' }).Count -ge 1) 'tick start status event missing'
    Assert-True (@($events | Where-Object { $_.status -eq 'consumer_tick_finished' }).Count -ge 1) 'tick finish status event missing'

    [System.IO.File]::WriteAllText($wakePath, 'wake')
    $timeoutRun = @(& $script `
        -Agent codex-tools-1 `
        -RuntimeRoot $runtimeRoot `
        -Worktree $worktree `
        -CodexCommand $slowCodex `
        -CodexTimeoutSeconds 1 `
        -SkipHeartbeatDuringCodex `
        -MaxIterations 1 `
        -DurationMinutes 0 `
        -PollSeconds 0)
    Assert-True (@($timeoutRun).Count -eq 1) 'expected one timeout fake-codex iteration'
    Assert-True ([bool]$timeoutRun[0].ran_codex) 'slow fake codex should have run'
    Assert-True ([bool]$timeoutRun[0].codex_timed_out) 'slow fake codex should time out'
    Assert-True ($timeoutRun[0].exit_code -eq 124) 'timeout should use exit code 124'
    Assert-True (-not [string]$timeoutRun[0].status_event_error) 'timeout status event write should succeed'
    Assert-True ((Get-Content -Raw -LiteralPath $timeoutRun[0].log_path) -match 'timed out after 1 seconds') 'timeout log missing evidence'
    Assert-True (Test-Path -LiteralPath $slowChildPid -PathType Leaf) 'slow fake codex did not record child pid'
    $childPid = [int](Get-Content -Raw -LiteralPath $slowChildPid -Encoding UTF8)
    Start-Sleep -Milliseconds 500
    Assert-True (-not (Get-Process -Id $childPid -ErrorAction SilentlyContinue)) 'timeout did not clean slow child process'
    $events = @(Get-Content -LiteralPath $eventsPath -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop)
    Assert-True (@($events | Where-Object { $_.status -eq 'consumer_tick_timed_out' }).Count -ge 1) 'tick timeout status event missing'

    'Bridge consumer loop smoke passed.'
} finally {
    Exit-BridgeSmokeIdentityIsolation -Snapshot $identitySnapshot
    Remove-Item Env:\BRIDGE_CONSUMER_SLOW_CHILD_PID_PATH -ErrorAction SilentlyContinue
    Remove-Item Env:\BRIDGE_CONSUMER_PROMPT_PATH -ErrorAction SilentlyContinue
    Remove-Item Env:\BRIDGE_CONSUMER_ARGUMENTS_PATH -ErrorAction SilentlyContinue
    Remove-Item Env:\BRIDGE_CONSUMER_INVOKE_SPEC_COUNT_PATH -ErrorAction SilentlyContinue
    Remove-Item Env:\BRIDGE_CONSUMER_TRANSPORT_ENVIRONMENT_PATH -ErrorAction SilentlyContinue
    Remove-Item Env:\BRIDGE_CONSUMER_TEST_LOG_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:\BRIDGE_CONSUMER_CLAIM_SCRIPT -ErrorAction SilentlyContinue
    Remove-Item Env:\BRIDGE_CONSUMER_INITIAL_HEARTBEAT_PATH -ErrorAction SilentlyContinue
    $tmpFull = [System.IO.Path]::GetFullPath($tmpRoot)
    $auditFull = [System.IO.Path]::GetFullPath($auditRoot)
    if ($tmpFull.StartsWith($auditFull, [System.StringComparison]::OrdinalIgnoreCase) -and
        (Test-Path -LiteralPath $tmpFull)) {
        Remove-Item -LiteralPath $tmpFull -Recurse -Force -ErrorAction SilentlyContinue
    }
}
