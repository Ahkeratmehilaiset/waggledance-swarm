"""Synthetic Windows transport checks, not a production security certification.

No live service, real query, model, or old process-tree candidate is invoked.
The wrong-peer test changes the expected PID for a real connected synthetic child;
it does not simulate a hostile independently connected pipe peer.
"""
import ctypes
from ctypes import wintypes
import importlib
import os
import subprocess
import sys
import time
import uuid

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows pipe conformance")


@pytest.fixture
def transport():
    return importlib.import_module("tools.wd_q4_secure_transport")


def _run(transport, tmp_path, code, payload=b"", timeout=5, limit=1024):
    return transport.run_bounded(
        executable=sys.executable,
        arguments=("-I", "-B", "-c", code),
        cwd=str(tmp_path), stdin_bytes=payload,
        timeout_seconds=timeout, max_output_bytes=limit,
    )


def test_wrong_expected_peer_pid_refuses_before_payload(transport, tmp_path, monkeypatch):
    real_peer = transport._peer
    frames = []
    original_write = transport._frame_write

    def wrong_parent_expectation(k, pipe, pid, created, *, client):
        if client:
            # The child is a different, real process; request the parent PID.
            return real_peer(k, pipe, os.getpid(),
                             transport._created(k, k.GetCurrentProcess()), client=True)
        return real_peer(k, pipe, pid, created, client=client)

    def record_write(*args, **kwargs):
        frames.append(True)
        return original_write(*args, **kwargs)

    monkeypatch.setattr(transport, "_peer", wrong_parent_expectation)
    monkeypatch.setattr(transport, "_frame_write", record_write)
    with pytest.raises(ValueError):
        _run(transport, tmp_path, "pass", payload=b"synthetic-peer-probe")
    assert frames == [], "payload framing was reached despite the wrong peer"


def test_occupied_pipe_name_refuses_before_child_launch(transport, tmp_path, monkeypatch):
    import _winapi

    k, a = transport._api()
    suffix = uuid.uuid4().hex
    name = r"\\.\pipe\wd-q4-synthetic-" + suffix
    descriptor = transport._descriptor(k, a)
    try:
        security = transport._SA(ctypes.sizeof(transport._SA), descriptor, False)
        occupied = transport._handle(k.CreateNamedPipeW(
            name, 3 | 0x40000000 | 0x00080000, 8, 1, 65536, 65536, 0,
            ctypes.byref(security)))
    finally:
        k.LocalFree(descriptor)

    class FixedUuid:
        hex = suffix

    def unexpected_launch(*args, **kwargs):
        pytest.fail("an occupied first-instance name must refuse before spawning")

    try:
        monkeypatch.setattr(transport.uuid, "uuid4", lambda: FixedUuid())
        monkeypatch.setattr(_winapi, "CreateProcess", unexpected_launch)
        with pytest.raises(ValueError):
            _run(transport, tmp_path, "pass")
    finally:
        k.CloseHandle(occupied)


def test_unconnected_child_times_out_without_query_in_arguments(transport, tmp_path, monkeypatch):
    import _winapi

    marker = b"SYNTHETIC_QUERY_SENTINEL_5d6aac68"
    calls = []
    original = _winapi.CreateProcess

    def observed(*args):
        environment = args[6] or {}
        calls.append((args[1], dict(environment)))
        return original(*args)

    # The bootstrap deliberately never opens the named pipe.
    monkeypatch.setattr(transport, "_BOOTSTRAP", "import time; time.sleep(30)")
    monkeypatch.setattr(_winapi, "CreateProcess", observed)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        _run(transport, tmp_path, "pass", payload=marker, timeout=0.2)
    assert time.monotonic() - started < 10
    assert len(calls) == 1
    command, environment = calls[0]
    assert marker.decode("ascii") not in command
    assert all(marker.decode("ascii") not in str(key) + str(value)
               for key, value in environment.items())


def test_declared_output_bound_is_enforced_for_real_synthetic_child(transport, tmp_path):
    code = "import sys; sys.stdout.buffer.write(b'x' * int(sys.stdin.read()))"
    assert _run(transport, tmp_path, code, payload=b"32", limit=32) == (b"x" * 32, 0)
    with pytest.raises(ValueError):
        _run(transport, tmp_path, code, payload=b"33", limit=32)


def test_connected_child_that_never_replies_hits_read_timeout(transport, tmp_path, monkeypatch):
    writes = []
    original = transport._frame_write

    def record_write(*args, **kwargs):
        original(*args, **kwargs)
        writes.append(True)

    monkeypatch.setattr(transport, "_frame_write", record_write)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        _run(transport, tmp_path, "import time; time.sleep(30)",
             payload=b"synthetic-read-timeout", timeout=1)
    assert len(writes) == 2, "timeout occurred before both frames were sent"
    assert time.monotonic() - started < 10


def test_unrelated_inheritor_cannot_open_pipe_handle_or_write_sentinel(transport, tmp_path, monkeypatch):
    import _winapi

    original_check = transport._noninheritable
    original_create = _winapi.CreateProcess
    pipe_handles = []
    probe_codes = []

    def capture_pipe(k, handle):
        original_check(k, handle)
        get_type = k.GetFileType
        get_type.argtypes = [wintypes.HANDLE]
        get_type.restype = wintypes.DWORD
        if get_type(handle) == 3 and not pipe_handles:
            pipe_handles.append(int(handle))

    def probe_before_root_launch(*args):
        assert len(pipe_handles) == 1
        # If the unrelated child obtains this inherited handle, attempt a
        # synthetic write as well as reporting the handle as a failure. A
        # failed write does not turn an inherited handle into a pass.
        code = (
            "import ctypes,sys; from ctypes import wintypes as w; "
            "k=ctypes.WinDLL('kernel32',use_last_error=True); "
            "g=k.GetHandleInformation; g.argtypes=[w.HANDLE,ctypes.POINTER(w.DWORD)]; g.restype=w.BOOL; "
            "t=k.GetFileType; t.argtypes=[w.HANDLE]; t.restype=w.DWORD; "
            "f=k.WriteFile; f.argtypes=[w.HANDLE,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD),ctypes.c_void_p]; f.restype=w.BOOL; "
            f"h=w.HANDLE({pipe_handles[0]}); flags=w.DWORD(); "
            "present=bool(g(h,ctypes.byref(flags))) and t(h)==3; "
            "sentinel=ctypes.create_string_buffer(b'SYNTHETIC-INHERITOR'); n=w.DWORD(); "
            "f(h,sentinel,19,ctypes.byref(n),None) if present else None; "
            "sys.exit(91 if present else 0)"
        )
        line = subprocess.list2cmdline([sys.executable, "-I", "-S", "-B", "-c", code])
        env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR") if key in os.environ}
        env.update(TEMP=str(tmp_path), TMP=str(tmp_path))
        proc = thread = None
        try:
            proc, thread, _, _ = original_create(
                sys.executable, line, None, None, True, 0x08000400,
                env, str(tmp_path), subprocess.STARTUPINFO())
            wait = _winapi.WaitForSingleObject(proc, 5000)
            if wait != _winapi.WAIT_OBJECT_0:
                _winapi.TerminateProcess(proc, 92)
                _winapi.WaitForSingleObject(proc, 2000)
                raise RuntimeError("synthetic_inheritor_timed_out")
            probe_codes.append(_winapi.GetExitCodeProcess(proc))
        finally:
            if thread is not None:
                _winapi.CloseHandle(thread)
            if proc is not None:
                _winapi.CloseHandle(proc)
        return original_create(*args)

    monkeypatch.setattr(transport, "_noninheritable", capture_pipe)
    monkeypatch.setattr(_winapi, "CreateProcess", probe_before_root_launch)
    result = _run(transport, tmp_path,
                  "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())",
                  payload=b"synthetic-only")
    assert result == (b"synthetic-only", 0)
    assert probe_codes == [0], "unrelated process inherited a working pipe handle"
