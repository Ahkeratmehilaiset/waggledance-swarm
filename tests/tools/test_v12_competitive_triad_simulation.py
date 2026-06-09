# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

import tools.run_v12_competitive_triad_simulation as triad
from tools.run_v12_competitive_triad_simulation import (
    REPORT_VERSION,
    build_competitive_triad_simulation,
    render_markdown,
)
from waggledance.core.magma.adversarial_corpus_eval import REQUIRED_DEFECT_TYPES


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "run_v12_competitive_triad_simulation.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_build_competitive_triad_simulation_keeps_guardrails() -> None:
    report = build_competitive_triad_simulation(
        now_utc=_fixed_now(),
        v12_proof=_v12_proof(),
        rival_matrix=_rival_matrix(),
        adversarial_report=_adversarial_report(),
    )

    assert report["report_version"] == REPORT_VERSION
    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["scenario_count"] == 4
    assert len(report["triad_profiles"]) == 3
    assert report["rival_matrix_summary"]["consensus_grade"] is False
    assert report["hex_cell_probe"]["authority_status"] == "non_authority_contract"
    assert report["hex_cell_probe"]["promotion_lifecycle"] == [
        "competition_non_authority",
        "promotion_acceptance_operator_gate_required",
        "operator_gate_authorization_cleared",
        "receipt_bound_activation_preflight",
    ]
    assert report["hex_cell_probe"]["promotion_acceptance_status"] == (
        triad.HEX_CELL_PROMOTION_ACCEPTANCE_STATUS
    )
    assert report["hex_cell_probe"]["operator_authorization_status"] == (
        triad.HEX_CELL_OPERATOR_GATE_AUTHORIZATION_STATUS
    )
    assert report["hex_cell_probe"]["activation_preflight_status"] == (
        triad.HEX_CELL_ACTIVATION_PREFLIGHT_STATUS
    )
    assert report["hex_cell_probe"]["operator_gate_cleared"] is True
    assert report["hex_cell_probe"]["receipt_bound_activation_verified"] is True
    # Winner-margin governance view: the probe's fixture (b=0.91 > c=0.86) is a
    # clear win, not a tie-break — surfaced for the operator at the gate.
    assert report["hex_cell_probe"]["winner_id"] == "triad-cand-b"
    assert report["hex_cell_probe"]["runner_up_id"] == "triad-cand-c"
    assert report["hex_cell_probe"]["winner_margin"] > 0.0
    assert report["hex_cell_probe"]["decided_by_tiebreak"] is False
    assert report["hex_cell_probe"]["runtime_authority_granted"] is False
    assert report["hex_cell_probe"]["runtime_traffic_mutation_applied"] is False
    assert report["hex_cell_probe"]["candidate_state_mutation_applied"] is False
    assert report["no_overclaim_guardrails"] == {
        "not_a_competitor_benchmark": True,
        "does_not_execute_untrusted_rival_code": True,
        "does_not_install_rival_sdks": True,
        "does_not_rank_rivals": True,
        "does_not_claim_frontier_model_superiority": True,
        "keeps_consensus_grade_false": True,
        "hex_competition_non_authority": True,
        "hex_promotion_lifecycle_receipt_bound": True,
        "hex_promotion_lifecycle_no_runtime_authority": True,
        "hex_promotion_lifecycle_statuses_expected": True,
    }
    assert "offline_adversarial_review" in report[
        "wd_local_evidence_only_scenarios"
    ]


def test_scenarios_report_rival_capability_gaps_without_ranking() -> None:
    report = build_competitive_triad_simulation(
        now_utc=_fixed_now(),
        v12_proof=_v12_proof(),
        rival_matrix=_rival_matrix(),
        adversarial_report=_adversarial_report(),
    )

    by_id = {row["scenario_id"]: row for row in report["scenarios"]}
    solver_growth = by_id["solver_growth_hex_competition"]

    assert solver_growth["wd_status"] == "measured"
    assert {row["profile_id"] for row in solver_growth["rival_profile_results"]} == {
        "governance_policy_runtime",
        "durable_graph_runtime",
        "multi_agent_workflow_runtime",
    }
    assert any(
        row["status"] == "capability_gap"
        for row in solver_growth["rival_profile_results"]
    )
    assert "does_not_rank_rivals" in report["no_overclaim_guardrails"]


def test_consensus_grade_true_is_blocked() -> None:
    rival = dict(_rival_matrix())
    rival["consensus_grade"] = True

    report = build_competitive_triad_simulation(
        now_utc=_fixed_now(),
        v12_proof=_v12_proof(),
        rival_matrix=rival,
        adversarial_report=_adversarial_report(),
    )

    assert report["ok"] is False
    assert "rival_consensus_grade_must_remain_false" in report["blockers"]
    assert report["no_overclaim_guardrails"]["keeps_consensus_grade_false"] is False


def test_forged_adversarial_case_aggregates_are_blocked() -> None:
    adversarial = _adversarial_report()
    adversarial["cases"][-1]["ok"] = False
    adversarial["ok"] = True
    adversarial["pass_count"] = adversarial["case_count"]
    adversarial["fail_count"] = 0

    report = build_competitive_triad_simulation(
        now_utc=_fixed_now(),
        v12_proof=_v12_proof(),
        rival_matrix=_rival_matrix(),
        adversarial_report=adversarial,
    )

    assert report["ok"] is False
    assert report["wd_signals"]["adversarial_full_pass"] is False
    assert "adversarial_eval_cases_not_caught" in report["blockers"]
    assert "adversarial_eval_pass_count_mismatch" in report["blockers"]
    assert "adversarial_eval_fail_count_mismatch" in report["blockers"]


def test_gutted_adversarial_defect_class_coverage_is_blocked() -> None:
    adversarial = _adversarial_report()
    one_defect = sorted(REQUIRED_DEFECT_TYPES)[0]
    for case in adversarial["cases"]:
        case["defect_class"] = one_defect

    report = build_competitive_triad_simulation(
        now_utc=_fixed_now(),
        v12_proof=_v12_proof(),
        rival_matrix=_rival_matrix(),
        adversarial_report=adversarial,
    )

    assert report["ok"] is False
    assert report["wd_signals"]["adversarial_full_pass"] is False
    assert "adversarial_eval_required_defect_classes_missing" in report["blockers"]


@pytest.mark.parametrize(
    ("mutation", "expected_blockers"),
    [
        (
            "top_ok_false",
            {"adversarial_eval_not_ok"},
        ),
        (
            "missing_cases",
            {"adversarial_eval_cases_missing_or_invalid"},
        ),
        (
            "case_count_bool",
            {"adversarial_eval_case_count_not_int"},
        ),
        (
            "case_count_mismatch",
            {"adversarial_eval_case_count_mismatch"},
        ),
        (
            "case_not_mapping",
            {
                "adversarial_eval_cases_invalid",
                "adversarial_eval_pass_count_mismatch",
                "adversarial_eval_fail_count_mismatch",
            },
        ),
        (
            "case_ok_string",
            {
                "adversarial_eval_cases_invalid",
                "adversarial_eval_pass_count_mismatch",
                "adversarial_eval_fail_count_mismatch",
            },
        ),
        (
            "invalid_defect_class",
            {
                "adversarial_eval_cases_invalid",
                "adversarial_eval_fail_count_mismatch",
            },
        ),
    ],
)
def test_malformed_adversarial_report_shapes_are_blocked(
    mutation: str,
    expected_blockers: set[str],
) -> None:
    adversarial = _adversarial_report()
    if mutation == "top_ok_false":
        adversarial["ok"] = False
    elif mutation == "missing_cases":
        adversarial.pop("cases")
    elif mutation == "case_count_bool":
        adversarial["case_count"] = True
    elif mutation == "case_count_mismatch":
        adversarial["case_count"] = adversarial["case_count"] + 1
    elif mutation == "case_not_mapping":
        adversarial["cases"][0] = "not-a-case"
    elif mutation == "case_ok_string":
        adversarial["cases"][0]["ok"] = "true"
    elif mutation == "invalid_defect_class":
        adversarial["cases"][0]["defect_class"] = "unknown"
    else:  # pragma: no cover - protects the parametrized table.
        raise AssertionError(f"unhandled mutation: {mutation}")

    report = build_competitive_triad_simulation(
        now_utc=_fixed_now(),
        v12_proof=_v12_proof(),
        rival_matrix=_rival_matrix(),
        adversarial_report=adversarial,
    )

    assert report["ok"] is False
    assert report["wd_signals"]["adversarial_full_pass"] is False
    assert expected_blockers <= set(report["blockers"])


def test_render_markdown_carries_scope_and_next_100h() -> None:
    report = build_competitive_triad_simulation(
        now_utc=_fixed_now(),
        v12_proof=_v12_proof(),
        rival_matrix=_rival_matrix(),
        adversarial_report=_adversarial_report(),
    )

    markdown = render_markdown(report)

    assert "WD V12 Competitive Triad Simulation" in markdown
    assert "rival local checks: `1/4 rival local checks passed`" in markdown
    assert "consensus_grade: `false`" in markdown
    assert "solver_growth_hex_competition" in markdown
    assert "receipt_bound_activation_preflight" in markdown
    assert "runtime_authority_granted: `false`" in markdown
    assert "This report is a local evidence simulation" in markdown
    assert "runtime-authority commit gate" in markdown


@pytest.mark.parametrize(
    ("field", "value", "expected_blocker", "expect_solver_growth_blocked"),
    [
        (
            "receipt_bound_activation_verified",
            False,
            "hex_promotion_lifecycle_not_receipt_bound",
            False,
        ),
        (
            "runtime_authority_granted",
            True,
            "hex_promotion_lifecycle_runtime_authority_granted",
            True,
        ),
        (
            "promotion_acceptance_status",
            "runtime_authority_granted",
            "hex_promotion_lifecycle_acceptance_status_drift",
            False,
        ),
    ],
)
def test_hex_promotion_lifecycle_drift_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
    expected_blocker: str,
    expect_solver_growth_blocked: bool,
) -> None:
    probe = triad._build_hex_competition_probe()
    probe[field] = value
    monkeypatch.setattr(triad, "_build_hex_competition_probe", lambda: probe)

    report = build_competitive_triad_simulation(
        now_utc=_fixed_now(),
        v12_proof=_v12_proof(),
        rival_matrix=_rival_matrix(),
        adversarial_report=_adversarial_report(),
    )

    assert report["ok"] is False
    assert expected_blocker in report["blockers"]
    solver_growth = {
        row["scenario_id"]: row
        for row in report["scenarios"]
    }["solver_growth_hex_competition"]
    if expect_solver_growth_blocked:
        assert report["wd_signals"]["a4_solver_growth_hex_non_authority"] is False
        assert solver_growth["wd_status"] == "blocked"
        assert (
            "solver_growth_hex_competition"
            not in report["wd_local_evidence_only_scenarios"]
        )


def test_cli_json_reports_real_triad_simulation() -> None:
    result = _run("--json", "--now", "2026-05-24T06:30:00Z")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["report_version"] == REPORT_VERSION
    assert payload["ok"] is True
    assert payload["scenario_count"] == 4
    assert payload["rival_matrix_summary"]["consensus_grade"] is False
    assert payload["no_overclaim_guardrails"]["not_a_competitor_benchmark"] is True
    assert payload["hex_cell_probe"]["receipt_bound_activation_verified"] is True
    assert payload["hex_cell_probe"]["runtime_authority_granted"] is False


def test_cli_rejects_markdown_out_outside_repo(tmp_path: Path) -> None:
    out = tmp_path / "competitive-triad.md"

    result = _run("--markdown-out", str(out))

    assert result.returncode == 1
    assert "markdown_out must stay under repo root" in result.stderr
    assert not out.exists()


def _fixed_now() -> datetime:
    return datetime(2026, 5, 24, 6, 30, tzinfo=timezone.utc)


def _v12_proof() -> dict:
    return {
        "ok": True,
        "a3_counterfactual_axis": {
            "claim_label": "MEASURED_LOCAL_PARTIAL",
            "counterfactual_delta_proven": True,
            "receipt_chain_verified": True,
        },
        "a4_solver_growth_axis": {
            "claim_label": "MEASURED_LOCAL_SYNTHETIC",
            "solver_growth_proven": True,
            "receipt_chain_verified": True,
        },
        "adoption": {
            "high_criticality_gap_count": 0,
            "status_counts": {
                "receipt_bound": 3,
                "receipt_capable_opt_in": 4,
            },
        },
    }


def _rival_matrix() -> dict:
    return {
        "passed_count": 1,
        "required_count": 4,
        "blocked_count": 3,
        "consensus_grade": False,
        "rival_local_checks_status": "1/4 rival local checks passed",
    }


def _adversarial_report() -> dict:
    required = sorted(REQUIRED_DEFECT_TYPES)
    return {
        "ok": True,
        "case_count": 42,
        "pass_count": 42,
        "fail_count": 0,
        "cases": [
            {
                "case_id": f"case-{i}",
                "defect_class": required[i % len(required)],
                "ok": True,
            }
            for i in range(42)
        ],
        "coverage": {
            "risk_class_counts": {
                "external_effect": 22,
                "informational": 10,
                "internal_memory": 5,
                "local_artifact": 5,
            },
        },
    }
