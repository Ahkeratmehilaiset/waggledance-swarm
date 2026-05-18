# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
from pathlib import Path

from waggledance.core.idle_protocol import (
    detect_idle_convergence,
    validate_idle_proposal,
)


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "idle_protocol"
PRIVATE_MARKERS = ("PRIVATE_MARKER", "_DO_NOT_LEAK")


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _assert_fixture_has_no_private_markers(payload: object) -> None:
    text = json.dumps(payload, sort_keys=True)
    for marker in PRIVATE_MARKERS:
        assert marker not in text


def _validated_events(fixture: dict) -> list[dict]:
    events = fixture["events"]
    for event in events:
        ok, errors = validate_idle_proposal(event)
        assert ok, errors
    return events


def test_soft_convergence_fixture_detects_operator_gated_consensus() -> None:
    fixture = _load_fixture("soft_convergence.json")
    _assert_fixture_has_no_private_markers(fixture)

    report = detect_idle_convergence(_validated_events(fixture))

    assert report["status"] == "soft_convergence"
    assert report["target_proposal_id"] == "idle-soft-002"
    assert report["operator_gate_required"] is True
    assert report["auto_execute"] is False


def test_hard_convergence_fixture_returns_finalists_without_auto_execute() -> None:
    fixture = _load_fixture("hard_convergence.json")
    _assert_fixture_has_no_private_markers(fixture)

    report = detect_idle_convergence(_validated_events(fixture))

    assert report["status"] == "hard_convergence"
    assert report["round_number"] == 10
    assert report["operator_gate_required"] is True
    assert report["auto_execute"] is False
    assert report["finalist_proposal_ids"] == [
        "idle-hard-010",
        "idle-hard-009",
        "idle-hard-008",
    ]


def test_charter_violation_fixture_terminates_immediately() -> None:
    fixture = _load_fixture("charter_violation.json")
    _assert_fixture_has_no_private_markers(fixture)

    report = detect_idle_convergence(_validated_events(fixture))

    assert report["status"] == "charter_violation"
    assert report["violating_proposal_id"] == "idle-charter-001"
    assert report["terminate_protocol"] is True
    assert report["operator_escalation_required"] is True


def test_low_quality_response_fixture_keeps_convergence_open() -> None:
    fixture = _load_fixture("low_quality_response.json")
    _assert_fixture_has_no_private_markers(fixture)
    ok, errors = validate_idle_proposal(fixture["rejected_payload"])

    assert ok is False
    assert errors
    assert detect_idle_convergence(_validated_events(fixture)) is None


def test_all_fixture_files_are_private_marker_free() -> None:
    fixtures = sorted(FIXTURE_DIR.glob("*.json"))
    assert fixtures
    for fixture_path in fixtures:
        _assert_fixture_has_no_private_markers(
            json.loads(fixture_path.read_text(encoding="utf-8"))
        )
