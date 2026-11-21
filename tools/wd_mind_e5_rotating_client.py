# SPDX-License-Identifier: BUSL-1.1
"""Cooperatively rotate pinned E5 workers before their existing budgets expire.

The caller authenticates the factory and source.  ``source_check`` must verify
the complete caller-selected source and policy before returning its pinned
digest; a matching digest supplied by an untrusted caller proves nothing.
No files, process, network connection or model are created by this module
itself.  The supplied factory owns process creation.  A failed or uncertain
shutdown retains the old owner and prevents a replacement from starting.
"""
import hashlib
import hmac
import json
import math
import re
import threading
import time

from tools.wd_mind_e5_client import GracefulE5WorkerClient
from tools.wd_mind_e5_lifecycle import BULK_CONTRACT_SHA256, BULK_STDOUT_BYTES, MAX_STDOUT_BYTES

_HEX = re.compile(r'[0-9a-f]{64}\Z')
_FRAME_OVERHEAD = 79  # 4-byte length plus <4sIBH32s32s response header.
_VECTOR_BYTES = 384 * 4
_FRAME_BATCH = 4
_MAX_RECORDS = 256
_BULK_NONCE_LIMIT = 8192
_ORDINARY_NONCE_LIMIT = 1024
_SESSION_NONCE_LIMIT = 65536


class RotationRefused(ValueError):
    """Fixed refusal without exposing caller text or provider exceptions."""


class RotatingGracefulE5Client:
    """Retain one worker at a time; ownership remains reachable on HOLD.

    The caller must retain this instance whenever ``owner`` is not None.  Its
    source callback and manifest-backed factory require independent trust.
    Replay tracking is process-local and is not an authentication guarantee.
    It retains at most 65,536 distinct request nonces across worker rotations;
    later requests refuse without resetting that replay boundary.
    """

    def __init__(self, *, factory, encoder_binding_sha256, snapshot_sha256,
                 source_check, shutdown_timeout, call_timeout, bulk_contract_sha256=None):
        if (not callable(factory) or isinstance(factory, type)
                or not callable(source_check) or isinstance(source_check, type)
                or type(encoder_binding_sha256) is not str or not _HEX.fullmatch(encoder_binding_sha256)
                or type(snapshot_sha256) is not str or not _HEX.fullmatch(snapshot_sha256)
                or type(shutdown_timeout) not in (int, float) or not math.isfinite(shutdown_timeout)
                or not 0 <= shutdown_timeout <= 1800
                or type(call_timeout) not in (int, float) or not math.isfinite(call_timeout)
                or not 0 < call_timeout <= 1800
                or (bulk_contract_sha256 is not None and bulk_contract_sha256 != BULK_CONTRACT_SHA256)):
            raise RotationRefused('mind_e5_rotation_refused')
        self._factory, self._source_check = factory, source_check
        self._binding, self._snapshot = encoder_binding_sha256, snapshot_sha256
        self._timeout, self._shutdown_timeout = float(call_timeout), float(shutdown_timeout)
        self._bulk_contract_sha256 = bulk_contract_sha256
        self._stdout_limit = BULK_STDOUT_BYTES if bulk_contract_sha256 else MAX_STDOUT_BYTES
        self._request_limit = _BULK_NONCE_LIMIT if bulk_contract_sha256 else _ORDINARY_NONCE_LIMIT
        self._client = self._owner = None
        self._owned_process = self._owned_job = None
        self._worker_timeout = None
        self._owner_conflicts = []
        self._used_bytes, self._requests = _FRAME_OVERHEAD, 0
        self._frames = 0
        self._worker_nonces = set()
        self._session_nonces = set()
        self._failed = self._closed = self._hold = False
        self._lock = threading.Lock()

    @property
    def owner(self):
        return self._owner

    @property
    def active_client(self):
        return self._client

    @property
    def call_timeout(self):
        return self._timeout

    @property
    def retained_owners(self):
        """Expose all retained owners for diagnostics; never discard conflicts."""
        primary = () if self._owner is None else (self._owner,)
        return primary + tuple(self._owner_conflicts)

    def worker_exit_diagnostic(self):
        """Return a fixed untrusted child-code hint without waiting."""
        if not self._lock.acquire(blocking=False):
            return ('UNKNOWN', None)
        try:
            if self._owner_conflicts or self._owner is None:
                return ('UNKNOWN', None)
            try:
                return self._owner.worker_exit_diagnostic()
            except BaseException:
                return ('UNKNOWN', None)
        finally:
            self._lock.release()

    def status(self):
        """Never wait on an in-flight request; uncertain ownership is HOLD."""
        if not self._lock.acquire(blocking=False):
            return {'state': 'BUSY', 'owned': None, 'authority': 'none'}
        try:
            if self._owner_conflicts:
                return {'state': 'HOLD', 'owned': None, 'authority': 'none'}
            if self._owner is None:
                state = 'FAILED' if self._failed else ('EXITED' if self._closed else 'NEW')
                return {'state': state,
                        'owned': False, 'authority': 'none'}
            try:
                status = self._owner.status()
                owned = status.get('owned') if type(status) is dict else None
                state = 'HOLD' if self._hold or self._failed else status.get('state')
                return {'state': state, 'owned': owned, 'authority': 'none'}
            except BaseException:
                return {'state': 'HOLD', 'owned': None, 'authority': 'none'}
        finally:
            self._lock.release()

    def _check_source(self):
        digest = self._source_check()
        if type(digest) is not str or not hmac.compare_digest(digest, self._snapshot):
            raise ValueError('source_changed')

    def _capture_owner(self, owner):
        if owner is None:
            raise ValueError('owner_missing')
        if self._owner is not None and self._owner is not owner:
            self._owner_conflicts.append(owner)
            self._hold = True
            raise ValueError('owner_changed')
        self._owner = owner

    def _start(self, call_deadline):
        if time.monotonic() >= call_deadline:
            raise ValueError('deadline')
        # There is deliberately no legacy owner_sink-only fallback: a factory
        # unable to receive the original deadline must not create a worker.
        result = self._factory(owner_sink=self._capture_owner, call_deadline=call_deadline)
        if type(result) is not dict:
            raise ValueError('start')
        returned = result.get('owner')
        if returned is not self._owner or self._owner is None:
            if returned is not None:
                self._owner_conflicts.append(returned)
            self._hold = True
            raise ValueError('owner_changed')
        client = result.get('client')
        # Store first, before further fallible checks, to retain its resources.
        self._client = client
        if time.monotonic() >= call_deadline:
            self._hold = True
            raise ValueError('deadline')
        if (result.get('status') != 'READY' or type(client) is not GracefulE5WorkerClient
                or client.owner is not self._owner or client._binding_sha256 != self._binding
                or client._bulk_contract_sha256 != self._bulk_contract_sha256
                or type(client._timeout) not in (int, float) or not math.isfinite(client._timeout)
                or not 0 < client._timeout <= self._timeout
                or client._closed is not False or client._next_id != 1 or client._used_nonces
                or getattr(self._owner, '_bulk_contract_sha256', None) != self._bulk_contract_sha256
                or (self._bulk_contract_sha256 is not None
                    and result.get('bulk_contract_sha256') != self._bulk_contract_sha256)):
            self._hold = True
            raise ValueError('start')
        # The launcher completes the handshake within its remaining startup
        # deadline. Retain that (possibly shorter) call timeout exactly; never
        # extend it to the factory's configured ceiling.
        self._worker_timeout = client._timeout
        status = self._owner.status()
        if (type(status) is not dict or status.get('state') != 'RUNNING'
                or status.get('owned') is not True or status.get('reason') is not None):
            self._hold = True
            raise ValueError('owner_unavailable')
        self._owned_process = self._owner.resources.process
        self._owned_job = self._owner.resources.job
        if self._owned_process is None or self._owned_job is None:
            self._hold = True
            raise ValueError('resources')
        self._used_bytes, self._requests = _FRAME_OVERHEAD, 0
        self._frames, self._worker_nonces = 0, set()
        self._check_worker(_FRAME_OVERHEAD, 0, self._worker_nonces)

    def _check_worker(self, expected_bytes, expected_frames, expected_nonces):
        """Compare against actual pump counters, not an assumed byte estimate."""
        self._check_worker_identity()
        if (type(self._client._used_nonces) is not set
                or self._client._used_nonces != expected_nonces
                or self._client._next_id != expected_frames + 1):
            raise ValueError('worker_binding')
        with self._owner._condition:
            if (type(self._owner._counts) is not dict or type(self._owner._counts.get('stdout')) is not int
                    or self._owner._counts['stdout'] != expected_bytes
                    or type(self._owner._output) is not bytearray or self._owner._output):
                raise ValueError('worker_output')

    def _check_worker_identity(self):
        if (type(self._client) is not GracefulE5WorkerClient or self._client.owner is not self._owner
                or self._owner.resources.process is not self._owned_process
                or self._owner.resources.job is not self._owned_job
                or self._client._closed is not False or self._client._binding_sha256 != self._binding
                or self._client._bulk_contract_sha256 != self._bulk_contract_sha256
                or type(self._worker_timeout) not in (int, float) or not math.isfinite(self._worker_timeout)
                or not 0 < self._worker_timeout <= self._timeout
                or type(self._client._timeout) is not type(self._worker_timeout)
                or self._client._timeout != self._worker_timeout):
            raise ValueError('worker_binding')
        status = self._owner.status()
        if (type(status) is not dict or status.get('state') != 'RUNNING'
                or status.get('owned') is not True or status.get('reason') is not None):
            raise ValueError('worker_unavailable')

    def _finish(self, timeout):
        if self._owner_conflicts:
            self._hold = True
            raise ValueError('owner_conflict')
        if self._client is None:
            if self._owner is not None:
                # The launch may fail after reserving its owner but before a
                # client exists.  An explicit close can reconcile that owner;
                # a build request must never start a replacement on this path.
                if not self._closed:
                    self._hold = True
                    raise ValueError('owner_retained')
                try:
                    status = self._owner.shutdown(timeout=timeout)
                except BaseException:
                    self._hold = True
                    raise
                if (type(status) is not dict or status.get('owned') is not False
                        or status.get('state') not in ('CANCELLED', 'EXITED')):
                    self._hold = True
                    raise ValueError('owner_retained')
                self._owner = None
                self._hold = False
                return status
            return {'state': 'EXITED', 'owned': False, 'exit_code': 0}
        if type(self._client) is not GracefulE5WorkerClient or self._client.owner is not self._owner:
            self._hold = True
            raise ValueError('owner_changed')
        try:
            status = self._client.close(timeout=timeout)
        except BaseException:
            self._hold = True
            raise
        if (type(status) is not dict or status.get('state') != 'EXITED'
                or status.get('owned') is not False or status.get('exit_code') != 0):
            self._hold = True
            raise ValueError('owner_retained')
        self._client = self._owner = None
        self._owned_process = self._owned_job = None
        self._worker_timeout = None
        self._hold = False
        return status

    def _validate(self, request):
        if type(request) is not dict or request.get('operation') not in ('refresh', 'query'):
            raise ValueError('request')
        field = 'passages' if request['operation'] == 'refresh' else 'query'
        if (set(request) != {'schema', 'operation', 'nonce', 'encoder_binding_sha256', 'request_sha256', field}
                or request['schema'] != 'wd.mind-supervised-e5-request.v1'
                or request['encoder_binding_sha256'] != self._binding
                or type(request['nonce']) is not str or not _HEX.fullmatch(request['nonce'])
                or type(request['request_sha256']) is not str or not _HEX.fullmatch(request['request_sha256'])):
            raise ValueError('binding')
        values = request[field] if field == 'passages' else [request[field]]
        if type(values) is not list or not 1 <= len(values) <= _MAX_RECORDS:
            raise ValueError('count')
        prefix = 'passage: ' if field == 'passages' else 'query: '
        if any(type(value) is not str or not value.startswith(prefix) or not value[len(prefix):].strip()
               or len(value) > 2009 or '\x00' in value for value in values):
            raise ValueError('text')
        unsigned = {key: value for key, value in request.items() if key != 'request_sha256'}
        raw = json.dumps(unsigned, sort_keys=True, ensure_ascii=False,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
        if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), request['request_sha256']):
            raise ValueError('digest')
        return len(values)

    def encode_supervised(self, request, *, frame_check=None, call_deadline=None):
        if not self._lock.acquire(blocking=False):
            raise RotationRefused('mind_e5_rotation_refused')
        try:
            # Caller mistakes do not retire a healthy shared owner.
            try:
                if self._failed or self._closed or self._hold:
                    raise ValueError('closed')
                count = self._validate(request)
                if (frame_check is not None and (not callable(frame_check) or isinstance(frame_check, type))
                        or call_deadline is not None and (type(call_deadline) not in (int, float)
                           or not math.isfinite(call_deadline) or time.monotonic() >= call_deadline)):
                    raise ValueError('arguments')
                limit = time.monotonic() + self._timeout
                call_deadline = min(float(call_deadline), limit) if call_deadline is not None else limit
                frozen = json.dumps(request, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode('utf-8')
                request = json.loads(frozen.decode('utf-8'))
                nonce = request['nonce']
                if nonce in self._session_nonces or len(self._session_nonces) >= _SESSION_NONCE_LIMIT:
                    raise ValueError('nonce')
                needed = ((count + _FRAME_BATCH - 1) // _FRAME_BATCH) * _FRAME_OVERHEAD + count * _VECTOR_BYTES
                frames = (count + _FRAME_BATCH - 1) // _FRAME_BATCH
                if _FRAME_OVERHEAD + needed > self._stdout_limit:
                    raise ValueError('budget')
            except Exception:
                raise RotationRefused('mind_e5_rotation_refused') from None
            try:
                self._session_nonces.add(nonce)  # Never reuse after a failed call.
                self._check_source()
                if self._client is not None:
                    self._check_worker(self._used_bytes, self._frames, self._worker_nonces)
                if self._client is not None and (self._requests >= self._request_limit
                        or self._used_bytes + needed > self._stdout_limit
                        or self._frames + frames > 0xFFFFFFFF):
                    timeout = self._shutdown_timeout if call_deadline is None else min(
                        self._shutdown_timeout, max(0, call_deadline - time.monotonic()))
                    self._finish(timeout)
                    self._check_source()
                if call_deadline is not None and time.monotonic() >= call_deadline:
                    raise ValueError('deadline')
                if self._client is None:
                    self._start(call_deadline)

                def guarded_frame_check():
                    self._check_worker_identity()
                    if frame_check is not None:
                        frame_check()
                    self._check_worker_identity()

                result = self._client.encode_supervised(request, frame_check=guarded_frame_check,
                                                        call_deadline=call_deadline)
                if (type(result) is not dict or set(result) != {'nonce', 'request_sha256', 'vector_bytes'}
                        or result['nonce'] != nonce or result['request_sha256'] != request['request_sha256']
                        or type(result['vector_bytes']) is not bytes
                        or len(result['vector_bytes']) != count * _VECTOR_BYTES
                        or json.dumps(request, sort_keys=True, ensure_ascii=False,
                                      separators=(',', ':'), allow_nan=False).encode('utf-8') != frozen):
                    raise ValueError('response')
                new_nonces = self._worker_nonces | {nonce}
                self._check_worker(self._used_bytes + needed, self._frames + frames, new_nonces)
                self._check_source()
                if call_deadline is not None and time.monotonic() >= call_deadline:
                    raise ValueError('deadline')
                self._used_bytes += needed
                self._requests += 1
                self._frames += frames
                self._worker_nonces = new_nonces
                return result
            except BaseException as error:
                self._failed = True
                # A factory exception after owner_sink may leave a live worker
                # without a client. Retain it; never start a replacement.
                if self._owner is not None and self._client is None:
                    self._hold = True
                if not self._hold:
                    try:
                        self._finish(0)
                    except BaseException:
                        self._hold = True
                if isinstance(error, Exception):
                    raise RotationRefused('mind_e5_rotation_refused') from None
                raise
        finally:
            self._lock.release()

    def close(self, *, timeout=0):
        if (type(timeout) not in (int, float) or not math.isfinite(timeout)
                or not 0 <= timeout <= 1800 or not self._lock.acquire(blocking=False)):
            raise RotationRefused('mind_e5_rotation_refused')
        try:
            self._closed = True
            try:
                return self._finish(float(timeout))
            except BaseException as error:
                self._hold = True
                if isinstance(error, Exception):
                    raise RotationRefused('mind_e5_rotation_refused') from None
                raise
        finally:
            self._lock.release()
