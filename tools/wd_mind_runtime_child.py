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
# One fixed fleet-wide lock name preserves the reader-slot sharing contract.
# It is an inert sentinel under the operator's audit tree; no lock file is opened.
_AUDIT_ROOT = Path(r"C:\Python\project2\.codex-audit")
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


class _LocalManifestQuery:
    """Retain one pinned owner in this process; no lane identity is authenticated.

    The caller must retain this object through STARTING or HOLD and must not
    terminate an owned worker on a timeout. Neither startup latency nor
    automatic refresh is provided by this adapter.
    """
    __slots__ = ('_owner', '_metadata', '_bundle', '_manifest')

    def __init__(self, owner, lane, bundle, manifest):
        self._owner = owner
        self._metadata = {'lane': lane}
        self._bundle, self._manifest = bundle, manifest

    def __repr__(self):
        return '<PrivateMindLocalManifestQuery>'

    def start(self):
        return self._owner.start()

    def status(self):
        return self._owner.status()

    def close(self):
        return self._owner.close()

    def query(self):
        from tools import wd_mind_full_runtime as runtime
        try:
            lane = self._metadata['lane']
            scope = _startup_scope_for_lane(lane)
            controls = runtime._bundle_inputs(Path(self._bundle), self._manifest)
            fleet_sha = hashlib.sha256(controls[0]).hexdigest()
            result = self._owner.query_local(enabled=True, lane=lane, scope=scope,
                                             query=runtime._STARTUP_PIPE_QUERY)
            raw = runtime.reader._canonical(result)
            if not 0 < len(raw) <= 16384:
                raise ValueError('result_size')
            # The owner re-reads and screens selected source rows. Validate the
            # lane-facing envelope and recalculate age at this last boundary.
            return _RetainedParentPair._validated_result(self, raw, fleet_sha)
        except Exception:
            raise LaunchRefused('mind_e5_start_refused') from None


def create_local_manifest_query(*, enabled=False, bundle_root=None, expected_manifest_sha256=None,
                                identifier_policy_bytes=None, expected_identifier_policy_sha256=None,
                                operator_approval_reference_sha256=None, lane=None,
                                worker_timeout_seconds=300, lock_timeout_seconds=0, shutdown_timeout=5):
    """Prepare, but do not start, a pinned in-process Mind owner.

    The manifest and operator-policy inputs must come independently from the
    authorized caller. Matching hashes do not authenticate that caller. The
    retained result must be kept alive across a start, query, timeout or HOLD.
    """
    result = binding = factory = owner = None
    try:
        from tools import wd_mind_full_runtime as runtime
        from tools import wd_mind_e5_client as client
        if (enabled is not True or type(bundle_root) is not str
                or type(expected_manifest_sha256) is not str
                or type(identifier_policy_bytes) is not bytes
                or type(expected_identifier_policy_sha256) is not str
                or type(operator_approval_reference_sha256) is not str):
            raise ValueError('input')
        _startup_scope_for_lane(lane)
        runtime.create_operator_pinned_policy_anchor(enabled=True, bundle_root=bundle_root,
            expected_manifest_sha256=expected_manifest_sha256,
            operator_policy_sha256=expected_identifier_policy_sha256,
            operator_approval_reference_sha256=operator_approval_reference_sha256)
        binding = runtime.assemble_full_runtime(enabled=True, bundle_root=bundle_root,
            expected_manifest_sha256=expected_manifest_sha256, identifier_policy_bytes=identifier_policy_bytes,
            expected_identifier_policy_sha256=expected_identifier_policy_sha256)
        factory = runtime.create_manifest_worker_factory(enabled=True, bundle_root=bundle_root,
            expected_manifest_sha256=expected_manifest_sha256, worker_timeout_seconds=worker_timeout_seconds,
            lock_timeout_seconds=lock_timeout_seconds, bulk_contract_sha256=client.BULK_CONTRACT_SHA256)
        owner = runtime.create_single_manifest_owner(enabled=True, binding=binding, lane=lane,
            worker_factory=factory, shutdown_timeout=shutdown_timeout)
        # Return the owner before any start operation can launch a worker.
        result = _LocalManifestQuery(owner, lane, bundle_root, expected_manifest_sha256)
    except Exception:
        pass
    finally:
        identifier_policy_bytes = binding = factory = owner = None
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
                     bulk_contract_sha256=None, worker_only=False, owner_sink=None):
    if cooperative is not True:
        raise LaunchRefused("mind_e5_start_refused") from None
    job = process = owner = None
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
        env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR") if key in os.environ}
        env.update(TEMP=os.devnull, TMP=os.devnull, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        for name in ("HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE", "HF_ASSETS_CACHE",
                     "SENTENCE_TRANSFORMERS_HOME", "TORCH_HOME", "XDG_CACHE_HOME", "CUDA_CACHE_PATH"):
            env[name] = os.devnull
        if cooperative:
            lifecycle = importlib.import_module("tools.wd_mind_e5_lifecycle")
            if time.monotonic() >= deadline:
                raise ValueError("deadline")
            if bulk_contract_sha256 is not None:
                if (lifecycle.BULK_CONTRACT != BULK_CONTRACT or lifecycle.BULK_CONTRACT_SHA256 != BULK_CONTRACT_SHA256
                        or lifecycle.BULK_STDOUT_BYTES != 64 * 1024 * 1024
                        or client_module.BULK_CONTRACT_SHA256 != BULK_CONTRACT_SHA256):
                    raise ValueError('bulk_contract_mismatch')
                owner = lifecycle.reserve(enabled=True, bulk_contract_sha256=bulk_contract_sha256)
            else:
                owner = lifecycle.reserve(enabled=True)
            if owner_sink is not None:
                owner_sink(owner)
            owner.resources.job = job = _Job(kill_on_close=False, _owner=owner)
            owner.set_job(job)
            if time.monotonic() >= deadline:
                raise ValueError("deadline")
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
                client = client_module.GracefulE5WorkerClient(owner, timeout=remaining,
                    encoder_binding_sha256=binding_sha, bulk_contract_sha256=bulk_contract_sha256)
            else:
                client = client_module.GracefulE5WorkerClient(owner, timeout=remaining,
                                                             encoder_binding_sha256=binding_sha)
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
            raise LaunchRefused("mind_e5_start_refused") from None
    raise LaunchRefused("mind_e5_start_refused") from None


_PARENT_ACTIVE = None


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


def _fixed_child_main(argv):
    """Route only the fixed parent and owner flags; all other inputs refuse."""
    if argv not in (['--mind-owner-child'], ['--parent-control']):
        return 2
    try:
        expected = os.environ.get('WD_REBOOT_EXPECTED_MANIFEST_HASH', '').lower()
        if not _HEX.fullmatch(expected):
            return 2
        bundle = str(Path(__file__).resolve().parents[2])
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
