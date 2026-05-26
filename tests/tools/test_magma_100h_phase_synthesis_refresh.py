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
    }


def test_report_records_phase_refresh_without_overclaim() -> None:
    report = build_report(
        baseline=_baseline(),
        rival_report=_rival_report(),
        release_gate_report=_release_gate_report(),
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
        package["id"] for package in report["landed_work_packages"]
    ] == [
        "rival_local_evidence_execution_or_accepted_blockers",
        "release_gate_readonly_recheck",
        "phase_synthesis_and_baseline_refresh",
    ]
    assert report["landed_work_packages"][0]["evidence"] == (
        "docs/runs/magma_100h_sprint_2026_05_26/"
        "rival_local_accepted_blockers.json"
    )
    assert report["remaining_work_packages"][0]["status"] == (
        "operator_decision_required"
    )


def test_report_fails_closed_on_baseline_release_boundary_mutation() -> None:
    baseline = _baseline()
    baseline["release_boundary"] = dict(FALSE_RELEASE_BOUNDARY)
    baseline["release_boundary"]["tag_creation"] = True

    report = build_report(
        baseline=baseline,
        rival_report=_rival_report(),
        release_gate_report=_release_gate_report(),
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
        generated_at_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert "release_gate_release_boundary_mutated" in report["blockers"]


def test_cli_writes_phase_synthesis_refresh_report(
    tmp_path: Path,
    capsys,
) -> None:
    baseline_path = tmp_path / "baseline.json"
    rival_path = tmp_path / "rivals.json"
    release_path = tmp_path / "release.json"
    output_path = tmp_path / "phase_synthesis_refresh.json"
    baseline_path.write_text(json.dumps(_baseline()), encoding="utf-8")
    rival_path.write_text(json.dumps(_rival_report()), encoding="utf-8")
    release_path.write_text(json.dumps(_release_gate_report()), encoding="utf-8")

    rc = main(
        [
            "--baseline",
            str(baseline_path),
            "--rival-accepted-blockers",
            str(rival_path),
            "--release-gate-recheck",
            str(release_path),
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
