# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.run_magma_100h_sprint_baseline import build_baseline


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "run_magma_100h_sprint_baseline.py"
FIXED_NOW = datetime(2026, 5, 23, 2, 45, tzinfo=timezone.utc)


def _minimal_v12_proof() -> dict[str, object]:
    return {
        "ok": True,
        "adoption": {
            "available": True,
            "high_criticality_gap_count": 0,
            "action_required_gap_count": 0,
            "accepted_exception_count": 1,
            "medium_gap_targets": [
                {
                    "label": "Autonomy runtime MAGMA append path",
                    "path": "waggledance/core/autonomy/runtime.py",
                    "status": "magma_event_only",
                    "accepted_exception": "accepted_observability_path",
                }
            ],
            "status_counts": {"receipt_bound": 6, "magma_event_only": 1},
        },
        "adversarial_eval": {
            "available": True,
            "ok": True,
            "case_count": 20,
            "pass_count": 20,
            "fail_count": 0,
            "gate_accuracy": 1.0,
            "verdict_accuracy": 1.0,
            "reason_code_accuracy": 1.0,
            "coverage": {
                "evaluation_result_case_count": 3,
                "receipt_binding_case_count": 2,
                "counterfactual_case_count": 3,
            },
        },
        "a3_counterfactual_axis": {
            "available": True,
            "claim_label": "MEASURED_LOCAL_PARTIAL",
            "counterfactual_delta_proven": True,
            "receipt_chain_verified": True,
            "variant_count": 3,
            "variants_with_kind_delta": 3,
            "variants_with_gate_delta": 2,
        },
        "a4_solver_growth_axis": {
            "available": True,
            "claim_label": "MEASURED_LOCAL_SYNTHETIC",
            "solver_growth_proven": True,
            "receipt_chain_verified": True,
        },
        "a4_solver_lifecycle": {
            "available": True,
            "ok": True,
            "claim_label": "MEASURED_LOCAL_PARTIAL",
            "runtime_path": "SolverProvenance.{sign,activate,revoke}",
            "transitions": [
                "activation_authorised",
                "activation_revoked",
            ],
            "receipt_count": 2,
            "receipt_chain_verified": True,
            "evidence_scope": (
                "real opt-in SolverProvenance sign/activate/revoke path; "
                "not production auto-promotion authority"
            ),
            "external_writes_applied": False,
            "receipt_emission_mode": "opt_in_disk_bundle_sink",
        },
        "a4_autogrowth_lifecycle": {
            "available": True,
            "ok": True,
            "claim_label": "MEASURED_LOCAL_PARTIAL",
            "runtime_path": (
                "AutogrowthScheduler.tick -> LowRiskGrower.grow_from_gap -> "
                "AutoPromotionEngine.evaluate_candidate"
            ),
            "transitions": ["auto_promoted"],
            "receipt_count": 1,
            "receipt_chain_verified": True,
            "evidence_scope": (
                "real opt-in AutogrowthScheduler queue->grower->engine path; "
                "proof fixture only; not long-running production auto-promotion "
                "authority"
            ),
            "external_writes_applied": False,
            "receipt_emission_mode": "opt_in_disk_bundle_sink",
            "default_sink_required": False,
            "sink_none_preserved": True,
        },
        "a4_autogrowth_soak_fixture": {
            "available": True,
            "ok": True,
            "claim_label": "MEASURED_LOCAL_PARTIAL",
            "runtime_path": (
                "AutogrowthScheduler.run_until_idle -> "
                "LowRiskGrower.grow_from_gap -> AutoPromotionEngine.evaluate_candidate"
            ),
            "round_count": 3,
            "ok_rounds": 3,
            "failed_rounds": 0,
            "intent_count_per_round": 6,
            "expected_receipt_count": 18,
            "total_receipt_count": 18,
            "pass_rate": 1.0,
            "receipt_chain_verified": True,
            "sink_none_preserved": True,
            "raw_payload_leak_check": True,
            "not_release_soak_evidence": True,
            "not_production_authority": True,
            "evidence_scope": (
                "local repeated multi-intent AutogrowthScheduler soak fixture; "
                "not release soak evidence; not long-running production "
                "auto-promotion authority"
            ),
            "stability_metrics": {
                "verifier_failures": 0,
                "sink_none_failures": 0,
                "raw_payload_leak_failures": 0,
            },
        },
        "governance_throughput": {
            "available": True,
            "metric_count": 8,
            "status_counts": {"insufficient_data": 7, "deferred": 1},
        },
        "competitor_pilot": {
            "available": True,
            "pilot_status": "scope_ready_not_consensus_grade",
            "consensus_grade": False,
            "rival_local_checks_status": "1/4 rival local checks passed",
            "rival_local_check_matrix_available": True,
            "rival_local_check_pass_count": 1,
            "rival_local_check_required_count": 4,
            "rival_local_check_blocked_count": 3,
            "rival_local_check_consensus_grade": False,
            "must_win_axes": [
                "A3 counterfactual_evaluation_delta",
                "A4 solver_growth_shadow_canary_live_lifecycle",
            ],
            "ceded_axes": [
                "A6 adapter_distribution_friction",
                "A7 public_cryptographic_verification",
                "A8 standard_policy_language_portability",
            ],
            "rivals": ["JamJet", "Asqav", "Microsoft AGT", "Preloop"],
        },
    }


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_build_baseline_locks_honest_magma_sprint_state() -> None:
    baseline = build_baseline(
        v12_proof=_minimal_v12_proof(),
        generated_at_utc=FIXED_NOW,
    )

    assert baseline["schema_version"] == "waggledance.magma_100h_sprint_baseline.v0"
    assert baseline["sprint_id"] == "magma-100h-2026-05-23"
    assert baseline["generated_at_utc"] == "2026-05-23T02:45:00Z"
    assert baseline["ok"] is True
    assert baseline["blockers"] == []
    assert baseline["release_boundary"] == {
        "docker_latest_move": False,
        "external_effect_authority_change": False,
        "stable_release_claim": False,
        "tag_creation": False,
    }
    assert baseline["current_state"]["competitor_pilot"]["consensus_grade"] is False
    assert baseline["current_state"]["a3_counterfactual_axis"]["variant_count"] == 3
    assert (
        baseline["current_state"]["adversarial_corpus"]["coverage"][
            "evaluation_result_case_count"
        ]
        == 3
    )
    assert (
        baseline["current_state"]["a4_solver_lifecycle"]["claim_label"]
        == "MEASURED_LOCAL_PARTIAL"
    )
    assert baseline["current_state"]["a4_solver_lifecycle"]["receipt_count"] == 2
    assert (
        baseline["current_state"]["a4_solver_lifecycle"]["transitions"]
        == ["activation_authorised", "activation_revoked"]
    )
    assert (
        baseline["current_state"]["a4_autogrowth_lifecycle"]["claim_label"]
        == "MEASURED_LOCAL_PARTIAL"
    )
    assert baseline["current_state"]["a4_autogrowth_lifecycle"]["receipt_count"] == 1
    assert (
        baseline["current_state"]["a4_autogrowth_lifecycle"]["transitions"]
        == ["auto_promoted"]
    )
    assert (
        baseline["current_state"]["a4_autogrowth_lifecycle"]["sink_none_preserved"]
        is True
    )
    assert (
        baseline["current_state"]["a4_autogrowth_soak_fixture"]["claim_label"]
        == "MEASURED_LOCAL_PARTIAL"
    )
    assert baseline["current_state"]["a4_autogrowth_soak_fixture"]["round_count"] == 3
    assert (
        baseline["current_state"]["a4_autogrowth_soak_fixture"][
            "total_receipt_count"
        ]
        == 18
    )
    assert (
        baseline["current_state"]["a4_autogrowth_soak_fixture"][
            "not_release_soak_evidence"
        ]
        is True
    )
    assert (
        baseline["current_state"]["competitor_pilot"]["rival_local_checks_status"]
        == "1/4 rival local checks passed"
    )
    assert baseline["current_state"]["competitor_pilot"]["rival_local_check_pass_count"] == 1
    assert (
        baseline["current_state"]["competitor_pilot"][
            "rival_local_check_consensus_grade"
        ]
        is False
    )
    assert "rival benchmark consensus-grade" in baseline["forbidden_claims"]


def test_baseline_requires_claude_substantive_roles() -> None:
    baseline = build_baseline(
        v12_proof=_minimal_v12_proof(),
        generated_at_utc=FIXED_NOW,
    )

    contract = baseline["claude_activation_contract"]
    assert contract["heartbeat_only_timeout_minutes"] == 5
    assert contract["rco_timeout_minutes_after_ci_green"] == 10
    assert contract["must_emit_substantive_event_per_phase"] is True
    assert {
        "independent_audit",
        "adversarial_review",
        "rco_before_merge",
        "competitor_counter_read",
        "phase_synthesis",
    } <= set(contract["required_roles"])


def test_baseline_fail_closes_on_receipt_gaps() -> None:
    proof = _minimal_v12_proof()
    proof["adoption"] = dict(proof["adoption"])
    proof["adoption"]["high_criticality_gap_count"] = 1

    baseline = build_baseline(v12_proof=proof, generated_at_utc=FIXED_NOW)

    assert baseline["ok"] is False
    assert "high_criticality_receipt_gaps_present" in baseline["blockers"]


def test_baseline_fail_closes_on_missing_a3_receipt_chain() -> None:
    proof = _minimal_v12_proof()
    proof["a3_counterfactual_axis"] = dict(proof["a3_counterfactual_axis"])
    proof["a3_counterfactual_axis"]["receipt_chain_verified"] = False

    baseline = build_baseline(v12_proof=proof, generated_at_utc=FIXED_NOW)

    assert baseline["ok"] is False
    assert "a3_receipt_chain_not_verified" in baseline["blockers"]


def test_baseline_rejects_competitor_consensus_grade_overclaim() -> None:
    proof = _minimal_v12_proof()
    proof["competitor_pilot"] = dict(proof["competitor_pilot"])
    proof["competitor_pilot"]["consensus_grade"] = True

    baseline = build_baseline(v12_proof=proof, generated_at_utc=FIXED_NOW)

    assert baseline["ok"] is False
    assert "competitor_pilot_overclaims_consensus_grade" in baseline["blockers"]


def test_baseline_fail_closes_on_missing_a4_lifecycle_receipt_chain() -> None:
    proof = _minimal_v12_proof()
    proof["a4_solver_lifecycle"] = dict(proof["a4_solver_lifecycle"])
    proof["a4_solver_lifecycle"]["receipt_chain_verified"] = False

    baseline = build_baseline(v12_proof=proof, generated_at_utc=FIXED_NOW)

    assert baseline["ok"] is False
    assert "a4_solver_lifecycle_receipt_chain_not_verified" in baseline["blockers"]


def test_baseline_fail_closes_on_missing_a4_autogrowth_receipt_chain() -> None:
    proof = _minimal_v12_proof()
    proof["a4_autogrowth_lifecycle"] = dict(proof["a4_autogrowth_lifecycle"])
    proof["a4_autogrowth_lifecycle"]["receipt_chain_verified"] = False

    baseline = build_baseline(v12_proof=proof, generated_at_utc=FIXED_NOW)

    assert baseline["ok"] is False
    assert "a4_autogrowth_lifecycle_receipt_chain_not_verified" in (
        baseline["blockers"]
    )


def test_baseline_fail_closes_on_a4_autogrowth_sink_none_regression() -> None:
    proof = _minimal_v12_proof()
    proof["a4_autogrowth_lifecycle"] = dict(proof["a4_autogrowth_lifecycle"])
    proof["a4_autogrowth_lifecycle"]["sink_none_preserved"] = False

    baseline = build_baseline(v12_proof=proof, generated_at_utc=FIXED_NOW)

    assert baseline["ok"] is False
    assert "a4_autogrowth_lifecycle_sink_none_not_preserved" in (
        baseline["blockers"]
    )


def test_baseline_fail_closes_on_a4_autogrowth_soak_receipt_chain() -> None:
    proof = _minimal_v12_proof()
    proof["a4_autogrowth_soak_fixture"] = dict(
        proof["a4_autogrowth_soak_fixture"]
    )
    proof["a4_autogrowth_soak_fixture"]["receipt_chain_verified"] = False

    baseline = build_baseline(v12_proof=proof, generated_at_utc=FIXED_NOW)

    assert baseline["ok"] is False
    assert "a4_autogrowth_soak_fixture_receipt_chain_not_verified" in (
        baseline["blockers"]
    )


def test_baseline_fail_closes_on_a4_autogrowth_soak_release_boundary() -> None:
    proof = _minimal_v12_proof()
    proof["a4_autogrowth_soak_fixture"] = dict(
        proof["a4_autogrowth_soak_fixture"]
    )
    proof["a4_autogrowth_soak_fixture"]["not_release_soak_evidence"] = False

    baseline = build_baseline(v12_proof=proof, generated_at_utc=FIXED_NOW)

    assert baseline["ok"] is False
    assert "a4_autogrowth_soak_fixture_release_boundary_ambiguous" in (
        baseline["blockers"]
    )


def test_cli_writes_json_baseline(tmp_path: Path) -> None:
    output = tmp_path / "baseline.json"

    result = _run("--json", "--output", str(output), "--since-utc-days", "1")

    assert result.returncode == 0, result.stderr
    stdout_payload = json.loads(result.stdout)
    file_payload = json.loads(output.read_text(encoding="utf-8"))
    assert stdout_payload == file_payload
    assert file_payload["ok"] is True
    assert file_payload["current_state"]["a3_counterfactual_axis"]["claim_label"] == (
        "MEASURED_LOCAL_PARTIAL"
    )
    assert file_payload["current_state"]["a4_solver_growth_axis"]["claim_label"] == (
        "MEASURED_LOCAL_SYNTHETIC"
    )
    assert file_payload["current_state"]["a4_solver_lifecycle"]["claim_label"] == (
        "MEASURED_LOCAL_PARTIAL"
    )
    assert file_payload["current_state"]["a4_autogrowth_lifecycle"]["claim_label"] == (
        "MEASURED_LOCAL_PARTIAL"
    )
    assert (
        file_payload["current_state"]["a4_autogrowth_lifecycle"][
            "sink_none_preserved"
        ]
        is True
    )
    assert (
        file_payload["current_state"]["a4_autogrowth_soak_fixture"][
            "not_release_soak_evidence"
        ]
        is True
    )
