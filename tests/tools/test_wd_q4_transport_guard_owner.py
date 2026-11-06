"""Transport-owner wiring tests with a synthetic kernel, not production proof."""
import hashlib
import json
import os
import struct
import sys

import pytest

from tools import wd_q4_guard_completion as completion
from tools import wd_q4_secure_transport as transport


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows kernel seam")


@pytest.fixture
def guarded(tmp_path):
    runtime = tmp_path / "runtime"
    state = runtime / "shared" / ".codex-audit" / "codex-tools-1" / "q4-caller-state"
    state.mkdir(parents=True)
    raw = json.dumps({"runtime_root": str(runtime)}).encode("ascii")
    guard = completion.acquire_guard(fleet_bytes=raw, expected_fleet_sha256=hashlib.sha256(raw).hexdigest(),
        lane="codex-tools-1", identity_sha256="a" * 64, descriptor_sha256="b" * 64)
    return guard, state / "codex-tools-1.pending.json"


@pytest.mark.parametrize("failure", [None, "normal", "refused", "unexpected", "terminate", "job_active", "close", "interrupt", "complete_error", "wait_timeout", "wait_failed", "wait_error"])
def test_transport_contributes_only_after_own_confirmed_cleanup(guarded, tmp_path, monkeypatch, failure):
    guard, marker = guarded
    before = marker.read_bytes()
    trace = []

    class Kernel:
        def CreateNamedPipeW(self, *args): return 11
        def CreateJobObjectW(self, *args): return 12
        def SetInformationJobObject(self, *args): return 1
        def GetCurrentProcess(self): return 99
        def ResumeThread(self, *args): return 1
        def LocalFree(self, *args): return 0
        def TerminateJobObject(self, *args):
            trace.append("terminate")
            return failure != "terminate"
        def QueryInformationJobObject(self, job, cls, out, size, returned):
            import ctypes
            trace.append("job_check")
            ctypes.cast(out, ctypes.POINTER(transport._JobAccounting)).contents.active_processes = int(failure == "job_active")
            return 1
        def CloseHandle(self, handle):
            trace.append(("close", int(handle)))
            return not (failure == "close" and int(handle) == 12)
        def WaitForSingleObject(self, *args):
            trace.append("process_wait")
            if failure == "wait_error":
                raise OSError("synthetic wait error")
            return 258 if failure == "wait_timeout" else 0xFFFFFFFF if failure == "wait_failed" else 0
        def GetExitCodeProcess(self, handle, out):
            import ctypes
            ctypes.cast(out, ctypes.POINTER(transport.W.DWORD)).contents.value = 0
            return 1

    monkeypatch.setattr(transport, "_QUARANTINE", [])
    monkeypatch.setattr(transport, "_api", lambda: (Kernel(), None))
    monkeypatch.setattr(transport, "_descriptor", lambda *args: 14)
    monkeypatch.setattr(transport, "_noninheritable", lambda *args: None)
    monkeypatch.setattr(transport, "_created", lambda *args: 1)
    monkeypatch.setattr(transport, "_create_process_in_job", lambda *args: (15, 16, 17, 18))
    def stop(*args, **kwargs):
        if failure == "interrupt":
            raise KeyboardInterrupt()
        if failure == "refused":
            raise transport._Refused()
        if failure == "unexpected":
            raise RuntimeError("synthetic unexpected error")
        raise TimeoutError()
    if failure == "normal":
        monkeypatch.setattr(transport, "_io", lambda *args, **kwargs: b"")
        monkeypatch.setattr(transport, "_peer", lambda *args, **kwargs: None)
        monkeypatch.setattr(transport, "_frame_write", lambda *args, **kwargs: None)
        monkeypatch.setattr(transport, "_frame_read", lambda *args, **kwargs: b"synthetic")
        reads = iter((b"\x01", struct.pack(">i", 0), b"END!"))
        monkeypatch.setattr(transport, "_read", lambda *args, **kwargs: next(reads))
    else:
        monkeypatch.setattr(transport, "_io", stop)
    owner = guard._transport_owner()
    old_complete = guard._registry._complete_owner
    def observe(capability):
        assert not transport._RUN_LOCK.locked()
        trace.append("owner")
        if failure == "complete_error":
            raise ValueError("synthetic completion error")
        return old_complete(capability)
    monkeypatch.setattr(guard._registry, "_complete_owner", observe)
    if failure == "normal":
        assert transport._run([sys.executable], str(tmp_path), {"mode": "python-c"}, b"", 0.05, 1024,
                              transport_owner=owner) == (b"synthetic", 0)
    else:
        expected = (KeyboardInterrupt if failure == "interrupt" else TimeoutError if failure is None
                    else transport._Refused if failure == "refused" else transport.TransportCleanupUnknown)
        with pytest.raises(expected) as raised:
            transport._run([sys.executable], str(tmp_path), {"mode": "python-c"}, b"", 0.05, 1024,
                           transport_owner=owner)
        if failure == "complete_error":
            assert raised.value.prior_outcome == "timeout"
    assert marker.read_bytes() == before
    if failure in (None, "normal", "refused"):
        assert guard._registry._done == {"transport"}
        last_wait = max(index for index, event in enumerate(trace) if event == "process_wait")
        assert trace.index("job_check") < last_wait < trace.index(("close", 12)) < trace.index("owner")
        with pytest.raises(completion.GuardCompletionUnavailable):
            guard._registry._issue()  # Other owners are still absent.
        with pytest.raises(completion.GuardCompletionUnavailable):
            owner._begin()  # A DONE-owner ticket cannot be reused.
    else:
        assert "transport" not in guard._registry._done
        assert guard._registry._uncertain
        if failure != "complete_error":
            assert "owner" not in trace


def test_forged_owner_is_rejected_without_native_work(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(transport, "_api", lambda: called.append(True))
    with pytest.raises((ValueError, TypeError, transport._Refused)):
        transport._run([sys.executable], str(tmp_path), {"mode": "python-c"}, b"", 1, 1024,
                       transport_owner={"cleanup": "chain_confirmed"})
    assert not called


@pytest.mark.parametrize("with_owner", [False, True], ids=["legacy", "guarded"])
def test_empty_job_with_unsignaled_process_is_cleanup_unknown(guarded, tmp_path, monkeypatch, with_owner):
    guard, marker = guarded
    class Kernel:
        def CreateNamedPipeW(self, *args): return 11
        def CreateJobObjectW(self, *args): return 12
        def SetInformationJobObject(self, *args): return 1
        def GetCurrentProcess(self): return 99
        def ResumeThread(self, *args): return 1
        def LocalFree(self, *args): return 0
        def TerminateJobObject(self, *args): return 1
        def QueryInformationJobObject(self, *args): return 1  # zero active processes
        def WaitForSingleObject(self, *args): return 258  # WAIT_TIMEOUT
        def CloseHandle(self, *args): return 1
    monkeypatch.setattr(transport, "_QUARANTINE", [])
    monkeypatch.setattr(transport, "_api", lambda: (Kernel(), None))
    monkeypatch.setattr(transport, "_descriptor", lambda *args: 14)
    monkeypatch.setattr(transport, "_noninheritable", lambda *args: None)
    monkeypatch.setattr(transport, "_created", lambda *args: 1)
    monkeypatch.setattr(transport, "_create_process_in_job", lambda *args: (15, 16, 17, 18))
    monkeypatch.setattr(transport, "_io", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError()))
    owner = guard._transport_owner() if with_owner else None
    with pytest.raises(transport.TransportCleanupUnknown):
        transport._run([sys.executable], str(tmp_path), {"mode": "python-c"}, b"", 1, 1024,
                       transport_owner=owner)
    assert marker.exists()
    assert transport._QUARANTINE
    if with_owner:
        assert guard._registry._uncertain and "transport" not in guard._registry._done


def test_unknown_after_ticket_issue_refuses_before_native_work(guarded, tmp_path, monkeypatch):
    guard, marker = guarded
    before = marker.read_bytes()
    owner = guard._transport_owner()
    guard.mark_unknown()  # Another owner reports uncertainty before this transport starts.
    called = []
    monkeypatch.setattr(transport, "_api", lambda: called.append(True))
    with pytest.raises(completion.GuardCompletionUnavailable):
        transport._run([sys.executable], str(tmp_path), {"mode": "python-c"}, b"", 1, 1024,
                       transport_owner=owner)
    assert not called
    assert not guard._registry._done
    assert marker.read_bytes() == before


def test_owner_is_single_use_and_unknown_cannot_be_reissued(guarded, tmp_path, monkeypatch):
    guard, marker = guarded
    before = marker.read_bytes()
    owner = guard._transport_owner()
    with pytest.raises(completion.GuardCompletionUnavailable):
        guard._transport_owner()
    monkeypatch.setattr(transport, "_api", lambda: (_ for _ in ()).throw(RuntimeError()))
    with pytest.raises(transport.TransportCleanupUnknown):
        transport._run([sys.executable], str(tmp_path), {"mode": "python-c"}, b"", 1, 1024,
                       transport_owner=owner)
    assert guard._registry._uncertain and not guard._registry._done
    assert marker.read_bytes() == before
    with pytest.raises(completion.GuardCompletionUnavailable):
        guard._registry._issue()
    with pytest.raises(completion.GuardCompletionUnavailable):
        transport._run([sys.executable], str(tmp_path), {"mode": "python-c"}, b"", 1, 1024,
                       transport_owner=owner)


def test_past_cleanup_unknown_cannot_become_a_transport_contribution(guarded, tmp_path, monkeypatch):
    guard, marker = guarded
    before = marker.read_bytes()
    monkeypatch.setattr(transport, "_QUARANTINE", [])
    class Kernel:
        def CreateNamedPipeW(self, *args): return 11
        def CloseHandle(self, *args): return 1
    monkeypatch.setattr(transport, "_api", lambda: (Kernel(), None))
    monkeypatch.setattr(transport, "_descriptor", lambda *args: 14)
    monkeypatch.setattr(transport, "_release_once", lambda *args, **kwargs: False)
    with pytest.raises(transport.TransportCleanupUnknown):
        transport._run([sys.executable], str(tmp_path), {"mode": "python-c"}, b"", 1, 1024,
                       transport_owner=guard._transport_owner())
    assert guard._registry._uncertain and not guard._registry._done
    assert marker.read_bytes() == before


def test_refused_transport_does_not_retain_query_or_metadata_in_own_traceback(tmp_path):
    query = b"private-query-traceback-sentinel"
    metadata = {"mode": "fixed"}  # Invalid without fixed inputs: refusal precedes native work.
    with pytest.raises(transport._Refused) as raised:
        transport._run([sys.executable], str(tmp_path), metadata, query, 1, 1024)
    tb = raised.value.__traceback__
    frames = []
    while tb is not None:
        if tb.tb_frame.f_code.co_name == "_run":
            frames.append(tb.tb_frame)
        tb = tb.tb_next
    assert frames
    assert all(value is not query and value is not metadata
               for frame in frames for value in frame.f_locals.values())


@pytest.mark.parametrize("outcome", ["pre_api", "timeout", "post_refused", "success"])
def test_run_clears_input_locals_on_all_exit_paths(tmp_path, monkeypatch, outcome):
    """Inspect only _run's returning frame; nested frames need independent review."""
    query = b"synthetic-run-sensitive-query"
    fixed = ({"nonce": "synthetic"}, b"synthetic-plan", b"synthetic-bundle") if outcome != "success" else None
    metadata = {"mode": "fixed" if fixed else "python-c", "canary": "synthetic-metadata"}
    class Kernel:
        def CreateNamedPipeW(self, *args): return 11
        def CreateJobObjectW(self, *args): return 12
        def SetInformationJobObject(self, *args): return 1
        def GetCurrentProcess(self): return 99
        def ResumeThread(self, *args): return 1
        def LocalFree(self, *args): return 0
        def TerminateJobObject(self, *args): return 1
        def QueryInformationJobObject(self, *args): return 1
        def CloseHandle(self, *args): return 1
        def WaitForSingleObject(self, *args): return 0
        def GetExitCodeProcess(self, handle, out):
            import ctypes
            ctypes.cast(out, ctypes.POINTER(transport.W.DWORD)).contents.value = 0
            return 1
    def api():
        if outcome == "pre_api":
            raise transport._Refused()
        return Kernel(), None
    def send_or_fail(*args, **kwargs):
        if outcome == "timeout":
            raise TimeoutError()
        if outcome == "post_refused":
            raise transport._Refused()
        return b""
    monkeypatch.setattr(transport, "_QUARANTINE", [])
    monkeypatch.setattr(transport, "_api", api)
    monkeypatch.setattr(transport, "_descriptor", lambda *args: 14)
    monkeypatch.setattr(transport, "_noninheritable", lambda *args: None)
    monkeypatch.setattr(transport, "_created", lambda *args: 1)
    monkeypatch.setattr(transport, "_create_process_in_job", lambda *args: (15, 16, 17, 18))
    monkeypatch.setattr(transport, "_io", send_or_fail)
    monkeypatch.setattr(transport, "_peer", lambda *args, **kwargs: None)
    monkeypatch.setattr(transport, "_frame_write", lambda *args, **kwargs: None)
    monkeypatch.setattr(transport, "_frame_read", lambda *args, **kwargs: b"synthetic")
    reads = iter((b"\x01", struct.pack(">i", 0), b"END!"))
    monkeypatch.setattr(transport, "_read", lambda *args, **kwargs: next(reads))
    seen = []
    previous = sys.gettrace()
    def trace(frame, event, arg):
        if frame.f_code is transport._run.__code__ and event == "return":
            seen.append(dict(frame.f_locals))
        return trace
    try:
        sys.settrace(trace)
        if outcome == "success":
            assert transport._run([sys.executable], str(tmp_path), metadata, query, 1, 1024, fixed=fixed) == (b"synthetic", 0)
        else:
            expected = TimeoutError if outcome == "timeout" else transport._Refused
            with pytest.raises(expected):
                transport._run([sys.executable], str(tmp_path), metadata, query, 1, 1024, fixed=fixed)
    finally:
        sys.settrace(previous)
    assert len(seen) == 1
    own = seen[0]
    assert not {"stdin_bytes", "metadata", "fixed", "raw_meta"} & set(own)
    assert all(value is not query and value is not metadata and (fixed is None or value is not fixed)
               for value in own.values())


def test_owner_unknown_failure_clears_input_locals_before_unwinding(guarded, tmp_path, monkeypatch):
    guard, _marker = guarded
    owner = guard._transport_owner()
    query = b"synthetic-unknown-failure-query"
    metadata = {"mode": "invalid", "canary": "synthetic"}
    def fail_unknown():
        raise RuntimeError("synthetic unknown failure")
    monkeypatch.setattr(owner._registry, "_unknown", fail_unknown)
    with pytest.raises(RuntimeError) as raised:
        transport._run([sys.executable], str(tmp_path), metadata, query, 1, 1024,
                       transport_owner=owner)
    tb = raised.value.__traceback__
    frames = []
    while tb is not None:
        if tb.tb_frame.f_code.co_name == "_run":
            frames.append(tb.tb_frame)
        tb = tb.tb_next
    assert frames
    assert all("stdin_bytes" not in frame.f_locals and "metadata" not in frame.f_locals
               and "fixed" not in frame.f_locals for frame in frames)


@pytest.mark.parametrize("failure", ["timeout", "refused", "cleanup_unknown"])
def test_no_query_plan_bundle_in_any_callee_exception_chain(guarded, tmp_path, monkeypatch, failure):
    """Exercise real _fixed_exchange/_frame_write; only the harness frame may own canaries."""
    guard, marker = guarded
    query, plan, bundle = b"SYNTHETIC_QUERY_CHAIN", b"SYNTHETIC_PLAN_CHAIN", b"SYNTHETIC_BUNDLE_CHAIN"
    control = {"schema": "wd.q4-fixed-control.v1", "nonce": "a" * 32,
               "binding_sha256": hashlib.sha256(plan).hexdigest()}
    fixed = (control, plan, bundle)
    class Kernel:
        def CreateNamedPipeW(self, *args): return 11
        def CreateJobObjectW(self, *args): return 12
        def SetInformationJobObject(self, *args): return 1
        def GetCurrentProcess(self): return 99
        def ResumeThread(self, *args): return 1
        def LocalFree(self, *args): return 0
        def TerminateJobObject(self, *args): return 1
        def QueryInformationJobObject(self, *args): return 1
        def WaitForSingleObject(self, *args): return 0
        def CloseHandle(self, *args): return 1
    monkeypatch.setattr(transport, "_QUARANTINE", [])
    monkeypatch.setattr(transport, "_api", lambda: (Kernel(), None))
    monkeypatch.setattr(transport, "_descriptor", lambda *args: 14)
    monkeypatch.setattr(transport, "_noninheritable", lambda *args: None)
    monkeypatch.setattr(transport, "_created", lambda *args: 1)
    monkeypatch.setattr(transport, "_create_process_in_job", lambda *args: (15, 16, 17, 18))
    monkeypatch.setattr(transport, "_peer", lambda *args, **kwargs: None)
    monkeypatch.setattr(transport, "_fixed_plan", lambda raw: {
        "bundle_bytes": len(bundle), "bundle_sha256": hashlib.sha256(bundle).hexdigest()})
    def ready(k, pipe, pid, created, control_arg, query_arg, deadline, before_query=None):
        transport._frame_write(k, pipe, query_arg, deadline)
    monkeypatch.setattr(transport, "_fixed_ready_exchange", ready)
    def io(k, pipe, data, count, deadline, **kwargs):
        if data is query:
            if failure == "timeout":
                raise TimeoutError()
            if failure == "cleanup_unknown":
                raise transport.TransportCleanupUnknown("timeout")
            raise transport._Refused()
        return b""
    monkeypatch.setattr(transport, "_io", io)
    expected = (TimeoutError if failure == "timeout" else transport.TransportCleanupUnknown
                if failure == "cleanup_unknown" else transport._Refused)
    with pytest.raises(expected) as raised:
        transport._run([sys.executable], str(tmp_path), {"mode": "fixed"}, query, 1, 1024,
                       fixed=fixed, transport_owner=guard._transport_owner())
    assert marker.exists()
    seen, todo, frames = set(), [raised.value], []
    while todo:
        exc = todo.pop()
        if exc is None or id(exc) in seen:
            continue
        seen.add(id(exc))
        todo.extend((exc.__context__, exc.__cause__))
        tb = exc.__traceback__
        while tb is not None:
            if tb.tb_frame.f_code is not test_no_query_plan_bundle_in_any_callee_exception_chain.__code__:
                frames.append(tb.tb_frame)
            tb = tb.tb_next
    assert frames
    assert not any(value is query or value is plan or value is bundle or value is fixed
                   for frame in frames for value in frame.f_locals.values())


@pytest.mark.parametrize("kind", ["exception_group", "base_group", "oversized_group", "oversized_base_group", "cyclic_group", "custom_base", "frame_budget", "interrupt_budget", "systemexit_budget"])
def test_exception_groups_keep_owner_unknown_and_clear_every_exception_frame(guarded, tmp_path, monkeypatch, kind):
    """Nested group/context/cause members must not expose a synthetic query."""
    guard, marker = guarded
    query = b"SYNTHETIC_GROUP_QUERY_SENTINEL"
    class Kernel:
        def CreateNamedPipeW(self, *args): return 11
        def CreateJobObjectW(self, *args): return 12
        def SetInformationJobObject(self, *args): return 1
        def GetCurrentProcess(self): return 99
        def ResumeThread(self, *args): return 1
        def LocalFree(self, *args): return 0
        def TerminateJobObject(self, *args): return 1
        def QueryInformationJobObject(self, *args): return 1
        def WaitForSingleObject(self, *args): return 0
        def CloseHandle(self, *args): return 1
    monkeypatch.setattr(transport, "_QUARANTINE", [])
    monkeypatch.setattr(transport, "_api", lambda: (Kernel(), None))
    monkeypatch.setattr(transport, "_descriptor", lambda *args: 14)
    monkeypatch.setattr(transport, "_noninheritable", lambda *args: None)
    monkeypatch.setattr(transport, "_created", lambda *args: 1)
    monkeypatch.setattr(transport, "_create_process_in_job", lambda *args: (15, 16, 17, 18))
    monkeypatch.setattr(transport, "_peer", lambda *args, **kwargs: None)
    if kind == "frame_budget":
        # A lower test-only cap exercises the actual bounded traversal path.
        monkeypatch.setattr(transport, "_MAX_EXCEPTION_FRAMES", 1, raising=False)
    if kind in ("interrupt_budget", "systemexit_budget"):
        monkeypatch.setattr(transport, "_clear_exception_frames", lambda exc: False)
    def leaf_failure(payload):
        raise ValueError("synthetic")
    class CustomBase(BaseException):
        pass
    def io(k, pipe, data, count, deadline, **kwargs):
        if data is query:
            leaves = []
            for _ in range(130 if kind in ("oversized_group", "oversized_base_group") else 1):
                try:
                    leaf_failure(data)
                except ValueError as leaf:
                    leaves.append(leaf)
            if kind == "custom_base":
                raise CustomBase() from leaves[0]
            if kind == "interrupt_budget":
                raise KeyboardInterrupt() from leaves[0]
            if kind == "systemexit_budget":
                raise SystemExit(77) from leaves[0]
            if kind == "cyclic_group":
                other = ValueError("synthetic cycle")
                leaves[0].__context__ = other
                other.__context__ = leaves[0]
            nested = ExceptionGroup("nested synthetic", leaves)
            if kind in ("base_group", "oversized_base_group"):
                raise BaseExceptionGroup("outer synthetic", [KeyboardInterrupt(), SystemExit(1), nested])
            raise ExceptionGroup("outer synthetic", [nested])
        return b""
    monkeypatch.setattr(transport, "_io", io)
    expected = (BaseExceptionGroup if kind in ("base_group", "oversized_base_group") else CustomBase if kind == "custom_base"
                else KeyboardInterrupt if kind == "interrupt_budget" else SystemExit if kind == "systemexit_budget"
                else transport.TransportCleanupUnknown)
    with pytest.raises(expected) as raised:
        transport._run([sys.executable], str(tmp_path), {"mode": "python-c"}, query, 1, 1024,
                       transport_owner=guard._transport_owner())
    assert marker.exists() and guard._registry._uncertain
    assert "transport" not in guard._registry._done
    if kind in ("oversized_group", "oversized_base_group", "frame_budget", "interrupt_budget", "systemexit_budget"):
        assert raised.value.__context__ is None and raised.value.__cause__ is None
    if kind == "systemexit_budget":
        assert raised.value.code == 77
    seen, todo, frames = set(), [raised.value], []
    while todo:
        exc = todo.pop()
        if exc is None or id(exc) in seen:
            continue
        seen.add(id(exc))
        todo.extend((exc.__context__, exc.__cause__))
        if isinstance(exc, BaseExceptionGroup):
            todo.extend(exc.exceptions)
        tb = exc.__traceback__
        while tb is not None:
            if tb.tb_frame.f_code is not test_exception_groups_keep_owner_unknown_and_clear_every_exception_frame.__code__:
                frames.append(tb.tb_frame)
            tb = tb.tb_next
    assert frames
    assert not any(value is query for frame in frames for value in frame.f_locals.values())


def test_active_outer_exception_frame_is_not_retained_in_sanitized_failure(guarded, tmp_path, monkeypatch):
    guard, marker = guarded
    query = b"SYNTHETIC_ACTIVE_OUTER_QUERY"
    monkeypatch.setattr(transport, "_QUARANTINE", [])
    try:
        raise ValueError(query)
    except ValueError as active_error:
        def fail_api():
            raise transport._Refused() from active_error
        monkeypatch.setattr(transport, "_api", fail_api)
        with pytest.raises(transport.TransportCleanupUnknown) as raised:
            transport._run([sys.executable], str(tmp_path), {"mode": "python-c"}, query, 1, 1024,
                           transport_owner=guard._transport_owner())
        assert raised.value.__context__ is None and raised.value.__cause__ is None
    assert marker.exists() and guard._registry._uncertain
    assert "transport" not in guard._registry._done
