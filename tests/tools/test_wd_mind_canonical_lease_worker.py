"""Nested worker integration with synthetic pins, job, process and I/O only."""

import hashlib
import io
import os
from pathlib import Path
import subprocess

import pytest

from test_wd_mind_runtime_process import candidate
from test_wd_mind_runtime_child import staged, staged_e5, staged_graceful, synthetic_windows_subprocess
from test_wd_mind_e5_lifecycle import Job as SyntheticJob, Process, finish
from test_wd_mind_e5_client import response
from tools import wd_mind_full_runtime as runtime
from tools import wd_mind_runtime_child as child


class _Job(SyntheticJob):
    def __init__(self, *, kill_on_close, _owner=None):
        assert kill_on_close is False
        super().__init__()

    def attach(self, process):
        pass


@pytest.mark.skipif(os.name != 'nt', reason='reservation uses Windows byte locks')
def test_actual_factory_and_worker_use_same_admission(staged_graceful, monkeypatch, tmp_path,
                                                      synthetic_windows_subprocess):
    marker_root = tmp_path / 'synthetic-reservation'
    marker_root.mkdir()
    marker = marker_root / child._OWNER_RESERVATION_NAME
    marker.write_bytes(child._OWNER_RESERVATION_MAGIC + b'C')
    guard = child._reserve_local_owner(marker_root)
    lease = child._LocalAdmission(guard, {'clean': False})
    policy = b'synthetic policy'
    owner = child.create_local_manifest_query(enabled=True, bundle_root=str(staged_graceful['bundle_root']),
        expected_manifest_sha256=staged_graceful['deployment_manifest_sha256'], identifier_policy_bytes=policy,
        expected_identifier_policy_sha256=hashlib.sha256(policy).hexdigest(),
        operator_approval_reference_sha256='b' * 64, lane='codex-lead-1', _admission=lease)
    args = staged_graceful['tuple_args']
    current = {'runtime_root':args['runtime-root'], 'runtime_manifest':args['runtime-manifest'],
               'runtime_manifest_sha256':args['runtime-manifest-sha256'],
               'runtime_generation':args['runtime-generation'], 'runtime_revision':args['runtime-revision'],
               'interpreter':staged_graceful['interpreter'], 'interpreter_sha256':staged_graceful['interpreter_sha256'],
               'interpreter_root':staged_graceful['interpreter_root']}
    monkeypatch.setattr(runtime, 'resolve_manifest_runtime_location', lambda **kwargs: current)
    monkeypatch.setattr(runtime, '_worker_factory_preflight', lambda *unused: child)
    monkeypatch.setattr(runtime, '_fixed_worker_audit_lock', lambda: Path(args['lock-file']))
    factory = runtime.create_manifest_worker_factory(enabled=True, bundle_root=str(staged_graceful['bundle_root']),
        expected_manifest_sha256=staged_graceful['deployment_manifest_sha256'], worker_timeout_seconds=5,
        lock_timeout_seconds=0, bulk_contract_sha256=child.BULK_CONTRACT_SHA256,
        _admission=lease, _admission_owner=owner)
    monkeypatch.setattr(child, '_Job', _Job)
    process = Process(response(0, 0), on_eof=False)
    spawn_states, captured = [], []

    def popen(*unused, **kwargs):
        try:
            spawn_states.append((lease._spawning, lease._worker._spawn_state, guard._read()))
        except BaseException:
            spawn_states.append(('observation_failed',))
        return process

    monkeypatch.setattr(subprocess, 'Popen', popen)
    result = None
    try:
        result = factory(owner_sink=captured.append)
        assert result['status'] == 'READY'
        assert spawn_states == [(True, 'ATTEMPTED', child._OWNER_RESERVATION_MAGIC + b'R')]
        assert result['owner'] is lease._worker
        assert not lease.quiescent()
        with pytest.raises(child.LaunchRefused):
            factory()
    finally:
        if captured and captured[0].resources.process is process:
            finish(captured[0], process)
            captured[0]._keeper.join(timeout=2)
        guard.abandon()
    assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC + b'R'


def test_factory_without_admission_refuses_before_location_io(monkeypatch):
    monkeypatch.setattr(runtime, 'resolve_manifest_runtime_location', lambda **kwargs: pytest.fail('unexpected I/O'))
    with pytest.raises(runtime.FullRuntimeRefused):
        runtime.create_manifest_worker_factory(enabled=True, bundle_root='ignored',
            expected_manifest_sha256='a' * 64, worker_timeout_seconds=5, lock_timeout_seconds=0,
            bulk_contract_sha256=child.BULK_CONTRACT_SHA256)


@pytest.mark.skipif(os.name != 'nt', reason='reservation uses Windows byte locks')
def test_owner_preparation_passes_its_own_lease_to_factory(tmp_path, monkeypatch):
    marker = tmp_path / child._OWNER_RESERVATION_NAME
    marker.write_bytes(child._OWNER_RESERVATION_MAGIC + b'C')
    guard = child._reserve_local_owner(tmp_path)
    lease = child._LocalAdmission(guard)
    policy = b'synthetic policy'
    owner = child.create_local_manifest_query(enabled=True, bundle_root=str(tmp_path),
        expected_manifest_sha256='a' * 64, identifier_policy_bytes=policy,
        expected_identifier_policy_sha256=hashlib.sha256(policy).hexdigest(),
        operator_approval_reference_sha256='b' * 64, lane='codex-lead-1', _admission=lease)
    observed = []
    monkeypatch.setattr(runtime, 'create_operator_pinned_policy_anchor', lambda **kwargs: object())
    monkeypatch.setattr(runtime, 'assemble_full_runtime', lambda **kwargs: object())

    def factory(**kwargs):
        observed.append((kwargs['_admission'], kwargs['_admission_owner']))
        raise ValueError('synthetic stop before worker')

    monkeypatch.setattr(runtime, 'create_manifest_worker_factory', factory)
    try:
        assert owner.start()['state'] == 'STARTING'
        owner._thread.join(timeout=2)
        assert not owner._thread.is_alive()
        assert observed == [(lease, owner)]
        assert owner.close()['state'] == 'STOPPED'
    finally:
        guard.abandon()


@pytest.mark.skipif(os.name != 'nt', reason='reservation uses Windows byte locks')
def test_quiescent_alone_cannot_release_reservation(tmp_path):
    marker = tmp_path / child._OWNER_RESERVATION_NAME
    marker.write_bytes(child._OWNER_RESERVATION_MAGIC + b'C')
    guard = child._reserve_local_owner(tmp_path)
    lease = child._LocalAdmission(guard)
    try:
        assert lease.quiescent()
        with pytest.raises(child.LaunchRefused):
            lease.release_clean()
    finally:
        guard.abandon()
    assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC + b'R'


def test_deferred_parent_and_coordinator_entrypoints_refuse_before_io(monkeypatch):
    class NoRead(io.BytesIO):
        def read(self, *args, **kwargs):
            pytest.fail('parent control read before authorization')

    stream = NoRead()
    assert child._parent_control_entry(control_stream=stream, output_stream=io.BytesIO(),
        bundle_root='synthetic', expected_manifest_sha256='a' * 64) == 2
    assert child._owner_child_entry(control_stream=stream, data_stream=io.BytesIO(), candidate_stream=io.BytesIO(),
        bundle_root='synthetic', expected_manifest_sha256='a' * 64) == 2
    with pytest.raises(runtime.FullRuntimeRefused):
        runtime._require_trusted_parent_export(owner=None, stream=None, bootstrap=None)
    with pytest.raises(runtime.FullRuntimeRefused):
        runtime.create_manifest_coordinator(enabled=True)
    with pytest.raises(runtime.FullRuntimeRefused):
        runtime.start_manifest_bound_owner(enabled=True)


def test_private_unleased_coordinator_holds_before_worker(monkeypatch):
    args = {'bundle_root':'synthetic', 'expected_manifest_sha256':'a' * 64,
            'worker_timeout_seconds':5, 'lock_timeout_seconds':0,
            'bulk_contract_sha256':child.BULK_CONTRACT_SHA256}
    monkeypatch.setattr(runtime, 'assemble_full_runtime', lambda **kwargs: object())
    monkeypatch.setattr(runtime, 'resolve_manifest_runtime_location', lambda **kwargs: pytest.fail('worker preflight'))
    coordinator = runtime._ManifestCoordinator(args)
    with pytest.raises(runtime.FullRuntimeRefused):
        coordinator.start()
    assert coordinator.status()['state'] == 'HOLD'
