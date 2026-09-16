"""Offline tests for tools/run_solver_trace_magma_receipt_proof.py.

Runs the real (opt-in, temp-only) MAGMA receipt-bound solver-trace proof once,
then applies the rco-2 fail-open checklist as forges: every binding/verifier
field and the determinism check must fail the proof CLOSED when the inner proof
result degrades (derived-not-hardcoded, per-field forge).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "run_solver_trace_magma_receipt_proof",
    REPO_ROOT / "tools" / "run_solver_trace_magma_receipt_proof.py",
)
mod = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(mod)  # type: ignore[union-attr]


def _good_inner() -> dict:
    return {
        "proof_id": "solver_trace_runtime_receipt_v1",
        "ok": True,
        "blockers": [],
        "receipt_scope": "opt_in_handle_query_runtime_summary",
        "receipt_count": 1,
        "verifier_ok": True,
        "solver_call_trace_count": 1,
        "solver_call_trace_digest_bound": True,
        "solver_call_trace_receipt_bound": True,
        "solver_call_trace_privacy_safe": True,
        "raw_payload_leak_check": True,
        "external_writes_applied": False,
        "default_sink_required": False,
        "temp_artifacts_removed": True,
    }


def test_real_proof_ok_and_deterministic():
    report = mod.build_solver_trace_magma_receipt_standalone_proof()
    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["deterministic_replay"]["stable_evidence_identical"] is True
    re_ = report["receipt_evidence"]
    assert re_["solver_call_trace_receipt_bound"] is True
    assert re_["solver_call_trace_digest_bound"] is True
    assert re_["verifier_ok"] is True
    eva = report["evidence_vs_authority"]
    assert eva["evidence_present"] is True
    assert eva["runtime_authority_granted"] is False


def test_invariants_and_clean_summary():
    report = mod.build_solver_trace_magma_receipt_standalone_proof()
    inv = report["invariants"]
    for flag in ("deterministic_offline", "opt_in_temp_only",
                 "no_runtime_authority_flip", "no_external_writes"):
        assert inv[flag] is True
    summary = mod.render_summary(report)
    mod.assert_vocabulary_clean(summary)


@pytest.mark.parametrize("field,blocker", [
    ("ok", "inner_proof_not_ok"),
    ("solver_call_trace_receipt_bound", "solver_trace_not_receipt_bound"),
    ("solver_call_trace_digest_bound", "solver_trace_not_digest_bound"),
    ("verifier_ok", "receipt_verifier_failed"),
    ("solver_call_trace_privacy_safe", "trace_not_privacy_safe"),
    ("raw_payload_leak_check", "raw_payload_leak_check_failed"),
])
def test_forge_each_binding_field_fails_closed(monkeypatch, field, blocker):
    # Note privacy fields: inner ok=True with privacy False must STILL fail
    # closed (independent gate, not relying on inner ok).
    bad = _good_inner()
    bad[field] = False
    monkeypatch.setattr(mod, "build_solver_trace_magma_receipt_proof", lambda: dict(bad))
    report = mod.build_solver_trace_magma_receipt_standalone_proof()
    assert report["ok"] is False
    assert blocker in report["blockers"]
    assert report["evidence_vs_authority"]["evidence_present"] is False


@pytest.mark.parametrize("field,value,blocker", [
    ("receipt_count", 0, "receipt_count_not_positive"),
    ("receipt_count", True, "receipt_count_not_positive"),
    ("solver_call_trace_count", 0, "solver_call_trace_count_not_positive"),
    ("solver_call_trace_count", True, "solver_call_trace_count_not_positive"),
    ("receipt_scope", "production_default_sink", "unexpected_receipt_scope"),
    ("runtime_authority_granted", True, "runtime_authority_granted"),
    ("external_writes_applied", True, "external_writes_applied"),
    ("temp_artifacts_removed", False, "temp_artifacts_not_removed"),
])
def test_forge_count_scope_authority_fields_fail_closed(monkeypatch, field, value, blocker):
    # Even with inner ok=True + all bindings true, an invalid count, wrong
    # scope, or authority/leak flag must fail closed (no hardcoded "safe").
    bad = _good_inner()
    bad[field] = value
    monkeypatch.setattr(mod, "build_solver_trace_magma_receipt_proof", lambda: dict(bad))
    report = mod.build_solver_trace_magma_receipt_standalone_proof()
    assert report["ok"] is False, field
    assert blocker in report["blockers"], field
    assert report["evidence_vs_authority"]["evidence_present"] is False, field


def test_authority_fields_are_derived_not_hardcoded(monkeypatch):
    # An inner result reporting runtime authority / external writes must surface
    # those in evidence_vs_authority + flip the invariants (derived, not hardcoded).
    bad = {**_good_inner(), "runtime_authority_granted": True, "external_writes_applied": True}
    monkeypatch.setattr(mod, "build_solver_trace_magma_receipt_proof", lambda: dict(bad))
    report = mod.build_solver_trace_magma_receipt_standalone_proof()
    eva = report["evidence_vs_authority"]
    assert eva["runtime_authority_granted"] is True
    assert eva["external_writes_applied"] is True
    assert report["invariants"]["no_runtime_authority_flip"] is False
    assert report["invariants"]["no_external_writes"] is False


def test_forge_absent_privacy_field_fails_closed(monkeypatch):
    # A missing privacy verdict must fail closed (never assume absent == safe).
    bad = _good_inner()
    bad.pop("solver_call_trace_privacy_safe")
    monkeypatch.setattr(mod, "build_solver_trace_magma_receipt_proof", lambda: dict(bad))
    report = mod.build_solver_trace_magma_receipt_standalone_proof()
    assert report["ok"] is False
    assert "trace_not_privacy_safe" in report["blockers"]


def test_forge_nondeterministic_fails_closed(monkeypatch):
    seq = [_good_inner(), {**_good_inner(), "receipt_count": 2}]

    def _drift():
        return seq.pop(0) if seq else _good_inner()

    monkeypatch.setattr(mod, "build_solver_trace_magma_receipt_proof", _drift)
    report = mod.build_solver_trace_magma_receipt_standalone_proof()
    assert report["ok"] is False
    assert "non_deterministic_receipt_evidence" in report["blockers"]


@pytest.mark.parametrize("field,blocker", [
    ("receipt_count", "receipt_count_not_positive"),
    ("solver_call_trace_count", "solver_call_trace_count_not_positive"),
])
def test_forge_replay_count_int_subclass_fails_closed(monkeypatch, field, blocker):
    class IntSubclass(int):
        pass

    seq = [_good_inner(), {**_good_inner(), field: IntSubclass(1)}]

    def _drift():
        return seq.pop(0) if seq else _good_inner()

    monkeypatch.setattr(mod, "build_solver_trace_magma_receipt_proof", _drift)
    report = mod.build_solver_trace_magma_receipt_standalone_proof()
    assert report["deterministic_replay"]["stable_evidence_identical"] is True
    assert report["ok"] is False, field
    assert blocker in report["blockers"], field
    assert report["evidence_vs_authority"]["evidence_present"] is False, field


@pytest.mark.parametrize("drift_field,drift_value", [
    ("runtime_authority_granted", True),
    ("external_writes_applied", True),
    ("default_sink_required", True),
    ("temp_artifacts_removed", False),
    ("receipt_scope", "production_default_sink"),
])
def test_forge_run2_drift_on_any_gated_field_fails_closed(monkeypatch, drift_field, drift_value):
    # run1 clean, run2 flips a gated field: because every gated field is in the
    # stable replay set, the determinism check must catch the run2 drift (so a
    # second-run authority/scope leak can't slip past a run1-only gate).
    seq = [_good_inner(), {**_good_inner(), drift_field: drift_value}]

    def _drift():
        return seq.pop(0) if seq else _good_inner()

    monkeypatch.setattr(mod, "build_solver_trace_magma_receipt_proof", _drift)
    report = mod.build_solver_trace_magma_receipt_standalone_proof()
    assert report["ok"] is False, drift_field
    assert "non_deterministic_receipt_evidence" in report["blockers"], drift_field


def test_main_json_exit0():
    assert mod.main(["--json"]) == 0


def test_out_dir_writes_artifact(tmp_path):
    out = tmp_path / "proof_out"
    assert mod.main(["--out-dir", str(out)]) == 0
    artifact = out / "solver_trace_magma_receipt_proof.json"
    assert artifact.is_file()
    assert json.loads(artifact.read_text(encoding="utf-8"))["ok"] is True
