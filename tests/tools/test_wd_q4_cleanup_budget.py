"""Synthetic kernel only: no live child, service, or production data."""
import ctypes
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
