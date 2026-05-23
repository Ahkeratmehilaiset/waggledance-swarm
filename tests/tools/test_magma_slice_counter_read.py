# SPDX-License-Identifier: Apache-2.0
"""Unit tests for tools/magma_slice_counter_read.py.

The committed baseline.json is used as a real anchor fixture; the
candidate variants are synthetic in-memory dicts. No GitHub API call
is made and no file outside the worktree is read.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools.magma_slice_counter_read import (
    DEFAULT_BASELINE,
    REQUIRED_FORBIDDEN_CLAIMS,
    check_delta,
    check_invariants,
    counter_read,
    main,
)

ROOT = Path(__file__).resolve().parents[2]


def _anchor() -> dict:
    """The committed baseline.json -- a known-honest anchor."""
    return json.loads(
        (ROOT / DEFAULT_BASELINE).read_text(encoding="utf-8")
    )


def _write(tmp_path: Path, payload: dict, name: str = "baseline.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return p


# --- static invariants -----------------------------------------------------

def test_committed_baseline_passes_static_invariants():
    findings = check_invariants(_anchor())
    assert findings == [], findings


def test_block_when_consensus_grade_flipped_true():
    bad = _anchor()
    bad["current_state"]["competitor_pilot"]["consensus_grade"] = True

    findings = check_invariants(bad)
    codes = [f["code"] for f in findings]
    assert "competitor_consensus_grade_must_be_false" in codes


def test_block_when_release_boundary_flag_flipped():
    bad = _anchor()
    bad["release_boundary"]["docker_latest_move"] = True

    findings = check_invariants(bad)
    codes = [f["code"] for f in findings]
    assert "release_boundary_flag_must_be_false" in codes


def test_block_when_a3_label_unqualified():
    bad = _anchor()
    bad["current_state"]["a3_counterfactual_axis"]["claim_label"] = "PROVEN"

    findings = check_invariants(bad)
    codes = [f["code"] for f in findings]
    assert "a3_claim_label_unqualified" in codes


def test_block_when_a4_label_unqualified():
    bad = _anchor()
    bad["current_state"]["a4_solver_growth_axis"]["claim_label"] = "GA"

    findings = check_invariants(bad)
    codes = [f["code"] for f in findings]
    assert "a4_claim_label_unqualified" in codes


def test_block_when_required_forbidden_claim_missing():
    bad = _anchor()
    bad["forbidden_claims"] = [
        c for c in bad["forbidden_claims"] if c != "rival benchmark consensus-grade"
    ]

    findings = check_invariants(bad)
    codes = [f["code"] for f in findings]
    assert "required_forbidden_claim_missing" in codes


def test_block_when_rival_local_check_consensus_grade_flipped_true():
    bad = _anchor()
    bad["current_state"]["competitor_pilot"][
        "rival_local_check_consensus_grade"
    ] = True

    findings = check_invariants(bad)
    codes = [f["code"] for f in findings]
    assert "rival_local_check_consensus_grade_must_be_false" in codes


def test_block_when_schema_version_changed():
    bad = _anchor()
    bad["schema_version"] = "waggledance.magma_100h_sprint_baseline.v1"

    findings = check_invariants(bad)
    codes = [f["code"] for f in findings]
    assert "schema_version_unexpected" in codes


# --- delta mode ------------------------------------------------------------

def test_delta_blocks_forbidden_claim_removed():
    anchor = _anchor()
    candidate = copy.deepcopy(anchor)
    candidate["forbidden_claims"] = [
        c for c in candidate["forbidden_claims"] if c != "AGI"
    ]

    findings = check_delta(anchor, candidate)
    codes = [f["code"] for f in findings]
    assert "forbidden_claim_removed_in_candidate" in codes


def test_delta_blocks_release_boundary_change():
    anchor = _anchor()
    candidate = copy.deepcopy(anchor)
    candidate["release_boundary"]["tag_creation"] = True

    findings = check_delta(anchor, candidate)
    codes = [f["code"] for f in findings]
    assert "release_boundary_flag_changed_in_candidate" in codes


def test_delta_blocks_a3_unqualified_upgrade():
    anchor = _anchor()
    candidate = copy.deepcopy(anchor)
    candidate["current_state"]["a3_counterfactual_axis"][
        "claim_label"
    ] = "CONSENSUS_GRADE"

    findings = check_delta(anchor, candidate)
    codes = [f["code"] for f in findings]
    assert "a3_label_upgraded_to_unqualified" in codes


def test_delta_allows_honest_qualified_label_transition():
    anchor = _anchor()
    candidate = copy.deepcopy(anchor)
    # An honest upgrade from PARTIAL to LOCAL within the qualified set
    # should not trigger the delta block.
    candidate["current_state"]["a3_counterfactual_axis"][
        "claim_label"
    ] = "MEASURED_LOCAL"

    findings = check_delta(anchor, candidate)
    assert findings == []


# --- counter_read end-to-end + main() exit codes --------------------------

def test_counter_read_pass_on_committed_baseline(tmp_path):
    baseline_path = ROOT / DEFAULT_BASELINE
    report = counter_read(baseline_path)
    assert report["decision"] == "pass"
    assert report["findings_count"] == 0


def test_counter_read_block_on_synthetic_overclaim(tmp_path):
    bad = _anchor()
    bad["current_state"]["competitor_pilot"]["consensus_grade"] = True
    p = _write(tmp_path, bad)

    report = counter_read(p)
    assert report["decision"] == "block"
    assert report["findings_count"] >= 1


def test_main_exits_zero_on_pass(capsys):
    rc = main(["--baseline", str(ROOT / DEFAULT_BASELINE)])
    assert rc == 0
    captured = capsys.readouterr()
    assert '"decision": "pass"' in captured.out


def test_main_exits_one_on_block(tmp_path, capsys):
    bad = _anchor()
    bad["release_boundary"]["stable_release_claim"] = True
    p = _write(tmp_path, bad)

    rc = main(["--baseline", str(p)])
    assert rc == 1
    captured = capsys.readouterr()
    assert '"decision": "block"' in captured.out


def test_main_exits_two_on_missing_baseline(tmp_path, capsys):
    rc = main(["--baseline", str(tmp_path / "does-not-exist.json")])
    assert rc == 2
    captured = capsys.readouterr()
    assert "baseline not found" in captured.err


def test_delta_mode_passes_when_anchor_equals_candidate(tmp_path):
    anchor = _anchor()
    a_path = _write(tmp_path, anchor, "anchor.json")
    c_path = _write(tmp_path, anchor, "candidate.json")

    report = counter_read(c_path, against=a_path)
    assert report["decision"] == "pass"
    assert report["delta_findings"] == []


def test_required_forbidden_claims_constants_match_committed_baseline():
    """Guard the constant list against silent drift away from the
    committed forbidden_claims set in baseline.json."""
    fc = set(_anchor()["forbidden_claims"])
    for required in REQUIRED_FORBIDDEN_CLAIMS:
        assert required in fc, required
