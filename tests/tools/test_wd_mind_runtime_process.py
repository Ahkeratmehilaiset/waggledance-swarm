"""Synthetic-only runtime-process preflight tests; no real model is loaded."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

import pytest

from tools import wd_mind_runtime_process as preflight


def _candidate_frame(request):
    return {'schema':'wd.mind-pipe-candidates.v1', 'nonce':request['nonce'],
            'lane':request['lane'], 'scope':request['scope'], 'status':'CANDIDATES',
            'candidates':[{'ref_sha256':'a' * 64, 'doc_id_sha256':'b' * 64,
                           'source_event_sha256':'c' * 64}]}


def _candidate_request(lane='codex-lead-1', scope='own'):
    return {'schema':'wd.mind-pipe-request.v1', 'query_id':'startup_work_overview',
            'nonce':'d' * 64, 'lane':lane, 'scope':scope}


class _SyntheticCandidateOwner:
    def __init__(self):
        self.requests = []

    def candidates(self, request):
        self.requests.append(request)
        return _candidate_frame(request)


def _located_request(lane='codex-lead-1', scope='own'):
    return {**_candidate_request(lane, scope), 'schema':'wd.mind-pipe-request.v2'}


def _located_frame(request):
    result = _candidate_frame(request)
    result['schema'] = 'wd.mind-pipe-candidates.v2'
    result['candidates'][0].update(source_offset=123, source_length=456, chunk_index=2)
    return result


def test_candidate_ipc_service_is_off_by_default():
    with pytest.raises(preflight.RuntimePreflightRefused):
        preflight.create_local_candidate_service(owner=_SyntheticCandidateOwner(), pipe_name='x')


def test_synthetic_failed_service_retains_hold_if_listener_close_is_unproved():
    class Listener:
        def accept(self): raise OSError('synthetic accept failure')
        def close(self): raise OSError('synthetic close failure')
    service = preflight._LocalCandidateService(_SyntheticCandidateOwner(), 'synthetic')
    service._listener = Listener()
    service._thread = threading.Thread(target=service._serve)
    service._thread.start()
    service._thread.join(3)
    assert not service._thread.is_alive() and service._serve_done.is_set()
    assert service.status()['state'] == 'HOLD'
    assert service.close(timeout=0)['state'] == 'HOLD'


def test_synthetic_service_retains_hold_after_accept_ownership_becomes_unknown():
    class Listener:
        def accept(self): raise OSError('synthetic accept failure')
        def close(self): pass
    service = preflight._LocalCandidateService(_SyntheticCandidateOwner(), 'synthetic')
    service._listener = Listener()
    service._thread = threading.Thread(target=service._serve)
    service._thread.start()
    service._thread.join(3)
    assert not service._thread.is_alive() and service._serve_done.is_set()
    assert service._listener_closed is True
    assert service.close(timeout=0)['state'] == 'HOLD'


def test_synthetic_failed_service_retains_hold_until_wake_thread_exits():
    class Connection:
        def poll(self, timeout): raise KeyboardInterrupt()
        def close(self): pass
    class Listener:
        def accept(self): return Connection()
        def close(self): pass
    service = preflight._LocalCandidateService(_SyntheticCandidateOwner(), 'synthetic')
    service._listener = Listener()
    service._thread = threading.Thread(target=service._serve)
    service._thread.start()
    service._thread.join(3)
    assert not service._thread.is_alive() and service._serve_done.is_set()
    service._wake_done = threading.Event()
    release = threading.Event()
    def wake():
        service._wake_done.set()
        release.wait(3)
    service._wake_thread = threading.Thread(target=wake)
    service._wake_thread.start()
    try:
        assert service._wake_done.wait(3) and service._wake_thread.is_alive()
        assert service.close(timeout=0)['state'] == 'HOLD'
    finally:
        release.set()
        service._wake_thread.join(3)
    assert service.close(timeout=0)['state'] == 'FAILED'


def test_synthetic_failed_service_retains_hold_until_serve_thread_exits(monkeypatch):
    class Connection:
        def poll(self, timeout): raise KeyboardInterrupt()
        def close(self): pass
    class Listener:
        def accept(self): return Connection()
        def close(self): pass
    service = preflight._LocalCandidateService(_SyntheticCandidateOwner(), 'synthetic')
    service._listener = Listener()
    exit_gate = threading.Event()
    def serve():
        service._serve()
        exit_gate.wait(3)
    monkeypatch.setattr(service, '_wake', lambda: None)
    service._thread = threading.Thread(target=serve)
    service._thread.start()
    try:
        assert service._serve_done.wait(3) and service._thread.is_alive()
        assert service.close(timeout=0)['state'] == 'HOLD'
    finally:
        exit_gate.set()
        service._thread.join(3)
        if service._wake_thread is not None:
            service._wake_thread.join(3)
    assert service.close(timeout=0)['state'] == 'FAILED'


def test_concurrent_close_cannot_use_previous_wake_generation(monkeypatch):
    service = preflight._LocalCandidateService(object(), 'synthetic')
    serve_gate, at_old_wait, release_old_wait = threading.Event(), threading.Event(), threading.Event()
    start_enter, allow_start = threading.Event(), threading.Event()
    class PausingEvent:
        def __init__(self): self.inner = threading.Event()
        def is_set(self): return self.inner.is_set()
        def set(self): self.inner.set()
        def wait(self, timeout=None):
            at_old_wait.set()
            assert release_old_wait.wait(3)
            return self.inner.is_set()
    old_done = PausingEvent()
    def serve():
        serve_gate.wait(3)
        service._listener_closed = True
        service._serve_done.set()
    service._thread = threading.Thread(target=serve, name='synthetic-serve')
    service._thread.start()
    service._wake_done = old_done
    service._wake_thread = threading.Thread(target=lambda: old_done.inner.wait(3), name='synthetic-old-wake')
    old_wake = service._wake_thread
    old_wake.start()
    service._failed = True
    monkeypatch.setattr(service, '_wake', lambda: None)
    original_start = threading.Thread.start
    def gated_start(thread):
        if thread.name == 'mind-local-wake':
            start_enter.set()
            assert allow_start.wait(3)
        return original_start(thread)
    monkeypatch.setattr(threading.Thread, 'start', gated_start)
    results, a_done = {}, threading.Event()
    def close_a():
        try: results['a'] = service.close(timeout=0)
        finally: a_done.set()
    def close_b(): results['b'] = service.close(timeout=0)
    a = threading.Thread(target=close_a, name='synthetic-close-a')
    b = threading.Thread(target=close_b, name='synthetic-close-b')
    try:
        original_start(a)
        assert at_old_wait.wait(3)
        old_done.set()
        old_wake.join(3)
        assert not old_wake.is_alive()
        original_start(b)
        assert start_enter.wait(3)
        serve_gate.set()
        service._thread.join(3)
        assert service._serve_done.is_set() and not service._thread.is_alive()
        release_old_wait.set()
        assert a_done.wait(2), 'concurrent close blocked instead of returning HOLD'
        assert results['a']['state'] == 'HOLD', 'terminal state before next wake starts'
    finally:
        serve_gate.set()
        release_old_wait.set()
        allow_start.set()
        a.join(3)
        if b.ident is not None: b.join(3)
        service._thread.join(3)
        old_wake.join(3)
        if service._wake_thread is not None and service._wake_thread.ident is not None:
            service._wake_thread.join(3)
    assert not a.is_alive() and not b.is_alive()
    assert results['a']['state'] == 'HOLD'


def test_synthetic_failed_listener_creation_cleanup_does_not_claim_stopped(monkeypatch):
    import multiprocessing.connection as connection
    class Listener:
        def close(self): raise OSError('synthetic close failure')
    monkeypatch.setattr(connection, 'Listener', lambda **kwargs: Listener())
    def refuse_thread(*args, **kwargs): raise RuntimeError('synthetic thread construction failure')
    monkeypatch.setattr(preflight.threading, 'Thread', refuse_thread)
    service = preflight._LocalCandidateService(_SyntheticCandidateOwner(), 'synthetic')
    with pytest.raises(OSError):
        service.start()
    assert service.close(timeout=0)['state'] == 'HOLD'


@pytest.mark.parametrize('error', [OSError, KeyboardInterrupt])
def test_synthetic_listener_constructor_exception_retains_unknown_ownership(monkeypatch, error):
    import multiprocessing.connection as connection
    retained = []
    def listener(**kwargs):
        retained.append(object())
        raise error('synthetic resource acquired before constructor failure')
    monkeypatch.setattr(connection, 'Listener', listener)
    service = preflight._LocalCandidateService(_SyntheticCandidateOwner(), 'synthetic')
    expected = preflight.RuntimePreflightRefused if error is OSError else error
    with pytest.raises(expected):
        service.start()
    assert len(retained) == 1
    assert service.status()['state'] == 'HOLD'
    assert service.close(timeout=0)['state'] == 'HOLD'


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows named-pipe admission')
def test_candidate_duplicate_pipe_denial_has_no_owned_listener():
    import multiprocessing.connection as connection
    import uuid

    name = r'\\.\pipe\mind-pr1-test-' + uuid.uuid4().hex
    first = connection.Listener(address=name, family='AF_PIPE', authkey=None)
    try:
        service = preflight._LocalCandidateService(_SyntheticCandidateOwner(), name)
        with pytest.raises(preflight.RuntimePreflightRefused):
            service.start()
        assert service.status()['state'] == 'STOPPED'
        assert service.close(timeout=0)['state'] == 'STOPPED'
    finally:
        first.close()


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows error semantics')
def test_constructor_denial_from_unknown_origin_remains_hold(monkeypatch):
    import multiprocessing.connection as connection

    retained = []
    def listener(**kwargs):
        retained.append(object())
        raise PermissionError(13, 'synthetic post-allocation denial', None, 5)
    monkeypatch.setattr(connection, 'Listener', listener)
    service = preflight._LocalCandidateService(_SyntheticCandidateOwner(), 'synthetic')
    with pytest.raises(preflight.RuntimePreflightRefused):
        service.start()
    assert retained and service.status()['state'] == 'HOLD'
    assert service.close(timeout=0)['state'] == 'HOLD'


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows named-pipe implementation')
def test_post_allocation_constructor_denial_remains_hold(monkeypatch):
    import multiprocessing.connection as connection

    retained = object()
    monkeypatch.setattr(connection.PipeListener, '_new_handle', lambda self, first: retained)
    def post_allocation(*args):
        raise PermissionError(13, 'synthetic post-allocation denial', None, 5)
    monkeypatch.setattr(connection.util, 'sub_debug', post_allocation)
    service = preflight._LocalCandidateService(_SyntheticCandidateOwner(), 'synthetic')
    with pytest.raises(preflight.RuntimePreflightRefused):
        service.start()
    assert service.status()['state'] == 'HOLD'
    assert service.close(timeout=0)['state'] == 'HOLD'


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows named-pipe implementation')
def test_untrusted_python_create_named_pipe_denial_remains_hold(monkeypatch):
    import multiprocessing.connection as connection

    def fake_create(*args):
        raise PermissionError(13, 'synthetic native-call substitute', None, 5)
    monkeypatch.setattr(connection._winapi, 'CreateNamedPipe', fake_create)
    service = preflight._LocalCandidateService(_SyntheticCandidateOwner(), 'synthetic')
    with pytest.raises(preflight.RuntimePreflightRefused):
        service.start()
    assert service.status()['state'] == 'HOLD'
    assert service.close(timeout=0)['state'] == 'HOLD'


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows named-pipe implementation')
def test_replaced_constructor_cannot_clear_unknown_ownership(monkeypatch):
    import multiprocessing.connection as connection
    import uuid

    name = r'\\.\pipe\mind-pr1-test-' + uuid.uuid4().hex
    first = connection.Listener(address=name, family='AF_PIPE', authkey=None)
    retained = []
    def replaced_init(self, address, backlog=None):
        retained.append(object())
        self._address = address
        self._new_handle(first=True)
    monkeypatch.setattr(connection.PipeListener, '__init__', replaced_init)
    try:
        service = preflight._LocalCandidateService(_SyntheticCandidateOwner(), name)
        with pytest.raises(preflight.RuntimePreflightRefused):
            service.start()
        assert retained and service.status()['state'] == 'HOLD'
        assert service.close(timeout=0)['state'] == 'HOLD'
    finally:
        first.close()


@pytest.mark.parametrize('close_error', [OSError, KeyboardInterrupt])
def test_synthetic_service_retains_connection_when_close_is_unproved(close_error):
    service = preflight._LocalCandidateService(_SyntheticCandidateOwner(), 'synthetic')
    evidence = {'connection_close_attempted': False, 'listener_closed': False}
    class Connection:
        def close(self):
            evidence['connection_close_attempted'] = True
            raise close_error('synthetic connection close failure')
    connection = Connection()
    class Listener:
        def accept(self):
            service._stop.set()
            return connection
        def close(self): evidence['listener_closed'] = True
    service._listener = Listener()
    service._thread = threading.Thread(target=service._serve)
    service._thread.start()
    service._thread.join(3)
    assert not service._thread.is_alive() and service._serve_done.is_set()
    assert all(evidence.values())
    assert service.close(timeout=0)['state'] == 'HOLD'
    assert service._uncertain_connection is connection


@pytest.mark.parametrize('close_error', [OSError, KeyboardInterrupt])
def test_synthetic_idle_connection_close_failure_stops_accepting(close_error):
    class Owner:
        calls = 0
        def candidates(self, request):
            self.calls += 1
            return {}
    owner = Owner()
    service = preflight._LocalCandidateService(owner, 'synthetic')
    class Connection:
        def poll(self, timeout): return False
        def close(self): raise close_error('synthetic connection close failure')
    connection = Connection()
    class Listener:
        accepts = 0
        closed = False
        def accept(self):
            self.accepts += 1
            if self.accepts > 1:
                raise AssertionError('accepted another connection after a close failure')
            return connection
        def close(self): self.closed = True
    listener = Listener()
    service._listener = listener
    service._serve()
    assert listener.accepts == 1
    assert listener.closed
    assert owner.calls == 0
    assert service._uncertain_connection is connection
    assert service.status()['state'] == 'HOLD'


def test_synthetic_service_clean_connection_close_allows_stopped():
    service = preflight._LocalCandidateService(_SyntheticCandidateOwner(), 'synthetic')
    evidence = {'connection_closed': False, 'listener_closed': False}
    class Connection:
        def close(self): evidence['connection_closed'] = True
    class Listener:
        def accept(self):
            service._stop.set()
            return Connection()
        def close(self): evidence['listener_closed'] = True
    service._listener = Listener()
    service._thread = threading.Thread(target=service._serve)
    service._thread.start()
    service._thread.join(3)
    assert not service._thread.is_alive() and service._serve_done.is_set()
    assert all(evidence.values())
    assert service.close(timeout=0)['state'] == 'STOPPED'


def test_synthetic_wake_skips_client_after_listener_closed(monkeypatch):
    import multiprocessing.connection as connection_module
    service = preflight._LocalCandidateService(_SyntheticCandidateOwner(), 'synthetic')
    accepting, accept_release, wake_entered, wake_release = (threading.Event() for _ in range(4))
    calls = []
    class Connection:
        def close(self): pass
    class Listener:
        def accept(self):
            accepting.set()
            assert accept_release.wait(3)
            return Connection()
        def close(self): pass
    service._listener = Listener()
    service._thread = threading.Thread(target=service._serve)
    original_wake = service._wake
    def gated_wake():
        wake_entered.set()
        assert wake_release.wait(3)
        original_wake()
    monkeypatch.setattr(service, '_wake', gated_wake)
    def client(**kwargs):
        calls.append(kwargs)
        raise OSError('synthetic listener closed')
    monkeypatch.setattr(connection_module, 'Client', client)
    service._thread.start()
    try:
        assert accepting.wait(3)
        assert service.close(timeout=0)['state'] == 'HOLD'
        assert wake_entered.wait(3)
        accept_release.set()
        service._thread.join(3)
        assert not service._thread.is_alive() and service._listener_closed
        wake_release.set()
        service._wake_thread.join(3)
        assert not service._wake_thread.is_alive()
        assert calls == []
        assert service.close(timeout=0)['state'] == 'STOPPED'
    finally:
        accept_release.set()
        wake_release.set()
        service._thread.join(3)
        if service._wake_thread is not None:
            service._wake_thread.join(3)


@pytest.mark.parametrize('method', ['status', 'close'])
def test_terminal_state_checks_wake_ownership_after_thread_exit(method):
    service = preflight._LocalCandidateService(_SyntheticCandidateOwner(), 'synthetic')
    class DeadServe:
        def is_alive(self): return False
    class RacingWake:
        checks = 0
        def is_alive(self):
            self.checks += 1
            # close observes a wake in flight twice before its final check;
            # status performs only its single final check.
            if method == 'close' and self.checks < 3: return True
            service._wake_ownership_unknown = True
            return False
        def join(self, timeout=None): pass
    service._thread = DeadServe()
    service._serve_done.set()
    service._listener_closed = True
    service._wake_done = threading.Event()
    service._wake_done.set()
    service._wake_thread = RacingWake()
    result = service.status() if method == 'status' else service.close(timeout=0)
    assert result['state'] == 'HOLD'


@pytest.mark.parametrize('first', ['listener', 'client'])
def test_synthetic_listener_wake_lifecycle_is_serialized_without_blocking_close(monkeypatch, first):
    import multiprocessing.connection as connection_module
    service = preflight._LocalCandidateService(_SyntheticCandidateOwner(), 'synthetic')
    entered, release = threading.Event(), threading.Event()
    listener_called, client_called = threading.Event(), threading.Event()
    class Listener:
        def close(self):
            listener_called.set()
            if first == 'listener':
                entered.set()
                assert release.wait(3)
    class Connection:
        def close(self): pass
    def client(**kwargs):
        client_called.set()
        if first == 'client':
            entered.set()
            assert release.wait(3)
        return Connection()
    monkeypatch.setattr(connection_module, 'Client', client)
    service._listener = Listener()
    service._stop.set()
    service._thread = threading.Thread(target=service._serve)
    service._wake_done = threading.Event()
    service._wake_thread = threading.Thread(target=service._wake_then_done, args=(service._wake_done,))
    t1, t2 = (service._thread, service._wake_thread) if first == 'listener' else (service._wake_thread, service._thread)
    t1.start()
    try:
        assert entered.wait(3)
        t2.start()
        # close must not wait for the synthetic resource operation.
        start = time.monotonic()
        assert service.close(timeout=0)['state'] == 'HOLD'
        assert time.monotonic() - start < 1
        if first == 'listener':
            assert not client_called.wait(0.1)
        else:
            assert not listener_called.wait(0.1)
        release.set()
        t1.join(3)
        t2.join(3)
        assert not t1.is_alive() and not t2.is_alive()
        assert listener_called.is_set()
        assert client_called.is_set() == (first == 'client')
        assert service.close(timeout=0)['state'] == 'STOPPED'
    finally:
        release.set()
        t1.join(3)
        if t2.ident is not None:
            t2.join(3)


@pytest.mark.parametrize('outcome', ['clean', 'close_failure', 'constructor_failure'])
def test_synthetic_wake_connection_closure_evidence(monkeypatch, outcome):
    import multiprocessing.connection as connection_module
    service = preflight._LocalCandidateService(_SyntheticCandidateOwner(), 'synthetic')
    class Connection:
        closed = False
        def close(self):
            if outcome == 'close_failure': raise OSError('synthetic close failure')
            self.closed = True
    connection = Connection()
    def client(**kwargs):
        if outcome == 'constructor_failure': raise OSError('synthetic constructor failure')
        return connection
    monkeypatch.setattr(connection_module, 'Client', client)
    service._wake()
    if outcome == 'clean':
        assert connection.closed
        assert service.close(timeout=0)['state'] == 'STOPPED'
    else:
        assert service.close(timeout=0)['state'] == 'HOLD'
        assert service._wake_ownership_unknown is True
        if outcome == 'close_failure':
            assert service._uncertain_wake_connection is connection


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes only')
def test_candidate_ipc_service_uncertain_thread_start_stays_owned(monkeypatch):
    import multiprocessing.connection as connection
    class Listener:
        closed = False
        def close(self): self.closed = True
    listener = Listener()
    class UncertainThread:
        def __init__(self, *args, **kwargs): self.target = kwargs['target']
        def start(self): raise KeyboardInterrupt()
        def is_alive(self): return False
        def fire(self): self.target()
    monkeypatch.setattr(connection, 'Listener', lambda **kwargs: listener)
    monkeypatch.setattr(preflight.threading, 'Thread', UncertainThread)
    owner = _SyntheticCandidateOwner()
    name = preflight.local_candidate_pipe_name(manifest_sha256='a' * 64)
    service = preflight.create_local_candidate_service(enabled=True, owner=owner, pipe_name=name)
    with pytest.raises(KeyboardInterrupt):
        service.start()
    assert service.status()['state'] == 'HOLD'
    assert service.close(timeout=0)['state'] == 'HOLD'
    assert service._owner is owner and not listener.closed
    service._thread.fire()
    assert service.status()['state'] == 'STOPPED' and listener.closed


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes only')
def test_candidate_ipc_client_uncertain_thread_start_stays_owned(monkeypatch):
    import multiprocessing.connection as connection
    real_thread = threading.Thread
    class UncertainThread:
        def __init__(self, *args, **kwargs): self.target, self.ident = kwargs['target'], None
        def start(self): raise KeyboardInterrupt()
        def is_alive(self): return False
        def fire(self):
            done = threading.Event()
            def target():
                try: self.target()
                finally: done.set()
            actual = real_thread(target=target)
            pending = None
            try:
                actual.start()
            except BaseException as exc:
                pending = exc
            # Even repeated cancellations cannot discard an uncertain start.
            import time
            while True:
                try:
                    if done.is_set() and actual.ident is not None:
                        actual.join(1)
                        if not actual.is_alive(): break
                except BaseException as exc:
                    if pending is None: pending = exc
                try:
                    time.sleep(0.05)
                except BaseException as exc:
                    if pending is None: pending = exc
            self.ident = actual.ident
            if pending is not None: raise pending
    monkeypatch.setattr(preflight.threading, 'Thread', UncertainThread)
    def refused(**kwargs): raise OSError('synthetic refusal')
    monkeypatch.setattr(connection, 'Client', refused)
    name = preflight.local_candidate_pipe_name(manifest_sha256='a' * 64)
    query = preflight.create_local_candidate_query(enabled=True, pipe_name=name, request=_candidate_request())
    with pytest.raises(KeyboardInterrupt):
        query.start()
    assert query.status()['state'] == 'HOLD'
    assert query.wait(timeout=0)['status'] == 'HOLD'
    assert query.close(timeout=0)['state'] == 'HOLD'
    query._thread.fire()
    assert query.status()['state'] == 'STOPPED'
    assert query.wait(timeout=0)['status'] == 'TIMEOUT'
    assert query.close(timeout=0)['state'] == 'STOPPED'


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes only')
def test_candidate_ipc_closed_unstarted_client_cannot_start():
    name = preflight.local_candidate_pipe_name(manifest_sha256='a' * 64)
    query = preflight.create_local_candidate_query(enabled=True, pipe_name=name, request=_candidate_request())
    assert query.close(timeout=0)['state'] == 'STOPPED'
    assert query.status()['state'] == 'STOPPED'
    with pytest.raises(preflight.RuntimePreflightRefused):
        query.start()


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes only')
def test_candidate_ipc_wake_creation_failure_can_retry(monkeypatch):
    name = preflight.local_candidate_pipe_name(manifest_sha256=_hash(os.urandom(32)))
    service = preflight.create_local_candidate_service(enabled=True, owner=_SyntheticCandidateOwner(), pipe_name=name)
    service.start()
    original = threading.Thread
    calls = []
    def factory(*args, **kwargs):
        if kwargs.get('name') == 'mind-local-wake' and not calls:
            calls.append('construction_failed_before_start')
            raise RuntimeError('synthetic pre-start construction failure')
        return original(*args, **kwargs)
    monkeypatch.setattr(preflight.threading, 'Thread', factory)
    try:
        assert service.close(timeout=0)['state'] == 'HOLD'
        assert service.close(timeout=5)['state'] == 'STOPPED'
    finally:
        monkeypatch.setattr(preflight.threading, 'Thread', original)
        service.close(timeout=5)


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes only')
def test_candidate_ipc_completed_unsuccessful_wake_can_retry(monkeypatch):
    name = preflight.local_candidate_pipe_name(manifest_sha256=_hash(os.urandom(32)))
    service = preflight.create_local_candidate_service(enabled=True, owner=_SyntheticCandidateOwner(), pipe_name=name)
    service.start()
    original = service._wake
    calls = []
    def wake():
        calls.append('wake')
        if len(calls) > 1: original()
    monkeypatch.setattr(service, '_wake', wake)
    try:
        assert service.close(timeout=0)['state'] == 'HOLD'
        service._wake_thread.join(5)
        assert service.close(timeout=5)['state'] == 'STOPPED'
        assert len(calls) == 2
    finally:
        monkeypatch.setattr(service, '_wake', original)
        if service._thread.is_alive():
            from multiprocessing.connection import Client
            with Client(address=name, family='AF_PIPE', authkey=None): pass
            service._thread.join(5)


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes only')
@pytest.mark.parametrize('error', [KeyboardInterrupt, RuntimeError])
def test_candidate_ipc_uncertain_wake_start_stays_hold_after_service_exit(monkeypatch, error):
    from multiprocessing.connection import Client
    name = preflight.local_candidate_pipe_name(manifest_sha256=_hash(os.urandom(32)))
    service = preflight.create_local_candidate_service(enabled=True, owner=_SyntheticCandidateOwner(), pipe_name=name)
    service.start()
    original = threading.Thread
    class UncertainWake:
        def __init__(self, *args, **kwargs): self.target = kwargs['target']
        def start(self): raise error()
        def is_alive(self): return False
        def fire(self): self.target()
    def factory(*args, **kwargs):
        return UncertainWake(*args, **kwargs) if kwargs.get('name') == 'mind-local-wake' else original(*args, **kwargs)
    monkeypatch.setattr(preflight.threading, 'Thread', factory)
    try:
        if error is KeyboardInterrupt:
            with pytest.raises(KeyboardInterrupt):
                service.close(timeout=0)
        else:
            assert service.close(timeout=0)['state'] == 'HOLD'
        with Client(address=name, family='AF_PIPE', authkey=None): pass
        service._thread.join(5)
        assert not service._thread.is_alive()
        assert service.status()['state'] == 'HOLD'
        assert service.close(timeout=0)['state'] == 'HOLD'
        service._wake_thread.fire()
        # The listener is already closed, so the late wake does not open a client.
        assert service._wake_ownership_unknown is False
        assert service.status()['state'] == 'STOPPED'
        assert service.close(timeout=0)['state'] == 'STOPPED'
    finally:
        monkeypatch.setattr(preflight.threading, 'Thread', original)
        if service._thread.is_alive():
            with Client(address=name, family='AF_PIPE', authkey=None): pass
            service._thread.join(5)


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes only')
def test_candidate_ipc_owner_baseexception_is_failed_not_stopped():
    class InterruptingOwner:
        def candidates(self, request): raise KeyboardInterrupt()
    name = preflight.local_candidate_pipe_name(manifest_sha256=_hash(os.urandom(32)))
    service = preflight.create_local_candidate_service(enabled=True, owner=InterruptingOwner(), pipe_name=name)
    service.start()
    query = preflight.create_local_candidate_query(enabled=True, pipe_name=name, request=_candidate_request())
    query.start()
    assert query.wait(timeout=5)['status'] == 'REFUSED'
    assert service.close(timeout=5)['state'] == 'FAILED'
    assert service.status()['state'] == 'FAILED'


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes only')
def test_candidate_ipc_serves_all_five_lanes_as_unverified_references():
    owner = _SyntheticCandidateOwner()
    name = preflight.local_candidate_pipe_name(manifest_sha256=_hash(os.urandom(32)))
    service = preflight.create_local_candidate_service(enabled=True, owner=owner, pipe_name=name)
    service.start()
    try:
        lanes = ('codex-lead-1', 'codex-tools-1', 'claude-rco-1', 'claude-rco-2', 'fable-5')
        for lane in lanes:
            scope = 'shared' if lane.startswith('claude-') else 'own'
            request = _candidate_request(lane, scope)
            query = preflight.create_local_candidate_query(enabled=True, pipe_name=name, request=request)
            query.start()
            result = query.wait(timeout=10)
            assert result['status'] == 'CANDIDATES'
            assert result['authority'] == 'none' and result['sender_authentication'] == 'UNKNOWN'
            assert result['source_verification'] == 'UNVERIFIED'
            assert json.loads(result['candidate_frame']) == _candidate_frame(request)
        assert len(owner.requests) == 5
    finally:
        assert service.close(timeout=5)['state'] == 'STOPPED'


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes only')
def test_candidate_ipc_five_simultaneous_lane_queries_close_cleanly():
    lanes = ('codex-lead-1', 'codex-tools-1', 'claude-rco-1', 'claude-rco-2', 'fable-5')
    release_owner = threading.Event()
    start_gate = threading.Barrier(6, timeout=10)
    started_gate = threading.Barrier(6, timeout=10)
    requests, queries, results, errors = {}, {}, {}, []

    class Owner:
        def candidates(self, request):
            if not release_owner.wait(10):
                raise TimeoutError('test owner not released')
            response = _candidate_frame(request)
            response['candidates'][0]['source_event_sha256'] = _hash(request['lane'].encode())
            return response

    name = preflight.local_candidate_pipe_name(manifest_sha256=_hash(os.urandom(32)))
    service = preflight.create_local_candidate_service(enabled=True, owner=Owner(), pipe_name=name)
    service.start()
    workers = []
    def run(lane):
        try:
            start_gate.wait()
            queries[lane].start()
            started_gate.wait()
            results[lane] = queries[lane].wait(timeout=30)
        except BaseException as exc:
            errors.append((lane, exc))

    try:
        for lane in lanes:
            request = _candidate_request(lane, 'shared' if lane.startswith('claude-') else 'own')
            request['nonce'] = _hash(('nonce:' + lane).encode())
            requests[lane] = request
            queries[lane] = preflight.create_local_candidate_query(enabled=True, pipe_name=name, request=request)
            worker = threading.Thread(target=run, args=(lane,))
            workers.append(worker)
            worker.start()
        start_gate.wait()
        started_gate.wait()
        release_owner.set()
        for worker in workers:
            worker.join(timeout=35)
        assert not errors and all(not worker.is_alive() for worker in workers)
        assert set(results) == set(lanes)
        for lane in lanes:
            result = results[lane]
            assert result['status'] == 'CANDIDATES'
            frame = json.loads(result['candidate_frame'])
            assert frame['lane'] == lane and frame['nonce'] == requests[lane]['nonce']
            assert frame['candidates'][0]['source_event_sha256'] == _hash(lane.encode())
    finally:
        release_owner.set()
        for worker in workers:
            worker.join(timeout=35)
        for query in queries.values():
            assert query.close(timeout=10)['state'] == 'STOPPED'
        assert service.close(timeout=10)['state'] == 'STOPPED'


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes only')
def test_candidate_ipc_rejects_text_and_retains_owner_on_timeout(monkeypatch):
    gate = threading.Event()
    entered = threading.Event()
    class SlowOwner:
        def candidates(self, request):
            entered.set()
            gate.wait(10)
            return {**_candidate_frame(request), 'text': 'must not be returned'}
    name = preflight.local_candidate_pipe_name(manifest_sha256=_hash(os.urandom(32)))
    service = preflight.create_local_candidate_service(enabled=True, owner=SlowOwner(), pipe_name=name)
    service.start()
    # This case is blocked inside candidates(), so STOP needs no wake client.
    # Exercise clean owner release separately from wake-constructor uncertainty.
    monkeypatch.setattr(service, '_wake', lambda: None)
    query = preflight.create_local_candidate_query(enabled=True, pipe_name=name, request=_candidate_request())
    try:
        query.start()
        assert entered.wait(5)
        assert query.wait(timeout=0.01)['status'] == 'TIMEOUT'
        assert service.close(timeout=0.01)['state'] == 'HOLD'
    finally:
        gate.set()
        assert service.close(timeout=5)['state'] == 'STOPPED'
    assert query.wait(timeout=5)['status'] == 'TIMEOUT'


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes only')
@pytest.mark.parametrize('fault', ['text', 'nonce', 'lane', 'source_hash'])
def test_candidate_ipc_rejects_untrusted_or_malformed_reply(fault):
    class BadOwner:
        def candidates(self, request):
            result = _candidate_frame(request)
            if fault == 'text': result['text'] = 'must not be returned'
            elif fault == 'nonce': result['nonce'] = 'e' * 64
            elif fault == 'lane': result['lane'] = 'fable-5'
            else: result['candidates'][0]['source_event_sha256'] = 'not-a-hash'
            return result
    name = preflight.local_candidate_pipe_name(manifest_sha256=_hash(os.urandom(32)))
    service = preflight.create_local_candidate_service(enabled=True, owner=BadOwner(), pipe_name=name)
    service.start()
    try:
        query = preflight.create_local_candidate_query(enabled=True, pipe_name=name, request=_candidate_request())
        query.start()
        result = query.wait(timeout=10)
        assert result['status'] == 'REFUSED' and result['candidate_frame'] is None
        assert result['source_verification'] == 'UNVERIFIED'
    finally:
        assert service.close(timeout=5)['state'] == 'STOPPED'


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes only')
def test_candidate_ipc_serves_an_independent_python_process():
    name = preflight.local_candidate_pipe_name(manifest_sha256=_hash(os.urandom(32)))
    service = preflight.create_local_candidate_service(enabled=True, owner=_SyntheticCandidateOwner(), pipe_name=name)
    service.start()
    script = '''
import json, sys
from tools import wd_mind_runtime_process as p
request = {'schema':'wd.mind-pipe-request.v1','query_id':'startup_work_overview',
           'nonce':'d'*64,'lane':'fable-5','scope':'own'}
query = p.create_local_candidate_query(enabled=True, pipe_name=sys.argv[1], request=request)
query.start()
result = query.wait(timeout=10)
print(json.dumps({'status':result['status'],'verified':result['source_verification'],
                  'frame':json.loads(result['candidate_frame'])}))
'''
    try:
        completed = subprocess.run([sys.executable, '-c', script, name], cwd=str(Path(__file__).resolve().parents[2]),
                                   capture_output=True, text=True, timeout=20, check=True)
        result = json.loads(completed.stdout)
        assert result == {'status':'CANDIDATES', 'verified':'UNVERIFIED',
                          'frame':_candidate_frame(_candidate_request('fable-5', 'own'))}
    finally:
        assert service.close(timeout=5)['state'] == 'STOPPED'


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes only')
def test_candidate_ipc_v2_locations_remain_unverified_and_v1_is_unchanged():
    class Owner(_SyntheticCandidateOwner):
        def located_candidates(self, request): return _located_frame(request)
    name = preflight.local_candidate_pipe_name(manifest_sha256=_hash(os.urandom(32)))
    service = preflight.create_local_candidate_service(enabled=True, owner=Owner(), pipe_name=name)
    service.start()
    try:
        for request, expected in ((_candidate_request(), _candidate_frame), (_located_request(), _located_frame)):
            query = preflight.create_local_candidate_query(enabled=True, pipe_name=name, request=request)
            query.start()
            answer = query.wait(timeout=10)
            assert answer['status'] == 'CANDIDATES' and answer['source_verification'] == 'UNVERIFIED'
            assert json.loads(answer['candidate_frame']) == expected(request)
    finally:
        assert service.close(timeout=5)['state'] == 'STOPPED'


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes only')
@pytest.mark.parametrize('field,value', [
    ('source_offset', -1), ('source_offset', True), ('source_offset', 2**63),
    ('source_length', 0), ('source_length', 4194305), ('source_length', '10'),
    ('chunk_index', -1), ('chunk_index', False), ('chunk_index', 4194305),
])
def test_candidate_ipc_v2_refuses_bad_untrusted_locations(field, value):
    class Owner(_SyntheticCandidateOwner):
        def located_candidates(self, request):
            result = _located_frame(request)
            result['candidates'][0][field] = value
            return result
    name = preflight.local_candidate_pipe_name(manifest_sha256=_hash(os.urandom(32)))
    service = preflight.create_local_candidate_service(enabled=True, owner=Owner(), pipe_name=name)
    service.start()
    try:
        query = preflight.create_local_candidate_query(enabled=True, pipe_name=name, request=_located_request())
        query.start()
        assert query.wait(timeout=10)['status'] == 'REFUSED'
    finally:
        assert service.close(timeout=5)['state'] == 'STOPPED'


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes only')
def test_candidate_ipc_duplicate_keys_and_binary_frames_are_not_deserialized():
    from multiprocessing.connection import Client
    owner = _SyntheticCandidateOwner()
    name = preflight.local_candidate_pipe_name(manifest_sha256=_hash(os.urandom(32)))
    service = preflight.create_local_candidate_service(enabled=True, owner=owner, pipe_name=name)
    service.start()
    try:
        for raw in (b'{"schema":"one","schema":"two"}', b'\x80\x04N.'):
            with Client(address=name, family='AF_PIPE', authkey=None) as connection:
                connection.send_bytes(raw)
                try:
                    assert connection.poll(5)
                    with pytest.raises((EOFError, BrokenPipeError)):
                        connection.recv_bytes()
                except BrokenPipeError:
                    pass  # Windows can signal closure at poll or at recv.
        assert owner.requests == []
    finally:
        assert service.close(timeout=5)['state'] == 'STOPPED'


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()










def _fixture_pinned_verifier_bytes(raw):
    """Neutralize checkout line endings only for this synthetic bundle."""
    canonical = raw.replace(b'\r\n', b'\n')
    if _hash(canonical) != preflight._PACKAGED_VERIFIER_SHA256:
        raise ValueError('synthetic_verifier_pin_mismatch')
    return canonical


def test_synthetic_verifier_checkout_translation_never_changes_the_pin():
    source = Path(__file__).resolve().parents[2] / 'tools/wd_mind_runtime_manifest.py'
    canonical = _fixture_pinned_verifier_bytes(source.read_bytes())
    assert _hash(canonical) == preflight._PACKAGED_VERIFIER_SHA256
    assert _fixture_pinned_verifier_bytes(canonical.replace(b'\n', b'\r\n')) == canonical
    with pytest.raises(ValueError, match='synthetic_verifier_pin_mismatch'):
        _fixture_pinned_verifier_bytes(canonical + b'# altered\n')


@pytest.fixture
def candidate(tmp_path):
    root = tmp_path / "runtime"
    files = []
    for path, raw in (("site/synthetic.py", b"# inert synthetic site\n"),
                      ("model/config.json", b"{}"), ("assets/code/read.py", b"# inert\n")):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        files.append({"path": path, "size": len(raw), "sha256": _hash(raw)})
    generation, revision = "a" * 40, "b" * 40
    data = json.dumps({"schema": "wd.q4-runtime-manifest.v1", "generation": generation,
                       "revision": revision, "files": files}, sort_keys=True).encode()
    manifest = tmp_path / "runtime-manifest.json"
    manifest.write_bytes(data)
    interpreter = tmp_path / "synthetic-interpreter.exe"
    bootstrap = tmp_path / "synthetic-bootstrap.py"
    bundle = tmp_path / "synthetic-bundle"
    verifier = bundle / "tools-bootstrap/tools/wd_mind_runtime_manifest.py"
    verifier.parent.mkdir(parents=True)
    interpreter.write_bytes(b"synthetic interpreter, never execute")
    bootstrap.write_bytes(b"raise AssertionError('must never run')\n")
    verifier.write_bytes(_fixture_pinned_verifier_bytes(
        (Path(__file__).resolve().parents[2] / "tools/wd_mind_runtime_manifest.py").read_bytes()))
    deployment = json.dumps({"schema_version": 1, "files": {
        "tools-bootstrap/tools/wd_mind_runtime_manifest.py": _hash(verifier.read_bytes()).upper()}},
        sort_keys=True).encode()
    (bundle / "deployment-manifest.json").write_bytes(deployment)
    return {"interpreter": interpreter, "interpreter_sha256": _hash(interpreter.read_bytes()),
            "bootstrap": bootstrap, "bootstrap_sha256": _hash(bootstrap.read_bytes()),
            "bundle_root": bundle, "deployment_manifest_sha256": _hash(deployment),
            "verifier": verifier, "verifier_sha256": _hash(verifier.read_bytes()),
            "runtime_root": root, "runtime_manifest": manifest, "runtime_manifest_sha256": _hash(data),
            "generation": generation, "revision": revision}


def _refuses(candidate):
    with pytest.raises(preflight.RuntimePreflightRefused, match="^runtime_process_preflight_refused$") as exc:
        preflight.verify_runtime_process_preflight(**candidate)
    assert exc.value.__context__ is None


def test_verified_preflight_never_runs_process_opens_network_or_loads_model(candidate, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("process started"))
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("network opened"))
    root = candidate["runtime_root"].parent
    files_before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    before = {name for name in sys.modules if name.split(".")[0] in {"torch", "faiss", "sentence_transformers"}}
    result = preflight.verify_runtime_process_preflight(**candidate)
    assert result["status"] == "VERIFIED_PREFLIGHT_ONLY"
    assert result["process_started"] is False and result["model_loaded"] is False
    assert result["native_closure"] == result["network_isolation"] == result["same_user_race"] == "UNKNOWN"
    assert result["file_count"] == 3 and result["runtime_manifest_sha256"] == candidate["runtime_manifest_sha256"]
    assert before == {name for name in sys.modules if name.split(".")[0] in {"torch", "faiss", "sentence_transformers"}}
    assert files_before == {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("key", ["interpreter_sha256", "bootstrap_sha256", "verifier_sha256", "deployment_manifest_sha256",
                                  "runtime_manifest_sha256", "generation", "revision"])
def test_wrong_independently_pinned_value_fails_closed(candidate, monkeypatch, key):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("process started"))
    candidate[key] = "c" * (40 if key in {"generation", "revision"} else 64)
    _refuses(candidate)


@pytest.mark.parametrize("target", ["interpreter", "bootstrap", "verifier"])
def test_missing_or_mutated_code_fails_before_execution(candidate, monkeypatch, target):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("process started"))
    candidate[target].unlink()
    _refuses(candidate)


def test_unapproved_verifier_import_never_executes(candidate, tmp_path, monkeypatch):
    marker = tmp_path / "must-not-exist"
    malicious = "from pathlib import Path\nPath(%r).write_text('import executed')\n" % str(marker)
    candidate["verifier"].write_text(malicious, encoding="utf-8")
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("process started"))
    _refuses(candidate)
    assert not marker.exists()


def test_self_pinned_unapproved_verifier_still_never_executes(candidate, tmp_path, monkeypatch):
    marker = tmp_path / "must-not-exist"
    malicious = ("from pathlib import Path\nPath(%r).write_text('unauthorized')\n" % str(marker)).encode()
    candidate["verifier"].write_bytes(malicious)
    # Matching attacker-supplied bytes and digest are not a trust anchor.
    candidate["verifier_sha256"] = _hash(malicious)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("process started"))
    _refuses(candidate)
    assert not marker.exists()


def test_self_pinned_manifest_and_verifier_still_refuse_unapproved_code(candidate, tmp_path):
    marker = tmp_path / "must-not-exist"
    malicious = ("from pathlib import Path\nPath(%r).write_text('unauthorized')\n" % str(marker)).encode()
    candidate["verifier"].write_bytes(malicious)
    candidate["verifier_sha256"] = _hash(malicious)
    manifest = json.dumps({"schema_version": 1, "files": {
        "tools-bootstrap/tools/wd_mind_runtime_manifest.py": _hash(malicious)}}, sort_keys=True).encode()
    (candidate["bundle_root"] / "deployment-manifest.json").write_bytes(manifest)
    candidate["deployment_manifest_sha256"] = _hash(manifest)
    _refuses(candidate)
    assert not marker.exists()


def test_verifier_outside_pinned_bundle_is_refused(candidate, tmp_path):
    outsider = tmp_path / "outside-verifier.py"
    outsider.write_bytes(candidate["verifier"].read_bytes())
    candidate["verifier"] = outsider
    _refuses(candidate)


def test_duplicate_deployment_manifest_keys_refuse(candidate):
    manifest = b'{"schema_version":1,"schema_version":1,"files":{}}'
    (candidate["bundle_root"] / "deployment-manifest.json").write_bytes(manifest)
    candidate["deployment_manifest_sha256"] = _hash(manifest)
    _refuses(candidate)


@pytest.mark.parametrize("mutation", ["missing", "changed", "extra"])
def test_runtime_tree_mutation_fails_closed(candidate, mutation):
    model = candidate["runtime_root"] / "model/config.json"
    if mutation == "missing":
        model.unlink()
    elif mutation == "changed":
        model.write_bytes(b"[]")  # same length, different content
    else:
        (candidate["runtime_root"] / "model/extra.json").write_bytes(b"{}")
    _refuses(candidate)


def test_syntax_error_in_pinned_verifier_is_fixed_refusal(candidate):
    bad = b"this is not valid python !\n"
    candidate["verifier"].write_bytes(bad)
    candidate["verifier_sha256"] = _hash(bad)
    _refuses(candidate)


@pytest.mark.parametrize('entrypoint', ['reserve_guardian_attempt','create_candidate_pipe',
    'query_candidate_pipe','create_candidate_service','start_manifest_guardian'])
def test_deferred_guardian_and_named_pipe_endpoints_never_start(monkeypatch, entrypoint):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('process started'))
    monkeypatch.setattr(socket, 'socket', lambda *a, **k: pytest.fail('network opened'))
    with pytest.raises(preflight.RuntimePreflightRefused, match='^runtime_process_preflight_refused$'):
        getattr(preflight, entrypoint)(enabled=True)
    assert not hasattr(preflight, '_GuardianNative')
    assert not hasattr(preflight, '_CandidatePipe')
    assert not hasattr(preflight, '_CandidateService')
    assert not hasattr(preflight, '_ManifestGuardian')


def test_synthetic_idle_clean_close_allows_next_request():
    request = {'schema': 'wd.mind-pipe-request.v1', 'query_id': 'startup_work_overview',
               'nonce': 'd' * 64, 'lane': 'codex-lead-1', 'scope': 'own'}
    expected_reply = {
        'schema': 'wd.mind-pipe-candidates.v1', 'nonce': request['nonce'],
        'lane': request['lane'], 'scope': request['scope'], 'status': 'CANDIDATES',
        'candidates': [{'ref_sha256': 'a' * 64, 'doc_id_sha256': 'b' * 64,
                        'source_event_sha256': 'c' * 64}],
    }

    class Owner:
        def __init__(self):
            self.requests = []
        def candidates(self, received):
            self.requests.append(received)
            return expected_reply

    owner = Owner()
    service = preflight._LocalCandidateService(owner, 'synthetic')
    calls = []
    replies = []

    class Idle:
        def poll(self, timeout):
            calls.append(('idle_poll', timeout))
            return False
        def close(self):
            calls.append(('idle_close',))

    class Request:
        def poll(self, timeout):
            calls.append(('request_poll', timeout))
            return True
        def recv_bytes(self, max_bytes):
            calls.append(('request_recv', max_bytes))
            return json.dumps(request).encode('utf-8')
        def send_bytes(self, payload):
            replies.append(json.loads(payload))
            service._stop.set()
        def close(self):
            calls.append(('request_close',))

    class Listener:
        def __init__(self):
            self.connections = iter((Idle(), Request()))
            self.accepts = 0
            self.closed = False
        def accept(self):
            self.accepts += 1
            return next(self.connections)
        def close(self):
            self.closed = True

    listener = Listener()
    service._listener = listener
    service._serve()
    assert listener.accepts == 2
    assert listener.closed
    assert calls[0] == ('idle_poll', 1)
    assert calls[1] == ('idle_close',)
    assert calls[2] == ('request_poll', 1)
    assert calls[-1] == ('request_close',)
    assert owner.requests == [request]
    assert replies == [expected_reply]
    assert service._uncertain_connection is None
    assert service.status()['state'] == 'STOPPED'
