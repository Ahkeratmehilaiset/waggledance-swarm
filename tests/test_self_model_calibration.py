"""Targeted tests for calibration corrections, history-chain
validation, invariants/ruptures, why_it_matters regex, and
expected_domains schema (B.txt commit 4, 12 cases)."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.magma import self_model as sm


def _load_tool():
    path = ROOT / "tools" / "build_self_model_snapshot.py"
    spec = importlib.util.spec_from_file_location("build_self_model_snapshot_b4", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_self_model_snapshot_b4"] = mod
    spec.loader.exec_module(mod)
    return mod


tool = _load_tool()


# ── 1. calibration_evidence generation ───────────────────────────

def test_calibration_evidence_dataclass_shape():
    ev = sm.CalibrationEvidence(
        dimension="x",
        evidence_implied_score=0.4,
        evidence_refs=("ev1", "ev2"),
        calibration_status="mismatch",
    )
    assert ev.dimension == "x"
    assert ev.evidence_refs == ("ev1", "ev2")


# ── 2. calibration_corrections emission ──────────────────────────

def test_calibration_correction_appends_to_jsonl(tmp_path):
    log = tmp_path / "calibration_corrections.jsonl"
    record = {
        "dimension": "x",
        "prior_score": 0.9,
        "evidence_implied_score": 0.4,
        "correction_magnitude": 0.5,
        "confidence": 0.8,
        "source_refs": ["ev1"],
        "correction_count_in_window": 0,
    }
    sm.append_calibration_correction(log, record)
    sm.append_calibration_correction(log, record)
    lines = [l for l in log.read_text("utf-8").splitlines() if l.strip()]
    assert len(lines) == 2
    parsed = [json.loads(l) for l in lines]
    assert all(p["dimension"] == "x" for p in parsed)


# ── 3. calibration oscillation damping/freeze ────────────────────

def test_adjusted_score_standard_mode():
    new, mode = sm.adjusted_score(prior_score=0.9,
                                     evidence_implied_score=0.3,
                                     correction_count_in_window=0)
    assert mode == "standard"
    assert abs(new - (0.7 * 0.3 + 0.3 * 0.9)) < 1e-9


def test_adjusted_score_dampened_at_three():
    new, mode = sm.adjusted_score(prior_score=0.9,
                                     evidence_implied_score=0.3,
                                     correction_count_in_window=3)
    assert mode == "dampened"
    assert abs(new - (0.5 * 0.3 + 0.5 * 0.9)) < 1e-9


def test_adjusted_score_frozen_at_five():
    new, mode = sm.adjusted_score(prior_score=0.9,
                                     evidence_implied_score=0.3,
                                     correction_count_in_window=5)
    assert mode == "frozen"
    assert new == 0.9   # prior preserved


# ── 4. oscillation window deterministic in test mode ─────────────

def test_correction_window_count_is_index_based(tmp_path):
    """Spec §B3: in tests, always last 10 by index. Verify the
    counter only counts the matching dimension, not all entries."""
    corrections = [
        {"dimension": "x"}, {"dimension": "x"}, {"dimension": "y"},
        {"dimension": "x"}, {"dimension": "y"}, {"dimension": "y"},
    ]
    n_x = sm.correction_count_for_dimension(corrections, "x")
    n_y = sm.correction_count_for_dimension(corrections, "y")
    n_z = sm.correction_count_for_dimension(corrections, "z")
    assert n_x == 3
    assert n_y == 3
    assert n_z == 0


# ── 5. continuity anchors invariants/ruptures structure ──────────

def test_invariant_dataclass_fields():
    inv = sm.Invariant(
        property_id="cell_state__thermal__strong",
        description="cell thermal held strong",
        held_for_snapshots=5,
        evidence_refs=("e1", "e2"),
    )
    assert inv.held_for_snapshots == 5
    assert inv.evidence_refs == ("e1", "e2")


def test_rupture_dataclass_fields():
    r = sm.Rupture(
        property_id="cell_state__thermal__strong",
        description="thermal broke strong → weak",
        held_for_snapshots=5,
        evidence_refs=(),
    )
    assert r.held_for_snapshots == 5


def test_detect_invariants_and_ruptures_with_stable_history():
    """Five snapshots all show thermal=strong → invariant fires."""
    history = [
        {"snapshot_id": f"snap_{i}",
         "cells": [{"cell_id": "thermal", "state": "strong"}]}
        for i in range(5)
    ]
    current = {"cells": [{"cell_id": "thermal", "state": "strong"}]}
    invs, rupts = sm.detect_invariants_and_ruptures(history, current)
    assert any(i.property_id.startswith("cell_state__thermal__strong")
                for i in invs)
    assert not rupts


def test_detect_invariants_and_ruptures_detects_rupture():
    """Five snapshots had thermal=strong, current has weak → rupture."""
    history = [
        {"snapshot_id": f"snap_{i}",
         "cells": [{"cell_id": "thermal", "state": "strong"}]}
        for i in range(5)
    ]
    current = {"cells": [{"cell_id": "thermal", "state": "weak"}]}
    invs, rupts = sm.detect_invariants_and_ruptures(history, current)
    assert any(r.property_id.startswith("cell_state__thermal__strong")
                for r in rupts)


# ── 6. why_it_matters regex format ───────────────────────────────

def test_why_it_matters_regex_accepts_correct_form():
    s = sm.make_why_it_matters(
        affects="thermal capability",
        improves_when="more solvers land",
        degrades_when="cell scope expands without solvers"
    )
    assert sm.WHY_IT_MATTERS_RE.match(s)


def test_why_it_matters_regex_rejects_wrong_form():
    """affects must not contain a comma (regex anchors break)."""
    with pytest.raises(ValueError):
        sm.make_why_it_matters(
            affects="thermal, with comma",
            improves_when="x",
            degrades_when="y"
        )

    # And the bare narrative form does not match
    bare = "Some narrative summary about thermal."
    assert not sm.WHY_IT_MATTERS_RE.match(bare)


# ── 7. history chain validation ──────────────────────────────────

def test_history_genesis_first_entry(tmp_path):
    p = tmp_path / "HISTORY.jsonl"
    e = sm.append_history_entry(p, {
        "snapshot_id": "s1",
        "base_commit_hash": "abc",
        "pinned_input_manifest_sha256": "sha256:x",
        "output_dir": "out",
    })
    assert e["prev_entry_sha256"] == sm.GENESIS_PREV_SHA


def test_history_chain_validates_genesis_then_normal(tmp_path):
    p = tmp_path / "HISTORY.jsonl"
    sm.append_history_entry(p, {"snapshot_id": "s1", "x": 1})
    sm.append_history_entry(p, {"snapshot_id": "s2", "x": 2})
    sm.append_history_entry(p, {"snapshot_id": "s3", "x": 3})
    entries = sm.read_history(p)
    assert len(entries) == 3
    ok, reason = sm.validate_history_chain(entries)
    assert ok, reason


def test_history_chain_detects_break(tmp_path):
    p = tmp_path / "HISTORY.jsonl"
    sm.append_history_entry(p, {"snapshot_id": "s1", "x": 1})
    sm.append_history_entry(p, {"snapshot_id": "s2", "x": 2})
    # Tamper with line 2's prev_entry_sha
    lines = p.read_text("utf-8").splitlines()
    second = json.loads(lines[1])
    second["prev_entry_sha256"] = "0" * 64   # would only be valid as line 1
    lines[1] = json.dumps(second)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    entries = sm.read_history(p)
    ok, reason = sm.validate_history_chain(entries)
    assert not ok


# ── 8. expected_domains.yaml schema ──────────────────────────────

def test_expected_domains_yaml_schema_validates():
    import yaml as _yaml
    p = ROOT / "docs" / "taxonomy" / "expected_domains.yaml"
    assert p.exists(), "expected_domains.yaml must exist for B.4"
    domains = _yaml.safe_load(p.read_text(encoding="utf-8"))
    ok, reason = sm.validate_expected_domains_yaml(domains)
    assert ok, reason


def test_expected_domains_validator_rejects_missing_required_keys():
    bad = [{"domain_id": "x"}]   # missing description, signals, cells
    ok, reason = sm.validate_expected_domains_yaml(bad)
    assert not ok


# ── 9. previous-snapshot validation against history ──────────────

def test_previous_snapshot_validation_via_history(tmp_path):
    """A previous snapshot whose snapshot_id appears in HISTORY.jsonl
    is valid; one whose id is absent is not."""
    p = tmp_path / "HISTORY.jsonl"
    sm.append_history_entry(p, {"snapshot_id": "s1", "data": 1})
    sm.append_history_entry(p, {"snapshot_id": "s2", "data": 2})
    entries = sm.read_history(p)
    known_ids = {e["snapshot_id"] for e in entries}
    assert "s2" in known_ids
    assert "missing_snap" not in known_ids


# ── 10. self_model_delta.json byte-identical determinism ─────────

def test_self_model_delta_json_byte_identical(tmp_path):
    snap_dict = {"snapshot_id": "s1",
                  "scorecard": [{"name": "x", "score": 0.5}],
                  "workspace_tensions": [], "blind_spots": []}
    a = tool._compute_delta(snap_dict, None, "bootstrap")
    b = tool._compute_delta(snap_dict, None, "bootstrap")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ── 11. data_provenance.json byte-identical determinism ──────────

def test_data_provenance_json_byte_identical(tmp_path, monkeypatch):
    fix = tmp_path / "fix"; fix.mkdir()
    (fix / "curiosity_log.jsonl").write_text(
        json.dumps({
            "curiosity_id": "c1", "candidate_cell": "thermal",
            "estimated_value": 5.0, "suspected_gap_type": "missing_solver",
            "count": 3, "fallback_rate": 0.5,
        }) + "\n", encoding="utf-8")
    pinned = [{
        "path": "curiosity_log.jsonl", "size_bytes": (fix / "curiosity_log.jsonl").stat().st_size,
        "sha256_full": "x" * 64, "sha256_first_4096_bytes": "x" * 64,
        "sha256_last_4096_bytes": "x" * 64, "mtime_epoch": 0.0, "lines": 1,
    }]
    monkeypatch.setattr(tool, "ROOT", fix)
    snap_a, prov_a = tool.build_snapshot(
        pin_hash="sha256:f", pinned_inputs=pinned,
        branch_name="t", base_commit_hash="abc",
    )
    snap_b, prov_b = tool.build_snapshot(
        pin_hash="sha256:f", pinned_inputs=pinned,
        branch_name="t", base_commit_hash="abc",
    )
    assert json.dumps(prov_a, sort_keys=True) == json.dumps(prov_b, sort_keys=True)


# ── 12. correction record minimal shape ──────────────────────────

def test_calibration_evidence_generated_for_dimensions(tmp_path, monkeypatch):
    """Acc #8: calibration_evidence exists for at least one dimension
    when data permits. After acceptance gap fix, all 7 dims carry
    a CalibrationEvidence (with status='ok' or 'mismatch' or
    'unavailable')."""
    fix = tmp_path / "fix"; fix.mkdir()
    (fix / "curiosity_log.jsonl").write_text(
        "\n".join(json.dumps({
            "curiosity_id": f"c{i}", "candidate_cell": "thermal",
            "estimated_value": 5.0, "suspected_gap_type": "missing_solver",
            "count": 5, "fallback_rate": 0.6,
        }) for i in range(8)) + "\n", encoding="utf-8")
    pinned = [{
        "path": "curiosity_log.jsonl",
        "size_bytes": (fix / "curiosity_log.jsonl").stat().st_size,
        "sha256_full": "x" * 64, "sha256_first_4096_bytes": "x" * 64,
        "sha256_last_4096_bytes": "x" * 64, "mtime_epoch": 0.0, "lines": 8,
    }]
    monkeypatch.setattr(tool, "ROOT", fix)
    snap, _ = tool.build_snapshot(
        pin_hash="sha256:f", pinned_inputs=pinned,
        branch_name="t", base_commit_hash="abc",
    )
    has_evidence = sum(1 for d in snap.scorecard if d.calibration_evidence is not None)
    assert has_evidence >= 1


def test_calibration_corrections_emitted_when_mismatch(tmp_path, monkeypatch):
    """Acc #9: calibration_corrections.jsonl is appended when
    abs(prior - evidence) > drift threshold."""
    # Build a snapshot, then call _emit_calibration_corrections directly
    fix = tmp_path / "fix"; fix.mkdir()
    (fix / "curiosity_log.jsonl").write_text(
        "\n".join(json.dumps({
            "curiosity_id": f"c{i}", "candidate_cell": "thermal",
            "estimated_value": 5.0, "suspected_gap_type": "missing_solver",
            "count": 5, "fallback_rate": 0.6,
        }) for i in range(8)) + "\n", encoding="utf-8")
    pinned = [{
        "path": "curiosity_log.jsonl",
        "size_bytes": (fix / "curiosity_log.jsonl").stat().st_size,
        "sha256_full": "x" * 64, "sha256_first_4096_bytes": "x" * 64,
        "sha256_last_4096_bytes": "x" * 64, "mtime_epoch": 0.0, "lines": 8,
    }]
    monkeypatch.setattr(tool, "ROOT", fix)
    snap, _ = tool.build_snapshot(
        pin_hash="sha256:f", pinned_inputs=pinned,
        branch_name="t", base_commit_hash="abc",
    )
    corrections_path = tmp_path / "corrections.jsonl"
    n = tool._emit_calibration_corrections(
        scorecard=snap.scorecard,
        corrections_path=corrections_path,
        previous_corrections=[],
    )
    # At least one dim should mismatch (unified_workspace_readiness=0.5
    # vs evidence implies 1.0 since curiosity+attention are present)
    assert n >= 1
    assert corrections_path.exists()
    # And value_layer_readiness must NEVER be in the corrections (per
    # spec §B3 exclusion).
    rows = sm.read_corrections(corrections_path)
    for r in rows:
        assert r["dimension"] != "value_layer_readiness"


def test_value_layer_readiness_excluded_from_auto_correction(tmp_path):
    """Spec §B3: value_layer_readiness MUST NOT generate a correction
    record even if calibration_evidence diverges."""
    # Build a fake scorecard with a deliberate mismatch on
    # value_layer_readiness
    dim = sm.ScorecardDimension(
        name="value_layer_readiness",
        score=0.1,
        evidence=("phase_8_5_intentional_floor",),
        calibration_evidence=sm.CalibrationEvidence(
            dimension="value_layer_readiness",
            evidence_implied_score=0.9,  # huge mismatch
            evidence_refs=("synthetic",),
            calibration_status="mismatch",
        ),
        uncertainty="high",
        why_it_matters="Affects: x, Improves when: y, Degrades when: z.",
    )
    corrections_path = tmp_path / "corrections.jsonl"
    n = tool._emit_calibration_corrections(
        scorecard=(dim,),
        corrections_path=corrections_path,
        previous_corrections=[],
    )
    assert n == 0
    assert not corrections_path.exists() or corrections_path.read_text("utf-8").strip() == ""


def test_correction_record_round_trip(tmp_path):
    p = tmp_path / "calibration_corrections.jsonl"
    record = {
        "dimension": "metacognition_maturity",
        "prior_score": 0.6,
        "evidence_implied_score": 0.2,
        "correction_magnitude": 0.4,
        "confidence": 0.9,
        "source_refs": ["blind_spot_count=0"],
        "correction_count_in_window": 1,
    }
    sm.append_calibration_correction(p, record)
    rows = sm.read_corrections(p)
    assert len(rows) == 1
    assert rows[0] == record
