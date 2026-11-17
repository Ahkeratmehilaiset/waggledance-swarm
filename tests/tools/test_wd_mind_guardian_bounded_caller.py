"""Synthetic candidate transport; no native pipe, model, or live process."""

import json
import os
import threading
import time

import pytest

from tools import wd_mind_guardian_bounded_caller as module


MANIFEST, SESSION, NONCE = 'a' * 64, 'b' * 64, 'c' * 64
LANE = 'codex-lead-1'


def _finished(caller):
    assert caller._finished.wait(2)
    with caller._lock:
        pass


def _stop(caller):
    deadline = time.monotonic() + 2
    while caller.request_stop()['state'] == 'BUSY' and time.monotonic() < deadline:
        time.sleep(0.001)
    caller._thread.join(timeout=2)
    assert not caller._thread.is_alive()


def test_caller_defaults_off_and_requires_bootstrap():
    assert module.create_caller(manifest_sha256=MANIFEST).start()['state'] == 'OFF'
    with pytest.raises(module.CallerRefused):
        module.create_caller(enabled=True, manifest_sha256=MANIFEST)


def test_stop_is_not_accepted_when_result_gate_is_busy():
    caller = module._BoundedCaller(True, MANIFEST)
    assert caller._lock.acquire(blocking=False)
    try:
        assert caller.request_stop()['state'] == 'BUSY'
        assert not caller._stop.is_set()
    finally:
        caller._lock.release()


def test_nonce_registry_accepts_1024_then_refuses_new_nonce():
    # Exercise submit directly; clear each ticket as if a quiescent pump had
    # consumed it. No thread, pipe, source data or model is started here.
    caller = module._BoundedCaller(True, MANIFEST)
    caller._state = 'READY'
    assert caller.attach(lane=LANE, session=SESSION)
    for n in range(1024):
        nonce = f'{n:064x}'
        assert caller.submit(lane=LANE, session=SESSION, nonce=nonce, budget_seconds=1)['state'] == 'PENDING'
        assert nonce in caller._used_nonces
        with caller._lock:
            caller._ticket = None
            caller._state = 'READY'
    assert len(caller._used_nonces) == 1024
    nonce_1025 = f'{1024:064x}'
    assert caller.submit(lane=LANE, session=SESSION, nonce=nonce_1025, budget_seconds=1)['state'] == 'REFUSED'
    assert nonce_1025 not in caller._used_nonces
    assert len(caller._used_nonces) == 1024


def test_invalid_and_blocked_submits_do_not_consume_nonce():
    caller = module._BoundedCaller(True, MANIFEST)
    caller._state = 'READY'
    assert caller.attach(lane=LANE, session=SESSION)
    for invalid_budget in (0, 10**1000, -(10**1000), float('nan'), float('inf'), True, '1'):
        assert caller.submit(lane=LANE, session=SESSION, nonce=NONCE,
                             budget_seconds=invalid_budget)['state'] == 'REFUSED'
        assert NONCE not in caller._used_nonces
    assert caller._lock.acquire(blocking=False)
    try:
        assert caller.submit(lane=LANE, session=SESSION, nonce=NONCE, budget_seconds=1)['state'] == 'WITHOUT_MIND'
        assert NONCE not in caller._used_nonces
    finally:
        caller._lock.release()
    caller._state = 'BUSY'
    assert caller.submit(lane=LANE, session=SESSION, nonce=NONCE, budget_seconds=1)['state'] == 'WITHOUT_MIND'
    assert NONCE not in caller._used_nonces
    caller._state = 'READY'
    assert caller.submit(lane=LANE, session=SESSION, nonce=NONCE, budget_seconds=1)['state'] == 'PENDING'
    assert NONCE in caller._used_nonces


@pytest.fixture
def transport(monkeypatch):
    import multiprocessing.connection as connection
    entered, release = threading.Event(), threading.Event()
    requests = []

    class FakeConnection:
        def __enter__(self): return self
        def __exit__(self, *unused): return False
        def send_bytes(self, raw): requests.append(json.loads(raw))
        def poll(self, timeout):
            entered.set()
            return release.wait(3)
        def recv_bytes(self, limit):
            request = requests[-1]
            return json.dumps({'schema':'wd.mind-pipe-candidates.v2', 'nonce':request['nonce'],
                'lane':request['lane'], 'scope':request['scope'], 'status':'CANDIDATES',
                'candidates':[{'ref_sha256':'d'*64, 'doc_id_sha256':'e'*64,
                    'source_event_sha256':'f'*64, 'source_offset':0, 'source_length':1, 'chunk_index':0}]}).encode()

    monkeypatch.setattr(connection, 'Client', lambda **kwargs: FakeConnection())
    monkeypatch.setattr(module, '_BOOTSTRAP_PROVEN', True)
    yield entered, release, requests
    release.set()


@pytest.mark.skipif(os.name != 'nt', reason='candidate pipe factory is Windows-only')
def test_caller_binds_candidate_to_session_and_requires_source_verification(transport):
    entered, release, requests = transport
    caller = module.create_caller(enabled=True, manifest_sha256=MANIFEST)
    try:
        assert caller.start()['state'] == 'READY'
        assert caller.attach(lane=LANE, session=SESSION)
        assert caller.submit(lane=LANE, session=SESSION, nonce=NONCE, budget_seconds=2)['state'] == 'PENDING'
        assert entered.wait(2)
        release.set()
        _finished(caller)
        result = caller.poll(lane=LANE, session=SESSION, nonce=NONCE)
        assert result['state'] == 'CANDIDATES_UNVERIFIED'
        assert result['authority'] == 'none' and result['source_verification'] == 'UNVERIFIED'
        assert result['manifest_sha256'] == MANIFEST and result['session'] == SESSION
        assert result['candidates'][0]['source_event_sha256'] == 'f' * 64
        assert requests[0]['lane'] == LANE and requests[0]['nonce'] == NONCE
        assert caller.poll(lane=LANE, session=SESSION, nonce=NONCE)['state'] == 'WITHOUT_MIND'
    finally:
        release.set()
        _stop(caller)


@pytest.mark.skipif(os.name != 'nt', reason='candidate pipe factory is Windows-only')
def test_late_reply_and_detach_do_not_release_blocked_caller(transport):
    entered, release, _ = transport
    caller = module.create_caller(enabled=True, manifest_sha256=MANIFEST)
    try:
        caller.start()
        assert caller.attach(lane=LANE, session=SESSION)
        assert caller.submit(lane=LANE, session=SESSION, nonce=NONCE, budget_seconds=0.02)['state'] == 'PENDING'
        assert entered.wait(2)
        time.sleep(0.03)
        assert caller.poll(lane=LANE, session=SESSION, nonce=NONCE)['state'] == 'WITHOUT_MIND'
        assert caller.detach(lane=LANE, session=SESSION)
        assert caller.request_stop()['state'] == 'HOLD'
        assert caller._thread.is_alive()
    finally:
        release.set()
        caller._thread.join(timeout=2)
        assert not caller._thread.is_alive()
    assert caller.poll(lane=LANE, session=SESSION, nonce=NONCE)['state'] == 'WITHOUT_MIND'


@pytest.mark.skipif(os.name != 'nt', reason='candidate pipe factory is Windows-only')
def test_expired_completed_result_does_not_block_a_later_query(transport):
    entered, release, _ = transport
    caller = module.create_caller(enabled=True, manifest_sha256=MANIFEST)
    try:
        caller.start()
        assert caller.attach(lane=LANE, session=SESSION)
        release.set()
        assert caller.submit(lane=LANE, session=SESSION, nonce=NONCE, budget_seconds=0.05)['state'] == 'PENDING'
        assert entered.wait(2)
        _finished(caller)
        time.sleep(0.06)
        assert caller.poll(lane=LANE, session=SESSION, nonce=NONCE)['state'] == 'WITHOUT_MIND'
        assert caller.submit(lane=LANE, session=SESSION, nonce='1' * 64, budget_seconds=2)['state'] == 'PENDING'
        _finished(caller)
        assert caller.poll(lane=LANE, session=SESSION, nonce='1' * 64)['state'] == 'CANDIDATES_UNVERIFIED'
    finally:
        release.set()
        _stop(caller)


@pytest.mark.skipif(os.name != 'nt', reason='candidate pipe factory is Windows-only')
def test_detach_discards_old_reply_and_allows_new_session_after_query_finishes(transport):
    entered, release, _ = transport
    caller = module.create_caller(enabled=True, manifest_sha256=MANIFEST)
    new_session = '2' * 64
    try:
        caller.start()
        assert caller.attach(lane=LANE, session=SESSION)
        assert caller.submit(lane=LANE, session=SESSION, nonce=NONCE, budget_seconds=2)['state'] == 'PENDING'
        assert entered.wait(2)
        assert caller.detach(lane=LANE, session=SESSION)
        assert caller.attach(lane=LANE, session=new_session)
        assert caller.submit(lane=LANE, session=new_session, nonce='1' * 64, budget_seconds=2)['state'] == 'WITHOUT_MIND'
        release.set()
        _finished(caller)
        assert caller.poll(lane=LANE, session=SESSION, nonce=NONCE)['state'] == 'WITHOUT_MIND'
        assert caller.submit(lane=LANE, session=new_session, nonce='1' * 64, budget_seconds=2)['state'] == 'PENDING'
        _finished(caller)
        assert caller.poll(lane=LANE, session=new_session, nonce='1' * 64)['session'] == new_session
    finally:
        release.set()
        _stop(caller)


@pytest.mark.skipif(os.name != 'nt', reason='candidate pipe factory is Windows-only')
def test_nonce_cannot_be_reused_after_session_change(transport):
    entered, release, _ = transport
    caller = module.create_caller(enabled=True, manifest_sha256=MANIFEST)
    try:
        caller.start()
        assert caller.attach(lane=LANE, session=SESSION)
        release.set()
        assert caller.submit(lane=LANE, session=SESSION, nonce=NONCE, budget_seconds=2)['state'] == 'PENDING'
        assert entered.wait(2)
        _finished(caller)
        assert caller.poll(lane=LANE, session=SESSION, nonce=NONCE)['state'] == 'CANDIDATES_UNVERIFIED'
        assert caller.detach(lane=LANE, session=SESSION)
        assert caller.attach(lane=LANE, session='2' * 64)
        assert caller.submit(lane=LANE, session='2' * 64, nonce=NONCE, budget_seconds=2)['state'] == 'REFUSED'
    finally:
        release.set()
        _stop(caller)


@pytest.mark.skipif(os.name != 'nt', reason='candidate pipe factory is Windows-only')
def test_stop_before_ticket_dequeue_never_starts_query(transport, monkeypatch):
    _, release, requests = transport
    caller = module.create_caller(enabled=True, manifest_sha256=MANIFEST)
    entered, gate = threading.Event(), threading.Event()
    original_wait = caller._wake.wait
    def wait_before_dequeue(timeout=None):
        entered.set()
        gate.wait(2)
        return original_wait(timeout)
    monkeypatch.setattr(caller._wake, 'wait', wait_before_dequeue)
    try:
        caller.start()
        assert entered.wait(2) and caller.attach(lane=LANE, session=SESSION)
        assert caller.submit(lane=LANE, session=SESSION, nonce=NONCE, budget_seconds=2)['state'] == 'PENDING'
        assert caller.request_stop()['state'] == 'HOLD'
        gate.set()
        caller._thread.join(timeout=2)
        assert not caller._thread.is_alive() and caller.status()['state'] == 'STOPPED'
        assert requests == []
    finally:
        gate.set()
        release.set()
        _stop(caller)


@pytest.mark.skipif(os.name != 'nt', reason='candidate pipe factory is Windows-only')
def test_stop_during_inert_query_creation_prevents_query_start(transport, monkeypatch):
    from tools import wd_mind_runtime_process as process
    _, release, requests = transport
    entered, gate = threading.Event(), threading.Event()
    original = process.create_local_candidate_query
    def create(**kwargs):
        query = original(**kwargs)
        entered.set()
        gate.wait(2)
        return query
    monkeypatch.setattr(process, 'create_local_candidate_query', create)
    caller = module.create_caller(enabled=True, manifest_sha256=MANIFEST)
    try:
        caller.start()
        assert caller.attach(lane=LANE, session=SESSION)
        assert caller.submit(lane=LANE, session=SESSION, nonce=NONCE, budget_seconds=2)['state'] == 'PENDING'
        assert entered.wait(2)
        assert caller.request_stop()['state'] == 'HOLD'
        gate.set()
        caller._thread.join(timeout=2)
        assert not caller._thread.is_alive() and caller.status()['state'] == 'STOPPED'
        assert requests == []
    finally:
        gate.set()
        release.set()
        _stop(caller)


@pytest.mark.skipif(os.name != 'nt', reason='candidate pipe factory is Windows-only')
def test_stop_after_start_commit_retains_query_and_discards_result(transport, monkeypatch):
    from tools import wd_mind_runtime_process as process
    _, release, requests = transport
    entered, gate = threading.Event(), threading.Event()
    original = process._LocalCandidateQuery.start

    def start(query):
        entered.set()
        gate.wait(2)
        return original(query)

    monkeypatch.setattr(process._LocalCandidateQuery, 'start', start)
    caller = module.create_caller(enabled=True, manifest_sha256=MANIFEST)
    try:
        caller.start()
        assert caller.attach(lane=LANE, session=SESSION)
        assert caller.submit(lane=LANE, session=SESSION, nonce=NONCE, budget_seconds=2)['state'] == 'PENDING'
        assert entered.wait(2)
        with caller._lock:
            assert caller._launch_committed and caller._query is not None
        assert caller.request_stop()['state'] == 'HOLD'
        assert caller.poll(lane=LANE, session=SESSION, nonce=NONCE)['state'] == 'WITHOUT_MIND'
        release.set()
        gate.set()
        caller._thread.join(timeout=2)
        assert not caller._thread.is_alive() and caller.status()['state'] == 'STOPPED'
        assert len(requests) == 1
    finally:
        gate.set()
        release.set()
        _stop(caller)
