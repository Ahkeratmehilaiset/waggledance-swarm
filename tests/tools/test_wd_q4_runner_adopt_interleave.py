"""Synthetic runner/adoption races; no live bridge or model is used."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
from threading import Event
import pytest

pytest_plugins = ('tests.tools.test_wd_q4_continuation_adopt',)
from tools import wd_q4_genesis_to_composer_cli as initial


def runner_fixture(tmp_path, monkeypatch, args):
    module_path = Path(__file__).with_name('test_wd_q4_status_prefix_runner.py')
    spec = importlib.util.spec_from_file_location('independent_status_race_fixture', module_path)
    status = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(status)
    runner = status.runner.__wrapped__()
    scope = tmp_path / 'runner-isolated-inputs'
    scope.mkdir()
    _, make_args, route, _, _, _ = status.case.__wrapped__(scope, monkeypatch, runner)
    gt = json.loads(args['genesis_tuple_bytes'])
    bound = initial._tuple_part(gt, 'route')
    private, shared = Path(bound['private_state_root']), Path(bound['checkpoint_root'])
    runner_args = make_args({**route, 'private_state_root': str(private), 'checkpoint_root': str(shared)})
    return status, runner, runner_args, private, shared


def test_actual_runner_first_prevents_actual_adoption(actual_continuation, tmp_path, monkeypatch):
    from tools import wd_q4_continuation_adopt as adopt
    args = actual_continuation
    status, runner, runner_args, private, shared = runner_fixture(tmp_path, monkeypatch, args)
    started, release = Event(), Event()
    def child(**kw):
        started.set()
        assert release.wait(10)
        return dict(status.child_report(), plan_sha256=kw['expected_plan_sha256'])
    status.fake_child(monkeypatch, child)
    old = adopt._prior_pointer
    pool = ThreadPoolExecutor(max_workers=1)
    future = []
    def start_runner(*a, **kw):
        result = old(*a, **kw)
        future.append(pool.submit(runner.run_once, **runner_args))
        assert started.wait(10), 'runner did not enter child'
        assert (private / runner._PENDING).exists() and (shared / runner._PENDING).exists()
        return result
    monkeypatch.setattr(adopt, '_prior_pointer', start_runner)
    try:
        with pytest.raises(adopt.AdoptOutcomeUnknown):
            adopt.run_once(**args)
        assert not (private / 'q4-continuation-adopt.pending.json').exists()
    finally:
        release.set()
        if future:
            future[0].result(timeout=10)
        pool.shutdown(wait=True)


def test_actual_adoption_first_prevents_actual_runner(actual_continuation, tmp_path, monkeypatch):
    from tools import wd_q4_continuation_adopt as adopt
    args = actual_continuation
    status, runner, runner_args, private, shared = runner_fixture(tmp_path, monkeypatch, args)
    marker = private / 'q4-continuation-adopt.pending.json'
    child_calls = []
    status.fake_child(monkeypatch, lambda **kw: child_calls.append(kw) or status.child_report())
    old = adopt.runner._create
    pool = ThreadPoolExecutor(max_workers=1)
    future = []
    def start_runner(path, raw):
        value = old(path, raw)
        if path == marker:
            future.append(pool.submit(runner.run_once, **runner_args))
        return value
    def fail_before_publication(*a, **kw):
        assert future
        with pytest.raises((runner.StatusRunnerRefused, runner.StatusRunnerOutcomeUnknown)):
            future[0].result(timeout=10)
        assert not child_calls
        raise OSError('synthetic interrupted publication')
    monkeypatch.setattr(adopt.runner, '_create', start_runner)
    monkeypatch.setattr(adopt.update, '_publish', fail_before_publication)
    try:
        with pytest.raises(adopt.AdoptOutcomeUnknown):
            adopt.run_once(**args)
        assert marker.exists()
        assert not (private / runner._PENDING).exists()
        assert not (shared / runner._PENDING).exists()
    finally:
        pool.shutdown(wait=True)
