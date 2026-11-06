"""Synthetic fixed-peer owner dispatch; no callback proves native cleanup."""
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

from tools import wd_q4_guard_completion as guard_api
from tools import wd_q4_secure_transport as transport


def new_guard(root):
    runtime = root / "runtime"
    state = runtime / "shared" / ".codex-audit" / "codex-tools-1" / "q4-caller-state"
    state.mkdir(parents=True)
    raw = json.dumps({"runtime_root": str(runtime)}).encode("ascii")
    guard = guard_api.acquire_guard(fleet_bytes=raw, expected_fleet_sha256=hashlib.sha256(raw).hexdigest(),
        lane="codex-tools-1", identity_sha256="a" * 64, descriptor_sha256="b" * 64)
    return guard, state / "codex-tools-1.pending.json"


def kwargs(tmp_path):
    return {"executable": sys.executable, "expected_interpreter_sha256": "a" * 64,
            "interpreter_max_bytes": 1024, "plan_bytes": b"synthetic-plan",
            "expected_plan_sha256": "b" * 64, "bundle_bytes": b"synthetic-bundle",
            "runtime_manifest_bytes": b"synthetic-manifest", "query_bytes": b"synthetic-query",
            "cwd": str(tmp_path), "timeout_seconds": 1, "max_output_bytes": 1024}


def test_legacy_fixed_peer_dispatch_stays_compatible(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(transport, "_run_fixed_peer_impl", lambda *a, **kw: seen.append((a, kw)) or (b"ok", 0))
    assert transport.run_fixed_peer(**kwargs(tmp_path)) == (b"ok", 0)
    assert len(seen) == 1 and "_owner_guard" not in seen[0][1] and "_owner_context" not in seen[0][1]


def test_matching_guard_and_context_are_forwarded_without_creating_completion(tmp_path, monkeypatch):
    """A fake success with no owner contributions must never publish output."""
    guard, marker = new_guard(tmp_path / "one")
    ctx = guard._owner_context()
    seen = []
    monkeypatch.setattr(transport, "_run_fixed_peer_impl", lambda *a, **kw: seen.append(kw) or (b"ok", 0))
    with pytest.raises(transport.TransportCleanupUnknown):
        transport.run_fixed_peer(**kwargs(tmp_path), _owner_guard=guard, _owner_context=ctx)
    assert seen == [{"_owner_guard": guard, "_owner_context": ctx}]
    assert guard._registry._done == set() and guard._registry._uncertain and marker.exists()
    with pytest.raises(guard_api.GuardCompletionUnavailable):
        guard._registry._issue()


def test_guarded_wrapper_accepts_only_synthetic_completed_inner_owners(tmp_path, monkeypatch):
    """Synthetic ticket completion tests the barrier only; it proves no native cleanup."""
    guard, marker = new_guard(tmp_path / "one")
    ctx = guard._owner_context()
    def fake_inner(*args, **kw):
        assert kw == {"_owner_guard": guard, "_owner_context": ctx}
        for name in ("attestation", "transport"):
            ticket = ctx._ticket(name)
            ticket._begin()
            ticket._complete()
        return b"synthetic-result", 0
    monkeypatch.setattr(transport, "_run_fixed_peer_impl", fake_inner)
    assert transport.run_fixed_peer(**kwargs(tmp_path), _owner_guard=guard, _owner_context=ctx) == (b"synthetic-result", 0)
    assert marker.exists() and guard._registry._done == {"attestation", "transport"}
    with pytest.raises(guard_api.GuardCompletionUnavailable):
        guard._registry._issue()  # Adapter and outer call remain unfinished.


def test_context_requires_both_exact_inner_tickets_and_fails_on_unknown(tmp_path):
    guard, marker = new_guard(tmp_path / "one")
    ctx = guard._owner_context()
    with pytest.raises(guard_api.GuardCompletionUnavailable):
        ctx._require_done(("attestation", "transport"))
    for name in ("attestation", "transport"):
        ticket = ctx._ticket(name)
        ticket._begin()
        ticket._complete()
    ctx._require_done(("attestation", "transport"))
    with pytest.raises(guard_api.GuardCompletionUnavailable):
        ctx._require_done(("attestation", "attestation"))
    with pytest.raises(guard_api.GuardCompletionUnavailable):
        ctx._require_done(("attestation", "unknown"))
    ctx._abort()
    with pytest.raises(guard_api.GuardCompletionUnavailable):
        ctx._require_done(("attestation", "transport"))
    assert marker.exists()


def test_forged_registry_done_set_without_ticket_completion_is_not_enough(tmp_path):
    guard, marker = new_guard(tmp_path / "one")
    ctx = guard._owner_context()
    with guard._registry._lock:
        guard._registry._done.update(("attestation", "transport"))
    with pytest.raises(guard_api.GuardCompletionUnavailable):
        ctx._require_done(("attestation", "transport"))
    assert marker.exists()


def test_context_require_done_refuses_after_guard_release(tmp_path):
    guard, marker = new_guard(tmp_path / "one")
    ctx = guard._owner_context()
    for name in ("attestation", "transport", "adapter"):
        ticket = ctx._ticket(name)
        ticket._begin()
        ticket._complete()
    ctx._finish()
    guard.release(guard._registry._issue())
    assert not marker.exists()
    with pytest.raises(guard_api.GuardCompletionUnavailable):
        ctx._require_done(("attestation", "transport"))


@pytest.mark.parametrize("pair", ["missing_guard", "missing_context", "foreign_same_lane"])
def test_invalid_owner_pair_refuses_before_fixed_peer_dispatch(tmp_path, monkeypatch, pair):
    guard, marker = new_guard(tmp_path / "one")
    other, other_marker = new_guard(tmp_path / "two")
    ctx, foreign = guard._owner_context(), other._owner_context()
    supplied = {"missing_guard": (None, ctx), "missing_context": (guard, None),
                "foreign_same_lane": (guard, foreign)}[pair]
    seen = []
    monkeypatch.setattr(transport, "_run_fixed_peer_impl", lambda *a, **kw: seen.append(True))
    with pytest.raises(transport._Refused):
        transport.run_fixed_peer(**kwargs(tmp_path), _owner_guard=supplied[0], _owner_context=supplied[1])
    assert not seen and marker.exists() and other_marker.exists()


@pytest.mark.parametrize("pair", ["missing_guard", "missing_context", "foreign_same_lane"])
def test_lower_impl_invalid_pair_refuses_before_validation_or_native_work(tmp_path, monkeypatch, pair):
    guard, marker = new_guard(tmp_path / "one")
    other, other_marker = new_guard(tmp_path / "two")
    ctx, foreign = guard._owner_context(), other._owner_context()
    supplied = {"missing_guard": (None, ctx), "missing_context": (guard, None),
                "foreign_same_lane": (guard, foreign)}[pair]
    seen = []
    monkeypatch.setattr(transport, "_validate", lambda *args: seen.append("validate"))
    monkeypatch.setattr(transport, "_run", lambda *args, **kwargs: seen.append("native"))
    with pytest.raises(transport._Refused):
        transport._run_fixed_peer_impl(**kwargs(tmp_path), _owner_guard=supplied[0], _owner_context=supplied[1])
    assert not seen and marker.exists() and other_marker.exists()


@pytest.mark.parametrize("public", [False, True], ids=["synthetic-backend", "public-3-leases-synthetic"])
def test_lower_impl_routes_distinct_tickets_and_closes_all_synthetic_leases(tmp_path, monkeypatch, public):
    """Synthetic lease/kernel and transport; not proof of native cleanup or a real answer."""
    from tools import wd_q4_fixed_parent_attestation as attestation
    guard, marker = new_guard(tmp_path / "one")
    ctx = guard._owner_context()
    query, bundle, manifest = b"synthetic-query", b"synthetic-bundle", b"synthetic-manifest"
    plan_raw = b"synthetic-plan"
    sha = lambda value: hashlib.sha256(value).hexdigest()
    verifier = Path(transport.__file__).resolve().parents[1] / "sandbox/mind/step2/runtime_manifest.py"
    plan = {"backend": "public-snapshot-v1" if public else "synthetic-v1", "interpreter_sha256": "a" * 64,
            "peer_code_sha256": sha(Path(transport.__file__).read_bytes()),
            "runtime_manifest_sha256": sha(manifest), "bundle_bytes": len(bundle),
            "bundle_sha256": sha(bundle), "verifier_sha256": sha(verifier.read_bytes())}
    monkeypatch.setattr(transport, "_fixed_plan", lambda raw: plan)
    monkeypatch.setattr(transport, "_fixed_public_validate", lambda *args: b"synthetic-result")
    closed, opened, runs = [], [], []
    class Kernel:
        def CloseHandle(self, handle):
            closed.append(handle)
            return 1
    def fake_attest(path, expected, *, max_bytes, enabled=False):
        handle = 100 + len(opened)
        opened.append(handle)
        return attestation.HeldFile(Kernel(), handle, "c" * 64, 1)
    monkeypatch.setattr(attestation, "attest_file", fake_attest)
    def fake_run(command, cwd, metadata, payload, timeout_seconds, max_output_bytes, fixed=None, *, transport_owner=None):
        runs.append(transport_owner)
        assert transport_owner is ctx._ticket("transport")
        assert type(transport_owner) is guard_api._TransportOwner
        return transport._fixed_wrap(fixed[0]["nonce"], payload, b"synthetic-result"), 0
    monkeypatch.setattr(transport, "_run", fake_run)
    result = transport._run_fixed_peer_impl(sys.executable, "a" * 64, 1024, plan_raw, sha(plan_raw),
        bundle, manifest, query, str(tmp_path), 1, 1024, public_snapshot_enabled=public,
        _owner_guard=guard, _owner_context=ctx)
    assert result == (b"synthetic-result", 0)
    assert len(closed) == (3 if public else 2) and len(runs) == 1
    assert closed == list(reversed(opened))
    assert guard._registry._done == {"attestation"}  # Transport stub supplies no proof.
    assert marker.exists()
    with pytest.raises(guard_api.GuardCompletionUnavailable):
        guard._registry._issue()


def test_wrong_transport_ticket_refuses_before_any_lease_or_spawn(tmp_path, monkeypatch):
    from tools import wd_q4_fixed_parent_attestation as attestation
    guard, marker = new_guard(tmp_path / "one")
    ctx = guard._owner_context()
    ctx._tickets["transport"] = ctx._ticket("adapter")  # Synthetic wiring mistake.
    bundle, manifest, plan_raw = b"bundle", b"manifest", b"plan"
    sha = lambda value: hashlib.sha256(value).hexdigest()
    plan = {"backend": "synthetic-v1", "interpreter_sha256": "a" * 64,
            "peer_code_sha256": sha(Path(transport.__file__).read_bytes()),
            "runtime_manifest_sha256": sha(manifest), "bundle_bytes": len(bundle), "bundle_sha256": sha(bundle)}
    monkeypatch.setattr(transport, "_fixed_plan", lambda raw: plan)
    seen = []
    monkeypatch.setattr(attestation, "attest_file", lambda *a, **kw: seen.append("lease"))
    monkeypatch.setattr(transport, "_run", lambda *a, **kw: seen.append("spawn"))
    with pytest.raises(guard_api.GuardCompletionUnavailable):
        transport._run_fixed_peer_impl(sys.executable, "a" * 64, 1024, plan_raw, sha(plan_raw),
            bundle, manifest, b"query", str(tmp_path), 1, 1024, _owner_guard=guard, _owner_context=ctx)
    assert not seen and guard._registry._uncertain and marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows owned child and attestation leases")
def test_real_owned_fixed_peer_records_both_inner_owners_without_releasing_guard(tmp_path, monkeypatch):
    """One real Windows child with synthetic query/bundle; no production Mind or guard release."""
    import _winapi
    guard, marker = new_guard(tmp_path / "one")
    ctx = guard._owner_context()
    sha = lambda value: hashlib.sha256(value).hexdigest()
    exe = Path(sys.executable)
    bundle, manifest = b"synthetic-bundle", b"synthetic-runtime-manifest"
    query = b'{"situation":"synthetic","time":"2026-11-06T00:00:00Z"}'
    plan = {"schema": "wd.q4-fixed-plan.v1", "backend": "synthetic-v1",
            "interpreter_sha256": sha(exe.read_bytes()),
            "peer_code_sha256": sha(Path(transport.__file__).read_bytes()),
            "runtime_manifest_sha256": sha(manifest), "bundle_sha256": sha(bundle), "bundle_bytes": len(bundle)}
    plan_raw = json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    observed, original = [], transport._create_process_in_job
    def capture(*args, **kwargs):
        result = original(*args, **kwargs)
        kernel = args[0]
        current = _winapi.GetCurrentProcess()
        observer = _winapi.DuplicateHandle(current, int(result[0]), current, 0, False, _winapi.DUPLICATE_SAME_ACCESS)
        observed.append((result[2], transport._created(kernel, int(result[0])), observer))
        return result
    monkeypatch.setattr(transport, "_create_process_in_job", capture)
    try:
        answer, code = transport.run_fixed_peer(executable=sys.executable, expected_interpreter_sha256=sha(exe.read_bytes()),
            interpreter_max_bytes=exe.stat().st_size, plan_bytes=plan_raw, expected_plan_sha256=sha(plan_raw),
            bundle_bytes=bundle, runtime_manifest_bytes=manifest, query_bytes=query, cwd=str(tmp_path),
            timeout_seconds=20, max_output_bytes=4096, _owner_guard=guard, _owner_context=ctx)
        assert code == 0 and json.loads(answer)["status"] == "SYNTHETIC"
        assert len(observed) == 1
        _pid, creation, handle = observed[0]
        kernel, _ = transport._api()
        assert transport._created(kernel, handle) == creation
        assert _winapi.WaitForSingleObject(handle, 0) == _winapi.WAIT_OBJECT_0
        print(f"synthetic_fixed_peer_launcher_pid={os.getpid()} child_pid={_pid} creation={creation} wait_raw=0")
        assert guard._registry._done == {"attestation", "transport"} and not guard._registry._uncertain
        assert marker.exists()
        with pytest.raises(guard_api.GuardCompletionUnavailable):
            guard._registry._issue()  # No adapter or outer-call proof is manufactured.
    finally:
        for _pid, _creation, handle in observed:
            _winapi.CloseHandle(handle)


def test_public_wrapper_aborts_guard_if_dispatch_fails_before_lower_entry(tmp_path, monkeypatch):
    guard, marker = new_guard(tmp_path / "one")
    ctx = guard._owner_context()
    def fail_dispatch(*args, **kwargs):
        raise RuntimeError("synthetic pre-entry failure")
    monkeypatch.setattr(transport, "_run_fixed_peer_impl", fail_dispatch)
    with pytest.raises(transport._Refused):
        transport.run_fixed_peer(**kwargs(tmp_path), _owner_guard=guard, _owner_context=ctx)
    assert guard._registry._uncertain and marker.exists()
    with pytest.raises(guard_api.GuardCompletionUnavailable):
        guard._registry._issue()


def test_public_wrapper_falls_back_to_guard_unknown_if_abort_raises(tmp_path, monkeypatch):
    guard, marker = new_guard(tmp_path / "one")
    ctx = guard._owner_context()
    monkeypatch.setattr(transport, "_run_fixed_peer_impl", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(guard_api._OwnerContext, "_abort", lambda self: (_ for _ in ()).throw(RuntimeError()))
    with pytest.raises(transport.TransportCleanupUnknown):
        transport.run_fixed_peer(**kwargs(tmp_path), _owner_guard=guard, _owner_context=ctx)
    assert guard._registry._uncertain and marker.exists()


@pytest.mark.parametrize("kind", ["interrupt", "exit", "base_group"])
def test_guarded_wrapper_keeps_interruption_signal_without_sensitive_context(tmp_path, monkeypatch, kind):
    guard, marker = new_guard(tmp_path / "one")
    ctx = guard._owner_context()
    def fail_dispatch(*args, **kwargs):
        if kind == "interrupt":
            raise KeyboardInterrupt()
        if kind == "exit":
            raise SystemExit(77)
        raise BaseExceptionGroup("synthetic", [KeyboardInterrupt(), ValueError("synthetic")])
    monkeypatch.setattr(transport, "_run_fixed_peer_impl", fail_dispatch)
    expected = KeyboardInterrupt if kind == "interrupt" else SystemExit if kind == "exit" else BaseExceptionGroup
    with pytest.raises(expected) as raised:
        transport.run_fixed_peer(**kwargs(tmp_path), _owner_guard=guard, _owner_context=ctx)
    assert raised.value.__context__ is None and raised.value.__cause__ is None
    if kind == "exit":
        assert raised.value.code == 77
    assert guard._registry._uncertain and marker.exists()


@pytest.mark.parametrize("failure", ["attest_open", "attest_close", "transport_unknown", "bad_output"])
def test_lower_failures_mark_whole_context_unknown(tmp_path, monkeypatch, failure):
    """Simulated leases/transport only; observes state transitions, not OS resources."""
    from tools import wd_q4_fixed_parent_attestation as attestation
    guard, marker = new_guard(tmp_path / "one")
    ctx = guard._owner_context()
    query, bundle, manifest, plan_raw = b"query", b"bundle", b"manifest", b"plan"
    sha = lambda value: hashlib.sha256(value).hexdigest()
    plan = {"backend": "synthetic-v1", "interpreter_sha256": "a" * 64,
            "peer_code_sha256": sha(Path(transport.__file__).read_bytes()),
            "runtime_manifest_sha256": sha(manifest), "bundle_bytes": len(bundle),
            "bundle_sha256": sha(bundle)}
    monkeypatch.setattr(transport, "_fixed_plan", lambda raw: plan)
    monkeypatch.setattr(attestation, "_QUARANTINED", [])
    opened, ran = [], []
    class Kernel:
        def CloseHandle(self, handle):
            return failure != "attest_close"
    def fake_attest(path, expected, *, max_bytes, enabled=False):
        if failure == "attest_open":
            raise attestation.AttestationRefused("attestation_refused")
        opened.append(len(opened) + 100)
        return attestation.HeldFile(Kernel(), opened[-1], "c" * 64, 1)
    monkeypatch.setattr(attestation, "attest_file", fake_attest)
    def fake_run(command, cwd, metadata, payload, timeout_seconds, max_output_bytes, fixed=None, *, transport_owner=None):
        ran.append(transport_owner)
        assert transport_owner is ctx._ticket("transport")
        if failure == "transport_unknown":
            raise transport.TransportCleanupUnknown("timeout")
        if failure == "bad_output":
            return b"bad-frame", 0
        return transport._fixed_wrap(fixed[0]["nonce"], payload, b"result"), 0
    monkeypatch.setattr(transport, "_run", fake_run)
    with pytest.raises(Exception):
        transport._run_fixed_peer_impl(sys.executable, "a" * 64, 1024, plan_raw, sha(plan_raw), bundle,
            manifest, query, str(tmp_path), 1, 1024, _owner_guard=guard, _owner_context=ctx)
    assert guard._registry._uncertain and marker.exists()
    assert len(ran) == (0 if failure == "attest_open" else 1)
    with pytest.raises(guard_api.GuardCompletionUnavailable):
        guard._registry._issue()
