"""Synthetic Windows transport contract; no real Mind service or model.

The CreateProcess spy only observes calls in this pytest process. It does not
prove named-pipe peer authentication, ACLs, descendant behavior or readiness
for production. The payload here is synthetic and is never a command argument.
"""

import importlib
import contextlib
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows CreateProcess contract")


@pytest.fixture
def transport():
    # Import here so every test is RED when the implementation is absent.
    return importlib.import_module("tools.wd_q4_secure_transport")


def test_fixed_peer_manifest_limit_matches_verifier_before_spawn(transport, tmp_path, monkeypatch):
    from sandbox.mind.step2.runtime_manifest import MAX_MANIFEST_BYTES
    from tools import wd_q4_fixed_parent_attestation as attestation

    document = {"schema": "wd.q4-runtime-manifest.v1", "generation": "a" * 40,
                "revision": "b" * 40,
                "files": [{"path": f"site/file{i:06d}", "size": 0, "sha256": "0" * 64}
                          for i in range(10000)]}
    raw = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    assert 1024 * 1024 < len(raw) < MAX_MANIFEST_BYTES
    assert transport._MAX_RUNTIME_MANIFEST == MAX_MANIFEST_BYTES
    sha = lambda value: hashlib.sha256(value).hexdigest()
    plan_bytes, bundle, interpreter_pin = b"plan", b"synthetic", "a" * 64
    plan = {"backend": "synthetic-v1", "interpreter_sha256": interpreter_pin,
            "peer_code_sha256": sha(Path(transport.__file__).read_bytes()),
            "runtime_manifest_sha256": sha(raw), "bundle_bytes": len(bundle), "bundle_sha256": sha(bundle)}
    monkeypatch.setattr(transport, "_fixed_plan", lambda _: plan)
    monkeypatch.setattr(attestation, "attest_file", lambda *a, **k: contextlib.nullcontext())
    calls = []
    # v5: the parent unwraps the child output, so the fake child returns a frame bound to the nonce and query
    monkeypatch.setattr(transport, "_run", lambda *a, **k: (
        calls.append(True) or (transport._fixed_wrap(k["fixed"][0]["nonce"], a[3], b"ok"), 0)))
    def invoke(value):
        return transport._run_fixed_peer_impl(sys.executable, interpreter_pin, 1, plan_bytes, sha(plan_bytes),
                                               bundle, value, b"query", str(tmp_path), 1, 1024)
    assert invoke(raw) == (b"ok", 0)
    too_large = raw + b" " * (MAX_MANIFEST_BYTES + 1 - len(raw))
    plan["runtime_manifest_sha256"] = sha(too_large)
    with pytest.raises(transport._Refused):
        invoke(too_large)
    assert calls == [True]


def _mock_spawn_from_winapi(k, job, executable, line, environment, cwd, info):
    import _winapi
    return _winapi.CreateProcess(executable, line, None, None, False, 0x08000404, environment, cwd, None)


@pytest.mark.parametrize("failure", [None, "init", "attribute", "create"])
def test_native_spawn_contract_preserves_job_environment_and_no_inheritance(transport, failure):
    import ctypes
    from ctypes import wintypes
    calls = []
    class Kernel:
        def InitializeProcThreadAttributeList(self, attrs, count, flags, size):
            assert count == 1 and flags == 0
            if attrs is None:
                ctypes.cast(size, ctypes.POINTER(ctypes.c_size_t)).contents.value = 128
            calls.append("init")
            return attrs is not None and failure != "init"
        def UpdateProcThreadAttribute(self, attrs, flags, attribute, values, size, prior, result):
            assert attribute == 0x0002000D and flags == 0 and size == ctypes.sizeof(wintypes.HANDLE)
            assert ctypes.cast(values, ctypes.POINTER(wintypes.HANDLE)).contents.value == 12
            calls.append("attribute")
            return failure != "attribute"
        def CreateProcessW(self, exe, command, proc_sa, thread_sa, inherit, flags, env, cwd, startup, info):
            assert exe == "synthetic.exe" and command.value == '"synthetic.exe" -I'
            assert proc_sa is None and thread_sa is None and inherit is False and flags == 0x08080404
            assert cwd == "synthetic-cwd"
            assert env and env.value
            child = ctypes.cast(info, ctypes.POINTER(transport._ProcessInfo)).contents
            assert not child.process and not child.thread and not child.pid and not child.tid
            assert ctypes.cast(startup, ctypes.POINTER(transport._StartupInfoEx)).contents.startup.cb == ctypes.sizeof(transport._StartupInfoEx)
            values = []
            cursor = env.value
            while True:
                value = ctypes.wstring_at(cursor)
                if not value: break
                values.append(value)
                cursor += (len(value) + 1) * ctypes.sizeof(ctypes.c_wchar)
            assert values == ["SYSTEMROOT=synthetic-root", "TEMP=synthetic-temp"]
            calls.append("create")
            if failure == "create": return 0
            child.process, child.thread, child.pid, child.tid = 15, 16, 17, 18
            return 1
        def DeleteProcThreadAttributeList(self, attrs):
            calls.append("delete")
    info = transport._ProcessInfo()
    args = (Kernel(), 12, "synthetic.exe", '"synthetic.exe" -I',
            {"TEMP": "synthetic-temp", "SYSTEMROOT": "synthetic-root"}, "synthetic-cwd", info)
    if failure:
        with pytest.raises(transport._Refused): transport._create_process_in_job(*args)
        assert not info.process and not info.thread
    else:
        assert transport._create_process_in_job(*args) == (15, 16, 17, 18)
    assert ("delete" in calls) is (failure != "init")
    assert ("create" in calls) is (failure not in ("init", "attribute"))


@pytest.mark.parametrize("error_kind", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_attribute_list_cleanup_after_confirmed_initialization_is_interruption_safe(transport, error_kind):
    """Fake kernel only; native-call-internal interruptions remain unproven."""
    import ctypes
    import dis

    calls, injected = [], []
    class Kernel:
        def InitializeProcThreadAttributeList(self, attrs, count, flags, size):
            ctypes.cast(size, ctypes.POINTER(ctypes.c_size_t)).contents.value = 128
            calls.append("initialized" if attrs is not None else "size")
            return attrs is not None
        def DeleteProcThreadAttributeList(self, attrs):
            calls.append("delete")
        def UpdateProcThreadAttribute(self, *args):
            calls.append("update")
            return True
        def CreateProcessW(self, *args):
            calls.append("spawn")
            return True

    target = transport._create_process_in_job.__code__
    previous = sys.gettrace()
    def trace(frame, event, arg):
        if frame.f_code is target:
            frame.f_trace_opcodes = True
            if event == "opcode" and calls == ["size", "initialized"]:
                operation = dis.opname[frame.f_code.co_code[frame.f_lasti]]
                # Old code reaches a NOP before its try; fixed code first stores
                # the known successful result inside the protected region.
                if frame.f_locals.get("initialized") is True or operation == "NOP":
                    injected.append(True)
                    raise error_kind("synthetic interruption")
        return trace
    try:
        sys.settrace(trace)
        with pytest.raises(error_kind):
            transport._create_process_in_job(Kernel(), 12, "fake.exe", "fake.exe", {}, "fake-cwd",
                                             transport._ProcessInfo())
    finally:
        sys.settrace(previous)
    assert injected == [True]
    assert calls == ["size", "initialized", "delete"]


@pytest.mark.parametrize("error_kind", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_unknown_attribute_initialization_retains_storage_and_blocks_retry(
        transport, tmp_path, monkeypatch, error_kind):
    """Interrupt before storing the native return; no real kernel call or child."""
    import ctypes
    import dis
    import threading

    quarantine, calls, injected = [], [], []
    monkeypatch.setattr(transport, "_QUARANTINE", quarantine)
    monkeypatch.setattr(transport, "_RUN_LOCK", threading.Lock())
    class Kernel:
        def InitializeProcThreadAttributeList(self, attrs, count, flags, size):
            ctypes.cast(size, ctypes.POINTER(ctypes.c_size_t)).contents.value = 128
            calls.append("initialized" if attrs is not None else "size")
            return attrs is not None
        def DeleteProcThreadAttributeList(self, attrs): calls.append("delete")
        def UpdateProcThreadAttribute(self, *args): calls.append("update"); return True
        def CreateProcessW(self, *args): calls.append("spawn"); return True
    target = transport._create_process_in_job.__code__
    stores = {i.offset for i in dis.get_instructions(target)
              if i.opname == "STORE_FAST" and i.argval == "initialized"}
    assert stores
    previous = sys.gettrace()
    def trace(frame, event, arg):
        if frame.f_code is target:
            frame.f_trace_opcodes = True
            if event == "opcode" and frame.f_lasti in stores and calls == ["size", "initialized"]:
                injected.append(True)
                raise error_kind("synthetic return-to-store interruption")
        return trace
    try:
        sys.settrace(trace)
        with pytest.raises(error_kind):
            transport._create_process_in_job(Kernel(), 12, "fake.exe", "fake.exe", {}, "fake-cwd",
                                             transport._ProcessInfo())
    finally:
        sys.settrace(previous)
    assert injected == [True] and calls == ["size", "initialized"]
    assert len(quarantine) == 1 and quarantine[0][2][0] == "attribute_init_unknown"
    assert quarantine[0][2][1] is not None  # Keep the caller-owned buffer alive.
    with pytest.raises(transport.TransportCleanupUnknown, match="^secure_transport_cleanup_unknown$") as refused:
        transport._run([sys.executable], str(tmp_path), {"mode": "python-c"}, b"", 1, 1024)
    assert refused.value.prior_outcome == "prior_unknown"


def _run(transport, tmp_path, code, payload=b"", *, timeout=5, output_limit=1024):
    return transport.run_bounded(
        executable=sys.executable,
        arguments=("-I", "-B", "-c", code),
        cwd=str(tmp_path),
        stdin_bytes=payload,
        timeout_seconds=timeout,
        max_output_bytes=output_limit,
    )


def test_synthetic_roundtrip_launches_with_noninheritable_handles(transport, tmp_path, monkeypatch):
    marker = b"WD_SYNTHETIC_STDIN_ONLY_15d8db0ad52f4b38"
    observations = []
    inherited = []
    original = transport._create_process_in_job
    original_api = transport._api

    def traced_api():
        kernel, advapi = original_api()
        original_create = kernel.CreateProcessW
        def traced_native(*args):
            inherited.append(args[4])
            return original_create(*args)
        kernel.CreateProcessW = traced_native
        return kernel, advapi

    def traced(*args, **kwargs):
        command_line = args[3]
        child_env = args[4]
        marker_text = marker.decode("ascii")
        payload_in_command = marker_text in str(command_line)
        payload_in_environment = any(
            marker_text in str(key) or marker_text in str(value)
            for key, value in child_env.items()
        )
        observations.append((payload_in_command, payload_in_environment))
        return original(*args, **kwargs)

    monkeypatch.setattr(transport, "_create_process_in_job", traced)
    monkeypatch.setattr(transport, "_api", traced_api)
    result = _run(transport, tmp_path,
                  "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())", marker)
    assert result == (marker, 0)
    assert observations, "root process creation was not observed in this process"
    assert inherited == [False]
    assert not any(in_args or in_env for in_args, in_env in observations)


def test_nonzero_exit_is_returned_with_synthetic_stdout(transport, tmp_path):
    result = _run(transport, tmp_path,
                  "import sys; sys.stdout.buffer.write(b'ok'); sys.exit(7)")
    assert result == (b"ok", 7)


def test_native_spawn_return_interrupt_terminates_own_synthetic_child(transport, tmp_path, monkeypatch):
    """Observe the child through a test-owned duplicate across the injected interruption."""
    import ctypes
    from ctypes import wintypes
    monkeypatch.setattr(transport, "_QUARANTINE", [])
    duplicated = wintypes.HANDLE()
    original_api = transport._api
    kernel_for_cleanup = []
    def traced_api():
        kernel, advapi = original_api()
        kernel_for_cleanup.append(kernel)
        kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                           ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
                                           wintypes.BOOL, wintypes.DWORD]
        kernel.DuplicateHandle.restype = wintypes.BOOL
        create = kernel.CreateProcessW
        def interrupt_after_create(*args):
            result = create(*args)
            assert result
            info = ctypes.cast(args[-1], ctypes.POINTER(transport._ProcessInfo)).contents
            assert info.process and info.thread
            current = kernel.GetCurrentProcess()
            assert kernel.DuplicateHandle(current, info.process, current, ctypes.byref(duplicated), 0, False, 2)
            raise KeyboardInterrupt("synthetic interruption after native creation")
        kernel.CreateProcessW = interrupt_after_create
        return kernel, advapi
    monkeypatch.setattr(transport, "_api", traced_api)
    try:
        with pytest.raises(transport.TransportCleanupUnknown):
            _run(transport, tmp_path, "print('synthetic')")
        assert duplicated.value
        assert kernel_for_cleanup[0].WaitForSingleObject(duplicated, 5000) == 0
        assert transport._QUARANTINE
    finally:
        if duplicated.value:
            kernel = kernel_for_cleanup[0]
            if kernel.WaitForSingleObject(duplicated, 0) != 0:
                kernel.TerminateProcess(duplicated, 126)
                assert kernel.WaitForSingleObject(duplicated, 5000) == 0
            kernel.CloseHandle(duplicated)


def test_output_above_the_declared_limit_is_refused(transport, tmp_path):
    with pytest.raises(ValueError):
        _run(transport, tmp_path, "import sys; sys.stdout.buffer.write(b'x' * 1025)",
             output_limit=1024)


def test_whole_call_timeout_is_reported_within_a_bounded_interval(transport, tmp_path):
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        _run(transport, tmp_path, "import time; time.sleep(30)", timeout=0.2)
    assert time.monotonic() - started < 10


@pytest.mark.parametrize("failed_operation", ["job", "handle", "job_raises", "handle_raises"])
@pytest.mark.parametrize("prior", ["timeout", "refused"])
def test_run_timeout_with_uncertain_cleanup_reports_unknown_and_blocks_new_work(
        transport, tmp_path, monkeypatch, failed_operation, prior):
    import _winapi

    class Kernel:
        calls = []
        def CreateNamedPipeW(self, *args): return 11
        def CreateJobObjectW(self, *args): return 12
        def SetInformationJobObject(self, *args): return 1
        def GetCurrentProcess(self): return 99
        def AssignProcessToJobObject(self, *args): return 1
        def ResumeThread(self, *args): return 1
        def LocalFree(self, *args): return 0
        def TerminateJobObject(self, *args):
            self.calls.append("terminate")
            if failed_operation == "job_raises":
                raise OSError("synthetic cleanup failure")
            return failed_operation != "job"
        def CloseHandle(self, handle):
            self.calls.append(("close", handle))
            if failed_operation == "handle_raises" and handle == 12:
                raise OSError("synthetic close failure")
            return not (failed_operation == "handle" and handle == 12)

    kernel = Kernel()
    monkeypatch.setattr(transport, "_QUARANTINE", [])
    monkeypatch.setattr(transport, "_api", lambda: (kernel, None))
    monkeypatch.setattr(transport, "_descriptor", lambda *args: 14)
    monkeypatch.setattr(transport, "_noninheritable", lambda *args: None)
    monkeypatch.setattr(transport, "_created", lambda *args: 1)
    monkeypatch.setattr(_winapi, "CreateProcess", lambda *args: (15, 16, 17, 18))
    monkeypatch.setattr(transport, "_create_process_in_job", _mock_spawn_from_winapi)
    def time_out(*args, **kwargs):
        if prior == "timeout":
            raise TimeoutError("synthetic timeout")
        raise ValueError("synthetic sensitive text")
    monkeypatch.setattr(transport, "_io", time_out)
    with pytest.raises(Exception) as raised:
        transport._run([sys.executable], str(tmp_path), {"mode": "python-c"}, b"", 10, 1024)
    assert type(raised.value).__name__ == "TransportCleanupUnknown"
    assert raised.value.prior_outcome == prior
    assert transport._QUARANTINE
    assert ("close", 15) in kernel.calls and ("close", 16) in kernel.calls and ("close", 11) in kernel.calls
    calls_before = list(kernel.calls)
    with pytest.raises(Exception) as retry:
        transport._run([sys.executable], str(tmp_path), {"mode": "python-c"}, b"", 10, 1024)
    assert type(retry.value).__name__ == "TransportCleanupUnknown"
    assert kernel.calls == calls_before  # no second process or cleanup attempt


@pytest.mark.parametrize('scenario', ['active', 'query_failure', 'query_raises', 'unassigned_alive'])
def test_child_cleanup_without_confirmed_exit_is_unknown(transport, tmp_path, monkeypatch, scenario):
    """A successful termination request alone cannot prove that children exited."""
    import _winapi
    class Kernel:
        def CreateNamedPipeW(self, *args): return 11
        def CreateJobObjectW(self, *args): return 12
        def SetInformationJobObject(self, *args): return 1
        def GetCurrentProcess(self): return 99
        def AssignProcessToJobObject(self, *args): return scenario != 'unassigned_alive'
        def ResumeThread(self, *args): return 1
        def LocalFree(self, *args): return 0
        def TerminateJobObject(self, *args): return 1
        def TerminateProcess(self, *args): return 1
        def QueryInformationJobObject(self, job, cls, out, size, returned):
            if scenario == 'query_raises':
                raise OSError('synthetic query failure')
            if scenario == 'query_failure':
                return 0
            import ctypes
            ctypes.cast(out, ctypes.POINTER(transport._JobAccounting)).contents.active_processes = 1
            return 1
        def WaitForSingleObject(self, *args): return 258
        def CloseHandle(self, *args): return 1
    monkeypatch.setattr(transport, '_QUARANTINE', [])
    monkeypatch.setattr(transport, '_api', lambda: (Kernel(), None))
    monkeypatch.setattr(transport, '_descriptor', lambda *args: 14)
    monkeypatch.setattr(transport, '_noninheritable', lambda *args: None)
    monkeypatch.setattr(transport, '_created', lambda *args: 1)
    monkeypatch.setattr(_winapi, 'CreateProcess', lambda *args: (15, 16, 17, 18))
    monkeypatch.setattr(transport, "_create_process_in_job", _mock_spawn_from_winapi)
    monkeypatch.setattr(transport, '_io', lambda *a, **kw: (_ for _ in ()).throw(ValueError()))
    with pytest.raises(transport.TransportCleanupUnknown):
        transport._run([sys.executable], str(tmp_path), {'mode': 'python-c'}, b'', 0.01, 1024)
    assert transport._QUARANTINE


def test_run_timeout_with_successful_cleanup_remains_timeout(transport, tmp_path, monkeypatch):
    import _winapi

    class Kernel:
        def CreateNamedPipeW(self, *args): return 11
        def CreateJobObjectW(self, *args): return 12
        def SetInformationJobObject(self, *args): return 1
        def GetCurrentProcess(self): return 99
        def AssignProcessToJobObject(self, *args): return 1
        def ResumeThread(self, *args): return 1
        def LocalFree(self, *args): return 0
        def TerminateJobObject(self, *args): return 1
        def QueryInformationJobObject(self, *args): return 1  # zero-initialized accounting
        def CloseHandle(self, *args): return 1

    monkeypatch.setattr(transport, "_QUARANTINE", [])
    monkeypatch.setattr(transport, "_api", lambda: (Kernel(), None))
    monkeypatch.setattr(transport, "_descriptor", lambda *args: 14)
    monkeypatch.setattr(transport, "_noninheritable", lambda *args: None)
    monkeypatch.setattr(transport, "_created", lambda *args: 1)
    monkeypatch.setattr(_winapi, "CreateProcess", lambda *args: (15, 16, 17, 18))
    monkeypatch.setattr(transport, "_create_process_in_job", _mock_spawn_from_winapi)
    monkeypatch.setattr(transport, "_io", lambda *a, **kw: (_ for _ in ()).throw(TimeoutError()))
    with pytest.raises(TimeoutError):
        transport._run([sys.executable], str(tmp_path), {"mode": "python-c"}, b"", 10, 1024)
    assert not transport._QUARANTINE


@pytest.mark.parametrize('spawn_failed,prior', [(False,'timeout'),(False,'refused'),(True,'refused')])
def test_confirmed_child_cleanup_precedes_close(transport, tmp_path, monkeypatch, spawn_failed, prior):
    """An uncertain spawn is quarantined even if this invocation's job empties."""
    import _winapi
    calls=[]
    class Kernel:
        def CreateNamedPipeW(self, *args): return 11
        def CreateJobObjectW(self, *args): return 12
        def SetInformationJobObject(self, *args): return 1
        def GetCurrentProcess(self): return 99
        def ResumeThread(self, *args): return 1
        def LocalFree(self, *args): return 0
        def TerminateJobObject(self, h, *args): calls.append(('terminate_job',h)); return 1
        def TerminateProcess(self, h, *args): calls.append(('terminate_child',h)); return 1
        def QueryInformationJobObject(self, h, *args): calls.append(('query_job',h)); return 1
        def WaitForSingleObject(self, h, *args): calls.append(('wait_child',h)); return 0
        def CloseHandle(self, h): calls.append(('close',h)); return 1
    monkeypatch.setattr(transport, '_QUARANTINE', [])
    monkeypatch.setattr(transport, '_api', lambda: (Kernel(), None))
    monkeypatch.setattr(transport, '_descriptor', lambda *args: 14)
    monkeypatch.setattr(transport, '_noninheritable', lambda *args: None)
    monkeypatch.setattr(transport, '_created', lambda *args: 1)
    monkeypatch.setattr(_winapi, 'CreateProcess', lambda *args: (15,16,17,18))
    monkeypatch.setattr(transport, "_create_process_in_job", _mock_spawn_from_winapi)
    if spawn_failed:
        def fail_spawn(*args):
            raise transport._Refused()
        monkeypatch.setattr(transport, '_create_process_in_job', fail_spawn)
    def fail(*args,**kwargs):
        if prior == 'timeout':
            raise TimeoutError()
        raise ValueError()
    monkeypatch.setattr(transport, '_io', fail)
    with pytest.raises(transport.TransportCleanupUnknown if spawn_failed else (TimeoutError if prior == 'timeout' else ValueError)):
        transport._run([sys.executable], str(tmp_path), {'mode':'python-c'}, b'', 10, 1024)
    assert bool(transport._QUARANTINE) is spawn_failed
    assert calls.index(('terminate_job',12)) < calls.index(('query_job',12)) < calls.index(('close',12))
    assert not any(item[0] == 'wait_child' for item in calls)


def test_job_accounting_polls_same_handle_until_confirmed_zero(transport, monkeypatch):
    import ctypes
    counts = iter((2, 1, 0))
    calls = []
    class Kernel:
        def QueryInformationJobObject(self, handle, info_class, out, size, returned):
            calls.append((handle, info_class, size, returned))
            ctypes.cast(out, ctypes.POINTER(transport._JobAccounting)).contents.active_processes = next(counts)
            return 1
    clock = iter((10.0, 10.5))
    monkeypatch.setattr(transport.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(transport.time, 'sleep', lambda delay: None)
    assert transport._wait_job_empty(Kernel(), 37, 11.0)
    assert calls == [(37, 1, ctypes.sizeof(transport._JobAccounting), None)] * 3


def test_job_accounting_deadline_still_allows_one_immediate_zero_probe(transport, monkeypatch):
    class Kernel:
        def QueryInformationJobObject(self, *args): return 1
    monkeypatch.setattr(transport.time, 'monotonic', lambda: 11.0)
    assert transport._wait_job_empty(Kernel(), 37, 10.0)


@pytest.mark.parametrize('allocation', ['overlapped', 'buffer', 'counter'])
@pytest.mark.parametrize('close_ok', [True, False])
def test_io_event_owned_when_early_allocation_fails(transport, monkeypatch, allocation, close_ok):
    calls=[]
    class Kernel:
        def CreateEventW(self, *args): return 21
        def CloseHandle(self, handle): calls.append(handle); return close_ok
    monkeypatch.setattr(transport, '_QUARANTINE', [])
    def fail(*args, **kwargs): raise MemoryError('synthetic allocation')
    if allocation == 'overlapped':
        monkeypatch.setattr(transport, '_OV', fail)
    elif allocation == 'buffer':
        monkeypatch.setattr(transport.C, 'create_string_buffer', fail)
    else:
        monkeypatch.setattr(transport.W, 'DWORD', fail)
    expected = MemoryError if close_ok else transport.TransportCleanupUnknown
    with pytest.raises(expected):
        transport._io(Kernel(), 11, None, 1, transport.time.monotonic() + 10)
    assert calls == [21]
    assert bool(transport._QUARANTINE) is not close_ok


@pytest.mark.parametrize('operation', ['connect', 'read', 'write'])
def test_io_interrupted_during_syscall_preserves_possible_pending_state(transport, monkeypatch, operation):
    calls=[]
    class Kernel:
        def CreateEventW(self, *args): return 21
        def ConnectNamedPipe(self, *args): raise KeyboardInterrupt('synthetic')
        def ReadFile(self, *args): raise KeyboardInterrupt('synthetic')
        def WriteFile(self, *args): raise KeyboardInterrupt('synthetic')
        def CancelIoEx(self, *args): calls.append('cancel'); return 0
        def CloseHandle(self, *args): calls.append('close'); return 1
    monkeypatch.setattr(transport, '_QUARANTINE', [])
    monkeypatch.setattr(transport, '_noninheritable', lambda *args: None)
    caught = None
    try:
        transport._io(Kernel(), 11, b'x' if operation == 'write' else None, 1,
                      transport.time.monotonic() + 10, connect=operation == 'connect', write=operation == 'write')
    except BaseException as exc:
        caught = exc
    assert isinstance(caught, transport.TransportCleanupUnknown)
    assert calls == ['cancel']
    assert any(item[1] == 11 and item[4] == 21 and isinstance(item[5], transport.W.DWORD)
               for item in transport._QUARANTINE)


@pytest.mark.parametrize('operation', ['connect', 'read', 'write'])
@pytest.mark.parametrize('error', [5, 6, 109])
def test_io_immediate_documented_failure_does_not_claim_pending(transport, monkeypatch, operation, error):
    import ctypes
    calls=[]
    class Kernel:
        def CreateEventW(self, *args): return 21
        def ConnectNamedPipe(self, *args): ctypes.set_last_error(error); return 0
        def ReadFile(self, *args): ctypes.set_last_error(error); return 0
        def WriteFile(self, *args): ctypes.set_last_error(error); return 0
        def CancelIoEx(self, *args): calls.append('cancel'); return 0
        def CloseHandle(self, *args): calls.append('close'); return 1
    monkeypatch.setattr(transport, '_QUARANTINE', [])
    monkeypatch.setattr(transport, '_noninheritable', lambda *args: None)
    with pytest.raises(ValueError):
        transport._io(Kernel(), 11, b'x' if operation == 'write' else None, 1,
                      transport.time.monotonic() + 10, connect=operation == 'connect', write=operation == 'write')
    assert calls == ['close']
    assert not transport._QUARANTINE


def test_signaled_pending_read_broken_pipe_refuses_frame_without_quarantine(transport, monkeypatch):
    import ctypes
    calls=[]
    class Kernel:
        def CreateEventW(self, *args): return 21
        def ReadFile(self, *args): ctypes.set_last_error(997); return 0
        def WaitForSingleObject(self, *args): calls.append('wait'); return 0
        def GetOverlappedResult(self, *args): ctypes.set_last_error(109); return 0
        def CancelIoEx(self, *args): calls.append('cancel'); return 0
        def CloseHandle(self, *args): calls.append('close'); return 1
    monkeypatch.setattr(transport, '_QUARANTINE', [])
    monkeypatch.setattr(transport, '_noninheritable', lambda *args: None)
    with pytest.raises(ValueError):
        transport._io(Kernel(), 11, None, 1, transport.time.monotonic() + 10)
    assert calls == ['wait','close']
    assert not transport._QUARANTINE


@pytest.mark.parametrize('failed', ['return_false', 'raise'])
def test_peer_process_close_uncertainty_is_quarantined(transport, monkeypatch, failed):
    import ctypes
    calls=[]
    class Kernel:
        def GetNamedPipeClientProcessId(self, pipe, out):
            ctypes.cast(out, ctypes.POINTER(transport.W.DWORD)).contents.value=17
            return 1
        def OpenProcess(self, *args): return 15
        def CloseHandle(self, h):
            calls.append(h)
            if failed == 'raise': raise OSError('synthetic close failure')
            return 0
    monkeypatch.setattr(transport, '_QUARANTINE', [])
    monkeypatch.setattr(transport, '_noninheritable', lambda *a: None)
    monkeypatch.setattr(transport, '_active', lambda *a: None)
    monkeypatch.setattr(transport, '_created', lambda *a: 1)
    with pytest.raises(transport.TransportCleanupUnknown):
        transport._peer(Kernel(), 11, 17, 1, client=True)
    assert calls == [15]
    assert transport._QUARANTINE


def test_descriptor_token_close_uncertainty_overrides_refusal(transport, monkeypatch):
    import ctypes
    calls=[]
    class Kernel:
        def GetCurrentProcess(self): return 99
        def CloseHandle(self, h): calls.append('close'); return 0
    class Advapi:
        def OpenProcessToken(self, current, access, out):
            ctypes.cast(out, ctypes.POINTER(transport.W.HANDLE)).contents.value = 15
            return 1
    monkeypatch.setattr(transport, '_QUARANTINE', [])
    monkeypatch.setattr(transport, '_noninheritable', lambda *a: (_ for _ in ()).throw(ValueError()))
    with pytest.raises(transport.TransportCleanupUnknown):
        transport._descriptor(Kernel(), Advapi())
    assert calls == ['close'] and transport._QUARANTINE


def test_descriptor_localfree_uncertainty_blocks_dispatch(transport, tmp_path, monkeypatch):
    calls=[]
    class Kernel:
        def CreateNamedPipeW(self, *args): calls.append('pipe'); return 11
        def LocalFree(self, ptr): calls.append('localfree'); return ptr
        def CloseHandle(self, h): calls.append('close'); return 1
    monkeypatch.setattr(transport, '_QUARANTINE', [])
    monkeypatch.setattr(transport, '_api', lambda: (Kernel(), None))
    monkeypatch.setattr(transport, '_descriptor', lambda *a: 14)
    with pytest.raises(transport.TransportCleanupUnknown):
        transport._run([sys.executable], str(tmp_path), {'mode':'python-c'}, b'', 10, 1024)
    assert calls.count('localfree') == 1 and 'pipe' in calls and transport._QUARANTINE


@pytest.mark.parametrize('scenario', ['success', 'text_free', 'token_close', 'descriptor_free'])
def test_descriptor_releases_owned_allocations_once(transport, monkeypatch, scenario):
    import ctypes
    calls=[]
    sid=ctypes.create_unicode_buffer('S-1-5-5-1-2')
    sid_address=ctypes.addressof(sid)
    class Kernel:
        def GetCurrentProcess(self): return 99
        def LocalFree(self, value):
            address=value.value if hasattr(value, 'value') else value
            calls.append(('free', address))
            return address if (scenario == 'text_free' and address == sid_address) or (
                scenario == 'descriptor_free' and address == 44) else 0
        def CloseHandle(self, value):
            calls.append(('close', value.value if hasattr(value, 'value') else value))
            return scenario not in ('token_close', 'descriptor_free')
    class Advapi:
        def OpenProcessToken(self, current, access, out):
            ctypes.cast(out, ctypes.POINTER(transport.W.HANDLE)).contents.value=15
            return 1
        def GetTokenInformation(self, token, kind, buffer, size, out):
            ctypes.cast(out, ctypes.POINTER(transport.W.DWORD)).contents.value=ctypes.sizeof(transport._Groups)
            if buffer is None: return 0
            groups=ctypes.cast(buffer, ctypes.POINTER(transport._Groups)).contents
            groups.count=1
            groups.groups[0].attributes=0xC0000000
            groups.groups[0].sid=55
            return 1
        def ConvertSidToStringSidW(self, value, out):
            ctypes.cast(out, ctypes.POINTER(ctypes.c_void_p)).contents.value=sid_address
            return 1
        def ConvertStringSecurityDescriptorToSecurityDescriptorW(self, *args):
            ctypes.cast(args[2], ctypes.POINTER(ctypes.c_void_p)).contents.value=44
            return 1
    monkeypatch.setattr(transport, '_QUARANTINE', [])
    monkeypatch.setattr(transport, '_noninheritable', lambda *a: None)
    if scenario == 'success':
        assert transport._descriptor(Kernel(), Advapi()).value == 44
        assert calls == [('free',sid_address),('close',15)]
        assert not transport._QUARANTINE
    else:
        with pytest.raises(transport.TransportCleanupUnknown):
            transport._descriptor(Kernel(), Advapi())
        assert calls == [('free',sid_address),('close',15),('free',44)]
        assert transport._QUARANTINE


@pytest.mark.parametrize('error', [6, 5, 109, 996, 1234])
def test_pending_overlapped_unknown_error_is_cancelled_or_quarantined(transport, monkeypatch, error):
    import ctypes
    calls = []
    class Kernel:
        def CreateEventW(self, *args): return 21
        def ConnectNamedPipe(self, *args): ctypes.set_last_error(997); return 0
        def WaitForSingleObject(self, *args): return 0
        def GetOverlappedResult(self, *args): ctypes.set_last_error(error); return 0
        def CancelIoEx(self, *args): calls.append('cancel'); return 0
        def CloseHandle(self, *args): calls.append('close'); return 1
    monkeypatch.setattr(transport, '_QUARANTINE', [])
    monkeypatch.setattr(transport, '_noninheritable', lambda *a: None)
    with pytest.raises(transport.TransportCleanupUnknown):
        transport._io(Kernel(), 11, None, 0, transport.time.monotonic() + 10, connect=True)
    assert calls == ['cancel']
    assert any(item[1] == 11 and item[4] == 21 for item in transport._QUARANTINE)


@pytest.mark.parametrize('cancel_completion', ['success', 'aborted'])
def test_pending_unknown_error_may_release_only_after_cancel_completion(transport, monkeypatch, cancel_completion):
    import ctypes
    calls=[]
    class Kernel:
        def CreateEventW(self, *args): return 21
        def ConnectNamedPipe(self, *args): ctypes.set_last_error(997); return 0
        def WaitForSingleObject(self, *args): calls.append('wait'); return 0
        def GetOverlappedResult(self, *args):
            calls.append('result')
            if calls.count('result') == 1:
                ctypes.set_last_error(6)
                return 0
            if cancel_completion == 'aborted':
                ctypes.set_last_error(995)
                return 0
            return 1
        def CancelIoEx(self, *args): calls.append('cancel'); return 1
        def CloseHandle(self, *args): calls.append('close'); return 1
    monkeypatch.setattr(transport, '_QUARANTINE', [])
    monkeypatch.setattr(transport, '_noninheritable', lambda *a: None)
    with pytest.raises(ValueError):
        transport._io(Kernel(), 11, None, 0, transport.time.monotonic() + 10, connect=True)
    assert calls == ['wait', 'result', 'cancel', 'wait', 'result', 'close']
    assert not transport._QUARANTINE


@pytest.mark.parametrize('error,eof', [(995, False), (109, True)])
def test_pending_overlapped_documented_terminal_error_does_not_quarantine(transport, monkeypatch, error, eof):
    import ctypes
    calls = []
    class Kernel:
        def CreateEventW(self, *args): return 21
        def ReadFile(self, *args): ctypes.set_last_error(997); return 0
        def WaitForSingleObject(self, *args): return 0
        def GetOverlappedResult(self, *args): ctypes.set_last_error(error); return 0
        def CancelIoEx(self, *args): calls.append('cancel'); return 0
        def CloseHandle(self, *args): calls.append('close'); return 1
    monkeypatch.setattr(transport, '_QUARANTINE', [])
    monkeypatch.setattr(transport, '_noninheritable', lambda *a: None)
    if eof:
        assert transport._io(Kernel(), 11, None, 1, transport.time.monotonic() + 10, eof=True) == b''
    else:
        with pytest.raises(ValueError):
            transport._io(Kernel(), 11, None, 1, transport.time.monotonic() + 10)
    assert calls == ['close']
    assert not transport._QUARANTINE


def test_fixed_peer_propagates_sanitized_cleanup_unknown(transport, monkeypatch):
    def impl(*args, **kwargs):
        try:
            raise TimeoutError("private synthetic text")
        except TimeoutError:
            raise transport.TransportCleanupUnknown("timeout")
    monkeypatch.setattr(transport, "_run_fixed_peer_impl", impl)
    with pytest.raises(transport.TransportCleanupUnknown) as caught:
        transport.run_fixed_peer(executable="x", expected_interpreter_sha256="x", interpreter_max_bytes=1,
                                 plan_bytes=b"", expected_plan_sha256="x", bundle_bytes=b"",
                                 runtime_manifest_bytes=b"", query_bytes=b"", cwd="x", timeout_seconds=1,
                                 max_output_bytes=1)
    assert caught.value.prior_outcome == "timeout"
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert "private synthetic text" not in str(caught.value)


def test_ready_exchange_preserves_timeout_without_payload(transport, monkeypatch):
    monkeypatch.setattr(transport, "_peer", lambda *a, **kw: None)
    monkeypatch.setattr(transport, "_frame_write",
                        lambda *a, **kw: (_ for _ in ()).throw(TimeoutError("synthetic private")))
    with pytest.raises(TimeoutError, match="^secure_transport_timeout$"):
        transport._fixed_ready_exchange(None, None, 1, 1,
            {"schema": "wd.q4-fixed-control.v1", "nonce": "a" * 32, "binding_sha256": "b" * 64}, b"x", 1)


def test_fixed_exchange_preserves_timeout_without_payload(transport, monkeypatch):
    monkeypatch.setattr(transport, "_peer", lambda *a, **kw: None)
    monkeypatch.setattr(transport, "_frame_write", lambda *a, **kw: None)
    monkeypatch.setattr(transport, "_sha", lambda *a: "b" * 64)
    monkeypatch.setattr(transport, "_fixed_plan", lambda *a: {"bundle_bytes": 1, "bundle_sha256": "b" * 64})
    monkeypatch.setattr(transport, "_fixed_ready_exchange",
                        lambda *a, **kw: (_ for _ in ()).throw(TimeoutError("synthetic private")))
    with pytest.raises(TimeoutError, match="^secure_transport_timeout$"):
        transport._fixed_exchange(None, None, 1, 1,
            {"schema": "wd.q4-fixed-control.v1", "nonce": "a" * 32, "binding_sha256": "b" * 64},
            b"p", b"b", b"q", 1)


@pytest.mark.parametrize("scenario", ["success_close_fail", "timeout_close_fail", "timeout_close_raises",
                                     "timeout_cancel_raises", "timeout_cancel_false", "timeout_wait_raises",
                                     "timeout_completion_incomplete", "timeout_completion_raises"])
def test_io_cleanup_unknown_retains_unsafe_state_and_stops_new_runs(transport, tmp_path, monkeypatch, scenario):
    import ctypes
    import _winapi

    class Kernel:
        calls = []
        waits = 0
        def CreateNamedPipeW(self, *args): return 11
        def CreateJobObjectW(self, *args): return 12
        def SetInformationJobObject(self, *args): return 1
        def GetCurrentProcess(self): return 99
        def AssignProcessToJobObject(self, *args): return 1
        def ResumeThread(self, *args): return 1
        def LocalFree(self, *args): return 0
        def TerminateJobObject(self, *args): return 1
        def CreateEventW(self, *args): return 21
        def ConnectNamedPipe(self, *args):
            if scenario != "success_close_fail":
                ctypes.set_last_error(997)
                return 0
            return 1
        def WaitForSingleObject(self, *args):
            self.waits += 1
            if self.waits > 1 and scenario == "timeout_wait_raises":
                raise OSError("synthetic wait failure")
            return 258 if self.waits == 1 else 0
        def CancelIoEx(self, *args):
            if scenario == "timeout_cancel_raises":
                raise OSError("synthetic cancel failure")
            if scenario == "timeout_cancel_false":
                return 0
            return 1
        def GetOverlappedResult(self, *args):
            if scenario == "timeout_completion_raises":
                raise OSError("synthetic completion failure")
            if scenario != "success_close_fail":
                ctypes.set_last_error(996 if scenario == "timeout_completion_incomplete" else 995)
                return 0
            return 1
        def CloseHandle(self, handle):
            self.calls.append(handle)
            if handle == 21 and scenario == "timeout_close_raises":
                raise OSError("synthetic close failure")
            return handle != 21

    kernel = Kernel()
    monkeypatch.setattr(transport, "_QUARANTINE", [])
    monkeypatch.setattr(transport, "_api", lambda: (kernel, None))
    monkeypatch.setattr(transport, "_descriptor", lambda *args: 14)
    monkeypatch.setattr(transport, "_noninheritable", lambda *args: None)
    monkeypatch.setattr(transport, "_created", lambda *args: 1)
    monkeypatch.setattr(_winapi, "CreateProcess", lambda *args: (15, 16, 17, 18))
    monkeypatch.setattr(transport, "_create_process_in_job", _mock_spawn_from_winapi)
    with pytest.raises(transport.TransportCleanupUnknown) as caught:
        transport._run([sys.executable], str(tmp_path), {"mode": "python-c"}, b"", 10, 1024)
    assert caught.value.prior_outcome == ("none" if scenario == "success_close_fail" else "timeout")
    assert transport._QUARANTINE
    assert all(kernel.calls.count(h) <= 1 for h in (11, 12, 15, 16, 21))
    if scenario in ("timeout_cancel_raises", "timeout_cancel_false", "timeout_wait_raises",
                    "timeout_completion_incomplete", "timeout_completion_raises"):
        assert 21 not in kernel.calls and 11 not in kernel.calls
        assert any(entry[1] == 11 and entry[4] == 21 for entry in transport._QUARANTINE)
    else:
        assert kernel.calls.count(21) == 1 and kernel.calls.count(11) == 1
    with pytest.raises(transport.TransportCleanupUnknown):
        transport._run([sys.executable], str(tmp_path), {"mode": "python-c"}, b"", 10, 1024)
