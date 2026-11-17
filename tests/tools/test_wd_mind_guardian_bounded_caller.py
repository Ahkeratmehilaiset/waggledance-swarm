"""Synthetic candidate transport; no native pipe, model, or live process."""

import json
import os
import threading
import time

import pytest

from tools import wd_mind_guardian_bounded_caller as module


MANIFEST, SESSION, NONCE = 'a' * 64, 'b' * 64, 'c' * 64
LANE = 'codex-lead-1'


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
        assert caller._finished.wait(2)
        result = caller.poll(lane=LANE, session=SESSION, nonce=NONCE)
        assert result['state'] == 'CANDIDATES_UNVERIFIED'
        assert result['authority'] == 'none' and result['source_verification'] == 'UNVERIFIED'
        assert result['manifest_sha256'] == MANIFEST and result['session'] == SESSION
        assert result['candidates'][0]['source_event_sha256'] == 'f' * 64
        assert requests[0]['lane'] == LANE and requests[0]['nonce'] == NONCE
        assert caller.poll(lane=LANE, session=SESSION, nonce=NONCE)['state'] == 'WITHOUT_MIND'
    finally:
        release.set()
        caller.request_stop()
        caller._thread.join(timeout=2)
        assert not caller._thread.is_alive()


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
        assert entered.wait(2) and caller._finished.wait(2)
        time.sleep(0.06)
        assert caller.poll(lane=LANE, session=SESSION, nonce=NONCE)['state'] == 'WITHOUT_MIND'
        assert caller.submit(lane=LANE, session=SESSION, nonce='1' * 64, budget_seconds=2)['state'] == 'PENDING'
        assert caller._finished.wait(2)
        assert caller.poll(lane=LANE, session=SESSION, nonce='1' * 64)['state'] == 'CANDIDATES_UNVERIFIED'
    finally:
        release.set()
        caller.request_stop()
        caller._thread.join(timeout=2)
        assert not caller._thread.is_alive()
