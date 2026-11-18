"""Synthetic real threads expose done-before-native-exit; no process or model."""
import threading
import time
import pytest

from tools import wd_mind_runtime_process as process

def query():
    return process._LocalCandidateQuery('synthetic',{})

def finish_owned(thread, release, pending=None):
    # Keep the first cancellation and retry every operation, including release,
    # across later interruptions until identity and physical exit are observed.
    while True:
        try:
            release.set()
            if thread._test_owner_done.is_set() and thread.ident is not None:
                thread.join(timeout=1)
                if not thread.is_alive(): break
        except BaseException as exc:
            if pending is None: pending=exc
        try:
            time.sleep(0.05)
        except BaseException as exc:
            if pending is None: pending=exc
    if pending is not None: raise pending

def finish_all_owned(threads, release):
    # A cancellation raised while draining one owner must not skip the rest.
    pending = None
    for thread in threads:
        try:
            finish_owned(thread, release)
        except BaseException as exc:
            if pending is None: pending = exc
    if pending is not None: raise pending

def start_owned(thread, release):
    target, args, kwargs = thread._target, thread._args, thread._kwargs
    thread._test_owner_done = threading.Event()
    def observed_target():
        try:
            target(*args, **kwargs)
        finally:
            thread._test_owner_done.set()
    thread._target, thread._args, thread._kwargs = observed_target, (), {}
    try:
        thread.start()
    except BaseException as exc:
        # A raised native start does not prove that no thread may run.
        # Allow any late worker to exit, then keep the handle until a real
        # identity and exit can be observed; otherwise remain in HOLD.
        finish_owned(thread,release,exc)

@pytest.mark.parametrize('method',['status','close'])
def test_completed_event_is_not_physical_thread_exit(method):
    q=query()
    observed=threading.Event()
    release=threading.Event()
    def worker():
        q._done.set()
        observed.set()
        release.wait()
    q._thread=threading.Thread(target=worker)
    try:
        start_owned(q._thread,release)
        assert observed.wait(5)
        assert q._thread.is_alive() and q._done.is_set()
        observed_state = q.status() if method=='status' else q.close(timeout=0)
        assert observed_state['state']=='HOLD'
    finally:
        finish_owned(q._thread,release)
    assert not q._thread.is_alive() and q._thread.ident is not None
    assert q.status()['state']=='STOPPED'
    assert q.close(timeout=0)['state']=='STOPPED'

def test_uncertain_unstarted_attempt_stays_held():
    q=query()
    q._thread=threading.Thread(target=lambda:None)
    q._start_uncertain=True
    assert q._thread.ident is None and not q._done.is_set()
    assert q.status()['state']=='HOLD'
    assert q.close(timeout=0)['state']=='HOLD'

def test_uncertain_done_signal_without_started_identity_stays_held():
    q=query()
    q._thread=threading.Thread(target=lambda:None)
    q._start_uncertain=True
    q._done.set()
    assert q._thread.ident is None and not q._thread.is_alive()
    assert q.status()['state']=='HOLD'
    assert q.close(timeout=0)['state']=='HOLD'

def test_close_waits_for_real_exit_without_holding_query_lock():
    q=query()
    observed=threading.Event()
    release=threading.Event()
    join_entered=threading.Event()
    def worker():
        q._done.set()
        observed.set()
        release.wait()
    q._thread=threading.Thread(target=worker)
    real_join=q._thread.join
    def tracked_join(timeout=None):
        join_entered.set()
        return real_join(timeout)
    q._thread.join=tracked_join
    result=[]
    closer=threading.Thread(target=lambda:result.append(q.close(timeout=5)))
    closer_attempted=False
    try:
        start_owned(q._thread,release)
        assert observed.wait(5)
        closer_attempted=True
        start_owned(closer,release)
        assert join_entered.wait(5)
        assert q._lock.acquire(blocking=False)
        q._lock.release()
        release.set()
        closer.join()
        assert result[0]['state']=='STOPPED'
    finally:
        finish_all_owned([q._thread] + ([closer] if closer_attempted else []), release)

def test_close_before_any_start_is_clean():
    q=query()
    assert q.status()['state']=='NEW'
    assert q.close(timeout=0)['state']=='STOPPED'
    assert q.status()['state']=='STOPPED'

def test_finish_owned_retries_interruptions_and_raises_first_after_exit(monkeypatch):
    class Release:
        calls=0
        def set(self):
            self.calls += 1
            if self.calls <= 2: raise SystemExit('later release interruption')
    class Thread:
        ident=123
        calls=0
        exited=False
        _test_owner_done=threading.Event()
        def join(self, timeout):
            self.calls += 1
            if self.calls <= 2: raise KeyboardInterrupt('later join interruption')
            self.exited=True
        def is_alive(self): return not self.exited
    sleep_calls=[]
    def interrupted_sleep(_):
        sleep_calls.append(1)
        if len(sleep_calls)==1: raise GeneratorExit('later sleep interruption')
    monkeypatch.setattr(time,'sleep',interrupted_sleep)
    first=KeyboardInterrupt('original interruption')
    release, thread=Release(), Thread()
    thread._test_owner_done.set()
    with pytest.raises(KeyboardInterrupt) as caught:
        finish_owned(thread, release, first)
    assert caught.value is first
    assert thread.exited and thread.calls==3 and release.calls>=5 and sleep_calls

def test_finish_all_preserves_remaining_owners_after_cancellation(monkeypatch):
    handled=[]
    original=KeyboardInterrupt('original interruption')
    def finish(thread, release):
        handled.append(thread)
        if thread=='first': raise original
    monkeypatch.setitem(finish_all_owned.__globals__,'finish_owned',finish)
    with pytest.raises(KeyboardInterrupt) as caught:
        finish_all_owned(['first','second'], object())
    assert caught.value is original
    assert handled==['first','second']

def test_finish_owned_never_releases_early_identity_without_target_done(monkeypatch):
    class Thread:
        ident=123
        _test_owner_done=threading.Event()
        joined=False
        def join(self, timeout):
            assert self._test_owner_done.is_set()
            self.joined=True
        def is_alive(self): return False
    class Release:
        def set(self): pass
    thread=Thread()
    waits=[]
    def advance(_):
        waits.append(1)
        if len(waits)==2: thread._test_owner_done.set()
    monkeypatch.setattr(time,'sleep',advance)
    finish_owned(thread, Release())
    assert len(waits)==2 and thread.joined
