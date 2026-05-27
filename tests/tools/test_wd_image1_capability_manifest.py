# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from tools.wd_image1_capability_manifest import build_manifest
from tools.wd_image1_capability_manifest import build_hexagonal_upgrade_proof
from tools.wd_image1_capability_manifest import build_hex_mesh_entry_proof
from tools.wd_image1_capability_manifest import build_hex_mesh_runtime_trace_smoke
from tools.wd_image1_capability_manifest import build_low_risk_autonomy_proof


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "wd_image1_capability_manifest.py"


def _by_id(report: dict) -> dict[str, dict]:
    return {
        capability["capability_id"]: capability
        for capability in report["capabilities"]
    }


def test_manifest_reports_six_image_capabilities() -> None:
    report = build_manifest(ROOT)
    capabilities = _by_id(report)

    assert report["schema_version"] == "wd_image1_capability_manifest.v1"
    assert set(capabilities) == {
        "hex_mesh_entry",
        "deterministic_solver_first",
        "magma_audit_log",
        "low_risk_autonomy_loop",
        "hexagonal_upgrades",
        "future_waggledance_swarm",
    }
    assert report["summary"]["capability_count"] == 6


def test_manifest_keeps_literal_image_overclaims_unsafe() -> None:
    report = build_manifest(ROOT)
    capabilities = _by_id(report)

    assert capabilities["hex_mesh_entry"]["status"] == "partial"
    assert capabilities["hex_mesh_entry"]["claim_safe"] is False
    assert "two independent" in capabilities["hex_mesh_entry"]["safe_statement"]
    assert capabilities["hex_mesh_entry"]["proof"]["literal_claim_safe"] is False

    assert capabilities["magma_audit_log"]["status"] == "partial"
    assert capabilities["magma_audit_log"]["claim_safe"] is False
    assert "hard append-only" in capabilities["magma_audit_log"]["safe_statement"]

    future = capabilities["future_waggledance_swarm"]
    assert future["status"] == "planned"
    assert future["claim_safe"] is False
    assert "unlimited scalability" in future["safe_statement"]


def test_manifest_evidence_paths_are_present_for_current_repo() -> None:
    report = build_manifest(ROOT)

    for capability in report["capabilities"]:
        assert capability["evidence"], capability["capability_id"]
        assert any(item["present"] for item in capability["evidence"])


def test_hex_mesh_entry_proof_reports_current_route_order_and_flags() -> None:
    proof = build_hex_mesh_entry_proof(ROOT)

    assert proof["ok"] is True
    assert proof["proof_id"] == "hex_mesh_entry_route_order_v1"
    assert proof["proves_every_query_first_enters_mesh"] is False
    assert proof["literal_claim_safe"] is False
    assert proof["current_config"] == {
        "hybrid_retrieval_enabled": True,
        "hybrid_retrieval_mode": "candidate",
        "hybrid_retrieval_authoritative": False,
        "hex_mesh_enabled": False,
        "hex_mesh_cell_config_path": "configs/hex_cells.yaml",
    }
    assert proof["topologies"]["solver_retrieval"]["cell_count"] == 8
    assert proof["topologies"]["agent_routing"]["cell_count"] == 7
    assert proof["chat_route_order"] == [
        "language_detection",
        "hot_cache",
        "memory_context",
        "route_selection",
        "deterministic_solver",
        "hybrid_retrieval_8_cell",
        "hex_neighbor_assist_7_cell",
        "orchestrator_llm_fallback",
    ]
    assert proof["pre_hex_steps"] == [
        "language_detection",
        "hot_cache",
        "memory_context",
        "route_selection",
        "deterministic_solver",
    ]
    assert [item["cell_id"] for item in proof["solver_retrieval_samples"]] == [
        "thermal",
        "math",
        "energy",
    ]
    assert [item["cell_id"] for item in proof["agent_routing_samples"]] == [
        "bee_ops",
        "safety_security",
        "home_comfort",
    ]
    assert proof["runtime_trace_smoke"]["ok"] is True
    assert proof["runtime_trace_smoke"]["disabled_static_stages"] == [
        "hex_neighbor_assist_7_cell",
    ]
    assert "do not literally enter a hex mesh first" in proof["safe_conclusion"]


def test_hex_mesh_runtime_trace_smoke_matches_live_chatservice_order() -> None:
    smoke = build_hex_mesh_runtime_trace_smoke(ROOT)

    assert smoke["ok"] is True
    assert smoke["static_route_order"] == [
        "language_detection",
        "hot_cache",
        "memory_context",
        "route_selection",
        "deterministic_solver",
        "hybrid_retrieval_8_cell",
        "hex_neighbor_assist_7_cell",
        "orchestrator_llm_fallback",
    ]
    assert smoke["expected_live_route_order"] == [
        "language_detection",
        "hot_cache",
        "memory_context",
        "route_selection",
        "deterministic_solver",
        "hybrid_retrieval_8_cell",
        "orchestrator_llm_fallback",
    ]
    assert smoke["observed_route_order"] == smoke["expected_live_route_order"]
    assert smoke["trace_source"] == "ChatResult.route_stage_trace"
    assert smoke["test_only_instrumentation"] is False
    assert smoke["pre_hex_stages_observed_before_optional_hex"] is True
    assert smoke["disabled_static_stages"] == ["hex_neighbor_assist_7_cell"]
    assert smoke["extra_observed_stages"] == []
    assert smoke["live_result"] == {
        "source": "llm",
        "confidence": 0.8,
        "cached": False,
        "hybrid_trace_present": True,
        "round_table": False,
    }
    assert smoke["no_runtime_mutation"] is True
    assert smoke["external_writes_applied"] is False


def test_bad_root_manifest_fails_closed_without_file_errors(tmp_path: Path) -> None:
    report = build_manifest(tmp_path)
    capabilities = _by_id(report)
    hex_proof = capabilities["hex_mesh_entry"]["proof"]

    assert all(
        capability["status"] == "blocked"
        for capability in report["capabilities"]
    )
    assert all(
        capability["claim_safe"] is False
        for capability in report["capabilities"]
    )
    assert hex_proof["ok"] is False
    assert hex_proof["blocked_reason"] == "missing_required_inputs"
    assert hex_proof["missing_inputs"] == ["configs/settings.yaml"]
    assert report["summary"]["all_literal_claims_safe"] is False
    assert report["summary"]["proofs_ok"] is False


def test_hexagonal_upgrade_proof_is_pure_and_delivers_messages() -> None:
    proof = build_hexagonal_upgrade_proof()

    assert proof["ok"] is True
    assert proof["no_runtime_mutation"] is True
    assert proof["plan"]["target_state"] == "subdivision_in_shadow"
    assert proof["relations"]["thermal_children"] == [
        "thermal.cooling",
        "thermal.heating",
    ]
    assert proof["relations"]["heating_siblings"] == ["thermal.cooling"]
    assert proof["relations"]["heating_ancestors"] == ["thermal"]
    assert [item["delivered"] for item in proof["deliveries"]] == [
        True,
        True,
        True,
    ]


def test_low_risk_autonomy_proof_flows_gap_to_scheduler_outcome() -> None:
    proof = build_low_risk_autonomy_proof()

    assert proof["ok"] is True
    assert proof["family_kind"] == "scalar_unit_conversion"
    assert proof["family_low_risk"] is True
    assert proof["external_writes_applied"] is False
    assert proof["operator_gate_required"] is False
    assert proof["runtime_authority_changed"] is False
    assert proof["temporary_control_plane_db"] is True
    assert proof["temp_db_removed"] is True
    assert proof["route_before"]["served"] is False
    assert proof["route_before"]["source"] == "gap_emitted"
    assert proof["recorded_signal"]["count"] == 1
    assert proof["digest"]["intents_created"] == 1
    assert proof["digest"]["intents_enqueued"] == 1
    assert proof["queued_before_tick"][0]["status"] == "queued"
    assert proof["scheduler_tick"]["claimed"] is True
    assert proof["scheduler_tick"]["outcome"] == "auto_promoted"
    assert proof["intent_after_tick"]["status"] == "fulfilled"
    assert proof["queue_after_tick"][0]["status"] == "completed"
    assert proof["route_after"]["served"] is True
    assert proof["route_after"]["source"] == "auto_promoted_solver"
    assert proof["route_after"]["output"] == 298.15
    assert proof["run_outcomes"] == ["auto_promoted"]
    assert proof["growth_event_counts"] == {
        "signal_recorded": 1,
        "intent_created": 1,
        "intent_enqueued": 1,
        "solver_auto_promoted": 1,
    }


def test_manifest_embeds_hexagonal_upgrade_proof_without_upgrading_claim() -> None:
    report = build_manifest(ROOT)
    capability = _by_id(report)["hexagonal_upgrades"]

    assert capability["status"] == "partial"
    assert capability["claim_safe"] is False
    assert capability["proof"]["ok"] is True
    assert report["summary"]["proofs_ok"] is True


def test_manifest_embeds_low_risk_autonomy_proof_without_upgrading_claim() -> None:
    report = build_manifest(ROOT)
    capability = _by_id(report)["low_risk_autonomy_loop"]

    assert capability["status"] == "partial"
    assert capability["claim_safe"] is False
    assert capability["proof"]["ok"] is True
    assert capability["proof"]["route_before"]["source"] == "gap_emitted"
    assert capability["proof"]["scheduler_tick"]["outcome"] == "auto_promoted"
    assert capability["proof"]["route_after"]["source"] == "auto_promoted_solver"
    assert report["summary"]["proofs_ok"] is True


def test_manifest_embeds_hex_entry_proof_without_upgrading_claim() -> None:
    report = build_manifest(ROOT)
    capability = _by_id(report)["hex_mesh_entry"]

    assert capability["status"] == "partial"
    assert capability["claim_safe"] is False
    assert capability["proof"]["ok"] is True
    assert capability["proof"]["literal_claim_safe"] is False
    assert capability["proof"]["topologies"]["solver_retrieval"]["cell_count"] == 8
    assert capability["proof"]["topologies"]["agent_routing"]["cell_count"] == 7
    assert report["summary"]["proofs_ok"] is True


def test_cli_emits_json_and_strict_claims_fails_on_unsafe_claims() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(proc.stdout)
    assert payload["summary"]["all_literal_claims_safe"] is False

    strict = subprocess.run(
        [sys.executable, str(SCRIPT), "--strict-claims"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert strict.returncode == 2
