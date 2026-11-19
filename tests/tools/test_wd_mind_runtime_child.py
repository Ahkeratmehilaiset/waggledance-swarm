"""Synthetic runtime-child tests; never run a model or native executable."""
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import time
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tools import wd_mind_interpreter_identity as identity
from tools import wd_mind_runtime_child as child
from tools import wd_mind_runtime_manifest as verifier
from tools import wd_mind_runtime_process as anchor
from tools import wd_mind_e5_client as e5_client
from test_wd_mind_runtime_process import candidate


def _verified_parent_result(*, timestamp, age, freshness='UNKNOWN'):
    return {
        'schema': 'wd.mind-flat-verified-session.v1', 'authority': 'none',
        'lane': 'codex-lead-1', 'fleet_sha256': 'b' * 64,
        'sender_authentication': 'UNKNOWN', 'index_match': 'UNKNOWN',
        'same_user_aba': 'UNKNOWN', 'current_status_freshness': freshness,
        'results': [{'text': 'synthetic', 'source_event_sha256': 'a' * 64,
                     'event_time_utc_floor': timestamp, 'source_agent': 'codex-lead-1',
                     'event_age_seconds_upper_bound': age}],
    }


def _validate_parent_result(result):
    pair = SimpleNamespace(_metadata={'lane': 'codex-lead-1'})
    raw = json.dumps(result).encode('utf-8')
    return json.loads(child._RetainedParentPair._validated_result(pair, raw, 'b' * 64))


def test_parent_result_accepts_historical_event_and_reports_age():
    timestamp = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    result = _validate_parent_result(_verified_parent_result(timestamp=timestamp, age=1))
    assert result['current_status_freshness'] == 'UNKNOWN'
    assert result['results'][0]['event_time_utc_floor'] == timestamp
    assert result['results'][0]['event_age_seconds_upper_bound'] >= 172800


@pytest.mark.parametrize('timestamp,age,freshness', [
    ((datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), 0, 'UNKNOWN'),
    ('2026-01-01T00:00:00', 1, 'UNKNOWN'),
    ('2026-01-01T00:00:00Z', -1, 'UNKNOWN'),
    ('2026-01-01T00:00:00Z', True, 'UNKNOWN'),
    ('2026-01-01T00:00:00Z', 1, 'FRESH'),
])
def test_parent_result_refuses_invalid_time_age_or_status(timestamp, age, freshness):
    with pytest.raises(ValueError):
        _validate_parent_result(_verified_parent_result(timestamp=timestamp, age=age, freshness=freshness))


@pytest.fixture
def synthetic_local_admission(monkeypatch):
    """In-process double; real Windows reservation is covered separately."""
    guard = object.__new__(child._LocalOwnerReservation)
    guard.fd = 1  # Inert sentinel; this fixture never reads or closes it.
    monkeypatch.setattr(guard, '_check', lambda: None)
    monkeypatch.setattr(guard, '_read', lambda: child._OWNER_RESERVATION_MAGIC + b'R')
    return child._LocalAdmission(guard)


@pytest.fixture
def synthetic_worker_admission(synthetic_local_admission):
    """Exercise worker plumbing without opening any live marker or process."""
    lease = synthetic_local_admission
    owner, factory = object(), object()
    lease.bind_owner(owner)
    lease.bind_factory(owner, factory)
    lease.begin_worker(owner, factory)
    try:
        yield {'_admission':lease, '_admission_owner':owner, '_admission_factory':factory}
    finally:
        lease.finish_worker_start(owner, factory)


@pytest.fixture
def synthetic_held_keeper(monkeypatch):
    """A test-only keeper for synthetic resources; it never releases HOLD."""
    from tools import wd_mind_e5_lifecycle as lifecycle
    done = threading.Event()
    monkeypatch.setattr(lifecycle, '_active', None)
    monkeypatch.setattr(lifecycle._Owner, '_keep', lambda self: done.wait(5))
    yield
    done.set()
    owner = lifecycle._active
    if owner is not None:
        owner._keeper.join(timeout=2)
        assert not owner._keeper.is_alive()


def test_local_manifest_query_is_default_off_and_refuses_an_unpinned_bundle(tmp_path, synthetic_local_admission):
    policy = b'synthetic'
    args = {'bundle_root': str(tmp_path), 'expected_manifest_sha256': 'a' * 64,
            'identifier_policy_bytes': policy, 'expected_identifier_policy_sha256': hashlib.sha256(policy).hexdigest(),
            'operator_approval_reference_sha256': 'c' * 64, 'lane': 'codex-lead-1',
            '_admission': synthetic_local_admission}
    with pytest.raises(child.LaunchRefused, match='^mind_e5_start_refused$'):
        child.create_local_manifest_query(**args)
    adapter = child.create_local_manifest_query(enabled=True, **args)
    assert adapter.start()['state'] == 'STARTING'
    assert adapter.wait(timeout=5)['state'] == 'HOLD'
    assert adapter.close()['state'] == 'STOPPED'


def test_local_manifest_query_timeout_retains_preparation_and_never_launches_late(monkeypatch, tmp_path, synthetic_local_admission):
    from tools import wd_mind_full_runtime as runtime
    entered, release = threading.Event(), threading.Event()
    launches = []
    monkeypatch.setattr(runtime, 'create_operator_pinned_policy_anchor', lambda **kwargs: object())
    def prepare(**kwargs):
        entered.set()
        release.wait(5)
        return object()
    monkeypatch.setattr(runtime, 'assemble_full_runtime', prepare)
    monkeypatch.setattr(runtime, 'create_manifest_worker_factory', lambda **kwargs: launches.append('factory'))
    policy = b'synthetic'
    adapter = child.create_local_manifest_query(enabled=True, bundle_root=str(tmp_path),
        expected_manifest_sha256='a'*64, identifier_policy_bytes=policy,
        expected_identifier_policy_sha256=hashlib.sha256(policy).hexdigest(),
        operator_approval_reference_sha256='c'*64, lane='codex-lead-1', _admission=synthetic_local_admission)
    assert not entered.is_set()  # The caller has the owner before preparation.
    try:
        assert adapter.start()['state'] == 'STARTING'
        assert entered.wait(2)
        assert adapter.wait(timeout=0.01)['state'] == 'HOLD'
        assert adapter.status()['state'] == 'HOLD'
    finally:
        release.set()
        adapter._thread.join(timeout=5)
    assert not adapter._thread.is_alive()
    assert launches == []
    assert adapter.close()['state'] == 'STOPPED'


def test_local_manifest_query_timeout_during_anchor_never_prepares_or_launches(monkeypatch, tmp_path, synthetic_local_admission):
    from tools import wd_mind_full_runtime as runtime
    entered, release = threading.Event(), threading.Event()
    launches = []
    def anchor(**kwargs):
        entered.set()
        release.wait(5)
        return object()
    monkeypatch.setattr(runtime, 'create_operator_pinned_policy_anchor', anchor)
    monkeypatch.setattr(runtime, 'assemble_full_runtime', lambda **kwargs: launches.append('assemble'))
    policy = b'synthetic'
    adapter = child.create_local_manifest_query(enabled=True, bundle_root=str(tmp_path),
        expected_manifest_sha256='a'*64, identifier_policy_bytes=policy,
        expected_identifier_policy_sha256=hashlib.sha256(policy).hexdigest(),
        operator_approval_reference_sha256='c'*64, lane='codex-lead-1', _admission=synthetic_local_admission)
    assert not entered.is_set()
    try:
        assert adapter.start()['state'] == 'STARTING'
        assert entered.wait(2)
        assert adapter.wait(timeout=0.01)['state'] == 'HOLD'
    finally:
        release.set()
        adapter._thread.join(timeout=5)
    assert not adapter._thread.is_alive() and launches == []
    assert adapter.close()['state'] == 'STOPPED'


@pytest.mark.parametrize('lane,scope', [
    ('codex-lead-1', 'own'), ('codex-tools-1', 'own'), ('fable-5', 'own'),
    ('claude-rco-1', 'shared'), ('claude-rco-2', 'shared'),
])
def test_local_manifest_query_retains_owner_and_routes_bound_lane(monkeypatch, tmp_path, lane, scope, synthetic_local_admission):
    from tools import wd_mind_full_runtime as runtime
    from tools import wd_mind_e5_client as client
    controls = (b'synthetic-fleet', b'synthetic-roster', b'synthetic-policy')
    fleet_sha = hashlib.sha256(controls[0]).hexdigest()
    calls = []
    class Owner:
        def __init__(self): self.state = 'NEW'
        def start(self): self.state = 'READY'; calls.append('start'); return {'state': self.state, 'owned': True, 'authority': 'none'}
        def status(self): return {'state': self.state, 'owned': True, 'authority': 'none'}
        def close(self): self.state = 'HOLD'; calls.append('close'); return {'state': self.state, 'owned': True, 'authority': 'none'}
        def query_local(self, **kwargs):
            calls.append(kwargs)
            result = _verified_parent_result(timestamp=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat(), age=1)
            result.update(lane=lane, fleet_sha256=fleet_sha)
            return result
    held = Owner()
    monkeypatch.setattr(runtime, 'create_operator_pinned_policy_anchor', lambda **kwargs: calls.append(('anchor', kwargs)))
    monkeypatch.setattr(runtime, 'assemble_full_runtime', lambda **kwargs: calls.append(('binding', kwargs)) or object())
    monkeypatch.setattr(runtime, 'create_manifest_worker_factory', lambda **kwargs: calls.append(('factory', kwargs)) or object())
    monkeypatch.setattr(runtime, 'create_single_manifest_owner', lambda **kwargs: calls.append(('owner', kwargs)) or held)
    monkeypatch.setattr(runtime, '_bundle_inputs', lambda root, manifest: controls)
    policy = b'synthetic'
    adapter = child.create_local_manifest_query(enabled=True, bundle_root=str(tmp_path),
        expected_manifest_sha256='a'*64, identifier_policy_bytes=policy,
        expected_identifier_policy_sha256=hashlib.sha256(policy).hexdigest(),
        operator_approval_reference_sha256='c'*64, lane=lane, _admission=synthetic_local_admission)
    assert calls == []
    assert 'start' not in calls
    assert adapter.start()['state'] == 'STARTING'
    assert adapter.wait(timeout=5)['state'] == 'READY', [row if type(row) is str else row[0] for row in calls]
    assert next(row[1] for row in calls if type(row) is tuple and row[0] == 'factory')['bulk_contract_sha256'] == client.BULK_CONTRACT_SHA256
    assert next(row[1] for row in calls if type(row) is tuple and row[0] == 'owner')['worker_rotation'] is True
    assert next(row[1] for row in calls if type(row) is tuple and row[0] == 'owner')['vector_cache_enabled'] is True
    before = list(calls)
    with pytest.raises(child.LaunchRefused): adapter.query()
    assert calls == before and child._startup_scope_for_lane(lane) == scope
    assert adapter.close() == {'state': 'HOLD', 'owned': True, 'authority': 'none'}
    assert adapter.status()['owned'] is True


@pytest.mark.parametrize('when', ['before_query', 'during_query'])
def test_local_query_rejects_real_approval_descriptor_mutation(monkeypatch, tmp_path, when, synthetic_local_admission):
    # The owner double applies the real pinned approval check on both sides of
    # located_candidates; real-source tests cover the non-double owner.
    from tools import wd_mind_full_runtime as runtime
    policy = b'synthetic-policy'
    policy_sha = hashlib.sha256(policy).hexdigest()
    approval_sha = 'c' * 64
    name = 'wd-mind-identifier-policy-approval.json'
    descriptor = json.dumps({'schema':'wd.mind-identifier-policy-approval.v1',
        'identifier_policy_sha256':policy_sha, 'approval_reference_sha256':approval_sha}).encode()
    descriptor_path = tmp_path / name
    descriptor_path.write_bytes(descriptor)
    manifest = json.dumps({'schema_version':1, 'files':{name:hashlib.sha256(descriptor).hexdigest()}}).encode()
    (tmp_path / 'deployment-manifest.json').write_bytes(manifest)
    manifest_sha = hashlib.sha256(manifest).hexdigest()
    calls = []
    def mutate():
        descriptor_path.write_bytes(descriptor.replace(b'"c' + b'c'*63, b'"d' + b'c'*63))
        assert descriptor_path.read_bytes() != descriptor
    def check():
        return runtime.create_operator_pinned_policy_anchor(enabled=True, bundle_root=str(tmp_path),
            expected_manifest_sha256=manifest_sha, operator_policy_sha256=policy_sha,
            operator_approval_reference_sha256=approval_sha)
    class Owner:
        state = 'READY'
        def start(self): return self.status()
        def status(self): return {'state':self.state, 'owned':True, 'authority':'none'}
        def close(self): return {'state':'HOLD', 'owned':True, 'authority':'none'}
        def located_candidates(self, request):
            if self.state != 'READY': raise ValueError('owner_held')
            try:
                check()
                calls.append('query')
                if when == 'during_query': mutate()
                check()
            except Exception:
                self.state = 'HOLD'
                raise
            return {'synthetic':'candidate'}
    monkeypatch.setattr(runtime, 'assemble_full_runtime', lambda **kwargs: object())
    monkeypatch.setattr(runtime, 'create_manifest_worker_factory', lambda **kwargs: object())
    monkeypatch.setattr(runtime, 'create_single_manifest_owner', lambda **kwargs: Owner())
    adapter = child.create_local_manifest_query(enabled=True, bundle_root=str(tmp_path),
        expected_manifest_sha256=manifest_sha, identifier_policy_bytes=policy,
        expected_identifier_policy_sha256=policy_sha, operator_approval_reference_sha256=approval_sha,
        lane='codex-lead-1', _admission=synthetic_local_admission)
    assert adapter.start()['state'] == 'STARTING'
    assert adapter.wait(timeout=5)['state'] == 'READY'
    request = {'synthetic':'request'}
    if when == 'before_query': mutate()
    with pytest.raises(child.LaunchRefused): adapter.located_candidates(request)
    assert calls == ([] if when == 'before_query' else ['query'])
    assert adapter.status()['state'] == 'HOLD'
    descriptor_path.write_bytes(descriptor)
    with pytest.raises(child.LaunchRefused): adapter.located_candidates(request)


@pytest.mark.parametrize('raw', [b'', b'MCL2\x00\x00\x00\x01x', b'MCL1\x00\x00\x00\x00',
                                 b'MCL1\x00\x01\x00\x01', b'MCL1\x00\x00\x00\x02x',
                                 b'MCL1\x00\x00\x00\x01xy'])
def test_local_policy_frame_rejects_bad_or_extra_bytes(raw):
    with pytest.raises(ValueError): child._read_local_policy_frame(io.BytesIO(raw))


def test_local_policy_frame_accepts_exact_bounded_bytes():
    assert child._read_local_policy_frame(io.BytesIO(b'MCL1\x00\x00\x00\x03abc')) == b'abc'


@pytest.mark.parametrize('query_ok', [True, False])
def test_closed_local_cli_never_reads_policy_or_starts_owner(monkeypatch, query_ok):
    def unexpected(**kwargs): raise AssertionError('owner must not start')
    monkeypatch.setattr(child, 'create_local_manifest_query', unexpected)
    output = io.BytesIO()
    status = child._local_query_entry(control_stream=io.BytesIO(b'synthetic-secret'),
        output_stream=output, bundle_root='unused', expected_manifest_sha256='a'*64,
        lane='codex-lead-1', policy_sha256='b'*64, approval_reference_sha256='c'*64)
    assert status == 2 and output.getvalue() == b''


def test_closed_local_cli_never_emits_output(monkeypatch):
    def unexpected(**kwargs): raise AssertionError('owner must not start')
    monkeypatch.setattr(child, 'create_local_manifest_query', unexpected)
    output = io.BytesIO()
    status = child._local_query_entry(control_stream=io.BytesIO(b'synthetic-secret'),
        output_stream=output, bundle_root='unused', expected_manifest_sha256='a'*64,
        lane='codex-lead-1', policy_sha256='b'*64, approval_reference_sha256='c'*64)
    assert status == 2 and output.getvalue() == b''


def test_closed_local_cli_refuses_before_admission(monkeypatch):
    seen = []
    monkeypatch.setattr(child, '_guarded_local_entry', lambda *args, **kwargs: seen.append(True))
    args = ['--mind-local-query', '--policy-sha256', 'b'*64,
            '--approval-reference-sha256', 'c'*64, '--lane', 'codex-lead-1']
    assert child._fixed_child_main(args) == 2 and child._fixed_child_main(args[:-1]) == 2
    assert seen == []


@pytest.mark.parametrize('policy_mode', ['frame', 'path'])
@pytest.mark.parametrize('stop_byte,expected_exit', [(b'S', 0), (b'', 0), (b'X', 2)])
def test_local_candidate_owner_shares_one_owner_for_five_lanes_and_holds_on_close(monkeypatch, tmp_path, stop_byte, expected_exit, policy_mode):
    from tools import wd_mind_full_runtime as runtime
    from tools import wd_mind_runtime_process as process
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    reservation_root = tmp_path / 'synthetic-owner-reservation'
    reservation_root.mkdir()
    marker = reservation_root / child._OWNER_RESERVATION_NAME
    marker.write_bytes(child._OWNER_RESERVATION_MAGIC + b'C')
    monkeypatch.setattr(child, '_AUDIT_ROOT', reservation_root)
    ready = threading.Event()
    policy = b'synthetic-private-policy'
    policy_path = tmp_path / 'policy.json'
    policy_path.write_bytes(policy)
    source = io.BytesIO(b'MCL1' + struct.pack('>I', len(policy)) + policy if policy_mode == 'frame' else b'')
    class Input:
        def read(self, size):
            value = source.read(size)
            if value: return value
            assert ready.wait(5)
            return stop_byte
    statuses, requests, starts = [], [], []
    class Output:
        def write(self, data):
            raw = bytes(data)
            statuses.append(json.loads(raw))
            if statuses[-1]['state'] == 'READY': ready.set()
            return len(raw)
        def flush(self): pass
    class Owner:
        def __init__(self): self._owner = self; self.closes = 0
        def start(self):
            if os.name == 'nt':
                with pytest.raises(child.LaunchRefused):
                    child._reserve_local_owner(reservation_root)
            starts.append('owner')
            return {'state':'STARTING'}
        def status(self): return {'state':'READY', 'owned':True}
        def start_diagnostic(self):
            return {'reason':'NONE', 'exception_type':'NONE', 'authority':'none'}
        def located_candidates(self, request):
            requests.append(request)
            return {'schema':'wd.mind-pipe-candidates.v2', 'lane':request['lane'],
                    'scope':request['scope'], 'nonce':request['nonce'], 'status':'ABSTAIN', 'candidates':[]}
        def close(self):
            self.closes += 1
            return {'state':'HOLD', 'owned':None} if self.closes == 1 else {'state':'STOPPED', 'owned':False}
    owner = Owner()
    class Service:
        def __init__(self, adapter): self.adapter, self.closes = adapter, 0
        def start(self):
            for lane in ('codex-lead-1','codex-tools-1','claude-rco-1','claude-rco-2','fable-5'):
                request = {'schema':'wd.mind-pipe-request.v2', 'query_id':'startup_work_overview',
                           'nonce':'a'*64, 'lane':lane, 'scope':'shared'}
                assert self.adapter.located_candidates(request)['status'] == 'ABSTAIN'
        def status(self): return {'state':'RUNNING', 'authority':'none', 'caller_authentication':'UNKNOWN'}
        def close(self, *, timeout):
            self.closes += 1
            return {'state':'HOLD'} if self.closes == 1 else {'state':'STOPPED'}
    adapter_ref = []
    def make_adapter(**kwargs):
        adapter = child._LocalManifestQuery('codex-lead-1', 'synthetic', 'a'*64, policy,
            hashlib.sha256(policy).hexdigest(), 'c'*64, 1, 1, 1, admission=kwargs['_admission'])
        adapter._owner, adapter._state = owner, 'READY'
        if kwargs['_admission'] is not None:
            kwargs['_admission'].bind_owner(adapter)
        adapter_ref.append(adapter)
        return adapter
    def service_factory(**kwargs):
        assert kwargs['owner'] is adapter_ref[0]
        assert kwargs['pipe_name'] == process.local_candidate_pipe_name(manifest_sha256='a'*64)
        return Service(kwargs['owner'])
    monkeypatch.setattr(child, 'create_local_manifest_query', make_adapter)
    monkeypatch.setattr(child._LocalManifestQuery, 'start', lambda self: owner.start())
    monkeypatch.setattr(child._LocalManifestQuery, 'status', lambda self: owner.status())
    monkeypatch.setattr(child._LocalManifestQuery, 'located_candidates', lambda self, request: owner.located_candidates(request))
    monkeypatch.setattr(process, 'create_local_candidate_service', service_factory)
    entry = child._guarded_local_entry if os.name == 'nt' else lambda fn, **kwargs: fn(**kwargs)
    result = entry(child._local_candidate_owner_entry, control_stream=Input(), output_stream=Output(),
        bundle_root='synthetic', expected_manifest_sha256='a'*64,
        policy_sha256=hashlib.sha256(policy).hexdigest(), approval_reference_sha256='c'*64,
        policy_path=str(policy_path) if policy_mode == 'path' else None)
    assert result == expected_exit and starts == ['owner'] and len(requests) == 5
    assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC + b'C'
    assert [row['state'] for row in statuses] == ['STARTING','READY','HOLD','STOPPED']
    assert all(row['authority'] == 'none' and row['caller_authentication'] == 'UNKNOWN'
               and row['source_authentication'] == 'UNKNOWN' and row['index_refresh'] == 'NOT_SCHEDULED'
               for row in statuses)
    assert policy.decode() not in json.dumps(statuses)


@pytest.mark.parametrize('disabled_at', ['entry', 'service_start'])
def test_local_candidate_owner_never_reports_ready_with_candidate_gate_disabled(monkeypatch, disabled_at):
    from tools import wd_mind_full_runtime as runtime
    from tools import wd_mind_runtime_process as process

    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', disabled_at != 'entry')
    settled = threading.Event()
    statuses, starts, services, closes = [], [], [], []
    policy = b'synthetic-private-policy'
    source = io.BytesIO(b'MCL1' + struct.pack('>I', len(policy)) + policy)

    class Input:
        def read(self, size):
            value = source.read(size)
            if value:
                return value
            assert settled.wait(5)
            return b'S'

    class Output:
        def write(self, data):
            statuses.append(json.loads(bytes(data))['state'])
            if statuses[-1] in ('READY', 'HOLD'):
                settled.set()
            return len(data)
        def flush(self): pass

    class Owner:
        _owner = object()
        def start(self):
            starts.append(True)
            return {'state': 'STARTING'}
        def status(self): return {'state': 'READY', 'owned': True}
        def close(self):
            closes.append('owner')
            return {'state': 'STOPPED', 'owned': False}

    class Service:
        def start(self):
            if disabled_at == 'service_start':
                monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', False)
        def status(self): return {'state': 'RUNNING'}
        def close(self, *, timeout):
            closes.append('service')
            return {'state': 'STOPPED'}

    monkeypatch.setattr(child, 'create_local_manifest_query', lambda **kwargs: Owner())
    def service_factory(**kwargs):
        services.append(True)
        return Service()
    monkeypatch.setattr(process, 'create_local_candidate_service', service_factory)
    result = child._local_candidate_owner_entry(control_stream=Input(), output_stream=Output(),
        bundle_root='synthetic', expected_manifest_sha256='a'*64,
        policy_sha256=hashlib.sha256(policy).hexdigest(), approval_reference_sha256='c'*64)

    assert result == 2
    if disabled_at == 'entry':
        assert statuses == ['HOLD', 'STOPPED']
        assert starts == [] and services == [] and closes == []
    else:
        assert statuses == ['STARTING', 'HOLD', 'STOPPED']
        assert starts == [True] and services == [True]
        assert closes == ['service', 'owner']


def test_stop_cancels_but_retains_owner_during_service_start_with_blocked_output(monkeypatch):
    from tools import wd_mind_full_runtime as runtime
    from tools import wd_mind_runtime_process as process

    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    service_entered, release_service, hold_blocked, release_output, owner_closed = (
        threading.Event() for _ in range(5))
    policy = b'synthetic-policy'
    source = io.BytesIO(b'MCL1' + struct.pack('>I', len(policy)) + policy)
    class Input:
        def read(self, size):
            value = source.read(size)
            if value:
                return value
            assert service_entered.wait(5)
            return b'S'
    class Output:
        def write(self, data):
            if json.loads(bytes(data))['state'] == 'HOLD':
                hold_blocked.set()
                assert release_output.wait(5)
            return len(data)
        def flush(self): pass
    class Owner:
        def status(self): return {'state': 'READY' if getattr(self, 'starts', 1) else 'NEW'}
        def close(self):
            owner_closed.set()
            return {'state': 'STOPPED', 'owned': False}
    adapter = child._LocalManifestQuery('codex-lead-1', 'synthetic', 'a'*64, policy,
        hashlib.sha256(policy).hexdigest(), 'c'*64, 1, 1, 1)
    adapter._owner = Owner()
    adapter._state = 'READY'
    monkeypatch.setattr(child._LocalManifestQuery, 'start', lambda self: {'state': 'STARTING'})
    monkeypatch.setattr(child._LocalManifestQuery, 'status', lambda self: {'state': 'READY', 'owned': True})
    monkeypatch.setattr(child._LocalManifestQuery, 'start_failure_observed', lambda self: False)
    monkeypatch.setattr(child, 'create_local_manifest_query', lambda **kwargs: adapter)
    class Service:
        def start(self):
            service_entered.set()
            assert release_service.wait(5)
        def status(self): return {'state': 'RUNNING'}
        def close(self, *, timeout): return {'state': 'STOPPED'}
    monkeypatch.setattr(process, 'create_local_candidate_service', lambda **kwargs: Service())
    result = []
    thread = threading.Thread(target=lambda: result.append(child._local_candidate_owner_entry(
        control_stream=Input(), output_stream=Output(), bundle_root='synthetic',
        expected_manifest_sha256='a'*64, policy_sha256=hashlib.sha256(policy).hexdigest(),
        approval_reference_sha256='c'*64)))
    thread.start()
    try:
        assert hold_blocked.wait(5)
        assert adapter._cancel.wait(5)
        assert not owner_closed.is_set()
        release_service.set()
        assert owner_closed.wait(5)
    finally:
        release_service.set()
        release_output.set()
        thread.join(5)
    assert not thread.is_alive()
    assert result == [0]


def test_blocked_close_diagnostic_does_not_block_cooperative_close(monkeypatch):
    from tools import wd_mind_full_runtime as runtime
    from tools import wd_mind_runtime_process as process

    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    monkeypatch.setattr(child, '_LOCAL_SERVICE_CLOSE_GRACE_SECONDS', 0.05)
    ready, diagnostic_blocked, release_output, release_owner, owner_closed = (
        threading.Event() for _ in range(5))
    policy = b'synthetic-policy'
    source = io.BytesIO(b'MCL1' + struct.pack('>I', len(policy)) + policy)
    class Input:
        def read(self, size):
            value = source.read(size)
            if value:
                return value
            assert ready.wait(5)
            return b'S'
    class Output:
        def write(self, data):
            row = json.loads(bytes(data))
            if row.get('state') == 'READY':
                ready.set()
            if row.get('schema') == 'wd.mind-local-owner-close-diagnostic.v1':
                diagnostic_blocked.set()
                assert release_output.wait(5)
            return len(data)
        def flush(self): pass
    class Owner:
        def status(self): return {'state': 'READY' if getattr(self, 'starts', 1) else 'NEW'}
        def close(self):
            if not release_owner.is_set():
                return {'state': 'HOLD', 'owned': None}
            owner_closed.set()
            return {'state': 'STOPPED', 'owned': False}
    adapter = child._LocalManifestQuery('codex-lead-1', 'synthetic', 'a'*64, policy,
        hashlib.sha256(policy).hexdigest(), 'c'*64, 1, 1, 1)
    adapter._owner = Owner()
    adapter._state = 'READY'
    monkeypatch.setattr(child._LocalManifestQuery, 'start', lambda self: {'state': 'STARTING'})
    monkeypatch.setattr(child._LocalManifestQuery, 'status', lambda self: {'state': 'READY', 'owned': True})
    monkeypatch.setattr(child._LocalManifestQuery, 'start_failure_observed', lambda self: False)
    monkeypatch.setattr(child, 'create_local_manifest_query', lambda **kwargs: adapter)
    class Service:
        def start(self): pass
        def status(self): return {'state': 'RUNNING'}
        def close(self, *, timeout): return {'state': 'STOPPED'}
    monkeypatch.setattr(process, 'create_local_candidate_service', lambda **kwargs: Service())
    result = []
    thread = threading.Thread(target=lambda: result.append(child._local_candidate_owner_entry(
        control_stream=Input(), output_stream=Output(), bundle_root='synthetic',
        expected_manifest_sha256='a'*64, policy_sha256=hashlib.sha256(policy).hexdigest(),
        approval_reference_sha256='c'*64)))
    thread.start()
    try:
        assert diagnostic_blocked.wait(5)
        release_owner.set()
        assert owner_closed.wait(5)
        assert thread.is_alive()  # The blocked writer is still owned.
    finally:
        release_owner.set()
        release_output.set()
        thread.join(5)
    assert not thread.is_alive()
    assert result == [0]


def test_close_starts_before_close_diagnostic_thread_start_can_block(monkeypatch):
    from tools import wd_mind_full_runtime as runtime
    from tools import wd_mind_runtime_process as process

    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    ready, diagnostic_start_blocked, release_start, owner_close_called, release_owner = (
        threading.Event() for _ in range(5))
    policy = b'synthetic-policy'
    source = io.BytesIO(b'MCL1' + struct.pack('>I', len(policy)) + policy)
    class Input:
        def read(self, size):
            value = source.read(size)
            if value:
                return value
            assert ready.wait(5)
            return b'S'
    class Output:
        def write(self, data):
            if json.loads(bytes(data)).get('state') == 'READY':
                ready.set()
            return len(data)
        def flush(self): pass
    class Owner:
        def status(self): return {'state': 'READY' if getattr(self, 'starts', 1) else 'NEW'}
        def close(self):
            owner_close_called.set()
            return ({'state': 'STOPPED', 'owned': False} if release_owner.is_set()
                    else {'state': 'HOLD', 'owned': None})
    adapter = child._LocalManifestQuery('codex-lead-1', 'synthetic', 'a'*64, policy,
        hashlib.sha256(policy).hexdigest(), 'c'*64, 1, 1, 1)
    adapter._owner = Owner()
    adapter._state = 'READY'
    monkeypatch.setattr(child._LocalManifestQuery, 'start', lambda self: {'state': 'STARTING'})
    monkeypatch.setattr(child._LocalManifestQuery, 'status', lambda self: {'state': 'READY', 'owned': True})
    monkeypatch.setattr(child._LocalManifestQuery, 'start_failure_observed', lambda self: False)
    monkeypatch.setattr(child, 'create_local_manifest_query', lambda **kwargs: adapter)
    class Service:
        def start(self): pass
        def status(self): return {'state': 'RUNNING'}
        def close(self, *, timeout): return {'state': 'STOPPED'}
    monkeypatch.setattr(process, 'create_local_candidate_service', lambda **kwargs: Service())
    actual_start = threading.Thread.start
    def start_with_stall(thread):
        if thread.name == 'mind-owner-close-diagnostic':
            diagnostic_start_blocked.set()
            assert release_start.wait(5)
        return actual_start(thread)
    monkeypatch.setattr(threading.Thread, 'start', start_with_stall)
    result = []
    thread = threading.Thread(target=lambda: result.append(child._local_candidate_owner_entry(
        control_stream=Input(), output_stream=Output(), bundle_root='synthetic',
        expected_manifest_sha256='a'*64, policy_sha256=hashlib.sha256(policy).hexdigest(),
        approval_reference_sha256='c'*64)))
    thread.start()
    try:
        assert diagnostic_start_blocked.wait(5)
        assert adapter._cancel.is_set()
        assert owner_close_called.is_set()
    finally:
        release_owner.set()
        release_start.set()
        thread.join(5)
    assert not thread.is_alive()
    assert result == [0]


def test_close_frame_can_arrive_while_first_owner_close_is_blocked(monkeypatch):
    from tools import wd_mind_full_runtime as runtime
    from tools import wd_mind_runtime_process as process
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    monkeypatch.setattr(child, '_LOCAL_SERVICE_CLOSE_GRACE_SECONDS', 0.05)
    ready, close_blocked, release_close, diagnostic_seen = (threading.Event() for _ in range(4))
    policy = b'synthetic-policy'
    source = io.BytesIO(b'MCL1' + struct.pack('>I', len(policy)) + policy)
    class Input:
        def read(self, size):
            value = source.read(size)
            if value: return value
            assert ready.wait(5)
            return b'S'
    class Output:
        def write(self, data):
            row = json.loads(bytes(data))
            if row.get('state') == 'READY': ready.set()
            if row.get('schema') == 'wd.mind-local-owner-close-diagnostic.v1': diagnostic_seen.set()
            return len(data)
        def flush(self): pass
    class Owner:
        def status(self): return {'state': 'READY' if getattr(self, 'starts', 1) else 'NEW'}
        def close(self):
            close_blocked.set()
            assert release_close.wait(5)
            return {'state': 'STOPPED', 'owned': False}
    adapter = child._LocalManifestQuery('codex-lead-1', 'synthetic', 'a'*64, policy,
        hashlib.sha256(policy).hexdigest(), 'c'*64, 1, 1, 1)
    adapter._owner, adapter._state = Owner(), 'READY'
    monkeypatch.setattr(child._LocalManifestQuery, 'start', lambda self: {'state': 'STARTING'})
    monkeypatch.setattr(child._LocalManifestQuery, 'status', lambda self: {'state': 'READY', 'owned': True})
    monkeypatch.setattr(child._LocalManifestQuery, 'start_failure_observed', lambda self: False)
    monkeypatch.setattr(child, 'create_local_manifest_query', lambda **kwargs: adapter)
    class Service:
        def start(self): pass
        def status(self): return {'state': 'RUNNING'}
        def close(self, *, timeout): return {'state': 'STOPPED'}
    monkeypatch.setattr(process, 'create_local_candidate_service', lambda **kwargs: Service())
    result = []
    thread = threading.Thread(target=lambda: result.append(child._local_candidate_owner_entry(
        control_stream=Input(), output_stream=Output(), bundle_root='synthetic',
        expected_manifest_sha256='a'*64, policy_sha256=hashlib.sha256(policy).hexdigest(),
        approval_reference_sha256='c'*64)))
    thread.start()
    try:
        assert close_blocked.wait(5)
        assert diagnostic_seen.wait(5)
        assert thread.is_alive()
    finally:
        release_close.set()
        thread.join(5)
    assert not thread.is_alive()
    assert result == [0]


def test_close_retries_while_diagnostic_start_and_status_output_are_blocked(monkeypatch):
    from tools import wd_mind_full_runtime as runtime
    from tools import wd_mind_runtime_process as process
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    ready, start_blocked, status_blocked, release_start, release_output, second_close, release_owner = (
        threading.Event() for _ in range(7))
    policy = b'synthetic-policy'
    source = io.BytesIO(b'MCL1' + struct.pack('>I', len(policy)) + policy)
    class Input:
        def read(self, size):
            value = source.read(size)
            if value: return value
            assert ready.wait(5)
            return b'S'
    class Output:
        def write(self, data):
            row = json.loads(bytes(data))
            if row.get('state') == 'READY': ready.set()
            if row.get('state') == 'HOLD':
                status_blocked.set()
                assert release_output.wait(5)
            return len(data)
        def flush(self): pass
    class Owner:
        def status(self): return {'state': 'READY' if getattr(self, 'starts', 1) else 'NEW'}
        calls = 0
        def close(self):
            self.calls += 1
            if self.calls >= 2: second_close.set()
            return ({'state': 'STOPPED', 'owned': False} if release_owner.is_set()
                    else {'state': 'HOLD', 'owned': None})
    adapter = child._LocalManifestQuery('codex-lead-1', 'synthetic', 'a'*64, policy,
        hashlib.sha256(policy).hexdigest(), 'c'*64, 1, 1, 1)
    adapter._owner, adapter._state = Owner(), 'READY'
    monkeypatch.setattr(child._LocalManifestQuery, 'start', lambda self: {'state': 'STARTING'})
    monkeypatch.setattr(child._LocalManifestQuery, 'status', lambda self: {'state': 'READY', 'owned': True})
    monkeypatch.setattr(child._LocalManifestQuery, 'start_failure_observed', lambda self: False)
    monkeypatch.setattr(child, 'create_local_manifest_query', lambda **kwargs: adapter)
    class Service:
        def start(self): pass
        def status(self): return {'state': 'RUNNING'}
        def close(self, *, timeout): return {'state': 'STOPPED'}
    monkeypatch.setattr(process, 'create_local_candidate_service', lambda **kwargs: Service())
    actual_start = threading.Thread.start
    def start_with_stall(thread):
        if thread.name == 'mind-owner-close-diagnostic':
            start_blocked.set()
            assert release_start.wait(5)
        return actual_start(thread)
    monkeypatch.setattr(threading.Thread, 'start', start_with_stall)
    result = []
    thread = threading.Thread(target=lambda: result.append(child._local_candidate_owner_entry(
        control_stream=Input(), output_stream=Output(), bundle_root='synthetic',
        expected_manifest_sha256='a'*64, policy_sha256=hashlib.sha256(policy).hexdigest(),
        approval_reference_sha256='c'*64)))
    thread.start()
    try:
        assert start_blocked.wait(5)
        assert status_blocked.wait(5)
        assert second_close.wait(5)
    finally:
        release_owner.set()
        release_start.set()
        release_output.set()
        thread.join(5)
    assert not thread.is_alive()
    assert result == [0]


def test_cleanup_evidence_stays_false_until_held_service_stops(monkeypatch):
    from tools import wd_mind_full_runtime as runtime
    from tools import wd_mind_runtime_process as process

    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    ready, service_held, release_service, owner_closed = (threading.Event() for _ in range(4))
    policy = b'synthetic-policy'
    source = io.BytesIO(b'MCL1' + struct.pack('>I', len(policy)) + policy)
    class Input:
        def read(self, size):
            value = source.read(size)
            if value:
                return value
            assert ready.wait(5)
            return b'S'
    class Output:
        def write(self, data):
            if json.loads(bytes(data)).get('state') == 'READY':
                ready.set()
            return len(data)
        def flush(self): pass
    class Owner:
        def status(self): return {'state': 'READY' if getattr(self, 'starts', 1) else 'NEW'}
        def close(self):
            owner_closed.set()
            return {'state': 'STOPPED', 'owned': False}
    adapter = child._LocalManifestQuery('codex-lead-1', 'synthetic', 'a'*64, policy,
        hashlib.sha256(policy).hexdigest(), 'c'*64, 1, 1, 1)
    adapter._owner = Owner()
    adapter._state = 'READY'
    monkeypatch.setattr(child._LocalManifestQuery, 'start', lambda self: {'state': 'STARTING'})
    monkeypatch.setattr(child._LocalManifestQuery, 'status', lambda self: {'state': 'READY', 'owned': True})
    monkeypatch.setattr(child._LocalManifestQuery, 'start_failure_observed', lambda self: False)
    monkeypatch.setattr(child, 'create_local_manifest_query', lambda **kwargs: adapter)
    class Service:
        def start(self): pass
        def status(self): return {'state': 'RUNNING'}
        def close(self, *, timeout):
            if not release_service.is_set():
                service_held.set()
                return {'state': 'HOLD'}
            return {'state': 'STOPPED'}
    monkeypatch.setattr(process, 'create_local_candidate_service', lambda **kwargs: Service())
    evidence = {'clean': False}
    result = []
    thread = threading.Thread(target=lambda: result.append(child._local_candidate_owner_entry(
        control_stream=Input(), output_stream=Output(), bundle_root='synthetic',
        expected_manifest_sha256='a'*64, policy_sha256=hashlib.sha256(policy).hexdigest(),
        approval_reference_sha256='c'*64, _cleanup_evidence=evidence)))
    thread.start()
    try:
        assert owner_closed.wait(5)
        assert service_held.wait(5)
        assert evidence['clean'] is False
        assert thread.is_alive()
    finally:
        release_service.set()
        thread.join(5)
    assert not thread.is_alive()
    assert result == [0]
    assert evidence['clean'] is True


def test_stop_is_read_while_adapter_start_is_inflight(monkeypatch):
    from tools import wd_mind_full_runtime as runtime
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    start_entered, release_start, owner_closed = (threading.Event() for _ in range(3))
    policy = b'synthetic-policy'
    source = io.BytesIO(b'MCL1' + struct.pack('>I', len(policy)) + policy)
    class Input:
        def read(self, size):
            value = source.read(size)
            if value:
                return value
            assert start_entered.wait(5)
            return b'S'
    class Output:
        def write(self, data): return len(data)
        def flush(self): pass
    class Owner:
        def status(self): return {'state': 'READY' if getattr(self, 'starts', 1) else 'NEW'}
        def close(self):
            owner_closed.set()
            return {'state': 'STOPPED', 'owned': False}
    adapter = child._LocalManifestQuery('codex-lead-1', 'synthetic', 'a'*64, policy,
        hashlib.sha256(policy).hexdigest(), 'c'*64, 1, 1, 1)
    adapter._owner = Owner()
    adapter._state = 'READY'
    def start(self):
        with self._lock:
            start_entered.set()
            assert release_start.wait(5)
        return {'state': 'STARTING'}
    monkeypatch.setattr(child._LocalManifestQuery, 'start', start)
    monkeypatch.setattr(child._LocalManifestQuery, 'status', lambda self: {'state': 'READY', 'owned': True})
    monkeypatch.setattr(child._LocalManifestQuery, 'start_failure_observed', lambda self: False)
    monkeypatch.setattr(child, 'create_local_manifest_query', lambda **kwargs: adapter)
    result = []
    thread = threading.Thread(target=lambda: result.append(child._local_candidate_owner_entry(
        control_stream=Input(), output_stream=Output(), bundle_root='synthetic',
        expected_manifest_sha256='a'*64, policy_sha256=hashlib.sha256(policy).hexdigest(),
        approval_reference_sha256='c'*64)))
    thread.start()
    try:
        assert start_entered.wait(5)
        assert adapter._cancel.wait(5)
        assert not owner_closed.is_set()
    finally:
        release_start.set()
        thread.join(5)
    assert not thread.is_alive()
    assert owner_closed.is_set()
    assert result == [0]


def test_uncertain_control_thread_start_keeps_cleanup_held(monkeypatch):
    from tools import wd_mind_full_runtime as runtime
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    reader_started, release_reader, status_hold = (threading.Event() for _ in range(3))
    policy = b'synthetic-policy'
    source = io.BytesIO(b'MCL1' + struct.pack('>I', len(policy)) + policy)
    class Input:
        def read(self, size):
            value = source.read(size)
            if value:
                return value
            reader_started.set()
            assert release_reader.wait(5)
            return b'S'
    class Output:
        def write(self, data):
            if json.loads(bytes(data)).get('state') == 'HOLD':
                status_hold.set()
            return len(data)
        def flush(self): pass
    class Adapter:
        def close(self): return {'state': 'STOPPED', 'owned': False, 'authority': 'none'}
    monkeypatch.setattr(child, 'create_local_manifest_query', lambda **kwargs: Adapter())
    actual_start = threading.Thread.start
    def uncertain_start(thread):
        actual_start(thread)
        if thread.name == 'mind-local-control':
            raise RuntimeError('synthetic-uncertain-start')
    monkeypatch.setattr(threading.Thread, 'start', uncertain_start)
    evidence, result = {'clean': False}, []
    thread = threading.Thread(target=lambda: result.append(child._local_candidate_owner_entry(
        control_stream=Input(), output_stream=Output(), bundle_root='synthetic',
        expected_manifest_sha256='a'*64, policy_sha256=hashlib.sha256(policy).hexdigest(),
        approval_reference_sha256='c'*64, _cleanup_evidence=evidence)))
    thread.start()
    try:
        assert reader_started.wait(5)
        assert status_hold.wait(5)
        assert thread.is_alive()
        assert evidence['clean'] is False
    finally:
        release_reader.set()
        thread.join(5)
    assert not thread.is_alive()
    assert evidence['clean'] is True
    assert result == [2]


def test_uncertain_close_worker_start_keeps_cleanup_held(monkeypatch):
    from tools import wd_mind_full_runtime as runtime
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    uncertain, status_hold = threading.Event(), threading.Event()
    policy = b'synthetic-policy'
    source = io.BytesIO(b'MCL1' + struct.pack('>I', len(policy)) + policy)
    class Output:
        def write(self, data):
            if json.loads(bytes(data)).get('state') == 'HOLD': status_hold.set()
            return len(data)
        def flush(self): pass
    started = []
    class Adapter:
        def start(self):
            started.append(True)
            return {'state': 'STARTING'}
        def close(self): return {'state': 'STOPPED', 'owned': False}
    monkeypatch.setattr(child, 'create_local_manifest_query', lambda **kwargs: Adapter())
    actual_start = threading.Thread.start
    uncertain_threads = []
    def uncertain_start(thread):
        if thread.name == 'mind-local-close':
            uncertain_threads.append(thread)
            uncertain.set()
            raise RuntimeError('synthetic-uncertain-start')
        return actual_start(thread)
    monkeypatch.setattr(threading.Thread, 'start', uncertain_start)
    evidence, result = {'clean': False}, []
    thread = threading.Thread(target=lambda: result.append(child._local_candidate_owner_entry(
        control_stream=source, output_stream=Output(), bundle_root='synthetic',
        expected_manifest_sha256='a'*64, policy_sha256=hashlib.sha256(policy).hexdigest(),
        approval_reference_sha256='c'*64, _cleanup_evidence=evidence)))
    thread.start()
    try:
        assert uncertain.wait(5)
        assert status_hold.wait(5)
        assert thread.is_alive()
        assert evidence['clean'] is False
        assert started == []
    finally:
        # Release only the test's captured synthetic thread; the implementation
        # itself must not assume a failed start means the target cannot run.
        if uncertain_threads:
            actual_start(uncertain_threads[0])
        thread.join(5)
    assert not thread.is_alive()
    assert result == [2]
    assert evidence['clean'] is True


def test_ambiguous_owner_thread_start_never_cleans_until_target_finishes(monkeypatch):
    target_started, release_target = threading.Event(), threading.Event()
    class Admission:
        def assert_owner(self, owner): pass
    admission = Admission()
    adapter = child._LocalManifestQuery('codex-lead-1', 'synthetic', 'a'*64,
        b'synthetic-policy', 'b'*64, 'c'*64, 1, 1, 1, admission=admission)
    def prepare(self):
        target_started.set()
        assert release_target.wait(5)
    monkeypatch.setattr(child._LocalManifestQuery, '_prepare', prepare)
    actual_start = threading.Thread.start
    uncertain_threads = []
    def uncertain_start(thread):
        if thread.name == 'mind-local-owner':
            uncertain_threads.append(thread)
            raise RuntimeError('synthetic-uncertain-start')
        return actual_start(thread)
    monkeypatch.setattr(threading.Thread, 'start', uncertain_start)
    with pytest.raises(RuntimeError):
        adapter.start()
    assert adapter.close() == {'state': 'HOLD', 'owned': None, 'authority': 'none'}
    assert not target_started.is_set()
    try:
        actual_start(uncertain_threads[0])
        assert target_started.wait(5)
        assert adapter.close() == {'state': 'HOLD', 'owned': None, 'authority': 'none'}
    finally:
        release_target.set()
        uncertain_threads[0].join(5)
    assert not uncertain_threads[0].is_alive()
    assert adapter.close() == {'state': 'STOPPED', 'owned': False, 'authority': 'none'}


@pytest.mark.parametrize('stop_at', ['before_commit', 'committed'])
def test_owner_start_commit_and_cancellation_share_one_gate(monkeypatch, stop_at):
    from tools import wd_mind_full_runtime as runtime
    reached, release = threading.Event(), threading.Event()
    class Admission:
        def assert_owner(self, owner): pass
    class Owner:
        def status(self): return {'state': 'READY' if getattr(self, 'starts', 1) else 'NEW'}
        starts = 0
        def start(self):
            self.starts += 1
            if stop_at == 'committed':
                reached.set()
                assert release.wait(5)
            return {'state': 'READY', 'owned': True}
        def close(self): return {'state': 'STOPPED', 'owned': False, 'authority': 'none'}
    owner, admission = Owner(), Admission()
    adapter = child._LocalManifestQuery('codex-lead-1', 'synthetic', 'a'*64,
        b'synthetic-policy', 'b'*64, 'c'*64, 1, 1, 1, admission=admission)
    monkeypatch.setattr(child._LocalManifestQuery, '_check_policy', lambda self: object())
    calls = []
    def check_external(self):
        calls.append(True)
        if stop_at == 'before_commit' and len(calls) == 2:
            reached.set()
            assert release.wait(5)
    monkeypatch.setattr(child._LocalManifestQuery, '_check_external_policy', check_external)
    monkeypatch.setattr(runtime, 'assemble_full_runtime', lambda **kwargs: object())
    monkeypatch.setattr(runtime, 'create_manifest_worker_factory', lambda **kwargs: object())
    monkeypatch.setattr(runtime, 'create_single_manifest_owner', lambda **kwargs: owner)
    adapter.start()
    try:
        assert reached.wait(5)
        assert adapter.close() == {'state': 'HOLD', 'owned': None, 'authority': 'none'}
        assert adapter._cancel.is_set()
    finally:
        release.set()
        adapter._thread.join(5)
    assert not adapter._thread.is_alive()
    assert owner.starts == (0 if stop_at == 'before_commit' else 1)
    assert adapter.close() == {'state': 'STOPPED', 'owned': False, 'authority': 'none'}


@pytest.mark.parametrize('unstarted_owned,force_committed,gate_busy,expected', [
    (False, False, False, 0), (None, False, False, 2), (False, True, False, 2),
    (True, False, False, 2), ('missing', False, False, 2), (False, False, True, 2),
])
def test_stop_blocks_owner_start_even_if_close_worker_is_delayed(
        monkeypatch, unstarted_owned, force_committed, gate_busy, expected):
    from tools import wd_mind_full_runtime as runtime
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    before_commit, release_prepare, close_blocked, release_close = (threading.Event() for _ in range(4))
    new_observed, gate_probe_seen, refused_status = (threading.Event() for _ in range(3))
    status_timeouts, gate_results = [], []
    policy = b'synthetic-policy'
    source = io.BytesIO(b'MCL1' + struct.pack('>I', len(policy)) + policy)
    class Admission:
        def assert_owner(self, owner): pass
    class Input:
        def read(self, size):
            value = source.read(size)
            if value: return value
            assert before_commit.wait(5)
            return b'S'
    class Output:
        def write(self, data):
            if b'"state":"HOLD"' in bytes(data):
                refused_status.set()
            return len(data)
        def flush(self): pass
    class Owner:
        def status(self):
            value = {'state': 'READY' if self.starts else 'NEW'}
            if self.starts:
                value['owned'] = True
            elif unstarted_owned != 'missing':
                value['owned'] = unstarted_owned
            return value
        starts = 0
        def start(self):
            self.starts += 1
            return {'state': 'READY', 'owned': True}
        def close(self): return {'state': 'STOPPED', 'owned': False, 'authority': 'none'}
        def start_diagnostic(self):
            return {'reason': 'NONE', 'exception_type': 'NONE', 'authority': 'none'}
    owner, admission = Owner(), Admission()
    adapter = child._LocalManifestQuery('codex-lead-1', 'synthetic', 'a'*64, policy,
        hashlib.sha256(policy).hexdigest(), 'c'*64, 1, 1, 1, admission=admission)

    class GateProbe:
        """Expose one busy nonblocking probe, then release for cleanup."""
        def __init__(self):
            self.lock, self.armed, self.was_armed = threading.Lock(), False, False
        def acquire(self, blocking=True):
            acquired = self.lock.acquire(blocking=blocking)
            if not blocking and not acquired and self.armed:
                self.armed = False
                self.lock.release()
            if not blocking:
                gate_results.append(acquired)
                gate_probe_seen.set()
            return acquired
        def release(self): self.lock.release()
        def __enter__(self):
            self.acquire()
            return self
        def __exit__(self, *args): self.release()
        def arm_busy(self):
            self.lock.acquire()
            self.armed = self.was_armed = True
        def cleanup(self):
            if self.armed:
                self.armed = False
                self.lock.release()

    gate_probe = GateProbe()
    adapter._owner_start_gate = gate_probe
    monkeypatch.setattr(child, 'create_local_manifest_query', lambda **kwargs: adapter)
    monkeypatch.setattr(child._LocalManifestQuery, '_check_policy', lambda self: object())
    calls = []
    def check_external(self):
        calls.append(True)
        if len(calls) == 2:
            before_commit.set()
            assert release_prepare.wait(5)
    monkeypatch.setattr(child._LocalManifestQuery, '_check_external_policy', check_external)
    monkeypatch.setattr(runtime, 'assemble_full_runtime', lambda **kwargs: object())
    monkeypatch.setattr(runtime, 'create_manifest_worker_factory', lambda **kwargs: object())
    monkeypatch.setattr(runtime, 'create_single_manifest_owner', lambda **kwargs: owner)
    actual_close = child._LocalManifestQuery.close
    actual_status = child._LocalManifestQuery.status
    def delayed_close(self, **kwargs):
        close_blocked.set()
        assert release_close.wait(5)
        return actual_close(self, **kwargs)

    def status_after_cancelled_prepare(self):
        # Force the final post-STOP status to observe the owner after the
        # prepare thread exits, instead of racing the STARTING observation.
        if self._cancel.is_set():
            release_prepare.set()
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                status_timeouts.append('prepare_join')
            if force_committed:
                # Fault injection: even a NEW owner is not clean evidence if
                # its launch was marked committed. No real worker is started.
                self._owner_start_committed = True
            if gate_busy and not gate_probe.was_armed:
                gate_probe.arm_busy()
        value = actual_status(self)
        if self._cancel.is_set() and value.get('state') == 'NEW':
            new_observed.set()
        return value

    monkeypatch.setattr(child._LocalManifestQuery, 'close', delayed_close)
    monkeypatch.setattr(child._LocalManifestQuery, 'status', status_after_cancelled_prepare)
    result = []
    thread = threading.Thread(target=lambda: result.append(child._local_candidate_owner_entry(
        control_stream=Input(), output_stream=Output(), bundle_root='synthetic',
        expected_manifest_sha256='a'*64, policy_sha256=hashlib.sha256(policy).hexdigest(),
        approval_reference_sha256='c'*64)))
    thread.start()
    try:
        assert close_blocked.wait(5)
        assert adapter._cancel.is_set()
        assert new_observed.wait(5)
        if unstarted_owned is False:
            # A successful probe still owns the real lock here; the close
            # worker cannot change the fence before the source checks it.
            assert gate_probe_seen.wait(5)
        else:
            # These statuses are rejected before attempting the gate. HOLD
            # proves the final status decision happened before close resumes.
            assert refused_status.wait(5)
        adapter._thread.join(5)
        assert not adapter._thread.is_alive()
        assert owner.starts == 0 and adapter._owner_start_committed is force_committed
    finally:
        gate_probe.cleanup()
        release_prepare.set()
        release_close.set()
        thread.join(5)
    assert not thread.is_alive() and not status_timeouts
    if unstarted_owned is False:
        assert gate_results == [not gate_busy]
    else:
        assert not gate_probe_seen.is_set()
    assert result == [expected]


@pytest.mark.parametrize('committed,expected', [(False, False), (True, None)])
def test_preclose_new_without_owner_start_commit_is_the_only_clean_case(committed, expected):
    adapter = child._LocalManifestQuery('codex-lead-1', 'synthetic', 'a'*64,
        b'synthetic-policy', 'b'*64, 'c'*64, 1, 1, 1)
    adapter._owner = object()
    adapter._preclose_runtime_state = 'NEW'
    adapter._owner_start_committed = committed
    # An unknown result after a committed start cannot establish clean exit.
    assert adapter.close_failure_observed() is expected


def test_inflight_query_that_fails_during_close_keeps_runtime_evidence_unknown(monkeypatch):
    query_entered, release_query, query_finished, close_entered = (threading.Event() for _ in range(4))
    class Owner:
        state = 'READY'
        def status(self): return {'state': self.state}
        def candidates(self, request):
            query_entered.set()
            assert release_query.wait(5)
            self.state = 'HOLD'
            query_finished.set()
            return {'status': 'ABSTAIN'}
        def close(self):
            close_entered.set()
            assert query_finished.wait(5)
            return {'state': 'STOPPED', 'owned': False}
    adapter = child._LocalManifestQuery('codex-lead-1', 'synthetic', 'a'*64,
        b'synthetic-policy', 'b'*64, 'c'*64, 1, 1, 1)
    adapter._owner, adapter._state = Owner(), 'READY'
    monkeypatch.setattr(child._LocalManifestQuery, '_check_external_policy', lambda self: None)
    query_errors, close_result = [], []
    def query():
        try: adapter.candidates({'synthetic': 'request'})
        except Exception as exc: query_errors.append(type(exc).__name__)
    query_thread = threading.Thread(target=query)
    close_thread = threading.Thread(target=lambda: close_result.append(adapter.close()))
    query_thread.start()
    try:
        assert query_entered.wait(5)
        close_thread.start()
        assert close_entered.wait(5)
        release_query.set()
        query_thread.join(5)
        close_thread.join(5)
    finally:
        release_query.set()
        query_thread.join(5)
        if close_thread.ident is not None: close_thread.join(5)
    assert not query_thread.is_alive() and not close_thread.is_alive()
    assert close_result[0]['state'] == 'STOPPED'
    assert query_errors == ['LaunchRefused']
    assert adapter.close_failure_observed() is None


def test_candidate_inflight_is_released_on_baseexception(monkeypatch):
    class Owner:
        def candidates(self, request): raise KeyboardInterrupt()
    adapter = child._LocalManifestQuery('codex-lead-1', 'synthetic', 'a'*64,
        b'synthetic-policy', 'b'*64, 'c'*64, 1, 1, 1)
    adapter._owner, adapter._state = Owner(), 'READY'
    monkeypatch.setattr(child._LocalManifestQuery, '_check_external_policy', lambda self: None)
    with pytest.raises(KeyboardInterrupt):
        adapter.candidates({'synthetic': 'request'})
    assert adapter._candidate_inflight == 0
    assert adapter._candidate_accounting_uncertain is False


def test_preclose_ready_does_not_hide_racing_busy_query():
    class Owner:
        calls = 0
        def status(self): return {'state': 'READY'}
        def close(self):
            self.calls += 1
            return ({'state': 'BUSY', 'owned': None} if self.calls == 1
                    else {'state': 'STOPPED', 'owned': False})
    adapter = child._LocalManifestQuery('codex-lead-1', 'synthetic', 'a'*64,
        b'synthetic-policy', 'b'*64, 'c'*64, 1, 1, 1)
    adapter._owner, adapter._state = Owner(), 'READY'
    assert adapter.close()['state'] == 'BUSY'
    assert adapter.close()['state'] == 'STOPPED'
    assert adapter.close_failure_observed() is None


def test_control_reader_exception_remains_failure_and_holds_uncertain_owner(monkeypatch):
    from tools import wd_mind_full_runtime as runtime
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    starting, gate_failed, release_gate = (threading.Event() for _ in range(3))
    policy = b'synthetic-policy'
    source = io.BytesIO(b'MCL1' + struct.pack('>I', len(policy)) + policy)
    class Input:
        def read(self, size):
            value = source.read(size)
            if value: return value
            assert starting.wait(5)
            return b'S'
    class Gate:
        def __enter__(self):
            if not release_gate.is_set():
                gate_failed.set()
                raise RuntimeError('synthetic-control-gate-failure')
            return self
        def __exit__(self, *args): return False
    adapter = child._LocalManifestQuery('codex-lead-1', 'synthetic', 'a'*64, policy,
        hashlib.sha256(policy).hexdigest(), 'c'*64, 1, 1, 1)
    adapter._owner_start_gate = Gate()
    class Output:
        def write(self, data):
            if json.loads(bytes(data)).get('state') == 'STARTING': starting.set()
            return len(data)
        def flush(self): pass
    monkeypatch.setattr(child._LocalManifestQuery, 'start', lambda self: {'state': 'STARTING'})
    monkeypatch.setattr(child._LocalManifestQuery, 'status', lambda self: {'state': 'STARTING', 'owned': None})
    monkeypatch.setattr(child, 'create_local_manifest_query', lambda **kwargs: adapter)
    evidence, result = {'clean': False}, []
    thread = threading.Thread(target=lambda: result.append(child._local_candidate_owner_entry(
        control_stream=Input(), output_stream=Output(), bundle_root='synthetic',
        expected_manifest_sha256='a'*64, policy_sha256=hashlib.sha256(policy).hexdigest(),
        approval_reference_sha256='c'*64, _cleanup_evidence=evidence)))
    thread.start()
    try:
        assert gate_failed.wait(5)
        assert evidence['clean'] is False
    finally:
        release_gate.set()
        thread.join(5)
    assert not thread.is_alive()
    assert result == [2]
    assert evidence['clean'] is False


@pytest.mark.parametrize(('runtime_state', 'expected'), [('READY', 0), ('HOLD', 2), ('BUSY', 2)])
def test_stop_does_not_hide_preexisting_runtime_hold_or_unknown(monkeypatch, runtime_state, expected):
    from tools import wd_mind_full_runtime as runtime
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    starting = threading.Event()
    policy = b'synthetic-policy'
    source = io.BytesIO(b'MCL1' + struct.pack('>I', len(policy)) + policy)
    class Input:
        def read(self, size):
            value = source.read(size)
            if value: return value
            assert starting.wait(5)
            return b'S'
    class Owner:
        def status(self): return {'state': runtime_state}
        def close(self): return {'state': 'STOPPED', 'owned': False, 'authority': 'none'}
        def start_diagnostic(self):
            return {'reason': 'NONE', 'exception_type': 'NONE', 'authority': 'none'}
    adapter = child._LocalManifestQuery('codex-lead-1', 'synthetic', 'a'*64, policy,
        hashlib.sha256(policy).hexdigest(), 'c'*64, 1, 1, 1)
    adapter._owner, adapter._state = Owner(), 'READY'
    class Output:
        def write(self, data):
            if json.loads(bytes(data)).get('state') == 'STARTING':
                starting.set()
                assert adapter._cancel.wait(5)
            return len(data)
        def flush(self): pass
    monkeypatch.setattr(child._LocalManifestQuery, 'start', lambda self: {'state': 'STARTING'})
    monkeypatch.setattr(child, 'create_local_manifest_query', lambda **kwargs: adapter)
    result = child._local_candidate_owner_entry(control_stream=Input(), output_stream=Output(),
        bundle_root='synthetic', expected_manifest_sha256='a'*64,
        policy_sha256=hashlib.sha256(policy).hexdigest(), approval_reference_sha256='c'*64)
    assert result == expected


def test_starting_output_failure_fences_owner_start_before_delayed_close(monkeypatch):
    from tools import wd_mind_full_runtime as runtime
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    before_commit, release_prepare, close_blocked, release_close, release_control, owner_started = (
        threading.Event() for _ in range(6))
    policy = b'synthetic-policy'
    source = io.BytesIO(b'MCL1' + struct.pack('>I', len(policy)) + policy)
    class Admission:
        def assert_owner(self, owner): pass
    class Input:
        def read(self, size):
            value = source.read(size)
            if value: return value
            assert release_control.wait(5)
            return b'S'
    class Output:
        def write(self, data):
            if json.loads(bytes(data)).get('state') == 'STARTING':
                assert before_commit.wait(5)
                raise OSError('synthetic-starting-output-failure')
            return len(data)
        def flush(self): pass
    class Owner:
        def start(self):
            owner_started.set()
            return {'state': 'READY', 'owned': True}
        def status(self): return {'state': 'READY', 'owned': True, 'authority': 'none'}
        def close(self): return {'state': 'STOPPED', 'owned': False, 'authority': 'none'}
        def start_diagnostic(self):
            return {'reason': 'NONE', 'exception_type': 'NONE', 'authority': 'none'}
    admission = Admission()
    adapter = child._LocalManifestQuery('codex-lead-1', 'synthetic', 'a'*64, policy,
        hashlib.sha256(policy).hexdigest(), 'c'*64, 1, 1, 1, admission=admission)
    monkeypatch.setattr(child, 'create_local_manifest_query', lambda **kwargs: adapter)
    monkeypatch.setattr(child._LocalManifestQuery, '_check_policy', lambda self: object())
    calls = []
    def check_external(self):
        calls.append(True)
        if len(calls) == 2:
            before_commit.set()
            assert release_prepare.wait(5)
    monkeypatch.setattr(child._LocalManifestQuery, '_check_external_policy', check_external)
    monkeypatch.setattr(runtime, 'assemble_full_runtime', lambda **kwargs: object())
    monkeypatch.setattr(runtime, 'create_manifest_worker_factory', lambda **kwargs: object())
    monkeypatch.setattr(runtime, 'create_single_manifest_owner', lambda **kwargs: Owner())
    actual_close = child._LocalManifestQuery.close
    def delayed_close(self, **kwargs):
        close_blocked.set()
        assert release_close.wait(5)
        return actual_close(self, **kwargs)
    monkeypatch.setattr(child._LocalManifestQuery, 'close', delayed_close)
    evidence, result = {'clean': False}, []
    thread = threading.Thread(target=lambda: result.append(child._local_candidate_owner_entry(
        control_stream=Input(), output_stream=Output(), bundle_root='synthetic',
        expected_manifest_sha256='a'*64, policy_sha256=hashlib.sha256(policy).hexdigest(),
        approval_reference_sha256='c'*64, _cleanup_evidence=evidence)))
    thread.start()
    try:
        assert close_blocked.wait(5)
        release_prepare.set()
        assert not owner_started.wait(0.5)
        assert evidence['clean'] is False
    finally:
        release_prepare.set()
        release_close.set()
        release_control.set()
        thread.join(5)
    assert not thread.is_alive()
    assert result == [2]


def test_missing_adapter_before_threads_has_no_ambiguous_owner(monkeypatch):
    from tools import wd_mind_full_runtime as runtime
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    monkeypatch.setattr(child, '_read_local_policy_frame', lambda *args, **kwargs: b'synthetic-policy')
    def refuse(**kwargs): raise ValueError('synthetic-pre-adapter-refusal')
    monkeypatch.setattr(child, 'create_local_manifest_query', refuse)
    evidence = {'clean': False}
    code = child._local_candidate_owner_entry(control_stream=io.BytesIO(), output_stream=io.BytesIO(),
        bundle_root='synthetic', expected_manifest_sha256='a'*64, policy_sha256='b'*64,
        approval_reference_sha256='c'*64, _cleanup_evidence=evidence)
    assert code == 2
    assert evidence['clean'] is True


def test_unknown_adapter_with_admission_never_proves_clean_stop(monkeypatch):
    from tools import wd_mind_full_runtime as runtime
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    monkeypatch.setattr(child, '_read_local_policy_frame', lambda *args, **kwargs: b'synthetic-policy')
    class UnknownAdapter:
        def start(self): return {'state': 'STARTING'}
        def status(self): return {'state': 'STARTING', 'owned': None}
        def close(self): return {'state': 'STOPPED', 'owned': False}
    monkeypatch.setattr(child, 'create_local_manifest_query', lambda **kwargs: UnknownAdapter())
    evidence = {'clean': False}
    code = child._local_candidate_owner_entry(control_stream=io.BytesIO(), output_stream=io.BytesIO(),
        bundle_root='synthetic', expected_manifest_sha256='a'*64, policy_sha256='b'*64,
        approval_reference_sha256='c'*64, _admission=object(), _cleanup_evidence=evidence)
    assert code == 2
    assert evidence['clean'] is False


def test_candidate_owner_monitor_keeps_service_alive_during_locked_query(monkeypatch):
    from tools import wd_mind_full_runtime as runtime
    from tools import wd_mind_runtime_process as process
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)

    ready, busy_seen, queries_done = (threading.Event() for _ in range(3))
    policy = b'synthetic-policy'
    source = io.BytesIO(b'MCL1' + struct.pack('>I', len(policy)) + policy)
    statuses, results, held_before_queries_done = [], [], []

    class Input:
        def read(self, size):
            value = source.read(size)
            if value:
                return value
            assert queries_done.wait(5)
            return b'S'

    class Output:
        def write(self, data):
            raw = bytes(data)
            state = json.loads(raw)['state']
            statuses.append(state)
            if state == 'HOLD' and not queries_done.is_set():
                held_before_queries_done.append(True)
            if state == 'READY':
                ready.set()
            return len(raw)
        def flush(self): pass

    class Owner:
        def __init__(self):
            self._owner = self
            self.lock = threading.Lock()
        def start(self): return {'state': 'STARTING'}
        def status(self):
            if not self.lock.acquire(blocking=False):
                busy_seen.set()
                return {'state': 'BUSY', 'owned': None}
            self.lock.release()
            return {'state': 'READY', 'owned': True}
        def located_candidates(self, request):
            with self.lock:
                if not results:
                    assert busy_seen.wait(5)
                results.append(request)
                return {'status': 'ABSTAIN'}
        def close(self): return {'state': 'STOPPED', 'owned': False}

    owner = Owner()
    class Service:
        def start(self):
            def query():
                try:
                    assert ready.wait(5)
                    owner.located_candidates('first')
                    owner.located_candidates('second')
                finally:
                    queries_done.set()
            self.worker = threading.Thread(target=query)
            self.worker.start()
        def status(self): return {'state': 'RUNNING'}
        def close(self, *, timeout):
            self.worker.join(timeout=1)
            return {'state': 'STOPPED' if not self.worker.is_alive() else 'HOLD'}

    monkeypatch.setattr(child, 'create_local_manifest_query', lambda **kwargs: owner)
    monkeypatch.setattr(process, 'create_local_candidate_service', lambda **kwargs: Service())
    result = child._local_candidate_owner_entry(control_stream=Input(), output_stream=Output(),
        bundle_root='synthetic', expected_manifest_sha256='a'*64,
        policy_sha256=hashlib.sha256(policy).hexdigest(), approval_reference_sha256='c'*64)
    assert busy_seen.is_set() and results == ['first', 'second']
    assert result == 0 and not held_before_queries_done


def test_fixed_local_candidate_owner_cli_uses_named_pins(monkeypatch):
    monkeypatch.setenv('WD_REBOOT_EXPECTED_MANIFEST_HASH', 'a'*64)
    monkeypatch.setattr(child, '_guarded_local_entry', lambda entry, **kwargs: entry(**kwargs))
    seen = []
    monkeypatch.setattr(child, '_local_candidate_owner_entry', lambda **kwargs: seen.append(kwargs) or 0)
    args = ['--mind-local-candidate-owner', '--policy-sha256', 'b'*64,
            '--approval-reference-sha256', 'c'*64]
    assert child._fixed_child_main(args) == 0
    assert seen[0]['expected_manifest_sha256'] == 'a'*64
    assert seen[0]['policy_sha256'] == 'b'*64 and seen[0]['approval_reference_sha256'] == 'c'*64
    assert child._fixed_child_main(args[:-1]) == 2
    args = ['--mind-local-candidate-owner', '--policy-path', 'C:\\synthetic\\policy.json',
            '--policy-sha256', 'b'*64, '--approval-reference-sha256', 'c'*64]
    assert child._fixed_child_main(args) == 0
    assert seen[-1]['policy_path'] == 'C:\\synthetic\\policy.json'


def test_local_candidate_owner_rejects_wrong_path_policy_hash(tmp_path, monkeypatch):
    from tools import wd_mind_full_runtime as runtime
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    policy = tmp_path / 'policy.json'
    policy.write_bytes(b'synthetic-private-policy')
    output = io.BytesIO()
    result = child._local_candidate_owner_entry(control_stream=io.BytesIO(), output_stream=output,
        bundle_root='synthetic', expected_manifest_sha256='a'*64, policy_sha256='b'*64,
        approval_reference_sha256='c'*64, policy_path=str(policy))
    assert result == 2
    assert b'synthetic-private-policy' not in output.getvalue()


@pytest.mark.parametrize('when', ['before', 'during'])
@pytest.mark.parametrize('method', ['candidates', 'located_candidates'])
def test_candidate_proxy_rechecks_real_external_policy_file(monkeypatch, tmp_path, when, method, synthetic_local_admission):
    from tools import wd_mind_full_runtime as runtime
    policy = b'synthetic-approved-policy'
    policy_path = tmp_path / 'policy.json'
    policy_path.write_bytes(policy)
    calls = []
    class Owner:
        def __init__(self): self.state='NEW'; self.retained=object()
        def start(self): self.state='READY'; return {'state':'READY','owned':True,'authority':'none'}
        def status(self): return {'state':self.state,'owned':True,'authority':'none'}
        def close(self): return {'state':'HOLD','owned':True,'authority':'none'}
        def invalidate_external_policy(self):
            self.state='HOLD'; self.retained=None
            return {'state':'HOLD','owned':True,'authority':'none'}
        def candidates(self, request):
            calls.append('query')
            if when == 'during': policy_path.write_bytes(b'x' * len(policy))
            return {'status':'ABSTAIN','candidates':[]}
        def located_candidates(self, request): return self.candidates(request)
    held=Owner()
    monkeypatch.setattr(runtime, 'create_operator_pinned_policy_anchor', lambda **kwargs: object())
    monkeypatch.setattr(runtime, 'assemble_full_runtime', lambda **kwargs: object())
    monkeypatch.setattr(runtime, 'create_manifest_worker_factory', lambda **kwargs: object())
    monkeypatch.setattr(runtime, 'create_single_manifest_owner', lambda **kwargs: held)
    adapter=child.create_local_manifest_query(enabled=True, bundle_root=str(tmp_path),
        expected_manifest_sha256='a'*64, identifier_policy_bytes=policy,
        expected_identifier_policy_sha256=hashlib.sha256(policy).hexdigest(),
        operator_approval_reference_sha256='c'*64, lane='codex-lead-1', policy_path=str(policy_path),
        _admission=synthetic_local_admission)
    assert adapter.start()['state'] == 'STARTING'
    assert adapter.wait(timeout=5)['state'] == 'READY'
    if when == 'before': policy_path.write_bytes(b'x' * len(policy))
    with pytest.raises(child.LaunchRefused): getattr(adapter, method)({})
    assert calls == ([] if when == 'before' else ['query'])
    assert held.retained is None and held.state == 'HOLD' and adapter.status()['state'] == 'HOLD'
    policy_path.write_bytes(policy)
    with pytest.raises(child.LaunchRefused): getattr(adapter, method)({})


@pytest.mark.skipif(os.name != 'nt', reason='Windows junction test')
def test_local_candidate_owner_rejects_policy_through_real_junction(tmp_path, monkeypatch):
    from tools import wd_mind_full_runtime as runtime
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    real = tmp_path / 'real'
    real.mkdir()
    policy = real / 'policy.json'
    raw = b'synthetic-approved-policy'
    policy.write_bytes(raw)
    link = tmp_path / 'junction'
    made = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(link), str(real)],
                          capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    if made.returncode != 0:
        pytest.skip('junction creation unavailable in synthetic test directory')
    output = io.BytesIO()
    result = child._local_candidate_owner_entry(control_stream=io.BytesIO(), output_stream=output,
        bundle_root='synthetic', expected_manifest_sha256='a'*64,
        policy_sha256=hashlib.sha256(raw).hexdigest(), approval_reference_sha256='c'*64,
        policy_path=str(link / 'policy.json'))
    assert result == 2 and raw not in output.getvalue()


def test_local_candidate_owner_rejects_missing_manifest_and_reports_hold(tmp_path, monkeypatch, synthetic_local_admission):
    from tools import wd_mind_full_runtime as runtime
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    held = threading.Event()
    policy = b'synthetic-private-policy'
    source = io.BytesIO(b'MCL1' + struct.pack('>I', len(policy)) + policy)
    class Input:
        def read(self, size):
            value = source.read(size)
            if value: return value
            assert held.wait(5)
            return b'S'
    statuses = []
    class Output:
        def write(self, data):
            raw = bytes(data)
            statuses.append(json.loads(raw))
            if statuses[-1]['state'] == 'HOLD': held.set()
            return len(raw)
        def flush(self): pass
    result = child._local_candidate_owner_entry(control_stream=Input(), output_stream=Output(),
        bundle_root=str(tmp_path), expected_manifest_sha256='a'*64,
        policy_sha256=hashlib.sha256(policy).hexdigest(), approval_reference_sha256='c'*64,
        _admission=synthetic_local_admission)
    assert result == 2
    assert [row['state'] for row in statuses] == ['STARTING', 'HOLD', 'STOPPED']
    assert policy.decode() not in json.dumps(statuses)


@pytest.mark.parametrize('mutation', ['none', 'source', 'approval'])
def test_local_post_close_rechecks_real_synthetic_source_and_approval(monkeypatch, tmp_path, mutation):
    from test_wd_mind_content_snapshot_package import _flat_owner_fixture, fixtures, package
    from tools import wd_mind_full_runtime as runtime
    source_owner, proposals, snapshot, _ = _flat_owner_fixture(
        package.__wrapped__(), fixtures.__wrapped__(), tmp_path, monkeypatch)
    chosen = None
    for proposal in proposals:
        try:
            chosen = source_owner.resolve(proposals=[proposal], lane='codex-lead-1', scope='own')['results'][0]
            break
        except ValueError:
            pass
    assert chosen is not None
    wrapper_bundle = tmp_path / 'post-close-bundle'
    wrapper_bundle.mkdir()
    descriptor_name = 'wd-mind-identifier-policy-approval.json'
    policy_sha, approval_sha = 'b' * 64, 'c' * 64
    descriptor = json.dumps({'schema': 'wd.mind-identifier-policy-approval.v1',
        'identifier_policy_sha256': policy_sha, 'approval_reference_sha256': approval_sha}).encode()
    descriptor_path = wrapper_bundle / descriptor_name
    descriptor_path.write_bytes(descriptor)
    manifest = json.dumps({'schema_version': 1, 'files': {descriptor_name: hashlib.sha256(descriptor).hexdigest()}}).encode()
    (wrapper_bundle / 'deployment-manifest.json').write_bytes(manifest)
    manifest_sha = hashlib.sha256(manifest).hexdigest()
    controls = (b'synthetic-fleet', b'synthetic-roster', b'synthetic-policy')
    class Owner:
        def __init__(self):
            self._snapshot, self._lock, self._state = snapshot, threading.Lock(), 'READY'
        def close(self):
            if mutation == 'source':
                path = Path(snapshot['messages']['source_path'])
                lines = path.read_bytes().splitlines(keepends=True)
                index = next(i for i, line in enumerate(lines)
                    if runtime.reader._sha(runtime.reader._canonical(json.loads(line))) == chosen['source_event_sha256'])
                old = lines[index]
                lines[index] = old.replace(b'"message":', b'"messagE":', 1)
                assert lines[index] != old and len(lines[index]) == len(old)
                path.write_bytes(b''.join(lines))
            elif mutation == 'approval':
                descriptor_path.write_bytes(descriptor.replace(b'"c' + b'c'*63, b'"d' + b'c'*63))
            self._state = 'STOPPED'
            return {'state':'STOPPED', 'owned':False, 'authority':'none'}
    monkeypatch.setattr(runtime, '_bundle_inputs', lambda root, pin: controls)
    monkeypatch.setattr(runtime, '_local_generation_policy_pin', lambda owner: policy_sha)
    adapter = child._LocalManifestQuery('codex-lead-1', str(wrapper_bundle), manifest_sha,
        b'synthetic-policy', policy_sha, approval_sha, 5, 0, 5)
    adapter._owner, adapter._state = Owner(), 'READY'
    answer = _verified_parent_result(timestamp=chosen['event_time_utc_floor'],
                                      age=chosen['event_age_seconds_upper_bound'])
    answer['fleet_sha256'] = hashlib.sha256(controls[0]).hexdigest()
    answer['results'][0].update(text=runtime.reader.decode_text(chosen['text']),
        source_event_sha256=chosen['source_event_sha256'], source_agent=chosen['source_agent'])
    adapter._pending_result = runtime.reader._canonical(answer)
    assert adapter.close()['state'] == 'STOPPED'
    if mutation == 'none':
        delivered = json.loads(adapter.finalize_result())
        assert delivered['results'][0]['source_event_sha256'] == chosen['source_event_sha256']
        assert delivered['current_status_freshness'] == 'UNKNOWN'
    else:
        with pytest.raises(child.LaunchRefused): adapter.finalize_result()
    assert adapter._pending_result is None


def test_local_post_close_selects_identical_duplicate_chunks_deterministically():
    from tools import wd_mind_content_query_readonly as reader
    source = 'a' * 64
    def record(doc, chunk, *, available='2026-01-01T00:01:00Z'):
        return {'record_id': doc, 'kind':'event', 'time_utc':'2026-01-01T00:00:00Z',
            'stored_text':reader.encode_text('same synthetic chunk'), 'meta':{'agent':'codex-lead-1'},
            'provenance':{'source_event_ref':source, 'event_ref':'ref-'+doc, 'chunk_index':chunk,
                          'chunk_count':2, 'available_utc':available}}
    one, two = record('b', 1), record('a', 0)
    hit = {'source_event_sha256':source, 'event_time_utc_floor':'2026-01-01T00:00:00Z',
           'source_agent':'codex-lead-1', 'text':'same synthetic chunk'}
    used = set()
    assert child._select_local_source_record(reader, [one, two], hit, used)['record_id'] == 'a'
    assert child._select_local_source_record(reader, [one, two], hit, used)['record_id'] == 'b'
    with pytest.raises(ValueError): child._select_local_source_record(reader, [one, two], hit, used)
    with pytest.raises(ValueError):
        child._select_local_source_record(reader, [one, record('a', 0, available='2026-01-02T00:01:00Z')], hit, set())


def digest(data):
    return hashlib.sha256(data).hexdigest()


def _fixture_pinned_verifier_bytes(data):
    expected = anchor._PACKAGED_VERIFIER_SHA256
    if digest(data) == expected:
        return data
    candidate = data.replace(b'\r\n', b'\n')
    if digest(candidate) != expected:
        raise ValueError('synthetic_verifier_pin_mismatch')
    return candidate


def test_synthetic_verifier_checkout_translation_never_changes_the_pin():
    source = Path(__file__).resolve().parents[2] / 'tools/wd_mind_runtime_manifest.py'
    canonical = _fixture_pinned_verifier_bytes(source.read_bytes())
    assert digest(canonical) == anchor._PACKAGED_VERIFIER_SHA256
    assert _fixture_pinned_verifier_bytes(canonical.replace(b'\n', b'\r\n')) == canonical
    with pytest.raises(ValueError, match='synthetic_verifier_pin_mismatch'):
        _fixture_pinned_verifier_bytes(canonical + b'# altered\n')


@pytest.fixture
def synthetic_windows_subprocess(monkeypatch):
    # Only tests replacing Popen use these missing POSIX attributes. No native
    # process launch or production platform guard is changed by this fixture.
    if os.name != 'nt':
        monkeypatch.setattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000, raising=False)
        monkeypatch.setattr(subprocess, 'STARTUPINFO', SimpleNamespace, raising=False)


def _patch_windows_job_platform(monkeypatch):
    class WindowsOS:
        name = 'nt'
        def __getattr__(self, attr): return getattr(os, attr)
    real_os, real_job = child.os, child._Job

    def make_job(*args, **kwargs):
        # Keep POSIX path validation genuine; simulate Windows only while
        # constructing the mocked Job object, including failure cleanup.
        monkeypatch.setattr(child, 'os', WindowsOS())
        try:
            return real_job(*args, **kwargs)
        finally:
            monkeypatch.setattr(child, 'os', real_os)

    monkeypatch.setattr(child, '_Job', make_job)


@pytest.fixture
def synthetic_windows_job_platform(monkeypatch):
    if os.name != 'nt':
        _patch_windows_job_platform(monkeypatch)


def test_synthetic_job_platform_restores_os_after_failed_constructor(monkeypatch):
    real_os = child.os
    observed = []

    def failing_job(**kwargs):
        observed.append(child.os.name)
        raise RuntimeError('synthetic failure')

    monkeypatch.setattr(child, '_Job', failing_job)
    _patch_windows_job_platform(monkeypatch)
    assert child.os is real_os
    with pytest.raises(RuntimeError, match='synthetic failure'):
        child._Job()
    assert observed == ['nt']
    assert child.os is real_os


@pytest.fixture
def staged(candidate, tmp_path, monkeypatch):
    bundle = candidate["bundle_root"]
    source = Path(__file__).resolve().parents[2]
    files = {}
    for name in ("wd_mind_runtime_bootstrap.py", "wd_mind_runtime_manifest.py", "wd_mind_runtime_mutex.py", "wd_mind_runtime_process.py",
                 "wd_mind_runtime_child.py", "wd_mind_interpreter_identity.py"):
        relative = "tools-bootstrap/tools/" + name
        path = bundle / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        data = (source / "tools" / name).read_bytes()
        if name == 'wd_mind_runtime_manifest.py':
            data = _fixture_pinned_verifier_bytes(data)
        path.write_bytes(data)
        files[relative] = digest(path.read_bytes())
    runtime_manifest = bundle / "wd-mind-runtime-manifest.json"
    runtime_manifest.write_bytes(candidate["runtime_manifest"].read_bytes())
    files[runtime_manifest.name] = digest(runtime_manifest.read_bytes())
    interpreter_root = tmp_path / "interpreter-tree"
    records = []
    for relative, role, raw in (("bin/python.exe", "interpreter", b"synthetic executable"),
                                ("lib/stdlib.zip", "stdlib", b"synthetic stdlib"),
                                ("dll/runtime.dll", "dll", b"synthetic DLL")):
        path = interpreter_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        records.append({"role": role, "path": relative, "size": len(raw), "sha256": digest(raw)})
    interpreter = interpreter_root / "bin/python.exe"
    descriptor = bundle / "wd-mind-interpreter-identity.json"
    descriptor.write_bytes(json.dumps({"schema": "wd.q4-interpreter-identity.v1", "files": records}).encode())
    files[descriptor.name] = digest(descriptor.read_bytes())
    deployment = bundle / "deployment-manifest.json"
    deployment.write_bytes(json.dumps({"schema_version": 1, "files": files}, sort_keys=True).encode())
    monkeypatch.setattr(child, "__file__", str(bundle / "tools-bootstrap/tools/wd_mind_runtime_child.py"))
    monkeypatch.setattr(anchor, "__file__", str(bundle / "tools-bootstrap/tools/wd_mind_runtime_process.py"))
    monkeypatch.setattr(identity, "__file__", str(bundle / "tools-bootstrap/tools/wd_mind_interpreter_identity.py"))
    monkeypatch.setattr(verifier, "__file__", str(bundle / "tools-bootstrap/tools/wd_mind_runtime_manifest.py"))
    args = {"runtime-root": str(candidate["runtime_root"]), "runtime-manifest": str(runtime_manifest),
            "runtime-manifest-sha256": digest(runtime_manifest.read_bytes()),
            "runtime-generation": candidate["generation"], "runtime-revision": candidate["revision"],
            "memory": str(tmp_path / "synthetic-memory"), "descriptor": str(tmp_path / "descriptor.json"),
            "descriptor-sha256": "b" * 64, "lock-file": str(tmp_path / "read.lock"), "lock-timeout": "1"}
    return {"bundle_root": bundle, "deployment_manifest_sha256": digest(deployment.read_bytes()),
            "interpreter": interpreter, "interpreter_sha256": digest(interpreter.read_bytes()),
            "interpreter_root": interpreter_root, "tuple_args": args,
            "query": b'{"situation":"synthetic-private","time":"2026-01-01T00:00:00Z"}',
            "timeout": 5, "scratch_root": None}




class SyntheticStdin:
    def __init__(self): self.contents = b""
    def write(self, data): self.contents += data
    def close(self): pass


class SyntheticProcess:
    def __init__(self, *, timeout=False):
        self.stdin = SyntheticStdin()
        self.stdout = io.BytesIO(b'{"schema":"wd.bridge-mind-q4-private-read.v1",'
                                 b'"identity":"caller_declared_not_authenticated",'
                                 b'"status":"REFUSED","reason":"reader_busy","results":[]}')
        self.returncode = 2
        self.timeout = timeout
        self.waits = 0
    def wait(self, timeout=None):
        self.waits += 1
        if self.timeout and self.waits == 1: raise subprocess.TimeoutExpired("synthetic", timeout)
        return self.returncode
    def poll(self): return self.returncode
    def kill(self): pytest.fail("unexpected kill")








@pytest.mark.skipif(os.name != "nt", reason="Windows Job API requires Windows")
def test_real_windows_job_object_creation_and_close_without_process():
    job = child._Job()
    assert job.handle
    job.close()
    assert not job.handle


@pytest.fixture
def audit_scope(tmp_path, monkeypatch):
    audit = tmp_path / ".codex-audit"
    scope = audit / "wd-mind-runtime"
    scratch = scope / "scratch"
    scratch.mkdir(parents=True)
    monkeypatch.setattr(child, "_AUDIT_ROOT", audit)
    return audit, scope / "q4-reader.lock", scratch


@pytest.mark.parametrize("case", ["lock_live", "lock_sibling", "lock_traversal", "scratch_live",
                                  "scratch_sibling", "scratch_traversal"])
def test_non_audit_or_traversal_write_targets_refuse_without_side_effects(audit_scope, tmp_path, case):
    audit, lock, _ = audit_scope
    scratch = None
    live = tmp_path / "live"
    live.mkdir()
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    if case == "lock_live": lock = live / "q4-reader.lock"
    elif case == "lock_sibling": lock = audit / "other.lock"
    elif case == "lock_traversal": lock = audit / "wd-mind-runtime" / ".." / "q4-reader.lock"
    elif case == "scratch_live": scratch = live
    elif case == "scratch_sibling": scratch = audit
    else: scratch = audit / "wd-mind-runtime" / ".." / "scratch"
    with pytest.raises(child.LaunchRefused, match="^audit_write_scope_refused$"):
        child._validate_audit_write_scope(lock, scratch)
    assert before == sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    assert not any(live.iterdir())


@pytest.mark.parametrize("target", ["scope", "lock"])
def test_reparse_target_refuses_before_any_write(audit_scope, monkeypatch, target):
    _, lock, scratch = audit_scope
    original = child.os.lstat
    def fake_lstat(path, *args, **kwargs):
        if Path(path) == (scratch.parent if target == "scope" else lock):
            return SimpleNamespace(st_mode=(stat.S_IFDIR if target == "scope" else stat.S_IFREG),
                                   st_file_attributes=0x400, st_nlink=1)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(child.os, "lstat", fake_lstat)
    with pytest.raises(child.LaunchRefused, match="^audit_write_scope_refused$"):
        child._validate_audit_write_scope(lock, None)


def test_validated_audit_scope_does_not_create_file_or_scratch(audit_scope):
    _, lock, scratch = audit_scope
    child._require_safe_writer_scope(lock, None)
    assert not lock.exists() and not any(scratch.iterdir())




@pytest.fixture
def staged_e5(staged, monkeypatch):
    bundle = staged["bundle_root"]
    deployment = bundle / "deployment-manifest.json"
    manifest = json.loads(deployment.read_text())
    source = Path(__file__).resolve().parents[2]
    for name in ("wd_mind_e5_worker.py", "wd_mind_e5_client.py"):
        path = bundle / "tools-bootstrap/tools" / name
        path.write_bytes((source / "tools" / name).read_bytes())
        manifest["files"]["tools-bootstrap/tools/" + name] = digest(path.read_bytes())
    monkeypatch.setattr(e5_client, "__file__", str(bundle / "tools-bootstrap/tools/wd_mind_e5_client.py"))
    weights = b"synthetic weights; no model"
    weights_path = Path(staged["tuple_args"]["runtime-root"]) / "model/model.safetensors"
    weights_path.write_bytes(weights)
    runtime_path = bundle / "wd-mind-runtime-manifest.json"
    runtime = json.loads(runtime_path.read_text())
    runtime["files"].append({"path": "model/model.safetensors", "size": len(weights), "sha256": digest(weights)})
    runtime_path.write_text(json.dumps(runtime, sort_keys=True))
    staged["tuple_args"]["runtime-manifest-sha256"] = digest(runtime_path.read_bytes())
    manifest["files"][runtime_path.name] = digest(runtime_path.read_bytes())
    policy = {"truncate_chars": 2000, "normalize": True, "dtype": "float32", "dim": 384}
    binding = {"schema": "wd.mind-memory-encoder-binding.v1",
               "encoder": {"model": "intfloat/multilingual-e5-small", "revision": staged["tuple_args"]["runtime-revision"],
                           "weights_sha256": digest(weights)},
               "code_sha256": manifest["files"]["tools-bootstrap/tools/wd_mind_e5_worker.py"],
               "passage_policy": dict(policy, prefix="passage: "), "query_policy": dict(policy, prefix="query: ")}
    binding_path = bundle / "wd-mind-e5-encoder-binding.json"
    binding_path.write_text(json.dumps(binding, sort_keys=True))
    manifest["files"][binding_path.name] = digest(binding_path.read_bytes())
    deployment.write_text(json.dumps(manifest, sort_keys=True))
    staged["deployment_manifest_sha256"] = digest(deployment.read_bytes())
    return staged






@pytest.fixture
def staged_graceful(staged_e5, monkeypatch):
    from tools import wd_mind_e5_lifecycle as lifecycle
    bundle = staged_e5["bundle_root"]
    relative = "tools-bootstrap/tools/wd_mind_e5_lifecycle.py"
    source = Path(__file__).resolve().parents[2] / "tools/wd_mind_e5_lifecycle.py"
    path = bundle / relative
    path.write_bytes(source.read_bytes())
    deployment = bundle / "deployment-manifest.json"
    manifest = json.loads(deployment.read_bytes())
    manifest["files"][relative] = digest(path.read_bytes())
    deployment.write_text(json.dumps(manifest, sort_keys=True))
    staged_e5["deployment_manifest_sha256"] = digest(deployment.read_bytes())
    monkeypatch.setattr(lifecycle, "__file__", str(path))
    monkeypatch.setattr(child, "_require_safe_writer_scope", lambda *args: None)
    return {key: value for key, value in staged_e5.items() if key != "query"}


def test_bulk_worker_only_launch_is_pinned_and_preserves_owner(staged_graceful, monkeypatch, synthetic_windows_subprocess, synthetic_worker_admission):
    from test_wd_mind_e5_lifecycle import Job as OwnedJob, Process as OwnedProcess, finish
    from test_wd_mind_e5_client import response
    process = OwnedProcess(response(0, 0), on_eof=False)
    calls = {}
    class Job(OwnedJob):
        def __init__(self, *, kill_on_close, _owner=None):
            assert kill_on_close is False
            super().__init__()
        def attach(self, value): assert value is process
    def popen(argv, **kwargs):
        calls['argv'] = argv
        calls['bulk_env_keys_present'] = any('BULK' in key for key in kwargs['env'])
        return process
    monkeypatch.setattr(child, '_Job', Job)
    monkeypatch.setattr(subprocess, 'Popen', popen)
    options = dict(staged_graceful)
    options['tuple_args'] = {k:v for k,v in options['tuple_args'].items()
                             if k not in ('memory','descriptor','descriptor-sha256')}
    result = child.start_e5_worker_graceful(enabled=True, worker_only=True,
                bulk_contract_sha256=child.BULK_CONTRACT_SHA256, **synthetic_worker_admission, **options)
    owner = result['owner']
    try:
        assert result['status'] == 'READY' and result['bulk_contract_sha256'] == child.BULK_CONTRACT_SHA256
        assert result['client']._bulk_contract_sha256 == owner._bulk_contract_sha256 == child.BULK_CONTRACT_SHA256
        assert '--worker-only' in calls['argv'] and '--bulk-contract-sha256' in calls['argv']
        assert all('--'+key not in calls['argv'] for key in ('memory','descriptor','descriptor-sha256'))
        assert not calls['bulk_env_keys_present']
        assert result['client'].close(timeout=0.02)['state'] == 'HOLD'
    finally:
        finish(owner, process)


@pytest.mark.parametrize('fault', ['bad_contract', 'wrong_shape', 'worker_only_int'])
def test_bulk_worker_only_refuses_bad_contract_before_spawn(staged_graceful, monkeypatch, fault, synthetic_worker_admission):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a,**k: pytest.fail('spawn forbidden'))
    options = dict(staged_graceful)
    options['tuple_args'] = {k:v for k,v in options['tuple_args'].items()
                             if k not in ('memory','descriptor','descriptor-sha256')}
    flag = 1 if fault == 'worker_only_int' else True
    contract = '0'*64 if fault == 'bad_contract' else child.BULK_CONTRACT_SHA256
    if fault == 'wrong_shape': options['tuple_args']['memory'] = 'untrusted fake'
    with pytest.raises(child.LaunchRefused):
        child.start_e5_worker_graceful(enabled=True, worker_only=flag, bulk_contract_sha256=contract,
                                       **synthetic_worker_admission, **options)


@pytest.mark.parametrize('name',['wd_mind_e5_worker.py','wd_mind_runtime_bootstrap.py'])
def test_bulk_refuses_manifest_pinned_code_without_contract(staged_graceful, monkeypatch, name, synthetic_worker_admission):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a,**k: pytest.fail('spawn forbidden'))
    bundle=staged_graceful['bundle_root']
    path=bundle/'tools-bootstrap/tools'/name
    raw=path.read_bytes()
    assert child.BULK_CONTRACT in raw
    path.write_bytes(raw.replace(child.BULK_CONTRACT,b'not-the-bulk-contract'))
    manifest_path=bundle/'deployment-manifest.json'
    manifest=json.loads(manifest_path.read_bytes())
    manifest['files']['tools-bootstrap/tools/'+name]=digest(path.read_bytes())
    if name == 'wd_mind_e5_worker.py':
        binding_path=bundle/'wd-mind-e5-encoder-binding.json'
        binding=json.loads(binding_path.read_bytes())
        binding['code_sha256']=digest(path.read_bytes())
        binding_path.write_bytes(json.dumps(binding,sort_keys=True).encode())
        manifest['files'][binding_path.name]=digest(binding_path.read_bytes())
    manifest_path.write_bytes(json.dumps(manifest,sort_keys=True).encode())
    options=dict(staged_graceful, deployment_manifest_sha256=digest(manifest_path.read_bytes()))
    options['tuple_args']={k:v for k,v in options['tuple_args'].items()
                           if k not in ('memory','descriptor','descriptor-sha256')}
    with pytest.raises(child.LaunchRefused):
        child.start_e5_worker_graceful(enabled=True,worker_only=True,
            bulk_contract_sha256=child.BULK_CONTRACT_SHA256, **synthetic_worker_admission, **options)


def test_graceful_default_does_not_read_pins_or_create_job(staged_e5, monkeypatch):
    monkeypatch.setattr(anchor, "_read_pinned_file", lambda *a, **k: pytest.fail("read before opt in"))
    monkeypatch.setattr(child, "_Job", lambda *a, **k: pytest.fail("job before opt in"))
    with pytest.raises(child.LaunchRefused):
        child.start_e5_worker_graceful(**{k: v for k, v in staged_e5.items() if k != "query"})


@pytest.mark.parametrize("fault", ["missing_pin", "lifecycle", "origin", "worker", "binding", "weights", "bootstrap", "identity"])
def test_graceful_all_pins_before_spawn(staged_graceful, monkeypatch, fault, synthetic_worker_admission):
    from tools import wd_mind_e5_lifecycle as lifecycle
    bundle = staged_graceful["bundle_root"]
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("process before pins"))
    if fault == "missing_pin":
        deployment = bundle / "deployment-manifest.json"
        manifest = json.loads(deployment.read_bytes())
        del manifest["files"]["tools-bootstrap/tools/wd_mind_e5_lifecycle.py"]
        deployment.write_text(json.dumps(manifest, sort_keys=True))
        staged_graceful["deployment_manifest_sha256"] = digest(deployment.read_bytes())
    elif fault == "origin":
        monkeypatch.setattr(lifecycle, "__file__", str(bundle / "wrong.py"))
    else:
        paths = {"lifecycle": bundle / "tools-bootstrap/tools/wd_mind_e5_lifecycle.py",
                 "worker": bundle / "tools-bootstrap/tools/wd_mind_e5_worker.py",
                 "binding": bundle / "wd-mind-e5-encoder-binding.json",
                 "weights": Path(staged_graceful["tuple_args"]["runtime-root"]) / "model/model.safetensors",
                 "bootstrap": bundle / "tools-bootstrap/tools/wd_mind_runtime_bootstrap.py",
                 "identity": bundle / "wd-mind-interpreter-identity.json"}
        paths[fault].write_bytes(b"tampered")
    with pytest.raises(child.LaunchRefused):
        child.start_e5_worker_graceful(enabled=True, **synthetic_worker_admission, **staged_graceful)


@pytest.mark.parametrize("fault", [None, "attach", "handshake", "late_spawn"])
def test_graceful_launch_retains_owner_on_post_spawn_failure(staged_graceful, monkeypatch, fault, synthetic_windows_subprocess, synthetic_worker_admission):
    from test_wd_mind_e5_lifecycle import Job as OwnedJob, Process as OwnedProcess, finish
    from test_wd_mind_e5_client import response
    calls = []
    process = OwnedProcess(response(0, 0) if fault != "handshake" else b"", on_eof=False)
    clock = [time.monotonic()]
    monkeypatch.setattr(child, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    class Job(OwnedJob):
        def __init__(self, *, kill_on_close, _owner=None):
            assert kill_on_close is False
            super().__init__()
            calls.append("job")

        def attach(self, value):
            assert value is process
            calls.append("attach")
            if fault == "attach":
                raise ValueError("synthetic private failure")

    def popen(args, **kwargs):
        calls.append("spawn")
        # No assertions here: a traceback can render kwargs, including the env.
        calls.append(("spawn_options", kwargs.get("stderr") == subprocess.PIPE,
                      kwargs.get("env", {}).get("HF_HUB_OFFLINE") == "1",
                      args[1:4] == ["-I", "-S", "-B"]))
        if fault == "late_spawn":
            clock[0] += 2
        return process

    monkeypatch.setattr(child, "_Job", Job)
    monkeypatch.setattr(subprocess, "Popen", popen)
    staged_graceful["timeout"] = 0.03 if fault == "handshake" else 1
    result = child.start_e5_worker_graceful(enabled=True, **synthetic_worker_admission, **staged_graceful)
    owner = result["owner"]
    try:
        assert calls[:2] == ["job", "spawn"]
        assert ("spawn_options", True, True, True) in calls
        assert owner._process is process and result["authority"] == "none"
        if fault is None:
            assert result["status"] == "READY" and process.written == b"\0"
            assert result["client"].close(timeout=0.02)["owned"]
        else:
            assert result["status"] == "HOLD" and result["client"] is None
            assert owner.status()["owned"] and not owner._job.closed
            if fault in ("attach", "late_spawn"):
                assert process.written == b""
    finally:
        finish(owner, process)


def test_graceful_launch_preserves_per_call_timeout_after_slow_start(staged_graceful, monkeypatch, synthetic_windows_subprocess, synthetic_worker_admission):
    from test_wd_mind_e5_lifecycle import Job as OwnedJob, Process as OwnedProcess, finish
    from test_wd_mind_e5_client import response
    process = OwnedProcess(response(0, 0), on_eof=False)
    clock = [time.monotonic()]
    monkeypatch.setattr(child, 'time', SimpleNamespace(monotonic=lambda: clock[0]))

    class Job(OwnedJob):
        def __init__(self, *, kill_on_close, _owner=None):
            assert kill_on_close is False
            super().__init__()
        def attach(self, value):
            assert value is process

    def popen(*args, **kwargs):
        clock[0] += 0.8
        return process

    monkeypatch.setattr(child, '_Job', Job)
    monkeypatch.setattr(subprocess, 'Popen', popen)
    staged_graceful['timeout'] = 1
    result = child.start_e5_worker_graceful(enabled=True, **synthetic_worker_admission, **staged_graceful)
    owner = result['owner']
    try:
        assert result['status'] == 'READY'
        assert result['client']._timeout == 1.0
    finally:
        finish(owner, process)


def test_graceful_launch_rechecks_deadline_after_handshake(staged_graceful, monkeypatch, synthetic_windows_subprocess, synthetic_worker_admission):
    from test_wd_mind_e5_lifecycle import Job as OwnedJob, Process as OwnedProcess, finish
    from test_wd_mind_e5_client import response
    from tools import wd_mind_e5_client as e5
    process = OwnedProcess(response(0, 0), on_eof=False)
    clock = [time.monotonic()]
    monkeypatch.setattr(child, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    real_client = e5.GracefulE5WorkerClient

    class LateClient(real_client):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            clock[0] += 2

    class Job(OwnedJob):
        def __init__(self, *, kill_on_close, _owner=None):
            assert kill_on_close is False
            super().__init__()
        def attach(self, value):
            assert value is process

    monkeypatch.setattr(e5, 'GracefulE5WorkerClient', LateClient)
    monkeypatch.setattr(child, '_Job', Job)
    monkeypatch.setattr(subprocess, 'Popen', lambda *args, **kwargs: process)
    staged_graceful['timeout'] = 1
    captured = []
    result = child.start_e5_worker_graceful(enabled=True, owner_sink=captured.append,
                                            **synthetic_worker_admission, **staged_graceful)
    owner = result['owner']
    try:
        assert captured == [owner] and result['status'] == 'HOLD'
        assert result['client'] is None and owner.status()['owned'] and not owner._job.closed
    finally:
        finish(owner, process)


def test_graceful_expired_preflight_never_spawns(staged_graceful, monkeypatch, synthetic_worker_admission):
    clock = [time.monotonic()]
    monkeypatch.setattr(child, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    original = anchor.verify_runtime_process_preflight

    def verify(**kwargs):
        original(**kwargs)
        clock[0] += 100

    monkeypatch.setattr(anchor, "verify_runtime_process_preflight", verify)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("late spawn"))
    with pytest.raises(child.LaunchRefused):
        child.start_e5_worker_graceful(enabled=True, **synthetic_worker_admission, **staged_graceful)


def test_graceful_spawn_failure_retains_uncertain_owner(staged_graceful, monkeypatch, synthetic_worker_admission):
    from test_wd_mind_e5_lifecycle import Job
    from tools import wd_mind_e5_lifecycle as lifecycle
    job = Job()
    stop_synthetic_keeper = threading.Event()
    monkeypatch.setattr(lifecycle._Owner, '_keep', lambda self: stop_synthetic_keeper.wait(3))
    monkeypatch.setattr(lifecycle, '_active', None)
    monkeypatch.setattr(child, "_Job", lambda **kwargs: job)

    def unavailable(*args, **kwargs):
        raise OSError("private synthetic path")

    monkeypatch.setattr(subprocess, "Popen", unavailable)
    result = child.start_e5_worker_graceful(enabled=True, **synthetic_worker_admission, **staged_graceful)
    owner = result['owner']
    try:
        assert result['status'] == 'HOLD' and result['client'] is None
        assert owner._job is job and owner._spawn_state == 'ATTEMPTED'
        assert owner.status()['owned'] is True and not job.closed
    finally:
        # No native process was created; only the synthetic keeper is stopped.
        stop_synthetic_keeper.set()
        owner._keeper.join(timeout=2)
        assert not owner._keeper.is_alive()


def test_cooperative_job_close_failure_retains_actual_handle():
    job = object.__new__(child._Job)
    job.handle, job.kill_on_close = 123, False
    job.api = SimpleNamespace(CloseHandle=lambda handle: 0)
    with pytest.raises(child.LaunchRefused, match="^job_close_failed$"):
        job.close()
    assert job.handle == 123


@pytest.mark.parametrize("registered", [False, True])
def test_review_bind_failure_keeps_live_process_owned(staged_graceful, monkeypatch, registered, synthetic_windows_subprocess,
                                                      synthetic_worker_admission, synthetic_held_keeper):
    from test_wd_mind_e5_lifecycle import Job, Process
    from tools import wd_mind_e5_lifecycle as lifecycle
    process, job = Process(on_eof=False), Job()
    monkeypatch.setattr(child, "_Job", lambda **kwargs: job)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    original = lifecycle._Owner.bind

    def fail(owner, worker):
        if registered:
            original(owner, worker)
        raise RuntimeError("synthetic pre/post-bind failure")

    monkeypatch.setattr(lifecycle._Owner, "bind", fail)
    try:
        result = child.start_e5_worker_graceful(enabled=True, **synthetic_worker_admission, **staged_graceful)
        owner = result["owner"]
        assert result["status"] == "HOLD" and owner.status()["owned"]
        assert owner._process is process and owner._job is job
        assert owner._spawn_state == ('BOUND' if registered else 'ATTEMPTED')
        assert process.poll() is None and not job.closed
        with pytest.raises(lifecycle.LifecycleRefused):
            lifecycle.reserve(enabled=True)
    finally:
        process.finish()


@pytest.mark.parametrize("registered", [False, True])
def test_review_set_job_failure_retains_job_only_hold_channel(staged_graceful, monkeypatch, registered,
                                                             synthetic_worker_admission, synthetic_held_keeper):
    from test_wd_mind_e5_lifecycle import Job
    from tools import wd_mind_e5_lifecycle as lifecycle
    job = Job()
    original_set = lifecycle._Owner.set_job

    def fail(owner, supplied):
        if registered:
            original_set(owner, supplied)
        raise RuntimeError("synthetic pre/post-registration failure")

    monkeypatch.setattr(child, "_Job", lambda **kwargs: job)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("spawn forbidden"))
    monkeypatch.setattr(lifecycle._Owner, "set_job", fail)
    result = child.start_e5_worker_graceful(enabled=True, **synthetic_worker_admission, **staged_graceful)
    assert result["status"] == "HOLD" and result["owner"]._job is job
    assert result["owner"].status()["pid"] is None
    assert result['owner']._job_state == ('BOUND' if registered else 'ATTEMPTED')
    assert result['owner'].status()['owned'] is True and not job.closed
    with pytest.raises(lifecycle.LifecycleRefused):
        lifecycle.reserve(enabled=True)


def test_review_uncertain_spawn_retained_with_synthetic_keeper(staged_graceful, monkeypatch,
                                                              synthetic_worker_admission, synthetic_held_keeper):
    from test_wd_mind_e5_lifecycle import Job
    from tools import wd_mind_e5_lifecycle as lifecycle
    job, failed = Job(), threading.Event()

    def close():
        failed.set()
        raise OSError("synthetic private error")

    def unavailable(*args, **kwargs):
        raise OSError("synthetic spawn failure")

    job.close = close
    monkeypatch.setattr(child, "_Job", lambda **kwargs: job)
    monkeypatch.setattr(subprocess, "Popen", unavailable)
    result = child.start_e5_worker_graceful(enabled=True, **synthetic_worker_admission, **staged_graceful)
    owner = result["owner"]
    assert result["status"] == "HOLD" and not failed.is_set()
    assert owner._spawn_state == 'ATTEMPTED'
    assert owner.status()["owned"] and owner._job is job and owner._process is None
    assert "private" not in repr(result["lifecycle"]) and "private" not in repr(owner.status())
    assert owner._keeper.is_alive() and not owner._keeper.daemon
    with pytest.raises(lifecycle.LifecycleRefused):
        lifecycle.reserve(enabled=True)


def test_review_partial_job_constructor_failure_retains_created_handle(staged_graceful, monkeypatch, synthetic_windows_job_platform,
                                                                       synthetic_worker_admission, synthetic_held_keeper):
    from tools import wd_mind_e5_lifecycle as lifecycle
    class Call:
        def __init__(self, function):
            self.function = function

        def __call__(self, *args):
            return self.function(*args)

    api = SimpleNamespace(CreateJobObjectW=Call(lambda *args: 123),
                          SetInformationJobObject=Call(lambda *args: 0),
                          AssignProcessToJobObject=Call(lambda *args: pytest.fail("attach forbidden")),
                          TerminateJobObject=Call(lambda *args: pytest.fail("terminate forbidden")),
                          CloseHandle=Call(lambda *args: pytest.fail('uncertain Job init must not close')))
    native = SimpleNamespace(**{name: getattr(child.ctypes, name) for name in ("c_void_p", "c_int", "byref", "sizeof")},
                             WinDLL=lambda *a, **k: api)
    monkeypatch.setattr(child, "ctypes", native)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("spawn forbidden"))
    result = child.start_e5_worker_graceful(enabled=True, **synthetic_worker_admission, **staged_graceful)
    owner = result["owner"]
    assert result["status"] == "HOLD" and owner.status()["owned"]
    assert owner._job is not None and owner._job.handle == 123
    assert owner._job_state == 'ATTEMPTED' and owner._process is None


@pytest.mark.parametrize('signal_name', ['keyboard', 'cancelled'])
def test_graceful_launch_preserves_owner_after_spawn_interruption(staged_graceful, monkeypatch, signal_name,
                                                                 synthetic_windows_subprocess, synthetic_worker_admission):
    import asyncio
    from test_wd_mind_e5_lifecycle import Job as OwnedJob, Process as OwnedProcess, finish
    from tools import wd_mind_e5_lifecycle as lifecycle
    signal = KeyboardInterrupt if signal_name == 'keyboard' else asyncio.CancelledError
    process, captured, calls = OwnedProcess(on_eof=False), [], []
    class Job(OwnedJob):
        def __init__(self, *, kill_on_close, _owner=None):
            assert kill_on_close is False
            super().__init__()
        def attach(self, value):
            assert value is process
            assert captured and captured[0]._process is process
            raise signal()
    def spawn(*args, **kwargs):
        assert len(captured) == 1 and captured[0]._process is None
        calls.append('spawn')
        return process
    monkeypatch.setattr(child, '_Job', Job)
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    try:
        with pytest.raises(signal):
            child.start_e5_worker_graceful(enabled=True, owner_sink=captured.append,
                                            **synthetic_worker_admission, **staged_graceful)
        assert calls == ['spawn'] and captured[0] is lifecycle._active
        assert captured[0]._process is process and captured[0].status()['owned'] is True
        assert process.poll() is None and not captured[0]._job.closed
    finally:
        if captured:
            finish(captured[0], process)


def test_single_owner_caller_interrupt_keeps_real_launch_owner_during_spawn(staged_graceful, monkeypatch, tmp_path,
                                                                            synthetic_windows_subprocess, synthetic_local_admission):
    from test_wd_mind_e5_lifecycle import Job as OwnedJob, Process as OwnedProcess, finish
    from test_wd_mind_e5_client import response
    from tools import wd_mind_e5_client, wd_mind_full_runtime as runtime
    process = OwnedProcess(response(0, 0), on_eof=False)
    entered, release = threading.Event(), threading.Event()
    class Job(OwnedJob):
        def __init__(self, *, kill_on_close, _owner=None):
            assert kill_on_close is False
            super().__init__()
        def attach(self, value): assert value is process
    def spawn(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return process
    monkeypatch.setattr(child, '_Job', Job)
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    fleet = json.dumps({'lanes':[{'agent':'codex-lead-1'}],
                        'tools_supervisor':{'agent':'codex-tools-1'}}, sort_keys=True).encode()
    snapshot = {'projection_sha256':'b'*64, 'messages':{'end_cursor':SimpleNamespace(offset=1)}}
    binding = runtime._PrivateBinding(snapshot, tmp_path, 'a'*64, (fleet,), {})
    admission_owner = object()
    synthetic_local_admission.bind_owner(admission_owner)
    factory = runtime._ManifestWorkerFactory(str(tmp_path), 'a'*64, {}, tmp_path/'lock', 5, 0,
                                             wd_mind_e5_client.BULK_CONTRACT_SHA256,
                                             synthetic_local_admission, admission_owner)
    synthetic_local_admission.bind_factory(admission_owner, factory)
    synthetic_local_admission.begin_worker(admission_owner, factory)
    monkeypatch.setattr(runtime._PrivateBinding, 'snapshot_for_query', lambda self: snapshot)
    monkeypatch.setattr(runtime, '_flat_snapshot', lambda candidate, frozen: frozen)
    def launch(self, *, owner_sink):
        return child.start_e5_worker_graceful(enabled=True, owner_sink=owner_sink,
            _admission=synthetic_local_admission, _admission_owner=admission_owner,
            _admission_factory=factory, **staged_graceful)
    def interrupt(self):
        assert entered.wait(2)
        raise KeyboardInterrupt()
    monkeypatch.setattr(runtime._ManifestWorkerFactory, '__call__', launch)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_wait_for_launch', interrupt)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    try:
        with pytest.raises(KeyboardInterrupt): owner.start()
        assert entered.is_set()
        held = owner._owner
        assert held is not None and held.status()['owned'] is True
        assert owner.status() == {'state':'HOLD', 'owned':True, 'authority':'none'}
        assert owner.close() == {'state':'HOLD', 'owned':True, 'authority':'none'}
        release.set()
        owner._launch_thread.join(timeout=5)
        assert not owner._launch_thread.is_alive()
        assert held._process is process and process.poll() is None
        assert owner.status() == {'state':'HOLD', 'owned':True, 'authority':'none'}
    finally:
        release.set()
        if owner._launch_thread is not None: owner._launch_thread.join(timeout=5)
        if owner._owner is not None: finish(owner._owner, process)
        synthetic_local_admission.finish_worker_start(admission_owner, factory)


@pytest.mark.skipif(os.name != 'nt', reason='Windows handle inheritance test')
def test_windows_two_synthetic_children_receive_separate_bootstrap_channels():
    """No model, secrets in argv/env, files, shell, or non-test processes."""
    import hmac, secrets, sys
    channel_key = secrets.token_bytes(32)
    owner_read, owner_write = os.pipe()
    consumer_read, consumer_write = os.pipe()
    data_read, data_write = os.pipe()
    fds = {owner_read, owner_write, consumer_read, consumer_write, data_read, data_write}
    owner = consumer = None
    owner_code = r'''
import hashlib,hmac,sys
key = sys.stdin.buffer.read(32)
assert len(key) == 32
sys.stdout.buffer.write(hmac.new(key,b'synthetic-data',hashlib.sha256).digest())
sys.stdout.buffer.flush()
'''
    consumer_code = r'''
import ctypes,hashlib,hmac,sys
kernel=ctypes.WinDLL('kernel32',use_last_error=True)
kernel.GetStdHandle.argtypes=[ctypes.c_ulong]
kernel.GetStdHandle.restype=ctypes.c_void_p
kernel.ReadFile.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ulong,ctypes.POINTER(ctypes.c_ulong),ctypes.c_void_p]
kernel.ReadFile.restype=ctypes.c_int
handle=kernel.GetStdHandle((-12) & 0xffffffff)
buffer=ctypes.create_string_buffer(32)
received=ctypes.c_ulong()
assert kernel.ReadFile(handle,buffer,32,ctypes.byref(received),None) and received.value == 32
key=buffer.raw
actual=sys.stdin.buffer.read(32)
assert len(actual)==32 and hmac.compare_digest(actual,hmac.new(key,b'synthetic-data',hashlib.sha256).digest())
sys.stdout.buffer.write(b'synthetic-ok\n')
sys.stdout.buffer.flush()
'''
    try:
        owner = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c', owner_code],
            stdin=owner_read, stdout=data_write, stderr=subprocess.DEVNULL,
            close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW)
        consumer = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c', consumer_code],
            stdin=data_read, stdout=subprocess.PIPE, stderr=consumer_read,
            close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW)
        for descriptor in (owner_read, data_write, data_read, consumer_read):
            os.close(descriptor)
            fds.remove(descriptor)
        assert os.write(owner_write, channel_key) == 32
        assert os.write(consumer_write, channel_key) == 32
        os.close(owner_write); fds.remove(owner_write)
        os.close(consumer_write); fds.remove(consumer_write)
        out, _ = consumer.communicate(timeout=10)
        assert consumer.returncode == 0 and out == b'synthetic-ok\n'
        assert owner.wait(timeout=10) == 0
    finally:
        for descriptor in fds: os.close(descriptor)
        for process in (owner, consumer):
            if process is not None and process.poll() is None:
                process.wait(timeout=5)


def test_parent_child_spawn_default_refuses_before_command_or_process(monkeypatch):
    monkeypatch.setattr(child, '_verified_parent_child_command', lambda **_: pytest.fail('command resolution forbidden'))
    with pytest.raises(child.LaunchRefused):
        child.create_parent_child_launch(enabled=True, role='owner', bundle_root='C:\\synthetic',
            expected_manifest_sha256='a'*64, stdin_fd=0, stdout_fd=1, stderr_fd=2,
            deadline_monotonic=time.monotonic()+5)


def test_parent_child_spawn_interrupted_constructor_retains_unknown_owner(monkeypatch, synthetic_windows_subprocess):
    import threading
    created = []
    class InterruptedPopen:
        def __new__(cls, *args, **kwargs): return object.__new__(cls)
        def __init__(self, *args, **kwargs):
            created.append(self)
            raise KeyboardInterrupt()
    monkeypatch.setattr(child, '_require_verified_parent_spawn', lambda: None)
    monkeypatch.setattr(child, '_verified_parent_child_command', lambda **_: ('synthetic-interpreter',))
    monkeypatch.setattr(child, '_validate_parent_pipe_fds', lambda *_: None)
    monkeypatch.setattr(child.subprocess, 'Popen', InterruptedPopen)
    launch = child.create_parent_child_launch(enabled=True, role='owner', bundle_root='C:\\synthetic',
        expected_manifest_sha256='a'*64, stdin_fd=0, stdout_fd=1, stderr_fd=2,
        deadline_monotonic=time.monotonic()+5)
    launch.start()
    assert launch._done.wait(5)
    launch._thread.join(timeout=5)
    assert len(created) == 1 and launch._process is created[0]
    assert launch.status() == {'state':'HOLD', 'owned':None, 'worker_active':False}
    with pytest.raises(child.LaunchRefused): launch.close()


def test_parent_child_interrupted_thread_start_remains_hold_after_late_spawn(monkeypatch, synthetic_windows_subprocess):
    actual_thread = threading.Thread
    class InterruptedStartThread(actual_thread):
        def start(self):
            super().start()
            raise KeyboardInterrupt()
    class SyntheticPopen:
        def __new__(cls, *args, **kwargs): return object.__new__(cls)
        def __init__(self, *args, **kwargs): pass
        def poll(self): return None
    monkeypatch.setattr(child, '_require_verified_parent_spawn', lambda: None)
    monkeypatch.setattr(child, '_verified_parent_child_command', lambda **_: ('synthetic-interpreter',))
    monkeypatch.setattr(child, '_validate_parent_pipe_fds', lambda *_: None)
    monkeypatch.setattr(child.subprocess, 'Popen', SyntheticPopen)
    monkeypatch.setattr(child.threading, 'Thread', InterruptedStartThread)
    launch = child.create_parent_child_launch(enabled=True, role='owner', bundle_root='C:\\synthetic',
        expected_manifest_sha256='a'*64, stdin_fd=0, stdout_fd=1, stderr_fd=2,
        deadline_monotonic=time.monotonic()+5)
    with pytest.raises(KeyboardInterrupt): launch.start()
    launch._thread.join(timeout=5)
    assert launch.status() == {'state':'HOLD', 'owned':True, 'worker_active':False}
    with pytest.raises(child.LaunchRefused): launch.close()


def test_parent_child_late_constructor_cannot_self_adopt_after_start_cancellation(monkeypatch, synthetic_windows_subprocess):
    import inspect, sys
    entered, release = threading.Event(), threading.Event()
    class DelayedPopen:
        def __new__(cls, *args, **kwargs): return object.__new__(cls)
        def __init__(self, *args, **kwargs): entered.set(); release.wait(5)
        def poll(self): return None
    monkeypatch.setattr(child, '_require_verified_parent_spawn', lambda: None)
    monkeypatch.setattr(child, '_verified_parent_child_command', lambda **_: ('synthetic-interpreter',))
    monkeypatch.setattr(child, '_validate_parent_pipe_fds', lambda *_: None)
    monkeypatch.setattr(child.subprocess, 'Popen', DelayedPopen)
    launch = child.create_parent_child_launch(enabled=True, role='owner', bundle_root='C:\\synthetic',
        expected_manifest_sha256='a'*64, stdin_fd=0, stdout_fd=1, stderr_fd=2,
        deadline_monotonic=time.monotonic()+5)
    lines, first = inspect.getsourcelines(child._ParentChildLaunch.start)
    target = first + next(i for i, line in enumerate(lines) if line.strip() == 'if cancelled_type is not None:')
    def trace(frame, event, arg):
        if frame.f_code is child._ParentChildLaunch.start.__code__ and event == 'line' and frame.f_lineno == target:
            raise KeyboardInterrupt()
        return trace
    sys.settrace(trace)
    try:
        with pytest.raises(KeyboardInterrupt): launch.start()
    finally:
        sys.settrace(None)
        release.set()
    launch._thread.join(timeout=5)
    assert entered.is_set()
    assert launch.status() == {'state':'HOLD', 'owned':True, 'worker_active':False}


def test_parent_child_requires_one_use_adoption_before_spawned_status(monkeypatch, synthetic_windows_subprocess):
    class SyntheticPopen:
        def __new__(cls, *args, **kwargs): return object.__new__(cls)
        def __init__(self, *args, **kwargs): self.exit_code = None
        def poll(self): return self.exit_code
    monkeypatch.setattr(child, '_require_verified_parent_spawn', lambda: None)
    monkeypatch.setattr(child, '_verified_parent_child_command', lambda **_: ('synthetic-interpreter',))
    monkeypatch.setattr(child, '_validate_parent_pipe_fds', lambda *_: None)
    monkeypatch.setattr(child.subprocess, 'Popen', SyntheticPopen)
    launch = child.create_parent_child_launch(enabled=True, role='owner', bundle_root='C:\\synthetic',
        expected_manifest_sha256='a'*64, stdin_fd=0, stdout_fd=1, stderr_fd=2,
        deadline_monotonic=time.monotonic()+5)
    token = launch.start()
    launch._thread.join(timeout=5)
    assert launch.status() == {'state':'HOLD', 'owned':True, 'worker_active':False}
    with pytest.raises(child.LaunchRefused): launch.confirm_started(object())
    launch.confirm_started(token)
    assert launch.status() == {'state':'SPAWNED_UNVERIFIED', 'owned':True, 'worker_active':False}
    with pytest.raises(child.LaunchRefused): launch.confirm_started(token)
    launch._process.exit_code = 0
    launch.close()
    assert launch.status() == {'state':'EXITED', 'owned':False, 'worker_active':False}


@pytest.mark.parametrize('flag', ['--mind-owner-child', '--parent-control'])
def test_owner_child_fixed_cli_refuses_without_trusted_parent(flag):
    import sys
    script = Path(child.__file__).resolve()
    env = os.environ.copy()
    env['WD_REBOOT_EXPECTED_MANIFEST_HASH'] = 'a' * 64
    proc = subprocess.run([sys.executable, '-B', str(script), flag],
                          input=b'synthetic-private-owner-control', capture_output=True, timeout=10,
                          env=env,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    assert proc.returncode == 2 and proc.stdout == b'' and proc.stderr == b''


def test_parent_candidate_relay_frames_bounded_stream_without_granting_authority(monkeypatch):
    class PartialInput:
        def __init__(self, data): self.data, self.reads = data, 0
        def readinto(self, view):
            self.reads += 1
            count = min(3, len(view), len(self.data))
            view[:count], self.data = self.data[:count], self.data[count:]
            return count
    class PartialOutput:
        def __init__(self): self.data, self.closed = bytearray(), False
        def write(self, data):
            count = min(2, len(data))
            self.data.extend(data[:count])
            return count
        def close(self): self.closed = True
    raw = b'{"synthetic":"candidate"}'
    source, sink = PartialInput(raw), PartialOutput()
    inp, out = child._PrivateCandidateInput(source), child._PrivateCandidateOutput(sink)
    with pytest.raises(child.LaunchRefused):
        child.relay_parent_candidates(enabled=True, source=inp, destination=out,
                                      deadline_monotonic=time.monotonic()+5)
    assert source.reads == 0 and not sink.data and not sink.closed
    monkeypatch.setattr(child, '_require_verified_parent_spawn', lambda: None)
    receipt = child.relay_parent_candidates(enabled=True, source=inp, destination=out,
                                             deadline_monotonic=time.monotonic()+5)
    assert receipt == {'status':'RELAYED', 'authority':'none'}
    assert bytes(sink.data) == b'MCR1' + struct.pack('>I', len(raw)) + raw and sink.closed


def test_parent_candidate_relay_rejects_oversized_input_before_output(monkeypatch):
    class Sink:
        def __init__(self): self.data, self.closed = bytearray(), False
        def write(self, data): self.data.extend(data); return len(data)
        def close(self): self.closed = True
    monkeypatch.setattr(child, '_require_verified_parent_spawn', lambda: None)
    sink = Sink()
    with pytest.raises(child.LaunchRefused):
        child.relay_parent_candidates(enabled=True, source=child._PrivateCandidateInput(io.BytesIO(b'x'*2049)),
            destination=child._PrivateCandidateOutput(sink), deadline_monotonic=time.monotonic()+5)
    assert not sink.data and sink.closed


def test_parent_control_derives_distinct_child_controls_from_bound_input(monkeypatch):
    from tools import wd_mind_full_runtime as runtime
    policy = b'{"identifiers":[],"schema":"wd.mind-identifier-policy.v2"}'
    manifest, nonce, key = b'a'*32, b'n'*32, b'k'*32
    policy_sha, approval_sha, lane = hashlib.sha256(policy).digest(), b'b'*32, b'codex-lead-1'
    mct = struct.pack('>4sI', b'MCT1', len(policy)) + manifest + nonce + key + policy_sha + policy
    raw = b'MCP1' + manifest + policy_sha + approval_sha + bytes([len(lane)]) + lane + mct
    monkeypatch.setattr(child, '_require_verified_parent_spawn', lambda: None)
    buffer = runtime._ParentControlBuffer(runtime._PrivateParentStream(io.BytesIO(raw)), prefix_budget=133)
    buffer.read_from(enabled=True)
    controls = child._derive_parent_child_controls(enabled=True, control_buffer=buffer,
                                                    expected_manifest_sha256=manifest.hex())
    assert 'synthetic' not in repr(controls)
    with pytest.raises(child.LaunchRefused):
        child._derive_parent_child_controls(enabled=True, control_buffer=buffer,
                                            expected_manifest_sha256=manifest.hex())
    assert controls.metadata() == {'manifest_sha256':manifest.hex(), 'identifier_policy_sha256':policy_sha.hex(),
        'approval_reference_sha256':approval_sha.hex(), 'lane':lane.decode(), 'nonce':nonce.hex()}
    owner_control, consumer_control = controls.take()
    assert owner_control == b'MCO1' + raw[4:]
    assert consumer_control == (b'MCC1' + manifest + policy_sha + approval_sha + nonce + key
                                + bytes([len(lane)]) + lane)
    with pytest.raises(child.LaunchRefused): controls.take()


def test_parent_pair_timeout_sets_abort_and_retains_running_worker(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    controls = child._PrivatePairControls(b'owner', b'consumer', {'manifest_sha256':'a'*64})
    monkeypatch.setattr(child, '_require_verified_parent_spawn', lambda: None)
    def delayed(self):
        entered.set()
        release.wait(5)
        assert self._abort.is_set()
        return b'late-result'
    monkeypatch.setattr(child._RetainedParentPair, '_run_pipeline', delayed)
    pair = child.create_retained_parent_pair(enabled=True, bundle_root='C:\\synthetic',
        expected_manifest_sha256='a'*64, controls=controls, deadline_monotonic=time.monotonic()+0.2)
    try:
        token = pair.start()
        assert entered.wait(2)
        with pytest.raises(child.LaunchRefused): pair.wait(token)
        assert pair._abort.is_set() and pair.status()['worker_active'] is True
    finally:
        release.set()
        pair._thread.join(timeout=5)


def test_parent_pair_interrupted_close_never_reuses_uncertain_fd(monkeypatch):
    controls = child._PrivatePairControls(b'owner', b'consumer', {'manifest_sha256':'a'*64})
    monkeypatch.setattr(child, '_require_verified_parent_spawn', lambda: None)
    pair = child.create_retained_parent_pair(enabled=True, bundle_root='C:\\synthetic',
        expected_manifest_sha256='a'*64, controls=controls, deadline_monotonic=time.monotonic()+5)
    read_fd, write_fd = os.pipe()
    pair._fds.update((read_fd, write_fd))
    real_close = os.close
    def interrupt_after_close(fd):
        real_close(fd)
        raise KeyboardInterrupt()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(child.os, 'close', interrupt_after_close)
            with pytest.raises(KeyboardInterrupt): pair._close_fd(read_fd)
        assert read_fd not in pair._fds
        assert pair._fd_inflight == read_fd
    finally:
        real_close(write_fd)


def test_parent_pair_failed_stream_close_stays_held_and_never_retries(monkeypatch):
    controls = child._PrivatePairControls(b'owner', b'consumer', {'manifest_sha256':'a'*64})
    monkeypatch.setattr(child, '_require_verified_parent_spawn', lambda: None)
    pair = child.create_retained_parent_pair(enabled=True, bundle_root='C:\\synthetic',
        expected_manifest_sha256='a'*64, controls=controls, deadline_monotonic=time.monotonic()+5)
    attempted = threading.Event()
    class FailingClose:
        calls = 0
        def close(self):
            self.calls += 1
            attempted.set()
            raise OSError('synthetic close uncertainty')
    raw_stream = FailingClose()
    pair._streams.append(child._TrackedParentStream(pair, 123, raw_stream))
    monkeypatch.setattr(child._RetainedParentPair, '_run_pipeline', lambda self: b'synthetic-result')
    try:
        pair.start()
        assert attempted.wait(2)
        time.sleep(0.05)
        assert pair._thread.is_alive() and pair.status()['fd_ownership'] == 'UNKNOWN'
        assert pair._result is None and raw_stream.calls == 1
    finally:
        # Synthetic uncertainty is explicitly resolved only for fixture cleanup.
        pair._fd_inflight = None
        pair._stream_cleanup_pending = False
        pair._thread.join(timeout=5)


@pytest.mark.skipif(os.name != 'nt', reason='Windows handle inheritance test')
def test_windows_handle_list_excludes_unrelated_inheritable_handle():
    """Probe in an isolated synthetic parent, without mutating fleet handles."""
    import sys
    probe = r'''
import ctypes,msvcrt,os,struct,subprocess,sys
read_fd,write_fd=os.pipe()
handle=msvcrt.get_osfhandle(read_fd)
os.set_handle_inheritable(handle,True)
child=r"""
import ctypes,struct,sys
value=struct.unpack('<Q',sys.stdin.buffer.read(8))[0]
kernel=ctypes.WinDLL('kernel32',use_last_error=True)
kernel.GetFileType.argtypes=[ctypes.c_void_p]
kernel.GetFileType.restype=ctypes.c_ulong
kind=kernel.GetFileType(ctypes.c_void_p(value))
error=ctypes.get_last_error()
assert kind==0 and error==6
sys.stdout.write('excluded\\n')
"""
try:
    worker=subprocess.Popen([sys.executable,'-I','-S','-B','-c',child],stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,stderr=subprocess.PIPE,close_fds=True,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    result,error=worker.communicate(struct.pack('<Q',handle),timeout=10)
    assert worker.returncode==0 and result==b'excluded\\n'
    print('isolated-handle-exclusion-ok')
finally:
    os.close(read_fd)
    os.close(write_fd)
'''
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', probe],
        capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, 'isolated handle exclusion probe failed'
    assert result.stdout.strip() == b'isolated-handle-exclusion-ok'


def test_export_frame_codec_is_default_off_bounded_and_keyed():
    import hmac
    key, nonce, payload = b'k' * 32, b'n' * 32, b'synthetic-payload'
    with pytest.raises(child.LaunchRefused):
        child._encode_export_frame(channel_key=key, nonce=nonce, kind=b'M', sequence=0, payload=payload)
    frame = child._encode_export_frame(enabled=True, channel_key=key, nonce=nonce, kind=b'M', sequence=0, payload=payload)
    header = struct.pack('>4sB3xII', b'MEX1', ord('M'), 0, len(payload))
    tag = hmac.new(key, b'wd.mind-export-frame.v1\0' + nonce + header + payload, hashlib.sha256).digest()
    assert frame == header + payload + tag and len(frame) <= 65536
    for change in ({'sequence': True}, {'kind':b'D','sequence':0}, {'payload':b'x'*65489},
                   {'channel_key':b'x'}, {'nonce':b'x'}, {'kind':b'X'}):
        values = dict(enabled=True, channel_key=key, nonce=nonce, kind=b'M', sequence=0, payload=payload)
        values.update(change)
        with pytest.raises(child.LaunchRefused): child._encode_export_frame(**values)


def test_export_frame_refusal_never_exposes_keys_in_traceback():
    import traceback
    channel_key, payload = b'k'*32, b'synthetic-private-payload'
    with pytest.raises(child.LaunchRefused) as raised:
        child._encode_export_frame(enabled=True, channel_key=channel_key, nonce=b'n'*32,
                                   kind=b'D', sequence=0, payload=payload)
    trace = traceback.TracebackException.from_exception(raised.value, capture_locals=True)
    frames = [frame for frame in trace.stack if frame.name == '_encode_export_frame']
    assert frames
    assert all(repr(secret) not in value for secret in (channel_key, payload)
               for frame in frames for value in (frame.locals or {}).values())


def test_export_wire_budget_counts_metadata_all_blocks_headers_and_final():
    lengths = (1, 65488, 65489, 0, 32, 100, 200, 300, 400, 500, 600, 700)
    data_frames = sum((length + 65487) // 65488 for length in lengths)
    expected = 123 + sum(lengths) + 48 * (1 + data_frames + 1) + 44
    assert child._export_wire_size(enabled=True, metadata_length=123, block_lengths=lengths,
                                   max_wire_bytes=expected+1) == expected
    with pytest.raises(child.LaunchRefused):
        child._export_wire_size(enabled=True, metadata_length=123, block_lengths=lengths,
                                max_wire_bytes=expected)
    for bad in ((), lengths[:-1], (True,) + lengths[1:], (-1,) + lengths[1:]):
        with pytest.raises(child.LaunchRefused):
            child._export_wire_size(enabled=True, metadata_length=123, block_lengths=bad,
                                    max_wire_bytes=2**63-1)


@pytest.mark.skipif(os.name != 'nt', reason='Windows pipe cancellation')
def test_cancelable_pipe_read_interrupts_blocked_native_read_without_termination():
    import msvcrt
    read_fd, write_fd = os.pipe()
    reader = None
    try:
        reader = child.create_cancelable_pipe_reader(enabled=True, handle=msvcrt.get_osfhandle(read_fd), deadline_monotonic=time.monotonic()+0.2)
        with pytest.raises(child.LaunchRefused): reader.read(1)
        status = reader.status()
        assert status['state'] == 'HOLD' and status['worker_active'] is False
        assert status['cancellation'] == 'CONFIRMED'
    finally:
        if reader is not None: reader.close()
        os.close(write_fd)
        os.close(read_fd)


@pytest.mark.skipif(os.name != 'nt', reason='Windows pipe cancellation')
def test_failed_pipe_cancellation_retains_live_thread_and_handle(monkeypatch):
    import msvcrt
    read_fd, write_fd = os.pipe()
    reader = None
    try:
        reader = child.create_cancelable_pipe_reader(enabled=True, handle=msvcrt.get_osfhandle(read_fd), deadline_monotonic=time.monotonic()+0.05)
        monkeypatch.setattr(child._CancelablePipeReader, '_cancel_worker', lambda self: False)
        with pytest.raises(child.LaunchRefused): reader.read(1)
        status = reader.status()
        assert status == {'state':'HOLD','worker_active':True,'cancellation':'UNCONFIRMED'}
        with pytest.raises(child.LaunchRefused): reader.close()
        assert reader.status()['worker_active'] is True
        assert os.write(write_fd,b'x') == 1
        reader._thread.join(timeout=2)
        assert not reader._thread.is_alive() and reader._result is None
        assert reader.status()['state'] == 'HOLD'
        reader.close()
        assert reader.status()['state'] == 'DETACHED'
    finally:
        os.close(write_fd)
        os.close(read_fd)


@pytest.mark.skipif(os.name != 'nt', reason='Windows pipe cancellation')
def test_cancelable_pipe_reader_reads_bytes_and_borrows_original_handle():
    import msvcrt
    read_fd, write_fd = os.pipe()
    reader = None
    try:
        reader = child.create_cancelable_pipe_reader(enabled=True, handle=msvcrt.get_osfhandle(read_fd), deadline_monotonic=time.monotonic()+2)
        assert os.write(write_fd, b'abc') == 3
        assert reader.read(3) == b'abc'
        os.close(write_fd)
        write_fd = None
        assert reader.read(1) == b''
        reader.close()
        assert reader.status()['state'] == 'DETACHED'
        assert msvcrt.get_osfhandle(read_fd) != -1
    finally:
        if write_fd is not None: os.close(write_fd)
        os.close(read_fd)


@pytest.mark.skipif(os.name != 'nt', reason='Windows pipe cancellation')
def test_cancelable_pipe_reader_uses_one_absolute_deadline_across_reads():
    import msvcrt
    read_fd, write_fd = os.pipe()
    reader = None
    try:
        deadline = time.monotonic() + 0.2
        reader = child.create_cancelable_pipe_reader(enabled=True, handle=msvcrt.get_osfhandle(read_fd),
                                                      deadline_monotonic=deadline)
        assert os.write(write_fd, b'a') == 1 and reader.read(1) == b'a'
        time.sleep(max(0, deadline - time.monotonic()) + 0.05)
        before = time.monotonic()
        with pytest.raises(child.LaunchRefused): reader.read(1)
        assert time.monotonic() - before < 0.1
        assert reader.status()['state'] == 'HOLD' and reader.status()['worker_active'] is False
    finally:
        if reader is not None: reader.close()
        os.close(write_fd)
        os.close(read_fd)


@pytest.mark.skipif(os.name != 'nt', reason='Windows pipe cancellation')
def test_interrupted_thread_start_never_detaches_unresolved_worker(monkeypatch):
    import msvcrt
    read_fd, write_fd = os.pipe()
    class DelayedStart:
        def __init__(self, *args, **kwargs): self.finished = False
        def start(self): raise KeyboardInterrupt()
        def is_alive(self): return False
        def join(self, timeout=None):
            if not self.finished: raise RuntimeError('start unresolved')
    reader = child.create_cancelable_pipe_reader(enabled=True, handle=msvcrt.get_osfhandle(read_fd),
                                                  deadline_monotonic=time.monotonic()+2)
    monkeypatch.setattr(child.threading, 'Thread', DelayedStart)
    try:
        with pytest.raises(KeyboardInterrupt): reader.read(1)
        assert reader.status()['state'] == 'HOLD' and reader.status()['worker_active'] is None
        with pytest.raises(child.LaunchRefused): reader.close()
        reader._thread.finished = True
        reader._done.set()
        reader.close()
        assert reader.status()['state'] == 'DETACHED'
    finally:
        os.close(write_fd)
        os.close(read_fd)


@pytest.mark.skipif(os.name != 'nt', reason='Windows pipe cancellation')
def test_pipe_cancel_uses_retained_thread_handle_not_recycled_native_id(monkeypatch):
    import msvcrt
    read_fd, write_fd = os.pipe()
    original = child._CancelablePipeReader._cancel_worker
    observed = []
    def cancel(reader):
        observed.append(reader._thread_handle.value)
        reader._thread._native_id = 0x7fffffff  # Synthetic stale/reused identity value.
        return original(reader)
    monkeypatch.setattr(child._CancelablePipeReader, '_cancel_worker', cancel)
    reader = child.create_cancelable_pipe_reader(enabled=True, handle=msvcrt.get_osfhandle(read_fd),
                                                  deadline_monotonic=time.monotonic()+0.2)
    try:
        with pytest.raises(child.LaunchRefused): reader.read(1)
        assert observed and observed[0] is not None
        assert reader.status() == {'state':'HOLD','worker_active':False,'cancellation':'CONFIRMED'}
        reader.close()
    finally:
        os.close(write_fd)
        os.close(read_fd)


def test_source_gate_timeout_retains_late_worker_and_discards_result():
    started, release = threading.Event(), threading.Event()
    def blocked_gate():
        started.set()
        assert release.wait(5)
        return object()
    holder = child.create_retained_source_gate_call(enabled=True, callback=blocked_gate,
                                                     deadline_monotonic=time.monotonic()+0.1)
    try:
        holder.start()
        assert started.wait(1)
        with pytest.raises(child.LaunchRefused): holder.wait()
        assert holder.status() == {'state':'HOLD', 'worker_active':True}
        with pytest.raises(child.LaunchRefused): holder.close()
        release.set()
        holder._thread.join(timeout=2)
        assert not holder._thread.is_alive()
        with pytest.raises(child.LaunchRefused): holder.wait()
        assert holder._result is None and holder.status()['state'] == 'HOLD'
        holder.close()
        assert holder.status()['state'] == 'DETACHED'
    finally:
        release.set()
        if holder._thread is not None: holder._thread.join(timeout=2)


def test_source_gate_success_is_one_shot_and_default_off():
    value = object()
    with pytest.raises(child.LaunchRefused):
        child.create_retained_source_gate_call(callback=lambda: value, deadline_monotonic=time.monotonic()+2)
    holder = child.create_retained_source_gate_call(enabled=True, callback=lambda: value,
                                                     deadline_monotonic=time.monotonic()+2)
    holder.start()
    assert holder.wait() is value and holder.status() == {'state':'SUCCEEDED','worker_active':False}
    with pytest.raises(child.LaunchRefused): holder.wait()
    holder.close()
    assert holder.status()['state'] == 'DETACHED'


def test_source_gate_interrupted_start_never_releases_uncertain_worker(monkeypatch):
    class DelayedStart:
        def __init__(self, *args, **kwargs): self.finished = False
        def start(self): raise KeyboardInterrupt()
        def is_alive(self): return False
        def join(self, timeout=None):
            if not self.finished: raise RuntimeError('unresolved')
    holder = child.create_retained_source_gate_call(enabled=True, callback=lambda: object(),
                                                     deadline_monotonic=time.monotonic()+2)
    monkeypatch.setattr(child.threading, 'Thread', DelayedStart)
    with pytest.raises(KeyboardInterrupt): holder.start()
    assert holder.status() == {'state':'HOLD','worker_active':None}
    with pytest.raises(child.LaunchRefused): holder.close()
    holder._thread.finished = True
    holder._done.set()
    holder.close()
    assert holder.status()['state'] == 'DETACHED'


def test_source_gate_factory_refusal_scrubs_callback_from_traceback():
    import traceback
    fake_secret = 'synthetic-private-callback-value'
    class PrivateCallback:
        def __call__(self): return None
        def __repr__(self): return fake_secret
    with pytest.raises(child.LaunchRefused) as raised:
        child.create_retained_source_gate_call(enabled=True, callback=PrivateCallback(), deadline_monotonic=False)
    trace = traceback.TracebackException.from_exception(raised.value, capture_locals=True)
    frames = [frame for frame in trace.stack if frame.name == 'create_retained_source_gate_call']
    assert frames and all(fake_secret not in value for frame in frames for value in (frame.locals or {}).values())


@pytest.mark.parametrize('signal_name', ['keyboard','cancelled','generator'])
def test_source_gate_worker_propagates_only_sanitized_builtin_cancellation(signal_name):
    import asyncio, traceback
    base = {'keyboard':KeyboardInterrupt, 'cancelled':asyncio.CancelledError, 'generator':GeneratorExit}[signal_name]
    fake_secret = b'synthetic-worker-only-secret'
    class UntrustedSignal(base):
        def __setattr__(self, name, value):
            if name in ('__traceback__','__context__','__cause__'):
                raise RuntimeError('synthetic private failure')
            return super().__setattr__(name, value)
    event = UntrustedSignal(fake_secret)
    def gate():
        private = fake_secret
        raise event
    holder = child.create_retained_source_gate_call(enabled=True, callback=gate,
                                                     deadline_monotonic=time.monotonic()+2)
    holder.start()
    with pytest.raises(base) as raised: holder.wait()
    assert type(raised.value) is base and raised.value.args == () and raised.value.__context__ is None
    trace = traceback.TracebackException.from_exception(raised.value, capture_locals=True)
    frames = [frame for frame in trace.stack if frame.name != 'test_source_gate_worker_propagates_only_sanitized_builtin_cancellation']
    assert all(repr(fake_secret) not in value for frame in frames for value in (frame.locals or {}).values())
    assert holder.status() == {'state':'HOLD','worker_active':False}
    holder.close()


def test_deferred_historical_launch_never_spawns_or_terminates(staged, staged_e5, monkeypatch):
    calls = []
    monkeypatch.setattr(child, '_require_safe_writer_scope', lambda *args: None)
    def spawn(*args, **kwargs):
        calls.append('spawn')
        raise OSError('synthetic')
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    for fn, options in ((child.launch, staged), (child.start_e5_worker, {k:v for k,v in staged_e5.items() if k != 'query'})):
        with pytest.raises(child.LaunchRefused): fn(**options)
    assert calls == []


def test_job_refuses_kill_on_close_before_native_calls(monkeypatch):
    monkeypatch.setattr(child.ctypes, 'WinDLL', lambda *args, **kwargs: pytest.fail('native call forbidden'), raising=False)
    with pytest.raises(child.LaunchRefused): child._Job(kill_on_close=True)
    assert not hasattr(child._Job, 'terminate')


def test_child_popen_receives_verified_runtime_cache_and_isolated_environment(
        staged_graceful, monkeypatch, synthetic_windows_subprocess, synthetic_worker_admission):
    """Exercise real synthetic pin preflight and capture Popen; no child is spawned."""
    from test_wd_mind_e5_lifecycle import Job as OwnedJob, Process as OwnedProcess, finish
    from test_wd_mind_e5_client import response
    process = OwnedProcess(response(0, 0), on_eof=False)
    calls = {}
    class Job(OwnedJob):
        def __init__(self, *, kill_on_close, _owner=None):
            assert kill_on_close is False
            super().__init__()
        def attach(self, value):
            assert value is process
    def popen(argv, **kwargs):
        env = kwargs["env"]
        cache_keys = ("HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE",
                      "HF_ASSETS_CACHE", "SENTENCE_TRANSFORMERS_HOME", "TORCH_HOME", "XDG_CACHE_HOME", "CUDA_CACHE_PATH")
        # Retain only booleans so assertion introspection cannot expose env values.
        calls["runtime_cache_matches"] = env.get("TORCHINDUCTOR_CACHE_DIR") == calls.get("verified_root")
        calls["temp_isolated"] = all(env.get(name) == os.devnull for name in ("TEMP", "TMP"))
        calls["offline_enabled"] = all(env.get(name) == "1" for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"))
        calls["caches_isolated"] = all(env.get(name) == os.devnull for name in cache_keys)
        return process
    original_verify = anchor.verify_runtime_process_preflight
    def recording_verify(*args, **kwargs):
        result = original_verify(*args, **kwargs)
        calls["verified_root"] = kwargs["runtime_root"]
        return result
    monkeypatch.setattr(anchor, "verify_runtime_process_preflight", recording_verify)
    monkeypatch.setattr(child, "_Job", Job)
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setenv("TORCHINDUCTOR_CACHE_DIR", "caller-controlled-synthetic")
    result = child.start_e5_worker_graceful(enabled=True, **synthetic_worker_admission, **staged_graceful)
    owner = result["owner"]
    try:
        assert result["status"] == "READY"
        assert calls["verified_root"] == staged_graceful["tuple_args"]["runtime-root"]
        assert calls["runtime_cache_matches"]
        assert os.environ["TORCHINDUCTOR_CACHE_DIR"] == "caller-controlled-synthetic"
        assert calls["temp_isolated"]
        assert calls["offline_enabled"]
        assert calls["caches_isolated"]
    finally:
        finish(owner, process)
