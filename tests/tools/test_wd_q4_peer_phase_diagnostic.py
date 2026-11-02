"""Isolated synthetic process tests for the opt-in fixed-peer diagnostic."""

import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

from tools import wd_q4_secure_transport as transport

pytestmark = pytest.mark.skipif(os.name != "nt", reason="fixed peer requires Windows")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fixture(tmp_path):
    interpreter = str(Path(sys.executable).resolve(strict=True))
    assert os.path.normcase(interpreter) == os.path.normcase(os.path.abspath(sys.executable))
    bundle = b"fixed synthetic bundle; no live data"
    manifest = b"fixed synthetic manifest; no live data"
    peer = Path(transport.__file__).read_bytes()
    interpreter_sha = sha(Path(interpreter).read_bytes())
    plan = {"schema": "wd.q4-fixed-plan.v1", "backend": "synthetic-v1",
            "interpreter_sha256": interpreter_sha, "peer_code_sha256": sha(peer),
            "runtime_manifest_sha256": sha(manifest), "bundle_sha256": sha(bundle), "bundle_bytes": len(bundle)}
    plan_bytes = json.dumps(plan, sort_keys=True, separators=(",", ":")).encode("ascii")
    return {"executable": interpreter, "expected_interpreter_sha256": interpreter_sha,
            "interpreter_max_bytes": 128 * 1024 * 1024, "plan_bytes": plan_bytes,
            "expected_plan_sha256": sha(plan_bytes), "bundle_bytes": bundle,
            "runtime_manifest_bytes": manifest, "query_bytes": b"synthetic-private-query-marker",
            "cwd": str(tmp_path), "timeout_seconds": 45, "max_output_bytes": 65536}


def call(**kwargs):
    return transport.run_fixed_peer_diagnostic(**kwargs)


def test_default_off_does_not_inspect_pins_or_spawn():
    assert call(executable=None, expected_interpreter_sha256=None, interpreter_max_bytes=None,
                plan_bytes=None, expected_plan_sha256=None, bundle_bytes=None, runtime_manifest_bytes=None,
                query_bytes=None, cwd=None, timeout_seconds=None, max_output_bytes=None) == {"status": "OFF"}


@pytest.mark.parametrize("fault, expected", [(False, "completed"), (True, "after_query")])
def test_real_synthetic_peer_returns_only_closed_phase(tmp_path, fault, expected):
    values = fixture(tmp_path)
    result = call(**values, enabled=True, synthetic_force_after_query=fault)
    assert result == {"status": "DIAGNOSTIC", "phase": expected, "cleanup": "UNKNOWN"}
    text = repr(result)
    assert "synthetic-private-query-marker" not in text and str(tmp_path) not in text
    assert values["expected_plan_sha256"] not in text and values["expected_interpreter_sha256"] not in text


def test_diagnostic_switch_requires_literal_true_and_checks_pins_before_peer(tmp_path):
    values = fixture(tmp_path)
    with pytest.raises(ValueError):
        call(**values, enabled=1)
    values["expected_plan_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        call(**values, enabled=True)


def test_cleanup_unknown_propagates_and_is_not_a_diagnostic_phase(tmp_path, monkeypatch):
    values = fixture(tmp_path)
    calls = []
    def unknown(*args):
        calls.append(True)
        raise transport.TransportCleanupUnknown("refused")
    monkeypatch.setattr(transport, "_run_fixed_peer_impl", unknown)
    with pytest.raises(transport.TransportCleanupUnknown) as caught:
        call(**values, enabled=True)
    assert calls == [True] and caught.value.prior_outcome == "refused"
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_backend_payload_cannot_be_returned_as_a_phase(tmp_path, monkeypatch):
    values = fixture(tmp_path)
    monkeypatch.setattr(transport, "_run", lambda *args, **kwargs: (b"synthetic-private-query-marker", 0))
    with pytest.raises(ValueError, match="^secure_transport_refused$") as caught:
        call(**values, enabled=True)
    assert "synthetic-private-query-marker" not in repr(caught.value)


def public_fixture(tmp_path):
    values = fixture(tmp_path)
    plan = json.loads(values["plan_bytes"])
    plan.update({"schema": "wd.q4-fixed-plan.v2", "backend": "public-snapshot-v1",
                 "verifier_sha256": sha((Path(transport.__file__).resolve().parents[1]
                                          / "sandbox/mind/step2/runtime_manifest.py").read_bytes()),
                 "runtime_root": str(tmp_path), "runtime_manifest_path": str(tmp_path / "synthetic-manifest"),
                 "runtime_generation": "a" * 40, "runtime_revision": "b" * 40,
                 "approved_tasks": ["synthetic-task"]})
    plan.update({key: "c" * 64 for key in transport._PUBLIC_PINS})
    values["plan_bytes"] = json.dumps(plan, sort_keys=True, separators=(",", ":")).encode("ascii")
    values["expected_plan_sha256"] = sha(values["plan_bytes"])
    return values


@pytest.mark.parametrize("fault", (False, True))
def test_diagnostic_rejects_public_plan_before_attestation_or_spawn(tmp_path, monkeypatch, fault):
    import contextlib
    from tools import wd_q4_fixed_parent_attestation as attestation
    events = []
    @contextlib.contextmanager
    def fake_attest(*args, **kwargs):
        events.append("attest")
        yield
    monkeypatch.setattr(attestation, "attest_file", fake_attest)
    monkeypatch.setattr(transport, "_run", lambda *args, **kwargs: (events.append("spawn") or (b"completed", 0)))
    with pytest.raises(ValueError, match="^secure_transport_refused$"):
        call(**public_fixture(tmp_path), enabled=True, public_snapshot_enabled=True,
             synthetic_force_after_query=fault)
    assert events == []


def test_normal_public_route_is_still_available(tmp_path, monkeypatch):
    import contextlib
    from tools import wd_q4_fixed_parent_attestation as attestation
    events = []
    @contextlib.contextmanager
    def fake_attest(*args, **kwargs):
        events.append("attest")
        yield
    monkeypatch.setattr(attestation, "attest_file", fake_attest)
    monkeypatch.setattr(transport, "_run", lambda *args, **kwargs: (b"synthetic-result", 0))
    monkeypatch.setattr(transport, "_fixed_public_validate", lambda *args: b"synthetic-validated")
    assert transport.run_fixed_peer(**public_fixture(tmp_path), public_snapshot_enabled=True) == (b"synthetic-validated", 0)
    assert events == ["attest", "attest", "attest"]


@pytest.mark.parametrize("interruption", (KeyboardInterrupt, SystemExit, GeneratorExit))
def test_child_does_not_convert_interruption_to_after_query(monkeypatch, interruption):
    class FakeKernel:
        def CreateFileW(self, *args): return 7
        def CloseHandle(self, *args): return 1
    monkeypatch.setattr(transport, "_api", lambda: (FakeKernel(), None))
    monkeypatch.setattr(transport, "_handle", lambda handle: handle)
    monkeypatch.setattr(transport, "_noninheritable", lambda *args: None)
    monkeypatch.setattr(transport, "_peer", lambda *args, **kwargs: None)
    writes = []
    monkeypatch.setattr(transport, "_sync_write", lambda *args: writes.append(args[-1]))
    def interrupted(*args, **kwargs):
        kwargs["_diagnostic_marker"][0] = True
        raise interruption("synthetic-private")
    monkeypatch.setattr(transport, "_fixed_child_protocol", interrupted)
    with pytest.raises(interruption):
        transport._fixed_diagnostic_main("a" * 64, "observe", r"\\.\pipe\wd-q4-synthetic-" + "1" * 32, "1", "2")
    assert writes == []


def test_diagnostic_contract_is_honest_about_query_attempt_and_caller_data():
    text = transport.run_fixed_peer_diagnostic.__doc__
    assert "query read was attempted" in text
    assert "does not prove query receipt or backend execution" in text
    assert "synthetic backend does not authenticate caller bytes" in text
