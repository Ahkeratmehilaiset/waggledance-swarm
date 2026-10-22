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

function Get-WdShadowCaseVerdict {
    # One case from one strict observation: the run binding first (partial is UNKNOWN, foreign is FAIL), then every
    # required sub-case once. A4 is always NOT_READY: A4 is the job kill at the publication replacement and no
    # evidence of it exists here; run_intent or run_start is never that witness. A7 has its own pre-start attempt
    # binding (Get-WdShadowA7CaseVerdict); the managed launch binding below is never loosened for it.
    param([string] $CaseId, $Binding, $Observation)
    if ($CaseId -ceq 'A4') { return 'NOT_READY' }
    if ($CaseId -ceq 'A7') { return Get-WdShadowA7CaseVerdict -Binding $Binding -Observation $Observation }
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
    # The child's whole command: for preloaded_type the R1 preload (the installed lane loop's one
    # Initialize-WdNativeTurnType, defined and called; no single function is a setup error, exit 4), then exactly one
    # call of the adapter with the planned inputs as literals. An adapter refusal becomes one error line
    # "WD-A7-REFUSAL <message>" and exit 2; otherwise the adapter's own exit code passes through (0 when it set none).
    param($Plan, [string] $AdapterPath)
    $inputs = $Plan['inputs']
    $text = '$ErrorActionPreference = ''Stop''; '
    if ($Plan['preload'] -ceq 'lane_loop_type') {
        $loop = ConvertTo-WdShadowA7Literal ('C:\Python\wd-reboot-bundles\' + $inputs['ExpectedSourceCommit'] +
            '\Invoke-WdLaneTurnLoop.ps1')
        $text += '$wdA7Tokens = $null; $wdA7Errors = $null; ' +
            '$wdA7Ast = [Management.Automation.Language.Parser]::ParseFile(' + $loop +
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
    # UNKNOWN. $null when the sub-case has no plan.
    param([string] $Name, [hashtable] $Inputs, [string] $CaseRoot, [string] $AdapterPath, [string] $HostPath,
        [scriptblock] $Prepare, [scriptblock] $RunChild, [scriptblock] $ListChildren)
    if ($null -eq $Prepare -or $null -eq $RunChild -or $null -eq $ListChildren) {
        throw 'refused: a driver seam is missing'
    }
    $plan = Get-WdShadowA7Plan -Name $Name -Inputs $Inputs -CaseRoot $CaseRoot
    if ($null -eq $plan) { return $null }
    $command = Get-WdShadowA7ChildCommand -Plan $plan -AdapterPath $AdapterPath
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
    # evidence is incomplete, which is never PASS).
    param([string] $CaseId, [string] $Name, [hashtable] $Run, [hashtable] $Seams)
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
    $binding = $plan['binding']
    $deadline = [double]$plan['deadline_seconds']
    $members = [string[]]$plan['members']
    $launchId = [string]$Run['launch_id']
    $observation = $null
    $managed = $null
    $createError = $null
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
                $now = Get-WdShadowMemberObservation -WitnessDirectory $directory -Expected $want
                if (@($want | Where-Object { [string]$now[$_].start_witness -cne 'present' }).Count -eq 0) { break }
                & $Seams['Sleep'] 250
            }
        }
        $result = $null
        if ($CaseId -ceq 'A3') {
            $root = (Get-WdShadowMemberObservation -WitnessDirectory $directory -Expected @('root'))['root']
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
                $now = Get-WdShadowMemberObservation -WitnessDirectory $directory -Expected $members
                if (@($members | Where-Object { $now[$_].released -eq $false }).Count -eq 0) { break }
                & $Seams['Sleep'] 250
            }
            $observed = Get-WdShadowMemberObservation -WitnessDirectory $directory -Expected $members
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
                        $orphan = (Get-WdShadowMemberObservation -WitnessDirectory $directory -Expected @('orphan'))
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
        $after = Get-WdShadowMemberObservation -WitnessDirectory $directory -Expected $members
        foreach ($member in @('orphan', 'root')) {
            if ($members -cnotcontains $member) { continue }
            $seen = $after[$member]
            if ($seen.released -ne $true -or [string]$seen.launch_id -cne $launchId) {
                if ($member -ceq 'orphan') { $orphanAlive = $null }
                if ($null -ne $result) { $result = 'root_unwitnessed' }
            }
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
        }
        if ($CaseId -ceq 'A2') {
            $observed = Get-WdShadowMemberObservation -WitnessDirectory $directory -Expected $members
            $state = Get-WdShadowLifetimeHeld -Observed $observed -Members $members -LaunchId $launchId
            if ($state['complete']) {
                $live = @()
                $independent = @()
                if ($active -gt 0) { $live = @($state['held']) } else { $independent = @($state['held']) }
                $observation = @{ live_descendants = $live; independent_survivors = $independent
                    child_started_during_stop_survived = (@($state['held']) -ccontains 'late_child') }
            }
        }
        if ($CaseId -ceq 'A6') {
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
    } finally {
        if ($null -ne $managed) { $managed.Dispose() }
    }
    return @{ case_id = $CaseId; name = $Name; binding = $binding; observation = $observation }
}

function ConvertTo-WdShadowLifetimeCaseObservation {
    # A case observation for Get-WdShadowCaseVerdict from driver results, with the old launch binding unchanged. That
    # binding names one launch and root, so only the one-sub-case lifetime cases (A2, A5, A6) compose; A1 and A3 need
    # one launch per sub-case, and composing them is refused until a per-sub-case binding is decided.
    param([string] $CaseId, [object[]] $Results)
    if (@('A2', 'A5', 'A6') -cnotcontains $CaseId) {
        throw "refused: $CaseId needs one launch per sub-case; the case binding names one launch and root"
    }
    if (@($Results).Count -ne 1 -or $Results[0] -isnot [hashtable] -or [string]$Results[0]['case_id'] -cne $CaseId) {
        throw 'refused: one driver result of this case is required'
    }
    $subcases = @{ ([string]$Results[0]['name']) = $Results[0]['observation'] }
    return @{ binding = $Results[0]['binding']; subcases = $subcases }
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
