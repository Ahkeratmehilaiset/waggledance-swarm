# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from tools.run_operator_authority_readiness import (
    DECISION_PACKET_SCHEMA_VERSION,
    FALSE_RELEASE_BOUNDARY,
    SCHEMA_VERSION,
    STRICT_BLOCKED_EXIT_CODE,
    build_report,
    explicit_operator_approval_events,
    main,
    strict_exit_code,
)


FIXED_NOW = dt.datetime(2026, 5, 26, 6, 0, tzinfo=dt.UTC)


def _phase_synthesis_refresh() -> dict[str, object]:
    return {
        "schema_version": "waggledance.magma_100h_phase_synthesis_refresh.v0",
        "sprint_id": "magma-100h-sprint3-2026-05-26",
        "generated_at_utc": "2026-05-26T08:36:00Z",
        "ok": True,
        "release_boundary": dict(FALSE_RELEASE_BOUNDARY),
        "remaining_work_packages": [
            {
                "id": "operator_gated_authority_activation_decision",
                "owner": "operator",
                "status": "operator_decision_required",
                "acceptance": (
                    "requires an explicit operator approval event; no runtime "
                    "traffic or candidate-state mutation before approval"
                ),
            },
            {
                "id": "release_soak_evidence_blocker_resolution",
                "owner": "operator,codex",
                "status": "blocked_until_release_gate_soak_evidence_passes",
            },
        ],
    }


def _operator_approval_event() -> dict[str, object]:
    return {
        "ts_utc": "2026-05-26T06:00:00Z",
        "agent": "operator",
        "type": "decision",
        "task_id": "operator_gated_authority_activation_decision",
        "status": "approved",
        "message": "Approve operator-gated authority activation decision.",
    }


def test_report_records_hold_when_operator_approval_is_missing() -> None:
    report = build_report(
        phase_synthesis_refresh=_phase_synthesis_refresh(),
        events=[],
        checked_at_utc=FIXED_NOW,
    )

    assert report["schema_version"] == SCHEMA_VERSION
    assert report["checked_at_utc"] == "2026-05-26T06:00:00Z"
    assert report["ok"] is True
    assert report["authority_activation_status"] == (
        "hold_operator_approval_required"
    )
    assert report["explicit_operator_approval_found"] is False
    assert "explicit_operator_approval_event_missing" in (
        report["activation_blockers"]
    )
    assert report["authority_guardrails"] == {
        "activation_effect": "none",
        "candidate_state_mutation_applied": False,
        "operator_gate_required": True,
        "requires_separate_receipt_bound_activation": True,
        "runtime_authority_granted": False,
        "runtime_traffic_mutation_applied": False,
    }
    assert report["source_phase_synthesis_refresh"] == {
        "schema_version": "waggledance.magma_100h_phase_synthesis_refresh.v0",
        "sprint_id": "magma-100h-sprint3-2026-05-26",
        "generated_at_utc": "2026-05-26T08:36:00Z",
        "ok": True,
        "release_boundary_all_false": True,
        "remaining_release_soak_package": {
            "id": "release_soak_evidence_blocker_resolution",
            "owner": "operator,codex",
            "status": "blocked_until_release_gate_soak_evidence_passes",
        },
    }
    assert report["release_boundary"] == FALSE_RELEASE_BOUNDARY
    packet = report["operator_decision_packet"]
    assert packet["schema_version"] == DECISION_PACKET_SCHEMA_VERSION
    assert packet["id"] == "operator_gated_authority_activation_decision"
    assert packet["decision_status"] == "operator_approval_missing"
    assert packet["default_recommendation"] == "hold_no_authority_change"
    assert packet["operator_input_required"] is True
    assert packet["activation_effect_before_followup"] == "none"
    assert {
        option["id"] for option in packet["decision_options"]
    } == {
        "hold_no_authority_change",
        "approve_receipt_bound_activation_preparation",
    }
    assert all(
        option["runtime_authority_granted"] is False
        for option in packet["decision_options"]
    )
    assert all(
        option["runtime_traffic_mutation_allowed"] is False
        for option in packet["decision_options"]
    )
    assert all(
        option["candidate_state_mutation_allowed"] is False
        for option in packet["decision_options"]
    )
    assert all(
        option["release_boundary_mutation_allowed"] is False
        for option in packet["decision_options"]
    )


def test_only_operator_approval_event_counts() -> None:
    near_misses = [
        {
            "agent": "codex-lead-1",
            "type": "decision",
            "task_id": "operator_gated_authority_activation_decision",
            "status": "approved",
            "message": "Not an operator approval.",
        },
        {
            "agent": "operator",
            "type": "handoff",
            "task_id": "operator_gated_authority_activation_decision",
            "status": "approved",
            "message": "Wrong event type.",
        },
        {
            "agent": "operator",
            "type": "decision",
            "task_id": "unrelated-task",
            "status": "approved",
            "message": "Unrelated approval.",
        },
    ]

    assert explicit_operator_approval_events(near_misses) == []


def test_report_records_approval_without_granting_authority() -> None:
    report = build_report(
        phase_synthesis_refresh=_phase_synthesis_refresh(),
        events=[_operator_approval_event()],
        checked_at_utc=FIXED_NOW,
    )

    assert report["authority_activation_status"] == (
        "operator_approved_activation_still_not_granted"
    )
    assert report["activation_blockers"] == []
    assert report["explicit_operator_approval_found"] is True
    assert report["approval_event_count"] == 1
    assert report["authority_guardrails"]["runtime_authority_granted"] is False
    assert (
        report["authority_guardrails"]["runtime_traffic_mutation_applied"]
        is False
    )
    assert (
        report["authority_guardrails"]["candidate_state_mutation_applied"]
        is False
    )
    packet = report["operator_decision_packet"]
    assert packet["decision_status"] == "operator_approval_recorded"
    assert packet["operator_input_required"] is False
    assert packet["activation_effect_before_followup"] == "none"
    assert all(
        option["runtime_authority_granted"] is False
        for option in packet["decision_options"]
    )
    assert all(
        option["release_boundary_mutation_allowed"] is False
        for option in packet["decision_options"]
    )


def test_report_fails_activation_if_phase_synthesis_boundary_mutates() -> None:
    phase = _phase_synthesis_refresh()
    phase["release_boundary"] = dict(FALSE_RELEASE_BOUNDARY)
    phase["release_boundary"]["external_effect_authority_change"] = True

    report = build_report(
        phase_synthesis_refresh=phase,
        events=[_operator_approval_event()],
        checked_at_utc=FIXED_NOW,
    )

    assert "phase_synthesis_release_boundary_mutated" in (
        report["activation_blockers"]
    )
    assert report["authority_guardrails"]["activation_effect"] == "none"


def test_cli_writes_hold_report(tmp_path: Path, capsys) -> None:
    phase_path = tmp_path / "phase_synthesis_refresh.json"
    events_path = tmp_path / "events.jsonl"
    output_path = tmp_path / "operator_authority_readiness.json"
    phase_path.write_text(
        json.dumps(_phase_synthesis_refresh()),
        encoding="utf-8",
    )
    events_path.write_text("", encoding="utf-8")

    rc = main(
        [
            "--phase-synthesis-refresh",
            str(phase_path),
            "--events",
            str(events_path),
            "--checked-at-utc",
            "2026-05-26T06:00:00Z",
            "--output",
            str(output_path),
            "--json",
        ]
    )

    assert rc == 0
    stdout_report = json.loads(capsys.readouterr().out)
    disk_report = json.loads(output_path.read_text(encoding="utf-8"))
    assert stdout_report == disk_report
    assert disk_report["authority_activation_status"] == (
        "hold_operator_approval_required"
    )


def test_strict_exit_code_reports_blocked_hold() -> None:
    report = build_report(
        phase_synthesis_refresh=_phase_synthesis_refresh(),
        events=[],
        checked_at_utc=FIXED_NOW,
    )

    assert strict_exit_code(report) == STRICT_BLOCKED_EXIT_CODE


def test_cli_strict_returns_blocked_after_writing_report(
    tmp_path: Path,
    capsys,
) -> None:
    phase_path = tmp_path / "phase_synthesis_refresh.json"
    events_path = tmp_path / "events.jsonl"
    output_path = tmp_path / "operator_authority_readiness.json"
    phase_path.write_text(
        json.dumps(_phase_synthesis_refresh()),
        encoding="utf-8",
    )
    events_path.write_text("", encoding="utf-8")

    rc = main(
        [
            "--phase-synthesis-refresh",
            str(phase_path),
            "--events",
            str(events_path),
            "--checked-at-utc",
            "2026-05-26T06:00:00Z",
            "--output",
            str(output_path),
            "--json",
            "--strict",
        ]
    )

    assert rc == STRICT_BLOCKED_EXIT_CODE
    stdout_report = json.loads(capsys.readouterr().out)
    disk_report = json.loads(output_path.read_text(encoding="utf-8"))
    assert stdout_report == disk_report
    assert disk_report["activation_blockers"] == [
        "explicit_operator_approval_event_missing"
    ]


def test_cli_strict_passes_when_approval_is_recorded_without_authority_grant(
    tmp_path: Path,
    capsys,
) -> None:
    phase_path = tmp_path / "phase_synthesis_refresh.json"
    events_path = tmp_path / "events.jsonl"
    output_path = tmp_path / "operator_authority_readiness.json"
    phase_path.write_text(
        json.dumps(_phase_synthesis_refresh()),
        encoding="utf-8",
    )
    events_path.write_text(json.dumps(_operator_approval_event()) + "\n")

    rc = main(
        [
            "--phase-synthesis-refresh",
            str(phase_path),
            "--events",
            str(events_path),
            "--checked-at-utc",
            "2026-05-26T06:00:00Z",
            "--output",
            str(output_path),
            "--json",
            "--strict",
        ]
    )

    assert rc == 0
    stdout_report = json.loads(capsys.readouterr().out)
    assert stdout_report["activation_blockers"] == []
    assert stdout_report["authority_activation_status"] == (
        "operator_approved_activation_still_not_granted"
    )
    assert stdout_report["authority_guardrails"]["runtime_authority_granted"] is False
