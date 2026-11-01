"""Unwired Windows synthetic pipe candidate. NOT a production transport.

No work happens on import. Explicit calls accept only synthetic Python -c
programs using this interpreter, or the separately pinned synthetic echo peer.
There is no general executable/wrapper adapter, model access, installer or CLI.
The caller must independently pin/trust this module, interpreter and peer code.
Self-hashing the child source is consistency, not authentication.

All transport handles are created non-inheritable. The child is created with
inherit_handles=False, suspended, then assigned to a kill-on-close job before
resume. Both peers check kernel-reported PID and process creation time before
payload transfer. The pipe has an explicit protected logon-SID DACL and refuses
remote clients. This is NOT a sandbox against malicious same-user code capable
of opening another process with PROCESS_DUP_HANDLE.

No security test, production readiness or whole-OS hard deadline is claimed.
Abrupt parent death before job assignment may leave a suspended child; no
payload is sent before assignment. Cancellation with uncompleted kernel I/O
quarantines its memory/handles and disables further calls, rather than freeing
an in-use OVERLAPPED or claiming complete cleanup. Lead owns conformance tests.
"""

import ctypes as C
from ctypes import wintypes as W
import hashlib
import hmac
import io
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
import time
import uuid


_MAX_INPUT = 65536
_MAX_OUTPUT = 262144
_MAX_META = 65536
_INVALID = C.c_void_p(-1).value
_QUARANTINE = []  # At most one invocation; any entry disables new work.
_RUN_LOCK = threading.Lock()


class _Refused(ValueError):
    def __init__(self):
        super().__init__("secure_transport_refused")


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
        "AssignProcessToJobObject": (W.BOOL, [W.HANDLE, W.HANDLE]),
        "TerminateJobObject": (W.BOOL, [W.HANDLE, W.UINT]),
        "TerminateProcess": (W.BOOL, [W.HANDLE, W.UINT]),
        "ResumeThread": (W.DWORD, [W.HANDLE]),
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


def _active(k, handle):
    code = W.DWORD()
    _check(k.GetExitCodeProcess(handle, C.byref(code)))
    _check(code.value == 259)


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
        k.CloseHandle(process)


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
        if text.value:
            k.LocalFree(text)
        k.CloseHandle(token)


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("secure_transport_timeout")
    return max(1, min(0xFFFFFFFE, math.ceil(remaining * 1000)))


def _io(k, pipe, data, count, deadline, *, connect=False, write=False, eof=False):
    event = _handle(k.CreateEventW(None, True, False, None))
    ov = _OV(event=event)
    buffer = C.create_string_buffer(data if write else count)
    done = W.DWORD()
    outstanding = False
    retained = False
    try:
        _noninheritable(k, event)
        _remaining(deadline)
        if connect:
            ok = k.ConnectNamedPipe(pipe, C.byref(ov))
        else:
            function = k.WriteFile if write else k.ReadFile
            ok = function(pipe, buffer, count, C.byref(done), C.byref(ov))
        error = C.get_last_error() if not ok else 0
        if connect and error == 535:  # Client connected before ConnectNamedPipe.
            return b""
        if eof and error == 109:
            return b""
        _check(ok or error == 997)
        outstanding = not ok
        if outstanding:
            state = k.WaitForSingleObject(event, _remaining(deadline))
            if state == 258:
                raise TimeoutError("secure_transport_timeout")
            _check(state == 0)
        ok = k.GetOverlappedResult(pipe, C.byref(ov), C.byref(done), False)
        error = C.get_last_error() if not ok else 0
        outstanding = not ok and error == 996
        if eof and error == 109:
            return b""
        _check(ok and done.value <= count)
        if write:
            _check(done.value == count)
            return b""
        return buffer.raw[:done.value]
    finally:
        if outstanding:
            k.CancelIoEx(pipe, C.byref(ov))
            # Cancellation is a request, not completion. Never release live memory.
            if k.WaitForSingleObject(event, 1000) != 0:
                _QUARANTINE.append((k, pipe, ov, buffer, event))
                retained = True
            else:
                k.GetOverlappedResult(pipe, C.byref(ov), C.byref(done), False)
        if not retained:
            k.CloseHandle(event)


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


def _fixed_ready_exchange(k, pipe, expected_pid, expected_created, control, query, deadline):
    """Synthetic protocol primitive: challenge/order only, not authentication.

    The fixed peer must establish its own code/runtime/data bindings before it
    emits READY. A matching peer declaration does not authenticate those
    bindings. The parent checks peer PID/creation and the closed reply only;
    it does not disclose its expected binding digest in the challenge.
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
        _frame_write(k, pipe, query, deadline)
        return
    except Exception:
        pass
    del control, query, raw, ready, ready_raw, challenge
    raise _Refused() from None


_BOOTSTRAP = """
import hashlib,sys
try:
 with open(sys.argv[1], 'rb') as source:
  raw=source.read(131073)
 if len(raw)>131072 or hashlib.sha256(raw).hexdigest()!=sys.argv[2]:
  raise ValueError()
 scope={'__name__':'_wd_secure_child','__file__':sys.argv[1]}
 exec(compile(raw,'<wd-secure-child>','exec'),scope)
 scope['_client_main'](*sys.argv[3:])
except BaseException:
 sys.exit(2)
"""


def _validate(stdin_bytes, timeout_seconds, max_output_bytes):
    _check(type(stdin_bytes) is bytes and len(stdin_bytes) <= _MAX_INPUT)
    _check(type(timeout_seconds) in (int, float) and math.isfinite(timeout_seconds)
           and 0 < timeout_seconds <= 1800)
    _check(type(max_output_bytes) is int and 0 < max_output_bytes <= _MAX_OUTPUT)


def _run(command, cwd, metadata, stdin_bytes, timeout_seconds, max_output_bytes):
    # No fixed-Q4 process may start until a trusted peer binder exists.
    # Reject non-dicts without invoking arbitrary caller-supplied mapping code.
    # The READY primitive above is intentionally disconnected from _run.
    if not (type(metadata) is dict and all(type(key) is str for key in metadata)
            and type(metadata.get("mode")) is str
            and metadata["mode"] in ("python-c", "echo")):
        del metadata, stdin_bytes
        raise _Refused()
    _validate(stdin_bytes, timeout_seconds, max_output_bytes)
    if not _RUN_LOCK.acquire(blocking=False):
        raise _Refused()
    k = a = None
    pipe = job = process = thread = None
    assigned = False
    deadline = time.monotonic() + timeout_seconds
    try:
        _check(not _QUARANTINE)
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
            k.LocalFree(descriptor)
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
        import _winapi
        environment = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR")
                       if key in os.environ}
        environment.update(TEMP=cwd, TMP=cwd)  # Caller-owned, budgeted synthetic scratch.
        # No STARTF_USESTDHANDLES, HANDLE_LIST, anonymous pipes or inheritable gap.
        process, thread, pid, _ = _winapi.CreateProcess(
            command[0], line, None, None, False, 0x08000404, environment,
            cwd, subprocess.STARTUPINFO())
        _noninheritable(k, int(process))
        _noninheritable(k, int(thread))
        _check(k.AssignProcessToJobObject(job, int(process)))
        assigned = True
        child_created = _created(k, int(process))
        _check(k.ResumeThread(int(thread)) != 0xFFFFFFFF)
        _io(k, pipe, None, 0, deadline, connect=True)
        _peer(k, pipe, pid, child_created, client=True)
        _frame_write(k, pipe, raw_meta, deadline)
        _frame_write(k, pipe, stdin_bytes, deadline)
        status = _read(k, pipe, 1, deadline)
        if status != b"\1":
            raise _Refused()
        output = _frame_read(k, pipe, max_output_bytes, deadline)
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
    except TimeoutError:
        raise TimeoutError("secure_transport_timeout") from None
    except Exception:
        raise _Refused() from None
    finally:
        cleanup_ok = True
        if k is not None:
            if process is not None:
                if assigned and job:
                    cleanup_ok = bool(k.TerminateJobObject(job, 124)) and cleanup_ok
                elif not assigned:
                    cleanup_ok = bool(k.TerminateProcess(int(process), 125)) and cleanup_ok
            if job:
                cleanup_ok = bool(k.CloseHandle(job)) and cleanup_ok
            if process is not None:
                cleanup_ok = bool(k.CloseHandle(int(process))) and cleanup_ok
            if thread is not None:
                cleanup_ok = bool(k.CloseHandle(int(thread))) and cleanup_ok
            if pipe and not any(entry[1] == pipe for entry in _QUARANTINE):
                cleanup_ok = bool(k.CloseHandle(pipe)) and cleanup_ok
        _RUN_LOCK.release()
        if not cleanup_ok:
            raise _Refused()


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
    cleanup_type = cleanup_lease = failure = None
    try:
        from tools.wd_q4_fixed_parent_attestation import AttestationCleanupUnknown as cleanup_type
        return _run_bounded_impl(executable=executable, arguments=arguments, cwd=cwd,
            stdin_bytes=stdin_bytes, timeout_seconds=timeout_seconds, max_output_bytes=max_output_bytes,
            expected_interpreter_sha256=expected_interpreter_sha256, interpreter_max_bytes=interpreter_max_bytes)
    except BaseException as exc:
        if cleanup_type is not None and isinstance(exc, cleanup_type):
            cleanup_lease = exc.lease
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
        raw = stream.read(131073)
    _check(len(raw) <= 131072)
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
