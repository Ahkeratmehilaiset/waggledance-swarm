"""Synthetic threads only; no child process, real source or policy data."""
import threading
import time
import io
import pytest
from tools import wd_bridge_mind_session as session
from tools import wd_mind_runtime_process as runtime


def _drain(thread, release, done):
    pending = None
    while True:
        try:
            release.set()
            if done.is_set() and thread.ident is not None:
                thread.join(1)
                if not thread.is_alive():
                    break
        except BaseException as exc:
            if pending is None: pending = exc
        try:
            time.sleep(.01)
        except BaseException as exc:
            if pending is None: pending = exc
    if pending is not None: raise pending


def _state(query=None):
    return {'result':b'synthetic-marker', 'resolver':object(), 'query':query,
            'abandoned':True, 'result_lock':threading.Lock(), 'worker_done':threading.Event()}


def _drain_all(owners):
    pending = None
    for thread, release, done in owners:
        try:
            _drain(thread, release, done)
        except BaseException as exc:
            if pending is None: pending = exc
    if pending is not None: raise pending


def test_reaper_keeps_live_owner_then_erases_after_proven_exit():
    entered, release = threading.Event(), threading.Event()
    def work():
        try:
            entered.set()
            release.wait()
        finally:
            state['worker_done'].set()
    worker = threading.Thread(target=work)
    state = _state()
    pending = (worker, state)
    with session._LOCAL_CANDIDATE_PENDING_LOCK:
        session._LOCAL_CANDIDATE_PENDING.append(pending)
    try:
        worker.start()
        assert entered.wait(5)
        session._reap_local_candidate_pending()
        assert pending in session._LOCAL_CANDIDATE_PENDING
    finally:
        _drain(worker, release, state['worker_done'])
    session._reap_local_candidate_pending()
    assert pending not in session._LOCAL_CANDIDATE_PENDING
    assert state['result'] is state['resolver'] is state['query'] is None


def test_reaper_keeps_ambiguous_unstarted_worker():
    worker = threading.Thread(target=lambda: None)
    state = _state()
    pending = (worker, state)
    with session._LOCAL_CANDIDATE_PENDING_LOCK:
        session._LOCAL_CANDIDATE_PENDING.append(pending)
    try:
        session._reap_local_candidate_pending()
        assert pending in session._LOCAL_CANDIDATE_PENDING
    finally:
        # Synthetic test knows no native start was attempted.
        with session._LOCAL_CANDIDATE_PENDING_LOCK:
            session._LOCAL_CANDIDATE_PENDING.remove(pending)


def test_reaper_keeps_identity_published_before_worker_started():
    class EarlyIdentity:
        ident=123
        def is_alive(self): return False
    state = _state()
    pending = (EarlyIdentity(), state)
    with session._LOCAL_CANDIDATE_PENDING_LOCK:
        session._LOCAL_CANDIDATE_PENDING.append(pending)
    try:
        session._reap_local_candidate_pending()
        assert pending in session._LOCAL_CANDIDATE_PENDING
    finally:
        with session._LOCAL_CANDIDATE_PENDING_LOCK:
            session._LOCAL_CANDIDATE_PENDING.remove(pending)


def test_reaper_keeps_uncertain_query_then_reaps_after_physical_exit():
    entered, release = threading.Event(), threading.Event()
    query = runtime._LocalCandidateQuery('synthetic', {})
    query_done = threading.Event()
    def work():
        try:
            query._done.set()
            entered.set()
            release.wait()
        finally:
            query_done.set()
    query._thread = threading.Thread(target=work)
    owner = threading.Thread(target=lambda: state['worker_done'].set())
    state = _state(query)
    pending = (owner, state)
    with session._LOCAL_CANDIDATE_PENDING_LOCK:
        session._LOCAL_CANDIDATE_PENDING.append(pending)
    owner_release = threading.Event()
    query_attempted = False
    try:
        owner.start()
        query_attempted = True
        query._thread.start()
        assert entered.wait(5)
        owner.join(5)
        session._reap_local_candidate_pending()
        assert pending in session._LOCAL_CANDIDATE_PENDING
    finally:
        _drain_all(([(query._thread, release, query_done)] if query_attempted else []) + [(owner, owner_release, state['worker_done'])])
    session._reap_local_candidate_pending()
    assert pending not in session._LOCAL_CANDIDATE_PENDING
    assert state['result'] is state['resolver'] is state['query'] is None


def test_timed_out_entry_reaps_real_late_worker(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def delayed(**kwargs):
        entered.set()
        release.wait()
        return object()
    monkeypatch.setattr(session, '_selected_pin_read', lambda *args: b'{}')
    monkeypatch.setattr(session, 'create_selected_row_resolver', delayed)
    args = ['--mind-local-candidate-read', '--policy-path', 'synthetic',
            '--policy-sha256', 'a'*64, '--approval-reference-sha256', 'b'*64,
            '--lane', 'codex-lead-1', '--timeout-seconds', '1']
    out, err = io.BytesIO(), io.BytesIO()
    before = set(id(p) for p in session._LOCAL_CANDIDATE_PENDING)
    try:
        code = session._local_candidate_entry(args, out, err, 'synthetic', 'c'*64)
        created = [p for p in session._LOCAL_CANDIDATE_PENDING if id(p) not in before]
        assert entered.is_set() and code == 2 and out.getvalue() == b''
        assert len(created) == 1 and created[0][0].is_alive()
        worker, state = created[0]
        assert state['abandoned'] and state['result'] is None
        session._reap_local_candidate_pending()
        assert created[0] in session._LOCAL_CANDIDATE_PENDING
    finally:
        # Preserve each synthetic worker even after interrupted start or wait.
        created = [p for p in session._LOCAL_CANDIDATE_PENDING if id(p) not in before]
        _drain_all([(p[0], release, p[1]['worker_done']) for p in created])
    session._reap_local_candidate_pending()
    assert created[0] not in session._LOCAL_CANDIDATE_PENDING
    assert state['result'] is state['resolver'] is state['query'] is None


def test_timeout_before_result_store_discards_late_success(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    saved_store = session._store_local_candidate_result
    def delayed_store(state, encoded):
        entered.set()
        release.wait()
        saved_store(state, encoded)
    class Resolver:
        def resolve(self, **kwargs):
            return {'schema':'wd.mind-flat-verified-session.v1', 'authority':'none',
                    'sender_authentication':'UNKNOWN', 'lane':'codex-lead-1',
                    'results':[{'text':'synthetic-marker'}]}
    query = runtime._LocalCandidateQuery('synthetic', {})
    monkeypatch.setattr(query, 'start', lambda: None)
    monkeypatch.setattr(query, 'wait', lambda **kwargs: {'status':'CANDIDATES', 'authority':'none',
        'sender_authentication':'UNKNOWN', 'source_verification':'UNVERIFIED', 'candidate_frame':b'synthetic'})
    monkeypatch.setattr(runtime, 'create_local_candidate_query', lambda **kwargs: query)
    monkeypatch.setattr(session, '_selected_pin_read', lambda *args: b'{}')
    monkeypatch.setattr(session, 'create_selected_row_resolver', lambda **kwargs: Resolver())
    monkeypatch.setattr(session, '_store_local_candidate_result', delayed_store)
    args = ['--mind-local-candidate-read', '--policy-path', 'synthetic',
            '--policy-sha256', 'a'*64, '--approval-reference-sha256', 'b'*64,
            '--lane', 'codex-lead-1', '--timeout-seconds', '1']
    out, err = io.BytesIO(), io.BytesIO()
    before = set(id(p) for p in session._LOCAL_CANDIDATE_PENDING)
    try:
        code = session._local_candidate_entry(args, out, err, 'synthetic', 'c'*64)
        created = [p for p in session._LOCAL_CANDIDATE_PENDING if id(p) not in before]
        assert entered.is_set() and code == 2 and out.getvalue() == b''
        assert len(created) == 1 and created[0][1]['abandoned']
        worker, state = created[0]
    finally:
        created = [p for p in session._LOCAL_CANDIDATE_PENDING if id(p) not in before]
        _drain_all([(p[0], release, p[1]['worker_done']) for p in created])
    assert state['result'] is None and out.getvalue() == b''
    session._reap_local_candidate_pending()
    assert created[0] not in session._LOCAL_CANDIDATE_PENDING


def test_abandon_retries_interruptions_before_clearing_state():
    original = KeyboardInterrupt('synthetic first cancellation')
    class InterruptedLock:
        attempts = 0
        def __enter__(self):
            self.attempts += 1
            if self.attempts == 1: raise original
            if self.attempts == 2: raise SystemExit('synthetic second cancellation')
            return self
        def __exit__(self, *args): return False
    state = _state()
    state['abandoned'] = False
    lock = state['result_lock'] = InterruptedLock()
    result = session._abandon_local_candidate_state(state)
    assert result is original and lock.attempts == 3
    assert state['abandoned'] and state['result'] is None


@pytest.mark.parametrize('exc', [ValueError('synthetic'), KeyboardInterrupt('synthetic')])
def test_entry_reaper_exception_refuses_or_preserves_cancellation(monkeypatch, exc):
    def interrupted(): raise exc
    monkeypatch.setattr(session, '_reap_local_candidate_pending', interrupted)
    out, err = io.BytesIO(), io.BytesIO()
    if isinstance(exc, Exception):
        assert session._local_candidate_entry([], out, err, 'synthetic', 'c'*64) == 2
    else:
        with pytest.raises(KeyboardInterrupt) as caught:
            session._local_candidate_entry([], out, err, 'synthetic', 'c'*64)
        assert caught.value is exc
    assert out.getvalue() == b''
    assert err.getvalue() == b'{"error":"mind_session_content_refused","authority":"none"}\n'
