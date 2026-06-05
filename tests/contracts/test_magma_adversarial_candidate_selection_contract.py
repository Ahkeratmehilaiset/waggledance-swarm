# SPDX-License-Identifier: BUSL-1.1
"""Contracts for MAGMA adversarial ASI/defect candidate selection."""
from __future__ import annotations

from tools.generate_magma_adversarial_candidates import build_candidate_report


def test_asi04_path_escape_fails_closed_without_partial_candidates() -> None:
    report = build_candidate_report(
        limit=1,
        asi_ids=["ASI04"],
        defect_types=["path_escape"],
    )

    assert report["ok"] is False
    assert report["candidate_count"] == 0
    assert report["selection"]["requested_asi_ids"] == ["asi04"]
    assert report["selection"]["requested_defect_types"] == ["path_escape"]
    assert report["selection"]["selected_defect_types"] == []
    assert report["candidates"] == []
    assert report["errors"] == [
        "selection: defect_type outside requested ASI mapping: path_escape"
    ]


def test_mixed_asi04_valid_and_invalid_defects_produce_zero_candidates() -> None:
    report = build_candidate_report(
        limit=3,
        asi_ids=["ASI04"],
        defect_types=["governance_bypass", "path_escape"],
    )

    assert report["ok"] is False
    assert report["candidate_count"] == 0
    assert report["selection"]["requested_asi_ids"] == ["asi04"]
    assert report["selection"]["requested_defect_types"] == [
        "governance_bypass",
        "path_escape",
    ]
    assert report["selection"]["selected_defect_types"] == []
    assert report["candidates"] == []
    assert report["errors"] == [
        "selection: defect_type outside requested ASI mapping: path_escape"
    ]


def test_asi04_governance_bypass_control_generates_tagged_candidate() -> None:
    report = build_candidate_report(
        limit=1,
        asi_ids=["ASI04"],
        defect_types=["governance_bypass"],
    )

    assert report["ok"] is True
    assert report["candidate_count"] == 1
    assert report["selection"]["requested_asi_ids"] == ["asi04"]
    assert report["selection"]["requested_defect_types"] == ["governance_bypass"]
    assert report["selection"]["selected_defect_types"] == ["governance_bypass"]

    candidate = report["candidates"][0]
    case = candidate["case"]
    assert candidate["asi_targets"] == ["asi04"]
    assert case["defect_type"] == "governance_bypass"
    assert "asi04" in case["tags"]
    assert "path_escape" not in report["selection"]["selected_defect_types"]
