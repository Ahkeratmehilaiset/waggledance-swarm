"""Synthetic tests of the cross-process Mind owner reservation.

No production marker or live owner is touched. A preseeded CLEAR fixture is
not evidence that an older, unguarded owner was absent during migration.
"""
import io
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools import wd_mind_runtime_child as child


ROOT = Path(__file__).resolve().parents[2]
MARKER = 'mind-owner-reservation.v1'
PREFIX = b'WD_MIND_OWNER_V1\n'
WINDOWS = pytest.mark.skipif(os.name != 'nt', reason='Windows reservation uses byte-range locking')


class _SyntheticReadyOwner:
    """Exercise the real adapter close and its pre-close status evidence."""
    def __init__(self, close_callback):
        self._close_callback = close_callback

    def status(self):
        return {'state': 'READY', 'owned': True}

    def close(self):
        return self._close_callback()


def test_blocked_hold_output_allows_cooperative_adapter_close(monkeypatch):
    """A blocked status writer cannot prevent a cooperative close attempt."""
    import threading
    from tools import wd_mind_full_runtime as runtime
    from tools import wd_mind_runtime_process as process

    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    monkeypatch.setattr(child, '_LOCAL_SERVICE_CLOSE_GRACE_SECONDS', 0.05)
    monkeypatch.setattr(child, '_read_local_policy_frame', lambda *args, **kwargs: b'synthetic-policy')
    service_started, service_close_seen, adapter_closed = (threading.Event() for _ in range(3))
    hold_write_started, release_output = threading.Event(), threading.Event()
    adapter = child._LocalManifestQuery('codex-lead-1', 'synthetic', 'a' * 64,
        b'synthetic-policy', 'b' * 64, 'c' * 64, 1, 0, 1)
    monkeypatch.setattr(child._LocalManifestQuery, 'start', lambda self: {'state': 'STARTING'})
    monkeypatch.setattr(child._LocalManifestQuery, 'status', lambda self: {'state': 'READY', 'owned': True})
    monkeypatch.setattr(child._LocalManifestQuery, 'start_failure_observed', lambda self: False)

    def close_owner():
        assert hold_write_started.wait(3)
        adapter_closed.set()
        return ({'state': 'STOPPED', 'owned': False} if release_output.is_set()
                else {'state': 'HOLD', 'owned': True})

    adapter._owner = _SyntheticReadyOwner(close_owner)
    monkeypatch.setattr(child, 'create_local_manifest_query', lambda **kwargs: adapter)

    class Service:
        def start(self): service_started.set()
        def status(self): return {'state': 'RUNNING'}
        def close(self, timeout):
            service_close_seen.set()
            return {'state': 'STOPPED' if adapter_closed.is_set() else 'HOLD'}

    class Input:
        def read(self, size):
            assert service_started.wait(3)
            return b'S'

    class BlockedOutput(io.BytesIO):
        def write(self, data):
            if b'"state":"HOLD"' in bytes(data):
                hold_write_started.set()
                if not release_output.wait(3):
                    raise TimeoutError('synthetic output was not released')
            return super().write(data)

    monkeypatch.setattr(process, 'local_candidate_pipe_name', lambda **kwargs: 'synthetic')
    monkeypatch.setattr(process, 'create_local_candidate_service', lambda **kwargs: Service())
    result, output = [], BlockedOutput()

    def run():
        result.append(child._local_candidate_owner_entry(control_stream=Input(), output_stream=output,
            bundle_root='synthetic', expected_manifest_sha256='a' * 64,
            policy_sha256='b' * 64, approval_reference_sha256='c' * 64))

    owner_thread = threading.Thread(target=run, daemon=True)
    owner_thread.start()
    try:
        assert service_close_seen.wait(3)
        assert hold_write_started.wait(3)
        assert adapter_closed.wait(2), 'blocked HOLD output prevented adapter close'
        assert owner_thread.is_alive()
        assert b'"state":"STOPPED"' not in output.getvalue()
        release_output.set()
        owner_thread.join(3)
        assert not owner_thread.is_alive()
        assert result == [0]
    finally:
        release_output.set()
        hold_write_started.set()
        owner_thread.join(4)
        assert not owner_thread.is_alive(), 'synthetic owner did not close cooperatively'


@pytest.mark.parametrize('unproven_owner', [False, True])
def test_held_service_allows_cooperative_owner_close_after_bounded_grace(monkeypatch, unproven_owner):
    """Keep service ownership while allowing the real adapter to stop its owner."""
    import threading
    from tools import wd_mind_full_runtime as runtime
    from tools import wd_mind_runtime_process as process

    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    monkeypatch.setattr(child, '_LOCAL_SERVICE_CLOSE_GRACE_SECONDS', 0.05, raising=False)
    monkeypatch.setattr(child, '_read_local_policy_frame', lambda *args, **kwargs: b'synthetic-policy')
    service_started, release_service, adapter_closed = threading.Event(), threading.Event(), threading.Event()
    close_calls = []
    adapter = child._LocalManifestQuery('codex-lead-1', 'synthetic', 'a' * 64,
        b'synthetic-policy', 'b' * 64, 'c' * 64, 1, 0, 1)
    monkeypatch.setattr(child._LocalManifestQuery, 'start', lambda self: {'state': 'STARTING'})
    monkeypatch.setattr(child._LocalManifestQuery, 'status', lambda self: {'state': 'READY', 'owned': True})
    monkeypatch.setattr(child._LocalManifestQuery, 'start_failure_observed', lambda self: False)

    def close_owner():
        close_calls.append(1)
        adapter_closed.set()
        if unproven_owner and not release_service.is_set():
            return {'state': 'HOLD', 'owned': True}
        release_service.set()
        return {'state': 'STOPPED', 'owned': False}

    adapter._owner = _SyntheticReadyOwner(close_owner)
    monkeypatch.setattr(child, 'create_local_manifest_query', lambda **kwargs: adapter)

    class Service:
        def start(self): service_started.set()
        def status(self): return {'state': 'RUNNING'}
        def close(self, timeout):
            return {'state': 'STOPPED' if release_service.is_set() else 'HOLD'}

    class Input:
        def read(self, size):
            assert service_started.wait(3)
            return b''

    monkeypatch.setattr(process, 'local_candidate_pipe_name', lambda **kwargs: 'synthetic')
    monkeypatch.setattr(process, 'create_local_candidate_service', lambda **kwargs: Service())
    result, output = [], io.BytesIO()
    def run():
        result.append(child._local_candidate_owner_entry(control_stream=Input(), output_stream=output,
            bundle_root='synthetic', expected_manifest_sha256='a' * 64,
            policy_sha256='b' * 64, approval_reference_sha256='c' * 64))
    owner_thread = threading.Thread(target=run, daemon=True)
    owner_thread.start()
    try:
        assert adapter_closed.wait(2), 'service HOLD blocked cooperative adapter close'
        if unproven_owner:
            assert owner_thread.is_alive(), 'unproven service must remain held'
            assert b'"state":"STOPPED"' not in output.getvalue()
            release_service.set()
        owner_thread.join(3)
        assert not owner_thread.is_alive()
        assert result == [0]
        if not unproven_owner:
            assert len(close_calls) == 1, 'confirmed owner close must not be repeated'
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        assert [record['state'] for record in records if 'state' in record][-1] == 'STOPPED'
    finally:
        release_service.set()
        owner_thread.join(4)
        assert not owner_thread.is_alive(), 'synthetic owner did not close cooperatively'


def test_cooperative_adapter_close_retains_busy_owner():
    """Early cancellation never closes an owner while its query lock is held."""
    import threading
    from tools import wd_mind_full_runtime as runtime

    owner = object.__new__(runtime._SingleManifestOwner)
    owner._lock = threading.Lock()
    adapter = child._LocalManifestQuery('codex-lead-1', 'synthetic', 'a' * 64,
        b'synthetic-policy', 'b' * 64, 'c' * 64, 1, 0, 1)
    adapter._owner = owner
    adapter._state = 'READY'
    owner._lock.acquire()
    try:
        assert adapter.close() == {'state': 'BUSY', 'owned': None, 'authority': 'none'}
        assert adapter._cancel.is_set()
        assert adapter._state == 'HOLD'
        assert adapter._owner is owner
    finally:
        owner._lock.release()


@pytest.mark.parametrize('bad_clock', [float('nan'), float('inf'), -float('inf'), True, None, 'invalid'])
def test_close_clock_invalid_values_disable_early_close(monkeypatch, bad_clock):
    class Clock:
        @staticmethod
        def monotonic(): return bad_clock
    monkeypatch.setattr(child, 'time', Clock())
    assert child._local_service_close_clock() is None


@pytest.mark.parametrize('fail_call', [1, 2])
@pytest.mark.parametrize('failure', [KeyboardInterrupt, RuntimeError])
def test_close_clock_failure_retains_service_until_confirmed(monkeypatch, fail_call, failure):
    """A clock error cannot skip service ownership or report a false stop."""
    import threading
    import time
    from tools import wd_mind_full_runtime as runtime
    from tools import wd_mind_runtime_process as process

    clock_failure_seen = threading.Event()
    class Clock:
        calls = 0
        @staticmethod
        def sleep(seconds): time.sleep(seconds)
        def monotonic(self):
            self.calls += 1
            if self.calls == fail_call:
                clock_failure_seen.set()
                raise failure('synthetic-clock-failure')
            return time.monotonic()

    monkeypatch.setattr(child, 'time', Clock())
    monkeypatch.setattr(child, '_LOCAL_SERVICE_CLOSE_GRACE_SECONDS', 0.05)
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    monkeypatch.setattr(child, '_read_local_policy_frame', lambda *args, **kwargs: b'synthetic-policy')
    started, close_seen, service_hold_seen, release, adapter_closed = (threading.Event() for _ in range(5))
    cleanup_evidence = {'clean': False}
    adapter = child._LocalManifestQuery('codex-lead-1', 'synthetic', 'a' * 64,
        b'synthetic-policy', 'b' * 64, 'c' * 64, 1, 0, 1)
    monkeypatch.setattr(child._LocalManifestQuery, 'start', lambda self: {'state': 'STARTING'})
    monkeypatch.setattr(child._LocalManifestQuery, 'status', lambda self: {'state': 'READY', 'owned': True})
    monkeypatch.setattr(child._LocalManifestQuery, 'start_failure_observed', lambda self: False)
    def close_owner():
        adapter_closed.set()
        return ({'state': 'STOPPED', 'owned': False} if release.is_set()
                else {'state': 'HOLD', 'owned': True})
    adapter._owner = _SyntheticReadyOwner(close_owner)
    monkeypatch.setattr(child, 'create_local_manifest_query', lambda **kwargs: adapter)

    class Service:
        def start(self): started.set()
        def status(self): return {'state': 'RUNNING'}
        def close(self, timeout):
            close_seen.set()
            if not release.is_set():
                service_hold_seen.set()
                return {'state': 'HOLD'}
            return {'state': 'STOPPED'}
    class Input:
        def read(self, size):
            assert started.wait(3)
            return b''
    monkeypatch.setattr(process, 'local_candidate_pipe_name', lambda **kwargs: 'synthetic')
    monkeypatch.setattr(process, 'create_local_candidate_service', lambda **kwargs: Service())
    result, output = [], io.BytesIO()
    def run():
        result.append(child._local_candidate_owner_entry(control_stream=Input(), output_stream=output,
            bundle_root='synthetic', expected_manifest_sha256='a' * 64,
            policy_sha256='b' * 64, approval_reference_sha256='c' * 64, _cleanup_evidence=cleanup_evidence))
    owner_thread = threading.Thread(target=run, daemon=True)
    owner_thread.start()
    try:
        assert close_seen.wait(3)
        assert service_hold_seen.wait(2), 'service HOLD was not observed'
        assert clock_failure_seen.wait(2), 'requested clock failure was not injected'
        assert adapter_closed.wait(2), 'cooperative adapter close did not start'
        assert owner_thread.is_alive()
        assert b'"state":"STOPPED"' not in output.getvalue()
        assert cleanup_evidence['clean'] is False
        release.set()
        owner_thread.join(3)
        assert not owner_thread.is_alive()
        assert adapter_closed.is_set()
        assert result == [0]
        assert cleanup_evidence['clean'] is True
    finally:
        release.set()
        owner_thread.join(4)
        assert not owner_thread.is_alive(), 'synthetic owner did not close cooperatively'


def _seed(root, state=b'C'):
    root.mkdir()
    (root / MARKER).write_bytes(PREFIX + state)
    return root


def _run_synthetic_owner_bounded(call, *, release_events=(), timeout=8):
    """Always release synthetic gates; a timeout is a failure, never success."""
    import threading
    result, errors = [], []

    def run():
        try:
            result.append(call())
        except BaseException as exc:
            errors.append(exc)

    owner = threading.Thread(target=run, name='synthetic-test-owner', daemon=True)
    owner.start()
    try:
        owner.join(timeout)
        completed_in_time = not owner.is_alive()
    finally:
        for event in release_events:
            event.set()
        owner.join(5)
    assert not owner.is_alive(), 'synthetic owner remains alive after releasing all test gates'
    assert completed_in_time, 'synthetic owner timed out before test gates were released'
    if errors:
        raise errors[0]
    assert len(result) == 1
    return result[0]


def test_synthetic_owner_control_eof_before_start_is_clean_stop(monkeypatch):
    import threading
    from tools import wd_mind_full_runtime as runtime
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    monkeypatch.setattr(child, '_read_local_policy_frame', lambda *args, **kwargs: b'synthetic-policy')
    starts = []
    class Adapter:
        def start(self):
            starts.append(True)
            return {'state': 'STARTING'}
        def status(self): return {'state': 'STARTING', 'owned': None}
        def close(self): return {'state': 'STOPPED', 'owned': False}
    monkeypatch.setattr(child, 'create_local_manifest_query', lambda **kwargs: Adapter())
    original_thread = threading.Thread
    class EagerControlThread(original_thread):
        def start(self):
            super().start()
            if self.name == 'mind-local-control':
                self.join(3)
                assert not self.is_alive()
    monkeypatch.setattr(child.threading, 'Thread', EagerControlThread)
    output = io.BytesIO()
    code = _run_synthetic_owner_bounded(lambda: child._local_candidate_owner_entry(control_stream=io.BytesIO(b''), output_stream=output,
        bundle_root='synthetic', expected_manifest_sha256='a' * 64,
        policy_sha256='b' * 64, approval_reference_sha256='c' * 64))
    states = [json.loads(line)['state'] for line in output.getvalue().splitlines()]
    assert code == 0
    assert starts == []
    assert states[-1] == 'STOPPED'
    assert all(state in ('HOLD', 'STOPPED') for state in states)


def test_synthetic_eof_does_not_hide_already_failed_start(monkeypatch):
    import threading
    from tools import wd_mind_full_runtime as runtime
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    monkeypatch.setattr(child, '_read_local_policy_frame', lambda *args, **kwargs: b'synthetic-policy')
    failed_status_seen, hold_written = threading.Event(), threading.Event()
    class Adapter:
        def start(self): return {'state': 'STARTING'}
        def status(self):
            failed_status_seen.set()
            return {'state': 'HOLD', 'owned': False}
        def close(self): return {'state': 'STOPPED', 'owned': False}
    monkeypatch.setattr(child, 'create_local_manifest_query', lambda **kwargs: Adapter())
    class Input:
        def read(self, size):
            assert failed_status_seen.wait(3)
            assert hold_written.wait(3)
            return b''
    class Output(io.BytesIO):
        def write(self, data):
            if b'"state":"HOLD"' in bytes(data): hold_written.set()
            return super().write(data)
    output = Output()
    code = _run_synthetic_owner_bounded(lambda: child._local_candidate_owner_entry(control_stream=Input(), output_stream=output,
        bundle_root='synthetic', expected_manifest_sha256='a' * 64,
        policy_sha256='b' * 64, approval_reference_sha256='c' * 64),
        release_events=(failed_status_seen, hold_written))
    states = [json.loads(line)['state'] for line in output.getvalue().splitlines()]
    assert code == 2
    assert states == ['STARTING', 'HOLD', 'STOPPED']


def test_synthetic_eof_does_not_hide_late_service_start_failure(monkeypatch):
    import threading
    from tools import wd_mind_full_runtime as runtime
    from tools import wd_mind_runtime_process as process
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    monkeypatch.setattr(child, '_read_local_policy_frame', lambda *args, **kwargs: b'synthetic-policy')
    entered, release = threading.Event(), threading.Event()
    class Adapter:
        _owner = object()
        def start(self): return {'state': 'STARTING'}
        def status(self): return {'state': 'READY', 'owned': True}
        def close(self): return {'state': 'STOPPED', 'owned': False}
    class Service:
        def start(self):
            entered.set()
            assert release.wait(3)
            raise ValueError('synthetic-service-start-failure')
        def close(self, timeout): return {'state': 'STOPPED'}
    class Input:
        def read(self, size):
            assert entered.wait(3)
            return b''
    class Output(io.BytesIO):
        def write(self, data):
            if b'"state":"HOLD"' in bytes(data): release.set()
            return super().write(data)
    monkeypatch.setattr(child, 'create_local_manifest_query', lambda **kwargs: Adapter())
    monkeypatch.setattr(process, 'local_candidate_pipe_name', lambda **kwargs: 'synthetic')
    monkeypatch.setattr(process, 'create_local_candidate_service', lambda **kwargs: Service())
    output = Output()
    code = _run_synthetic_owner_bounded(lambda: child._local_candidate_owner_entry(control_stream=Input(), output_stream=output,
        bundle_root='synthetic', expected_manifest_sha256='a' * 64,
        policy_sha256='b' * 64, approval_reference_sha256='c' * 64),
        release_events=(entered, release))
    states = [json.loads(line)['state'] for line in output.getvalue().splitlines()]
    assert code == 2
    assert states == ['STARTING', 'HOLD', 'STOPPED']


@WINDOWS
@pytest.mark.parametrize('serve_fails', [False, True])
def test_synthetic_real_service_finishes_after_stop_with_failure_evidence(monkeypatch, serve_fails):
    """Real service threads/close, synthetic AF_PIPE transport and adapter."""
    import threading
    import multiprocessing.connection as transport
    from tools import wd_mind_full_runtime as runtime
    from tools import wd_mind_runtime_process as process
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    monkeypatch.setattr(child, '_read_local_policy_frame', lambda *args, **kwargs: b'synthetic-policy')
    entered, wake, closed, failure_seen = threading.Event(), threading.Event(), threading.Event(), threading.Event()
    class Adapter:
        _owner = object()
        def start(self): return {'state': 'STARTING'}
        def status(self): return {'state': 'READY', 'owned': True}
        def close(self): return {'state': 'STOPPED', 'owned': False}
        def candidates(self, request): raise AssertionError('no candidate query expected')
    class Connection:
        def poll(self, timeout):
            failure_seen.set()
            raise KeyboardInterrupt()
        def close(self): pass
    class Listener:
        def __init__(self, **kwargs): pass
        def accept(self):
            entered.set()
            if not serve_fails: assert wake.wait(4)
            return Connection()
        def close(self): closed.set()
    def client(**kwargs):
        wake.set()
        return Connection()
    monkeypatch.setattr(transport, 'Listener', Listener)
    monkeypatch.setattr(transport, 'Client', client)
    monkeypatch.setattr(child, 'create_local_manifest_query', lambda **kwargs: Adapter())
    class Input:
        def read(self, size):
            assert (failure_seen if serve_fails else entered).wait(4)
            return b''
    output, result = io.BytesIO(), []
    def owner_entry():
        result.append(child._local_candidate_owner_entry(control_stream=Input(), output_stream=output,
            bundle_root='synthetic', expected_manifest_sha256='a' * 64,
            policy_sha256='b' * 64, approval_reference_sha256='c' * 64))
    owner = threading.Thread(target=owner_entry, name='synthetic-owner', daemon=True)
    owner.start()
    try:
        owner.join(4)
        assert not owner.is_alive(), 'owner retained HOLD after actual service threads ended'
        assert closed.is_set()
        assert result == [2 if serve_fails else 0]
        states = [json.loads(line)['state'] for line in output.getvalue().splitlines()]
        assert states[0] == 'STARTING' and states[-1] == 'STOPPED'
        if serve_fails: assert 'HOLD' in states
    finally:
        wake.set()
        owner.join(4)
        assert not owner.is_alive(), 'synthetic owner did not close cooperatively'


@pytest.mark.parametrize('startup_failed', [False, True])
def test_synthetic_eof_does_not_hide_adapter_failure_during_cleanup(monkeypatch, startup_failed):
    import threading
    from tools import wd_mind_full_runtime as runtime
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    monkeypatch.setattr(child, '_read_local_policy_frame', lambda *args, **kwargs: b'synthetic-policy')
    guard = child._LocalOwnerReservation(Path('synthetic'), Path('marker'), 123, (), ())
    monkeypatch.setattr(guard, '_check', lambda: None)
    monkeypatch.setattr(guard, '_read', lambda: PREFIX + b'R')
    admission = child._LocalAdmission(guard)
    adapter = child._LocalManifestQuery('codex-lead-1', 'synthetic', 'a' * 64,
        b'synthetic-policy', 'b' * 64, 'c' * 64, 1, 0, 1, admission=admission)
    admission.bind_owner(adapter)
    statuses = []
    original_status = child._LocalManifestQuery.status
    def observed_status(self):
        value = original_status(self)
        statuses.append((value, self._cancel.is_set(), self._thread.is_alive()))
        return value
    monkeypatch.setattr(child._LocalManifestQuery, 'status', observed_status)
    release, prepare_started, starting_written = threading.Event(), threading.Event(), threading.Event()
    def prepare(self):
        prepare_started.set()
        assert release.wait(3)
        if startup_failed:
            with self._lock:
                self._state = 'HOLD'
                self._start_failure = {'reason': 'OWNER_START', 'exception_type': 'ValueError'}
    monkeypatch.setattr(child._LocalManifestQuery, '_prepare', prepare)
    monkeypatch.setattr(child, 'create_local_manifest_query', lambda **kwargs: adapter)
    class Input:
        def read(self, size):
            assert prepare_started.wait(3)
            assert starting_written.wait(3)
            return b''
    class Output(io.BytesIO):
        def write(self, data):
            if b'"state":"STARTING"' in bytes(data): starting_written.set()
            if b'"state":"HOLD"' in bytes(data): release.set()
            return super().write(data)
    output = Output()
    code = _run_synthetic_owner_bounded(lambda: child._local_candidate_owner_entry(control_stream=Input(), output_stream=output,
        bundle_root='synthetic', expected_manifest_sha256='a' * 64,
        policy_sha256='b' * 64, approval_reference_sha256='c' * 64, _admission=admission),
        release_events=(release, prepare_started, starting_written))
    states = [json.loads(line)['state'] for line in output.getvalue().splitlines()]
    assert code == (2 if startup_failed else 0), (states, statuses)
    assert states == ['STARTING', 'HOLD', 'STOPPED']


@pytest.mark.parametrize(('reason', 'exception_type', 'expected'), [
    ('NONE', 'NONE', False), ('UNKNOWN', 'UNKNOWN', None),
    ('WORKER_SETUP', 'ValueError', True), ('BUSY', 'UNKNOWN', None)])
def test_completed_adapter_start_failure_evidence_is_tristate(reason, exception_type, expected):
    adapter = child._LocalManifestQuery('codex-lead-1', 'synthetic', 'a' * 64,
        b'synthetic-policy', 'b' * 64, 'c' * 64, 1, 0, 1)
    class Owner:
        def start_diagnostic(self):
            return {'reason': reason, 'exception_type': exception_type, 'authority': 'none'}
    adapter._owner = Owner()
    assert adapter.start_failure_observed() is expected


@WINDOWS
def test_held_reservation_rejects_second_handle_truncation(tmp_path):
    root = _seed(tmp_path / 'synthetic-audit')
    marker = root / MARKER
    guard = child._reserve_local_owner(root)
    opened = False
    try:
        try:
            fd = os.open(marker, os.O_WRONLY | os.O_TRUNC | getattr(os, 'O_BINARY', 0))
        except OSError:
            pass
        else:
            opened = True
            os.close(fd)
        assert not opened
        assert guard._read() == PREFIX + b'R'
        guard.release_clean(service_stopped=True, owner_stopped=True,
                            owner_owned=False, control_stopped=True)
    finally:
        guard.abandon()


@WINDOWS
@pytest.mark.parametrize('mutation', ['write', 'unlink', 'rename'])
def test_held_reservation_rejects_other_handle_mutation(tmp_path, mutation):
    root = _seed(tmp_path / 'synthetic-audit')
    marker = root / MARKER
    guard = child._reserve_local_owner(root)
    try:
        with pytest.raises(OSError):
            if mutation == 'write':
                fd = os.open(marker, os.O_WRONLY | getattr(os, 'O_BINARY', 0))
                try: os.write(fd, b'X')
                finally: os.close(fd)
            elif mutation == 'unlink':
                marker.unlink()
            else:
                marker.rename(root / 'synthetic-other-marker')
        assert guard._read() == PREFIX + b'R'
        guard.release_clean(service_stopped=True, owner_stopped=True,
                            owner_owned=False, control_stopped=True)
        assert marker.read_bytes() == PREFIX + b'C'
    finally:
        guard.abandon()


@WINDOWS
def test_reservation_refuses_existing_writable_handle_without_mutating_marker(tmp_path):
    root = _seed(tmp_path / 'synthetic-audit')
    marker = root / MARKER
    fd = os.open(marker, os.O_WRONLY | getattr(os, 'O_BINARY', 0))
    try:
        with pytest.raises(child.LaunchRefused):
            child._reserve_local_owner(root)
        assert marker.read_bytes() == PREFIX + b'C'
    finally:
        os.close(fd)


@WINDOWS
@pytest.mark.parametrize('condition', ['missing', 'reserved', 'invalid', 'locked', 'unreadable'])
@pytest.mark.parametrize('diagnostics', ['off', 'v1', 'v2'])
def test_reservation_refusal_is_fixed_and_never_provisions(tmp_path, monkeypatch, condition, diagnostics):
    root = tmp_path / 'synthetic-audit'
    root.mkdir()
    marker = root / MARKER
    before = {'reserved': PREFIX + b'R', 'invalid': PREFIX + b'X',
              'locked': PREFIX + b'C', 'unreadable': PREFIX + b'C'}.get(condition)
    if before is not None:
        marker.write_bytes(before)
    monkeypatch.setattr(child, '_AUDIT_ROOT', root)
    reads = []
    if condition == 'locked':
        import msvcrt
        original_open, original_read = child._open_owned_marker, child.os.read
        marker_fds = []
        def tracked_open(path):
            fd = original_open(path)
            if Path(path) == marker: marker_fds.append(fd)
            return fd
        def tracked_read(fd, size):
            if fd in marker_fds:
                reads.append(True)
                raise AssertionError('marker bytes must not be read without lock')
            return original_read(fd, size)
        monkeypatch.setattr(child, '_open_owned_marker', tracked_open)
        monkeypatch.setattr(child.os, 'read', tracked_read)
        monkeypatch.setattr(msvcrt, 'locking', lambda *args: (_ for _ in ()).throw(OSError('private-lock-marker')))
        def forbidden_read(self):
            reads.append(True)
            raise AssertionError('marker must not be read without lock')
        monkeypatch.setattr(child._LocalOwnerReservation, '_read', forbidden_read)
    if condition == 'unreadable':
        monkeypatch.setattr(child, '_open_owned_marker', lambda *args, **kw: (_ for _ in ()).throw(PermissionError('private-open-marker')))
    output = io.BytesIO()
    result = child._guarded_local_entry(child._local_candidate_owner_entry, output_stream=output,
        expected_manifest_sha256='a' * 64, include_start_diagnostics=diagnostics == 'v1',
        include_worker_stage_diagnostics=diagnostics == 'v2')
    assert result == 2
    assert not reads
    if condition == 'locked':
        assert len(marker_fds) == 1
    assert (marker.read_bytes() if marker.exists() else None) == before
    assert b'private-' not in output.getvalue() and b'READY' not in output.getvalue()
    if diagnostics == 'off':
        assert output.getvalue() == b''
    else:
        status_wire = (b'{"authority":"none","caller_authentication":"UNKNOWN",'
            b'"index_refresh":"NOT_SCHEDULED","manifest_sha256":"' + b'a' * 64 +
            b'","schema":"wd.mind-local-owner-status.v1",'
            b'"source_authentication":"UNKNOWN","state":"HOLD"}\n')
        reason = {'missing': 'ADMISSION_MISSING', 'reserved': 'ADMISSION_RESERVED',
                  'locked': 'ADMISSION_LOCK_UNAVAILABLE'}.get(condition, 'ADMISSION')
        diagnostic_wire = (b'{"authority":"none","exception_type":"LaunchRefused",'
            b'"reason":"' + reason.encode('ascii') + b'","schema":"wd.mind-local-owner-start-diagnostic.' +
            diagnostics.encode('ascii') + b'"')
        if diagnostics == 'v2':
            diagnostic_wire += b',"worker_exit_code":null,"worker_exit_stage_hint":"UNKNOWN"'
        assert output.getvalue() == status_wire + diagnostic_wire + b'}\n'
        status, detail = map(json.loads, output.getvalue().splitlines())
        assert status == {'schema':'wd.mind-local-owner-status.v1', 'state':'HOLD',
            'manifest_sha256':'a' * 64, 'authority':'none', 'caller_authentication':'UNKNOWN',
            'source_authentication':'UNKNOWN', 'index_refresh':'NOT_SCHEDULED'}
        expected = {'schema':'wd.mind-local-owner-start-diagnostic.' + diagnostics,
            'reason':reason, 'exception_type':'LaunchRefused', 'authority':'none'}
        if diagnostics == 'v2':
            expected.update(worker_exit_stage_hint='UNKNOWN', worker_exit_code=None)
        assert detail == expected


@WINDOWS
@pytest.mark.parametrize('fail_at', ['write', 'partial', 'flush'])
def test_reservation_diagnostic_write_failure_still_refuses(tmp_path, monkeypatch, fail_at):
    root = tmp_path / 'synthetic-audit'
    root.mkdir()
    monkeypatch.setattr(child, '_AUDIT_ROOT', root)
    class BrokenOutput:
        def __init__(self): self.data = b''
        def write(self, data):
            if fail_at == 'write' or (fail_at == 'partial' and self.data):
                raise OSError('private-output-marker')
            take = 8 if fail_at == 'partial' else len(data)
            self.data += bytes(data[:take])
            return take
        def flush(self):
            if fail_at == 'flush': raise OSError('private-output-marker')
    output = BrokenOutput()
    assert child._guarded_local_entry(child._local_candidate_owner_entry, output_stream=output,
        expected_manifest_sha256='a' * 64, include_start_diagnostics=True) == 2
    assert b'private-output-marker' not in output.data and b'READY' not in output.data
    assert not (root / MARKER).exists()


@WINDOWS
def test_reservation_diagnostic_accepts_successful_short_writes(tmp_path, monkeypatch):
    root = tmp_path / 'synthetic-audit'
    root.mkdir()
    monkeypatch.setattr(child, '_AUDIT_ROOT', root)
    class ShortOutput(io.BytesIO):
        def write(self, data): return super().write(data[:3])
    output = ShortOutput()
    assert child._guarded_local_entry(child._local_candidate_owner_entry, output_stream=output,
        expected_manifest_sha256='a' * 64, include_start_diagnostics=True) == 2
    status, detail = map(json.loads, output.getvalue().splitlines())
    assert status['state'] == 'HOLD' and detail['reason'] == 'ADMISSION_MISSING'
    assert detail['exception_type'] == 'LaunchRefused'
    assert not (root / MARKER).exists()


@WINDOWS
@pytest.mark.parametrize('cancel', [KeyboardInterrupt, GeneratorExit, asyncio.CancelledError])
def test_reservation_diagnostic_preserves_cancellation(tmp_path, monkeypatch, cancel):
    root = tmp_path / 'synthetic-audit'
    root.mkdir()
    monkeypatch.setattr(child, '_AUDIT_ROOT', root)
    class CancelOutput:
        def write(self, data): raise cancel('synthetic-private-cancellation')
    with pytest.raises(cancel, match='synthetic-private-cancellation'):
        child._guarded_local_entry(child._local_candidate_owner_entry, output_stream=CancelOutput(),
            expected_manifest_sha256='a' * 64, include_start_diagnostics=True)
    assert not (root / MARKER).exists()


@WINDOWS
def test_missing_or_invalid_guard_refuses_without_creating_or_clearing(tmp_path):
    root = tmp_path / 'audit'
    root.mkdir()
    with pytest.raises(child.LaunchRefused):
        child._reserve_local_owner(root)
    assert not (root / MARKER).exists()
    for value in (b'', PREFIX, PREFIX + b'R', PREFIX + b'X', PREFIX + b'CC'):
        (root / MARKER).write_bytes(value)
        with pytest.raises(child.LaunchRefused):
            child._reserve_local_owner(root)
        assert (root / MARKER).read_bytes() == value


@WINDOWS
def test_valid_clear_is_reserved_until_explicit_proven_cleanup(tmp_path):
    root = _seed(tmp_path / 'audit')
    guard = child._reserve_local_owner(root)
    assert guard._read() == PREFIX + b'R'
    with pytest.raises(child.LaunchRefused):
        child._reserve_local_owner(root)
    guard.release_clean(service_stopped=True, owner_stopped=True, owner_owned=False, control_stopped=True)
    assert (root / MARKER).read_bytes() == PREFIX + b'C'
    second = child._reserve_local_owner(root)
    second.release_clean(service_stopped=True, owner_stopped=True, owner_owned=False, control_stopped=True)


@WINDOWS
@pytest.mark.parametrize('evidence', [
    {'service_stopped': False, 'owner_stopped': True, 'owner_owned': False, 'control_stopped': True},
    {'service_stopped': True, 'owner_stopped': False, 'owner_owned': False, 'control_stopped': True},
    {'service_stopped': True, 'owner_stopped': True, 'owner_owned': True, 'control_stopped': True},
    {'service_stopped': True, 'owner_stopped': True, 'owner_owned': False, 'control_stopped': False},
    {'service_stopped': 1, 'owner_stopped': True, 'owner_owned': False, 'control_stopped': True},
])
def test_incomplete_cleanup_never_clears_reserved_guard(tmp_path, evidence):
    root = _seed(tmp_path / 'audit')
    guard = child._reserve_local_owner(root)
    with pytest.raises(child.LaunchRefused):
        guard.release_clean(**evidence)
    assert guard._read() == PREFIX + b'R'
    guard.release_clean(service_stopped=True, owner_stopped=True, owner_owned=False, control_stopped=True)


@WINDOWS
def test_cross_process_guard_prevents_second_owner_and_persists_clean_release(tmp_path):
    root = _seed(tmp_path / 'audit')
    script = r'''import sys
sys.path.insert(0,sys.argv[1])
from pathlib import Path
from tools import wd_mind_runtime_child as child
guard=child._reserve_local_owner(Path(sys.argv[2]))
print('RESERVED',flush=True)
assert sys.stdin.buffer.read(1)==b'S'
guard.release_clean(service_stopped=True,owner_stopped=True,owner_owned=False,control_stopped=True)
print('STOPPED',flush=True)
'''
    proc = subprocess.Popen([sys.executable, '-I', '-u', '-c', script, str(ROOT), str(root)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        assert proc.stdout.readline().strip() == b'RESERVED'
        with pytest.raises(child.LaunchRefused):
            child._reserve_local_owner(root)
        proc.stdin.write(b'S'); proc.stdin.flush(); proc.stdin.close()
        assert proc.wait(timeout=20) == 0
        assert proc.stdout.read().strip() == b'STOPPED'
        guard = child._reserve_local_owner(root)
        guard.release_clean(service_stopped=True, owner_stopped=True, owner_owned=False, control_stopped=True)
    finally:
        if proc.poll() is None:
            # Test processes only: request graceful EOF; never terminate/kill.
            if proc.stdin is not None and not proc.stdin.closed:
                proc.stdin.close()


@WINDOWS
def test_owner_crash_marker_stays_held_without_timeout_clear(tmp_path):
    root = _seed(tmp_path / 'audit')
    script = r'''import sys
sys.path.insert(0,sys.argv[1])
from pathlib import Path
from tools import wd_mind_runtime_child as child
child._reserve_local_owner(Path(sys.argv[2]))
raise RuntimeError('synthetic interruption')
'''
    proc = subprocess.Popen([sys.executable, '-I', '-c', script, str(ROOT), str(root)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert proc.wait(timeout=20) != 0
    assert (root / MARKER).read_bytes() == PREFIX + b'R'
    with pytest.raises(child.LaunchRefused):
        child._reserve_local_owner(root)


@WINDOWS
def test_other_process_cannot_write_locked_state_byte(tmp_path):
    root = _seed(tmp_path / 'audit')
    guard = child._reserve_local_owner(root)
    script = r'''import os,sys
fd=None
try:
    fd=os.open(sys.argv[1],os.O_RDWR|os.O_BINARY)
    os.lseek(fd,int(sys.argv[2]),os.SEEK_SET)
    os.write(fd,b'C')
except PermissionError:
    print('BLOCKED')
else:
    print('WRITTEN')
finally:
    if fd is not None: os.close(fd)
'''
    result = subprocess.run([sys.executable, '-I', '-c', script, str(root / MARKER), str(len(PREFIX))],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, check=True,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert result.stdout.strip() == b'BLOCKED'
    assert guard._read() == PREFIX + b'R'
    guard.release_clean(service_stopped=True, owner_stopped=True, owner_owned=False, control_stopped=True)


@WINDOWS
@pytest.mark.parametrize('entry_name,args', [
    ('_local_candidate_owner_entry', ['--mind-local-candidate-owner', '--policy-sha256', 'b'*64,
        '--approval-reference-sha256', 'c'*64]),
])
@pytest.mark.parametrize('outcome', ['success', 'clean_refused', 'refused', 'exception'])
def test_fixed_cli_reserves_before_entry_and_only_clears_on_proven_success(
        tmp_path, monkeypatch, entry_name, args, outcome):
    root = _seed(tmp_path / 'audit')
    monkeypatch.setattr(child, '_AUDIT_ROOT', root)
    monkeypatch.setenv('WD_REBOOT_EXPECTED_MANIFEST_HASH', 'a'*64)
    seen = []
    def entry(**kwargs):
        # This point precedes the adapter start inside the real entry.
        seen.append('entered')
        with pytest.raises(child.LaunchRefused):
            child._reserve_local_owner(root)
        if outcome == 'exception':
            raise RuntimeError('synthetic failure')
        if outcome in ('success', 'clean_refused'):
            kwargs['_cleanup_evidence']['clean'] = True
        return 0 if outcome == 'success' else 2
    monkeypatch.setattr(child, entry_name, entry)
    assert child._fixed_child_main(args) == (0 if outcome == 'success' else 2)
    assert seen == ['entered']
    assert (root / MARKER).read_bytes() == PREFIX + (b'C' if outcome in ('success', 'clean_refused') else b'R')


@WINDOWS
def test_invalid_initial_input_leaves_slot_available_for_next_caller(tmp_path, monkeypatch):
    root = _seed(tmp_path / 'audit')
    monkeypatch.setattr(child, '_AUDIT_ROOT', root)
    assert child._guarded_local_entry(child._local_query_entry,
        control_stream=io.BytesIO(b''), output_stream=io.BytesIO(), bundle_root='synthetic',
        expected_manifest_sha256='a'*64, lane='codex-lead-1', policy_sha256='b'*64,
        approval_reference_sha256='c'*64) == 2
    assert (root / MARKER).read_bytes() == PREFIX + b'C'
    guard = child._reserve_local_owner(root)
    guard.release_clean(service_stopped=True, owner_stopped=True, owner_owned=False, control_stopped=True)


@WINDOWS
def test_hardlinked_marker_refuses_without_changes(tmp_path):
    root = _seed(tmp_path / 'audit')
    os.link(root / MARKER, root / 'alias')
    with pytest.raises(child.LaunchRefused):
        child._reserve_local_owner(root)
    assert (root / MARKER).read_bytes() == PREFIX + b'C'


@WINDOWS
def test_changed_marker_never_clears(tmp_path):
    root = _seed(tmp_path / 'audit')
    guard = child._reserve_local_owner(root)
    os.lseek(guard.fd, len(PREFIX), os.SEEK_SET)
    os.write(guard.fd, b'X')
    with pytest.raises(child.LaunchRefused):
        guard.release_clean(service_stopped=True, owner_stopped=True, owner_owned=False, control_stopped=True)
    guard.abandon()
    assert (root / MARKER).read_bytes() == PREFIX + b'X'
    with pytest.raises(child.LaunchRefused):
        child._reserve_local_owner(root)


@WINDOWS
def test_reparse_ancestor_refuses(tmp_path):
    real = _seed(tmp_path / 'audit')
    alias = tmp_path / 'alias'
    try:
        os.symlink(real, alias, target_is_directory=True)
    except OSError as exc:
        pytest.skip('symlink privilege unavailable: ' + type(exc).__name__)
    with pytest.raises(child.LaunchRefused):
        child._reserve_local_owner(alias)
    assert (real / MARKER).read_bytes() == PREFIX + b'C'


@WINDOWS
@pytest.mark.parametrize('state', [b'C', b'R'])
def test_closed_local_query_does_not_reserve_or_change_marker(tmp_path, monkeypatch, state):
    root = _seed(tmp_path / 'audit', state=state)
    monkeypatch.setattr(child, '_AUDIT_ROOT', root)
    def unexpected(*args, **kwargs):
        raise AssertionError('closed route attempted admission or source I/O')
    monkeypatch.setattr(child, '_reserve_local_owner', unexpected)
    monkeypatch.setattr(child, '_LocalAdmission', unexpected)
    monkeypatch.setattr(child, 'create_local_manifest_query', unexpected)
    class Untouchable:
        def read(self, *args):
            raise AssertionError('closed route attempted input')
    output = io.BytesIO()
    assert child._guarded_local_entry(child._local_query_entry,
        control_stream=Untouchable(), output_stream=output, bundle_root='synthetic',
        expected_manifest_sha256='a'*64, lane='codex-lead-1', policy_sha256='b'*64,
        approval_reference_sha256='c'*64) == 2
    assert (root / MARKER).read_bytes() == PREFIX + state
    assert output.getvalue() == b''
