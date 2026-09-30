<#
.SYNOPSIS
    Bridge-owned Limited bootstrap of the five bridge watchers and Tools while WD-Supervisor is OFF.

.DESCRIPTION
    Dot-source only. The fleet wrapper calls Invoke-WdBridgeLimitedBootstrap in its own
    non-elevated process after the elevated restore has returned. Nothing here registers,
    enables, starts, disables or stops a scheduled task; WD-Supervisor stays exactly Disabled.

    The one launch is one run of the installed bundle's own watcher and Tools reconcile
    (wd_supervisor.ps1 -Apply -BridgeWorkersOnly) as this Limited user. That mode only verifies
    the merge-driver HOLD and refuses before any launch, never disabling or stopping a task; a
    launcher or supervisor without the switch fails parameter binding first. Its workers get the
    same canonical command lines, generation binding and Global reconcile mutexes as under WD-Supervisor,
    so when the mode changes each side finds the other's workers current and launches no
    duplicate. An elevated caller is refused: its workers would be opaque to a later
    Limited supervisor. A -BridgeWorkersOnly dry run must then find nothing to launch,
    replace or resolve, the Tools readiness record must bind the pinned generation and thread
    to the live consumer and its native codex.exe (PID and start within 1 s), and
    WD-Supervisor must still be exactly Disabled. Observations are injected
    ports, so fakes prove every refusal without a process or a task. The result claims
    process currency only: responsiveness and any pre-boot (legacy) record stay unknown,
    and nothing here replays, rewrites or clears a record.
#>

function Get-WdBridgeLimitedBootstrapDecision {
    param(
        [Parameter(Mandatory)] [bool] $IsAdministrator,
        [AllowNull()] [object] $SupervisorTaskEnabled = $null,
        [AllowEmptyString()] [string] $SupervisorTaskState = '',
        [AllowNull()] [object[]] $DriverTasks = $null,
        [AllowEmptyString()] [string] $DeployedGeneration = '',
        [AllowEmptyString()] [string] $ExpectedGeneration = '',
        [AllowNull()] [string[]] $WatcherAgents = $null,
        [AllowEmptyString()] [string] $ToolsThreadId = ''
    )

    $lanes = @('codex-lead-1', 'codex-tools-1', 'claude-rco-1', 'claude-rco-2', 'fable-5')
    $reasons = New-Object System.Collections.Generic.List[string]
    if ($IsAdministrator) {
        $reasons.Add('elevated caller: the workers must run Limited like WD-Supervisor workers; use a non-elevated terminal')
    }
    if ($SupervisorTaskEnabled -isnot [bool] -or $SupervisorTaskEnabled -or $SupervisorTaskState -cne 'Disabled') {
        $reasons.Add('WD-Supervisor is not exactly Disabled; the Bridge path never runs beside it')
    }
    $drivers = @($DriverTasks)
    if ($null -eq $DriverTasks -or $drivers.Count -ne 2) {
        $reasons.Add('the two merge-driver tasks were not observed')
    }
    else {
        foreach ($task in $drivers) {
            # Exact field names only: the PSObject.Properties indexer ignores case.
            $field = @{}
            if ($null -ne $task) {
                foreach ($property in @($task.PSObject.Properties)) {
                    if ($property.Name -cin @('present', 'enabled', 'state')) { $field[$property.Name] = $property.Value }
                }
            }
            # A missing, unreadable or ambiguous task is unknown, never contained (the Limited user may
            # not see it): only a present, not enabled, exactly Disabled task is proven held.
            $contained = $field['present'] -is [bool] -and $field['present'] -and $field['enabled'] -is [bool] -and
                -not $field['enabled'] -and [string]$field['state'] -ceq 'Disabled'
            if (-not $contained) { $reasons.Add('a merge-driver task is not proven present, disabled and not running') }
        }
    }
    if ($DeployedGeneration -cnotmatch '^[0-9a-f]{40}$' -or $DeployedGeneration -cne $ExpectedGeneration) {
        $reasons.Add('the installed bundle generation is not the exact pinned generation')
    }
    $agents = @($WatcherAgents)
    $exact = $null -ne $WatcherAgents -and $agents.Count -eq $lanes.Count
    foreach ($lane in $lanes) {
        $seen = 0
        foreach ($agent in $agents) { if ($agent -ceq $lane) { $seen++ } }
        if ($seen -ne 1) { $exact = $false }
    }
    if (-not $exact) { $reasons.Add('the watcher agents are not exactly the five bridge lanes') }
    if ($ToolsThreadId -cnotmatch '^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$') {
        $reasons.Add('the Tools thread pin is missing or malformed')
    }
    return [pscustomobject]@{
        schema = 'wd.bridge-limited-bootstrap-decision.v1'
        authority = 'none'
        action = if ($reasons.Count -eq 0) { 'launch' } else { 'refuse' }
        reasons = @($reasons)
    }
}

function Get-WdBridgeReconcileVerdict {
    param(
        [AllowNull()] [object] $Run,
        [Parameter(Mandatory)] [string] $Mode
    )

    if ($Mode -cnotin @('APPLY', 'dry-run')) { throw "unsupported reconcile mode: $Mode" }
    $reasons = New-Object System.Collections.Generic.List[string]
    $notes = New-Object System.Collections.Generic.List[string]
    $exitCode = $null
    $lines = @()
    if ($null -ne $Run) {
        foreach ($property in @($Run.PSObject.Properties)) {
            if ($property.Name -ceq 'exit_code') { $exitCode = $property.Value }
            if ($property.Name -ceq 'lines') { $lines = @($property.Value) }
        }
    }
    if ($exitCode -isnot [int] -or $exitCode -ne 0) { $reasons.Add("the $Mode reconcile did not exit 0") }
    # wd_supervisor.ps1 prints one line: [utc] [APPLY|dry-run] host=...; note :: action; action
    $pattern = '^\[[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\] \[' + [regex]::Escape($Mode) + '\] host='
    $summary = @($lines | Where-Object { [string]$_ -cmatch $pattern })
    if ($summary.Count -ne 1) {
        $reasons.Add("the $Mode reconcile did not print exactly one summary line")
    }
    else {
        $text = [string]$summary[0]
        $at = $text.IndexOf(' :: ', [StringComparison]::Ordinal)
        if ($at -lt 0) {
            $reasons.Add("the $Mode summary line has no action list")
        }
        else {
            foreach ($action in @($text.Substring($at + 4) -split '; ')) {
                if ($action -cmatch '^CONFLICT\s') { $reasons.Add('conflict: ' + $action) }
                elseif ($action -cmatch '^(DISABLED|STOPPED)\s') { $reasons.Add('a scheduled task was mutated: ' + $action) }
                elseif ($Mode -ceq 'dry-run' -and $action -cmatch '^WOULD-') { $reasons.Add('still to reconcile: ' + $action) }
                elseif ($action -cmatch '^(WARN|ALERT)\s') { $notes.Add($action) }
            }
        }
    }
    return [pscustomobject]@{
        ok = $reasons.Count -eq 0
        reasons = @($reasons)
        notes = @($notes)
        summary = if ($summary.Count -eq 1) { [string]$summary[0] } else { '' }
    }
}

function ConvertTo-WdBridgeUtc {
    param([AllowNull()] [object] $Value)

    # Explicit UTC only: a DateTimeOffset, a Utc or Local DateTime, or an ISO 8601 string with Z or
    # an explicit offset. An offset-less string or an Unspecified DateTime stays unknown ($null).
    if ($Value -is [DateTimeOffset]) { return ([DateTimeOffset]$Value).ToUniversalTime() }
    if ($Value -is [datetime]) {
        if (([datetime]$Value).Kind -eq [DateTimeKind]::Unspecified) { return $null }
        return ([DateTimeOffset][datetime]$Value).ToUniversalTime()
    }
    $pattern = '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:[.][0-9]{1,7})?(?:Z|[+-][0-9]{2}:[0-9]{2})$'
    $parsed = [DateTimeOffset]::MinValue
    if ($Value -is [string] -and $Value -cmatch $pattern -and
        [DateTimeOffset]::TryParse($Value, [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::AdjustToUniversal, [ref]$parsed)) {
        return $parsed.ToUniversalTime()
    }
    return $null
}

function Test-WdBridgeToolsReadinessPinned {
    param(
        [AllowNull()] [object] $Record,
        [AllowEmptyString()] [string] $Generation = '',
        [AllowEmptyString()] [string] $ThreadId = '',
        [Parameter(Mandatory)] [scriptblock] $GetProcess
    )

    if ($null -eq $Record -or $Record -isnot [Management.Automation.PSCustomObject]) { return $false }
    # Exact field names only: the PSObject.Properties indexer ignores case.
    $field = @{}
    foreach ($property in @($Record.PSObject.Properties)) {
        if ($property.Name -cin @('schema', 'status', 'readiness_scope', 'conversation_surface', 'agent', 'generation',
                'thread_id', 'pid', 'process_start_utc', 'native_pid', 'native_parent_pid', 'native_process_start_utc',
                'ready_at_utc', 'task_completion_verified')) {
            $field[$property.Name] = $property.Value
        }
    }
    # The native_terminal predicate of start-wd-all.ps1 Test-ToolsProcessReadiness.
    $expected = [ordered]@{
        schema = 'wd.tools-consumer-ready.v3'; status = 'terminal_ready'; readiness_scope = 'native_cli_only'
        conversation_surface = 'native_terminal'; agent = 'codex-tools-1'; generation = $Generation; thread_id = $ThreadId
    }
    foreach ($name in @($expected.get_Keys())) {
        if ($field[$name] -isnot [string] -or $field[$name].Length -eq 0 -or $field[$name] -cne $expected[$name]) { return $false }
    }
    if ($field['task_completion_verified'] -isnot [bool] -or $field['task_completion_verified']) { return $false }
    foreach ($name in @('pid', 'native_pid', 'native_parent_pid')) {
        if (($field[$name] -isnot [int] -and $field[$name] -isnot [long]) -or $field[$name] -lt 1 -or $field[$name] -gt [int]::MaxValue) {
            return $false
        }
    }
    $consumerAt = ConvertTo-WdBridgeUtc $field['process_start_utc']
    $nativeAt = ConvertTo-WdBridgeUtc $field['native_process_start_utc']
    $readyAt = ConvertTo-WdBridgeUtc $field['ready_at_utc']
    if ($null -eq $consumerAt -or $null -eq $nativeAt -or $null -eq $readyAt -or
        $field['native_parent_pid'] -ne $field['pid'] -or $nativeAt -lt $consumerAt -or $nativeAt -gt $readyAt) {
        return $false
    }
    # Both processes the record names must be alive now with the recorded starts (1 s, as in
    # start-wd-all.ps1): a stale record, a reused PID or an unrelated live codex.exe never counts.
    $consumer = & $GetProcess ([int]$field['pid'])
    $native = & $GetProcess ([int]$field['native_pid'])
    if ($consumer -isnot [hashtable] -or $native -isnot [hashtable] -or
        $consumer['created_utc'] -isnot [DateTimeOffset] -or $native['created_utc'] -isnot [DateTimeOffset]) {
        return $false
    }
    return (
        [int]$consumer['process_id'] -eq [int]$field['pid'] -and
        [Math]::Abs(($consumer['created_utc'] - $consumerAt).TotalSeconds) -le 1 -and
        [int]$native['process_id'] -eq [int]$field['native_pid'] -and
        [int]$native['parent_process_id'] -eq [int]$field['pid'] -and
        [string]$native['name'] -ieq 'codex.exe' -and
        [Math]::Abs(($native['created_utc'] - $nativeAt).TotalSeconds) -le 1
    )
}

function Invoke-WdBridgeLimitedBootstrap {
    param(
        [Parameter(Mandatory)] [scriptblock] $Observe,
        [Parameter(Mandatory)] [scriptblock] $RunSupervisorOnce,
        [Parameter(Mandatory)] [scriptblock] $ReadToolsReadiness,
        [Parameter(Mandatory)] [scriptblock] $GetProcess,
        [Parameter(Mandatory)] [scriptblock] $Sleep,
        [ValidateRange(1, 900)] [int] $ToolsWaitSeconds = 180
    )

    $result = [ordered]@{
        schema = 'wd.bridge-limited-bootstrap.v1'
        authority = 'none'
        ok = $false
        stage = 'decision'
        supervisor_task = 'untouched'
        reconcile_ran = $false
        process_currency = 'not_claimed'
        responsiveness = 'unknown'
        legacy_records = 'unknown'
        apply_summary = ''
        verify_summary = ''
        tools_binding = $null
        reasons = @()
        notes = @()
    }
    $inputs = & $Observe
    if ($inputs -isnot [hashtable]) {
        $result['reasons'] = @('the observation port did not return one table')
        return [pscustomobject]$result
    }
    $decision = Get-WdBridgeLimitedBootstrapDecision @inputs
    if ($decision.action -cne 'launch') {
        $result['reasons'] = @($decision.reasons)
        return [pscustomobject]$result
    }

    # The one launch: the bundle's own reconcile as this Limited user, under its Global mutexes.
    $result['stage'] = 'apply'
    $result['reconcile_ran'] = $true
    $applied = Get-WdBridgeReconcileVerdict -Run (& $RunSupervisorOnce $true) -Mode 'APPLY'
    $result['apply_summary'] = $applied.summary
    $result['notes'] = @($applied.notes)
    if (-not $applied.ok) {
        $result['reasons'] = @($applied.reasons)
        return [pscustomobject]$result
    }

    $result['stage'] = 'tools'
    $pinned = $false
    for ($second = 0; $second -le $ToolsWaitSeconds; $second++) {
        $record = & $ReadToolsReadiness
        if (Test-WdBridgeToolsReadinessPinned -Record $record -Generation ([string]$inputs['ExpectedGeneration']) `
                -ThreadId ([string]$inputs['ToolsThreadId']) -GetProcess $GetProcess) {
            $pinned = $true
            break
        }
        if ($second -lt $ToolsWaitSeconds) { & $Sleep 1 }
    }
    if (-not $pinned) {
        $result['reasons'] = @('no Tools readiness record bound the pinned generation and thread to the live consumer and native codex.exe')
        return [pscustomobject]$result
    }
    $binding = [ordered]@{}
    foreach ($property in @($record.PSObject.Properties)) {
        if ($property.Name -cin @('schema', 'generation', 'thread_id', 'pid', 'native_pid')) { $binding[$property.Name] = $property.Value }
        elseif ($property.Name -cin @('process_start_utc', 'native_process_start_utc', 'ready_at_utc')) {
            $binding[$property.Name] = (ConvertTo-WdBridgeUtc $property.Value).ToString('o', [Globalization.CultureInfo]::InvariantCulture)
        }
    }
    $result['tools_binding'] = [pscustomobject]$binding

    $result['stage'] = 'verify'
    $verified = Get-WdBridgeReconcileVerdict -Run (& $RunSupervisorOnce $false) -Mode 'dry-run'
    $result['verify_summary'] = $verified.summary
    $result['notes'] = @($applied.notes) + @($verified.notes)
    if (-not $verified.ok) {
        $result['reasons'] = @($verified.reasons)
        return [pscustomobject]$result
    }

    $result['stage'] = 'mode'
    $after = & $Observe
    $again = if ($after -is [hashtable]) { Get-WdBridgeLimitedBootstrapDecision @after } else { $null }
    if ($null -eq $again -or $again.action -cne 'launch') {
        $result['reasons'] = @('the mode or a pin changed during the bootstrap')
        return [pscustomobject]$result
    }
    $result['stage'] = 'done'
    $result['ok'] = $true
    $result['process_currency'] = 'five_watchers_and_tools_process_current'
    return [pscustomobject]$result
}

function Get-WdBridgeLimitedObservation {
    param(
        [Parameter(Mandatory)] [string] $SupervisorTaskName,
        [Parameter(Mandatory)] [string[]] $DriverTaskNames,
        [Parameter(Mandatory)] [string] $DeploymentManifestPath,
        [Parameter(Mandatory)] [string] $ExpectedGeneration,
        [Parameter(Mandatory)] [string] $SupervisorConfigPath,
        [AllowEmptyString()] [string] $ToolsThreadId = ''
    )

    # Read-only: tasks the Limited user can see, in the root task folder.
    $root = [string][IO.Path]::DirectorySeparatorChar
    $tasks = @(Get-ScheduledTask -ErrorAction Stop | Where-Object { $_.TaskPath -ceq $root })
    $supervisor = @($tasks | Where-Object { $_.TaskName -ceq $SupervisorTaskName })
    $drivers = foreach ($name in $DriverTaskNames) {
        $found = @($tasks | Where-Object { $_.TaskName -ceq $name })
        if ($found.Count -eq 0) { [pscustomobject]@{ present = $false; enabled = $null; state = '' } }
        elseif ($found.Count -eq 1) {
            [pscustomobject]@{ present = $true; enabled = [bool]$found[0].Settings.Enabled; state = [string]$found[0].State }
        }
        else { [pscustomobject]@{ present = $null; enabled = $null; state = '' } }
    }
    $deployed = ''
    try { $deployed = [string](Get-Content -LiteralPath $DeploymentManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json).source_commit }
    catch { $deployed = '' }
    $agents = $null
    try { $agents = [string[]]@((Get-Content -LiteralPath $SupervisorConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json).watchers.agents) }
    catch { $agents = $null }
    $principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    return @{
        IsAdministrator = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        SupervisorTaskEnabled = if ($supervisor.Count -eq 1) { [bool]$supervisor[0].Settings.Enabled } else { $null }
        SupervisorTaskState = if ($supervisor.Count -eq 1) { [string]$supervisor[0].State } else { '' }
        DriverTasks = @($drivers)
        DeployedGeneration = $deployed
        ExpectedGeneration = $ExpectedGeneration
        WatcherAgents = $agents
        ToolsThreadId = $ToolsThreadId
    }
}

function Read-WdBridgeToolsReadiness {
    param([Parameter(Mandatory)] [string] $Path)

    try {
        $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
        if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $item.Length -gt 65536) {
            return $null
        }
        return ([IO.File]::ReadAllText($item.FullName, [Text.Encoding]::UTF8) | ConvertFrom-Json -ErrorAction Stop)
    }
    catch { return $null }
}

function Read-WdBridgeToolsThreadPin {
    param([Parameter(Mandatory)] [string] $Worktree)

    # The identity the Tools consumer resumes (start-wd-tools-consumer.ps1 Get-WdNativeToolsResumeState).
    $saved = Read-WdBridgeToolsReadiness -Path (Join-Path (Join-Path (Join-Path $Worktree '.codex-audit') 'wd-turn-loop') 'conversation.json')
    if ($null -eq $saved -or $saved -isnot [Management.Automation.PSCustomObject]) { return '' }
    $field = @{}
    foreach ($property in @($saved.PSObject.Properties)) {
        if ($property.Name -cin @('schema', 'agent', 'worktree', 'thread_id')) { $field[$property.Name] = $property.Value }
    }
    if ($field['schema'] -cne 'wd.codex-conversation.v1' -or $field['agent'] -cne 'codex-tools-1' -or
        $field['worktree'] -isnot [string] -or -not $field['worktree'].Equals($Worktree, [StringComparison]::OrdinalIgnoreCase) -or
        $field['thread_id'] -isnot [string]) {
        return ''
    }
    return $field['thread_id']
}

function Get-WdBridgeProcessFacts {
    param([Parameter(Mandatory)] [int] $ProcessId)

    try {
        $found = @(Get-CimInstance -ClassName Win32_Process -Filter ('ProcessId={0}' -f $ProcessId) -ErrorAction Stop)
    }
    catch { return $null }
    if ($found.Count -ne 1 -or $found[0].CreationDate -isnot [datetime]) { return $null }
    return @{
        process_id = [int]$found[0].ProcessId
        parent_process_id = [int]$found[0].ParentProcessId
        name = [string]$found[0].Name
        created_utc = ([DateTimeOffset]$found[0].CreationDate).ToUniversalTime()
    }
}

function Get-WdBridgeSupervisorArguments {
    param(
        [Parameter(Mandatory)] [string] $SupervisorScript,
        [Parameter(Mandatory)] [string] $ConfigPath,
        [Parameter(Mandatory)] [bool] $Apply
    )

    # -BridgeWorkersOnly on BOTH runs: the supervisor then only verifies the merge-driver HOLD and
    # never disables or stops a task. The launcher and the supervisor are advanced scripts, so one
    # without the switch fails parameter binding (exit 1) before it runs, and the verdict refuses.
    $arguments = @('-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $SupervisorScript)
    if ($Apply) { $arguments += '-Apply' }
    $arguments += @('-BridgeWorkersOnly', '-ConfigPath', $ConfigPath)
    foreach ($argument in $arguments) {
        if ($argument.Length -eq 0 -or $argument.IndexOf('"') -ge 0 -or
            $argument.EndsWith([string][IO.Path]::DirectorySeparatorChar)) {
            throw 'a supervisor argument cannot be quoted exactly'
        }
    }
    return $arguments
}

function Invoke-WdBridgeSupervisorOnce {
    param(
        [Parameter(Mandatory)] [string] $HostPath,
        [Parameter(Mandatory)] [string] $SupervisorScript,
        [Parameter(Mandatory)] [string] $ConfigPath,
        [Parameter(Mandatory)] [bool] $Apply
    )

    $arguments = @(Get-WdBridgeSupervisorArguments -SupervisorScript $SupervisorScript -ConfigPath $ConfigPath -Apply $Apply)
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $HostPath
    $info.Arguments = (@($arguments | ForEach-Object { '"' + $_ + '"' }) -join ' ')
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    # A PowerShell 7 caller leaks its module path into Windows PowerShell 5.1.
    $info.EnvironmentVariables['PSModulePath'] = (@(
            (Join-Path (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'WindowsPowerShell') 'Modules'),
            (Join-Path (Join-Path $env:ProgramFiles 'WindowsPowerShell') 'Modules'),
            (Join-Path (Join-Path (Join-Path (Join-Path $env:SystemRoot 'System32') 'WindowsPowerShell') 'v1.0') 'Modules')
        ) -join ';')
    $process = [Diagnostics.Process]::Start($info)
    $errorText = $process.StandardError.ReadToEndAsync()
    $output = $process.StandardOutput.ReadToEnd()
    $process.WaitForExit()
    return [pscustomobject]@{
        exit_code = [int]$process.ExitCode
        lines = @($output -split "`r?`n" | Where-Object { $_.Length -gt 0 })
        error_text = [string]$errorText.Result
    }
}

function Invoke-WdBridgeWrapperBootstrap {
    param(
        [Parameter(Mandatory)] [string] $BundleRoot,
        [Parameter(Mandatory)] [string] $FleetManifestPath,
        [Parameter(Mandatory)] [string] $SupervisorScript,
        [Parameter(Mandatory)] [string] $HostPath
    )

    # The fleet wrapper's real ports. SupervisorScript is the installed wd_supervisor.ps1 launcher
    # that the WD-Supervisor task runs; the config is the bundle's own, the one that task uses.
    $wdbTools = (Get-Content -LiteralPath $FleetManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json).tools_supervisor
    $wdbConfigPath = Join-Path $BundleRoot 'wd_supervisor_loop.json'
    $wdbDrivers = (Get-Content -LiteralPath $wdbConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json).driver_containment
    $wdbObservation = @{
        SupervisorTaskName = [string]$wdbTools.task_name
        DriverTaskNames = @([string]$wdbDrivers.standing_task, [string]$wdbDrivers.legacy_task)
        DeploymentManifestPath = Join-Path $BundleRoot 'deployment-manifest.json'
        ExpectedGeneration = Split-Path -Leaf $BundleRoot
        SupervisorConfigPath = $wdbConfigPath
        ToolsThreadId = Read-WdBridgeToolsThreadPin -Worktree ([string]$wdbTools.worktree)
    }
    return Invoke-WdBridgeLimitedBootstrap `
        -Observe { Get-WdBridgeLimitedObservation @wdbObservation } `
        -RunSupervisorOnce {
            param([bool] $ApplyRun)
            Invoke-WdBridgeSupervisorOnce -HostPath $HostPath -SupervisorScript $SupervisorScript `
                -ConfigPath $wdbConfigPath -Apply $ApplyRun
        } `
        -ReadToolsReadiness { Read-WdBridgeToolsReadiness -Path ([string]$wdbTools.readiness_path) } `
        -GetProcess { param([int] $ProcessId) Get-WdBridgeProcessFacts -ProcessId $ProcessId } `
        -Sleep { param([int] $Seconds) Start-Sleep -Seconds $Seconds } `
        -ToolsWaitSeconds ([int]$wdbTools.wait_seconds)
}
