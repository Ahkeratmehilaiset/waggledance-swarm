# SPDX-License-Identifier: BUSL-1.1
# Based on sandbox/mind/step2/runtime_launcher.py at Git commit e98c5aef5bd3bd66c61e5692e738f17a4ecf6beb; verifier/import paths adapted.
"""Bounded Windows Q4 launcher; not an installer, identity or isolation boundary.

All trust pins and paths come from the caller, never from an environment variable.
The interpreter starts a fixed isolated stdlib trampoline which waits for a byte
before running the verified bootstrap. The parent sends that byte only after
assigning the process to a non-terminating Windows job. This does not protect
against malicious native code, independently breakaway jobs, hostile same-user
mutation, reparse changes, native DLL substitution, or file-check/use races.
The manifest verifier, caller, actual ACLs, network isolation and all runtime
assets must be independently trusted.  No caller identity is authenticated.

Historical force-stop launch entrypoints are deferred and refuse immediately.
Hash preflight, Windows process creation and job assignment may block; no hard
wall-clock bound is claimed for the whole call. Output is an intentionally lossy
projection; even digests and statuses are data channels, not full privacy.

start_e5_worker_graceful is a separate, default-off cooperative path. Its Job
does not kill on close; a pinned non-daemon owner retains a timed-out child in
HOLD. This bounds pipe waits, not the lifetime of a stuck native operation.
"""

import asyncio
import ast
import _imp
import ctypes
from ctypes import wintypes
import hashlib
import hmac
import importlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import stat
import struct
import threading
import time


class LaunchRefused(ValueError):
    """Fixed refusal code only; never include raw data or paths."""


_HEX = re.compile(r"[0-9a-f]{64}\Z")
BULK_CONTRACT = b'{"frame_bytes":65536,"inflight_bytes":65540,"nonce_limit":8192,"schema":"wd.mind-e5-bulk-contract.v1","stdout_bytes":67108864}'
BULK_CONTRACT_SHA256 = '61998bfeea1342988301f30f21d9781017249ef66761ec81874e5cc34633bc08'
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_REASON = re.compile(r"[a-z0-9_]{1,64}\Z")
_TIME = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?(?:Z|\+00:00)\Z")
_REF = re.compile(r"(?:[a-z][a-z0-9_-]{0,15}:)?[0-9a-f]{40,64}\Z")
_KEYS = ("runtime-root", "runtime-manifest", "runtime-manifest-sha256", "runtime-generation",
         "runtime-revision", "mutex-sha256", "memory", "descriptor", "descriptor-sha256", "lock-file", "lock-timeout")
_PUBLIC_KEYS = tuple(key for key in _KEYS if key != "mutex-sha256")
_WORKER_ONLY_KEYS = tuple(key for key in _PUBLIC_KEYS if key not in ('memory', 'descriptor', 'descriptor-sha256'))
_PATHS = ("runtime-root", "runtime-manifest", "memory", "descriptor", "lock-file")
_MAX_QUERY = 65536
_MAX_OUTPUT = 262144
_SHARED_SCOPE = "wd.q4-shared-projected-events.v1"
# Fixed audit root for the reader-slot sentinel and cooperating owner marker.
# Owner startup only opens an existing marker; it never provisions one.
_AUDIT_ROOT = Path(r"C:\Python\project2\.codex-audit")
_OWNER_RESERVATION_NAME = 'mind-owner-reservation.v1'
_OWNER_RESERVATION_MAGIC = b'WD_MIND_OWNER_V1\n'


class _LocalOwnerReservation:
    """A crash-persistent slot for cooperating owners, not a migration proof."""

    def __init__(self, root, path, fd, root_id, file_id):
        self.root, self.path, self.fd = root, path, fd
        self.root_id, self.file_id = root_id, file_id

    def _check(self):
        from tools import wd_mind_absent_generation_prefix_readonly as absent
        absent._plain(self.root, directory=True)
        absent._plain(self.path, directory=False)
        root = os.lstat(self.root)
        path = os.lstat(self.path)
        opened = os.fstat(self.fd)
        if ((root.st_dev, root.st_ino) != self.root_id
                or (path.st_dev, path.st_ino) != self.file_id
                or (opened.st_dev, opened.st_ino) != self.file_id
                or path.st_nlink != 1 or opened.st_nlink != 1
                or path.st_size != len(_OWNER_RESERVATION_MAGIC) + 1
                or opened.st_size != len(_OWNER_RESERVATION_MAGIC) + 1):
            raise LaunchRefused('owner_reservation_changed')

    def _read(self):
        os.lseek(self.fd, 0, os.SEEK_SET)
        return os.read(self.fd, len(_OWNER_RESERVATION_MAGIC) + 2)

    def abandon(self):
        """Leave RESERVED on disk; release only this process's kernel handle."""
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def release_clean(self, *, service_stopped, owner_stopped, owner_owned, control_stopped):
        if not (service_stopped is True and owner_stopped is True
                and owner_owned is False and control_stopped is True):
            raise LaunchRefused('owner_cleanup_unproven')
        try:
            self._check()
            if self._read() != _OWNER_RESERVATION_MAGIC + b'R':
                raise LaunchRefused('owner_reservation_changed')
            os.lseek(self.fd, len(_OWNER_RESERVATION_MAGIC), os.SEEK_SET)
            if os.write(self.fd, b'C') != 1:
                raise LaunchRefused('owner_reservation_write')
            os.fsync(self.fd)
            self._check()
            if self._read() != _OWNER_RESERVATION_MAGIC + b'C':
                raise LaunchRefused('owner_reservation_changed')
            import msvcrt
            os.lseek(self.fd, 0, os.SEEK_SET)
            msvcrt.locking(self.fd, msvcrt.LK_UNLCK, len(_OWNER_RESERVATION_MAGIC) + 1)
            os.close(self.fd)
            self.fd = -1
        except BaseException:
            raise LaunchRefused('owner_reservation_release_failed') from None


class _LocalAdmission:
    """One owner/factory capability backed by a held cooperating reservation.

    This does not exclude legacy interpreters or authenticate callers. A
    missing or uncertain worker remains HOLD; no recovery is attempted here.
    """
    def __init__(self, guard, cleanup_evidence=None):
        if type(guard) is not _LocalOwnerReservation:
            raise LaunchRefused('owner_admission_refused')
        self._guard, self._pid, self._lock = guard, os.getpid(), threading.Lock()
        self._guard_anchor, self._cleanup_evidence = guard, cleanup_evidence
        self._owner = self._factory = self._worker = None
        self._spawning = self._unknown = self._closed = False
        self._pre_resource_refusal = False

    def _check_locked(self):
        if (self._closed or self._unknown or self._guard is not self._guard_anchor
                or self._pid != os.getpid() or self._guard.fd < 0):
            raise LaunchRefused('owner_admission_refused')
        self._guard._check()
        if self._guard._read() != _OWNER_RESERVATION_MAGIC + b'R':
            raise LaunchRefused('owner_admission_refused')

    def bind_owner(self, owner):
        with self._lock:
            self._check_locked()
            if owner is None or (self._owner is not None and self._owner is not owner):
                raise LaunchRefused('owner_admission_refused')
            self._owner = owner

    def bind_factory(self, owner, factory):
        with self._lock:
            self._check_locked()
            if (owner is not self._owner or owner is None or factory is None
                    or (self._factory is not None and self._factory is not factory)):
                raise LaunchRefused('owner_admission_refused')
            self._factory = factory

    def assert_owner(self, owner):
        with self._lock:
            self._check_locked()
            if owner is None or owner is not self._owner:
                raise LaunchRefused('owner_admission_refused')

    def _worker_finished_locked(self):
        if self._worker is None:
            return True
        worker = self._worker
        try:
            from tools import wd_mind_e5_lifecycle as lifecycle
            if (type(worker) is not lifecycle._Owner or worker._keeper.is_alive()
                    or worker._require_spawn_attempt is not True):
                return False
            status = worker.status()
            return (type(status) is dict and status.get('owned') is False
                    and ((status.get('state') == 'CANCELLED' and worker._spawn_state == 'NOT_ENTERED')
                         or (status.get('state') in ('EXITED', 'FAILED') and worker._spawn_state == 'BOUND')))
        except BaseException:
            return False

    def begin_worker(self, owner, factory):
        with self._lock:
            self._check_locked()
            if (owner is not self._owner or factory is not self._factory or factory is None
                    or self._spawning or not self._worker_finished_locked()):
                raise LaunchRefused('owner_admission_refused')
            self._worker = None
            self._spawning = True
            self._pre_resource_refusal = False

    def note_pre_resource_refusal(self, owner, factory):
        """Record a refusal observed before the lifecycle reservation was entered."""
        with self._lock:
            self._check_locked()
            if (owner is not self._owner or factory is not self._factory
                    or not self._spawning or self._worker is not None):
                self._unknown = True
                raise LaunchRefused('owner_admission_refused')
            self._pre_resource_refusal = True

    def capture_worker(self, owner, factory, worker):
        with self._lock:
            self._check_locked()
            from tools import wd_mind_e5_lifecycle as lifecycle
            if (owner is not self._owner or factory is not self._factory or not self._spawning
                    or self._worker is not None or self._pre_resource_refusal
                    or type(worker) is not lifecycle._Owner
                    or worker._require_spawn_attempt is not True):
                self._unknown = True
                raise LaunchRefused('owner_admission_refused')
            self._worker = worker

    def assert_worker_start(self, owner, factory):
        with self._lock:
            self._check_locked()
            if (owner is not self._owner or factory is not self._factory
                    or factory is None or not self._spawning or self._worker is not None):
                raise LaunchRefused('owner_admission_refused')

    def finish_worker_start(self, owner, factory):
        with self._lock:
            if (owner is not self._owner or factory is not self._factory
                    or not self._spawning
                    or (self._worker is None and self._pre_resource_refusal is not True)):
                self._unknown = True
            self._spawning = False
            self._pre_resource_refusal = False

    def quiescent(self):
        with self._lock:
            try:
                self._check_locked()
                return not self._spawning and self._worker_finished_locked()
            except BaseException:
                return False

    def release_clean(self):
        """Additional to the caller's separately established cleanup evidence."""
        with self._lock:
            self._check_locked()
            if (type(self._cleanup_evidence) is not dict or self._cleanup_evidence.get('clean') is not True
                    or self._spawning or not self._worker_finished_locked()):
                raise LaunchRefused('owner_admission_refused')
            self._guard.release_clean(service_stopped=True, owner_stopped=True,
                                      owner_owned=False, control_stopped=True)
            self._closed = True

    def abandon(self):
        with self._lock:
            self._closed = True
            self._guard.abandon()


def _open_owned_marker(path):
    """Open an existing marker, denying other write/delete handles on Windows."""
    if os.name != 'nt':
        raise OSError('owner_reservation_platform')
    import msvcrt
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                     wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                     wintypes.HANDLE]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateFileW(os.fspath(path), 0x80000000 | 0x40000000,
                                  0x00000001, None, 3, 0x00200000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise OSError(ctypes.get_last_error(), 'owner_reservation_open')
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDWR | getattr(os, 'O_BINARY', 0)
                                    | getattr(os, 'O_NOINHERIT', 0))
    except BaseException:
        kernel32.CloseHandle(handle)
        raise


def _reserve_local_owner(root):
    """Reserve only an existing valid CLEAR sentinel; never provision or recover it.

    This serializes cooperating owners only.  Absence of an older unguarded
    owner must be independently established before provisioning; it is UNKNOWN.
    """
    fd = None
    refusal = 'owner_reservation_hold'
    try:
        if os.name != 'nt' or not isinstance(root, Path) or not root.is_absolute():
            raise LaunchRefused('owner_reservation_invalid')
        from tools import wd_mind_absent_generation_prefix_readonly as absent
        import msvcrt
        path = root / _OWNER_RESERVATION_NAME
        absent._plain(root, directory=True)
        try:
            os.lstat(path)
        except FileNotFoundError:
            refusal = 'owner_reservation_missing'
            raise
        absent._plain(path, directory=False)
        root_stat, path_stat = os.lstat(root), os.lstat(path)
        if not root_stat.st_ino or not path_stat.st_ino or path_stat.st_nlink != 1:
            raise LaunchRefused('owner_reservation_invalid')
        fd = _open_owned_marker(path)
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, len(_OWNER_RESERVATION_MAGIC) + 1)
        except OSError:
            # An unavailable byte lock may have several causes; it is not
            # proof that a live owner holds it. Never read an unlocked marker.
            refusal = 'owner_reservation_lock_unavailable'
            raise
        guard = _LocalOwnerReservation(root, path, fd,
            (root_stat.st_dev, root_stat.st_ino), (path_stat.st_dev, path_stat.st_ino))
        guard._check()
        marker = guard._read()
        if marker == _OWNER_RESERVATION_MAGIC + b'R':
            refusal = 'owner_reservation_reserved'
            raise LaunchRefused('owner_reservation_hold')
        if marker != _OWNER_RESERVATION_MAGIC + b'C':
            raise LaunchRefused('owner_reservation_hold')
        os.lseek(fd, len(_OWNER_RESERVATION_MAGIC), os.SEEK_SET)
        if os.write(fd, b'R') != 1:
            raise LaunchRefused('owner_reservation_write')
        os.fsync(fd)
        guard._check()
        if guard._read() != _OWNER_RESERVATION_MAGIC + b'R':
            raise LaunchRefused('owner_reservation_changed')
        fd = None
        return guard
    except BaseException:
        if fd is not None:
            try: os.close(fd)
            except OSError: pass
        raise LaunchRefused(refusal) from None
_BOOTSTRAP_REFUSALS = frozenset({"isolation_required", "runtime_verification_failed", "reader_execution_failed"})
_READER_REFUSALS = frozenset({
    "stdin_unreadable", "stdin_over_budget", "stdin_not_json", "query_shape", "situation_invalid", "time_invalid",
    "descriptor_pin_invalid", "descriptor_over_budget", "descriptor_pin_mismatch", "descriptor_unreadable",
    "descriptor_scope_mismatch", "descriptor_shape", "memory_descriptor_mismatch", "memory_descriptor_unreadable",
    "memory_unreadable", "memory_descriptor_changed", "memory_dim_mismatch", "memory_no_healthy_cell",
    "encoder_identity_mismatch", "memory_scope_mismatch", "encoder_runtime_unavailable", "encoder_runtime_mismatch",
    "q4_result_shape", "runtime_revision_mismatch", "encoder_load_failed", "q4_execution_failed", "output_guarded",
    "reader_lock_invalid", "reader_busy", "reader_lock_unavailable", "runtime_unavailable",
})
_TRAMPOLINE = ("import sys,runpy; "
               "assert sys.stdin.buffer.read(1)==b'\\0'; "
               "sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name='__main__')")

_PARENT_CHILD_TRAMPOLINE = ("import runpy,sys; root,tool=sys.argv[1:3]; "
    "sys.path.insert(0,root); sys.argv=[tool]+sys.argv[3:]; runpy.run_path(tool,run_name='__main__')")


def _require_verified_parent_spawn():
    # A real inherited parent-control provider has not yet been proved.
    raise LaunchRefused('mind_e5_start_refused') from None


class _PrivateCandidateInput:
    __slots__ = ('__stream',)

    def __init__(self, stream): self.__stream = stream
    def __repr__(self): return '<PrivateMindCandidateInput>'
    def readinto(self, view): return self.__stream.readinto(view)


class _PrivateCandidateOutput:
    __slots__ = ('__stream',)

    def __init__(self, stream): self.__stream = stream
    def __repr__(self): return '<PrivateMindCandidateOutput>'
    def write(self, view): return self.__stream.write(view)
    def close(self): return self.__stream.close()


class _CandidateRelayBuffer:
    __slots__ = ('__data', 'length')

    def __init__(self): self.__data, self.length = bytearray(2049), 0
    def __repr__(self): return '<PrivateMindCandidateBuffer>'
    def remaining(self): return memoryview(self.__data)[self.length:]
    def contents(self): return memoryview(self.__data)[:self.length]


def relay_parent_candidates(*, enabled=False, source=None, destination=None, deadline_monotonic=None):
    """Frame raw candidate refs for a consumer, without granting authority.

    Synchronous I/O may block; caller must retain the whole relay worker and
    both endpoints in HOLD through uncertainty. A returned receipt only means
    the bounded bytes were framed and the output close returned before deadline.
    """
    authorized = success = False
    cancelled_type = None
    try:
        if (enabled is not True or type(source) is not _PrivateCandidateInput
                or type(destination) is not _PrivateCandidateOutput
                or type(deadline_monotonic) not in (int, float) or not math.isfinite(deadline_monotonic)
                or not time.monotonic() < deadline_monotonic <= time.monotonic() + 1800):
            raise ValueError('relay_input')
        _require_verified_parent_spawn()
        authorized = True
        data = _CandidateRelayBuffer()
        while data.length < 2049:
            if time.monotonic() >= deadline_monotonic:
                raise ValueError('deadline')
            count = source.readinto(data.remaining())
            if (time.monotonic() >= deadline_monotonic or type(count) is not int
                    or not 0 <= count <= 2049 - data.length):
                raise ValueError('relay_read')
            if count == 0:
                break
            data.length += count
        if not 1 <= data.length <= 2048:
            raise ValueError('relay_size')
        header = struct.pack('>4sI', b'MCR1', data.length)
        for part in (memoryview(header), data.contents()):
            offset = 0
            while offset < len(part):
                if time.monotonic() >= deadline_monotonic:
                    raise ValueError('deadline')
                count = destination.write(part[offset:])
                if (time.monotonic() >= deadline_monotonic or type(count) is not int
                        or not 0 < count <= len(part) - offset):
                    raise ValueError('relay_write')
                offset += count
        destination.close()
        if time.monotonic() >= deadline_monotonic:
            raise ValueError('deadline')
        success = True
    except KeyboardInterrupt:
        cancelled_type = KeyboardInterrupt
    except GeneratorExit:
        cancelled_type = GeneratorExit
    except asyncio.CancelledError:
        cancelled_type = asyncio.CancelledError
    except BaseException:
        pass
    finally:
        if authorized and not success:
            try:
                destination.close()
            except BaseException:
                pass  # Closure remains unproven; retain owner in HOLD.
        source = destination = None
    if cancelled_type is not None:
        raise cancelled_type() from None
    if not success:
        raise LaunchRefused('mind_e5_start_refused') from None
    return {'status':'RELAYED', 'authority':'none'}


def _validate_parent_pipe_fds(*fds):
    if (len(fds) != 3 or any(type(fd) is not int or fd < 0 for fd in fds)
            or any(not stat.S_ISFIFO(os.fstat(fd).st_mode) for fd in fds)):
        raise ValueError('parent_pipe')


def _verified_parent_child_command(*, role, bundle_root, expected_manifest_sha256):
    """Resolve only fixed package entries and a manifest-pinned interpreter."""
    from tools import wd_mind_full_runtime as runtime
    if role not in ('owner', 'consumer') or type(bundle_root) is not str:
        raise ValueError('parent_role')
    root = Path(bundle_root)
    runtime._bundle_inputs(root, expected_manifest_sha256)
    location = runtime.resolve_manifest_runtime_location(enabled=True, bundle_root=bundle_root,
                                                        expected_manifest_sha256=expected_manifest_sha256)
    manifest_raw = runtime.prefix._read_pinned(root / 'deployment-manifest.json', expected_manifest_sha256,
                                               runtime.prefix.MAX_CONTROL_BYTES)
    manifest = json.loads(manifest_raw.decode('utf-8-sig'), object_pairs_hook=runtime.prefix._unique_object,
                          parse_constant=runtime.reader._bad_constant)
    relative = ('tools-bootstrap/tools/wd_mind_runtime_child.py' if role == 'owner'
                else 'tools-bootstrap/tools/wd_bridge_mind_session.py')
    runtime.prefix._read_pinned(root.joinpath(*relative.split('/')), manifest['files'].get(relative), 8 * 1024 * 1024)
    entry = root.joinpath(*relative.split('/'))
    flag = '--mind-owner-child' if role == 'owner' else '--mind-consumer-child'
    return (location['interpreter'], '-I', '-S', '-B', '-c', _PARENT_CHILD_TRAMPOLINE,
            str(root / 'tools-bootstrap'), str(entry), flag)


class _ParentChildLaunch:
    """Retain a native launch reservation and its worker through uncertainty.

    A successful spawn is not a startup receipt or authenticated peer. A failed
    or interrupted Popen constructor remains HOLD/owned UNKNOWN; it is never
    killed or assumed absent. The caller owns the pipe endpoints independently.
    """
    __slots__ = ('_command', '_role', '_bundle', '_manifest', '_fds', '_deadline', '_process',
                 '_thread', '_done', '_state', '_start_confirmed', '_init_confirmed', '_abandoned',
                 '_adoption_token', '_lock')

    def __init__(self, command, role, bundle, manifest, fds, deadline):
        self._command, self._role, self._bundle, self._manifest = command, role, bundle, manifest
        self._fds, self._deadline = fds, deadline
        self._process = self._thread = None
        self._done, self._lock = threading.Event(), threading.Lock()
        self._state, self._start_confirmed, self._init_confirmed, self._abandoned = 'NEW', False, False, True
        self._adoption_token = None

    def __repr__(self):
        return '<PrivateMindParentChildLaunch>'

    def _run(self):
        try:
            if time.monotonic() >= self._deadline:
                raise ValueError('deadline')
            if _verified_parent_child_command(role=self._role, bundle_root=self._bundle,
                                              expected_manifest_sha256=self._manifest) != self._command:
                raise ValueError('pin_changed')
            _validate_parent_pipe_fds(*self._fds)
            startup = subprocess.STARTUPINFO()
            startup.lpAttributeList = {'handle_list': []}
            # Retain the Popen instance before its constructor can spawn. If the
            # constructor is interrupted, native process ownership is UNKNOWN.
            self._process = subprocess.Popen.__new__(subprocess.Popen)
            subprocess.Popen.__init__(self._process, self._command, stdin=self._fds[0], stdout=self._fds[1],
                stderr=self._fds[2], close_fds=True, startupinfo=startup,
                creationflags=subprocess.CREATE_NO_WINDOW)
            self._init_confirmed = True
            with self._lock:
                self._state = ('SPAWNED_UNVERIFIED' if self._start_confirmed and not self._abandoned else 'HOLD')
        except BaseException:
            with self._lock:
                self._state = 'HOLD'
        finally:
            self._done.set()

    def start(self):
        if not self._lock.acquire(blocking=False):
            raise LaunchRefused('mind_e5_start_refused') from None
        cancelled_type = None
        try:
            if self._state != 'NEW' or time.monotonic() >= self._deadline:
                raise ValueError('parent_start')
            # Fence before Thread.start: an uncertain start cannot claim absence.
            self._state = 'HOLD'
            self._adoption_token = object()
            self._thread = threading.Thread(target=self._run, name='mind-parent-child', daemon=False)
            self._thread.start()
            self._start_confirmed = True
        except KeyboardInterrupt:
            self._abandoned, self._state, self._adoption_token = True, 'HOLD', None
            cancelled_type = KeyboardInterrupt
        except GeneratorExit:
            self._abandoned, self._state, self._adoption_token = True, 'HOLD', None
            cancelled_type = GeneratorExit
        except asyncio.CancelledError:
            self._abandoned, self._state, self._adoption_token = True, 'HOLD', None
            cancelled_type = asyncio.CancelledError
        except BaseException:
            self._abandoned, self._state, self._adoption_token = True, 'HOLD', None
        finally:
            self._lock.release()
        if cancelled_type is not None:
            raise cancelled_type() from None
        if not self._start_confirmed:
            raise LaunchRefused('mind_e5_start_refused') from None
        return self._adoption_token

    def confirm_started(self, token):
        """Adopt only after the caller received its one-use start token."""
        if not self._lock.acquire(blocking=False):
            raise LaunchRefused('mind_e5_start_refused') from None
        try:
            if (token is None or token is not self._adoption_token or not self._start_confirmed
                    or not self._done.is_set() or not self._init_confirmed or time.monotonic() >= self._deadline):
                raise ValueError('adoption')
            self._thread.join(timeout=0)
            if self._thread.is_alive() or self._process.poll() is not None:
                raise ValueError('not_running')
            self._adoption_token, self._abandoned, self._state = None, False, 'SPAWNED_UNVERIFIED'
        except Exception:
            self._state = 'HOLD'
            raise LaunchRefused('mind_e5_start_refused') from None
        finally:
            self._lock.release()

    def status(self):
        if self._thread is not None and not self._start_confirmed and not self._done.is_set():
            active = None
        else:
            active = self._thread is not None and self._thread.is_alive()
        if not self._init_confirmed:
            return {'state':self._state, 'owned':False if self._state == 'NEW' else None,
                    'worker_active':active}
        try:
            exited = self._process.poll() is not None
        except BaseException:
            self._state = 'HOLD'
            return {'state':'HOLD', 'owned':None, 'worker_active':active}
        return {'state':'EXITED' if exited else self._state, 'owned':not exited, 'worker_active':active}

    def close(self):
        # No kill/terminate, and no release while an uncertain constructor can run.
        if not self._done.is_set() or self._thread is None:
            raise LaunchRefused('mind_e5_start_refused') from None
        self._thread.join(timeout=0)
        if self._thread.is_alive() or not self._init_confirmed:
            self._state = 'HOLD'
            raise LaunchRefused('mind_e5_start_refused') from None
        try:
            if self._process.poll() is None:
                self._state = 'HOLD'
                raise ValueError('running')
        except Exception:
            raise LaunchRefused('mind_e5_start_refused') from None
        self._state = 'EXITED'


def create_parent_child_launch(*, enabled=False, role=None, bundle_root=None, expected_manifest_sha256=None,
                               stdin_fd=None, stdout_fd=None, stderr_fd=None, deadline_monotonic=None):
    """Create an inert retained child owner; production authorization refuses."""
    result = None
    try:
        if (enabled is not True or role not in ('owner', 'consumer') or type(deadline_monotonic) not in (int, float)
                or not math.isfinite(deadline_monotonic)
                or not time.monotonic() < deadline_monotonic <= time.monotonic() + 1800):
            raise ValueError('input')
        _require_verified_parent_spawn()
        _validate_parent_pipe_fds(stdin_fd, stdout_fd, stderr_fd)
        command = _verified_parent_child_command(role=role, bundle_root=bundle_root,
                                                  expected_manifest_sha256=expected_manifest_sha256)
        result = _ParentChildLaunch(command, role, bundle_root, expected_manifest_sha256,
                                    (stdin_fd, stdout_fd, stderr_fd), float(deadline_monotonic))
    except Exception:
        pass
    if result is None:
        raise LaunchRefused('mind_e5_start_refused') from None
    return result


def _decode_owner_control_bytes(raw, expected_manifest_sha256):
    """Parse MCO1 in the isolated owner; parent provenance is separate."""
    from tools import wd_mind_full_runtime as runtime
    if (type(raw) is not bytes or len(raw) < 101 + 1 + 136
            or len(raw) > 101 + 32 + 136 + 65536 or raw[:4] != b'MCO1'):
        raise ValueError('owner_control')
    manifest_sha, policy_sha, approval_sha = raw[4:36].hex(), raw[36:68].hex(), raw[68:100].hex()
    lane_length = raw[100]
    if not 1 <= lane_length <= 32 or len(raw) < 101 + lane_length + 136:
        raise ValueError('owner_lane')
    lane = raw[101:101 + lane_length].decode('ascii')
    if lane not in runtime.reader._LANES or manifest_sha != expected_manifest_sha256:
        raise ValueError('owner_binding')
    mct = raw[101 + lane_length:]
    if len(mct) < 136 or mct[:4] != b'MCT1':
        raise ValueError('owner_mct')
    nonce = mct[40:72].hex()
    buffer = runtime._ParentControlBuffer(runtime._PrivateParentStream(io.BytesIO(mct)))
    buffer.read_from(enabled=True)
    return manifest_sha, policy_sha, approval_sha, lane, nonce, buffer


class _PrivatePairControls:
    """Opaque, one-use pair of owner and consumer control frames."""
    __slots__ = ('__bootstrap', '__metadata')

    def __init__(self, owner_control, consumer_control, metadata):
        from tools import wd_mind_full_runtime as runtime
        self.__bootstrap = runtime._ParentBootstrap((owner_control, consumer_control))
        self.__metadata = metadata

    def __repr__(self): return '<PrivateMindPairControls>'

    def metadata(self): return dict(self.__metadata)

    def take(self):
        try:
            return self.__bootstrap.take()
        except Exception:
            raise LaunchRefused('mind_e5_start_refused') from None


def _derive_parent_child_controls(*, enabled=False, control_buffer=None, expected_manifest_sha256=None):
    """Bind MCP1 to MCO1/MCC1; parent origin and operator approval remain separate."""
    output = cancelled_type = None
    try:
        from tools import wd_mind_full_runtime as runtime
        if enabled is not True or type(control_buffer) is not runtime._ParentControlBuffer:
            raise ValueError('input')
        _require_verified_parent_spawn()
        output = _derive_parent_child_controls_bytes(control_buffer.take_snapshot(), expected_manifest_sha256)
    except KeyboardInterrupt:
        cancelled_type, output = KeyboardInterrupt, None
    except GeneratorExit:
        cancelled_type, output = GeneratorExit, None
    except asyncio.CancelledError:
        cancelled_type, output = asyncio.CancelledError, None
    except BaseException:
        output = None
    finally:
        control_buffer = None
    if cancelled_type is not None:
        raise cancelled_type() from None
    if output is None:
        raise LaunchRefused('mind_e5_start_refused') from None
    return output


def _derive_parent_child_controls_bytes(raw, expected_manifest_sha256):
    from tools import wd_mind_full_runtime as runtime
    if type(raw) is not bytes or raw[:4] != b'MCP1':
        raise ValueError('parent_control')
    owner_control = b'MCO1' + raw[4:]
    manifest, policy_sha, approval_sha, lane, nonce, mct = _decode_owner_control_bytes(
        owner_control, expected_manifest_sha256)
    bootstrap = runtime._parse_parent_control(enabled=True, control_buffer=mct,
        expected_manifest_sha256=manifest, expected_identifier_policy_sha256=policy_sha)
    bound_manifest, bound_nonce, channel_key, bound_policy, _ = bootstrap.take()
    if (bound_manifest != manifest or bound_nonce.hex() != nonce or bound_policy != policy_sha):
        raise ValueError('control_binding')
    lane_bytes = lane.encode('ascii')
    consumer_control = (b'MCC1' + bytes.fromhex(manifest) + bytes.fromhex(policy_sha)
        + bytes.fromhex(approval_sha) + bound_nonce + channel_key + bytes([len(lane_bytes)]) + lane_bytes)
    return _PrivatePairControls(owner_control, consumer_control, {'manifest_sha256':manifest,
        'identifier_policy_sha256':policy_sha, 'approval_reference_sha256':approval_sha,
        'lane':lane, 'nonce':nonce})


class _TrackedParentStream:
    """Keep uncertain close operations from retrying a possibly reused FD."""
    __slots__ = ('__parent', '__fd', '__stream', '__closed')

    def __init__(self, parent, fd, stream):
        self.__parent, self.__fd, self.__stream, self.__closed = parent, fd, stream, False

    def __repr__(self): return '<PrivateMindTrackedParentStream>'
    def readinto(self, view): return self.__stream.readinto(view)
    def write(self, view): return self.__stream.write(view)

    def close(self):
        if self.__closed:
            return
        if self.__parent._fd_inflight is not None:
            raise ValueError('fd_uncertain')
        self.__parent._fd_inflight = self.__fd
        self.__stream.close()
        self.__closed = True
        self.__parent._fd_inflight = None


class _RetainedParentPair:
    """Own the actual fixed child launch/pipe graph until both exits are proven."""
    __slots__ = ('_bundle', '_manifest', '_controls', '_metadata', '_deadline', '_owner', '_consumer',
                 '_thread', '_done', '_abort', '_result', '_token', '_fds', '_streams', '_fd_inflight',
                 '_stream_cleanup_pending', '_start_confirmed')

    def __init__(self, bundle, manifest, controls, deadline):
        self._bundle, self._manifest, self._controls = bundle, manifest, controls
        self._metadata, self._deadline = controls.metadata(), deadline
        self._owner = self._consumer = self._thread = self._result = self._token = None
        self._done, self._abort, self._fds, self._streams = threading.Event(), threading.Event(), set(), []
        self._fd_inflight = None
        self._stream_cleanup_pending = False
        self._start_confirmed = False

    def __repr__(self): return '<PrivateMindRetainedParentPair>'

    def status(self):
        return {'state':'HOLD', 'owner':None if self._owner is None else self._owner.status(),
                'consumer':None if self._consumer is None else self._consumer.status(),
                'fd_ownership':'UNKNOWN' if self._fd_inflight is not None or self._stream_cleanup_pending else 'TRACKED',
                'worker_active':None if self._thread is not None and not self._start_confirmed and not self._done.is_set()
                                else self._thread is not None and self._thread.is_alive()}

    def _pipe(self):
        self._check_continue()
        if self._fd_inflight is not None:
            raise ValueError('fd_uncertain')
        self._fd_inflight = 'PIPE_CREATION'
        try:
            read_fd, write_fd = os.pipe()
        except OSError:
            self._fd_inflight = None
            raise
        self._fds.update((read_fd, write_fd))
        self._fd_inflight = None
        return read_fd, write_fd

    def _close_fd(self, fd):
        if self._fd_inflight is not None or fd not in self._fds:
            raise ValueError('fd_uncertain')
        self._fd_inflight = fd
        self._fds.remove(fd)
        os.close(fd)
        self._fd_inflight = None

    def _transfer_fd(self, fd, mode):
        if self._fd_inflight is not None or fd not in self._fds:
            raise ValueError('fd_uncertain')
        self._fd_inflight = fd
        self._fds.remove(fd)
        stream = _TrackedParentStream(self, fd, os.fdopen(fd, mode, buffering=0))
        self._streams.append(stream)
        self._fd_inflight = None
        return stream

    def _write_fd(self, fd, value):
        offset = 0
        while offset < len(value):
            self._check_continue()
            count = os.write(fd, memoryview(value)[offset:])
            if type(count) is not int or not 0 < count <= len(value) - offset:
                raise ValueError('write')
            offset += count
        self._check_continue()

    def _check_continue(self):
        if self._abort.is_set() or time.monotonic() >= self._deadline:
            raise ValueError('parent_aborted')

    def cancel(self):
        # Do not interrupt native I/O or terminate children; retain ownership.
        self._abort.set()

    def _launch(self, role, fds):
        self._check_continue()
        launch = create_parent_child_launch(enabled=True, role=role, bundle_root=self._bundle,
            expected_manifest_sha256=self._manifest, stdin_fd=fds[0], stdout_fd=fds[1], stderr_fd=fds[2],
            deadline_monotonic=self._deadline)
        if role == 'owner': self._owner = launch
        else: self._consumer = launch
        token = launch.start()
        remaining = max(0, self._deadline - time.monotonic())
        if not launch._done.wait(remaining):
            raise ValueError('launch_timeout')
        launch._thread.join(timeout=max(0, self._deadline - time.monotonic()))
        self._check_continue()
        launch.confirm_started(token)

    def _read_result(self, fd):
        result = bytearray()
        while len(result) <= 16384:
            self._check_continue()
            part = os.read(fd, min(4096, 16385 - len(result)))
            self._check_continue()
            if not part:
                break
            result.extend(part)
        if not 0 < len(result) <= 16384:
            raise ValueError('result_size')
        return bytes(result)

    def _validated_result(self, raw, fleet_sha):
        from tools import wd_mind_full_runtime as runtime
        result = json.loads(raw.decode('utf-8'), object_pairs_hook=runtime.prefix._unique_object,
                            parse_constant=runtime.reader._bad_constant)
        expected_keys = {'schema','authority','lane','fleet_sha256','sender_authentication',
                         'index_match','same_user_aba','current_status_freshness','results'}
        if (type(result) is not dict or set(result) != expected_keys
                or result['schema'] != 'wd.mind-flat-verified-session.v1' or result['authority'] != 'none'
                or result['lane'] != self._metadata['lane'] or result['fleet_sha256'] != fleet_sha
                or result['sender_authentication'] != 'UNKNOWN' or result['index_match'] != 'UNKNOWN'
                or result['same_user_aba'] != 'UNKNOWN' or result['current_status_freshness'] != 'UNKNOWN'
                or type(result['results']) is not list
                or not 1 <= len(result['results']) <= 3):
            raise ValueError('result_binding')
        for hit in result['results']:
            if (type(hit) is not dict or set(hit) != {'text','source_event_sha256','event_time_utc_floor',
                    'source_agent','event_age_seconds_upper_bound'} or type(hit['text']) is not str
                    or not _HEX.fullmatch(hit['source_event_sha256']) or type(hit['event_age_seconds_upper_bound']) is not int
                    or hit['event_age_seconds_upper_bound'] < 0):
                raise ValueError('result_hit')
            age = (runtime.reader.dt.datetime.now(runtime.reader.dt.timezone.utc)
                   - runtime.reader._utc(hit['event_time_utc_floor'])).total_seconds()
            if age < 0:
                raise ValueError('result_hit')
            hit['event_age_seconds_upper_bound'] = max(hit['event_age_seconds_upper_bound'], math.ceil(age))
        return runtime.reader._canonical(result)

    def _run_pipeline(self):
        from tools import wd_mind_full_runtime as runtime
        self._check_continue()
        controls = runtime._bundle_inputs(Path(self._bundle), self._manifest)
        fleet_sha = hashlib.sha256(controls[0]).hexdigest()
        roster = json.loads(controls[1].decode('utf-8'), object_pairs_hook=runtime.prefix._unique_object)
        lanes = {entry['agent'] for entry in roster['lanes']} | {roster['tools_supervisor']['agent']}
        if self._metadata['lane'] not in lanes:
            raise ValueError('lane')
        runtime.create_operator_pinned_policy_anchor(enabled=True, bundle_root=self._bundle,
            expected_manifest_sha256=self._manifest, operator_policy_sha256=self._metadata['identifier_policy_sha256'],
            operator_approval_reference_sha256=self._metadata['approval_reference_sha256'])
        owner_r, owner_w = self._pipe()
        control_r, control_w = self._pipe()
        data_r, data_w = self._pipe()
        candidate_r, candidate_w = self._pipe()
        result_r, result_w = self._pipe()
        self._launch('owner', (owner_r, data_w, candidate_w))
        self._check_continue()
        self._launch('consumer', (data_r, result_w, control_r))
        for fd in (owner_r, data_w, candidate_w, data_r, result_w, control_r):
            self._close_fd(fd)
        owner_control, consumer_control = self._controls.take()
        self._write_fd(owner_w, owner_control)
        self._close_fd(owner_w)
        self._write_fd(control_w, consumer_control)
        source = self._transfer_fd(candidate_r, 'rb')
        destination = self._transfer_fd(control_w, 'wb')
        relay_parent_candidates(enabled=True, source=_PrivateCandidateInput(source),
            destination=_PrivateCandidateOutput(destination), deadline_monotonic=self._deadline)
        self._check_continue()
        source.close()
        raw_result = self._read_result(result_r)
        self._close_fd(result_r)
        result = self._validated_result(raw_result, fleet_sha)
        for launch in (self._owner, self._consumer):
            remaining = self._deadline - time.monotonic()
            if remaining <= 0 or launch._process.wait(timeout=remaining) != 0:
                raise ValueError('child_exit')
        return result

    def _release_children(self):
        for launch in (self._owner, self._consumer):
            if launch is None:
                continue
            while True:
                status = launch.status()
                if status['state'] == 'NEW':
                    break
                if status['state'] == 'EXITED' and status['worker_active'] is False:
                    try:
                        launch.close()
                        break
                    except BaseException:
                        pass
                time.sleep(0.25)  # Unknown/running child remains retained; never kill it.

    def _run(self):
        try:
            value = self._run_pipeline()
            if not self._abort.is_set():
                self._result = value
        except BaseException:
            self.cancel()
            self._result = None
        finally:
            if self._fd_inflight is None:
                for stream in self._streams:
                    self._stream_cleanup_pending = True
                    try:
                        stream.close()
                    except BaseException:
                        self.cancel()
                        self._result = None
                        break
                    self._stream_cleanup_pending = False
                if self._fd_inflight is None and not self._stream_cleanup_pending:
                    for fd in tuple(self._fds):
                        try: self._close_fd(fd)
                        except BaseException:
                            self.cancel()
                            self._result = None
                            break
            while self._fd_inflight is not None or self._stream_cleanup_pending:
                # A close, transfer or pipe-creation result is unknown. Never
                # retry the numeric descriptor: it may already be reused.
                time.sleep(0.25)
            self._release_children()
            self._done.set()

    def start(self):
        if self._thread is not None or time.monotonic() >= self._deadline:
            raise LaunchRefused('mind_e5_start_refused') from None
        self._token = object()
        self._thread = threading.Thread(target=self._run, name='mind-parent-pair', daemon=False)
        try:
            self._thread.start()
            self._start_confirmed = True
        except BaseException:
            self.cancel()
            raise
        return self._token

    def wait(self, token):
        if token is None or token is not self._token or not self._start_confirmed:
            raise LaunchRefused('mind_e5_start_refused') from None
        if not self._done.wait(max(0, self._deadline-time.monotonic())):
            self.cancel()
            raise LaunchRefused('mind_e5_start_refused') from None
        self._thread.join(timeout=max(0, self._deadline-time.monotonic()))
        if self._thread.is_alive() or self._result is None or self._abort.is_set() or time.monotonic() >= self._deadline:
            self.cancel()
            raise LaunchRefused('mind_e5_start_refused') from None
        self._token = None
        return self._result


def create_retained_parent_pair(*, enabled=False, bundle_root=None, expected_manifest_sha256=None,
                                controls=None, deadline_monotonic=None):
    """Prepare an inert fixed-role parent; production control gate is closed."""
    result = None
    try:
        if (enabled is not True or type(controls) is not _PrivatePairControls
                or type(bundle_root) is not str or type(expected_manifest_sha256) is not str
                or controls.metadata()['manifest_sha256'] != expected_manifest_sha256
                or type(deadline_monotonic) not in (int, float) or not math.isfinite(deadline_monotonic)
                or not time.monotonic() < deadline_monotonic <= time.monotonic() + 1800):
            raise ValueError('input')
        _require_verified_parent_spawn()
        result = _RetainedParentPair(bundle_root, expected_manifest_sha256, controls, float(deadline_monotonic))
    except Exception:
        pass
    if result is None:
        raise LaunchRefused('mind_e5_start_refused') from None
    return result


def _wait_for_owner_close(owner):
    """Keep this child alive while its owned worker cannot be closed safely."""
    while True:
        try:
            result = owner.close()
            if (type(result) is dict and result.get('state') == 'STOPPED'
                    and result.get('owned') is False):
                return
        except BaseException:
            pass  # The process remains the retained owner; no forced stop.
        time.sleep(0.25)


def _startup_scope_for_lane(lane):
    """Keep build lanes own-scoped and let RCO lanes read allowed build rows."""
    if type(lane) is not str:
        raise ValueError('lane')
    if lane in ('claude-rco-1', 'claude-rco-2'):
        return 'shared'
    if lane in ('codex-lead-1', 'codex-tools-1', 'fable-5'):
        return 'own'
    raise ValueError('lane')


def _select_local_source_record(reader, records, hit, used):
    """Choose an equivalent chunk deterministically after source re-derivation."""
    matches = [record for record in records
        if record['provenance']['source_event_ref'] == hit['source_event_sha256']
        and record['time_utc'] == hit['event_time_utc_floor']
        and record['meta']['agent'] == hit['source_agent']
        and reader.decode_text(record['stored_text']) == hit['text']]
    if not matches:
        raise ValueError('selected_source')
    def comparable(record):
        return ({key: value for key, value in record.items() if key not in ('record_id', 'provenance')},
                {key: value for key, value in record['provenance'].items() if key not in ('event_ref', 'chunk_index')})
    if any(comparable(record) != comparable(matches[0]) for record in matches[1:]):
        raise ValueError('ambiguous_source')
    remaining = sorted((record for record in matches if record['record_id'] not in used),
                       key=lambda record: record['record_id'])
    if not remaining:
        raise ValueError('selected_source')
    selected = remaining[0]
    used.add(selected['record_id'])
    return selected


_LOCAL_START_REASONS = frozenset(('BINDING_CHECK', 'CACHE_PREFLIGHT', 'WORKER_SETUP',
    'HANDLER_BUILD', 'CACHE_CAPACITY', 'CACHE_RESULT', 'FINAL_BINDING_CHECK', 'SOURCE_PREWARM', 'SOURCE_BUDGET',
    'OWNER_METER_UNAVAILABLE', 'OWNER_MEMORY_BUDGET',
    'ADMISSION', 'POLICY', 'RUNTIME_BINDING', 'WORKER_FACTORY', 'OWNER_CREATE',
    'EXTERNAL_POLICY', 'OWNER_START', 'OWNER_NOT_READY', 'OWNER_ENTRY', 'IMPORT', 'THREAD_START', 'UNKNOWN'))
_LOCAL_START_TYPES = frozenset(('ValueError', 'TypeError', 'RuntimeError', 'OSError',
    'FileNotFoundError', 'PermissionError', 'TimeoutError', 'KeyboardInterrupt',
    'GeneratorExit', 'CancelledError', 'FullRuntimeRefused', 'FullRuntimeCancellationUnknown',
    'BaseExceptionGroup', 'ExceptionGroup', 'LaunchRefused', 'UNKNOWN'))
_LOCAL_WORKER_HINTS = frozenset(('arguments', 'model_pin', 'offline_environment',
    'reader_mutex', 'library_import', 'model_load', 'serving', 'UNKNOWN'))


def _safe_worker_diagnostic(value):
    if type(value) is not tuple or len(value) != 2:
        return ('UNKNOWN', None)
    hint, code = value
    if type(hint) is not str or hint not in _LOCAL_WORKER_HINTS:
        return ('UNKNOWN', None)
    if hint != 'UNKNOWN':
        return (hint, None) if code is None else ('UNKNOWN', None)
    if code is None or (type(code) is int and 0 <= code <= 0xffffffff and code not in (20,21,22,23,24,25,29)):
        return ('UNKNOWN', code)
    return ('UNKNOWN', None)

def _local_exception_type(exc):
    pairs = ((ValueError, 'ValueError'), (TypeError, 'TypeError'), (RuntimeError, 'RuntimeError'),
             (OSError, 'OSError'), (FileNotFoundError, 'FileNotFoundError'),
             (PermissionError, 'PermissionError'), (TimeoutError, 'TimeoutError'),
             (KeyboardInterrupt, 'KeyboardInterrupt'), (GeneratorExit, 'GeneratorExit'),
             (LaunchRefused, 'LaunchRefused'))
    return next((name for kind, name in pairs if type(exc) is kind), 'UNKNOWN')

def _validated_start_failure(value):
    if (type(value) is dict and set(value) == {'reason', 'exception_type'}
            and type(value['reason']) is str and value['reason'] in _LOCAL_START_REASONS
            and type(value['exception_type']) is str and value['exception_type'] in _LOCAL_START_TYPES):
        return {'reason': value['reason'], 'exception_type': value['exception_type']}
    return {'reason': 'UNKNOWN', 'exception_type': 'UNKNOWN'}

class _LocalManifestQuery:
    """Retain one pinned owner in this process; no lane identity is authenticated.

    The caller must retain this object through STARTING or HOLD and must not
    terminate an owned worker on a timeout. Neither startup latency nor
    automatic refresh is provided by this adapter.
    """
    __slots__ = ('_owner', '_metadata', '_bundle', '_manifest', '_policy', '_policy_sha', '_approval_sha', '_policy_path',
                 '_admission', '_admission_anchor',
                 '_worker_timeout', '_lock_timeout', '_shutdown_timeout', '_thread', '_thread_done',
                 '_thread_start_uncertain', '_lock', '_cancel', '_owner_start_gate', '_owner_start_committed',
                 '_state', '_policy_invalid', '_pending_result', '_preclose_runtime_state',
                 '_candidate_inflight', '_candidate_accounting_uncertain', '_host_start_gate', '_host_stop',
                 '_host_owner_start_inflight', '_start_failure')

    def __init__(self, lane, bundle, manifest, policy, policy_sha, approval_sha,
                 worker_timeout, lock_timeout, shutdown_timeout, policy_path=None, admission=None,
                 host_start_gate=None, host_stop=None, host_owner_start_inflight=None):
        self._owner = None
        self._metadata = {'lane': lane}
        self._bundle, self._manifest = bundle, manifest
        self._policy, self._policy_sha, self._approval_sha = policy, policy_sha, approval_sha
        self._policy_path = policy_path
        self._admission = self._admission_anchor = admission
        self._host_start_gate, self._host_stop = host_start_gate, host_stop
        self._host_owner_start_inflight = host_owner_start_inflight
        self._worker_timeout, self._lock_timeout, self._shutdown_timeout = worker_timeout, lock_timeout, shutdown_timeout
        self._thread, self._lock, self._cancel = None, threading.Lock(), threading.Event()
        self._thread_done, self._thread_start_uncertain = threading.Event(), False
        self._owner_start_gate = host_start_gate if host_start_gate is not None else threading.Lock()
        self._owner_start_committed = False
        self._state, self._policy_invalid = 'NEW', False
        self._pending_result = None
        self._preclose_runtime_state = None
        self._candidate_inflight, self._candidate_accounting_uncertain = 0, False
        self._start_failure = None

    def __repr__(self):
        return '<PrivateMindLocalManifestQuery>'

    def _check_policy(self):
        from tools import wd_mind_full_runtime as runtime
        try:
            if self._policy_invalid:
                raise ValueError('policy_invalid')
            return runtime.create_operator_pinned_policy_anchor(enabled=True, bundle_root=self._bundle,
                expected_manifest_sha256=self._manifest, operator_policy_sha256=self._policy_sha,
                operator_approval_reference_sha256=self._approval_sha)
        except Exception:
            self._policy_invalid = True
            raise

    def _check_external_policy(self):
        if self._policy_path is None:
            return
        raw = None
        try:
            from tools import wd_mind_absent_generation_prefix_readonly as absent
            raw = absent._read_pinned(Path(self._policy_path), self._policy_sha, 65536)
        except Exception:
            self._policy_invalid = True
            owner = self._owner
            if owner is not None:
                try: owner.invalidate_external_policy()
                except Exception: pass  # This wrapper remains HOLD and returns no data.
            raise
        finally:
            raw = None

    def _prepare(self):
        binding = factory = owner = None
        stage = 'IMPORT'
        try:
            from tools import wd_mind_full_runtime as runtime
            from tools import wd_mind_e5_client as client
            stage = 'ADMISSION'
            if self._admission is not self._admission_anchor:
                raise ValueError('admission_changed')
            self._admission.assert_owner(self)
            stage = 'POLICY'
            self._check_policy()
            self._check_external_policy()
            if self._cancel.is_set():
                return
            stage = 'RUNTIME_BINDING'
            try:
                binding = runtime.assemble_full_runtime(enabled=True, bundle_root=self._bundle,
                    expected_manifest_sha256=self._manifest, identifier_policy_bytes=self._policy,
                    expected_identifier_policy_sha256=self._policy_sha)
            except runtime._OwnerMeterUnavailable as exc:
                if type(exc) is runtime._OwnerMeterUnavailable:
                    stage = 'OWNER_METER_UNAVAILABLE'
                raise
            except runtime._OwnerMemoryBudgetRefused as exc:
                if type(exc) is runtime._OwnerMemoryBudgetRefused:
                    stage = 'OWNER_MEMORY_BUDGET'
                raise
            except runtime._EagerSourceBudgetRefused as exc:
                if type(exc) is runtime._EagerSourceBudgetRefused:
                    stage = 'SOURCE_BUDGET'
                raise
            if self._cancel.is_set():
                return
            stage = 'WORKER_FACTORY'
            factory = runtime.create_manifest_worker_factory(enabled=True, bundle_root=self._bundle,
                expected_manifest_sha256=self._manifest, worker_timeout_seconds=self._worker_timeout,
                lock_timeout_seconds=self._lock_timeout, bulk_contract_sha256=client.BULK_CONTRACT_SHA256,
                _admission=self._admission, _admission_owner=self)
            anchor = self._check_policy()
            stage = 'OWNER_CREATE'
            owner = runtime.create_single_manifest_owner(enabled=True, binding=binding, lane=self._metadata['lane'],
                worker_factory=factory, shutdown_timeout=self._shutdown_timeout, policy_anchor=anchor,
                worker_rotation=True, vector_cache_enabled=True)
            with self._lock:
                self._owner = owner  # Retain before any worker may be launched.
            stage = 'EXTERNAL_POLICY'
            self._check_external_policy()
            stage = 'OWNER_START'
            # Serialize only the decision to launch with cancellation. The
            # potentially long start runs outside this gate and its thread is
            # retained until it has positively finished.
            with self._owner_start_gate:
                if self._cancel.is_set() or (self._host_stop is not None and self._host_stop.is_set()):
                    return
                self._owner_start_committed = True
                if self._host_owner_start_inflight is not None:
                    self._host_owner_start_inflight.set()
            try:
                outcome = owner.start()
            finally:
                if self._host_owner_start_inflight is not None:
                    with self._owner_start_gate:
                        self._host_owner_start_inflight.clear()
            with self._lock:
                self._state = ('READY' if not self._cancel.is_set() and type(outcome) is dict
                               and outcome.get('state') == 'READY' and outcome.get('owned') is True else 'HOLD')
        except BaseException as exc:
            with self._lock:
                self._state = 'HOLD'
                self._start_failure = {'reason': stage, 'exception_type': _local_exception_type(exc)}
        finally:
            self._policy = binding = factory = owner = None
            with self._lock:
                if self._cancel.is_set() and self._owner is None:
                    self._state = 'STOPPED'

    def _prepare_target(self):
        try:
            self._prepare()
        finally:
            self._thread_done.set()

    def start(self):
        if self._admission is not self._admission_anchor:
            raise LaunchRefused('owner_admission_refused')
        self._admission.assert_owner(self)
        with self._lock:
            if self._state != 'NEW':
                raise LaunchRefused('mind_e5_start_refused') from None
            self._state = 'STARTING'
            # Record uncertainty before constructing or starting the target.
            # A Thread.start exception does not prove the target cannot run.
            self._thread_start_uncertain = True
            self._thread = threading.Thread(target=self._prepare_target, name='mind-local-owner', daemon=False)
            try:
                self._thread.start()
            except BaseException as exc:
                self._cancel.set()
                self._state = 'HOLD'
                self._start_failure = {'reason': 'THREAD_START', 'exception_type': _local_exception_type(exc)}
                raise
            self._thread_start_uncertain = False
            return {'state': 'STARTING', 'owned': None, 'authority': 'none'}

    def wait(self, *, timeout):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 1800:
            raise LaunchRefused('mind_e5_start_refused') from None
        thread = self._thread
        if thread is None:
            raise LaunchRefused('mind_e5_start_refused') from None
        try:
            thread.join(timeout=float(timeout))
        except BaseException:
            self._cancel.set()
            with self._lock:
                self._state = 'HOLD'
            raise
        if thread.is_alive():
            self._cancel.set()
            with self._lock:
                self._state = 'HOLD'
        return self.status()

    def status(self):
        with self._lock:
            state, owner = self._state, self._owner
            active = self._thread is not None and self._thread.is_alive()
        if active:
            return {'state': 'HOLD' if state == 'HOLD' else 'STARTING', 'owned': None, 'authority': 'none'}
        if owner is None:
            return {'state': state, 'owned': False if state in ('NEW', 'STOPPED') else None, 'authority': 'none'}
        outcome = owner.status()
        if state == 'HOLD' or self._policy_invalid:
            return {'state': 'HOLD', 'owned': outcome.get('owned') if type(outcome) is dict else None, 'authority': 'none'}
        return outcome

    def start_diagnostic(self):
        if not self._lock.acquire(blocking=False):
            return {'reason': 'UNKNOWN', 'exception_type': 'UNKNOWN', 'authority': 'none'}
        try:
            failure, owner = self._start_failure, self._owner
        finally:
            self._lock.release()
        if failure is None and owner is not None:
            try:
                value = owner.start_diagnostic()
                if type(value) is dict and value.get('authority') == 'none':
                    failure = {'reason': value.get('reason'), 'exception_type': value.get('exception_type')}
            except BaseException:
                failure = None
        safe = _validated_start_failure(failure)
        return {'reason': safe['reason'], 'exception_type': safe['exception_type'], 'authority': 'none'}

    def start_failure_observed(self):
        """Inspect completed startup after close; unknown is not a clean result."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return None
            failure, owner, policy_invalid = self._start_failure, self._owner, self._policy_invalid
        if failure is not None or policy_invalid:
            return True
        if owner is None:
            return False
        try:
            value = owner.start_diagnostic()
            if type(value) is not dict or value.get('authority') != 'none':
                return None
            reason = value.get('reason')
            if reason == 'NONE' and value.get('exception_type') == 'NONE':
                return False
            if reason in _LOCAL_START_REASONS and reason not in ('UNKNOWN',):
                return True
        except BaseException:
            pass
        return None

    def close_failure_observed(self):
        """Return pre-close runtime failure evidence; unknown is not clean."""
        with self._lock:
            value, owner = self._preclose_runtime_state, self._owner
        if owner is None and value is None:
            return False
        if value == 'HOLD':
            return True
        if value == 'READY' or (value == 'NEW' and not self._owner_start_committed):
            return False
        return None

    def worker_exit_diagnostic(self):
        if not self._lock.acquire(blocking=False):
            return ('UNKNOWN', None)
        try:
            owner = self._owner
        finally:
            self._lock.release()
        if owner is None:
            return ('UNKNOWN', None)
        try:
            value = _safe_worker_diagnostic(owner.worker_exit_diagnostic())
        except BaseException:
            return ('UNKNOWN', None)
        if not self._lock.acquire(blocking=False):
            return ('UNKNOWN', None)
        try:
            return value if self._owner is owner else ('UNKNOWN', None)
        finally:
            self._lock.release()

    def close(self, *, _service_start_released=None, _close_started=None):
        with self._owner_start_gate:
            self._cancel.set()
            if _close_started is not None:
                _close_started.set()
        if _service_start_released is not None:
            try:
                if _service_start_released() is not True:
                    return {'state': 'HOLD', 'owned': None, 'authority': 'none'}
            except BaseException:
                return {'state': 'HOLD', 'owned': None, 'authority': 'none'}
        with self._lock:
            owner = self._owner
            # Once cancellation is set, the admission lock prevents any new
            # candidate request. A request already in flight can change the
            # owner's state after a READY observation, so its outcome remains
            # unknown for this close even if the request exits moments later.
            if self._candidate_inflight or self._candidate_accounting_uncertain:
                if self._preclose_runtime_state != 'HOLD':
                    self._preclose_runtime_state = 'UNKNOWN'
            if (self._thread_start_uncertain and (self._thread is None or not self._thread_done.is_set()
                                                  or self._thread.is_alive())):
                self._state = 'HOLD'
                return {'state': 'HOLD', 'owned': None, 'authority': 'none'}
            active = self._thread is not None and self._thread.is_alive()
            if active:
                self._state = 'HOLD'
                return {'state': 'HOLD', 'owned': None, 'authority': 'none'}
            if owner is None:
                self._state = 'STOPPED'
                self._policy = None
                return {'state': 'STOPPED', 'owned': False, 'authority': 'none'}
        if self._preclose_runtime_state is None:
            try:
                before = owner.status()
                state = before.get('state') if type(before) is dict else None
                if state not in ('READY', 'HOLD') and not (state == 'NEW' and not self._owner_start_committed):
                    state = 'UNKNOWN'
            except BaseException:
                state = 'UNKNOWN'
            with self._lock:
                if self._preclose_runtime_state is None:
                    self._preclose_runtime_state = state
        outcome = owner.close()
        with self._lock:
            # A request can acquire the owner after the pre-close status read.
            # If close observes that contention, the request's later outcome
            # was not covered by the earlier READY observation.
            if (type(outcome) is dict and outcome.get('state') == 'BUSY'
                    and self._preclose_runtime_state == 'READY'):
                self._preclose_runtime_state = 'UNKNOWN'
            self._state = ('STOPPED' if type(outcome) is dict and outcome.get('state') == 'STOPPED'
                           and outcome.get('owned') is False else 'HOLD')
        return outcome

    def _candidate_query(self, request, method):
        registered = False
        try:
            with self._lock:
                if self._state != 'READY' or self._policy_invalid or self._cancel.is_set():
                    raise ValueError('owner_not_ready')
                prior_uncertainty = self._candidate_accounting_uncertain
                self._candidate_accounting_uncertain = True
                self._candidate_inflight += 1
                registered = True
                self._candidate_accounting_uncertain = prior_uncertainty
                owner = self._owner
            self._check_external_policy()
            result = getattr(owner, method)(request)
            self._check_external_policy()
            with self._lock:
                if self._state != 'READY' or self._policy_invalid or self._cancel.is_set():
                    raise ValueError('owner_not_ready')
            return result
        except Exception:
            if self._policy_invalid:
                with self._lock:
                    self._state = 'HOLD'
            raise LaunchRefused('mind_e5_start_refused') from None
        finally:
            if registered:
                with self._lock:
                    prior_uncertainty = self._candidate_accounting_uncertain
                    self._candidate_accounting_uncertain = True
                    if self._candidate_inflight > 0:
                        self._candidate_inflight -= 1
                        self._candidate_accounting_uncertain = prior_uncertainty

    def candidates(self, request):
        return self._candidate_query(request, 'candidates')

    def located_candidates(self, request):
        return self._candidate_query(request, 'located_candidates')

    def query(self):
        """Refuse the unbudgeted legacy query before accessing owner state."""
        raise LaunchRefused('mind_e5_start_refused') from None

    def finalize_result(self):
        """Re-read selected source rows after a confirmed stop, before delivery."""
        from tools import wd_mind_full_runtime as runtime
        owner = snapshot = projection = context = report = records = None
        acquired = False
        try:
            with self._lock:
                if self._state != 'STOPPED' or self._policy_invalid or self._pending_result is None:
                    raise ValueError('not_stopped')
                owner, raw = self._owner, self._pending_result
            if owner is None or not owner._lock.acquire(blocking=False):
                raise ValueError('owner_busy')
            acquired = True
            if owner._state != 'STOPPED' or type(owner._snapshot) is not dict:
                raise ValueError('owner_state')
            self._check_policy()
            self._check_external_policy()
            if not hmac.compare_digest(runtime._local_generation_policy_pin(owner), self._policy_sha):
                raise ValueError('policy_changed')
            controls = runtime._bundle_inputs(Path(self._bundle), self._manifest)
            fleet_sha = hashlib.sha256(controls[0]).hexdigest()
            result = json.loads(_RetainedParentPair._validated_result(self, raw, fleet_sha).decode('utf-8'),
                                object_pairs_hook=runtime.prefix._unique_object,
                                parse_constant=runtime.reader._bad_constant)
            snapshot = owner._snapshot
            projection, context, report = runtime.reader._verified_complete(snapshot)
            records = projection['messages']['records']
            selected, proposals, used = [], [], set()
            for hit in result['results']:
                record = _select_local_source_record(runtime.reader, records, hit, used)
                if (_startup_scope_for_lane(self._metadata['lane']) == 'own'
                        and record['meta']['agent'] != self._metadata['lane']):
                    raise ValueError('source_scope')
                selected.append(record)
                proposals.append({'ref_sha256': runtime.reader._sha(record['provenance']['event_ref'].encode()),
                    'doc_id_sha256': runtime.reader._sha(record['record_id'].encode()),
                    'ref_kind': 'sha256_of_projected_metadata', 'canonical_event': False})
            now = runtime.reader.dt.datetime.now(runtime.reader.dt.timezone.utc)
            resolved = runtime.reader.resolve_content_results(proposals, runtime.reader._utc_text(now),
                                                               _complete=True, **snapshot)
            if len(resolved) != len(selected):
                raise ValueError('selected_count')
            for hit, record, fresh in zip(result['results'], selected, resolved):
                if (fresh['source_reference']['sha256'] != hit['source_event_sha256']
                        or runtime.reader.decode_text(fresh['stored_text']) != hit['text']):
                    raise ValueError('source_changed')
            self._check_policy()
            self._check_external_policy()
            if owner._snapshot is not snapshot or runtime._local_generation_policy_pin(owner) != self._policy_sha:
                raise ValueError('policy_changed')
            finished = runtime.reader.dt.datetime.now(runtime.reader.dt.timezone.utc)
            for hit, record in zip(result['results'], selected):
                age = (finished - runtime.reader._utc(record['time_utc'])).total_seconds()
                expiry = record['provenance'].get('valid_until_utc')
                if age < 0 or (expiry is not None and finished >= runtime.reader._utc(expiry)):
                    raise ValueError('selected_time')
                hit['event_age_seconds_upper_bound'] = max(hit['event_age_seconds_upper_bound'], math.ceil(age))
            return runtime.reader._canonical(result)
        except Exception:
            raise LaunchRefused('mind_e5_start_refused') from None
        finally:
            self._pending_result = None
            if acquired:
                owner._lock.release()
            owner = snapshot = projection = context = report = records = None


def create_local_manifest_query(*, enabled=False, bundle_root=None, expected_manifest_sha256=None,
                                identifier_policy_bytes=None, expected_identifier_policy_sha256=None,
                                operator_approval_reference_sha256=None, lane=None,
                                worker_timeout_seconds=300, lock_timeout_seconds=0, shutdown_timeout=5,
                                policy_path=None, _admission=None, _host_start_gate=None,
                                _host_stop=None, _host_owner_start_inflight=None):
    """Prepare, but do not start, a pinned in-process Mind owner.

    The manifest and operator-policy inputs must come independently from the
    authorized caller. Matching hashes do not authenticate that caller. The
    retained result must be kept alive across a start, query, timeout or HOLD.
    """
    result = None
    try:
        host_args = (_host_start_gate, _host_stop, _host_owner_start_inflight)
        if (not (all(value is None for value in host_args) or all(value is not None for value in host_args))
                or enabled is not True or type(_admission) is not _LocalAdmission or type(bundle_root) is not str
                or type(expected_manifest_sha256) is not str or not _HEX.fullmatch(expected_manifest_sha256)
                or type(identifier_policy_bytes) is not bytes or not 0 < len(identifier_policy_bytes) <= 65536
                or type(expected_identifier_policy_sha256) is not str or not _HEX.fullmatch(expected_identifier_policy_sha256)
                or type(operator_approval_reference_sha256) is not str or not _HEX.fullmatch(operator_approval_reference_sha256)
                or (policy_path is not None and type(policy_path) is not str)
                or not hmac.compare_digest(hashlib.sha256(identifier_policy_bytes).hexdigest(),
                                           expected_identifier_policy_sha256)
                or type(worker_timeout_seconds) not in (int, float) or not math.isfinite(worker_timeout_seconds)
                or not 0 < worker_timeout_seconds <= 1800
                or type(lock_timeout_seconds) not in (int, float) or not math.isfinite(lock_timeout_seconds)
                or not 0 <= lock_timeout_seconds <= 30
                or type(shutdown_timeout) not in (int, float) or not math.isfinite(shutdown_timeout)
                or not 0 <= shutdown_timeout <= 15):
            raise ValueError('input')
        _startup_scope_for_lane(lane)
        # Return before the expensive source preparation or any worker launch.
        result = _LocalManifestQuery(lane, bundle_root, expected_manifest_sha256, identifier_policy_bytes,
            expected_identifier_policy_sha256, operator_approval_reference_sha256,
            worker_timeout_seconds, lock_timeout_seconds, shutdown_timeout, policy_path, _admission,
            _host_start_gate, _host_stop, _host_owner_start_inflight)
        _admission.bind_owner(result)
    except Exception:
        result = None
    finally:
        identifier_policy_bytes = None
    if result is None:
        raise LaunchRefused('mind_e5_start_refused') from None
    return result


def _owner_child_entry(*, control_stream, data_stream, candidate_stream,
                       bundle_root, expected_manifest_sha256):
    """Run one retained owner role; trusted-parent gate is currently closed.

    stdin is the parent-created MCO1 control, stdout carries MEX1, and stderr
    is a reference-only candidate pipe, not a diagnostic or logging channel.
    Parent owns the process and all I/O through timeout/HOLD. No traceback or
    raw input is deliberately written on either output path.
    """
    owner = None
    success = False
    try:
        _require_verified_parent_spawn()
        from tools import wd_mind_full_runtime as runtime
        from tools import wd_mind_e5_client as client
        raw = control_stream.read(101 + 32 + 136 + 65536 + 1)
        manifest, policy_sha, approval_sha, lane, nonce, control = _decode_owner_control_bytes(
            raw, expected_manifest_sha256)
        raw = None
        anchor = runtime.create_operator_pinned_policy_anchor(enabled=True, bundle_root=bundle_root,
            expected_manifest_sha256=manifest, operator_policy_sha256=policy_sha,
            operator_approval_reference_sha256=approval_sha)
        entry = runtime.prepare_manifest_bound_owner(enabled=True, policy_anchor=anchor, control_buffer=control,
            bundle_root=bundle_root, expected_manifest_sha256=manifest, lane=lane,
            worker_timeout_seconds=300, lock_timeout_seconds=0, shutdown_timeout=5,
            bulk_contract_sha256=client.BULK_CONTRACT_SHA256)
        owner = entry['owner']  # Retain before any worker launch.
        bootstrap = entry['bootstrap']
        started = owner.start()
        if type(started) is not dict or started.get('state') != 'READY' or started.get('owned') is not True:
            raise ValueError('owner_start')
        candidate = owner.candidates({'schema':'wd.mind-pipe-request.v1',
            'query_id':'startup_work_overview', 'nonce':nonce, 'lane':lane,
            'scope':_startup_scope_for_lane(lane)})
        candidate_bytes = runtime.reader._canonical(candidate)
        if not 1 <= len(candidate_bytes) <= 2048:
            raise ValueError('candidate_size')
        offset = 0
        while offset < len(candidate_bytes):
            count = candidate_stream.write(memoryview(candidate_bytes)[offset:])
            if type(count) is not int or not 0 < count <= len(candidate_bytes) - offset:
                raise ValueError('candidate_write')
            offset += count
        candidate_stream.close()  # EOF separates this reference stream.
        prepared = owner.prepare_consumer_export(enabled=True, expected_nonce=nonce,
            expected_identifier_policy_sha256=policy_sha, max_total_bytes=64 * 1024 * 1024)
        outcome = prepared._send_parent_stream(enabled=True, stream=runtime._PrivateExportStream(data_stream),
            bootstrap=bootstrap, max_wire_bytes=64 * 1024 * 1024, deadline_monotonic=time.monotonic()+300)
        if type(outcome) is not dict or outcome.get('status') != 'SENT':
            raise ValueError('export')
        success = True
    except BaseException:
        success = False
    finally:
        # Output cleanup never emits error details; failed close is unproven.
        for stream in (candidate_stream, data_stream):
            try:
                stream.close()
            except BaseException:
                pass
        if owner is not None:
            _wait_for_owner_close(owner)
    return 0 if success else 2


def _encode_export_frame(*, enabled=False, channel_key=None, nonce=None, kind=None,
                         sequence=None, payload=None):
    """Construct one in-memory MEX1 frame; this function performs no I/O.

    Frame MAC authenticates possession of the channel key, not peer identity.
    The production handle export remains disabled until launcher provenance is
    independently established. Callers own the aggregate wire-byte limit.
    """
    result = None
    cancelled_type = None
    try:
        if (enabled is not True or type(channel_key) is not bytes or len(channel_key) != 32
                or type(nonce) is not bytes or len(nonce) != 32
                or type(kind) is not bytes or kind not in (b'M', b'D', b'F')
                or type(sequence) is not int or not 0 <= sequence <= 0xffffffff
                or (kind == b'M' and sequence != 0) or (kind != b'M' and sequence == 0)
                or type(payload) is not bytes or not 0 < len(payload) <= 65488
                or (kind == b'F' and len(payload) != 44)):
            raise ValueError('frame')
        header = struct.pack('>4sB3xII', b'MEX1', kind[0], sequence, len(payload))
        tag = hmac.new(channel_key, b'wd.mind-export-frame.v1\0' + nonce + header + payload,
                       hashlib.sha256).digest()
        result = header + payload + tag
    except KeyboardInterrupt:
        cancelled_type = KeyboardInterrupt
    except GeneratorExit:
        cancelled_type = GeneratorExit
    except asyncio.CancelledError:
        cancelled_type = asyncio.CancelledError
    except BaseException:
        pass
    finally:
        channel_key = nonce = payload = header = tag = None
    if cancelled_type is not None:
        raise cancelled_type() from None
    if result is None:
        raise LaunchRefused('mind_e5_start_refused') from None
    return result


def _export_wire_size(*, enabled=False, metadata_length=None, block_lengths=None, max_wire_bytes=None):
    """Preflight the complete MEX1 wire size without reading any block."""
    result = None
    try:
        if (enabled is not True or type(metadata_length) is not int or not 0 < metadata_length <= 65488
                or type(block_lengths) is not tuple or len(block_lengths) != 12
                or any(type(length) is not int or not 0 <= length < 2**63 for length in block_lengths)
                or type(max_wire_bytes) is not int or not 0 < max_wire_bytes < 2**63):
            raise ValueError('wire_budget')
        data_frames = sum((length + 65487) // 65488 for length in block_lengths)
        if data_frames > 0xfffffffe:
            raise ValueError('wire_sequence')
        total = metadata_length + sum(block_lengths) + 48 * (data_frames + 2) + 44
        # Reserve one byte for the receiver's trailing-EOF probe.
        if total >= 2**63 or total >= max_wire_bytes:
            raise ValueError('wire_budget')
        result = total
    except Exception:
        pass
    if result is None:
        raise LaunchRefused('mind_e5_start_refused') from None
    return result


class _CancelablePipeReader:
    """Borrow the parent's retained pipe; retain a blocked read thread on HOLD."""
    __slots__ = ('_api', '_handle', '_deadline', '_lock', '_result_lock', '_thread', '_thread_handle', '_start_confirmed', '_done', '_buffer',
                 '_result', '_error', '_abandoned', '_state', '_cancellation')

    def __init__(self, api, handle, deadline):
        self._api, self._handle, self._deadline = api, handle, deadline
        self._lock, self._result_lock, self._done = threading.Lock(), threading.Lock(), threading.Event()
        self._thread = self._buffer = self._result = self._error = None
        self._thread_handle = wintypes.HANDLE()
        self._start_confirmed = False
        self._abandoned, self._state, self._cancellation = False, 'IDLE', 'NOT_REQUESTED'

    def __repr__(self):
        return '<PrivateMindCancelablePipeReader>'

    def status(self):
        active = (None if self._thread is not None and not self._start_confirmed and not self._done.is_set()
                  else self._thread is not None and self._thread.is_alive())
        return {'state': self._state, 'worker_active': active, 'cancellation': self._cancellation}

    def _read_worker(self, size):
        try:
            current = self._api.GetCurrentProcess()
            # Duplicate the current thread's pseudo-handle inside the worker;
            # an integer native_id may already be recycled when cancel runs.
            if not self._api.DuplicateHandle(current, self._api.GetCurrentThread(), current,
                    ctypes.byref(self._thread_handle), 0, False, 2):
                raise ValueError('thread_handle')
            with self._result_lock:
                if self._abandoned:
                    self._error = 995
                    return
            received = wintypes.DWORD()
            ok = self._api.ReadFile(self._handle, self._buffer, size, ctypes.byref(received), None)
            error = 0 if ok else ctypes.get_last_error()
            with self._result_lock:
                if not self._abandoned:
                    if ok:
                        self._result = self._buffer.raw[:received.value]
                    elif error == 109:  # ERROR_BROKEN_PIPE: ordinary EOF.
                        self._result = b''
                self._error = error
        except BaseException:
            self._error = -1
        finally:
            self._done.set()

    def _cancel_worker(self):
        native_thread = self._thread_handle.value
        if native_thread is None:
            return False
        # Windows requires THREAD_TERMINATE to call CancelSynchronousIo; no
        # thread or process termination function is invoked.
        return bool(self._api.CancelSynchronousIo(native_thread))

    def _release_thread_handle(self):
        if self._thread_handle.value is not None:
            if not self._api.CloseHandle(self._thread_handle):
                raise ValueError('thread_handle_close')
            self._thread_handle.value = None

    def _hold_and_cancel(self):
        self._state = 'HOLD'
        with self._result_lock:
            self._abandoned = True
            self._result = None
        cancelled = False
        try:
            if self._thread is not None:
                cancelled = self._cancel_worker()
                self._thread.join(timeout=0.25)
        except BaseException:
            pass
        active = self._thread is not None and self._thread.is_alive()
        self._cancellation = ('CONFIRMED' if cancelled and not active and self._error == 995
                              else 'UNCONFIRMED')

    def read(self, size):
        if not self._lock.acquire(blocking=False):
            raise LaunchRefused('mind_e5_start_refused') from None
        result = None
        cancelled_type = None
        try:
            if self._state != 'IDLE' or type(size) is not int or not 0 < size <= 65536:
                raise ValueError('pipe_read')
            if time.monotonic() >= self._deadline:
                self._state = 'HOLD'
                raise ValueError('pipe_deadline')
            self._done.clear()
            self._result, self._error, self._abandoned = None, None, False
            self._buffer = ctypes.create_string_buffer(size)
            self._state = 'READING'
            deadline = self._deadline
            self._thread = threading.Thread(target=self._read_worker, args=(size,),
                                            name='mind-owned-pipe-read', daemon=False)
            self._start_confirmed = False
            self._thread.start()
            self._start_confirmed = True
            if not self._done.wait(max(0, deadline - time.monotonic())) or time.monotonic() >= deadline:
                self._hold_and_cancel()
                raise ValueError('pipe_deadline')
            self._thread.join(timeout=0.25)
            if self._thread.is_alive() or type(self._result) is not bytes or time.monotonic() >= deadline:
                self._hold_and_cancel()
                raise ValueError('pipe_incomplete')
            self._release_thread_handle()
            result, self._result = self._result, None
            self._buffer, self._state = None, 'IDLE'
        except KeyboardInterrupt:
            self._hold_and_cancel()
            cancelled_type = KeyboardInterrupt
        except GeneratorExit:
            self._hold_and_cancel()
            cancelled_type = GeneratorExit
        except asyncio.CancelledError:
            self._hold_and_cancel()
            cancelled_type = asyncio.CancelledError
        except BaseException:
            if self._state == 'READING':
                self._abandoned, self._state = True, 'HOLD'
        finally:
            self._lock.release()
        if cancelled_type is not None:
            raise cancelled_type() from None
        if result is None:
            raise LaunchRefused('mind_e5_start_refused') from None
        return result

    def close(self):
        if not self._lock.acquire(blocking=False):
            raise LaunchRefused('mind_e5_start_refused') from None
        try:
            if self._thread is not None:
                if not self._done.is_set():
                    self._state = 'HOLD'
                    raise LaunchRefused('mind_e5_start_refused') from None
                try:
                    self._thread.join(timeout=0)
                    if self._thread.is_alive():
                        raise ValueError('thread_active')
                except BaseException:
                    self._state = 'HOLD'
                    raise LaunchRefused('mind_e5_start_refused') from None
            try:
                self._release_thread_handle()
            except Exception:
                self._state = 'HOLD'
                raise LaunchRefused('mind_e5_start_refused') from None
            # Detach only. The caller retains and closes the original handle.
            self._handle = None
            self._buffer = self._result = None
            self._state = 'DETACHED'
        finally:
            self._lock.release()


def create_cancelable_pipe_reader(*, enabled=False, handle=None, deadline_monotonic=None):
    """Borrow a retained pipe and use one parent-provided absolute deadline."""
    result = None
    try:
        if (enabled is not True or os.name != 'nt' or type(handle) is not int or handle <= 0
                or type(deadline_monotonic) not in (int, float) or not math.isfinite(deadline_monotonic)
                or not time.monotonic() < deadline_monotonic <= time.monotonic() + 1800):
            raise ValueError('pipe')
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.GetCurrentProcess.argtypes, api.GetCurrentProcess.restype = [], wintypes.HANDLE
        api.GetCurrentThread.argtypes, api.GetCurrentThread.restype = [], wintypes.HANDLE
        api.GetFileType.argtypes, api.GetFileType.restype = [wintypes.HANDLE], wintypes.DWORD
        api.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                        ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL,
                                        wintypes.DWORD]
        api.DuplicateHandle.restype = wintypes.BOOL
        api.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                                 ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
        api.ReadFile.restype = wintypes.BOOL
        api.CancelSynchronousIo.argtypes, api.CancelSynchronousIo.restype = [wintypes.HANDLE], wintypes.BOOL
        api.CloseHandle.argtypes, api.CloseHandle.restype = [wintypes.HANDLE], wintypes.BOOL
        if api.GetFileType(handle) != 3:  # FILE_TYPE_PIPE
            raise ValueError('pipe_type')
        result = _CancelablePipeReader(api, handle, float(deadline_monotonic))
    except Exception:
        pass
    if result is None:
        raise LaunchRefused('mind_e5_start_refused') from None
    return result


class _RetainedSourceGateCall:
    """Retain one decoder/source-gate worker through timeout or uncertainty."""
    __slots__ = ('_callback', '_deadline', '_lock', '_result_lock', '_done', '_thread',
                 '_start_confirmed', '_abandoned', '_result', '_signal_type', '_state')

    def __init__(self, callback, deadline):
        self._callback, self._deadline = callback, deadline
        self._lock, self._result_lock, self._done = threading.Lock(), threading.Lock(), threading.Event()
        self._thread, self._result, self._signal_type = None, None, None
        self._start_confirmed, self._abandoned, self._state = False, False, 'NEW'

    def __repr__(self):
        return '<PrivateMindRetainedSourceGateCall>'

    def status(self):
        active = (None if self._thread is not None and not self._start_confirmed and not self._done.is_set()
                  else self._thread is not None and self._thread.is_alive())
        return {'state': self._state, 'worker_active': active}

    def _abandon(self):
        self._state = 'HOLD'
        with self._result_lock:
            self._abandoned, self._result = True, None

    def _run(self):
        value = None
        signal_type = None
        try:
            value = self._callback()
        except KeyboardInterrupt:
            signal_type = KeyboardInterrupt
        except GeneratorExit:
            signal_type = GeneratorExit
        except asyncio.CancelledError:
            signal_type = asyncio.CancelledError
        except BaseException:
            pass
        finally:
            with self._result_lock:
                if not self._abandoned:
                    self._result, self._signal_type = value, signal_type
            value = None
            self._done.set()

    def start(self):
        if not self._lock.acquire(blocking=False):
            raise LaunchRefused('mind_e5_start_refused') from None
        cancelled_type = None
        try:
            if self._state != 'NEW' or time.monotonic() >= self._deadline:
                raise ValueError('source_gate_start')
            self._state = 'STARTING'
            self._thread = threading.Thread(target=self._run, name='mind-source-gate', daemon=False)
            self._thread.start()
            self._start_confirmed = True
        except KeyboardInterrupt:
            self._abandon(); cancelled_type = KeyboardInterrupt
        except GeneratorExit:
            self._abandon(); cancelled_type = GeneratorExit
        except asyncio.CancelledError:
            self._abandon(); cancelled_type = asyncio.CancelledError
        except BaseException:
            self._abandon()
        finally:
            self._lock.release()
        if cancelled_type is not None:
            raise cancelled_type() from None
        if self._state != 'STARTING':
            raise LaunchRefused('mind_e5_start_refused') from None

    def wait(self):
        if not self._lock.acquire(blocking=False):
            raise LaunchRefused('mind_e5_start_refused') from None
        result = None
        cancelled_type = None
        try:
            if self._state != 'STARTING' or not self._done.wait(max(0, self._deadline-time.monotonic())):
                raise ValueError('source_gate_deadline')
            self._thread.join(timeout=max(0, self._deadline-time.monotonic()))
            if self._thread.is_alive() or time.monotonic() >= self._deadline:
                raise ValueError('source_gate_incomplete')
            if self._signal_type is not None:
                cancelled_type = self._signal_type
                self._abandon()
            elif self._result is None:
                raise ValueError('source_gate_incomplete')
            else:
                result, self._result, self._state = self._result, None, 'SUCCEEDED'
        except KeyboardInterrupt:
            self._abandon(); cancelled_type = KeyboardInterrupt
        except GeneratorExit:
            self._abandon(); cancelled_type = GeneratorExit
        except asyncio.CancelledError:
            self._abandon(); cancelled_type = asyncio.CancelledError
        except BaseException:
            self._abandon()
        finally:
            self._lock.release()
        if cancelled_type is not None:
            raise cancelled_type() from None
        if result is None:
            raise LaunchRefused('mind_e5_start_refused') from None
        return result

    def close(self):
        if not self._lock.acquire(blocking=False):
            raise LaunchRefused('mind_e5_start_refused') from None
        try:
            if self._thread is not None:
                if not self._done.is_set():
                    self._abandon()
                    raise LaunchRefused('mind_e5_start_refused') from None
                try:
                    self._thread.join(timeout=0)
                    if self._thread.is_alive():
                        raise ValueError('source_gate_active')
                except BaseException:
                    self._abandon()
                    raise LaunchRefused('mind_e5_start_refused') from None
            self._callback, self._result, self._state = None, None, 'DETACHED'
        finally:
            self._lock.release()


def create_retained_source_gate_call(*, enabled=False, callback=None, deadline_monotonic=None):
    """Prepare an inert owner; caller must retain it before calling start()."""
    result = None
    try:
        if (enabled is not True or not callable(callback) or isinstance(callback, type)
                or type(deadline_monotonic) not in (int, float) or not math.isfinite(deadline_monotonic)
                or not time.monotonic() < deadline_monotonic <= time.monotonic() + 1800):
            raise ValueError('source_gate')
        result = _RetainedSourceGateCall(callback, float(deadline_monotonic))
    except Exception:
        pass
    finally:
        callback = None
    if result is None:
        raise LaunchRefused('mind_e5_start_refused') from None
    return result


def _path(value):
    try:
        path = Path(value)
        text = str(path)
        if not path.is_absolute() or text.startswith(("\\\\", "//")) or "\x00" in text:
            raise ValueError()
        # Drive-relative and alternate data streams are not local ordinary paths.
        if os.name == "nt" and (len(text) < 3 or not text[0].isalpha() or text[1:3] != ":\\" or ":" in text[2:]):
            raise ValueError()
        return path
    except (TypeError, ValueError, OSError):
        raise LaunchRefused("invalid_path") from None


def _worker_environment(runtime_root):
    """Create an isolated child environment from the verified runtime root."""
    runtime_cache = os.fspath(_path(runtime_root))
    if os.fspath(runtime_root) != runtime_cache or runtime_cache != os.path.abspath(runtime_cache):
        raise ValueError("runtime_cache_not_canonical")
    env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR") if key in os.environ}
    env["TORCHINDUCTOR_CACHE_DIR"] = runtime_cache
    env.update(TEMP=os.devnull, TMP=os.devnull, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    for name in ("HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE", "HF_ASSETS_CACHE",
                 "SENTENCE_TRANSFORMERS_HOME", "TORCH_HOME", "XDG_CACHE_HOME", "CUDA_CACHE_PATH"):
        env[name] = os.devnull
    return env


def _validate_audit_write_scope(lock_file, scratch_root):
    """Check the fixed inert sentinel and refuse any requested scratch path."""
    try:
        audit = _path(_AUDIT_ROOT)
        lock = _path(lock_file)
        if scratch_root is not None or any(".." in path.parts for path in (audit, lock)):
            raise ValueError()
        scope = audit / "wd-mind-runtime"
        expected_lock = scope / "q4-reader.lock"
        same = lambda a, b: os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
        if not same(lock, expected_lock):
            raise ValueError()
        # Existing ancestor directories only; never create during validation.
        for directory in (scope,):
            current = Path(directory.anchor)
            for part in directory.parts[1:]:
                if ":" in part or part.endswith((".", " ")):
                    raise ValueError()
                current /= part
                info = os.lstat(current)
                if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                        or getattr(info, "st_file_attributes", 0) & 0x400):
                    raise ValueError()
        try:
            info = os.lstat(lock)
        except FileNotFoundError:
            info = None
        if info is not None and (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
                                 or getattr(info, "st_file_attributes", 0) & 0x400
                                 or info.st_nlink != 1):
            raise ValueError()
    except Exception:
        raise LaunchRefused("audit_write_scope_refused") from None


def _require_safe_writer_scope(lock_file, scratch_root):
    _validate_audit_write_scope(lock_file, scratch_root)
    # The bootstrap must install the pinned named-mutex shim before importing
    # the historical reader. Its lock argument is only a fixed inert sentinel.
    # Scratch creation is disabled, though native-library I/O is not sandboxed.


def _matches(path, expected):
    if not isinstance(expected, str) or not _HEX.fullmatch(expected):
        return False
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError:
        return False
    return digest.hexdigest() == expected


class _BasicLimit(ctypes.Structure):
    _fields_ = [("per_process_time", ctypes.c_int64), ("per_job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
                ("max_working_set", ctypes.c_size_t), ("active_limit", wintypes.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in
                ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [("basic", _BasicLimit), ("io", _IoCounters), ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t), ("peak_process_memory", ctypes.c_size_t),
                ("peak_job_memory", ctypes.c_size_t)]


class _Job:
    def __init__(self, *, kill_on_close=False, _owner=None):
        if kill_on_close is not False:
            raise LaunchRefused("job_ownership_refused")
        self.handle, self.kill_on_close = None, kill_on_close
        if _owner is not None:
            if kill_on_close is not False:
                raise LaunchRefused("job_ownership_refused")
            # Retain even a partially initialized Job before allocating a handle.
            _owner.resources.job = self
        if os.name != "nt":
            raise LaunchRefused("windows_job_required")
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        api.CreateJobObjectW.restype = wintypes.HANDLE
        api.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        api.SetInformationJobObject.restype = wintypes.BOOL
        api.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        api.AssignProcessToJobObject.restype = wintypes.BOOL
        api.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.api = api
        self.handle = api.CreateJobObjectW(None, None)
        if not self.handle:
            raise LaunchRefused("job_unavailable")
        limits = _ExtendedLimit()
        limits.basic.flags = 0  # Never terminate the job on close.
        if not api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            if _owner is None:
                self.close()
            raise LaunchRefused("job_unavailable")

    def attach(self, process):
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise LaunchRefused("job_assignment_failed")


    def close(self):
        if self.handle:
            closed = self.api.CloseHandle(self.handle)
            if self.kill_on_close is False and not closed:
                raise LaunchRefused("job_close_failed")
            self.handle = None


def _object_pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate")
        obj[key] = value
    return obj


def _validate_output(raw, exit_code, expected_descriptor):
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_object_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(data, dict) or data.get("status") not in ("ANSWER", "ABSTAIN", "DEGRADED", "REFUSED"):
            raise ValueError()
        status = data["status"]
        reason = data.get("reason")
        if reason is not None and (not isinstance(reason, str) or not _REASON.fullmatch(reason)):
            raise ValueError()
        if data.get("schema") == "wd.q4-runtime-bootstrap.v1":
            if (status != "REFUSED" or exit_code != 2 or set(data) != {"schema", "status", "reason"}
                    or reason not in _BOOTSTRAP_REFUSALS):
                raise ValueError()
            return {"schema": "wd.q4-runtime-bootstrap.v1", "status": "REFUSED", "reason": reason}
        # Import failures happen before the reader defines IDENTITY.  Recognize
        # only its exact fixed refusal, never a successful or caller-bound result.
        if (data.get("schema") == "wd.bridge-mind-q4-private-read.v1" and status == "REFUSED"
                and exit_code == 2 and reason == "runtime_unavailable" and data.get("results") == []
                and set(data) == {"schema", "status", "reason", "results"}):
            return {"schema": "wd.bridge-mind-q4-private-read.v1", "status": "REFUSED",
                    "reason": "runtime_unavailable", "results": []}
        if data.get("schema") != "wd.bridge-mind-q4-private-read.v1" or data.get("identity") != "caller_declared_not_authenticated":
            raise ValueError()
        if (status == "REFUSED") != (exit_code == 2) or exit_code not in (0, 2):
            raise ValueError()
        results = data.get("results")
        if not isinstance(results, list) or len(results) > 100:
            raise ValueError()
        out = {"schema": "wd.bridge-mind-q4-private-read.v1", "identity": "caller_declared_not_authenticated",
               "status": status, "reason": "reader_refused" if status == "REFUSED" else None, "results": []}
        if status == "REFUSED":
            if results or reason not in _READER_REFUSALS:
                raise ValueError()
            out["reason"] = reason
            return out
        binding = data.get("binding")
        if not isinstance(binding, dict) or binding.get("descriptor_sha256") != expected_descriptor:
            raise ValueError()
        if binding.get("scope") != _SHARED_SCOPE:
            raise ValueError()
        if not isinstance(binding.get("memory_sha256"), str) or not _HEX.fullmatch(binding["memory_sha256"]):
            raise ValueError()
        rule = data.get("rule")
        if not isinstance(rule, str) or not _TOKEN.fullmatch(rule):
            raise ValueError()
        out.update(scope=_SHARED_SCOPE, descriptor_sha256=expected_descriptor, memory_sha256=binding["memory_sha256"])
        for item in results:
            if not isinstance(item, dict):
                raise ValueError()
            clean = {}
            for key in ("ref", "doc_id"):
                value = item.get(key)
                if value is not None and (not isinstance(value, str) or not _REF.fullmatch(value)):
                    raise ValueError()
                h = item.get(key + "_sha256")
                if not isinstance(h, str) or not _HEX.fullmatch(h):
                    raise ValueError()
                if value is not None and hashlib.sha256(value.encode("utf-8")).hexdigest() != h:
                    raise ValueError()
                clean[key + "_sha256"] = h
                if key == "ref":
                    reference = value
            for key in ("kind", "cell", "tier"):
                if not isinstance(item.get(key), str) or not _TOKEN.fullmatch(item[key]):
                    raise ValueError()
            when = item.get("time_utc")
            if when is not None and (not isinstance(when, str) or not _TIME.fullmatch(when)):
                raise ValueError()
            kind, canonical = item.get("ref_kind"), item.get("canonical_event")
            projected = (kind == "sha256_of_projected_metadata" and canonical is False
                         and item["kind"] == "event" and isinstance(reference, str)
                         and _HEX.fullmatch(reference) is not None)
            if not projected:
                raise ValueError()
            clean.update(ref_kind=kind, canonical_event=canonical)
            for key in ("score", "fused"):
                value = item.get(key)
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError()
            out["results"].append(clean)
        return out
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError, KeyError):
        raise LaunchRefused("invalid_output") from None


def _launch_historical(*, interpreter, interpreter_sha256, bootstrap, bootstrap_sha256, verifier_sha256,
           tuple_args, query, timeout, scratch_root=None, interpreter_identity=None):
    # Deferred: historical force-stop launch is outside this slice.
    raise LaunchRefused('mind_e5_start_refused') from None


def launch(*, bundle_root, deployment_manifest_sha256, interpreter, interpreter_sha256,
           interpreter_root, tuple_args, query, timeout, scratch_root):
    # Deferred: historical force-stop launch is outside this slice.
    raise LaunchRefused('mind_e5_start_refused') from None


def start_e5_worker(*, bundle_root, deployment_manifest_sha256, interpreter, interpreter_sha256,
                    interpreter_root, tuple_args, timeout, scratch_root=None):
    # Deferred: historical force-stop launch is outside this slice.
    raise LaunchRefused('mind_e5_start_refused') from None


def start_e5_worker_graceful(*, enabled=False, **options):
    """Explicit opt-in; requires a separately supplied pin for the new owner code.

    READY returns client and owner. A post-creation refusal returns client=None,
    owner and a visible HOLD/REFUSED status. Keep the owner session alive while
    HOLD persists; its non-daemon keeper intentionally prevents ordinary Python
    exit. No restart or termination is attempted. Refusals before reserving an
    owner raise the fixed LaunchRefused token. After reservation, even Job-only
    cleanup returns the owner and HOLD/REFUSED status. Existing deployed bundles
    are not upgraded here.
    """
    if enabled is not True or "cooperative" in options:
        raise LaunchRefused("mind_e5_start_refused")
    return _start_e5_worker(cooperative=True, **options)


def _start_e5_worker(*, bundle_root, deployment_manifest_sha256, interpreter, interpreter_sha256,
                     interpreter_root, tuple_args, timeout, scratch_root=None, cooperative,
                     bulk_contract_sha256=None, worker_only=False, owner_sink=None,
                     _admission=None, _admission_owner=None, _admission_factory=None):
    if cooperative is not True or type(_admission) is not _LocalAdmission:
        raise LaunchRefused("mind_e5_start_refused") from None
    _admission.assert_worker_start(_admission_owner, _admission_factory)
    job = process = owner = None
    owner_reservation_entered = False
    try:
        if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 1800
                or (bulk_contract_sha256 is not None and
                    (not cooperative or type(bulk_contract_sha256) is not str
                     or bulk_contract_sha256 != BULK_CONTRACT_SHA256
                     or hashlib.sha256(BULK_CONTRACT).hexdigest() != BULK_CONTRACT_SHA256))
                or type(worker_only) is not bool or (worker_only and not cooperative)
                or (owner_sink is not None and (not cooperative or not callable(owner_sink)))
                or type(tuple_args) is not dict
                or set(tuple_args) != set(_WORKER_ONLY_KEYS if worker_only else _PUBLIC_KEYS)):
            raise ValueError("request")
        deadline = time.monotonic() + timeout
        _require_safe_writer_scope(tuple_args["lock-file"], scratch_root)
        from tools import wd_mind_runtime_process as anchored
        bundle = Path(bundle_root)
        if not bundle.is_absolute() or ".." in bundle.parts:
            raise ValueError("bundle")
        manifest_raw = anchored._read_pinned_file(bundle / "deployment-manifest.json", deployment_manifest_sha256,
                                                 limit=4 * 1024 * 1024, retain=True)
        manifest = json.loads(manifest_raw.decode("utf-8-sig"), object_pairs_hook=anchored._unique_json_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")))
        if type(manifest) is not dict or manifest.get("schema_version") != 1 or type(manifest.get("files")) is not dict:
            raise ValueError("manifest")

        def pin(relative):
            value = manifest["files"].get(relative)
            if type(value) is not str or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
                raise ValueError("pin")
            path = bundle.joinpath(*relative.split("/"))
            return path, value.lower(), anchored._read_pinned_file(path, value.lower(), limit=8 * 1024 * 1024, retain=True)

        def same_path(left, right):
            return os.path.normcase(os.path.abspath(os.fspath(left))) == os.path.normcase(os.path.abspath(os.fspath(right)))

        bootstrap, bootstrap_sha, bootstrap_raw = pin("tools-bootstrap/tools/wd_mind_runtime_bootstrap.py")
        verifier, verifier_sha, _ = pin("tools-bootstrap/tools/wd_mind_runtime_manifest.py")
        _, mutex_sha, _ = pin("tools-bootstrap/tools/wd_mind_runtime_mutex.py")
        worker_path, worker_sha, worker_raw = pin("tools-bootstrap/tools/wd_mind_e5_worker.py")
        client_path, _, _ = pin("tools-bootstrap/tools/wd_mind_e5_client.py")
        child_path, _, _ = pin("tools-bootstrap/tools/wd_mind_runtime_child.py")
        process_path, _, _ = pin("tools-bootstrap/tools/wd_mind_runtime_process.py")
        identity_path, _, _ = pin("tools-bootstrap/tools/wd_mind_interpreter_identity.py")
        lifecycle_path = pin("tools-bootstrap/tools/wd_mind_e5_lifecycle.py")[0] if cooperative else None
        if bulk_contract_sha256 is not None and (BULK_CONTRACT not in bootstrap_raw or BULK_CONTRACT not in worker_raw):
            raise ValueError('bulk_contract_missing_from_pinned_code')
        if not same_path(__file__, child_path) or not same_path(anchored.__file__, process_path):
            raise ValueError("unpackaged_process_code")
        origins = [("tools.wd_mind_interpreter_identity", identity_path),
                               ("tools.wd_mind_runtime_manifest", verifier),
                               ("tools.wd_mind_e5_client", client_path)]
        if cooperative:
            origins.append(("tools.wd_mind_e5_lifecycle", lifecycle_path))
        for name, expected in origins:
            loaded = sys.modules.get(name)
            origin = getattr(loaded, "__file__", None) if loaded is not None else None
            if loaded is None:
                spec = importlib.util.find_spec(name)
                origin = None if spec is None else spec.origin
            if not origin or not same_path(origin, expected):
                raise ValueError("unpackaged_module")
        runtime_manifest, runtime_sha, runtime_raw = pin("wd-mind-runtime-manifest.json")
        _, identity_sha, identity_bytes = pin("wd-mind-interpreter-identity.json")
        _, binding_sha, binding_bytes = pin("wd-mind-e5-encoder-binding.json")
        if (not same_path(tuple_args["runtime-manifest"], runtime_manifest)
                or tuple_args["runtime-manifest-sha256"] != runtime_sha):
            raise ValueError("runtime_manifest_binding")
        binding = json.loads(binding_bytes.decode("utf-8"), object_pairs_hook=anchored._unique_json_object,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")))
        policy = {"truncate_chars": 2000, "normalize": True, "dtype": "float32", "dim": 384}
        if (type(binding) is not dict or set(binding) != {"schema", "encoder", "code_sha256", "passage_policy", "query_policy"}
                or binding["schema"] != "wd.mind-memory-encoder-binding.v1" or binding["code_sha256"] != worker_sha
                or binding["passage_policy"] != dict(policy, prefix="passage: ")
                or binding["query_policy"] != dict(policy, prefix="query: ")
                or type(binding["passage_policy"].get("normalize")) is not bool
                or type(binding["query_policy"].get("normalize")) is not bool
                or type(binding["passage_policy"].get("truncate_chars")) is not int
                or type(binding["query_policy"].get("truncate_chars")) is not int
                or type(binding["passage_policy"].get("dim")) is not int
                or type(binding["query_policy"].get("dim")) is not int
                or type(binding["encoder"]) is not dict
                or set(binding["encoder"]) != {"model", "revision", "weights_sha256"}
                or binding["encoder"]["model"] != "intfloat/multilingual-e5-small"
                or binding["encoder"]["revision"] != tuple_args["runtime-revision"]
                or type(binding["encoder"]["weights_sha256"]) is not str
                or _HEX.fullmatch(binding["encoder"]["weights_sha256"]) is None):
            raise ValueError("encoder_binding")
        runtime_document = json.loads(runtime_raw.decode("utf-8"), object_pairs_hook=anchored._unique_json_object)
        weights = [row for row in runtime_document.get("files", [])
                   if type(row) is dict and row.get("path") == "model/model.safetensors"]
        if len(weights) != 1 or weights[0].get("sha256") != binding["encoder"]["weights_sha256"]:
            raise ValueError("model_binding")
        anchored.verify_runtime_process_preflight(
            interpreter=interpreter, interpreter_sha256=interpreter_sha256,
            bootstrap=bootstrap, bootstrap_sha256=bootstrap_sha, bundle_root=bundle,
            deployment_manifest_sha256=deployment_manifest_sha256, verifier=verifier, verifier_sha256=verifier_sha,
            runtime_root=tuple_args["runtime-root"], runtime_manifest=runtime_manifest, runtime_manifest_sha256=runtime_sha,
            generation=tuple_args["runtime-generation"], revision=tuple_args["runtime-revision"])
        identity = importlib.import_module("tools.wd_mind_interpreter_identity")
        identity.verify_interpreter_identity(enabled=True, root=interpreter_root,
            descriptor_bytes=identity_bytes, expected_sha256=identity_sha,
            expected_interpreter=interpreter, expected_interpreter_sha256=interpreter_sha256)
        client_module = importlib.import_module("tools.wd_mind_e5_client")
        internal = dict(tuple_args, **{"mutex-sha256": mutex_sha})
        args = [str(_path(interpreter)), "-I", "-S", "-B", "-c", _TRAMPOLINE, str(bootstrap)]
        for key in (_KEYS if not worker_only else tuple(k for k in _KEYS if k not in ('memory','descriptor','descriptor-sha256'))):
            args.extend(("--" + key, internal[key]))
        if worker_only:
            args.append('--worker-only')
        args.extend(("--worker-sha256", worker_sha, "--weights-sha256", binding["encoder"]["weights_sha256"]))
        if bulk_contract_sha256 is not None:
            args.extend(('--bulk-contract-sha256', bulk_contract_sha256))
        env = _worker_environment(tuple_args["runtime-root"])
        if cooperative:
            lifecycle = importlib.import_module("tools.wd_mind_e5_lifecycle")
            if time.monotonic() >= deadline:
                raise ValueError("deadline")
            if bulk_contract_sha256 is not None:
                if (lifecycle.BULK_CONTRACT != BULK_CONTRACT or lifecycle.BULK_CONTRACT_SHA256 != BULK_CONTRACT_SHA256
                        or lifecycle.BULK_STDOUT_BYTES != 64 * 1024 * 1024
                        or client_module.BULK_CONTRACT_SHA256 != BULK_CONTRACT_SHA256):
                    raise ValueError('bulk_contract_mismatch')
                owner_reservation_entered = True
                owner = lifecycle.reserve(enabled=True, bulk_contract_sha256=bulk_contract_sha256,
                                          require_spawn_attempt=True)
            else:
                owner_reservation_entered = True
                owner = lifecycle.reserve(enabled=True, require_spawn_attempt=True)
            _admission.capture_worker(_admission_owner, _admission_factory, owner)
            owner.mark_job_attempt()
            if owner_sink is not None:
                owner_sink(owner)
            owner.resources.job = job = _Job(kill_on_close=False, _owner=owner)
            owner.set_job(job)
            if time.monotonic() >= deadline:
                raise ValueError("deadline")
        if cooperative:
            owner.mark_spawn_attempt()
        process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE,
                                   cwd=str(bootstrap.parent), env=env, creationflags=subprocess.CREATE_NO_WINDOW)
        if cooperative:
            # Store the returned process before calling the fallible bind hook.
            owner.resources.process = process
            owner.bind(process)
            if time.monotonic() >= deadline:
                raise ValueError("deadline")
        job.attach(process)
        if cooperative:
            owner.write(b"\0", deadline)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("deadline")
            if bulk_contract_sha256 is not None:
                client = client_module.GracefulE5WorkerClient(owner, timeout=timeout,
                    encoder_binding_sha256=binding_sha, bulk_contract_sha256=bulk_contract_sha256,
                    handshake_deadline=deadline)
            else:
                client = client_module.GracefulE5WorkerClient(owner, timeout=timeout,
                    encoder_binding_sha256=binding_sha, handshake_deadline=deadline)
            if time.monotonic() >= deadline:
                raise ValueError("deadline")
            owner.mark_ready()
        result = {"client": client, "encoder_binding_bytes": binding_bytes, "encoder_binding_sha256": binding_sha,
                "authority": "none", "network_isolation": "UNKNOWN", "native_closure": "UNKNOWN",
                "same_user_race": "UNKNOWN", "source_binding": "NOT_PROVEN"}
        if cooperative:
            result.update(status="READY", owner=owner, lifecycle=owner.status())
            if bulk_contract_sha256 is not None:
                result['bulk_contract_sha256'] = bulk_contract_sha256
        return result
    except Exception:
        if cooperative:
            if owner is not None:
                status = owner.shutdown(timeout=0)
                return {"status": "HOLD" if status["owned"] else "REFUSED", "client": None,
                        "owner": owner, "lifecycle": status, "authority": "none"}
            if not owner_reservation_entered and job is None and process is None:
                _admission.note_pre_resource_refusal(_admission_owner, _admission_factory)
            raise LaunchRefused("mind_e5_start_refused") from None
    raise LaunchRefused("mind_e5_start_refused") from None


_PARENT_ACTIVE = None


def _read_local_policy_frame(stream, *, require_eof=True):
    """Read the bounded one-shot policy frame; never log or echo its bytes."""
    def read_exact(size):
        output = bytearray()
        while len(output) < size:
            part = stream.read(size - len(output))
            if type(part) is not bytes or not part or len(part) > size - len(output):
                raise ValueError('local_frame')
            output.extend(part)
        return bytes(output)
    header = read_exact(8)
    magic, length = struct.unpack('>4sI', header)
    if magic != b'MCL1' or not 1 <= length <= 65536:
        raise ValueError('local_frame')
    policy = read_exact(length)
    if require_eof and stream.read(1) != b'':
        raise ValueError('local_frame')
    return policy


def _local_query_entry(*, control_stream, output_stream, bundle_root, expected_manifest_sha256,
                       lane, policy_sha256, approval_reference_sha256, _cleanup_evidence=None, _admission=None):
    """Refuse the unbudgeted entry before reading or starting an owner."""
    return 2


_LOCAL_SERVICE_CLOSE_GRACE_SECONDS = 60.0
_LOCAL_ADAPTER_CLOSE_RETRY_SECONDS = 1.0


def _local_service_close_clock():
    """A failed clock disables early close; it never abandons retained state."""
    try:
        now = time.monotonic()
        return now if type(now) in (int, float) and math.isfinite(now) else None
    except BaseException:
        return None


def _local_candidate_owner_entry(*, control_stream, output_stream, bundle_root, expected_manifest_sha256,
                                 policy_sha256, approval_reference_sha256, policy_path=None, _cleanup_evidence=None,
                                 _admission=None, _host_start_gate=None, _host_stop=None,
                                 _host_owner_start_inflight=None, include_start_diagnostics=False,
                                 include_worker_stage_diagnostics=False):
    """Retain one owner and a reference-only service until a graceful stop.

    The service does not authenticate clients or establish source correctness;
    every consumer must independently verify the proposed source references.
    """
    from tools import wd_mind_runtime_process as process
    policy = adapter = service = service_thread = control_thread = close_worker_thread = None
    stop, service_done, control_done = threading.Event(), threading.Event(), threading.Event()
    close_worker_done = threading.Event()
    service_gate, adapter_close_gate = threading.Lock(), threading.Lock()
    output_gate = threading.Lock()
    service_start_committed = False
    stop_fence_uncertain = threading.Event()
    adapter_close_done = threading.Event()
    adapter_close_started, cleanup_complete = threading.Event(), threading.Event()
    close_diagnostic_done = threading.Event()
    close_diagnostic_thread = None
    service_error, control_error = [], []
    last_state, failed, start_failure = None, False, None
    last_diagnostic, diagnostic_count = None, 0
    service_ready_observed = False
    service_ready_observed_event = threading.Event()
    diagnostic_stop, diagnostic_done, diagnostic_thread = threading.Event(), threading.Event(), None
    worker_exit_snapshot_lock = threading.Lock()
    worker_exit_snapshot = None

    class _HostStopBeforeStart(Exception):
        pass

    def write_record(raw, *, best_effort=False):
        if not output_gate.acquire(blocking=not best_effort):
            return False
        try:
            offset = 0
            while offset < len(raw):
                count = output_stream.write(memoryview(raw)[offset:])
                if type(count) is not int or not 0 < count <= len(raw) - offset:
                    raise ValueError('status_write')
                offset += count
            output_stream.flush()
            return True
        finally:
            output_gate.release()

    def emit(state):
        nonlocal last_state
        # The status stream cannot revoke a buffered or blocked READY write
        # when an internal close begins. Never advertise that state here.
        if state == 'READY':
            raise ValueError('ready_status_disabled')
        if state == last_state:
            return
        raw = json.dumps({'schema':'wd.mind-local-owner-status.v1', 'state':state,
            'manifest_sha256':expected_manifest_sha256, 'authority':'none',
            'caller_authentication':'UNKNOWN', 'source_authentication':'UNKNOWN',
            'index_refresh':'NOT_SCHEDULED'}, sort_keys=True, separators=(',', ':')).encode('ascii') + b'\n'
        write_record(raw)
        last_state = state

    def emit_diagnostic():
        nonlocal last_diagnostic, diagnostic_count
        if ((include_start_diagnostics is not True and include_worker_stage_diagnostics is not True)
                or diagnostic_count >= 2
                or (service_ready_observed and include_worker_stage_diagnostics is not True)):
            return
        value = start_failure
        if adapter is not None:
            try:
                candidate = adapter.start_diagnostic()
                if type(candidate) is dict and candidate.get('authority') == 'none':
                    proposed = _validated_start_failure({'reason': candidate.get('reason'),
                                                         'exception_type': candidate.get('exception_type')})
                    if proposed != {'reason': 'UNKNOWN', 'exception_type': 'UNKNOWN'} or value is None:
                        value = proposed
            except BaseException:
                pass
        safe = _validated_start_failure(value)
        # A worker exit after service readiness is not a startup failure; only its fixed,
        # untrusted worker-exit diagnostic may be reported in v2.
        if service_ready_observed:
            safe = {'reason': 'UNKNOWN', 'exception_type': 'UNKNOWN'}
        key = (safe['reason'], safe['exception_type'])
        if include_worker_stage_diagnostics is True:
            hint, exit_code = 'UNKNOWN', None
            with worker_exit_snapshot_lock:
                captured = worker_exit_snapshot
            if service_ready_observed and captured is not None:
                hint, exit_code = captured
            elif adapter is not None:
                try: hint, exit_code = _safe_worker_diagnostic(adapter.worker_exit_diagnostic())
                except BaseException: pass
            key = key + (hint, exit_code)
            if (key == last_diagnostic or (last_diagnostic is not None and
                    ((last_diagnostic[2] != 'UNKNOWN' or last_diagnostic[3] is not None) or
                     (hint == 'UNKNOWN' and exit_code is None)))):
                return
            record = {'schema':'wd.mind-local-owner-start-diagnostic.v2', 'reason':key[0],
                      'exception_type':key[1], 'worker_exit_stage_hint':key[2], 'worker_exit_code':key[3],
                      'authority':'none'}
        else:
            if key == last_diagnostic or (last_diagnostic is not None and last_diagnostic != ('UNKNOWN', 'UNKNOWN')):
                return
            record = {'schema':'wd.mind-local-owner-start-diagnostic.v1', 'reason':key[0],
                      'exception_type':key[1], 'authority':'none'}
        raw = json.dumps(record, sort_keys=True, separators=(',', ':')).encode('ascii') + b'\n'
        if len(raw) > 1024:
            raise ValueError('diagnostic_size')
        write_record(raw)
        last_diagnostic, diagnostic_count = key, diagnostic_count + 1

    def diagnostic_worker():
        try:
            while True:
                emit_diagnostic()
                if diagnostic_stop.wait(0.05):
                    emit_diagnostic()  # One final observation after close; duplicates are suppressed.
                    return
        except BaseException:
            pass
        finally:
            diagnostic_done.set()

    def start_diagnostic_worker():
        nonlocal diagnostic_thread
        if diagnostic_thread is not None:
            return
        try:
            diagnostic_thread = threading.Thread(target=diagnostic_worker,
                name='mind-owner-start-diagnostic', daemon=False)
            diagnostic_thread.start()
        except BaseException:
            pass  # A possibly-started thread stays reachable through cleanup.

    def report_verified_worker_exit_during_hold():
        nonlocal failed
        if (include_worker_stage_diagnostics is not True or not service_ready_observed
                or diagnostic_thread is not None or adapter is None):
            return
        try:
            with worker_exit_snapshot_lock:
                captured = worker_exit_snapshot
            hint, exit_code = (captured if captured is not None else
                               _safe_worker_diagnostic(adapter.worker_exit_diagnostic()))
            if hint != 'UNKNOWN' or (exit_code is not None and exit_code != 0):
                failed = True
                start_diagnostic_worker()
        except BaseException:
            pass  # An unverified observation remains UNKNOWN/HOLD.

    def service_start_released():
        with service_gate:
            if not service_start_committed:
                return True
            return (service_done.is_set() and service_thread is not None
                    and not service_thread.is_alive())

    def cancelled_unstarted_is_unowned(status):
        if (type(adapter) is not _LocalManifestQuery or type(status) is not dict
                or status.get('state') != 'NEW' or status.get('owned') is not False):
            return False
        # Read the two sides of the STOP/start fence together. Never wait on
        # an uncertain gate or hold it while asking the owner for status.
        if not adapter._owner_start_gate.acquire(blocking=False):
            return False
        try:
            return adapter._cancel.is_set() and adapter._owner_start_committed is False
        finally:
            adapter._owner_start_gate.release()

    def capture_worker_exit_after_incomplete_close():
        nonlocal worker_exit_snapshot
        if include_worker_stage_diagnostics is not True or not service_ready_observed_event.is_set():
            return
        try:
            hint = _safe_worker_diagnostic(adapter.worker_exit_diagnostic())
            if hint[0] != 'UNKNOWN' or (hint[1] is not None and hint[1] != 0):
                with worker_exit_snapshot_lock:
                    if worker_exit_snapshot is None:
                        worker_exit_snapshot = hint
        except BaseException:
            pass

    def close_adapter_once():
        if adapter_close_done.is_set():
            return True
        if adapter is None or not adapter_close_gate.acquire(blocking=False):
            return False
        attempted = False
        try:
            if adapter_close_done.is_set():
                return True
            if type(adapter) is _LocalManifestQuery:
                attempted = True
                value = adapter.close(_service_start_released=service_start_released,
                                      _close_started=adapter_close_started)
            elif service_start_released():
                attempted = True
                value = adapter.close()
                adapter_close_started.set()
            else:
                return False
            if (type(value) is dict and value.get('state') == 'STOPPED'
                    and value.get('owned') is False):
                adapter_close_done.set()
                return True
            # A retry may retire the owner before the main cleanup loop gets
            # to observe its already-verified exit. Preserve only fixed,
            # sanitized evidence after an incomplete close; never retain text.
            capture_worker_exit_after_incomplete_close()
        except BaseException:
            if attempted:
                capture_worker_exit_after_incomplete_close()
        finally:
            adapter_close_gate.release()
        return False

    def close_diagnostic_worker(start_time):
        try:
            if start_time is None:
                return
            if not adapter_close_started.wait(_LOCAL_SERVICE_CLOSE_GRACE_SECONDS):
                return
            current = _local_service_close_clock()
            if current is None:
                return
            remaining = max(0, start_time + _LOCAL_SERVICE_CLOSE_GRACE_SECONDS - current)
            if cleanup_complete.wait(remaining):
                return
            raw = (b'{"authority":"none","reason":"BOUND_EXPIRED_WORKER_UNCONFIRMED",'
                   b'"schema":"wd.mind-local-owner-close-diagnostic.v1"}\n')
            # If another writer is blocked, do not wait for it or interfere
            # with the close thread. Delivery in that case remains unknown.
            write_record(raw, best_effort=True)
        except BaseException:
            pass
        finally:
            close_diagnostic_done.set()

    def accept_stop():
        """Fence both entry shutdown paths against a later owner commitment."""
        with service_gate:
            if type(adapter) is _LocalManifestQuery:
                try:
                    with adapter._owner_start_gate:
                        adapter._cancel.set()
                        stop.set()
                except BaseException:
                    # Wake cleanup, but never turn an uncertain stop fence
                    # into clean reservation-release evidence.
                    stop_fence_uncertain.set()
                    stop.set()
            else:
                if ((adapter is None and (close_worker_thread is not None or control_thread is not None))
                        or (adapter is not None and _admission is not None)):
                    stop_fence_uncertain.set()
                stop.set()
            return _local_service_close_clock()

    def read_stop():
        nonlocal close_diagnostic_thread
        try:
            if control_stream.read(1) not in (b'S', b''):
                control_error.append(True)
        except BaseException:
            control_error.append(True)
        finally:
            # STOP acceptance and the owner's start commitment use the same
            # short gate, independently of close-worker timing.
            stop_time = accept_stop()
            # The independently owned close worker performs cancellation and
            # retries, even when output or diagnostic-thread start blocks.
            # A diagnostic may never precede a verified close entry.
            adapter_close_started.wait()
            try:
                close_diagnostic_thread = threading.Thread(target=close_diagnostic_worker,
                    args=(stop_time,), name='mind-owner-close-diagnostic', daemon=False)
                close_diagnostic_thread.start()
            except BaseException:
                pass  # The potentially-started thread remains owned in cleanup.
    def run_close_worker():
        try:
            stop.wait()
            # The adapter first requests cancellation, but retains the owner
            # while a committed service start may still use it.
            while not adapter_close_done.is_set():
                if close_adapter_once():
                    break
                adapter_close_done.wait(0.25)
        finally:
            close_worker_done.set()

    def start_service():
        try:
            service.start()
        except BaseException:
            service_error.append(True)
        finally:
            service_done.set()

    def run_control():
        try:
            read_stop()
        except BaseException:
            # A failed reader is not evidence of a clean control exchange.
            # Request cooperative cleanup, while preserving the failure.
            control_error.append(True)
            try:
                accept_stop()
            except BaseException:
                pass  # The monitor and ownership evidence remain fail-closed.
        finally:
            control_done.set()

    try:
        # A running pipe cannot answer any candidate request while this gate
        # is disabled. Refuse before starting an owner or advertising READY.
        from tools import wd_mind_full_runtime as runtime
        if runtime._STARTUP_OWNER_SOURCE_BUDGET_ENABLED is not True:
            raise ValueError('candidate_gate_disabled')
        if policy_path is None:
            policy = _read_local_policy_frame(control_stream, require_eof=False)
        else:
            from tools import wd_mind_absent_generation_prefix_readonly as absent
            if type(policy_path) is not str:
                raise ValueError('policy_path')
            policy = absent._read_pinned(Path(policy_path), policy_sha256, 65536)
        adapter = create_local_manifest_query(enabled=True, bundle_root=bundle_root,
            expected_manifest_sha256=expected_manifest_sha256, identifier_policy_bytes=policy,
            expected_identifier_policy_sha256=policy_sha256,
            operator_approval_reference_sha256=approval_reference_sha256, lane='codex-lead-1',
            policy_path=policy_path, _admission=_admission, _host_start_gate=_host_start_gate,
            _host_stop=_host_stop, _host_owner_start_inflight=_host_owner_start_inflight)
        policy = None
        # A host-accepted STOP and adapter-start commitment share one gate.
        # CLI stream callers have no host gate and retain their old semantics.
        host_args = (_host_start_gate, _host_stop, _host_owner_start_inflight)
        if not (all(value is None for value in host_args) or all(value is not None for value in host_args)):
            raise ValueError('host_start_guard')
        # Retain the close worker before admitting any owner start. If thread
        # startup is uncertain, cleanup must keep HOLD until it proves exit.
        close_worker_thread = threading.Thread(target=run_close_worker,
            name='mind-local-close', daemon=False)
        close_worker_thread.start()
        # The framed policy was fully consumed above. There is now exactly
        # one control reader, retained even if start() fails ambiguously.
        control_thread = threading.Thread(target=run_control, name='mind-local-control', daemon=False)
        control_thread.start()
        with service_gate:
            if stop.is_set():
                raise _HostStopBeforeStart()
        if _host_start_gate is None:
            start_status = adapter.start()
        else:
            with _host_start_gate:
                if _host_stop.is_set():
                    raise _HostStopBeforeStart()
                start_status = adapter.start()
        if start_status.get('state') != 'STARTING':
            raise ValueError('owner_start')
        emit('STARTING')
        while not stop.is_set():
            if control_error:
                raise ValueError('control_reader_failed')
            owner_status = adapter.status()
            if stop.is_set():
                break
            # Service creation is reachable only after an owned READY state.
            # A query can make the owner BUSY before READY was emitted; still
            # inspect service completion/errors and do not fail that query.
            busy = (type(owner_status) is dict and owner_status.get('state') == 'BUSY'
                    and service is not None)
            if not busy and (type(owner_status) is not dict or owner_status.get('state') not in ('STARTING', 'READY')):
                failed = True
                break
            if owner_status.get('state') == 'READY' and service is None:
                if owner_status.get('owned') is not True or adapter._owner is None:
                    raise ValueError('owner_not_ready')
                name = process.local_candidate_pipe_name(manifest_sha256=expected_manifest_sha256)
                candidate_service = process.create_local_candidate_service(enabled=True, owner=adapter, pipe_name=name)
                with service_gate:
                    if not stop.is_set():
                        service = candidate_service
                        service_thread = threading.Thread(target=start_service, name='mind-local-service-start', daemon=False)
                        service_start_committed = True
                if service_thread is not None:
                    service_thread.start()
            if service is not None and service_done.is_set():
                if service_error:
                    failed = True
                    break
                service_status = service.status()
                if type(service_status) is not dict or service_status.get('state') != 'RUNNING':
                    failed = True
                    break
                if runtime._STARTUP_OWNER_SOURCE_BUDGET_ENABLED is not True:
                    failed = True
                    break
                # Internal observation is used only for diagnostic phase
                # tracking; it is not a readiness claim on the status wire.
                service_ready_observed = True
                service_ready_observed_event.set()
            stop.wait(0.1)
        # EOF/STOP can arrive before the first status poll. Do not turn a
        # startup failure that is already visible into a successful stop.
        # STARTING and BUSY remain acceptable during an intentional stop.
        if stop.is_set():
            try:
                final_status = adapter.status()
                if (type(final_status) is not dict or
                        (final_status.get('state') not in ('STARTING', 'READY', 'BUSY') and
                         not (type(adapter) is _LocalManifestQuery and adapter._cancel.is_set()
                              and final_status.get('state') == 'HOLD') and
                          not cancelled_unstarted_is_unowned(final_status) and
                         not (adapter_close_done.is_set() and final_status.get('state') == 'STOPPED'
                              and final_status.get('owned') is False))):
                    failed = True
            except BaseException:
                failed = True
    except _HostStopBeforeStart:
        pass
    except BaseException as exc:
        start_failure = {'reason': 'OWNER_ENTRY', 'exception_type': _local_exception_type(exc)}
        failed = True
    finally:
        policy = None
        accept_stop()
        if failed:
            try: emit('HOLD')
            except BaseException: pass
            if (include_worker_stage_diagnostics is True or
                    (include_start_diagnostics is True and not service_ready_observed)):
                start_diagnostic_worker()
        if service_thread is not None:
            while not service_done.is_set() or service_thread.is_alive():
                try: emit('HOLD')
                except BaseException: failed = True
                report_verified_worker_exit_during_hold()
                try: service_done.wait(0.25)
                except BaseException: pass
            # STOP may have arrived while service.start was still in flight;
            # observe its failure after joining rather than losing it.
            if service_error:
                failed = True
                try: emit('HOLD')
                except BaseException: pass
                if (include_worker_stage_diagnostics is True or
                        (include_start_diagnostics is True and not service_ready_observed)):
                    start_diagnostic_worker()
        early_adapter_closed = False
        if service is not None:
            # Do not let an unresponsive candidate service indefinitely defer
            # the owner's cooperative close.  The exact adapter type protects
            # this early cancellation path: its close sets cancellation and
            # the owner serializes close against any in-flight candidate.
            # The service itself remains owned and in HOLD until its close is
            # actually confirmed; the grace deadline never proves cleanup.
            clock = _local_service_close_clock()
            next_adapter_close = (clock + _LOCAL_SERVICE_CLOSE_GRACE_SECONDS
                                  if clock is not None else None)
            while True:
                try:
                    service_status = service.close(timeout=0)
                    if type(service_status) is dict and service_status.get('state') == 'STOPPED':
                        break
                    if (type(service_status) is dict and service_status.get('state') == 'FAILED'
                            and type(service) is process._LocalCandidateService):
                        # The pinned service returns FAILED from close only
                        # after listener closure and all worker/wake exits are
                        # positively established. Preserve the failure result.
                        failed = True
                        try: emit('HOLD')
                        except BaseException: pass
                        if (include_worker_stage_diagnostics is True or
                                (include_start_diagnostics is True and not service_ready_observed)):
                            start_diagnostic_worker()
                        break
                except BaseException:
                    pass
                clock = _local_service_close_clock()
                if clock is None:
                    next_adapter_close = None
                if (not early_adapter_closed and next_adapter_close is not None
                        and clock is not None and clock >= next_adapter_close
                        and type(adapter) is _LocalManifestQuery):
                    try:
                        early_adapter_closed = close_adapter_once()
                    except BaseException:
                        pass  # The owner remains retained in HOLD.
                    clock = _local_service_close_clock()
                    next_adapter_close = (clock + _LOCAL_ADAPTER_CLOSE_RETRY_SECONDS
                                          if clock is not None else None)
                try: emit('HOLD')
                except BaseException: failed = True
                report_verified_worker_exit_during_hold()
                try: time.sleep(0.25)
                except BaseException: pass
        if adapter is not None:
            while not early_adapter_closed:
                try:
                    if close_adapter_once():
                        break
                except BaseException:
                    pass
                try: emit('HOLD')
                except BaseException: failed = True
                report_verified_worker_exit_during_hold()
                try: time.sleep(0.25)
                except BaseException: pass
            # The asynchronous preparation can fail after STOP's last status
            # poll but before close finishes. Preserve that evidence instead of
            # returning success merely because resource cleanup succeeded.
            if type(adapter) is _LocalManifestQuery:
                try: startup_failed = adapter.start_failure_observed()
                except BaseException: startup_failed = None
                try: runtime_failed = adapter.close_failure_observed()
                except BaseException: runtime_failed = None
                if startup_failed is not False or runtime_failed is not False:
                    failed = True
                    try: emit('HOLD')
                    except BaseException: pass
                    if (include_worker_stage_diagnostics is True or
                            (include_start_diagnostics is True and not service_ready_observed)):
                        start_diagnostic_worker()
            # Close may finish on the worker before either cleanup loop polls.
            # Consume any fixed exit evidence captured before owner retirement.
            with worker_exit_snapshot_lock:
                captured = worker_exit_snapshot
            if captured is not None:
                try: emit('HOLD')
                except BaseException: failed = True
                report_verified_worker_exit_during_hold()
        if control_thread is not None:
            while not control_done.is_set() or control_thread.is_alive():
                try: emit('HOLD')
                except BaseException: failed = True
                report_verified_worker_exit_during_hold()
                try: control_thread.join(timeout=0.25)
                except BaseException: pass
        if close_worker_thread is not None:
            while not close_worker_done.is_set() or close_worker_thread.is_alive():
                try: close_worker_done.wait(0.25)
                except BaseException: pass
        cleanup_complete.set()
        if close_diagnostic_thread is not None:
            while not close_diagnostic_done.is_set() or close_diagnostic_thread.is_alive():
                try: close_diagnostic_done.wait(0.25)
                except BaseException: pass
        if diagnostic_thread is not None:
            diagnostic_stop.set()
            while not diagnostic_done.is_set():
                try: diagnostic_done.wait(0.25)
                except BaseException: pass  # A failed start may still have scheduled the thread.
            while diagnostic_thread.is_alive():
                try: diagnostic_thread.join(timeout=0.25)
                except BaseException: pass  # Retain the writer and remain HOLD until it exits.
        if stop_fence_uncertain.is_set():
            failed = True
        if _cleanup_evidence is not None and not stop_fence_uncertain.is_set():
            _cleanup_evidence['clean'] = True
        try: emit('STOPPED')
        except BaseException: failed = True
    return 2 if failed or control_error else 0


def _wait_parent_pair_release(pair):
    """Do not let an uncertain parent process abandon retained child owners."""
    if pair._thread is None:
        return
    while True:
        try:
            if pair._done.is_set() and not pair._thread.is_alive():
                return
            time.sleep(0.25)
        except BaseException:
            # Uncertain cleanup is held; no process termination is attempted.
            pass


def _parent_control_entry(*, control_stream, output_stream, bundle_root, expected_manifest_sha256):
    """Own the parent/child graph; authorization remains refused by default."""
    global _PARENT_ACTIVE
    pair = None
    try:
        if _PARENT_ACTIVE is not None:
            raise ValueError('parent_busy')
        _require_verified_parent_spawn()
        from tools import wd_mind_full_runtime as runtime
        deadline = time.monotonic() + 300
        buffer = runtime._ParentControlBuffer(runtime._PrivateParentStream(control_stream), prefix_budget=133)
        buffer.read_from(enabled=True)
        if time.monotonic() >= deadline:
            raise ValueError('deadline')
        controls = _derive_parent_child_controls(enabled=True, control_buffer=buffer,
                                                  expected_manifest_sha256=expected_manifest_sha256)
        pair = create_retained_parent_pair(enabled=True, bundle_root=bundle_root,
            expected_manifest_sha256=expected_manifest_sha256, controls=controls, deadline_monotonic=deadline)
        _PARENT_ACTIVE = pair  # Retain before any potentially interrupted start.
        token = pair.start()
        result = pair.wait(token)
        offset = 0
        while offset < len(result):
            if time.monotonic() >= deadline:
                raise ValueError('deadline')
            count = output_stream.write(memoryview(result)[offset:])
            if type(count) is not int or not 0 < count <= len(result) - offset:
                raise ValueError('write')
            offset += count
        output_stream.flush()
        if time.monotonic() >= deadline:
            raise ValueError('deadline')
        return 0
    except BaseException:
        if pair is not None:
            pair.cancel()
            _wait_parent_pair_release(pair)
        return 2
    finally:
        # A still-running thread retains this process and both child owners.
        if pair is not None and pair._done.is_set() and pair._thread is not None and not pair._thread.is_alive():
            _PARENT_ACTIVE = None


def _emit_preowner_diagnostic(out, manifest, reason, exception_type, v2):
    """Emit only fixed non-sensitive status/diagnostic records."""
    status = {'schema':'wd.mind-local-owner-status.v1', 'state':'HOLD',
        'manifest_sha256':manifest, 'authority':'none',
        'caller_authentication':'UNKNOWN', 'source_authentication':'UNKNOWN',
        'index_refresh':'NOT_SCHEDULED'}
    diagnostic = {'schema':'wd.mind-local-owner-start-diagnostic.v2' if v2 else
        'wd.mind-local-owner-start-diagnostic.v1', 'reason':reason,
        'exception_type':exception_type, 'authority':'none'}
    if v2:
        diagnostic.update(worker_exit_stage_hint='UNKNOWN', worker_exit_code=None)
    for record in (status, diagnostic):
        data = json.dumps(record, sort_keys=True, separators=(',', ':')).encode('ascii') + b'\n'
        offset = 0
        while offset < len(data):
            count = out.write(memoryview(data)[offset:])
            if type(count) is not int or not 0 < count <= len(data) - offset:
                raise ValueError('diagnostic_write')
            offset += count
        out.flush()


def _emit_preanchor_diagnostic(out):
    """Emit a fixed refusal without asserting an unavailable manifest pin."""
    data = b'{"authority":"none","reason":"IMPORT","schema":"wd.mind-local-owner-preanchor-diagnostic.v1"}\n'
    offset = 0
    while offset < len(data):
        count = out.write(memoryview(data)[offset:])
        if type(count) is not int or not 0 < count <= len(data) - offset:
            raise ValueError('diagnostic_write')
        offset += count
    out.flush()


def _guarded_local_entry(entry, **kwargs):
    """Guard both fixed local CLI routes, not arbitrary Python callers or old bundles."""
    if entry is _local_query_entry:
        return 2  # The closed route must not reserve or change the owner marker.
    try:
        guard = _reserve_local_owner(_AUDIT_ROOT)
    except LaunchRefused as exc:
        # This is a pre-owner failure. Do not inspect or repair the marker;
        # never expose its path, bytes, or the underlying exception.
        v1 = kwargs.get('include_start_diagnostics') is True
        v2 = kwargs.get('include_worker_stage_diagnostics') is True
        manifest = kwargs.get('expected_manifest_sha256')
        if (entry is _local_candidate_owner_entry and (v1 or v2)
                and type(manifest) is str and _HEX.fullmatch(manifest)):
            try:
                reason = {'owner_reservation_missing':'ADMISSION_MISSING',
                          'owner_reservation_reserved':'ADMISSION_RESERVED',
                          'owner_reservation_lock_unavailable':'ADMISSION_LOCK_UNAVAILABLE'}.get(str(exc), 'ADMISSION')
                _emit_preowner_diagnostic(kwargs['output_stream'], manifest, reason, 'LaunchRefused', v2)
            except Exception:
                pass  # Output uncertainty remains a refusal, never READY.
        return 2
    cleanup_evidence = {'clean': False}
    admission = _LocalAdmission(guard, cleanup_evidence)
    try:
        result = entry(_cleanup_evidence=cleanup_evidence, _admission=admission, **kwargs)
        if cleanup_evidence['clean'] is True and admission.quiescent():
            admission.release_clean()
        else:
            return 2
        if type(result) is not int or result != 0:
            return 2
        return result
    finally:
        admission.abandon()


def _load_pinned_canonical_child(bundle, expected_manifest_sha256):
    """Load verified bytes under one package identity before owner admission.

    The caller independently pins the manifest. Same-user path races and
    caller authentication remain UNKNOWN; this is not a sandbox.
    """
    names = ('tools', 'tools.wd_mind_runtime_child')
    inserted = []
    _imp.acquire_lock()
    try:
        if (not isinstance(bundle, Path) or not bundle.is_absolute()
                or type(expected_manifest_sha256) is not str
                or not _HEX.fullmatch(expected_manifest_sha256)
                or any(name == 'tools' or name.startswith('tools.') for name in sys.modules)):
            raise ValueError('canonical_input')

        def read_regular(path, limit):
            before = os.lstat(path)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or getattr(before, 'st_file_attributes', 0) & 0x400):
                raise ValueError('canonical_file')
            with path.open('rb') as stream:
                opened = os.fstat(stream.fileno())
                if ((opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                        or opened.st_nlink != 1):
                    raise ValueError('canonical_identity')
                raw = stream.read(limit + 1)
                after = os.fstat(stream.fileno())
            if (len(raw) > limit or (after.st_dev, after.st_ino, after.st_size)
                    != (opened.st_dev, opened.st_ino, opened.st_size)
                    or len(raw) != opened.st_size):
                raise ValueError('canonical_changed')
            return raw

        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('canonical_duplicate')
                result[key] = value
            return result

        manifest_raw = read_regular(bundle / 'deployment-manifest.json', 4 * 1024 * 1024)
        if not hmac.compare_digest(hashlib.sha256(manifest_raw).hexdigest(), expected_manifest_sha256):
            raise ValueError('canonical_manifest')
        manifest = json.loads(manifest_raw.decode('utf-8-sig'), object_pairs_hook=unique)
        paths = ('tools-bootstrap/tools/__init__.py', 'tools-bootstrap/tools/wd_mind_runtime_child.py')
        source_paths = tuple(bundle.joinpath(*name.split('/')) for name in paths)
        if not os.path.samefile(source_paths[1], Path(__file__)):
            raise ValueError('canonical_script')
        raw_files = tuple(read_regular(path, 8 * 1024 * 1024) for path in source_paths)
        for name, raw in zip(paths, raw_files):
            digest = manifest['files'][name]
            if (type(digest) is not str or not _HEX.fullmatch(digest.lower())
                    or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), digest.lower())):
                raise ValueError('canonical_pin')
        # Do not execute arbitrary package initialization as part of the
        # bootstrap; today's pinned package is inert. Nonempty code refuses.
        if ast.parse(raw_files[0].decode('utf-8-sig'), filename=str(source_paths[0])).body:
            raise ValueError('canonical_package_not_inert')

        for name, path, raw in zip(names, source_paths, raw_files):
            if name in sys.modules:
                raise ValueError('canonical_collision')
            spec = importlib.util.spec_from_file_location(name, path,
                submodule_search_locations=[str(path.parent)] if name == 'tools' else None)
            if spec is None or spec.loader is None:
                raise ValueError('canonical_spec')
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            inserted.append(name)
            exec(compile(raw, str(path), 'exec'), module.__dict__)
            if sys.modules.get(name) is not module:
                raise ValueError('canonical_replaced')
            if any(key.startswith('tools.') and key != names[1] for key in sys.modules):
                raise ValueError('canonical_foreign_import')
        setattr(sys.modules['tools'], 'wd_mind_runtime_child', sys.modules[names[1]])
        return sys.modules[names[1]]
    except BaseException:
        for name in reversed(inserted):
            sys.modules.pop(name, None)
        raise LaunchRefused('canonical_child_refused') from None
    finally:
        _imp.release_lock()


def _fixed_child_main(argv):
    """Route fixed parent and owner flags; other inputs refuse."""
    if argv and argv[0] == '--mind-local-query':
        return 2  # Before manifest loading, admission, or other side effects.
    diagnostic_flag = argv[-1] if argv and argv[-1] in ('--start-diagnostics', '--start-diagnostics-v2') else None
    diagnostic_optin = diagnostic_flag == '--start-diagnostics'
    worker_diagnostic_optin = diagnostic_flag == '--start-diagnostics-v2'
    if diagnostic_flag:
        if not argv or argv[0] != '--mind-local-candidate-owner': return 2
        argv = argv[:-1]
    local_owner = (len(argv) == 5 and argv[0] == '--mind-local-candidate-owner'
                   and argv[1] == '--policy-sha256' and argv[3] == '--approval-reference-sha256'
                   and _HEX.fullmatch(argv[2]) and _HEX.fullmatch(argv[4]))
    local_owner_path = (len(argv) == 7 and argv[0] == '--mind-local-candidate-owner'
                        and argv[1] == '--policy-path' and argv[3] == '--policy-sha256'
                        and argv[5] == '--approval-reference-sha256'
                        and _HEX.fullmatch(argv[4]) and _HEX.fullmatch(argv[6]))
    if argv not in (['--mind-owner-child'], ['--parent-control']) and not local_owner and not local_owner_path:
        return 2
    expected = ''
    try:
        expected = os.environ.get('WD_REBOOT_EXPECTED_MANIFEST_HASH', '').lower()
        if not _HEX.fullmatch(expected):
            if (local_owner or local_owner_path) and (diagnostic_optin or worker_diagnostic_optin):
                try:
                    _emit_preanchor_diagnostic(sys.stdout.buffer)
                except BaseException:
                    pass  # Output uncertainty remains a refusal, never READY.
            return 2
        bundle = str(Path(__file__).resolve().parents[2])
        if __name__ == '__main__':
            try:
                canonical = _load_pinned_canonical_child(Path(bundle), expected)
            except BaseException:
                if (local_owner or local_owner_path) and (diagnostic_optin or worker_diagnostic_optin):
                    try:
                        _emit_preanchor_diagnostic(sys.stdout.buffer)
                    except BaseException:
                        pass
                return 2
            return canonical._fixed_child_main(argv + ([diagnostic_flag] if diagnostic_flag else []))
        if local_owner_path:
            return _guarded_local_entry(_local_candidate_owner_entry, control_stream=sys.stdin.buffer, output_stream=sys.stdout.buffer,
                bundle_root=bundle, expected_manifest_sha256=expected, policy_path=argv[2],
                policy_sha256=argv[4], approval_reference_sha256=argv[6], include_start_diagnostics=diagnostic_optin,
                include_worker_stage_diagnostics=worker_diagnostic_optin)
        if local_owner:
            return _guarded_local_entry(_local_candidate_owner_entry, control_stream=sys.stdin.buffer, output_stream=sys.stdout.buffer,
                bundle_root=bundle, expected_manifest_sha256=expected, policy_sha256=argv[2],
                approval_reference_sha256=argv[4], include_start_diagnostics=diagnostic_optin,
                include_worker_stage_diagnostics=worker_diagnostic_optin)
        if argv == ['--parent-control']:
            return _parent_control_entry(control_stream=sys.stdin.buffer, output_stream=sys.stdout.buffer,
                                         bundle_root=bundle, expected_manifest_sha256=expected)
        return _owner_child_entry(control_stream=sys.stdin.buffer, data_stream=sys.stdout.buffer,
                                  candidate_stream=sys.stderr.buffer, bundle_root=bundle,
                                  expected_manifest_sha256=expected)
    except BaseException:
        return 2


if __name__ == '__main__':
    raise SystemExit(_fixed_child_main(sys.argv[1:]))
