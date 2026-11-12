"""Offline CI must not run untrusted source or alter a persistent host firewall."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest
import yaml


WORKFLOW = Path(__file__).resolve().parents[2] / '.github/workflows/mind-adopt-platform-coverage.yml'
HOSTED_GUARD = 'if [ "${RUNNER_ENVIRONMENT:-}" != \'github-hosted\' ]; then echo \'Expected disposable hosted Linux\' >&2; exit 1; fi'


def _linux_steps():
    workflow = yaml.safe_load(WORKFLOW.read_bytes())
    return {step['name']: step['run'] for step in workflow['jobs']['adopt-platform-linux']['steps']
            if 'name' in step and 'run' in step}


def _windows_script():
    workflow = yaml.safe_load(WORKFLOW.read_bytes())
    return next(step['run'] for step in workflow['jobs']['adopt-platform-windows']['steps']
                if step.get('name') == 'Run exact synthetic coverage in offline Windows container')


def _isolated_python(code, optimized, *args):
    env = {'PATH': '', 'PYTHONDONTWRITEBYTECODE': '1'}
    if os.name == 'nt':
        env['SystemRoot'] = os.environ['SystemRoot']
    command = [sys.executable, '-S']
    if optimized:
        command.append('-O')
        env['PYTHONOPTIMIZE'] = '1'
    return subprocess.run(command + ['-c', code, *map(str, args)], env=env, capture_output=True, text=True, timeout=10)


@pytest.mark.parametrize(('connected', 'optimized'), [(True, False), (True, True), (False, False), (False, True)])
def test_windows_network_probe_cannot_be_disabled_by_optimization(connected, optimized):
    line = next(line.strip() for line in _windows_script().splitlines()
                if 'python.exe -c' in line and 'connect_ex' in line)
    probe = re.search(r' -c "(.*)"$', line).group(1)
    # Fake socket never calls the network; 0 emulates an unexpected connection.
    fake = '''import sys,types\nclass FakeSocket:\n def settimeout(self,*a): pass\n def connect_ex(self,*a): return %d\n def close(self): pass\nmodule=types.ModuleType("socket"); module.socket=FakeSocket; sys.modules["socket"]=module\n''' % (0 if connected else 1)
    result = _isolated_python(fake + probe, optimized)
    assert (result.returncode != 0) is connected


def test_windows_pytest_rejects_optimized_interpreter():
    script = _windows_script()
    unset = script.index('Remove-Item Env:PYTHONOPTIMIZE -ErrorAction SilentlyContinue')
    line = next(line.strip() for line in script.splitlines() if 'python.exe -c' in line and 'sys.flags.optimize' in line)
    check = re.search(r' -c "(.*)"$', line).group(1)
    assert _isolated_python(check, optimized=True).returncode != 0
    assert _isolated_python(check, optimized=False).returncode == 0
    assert unset < script.index(line) < script.index('python.exe -m pytest')
    assert "if ($LASTEXITCODE -ne 0) { throw 'Optimized Python is not allowed' }" in script


@pytest.mark.parametrize(('index', 'connected', 'optimized'), [(i, c, o) for i in range(2) for c in (False, True) for o in (False, True)])
def test_linux_network_probe_cannot_be_disabled_by_optimization(index, connected, optimized):
    script = _linux_steps()['Portable tests with outbound denial']
    lines = [line.strip() for line in script.splitlines() if 'python -c' in line and 'connect_ex' in line]
    assert len(lines) == 2
    line = lines[index]
    probe = re.search(r"python -c '(.*)'$", line).group(1)
    fake = '''import sys,types\nclass FakeSocket:\n def __init__(self,*a): pass\n def settimeout(self,*a): pass\n def connect_ex(self,*a): return %d\n def close(self): pass\nmodule=types.ModuleType("socket"); module.socket=FakeSocket; module.AF_INET6=10; module.SOCK_STREAM=1; sys.modules["socket"]=module\n''' % (0 if connected else 1)
    result = _isolated_python(fake + probe, optimized)
    assert (result.returncode != 0) is connected


def test_linux_pytest_uses_unoptimized_clean_environment():
    script = _linux_steps()['Portable tests with outbound denial']
    line = next(line.strip() for line in script.splitlines() if 'python -c' in line and 'sys.flags.optimize' in line)
    check = re.search(r"python -c '(.*)'$", line).group(1)
    assert _isolated_python(check, optimized=True).returncode != 0
    assert _isolated_python(check, optimized=False).returncode == 0
    assert line.startswith('env -i ') and script.index(line) < script.index('python -m pytest')


def test_linux_network_denial_covers_ipv4_and_ipv6():
    script = _linux_steps()['Portable tests with outbound denial']
    trap = script.index("trap 'cleanup || exit 1' EXIT")
    for tool in ('iptables', 'ip6tables'):
        assert f'sudo {tool} -I OUTPUT' in script
        assert f'sudo {tool} -C OUTPUT' in script
        assert trap < script.index(f'sudo {tool} -I OUTPUT')
    assert 'socket.AF_INET6' in script


@pytest.mark.parametrize(('index', 'optimized', 'valid'), [(i, o, v) for i in range(2) for o in (False, True) for v in (False, True)])
def test_linux_coverage_checks_cannot_be_disabled_by_optimization(tmp_path, index, optimized, valid):
    script = _linux_steps()['Portable tests with outbound denial']
    lines = [line.strip() for line in script.splitlines() if 'python -c' in line and 'xml.etree' in line]
    check = re.search(r"python -c '(.*)' \"\$scratch/[^\"]+\"$", lines[index]).group(1)
    passed = (['test_default_off', 'test_bad_pins_refuse_without_job', 'test_marker_window_starts_after_synthetic_preflight',
               'test_preflight_and_marker_waits_remain_bounded[False]', 'test_preflight_and_marker_waits_remain_bounded[True]']
              if index == 0 else ['test_launcher_default_off', 'test_launcher_refuses_invalid_config_without_starting_job'])
    skipped = 33 if index == 0 else 10
    root = ET.Element('testsuites'); suite = ET.SubElement(root, 'testsuite', skipped=str(skipped), failures='0', errors='0')
    if valid:
        for name in passed:
            ET.SubElement(suite, 'testcase', name=name)
        for number in range(skipped):
            ET.SubElement(ET.SubElement(suite, 'testcase', name='skip-' + str(number)), 'skipped')
    path = tmp_path / 'synthetic-junit.xml'
    ET.ElementTree(root).write(path, encoding='utf-8')
    result = _isolated_python(check, optimized, path)
    assert (result.returncode == 0) is valid


@pytest.mark.parametrize(('index', 'fault'), [(i, f) for i in range(2) for f in ('fake_skip_count', 'duplicate_skipped', 'hidden_failure', 'hidden_error')])
def test_linux_coverage_requires_real_skips_and_unique_testcases(tmp_path, index, fault):
    script = _linux_steps()['Portable tests with outbound denial']
    lines = [line.strip() for line in script.splitlines() if 'python -c' in line and 'xml.etree' in line]
    check = re.search(r"python -c '(.*)' \"\$scratch/[^\"]+\"$", lines[index]).group(1)
    passed = (['test_default_off', 'test_bad_pins_refuse_without_job', 'test_marker_window_starts_after_synthetic_preflight',
               'test_preflight_and_marker_waits_remain_bounded[False]', 'test_preflight_and_marker_waits_remain_bounded[True]']
              if index == 0 else ['test_launcher_default_off', 'test_launcher_refuses_invalid_config_without_starting_job'])
    skipped = 33 if index == 0 else 10
    root = ET.Element('testsuites'); suite = ET.SubElement(root, 'testsuite', skipped=str(skipped), failures='0', errors='0')
    if fault == 'fake_skip_count':
        for i in range(len(passed) + skipped):
            ET.SubElement(suite, 'testcase', classname='synthetic', name=passed[i % len(passed)])
    elif fault == 'duplicate_skipped':
        for name in passed:
            ET.SubElement(suite, 'testcase', classname='synthetic', name=name)
        for _ in range(skipped):
            ET.SubElement(ET.SubElement(suite, 'testcase', classname='synthetic', name='same-skipped-id'), 'skipped')
    else:
        for index, name in enumerate(passed):
            case = ET.SubElement(suite, 'testcase', classname='synthetic', name=name)
            if index == 0:
                ET.SubElement(case, 'failure' if fault == 'hidden_failure' else 'error')
        for index in range(skipped):
            ET.SubElement(ET.SubElement(suite, 'testcase', classname='synthetic', name='skip-' + str(index)), 'skipped')
    path = tmp_path / 'synthetic-forged-junit.xml'
    ET.ElementTree(root).write(path, encoding='utf-8')
    result = _isolated_python(check, True, path)
    assert result.returncode != 0


def _powershell_hosts():
    names = ('powershell.exe', 'pwsh.exe') if os.name == 'nt' else ('pwsh',)
    return [host for name in names if (host := shutil.which(name))]


@pytest.mark.parametrize('host', _powershell_hosts())
@pytest.mark.parametrize(('index', 'fault'), [(i, f) for i in range(2) for f in ('valid', 'hidden_skip', 'hidden_failure', 'hidden_error', 'duplicate')])
def test_windows_coverage_checks_actual_testcase_elements(tmp_path, host, index, fault):
    script = _windows_script()
    if index == 0:
        marker = '$suite=$result.testsuites.testsuite;'
        ending = "Write-Output 'Windows coverage:"
        doc = 'result'
        names = ['passed-' + str(i) for i in range(36)] + ['test_real_job_finishes_proven_early_refusal', 'test_stdout_error_after_real_f3_mutation_is_unknown']
        skip_names = set(names[-2:])
    else:
        marker = '$suite=$continuation.testsuites.testsuite;'
        ending = "Write-Output 'Continuation Windows coverage:"
        doc = 'continuation'
        names = ['test_anchored_template_validates_real_synthetic_files_and_pinned_script',
                 'test_tampered_tuple_is_refused_before_attempt',
                 'test_fixed_process_invokes_real_job_bootstrap_and_cli_with_synthetic_continuation',
                 'test_real_config_and_template_are_both_hash_bound',
                 'test_actual_cli_through_real_job_with_synthetic_continuation',
                 'test_actual_cli_job_refuses_changed_record', 'test_launcher_default_off',
                 'test_launcher_refuses_invalid_config_without_starting_job',
                 'test_real_job_bootstrap_cli_twice_with_synthetic_orchestrator',
                 'test_real_nested_windows_jobs_use_actual_fixed_call',
                 'test_interruption_is_unknown_and_pending_survives_a_second_invocation',
                 'test_landed_result_with_mismatched_record_is_unknown']
        skip_names = set()
    block = marker + script.split(marker, 1)[1].split(ending, 1)[0]
    root = ET.Element('testsuites'); suite = ET.SubElement(root, 'testsuite', failures='0', errors='0', skipped=str(len(skip_names)))
    cases = [ET.SubElement(suite, 'testcase', name=name) for name in names]
    for case in cases:
        if case.attrib['name'] in skip_names:
            ET.SubElement(case, 'skipped')
    if fault == 'hidden_skip': ET.SubElement(cases[0], 'skipped')
    if fault == 'hidden_failure': ET.SubElement(cases[0], 'failure')
    if fault == 'hidden_error': ET.SubElement(cases[0], 'error')
    if fault == 'duplicate': cases[1].set('name', cases[0].attrib['name'])
    path = tmp_path / 'synthetic-windows-junit.xml'; ET.ElementTree(root).write(path, encoding='utf-8')
    env = {'PATH': '', 'WD_TEST_JUNIT': str(path)}
    if os.name == 'nt': env['SystemRoot'] = os.environ['SystemRoot']
    code = "$ErrorActionPreference='Stop'\n[xml]$" + doc + " = Get-Content -Raw -LiteralPath $env:WD_TEST_JUNIT\n" + block
    result = subprocess.run([host, '-NoProfile', '-NonInteractive', '-Command', code], env=env, capture_output=True, text=True, timeout=20)
    assert (result.returncode == 0) is (fault == 'valid'), result.stderr


def _assert_hosted_before_firewall(script):
    assert HOSTED_GUARD in script
    first_call = re.search(r'\bsudo\s+(?:iptables\b|ip6tables\b|"\$tool")', script)
    assert first_call is not None and script.index(HOSTED_GUARD) < first_call.start()


def test_firewall_test_and_cleanup_require_disposable_host():
    steps = _linux_steps()
    _assert_hosted_before_firewall(steps['Portable tests with outbound denial'])
    _assert_hosted_before_firewall(steps['Cleanup firewall'])


def test_named_pytest_invocations_use_source_only_archive():
    script = _linux_steps()['Portable tests with outbound denial']
    lines = [line.strip() for line in script.splitlines() if 'python -m pytest' in line]
    assert len(lines) == 3
    assert all(line.startswith('( cd "$scratch/source"; env -i ') and line.endswith(' )') for line in lines)
    assert sum('--collect-only' in line for line in lines) == 1
    assert sum('--junitxml' in line for line in lines) == 2
    assert all('PYTEST_DISABLE_PLUGIN_AUTOLOAD=1' in line for line in lines)
    assert "trap 'cleanup || exit 1' EXIT" in script


# Shell function only: it records calls to this test's files and can never run
# the host's sudo or iptables. No subprocess inherits the host PATH.
FAKE_SUDO = r'''
sudo() {
  printf '%s\n' "$*" >> "$LOG"
  case "$1" in
    iptables) state_file="$STATE" ;;
    ip6tables) state_file="$STATE6" ;;
    *) return 20 ;;
  esac
  case "$MODE" in *_ipv6) target=ip6tables; mode="${MODE%_ipv6}" ;; *) target=iptables; mode="$MODE" ;; esac
  if [ "$1" != "$target" ]; then mode=normal; fi
  case "$2" in
    -C)
      [ "$mode" != inspect_error ] || return 21
      read -r state < "$state_file"
      [ "$mode" != inspect_after_delete ] || [ "$state" != absent ] || return 22
      [ "$state" = present ]
      ;;
    -S)
      [ "$mode" != inspect_error ] || return 21
      read -r state < "$state_file"
      if [ "$mode" = inspect_after_delete ] && [ "$state" = absent ]; then return 22; fi
      if [ "$state" = present ]; then printf '%s\n' "-A OUTPUT -m comment --comment $rule -j REJECT"; else printf '%s\n' '-P OUTPUT ACCEPT'; fi
      ;;
    -D)
      [ "$mode" != delete_error ] || return 23
      if [ "$mode" != remains ]; then printf '%s\n' absent > "$state_file"; fi
      ;;
    *) return 24 ;;
  esac
}
'''


def _run_fake_cleanup(tmp_path, script, *, mode, initial, hosted=True, initial6='absent'):
    bash = Path(r'C:\Program Files\Git\bin\bash.exe') if os.name == 'nt' else Path(shutil.which('bash') or '')
    if not bash.is_file():
        pytest.skip('Bash is required to exercise the offline cleanup shell')
    state, state6, log = tmp_path / 'state', tmp_path / 'state6', tmp_path / 'calls'
    state.write_bytes((initial + '\n').encode('ascii'))
    state6.write_bytes((initial6 + '\n').encode('ascii'))
    env = {'PATH': '', 'RUNNER_ENVIRONMENT': 'github-hosted' if hosted else 'self-hosted',
           'GITHUB_RUN_ID': '42', 'GITHUB_RUN_ATTEMPT': '7', 'STATE': str(state), 'STATE6': str(state6),
           'LOG': str(log), 'MODE': mode}
    if os.name == 'nt':
        env['SystemRoot'] = os.environ['SystemRoot']
    result = subprocess.run([str(bash), '-c', FAKE_SUDO + '\n' + script], env=env,
                            text=True, capture_output=True, timeout=10)
    return result, state.read_text(encoding='ascii').strip(), log.read_text(encoding='ascii') if log.exists() else ''


@pytest.mark.parametrize(('mode', 'initial', 'ok', 'after'), [
    ('normal', 'absent', True, 'absent'),
    ('normal', 'present', True, 'absent'),
    ('inspect_error', 'present', False, 'present'),
    ('delete_error', 'present', False, 'present'),
    ('remains', 'present', False, 'present'),
    ('inspect_after_delete', 'present', False, 'absent'),
])
def test_always_cleanup_distinguishes_absence_from_errors(tmp_path, mode, initial, ok, after):
    result, state, calls = _run_fake_cleanup(tmp_path, _linux_steps()['Cleanup firewall'],
                                              mode=mode, initial=initial)
    assert (result.returncode == 0) is ok
    assert state == after
    assert 'iptables -S OUTPUT' in calls


def test_always_cleanup_never_touches_self_hosted_firewall(tmp_path):
    result, state, calls = _run_fake_cleanup(tmp_path, _linux_steps()['Cleanup firewall'],
                                              mode='normal', initial='present', hosted=False)
    assert result.returncode != 0
    assert state == 'present'
    assert calls == ''


@pytest.mark.parametrize(('mode', 'ok', 'after'), [
    ('normal', True, 'absent'),
    ('inspect_error', False, 'present'),
    ('delete_error', False, 'present'),
    ('remains', False, 'present'),
    ('inspect_after_delete', False, 'absent'),
])
def test_exit_trap_confirms_removal_and_inspection(tmp_path, mode, ok, after):
    script = _linux_steps()['Portable tests with outbound denial']
    begin = script.index('cleanup() {')
    end = script.index('\n}', begin) + len('\n}')
    function = script[begin:end]
    trap = next(line for line in script.splitlines() if line.startswith('trap '))
    assert trap == "trap 'cleanup || exit 1' EXIT"
    probe = 'rule="wd-adopt-42-7"\n' + function + '\n' + trap + '\nexit 0'
    result, state, calls = _run_fake_cleanup(tmp_path, probe, mode=mode, initial='present')
    assert (result.returncode == 0) is ok
    assert state == after
    assert 'iptables -S OUTPUT' in calls
    if mode != 'inspect_error':
        assert 'iptables -D OUTPUT' in calls


def test_exit_trap_preserves_body_failure_after_successful_cleanup(tmp_path):
    script = _linux_steps()['Portable tests with outbound denial']
    begin = script.index('cleanup() {')
    end = script.index('\n}', begin) + len('\n}')
    trap = next(line for line in script.splitlines() if line.startswith('trap '))
    probe = 'rule="wd-adopt-42-7"\n' + script[begin:end] + '\n' + trap + '\nexit 9'
    result, state, calls = _run_fake_cleanup(tmp_path, probe, mode='normal', initial='present')
    assert result.returncode == 9 and state == 'absent'
    assert 'iptables -D OUTPUT' in calls


@pytest.mark.parametrize('kind', ['always', 'exit_trap'])
@pytest.mark.parametrize(('mode', 'initial6', 'ok', 'after4', 'after6'), [
    ('normal', 'present', True, 'absent', 'absent'),
    ('normal', 'absent', True, 'absent', 'absent'),
    ('inspect_error_ipv6', 'present', False, 'absent', 'present'),
    ('delete_error_ipv6', 'present', False, 'absent', 'present'),
    ('remains_ipv6', 'present', False, 'absent', 'present'),
    ('inspect_after_delete_ipv6', 'present', False, 'absent', 'absent'),
    ('inspect_error', 'present', False, 'present', 'absent'),
])
def test_both_firewall_families_are_checked_and_cleaned(tmp_path, kind, mode, initial6, ok, after4, after6):
    steps = _linux_steps()
    if kind == 'always':
        script = steps['Cleanup firewall']
    else:
        source = steps['Portable tests with outbound denial']
        begin = source.index('cleanup() {'); end = source.index('\n}', begin) + len('\n}')
        trap = next(line for line in source.splitlines() if line.startswith('trap '))
        script = 'rule="wd-adopt-42-7"\n' + source[begin:end] + '\n' + trap + '\nexit 0'
    result, state, calls = _run_fake_cleanup(tmp_path, script, mode=mode, initial='present', initial6=initial6)
    assert (result.returncode == 0) is ok
    assert state == after4
    assert (tmp_path / 'state6').read_text(encoding='ascii').strip() == after6
    assert 'iptables -S OUTPUT' in calls and 'ip6tables -S OUTPUT' in calls
