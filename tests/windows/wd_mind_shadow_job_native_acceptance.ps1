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
    and the run binding (harness run, case, launch id, root PID, root creation time, deadline); a missing, extra,
    mistyped or partial value is UNKNOWN and a foreign binding is FAIL. A4 is always NOT_READY: the type resumes the
    root inside its constructor (L1), so no supported before-resume timing point exists, and no whole run can PASS
    until one does. No native driver produces observations yet, so every case stays NOT_READY (native NOT_RUN); a
    synthetic or supplied PASS is never native acceptance, and the script takes no observation input.

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
    # A1 stop sub-cases: the named outcome, a confirmed drain to ActiveProcesses 0 and an end inside the deadline plus
    # the stop wait (1800 + 30 s); any mistyped value is UNKNOWN.
    param($Observation, [string] $Outcome)
    if (-not (Test-WdShadowExactKeys $Observation @('outcome', 'stop_confirmed', 'active_processes_after',
            'elapsed_seconds'))) { return 'UNKNOWN' }
    $active = $Observation['active_processes_after']
    $elapsed = $Observation['elapsed_seconds']
    if (-not (Test-WdShadowCount $active) -or -not (Test-WdShadowCount $elapsed) -or
        $Observation['stop_confirmed'] -isnot [bool] -or $Observation['outcome'] -isnot [string]) { return 'UNKNOWN' }
    if ($Observation['outcome'] -cne $Outcome -or $active -ne 0 -or $elapsed -gt 1830 -or
        -not $Observation['stop_confirmed']) { return 'FAIL' }
    return 'PASS'
}

function Get-WdShadowRefusalVerdict {
    # A7 sub-cases (R1, R2, T1 and the pre-start guards): refused with a nonzero exit and the refusal seen, and
    # neither a run directory nor a process was created.
    param($Observation)
    if (-not (Test-WdShadowExactKeys $Observation @('exit_code', 'refusal_seen', 'run_dir_created',
            'process_started'))) { return 'UNKNOWN' }
    if (-not (Test-WdShadowCount $Observation['exit_code']) -or $Observation['refusal_seen'] -isnot [bool] -or
        $Observation['run_dir_created'] -isnot [bool] -or $Observation['process_started'] -isnot [bool]) {
        return 'UNKNOWN'
    }
    if ($Observation['process_started'] -or $Observation['run_dir_created'] -or $Observation['exit_code'] -eq 0) {
        return 'FAIL'
    }
    if ($Observation['refusal_seen']) { return 'PASS' }
    return 'UNKNOWN'
}

function Get-WdShadowSubcaseVerdict {
    # One sub-case of RCO1's A1-A7 contract (proposal v2) with RCO2's G1-G4 and Lead's guard cases.
    param([string] $CaseId, [string] $Name, $Observation, [hashtable] $Binding)
    switch -CaseSensitive ($CaseId + '/' + $Name) {
        'A1/deadline' { return Get-WdShadowStopVerdict $Observation 'stopped_at_deadline' }
        'A1/pipe_holding_grandchild' { return Get-WdShadowStopVerdict $Observation 'stopped_at_deadline' }
        'A1/off_switch_during_run' { return Get-WdShadowStopVerdict $Observation 'stopped_by_off_switch' }
        'A1/disk_floor_hit' { return Get-WdShadowStopVerdict $Observation 'stopped_at_disk_floor' }
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
                $lock['released'] -isnot [bool]) { return 'UNKNOWN' }
            if ($handles.Count -gt 0 -or
                ($lock['attempted'] -and (-not $lock['acquired'] -or -not $lock['released']))) {
                return 'FAIL'
            }
            if ($lock['attempted']) { return 'PASS' }
            return 'UNKNOWN'
        }
        { $_ -cmatch '^A7/(integrity_mismatch|preloaded_type|lane_loop_zero_functions|lane_loop_two_functions|' +
            'off_switch_before_start|off_switch_denied_read|disk_floor_before_start|reparse_ancestry)$' } {
            return Get-WdShadowRefusalVerdict $Observation
        }
    }
    return 'UNKNOWN'
}

function Get-WdShadowCaseVerdict {
    # One case from one strict observation: the run binding first (partial is UNKNOWN, foreign is FAIL), then every
    # required sub-case once. A4 is always NOT_READY: no supported before-resume timing point exists (L1), and
    # run_intent or run_start is never that witness.
    param([string] $CaseId, $Binding, $Observation)
    if ($CaseId -ceq 'A4') { return 'NOT_READY' }
    $required = @{
        A1 = @('deadline', 'early_root_exit_orphan', 'pipe_holding_grandchild', 'off_switch_during_run',
            'disk_floor_hit', 'single_confirmed_stop')
        A2 = @('survivors')
        A3 = @('outer_no_limits', 'outer_kill_on_close', 'planned_launch_context')
        A5 = @('controller_death')
        A6 = @('inherited_handles')
        A7 = @('integrity_mismatch', 'preloaded_type', 'lane_loop_zero_functions', 'lane_loop_two_functions',
            'off_switch_before_start', 'off_switch_denied_read', 'disk_floor_before_start', 'reparse_ancestry')
    }
    if (@('A1', 'A2', 'A3', 'A5', 'A6', 'A7') -cnotcontains $CaseId) { return 'UNKNOWN' }
    $bindingKeys = @('harness_run_id', 'case_id', 'launch_id', 'root_pid', 'root_creation_utc', 'deadline_seconds')
    if (-not (Test-WdShadowExactKeys $Binding $bindingKeys) -or [string]$Binding['case_id'] -cne $CaseId -or
        -not (Test-WdShadowExactKeys $Observation @('binding', 'subcases'))) { return 'UNKNOWN' }
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
    # The whole run: exactly A1-A7, each one of the four verdicts. Any FAIL fails it; while no supported A4
    # before-resume timing point exists (L1), no run can PASS, whatever A4 verdict is supplied.
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

# Checkpoint 2: the case evaluators exist, but no native driver produces their observations, so every case stays
# NOT_READY (native NOT_RUN) and the harness refuses even on opt-in.
$cases = [ordered]@{
    'A1' = 'NOT_READY'
    'A2' = 'NOT_READY'
    'A3' = 'NOT_READY'
    'A4' = 'NOT_READY'
    'A5' = 'NOT_READY'
    'A6' = 'NOT_READY'
    'A7' = 'NOT_READY'
}
Write-Error ('refused: checkpoint 2 has case evaluators but no native drivers; ' + (@($cases.Keys) -join ', ') +
    ' stay NOT_READY (native NOT_RUN)')
exit 3
