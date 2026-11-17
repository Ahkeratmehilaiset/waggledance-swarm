"""Synthetic tests of the actual cooperative worker-start route."""

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


@pytest.mark.parametrize('worker_only', [False, True])
def test_production_worker_marks_attempt_before_popen(staged_graceful, monkeypatch, synthetic_windows_subprocess,
                                                     worker_only):
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
    result = child.start_e5_worker_graceful(enabled=True, **options)
    owner = result['owner']
    try:
        assert observed == [(True, 'ATTEMPTED')]
        assert owner._spawn_state == 'BOUND'
        assert result['status'] == 'READY'
    finally:
        finish(owner, process)


@pytest.mark.parametrize('worker_only', [False, True])
def test_ambiguous_popen_exception_holds_actual_owner(staged_graceful, monkeypatch, synthetic_windows_subprocess,
                                                      worker_only):
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
    result = child.start_e5_worker_graceful(enabled=True, **options)
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
