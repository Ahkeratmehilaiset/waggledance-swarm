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
])
def test_forge_each_binding_field_fails_closed(monkeypatch, field, blocker):
    bad = _good_inner()
    bad[field] = False
    monkeypatch.setattr(mod, "build_solver_trace_magma_receipt_proof", lambda: dict(bad))
    report = mod.build_solver_trace_magma_receipt_standalone_proof()
    assert report["ok"] is False
    assert blocker in report["blockers"]
    assert report["evidence_vs_authority"]["evidence_present"] is False


def test_forge_nondeterministic_fails_closed(monkeypatch):
    seq = [_good_inner(), {**_good_inner(), "receipt_count": 2}]

    def _drift():
        return seq.pop(0) if seq else _good_inner()

    monkeypatch.setattr(mod, "build_solver_trace_magma_receipt_proof", _drift)
    report = mod.build_solver_trace_magma_receipt_standalone_proof()
    assert report["ok"] is False
    assert "non_deterministic_receipt_evidence" in report["blockers"]


def test_main_json_exit0():
    assert mod.main(["--json"]) == 0


def test_out_dir_writes_artifact(tmp_path):
    out = tmp_path / "proof_out"
    assert mod.main(["--out-dir", str(out)]) == 0
    artifact = out / "solver_trace_magma_receipt_proof.json"
    assert artifact.is_file()
    assert json.loads(artifact.read_text(encoding="utf-8"))["ok"] is True
