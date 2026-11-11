"""Owned synthetic worker tests; no fleet process, files or live adapter."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import time

import pytest


def synthetic_snapshot():
    path = Path(__file__).with_name('test_wd_q4_startup_observer.py')
    spec = importlib.util.spec_from_file_location('synthetic_observer_cases', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = module.sample()
    data['clock_utc'] = module.NOW
    return data


def test_default_off_does_not_spawn(monkeypatch):
    from tools import wd_q4_startup_observer_process as runner
    def forbidden(*args, **kwargs): raise AssertionError('spawn forbidden')
    monkeypatch.setattr(runner.subprocess, 'Popen', forbidden)
    out = runner.run_synthetic_observation(snapshot=synthetic_snapshot())
    assert out['status'] == 'OFF' and out['authority_effect'] == 'none'


def test_isolated_worker_calls_real_observer_but_never_claims_production():
    from tools.wd_q4_startup_observer_process import run_synthetic_observation
    out = run_synthetic_observation(enabled=True, snapshot=synthetic_snapshot(), timeout_seconds=10)
    assert out['status'] == 'HOLD'
    assert out['worker_status'] == 'PARTIAL_OBSERVATIONS_MATCH'
    assert out['production_ready'] is False and out['authority_effect'] == 'none'
    assert out['live_sources_verified'] is False and out['rollback_performed'] is False
    assert out['wall_clock_deadline_enforced'] is False


def test_missing_source_returns_hold_in_owned_process():
    from tools.wd_q4_startup_observer_process import run_synthetic_observation
    d = synthetic_snapshot()
    del d['receipts']['fable-5']
    out = run_synthetic_observation(enabled=True, snapshot=d, timeout_seconds=10)
    assert out['status'] == 'HOLD' and out['worker_status'] == 'HOLD'
    assert not out['rollback_performed']


def test_hung_owned_child_times_out_and_is_reaped(monkeypatch):
    from tools import wd_q4_startup_observer_process as runner
    original = subprocess.Popen
    children = []
    def start_synthetic_hung_child(*args, **kwargs):
        child = original([sys.executable, '-I', '-S', '-c', 'import time; time.sleep(30)'], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(runner.subprocess, 'Popen', start_synthetic_hung_child)
    start = time.monotonic()
    out = runner.run_synthetic_observation(enabled=True, snapshot=synthetic_snapshot(), timeout_seconds=0.3)
    assert time.monotonic() - start < 10
    assert out['status'] == 'HOLD' and out['worker_status'] == 'TIMEOUT'
    assert out['owned_child_reaped'] is True
    assert len(children) == 1 and children[0].poll() is not None


@pytest.mark.parametrize('kwargs', [dict(enabled=1), dict(enabled=True, timeout_seconds=0),
                                  dict(enabled=True, timeout_seconds=True),
                                  dict(enabled=True, timeout_seconds=61),
                                  dict(enabled=True, timeout_seconds=float('nan')),
                                  dict(enabled=True, timeout_seconds=float('inf'))])
def test_bad_enable_and_deadlines_fail_closed_without_spawn(monkeypatch, kwargs):
    from tools import wd_q4_startup_observer_process as runner
    monkeypatch.setattr(runner.subprocess, 'Popen', lambda *a, **k: pytest.fail('unexpected spawn'))
    out = runner.run_synthetic_observation(snapshot=synthetic_snapshot(), **kwargs)
    assert out['status'] == 'HOLD' and out['authority_effect'] == 'none'


def test_oversized_fixture_fails_before_process_start(monkeypatch):
    from tools import wd_q4_startup_observer_process as runner
    monkeypatch.setattr(runner.subprocess, 'Popen', lambda *a, **k: pytest.fail('unexpected spawn'))
    data = synthetic_snapshot()
    data['padding'] = 'x' * 20000
    out = runner.run_synthetic_observation(enabled=True, snapshot=data)
    assert out['status'] == 'HOLD' and out['worker_status'] == 'UNKNOWN'


def test_process_creation_failure_returns_unknown(monkeypatch):
    from tools import wd_q4_startup_observer_process as runner
    def denied(*args, **kwargs): raise OSError('synthetic failure')
    monkeypatch.setattr(runner.subprocess, 'Popen', denied)
    out = runner.run_synthetic_observation(enabled=True, snapshot=synthetic_snapshot())
    assert out['status'] == 'HOLD' and out['worker_status'] == 'UNKNOWN'
    assert out['owned_child_reaped'] is None
