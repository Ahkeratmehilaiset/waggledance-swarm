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
    monkeypatch.setattr(transport, "_run", lambda *a, **k: (calls.append(True) or (b"ok", 0)))
    def invoke(value):
        return transport._run_fixed_peer_impl(sys.executable, interpreter_pin, 1, plan_bytes, sha(plan_bytes),
                                               bundle, value, b"query", str(tmp_path), 1, 1024)
    assert invoke(raw) == (b"ok", 0)
    too_large = raw + b" " * (MAX_MANIFEST_BYTES + 1 - len(raw))
    plan["runtime_manifest_sha256"] = sha(too_large)
    with pytest.raises(transport._Refused):
        invoke(too_large)
    assert calls == [True]


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
    import _winapi

    marker = b"WD_SYNTHETIC_STDIN_ONLY_15d8db0ad52f4b38"
    observations = []
    original = _winapi.CreateProcess

    def traced(*args, **kwargs):
        # CPython's CreateProcess binding uses positional parameters here.
        command_line = args[1]
        inherit_handles = args[4]
        child_env = args[6] if args[6] is not None else os.environ
        marker_text = marker.decode("ascii")
        payload_in_command = marker_text in str(command_line)
        payload_in_environment = any(
            marker_text in str(key) or marker_text in str(value)
            for key, value in child_env.items()
        )
        observations.append((inherit_handles, payload_in_command, payload_in_environment))
        return original(*args, **kwargs)

    monkeypatch.setattr(_winapi, "CreateProcess", traced)
    result = _run(transport, tmp_path,
                  "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())", marker)
    assert result == (marker, 0)
    assert observations, "root CreateProcess was not observed in this process"
    assert all(inherit is False or inherit == 0 for inherit, _, _ in observations)
    assert not any(in_args or in_env for _, in_args, in_env in observations)


def test_nonzero_exit_is_returned_with_synthetic_stdout(transport, tmp_path):
    result = _run(transport, tmp_path,
                  "import sys; sys.stdout.buffer.write(b'ok'); sys.exit(7)")
    assert result == (b"ok", 7)


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
        def CloseHandle(self, *args): return 1

    monkeypatch.setattr(transport, "_QUARANTINE", [])
    monkeypatch.setattr(transport, "_api", lambda: (Kernel(), None))
    monkeypatch.setattr(transport, "_descriptor", lambda *args: 14)
    monkeypatch.setattr(transport, "_noninheritable", lambda *args: None)
    monkeypatch.setattr(transport, "_created", lambda *args: 1)
    monkeypatch.setattr(_winapi, "CreateProcess", lambda *args: (15, 16, 17, 18))
    monkeypatch.setattr(transport, "_io", lambda *a, **kw: (_ for _ in ()).throw(TimeoutError()))
    with pytest.raises(TimeoutError):
        transport._run([sys.executable], str(tmp_path), {"mode": "python-c"}, b"", 10, 1024)
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
