"""Tests use fabricated observations; no live runtime or rollback is touched."""

import importlib.util
from pathlib import Path
import subprocess

import pytest

from tools.wd_q4_startup_observer_process import observe_synthetic_process


def sample():
    path = Path(__file__).with_name('test_wd_q4_startup_observer.py')
    spec = importlib.util.spec_from_file_location('synthetic_observer_fixture', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return dict(module.sample(), now=module.NOW, stall=False)


def test_default_off_and_non_boolean_never_launch(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('unexpected subprocess')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    assert observe_synthetic_process()['status'] == 'OFF'
    assert observe_synthetic_process(enabled=1, fixture=sample())['status'] == 'HOLD'


def test_matching_fixture_remains_synthetic_and_has_no_authority():
    out = observe_synthetic_process(enabled=True, fixture=sample(), timeout_seconds=5)
    assert out['status'] == 'SYNTHETIC_PARTIAL'
    assert out['observation']['receipt_decision'] == 'RECEIPTS_OK'
    assert out['observation']['agent_context_verified'] is False
    assert out['production_decision_supported'] is False
    assert out['wall_clock_deadline_enforced'] is False
    assert out['rollback_performed'] is False


def test_missing_receipt_is_hold():
    data = sample()
    del data['receipts']['fable-5']
    out = observe_synthetic_process(enabled=True, fixture=data, timeout_seconds=5)
    assert out['status'] == 'HOLD'
    assert out['reason'] == 'observation_hold'
    assert out['rollback_performed'] is False


def test_stalled_synthetic_child_is_terminated_without_rollback():
    data = sample()
    data['stall'] = True
    out = observe_synthetic_process(enabled=True, fixture=data, timeout_seconds=0.5)
    assert out['status'] == 'HOLD'
    assert out['reason'] == 'child_timeout'
    assert out['wall_clock_deadline_enforced'] is False
    assert out['rollback_performed'] is False


@pytest.mark.parametrize('timeout', [True, 0, -1, 11, float('nan'), float('inf'), '1'])
def test_invalid_timeout_refuses_before_launch(timeout, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('unexpected subprocess')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    assert observe_synthetic_process(enabled=True, fixture=sample(), timeout_seconds=timeout)['reason'] == 'invalid_input'


def test_child_process_receives_no_ambient_secrets(monkeypatch):
    seen = {}
    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 1, b'')
    monkeypatch.setenv('SYNTHETIC_SECRET_FOR_TEST', 'not-for-child')
    monkeypatch.setattr(subprocess, 'run', fake_run)
    out = observe_synthetic_process(enabled=True, fixture=sample())
    assert out['reason'] == 'child_failed'
    assert 'SYNTHETIC_SECRET_FOR_TEST' not in seen['env']
    assert 'PYTHONPATH' not in seen['env']
