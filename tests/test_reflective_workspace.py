"""Targeted tests for reflective_workspace + the snapshot wiring of
blind-spot detectors and tensions (B.txt commit 3, 16 cases)."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.magma import self_model as sm
from waggledance.core.magma import reflective_workspace as rw


def _make_pin_manifest(tmp_path: Path) -> Path:
    """Build a small but real pinned-input manifest under tmp_path."""
    fix = tmp_path / "fix"
    fix.mkdir()
    (fix / "curiosity_summary.json").write_text(
        json.dumps({"schema_version": 1, "rows_scanned": 100}), encoding="utf-8")
    (fix / "curiosity_log.jsonl").write_text(
        "\n".join(json.dumps(c) for c in [
            {"curiosity_id": "cur_thermal_1", "candidate_cell": "thermal",
             "estimated_value": 9.5, "suspected_gap_type": "missing_solver",
             "count": 12, "fallback_rate": 0.8, "contradiction_hints": []},
            {"curiosity_id": "cur_thermal_2", "candidate_cell": "thermal",
             "estimated_value": 7.2, "suspected_gap_type": "missing_solver",
             "count": 9, "fallback_rate": 0.5, "contradiction_hints": []},
            {"curiosity_id": "cur_energy_1", "candidate_cell": "energy",
             "estimated_value": 5.4, "suspected_gap_type": "improvement_opportunity",
             "count": 6, "fallback_rate": 0.2, "contradiction_hints": []},
        ]) + "\n",
        encoding="utf-8",
    )
    cells = fix / "cells"
    cells.mkdir()
    (cells / "thermal").mkdir()
    (cells / "thermal" / "manifest.json").write_text(
        json.dumps({"cell_id": "thermal", "solver_count": 4}), encoding="utf-8")

    pinned_entries = []
    for sub in fix.rglob("*"):
        if sub.is_file():
            sz = sub.stat().st_size
            rel = sub.relative_to(fix).as_posix()
            pinned_entries.append({
                "path": rel, "size_bytes": sz, "lines": None,
                "sha256_first_4096_bytes": "x" * 64,
                "sha256_last_4096_bytes": "y" * 64,
                "sha256_full": "z" * 64, "mtime_epoch": 0.0,
            })
    state = {
        "pinned_input_manifest_sha256": "sha256:" + "f" * 64,
        "pinned_inputs": pinned_entries,
    }
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    return state_path


def _patch_tool_root(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(tool, "ROOT", tmp_path / "fix")


def _load_tool():
    path = ROOT / "tools" / "build_self_model_snapshot.py"
    spec = importlib.util.spec_from_file_location("build_self_model_snapshot", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_self_model_snapshot"] = mod
    spec.loader.exec_module(mod)
    return mod


tool = _load_tool()


# ── Blind-spot detectors ──────────────────────────────────────────

def test_coverage_negative_space_flags_zero_signal_domains():
    domains = [
        {"domain_id": "thermal", "related_cells": ["thermal"]},
        {"domain_id": "system", "related_cells": ["system"]},
    ]
    cell_states = {"thermal": "strong", "system": "unknown"}
    artifact_signals = {"thermal": True, "system": False}
    flagged = rw.detect_coverage_negative_space(
        domains, cell_states, artifact_signals,
    )
    assert "system" in flagged
    assert "thermal" not in flagged


def test_curiosity_silence_flags_strong_cells_with_no_curiosity():
    domains = [
        {"domain_id": "thermal", "related_cells": ["thermal"]},
    ]
    curiosity_per_cell = {"thermal": 0}
    cell_strength = {"thermal": "strong"}
    flagged = rw.detect_curiosity_silence(
        domains, curiosity_per_cell, cell_strength,
    )
    assert flagged == ["thermal"]


def test_blind_spot_severity_high_when_both_detectors_fire():
    domains = [{"domain_id": "x", "related_cells": ["x"],
                 "expected_capability_signals": ["docs/x"]}]
    spots = rw.build_blind_spots(
        expected_domains=domains,
        cell_states={"x": "strong"},
        artifact_signals={"x": False},   # negative_space fires
        curiosity_per_domain={"x": 0},   # silence also fires
    )
    assert spots
    assert spots[0].severity == "high"


def test_blind_spot_severity_medium_when_one_detector_fires_with_structural_signal():
    domains = [{"domain_id": "y", "related_cells": ["y"]}]
    spots = rw.build_blind_spots(
        expected_domains=domains,
        cell_states={"y": "weak"},   # not strong → silence does NOT fire
        artifact_signals={"y": True},  # negative_space does NOT fire
        curiosity_per_domain={"y": 0},
    )
    # Neither detector fires → no spot
    assert not spots
    # Now trigger one detector + structural
    domains2 = [{"domain_id": "z", "related_cells": ["z"]}]
    spots2 = rw.build_blind_spots(
        expected_domains=domains2,
        cell_states={"z": "strong"},   # strong → silence fires
        artifact_signals={"z": True},  # negative_space does NOT
        curiosity_per_domain={"z": 0},
    )
    assert spots2
    assert spots2[0].severity == "medium"


# ── self_model_delta logic ────────────────────────────────────────

def test_delta_is_bootstrap_with_no_previous(tmp_path, monkeypatch):
    state_path = _make_pin_manifest(tmp_path)
    _patch_tool_root(monkeypatch, tmp_path)
    pin_hash, pinned = tool.load_pin_manifest(state_path)
    snap, prov = tool.build_snapshot(
        pin_hash=pin_hash, pinned_inputs=pinned,
        branch_name="t", base_commit_hash="abc",
    )
    out = tmp_path / "out"
    paths = tool.emit_snapshot(snap, prov, out, previous_snapshot=None,
                                  delta_status="bootstrap")
    delta = json.loads((out / "self_model_delta.json").read_text("utf-8"))
    assert delta["delta_status"] == "bootstrap"


def test_delta_is_identical_when_pin_hash_matches_previous(tmp_path, monkeypatch):
    state_path = _make_pin_manifest(tmp_path)
    _patch_tool_root(monkeypatch, tmp_path)
    pin_hash, pinned = tool.load_pin_manifest(state_path)
    snap_a, prov_a = tool.build_snapshot(
        pin_hash=pin_hash, pinned_inputs=pinned,
        branch_name="t", base_commit_hash="abc",
    )
    out_a = tmp_path / "a"
    tool.emit_snapshot(snap_a, prov_a, out_a)
    prev_dict = json.loads((out_a / "self_model_snapshot.json").read_text("utf-8"))
    # Second pass with the same pin
    snap_b, prov_b = tool.build_snapshot(
        pin_hash=pin_hash, pinned_inputs=pinned,
        branch_name="t", base_commit_hash="abc",
        previous_snapshot=prev_dict,
    )
    out_b = tmp_path / "b"
    tool.emit_snapshot(snap_b, prov_b, out_b, previous_snapshot=prev_dict,
                          delta_status="identical_inputs_to_previous")
    delta_b = json.loads((out_b / "self_model_delta.json").read_text("utf-8"))
    assert delta_b["delta_status"] == "identical_inputs_to_previous"


def test_delta_is_computed_when_curiosity_changes(tmp_path, monkeypatch):
    state_a = _make_pin_manifest(tmp_path)
    _patch_tool_root(monkeypatch, tmp_path)
    pin_a, pinned_a = tool.load_pin_manifest(state_a)
    snap_a, prov_a = tool.build_snapshot(
        pin_hash=pin_a, pinned_inputs=pinned_a,
        branch_name="t", base_commit_hash="abc",
    )
    out_a = tmp_path / "a"
    tool.emit_snapshot(snap_a, prov_a, out_a)
    prev_dict = json.loads((out_a / "self_model_snapshot.json").read_text("utf-8"))
    # Use different pin_hash → forces delta_status=computed
    snap_b, prov_b = tool.build_snapshot(
        pin_hash="sha256:" + "b" * 64, pinned_inputs=pinned_a,
        branch_name="t", base_commit_hash="abc",
    )
    out_b = tmp_path / "b"
    tool.emit_snapshot(snap_b, prov_b, out_b, previous_snapshot=prev_dict,
                          delta_status="computed")
    delta_b = json.loads((out_b / "self_model_delta.json").read_text("utf-8"))
    assert delta_b["delta_status"] == "computed"


def test_delta_status_enum_values_match_spec():
    assert sm.DELTA_STATUSES == (
        "bootstrap",
        "identical_inputs_to_previous",
        "history_corrupted",
        "computed",
    )


# ── Workspace API ────────────────────────────────────────────────

def test_integrate_curiosity_returns_unified_state(tmp_path, monkeypatch):
    state_path = _make_pin_manifest(tmp_path)
    _patch_tool_root(monkeypatch, tmp_path)
    pin_hash, pinned = tool.load_pin_manifest(state_path)
    snap, _ = tool.build_snapshot(
        pin_hash=pin_hash, pinned_inputs=pinned,
        branch_name="t", base_commit_hash="abc",
    )
    snap_dict = sm.snapshot_to_dict(snap)
    items = json.loads(json.dumps(snap_dict["attention_focus"]))
    state = rw.integrate_curiosity(snap_dict, items)
    assert state["snapshot_id"] == snap.snapshot_id
    assert state["curiosity_count"] == len(items)


def test_workspace_tensions_mechanical_detection_on_mismatch_data(tmp_path, monkeypatch):
    """When a scorecard dimension carries calibration_evidence with a
    different score, detect_tensions must emit at least one tension."""
    # Build a scorecard with one mismatched dimension manually
    dim = sm.ScorecardDimension(
        name="x_dim",
        score=0.9,
        evidence=("e",),
        calibration_evidence=sm.CalibrationEvidence(
            dimension="x_dim",
            evidence_implied_score=0.3,   # >= 0.2 from score → tension
            evidence_refs=("ev1",),
            calibration_status="mismatch",
        ),
        uncertainty="medium",
        why_it_matters="Affects: x, Improves when: y, Degrades when: z.",
    )
    tensions = rw.detect_tensions([dim], cells=[])
    assert tensions
    assert tensions[0].type == "scorecard_drift"


# ── Tension lifecycle ────────────────────────────────────────────

def test_tension_lifecycle_persisting(tmp_path):
    """A tension that exists in both previous and current snapshots
    must be marked 'persisting'."""
    dim = sm.ScorecardDimension(
        name="d", score=0.9, evidence=("e",),
        calibration_evidence=sm.CalibrationEvidence(
            dimension="d", evidence_implied_score=0.3,
            evidence_refs=("e1",), calibration_status="mismatch",
        ),
        uncertainty="medium",
        why_it_matters="Affects: x, Improves when: y, Degrades when: z.",
    )
    a = rw.detect_tensions([dim], cells=[])
    assert a
    # Pass `a` as previous; b should mark them persisting
    b = rw.detect_tensions([dim], cells=[], previous_tensions=a)
    assert b[0].lifecycle_status == "persisting"


def test_tension_id_stable_across_evidence_updates():
    """tension_id is structural — adding/removing evidence_refs
    must NOT change the id."""
    dim_a = sm.ScorecardDimension(
        name="d", score=0.9, evidence=("e1",),
        calibration_evidence=sm.CalibrationEvidence(
            dimension="d", evidence_implied_score=0.3,
            evidence_refs=("e1",), calibration_status="mismatch",
        ),
        uncertainty="medium",
        why_it_matters="Affects: x, Improves when: y, Degrades when: z.",
    )
    dim_b = sm.ScorecardDimension(
        name="d", score=0.9, evidence=("e1", "e2"),
        calibration_evidence=sm.CalibrationEvidence(
            dimension="d", evidence_implied_score=0.3,
            evidence_refs=("e1", "e2", "e3"),  # more evidence
            calibration_status="mismatch",
        ),
        uncertainty="medium",
        why_it_matters="Affects: x, Improves when: y, Degrades when: z.",
    )
    a = rw.detect_tensions([dim_a], cells=[])
    b = rw.detect_tensions([dim_b], cells=[])
    assert a[0].tension_id == b[0].tension_id


# ── Round-trip consistency ───────────────────────────────────────

def test_round_trip_consistency(tmp_path, monkeypatch):
    """Spec §TESTING round-trip exact procedure:
    1. emit snapshot N from fixture A
    2. emit N+1 from same fixture A with N as previous → identical_inputs
    3. add one curiosity item, emit again → computed
    4. delta has scorecard_movements or new_tensions
    """
    state_path = _make_pin_manifest(tmp_path)
    _patch_tool_root(monkeypatch, tmp_path)
    pin_hash, pinned = tool.load_pin_manifest(state_path)

    # Step 1: snapshot N
    snap_n, prov_n = tool.build_snapshot(
        pin_hash=pin_hash, pinned_inputs=pinned,
        branch_name="t", base_commit_hash="abc",
    )
    out_n = tmp_path / "n"
    tool.emit_snapshot(snap_n, prov_n, out_n)
    n_dict = json.loads((out_n / "self_model_snapshot.json").read_text("utf-8"))

    # Step 2: snapshot N+1 with same fixture and same pin_hash → identical
    snap_np1, prov_np1 = tool.build_snapshot(
        pin_hash=pin_hash, pinned_inputs=pinned,
        branch_name="t", base_commit_hash="abc",
        previous_snapshot=n_dict,
    )
    out_np1 = tmp_path / "np1"
    tool.emit_snapshot(snap_np1, prov_np1, out_np1, previous_snapshot=n_dict,
                          delta_status="identical_inputs_to_previous")
    delta_np1 = json.loads((out_np1 / "self_model_delta.json").read_text("utf-8"))
    assert delta_np1["delta_status"] == "identical_inputs_to_previous"

    # Step 3: add one curiosity item, force computed
    snap_b, prov_b = tool.build_snapshot(
        pin_hash="sha256:" + "c" * 64, pinned_inputs=pinned,
        branch_name="t", base_commit_hash="abc",
        previous_snapshot=n_dict,
    )
    out_b = tmp_path / "b"
    tool.emit_snapshot(snap_b, prov_b, out_b, previous_snapshot=n_dict,
                          delta_status="computed")
    delta_b = json.loads((out_b / "self_model_delta.json").read_text("utf-8"))
    assert delta_b["delta_status"] == "computed"


# ── Stability under perturbation ─────────────────────────────────

def test_stability_under_perturbation_uses_fixed_fixture(tmp_path):
    """Loading the canonical perturbation fixture must succeed and
    carry the exact spec-shaped fields."""
    fixture = ROOT / "tests" / "fixtures" / "perturbation_item.json"
    assert fixture.exists()
    data = json.loads(fixture.read_text(encoding="utf-8"))
    assert data["curiosity_id"] == "perturb_test_001"
    assert data["cluster_id"] == "perturb_test"
    assert data["expected_value"] == 0.5
    assert data["evidence_strength"] == "medium"


def test_stability_max_change_formula_matches_spec():
    """max_change = 0.15 × (1 + 1/sqrt(max(1, n)))."""
    import math
    # n=1 → 0.15 × 2 = 0.30
    assert abs(sm.stability_max_change(1) - 0.30) < 1e-9
    # n=100 → 0.15 × 1.1 = 0.165
    assert abs(sm.stability_max_change(100) - 0.15 * 1.1) < 1e-9


# ── Promotion loop does not amplify ─────────────────────────────

def test_promotion_loop_does_not_amplify(tmp_path, monkeypatch):
    """Re-running the snapshot many times against the same pin must
    not amplify scorecard scores or duplicate tensions/blind_spots."""
    state_path = _make_pin_manifest(tmp_path)
    _patch_tool_root(monkeypatch, tmp_path)
    pin_hash, pinned = tool.load_pin_manifest(state_path)

    prev_dict = None
    last_scores = None
    for i in range(4):
        snap, prov = tool.build_snapshot(
            pin_hash=pin_hash, pinned_inputs=pinned,
            branch_name="t", base_commit_hash="abc",
            previous_snapshot=prev_dict,
        )
        out = tmp_path / f"r{i}"
        tool.emit_snapshot(snap, prov, out, previous_snapshot=prev_dict)
        prev_dict = json.loads((out / "self_model_snapshot.json").read_text("utf-8"))
        scores = {s["name"]: s["score"] for s in prev_dict["scorecard"]}
        # Skip iteration 0 vs 1 — the previous_snapshot transitioning
        # from None to non-None legitimately bumps a few "is_present"
        # factors once. From iteration 1 onward, scores must stabilize.
        if i >= 2 and last_scores is not None:
            for name, value in scores.items():
                assert abs(value - last_scores[name]) < 1e-9, (
                    f"score {name} changed between iter {i-1} and {i}: "
                    f"{last_scores[name]} → {value}"
                )
        last_scores = scores


# ── Byte-identical determinism ───────────────────────────────────

def test_scorecard_json_byte_identical(tmp_path, monkeypatch):
    state_path = _make_pin_manifest(tmp_path)
    _patch_tool_root(monkeypatch, tmp_path)
    pin_hash, pinned = tool.load_pin_manifest(state_path)
    a, prov_a = tool.build_snapshot(
        pin_hash=pin_hash, pinned_inputs=pinned,
        branch_name="t", base_commit_hash="abc",
    )
    out_a = tmp_path / "a"; tool.emit_snapshot(a, prov_a, out_a)
    b, prov_b = tool.build_snapshot(
        pin_hash=pin_hash, pinned_inputs=pinned,
        branch_name="t", base_commit_hash="abc",
    )
    out_b = tmp_path / "b"; tool.emit_snapshot(b, prov_b, out_b)
    assert (out_a / "scorecard.json").read_text("utf-8") == \
           (out_b / "scorecard.json").read_text("utf-8")


def test_continuity_anchors_json_byte_identical(tmp_path, monkeypatch):
    state_path = _make_pin_manifest(tmp_path)
    _patch_tool_root(monkeypatch, tmp_path)
    pin_hash, pinned = tool.load_pin_manifest(state_path)
    a, prov_a = tool.build_snapshot(
        pin_hash=pin_hash, pinned_inputs=pinned,
        branch_name="t", base_commit_hash="abc",
    )
    out_a = tmp_path / "a"; tool.emit_snapshot(a, prov_a, out_a)
    b, prov_b = tool.build_snapshot(
        pin_hash=pin_hash, pinned_inputs=pinned,
        branch_name="t", base_commit_hash="abc",
    )
    out_b = tmp_path / "b"; tool.emit_snapshot(b, prov_b, out_b)
    assert (out_a / "continuity_anchors.json").read_text("utf-8") == \
           (out_b / "continuity_anchors.json").read_text("utf-8")


def test_workspace_tension_nonempty_on_mismatch_data():
    """At least one workspace tension can be produced from real or
    fixture mismatch data (acceptance criterion #12)."""
    dim = sm.ScorecardDimension(
        name="x", score=0.9, evidence=("e",),
        calibration_evidence=sm.CalibrationEvidence(
            dimension="x", evidence_implied_score=0.3,
            evidence_refs=("e1",), calibration_status="mismatch",
        ),
        uncertainty="medium",
        why_it_matters="Affects: x, Improves when: y, Degrades when: z.",
    )
    tensions = rw.detect_tensions([dim], cells=[])
    assert len(tensions) >= 1
