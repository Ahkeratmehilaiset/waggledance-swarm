# SPDX-License-Identifier: BUSL-1.1
"""Offline preflight and separate default-off Windows runtime building blocks.

The caller must independently anchor every supplied pin and authorize every
path. This preflight checks three exact files and the external runtime manifest
using the pinned verifier before any runtime module, bootstrap, or model is
executed. The verifier is the separate packaged adaptation of
``sandbox/mind/step2/runtime_manifest.py`` at commit
``e98c5aef5bd3bd66c61e5692e738f17a4ecf6beb``. It is loaded from the exact
bytes whose digest is checked, not by an ambient import path.

The preflight performs no process, network, model load or write. Guardian and
named-pipe service entrypoints are explicitly deferred and refuse immediately.
This does not assert native-library closure, OS network isolation, process
readiness, same-user race resistance or a hard bound on synchronous checks.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib
import importlib.util
import dis
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import threading
import time
import types
import unicodedata

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_CODE = 2 * 1024 * 1024
_MAX_INTERPRETER = 512 * 1024 * 1024
_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z", re.IGNORECASE)
# SHA256 of the packaged e98-derived verifier. This file and its constant must
# themselves be covered by an independently anchored bundle manifest.
_PACKAGED_VERIFIER_SHA256 = "d488a8495f0ee89aebc26d2d1345878d26e5a6c85b228e2c10086f4134680a64"
_VERIFIER_RELATIVE = "tools-bootstrap/tools/wd_mind_runtime_manifest.py"


class RuntimePreflightRefused(ValueError):
    """Fixed, path-free refusal for the offline preflight."""


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _read_pinned_file(value, pin, *, limit, retain=False):
    if type(pin) is not str or not _SHA256.fullmatch(pin):
        raise ValueError("pin")
    path = Path(value)
    if (not path.is_absolute() or ".." in path.parts or
            (os.name == "nt" and path.anchor.startswith("\\\\"))):
        raise ValueError("path")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        if ":" in part or part.endswith((".", " ")) or _DEVICE.fullmatch(part):
            raise ValueError("path")
        current /= part
        info = os.lstat(current)
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("reparse")
    before = os.lstat(path)
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or not 0 < before.st_size <= limit):
        raise ValueError("file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    digest = hashlib.sha256()
    blocks = [] if retain else None
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or _identity(before) != _identity(opened):
            raise ValueError("file_changed")
        remaining = opened.st_size
        while remaining:
            block = stream.read(min(1024 * 1024, remaining))
            if not block:
                raise ValueError("file_changed")
            remaining -= len(block)
            digest.update(block)
            if retain:
                blocks.append(block)
        after = os.fstat(stream.fileno())
        final = os.lstat(path)
        if (stream.read(1) or not stat.S_ISREG(final.st_mode) or final.st_nlink != 1
                or _identity(before) != _identity(after) or _identity(before) != _identity(final)):
            raise ValueError("file_changed")
    if not hmac.compare_digest(digest.hexdigest(), pin):
        raise ValueError("pin")
    return b"".join(blocks) if retain else None


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_manifest_key")
        result[key] = value
    return result


# Deferred guardian and named-pipe service surfaces. No reservation, process,
# endpoint, or filesystem side effect is performed by these entrypoints.
_CANDIDATE_QUERY_TIMEOUT_SECONDS = 330

def _deferred_runtime_service(*args, **kwargs):
    raise RuntimePreflightRefused("runtime_process_preflight_refused") from None

reserve_guardian_attempt = _deferred_runtime_service
create_candidate_pipe = _deferred_runtime_service
query_candidate_pipe = _deferred_runtime_service
create_candidate_service = _deferred_runtime_service
start_manifest_guardian = _deferred_runtime_service

def _guardian_entrypoint(argv, stdin, stdout):
    return 2


_LOCAL_CANDIDATE_PIPE_PREFIX = r"\\.\pipe\wd-mind-"
_LOCAL_CANDIDATE_MAX_BYTES = 4096
_LOCAL_CANDIDATE_LANES = frozenset(('codex-lead-1', 'codex-tools-1', 'claude-rco-1', 'claude-rco-2', 'fable-5'))


def local_candidate_pipe_name(*, manifest_sha256):
    """Return a local rendezvous name, not proof of the server's identity."""
    if type(manifest_sha256) is not str or not _SHA256.fullmatch(manifest_sha256):
        raise RuntimePreflightRefused('runtime_process_preflight_refused') from None
    return _LOCAL_CANDIDATE_PIPE_PREFIX + manifest_sha256


def _local_candidate_pipe_name_valid(value):
    return (type(value) is str and value.startswith(_LOCAL_CANDIDATE_PIPE_PREFIX)
            and bool(_SHA256.fullmatch(value[len(_LOCAL_CANDIDATE_PIPE_PREFIX):])))


def _local_candidate_json(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= _LOCAL_CANDIDATE_MAX_BYTES:
        raise ValueError('frame_size')
    return json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_json_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('constant')))


def _local_candidate_encode(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('ascii')
    if not 0 < len(raw) <= _LOCAL_CANDIDATE_MAX_BYTES:
        raise ValueError('frame_size')
    return raw


# Train 4 item C10: one free-text question beside the fixed startup question.
FREE_TEXT_QUERY_ID = 'free_text_v1'
# At most 512 code points, AND at most 2048 bytes once ASCII-escaped: the JSON
# frame is escaped (a supplementary character takes 12 bytes) and capped at
# _LOCAL_CANDIDATE_MAX_BYTES, so a question of non-ASCII text is shorter.
FREE_TEXT_MAX_CHARS = 512
FREE_TEXT_MAX_ESCAPED_BYTES = 2048
# The fixed startup sentence (tools/wd_mind_full_runtime.py _STARTUP_PIPE_QUERY).
# Ranking keys on that text, so it is never a free question; refusing it here and
# in the owner's first validation keeps the refusal before any owner state.
FREE_TEXT_RESERVED = 'current task active work next step recent status'


def free_text_question_ok(value):
    """True only for an allowed free-text question. Never raises and never echoes the text."""
    try:
        return (type(value) is str and 1 <= len(value) <= FREE_TEXT_MAX_CHARS and bool(value.strip())
                and value.isprintable() and unicodedata.normalize('NFC', value) == value
                and len(json.dumps(value, ensure_ascii=True)) - 2 <= FREE_TEXT_MAX_ESCAPED_BYTES
                and value != FREE_TEXT_RESERVED)
    except Exception:
        return False


def _local_candidate_request(value):
    keys = {'schema', 'query_id', 'nonce', 'lane', 'scope'}
    if type(value) is not dict:
        raise ValueError('request')
    free = value.get('query_id') == FREE_TEXT_QUERY_ID
    if free:
        keys = keys | {'query'}
    if (set(value) != keys or any(type(value[key]) is not str for key in keys)
            or value['schema'] not in ('wd.mind-pipe-request.v1', 'wd.mind-pipe-request.v2')
            or value['query_id'] not in ('startup_work_overview', FREE_TEXT_QUERY_ID)
            or not _SHA256.fullmatch(value['nonce'])
            or value['lane'] not in _LOCAL_CANDIDATE_LANES or value['scope'] not in ('own', 'shared')
            or (free and not free_text_question_ok(value['query']))):
        raise ValueError('request')
    return dict(value)


def _local_candidate_reply(value, request):
    keys = {'schema', 'nonce', 'lane', 'scope', 'status', 'candidates'}
    version = 'v2' if request['schema'] == 'wd.mind-pipe-request.v2' else 'v1'
    if (type(value) is not dict or set(value) != keys or value['schema'] != 'wd.mind-pipe-candidates.' + version
            or type(value['nonce']) is not str or not hmac.compare_digest(value['nonce'], request['nonce'])
            or value['lane'] != request['lane'] or value['scope'] != request['scope']
            or value['status'] not in ('CANDIDATES', 'ABSTAIN') or type(value['candidates']) is not list
            or len(value['candidates']) > 3 or ((value['status'] == 'CANDIDATES') != bool(value['candidates']))):
        raise ValueError('reply')
    seen = set()
    for candidate in value['candidates']:
        names = ('ref_sha256', 'doc_id_sha256', 'source_event_sha256')
        expected = set(names) | ({'source_offset', 'source_length', 'chunk_index'} if version == 'v2' else set())
        if (type(candidate) is not dict or set(candidate) != expected
                or any(type(candidate[name]) is not str or not _SHA256.fullmatch(candidate[name]) for name in names)):
            raise ValueError('candidate')
        if version == 'v2':
            offset, length, index = (candidate['source_offset'], candidate['source_length'], candidate['chunk_index'])
            if (type(offset) is not int or type(length) is not int or type(index) is not int
                    or offset < 0 or not 1 <= length <= 4 * 1024 * 1024 or not 0 <= index <= 4 * 1024 * 1024
                    or offset + length > 2**63 - 1):
                raise ValueError('candidate_location')
        key = tuple(candidate[name] for name in names)
        if key in seen:
            raise ValueError('duplicate')
        seen.add(key)
    return value


def _first_pipe_create_denied_without_handle(error, address):
    """Recognize only a native first-instance CreateNamedPipe denial.

    Other constructor errors may occur after a pipe handle was allocated;
    they cannot prove that this process owns no native resource.  This check
    is deliberately specific to CPython's Windows AF_PIPE implementation and
    falls back to unknown ownership if its traceback or bytecode differs.
    """
    if (sys.platform != 'win32' or sys.implementation.name != 'cpython' or
            type(error) is not PermissionError or getattr(error, 'winerror', None) != 5):
        return False
    try:
        import _winapi
        import multiprocessing.connection as connection

        native_create = _winapi.CreateNamedPipe
        if (type(native_create) is not types.BuiltinFunctionType or
                native_create.__module__ != '_winapi' or native_create.__name__ != 'CreateNamedPipe'):
            return False
        trace = []
        tb = error.__traceback__
        while tb is not None:
            trace.append(tb)
            tb = tb.tb_next
        if len(trace) != 4:
            return False
        pipe = connection.PipeListener
        if type(connection.__file__) is not str:
            return False
        source = os.path.normcase(os.path.abspath(connection.__file__))
        if any(os.path.normcase(os.path.abspath(method.__code__.co_filename)) != source
               for method in (connection.Listener.__init__, pipe.__init__, pipe._new_handle)):
            return False
        expected = (_LocalCandidateService.start.__code__, connection.Listener.__init__.__code__,
                    pipe.__init__.__code__, pipe._new_handle.__code__)
        if tuple(item.tb_frame.f_code for item in trace) != expected:
            return False
        constructor, create = trace[2].tb_frame, trace[3].tb_frame
        instance = constructor.f_locals.get('self')
        if (instance is not create.f_locals.get('self') or
                create.f_locals.get('first') is not True or
                constructor.f_locals.get('address') != address or
                instance.__dict__.get('_address') != address or
                '_handle_queue' in instance.__dict__):
            return False
        instructions = list(dis.get_instructions(pipe._new_handle))
        loads = [index for index, item in enumerate(instructions)
                 if item.opname in ('LOAD_ATTR', 'LOAD_METHOD') and item.argval == 'CreateNamedPipe']
        if len(loads) != 1:
            return False
        index = loads[0]
        if index == 0 or instructions[index - 1].opname != 'LOAD_GLOBAL' or instructions[index - 1].argval != '_winapi':
            return False
        calls = [item for item in instructions[index + 1:]
                 if item.opname in ('CALL', 'CALL_FUNCTION', 'CALL_METHOD')]
        return bool(calls and calls[0].offset == trace[3].tb_lasti and calls[0].arg == 8)
    except Exception:
        return False


class _LocalCandidateService:
    """One caller-owned owner and local reference-only service; no caller authentication.

    The caller retains this object, especially after HOLD. Its own owner is not
    started or closed here; process creation and source verification are separate.
    """
    def __init__(self, owner, pipe_name):
        self._owner, self._name = owner, pipe_name
        self._listener = self._thread = self._wake_thread = None
        self._listener_closed = False
        self._listener_closing = False
        self._pipe_lifecycle_lock = threading.Lock()
        self._listener_creation_unknown = False
        self._uncertain_connection = self._uncertain_wake_connection = None
        self._accept_ownership_unknown = self._wake_ownership_unknown = False
        self._stop, self._lock, self._failed = threading.Event(), threading.Lock(), False
        self._serve_done, self._start_uncertain = threading.Event(), False
        self._wake_done, self._wake_start_uncertain = None, False

    def start(self):
        from multiprocessing.connection import Listener
        with self._lock:
            if self._thread is not None or self._stop.is_set():
                raise RuntimePreflightRefused('runtime_process_preflight_refused') from None
            try:
                # A constructor can allocate a native handle before it raises.
                self._listener_creation_unknown = True
                self._listener = Listener(address=self._name, family='AF_PIPE', authkey=None)
                self._listener_creation_unknown = False
                self._thread = threading.Thread(target=self._serve, name='mind-local-candidates', daemon=False)
                self._thread.start()
            except BaseException as exc:
                if self._listener_creation_unknown and _first_pipe_create_denied_without_handle(exc, self._name):
                    self._listener_creation_unknown = False
                # Once Thread.start has been entered, native execution may
                # have begun even if Python never observed the start event.
                # Retain the listener and owner until the worker proves exit.
                if self._thread is not None:
                    self._start_uncertain = True
                elif self._listener is not None:
                    self._listener.close()
                    self._listener_closed = True
                self._stop.set()
                if not isinstance(exc, Exception):
                    raise
                raise RuntimePreflightRefused('runtime_process_preflight_refused') from None

    def _serve(self):
        try:
            while not self._stop.is_set():
                connection = None
                try:
                    connection = self._listener.accept()
                except BaseException:
                    self._failed = True
                    # The Windows listener may have detached a raw handle
                    # before accept raises, so listener.close is not proof.
                    self._accept_ownership_unknown = True
                    break
                try:
                    if self._stop.is_set():
                        break
                    if connection.poll(1):
                        request = _local_candidate_request(_local_candidate_json(connection.recv_bytes(_LOCAL_CANDIDATE_MAX_BYTES)))
                        if request['schema'] == 'wd.mind-pipe-request.v2':
                            response = self._owner.located_candidates(dict(request))
                        else:
                            response = self._owner.candidates(dict(request))
                        response = _local_candidate_reply(response, request)
                        if not self._stop.is_set():
                            connection.send_bytes(_local_candidate_encode(response))
                except Exception:
                    pass
                except BaseException:
                    self._failed = True
                    break
                finally:
                    if connection is not None:
                        try:
                            connection.close()
                        except BaseException:
                            self._failed = True
                            self._uncertain_connection = connection
                if self._uncertain_connection is not None:
                    break
        finally:
            with self._pipe_lifecycle_lock:
                self._listener_closing = True
                try:
                    self._listener.close()
                except BaseException:
                    self._failed = True
                else:
                    self._listener_closed = True
                finally:
                    self._serve_done.set()

    def status(self):
        with self._lock:
            if ((self._wake_thread is not None and
                     (self._wake_done is None or not self._wake_done.is_set() or self._wake_thread.is_alive()))
                    or self._listener_creation_unknown
                    or self._accept_ownership_unknown or self._wake_ownership_unknown
                    or self._uncertain_connection is not None
                    or self._uncertain_wake_connection is not None
                    or (self._listener is not None and not self._listener_closed
                        and (self._thread is None or self._serve_done.is_set())) or
                    (self._start_uncertain and not self._serve_done.is_set())):
                state = 'HOLD'
            elif self._failed:
                state = 'FAILED'
            elif self._thread is None:
                state = 'STOPPED' if self._stop.is_set() else 'NEW'
            elif self._thread.is_alive():
                state = 'HOLD' if self._stop.is_set() else 'RUNNING'
            else:
                state = 'STOPPED'
        return {'state':state, 'authority':'none', 'caller_authentication':'UNKNOWN'}

    def close(self, *, timeout=0):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 90:
            raise RuntimePreflightRefused('runtime_process_preflight_refused') from None
        deadline = time.monotonic() + float(timeout)
        self._stop.set()
        if not self._lock.acquire(blocking=False):
            return {'state':'HOLD', 'authority':'none', 'caller_authentication':'UNKNOWN'}
        try:
            thread = self._thread
            if thread is None:
                state = ('STOPPED' if (self._wake_thread is None or
                         (self._wake_done is not None and self._wake_done.is_set()
                          and not self._wake_thread.is_alive()))
                         and (self._listener is None or self._listener_closed)
                         and not self._listener_creation_unknown
                         and not self._accept_ownership_unknown and not self._wake_ownership_unknown
                         and self._uncertain_connection is None
                         and self._uncertain_wake_connection is None else 'HOLD')
                return {'state':state, 'authority':'none', 'caller_authentication':'UNKNOWN'}
            if self._start_uncertain and not self._serve_done.is_set() and not thread.is_alive():
                return {'state':'HOLD', 'authority':'none', 'caller_authentication':'UNKNOWN'}
            if (self._wake_thread is not None and self._wake_done.is_set()
                    and not self._wake_thread.is_alive()):
                self._wake_thread, self._wake_done, self._wake_start_uncertain = None, None, False
            if self._wake_start_uncertain and not self._wake_done.is_set():
                return {'state':'HOLD', 'authority':'none', 'caller_authentication':'UNKNOWN'}
            if thread.is_alive() and self._wake_thread is None:
                done = threading.Event()
                try:
                    wake = threading.Thread(target=lambda: self._wake_then_done(done),
                                            name='mind-local-wake', daemon=True)
                except Exception:
                    # Construction failed before Thread.start was called.
                    # A later close may retry without abandoning a worker.
                    return {'state':'HOLD', 'authority':'none', 'caller_authentication':'UNKNOWN'}
                self._wake_thread, self._wake_done = wake, done
                try:
                    wake.start()
                except BaseException as exc:
                    # is_alive() and the Python _started event cannot prove
                    # whether native start raced the exception. Retain the
                    # wake and listener until the target itself finishes.
                    self._wake_start_uncertain = True
                    if not isinstance(exc, Exception):
                        raise
                    return {'state':'HOLD', 'authority':'none', 'caller_authentication':'UNKNOWN'}
            # Take the wake thread and its completion event as one generation.
            # A concurrent close may retire and replace this pair while we
            # wait, so it must be checked again under the same lock below.
            wake_thread, wake_done = self._wake_thread, self._wake_done
        finally:
            self._lock.release()
        if thread.is_alive():
            thread.join(max(0, deadline - time.monotonic()))
        if wake_done is not None and not wake_done.is_set():
            wake_done.wait(max(0, deadline - time.monotonic()))
        if wake_thread is not None and wake_done is not None and wake_done.is_set() and wake_thread.is_alive():
            join = getattr(wake_thread, 'join', None)
            if callable(join) and deadline > time.monotonic():
                join(max(0, deadline - time.monotonic()))
        if not self._lock.acquire(blocking=False):
            return {'state':'HOLD', 'authority':'none', 'caller_authentication':'UNKNOWN'}
        try:
            if (self._thread is not thread or self._wake_thread is not wake_thread
                    or self._wake_done is not wake_done):
                state = 'HOLD'
            else:
                state = ('HOLD' if thread.is_alive() or not self._serve_done.is_set() or not self._listener_closed
                         or (wake_thread is not None and (wake_done is None or not wake_done.is_set()
                                                          or wake_thread.is_alive()))
                         or self._accept_ownership_unknown or self._wake_ownership_unknown
                         or self._uncertain_connection is not None
                         or self._uncertain_wake_connection is not None
                         else 'FAILED' if self._failed else 'STOPPED')
        finally:
            self._lock.release()
        return {'state':state, 'authority':'none',
                'caller_authentication':'UNKNOWN'}

    def _wake_then_done(self, done):
        try:
            self._wake()
        finally:
            done.set()

    def _wake(self):
        from multiprocessing.connection import Client
        connection = None
        try:
            # Serialize connection construction against listener close. Never
            # hold the service state lock across a potentially blocking call.
            with self._pipe_lifecycle_lock:
                if self._listener_closing:
                    return
                connection = Client(address=self._name, family='AF_PIPE', authkey=None)
            connection.close()
        except BaseException:
            # Client may have allocated a native handle even before raising.
            # If it returned an object, retain it when close is uncertain.
            self._failed = True
            self._wake_ownership_unknown = True
            if connection is not None:
                self._uncertain_wake_connection = connection


def create_local_candidate_service(*, enabled=False, owner=None, pipe_name=None):
    """Prepare a local service; it never authenticates peers or serves message text."""
    if (enabled is not True or os.name != 'nt' or not _local_candidate_pipe_name_valid(pipe_name)
            or owner is None or not callable(getattr(owner, 'candidates', None))):
        raise RuntimePreflightRefused('runtime_process_preflight_refused') from None
    return _LocalCandidateService(owner, pipe_name)


# Fixed stage tokens for a refused local candidate query; never content or an OS message.
_LOCAL_CANDIDATE_REFUSALS = frozenset({'PIPE_ACCESS_DENIED', 'PIPE_NOT_FOUND', 'PIPE_CONNECT_FAILED',
                                       'PIPE_IO', 'PIPE_REPLY_TIMEOUT', 'PIPE_REPLY_INVALID'})


class _LocalCandidateQuery:
    """A retained bounded-wait client; received hashes remain untrusted proposals."""
    def __init__(self, name, request):
        self._name, self._request = name, request
        self._thread = None
        self._done, self._lock = threading.Event(), threading.Lock()
        self._result, self._timed_out, self._start_uncertain, self._closed = None, False, False, False
        self._refusal = None

    def start(self):
        with self._lock:
            if self._thread is not None or self._closed:
                raise RuntimePreflightRefused('runtime_process_preflight_refused') from None
            self._thread = threading.Thread(target=self._run, name='mind-local-query', daemon=False)
            try:
                self._thread.start()
            except BaseException:
                self._start_uncertain = True
                self._timed_out = True
                raise

    def _run(self):
        from multiprocessing.connection import Client
        result, refusal = None, 'PIPE_IO'
        try:
            # The stage of a refusal is kept as a fixed token only (C15): a lane
            # read inside a sandbox must say whether it could reach the pipe at all.
            try:
                connection = Client(address=self._name, family='AF_PIPE', authkey=None)
            except PermissionError:
                refusal = 'PIPE_ACCESS_DENIED'
                raise
            except FileNotFoundError:
                refusal = 'PIPE_NOT_FOUND'
                raise
            except BaseException:
                refusal = 'PIPE_CONNECT_FAILED'
                raise
            with connection:
                connection.send_bytes(_local_candidate_encode(self._request))
                if not connection.poll(90):
                    refusal = 'PIPE_REPLY_TIMEOUT'
                    raise ValueError('timeout')
                raw = connection.recv_bytes(_LOCAL_CANDIDATE_MAX_BYTES)
                refusal = 'PIPE_REPLY_INVALID'
                response = _local_candidate_reply(_local_candidate_json(raw), self._request)
                result = _local_candidate_encode(response)
        except BaseException:
            pass
        finally:
            with self._lock:
                if not self._timed_out:
                    self._result = result
                    self._refusal = None if result is not None else refusal
            self._done.set()

    def wait(self, *, timeout):
        if (self._thread is None or type(timeout) not in (int, float) or not math.isfinite(timeout)
                or not 0 <= timeout <= 90):
            raise RuntimePreflightRefused('runtime_process_preflight_refused') from None
        complete = self._done.wait(float(timeout))
        with self._lock:
            if not complete:
                self._timed_out = True
            raw = None if self._timed_out else self._result
            status = ('HOLD' if self._start_uncertain and not self._done.is_set() else
                      'TIMEOUT' if self._timed_out else ('REFUSED' if raw is None else _local_candidate_json(raw)['status']))
            refusal = self._refusal if status == 'REFUSED' and self._refusal in _LOCAL_CANDIDATE_REFUSALS else None
        return {'status':status, 'authority':'none', 'sender_authentication':'UNKNOWN',
                'source_verification':'UNVERIFIED', 'candidate_frame':raw, 'refusal':refusal}

    def _thread_exited_locked(self):
        # done is set in the worker's finally, before native thread exit. An
        # ambiguous start with no identity is not evidence of non-existence.
        if self._thread is None or not self._done.is_set():
            return False
        try:
            return self._thread.ident is not None and not self._thread.is_alive()
        except Exception:
            return False

    def status(self):
        with self._lock:
            state = (('STOPPED' if self._closed else 'NEW') if self._thread is None else
                     'STOPPED' if self._thread_exited_locked() else
                     'HOLD' if self._done.is_set() or self._timed_out or self._start_uncertain else 'RUNNING')
        return {'state':state, 'authority':'none', 'sender_authentication':'UNKNOWN'}

    def close(self, *, timeout=0):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 90:
            raise RuntimePreflightRefused('runtime_process_preflight_refused') from None
        with self._lock:
            self._closed = True
            if self._thread is None:
                return {'state':'STOPPED', 'authority':'none', 'sender_authentication':'UNKNOWN'}
            self._timed_out = True
            self._result = None
        deadline = time.monotonic() + float(timeout)
        complete = self._done.wait(max(0.0, deadline - time.monotonic()))
        join_failed = False
        if complete:
            # Never join under _lock: the worker may still need that lock.
            with self._lock:
                worker = self._thread
                already_exited = self._thread_exited_locked()
            if not already_exited and worker is not None and worker is not threading.current_thread():
                try:
                    if worker.ident is not None:
                        worker.join(max(0.0, deadline - time.monotonic()))
                except Exception:
                    join_failed = True
        with self._lock:
            stopped = not join_failed and self._thread_exited_locked()
        return {'state':'STOPPED' if stopped else 'HOLD', 'authority':'none',
                'sender_authentication':'UNKNOWN'}


def create_local_candidate_query(*, enabled=False, pipe_name=None, request=None):
    """Prepare a local lookup; caller must independently verify returned source rows."""
    try:
        if enabled is not True or os.name != 'nt' or not _local_candidate_pipe_name_valid(pipe_name):
            raise ValueError('input')
        return _LocalCandidateQuery(pipe_name, _local_candidate_request(request))
    except Exception:
        raise RuntimePreflightRefused('runtime_process_preflight_refused') from None


def verify_runtime_process_preflight(*, interpreter, interpreter_sha256, bootstrap, bootstrap_sha256,
                                     bundle_root, deployment_manifest_sha256, verifier, verifier_sha256,
                                     runtime_root, runtime_manifest,
                                     runtime_manifest_sha256, generation, revision):
    """Verify external pins and runtime tree; return evidence, not permission to run.

    All pins must come from an independent manifest/authority, not the runtime
    tree being checked. Interpreter-tree and native-library closure remain the
    caller's independent responsibility. No query or private message is read.
    """
    result = None
    try:
        bundle = Path(bundle_root)
        if not bundle.is_absolute() or ".." in bundle.parts:
            raise ValueError("bundle")
        manifest_bytes = _read_pinned_file(bundle / "deployment-manifest.json", deployment_manifest_sha256,
                                           limit=4 * 1024 * 1024, retain=True)
        manifest = json.loads(manifest_bytes.decode("utf-8-sig"), object_pairs_hook=_unique_json_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")))
        if (type(manifest) is not dict or manifest.get("schema_version") != 1
                or type(manifest.get("files")) is not dict
                or type(manifest["files"].get(_VERIFIER_RELATIVE)) is not str
                or manifest["files"][_VERIFIER_RELATIVE].lower() != _PACKAGED_VERIFIER_SHA256):
            raise ValueError("verifier_not_manifest_bound")
        packaged_verifier = bundle.joinpath(*_VERIFIER_RELATIVE.split("/"))
        if os.path.normcase(os.path.abspath(os.fspath(verifier))) != os.path.normcase(os.path.abspath(packaged_verifier)):
            raise ValueError("verifier_not_packaged")
        if (type(verifier_sha256) is not str or
                not hmac.compare_digest(verifier_sha256, _PACKAGED_VERIFIER_SHA256)):
            raise ValueError("unapproved_verifier")
        # Wrong bootstrap/interpreter/verifier pins refuse before verifier code.
        _read_pinned_file(interpreter, interpreter_sha256, limit=_MAX_INTERPRETER)
        _read_pinned_file(bootstrap, bootstrap_sha256, limit=_MAX_CODE)
        verifier_bytes = _read_pinned_file(verifier, verifier_sha256, limit=_MAX_CODE, retain=True)
        namespace = {"__name__": "_wd_pinned_runtime_manifest", "__file__": str(verifier)}
        exec(compile(verifier_bytes, "<pinned-runtime-manifest>", "exec"), namespace)
        verify = namespace.get("verify_runtime")
        if not callable(verify):
            raise ValueError("verifier")
        evidence = verify(runtime_root, runtime_manifest, runtime_manifest_sha256,
                          expected_generation=generation, expected_revision=revision)
        if (type(evidence) is not dict or evidence.get("manifest_sha256") != runtime_manifest_sha256
                or evidence.get("generation") != generation or evidence.get("revision") != revision
                or type(evidence.get("file_count")) is not int or type(evidence.get("total_bytes")) is not int):
            raise ValueError("evidence")
        result = {"schema": "wd.mind-runtime-process-preflight.v1", "status": "VERIFIED_PREFLIGHT_ONLY",
                  "process_started": False, "model_loaded": False, "network_isolation": "UNKNOWN",
                  "native_closure": "UNKNOWN", "same_user_race": "UNKNOWN",
                  "deployment_manifest_sha256": deployment_manifest_sha256,
                  "runtime_manifest_sha256": runtime_manifest_sha256,
                  "generation": generation, "revision": revision,
                  "file_count": evidence["file_count"], "total_bytes": evidence["total_bytes"]}
    except Exception:
        pass
    if result is None:
        raise RuntimePreflightRefused("runtime_process_preflight_refused") from None
    return result


if __name__ == "__main__":
    if "tools.wd_mind_runtime_process" not in sys.modules:
        sys.modules["tools.wd_mind_runtime_process"] = sys.modules[__name__]
    raise SystemExit(_guardian_entrypoint(sys.argv[1:], sys.stdin, sys.stdout))
