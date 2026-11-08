"""Bounded Windows-only negative tests for the unwired synthetic transport.

No model, real input, live bridge, old candidate or production process is used.
Direct CreateProcess calls are outside the D15 subprocess-event proof boundary.
"""
import ctypes
from ctypes import wintypes
import importlib
import os
import struct
import subprocess
import sys
import time
import uuid

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows named pipes")


def _finish_owned_processes(api, handles, *, wait_ms=5000):
    """Check and close every original-process handle, collecting all failures.

    A wait failure is UNKNOWN: never use a PID or blindly terminate that process.
    """
    problems = []
    for process in handles:
        try:
            try:
                state = api.WaitForSingleObject(process, wait_ms)
            except BaseException:
                problems.append("synthetic_process_wait_unknown")
                continue
            if state == api.WAIT_OBJECT_0:
                continue
            if state != api.WAIT_TIMEOUT:
                problems.append("synthetic_process_wait_unknown")
                continue
            problems.append("synthetic_process_not_reaped")
            try:
                api.TerminateProcess(process, 127)
            except BaseException:
                # The process may have exited concurrently. Still wait below.
                problems.append("synthetic_process_terminate_failed")
            try:
                if api.WaitForSingleObject(process, 5000) != api.WAIT_OBJECT_0:
                    problems.append("synthetic_process_cleanup_unknown")
            except BaseException:
                problems.append("synthetic_process_cleanup_unknown")
        finally:
            try:
                api.CloseHandle(process)
            except BaseException:
                problems.append("synthetic_process_handle_close_failed")
    return problems


def _close_owned_handles(api, handles):
    problems = []
    for handle in handles:
        if handle is not None:
            try:
                api.CloseHandle(handle)
            except BaseException:
                problems.append("synthetic_handle_close_unknown")
    return problems


def _reap_handles(api, process, thread):
    problems = []
    try:
        if process is not None:
            # Forced termination is expected for the test's competitor. All
            # failure/UNKNOWN states still remain errors.
            result = _finish_owned_processes(api, [process], wait_ms=0)
            problems.extend(item for item in result if item != "synthetic_process_not_reaped")
    except BaseException:
        problems.append("synthetic_process_cleanup_unknown")
        problems.extend(_close_owned_handles(api, [process]))
    finally:
        problems.extend(_close_owned_handles(api, [thread]))
    return problems


def _tracked_create(api, original, handles, args):
    result = original(*args)
    process, thread = result[:2]
    try:
        current = api.GetCurrentProcess()
        duplicate = api.DuplicateHandle(current, process, current, 0, False,
                                        api.DUPLICATE_SAME_ACCESS)
    except BaseException as exc:
        # Both original handles came from this exact CreateProcess call.
        problems = _reap_handles(api, process, thread)
        message = "synthetic_process_tracking_failed"
        if problems:
            message += ": " + ", ".join(problems)
        raise RuntimeError(message) from exc
    handles.append(duplicate)
    return result


@pytest.fixture
def transport():
    return importlib.import_module("tools.wd_q4_secure_transport")


@pytest.fixture(autouse=True)
def own_and_reap_synthetic_processes(monkeypatch):
    """Duplicate each directly created process handle and verify its exit.

    These handles identify the original kernel process objects, not PIDs read
    back from a file. Any necessary forced cleanup is still a test failure.
    """
    if os.name != "nt":
        yield
        return
    import _winapi

    original = _winapi.CreateProcess
    handles = []

    def tracked_create(*args):
        return _tracked_create(_winapi, original, handles, args)

    monkeypatch.setattr(_winapi, "CreateProcess", tracked_create)
    yield
    problems = _finish_owned_processes(_winapi, handles)
    if problems:
        pytest.fail(", ".join(problems))


def test_cleanup_checks_all_owned_handles_after_wait_terminate_and_close_failures():
    class SyntheticApi:
        WAIT_OBJECT_0 = 0
        WAIT_TIMEOUT = 258
        WAIT_FAILED = 0xFFFFFFFF

        def __init__(self):
            self.waited = []
            self.terminated = []
            self.closed = []

        def WaitForSingleObject(self, handle, timeout):
            self.waited.append(handle)
            if handle == "timeout":
                return self.WAIT_TIMEOUT
            if handle == "wait_exception":
                raise OSError("synthetic")
            if handle == "wait_failed":
                return self.WAIT_FAILED
            return self.WAIT_OBJECT_0

        def TerminateProcess(self, handle, code):
            self.terminated.append(handle)
            raise OSError("synthetic")

        def CloseHandle(self, handle):
            self.closed.append(handle)
            if handle == "wait_exception":
                raise OSError("synthetic")

    api = SyntheticApi()
    problems = _finish_owned_processes(api, ["timeout", "wait_failed", "wait_exception", "finished"])
    assert api.terminated == ["timeout"]  # WAIT_FAILED/exception never authorizes termination.
    assert api.closed == ["timeout", "wait_failed", "wait_exception", "finished"]
    assert "finished" in api.waited
    assert problems.count("synthetic_process_wait_unknown") == 2
    assert "synthetic_process_terminate_failed" in problems
    assert "synthetic_process_cleanup_unknown" in problems
    assert "synthetic_process_handle_close_failed" in problems


@pytest.mark.parametrize("fail_at", ["current", "duplicate", "reap"])
def test_creation_and_reap_failures_attempt_both_closes(fail_at):
    class SyntheticApi:
        WAIT_OBJECT_0 = 0
        WAIT_TIMEOUT = 258
        WAIT_FAILED = 0xFFFFFFFF
        DUPLICATE_SAME_ACCESS = 2

        def __init__(self):
            self.closed = []
            self.terminated = []
            self.waits = 0

        def GetCurrentProcess(self):
            if fail_at == "current":
                raise OSError("synthetic_get_current_failure")
            return "current"

        def DuplicateHandle(self, *args):
            raise OSError("synthetic_duplicate_failure")

        def WaitForSingleObject(self, handle, timeout):
            self.waits += 1
            if fail_at == "duplicate":
                return self.WAIT_FAILED
            return self.WAIT_TIMEOUT if self.waits == 1 else self.WAIT_FAILED

        def TerminateProcess(self, handle, code):
            self.terminated.append(handle)
            raise OSError("synthetic_terminate_failure")

        def CloseHandle(self, handle):
            self.closed.append(handle)
            if handle == "process":
                raise OSError("synthetic_process_close_failure")

    api = SyntheticApi()
    if fail_at == "reap":
        problems = _reap_handles(api, "process", "thread")
    else:
        with pytest.raises(RuntimeError, match="synthetic_process_tracking_failed") as raised:
            _tracked_create(api, lambda: ("process", "thread", 1, 1), [], ())
        problems = str(raised.value)
    assert api.closed == ["process", "thread"]
    assert "synthetic_process_handle_close_failed" in problems
    if fail_at == "duplicate":
        assert api.terminated == []
        assert "synthetic_process_wait_unknown" in problems
    else:
        assert api.terminated == ["process"]
        assert "synthetic_process_cleanup_unknown" in problems


def _run(t, tmp_path, *, code="pass", payload=b"synthetic", timeout=5, limit=64):
    return t.run_bounded(executable=sys.executable, arguments=("-I", "-B", "-c", code),
                         cwd=str(tmp_path), stdin_bytes=payload,
                         timeout_seconds=timeout, max_output_bytes=limit)


def _start_probe(original, tmp_path, code, *args):
    import _winapi
    line = subprocess.list2cmdline([sys.executable, "-I", "-S", "-B", "-c", code, *args])
    environment = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR") if key in os.environ}
    environment.update(TEMP=str(tmp_path), TMP=str(tmp_path))
    return original(sys.executable, line, None, None, False, 0x08000400,
                    environment, str(tmp_path), subprocess.STARTUPINFO())


def _reap(proc, thread):
    import _winapi
    problems = _reap_handles(_winapi, proc, thread)
    if problems:
        raise RuntimeError(", ".join(problems))


def test_oversized_input_refuses_before_any_child(transport, tmp_path, monkeypatch):
    import _winapi
    def unexpected(*args, **kwargs):
        pytest.fail("oversized input must not launch")
    monkeypatch.setattr(_winapi, "CreateProcess", unexpected)
    with pytest.raises(ValueError):
        _run(transport, tmp_path, payload=b"x" * (transport._MAX_INPUT + 1))


def test_real_creation_requests_first_instance_local_only_noninheritable(transport, tmp_path, monkeypatch):
    real_api = transport._api
    observed = []

    class KernelProxy:
        def __init__(self, kernel):
            self.kernel = kernel
        def __getattr__(self, name):
            return getattr(self.kernel, name)
        def CreateNamedPipeW(self, *args):
            attrs = ctypes.cast(args[7], ctypes.POINTER(transport._SA)).contents
            observed.append((args[1], args[2], attrs.inherit, bool(attrs.descriptor)))
            return self.kernel.CreateNamedPipeW(*args)

    def wrapped_api():
        kernel, advapi = real_api()
        return KernelProxy(kernel), advapi

    monkeypatch.setattr(transport, "_api", wrapped_api)
    assert _run(transport, tmp_path) == (b"", 0)
    assert len(observed) == 1
    open_mode, pipe_mode, inherit, has_descriptor = observed[0]
    assert open_mode & 0x00080000 and open_mode & 0x40000000  # first instance, overlapped
    assert pipe_mode & 8  # PIPE_REJECT_REMOTE_CLIENTS requested, not an actual remote test.
    assert not inherit and has_descriptor


def test_real_wrong_client_connects_first_and_is_rejected(transport, tmp_path, monkeypatch):
    import _winapi
    suffix = uuid.uuid4().hex
    name = r"\\.\pipe\wd-q4-synthetic-" + suffix
    ready = tmp_path / "rogue-ready"
    proc = thread = None
    real_create = _winapi.CreateProcess
    real_spawn = transport._create_process_in_job
    original_write = transport._frame_write
    writes = []

    class FixedUuid:
        hex = suffix

    # The probe owns only its client handle; it stays alive until test cleanup.
    code = r'''import ctypes,sys,time
from ctypes import wintypes as w
k=ctypes.WinDLL('kernel32',use_last_error=True)
f=k.CreateFileW; f.restype=w.HANDLE; f.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,ctypes.c_void_p,w.DWORD,w.DWORD,w.HANDLE]
h=f(sys.argv[1],0xC0000000,0,None,3,0,None)
if h in (None,0,ctypes.c_void_p(-1).value): sys.exit(3)
with open(sys.argv[2],'w',encoding='ascii') as out: out.write('connected')
time.sleep(30)
'''

    def with_competitor(*args):
        nonlocal proc, thread
        proc, thread, _, _ = _start_probe(real_create, tmp_path, code, name, str(ready))
        deadline = time.monotonic() + 5
        while not ready.is_file():
            if _winapi.WaitForSingleObject(proc, 0) == _winapi.WAIT_OBJECT_0:
                raise AssertionError("synthetic_competitor_failed_to_connect")
            if time.monotonic() >= deadline:
                raise AssertionError("synthetic_competitor_timeout")
            time.sleep(0.01)
        return real_spawn(*args)

    def record_write(*args, **kwargs):
        writes.append(True)
        return original_write(*args, **kwargs)

    monkeypatch.setattr(transport.uuid, "uuid4", lambda: FixedUuid())
    monkeypatch.setattr(transport, "_create_process_in_job", with_competitor)
    monkeypatch.setattr(transport, "_frame_write", record_write)
    try:
        with pytest.raises(ValueError):
            _run(transport, tmp_path, payload=b"synthetic-not-for-competitor")
        assert ready.read_text(encoding="ascii") == "connected"
        assert writes == [], "payload must not be sent to the wrong PID"
    finally:
        _reap(proc, thread)


def test_real_child_rejects_wrong_server_pid_before_metadata(transport, tmp_path):
    import _winapi
    k, a = transport._api()
    name = r"\\.\pipe\wd-q4-synthetic-" + uuid.uuid4().hex
    pipe = proc = thread = None
    try:
        descriptor = transport._descriptor(k, a)
        try:
            attrs = transport._SA(ctypes.sizeof(transport._SA), descriptor, False)
            pipe = transport._handle(k.CreateNamedPipeW(name, 3 | 0x40000000 | 0x00080000,
                                                       8, 1, 65536, 65536, 0, ctypes.byref(attrs)))
        finally:
            k.LocalFree(descriptor)
        source = str((__import__("pathlib").Path(transport.__file__)).resolve())
        # A real server is this process; expected server PID 1 cannot match it.
        code = r'''import runpy,sys
m=runpy.run_path(sys.argv[1])
try: m['_client_main'](sys.argv[2], '1', '1')
except Exception: sys.exit(77)
sys.exit(0)
'''
        proc, thread, _, _ = _start_probe(_winapi.CreateProcess, tmp_path, code, source, name)
        deadline = time.monotonic() + 5
        transport._io(k, pipe, None, 0, deadline, connect=True)
        assert transport._read(k, pipe, 1, deadline) == b"\0"
        assert _winapi.WaitForSingleObject(proc, 5000) == _winapi.WAIT_OBJECT_0
        assert _winapi.GetExitCodeProcess(proc) == 77
    finally:
        try:
            _reap(proc, thread)
        finally:
            if pipe is not None and not k.CloseHandle(pipe):
                raise RuntimeError("synthetic_pipe_handle_close_unknown")


_MALFORMED_PEER = r'''import ctypes,sys,struct,time
from ctypes import wintypes as w
k=ctypes.WinDLL('kernel32',use_last_error=True)
o=k.CreateFileW; o.restype=w.HANDLE; o.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,ctypes.c_void_p,w.DWORD,w.DWORD,w.HANDLE]
r=k.ReadFile; r.restype=w.BOOL; r.argtypes=[w.HANDLE,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD),ctypes.c_void_p]
v=k.WriteFile; v.restype=w.BOOL; v.argtypes=[w.HANDLE,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD),ctypes.c_void_p]
c=k.CloseHandle; c.argtypes=[w.HANDLE]
h=o(sys.argv[-3],0xC0000000,0,None,3,0,None)
if h in (None,0,ctypes.c_void_p(-1).value): sys.exit(3)
def read(n):
 out=b''
 while len(out)<n:
  b=ctypes.create_string_buffer(n-len(out)); done=w.DWORD()
  if not r(h,b,len(b),ctypes.byref(done),None) or not done.value: sys.exit(4)
  out+=b.raw[:done.value]
 return out
for _ in range(2):
 n=struct.unpack('>I',read(4))[0]
 if n>65536: sys.exit(5)
 read(n)
kind=KIND
if kind=='oversized': out=b'\1'+struct.pack('>I',65)
elif kind=='truncated': out=b'\1'+struct.pack('>I',4)+b'ab'
elif kind=='wrong': out=b'\1'+struct.pack('>I',1)+b'a'+struct.pack('>i',0)+b'FAIL'
elif kind=='extra': out=b'\1'+struct.pack('>I',1)+b'a'+struct.pack('>i',0)+b'END!X'
else: sys.exit(6)
b=ctypes.create_string_buffer(out); n=w.DWORD()
if not v(h,b,len(out),ctypes.byref(n),None) or n.value!=len(out): sys.exit(7)
time.sleep(.1)
c(h)
'''


@pytest.mark.parametrize("kind", ["oversized", "truncated", "wrong", "extra"])
def test_real_synthetic_peer_malformed_wire_is_refused(transport, tmp_path, monkeypatch, kind):
    observed = []
    original_io = transport._io

    def observe_read(*args, **kwargs):
        value = original_io(*args, **kwargs)
        if not kwargs.get("connect") and not kwargs.get("write"):
            observed.append(value)
        return value

    monkeypatch.setattr(transport, "_io", observe_read)
    monkeypatch.setattr(transport, "_BOOTSTRAP", _MALFORMED_PEER.replace("KIND", repr(kind)))
    with pytest.raises(ValueError):
        _run(transport, tmp_path)
    wire = b"".join(observed)
    if kind == "oversized":
        assert wire == b"\1" + struct.pack(">I", 65)
    elif kind == "truncated":
        assert wire == b"\1" + struct.pack(">I", 4) + b"ab"
    elif kind == "wrong":
        assert wire.endswith(b"FAIL")
    else:
        assert wire.endswith(b"END!X")


@pytest.mark.skip(reason="no creation-time-owned grandchild handle with verified cleanup")
def test_timed_out_synthetic_job_closes_its_spawned_grandchild():
    """Intentionally not run until test-owned descendant custody is proven."""
