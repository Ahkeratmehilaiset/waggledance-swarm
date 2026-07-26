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
$identityCapture = Join-Path $tmpRoot 'fake-codex-identity.json'
$timeoutIdentityCapture = Join-Path $tmpRoot 'slow-codex-identity.json'
$agentUuid = '7a8af68d-20bc-4598-9953-23c5dd98b102'
$role = 'tools-tests'
$capabilities = 'tools,tests,bridge_loop'
$protectedEnvironmentNames = @(
    'AGENT_BRIDGE_RUNTIME_ROOT'
    'AGENT_BRIDGE_AGENT'
    'AGENT_BRIDGE_AGENT_UUID'
    'AGENT_BRIDGE_ROLE'
    'AGENT_BRIDGE_CAPABILITIES'
    'AGENT_BRIDGE_RUN_ID'
    'AGENT_BRIDGE_SESSION_ID'
    'AGENT_BRIDGE_HEARTBEAT_JOB'
    'AGENT_BRIDGE_WAKE_JOB'
    'WD_AGENT_PROFILE'
    'WD_AGENT_PROMPT_FILE'
    'SESSION_ID'
)
$poisonEnvironment = [ordered]@{
    AGENT_BRIDGE_RUNTIME_ROOT = 'C:\poisoned-lead-runtime'
    AGENT_BRIDGE_AGENT = 'codex-lead-1'
    AGENT_BRIDGE_AGENT_UUID = 'd3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101'
    AGENT_BRIDGE_ROLE = 'lead-impl'
    AGENT_BRIDGE_CAPABILITIES = 'implementation,admin'
    AGENT_BRIDGE_RUN_ID = 'codex-lead-1-inherited-run'
    AGENT_BRIDGE_SESSION_ID = 'codex-lead-1-inherited-session'
    AGENT_BRIDGE_HEARTBEAT_JOB = '31337'
    AGENT_BRIDGE_WAKE_JOB = '31338'
    WD_AGENT_PROFILE = 'codex-lead'
    WD_AGENT_PROMPT_FILE = 'C:\poisoned\codex-lead-1.md'
    SESSION_ID = 'codex-lead-1-generic-session'
}
$savedEnvironment = [ordered]@{}
foreach ($name in @($protectedEnvironmentNames)) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable(
        $name,
        [EnvironmentVariableTarget]::Process
    )
}
$smokeEnvironmentNames = @(
    'BRIDGE_CONSUMER_SLOW_CHILD_PID_PATH'
    'BRIDGE_CONSUMER_IDENTITY_CAPTURE_PATH'
    'BRIDGE_CONSUMER_TIMEOUT_IDENTITY_CAPTURE_PATH'
)
$savedSmokeEnvironment = [ordered]@{}
foreach ($name in @($smokeEnvironmentNames)) {
    $savedSmokeEnvironment[$name] = [Environment]::GetEnvironmentVariable(
        $name,
        [EnvironmentVariableTarget]::Process
    )
}

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
$capturePath = [string]$env:BRIDGE_CONSUMER_IDENTITY_CAPTURE_PATH
if (-not $capturePath) { throw 'missing BRIDGE_CONSUMER_IDENTITY_CAPTURE_PATH' }
$protectedNames = @(
    'AGENT_BRIDGE_RUNTIME_ROOT',
    'AGENT_BRIDGE_AGENT',
    'AGENT_BRIDGE_AGENT_UUID',
    'AGENT_BRIDGE_ROLE',
    'AGENT_BRIDGE_CAPABILITIES',
    'AGENT_BRIDGE_RUN_ID',
    'AGENT_BRIDGE_SESSION_ID',
    'AGENT_BRIDGE_HEARTBEAT_JOB',
    'AGENT_BRIDGE_WAKE_JOB',
    'WD_AGENT_PROFILE',
    'WD_AGENT_PROMPT_FILE',
    'SESSION_ID'
)
$capture = [ordered]@{
    prompt = (@($input) -join [Environment]::NewLine)
}
foreach ($name in $protectedNames) {
    $capture[$name] = [Environment]::GetEnvironmentVariable(
        $name,
        [EnvironmentVariableTarget]::Process
    )
}
$encoding = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText(
    $capturePath,
    ($capture | ConvertTo-Json -Depth 8),
    $encoding
)
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
$heartbeatDeadline = (Get-Date).AddSeconds(10)
do {
    Start-Sleep -Milliseconds 250
    $currentClaim = Get-Content -Raw -LiteralPath $claimPath -Encoding UTF8 |
        ConvertFrom-Json -ErrorAction Stop
    if ([string]$currentClaim.last_heartbeat_utc -ne '2000-01-01T00:00:00Z') {
        break
    }
} while ((Get-Date) -lt $heartbeatDeadline)
exit 0
'@ | Set-Content -LiteralPath $fakeCodex -Encoding UTF8
    @'
$capturePath = [string]$env:BRIDGE_CONSUMER_TIMEOUT_IDENTITY_CAPTURE_PATH
if (-not $capturePath) { throw 'missing BRIDGE_CONSUMER_TIMEOUT_IDENTITY_CAPTURE_PATH' }
$protectedNames = @(
    'AGENT_BRIDGE_RUNTIME_ROOT',
    'AGENT_BRIDGE_AGENT',
    'AGENT_BRIDGE_AGENT_UUID',
    'AGENT_BRIDGE_ROLE',
    'AGENT_BRIDGE_CAPABILITIES',
    'AGENT_BRIDGE_RUN_ID',
    'AGENT_BRIDGE_SESSION_ID',
    'AGENT_BRIDGE_HEARTBEAT_JOB',
    'AGENT_BRIDGE_WAKE_JOB',
    'WD_AGENT_PROFILE',
    'WD_AGENT_PROMPT_FILE',
    'SESSION_ID'
)
$capture = [ordered]@{}
foreach ($name in $protectedNames) {
    $capture[$name] = [Environment]::GetEnvironmentVariable(
        $name,
        [EnvironmentVariableTarget]::Process
    )
}
$encoding = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText(
    $capturePath,
    ($capture | ConvertTo-Json -Depth 8),
    $encoding
)
$pidPath = [string]$env:BRIDGE_CONSUMER_SLOW_CHILD_PID_PATH
$child = Start-Process -FilePath powershell -ArgumentList @('-NoProfile','-Command','Start-Sleep -Seconds 30') -WindowStyle Hidden -PassThru
if ($pidPath) {
    [System.IO.File]::WriteAllText($pidPath, [string]$child.Id)
}
Wait-Process -Id $child.Id
exit 0
'@ | Set-Content -LiteralPath $slowCodex -Encoding UTF8
    $env:BRIDGE_CONSUMER_SLOW_CHILD_PID_PATH = $slowChildPid
    $env:BRIDGE_CONSUMER_IDENTITY_CAPTURE_PATH = $identityCapture
    $env:BRIDGE_CONSUMER_TIMEOUT_IDENTITY_CAPTURE_PATH = $timeoutIdentityCapture

    $script = Join-Path $PSScriptRoot 'Start-AgentBridgeConsumerLoop.ps1'
    foreach ($name in @($protectedEnvironmentNames)) {
        [Environment]::SetEnvironmentVariable(
            $name,
            $null,
            [EnvironmentVariableTarget]::Process
        )
    }
    $cleanEnvironmentRun = @(& $script `
        -Agent codex-tools-1 `
        -AgentUuid $agentUuid `
        -Role $role `
        -Capabilities $capabilities `
        -RuntimeRoot $runtimeRoot `
        -Worktree $worktree `
        -MaxIterations 1 `
        -DurationMinutes 0 `
        -PollSeconds 0 `
        -DryRun)
    Assert-True (@($cleanEnvironmentRun).Count -eq 1) 'clean-environment dry-run did not complete'
    foreach ($name in @($protectedEnvironmentNames)) {
        Assert-True (
            -not [Environment]::GetEnvironmentVariable(
                $name,
                [EnvironmentVariableTarget]::Process
            )
        ) "clean-environment dry-run left protected variable $name behind"
    }

    foreach ($name in @($poisonEnvironment.Keys)) {
        [Environment]::SetEnvironmentVariable(
            [string]$name,
            [string]$poisonEnvironment[$name],
            [EnvironmentVariableTarget]::Process
        )
    }
    [System.IO.File]::WriteAllText($wakePath, 'wake')

    $inheritedIdentityRejected = $false
    try {
        & $script `
            -Agent codex-tools-1 `
            -RuntimeRoot $runtimeRoot `
            -Worktree $worktree `
            -MaxIterations 1 `
            -DurationMinutes 0 `
            -PollSeconds 0 `
            -DryRun | Out-Null
    } catch {
        $inheritedIdentityRejected = (
            $_.Exception.Message -match 'AgentUuid must be passed explicitly'
        )
    }
    Assert-True $inheritedIdentityRejected 'consumer accepted inherited bridge identity without explicit metadata'

    $result = @(& $script `
        -Agent codex-tools-1 `
        -AgentUuid $agentUuid `
        -Role $role `
        -Capabilities $capabilities `
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
    Assert-True ($tick.agent_uuid -eq $agentUuid) 'dry-run reported the wrong agent UUID'
    Assert-True ($tick.role -eq $role) 'dry-run reported the wrong role'
    Assert-True ((@($tick.capabilities) -join ',') -eq $capabilities) 'dry-run reported the wrong capabilities'
    Assert-True ([string]$tick.run_id -match '^codex-tools-1-consumer-[0-9]+-[0-9]{8}T[0-9]{6}Z$') 'dry-run did not create a Tools-owned run ID'
    Assert-True ($tick.session_id -eq $tick.run_id) 'dry-run session ID must equal its owned run ID'

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
        -AgentUuid $agentUuid `
        -Role $role `
        -Capabilities $capabilities `
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
        -AgentUuid $agentUuid `
        -Role $role `
        -Capabilities $capabilities `
        -RuntimeRoot $runtimeRoot `
        -Worktree $worktree `
        -CodexCommand $fakeCodex `
        -Prompt 'claim and edit now' `
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

    Assert-True (Test-Path -LiteralPath $identityCapture -PathType Leaf) 'fake codex did not capture its identity environment'
    $capturedIdentity = Get-Content -Raw -LiteralPath $identityCapture -Encoding UTF8 |
        ConvertFrom-Json -ErrorAction Stop
    Assert-True ($capturedIdentity.AGENT_BRIDGE_RUNTIME_ROOT -eq $runtimeRoot) 'Codex child inherited the wrong runtime root'
    Assert-True ($capturedIdentity.AGENT_BRIDGE_AGENT -eq 'codex-tools-1') 'Codex child inherited the wrong agent'
    Assert-True ($capturedIdentity.AGENT_BRIDGE_AGENT_UUID -eq $agentUuid) 'Codex child inherited the wrong UUID'
    Assert-True ($capturedIdentity.AGENT_BRIDGE_ROLE -eq $role) 'Codex child inherited the wrong role'
    Assert-True ($capturedIdentity.AGENT_BRIDGE_CAPABILITIES -eq $capabilities) 'Codex child inherited the wrong capabilities'
    Assert-True ($capturedIdentity.AGENT_BRIDGE_RUN_ID -eq $liveRun[0].run_id) 'Codex child inherited the wrong run ID'
    Assert-True ($capturedIdentity.AGENT_BRIDGE_SESSION_ID -eq $liveRun[0].session_id) 'Codex child inherited the wrong session ID'
    Assert-True (-not [string]$capturedIdentity.AGENT_BRIDGE_HEARTBEAT_JOB) 'Codex child inherited the parent heartbeat job ID'
    Assert-True (-not [string]$capturedIdentity.AGENT_BRIDGE_WAKE_JOB) 'Codex child inherited the parent wake job ID'
    Assert-True (-not [string]$capturedIdentity.WD_AGENT_PROFILE) 'Codex child inherited the lead WD profile'
    Assert-True (-not [string]$capturedIdentity.WD_AGENT_PROMPT_FILE) 'Codex child inherited the lead WD prompt file'
    Assert-True (-not [string]$capturedIdentity.SESSION_ID) 'Codex child inherited a generic foreign session ID'
    Assert-True ([string]$capturedIdentity.prompt -match 'exact bridge identity is codex-tools-1, role tools-tests') 'Codex prompt lacks the exact Tools identity'
    Assert-True ([string]$capturedIdentity.prompt -match 'Capabilities are routing metadata, not authority') 'Codex prompt treats routing capabilities as authority'
    Assert-True ([string]$capturedIdentity.prompt -match 'bridge_next_action\.py --agent codex-tools-1 --json') 'Codex prompt lacks the exact bridge command'
    Assert-True ([string]$capturedIdentity.prompt -match 'agent_next_task\.py --agent codex-tools-1 --json') 'Codex prompt lacks the exact next-task command'
    Assert-True ([string]$capturedIdentity.prompt -match 'safe_mode is read-only') 'Codex prompt lacks the bridge read-only safe-mode guard'
    Assert-True ([string]$capturedIdentity.prompt -match 'candidate\.mode read-only') 'Codex prompt lacks the task-candidate read-only guard'
    Assert-True ([string]$capturedIdentity.prompt -match 'sole bridge-write exception.*answer_incoming') 'Codex prompt would deadlock a selected incoming bridge reply'
    Assert-True ([string]$capturedIdentity.prompt -notmatch 'codex-lead-1') 'Codex prompt leaked the lead identity'
    $guardIndex = ([string]$capturedIdentity.prompt).IndexOf(
        'For bridge_next_action, obey the top-level safe_mode.',
        [System.StringComparison]::Ordinal
    )
    $customTaskIndex = ([string]$capturedIdentity.prompt).IndexOf(
        'claim and edit now',
        [System.StringComparison]::Ordinal
    )
    Assert-True ($guardIndex -ge 0 -and $guardIndex -lt $customTaskIndex) 'custom prompt appeared before the immutable safe-mode guard'

    $claimPath = Join-Path $runtimeRoot 'work_queue\claims\consumer-heartbeat-smoke.json'
    $claim = Get-Content -Raw -LiteralPath $claimPath -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
    Assert-True ([string]$claim.last_heartbeat_utc -ne '2000-01-01T00:00:00Z') 'heartbeat did not refresh claim lease during codex tick'
    $eventsPath = Join-Path $runtimeRoot 'shared\events.jsonl'
    $events = @(Get-Content -LiteralPath $eventsPath -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop)
    $startEvents = @($events | Where-Object { $_.status -eq 'consumer_tick_started' })
    $finishEvents = @($events | Where-Object { $_.status -eq 'consumer_tick_finished' })
    Assert-True ($startEvents.Count -ge 1) 'tick start status event missing'
    Assert-True ($finishEvents.Count -ge 1) 'tick finish status event missing'
    Assert-True ($startEvents[-1].agent -eq 'codex-tools-1') 'tick start event used the wrong agent'
    Assert-True ($startEvents[-1].run_id -eq $liveRun[0].run_id) 'tick start event used the wrong run ID'
    Assert-True ($startEvents[-1].session_id -eq $liveRun[0].session_id) 'tick start event used the wrong session ID'
    Assert-True ($finishEvents[-1].run_id -eq $liveRun[0].run_id) 'tick finish event used the wrong run ID'

    [System.IO.File]::WriteAllText($wakePath, 'wake')
    $timeoutRun = @(& $script `
        -Agent codex-tools-1 `
        -AgentUuid $agentUuid `
        -Role $role `
        -Capabilities $capabilities `
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
    Assert-True (Test-Path -LiteralPath $timeoutIdentityCapture -PathType Leaf) 'timed-out Codex did not capture its identity environment'
    $timeoutIdentity = Get-Content -Raw -LiteralPath $timeoutIdentityCapture -Encoding UTF8 |
        ConvertFrom-Json -ErrorAction Stop
    Assert-True ($timeoutIdentity.AGENT_BRIDGE_AGENT -eq 'codex-tools-1') 'timed-out Codex inherited the wrong agent'
    Assert-True ($timeoutIdentity.AGENT_BRIDGE_RUN_ID -eq $timeoutRun[0].run_id) 'timed-out Codex inherited the wrong run ID'
    Assert-True (-not [string]$timeoutIdentity.WD_AGENT_PROFILE) 'timed-out Codex inherited the lead WD profile'
    Assert-True (-not [string]$timeoutIdentity.WD_AGENT_PROMPT_FILE) 'timed-out Codex inherited the lead WD prompt file'

    foreach ($name in @($poisonEnvironment.Keys)) {
        Assert-True (
            [Environment]::GetEnvironmentVariable(
                [string]$name,
                [EnvironmentVariableTarget]::Process
            ) -eq [string]$poisonEnvironment[$name]
        ) "consumer invocation did not restore parent environment variable $name"
    }

    'Bridge consumer loop smoke passed.'
} finally {
    foreach ($name in @($smokeEnvironmentNames)) {
        [Environment]::SetEnvironmentVariable(
            $name,
            $null,
            [EnvironmentVariableTarget]::Process
        )
        if ($null -ne $savedSmokeEnvironment[$name]) {
            [Environment]::SetEnvironmentVariable(
                $name,
                [string]$savedSmokeEnvironment[$name],
                [EnvironmentVariableTarget]::Process
            )
        }
    }
    foreach ($name in @($protectedEnvironmentNames)) {
        [Environment]::SetEnvironmentVariable(
            $name,
            $null,
            [EnvironmentVariableTarget]::Process
        )
        if ($null -ne $savedEnvironment[$name]) {
            [Environment]::SetEnvironmentVariable(
                $name,
                [string]$savedEnvironment[$name],
                [EnvironmentVariableTarget]::Process
            )
        }
    }
    $tmpFull = [System.IO.Path]::GetFullPath($tmpRoot)
    $auditFull = [System.IO.Path]::GetFullPath($auditRoot)
    if ($tmpFull.StartsWith($auditFull, [System.StringComparison]::OrdinalIgnoreCase) -and
        (Test-Path -LiteralPath $tmpFull)) {
        Remove-Item -LiteralPath $tmpFull -Recurse -Force -ErrorAction SilentlyContinue
    }
}
