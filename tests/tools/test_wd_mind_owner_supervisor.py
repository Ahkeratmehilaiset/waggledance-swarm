"""Synthetic supervisor process checks; no installed policy or owner."""
import ctypes
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

import pytest
import test_wd_mind_owner_host as host_test


ROOT = Path(__file__).resolve().parents[2]
SUPERVISOR = ROOT / 'ops/windows/reboot/Start-WdMindOwnerSupervisor.ps1'
SHELL = host_test.SHELL
control = host_test.control


@pytest.fixture
def stop_event():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateEventW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p)
    kernel.CreateEventW.restype = ctypes.c_void_p
    kernel.SetEvent.argtypes = (ctypes.c_void_p,)
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    name = 'Local\\wd-mind-supervisor-stop-' + uuid.uuid4().hex
    handle = kernel.CreateEventW(None, True, False, name)
    if not handle: raise OSError('synthetic event failed')
    try:
        yield name, handle, kernel
    finally:
        kernel.SetEvent(handle)
        kernel.CloseHandle(handle)


def _bundle(tmp_path, control):
    host, marker, args = host_test._bundle(tmp_path, control)
    options = dict(zip(args[1::2], args[2::2]))
    root = host.parent
    manifest_path = root / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    config = {'schema':'wd.mind-startup-config.v1', 'enabled':True, 'authority':'none',
        'identifier_policy_path':options['-OperatorPolicyPath'],
        'identifier_policy_sha256':options['-OperatorPolicySha256'],
        'approval_reference_sha256':options['-OperatorApprovalReferenceSha256']}
    data = json.dumps(config, separators=(',', ':')).encode('ascii')
    (root / 'wd-mind-startup-config.json').write_bytes(data)
    manifest['files']['wd-mind-startup-config.json'] = hashlib.sha256(data).hexdigest().upper()
    manifest['bridge_code_package'] = {'python_executable':options['-BridgePythonPath'],
        'python_executable_sha256':options['-BridgePythonSha256']}
    raw = json.dumps(manifest, separators=(',', ':')).encode('ascii')
    manifest_path.write_bytes(raw)
    return root, marker, hashlib.sha256(raw).hexdigest()


def _start(root, pin, event):
    return subprocess.Popen([str(SHELL), '-NoProfile', '-NonInteractive', '-File', str(SUPERVISOR),
        '-Enabled', '-BundleRoot', str(root), '-ExpectedManifestHash', pin, '-StopEventName', event],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=host_test._environment())


@pytest.mark.skipif(os.name != 'nt', reason='Windows supervisor lifecycle')
def test_disabled_supervisor_is_inert():
    proc = subprocess.run([str(SHELL), '-NoProfile', '-NonInteractive', '-File', str(SUPERVISOR)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=15, env=host_test._environment())
    assert proc.returncode == 0
    assert json.loads(proc.stdout)['state'] == 'DISABLED'
    assert proc.stderr == b''


@pytest.mark.skipif(os.name != 'nt', reason='Windows supervisor status parser')
@pytest.mark.parametrize('fault', ['valid', 'pending_hold', 'after_ready', 'duplicate', 'forged', 'crlf', 'truncated'])
def test_supervisor_accepts_only_exact_preowner_frame_before_ready(fault):
    frame = ('{"schema":"wd.mind-owner-host-status.v1","state":"PRE_OWNER_REFUSED",'
             '"authority":"none","caller_authentication":"UNKNOWN","index_refresh":"NOT_SCHEDULED"}')
    if fault == 'forged': frame = frame.replace('UNKNOWN', 'VERIFIED', 1)
    if fault == 'crlf': frame += '\r'
    if fault == 'truncated': frame = frame[:-1]
    raw = base64.b64encode(frame.encode('ascii')).decode('ascii')
    path = str(SUPERVISOR).replace("'", "''")
    previous = 'READY' if fault == 'after_ready' else 'HOLD' if fault == 'pending_hold' else 'STARTING'
    script = f"""
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$tree=[Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$tokens,[ref]$errors)
$fn=@($tree.FindAll({{param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'Get-WdSupervisorHostFrame'}},$true))
if($fn.Count -ne 1){{exit 3}}
. ([scriptblock]::Create($fn[0].Extent.Text))
$line=[Text.Encoding]::ASCII.GetString([Convert]::FromBase64String('{raw}'))
try {{ Get-WdSupervisorHostFrame $line '{previous}' ${str(fault == 'after_ready').lower()} ${str(fault == 'duplicate').lower()} }} catch {{ 'REFUSED' }}
"""
    encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
    process = subprocess.run([str(SHELL), '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=15, env=host_test._environment())
    assert process.returncode == 0 and process.stderr == b''
    result = process.stdout.decode('ascii').strip()
    assert result == ('PRE_OWNER_REFUSED' if fault in ('valid', 'pending_hold') else 'REFUSED')


@pytest.mark.skipif(os.name != 'nt', reason='Windows supervisor lifecycle')
def test_supervisor_retains_actual_host_and_cooperatively_stops(tmp_path, control, stop_event):
    root, marker, pin = _bundle(tmp_path, control)
    name, handle, kernel = stop_event
    process = _start(root, pin, name)
    incoming, reader = host_test._lines(process)
    states = []
    while 'READY' not in states:
        raw = incoming.get(timeout=20)
        assert raw is not None
        states.append(json.loads(raw)['state'])
        assert states[-1] not in ('REFUSED', 'HOLD')
    assert marker.exists()
    assert kernel.SetEvent(handle)
    assert process.wait(timeout=20) == 0
    reader.join(timeout=3)
    while not incoming.empty():
        raw = incoming.get_nowait()
        if raw is not None: states.append(json.loads(raw)['state'])
    assert states[-1] == 'STOPPED'
    assert process.stderr.read() == b''


@pytest.mark.skipif(os.name != 'nt', reason='Windows supervisor pin preflight')
@pytest.mark.parametrize('fault', ['manifest', 'manifest_pin', 'host', 'config'])
def test_supervisor_rejects_tampered_inputs_before_spawning(tmp_path, control, stop_event, fault):
    root, marker, pin = _bundle(tmp_path, control)
    if fault == 'manifest': (root / 'deployment-manifest.json').write_bytes(b'{}')
    if fault == 'manifest_pin': pin = '0' * 64
    if fault == 'host': (root / 'Start-WdMindOwnerHost.ps1').write_bytes(b'# changed')
    if fault == 'config': (root / 'wd-mind-startup-config.json').write_bytes(b'{}')
    name, _, _ = stop_event
    process = _start(root, pin, name)
    stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == 2 and stderr == b''
    assert json.loads(stdout.splitlines()[-1])['state'] == 'REFUSED'
    assert not marker.exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows supervisor lifecycle')
def test_supervisor_reports_start_without_waiting_for_slow_child(tmp_path, control, stop_event):
    root, _, _ = _bundle(tmp_path, control)
    release = tmp_path / 'synthetic-release'
    waiting = tmp_path / 'synthetic-waiting'
    wrapper = root / 'Invoke-WdBridgePython.ps1'
    source = wrapper.read_text(encoding='utf-8')
    needle = 'if ($env:SYNTHETIC_ENV_CANARY)'
    assert source.count(needle) == 1
    wait = '[IO.File]::WriteAllText(' + host_test._quote(waiting) + ",'waiting')\n" + 'while (-not (Test-Path -LiteralPath ' + host_test._quote(release) + ')) { Start-Sleep -Milliseconds 25 }\n'
    wrapper.write_text(source.replace(needle, wait + needle), encoding='utf-8')
    manifest_path = root / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['files']['Invoke-WdBridgePython.ps1'] = hashlib.sha256(wrapper.read_bytes()).hexdigest().upper()
    raw = json.dumps(manifest, separators=(',', ':')).encode('ascii')
    manifest_path.write_bytes(raw)
    name, handle, kernel = stop_event
    process = _start(root, hashlib.sha256(raw).hexdigest(), name)
    incoming, thread = host_test._lines(process)
    try:
        first = incoming.get(timeout=15)
        assert first is not None and json.loads(first)['state'] == 'STARTING'
        assert not release.exists()
        deadline = time.monotonic() + 15
        while not waiting.exists() and time.monotonic() < deadline:
            time.sleep(0.025)
        assert waiting.exists()
        assert kernel.SetEvent(handle)
    finally:
        release.write_bytes(b'go')
    assert process.wait(timeout=20) == 0
    thread.join(timeout=3)
    assert process.stderr.read() == b''


@pytest.mark.skipif(os.name != 'nt', reason='Windows supervisor lifecycle')
def test_hold_is_retained_without_retry_until_cooperative_stop(tmp_path, control, stop_event):
    root, marker, _ = _bundle(tmp_path, control)
    host = root / 'Start-WdMindOwnerHost.ps1'
    fake = """param([switch]$Enabled,[switch]$SourceBoundScriptBlock,[string]$BundleRoot,
 [string]$ExpectedManifestHash,[string]$OperatorPolicyPath,[string]$OperatorPolicySha256,
 [string]$OperatorApprovalReferenceSha256,[string]$BridgePythonPath,[string]$BridgePythonSha256,
 [string]$StopEventName,[int]$ControllerPid,[string]$ControllerStartUtc)
$out=[Console]::OpenStandardOutput()
function Emit([string]$state) {
 $line='{"schema":"wd.mind-owner-host-status.v1","state":"'+$state+'","authority":"none","caller_authentication":"UNKNOWN","index_refresh":"NOT_SCHEDULED"}' + "`n"
 $data=[Text.Encoding]::ASCII.GetBytes($line);$out.Write($data,0,$data.Length);$out.Flush()
}
[IO.File]::AppendAllText(MARKER,'1');$event=[Threading.EventWaitHandle]::OpenExisting($StopEventName)
Emit 'HOLD';if(-not $event.WaitOne(20000)){exit 7};Emit 'STOPPED';$event.Dispose();exit 0
""".replace('MARKER', host_test._quote(marker))
    host.write_text(fake, encoding='utf-8')
    manifest_path = root / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['files'][host.name] = hashlib.sha256(host.read_bytes()).hexdigest().upper()
    raw = json.dumps(manifest, separators=(',', ':')).encode('ascii')
    manifest_path.write_bytes(raw)
    name, handle, kernel = stop_event
    process = _start(root, hashlib.sha256(raw).hexdigest(), name)
    incoming, thread = host_test._lines(process)
    while True:
        value = incoming.get(timeout=15)
        assert value is not None
        state = json.loads(value)['state']
        assert state != 'REFUSED'
        if state == 'HOLD': break
    assert process.poll() is None and marker.read_bytes() == b'1'
    assert kernel.SetEvent(handle)
    assert process.wait(timeout=15) == 0
    thread.join(timeout=3)
    assert marker.read_bytes() == b'1' and process.stderr.read() == b''


def _reservation_bundle(tmp_path, control, initial=b'C'):
    root, marker, pin = _bundle(tmp_path, control)
    reservation_root = tmp_path / 'synthetic-reservation'
    reservation_root.mkdir()
    reservation = reservation_root / 'mind-owner-reservation.v1'
    reservation.write_bytes(b'WD_MIND_OWNER_V1\n' + initial)
    owner_script = root / 'tools-bootstrap/tools/wd_mind_runtime_child.py'
    script = '''import json,os,sys\nfrom pathlib import Path\nsys.path.insert(0, {repo!r})\n'''.format(repo=str(ROOT))
    script += '''from tools import wd_mind_runtime_child as child\n'''
    script += '''child._AUDIT_ROOT=Path({reservation!r})\n'''.format(reservation=str(reservation_root))
    script += '''class Admission:\n def __init__(self, guard, evidence): self.guard=guard\n def quiescent(self): return True\n def release_clean(self): self.guard.release_clean(service_stopped=True,owner_stopped=True,owner_owned=False,control_stopped=True)\n def abandon(self): self.guard.abandon()\n'''
    script += '''child._LocalAdmission=Admission\n'''
    script += '''def entry(*,output_stream,expected_manifest_sha256,_cleanup_evidence,_admission,**kwargs):\n'''
    script += ''' with open({marker!r},'ab') as f: f.write(b'1')\n'''.format(marker=str(marker))
    script += ''' def emit(state):\n  value=dict(schema='wd.mind-local-owner-status.v1',state=state,manifest_sha256=expected_manifest_sha256,authority='none',caller_authentication='UNKNOWN',source_authentication='UNKNOWN',index_refresh='NOT_SCHEDULED')\n  output_stream.write(json.dumps(value,sort_keys=True,separators=(',',':')).encode()+b'\\n');output_stream.flush()\n'''
    script += ''' emit('STARTING');emit('READY');sys.stdin.buffer.read();emit('STOPPED');_cleanup_evidence['clean']=True;return 0\n'''
    script += '''child._local_candidate_owner_entry=entry\n'''
    script += '''sys.exit(child._guarded_local_entry(entry,output_stream=sys.stdout.buffer,expected_manifest_sha256=os.environ['WD_REBOOT_EXPECTED_MANIFEST_HASH'].lower(),_include_preowner_proof=True))\n'''
    owner_script.write_text(script, encoding='utf-8')
    manifest_path = root / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['files']['tools-bootstrap/tools/wd_mind_runtime_child.py'] = hashlib.sha256(owner_script.read_bytes()).hexdigest().upper()
    raw = json.dumps(manifest, separators=(',', ':')).encode('ascii')
    manifest_path.write_bytes(raw)
    pin = hashlib.sha256(raw).hexdigest()
    return root, marker, pin, reservation


@pytest.mark.skipif(os.name != 'nt', reason='Windows reservation and supervisor lifecycle')
def test_concurrent_supervisors_admit_only_one_synthetic_owner(tmp_path, control, stop_event):
    root, marker, pin, reservation = _reservation_bundle(tmp_path, control)
    name, handle, kernel = stop_event
    processes = [_start(root, pin, name) for _ in range(2)]
    readers = [host_test._lines(process) for process in processes]
    outcomes = []
    for incoming, _ in readers:
        while True:
            value = incoming.get(timeout=30)
            assert value is not None
            state = json.loads(value)['state']
            assert state != 'REFUSED'
            if state in ('READY', 'PRE_OWNER_REFUSED'):
                outcomes.append(state)
                break
    assert sorted(outcomes) == ['PRE_OWNER_REFUSED', 'READY']
    loser = outcomes.index('PRE_OWNER_REFUSED')
    assert processes[loser].wait(timeout=15) == 2
    assert marker.read_bytes() == b'1'
    with pytest.raises(PermissionError):
        reservation.read_bytes()  # The winner retains the real Windows byte lock.
    assert kernel.SetEvent(handle)
    assert processes[1-loser].wait(timeout=20) == 0
    assert reservation.read_bytes() == b'WD_MIND_OWNER_V1\nC'
    for process, (_, thread) in zip(processes, readers):
        thread.join(timeout=3)
        assert process.stderr.read() == b''


@pytest.mark.skipif(os.name != 'nt', reason='Windows reservation and supervisor lifecycle')
def test_reserved_marker_refuses_without_cleanup_or_retry(tmp_path, control, stop_event):
    root, marker, pin, reservation = _reservation_bundle(tmp_path, control)
    # Synthetic process exits itself without cleanup, modelling a lost owner;
    # no running process is terminated and the reservation is not live state.
    script = ("import os,sys; from pathlib import Path; sys.path.insert(0, " + repr(str(ROOT)) + "); "
              "from tools import wd_mind_runtime_child as c; "
              "guard=c._reserve_local_owner(Path(" + repr(str(reservation.parent)) + ")); os._exit(0)")
    abrupt = subprocess.run([str(host_test.PYTHON), '-I', '-S', '-B', '-c', script],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=15, env=host_test._environment())
    assert abrupt.returncode == 0 and abrupt.stdout == b'' and abrupt.stderr == b''
    assert reservation.read_bytes() == b'WD_MIND_OWNER_V1\nR'
    name, _, _ = stop_event
    process = _start(root, pin, name)
    stdout, stderr = process.communicate(timeout=20)
    assert process.returncode == 2 and stderr == b''
    states = [json.loads(line)['state'] for line in stdout.splitlines()]
    assert states[-1] == 'PRE_OWNER_REFUSED'
    assert reservation.read_bytes() == b'WD_MIND_OWNER_V1\nR'
    assert not marker.exists()
