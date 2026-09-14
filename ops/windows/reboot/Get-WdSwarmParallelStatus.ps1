#requires -Version 5.1
<#
.SYNOPSIS
    Reports compact-state, wake, and write-scope parallelism for the WD fleet.

.DESCRIPTION
    Read-only. It does not acknowledge bridge events, consume wake sentinels,
    change Git state, or start processes. Missing or stale compact state is
    reported, never repaired implicitly.

    Compatibility: runnable and summary.runnable_lanes retain their v1 meaning
    (a recorded next action outside the literal blocked status). They do not
    establish current runtime readiness. runnable_evidence is an observation,
    not task authority or a scheduler decision. No heartbeat is counted as
    substantive progress. A readiness record is not a full runtime attestation.
#>
[CmdletBinding()]
param(
    [string] $ManifestPath = '',
    [string] $CurrentStatePath = 'C:\Python\WD_REBOOT_STATE_CURRENT.json',
    [ValidateRange(60, 86400)]
    [int] $StaleAfterSeconds = 1800,
    [switch] $Json
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Resolve-WdStatusManifest {
    param([string] $Requested)

    if ($Requested) { return [IO.Path]::GetFullPath($Requested) }
    $pointerPath = $CurrentStatePath
    if (-not (Test-Path -LiteralPath $pointerPath -PathType Leaf)) {
        throw "current reboot pointer is missing: $pointerPath"
    }
    $pointer = Read-WdStatusRecord -Path $pointerPath
    if ([string]::IsNullOrWhiteSpace([string]$pointer.fleet_manifest)) {
        throw 'current reboot pointer has no fleet_manifest'
    }
    return [IO.Path]::GetFullPath([string]$pointer.fleet_manifest)
}

function Get-WdStatusGitText {
    param(
        [Parameter(Mandatory)] [string] $Git,
        [Parameter(Mandatory)] [string] $Worktree,
        [Parameter(Mandatory)] [string[]] $Arguments
    )
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& $Git --no-replace-objects -C $Worktree @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
    if ($exitCode -ne 0) { return '' }
    return (@($output | ForEach-Object { [string]$_ }) -join "`n").Trim()
}

function Get-WdStatusProperty {
    param($Object, [string] $Name)
    if ($null -ne $Object -and $null -ne $Object.PSObject.Properties[$Name]) {
        return $Object.PSObject.Properties[$Name].Value
    }
    return $null
}

function Read-WdStatusRecord {
    param([string] $Path)
    # Bound memory and I/O even if a concurrently written record grows.
    # Permit atomic checkpoint/readiness replacement while this snapshot is open.
    $stream = [IO.File]::Open($Path, [IO.FileMode]::Open,
        [IO.FileAccess]::Read, ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
    try {
        $buffer = New-Object byte[] 32769
        $count = 0
        while ($count -lt $buffer.Length) {
            $read = $stream.Read($buffer, $count, $buffer.Length - $count)
            if ($read -eq 0) { break }
            $count += $read
        }
        if ($count -gt 32768) { throw 'record exceeds 32 KiB' }
        return ([Text.Encoding]::UTF8.GetString($buffer, 0, $count).TrimStart(
            [char]0xFEFF) | ConvertFrom-Json -ErrorAction Stop)
    }
    finally { $stream.Dispose() }
}

function Get-WdStatusRuntime {
    param($Definition, $InstalledBundle, [DateTimeOffset] $Now)
    $result = [pscustomobject]@{
        source_domain = 'process_query_and_readiness_record'
        identity = 'unknown'
        reason = 'no_readiness_source'
        readiness_path = [string]$Definition.readiness_path
        readiness_status = 'unknown'
        readiness_age_seconds = $null
        recorded_pid = $null
        recorded_process_start_utc = $null
        recorded_generation = $null
        observed_pid = $null
        observed_process_start_utc = $null
        observed_generation = $null
    }
    if (-not $result.readiness_path) { return $result }
    try {
        $result.reason = 'readiness_missing_or_invalid'
        $record = Read-WdStatusRecord -Path $result.readiness_path
        $created = [DateTimeOffset]::Parse([string]$record.process_start_utc,
            [Globalization.CultureInfo]::InvariantCulture).ToUniversalTime()
        $readyAt = [DateTimeOffset]::Parse([string]$record.ready_at_utc,
            [Globalization.CultureInfo]::InvariantCulture).ToUniversalTime()
        if (
            [string]$record.schema -cne 'wd.tools-consumer-ready.v1' -or
            [string]$record.status -cnotin @('ready', 'degraded') -or
            [string]$record.generation -cnotmatch '^[0-9a-f]{40}$' -or
            [string]$record.pid -cnotmatch '^[1-9][0-9]{0,9}$' -or
            [int64]$record.pid -gt [int]::MaxValue -or
            -not ([string]$record.worktree).Equals(
                [string]$Definition.worktree, [StringComparison]::OrdinalIgnoreCase) -or
            $readyAt -lt $created -or $readyAt -gt $Now.AddSeconds(5)
        ) { return $result }
        $result.readiness_status = [string]$record.status
        $result.readiness_age_seconds = [Math]::Max(0,
            [int64][Math]::Floor(($Now - $readyAt).TotalSeconds))
        $result.recorded_pid = [int]$record.pid
        $result.recorded_process_start_utc = $created.ToString('o')
        $result.recorded_generation = [string]$record.generation
        $result.reason = 'process_query_unavailable'
        $processes = @(Get-CimInstance -ClassName Win32_Process `
            -Filter ("ProcessId={0}" -f $result.recorded_pid) -ErrorAction Stop)
        if ($processes.Count -eq 0) {
            $result.identity = 'absent'
            $result.reason = 'recorded_pid_absent'
            return $result
        }
        if ($processes.Count -ne 1) { return $result }
        $process = $processes[0]
        $result.observed_pid = [int]$process.ProcessId
        $observedStart = [DateTimeOffset]([datetime]$process.CreationDate).ToUniversalTime()
        $result.observed_process_start_utc = $observedStart.ToString('o')
        if ($result.observed_pid -ne $result.recorded_pid -or
            [Math]::Abs(($observedStart - $created).TotalSeconds) -gt 1) {
            $result.identity = 'mismatch'
            $result.reason = 'process_identity_mismatch'
            return $result
        }
        # Observe the ordinary launcher argument without executing or disclosing
        # the process command line. Missing/ambiguous generation stays unknown.
        $pattern = '(?i)(?:^|\s)-Generation\s+(?:"([0-9a-f]{40})"|''([0-9a-f]{40})''|([0-9a-f]{40}))(?=\s|$)'
        $generationArguments = [regex]::Matches([string]$process.CommandLine, $pattern)
        $result.reason = 'process_generation_unknown'
        if ($generationArguments.Count -ne 1) { return $result }
        $result.observed_generation = (@(1..3 | ForEach-Object {
            $generationArguments[0].Groups[$_].Value
        } | Where-Object { $_ }) -join '').ToLowerInvariant()
        if ($result.observed_generation -cne $result.recorded_generation) {
            $result.identity = 'mismatch'
            $result.reason = 'readiness_generation_mismatch'
            return $result
        }
        $result.reason = 'installed_generation_unknown'
        if ($InstalledBundle.status -cne 'recorded' -or
            -not $InstalledBundle.matches_selected_manifest) { return $result }
        if ($result.observed_generation -cne $InstalledBundle.source_commit) {
            $result.identity = 'mismatch'
            $result.reason = 'installed_generation_mismatch'
            return $result
        }
        $result.identity = 'matched'
        $result.reason = 'pid_start_and_generation_match'
    }
    catch {
        # Keep the failed evidence stage visible, never infer a live process.
        $result.identity = 'unknown'
    }
    return $result
}

$manifestFull = Resolve-WdStatusManifest -Requested $ManifestPath
if (-not (Test-Path -LiteralPath $manifestFull -PathType Leaf)) {
    throw "fleet manifest is missing: $manifestFull"
}
$manifest = Get-Content -LiteralPath $manifestFull -Raw |
    ConvertFrom-Json -ErrorAction Stop
if ([int]$manifest.schema_version -ne 2) {
    throw "unsupported fleet manifest schema: $($manifest.schema_version)"
}
$git = [string]$manifest.git_executable
if (-not (Test-Path -LiteralPath $git -PathType Leaf)) {
    throw "fleet Git executable is missing: $git"
}

$definitions = [Collections.Generic.List[object]]::new()
foreach ($lane in @($manifest.lanes)) {
    $definitions.Add([pscustomobject]@{
        agent = [string]$lane.agent
        worktree = [IO.Path]::GetFullPath([string]$lane.worktree)
        readiness_path = ''
    })
}
$definitions.Add([pscustomobject]@{
    agent = [string]$manifest.tools_supervisor.agent
    worktree = [IO.Path]::GetFullPath(
        [string]$manifest.tools_supervisor.worktree
    )
    readiness_path = [string](Get-WdStatusProperty $manifest.tools_supervisor 'readiness_path')
})
if (@($definitions).Count -ne 5) {
    throw 'parallel status requires exactly five unique lane definitions'
}
if (@($definitions.agent | Select-Object -Unique).Count -ne 5) {
    throw 'parallel status lane identities are not unique'
}

$runtimeRoot = [IO.Path]::GetFullPath([string]$manifest.runtime_root)
$now = [DateTimeOffset]::UtcNow
$installedBundle = [pscustomobject]@{
    source_domain = 'installed_pointer'
    source_path = $CurrentStatePath
    status = 'unknown'
    source_commit = $null
    active_bundle = $null
    installed_at_utc = $null
    matches_selected_manifest = $false
}
try {
    $installed = Read-WdStatusRecord -Path $CurrentStatePath
    if ([int]$installed.schema_version -ne 1 -or
        [string]$installed.source_commit -cnotmatch '^[0-9a-f]{40}$' -or
        [string]::IsNullOrWhiteSpace([string]$installed.active_bundle) -or
        [string]::IsNullOrWhiteSpace([string]$installed.fleet_manifest)) {
        throw 'invalid installation pointer'
    }
    $installedBundle.source_commit = [string]$installed.source_commit
    $installedBundle.active_bundle = [IO.Path]::GetFullPath([string]$installed.active_bundle)
    $installedBundle.installed_at_utc = Get-WdStatusProperty $installed 'installed_at_utc'
    $installedBundle.matches_selected_manifest = ([IO.Path]::GetFullPath(
        [string]$installed.fleet_manifest)).Equals($manifestFull,
        [StringComparison]::OrdinalIgnoreCase)
    $installedBundle.status = 'recorded'
}
catch { $installedBundle.status = 'unknown' }
$supervisor = [pscustomobject]@{
    source_domain = 'scheduled_task_query'
    task_name = [string](Get-WdStatusProperty $manifest.tools_supervisor 'task_name')
    status = 'unknown'
    observed_state = $null
}
try {
    if ($supervisor.task_name) {
        $task = @(Get-ScheduledTask -TaskName $supervisor.task_name -ErrorAction Stop)
        if ($task.Count -eq 1) {
            $supervisor.observed_state = [string]$task[0].State
            $supervisor.status = switch ($supervisor.observed_state) {
                'Disabled' { 'disabled' }
                'Ready' { 'enabled' }
                'Running' { 'enabled' }
                default { 'unknown' }
            }
        }
    }
}
catch { $supervisor.status = 'unknown' }
$lanes = [Collections.Generic.List[object]]::new()
$scopeOwners = @{}
foreach ($definition in @($definitions)) {
    $agent = [string]$definition.agent
    $worktree = [string]$definition.worktree
    $statePath = Join-Path $worktree '.codex-audit\wd-current-state.json'
    $stateHealth = 'missing'
    $state = $null
    $ageSeconds = $null
    if (Test-Path -LiteralPath $statePath -PathType Leaf) {
        try {
            $state = Read-WdStatusRecord -Path $statePath
            $updated = [DateTimeOffset]::Parse(
                [string]$state.updated_at_utc,
                [Globalization.CultureInfo]::InvariantCulture
            ).ToUniversalTime()
            if ($updated -gt $now.AddSeconds(5)) { throw 'checkpoint is future dated' }
            $ageSeconds = [Math]::Max(
                0,
                [int64][Math]::Floor(($now - $updated).TotalSeconds)
            )
            if (
                [string]$state.schema -cne 'wd.lane-current.v1' -or
                [string]$state.agent -cne $agent -or
                -not ([string]$state.worktree).Equals(
                    $worktree,
                    [StringComparison]::OrdinalIgnoreCase
                ) -or
                [string]$state.status -cnotmatch '^[a-z][a-z0-9_-]{0,63}$' -or
                [string]$state.head -cnotmatch '^[0-9a-f]{40}$' -or
                $null -eq $state.PSObject.Properties['write_scope'] -or
                [string]::IsNullOrWhiteSpace([string]$state.task_id) -or
                [string]::IsNullOrWhiteSpace([string]$state.next_action)
            ) {
                throw 'state identity mismatch'
            }
            $stateHealth = if ($ageSeconds -gt $StaleAfterSeconds) {
                'stale'
            } else { 'current' }
        }
        catch {
            $stateHealth = 'invalid'
            $state = $null
            $ageSeconds = $null
        }
    }

    $branch = Get-WdStatusGitText -Git $git -Worktree $worktree -Arguments @(
        'branch', '--show-current'
    )
    $head = Get-WdStatusGitText -Git $git -Worktree $worktree -Arguments @(
        'rev-parse', 'HEAD'
    )
    $scope = if ($null -eq $state) { @() } else {
        @($state.write_scope | ForEach-Object { ([string]$_).Trim() } |
            Where-Object { $_ })
    }
    foreach ($path in $scope) {
        $key = $path.Replace('/', '\').ToLowerInvariant()
        if (-not $scopeOwners.ContainsKey($key)) {
            $scopeOwners[$key] = [Collections.Generic.List[string]]::new()
        }
        $scopeOwners[$key].Add($agent)
    }
    $status = if ($null -eq $state) { '' } else { [string]$state.status }
    $nextAction = if ($null -eq $state) { '' } else {
        [string]$state.next_action
    }
    $runtime = Get-WdStatusRuntime -Definition $definition `
        -InstalledBundle $installedBundle -Now $now
    $headMatches = ($null -ne $state -and $head -and
        [string](Get-WdStatusProperty $state 'head') -ceq $head)
    $blockers = @(Get-WdStatusProperty $state 'blockers' | Where-Object {
        -not [string]::IsNullOrWhiteSpace([string]$_)
    })
    $runnableEvidence = 'unknown'
    if ($stateHealth -ceq 'current' -and
        ($status -cin @('blocked', 'waiting', 'paused', 'stopped', 'disabled',
            'complete', 'completed', 'done', 'idle') -or $blockers.Count -gt 0)) {
        $runnableEvidence = 'not_observed'
    }
    elseif ($definition.readiness_path -and $supervisor.status -ceq 'disabled') {
        $runnableEvidence = 'not_observed'
    }
    elseif ($runtime.identity -cin @('absent', 'mismatch') -or
        $runtime.readiness_status -ceq 'degraded') {
        $runnableEvidence = 'not_observed'
    }
    elseif ($stateHealth -ceq 'current' -and $headMatches -and
        $status -cin @('ready', 'working', 'running', 'active', 'in_progress', 'in-progress') -and
        -not [string]::IsNullOrWhiteSpace($nextAction) -and
        $runtime.identity -ceq 'matched' -and
        $runtime.readiness_status -ceq 'ready' -and $supervisor.status -ceq 'enabled') {
        $runnableEvidence = 'observed'
    }
    $lanes.Add([pscustomobject]@{
        agent = $agent
        worktree = $worktree
        state_health = $stateHealth
        age_seconds = $ageSeconds
        task_id = if ($null -eq $state) { '' } else { [string]$state.task_id }
        status = $status
        branch = $branch
        head = $head
        checkpoint_head = if ($null -eq $state) { '' } else {
            [string]$state.head
        }
        head_matches = (
            $null -ne $state -and $head -and
            [string]$state.head -ceq $head
        )
        write_scope = @($scope)
        next_action = $nextAction
        recorded_next_action = $nextAction
        runnable_evidence = $runnableEvidence
        checkpoint = [pscustomobject]@{
            source_domain = 'lane_checkpoint'
            source_path = $statePath
            freshness = $stateHealth
            updated_at_utc = Get-WdStatusProperty $state 'updated_at_utc'
            age_seconds = $ageSeconds
        }
        checkout_source_domain = 'manifest_worktree_git_query'
        runtime = $runtime
        progress = [pscustomobject]@{
            status = 'unknown'
            source_domain = 'not_observed'
            last_substantive_progress_at_utc = $null
            wait_age_seconds = $null
        }
        runnable = (
            $null -ne $state -and
            $status -cne 'blocked' -and
            -not [string]::IsNullOrWhiteSpace($nextAction)
        )
        wake_pending = Test-Path -LiteralPath (
            Join-Path $runtimeRoot ("wake_{0}" -f $agent)
        ) -PathType Leaf
    })
}

$collisions = @(
    foreach ($key in @($scopeOwners.Keys | Sort-Object)) {
        $owners = @($scopeOwners[$key] | Select-Object -Unique)
        if ($owners.Count -gt 1) {
            [pscustomobject]@{ write_scope = $key; agents = $owners }
        }
    }
)
$report = [pscustomobject]@{
    schema = 'wd.swarm-parallel-status.v1'
    observed_at_utc = $now.ToString('o')
    manifest = $manifestFull
    stale_after_seconds = $StaleAfterSeconds
    installed_bundle = $installedBundle
    supervisor = $supervisor
    semantics = [pscustomobject]@{
        runnable = 'legacy recorded next action; no freshness or runtime guarantee'
        runnable_evidence = 'observed requires current matching checkpoint with recognized active status, no recorded blocker, enabled supervisor and matching ready PID/start/generation; not authority or full runtime attestation'
        installed_bundle = 'installation pointer record, not proof of running code or package integrity'
        progress = 'not inferred from checkpoint, readiness, wake or heartbeat timestamps'
    }
    lanes = @($lanes)
    summary = [pscustomobject]@{
        total_lanes = @($lanes).Count
        current_checkpoints = @($lanes | Where-Object {
                $_.state_health -ceq 'current'
            }).Count
        runnable_lanes = @($lanes | Where-Object { $_.runnable }).Count
        fresh_runnable_evidence_lanes = @($lanes | Where-Object {
                $_.runnable_evidence -ceq 'observed'
            }).Count
        unknown_runnable_evidence_lanes = @($lanes | Where-Object {
                $_.runnable_evidence -ceq 'unknown'
            }).Count
        matched_runtime_identities = @($lanes | Where-Object {
                $_.runtime.identity -ceq 'matched'
            }).Count
        blocked_lanes = @($lanes | Where-Object {
                $_.status -ceq 'blocked'
            }).Count
        pending_wakes = @($lanes | Where-Object { $_.wake_pending }).Count
        scope_collisions = $collisions.Count
    }
    scope_collisions = @($collisions)
}

if ($Json) {
    $report | ConvertTo-Json -Depth 8
} else {
    $report.lanes | Format-Table `
        agent, state_health, age_seconds, status, task_id, head_matches,
        runnable_evidence, wake_pending -AutoSize
    $report.summary | Format-List
    if ($collisions.Count -gt 0) {
        $collisions | Format-Table write_scope, agents -AutoSize
    }
}
