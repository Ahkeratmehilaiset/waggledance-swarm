# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from tools.build_memory_palace_hierarchy_map_summary import (
    SUMMARY_VERSION,
    build_memory_palace_hierarchy_map_summary,
    render_markdown,
)
from waggledance.core.memory_palace import (
    MemoryPlacement,
    PalaceNode,
    PalaceShortcutHint,
    build_memory_palace_projection,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "build_memory_palace_hierarchy_map_summary.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_hierarchy_map_summary_reports_roots_coverage_and_boundary() -> None:
    summary = build_memory_palace_hierarchy_map_summary(_projection())

    assert summary["ok"] is True
    assert summary["summary_version"] == SUMMARY_VERSION
    assert summary["claim_label"] == "READ_ONLY_HIERARCHY_MAP"
    assert summary["source_of_truth"] == "projection_only"
    assert summary["node_count"] == 6
    assert summary["root_count"] == 2
    assert summary["max_depth"] == 2
    assert summary["kind_counts"] == {"closet": 1, "room": 3, "wing": 2}

    roots = {root["node_id"]: root for root in summary["roots"]}
    assert roots["wing.learning"]["child_count"] == 1
    assert roots["wing.learning"]["descendant_count"] == 2
    assert roots["wing.learning"]["placement_count"] == 1
    assert roots["wing.learning"]["shortcut_source_count"] == 2
    assert roots["wing.learning"]["shortcut_target_count"] == 0
    assert roots["wing.learning"]["sample_descendant_node_ids"] == [
        "closet.learning.protocols",
        "room.learning.cell_imaging",
    ]
    assert roots["wing.research"]["child_count"] == 2
    assert roots["wing.research"]["descendant_count"] == 2
    assert roots["wing.research"]["placement_count"] == 0
    assert roots["wing.research"]["shortcut_source_count"] == 0
    assert roots["wing.research"]["shortcut_target_count"] == 2

    coverage = summary["coverage"]
    assert coverage["placement_count"] == 1
    assert coverage["node_ids_with_placements"] == [
        "room.learning.cell_imaging"
    ]
    assert coverage["shortcut_hint_count"] == 2
    assert coverage["long_shortcut_hint_count"] == 2
    assert coverage["node_ids_with_shortcuts"] == [
        "room.learning.cell_imaging",
        "room.research.pathology",
        "room.research.statistics",
    ]

    boundary = summary["authority_boundary"]
    assert boundary["read_side_projection_only"] is True
    assert boundary["runtime_route_changed"] is False
    assert boundary["storage_write_performed"] is False
    assert boundary["bridge_append_performed"] is False
    assert boundary["solver_call_performed"] is False
    assert boundary["scheduler_enqueue_performed"] is False
    assert boundary["promotion_performed"] is False
    assert boundary["gate_skip_performed"] is False
    assert boundary["network_access_performed"] is False
    assert boundary["runtime_authority_granted"] is False
    assert boundary["artifact_payloads_included"] is False
    assert boundary["local_paths_recorded"] is False


def test_render_markdown_shows_hierarchy_without_authority_claims() -> None:
    summary = build_memory_palace_hierarchy_map_summary(_projection())

    markdown = render_markdown(summary)

    assert "# Memory Palace Hierarchy Map Summary" in markdown
    assert "ok: `true`" in markdown
    assert "wing.learning" in markdown
    assert "wing.research" in markdown
    assert "long_shortcut_hint_count: `2`" in markdown
    assert "runtime_route_changed: `false`" in markdown
    assert "This is a read-only hierarchy map" in markdown
    assert "solver execution" in markdown


def test_cli_json_and_markdown_outputs(tmp_path: Path) -> None:
    projection_path = tmp_path / "projection.json"
    projection_path.write_text(json.dumps(_projection()), encoding="utf-8")

    json_result = _run("--projection-json", str(projection_path), "--json")

    assert json_result.returncode == 0, json_result.stderr
    payload = json.loads(json_result.stdout)
    assert payload["ok"] is True
    assert payload["coverage"]["shortcut_hint_count"] == 2
    assert payload["authority_boundary"]["bridge_append_performed"] is False

    markdown_result = _run("--projection-json", str(projection_path))

    assert markdown_result.returncode == 0, markdown_result.stderr
    assert "# Memory Palace Hierarchy Map Summary" in markdown_result.stdout
    assert "wing.learning" in markdown_result.stdout


def test_summary_fails_closed_on_malformed_or_unsafe_projection() -> None:
    assert build_memory_palace_hierarchy_map_summary(["not", "object"])[
        "blockers"
    ] == ["projection_not_object"]

    non_finite = _projection()
    non_finite["placements"][0]["confidence"] = float("nan")
    report = build_memory_palace_hierarchy_map_summary(non_finite)
    assert report["ok"] is False
    assert "projection_contains_non_finite" in report["blockers"]
    assert report["authority_boundary"]["runtime_route_changed"] is False

    unsafe = _projection()
    unsafe["nodes"][0]["metadata"] = {"runtime_authority": True}
    report = build_memory_palace_hierarchy_map_summary(unsafe)
    assert report["ok"] is False
    assert any(
        blocker.startswith("authority_effect_flag_not_false:")
        for blocker in report["blockers"]
    )
    assert report["authority_boundary"]["runtime_authority_granted"] is False


def test_summary_rejects_payload_and_path_markers_without_echo() -> None:
    path_projection = _projection()
    path_projection["nodes"][0]["metadata"] = {
        "payload": "C:\\secret\\case.txt"
    }

    report = build_memory_palace_hierarchy_map_summary(path_projection)

    assert report["ok"] is False
    assert "projection_contains_forbidden_payload_or_path_marker" in report[
        "blockers"
    ]
    encoded = json.dumps(report, sort_keys=True)
    assert "secret" not in encoded
    assert "case.txt" not in encoded

    raw_key_projection = _projection()
    raw_key_projection["nodes"][0]["metadata"] = {
        "/workspace/waggledance/private/raw.json": {"runtime_authority": True}
    }

    report = build_memory_palace_hierarchy_map_summary(raw_key_projection)

    assert report["ok"] is False
    assert "projection_contains_forbidden_payload_or_path_marker" in report[
        "blockers"
    ]
    assert "authority_effect_flag_not_false:runtime_authority" in report[
        "blockers"
    ]
    encoded = json.dumps(report, sort_keys=True)
    assert "workspace" not in encoded
    assert "raw.json" not in encoded


def test_summary_fails_closed_on_unknown_projection_references() -> None:
    unknown_placement = _projection()
    unknown_placement["placements"][0]["palace_node_id"] = "room.missing"
    report = build_memory_palace_hierarchy_map_summary(unknown_placement)
    assert report["ok"] is False
    assert any(
        "projection_validation_failed:placement_references_unknown_palace_node"
        in blocker
        for blocker in report["blockers"]
    )

    unknown_source = _projection()
    unknown_source["shortcuts"][0]["source_node_id"] = "room.missing"
    report = build_memory_palace_hierarchy_map_summary(unknown_source)
    assert report["ok"] is False
    assert any(
        "projection_validation_failed:shortcut_references_unknown_source_node_id"
        in blocker
        for blocker in report["blockers"]
    )

    unknown_target = _projection()
    unknown_target["shortcuts"][0]["target_node_id"] = "room.missing"
    report = build_memory_palace_hierarchy_map_summary(unknown_target)
    assert report["ok"] is False
    assert any(
        "projection_validation_failed:shortcut_references_unknown_target_node_id"
        in blocker
        for blocker in report["blockers"]
    )


def test_cli_fails_closed_on_invalid_json(tmp_path: Path) -> None:
    projection_path = tmp_path / "bad.json"
    projection_path.write_text("{not json", encoding="utf-8")

    result = _run("--projection-json", str(projection_path), "--json")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "projection_json_decode_failed:JSONDecodeError" in payload["blockers"]


def test_cli_rejects_duplicate_json_keys_without_echoing_payload_path(
    tmp_path: Path,
) -> None:
    projection_path = tmp_path / "source_txt.json"
    projection_path.write_text(
        (
            '{"schema_version":"memory_palace_projection.v1",'
            '"schema_version":"memory_palace_projection.v1",'
            '"payload":"C:\\\\secret\\\\case.txt"}'
        ),
        encoding="utf-8",
    )

    result = _run("--projection-json", str(projection_path), "--json")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["blockers"] == ["projection_json_duplicate_key"]
    assert "secret" not in result.stdout
    assert "case.txt" not in result.stdout
    assert "source_txt" not in result.stdout
    assert "secret" not in result.stderr
    assert "case.txt" not in result.stderr


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
