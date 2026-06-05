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
        counters["milestone_counters"]["per_query_receipt_coverage_percent"][
            "current_value"
        ]
        == 0.0
    )
    assert (
        counters["milestone_counters"]["future_claim_gate_satisfied"]["satisfied"]
        is True
    )


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
    future = {
        item["capability_id"]: item
        for item in counters["panel_counters"]
    }["future_waggledance_swarm"]
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
