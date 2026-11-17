"""Synthetic owner-host tests; no model, native child, or live marker."""

import json
import os
from pathlib import Path
import runpy
import threading

import pytest

from tools import wd_mind_guardian_owned_host as host
from tools import wd_mind_runtime_child as child


MANIFEST = 'a' * 64
POLICY = 'b' * 64
APPROVAL = 'c' * 64


def _new(tmp_path, *, enabled=False):
    return host.create_host(enabled=enabled, bundle_root=str(tmp_path), manifest_sha256=MANIFEST,
                            policy_path=str(tmp_path / 'synthetic-policy'), policy_sha256=POLICY,
                            approval_reference_sha256=APPROVAL)


def _emit(stream, state):
    raw = json.dumps({'schema':'wd.mind-local-owner-status.v1', 'state':state,
        'manifest_sha256':MANIFEST, 'authority':'none', 'caller_authentication':'UNKNOWN',
        'source_authentication':'UNKNOWN', 'index_refresh':'NOT_SCHEDULED'},
        sort_keys=True, separators=(',', ':')).encode('ascii') + b'\n'
    assert stream.write(raw) == len(raw)
    stream.flush()


def test_default_off_never_starts_owner(tmp_path):
    owner = _new(tmp_path)
    assert owner.start()['state'] == 'OFF'
    assert owner.status()['state'] == 'OFF'


def test_unverified_bootstrap_refuses_before_owner_start(tmp_path):
    with pytest.raises(host.HostRefused):
        _new(tmp_path, enabled=True)


def test_direct_host_construction_cannot_bypass_closed_bootstrap(tmp_path):
    owner = host._OwnedHost(True, str(tmp_path), MANIFEST, str(tmp_path / 'policy'), POLICY, APPROVAL)
    assert owner.start()['state'] == 'HOLD'
    assert owner._thread is None


def test_direct_run_refuses_without_importing_child(tmp_path, monkeypatch):
    import builtins
    original = builtins.__import__

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'tools' and 'wd_mind_runtime_child' in fromlist:
            raise AssertionError('child imported before verified host start')
        return original(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, '__import__', guarded)
    owner = host._OwnedHost(True, str(tmp_path), MANIFEST, str(tmp_path / 'policy'), POLICY, APPROVAL)
    owner._run()
    assert owner.status()['state'] == 'HOLD'


def test_off_module_import_does_not_execute_child(monkeypatch):
    import builtins
    original = builtins.__import__

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'tools' and 'wd_mind_runtime_child' in fromlist:
            raise AssertionError('child imported before verified host start')
        return original(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, '__import__', guarded)
    module_path = Path(__file__).resolve().parents[2] / 'tools/wd_mind_guardian_owned_host.py'
    loaded = runpy.run_path(str(module_path), run_name='synthetic_off_host')
    assert loaded['_BOOTSTRAP_PROVEN'] is False


def test_stop_does_not_wait_on_host_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(host, '_BOOTSTRAP_PROVEN', True)
    owner = _new(tmp_path, enabled=True)
    assert owner._lock.acquire(blocking=False)
    try:
        assert owner.request_stop()['state'] == 'HOLD'
        assert owner._stop.is_set()
    finally:
        owner._lock.release()


def test_stop_before_delayed_host_run_never_enters_guarded_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(host, '_BOOTSTRAP_PROVEN', True)
    calls = []
    monkeypatch.setattr(child, '_guarded_local_entry', lambda *args, **kwargs: calls.append('entered'))

    class DelayedThread:
        def __init__(self, *, target, **kwargs):
            self.target = target

        def start(self):
            pass  # Deterministically defer the worker without creating a thread.

    monkeypatch.setattr(host.threading, 'Thread', DelayedThread)
    owner = _new(tmp_path, enabled=True)
    assert owner.start()['state'] == 'STARTING'
    assert owner.request_stop()['state'] == 'HOLD'
    owner._thread.target()
    assert calls == []
    assert owner.status()['state'] == 'STOPPED'


def test_stop_during_entry_gate_is_not_accepted_and_can_be_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(host, '_BOOTSTRAP_PROVEN', True)
    owner = _new(tmp_path, enabled=True)
    assert owner._launch_gate.acquire(blocking=False)
    try:
        assert owner.request_stop()['state'] == 'BUSY'
        assert not owner._stop.is_set()
    finally:
        owner._launch_gate.release()
    assert owner.request_stop()['state'] == 'HOLD'
    assert owner._stop.is_set()


def test_stop_after_entry_commit_keeps_uncertain_owner_in_hold(tmp_path, monkeypatch):
    monkeypatch.setattr(host, '_BOOTSTRAP_PROVEN', True)
    owner = _new(tmp_path, enabled=True)
    calls = []

    def entry(*args, **kwargs):
        calls.append('entered')
        assert owner.request_stop()['state'] == 'HOLD'
        return 2  # Entry did not prove cooperative cleanup.

    monkeypatch.setattr(child, '_guarded_local_entry', entry)
    owner._run()
    assert calls == ['entered']
    assert owner.status()['state'] == 'HOLD'


@pytest.mark.skipif(os.name != 'nt', reason='reservation uses Windows byte locks')
def test_owned_host_retains_guard_until_voluntary_stop(tmp_path, monkeypatch):
    marker = tmp_path / child._OWNER_RESERVATION_NAME
    marker.write_bytes(child._OWNER_RESERVATION_MAGIC + b'C')
    monkeypatch.setattr(host, '_BOOTSTRAP_PROVEN', True)  # Synthetic-only gate override.
    monkeypatch.setattr(child, '_AUDIT_ROOT', tmp_path)
    ready = threading.Event()

    def fake_entry(*, control_stream, output_stream, _cleanup_evidence, _admission, **unused):
        _emit(output_stream, 'STARTING')
        _emit(output_stream, 'READY')
        ready.set()
        assert control_stream.read(1) == b'S'
        _cleanup_evidence['clean'] = True
        _emit(output_stream, 'STOPPED')
        return 0

    monkeypatch.setattr(child, '_local_candidate_owner_entry', fake_entry)
    owner = _new(tmp_path, enabled=True)
    try:
        assert owner.start()['state'] in ('STARTING', 'READY')
        assert ready.wait(2)
        with pytest.raises(child.LaunchRefused):
            child._reserve_local_owner(tmp_path)
        assert owner.request_stop()['state'] in ('HOLD', 'STOPPED')
        owner._thread.join(timeout=2)
        assert not owner._thread.is_alive()
        assert owner.status()['state'] == 'STOPPED'
        assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC + b'C'
    finally:
        owner.request_stop()
        if owner._thread is not None:
            owner._thread.join(timeout=2)


@pytest.mark.skipif(os.name != 'nt', reason='reservation uses Windows byte locks')
def test_blocked_owner_retains_hold_until_cooperative_completion(tmp_path, monkeypatch):
    marker = tmp_path / child._OWNER_RESERVATION_NAME
    marker.write_bytes(child._OWNER_RESERVATION_MAGIC + b'C')
    monkeypatch.setattr(host, '_BOOTSTRAP_PROVEN', True)
    monkeypatch.setattr(child, '_AUDIT_ROOT', tmp_path)
    ready, release, stopping = threading.Event(), threading.Event(), threading.Event()

    def fake_entry(*, control_stream, output_stream, _cleanup_evidence, _admission, **unused):
        _emit(output_stream, 'READY')
        ready.set()
        assert control_stream.read(1) == b'S'
        stopping.set()
        assert release.wait(2)
        _cleanup_evidence['clean'] = True
        _emit(output_stream, 'STOPPED')
        return 0

    monkeypatch.setattr(child, '_local_candidate_owner_entry', fake_entry)
    owner = _new(tmp_path, enabled=True)
    owner.start()
    try:
        assert ready.wait(2)
        assert owner.request_stop()['state'] == 'HOLD'
        assert stopping.wait(2) and owner._thread.is_alive()
        with pytest.raises(child.LaunchRefused):
            child._reserve_local_owner(tmp_path)
    finally:
        release.set()
        owner._thread.join(timeout=2)
    assert not owner._thread.is_alive() and owner.status()['state'] == 'STOPPED'
    assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC + b'C'


@pytest.mark.skipif(os.name != 'nt', reason='reservation uses Windows byte locks')
def test_invalid_status_refuses_and_preserves_reserved_marker(tmp_path, monkeypatch):
    marker = tmp_path / child._OWNER_RESERVATION_NAME
    marker.write_bytes(child._OWNER_RESERVATION_MAGIC + b'C')
    monkeypatch.setattr(host, '_BOOTSTRAP_PROVEN', True)
    monkeypatch.setattr(child, '_AUDIT_ROOT', tmp_path)

    def fake_entry(*, output_stream, **unused):
        output_stream.write(b'{"state":"READY","extra":"do-not-trust"}\n')
        raise AssertionError('unreachable')

    monkeypatch.setattr(child, '_local_candidate_owner_entry', fake_entry)
    owner = _new(tmp_path, enabled=True)
    owner.start()
    owner._thread.join(timeout=2)
    assert not owner._thread.is_alive()
    assert owner.status()['state'] == 'HOLD'
    assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC + b'R'
