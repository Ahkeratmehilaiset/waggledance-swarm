# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools.hex_shadow_subdivision_replay import (
    build_shadow_subdivision_replay_artifact,
)
from tools.wd_image1_capability_manifest import build_manifest
from tools.wd_image1_capability_manifest import (
    PER_QUERY_RECEIPT_COVERAGE_ENV,
    _PER_QUERY_RECEIPT_COVERAGE_SAFE_KEYS,
    _safe_per_query_receipt_coverage_aggregate,
    build_per_query_receipt_coverage_aggregate,
)
from tools.wd_image1_capability_manifest import (
    FIRST_HOP_COVERAGE_ENV,
    _FIRST_HOP_COVERAGE_SAFE_KEYS,
    _safe_first_hop_coverage_aggregate,
    build_first_hop_coverage_aggregate,
)
from tools.wd_image1_capability_manifest import (
    REPEAT_WINDOW_TREND_ENV,
    _REPEAT_WINDOW_TREND_SAFE_KEYS,
    _safe_repeat_window_trend_aggregate,
    build_repeat_window_trend_aggregate,
)
from tools.wd_image1_capability_manifest import (
    _REAL_LOOP_MANIFEST_CONTRIBUTION_SAFE_KEYS,
    _safe_real_loop_manifest_contribution,
    build_real_loop_manifest_contribution,
)
from tools.wd_image1_capability_manifest import build_deterministic_solver_trace_proof
from tools.wd_image1_capability_manifest import build_future_scale_axis_scorecard
from tools.wd_image1_capability_manifest import build_hexagonal_upgrade_proof
from tools.wd_image1_capability_manifest import (
    build_hexagonal_upgrade_runtime_smoke,
)
from tools.wd_image1_capability_manifest import build_hex_mesh_entry_proof
from tools.wd_image1_capability_manifest import (
    build_hex_mesh_route_stage_operator_metrics_smoke,
)
from tools.wd_image1_capability_manifest import (
    build_hex_mesh_route_stage_runtime_metrics_smoke,
)
from tools.wd_image1_capability_manifest import build_hex_mesh_route_stage_ui_smoke
from tools.wd_image1_capability_manifest import build_hex_mesh_runtime_trace_smoke
from tools.wd_image1_capability_manifest import (
    build_low_risk_autogrowth_runtime_boundary_smoke,
)
from tools.wd_image1_capability_manifest import (
    build_low_risk_autogrowth_operator_metrics_smoke,
)
from tools.wd_image1_capability_manifest import (
    build_low_risk_autogrowth_alert_runbook_smoke,
)
from tools.wd_image1_capability_manifest import (
    build_magma_handoff_provider_metrics_runbook_smoke,
)
from tools.wd_image1_capability_manifest import (
    build_magma_handoff_metrics_alert_state_smoke,
)
from tools.wd_image1_capability_manifest import (
    build_magma_handoff_metrics_alertmanager_adapter_smoke,
)
from tools.wd_image1_capability_manifest import (
    build_low_risk_autogrowth_ops_alert_state_smoke,
)
from tools.wd_image1_capability_manifest import _build_future_scale_runtime_evidence
from tools.wd_image1_capability_manifest import build_low_risk_autonomy_proof
from tools.wd_image1_capability_manifest import build_solver_trace_magma_receipt_proof
from tools.build_wd_vision_progress_counters import build_vision_progress_counters

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "wd_image1_capability_manifest.py"


def _by_id(report: dict) -> dict[str, dict]:
    return {
        capability["capability_id"]: capability for capability in report["capabilities"]
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
    assert future["status"] == "partial"
    assert future["claim_safe"] is False
    assert "unlimited scalability" in future["safe_statement"]
    assert future["proof"]["literal_future_claim_safe"] is False


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
    assert proof["route_stage_ui_smoke"]["ok"] is True
    assert (
        proof["route_stage_ui_smoke"]["checks"]["dashboard_stage_container_present"]
        is True
    )
    assert proof["route_stage_operator_metrics_smoke"]["ok"] is True
    assert (
        proof["route_stage_operator_metrics_smoke"]["operator_visible_metrics"] is True
    )
    assert proof["route_stage_runtime_metrics_smoke"]["ok"] is True
    assert (
        proof["route_stage_runtime_metrics_smoke"]["operator_visible_metrics"] is True
    )
    assert "do not literally enter a hex mesh first" in proof["safe_conclusion"]


def test_hex_mesh_route_stage_ui_smoke_reports_dashboard_contract() -> None:
    smoke = build_hex_mesh_route_stage_ui_smoke(ROOT)

    assert smoke["ok"] is True
    assert smoke["proof_id"] == "hex_mesh_route_stage_ui_smoke_v1"
    assert smoke["expected_route_stages"] == [
        "language_detection",
        "hot_cache",
        "memory_context",
        "route_selection",
        "deterministic_solver",
        "hybrid_retrieval_8_cell",
        "hex_neighbor_assist_7_cell",
        "orchestrator_llm_fallback",
    ]
    assert all(smoke["checks"].values())
    assert smoke["ws_event_contract"]["ok"] is True
    assert smoke["ws_event_contract"]["forbidden_raw_markers_absent"] is True
    assert "query" not in smoke["ws_event_contract"]["data_keys"]
    assert "language" not in smoke["ws_event_contract"]["data_keys"]
    assert "profile" not in smoke["ws_event_contract"]["data_keys"]
    assert (
        "hex_neighbor_assist_7_cell"
        in smoke["ws_event_contract"]["disabled_route_stages"]
    )
    assert smoke["observed_ui_stage_names"] == smoke["expected_route_stages"]
    assert smoke["no_runtime_mutation"] is True
    assert smoke["external_writes_applied"] is False


def test_hex_mesh_route_stage_operator_metrics_smoke_reports_counts() -> None:
    smoke = build_hex_mesh_route_stage_operator_metrics_smoke(ROOT)

    assert smoke["ok"] is True
    assert smoke["proof_id"] == "hex_mesh_route_stage_operator_metrics_smoke_v1"
    assert smoke["metric_name"] == "waggledance_route_stage_count"
    assert smoke["metric_groups"] == [
        "expected",
        "enabled",
        "pre_hex",
        "hex_backed",
        "optional",
        "disabled_optional",
    ]
    assert all(smoke["checks"].values())
    assert smoke["runtime_contract"]["ok"] is True
    assert smoke["runtime_contract"]["missing_lines"] == []
    assert smoke["runtime_contract"]["forbidden_payload_markers_absent"] is True
    assert smoke["operator_visible_metrics"] is True
    assert smoke["runtime_routing_changed"] is False
    assert smoke["disabled_hex_paths_enabled"] is False
    assert smoke["no_runtime_mutation"] is True
    assert smoke["external_writes_applied"] is False


def test_hex_mesh_route_stage_runtime_metrics_smoke_reports_counters() -> None:
    smoke = build_hex_mesh_route_stage_runtime_metrics_smoke(ROOT)

    assert smoke["ok"] is True
    assert smoke["proof_id"] == "hex_mesh_route_stage_runtime_metrics_smoke_v1"
    assert smoke["metric_names"] == [
        "waggledance_route_stage_observations_total",
        "waggledance_route_stage_request_latency_ms_total",
        "waggledance_route_stage_request_latency_histogram_ms",
    ]
    assert all(smoke["checks"].values())
    assert smoke["runtime_contract"]["ok"] is True
    assert smoke["runtime_contract"]["missing_lines"] == []
    assert smoke["runtime_contract"]["forbidden_payload_markers_absent"] is True
    assert smoke["operator_visible_metrics"] is True
    assert smoke["rate_query_supported"] is True
    assert smoke["histogram_quantile_supported"] is True
    assert smoke["latency_panel_templates_visible"] is True
    assert smoke["prometheus_alertmanager_feed_supported"] is True
    assert smoke["prometheus_alertmanager_feed_provider_configured"] is True
    assert (
        smoke[
            "latency_feed_drill_evidence_verification_summary_bridge_event_template_index_entry_supported"
        ]
        is True
    )
    assert (
        smoke["latency_feed_drill_evidence_reviewer_handoff_summary_supported"]
        is True
    )
    assert (
        smoke[
            "latency_feed_drill_evidence_reviewer_handoff_bundle_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_supported"
        ]
        is True
    )
    assert (
        smoke[
            "latency_feed_drill_evidence_reviewer_handoff_bundle_verifier_summary_bridge_event_template_index_entry_verifier_supported"
        ]
        is True
    )
    assert smoke["latency_feed_state_visible"] is True
    assert smoke["alert_thresholds_documented"] is True
    assert smoke["runbook_path"] == ("docs/operations/ROUTE_STAGE_LATENCY_RUNBOOK.md")
    assert smoke["latency_metric_semantics"] == ("stage_correlated_request_latency")
    assert smoke["raw_payload_recorded"] is False
    assert smoke["runtime_routing_changed"] is False
    assert smoke["disabled_hex_paths_enabled"] is False
    assert smoke["no_runtime_mutation"] is True
    assert smoke["external_writes_applied"] is False


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


def test_deterministic_solver_trace_proof_is_privacy_safe() -> None:
    proof = build_deterministic_solver_trace_proof(ROOT)

    assert proof["ok"] is True
    assert proof["proof_id"] == "deterministic_solver_trace_v1"
    assert proof["router_entrypoint"] == (
        "waggledance.core.reasoning.solver_router.SolverRouter.route"
    )
    assert proof["quality_path"] == "gold"
    assert proof["fallback_used"] is False
    assert proof["selected_solver_ids"] == ["solve.math"]
    assert proof["trace"] == [
        {
            "stage": "solver_call",
            "status": "selected",
            "intent": "math",
            "capability_id": "solve.math",
            "selected_index": 0,
            "quality_path": "gold",
            "execution_boundary": "safe_action_bus",
        }
    ]
    assert proof["query_text_recorded"] is False
    assert proof["magma_execution_receipt_claimed"] is True
    assert proof["magma_execution_receipt_scope"] == (
        "opt_in_handle_query_runtime_summary"
    )
    assert proof["magma_execution_receipt_proof"]["ok"] is True
    assert (
        proof["magma_execution_receipt_proof"]["solver_call_trace_receipt_bound"]
        is True
    )
    assert (
        proof["magma_execution_receipt_proof"]["solver_call_trace_privacy_safe"] is True
    )
    assert proof["receipt_metrics"] == {
        "receipt_count": 1,
        "solver_call_trace_count": 1,
        "solver_call_trace_receipt_bound": True,
        "prometheus_metric_names": [
            "waggledance_runtime_receipt_metrics_up",
            "waggledance_runtime_receipt_sink_configured",
            "waggledance_runtime_receipt_sink_calls_total",
            "waggledance_runtime_receipt_sink_successes_total",
            "waggledance_runtime_receipt_sink_failures_total",
            "waggledance_runtime_receipt_bundle_receipts_total",
            "waggledance_runtime_receipt_solver_call_trace_entries_total",
        ],
    }
    assert proof["external_writes_applied"] is False


def test_deterministic_solver_trace_proof_blocks_foreign_root(
    tmp_path: Path,
) -> None:
    for rel_path in (
        "waggledance/core/reasoning/solver_router.py",
        "waggledance/core/capabilities/selector.py",
        "waggledance/core/capabilities/registry.py",
    ):
        path = tmp_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# placeholder\n", encoding="utf-8")

    proof = build_deterministic_solver_trace_proof(tmp_path)

    assert proof["ok"] is False
    assert proof["blocked_reason"] == "non_current_import_root"
    assert proof["missing_inputs"] == []
    assert proof["selected_solver_ids"] == []
    assert proof["trace"] == []
    assert proof["magma_execution_receipt_claimed"] is False


def test_solver_trace_magma_receipt_proof_binds_trace_without_raw_payload() -> None:
    proof = build_solver_trace_magma_receipt_proof(ROOT)

    assert proof["ok"] is True
    assert proof["proof_id"] == "solver_trace_runtime_receipt_v1"
    assert proof["receipt_scope"] == "opt_in_handle_query_runtime_summary"
    assert proof["receipt_count"] == 1
    assert proof["verifier_ok"] is True
    assert proof["solver_call_trace_count"] == 1
    assert proof["solver_call_trace_digest_bound"] is True
    assert proof["solver_call_trace_receipt_bound"] is True
    assert proof["solver_call_trace_privacy_safe"] is True
    assert proof["raw_payload_leak_check"] is True
    assert proof["temp_artifacts_removed"] is True
    assert proof["default_sink_required"] is False
    assert proof["operator_gate_required"] is False


def test_bad_root_manifest_fails_closed_without_file_errors(tmp_path: Path) -> None:
    report = build_manifest(tmp_path)
    capabilities = _by_id(report)
    hex_proof = capabilities["hex_mesh_entry"]["proof"]

    assert all(
        capability["status"] == "blocked" for capability in report["capabilities"]
    )
    assert all(
        capability["claim_safe"] is False for capability in report["capabilities"]
    )
    assert hex_proof["ok"] is False
    assert hex_proof["blocked_reason"] == "missing_required_inputs"
    assert hex_proof["missing_inputs"] == ["configs/settings.yaml"]
    assert report["summary"]["all_literal_claims_safe"] is False
    assert report["summary"]["proofs_ok"] is False


def test_hexagonal_upgrade_proof_is_pure_and_delivers_messages() -> None:
    proof = build_hexagonal_upgrade_proof(ROOT)

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


def test_hexagonal_upgrade_proof_blocks_foreign_root(tmp_path: Path) -> None:
    for rel_path in (
        "waggledance/core/hex_topology/subdivision_operator.py",
        "waggledance/core/hex_topology/ring_messaging.py",
        "waggledance/core/hex_topology/parent_child_relations.py",
    ):
        path = tmp_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# placeholder\n", encoding="utf-8")

    proof = build_hexagonal_upgrade_proof(tmp_path)

    assert proof["ok"] is False
    assert proof["blocked_reason"] == "non_current_import_root"
    assert proof["missing_inputs"] == []
    assert proof["plan"] is None
    assert proof["relations"] == {}
    assert proof["deliveries"] == []


def test_hexagonal_upgrade_runtime_smoke_reports_active_topology_boundary() -> None:
    proof = build_hexagonal_upgrade_runtime_smoke(ROOT)

    assert proof["ok"] is True
    assert proof["proof_id"] == "hexagonal_upgrades_runtime_boundary_smoke_v1"
    assert proof["runtime_wiring_present"] is True
    assert proof["container_registry_present"] is True
    assert proof["container_hex_neighbor_assist_wiring_present"] is True
    assert proof["current_config"] == {
        "hex_mesh_enabled": False,
        "hex_mesh_cell_config_path": "configs/hex_cells.yaml",
    }
    assert proof["active_runtime_dispatch_enabled"] is False
    assert proof["runtime_topology"]["cell_count"] == 7
    assert proof["runtime_topology"]["enabled_cell_count"] == 7
    assert sorted(proof["runtime_topology"]["neighbor_map"]["hub"]) == [
        "bee_ops",
        "environment",
        "home_comfort",
        "logistics",
        "production",
        "safety_security",
    ]
    assert all(
        item["matched_expected"] for item in proof["runtime_topology"]["sample_origins"]
    )
    metrics = proof["operator_metrics_smoke"]
    assert metrics["ok"] is True
    assert metrics["operator_visible_metrics"] is True
    assert metrics["runtime_contract"]["ok"] is True
    assert "waggledance_hex_topology_cells" in metrics["metric_names"]
    assert (
        "waggledance_hex_topology_runtime_mutation_authority" in metrics["metric_names"]
    )
    assert metrics["no_runtime_topology_mutation"] is True
    assert metrics["runtime_authority_changed"] is False
    assert proof["shadow_child_cell_ids_absent_from_runtime_config"] is True
    assert proof["no_runtime_topology_mutation"] is True
    assert proof["runtime_authority_changed"] is False
    assert proof["operator_gate_required"] is False
    assert proof["external_writes_applied"] is False


def test_hexagonal_shadow_replay_binds_pure_plan_to_metric_contract() -> None:
    upgrade_proof = build_hexagonal_upgrade_proof(ROOT)
    runtime_smoke = build_hexagonal_upgrade_runtime_smoke(ROOT)

    replay = build_shadow_subdivision_replay_artifact(
        upgrade_proof=upgrade_proof,
        runtime_boundary_smoke=runtime_smoke,
    )

    assert replay["ok"] is True
    assert replay["proof_id"] == "hex_shadow_subdivision_replay_v1"
    assert replay["proof_type"] == "shadow_replay_hypothetical"
    assert replay["binding_scope"] == "structural_metrics_contract_only"
    assert replay["shadow_plan_summary"]["plan_id"] == (
        upgrade_proof["plan"]["plan_id"]
    )
    assert replay["shadow_plan_summary"]["target_state"] == ("subdivision_in_shadow")
    assert replay["delivery_summary"] == {
        "message_count": 3,
        "delivered_count": 3,
        "blocked_count": 0,
        "message_kinds": [
            "child_to_parent",
            "parent_to_child",
            "ring_request",
        ],
    }
    assert replay["runtime_topology_summary"] == {
        "cell_count": 7,
        "enabled_cell_count": 7,
        "dispatch_enabled": False,
        "shadow_child_cell_ids_absent_from_runtime_config": True,
    }
    assert (
        "waggledance_hex_topology_runtime_mutation_authority"
        in replay["metric_contract_summary"]["metric_names"]
    )
    assert (
        "waggledance_hex_topology_cells"
        in replay["metric_contract_summary"]["metric_names"]
    )
    assert replay["guardrails"]["no_runtime_topology_mutation"] is True
    assert replay["guardrails"]["runtime_authority_changed"] is False
    assert replay["guardrails"]["dispatch_controls_added"] is False
    assert replay["guardrails"]["network_transport_added"] is False
    assert replay["guardrails"]["raw_query_or_payload_included"] is False
    assert replay["guardrails"]["runtime_config_contents_included"] is False
    assert replay["guardrails"]["numeric_equality_to_shadow_children_claimed"] is False
    assert replay["artifact_digest"].startswith("sha256:")
    assert all(digest.startswith("sha256:") for digest in replay["digests"].values())
    serialized = json.dumps(replay, sort_keys=True)
    assert "bee hive" not in serialized
    assert "energy hvac" not in serialized
    assert "neighbor proof" not in serialized
    assert "hierarchy proof" not in serialized


def test_hex_shadow_replay_tool_cli_emits_path_free_json() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "hex_shadow_subdivision_replay.py"),
            "--root",
            str(ROOT),
            "--json",
            "--strict",
        ],
        check=False,
        capture_output=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    replay = json.loads(result.stdout)
    assert replay["ok"] is True
    assert replay["source_snapshot"]["git_commit_available"] is True
    assert replay["digests"]["full_binding"].startswith("sha256:")
    assert str(ROOT) not in result.stdout
    assert "bee hive" not in result.stdout
    assert "hierarchy proof" not in result.stdout


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


def test_low_risk_autogrowth_runtime_boundary_smoke_reports_runtime_wiring() -> None:
    proof = build_low_risk_autogrowth_runtime_boundary_smoke(ROOT)

    assert proof["ok"] is True
    assert proof["proof_id"] == "low_risk_autogrowth_runtime_boundary_smoke_v1"
    assert proof["runtime_wiring_present"] is True
    assert proof["container_ticker_present"] is True
    assert proof["lifespan_start_stop_present"] is True
    assert proof["default_interval_seconds"] == 30.0
    assert proof["default_max_ticks_per_wake"] == 20
    assert proof["is_running_before_start"] is False
    assert proof["temporary_control_plane_db"] is True
    assert proof["control_plane_schema_version_present"] is True
    assert proof["temp_artifacts_removed"] is True
    assert proof["production_control_plane_mutated"] is False
    assert proof["runtime_authority_changed"] is False
    assert proof["operator_gate_required"] is False
    assert proof["external_writes_applied"] is False


def test_low_risk_autogrowth_operator_metrics_smoke_reports_prometheus_contract() -> (
    None
):
    proof = build_low_risk_autogrowth_operator_metrics_smoke(ROOT)

    assert proof["ok"] is True
    assert proof["proof_id"] == "low_risk_autogrowth_operator_metrics_smoke_v1"
    assert proof["metrics_endpoint"] == "/metrics"
    assert proof["prometheus_namespace"] == "waggledance_autogrowth"
    assert proof["operator_visible_metrics"] is True
    assert proof["missing_metrics"] == []
    assert proof["double_suffix_absent"] is True
    assert "waggledance_autogrowth_wakeups_total" in proof["metric_names"]
    assert "waggledance_autogrowth_background_interval_seconds" in (
        proof["metric_names"]
    )
    assert proof["runtime_authority_changed"] is False
    assert proof["operator_gate_required"] is False
    assert proof["external_writes_applied"] is False


def test_low_risk_autogrowth_operator_metrics_smoke_blocks_foreign_root(
    tmp_path: Path,
) -> None:
    for rel_path in (
        "waggledance/adapters/http/routes/metrics.py",
        "waggledance/core/autonomy_growth/autogrowth_scheduler.py",
        "tests/test_metrics_endpoint.py",
        "docs/API.md",
    ):
        path = tmp_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# placeholder\n", encoding="utf-8")

    proof = build_low_risk_autogrowth_operator_metrics_smoke(tmp_path)

    assert proof["ok"] is False
    assert proof["blocked_reason"] == "non_current_import_root"
    assert proof["missing_inputs"] == []
    assert proof["operator_visible_metrics"] is False
    assert proof["metric_names"] == []


def test_low_risk_autogrowth_alert_runbook_smoke_reports_threshold_contract() -> None:
    proof = build_low_risk_autogrowth_alert_runbook_smoke(ROOT)

    assert proof["ok"] is True
    assert proof["proof_id"] == "low_risk_autogrowth_alert_runbook_smoke_v1"
    assert proof["runbook_path"] == ("docs/operations/LOW_RISK_AUTOGROWTH_RUNBOOK.md")
    assert proof["api_docs_path"] == "docs/API.md"
    assert proof["alert_thresholds_documented"] is True
    assert proof["missing_metric_mentions"] == []
    assert proof["missing_threshold_rules"] == []
    assert proof["api_docs_link_runbook"] is True
    assert proof["forbidden_controls_absent"] is True
    assert proof["forbidden_control_tokens_found"] == []
    assert "waggledance_autogrowth_errors_total" in proof["metric_names"]
    assert "waggledance_autogrowth_wakeups_total" in proof["metric_names"]
    assert proof["runtime_authority_changed"] is False
    assert proof["operator_gate_required"] is False
    assert proof["external_writes_applied"] is False


def test_low_risk_autogrowth_alert_runbook_smoke_blocks_missing_inputs(
    tmp_path: Path,
) -> None:
    proof = build_low_risk_autogrowth_alert_runbook_smoke(tmp_path)

    assert proof["ok"] is False
    assert proof["blocked_reason"] == "missing_required_inputs"
    assert "docs/operations/LOW_RISK_AUTOGROWTH_RUNBOOK.md" in (proof["missing_inputs"])
    assert "docs/API.md" in proof["missing_inputs"]
    assert proof["runtime_authority_changed"] is False


def test_magma_handoff_provider_metrics_runbook_smoke_reports_threshold_contract() -> (
    None
):
    proof = build_magma_handoff_provider_metrics_runbook_smoke(ROOT)

    assert proof["ok"] is True
    assert proof["proof_id"] == ("magma_handoff_provider_metrics_runbook_smoke_v1")
    assert proof["runbook_path"] == (
        "docs/operations/MAGMA_HANDOFF_PROVIDER_METRICS_RUNBOOK.md"
    )
    assert proof["api_docs_path"] == "docs/API.md"
    assert proof["metrics_endpoint"] == "/metrics"
    assert proof["alert_thresholds_documented"] is True
    assert proof["missing_metric_mentions"] == []
    assert proof["missing_threshold_rules"] == []
    assert proof["api_docs_link_runbook"] is True
    assert proof["source_metrics_contract_present"] is True
    assert proof["forbidden_controls_absent"] is True
    assert proof["forbidden_control_tokens_found"] == []
    assert "waggledance_magma_handoff_provider_up" in proof["metric_names"]
    assert "waggledance_magma_handoff_provider_alert_active" in (proof["metric_names"])
    assert proof["runtime_authority_changed"] is False
    assert proof["operator_gate_required"] is False
    assert proof["external_writes_applied"] is False


def test_magma_handoff_provider_metrics_runbook_smoke_blocks_missing_inputs(
    tmp_path: Path,
) -> None:
    proof = build_magma_handoff_provider_metrics_runbook_smoke(tmp_path)

    assert proof["ok"] is False
    assert proof["blocked_reason"] == "missing_required_inputs"
    assert "docs/operations/MAGMA_HANDOFF_PROVIDER_METRICS_RUNBOOK.md" in (
        proof["missing_inputs"]
    )
    assert "docs/API.md" in proof["missing_inputs"]
    assert proof["runtime_authority_changed"] is False


def test_magma_handoff_metrics_alert_state_smoke_reports_dashboard_contract() -> None:
    proof = build_magma_handoff_metrics_alert_state_smoke(ROOT)

    assert proof["ok"] is True
    assert proof["proof_id"] == "magma_handoff_metrics_alert_state_smoke_v1"
    assert proof["ops_endpoint"] == "/api/ops"
    assert proof["dashboard_path"] == "web/hologram-brain-v6.html"
    assert proof["api_contract_present"] is True
    assert proof["ui_contract_present"] is True
    assert proof["test_contract_present"] is True
    assert proof["docs_contract_present"] is True
    assert proof["runbook_contract_present"] is True
    assert proof["fixed_alert_ids_enforced"] is True
    assert proof["alert_state_visible"] is True
    assert proof["forbidden_controls_absent"] is True
    assert proof["forbidden_control_tokens_found"] == []
    assert "MagmaHandoffRuntimeAuthorityReported" in proof["alert_ids"]
    assert proof["runtime_authority_changed"] is False
    assert proof["operator_gate_required"] is False
    assert proof["external_writes_applied"] is False


def test_magma_handoff_metrics_alert_state_smoke_blocks_missing_inputs(
    tmp_path: Path,
) -> None:
    proof = build_magma_handoff_metrics_alert_state_smoke(tmp_path)

    assert proof["ok"] is False
    assert proof["blocked_reason"] == "missing_required_inputs"
    assert "waggledance/adapters/http/routes/compat_dashboard.py" in (
        proof["missing_inputs"]
    )
    assert "web/hologram-brain-v6.html" in proof["missing_inputs"]
    assert "docs/API.md" in proof["missing_inputs"]
    assert proof["runtime_authority_changed"] is False


def test_magma_handoff_metrics_alertmanager_adapter_smoke_reports_contract() -> None:
    proof = build_magma_handoff_metrics_alertmanager_adapter_smoke(ROOT)

    assert proof["ok"] is True
    assert proof["proof_id"] == ("magma_handoff_metrics_alertmanager_adapter_smoke_v1")
    assert proof["adapter_path"] == (
        "waggledance/adapters/http/magma_handoff_metrics_alert_feed.py"
    )
    assert proof["settings_path"] == "configs/settings.yaml"
    assert proof["ops_endpoint"] == "/api/ops"
    assert proof["adapter_contract_present"] is True
    assert proof["container_contract_present"] is True
    assert proof["settings_contract_present"] is True
    assert proof["test_contract_present"] is True
    assert proof["docs_contract_present"] is True
    assert proof["cache_backoff_contract_present"] is True
    assert proof["slo_drill_contract_present"] is True
    assert proof["release_gate_examples_present"] is True
    assert proof["release_evidence_package_contract_present"] is True
    assert proof["release_evidence_validator_contract_present"] is True
    assert proof["reviewer_handoff_summary_contract_present"] is True
    assert proof["reviewer_bridge_event_template_contract_present"] is True
    assert (
        proof["reviewer_bridge_event_template_decision_reference_slot_present"] is True
    )
    assert proof["reviewer_handoff_bundle_index_contract_present"] is True
    assert proof["reviewer_handoff_bundle_verifier_contract_present"] is True
    assert (
        proof["reviewer_handoff_bundle_verification_summary_contract_present"] is True
    )
    assert (
        proof[
            "reviewer_handoff_bundle_operator_decision_reference_validator_contract_present"
        ]
        is True
    )
    assert (
        proof[
            "reviewer_handoff_bundle_operator_decision_reference_review_summary_contract_present"
        ]
        is True
    )
    assert (
        proof[
            "reviewer_handoff_bundle_operator_decision_reference_review_bundle_index_contract_present"
        ]
        is True
    )
    assert (
        proof[
            "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verifier_contract_present"
        ]
        is True
    )
    assert (
        proof[
            "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_summary_contract_present"
        ]
        is True
    )
    assert (
        proof[
            "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_contract_present"
        ]
        is True
    )
    assert (
        proof[
            "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_contract_present"
        ]
        is True
    )
    assert (
        proof[
            "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verifier_contract_present"
        ]
        is True
    )
    assert (
        proof[
            "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_contract_present"
        ]
        is True
    )
    assert (
        proof[
            "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_contract_present"
        ]
        is True
    )
    assert (
        proof[
            "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_contract_present"
        ]
        is True
    )
    assert (
        proof[
            "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verifier_contract_present"
        ]
        is True
    )
    assert proof["guardrails_present"] is True
    assert proof["forbidden_controls_absent"] is True
    assert proof["forbidden_control_tokens_found"] == []
    assert proof["runtime_authority_changed"] is False
    assert proof["operator_gate_required"] is False
    assert proof["external_writes_applied"] is False


def test_magma_handoff_metrics_alertmanager_adapter_smoke_blocks_missing_inputs(
    tmp_path: Path,
) -> None:
    proof = build_magma_handoff_metrics_alertmanager_adapter_smoke(tmp_path)

    assert proof["ok"] is False
    assert proof["blocked_reason"] == "missing_required_inputs"
    assert "waggledance/adapters/http/magma_handoff_metrics_alert_feed.py" in (
        proof["missing_inputs"]
    )
    assert "configs/settings.yaml" in proof["missing_inputs"]
    assert proof["runtime_authority_changed"] is False


def test_low_risk_autogrowth_ops_alert_state_smoke_reports_dashboard_contract() -> None:
    proof = build_low_risk_autogrowth_ops_alert_state_smoke(ROOT)

    assert proof["ok"] is True
    assert proof["proof_id"] == ("low_risk_autogrowth_ops_alert_state_smoke_v1")
    assert proof["ops_endpoint"] == "/api/ops"
    assert proof["dashboard_path"] == "web/hologram-brain-v6.html"
    assert proof["runbook_path"] == ("docs/operations/LOW_RISK_AUTOGROWTH_RUNBOOK.md")
    assert proof["api_contract_present"] is True
    assert proof["alertmanager_adapter_contract_present"] is True
    assert proof["provider_health_metrics_visible"] is True
    assert proof["ui_contract_present"] is True
    assert proof["test_contract_present"] is True
    assert proof["docs_contract_present"] is True
    assert proof["alert_state_visible"] is True
    assert proof["local_snapshot_source"] is True
    assert proof["prometheus_alertmanager_feed_supported"] is True
    assert proof["feed_slo_panels_visible"] is True
    assert proof["feed_drill_evidence_visible"] is True
    assert proof["fixed_alert_ids_enforced"] is True
    assert proof["raw_alertmanager_labels_excluded"] is True
    assert proof["rate_rules_deferred"] is True
    assert proof["forbidden_controls_absent"] is True
    assert proof["forbidden_control_tokens_found"] == []
    assert proof["runtime_authority_changed"] is False
    assert proof["operator_gate_required"] is False
    assert proof["external_writes_applied"] is False


def test_low_risk_autogrowth_ops_alert_state_smoke_blocks_missing_inputs(
    tmp_path: Path,
) -> None:
    proof = build_low_risk_autogrowth_ops_alert_state_smoke(tmp_path)

    assert proof["ok"] is False
    assert proof["blocked_reason"] == "missing_required_inputs"
    assert "waggledance/adapters/http/routes/compat_dashboard.py" in (
        proof["missing_inputs"]
    )
    assert "waggledance/adapters/http/autogrowth_alert_feed.py" in (
        proof["missing_inputs"]
    )
    assert "web/hologram-brain-v6.html" in proof["missing_inputs"]
    assert proof["runtime_authority_changed"] is False


def test_hex_mesh_route_stage_ui_smoke_blocks_missing_inputs(tmp_path: Path) -> None:
    proof = build_hex_mesh_route_stage_ui_smoke(tmp_path)

    assert proof["ok"] is False
    assert proof["blocked_reason"] == "missing_required_inputs"
    assert "web/hologram-brain-v6.html" in proof["missing_inputs"]
    assert "waggledance/adapters/http/routes/chat.py" in proof["missing_inputs"]
    assert proof["no_runtime_mutation"] is True
    assert proof["external_writes_applied"] is False


def test_hex_mesh_route_stage_ui_smoke_blocks_foreign_root(
    tmp_path: Path,
) -> None:
    for rel_path in (
        "web/hologram-brain-v6.html",
        "waggledance/adapters/http/routes/chat.py",
        "tests/test_hologram_ui_stabilization.py",
        "tests/integration/test_chat_api_contract.py",
    ):
        path = tmp_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# placeholder\n", encoding="utf-8")

    proof = build_hex_mesh_route_stage_ui_smoke(tmp_path)

    assert proof["ok"] is False
    assert proof["blocked_reason"] == "non_current_import_root"
    assert proof["missing_inputs"] == []
    assert proof["inspected_root"] == str(tmp_path.resolve())
    assert proof["import_root"] == str(ROOT.resolve())
    assert proof["no_runtime_mutation"] is True
    assert proof["external_writes_applied"] is False


def test_hex_mesh_route_stage_operator_metrics_smoke_blocks_missing_inputs(
    tmp_path: Path,
) -> None:
    proof = build_hex_mesh_route_stage_operator_metrics_smoke(tmp_path)

    assert proof["ok"] is False
    assert proof["blocked_reason"] == "missing_required_inputs"
    assert "waggledance/adapters/http/routes/metrics.py" in proof["missing_inputs"]
    assert "tests/test_metrics_endpoint.py" in proof["missing_inputs"]
    assert proof["runtime_routing_changed"] is False
    assert proof["disabled_hex_paths_enabled"] is False
    assert proof["no_runtime_mutation"] is True
    assert proof["external_writes_applied"] is False


def test_hex_mesh_route_stage_operator_metrics_smoke_blocks_foreign_root(
    tmp_path: Path,
) -> None:
    for rel_path in (
        "waggledance/adapters/http/routes/metrics.py",
        "waggledance/adapters/http/routes/chat.py",
        "tests/test_metrics_endpoint.py",
        "docs/API.md",
    ):
        path = tmp_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# placeholder\n", encoding="utf-8")

    proof = build_hex_mesh_route_stage_operator_metrics_smoke(tmp_path)

    assert proof["ok"] is False
    assert proof["blocked_reason"] == "non_current_import_root"
    assert proof["missing_inputs"] == []
    assert proof["inspected_root"] == str(tmp_path.resolve())
    assert proof["import_root"] == str(ROOT.resolve())
    assert proof["runtime_routing_changed"] is False
    assert proof["disabled_hex_paths_enabled"] is False
    assert proof["no_runtime_mutation"] is True
    assert proof["external_writes_applied"] is False


def test_hex_mesh_route_stage_runtime_metrics_smoke_blocks_missing_inputs(
    tmp_path: Path,
) -> None:
    proof = build_hex_mesh_route_stage_runtime_metrics_smoke(tmp_path)

    assert proof["ok"] is False
    assert proof["blocked_reason"] == "missing_required_inputs"
    assert "waggledance/adapters/http/routes/chat.py" in proof["missing_inputs"]
    assert "waggledance/adapters/http/routes/metrics.py" in proof["missing_inputs"]
    assert "docs/operations/ROUTE_STAGE_LATENCY_RUNBOOK.md" in (proof["missing_inputs"])
    assert proof["runtime_routing_changed"] is False
    assert proof["disabled_hex_paths_enabled"] is False
    assert proof["raw_payload_recorded"] is False
    assert proof["no_runtime_mutation"] is True
    assert proof["external_writes_applied"] is False


def test_hex_mesh_route_stage_runtime_metrics_smoke_blocks_foreign_root(
    tmp_path: Path,
) -> None:
    for rel_path in (
        "waggledance/adapters/http/routes/chat.py",
        "waggledance/adapters/http/routes/metrics.py",
        "waggledance/adapters/http/routes/compat_dashboard.py",
        "waggledance/adapters/http/route_stage_latency_feed.py",
        "waggledance/bootstrap/container.py",
        "web/hologram-brain-v6.html",
        "configs/settings.yaml",
        "tests/test_metrics_endpoint.py",
        "tests/integration/test_chat_api_contract.py",
        "tests/test_legacy_consolidation.py",
        "docs/API.md",
        "docs/operations/ROUTE_STAGE_LATENCY_RUNBOOK.md",
        "tools/build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template.py",
        "tests/tools/test_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template.py",
        "tools/build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry.py",
        "tests/tools/test_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry.py",
        "tools/verify_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry.py",
        "tests/tools/test_verify_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry.py",
        "tools/build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary.py",
        "tests/tools/test_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary.py",
        "tools/build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template.py",
        "tests/tools/test_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template.py",
        "tools/build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry.py",
        "tests/tools/test_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry.py",
        "tools/verify_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry.py",
        "tests/tools/test_verify_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry.py",
        "tools/build_route_stage_feed_health_drill_evidence_reviewer_handoff_summary.py",
        "tests/tools/test_route_stage_feed_health_drill_evidence_reviewer_handoff_summary.py",
        "tools/build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index.py",
        "tests/tools/test_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index.py",
        "tools/verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index.py",
        "tools/verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry.py",
        "tests/tools/test_verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry.py",
        "tools/build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry.py",
        "tests/tools/test_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry.py",
        "tools/build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template.py",
        "tests/tools/test_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template.py",
    ):
        path = tmp_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# placeholder\n", encoding="utf-8")

    proof = build_hex_mesh_route_stage_runtime_metrics_smoke(tmp_path)

    assert proof["ok"] is False
    assert proof["blocked_reason"] == "non_current_import_root"
    assert proof["missing_inputs"] == []
    assert proof["inspected_root"] == str(tmp_path.resolve())
    assert proof["import_root"] == str(ROOT.resolve())
    assert proof["runtime_routing_changed"] is False
    assert proof["disabled_hex_paths_enabled"] is False
    assert proof["raw_payload_recorded"] is False
    assert proof["no_runtime_mutation"] is True
    assert proof["external_writes_applied"] is False


def test_manifest_embeds_hexagonal_upgrade_proof_without_upgrading_claim() -> None:
    report = build_manifest(ROOT)
    capability = _by_id(report)["hexagonal_upgrades"]

    assert capability["status"] == "partial"
    assert capability["claim_safe"] is False
    assert capability["proof"]["ok"] is True
    assert capability["proof"]["runtime_boundary_smoke"]["ok"] is True
    assert (
        capability["proof"]["runtime_boundary_smoke"]["active_runtime_dispatch_enabled"]
        is False
    )
    assert (
        capability["proof"]["runtime_boundary_smoke"][
            "shadow_child_cell_ids_absent_from_runtime_config"
        ]
        is True
    )
    assert (
        capability["proof"]["runtime_boundary_smoke"]["operator_metrics_smoke"]["ok"]
        is True
    )
    assert capability["proof"]["shadow_subdivision_replay"]["ok"] is True
    assert (
        capability["proof"]["shadow_subdivision_replay"]["proof_type"]
        == "shadow_replay_hypothetical"
    )
    assert (
        capability["proof"]["shadow_subdivision_replay"]["guardrails"][
            "runtime_authority_changed"
        ]
        is False
    )
    assert (
        capability["proof"]["shadow_subdivision_replay"]["source_snapshot"][
            "git_commit_available"
        ]
        is True
    )
    assert capability["proof"]["shadow_subdivision_replay_verification"]["ok"] is True
    assert (
        capability["proof"]["shadow_subdivision_replay_verification"]["proof_id"]
        == "hex_shadow_subdivision_replay_verifier_v1"
    )
    assert (
        capability["proof"]["shadow_subdivision_replay_verification"][
            "artifact_declared_ok"
        ]
        is True
    )
    assert (
        capability["proof"]["shadow_subdivision_replay_verification"][
            "expected_git_commit"
        ]
        == capability["proof"]["shadow_subdivision_replay"]["source_snapshot"][
            "git_commit"
        ]
    )
    assert (
        capability["proof"]["shadow_subdivision_replay_verifier_summary"]["ok"] is True
    )
    assert (
        capability["proof"]["shadow_subdivision_replay_verifier_summary"]["proof_id"]
        == "hex_shadow_subdivision_replay_verifier_summary_v1"
    )
    assert (
        capability["proof"]["shadow_subdivision_replay_verifier_summary"][
            "approval_granted"
        ]
        is False
    )
    assert (
        capability["proof"]["shadow_subdivision_replay_verifier_summary"][
            "direct_bridge_write_performed"
        ]
        is False
    )
    assert (
        capability["proof"]["shadow_subdivision_replay_verifier_summary"][
            "runtime_subdivision_authority_granted"
        ]
        is False
    )
    template = capability["proof"][
        "shadow_subdivision_replay_verifier_summary_bridge_event_template"
    ]
    assert template["ok"] is True
    assert (
        template["proof_id"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_v1"
    )
    assert template["template_only"] is True
    assert template["direct_bridge_write_performed"] is False
    assert template["runtime_subdivision_authority_granted"] is False
    assert template["bridge_event_template"]["cwd"] == "template_not_emitted"
    assert (
        template["bridge_event_template"]["payload"]["schema_version"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template.v1"
    )
    index_entry = capability["proof"][
        "shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry"
    ]
    assert index_entry["ok"] is True
    assert (
        index_entry["proof_id"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_v1"
    )
    assert index_entry["template_only"] is True
    assert index_entry["template_index_entry"]["bridge_event_schema_validated"] is True
    assert (
        index_entry["template_index_entry"]["event_status"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_ready"
    )
    assert index_entry["direct_bridge_write_performed"] is False
    assert index_entry["runtime_subdivision_authority_granted"] is False
    assert index_entry["artifact_payloads_included"] is False
    assert index_entry["local_paths_recorded"] is False
    index_entry_verification = capability["proof"][
        "shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification"
    ]
    assert index_entry_verification["ok"] is True
    assert (
        index_entry_verification["proof_id"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_v1"
    )
    assert (
        index_entry_verification["verification_version"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification.v1"
    )
    assert index_entry_verification["source_contract_check"] == "match"
    assert index_entry_verification["rebuilt_index_entry_check"] == "match"
    assert index_entry_verification["bridge_event_schema_check"] == "match"
    assert (
        index_entry_verification["digest_checks"][
            "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template"
        ]
        == "match"
    )
    assert index_entry_verification["direct_bridge_write_performed"] is False
    assert index_entry_verification["runtime_subdivision_authority_granted"] is False
    assert index_entry_verification["artifact_payloads_included"] is False
    assert index_entry_verification["local_paths_recorded"] is False
    index_entry_verification_summary = capability["proof"][
        "shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary"
    ]
    assert index_entry_verification_summary["ok"] is True
    assert (
        index_entry_verification_summary["proof_id"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_v1"
    )
    assert (
        index_entry_verification_summary["summary_version"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary.v1"
    )
    summary_nested = index_entry_verification_summary[
        "shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification"
    ]
    assert summary_nested["verification_ok"] is True
    assert summary_nested["source_contract_check"] == "match"
    assert summary_nested["rebuilt_index_entry_check"] == "match"
    assert summary_nested["bridge_event_schema_check"] == "match"
    assert (
        summary_nested["digest_checks"][
            "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template"
        ]
        == "match"
    )
    assert index_entry_verification_summary["direct_bridge_write_performed"] is False
    assert (
        index_entry_verification_summary["runtime_subdivision_authority_granted"]
        is False
    )
    assert index_entry_verification_summary["artifact_payloads_included"] is False
    assert index_entry_verification_summary["local_paths_recorded"] is False
    index_entry_verification_summary_template = capability["proof"][
        "shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template"
    ]
    assert index_entry_verification_summary_template["ok"] is True
    assert (
        index_entry_verification_summary_template["proof_id"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_v1"
    )
    assert (
        index_entry_verification_summary_template["template_version"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template.v1"
    )
    assert index_entry_verification_summary_template["template_only"] is True
    assert (
        index_entry_verification_summary_template["direct_bridge_write_performed"]
        is False
    )
    assert (
        index_entry_verification_summary_template[
            "runtime_subdivision_authority_granted"
        ]
        is False
    )
    summary_template_event = index_entry_verification_summary_template[
        "bridge_event_template"
    ]
    assert summary_template_event["cwd"] == "template_not_emitted"
    assert (
        summary_template_event["status"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_ready"
    )
    assert (
        summary_template_event["payload"]["schema_version"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template.v1"
    )
    assert (
        summary_template_event["payload"]["summary_proof_id"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_v1"
    )
    assert (
        summary_template_event["payload"]["operator_boundary"][
            "index_entry_verification_report_boundary_ok"
        ]
        is True
    )
    summary_template_index_entry = capability["proof"][
        "shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry"
    ]
    summary_template_index_artifact = summary_template_index_entry["artifacts"][0]
    summary_template_index = summary_template_index_entry["template_index_entry"]
    assert summary_template_index_entry["ok"] is True
    assert (
        summary_template_index_entry["proof_id"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_v1"
    )
    assert (
        summary_template_index_entry["index_entry_version"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry.v1"
    )
    assert summary_template_index_entry["artifact_count"] == 1
    assert (
        summary_template_index_artifact["artifact_id"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template"
    )
    assert summary_template_index_artifact["payload_included"] is False
    assert summary_template_index_artifact["local_path_recorded"] is False
    assert (
        summary_template_index["source_summary_proof_id"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_v1"
    )
    assert (
        summary_template_index["template_proof_id"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_v1"
    )
    assert summary_template_index["source_contract_check"] == "match"
    assert summary_template_index["bridge_event_schema_validated"] is True
    assert summary_template_index["template_only"] is True
    assert summary_template_index_entry["template_only"] is True
    assert summary_template_index_entry["direct_bridge_write_performed"] is False
    assert summary_template_index_entry["transport_added"] is False
    assert summary_template_index_entry["external_fetch_performed"] is False
    assert (
        summary_template_index_entry["runtime_subdivision_authority_granted"] is False
    )
    assert summary_template_index_entry["artifact_payloads_included"] is False
    assert summary_template_index_entry["local_paths_recorded"] is False
    summary_template_index_entry_verification = capability["proof"][
        "shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification"
    ]
    assert summary_template_index_entry_verification["ok"] is True
    assert (
        summary_template_index_entry_verification["proof_id"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_v1"
    )
    assert (
        summary_template_index_entry_verification["verification_version"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification.v1"
    )
    assert (
        summary_template_index_entry_verification["verified_proof_id"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_v1"
    )
    summary_template_index_entry_artifact_id = (
        "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template"
    )
    assert (
        summary_template_index_entry_verification["digest_checks"][
            summary_template_index_entry_artifact_id
        ]
        == "match"
    )
    assert (
        summary_template_index_entry_verification["size_checks"][
            summary_template_index_entry_artifact_id
        ]
        == "match"
    )
    assert (
        summary_template_index_entry_verification["schema_version_checks"][
            summary_template_index_entry_artifact_id
        ]
        == "match"
    )
    assert summary_template_index_entry_verification["source_contract_check"] == "match"
    assert summary_template_index_entry_verification["rebuilt_index_entry_check"] == (
        "match"
    )
    assert summary_template_index_entry_verification["bridge_event_schema_check"] == (
        "match"
    )
    assert (
        summary_template_index_entry_verification["direct_bridge_write_performed"]
        is False
    )
    assert (
        summary_template_index_entry_verification[
            "runtime_subdivision_authority_granted"
        ]
        is False
    )
    assert (
        summary_template_index_entry_verification["artifact_payloads_included"]
        is False
    )
    assert summary_template_index_entry_verification["local_paths_recorded"] is False
    assert "local verifier" in capability["safe_statement"]
    assert "reviewer summary" in capability["safe_statement"]
    assert "bridge-event template" in capability["safe_statement"]
    assert "local index entry" in capability["safe_statement"]
    assert "verification summary" in capability["safe_statement"]
    assert (
        "template-only bridge-event template renderer" in capability["safe_statement"]
    )
    assert "verification summary bridge-event template digest" in (
        capability["safe_statement"]
    )
    # next_smallest_pr advanced beyond the local verifier slice to its summary renderer.
    assert "cross-consistency digest" in capability["next_smallest_pr"]
    assert "verifier result" in capability["next_smallest_pr"]
    assert "index-entry" in capability["next_smallest_pr"]
    assert "reviewer summary renderer" in capability["next_smallest_pr"]
    assert "Add a local verifier" not in capability["next_smallest_pr"]
    assert report["summary"]["proofs_ok"] is True


def test_manifest_embeds_shadow_only_invariant_proof_without_upgrading_claim() -> None:
    report = build_manifest(ROOT)
    capability = _by_id(report)["hexagonal_upgrades"]
    proof = capability["proof"]
    # The measurement-only proof is embedded but the capability claim is NOT
    # upgraded and the hex proof "ok" is unchanged (it is not folded in).
    assert capability["claim_safe"] is False
    assert proof["ok"] is True
    inv = proof["shadow_only_invariant"]
    assert inv["report_version"] == "wd.shadow_only_invariant_proof.v1"
    assert inv["ok"] is True
    assert inv["measurement_basis"] == "v1_shadow_only_invariant"
    assert inv["deterministic_replay"]["stable_identical"] is True
    block = inv["invariant"]
    assert block["invariant_holds"] is True
    assert block["target_state_is_shadow"] is True
    assert block["no_runtime_mutation"] is True
    assert block["guardrails_all_clean"] is True
    assert block["transition_occurred"] is False
    # Honest STRICT-int 0 transition count (never a bool), never upgraded.
    transition_count = block["shadow_to_candidate_subdivision_transitions_total"]
    assert transition_count == 0
    assert type(transition_count) is int and not isinstance(transition_count, bool)
    assert block["claim_safe"] is False
    # The measurement-only proof must NOT have flipped the capability claim.
    assert proof.get("claim_safe") is not True
    # And it surfaces affirmatively in the progress counter as shadow_only_enforced.
    counters = build_vision_progress_counters(report)
    shadow_counter = counters["milestone_counters"][
        "hex_subdivision_shadow_only_invariant"
    ]
    assert shadow_counter["invariant_proof_available"] is True
    assert shadow_counter["shadow_only_enforced"] is True
    assert shadow_counter["claim_safe"] is False
    transitions_total = counters["milestone_counters"][
        "shadow_to_candidate_subdivision_transitions_total"
    ]
    assert transitions_total["current_value"] == 0
    assert transitions_total["satisfied"] is False


def test_manifest_embeds_low_risk_autonomy_proof_without_upgrading_claim() -> None:
    report = build_manifest(ROOT)
    capability = _by_id(report)["low_risk_autonomy_loop"]

    assert capability["status"] == "partial"
    assert capability["claim_safe"] is False
    assert capability["proof"]["ok"] is True
    assert capability["proof"]["route_before"]["source"] == "gap_emitted"
    assert capability["proof"]["scheduler_tick"]["outcome"] == "auto_promoted"
    assert capability["proof"]["route_after"]["source"] == "auto_promoted_solver"
    assert capability["proof"]["runtime_boundary_smoke"]["ok"] is True
    assert (
        capability["proof"]["runtime_boundary_smoke"]["default_interval_seconds"]
        == 30.0
    )
    assert (
        capability["proof"]["runtime_boundary_smoke"]["default_max_ticks_per_wake"]
        == 20
    )
    assert (
        capability["proof"]["runtime_boundary_smoke"]["runtime_authority_changed"]
        is False
    )
    real_loop = capability["proof"]["real_loop_dry_run"]
    assert real_loop["ok"] is True
    assert real_loop["claim_label"] == "MEASURED_LOCAL_DRY_RUN"
    assert real_loop["local_artifacts_written"] is False
    assert real_loop["chain"]["detector_signals_recorded"] == 1
    assert real_loop["chain"]["intents_created"] == 1
    assert real_loop["chain"]["intents_enqueued"] == 1
    assert real_loop["chain"]["scheduler_outcome"] == "auto_promoted"
    assert real_loop["chain"]["auto_promoted_solver_count"] == 1
    assert real_loop["dispatch"]["matched"] is True
    assert real_loop["dispatch"]["output"] == 298.15
    assert real_loop["authority_boundary"] == {
        "external_writes_applied": False,
        "production_control_plane_touched": False,
        "production_scheduler_enqueue": False,
        "provider_jobs_created": False,
        "builder_jobs_created": False,
        "gate_skip_authority": False,
        "operator_gate_bypassed": False,
        "runtime_authority_granted": False,
        "fast_track_priority": False,
    }
    manifest_contribution = capability["proof"]["real_loop_manifest_contribution"]
    assert set(manifest_contribution) == set(
        _REAL_LOOP_MANIFEST_CONTRIBUTION_SAFE_KEYS
    )
    assert manifest_contribution["ok"] is True
    assert manifest_contribution["deterministic"] is True
    assert manifest_contribution["evidence_present"] is True
    assert manifest_contribution["runtime_authority_granted"] is False
    assert manifest_contribution["external_writes_applied"] is False
    assert manifest_contribution["scheduler_enqueue"] is False
    assert manifest_contribution["production_flip"] is False
    assert manifest_contribution["production_authority_granted"] is False
    assert manifest_contribution["provider_calls"] == 0
    assert manifest_contribution["claim_safe"] is False
    assert (
        manifest_contribution["measurement_basis"]
        == "v1_low_risk_real_loop_manifest_contribution"
    )
    assert capability["proof"]["operator_metrics_smoke"]["ok"] is True
    assert (
        capability["proof"]["operator_metrics_smoke"]["operator_visible_metrics"]
        is True
    )
    assert (
        capability["proof"]["operator_metrics_smoke"]["runtime_authority_changed"]
        is False
    )
    assert capability["proof"]["alert_runbook_smoke"]["ok"] is True
    assert (
        capability["proof"]["alert_runbook_smoke"]["alert_thresholds_documented"]
        is True
    )
    assert (
        capability["proof"]["alert_runbook_smoke"]["forbidden_controls_absent"] is True
    )
    assert (
        capability["proof"]["alert_runbook_smoke"]["runtime_authority_changed"] is False
    )
    assert capability["proof"]["ops_alert_state_smoke"]["ok"] is True
    assert capability["proof"]["ops_alert_state_smoke"]["alert_state_visible"] is True
    assert (
        capability["proof"]["ops_alert_state_smoke"]["forbidden_controls_absent"]
        is True
    )
    assert (
        capability["proof"]["ops_alert_state_smoke"]["runtime_authority_changed"]
        is False
    )
    gap_report = capability["proof"]["runtime_gap_report"]
    assert gap_report["scheduler_candidate_count"] == 1
    assert gap_report["queue_writes_applied"] is False
    preview = capability["proof"]["scheduler_candidate_artifact_preview"]
    assert preview["scheduler_candidate_count"] == 1
    assert preview["scheduler_enqueue_allowed"] is False
    assert preview["scheduler_tick_allowed"] is False
    assert preview["bridge_event_written"] is False
    assert preview["fast_track_priority"] is False
    assert preview["scheduler_candidates"][0]["queue_priority"] == "normal"
    assert preview["scheduler_candidates"][0]["gate_skip_allowed"] is False
    template = capability["proof"]["scheduler_candidate_bridge_event_template"]
    assert template["ok"] is True
    assert template["template_only"] is True
    assert template["direct_bridge_write_performed"] is False
    assert template["scheduler_enqueue_allowed"] is False
    assert template["scheduler_tick_allowed"] is False
    assert template["bridge_event_written"] is False
    assert template["fast_track_priority"] is False
    assert template["gate_skip_allowed"] is False
    event_payload = template["bridge_event_template"]["payload"]
    assert event_payload["artifact_payloads_included"] is False
    assert event_payload["authority_boundary"]["runtime_authority_granted"] is False
    assert event_payload["authority_boundary"]["scheduler_enqueue_allowed"] is False
    assert event_payload["authority_boundary"]["scheduler_tick_allowed"] is False
    assert event_payload["authority_boundary"]["gate_skip_allowed"] is False
    assert (
        event_payload["scheduler_candidate_preview"]["scheduler_candidate_count"]
        == 1
    )
    index_entry = capability["proof"][
        "scheduler_candidate_bridge_event_template_index_entry"
    ]
    assert index_entry["ok"] is True
    assert index_entry["template_only"] is True
    assert index_entry["template_index_entry"]["source_contract_check"] == "match"
    assert index_entry["template_index_entry"]["rebuilt_template_check"] == "match"
    assert index_entry["template_index_entry"]["scheduler_enqueue_allowed"] is False
    assert index_entry["template_index_entry"]["scheduler_tick_allowed"] is False
    assert index_entry["template_index_entry"]["bridge_event_written"] is False
    assert index_entry["template_index_entry"]["fast_track_priority"] is False
    assert index_entry["template_index_entry"]["gate_skip_allowed"] is False
    assert index_entry["artifact_payloads_included"] is False
    assert index_entry["local_paths_recorded"] is False
    verification = capability["proof"][
        "scheduler_candidate_bridge_event_template_index_entry_verification"
    ]
    assert verification["ok"] is True
    assert verification["source_contract_check"] == "match"
    assert verification["rebuilt_index_entry_check"] == "match"
    assert verification["bridge_event_schema_check"] == "match"
    assert verification["scheduler_enqueue_allowed"] is False
    assert verification["scheduler_tick_allowed"] is False
    assert verification["bridge_event_written"] is False
    assert verification["fast_track_priority"] is False
    assert verification["gate_skip_allowed"] is False
    assert verification["artifact_payloads_included"] is False
    assert verification["local_paths_recorded"] is False
    # next_smallest_pr advanced beyond the now-wired cross-consistency digest bridge-event
    # template (#1291) to a local index entry for that template (next chain step).
    assert "cross-consistency digest" in capability["next_smallest_pr"]
    assert "index entry" in capability["next_smallest_pr"]
    assert "bridge-event template" in capability["next_smallest_pr"]
    # no longer the just-completed renderer slice.
    assert "bridge-event renderer" not in capability["next_smallest_pr"]
    # no longer the just-completed digest slice ("Add a ... digest confirming ...").
    assert "digest confirming the real-loop dry-run proof" not in (
        capability["next_smallest_pr"]
    )
    assert "Promote the measured local autogrowth real-loop proof" not in (
        capability["next_smallest_pr"]
    )
    assert "read-only dashboard ops overlay" in capability["safe_statement"]
    assert "local fallback alert state" in capability["safe_statement"]
    assert "optional sanitized Alertmanager alert feed" in (
        capability["safe_statement"]
    )
    assert "operator alert thresholds" in capability["safe_statement"]
    assert "measured local real-loop dry run" in capability["safe_statement"]
    assert "scheduler-candidate preview artifact" in capability["safe_statement"]
    assert "template-only bridge-event renderer" in capability["safe_statement"]
    assert "local index entry" in capability["safe_statement"]
    assert "local verifier" in capability["safe_statement"]
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
    assert capability["proof"]["route_stage_ui_smoke"]["ok"] is True
    assert capability["proof"]["route_stage_operator_metrics_smoke"]["ok"] is True
    assert capability["proof"]["route_stage_runtime_metrics_smoke"]["ok"] is True
    assert (
        capability["proof"]["route_stage_runtime_metrics_smoke"][
            "histogram_quantile_supported"
        ]
        is True
    )
    assert (
        capability["proof"]["route_stage_runtime_metrics_smoke"][
            "latency_panel_templates_visible"
        ]
        is True
    )
    assert (
        capability["proof"]["route_stage_runtime_metrics_smoke"][
            "latency_feed_state_visible"
        ]
        is True
    )
    assert (
        capability["proof"]["route_stage_runtime_metrics_smoke"][
            "latency_feed_slo_drill_supported"
        ]
        is True
    )
    assert (
        capability["proof"]["route_stage_runtime_metrics_smoke"][
            "latency_feed_drill_evidence_verifier_supported"
        ]
        is True
    )
    assert (
        capability["proof"]["route_stage_runtime_metrics_smoke"][
            "latency_feed_drill_evidence_verification_summary_bridge_event_template_supported"
        ]
        is True
    )
    assert (
        capability["proof"]["route_stage_runtime_metrics_smoke"][
            "latency_feed_drill_evidence_verification_summary_bridge_event_template_index_entry_supported"
        ]
        is True
    )
    assert (
        capability["proof"]["route_stage_runtime_metrics_smoke"][
            "latency_feed_drill_evidence_verification_summary_bridge_event_template_index_entry_verifier_supported"
        ]
        is True
    )
    assert (
        capability["proof"]["route_stage_runtime_metrics_smoke"][
            "latency_feed_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_supported"
        ]
        is True
    )
    assert (
        capability["proof"]["route_stage_runtime_metrics_smoke"][
            "latency_feed_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_supported"
        ]
        is True
    )
    assert (
        capability["proof"]["route_stage_runtime_metrics_smoke"][
            "latency_feed_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_supported"
        ]
        is True
    )
    assert (
        capability["proof"]["route_stage_runtime_metrics_smoke"][
            "latency_feed_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verifier_supported"
        ]
        is True
    )
    assert (
        capability["proof"]["route_stage_runtime_metrics_smoke"][
            "latency_feed_drill_evidence_reviewer_handoff_summary_supported"
        ]
        is True
    )
    assert (
        capability["proof"]["route_stage_runtime_metrics_smoke"][
            "latency_feed_drill_evidence_reviewer_handoff_bundle_verifier_summary_bridge_event_template_index_entry_supported"
        ]
        is True
    )
    assert (
        capability["proof"]["route_stage_runtime_metrics_smoke"][
            "latency_feed_drill_evidence_reviewer_handoff_bundle_verifier_summary_bridge_event_template_index_entry_verifier_supported"
        ]
        is True
    )
    assert (
        capability["proof"]["route_stage_runtime_metrics_smoke"][
            "latency_feed_drill_evidence_reviewer_handoff_bundle_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_supported"
        ]
        is True
    )
    verifier_smoke = capability["proof"]["route_stage_runtime_metrics_smoke"][
        "drill_evidence_verifier_smoke"
    ]
    assert verifier_smoke["ok"] is True
    assert verifier_smoke["accepts_valid_package"] is True
    assert verifier_smoke["rejects_authority_forgery"] is True
    assert verifier_smoke["network_access_performed"] is False
    assert verifier_smoke["runtime_authority_granted"] is False
    assert verifier_smoke["external_writes_applied"] is False
    template_smoke = verifier_smoke["verification_summary_bridge_event_template_smoke"]
    assert template_smoke["ok"] is True
    assert template_smoke["template_only"] is True
    assert template_smoke["manual_review_required"] is True
    assert template_smoke["direct_bridge_write_performed"] is False
    assert template_smoke["artifact_payloads_included"] is False
    assert template_smoke["local_paths_recorded"] is False
    assert template_smoke["network_access_performed"] is False
    template_index_entry_smoke = verifier_smoke[
        "verification_summary_bridge_event_template_index_entry_smoke"
    ]
    assert template_index_entry_smoke["ok"] is True
    assert template_index_entry_smoke["template_only"] is True
    assert template_index_entry_smoke["manual_review_required"] is True
    assert template_index_entry_smoke["source_contract_check"] == "match"
    assert template_index_entry_smoke["rebuilt_template_check"] == "match"
    assert template_index_entry_smoke["direct_bridge_write_performed"] is False
    assert template_index_entry_smoke["artifact_payloads_included"] is False
    assert template_index_entry_smoke["local_paths_recorded"] is False
    assert template_index_entry_smoke["network_access_performed"] is False
    template_index_entry_verification_smoke = verifier_smoke[
        "verification_summary_bridge_event_template_index_entry_verification_smoke"
    ]
    assert template_index_entry_verification_smoke["ok"] is True
    assert (
        template_index_entry_verification_smoke["source_contract_check"] == "match"
    )
    assert (
        template_index_entry_verification_smoke["rebuilt_index_entry_check"]
        == "match"
    )
    assert (
        template_index_entry_verification_smoke["bridge_event_schema_check"]
        == "match"
    )
    assert set(template_index_entry_verification_smoke["digest_checks"].values()) == {
        "match"
    }
    assert (
        template_index_entry_verification_smoke["direct_bridge_write_performed"]
        is False
    )
    assert (
        template_index_entry_verification_smoke["artifact_payloads_included"]
        is False
    )
    assert template_index_entry_verification_smoke["local_paths_recorded"] is False
    assert (
        template_index_entry_verification_smoke["network_access_performed"] is False
    )
    template_index_entry_verification_summary_smoke = verifier_smoke[
        "verification_summary_bridge_event_template_index_entry_verification_summary_smoke"
    ]
    assert template_index_entry_verification_summary_smoke["ok"] is True
    assert (
        template_index_entry_verification_summary_smoke["verification_ok"] is True
    )
    assert (
        template_index_entry_verification_summary_smoke["source_contract_check"]
        == "match"
    )
    assert (
        template_index_entry_verification_summary_smoke[
            "rebuilt_index_entry_check"
        ]
        == "match"
    )
    assert (
        template_index_entry_verification_summary_smoke[
            "bridge_event_schema_check"
        ]
        == "match"
    )
    assert (
        template_index_entry_verification_summary_smoke[
            "verification_report_boundary_ok"
        ]
        is True
    )
    assert (
        template_index_entry_verification_summary_smoke[
            "direct_bridge_write_performed"
        ]
        is False
    )
    assert (
        template_index_entry_verification_summary_smoke[
            "artifact_payloads_included"
        ]
        is False
    )
    assert (
        template_index_entry_verification_summary_smoke["local_paths_recorded"]
        is False
    )
    assert (
        template_index_entry_verification_summary_smoke["network_access_performed"]
        is False
    )
    summary_template_smoke = verifier_smoke[
        "verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_smoke"
    ]
    assert summary_template_smoke["ok"] is True
    assert summary_template_smoke["template_only"] is True
    assert summary_template_smoke["manual_review_required"] is True
    assert summary_template_smoke["direct_bridge_write_performed"] is False
    assert summary_template_smoke["artifact_payloads_included"] is False
    assert summary_template_smoke["local_paths_recorded"] is False
    assert summary_template_smoke["network_access_performed"] is False
    summary_template_index_entry_smoke = verifier_smoke[
        "verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_smoke"
    ]
    assert summary_template_index_entry_smoke["ok"] is True
    assert summary_template_index_entry_smoke["template_only"] is True
    assert summary_template_index_entry_smoke["manual_review_required"] is True
    assert (
        summary_template_index_entry_smoke["source_contract_check"] == "match"
    )
    assert (
        summary_template_index_entry_smoke["rebuilt_template_check"] == "match"
    )
    assert (
        summary_template_index_entry_smoke["direct_bridge_write_performed"]
        is False
    )
    assert (
        summary_template_index_entry_smoke["artifact_payloads_included"] is False
    )
    assert summary_template_index_entry_smoke["local_paths_recorded"] is False
    assert summary_template_index_entry_smoke["network_access_performed"] is False
    summary_template_index_entry_verification_smoke = verifier_smoke[
        "verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_smoke"
    ]
    assert summary_template_index_entry_verification_smoke["ok"] is True
    assert (
        summary_template_index_entry_verification_smoke["source_contract_check"]
        == "match"
    )
    assert (
        summary_template_index_entry_verification_smoke["rebuilt_index_entry_check"]
        == "match"
    )
    assert (
        summary_template_index_entry_verification_smoke["bridge_event_schema_check"]
        == "match"
    )
    assert (
        set(
            summary_template_index_entry_verification_smoke[
                "digest_checks"
            ].values()
        )
        == {"match"}
    )
    assert (
        summary_template_index_entry_verification_smoke[
            "direct_bridge_write_performed"
        ]
        is False
    )
    assert (
        summary_template_index_entry_verification_smoke[
            "artifact_payloads_included"
        ]
        is False
    )
    assert (
        summary_template_index_entry_verification_smoke["local_paths_recorded"]
        is False
    )
    assert (
        summary_template_index_entry_verification_smoke["network_access_performed"]
        is False
    )
    reviewer_handoff_summary_smoke = verifier_smoke[
        "reviewer_handoff_summary_smoke"
    ]
    assert reviewer_handoff_summary_smoke["ok"] is True
    assert reviewer_handoff_summary_smoke["verification_ok"] is True
    assert reviewer_handoff_summary_smoke["source_contract_check"] == "match"
    assert reviewer_handoff_summary_smoke["rebuilt_index_entry_check"] == "match"
    assert reviewer_handoff_summary_smoke["bridge_event_schema_check"] == "match"
    assert reviewer_handoff_summary_smoke["verification_report_boundary_ok"] is True
    assert reviewer_handoff_summary_smoke["manual_review_required"] is True
    assert reviewer_handoff_summary_smoke["approval_granted"] is False
    assert (
        reviewer_handoff_summary_smoke["direct_bridge_write_performed"] is False
    )
    assert reviewer_handoff_summary_smoke["artifact_payloads_included"] is False
    assert reviewer_handoff_summary_smoke["local_paths_recorded"] is False
    assert reviewer_handoff_summary_smoke["network_access_performed"] is False
    reviewer_handoff_bundle_index_smoke = verifier_smoke[
        "reviewer_handoff_bundle_index_smoke"
    ]
    assert reviewer_handoff_bundle_index_smoke["ok"] is True
    assert reviewer_handoff_bundle_index_smoke["artifact_count"] == 2
    assert reviewer_handoff_bundle_index_smoke["source_contract_check"] == "match"
    assert reviewer_handoff_bundle_index_smoke["rebuilt_summary_check"] == "match"
    assert (
        reviewer_handoff_bundle_index_smoke["direct_bridge_write_performed"]
        is False
    )
    assert (
        reviewer_handoff_bundle_index_smoke["artifact_payloads_included"] is False
    )
    assert reviewer_handoff_bundle_index_smoke["local_paths_recorded"] is False
    assert reviewer_handoff_bundle_index_smoke["network_access_performed"] is False
    reviewer_handoff_bundle_index_verification_smoke = verifier_smoke[
        "reviewer_handoff_bundle_index_verification_smoke"
    ]
    assert reviewer_handoff_bundle_index_verification_smoke["ok"] is True
    assert (
        reviewer_handoff_bundle_index_verification_smoke["source_contract_check"]
        == "match"
    )
    assert (
        reviewer_handoff_bundle_index_verification_smoke[
            "rebuilt_bundle_index_check"
        ]
        == "match"
    )
    assert (
        reviewer_handoff_bundle_index_verification_smoke[
            "reviewer_handoff_summary_check"
        ]
        == "match"
    )
    assert (
        set(
            reviewer_handoff_bundle_index_verification_smoke[
                "digest_checks"
            ].values()
        )
        == {"match"}
    )
    assert (
        reviewer_handoff_bundle_index_verification_smoke[
            "direct_bridge_write_performed"
        ]
        is False
    )
    assert (
        reviewer_handoff_bundle_index_verification_smoke[
            "artifact_payloads_included"
        ]
        is False
    )
    assert (
        reviewer_handoff_bundle_index_verification_smoke["local_paths_recorded"]
        is False
    )
    assert (
        reviewer_handoff_bundle_index_verification_smoke[
            "network_access_performed"
        ]
        is False
    )
    reviewer_handoff_bundle_verifier_summary_template_index_entry_smoke = verifier_smoke[
        "reviewer_handoff_bundle_verifier_summary_bridge_event_template_index_entry_smoke"
    ]
    assert (
        reviewer_handoff_bundle_verifier_summary_template_index_entry_smoke["ok"]
        is True
    )
    assert (
        reviewer_handoff_bundle_verifier_summary_template_index_entry_smoke[
            "source_contract_check"
        ]
        == "match"
    )
    assert (
        reviewer_handoff_bundle_verifier_summary_template_index_entry_smoke[
            "rebuilt_template_check"
        ]
        == "match"
    )
    assert (
        reviewer_handoff_bundle_verifier_summary_template_index_entry_smoke[
            "direct_bridge_write_performed"
        ]
        is False
    )
    assert (
        reviewer_handoff_bundle_verifier_summary_template_index_entry_smoke[
            "artifact_payloads_included"
        ]
        is False
    )
    assert (
        reviewer_handoff_bundle_verifier_summary_template_index_entry_smoke[
            "local_paths_recorded"
        ]
        is False
    )
    assert (
        reviewer_handoff_bundle_verifier_summary_template_index_entry_smoke[
            "network_access_performed"
        ]
        is False
    )
    reviewer_handoff_bundle_verifier_summary_template_index_entry_verification_smoke = verifier_smoke[
        "reviewer_handoff_bundle_verifier_summary_bridge_event_template_index_entry_verification_smoke"
    ]
    assert (
        reviewer_handoff_bundle_verifier_summary_template_index_entry_verification_smoke[
            "ok"
        ]
        is True
    )
    assert (
        reviewer_handoff_bundle_verifier_summary_template_index_entry_verification_smoke[
            "source_contract_check"
        ]
        == "match"
    )
    assert (
        reviewer_handoff_bundle_verifier_summary_template_index_entry_verification_smoke[
            "rebuilt_index_entry_check"
        ]
        == "match"
    )
    assert (
        reviewer_handoff_bundle_verifier_summary_template_index_entry_verification_smoke[
            "bridge_event_schema_check"
        ]
        == "match"
    )
    assert (
        set(
            reviewer_handoff_bundle_verifier_summary_template_index_entry_verification_smoke[
                "digest_checks"
            ].values()
        )
        == {"match"}
    )
    assert (
        reviewer_handoff_bundle_verifier_summary_template_index_entry_verification_smoke[
            "direct_bridge_write_performed"
        ]
        is False
    )
    assert (
        reviewer_handoff_bundle_verifier_summary_template_index_entry_verification_smoke[
            "artifact_payloads_included"
        ]
        is False
    )
    assert (
        reviewer_handoff_bundle_verifier_summary_template_index_entry_verification_smoke[
            "local_paths_recorded"
        ]
        is False
    )
    assert (
        reviewer_handoff_bundle_verifier_summary_template_index_entry_verification_smoke[
            "network_access_performed"
        ]
        is False
    )
    reviewer_handoff_bundle_verifier_summary_template_index_entry_verification_summary_smoke = verifier_smoke[
        "reviewer_handoff_bundle_verifier_summary_bridge_event_template_index_entry_verification_summary_smoke"
    ]
    assert (
        reviewer_handoff_bundle_verifier_summary_template_index_entry_verification_summary_smoke[
            "ok"
        ]
        is True
    )
    assert (
        reviewer_handoff_bundle_verifier_summary_template_index_entry_verification_summary_smoke[
            "source_contract_check"
        ]
        == "match"
    )
    assert (
        reviewer_handoff_bundle_verifier_summary_template_index_entry_verification_summary_smoke[
            "rebuilt_index_entry_check"
        ]
        == "match"
    )
    assert (
        reviewer_handoff_bundle_verifier_summary_template_index_entry_verification_summary_smoke[
            "bridge_event_schema_check"
        ]
        == "match"
    )
    assert (
        reviewer_handoff_bundle_verifier_summary_template_index_entry_verification_summary_smoke[
            "direct_bridge_write_performed"
        ]
        is False
    )
    assert (
        reviewer_handoff_bundle_verifier_summary_template_index_entry_verification_summary_smoke[
            "artifact_payloads_included"
        ]
        is False
    )
    assert (
        reviewer_handoff_bundle_verifier_summary_template_index_entry_verification_summary_smoke[
            "local_paths_recorded"
        ]
        is False
    )
    assert (
        reviewer_handoff_bundle_verifier_summary_template_index_entry_verification_summary_smoke[
            "network_access_performed"
        ]
        is False
    )
    reviewer_handoff_bundle_verifier_summary_template_index_entry_verification_summary_bridge_event_template_smoke = verifier_smoke[
        "reviewer_handoff_bundle_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_smoke"
    ]
    assert (
        reviewer_handoff_bundle_verifier_summary_template_index_entry_verification_summary_bridge_event_template_smoke[
            "ok"
        ]
        is True
    )
    assert (
        reviewer_handoff_bundle_verifier_summary_template_index_entry_verification_summary_bridge_event_template_smoke[
            "template_only"
        ]
        is True
    )
    assert (
        reviewer_handoff_bundle_verifier_summary_template_index_entry_verification_summary_bridge_event_template_smoke[
            "manual_review_required"
        ]
        is True
    )
    assert (
        reviewer_handoff_bundle_verifier_summary_template_index_entry_verification_summary_bridge_event_template_smoke[
            "direct_bridge_write_performed"
        ]
        is False
    )
    assert (
        reviewer_handoff_bundle_verifier_summary_template_index_entry_verification_summary_bridge_event_template_smoke[
            "artifact_payloads_included"
        ]
        is False
    )
    assert (
        reviewer_handoff_bundle_verifier_summary_template_index_entry_verification_summary_bridge_event_template_smoke[
            "local_paths_recorded"
        ]
        is False
    )
    assert (
        reviewer_handoff_bundle_verifier_summary_template_index_entry_verification_summary_bridge_event_template_smoke[
            "network_access_performed"
        ]
        is False
    )
    assert "route-stage labels" in capability["safe_statement"]
    assert "route-stage operator metrics" in capability["safe_statement"]
    assert "runtime rate/latency counters" in capability["safe_statement"]
    assert "p95/p99" in capability["safe_statement"]
    assert "feed provider" in capability["safe_statement"]
    assert "private-host guardrails" in capability["safe_statement"]
    assert "operator SLO/drill evidence" in capability["safe_statement"]
    assert "offline" in capability["safe_statement"]
    assert "drill evidence verifier" in capability["safe_statement"]
    assert "verification-summary bridge-event template" in (
        capability["safe_statement"]
    )
    assert "local template index entry" in capability["safe_statement"]
    assert "verifier summary renderer" in capability["safe_statement"]
    assert "bridge-event renderer for the verifier summary" in (
        capability["safe_statement"]
    )
    assert "local index entry for that renderer" in capability["safe_statement"]
    assert "local verifier for that index entry" in capability["safe_statement"]
    assert "reviewer handoff summary" in capability["safe_statement"]
    assert "handoff bundle index" in capability["safe_statement"]
    assert "verifier-summary bridge-event template" in capability["safe_statement"]
    assert "verifier-summary renderer for that template index entry" in (
        capability["safe_statement"]
    )
    assert "handoff bundle" in capability["next_smallest_pr"]
    assert "verifier-summary bridge-event template index entry" in (
        capability["next_smallest_pr"]
    )
    assert "local verifier" in capability["next_smallest_pr"]
    assert "without including payloads" in capability["next_smallest_pr"]
    assert report["summary"]["proofs_ok"] is True


def test_manifest_embeds_solver_trace_proof_without_upgrading_claim() -> None:
    report = build_manifest(ROOT)
    capability = _by_id(report)["deterministic_solver_first"]

    assert capability["status"] == "partial"
    assert capability["claim_safe"] is False
    assert capability["proof"]["ok"] is True
    assert capability["proof"]["selected_solver_ids"] == ["solve.math"]
    assert capability["proof"]["magma_execution_receipt_claimed"] is True
    assert "opt-in MAGMA" in capability["safe_statement"]
    assert report["summary"]["proofs_ok"] is True


def test_manifest_embeds_magma_receipt_proof_without_upgrading_claim() -> None:
    report = build_manifest(ROOT)
    capability = _by_id(report)["magma_audit_log"]

    assert capability["status"] == "partial"
    assert capability["claim_safe"] is False
    assert capability["proof"]["ok"] is True
    assert capability["proof"]["solver_call_trace_receipt_bound"] is True
    assert "operator-gated local exporter" in capability["safe_statement"]
    assert "no-authority importer" in capability["safe_statement"]
    assert "provider health" in capability["safe_statement"]
    assert "freshness/retention thresholds" in capability["safe_statement"]
    assert "operator-owned feed freshness source" in capability["safe_statement"]
    assert "privacy-safe /metrics gauges" in capability["safe_statement"]
    assert "metrics runbook" in capability["safe_statement"]
    assert "metrics_alert_state" in capability["safe_statement"]
    assert "configured adapter" in capability["safe_statement"]
    assert "bounded failure-backoff guardrails" in capability["safe_statement"]
    assert "SLO panels" in capability["safe_statement"]
    assert "drill-evidence" in capability["safe_statement"]
    assert "manual release-gate examples" in capability["safe_statement"]
    assert "evidence package tool" in capability["safe_statement"]
    assert "companion validator" in capability["safe_statement"]
    assert "verification summary renderer" in capability["safe_statement"]
    assert "operator decision-reference validator" in capability["safe_statement"]
    assert "decision-reference review summary renderer" in capability["safe_statement"]
    assert "decision-reference review bundle index" in capability["safe_statement"]
    assert "decision-reference review bundle verifier" in (capability["safe_statement"])
    assert "decision-reference review bundle verification summary" in (
        capability["safe_statement"]
    )
    assert "decision-reference review bundle verification bridge-event template" in (
        capability["safe_statement"]
    )
    assert capability["proof"]["provider_metrics_runbook_smoke"]["ok"] is True
    assert (
        capability["proof"]["provider_metrics_runbook_smoke"][
            "alert_thresholds_documented"
        ]
        is True
    )
    assert (
        capability["proof"]["provider_metrics_runbook_smoke"][
            "forbidden_controls_absent"
        ]
        is True
    )
    assert capability["proof"]["metrics_alert_state_smoke"]["ok"] is True
    assert (
        capability["proof"]["metrics_alert_state_smoke"]["fixed_alert_ids_enforced"]
        is True
    )
    assert capability["proof"]["metrics_alertmanager_adapter_smoke"]["ok"] is True
    evidence_paths = {entry["path"] for entry in capability["evidence"]}
    assert (
        "tools/build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary.py"
        in evidence_paths
    )
    assert (
        "tests/tools/test_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary.py"
        in evidence_paths
    )
    assert (
        "tools/build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template.py"
        in evidence_paths
    )
    assert (
        "tests/tools/test_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template.py"
        in evidence_paths
    )
    assert (
        capability["proof"]["metrics_alertmanager_adapter_smoke"][
            "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verifier_contract_present"
        ]
        is True
    )
    assert (
        capability["proof"]["metrics_alertmanager_adapter_smoke"][
            "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_summary_contract_present"
        ]
        is True
    )
    assert (
        capability["proof"]["metrics_alertmanager_adapter_smoke"][
            "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_contract_present"
        ]
        is True
    )
    assert (
        capability["proof"]["metrics_alertmanager_adapter_smoke"][
            "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_contract_present"
        ]
        is True
    )
    assert (
        capability["proof"]["metrics_alertmanager_adapter_smoke"][
            "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verifier_contract_present"
        ]
        is True
    )
    assert (
        capability["proof"]["metrics_alertmanager_adapter_smoke"][
            "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_contract_present"
        ]
        is True
    )
    assert (
        capability["proof"]["metrics_alertmanager_adapter_smoke"][
            "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_contract_present"
        ]
        is True
    )
    assert (
        capability["proof"]["metrics_alertmanager_adapter_smoke"][
            "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_contract_present"
        ]
        is True
    )
    assert (
        capability["proof"]["metrics_alertmanager_adapter_smoke"][
            "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verifier_contract_present"
        ]
        is True
    )
    assert (
        capability["proof"]["metrics_alertmanager_adapter_smoke"][
            "reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_contract_present"
        ]
        is True
    )
    assert (
        capability["proof"]["metrics_alertmanager_adapter_smoke"]["guardrails_present"]
        is True
    )
    assert "hard append-only" in capability["safe_statement"]
    assert "local index entry" in capability["next_smallest_pr"]
    assert "bridge-event template" in capability["next_smallest_pr"]
    assert "verifier-summary" in capability["next_smallest_pr"]
    assert "bridge-event template renderer" not in capability["next_smallest_pr"]
    assert report["summary"]["proofs_ok"] is True


def test_future_scale_axis_scorecard_gates_unbounded_claims() -> None:
    proof = build_future_scale_axis_scorecard(ROOT)

    assert proof["ok"] is True
    assert proof["proof_id"] == "future_scale_axis_scorecard_v1"
    assert proof["literal_future_claim_safe"] is False
    assert proof["unbounded_claims_rejected"] is True
    assert proof["axis_count"] == 8
    assert proof["defined_axis_count"] == 8
    assert proof["all_axis_proxies_named"] is True
    assert proof["eig_disabled_by_default"] is True
    assert proof["eig_benchmark_only"] is True
    assert proof["scorecard_doc_present"] is True
    summary = proof["runtime_evidence_summary"]
    assert summary["route_stage_runtime_metrics_smoke_ok"] is True
    assert summary["route_stage_runtime_contract_ok"] is True
    assert summary["feed_health_drill_evidence_verifier_smoke_ok"] is True
    assert summary["solver_trace_receipt_proof_ok"] is True
    assert summary["required_runtime_evidence_present"] is False
    assert summary["runtime_evidence_axis_count"] == 8
    assert summary["unmeasured_axis_count"] == 0
    assert summary["unmeasured_axes"] == []
    assert "route_depth" in summary["required_runtime_axes"]
    window_summary = proof["benchmark_window_summary"]
    assert window_summary["ok"] is True
    assert (
        window_summary["proof_id"]
        == "future_scale_repeated_benchmark_window_summary_v1"
    )
    assert window_summary["status"] == "benchmark_windows_available"
    assert window_summary["window_count"] == 2
    assert window_summary["axis_count"] == 4
    assert window_summary["record_count"] == 8
    assert window_summary["ok_record_count"] == 8
    assert window_summary["axis_ids"] == [
        "route_depth",
        "useful_composite_paths",
        "contradiction_rate",
        "insight_score",
    ]
    assert window_summary["axes_with_repeated_windows"] == window_summary["axis_ids"]
    assert window_summary["stable_sample_axes"] == window_summary["axis_ids"]
    assert window_summary["all_samples_stable_across_windows"] is True
    assert window_summary["claim_gate_satisfied"] is False
    assert window_summary["required_runtime_evidence_present"] is False
    assert window_summary["literal_future_claim_safe"] is False
    assert window_summary["runtime_authority_changed"] is False
    assert window_summary["operator_gate_required"] is False
    assert window_summary["external_writes_applied"] is False
    assert all(
        len(digests) == 1
        for digests in window_summary["sample_digests_by_axis"].values()
    )
    assert all(
        record["claim_gate_satisfied"] is False
        and record["required_runtime_evidence_present"] is False
        and record["literal_future_claim_safe"] is False
        and record["runtime_authority_changed"] is False
        and record["external_writes_applied"] is False
        for record in window_summary["records"]
    )
    assert proof["runtime_authority_changed"] is False
    assert proof["operator_gate_required"] is False
    assert proof["external_writes_applied"] is False
    axes = {item["axis_id"]: item for item in proof["axes"]}
    assert set(axes) == {
        "coverage",
        "llm_fallback_rate",
        "route_depth",
        "useful_composite_paths",
        "contradiction_rate",
        "insight_score",
        "latency",
        "audit_completeness",
    }
    assert axes["coverage"]["runtime_evidence"]["status"] == "runtime_proxy_defined"
    assert (
        axes["llm_fallback_rate"]["runtime_evidence"]["sample"][
            "fallback_stage_observed_in_smoke"
        ]
        is True
    )
    assert axes["latency"]["runtime_evidence"]["status"] == "runtime_metric_defined"
    assert (
        "waggledance_route_stage_request_latency_histogram_ms_bucket"
        in axes["latency"]["runtime_evidence"]["metric_names"]
    )
    assert (
        axes["audit_completeness"]["runtime_evidence"]["status"]
        == "contract_proof_available"
    )
    assert (
        axes["audit_completeness"]["runtime_evidence"]["sample"][
            "solver_call_trace_receipt_bound"
        ]
        is True
    )
    assert (
        axes["route_depth"]["runtime_evidence"]["status"]
        == "benchmark_histogram_capture_attachment_and_summary_contract_available"
    )
    assert axes["route_depth"]["runtime_evidence"]["metric_names"] == []
    assert axes["route_depth"]["runtime_evidence"]["sample"]["sample_count"] == 5
    assert axes["route_depth"]["runtime_evidence"]["sample"]["p50_depth"] == 6.0
    assert axes["route_depth"]["runtime_evidence"]["sample"]["p95_depth"] == 7.8
    assert axes["route_depth"]["runtime_evidence"]["sample"]["p99_depth"] == 7.96
    assert (
        axes["route_depth"]["runtime_evidence"]["sample"][
            "production_histogram_artifact_status"
        ]
        == "production_histogram_artifact_contract_available"
    )
    assert (
        "waggledance_route_depth_histogram_bucket"
        in axes["route_depth"]["runtime_evidence"]["sample"][
            "production_histogram_metric_names"
        ]
    )
    assert axes["route_depth"]["runtime_evidence"]["sample"][
        "production_histogram_label_names"
    ] == ["route_profile", "final_stage", "le"]
    assert (
        axes["route_depth"]["runtime_evidence"]["sample"][
            "production_histogram_sample_count"
        ]
        == 5
    )
    assert (
        axes["route_depth"]["runtime_evidence"]["sample"][
            "production_histogram_route_profile_count"
        ]
        == 5
    )
    assert (
        axes["route_depth"]["runtime_evidence"]["sample"][
            "production_histogram_runtime_data_attached"
        ]
        is False
    )
    assert (
        len(
            axes["route_depth"]["runtime_evidence"]["sample"][
                "production_histogram_digest_sha256"
            ]
        )
        == 64
    )
    assert (
        axes["route_depth"]["runtime_evidence"]["sample"][
            "production_capture_window_attachment_status"
        ]
        == "capture_window_attachment_contract_available"
    )
    assert (
        axes["route_depth"]["runtime_evidence"]["sample"][
            "production_capture_window_runtime_data_attached"
        ]
        is False
    )
    assert (
        axes["route_depth"]["runtime_evidence"]["sample"][
            "production_capture_window_count"
        ]
        == 0
    )
    assert (
        axes["route_depth"]["runtime_evidence"]["sample"][
            "production_capture_window_required_runtime"
        ]
        is False
    )
    assert (
        len(
            axes["route_depth"]["runtime_evidence"]["sample"][
                "production_capture_window_digest_sha256"
            ]
        )
        == 64
    )
    assert (
        axes["route_depth"]["runtime_evidence"]["sample"][
            "production_capture_window_summary_schema_version"
        ]
        == "future_scale_route_depth_capture_window_verification_summary.v1"
    )
    assert (
        axes["route_depth"]["runtime_evidence"]["sample"][
            "production_capture_window_summary_status"
        ]
        == "operator_capture_window_verification_summary_blocked"
    )
    assert (
        axes["route_depth"]["runtime_evidence"]["sample"][
            "production_capture_window_summary_ok"
        ]
        is False
    )
    assert (
        "capture_window_count_insufficient"
        in axes["route_depth"]["runtime_evidence"]["sample"][
            "production_capture_window_summary_blockers"
        ]
    )
    assert (
        "needs operator-owned live production route-depth exports run through the capture-window verifier"
        in axes["route_depth"]["runtime_evidence"]["blockers"]
    )
    assert (
        "needs path-free capture-window verification summaries for attached operator-owned exports"
        in axes["route_depth"]["runtime_evidence"]["blockers"]
    )
    assert (
        axes["useful_composite_paths"]["runtime_evidence"]["status"]
        == "benchmark_contract_available"
    )
    assert (
        axes["useful_composite_paths"]["runtime_evidence"]["sample"][
            "useful_composite_paths_total"
        ]
        > 0
    )
    assert (
        axes["contradiction_rate"]["runtime_evidence"]["status"]
        == "benchmark_contract_available"
    )
    assert (
        axes["contradiction_rate"]["runtime_evidence"]["sample"]["contradiction_rate"]
        == 0.333333
    )
    assert (
        axes["contradiction_rate"]["runtime_evidence"]["sample"]["false_positive_count"]
        == 0
    )
    assert (
        axes["contradiction_rate"]["runtime_evidence"]["sample"]["false_negative_count"]
        == 0
    )
    assert (
        axes["insight_score"]["runtime_evidence"]["status"]
        == "benchmark_contract_available"
    )
    assert (
        axes["insight_score"]["runtime_evidence"]["sample"]["producer_harness_present"]
        is True
    )
    assert (
        axes["insight_score"]["runtime_evidence"]["sample"]["schema_version"]
        == "insight_score_benchmark.v1"
    )
    assert (
        axes["insight_score"]["runtime_evidence"]["sample"]["corpus_case_count"] == 12
    )
    assert (
        axes["insight_score"]["runtime_evidence"]["sample"]["mean_insight_score"]
        == 0.166667
    )
    assert (
        axes["insight_score"]["runtime_evidence"]["sample"]["controls_measured"] is True
    )
    assert all(
        item["runtime_evidence"]["claim_gate_satisfied"] is False
        for item in axes.values()
    )
    assert all(
        item["literal_claim_safe"] is False for item in proof["claim_decomposition"]
    )


def test_future_scale_axis_scorecard_blocks_foreign_root(
    tmp_path: Path,
) -> None:
    for rel_path in (
        "docs/architecture/explosive_intelligence_growth_2.md",
        "docs/architecture/HONEYCOMB_SOLVER_SCALING.md",
        "docs/architecture/WD_IMAGE1_FUNCTIONALITY_MANIFEST.md",
    ):
        path = tmp_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# placeholder\n", encoding="utf-8")

    proof = build_future_scale_axis_scorecard(tmp_path)

    assert proof["ok"] is False
    assert proof["blocked_reason"] == "non_current_import_root"
    assert proof["missing_inputs"] == []
    assert proof["axes"] == []
    assert proof["claim_decomposition"] == []


def test_future_scale_runtime_evidence_rejects_nested_type_confusion() -> None:
    route_stage_smoke = {
        "ok": True,
        "runtime_contract": {
            "ok": "true",
            "sanitized_trace": [{"stage": "orchestrator_llm_fallback"}],
        },
        "drill_evidence_verifier_smoke": {"ok": 1},
        "histogram_quantile_supported": True,
        "latency_panel_templates_visible": True,
    }
    solver_receipt_proof = {
        "ok": True,
        "solver_call_trace_count": "1",
        "solver_call_trace_receipt_bound": "yes",
    }

    evidence_by_axis, summary = _build_future_scale_runtime_evidence(
        route_stage_smoke,
        solver_receipt_proof,
    )

    assert summary["route_stage_runtime_metrics_smoke_ok"] is True
    assert summary["route_stage_runtime_contract_ok"] is False
    assert summary["feed_health_drill_evidence_verifier_smoke_ok"] is False
    assert summary["solver_trace_receipt_proof_ok"] is True
    assert summary["required_runtime_evidence_present"] is False
    assert evidence_by_axis["coverage"]["status"] == "runtime_contract_unavailable"
    assert evidence_by_axis["latency"]["status"] == "runtime_contract_unavailable"
    assert (
        evidence_by_axis["route_depth"]["status"]
        == "benchmark_histogram_capture_attachment_and_summary_contract_available"
    )
    assert (
        "route-stage runtime metrics smoke failed"
        not in evidence_by_axis["route_depth"]["blockers"]
    )
    assert (
        evidence_by_axis["audit_completeness"]["status"]
        == "drill_evidence_contract_unavailable"
    )
    assert all(
        item["claim_gate_satisfied"] is False for item in evidence_by_axis.values()
    )


def test_manifest_embeds_future_scorecard_without_upgrading_claim() -> None:
    report = build_manifest(ROOT)
    capability = _by_id(report)["future_waggledance_swarm"]

    assert capability["status"] == "partial"
    assert capability["claim_safe"] is False
    assert capability["proof"]["ok"] is True
    assert capability["proof"]["literal_future_claim_safe"] is False
    assert capability["proof"]["unbounded_claims_rejected"] is True
    assert capability["proof"]["axis_count"] == 8
    assert (
        capability["proof"]["runtime_evidence_summary"][
            "required_runtime_evidence_present"
        ]
        is False
    )
    assert capability["proof"]["benchmark_window_summary"]["ok"] is True
    assert "benchmark-window evidence" in capability["safe_statement"]
    assert "operator-owned live route-depth export" in (capability["next_smallest_pr"])
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


# --- per-query receipt coverage aggregate (option A: default-off/on-demand) ---
def test_per_query_coverage_flag_off_returns_none() -> None:
    # Default OFF: no env flag -> no proof run -> None (manifest byte-unaffected).
    prior = os.environ.pop(PER_QUERY_RECEIPT_COVERAGE_ENV, None)
    try:
        assert build_per_query_receipt_coverage_aggregate() is None
    finally:
        if prior is not None:
            os.environ[PER_QUERY_RECEIPT_COVERAGE_ENV] = prior


def test_per_query_coverage_force_real_aggregate_is_safe() -> None:
    # On-demand (force) runs the REAL proof and aggregates only safe scalars.
    aggregate = build_per_query_receipt_coverage_aggregate(force=True)
    assert set(aggregate) == set(_PER_QUERY_RECEIPT_COVERAGE_SAFE_KEYS)
    assert aggregate["ok"] is True
    assert aggregate["raw_payload_leak_check"] is True
    ratio = aggregate["receipt_coverage_ratio"]
    assert isinstance(ratio, float) and 0.0 <= ratio <= 1.0
    # Forge 7: prove (do not trust the comment) that no private/raw content rode
    # along into the aggregate that lands in the manifest.
    blob = json.dumps(aggregate)
    for marker in ("query_reports", "operator_note", "DO_NOT_LEAK", "context secret"):
        assert marker not in blob


def test_safe_aggregate_drops_unsafe_keys() -> None:
    # Even if the proof report carries query_reports / raw context / markers, the
    # aggregate copies ONLY the allowlisted scalar keys.
    poisoned_report = {
        "ok": True,
        "receipt_coverage_ratio": 1.0,
        "all_queries_receipt_bound": True,
        "raw_payload_leak_check": True,
        "authority_boundary": {
            "default_sink_required": False,
            "default_runtime_receipt_emission_changed": False,
        },
        "query_reports": [{"query": "secret query", "note": "operator_note"}],
        "query_ids": ["q1"],
        "report_path": "/tmp/x",
    }
    aggregate = _safe_per_query_receipt_coverage_aggregate(poisoned_report)
    assert set(aggregate) == set(_PER_QUERY_RECEIPT_COVERAGE_SAFE_KEYS)
    assert "query_reports" not in aggregate
    assert "query_ids" not in aggregate


def _good_coverage_report() -> dict:
    return {
        "ok": True,
        "receipt_coverage_ratio": 1.0,
        "all_queries_receipt_bound": True,
        "raw_payload_leak_check": True,
        "authority_boundary": {
            "default_sink_required": False,
            "default_runtime_receipt_emission_changed": False,
        },
    }


@pytest.mark.parametrize("bad_ratio", [
    "how much honey 4521kg SECRETZ_raw_query DO_NOT_LEAK",  # raw query + bare marker
    "1.0 operator_note DO_NOT_LEAK",                        # marker phrase
    "DO_NOT_LEAK",                                          # bare upstream sentinel
    "1.0",                                                  # plain numeric string
    float("nan"),                                           # NaN
    float("inf"),                                           # +Inf
    float("-inf"),                                          # -Inf
    1.5,                                                    # out of range high
    -0.1,                                                   # out of range low
    True,                                                   # bool type confusion
    None,                                                   # missing
])
def test_safe_aggregate_rejects_bad_ratio(bad_ratio) -> None:
    # The ratio is shape-constrained to a finite number in [0,1]; anything else
    # (raw string, marker, NaN/Inf, out-of-range, bool, missing) fails closed so
    # no raw content can ride in via the only non-boolean field.
    report = _good_coverage_report()
    report["receipt_coverage_ratio"] = bad_ratio
    with pytest.raises(ValueError):
        _safe_per_query_receipt_coverage_aggregate(report)


@pytest.mark.parametrize("field,bad", [
    ("ok", "true"),
    ("all_queries_receipt_bound", 1),
    ("raw_payload_leak_check", "DO_NOT_LEAK"),
])
def test_safe_aggregate_rejects_non_bool_top_field(field, bad) -> None:
    report = _good_coverage_report()
    report[field] = bad
    with pytest.raises(ValueError):
        _safe_per_query_receipt_coverage_aggregate(report)


@pytest.mark.parametrize("field,bad", [
    ("default_sink_required", "False"),
    ("default_runtime_receipt_emission_changed", 0),
])
def test_safe_aggregate_rejects_non_bool_authority_field(field, bad) -> None:
    report = _good_coverage_report()
    report["authority_boundary"][field] = bad
    with pytest.raises(ValueError):
        _safe_per_query_receipt_coverage_aggregate(report)


def test_safe_aggregate_accepts_clean_report() -> None:
    aggregate = _safe_per_query_receipt_coverage_aggregate(_good_coverage_report())
    assert set(aggregate) == set(_PER_QUERY_RECEIPT_COVERAGE_SAFE_KEYS)
    assert aggregate["receipt_coverage_ratio"] == 1.0
    assert all(
        isinstance(aggregate[k], bool)
        for k in _PER_QUERY_RECEIPT_COVERAGE_SAFE_KEYS
        if k != "receipt_coverage_ratio"
    )


def test_flag_off_manifest_omits_coverage_key() -> None:
    # Byte-unaffected invariant: with the flag off the magma proof has no
    # per_query_receipt_coverage key at all.
    prior = os.environ.pop(PER_QUERY_RECEIPT_COVERAGE_ENV, None)
    try:
        manifest = build_manifest()
    finally:
        if prior is not None:
            os.environ[PER_QUERY_RECEIPT_COVERAGE_ENV] = prior
    magma = next(
        c for c in manifest["capabilities"]
        if c["capability_id"] == "magma_audit_log"
    )
    assert "per_query_receipt_coverage" not in magma["proof"]


# --- authoritative first-hop coverage aggregate (default-off/on-demand) ---
def test_first_hop_coverage_flag_off_returns_none() -> None:
    prior = os.environ.pop(FIRST_HOP_COVERAGE_ENV, None)
    try:
        assert build_first_hop_coverage_aggregate() is None
    finally:
        if prior is not None:
            os.environ[FIRST_HOP_COVERAGE_ENV] = prior


def test_first_hop_coverage_force_real_aggregate_is_safe() -> None:
    aggregate = build_first_hop_coverage_aggregate(force=True)
    assert set(aggregate) == set(_FIRST_HOP_COVERAGE_SAFE_KEYS)
    assert aggregate["coverage_measurement_available"] is True
    assert aggregate["capsule_declares_authoritative_order"] is True
    assert aggregate["measurement_basis"] == "v1_first_hop_authoritative_order"
    ratio = aggregate["authoritative_first_hop_coverage"]
    assert ratio is None or (isinstance(ratio, float) and 0.0 <= ratio <= 1.0)
    blob = json.dumps(aggregate)
    for marker in ("first_hop_records", "per_route_summary", "expected", "predicted"):
        assert marker not in blob


def _good_first_hop_report() -> dict:
    return {
        "coverage_measurement_available": True,
        "authoritative_first_hop_coverage": 0.6667,
        "measurement_basis": "v1_first_hop_authoritative_order",
        "corpus_size": 30,
        "hot_cache_count": 0,
        "routable_size": 30,
        "authoritative_first_hop_count": 20,
        "heuristic_first_hop_count": 10,
        "invariants": {"capsule_declares_authoritative_order": True},
    }


def test_first_hop_aggregate_accepts_none_coverage_when_unavailable() -> None:
    # capsule with no declared order: coverage None + unavailable is valid.
    report = {**_good_first_hop_report(),
              "coverage_measurement_available": False,
              "authoritative_first_hop_coverage": None,
              "invariants": {"capsule_declares_authoritative_order": False}}
    aggregate = _safe_first_hop_coverage_aggregate(report)
    assert aggregate["authoritative_first_hop_coverage"] is None
    assert aggregate["coverage_measurement_available"] is False
    assert aggregate["capsule_declares_authoritative_order"] is False


@pytest.mark.parametrize("bad", [1.5, -0.1, float("nan"), float("inf"), "0.5", True])
def test_first_hop_aggregate_rejects_bad_coverage(bad) -> None:
    with pytest.raises(ValueError):
        _safe_first_hop_coverage_aggregate(
            {**_good_first_hop_report(), "authoritative_first_hop_coverage": bad}
        )


@pytest.mark.parametrize("field,bad", [
    ("coverage_measurement_available", "true"),
    ("corpus_size", -1),
    ("authoritative_first_hop_count", 1.5),
    ("measurement_basis", "spoofed_basis"),
])
def test_first_hop_aggregate_rejects_bad_scalar(field, bad) -> None:
    with pytest.raises(ValueError):
        _safe_first_hop_coverage_aggregate({**_good_first_hop_report(), field: bad})


def test_first_hop_aggregate_rejects_non_bool_declares_order() -> None:
    with pytest.raises(ValueError):
        _safe_first_hop_coverage_aggregate(
            {**_good_first_hop_report(),
             "invariants": {"capsule_declares_authoritative_order": 1}}
        )


def test_flag_off_manifest_omits_first_hop_key() -> None:
    prior = os.environ.pop(FIRST_HOP_COVERAGE_ENV, None)
    try:
        manifest = build_manifest()
    finally:
        if prior is not None:
            os.environ[FIRST_HOP_COVERAGE_ENV] = prior
    hex_mesh = next(
        c for c in manifest["capabilities"] if c["capability_id"] == "hex_mesh_entry"
    )
    assert "first_hop_coverage" not in hex_mesh["proof"]


# --- repeat-window trend aggregate (default-on measurement-only evidence) ---
def test_repeat_window_trend_default_on_returns_aggregate() -> None:
    # Default-on: with the env flag unset the proof runs and yields a safe aggregate.
    prior = os.environ.pop(REPEAT_WINDOW_TREND_ENV, None)
    try:
        aggregate = build_repeat_window_trend_aggregate()
    finally:
        if prior is not None:
            os.environ[REPEAT_WINDOW_TREND_ENV] = prior
    assert aggregate is not None
    assert set(aggregate) == set(_REPEAT_WINDOW_TREND_SAFE_KEYS)
    assert aggregate["ok"] is True
    assert aggregate["measurement_basis"] == "v1_low_risk_real_loop_repeat_window"


@pytest.mark.parametrize("token", ["0", "false", "off", "no", "FALSE", "Off"])
def test_repeat_window_trend_opt_out_returns_none(token) -> None:
    # An explicit falsey env value opts OUT (returns None) for fast/byte-minimal builds.
    prior = os.environ.get(REPEAT_WINDOW_TREND_ENV)
    os.environ[REPEAT_WINDOW_TREND_ENV] = token
    try:
        assert build_repeat_window_trend_aggregate() is None
    finally:
        if prior is None:
            os.environ.pop(REPEAT_WINDOW_TREND_ENV, None)
        else:
            os.environ[REPEAT_WINDOW_TREND_ENV] = prior


def test_repeat_window_trend_force_real_aggregate_is_safe() -> None:
    aggregate = build_repeat_window_trend_aggregate(force=True)
    assert set(aggregate) == set(_REPEAT_WINDOW_TREND_SAFE_KEYS)
    assert aggregate["ok"] is True
    assert aggregate["deterministic"] is True
    assert aggregate["evidence_present"] is True
    assert aggregate["measurement_basis"] == "v1_low_risk_real_loop_repeat_window"
    assert isinstance(aggregate["window_size"], int) and aggregate["window_size"] >= 2
    blob = json.dumps(aggregate)
    for marker in ("trend", "claim_label", "report_path", "autogrowth_run_id"):
        # only the allowlisted scalar keys; no nested proof structures / ids
        assert marker not in set(aggregate)


def _good_trend_report() -> dict:
    return {
        "ok": True,
        "measurement_basis": "v1_low_risk_real_loop_repeat_window",
        "deterministic_replay": {"stable_trend_identical": True},
        "evidence_vs_authority": {
            "evidence_present": True,
            "runtime_authority_granted": False,
            "external_writes_applied": False,
        },
        "trend": {
            "window_size": 3,
            "all_runs_ok": True,
            "any_guardrail_tripped": False,
            "promoted_solver_count_min": 1,
            "promoted_solver_count_max": 1,
            "promoted_solver_count_stable": True,
        },
    }


def test_repeat_window_trend_aggregate_accepts_clean_report() -> None:
    aggregate = _safe_repeat_window_trend_aggregate(_good_trend_report())
    assert set(aggregate) == set(_REPEAT_WINDOW_TREND_SAFE_KEYS)
    assert aggregate["window_size"] == 3


def test_repeat_window_trend_rejects_bad_basis() -> None:
    bad = _good_trend_report()
    bad["measurement_basis"] = "spoofed"
    with pytest.raises(ValueError):
        _safe_repeat_window_trend_aggregate(bad)


@pytest.mark.parametrize("path,bad", [
    (("evidence_vs_authority", "evidence_present"), "true"),
    (("trend", "all_runs_ok"), 1),
    (("trend", "promoted_solver_count_stable"), "yes"),
])
def test_repeat_window_trend_rejects_non_bool(path, bad) -> None:
    report = _good_trend_report()
    report[path[0]][path[1]] = bad
    with pytest.raises(ValueError):
        _safe_repeat_window_trend_aggregate(report)


@pytest.mark.parametrize("field,bad", [
    ("window_size", -1),
    ("promoted_solver_count_min", 1.5),
    ("promoted_solver_count_max", True),
])
def test_repeat_window_trend_rejects_bad_count(field, bad) -> None:
    report = _good_trend_report()
    report["trend"][field] = bad
    with pytest.raises(ValueError):
        _safe_repeat_window_trend_aggregate(report)


@pytest.mark.parametrize("bad_window", [1, 26, 1_000_000])
def test_repeat_window_trend_rejects_window_out_of_range(bad_window) -> None:
    # window_size must be in [2, MAX_WINDOW]; a forged tiny/huge window fails closed.
    report = _good_trend_report()
    report["trend"]["window_size"] = bad_window
    with pytest.raises(ValueError):
        _safe_repeat_window_trend_aggregate(report)


def test_default_manifest_includes_repeat_window_trend_key() -> None:
    # Default-on: build_manifest ships the measurement-only trend as default evidence
    # under the low-risk capability proof.
    prior = os.environ.pop(REPEAT_WINDOW_TREND_ENV, None)
    try:
        manifest = build_manifest()
    finally:
        if prior is not None:
            os.environ[REPEAT_WINDOW_TREND_ENV] = prior
    low_risk = next(
        c for c in manifest["capabilities"]
        if c["capability_id"] == "low_risk_autonomy_loop"
    )
    assert "repeat_window_trend" in low_risk["proof"]
    trend = low_risk["proof"]["repeat_window_trend"]
    assert set(trend) == set(_REPEAT_WINDOW_TREND_SAFE_KEYS)
    assert trend["ok"] is True
    assert trend["measurement_basis"] == "v1_low_risk_real_loop_repeat_window"
    # measurement-only: no authority granted, no writes (prefixed so the consumer's
    # root authority scan never picks them up).
    assert trend["trend_runtime_authority_granted"] is False
    assert trend["trend_external_writes_applied"] is False


def test_opt_out_manifest_omits_repeat_window_trend_key() -> None:
    prior = os.environ.get(REPEAT_WINDOW_TREND_ENV)
    os.environ[REPEAT_WINDOW_TREND_ENV] = "0"
    try:
        manifest = build_manifest()
    finally:
        if prior is None:
            os.environ.pop(REPEAT_WINDOW_TREND_ENV, None)
        else:
            os.environ[REPEAT_WINDOW_TREND_ENV] = prior
    low_risk = next(
        c for c in manifest["capabilities"]
        if c["capability_id"] == "low_risk_autonomy_loop"
    )
    assert "repeat_window_trend" not in low_risk["proof"]


# --- #1273 hex reviewer-summary wired into the manifest hex_upgrade proof ---
def test_manifest_stores_hex_reviewer_summary_content_safe() -> None:
    manifest = build_manifest()
    hex_cap = next(
        c for c in manifest["capabilities"]
        if c["capability_id"] == "hexagonal_upgrades"
    )
    reviewer = hex_cap["proof"].get("reviewer_summary")
    assert isinstance(reviewer, dict)
    # content-safe by construction: only the version string + derived bools/ints
    for key, value in reviewer.items():
        if key == "report_version":
            assert isinstance(value, str)
        else:
            assert isinstance(value, (bool, int)), key
    assert reviewer["path_free_verified"] is True
    assert reviewer["verdict_ok"] is True
    # no raw repo path leaked into the stored summary
    assert str(ROOT) not in json.dumps(reviewer)


def test_manifest_hex_reviewer_summary_not_folded_into_ok() -> None:
    # The reviewer summary is measurement-only: hex_upgrade proof ok must not
    # depend on it. Verified structurally - the proof is ok while the summary is
    # present, and the ok computation does not reference reviewer_summary.
    import inspect
    from tools import wd_image1_capability_manifest as mod

    src = inspect.getsource(mod._capabilities)
    ok_assign = src.split('hex_upgrade_proof["ok"] = bool(', 1)[1].split(")", 1)[0]
    assert "reviewer_summary" not in ok_assign


def test_manifest_stores_chain_final_summary_content_safe() -> None:
    manifest = build_manifest()
    hex_cap = next(
        c for c in manifest["capabilities"]
        if c["capability_id"] == "hexagonal_upgrades"
    )
    chain = hex_cap["proof"].get("chain_final_summary")
    assert isinstance(chain, dict)
    # content-safe by construction: only the version string + derived bools/ints
    for key, value in chain.items():
        if key == "report_version":
            assert isinstance(value, str)
        else:
            assert isinstance(value, (bool, int)), key
    assert chain["chain_clean"] is True
    assert chain["path_free_verified"] is True
    assert chain["chain_levels_present"] == chain["chain_levels_total"]
    assert chain["chain_levels_total"] >= 1
    # no raw repo path leaked into the stored summary
    assert str(ROOT) not in json.dumps(chain)


def test_manifest_chain_final_summary_not_folded_into_ok() -> None:
    # The chain final summary is measurement-only: hex_upgrade proof ok must not
    # depend on it (the ok computation does not reference chain_final_summary).
    import inspect
    from tools import wd_image1_capability_manifest as mod

    src = inspect.getsource(mod._capabilities)
    ok_assign = src.split('hex_upgrade_proof["ok"] = bool(', 1)[1].split(")", 1)[0]
    assert "chain_final_summary" not in ok_assign


def test_manifest_stores_cross_consistency_digest_content_safe() -> None:
    manifest = build_manifest()
    hex_cap = next(
        c for c in manifest["capabilities"]
        if c["capability_id"] == "hexagonal_upgrades"
    )
    digest = hex_cap["proof"].get("cross_consistency_digest")
    assert isinstance(digest, dict)
    # content-safe by construction: only the version string + derived booleans
    for key, value in digest.items():
        if key == "report_version":
            assert isinstance(value, str)
        else:
            assert isinstance(value, bool), key
    assert digest["cross_consistent"] is True
    assert digest["all_views_present"] is True
    assert digest["path_free_verified"] is True
    assert digest["claim_safe"] is False
    # no raw repo path leaked into the stored digest
    assert str(ROOT) not in json.dumps(digest)


def test_manifest_cross_consistency_digest_not_folded_into_ok() -> None:
    # The cross-consistency digest is measurement-only: hex_upgrade proof ok must not
    # depend on it (the ok computation does not reference cross_consistency_digest).
    import inspect
    from tools import wd_image1_capability_manifest as mod

    src = inspect.getsource(mod._capabilities)
    ok_assign = src.split('hex_upgrade_proof["ok"] = bool(', 1)[1].split(")", 1)[0]
    assert "cross_consistency_digest" not in ok_assign


def _hex_proof_for_reviewer():
    manifest = build_manifest()
    return next(
        c for c in manifest["capabilities"]
        if c["capability_id"] == "hexagonal_upgrades"
    )["proof"]


def test_manifest_stores_hex_xcons_template_summary_content_safe() -> None:
    summary = _hex_proof_for_reviewer().get(
        "cross_consistency_digest_bridge_event_template"
    )
    assert isinstance(summary, dict)
    # content-safe by construction: only the version string + derived booleans - NEVER
    # the raw bridge-event (no message text, ts_utc, agent, payload).
    for key, value in summary.items():
        if key == "report_version":
            assert isinstance(value, str)
        else:
            assert isinstance(value, bool), key
    assert summary["template_available"] is True
    assert summary["template_only"] is True
    assert summary["no_runtime_authority_granted"] is True
    assert summary["no_runtime_subdivision_authority_granted"] is True
    assert summary["no_approval_granted"] is True
    assert summary["cross_consistent"] is True
    assert summary["all_views_present"] is True
    assert summary["reviewer_clean"] is True
    assert summary["shadow_only_clean"] is True
    assert summary["chain_summary_clean"] is True
    assert summary["path_free_verified"] is True
    assert summary["claim_safe"] is False
    blob = json.dumps(summary)
    assert str(ROOT) not in blob
    assert "ts_utc" not in blob
    assert "template ready" not in blob.lower()


def test_manifest_hex_xcons_template_measurement_only() -> None:
    # Enriched AFTER the hex ok recompute, so the ok expression cannot reference it.
    proof = _hex_proof_for_reviewer()
    assert isinstance(proof.get("cross_consistency_digest_bridge_event_template"), dict)
    assert proof.get("ok") is True
    import inspect
    from tools import wd_image1_capability_manifest as mod

    cap_src = inspect.getsource(mod._capabilities)
    ok_assign = cap_src.split('hex_upgrade_proof["ok"] = bool(', 1)[1].split(
        ")", 1
    )[0]
    assert "cross_consistency_digest_bridge_event_template" not in ok_assign
    ok_idx = cap_src.index('hex_upgrade_proof["ok"] = bool(')
    tpl_idx = cap_src.index(
        'hex_upgrade_proof["cross_consistency_digest_bridge_event_template"]'
    )
    assert tpl_idx > ok_idx, (
        "template summary must be enriched after the hex ok recomputation"
    )


def test_manifest_hex_xcons_template_cannot_flip_ok(monkeypatch) -> None:
    # A forged "lying" template summary (self-claim_safe True, every clean flag False)
    # must leave the hex proof ok byte-identical and be stored as-is (decoupled).
    baseline_ok = _hex_proof_for_reviewer().get("ok")
    from tools import wd_image1_capability_manifest as mod

    forged = {
        "report_version": "forged",
        "template_available": False,
        "template_only": False,
        "no_runtime_authority_granted": False,
        "no_runtime_subdivision_authority_granted": False,
        "no_approval_granted": False,
        "cross_consistent": False,
        "all_views_present": False,
        "reviewer_clean": False,
        "shadow_only_clean": False,
        "chain_summary_clean": False,
        "path_free_verified": False,
        "claim_safe": True,
    }
    monkeypatch.setattr(
        mod,
        "_hex_upgrade_cross_consistency_bridge_event_template_summary",
        lambda *_a, **_k: dict(forged),
    )
    proof = _hex_proof_for_reviewer()
    assert proof.get("ok") == baseline_ok
    assert proof.get("cross_consistency_digest_bridge_event_template") == forged


def test_manifest_stores_hex_xcons_template_index_entry_without_authority() -> None:
    entry = _hex_proof_for_reviewer().get(
        "cross_consistency_digest_bridge_event_template_index_entry"
    )
    assert isinstance(entry, dict)
    assert entry["ok"] is True
    assert (
        entry["proof_id"]
        == "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_v1"
    )
    assert (
        entry["index_entry_version"]
        == "wd.hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry.v1"
    )
    assert entry["template_only"] is True
    assert entry["manual_review_required"] is True
    assert entry["artifact_count"] == 1
    artifact = entry["artifacts"][0]
    assert (
        artifact["artifact_id"]
        == "hex_upgrade_cross_consistency_digest_bridge_event_template"
    )
    assert artifact["payload_included"] is False
    assert artifact["local_path_recorded"] is False
    template_index = entry["template_index_entry"]
    assert template_index["source_contract_check"] == "match"
    assert template_index["template_contract_check"] == "match"
    assert template_index["authority_boundary_check"] == "match"
    assert template_index["cross_consistency_safe_keys_check"] == "match"
    assert template_index["bridge_event_schema_validated"] is True
    assert (
        template_index["event_status"]
        == "hex_upgrade_cross_consistency_digest_bridge_event_template_ready"
    )
    assert template_index["source_digest_ref"].startswith("sha256:")
    assert template_index["cross_consistent"] is True
    assert template_index["runtime_subdivision_authority_granted"] is False
    assert template_index["direct_bridge_write_performed"] is False
    assert template_index["bridge_event_written"] is False
    assert entry["runtime_subdivision_authority_granted"] is False
    assert entry["artifact_payloads_included"] is False
    assert entry["local_paths_recorded"] is False
    assert entry["path_free_verified"] is True
    blob = json.dumps(entry)
    assert str(ROOT) not in blob
    assert "ts_utc" not in blob
    assert "template ready" not in blob.lower()


def test_manifest_stores_hex_xcons_template_index_entry_verification_without_authority() -> None:
    verification = _hex_proof_for_reviewer().get(
        "cross_consistency_digest_bridge_event_template_index_entry_verification"
    )
    assert isinstance(verification, dict)
    assert verification["ok"] is True
    assert (
        verification["verification_version"]
        == "wd.hex_upgrade_cross_consistency_digest_bridge_event_template_"
        "index_entry_verification.v1"
    )
    assert (
        verification["index_entry_version"]
        == "wd.hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry.v1"
    )
    assert verification["source_contract_check"] == "match"
    assert verification["rebuilt_index_entry_check"] == "match"
    assert verification["bridge_event_schema_check"] == "match"
    assert verification["digest_checks"] == {
        "hex_upgrade_cross_consistency_digest_bridge_event_template": "match",
    }
    assert verification["size_checks"] == {
        "hex_upgrade_cross_consistency_digest_bridge_event_template": "match",
    }
    assert verification["schema_version_checks"] == {
        "hex_upgrade_cross_consistency_digest_bridge_event_template": "match",
    }
    assert verification["template_only"] is True
    assert verification["manual_review_required"] is True
    assert verification["runtime_subdivision_authority_granted"] is False
    assert verification["direct_bridge_write_performed"] is False
    assert verification["bridge_event_written"] is False
    assert verification["artifact_payloads_included"] is False
    assert verification["local_paths_recorded"] is False
    assert verification["path_free_verified"] is True
    blob = json.dumps(verification)
    assert str(ROOT) not in blob
    assert "ts_utc" not in blob
    assert "template ready" not in blob.lower()


def test_manifest_stores_hex_xcons_template_index_entry_verification_summary_without_authority() -> None:
    summary = _hex_proof_for_reviewer().get(
        "cross_consistency_digest_bridge_event_template_index_entry_verification_summary"
    )
    assert isinstance(summary, dict)
    assert summary["ok"] is True
    assert (
        summary["summary_version"]
        == "waggledance.hex_upgrade_cross_consistency_digest_bridge_event_template_"
        "index_entry_verification_summary.v1"
    )
    verification = summary[
        "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification"
    ]
    assert verification["verification_ok"] is True
    assert (
        verification["verification_version"]
        == "wd.hex_upgrade_cross_consistency_digest_bridge_event_template_"
        "index_entry_verification.v1"
    )
    assert (
        verification["index_entry_version"]
        == "wd.hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry.v1"
    )
    assert verification["artifact_count_checked"] == 1
    assert verification["source_contract_check"] == "match"
    assert verification["rebuilt_index_entry_check"] == "match"
    assert verification["bridge_event_schema_check"] == "match"
    assert verification["runtime_subdivision_authority_granted"] is False
    assert verification["claim_safe"] is False
    assert summary["operator_boundary"]["verification_report_boundary_ok"] is True
    assert summary["manual_review_required"] is True
    assert summary["approval_granted"] is False
    assert summary["release_decision_made"] is False
    assert summary["automatic_release_decision"] is False
    assert summary["claim_safe"] is False
    assert summary["direct_bridge_write_performed"] is False
    assert summary["transport_added"] is False
    assert summary["runtime_subdivision_authority_granted"] is False
    assert summary["bridge_event_written"] is False
    assert summary["artifact_payloads_included"] is False
    assert summary["local_paths_recorded"] is False
    assert summary["path_free_verified"] is True
    blob = json.dumps(summary)
    assert str(ROOT) not in blob
    assert "ts_utc" not in blob
    assert "template ready" not in blob.lower()


def test_manifest_stores_hex_xcons_template_index_entry_verification_summary_bridge_event_template_without_authority() -> None:
    template = _hex_proof_for_reviewer().get(
        "cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template"
    )
    assert isinstance(template, dict)
    assert template["ok"] is True
    assert (
        template["template_version"]
        == "waggledance.hex_upgrade_cross_consistency_digest_bridge_event_template_"
        "index_entry_verification_summary_bridge_event_template.v1"
    )
    assert template["template_only"] is True
    assert template["manual_review_required"] is True
    assert template["approval_granted"] is False
    assert template["release_decision_made"] is False
    assert template["automatic_release_decision"] is False
    assert template["claim_safe"] is False
    assert template["runtime_subdivision_authority_granted"] is False
    assert template["direct_bridge_write_performed"] is False
    assert template["transport_added"] is False
    assert template["bridge_event_written"] is False
    assert template["artifact_payloads_included"] is False
    assert template["local_paths_recorded"] is False
    event = template["bridge_event_template"]
    assert event["type"] == "handoff"
    assert (
        event["status"]
        == "hex_upgrade_cross_consistency_digest_bridge_event_template_"
        "index_entry_verification_summary_ready"
    )
    assert event["paths"] == []
    assert event["write_scope"] == []
    assert event["pid"] == 0
    assert event["cwd"] == "template_not_emitted"
    payload = event["payload"]
    assert payload["template_only"] is True
    assert payload["manual_review_required"] is True
    assert payload["claim_safe"] is False
    assert payload["runtime_subdivision_authority_granted"] is False
    verification = payload[
        "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification"
    ]
    assert verification["verification_ok"] is True
    assert verification["source_contract_check"] == "match"
    assert verification["rebuilt_index_entry_check"] == "match"
    assert verification["bridge_event_schema_check"] == "match"
    assert verification["claim_safe"] is False
    assert verification["runtime_subdivision_authority_granted"] is False
    blob = json.dumps(template)
    assert str(ROOT) not in blob
    assert "ts_utc" in blob
    assert "template ready" in blob.lower()


def test_manifest_hex_xcons_template_index_entry_measurement_only() -> None:
    proof = _hex_proof_for_reviewer()
    assert isinstance(
        proof.get("cross_consistency_digest_bridge_event_template_index_entry"),
        dict,
    )
    assert isinstance(
        proof.get(
            "cross_consistency_digest_bridge_event_template_index_entry_verification"
        ),
        dict,
    )
    assert isinstance(
        proof.get(
            "cross_consistency_digest_bridge_event_template_index_entry_verification_summary"
        ),
        dict,
    )
    assert isinstance(
        proof.get(
            "cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template"
        ),
        dict,
    )
    assert proof.get("ok") is True
    import inspect
    from tools import wd_image1_capability_manifest as mod

    cap_src = inspect.getsource(mod._capabilities)
    ok_assign = cap_src.split('hex_upgrade_proof["ok"] = bool(', 1)[1].split(
        ")",
        1,
    )[0]
    assert "cross_consistency_digest_bridge_event_template_index_entry" not in (
        ok_assign
    )
    assert (
        "cross_consistency_digest_bridge_event_template_index_entry_verification"
        not in ok_assign
    )
    assert (
        "cross_consistency_digest_bridge_event_template_index_entry_verification_summary"
        not in ok_assign
    )
    assert (
        "cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template"
        not in ok_assign
    )
    ok_idx = cap_src.index('hex_upgrade_proof["ok"] = bool(')
    index_idx = cap_src.index(
        'hex_upgrade_proof[\n        "cross_consistency_digest_bridge_event_template_index_entry"'
    )
    verification_idx = cap_src.index(
        'hex_upgrade_proof[\n        "cross_consistency_digest_bridge_event_template_index_entry_verification"'
    )
    summary_idx = cap_src.index(
        'hex_upgrade_proof[\n        "cross_consistency_digest_bridge_event_template_index_entry_verification_summary"'
    )
    template_idx = cap_src.index(
        'hex_upgrade_proof[\n        "cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template"'
    )
    assert index_idx > ok_idx, (
        "template index entry must be enriched after the hex ok recomputation"
    )
    assert verification_idx > ok_idx, (
        "template index-entry verification must be enriched after the hex ok recomputation"
    )
    assert summary_idx > ok_idx, (
        "template index-entry verification summary must be enriched after "
        "the hex ok recomputation"
    )
    assert template_idx > ok_idx, (
        "template index-entry verification summary bridge-event template must "
        "be enriched after the hex ok recomputation"
    )


def test_manifest_hex_xcons_template_index_entry_cannot_flip_ok(monkeypatch) -> None:
    baseline_ok = _hex_proof_for_reviewer().get("ok")
    from tools import wd_image1_capability_manifest as mod

    forged = {
        "proof_id": "forged",
        "ok": False,
        "claim_safe": True,
        "runtime_subdivision_authority_granted": True,
    }
    forged_verification = {
        "verification_version": "forged",
        "ok": False,
        "claim_safe": True,
        "runtime_subdivision_authority_granted": True,
    }
    forged_summary = {
        "summary_version": "forged",
        "ok": False,
        "claim_safe": True,
        "runtime_subdivision_authority_granted": True,
    }
    forged_template = {
        "template_version": "forged",
        "ok": False,
        "claim_safe": True,
        "runtime_subdivision_authority_granted": True,
    }
    monkeypatch.setattr(
        mod,
        "_hex_upgrade_cross_consistency_bridge_event_template_index_entry_bundle",
        lambda *_a, **_k: (dict(forged), dict(forged_verification)),
    )
    monkeypatch.setattr(
        mod,
        "_hex_upgrade_cross_consistency_bridge_event_template_index_entry_verification_summary",
        lambda *_a, **_k: dict(forged_summary),
    )
    monkeypatch.setattr(
        mod,
        "_hex_upgrade_cross_consistency_bridge_event_template_index_entry_verification_summary_bridge_event_template",
        lambda *_a, **_k: dict(forged_template),
    )
    proof = _hex_proof_for_reviewer()
    assert proof.get("ok") == baseline_ok
    assert (
        proof.get("cross_consistency_digest_bridge_event_template_index_entry")
        == forged
    )
    assert (
        proof.get(
            "cross_consistency_digest_bridge_event_template_index_entry_verification"
        )
        == forged_verification
    )
    assert (
        proof.get(
            "cross_consistency_digest_bridge_event_template_index_entry_verification_summary"
        )
        == forged_summary
    )
    assert (
        proof.get(
            "cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template"
        )
        == forged_template
    )


def test_manifest_hex_next_smallest_pr_advanced_beyond_xcons_template() -> None:
    manifest = build_manifest()
    cap = next(
        c for c in manifest["capabilities"]
        if c["capability_id"] == "hexagonal_upgrades"
    )
    nsp = cap["next_smallest_pr"].lower()
    assert "cross-consistency digest that confirms" not in nsp
    assert "verification summary bridge-event template" in nsp
    assert "index-entry" in nsp
    assert "local index entry" in nsp
    assert "bridge-event renderer" not in nsp
    assert "reviewer summary renderer" not in nsp
    assert "add a local verifier" not in nsp


def test_manifest_stores_ring_hierarchy_summary_content_safe() -> None:
    manifest = build_manifest()
    hex_cap = next(
        c for c in manifest["capabilities"]
        if c["capability_id"] == "hexagonal_upgrades"
    )
    summary = hex_cap["proof"].get("ring_hierarchy_summary")
    assert isinstance(summary, dict)
    # content-safe by construction: version string + derived bools + an int
    for key, value in summary.items():
        if key == "report_version":
            assert isinstance(value, str)
        elif key == "blocker_count":
            assert value is None or (
                isinstance(value, int) and not isinstance(value, bool)
            )
        else:
            assert isinstance(value, bool), key
    assert summary["ok"] is True
    assert summary["hierarchy_ok"] is True
    assert summary["ring_boundary_ok"] is True
    assert summary["no_runtime_mutation"] is True
    assert summary["deterministic"] is True
    assert summary["blocker_count"] == 0
    assert str(ROOT) not in json.dumps(summary)


def test_manifest_ring_hierarchy_summary_not_folded_into_ok() -> None:
    # The ring/hierarchy summary is measurement-only: hex_upgrade proof ok must not
    # depend on it (the ok computation does not reference ring_hierarchy_summary).
    import inspect
    from tools import wd_image1_capability_manifest as mod

    src = inspect.getsource(mod._capabilities)
    ok_assign = src.split('hex_upgrade_proof["ok"] = bool(', 1)[1].split(")", 1)[0]
    assert "ring_hierarchy_summary" not in ok_assign


def test_manifest_hex_proof_excludes_raw_ring_forbidden_vocabulary() -> None:
    # The CURATED ring summary must NOT embed the raw proof's forbidden_vocabulary
    # exclusion list (which would otherwise leak the forbidden terms into the manifest).
    manifest = build_manifest()
    hex_cap = next(
        c for c in manifest["capabilities"]
        if c["capability_id"] == "hexagonal_upgrades"
    )
    blob = json.dumps(hex_cap["proof"])
    for term in ("conscious", "sentient", "self-aware", "explosive intelligence"):
        assert term not in blob, term


def _low_risk_proof_for_reviewer():
    manifest = build_manifest()
    return next(
        c for c in manifest["capabilities"]
        if c["capability_id"] == "low_risk_autonomy_loop"
    )["proof"]


def test_manifest_stores_repeat_window_reviewer_summary_content_safe() -> None:
    summary = _low_risk_proof_for_reviewer().get("repeat_window_trend_reviewer_summary")
    assert isinstance(summary, dict)
    # content-safe by construction: version string + derived bools + strict ints/None
    for key, value in summary.items():
        if key == "report_version":
            assert isinstance(value, str)
        elif key in (
            "window_size", "promoted_solver_count_min", "promoted_solver_count_max",
        ):
            assert value is None or (
                isinstance(value, int) and not isinstance(value, bool)
            )
        else:
            assert isinstance(value, bool), key
    assert summary["trend_review_clean"] is True
    assert summary["path_free_verified"] is True
    assert summary["claim_safe"] is False
    assert str(ROOT) not in json.dumps(summary)


def test_manifest_repeat_window_reviewer_summary_measurement_only() -> None:
    # Measurement-only: the reviewer summary is enriched AFTER the low-risk proof ok is
    # recomputed, so the ok expression cannot reference it. We assert this against the
    # REAL _capabilities ok recomputation (#1286 review: the prior test inspected
    # build_low_risk_autonomy_proof, but the actual insertion + ok block live in
    # _capabilities).
    proof = _low_risk_proof_for_reviewer()
    assert isinstance(proof.get("repeat_window_trend_reviewer_summary"), dict)
    assert proof.get("ok") is True
    import inspect
    from tools import wd_image1_capability_manifest as mod

    src = inspect.getsource(mod)
    parts = src.split("def build_low_risk_autonomy_proof", 1)
    if len(parts) > 1:
        body = parts[1].split("\ndef ", 1)[0]
        assert "repeat_window_trend_reviewer_summary" not in body

    # The real _capabilities ok recomputation must precede the summary insertion, so the
    # summary is provably outside the ok expression (decoupled by construction).
    cap_src = inspect.getsource(mod._capabilities)
    ok_idx = cap_src.index('low_risk_autonomy_proof["ok"] = bool(')
    summary_idx = cap_src.index(
        'low_risk_autonomy_proof["repeat_window_trend_reviewer_summary"]'
    )
    assert summary_idx > ok_idx, (
        "reviewer summary must be enriched after the low-risk ok recomputation"
    )


def test_manifest_reviewer_summary_cannot_flip_low_risk_ok(monkeypatch) -> None:
    # Runtime proof of decoupling at the real _capabilities level: a forged "lying"
    # reviewer summary (self-claim_safe True, every clean flag False) must leave the
    # low-risk proof ok byte-identical to the clean baseline, and the forged summary is
    # still stored raw (measurement-only, never folded into ok).
    baseline_ok = _low_risk_proof_for_reviewer().get("ok")
    import tools.render_low_risk_repeat_window_trend_reviewer_summary as rmod

    forged = {
        "report_version": "forged",
        "trend_review_clean": False,
        "path_free_verified": False,
        "claim_safe": True,
        "all_runs_ok": False,
    }
    monkeypatch.setattr(
        rmod, "render_repeat_window_trend_reviewer_summary", lambda *_a, **_k: dict(forged)
    )
    proof = _low_risk_proof_for_reviewer()
    assert proof.get("ok") == baseline_ok
    assert proof.get("repeat_window_trend_reviewer_summary") == forged


def test_manifest_stores_low_risk_cross_consistency_digest_content_safe() -> None:
    digest = _low_risk_proof_for_reviewer().get("cross_consistency_digest")
    assert isinstance(digest, dict)
    # content-safe by construction: only the version string + derived booleans
    for key, value in digest.items():
        if key == "report_version":
            assert isinstance(value, str)
        else:
            assert isinstance(value, bool), key
    assert digest["cross_consistent"] is True
    assert digest["all_views_present"] is True
    assert digest["real_loop_clean"] is True
    assert digest["trend_clean"] is True
    assert digest["reviewer_clean"] is True
    assert digest["reviewer_matches_trend"] is True
    assert digest["path_free_verified"] is True
    assert digest["claim_safe"] is False
    # no raw repo path leaked into the stored digest
    assert str(ROOT) not in json.dumps(digest)


def test_manifest_low_risk_cross_consistency_digest_measurement_only() -> None:
    # Measurement-only: the digest is enriched AFTER the low-risk proof ok is recomputed,
    # so the ok expression cannot reference it. Asserted against the REAL _capabilities ok
    # recomputation (the actual insertion + ok block live in _capabilities).
    proof = _low_risk_proof_for_reviewer()
    assert isinstance(proof.get("cross_consistency_digest"), dict)
    assert proof.get("ok") is True
    import inspect
    from tools import wd_image1_capability_manifest as mod

    cap_src = inspect.getsource(mod._capabilities)
    ok_assign = cap_src.split('low_risk_autonomy_proof["ok"] = bool(', 1)[1].split(
        ")", 1
    )[0]
    assert "cross_consistency_digest" not in ok_assign
    # The ok recomputation must PRECEDE the digest insertion, so the digest is provably
    # outside the ok expression (decoupled by construction, not just by convention).
    ok_idx = cap_src.index('low_risk_autonomy_proof["ok"] = bool(')
    digest_idx = cap_src.index(
        'low_risk_autonomy_proof["cross_consistency_digest"]'
    )
    assert digest_idx > ok_idx, (
        "cross-consistency digest must be enriched after the low-risk ok recomputation"
    )


def test_manifest_low_risk_cross_consistency_digest_cannot_flip_ok(monkeypatch) -> None:
    # Runtime proof of decoupling at the real _capabilities level: a forged "lying" digest
    # (self-claim_safe True, every clean flag False) must leave the low-risk proof ok
    # byte-identical to the clean baseline, and the forged digest is still stored raw
    # (measurement-only, never folded into ok).
    baseline_ok = _low_risk_proof_for_reviewer().get("ok")
    import tools.run_low_risk_cross_consistency_digest as xmod

    forged = {
        "report_version": "forged",
        "cross_consistent": False,
        "all_views_present": False,
        "real_loop_clean": False,
        "trend_clean": False,
        "reviewer_clean": False,
        "reviewer_matches_trend": False,
        "path_free_verified": False,
        "claim_safe": True,
    }
    monkeypatch.setattr(
        xmod, "build_low_risk_cross_consistency_digest", lambda *_a, **_k: dict(forged)
    )
    proof = _low_risk_proof_for_reviewer()
    assert proof.get("ok") == baseline_ok
    assert proof.get("cross_consistency_digest") == forged


def _good_real_loop_manifest_contribution_report() -> dict:
    return {
        "report_version": "wd.low_risk_autogrowth_real_loop_proof.v1",
        "ok": True,
        "deterministic_replay": {"evidence_identical": True},
        "evidence_vs_authority": {
            "evidence_present": True,
            "production_authority_granted": False,
        },
        "manifest_contribution": {
            "capability_id": "low_risk_autonomy_loop",
            "evidence_present": True,
            "runtime_authority_granted": False,
            "external_writes_applied": False,
            "scheduler_enqueue": False,
            "production_flip": False,
            "provider_calls": 0,
            "claim_safe": False,
        },
    }


def test_real_loop_manifest_contribution_aggregate_accepts_clean_report() -> None:
    aggregate = _safe_real_loop_manifest_contribution(
        _good_real_loop_manifest_contribution_report()
    )
    assert set(aggregate) == set(_REAL_LOOP_MANIFEST_CONTRIBUTION_SAFE_KEYS)
    assert aggregate["ok"] is True
    assert aggregate["deterministic"] is True
    assert aggregate["evidence_present"] is True
    assert aggregate["production_authority_granted"] is False
    assert aggregate["provider_calls"] == 0
    assert aggregate["claim_safe"] is False


@pytest.mark.parametrize("path,bad", [
    (("report_version",), "unexpected"),
    (("deterministic_replay", "evidence_identical"), "true"),
    (("manifest_contribution", "provider_calls"), True),
    (("manifest_contribution", "provider_calls"), -1),
    (("manifest_contribution", "capability_id"), "hex_mesh_entry"),
])
def test_real_loop_manifest_contribution_aggregate_rejects_bad_shape(
    path, bad
) -> None:
    report = _good_real_loop_manifest_contribution_report()
    target = report
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = bad
    with pytest.raises(ValueError):
        _safe_real_loop_manifest_contribution(report)


def test_build_real_loop_manifest_contribution_returns_safe_scalar_view() -> None:
    aggregate = build_real_loop_manifest_contribution()
    assert set(aggregate) == set(_REAL_LOOP_MANIFEST_CONTRIBUTION_SAFE_KEYS)
    assert aggregate["ok"] is True
    assert aggregate["evidence_present"] is True
    assert aggregate["runtime_authority_granted"] is False
    assert aggregate["claim_safe"] is False


def test_manifest_real_loop_manifest_contribution_measurement_only() -> None:
    # Measurement-only: the contribution is enriched AFTER the low-risk proof ok is
    # recomputed, so the ok expression cannot reference it.
    proof = _low_risk_proof_for_reviewer()
    assert isinstance(proof.get("real_loop_manifest_contribution"), dict)
    assert proof.get("ok") is True
    import inspect
    from tools import wd_image1_capability_manifest as mod

    cap_src = inspect.getsource(mod._capabilities)
    ok_assign = cap_src.split('low_risk_autonomy_proof["ok"] = bool(', 1)[1].split(
        ")", 1
    )[0]
    assert "real_loop_manifest_contribution" not in ok_assign
    ok_idx = cap_src.index('low_risk_autonomy_proof["ok"] = bool(')
    contribution_idx = cap_src.index(
        'low_risk_autonomy_proof["real_loop_manifest_contribution"]'
    )
    assert contribution_idx > ok_idx


def test_manifest_real_loop_manifest_contribution_cannot_flip_ok(
    monkeypatch,
) -> None:
    baseline_ok = _low_risk_proof_for_reviewer().get("ok")
    import tools.run_low_risk_autogrowth_real_loop_proof as rmod

    forged = _good_real_loop_manifest_contribution_report()
    forged["manifest_contribution"]["claim_safe"] = True
    monkeypatch.setattr(rmod, "build_real_loop_proof", lambda *_a, **_k: forged)

    proof = _low_risk_proof_for_reviewer()
    assert proof.get("ok") == baseline_ok
    assert proof["real_loop_manifest_contribution"]["claim_safe"] is True


def test_manifest_low_risk_next_smallest_pr_advanced_beyond_cross_consistency_digest() -> None:
    # The #1291 bridge-event-template WIRING slice advances next_smallest_pr beyond the
    # now-completed renderer/wiring work to the next honest smallest step: a local index
    # entry for the cross-consistency digest bridge-event template.
    manifest = build_manifest()
    cap = next(
        c for c in manifest["capabilities"]
        if c["capability_id"] == "low_risk_autonomy_loop"
    )
    nsp = cap["next_smallest_pr"].lower()
    # no longer the just-completed renderer slice
    assert "bridge-event renderer" not in nsp
    # advanced to the next step: a local index entry for the bridge-event template
    assert "index entry" in nsp
    assert "bridge-event template" in nsp


def test_manifest_stores_low_risk_xcons_template_summary_content_safe() -> None:
    summary = _low_risk_proof_for_reviewer().get(
        "cross_consistency_digest_bridge_event_template"
    )
    assert isinstance(summary, dict)
    # content-safe by construction: only the version string + derived booleans - NEVER
    # the raw bridge-event (no message text, ts_utc, agent, payload).
    for key, value in summary.items():
        if key == "report_version":
            assert isinstance(value, str)
        else:
            assert isinstance(value, bool), key
    assert summary["template_available"] is True
    assert summary["template_only"] is True
    assert summary["no_runtime_authority_granted"] is True
    assert summary["no_approval_granted"] is True
    assert summary["cross_consistent"] is True
    assert summary["path_free_verified"] is True
    assert summary["claim_safe"] is False
    blob = json.dumps(summary)
    assert str(ROOT) not in blob
    # the all-bool check above already proves no raw event/message/payload (strings/dicts)
    # is stored; ts_utc is the distinctive raw-event timestamp that must never appear.
    assert "ts_utc" not in blob
    assert "template ready" not in blob.lower()


def test_manifest_low_risk_xcons_template_measurement_only() -> None:
    # Enriched AFTER the low-risk ok recompute, so the ok expression cannot reference it.
    proof = _low_risk_proof_for_reviewer()
    assert isinstance(proof.get("cross_consistency_digest_bridge_event_template"), dict)
    assert proof.get("ok") is True
    import inspect
    from tools import wd_image1_capability_manifest as mod

    cap_src = inspect.getsource(mod._capabilities)
    ok_assign = cap_src.split('low_risk_autonomy_proof["ok"] = bool(', 1)[1].split(
        ")", 1
    )[0]
    assert "cross_consistency_digest_bridge_event_template" not in ok_assign
    ok_idx = cap_src.index('low_risk_autonomy_proof["ok"] = bool(')
    tpl_idx = cap_src.index(
        'low_risk_autonomy_proof["cross_consistency_digest_bridge_event_template"]'
    )
    assert tpl_idx > ok_idx, (
        "template summary must be enriched after the low-risk ok recomputation"
    )


def test_manifest_low_risk_xcons_template_cannot_flip_ok(monkeypatch) -> None:
    # A forged "lying" template summary (self-claim_safe True, every clean flag False)
    # must leave the low-risk proof ok byte-identical and be stored as-is (decoupled).
    baseline_ok = _low_risk_proof_for_reviewer().get("ok")
    from tools import wd_image1_capability_manifest as mod

    forged = {
        "report_version": "forged",
        "template_available": False,
        "template_only": False,
        "no_runtime_authority_granted": False,
        "no_approval_granted": False,
        "cross_consistent": False,
        "all_views_present": False,
        "path_free_verified": False,
        "claim_safe": True,
    }
    monkeypatch.setattr(
        mod,
        "_low_risk_cross_consistency_bridge_event_template_summary",
        lambda *_a, **_k: dict(forged),
    )
    proof = _low_risk_proof_for_reviewer()
    assert proof.get("ok") == baseline_ok
    assert proof.get("cross_consistency_digest_bridge_event_template") == forged
