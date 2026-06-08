# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from tools.build_memory_palace_operator_overview import (
    OVERVIEW_VERSION,
    build_memory_palace_operator_overview,
    render_markdown,
)
from waggledance.core.memory_palace import (
    MemoryPlacement,
    PalaceNode,
    PalaceShortcutHint,
    build_memory_palace_projection,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "build_memory_palace_operator_overview.py"
MEMORY_ID = "memory.learning.cell_imaging.1"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_builds_operator_overview_from_existing_read_only_helpers() -> None:
    overview = build_memory_palace_operator_overview(_projection(), [MEMORY_ID])

    assert overview["ok"] is True
    assert overview["overview_version"] == OVERVIEW_VERSION
    assert overview["claim_label"] == "READ_ONLY_MEMORY_PALACE_OPERATOR_OVERVIEW"
    assert overview["source_of_truth"] == "projection_only"
    assert overview["memory_ids"] == [MEMORY_ID]
    assert overview["component_versions"] == {
        "hierarchy_map_summary": "wd.v12.memory_palace_hierarchy_map_summary.v0",
        "read_path_preview": "wd.v12.memory_palace_read_path_preview.v0",
    }

    hierarchy = overview["hierarchy"]
    assert hierarchy["node_count"] == 6
    assert hierarchy["root_count"] == 3
    assert hierarchy["coverage"]["placement_count"] == 1
    assert hierarchy["coverage"]["shortcut_hint_count"] == 2

    aggregate = overview["aggregate"]
    assert aggregate["memory_count"] == 1
    assert aggregate["total_candidate_count"] == 2
    assert aggregate["unique_source_node_ids"] == ["room.learning.cell_imaging"]
    assert aggregate["unique_target_node_ids"] == [
        "room.research.pathology",
        "room.system.statistics",
    ]
    assert aggregate["max_intermediate_hops_skipped"] == 2

    row = overview["read_path_overview"][0]
    assert row["memory_id"] == MEMORY_ID
    assert row["source_node_id"] == "room.learning.cell_imaging"
    assert row["source_path_node_ids"] == [
        "wing.learning",
        "room.learning.cell_imaging",
    ]
    assert row["candidate_count"] == 2
    assert row["top_target_node_id"] == "room.research.pathology"
    assert row["top_rank_score"] == 0.72

    target = row["ranked_targets"][0]
    assert target["rank"] == 1
    assert target["shortcut_id"] == "shortcut.imaging.to.pathology"
    assert target["target_node_id"] == "room.research.pathology"
    assert target["target_path_node_ids"] == [
        "wing.research",
        "room.research.pathology",
    ]
    assert target["rank_score"] == 0.72
    assert target["hierarchy_hops"] == 3
    assert target["intermediate_hops_skipped"] == 2
    assert target["matched_selector_keys"] == ["tags", "vector_kind"]
    assert target["matched_value_count_by_key"] == {"tags": 1, "vector_kind": 1}
    assert "matched_values" not in target

    boundary = overview["authority_boundary"]
    assert boundary["read_side_projection_only"] is True
    assert boundary["hierarchy_summary_ok"] is True
    assert boundary["read_path_preview_count"] == 1
    assert boundary["component_authority_boundaries_false"] is True
    assert boundary["memory_payload_included"] is False
    assert boundary["matched_values_included"] is False
    assert boundary["local_paths_recorded"] is False
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


def test_render_markdown_shows_operator_surface_without_payload_values() -> None:
    overview = build_memory_palace_operator_overview(_projection(), [MEMORY_ID])

    markdown = render_markdown(overview)

    assert "# Memory Palace Operator Overview" in markdown
    assert "ok: `true`" in markdown
    assert "room.learning.cell_imaging" in markdown
    assert "room.research.pathology" in markdown
    assert "bridge_append_performed: `false`" in markdown
    assert "not a runtime route" in markdown
    assert "segmentation" not in markdown


def test_cli_json_and_markdown_outputs(tmp_path: Path) -> None:
    projection_path = tmp_path / "projection.json"
    projection_path.write_text(json.dumps(_projection()), encoding="utf-8")

    json_result = _run(
        "--projection-json",
        str(projection_path),
        "--memory-id",
        MEMORY_ID,
        "--json",
    )

    assert json_result.returncode == 0, json_result.stderr
    payload = json.loads(json_result.stdout)
    assert payload["ok"] is True
    assert payload["aggregate"]["total_candidate_count"] == 2
    assert payload["read_path_overview"][0]["top_target_node_id"] == (
        "room.research.pathology"
    )
    assert "matched_values" not in payload["read_path_overview"][0][
        "ranked_targets"
    ][0]
    assert payload["authority_boundary"]["matched_values_included"] is False
    assert str(tmp_path) not in json_result.stdout

    markdown_result = _run(
        "--projection-json",
        str(projection_path),
        "--memory-id",
        MEMORY_ID,
    )

    assert markdown_result.returncode == 0, markdown_result.stderr
    assert "# Memory Palace Operator Overview" in markdown_result.stdout
    assert "room.research.pathology" in markdown_result.stdout


def test_overview_fails_closed_on_invalid_options_without_echoing_memory_id() -> None:
    duplicate = build_memory_palace_operator_overview(
        _projection(),
        [MEMORY_ID, MEMORY_ID],
    )
    assert duplicate["ok"] is False
    assert duplicate["blockers"] == ["memory_ids_not_unique"]
    assert duplicate["memory_ids"] == []

    missing = build_memory_palace_operator_overview(
        _projection(),
        ["memory.missing"],
    )
    assert missing["ok"] is False
    assert missing["blockers"] == [
        "read_path_preview:memory_index_1:memory_id_not_placed"
    ]

    path_like_memory = build_memory_palace_operator_overview(
        _projection(),
        ["/workspace/waggledance/private/memory.json"],
    )
    assert path_like_memory["ok"] is False
    serialized = json.dumps(path_like_memory, sort_keys=True)
    assert "memory_id_contains_path_marker" in serialized
    assert "workspace" not in serialized
    assert "memory.json" not in serialized


def test_cli_fails_closed_on_invalid_json_and_duplicate_keys(tmp_path: Path) -> None:
    bad_path = tmp_path / "bad.json"
    bad_path.write_text("{not json", encoding="utf-8")

    result = _run(
        "--projection-json",
        str(bad_path),
        "--memory-id",
        MEMORY_ID,
        "--json",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "projection_json_decode_failed:JSONDecodeError" in payload["blockers"]

    duplicate_path = tmp_path / "duplicate.json"
    duplicate_path.write_text(
        '{"/workspace/waggledance/private/palace.json":"a",'
        '"/workspace/waggledance/private/palace.json":"b"}',
        encoding="utf-8",
    )

    result = _run(
        "--projection-json",
        str(duplicate_path),
        "--memory-id",
        MEMORY_ID,
        "--json",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["blockers"] == ["projection_json_duplicate_key"]
    assert "workspace" not in result.stdout
    assert "palace.json" not in result.stdout
    assert "workspace" not in result.stderr
    assert "palace.json" not in result.stderr


def test_projection_path_and_authority_markers_fail_closed_without_echo() -> None:
    path_projection = _projection()
    path_projection["nodes"][0]["metadata"] = {
        "payload_path": "/workspace/waggledance/private/source.txt"
    }

    overview = build_memory_palace_operator_overview(path_projection, [MEMORY_ID])

    assert overview["ok"] is False
    serialized = json.dumps(overview, sort_keys=True)
    assert "projection_contains_forbidden_payload_or_path_marker" in serialized
    assert "workspace" not in serialized
    assert "source.txt" not in serialized

    raw_key_projection = _projection()
    raw_key_projection["nodes"][0]["metadata"] = {
        "/workspace/waggledance/private/raw.json": {"runtime_authority": True}
    }

    overview = build_memory_palace_operator_overview(raw_key_projection, [MEMORY_ID])

    assert overview["ok"] is False
    serialized = json.dumps(overview, sort_keys=True)
    assert "authority_effect_flag_not_false:runtime_authority" in serialized
    assert "workspace" not in serialized
    assert "raw.json" not in serialized


def _projection() -> dict[str, object]:
    nodes = [
        PalaceNode(node_id="wing.learning", kind="wing", label="Learning"),
        PalaceNode(
            node_id="room.learning.cell_imaging",
            kind="room",
            label="Cell imaging cases",
            parent_id="wing.learning",
        ),
        PalaceNode(node_id="wing.research", kind="wing", label="Research"),
        PalaceNode(
            node_id="room.research.pathology",
            kind="room",
            label="Pathology expertise",
            parent_id="wing.research",
        ),
        PalaceNode(node_id="wing.system", kind="wing", label="System"),
        PalaceNode(
            node_id="room.system.statistics",
            kind="room",
            label="Statistics expertise",
            parent_id="wing.system",
        ),
    ]
    return build_memory_palace_projection(
        nodes,
        placements=[
            MemoryPlacement(
                memory_id=MEMORY_ID,
                palace_node_id="room.learning.cell_imaging",
                confidence=0.8,
                placement_source="manual",
            )
        ],
        shortcuts=[
            PalaceShortcutHint(
                shortcut_id="shortcut.imaging.to.pathology",
                source_node_id="room.learning.cell_imaging",
                target_node_id="room.research.pathology",
                matched_selector_keys=["tags", "vector_kind"],
                matched_values={
                    "tags": ["segmentation"],
                    "vector_kind": ["claim"],
                },
                confidence=0.9,
                hierarchy_hops=3,
            ),
            PalaceShortcutHint(
                shortcut_id="shortcut.imaging.to.statistics",
                source_node_id="room.learning.cell_imaging",
                target_node_id="room.system.statistics",
                matched_selector_keys=["tags"],
                matched_values={"tags": ["segmentation"]},
                confidence=0.7,
                hierarchy_hops=3,
            ),
        ],
    )
