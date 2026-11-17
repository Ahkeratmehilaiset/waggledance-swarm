"""Synthetic in-process host/client prototype; default off, not an IPC service.

There is no real child process, launcher binding, peer authentication, global
single-flight, crash recovery or source-content verification here.  Results
are untrusted source-reference candidates, not Mind context.  Tests supply only
synthetic inert adapters. Never use this prototype for installation.
"""
import math
import re
import threading
import time
from dataclasses import dataclass

from tools.wd_mind_startup_guardian import Guardian

_HEX=re.compile(r'[0-9a-f]{64}\Z')
_LANES=frozenset(('codex-lead-1','codex-tools-1','claude-rco-1','claude-rco-2','fable-5'))

@dataclass(frozen=True)
class Request:
    lane: str
    session: str
    nonce: str
    manifest_sha256: str


def _valid(request):
    return (type(request) is Request and type(request.lane) is str and request.lane in _LANES and
            all(type(value) is str and len(value)==64 and _HEX.fullmatch(value) is not None
                for value in (request.session,request.nonce,request.manifest_sha256)))


class _Exchange:
    """One single-use bounded response; EXPIRED cannot become READY."""
    def __init__(self,request,deadline,clock,permitted=None):
        self.request,self.deadline,self._clock=request,deadline,clock
        self._permitted=permitted if permitted is not None else (lambda exchange:(lambda:None))
        self._lock=threading.Lock();self._state='PENDING';self._result=None
        self.event=threading.Event()

    def expire(self):
        if not self._lock.acquire(False): return False
        try:
            if self._state in ('PENDING','READY'): self._state='EXPIRED';self._result=None
        finally:self._lock.release()
        # A woken reader must never contend with the publisher's lock.
        self.event.set()
        return True

    def complete(self,result):
        if not self._lock.acquire(False): return False
        release=None
        try:
            if self._state=='PENDING' and self._clock()<self.deadline:
                release=self._permitted(self)
            accepted=release is not None and self._clock()<self.deadline
            if accepted:
                self._result=result;self._state='READY'
            else:
                if self._state=='PENDING': self._state='EXPIRED'
                self._result=None
        finally:
            if release is not None:release()
            self._lock.release()
        self.event.set()
        return accepted

    def read(self):
        if not self._lock.acquire(False): return {'state':'WITHOUT_MIND','reason':'CONTENDED'}
        release=None
        try:
            if self._state in ('PENDING','READY'):
                release=self._permitted(self)
                if release is None:self._state='EXPIRED';self._result=None
            if self._state in ('PENDING','READY') and self._clock()>=self.deadline:
                self._state='EXPIRED';self._result=None
            if self._state=='READY':
                # The host gate is retained across this actual consume point.
                result=self._result;self._result=None;self._state='CONSUMED'
                return {'state':'CANDIDATES_UNTRUSTED','result':result}
            return {'state':'WITHOUT_MIND','reason':self._state}
        finally:
            if release is not None:release()
            self._lock.release()


class SyntheticHost:
    """One background host and one active exchange in this Python process only."""
    def __init__(self,*,enabled=False,guardian=None,clock=time.monotonic):
        if type(enabled) is not bool or (enabled and type(guardian) is not Guardian):
            raise ValueError('host_binding')
        self.enabled,self.guardian,self.clock=enabled,guardian,clock
        self._lock=threading.Lock();self._wake=threading.Event();self._stop=threading.Event()
        self._active=None;self._thread=None;self._state='OFF' if not enabled else 'NEW'
        self._sessions={};self._latest={}

    def start(self):
        # Start is deliberately outside the client's deadline/launcher path.
        if not self.enabled:return 'OFF'
        if not self._lock.acquire(False):return 'HOLD'
        try:
            if self._thread is not None:return self._state
            self._state='STARTING'
            self._thread=threading.Thread(target=self._run,name='synthetic-mind-guardian-host',daemon=True)
            try:self._thread.start()
            except BaseException:self._state='HOLD'
            return self._state
        finally:self._lock.release()

    def offer(self,request,budget,started):
        if not self.enabled:return None,'OFF'
        if (not _valid(request) or type(budget) not in (int,float) or not math.isfinite(budget)
                or not 0<budget<=30 or type(started) not in (int,float) or not math.isfinite(started)):return None,'INVALID'
        deadline=started+budget
        if self.clock()>=deadline:return None,'EXPIRED'
        if not self._lock.acquire(False):return None,'BUSY'
        try:
            if self._stop.is_set():return None,'BUSY'
            previous=self._latest.get(request.lane)
            if self._sessions.get(request.lane)!=request.session:
                self._sessions[request.lane]=request.session
                if previous is not None:previous.expire()
            if self._state!='READY' or self._active is not None:return None,'BUSY'
            if previous is not None:previous.expire()
            exchange=_Exchange(request,deadline,self.clock,self._permitted)
            self._latest[request.lane]=exchange
            self._active=exchange;self._wake.set();return exchange,'PENDING'
        finally:self._lock.release()

    def _permitted(self,exchange):
        # On success the caller owns this host gate until consume/publish finishes.
        if self._stop.is_set() or not self._lock.acquire(False):return None
        request=exchange.request
        if (not self._stop.is_set() and self._sessions.get(request.lane)==request.session
                and self._latest.get(request.lane) is exchange):
            return self._lock.release
        self._lock.release()
        return None

    def attach(self,*,lane,session):
        if (type(lane) is not str or lane not in _LANES or type(session) is not str
                or len(session)!=64 or _HEX.fullmatch(session) is None):return False
        if not self.enabled or self._stop.is_set() or not self._lock.acquire(False):return False
        try:
            if self._stop.is_set():return False
            if self._sessions.get(lane)!=session:
                self._sessions[lane]=session
                previous=self._latest.get(lane)
                if previous is not None:previous.expire()
            return True
        finally:self._lock.release()

    def detach(self,*,lane,session):
        if (type(lane) is not str or lane not in _LANES or type(session) is not str
                or len(session)!=64 or _HEX.fullmatch(session) is None):return False
        if not self._lock.acquire(False):return False
        try:
            if self._sessions.get(lane)!=session:return False
            del self._sessions[lane]
            previous=self._latest.get(lane)
            if previous is not None:previous.expire()
            return True
        finally:self._lock.release()

    def request_stop(self):
        if not self._lock.acquire(False):return 'BUSY'
        try:
            self._stop.set();self._wake.set()
            return 'STOPPED' if self._state=='STOPPED' else 'HOLD'
        finally:self._lock.release()

    def status(self):
        return {'state':self._state,'ownership':'UNKNOWN',
                'scope':'ONE_SYNTHETIC_PROCESS','crash_persistence':'UNKNOWN'}

    def _run(self):
        try:
            self.guardian.start()
            while not self._stop.is_set():
                state=self.guardian.status()
                if state['state']=='READY' and not state['inflight']:
                    self._state='READY';break
                if state['state'] in ('HOLD','DISABLED','STOPPED'):self._state='HOLD';break
                self._wake.wait(.01);self._wake.clear()
            while not self._stop.is_set():
                self._wake.wait(.01);self._wake.clear()
                with self._lock: exchange=self._active
                if exchange is None:continue
                self._state='BUSY'
                self._run_exchange(exchange)
                if self._stop.is_set():break
                state=self.guardian.status()
                if state['state']!='READY' or state['inflight']:
                    self._state='HOLD';break
                with self._lock:self._active=None
                self._state='READY'
        except BaseException:
            self._state='HOLD'
            with self._lock:
                if self._active is not None:self._active.expire()
        finally:
            # Never cancel a callback or close an adapter while it reports active.
            # A blocked/uncertain guardian remains in HOLD; no forced teardown.
            self._state='HOLD'
            with self._lock:
                if self._active is not None:self._active.expire()
            try:
                self.guardian.request_stop()
                while True:
                    status=self.guardian.status()
                    if status['state']=='STOPPED' and not status['inflight']:
                        self._state='STOPPED';return
                    self._wake.wait(.01);self._wake.clear()
            except BaseException:self._state='HOLD'

    def _run_exchange(self,exchange):
        r=exchange.request
        remaining=exchange.deadline-self.clock()
        if remaining<=0:exchange.expire();return
        if not self.guardian.attach(lane=r.lane,session=r.session,expected_manifest_sha256=r.manifest_sha256):
            exchange.expire();return
        answer=self.guardian.submit(lane=r.lane,session=r.session,nonce=r.nonce,budget_seconds=remaining)
        if answer.get('state')!='PENDING':exchange.expire();return
        while not self._stop.is_set():
            answer=self.guardian.poll(lane=r.lane,session=r.session,nonce=r.nonce)
            if answer.get('state')=='RESULT':
                value=answer.get('result')
                # Core has checked syntax/binding only; these are NOT verified source refs.
                keys={'schema','lane','nonce','manifest_sha256','query_id','status','source_refs'}
                refs=value.get('source_refs') if type(value) is dict else None
                if (type(value) is dict and set(value)==keys and
                        value['schema']=='wd.mind-guardian-candidates.v1' and value['lane']==r.lane
                        and value['nonce']==r.nonce and value['manifest_sha256']==r.manifest_sha256
                        and value['query_id']=='startup_work_overview' and type(refs) is list and len(refs)<=16
                        and all(type(ref) is str and len(ref)==64 and _HEX.fullmatch(ref) for ref in refs)
                        and ((value['status']=='ABSTAIN' and not refs) or
                             (value['status']=='CANDIDATES' and bool(refs)))):
                    exchange.complete(dict(value,source_refs=list(refs)))
                else:exchange.expire()
                break
            if self.clock()>=exchange.deadline:exchange.expire()
            status=self.guardian.status()
            if not status['inflight']:
                exchange.expire();break
            self._wake.wait(.01);self._wake.clear()
        if self._stop.is_set():exchange.expire()


class SyntheticClient:
    """In-memory client. No I/O, process creation, or host wait on caller thread."""
    def __init__(self,host):self.host,self.clock=host,host.clock
    def submit(self,request,budget):
        started=self.clock()
        return self.host.offer(request,budget,started)
    def wait(self,exchange):
        remaining=max(0.0,exchange.deadline-self.clock())
        if remaining:exchange.event.wait(remaining)
        return exchange.read()
