# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import copy
import datetime as dt
import json
from pathlib import Path

from tools.run_magma_100h_phase_synthesis_refresh import (
    FALSE_RELEASE_BOUNDARY,
    SCHEMA_VERSION,
    build_report,
    main,
)


FIXED_NOW = dt.datetime(2026, 5, 26, 2, 15, tzinfo=dt.UTC)


def _baseline() -> dict[str, object]:
    return {
        "schema_version": "waggledance.magma_100h_sprint_baseline.v0",
        "sprint_id": "magma-100h-sprint3-2026-05-26",
        "generated_at_utc": "2026-05-26T02:10:00Z",
        "ok": True,
        "blockers": [],
        "release_boundary": dict(FALSE_RELEASE_BOUNDARY),
        "forbidden_claims": [
            "beats all competitors",
            "world best AI",
            "AGI",
            "consciousness",
            "production-ready fleet learning",
            "public cryptographic verification parity",
            "rival benchmark consensus-grade",
        ],
        "current_state": {
            "a3_counterfactual_axis": {
                "claim_label": "MEASURED_LOCAL_PARTIAL",
                "counterfactual_delta_proven": True,
                "receipt_chain_verified": True,
                "variant_count": 3,
                "variants_with_gate_delta": 2,
                "variants_with_kind_delta": 3,
            },
            "a4_solver_growth_axis": {
                "claim_label": "MEASURED_LOCAL_SYNTHETIC",
                "solver_growth_proven": True,
                "receipt_chain_verified": True,
            },
            "adversarial_corpus": {
                "available": True,
                "case_count": 42,
                "pass_count": 42,
                "fail_count": 0,
                "gate_accuracy": 1.0,
                "verdict_accuracy": 1.0,
                "reason_code_accuracy": 1.0,
                "coverage": {"counterfactual_case_count": 3},
            },
            "competitor_pilot": {
                "pilot_status": "scope_ready_not_consensus_grade",
                "consensus_grade": False,
                "rival_local_checks_status": "1/4 rival local checks passed",
                "rival_local_check_pass_count": 1,
                "rival_local_check_required_count": 4,
                "rival_local_check_blocked_count": 3,
                "rival_local_check_consensus_grade": False,
                "rivals": ["JamJet", "Asqav", "Microsoft AGT", "Preloop"],
                "ceded_axes": [
                    "A6 adapter_distribution_friction",
                    "A7 public_cryptographic_verification",
                    "A8 standard_policy_language_portability",
                ],
            },
            "governance_throughput": {
                "metric_count": 8,
                "event_count_in_window": 2077,
                "task_count_in_window": 351,
                "window_label": "last-7d",
                "status_counts": {"ok": 5},
            },
            "receipt_adoption": {
                "high_criticality_gap_count": 0,
                "action_required_gap_count": 0,
                "accepted_exception_count": 0,
                "medium_gap_targets": [],
                "status_counts": {"receipt_bound": 3},
            },
        },
        "next_work_packages": [
            {
                "id": "phase_synthesis_and_baseline_refresh",
                "owner": "codex",
                "peer": "claude",
                "target": "summarize landed evidence",
                "acceptance": "baseline remains ok=true",
            }
        ],
    }


def _rival_report() -> dict[str, object]:
    return {
        "ok": True,
        "accepted_blocker_count": 3,
        "passed_count": 1,
        "blocked_count": 3,
        "consensus_grade": False,
        "no_overclaim_guardrails": {
            "blocked_rows_do_not_contribute_to_consensus_grade": True,
            "consensus_grade_remains_false": True,
            "does_not_promote_blocked_rows": True,
            "requires_artifact_digest_verified": True,
        },
        "accepted_blockers": [
            {
                "rival": "JamJet",
                "accepted_blocker": True,
                "artifact_digest_verified": True,
                "consensus_grade_contribution": False,
            },
            {
                "rival": "Asqav",
                "accepted_blocker": True,
                "artifact_digest_verified": True,
                "consensus_grade_contribution": False,
            },
            {
                "rival": "Preloop",
                "accepted_blocker": True,
                "artifact_digest_verified": True,
                "consensus_grade_contribution": False,
            },
        ],
    }


def _release_gate_report() -> dict[str, object]:
    return {
        "ok": True,
        "read_only": True,
        "release_gate_decision": "hold",
        "blockers": [
            "soak_evidence_result_not_pass",
            "soak_evidence_duration_lt_336h",
        ],
        "release_gate_effect": "none",
        "release_boundary": dict(FALSE_RELEASE_BOUNDARY),
        "read_only_invariants": {
            "release_gate_effect": "observation_only",
            "no_tag_created": True,
            "no_docker_latest_moved": True,
            "no_stable_release_claim": True,
            "no_external_effect_authority_change": True,
        },
        "gate": {
            "soak_evidence_diagnostics": {
                "target_version": "v3.12.0",
                "result": "hold",
                "duration_hours": 311.365,
                "required_duration_hours": 336,
                "ended_at_date": "2026-05-22",
                "required_soak_end": "2026-05-24",
                "silent_failures": 0,
                "expected_silent_failures": 0,
                "error_log_clean": True,
                "expected_error_log_clean": True,
                "docker_stable_policy": "finalized",
                "expected_docker_stable_policy": "finalized",
                "status_fields": {
                    "ci_status": {"actual": "pass", "expected": "pass"},
                    "axis_b_gate": {"actual": "pass", "expected": "pass"},
                },
            }
        },
    }


def _release_gate_pass_report() -> dict[str, object]:
    report = _release_gate_report()
    report["release_gate_decision"] = "pass"
    report["blockers"] = []
    diagnostics = report["gate"]["soak_evidence_diagnostics"]
    diagnostics["result"] = "pass"
    diagnostics["duration_hours"] = 336
    diagnostics["ended_at_date"] = "2026-05-24"
    diagnostics["required_soak_end"] = "2026-05-24"
    return report


def _operator_authority_report() -> dict[str, object]:
    return {
        "ok": True,
        "authority_activation_status": "hold_operator_approval_required",
        "activation_blockers": ["explicit_operator_approval_event_missing"],
        "explicit_operator_approval_found": False,
        "release_boundary": dict(FALSE_RELEASE_BOUNDARY),
        "authority_guardrails": {
            "operator_gate_required": True,
            "requires_separate_receipt_bound_activation": True,
            "activation_effect": "none",
            "runtime_authority_granted": False,
            "runtime_traffic_mutation_applied": False,
            "candidate_state_mutation_applied": False,
        },
        "read_only_invariants": {
            "no_runtime_authority_granted": True,
            "no_runtime_traffic_mutated": True,
            "no_candidate_state_mutated": True,
            "no_release_boundary_mutated": True,
        },
        "operator_decision_packet": {
            "schema_version": "waggledance.operator_authority_decision_packet.v0",
            "decision_status": "operator_approval_missing",
            "default_recommendation": "hold_no_authority_change",
            "approval_event_required_for_activation": True,
            "activation_effect_before_followup": "none",
            "decision_options": [
                {
                    "id": "hold_no_authority_change",
                    "runtime_authority_granted": False,
                    "runtime_traffic_mutation_allowed": False,
                    "candidate_state_mutation_allowed": False,
                    "release_boundary_mutation_allowed": False,
                },
                {
                    "id": "approve_receipt_bound_activation_preparation",
                    "runtime_authority_granted": False,
                    "runtime_traffic_mutation_allowed": False,
                    "candidate_state_mutation_allowed": False,
                    "release_boundary_mutation_allowed": False,
                },
            ],
        },
    }


def _release_boundary_readiness_report() -> dict[str, object]:
    return {
        "schema_version": "waggledance.release_boundary_readiness.v0",
        "checked_at_utc": "2026-06-01T03:00:00Z",
        "ok": True,
        "release_boundary_status": "ready_for_operator_finalization",
        "release_boundary_blockers": [],
        "operator_finalization_required": True,
        "release_boundary": dict(FALSE_RELEASE_BOUNDARY),
        "release_boundary_guardrails": {
            "release_boundary_effect": "none",
            "tag_creation_applied": False,
            "docker_latest_move_applied": False,
            "docker_stable_move_applied": False,
            "stable_release_claim_applied": False,
            "external_effect_authority_change_applied": False,
            "requires_operator_only_finalization": True,
        },
        "read_only_invariants": {
            "no_tag_created": True,
            "no_docker_latest_moved": True,
            "no_docker_stable_moved": True,
            "no_stable_release_claim": True,
            "no_external_effect_authority_change": True,
            "release_boundary_effect": "none",
        },
        "source_phase_synthesis_refresh": {
            "schema_version": "waggledance.magma_100h_phase_synthesis_refresh.v0",
            "sprint_id": "magma-100h-sprint3-2026-05-26",
            "generated_at_utc": "2026-06-01T02:56:09Z",
            "ok": True,
            "release_boundary_all_false": True,
            "remaining_release_soak_package": {
                "id": "release_soak_evidence_blocker_resolution",
                "status": "ready_for_release_boundary_review",
                "owner": "operator,codex",
            },
            "landed_release_soak_package": {
                "id": "release_soak_evidence_blocker_resolution",
                "status": None,
                "owner": None,
            },
        },
        "source_release_gate_readonly_recheck": {
            "schema_version": "waggledance.release_gate_readonly_recheck.v0",
            "ok": True,
            "read_only": True,
            "release_gate_decision": "pass",
            "blockers": [],
            "release_gate_effect": "none",
            "release_boundary_all_false": True,
        },
        "release_decision_packet": {
            "schema_version": "waggledance.release_boundary_decision_packet.v0",
            "decision_status": "operator_finalization_required",
            "default_recommendation": "hold_no_release_boundary_change",
            "release_boundary_effect_before_followup": "none",
            "operator_finalization_required": True,
            "decision_options": [
                {
                    "id": "hold_no_release_boundary_change",
                    "tag_creation_allowed": False,
                    "docker_latest_move_allowed": False,
                    "docker_stable_move_allowed": False,
                    "stable_release_claim_allowed": False,
                    "external_effect_authority_change_allowed": False,
                },
                {
                    "id": "operator_finalizes_release_boundary_separately",
                    "tag_creation_allowed": False,
                    "docker_latest_move_allowed": False,
                    "docker_stable_move_allowed": False,
                    "stable_release_claim_allowed": False,
                    "external_effect_authority_change_allowed": False,
                },
            ],
        },
    }


def test_report_records_phase_refresh_without_overclaim() -> None:
    report = build_report(
        baseline=_baseline(),
        rival_report=_rival_report(),
        release_gate_report=_release_gate_report(),
        operator_authority_report=_operator_authority_report(),
        generated_at_utc=FIXED_NOW,
    )

    assert report["schema_version"] == SCHEMA_VERSION
    assert report["generated_at_utc"] == "2026-05-26T02:15:00Z"
    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["release_boundary"] == FALSE_RELEASE_BOUNDARY
    assert report["baseline_refresh"]["baseline_guardrails"][
        "release_boundary_all_false"
    ] is True
    assert report["phase_synthesis"]["baseline_generated_at_utc"] == (
        "2026-05-26T02:10:00Z"
    )
    assert [
        package["id"]
        for package in report["phase_synthesis"]["baseline_next_work_packages"]
    ] == ["phase_synthesis_and_baseline_refresh"]
    assert [
        package["id"] for package in report["phase_synthesis"]["next_work_packages"]
    ] == [
        "operator_gated_authority_activation_decision",
        "release_soak_evidence_blocker_resolution",
    ]
    assert not (
        set(report["phase_synthesis"]["landed_work_package_ids"])
        & {
            package["id"]
            for package in report["phase_synthesis"]["next_work_packages"]
        }
    )
    assert [
        package["id"] for package in report["landed_work_packages"]
    ] == [
        "rival_local_evidence_execution_or_accepted_blockers",
        "release_gate_readonly_recheck",
        "operator_authority_decision_packet",
        "phase_synthesis_and_baseline_refresh",
    ]
    assert report["landed_work_packages"][0]["evidence"] == (
        "docs/runs/magma_100h_sprint_2026_05_26/"
        "rival_local_accepted_blockers.json"
    )
    authority_summary = report["landed_work_packages"][2]["summary"]
    assert authority_summary["report_ok"] is True
    assert authority_summary["authority_activation_status"] == (
        "hold_operator_approval_required"
    )
    assert authority_summary["release_boundary_all_false"] is True
    assert authority_summary["authority_guardrails"] == {
        "operator_gate_required": True,
        "requires_separate_receipt_bound_activation": True,
        "activation_effect": "none",
        "runtime_authority_granted": False,
        "runtime_traffic_mutation_applied": False,
        "candidate_state_mutation_applied": False,
    }
    assert authority_summary["operator_decision_packet"] == {
        "schema_version": "waggledance.operator_authority_decision_packet.v0",
        "decision_status": "operator_approval_missing",
        "default_recommendation": "hold_no_authority_change",
        "approval_event_required_for_activation": True,
        "activation_effect_before_followup": "none",
        "option_count": 2,
        "all_options_non_mutating": True,
    }
    release_summary = report["landed_work_packages"][1]["summary"]
    assert release_summary["decision"] == "hold"
    assert release_summary["soak_evidence_diagnostics"] == {
        "target_version": "v3.12.0",
        "result": "hold",
        "duration_hours": 311.365,
        "required_duration_hours": 336,
        "ended_at_date": "2026-05-22",
        "required_soak_end": "2026-05-24",
        "silent_failures": 0,
        "expected_silent_failures": 0,
        "error_log_clean": True,
        "expected_error_log_clean": True,
        "docker_stable_policy": "finalized",
        "expected_docker_stable_policy": "finalized",
        "status_fields": {
            "axis_b_gate": {"actual": "pass", "expected": "pass"},
            "ci_status": {"actual": "pass", "expected": "pass"},
        },
    }
    assert report["remaining_work_packages"][0]["status"] == (
        "operator_approval_missing_decision_packet_recorded"
    )


def test_report_lands_release_soak_when_boundary_readiness_recorded() -> None:
    report = build_report(
        baseline=_baseline(),
        rival_report=_rival_report(),
        release_gate_report=_release_gate_pass_report(),
        operator_authority_report=_operator_authority_report(),
        release_boundary_readiness_report=_release_boundary_readiness_report(),
        generated_at_utc=FIXED_NOW,
    )

    assert report["ok"] is True
    assert report["blockers"] == []
    assert [
        package["id"] for package in report["phase_synthesis"]["next_work_packages"]
    ] == [
        "operator_gated_authority_activation_decision",
        "operator_release_finalization_decision",
    ]
    assert [
        package["id"] for package in report["landed_work_packages"]
    ] == [
        "rival_local_evidence_execution_or_accepted_blockers",
        "release_gate_readonly_recheck",
        "release_soak_evidence_blocker_resolution",
        "operator_authority_decision_packet",
        "phase_synthesis_and_baseline_refresh",
    ]
    boundary_summary = report["landed_work_packages"][2]["summary"]
    assert boundary_summary["report_ok"] is True
    assert boundary_summary["release_boundary_status"] == (
        "ready_for_operator_finalization"
    )
    assert boundary_summary["release_boundary_blockers"] == []
    assert boundary_summary["operator_finalization_required"] is True
    assert boundary_summary["release_boundary_all_false"] is True
    assert boundary_summary["release_boundary_guardrails"] == {
        "release_boundary_effect": "none",
        "tag_creation_applied": False,
        "docker_latest_move_applied": False,
        "docker_stable_move_applied": False,
        "stable_release_claim_applied": False,
        "external_effect_authority_change_applied": False,
        "requires_operator_only_finalization": True,
    }
    assert boundary_summary["release_decision_packet"] == {
        "schema_version": "waggledance.release_boundary_decision_packet.v0",
        "decision_status": "operator_finalization_required",
        "default_recommendation": "hold_no_release_boundary_change",
        "operator_finalization_required": True,
        "release_boundary_effect_before_followup": "none",
        "option_count": 2,
        "all_options_non_mutating": True,
    }
    assert boundary_summary["source_phase_synthesis_refresh"] == {
        "schema_version": "waggledance.magma_100h_phase_synthesis_refresh.v0",
        "sprint_id": "magma-100h-sprint3-2026-05-26",
        "generated_at_utc": "2026-06-01T02:56:09Z",
        "ok": True,
        "release_boundary_all_false": True,
        "remaining_release_soak_status": "ready_for_release_boundary_review",
        "landed_release_soak_status": None,
    }
    assert boundary_summary["source_release_gate_readonly_recheck"] == {
        "schema_version": "waggledance.release_gate_readonly_recheck.v0",
        "ok": True,
        "read_only": True,
        "release_gate_decision": "pass",
        "release_gate_effect": "none",
        "release_boundary_all_false": True,
        "blockers": [],
    }
    assert report["remaining_work_packages"][1]["status"] == (
        "operator_release_finalization_required"
    )


def test_report_fails_closed_on_baseline_release_boundary_mutation() -> None:
    baseline = _baseline()
    baseline["release_boundary"] = dict(FALSE_RELEASE_BOUNDARY)
    baseline["release_boundary"]["tag_creation"] = True

    report = build_report(
        baseline=baseline,
        rival_report=_rival_report(),
        release_gate_report=_release_gate_report(),
        operator_authority_report=_operator_authority_report(),
        generated_at_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert "baseline_release_boundary_mutated" in report["blockers"]


def test_report_fails_closed_on_rival_consensus_overclaim() -> None:
    rival_report = _rival_report()
    rival_report["consensus_grade"] = True

    report = build_report(
        baseline=_baseline(),
        rival_report=rival_report,
        release_gate_report=_release_gate_report(),
        operator_authority_report=_operator_authority_report(),
        generated_at_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert "rival_consensus_grade_overclaim" in report["blockers"]


def test_report_fails_closed_on_blocked_rival_contribution() -> None:
    rival_report = _rival_report()
    accepted = copy.deepcopy(rival_report["accepted_blockers"])
    accepted[0]["consensus_grade_contribution"] = True
    rival_report["accepted_blockers"] = accepted

    report = build_report(
        baseline=_baseline(),
        rival_report=rival_report,
        release_gate_report=_release_gate_report(),
        operator_authority_report=_operator_authority_report(),
        generated_at_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert "rival_blockers_not_verified_non_contributing" in report["blockers"]


def test_report_fails_closed_on_release_gate_boundary_mutation() -> None:
    release_report = _release_gate_report()
    release_report["release_boundary"] = dict(FALSE_RELEASE_BOUNDARY)
    release_report["release_boundary"]["docker_latest_move"] = True

    report = build_report(
        baseline=_baseline(),
        rival_report=_rival_report(),
        release_gate_report=release_report,
        operator_authority_report=_operator_authority_report(),
        generated_at_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert "release_gate_release_boundary_mutated" in report["blockers"]


def test_report_fails_closed_on_release_boundary_readiness_mutation() -> None:
    readiness = _release_boundary_readiness_report()
    guardrails = dict(readiness["release_boundary_guardrails"])
    guardrails["docker_latest_move_applied"] = True
    readiness["release_boundary_guardrails"] = guardrails

    report = build_report(
        baseline=_baseline(),
        rival_report=_rival_report(),
        release_gate_report=_release_gate_pass_report(),
        operator_authority_report=_operator_authority_report(),
        release_boundary_readiness_report=readiness,
        generated_at_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert "release_boundary_docker_latest_moved" in report["blockers"]


def test_report_fails_closed_on_release_boundary_source_sprint_mismatch() -> None:
    readiness = _release_boundary_readiness_report()
    source_phase = dict(readiness["source_phase_synthesis_refresh"])
    source_phase["sprint_id"] = "wrong-sprint"
    readiness["source_phase_synthesis_refresh"] = source_phase

    report = build_report(
        baseline=_baseline(),
        rival_report=_rival_report(),
        release_gate_report=_release_gate_pass_report(),
        operator_authority_report=_operator_authority_report(),
        release_boundary_readiness_report=readiness,
        generated_at_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert "release_boundary_source_sprint_mismatch" in report["blockers"]
    assert [
        package["id"] for package in report["landed_work_packages"]
    ] == [
        "rival_local_evidence_execution_or_accepted_blockers",
        "release_gate_readonly_recheck",
        "operator_authority_decision_packet",
        "phase_synthesis_and_baseline_refresh",
    ]
    assert [
        package["id"] for package in report["remaining_work_packages"]
    ] == [
        "operator_gated_authority_activation_decision",
        "release_soak_evidence_blocker_resolution",
    ]
    assert report["remaining_work_packages"][1]["status"] == (
        "release_boundary_readiness_blocked"
    )


def test_report_fails_closed_on_release_boundary_source_gate_hold() -> None:
    readiness = _release_boundary_readiness_report()
    source_gate = dict(readiness["source_release_gate_readonly_recheck"])
    source_gate["release_gate_decision"] = "hold"
    source_gate["blockers"] = ["soak_evidence_duration_lt_336h"]
    readiness["source_release_gate_readonly_recheck"] = source_gate

    report = build_report(
        baseline=_baseline(),
        rival_report=_rival_report(),
        release_gate_report=_release_gate_pass_report(),
        operator_authority_report=_operator_authority_report(),
        release_boundary_readiness_report=readiness,
        generated_at_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert "release_boundary_source_gate_not_pass" in report["blockers"]
    assert "release_boundary_source_gate_blockers_present" in report["blockers"]
    assert [
        package["id"] for package in report["landed_work_packages"]
    ] == [
        "rival_local_evidence_execution_or_accepted_blockers",
        "release_gate_readonly_recheck",
        "operator_authority_decision_packet",
        "phase_synthesis_and_baseline_refresh",
    ]
    assert [
        package["id"] for package in report["remaining_work_packages"]
    ] == [
        "operator_gated_authority_activation_decision",
        "release_soak_evidence_blocker_resolution",
    ]
    assert report["remaining_work_packages"][1]["status"] == (
        "release_boundary_readiness_blocked"
    )


def test_report_fails_closed_on_operator_authority_mutation() -> None:
    authority_report = _operator_authority_report()
    authority_guardrails = dict(authority_report["authority_guardrails"])
    authority_guardrails["runtime_authority_granted"] = True
    authority_report["authority_guardrails"] = authority_guardrails

    report = build_report(
        baseline=_baseline(),
        rival_report=_rival_report(),
        release_gate_report=_release_gate_report(),
        operator_authority_report=authority_report,
        generated_at_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert "operator_authority_runtime_authority_granted" in report["blockers"]


def test_report_fails_closed_on_mutating_operator_decision_option() -> None:
    authority_report = _operator_authority_report()
    packet = dict(authority_report["operator_decision_packet"])
    options = copy.deepcopy(packet["decision_options"])
    options[1]["candidate_state_mutation_allowed"] = True
    packet["decision_options"] = options
    authority_report["operator_decision_packet"] = packet

    report = build_report(
        baseline=_baseline(),
        rival_report=_rival_report(),
        release_gate_report=_release_gate_report(),
        operator_authority_report=authority_report,
        generated_at_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert "operator_authority_decision_option_mutates" in report["blockers"]


def test_cli_writes_phase_synthesis_refresh_report(
    tmp_path: Path,
    capsys,
) -> None:
    baseline_path = tmp_path / "baseline.json"
    rival_path = tmp_path / "rivals.json"
    release_path = tmp_path / "release.json"
    operator_path = tmp_path / "operator_authority.json"
    output_path = tmp_path / "phase_synthesis_refresh.json"
    baseline_path.write_text(json.dumps(_baseline()), encoding="utf-8")
    rival_path.write_text(json.dumps(_rival_report()), encoding="utf-8")
    release_path.write_text(json.dumps(_release_gate_report()), encoding="utf-8")
    operator_path.write_text(
        json.dumps(_operator_authority_report()),
        encoding="utf-8",
    )

    rc = main(
        [
            "--baseline",
            str(baseline_path),
            "--rival-accepted-blockers",
            str(rival_path),
            "--release-gate-recheck",
            str(release_path),
            "--operator-authority-readiness",
            str(operator_path),
            "--skip-release-boundary-readiness",
            "--generated-at-utc",
            "2026-05-26T02:15:00Z",
            "--output",
            str(output_path),
            "--json",
        ]
    )

    assert rc == 0
    stdout_report = json.loads(capsys.readouterr().out)
    disk_report = json.loads(output_path.read_text(encoding="utf-8"))
    assert stdout_report == disk_report
    assert disk_report["ok"] is True
    assert disk_report["phase_synthesis"]["sprint_id"] == (
        "magma-100h-sprint3-2026-05-26"
    )
