# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from tools.build_memory_palace_hierarchy_map_summary import (
    build_memory_palace_hierarchy_map_summary,
)
from tools.verify_memory_palace_hierarchy_map_summary import (
    VERIFICATION_VERSION,
    verify_memory_palace_hierarchy_map_summary,
)
from waggledance.core.memory_palace import (
    MemoryPlacement,
    PalaceNode,
    PalaceShortcutHint,
    build_memory_palace_projection,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "verify_memory_palace_hierarchy_map_summary.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_verifies_valid_hierarchy_map_summary() -> None:
    verification = verify_memory_palace_hierarchy_map_summary(_valid_summary())

    assert verification["ok"] is True
    assert verification["verification_version"] == VERIFICATION_VERSION
    assert verification["source_summary_version_check"] == "match"
    assert verification["source_claim_label_check"] == "match"
    assert verification["source_summary_ok"] is True
    assert verification["source_of_truth_check"] == "match"
    assert verification["counts_check"] == "match"
    assert verification["roots_check"] == "match"
    assert verification["coverage_check"] == "match"
    assert verification["authority_boundary_check"] == "match"
    assert verification["path_free_check"] == "match"
    assert verification["node_count_checked"] == 6
    assert verification["root_count_checked"] == 2
    assert verification["max_depth_checked"] == 2
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


def test_rejects_header_and_source_blocker_drift() -> None:
    summary = _valid_summary()
    summary["summary_version"] = "wrong"
    summary["claim_label"] = "WRITE_ENABLED"
    summary["ok"] = False
    summary["blockers"] = ["source_failed"]

    verification = verify_memory_palace_hierarchy_map_summary(summary)

    assert verification["ok"] is False
    assert verification["source_summary_version_check"] == "mismatch"
    assert verification["source_claim_label_check"] == "mismatch"
    assert verification["source_summary_ok"] is False
    assert "summary_version_mismatch" in verification["blockers"]
    assert "claim_label_mismatch" in verification["blockers"]
    assert "source_summary_not_ok" in verification["blockers"]
    assert "source_summary_blockers_present" in verification["blockers"]


def test_rejects_count_root_and_coverage_drift() -> None:
    summary = _valid_summary()
    summary["kind_counts"]["room"] = 99
    summary["root_count"] = 3
    summary["roots"][0]["child_count"] = 99
    summary["coverage"]["long_shortcut_hint_count"] = 9
    summary["coverage"]["node_ids_with_shortcuts"] = [
        "room.research.pathology",
        "room.learning.cell_imaging",
    ]

    verification = verify_memory_palace_hierarchy_map_summary(summary)

    assert verification["ok"] is False
    assert verification["counts_check"] == "mismatch"
    assert verification["roots_check"] == "mismatch"
    assert verification["coverage_check"] == "mismatch"
    assert "kind_counts_total_mismatch" in verification["blockers"]
    assert "root_count_mismatch" in verification["blockers"]
    assert "root_0_child_count_mismatch" in verification["blockers"]
    assert (
        "coverage_long_shortcut_count_exceeds_shortcut_count"
        in verification["blockers"]
    )
    assert (
        "coverage_node_ids_with_shortcuts_invalid"
        in verification["blockers"]
    )


def test_rejects_authority_boundary_side_effects_path_free() -> None:
    summary = _valid_summary()
    summary["authority_boundary"]["bridge_append_performed"] = True
    summary["operator_interpretation"] = r"C:\operator\private\palace.json"

    verification = verify_memory_palace_hierarchy_map_summary(summary)
    encoded = json.dumps(verification, sort_keys=True)

    assert verification["ok"] is False
    assert verification["authority_boundary_check"] == "mismatch"
    assert verification["path_free_check"] == "mismatch"
    assert (
        "authority_boundary_bridge_append_performed_not_false"
        in verification["blockers"]
    )
    assert "forbidden_path_marker_present" in verification["blockers"]
    assert r"C:\operator\private\palace.json" not in encoded
    assert "palace.json" not in encoded


def test_rejects_non_finite_payload_and_path_keys_without_echoing_values() -> None:
    summary = _valid_summary()
    summary["coverage"]["placement_count"] = float("nan")
    summary["raw_payload"] = {"secret": "do not echo"}
    summary["source_path"] = r"C:\operator\private\projection.json"

    verification = verify_memory_palace_hierarchy_map_summary(summary)
    encoded = json.dumps(verification, sort_keys=True)

    assert verification["ok"] is False
    assert "summary_contains_non_finite" in verification["blockers"]
    assert "forbidden_payload_key_present" in verification["blockers"]
    assert "forbidden_path_key_present" in verification["blockers"]
    assert "forbidden_path_marker_present" in verification["blockers"]
    assert "do not echo" not in encoded
    assert "projection.json" not in encoded


def test_rejects_dynamic_path_like_values_without_echoing_them() -> None:
    summary = _valid_summary()
    unsafe_value = r"C:\operator\private\palace.json"
    summary["kind_counts"][unsafe_value] = "bad"
    summary["roots"][0]["node_id"] = unsafe_value
    summary["roots"][1]["node_id"] = unsafe_value

    verification = verify_memory_palace_hierarchy_map_summary(summary)
    encoded = json.dumps(verification, sort_keys=True)

    assert verification["ok"] is False
    assert "kind_count_not_nonnegative_int" in verification["blockers"]
    assert "root_1_duplicate_node_id" in verification["blockers"]
    assert "forbidden_path_marker_present" in verification["blockers"]
    assert "operator" not in encoded
    assert "palace.json" not in encoded


def test_cli_json_verifies_summary_path_free(tmp_path: Path) -> None:
    summary_path = tmp_path / "summary.json"
    _write_json(summary_path, _valid_summary())

    result = _run("--summary-json", str(summary_path), "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["node_count_checked"] == 6
    assert str(tmp_path) not in result.stdout
    assert summary_path.name not in result.stdout
    assert str(tmp_path) not in result.stderr
    assert summary_path.name not in result.stderr


def test_cli_rejects_duplicate_json_keys_path_free(tmp_path: Path) -> None:
    summary_path = tmp_path / "unsafe_summary.json"
    summary_path.write_text(
        '{"summary_version":"x","summary_version":"y"}',
        encoding="utf-8",
    )

    result = _run("--summary-json", str(summary_path), "--json")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["blockers"] == [
        "memory_palace_hierarchy_map_summary_verification_failed:"
        "memory_palace_hierarchy_map_summary_json_error"
    ]
    combined = result.stdout + result.stderr
    assert str(tmp_path) not in combined
    assert summary_path.name not in combined


def test_cli_missing_input_is_path_free() -> None:
    missing = Path("C:/operator/private/missing_summary.json")

    result = _run("--summary-json", str(missing), "--json")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["blockers"] == [
        "memory_palace_hierarchy_map_summary_verification_failed:"
        "memory_palace_hierarchy_map_summary_unreadable"
    ]
    combined = result.stdout + result.stderr
    assert str(missing) not in combined
    assert "missing_summary.json" not in combined


def _valid_summary() -> dict[str, object]:
    return build_memory_palace_hierarchy_map_summary(_projection())


def _projection() -> dict[str, object]:
    nodes = [
        PalaceNode(node_id="wing.learning", kind="wing", label="Learning"),
        PalaceNode(
            node_id="room.learning.cell_imaging",
            kind="room",
            label="Cell imaging cases",
            parent_id="wing.learning",
        ),
        PalaceNode(
            node_id="closet.learning.protocols",
            kind="closet",
            label="Protocol notes",
            parent_id="room.learning.cell_imaging",
        ),
        PalaceNode(node_id="wing.research", kind="wing", label="Research"),
        PalaceNode(
            node_id="room.research.pathology",
            kind="room",
            label="Pathology expertise",
            parent_id="wing.research",
        ),
        PalaceNode(
            node_id="room.research.statistics",
            kind="room",
            label="Statistics expertise",
            parent_id="wing.research",
        ),
    ]
    return build_memory_palace_projection(
        nodes,
        placements=[
            MemoryPlacement(
                memory_id="memory.learning.cell_imaging.1",
                palace_node_id="room.learning.cell_imaging",
                confidence=0.8,
                placement_source="manual",
            )
        ],
        shortcuts=[
            PalaceShortcutHint(
                shortcut_id="shortcut.learning.pathology",
                source_node_id="room.learning.cell_imaging",
                target_node_id="room.research.pathology",
                matched_selector_keys=["tags", "vector_kind"],
                matched_values={
                    "tags": ["segmentation"],
                    "vector_kind": ["claim"],
                },
                confidence=0.8,
                hierarchy_hops=3,
            ),
            PalaceShortcutHint(
                shortcut_id="shortcut.learning.statistics",
                source_node_id="room.learning.cell_imaging",
                target_node_id="room.research.statistics",
                matched_selector_keys=["tags"],
                matched_values={"tags": ["segmentation"]},
                confidence=0.6,
                hierarchy_hops=2,
            ),
        ],
    )


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
