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

try {
    [void](New-Item -ItemType Directory -Path $runtimeRoot -Force -ErrorAction Stop)
    [void](New-Item -ItemType Directory -Path $worktree -Force -ErrorAction Stop)
    [System.IO.File]::WriteAllText($wakePath, 'wake')
    @'
$ErrorActionPreference = 'Stop'
$root = [string]$env:AGENT_BRIDGE_RUNTIME_ROOT
$agent = [string]$env:AGENT_BRIDGE_AGENT
if (-not $root) { throw 'missing AGENT_BRIDGE_RUNTIME_ROOT' }
if (-not $agent) { throw 'missing AGENT_BRIDGE_AGENT' }
$claims = Join-Path $root 'work_queue\claims'
[void](New-Item -ItemType Directory -Path $claims -Force -ErrorAction Stop)
$claimPath = Join-Path $claims 'consumer-heartbeat-smoke.json'
$claim = [ordered]@{
    task_id = 'consumer-heartbeat-smoke'
    agent = $agent
    claimed_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    last_heartbeat_utc = '2000-01-01T00:00:00Z'
    lease_seconds = 300
    mode = 'write'
    write_scope = @('smoke')
}
$claim | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $claimPath -Encoding UTF8
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

    $claimPath = Join-Path $runtimeRoot 'work_queue\claims\consumer-heartbeat-smoke.json'
    $claim = Get-Content -Raw -LiteralPath $claimPath -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
    Assert-True ([string]$claim.last_heartbeat_utc -ne '2000-01-01T00:00:00Z') 'heartbeat did not refresh claim lease during codex tick'
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
    Remove-Item Env:\BRIDGE_CONSUMER_SLOW_CHILD_PID_PATH -ErrorAction SilentlyContinue
    $tmpFull = [System.IO.Path]::GetFullPath($tmpRoot)
    $auditFull = [System.IO.Path]::GetFullPath($auditRoot)
    if ($tmpFull.StartsWith($auditFull, [System.StringComparison]::OrdinalIgnoreCase) -and
        (Test-Path -LiteralPath $tmpFull)) {
        Remove-Item -LiteralPath $tmpFull -Recurse -Force -ErrorAction SilentlyContinue
    }
}
