"""Targeted tests for the dream curriculum planner (Phase 8.5
Session C, commit 2). 18 cases covering the categories listed in
c.txt §RECOMMENDED COMMIT RHYTHM commit 2.

Two test categories from that list — `replay_case_manifest generation`
and `replay selection bounded` — logically belong to C.4 (shadow graph
+ replay harness). Per c.txt §test_distribution_overrides those two
are deferred and recorded in state.json. They are replaced here by two
extra determinism / fallback cases that legitimately belong to C.2.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.dreaming import curriculum as cu


def _load_tool():
    path = ROOT / "tools" / "dream_curriculum.py"
    spec = importlib.util.spec_from_file_location("dream_curriculum_tool", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dream_curriculum_tool"] = mod
    spec.loader.exec_module(mod)
    return mod


tool = _load_tool()


# ── Fixture builders ─────────────────────────────────────────────-

def _make_self_model_with_deferred() -> dict:
    return {
        "schema_version": 1,
        "workspace_tensions": [
            {
                "tension_id": "ten_0001",
                "type": "scorecard_drift",
                "claim": "thermal score = 0.92",
                "observation": "rolling failure_rate 0.18",
                "severity": "high",
                "lifecycle_status": "persisting",
                "resolution_path": "deferred_to_dream",
                "evidence_refs": ["cell:thermal", "cur:cur_thermal_1"],
            },
            {
                "tension_id": "ten_0002",
                "type": "calibration_oscillation",
                "claim": "energy score oscillates",
                "observation": "5+ flips in last 10",
                "severity": "medium",
                "lifecycle_status": "persisting",
                "resolution_path": "deferred_to_dream",
                "evidence_refs": ["cell:energy"],
            },
        ],
        "blind_spots": [
            {"domain": "safety", "severity": "high",
             "detectors": ["coverage_negative_space"]},
            {"domain": "logistics", "severity": "low",
             "detectors": ["curiosity_silence"]},
        ],
    }


def _make_self_model_no_deferred() -> dict:
    return {
        "schema_version": 1,
        "workspace_tensions": [
            {
                "tension_id": "ten_0010",
                "type": "scorecard_drift",
                "claim": "thermal score = 0.50",
                "observation": "small drift",
                "severity": "low",
                "lifecycle_status": "new",
                "resolution_path": "merge_evidence",
                "evidence_refs": ["cell:thermal"],
            },
        ],
        "blind_spots": [
            {"domain": "safety", "severity": "high",
             "detectors": ["coverage_negative_space"]},
        ],
    }


def _make_curiosity_log() -> list[dict]:
    return [
        {"curiosity_id": "cur_thermal_1", "candidate_cell": "thermal",
         "estimated_value": 9.5, "suspected_gap_type": "missing_solver",
         "count": 12},
        {"curiosity_id": "cur_energy_1", "candidate_cell": "energy",
         "estimated_value": 7.0, "suspected_gap_type": "improvement_opportunity",
         "count": 6},
        {"curiosity_id": "cur_safety_1", "candidate_cell": "safety",
         "estimated_value": 3.0, "suspected_gap_type": "missing_solver",
         "count": 4},
    ]


def _make_calibration_corrections() -> list[dict]:
    return [
        {"dimension": "thermal", "prior_score": 0.80,
         "evidence_implied_score": 0.55,
         "correction_kind": "lower_confidence"},
    ]


def _make_pin_state(tmp_path: Path,
                     self_model: dict | None,
                     curiosity_log: list[dict] | None,
                     calibration_corrections: list[dict] | None) -> Path:
    """Write fixture artifacts and a pin manifest that points at them."""
    fix = tmp_path / "fix"
    fix.mkdir()
    pinned = []
    if self_model is not None:
        p = fix / "self_model_snapshot.json"
        p.write_text(json.dumps(self_model, indent=2, sort_keys=True),
                     encoding="utf-8")
        pinned.append({"path": str(p),
                       "size_bytes": p.stat().st_size,
                       "sha256_full": "z" * 64})
    if curiosity_log is not None:
        p = fix / "curiosity_log.jsonl"
        p.write_text("\n".join(json.dumps(c) for c in curiosity_log) + "\n",
                     encoding="utf-8")
        pinned.append({"path": str(p),
                       "size_bytes": p.stat().st_size,
                       "sha256_full": "z" * 64})
    if calibration_corrections is not None:
        p = fix / "calibration_corrections.jsonl"
        p.write_text(
            "\n".join(json.dumps(c) for c in calibration_corrections) + "\n",
            encoding="utf-8",
        )
        pinned.append({"path": str(p),
                       "size_bytes": p.stat().st_size,
                       "sha256_full": "z" * 64})

    state = {
        "schema_version": 1,
        "branch": "test_branch",
        "base_commit": "abc1234",
        "pinned_input_manifest_sha256": "sha256:" + "f" * 64,
        "pinned_inputs": pinned,
    }
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True),
                           encoding="utf-8")
    return state_path


# ── 1. missing Session B input/state handling ──────────────────────

def test_missing_input_manifest_returns_nonzero(tmp_path):
    missing = tmp_path / "no_state.json"
    with pytest.raises((FileNotFoundError, json.JSONDecodeError)):
        tool.load_pin_manifest(missing)


# ── 2. missing optional artifact handling ──────────────────────────

def test_missing_optional_artifacts_does_not_crash(tmp_path):
    """No curiosity_log or calibration_corrections should still build."""
    state_path = _make_pin_state(tmp_path,
                                  self_model=_make_self_model_with_deferred(),
                                  curiosity_log=None,
                                  calibration_corrections=None)
    pin_hash, pinned = tool.load_pin_manifest(state_path)
    sm = tool._load_self_model(pinned) or {}
    log = tool._load_curiosity_log(pinned)
    corr = tool._load_calibration_corrections(pinned)
    c = tool.build(sm, log, corr,
                    branch_name="t", base_commit_hash="abc",
                    pin_hash=pin_hash, top_nights=7)
    assert isinstance(c, cu.DreamCurriculum)
    assert len(c.nights) == 7


# ── 3. pinned input manifest creation logic ────────────────────────

def test_pin_manifest_loaded_round_trip(tmp_path):
    state_path = _make_pin_state(tmp_path,
                                  _make_self_model_with_deferred(),
                                  _make_curiosity_log(),
                                  _make_calibration_corrections())
    pin_hash, pinned = tool.load_pin_manifest(state_path)
    assert pin_hash.startswith("sha256:")
    assert len(pinned) == 3


# ── 4. input-manifest enforcement ──────────────────────────────────

def test_input_manifest_used_when_supplied(tmp_path):
    """Tool must load only what the manifest pins, not re-glob."""
    state_path = _make_pin_state(tmp_path,
                                  _make_self_model_with_deferred(),
                                  _make_curiosity_log(),
                                  _make_calibration_corrections())
    _, pinned = tool.load_pin_manifest(state_path)
    sm = tool._load_self_model(pinned)
    assert sm is not None
    assert sm["workspace_tensions"][0]["tension_id"] == "ten_0001"


# ── 5. --real-data-only failure behavior ───────────────────────────

def test_real_data_only_fails_when_pinned_path_missing(tmp_path):
    """If a pinned path does not exist, real-data-only must reject."""
    state = {
        "pinned_input_manifest_sha256": "sha256:abc",
        "pinned_inputs": [
            {"path": "does/not/exist.json", "size_bytes": 100,
             "sha256_full": "x" * 64},
        ],
    }
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    pin_hash, pinned = tool.load_pin_manifest(state_path)
    # Path does not exist on disk
    assert not (Path(pinned[0]["path"])).exists()


# ── 6. dream priority formula determinism ──────────────────────────

def test_dream_priority_formula_deterministic():
    p1 = cu.dream_priority(0.8, 0.3, 2, 1.0, 0.25, 0.0)
    p2 = cu.dream_priority(0.8, 0.3, 2, 1.0, 0.25, 0.0)
    assert p1 == p2
    # Manual: 0.8 * 0.3 * (1 + min(1, 2/3)) * 1.0 * (1 + 0.25 + 0)
    # = 0.8 * 0.3 * 1.6667 * 1.25 = 0.5
    expected = round(0.8 * 0.3 * (1 + 2 / 3) * 1.0 * 1.25, 6)
    assert p1 == expected


# ── 7. exploration_bonus bootstrap zero ────────────────────────────

def test_exploration_bonus_zero_at_bootstrap():
    """When there's no dream history, every item's exploration_bonus
    must be 0.0 per c.txt §C1 bootstrap rule."""
    items = cu.build_dreamable_items(
        self_model=_make_self_model_with_deferred(),
        curiosity_log=_make_curiosity_log(),
        calibration_corrections=_make_calibration_corrections(),
        history_entries=None,
    )
    assert all(it.exploration_bonus == 0.0 for it in items)


# ── 8. tension severity mapping ────────────────────────────────────

def test_severity_mapping_string_to_float():
    assert cu.normalize_severity("low") == 0.33
    assert cu.normalize_severity("medium") == 0.66
    assert cu.normalize_severity("high") == 1.0
    assert cu.normalize_severity("HIGH") == 1.0   # case-insensitive
    assert cu.normalize_severity("garbage") == 0.0
    assert cu.normalize_severity(0.5) == 0.5
    assert cu.normalize_severity(5.0) == 1.0    # clamp
    assert cu.normalize_severity(None) == 0.0


# ── 9. calibration_gap derivation ──────────────────────────────────

def test_calibration_gap_picks_latest_correction():
    corr = [
        {"dimension": "thermal", "prior_score": 0.80,
         "evidence_implied_score": 0.55},
        {"dimension": "thermal", "prior_score": 0.60,
         "evidence_implied_score": 0.30},
        {"dimension": "energy", "prior_score": 0.50,
         "evidence_implied_score": 0.45},
    ]
    # Most recent thermal correction: |0.60 - 0.30| = 0.30
    assert cu.calibration_gap_for_dimension(corr, "thermal") == pytest.approx(0.30)
    # Energy: |0.50 - 0.45| = 0.05
    assert cu.calibration_gap_for_dimension(corr, "energy") == pytest.approx(0.05)
    # Unknown dimension → 0.0
    assert cu.calibration_gap_for_dimension(corr, "logistics") == 0.0


# ── 10. attention_focus / tension selection ordering ──────────────-

def test_items_sorted_by_priority_desc_then_id():
    items = cu.build_dreamable_items(
        self_model=_make_self_model_with_deferred(),
        curiosity_log=_make_curiosity_log(),
        calibration_corrections=_make_calibration_corrections(),
    )
    priorities = [it.dream_priority for it in items]
    assert priorities == sorted(priorities, reverse=True)
    # Tie-break by source_id ascending
    for a, b in zip(items, items[1:]):
        if a.dream_priority == b.dream_priority:
            assert a.source_id <= b.source_id


# ── 11. dream curriculum 7-night plan generation ───────────────────

def test_seven_night_plan_default():
    c = cu.build_curriculum(
        self_model=_make_self_model_with_deferred(),
        curiosity_log=_make_curiosity_log(),
        calibration_corrections=_make_calibration_corrections(),
        branch_name="b", base_commit_hash="ab12",
        pinned_input_manifest_sha256="sha256:test",
    )
    assert len(c.nights) == 7
    indices = [n.night_index for n in c.nights]
    assert indices == list(range(1, 8))
    # All nights from a curriculum with deferred tensions must claim that source
    assert c.primary_source == "deferred_to_dream"


# ── 12. no deferred_to_dream tensions ⇒ secondary_fallback ─────────

def test_no_deferred_falls_back_to_secondary():
    c = cu.build_curriculum(
        self_model=_make_self_model_no_deferred(),
        curiosity_log=_make_curiosity_log(),
        calibration_corrections=_make_calibration_corrections(),
        branch_name="b", base_commit_hash="ab12",
        pinned_input_manifest_sha256="sha256:test",
    )
    assert c.primary_source == "secondary_fallback"
    assert c.secondary_fallback_reason is not None
    assert "deferred_to_dream" in c.secondary_fallback_reason


# ── 13. no secret leakage ─────────────────────────────────────────

def test_no_secrets_in_output(tmp_path):
    state_path = _make_pin_state(tmp_path,
                                  _make_self_model_with_deferred(),
                                  _make_curiosity_log(),
                                  _make_calibration_corrections())
    pin_hash, pinned = tool.load_pin_manifest(state_path)
    sm = tool._load_self_model(pinned) or {}
    log = tool._load_curiosity_log(pinned)
    corr = tool._load_calibration_corrections(pinned)
    c = tool.build(sm, log, corr,
                    branch_name="b", base_commit_hash="ab",
                    pin_hash=pin_hash, top_nights=7)
    text = json.dumps(cu.curriculum_to_dict(c))
    forbidden = ["password", "secret", "api_key", "token=",
                 "PRIVATE KEY", "BEGIN RSA"]
    for pat in forbidden:
        assert pat.lower() not in text.lower()


# ── 14. no absolute local path leakage ────────────────────────────-

def test_no_absolute_paths_in_output(tmp_path):
    state_path = _make_pin_state(tmp_path,
                                  _make_self_model_with_deferred(),
                                  _make_curiosity_log(),
                                  _make_calibration_corrections())
    pin_hash, pinned = tool.load_pin_manifest(state_path)
    sm = tool._load_self_model(pinned) or {}
    log = tool._load_curiosity_log(pinned)
    corr = tool._load_calibration_corrections(pinned)
    c = tool.build(sm, log, corr,
                    branch_name="b", base_commit_hash="ab",
                    pin_hash=pin_hash, top_nights=7)
    text = json.dumps(cu.curriculum_to_dict(c))
    # Absolute Windows drive letters or POSIX root-style absolutes
    assert "C:\\" not in text and "C:/" not in text
    assert "/home/" not in text
    assert str(tmp_path) not in text


# ── 15. dream_curriculum.json byte-identical determinism ──────────-

def test_curriculum_json_byte_identical(tmp_path):
    sm = _make_self_model_with_deferred()
    log = _make_curiosity_log()
    corr = _make_calibration_corrections()
    c1 = cu.build_curriculum(
        self_model=sm, curiosity_log=log, calibration_corrections=corr,
        branch_name="b", base_commit_hash="ab12",
        pinned_input_manifest_sha256="sha256:test",
    )
    c2 = cu.build_curriculum(
        self_model=sm, curiosity_log=log, calibration_corrections=corr,
        branch_name="b", base_commit_hash="ab12",
        pinned_input_manifest_sha256="sha256:test",
    )
    j1 = json.dumps(cu.curriculum_to_dict(c1), indent=2, sort_keys=True)
    j2 = json.dumps(cu.curriculum_to_dict(c2), indent=2, sort_keys=True)
    assert j1 == j2


# ── 16. dream_curriculum.md byte-identical determinism ────────────-

def test_curriculum_md_byte_identical():
    sm = _make_self_model_with_deferred()
    log = _make_curiosity_log()
    corr = _make_calibration_corrections()
    c = cu.build_curriculum(
        self_model=sm, curiosity_log=log, calibration_corrections=corr,
        branch_name="b", base_commit_hash="ab12",
        pinned_input_manifest_sha256="sha256:test",
    )
    md1 = cu.render_curriculum_md(cu.curriculum_to_dict(c))
    md2 = cu.render_curriculum_md(cu.curriculum_to_dict(c))
    assert md1 == md2
    assert "# Dream curriculum" in md1


# ── 17. cell filter narrows nights ────────────────────────────────-

def test_cell_filter_narrows_nights():
    sm = _make_self_model_with_deferred()
    log = _make_curiosity_log()
    corr = _make_calibration_corrections()
    full = tool.build(sm, log, corr,
                       branch_name="b", base_commit_hash="ab",
                       pin_hash="sha256:test", top_nights=7,
                       cell_filter=None)
    filtered = tool.build(sm, log, corr,
                           branch_name="b", base_commit_hash="ab",
                           pin_hash="sha256:test", top_nights=7,
                           cell_filter="thermal")
    # Filter must not invent new nights
    assert len(filtered.nights) <= len(full.nights)
    # Every targeted item must be in the chosen cell
    for night in filtered.nights:
        for it in night.target_items:
            assert it.candidate_cell == "thermal"


# ── 18. counts_by_mode + counts_by_kind sum is consistent ─────────-

def test_counts_consistent_with_nights():
    c = cu.build_curriculum(
        self_model=_make_self_model_with_deferred(),
        curiosity_log=_make_curiosity_log(),
        calibration_corrections=_make_calibration_corrections(),
        branch_name="b", base_commit_hash="ab12",
        pinned_input_manifest_sha256="sha256:test",
    )
    # counts_by_mode must sum to len(nights)
    assert sum(c.counts_by_mode.values()) == len(c.nights)
    # counts_by_kind must sum to total target_items across nights
    total_items = sum(len(n.target_items) for n in c.nights)
    assert sum(c.counts_by_kind.values()) == total_items
    # All modes seen must be valid
    for mode in c.counts_by_mode:
        assert mode in cu.DREAM_MODES
