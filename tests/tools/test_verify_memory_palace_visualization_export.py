# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from tools.build_memory_palace_sample_projection import (
    build_memory_palace_sample_projection,
)
from tools.build_memory_palace_visualization_export import (
    build_memory_palace_visualization_export,
)
from tools.verify_memory_palace_visualization_export import (
    VERIFICATION_VERSION,
    verify_memory_palace_visualization_export,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "verify_memory_palace_visualization_export.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_verifies_valid_visualization_export() -> None:
    verification = verify_memory_palace_visualization_export(_valid_export())

    assert verification["ok"] is True
    assert verification["verification_version"] == VERIFICATION_VERSION
    assert verification["source_export_version_check"] == "match"
    assert verification["source_claim_label_check"] == "match"
    assert verification["source_export_ok"] is True
    assert verification["source_of_truth_check"] == "match"
    assert verification["source_projection_schema_version_check"] == "match"
    assert verification["layout_check"] == "match"
    assert verification["graph_check"] == "match"
    assert verification["aggregate_check"] == "match"
    assert verification["authority_boundary_check"] == "match"
    assert verification["guardrail_check"] == "match"
    assert verification["path_free_check"] == "match"
    assert verification["node_count_checked"] == 6
    assert verification["edge_count_checked"] == 5
    assert verification["shortcut_edge_count_checked"] == 2
    assert verification["runtime_route_changed"] is False
    assert verification["storage_write_performed"] is False
    assert verification["bridge_append_performed"] is False
    assert verification["solver_call_performed"] is False
    assert verification["scheduler_enqueue_performed"] is False
    assert verification["promotion_performed"] is False
    assert verification["gate_skip_performed"] is False
    assert verification["network_access_performed"] is False
    assert verification["runtime_authority_granted"] is False
    assert verification["artifact_payloads_included"] is False
    assert verification["local_paths_recorded"] is False
    assert verification["blockers"] == []


def test_rejects_header_layout_and_source_blocker_drift() -> None:
    export = _valid_export()
    export["export_version"] = "wrong"
    export["claim_label"] = "WRITE_ENABLED"
    export["ok"] = False
    export["source_of_truth"] = "runtime"
    export["blockers"] = ["source_failed"]
    export["layout_hints"]["coordinates_included"] = True

    verification = verify_memory_palace_visualization_export(export)

    assert verification["ok"] is False
    assert verification["source_export_version_check"] == "mismatch"
    assert verification["source_claim_label_check"] == "mismatch"
    assert verification["source_export_ok"] is False
    assert verification["source_of_truth_check"] == "mismatch"
    assert verification["layout_check"] == "mismatch"
    assert "export_version_mismatch" in verification["blockers"]
    assert "claim_label_mismatch" in verification["blockers"]
    assert "source_export_not_ok" in verification["blockers"]
    assert "source_export_blockers_present" in verification["blockers"]
    assert "layout_coordinates_included_mismatch" in verification["blockers"]


def test_rejects_graph_aggregate_and_guardrail_drift() -> None:
    export = _valid_export()
    export["nodes"][0]["child_count"] = 99
    export["edges"][0]["directed"] = False
    export["aggregate"]["edge_count"] = 99
    export["aggregate"]["node_ids_with_placements"] = [
        "room.system.statistics",
        "room.learning.cell_imaging",
    ]
    export["no_overclaim_guardrails"]["not_gate_skip"] = False

    verification = verify_memory_palace_visualization_export(export)

    assert verification["ok"] is False
    assert verification["graph_check"] == "mismatch"
    assert verification["aggregate_check"] == "mismatch"
    assert verification["guardrail_check"] == "mismatch"
    assert "edge_0_directed_not_true" in verification["blockers"]
    assert "node_child_count_hierarchy_edge_mismatch" in verification["blockers"]
    assert "aggregate_edge_count_mismatch" in verification["blockers"]
    assert "aggregate_node_ids_with_placements_invalid" in verification["blockers"]
    assert "guardrail_not_gate_skip_not_true" in verification["blockers"]


def test_rejects_authority_boundary_side_effects_path_free() -> None:
    export = _valid_export()
    export["authority_boundary"]["bridge_append_performed"] = True
    export["operator_interpretation"] = (
        "/workspace/waggledance-swarm/private/export.json"
    )

    verification = verify_memory_palace_visualization_export(export)
    encoded = json.dumps(verification, sort_keys=True)

    assert verification["ok"] is False
    assert verification["authority_boundary_check"] == "mismatch"
    assert verification["path_free_check"] == "mismatch"
    assert (
        "authority_boundary_bridge_append_performed_not_false"
        in verification["blockers"]
    )
    assert "forbidden_path_marker_present" in verification["blockers"]
    assert "workspace" not in encoded
    assert "export.json" not in encoded


def test_rejects_non_finite_payload_keys_and_dynamic_paths_without_echoing_values() -> None:
    export = _valid_export()
    export["nodes"][0]["placement_count"] = float("nan")
    export["raw_payload"] = {"secret": "do not echo"}
    export["source_path"] = r"C:\operator\private\visualization.json"
    export["edges"][0]["edge_id"] = r"C:\operator\private\edge.json"

    verification = verify_memory_palace_visualization_export(export)
    encoded = json.dumps(verification, sort_keys=True)

    assert verification["ok"] is False
    assert "export_contains_non_finite" in verification["blockers"]
    assert "forbidden_payload_key_present" in verification["blockers"]
    assert "forbidden_path_marker_present" in verification["blockers"]
    assert "edge_0_edge_id_path_marker" in verification["blockers"]
    assert "do not echo" not in encoded
    assert "operator" not in encoded
    assert "visualization.json" not in encoded
    assert "edge.json" not in encoded


def test_rejects_unknown_edge_target_and_shortcut_count_drift() -> None:
    export = _valid_export()
    shortcut_edge = [
        edge for edge in export["edges"] if edge["edge_kind"] == "shortcut"
    ][0]
    shortcut_edge["target_node_id"] = "room.unknown.target"
    shortcut_edge["shortcut_confidence"] = 1.5
    export["nodes"][1]["shortcut_source_count"] = 99

    verification = verify_memory_palace_visualization_export(export)

    assert verification["ok"] is False
    assert "edge_3_target_node_id_unknown" in verification["blockers"]
    assert "edge_3_shortcut_confidence_invalid" in verification["blockers"]
    assert "node_shortcut_source_count_mismatch" in verification["blockers"]


def test_cli_json_verifies_visualization_export_path_free(tmp_path: Path) -> None:
    export_path = tmp_path / "visualization_export.json"
    _write_json(export_path, _valid_export())

    result = _run("--export-json", str(export_path), "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["edge_count_checked"] == 5
    combined = result.stdout + result.stderr
    assert str(tmp_path) not in combined
    assert export_path.name not in combined


def test_cli_rejects_duplicate_json_keys_path_free(tmp_path: Path) -> None:
    export_path = tmp_path / "unsafe_export.json"
    export_path.write_text(
        '{"export_version":"x","export_version":"y"}',
        encoding="utf-8",
    )

    result = _run("--export-json", str(export_path), "--json")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["blockers"] == [
        "memory_palace_visualization_export_verification_failed:"
        "memory_palace_visualization_export_json_error"
    ]
    combined = result.stdout + result.stderr
    assert str(tmp_path) not in combined
    assert export_path.name not in combined


def test_cli_missing_input_is_path_free() -> None:
    missing = Path("C:/operator/private/missing_visualization_export.json")

    result = _run("--export-json", str(missing), "--json")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["blockers"] == [
        "memory_palace_visualization_export_verification_failed:"
        "memory_palace_visualization_export_unreadable"
    ]
    combined = result.stdout + result.stderr
    assert str(missing) not in combined
    assert "missing_visualization_export.json" not in combined


def _valid_export() -> dict[str, object]:
    return build_memory_palace_visualization_export(
        build_memory_palace_sample_projection()
    )


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
