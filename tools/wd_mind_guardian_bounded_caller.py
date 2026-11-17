"""Default-off, reference-only candidate caller with retained asynchronous I/O.

This WIP is not installed or connected to a launcher. A trusted external
bootstrap, peer authentication, cross-process ownership and source verification
remain unproved. Results are untrusted reference proposals, never Mind content.
No caller request or result is written to disk. A blocked query stays owned in
HOLD; this module never kills a thread, process or remote owner.
The per-instance nonce registry refuses further requests after 1024 accepted
nonces, an availability limit. Replay protection across caller or host restart
is UNKNOWN; this prototype must not be connected to production on that basis.
"""

import hashlib
import json
import math
import re
import threading
import time


_BOOTSTRAP_PROVEN = False
_HEX = re.compile(r'[0-9a-f]{64}\Z')
_LANES = frozenset(('codex-lead-1', 'codex-tools-1', 'claude-rco-1', 'claude-rco-2', 'fable-5'))


class CallerRefused(ValueError):
    """Fixed non-sensitive refusal."""


def _binding(lane, session):
    return type(lane) is str and lane in _LANES and type(session) is str and _HEX.fullmatch(session) is not None


class _BoundedCaller:
    def __init__(self, enabled, manifest):
        self._enabled, self._manifest = enabled, manifest
        self._lock, self._wake, self._stop, self._finished = (threading.Lock(), threading.Event(),
                                                               threading.Event(), threading.Event())
        self._state = 'NEW' if enabled else 'OFF'
        self._thread = self._query = self._ticket = None
        self._lane = self._session = None
        self._generation = 0
        self._used_nonces = set()
        self._launch_committed = False

    def start(self):
        with self._lock:
            if not self._enabled:
                return self._status_locked()
            if not _BOOTSTRAP_PROVEN or self._state != 'NEW' or self._stop.is_set():
                self._state = 'HOLD'
                return self._status_locked()
            self._state = 'READY'  # Pump readiness only; remote readiness is UNKNOWN.
            self._thread = threading.Thread(target=self._run, name='mind-bounded-caller', daemon=False)
            try:
                self._thread.start()
            except BaseException:
                self._state = 'HOLD'
            return self._status_locked()

    def attach(self, *, lane, session):
        if not _binding(lane, session) or not self._lock.acquire(blocking=False):
            return False
        try:
            if not self._enabled or self._stop.is_set() or self._state in ('NEW', 'OFF', 'STOPPED'):
                return False
            if (lane, session) != (self._lane, self._session):
                self._generation += 1
                if self._ticket is not None:
                    self._ticket['expired'] = True
                    if self._state == 'READY' and self._query is None:
                        self._ticket = None
            self._lane, self._session = lane, session
            return True
        finally:
            self._lock.release()

    def detach(self, *, lane, session):
        if not _binding(lane, session) or not self._lock.acquire(blocking=False):
            return False
        try:
            if (lane, session) != (self._lane, self._session):
                return False
            self._lane = self._session = None
            self._generation += 1
            if self._ticket is not None:
                self._ticket['expired'] = True
                if self._state == 'READY' and self._query is None:
                    self._ticket = None
            return True
        finally:
            self._lock.release()

    def submit(self, *, lane, session, nonce, budget_seconds):
        if (not _binding(lane, session) or type(nonce) is not str or not _HEX.fullmatch(nonce)
                or type(budget_seconds) not in (int, float) or not 0 < budget_seconds <= 30
                or not math.isfinite(budget_seconds)):
            return {'state':'REFUSED'}
        started = time.monotonic()
        if not self._lock.acquire(blocking=False):
            return {'state':'WITHOUT_MIND'}
        try:
            if (not self._enabled or self._stop.is_set() or self._state != 'READY'
                    or self._ticket is not None or (lane, session) != (self._lane, self._session)):
                return {'state':'WITHOUT_MIND'}
            if nonce in self._used_nonces or len(self._used_nonces) >= 1024:
                return {'state':'REFUSED'}
            scope = 'shared' if lane in ('claude-rco-1', 'claude-rco-2') else 'own'
            request = {'schema':'wd.mind-pipe-request.v2', 'query_id':'startup_work_overview',
                       'nonce':nonce, 'lane':lane, 'scope':scope}
            self._ticket = {'request':request, 'session':session, 'generation':self._generation,
                            'deadline':started + float(budget_seconds), 'expired':False, 'result':None}
            self._used_nonces.add(nonce)
            self._state = 'BUSY'
            self._finished.clear()
            self._wake.set()
            return {'state':'PENDING'}
        finally:
            self._lock.release()

    def poll(self, *, lane, session, nonce):
        if not _binding(lane, session) or type(nonce) is not str or not _HEX.fullmatch(nonce):
            return {'state':'WITHOUT_MIND'}
        if not self._lock.acquire(blocking=False):
            return {'state':'WITHOUT_MIND'}
        try:
            ticket = self._ticket
            if (ticket is None or self._stop.is_set() or (lane, session) != (self._lane, self._session)
                    or ticket['session'] != session or ticket['request']['nonce'] != nonce
                    or ticket['generation'] != self._generation or ticket['expired']):
                return {'state':'WITHOUT_MIND'}
            if time.monotonic() >= ticket['deadline']:
                ticket['expired'], ticket['result'] = True, None
                if self._state == 'READY' and self._query is None:
                    self._ticket = None
                return {'state':'WITHOUT_MIND'}
            result = ticket['result']
            if result is None:
                return {'state':'PENDING'}
            self._ticket = None
            return result
        finally:
            self._lock.release()

    def request_stop(self):
        # STOP acceptance and result consumption share the same gate. BUSY
        # means no STOP was accepted and the caller must retry.
        if not self._lock.acquire(blocking=False):
            return {'state':'BUSY', 'authority':'none', 'remote_ownership':'UNKNOWN',
                    'sender_authentication':'UNKNOWN'}
        try:
            self._stop.set()
            self._wake.set()
            return self._status_locked()
        finally:
            self._lock.release()

    def _status_locked(self):
        state = self._state
        if self._stop.is_set() and state not in ('OFF', 'STOPPED'):
            state = 'HOLD'
        return {'state':state, 'authority':'none', 'remote_ownership':'UNKNOWN',
                'sender_authentication':'UNKNOWN'}

    def status(self):
        if not self._lock.acquire(blocking=False):
            return {'state':'HOLD', 'authority':'none', 'remote_ownership':'UNKNOWN',
                    'sender_authentication':'UNKNOWN'}
        try:
            return self._status_locked()
        finally:
            self._lock.release()

    def _run(self):
        if not _BOOTSTRAP_PROVEN:
            with self._lock:
                self._state = 'HOLD'
            return
        try:
            # Production bootstrap must pin this dependency before import.
            from tools import wd_mind_runtime_process as process
            while True:
                self._wake.wait()
                self._wake.clear()
                with self._lock:
                    ticket = self._ticket if self._state == 'BUSY' else None
                    if self._stop.is_set():
                        if ticket is not None:
                            ticket['expired'], ticket['result'] = True, None
                            self._ticket = None
                        self._state = 'STOPPED'
                        self._finished.set()
                        return
                if ticket is None:
                    continue
                try:
                    query = process.create_local_candidate_query(enabled=True,
                        pipe_name=process.local_candidate_pipe_name(manifest_sha256=self._manifest),
                        request=ticket['request'])
                    with self._lock:
                        self._query = query  # Retain before any thread can start.
                        skip = (self._stop.is_set() or ticket['expired']
                                or ticket['generation'] != self._generation
                                or (ticket['request']['lane'], ticket['session']) != (self._lane, self._session)
                                or time.monotonic() >= ticket['deadline'])
                        if skip:
                            ticket['expired'], ticket['result'] = True, None
                            self._ticket = self._query = None
                            self._state = 'STOPPED' if self._stop.is_set() else 'READY'
                            self._finished.set()
                        else:
                            # STOP and the decision to enter query.start share
                            # this gate; no blocking I/O runs while it is held.
                            self._launch_committed = True
                    if skip:
                        with self._lock:
                            if self._stop.is_set():
                                self._state = 'STOPPED'
                                return
                        continue
                    query.start()
                    # Continue retaining the actual query even after deadline,
                    # detach or STOP; no new operation starts while it is alive.
                    while not query._done.wait(0.05) or query._thread.is_alive():
                        if time.monotonic() >= ticket['deadline'] or self._stop.is_set():
                            with self._lock:
                                ticket['expired'], ticket['result'], self._state = True, None, 'HOLD'
                    observed = query.wait(timeout=0)
                    closed = query.close(timeout=0)
                    if type(closed) is not dict or closed.get('state') != 'STOPPED':
                        raise ValueError('query_hold')
                    raw = observed.get('candidate_frame') if type(observed) is dict else None
                    reply = process._local_candidate_reply(process._local_candidate_json(raw), ticket['request'])
                    request_raw = json.dumps(ticket['request'], sort_keys=True, separators=(',', ':'),
                                             ensure_ascii=True, allow_nan=False).encode('ascii')
                    proposal = {'state':'CANDIDATES_UNVERIFIED' if reply['candidates'] else 'ABSTAIN',
                        'authority':'none', 'source_verification':'UNVERIFIED', 'sender_authentication':'UNKNOWN',
                        'manifest_sha256':self._manifest, 'session':ticket['session'],
                        'request_sha256':hashlib.sha256(request_raw).hexdigest(),
                        'lane':ticket['request']['lane'], 'nonce':ticket['request']['nonce'],
                        'candidates':[dict(candidate) for candidate in reply['candidates']]}
                    with self._lock:
                        if (not self._stop.is_set() and not ticket['expired']
                                and ticket['generation'] == self._generation
                                and (ticket['request']['lane'], ticket['session']) == (self._lane, self._session)
                                and time.monotonic() < ticket['deadline']):
                            ticket['result'] = proposal
                        else:
                            ticket['expired'], ticket['result'] = True, None
                            if self._ticket is ticket:
                                self._ticket = None
                        self._query = None
                        self._launch_committed = False
                        self._state = 'STOPPED' if self._stop.is_set() else 'READY'
                        self._finished.set()
                except BaseException:
                    with self._lock:
                        ticket['expired'], ticket['result'], self._state = True, None, 'HOLD'
                    # Unknown thread start, transport or close cannot be
                    # reconciled by timeout. Retain query and pump on HOLD.
                    self._finished.set()
                    while True:
                        self._wake.wait(0.05)
                        self._wake.clear()
                if self._stop.is_set():
                    with self._lock:
                        self._state = 'STOPPED'
                    return
        except BaseException:
            with self._lock:
                self._state = 'HOLD'


def create_caller(*, enabled=False, manifest_sha256=None):
    if (type(enabled) is not bool or type(manifest_sha256) is not str
            or not _HEX.fullmatch(manifest_sha256)):
        raise CallerRefused('guardian_caller_refused')
    if enabled and not _BOOTSTRAP_PROVEN:
        raise CallerRefused('guardian_caller_refused')
    return _BoundedCaller(enabled, manifest_sha256)
