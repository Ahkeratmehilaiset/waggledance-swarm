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
    manifest['source_commit'] = 'a' * 40
    automation = dict(schema='wd.mind-owner-automation.v1', enabled=True, agent='codex-lead-1',
                      source_commit=manifest['source_commit'], startup_config_sha256=hashlib.sha256(data).hexdigest())
    automation_bytes = json.dumps(automation, separators=(',', ':')).encode('ascii')
    (root / 'wd-mind-owner-automation.json').write_bytes(automation_bytes)
    manifest['files']['wd-mind-owner-automation.json'] = hashlib.sha256(automation_bytes).hexdigest().upper()
    manifest['bridge_code_package'] = {'python_executable':options['-BridgePythonPath'],
        'python_executable_sha256':options['-BridgePythonSha256']}
    raw = json.dumps(manifest, separators=(',', ':')).encode('ascii')
    manifest_path.write_bytes(raw)
    return root, marker, hashlib.sha256(raw).hexdigest()


def _start(root, pin, event, controller_pid=None, controller_start=None, script=SUPERVISOR):
    if controller_pid is None: controller_pid = os.getpid()
    if controller_start is None: controller_start = host_test._process_start(controller_pid)
    return subprocess.Popen([str(SHELL), '-NoProfile', '-NonInteractive', '-File', str(script),
        '-Enabled', '-BundleRoot', str(root), '-ExpectedManifestHash', pin, '-StopEventName', event,
        '-ControllerPid', str(controller_pid), '-ControllerStartUtc', controller_start],
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


@pytest.mark.skipif(os.name != 'nt', reason='Windows supervisor spawn preflight')
def test_supervisor_refuses_missing_executable_before_process_is_created(tmp_path, control, stop_event):
    root, marker, pin = _bundle(tmp_path, control)
    source = SUPERVISOR.read_text(encoding='utf-8')
    original = "$info.FileName=Join-Path $PSHOME $(if($PSVersionTable.PSEdition -ceq 'Core'){'pwsh.exe'}else{'powershell.exe'})"
    assert source.count(original) == 1
    unavailable = root / 'synthetic-missing-executable.exe'
    assert not unavailable.exists()
    source = source.replace(original, '$info.FileName=' + host_test._quote(unavailable))
    script = tmp_path / 'synthetic-supervisor-missing-executable.ps1'
    script.write_text(source, encoding='utf-8')
    name, _, _ = stop_event
    process = _start(root, pin, name, script=script)
    stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == 2 and stderr == b''
    assert json.loads(stdout.splitlines()[-1])['state'] == 'REFUSED'
    assert not marker.exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows supervisor spawn uncertainty')
def test_supervisor_keeps_ambiguous_spawn_failure_in_hold(tmp_path, control, stop_event):
    root, marker, pin = _bundle(tmp_path, control)
    source = SUPERVISOR.read_text(encoding='utf-8')
    spawn = "if (-not $child.Start()) {throw 'spawn'}"
    hold_tick = 'Flush-WdSupervisorState; [Threading.Thread]::Sleep(100)'
    assert source.count(spawn) == 1 and source.count(hold_tick) == 1
    # Inject failure at the call site and stop the synthetic test after its
    # first HOLD. No host process is launched by this instrumented copy.
    source = source.replace(spawn, "throw 'synthetic_uncertain_spawn'")
    source = source.replace(hold_tick, 'Flush-WdSupervisorState; Finish-WdSupervisorOutput; exit 0')
    script = tmp_path / 'synthetic-supervisor-uncertain-spawn.ps1'
    script.write_text(source, encoding='utf-8')
    name, _, _ = stop_event
    process = _start(root, pin, name, script=script)
    stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == 0 and stderr == b''
    assert json.loads(stdout.splitlines()[-1])['state'] == 'HOLD'
    assert not marker.exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows supervisor parent identity')
@pytest.mark.parametrize('fault', ['wrong_start', 'wrong_live_parent', 'parent_query_error'])
def test_supervisor_refuses_unverified_actual_controller_before_spawn(tmp_path, control, stop_event, fault):
    root, marker, pin = _bundle(tmp_path, control)
    name, _, _ = stop_event
    controller_pid = os.getppid() if fault == 'wrong_live_parent' else os.getpid()
    controller_start = ('2000-01-01T00:00:00.0000000Z' if fault == 'wrong_start'
                        else host_test._process_start(controller_pid))
    script = SUPERVISOR
    if fault == 'parent_query_error':
        source = SUPERVISOR.read_text(encoding='utf-8')
        begin = source.index('function Get-WdSupervisorParentPid {')
        end = source.index('\n}', begin) + 2
        source = source[:begin] + "function Get-WdSupervisorParentPid { throw 'synthetic_query_error' }" + source[end:]
        script = tmp_path / 'synthetic-supervisor-query-error.ps1'
        script.write_text(source, encoding='utf-8')
    process = _start(root, pin, name, controller_pid, controller_start, script)
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


@pytest.mark.skipif(os.name != 'nt', reason='Windows supervisor parent lifetime')
@pytest.mark.parametrize('during_hold', [False, True], ids=['ready', 'hold'])
def test_controller_exit_cooperatively_stops_the_supervisor_child(tmp_path, control, stop_event, during_hold):
    root, marker, _ = _bundle(tmp_path, control)
    host = root / 'Start-WdMindOwnerHost.ps1'
    release = tmp_path / 'synthetic-hold-release'
    fake = """param([switch]$Enabled,[switch]$SourceBoundScriptBlock,[string]$BundleRoot,
 [string]$ExpectedManifestHash,[string]$OperatorPolicyPath,[string]$OperatorPolicySha256,
 [string]$OperatorApprovalReferenceSha256,[string]$BridgePythonPath,[string]$BridgePythonSha256,
 [string]$StopEventName,[int]$ControllerPid,[string]$ControllerStartUtc)
$out=[Console]::OpenStandardOutput()
function Emit([string]$state) {
 $line='{"schema":"wd.mind-owner-host-status.v1","state":"'+$state+'","authority":"none","caller_authentication":"UNKNOWN","index_refresh":"NOT_SCHEDULED"}' + "`n"
 $data=[Text.Encoding]::ASCII.GetBytes($line);$out.Write($data,0,$data.Length);$out.Flush()
}
[IO.File]::WriteAllText(MARKER,'STARTED');$event=[Threading.EventWaitHandle]::OpenExisting($StopEventName)
Emit 'INITIAL_STATE';if(-not $event.WaitOne(25000)){exit 7}
[IO.File]::AppendAllText(MARKER,'_STOP_REQUESTED')
if (DELAYED) { $until=[DateTime]::UtcNow.AddSeconds(20); while (-not (Test-Path -LiteralPath RELEASE) -and [DateTime]::UtcNow -lt $until) {Start-Sleep -Milliseconds 25}; if (-not (Test-Path -LiteralPath RELEASE)) {exit 8} }
[IO.File]::AppendAllText(MARKER,'_STOPPED');Emit 'STOPPED';$event.Dispose();exit 0
""".replace('MARKER', host_test._quote(marker)).replace('INITIAL_STATE', 'HOLD' if during_hold else 'READY')
    fake = fake.replace('DELAYED', '$true' if during_hold else '$false').replace('RELEASE', host_test._quote(release))
    host.write_text(fake, encoding='utf-8')
    manifest_path = root / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['files'][host.name] = hashlib.sha256(host.read_bytes()).hexdigest().upper()
    raw = json.dumps(manifest, separators=(',', ':')).encode('ascii')
    manifest_path.write_bytes(raw)
    pin = hashlib.sha256(raw).hexdigest()
    name, _, _ = stop_event
    controller_code = '''import json,os,subprocess,sys\n'''
    controller_code += '''names=('SYSTEMROOT','WINDIR','SYSTEMDRIVE','PATH','PATHEXT','TEMP','TMP','USERPROFILE','LOCALAPPDATA','APPDATA')\n'''
    controller_code += '''env={k:os.environ[k] for k in names if k in os.environ}\n'''
    controller_code += '''shell={shell!r}\n'''.format(shell=str(SHELL))
    controller_code += '''q='(Get-Process -Id '+str(os.getpid())+\").StartTime.ToUniversalTime().ToString('o')\"\n'''
    controller_code += '''start=subprocess.check_output([shell,'-NoProfile','-NonInteractive','-Command',q],env=env).decode('ascii').strip()\n'''
    controller_code += '''args=[shell,'-NoProfile','-NonInteractive','-File',{supervisor!r},'-Enabled','-BundleRoot',{root!r},'-ExpectedManifestHash',{pin!r},'-StopEventName',{event!r},'-ControllerPid',str(os.getpid()),'-ControllerStartUtc',start]\n'''.format(supervisor=str(SUPERVISOR),root=str(root),pin=pin,event=name)
    controller_code += '''child=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env)\n'''
    target = 'HOLD' if during_hold else 'READY'
    controller_code += '''for raw in child.stdout:\n state=json.loads(raw)['state']\n if state=={target!r}: print(json.dumps({{'supervisor_pid':child.pid}}),flush=True);break\n if state=='REFUSED' or (state=='HOLD' and {target!r}!='HOLD'): sys.exit(8)\nelse: sys.exit(9)\n'''.format(target=target)
    controller_code += '''sys.stdin.buffer.read(1)\n'''
    controller_script = tmp_path / 'synthetic-controller.py'
    controller_script.write_text(controller_code, encoding='utf-8')
    controller = subprocess.Popen([str(host_test.PYTHON), '-I', '-S', '-B', str(controller_script)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=host_test._environment())
    incoming, thread = host_test._lines(controller)
    handle = None
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    try:
        ready = incoming.get(timeout=20)
        assert ready is not None
        supervisor_pid = json.loads(ready)['supervisor_pid']
        handle = kernel.OpenProcess(0x00100000, False, supervisor_pid)
        assert handle
        assert marker.read_text() == 'STARTED'
    finally:
        try: controller.stdin.write(b'x'); controller.stdin.flush()
        except (BrokenPipeError, OSError): pass
        controller.stdin.close()
    assert controller.wait(timeout=15) == 0
    thread.join(timeout=3)
    try:
        if during_hold:
            deadline = time.monotonic() + 15
            while '_STOP_REQUESTED' not in marker.read_text() and time.monotonic() < deadline:
                time.sleep(0.025)
            assert marker.read_text() == 'STARTED_STOP_REQUESTED'
            assert kernel.WaitForSingleObject(handle, 0) == 258  # HOLD remains alive.
            release.write_bytes(b'go')
        assert kernel.WaitForSingleObject(handle, 20000) == 0
        assert marker.read_text() == 'STARTED_STOP_REQUESTED_STOPPED'
    finally:
        release.write_bytes(b'go')
        kernel.CloseHandle(handle)
    assert controller.stderr.read() == b''


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

@pytest.mark.skipif(os.name != 'nt', reason='Windows pinned owner automation')
@pytest.mark.parametrize('fault', ['missing', 'tampered', 'disabled', 'wrong_agent', 'wrong_commit',
                                   'wrong_config', 'noncanonical', 'config_tampered'])
def test_supervisor_refuses_invalid_or_absent_automation_before_host(tmp_path, control, stop_event, fault):
    root, marker, _ = _bundle(tmp_path, control)
    path = root / 'wd-mind-owner-automation.json'
    manifest_path = root / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    value = json.loads(path.read_bytes())
    if fault == 'missing':
        del manifest['files'][path.name]
    elif fault == 'tampered':
        path.write_bytes(b'{}')
    elif fault == 'config_tampered':
        (root / 'wd-mind-startup-config.json').write_bytes(b'{}')
    else:
        if fault == 'disabled': value['enabled'] = False
        if fault == 'wrong_agent': value['agent'] = 'fable-5'
        if fault == 'wrong_commit': value['source_commit'] = 'b'*40
        if fault == 'wrong_config': value['startup_config_sha256'] = '0'*64
        raw = json.dumps(value, separators=(', ', ': ') if fault == 'noncanonical' else (',', ':')).encode()
        path.write_bytes(raw)
        manifest['files'][path.name] = hashlib.sha256(raw).hexdigest().upper()
    raw_manifest = json.dumps(manifest, separators=(',', ':')).encode()
    manifest_path.write_bytes(raw_manifest)
    pin = hashlib.sha256(raw_manifest).hexdigest()
    process = _start(root, pin, stop_event[0])
    stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == 2 and not marker.exists() and not stderr
    assert json.loads(stdout.decode().strip())['state'] == 'REFUSED'
