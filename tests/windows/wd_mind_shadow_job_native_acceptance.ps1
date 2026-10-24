#requires -Version 5.1
<#
.SYNOPSIS
    Opt-in native acceptance harness for ops/windows/reboot/Start-WdMindShadowJob.ps1 (cases A1-A7). It refuses by
    default and is never collected or run by CI.

.DESCRIPTION
    Checkpoint 1 holds only the default refusal, the fail-closed known-member witness (G1/D7) and the run-bound case
    record publication (G4). Every case is UNKNOWN until its body exists and an approved executor runs it on the target
    host at the approved token (D4/D5). Nothing here starts a process, loads a native type or imports the adapter.

    Witness (D7 baseline, RCO2's per-member example): each synthetic member creates <witness dir>\<member>.witness with
    CreateNew, writes one start line "<launch id> <member id>" and holds the handle with FileShare.None until it ends;
    the OS releases the handle then. The observer opens each file exclusively and holds no job handle. A missing,
    invalid or foreign start line, a held file (a survivor) or an unknown state is never PASS. No claim is made about
    processes created through a broker or a service.

    Checkpoint 2 (Lead Source-preparation GO, 2026-10-21): pure case evaluators turn one strict observation per case
    into PASS, FAIL, UNKNOWN or NOT_READY. The contract is RCO1's A1-A7 (proposal v2 as locked by Lead) with RCO2's
    G1-G4, the member witness and the A1 pipe-holding grandchild, and Lead's guard cases (off switch before and during
    the run, a denied switch read, the floor before the start and during the run, reparse ancestry, one confirmed
    Stop, the deadline) placed as A1 and A7 sub-cases. An observation must have exactly the expected keys and types
    and the run binding (A1-A6: harness run, case, launch id, root PID, root creation time, deadline; A7: its
    pre-start attempt binding, below); a missing, extra, mistyped or partial value is UNKNOWN and a foreign binding is
    FAIL. A4 is always NOT_READY: A4 is the job kill at the publication replacement (just before and just after the
    latest.json replace: the pointer is the old or the new complete one, last_ok intact, temp leftovers listed;
    Lead's correction 2026-10-22T19:14:40Z), no driver here produces that evidence, and no whole run can PASS until
    one does. No native driver produces observations yet, so every case stays NOT_READY (native NOT_RUN); a synthetic
    or supplied PASS is never native acceptance, and the script takes no observation input.

    Checkpoint 3 (Lead A7 driver GO, 2026-10-22): A7 pre-start refusal drivers. A driver plans one sub-case as the
    approved adapter inputs with at most one deviation, runs the actual adapter once in a child through injected seams
    and derives the four refusal observations from the child's exit code and error stream, two listings of the run
    root and the child's live children. RCO1's A1-A7 wording selects no particular adapter refusal (UNSPECIFIED, RCO1
    2026-10-22T19:01:24Z), so the refusal per sub-case is this file's explicit choice: T1 integrity, T1 user SID, R1
    type, the off switch, the floor and run-root reparse ancestry (Lead's v5 delta adds the user SID variant that
    RCO1's 10-17 refinement names).

    Lead's v6 decision (2026-10-22T19:41:59Z): the native A7 scope is those seven pre-start guards; the zero- and
    two-function lane-loop sub-cases cannot reach the count guard under the immutable production manifest, so they are
    structural fixture coverage only (Get-WdShadowA7Coverage), never native PASS. A7 binds to a versioned pre-start
    attempt binding (harness run, the measured controller id and creation time, Source and pin identity, one expected
    attempt id per sub-case, the measured probe id and creation time), never to a managed launch id or root PID. An
    empty live-child listing is never no-start evidence, so without an owned-job witness a driver observation stays
    UNKNOWN; the uncontained real child and lister seams are disabled (they refuse before any process exists). The
    probe child and the adapter's whoami.exe are probe processes, not the producer workload. The script never calls
    the drivers; A7 stays NOT_READY, native NOT_RUN.

    Checkpoint 4 (Lead's v7 GO, 2026-10-22T20:06:21Z): the A1, A2, A3, A5 and A6 lifetime driver bodies
    (Invoke-WdShadowLifetimeDriver). Each runs one sub-case: it preregisters the synthetic members
    (wd_mind_shadow_acceptance_member.ps1) in a fresh run-bound witness directory, launches the root member in an
    owned job, waits under the adapter's deadline, guard and drain rules, stops once, confirms the drain, reads the
    member witnesses and, for A6, reacquires the lock itself; A5 kills the controller instead. The real job, root
    identity, context, controller-kill and handle seams are disabled until the pinned owned-job API is reconciled once,
    so the drivers run only on strict fakes. The workload is the synthetic member, never the producer, so the
    adapter's own loop is not exercised. The old case binding names one launch and root, so only A2, A5 and A6
    compose into a case observation; A1 and A3 need one launch per sub-case. The script never calls the drivers.

    Design: RCO1 (A1-A7), RCO2 (G1-G4 and the witness), Lead (decisions and the guard cases); fable-5 wrote this file.
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [switch] $OptInNativeAcceptance
)
if (-not $OptInNativeAcceptance -or [string]$env:WD_MIND_SHADOW_NATIVE_ACCEPTANCE -cne 'opt-in') {
    Write-Error 'refused: the native acceptance harness runs only on an explicit opt-in by an approved executor'
    exit 3
}
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Get-WdShadowMemberObservation {
    param([string] $WitnessDirectory, [object[]] $Expected)
    $observed = @{}
    foreach ($member in @($Expected)) {
        $path = Join-Path $WitnessDirectory ([string]$member + '.witness')
        $entry = @{ start_witness = 'missing'; released = 'UNKNOWN'; launch_id = '' }
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            $entry.start_witness = 'present'
            try {
                $stream = New-Object IO.FileStream($path, [IO.FileMode]::Open, [IO.FileAccess]::Read,
                    [IO.FileShare]::None)
                try {
                    $line = [string](New-Object IO.StreamReader($stream)).ReadLine()
                    $parts = @($line.Split(' '))
                    if ($parts.Count -eq 2 -and $parts[1] -ceq [string]$member) {
                        $entry.launch_id = $parts[0]
                    } else {
                        $entry.start_witness = 'invalid'
                    }
                    $entry.released = $true
                } finally { $stream.Dispose() }
            } catch [IO.IOException] {
                $entry.released = $false
            }
        }
        $observed[[string]$member] = $entry
    }
    return $observed
}

function Test-WdShadowMemberWitness {
    # G1 (RCO2) / D7 (Lead): PASS only for a non-empty preregistered member set in which every member has a start
    # witness bound to this launch and an OS-released lifetime witness; anything missing or unknown is not PASS.
    param([object[]] $Expected, [hashtable] $Observed, [string] $LaunchId)
    if ($null -eq $Expected -or $Expected.Count -eq 0 -or -not $LaunchId) { return 'UNKNOWN' }
    foreach ($member in $Expected) {
        $seen = $Observed[[string]$member]
        if ($null -eq $seen) { return 'FAIL' }
        if ([string]$seen.start_witness -cne 'present') { return 'FAIL' }
        if ([string]$seen.launch_id -cne $LaunchId) { return 'FAIL' }
        if ($seen.released -isnot [bool]) { return 'UNKNOWN' }
        if (-not $seen.released) { return 'FAIL' }
    }
    return 'PASS'
}

function Publish-WdShadowCaseRecord {
    # G4: one record per case, bound to the run, published once without overwrite.
    param([string] $Directory, [string] $CaseId, $Record)
    $final = Join-Path $Directory ('case_' + $CaseId + '.json')
    $temporary = Join-Path $Directory ('.case_' + $CaseId + '.json.tmp')
    $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes((($Record | ConvertTo-Json -Depth 8) + "`n"))
    $stream = New-Object IO.FileStream($temporary, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write,
        [IO.FileShare]::None)
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
    [IO.File]::Move($temporary, $final)
}

function Test-WdShadowExactKeys {
    # Strict observations: a hashtable with exactly the expected keys (ordinal, case-sensitive); anything else is
    # never evidence.
    param($Table, [string[]] $Keys)
    if ($Table -isnot [hashtable] -or $Table.Count -ne @($Keys).Count) { return $false }
    $actual = [string[]]@($Table.Keys | ForEach-Object { [string]$_ })
    $wanted = [string[]]@($Keys)
    [Array]::Sort($actual, [StringComparer]::Ordinal)
    [Array]::Sort($wanted, [StringComparer]::Ordinal)
    return (($actual -join "`n") -ceq ($wanted -join "`n"))
}

function Test-WdShadowCount {
    # A measured count is an integer, never text or a fraction.
    param($Value)
    return ($Value -is [int] -or $Value -is [long])
}

function Get-WdShadowStopVerdict {
    # A1 stop sub-cases: the named outcome, a confirmed drain to ActiveProcesses 0 and a nonnegative integer elapsed
    # time inside the bound deadline plus the stop wait (30 s). A deadline stop must not end before its bound
    # deadline; an early off-switch or floor stop may. A mistyped or negative value or no valid deadline is UNKNOWN.
    param($Observation, [string] $Outcome, $DeadlineSeconds)
    if (-not (Test-WdShadowExactKeys $Observation @('outcome', 'stop_confirmed', 'active_processes_after',
            'elapsed_seconds'))) { return 'UNKNOWN' }
    $active = $Observation['active_processes_after']
    $elapsed = $Observation['elapsed_seconds']
    if (-not (Test-WdShadowCount $active) -or -not (Test-WdShadowCount $elapsed) -or
        $Observation['stop_confirmed'] -isnot [bool] -or $Observation['outcome'] -isnot [string]) { return 'UNKNOWN' }
    if ($elapsed -lt 0 -or -not (Test-WdShadowCount $DeadlineSeconds) -or $DeadlineSeconds -le 0) { return 'UNKNOWN' }
    if ($Observation['outcome'] -cne $Outcome -or $active -ne 0 -or $elapsed -gt $DeadlineSeconds + 30 -or
        ($Outcome -ceq 'stopped_at_deadline' -and $elapsed -lt $DeadlineSeconds) -or
        -not $Observation['stop_confirmed']) { return 'FAIL' }
    return 'PASS'
}

function Get-WdShadowRefusalVerdict {
    # A7 sub-cases (R1, R2, T1 and the pre-start guards): refused with a nonzero exit and the refusal seen, and
    # neither a run directory nor a process was created.
    param($Observation)
    if (-not (Test-WdShadowExactKeys $Observation @('exit_code', 'refusal_seen', 'run_dir_created',
            'process_started'))) { return 'UNKNOWN' }
    # Lead's v6 decision: process_started may be $null (no no-start witness); a seen start, a run directory or a zero
    # exit still fails, and a missing witness is never PASS.
    $started = $Observation['process_started']
    if (-not (Test-WdShadowCount $Observation['exit_code']) -or $Observation['refusal_seen'] -isnot [bool] -or
        $Observation['run_dir_created'] -isnot [bool] -or ($null -ne $started -and $started -isnot [bool])) {
        return 'UNKNOWN'
    }
    if ($started -eq $true -or $Observation['run_dir_created'] -or $Observation['exit_code'] -eq 0) {
        return 'FAIL'
    }
    if ($started -isnot [bool]) { return 'UNKNOWN' }
    if ($Observation['refusal_seen']) { return 'PASS' }
    return 'UNKNOWN'
}

function Get-WdShadowSubcaseVerdict {
    # One sub-case of RCO1's A1-A7 contract (proposal v2) with RCO2's G1-G4 and Lead's guard cases.
    param([string] $CaseId, [string] $Name, $Observation, [hashtable] $Binding)
    $deadline = $null
    if ($null -ne $Binding -and $Binding['deadline_seconds'] -is [string] -and
        $Binding['deadline_seconds'] -cmatch '^[1-9][0-9]{0,5}\z') { $deadline = [long]$Binding['deadline_seconds'] }
    switch -CaseSensitive ($CaseId + '/' + $Name) {
        'A1/deadline' { return Get-WdShadowStopVerdict $Observation 'stopped_at_deadline' $deadline }
        'A1/pipe_holding_grandchild' { return Get-WdShadowStopVerdict $Observation 'stopped_at_deadline' $deadline }
        'A1/off_switch_during_run' { return Get-WdShadowStopVerdict $Observation 'stopped_by_off_switch' $deadline }
        'A1/disk_floor_hit' { return Get-WdShadowStopVerdict $Observation 'stopped_at_disk_floor' $deadline }
        'A1/early_root_exit_orphan' {
            # G3: the root exits early while an orphan lives; that is never completed.
            if (-not (Test-WdShadowExactKeys $Observation @('outcome', 'orphan_alive_at_root_exit')) -or
                $Observation['outcome'] -isnot [string] -or
                $Observation['orphan_alive_at_root_exit'] -isnot [bool] -or
                -not $Observation['orphan_alive_at_root_exit']) { return 'UNKNOWN' }
            if ($Observation['outcome'] -ceq 'stopped_at_deadline') { return 'PASS' }
            return 'FAIL'
        }
        'A1/single_confirmed_stop' {
            if (-not (Test-WdShadowExactKeys $Observation @('stop_calls', 'stop_confirmed')) -or
                -not (Test-WdShadowCount $Observation['stop_calls']) -or
                $Observation['stop_confirmed'] -isnot [bool]) { return 'UNKNOWN' }
            if ($Observation['stop_calls'] -eq 1 -and $Observation['stop_confirmed']) { return 'PASS' }
            return 'FAIL'
        }
        'A2/survivors' {
            if (-not (Test-WdShadowExactKeys $Observation @('live_descendants', 'independent_survivors',
                    'child_started_during_stop_survived'))) { return 'UNKNOWN' }
            $live = $Observation['live_descendants']
            $independent = $Observation['independent_survivors']
            if ($live -isnot [array] -or $independent -isnot [array] -or
                $Observation['child_started_during_stop_survived'] -isnot [bool]) { return 'UNKNOWN' }
            if ($live.Count -gt 0 -or $independent.Count -gt 0 -or
                $Observation['child_started_during_stop_survived']) { return 'FAIL' }
            return 'PASS'
        }
        { $_ -ceq 'A3/outer_no_limits' -or $_ -ceq 'A3/outer_kill_on_close' -or $_ -ceq 'A3/planned_launch_context' } {
            # Assignment succeeds or the start is refused; a start outside the job fails.
            if (-not (Test-WdShadowExactKeys $Observation @('result')) -or $Observation['result'] -isnot [string]) {
                return 'UNKNOWN'
            }
            if ($Observation['result'] -ceq 'assigned_before_resume' -or
                $Observation['result'] -ceq 'refused_before_start') { return 'PASS' }
            if ($Observation['result'] -ceq 'started_unassigned') { return 'FAIL' }
            return 'UNKNOWN'
        }
        'A5/controller_death' {
            # G1: termination evidence closed over the known member set, bound to this launch (D7 witness).
            if (-not (Test-WdShadowExactKeys $Observation @('expected', 'observed')) -or
                $Observation['expected'] -isnot [array] -or $Observation['observed'] -isnot [hashtable]) {
                return 'UNKNOWN'
            }
            return Test-WdShadowMemberWitness -Expected $Observation['expected'] -Observed $Observation['observed'] `
                -LaunchId ([string]$Binding['launch_id'])
        }
        'A6/inherited_handles' {
            # A6 with G2: no unexpected inherited handle and a positive exclusive live-dir lock reacquire.
            if (-not (Test-WdShadowExactKeys $Observation @('unexpected_inherited_handles', 'lock_reacquire'))) {
                return 'UNKNOWN'
            }
            $handles = $Observation['unexpected_inherited_handles']
            $lock = $Observation['lock_reacquire']
            if ($handles -isnot [array] -or
                -not (Test-WdShadowExactKeys $lock @('attempted', 'acquired', 'released', 'error')) -or
                $lock['attempted'] -isnot [bool] -or $lock['acquired'] -isnot [bool] -or
                $lock['released'] -isnot [bool] -or
                ($null -ne $lock['error'] -and $lock['error'] -isnot [string])) { return 'UNKNOWN' }
            if ($handles.Count -gt 0 -or
                ($lock['attempted'] -and (-not $lock['acquired'] -or -not $lock['released']))) {
                return 'FAIL'
            }
            # An explicit lock error is never PASS, even when every flag reads true.
            if ($lock['attempted'] -and $null -eq $lock['error']) { return 'PASS' }
            return 'UNKNOWN'
        }
        # Lead's v6 decision: the two lane-loop sub-cases are structural fixture coverage only, never an A7 verdict.
        { $_ -cmatch '^A7/(integrity_mismatch|user_sid_mismatch|preloaded_type|off_switch_before_start|' +
            'off_switch_denied_read|disk_floor_before_start|reparse_ancestry)$' } {
            return Get-WdShadowRefusalVerdict $Observation
        }
    }
    return 'UNKNOWN'
}

function Get-WdShadowA7Coverage {
    # Lead's v6 decision: the exact A7 native acceptance scope. The seven pre-start guard sub-cases can be reached by
    # an actual run (native_prestart); the zero- and two-function lane loops cannot reach the count guard under the
    # immutable production manifest (the loop bytes are checked against its pin first), so they are structural
    # fixture coverage of the adapter's count guard only, never native PASS.
    return [ordered]@{
        integrity_mismatch = 'native_prestart'
        user_sid_mismatch = 'native_prestart'
        preloaded_type = 'native_prestart'
        off_switch_before_start = 'native_prestart'
        off_switch_denied_read = 'native_prestart'
        disk_floor_before_start = 'native_prestart'
        reparse_ancestry = 'native_prestart'
        lane_loop_zero_functions = 'structural_fixture_only'
        lane_loop_two_functions = 'structural_fixture_only'
    }
}

function Get-WdShadowA7CaseVerdict {
    # Lead's v6 decision: A7 is bound to its pre-start probe attempts, never to a managed launch (a refusal has no
    # producer launch id or root PID). The expected binding is complete and valid first: the versioned schema, the
    # harness run, the measured controller id and creation time, the approved Source and pin identity and one
    # distinct expected attempt id per native sub-case.
    # The observed binding must equal it (foreign is FAIL); each native sub-case carries its attempt (its expected
    # attempt id plus the measured probe id and creation time, never shared by two sub-cases: a replay is FAIL) and
    # its refusal observation. A missing, extra or malformed value is UNKNOWN; only seven PASS sub-cases PASS.
    param($Binding, $Observation)
    $native = @('integrity_mismatch', 'user_sid_mismatch', 'preloaded_type', 'off_switch_before_start',
        'off_switch_denied_read', 'disk_floor_before_start', 'reparse_ancestry')
    $pinKeys = @('schema', 'harness_run_id', 'case_id', 'controller_pid', 'controller_creation_utc', 'source_commit',
        'manifest_sha256', 'adapter_sha256')
    if (-not (Test-WdShadowExactKeys $Binding ($pinKeys + @('attempts'))) -or
        -not (Test-WdShadowExactKeys $Observation @('binding', 'subcases'))) { return 'UNKNOWN' }
    $formats = @{
        schema = '^wd\.mind-shadow-a7-prestart-binding\.v1\z'; harness_run_id = '^\S+\z'; case_id = '^A7\z'
        controller_pid = '^[1-9][0-9]{0,9}\z'; controller_creation_utc = '^\S+\z'
        source_commit = '^[0-9a-f]{40}\z'; manifest_sha256 = '^[0-9A-F]{64}\z'; adapter_sha256 = '^[0-9A-F]{64}\z'
    }
    foreach ($key in $pinKeys) {
        if ($Binding[$key] -isnot [string] -or $Binding[$key] -cnotmatch $formats[$key]) { return 'UNKNOWN' }
    }
    $attempts = $Binding['attempts']
    if (-not (Test-WdShadowExactKeys $attempts $native)) { return 'UNKNOWN' }
    $guid = '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\z'
    $expectedIds = @{}
    foreach ($name in $native) {
        $id = $attempts[$name]
        if ($id -isnot [string] -or $id -cnotmatch $guid -or $expectedIds.ContainsKey($id)) { return 'UNKNOWN' }
        $expectedIds[$id] = $name
    }
    $seen = $Observation['binding']
    if (-not (Test-WdShadowExactKeys $seen $pinKeys)) { return 'UNKNOWN' }
    foreach ($key in $pinKeys) {
        if ($seen[$key] -isnot [string] -or $seen[$key] -cne [string]$Binding[$key]) { return 'FAIL' }
    }
    $subcases = $Observation['subcases']
    if (-not (Test-WdShadowExactKeys $subcases $native)) { return 'UNKNOWN' }
    $probes = @{}
    $verdicts = @(foreach ($name in $native) {
        $entry = $subcases[$name]
        $attempt = $null
        if ($entry -is [hashtable]) { $attempt = $entry['attempt'] }
        if (-not (Test-WdShadowExactKeys $entry @('attempt', 'refusal')) -or
            -not (Test-WdShadowExactKeys $attempt @('attempt_id', 'probe_pid', 'probe_creation_utc')) -or
            $attempt['attempt_id'] -isnot [string] -or $attempt['probe_pid'] -isnot [string] -or
            $attempt['probe_pid'] -cnotmatch '^[1-9][0-9]{0,9}\z' -or $attempt['probe_creation_utc'] -isnot [string] -or
            $attempt['probe_creation_utc'] -cnotmatch '^\S+\z') {
            'UNKNOWN'
            continue
        }
        $probe = $attempt['probe_pid'] + ' ' + $attempt['probe_creation_utc']
        if ($attempt['attempt_id'] -cne [string]$attempts[$name] -or $probes.ContainsKey($probe)) {
            'FAIL'
            continue
        }
        $probes[$probe] = $name
        Get-WdShadowSubcaseVerdict -CaseId 'A7' -Name $name -Observation $entry['refusal'] -Binding $null
    })
    if ($verdicts -ccontains 'FAIL') { return 'FAIL' }
    if ($verdicts.Count -ne $native.Count -or @($verdicts | Where-Object { $_ -cne 'PASS' }).Count) { return 'UNKNOWN' }
    return 'PASS'
}

function Get-WdShadowLifetimeSubcaseCaseVerdict {
    # Lead's v8 decision: A1 and A3 bind each preregistered sub-case to its own launch. The expected binding is
    # complete and valid first: the versioned schema, the harness run, the case, the measured controller id and
    # creation time, the approved Source, manifest and adapter identity, the deadline and one distinct lowercase GUID
    # launch per sub-case. The observed pins must equal it (foreign is FAIL); each sub-case carries its launch (its
    # expected launch id, a measured root id and creation time never shared by two sub-cases: a reused root is FAIL)
    # and its observation. A missing, extra or malformed value is UNKNOWN; only all PASS sub-cases PASS.
    param([string] $CaseId, $Binding, $Observation)
    $required = @{
        A1 = @('deadline', 'early_root_exit_orphan', 'pipe_holding_grandchild', 'off_switch_during_run',
            'disk_floor_hit', 'single_confirmed_stop')
        A3 = @('outer_no_limits', 'outer_kill_on_close', 'planned_launch_context')
    }
    if (-not $required.ContainsKey($CaseId)) { return 'UNKNOWN' }
    $names = $required[$CaseId]
    $pinKeys = @('schema', 'harness_run_id', 'case_id', 'controller_pid', 'controller_creation_utc', 'source_commit',
        'manifest_sha256', 'adapter_sha256', 'deadline_seconds')
    if (-not (Test-WdShadowExactKeys $Binding ($pinKeys + @('launches'))) -or
        -not (Test-WdShadowExactKeys $Observation @('binding', 'subcases'))) { return 'UNKNOWN' }
    $formats = @{
        schema = '^wd\.mind-shadow-lifetime-subcase-binding\.v1\z'; harness_run_id = '^\S+\z'
        case_id = '^' + $CaseId + '\z'; controller_pid = '^[1-9][0-9]{0,9}\z'; controller_creation_utc = '^\S+\z'
        source_commit = '^[0-9a-f]{40}\z'; manifest_sha256 = '^[0-9A-F]{64}\z'; adapter_sha256 = '^[0-9A-F]{64}\z'
        deadline_seconds = '^[1-9][0-9]{0,5}\z'
    }
    foreach ($key in $pinKeys) {
        if ($Binding[$key] -isnot [string] -or $Binding[$key] -cnotmatch $formats[$key]) { return 'UNKNOWN' }
    }
    $launches = $Binding['launches']
    if (-not (Test-WdShadowExactKeys $launches $names)) { return 'UNKNOWN' }
    $guid = '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\z'
    $ids = @{}
    foreach ($name in $names) {
        $id = $launches[$name]
        if ($id -isnot [string] -or $id -cnotmatch $guid -or $ids.ContainsKey($id)) { return 'UNKNOWN' }
        $ids[$id] = $name
    }
    $seen = $Observation['binding']
    if (-not (Test-WdShadowExactKeys $seen $pinKeys)) { return 'UNKNOWN' }
    foreach ($key in $pinKeys) {
        if ($seen[$key] -isnot [string] -or $seen[$key] -cne [string]$Binding[$key]) { return 'FAIL' }
    }
    $subcases = $Observation['subcases']
    if (-not (Test-WdShadowExactKeys $subcases $names)) { return 'UNKNOWN' }
    $roots = @{}
    $verdicts = @(foreach ($name in $names) {
        $entry = $subcases[$name]
        $launch = $null
        if ($entry -is [hashtable]) { $launch = $entry['launch'] }
        if (-not (Test-WdShadowExactKeys $entry @('launch', 'observation')) -or
            -not (Test-WdShadowExactKeys $launch @('launch_id', 'root_pid', 'root_creation_utc')) -or
            $launch['launch_id'] -isnot [string] -or $launch['root_pid'] -isnot [string] -or
            $launch['root_pid'] -cnotmatch '^[1-9][0-9]{0,9}\z' -or $launch['root_creation_utc'] -isnot [string] -or
            $launch['root_creation_utc'] -cnotmatch '^\S+\z') {
            'UNKNOWN'
            continue
        }
        $root = $launch['root_pid'] + ' ' + $launch['root_creation_utc']
        if ($launch['launch_id'] -cne [string]$launches[$name] -or $roots.ContainsKey($root)) {
            'FAIL'
            continue
        }
        $roots[$root] = $name
        Get-WdShadowSubcaseVerdict -CaseId $CaseId -Name $name -Observation $entry['observation'] `
            -Binding @{ deadline_seconds = $Binding['deadline_seconds']; launch_id = $launch['launch_id'] }
    })
    if ($verdicts -ccontains 'FAIL') { return 'FAIL' }
    if ($verdicts.Count -ne $names.Count -or @($verdicts | Where-Object { $_ -cne 'PASS' }).Count) { return 'UNKNOWN' }
    return 'PASS'
}

function Get-WdShadowCaseVerdict {
    # One case from one strict observation: the run binding first (partial is UNKNOWN, foreign is FAIL), then every
    # required sub-case once. A4 is always NOT_READY: A4 is the job kill at the publication replacement and no
    # evidence of it exists here; run_intent or run_start is never that witness. A7 has its own pre-start attempt
    # binding (Get-WdShadowA7CaseVerdict); the managed launch binding below is never loosened for it.
    param([string] $CaseId, $Binding, $Observation)
    if ($CaseId -ceq 'A4') { return 'NOT_READY' }
    if ($CaseId -ceq 'A7') { return Get-WdShadowA7CaseVerdict -Binding $Binding -Observation $Observation }
    if ($CaseId -ceq 'A1' -or $CaseId -ceq 'A3') {
        return Get-WdShadowLifetimeSubcaseCaseVerdict -CaseId $CaseId -Binding $Binding -Observation $Observation
    }
    $required = @{
        A1 = @('deadline', 'early_root_exit_orphan', 'pipe_holding_grandchild', 'off_switch_during_run',
            'disk_floor_hit', 'single_confirmed_stop')
        A2 = @('survivors')
        A3 = @('outer_no_limits', 'outer_kill_on_close', 'planned_launch_context')
        A5 = @('controller_death')
        A6 = @('inherited_handles')
    }
    if (@('A1', 'A2', 'A3', 'A5', 'A6') -cnotcontains $CaseId) { return 'UNKNOWN' }
    $bindingKeys = @('harness_run_id', 'case_id', 'launch_id', 'root_pid', 'root_creation_utc', 'deadline_seconds')
    if (-not (Test-WdShadowExactKeys $Binding $bindingKeys) -or [string]$Binding['case_id'] -cne $CaseId -or
        -not (Test-WdShadowExactKeys $Observation @('binding', 'subcases'))) { return 'UNKNOWN' }
    # A complete valid expected binding before any comparison (the adapter's launch id is a [guid] 'D' string);
    # a blank, malformed or unknown expected value refuses.
    $formats = @{
        harness_run_id = '^\S+\z'; case_id = '^A[1-7]\z'; root_pid = '^[1-9][0-9]{0,9}\z'
        launch_id = '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\z'
        root_creation_utc = '^\S+\z'; deadline_seconds = '^[1-9][0-9]{0,5}\z'
    }
    foreach ($key in $bindingKeys) {
        if ($Binding[$key] -isnot [string] -or $Binding[$key] -cnotmatch $formats[$key]) { return 'UNKNOWN' }
    }
    $seen = $Observation['binding']
    if (-not (Test-WdShadowExactKeys $seen $bindingKeys)) { return 'UNKNOWN' }
    foreach ($key in $bindingKeys) {
        if ($seen[$key] -isnot [string] -or $seen[$key] -cne [string]$Binding[$key]) { return 'FAIL' }
    }
    $subcases = $Observation['subcases']
    if (-not (Test-WdShadowExactKeys $subcases $required[$CaseId])) { return 'UNKNOWN' }
    $verdicts = @(foreach ($name in $required[$CaseId]) {
        Get-WdShadowSubcaseVerdict -CaseId $CaseId -Name $name -Observation $subcases[$name] -Binding $Binding
    })
    if ($verdicts -ccontains 'FAIL') { return 'FAIL' }
    if ($verdicts.Count -ne @($required[$CaseId]).Count -or @($verdicts | Where-Object { $_ -cne 'PASS' }).Count) {
        return 'UNKNOWN'
    }
    return 'PASS'
}

function Get-WdShadowRunVerdict {
    # The whole run: exactly A1-A7, each one of the four verdicts. Any FAIL fails it; while no A4 publication-kill
    # evidence exists, no run can PASS, whatever A4 verdict is supplied.
    param($Verdicts)
    $a4TimingPointSupported = $false
    $caseIds = @('A1', 'A2', 'A3', 'A4', 'A5', 'A6', 'A7')
    if (-not (Test-WdShadowExactKeys $Verdicts $caseIds)) { return 'UNKNOWN' }
    $values = @(foreach ($caseId in $caseIds) { $Verdicts[$caseId] })
    foreach ($value in $values) {
        if ($value -isnot [string] -or @('PASS', 'FAIL', 'UNKNOWN', 'NOT_READY') -cnotcontains $value) {
            return 'UNKNOWN'
        }
    }
    if ($values -ccontains 'FAIL') { return 'FAIL' }
    if (-not $a4TimingPointSupported -or $values -ccontains 'NOT_READY') { return 'NOT_READY' }
    if ($values -ccontains 'UNKNOWN') { return 'UNKNOWN' }
    return 'PASS'
}

# Checkpoint 3 (Lead A7 driver GO, 2026-10-22): the A7 pre-start refusal drivers. RCO1's A1-A7 wording names an
# integrity mismatch refused before the start (integrity and user variants) and selects no particular adapter refusal
# (UNSPECIFIED, RCO1 2026-10-22T19:01:24Z), so the refusal of each sub-case is this file's explicit choice: T1
# integrity (adapter L423), T1 user SID (L426, Lead's v5 delta), R1 type (L124), the off switch (L561), the floor
# (L569) and run-root reparse ancestry (L543). The approved inputs are the executor's, which pass every earlier adapter
# check (token user and integrity, adapter hash, state pointer, manifest, pins, wrapper, lane loop); a plan changes at
# most one of them. A wrong approved integrity level is the deviation for an actual Medium actor, never an elevation.
# Lead's v6 decision: a driver needs an owned-job witness to observe no start; the real child seam is disabled, so a
# driver can show a FAIL (a seen start, a run-root entry, a zero exit) but never a native PASS.

function Get-WdShadowA7Plan {
    # One A7 sub-case as a plan: the approved inputs with the run root replaced and at most one deviation, the case
    # root preparation, the R1 preload and the one refusal line the child must print. The two lane-loop sub-cases have
    # no plan (structural fixture coverage only, Get-WdShadowA7Coverage): the adapter reads the fixed reboot state
    # pointer and bundle store and checks the loop bytes against the manifest pin before it counts the functions, so
    # a zero- or two-function loop cannot reach that count in an actual run. Invalid input is refused, never planned.
    param([string] $Name, [hashtable] $Inputs, [string] $CaseRoot)
    $reachable = @('integrity_mismatch', 'user_sid_mismatch', 'preloaded_type', 'off_switch_before_start',
        'off_switch_denied_read', 'disk_floor_before_start', 'reparse_ancestry')
    $unreachable = @('lane_loop_zero_functions', 'lane_loop_two_functions')
    if ($reachable -cnotcontains $Name -and $unreachable -cnotcontains $Name) { throw 'refused: unknown A7 sub-case' }
    $keys = @('ExpectedSourceCommit', 'ExpectedManifestSha256', 'ExpectedAdapterSha256', 'ApprovedRunRoot', 'LiveDir',
        'ApprovedUserSid', 'ApprovedIntegrityLevel', 'ExpectedPeakAdditionalBytes')
    if (-not (Test-WdShadowExactKeys $Inputs $keys)) {
        throw 'refused: the approved inputs are not exactly the adapter parameters'
    }
    foreach ($key in $keys[0..6]) {
        if ($Inputs[$key] -isnot [string] -or $Inputs[$key] -cnotmatch '^[\x20-\x7E]+\z') {
            throw "refused: $key is not printable ASCII text"
        }
    }
    $level = [string]$Inputs['ApprovedIntegrityLevel']
    if (@('medium', 'high') -cnotcontains $level) { throw 'refused: ApprovedIntegrityLevel is not medium or high' }
    $bound = $Inputs['ExpectedPeakAdditionalBytes']
    if (-not (Test-WdShadowCount $bound) -or $bound -le 0) {
        throw 'refused: ExpectedPeakAdditionalBytes is not a positive integer'
    }
    if ($CaseRoot -cnotmatch '^[\x20-\x7E]+\z' -or -not [IO.Path]::IsPathRooted($CaseRoot)) {
        throw 'refused: the case root is not a rooted printable ASCII path'
    }
    if ($unreachable -ccontains $Name) { return $null }
    $root = [IO.Path]::GetFullPath($CaseRoot).TrimEnd('\')
    $runRoot = $root
    $prepare = 'none'
    $preload = 'none'
    $planned = @{}
    foreach ($key in $keys) { $planned[$key] = $Inputs[$key] }
    $offSwitch = 'refused: the operator off switch ' + $root +
        '\mind-shadow.off is on (or unreadable); nothing was started'
    switch -CaseSensitive ($Name) {
        'integrity_mismatch' {
            $other = @{ medium = 'high'; high = 'medium' }[$level]
            $planned['ApprovedIntegrityLevel'] = $other
            $pattern = [regex]::Escape("refused: the actual integrity level $level is not the approved $other")
        }
        'user_sid_mismatch' {
            # A foreign SID of the adapter's own form (its value check runs first): the approved SID with its last
            # sub-authority replaced by 0 (by 1 when it is 0).
            $sid = [string]$Inputs['ApprovedUserSid']
            if ($sid -cnotmatch '^S-1-5-21-[0-9]+-[0-9]+-[0-9]+-[0-9]+\z') {
                throw 'refused: ApprovedUserSid is not a domain user SID, so no foreign SID of its form is derived'
            }
            $prefix = $sid.Substring(0, $sid.LastIndexOf('-') + 1)
            $rid = '0'
            if ($sid.Substring($prefix.Length) -ceq '0') { $rid = '1' }
            $planned['ApprovedUserSid'] = $prefix + $rid
            $pattern = [regex]::Escape('refused: the token user SID is not the approved user SID')
        }
        'preloaded_type' {
            $preload = 'lane_loop_type'
            $pattern = [regex]::Escape('refused: WdManagedTurnProcess is already loaded in this process (R1); ' +
                'start the adapter in a fresh process')
        }
        'off_switch_before_start' { $prepare = 'off_switch'; $pattern = [regex]::Escape($offSwitch) }
        'off_switch_denied_read' { $prepare = 'off_switch_denied'; $pattern = [regex]::Escape($offSwitch) }
        'disk_floor_before_start' {
            $planned['ExpectedPeakAdditionalBytes'] = [long]::MaxValue
            $pattern = [regex]::Escape('refused: the disk budget is not met before the start (C:\ free=') + '[0-9]+' +
                [regex]::Escape(', additional=' + [long]::MaxValue + ', floor=') + '[0-9]+' + [regex]::Escape(')')
        }
        'reparse_ancestry' {
            $runRoot = $root + '\link\run'
            $prepare = 'junction'
            $pattern = [regex]::Escape('refused: ' + $root + '\link is a reparse point, so its textual root would ' +
                'not measure its backing volume')
        }
    }
    $planned['ApprovedRunRoot'] = $runRoot
    return [ordered]@{
        name = $Name; inputs = $planned; run_root = $runRoot; refusal = '^WD-A7-REFUSAL ' + $pattern + '\z'
        prepare = $prepare; preload = $preload
    }
}

function ConvertTo-WdShadowA7Literal {
    # A single-quoted literal of printable ASCII text; anything else is refused, never escaped.
    param([string] $Value)
    if ($Value -cnotmatch '^[\x20-\x7E]*\z') { throw 'refused: a child command value is not printable ASCII text' }
    return "'" + $Value.Replace("'", "''") + "'"
}

function Get-WdShadowA7ChildCommand {
    # The child's whole command: for preloaded_type the R1 preload (the loop bytes read ONCE and refused, exit 4,
    # unless their raw SHA-256 equals the explicit pin, then those same bytes strictly decoded and parsed; their one
    # Initialize-WdNativeTurnType, defined and called; no single function is a setup error, exit 4), then exactly one
    # call of the adapter with the planned inputs as literals. An adapter refusal becomes one error line
    # "WD-A7-REFUSAL <message>" and exit 2; otherwise the adapter's own exit code passes through (0 when it set none).
    param($Plan, [string] $AdapterPath, [string] $ExpectedLoopSha256)
    $inputs = $Plan['inputs']
    $text = '$ErrorActionPreference = ''Stop''; '
    if ($Plan['preload'] -ceq 'lane_loop_type') {
        if ($ExpectedLoopSha256 -cnotmatch '^[0-9A-F]{64}\z') {
            throw 'refused: the preload needs the exact uppercase SHA-256 pin of the loop bytes (no default)'
        }
        $loop = ConvertTo-WdShadowA7Literal ('C:\Python\wd-reboot-bundles\' + $inputs['ExpectedSourceCommit'] +
            '\Invoke-WdLaneTurnLoop.ps1')
        $text += '$wdA7Bytes = [IO.File]::ReadAllBytes(' + $loop + '); ' +
            '$wdA7Sha = [Security.Cryptography.SHA256]::Create(); ' +
            '$wdA7Hash = -join @($wdA7Sha.ComputeHash($wdA7Bytes) | ForEach-Object { $_.ToString(''X2'') }); ' +
            '$wdA7Sha.Dispose(); if ($wdA7Hash -cne ' + (ConvertTo-WdShadowA7Literal $ExpectedLoopSha256) + ') { ' +
            '[Console]::Error.WriteLine(''WD-A7-SETUP the lane loop is not its pinned bytes''); exit 4 }; ' +
            '$wdA7Skip = 0; if ($wdA7Bytes.Length -ge 3 -and $wdA7Bytes[0] -eq 239 -and $wdA7Bytes[1] -eq 187 ' +
            '-and $wdA7Bytes[2] -eq 191) { $wdA7Skip = 3 }; ' +
            '$wdA7Text = (New-Object Text.UTF8Encoding($false, $true)).GetString($wdA7Bytes, $wdA7Skip, ' +
            '$wdA7Bytes.Length - $wdA7Skip); $wdA7Tokens = $null; $wdA7Errors = $null; ' +
            '$wdA7Ast = [Management.Automation.Language.Parser]::ParseInput($wdA7Text' +
            ', [ref]$wdA7Tokens, [ref]$wdA7Errors); ' +
            '$wdA7Found = @($wdA7Ast.EndBlock.Statements | Where-Object { ' +
            '$_ -is [Management.Automation.Language.FunctionDefinitionAst] -and ' +
            '$_.Name -ceq ''Initialize-WdNativeTurnType'' }); ' +
            'if ($wdA7Errors.Count -or $wdA7Found.Count -ne 1) { ' +
            '[Console]::Error.WriteLine(''WD-A7-SETUP the lane loop has no single Initialize-WdNativeTurnType''); ' +
            'exit 4 }; . ([scriptblock]::Create($wdA7Found[0].Extent.Text)); Initialize-WdNativeTurnType; '
    }
    $call = '& ' + (ConvertTo-WdShadowA7Literal $AdapterPath)
    foreach ($key in @('ExpectedSourceCommit', 'ExpectedManifestSha256', 'ExpectedAdapterSha256', 'ApprovedRunRoot',
            'LiveDir', 'ApprovedUserSid', 'ApprovedIntegrityLevel')) {
        $call += ' -' + $key + ' ' + (ConvertTo-WdShadowA7Literal ([string]$inputs[$key]))
    }
    $call += ' -ExpectedPeakAdditionalBytes ' + ([long]$inputs['ExpectedPeakAdditionalBytes']).ToString(
        [Globalization.CultureInfo]::InvariantCulture)
    $text += 'try { ' + $call + '; $wdA7Code = $LASTEXITCODE; if ($wdA7Code -isnot [int]) { $wdA7Code = 0 }; ' +
        'exit $wdA7Code } catch { ' +
        '[Console]::Error.WriteLine(''WD-A7-REFUSAL '' + ($_.Exception.Message -replace ''[\r\n]+'', '' '')); exit 2 }'
    return $text
}

function Get-WdShadowA7Entries {
    # The entry names directly in a directory, sorted ordinally; a missing directory is an empty set and an unreadable
    # one throws (never evidence).
    param([string] $Path)
    if (-not [IO.Directory]::Exists($Path)) { return , ([string[]]@()) }
    $names = [string[]]@([IO.Directory]::GetFileSystemEntries($Path))
    [Array]::Sort($names, [StringComparer]::Ordinal)
    return , $names
}

function Invoke-WdShadowA7Driver {
    # One A7 sub-case through injected seams: Prepare builds the case root of the plan, RunChild runs the child command
    # once in a fresh host process and returns its id, exit code and error text, ListChildren lists the live processes
    # whose parent is that id. The observation comes only from those results and from two listings of the run root,
    # never from a supplied value: exit_code is the child's ($null when it did not end), refusal_seen needs exactly one
    # error line that is the planned refusal and run_dir_created is any new run-root entry. process_started is $true
    # for a listed live child (a start was seen) and otherwise $null: an empty listing misses a process that started
    # and ended and is never no-start evidence (Lead's v6 decision), so without an owned-job witness the sub-case stays
    # UNKNOWN. $null when the sub-case has no plan. The preload needs the explicit loop pin (Lead's v9 decision).
    param([string] $Name, [hashtable] $Inputs, [string] $CaseRoot, [string] $AdapterPath, [string] $HostPath,
        [scriptblock] $Prepare, [scriptblock] $RunChild, [scriptblock] $ListChildren, [string] $ExpectedLoopSha256)
    if ($null -eq $Prepare -or $null -eq $RunChild -or $null -eq $ListChildren) {
        throw 'refused: a driver seam is missing'
    }
    $plan = Get-WdShadowA7Plan -Name $Name -Inputs $Inputs -CaseRoot $CaseRoot
    if ($null -eq $plan) { return $null }
    $command = Get-WdShadowA7ChildCommand -Plan $plan -AdapterPath $AdapterPath -ExpectedLoopSha256 $ExpectedLoopSha256
    [void](& $Prepare $plan)
    if (-not [IO.Directory]::Exists([string]$plan['run_root'])) { throw 'refused: the preparation made no run root' }
    $before = Get-WdShadowA7Entries -Path $plan['run_root']
    $child = & $RunChild $HostPath $command
    if (-not (Test-WdShadowExactKeys $child @('pid', 'exit_code', 'error_text')) -or
        -not (Test-WdShadowCount $child['pid']) -or $child['pid'] -le 0 -or $child['error_text'] -isnot [string] -or
        ($null -ne $child['exit_code'] -and -not (Test-WdShadowCount $child['exit_code']))) {
        throw 'refused: the child result is not an id, an exit code and an error text'
    }
    $after = Get-WdShadowA7Entries -Path $plan['run_root']
    $listed = & $ListChildren ([int]$child['pid'])
    $children = @()
    if ($null -ne $listed) { $children = @($listed) }
    foreach ($id in $children) {
        if (-not (Test-WdShadowCount $id) -or $id -le 0) { throw 'refused: a listed child is not a process id' }
    }
    $refusal = [string]$plan['refusal']
    $seen = @(($child['error_text'] -split '\r?\n') | Where-Object { $_ -cmatch $refusal })
    $new = @($after | Where-Object { $before -cnotcontains $_ })
    $started = $null
    if ($children.Count -gt 0) { $started = $true }
    return @{
        exit_code = $child['exit_code']; refusal_seen = ($seen.Count -eq 1); run_dir_created = ($new.Count -gt 0)
        process_started = $started
    }
}

function Start-WdShadowA7Child {
    # DISABLED (Lead's v6 decision, 2026-10-22T19:41:59Z): the uncontained child of checkpoint 3 could be left running
    # at its bound, read its streams without a bound, and proved nothing about a workload start. Until an owned-job
    # controller (the pinned six-argument primitive) with a bounded drain and stop and an exact witness exists, this
    # seam refuses before any process exists. Never a PID kill.
    param([string] $HostPath, [string] $Command)
    throw 'refused: the real A7 child seam is disabled until an owned-job controller with an exact witness exists'
}

function Get-WdShadowA7ChildProcesses {
    # DISABLED (Lead's v6 decision): a live-child listing after the child ended misses a process that started and
    # ended, so it can never show that no workload started; it refuses before any process query.
    param([int] $ParentId)
    throw 'refused: the real A7 child lister is disabled: an empty listing is never no-start evidence'
}

function New-WdShadowA7CaseRoot {
    # The real preparation (an approved executor's opt-in run only): a fresh case root with its run root, plus the
    # sub-case object: the off switch file; the off switch file with a deny-read-attributes entry for the current user,
    # refused unless this process then cannot read the switch attributes (the parent directory listing may still
    # grant them, so a denied read is not assumed); or a junction "link" to an ordinary "target" that holds the run
    # directory. Nothing is removed afterwards.
    param($Plan)
    $runRoot = [string]$Plan['run_root']
    $prepare = [string]$Plan['prepare']
    if (@('none', 'off_switch', 'off_switch_denied', 'junction') -cnotcontains $prepare) {
        throw 'refused: unknown A7 preparation'
    }
    $caseRoot = $runRoot
    if ($prepare -ceq 'junction') { $caseRoot = [IO.Path]::GetDirectoryName([IO.Path]::GetDirectoryName($runRoot)) }
    if ([IO.Directory]::Exists($caseRoot) -or [IO.File]::Exists($caseRoot)) {
        throw 'refused: the case root is not fresh'
    }
    [void][IO.Directory]::CreateDirectory($caseRoot)
    if ($prepare -ceq 'junction') {
        $target = Join-Path $caseRoot 'target'
        [void][IO.Directory]::CreateDirectory((Join-Path $target 'run'))
        [void](New-Item -ItemType Junction -Path (Join-Path $caseRoot 'link') -Value $target)
        return
    }
    if ($prepare -ceq 'none') { return }
    $switchPath = Join-Path $runRoot 'mind-shadow.off'
    $stream = New-Object IO.FileStream($switchPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write,
        [IO.FileShare]::None)
    $stream.Dispose()
    if ($prepare -ceq 'off_switch') { return }
    $acl = Get-Acl -LiteralPath $switchPath
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($user,
        [Security.AccessControl.FileSystemRights]::ReadAttributes, [Security.AccessControl.AccessControlType]::Deny)))
    Set-Acl -LiteralPath $switchPath -AclObject $acl
    $denied = $false
    try { [void][IO.File]::GetAttributes($switchPath) } catch [UnauthorizedAccessException] { $denied = $true }
    if (-not $denied) {
        throw 'refused: the switch attributes are still readable here, so a denied read is not observed'
    }
}

# Checkpoint 4 (Lead's v7 GO, 2026-10-22T20:06:21Z): the A1, A2, A3, A5 and A6 lifetime drivers. One driver body runs
# one sub-case: it preregisters the sub-case's synthetic members in a fresh run-bound witness directory, starts the
# root member in an owned job through the job seam, polls the root and the job counts against the bound deadline and
# the guards (the adapter's loop rules: one guard reading per slice, a drain counts only strictly before the deadline),
# stops once when the job is not drained, confirms the drain within the stop wait, reads the member witnesses and, for
# A6, reacquires the lock itself. The observation comes from the job's counts, exit and Stop, the witness files and
# the lock attempt, never from a supplied value. The workload is the synthetic member fixture
# (wd_mind_shadow_acceptance_member.ps1), never the producer: the adapter's own loop starts only the producer, so these
# drivers exercise the owned-job primitive under the adapter's stop rules, not the adapter loop itself. The real job,
# root identity, context, controller-kill and handle seams are disabled until the pinned owned-job API is reconciled
# once, so the drivers run only on strict fakes here.

function Get-WdShadowLifetimeMembers {
    # The preregistered synthetic members of each lifetime sub-case, root first; the member fixture holds the same
    # table and refuses any other name, sub-case or tree.
    return [ordered]@{
        'A1/deadline' = @('root')
        'A1/early_root_exit_orphan' = @('root', 'orphan')
        'A1/pipe_holding_grandchild' = @('root', 'child', 'pipe_grandchild')
        'A1/off_switch_during_run' = @('root')
        'A1/disk_floor_hit' = @('root')
        'A1/single_confirmed_stop' = @('root')
        'A2/survivors' = @('root', 'child', 'grandchild', 'late_child')
        'A3/outer_no_limits' = @('root')
        'A3/outer_kill_on_close' = @('root')
        'A3/planned_launch_context' = @('root')
        'A5/controller_death' = @('root', 'child', 'grandchild')
        'A6/inherited_handles' = @('root')
    }
}

function Get-WdShadowLifetimePlan {
    # One lifetime sub-case as a plan: its preregistered members, the fresh run-bound witness directory under a
    # .codex-audit folder (named by the launch id), the deadline, the guard trigger and the root member's command.
    # Invalid input is refused, never planned.
    param([string] $CaseId, [string] $Name, [hashtable] $Run)
    $registry = Get-WdShadowLifetimeMembers
    $key = $CaseId + '/' + $Name
    if (-not $registry.Contains($key)) { throw 'refused: unknown lifetime sub-case' }
    $keys = @('harness_run_id', 'launch_id', 'deadline_seconds', 'witness_root', 'host_path', 'member_path')
    if (-not (Test-WdShadowExactKeys $Run $keys)) { throw 'refused: the run inputs are not exactly the lifetime keys' }
    foreach ($field in $keys) {
        if ($Run[$field] -isnot [string] -or $Run[$field] -cnotmatch '^[\x21-\x7E][\x20-\x7E]*\z' -or
            $Run[$field].Contains('"')) { throw "refused: $field is not printable ASCII text" }
    }
    if ($Run['launch_id'] -cnotmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\z') {
        throw 'refused: the launch id is not a lowercase GUID'
    }
    if ($Run['deadline_seconds'] -cnotmatch '^[1-9][0-9]{0,5}\z') { throw 'refused: the deadline is not positive' }
    foreach ($field in @('witness_root', 'host_path', 'member_path')) {
        if (-not [IO.Path]::IsPathRooted($Run[$field])) { throw "refused: $field is not a rooted path" }
    }
    $root = [IO.Path]::GetFullPath($Run['witness_root']).TrimEnd('\')
    if ([IO.Path]::GetFileName([IO.Path]::GetDirectoryName($root)) -cne '.codex-audit') {
        throw 'refused: the witness root is not a run folder directly in a .codex-audit folder'
    }
    $directory = $root + '\' + $Run['launch_id']
    $deadline = [long]$Run['deadline_seconds']
    $guard = 'none'
    if ($key -ceq 'A1/off_switch_during_run') { $guard = 'off_switch' }
    if ($key -ceq 'A1/disk_floor_hit') { $guard = 'disk_floor' }
    $arguments = [string[]]@('-NoProfile', '-NonInteractive', '-File', $Run['member_path'], '-OptInSyntheticMember',
        '-LaunchId', $Run['launch_id'], '-Member', 'root', '-Subcase', $key, '-WitnessDirectory', $directory,
        '-LifetimeSeconds', ([string]($deadline + 60)))
    return @{
        case_id = $CaseId; name = $Name; members = [string[]]@($registry[$key]); witness_directory = $directory
        deadline_seconds = $deadline; guard = $guard; trigger_seconds = [long][Math]::Floor($deadline / 2)
        off_switch = $directory + '\mind-shadow.off'; lock_path = $directory + '\producer.lock'
        disk_floor_bytes = [long]3221225472; host_path = $Run['host_path']; arguments = $arguments
        binding = @{
            harness_run_id = $Run['harness_run_id']; case_id = $CaseId; launch_id = $Run['launch_id']
            root_pid = 'UNKNOWN'; root_creation_utc = 'UNKNOWN'; deadline_seconds = $Run['deadline_seconds']
        }
    }
}

function Get-WdShadowLifetimeSeams {
    # The real seams of an approved executor's run. The clock, the sleep and the free-space reading are plain reads;
    # the job (the pinned six-argument owned-job primitive with its read-only TotalProcesses), the root identity, the
    # outer context, the controller kill and the inherited-handle listing are DISABLED until that API is reconciled
    # once with its Source pin (Lead's v7): they refuse before any process, job or handle exists. Never a PID kill.
    $clock = [Diagnostics.Stopwatch]::StartNew()
    return @{
        NewManaged = {
            param($Plan)
            throw 'refused: the real lifetime job seam is disabled until the pinned owned-job API is reconciled'
        }
        RootCreation = { param($Managed) throw 'refused: the real root identity seam is disabled with the job seam' }
        Clock = { return $clock.Elapsed.TotalSeconds }.GetNewClosure()
        Sleep = { param([int] $Milliseconds) [Threading.Thread]::Sleep($Milliseconds) }
        FreeBytes = {
            param([string] $Path)
            return [long](New-Object IO.DriveInfo([IO.Path]::GetPathRoot($Path))).AvailableFreeSpace
        }
        Context = { param($Plan) throw 'refused: the real outer job context seam is disabled with the job seam' }
        KillController = {
            param($Managed)
            throw 'refused: the real controller kill seam is disabled with the job seam'
        }
        InheritedHandles = {
            param($Managed)
            throw 'refused: the real inherited-handle seam is disabled with the job seam'
        }
    }
}

function Test-WdShadowLifetimeOffSwitch {
    # The adapter's rule: existence only, and any doubt counts as on.
    param([string] $Path)
    try { [void][IO.File]::GetAttributes($Path); return $true }
    catch [IO.FileNotFoundException] { return $false }
    catch [IO.DirectoryNotFoundException] { return $false }
    catch { return $true }
}

function Get-WdShadowLifetimeHeld {
    # The members whose witness file is still held (a live member: its start line cannot be read while it is held,
    # and only preregistered members write into the fresh run directory), and whether every member left a start
    # witness with a known state, a released one bound to this launch.
    param([hashtable] $Observed, [string[]] $Members, [string] $LaunchId)
    $held = @()
    $complete = $true
    foreach ($member in $Members) {
        $seen = $Observed[$member]
        if ($null -eq $seen -or [string]$seen.start_witness -cne 'present' -or $seen.released -isnot [bool] -or
            ($seen.released -and [string]$seen.launch_id -cne $LaunchId)) { $complete = $false; continue }
        if (-not $seen.released) { $held += $member }
    }
    return @{ held = $held; complete = $complete }
}

function Invoke-WdShadowLifetimeDriver {
    # One lifetime sub-case through injected seams (exactly the keys of Get-WdShadowLifetimeSeams): launch the root
    # member in the owned job, wait (deadline, guards, root exit, drain), stop once when not drained, confirm the drain
    # within the 30 s stop wait, observe. A5 kills the controller instead of stopping. Returns the sub-case, its launch
    # binding (measured root id and creation time, UNKNOWN when nothing started) and its observation ($null when the
    # evidence is incomplete, which is never PASS). WitnessObserve (optional, private to the owned wrapper) is called
    # immediately before and after every direct witness read; it only observes, it never replaces a seam.
    param([string] $CaseId, [string] $Name, [hashtable] $Run, [hashtable] $Seams, [scriptblock] $WitnessObserve = $null)
    $seamKeys = @('NewManaged', 'RootCreation', 'Clock', 'Sleep', 'FreeBytes', 'Context', 'KillController',
        'InheritedHandles')
    if (-not (Test-WdShadowExactKeys $Seams $seamKeys)) { throw 'refused: the seams are not exactly the driver seams' }
    foreach ($seam in $seamKeys) {
        if ($Seams[$seam] -isnot [scriptblock]) { throw "refused: the $seam seam is not a script block" }
    }
    $plan = Get-WdShadowLifetimePlan -CaseId $CaseId -Name $Name -Run $Run
    $directory = [string]$plan['witness_directory']
    if ([IO.Directory]::Exists($directory) -or [IO.File]::Exists($directory)) {
        throw 'refused: the witness directory is not fresh'
    }
    [void][IO.Directory]::CreateDirectory($directory)
    $lines = (@($plan['members'] | ForEach-Object { $Run['launch_id'] + ' ' + $_ }) -join "`n") + "`n"
    $bytes = [Text.Encoding]::ASCII.GetBytes($lines)
    $stream = New-Object IO.FileStream(($directory + '\members.registration'), [IO.FileMode]::CreateNew,
        [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
    if ($CaseId -ceq 'A3') {
        $context = & $Seams['Context'] $plan
        if ($context -isnot [string] -or $context -cne $Name) {
            throw 'refused: the planned launch context was not entered'
        }
    }
    $read = {
        param([object[]] $Want)
        if ($null -ne $WitnessObserve) { & $WitnessObserve }
        $seen = Get-WdShadowMemberObservation -WitnessDirectory $directory -Expected $Want
        if ($null -ne $WitnessObserve) { & $WitnessObserve }
        return $seen
    }
    $binding = $plan['binding']
    $deadline = [double]$plan['deadline_seconds']
    $members = [string[]]$plan['members']
    $launchId = [string]$Run['launch_id']
    $observation = $null
    $managed = $null
    $createError = $null
    $driverFailure = $null
    $driverStopAttempted = $false
    try {
        try { $managed = & $Seams['NewManaged'] $plan } catch { $createError = $_.Exception.Message }
        if ($null -eq $managed) {
            if ($CaseId -cne 'A3') { throw ('refused: the owned job did not start the root member: ' + $createError) }
            # A refused start without an owned-job witness of no start is never refused_before_start.
            return @{ case_id = $CaseId; name = $Name; binding = $binding
                observation = @{ result = 'refused_unwitnessed' } }
        }
        $binding['root_pid'] = [string][long]$managed.Pid
        $creation = & $Seams['RootCreation'] $managed
        if ($creation -isnot [string] -or $creation -cnotmatch '^\S+\z') { throw 'refused: no root creation time' }
        $binding['root_creation_utc'] = $creation
        $start = [double](& $Seams['Clock'])
        $elapsed = { [double](& $Seams['Clock']) - $start }
        if ($CaseId -ceq 'A3' -or $CaseId -ceq 'A5' -or $CaseId -ceq 'A6') {
            # Wait (bounded by the deadline) until the members have started: A3 and A6 need the root alive, A5 the
            # whole tree before the controller dies.
            $want = @('root')
            if ($CaseId -ceq 'A5') { $want = $members }
            while ((& $elapsed) -lt $deadline) {
                $now = & $read $want
                if (@($want | Where-Object { [string]$now[$_].start_witness -cne 'present' }).Count -eq 0) { break }
                & $Seams['Sleep'] 250
            }
        }
        $result = $null
        if ($CaseId -ceq 'A3') {
            $root = (& $read @('root'))['root']
            $total = [long]$managed.TotalProcesses
            $result = 'root_unwitnessed'
            if ([string]$root.start_witness -ceq 'present' -and $root.released -eq $false) {
                # The pinned primitive creates the root suspended, assigns it and only then resumes it (from code, not
                # observed): a live root witness with the root counted in the job is that order's outcome. Its start
                # line is checked after the stop, once the witness is released.
                if ($total -ge 1) { $result = 'assigned_before_resume' } else { $result = 'started_unassigned' }
            }
        }
        $handles = $null
        if ($CaseId -ceq 'A6') {
            $handles = @(& $Seams['InheritedHandles'] $managed)
            foreach ($handle in $handles) {
                if ($handle -isnot [string]) { throw 'refused: an inherited handle is not a text description' }
            }
        }
        if ($CaseId -ceq 'A5') {
            & $Seams['KillController'] $managed
            $until = (& $elapsed) + 30
            while ((& $elapsed) -lt $until) {
                $now = & $read $members
                if (@($members | Where-Object { $now[$_].released -eq $false }).Count -eq 0) { break }
                & $Seams['Sleep'] 250
            }
            $observed = & $read $members
            return @{ case_id = $CaseId; name = $Name; binding = $binding
                observation = @{ expected = $members; observed = $observed } }
        }
        # The adapter's loop: one guard reading per slice, then one wait; a drain counts only strictly before the
        # deadline. A3 and A6 stop as soon as they have observed what they need.
        $drained = $false
        $guardStop = $null
        $rootExitAt = $null
        $orphanAlive = $null
        $floor = [long]$plan['disk_floor_bytes']
        $triggered = $false
        $loop = $CaseId -ceq 'A1' -or $CaseId -ceq 'A2'
        while ($loop -and (& $elapsed) -lt $deadline) {
            if ($plan['guard'] -cne 'none' -and -not $triggered -and (& $elapsed) -ge $plan['trigger_seconds']) {
                # The during-run guard: the driver itself turns the off switch on or raises the floor above the
                # measured free space (a synthetic floor hit, never a filled disk).
                $triggered = $true
                if ($plan['guard'] -ceq 'off_switch') {
                    $switch = New-Object IO.FileStream([string]$plan['off_switch'], [IO.FileMode]::CreateNew,
                        [IO.FileAccess]::Write, [IO.FileShare]::None)
                    $switch.Dispose()
                } else {
                    $floor = [long](& $Seams['FreeBytes'] $directory) + 1
                }
            }
            if (Test-WdShadowLifetimeOffSwitch ([string]$plan['off_switch'])) { $guardStop = 'off_switch'; break }
            if ([long](& $Seams['FreeBytes'] $directory) -lt $floor) { $guardStop = 'disk_floor'; break }
            $slice = [int][Math]::Max(1, [Math]::Min(1000, ($deadline - (& $elapsed)) * 1000))
            if ($null -eq $rootExitAt) {
                if ($managed.Wait($slice)) {
                    $rootExitAt = & $elapsed
                    if ($Name -ceq 'early_root_exit_orphan') {
                        # Alive at the root exit = its witness held then; its start line is checked after the stop.
                        $orphan = (& $read @('orphan'))
                        $seen = $orphan['orphan']
                        if ([string]$seen.start_witness -ceq 'present' -and $seen.released -is [bool]) {
                            $orphanAlive = -not $seen.released
                        }
                    }
                }
            } else {
                & $Seams['Sleep'] ([Math]::Min(250, $slice))
            }
            if ($null -ne $rootExitAt -and [long]$managed.ActiveProcesses -eq 0) {
                if ((& $elapsed) -lt $deadline) { $drained = $true }
                break
            }
        }
        $stopCalls = 0
        $stopOk = $false
        $stopConfirmed = $false
        $confirmedAt = $null
        if (-not $drained) {
            $stopCalls++
            $driverStopAttempted = $true
            try { $managed.Stop(); $stopOk = $true } catch { $stopOk = $false }
            $until = (& $elapsed) + 30
            while ($true) {
                if ([long]$managed.ActiveProcesses -eq 0) { $stopConfirmed = $true; $confirmedAt = & $elapsed; break }
                if ((& $elapsed) -ge $until) { break }
                & $Seams['Sleep'] 250
            }
            if ($stopConfirmed -and $null -eq $rootExitAt -and $managed.Wait(0)) { $rootExitAt = & $elapsed }
        } else {
            $confirmedAt = $rootExitAt
        }
        $active = [long]$managed.ActiveProcesses
        $outcome = 'completed'
        if (-not $drained) {
            $outcome = 'stop_unconfirmed'
            if ($stopOk -and $stopConfirmed -and $null -ne $rootExitAt) {
                $outcome = 'stopped_at_deadline'
                if ($guardStop -ceq 'off_switch') { $outcome = 'stopped_by_off_switch' }
                if ($guardStop -ceq 'disk_floor') { $outcome = 'stopped_at_disk_floor' }
            }
        }
        # The drain time, or the end of the stop wait when the drain was never confirmed (a FAIL, never a PASS).
        if ($null -eq $confirmedAt) { $confirmedAt = & $elapsed }
        $seconds = [long][Math]::Floor($confirmedAt)
        # A start line is read only once its witness is released: a foreign, missing or still-held one leaves the
        # orphan and root observations without evidence.
        $after = & $read $members
        foreach ($member in @('orphan', 'root')) {
            if ($members -cnotcontains $member) { continue }
            $seen = $after[$member]
            if ($seen.released -ne $true -or [string]$seen.launch_id -cne $launchId) {
                if ($member -ceq 'orphan') { $orphanAlive = $null }
                if ($null -ne $result) { $result = 'root_unwitnessed' }
            }
        }
        # PR1918 bot P2: an A1 sub-case succeeds only when EVERY expected member started, was released and is bound
        # to this launch; a missing, still-held or foreign one leaves it without evidence (an unconfirmed stop
        # still fails on the job's own count).
        $witnessed = $true
        foreach ($member in $members) {
            $seen = $after[$member]
            if ($null -eq $seen -or [string]$seen.start_witness -cne 'present' -or $seen.released -isnot [bool] -or
                -not $seen.released -or [string]$seen.launch_id -cne $launchId) { $witnessed = $false }
        }
        if ($CaseId -ceq 'A3') { $observation = @{ result = $result } }
        if ($CaseId -ceq 'A1') {
            switch -CaseSensitive ($Name) {
                'early_root_exit_orphan' {
                    $observation = @{ outcome = $outcome; orphan_alive_at_root_exit = $orphanAlive }
                }
                'single_confirmed_stop' { $observation = @{ stop_calls = $stopCalls; stop_confirmed = $stopConfirmed } }
                default {
                    $observation = @{ outcome = $outcome; stop_confirmed = $stopConfirmed
                        active_processes_after = $active; elapsed_seconds = $seconds }
                }
            }
            if (-not $witnessed) {
                if ($Name -ceq 'early_root_exit_orphan') { $observation['orphan_alive_at_root_exit'] = $null }
                elseif ($drained -or $stopConfirmed) { $observation = $null }
            }
        }
        if ($CaseId -ceq 'A2') {
            $observed = & $read $members
            $state = Get-WdShadowLifetimeHeld -Observed $observed -Members $members -LaunchId $launchId
            # Registered witnesses are not an exhaustive job inventory. Preserve a witnessed survivor as FAIL,
            # but do not emit an empty-survivor PASS without a successful Stop and confirmed zero job count.
            $noUnwitnessedSurvivor = ($active -eq 0 -and $stopOk -and $stopConfirmed)
            if ($state['complete'] -and (@($state['held']).Count -gt 0 -or $noUnwitnessedSurvivor)) {
                $live = @()
                $independent = @()
                if ($active -gt 0) { $live = @($state['held']) } else { $independent = @($state['held']) }
                $observation = @{ live_descendants = $live; independent_survivors = $independent
                    child_started_during_stop_survived = (@($state['held']) -ccontains 'late_child') }
            }
        }
        # A successful lock check is not evidence of the run when the root witness is missing, held or foreign.
        if ($CaseId -ceq 'A6' -and $witnessed) {
            $lock = @{ attempted = $false; acquired = $false; released = $false; error = $null }
            if ($stopConfirmed) {
                $lock['attempted'] = $true
                try {
                    $held = New-Object IO.FileStream([string]$plan['lock_path'], [IO.FileMode]::OpenOrCreate,
                        [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
                    $lock['acquired'] = $true
                    $held.Dispose()
                    $lock['released'] = $true
                } catch { $lock['error'] = $_.Exception.GetType().FullName }
            }
            $observation = @{ unexpected_inherited_handles = $handles; lock_reacquire = $lock }
        }
    } catch {
        $driverFailure = $_
        if ($null -ne $managed) {
            try {
                if ($null -ne $managed.PSObject.Methods['StopForExceptionalCleanup']) {
                    $managed.StopForExceptionalCleanup()
                } elseif (-not $driverStopAttempted) { $managed.Stop() }
            } catch { $driverFailure.Exception.Data['owned_cleanup_stop_error'] = [string]$_ }
        }
        throw $driverFailure
    } finally {
        if ($null -ne $managed) {
            try { $managed.Dispose() } catch {
                if ($null -eq $driverFailure) { throw }
                $driverFailure.Exception.Data['owned_cleanup_dispose_error'] = [string]$_
            }
        }
    }
    return @{ case_id = $CaseId; name = $Name; binding = $binding; observation = $observation }
}

function ConvertTo-WdShadowLifetimeCaseObservation {
    # A case observation for Get-WdShadowCaseVerdict from driver results. A2, A5 and A6 (one sub-case each) keep the
    # old one-launch binding; A1 and A3 take Lead's v8 per-sub-case binding: the pins of the run (versioned schema,
    # harness run, case, measured controller, Source, manifest, adapter, deadline) plus, per sub-case, its own launch
    # and measured root from its own driver run. A duplicate sub-case or a result of another case, run or deadline
    # is refused; a missing sub-case is left for the evaluator (UNKNOWN).
    param([string] $CaseId, [object[]] $Results, $Pins)
    if ($CaseId -ceq 'A1' -or $CaseId -ceq 'A3') {
        $pinKeys = @('schema', 'harness_run_id', 'case_id', 'controller_pid', 'controller_creation_utc',
            'source_commit', 'manifest_sha256', 'adapter_sha256', 'deadline_seconds')
        if (-not (Test-WdShadowExactKeys $Pins $pinKeys) -or
            [string]$Pins['schema'] -cne 'wd.mind-shadow-lifetime-subcase-binding.v1' -or
            [string]$Pins['case_id'] -cne $CaseId) {
            throw 'refused: the run pins are not exactly the versioned per-sub-case binding pins'
        }
        $subcases = @{}
        foreach ($result in @($Results)) {
            if ($result -isnot [hashtable] -or [string]$result['case_id'] -cne $CaseId) {
                throw 'refused: a driver result of another case'
            }
            $name = [string]$result['name']
            $bound = $result['binding']
            if ($subcases.ContainsKey($name)) { throw 'refused: two driver results for one sub-case' }
            if ([string]$bound['harness_run_id'] -cne [string]$Pins['harness_run_id'] -or
                [string]$bound['deadline_seconds'] -cne [string]$Pins['deadline_seconds']) {
                throw 'refused: a driver result of another harness run or deadline'
            }
            $subcases[$name] = @{ observation = $result['observation']
                launch = @{ launch_id = $bound['launch_id']; root_pid = $bound['root_pid']
                    root_creation_utc = $bound['root_creation_utc'] } }
        }
        $binding = @{}
        foreach ($key in $pinKeys) { $binding[$key] = $Pins[$key] }
        return @{ binding = $binding; subcases = $subcases }
    }
    if (@('A2', 'A5', 'A6') -cnotcontains $CaseId) { throw "refused: $CaseId has no lifetime case composition" }
    if (@($Results).Count -ne 1 -or $Results[0] -isnot [hashtable] -or [string]$Results[0]['case_id'] -cne $CaseId) {
        throw 'refused: one driver result of this case is required'
    }
    $subcases = @{ ([string]$Results[0]['name']) = $Results[0]['observation'] }
    return @{ binding = $Results[0]['binding']; subcases = $subcases }
}

# Checkpoint 5 (Lead's v8 GO, 2026-10-22T20:34:33Z): the future owned wiring through Root's frozen A7 runner
# (tests/windows/wd_mind_shadow_a7_owned_probe.ps1 at Root head b066290d). Its module file is not on this branch, so
# every routine takes the runner and the other pinned files by explicit path and expected SHA-256 and refuses when
# one is missing or differs; the opt-in switch is an accident guard, never operator or Medium authority. Admission
# (Test-WdShadowOwnedAdmission) runs before any write, load or process seam. The A7 attempt calls Root's
# Invoke-WdMindShadowA7OwnedProbe once with this file's unchanged pinned planner output; the lifetime factory takes
# Root's type initializer, six-argument managed process and measured identity for the A1/A2 job and root seams. The
# outer-context, controller-kill and handle seams stay refused. Nothing here runs a real routine in tests.

function Test-WdShadowOwnedAdmission {
    # Before any write, load or process seam: exactly the host, adapter, loop, planner and runner files, each an
    # existing full C:\ path of printable ASCII with no reparse point in its ancestry and SHA-256 bytes equal to its
    # uppercase pin; the evidence root an existing directory directly in a .codex-audit folder, no reparse ancestry, on
    # the local fixed NTFS C: volume, which keeps the 3 GiB floor plus the bound. Returns the measured pins.
    param([hashtable] $Files, [hashtable] $Expected, [string] $EvidenceRoot, [long] $BoundBytes)
    $keys = @('host', 'adapter', 'loop', 'planner', 'runner')
    if (-not (Test-WdShadowExactKeys $Files $keys) -or -not (Test-WdShadowExactKeys $Expected $keys)) {
        throw 'refused: the pinned files are not exactly host, adapter, loop, planner and runner'
    }
    foreach ($path in (@($keys | ForEach-Object { $Files[$_] }) + @($EvidenceRoot))) {
        if ($path -isnot [string] -or $path -cnotmatch '^C:\\[\x20-\x7E]+\z' -or $path.Contains('"') -or
            [IO.Path]::GetFullPath($path).TrimEnd('\') -cne $path.TrimEnd('\')) {
            throw 'refused: a pinned path is not a full printable ASCII C:\ path'
        }
        $current = $path.TrimEnd('\')
        while ($current.Length -gt 3) {
            if (([IO.File]::Exists($current) -or [IO.Directory]::Exists($current)) -and
                ([IO.File]::GetAttributes($current) -band [IO.FileAttributes]::ReparsePoint)) {
                throw 'refused: a pinned path has reparse ancestry'
            }
            $current = [IO.Path]::GetDirectoryName($current)
        }
    }
    $measured = @{}
    foreach ($key in $keys) {
        if ($Expected[$key] -isnot [string] -or $Expected[$key] -cnotmatch '^[0-9A-F]{64}\z') {
            throw "refused: the $key pin is not an uppercase SHA-256"
        }
        if (-not [IO.File]::Exists($Files[$key])) { throw "refused: the pinned $key file is missing" }
        $sha = [Security.Cryptography.SHA256]::Create()
        try {
            $hash = -join @($sha.ComputeHash([IO.File]::ReadAllBytes($Files[$key])) |
                ForEach-Object { $_.ToString('X2') })
        }
        finally { $sha.Dispose() }
        if ($hash -cne $Expected[$key]) { throw "refused: the $key file is not its pinned bytes" }
        $measured[$key] = $hash
    }
    if (-not [IO.Directory]::Exists($EvidenceRoot) -or
        [IO.Path]::GetFileName([IO.Path]::GetDirectoryName($EvidenceRoot.TrimEnd('\'))) -cne '.codex-audit') {
        throw 'refused: the evidence root is not an existing directory directly inside a .codex-audit folder'
    }
    $drive = New-Object IO.DriveInfo('C')
    if ($drive.DriveType -ne [IO.DriveType]::Fixed -or $drive.DriveFormat -cne 'NTFS') {
        throw 'refused: the evidence volume is not the local fixed NTFS C: volume'
    }
    if ($BoundBytes -le 0 -or $drive.AvailableFreeSpace -lt 3221225472 + $BoundBytes) {
        throw 'refused: C: would fall below the 3 GiB floor plus the bound'
    }
    return $measured
}

function Invoke-WdShadowA7OwnedAttempt {
    # One native A7 sub-case through Root's frozen public runner: admission, this file's pinned plan and literal child
    # command (the five planner functions Root pins are unchanged), Root's runner loaded from its pinned path, ONE call
    # of Invoke-WdMindShadowA7OwnedProbe, and its receipt reduced to the strict A7 entry (attempt and refusal) with the
    # owned-job counts and raw output kept separately. A whole-A7 PASS claim, another sub-case or an entry of any other
    # shape is refused. The two structural loop sub-cases have no owned attempt.
    param([switch] $ApprovedNativeExecution, [string] $Name, [hashtable] $Inputs, [string] $CaseRoot,
        [hashtable] $CaseBinding, [hashtable] $Files, [hashtable] $Expected, [string] $EvidenceRoot)
    if (-not $ApprovedNativeExecution) {
        throw 'refused: the owned A7 attempt needs the explicit opt-in (an accident guard, never authority)'
    }
    [void](Test-WdShadowOwnedAdmission -Files $Files -Expected $Expected -EvidenceRoot $EvidenceRoot `
        -BoundBytes 1048576)
    $plan = Get-WdShadowA7Plan -Name $Name -Inputs $Inputs -CaseRoot $CaseRoot
    if ($null -eq $plan) { throw 'refused: a structural-only A7 sub-case has no owned attempt' }
    $command = Get-WdShadowA7ChildCommand -Plan $plan -AdapterPath $Files['adapter'] `
        -ExpectedLoopSha256 $Expected['loop']
    . $Files['runner']
    $receipt = Invoke-WdMindShadowA7OwnedProbe -ApprovedNativeExecution -Plan $plan -ApprovedInputs $Inputs `
        -CaseRoot $CaseRoot -LiteralAdapterCommand $command -CaseBinding $CaseBinding -HostPath $Files['host'] `
        -AdapterPath $Files['adapter'] -LoopPath $Files['loop'] -PlannerPath $Files['planner'] `
        -EvidenceRoot $EvidenceRoot -ExpectedHostSha256 $Expected['host'] -ExpectedAdapterSha256 $Expected['adapter'] `
        -ExpectedLoopSha256 $Expected['loop'] -ExpectedPlannerSha256 $Expected['planner'] `
        -ExpectedRunnerSha256 $Expected['runner']
    $result = $null
    if ($null -ne $receipt) { $result = $receipt.result }
    if ($null -eq $result -or $receipt.whole_A7_PASS -ne $false -or $result.whole_A7_PASS -ne $false -or
        [string]$receipt.name -cne $Name) {
        throw 'refused: the owned runner receipt is not one attempt of this sub-case'
    }
    $entry = $result.entry
    if (-not (Test-WdShadowExactKeys $entry @('attempt', 'refusal')) -or
        -not (Test-WdShadowExactKeys $entry['attempt'] @('attempt_id', 'probe_pid', 'probe_creation_utc')) -or
        -not (Test-WdShadowExactKeys $entry['refusal'] @('exit_code', 'refusal_seen', 'run_dir_created',
            'process_started'))) { throw 'refused: the owned runner entry is not exactly attempt and refusal' }
    return @{
        name = $Name; entry = $entry; owned_job = $result.owned_job; raw = $result.raw
        status = [string]$result.status
    }
}

function ConvertTo-WdShadowA7CaseObservation {
    # The A7 case observation from owned attempts: the binding pins the runner accepted and, per native sub-case, its
    # strict entry; a duplicate sub-case is refused, a missing one is left for the evaluator (UNKNOWN).
    param([hashtable] $CaseBinding, [object[]] $Attempts)
    $binding = @{}
    foreach ($key in @('schema', 'harness_run_id', 'case_id', 'controller_pid', 'controller_creation_utc',
            'source_commit', 'manifest_sha256', 'adapter_sha256')) { $binding[$key] = $CaseBinding[$key] }
    $subcases = @{}
    foreach ($attempt in @($Attempts)) {
        if ($attempt -isnot [hashtable] -or $subcases.ContainsKey([string]$attempt['name'])) {
            throw 'refused: two owned attempts for one A7 sub-case'
        }
        $subcases[[string]$attempt['name']] = $attempt['entry']
    }
    return @{ binding = $binding; subcases = $subcases }
}

# Checkpoint 7 (Lead's v11 GO, 2026-10-22T21:44:33Z) supersedes the launch refusal below: the A1/A2 root launch
# uses the v10 fixed member plan (child-only opt-in by a fixed literal -Command, which the six-argument API
# accepts as argv) and the opt-in output observer behind one stop-once facade; see New-WdShadowOwnedLifetimeSeams.
# Checkpoint 6 (Lead's v9 GO, 2026-10-22T21:01:34Z): the owned lifetime seams take the synthetic member by explicit
# pinned path, bind the run to the admitted host, member and evidence root and regenerate the one approved plan
# before any write, load or process seam; a substituted plan refuses inside the seam before its first write. The
# six-argument managed process has no child environment and no output cap, so the fixed member's child-local opt-in
# (switch AND WD_MIND_SHADOW_SYNTHETIC_MEMBER=opt-in) and finite log bounds cannot be established: the root launch
# seam refuses with that exact reason and the native lifetime branch stays NOT_READY (no caller or global
# environment change, no generic shell command).

function Test-WdShadowOwnedMemberPin {
    # The pinned synthetic member: the same full C:\ path, ancestry and byte rules as the five admitted files (the A7
    # five-file Test-WdShadowOwnedAdmission stays unchanged).
    param([string] $MemberPath, [string] $ExpectedMemberSha256, [string] $What = 'member')
    if ($MemberPath -cnotmatch '^C:\\[\x20-\x7E]+\z' -or $MemberPath.Contains('"') -or
        [IO.Path]::GetFullPath($MemberPath) -cne $MemberPath) {
        throw "refused: the $What path is not a full printable ASCII C:\ path"
    }
    $current = $MemberPath
    while ($current.Length -gt 3) {
        if (([IO.File]::Exists($current) -or [IO.Directory]::Exists($current)) -and
            ([IO.File]::GetAttributes($current) -band [IO.FileAttributes]::ReparsePoint)) {
            throw "refused: the $What path has reparse ancestry"
        }
        $current = [IO.Path]::GetDirectoryName($current)
    }
    if ($ExpectedMemberSha256 -cnotmatch '^[0-9A-F]{64}\z') {
        throw "refused: the $What pin is not an uppercase SHA-256"
    }
    if (-not [IO.File]::Exists($MemberPath)) { throw "refused: the pinned $What file is missing" }
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $hash = -join @($sha.ComputeHash([IO.File]::ReadAllBytes($MemberPath)) | ForEach-Object { $_.ToString('X2') })
    } finally { $sha.Dispose() }
    if ($hash -cne $ExpectedMemberSha256) { throw "refused: the $What file is not its pinned bytes" }
}

function New-WdShadowOwnedStopOnce {
    # ONE private stop-once facade over the owned managed object, shared by the driver and the output observer: the
    # first Stop request reaches the owned object exactly once (a failure is recorded and rethrown, never retried) and
    # records whether the observer or the driver asked first; later requests only count. Wait observes the output
    # budget before and after the owned wait. Never a process or PID kill.
    param($Inner, [Collections.IDictionary] $Record, [hashtable] $Shared, [scriptblock] $Observe)
    $facade = [pscustomobject]@{ Inner = $Inner; StopRecord = $Record; Shared = $Shared; ObserveHook = $Observe }
    $facade | Add-Member -MemberType ScriptProperty -Name Pid -Value { $this.Inner.Pid }
    $facade | Add-Member -MemberType ScriptProperty -Name ActiveProcesses -Value { $this.Inner.ActiveProcesses }
    $facade | Add-Member -MemberType ScriptProperty -Name TotalProcesses -Value { $this.Inner.TotalProcesses }
    $facade | Add-Member -MemberType ScriptProperty -Name ExitCode -Value { $this.Inner.ExitCode }
    $facade | Add-Member -MemberType ScriptMethod -Name Stop -Value {
        $record = $this.StopRecord
        $record['requests'] = [int]$record['requests'] + 1
        if ([int]$record['inner_calls'] -ne 0) { return }
        $record['inner_calls'] = 1
        $record['origin'] = 'driver'
        if ($this.Shared['observing']) { $record['origin'] = 'observer' }
        if ($this.Shared['exceptional_cleanup']) { $record['origin'] = 'driver_exception_cleanup' }
        try { $this.Inner.Stop() } catch { $record['failed'] = $true; $record['error'] = [string]$_; throw }
    }
    $facade | Add-Member -MemberType ScriptMethod -Name StopForExceptionalCleanup -Value {
        $this.Shared['driver_exception'] = $true
        # A failed earlier Stop counts as attempted too: never another request or inner call.
        if ([int]$this.StopRecord['inner_calls'] -ne 0) { return }
        $this.Shared['exceptional_cleanup'] = $true
        try { $this.Stop() } catch {
            $this.Shared['errors'].Add('driver cleanup stop: ' + [string]$_)
            throw
        } finally { $this.Shared['exceptional_cleanup'] = $false }
    }
    $facade | Add-Member -MemberType ScriptMethod -Name Wait -Value {
        param($Milliseconds)
        & $this.ObserveHook
        $done = $this.Inner.Wait($Milliseconds)
        & $this.ObserveHook
        return $done
    }
    $facade | Add-Member -MemberType ScriptMethod -Name Dispose -Value {
        # A failed owned Dispose is recorded (the receipt then blocks), never rethrown over the original error.
        try { $this.Inner.Dispose(); $this.StopRecord['dispose'] = 'ok' } catch {
            $this.StopRecord['dispose'] = 'failed'; $this.StopRecord['dispose_error'] = [string]$_
            if ($this.Shared['driver_exception']) { $this.Shared['errors'].Add('driver cleanup dispose: ' + [string]$_) }
        }
    }
    return $facade
}

function New-WdShadowOwnedLifetimeSeams {
    # The future real job and root seams of one A1 or A2 lifetime sub-case (Lead's v11 GO): the opt-in switch (an
    # accident guard, never authority); A3, A5 and A6 refused before any side effect; the five-file admission, the
    # member pin, the launch helper pin, the run bound to the admitted host, member and evidence root and the one
    # approved plan regenerated here, all before Root's frozen runner or the helper is loaded into a private module.
    # NewManaged refuses any plan that is not exactly that approved plan before any write, type or process call; for
    # the approved plan it creates the empty root.input, runs Root's type initializer and its six-argument managed
    # process with the admitted host and the ACTUAL argv of the helper's fixed member plan (child-only opt-in, the
    # literal member; never the generic -File arguments), wraps the owned object in the stop-once facade and binds
    # the helper's output observer to that facade. The clock, sleep and free-space seams and every facade wait
    # observe the output budget. Record (one fresh empty table) receives the launch, budget and stop evidence for
    # the receipt. RootCreation reads Root's measured identity; outer-context, controller-kill and handle seams stay
    # refused.
    param([switch] $ApprovedNativeExecution, [string] $CaseId, [string] $Name, [hashtable] $Run, [hashtable] $Files,
        [hashtable] $Expected, [string] $MemberPath, [string] $ExpectedMemberSha256, [string] $HelperPath,
        [string] $ExpectedHelperSha256, [hashtable] $Record, [string] $EvidenceRoot, [long] $MaxOutputBytes = 1048576,
        [hashtable] $Hooks = $null)
    if (-not $ApprovedNativeExecution) {
        throw 'refused: the owned lifetime seams need the explicit opt-in (an accident guard, never authority)'
    }
    if ($MaxOutputBytes -lt 1 -or $MaxOutputBytes -gt 8388608) {
        throw 'refused: the output bound is not 1 to 8388608 bytes (checked before any side effect)'
    }
    if ($null -ne $Hooks -and $Hooks.Count -ne 0) { throw 'refused: the hook table is not one empty table' }
    if ($CaseId -cne 'A1' -and $CaseId -cne 'A2') {
        throw ('refused: owned ' + $CaseId + ' is not supported here (A3 real contexts, A5 parent kill and A6 ' +
            'handle listing stay refused)')
    }
    if ($Record -isnot [hashtable] -or $Record.Count -ne 0) { throw 'refused: the owned record is not one empty table' }
    [void](Test-WdShadowOwnedAdmission -Files $Files -Expected $Expected -EvidenceRoot $EvidenceRoot `
        -BoundBytes 8388608)
    Test-WdShadowOwnedMemberPin -MemberPath $MemberPath -ExpectedMemberSha256 $ExpectedMemberSha256
    Test-WdShadowOwnedMemberPin -MemberPath $HelperPath -ExpectedMemberSha256 $ExpectedHelperSha256 -What 'helper'
    if ($Run -isnot [hashtable] -or [string]$Run['witness_root'] -cne $EvidenceRoot -or
        [string]$Run['host_path'] -cne [string]$Files['host'] -or [string]$Run['member_path'] -cne $MemberPath) {
        throw 'refused: the run is not bound to the admitted host, member and evidence root'
    }
    $approved = Get-WdShadowLifetimePlan -CaseId $CaseId -Name $Name -Run $Run
    $approvedHost = [string]$approved['host_path']
    $approvedDirectory = [string]$approved['witness_directory']
    $approvedLaunch = [string]$approved['binding']['launch_id']
    $approvedArguments = [string[]]@($approved['arguments'])
    $module = New-Module -Name 'WdShadowOwnedRunner' -ScriptBlock { param([string] $Runner) . $Runner } `
        -ArgumentList $Files['runner']
    $launcher = New-Module -Name 'WdShadowOwnedLaunch' -ScriptBlock { param([string] $Helper) . $Helper } `
        -ArgumentList $HelperPath
    $fixed = & $launcher {
        param($HostPath, $Member, $Directory, $Launch, $Subcase, $Lifetime, $Pin)
        Get-WdShadowFixedMemberLaunchPlan -HostPath $HostPath -MemberPath $Member -WitnessDirectory $Directory `
            -LaunchId $Launch -Subcase $Subcase -LifetimeSeconds $Lifetime -ExpectedMemberSha256 $Pin
    } $Files['host'] $MemberPath $approvedDirectory $approvedLaunch ($CaseId + '/' + $Name) `
        ([long]$approved['deadline_seconds'] + 60) $ExpectedMemberSha256
    $fixedArgv = [string[]]@($fixed['argv'])
    $loopText = [string](& $module {
        param([string] $Path, [string] $Sha) (Read-WdA7PinnedFile $Path $Sha -AsText).text
    } $Files['loop'] $Expected['loop'])
    $Record['launch'] = [ordered]@{
        host_path = [string]$Files['host']; argv = $fixedArgv; command_sha256 = [string]$fixed['command_sha256']
        plan_schema = [string]$fixed['schema']; member_sha256 = $ExpectedMemberSha256; started = $false
    }
    $Record['budget'] = [ordered]@{ observations = 0; last = $null; trigger = $null }
    $Record['stop'] = [ordered]@{ requests = 0; inner_calls = 0; origin = $null; failed = $false; error = $null
        dispose = 'not_called'; dispose_error = $null }
    $Record['errors'] = New-Object Collections.Generic.List[string]
    $errors = $Record['errors']
    $shared = @{ observer = $null; observing = $false; exceptional_cleanup = $false
        driver_exception = $false; errors = $errors }
    $budget = $Record['budget']
    $observe = {
        if ($null -eq $shared['observer']) { return }
        $shared['observing'] = $true
        try { $seen = & $shared['observer'] } finally { $shared['observing'] = $false }
        $budget['observations'] = [int]$budget['observations'] + 1
        $budget['last'] = $seen
        if ($null -eq $budget['trigger'] -and [string]$seen['status'] -cne 'WITHIN_OBSERVED_BUDGET') {
            $budget['trigger'] = [string]$seen['status']
        }
    }.GetNewClosure()
    $seams = Get-WdShadowLifetimeSeams
    $clock = $seams['Clock']; $sleep = $seams['Sleep']; $freeBytes = $seams['FreeBytes']
    $seams['Clock'] = { & $observe; return (& $clock @args) }.GetNewClosure()
    $seams['Sleep'] = { & $observe; & $sleep @args }.GetNewClosure()
    $seams['FreeBytes'] = { & $observe; return (& $freeBytes @args) }.GetNewClosure()
    $stopRecord = $Record['stop']; $launchRecord = $Record['launch']
    $stopOnce = ${function:New-WdShadowOwnedStopOnce}
    $seams['NewManaged'] = {
        param($Plan)
        $given = @()
        if ($Plan -is [hashtable]) { $given = @($Plan['arguments']) }
        $same = $Plan -is [hashtable] -and [string]$Plan['host_path'] -ceq $approvedHost -and
            [string]$Plan['witness_directory'] -ceq $approvedDirectory -and $Plan['binding'] -is [hashtable] -and
            [string]$Plan['binding']['launch_id'] -ceq $approvedLaunch -and $given.Count -eq $approvedArguments.Count
        for ($index = 0; $same -and $index -lt $given.Count; $index++) {
            if ([string]$given[$index] -cne $approvedArguments[$index]) { $same = $false }
        }
        if (-not $same) { throw 'refused: the plan is not the approved lifetime plan' }
        $inputPath = $approvedDirectory + '\root.input'
        $stream = New-Object IO.FileStream($inputPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write,
            [IO.FileShare]::None)
        $stream.Dispose()
        try {
            $inner = & $module {
                param([string] $Text, [string] $HostPath, [string[]] $Arguments, [string] $Directory,
                    [string] $InputPath)
                Initialize-WdA7OwnedType $Text
                New-WdA7ManagedProcess $HostPath $Arguments $Directory $InputPath ($Directory + '\root.stdout.log') `
                    ($Directory + '\root.stderr.log')
            } $loopText $approvedHost $fixedArgv $approvedDirectory $inputPath
        } catch { $errors.Add('launch: ' + [string]$_); throw }
        $launchRecord['started'] = $true
        try {
            $facade = & $stopOnce -Inner $inner -Record $stopRecord -Shared $shared -Observe $observe
            $shared['observer'] = & $launcher {
                param($Managed, $Directory, $Max)
                New-WdShadowOwnedOutputObserver -ApprovedObservation -Managed $Managed -EvidenceDirectory $Directory `
                    -MaxOutputBytes $Max
            } $facade $approvedDirectory $MaxOutputBytes
            & $observe
        } catch {
            # Setup failed after the owned object was acquired and before the driver received it: one owned stop
            # (only when none was made yet, never retried) and one Dispose, every error kept, the original rethrown.
            $setup = $_
            $errors.Add('setup: ' + [string]$setup)
            if ([int]$stopRecord['inner_calls'] -eq 0) {
                $stopRecord['inner_calls'] = 1
                $stopRecord['origin'] = 'setup_cleanup'
                try { $inner.Stop() } catch {
                    $stopRecord['failed'] = $true; $stopRecord['error'] = [string]$_
                    $errors.Add('cleanup stop: ' + [string]$_)
                }
            }
            try { $inner.Dispose(); $stopRecord['dispose'] = 'ok' } catch {
                $stopRecord['dispose'] = 'failed'; $stopRecord['dispose_error'] = [string]$_
                $errors.Add('cleanup dispose: ' + [string]$_)
            }
            throw $setup
        }
        return $facade
    }.GetNewClosure()
    $seams['RootCreation'] = {
        param($Managed)
        return [string](& $module { param([int] $Id) (Get-WdA7ObservedIdentity $Id).creation_utc } ([int]$Managed.Pid))
    }.GetNewClosure()
    if ($null -ne $Hooks) { $Hooks['witness'] = $observe }
    return $seams
}

function ConvertTo-WdShadowOwnedLifetimeReceipt {
    # The versioned owned lifetime receipt: the actual launch evidence (the admitted host and the fixed plan argv and
    # command hash passed to the owned process), the observed output budget and the stop-once record. A budget
    # trigger (over the bound, the floor or UNKNOWN), a failed stop or a stop first requested by the observer makes
    # the observation null (UNKNOWN), never PASS; the evidence is kept. Observed bytes only, never native PASS.
    # v2: a driver failure (its original error), a kept setup or cleanup error, a failed owned Dispose, a setup
    # cleanup stop or a partial Record also gives UNKNOWN; reading a partial Record never throws over the failure.
    param([hashtable] $Result, [hashtable] $Record, [object] $Failure = $null)
    $empty = @{}
    $launch = $null; $budget = $null; $stop = $null; $errors = @()
    if ($null -ne $Record) {
        $launch = $Record['launch']; $budget = $Record['budget']; $stop = $Record['stop']
        if ($null -ne $Record['errors']) { $errors = @($Record['errors']) }
    }
    $given = $empty
    if ($null -ne $Result) { $given = $Result }
    $partial = $launch -isnot [Collections.IDictionary] -or $budget -isnot [Collections.IDictionary] -or
        $stop -isnot [Collections.IDictionary]
    $blocked = $partial -or $null -ne $Failure -or $null -eq $Result -or $errors.Count -ne 0
    if (-not $partial) {
        $blocked = $blocked -or $null -ne $budget['trigger'] -or [bool]$stop['failed'] -or
            [string]$stop['origin'] -ceq 'observer' -or [string]$stop['origin'] -ceq 'setup_cleanup' -or
            [string]$stop['origin'] -ceq 'driver_exception_cleanup' -or
            [string]$stop['dispose'] -ceq 'failed'
    }
    $observation = $null
    if (-not $blocked) { $observation = $given['observation'] }
    $failed = $null
    if ($null -ne $Failure) {
        $failed = [ordered]@{ type = $Failure.Exception.GetType().FullName; message = [string]$Failure }
    }
    return [ordered]@{
        schema = 'wd.mind-shadow-owned-lifetime-receipt.v2'; case_id = $given['case_id']; name = $given['name']
        binding = $given['binding']; observation = $observation; launch = $launch; budget = $budget; stop = $stop
        errors = [string[]]$errors; failure = $failed; budget_or_stop_blocked = [bool]$blocked; native_pass = $false
    }
}

function Invoke-WdShadowOwnedLifetimeDriver {
    # The public owned A1/A2 lifetime run: the opt-in switch, then the owned seams (all admission, pins, run binding
    # and the approved plan before any write), then the generic driver, whose first write is the registration file,
    # and the versioned receipt. The generic driver with injected seams stays a test surface, never a boundary.
    param([switch] $ApprovedNativeExecution, [string] $CaseId, [string] $Name, [hashtable] $Run, [hashtable] $Files,
        [hashtable] $Expected, [string] $MemberPath, [string] $ExpectedMemberSha256, [string] $HelperPath,
        [string] $ExpectedHelperSha256, [string] $EvidenceRoot, [long] $MaxOutputBytes = 1048576)
    if (-not $ApprovedNativeExecution) {
        throw 'refused: the owned lifetime run needs the explicit opt-in (an accident guard, never authority)'
    }
    if ($MaxOutputBytes -lt 1 -or $MaxOutputBytes -gt 8388608) {
        throw 'refused: the output bound is not 1 to 8388608 bytes (checked before any side effect)'
    }
    $record = @{}
    $hooks = @{}
    $seams = New-WdShadowOwnedLifetimeSeams -ApprovedNativeExecution -CaseId $CaseId -Name $Name -Run $Run `
        -Files $Files -Expected $Expected -MemberPath $MemberPath -ExpectedMemberSha256 $ExpectedMemberSha256 `
        -HelperPath $HelperPath -ExpectedHelperSha256 $ExpectedHelperSha256 -Record $record `
        -EvidenceRoot $EvidenceRoot -MaxOutputBytes $MaxOutputBytes -Hooks $hooks
    # After admission every driver failure is kept in the v2 receipt (UNKNOWN), never rethrown without it.
    $result = $null
    $failure = $null
    try {
        $result = Invoke-WdShadowLifetimeDriver -CaseId $CaseId -Name $Name -Run $Run -Seams $seams `
            -WitnessObserve $hooks['witness']
    } catch { $failure = $_ }
    try { return ConvertTo-WdShadowOwnedLifetimeReceipt -Result $result -Record $record -Failure $failure }
    catch { if ($null -ne $failure) { throw $failure }; throw }
}

# Checkpoints 2 to 4: the case evaluators, the A7 pre-start drivers and the A1, A2, A3, A5 and A6 lifetime drivers
# exist, but no driver produces native observations (A7 has no owned-job no-start witness and its real child seam is
# disabled; the real lifetime job seams are disabled until the pinned owned-job API is reconciled, and A1 and A3 have no
# per-sub-case launch binding; A4 has no publication-kill evidence), so every case stays NOT_READY (native NOT_RUN) and
# the harness refuses by default, on a partial run and on opt-in; it never calls a driver.
$cases = [ordered]@{
    'A1' = 'NOT_READY'
    'A2' = 'NOT_READY'
    'A3' = 'NOT_READY'
    'A4' = 'NOT_READY'
    'A5' = 'NOT_READY'
    'A6' = 'NOT_READY'
    'A7' = 'NOT_READY'
}
Write-Error ('refused: checkpoint 4 has case evaluators, A7 pre-start drivers and lifetime drivers on disabled ' +
    'real seams but no whole run; ' +
    (@($cases.Keys) -join ', ') + ' stay NOT_READY (native NOT_RUN)')
exit 3
