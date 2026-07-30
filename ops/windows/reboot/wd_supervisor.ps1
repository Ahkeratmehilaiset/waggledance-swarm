#requires -Version 5.1
<#
.SYNOPSIS
    Fail-closed watchdog for bridge watchers, the Tools consumer, and drivers.

.DESCRIPTION
    In report-only mode, verifies the exact configured processes and deliberate
    merge-driver HOLD. With -Apply it starts only absent helpers and contains
    driver tasks that are running or enabled without proven non-Apply legacy
    arguments. It never enables a scheduled task and emits no synthetic bridge
    events.
#>
[CmdletBinding()]
param(
    [switch] $Apply,
    [string] $ConfigPath = '',
    [string] $LogPath = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $ConfigPath) {
    $ConfigPath = Join-Path $PSScriptRoot 'wd_supervisor_loop.json'
}
$selfPid = $PID
$actions = New-Object 'System.Collections.Generic.List[string]'

function Get-RequiredText {
    param(
        [Parameter(Mandatory)] [psobject] $Object,
        [Parameter(Mandatory)] [string] $Name
    )

    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property -or [string]::IsNullOrWhiteSpace([string]$property.Value)) {
        throw "supervisor configuration is missing '$Name'"
    }
    return [string]$property.Value
}

function Resolve-PowerShellChildHost {
    $candidates = New-Object 'System.Collections.Generic.List[string]'
    $currentWindowsPowerShell = ''

    try {
        $current = Get-Process -Id $PID -ErrorAction Stop
        $currentHostName = [IO.Path]::GetFileName([string]$current.Path)
        if ($current.Path -and $currentHostName -match '^(?i:pwsh)\.exe$') {
            $candidates.Add($current.Path)
        }
        elseif ($current.Path -and $currentHostName -match '^(?i:powershell)\.exe$') {
            $currentWindowsPowerShell = $current.Path
        }
    }
    catch {
        $actions.Add("WARN could not inspect current PowerShell host: $($_.Exception.Message)")
    }

    foreach ($commandName in @('pwsh.exe', 'pwsh')) {
        try {
            $command = Get-Command $commandName -ErrorAction Stop |
                Where-Object {
                    $_.CommandType -eq [Management.Automation.CommandTypes]::Application
                } |
                Select-Object -First 1
            if ($null -ne $command) {
                $path = if ($command.Path) { $command.Path } else { $command.Source }
                if ($path) { $candidates.Add([string]$path) }
            }
        }
        catch {
            # Other dynamic probes below may still resolve a supported host.
        }
    }

    if ($env:ProgramFiles) {
        $powerShellRoot = Join-Path $env:ProgramFiles 'PowerShell'
        if (Test-Path -LiteralPath $powerShellRoot -PathType Container) {
            $versionDirectories = @(
                Get-ChildItem -LiteralPath $powerShellRoot -Directory -ErrorAction SilentlyContinue |
                    Sort-Object {
                        $version = [Version]'0.0'
                        [void][Version]::TryParse($_.Name, [ref]$version)
                        $version
                    } -Descending
            )
            foreach ($directory in $versionDirectories) {
                $candidates.Add((Join-Path $directory.FullName 'pwsh.exe'))
            }
        }
    }

    if ($env:SystemRoot) {
        if ($currentWindowsPowerShell) {
            $candidates.Add($currentWindowsPowerShell)
        }
        $candidates.Add(
            (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe')
        )
    }
    foreach ($commandName in @('powershell.exe', 'powershell')) {
        try {
            $command = Get-Command $commandName -ErrorAction Stop |
                Where-Object {
                    $_.CommandType -eq [Management.Automation.CommandTypes]::Application
                } |
                Select-Object -First 1
            if ($null -ne $command) {
                $path = if ($command.Path) { $command.Path } else { $command.Source }
                if ($path) { $candidates.Add([string]$path) }
            }
        }
        catch {
            # The final candidate validation below emits one fail-closed error.
        }
    }

    $seen = @{}
    foreach ($candidate in $candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) { continue }
        try {
            $full = [IO.Path]::GetFullPath($candidate)
        }
        catch {
            continue
        }
        $key = $full.ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        if (Test-Path -LiteralPath $full -PathType Leaf) {
            return $full
        }
    }

    throw 'could not dynamically resolve a supported pwsh/powershell child executable'
}

function Test-TextContains {
    param(
        [AllowEmptyString()] [string] $Text,
        [Parameter(Mandatory)] [string] $Expected
    )
    return $Text.IndexOf($Expected, [StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Get-AgentCommandProcesses {
    param(
        [Parameter(Mandatory)] [object[]] $Processes,
        [Parameter(Mandatory)] [string] $ScriptName,
        [Parameter(Mandatory)] [string] $Agent
    )

    $agentPattern = '(?i)(?:^|\s)-Agent\s+(?:"{0}"|{0})(?:\s|$)' -f
        [Regex]::Escape($Agent)
    return @(
        $Processes |
            Where-Object {
                (Test-TextContains ([string]$_.CommandLine) $ScriptName) -and
                [string]$_.CommandLine -match $agentPattern
            }
    )
}

function Invoke-WithChildIdentity {
    param(
        [Parameter(Mandatory)] [string] $Agent,
        [Parameter(Mandatory)] [string] $RuntimeRoot,
        [Parameter(Mandatory)] [scriptblock] $Action
    )

    $values = @{}
    $names = @(
        'AGENT_BRIDGE_RUNTIME_ROOT',
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
    foreach ($name in $names) {
        $values[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }

    try {
        foreach ($name in $names) {
            [Environment]::SetEnvironmentVariable($name, $null, 'Process')
        }
        [Environment]::SetEnvironmentVariable(
            'AGENT_BRIDGE_RUNTIME_ROOT',
            $RuntimeRoot,
            'Process'
        )
        [Environment]::SetEnvironmentVariable('AGENT_BRIDGE_AGENT', $Agent, 'Process')
        & $Action
    }
    finally {
        foreach ($name in $names) {
            [Environment]::SetEnvironmentVariable($name, $values[$name], 'Process')
        }
    }
}

function Start-DetachedPowerShell {
    param(
        [Parameter(Mandatory)] [string] $HostPath,
        [Parameter(Mandatory)] [string[]] $ArgumentList,
        [Parameter(Mandatory)] [string] $Name
    )

    $process = Start-Process `
        -FilePath $HostPath `
        -ArgumentList $ArgumentList `
        -WindowStyle Hidden `
        -PassThru `
        -ErrorAction Stop
    $actions.Add("RELAUNCHED $Name pid=$($process.Id)")
}

function Test-LegacyDriverProvenNonApply {
    param(
        [Parameter(Mandatory)] $Task,
        [Parameter(Mandatory)] [string] $ExpectedScript
    )

    $taskActions = @($Task.Actions)
    if ($taskActions.Count -eq 0) { return $false }
    $escapedScript = [Regex]::Escape($ExpectedScript)
    $safeArguments = (
        '^(?i:-NoProfile\s+-ExecutionPolicy\s+Bypass\s+-File\s+' +
        '(?:"{0}"|{0})\s+-Loop\s+-PollSeconds\s+120)\s*$'
    ) -f $escapedScript
    $stablePowerShell = Join-Path $env:SystemRoot (
        'System32\WindowsPowerShell\v1.0\powershell.exe'
    )
    foreach ($taskAction in $taskActions) {
        $execute = [string]$taskAction.Execute
        $arguments = [string]$taskAction.Arguments
        if ([string]::IsNullOrWhiteSpace($execute) -or
            [string]::IsNullOrWhiteSpace($arguments)) {
            return $false
        }
        if (
            -not $execute.Equals(
                'powershell.exe',
                [StringComparison]::OrdinalIgnoreCase
            ) -and
            -not $execute.Equals(
                $stablePowerShell,
                [StringComparison]::OrdinalIgnoreCase
            )
        ) {
            return $false
        }
        if ($arguments -notmatch $safeArguments) {
            return $false
        }
    }
    return $true
}

function Get-OptionalScheduledTask {
    param([Parameter(Mandatory)] [string] $TaskName)

    try {
        return Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    }
    catch {
        if (
            [string]$_.FullyQualifiedErrorId -like 'CmdletizationQuery_NotFound*' -or
            $_.CategoryInfo.Category -eq [Management.Automation.ErrorCategory]::ObjectNotFound
        ) {
            return $null
        }
        throw
    }
}

function Invoke-TaskContainment {
    param(
        [Parameter(Mandatory)] [string] $TaskName,
        [Parameter(Mandatory)] [string] $Reason
    )

    $task = Get-OptionalScheduledTask -TaskName $TaskName
    if ($null -eq $task) {
        $actions.Add("WARN scheduled task '$TaskName' not found for $Reason")
        return
    }

    $isRunning = [string]$task.State -eq 'Running'
    $isEnabled = [bool]$task.Settings.Enabled
    if (-not $isRunning -and -not $isEnabled) {
        $actions.Add("HOLD verified $TaskName disabled/not-running ($Reason)")
        return
    }

    if (-not $Apply) {
        if ($isRunning) {
            $actions.Add("WOULD-STOP $TaskName ($Reason)")
        }
        if ($isEnabled) {
            $actions.Add("WOULD-DISABLE $TaskName ($Reason)")
        }
        return
    }

    if ($isRunning) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        $actions.Add("STOPPED $TaskName ($Reason)")
    }
    if ($isEnabled) {
        Disable-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Out-Null
        $actions.Add("DISABLED $TaskName ($Reason)")
    }
}

$configFull = [IO.Path]::GetFullPath($ConfigPath)
if (-not (Test-Path -LiteralPath $configFull -PathType Leaf)) {
    throw "supervisor configuration not found: $configFull"
}
$configuration = Get-Content -LiteralPath $configFull -Raw -Encoding UTF8 |
    ConvertFrom-Json -ErrorAction Stop
if ([string]$configuration.schema -cne 'wd.supervisor-loop.v2') {
    throw "unsupported supervisor configuration schema: $($configuration.schema)"
}

$runtimeRoot = [IO.Path]::GetFullPath((Get-RequiredText $configuration 'runtime_root'))
if (-not $LogPath) {
    $LogPath = Get-RequiredText $configuration 'log_path'
}
$logFull = [IO.Path]::GetFullPath($LogPath)
$logParent = Split-Path -Parent $logFull
if (-not (Test-Path -LiteralPath $logParent -PathType Container)) {
    [void](New-Item -ItemType Directory -Path $logParent -Force -ErrorAction Stop)
}

$powerShellHost = Resolve-PowerShellChildHost
$processes = @(
    Get-CimInstance Win32_Process -ErrorAction Stop |
        Where-Object {
            $_.ProcessId -ne $selfPid -and
            -not [string]::IsNullOrWhiteSpace([string]$_.CommandLine)
        }
)

if ($null -eq $configuration.watchers) {
    throw 'supervisor configuration has no watchers object'
}
$watcherScript = [IO.Path]::GetFullPath(
    (Get-RequiredText $configuration.watchers 'script_path')
)
$watcherAgents = @(
    @($configuration.watchers.agents) |
        ForEach-Object { [string]$_ } |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
)
if ($watcherAgents.Count -eq 0) {
    throw 'supervisor watcher agent list must not be empty'
}
if (-not (Test-Path -LiteralPath $watcherScript -PathType Leaf)) {
    throw "required watcher source is missing: $watcherScript"
}
else {
    foreach ($agent in $watcherAgents) {
        if ($agent -cnotmatch '^[a-z][a-z0-9_-]{1,32}$') {
            throw "invalid configured watcher identity: $agent"
        }
        $allAgentWatchers = @(
            Get-AgentCommandProcesses `
                $processes `
                'Watch-Bridge.ps1' `
                $agent
        )
        $exactAgentWatchers = @(
            $allAgentWatchers |
                Where-Object {
                    (Test-TextContains ([string]$_.CommandLine) $watcherScript) -and
                    (Test-TextContains ([string]$_.CommandLine) $runtimeRoot)
                }
        )

        if ($exactAgentWatchers.Count -eq 1 -and $allAgentWatchers.Count -eq 1) {
            continue
        }
        if ($allAgentWatchers.Count -gt 0) {
            $ids = @($allAgentWatchers | ForEach-Object { [string]$_.ProcessId }) -join ','
            $actions.Add(
                "CONFLICT watcher:$agent count=$($allAgentWatchers.Count) pids=$ids; no duplicate launched"
            )
            continue
        }

        if (-not $Apply) {
            $actions.Add("WOULD-RELAUNCH watcher:$agent")
            continue
        }

        $watcherArguments = @(
            '-NoProfile',
            '-ExecutionPolicy', 'Bypass',
            '-File', $watcherScript,
            '-Agent', $agent,
            '-RuntimeRoot', $runtimeRoot
        )
        Invoke-WithChildIdentity $agent $runtimeRoot {
            Start-DetachedPowerShell $powerShellHost $watcherArguments "watcher:$agent"
        }
    }
}

if ($null -eq $configuration.tools_consumer) {
    throw 'supervisor configuration has no tools_consumer object'
}
$tools = $configuration.tools_consumer
if ([bool]$tools.enabled) {
    $toolsAgent = Get-RequiredText $tools 'agent'
    $toolsLauncher = [IO.Path]::GetFullPath(
        (Get-RequiredText $tools 'launcher_script')
    )
    $toolsConfig = [IO.Path]::GetFullPath(
        (Get-RequiredText $tools 'config_path')
    )
    if (-not (Test-Path -LiteralPath $toolsLauncher -PathType Leaf)) {
        throw "required tools consumer launcher is missing: $toolsLauncher"
    }
    if (-not (Test-Path -LiteralPath $toolsConfig -PathType Leaf)) {
        throw "required tools consumer config is missing: $toolsConfig"
    }
    $wrapperProcesses = @(
        $processes |
            Where-Object {
                Test-TextContains ([string]$_.CommandLine) $toolsLauncher
            }
    )
    $exactWrapperProcesses = @(
        $wrapperProcesses |
            Where-Object {
                Test-TextContains ([string]$_.CommandLine) $toolsConfig
            }
    )
    $legacyConsumers = @(
        Get-AgentCommandProcesses `
            $processes `
            'Start-AgentBridgeConsumerLoop.ps1' `
            $toolsAgent
    )

    if ($exactWrapperProcesses.Count -eq 1 -and
        $wrapperProcesses.Count -eq 1 -and
        $legacyConsumers.Count -eq 0) {
        # Healthy: the wrapper owns both the initial and Forever/WakeOnly phases.
    }
    elseif ($wrapperProcesses.Count -gt 0 -or $legacyConsumers.Count -gt 0) {
        $wrapperIds = @($wrapperProcesses | ForEach-Object { [string]$_.ProcessId }) -join ','
        $legacyIds = @($legacyConsumers | ForEach-Object { [string]$_.ProcessId }) -join ','
        $actions.Add(
            "CONFLICT tools consumer wrappers=$wrapperIds legacy=$legacyIds; no duplicate launched"
        )
    }
    elseif (-not $Apply) {
        $actions.Add('WOULD-RELAUNCH consumer-loop:codex-tools-1')
    }
    else {
        $toolsArguments = @(
            '-NoProfile',
            '-ExecutionPolicy', 'Bypass',
            '-File', $toolsLauncher,
            '-ConfigPath', $toolsConfig
        )
        Start-DetachedPowerShell `
            $powerShellHost `
            $toolsArguments `
            'consumer-loop:codex-tools-1'
    }
}

if ($null -eq $configuration.driver_containment) {
    throw 'supervisor configuration has no driver_containment object'
}
$driver = $configuration.driver_containment
$standingTaskName = Get-RequiredText $driver 'standing_task'
$legacyTaskName = Get-RequiredText $driver 'legacy_task'
$legacyScript = [IO.Path]::GetFullPath(
    (Get-RequiredText $driver 'legacy_script_path')
)

Invoke-TaskContainment $standingTaskName 'deliberate standing-driver HOLD'

$legacyTask = Get-OptionalScheduledTask -TaskName $legacyTaskName
if ($null -eq $legacyTask) {
    $actions.Add("WARN scheduled task '$legacyTaskName' not found for legacy verification")
}
else {
    if (Test-LegacyDriverProvenNonApply $legacyTask $legacyScript) {
        $actions.Add("LEGACY-NONAPPLY verified $legacyTaskName; left unchanged")
    }
    else {
        Invoke-TaskContainment $legacyTaskName 'legacy action is not proven non-Apply'
    }
}

$bridgeNote = 'bridge-last-event-age=unknown'
try {
    $eventsPath = Join-Path $runtimeRoot 'shared\events.jsonl'
    if (Test-Path -LiteralPath $eventsPath -PathType Leaf) {
        $newest = $null
        foreach ($line in (Get-Content -LiteralPath $eventsPath -Tail 80 -ErrorAction Stop)) {
            try {
                $timestamp = [string](($line | ConvertFrom-Json -ErrorAction Stop).ts_utc)
                if ($timestamp) {
                    $candidate = [DateTimeOffset]::Parse(
                        $timestamp,
                        [Globalization.CultureInfo]::InvariantCulture,
                        [Globalization.DateTimeStyles]::AssumeUniversal
                    ).UtcDateTime
                    if ($null -eq $newest -or $candidate -gt $newest) {
                        $newest = $candidate
                    }
                }
            }
            catch {
                # One concurrently appended/truncated row cannot hide valid rows.
            }
        }
        if ($null -ne $newest) {
            $ageMinutes = ([DateTime]::UtcNow - $newest).TotalMinutes
            $bridgeNote = 'bridge-last-event-age={0:N1}min' -f $ageMinutes
            if ($ageMinutes -gt 30) {
                $actions.Add("ALERT bridge quiet for $([int]$ageMinutes)min")
            }
        }
    }
}
catch {
    $actions.Add("WARN bridge freshness check failed: $($_.Exception.Message)")
}

$mode = if ($Apply) { 'APPLY' } else { 'dry-run' }
$timestampUtc = [DateTime]::UtcNow.ToString(
    'yyyy-MM-ddTHH:mm:ssZ',
    [Globalization.CultureInfo]::InvariantCulture
)
$summary = if ($actions.Count -eq 0) {
    'all configured helpers healthy; driver HOLD verified'
}
else {
    $actions -join '; '
}
$line = "[$timestampUtc] [$mode] host=$powerShellHost; $bridgeNote :: $summary"
Add-Content -LiteralPath $logFull -Value $line -Encoding UTF8
$line
