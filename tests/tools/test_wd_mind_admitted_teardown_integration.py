"""Real teardown layers, with synthetic startup, transport, model and process I/O."""

import hashlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import threading

import pytest

from test_wd_mind_runtime_process import candidate
from test_wd_mind_runtime_child import staged, staged_e5, staged_graceful, synthetic_windows_subprocess
from test_wd_mind_e5_lifecycle import Job as SyntheticJob, Process
from test_wd_mind_e5_client import response
from tools import wd_mind_e5_snapshot_adapter as adapter_module
from tools import wd_mind_full_runtime as runtime
from tools import wd_mind_runtime_child as child
from tools import wd_mind_runtime_process as process_module


@pytest.mark.skipif(os.name != 'nt', reason='reservation uses Windows byte locks')
@pytest.mark.parametrize('hold_service', [False, True])
def test_real_service_control_owner_and_worker_teardown(staged_graceful, monkeypatch, tmp_path,
                                                        synthetic_windows_subprocess, hold_service):
    """No native pipe/model/process: only lifecycle and teardown are real."""
    import multiprocessing.connection as transport

    root = tmp_path / 'synthetic-reservation'
    root.mkdir()
    marker = root / child._OWNER_RESERVATION_NAME
    marker.write_bytes(child._OWNER_RESERVATION_MAGIC + b'C')
    monkeypatch.setattr(child, '_AUDIT_ROOT', root)
    stop, ready, wake = threading.Event(), threading.Event(), threading.Event()
    wake_entered, wake_release = threading.Event(), threading.Event()
    statuses, entries, listeners, services, owners, admissions, marker_states = [], [], [], [], [], [], []
    policy = b'synthetic-policy'
    frame = io.BytesIO(b'MCL1' + struct.pack('>I', len(policy)) + policy)

    class Input:
        def read(self, size):
            data = frame.read(size)
            if data:
                return data
            stop.wait(5)
            return b'S'

    class Output:
        def write(self, data):
            status = json.loads(bytes(data))
            statuses.append(status['state'])
            if status['state'] in ('STARTING', 'READY', 'STOPPED'):
                assert len(admissions) == 1
                with admissions[0]._lock:
                    marker_states.append((status['state'], admissions[0]._guard._read()))
                assert marker_states[-1][1] == child._OWNER_RESERVATION_MAGIC + b'R'
            if status['state'] == 'STOPPED':
                adapter, owner, factory = owners[0]
                assert services[0].status()['state'] == 'STOPPED' and services[0]._serve_done.is_set()
                assert owner.status()['state'] == 'STOPPED' and owner.status()['owned'] is False
                assert factory._admission._worker.status()['owned'] is False
                assert not factory._admission._worker._keeper.is_alive()
                assert not any(thread.name == 'mind-local-control' and thread.is_alive()
                               for thread in threading.enumerate())
            if status['state'] == 'READY':
                ready.set()
            return len(data)
        def flush(self):
            pass

    class Connection:
        def close(self):
            pass

    class Listener:
        def __init__(self, **kwargs):
            self.closed = False
            listeners.append(self)
        def accept(self):
            wake.wait(5)
            return Connection()
        def close(self):
            self.closed = True

    def client(**kwargs):
        wake_entered.set()
        if hold_service:
            wake_release.wait(5)
        wake.set()
        return Connection()

    class Job(SyntheticJob):
        def __init__(self, *, kill_on_close, _owner=None):
            assert kill_on_close is False
            super().__init__()
        def attach(self, process):
            pass

    process = Process(response(0, 0), on_eof=True)
    monkeypatch.setattr(transport, 'Listener', Listener)
    monkeypatch.setattr(transport, 'Client', client)
    original_service_factory = process_module.create_local_candidate_service
    def create_service(**kwargs):
        service = original_service_factory(**kwargs)
        services.append(service)
        return service
    monkeypatch.setattr(process_module, 'create_local_candidate_service', create_service)
    original_query_factory = child.create_local_manifest_query
    def create_query(**kwargs):
        admissions.append(kwargs['_admission'])
        return original_query_factory(**kwargs)
    monkeypatch.setattr(child, 'create_local_manifest_query', create_query)
    monkeypatch.setattr(child, '_Job', Job)
    monkeypatch.setattr(subprocess, 'Popen', lambda *args, **kwargs: process)
    args = staged_graceful['tuple_args']
    location = {'runtime_root':args['runtime-root'], 'runtime_manifest':args['runtime-manifest'],
        'runtime_manifest_sha256':args['runtime-manifest-sha256'], 'runtime_generation':args['runtime-generation'],
        'runtime_revision':args['runtime-revision'], 'interpreter':str(staged_graceful['interpreter']),
        'interpreter_sha256':staged_graceful['interpreter_sha256'],
        'interpreter_root':str(staged_graceful['interpreter_root'])}
    monkeypatch.setattr(runtime, 'resolve_manifest_runtime_location', lambda **kwargs: location)
    monkeypatch.setattr(runtime, '_worker_factory_preflight', lambda *unused: child)
    monkeypatch.setattr(runtime, '_fixed_worker_audit_lock', lambda: Path(args['lock-file']))
    snapshot = {'projection_sha256':'b' * 64}
    fleet = json.dumps({'lanes':[{'agent':'codex-lead-1'}],
                        'tools_supervisor':{'agent':'codex-tools-1'}}).encode()
    binding = runtime._PrivateBinding(snapshot, staged_graceful['bundle_root'],
        staged_graceful['deployment_manifest_sha256'], (fleet,), {})
    monkeypatch.setattr(runtime._PrivateBinding, 'snapshot_for_query', lambda self: snapshot)
    monkeypatch.setattr(runtime, '_flat_snapshot', lambda *unused: snapshot)
    monkeypatch.setattr(adapter_module, 'create_single_flat_adapter', lambda **kwargs: object())
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', lambda self: {
        'schema':'wd.mind-flat-owner-build.v1', 'status':'BUILT_IN_MEMORY', 'authority':'none'})

    def synthetic_prepare(self):
        factory = runtime.create_manifest_worker_factory(enabled=True, bundle_root=self._bundle,
            expected_manifest_sha256=self._manifest, worker_timeout_seconds=5, lock_timeout_seconds=0,
            bulk_contract_sha256=child.BULK_CONTRACT_SHA256, _admission=self._admission, _admission_owner=self)
        owner = runtime.create_single_manifest_owner(enabled=True, binding=binding, lane='codex-lead-1',
            worker_factory=factory, shutdown_timeout=2)
        owners.append((self, owner, factory))
        with self._lock:
            self._owner = owner
        outcome = owner.start()
        with self._lock:
            self._state = 'READY' if outcome.get('state') == 'READY' else 'HOLD'

    monkeypatch.setattr(child._LocalManifestQuery, '_prepare', synthetic_prepare)

    def run_entry():
        try:
            entries.append(child._guarded_local_entry(child._local_candidate_owner_entry,
                control_stream=Input(), output_stream=Output(), bundle_root=str(staged_graceful['bundle_root']),
                expected_manifest_sha256=staged_graceful['deployment_manifest_sha256'],
                policy_sha256=hashlib.sha256(policy).hexdigest(), approval_reference_sha256='c' * 64))
        except BaseException as error:
            entries.append(type(error).__name__)

    entry = threading.Thread(target=run_entry, daemon=True)
    entry.start()
    try:
        assert ready.wait(5), (statuses, entries)
        with pytest.raises(child.LaunchRefused):
            child._reserve_local_owner(root)
        stop.set()
        if hold_service:
            assert wake_entered.wait(3)
            assert entry.is_alive() and owners[0][1].status()['owned'] is True
            assert services[0].status()['state'] == 'HOLD'
            admission = owners[0][2]._admission
            with admission._lock:
                assert admission._guard._read() == child._OWNER_RESERVATION_MAGIC + b'R'
            wake_release.set()
        entry.join(timeout=5)
        assert not entry.is_alive()
        assert entries == [0]
        assert listeners and listeners[0].closed
        assert type(services[0]) is process_module._LocalCandidateService
        assert services[0].status()['state'] == 'STOPPED' and services[0]._serve_done.is_set()
        assert not services[0]._thread.is_alive()
        adapter, owner, factory = owners[0]
        assert type(adapter) is child._LocalManifestQuery and type(owner) is runtime._SingleManifestOwner
        assert owner.status()['state'] == 'STOPPED' and owner.status()['owned'] is False
        assert factory._admission._worker.status()['owned'] is False
        assert not factory._admission._worker._keeper.is_alive()
        assert process.stdin.closed
        assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC + b'C'
        assert statuses[0] == 'STARTING' and statuses[-1] == 'STOPPED'
        assert [state for state, _ in marker_states] == ['STARTING', 'READY', 'STOPPED']
    finally:
        stop.set()
        wake_release.set()
        wake.set()
        process.finish()
        entry.join(timeout=5)
