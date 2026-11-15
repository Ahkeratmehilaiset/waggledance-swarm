"""Synthetic installed-bundle tests; never read an operator bridge log."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools import wd_mind_content_query_readonly as reader

ROOT = Path(__file__).resolve().parents[2]


def test_single_manifest_owner_defaults_off_and_is_inert(tmp_path, monkeypatch):
    """Constructing a disabled/default owner must not start a worker."""
    from tools import wd_mind_full_runtime as runtime
    calls = []
    monkeypatch.setattr(runtime._ManifestWorkerFactory, '__call__', lambda self: calls.append('spawn'))
    with pytest.raises(runtime.FullRuntimeRefused):
        runtime.create_single_manifest_owner()
    assert calls == []


@pytest.mark.parametrize('fault', ['vectors', 'policy_pin'])
def test_single_adapter_symbol_and_manifest_pins_without_rotator(synthetic_bundle, fault):
    """Import real bundle bytes; no model process or fake adapter is started."""
    bundle = synthetic_bundle['bundle']
    import datetime
    source = synthetic_bundle['source']
    rows = [json.loads(row) for row in source.read_bytes().splitlines()]
    for row in rows:
        row['ts_utc'] = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=2)).isoformat().replace('+00:00', 'Z')
    source.write_bytes(b''.join(canonical(row) + b'\n' for row in rows))
    event_sha = hashlib.sha256(canonical(rows[0])).hexdigest()
    path = bundle / 'deployment-manifest.json'
    manifest = json.loads(path.read_bytes())
    manifest['files'].pop('tools-bootstrap/tools/wd_mind_e5_rotating_client.py')
    raw = canonical(manifest)
    path.write_bytes(raw)
    pin = hashlib.sha256(raw).hexdigest()
    script = r'''
import hashlib, pathlib, sys, types
root, pin = pathlib.Path(sys.argv[1]), sys.argv[2]
sys.path.insert(0, str(root / 'tools-bootstrap'))
from tools import wd_mind_e5_snapshot_adapter as adapter
from tools import wd_mind_e5_lifecycle as lifecycle
from tools import wd_mind_e5_client as client_module
assert pathlib.Path(adapter.__file__).resolve() == (root / 'tools-bootstrap/tools/wd_mind_e5_snapshot_adapter.py').resolve()
assert callable(adapter.create_single_flat_adapter)
binding_hash = hashlib.sha256((root / 'wd-mind-e5-encoder-binding.json').read_bytes()).hexdigest()
owner = lifecycle._Owner(client_module.BULK_CONTRACT_SHA256)
owner.resources.process = types.SimpleNamespace(pid=1)
owner.resources.job = object()
owner._state = 'RUNNING'
handshake = client_module._RESPONSE.pack(b'ME5R', 0, 0, 0, b'\0' * 32, b'\0' * 32)
owner._output.extend(client_module._LENGTH.pack(len(handshake)) + handshake)
client = client_module.GracefulE5WorkerClient(owner, timeout=1, encoder_binding_sha256=binding_hash,
                                             bulk_contract_sha256=client_module.BULK_CONTRACT_SHA256)
single = adapter.create_single_flat_adapter(enabled=True, bundle_root=str(root),
                                            expected_manifest_sha256=pin, client=client)
assert type(single) is adapter._SingleFlatAdapter
# This part uses the real bundled runtime, source binding and adapter. Only
# process launch and vector construction are synthetic; no model is started.
from tools import wd_mind_full_runtime as runtime
binding = runtime.assemble_full_runtime(enabled=True, bundle_root=str(root), expected_manifest_sha256=pin)
factory = runtime._ManifestWorkerFactory(str(root), pin, {}, root / 'synthetic-lock', 1, 0,
                                         client_module.BULK_CONTRACT_SHA256)
def launch(self, *, owner_sink):
    owner_sink(owner)
    return {'status':'READY', 'client':client, 'owner':owner, 'lifecycle':owner.status(), 'authority':'none'}
runtime._ManifestWorkerFactory.__call__ = launch
# Synthetic encoder responses, but real snapshot, adapter and flat-memory build.
import struct
vector = struct.pack('<384f', 1.0, *([0.0] * 383))
def encode(payload, **kwargs):
    check = kwargs.get('frame_check')
    if check is not None: check()
    count = len(payload['passages']) if payload['operation'] == 'refresh' else 1
    return {'nonce':payload['nonce'], 'request_sha256':payload['request_sha256'], 'vector_bytes':vector * count}
client.encode_supervised = encode
wrapper = runtime.create_single_manifest_owner(enabled=True, binding=binding, lane='codex-lead-1',
    worker_factory=factory, shutdown_timeout=0)
assert wrapper.start() == {'state':'READY','owned':True,'authority':'none'}
assert type(wrapper._adapter) is adapter._SingleFlatAdapter and wrapper._client is client
assert wrapper._handler._memory is not None and wrapper._handler._memory.binding is binding
policy_pin = hashlib.sha256(binding._PrivateBinding__snapshot['identifier_policy_binding'][0]).hexdigest()
for bad in ({}, {'enabled':True,'expected_nonce':'x'*64,'expected_identifier_policy_sha256':policy_pin,'max_total_bytes':10000000},
            {'enabled':True,'expected_nonce':'e'*64,'expected_identifier_policy_sha256':policy_pin,'max_total_bytes':1},
            {'enabled':True,'expected_nonce':'e'*64,'expected_identifier_policy_sha256':policy_pin,'max_total_bytes':True}):
    try:
        wrapper.prepare_consumer_export(**bad)
        raise AssertionError('bad export input accepted')
    except runtime.FullRuntimeRefused:
        pass
import traceback
synthetic_private_key = binding._PrivateBinding__snapshot['identifier_policy_binding'][1]
try:
    wrapper.prepare_consumer_export(enabled=True, expected_nonce='e'*64, expected_identifier_policy_sha256=policy_pin, max_total_bytes=1)
    raise AssertionError('size limit accepted')
except runtime.FullRuntimeRefused as exc:
    captured = traceback.TracebackException.from_exception(exc, capture_locals=True)
    frames = [frame for frame in captured.stack if frame.name == 'prepare_consumer_export']
    assert frames and all(repr(synthetic_private_key) not in value for frame in frames for value in (frame.locals or {}).values()), 'traceback locals exposed identifier key'
prepared = wrapper.prepare_consumer_export(enabled=True, expected_nonce='e'*64, expected_identifier_policy_sha256=policy_pin, max_total_bytes=10000000)
info = prepared.status()
assert info['state'] == 'PREPARED_LOCAL_ONLY' and info['authority'] == 'none'
assert info['receiver_authentication'] == 'UNKNOWN' and info['transmission'] == 'NOT_AVAILABLE'
assert info['block_count'] == 12 and info['total_bytes'] > 0
import json
metadata_raw = prepared._PrivateConsumerExport__metadata
metadata = json.loads(metadata_raw)
blocks = prepared._PrivateConsumerExport__blocks
assert metadata['schema'] == 'wd.mind-owner-export.v1' and metadata['authority'] == 'none'
assert metadata['nonce'] == 'e'*64
assert [item['name'] for item in metadata['blocks']] == [name for name, value in blocks]
assert all(item['length'] == len(value) for item, (_, value) in zip(metadata['blocks'], blocks))
assert info['total_bytes'] == len(metadata_raw) + sum(len(value) for _, value in blocks)
export = dict(blocks)
assert export['identifier_key'] == binding._PrivateBinding__snapshot['identifier_policy_binding'][1]
assert export['projection'] == binding._PrivateBinding__snapshot['projection_bytes']
assert export['flat_manifest'] == wrapper._handler._memory.manifest
assert export['vectors'] == wrapper._handler._memory.vectors
assert export['ids'] == runtime.reader._canonical(list(wrapper._handler._memory.ids))
assert export['identifier_key'] not in metadata_raw
assert binding._PrivateBinding__snapshot['identifier_policy_binding'][1].hex() not in repr(prepared)
class UntrustedDestination:
    calls = 0
    def write(self, value):
        self.calls += 1
        raise AssertionError('private bytes escaped')
destination = UntrustedDestination()
try:
    prepared.write_to_handle(destination)
    raise AssertionError('unverified export must refuse')
except runtime.FullRuntimeRefused:
    pass
assert destination.calls == 0
prepared.discard()
assert prepared.status()['state'] == 'DISCARDED' and prepared.status()['total_bytes'] == 0
response = wrapper.candidates({'schema':'wd.mind-pipe-request.v1','query_id':'startup_work_overview',
    'nonce':'d'*64,'lane':'codex-lead-1','scope':'own'})
assert response['status'] == 'CANDIDATES' and len(response['candidates']) == 1, repr(response)
assert response['candidates'][0]['source_event_sha256'] == sys.argv[3]
memory = wrapper._handler._memory
if sys.argv[4] == 'vectors':
    memory.vectors = b'\0' * len(memory.vectors)
else:
    policy_pin = '0'*64
try:
    wrapper.prepare_consumer_export(enabled=True, expected_nonce='e'*64, expected_identifier_policy_sha256=policy_pin, max_total_bytes=10000000)
    raise AssertionError('altered vectors or policy pin accepted')
except runtime.FullRuntimeRefused:
    pass
assert wrapper.status()['state'] == 'HOLD'
assert 'tools.wd_mind_e5_rotating_client' not in sys.modules
print('synthetic_adapter_manifest_verified')
'''
    proc = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', script, str(bundle), pin, event_sha, fault],
                          cwd=bundle, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == 'synthetic_adapter_manifest_verified'


def test_single_manifest_owner_retains_failed_launch_owner(tmp_path, monkeypatch):
    """A post-reservation HOLD must stay reachable without an extra worker."""
    from tools import wd_mind_full_runtime as runtime
    from tools import wd_mind_e5_client
    fleet = canonical({'lanes': [{'agent': 'codex-lead-1'}],
                       'tools_supervisor': {'agent': 'codex-tools-1'}})
    snapshot = {'projection_sha256': 'b' * 64}
    binding = runtime._PrivateBinding(snapshot, tmp_path, 'a' * 64, (fleet,), {})
    factory = runtime._ManifestWorkerFactory(str(tmp_path), 'a' * 64, {}, tmp_path / 'lock', 5, 0,
                                             wd_mind_e5_client.BULK_CONTRACT_SHA256)
    monkeypatch.setattr(runtime._PrivateBinding, 'snapshot_for_query', lambda self: snapshot)
    monkeypatch.setattr(runtime, '_flat_snapshot', lambda candidate, frozen: frozen)

    class Owner:
        def status(self):
            return {'state': 'HOLD', 'owned': True}
        def shutdown(self, *, timeout):
            return {'state': 'HOLD', 'owned': True}

    held = Owner()
    calls = []
    def start(self, **kwargs):
        calls.append(True)
        kwargs['owner_sink'](held)
        return {'status': 'HOLD', 'client': None, 'owner': held,
                'lifecycle': held.status(), 'authority': 'none'}
    monkeypatch.setattr(runtime._ManifestWorkerFactory, '__call__', start)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    assert calls == []
    assert owner.start() == {'state': 'HOLD', 'owned': True, 'authority': 'none'}
    assert owner.close() == {'state': 'HOLD', 'owned': True, 'authority': 'none'}
    with pytest.raises(runtime.FullRuntimeRefused):
        owner.start()
    assert calls == [True]


def _single_owner_synthetic(monkeypatch, tmp_path):
    from tools import wd_mind_full_runtime as runtime
    from tools import wd_mind_e5_client
    from tools import wd_mind_e5_snapshot_adapter as adapter_module
    fleet = canonical({'lanes': [{'agent': 'codex-lead-1'}],
                       'tools_supervisor': {'agent': 'codex-tools-1'}})
    snapshot = {'projection_sha256': 'b' * 64}
    binding = runtime._PrivateBinding(snapshot, tmp_path, 'a' * 64, (fleet,), {})
    factory = runtime._ManifestWorkerFactory(str(tmp_path), 'a' * 64, {}, tmp_path / 'lock', 5, 0,
                                             wd_mind_e5_client.BULK_CONTRACT_SHA256)
    monkeypatch.setattr(runtime._PrivateBinding, 'snapshot_for_query', lambda self: snapshot)
    monkeypatch.setattr(runtime, '_flat_snapshot', lambda candidate, frozen: frozen)

    class Owner:
        def status(self): return {'state': 'RUNNING', 'owned': True}
        def shutdown(self, *, timeout): return {'state': 'HOLD', 'owned': True}
    class Client:
        def __init__(self, owner): self.owner = owner
        def close(self, *, timeout): return {'state': 'EXITED', 'owned': False, 'exit_code': 0}
    class Adapter:
        def passage(self, values): return b'synthetic'
    held = Owner()
    client = Client(held)
    calls = []
    def start(self, **kwargs):
        calls.append(True)
        kwargs['owner_sink'](held)
        return {'status': 'READY', 'client': client, 'owner': held,
                'lifecycle': held.status(), 'authority': 'none'}
    monkeypatch.setattr(runtime._ManifestWorkerFactory, '__call__', start)
    monkeypatch.setattr(adapter_module, 'create_single_flat_adapter', lambda **kwargs: Adapter(), raising=False)
    return runtime, adapter_module, binding, factory, calls


def test_single_manifest_owner_ready_and_close_are_explicit(tmp_path, monkeypatch):
    runtime, _, binding, factory, calls = _single_owner_synthetic(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', lambda self: {
        'schema': 'wd.mind-flat-owner-build.v1', 'status': 'BUILT_IN_MEMORY', 'authority': 'none'})
    expected = {'schema': 'synthetic-refs-only', 'authority': 'none', 'results': []}
    monkeypatch.setattr(runtime, 'map_pipe_candidates', lambda **kwargs: expected)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    assert calls == []
    assert owner.start() == {'state': 'READY', 'owned': True, 'authority': 'none'}
    assert owner.candidates({'synthetic': 'request'}) == expected
    assert owner.close() == {'state': 'STOPPED', 'owned': False, 'authority': 'none'}
    with pytest.raises(runtime.FullRuntimeRefused):
        owner.candidates({'synthetic': 'request'})
    assert calls == [True]


def test_single_manifest_owner_rechecks_source_before_spawn(tmp_path, monkeypatch):
    runtime, _, binding, factory, calls = _single_owner_synthetic(monkeypatch, tmp_path)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    def changed(*args): raise ValueError('synthetic source changed')
    monkeypatch.setattr(runtime, '_flat_snapshot', changed)
    assert owner.start() == {'state': 'HOLD', 'owned': None, 'authority': 'none'}
    assert calls == []


@pytest.mark.parametrize('fault', ['bundle', 'manifest', 'bulk', 'location'])
def test_single_manifest_owner_rejects_factory_retarget_before_spawn(tmp_path, monkeypatch, fault):
    runtime, _, binding, factory, calls = _single_owner_synthetic(monkeypatch, tmp_path)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    if fault == 'bundle': factory._bundle = str(tmp_path / 'foreign')
    elif fault == 'manifest': factory._manifest = '0' * 64
    elif fault == 'bulk': factory._bulk = '0' * 64
    else: factory._location['runtime_revision'] = 'changed'
    assert owner.start() == {'state': 'HOLD', 'owned': None, 'authority': 'none'}
    assert calls == []


def test_single_manifest_owner_keeps_owner_if_factory_retargets_during_launch(tmp_path, monkeypatch):
    runtime, _, binding, factory, calls = _single_owner_synthetic(monkeypatch, tmp_path)
    original = runtime._ManifestWorkerFactory.__call__
    def retarget(self, **kwargs):
        result = original(self, **kwargs)
        self._manifest = '0' * 64
        return result
    monkeypatch.setattr(runtime._ManifestWorkerFactory, '__call__', retarget)
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', lambda self: {
        'schema': 'wd.mind-flat-owner-build.v1', 'status': 'BUILT_IN_MEMORY', 'authority': 'none'})
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    assert owner.start() == {'state': 'HOLD', 'owned': True, 'authority': 'none'}
    assert owner._owner is not None and calls == [True]


@pytest.mark.parametrize('phase', ['before', 'during'])
def test_single_manifest_owner_holds_and_never_returns_retargeted_candidates(tmp_path, monkeypatch, phase):
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', lambda self: {
        'schema': 'wd.mind-flat-owner-build.v1', 'status': 'BUILT_IN_MEMORY', 'authority': 'none'})
    calls = []
    def candidates(**kwargs):
        calls.append(True)
        if phase == 'during': factory._manifest = '0' * 64
        return {'synthetic': 'must not escape'}
    monkeypatch.setattr(runtime, 'map_pipe_candidates', candidates)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    assert owner.start() == {'state': 'READY', 'owned': True, 'authority': 'none'}
    if phase == 'before': factory._manifest = '0' * 64
    with pytest.raises(runtime.FullRuntimeRefused, match='^mind_full_runtime_refused$'):
        owner.candidates({'synthetic': 'request'})
    assert owner.status() == {'state': 'HOLD', 'owned': True, 'authority': 'none'}
    assert calls == ([] if phase == 'before' else [True])


def test_single_manifest_owner_never_reports_ready_when_owner_status_raises(tmp_path, monkeypatch):
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', lambda self: {
        'schema': 'wd.mind-flat-owner-build.v1', 'status': 'BUILT_IN_MEMORY', 'authority': 'none'})
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    assert owner.start() == {'state': 'READY', 'owned': True, 'authority': 'none'}
    held = owner._owner
    def failed_status(): raise ValueError('synthetic private owner detail')
    monkeypatch.setattr(held, 'status', failed_status)
    assert owner.status() == {'state': 'HOLD', 'owned': None, 'authority': 'none'}
    assert owner._owner is held


@pytest.mark.parametrize('signal_name', ['keyboard', 'cancelled'])
def test_single_manifest_owner_retains_worker_when_adapter_is_cancelled(tmp_path, monkeypatch, signal_name):
    import asyncio
    runtime, adapter_module, binding, factory, calls = _single_owner_synthetic(monkeypatch, tmp_path)
    signal = KeyboardInterrupt if signal_name == 'keyboard' else asyncio.CancelledError
    def interrupt(**kwargs): raise signal()
    monkeypatch.setattr(adapter_module, 'create_single_flat_adapter', interrupt, raising=False)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    with pytest.raises(signal):
        owner.start()
    assert owner.status() == {'state': 'HOLD', 'owned': True, 'authority': 'none'}
    assert owner.close() == {'state': 'STOPPED', 'owned': False, 'authority': 'none'}
    assert calls == [True]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


@pytest.fixture
def synthetic_bundle(tmp_path):
    bundle = tmp_path / 'bundle'
    bootstrap = bundle / 'tools-bootstrap'
    shared = tmp_path / 'runtime' / 'shared'
    shared.mkdir(parents=True)
    lane_names = ('claude-rco-1', 'claude-rco-2', 'codex-lead-1', 'fable-5')
    ids = {name: f'00000000-0000-4000-8000-{index:012d}'
           for index, name in enumerate((*lane_names, 'codex-tools-1'), 1)}
    fleet = {'schema_version': 2, 'runtime_root': str(shared.parent),
             'lanes': [{'agent': n, 'agent_uuid': ids[n]} for n in lane_names],
             'tools_supervisor': {'agent': 'codex-tools-1', 'agent_uuid': ids['codex-tools-1']}}
    policy = {'schema': 'wd.mind-content-policy.v3', 'lanes': ['codex-lead-1', 'codex-tools-1', 'fable-5'],
              'types': ['message', 'done', 'decision'], 'task_scope': 'all_tasks', 'removed_source_refs': [],
              'max_message_chars': 4096, 'max_chunk_chars': 300}
    files = {}
    def put(relative, data):
        path = bundle.joinpath(*relative.split('/'))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        files[relative] = hashlib.sha256(data).hexdigest()
    put('wd-fleet.json', canonical(fleet))
    put('wd-mind-message-policy.json', canonical(policy))
    code_paths = set(reader._REQUIRED_CODE) | {'tools/wd_mind_content_query_readonly.py',
        'tools/wd_mind_startup_content.py', 'tools/wd_mind_full_runtime.py',
        'tools/wd_mind_content_snapshot_package.py', 'tools/wd_mind_e5_client.py',
        'tools/wd_mind_e5_lifecycle.py', 'tools/wd_mind_e5_rotating_client.py',
        'tools/wd_mind_e5_snapshot_adapter.py', 'tools/wd_mind_e5_worker.py',
        'tools/wd_mind_runtime_manifest.py', 'tools/wd_mind_interpreter_identity.py',
        'tools/wd_mind_runtime_child.py', 'tools/wd_mind_runtime_process.py',
        'tools/wd_mind_runtime_bootstrap.py', 'tools/wd_mind_runtime_mutex.py'}
    for relative in sorted(code_paths):
        put('tools-bootstrap/' + relative, (ROOT / relative).read_bytes())
    model_root, interpreter_root = tmp_path / 'model-root', tmp_path / 'interpreter-root'
    def asset(root, relative, data):
        path = root.joinpath(*relative.split('/')); path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        return {'path': relative, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    weight = asset(model_root, 'model/model.safetensors', b'synthetic-model-weights')
    runtime_manifest = canonical({'schema':'wd.q4-runtime-manifest.v1', 'generation':'a'*40,
                                  'revision':'b'*40, 'files':[weight]})
    interpreter_files = [dict(asset(interpreter_root, relative, data), role=role) for relative, data, role in (
        ('python.exe', b'synthetic-python', 'interpreter'), ('python.dll', b'synthetic-dll', 'dll'),
        ('lib/os.py', b'synthetic-stdlib', 'stdlib'))]
    interpreter_identity = canonical({'schema':'wd.q4-interpreter-identity.v1', 'files':interpreter_files})
    put('wd-mind-runtime-manifest.json', runtime_manifest)
    put('wd-mind-interpreter-identity.json', interpreter_identity)
    e5_policy = {'truncate_chars': 2000, 'normalize': True, 'dtype': 'float32', 'dim': 384}
    runtime_binding = {'schema': 'wd.mind-memory-encoder-binding.v1',
        'encoder': {'model': 'intfloat/multilingual-e5-small', 'revision': 'b' * 40, 'weights_sha256': weight['sha256']},
        'code_sha256': files['tools-bootstrap/tools/wd_mind_e5_worker.py'],
        'passage_policy': dict(e5_policy, prefix='passage: '), 'query_policy': dict(e5_policy, prefix='query: ')}
    put('wd-mind-e5-encoder-binding.json', canonical(runtime_binding))
    location = {'schema':'wd.mind-runtime-location.v1', 'source_commit':'c'*40,
        'runtime_root':str(model_root), 'interpreter_root':str(interpreter_root),
        'runtime_manifest_sha256':files['wd-mind-runtime-manifest.json'],
        'interpreter_identity_sha256':files['wd-mind-interpreter-identity.json'],
        'encoder_binding_sha256':files['wd-mind-e5-encoder-binding.json']}
    put('wd-mind-runtime-location.json', canonical(location))
    manifest = canonical({'schema_version': 1, 'source_commit':'c'*40, 'files': files})
    (bundle / 'deployment-manifest.json').write_bytes(manifest)
    def row(message, agent='codex-lead-1', recipient='codex-tools-1'):
        return {'agent': agent, 'agent_uuid': ids[agent], 'to': recipient,
                'type': 'message', 'status': 'in_progress', 'task_id': 'synthetic-task',
                'ts_utc': '2026-01-01T00:00:00.000000Z', 'message': message}
    events = [row('ordinary synthetic work outcome'), row('password=syntheticfixturevalue'),
              row('synthetic rco authored', agent='claude-rco-1'),
              row('synthetic rco addressed', recipient='claude-rco-2')]
    wrong_identity = row('synthetic wrong identity')
    wrong_identity['agent_uuid'] = ids['fable-5']
    events.append(wrong_identity)
    source = shared / 'events.jsonl'
    source.write_bytes(b''.join(canonical(event) + b'\n' for event in events))
    return {'bundle': bundle, 'source': source, 'digest': hashlib.sha256(manifest).hexdigest(),
            'model_root':model_root, 'interpreter_root':interpreter_root}


def invoke(case, scenario='normal'):
    code = r'''
import hashlib, io, json, os, pathlib, struct, sys
sys.path.insert(0, sys.argv[1] + '/tools-bootstrap')
from tools import wd_mind_full_runtime as runtime
from tools import wd_mind_content_query_readonly as reader
from tools import wd_mind_absent_generation_prefix_readonly as prefix
mode = sys.argv[4]
if mode.startswith(('package', 'handler')):
    path = pathlib.Path(sys.argv[3])
    row = json.loads(path.read_bytes().splitlines()[0])
    with path.open('ab') as output:
        for number in range(25 if mode in ('handler_fast_multibatch','handler_fast_rebind') else 4):
            row['message'] = 'synthetic additional working item ' + str(number)
            if mode.startswith('handler') and not mode.startswith('handler_historical'):
                import datetime
                minutes = -2 if mode == 'handler_future_time' else (20 if mode == 'handler_stale_time' else 2)
                row['ts_utc'] = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes)).isoformat().replace('+00:00', 'Z')
            output.write(json.dumps(row, sort_keys=True, separators=(',', ':')).encode() + b'\n')
        fleet = json.loads((pathlib.Path(sys.argv[1]) / 'wd-fleet.json').read_bytes())
        fable = next(entry for entry in fleet['lanes'] if entry['agent'] == 'fable-5')
        row.update(agent='fable-5', agent_uuid=fable['agent_uuid'], message='synthetic external note')
        output.write(json.dumps(row, sort_keys=True, separators=(',', ':')).encode() + b'\n')
if mode in ('rewrite', 'rotation'):
    observe = prefix.observe_prefix
    def changed(**kwargs):
        result = observe(**kwargs)
        path = pathlib.Path(sys.argv[3])
        data = path.read_bytes()
        if mode == 'rewrite': path.write_bytes(data.replace(b'ordinary', b'Ordinary'))
        else:
            replacement = path.with_suffix('.replacement')
            replacement.write_bytes(data)
            os.replace(replacement, path)
        return result
    prefix.observe_prefix = changed
if mode == 'policy_during':
    original = reader.rederive_complete_message_projection
    def changed_projection(**kwargs):
        result = original(**kwargs)
        (pathlib.Path(sys.argv[1]) / 'wd-mind-message-policy.json').write_bytes(b'{}')
        return result
    reader.rederive_complete_message_projection = changed_projection
try:
    if mode.startswith('owner_preflight_'):
        from tools import wd_mind_runtime_child as child, wd_mind_runtime_process as process
        synthetic_audit = pathlib.Path(sys.argv[1]).parent / 'synthetic-owner-audit'
        (synthetic_audit / 'wd-mind-runtime').mkdir(parents=True)
        child._AUDIT_ROOT = synthetic_audit
        runtime._fixed_worker_audit_lock = lambda: synthetic_audit / 'wd-mind-runtime' / 'q4-reader.lock'
        calls = []
        class SyntheticNative:
            def __init__(self): self.handles, self.allocations = [], []
            def logon_sid(self, *, require_non_elevated):
                calls.append(require_non_elevated)
                if mode == 'owner_preflight_admin': raise ValueError('synthetic elevated')
                return 'S-1-5-5-1-2'
        process._PipeNative = SyntheticNative
        if mode == 'owner_preflight_bad_pin':
            (pathlib.Path(sys.argv[1]) / 'wd-mind-message-policy.json').write_bytes(b'{}')
        ok = runtime._owner_preflight(bundle_root=sys.argv[1], manifest_sha256=sys.argv[2])
        print(json.dumps({'result':'ok', 'accepted':ok, 'token_calls':calls})); sys.exit(0)
    if mode.startswith('factory'):
        from tools import wd_mind_runtime_child as child, wd_mind_e5_rotating_client as rotating
        synthetic_audit = pathlib.Path(sys.argv[1]).parent / 'synthetic-audit'
        (synthetic_audit / 'wd-mind-runtime').mkdir(parents=True)
        child._AUDIT_ROOT = synthetic_audit  # Synthetic fixture; never change the host audit policy.
        runtime._fixed_worker_audit_lock = lambda: synthetic_audit / 'wd-mind-runtime' / 'q4-reader.lock'
        calls = []
        def fake_start(**kwargs):
            calls.append(kwargs)
            return {'status':'HOLD','owner':'synthetic-owner','client':None}
        child.start_e5_worker_graceful = fake_start
        try:
            if mode == 'factory_missing_audit': (synthetic_audit / 'wd-mind-runtime').rmdir()
            if mode == 'factory_bad_origin': child.__file__ = str(pathlib.Path(sys.argv[1]) / 'wrong-child.py')
            factory = runtime.create_manifest_worker_factory(enabled=(mode != 'factory_disabled'),
                bundle_root=sys.argv[1], expected_manifest_sha256=sys.argv[2],
                worker_timeout_seconds=0 if mode == 'factory_bad_timeout' else 5,
                lock_timeout_seconds=-1 if mode == 'factory_bad_lock_timeout' else 1,
                bulk_contract_sha256=('0'*64 if mode == 'factory_bad_bulk' else rotating.BULK_CONTRACT_SHA256))
            if mode == 'factory_asset_mutation':
                location = json.loads((pathlib.Path(sys.argv[1]) / 'wd-mind-runtime-location.json').read_bytes())
                (pathlib.Path(location['runtime_root']) / 'model/model.safetensors').write_bytes(b'changed')
            if mode == 'factory_manifest_mutation':
                (pathlib.Path(sys.argv[1]) / 'wd-mind-message-policy.json').write_bytes(b'{}')
            if mode == 'factory_audit_removed': (synthetic_audit / 'wd-mind-runtime').rmdir()
            if mode == 'factory_lock_changed': runtime._fixed_worker_audit_lock = lambda: synthetic_audit / 'other.lock'
            output = factory()
            item = calls[0] if calls else {}
            args = item.get('tuple_args', {})
            print(json.dumps({'result':'ok','calls':len(calls),'status':output.get('status'),
                'same_owner':output.get('owner') == 'synthetic-owner',
                'worker_only':item.get('worker_only') is True,'scratch_none':item.get('scratch_root') is None,
                'lock_exact':args.get('lock-file') == str(synthetic_audit / 'wd-mind-runtime' / 'q4-reader.lock'),
                'args_exact':set(args) == {'runtime-root','runtime-manifest','runtime-manifest-sha256',
                                           'runtime-generation','runtime-revision','lock-file','lock-timeout'}}))
        except Exception:
            print(json.dumps({'result':'refused','calls':len(calls)}))
        sys.exit(0)
    if mode == 'location':
        resolved = runtime.resolve_manifest_runtime_location(enabled=True, bundle_root=sys.argv[1],
                                                             expected_manifest_sha256=sys.argv[2])
        print(json.dumps({'result':'ok', 'generation':resolved['runtime_generation'],
                          'revision':resolved['runtime_revision'],
                          'interpreter_name':pathlib.Path(resolved['interpreter']).name,
                          'verified':resolved['runtime_status'] == resolved['interpreter_status'] == 'VERIFIED'}))
        sys.exit(0)
    binding = runtime.assemble_full_runtime(enabled=(mode != 'disabled'), bundle_root=sys.argv[1], expected_manifest_sha256=sys.argv[2])
    if mode.startswith(('package', 'handler')):
        def canonical(obj): return json.dumps(obj, sort_keys=True, separators=(',', ':')).encode()
        def sha(raw): return hashlib.sha256(raw).hexdigest()
        runtime_binding_raw = (pathlib.Path(sys.argv[1]) / 'wd-mind-e5-encoder-binding.json').read_bytes()
        runtime_binding = json.loads(runtime_binding_raw)
        identity = runtime_binding['encoder']
        encoder_binding = canonical({'schema':'wd.mind-readonly-encoder-binding.v1','encoder':identity,
            'code_sha256':(runtime_binding['code_sha256'] if mode.startswith('handler') else '2'*64), 'query_policy':{'prefix':'query: ', 'truncate_chars':2000,
            'normalize':True, 'dtype':'float32', 'dim':384}})
        row = struct.pack('<384f', 1.0, *([0.0]*383))
        def mutate(stage):
            if mode == 'package_source_' + stage:
                path = pathlib.Path(sys.argv[3]); path.write_bytes(path.read_bytes().replace(b'ordinary', b'Ordinary'))
            if mode == 'package_bundle_' + stage:
                (pathlib.Path(sys.argv[1]) / 'wd-mind-message-policy.json').write_bytes(b'{}')
            if mode == 'package_provider_' + stage: raise RuntimeError('synthetic provider failure')
            if mode == 'package_memory_repin_' + stage:
                opposite = struct.pack('<384f', -1.0, *([0.0]*383))
                product.vectors = opposite * len(product.ids)
                changed = json.loads(product.manifest)
                changed.update(runtime._flat_hashes(product.ids, product.vectors))
                product.manifest = canonical(changed)
        def passage(texts):
            mutate('passage')
            if mode == 'package_general':
                other = struct.pack('<384f', 0.0, 1.0, *([0.0]*382))
                return b''.join(row if 'synthetic external note' in text else other for text in texts)
            return row * len(texts)
        if mode.startswith('handler'):
            import threading
            from tools import wd_mind_e5_client, wd_mind_e5_rotating_client
            bulk = wd_mind_e5_rotating_client.BULK_CONTRACT_SHA256
            busy_entered, busy_release = threading.Event(), threading.Event()
            class FakeOwner:
                def __init__(self, number): self.owned, self.state, self._bulk_contract_sha256, self.number = True, 'RUNNING', bulk, number
                def status(self): return {'state':self.state, 'owned':self.owned, 'exit_code':None}
                def shutdown(self, timeout=0):
                    if (mode in ('handler_hold', 'handler_build_hold', 'handler_fast_hold') or
                            (mode in ('handler_refresh_hold', 'handler_auto_hold') and self.number == 1) or
                            (mode == 'handler_refresh_failure_hold' and self.number == 2)): self.state = 'HOLD'
                    else: self.state, self.owned = 'EXITED', False
                    return {'state':self.state, 'owned':self.owned, 'exit_code':0 if not self.owned else None}
            starts = []
            audit = {'provider_calls':0,'changed':False,'original_rows':None,'foreign_calls':0}
            def factory():
                owner = FakeOwner(len(starts) + 1)
                starts.append(owner)
                client = object.__new__(wd_mind_e5_client.GracefulE5WorkerClient)
                client.owner, client._binding_sha256, client._closed, client._next_id = owner, sha(runtime_binding_raw), False, 1
                client._used_nonces = set()
                client._bulk_contract_sha256 = bulk
                def supervised(transport):
                    if mode in ('handler_fast_mid_hash','handler_fast_rebind'):
                        audit['provider_calls'] += 1
                    if mode == 'handler_fast_rebind':
                        fence = handler._rotator._build_source.__self__
                        if audit['original_rows'] is None:
                            audit['original_rows'] = fence.rows
                            fence.rows = fence.rows[:16] + tuple((key,'passage: foreign synthetic material')
                                                                 for key,text in fence.rows[16:])
                            fence.rows_sha = reader._sha(reader._canonical(fence.rows))
                        if any(text == 'passage: foreign synthetic material' for text in transport['passages']):
                            audit['foreign_calls'] += 1
                            if fence.position == len(fence.chunks)-1:
                                fence.rows = audit['original_rows']
                                fence.rows_sha = reader._sha(reader._canonical(fence.rows))
                                fence.batch = tuple(text for key,text in fence.rows[fence.consumed:fence.consumed+16])
                                fence.batch_sha = reader._sha(reader._canonical(fence.batch))
                                fence.chunks = tuple(fence.batch[i:i+4] for i in range(0,len(fence.batch),4))
                    if mode == 'handler_auto_busy' and owner.number == 2 and transport['operation'] == 'refresh':
                        busy_entered.set()
                        if not busy_release.wait(15): raise RuntimeError('synthetic deadline')
                    if ((mode == 'handler_build_hold' or (mode in ('handler_refresh_failure', 'handler_refresh_failure_hold') and owner.number == 2))
                            and transport['operation'] == 'refresh'):
                        raise RuntimeError('synthetic failure')
                    if mode == 'handler_refresh_mutation' and owner.number == 2 and transport['operation'] == 'refresh':
                        path = pathlib.Path(sys.argv[3]); path.write_bytes(path.read_bytes().replace(b'ordinary', b'Ordinary'))
                    if mode == 'handler_historical_source_mutation' and transport['operation'] == 'query':
                        path = pathlib.Path(sys.argv[3]); path.write_bytes(path.read_bytes().replace(b'ordinary', b'Ordinary'))
                    if mode == 'handler_historical_provider_error' and transport['operation'] == 'query':
                        raise ValueError('synthetic provider error')
                    if mode == 'handler_historical_bundle_mutation' and transport['operation'] == 'query':
                        (pathlib.Path(sys.argv[1]) / 'wd-mind-message-policy.json').write_bytes(b'{}')
                    if mode == 'handler_fast_source_mutation' and transport['operation'] == 'refresh':
                        path = pathlib.Path(sys.argv[3]); path.write_bytes(path.read_bytes().replace(b'ordinary', b'Ordinary'))
                    if mode == 'handler_fast_bundle_mutation' and transport['operation'] == 'refresh':
                        (pathlib.Path(sys.argv[1]) / 'wd-mind-message-policy.json').write_bytes(b'{}')
                    if mode == 'handler_fast_snapshot_mutation' and transport['operation'] == 'refresh':
                        handler._snapshot['projection_bytes'] = b'changed'
                    if mode == 'handler_fast_request_mutation' and transport['operation'] == 'refresh':
                        transport['passages'][0] = 'passage: modified synthetic text'
                    if mode in ('handler_fast_provider_error','handler_fast_hold') and transport['operation'] == 'refresh':
                        raise ValueError('synthetic failure')
                    if mode == 'handler_source_mutation':
                        path = pathlib.Path(sys.argv[3]); path.write_bytes(path.read_bytes().replace(b'ordinary', b'Ordinary'))
                    if mode == 'handler_request_mutation' and transport['operation'] == 'query':
                        request['query'] = 'changed synthetic query'
                    if mode == 'handler_mapper_mutation' and transport['operation'] == 'query':
                        wire['scope'] = 'shared'
                    if mode == 'handler_owner_hold' and transport['operation'] == 'query': owner.state = 'HOLD'
                    if mode == 'handler_binding_mutation' and transport['operation'] == 'query':
                        (pathlib.Path(sys.argv[1]) / 'wd-mind-e5-encoder-binding.json').write_bytes(b'{}')
                    count = len(transport['passages']) if transport['operation'] == 'refresh' else 1
                    return {'nonce':transport['nonce'], 'request_sha256':transport['request_sha256'], 'vector_bytes':row * count}
                client.encode_supervised = supervised
                client.close = lambda timeout=0: owner.shutdown(timeout=timeout)
                return {'status':'READY', 'owner':None if mode == 'handler_missing_factory_owner' else owner,
                        'client':client, 'bulk_contract_sha256':bulk}
            bound_lane = 'claude-rco-1' if mode in ('handler_rco', 'handler_rco_shared', 'handler_abstain_hold') else 'codex-lead-1'
            if mode in ('handler_coordinator','handler_coordinator_fast'):
                from tools import wd_mind_runtime_process as process
                class SyntheticService:
                    def __init__(self, callback): self.callback = callback
                    def start(self): return {'state':'STARTING','owned':True}
                    def status(self): return {'state':'RECEIVING','owned':True}
                    def stop(self, *, timeout=0): return {'state':'STOPPED','owned':False}
                if mode == 'handler_coordinator_fast':
                    from tools import wd_mind_runtime_child as child
                    synthetic_audit = pathlib.Path(sys.argv[1]).parent / 'synthetic-coordinator-audit'
                    (synthetic_audit / 'wd-mind-runtime').mkdir(parents=True)
                    child._AUDIT_ROOT = synthetic_audit
                    runtime._fixed_worker_audit_lock = lambda: synthetic_audit / 'wd-mind-runtime' / 'q4-reader.lock'
                    original_factory = factory
                    pinned_factory = runtime.create_manifest_worker_factory(enabled=True, bundle_root=sys.argv[1],
                        expected_manifest_sha256=sys.argv[2], worker_timeout_seconds=5, lock_timeout_seconds=1,
                        bulk_contract_sha256=bulk)
                    runtime._ManifestWorkerFactory.__call__ = lambda self: original_factory()
                    factory = pinned_factory
                runtime.create_manifest_worker_factory = lambda **kwargs: factory
                process.create_candidate_service = lambda **kwargs: SyntheticService(kwargs['handler'])
                coordinator = runtime.create_manifest_coordinator(enabled=True, bundle_root=sys.argv[1],
                    expected_manifest_sha256=sys.argv[2], lane='codex-lead-1', worker_timeout_seconds=5,
                    lock_timeout_seconds=1, shutdown_timeout=1, refresh_interval_seconds=3600,
                    bulk_contract_sha256=bulk, fast_build=(mode == 'handler_coordinator_fast'))
                coordinator.start()
                wire = {'schema':'wd.mind-pipe-request.v1','query_id':'startup_work_overview',
                        'nonce':'d'*64,'lane':'codex-lead-1','scope':'own'}
                candidates = coordinator._service.callback(wire)
                ok = bool(candidates) and all(set(item) == {'ref_sha256','doc_id_sha256','source_event_sha256'}
                                              for item in candidates)
                status = coordinator.status()['state']
                closed = coordinator.stop(timeout=1)['state']
                print(json.dumps({'result':'ok','status':status,'safe':ok,'closed':closed,'starts':len(starts)}))
                sys.exit(0)
            if (mode.startswith('handler_historical') and mode != 'handler_historical_untrusted') or (mode.startswith('handler_fast') and mode != 'handler_fast_untrusted'):
                from tools import wd_mind_runtime_child as child
                synthetic_audit = pathlib.Path(sys.argv[1]).parent / 'synthetic-history-audit'
                (synthetic_audit / 'wd-mind-runtime').mkdir(parents=True)
                child._AUDIT_ROOT = synthetic_audit
                runtime._fixed_worker_audit_lock = lambda: synthetic_audit / 'wd-mind-runtime' / 'q4-reader.lock'
                original_factory = factory
                pinned_factory = runtime.create_manifest_worker_factory(enabled=True, bundle_root=sys.argv[1],
                    expected_manifest_sha256=sys.argv[2], worker_timeout_seconds=5, lock_timeout_seconds=1,
                    bulk_contract_sha256=bulk)
                runtime._ManifestWorkerFactory.__call__ = lambda self: original_factory()
                factory = pinned_factory
            handler = runtime.create_flat_request_handler(enabled=True, binding=binding, lane=bound_lane,
                worker_factory=factory, shutdown_timeout=1, bulk_contract_sha256=bulk)
            if mode.startswith('handler_fast'):
                if mode == 'handler_fast_mid_hash':
                    import inspect
                    actual_open = runtime.bridge._open_log
                    class RaceRead:
                        def __init__(self, stream): self.stream=stream
                        def __getattr__(self, name): return getattr(self.stream,name)
                        def __enter__(self): return self
                        def __exit__(self,*args): return self.stream.__exit__(*args)
                        def read(self,size=-1):
                            data=self.stream.read(size)
                            if size > 1 and not audit['changed']:
                                frame=inspect.currentframe()
                                while frame and frame.f_code.co_name != '_check_source': frame=frame.f_back
                                if frame:
                                    audit['changed']=True
                                    path=pathlib.Path(sys.argv[3]); content=path.read_bytes()
                                    with path.open('r+b') as writer:
                                        writer.seek(content.index(b'ordinary'));writer.write(b'O')
                            return data
                    def race_open(path):
                        stream=actual_open(path)
                        return RaceRead(stream) if pathlib.Path(path)==pathlib.Path(sys.argv[3]) else stream
                    runtime.bridge._open_log=race_open
                if mode == 'handler_fast_postvalidation':
                    def refused_source(self): raise ValueError('synthetic final validation failure')
                    runtime._FlatRequestHandler._source_check = refused_source
                try:
                    result = runtime.build_fast_fenced_owner(enabled=(mode != 'handler_fast_disabled'),handler=handler)
                    inspection = handler.owner_status()
                    closed = handler.close()
                    print(json.dumps({'result':'ok','build':result['status'],'owner':inspection['state'],
                                      'closed':closed['state'],'starts':len(starts)}))
                except Exception:
                    state = handler.owner_status()
                    no_partial = handler._memory is None
                    failed = handler._failed
                    closed = handler.close()
                    if mode in ('handler_fast_hold','handler_fast_postvalidation'):
                        print(json.dumps({'result':'ok','owner':state['state'],'owned':state['owned'],
                                          'close':closed['state'],'no_partial':no_partial,'failed':failed}))
                    elif mode in ('handler_fast_mid_hash','handler_fast_rebind'):
                        print(json.dumps({'result':'refused','starts':len(starts),'provider_calls':audit['provider_calls'],
                                          'changed':audit['changed'],'foreign_calls':audit['foreign_calls'],
                                          'no_partial':no_partial}))
                    else:
                        print(json.dumps({'result':'refused'}))
                sys.exit(0)
            if mode == 'handler_build_hold':
                try: handler.build()
                except Exception:
                    close = handler.close()
                    print(json.dumps({'result':'ok', 'state':close['state'], 'owned':close['owned'],
                                      'inspection':handler.owner_status()}))
                    sys.exit(0)
                raise ValueError('synthetic build should refuse')
            if mode == 'handler_missing_factory_owner':
                try: handler.build()
                except Exception:
                    inspection = handler.owner_status()
                    retained_owner = handler._rotator._client.owner
                    close = handler.close()
                    print(json.dumps({'result':'ok', 'inspection':inspection,
                                      'retained_owned':retained_owner.owned, 'close':close}))
                    retained_owner.shutdown(); sys.exit(0)
                raise ValueError('malformed factory result accepted')
            handler.build()
            if mode.startswith('handler_historical'):
                try:
                    result = runtime.query_historical_flat_memory(enabled=(mode != 'handler_historical_disabled'),
                        handler=handler,lane='codex-lead-1',own_lane_only=True,query='synteettinen muistihaku')
                    closed = handler.close()
                    print(json.dumps({'result':'ok','status':result['status'],'scope':result['scope'],
                        'old':any(hit['age_seconds'] > 899 for hit in result['results']),
                        'safe':all('stored_text' not in hit and 'display_text' in hit and
                                   'synthetic rco' not in hit['display_text'] and 'password=' not in hit['display_text']
                                   for hit in result['results']), 'closed':closed['state']}))
                except Exception:
                    handler.close()
                    print(json.dumps({'result':'refused'}))
                sys.exit(0)
            if mode.startswith('handler_mapper'):
                target = 'claude-rco-1' if mode in ('handler_mapper_rco_own','handler_mapper_rco_shared') else 'codex-lead-1'
                wire = {'schema':'wd.mind-pipe-request.v1', 'query_id':'startup_work_overview',
                        'nonce':'d'*64, 'lane':target, 'scope':'shared' if mode == 'handler_mapper_rco_shared' else 'own'}
                if mode == 'handler_mapper_bad_schema': wire['schema'] = 'wd.bad.v1'
                if mode == 'handler_mapper_bad_nonce': wire['nonce'] = 'D'*64
                if mode == 'handler_mapper_bad_query': wire['query_id'] = 'free_query'
                if mode == 'handler_mapper_bad_lane': wire['lane'] = 'unknown'
                if mode == 'handler_mapper_bad_scope': wire['scope'] = 'bad'
                if mode == 'handler_mapper_extra': wire['text'] = 'not allowed'
                if mode == 'handler_mapper_hold': starts[-1].state = 'HOLD'
                expected = None
                if mode in ('handler_mapper', 'handler_mapper_rco_own', 'handler_mapper_rco_shared'):
                    direct = handler.lane_view(target).handle({'schema':'wd.mind-flat-owner-request.v1',
                        'lane':target,'scope':wire['scope'],'query':'current task active work next step recent status'})
                    expected = [{'ref_sha256':hit['ref_sha256'],'doc_id_sha256':hit['doc_id_sha256'],
                                 'source_event_sha256':hit['source_reference']['sha256']}
                                for hit in direct['result']['results']]
                mapped = runtime.map_pipe_candidates(enabled=(mode != 'handler_mapper_disabled'), handler=handler, request=wire)
                safe = (set(mapped) == {'schema','nonce','lane','scope','status','candidates'} and
                        all(set(item) == {'ref_sha256','doc_id_sha256','source_event_sha256'} for item in mapped['candidates']))
                closed = handler.close()
                print(json.dumps({'result':'ok','status':mapped['status'],'safe':safe,
                                  'matches':mapped['candidates'] == expected,'nonce':mapped['nonce'] == 'd'*64,
                                  'lane':mapped['lane'],'scope':mapped['scope'],'closed':closed['state']})); sys.exit(0)
            if mode.startswith('handler_auto'):
                import time, datetime
                if mode == 'handler_auto_disabled':
                    try: runtime.create_flat_auto_refresh(handler=handler, interval_seconds=0.05)
                    except Exception:
                        close = handler.close()
                        print(json.dumps({'result':'ok','closed':close['state'],'starts':len(starts)})); sys.exit(0)
                    raise ValueError('default must be disabled')
                if mode == 'handler_auto_duplicate':
                    control = runtime.create_flat_auto_refresh(enabled=True, handler=handler, interval_seconds=0.05)
                    try: runtime.create_flat_auto_refresh(enabled=True, handler=handler, interval_seconds=0.05); duplicate = False
                    except Exception: duplicate = True
                    busy = handler.close()['state']
                    first = control.stop(timeout=0)
                    other = runtime.create_flat_auto_refresh(enabled=True, handler=handler, interval_seconds=0.05)
                    second = other.stop(timeout=0)
                    close = handler.close()
                    print(json.dumps({'result':'ok', 'duplicate_rejected':duplicate, 'close_while_reserved':busy,
                                      'first':first['state'], 'second':second['state'], 'closed':close['state']})); sys.exit(0)
                path = pathlib.Path(sys.argv[3]); event = json.loads(path.read_bytes().splitlines()[0])
                event['message'] = 'refreshed synthetic clue marker'
                event['ts_utc'] = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=2)).isoformat().replace('+00:00','Z')
                with path.open('ab') as output: output.write(canonical(event)+b'\n')
                control = runtime.create_flat_auto_refresh(enabled=True, handler=handler, interval_seconds=0.05)
                control.start()
                if mode == 'handler_auto_busy':
                    if not busy_entered.wait(15): raise ValueError('test did not enter provider')
                    first = control.stop(timeout=0)
                    busy_release.set()
                    second = control.stop(timeout=15)
                    close = handler.close()
                    print(json.dumps({'result':'ok','first':first['state'],'second':second['state'],
                                      'starts':len(starts),'closed':close['state']})); sys.exit(0)
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    state = control.status()
                    if state['last_result'] == 'REFRESHED' or state['state'] == 'REFUSED': break
                    time.sleep(0.05)
                state = control.stop(timeout=15)
                inspection = handler.owner_status()
                if mode == 'handler_auto_hold':
                    print(json.dumps({'result':'ok','state':state['state'],'owner':inspection,
                                      'starts':len(starts)})); sys.exit(0)
                response = handler.handle({'schema':'wd.mind-flat-owner-request.v1','lane':'codex-lead-1',
                                           'query':'refreshed clue','scope':'own'})
                found = any('refreshed synthetic clue marker' in hit['stored_text'] for hit in response['result']['results'])
                close = handler.close()
                print(json.dumps({'result':'ok','state':state['state'],'found':found,
                                  'starts':len(starts),'closed':close['state']})); sys.exit(0)
            if mode.startswith('handler_refresh'):
                before = handler._memory
                if mode == 'handler_refresh_concurrent':
                    import threading
                    seen = []
                    handler._lock.acquire()
                    def concurrent():
                        try: handler.refresh(); seen.append('accepted')
                        except Exception: seen.append('refused')
                    thread = threading.Thread(target=concurrent); thread.start(); thread.join(5)
                    handler._lock.release()
                    close = handler.close()
                    print(json.dumps({'result':'ok', 'concurrent':seen, 'starts':len(starts), 'closed':close['state']}))
                    sys.exit(0)
                if mode != 'handler_refresh_unchanged':
                    import datetime
                    path = pathlib.Path(sys.argv[3]); event = json.loads(path.read_bytes().splitlines()[0])
                    event['message'] = 'refreshed synthetic clue marker'
                    event['ts_utc'] = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=2)).isoformat().replace('+00:00', 'Z')
                    with path.open('ab') as output: output.write(canonical(event) + b'\n')
                try:
                    refreshed = handler.refresh()
                    if mode in ('handler_refresh_hold', 'handler_refresh_failure', 'handler_refresh_failure_hold', 'handler_refresh_mutation'):
                        raise ValueError('refresh should fail')
                    if mode == 'handler_refresh_unchanged':
                        close = handler.close()
                        print(json.dumps({'result':'ok', 'refresh':refreshed['status'],
                                          'starts':len(starts), 'closed':close['state']})); sys.exit(0)
                    response = handler.handle({'schema':'wd.mind-flat-owner-request.v1','lane':'codex-lead-1',
                                               'query':'refreshed clue','scope':'own'})
                    found = any('refreshed synthetic clue marker' in hit['stored_text']
                                for hit in response['result']['results'])
                    close = handler.close()
                    print(json.dumps({'result':'ok', 'refresh':refreshed['status'], 'found':found,
                                      'starts':len(starts), 'closed':close['state']})); sys.exit(0)
                except Exception:
                    if mode not in ('handler_refresh_hold', 'handler_refresh_failure', 'handler_refresh_failure_hold', 'handler_refresh_mutation'):
                        raise
                    try:
                        handler.handle({'schema':'wd.mind-flat-owner-request.v1','lane':'codex-lead-1',
                                        'query':'refreshed clue','scope':'own'})
                        refused = False
                    except Exception: refused = True
                    inspection = handler.owner_status()
                    print(json.dumps({'result':'ok','refused':refused,'retained':handler._memory is before,
                                      'starts':len(starts),'inspection':inspection})); sys.exit(0)
            if mode == 'handler_five':
                views = {lane:handler.lane_view(lane) for lane in
                         ('codex-lead-1','codex-tools-1','fable-5','claude-rco-1','claude-rco-2')}
                states = {}
                for target, view in views.items():
                    observed = view.handle({'schema':'wd.mind-flat-owner-request.v1', 'lane':target,
                                            'query':'yhteyskatko uudelleenyritys', 'scope':'own'})
                    states[target] = observed['result']['status']
                try:
                    views['codex-lead-1'].handle({'schema':'wd.mind-flat-owner-request.v1', 'lane':'fable-5',
                                                   'query':'yhteyskatko uudelleenyritys', 'scope':'own'})
                    forged = False
                except Exception: forged = True
                close = handler.close()
                print(json.dumps({'result':'ok', 'states':states, 'forged_rejected':forged,
                                  'closed':close['state'], 'starts':len(starts)}))
                sys.exit(0)
            if mode == 'handler_source_after_build':
                path = pathlib.Path(sys.argv[3]); path.write_bytes(path.read_bytes().replace(b'ordinary', b'Ordinary'))
            if mode == 'handler_abstain_hold': starts[-1].state = 'HOLD'
            request = {'schema':'wd.mind-flat-owner-request.v1', 'lane':bound_lane,
                       'query':'yhteyskatko uudelleenyritys', 'scope':'shared' if mode == 'handler_rco_shared' else 'own'}
            if mode == 'handler_wrong_lane': request['lane'] = 'fable-5'
            if mode == 'handler_extra': request['extra'] = True
            if mode == 'handler_wrong_schema': request['schema'] = 'wd.synthetic-wrong.v1'
            if mode == 'handler_wrong_scope': request['scope'] = 'unknown'
            if mode == 'handler_time_injection': request['as_of_utc'] = '2026-01-01T00:02:00Z'
            if mode == 'handler_input_race':
                import inspect
                source_lines, first = inspect.getsourcelines(runtime._FlatRequestHandler._handle_for_lane)
                freeze_line = first + next(i for i, line in enumerate(source_lines) if 'frozen = dict(request)' in line)
                def trace(frame, event, arg):
                    if (event == 'line' and frame.f_code is runtime._FlatRequestHandler._handle_for_lane.__code__
                            and frame.f_lineno == freeze_line):
                        request['scope'] = 'INVALID'
                        sys.settrace(None)
                    return trace
                sys.settrace(trace)
            response = handler.handle(request)
            close = handler.close()
            closed_rejected = False
            try: handler.handle(request)
            except Exception: closed_rejected = True
            print(json.dumps({'result':'ok', 'status':response['result']['status'],
                              'expiry':isinstance(response['expires_utc'], str),
                              'lane':response['lane'], 'owned':close['owned'], 'state':close['state'],
                              'scope':response['scope'], 'closed_rejected':closed_rejected,
                              'expiry_scope':response['expiry_scope'], 'snapshot_freshness':response['snapshot_freshness'],
                              'agents':[hit['source_agent'] for hit in response['result']['results']],
                              'latest':response['result']['own_latest_task']}))
            sys.exit(0)
        product = runtime.build_flat_memory(enabled=True, binding=(object() if mode == 'package_fake_binding' else binding),
            passage_encoder=passage, encoder_binding_bytes=encoder_binding, encoder_binding_sha256=sha(encoder_binding))
        def encode_query(text):
            mutate('query')
            if mode == 'package_orthogonal': return (0.0, 1.0) + (0.0,) * 382
            return (1.0,) + (0.0,) * 383
        if mode.startswith('package_historical'):
            if mode == 'package_historical_orthogonal':
                encode_query = lambda text: (0.0,1.0) + (0.0,)*382
            if mode == 'package_historical_mutation':
                def encode_query(text):
                    path = pathlib.Path(sys.argv[3]); path.write_bytes(path.read_bytes().replace(b'ordinary', b'Ordinary'))
                    return (1.0,) + (0.0,)*383
            if mode == 'package_historical_provider_error':
                def encode_query(text): raise ValueError('synthetic provider error')
            result = runtime._query_historical_flat_memory(enabled=(mode != 'package_historical_disabled'),
                memory=product, lane='codex-lead-1', own_lane_only=True,
                query='yhteyskatko uudelleenyritys', query_encoder=encode_query)
            import datetime
            ages_ok = all(hit['age_seconds'] > 899 and hit['source_time_utc'].startswith('2026-01-01')
                          for hit in result['results'])
            startup = runtime.query_flat_memory(enabled=True,memory=product,lane='codex-lead-1',own_lane_only=True,
                query='yhteyskatko uudelleenyritys',as_of_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                query_encoder=encode_query)
            print(json.dumps({'result':'ok','status':result['status'],'scope':result['scope'],
                'age_ok':ages_ok,'hits':len(result['results']),'startup':startup['status'],
                'authority':result['authority'],'freshness':result['snapshot_freshness'],
                'latest':result['own_latest_task'],'refs':all(h['source_reference']['verified']=='MATCH_WITHIN_PREFIX'
                                                            for h in result['results']),
                'safe':all('password=' not in h['display_text'] and 'synthetic rco' not in h['display_text']
                           and 'stored_text' not in h for h in result['results'])}));sys.exit(0)
        lane = 'claude-rco-1' if mode == 'package_rco' else ('codex-tools-1' if mode == 'package_other' else 'codex-lead-1')
        result = runtime.query_flat_memory(enabled=True, memory=product, lane=lane,
            own_lane_only=mode != 'package_general', query='yhteyskatko uudelleenyritys',
            as_of_utc=('2026-01-01T00:17:00Z' if mode == 'package_stale' else '2026-01-01T00:02:00Z'),
            query_encoder=encode_query)
        projection, _, _ = reader._verified_complete(binding.snapshot_for_query())
        records = {sha(record['record_id'].encode()): record for record in projection['messages']['records']}
        references_match = all(hit['doc_id_sha256'] in records and
            hit['source_reference']['sha256'] == records[hit['doc_id_sha256']]['provenance']['source_event_ref'] and
            hit['stored_text'] == records[hit['doc_id_sha256']]['stored_text'] and
            hit['source_agent'] == records[hit['doc_id_sha256']]['meta']['agent'] and
            hit['source_time_utc'] == records[hit['doc_id_sha256']]['time_utc'] for hit in result['results'])
        print(json.dumps({'result':'ok', 'status':result['status'], 'relevance':result.get('relevance'),
                          'hits':len(result['results']), 'agents':[hit['source_agent'] for hit in result['results']],
                          'refs':references_match and all(hit['source_reference']['verified'] == 'MATCH_WITHIN_PREFIX'
                                                           for hit in result['results']),
                          'latest':result['own_latest_task'], 'index_match':result['index_match']}))
        sys.exit(0)
    if mode == 'policy_after':
        (pathlib.Path(sys.argv[1]) / 'wd-mind-message-policy.json').write_bytes(b'{}')
    if mode == 'source_after':
        path = pathlib.Path(sys.argv[3])
        path.write_bytes(path.read_bytes().replace(b'ordinary', b'Ordinary'))
    snapshot = binding.snapshot_for_query()
    if mode == 'ephemeral':
        other = runtime.assemble_full_runtime(enabled=True, bundle_root=sys.argv[1], expected_manifest_sha256=sys.argv[2])
        print(json.dumps({'result':'ok', 'fresh': snapshot['identifier_policy_binding'][2] !=
                          other.snapshot_for_query()['identifier_policy_binding'][2]}))
        sys.exit(0)
    projection, _, report = reader._verified_complete(snapshot)
    print(json.dumps({'result':'ok','records':len(projection['messages']['records']),
                      'verified':report['status'], 'authority':binding.authority,
                      'safe_repr': repr(binding) == '<PrivateMindFullRuntimeBinding>'}))
except Exception:
    print(json.dumps({'result':'refused'}))
'''
    proc = subprocess.run([sys.executable, '-B', '-', str(case['bundle']), case['digest'],
                           str(case['source']), scenario], cwd=case['bundle'], input=code, capture_output=True, text=True,
                          timeout=30, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_synthetic_manifest_bound_runtime_accepts_screened_records(synthetic_bundle):
    result = invoke(synthetic_bundle)
    assert result == {'result': 'ok', 'records': 1, 'verified': 'MATCH', 'authority': 'none', 'safe_repr': True}


def test_manifest_runtime_location_verifies_both_synthetic_trees(synthetic_bundle):
    assert invoke(synthetic_bundle, 'location') == {'result':'ok', 'generation':'a'*40,
        'revision':'b'*40, 'interpreter_name':'python.exe', 'verified':True}


def test_manifest_worker_factory_uses_exact_bindings_and_preserves_hold(synthetic_bundle):
    result = invoke(synthetic_bundle, 'factory')
    assert result == {'result':'ok','calls':1,'status':'HOLD','same_owner':True,
                      'worker_only':True,'scratch_none':True,'lock_exact':True,'args_exact':True}


@pytest.mark.parametrize('scenario', ['factory_disabled', 'factory_missing_audit',
    'factory_asset_mutation', 'factory_manifest_mutation', 'factory_bad_origin', 'factory_bad_timeout',
    'factory_bad_lock_timeout', 'factory_bad_bulk', 'factory_audit_removed', 'factory_lock_changed'])
def test_manifest_worker_factory_refuses_before_spawn(synthetic_bundle, scenario):
    assert invoke(synthetic_bundle, scenario) == {'result':'refused','calls':0}


@pytest.mark.parametrize('fault', ['manifest', 'fleet', 'runtime_descriptor', 'identity_descriptor',
    'binding_descriptor', 'location_descriptor', 'policy_descriptor', 'model_asset', 'interpreter_asset',
    'missing_model_root', 'missing_interpreter_root'])
def test_manifest_runtime_location_refuses_mutation_or_missing_roots(synthetic_bundle, fault):
    case = synthetic_bundle
    if fault == 'manifest': case['digest'] = '0'*64
    if fault == 'fleet': (case['bundle'] / 'wd-fleet.json').write_bytes(b'{}')
    descriptor_files = {'runtime_descriptor':'wd-mind-runtime-manifest.json',
        'identity_descriptor':'wd-mind-interpreter-identity.json', 'binding_descriptor':'wd-mind-e5-encoder-binding.json',
        'location_descriptor':'wd-mind-runtime-location.json', 'policy_descriptor':'wd-mind-message-policy.json'}
    if fault in descriptor_files: (case['bundle'] / descriptor_files[fault]).write_bytes(b'{}')
    if fault == 'model_asset': (case['model_root'] / 'model/model.safetensors').write_bytes(b'changed')
    if fault == 'interpreter_asset': (case['interpreter_root'] / 'python.exe').write_bytes(b'changed')
    if fault == 'missing_model_root':
        (case['model_root'] / 'model/model.safetensors').unlink()
        (case['model_root'] / 'model').rmdir(); case['model_root'].rmdir()
    if fault == 'missing_interpreter_root':
        for relative in ('python.exe', 'python.dll', 'lib/os.py'):
            (case['interpreter_root'] / relative).unlink()
        (case['interpreter_root'] / 'lib').rmdir(); case['interpreter_root'].rmdir()
    assert invoke(case, 'location') == {'result':'refused'}


@pytest.mark.parametrize('fault', ['source_commit', 'runtime_cross_pin', 'identity_cross_pin',
    'binding_cross_pin', 'relative_root', 'overlapping_roots', 'wrong_model_revision',
    'duplicate_interpreter', 'wrong_worker_code_pin'])
def test_rehashed_descriptor_conflicts_still_refuse(synthetic_bundle, fault):
    case = synthetic_bundle
    bundle = case['bundle']
    manifest_path = bundle / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    def repin(name, value):
        raw = canonical(value); (bundle / name).write_bytes(raw)
        manifest['files'][name] = hashlib.sha256(raw).hexdigest()
    location = json.loads((bundle / 'wd-mind-runtime-location.json').read_bytes())
    binding = json.loads((bundle / 'wd-mind-e5-encoder-binding.json').read_bytes())
    if fault == 'source_commit': location['source_commit'] = 'd'*40
    if fault == 'runtime_cross_pin': location['runtime_manifest_sha256'] = '0'*64
    if fault == 'identity_cross_pin': location['interpreter_identity_sha256'] = '0'*64
    if fault == 'binding_cross_pin': location['encoder_binding_sha256'] = '0'*64
    if fault == 'relative_root': location['runtime_root'] = 'relative-root'
    if fault == 'overlapping_roots': location['runtime_root'] = location['interpreter_root']
    if fault == 'wrong_model_revision':
        binding['encoder']['revision'] = 'd'*40
        repin('wd-mind-e5-encoder-binding.json', binding)
        location['encoder_binding_sha256'] = manifest['files']['wd-mind-e5-encoder-binding.json']
    if fault == 'wrong_worker_code_pin':
        binding['code_sha256'] = '0'*64
        repin('wd-mind-e5-encoder-binding.json', binding)
        location['encoder_binding_sha256'] = manifest['files']['wd-mind-e5-encoder-binding.json']
    if fault == 'duplicate_interpreter':
        identity = json.loads((bundle / 'wd-mind-interpreter-identity.json').read_bytes())
        identity['files'].append(dict(identity['files'][0]))
        repin('wd-mind-interpreter-identity.json', identity)
        location['interpreter_identity_sha256'] = manifest['files']['wd-mind-interpreter-identity.json']
    repin('wd-mind-runtime-location.json', location)
    raw = canonical(manifest); manifest_path.write_bytes(raw); case['digest'] = hashlib.sha256(raw).hexdigest()
    assert invoke(case, 'location') == {'result':'refused'}


@pytest.mark.parametrize('fault', ['disabled', 'manifest', 'fleet', 'policy', 'code', 'sidecar', 'rewrite', 'rotation',
                                   'policy_during', 'policy_after', 'source_after'])
def test_synthetic_manifest_runtime_refuses_mutation_or_rotation(synthetic_bundle, fault):
    case = synthetic_bundle
    if fault == 'manifest': case['digest'] = '0' * 64
    if fault == 'fleet': (case['bundle'] / 'wd-fleet.json').write_bytes(b'{}')
    if fault == 'policy': (case['bundle'] / 'wd-mind-message-policy.json').write_bytes(b'{}')
    if fault == 'code':
        with (case['bundle'] / 'tools-bootstrap' / 'tools' / 'wd_mind_content_query_readonly.py').open('ab') as stream:
            stream.write(b'\n# synthetic tampering\n')
    if fault == 'sidecar': case['source'].with_name('events.generation.json').write_bytes(b'{}')
    assert invoke(case, fault) == {'result': 'refused'}


def test_each_private_binding_gets_a_fresh_ephemeral_hmac_key(synthetic_bundle):
    assert invoke(synthetic_bundle, 'ephemeral') == {'result': 'ok', 'fresh': True}


def test_synthetic_private_binding_build_package_and_query(synthetic_bundle):
    result = invoke(synthetic_bundle, 'package')
    assert result['result'] == 'ok' and result['status'] == 'SEMANTIC_CANDIDATE'
    assert result['relevance'] == 'UNVERIFIED' and result['hits'] > 0
    assert set(result['agents']) == {'codex-lead-1'} and result['refs']
    assert result['latest'] == 'UNKNOWN' and result['index_match'] == 'NOT_APPLICABLE'


@pytest.mark.parametrize('scenario', ['package_rco', 'package_other'])
def test_own_lane_query_does_not_claim_another_lanes_work(synthetic_bundle, scenario):
    result = invoke(synthetic_bundle, scenario)
    assert result['status'] == 'ABSTAIN' and result['hits'] == 0 and result['latest'] == 'UNKNOWN'


def test_general_query_reports_own_task_unknown_for_another_lanes_hit(synthetic_bundle):
    result = invoke(synthetic_bundle, 'package_general')
    assert result['status'] == 'SEMANTIC_CANDIDATE' and result['agents'] == ['fable-5']
    assert result['latest'] == 'UNKNOWN' and result['refs']












































@pytest.mark.parametrize('scenario', ['package_orthogonal', 'package_stale'])
def test_flat_query_abstains_on_orthogonal_or_stale_hits(synthetic_bundle, scenario):
    result = invoke(synthetic_bundle, scenario)
    assert result['status'] == 'ABSTAIN' and result['hits'] == 0


@pytest.mark.parametrize('fault', ['package_fake_binding', 'package_source_passage', 'package_bundle_passage',
    'package_provider_passage', 'package_source_query', 'package_bundle_query', 'package_provider_query',
    'package_memory_repin_query'])
def test_synthetic_in_memory_package_refuses_provider_or_binding_changes(synthetic_bundle, fault):
    assert invoke(synthetic_bundle, fault) == {'result': 'refused'}


@pytest.mark.parametrize('fault', ['missing_removals', 'missing_lane', 'wrong_scope', 'unknown_type'])
def test_manifest_pinned_invalid_content_policy_is_not_accepted(synthetic_bundle, fault):
    case = synthetic_bundle
    path = case['bundle'] / 'wd-mind-message-policy.json'
    policy = json.loads(path.read_bytes())
    if fault == 'missing_removals': policy.pop('removed_source_refs')
    if fault == 'missing_lane': policy['lanes'].pop()
    if fault == 'wrong_scope': policy['task_scope'] = 'selected'
    if fault == 'unknown_type': policy['types'].append('synthetic-unknown')
    raw = canonical(policy)
    path.write_bytes(raw)
    manifest_path = case['bundle'] / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['files']['wd-mind-message-policy.json'] = hashlib.sha256(raw).hexdigest()
    manifest_raw = canonical(manifest)
    manifest_path.write_bytes(manifest_raw)
    case['digest'] = hashlib.sha256(manifest_raw).hexdigest()
    assert invoke(case) == {'result': 'refused'}















@pytest.mark.parametrize('elevated', [False, True])
def test_owner_preflight_explicit_limited_token_branch(monkeypatch, elevated):
    from tools import wd_mind_full_runtime as runtime
    from tools import wd_mind_runtime_process as process
    calls = []
    class FakeNative:
        def __init__(self): self.handles, self.allocations = [], []
        def logon_sid(self, *, require_non_elevated):
            calls.append(require_non_elevated)
            if elevated: raise ValueError('synthetic elevated')
            return 'S-1-5-5-1-2'
    monkeypatch.setattr(runtime, '_bundle_inputs', lambda *args: None)
    monkeypatch.setattr(runtime, 'resolve_manifest_runtime_location', lambda **kw: None)
    monkeypatch.setattr(runtime, '_worker_factory_preflight', lambda *args: None)
    monkeypatch.setattr(process, '_PipeNative', FakeNative)
    assert runtime._owner_preflight(bundle_root=str(Path.cwd()), manifest_sha256='a'*64) is (not elevated)
    assert calls == [True]


@pytest.mark.parametrize('scenario,accepted,calls', [
    ('owner_preflight_limited',True,[True]), ('owner_preflight_admin',False,[True]),
    ('owner_preflight_bad_pin',False,[])])
def test_owner_preflight_synthetic_pinned_bundle(synthetic_bundle, scenario, accepted, calls):
    assert invoke(synthetic_bundle, scenario) == {'result':'ok','accepted':accepted,'token_calls':calls}














































def test_single_owner_interrupt_during_spawn_preserves_owned_hold(tmp_path, monkeypatch):
    import threading
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    entered, release = threading.Event(), threading.Event()
    class Held:
        def status(self): return {'state': 'RUNNING', 'owned': True}
        def shutdown(self, *, timeout): return {'state': 'HOLD', 'owned': True}
    held = Held()
    def launch(self, *, owner_sink):
        owner_sink(held)
        entered.set()
        assert release.wait(5)
        return {'status': 'HOLD', 'client': None, 'owner': held}
    def interrupt(self):
        assert entered.wait(2)
        raise KeyboardInterrupt()
    monkeypatch.setattr(runtime._ManifestWorkerFactory, '__call__', launch)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_wait_for_launch', interrupt, raising=False)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    try:
        with pytest.raises(KeyboardInterrupt):
            owner.start()
        assert owner.status() == {'state': 'HOLD', 'owned': True, 'authority': 'none'}
        assert owner._owner is held
        assert owner.close() == {'state': 'HOLD', 'owned': True, 'authority': 'none'}
    finally:
        release.set()
        if getattr(owner, '_launch_thread', None) is not None:
            owner._launch_thread.join(timeout=2)



@pytest.mark.parametrize('entrypoint', ['handler', 'refresh', 'fast', 'coordinator', 'entrypoint'])
def test_deferred_legacy_rotator_routes_refuse_without_starting_a_worker(monkeypatch, entrypoint):
    from tools import wd_mind_full_runtime as runtime
    if entrypoint == 'entrypoint':
        import io
        output = io.StringIO()
        assert runtime._owner_entrypoint([], io.StringIO(), output) != 0
        assert 'READY' not in output.getvalue()
        return
    if entrypoint == 'handler': call = lambda: runtime.create_flat_request_handler(enabled=True)
    elif entrypoint == 'fast': call = lambda: runtime.build_fast_fenced_owner(enabled=True)
    elif entrypoint == 'coordinator': call = lambda: runtime.create_manifest_coordinator(enabled=True)
    else:
        handler = object.__new__(runtime._FlatRequestHandler)
        call = handler.refresh
    with pytest.raises(runtime.FullRuntimeRefused, match='^mind_full_runtime_refused$'):
        call()


def test_single_owner_package_has_unchanged_static_closure_without_rotator():
    from test_bridge_code_package_closure import _closure, _packaged_python, _definition
    omitted = 'tools/wd_mind_e5_rotating_client.py'
    entries = _packaged_python()
    assert omitted not in entries
    assert 'mind_guardian' not in _definition()['python_entrypoints']
    gaps, unresolved = _closure(entries)
    assert not gaps, gaps
    assert not unresolved, unresolved


def test_single_owner_retains_both_owners_on_mismatched_launch_result(tmp_path, monkeypatch):
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    class Owner:
        def status(self): return {'state':'RUNNING','owned':True}
        def shutdown(self, *, timeout): return {'state':'HOLD','owned':True}
    sink_owner, returned_owner = Owner(), Owner()
    class Client:
        owner = returned_owner
    def launch(self, *, owner_sink):
        owner_sink(sink_owner)
        return {'status':'READY', 'client':Client(), 'owner':returned_owner}
    monkeypatch.setattr(runtime._ManifestWorkerFactory, '__call__', launch)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    assert owner.start() == {'state':'HOLD','owned':None,'authority':'none'}
    assert owner.close() == {'state':'HOLD','owned':None,'authority':'none'}
    assert owner._owner is sink_owner and owner._launch['owner'] is returned_owner
    assert owner._client is None


def test_single_owner_retains_conflicting_sink_owners(tmp_path, monkeypatch):
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    class Owner:
        def status(self): return {'state':'RUNNING','owned':True}
    first, second = Owner(), Owner()
    def launch(self, *, owner_sink):
        owner_sink(first)
        owner_sink(second)
        pytest.fail('duplicate owner must refuse')
    monkeypatch.setattr(runtime._ManifestWorkerFactory, '__call__', launch)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    assert owner.start() == {'state':'HOLD','owned':None,'authority':'none'}
    assert owner._owner is first and owner._owner_conflicts == [second]
    assert owner.close() == {'state':'HOLD','owned':None,'authority':'none'}


def test_cancelled_single_owner_retains_later_conflicting_factory_owner(tmp_path, monkeypatch):
    import threading
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    entered, release = threading.Event(), threading.Event()
    class Owner:
        def status(self): return {'state':'RUNNING','owned':True}
        def shutdown(self, *, timeout): return {'state':'EXITED','owned':False}
    first, second = Owner(), Owner()
    def launch(self, *, owner_sink):
        owner_sink(first)
        entered.set()
        assert release.wait(5)
        return {'status':'READY', 'owner':second, 'client':None}
    def interrupt(self):
        assert entered.wait(2)
        raise KeyboardInterrupt()
    monkeypatch.setattr(runtime._ManifestWorkerFactory, '__call__', launch)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_wait_for_launch', interrupt)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    try:
        with pytest.raises(KeyboardInterrupt): owner.start()
        release.set()
        owner._launch_thread.join(timeout=5)
        assert not owner._launch_thread.is_alive()
        assert owner._owner is first and owner._launch['owner'] is second
        assert second in owner._owner_conflicts
        assert owner.close() == {'state':'HOLD','owned':None,'authority':'none'}
    finally:
        release.set()
        if owner._launch_thread is not None: owner._launch_thread.join(timeout=5)


@pytest.mark.parametrize('signal_name', ['keyboard','cancelled'])
def test_export_cancellation_never_exposes_inner_secret_traceback(tmp_path, monkeypatch, signal_name):
    import asyncio, traceback
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', lambda self: {
        'schema':'wd.mind-flat-owner-build.v1','status':'BUILT_IN_MEMORY','authority':'none'})
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    assert owner.start()['state'] == 'READY'
    signal = KeyboardInterrupt if signal_name == 'keyboard' else asyncio.CancelledError
    fake_key = b'synthetic-policy-key-32-bytes!!!'
    def interrupted(memory):
        inner_private = fake_key
        try:
            raise ValueError(fake_key)
        except ValueError:
            raise signal(fake_key)
    monkeypatch.setattr(runtime, '_flat_check', interrupted)
    with pytest.raises(signal) as captured:
        owner.prepare_consumer_export(enabled=True, expected_nonce='e'*64, expected_identifier_policy_sha256='a'*64, max_total_bytes=1000)
    trace = traceback.TracebackException.from_exception(captured.value, capture_locals=True)
    owned_frames = [frame for frame in trace.stack if frame.name != 'test_export_cancellation_never_exposes_inner_secret_traceback']
    assert owned_frames
    assert captured.value.__context__ is None and captured.value.args == ()
    assert all(repr(fake_key) not in value for frame in owned_frames for value in (frame.locals or {}).values())
    assert owner.status()['state'] == 'HOLD'


@pytest.mark.parametrize('signal_name', ['keyboard','cancelled','generator'])
def test_export_cancellation_subclass_constructor_never_runs_again(tmp_path, monkeypatch, signal_name):
    import asyncio
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', lambda self: {
        'schema':'wd.mind-flat-owner-build.v1','status':'BUILT_IN_MEMORY','authority':'none'})
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    assert owner.start()['state'] == 'READY'
    base = {'keyboard':KeyboardInterrupt, 'cancelled':asyncio.CancelledError, 'generator':GeneratorExit}[signal_name]
    calls = []
    class UntrustedSignal(base):
        def __init__(self):
            calls.append(True)
            if len(calls) > 1: raise RuntimeError('synthetic private failure')
    event = UntrustedSignal()
    def interrupted(memory): raise event
    monkeypatch.setattr(runtime, '_flat_check', interrupted)
    with pytest.raises(base) as captured:
        owner.prepare_consumer_export(enabled=True, expected_nonce='e'*64, expected_identifier_policy_sha256='a'*64, max_total_bytes=1000)
    assert type(captured.value) is base and calls == [True]
    assert captured.value.args == () and captured.value.__context__ is None
    assert owner.status()['state'] == 'HOLD'


@pytest.mark.parametrize('signal_name', ['keyboard','cancelled','generator'])
def test_export_cancellation_never_touches_untrusted_signal_attributes(tmp_path, monkeypatch, signal_name):
    import asyncio
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', lambda self: {
        'schema':'wd.mind-flat-owner-build.v1','status':'BUILT_IN_MEMORY','authority':'none'})
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    assert owner.start()['state'] == 'READY'
    base = {'keyboard':KeyboardInterrupt, 'cancelled':asyncio.CancelledError, 'generator':GeneratorExit}[signal_name]
    class UntrustedSignal(base):
        def __setattr__(self, name, value):
            if name in ('__traceback__', '__context__', '__cause__'):
                raise RuntimeError('synthetic private failure')
            return super().__setattr__(name, value)
    event = UntrustedSignal()
    def interrupted(memory): raise event
    monkeypatch.setattr(runtime, '_flat_check', interrupted)
    with pytest.raises(base) as captured:
        owner.prepare_consumer_export(enabled=True, expected_nonce='e'*64, expected_identifier_policy_sha256='a'*64, max_total_bytes=1000)
    assert type(captured.value) is base
    assert captured.value.args == () and captured.value.__context__ is None
    assert owner.status()['state'] == 'HOLD'
