# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.magma.reflective_workspace.

Codex scout round 2 flagged this as Candidate 3 (medium risk if missing):
``reflective_workspace`` is the bridge from MAGMA / self-model evidence
to the next question and downstream dream/meta flows. Silent priority
inversions here would send attention to the wrong cell or make recurring
tensions look new. The functions are pure Python and can be covered
without ChromaDB, Ollama, subprocesses, or product-code edits.

Pinned invariants (B.txt §B4-B5):

- `detect_coverage_negative_space`: domain with no artifact_signals
  becomes a flag.
- `detect_curiosity_silence`: domain with strong cell but zero curiosity
  items becomes a flag.
- `build_blind_spots`: severity follows the §B4 matrix
  (≥2 detectors → high; 1 detector + structural evidence → medium;
  otherwise → low). Output sorted by (-severity_weight, domain).
- `detect_tensions`: scorecard drift ≥ CALIBRATION_DRIFT_THRESHOLD emits
  a tension; lifecycle_status = "persisting" iff tension_id was in the
  previous snapshot.
- `resolve_tensions_lifecycle`: returns (tagged_current, resolved_ids).
- `next_question`: highest-severity tension wins; else highest-severity
  blind spot; else meta_curiosity question; else default fallback.
"""
from __future__ import annotations

import pytest

from waggledance.core.magma import self_model as sm
from waggledance.core.magma.reflective_workspace import (
    build_blind_spots,
    detect_coverage_negative_space,
    detect_curiosity_silence,
    detect_tensions,
    next_question,
    resolve_tensions_lifecycle,
)


# --- detect_coverage_negative_space --------------------------------

def test_coverage_negative_space_flags_domains_with_no_artifact_signal():
    expected = [
        {"domain_id": "math"},
        {"domain_id": "vision"},
        {"domain_id": "lang"},
    ]
    artifact_signals = {"math": True, "vision": False}  # 'lang' missing entirely
    flagged = detect_coverage_negative_space(expected, {}, artifact_signals)
    assert flagged == ["lang", "vision"]  # sorted


def test_coverage_negative_space_skips_entries_without_domain_id():
    expected = [{"domain_id": ""}, {"domain_id": None}, {"domain_id": "math"}]
    flagged = detect_coverage_negative_space(expected, {}, {"math": True})
    assert flagged == []


# --- detect_curiosity_silence --------------------------------------

def test_curiosity_silence_fires_on_strong_cell_with_zero_curiosity():
    expected = [
        {"domain_id": "math", "related_cells": ["cell:hex_a"]},
    ]
    cell_strength = {"cell:hex_a": "strong"}
    curiosity_per_domain = {"cell:hex_a": 0}
    flagged = detect_curiosity_silence(expected, curiosity_per_domain, cell_strength)
    assert flagged == ["math"]


def test_curiosity_silence_does_not_fire_when_cell_weak():
    expected = [
        {"domain_id": "math", "related_cells": ["cell:hex_a"]},
    ]
    cell_strength = {"cell:hex_a": "weak"}
    curiosity_per_domain = {"cell:hex_a": 0}
    flagged = detect_curiosity_silence(expected, curiosity_per_domain, cell_strength)
    assert flagged == []


def test_curiosity_silence_does_not_fire_when_curiosity_present():
    expected = [
        {"domain_id": "math", "related_cells": ["cell:hex_a"]},
    ]
    cell_strength = {"cell:hex_a": "strong"}
    curiosity_per_domain = {"cell:hex_a": 5}
    flagged = detect_curiosity_silence(expected, curiosity_per_domain, cell_strength)
    assert flagged == []


# --- build_blind_spots: severity matrix + ordering -----------------

def test_blind_spots_two_detectors_yields_high_severity():
    expected = [
        {"domain_id": "math", "description": "math domain",
         "expected_capability_signals": ["docs/x.json"]},
    ]
    # Both negative_space (no artifact) AND curiosity_silence (strong cell, zero curiosity)
    artifact_signals = {"math": False}
    cell_strength = {"math": "strong"}
    curiosity_per_domain = {"math": 0}
    expected_ws_strength = expected.copy()
    expected_ws_strength[0]["related_cells"] = ["math"]
    bs = build_blind_spots(
        expected_domains=expected_ws_strength,
        cell_states=cell_strength,
        artifact_signals=artifact_signals,
        curiosity_per_domain=curiosity_per_domain,
    )
    assert len(bs) == 1
    assert bs[0].severity == "high"
    assert set(bs[0].detectors) == {"coverage_negative_space", "curiosity_silence"}


def test_blind_spots_one_detector_with_structural_evidence_yields_medium():
    """1 detector + has_structural_evidence (artifact present) → medium."""
    expected = [{"domain_id": "math",
                 "description": "math",
                 "related_cells": ["math"]}]
    # Only curiosity_silence fires (artifact present, but strong cell + zero curiosity)
    artifact_signals = {"math": True}
    cell_strength = {"math": "strong"}
    curiosity_per_domain = {"math": 0}
    bs = build_blind_spots(
        expected_domains=expected,
        cell_states=cell_strength,
        artifact_signals=artifact_signals,
        curiosity_per_domain=curiosity_per_domain,
    )
    assert len(bs) == 1
    assert bs[0].severity == "medium"
    assert bs[0].detectors == ("curiosity_silence",)


def test_blind_spots_one_detector_without_structural_evidence_yields_low():
    """1 detector + NO structural evidence (artifact absent) → low."""
    expected = [{"domain_id": "math",
                 "description": "math",
                 "related_cells": ["math"]}]
    # Only coverage_negative_space fires (no artifact); cell weak so no
    # curiosity_silence to add a second detector.
    artifact_signals = {"math": False}
    cell_strength = {"math": "weak"}
    curiosity_per_domain = {"math": 0}
    bs = build_blind_spots(
        expected_domains=expected,
        cell_states=cell_strength,
        artifact_signals=artifact_signals,
        curiosity_per_domain=curiosity_per_domain,
    )
    assert len(bs) == 1
    assert bs[0].severity == "low"


def test_blind_spots_sorted_high_severity_first():
    expected = [
        {"domain_id": "low_d", "description": "", "related_cells": ["low_d"]},
        {"domain_id": "high_d", "description": "", "related_cells": ["high_d"]},
    ]
    # high_d: 2 detectors. low_d: 1 detector + no structural evidence → low.
    artifact_signals = {"high_d": False, "low_d": False}
    cell_strength = {"high_d": "strong", "low_d": "weak"}
    curiosity_per_domain = {"high_d": 0, "low_d": 0}
    bs = build_blind_spots(
        expected_domains=expected,
        cell_states=cell_strength,
        artifact_signals=artifact_signals,
        curiosity_per_domain=curiosity_per_domain,
    )
    assert [(b.domain, b.severity) for b in bs] == [
        ("high_d", "high"),
        ("low_d", "low"),
    ]


# --- detect_tensions: scorecard drift ------------------------------

def _scorecard_with_drift(score: float, eis: float | None) -> sm.ScorecardDimension:
    ce = sm.CalibrationEvidence(
        dimension="solver_breadth",
        evidence_implied_score=eis,
        evidence_refs=("ref:1",),
        calibration_status="mismatch",
    )
    return sm.ScorecardDimension(
        name="solver_breadth",
        score=score,
        evidence=("ref:1",),
        calibration_evidence=ce,
        uncertainty="medium",
        why_it_matters="why_it_matters: solver breadth drives capability proof.",
    )


def test_detect_tensions_emits_when_score_drifts_at_or_above_threshold():
    """abs(score - eis) ≥ CALIBRATION_DRIFT_THRESHOLD (0.2) → tension."""
    dim = _scorecard_with_drift(score=0.8, eis=0.5)  # diff=0.3
    tensions = detect_tensions(scorecard=[dim], cells=[])
    assert len(tensions) == 1
    assert tensions[0].type == "scorecard_drift"
    assert tensions[0].lifecycle_status == "new"
    assert tensions[0].severity == "medium"  # 0.3 < 0.4 → medium


def test_detect_tensions_does_not_fire_below_threshold():
    dim = _scorecard_with_drift(score=0.6, eis=0.5)  # diff=0.1 < 0.2
    tensions = detect_tensions(scorecard=[dim], cells=[])
    assert tensions == []


def test_detect_tensions_high_severity_when_drift_at_or_above_0_4():
    dim = _scorecard_with_drift(score=0.9, eis=0.4)  # diff=0.5 ≥ 0.4
    tensions = detect_tensions(scorecard=[dim], cells=[])
    assert tensions[0].severity == "high"


def test_detect_tensions_lifecycle_persisting_on_history_match():
    """Same tension_id observed previously → lifecycle_status='persisting'."""
    dim = _scorecard_with_drift(score=0.8, eis=0.5)
    first = detect_tensions(scorecard=[dim], cells=[])
    assert first[0].lifecycle_status == "new"
    second = detect_tensions(scorecard=[dim], cells=[], previous_tensions=first)
    assert second[0].tension_id == first[0].tension_id
    assert second[0].lifecycle_status == "persisting"


def test_detect_tensions_skips_dimension_without_calibration_evidence():
    dim = sm.ScorecardDimension(
        name="x", score=0.5, evidence=(),
        calibration_evidence=None, uncertainty="low",
        why_it_matters="why_it_matters: x.",
    )
    assert detect_tensions(scorecard=[dim], cells=[]) == []


# --- resolve_tensions_lifecycle ------------------------------------

def test_resolve_tensions_lifecycle_marks_persisting_and_returns_resolved():
    prev_t = sm.WorkspaceTension(
        tension_id="t-prev-1", type="scorecard_drift",
        claim="c", observation="o", severity="medium",
        resolution_path="calibration_correction", lifecycle_status="new",
        evidence_refs=("ref",),
    )
    prev_t_gone = sm.WorkspaceTension(
        tension_id="t-gone", type="scorecard_drift",
        claim="c2", observation="o2", severity="low",
        resolution_path="calibration_correction", lifecycle_status="new",
        evidence_refs=(),
    )
    cur_t_persisting = sm.WorkspaceTension(
        tension_id="t-prev-1", type="scorecard_drift",
        claim="c", observation="o", severity="medium",
        resolution_path="calibration_correction", lifecycle_status="new",
        evidence_refs=("ref",),
    )
    cur_t_brand_new = sm.WorkspaceTension(
        tension_id="t-fresh", type="scorecard_drift",
        claim="c3", observation="o3", severity="high",
        resolution_path="calibration_correction", lifecycle_status="new",
        evidence_refs=(),
    )
    tagged, resolved = resolve_tensions_lifecycle(
        current=[cur_t_persisting, cur_t_brand_new],
        previous=[prev_t, prev_t_gone],
    )
    by_id = {t.tension_id: t for t in tagged}
    assert by_id["t-prev-1"].lifecycle_status == "persisting"
    assert by_id["t-fresh"].lifecycle_status == "new"
    assert resolved == ["t-gone"]


# --- next_question priority ----------------------------------------

def _tension(tid: str, severity: str) -> sm.WorkspaceTension:
    return sm.WorkspaceTension(
        tension_id=tid, type="scorecard_drift",
        claim=f"claim-{tid}", observation=f"obs-{tid}",
        severity=severity, resolution_path="calibration_correction",
        lifecycle_status="new", evidence_refs=(),
    )


def _blind_spot(domain: str, severity: str) -> sm.BlindSpot:
    return sm.BlindSpot(
        domain=domain, severity=severity,
        detectors=("coverage_negative_space",),
        description="", provenance={},
    )


def test_next_question_picks_highest_severity_tension_first():
    tensions = [_tension("t-low", "low"), _tension("t-high", "high")]
    blind_spots = [_blind_spot("dom", "high")]  # ignored when tensions present
    q = next_question(tensions, blind_spots, meta_curiosity=None)
    assert "claim-t-high" in q
    assert "obs-t-high" in q


def test_next_question_falls_back_to_blind_spot_when_no_tensions():
    blind_spots = [_blind_spot("low_d", "low"), _blind_spot("high_d", "high")]
    q = next_question(tensions=[], blind_spots=blind_spots, meta_curiosity=None)
    # Highest-severity blind spot wins.
    assert "high_d" in q
    assert "low_d" not in q


def test_next_question_falls_back_to_meta_curiosity_when_no_tensions_or_blind_spots():
    mc = sm.MetaCuriosity(
        question="What is the next epistemic move?",
        derivation_strength="medium",
        source_refs=(),
    )
    q = next_question(tensions=[], blind_spots=[], meta_curiosity=mc)
    assert q == "What is the next epistemic move?"


def test_next_question_default_when_everything_empty():
    q = next_question(tensions=[], blind_spots=[], meta_curiosity=None)
    assert q == "What does WD not yet know about itself?"
