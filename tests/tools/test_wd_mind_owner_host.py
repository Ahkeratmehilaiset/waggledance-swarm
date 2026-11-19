"""Synthetic owner-host integration; no installed bundle, model or live bridge."""
import hashlib
import base64
import ctypes
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / 'ops/windows/reboot/Start-WdMindOwnerHost.ps1'
HELPER = ROOT / 'ops/windows/reboot/Start-WdMindManualOwner.ps1'
SHELL = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
PYTHON = Path(sys.executable).resolve()


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _environment():
    # Never forward the live bridge/session environment into the child.
    names = ('SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'PATH', 'PATHEXT', 'TEMP', 'TMP',
             'USERPROFILE', 'LOCALAPPDATA', 'APPDATA')
    return {name: os.environ[name] for name in names if name in os.environ}


def _process_start(pid):
    process = subprocess.Popen([str(SHELL), '-NoProfile', '-NonInteractive', '-Command',
        '(Get-Process -Id ' + str(pid) + ").StartTime.ToUniversalTime().ToString('o')"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_environment())
    stdout, stderr = process.communicate(timeout=10)
    if process.returncode != 0 or stderr:
        raise RuntimeError('synthetic controller identity unavailable')
    return stdout.decode('ascii').strip()


@pytest.fixture
def control():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateEventW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p)
    kernel.CreateEventW.restype = ctypes.c_void_p
    kernel.SetEvent.argtypes = (ctypes.c_void_p,)
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    name = 'Local\\wd-mind-owner-stop-' + uuid.uuid4().hex
    handle = kernel.CreateEventW(None, True, False, name)
    if not handle:
        raise OSError('synthetic event creation failed')
    try:
        state = {'name': name, 'handle': handle, 'kernel': kernel, 'start': _process_start(os.getpid())}
        yield state
    finally:
        kernel.SetEvent(handle)
        kernel.CloseHandle(handle)


def _bundle(tmp_path, control, *, bad_manifest=False, bad_policy=False, tamper_helper=False, tamper_host=False,
            parent_query_failure=False):
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    host = bundle / HOST.name
    helper = bundle / HELPER.name
    host.write_bytes(HOST.read_bytes())
    helper.write_bytes(HELPER.read_bytes())
    if parent_query_failure:
        # Fault only a synthetic host copy; the actual parent-query function's
        # caller and its fail-closed handling remain unchanged.
        source = host.read_text(encoding='utf-8')
        original = "$rows = @(CimCmdlets\\Get-CimInstance -ClassName Win32_Process `\n" \
                   "        -Filter ('ProcessId = ' + [string]$PID) -Property ProcessId,ParentProcessId -ErrorAction Stop)"
        assert source.count(original) == 1
        host.write_text(source.replace(original, "throw 'synthetic_parent_query_failure'"), encoding='utf-8')
    policy = b'{"synthetic_only":true}'
    policy_path = tmp_path / 'synthetic_policy.json'
    policy_path.write_bytes(policy)
    policy_sha = _sha(policy)
    approval_sha = 'c' * 64
    descriptor = json.dumps({'schema': 'wd.mind-identifier-policy-approval.v1',
        'identifier_policy_sha256': policy_sha, 'approval_reference_sha256': approval_sha},
        sort_keys=True, separators=(',', ':')).encode()
    descriptor_path = bundle / 'wd-mind-identifier-policy-approval.json'
    descriptor_path.write_bytes(descriptor)
    marker = tmp_path / 'synthetic_child_started'
    child = bundle / 'tools-bootstrap/tools/wd_mind_runtime_child.py'
    child.parent.mkdir(parents=True)
    child.write_text('''import hashlib,json,os,sys,time\n'''
        '''policy=open(sys.argv[1],"rb").read()\n'''
        '''if hashlib.sha256(policy).hexdigest()!=sys.argv[2]:sys.exit(5)\n'''
        '''open(sys.argv[3],"w").write("started")\n'''
        '''def emit(state):\n'''
        ''' print(json.dumps({"schema":"wd.mind-local-owner-status.v1","state":state,"manifest_sha256":os.environ["WD_REBOOT_EXPECTED_MANIFEST_HASH"].lower(),"authority":"none","caller_authentication":"UNKNOWN","source_authentication":"UNKNOWN","index_refresh":"NOT_SCHEDULED"},sort_keys=True,separators=(",",":")),flush=True)\n'''
        '''emit("STARTING");time.sleep(0.1);emit("READY")\n'''
        '''if sys.stdin.buffer.read(1)!=b"":sys.exit(6)\n'''
        '''emit("STOPPED")\n''', encoding='utf-8')
    python_sha = _sha(PYTHON.read_bytes())
    wrapper = bundle / 'Invoke-WdBridgePython.ps1'
    wrapper.write_text('''param([Parameter(Position=0)][string]$Tool,[switch]$VerifyPackage,
    [Parameter(ValueFromRemainingArguments)][string[]]$Rest)
if ($env:SYNTHETIC_ENV_CANARY) { exit 9 }
if (-not $VerifyPackage -or $Tool -cne 'tools/wd_mind_runtime_child.py' -or
    $Rest.Count -ne 8 -or $Rest[7] -cne '--preowner-proof-v1' -or $Rest[0] -cne '--mind-local-candidate-owner' -or
    $Rest[1] -cne '--policy-path' -or $Rest[3] -cne '--policy-sha256' -or
    $Rest[5] -cne '--approval-reference-sha256') { exit 7 }
& ''' + _quote(PYTHON) + ' -I -S -B ' + _quote(child) + ' $Rest[2] $Rest[4] ' + _quote(marker) + '''
exit $LASTEXITCODE
''', encoding='utf-8')
    paths = (host, helper, descriptor_path, child, wrapper)
    files = {str(path.relative_to(bundle)).replace('\\', '/'): _sha(path.read_bytes()) for path in paths}
    manifest = json.dumps({'schema_version': 1, 'files': files}, separators=(',', ':')).encode()
    (bundle / 'deployment-manifest.json').write_bytes(manifest)
    if tamper_helper:
        helper.write_text('# modified after manifest', encoding='utf-8')
    if tamper_host:
        host.write_bytes(host.read_bytes() + b'\n# synthetic changed host\n')
    args = ['-Enabled', '-BundleRoot', str(bundle), '-ExpectedManifestHash', '0' * 64 if bad_manifest else _sha(manifest),
            '-OperatorPolicyPath', str(policy_path), '-OperatorPolicySha256', '0' * 64 if bad_policy else policy_sha,
            '-OperatorApprovalReferenceSha256', approval_sha, '-BridgePythonPath', str(PYTHON),
            '-BridgePythonSha256', python_sha, '-StopEventName', control['name'],
            '-ControllerPid', str(os.getpid()), '-ControllerStartUtc', control['start']]
    return host, marker, args


def _start(host, args):
    env = _environment()
    env['SYNTHETIC_ENV_CANARY'] = 'synthetic-canary-not-for-child'
    return subprocess.Popen([str(SHELL), '-NoProfile', '-NonInteractive', '-File', str(host), *args],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)


def _lines(process):
    received = queue.Queue()
    def read():
        for line in process.stdout:
            received.put(line)
        received.put(None)
    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    return received, thread


@pytest.mark.skipif(os.name != 'nt' or not SHELL.exists(), reason='Windows PowerShell required')
def test_disabled_is_inert_and_does_not_need_parameters():
    process = subprocess.Popen([str(SHELL), '-NoProfile', '-NonInteractive', '-File', str(HOST)],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_environment())
    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0
    assert json.loads(stdout)['state'] == 'DISABLED'
    assert stderr == b''


@pytest.mark.skipif(os.name != 'nt' or not SHELL.exists(), reason='Windows PowerShell required')
@pytest.mark.parametrize('fault', ['bad_manifest', 'bad_policy', 'tamper_helper', 'tamper_host', 'parent_query_failure'])
def test_preflight_refuses_without_starting_synthetic_child(tmp_path, control, fault):
    host, marker, args = _bundle(tmp_path, control, **{fault: True})
    process = _start(host, args)
    stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == 2
    assert json.loads(stdout.splitlines()[-1])['state'] == 'REFUSED'
    assert not marker.exists()
    assert b'synthetic_only' not in stdout + stderr
    assert b'synthetic-canary-not-for-child' not in stdout + stderr
    assert stderr == b''


@pytest.mark.skipif(os.name != 'nt' or not SHELL.exists(), reason='Windows PowerShell required')
def test_controller_identity_mismatch_refuses_before_child(tmp_path, control):
    host, marker, args = _bundle(tmp_path, control)
    args[-1] = '2000-01-01T00:00:00.0000000Z'
    process = _start(host, args)
    stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == 2
    assert json.loads(stdout.splitlines()[-1])['state'] == 'REFUSED'
    assert not marker.exists()
    assert stderr == b''


@pytest.mark.skipif(os.name != 'nt' or not SHELL.exists(), reason='Windows PowerShell required')
def test_other_live_process_cannot_impersonate_actual_controller(tmp_path, control):
    host, marker, args = _bundle(tmp_path, control)
    # This independently alive process has a real matching PID/start pair but
    # did not launch the host. It exits naturally; no process is terminated.
    other = subprocess.Popen([str(SHELL), '-NoProfile', '-NonInteractive', '-Command',
                              'Start-Sleep -Seconds 6'], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=_environment())
    try:
        args[-3] = str(other.pid)
        args[-1] = _process_start(other.pid)
        assert other.poll() is None
        process = _start(host, args)
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 2
        assert json.loads(stdout.splitlines()[-1])['state'] == 'REFUSED'
        assert not marker.exists()
        assert stderr == b''
    finally:
        other.wait(timeout=15)


@pytest.mark.skipif(os.name != 'nt' or not SHELL.exists(), reason='Windows PowerShell required')
@pytest.mark.parametrize('early_stop', [False, True], ids=['ready_then_stop', 'stop_during_start'])
def test_real_helper_owns_synthetic_child_until_cooperative_stop(tmp_path, control, early_stop):
    host, marker, args = _bundle(tmp_path, control)
    process = _start(host, args)
    incoming, thread = _lines(process)
    states = []
    target = 'STARTING' if early_stop else 'READY'
    while target not in states:
        raw = incoming.get(timeout=15)
        assert raw is not None
        record = json.loads(raw)
        assert record['authority'] == 'none'
        states.append(record['state'])
    assert control['kernel'].SetEvent(control['handle'])
    assert process.wait(timeout=15) == 0
    thread.join(2)
    while not incoming.empty():
        raw = incoming.get_nowait()
        if raw is not None:
            if not raw.startswith(b'{'):
                pytest.fail('synthetic host emitted a non-JSON status line')
            states.append(json.loads(raw)['state'])
    stderr = process.stderr.read()
    assert marker.exists()
    assert states[-1] == 'STOPPED'
    assert 'REFUSED' not in states
    assert b'synthetic_only' not in stderr
    assert b'synthetic-canary-not-for-child' not in stderr
    assert stderr == b''


@pytest.mark.skipif(os.name != 'nt' or not SHELL.exists(), reason='Windows PowerShell required')
def test_verified_scriptblock_host_runs_without_a_path_reopen(tmp_path, control):
    host, marker, args = _bundle(tmp_path, control)
    parts = ['-Enabled']
    for index in range(1, len(args), 2):
        parts.extend([args[index], _quote(args[index + 1])])
    bootstrap = "$s=[Console]::In.ReadToEnd(); . ([scriptblock]::Create($s)) " + ' '.join(parts) + ' -SourceBoundScriptBlock'
    encoded = base64.b64encode(bootstrap.encode('utf-16le')).decode('ascii')
    process = subprocess.Popen([str(SHELL), '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_environment())
    process.stdin.write(host.read_bytes())
    process.stdin.close()
    incoming, thread = _lines(process)
    states = []
    while 'READY' not in states:
        raw = incoming.get(timeout=15)
        assert raw is not None
        states.append(json.loads(raw)['state'])
    assert control['kernel'].SetEvent(control['handle'])
    assert process.wait(timeout=15) == 0
    thread.join(2)
    assert marker.exists()
    assert process.stderr.read() == b''
