# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.run_v12_memory_palace_shortcut_proof import (
    build_memory_palace_shortcut_proof,
    render_markdown,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "run_v12_memory_palace_shortcut_proof.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_build_memory_palace_shortcut_proof_reports_projection_only() -> None:
    report = build_memory_palace_shortcut_proof(now_utc=_fixed_now())

    assert report["ok"] is True
    assert report["report_version"] == "wd.v12.memory_palace_shortcut_proof.v0"
    assert report["claim_label"] == "MEASURED_LOCAL_PROJECTION"
    assert report["source_of_truth"] == "projection_only"
    assert report["memory_id"] == "memory.learning.cell_imaging.1"
    assert report["projection"] == {
        "schema_version": "memory_palace_projection.v1",
        "node_count": 6,
        "placement_count": 1,
        "shortcut_hint_count": 6,
    }
    navigation = report["navigation_index"]
    assert navigation["schema_version"] == "memory_palace_navigation_index.v0"
    assert navigation["source_of_truth"] == "projection_only"
    assert navigation["node_count"] == 6
    assert navigation["root_node_ids"] == [
        "wing.learning",
        "wing.research",
        "wing.system",
    ]
    assert navigation["max_depth"] == 1
    assert navigation["source_path_node_ids"] == [
        "wing.learning",
        "room.learning.cell_imaging",
    ]
    assert navigation["target_path_node_ids"] == [
        "wing.research",
        "room.research.pathology",
    ]
    ranked = report["ranked_shortcuts"]
    assert ranked["candidate_count"] == 2
    top = ranked["top_candidate"]
    assert top["target_node_id"] == "room.research.pathology"
    assert top["rank_score"] == 0.68
    assert top["hierarchy_hops"] == 3
    assert top["matched_selector_keys"] == ["tags", "vector_kind"]
    bypass = report["bypass_analysis"]
    assert bypass["source_node_id"] == "room.learning.cell_imaging"
    assert bypass["target_node_id"] == "room.research.pathology"
    assert bypass["source_path_node_ids"] == [
        "wing.learning",
        "room.learning.cell_imaging",
    ]
    assert bypass["target_path_node_ids"] == [
        "wing.research",
        "room.research.pathology",
    ]
    assert bypass["hierarchy_hops"] == 3
    assert bypass["projected_shortcut_hops"] == 1
    assert bypass["intermediate_hops_skipped"] == 2
    assert bypass["navigation_index_used"] is True
    assert bypass["navigation_index_runtime_load_required"] is False
    assert bypass["intermediate_node_traversal_required"] is False
    assert bypass["intermediate_nodes_not_loaded"] is True
    assert bypass["shortcut_ranked_without_runtime_dispatch"] is True
    assert bypass["runtime_route_changed"] is False
    assert bypass["solver_call_performed"] is False
    assert bypass["analysis_scope"] == "projection_only_read_side_hint"


def test_authority_boundary_stays_false_for_runtime_effects() -> None:
    report = build_memory_palace_shortcut_proof(now_utc=_fixed_now())
    boundary = report["authority_boundary"]

    assert boundary["read_side_projection_only"] is True
    assert boundary["projection_authority_flags_false"] is True
    assert boundary["navigation_authority_flags_false"] is True
    assert boundary["candidate_no_runtime_mutation"] is True
    assert boundary["candidate_authority_flags_false"] is True
    for key in (
        "runtime_route_changed",
        "storage_write_performed",
        "bridge_append_performed",
        "solver_call_performed",
        "scheduler_enqueue_performed",
        "promotion_performed",
        "gate_skip_performed",
        "network_access_performed",
    ):
        assert boundary[key] is False

    guardrails = report["no_overclaim_guardrails"]
    assert guardrails["not_router_dispatch"] is True
    assert guardrails["not_solver_call"] is True
    assert guardrails["not_production_memory_migration"] is True


def test_render_markdown_carries_shortcut_and_scope_caveats() -> None:
    report = build_memory_palace_shortcut_proof(now_utc=_fixed_now())

    markdown = render_markdown(report)

    assert "V12 Memory Palace Shortcut Proof" in markdown
    assert "ok: `true`" in markdown
    assert "target_node_id: `room.research.pathology`" in markdown
    assert "rank_score: `0.68`" in markdown
    assert "Navigation Index" in markdown
    assert (
        "source_path: `wing.learning / room.learning.cell_imaging`"
        in markdown
    )
    assert (
        "target_path: `wing.research / room.research.pathology`"
        in markdown
    )
    assert "Bypass Analysis" in markdown
    assert "intermediate_hops_skipped: `2`" in markdown
    assert "navigation_index_used: `true`" in markdown
    assert "shortcut_ranked_without_runtime_dispatch: `true`" in markdown
    assert "without traversing every hierarchy hop at runtime" in markdown
    assert "This is not router dispatch" in markdown
    assert "network retrieval" in markdown


def test_cli_json_reports_memory_palace_shortcut_proof() -> None:
    result = _run("--json", "--now", "2026-06-07T16:00:00Z")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["generated_at_utc"] == "2026-06-07T16:00:00Z"
    assert payload["ranked_shortcuts"]["top_candidate"]["target_node_id"] == (
        "room.research.pathology"
    )
    assert payload["navigation_index"]["source_path_node_ids"] == [
        "wing.learning",
        "room.learning.cell_imaging",
    ]
    assert payload["navigation_index"]["target_path_node_ids"] == [
        "wing.research",
        "room.research.pathology",
    ]
    assert payload["bypass_analysis"]["intermediate_hops_skipped"] == 2
    assert payload["bypass_analysis"]["navigation_index_used"] is True
    assert (
        payload["bypass_analysis"]["shortcut_ranked_without_runtime_dispatch"]
        is True
    )
    assert payload["authority_boundary"]["runtime_route_changed"] is False
    assert payload["authority_boundary"]["network_access_performed"] is False


def test_cli_default_outputs_markdown() -> None:
    result = _run("--now", "2026-06-07T16:00:00Z")

    assert result.returncode == 0, result.stderr
    assert "# V12 Memory Palace Shortcut Proof" in result.stdout
    assert "claim_label: `MEASURED_LOCAL_PROJECTION`" in result.stdout
    assert "This is not router dispatch" in result.stdout


def test_cli_rejects_invalid_now() -> None:
    result = _run("--json", "--now", "not-a-date")

    assert result.returncode == 1
    assert "--now must be an ISO-8601 UTC timestamp" in result.stderr


def _fixed_now() -> datetime:
    return datetime(2026, 6, 7, 16, 0, tzinfo=timezone.utc)
