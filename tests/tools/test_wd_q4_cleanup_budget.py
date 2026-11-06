"""Synthetic kernel only: no live child, service, or production data."""
import ctypes
import inspect
import sys
import threading

import pytest

pytest.importorskip("_winapi")

from tools import wd_q4_secure_transport as transport


@pytest.mark.parametrize("clock_error", (RuntimeError, KeyboardInterrupt, SystemExit, GeneratorExit))
def test_initial_deadline_exception_releases_lock_without_spawning(tmp_path, monkeypatch, clock_error):
    calls, clock_fails = [], [True]
    lock = threading.Lock()
    def clock():
        if clock_fails[0]:
            clock_fails[0] = False
            raise clock_error("synthetic clock error")
        return 100.0
    def api():
        calls.append("api")
        raise RuntimeError("synthetic api refusal")
    monkeypatch.setattr(transport, "_RUN_LOCK", lock)
    monkeypatch.setattr(transport, "_QUARANTINE", [])
    monkeypatch.setattr(transport.time, "monotonic", clock)
    monkeypatch.setattr(transport, "_api", api)
    try:
        transport._run([sys.executable], str(tmp_path), {"mode": "python-c"}, b"", 1, 1024)
    except BaseException as caught:
        assert type(caught) is clock_error
    else:
        pytest.fail("clock exception must propagate")
    assert not lock.locked() and calls == []
    with pytest.raises(transport._Refused):
        transport._run([sys.executable], str(tmp_path), {"mode": "python-c"}, b"", 1, 1024)
    assert calls == ["api"] and not lock.locked()


@pytest.mark.parametrize("scenario", ("job-delayed", "job-never-empty", "interrupt", "cleanup-interrupt", "unassigned"))
def test_expired_exchange_gets_a_separate_bounded_cleanup_budget(tmp_path, monkeypatch, scenario):
    import _winapi

    clock = [100.0]
    quarantine = []
    calls = []
    class Kernel:
        def CreateNamedPipeW(self, *args): return 11
        def CreateJobObjectW(self, *args): return 12
        def SetInformationJobObject(self, *args): return 1
        def GetCurrentProcess(self): return 99
        def AssignProcessToJobObject(self, *args): return scenario != "unassigned"
        def ResumeThread(self, *args): return 1
        def LocalFree(self, *args): return 0
        def TerminateJobObject(self, *args): calls.append("terminate-job"); return 1
        def TerminateProcess(self, *args): calls.append("terminate-child"); return 1
        def QueryInformationJobObject(self, job, cls, out, size, returned):
            calls.append("probe")
            if scenario == "cleanup-interrupt":
                raise KeyboardInterrupt()
            active = scenario == "job-never-empty" or clock[0] < 102.02
            ctypes.cast(out, ctypes.POINTER(transport._JobAccounting)).contents.active_processes = int(active)
            return 1
        def WaitForSingleObject(self, handle, wait_ms):
            calls.append(("wait", wait_ms))
            return 0 if wait_ms > 0 else 258
        def CloseHandle(self, handle): calls.append(("close", handle)); return 1

    kernel = Kernel()
    monkeypatch.setattr(transport, "_QUARANTINE", quarantine)
    monkeypatch.setattr(transport, "_api", lambda: (kernel, None))
    monkeypatch.setattr(transport, "_descriptor", lambda *args: 14)
    monkeypatch.setattr(transport, "_noninheritable", lambda *args: None)
    monkeypatch.setattr(transport, "_created", lambda *args: 1)
    monkeypatch.setattr(_winapi, "CreateProcess", lambda *args: (15, 16, 17, 18))
    monkeypatch.setattr(transport.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(transport.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    def fail_exchange(*args, **kwargs):
        clock[0] = 102.0  # original deadline 101.0 has expired
        if scenario == "interrupt":
            raise KeyboardInterrupt()
        raise TimeoutError()
    monkeypatch.setattr(transport, "_io", fail_exchange)
    if scenario == "unassigned":
        # Assignment fails before exchange; expire the call deadline there too.
        def fail_assignment(*args):
            clock[0] = 102.0
            return 0
        monkeypatch.setattr(kernel, "AssignProcessToJobObject", fail_assignment)

    expected = {"job-delayed": TimeoutError, "job-never-empty": transport.TransportCleanupUnknown,
                "cleanup-interrupt": transport.TransportCleanupUnknown,
                "interrupt": KeyboardInterrupt, "unassigned": transport._Refused}[scenario]
    with pytest.raises(expected):
        transport._run([sys.executable], str(tmp_path), {"mode": "python-c"}, b"", 1, 1024)
    assert bool(quarantine) == (scenario in ("job-never-empty", "cleanup-interrupt"))
    if scenario == "job-never-empty":
        assert 106.99 <= clock[0] <= 107.01
    elif scenario in ("job-delayed", "interrupt"):
        assert calls.count("probe") >= 2
        assert clock[0] >= 102.02
    elif scenario == "unassigned":
        assert any(item[0] == "wait" and 0 < item[1] <= 5000 for item in calls if isinstance(item, tuple))
    assert ("close", 15) in calls and ("close", 16) in calls


@pytest.mark.parametrize("interruption,classification", [(KeyboardInterrupt, "interrupt"),
    (SystemExit, "exit"), (RuntimeError, "timeout")])
@pytest.mark.parametrize("assigned", [True, False], ids=("assigned-job", "unassigned-child"))
def test_cleanup_deadline_exception_still_terminates_and_quarantines(
        tmp_path, monkeypatch, interruption, classification, assigned):
    import _winapi

    calls, quarantine, fail_clock = [], [], [False]
    class Kernel:
        def CreateNamedPipeW(self, *args): return 11
        def CreateJobObjectW(self, *args): return 12
        def SetInformationJobObject(self, *args): return 1
        def GetCurrentProcess(self): return 99
        def AssignProcessToJobObject(self, *args):
            if not assigned:
                fail_clock[0] = True
                raise TimeoutError()
            return 1
        def ResumeThread(self, *args): return 1
        def LocalFree(self, *args): return 0
        def TerminateJobObject(self, *args): calls.append("terminate-job"); return 1
        def TerminateProcess(self, *args): calls.append("terminate-child"); return 1
        def CloseHandle(self, handle): calls.append(("close", handle)); return 1

    kernel = Kernel()
    def api():
        calls.append("api")
        return kernel, None
    def monotonic():
        if fail_clock[0]:
            fail_clock[0] = False
            raise interruption("synthetic")
        return 100.0
    def expire(*args, **kwargs):
        fail_clock[0] = True
        raise TimeoutError()
    monkeypatch.setattr(transport, "_QUARANTINE", quarantine)
    monkeypatch.setattr(transport, "_api", api)
    monkeypatch.setattr(transport, "_descriptor", lambda *args: 14)
    monkeypatch.setattr(transport, "_noninheritable", lambda *args: None)
    monkeypatch.setattr(transport, "_created", lambda *args: 1)
    monkeypatch.setattr(_winapi, "CreateProcess", lambda *args: (15, 16, 17, 18))
    monkeypatch.setattr(transport.time, "monotonic", monotonic)
    monkeypatch.setattr(transport, "_io", expire)
    try:
        transport._run([sys.executable], str(tmp_path), {"mode": "python-c"}, b"", 1, 1024)
    except BaseException as caught:
        assert isinstance(caught, transport.TransportCleanupUnknown)
        assert caught.prior_outcome == classification
    else:
        pytest.fail("uncertain cleanup must raise")
    assert ("terminate-job" if assigned else "terminate-child") in calls
    assert quarantine
    assert ("close", 15) in calls and ("close", 16) in calls
    before = list(calls)
    with pytest.raises(transport.TransportCleanupUnknown):
        transport._run([sys.executable], str(tmp_path), {"mode": "python-c"}, b"", 1, 1024)
    assert calls == before


@pytest.mark.parametrize("fault", ["job_truth", "process_int", "pipe_truth", "pipe_tracking", "trace_cleanup", "trace_prologue"])
@pytest.mark.parametrize("error_kind", [RuntimeError, KeyboardInterrupt, SystemExit, transport.TransportCleanupUnknown],
                         ids=["runtime-error", "interrupt", "exit", "cleanup-unknown"])
@pytest.mark.parametrize("route", ["direct", "fixed_wrapper", "bounded_wrapper"])
def test_cleanup_decision_or_conversion_exception_keeps_cleaning_and_quarantines(tmp_path, monkeypatch, fault, error_kind, route):
    """Synthetic handles/kernel only: no OS child or live job is created."""
    import _winapi

    calls, quarantine = [], []
    phase = {"cleanup": False, "injected": False}

    class Job(int):
        def __bool__(self):
            if fault == "job_truth" and phase["cleanup"] and not phase["injected"]:
                phase["injected"] = True
                raise error_kind("synthetic job truth error")
            return True

    class Process(int):
        def __int__(self):
            if fault == "process_int" and phase["cleanup"] and not phase["injected"]:
                phase["injected"] = True
                raise error_kind("synthetic process conversion error")
            return super().__int__()

    class Pipe(int):
        def __bool__(self):
            if fault == "pipe_truth" and phase["cleanup"] and not phase["injected"]:
                phase["injected"] = True
                raise error_kind("synthetic pipe truth error")
            return True

    class BadQuarantineEntry:
        def __getitem__(self, key):
            phase["injected"] = True
            raise error_kind("synthetic pending-I/O tracking error")

    class Kernel:
        def CreateNamedPipeW(self, *args): return Pipe(11)
        def CreateJobObjectW(self, *args): return Job(12)
        def SetInformationJobObject(self, *args): return 1
        def GetCurrentProcess(self): return 99
        def AssignProcessToJobObject(self, *args): return 1
        def ResumeThread(self, *args): return 1
        def LocalFree(self, *args): return 0
        def TerminateJobObject(self, *args): calls.append("terminate-job"); return 1
        def CloseHandle(self, handle): calls.append(("close", handle)); return 1

    kernel = Kernel()
    # An unprotected prologue interruption can leave its dispatch lock held;
    # isolate the synthetic lock so a RED cannot contaminate later tests.
    monkeypatch.setattr(transport, "_RUN_LOCK", threading.Lock())
    monkeypatch.setattr(transport, "_QUARANTINE", quarantine)
    monkeypatch.setattr(transport, "_api", lambda: (kernel, None))
    monkeypatch.setattr(transport, "_descriptor", lambda *args: 14)
    monkeypatch.setattr(transport, "_noninheritable", lambda *args: None)
    monkeypatch.setattr(transport, "_created", lambda *args: 1)
    def wait_job(*args):
        calls.append("wait-job")
        return True
    monkeypatch.setattr(transport, "_wait_job_empty", wait_job)
    monkeypatch.setattr(_winapi, "CreateProcess", lambda *args: (Process(15), 16, 17, 18))

    def fail_exchange(*args, **kwargs):
        phase["cleanup"] = True
        if fault == "pipe_tracking":
            quarantine.append(BadQuarantineEntry())
        if fault in ("trace_cleanup", "trace_prologue"):
            lines, first = inspect.getsourcelines(transport._run)
            marker = "prior_outcome = _prior_outcome(sys.exc_info()[1])" if fault == "trace_prologue" else "if k is not None:"
            target = next(first + index for index, line in enumerate(lines) if line.strip() == marker)
            def trace(frame, event, arg):
                if frame.f_code is transport._run.__code__ and event == "line" and frame.f_lineno == target:
                    phase["injected"] = True
                    sys.settrace(None)
                    raise error_kind("other" if error_kind is transport.TransportCleanupUnknown else "synthetic cleanup line interruption")
                return trace
            sys._getframe(1).f_trace = trace
            sys.settrace(trace)
        raise TimeoutError("synthetic exchange timeout")

    monkeypatch.setattr(transport, "_io", fail_exchange)
    def direct():
        return transport._run([sys.executable], str(tmp_path), {"mode": "python-c"}, b"", 1, 1024)
    monkeypatch.setattr(transport, "_run_fixed_peer_impl", lambda *args, **kwargs: direct())
    monkeypatch.setattr(transport, "_run_bounded_impl", lambda **kwargs: direct())
    def invoke():
        if route == "direct":
            return direct()
        if route == "bounded_wrapper":
            return transport.run_bounded(executable="x", arguments=[], cwd="x", stdin_bytes=b"", timeout_seconds=1,
                max_output_bytes=1024, expected_interpreter_sha256="x", interpreter_max_bytes=1)
        return transport.run_fixed_peer(executable="x", expected_interpreter_sha256="x", interpreter_max_bytes=1,
            plan_bytes=b"", expected_plan_sha256="x", bundle_bytes=b"", runtime_manifest_bytes=b"",
            query_bytes=b"", cwd="x", timeout_seconds=1, max_output_bytes=1024)
    expected = TimeoutError if fault == "pipe_truth" else transport.TransportCleanupUnknown
    previous_trace = sys.gettrace()
    try:
        with pytest.raises(expected) as caught:
            invoke()
    finally:
        sys.settrace(previous_trace)
    if fault != "pipe_truth":
        expected_outcome = {RuntimeError: "other", KeyboardInterrupt: "interrupt", SystemExit: "exit", transport.TransportCleanupUnknown: "other"}[error_kind] if fault == "trace_prologue" else {RuntimeError: "timeout", KeyboardInterrupt: "interrupt", SystemExit: "exit", transport.TransportCleanupUnknown: "timeout"}[error_kind]
        assert caught.value.prior_outcome == expected_outcome
    assert phase["injected"] is (fault != "pipe_truth")
    assert bool(quarantine) is (fault != "pipe_truth")
    if fault not in ("trace_cleanup", "trace_prologue"):
        assert "terminate-job" in calls
        assert "wait-job" in calls
        assert ("close", 16) in calls
        assert (("close", 11) in calls) is (fault != "pipe_tracking")
    else:
        assert quarantine[-1] == (kernel, 11, (12, 15, 16))
        before = list(calls)
        with pytest.raises(transport.TransportCleanupUnknown) as retry:
            direct()
        assert retry.value.prior_outcome == "prior_unknown" and calls == before
    assert not transport._RUN_LOCK.locked()


@pytest.mark.parametrize("route", ["direct", "fixed_wrapper", "bounded_wrapper"])
@pytest.mark.parametrize("fault", ["trace_cleanup", "trace_prologue"])
def test_outer_cleanup_interruption_without_owned_resources_preserves_preflight_refusal(tmp_path, monkeypatch, route, fault):
    quarantine, injected = [], []
    monkeypatch.setattr(transport, "_QUARANTINE", quarantine)
    lines, first = inspect.getsourcelines(transport._run)
    marker = "prior_outcome = _prior_outcome(sys.exc_info()[1])" if fault == "trace_prologue" else "if k is not None:"
    target = next(first + index for index, line in enumerate(lines) if line.strip() == marker)
    def trace(frame, event, arg):
        if frame.f_code is transport._run.__code__ and event == "line" and frame.f_lineno == target:
            injected.append(True)
            sys.settrace(None)
            raise RuntimeError("synthetic preflight cleanup interruption")
        return trace
    def refuse_api():
        sys._getframe(1).f_trace = trace
        sys.settrace(trace)
        raise transport._Refused()
    monkeypatch.setattr(transport, "_api", refuse_api)
    def direct():
        return transport._run([sys.executable], str(tmp_path), {"mode": "python-c"}, b"", 1, 1024)
    monkeypatch.setattr(transport, "_run_fixed_peer_impl", lambda *args, **kwargs: direct())
    monkeypatch.setattr(transport, "_run_bounded_impl", lambda **kwargs: direct())
    def invoke():
        if route == "direct":
            return direct()
        if route == "bounded_wrapper":
            return transport.run_bounded(executable="x", arguments=[], cwd="x", stdin_bytes=b"", timeout_seconds=1,
                max_output_bytes=1024, expected_interpreter_sha256="x", interpreter_max_bytes=1)
        return transport.run_fixed_peer(executable="x", expected_interpreter_sha256="x", interpreter_max_bytes=1,
            plan_bytes=b"", expected_plan_sha256="x", bundle_bytes=b"", runtime_manifest_bytes=b"",
            query_bytes=b"", cwd="x", timeout_seconds=1, max_output_bytes=1024)
    previous_trace = sys.gettrace()
    try:
        with pytest.raises(RuntimeError if route == "direct" else transport._Refused):
            invoke()
    finally:
        sys.settrace(previous_trace)
    assert injected == [True] and quarantine == [] and not transport._RUN_LOCK.locked()
