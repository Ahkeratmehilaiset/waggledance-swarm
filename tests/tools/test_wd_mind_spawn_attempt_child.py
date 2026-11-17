"""Synthetic tests of the actual cooperative worker-start route."""

import os
import subprocess
import threading

import pytest

from test_wd_mind_runtime_process import candidate
from test_wd_mind_runtime_child import staged, staged_e5, staged_graceful, synthetic_windows_subprocess
from test_wd_mind_e5_lifecycle import Job as SyntheticJob, Process, finish
from test_wd_mind_e5_client import response
from tools import wd_mind_e5_lifecycle as lifecycle
from tools import wd_mind_runtime_child as child


class _Job(SyntheticJob):
    def __init__(self, *, kill_on_close, _owner=None):
        assert kill_on_close is False
        super().__init__()

    def attach(self, process):
        pass


@pytest.fixture
def admission_args(tmp_path):
    if os.name != 'nt':
        pytest.skip('reservation uses Windows byte locks')
    root = tmp_path / 'synthetic-admission'
    root.mkdir()
    (root / child._OWNER_RESERVATION_NAME).write_bytes(child._OWNER_RESERVATION_MAGIC + b'C')
    guard = child._reserve_local_owner(root)
    lease, owner, factory = child._LocalAdmission(guard), object(), object()
    lease.bind_owner(owner)
    lease.bind_factory(owner, factory)
    lease.begin_worker(owner, factory)
    try:
        yield {'_admission':lease, '_admission_owner':owner, '_admission_factory':factory}
    finally:
        lease.finish_worker_start(owner, factory)
        lease.abandon()


@pytest.mark.parametrize('worker_only', [False, True])
def test_production_worker_marks_attempt_before_popen(staged_graceful, monkeypatch, synthetic_windows_subprocess,
                                                     worker_only, admission_args):
    process = Process(response(0, 0), on_eof=False)
    observed = []

    def popen(*args, **kwargs):
        owner = lifecycle._active
        observed.append((owner._require_spawn_attempt, owner._spawn_state))
        return process

    monkeypatch.setattr(child, '_Job', _Job)
    monkeypatch.setattr(subprocess, 'Popen', popen)
    options = dict(staged_graceful)
    if worker_only:
        options['tuple_args'] = {k: v for k, v in options['tuple_args'].items()
                                 if k not in ('memory', 'descriptor', 'descriptor-sha256')}
        options.update(worker_only=True, bulk_contract_sha256=child.BULK_CONTRACT_SHA256)
    result = child.start_e5_worker_graceful(enabled=True, **admission_args, **options)
    owner = result['owner']
    try:
        assert observed == [(True, 'ATTEMPTED')]
        assert owner._spawn_state == 'BOUND'
        assert result['status'] == 'READY'
    finally:
        finish(owner, process)


@pytest.mark.parametrize('worker_only', [False, True])
def test_ambiguous_popen_exception_holds_actual_owner(staged_graceful, monkeypatch, synthetic_windows_subprocess,
                                                      worker_only, admission_args):
    done = threading.Event()
    observed = []
    monkeypatch.setattr(lifecycle._Owner, '_keep', lambda self: done.wait(3))
    monkeypatch.setattr(lifecycle, '_active', None)
    monkeypatch.setattr(child, '_Job', _Job)

    def popen(*args, **kwargs):
        observed.append((lifecycle._active._require_spawn_attempt, lifecycle._active._spawn_state))
        raise OSError('synthetic spawn outcome unknown')

    monkeypatch.setattr(subprocess, 'Popen', popen)
    options = dict(staged_graceful)
    if worker_only:
        options['tuple_args'] = {k: v for k, v in options['tuple_args'].items()
                                 if k not in ('memory', 'descriptor', 'descriptor-sha256')}
        options.update(worker_only=True, bulk_contract_sha256=child.BULK_CONTRACT_SHA256)
    result = child.start_e5_worker_graceful(enabled=True, **admission_args, **options)
    owner = result['owner']
    try:
        assert observed == [(True, 'ATTEMPTED')]
        assert result['status'] == 'HOLD'
        assert owner.status()['owned'] is True
        assert owner._spawn_state == 'ATTEMPTED'
        assert not owner._job.closed
    finally:
        done.set()
        owner._keeper.join(timeout=2)
        assert not owner._keeper.is_alive()


def test_noncooperative_route_refuses_before_popen(staged_graceful, monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('unexpected native spawn'))
    with pytest.raises(child.LaunchRefused):
        child._start_e5_worker(cooperative=False, **staged_graceful)


def test_partially_initialized_job_cannot_start_worker():
    owner = lifecycle._Owner(require_spawn_attempt=True)
    owner.mark_job_attempt()
    owner.resources.job = _Job(kill_on_close=False)
    with pytest.raises(lifecycle.LifecycleRefused):
        owner.mark_spawn_attempt()
    assert owner._job_state == 'ATTEMPTED'
    assert owner._spawn_state == 'NOT_ENTERED'


@pytest.mark.parametrize('phase', ['before_allocation', 'during_allocation'])
def test_shutdown_during_job_initialization_retains_owner(
        staged_graceful, monkeypatch, synthetic_windows_subprocess, phase, admission_args):
    entered, resume = threading.Event(), threading.Event()
    captured, jobs, outcome = [], [], []

    class BarrierJob(_Job):
        def __init__(self, *, kill_on_close, _owner=None):
            super().__init__(kill_on_close=kill_on_close, _owner=_owner)
            self.allocated = False
            jobs.append(self)
            if phase == 'during_allocation':
                _owner.resources.job = self  # Same early retention as the real Job.
                entered.set()
                resume.wait(3)
            self.allocated = True

    def sink(owner):
        captured.append(owner)
        if phase == 'before_allocation':
            entered.set()
            resume.wait(3)

    def launch():
        try:
            outcome.append(child.start_e5_worker_graceful(enabled=True, owner_sink=sink,
                                                          **admission_args, **staged_graceful))
        except BaseException as error:
            outcome.append(error)

    monkeypatch.setattr(child, '_Job', BarrierJob)
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('unexpected native spawn'))
    worker = threading.Thread(target=launch, daemon=True)
    worker.start()
    try:
        assert entered.wait(2)
        owner = captured[0]
        assert owner.shutdown(timeout=0)['owned'] is True
        owner._keeper.join(timeout=0.1)
        assert owner.status()['owned'] is True
        if jobs:
            assert not jobs[0].closed
    finally:
        resume.set()
        worker.join(timeout=3)
        if captured:
            captured[0]._keeper.join(timeout=3)
    assert not worker.is_alive()
    assert captured[0].status()['owned'] is False
    assert jobs and jobs[0].allocated and jobs[0].closed
