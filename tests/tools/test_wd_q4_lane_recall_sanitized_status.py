"""Synthetic relay diagnostics: never publish query or child stderr."""
import hashlib
import json
import os
import sys
import pytest

from test_wd_q4_lane_recall_helper import SHELLS, bundle, invoke

pytestmark = pytest.mark.skipif(os.name != 'nt' or not SHELLS, reason='Windows PowerShell required')
RAW = b'{"situation":"private-query-canary","time":"2026-11-06T00:00:00Z"}'


def synthetic_wrapper(bundle, exit_code):
    env, root = bundle
    wrapper = root / 'Invoke-WdBridgePython.ps1'
    wrapper.write_text('''param([Parameter(Position=0)][string]$Tool,[Parameter(ValueFromRemainingArguments)][string[]]$Rest)
$memory=[IO.MemoryStream]::new(); [Console]::OpenStandardInput().CopyTo($memory)
if ($env:DIAG_ENV_PATH) { [IO.File]::WriteAllText($env:DIAG_ENV_PATH,[string]$env:WD_Q4_DIAGNOSTIC_PHASES) }
if ($env:DIAG_PHASE) {
  if ($env:DIAG_EXTRA) { [Console]::Error.WriteLine('private-stderr-canary') }
  if (-not $env:DIAG_PHASE.StartsWith('{"status":')) { [Console]::Error.WriteLine('{"error":"lane_caller_refused","authority":"none"}') }
  [Console]::Error.WriteLine($env:DIAG_PHASE)
} else {
  [Console]::Error.WriteLine('private-stderr-canary')
  [Console]::Error.WriteLine('{"status":"EXITED","exit_code":0,"cleanup":"CONFIRMED"}')
}
if ($env:DIAG_EXIT -ne '0') { exit ([int]$env:DIAG_EXIT) }
$sha=[Security.Cryptography.SHA256]::Create(); try { [Console]::WriteLine(([BitConverter]::ToString($sha.ComputeHash($memory.ToArray()))).Replace('-','').ToLowerInvariant()) } finally { $sha.Dispose() }
$global:LASTEXITCODE=0
''', encoding='utf-8')
    manifest = root / 'deployment-manifest.json'
    manifest.write_text(json.dumps({'schema_version':1,'files':{'Invoke-WdBridgePython.ps1':hashlib.sha256(wrapper.read_bytes()).hexdigest()}}), encoding='utf-8')
    env['WD_REBOOT_EXPECTED_MANIFEST_HASH'] = hashlib.sha256(manifest.read_bytes()).hexdigest()
    env['DIAG_EXIT'] = str(exit_code)
    return env


@pytest.mark.parametrize('exit_code', [0, 2, 17])
def test_opt_in_reports_actual_child_exit_without_raw_stderr(bundle, exit_code):
    env = synthetic_wrapper(bundle, exit_code)
    result = invoke(SHELLS[0], env, RAW, '-Runtime', '-WithStatus')
    assert result.returncode == exit_code
    assert json.loads(result.stderr) == {'status':'EXITED','exit_code':exit_code,'cleanup':'CONFIRMED'}
    assert b'private' not in result.stderr
    assert result.stdout.strip() == (hashlib.sha256(RAW).hexdigest().encode() if exit_code == 0 else b'')


def test_diagnostic_remains_off_by_default(bundle):
    env = synthetic_wrapper(bundle, 2)
    result = invoke(SHELLS[0], env, RAW, '-Runtime')
    assert result.returncode == 2 and result.stdout == b'' and result.stderr == b''


def test_bad_input_never_claims_child_exit(bundle):
    env = synthetic_wrapper(bundle, 0)
    result = invoke(SHELLS[0], env, b'not-json', '-Runtime', '-WithStatus')
    assert result.returncode != 0 and result.stdout == b''
    assert json.loads(result.stderr) == {'status':'UNKNOWN','exit_code':None,'cleanup':'UNKNOWN'}


def test_timed_out_call_does_not_claim_a_child_exit(bundle):
    env, root = bundle
    env['RECALL_TEST_MODE'] = 'slow'
    env['RECALL_STARTED'] = str(root / 'started')
    env['RECALL_SENTINEL'] = str(root / 'sentinel')
    result = invoke(SHELLS[0], env, RAW, '-WithStatus', '-TimeoutSeconds', '4')
    assert result.returncode == 3 and result.stdout == b''
    assert json.loads(result.stderr) == {'status':'UNKNOWN','exit_code':None,'cleanup':'UNKNOWN'}


@pytest.mark.parametrize('raw_phase,expected', [
    ('{"phase":"CALLER_TRANSPORT_CLEANUP_UNKNOWN"}', 'CALLER_TRANSPORT_CLEANUP_UNKNOWN'),
    ('{"status":"BOOTSTRAP_REFUSED"}', 'BOOTSTRAP_REFUSED'),
    ('{"phase":"private-query-canary"}', None),
])
def test_opt_in_forwards_only_closed_phase_class(bundle, raw_phase, expected):
    env = synthetic_wrapper(bundle, 3)
    env['DIAG_PHASE'] = raw_phase
    result = invoke(SHELLS[0], env, RAW, '-Runtime', '-WithStatus')
    assert result.returncode == 3 and result.stdout == b''
    lines = [json.loads(line) for line in result.stderr.splitlines()]
    assert lines[0] == {'status':'EXITED','exit_code':3,'cleanup':'CONFIRMED'}
    assert lines[1:] == ([{'phase':expected}] if expected else [])
    assert b'private' not in result.stderr


@pytest.mark.parametrize('opt_in,inherited,expected', [(False,'1',''), (True,'','1')])
def test_diagnostic_environment_only_reaches_child_by_opt_in(bundle, opt_in, inherited, expected):
    env, root = bundle
    env = synthetic_wrapper(bundle, 2)
    env['WD_Q4_DIAGNOSTIC_PHASES'] = inherited
    env['DIAG_ENV_PATH'] = str(root/'diagnostic-env')
    result = invoke(SHELLS[0], env, RAW, '-Runtime', *(['-WithStatus'] if opt_in else []))
    assert result.returncode == 2 and (root/'diagnostic-env').read_text() == expected
    assert env['WD_Q4_DIAGNOSTIC_PHASES'] == inherited


def test_ambiguous_multiple_recognized_phases_are_discarded(bundle):
    env = synthetic_wrapper(bundle, 3)
    env['DIAG_PHASE'] = '{"phase":"CALLER_TIMEOUT_UNKNOWN"}\n{"status":"BOOTSTRAP_UNKNOWN"}'
    result = invoke(SHELLS[0], env, RAW, '-Runtime', '-WithStatus')
    assert result.returncode == 3
    assert [json.loads(line) for line in result.stderr.splitlines()] == [
        {'status':'EXITED','exit_code':3,'cleanup':'CONFIRMED'}]


def test_extra_stderr_cannot_spoof_a_phase(bundle):
    env = synthetic_wrapper(bundle, 3)
    env['DIAG_EXTRA'] = '1'
    env['DIAG_PHASE'] = '{"phase":"CALLER_TIMEOUT_UNKNOWN"}'
    result = invoke(SHELLS[0], env, RAW, '-Runtime', '-WithStatus')
    assert result.returncode == 3
    assert [json.loads(line) for line in result.stderr.splitlines()] == [
        {'status':'EXITED','exit_code':3,'cleanup':'CONFIRMED'}]
    assert b'private' not in result.stderr


def test_native_python_phase_survives_wrapper_without_raw_stderr(bundle):
    env, root = bundle
    wrapper = root/'Invoke-WdBridgePython.ps1'
    python = sys.executable.replace("'", "''")
    native = root/'synthetic_phase.py'
    native.write_text("import sys; sys.stdin.buffer.read(); sys.stderr.write('{\"error\":\"lane_caller_refused\",\"authority\":\"none\"}\\n{\"phase\":\"CALLER_SESSION_UNKNOWN\"}\\n'); sys.exit(3)", encoding='utf-8')
    wrapper.write_text("param([Parameter(Position=0)][string]$Tool,[Parameter(ValueFromRemainingArguments)][string[]]$Rest)\n"
                       "& '"+python+"' '"+str(native).replace("'", "''")+"'\nexit $LASTEXITCODE\n", encoding='utf-8')
    manifest = root/'deployment-manifest.json'
    manifest.write_text(json.dumps({'schema_version':1,'files':{'Invoke-WdBridgePython.ps1':hashlib.sha256(wrapper.read_bytes()).hexdigest()}}), encoding='utf-8')
    env['WD_REBOOT_EXPECTED_MANIFEST_HASH'] = hashlib.sha256(manifest.read_bytes()).hexdigest()
    result = invoke(SHELLS[0], env, RAW, '-Runtime', '-WithStatus')
    assert result.returncode == 3 and result.stdout == b''
    assert [json.loads(line) for line in result.stderr.splitlines()] == [
        {'status':'EXITED','exit_code':3,'cleanup':'CONFIRMED'}, {'phase':'CALLER_SESSION_UNKNOWN'}]
    assert b'private' not in result.stderr
