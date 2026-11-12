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


@pytest.mark.parametrize(('connected', 'optimized'), [(True, False), (True, True), (False, False), (False, True)])
def test_linux_network_probe_cannot_be_disabled_by_optimization(connected, optimized):
    script = _linux_steps()['Portable tests with outbound denial']
    line = next(line.strip() for line in script.splitlines() if 'python -c' in line and 'connect_ex' in line)
    probe = re.search(r"python -c '(.*)'$", line).group(1)
    fake = '''import sys,types\nclass FakeSocket:\n def settimeout(self,*a): pass\n def connect_ex(self,*a): return %d\n def close(self): pass\nmodule=types.ModuleType("socket"); module.socket=FakeSocket; sys.modules["socket"]=module\n''' % (0 if connected else 1)
    result = _isolated_python(fake + probe, optimized)
    assert (result.returncode != 0) is connected


def test_linux_pytest_uses_unoptimized_clean_environment():
    script = _linux_steps()['Portable tests with outbound denial']
    line = next(line.strip() for line in script.splitlines() if 'python -c' in line and 'sys.flags.optimize' in line)
    check = re.search(r"python -c '(.*)'$", line).group(1)
    assert _isolated_python(check, optimized=True).returncode != 0
    assert _isolated_python(check, optimized=False).returncode == 0
    assert line.startswith('env -i ') and script.index(line) < script.index('python -m pytest')


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


def _assert_hosted_before_firewall(script):
    assert HOSTED_GUARD in script
    assert script.index(HOSTED_GUARD) < script.index('sudo iptables')


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
  [ "$1" = iptables ] || return 20
  case "$2" in
    -C)
      [ "$MODE" != inspect_error ] || return 21
      read -r state < "$STATE"
      [ "$MODE" != inspect_after_delete ] || [ "$state" != absent ] || return 22
      [ "$state" = present ]
      ;;
    -S)
      [ "$MODE" != inspect_error ] || return 21
      read -r state < "$STATE"
      if [ "$MODE" = inspect_after_delete ] && [ "$state" = absent ]; then return 22; fi
      if [ "$state" = present ]; then printf '%s\n' "-A OUTPUT -m comment --comment $rule -j REJECT"; else printf '%s\n' '-P OUTPUT ACCEPT'; fi
      ;;
    -D)
      [ "$MODE" != delete_error ] || return 23
      if [ "$MODE" != remains ]; then printf '%s\n' absent > "$STATE"; fi
      ;;
    *) return 24 ;;
  esac
}
'''


def _run_fake_cleanup(tmp_path, script, *, mode, initial, hosted=True):
    bash = Path(r'C:\Program Files\Git\bin\bash.exe') if os.name == 'nt' else Path(shutil.which('bash') or '')
    if not bash.is_file():
        pytest.skip('Bash is required to exercise the offline cleanup shell')
    state, log = tmp_path / 'state', tmp_path / 'calls'
    state.write_bytes((initial + '\n').encode('ascii'))
    env = {'PATH': '', 'RUNNER_ENVIRONMENT': 'github-hosted' if hosted else 'self-hosted',
           'GITHUB_RUN_ID': '42', 'GITHUB_RUN_ATTEMPT': '7', 'STATE': str(state),
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
    ('inspect_error', False, 'absent'),
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
