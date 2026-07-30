#requires -Version 5.1
<#
.SYNOPSIS
    Start the durable codex-tools-1 bridge consumer from a pinned repo state.

.DESCRIPTION
    Validates the configured C-drive worktree, branch, and full commit before
    loading any bridge code. The process receives one initial bounded Codex
    tick so a reboot handoff is read even when no wake sentinel exists, then
    remains in the wake-only consumer loop.

    This wrapper deliberately supplies no model override. Codex therefore uses
    the provider default selected by the installed CLI.
#>
[CmdletBinding()]
param(
    [string] $ConfigPath = '',
    [switch] $ValidateOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $ConfigPath) {
    $ConfigPath = Join-Path $PSScriptRoot 'wd_supervisor_loop.json'
}

function Get-RequiredText {
    param(
        [Parameter(Mandatory)] [psobject] $Object,
        [Parameter(Mandatory)] [string] $Name
    )

    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property -or [string]::IsNullOrWhiteSpace([string]$property.Value)) {
        throw "tools consumer configuration is missing '$Name'"
    }
    return [string]$property.Value
}

function Resolve-ContainedScript {
    param(
        [Parameter(Mandatory)] [string] $Worktree,
        [Parameter(Mandatory)] [string] $RelativePath,
        [Parameter(Mandatory)] [string] $Label
    )

    if ([IO.Path]::IsPathRooted($RelativePath)) {
        throw "$Label must be relative to the pinned worktree"
    }
    $candidate = [IO.Path]::GetFullPath((Join-Path $Worktree $RelativePath))
    $prefix = $Worktree.TrimEnd('\') + '\'
    if (-not $candidate.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label escapes the pinned worktree: $RelativePath"
    }
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        throw "missing $Label at pinned head: $candidate"
    }
    return $candidate
}

function Invoke-GitText {
    param(
        [Parameter(Mandatory)] [string] $Worktree,
        [Parameter(Mandatory)] [string[]] $ArgumentList,
        [Parameter(Mandatory)] [string] $Operation
    )

    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& git -C $Worktree @ArgumentList 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($exitCode -ne 0) {
        throw "git $Operation failed in ${Worktree}: $($output -join ' ')"
    }
    return (@($output | ForEach-Object { [string]$_ }) -join "`n").Trim()
}

$configFull = [IO.Path]::GetFullPath($ConfigPath)
if (-not (Test-Path -LiteralPath $configFull -PathType Leaf)) {
    throw "tools consumer configuration not found: $configFull"
}
$configuration = Get-Content -LiteralPath $configFull -Raw -Encoding UTF8 |
    ConvertFrom-Json -ErrorAction Stop
if ([string]$configuration.schema -cne 'wd.supervisor-loop.v2') {
    throw "unsupported tools consumer configuration schema: $($configuration.schema)"
}
if ($null -eq $configuration.tools_consumer) {
    throw 'tools consumer configuration has no tools_consumer object'
}

$tools = $configuration.tools_consumer
if (-not [bool]$tools.enabled) {
    throw 'tools consumer is disabled in configuration'
}

$runtimeRoot = [IO.Path]::GetFullPath((Get-RequiredText $configuration 'runtime_root'))
$worktree = [IO.Path]::GetFullPath((Get-RequiredText $tools 'worktree'))
$expectedBranch = Get-RequiredText $tools 'expected_branch'
$expectedHead = (Get-RequiredText $tools 'expected_head').ToLowerInvariant()
$agent = Get-RequiredText $tools 'agent'
$agentUuid = (Get-RequiredText $tools 'agent_uuid').ToLowerInvariant()
$role = Get-RequiredText $tools 'role'
$runIdPrefix = Get-RequiredText $tools 'run_id_prefix'
$logDir = [IO.Path]::GetFullPath((Get-RequiredText $tools 'log_dir'))
$sandbox = Get-RequiredText $tools 'sandbox'
$approvalPolicy = Get-RequiredText $tools 'approval_policy'
$prompt = Get-RequiredText $tools 'prompt'

if ([IO.Path]::GetPathRoot($worktree).TrimEnd('\') -cne 'C:') {
    throw "tools worktree must be on persistent C: drive: $worktree"
}
if (-not (Test-Path -LiteralPath $worktree -PathType Container)) {
    throw "tools worktree does not exist: $worktree"
}
if (-not (Test-Path -LiteralPath (Join-Path $worktree '.git'))) {
    throw "tools worktree has no .git metadata: $worktree"
}
if ($expectedHead -cnotmatch '^[0-9a-f]{40}$') {
    throw 'tools expected_head must be a full lowercase Git commit'
}
if ($agent -cnotmatch '^[a-z][a-z0-9_-]{1,32}$') {
    throw "invalid tools agent identity: $agent"
}
if ($agentUuid -cnotmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$') {
    throw 'tools agent_uuid must be a UUID'
}
if ($role -cnotmatch '^[a-z][a-z0-9_-]{1,32}$') {
    throw "invalid tools role: $role"
}
if ($runIdPrefix -cnotmatch '^[A-Za-z0-9._:-]{1,80}$') {
    throw 'tools run_id_prefix is malformed'
}
if ($sandbox -cnotin @('read-only', 'workspace-write', 'danger-full-access')) {
    throw "unsupported tools sandbox: $sandbox"
}
if ($approvalPolicy -cnotin @('untrusted', 'on-failure', 'on-request', 'never')) {
    throw "unsupported tools approval policy: $approvalPolicy"
}

$capabilities = @(
    @($tools.capabilities) |
        ForEach-Object { [string]$_ } |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
)
if ($capabilities.Count -eq 0) {
    throw 'tools capabilities must not be empty'
}
foreach ($capability in $capabilities) {
    if ($capability -cnotmatch '^[a-z][a-z0-9_.:-]{1,64}$') {
        throw "invalid tools capability: $capability"
    }
}

$pollSeconds = [int]$tools.poll_seconds
$codexTimeoutSeconds = [int]$tools.codex_timeout_seconds
if ($pollSeconds -lt 1) {
    throw 'tools poll_seconds must be at least 1'
}
if ($codexTimeoutSeconds -lt 1) {
    throw 'tools codex_timeout_seconds must be at least 1'
}

$inside = Invoke-GitText $worktree @('rev-parse', '--is-inside-work-tree') 'worktree validation'
if ($inside -cne 'true') {
    throw "configured tools path is not a Git worktree: $worktree"
}
$actualBranch = Invoke-GitText $worktree @('symbolic-ref', '--quiet', '--short', 'HEAD') 'branch validation'
if ($actualBranch -cne $expectedBranch) {
    throw "tools worktree branch mismatch: expected '$expectedBranch', got '$actualBranch'"
}
$actualHead = (Invoke-GitText $worktree @('rev-parse', 'HEAD') 'head validation').ToLowerInvariant()
if ($actualHead -cne $expectedHead) {
    throw "tools worktree head mismatch: expected '$expectedHead', got '$actualHead'"
}

$sessionScript = Resolve-ContainedScript `
    $worktree `
    (Get-RequiredText $tools 'session_script_relative') `
    'bridge session script'
$consumerScript = Resolve-ContainedScript `
    $worktree `
    (Get-RequiredText $tools 'consumer_script_relative') `
    'bridge consumer script'

$validation = [pscustomobject]@{
    schema = 'wd.tools-consumer-validation.v1'
    config_path = $configFull
    runtime_root = $runtimeRoot
    worktree = $worktree
    branch = $actualBranch
    head = $actualHead
    agent = $agent
    agent_uuid = $agentUuid
    role = $role
    capabilities = @($capabilities)
    session_script = $sessionScript
    consumer_script = $consumerScript
    model_override = $null
    validated = $true
}
if ($ValidateOnly) {
    $validation
    return
}

# The supervisor may itself have been invoked from an agent-bound shell. This
# is a new dedicated process, so inherited identity must not constrain the
# configured tools identity before Start-AgentBridgeSession establishes it.
$identityVariables = @(
    'AGENT_BRIDGE_AGENT',
    'AGENT_BRIDGE_RUN_ID',
    'AGENT_BRIDGE_SESSION_ID',
    'AGENT_BRIDGE_AGENT_UUID',
    'AGENT_BRIDGE_ROLE',
    'AGENT_BRIDGE_CAPABILITIES',
    'AGENT_BRIDGE_OWNER_SESSION_ID',
    'AGENT_BRIDGE_OWNER_TOKEN',
    'AGENT_BRIDGE_OWNER_PID',
    'AGENT_BRIDGE_OWNER_PROCESS_START_UTC'
)
foreach ($variableName in $identityVariables) {
    [Environment]::SetEnvironmentVariable($variableName, $null, 'Process')
}

$runStamp = [DateTime]::UtcNow.ToString(
    'yyyyMMddTHHmmssfffZ',
    [Globalization.CultureInfo]::InvariantCulture
)
$runId = "$runIdPrefix-$runStamp-$PID"
if ($runId.Length -gt 128) {
    throw 'generated tools run id exceeds 128 characters'
}

. $sessionScript `
    -Agent $agent `
    -RuntimeRoot $runtimeRoot `
    -RepoRoot $worktree `
    -RunId $runId `
    -Role $role `
    -AgentUuid $agentUuid `
    -Capabilities $capabilities `
    -SkipBridgeRead `
    -SkipGitStatus `
    -SkipWakeWatcher `
    -SkipHeartbeatJob

$commonConsumerArguments = @{
    Agent = $agent
    AgentUuid = $agentUuid
    Role = $role
    Capabilities = @($capabilities)
    RuntimeRoot = $runtimeRoot
    Worktree = $worktree
    Sandbox = $sandbox
    ApprovalPolicy = $approvalPolicy
    CodexTimeoutSeconds = $codexTimeoutSeconds
    LogDir = $logDir
    Prompt = $prompt
}

# The first tick is intentionally not WakeOnly. It reads the durable handoff
# immediately after reboot without fabricating an operator/lead wake event.
$initialArguments = @{} + $commonConsumerArguments
$initialArguments['DurationMinutes'] = 0
$initialArguments['MaxIterations'] = 1
$initialArguments['PollSeconds'] = 0
$initialOutput = @(& $consumerScript @initialArguments)
$initialResult = @(
    $initialOutput |
        Where-Object {
            $_ -is [psobject] -and
            $_.PSObject.Properties.Name -contains 'exit_code'
        }
) | Select-Object -Last 1
if ($null -eq $initialResult) {
    throw 'initial tools consumer tick returned no structured result'
}
if ($null -eq $initialResult.exit_code -or [int]$initialResult.exit_code -ne 0) {
    throw "initial tools consumer tick failed with exit_code=$($initialResult.exit_code)"
}

$foreverArguments = @{} + $commonConsumerArguments
$foreverArguments['Forever'] = $true
$foreverArguments['WakeOnly'] = $true
$foreverArguments['PollSeconds'] = $pollSeconds
& $consumerScript @foreverArguments
