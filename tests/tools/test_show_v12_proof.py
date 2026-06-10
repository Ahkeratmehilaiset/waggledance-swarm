# SPDX-License-Identifier: BUSL-1.1
"""Tests for tools/show_v12_proof.py."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "show_v12_proof.py"
CORPUS = ROOT / "tests" / "fixtures" / "magma_adversarial_corpus" / "v0.json"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _expected_case_count() -> int:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    return len(corpus["cases"])


def test_default_text_output_includes_expected_sections() -> None:
    result = _run()

    assert result.returncode in {0, 1}, result.stderr
    out = result.stdout
    for marker in (
        "WaggleDance V12 substrate proof",
        "AUTHORITY-RECEIPT ADOPTION",
        "ADVERSARIAL CORPUS",
        "A3 COUNTERFACTUAL AXIS",
        "A4 SOLVER-GROWTH AXIS",
        "A4 SOLVER LIFECYCLE RECEIPTS",
        "A4 AUTOGROWTH LIFECYCLE RECEIPTS",
        "A4 AUTOGROWTH SOAK FIXTURE RECEIPTS",
        "MEMORY PALACE SHORTCUTS",
        "MEMORY PALACE PROMOTION CANDIDATES",
        "GOVERNANCE THROUGHPUT",
        "COMPETITOR-AXIS PILOT",
        "SUBSTRATE VELOCITY",
        "VERIFICATION",
    ):
        assert marker in out, f"missing section: {marker}"


def test_json_output_is_parseable_and_has_expected_keys() -> None:
    result = _run("--json")

    assert result.returncode in {0, 1}, result.stderr
    payload = json.loads(result.stdout)
    assert payload["report_version"] == "waggledance.v12_substrate_proof.v0"
    assert "adoption" in payload
    assert "adversarial_eval" in payload
    assert "a3_counterfactual_axis" in payload
    assert "a4_solver_growth_axis" in payload
    assert "a4_solver_lifecycle" in payload
    assert "a4_autogrowth_lifecycle" in payload
    assert "a4_autogrowth_soak_fixture" in payload
    assert "memory_palace_shortcuts" in payload
    assert "memory_palace_promotion_candidates" in payload
    assert "governance_throughput" in payload
    assert "competitor_pilot" in payload
    assert "substrate_velocity" in payload
    assert "generated_at_utc" in payload


def test_competitor_pilot_section_resolves_axes_from_doc() -> None:
    result = _run("--json")
    payload = json.loads(result.stdout)
    pilot = payload["competitor_pilot"]

    assert pilot["available"] is True, pilot
    assert pilot["bridge_consensus_sealed"] is True
    assert pilot["pilot_status"] == "scope_ready_not_consensus_grade"
    assert pilot["consensus_grade"] is False
    assert any("A3" in axis for axis in pilot["must_win_axes"]), pilot["must_win_axes"]
    assert any("A4" in axis for axis in pilot["must_win_axes"]), pilot["must_win_axes"]
    assert pilot["rivals"], "expected at least one rival"
    assert pilot["rival_local_checks_status"] == "1/4 rival local checks passed"
    assert pilot["rival_local_check_matrix_available"] is True
    assert pilot["rival_local_check_pass_count"] == 1
    assert pilot["rival_local_check_required_count"] == 4
    assert pilot["rival_local_check_blocked_count"] == 3
    assert pilot["rival_local_check_consensus_grade"] is False
    assert pilot["rival_evidence_template_count"] == 4
    assert "safe non-passing templates" in pilot["rival_evidence_template_status"]
    assert pilot["supervisor_demo_pack_command"] == (
        "python tools/run_v12_supervisor_demo_pack.py --out-dir <new-output-dir>"
    )


def test_competitor_pilot_text_separates_status_from_consensus_grade() -> None:
    result = _run()

    assert result.returncode in {0, 1}, result.stderr
    assert "pilot status                     : scope_ready_not_consensus_grade" in result.stdout
    assert "consensus grade                  : False" in result.stdout
    assert "rival-local checks status        : 1/4 rival local checks passed" in result.stdout
    assert "rival-local checks passed        : 1/4" in result.stdout
    assert "rival evidence templates         : 4 safe non-passing templates via supervisor demo pack" in result.stdout
    assert "demo pack command                : python tools/run_v12_supervisor_demo_pack.py --out-dir <new-output-dir>" in result.stdout
    assert "python tools/run_v12_supervisor_demo_pack.py --out-dir <new-output-dir>" in result.stdout


def test_adoption_high_gap_count_is_zero_on_current_main() -> None:
    result = _run("--json")
    payload = json.loads(result.stdout)
    adoption = payload["adoption"]

    assert adoption["available"] is True, adoption
    assert adoption["high_criticality_gap_count"] == 0, adoption
    assert adoption["action_required_gap_count"] == 0, adoption
    assert adoption["accepted_exception_count"] == 0, adoption
    assert adoption["status_counts"] == {
        "receipt_bound": 3,
        "receipt_capable_opt_in": 4,
    }
    assert adoption["medium_gap_targets"] == []


def test_text_output_does_not_overstate_medium_non_receipt_paths() -> None:
    result = _run()

    assert result.returncode in {0, 1}, result.stderr
    assert "medium non-receipt paths" not in result.stdout
    assert "accepted_observability_path" not in result.stdout
    assert "medium accepted-exception paths" not in result.stdout


def test_adversarial_corpus_section_reports_fixture_case_count() -> None:
    expected_case_count = _expected_case_count()
    result = _run("--json")
    payload = json.loads(result.stdout)
    adv = payload["adversarial_eval"]

    assert adv["available"] is True, adv
    assert adv["case_count"] == expected_case_count
    assert adv["pass_count"] == expected_case_count
    assert adv["fail_count"] == 0
    assert adv["ok"] is True
    assert adv["coverage"]["evaluation_result_case_count"] >= 3
    assert adv["coverage"]["receipt_binding_case_count"] >= 2
    assert adv["coverage"]["counterfactual_case_count"] >= 3


def test_a3_counterfactual_axis_section_reports_measured_partial() -> None:
    result = _run("--json")
    payload = json.loads(result.stdout)
    a3 = payload["a3_counterfactual_axis"]

    assert a3["available"] is True, a3
    assert a3["counterfactual_delta_proven"] is True, a3
    assert a3["claim_label"] == "MEASURED_LOCAL_PARTIAL"
    assert a3["variant_count"] == 4
    assert a3["variants_with_kind_delta"] == 4
    assert a3["variants_with_gate_delta"] == 3
    assert a3["delta"]["kind"] == ["KEEP_WIP", "CLOSE_OK"]
    assert a3["delta"]["actual_gate"] == ["review", "allow"]
    # Oracle-agreement direction surfaced from the A3 runtime smoke (its
    # recompute oracle agrees with both arms -> all 24 divergences neutral).
    direction = a3["runtime_smoke_direction"]
    assert direction["improvement_count"] == 0
    assert direction["regression_count"] == 0
    assert direction["neutral_divergence_count"] == 24
    # show_v12_proof now runs the A3 proof tool with --out-dir, so the
    # receipt bundle is built and verified end-to-end.
    assert a3["receipt_chain_verified"] is True


def test_a4_solver_growth_axis_section_reports_measured_synthetic() -> None:
    result = _run("--json")
    payload = json.loads(result.stdout)
    a4 = payload["a4_solver_growth_axis"]

    assert a4["available"] is True, a4
    assert a4["solver_growth_proven"] is True, a4
    assert a4["claim_label"] == "MEASURED_LOCAL_SYNTHETIC"
    assert a4["registration"]["registered_solver_count"] == 6
    assert a4["registration"]["rejected_registration_count"] == 8
    assert a4["dispatch"]["dispatch_success_count"] == 30
    assert a4["dispatch"]["dispatch_case_count"] == 30
    assert a4["dispatch"]["dispatch_failure_count"] == 0
    assert a4["dispatch"]["families_covered"] == 6
    # show_v12_proof now runs the A4 proof tool with --out-dir, so the
    # receipt bundle is built and verified end-to-end.
    assert a4["receipt_chain_verified"] is True


def test_a4_solver_lifecycle_section_reports_opt_in_runtime_path() -> None:
    result = _run("--json")
    payload = json.loads(result.stdout)
    lifecycle = payload["a4_solver_lifecycle"]

    assert lifecycle["available"] is True, lifecycle
    assert lifecycle["ok"] is True, lifecycle
    assert lifecycle["claim_label"] == "MEASURED_LOCAL_PARTIAL"
    assert lifecycle["runtime_path"] == "SolverProvenance.{sign,activate,revoke}"
    assert lifecycle["transitions"] == [
        "activation_authorised",
        "activation_revoked",
    ]
    assert lifecycle["receipt_count"] == 2
    assert lifecycle["receipt_chain_verified"] is True
    assert lifecycle["risk_class"] == "local_artifact"
    assert lifecycle["operator_gate_required"] is False
    assert lifecycle["external_writes_applied"] is False
    assert lifecycle["receipt_emission_mode"] == "opt_in_disk_bundle_sink"
    assert "not production auto-promotion" in lifecycle["evidence_scope"]


def test_a4_autogrowth_lifecycle_section_reports_scheduler_runtime_path() -> None:
    result = _run("--json")
    payload = json.loads(result.stdout)
    autogrowth = payload["a4_autogrowth_lifecycle"]

    assert autogrowth["available"] is True, autogrowth
    assert autogrowth["ok"] is True, autogrowth
    assert autogrowth["claim_label"] == "MEASURED_LOCAL_PARTIAL"
    assert autogrowth["runtime_path"] == (
        "AutogrowthScheduler.tick -> LowRiskGrower.grow_from_gap -> "
        "AutoPromotionEngine.evaluate_candidate"
    )
    assert autogrowth["transitions"] == ["auto_promoted"]
    assert autogrowth["receipt_count"] == 1
    assert autogrowth["receipt_chain_verified"] is True
    assert autogrowth["risk_class"] == "local_artifact"
    assert autogrowth["operator_gate_required"] is False
    assert autogrowth["external_writes_applied"] is False
    assert autogrowth["receipt_emission_mode"] == "opt_in_disk_bundle_sink"
    assert autogrowth["default_sink_required"] is False
    assert autogrowth["sink_none_preserved"] is True
    assert "proof fixture only" in autogrowth["evidence_scope"]
    assert "not long-running production auto-promotion" in (
        autogrowth["evidence_scope"]
    )


def test_a4_autogrowth_soak_fixture_section_reports_local_boundary() -> None:
    result = _run("--json")
    payload = json.loads(result.stdout)
    soak = payload["a4_autogrowth_soak_fixture"]

    assert soak["available"] is True, soak
    assert soak["ok"] is True, soak
    assert soak["claim_label"] == "MEASURED_LOCAL_PARTIAL"
    assert soak["runtime_path"] == (
        "AutogrowthScheduler.run_until_idle -> LowRiskGrower.grow_from_gap -> "
        "AutoPromotionEngine.evaluate_candidate"
    )
    assert soak["round_count"] == 3
    assert soak["ok_rounds"] == 3
    assert soak["failed_rounds"] == 0
    assert soak["intent_count_per_round"] == 6
    assert soak["expected_receipt_count"] == 18
    assert soak["total_receipt_count"] == 18
    assert soak["pass_rate"] == 1.0
    assert soak["receipt_chain_verified"] is True
    assert soak["sink_none_preserved"] is True
    assert soak["raw_payload_leak_check"] is True
    assert soak["not_release_soak_evidence"] is True
    assert soak["not_production_authority"] is True
    assert "not release soak evidence" in soak["evidence_scope"]
    assert "not long-running production" in soak["evidence_scope"]


def test_text_output_separates_synthetic_a4_axis_from_lifecycle_path() -> None:
    result = _run()

    assert result.returncode in {0, 1}, result.stderr
    assert "evidence scope                 : synthetic Phase 18C dispatch fixture" in result.stdout
    assert "A4 SOLVER LIFECYCLE RECEIPTS" in result.stdout
    assert "real opt-in SolverProvenance sign/activate/revoke path" in result.stdout
    assert "not production auto-promotion authority" in result.stdout
    assert "A4 AUTOGROWTH LIFECYCLE RECEIPTS" in result.stdout
    assert "real opt-in AutogrowthScheduler queue->grower->engine path" in result.stdout
    assert "not long-running production auto-promotion authority" in result.stdout
    assert "A4 AUTOGROWTH SOAK FIXTURE RECEIPTS" in result.stdout
    assert "not release soak evidence" in result.stdout


def test_memory_palace_shortcut_section_reports_read_side_projection() -> None:
    result = _run("--json")
    payload = json.loads(result.stdout)
    palace = payload["memory_palace_shortcuts"]

    assert palace["available"] is True, palace
    assert palace["ok"] is True, palace
    assert palace["schema_version"] == "memory_palace_projection.v1"
    assert palace["source_of_truth"] == "projection_only"
    assert palace["node_count"] == 6
    assert palace["placement_count"] == 1
    assert palace["shortcut_hint_count"] >= 2
    assert palace["ranked_candidate_count"] >= 2
    assert palace["memory_id"] == "memory.learning.imaging.1"
    assert palace["top_candidate_target"] in {
        "room.research.pathology",
        "room.system.statistics",
    }
    assert palace["top_candidate_rank_score"] > 0
    assert palace["top_candidate_hierarchy_hops"] == 3
    assert "tags" in palace["top_candidate_matched_selector_keys"]
    bypass = palace["bypass_analysis"]
    assert bypass["hierarchy_hops"] == 3
    assert bypass["projected_shortcut_hops"] == 1
    assert bypass["intermediate_hops_skipped"] == 2
    assert bypass["intermediate_node_traversal_required"] is False
    assert bypass["intermediate_nodes_not_loaded"] is True
    assert bypass["shortcut_ranked_without_runtime_dispatch"] is True
    assert bypass["runtime_route_changed"] is False
    assert bypass["solver_call_performed"] is False
    assert palace["authority_flags_false"] is True
    assert "projection-only local fixture" in palace["evidence_scope"]
    assert "not router/solver dispatch" in palace["evidence_scope"]


def test_memory_palace_shortcut_text_keeps_no_authority_boundary() -> None:
    result = _run()

    assert result.returncode in {0, 1}, result.stderr
    assert "MEMORY PALACE SHORTCUTS" in result.stdout
    assert "read-side shortcut ranking" in result.stdout
    assert "source of truth                : projection_only" in result.stdout
    assert "projected shortcut hops        : 1" in result.stdout
    assert "bypass hops skipped            : 2" in result.stdout
    assert "intermediate nodes not loaded  : True" in result.stdout
    assert "authority flags false          : True" in result.stdout
    assert "not router/solver dispatch" in result.stdout


def test_memory_palace_promotion_candidates_section_reports_action_free_rows() -> None:
    result = _run("--json")
    payload = json.loads(result.stdout)
    candidates = payload["memory_palace_promotion_candidates"]

    assert candidates["available"] is True, candidates
    assert candidates["ok"] is True, candidates
    assert candidates["source_of_truth"] == "projection_only"
    assert candidates["memory_id"] == "memory.learning.cell_imaging.1"
    assert candidates["source_candidate_count"] == 2
    assert candidates["promotion_observable_count"] == 2
    assert candidates["blocked_count"] == 0
    assert candidates["top_candidate_target"] == "room.research.pathology"
    assert candidates["min_rank_score"] == 0.6
    assert candidates["min_intermediate_hops_skipped"] == 2
    assert candidates["promotion_action_allowed"] is False
    assert candidates["authority_boundary_ok"] is True
    assert candidates["operator_gate_required_for_runtime_promotion"] is True
    assert "read-only promotion-candidate report" in candidates["evidence_scope"]
    assert "not route promotion" in candidates["evidence_scope"]


def test_memory_palace_promotion_candidates_text_keeps_action_boundary() -> None:
    result = _run()

    assert result.returncode in {0, 1}, result.stderr
    assert "MEMORY PALACE PROMOTION CANDIDATES" in result.stdout
    assert "promotion-candidate report" in result.stdout
    assert "observable candidates          : 2" in result.stdout
    assert "top candidate                  : room.research.pathology" in result.stdout
    assert "promotion action allowed       : False" in result.stdout
    assert "authority boundary ok          : True" in result.stdout
    assert "operator gate for runtime      : True" in result.stdout


def test_governance_throughput_section_reports_status_counts() -> None:
    result = _run("--json")
    payload = json.loads(result.stdout)
    gov = payload["governance_throughput"]

    assert gov["available"] is True, gov
    assert gov["metric_count"] == 8
    assert isinstance(gov["event_count_in_window"], int)
    assert gov["event_count_in_window"] >= 0
    assert isinstance(gov["task_count_in_window"], int)
    assert gov["task_count_in_window"] >= 0
    assert isinstance(gov["status_counts"], dict)
    assert sum(gov["status_counts"].values()) == gov["metric_count"]


def test_substrate_velocity_returns_a_non_negative_count() -> None:
    result = _run("--json", "--since-utc-days", "1")
    payload = json.loads(result.stdout)
    velocity = payload["substrate_velocity"]

    assert velocity["available"] is True, velocity
    assert isinstance(velocity["merged_commits"], int)
    assert velocity["merged_commits"] >= 0
    assert isinstance(velocity["pr_numbers"], list)


def test_overall_ok_flag_is_true_on_current_main() -> None:
    result = _run("--json")
    payload = json.loads(result.stdout)

    assert payload["ok"] is True, payload
