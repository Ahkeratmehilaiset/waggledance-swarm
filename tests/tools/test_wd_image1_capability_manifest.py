# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from tools.wd_image1_capability_manifest import build_manifest
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
    build_low_risk_autogrowth_ops_alert_state_smoke,
)
from tools.wd_image1_capability_manifest import build_low_risk_autonomy_proof
from tools.wd_image1_capability_manifest import build_solver_trace_magma_receipt_proof


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
    assert proof["route_stage_ui_smoke"]["checks"][
        "dashboard_stage_container_present"
    ] is True
    assert proof["route_stage_operator_metrics_smoke"]["ok"] is True
    assert proof["route_stage_operator_metrics_smoke"][
        "operator_visible_metrics"
    ] is True
    assert proof["route_stage_runtime_metrics_smoke"]["ok"] is True
    assert proof["route_stage_runtime_metrics_smoke"][
        "operator_visible_metrics"
    ] is True
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
    assert "hex_neighbor_assist_7_cell" in smoke["ws_event_contract"][
        "disabled_route_stages"
    ]
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
    assert smoke["latency_feed_state_visible"] is True
    assert smoke["alert_thresholds_documented"] is True
    assert smoke["runbook_path"] == (
        "docs/operations/ROUTE_STAGE_LATENCY_RUNBOOK.md"
    )
    assert smoke["latency_metric_semantics"] == (
        "stage_correlated_request_latency"
    )
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
    assert proof["magma_execution_receipt_proof"][
        "solver_call_trace_receipt_bound"
    ] is True
    assert proof["magma_execution_receipt_proof"][
        "solver_call_trace_privacy_safe"
    ] is True
    assert proof["receipt_metrics"] == {
        "receipt_count": 1,
        "solver_call_trace_count": 1,
        "solver_call_trace_receipt_bound": True,
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
        item["matched_expected"]
        for item in proof["runtime_topology"]["sample_origins"]
    )
    assert proof["shadow_child_cell_ids_absent_from_runtime_config"] is True
    assert proof["no_runtime_topology_mutation"] is True
    assert proof["runtime_authority_changed"] is False
    assert proof["operator_gate_required"] is False
    assert proof["external_writes_applied"] is False


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


def test_low_risk_autogrowth_operator_metrics_smoke_reports_prometheus_contract() -> None:
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
    assert proof["runbook_path"] == (
        "docs/operations/LOW_RISK_AUTOGROWTH_RUNBOOK.md"
    )
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
    assert "docs/operations/LOW_RISK_AUTOGROWTH_RUNBOOK.md" in (
        proof["missing_inputs"]
    )
    assert "docs/API.md" in proof["missing_inputs"]
    assert proof["runtime_authority_changed"] is False


def test_low_risk_autogrowth_ops_alert_state_smoke_reports_dashboard_contract() -> None:
    proof = build_low_risk_autogrowth_ops_alert_state_smoke(ROOT)

    assert proof["ok"] is True
    assert proof["proof_id"] == (
        "low_risk_autogrowth_ops_alert_state_smoke_v1"
    )
    assert proof["ops_endpoint"] == "/api/ops"
    assert proof["dashboard_path"] == "web/hologram-brain-v6.html"
    assert proof["api_contract_present"] is True
    assert proof["ui_contract_present"] is True
    assert proof["test_contract_present"] is True
    assert proof["docs_contract_present"] is True
    assert proof["alert_state_visible"] is True
    assert proof["local_snapshot_source"] is True
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
    assert "docs/operations/ROUTE_STAGE_LATENCY_RUNBOOK.md" in (
        proof["missing_inputs"]
    )
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
        "web/hologram-brain-v6.html",
        "tests/test_metrics_endpoint.py",
        "tests/integration/test_chat_api_contract.py",
        "tests/test_legacy_consolidation.py",
        "docs/API.md",
        "docs/operations/ROUTE_STAGE_LATENCY_RUNBOOK.md",
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
    assert capability["proof"]["runtime_boundary_smoke"][
        "active_runtime_dispatch_enabled"
    ] is False
    assert capability["proof"]["runtime_boundary_smoke"][
        "shadow_child_cell_ids_absent_from_runtime_config"
    ] is True
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
    assert capability["proof"]["runtime_boundary_smoke"]["ok"] is True
    assert capability["proof"]["runtime_boundary_smoke"][
        "default_interval_seconds"
    ] == 30.0
    assert capability["proof"]["runtime_boundary_smoke"][
        "default_max_ticks_per_wake"
    ] == 20
    assert capability["proof"]["runtime_boundary_smoke"][
        "runtime_authority_changed"
    ] is False
    assert capability["proof"]["operator_metrics_smoke"]["ok"] is True
    assert capability["proof"]["operator_metrics_smoke"][
        "operator_visible_metrics"
    ] is True
    assert capability["proof"]["operator_metrics_smoke"][
        "runtime_authority_changed"
    ] is False
    assert capability["proof"]["alert_runbook_smoke"]["ok"] is True
    assert capability["proof"]["alert_runbook_smoke"][
        "alert_thresholds_documented"
    ] is True
    assert capability["proof"]["alert_runbook_smoke"][
        "forbidden_controls_absent"
    ] is True
    assert capability["proof"]["alert_runbook_smoke"][
        "runtime_authority_changed"
    ] is False
    assert capability["proof"]["ops_alert_state_smoke"]["ok"] is True
    assert capability["proof"]["ops_alert_state_smoke"][
        "alert_state_visible"
    ] is True
    assert capability["proof"]["ops_alert_state_smoke"][
        "forbidden_controls_absent"
    ] is True
    assert capability["proof"]["ops_alert_state_smoke"][
        "runtime_authority_changed"
    ] is False
    assert "Prometheus/Alertmanager feed" in capability["next_smallest_pr"]
    assert "read-only dashboard ops overlay" in capability["safe_statement"]
    assert "local alert state" in capability["safe_statement"]
    assert "operator alert thresholds" in capability["safe_statement"]
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
    assert capability["proof"]["route_stage_runtime_metrics_smoke"][
        "histogram_quantile_supported"
    ] is True
    assert capability["proof"]["route_stage_runtime_metrics_smoke"][
        "latency_panel_templates_visible"
    ] is True
    assert capability["proof"]["route_stage_runtime_metrics_smoke"][
        "latency_feed_state_visible"
    ] is True
    assert "route-stage labels" in capability["safe_statement"]
    assert "route-stage operator metrics" in capability["safe_statement"]
    assert "runtime rate/latency counters" in capability["safe_statement"]
    assert "p95/p99" in capability["safe_statement"]
    assert "read-only feed state" in capability["safe_statement"]
    assert "endpoint configuration" in capability["next_smallest_pr"]
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
    assert "hard append-only" in capability["safe_statement"]
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
    assert proof["runtime_authority_changed"] is False
    assert proof["operator_gate_required"] is False
    assert proof["external_writes_applied"] is False
    assert {
        item["axis_id"] for item in proof["axes"]
    } == {
        "coverage",
        "llm_fallback_rate",
        "route_depth",
        "useful_composite_paths",
        "contradiction_rate",
        "insight_score",
        "latency",
        "audit_completeness",
    }
    assert all(
        item["literal_claim_safe"] is False
        for item in proof["claim_decomposition"]
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


def test_manifest_embeds_future_scorecard_without_upgrading_claim() -> None:
    report = build_manifest(ROOT)
    capability = _by_id(report)["future_waggledance_swarm"]

    assert capability["status"] == "partial"
    assert capability["claim_safe"] is False
    assert capability["proof"]["ok"] is True
    assert capability["proof"]["literal_future_claim_safe"] is False
    assert capability["proof"]["unbounded_claims_rejected"] is True
    assert capability["proof"]["axis_count"] == 8
    assert "runtime metrics" in capability["next_smallest_pr"]
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
