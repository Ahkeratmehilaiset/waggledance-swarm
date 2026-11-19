"""Status-controller tests use only synthetic C: files and no live bridge."""
import base64
import ctypes
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / 'ops/windows/reboot/Start-WdMindStatusController.ps1'
HELPER = ROOT / 'ops/windows/reboot/Invoke-WdMindStartupRead.ps1'
SHELL = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
LANE = 'codex-lead-1'
pytestmark = pytest.mark.skipif(os.name != 'nt' or not SHELL.exists(), reason='Windows PowerShell required')


def env():
    keys = ('SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'PATH', 'PATHEXT', 'TEMP', 'TMP', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA')
    return {key: os.environ[key] for key in keys if key in os.environ}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def psquote(value):
    return "'" + str(value).replace("'", "''") + "'"


def bundle(tmp_path, *, hold=False, bad_view=False, future=False, stale=False, real_reader=False, nested_offset=None, nested_future_source=False, snapshot_offset=0, source_age_seconds=0, nested_lanes=None, nested_source_delta=0, nested_bad_lane_age=False, nested_source_max_age=None):
    root = tmp_path / 'bundle'
    root.mkdir()
    bridge_root = tmp_path / '.agent-bridge'
    (bridge_root / 'shared').mkdir(parents=True)
    (bridge_root / 'shared/events.jsonl').write_bytes(b'')
    lane_names = ('codex-lead-1', 'codex-tools-1', 'claude-rco-1', 'claude-rco-2', 'fable-5')
    identities = {name: str(uuid.UUID(int=index+1)) for index, name in enumerate(lane_names)}
    fleet = {'runtime_root': str(bridge_root),
             'lanes': [{'agent': a, 'agent_uuid': identities[a]} for a in lane_names if a != 'codex-tools-1'],
             'tools_supervisor': {'agent': 'codex-tools-1', 'agent_uuid': identities['codex-tools-1']}}
    fleet_bytes = json.dumps(fleet, separators=(',', ':')).encode()
    (root / 'wd-fleet.json').write_bytes(fleet_bytes)
    (root / 'tools-bootstrap/tools').mkdir(parents=True)
    (root / 'tools-bootstrap/tools/wd_bridge_mind_session.py').write_text('# synthetic\n')
    (root / 'Invoke-WdMindStartupRead.ps1').write_bytes(HELPER.read_bytes())
    audit_root = tmp_path / '.codex-audit'
    audit_root.mkdir()
    latest = audit_root / 'mind-live/latest.json'
    config = "function Get-WdMindReadConfig { param($FleetPath,$FleetSha256) return @{WD_MIND_READ_ENABLED='1';WD_MIND_FLEET=$FleetPath;WD_MIND_FLEET_SHA256=$FleetSha256;WD_MIND_LATEST=" + psquote(latest) + ";WD_MIND_AUDIT_ROOT=" + psquote(audit_root) + "} }"
    (root / 'Get-WdMindReadConfig.ps1').write_text(config, encoding='utf-8')
    snapshot_time = datetime.now(timezone.utc) + timedelta(days=1 if future else (-1 if stale else 0), seconds=snapshot_offset)
    timestamp = snapshot_time.strftime('%Y-%m-%dT%H:%M:%SZ')
    observed_time = (snapshot_time - timedelta(seconds=source_age_seconds)).strftime('%Y-%m-%dT%H:%M:%SZ')
    view = {'schema':'wd.mind-direct-status.v1', 'authority':'none', 'scope':'bounded_bridge_status_not_gate',
            'lane': 'fable-5' if bad_view else LANE, 'fleet_sha256':sha(fleet_bytes), 'coverage':'bounded_tail',
            'generation':'UNKNOWN', 'continuity':'UNKNOWN', 'sender_authentication':'UNKNOWN',
            'as_of_utc':timestamp, 'observation_status':'OBSERVED',
            'facts':[{'agent':LANE, 'event_type':'liveness', 'observed_utc':observed_time,
                      'event_ref':'a'*64, 'age_seconds':source_age_seconds}]}
    if nested_offset is not None:
        outer_dt = datetime.now(timezone.utc) + timedelta(seconds=nested_offset)
        source_dt = outer_dt + timedelta(seconds=nested_source_delta)
        outer_time = outer_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
        source_time = source_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
        identity = 'windows-v1:00000001:0000000000000001'
        lane_rows = {lane:{'observed_in_window':False, 'status':'not_seen_in_window'} for lane in lane_names}
        for index, (lane, age) in enumerate((nested_lanes or {}).items()):
            lane_rows[lane] = {'observed_in_window':True, 'identity_verification':'roster_bound',
                               'last_event_utc':(source_dt-timedelta(seconds=age)).strftime('%Y-%m-%dT%H:%M:%SZ'),
                               'observation_age_seconds':max(age, 0) + (10 if nested_bad_lane_age and index == 0 else 0), 'claims':[]}
        view = {'schema':'wd.bridge-mind-lane-read.v1', 'authority':'none',
                'scope':'read_only_lane_view_not_gate', 'lane':LANE, 'as_of_utc':outer_time,
                'binding':{'file_identity':identity, 'generation':'g1', 'origin':'lane_reader_view_not_authenticated'},
                'pointer':{'cycle_dir':'20261112T180000Z', 'cycle_generation':1},
                'freshness':{'age_seconds':1, 'max_age_seconds':900, 'status':'fresh'},
                'identity':{'roster':'caller_declared', 'bound_lanes':sorted(lane_names)},
                'source':{'log_identity':'capture-window:'+identity, 'scope':'bounded_window_of_bridge_log_not_whole_log',
                          'generation':'g1', 'as_of_utc':source_time,
                          'max_event_utc':(datetime.now(timezone.utc)+timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ') if nested_future_source else ((source_dt-timedelta(seconds=nested_source_max_age)).strftime('%Y-%m-%dT%H:%M:%SZ') if nested_source_max_age is not None else 'UNKNOWN'),
                          'event_count':0, 'unique_event_count':0, 'duplicate_count':0,
                          'window':{'cursor_start':0, 'cursor_end':0, 'coverage':'complete_over_window_only'}, 'blocked':None},
                'lanes':lane_rows,
                'prs':{}, 'refusal':None, 'fleet_sha256':sha(fleet_bytes),
                'source_event_refs':['a'*64], 'status_observation':view}
    payload = base64.b64encode(json.dumps(view, separators=(',', ':')).encode()).decode()
    release = tmp_path / 'release'
    started = tmp_path / 'started'
    gate = ''
    if hold:
        gate = ('[IO.File]::WriteAllText('+psquote(started)+",'ready'); while (-not (Test-Path -LiteralPath "
                +psquote(release)+')) { Start-Sleep -Milliseconds 30 }; ')
    guard = ("if ($env:AGENT_BRIDGE_RUNTIME_ROOT -cne " + psquote(bridge_root) +
             " -or $env:AGENT_BRIDGE_AGENT_UUID -cne " + psquote(identities[LANE]) +
             " -or $env:AGENT_BRIDGE_OWNER_TOKEN) { exit 2 }; ")
    if real_reader:
        wrapper = guard + "$env:PYTHONPATH=" + psquote(ROOT) + "; & " + psquote(sys.executable) + " -m tools.wd_bridge_mind_session @($args | Select-Object -Skip 2); exit $LASTEXITCODE"
    else:
        wrapper = guard + gate + "[Console]::Out.Write([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + payload + "'))); exit 0"
    (root / 'Invoke-WdBridgePython.ps1').write_text(wrapper, encoding='utf-8')
    files = ('wd-fleet.json', 'Get-WdMindReadConfig.ps1', 'Invoke-WdBridgePython.ps1',
             'tools-bootstrap/tools/wd_bridge_mind_session.py', 'Invoke-WdMindStartupRead.ps1')
    manifest = {'schema_version':1, 'files':{name:sha((root / name).read_bytes()) for name in files}}
    data = json.dumps(manifest, separators=(',', ':')).encode()
    (root / 'deployment-manifest.json').write_bytes(data)
    return root, sha(data), sha(fleet_bytes), started, release


def start(case, *, deadline=15000, enabled=True):
    root, pin, _, _, _ = case
    stamp = subprocess.check_output([str(SHELL), '-NoProfile', '-NonInteractive', '-Command',
        f"(Get-Process -Id {os.getpid()}).StartTime.ToUniversalTime().ToString('o')"], env=env()).decode('ascii').strip()
    command = [str(SHELL), '-NoProfile', '-NonInteractive', '-File', str(CONTROLLER)]
    if enabled:
        command.append('-Enabled')
    command += ['-BundleRoot', str(root), '-ExpectedManifestHash', pin, '-Lane', LANE,
                '-ControllerPid', str(os.getpid()), '-ControllerStartUtc', stamp, '-DeadlineMilliseconds', str(deadline)]
    return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env())


def test_default_off(tmp_path):
    process = start(bundle(tmp_path), enabled=False)
    output, errors = process.communicate(timeout=20)
    assert errors == b'' and json.loads(output)['complete_verified'] is False


def test_real_helper_accepts_synthetic_status_without_writing_receipt(tmp_path):
    case = bundle(tmp_path)
    process = start(case)
    output, errors = process.communicate(timeout=30)
    assert process.returncode == 0, (output, errors)
    result = json.loads(output)
    assert result['complete_verified'] is True and result['lane'] == LANE
    assert result['manifest_sha256'] == case[1] and result['fleet_sha256'] == case[2]
    assert result['receipt'] == 'NOT_PROVEN' and result['record_written'] is False
    assert sha(result['view_json'].encode('utf-8')) == result['view_sha256']
    assert json.loads(result['view_json'])['facts'][0]['event_ref'] == 'a'*64
    assert json.loads(result['view_json'])['observation_status'] == 'OBSERVED'
    assert not (tmp_path / '.codex-audit/wd-mind-startup-view.json').exists()


def test_future_snapshot_refuses(tmp_path):
    process = start(bundle(tmp_path, future=True))
    output, _ = process.communicate(timeout=30)
    assert process.returncode == 2
    assert json.loads(output)['complete_verified'] is False


@pytest.mark.parametrize('nested', [False, True])
def test_delivery_age_uses_delivery_time_without_rewriting_source(tmp_path, nested):
    case = bundle(tmp_path, snapshot_offset=-600, nested_offset=-600 if nested else None)
    process = start(case)
    output, errors = process.communicate(timeout=30)
    assert process.returncode == 0, (output, errors)
    result = json.loads(output)
    view = json.loads(result['view_json'])
    status = view['status_observation'] if nested else view
    as_of = datetime.fromisoformat(status['as_of_utc'].replace('Z', '+00:00'))
    delivery_as_of = datetime.fromisoformat(result['delivery_as_of_utc'].replace('Z', '+00:00'))
    assert status['facts'][0]['age_seconds'] == 0  # age at the original source snapshot
    assert 600 <= result['delivery_age_seconds'] < 900
    assert (delivery_as_of - as_of).total_seconds() >= 600
    assert sha(result['view_json'].encode('utf-8')) == result['view_sha256']


@pytest.mark.parametrize('nested', [False, True])
def test_event_crossing_900_seconds_is_unknown_at_delivery(tmp_path, nested):
    # The source saw an 899-second event two seconds ago; it is old by delivery.
    process = start(bundle(tmp_path, snapshot_offset=-2, source_age_seconds=899,
                           nested_offset=-2 if nested else None))
    output, _ = process.communicate(timeout=30)
    assert process.returncode == 2
    result = json.loads(output)
    assert result['outcome'] == 'unknown' and result['observation_status'] == 'UNKNOWN'
    assert result['reason'] == 'status_stale' and 'view_json' not in result


def test_old_snapshot_is_unknown_without_retimestamping(tmp_path):
    process = start(bundle(tmp_path, stale=True))
    output, _ = process.communicate(timeout=30)
    assert process.returncode == 2
    result = json.loads(output)
    assert result['outcome'] == 'unknown' and result['observation_status'] == 'UNKNOWN'
    assert result['reason'] == 'status_stale' and 'view_json' not in result


@pytest.mark.parametrize('offset,expected', [(-3600, 'status_stale'), (3600, 'refused')])
def test_nested_projection_rejects_old_or_future_outer_snapshot(tmp_path, offset, expected):
    process = start(bundle(tmp_path, nested_offset=offset))
    output, _ = process.communicate(timeout=30)
    assert process.returncode == 2
    result = json.loads(output)
    assert result['reason'] == expected
    assert result['complete_verified'] is False


def test_nested_projection_refuses_future_source_event(tmp_path):
    process = start(bundle(tmp_path, nested_offset=0, nested_future_source=True))
    output, _ = process.communicate(timeout=30)
    assert process.returncode == 2
    assert json.loads(output)['complete_verified'] is False


def test_nested_projection_with_fresh_outer_and_status_is_accepted(tmp_path):
    process = start(bundle(tmp_path, nested_offset=0))
    output, errors = process.communicate(timeout=30)
    assert process.returncode == 0, (output, errors)
    assert json.loads(output)['complete_verified'] is True


def test_nested_delivers_age_and_freshness_for_each_observed_lane(tmp_path):
    case = bundle(tmp_path, nested_offset=-60, nested_source_delta=-120,
                  nested_lanes={'codex-lead-1':0, 'codex-tools-1':30, 'claude-rco-1':50,
                                'claude-rco-2':901, 'fable-5':950}, nested_source_max_age=0)
    process = start(case)
    output, errors = process.communicate(timeout=30)
    assert process.returncode == 0, (output, errors)
    result = json.loads(output)
    source = json.loads(result['view_json'])
    lanes = result['delivery_lane_ages']
    assert set(lanes) == {'codex-lead-1', 'codex-tools-1', 'claude-rco-1', 'claude-rco-2', 'fable-5'}
    assert lanes['codex-lead-1']['age_seconds'] >= 180
    assert lanes['codex-tools-1']['event_utc'] == source['lanes']['codex-tools-1']['last_event_utc']
    assert lanes['fable-5']['event_utc'] == source['lanes']['fable-5']['last_event_utc']
    assert 210 <= lanes['codex-tools-1']['age_seconds'] < 900
    assert lanes['codex-tools-1']['freshness'] == 'OBSERVED'
    assert lanes['claude-rco-1']['age_seconds'] >= 230
    assert lanes['claude-rco-1']['freshness'] == 'OBSERVED'
    assert lanes['claude-rco-2']['age_seconds'] >= 1081
    assert lanes['claude-rco-2']['freshness'] == 'UNKNOWN'
    assert lanes['fable-5']['age_seconds'] >= 1130
    assert lanes['fable-5']['freshness'] == 'UNKNOWN'
    assert result['delivery_source_max_event']['event_utc'] == source['source']['max_event_utc']
    assert result['delivery_source_max_event']['age_seconds'] >= 180
    assert result['delivery_source_max_event']['freshness'] == 'OBSERVED'
    # Source as-of and source-relative ages are preserved; only the separate
    # delivery metadata ages them against the later handoff time.
    assert source['lanes']['codex-tools-1']['observation_age_seconds'] == 30
    assert source['lanes']['fable-5']['observation_age_seconds'] == 950
    assert sha(result['view_json'].encode()) == result['view_sha256']


def test_nested_refuses_incorrect_lane_source_age(tmp_path):
    process = start(bundle(tmp_path, nested_offset=-20,
                           nested_lanes={'fable-5':30}, nested_bad_lane_age=True))
    output, _ = process.communicate(timeout=30)
    assert process.returncode == 2
    assert json.loads(output)['complete_verified'] is False


def test_nested_refuses_future_lane_event(tmp_path):
    process = start(bundle(tmp_path, nested_offset=0, nested_lanes={'fable-5':-3600}))
    output, _ = process.communicate(timeout=30)
    assert process.returncode == 2
    assert json.loads(output)['complete_verified'] is False


def test_real_python_reader_uses_only_pinned_fleet_identity_and_root(tmp_path):
    case = bundle(tmp_path, real_reader=True)
    process = start(case)
    output, errors = process.communicate(timeout=40)
    assert process.returncode == 0, (output, errors)
    result = json.loads(output)
    assert result['complete_verified'] is True
    assert json.loads(result['view_json'])['observation_status'] == 'UNKNOWN'


def test_real_python_reader_reports_pinned_lane_heartbeat_ref(tmp_path):
    case = bundle(tmp_path, real_reader=True)
    fleet = json.loads((case[0] / 'wd-fleet.json').read_text())
    identity = next(item['agent_uuid'] for item in fleet['lanes'] if item['agent'] == LANE)
    event = {'agent': LANE, 'agent_uuid': identity, 'type': 'heartbeat',
             'ts_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
    event_json = json.dumps(event, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    (tmp_path / '.agent-bridge/shared/events.jsonl').write_text(event_json + '\n', encoding='utf-8')
    process = start(case)
    output, errors = process.communicate(timeout=40)
    assert process.returncode == 0, (output, errors)
    result = json.loads(output)
    view = json.loads(result['view_json'])
    assert view['observation_status'] == 'OBSERVED'
    assert view['facts'][0]['event_ref'] == sha(event_json.encode())
    assert view['facts'][0]['agent'] == LANE


@pytest.mark.parametrize('fault', ['uuid', 'root', 'duplicate'])
def test_invalid_pinned_fleet_identity_or_root_refuses(tmp_path, fault):
    case = list(bundle(tmp_path))
    fleet_path = case[0] / 'wd-fleet.json'
    fleet = json.loads(fleet_path.read_text())
    if fault == 'uuid':
        fleet['lanes'][0]['agent_uuid'] = 'invalid'
    elif fault == 'root':
        fleet['runtime_root'] = str(tmp_path / 'not-a-bridge')
    else:
        fleet['lanes'].append(dict(fleet['lanes'][0]))
    fleet_bytes = json.dumps(fleet, separators=(',', ':')).encode()
    fleet_path.write_bytes(fleet_bytes)
    manifest_path = case[0] / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['files']['wd-fleet.json'] = sha(fleet_bytes)
    manifest_bytes = json.dumps(manifest, separators=(',', ':')).encode()
    manifest_path.write_bytes(manifest_bytes)
    case[1] = sha(manifest_bytes)
    process = start(case)
    output, _ = process.communicate(timeout=30)
    assert process.returncode == 2
    assert json.loads(output)['complete_verified'] is False


def test_real_helper_refuses_incorrect_lane(tmp_path):
    process = start(bundle(tmp_path, bad_view=True))
    output, _ = process.communicate(timeout=30)
    assert process.returncode == 2
    assert json.loads(output)['complete_verified'] is False


def test_timeout_retains_child_and_discards_late_status(tmp_path):
    case = bundle(tmp_path, hold=True)
    process = start(case, deadline=5000)
    try:
        limit = time.monotonic() + 20
        while not case[3].exists() and time.monotonic() < limit:
            time.sleep(0.03)
        assert case[3].exists()
        # Exceed both the outer deadline and the real helper's 15 s timeout.
        # The opt-in helper must still own the wrapper after its own timeout.
        time.sleep(17)
        assert process.poll() is None
    finally:
        case[4].write_text('release')
    output, _ = process.communicate(timeout=30)
    assert process.returncode != 0 and b'status_projection' not in output
    assert json.loads(output)['complete_verified'] is False


def test_changed_fleet_pin_refuses_before_wrapper_start(tmp_path):
    case = bundle(tmp_path)
    (case[0] / 'wd-fleet.json').write_bytes(b'{}')
    process = start(case)
    output, _ = process.communicate(timeout=20)
    assert process.returncode == 2 and json.loads(output)['complete_verified'] is False


def test_parent_exit_retains_held_real_helper_then_discards_output(tmp_path):
    case = bundle(tmp_path, hold=True)
    code = '''import os,subprocess,sys\n'''
    code += '''keys=('SYSTEMROOT','WINDIR','SYSTEMDRIVE','PATH','PATHEXT','TEMP','TMP','USERPROFILE','LOCALAPPDATA','APPDATA')\n'''
    code += '''env={k:os.environ[k] for k in keys if k in os.environ}\n'''
    code += '''shell={shell!r}\n'''.format(shell=str(SHELL))
    code += '''stamp=subprocess.check_output([shell,'-NoProfile','-NonInteractive','-Command',"(Get-Process -Id %s).StartTime.ToUniversalTime().ToString('o')"%os.getpid()],env=env).decode('ascii').strip()\n'''
    code += '''args=[shell,'-NoProfile','-NonInteractive','-File',{controller!r},'-Enabled','-BundleRoot',{root!r},'-ExpectedManifestHash',{pin!r},'-Lane',{lane!r},'-ControllerPid',str(os.getpid()),'-ControllerStartUtc',stamp,'-DeadlineMilliseconds','60000']\n'''.format(controller=str(CONTROLLER),root=str(case[0]),pin=case[1],lane=LANE)
    code += '''child=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=sys.stdout.buffer,stderr=sys.stderr.buffer,env=env)\nprint(child.pid,flush=True)\nsys.stdin.buffer.read(1)\n'''
    script = tmp_path / 'synthetic-parent.py'
    script.write_text(code, encoding='utf-8')
    runner = subprocess.Popen([sys.executable, '-I', '-S', '-B', str(script)], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env())
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = None
    try:
        pid = int(runner.stdout.readline())
        handle = kernel.OpenProcess(0x00100000, False, pid)
        assert handle
        limit = time.monotonic() + 20
        while not case[3].exists() and time.monotonic() < limit:
            time.sleep(0.03)
        assert case[3].exists()
        runner.stdin.write(b'x'); runner.stdin.flush(); runner.stdin.close()
        assert runner.wait(timeout=15) == 0
        assert kernel.WaitForSingleObject(handle, 1000) == 258
        case[4].write_text('release')
        assert kernel.WaitForSingleObject(handle, 20000) == 0
        assert b'status_projection' not in runner.stdout.read()
    finally:
        case[4].write_text('release')
        if handle:
            kernel.CloseHandle(handle)
