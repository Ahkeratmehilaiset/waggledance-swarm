"""Bridge-owned Limited bootstrap of the five watchers and Tools while WD-Supervisor is OFF (pure fakes)."""
import json
import os
from pathlib import Path

import pytest

from test_wd_reboot_bundle import REBOOT, LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import load

HELPER = REBOOT / 'WdBridgeLimitedBootstrap.ps1'
FUNCTIONS = ['Get-WdBridgeLimitedBootstrapDecision', 'Get-WdBridgeReconcileVerdict', 'ConvertTo-WdBridgeUtc',
             'Test-WdBridgeToolsReadinessPinned', 'Invoke-WdBridgeLimitedBootstrap', 'Get-WdBridgeSupervisorArguments']
PRELUDE = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
GEN = 'a' * 40
THREAD = '01a0adff-4558-7e80-8936-6aad0d6df821'
LANES = "@('codex-lead-1','codex-tools-1','claude-rco-1','claude-rco-2','fable-5')"
HELD = "[pscustomobject]@{present=$true;enabled=$false;state='Disabled'}"
GONE = "[pscustomobject]@{present=$false;enabled=$null;state=''}"
BASE = dict(IsAdministrator='$false', SupervisorTaskEnabled='$false', SupervisorTaskState="'Disabled'",
            DriverTasks=f'@({HELD},{HELD})', DeployedGeneration=f"'{GEN}'", ExpectedGeneration=f"'{GEN}'",
            WatcherAgents=LANES, ToolsThreadId=f"'{THREAD}'")
READY = ("[pscustomobject]@{schema='wd.tools-consumer-ready.v3';status='terminal_ready';readiness_scope='native_cli_only';"
         f"conversation_surface='native_terminal';agent='codex-tools-1';generation='{GEN}';thread_id='{THREAD}';"
         "pid=41;process_start_utc='2026-09-30T10:00:01Z';native_pid=42;native_parent_pid=41;"
         "native_process_start_utc='2026-09-30T10:00:02Z';ready_at_utc='2026-09-30T10:00:05Z';task_completion_verified=$false}")
INV = '[Globalization.CultureInfo]::InvariantCulture'
CONSUMER = ("@{process_id=41;parent_process_id=9;name='powershell.exe';"
            f"created_utc=[DateTimeOffset]::Parse('2026-09-30T10:00:01.4Z',{INV})}}")
NATIVE = ("@{process_id=42;parent_process_id=41;name='codex.exe';"
          f"created_utc=[DateTimeOffset]::Parse('2026-09-30T10:00:02.3Z',{INV})}}")
TOOLS_FAILURES = ('native_gone', 'native_reused', 'native_unrelated', 'consumer_reused', 'schema_v2', 'string_pid',
                  'thread_mismatch', 'miscased_readiness')
APPLY = '[2026-09-30T10:00:00Z] [APPLY] host=x; age :: RELAUNCHED watcher:fable-5 pid=7 out-of-task-job'
VERIFY = '[2026-09-30T10:00:09Z] [dry-run] host=x; age :: HOLD verified WD-BridgeMergeDriver disabled/not-running (hold)'


def observation(**changes):
    return '@{' + ';'.join(f'{key}={value}' for key, value in dict(BASE, **changes).items()) + '}'


def run(ps, body):
    script = PRELUDE + '\n'.join(load(HELPER, name) for name in FUNCTIONS) + '\n' + body
    return json.loads(_run_powershell(script, executable=ps).stdout)


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case,changes', [
    ('ready', {}), ('elevated', dict(IsAdministrator='$true')),
    ('supervisor_enabled', dict(SupervisorTaskEnabled='$true', SupervisorTaskState="'Ready'")),
    ('supervisor_unobserved', dict(SupervisorTaskEnabled='$null', SupervisorTaskState="''")),
    ('driver_enabled', dict(DriverTasks=f"@({HELD.replace('enabled=$false', 'enabled=$true')},{HELD})")),
    ('driver_running', dict(DriverTasks=f"@({HELD.replace('Disabled', 'Running')},{HELD})")),
    ('driver_queued', dict(DriverTasks=f"@({HELD.replace('Disabled', 'Queued')},{HELD})")),
    ('driver_ambiguous', dict(DriverTasks=f"@({HELD.replace('present=$true', 'present=$null')},{HELD})")),
    ('driver_missing', dict(DriverTasks=f'@({HELD},{GONE})')),
    ('drivers_unobserved', dict(DriverTasks='$null')),
    ('generation_drift', dict(DeployedGeneration=f"'{'b' * 40}'")),
    ('generation_upper', dict(DeployedGeneration=f"'{'A' * 40}'", ExpectedGeneration=f"'{'A' * 40}'")),
    ('lane_missing', dict(WatcherAgents=LANES.replace(",'fable-5'", ''))),
    ('lane_twice', dict(WatcherAgents=LANES.replace("'fable-5'", "'fable-5','fable-5'"))),
    ('lane_miscased', dict(WatcherAgents=LANES.replace("'fable-5'", "'Fable-5'"))),
    ('thread_missing', dict(ToolsThreadId="''")), ('thread_upper', dict(ToolsThreadId=f"'{THREAD.upper()}'"))])
def test_the_bridge_path_launches_only_limited_beside_a_disabled_supervisor(ps, case, changes):
    decision = run(ps, f"$o={observation(**changes)}\nGet-WdBridgeLimitedBootstrapDecision @o | ConvertTo-Json -Compress\n")
    assert (decision['schema'], decision['authority']) == ('wd.bridge-limited-bootstrap-decision.v1', 'none')
    assert decision['action'] == ('launch' if case == 'ready' else 'refuse'), decision
    assert (not decision['reasons']) == (case == 'ready')


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case,ok,stage,runs', [
    ('ready', True, 'done', ['True', 'False']), ('elevated', False, 'decision', []),
    ('apply_failed', False, 'apply', ['True']), ('apply_conflict', False, 'apply', ['True']),
    ('apply_mutated', False, 'apply', ['True']),
    ('late_readiness', True, 'done', ['True', 'False'])] + [(case, False, 'tools', ['True']) for case in TOOLS_FAILURES] + [
    ('verify_would', False, 'verify', ['True', 'False']), ('verify_wrong_mode', False, 'verify', ['True', 'False']),
    ('mode_changed', False, 'mode', ['True', 'False'])])
def test_one_reconcile_then_live_bound_tools_and_a_clean_dry_run(ps, case, ok, stage, runs):
    first = observation(IsAdministrator='$true') if case == 'elevated' else observation()
    later = observation(SupervisorTaskEnabled='$true', SupervisorTaskState="'Ready'") if case == 'mode_changed' else first
    applied = APPLY + {'apply_conflict': '; CONFLICT watcher:fable-5 count=2',
                       'apply_mutated': '; DISABLED WD-BridgeMergeDriver (deliberate standing-driver HOLD)'}.get(case, '')
    checked = {'verify_would': VERIFY + '; WOULD-RELAUNCH watcher:fable-5',
               'verify_wrong_mode': VERIFY.replace('[dry-run]', '[APPLY]')}.get(case, VERIFY)
    ready = {'thread_mismatch': READY.replace(THREAD, 'f' * 8 + THREAD[8:]),
             'miscased_readiness': READY.replace('thread_id', 'Thread_Id'),
             'schema_v2': READY.replace('ready.v3', 'ready.v2'),
             'string_pid': READY.replace(';pid=41;', ";pid='41';")}.get(case, READY)
    consumer = CONSUMER.replace('10:00:01.4Z', '10:00:04Z') if case == 'consumer_reused' else CONSUMER
    native = {'native_gone': '$null', 'native_reused': NATIVE.replace('10:00:02.3Z', '10:00:09Z'),
              'native_unrelated': NATIVE.replace('parent_process_id=41', 'parent_process_id=99')}.get(case, NATIVE)
    exit_code = 1 if case == 'apply_failed' else 0
    body = f"""
$script:seen=0; $script:runs=New-Object System.Collections.Generic.List[string]; $script:reads=0; $script:naps=0
$observe={{ $script:seen++; if($script:seen -eq 1){{ return {first} }}; return {later} }}
$run={{ param($Apply) $script:runs.Add([string]$Apply)
 if($Apply){{ return [pscustomobject]@{{exit_code={exit_code};lines=@('{applied}')}} }}
 return [pscustomobject]@{{exit_code=0;lines=@('{checked}')}} }}
$read={{ $script:reads++; if('{case}' -eq 'late_readiness' -and $script:reads -lt 3){{ return $null }}; return {ready} }}
$procs={{ param($ProcessId) if([int]$ProcessId -eq 41){{ return {consumer} }}; if([int]$ProcessId -eq 42){{ return {native} }}; return $null }}
$sleep={{ param($Seconds) $script:naps++ }}
$result=Invoke-WdBridgeLimitedBootstrap -Observe $observe -RunSupervisorOnce $run -ReadToolsReadiness $read `
 -GetProcess $procs -Sleep $sleep -ToolsWaitSeconds 4
@{{result=$result;runs=@($script:runs);naps=$script:naps}} | ConvertTo-Json -Depth 5 -Compress
"""
    out = run(ps, body)
    result = out['result']
    assert (result['schema'], result['authority'], result['supervisor_task']) == (
        'wd.bridge-limited-bootstrap.v1', 'none', 'untouched')
    assert (result['ok'], result['stage']) == (ok, stage), result
    assert list(out['runs'] or []) == runs
    assert result['reconcile_ran'] is (case != 'elevated')
    assert (result['process_currency'], result['responsiveness'], result['legacy_records']) == (
        'five_watchers_and_tools_process_current' if ok else 'not_claimed', 'unknown', 'unknown')
    assert out['naps'] == (2 if case == 'late_readiness' else 4 if case in TOOLS_FAILURES else 0)
    if ok:
        assert result['tools_binding'] == {
            'schema': 'wd.tools-consumer-ready.v3', 'generation': GEN, 'thread_id': THREAD, 'pid': 41, 'native_pid': 42,
            'process_start_utc': '2026-09-30T10:00:01.0000000+00:00',
            'native_process_start_utc': '2026-09-30T10:00:02.0000000+00:00',
            'ready_at_utc': '2026-09-30T10:00:05.0000000+00:00'}
        assert (result['apply_summary'], result['verify_summary']) == (APPLY, VERIFY)
    elif stage in ('verify', 'mode'):
        # Later refusal does not erase the already measured process binding.
        assert result['tools_binding']['thread_id'] == THREAD
        assert result['tools_binding']['native_pid'] == 42
    else:
        assert result['tools_binding'] is None


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['exact', 'other_agent', 'other_worktree', 'old_schema', 'missing'])
def test_the_tools_thread_pin_is_the_recorded_conversation_identity(tmp_path, ps, case):
    worktree = tmp_path / 'tools'
    state = worktree / '.codex-audit' / 'wd-turn-loop'
    state.mkdir(parents=True)
    saved = {'schema': 'wd.codex-conversation.v1', 'agent': 'codex-tools-1', 'worktree': str(worktree), 'thread_id': THREAD}
    saved.update({'other_agent': {'agent': 'codex-lead-1'}, 'other_worktree': {'worktree': str(tmp_path)},
                  'old_schema': {'schema': 'wd.codex-conversation.v0'}}.get(case, {}))
    if case != 'missing':
        (state / 'conversation.json').write_text(json.dumps(saved), encoding='utf-8')
    literal = str(worktree).replace("'", "''")
    script = (PRELUDE + '\n'.join(load(HELPER, name) for name in ('Read-WdBridgeToolsReadiness', 'Read-WdBridgeToolsThreadPin'))
              + f"\n@{{pin=(Read-WdBridgeToolsThreadPin -Worktree '{literal}')}} | ConvertTo-Json -Compress\n")
    assert json.loads(_run_powershell(script, executable=ps).stdout)['pin'] == (THREAD if case == 'exact' else '')


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_both_supervisor_runs_pass_the_exact_bridge_workers_only_switch(ps):
    body = ("$f={ param($Apply) @(Get-WdBridgeSupervisorArguments -SupervisorScript 'wd_supervisor.ps1' "
            "-ConfigPath 'wd_supervisor_loop.json' -Apply $Apply | ForEach-Object { [string]$_ }) }\n"
            "try { [void](Get-WdBridgeSupervisorArguments -SupervisorScript ('a' + [char]34 + 'b') -ConfigPath 'c' -Apply $true)"
            "; $q='accepted' } catch { $q='refused' }\n"
            "@{apply=@(& $f $true);verify=@(& $f $false);quoted=$q} | ConvertTo-Json -Compress\n")
    out = run(ps, body)
    head = ['-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', 'wd_supervisor.ps1']
    assert out['apply'] == head + ['-Apply', '-BridgeWorkersOnly', '-ConfigPath', 'wd_supervisor_loop.json']
    assert out['verify'] == head + ['-BridgeWorkersOnly', '-ConfigPath', 'wd_supervisor_loop.json']
    assert out['quoted'] == 'refused'
    # The one process start takes exactly these arguments, for the APPLY run and the dry run alike.
    text = HELPER.read_text(encoding='utf-8')
    assert text.count('Get-WdBridgeSupervisorArguments -SupervisorScript $SupervisorScript') == 1
    assert text.count('$info.Arguments = ') == 1


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('value,expected', [
    ("'2026-09-30T10:00:05Z'", '2026-09-30T10:00:05.0000000+00:00'),
    ("'2026-09-30T13:00:05.5+03:00'", '2026-09-30T10:00:05.5000000+00:00'),
    ("[datetime]::SpecifyKind([datetime]'2026-09-30T10:00:05', 'Utc')", '2026-09-30T10:00:05.0000000+00:00'),
    ("'2026-09-30T10:00:05'", None), ("'2026-09-30 10:00:05Z'", None), ("'2026-09-30T10:00:05z'", None),
    ("[datetime]::SpecifyKind([datetime]'2026-09-30T10:00:05', 'Unspecified')", None), ("''", None), ('41', None)])
def test_timestamps_count_only_with_an_explicit_utc_designator(ps, value, expected):
    body = (f"$u=ConvertTo-WdBridgeUtc ({value})\n"
            f"@{{utc=if($null -eq $u){{$null}}else{{$u.ToString('o',{INV})}}}} | ConvertTo-Json -Compress\n")
    assert run(ps, body)['utc'] == expected


# --- bounded supervisor wait (RCO2, Lead request b83d58ec) -------------------------------------------------
import subprocess  # noqa: E402
import time  # noqa: E402

SUPERVISOR_FUNCTIONS = ['Get-WdBridgeSupervisorArguments', 'Invoke-WdBridgeSupervisorOnce', 'Get-WdBridgeReconcileVerdict']
FAKE_PARAMS = "param([switch]$Apply, [switch]$BridgeWorkersOnly, [string]$ConfigPath = '')\n"
# The supervisor run builds the child's Windows PowerShell module path from $env:ProgramFiles and $env:SystemRoot,
# which are unset off Windows, so there it throws before any child starts (pwsh on Linux CI, 2026-10-01); the hung
# case also stops its child with taskkill.
WINDOWS_HOST = pytest.mark.skipif(os.name != 'nt', reason='the supervisor run builds a Windows PowerShell module path')


def run_supervisor(ps, body):
    script = PRELUDE + '\n'.join(load(HELPER, name) for name in SUPERVISOR_FUNCTIONS) + '\n' + body
    return json.loads(_run_powershell(script, executable=ps).stdout)


def supervise(ps, tmp_path, fake_body, timeout):
    fake = tmp_path / 'fake_supervisor.ps1'
    fake.write_text(FAKE_PARAMS + fake_body, encoding='utf-8')
    config = tmp_path / 'config.json'
    config.write_text('{}', encoding='utf-8')
    # The call is timed INSIDE PowerShell: a timed-out child inherits the harness's capture pipe
    # (.NET starts children with handle inheritance), so wall time outside would include its life.
    # The test stops its OWN timed-out fake right after measuring, so the pipe closes promptly.
    return run_supervisor(ps, (
        "$watch = [Diagnostics.Stopwatch]::StartNew()\n"
        f"$r = Invoke-WdBridgeSupervisorOnce -HostPath '{ps}' -SupervisorScript '{fake}' -ConfigPath '{config}' "
        f"-Apply $true -TimeoutSeconds {timeout}\n"
        "$elapsed = $watch.Elapsed.TotalSeconds\n"
        "if ($r.timed_out) { Stop-Process -Id $r.process_id -Force -ErrorAction SilentlyContinue }\n"
        "$v = Get-WdBridgeReconcileVerdict -Run $r -Mode 'APPLY'\n"
        "[pscustomobject]@{run=$r; verdict=$v; elapsed=$elapsed} | ConvertTo-Json -Depth 5 -Compress\n"))


@WINDOWS_HOST
@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_a_hung_reconcile_is_bounded_and_unknown_never_success(tmp_path, ps):
    result = supervise(ps, tmp_path, "Start-Sleep -Seconds 45\n", 2)
    try:
        assert result['elapsed'] < 20   # at 7779 this call did not return while the child ran (reproduced first)
        run, verdict = result['run'], result['verdict']
        assert run['timed_out'] is True and run['exit_code'] is None and isinstance(run['process_id'], int)
        assert verdict['ok'] is False
        assert any('UNKNOWN' in reason and 'not stopped' in reason for reason in verdict['reasons'])
    finally:
        # The helper leaves a timed-out run alive by design; this test stops its OWN fake process tree.
        subprocess.run(['taskkill', '/PID', str(result['run']['process_id']), '/T', '/F'], capture_output=True)


@WINDOWS_HOST
@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_a_reconcile_inside_its_bound_still_reports_its_exit_code_and_lines(tmp_path, ps):
    result = supervise(ps, tmp_path, f"Write-Output '{APPLY}'\nexit 0\n", 60)
    run, verdict = result['run'], result['verdict']
    assert run['timed_out'] is False and run['exit_code'] == 0 and run['lines'] == [APPLY]
    assert verdict['ok'] is True and verdict['summary'] == APPLY


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_the_bootstrap_stops_at_apply_on_a_timed_out_reconcile(ps):
    body = (f"$o={observation()}\n"
            "Invoke-WdBridgeLimitedBootstrap -Observe { $o }.GetNewClosure() "
            "-RunSupervisorOnce { param([bool] $ApplyRun) [pscustomobject]@{exit_code=$null; lines=@(); error_text=''; "
            "timed_out=$true; process_id=4242} } -ReadToolsReadiness { $null } -GetProcess { param([int] $Id) $null } "
            "-Sleep { param([int] $Seconds) } | ConvertTo-Json -Depth 5 -Compress\n")
    result = run(ps, body)
    assert (result['ok'], result['stage'], result['reconcile_ran']) == (False, 'apply', True)
    assert any('UNKNOWN' in reason and 'pid 4242' in reason for reason in result['reasons'])