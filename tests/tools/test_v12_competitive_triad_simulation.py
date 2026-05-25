# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.run_v12_competitive_triad_simulation import (
    REPORT_VERSION,
    build_competitive_triad_simulation,
    render_markdown,
)


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
    assert "This report is a local evidence simulation" in markdown
    assert "Promote hex-cell competition" in markdown


def test_cli_json_reports_real_triad_simulation() -> None:
    result = _run("--json", "--now", "2026-05-24T06:30:00Z")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["report_version"] == REPORT_VERSION
    assert payload["ok"] is True
    assert payload["scenario_count"] == 4
    assert payload["rival_matrix_summary"]["consensus_grade"] is False
    assert payload["no_overclaim_guardrails"]["not_a_competitor_benchmark"] is True


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
    return {
        "ok": True,
        "case_count": 42,
        "pass_count": 42,
        "fail_count": 0,
        "coverage": {
            "risk_class_counts": {
                "external_effect": 22,
                "informational": 10,
                "internal_memory": 5,
                "local_artifact": 5,
            },
        },
    }
