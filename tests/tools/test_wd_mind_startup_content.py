"""Synthetic manifest-to-bridge binding tests; never touch the live runtime."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import base64
import shutil
import stat
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from waggledance.core import bridge_log_reader as bridge






@pytest.mark.skipif(os.name != 'nt', reason='Windows PowerShell native stdin/EOF probe')
def test_synthetic_powershell_native_child_observes_eof_after_ready(tmp_path):
    scratch = tmp_path / '.codex-audit'
    scratch.mkdir()
    script = scratch / 'synthetic-eof-probe.py'
    script.write_text('import sys\nprint("READY",flush=True)\nb=sys.stdin.buffer.read(1)\n'
                      'print("EOF" if b==b"" else "DATA",flush=True)\n', encoding='utf-8')
    def quote(value): return "'" + str(value).replace("'", "''") + "'"
    command = '& ' + quote(sys.executable) + ' -I -S -B ' + quote(script) + '; exit $LASTEXITCODE'
    encoded = base64.b64encode(command.encode('utf-16le')).decode()
    shell = str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
    proc = subprocess.Popen([shell, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
    first = []
    thread = threading.Thread(target=lambda: first.append(proc.stdout.readline()), daemon=True)
    try:
        thread.start()
        thread.join(timeout=5)
        ready_before_eof = not thread.is_alive() and first == [b'READY\r\n']
        proc.stdin.close()
        code = proc.wait(timeout=8)
        thread.join(timeout=2)
        remainder = proc.stdout.read()
        assert ready_before_eof, f'READY was not observed before stdin EOF; first={first!r}'
        assert code == 0 and b'EOF' in remainder
    finally:
        if proc.poll() is None: proc.kill()  # Synthetic test process only.
        proc.wait(timeout=5)


@pytest.mark.skipif(os.name != 'nt', reason='Windows pinned wrapper EOF probe')
def test_real_pinned_wrapper_observes_eof_after_ready(tmp_path, monkeypatch):
    import test_wd_bridge_code_context as bridge_tests
    shell = str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
    monkeypatch.setattr(bridge_tests, 'PWSH', shell)
    if not Path(bridge_tests.BRIDGE_PYTHON).is_file():
        pytest.skip('pinned per-user bridge Python unavailable')
    monkeypatch.setenv('PSModulePath', str(Path(os.environ.get('SystemRoot', 'C:/Windows')) /
                                          'System32/WindowsPowerShell/v1.0/Modules'))
    bundle = bridge_tests._stage_fake_bundle(tmp_path)
    entry = bundle / 'tools-bootstrap/tools/bridge_next_action.py'
    entry.write_text('import sys\nprint("READY",flush=True)\nb=sys.stdin.buffer.read(1)\n'
                     'print("EOF" if b==b"" else "DATA",flush=True)\n', encoding='utf-8')
    manifest_path = bundle / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['files']['tools-bootstrap/tools/bridge_next_action.py'] = hashlib.sha256(entry.read_bytes()).hexdigest().upper()
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    anchor = hashlib.sha256(manifest_path.read_bytes()).hexdigest().upper()
    wrapper = str(bundle / 'Invoke-WdBridgePython.ps1').replace("'", "''")
    command = "& '" + wrapper + "' tools/bridge_next_action.py -VerifyPackage; exit $LASTEXITCODE"
    encoded = base64.b64encode(command.encode('utf-16le')).decode()
    env = dict(os.environ, WD_REBOOT_EXPECTED_MANIFEST_HASH=anchor, WD_BRIDGE_PYTHON=bridge_tests.BRIDGE_PYTHON)
    env.pop('WD_BRIDGE_PYTHON_SHA256', None)
    proc = subprocess.Popen([shell, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        creationflags=subprocess.CREATE_NO_WINDOW)
    first = []
    thread = threading.Thread(target=lambda: first.append(proc.stdout.readline()), daemon=True)
    try:
        thread.start()
        thread.join(timeout=15)
        ready_before_eof = not thread.is_alive() and first == [b'READY\r\n']
        proc.stdin.close()
        code = proc.wait(timeout=10)
        thread.join(timeout=2)
        remainder, error = proc.stdout.read(), proc.stderr.read()
        assert ready_before_eof, f'pinned wrapper READY not observed before EOF; first={first!r}; error={error!r}'
        assert code == 0 and b'EOF' in remainder, error
    finally:
        if proc.poll() is None: proc.kill()  # Synthetic test process only.
        proc.wait(timeout=5)






@pytest.fixture
def source_fixture(tmp_path, monkeypatch):
    bundle = tmp_path / 'bundle'
    shared = tmp_path / 'runtime' / 'shared'
    bundle.mkdir()
    shared.mkdir(parents=True)
    source = shared / 'events.jsonl'
    raw = b'{"message":"synthetic event"}\n'
    source.write_bytes(raw)
    fleet = json.dumps({'schema_version': 2, 'runtime_root': str(shared.parent)},
                       separators=(',', ':')).encode()
    (bundle / 'wd-fleet.json').write_bytes(fleet)
    files = {'wd-fleet.json': hashlib.sha256(fleet).hexdigest()}
    for name, relative in (
        ('tools.wd_mind_startup_content', 'tools/wd_mind_startup_content.py'),
        ('tools.wd_mind_absent_generation_prefix_readonly', 'tools/wd_mind_absent_generation_prefix_readonly.py'),
        ('tools.bridge_compact_view', 'tools/bridge_compact_view.py'),
        ('waggledance.core.bridge_log_reader', 'waggledance/core/bridge_log_reader.py'),
    ):
        module = importlib.import_module(name)
        data = Path(module.__file__).read_bytes()
        relative = 'tools-bootstrap/' + relative
        target = bundle.joinpath(*relative.split('/'))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        files[relative] = hashlib.sha256(data).hexdigest()
        monkeypatch.setattr(module, '__file__', str(target))
    manifest = json.dumps({'schema_version': 1, 'files': files},
                          separators=(',', ':')).encode()
    (bundle / 'deployment-manifest.json').write_bytes(manifest)
    with bridge._open_log(source) as stream:
        cursor = bridge.BridgeCursor(len(raw), bridge._file_identity(stream), None)
    return {'bundle_root': str(bundle), 'expected_manifest_sha256': hashlib.sha256(manifest).hexdigest(),
            'source_path': str(source), 'generation_path': str(shared / 'events.generation.json'),
            'cursor': cursor, 'expected_prefix_sha256': hashlib.sha256(raw).hexdigest()}


def bind(values):
    module = importlib.import_module('tools.wd_mind_startup_content')
    return module.bind_startup_source(enabled=True, **values)


LANES = ('codex-lead-1','codex-tools-1','fable-5','claude-rco-1','claude-rco-2')
UUIDS = {name:f'00000000-0000-4000-8000-{i:012d}' for i,name in enumerate(LANES,1)}


def _metadata_source(values, rows):
    from tools import bridge_compact_view
    bundle = Path(values['bundle_root'])
    fleet_path = bundle / 'wd-fleet.json'
    fleet = json.loads(fleet_path.read_bytes())
    fleet['lanes'] = [{'agent':lane,'agent_uuid':UUIDS[lane]} for lane in LANES if lane != 'codex-tools-1']
    fleet['tools_supervisor'] = {'agent':'codex-tools-1','agent_uuid':UUIDS['codex-tools-1']}
    data = json.dumps(fleet, separators=(',',':')).encode(); fleet_path.write_bytes(data)
    manifest_path = bundle / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['files']['wd-fleet.json'] = hashlib.sha256(data).hexdigest()
    raw_manifest = json.dumps(manifest, separators=(',',':')).encode(); manifest_path.write_bytes(raw_manifest)
    values['expected_manifest_sha256'] = hashlib.sha256(raw_manifest).hexdigest()
    raw = b''.join(json.dumps(row,sort_keys=True,separators=(',',':')).encode()+b'\n' for row in rows)
    Path(values['source_path']).write_bytes(raw)
    with bridge._open_log(Path(values['source_path'])) as stream:
        values['cursor'] = bridge.BridgeCursor(len(raw),bridge._file_identity(stream),None)
    values['expected_prefix_sha256'] = hashlib.sha256(raw).hexdigest()
    return {row['agent']:bridge_compact_view.event_id(row) for row in rows}


def _row(lane, kind, timestamp, task, **changes):
    return dict({'agent':lane,'agent_uuid':UUIDS[lane], 'type':kind,'ts_utc':timestamp,
                 'task_id':task,'message':'synthetic-private-content-DO-NOT-OUTPUT'}, **changes)


def test_metadata_projection_tracks_all_lanes_and_source_refs_without_messages(source_fixture):
    module = importlib.import_module('tools.wd_mind_startup_content')
    rows = [_row(lane,'claim','2026-01-01T00:02:00Z',f'{lane}/work') for lane in LANES]
    refs = _metadata_source(source_fixture, rows)
    result = module.project_lane_metadata(enabled=True, binding=bind(source_fixture),
                                           as_of_utc='2026-01-01T00:03:00Z',max_age_seconds=900)
    assert result['authority'] == 'none' and result['sender_authentication'] == 'UNKNOWN'
    assert set(result['lanes']) == set(LANES)
    assert 'synthetic-private-content' not in json.dumps(result)
    for lane in LANES:
        lane_view = result['lanes'][lane]
        assert lane_view['next_action'] == lane_view['active_claim'] == 'UNKNOWN'
        assert lane_view['claim_observation']['source_event_ref'] == refs[lane]
        assert lane_view['claim_observation']['age_seconds'] == 60
        assert lane_view['task_observation']['task_id_sha256'] == hashlib.sha256(f'{lane}/work'.encode()).hexdigest()


def test_metadata_projection_is_newest_first_and_does_not_infer_current_claim(source_fixture):
    module = importlib.import_module('tools.wd_mind_startup_content')
    old = _row('claude-rco-1','decision','2026-01-01T00:01:00Z','review-old',status='rco_pass')
    new = _row('claude-rco-1','decision','2026-01-01T00:02:00Z','review-new',status='changes_requested')
    future = _row('claude-rco-1','decision','2026-01-01T00:04:00Z','review-future',status='rco_pass')
    stale = _row('fable-5','claim','2025-12-31T23:00:00Z','stale')
    _metadata_source(source_fixture, [new,future,old,stale])
    out = module.project_lane_metadata(enabled=True,binding=bind(source_fixture),
        as_of_utc='2026-01-01T00:03:00Z',max_age_seconds=900)
    from tools import bridge_compact_view
    assert out['lanes']['claude-rco-1']['decision_observation']['source_event_ref'] == bridge_compact_view.event_id(new)
    assert out['lanes']['claude-rco-1']['decision_observation']['status'] == 'changes_requested'
    assert out['lanes']['fable-5']['claim_observation'] == 'UNKNOWN'
    assert out['lanes']['codex-tools-1']['task_observation'] == 'UNKNOWN'


def test_metadata_projection_rejects_fake_uuid_and_rechecks_source(source_fixture, monkeypatch):
    from tools import wd_mind_startup_content as module
    rows = [_row('codex-tools-1','claim','2026-01-01T00:02:00Z','fake',agent_uuid='fake')]
    _metadata_source(source_fixture, rows)
    anchor = bind(source_fixture)
    result = module.project_lane_metadata(enabled=True,binding=anchor,as_of_utc='2026-01-01T00:03:00Z')
    assert result['lanes']['codex-tools-1']['claim_observation'] == 'UNKNOWN'
    real = module.prefix.read_complete_generationless_prefix_rows
    def mutate(*args):
        data = real(*args)
        Path(source_fixture['source_path']).write_bytes(b'broken\n')
        return data
    monkeypatch.setattr(module.prefix,'read_complete_generationless_prefix_rows',mutate)
    with pytest.raises(ValueError,match='mind_startup_source_refused'):
        module.project_lane_metadata(enabled=True,binding=anchor,as_of_utc='2026-01-01T00:03:00Z')


def test_metadata_projection_handles_100ns_order_without_future_guess(source_fixture):
    from tools import wd_mind_startup_content as module, bridge_compact_view
    lane = 'claude-rco-2'
    old = _row(lane,'claim','2026-01-01T00:01:00.0000001Z','old')
    new = _row(lane,'release','2026-01-01T00:01:00.0000002Z','new')
    future = _row(lane,'claim','2026-01-01T00:01:00.0000003Z','future')
    _metadata_source(source_fixture,[future,new,old])
    result = module.project_lane_metadata(enabled=True,binding=bind(source_fixture),
        as_of_utc='2026-01-01T00:01:00.0000002Z')
    obs = result['lanes'][lane]['claim_observation']
    assert obs['source_event_ref'] == bridge_compact_view.event_id(new)
    assert obs['event_type'] == 'release' and obs['age_seconds'] == 0
    assert result['lanes'][lane]['active_claim'] == 'UNKNOWN'


def test_metadata_projection_refuses_forged_event_ref(source_fixture, monkeypatch):
    from tools import wd_mind_startup_content as module
    row = _row('codex-lead-1','message','2026-01-01T00:01:00Z','work')
    _metadata_source(source_fixture,[row])
    anchor = bind(source_fixture)
    monkeypatch.setattr(module.prefix,'read_complete_generationless_prefix_rows',
                        lambda *args: {'f' * 64:row})
    with pytest.raises(ValueError,match='mind_startup_source_refused'):
        module.project_lane_metadata(enabled=True,binding=anchor,as_of_utc='2026-01-01T00:03:00Z')


@pytest.mark.parametrize('flag', [False, None, 1, 'true'])
def test_metadata_projection_is_explicit_opt_in(flag):
    from tools import wd_mind_startup_content as module
    with pytest.raises(ValueError,match='mind_startup_source_refused'):
        module.project_lane_metadata(enabled=flag)


def test_source_binding_uses_manifest_fleet_and_records_unknowns(source_fixture):
    result = bind(source_fixture)
    assert result.source_path == source_fixture['source_path']
    assert result.cursor == source_fixture['cursor']
    assert result.prefix_sha256 == source_fixture['expected_prefix_sha256']
    assert result.authority == 'none'
    assert result.source_authentication == result.sender_authentication == result.continuity == 'UNKNOWN'


def test_identical_copy_with_different_identity_is_not_fleet_source(source_fixture, tmp_path):
    copied = tmp_path / 'other' / 'events.jsonl'
    copied.parent.mkdir()
    copied.write_bytes(Path(source_fixture['source_path']).read_bytes())
    with bridge._open_log(copied) as handle:
        copied_cursor = bridge.BridgeCursor(copied.stat().st_size, bridge._file_identity(handle), None)
    values = dict(source_fixture, source_path=str(copied),
                  generation_path=str(copied.with_name('events.generation.json')), cursor=copied_cursor)
    with pytest.raises(ValueError):
        bind(values)


@pytest.mark.parametrize('fault', ['manifest_pin', 'fleet_bytes', 'sidecar', 'wrong_sidecar', 'identity',
                                   'generation', 'cursor_inside_row', 'digest', 'wrong_source', 'code_bytes'])
def test_bad_anchor_refuses_without_partial_result(source_fixture, fault, tmp_path):
    values = dict(source_fixture)
    cursor = values['cursor']
    if fault == 'manifest_pin': values['expected_manifest_sha256'] = '0' * 64
    if fault == 'fleet_bytes': (Path(values['bundle_root']) / 'wd-fleet.json').write_bytes(b'{}')
    if fault == 'code_bytes':
        (Path(values['bundle_root']) / 'tools-bootstrap' / 'tools' /
         'wd_mind_absent_generation_prefix_readonly.py').write_bytes(b'# tampered synthetic code')
    if fault == 'sidecar': Path(values['generation_path']).write_bytes(b'{}')
    if fault == 'wrong_sidecar': values['generation_path'] = str(tmp_path / 'events.generation.json')
    if fault == 'identity': values['cursor'] = bridge.BridgeCursor(cursor.offset, 'wrong-identity', None)
    if fault == 'generation': values['cursor'] = bridge.BridgeCursor(cursor.offset, cursor.file_identity, 'g1')
    if fault == 'cursor_inside_row':
        values['cursor'] = bridge.BridgeCursor(cursor.offset - 1, cursor.file_identity, None)
        values['expected_prefix_sha256'] = hashlib.sha256(Path(values['source_path']).read_bytes()[:-1]).hexdigest()
    if fault == 'digest': values['expected_prefix_sha256'] = '0' * 64
    if fault == 'wrong_source': values['source_path'] = str(tmp_path / 'wrong' / 'events.jsonl')
    with pytest.raises(ValueError):
        bind(values)


def test_append_after_cursor_keeps_frozen_prefix_valid(source_fixture):
    with Path(source_fixture['source_path']).open('ab') as stream:
        stream.write(b'{"message":"later synthetic event"}\n')
    assert bind(source_fixture).cursor == source_fixture['cursor']


@pytest.mark.parametrize('link_type', ['symlink', 'junction'])
def test_reparse_ancestor_refuses(source_fixture, monkeypatch, link_type):
    from tools import wd_mind_absent_generation_prefix_readonly as absent
    selected = Path(source_fixture['source_path']).parent
    real_lstat = os.lstat
    def substitute(path, *args, **kwargs):
        if Path(path) == selected:
            return SimpleNamespace(st_mode=stat.S_IFLNK if link_type == 'symlink' else stat.S_IFDIR,
                                   st_file_attributes=0 if link_type == 'symlink' else 0x400)
        return real_lstat(path, *args, **kwargs)
    monkeypatch.setattr(absent.os, 'lstat', substitute)
    with pytest.raises(ValueError):
        bind(source_fixture)


def test_default_off_does_not_touch_inputs():
    module = importlib.import_module('tools.wd_mind_startup_content')
    with pytest.raises(ValueError):
        module.bind_startup_source()


def test_source_rewrite_during_first_hash_is_refused(source_fixture, monkeypatch):
    from tools import wd_mind_absent_generation_prefix_readonly as absent
    path = Path(source_fixture['source_path'])
    original = absent._hash_prefix
    def mutate(*args):
        digest = original(*args)
        path.write_bytes(path.read_bytes().replace(b'event', b'evEnt'))
        return digest
    monkeypatch.setattr(absent, '_hash_prefix', mutate)
    with pytest.raises(ValueError):
        bind(source_fixture)


def test_manifest_replaced_during_first_hash_is_refused(source_fixture, monkeypatch):
    from tools import wd_mind_absent_generation_prefix_readonly as absent
    path = Path(source_fixture['bundle_root']) / 'deployment-manifest.json'
    original = absent._hash_prefix
    def mutate(*args):
        digest = original(*args)
        path.write_bytes(b'{}')
        return digest
    monkeypatch.setattr(absent, '_hash_prefix', mutate)
    with pytest.raises(ValueError):
        bind(source_fixture)


def test_source_rewrite_during_final_code_recheck_is_refused(source_fixture, monkeypatch):
    module = importlib.import_module('tools.wd_mind_startup_content')
    path = Path(source_fixture['source_path'])
    original = module._check_packaged_code
    calls = 0
    def mutate(*args):
        nonlocal calls
        calls += 1
        original(*args)
        if calls == 2:
            path.write_bytes(path.read_bytes().replace(b'event', b'evEnt'))
    monkeypatch.setattr(module, '_check_packaged_code', mutate)
    with pytest.raises(ValueError):
        bind(source_fixture)


def test_fleet_rewrite_during_final_code_recheck_is_refused(source_fixture, monkeypatch):
    module = importlib.import_module('tools.wd_mind_startup_content')
    path = Path(source_fixture['bundle_root']) / 'wd-fleet.json'
    original = module._check_packaged_code
    calls = 0
    def mutate(*args):
        nonlocal calls
        calls += 1
        original(*args)
        if calls == 2:
            path.write_bytes(b'{}')
    monkeypatch.setattr(module, '_check_packaged_code', mutate)
    with pytest.raises(ValueError):
        bind(source_fixture)

# Complete-bundle fixtures are synthetic and imported to exercise the real pinned verifier.
from test_wd_mind_full_runtime import synthetic_bundle


def _invoke_situation(case, lane='codex-lead-1', policy_pin=None, as_of='2026-01-01T00:05:00Z',
                      *, identifier_case='valid', prebound=False):
    import subprocess, sys
    bundle = case['bundle']
    if policy_pin is None:
        policy_pin = hashlib.sha256((bundle / 'wd-mind-message-policy.json').read_bytes()).hexdigest()
    program = r'''
import hashlib, json, sys
sys.path.insert(0, sys.argv[1] + '/tools-bootstrap')
from tools import wd_mind_full_runtime as runtime
from tools.wd_mind_startup_content import project_lane_situation
try:
    policy = b'{"identifiers":[],"schema":"wd.mind-identifier-policy.v2"}'
    policy_sha = hashlib.sha256(policy).hexdigest()
    binding = runtime.assemble_full_runtime(enabled=True, bundle_root=sys.argv[1], expected_manifest_sha256=sys.argv[2],
        identifier_policy_bytes=policy, expected_identifier_policy_sha256=policy_sha) if sys.argv[7] == 'yes' else None
    if sys.argv[6] == 'missing_bytes': policy = None
    if sys.argv[6] == 'missing_pin': policy_sha = None
    if sys.argv[6] == 'missing_both': policy = policy_sha = None
    if sys.argv[6] == 'wrong_pin': policy_sha = '0' * 64
    if sys.argv[6] == 'different_policy':
        policy = b'{"identifiers":["synthetic-identifier"],"schema":"wd.mind-identifier-policy.v2"}'
        policy_sha = hashlib.sha256(policy).hexdigest()
    result = project_lane_situation(enabled=True, bundle_root=sys.argv[1], expected_manifest_sha256=sys.argv[2],
        expected_policy_sha256=sys.argv[3], lane=sys.argv[4], as_of_utc=sys.argv[5], binding=binding,
        identifier_policy_bytes=policy, expected_identifier_policy_sha256=policy_sha)
    print(json.dumps(result, sort_keys=True))
except Exception:
    print(json.dumps({'status':'REFUSED'}))
'''
    result = subprocess.run([sys.executable, '-B', '-c', program, str(bundle), case['digest'], policy_pin, lane, as_of,
                             identifier_case, 'yes' if prebound else 'no'],
                            cwd=str(bundle), text=True, capture_output=True, timeout=30, check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize('prebound', [False, True])
@pytest.mark.parametrize('identifier_case', ['missing_bytes', 'missing_pin', 'wrong_pin'])
def test_situation_refuses_incomplete_or_invalid_identifier_policy(synthetic_bundle, prebound, identifier_case):
    assert _invoke_situation(synthetic_bundle, prebound=prebound, identifier_case=identifier_case) == {'status': 'REFUSED'}


def test_situation_requires_identifier_policy_when_building_binding(synthetic_bundle):
    assert _invoke_situation(synthetic_bundle, identifier_case='missing_both') == {'status': 'REFUSED'}


def test_situation_rejects_a_different_valid_policy_for_prebound_binding(synthetic_bundle):
    assert _invoke_situation(synthetic_bundle, prebound=True, identifier_case='different_policy') == {'status': 'REFUSED'}


def test_situation_accepts_prebound_binding_without_repeated_policy(synthetic_bundle):
    result = _invoke_situation(synthetic_bundle, prebound=True, identifier_case='missing_both')
    assert result['schema'] == 'wd.mind-lane-situation.v2'


def test_situation_accepts_prebound_binding_with_matching_policy(synthetic_bundle):
    result = _invoke_situation(synthetic_bundle, prebound=True)
    assert result['schema'] == 'wd.mind-lane-situation.v2'


def test_situation_refusal_does_not_retain_identifier_policy_in_traceback(tmp_path):
    import traceback
    from tools.wd_mind_startup_content import StartupSourceRefused, project_lane_situation
    identifier = b'{"identifiers":["synthetic-sensitive-identifier"],"schema":"wd.mind-identifier-policy.v2"}'
    with pytest.raises(StartupSourceRefused) as caught:
        project_lane_situation(enabled=True, bundle_root=str(tmp_path), expected_manifest_sha256='a' * 64,
            expected_policy_sha256='b' * 64, lane='codex-lead-1', as_of_utc='2026-01-01T00:05:00Z',
            identifier_policy_bytes=identifier, expected_identifier_policy_sha256='0' * 64)
    stack = traceback.TracebackException.from_exception(caught.value, capture_locals=True).stack
    frame = next(frame for frame in stack if frame.name == 'project_lane_situation')
    assert 'synthetic-sensitive-identifier' not in repr(frame.locals)


def test_situation_uses_screened_own_latest_and_explicit_next_action(synthetic_bundle):
    from tools import bridge_compact_view
    fleet = json.loads((synthetic_bundle['bundle'] / 'wd-fleet.json').read_bytes())
    uuid = next(x['agent_uuid'] for x in fleet['lanes'] if x['agent'] == 'codex-lead-1')
    def row(kind, minute, message, **kw):
        return dict(agent='codex-lead-1', agent_uuid=uuid, to='codex-tools-1', type=kind,
                    task_id='synthetic-work', ts_utc=f'2026-01-01T00:0{minute}:00Z', message=message, **kw)
    older = row('message', 1, 'Synthetic previous task')
    decision = row('decision', 2, 'Synthetic decision', status='approved')
    next_step = row('message', 3, 'next_action: Run synthetic validation')
    latest = row('message', 4, 'Synthetic current task')
    source = synthetic_bundle['source']
    source.write_bytes(b''.join(json.dumps(item,sort_keys=True,separators=(',',':')).encode()+b'\n'
                                for item in (next_step, older, latest, decision)))
    out = _invoke_situation(synthetic_bundle, as_of='2026-01-01T00:06:10Z')
    assert out['authority'] == 'none' and out['sender_authentication'] == 'UNKNOWN'
    assert out['scope'] == 'latest_available_screened_within_frozen_prefix'
    assert out['snapshot_freshness'] == out['continuity'] == 'UNKNOWN'
    assert out['latest_event']['source_event_ref'] == bridge_compact_view.event_id(latest)
    assert out['decision']['source_event_ref'] == bridge_compact_view.event_id(decision)
    assert out['next_action']['source_event_ref'] == bridge_compact_view.event_id(next_step)
    assert out['next_action']['text'] == 'Run synthetic validation'


def test_situation_rejects_unpinned_policy_and_rco_contents(synthetic_bundle):
    assert _invoke_situation(synthetic_bundle, policy_pin='0'*64) == {'status':'REFUSED'}
    result = _invoke_situation(synthetic_bundle, lane='claude-rco-1')
    assert result['latest_event'] == result['decision'] == result['next_action'] == 'UNKNOWN'
    assert 'synthetic rco authored' not in json.dumps(result)


def test_situation_default_off():
    from tools.wd_mind_startup_content import project_lane_situation
    with pytest.raises(ValueError):
        project_lane_situation()


def test_situation_default_off_does_not_hash_identifier_policy(monkeypatch):
    from tools import wd_mind_startup_content as module
    def forbidden(*args, **kwargs):
        pytest.fail('disabled situation must not hash identifier policy')
    monkeypatch.setattr(module.hashlib, 'sha256', forbidden)
    with pytest.raises(module.StartupSourceRefused):
        module.project_lane_situation(identifier_policy_bytes=b'synthetic',
                                      expected_identifier_policy_sha256='a' * 64)


def test_situation_screening_excludes_secret_and_rco_routes(synthetic_bundle):
    from tools import bridge_compact_view
    fleet = json.loads((synthetic_bundle['bundle'] / 'wd-fleet.json').read_bytes())
    uuid = next(x['agent_uuid'] for x in fleet['lanes'] if x['agent'] == 'codex-lead-1')
    def row(minute, message, recipient='codex-tools-1'):
        return dict(agent='codex-lead-1', agent_uuid=uuid, to=recipient, type='message',
                    task_id='synthetic-work', ts_utc=f'2026-01-01T00:0{minute}:00Z', message=message)
    safe = row(1, 'next_action: Check synthetic status')
    secret = row(3, 'next_action: password=syntheticsecretvalue')
    rco = row(4, 'next_action: private synthetic review', recipient='claude-rco-1')
    synthetic_bundle['source'].write_bytes(b''.join(json.dumps(x,sort_keys=True,separators=(',',':')).encode()+b'\n'
                                                       for x in (safe,secret,rco)))
    out = _invoke_situation(synthetic_bundle)
    assert out['next_action']['source_event_ref'] == bridge_compact_view.event_id(safe)
    assert 'syntheticsecretvalue' not in json.dumps(out)
    assert 'private synthetic review' not in json.dumps(out)


def test_situation_future_stale_and_tied_times(synthetic_bundle):
    from tools import bridge_compact_view
    fleet = json.loads((synthetic_bundle['bundle'] / 'wd-fleet.json').read_bytes())
    uuid = next(x['agent_uuid'] for x in fleet['lanes'] if x['agent'] == 'codex-lead-1')
    def row(stamp, text):
        return dict(agent='codex-lead-1', agent_uuid=uuid, to='codex-tools-1', type='message',
                    task_id='synthetic-work', ts_utc=stamp, message=text)
    first = row('2026-01-01T00:04:00.0000001Z', 'Synthetic first')
    last = row('2026-01-01T00:04:00.0000002Z', 'Synthetic second')
    future = row('2026-01-01T00:07:01Z', 'Synthetic future')
    stale = row('2025-12-31T23:00:00Z', 'Synthetic stale')
    synthetic_bundle['source'].write_bytes(b''.join(json.dumps(x,sort_keys=True,separators=(',',':')).encode()+b'\n'
                                                       for x in (future,last,first,stale)))
    out = _invoke_situation(synthetic_bundle, as_of='2026-01-01T00:06:10Z')
    assert out['latest_event']['source_event_ref'] == bridge_compact_view.event_id(last)
    assert out['next_action'] == 'UNKNOWN'


def test_situation_refuses_missing_source_or_added_generation(synthetic_bundle):
    synthetic_bundle['source'].with_name('events.generation.json').write_text('{}', encoding='utf-8')
    assert _invoke_situation(synthetic_bundle) == {'status':'REFUSED'}


def test_situation_respects_manifest_pinned_removed_refs(synthetic_bundle):
    from tools import bridge_compact_view
    bundle = synthetic_bundle['bundle']
    fleet = json.loads((bundle / 'wd-fleet.json').read_bytes())
    uuid = next(x['agent_uuid'] for x in fleet['lanes'] if x['agent'] == 'codex-lead-1')
    def row(minute, text):
        return dict(agent='codex-lead-1', agent_uuid=uuid, to='codex-tools-1', type='message',
                    task_id='synthetic-work', ts_utc=f'2026-01-01T00:0{minute}:00Z', message=text)
    permitted = row(2, 'Synthetic permitted task')
    removed = row(4, 'Synthetic removed task')
    synthetic_bundle['source'].write_bytes(b''.join(json.dumps(x,sort_keys=True,separators=(',',':')).encode()+b'\n'
                                                       for x in (permitted,removed)))
    policy_path = bundle / 'wd-mind-message-policy.json'
    policy = json.loads(policy_path.read_bytes())
    policy['removed_source_refs'] = [bridge_compact_view.event_id(removed)]
    policy_bytes = json.dumps(policy,sort_keys=True,separators=(',',':')).encode()
    policy_path.write_bytes(policy_bytes)
    manifest_path = bundle / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['files']['wd-mind-message-policy.json'] = hashlib.sha256(policy_bytes).hexdigest()
    manifest_bytes = json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
    manifest_path.write_bytes(manifest_bytes)
    synthetic_bundle['digest'] = hashlib.sha256(manifest_bytes).hexdigest()
    out = _invoke_situation(synthetic_bundle)
    assert out['latest_event']['source_event_ref'] == bridge_compact_view.event_id(permitted)
    assert 'Synthetic removed task' not in json.dumps(out)


def test_situation_equal_time_uses_event_ref_tiebreak(synthetic_bundle):
    from tools import bridge_compact_view
    fleet = json.loads((synthetic_bundle['bundle'] / 'wd-fleet.json').read_bytes())
    uuid = next(x['agent_uuid'] for x in fleet['lanes'] if x['agent'] == 'codex-lead-1')
    rows = [dict(agent='codex-lead-1', agent_uuid=uuid, to='codex-tools-1', type='message',
                 task_id='synthetic-work', ts_utc='2026-01-01T00:04:00Z', message=text)
            for text in ('Synthetic tie alpha', 'Synthetic tie beta')]
    synthetic_bundle['source'].write_bytes(b''.join(json.dumps(x,sort_keys=True,separators=(',',':')).encode()+b'\n'
                                                       for x in reversed(rows)))
    out = _invoke_situation(synthetic_bundle, as_of='2026-01-01T00:06:10Z')
    assert out['latest_event']['source_event_ref'] == max(bridge_compact_view.event_id(x) for x in rows)


def test_situation_reuses_manifest_verified_private_binding(synthetic_bundle):
    import subprocess, sys
    bundle = synthetic_bundle['bundle']
    program = r'''
import hashlib,json,sys
sys.path.insert(0, sys.argv[1] + '/tools-bootstrap')
from tools import wd_mind_full_runtime as runtime
from tools.wd_mind_startup_content import project_lane_situation
policy=b'{"identifiers":[],"schema":"wd.mind-identifier-policy.v2"}'
b=runtime.assemble_full_runtime(enabled=True,bundle_root=sys.argv[1],expected_manifest_sha256=sys.argv[2],
    identifier_policy_bytes=policy,expected_identifier_policy_sha256=hashlib.sha256(policy).hexdigest())
runtime.assemble_full_runtime=lambda **kw: (_ for _ in ()).throw(AssertionError('unexpected rebuild'))
pin=hashlib.sha256((__import__('pathlib').Path(sys.argv[1])/'wd-mind-message-policy.json').read_bytes()).hexdigest()
try:
 out=project_lane_situation(enabled=True,binding=b,bundle_root=sys.argv[1],expected_manifest_sha256=sys.argv[2],
          expected_policy_sha256=pin,lane='codex-lead-1',as_of_utc='2026-01-01T00:01:30Z')
 print(json.dumps({'schema':out['schema'],'task':out['latest_event'] != 'UNKNOWN'}))
except Exception:
 print(json.dumps({'status':'REFUSED'}))
'''
    result=subprocess.run([sys.executable,'-B','-c',program,str(bundle),synthetic_bundle['digest']],cwd=str(bundle),
                          capture_output=True,text=True,timeout=30,check=True)
    assert json.loads(result.stdout) == {'schema':'wd.mind-lane-situation.v2','task':True}


def _situation_rows(case, rows):
    case['source'].write_bytes(b''.join(json.dumps(row,sort_keys=True,separators=(',',':')).encode()+b'\n' for row in rows))


def _situation_row(case, timestamp, text, *, task='synthetic-task', kind='message', **kwargs):
    fleet=json.loads((case['bundle']/'wd-fleet.json').read_bytes())
    uuid=next(x['agent_uuid'] for x in fleet['lanes'] if x['agent']=='codex-lead-1')
    return dict(agent='codex-lead-1',agent_uuid=uuid,to='codex-tools-1',type=kind,
                task_id=task,ts_utc=timestamp,message=text,**kwargs)


def test_situation_newest_split_action_never_revives_older_one(synthetic_bundle):
    old=_situation_row(synthetic_bundle,'2026-01-01T00:01:00Z','next_action: Synthetic previous action')
    latest=_situation_row(synthetic_bundle,'2026-01-01T00:03:00Z','next_action: '+('synthetic word '*100))
    _situation_rows(synthetic_bundle,[old,latest])
    result=_invoke_situation(synthetic_bundle)
    assert result['next_action']=='UNKNOWN'
    assert result['latest_event']=='UNKNOWN'


def test_situation_current_task_link_never_associates_different_task(synthetic_bundle):
    from tools import bridge_compact_view
    old=_situation_row(synthetic_bundle,'2026-01-01T00:01:00Z','next_action: Synthetic work A',task='synthetic-a')
    decision=_situation_row(synthetic_bundle,'2026-01-01T00:02:00Z','Synthetic decision A',task='synthetic-a',kind='decision',status='approved')
    latest=_situation_row(synthetic_bundle,'2026-01-01T00:03:00Z','Synthetic work B',task='synthetic-b')
    _situation_rows(synthetic_bundle,[old,decision,latest])
    result=_invoke_situation(synthetic_bundle)
    assert result['latest_event']['source_event_ref']==bridge_compact_view.event_id(latest)
    assert result['next_action']==result['decision']=='UNKNOWN'


def test_situation_availability_respects_reader_strict_boundary(synthetic_bundle):
    from tools import bridge_compact_view
    old=_situation_row(synthetic_bundle,'2026-01-01T00:02:00Z','Synthetic earlier available event')
    recent=_situation_row(synthetic_bundle,'2026-01-01T00:04:50Z','Synthetic not yet available')
    _situation_rows(synthetic_bundle,[old,recent])
    result=_invoke_situation(synthetic_bundle)
    assert result['latest_event']['source_event_ref']==bridge_compact_view.event_id(old)
    assert result['scope']=='latest_available_screened_within_frozen_prefix'


def test_situation_availability_uses_same_microsecond_rounding_as_reader(synthetic_bundle):
    from tools import bridge_compact_view
    old=_situation_row(synthetic_bundle,'2026-01-01T00:02:00Z','Synthetic earlier available event')
    recent=_situation_row(synthetic_bundle,'2026-01-01T00:04:50Z','Synthetic boundary event')
    _situation_rows(synthetic_bundle,[old,recent])
    result=_invoke_situation(synthetic_bundle,as_of='2026-01-01T00:05:01.0000001Z')
    assert result['latest_event']['source_event_ref']==bridge_compact_view.event_id(old)


def test_situation_split_action_blocks_older_even_when_newest_event_is_unsplit(synthetic_bundle):
    from tools import bridge_compact_view
    old=_situation_row(synthetic_bundle,'2026-01-01T00:01:00Z','next_action: Synthetic old action')
    split=_situation_row(synthetic_bundle,'2026-01-01T00:02:00Z','next_action: '+('synthetic word '*100))
    latest=_situation_row(synthetic_bundle,'2026-01-01T00:03:00Z','Synthetic later event')
    _situation_rows(synthetic_bundle,[old,split,latest])
    result=_invoke_situation(synthetic_bundle)
    assert result['latest_event']['source_event_ref']==bridge_compact_view.event_id(latest)
    assert result['next_action']=='UNKNOWN'
