# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from tools.build_wd_vision_progress_counters import (
    SCHEMA_VERSION,
    build_vision_progress_counters,
)
from tools.wd_image1_capability_manifest import build_manifest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "build_wd_vision_progress_counters.py"


def _minimal_manifest() -> dict:
    return {
        "schema_version": "wd_image1_capability_manifest.v1",
        "summary": {
            "capability_count": 3,
            "status_counts": {"partial": 2, "implemented": 1},
            "all_literal_claims_safe": False,
        },
        "capabilities": [
            {
                "capability_id": "hex_mesh_entry",
                "status": "partial",
                "claim_safe": False,
                "evidence": [
                    {"path": "configs/settings.yaml", "present": True},
                    {"path": "configs/hex_cells.yaml", "present": False},
                ],
                "gaps": ["first hop is not authoritative"],
                "next_smallest_pr": "add route-order counter",
                "proof": {
                    "ok": True,
                    "literal_claim_safe": False,
                    "proves_every_query_first_enters_mesh": False,
                    "pre_hex_steps": ["language_detection", "hot_cache"],
                    "current_config": {
                        "hybrid_retrieval_mode": "candidate",
                        "hex_mesh_enabled": False,
                        "hybrid_retrieval_authoritative": False,
                    },
                },
            },
            {
                "capability_id": "magma_audit_log",
                "status": "partial",
                "claim_safe": False,
                "evidence": [
                    {
                        "path": "waggledance/instrumentation/magma_receipts.py",
                        "present": True,
                    }
                ],
                "gaps": ["default receipt sink not mandatory"],
                "next_smallest_pr": "promote receipt coverage counter",
                "proof": {
                    "ok": True,
                    "solver_call_trace_receipt_bound": True,
                    "receipt_count": 1,
                    "default_sink_required": False,
                },
            },
            {
                "capability_id": "future_waggledance_swarm",
                "status": "implemented",
                "claim_safe": True,
                "evidence": [
                    {
                        "path": "tools/run_future_scale_route_depth_benchmark.py",
                        "present": True,
                    }
                ],
                "gaps": [],
                "next_smallest_pr": "",
                "proof": {
                    "ok": True,
                    "literal_future_claim_safe": True,
                    "claim_gate_satisfied": True,
                    "axis_count": 8,
                    "runtime_evidence_summary": {
                        "required_runtime_evidence_present": True,
                        "runtime_evidence_axis_count": 8,
                    },
                },
            },
        ],
    }


def test_build_counters_from_manifest_without_claim_mutation() -> None:
    counters = build_vision_progress_counters(
        _minimal_manifest(),
        generated_at_utc="2026-06-05T18:40:00Z",
    )

    assert counters["schema_version"] == SCHEMA_VERSION
    assert counters["ok"] is True
    assert counters["blockers"] == []
    assert counters["generated_at_utc"] == "2026-06-05T18:40:00Z"
    assert counters["summary"]["capability_count"] == 3
    assert counters["summary"]["claim_safe_count"] == 1
    assert counters["summary"]["unsafe_literal_claim_count"] == 2
    assert counters["summary"]["proof_ok_count"] == 3
    assert counters["summary"]["literal_claim_safe_ratio"] == 0.333333
    assert counters["summary"]["production_safe_capability_count"] == 1
    assert counters["next_smallest_pr_count"] == 2
    assert counters["guardrails"] == {
        "read_only": True,
        "external_writes_applied": False,
        "runtime_authority_changed": False,
        "claim_safe_flip_applied": False,
        "bridge_event_written": False,
        "github_mutation_performed": False,
    }


def test_panel_counters_expose_measurable_milestones() -> None:
    counters = build_vision_progress_counters(_minimal_manifest())
    by_id = {
        item["capability_id"]: item
        for item in counters["panel_counters"]
    }

    assert by_id["hex_mesh_entry"]["panel"] == 1
    assert by_id["hex_mesh_entry"]["evidence_paths"] == [
        "configs/settings.yaml",
        "configs/hex_cells.yaml",
    ]
    assert by_id["hex_mesh_entry"]["milestones"] == {
        "authoritative_first_hop_safe": False,
        "literal_claim_safe": False,
        "pre_hex_step_count": 2,
        "hybrid_retrieval_mode": "candidate",
        "hex_mesh_enabled": False,
        "hybrid_retrieval_authoritative": False,
    }
    assert by_id["magma_audit_log"]["milestones"]["receipt_count"] == 1
    assert (
        counters["milestone_counters"][
            "authoritative_first_hop_route_order_coverage"
        ]["current_value"]
        == 0.0
    )
    assert (
        counters["milestone_counters"]["per_query_receipt_claim_gate"][
            "current_value"
        ]
        is False
    )
    assert "per_query_receipt_coverage_percent" not in counters["milestone_counters"]
    assert (
        counters["milestone_counters"]["future_claim_gate_satisfied"]["satisfied"]
        is True
    )


def test_receipt_claim_gate_uses_same_condition_for_value_and_satisfied() -> None:
    manifest = _minimal_manifest()
    for capability in manifest["capabilities"]:
        if capability["capability_id"] == "magma_audit_log":
            capability["proof"]["default_sink_required"] = True
            capability["proof"]["solver_call_trace_receipt_bound"] = False

    counters = build_vision_progress_counters(manifest)
    receipt_gate = counters["milestone_counters"]["per_query_receipt_claim_gate"]

    assert receipt_gate["current_value"] is False
    assert receipt_gate["target_value"] is True
    assert receipt_gate["satisfied"] is False
    assert receipt_gate["coverage_measurement_available"] is False
    assert receipt_gate["measured_coverage_percent"] is None
    assert receipt_gate["measurement_basis"] == "manifest_claim_gate_flags"


def test_current_manifest_counters_do_not_upgrade_image_claims() -> None:
    counters = build_vision_progress_counters(build_manifest(ROOT))

    assert counters["source_schema_version"] == "wd_image1_capability_manifest.v1"
    assert counters["summary"]["capability_count"] == 6
    assert counters["summary"]["status_counts"] == {
        "blocked": 0,
        "implemented": 0,
        "partial": 6,
        "planned": 0,
    }
    assert counters["summary"]["claim_safe_count"] == 0
    assert counters["summary"]["all_literal_claims_safe"] is False
    assert counters["summary"]["proof_ok_count"] == 6
    assert counters["guardrails"]["claim_safe_flip_applied"] is False
    by_id = {
        item["capability_id"]: item
        for item in counters["panel_counters"]
    }
    low_risk = by_id["low_risk_autonomy_loop"]
    assert low_risk["milestones"]["real_loop_report_ok"] is True
    assert (
        low_risk["milestones"]["real_loop_claim_label"]
        == "MEASURED_LOCAL_DRY_RUN"
    )
    assert low_risk["milestones"]["measured_auto_promoted_solver_count"] == 1
    assert low_risk["milestones"]["measured_auto_promoted_run_count"] == 1
    assert low_risk["milestones"]["measured_provider_jobs_created"] == 0
    assert low_risk["milestones"]["measured_builder_jobs_created"] == 0
    assert low_risk["milestones"]["dry_run_runtime_authority_granted"] is False
    assert low_risk["milestones"]["dry_run_external_writes_applied"] is False
    gated_promotions = counters["milestone_counters"][
        "end_to_end_gated_promotions_total"
    ]
    assert gated_promotions == {
        "current_value": 1,
        "target_value": 1,
        "satisfied": True,
        "guardrail_runtime_authority_granted": False,
        "guardrail_tripped": False,
        "measurement_basis": "local_ephemeral_control_plane_real_loop",
        "claim_label": "MEASURED_LOCAL_DRY_RUN",
        "production_authority_granted": False,
    }
    future = by_id["future_waggledance_swarm"]
    assert future["milestones"]["literal_future_claim_safe"] is False
    assert future["milestones"]["future_claim_gate_satisfied"] is False
    assert future["milestones"]["required_runtime_evidence_present"] is False
    assert (
        counters["milestone_counters"]["future_claim_gate_satisfied"]["satisfied"]
        is False
    )


def test_empty_capability_manifest_blocks_and_keeps_zero_ratios() -> None:
    counters = build_vision_progress_counters(
        {
            "schema_version": "wd_image1_capability_manifest.v1",
            "summary": {"status_counts": {}},
            "capabilities": [],
        }
    )

    assert counters["ok"] is False
    assert counters["blockers"] == ["capabilities_empty"]
    assert counters["summary"]["capability_count"] == 0
    assert counters["summary"]["all_literal_claims_safe"] is False
    assert counters["summary"]["proof_ok_ratio"] == 0.0
    assert counters["summary"]["literal_claim_safe_ratio"] == 0.0
    assert counters["panel_counters"] == []


def test_malformed_manifest_reports_blockers_without_favorable_claims() -> None:
    counters = build_vision_progress_counters(
        {
            "schema_version": "unexpected",
            "capabilities": [
                {
                    "capability_id": "hex_mesh_entry",
                    "status": "partial",
                    "claim_safe": "yes",
                    "evidence": "not-list",
                    "proof": {"ok": "true"},
                },
                "not-a-capability",
            ],
        }
    )

    assert counters["ok"] is False
    assert "unexpected_manifest_schema_version" in counters["blockers"]
    assert "summary_not_mapping" in counters["blockers"]
    assert "hex_mesh_entry_claim_safe_not_bool" in counters["blockers"]
    assert "hex_mesh_entry_proof_ok_not_bool" in counters["blockers"]
    assert "hex_mesh_entry_evidence_not_list" in counters["blockers"]
    assert "capability_1_not_mapping" in counters["blockers"]
    assert counters["summary"]["claim_safe_count"] == 0
    assert counters["summary"]["proof_ok_count"] == 0
    assert counters["summary"]["literal_claim_safe_ratio"] == 0.0


def test_invalid_summary_status_counts_fail_closed_and_rederive_counts() -> None:
    counters = build_vision_progress_counters(
        {
            "schema_version": "wd_image1_capability_manifest.v1",
            "summary": {"status_counts": {"implemented": "one"}},
            "capabilities": [
                {
                    "capability_id": "hex_mesh_entry",
                    "status": "partial",
                    "claim_safe": False,
                    "evidence": [],
                    "proof": {"ok": True},
                }
            ],
        }
    )

    assert counters["ok"] is False
    assert (
        "summary_status_counts_implemented_not_non_negative_int"
        in counters["blockers"]
    )
    assert counters["summary"]["status_counts"] == {"partial": 1}
    assert counters["summary"]["all_literal_claims_safe"] is False


def test_non_mapping_manifest_returns_structured_blocker() -> None:
    counters = build_vision_progress_counters([])

    assert counters["ok"] is False
    assert counters["blockers"] == ["manifest_not_mapping"]
    assert counters["summary"]["capability_count"] == 0
    assert counters["summary"]["all_literal_claims_safe"] is False
    assert counters["panel_counters"] == []

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", "--manifest", "-"],
        input="[]",
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(proc.stdout)

    assert payload["ok"] is False
    assert payload["blockers"] == ["manifest_not_mapping"]


def test_cli_emits_json_from_manifest_file_and_strict_claims_fails(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_minimal_manifest()), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", "--manifest", str(manifest_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(proc.stdout)

    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["summary"]["all_literal_claims_safe"] is False

    strict = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--strict-claims",
            "--manifest",
            str(manifest_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert strict.returncode == 2


def test_cli_strict_claims_fails_when_manifest_has_blockers() -> None:
    manifest = {
        "schema_version": "unexpected",
        "summary": {"status_counts": {"implemented": 1}},
        "capabilities": [
            {
                "capability_id": "future_waggledance_swarm",
                "status": "implemented",
                "claim_safe": True,
                "evidence": [],
                "proof": {"ok": True},
            }
        ],
    }

    strict = subprocess.run(
        [sys.executable, str(SCRIPT), "--strict-claims", "--manifest", "-"],
        input=json.dumps(manifest),
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    payload = json.loads(strict.stdout)

    assert strict.returncode == 2
    assert payload["ok"] is False
    assert "unexpected_manifest_schema_version" in payload["blockers"]
    assert payload["summary"]["claim_safe_count"] == 0
    assert payload["summary"]["all_literal_claims_safe"] is False
    assert payload["summary"]["literal_claim_safe_ratio"] == 0.0
    assert payload["summary"]["production_safe_capability_count"] == 0
    assert payload["summary"]["unsafe_literal_claim_ids"] == [
        "future_waggledance_swarm"
    ]


def test_cli_run_does_not_mutate_tracked_files(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_minimal_manifest()), encoding="utf-8")
    before = subprocess.run(
        ["git", "status", "--short", "-uno"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--manifest", str(manifest_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    after = subprocess.run(
        ["git", "status", "--short", "-uno"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout

    assert json.loads(proc.stdout)["guardrails"]["read_only"] is True
    assert after == before


def test_gated_promotions_fail_closed_on_guardrail_leaks() -> None:
    # Regression for the tools changes_requested: a guardrail leak (runtime
    # authority / external writes / provider jobs / builder jobs) must drive
    # end_to_end_gated_promotions_total.current_value to 0, satisfied=False,
    # guardrail_tripped=True - never a misleading count of 1.
    import copy

    base = build_manifest(ROOT)

    def _inject(real_loop: dict, name: str) -> None:
        if name == "runtime_authority":
            real_loop.setdefault("authority_boundary", {})["runtime_authority_granted"] = True
        elif name == "external_writes":
            real_loop.setdefault("authority_boundary", {})["external_writes_applied"] = True
        elif name == "provider_jobs":
            real_loop.setdefault("control_plane", {}).setdefault("table_counts", {})["provider_jobs"] = 1
        elif name == "builder_jobs":
            real_loop.setdefault("control_plane", {}).setdefault("table_counts", {})["builder_jobs"] = 1

    for name in ("runtime_authority", "external_writes", "provider_jobs", "builder_jobs"):
        manifest = copy.deepcopy(base)
        for cap in manifest["capabilities"]:
            if cap["capability_id"] == "low_risk_autonomy_loop":
                _inject(cap["proof"].setdefault("real_loop_dry_run", {}), name)
        counters = build_vision_progress_counters(manifest)
        gp = counters["milestone_counters"]["end_to_end_gated_promotions_total"]
        assert gp["guardrail_tripped"] is True, name
        assert gp["satisfied"] is False, name
        assert gp["current_value"] == 0, name

    # production_authority_granted is derived, not hardcoded: true when a
    # runtime-authority leak is observed.
    leaked = copy.deepcopy(base)
    for cap in leaked["capabilities"]:
        if cap["capability_id"] == "low_risk_autonomy_loop":
            cap["proof"].setdefault("real_loop_dry_run", {}).setdefault(
                "authority_boundary", {}
            )["runtime_authority_granted"] = True
    gp_leaked = build_vision_progress_counters(leaked)["milestone_counters"][
        "end_to_end_gated_promotions_total"
    ]
    assert gp_leaked["production_authority_granted"] is True
