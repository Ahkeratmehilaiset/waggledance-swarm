"""Experimental Windows fixed-peer pipe candidate. NOT a production transport.

No work happens on import. Explicit calls accept only synthetic Python -c
programs using this interpreter, the separately pinned synthetic echo peer, or
the fixed peer entrypoint of THIS file under a caller-pinned plan. Its public
snapshot backend is explicitly enabled only and can load a pinned model; it
has only been exercised with a synthetic runtime. There is no general
executable/wrapper adapter, installer or CLI. The public child redirects its
standard streams before loading the backend, but other output sinks and
same-user access remain uncontained; do not use it with sensitive data.
The caller must independently pin/trust this module, interpreter and peer code.
Self-hashing the child source is consistency, not authentication.

All transport handles are created non-inheritable. The child is created with
inherit_handles=False, suspended, then assigned to a kill-on-close job before
resume. Both peers check kernel-reported PID and process creation time before
payload transfer. The pipe has an explicit protected logon-SID DACL and refuses
remote clients. This is NOT a sandbox against malicious same-user code capable
of opening another process with PROCESS_DUP_HANDLE.

No security test, production readiness or whole-OS hard deadline is claimed.
The exchange timeout is followed by a separate, fixed five-second cleanup
wait budget; callers requiring a whole-call deadline must allow for it and
independently bound potentially blocking kernel/attestation operations.
Abrupt parent death before job assignment may leave a suspended child; no
payload is sent before assignment. Cancellation with uncompleted kernel I/O
quarantines its memory/handles and disables further calls, rather than freeing
an in-use OVERLAPPED or claiming complete cleanup. Lead owns conformance tests.
"""

import base64
import contextlib
import ctypes as C
from ctypes import wintypes as W
import hashlib
import hmac
import io
import json
import math
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import threading
import time
import uuid


_MAX_INPUT = 65536
_MAX_OUTPUT = 262144
_MAX_META = 65536
_CLEANUP_WAIT_SECONDS = 5.0
_INVALID = C.c_void_p(-1).value
_QUARANTINE = []  # At most one invocation; any entry disables new work.
_RUN_LOCK = threading.Lock()


class _Refused(ValueError):
    def __init__(self):
        super().__init__("secure_transport_refused")


class TransportCleanupUnknown(RuntimeError):
    """Cleanup is not confirmed; this process must not start another transport."""
    def __init__(self, prior_outcome):
        self.prior_outcome = prior_outcome
        super().__init__("secure_transport_cleanup_unknown")


class _SA(C.Structure):
    _fields_ = [("length", W.DWORD), ("descriptor", C.c_void_p), ("inherit", W.BOOL)]


class _OV(C.Structure):
    _fields_ = [("internal", C.c_size_t), ("internal_high", C.c_size_t),
                ("offset", W.DWORD), ("offset_high", W.DWORD), ("event", W.HANDLE)]


class _Basic(C.Structure):
    _fields_ = [("process_time", C.c_longlong), ("job_time", C.c_longlong),
                ("flags", W.DWORD), ("min_working", C.c_size_t),
                ("max_working", C.c_size_t), ("active_limit", W.DWORD),
                ("affinity", C.c_size_t), ("priority", W.DWORD),
                ("scheduling", W.DWORD)]


class _Counters(C.Structure):
    _fields_ = [(name, C.c_ulonglong) for name in
                ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]


class _Limits(C.Structure):
    _fields_ = [("basic", _Basic), ("io", _Counters),
                ("process_memory", C.c_size_t), ("job_memory", C.c_size_t),
                ("peak_process", C.c_size_t), ("peak_job", C.c_size_t)]


class _JobAccounting(C.Structure):
    _fields_ = [("total_user", C.c_longlong), ("total_kernel", C.c_longlong),
                ("period_user", C.c_longlong), ("period_kernel", C.c_longlong),
                ("page_faults", W.DWORD), ("total_processes", W.DWORD),
                ("active_processes", W.DWORD), ("terminated_processes", W.DWORD)]


class _StartupInfo(C.Structure):
    _fields_ = [("cb", W.DWORD), ("reserved", W.LPWSTR), ("desktop", W.LPWSTR),
                ("title", W.LPWSTR), ("x", W.DWORD), ("y", W.DWORD),
                ("xsize", W.DWORD), ("ysize", W.DWORD), ("xcount", W.DWORD),
                ("ycount", W.DWORD), ("fill", W.DWORD), ("flags", W.DWORD),
                ("show", W.WORD), ("reserved2size", W.WORD), ("reserved2", C.c_void_p),
                ("std_in", W.HANDLE), ("std_out", W.HANDLE), ("std_err", W.HANDLE)]


class _StartupInfoEx(C.Structure):
    _fields_ = [("startup", _StartupInfo), ("attributes", C.c_void_p)]


class _ProcessInfo(C.Structure):
    _fields_ = [("process", W.HANDLE), ("thread", W.HANDLE),
                ("pid", W.DWORD), ("tid", W.DWORD)]


class _SidAttributes(C.Structure):
    _fields_ = [("sid", C.c_void_p), ("attributes", W.DWORD)]


class _Groups(C.Structure):
    _fields_ = [("count", W.DWORD), ("groups", _SidAttributes * 1)]


def _api():
    if os.name != "nt":
        raise _Refused()
    k = C.WinDLL("kernel32", use_last_error=True)
    a = C.WinDLL("advapi32", use_last_error=True)
    signatures = {
        "CloseHandle": (W.BOOL, [W.HANDLE]),
        "GetCurrentProcess": (W.HANDLE, []),
        "GetHandleInformation": (W.BOOL, [W.HANDLE, C.POINTER(W.DWORD)]),
        "GetProcessTimes": (W.BOOL, [W.HANDLE] + [C.POINTER(W.FILETIME)] * 4),
        "OpenProcess": (W.HANDLE, [W.DWORD, W.BOOL, W.DWORD]),
        "GetExitCodeProcess": (W.BOOL, [W.HANDLE, C.POINTER(W.DWORD)]),
        "GetNamedPipeClientProcessId": (W.BOOL, [W.HANDLE, C.POINTER(W.DWORD)]),
        "GetNamedPipeServerProcessId": (W.BOOL, [W.HANDLE, C.POINTER(W.DWORD)]),
        "CreateNamedPipeW": (W.HANDLE, [W.LPCWSTR, W.DWORD, W.DWORD, W.DWORD,
                                      W.DWORD, W.DWORD, W.DWORD, C.POINTER(_SA)]),
        "CreateFileW": (W.HANDLE, [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p,
                                 W.DWORD, W.DWORD, W.HANDLE]),
        "CreateEventW": (W.HANDLE, [C.c_void_p, W.BOOL, W.BOOL, W.LPCWSTR]),
        "ConnectNamedPipe": (W.BOOL, [W.HANDLE, C.POINTER(_OV)]),
        "ReadFile": (W.BOOL, [W.HANDLE, C.c_void_p, W.DWORD,
                             C.POINTER(W.DWORD), C.c_void_p]),
        "WriteFile": (W.BOOL, [W.HANDLE, C.c_void_p, W.DWORD,
                              C.POINTER(W.DWORD), C.c_void_p]),
        "GetOverlappedResult": (W.BOOL, [W.HANDLE, C.POINTER(_OV),
                                        C.POINTER(W.DWORD), W.BOOL]),
        "CancelIoEx": (W.BOOL, [W.HANDLE, C.POINTER(_OV)]),
        "WaitForSingleObject": (W.DWORD, [W.HANDLE, W.DWORD]),
        "CreateJobObjectW": (W.HANDLE, [C.c_void_p, W.LPCWSTR]),
        "SetInformationJobObject": (W.BOOL, [W.HANDLE, C.c_int, C.c_void_p, W.DWORD]),
        "QueryInformationJobObject": (W.BOOL, [W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)]),
        "AssignProcessToJobObject": (W.BOOL, [W.HANDLE, W.HANDLE]),
        "TerminateJobObject": (W.BOOL, [W.HANDLE, W.UINT]),
        "TerminateProcess": (W.BOOL, [W.HANDLE, W.UINT]),
        "ResumeThread": (W.DWORD, [W.HANDLE]),
        "InitializeProcThreadAttributeList": (W.BOOL, [C.c_void_p, W.DWORD, W.DWORD, C.POINTER(C.c_size_t)]),
        "UpdateProcThreadAttribute": (W.BOOL, [C.c_void_p, W.DWORD, C.c_size_t, C.c_void_p, C.c_size_t, C.c_void_p, C.c_void_p]),
        "DeleteProcThreadAttributeList": (None, [C.c_void_p]),
        "CreateProcessW": (W.BOOL, [W.LPCWSTR, W.LPWSTR, C.c_void_p, C.c_void_p, W.BOOL,
                                     W.DWORD, C.c_void_p, W.LPCWSTR, C.POINTER(_StartupInfoEx), C.POINTER(_ProcessInfo)]),
        "LocalFree": (C.c_void_p, [C.c_void_p]),
    }
    for name, (restype, argtypes) in signatures.items():
        function = getattr(k, name)  # Missing capabilities refuse before spawning.
        function.restype, function.argtypes = restype, argtypes
    for name, restype, argtypes in (
        ("OpenProcessToken", W.BOOL, [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]),
        ("GetTokenInformation", W.BOOL, [W.HANDLE, C.c_int, C.c_void_p,
                                        W.DWORD, C.POINTER(W.DWORD)]),
        ("ConvertSidToStringSidW", W.BOOL, [C.c_void_p, C.POINTER(C.c_void_p)]),
        ("ConvertStringSecurityDescriptorToSecurityDescriptorW", W.BOOL,
         [W.LPCWSTR, W.DWORD, C.POINTER(C.c_void_p), C.POINTER(W.DWORD)]),
    ):
        function = getattr(a, name)
        function.restype, function.argtypes = restype, argtypes
    return k, a


def _create_process_in_job(k, job, executable, line, environment, cwd, info):
    """Create the suspended child already in job; info is owned by the caller."""
    size = C.c_size_t()
    k.InitializeProcThreadAttributeList(None, 1, 0, C.byref(size))
    _check(size.value > 0)
    storage = C.create_string_buffer(size.value)
    attributes = C.cast(storage, C.c_void_p)
    # None covers interruption before Python stores the native result. In that
    # state DeleteProcThreadAttributeList is not safe to call blindly.
    initialized = None
    try:
        initialized = bool(k.InitializeProcThreadAttributeList(attributes, 1, 0, C.byref(size)))
        _check(initialized)
        handles = (W.HANDLE * 1)(job)
        # PROC_THREAD_ATTRIBUTE_JOB_LIST; the buffer must outlive the list.
        _check(k.UpdateProcThreadAttribute(attributes, 0, 0x0002000D,
                                           C.cast(handles, C.c_void_p), C.sizeof(handles), None, None))
        startup = _StartupInfoEx()
        startup.startup.cb = C.sizeof(startup)
        startup.attributes = attributes.value
        command_line = C.create_unicode_buffer(line)
        _check(all(type(key) is str and type(value) is str and "\0" not in key and
                   "\0" not in value and "=" not in key for key, value in environment.items()))
        environment_block = C.create_unicode_buffer(
            "\0".join(f"{key}={value}" for key, value in sorted(environment.items(), key=lambda pair: pair[0].upper())) + "\0\0")
        # CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT | CREATE_SUSPENDED |
        # EXTENDED_STARTUPINFO_PRESENT; no inherited handles.
        _check(k.CreateProcessW(executable, command_line, None, None, False, 0x08080404,
                                C.cast(environment_block, C.c_void_p), cwd,
                                C.byref(startup), C.byref(info)))
        return info.process, info.thread, info.pid, info.tid
    finally:
        if initialized is None:
            # Retain the caller-owned buffer and fail closed on future calls.
            # This does not establish whether native initialization completed.
            _QUARANTINE.append((k, None, ("attribute_init_unknown", storage, attributes)))
        elif initialized:
            k.DeleteProcThreadAttributeList(attributes)


def _check(ok):
    if not ok:
        raise _Refused()


def _handle(value):
    if value is None or value == 0 or value == _INVALID:
        raise _Refused()
    return value


def _noninheritable(k, handle):
    flags = W.DWORD()
    _check(k.GetHandleInformation(handle, C.byref(flags)))
    _check(not (flags.value & 1))


def _created(k, handle):
    fields = [W.FILETIME() for _ in range(4)]
    _check(k.GetProcessTimes(handle, *(C.byref(field) for field in fields)))
    return (fields[0].dwHighDateTime << 32) | fields[0].dwLowDateTime


def _wait_job_empty(k, job, deadline):
    """Confirm zero active processes on this job; unknown/error returns False.

    A zero-time probe remains possible after the original deadline. The bound
    does not preempt a blocking kernel call or protect against same-user races.
    """
    while True:
        info = _JobAccounting()
        if not k.QueryInformationJobObject(job, 1, C.byref(info), C.sizeof(info), None):
            return False
        if info.active_processes == 0:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.01, remaining))


def _wait_child_exit(k, process, deadline):
    remaining = max(0.0, deadline - time.monotonic())
    timeout_ms = min(0xFFFFFFFE, int(remaining * 1000))
    return k.WaitForSingleObject(int(process), timeout_ms) == 0


def _active(k, handle):
    code = W.DWORD()
    _check(k.GetExitCodeProcess(handle, C.byref(code)))
    _check(code.value == 259)


def _release_once(k, handle, *, local=False):
    """Record uncertain ownership; never retry a failed close/free."""
    try:
        released = not bool(k.LocalFree(handle)) if local else bool(k.CloseHandle(handle))
    except BaseException:
        released = False
    if not released:
        _QUARANTINE.append((k, None, ('local' if local else 'handle', handle)))
    return released


def _peer(k, pipe, expected_pid, expected_created, *, client):
    actual = W.DWORD()
    function = k.GetNamedPipeClientProcessId if client else k.GetNamedPipeServerProcessId
    _check(function(pipe, C.byref(actual)))
    _check(actual.value == expected_pid)
    process = _handle(k.OpenProcess(0x101000, False, expected_pid))
    try:
        _noninheritable(k, process)
        _active(k, process)
        _check(_created(k, process) == expected_created)
    finally:
        prior = _prior_outcome(sys.exc_info()[1])
        if not _release_once(k, process):
            raise TransportCleanupUnknown(prior) from None


def _descriptor(k, a):
    token, text, descriptor = W.HANDLE(), C.c_void_p(), C.c_void_p()
    _check(a.OpenProcessToken(k.GetCurrentProcess(), 8, C.byref(token)))
    try:
        _noninheritable(k, token)
        size = W.DWORD()
        a.GetTokenInformation(token, 28, None, 0, C.byref(size))  # TokenLogonSid.
        _check(C.sizeof(_Groups) <= size.value <= 65536)
        buffer = C.create_string_buffer(size.value)
        _check(a.GetTokenInformation(token, 28, buffer, size, C.byref(size)))
        groups = C.cast(buffer, C.POINTER(_Groups)).contents
        _check(groups.count == 1 and groups.groups[0].attributes & 0xC0000000 == 0xC0000000)
        _check(a.ConvertSidToStringSidW(groups.groups[0].sid, C.byref(text)))
        sid = C.wstring_at(text)
        _check(sid.startswith("S-1-5-5-"))
        sddl = "D:P(A;;GA;;;SY)(A;;GA;;;" + sid + ")"
        _check(a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, C.byref(descriptor), None))
        return descriptor
    finally:
        prior = _prior_outcome(sys.exc_info()[1])
        cleanup_ok = True
        if text.value:
            cleanup_ok = _release_once(k, text, local=True) and cleanup_ok
        cleanup_ok = _release_once(k, token) and cleanup_ok
        # A descriptor produced during a failed operation is not returned to
        # the caller, so this scope still owns its allocation.
        if descriptor.value and (prior != 'none' or not cleanup_ok):
            cleanup_ok = _release_once(k, descriptor, local=True) and cleanup_ok
        if not cleanup_ok:
            raise TransportCleanupUnknown(prior) from None


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("secure_transport_timeout")
    return max(1, min(0xFFFFFFFE, math.ceil(remaining * 1000)))


def _prior_outcome(exc):
    return (exc.prior_outcome if isinstance(exc, TransportCleanupUnknown) else
            "timeout" if isinstance(exc, TimeoutError) else
            "refused" if isinstance(exc, _Refused) else
            "interrupt" if isinstance(exc, KeyboardInterrupt) else
            "exit" if isinstance(exc, SystemExit) else
            "none" if exc is None else "other")


_MAX_EXCEPTION_NODES = 128
_MAX_EXCEPTION_FRAMES = 4096


def _clear_exception_frames(exc):
    """Drop unwound-frame locals across the complete exception graph.

    Active frames cannot be cleared here; the _run frame clears its own input
    locals in its outer finally. This does not authenticate a same-user caller.
    """
    seen, pending, frame_count = set(), [exc], 0
    while pending:
        item = pending.pop()
        if item is None or id(item) in seen:
            continue
        seen.add(id(item))
        if len(seen) > _MAX_EXCEPTION_NODES:
            return False
        pending.extend((item.__context__, item.__cause__))
        if isinstance(item, BaseExceptionGroup):
            if len(item.exceptions) + len(pending) > _MAX_EXCEPTION_NODES:
                return False
            pending.extend(item.exceptions)
        tb = item.__traceback__
        while tb is not None:
            frame_count += 1
            if frame_count > _MAX_EXCEPTION_FRAMES:
                return False
            try:
                tb.tb_frame.clear()
            except RuntimeError:
                if tb.tb_frame.f_code is not _run.__code__:
                    return False
            tb = tb.tb_next
    return True


def _raise_context_free(exc):
    """Raise a fresh, sanitized signal without an active caller's context."""
    try:
        raise exc from None
    except BaseException as sanitized:
        sanitized.__context__ = None
        sanitized.__cause__ = None
        raise


def _io(k, pipe, data, count, deadline, *, connect=False, write=False, eof=False):
    ov = buffer = done = None
    outstanding = False
    signaled = False
    event = _handle(k.CreateEventW(None, True, False, None))
    try:
        ov = _OV(event=event)
        buffer = C.create_string_buffer(data if write else count)
        done = W.DWORD()
        _noninheritable(k, event)
        _remaining(deadline)
        # The call may have reached the kernel even if Python is interrupted
        # before it observes a return value. Preserve every backing object until
        # synchronous completion, a documented terminal result, or quarantine.
        outstanding = True
        if connect:
            ok = k.ConnectNamedPipe(pipe, C.byref(ov))
        else:
            function = k.WriteFile if write else k.ReadFile
            ok = function(pipe, buffer, count, C.byref(done), C.byref(ov))
        error = C.get_last_error() if not ok else 0
        # A returned FALSE with an error other than IO_PENDING is an immediate
        # operation failure, not a queued request. An interruption before this
        # point keeps outstanding=True because no result was observed.
        if not ok and error != 997:
            outstanding = False
        if connect and error == 535:  # Client connected before ConnectNamedPipe.
            outstanding = False
            return b""
        if eof and error == 109:
            outstanding = False
            return b""
        if ok:
            outstanding = False
        _check(ok or error == 997)
        outstanding = not ok
        if outstanding:
            state = k.WaitForSingleObject(event, _remaining(deadline))
            if state == 258:
                raise TimeoutError("secure_transport_timeout")
            _check(state == 0)
            signaled = True
        ok = k.GetOverlappedResult(pipe, C.byref(ov), C.byref(done), False)
        error = C.get_last_error() if not ok else 0
        # Failure is not generally proof that the kernel has finished with
        # OVERLAPPED/buffer. Only completed cancellation and the explicitly
        # handled broken-pipe EOF are treated as terminal here.
        outstanding = not ok and error != 995
        # For a pending pipe read, a signaled event plus BROKEN_PIPE means the
        # read completed unsuccessfully; a required frame still refuses below.
        # This is deliberately not generalized to writes or connect calls.
        if signaled and not connect and not write and error == 109:
            outstanding = False
        if eof and error == 109:
            outstanding = False
            return b""
        _check(ok and done.value <= count)
        if write:
            _check(done.value == count)
            return b""
        return buffer.raw[:done.value]
    finally:
        prior_outcome = _prior_outcome(sys.exc_info()[1])
        retained = False
        if outstanding:
            # A cancel request is not completion. If cancel, wait, or the final
            # completion check fails unexpectedly, retain both the kernel handle
            # and the exact backing Python objects; never retry an uncertain close.
            try:
                cancelled = bool(k.CancelIoEx(pipe, C.byref(ov)))
                if cancelled and k.WaitForSingleObject(event, 1000) == 0:
                    ok = k.GetOverlappedResult(pipe, C.byref(ov), C.byref(done), False)
                    # ERROR_OPERATION_ABORTED explicitly reports completed
                    # cancellation. Other failures do not prove completion.
                    retained = not ok and C.get_last_error() != 995
                else:
                    retained = True
            except BaseException:
                retained = True
            if retained:
                _QUARANTINE.append((k, pipe, ov, buffer, event, done))
        if not retained:
            try:
                closed = bool(k.CloseHandle(event))
            except BaseException:
                closed = False
            if not closed:
                # Completed I/O: the pipe need not be retained, but the event's
                # close is uncertain and the same handle must not be retried.
                _QUARANTINE.append((k, None, ov, buffer, event, done))
                retained = True
        if retained:
            raise TransportCleanupUnknown(prior_outcome) from None


def _read(k, pipe, size, deadline):
    data = bytearray()
    while len(data) < size:
        part = _io(k, pipe, None, size - len(data), deadline)
        _check(bool(part))
        data.extend(part)
    return bytes(data)


def _frame_read(k, pipe, limit, deadline):
    size = struct.unpack(">I", _read(k, pipe, 4, deadline))[0]
    _check(size <= limit)
    return _read(k, pipe, size, deadline)


def _frame_write(k, pipe, data, deadline):
    for part in (struct.pack(">I", len(data)), data):
        if part:
            _io(k, pipe, part, len(part), deadline, write=True)


def _fixed_control(control):
    """Validate fixed-protocol labels only; caller must authenticate the tuple."""
    _check(type(control) is dict and set(control) == {"schema", "nonce", "binding_sha256"})
    control = dict(control)
    _check(control["schema"] == "wd.q4-fixed-control.v1")
    for name, size in (("nonce", 32), ("binding_sha256", 64)):
        value = control[name]
        _check(type(value) is str and len(value) == size and
               all(char in "0123456789abcdef" for char in value))
    return control


def _fixed_ready_exchange(k, pipe, expected_pid, expected_created, control, query, deadline, before_query=None):
    """Synthetic protocol primitive: challenge/order only, not authentication.

    The fixed peer must establish its own code/runtime/data bindings before it
    emits READY. A matching peer declaration does not authenticate those
    bindings. The parent checks peer PID/creation and the closed reply only;
    it does not disclose its expected binding digest in the challenge.
    ``before_query`` runs only after a matching READY and before the query;
    any failure in it is the same fixed refusal.
    No authorized production peer or trusted tuple is supplied by this module.
    """
    raw = ready = ready_raw = challenge = None
    try:
        control = _fixed_control(control)
        _check(type(expected_pid) is int and expected_pid > 0 and
               type(expected_created) is int and expected_created > 0)
        _check(type(query) is bytes and 0 < len(query) <= _MAX_INPUT)
        _peer(k, pipe, expected_pid, expected_created, client=True)
        challenge = {"schema": "wd.q4-fixed-challenge.v1", "nonce": control["nonce"]}
        raw = json.dumps(challenge, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        _frame_write(k, pipe, raw, deadline)
        ready_raw = _frame_read(k, pipe, 4096, deadline)
        _check(type(ready_raw) is bytes and 0 < len(ready_raw) <= 4096)
        ready = json.loads(ready_raw.decode("utf-8", "strict"), object_pairs_hook=_unique_pairs)
        _check(type(ready) is dict and set(ready) == {"schema", "status", "nonce", "binding_sha256"})
        _check(ready["schema"] == "wd.q4-fixed-ready.v1" and ready["status"] == "READY")
        _check(type(ready["nonce"]) is str and type(ready["binding_sha256"]) is str and
               hmac.compare_digest(ready["nonce"], control["nonce"]) and
               hmac.compare_digest(ready["binding_sha256"], control["binding_sha256"]))
        if before_query is not None:
            before_query(control["nonce"])
        _frame_write(k, pipe, query, deadline)
        return
    except TransportCleanupUnknown:
        raise
    except TimeoutError:
        raise TimeoutError("secure_transport_timeout") from None
    except Exception:
        pass
    del control, query, raw, ready, ready_raw, challenge, before_query
    raise _Refused() from None


_MAX_PLAN = 4096
_MAX_BUNDLE = 16 * 1024 * 1024
# Must match runtime_manifest.MAX_MANIFEST_BYTES. This module is also exec'd in
# an isolated child before the pinned verifier is loaded; importing the verifier
# here would break that boundary. A regression test checks the shared limit.
_MAX_RUNTIME_MANIFEST = 8 * 1024 * 1024
_PEER_CODE_LIMIT = 262144
# synthetic-v1 needs no runtime. public-snapshot-v1 is default-off: the parent
# refuses it unless explicitly enabled, and the child verifies the externally
# pinned runtime manifest with the pinned verifier before importing any
# runtime code or site. No approved runtime tree exists on this path yet.
_FIXED_BACKENDS = frozenset({"synthetic-v1", "public-snapshot-v1", "staged-card-wrapper-v1"})
_PLAN_KEYS = frozenset({"schema", "backend", "interpreter_sha256", "peer_code_sha256",
                        "runtime_manifest_sha256", "bundle_sha256", "bundle_bytes"})
_PUBLIC_PINS = ("expected_pointer_sha256", "policy_sha256", "roster_sha256", "expected_public_policy_sha256",
                "expected_source_sha256", "expected_encoder_sha256")
_PUBLIC_PLAN_KEYS = _PLAN_KEYS | frozenset(_PUBLIC_PINS) | frozenset({
    "verifier_sha256", "runtime_root", "runtime_manifest_path", "runtime_generation", "runtime_revision",
    "approved_tasks"})
_STAGED_PLAN_KEYS = _PUBLIC_PLAN_KEYS | frozenset({
    "card_decision_sha256", "card_policy_sha256", "wrapper_sha256", "deployment_manifest_sha256",
    "public_plan_sha256", "host_sha256", "backend_sha256", "bundle_root", "bundle_commit", "wrapper_path", "public_plan_path"})
_PLAN_SCHEMAS = {"wd.q4-fixed-plan.v1": ("synthetic-v1", _PLAN_KEYS),
                 "wd.q4-fixed-plan.v2": ("public-snapshot-v1", _PUBLIC_PLAN_KEYS),
                 "wd.q4-fixed-plan.v3": ("staged-card-wrapper-v1", _STAGED_PLAN_KEYS)}
_VERIFIER_LIMIT = 131072
_VERIFIER_PARTS = ("sandbox", "mind", "step2", "runtime_manifest.py")
_PUBLIC_RUNTIME = "MANIFEST_VERIFIED_BEFORE_IMPORT_NOT_ISOLATED"
_ANSWER_KEYS = frozenset({"schema", "status", "results", "plan_sha256", "bundle_sha256", "backend",
                          "source_authentication", "freshness", "runtime"})
_RESULT_KEYS = frozenset({"ref_sha256", "doc_id_sha256", "stored_text", "ref_kind", "canonical_event"})
_REF_KIND = "sha256_of_projected_metadata"  # shared_result_resolver.REF_KIND
_MAX_RESULTS = 100  # shared_result_resolver.MAX_RESULTS
_PRELOAD_DENY = ("sandbox", "tools", "waggledance", "core", "torch", "numpy", "scipy",
                 "transformers", "sentence_transformers", "cython_runtime")
_CONTAINED = []  # NUL descriptors and sinks kept alive for the whole child process
_BUNDLE_SCHEMA = "wd.q4-fixed-snapshot-bundle.v1"
_BUNDLE_KEYS = frozenset({"schema", "pointer", "policy", "roster", "public_policy", "active_artifacts", "history",
                          "cells"})
# Query-plus-answer binding (v5, RCO2 design 2026-11-06): the child binds its inner answer to the exact query
# it read under the per-call control nonce and the parent verifies that binding before any use. The nonce is
# sent to the child in the challenge, so this binds three byte strings only: it is NOT lane authentication,
# NOT freshness and NOT proof that a model used the query.
_BIND_DOMAIN = b"wd.q4-query-bind.v2"
_BIND_MAGIC = b"WDQ4BND2"
_BIND_OVERHEAD = len(_BIND_MAGIC) + 64  # 8-byte magic + 64 hex MAC characters before the inner answer


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _hex64(value):
    _check(type(value) is str and len(value) == 64 and all(char in "0123456789abcdef" for char in value))
    return value


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _staged_path(value):
    """A closed C-drive spelling; later handle checks still must reject reparse races."""
    _check(type(value) is str and 3 < len(value) <= 512 and value.isascii() and value.isprintable()
           and value[0].lower() == 'c' and value[1:3] == ':\\' and '/' not in value
           and '\\\\' not in value and ':' not in value[2:])
    for part in value[3:].split('\\'):
        stem = part.split('.', 1)[0].rstrip(' ').upper()
        _check(part not in ('', '.', '..') and not part.endswith(('.', ' '))
               and stem not in {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'}
               and stem not in {prefix + str(i) for prefix in ('COM', 'LPT') for i in range(10)})
    return value


def _staged_read(path, pin, limit):
    path = Path(_staged_path(str(path)))
    _hex64(pin)
    for item in (path, *path.parents):
        info = item.lstat()
        _check(not stat.S_ISLNK(info.st_mode) and not (getattr(info, 'st_file_attributes', 0) & 0x400))
    before = path.lstat()
    _check(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= limit)
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        _check(os.path.samestat(before, opened))
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    _check(len(raw) == before.st_size and os.path.samestat(opened, after)
           and os.path.samestat(after, path.lstat()) and hmac.compare_digest(_sha(raw), pin))
    return raw


def _read_staged_backend(peer_path, plan):
    """Read only the named candidate helper, after independent plan/manifest pinning."""
    try:
        root = Path(_staged_path(plan['bundle_root']))
        peer = root / 'tools-bootstrap' / 'tools' / 'wd_q4_secure_transport.py'
        helper = root / 'tools-bootstrap' / 'tools' / 'wd_q4_staged_fixed_backend.py'
        wrapper = root / 'Invoke-WdQ4Query.ps1'
        _check(os.path.normcase(os.path.abspath(peer_path)) == os.path.normcase(os.path.abspath(peer))
               and os.path.normcase(os.path.abspath(plan['wrapper_path'])) == os.path.normcase(os.path.abspath(wrapper)))
        raw = _staged_read(root/'deployment-manifest.json', plan['deployment_manifest_sha256'], 4*1024*1024)
        manifest = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_pairs,
                              parse_constant=lambda _: _check(False))
        _check(type(manifest) is dict and manifest.get('schema_version') == 1
               and manifest.get('source_commit') == plan['bundle_commit'] and type(manifest.get('files')) is dict)
        for rel, path, key, cap in (
            ('tools-bootstrap/tools/wd_q4_secure_transport.py', peer, 'peer_code_sha256', _PEER_CODE_LIMIT),
            ('tools-bootstrap/tools/wd_q4_staged_fixed_backend.py', helper, 'backend_sha256', _PEER_CODE_LIMIT),
            ('Invoke-WdQ4Query.ps1', wrapper, 'wrapper_sha256', 1024*1024)):
            _check(type(manifest['files'].get(rel)) is str
                   and manifest['files'][rel].lower() == plan[key])
            value = _staged_read(path, plan[key], cap)
            if key == 'backend_sha256':
                result = value
        return result
    except Exception:
        raise _Refused() from None


def _fixed_plan(plan_bytes):
    """Closed canonical plan; its digest is the binding both peers derive. Caller pins its digest."""
    _check(type(plan_bytes) is bytes and 0 < len(plan_bytes) <= _MAX_PLAN)
    plan = json.loads(plan_bytes.decode("ascii", "strict"), object_pairs_hook=_unique_pairs,
                      parse_constant=lambda _: _check(False))
    _check(type(plan) is dict and type(plan.get("schema")) is str and plan["schema"] in _PLAN_SCHEMAS)
    backend, keys = _PLAN_SCHEMAS[plan["schema"]]
    _check(set(plan) == keys and plan["backend"] == backend and backend in _FIXED_BACKENDS)
    for name in ("interpreter_sha256", "peer_code_sha256", "runtime_manifest_sha256", "bundle_sha256"):
        _hex64(plan[name])
    _check(type(plan["bundle_bytes"]) is int and 0 < plan["bundle_bytes"] <= _MAX_BUNDLE)
    if backend in ("public-snapshot-v1", "staged-card-wrapper-v1"):
        for name in _PUBLIC_PINS + ("verifier_sha256",):
            _hex64(plan[name])
        for name in ("runtime_root", "runtime_manifest_path"):
            value = plan[name]
            _check(type(value) is str and 0 < len(value) <= 512 and value.isascii() and value.isprintable()
                   and os.path.isabs(value))
        for name in ("runtime_generation", "runtime_revision"):
            value = plan[name]
            _check(type(value) is str and len(value) in (40, 64) and all(c in "0123456789abcdef" for c in value))
        tasks = plan["approved_tasks"]
        _check(type(tasks) is list and 0 < len(tasks) <= 64
               and all(type(t) is str and 0 < len(t) <= 128 and t.isascii() and t.isprintable() for t in tasks)
               and tasks == sorted(set(tasks)))
    if backend == "staged-card-wrapper-v1":
        for name in ("card_decision_sha256", "card_policy_sha256", "wrapper_sha256",
                     "deployment_manifest_sha256", "public_plan_sha256", "host_sha256", "backend_sha256"):
            _hex64(plan[name])
        _check(type(plan["bundle_commit"]) is str and len(plan["bundle_commit"]) == 40
               and all(c in "0123456789abcdef" for c in plan["bundle_commit"]))
        for name in ("bundle_root", "wrapper_path", "public_plan_path", "runtime_root", "runtime_manifest_path"):
            _staged_path(plan[name])
        _check(os.path.normcase(os.path.normpath(plan["wrapper_path"])) ==
               os.path.normcase(os.path.normpath(os.path.join(plan["bundle_root"], "Invoke-WdQ4Query.ps1"))))
    _check(_canonical(plan) == plan_bytes)
    return plan


def _fixed_ack(nonce, bundle_sha256):
    return _canonical({"schema": "wd.q4-fixed-bundle-ack.v1", "status": "BUNDLE_VERIFIED", "nonce": nonce,
                       "bundle_sha256": bundle_sha256})


def _fixed_bind(nonce, query, inner):
    """HMAC-SHA256 keyed by the per-call control nonce over the domain, NUL, the length-framed exact query and
    the length-framed exact inner answer, as 64 lowercase hex bytes. A pure function of its three inputs."""
    _check(type(nonce) is str and len(nonce) == 32 and all(c in "0123456789abcdef" for c in nonce))
    _check(type(query) is bytes and 0 < len(query) <= _MAX_INPUT)
    _check(type(inner) is bytes and 0 < len(inner) <= _MAX_OUTPUT)
    message = _BIND_DOMAIN + b"\0" + struct.pack(">I", len(query)) + query + struct.pack(">I", len(inner)) + inner
    return hmac.new(bytes.fromhex(nonce), message, hashlib.sha256).hexdigest().encode("ascii")


def _fixed_wrap(nonce, query, inner):
    """Child side: magic + MAC + inner. The inner cap stays _MAX_OUTPUT; the frame grows by _BIND_OVERHEAD only."""
    return _BIND_MAGIC + _fixed_bind(nonce, query, inner) + inner


def _fixed_unwrap(nonce, query, frame, inner_limit):
    """Parent side: the inner answer of a frame bound to this call's nonce and exact query, else a fixed refusal.

    Checked before any use of the answer: the magic, the inner length within the caller's cap (the pipe frame
    cap is that cap plus the overhead), then compare_digest against the MAC recomputed over the parent's own
    query bytes. No query, nonce, MAC or frame byte is placed in the refusal.
    """
    mac = inner = None
    try:
        _check(type(frame) is bytes and len(frame) > _BIND_OVERHEAD and frame[:len(_BIND_MAGIC)] == _BIND_MAGIC)
        _check(type(inner_limit) is int and 0 < inner_limit <= _MAX_OUTPUT)
        mac, inner = frame[len(_BIND_MAGIC):_BIND_OVERHEAD], frame[_BIND_OVERHEAD:]
        _check(len(inner) <= inner_limit and hmac.compare_digest(mac, _fixed_bind(nonce, query, inner)))
        return inner
    except Exception:
        pass
    del nonce, query, frame, mac, inner
    raise _Refused() from None


def _fixed_exchange(k, pipe, expected_pid, expected_created, control, plan_bytes, bundle_bytes, query, deadline):
    """Two phases: plan, challenge and READY before any bundle; bundle and ACK before the query.

    Phase 1 sends only the plan and a nonce-only challenge; the bundle is sent
    only after a READY equal to the parent-computed plan digest (never sent to
    the peer). Phase 2 sends the bundle and sends the query only after a closed
    bundle ACK naming the parent-computed bundle digest. Both replies are
    consistency only, not authentication: a peer can echo pins from the plan.
    """
    plan = bundle_sha256 = bundle_phase = None
    try:
        control = _fixed_control(control)
        _check(type(plan_bytes) is bytes and type(bundle_bytes) is bytes and 0 < len(bundle_bytes) <= _MAX_BUNDLE)
        _check(hmac.compare_digest(_sha(plan_bytes), control["binding_sha256"]))
        plan = _fixed_plan(plan_bytes)
        bundle_sha256 = _sha(bundle_bytes)
        _check(len(bundle_bytes) == plan["bundle_bytes"] and hmac.compare_digest(bundle_sha256, plan["bundle_sha256"]))
        _peer(k, pipe, expected_pid, expected_created, client=True)
        _frame_write(k, pipe, plan_bytes, deadline)

        def bundle_phase(nonce):
            _frame_write(k, pipe, bundle_bytes, deadline)
            ack = _frame_read(k, pipe, 4096, deadline)
            _check(type(ack) is bytes and 0 < len(ack) <= 4096)
            parsed = json.loads(ack.decode("ascii", "strict"), object_pairs_hook=_unique_pairs)
            _check(type(parsed) is dict and _canonical(parsed) == ack
                   and hmac.compare_digest(ack, _fixed_ack(nonce, bundle_sha256)))

        _fixed_ready_exchange(k, pipe, expected_pid, expected_created, control, query, deadline,
                              before_query=bundle_phase)
        return
    except TransportCleanupUnknown:
        raise
    except TimeoutError:
        raise TimeoutError("secure_transport_timeout") from None
    except Exception:
        pass
    del control, plan_bytes, bundle_bytes, query, plan, bundle_sha256, bundle_phase
    raise _Refused() from None


def _fixed_child_plan(plan_bytes, peer_code_sha256):
    """Child phase 1, before READY: re-derive the closed plan and check the peer-code pin."""
    plan = _fixed_plan(plan_bytes)
    _check(type(peer_code_sha256) is str and hmac.compare_digest(plan["peer_code_sha256"], _hex64(peer_code_sha256)))
    return plan


def _fixed_child_bundle(plan, bundle_bytes):
    """Child phase 2, before the ACK: re-hash every received bundle byte against the plan."""
    _check(type(bundle_bytes) is bytes and len(bundle_bytes) == plan["bundle_bytes"]
           and hmac.compare_digest(_sha(bundle_bytes), plan["bundle_sha256"]))


def _fixed_child_verify(plan_bytes, bundle_bytes, peer_code_sha256):
    """Both child checks together (plan, then bundle); used by the tests of each check."""
    plan = _fixed_child_plan(plan_bytes, peer_code_sha256)
    _fixed_child_bundle(plan, bundle_bytes)
    return plan


def _b64(data):
    return base64.b64encode(data).decode("ascii")


def _unb64(text):
    _check(type(text) is str)
    data = base64.b64decode(text.encode("ascii"), validate=True)
    _check(_b64(data) == text)
    return data


def _b64_map(mapping):
    _check(type(mapping) is dict and len(mapping) <= 4096
           and all(type(key) is str and type(value) is bytes for key, value in mapping.items()))
    return {key: _b64(value) for key, value in mapping.items()}


def _unb64_map(mapping):
    _check(type(mapping) is dict and len(mapping) <= 4096 and all(type(key) is str for key in mapping))
    return {key: _unb64(value) for key, value in mapping.items()}


def _bounded_fixed_snapshot(pointer_bytes, policy_bytes, roster_bytes, public_policy_bytes,
                            active_artifact_bytes, history_bytes, cell_snapshots):
    """Freeze inputs and count exact canonical JSON bytes before any base64 copy.

    Each map is copied entry by entry (at most 4096 entries); the total
    serialized size never exceeds the existing bundle cap. This is not a large
    snapshot transport and the original byte values are already caller-owned.
    The caller must provide a coherent input snapshot, without concurrent edits.
    """
    size = 0

    def add(n):
        nonlocal size
        size += n
        _check(size <= _MAX_BUNDLE)

    def string(value):
        _check(type(value) is str and len(value) <= _MAX_BUNDLE)
        add(2)
        for char in value:
            point = ord(char)
            if char in ('"', "\\", "\b", "\f", "\n", "\r", "\t"):
                add(2)
            elif point < 0x20 or 0x7f <= point <= 0xffff:
                add(6)
            elif point > 0xffff:
                add(12)
            else:
                add(1)

    def field(key):
        string(key)
        add(1)  # colon

    def blob(value):
        _check(type(value) is bytes)
        add(2 + 4 * ((len(value) + 2) // 3))  # JSON quotes + padded base64
        return value

    def mapping(value):
        _check(type(value) is dict and len(value) <= 4096)
        add(2)  # braces
        out = {}; count = 0
        for key, item in value.items():
            _check(count < 4096)
            if count: add(1)
            count += 1
            field(key)
            out[key] = blob(item)
        return out

    add(2 + 7)  # root braces + seven commas
    field("schema"); string(_BUNDLE_SCHEMA)
    field("pointer"); pointer = blob(pointer_bytes)
    field("policy"); policy = blob(policy_bytes)
    field("roster"); roster = blob(roster_bytes)
    field("public_policy"); public_policy = blob(public_policy_bytes)
    field("active_artifacts"); active = mapping(active_artifact_bytes)
    field("history"); history = mapping(history_bytes)
    field("cells")
    _check(type(cell_snapshots) is dict and len(cell_snapshots) <= 4096)
    add(2)
    cells = {}; count = 0
    for cell_id, item in cell_snapshots.items():
        _check(count < 4096)
        if count: add(1)
        count += 1
        field(cell_id)
        _check(type(item) is dict and len(item) == 3 and all(type(key) is str for key in item)
               and set(item) == {"pointer", "log", "artifacts"})
        # Capture the three values once; all payload bytes are immutable.
        pointer_value, log_value, artifacts_value = item["pointer"], item["log"], item["artifacts"]
        add(4)  # braces + two commas
        field("pointer"); cell_pointer = blob(pointer_value)
        field("log"); cell_log = blob(log_value)
        field("artifacts"); artifacts = mapping(artifacts_value)
        cells[cell_id] = {"pointer": cell_pointer, "log": cell_log, "artifacts": artifacts}
    return pointer, policy, roster, public_policy, active, history, cells, size


def fixed_snapshot_bundle(*, pointer_bytes, policy_bytes, roster_bytes, public_policy_bytes,
                          active_artifact_bytes, history_bytes, cell_snapshots):
    """Closed canonical container for the byte inputs of query_public_snapshot.

    Packaging only: contents are validated by query_public_snapshot itself
    against the plan's pins in the child. Bounded by the 16 MiB bundle cap;
    rejects oversized canonical inputs before creating base64 copies.
    """
    try:
        (pointer_bytes, policy_bytes, roster_bytes, public_policy_bytes, active_artifact_bytes,
         history_bytes, cell_snapshots, expected_size) = _bounded_fixed_snapshot(
             pointer_bytes, policy_bytes, roster_bytes, public_policy_bytes,
             active_artifact_bytes, history_bytes, cell_snapshots)
        cells = {}
        for cell_id, item in cell_snapshots.items():
            _check(type(cell_id) is str and type(item) is dict and set(item) == {"pointer", "log", "artifacts"}
                   and type(item["pointer"]) is bytes and type(item["log"]) is bytes)
            cells[cell_id] = {"pointer": _b64(item["pointer"]), "log": _b64(item["log"]),
                              "artifacts": _b64_map(item["artifacts"])}
        raw = _canonical({"schema": _BUNDLE_SCHEMA, "pointer": _b64(pointer_bytes), "policy": _b64(policy_bytes),
                          "roster": _b64(roster_bytes), "public_policy": _b64(public_policy_bytes),
                          "active_artifacts": _b64_map(active_artifact_bytes), "history": _b64_map(history_bytes),
                          "cells": cells})
        _check(len(raw) == expected_size and len(raw) <= _MAX_BUNDLE)
        return raw
    except Exception:
        pass
    raise _Refused() from None


def _fixed_bundle_decode(bundle_bytes):
    """Strict inverse of fixed_snapshot_bundle; canonical bytes and strict base64 only."""
    try:
        _check(type(bundle_bytes) is bytes and 0 < len(bundle_bytes) <= _MAX_BUNDLE)
        document = json.loads(bundle_bytes.decode("ascii", "strict"), object_pairs_hook=_unique_pairs,
                              parse_constant=lambda _: _check(False))
        _check(type(document) is dict and set(document) == _BUNDLE_KEYS and document["schema"] == _BUNDLE_SCHEMA
               and _canonical(document) == bundle_bytes)
        cells = document["cells"]
        _check(type(cells) is dict and len(cells) <= 4096)
        decoded = {}
        for cell_id, item in cells.items():
            _check(type(cell_id) is str and type(item) is dict and set(item) == {"pointer", "log", "artifacts"})
            decoded[cell_id] = {"pointer": _unb64(item["pointer"]), "log": _unb64(item["log"]),
                                "artifacts": _unb64_map(item["artifacts"])}
        return {"pointer_bytes": _unb64(document["pointer"]), "policy_bytes": _unb64(document["policy"]),
                "roster_bytes": _unb64(document["roster"]), "public_policy_bytes": _unb64(document["public_policy"]),
                "active_artifact_bytes": _unb64_map(document["active_artifacts"]),
                "history_bytes": _unb64_map(document["history"]), "cell_snapshots": decoded}
    except Exception:
        pass
    raise _Refused() from None


_CARD_BUNDLE_SCHEMA = 'wd.q4-fixed-card-bundle.v1'
_CARD_BUNDLE_FIELDS = frozenset({'pointer', 'policy', 'roster', 'public_policy', 'card_decision',
                                 'card_policy', 'memory', 'projection', 'descriptor'})


def fixed_card_bundle(*, pointer, policy, roster, public_policy, card_decision, card_policy,
                      memory, projection, descriptor, enabled=False):
    """Encode bounded caller-provided bytes; this does not validate a C2 snapshot."""
    if enabled is False:
        return {'status': 'OFF'}
    try:
        _check(enabled is True)
        items = {'pointer':pointer, 'policy':policy, 'roster':roster, 'public_policy':public_policy,
                 'card_decision':card_decision, 'card_policy':card_policy, 'memory':memory,
                 'projection':projection, 'descriptor':descriptor}
        _check(all(type(value) is bytes and 0 < len(value) <= _MAX_BUNDLE for value in items.values()))
        # Bound before allocating base64 copies and include room for field names.
        _check(sum(4 * ((len(value) + 2) // 3) for value in items.values()) + 1024 <= _MAX_BUNDLE)
        result = _canonical({'schema': _CARD_BUNDLE_SCHEMA, **{key:_b64(value) for key,value in items.items()}})
        _check(len(result) <= _MAX_BUNDLE)
        return result
    except Exception:
        pass
    raise _Refused() from None


def _fixed_card_bundle_decode(bundle_bytes):
    try:
        _check(type(bundle_bytes) is bytes and 0 < len(bundle_bytes) <= _MAX_BUNDLE)
        document = json.loads(bundle_bytes.decode('ascii', 'strict'), object_pairs_hook=_unique_pairs,
                              parse_constant=lambda _: _check(False))
        _check(type(document) is dict and set(document) == _CARD_BUNDLE_FIELDS | {'schema'}
               and document['schema'] == _CARD_BUNDLE_SCHEMA and _canonical(document) == bundle_bytes)
        result = {key:_unb64(document[key]) for key in _CARD_BUNDLE_FIELDS}
        _check(all(0 < len(value) <= _MAX_BUNDLE for value in result.values()))
        return result
    except Exception:
        pass
    raise _Refused() from None


_NUL_DEVICE = "\\Device\\Null"


class _UnicodeString(C.Structure):
    _fields_ = [("Length", W.USHORT), ("MaximumLength", W.USHORT), ("Buffer", C.c_void_p)]


def _nt_object_name(handle):
    """Kernel object name of ``handle`` (NtQueryObject ObjectNameInformation), e.g. \\Device\\Null."""
    ntdll = C.WinDLL("ntdll")
    ntdll.NtQueryObject.restype = C.c_long
    ntdll.NtQueryObject.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.ULONG, C.POINTER(W.ULONG)]
    buffer, needed = C.create_string_buffer(4096), W.ULONG()
    _check(ntdll.NtQueryObject(handle, 1, buffer, len(buffer), C.byref(needed)) == 0)
    name = _UnicodeString.from_buffer(buffer)
    _check(name.Buffer and name.Length % 2 == 0 and name.Length <= len(buffer))
    return C.wstring_at(name.Buffer, name.Length // 2)


def _contain_child_output():
    """Point THIS process's standard streams at NUL and detach its console; child side, public route only.

    One verified NUL handle (FILE_TYPE_CHAR and kernel object name exactly
    \\Device\\Null, also checked on each standard handle) backs CRT descriptors 0-2
    (Python os.write and C code sharing this UCRT), the Win32 standard handles
    (SetStdHandle, checked with GetStdHandle) and Python's sys streams.
    FreeConsole runs first, before the NUL handle exists; afterwards CONOUT$
    must not open and descriptors 1 and 2 must accept a write. Everything stays open for
    the process lifetime. NOT contained: native code with its own statically
    linked CRT that cached handles before this call, direct writes to other
    files, pipes or devices, OutputDebugString/ETW/event log, crash dumps and
    Windows Error Reporting, and grandchildren started with explicit handles.
    """
    import msvcrt
    k = C.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.restype = W.HANDLE
    k.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
    k.GetFileType.restype = W.DWORD
    k.GetFileType.argtypes = [W.HANDLE]
    k.SetStdHandle.restype = W.BOOL
    k.SetStdHandle.argtypes = [W.DWORD, W.HANDLE]
    k.GetStdHandle.restype = W.HANDLE
    k.GetStdHandle.argtypes = [W.DWORD]
    k.CloseHandle.argtypes = [W.HANDLE]
    invalid = C.c_void_p(-1).value
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    # Detach first: FreeConsole after the redirection invalidated the NUL
    # handles behind descriptors 1 and 2 in a CREATE_NO_WINDOW child. The old
    # descriptors are closed before any new handle exists, so no stale value
    # can be reused and then closed by dup2.
    k.FreeConsole()
    for target in (0, 1, 2):
        try:
            os.close(target)
        except OSError:
            pass
    handle = k.CreateFileW("\\\\.\\NUL", 0xC0000000, 3, None, 3, 0, None)
    _check(handle not in (None, invalid) and k.GetFileType(handle) == 2
           and _nt_object_name(handle) == _NUL_DEVICE)
    fd = msvcrt.open_osfhandle(handle, os.O_RDWR | getattr(os, "O_NOINHERIT", 0))
    _CONTAINED.append(fd)
    for target in (0, 1, 2):
        if target != fd:
            os.dup2(fd, target, inheritable=False)
    for std, target in ((-10, 0), (-11, 1), (-12, 2)):
        native = msvcrt.get_osfhandle(target)
        _check(k.SetStdHandle(std & 0xFFFFFFFF, native) and k.GetStdHandle(std & 0xFFFFFFFF) == native
               and k.GetFileType(native) == 2 and _nt_object_name(native) == _NUL_DEVICE)
    sink = io.TextIOWrapper(io.FileIO(os.dup(fd), "w"), encoding="utf-8", errors="replace", write_through=True)
    source = io.TextIOWrapper(io.FileIO(os.dup(fd), "r"), encoding="utf-8", errors="replace")
    _CONTAINED.extend((sink, source))
    sys.stdout = sys.stderr = sys.__stdout__ = sys.__stderr__ = sink
    sys.stdin = sys.__stdin__ = source
    console = k.CreateFileW("CONOUT$", 0x40000000, 3, None, 3, 0, None)
    if console not in (None, invalid):
        k.CloseHandle(console)
        _check(False)
    _check(all(os.write(target, b"") == 0 for target in (1, 2)))


def _fixed_closed_results(results):
    """Closed result items, both in the child answer and in the parent validation."""
    _check(type(results) is list and len(results) <= _MAX_RESULTS)
    for item in results:
        _check(type(item) is dict and set(item) == _RESULT_KEYS and item["ref_kind"] == _REF_KIND
               and item["canonical_event"] is False and type(item["stored_text"]) is str)
        _hex64(item["ref_sha256"])
        _hex64(item["doc_id_sha256"])
    return results


def _interpreter_roots():
    """The base interpreter's stdlib (Lib) and extension (DLLs) directories only.

    Not the whole base prefix: Scripts, the prefix root and Lib/site-packages
    are refused by _module_origins. Limit: these files are NOT manifested; they
    are trusted only as the pinned interpreter's own installation.
    """
    base = Path(sys.base_prefix)
    return [os.path.normcase(os.path.realpath(path)) for path in (base / "Lib", base / "DLLs")]


def _install_controlled_imports(runtime_root, manifest_path, manifest_sha256):
    """Execute the fixed helper only after verifying its runtime-manifest bytes."""
    root = Path(runtime_root).resolve(strict=True)
    with open(manifest_path, 'rb') as stream:
        raw = stream.read(8 * 1024 * 1024 + 1)
    _check(len(raw) <= 8 * 1024 * 1024 and hmac.compare_digest(_sha(raw), manifest_sha256))
    manifest = json.loads(raw.decode('utf-8', 'strict'), object_pairs_hook=_unique_pairs)
    _check(type(manifest) is dict and type(manifest.get('files')) is list)
    relative = 'assets/code/sandbox/mind/step2/q4_controlled_imports.py'
    matches = [item for item in manifest['files'] if type(item) is dict and item.get('path') == relative]
    _check(len(matches) == 1 and type(matches[0].get('size')) is int
           and 0 < matches[0]['size'] <= 128 * 1024 and type(matches[0].get('sha256')) is str)
    path = root.joinpath(*relative.split('/')).resolve(strict=True)
    _check(_under(os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(root))))
    with open(path, 'rb') as stream:
        code = stream.read(matches[0]['size'] + 1)
    _check(len(code) == matches[0]['size'] and hmac.compare_digest(_sha(code), matches[0]['sha256']))
    namespace = {'__name__': '_wd_q4_controlled_imports'}
    exec(compile(code, '<wd-q4-controlled-imports>', 'exec'), namespace)
    return namespace['install'](root, manifest_path, manifest_sha256, _check=_check, _sha=_sha,
        _unique_pairs=_unique_pairs, _interpreter_roots=_interpreter_roots, _under=_under)


class _FaissSwigOrigin:
    """Bind one runtime-created SWIG capsule to its verified FAISS image.

    This is process-local consistency, not same-user authentication or a
    defense against replacement of mapped images after runtime verification.
    """
    _NAME = 'swig_runtime_data4'
    _CAPSULE = b'swig_runtime_data4.type_pointer_capsule'
    _IMAGES = frozenset({'_swigfaiss.pyd', '_swigfaiss_avx2.pyd'})

    def __init__(self, runtime_root, manifest_path, manifest_sha256):
        self.root, self.manifest_path, self.manifest_sha256 = Path(runtime_root), Path(manifest_path), manifest_sha256
        self.module = self.capsule = None
        self.pointer = self.destructor = None

    def _manifest(self):
        with open(self.manifest_path, 'rb') as stream:
            raw = stream.read(8 * 1024 * 1024 + 1)
        _check(len(raw) <= 8 * 1024 * 1024 and hmac.compare_digest(_sha(raw), self.manifest_sha256))
        document = json.loads(raw.decode('utf-8', 'strict'), object_pairs_hook=_unique_pairs)
        _check(type(document) is dict and type(document.get('files')) is list)
        return document['files']

    def needs_faiss(self):
        paths = [item.get('path') for item in self._manifest() if type(item) is dict]
        package = paths.count('site/faiss/__init__.py')
        native = sum(paths.count('site/faiss/' + name) for name in self._IMAGES)
        _check((package == 0 and native == 0) or (package == 1 and native > 0))
        return package == 1

    def accept(self, module):
        _check(type(module) is type(sys) and sys.modules.get(self._NAME) is module)
        attrs = type(sys).__getattribute__(module, '__dict__')
        _check(set(attrs) == {'__name__', '__doc__', '__package__', '__loader__', '__spec__',
                              'type_pointer_capsule'} and attrs['__name__'] == self._NAME
               and all(attrs[key] is None for key in ('__doc__', '__package__', '__loader__', '__spec__')))
        capsule = attrs['type_pointer_capsule']
        valid = C.pythonapi.PyCapsule_IsValid
        valid.argtypes = [C.py_object, C.c_char_p]; valid.restype = C.c_int
        _check(valid(capsule, self._CAPSULE) == 1)
        get_pointer = C.pythonapi.PyCapsule_GetPointer
        get_pointer.argtypes = [C.py_object, C.c_char_p]; get_pointer.restype = C.c_void_p
        address = get_pointer(capsule, self._CAPSULE)
        get_context = C.pythonapi.PyCapsule_GetContext
        get_context.argtypes = [C.py_object]; get_context.restype = C.c_void_p
        get_destructor = C.pythonapi.PyCapsule_GetDestructor
        get_destructor.argtypes = [C.py_object]; get_destructor.restype = C.c_void_p
        destructor = get_destructor(capsule)
        _check(bool(address) and bool(destructor) and get_context(capsule) is None)
        kernel = C.WinDLL('kernel32', use_last_error=True)
        kernel.GetModuleHandleExW.argtypes = [W.DWORD, C.c_void_p, C.POINTER(C.c_void_p)]
        kernel.GetModuleHandleExW.restype = W.BOOL
        kernel.GetModuleFileNameW.argtypes = [C.c_void_p, W.LPWSTR, W.DWORD]
        kernel.GetModuleFileNameW.restype = W.DWORD
        handle = C.c_void_p()
        _check(bool(kernel.GetModuleHandleExW(0x4 | 0x2, C.c_void_p(address), C.byref(handle))))
        destructor_handle = C.c_void_p()
        _check(bool(kernel.GetModuleHandleExW(0x4 | 0x2, C.c_void_p(destructor), C.byref(destructor_handle)))
               and destructor_handle.value == handle.value)
        buffer = C.create_unicode_buffer(32768)
        count = kernel.GetModuleFileNameW(handle, buffer, len(buffer))
        _check(0 < count < len(buffer))
        image = Path(buffer.value)
        _check(image.name.lower() in self._IMAGES and image.parent.name.lower() == 'faiss'
               and os.path.samefile(image.parent, self.root / 'site' / 'faiss'))
        native = sys.modules.get('faiss.' + image.stem)
        _check(type(native) is type(sys))
        native_file = type(sys).__getattribute__(native, '__dict__').get('__file__')
        _check(type(native_file) is str and os.path.samefile(image, native_file))
        relative = 'site/faiss/' + image.name
        entries = [item for item in self._manifest() if type(item) is dict and item.get('path') == relative]
        _check(len(entries) == 1 and type(entries[0].get('size')) is int and 0 < entries[0]['size'] <= 128 * 1024 * 1024)
        with open(image, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            data = stream.read(entries[0]['size'] + 1)
            after = os.fstat(stream.fileno())
        _check(os.path.samestat(opened, after) and os.path.samestat(after, image.stat())
               and len(data) == entries[0]['size'] and hmac.compare_digest(_sha(data), entries[0]['sha256']))
        if self.module is None:
            self.module, self.capsule = module, capsule
            self.pointer, self.destructor = address, destructor
        else:
            _check(self.module is module and self.capsule is capsule
                   and self.pointer == address and self.destructor == destructor)

    def recheck(self):
        if self.module is not None:
            self.accept(self.module)


def _reject_preloaded_faiss():
    _check(_FaissSwigOrigin._NAME not in sys.modules and not any(
        name == 'faiss' or name.startswith('faiss.') for name in sys.modules))


def _reject_preloaded_runtime():
    _check(not any(name.startswith('_cython_') or any(name == deny or name.startswith(deny + '.')
               for deny in _PRELOAD_DENY) for name in sys.modules))


def _module_origins(before, roots, namespace_root=None, created_origin=None, swig_origin=None):
    """Check newly imported origins, including only the three known code-root namespaces.

    Namespace inspection is not an authenticity proof; the runtime tree and
    module state can still change after verification (same-user TOCTOU).
    """
    from importlib.machinery import BuiltinImporter, FrozenImporter, ModuleSpec, NamespaceLoader

    namespaces = {"sandbox": ("sandbox",), "sandbox.mind": ("sandbox", "mind"),
                  "sandbox.mind.step2": ("sandbox", "mind", "step2")}
    excluded = os.path.normcase(os.path.realpath(Path(sys.base_prefix) / "Lib" / "site-packages"))
    for name, module in list(sys.modules.items()):
        if name in before or module is None:
            continue
        # A module-level __getattr__ is executable code. Never invoke it while
        # deciding whether this very module is trustworthy.
        _check(isinstance(module, type(sys)))
        attrs = type(sys).__getattribute__(module, "__dict__")
        origin = attrs.get("__file__")
        spec = attrs.get("__spec__")
        _check(spec is None or type(spec) is ModuleSpec)
        if name in namespaces:
            # These names are namespace packages in the verified code tree;
            # a same-named regular module from another trusted root is not one.
            _check(origin is None)
        if origin is None:
            if name == _FaissSwigOrigin._NAME:
                _check(swig_origin is not None)
                swig_origin.accept(module)
                continue
            if name in namespaces:
                _check(namespace_root is not None)
                root = os.path.normcase(os.path.abspath(namespace_root))
                _check(root == os.path.normcase(os.path.realpath(root)) and
                       any(root == os.path.normcase(os.path.abspath(item)) for item in roots))
                expected = os.path.join(root, *namespaces[name])
                paths = getattr(spec, "submodule_search_locations", None)
                _check(getattr(spec, "name", None) == name and getattr(spec, "origin", None) is None and
                       isinstance(getattr(spec, "loader", None), NamespaceLoader) and
                       attrs.get("__loader__") is spec.loader and paths is not None and
                       attrs.get("__path__") is paths)
                locations = list(paths)
                _check(len(locations) == 1 and type(locations[0]) is str and os.path.isabs(locations[0]))
                path = os.path.normcase(os.path.abspath(locations[0]))
                _check(path == expected and _under(path, root) and os.path.isdir(path) and
                       os.path.normcase(os.path.realpath(path)) == expected and
                       not os.path.exists(os.path.join(path, "__init__.py")))
                continue
            if spec is not None and spec.origin is None and type(spec.loader) is NamespaceLoader:
                _check(created_origin is not None and created_origin.accept_namespace(name, module))
                continue
            if name in sys.builtin_module_names or getattr(spec, "origin", None) in ("built-in", "frozen"):
                # A ModuleSpec is mutable and its origin is merely a claim.
                # Bind it to the matching interpreter importer and registry;
                # this is cooperative consistency, not object authentication.
                _check(spec is not None and spec.name == name and attrs.get("__loader__") is spec.loader)
                if spec.origin == "built-in":
                    _check(name in sys.builtin_module_names and spec.loader is BuiltinImporter
                           and BuiltinImporter.find_spec(name) is not None)
                elif spec.origin == "frozen":
                    _check(spec.loader is FrozenImporter and FrozenImporter.find_spec(name) is not None)
                else:
                    _check(False)
                continue
            _check(created_origin is not None)
            created_origin.accept_created(name, module)
            continue
        _check(type(origin) is str)
        path = os.path.normcase(os.path.realpath(origin))
        _check(any(_under(path, root) for root in roots) and not _under(path, excluded))


def _under(path, root):
    try:
        return os.path.commonpath((path, root)) == root
    except ValueError:  # different drives
        return False


def _fixed_public_validate(plan_bytes, bundle_bytes, query_bytes, output):
    """Parent side: closed child answer, then re-resolution against the pinned projection in the bundle.

    The pointer is bound to the plan pin; memory and projection are bound by
    the existing resolver through the pointer's memory digest. Before that the
    parent runs the snapshot module's own public preflight over its own bytes:
    source pin (pointer anchor), encoder pin (descriptor), pinned public policy
    bytes, and the public boundary of every projected record. Every claimed
    (ref, doc id) pair must resolve and the child's text must equal the
    projection text exactly; the returned text always comes from the parent's
    own resolution, never from the child. A child cannot echo the query or add
    a pair. Freshness and source authentication remain UNKNOWN.
    """
    try:
        plan = _fixed_plan(plan_bytes)
        _check(plan["backend"] == "public-snapshot-v1" and type(output) is bytes and 0 < len(output) <= _MAX_OUTPUT)
        _check(type(bundle_bytes) is bytes and hmac.compare_digest(_sha(bundle_bytes), plan["bundle_sha256"]))
        answer = json.loads(output.decode("ascii", "strict"), object_pairs_hook=_unique_pairs,
                            parse_constant=lambda _: _check(False))
        _check(type(answer) is dict and set(answer) == _ANSWER_KEYS and _canonical(answer) == output)
        _check(answer["schema"] == "wd.q4-fixed-public-answer.v1" and answer["backend"] == plan["backend"]
               and answer["source_authentication"] == "UNKNOWN" and answer["freshness"] == "UNKNOWN"
               and answer["runtime"] == _PUBLIC_RUNTIME and answer["plan_sha256"] == _sha(plan_bytes)
               and answer["bundle_sha256"] == plan["bundle_sha256"])
        status = answer["status"]
        claimed = _fixed_closed_results(answer["results"])
        _check(status in ("ANSWER", "ABSTAIN", "DEGRADED") and (status != "ANSWER" or claimed)
               and (status != "ABSTAIN" or not claimed)
               and len({(item["ref_sha256"], item["doc_id_sha256"]) for item in claimed}) == len(claimed))
        query = json.loads(query_bytes.decode("utf-8", "strict"), object_pairs_hook=_unique_pairs)
        _check(type(query) is dict and set(query) == {"situation", "time"} and type(query["time"]) is str)
        parts = _fixed_bundle_decode(bundle_bytes)
        from sandbox.mind.step2 import shared_capture_adapter as adapter
        from sandbox.mind.step2 import shared_index_preflight as checkpoint
        from sandbox.mind.step2 import shared_result_resolver as resolver
        tasks, roster = adapter.strict_pins(parts["policy_bytes"], plan["policy_sha256"], parts["roster_bytes"],
                                            plan["roster_sha256"])
        _check(tasks == frozenset(plan["approved_tasks"]))
        pointer = checkpoint._parse_pointer_snapshot(
            parts["pointer_bytes"], expected_pointer_sha256=plan["expected_pointer_sha256"],
            policy_sha=plan["policy_sha256"], roster_sha=plan["roster_sha256"], prefix_required=True)
        active = checkpoint._bind_active_artifact_bytes(parts["active_artifact_bytes"],
                                                        expected_hashes=pointer["artifacts"], enabled=True)
        # The same public preflight the child's query runs, now in the parent over the parent's own bytes:
        # pointer anchor == source pin, descriptor encoder == encoder pin, public policy bytes == policy pin,
        # and EVERY projected record (so every returned one) inside public_tasks/public_types/public agents.
        checkpoint._preflight_public_snapshot(
            pointer, active, tasks, roster, public_policy_bytes=parts["public_policy_bytes"],
            public_policy_sha256=plan["expected_public_policy_sha256"],
            expected_source_sha256=plan["expected_source_sha256"],
            expected_encoder_sha256=plan["expected_encoder_sha256"])
        resolved = resolver.resolve_results(
            memory_manifest=active.read(checkpoint.ARTIFACTS["memory_sha256"]),
            expected_memory_sha256=pointer["artifacts"]["memory_sha256"],
            projection_bytes=active.read(checkpoint.ARTIFACTS["projection_sha256"]),
            results=[{key: item[key] for key in ("ref_sha256", "doc_id_sha256", "ref_kind", "canonical_event")}
                     for item in claimed],
            query_utc=query["time"], approved_tasks=tasks,
            policy_bytes=parts["policy_bytes"], policy_sha256=plan["policy_sha256"],
            roster_bytes=parts["roster_bytes"], roster_sha256=plan["roster_sha256"])
        _check(type(resolved) is list and len(resolved) == len(claimed)
               and all(mine == theirs for mine, theirs in zip(resolved, claimed)))
        return _canonical({"schema": "wd.q4-fixed-public-result.v1", "status": status, "results": resolved,
                           "source_authentication": "UNKNOWN", "freshness": "UNKNOWN", "runtime": _PUBLIC_RUNTIME,
                           "validation": "PARENT_RERESOLVED_AGAINST_PINNED_PROJECTION"})
    except Exception:
        pass
    raise _Refused() from None


def _fixed_child_backend(plan, peer_path):
    """Child phase 1 backend preparation, run BEFORE READY; returns the answer function.

    public-snapshot-v1: requires -I -S -B and no preloaded sandbox package; reads
    the verifier beside this repo's tools directory ONCE, checks it against the
    plan pin and executes those exact bytes; runs verify_runtime on the plan's
    root and manifest (pin, generation, revision); only then puts the verified
    assets/code first and site last on sys.path and imports the snapshot module,
    which must resolve inside the verified tree. Limits: files can still change
    between verification and import (same-user TOCTOU); native DLL search and
    the interpreter's own stdlib/DLLs are not verified; READY from this child
    is self-reported consistency, never independent authentication.
    """
    if plan["backend"] == "synthetic-v1":
        return _fixed_child_answer
    if plan["backend"] == "staged-card-wrapper-v1":
        _check(type(peer_path) is str)
        scope = {'__name__':'_wd_staged_fixed_backend'}
        exec(compile(_read_staged_backend(peer_path, plan), '<wd-staged-fixed-backend>', 'exec'), scope)
        staged = scope['prepare_staged_backend'](plan, peer_path, globals(), enabled=True)
        _check(callable(staged) and callable(getattr(staged, 'validate_bundle', None)))
        return staged
    _check(plan["backend"] == "public-snapshot-v1" and type(peer_path) is str)
    # First step, before any verifier, runtime or backend code runs in this process.
    _contain_child_output()
    _check(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode)
    _reject_preloaded_runtime()
    _reject_preloaded_faiss()
    imports = _install_controlled_imports(plan['runtime_root'], plan['runtime_manifest_path'],
                                          plan['runtime_manifest_sha256'])
    verifier = Path(peer_path).absolute().parents[1].joinpath(*_VERIFIER_PARTS)
    with open(verifier, "rb") as stream:
        source = stream.read(_VERIFIER_LIMIT + 1)
    _check(len(source) <= _VERIFIER_LIMIT and hmac.compare_digest(_sha(source), plan["verifier_sha256"]))
    scope = {"__name__": "_wd_fixed_runtime_manifest"}
    exec(compile(source, "<wd-runtime-verifier>", "exec"), scope)
    root = Path(plan["runtime_root"])
    scope["verify_runtime"](root, Path(plan["runtime_manifest_path"]), plan["runtime_manifest_sha256"],
                            expected_generation=plan["runtime_generation"], expected_revision=plan["runtime_revision"])
    code, site, model = root / "assets" / "code", root / "site", root / "model"
    entry = code.joinpath("sandbox", "mind", "step2", "shared_index_checkpoint.py")
    _check(code.is_dir() and site.is_dir() and model.is_dir() and entry.is_file())
    roots = [os.path.normcase(os.path.realpath(path)) for path in (code, site)] + _interpreter_roots()
    swig_origin = _FaissSwigOrigin(root, plan['runtime_manifest_path'], plan['runtime_manifest_sha256'])
    before = set(sys.modules)
    sys.path.insert(0, str(code))
    sys.path.append(str(site))
    import importlib
    if swig_origin.needs_faiss():
        importlib.import_module('faiss')
        _module_origins(before, roots, code, imports, swig_origin)
        _check(swig_origin.module is not None)
    module = importlib.import_module("sandbox.mind.step2.shared_index_checkpoint")
    _check(os.path.normcase(os.path.abspath(module.__file__)) == os.path.normcase(os.path.abspath(entry)))
    _module_origins(before, roots, code, imports, swig_origin)
    query_public_snapshot = module.query_public_snapshot
    _check(callable(query_public_snapshot))

    def answer(plan_, plan_bytes, bundle_bytes, query):
        _check(plan_ is plan and type(query) is bytes and 0 < len(query) <= _MAX_INPUT)
        imports.check()
        result = query_public_snapshot(**_fixed_bundle_decode(bundle_bytes),
                                       **{name: plan[name] for name in _PUBLIC_PINS},
                                       approved_tasks=frozenset(plan["approved_tasks"]), query_bytes=query,
                                       model_dir=str(model), expected_runtime_revision=plan["runtime_revision"],
                                       enabled=True)
        # The real query imports lazily, so origins are checked again after the call.
        _module_origins(before, roots, code, imports, swig_origin)
        swig_origin.recheck()
        imports.check()
        _check(type(result) is dict and result.get("status") in ("ANSWER", "ABSTAIN", "DEGRADED"))
        return _canonical({"schema": "wd.q4-fixed-public-answer.v1", "status": result["status"],
                           "results": _fixed_closed_results(result.get("results")), "plan_sha256": _sha(plan_bytes),
                           "bundle_sha256": _sha(bundle_bytes), "backend": plan["backend"],
                           "source_authentication": "UNKNOWN", "freshness": "UNKNOWN", "runtime": _PUBLIC_RUNTIME})
    return answer


def _fixed_child_protocol(read_frame, write_frame, peer_code_sha256, peer_path=None,
                          _diagnostic_marker=None, _diagnostic_fault=False):
    """Child order: plan, backend preparation, challenge, READY; bundle (read limit = plan length), ACK; query.

    No bundle byte is read before READY is written, and the query is not read
    before the bundle ACK. For public-snapshot-v1 the runtime is verified and
    imported before READY. READY and ACK are derived from verified bytes but are
    consistency only, not runtime or peer authentication. The answer is returned
    bound (magic, MAC, inner) to the challenge nonce and the exact query bytes read.
    """
    plan_bytes = read_frame(_MAX_PLAN)
    plan = _fixed_child_plan(plan_bytes, peer_code_sha256)
    backend = None
    try:
        backend = _fixed_child_backend(plan, peer_path)
    except Exception:
        pass
    if backend is None:
        # Fixed refusal: no verifier, runtime or import detail leaves the child.
        raise _Refused() from None
    challenge = json.loads(read_frame(4096).decode("ascii", "strict"), object_pairs_hook=_unique_pairs)
    _check(type(challenge) is dict and set(challenge) == {"schema", "nonce"}
           and challenge["schema"] == "wd.q4-fixed-challenge.v1" and type(challenge["nonce"]) is str
           and len(challenge["nonce"]) == 32 and all(c in "0123456789abcdef" for c in challenge["nonce"]))
    # Derived from the verified plan bytes; the parent never sends its expected digest.
    write_frame(_canonical({"schema": "wd.q4-fixed-ready.v1", "status": "READY", "nonce": challenge["nonce"],
                            "binding_sha256": _sha(plan_bytes)}))
    bundle_bytes = read_frame(plan["bundle_bytes"])
    _fixed_child_bundle(plan, bundle_bytes)
    if plan["backend"] == "staged-card-wrapper-v1":
        backend.validate_bundle(bundle_bytes)
    write_frame(_fixed_ack(challenge["nonce"], _sha(bundle_bytes)))
    if _diagnostic_marker is not None:
        # Mark before the read: a partial/failed query read must never reveal
        # a more specific phase than after_query.
        _diagnostic_marker[0] = True
    query = read_frame(_MAX_INPUT)
    if _diagnostic_fault:
        _check(plan["backend"] == "synthetic-v1")
        raise _Refused()
    return _fixed_wrap(challenge["nonce"], query, backend(plan, plan_bytes, bundle_bytes, query))


def _fixed_child_answer(plan, plan_bytes, bundle_bytes, query):
    """Synthetic backend: a closed answer over verified bytes; carries no query-derived value.

    Situations can be short and guessable, so a digest, length or prefix of the query would let a
    reader confirm guesses. The answer is identical for every accepted query; only the constant
    query RECEIVED says that a bounded query arrived after READY.
    """
    _check(plan["backend"] == "synthetic-v1" and type(query) is bytes and 0 < len(query) <= _MAX_INPUT)
    return _canonical({"schema": "wd.q4-fixed-synthetic-answer.v2", "status": "SYNTHETIC",
                       "plan_sha256": _sha(plan_bytes), "bundle_sha256": _sha(bundle_bytes),
                       "query": "RECEIVED", "backend": plan["backend"],
                       "source_authentication": "UNKNOWN", "runtime": "NOT_VERIFIED"})


_BOOTSTRAP = """
import hashlib,sys
try:
 with open(sys.argv[1], 'rb') as source:
  raw=source.read(262145)
 if len(raw)>262144 or hashlib.sha256(raw).hexdigest()!=sys.argv[2]:
  raise ValueError()
 scope={'__name__':'_wd_secure_child','__file__':sys.argv[1]}
 exec(compile(raw,'<wd-secure-child>','exec'),scope)
 scope['_client_main'](*sys.argv[3:])
except BaseException:
 sys.exit(2)
"""
# Same verified-source bootstrap; fixed entrypoint receives the digest it verified.
_FIXED_BOOTSTRAP = _BOOTSTRAP.replace("scope['_client_main'](*sys.argv[3:])",
                                      "scope['_fixed_child_main'](sys.argv[2],*sys.argv[3:])")
_check(_FIXED_BOOTSTRAP != _BOOTSTRAP)
_DIAGNOSTIC_BOOTSTRAP = _BOOTSTRAP.replace("scope['_client_main'](*sys.argv[3:])",
    "scope['_fixed_diagnostic_main'](sys.argv[2],sys.argv[3],*sys.argv[4:])")
_check(_DIAGNOSTIC_BOOTSTRAP != _BOOTSTRAP and _DIAGNOSTIC_BOOTSTRAP != _FIXED_BOOTSTRAP)


def _validate(stdin_bytes, timeout_seconds, max_output_bytes):
    try:
        _check(type(stdin_bytes) is bytes and len(stdin_bytes) <= _MAX_INPUT)
        _check(type(timeout_seconds) in (int, float) and math.isfinite(timeout_seconds)
               and 0 < timeout_seconds <= 1800)
        _check(type(max_output_bytes) is int and 0 < max_output_bytes <= _MAX_OUTPUT)
    finally:
        del stdin_bytes


def _run(command, cwd, metadata, stdin_bytes, timeout_seconds, max_output_bytes, fixed=None, *, transport_owner=None):
    try:
        # The optional private ticket is a cooperative contribution, never a result
        # dictionary or a claim that the rest of the caller chain is cleaned up.
        if transport_owner is not None:
            from tools.wd_q4_guard_completion import _TransportOwner
            if type(transport_owner) is not _TransportOwner:
                del metadata, stdin_bytes, fixed
                raise _Refused()
            try:
                transport_owner._begin()
            except BaseException:
                del metadata, stdin_bytes, fixed
                raise
        # Reject non-dicts without invoking arbitrary caller-supplied mapping code.
        # The READY primitive is reachable only through run_fixed_peer (mode
        # "fixed"), after every caller pin was checked and both leases are held.
        if not (type(metadata) is dict and all(type(key) is str for key in metadata)
                and type(metadata.get("mode")) is str
                and metadata["mode"] in ("python-c", "echo", "fixed")
                and (metadata["mode"] == "fixed") == (fixed is not None)
                and (fixed is None or (type(fixed) is tuple and len(fixed) == 3))):
            if transport_owner is not None:
                transport_owner._unknown()
            del metadata, stdin_bytes, fixed
            raise _Refused()
        try:
            _validate(stdin_bytes, timeout_seconds, max_output_bytes)
            # Allocate before acquiring the dispatch lock.
            child_info = _ProcessInfo()
            if not _RUN_LOCK.acquire(blocking=False):
                raise _Refused()
        except BaseException:
            if transport_owner is not None:
                transport_owner._unknown()
            del metadata, stdin_bytes, fixed
            raise
        if _QUARANTINE:
            try:
                _RUN_LOCK.release()
                if transport_owner is not None:
                    transport_owner._unknown()
            finally:
                del metadata, stdin_bytes, fixed
            raise TransportCleanupUnknown("prior_unknown") from None
        k = a = None
        pipe = job = process = thread = None
        spawn_attempted = False
        unexpected_exception = False
        unmanaged_exception = False
        scrub_failed = False
        deferred_interrupt = None
        deferred_exit_code = None
        raw_meta = None
        try:
            deadline = time.monotonic() + timeout_seconds
        except BaseException:
            # No OS resources have been acquired; release the dispatch lock while
            # preserving the original failure or interruption.
            try:
                _RUN_LOCK.release()
                if transport_owner is not None:
                    transport_owner._unknown()
            finally:
                del metadata, stdin_bytes, fixed, raw_meta
            raise
        try:
            k, a = _api()
            _check(type(cwd) is str and os.path.isabs(cwd) and os.path.isdir(cwd))
            metadata = dict(metadata, max_output_bytes=max_output_bytes)
            raw_meta = json.dumps(metadata, separators=(",", ":"), ensure_ascii=True).encode("ascii")
            _check(len(raw_meta) <= _MAX_META)
            name = r"\\.\pipe\wd-q4-synthetic-" + uuid.uuid4().hex
            descriptor = _descriptor(k, a)
            try:
                security = _SA(C.sizeof(_SA), descriptor, False)
                pipe = _handle(k.CreateNamedPipeW(
                    name, 3 | 0x40000000 | 0x00080000, 8, 1, 65536, 65536, 0,
                    C.byref(security)))
            finally:
                prior = _prior_outcome(sys.exc_info()[1])
                if not _release_once(k, descriptor, local=True):
                    raise TransportCleanupUnknown(prior) from None
            _noninheritable(k, pipe)
            job = _handle(k.CreateJobObjectW(None, None))
            _noninheritable(k, job)
            limits = _Limits()
            limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE, no breakaway flags.
            _check(k.SetInformationJobObject(job, 9, C.byref(limits), C.sizeof(limits)))
            parent_created = _created(k, k.GetCurrentProcess())
            argv = command + [name, str(os.getpid()), str(parent_created)]
            _check(all(type(value) is str and "\0" not in value for value in argv))
            line = subprocess.list2cmdline(argv)
            _check(len(line) < 32767)
            _remaining(deadline)
            environment = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR")
                           if key in os.environ}
            environment.update(TEMP=cwd, TMP=cwd)  # Caller-owned, budgeted synthetic scratch.
            # No STARTF_USESTDHANDLES, HANDLE_LIST, anonymous pipes or inheritable gap.
            spawn_attempted = True
            process, thread, pid, _ = _create_process_in_job(
                k, job, command[0], line, environment, cwd, child_info)
            _noninheritable(k, int(process))
            _noninheritable(k, int(thread))
            child_created = _created(k, int(process))
            _check(k.ResumeThread(int(thread)) != 0xFFFFFFFF)
            _io(k, pipe, None, 0, deadline, connect=True)
            _peer(k, pipe, pid, child_created, client=True)
            if fixed is not None:
                _fixed_exchange(k, pipe, pid, child_created, fixed[0], fixed[1], fixed[2], stdin_bytes, deadline)
            else:
                _frame_write(k, pipe, raw_meta, deadline)
                _frame_write(k, pipe, stdin_bytes, deadline)
            status = _read(k, pipe, 1, deadline)
            if status != b"\1":
                raise _Refused()
            # Fixed mode: the caller's cap bounds the inner answer; the frame carries the binding overhead only.
            output = _frame_read(k, pipe, max_output_bytes + (_BIND_OVERHEAD if fixed is not None else 0), deadline)
            exit_code = struct.unpack(">i", _read(k, pipe, 4, deadline))[0]
            _check(_read(k, pipe, 4, deadline) == b"END!")
            _check(_io(k, pipe, None, 1, deadline, eof=True) == b"")
            state = k.WaitForSingleObject(int(process), _remaining(deadline))
            if state == 258:
                raise TimeoutError("secure_transport_timeout")
            _check(state == 0)
            actual_exit = W.DWORD()
            _check(k.GetExitCodeProcess(int(process), C.byref(actual_exit)))
            _check(actual_exit.value == 0)
            return output, exit_code
        except TransportCleanupUnknown as exc:
            if _clear_exception_frames(exc):
                raise
            scrub_failed = True
        except TimeoutError as exc:
            if _clear_exception_frames(exc):
                raise TimeoutError("secure_transport_timeout") from None
            scrub_failed = True
        except (KeyboardInterrupt, SystemExit) as exc:
            unmanaged_exception = True
            if transport_owner is not None:
                transport_owner._unknown()
            if _clear_exception_frames(exc):
                raise
            deferred_interrupt = "interrupt" if isinstance(exc, KeyboardInterrupt) else "exit"
            if deferred_interrupt == "exit":
                deferred_exit_code = exc.code if exc.code is None or type(exc.code) is int else 1
            scrub_failed = True
        except Exception as exc:
            unexpected_exception = not isinstance(exc, _Refused)
            if _clear_exception_frames(exc):
                raise _Refused() from None
            scrub_failed = True
        except BaseException as exc:
            # Groups containing interrupts and all other unmanaged base errors
            # must never register successful owner cleanup.
            unmanaged_exception = True
            if transport_owner is not None:
                transport_owner._unknown()
            if _clear_exception_frames(exc):
                raise
            if isinstance(exc, BaseExceptionGroup):
                # Its unseen members cannot be classified within the budget.
                # Preserve a base-exception group signal without inventing a
                # particular interrupt or retaining the original graph.
                deferred_interrupt = "base_group"
            scrub_failed = True
        finally:
            # Cover the cleanup prologue as well as its operations. Once any
            # native handle was acquired, an unexpected interruption leaves
            # the outcome uncertain and must block a later call.
            try:
                prior_cleanup_unknown = isinstance(sys.exc_info()[1], TransportCleanupUnknown)
                prior_outcome = _prior_outcome(sys.exc_info()[1])
                # A suspended child may exist even when an exception prevented one
                # or both returned handles from reaching the local variables. We
                # cannot recover or terminate an unreferenced child/handle here.
                # Conservatively block subsequent work; this also treats ordinary
                # CreateProcess errors as uncertain rather than claiming cleanup.
                cleanup_ok = not (spawn_attempted and (process is None or thread is None))
                # The native call writes to caller-owned memory even if Python
                # cannot unpack the returned values after the call.
                if process is None:
                    process = child_info.process
                if thread is None:
                    thread = child_info.thread
                failed_closes = []
                def cleanup_call(operation):
                    nonlocal prior_outcome
                    try:
                        return bool(operation())
                    except BaseException as exc:
                        reason = _prior_outcome(exc)
                        if prior_outcome not in ("interrupt", "exit") and reason in ("interrupt", "exit"):
                            prior_outcome = reason
                        return False
                def close_call(handle, *, convert=False):
                    # Conversion can fail or be interrupted too; keep it inside the
                    # same boundary as CloseHandle so later handles are still tried.
                    closed = cleanup_call(lambda: k.CloseHandle(int(handle) if convert else handle))
                    if not closed:
                        failed_closes.append(handle)
                    return closed
                try:
                    if k is not None:
                        if spawn_attempted and job is not None:
                            cleanup_deadline = None
                            try:
                                cleanup_deadline = time.monotonic() + _CLEANUP_WAIT_SECONDS
                            except BaseException as exc:
                                # Still request termination and close owned handles. Without
                                # a clock bound there is no safe exit/zero wait; quarantine.
                                reason = _prior_outcome(exc)
                                if prior_outcome not in ("interrupt", "exit") and reason in ("interrupt", "exit"):
                                    prior_outcome = reason
                                cleanup_ok = False
                            try:
                                cleanup_ok = cleanup_call(lambda: k.TerminateJobObject(job, 124)) and cleanup_ok
                                if cleanup_deadline is not None:
                                    cleanup_ok = cleanup_call(lambda: _wait_job_empty(k, job, cleanup_deadline)) and cleanup_ok
                            except BaseException as exc:
                                # A failure in a decision outside the individual kernel
                                # calls cannot certify cleanup. Still try termination and
                                # all owned closes; do not claim the retry confirmed it.
                                reason = _prior_outcome(exc)
                                if prior_outcome not in ("interrupt", "exit") and reason in ("interrupt", "exit"):
                                    prior_outcome = reason
                                cleanup_ok = False
                                cleanup_call(lambda: k.TerminateJobObject(job, 124))
                                if cleanup_deadline is not None:
                                    cleanup_call(lambda: _wait_job_empty(k, job, cleanup_deadline))
                            # A job reporting zero active processes can precede
                            # its child process handle becoming signaled. Both
                            # observations are required for confirmed cleanup.
                            if process is not None:
                                if cleanup_deadline is None:
                                    cleanup_ok = False
                                else:
                                    cleanup_ok = cleanup_call(
                                        lambda: _wait_child_exit(k, process, cleanup_deadline)) and cleanup_ok
                        if job is not None:
                            cleanup_ok = close_call(job) and cleanup_ok
                        if process is not None:
                            cleanup_ok = close_call(process, convert=True) and cleanup_ok
                        if thread is not None:
                            cleanup_ok = close_call(thread, convert=True) and cleanup_ok
                        try:
                            if pipe is not None and not any(entry[1] == pipe for entry in _QUARANTINE):
                                cleanup_ok = close_call(pipe) and cleanup_ok
                            elif pipe is not None:
                                cleanup_ok = False  # Pending overlapped I/O remains quarantined.
                        except BaseException as exc:
                            # When pending-I/O ownership cannot be checked, retaining
                            # the handle is safer than closing a possibly active pipe.
                            reason = _prior_outcome(exc)
                            if prior_outcome not in ("interrupt", "exit") and reason in ("interrupt", "exit"):
                                prior_outcome = reason
                            cleanup_ok = False
                            if pipe is not None:
                                failed_closes.append(pipe)
                    if not cleanup_ok and (failed_closes or not _QUARANTINE):
                        # Record only uncertain closes, never successfully closed values.
                        _QUARANTINE.append((k, None, tuple(failed_closes)))
                except BaseException as exc:
                    # An interruption between guarded cleanup operations leaves their
                    # progress uncertain. Retain all handle objects and fail closed;
                    # do not retry a handle that may already have been closed/reused.
                    # Without acquired resources, keep the preflight failure unchanged.
                    if not any(value is not None for value in (pipe, job, process, thread)):
                        raise
                    reason = _prior_outcome(exc)
                    if prior_outcome not in ("interrupt", "exit") and reason in ("interrupt", "exit"):
                        prior_outcome = reason
                    _QUARANTINE.append((k, pipe, (job, process, thread)))
                    cleanup_ok = False
            except BaseException as exc:
                if transport_owner is not None:
                    transport_owner._unknown()
                if not any(value is not None for value in (pipe, job, process, thread)):
                    raise
                _QUARANTINE.append((k, pipe, (job, process, thread)))
                raise TransportCleanupUnknown(_prior_outcome(exc)) from None
            finally:
                try:
                    _RUN_LOCK.release()
                except BaseException:
                    if transport_owner is not None:
                        transport_owner._unknown()
                    raise
                finally:
                    # These inputs are no longer needed after cleanup; traceback
                    # frames must not keep their references on return or error.
                    del metadata, stdin_bytes, fixed, raw_meta
            if not cleanup_ok:
                if transport_owner is not None:
                    transport_owner._unknown()
                raise TransportCleanupUnknown(prior_outcome) from None
            if transport_owner is not None and unexpected_exception and not scrub_failed:
                transport_owner._unknown()
                raise TransportCleanupUnknown(prior_outcome) from None
            # A no-spawn path, earlier uncertainty or interruption does not count.
            if transport_owner is not None:
                if (spawn_attempted and job is not None and not prior_cleanup_unknown
                        and not unmanaged_exception and not scrub_failed
                        and prior_outcome not in ("interrupt", "exit")):
                    try:
                        transport_owner._complete()
                    except (KeyboardInterrupt, SystemExit):
                        transport_owner._unknown()
                        raise
                    except BaseException:
                        transport_owner._unknown()
                        raise TransportCleanupUnknown(prior_outcome) from None
                else:
                    transport_owner._unknown()
        if scrub_failed:
            # Raise only after leaving the original except block so an oversized
            # exception graph cannot survive as this error's implicit context.
            if deferred_interrupt == "interrupt":
                _raise_context_free(KeyboardInterrupt())
            if deferred_interrupt == "exit":
                _raise_context_free(SystemExit(deferred_exit_code))
            if deferred_interrupt == "base_group":
                _raise_context_free(BaseExceptionGroup("transport_exception_graph_unavailable",
                                                      [BaseException("transport_unmanaged")]))
            _raise_context_free(TransportCleanupUnknown("other"))


    finally:
        if "metadata" in locals():
            del metadata
        if "stdin_bytes" in locals():
            del stdin_bytes
        if "fixed" in locals():
            del fixed
        if "raw_meta" in locals():
            del raw_meta

def run_bounded(*, executable, arguments, cwd, stdin_bytes, timeout_seconds,
                max_output_bytes, expected_interpreter_sha256=None, interpreter_max_bytes=None):
    """Synthetic-only same-interpreter -c; optional caller-pinned file lease.

    The lease checks only the executable file. It does not authenticate the
    interpreter's imports, parent directories, DLLs, runtime, or loaded image.
    Arbitrary asynchronous interruptions around context entry are not proven
    to preserve the lease; this route is not a production identity boundary.
    """
    if expected_interpreter_sha256 is None and interpreter_max_bytes is None:
        return _run_bounded_impl(executable=executable, arguments=arguments, cwd=cwd,
            stdin_bytes=stdin_bytes, timeout_seconds=timeout_seconds, max_output_bytes=max_output_bytes)
    cleanup_type = cleanup_lease = failure = transport_cleanup = None
    try:
        from tools.wd_q4_fixed_parent_attestation import AttestationCleanupUnknown as cleanup_type
        return _run_bounded_impl(executable=executable, arguments=arguments, cwd=cwd,
            stdin_bytes=stdin_bytes, timeout_seconds=timeout_seconds, max_output_bytes=max_output_bytes,
            expected_interpreter_sha256=expected_interpreter_sha256, interpreter_max_bytes=interpreter_max_bytes)
    except BaseException as exc:
        if cleanup_type is not None and isinstance(exc, cleanup_type):
            cleanup_lease = exc.lease
        elif isinstance(exc, TransportCleanupUnknown):
            transport_cleanup = exc.prior_outcome
        elif isinstance(exc, TimeoutError):
            failure = "timeout"
        elif isinstance(exc, KeyboardInterrupt):
            failure = "interrupt"
        elif isinstance(exc, SystemExit):
            failure = "exit"
        else:
            failure = "refused"
    del executable, arguments, cwd, stdin_bytes, timeout_seconds, max_output_bytes
    del expected_interpreter_sha256, interpreter_max_bytes
    if cleanup_lease is not None:
        raise cleanup_type(cleanup_lease) from None
    if transport_cleanup is not None:
        raise TransportCleanupUnknown(transport_cleanup) from None
    if failure == "timeout":
        raise TimeoutError("secure_transport_timeout") from None
    if failure == "interrupt":
        raise KeyboardInterrupt() from None
    if failure == "exit":
        raise SystemExit(1) from None
    raise _Refused() from None


def _run_bounded_impl(*, executable, arguments, cwd, stdin_bytes, timeout_seconds,
                      max_output_bytes, expected_interpreter_sha256=None, interpreter_max_bytes=None):
    _validate(stdin_bytes, timeout_seconds, max_output_bytes)
    _check(type(executable) is str and
           os.path.normcase(os.path.abspath(executable)) ==
           os.path.normcase(os.path.abspath(sys.executable)))
    _check(type(arguments) in (tuple, list) and
           all(type(value) is str for value in arguments))
    args = list(arguments)
    _check(len(args) in (4, 5) and args[-2] == "-c" and
           args[:-2] in (["-I", "-B"], ["-I", "-S", "-B"]))
    _check(len(args[-1].encode("utf-8")) <= 32768)
    _check((expected_interpreter_sha256 is None) == (interpreter_max_bytes is None))
    path = str(Path(__file__).absolute())
    with open(path, "rb") as stream:
        raw = stream.read(_PEER_CODE_LIMIT + 1)
    _check(len(raw) <= _PEER_CODE_LIMIT)
    command = [executable, "-I", "-S", "-B", "-c", _BOOTSTRAP,
               path, hashlib.sha256(raw).hexdigest()]
    if expected_interpreter_sha256 is None:
        return _run(command, cwd, {"mode": "python-c", "code": args[-1]}, stdin_bytes,
                    timeout_seconds, max_output_bytes)
    from tools.wd_q4_fixed_parent_attestation import attest_file
    # The context owns the lease during _run, including its exceptional exit.
    # The outer public wrapper scrubs both run and cleanup exception chains.
    with attest_file(executable, expected_interpreter_sha256, max_bytes=interpreter_max_bytes, enabled=True):
        return _run(command, cwd, {"mode": "python-c", "code": args[-1]}, stdin_bytes,
                    timeout_seconds, max_output_bytes)


def _fixed_owner_pair(guard, context):
    """Validate one anchored guard/context pair; None/None is unguarded legacy."""
    if guard is None and context is None:
        return False
    from tools.wd_q4_guard_completion import _Guard, _OwnerContext
    if type(guard) is not _Guard or type(context) is not _OwnerContext:
        raise _Refused()
    try:
        guard._accept_owner_context(context)
    except Exception:
        raise _Refused() from None
    return True


def run_fixed_peer(*, executable, expected_interpreter_sha256, interpreter_max_bytes, plan_bytes,
                   expected_plan_sha256, bundle_bytes, runtime_manifest_bytes, query_bytes, cwd,
                   timeout_seconds, max_output_bytes, public_snapshot_enabled=False,
                   _owner_guard=None, _owner_context=None):
    """Fixed peer of THIS file over the bounded pipe; synthetic by default.

    Every caller pin is checked before spawning: the plan digest, the plan's
    interpreter and peer-code pins against the caller's interpreter pin and this
    file's bytes, the runtime manifest bytes, and the snapshot bundle length and
    digest. Share-denying leases on the interpreter file and on this file are
    held for the whole run; the bootstrap execs only bytes matching the pin.
    Order: plan and a nonce-only challenge, then a READY whose binding equals
    the parent-computed plan digest before any bundle byte is sent; then the
    bundle, which the child re-hashes against the plan (its read limit is the
    plan length), and a closed bundle ACK naming the parent-computed bundle
    digest before the query is sent. READY and ACK are consistency,
    not authentication: a peer echoing pins from the plan proves nothing.
    For synthetic-v1 the runtime pin is a byte pin on the caller's manifest
    only; the runtime tree (stdlib, DLLs, site, model) is NOT verified.
    public-snapshot-v1 (plan v2) is default-off: refused before spawning unless
    public_snapshot_enabled is True; the parent also checks and leases the
    pinned verifier file. Before READY the child executes that verifier from
    the exact pinned bytes, runs verify_runtime on the plan's root, manifest,
    generation and revision, and only then imports the snapshot module from the
    verified tree; after the ACK and the query it calls query_public_snapshot
    with the decoded bundle and the plan's pins. Limits: verifier/runtime
    TOCTOU between verification and import (same-user writers), native DLL
    search and the interpreter's stdlib/DLLs are NOT verified, no sandbox or
    ACL proof; READY is self-reported and never independent authentication.
    Before any verifier or runtime code the child points its Python, CRT and
    Win32 standard streams at the exact \\Device\\Null object and detaches its
    console (see _contain_child_output for what stays uncontained), refuses
    preloaded sandbox/tools/waggledance/core modules, and checks that every
    module added by the import or the call comes from the verified code/site or
    the interpreter's Lib/DLLs (never its site-packages; those stdlib files are
    not manifested). The parent never returns the child answer as is: it
    checks the closed answer, runs the snapshot module's public preflight over
    its own bytes (source, encoder and public policy pins, every projected
    record inside the public boundary), and re-resolves every pair against the
    projection bound by the plan's pointer pin, taking stored text only from there.
    Runtime files stay mutable after verification (only the interpreter, this
    file and the verifier are leased), and base64 makes the effective raw
    snapshot size about 12 MiB under the 16 MiB bundle cap.
    Query-plus-answer binding (RCO2 design 2026-11-06): after the query the
    child returns magic + HMAC-SHA256 + inner, keyed by the per-call control nonce
    over wd.q4-query-bind.v2, NUL, the length-framed exact query and the
    length-framed exact inner answer; the parent recomputes it over its own
    query bytes and checks it with compare_digest before any use, so an inner
    byte altered after the MAC, a frame for another query or a frame bound
    under another call's nonce is a fixed refusal. The nonce is sent to the
    child in the challenge, so this binds the three byte strings only: it is
    not lane authentication, not freshness and not proof that a model
    used the query; a same-user peer that read the challenge can bind any
    answer. The inner cap stays max_output_bytes; the pipe frame cap grows by
    the 72-byte framing overhead only; diagnostic output is not wrapped. An
    unwrap failure is a plain refusal raised after the child cleanup completed.
    No approved runtime tree or anchored pin exists yet, so in practice the
    real backend is unavailable; only a synthetic runtime exercises this path.
    Source authentication and freshness are UNKNOWN. Same limits as run_bounded.
    """
    cleanup_type = cleanup_lease = failure = transport_cleanup = None
    guarded = abort_failed = False
    exit_code = None
    try:
        guarded = _fixed_owner_pair(_owner_guard, _owner_context)
        from tools.wd_q4_fixed_parent_attestation import AttestationCleanupUnknown as cleanup_type
        if guarded:
            result = _run_fixed_peer_impl(executable, expected_interpreter_sha256, interpreter_max_bytes, plan_bytes,
                                          expected_plan_sha256, bundle_bytes, runtime_manifest_bytes, query_bytes, cwd,
                                          timeout_seconds, max_output_bytes, public_snapshot_enabled,
                                          _owner_guard=_owner_guard, _owner_context=_owner_context)
            try:
                _owner_guard._accept_owner_context(_owner_context)
                _owner_context._require_done(("attestation", "transport"))
            except Exception:
                raise TransportCleanupUnknown("other") from None
            return result
        return _run_fixed_peer_impl(executable, expected_interpreter_sha256, interpreter_max_bytes, plan_bytes,
                                    expected_plan_sha256, bundle_bytes, runtime_manifest_bytes, query_bytes, cwd,
                                    timeout_seconds, max_output_bytes, public_snapshot_enabled)
    except BaseException as exc:
        result = None  # Never retain a rejected guarded result in traceback locals.
        if guarded:
            try:
                _owner_context._abort()
            except BaseException:
                abort_failed = True
                try:
                    _owner_guard.mark_unknown()
                except BaseException:
                    # The unknown outcome is reported below; no cleanup or
                    # successful guard release is inferred from this failure.
                    pass
        if cleanup_type is not None and isinstance(exc, cleanup_type):
            cleanup_lease = exc.lease
        elif isinstance(exc, TransportCleanupUnknown):
            transport_cleanup = exc.prior_outcome
        elif isinstance(exc, TimeoutError):
            failure = "timeout"
        elif isinstance(exc, KeyboardInterrupt):
            failure = "interrupt"
        elif isinstance(exc, SystemExit):
            failure = "exit"
            exit_code = exc.code if exc.code is None or type(exc.code) is int else 1
        elif isinstance(exc, BaseExceptionGroup) and not isinstance(exc, ExceptionGroup) and guarded:
            failure = "base_group"
        else:
            failure = "refused"
    if abort_failed:
        transport_cleanup = "other"
    del executable, expected_interpreter_sha256, interpreter_max_bytes, plan_bytes, expected_plan_sha256
    del bundle_bytes, runtime_manifest_bytes, query_bytes, cwd, timeout_seconds, max_output_bytes
    del public_snapshot_enabled, _owner_guard, _owner_context
    if guarded:
        if cleanup_lease is not None:
            _raise_context_free(cleanup_type(cleanup_lease))
        if transport_cleanup is not None:
            _raise_context_free(TransportCleanupUnknown(transport_cleanup))
        if failure == "timeout":
            _raise_context_free(TimeoutError("secure_transport_timeout"))
        if failure == "interrupt":
            _raise_context_free(KeyboardInterrupt())
        if failure == "exit":
            _raise_context_free(SystemExit(exit_code))
        if failure == "base_group":
            _raise_context_free(BaseExceptionGroup("fixed_peer_exception_graph_unavailable",
                                                  [BaseException("fixed_peer_unmanaged")]))
        _raise_context_free(_Refused())
    if cleanup_lease is not None:
        raise cleanup_type(cleanup_lease) from None
    if transport_cleanup is not None:
        raise TransportCleanupUnknown(transport_cleanup) from None
    if failure == "timeout":
        raise TimeoutError("secure_transport_timeout") from None
    if failure == "interrupt":
        raise KeyboardInterrupt() from None
    if failure == "exit":
        raise SystemExit(1) from None
    raise _Refused() from None


def run_fixed_peer_diagnostic(*, executable, expected_interpreter_sha256, interpreter_max_bytes, plan_bytes,
                              expected_plan_sha256, bundle_bytes, runtime_manifest_bytes, query_bytes, cwd,
                              timeout_seconds, max_output_bytes, enabled=False, public_snapshot_enabled=False,
                              synthetic_force_after_query=False):
    """Opt-in synthetic-only phase probe in a separately pinned real child.

    The synthetic backend does not authenticate caller bytes.
    Caller MUST supply synthetic data and independently pin/authenticate this
    module, interpreter, plan and runtime. The output has no inputs, hashes,
    sizes, paths or exception detail. A successful diagnostic transport proves
    neither child truth nor process-tree cleanup; the latter remains UNKNOWN.
    Pre-query failures expose no diagnostic phase. after_query means the query read was attempted
    or a later step failed; it does not prove query receipt or backend execution.
    This is not a production route.
    """
    if enabled is False:
        return {"status": "OFF"}
    cleanup_type = cleanup_lease = failure = transport_cleanup = None
    try:
        _check(enabled is True and type(synthetic_force_after_query) is bool)
        from tools.wd_q4_fixed_parent_attestation import AttestationCleanupUnknown as cleanup_type
        mode = "synthetic-fault" if synthetic_force_after_query else "observe"
        return _run_fixed_peer_impl(executable, expected_interpreter_sha256, interpreter_max_bytes, plan_bytes,
                                    expected_plan_sha256, bundle_bytes, runtime_manifest_bytes, query_bytes, cwd,
                                    timeout_seconds, max_output_bytes, public_snapshot_enabled, mode)
    except BaseException as exc:
        if cleanup_type is not None and isinstance(exc, cleanup_type):
            cleanup_lease = exc.lease
        elif isinstance(exc, TransportCleanupUnknown):
            transport_cleanup = exc.prior_outcome
        elif isinstance(exc, TimeoutError):
            failure = "timeout"
        elif isinstance(exc, KeyboardInterrupt):
            failure = "interrupt"
        elif isinstance(exc, SystemExit):
            failure = "exit"
        else:
            failure = "refused"
    del executable, expected_interpreter_sha256, interpreter_max_bytes, plan_bytes, expected_plan_sha256
    del bundle_bytes, runtime_manifest_bytes, query_bytes, cwd, timeout_seconds, max_output_bytes
    del enabled, public_snapshot_enabled, synthetic_force_after_query
    if cleanup_lease is not None:
        raise cleanup_type(cleanup_lease) from None
    if transport_cleanup is not None:
        raise TransportCleanupUnknown(transport_cleanup) from None
    if failure == "timeout":
        raise TimeoutError("secure_transport_timeout") from None
    if failure == "interrupt":
        raise KeyboardInterrupt() from None
    if failure == "exit":
        raise SystemExit(1) from None
    raise _Refused() from None


def _run_fixed_peer_impl(executable, expected_interpreter_sha256, interpreter_max_bytes, plan_bytes,
                         expected_plan_sha256, bundle_bytes, runtime_manifest_bytes, query_bytes, cwd,
                         timeout_seconds, max_output_bytes, public_snapshot_enabled=False, _diagnostic_mode=None,
                         *, _owner_guard=None, _owner_context=None):
    guarded = completed = False
    attestation_owner = None
    try:
        guarded = _fixed_owner_pair(_owner_guard, _owner_context)
        _validate(query_bytes, timeout_seconds, max_output_bytes)
        _check(len(query_bytes) > 0 and type(public_snapshot_enabled) is bool)
        plan = _fixed_plan(plan_bytes)
        # The staged schema is parsed separately, but cannot run until its own
        # fixed child and independent parent validation are wired. Never let it
        # fall through to the synthetic backend during incremental development.
        _check(plan["backend"] != "staged-card-wrapper-v1")
        public = plan["backend"] == "public-snapshot-v1"
        _check(public_snapshot_enabled or not public)
        _check(_diagnostic_mode in (None, "observe", "synthetic-fault"))
        _check(_diagnostic_mode is None or plan["backend"] == "synthetic-v1")
        _check(hmac.compare_digest(_sha(plan_bytes), _hex64(expected_plan_sha256)))
        _check(hmac.compare_digest(plan["interpreter_sha256"], _hex64(expected_interpreter_sha256)))
        _check(type(executable) is str and
               os.path.normcase(os.path.abspath(executable)) == os.path.normcase(os.path.abspath(sys.executable)))
        path = str(Path(__file__).absolute())
        with open(path, "rb") as stream:
            raw = stream.read(_PEER_CODE_LIMIT + 1)
        _check(len(raw) <= _PEER_CODE_LIMIT and hmac.compare_digest(_sha(raw), plan["peer_code_sha256"]))
        _check(type(runtime_manifest_bytes) is bytes and len(runtime_manifest_bytes) <= _MAX_RUNTIME_MANIFEST
               and hmac.compare_digest(_sha(runtime_manifest_bytes), plan["runtime_manifest_sha256"]))
        _check(type(bundle_bytes) is bytes and len(bundle_bytes) == plan["bundle_bytes"]
               and hmac.compare_digest(_sha(bundle_bytes), plan["bundle_sha256"]))
        verifier = str(Path(path).parents[1].joinpath(*_VERIFIER_PARTS))
        if public:
            with open(verifier, "rb") as stream:
                source = stream.read(_VERIFIER_LIMIT + 1)
            _check(len(source) <= _VERIFIER_LIMIT and hmac.compare_digest(_sha(source), plan["verifier_sha256"]))
        control = {"schema": "wd.q4-fixed-control.v1", "nonce": os.urandom(16).hex(), "binding_sha256": _sha(plan_bytes)}
        bootstrap = _FIXED_BOOTSTRAP if _diagnostic_mode is None else _DIAGNOSTIC_BOOTSTRAP
        command = [executable, "-I", "-S", "-B", "-c", bootstrap, path, plan["peer_code_sha256"]]
        if _diagnostic_mode is not None:
            command.append(_diagnostic_mode)
        from tools.wd_q4_fixed_parent_attestation import attest_file, _AttestationOwner
        if guarded:
            attestation_ticket = _owner_context._ticket("attestation")
            _owner_context._accept_ticket("attestation", attestation_ticket)
            transport_ticket = _owner_context._ticket("transport")
            _owner_context._accept_ticket("transport", transport_ticket)
            attestation_ticket._begin()
            attestation_owner = _AttestationOwner(on_complete=attestation_ticket._complete,
                                                  on_unknown=attestation_ticket._unknown)
            attest = attestation_owner.attest
        else:
            attest = attest_file
        with attest(executable, expected_interpreter_sha256, max_bytes=interpreter_max_bytes, enabled=True):
            with attest(path, plan["peer_code_sha256"], max_bytes=_PEER_CODE_LIMIT, enabled=True):
                with (attest(verifier, plan["verifier_sha256"], max_bytes=_VERIFIER_LIMIT, enabled=True)
                      if public else contextlib.nullcontext()):
                    if guarded:
                        output, code = _run(command, cwd, {"mode": "fixed"}, query_bytes, timeout_seconds,
                                            max_output_bytes, fixed=(control, plan_bytes, bundle_bytes),
                                            transport_owner=transport_ticket)
                    else:
                        output, code = _run(command, cwd, {"mode": "fixed"}, query_bytes, timeout_seconds,
                                            max_output_bytes, fixed=(control, plan_bytes, bundle_bytes))
        if guarded:
            attestation_owner.finish()
        if _diagnostic_mode is not None:
            _check(code == 0 and output in (b"after_query", b"completed"))
            completed = True
            return {"status": "DIAGNOSTIC", "phase": output.decode("ascii"), "cleanup": "UNKNOWN"}
        # Unwrap after the diagnostic return and after _run's cleanup completed: the inner answer is used only if
        # it is bound to this call's nonce and the parent's own query bytes.
        output = _fixed_unwrap(control["nonce"], query_bytes, output, max_output_bytes)
        if not public:
            completed = True
            return output, code
        # Never return the child's public answer unvalidated: re-resolve it against the pinned bundle projection.
        _check(code == 0)
        result = _fixed_public_validate(plan_bytes, bundle_bytes, query_bytes, output), 0
        completed = True
        return result


    finally:
        try:
            if guarded and not completed:
                try:
                    if attestation_owner is not None:
                        attestation_owner.abort()
                finally:
                    _owner_context._abort()
        finally:
            if "plan_bytes" in locals():
                del plan_bytes
            if "bundle_bytes" in locals():
                del bundle_bytes
            if "runtime_manifest_bytes" in locals():
                del runtime_manifest_bytes
            if "query_bytes" in locals():
                del query_bytes
            if "control" in locals():
                del control
            if "result" in locals():
                del result

def run_synthetic_echo(*, powershell, bootstrap_path, expected_bootstrap_sha256,
                       cwd, stdin_bytes, timeout_seconds, max_output_bytes):
    """Explicit separately pinned PowerShell echo peer; NEVER dispatches a wrapper."""
    _validate(stdin_bytes, timeout_seconds, max_output_bytes)
    _check(type(powershell) is str and os.path.isabs(powershell))
    _check(type(bootstrap_path) is str and os.path.isabs(bootstrap_path))
    _check(type(expected_bootstrap_sha256) is str and
           len(expected_bootstrap_sha256) == 64 and
           all(c in "0123456789abcdef" for c in expected_bootstrap_sha256))
    with open(bootstrap_path, "rb") as stream:
        raw = stream.read(131073)
    _check(len(raw) <= 131072 and
           hashlib.sha256(raw).hexdigest() == expected_bootstrap_sha256)
    # Caller must hold/pin this file; this path-based PS launch has a TOCTOU limit.
    command = [powershell, "-NoProfile", "-NonInteractive", "-File",
               bootstrap_path, "-Enabled"]
    # Positional tail is PipeName, ParentPid, ParentCreated for this fixed peer.
    return _run(command, cwd, {"mode": "echo"}, stdin_bytes, timeout_seconds,
                max_output_bytes)


class _Output(io.BytesIO):
    def __init__(self, budget):
        super().__init__()
        self.budget = budget

    def write(self, value):
        if type(value) is not bytes:
            value = bytes(value)
        self.budget[0] += len(value)
        if self.budget[0] > self.budget[1]:
            self.budget[2] = True
            raise _Refused()
        return super().write(value)


class _Discard(_Output):
    def write(self, value):
        count = super().write(value)
        self.seek(0)
        self.truncate(0)
        return count


def _sync_read(k, pipe, size):
    result = bytearray()
    while len(result) < size:
        buffer = C.create_string_buffer(size - len(result))
        done = W.DWORD()
        _check(k.ReadFile(pipe, buffer, len(buffer), C.byref(done), None))
        _check(0 < done.value <= len(buffer))
        result.extend(buffer.raw[:done.value])
    return bytes(result)


def _sync_write(k, pipe, data):
    offset = 0
    while offset < len(data):
        part = data[offset:]
        done = W.DWORD()
        _check(k.WriteFile(pipe, C.create_string_buffer(part), len(part),
                           C.byref(done), None))
        _check(0 < done.value <= len(part))
        offset += done.value


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        _check(key not in result)
        result[key] = value
    return result


def _client_main(name, parent_pid, parent_created):
    """Private child endpoint; no CLI entrypoint is installed."""
    k, _ = _api()
    prefix = r"\\.\pipe\wd-q4-synthetic-"
    _check(type(name) is str and name.startswith(prefix) and
           len(name) == len(prefix) + 32 and
           all(character in "0123456789abcdef" for character in name[len(prefix):]))
    pid, created = int(parent_pid), int(parent_created)
    _check(pid > 0 and created > 0)
    pipe = _handle(k.CreateFileW(name, 0xC0000000, 0, None, 3, 0, None))
    try:
        _noninheritable(k, pipe)
        _peer(k, pipe, pid, created, client=False)
        length = struct.unpack(">I", _sync_read(k, pipe, 4))[0]
        _check(length <= _MAX_META)
        meta = json.loads(_sync_read(k, pipe, length).decode("utf-8"),
                          object_pairs_hook=_unique_pairs)
        _check(type(meta) is dict and set(meta) == {"mode", "code", "max_output_bytes"}
               and meta["mode"] == "python-c" and type(meta["code"]) is str
               and len(meta["code"].encode("utf-8")) <= 32768)
        cap = meta["max_output_bytes"]
        _check(type(cap) is int and 0 < cap <= _MAX_OUTPUT)
        size = struct.unpack(">I", _sync_read(k, pipe, 4))[0]
        _check(size <= _MAX_INPUT)
        query = _sync_read(k, pipe, size)
        budget = [0, cap, False]
        output, errors = _Output(budget), _Discard(budget)
        stdin = io.TextIOWrapper(io.BytesIO(query), encoding="utf-8", errors="strict")
        stdout = io.TextIOWrapper(output, encoding="utf-8", errors="strict", write_through=True)
        stderr = io.TextIOWrapper(errors, encoding="utf-8", errors="strict", write_through=True)
        sys.stdin = sys.__stdin__ = stdin
        sys.stdout = sys.__stdout__ = stdout
        sys.stderr = sys.__stderr__ = stderr
        sys.argv = ["-c"]
        code = 0
        try:
            exec(compile(meta["code"], "<synthetic-q4-child>", "exec"),
                 {"__name__": "__main__", "__builtins__": __builtins__})
        except SystemExit as exit_request:
            code = exit_request.code if type(exit_request.code) is int else (
                0 if exit_request.code is None else 1)
        stdout.flush()
        _check(not budget[2] and -2147483648 <= code <= 2147483647)
        raw = output.getvalue()
        _sync_write(k, pipe, b"\1" + struct.pack(">I", len(raw)) + raw +
                    struct.pack(">i", code) + b"END!")
    except BaseException:
        # Fixed refusal only; do not serialize the exception, query or stderr.
        try:
            _sync_write(k, pipe, b"\0")
        except BaseException:
            pass
        raise _Refused() from None
    finally:
        k.CloseHandle(pipe)


def _sync_frame(k, pipe, limit):
    size = struct.unpack(">I", _sync_read(k, pipe, 4))[0]
    _check(size <= limit)
    return _sync_read(k, pipe, size) if size else b""


def _fixed_child_main(peer_code_sha256, name, parent_pid, parent_created):
    """Private fixed child endpoint; reached only through the verified-source bootstrap."""
    k, _ = _api()
    prefix = r"\\.\pipe\wd-q4-synthetic-"
    _check(type(name) is str and name.startswith(prefix) and
           len(name) == len(prefix) + 32 and
           all(character in "0123456789abcdef" for character in name[len(prefix):]))
    pid, created = int(parent_pid), int(parent_created)
    _check(pid > 0 and created > 0)
    pipe = _handle(k.CreateFileW(name, 0xC0000000, 0, None, 3, 0, None))
    try:
        _noninheritable(k, pipe)
        _peer(k, pipe, pid, created, client=False)
        raw = _fixed_child_protocol(lambda limit: _sync_frame(k, pipe, limit),
                                    lambda data: _sync_write(k, pipe, struct.pack(">I", len(data)) + data),
                                    peer_code_sha256, peer_path=__file__)
        _check(len(raw) <= _MAX_OUTPUT + _BIND_OVERHEAD)
        _sync_write(k, pipe, b"\1" + struct.pack(">I", len(raw)) + raw + struct.pack(">i", 0) + b"END!")
    except BaseException:
        # Fixed refusal only; never serialize the exception, plan, bundle or query.
        try:
            _sync_write(k, pipe, b"\0")
        except BaseException:
            pass
        raise _Refused() from None
    finally:
        k.CloseHandle(pipe)


def _fixed_diagnostic_main(peer_code_sha256, mode, name, parent_pid, parent_created):
    """Separate pinned diagnostic child; never emits the backend's answer."""
    _check(mode in ("observe", "synthetic-fault"))
    k, _ = _api()
    prefix = r"\\.\pipe\wd-q4-synthetic-"
    _check(type(name) is str and name.startswith(prefix) and len(name) == len(prefix) + 32
           and all(character in "0123456789abcdef" for character in name[len(prefix):]))
    pid, created = int(parent_pid), int(parent_created)
    _check(pid > 0 and created > 0)
    pipe = _handle(k.CreateFileW(name, 0xC0000000, 0, None, 3, 0, None))
    query_started = [False]
    phase = None
    try:
        try:
            _noninheritable(k, pipe)
            _peer(k, pipe, pid, created, client=False)
            raw = _fixed_child_protocol(lambda limit: _sync_frame(k, pipe, limit),
                lambda data: _sync_write(k, pipe, struct.pack(">I", len(data)) + data),
                peer_code_sha256, peer_path=__file__, _diagnostic_marker=query_started,
                _diagnostic_fault=(mode == "synthetic-fault"))
            _check(type(raw) is bytes and len(raw) <= _MAX_OUTPUT + _BIND_OVERHEAD)
            phase = b"completed"
        except Exception:
            if query_started[0] is True:
                phase = b"after_query"
        if phase is None:
            try:
                _sync_write(k, pipe, b"\0")
            except BaseException:
                pass
            raise _Refused() from None
        # The payload is only a constant; there is no exception, data, path,
        # length, hash or backend answer in the diagnostic response.
        _sync_write(k, pipe, b"\1" + struct.pack(">I", len(phase)) + phase + struct.pack(">i", 0) + b"END!")
    finally:
        k.CloseHandle(pipe)
