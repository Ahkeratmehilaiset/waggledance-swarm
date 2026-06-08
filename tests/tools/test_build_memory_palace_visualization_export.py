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
    EXPORT_VERSION,
    build_memory_palace_visualization_export,
    render_markdown,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "build_memory_palace_visualization_export.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_builds_projection_only_visualization_graph_without_payload_fields() -> None:
    export = build_memory_palace_visualization_export(
        build_memory_palace_sample_projection()
    )

    assert export["ok"] is True
    assert export["export_version"] == EXPORT_VERSION
    assert export["source_of_truth"] == "projection_only"
    assert export["aggregate"] == {
        "node_count": 6,
        "edge_count": 5,
        "hierarchy_edge_count": 3,
        "shortcut_edge_count": 2,
        "placement_count": 1,
        "root_count": 3,
        "max_depth": 1,
        "node_ids_with_placements": ["room.learning.cell_imaging"],
    }
    assert export["authority_boundary"]["selectors_included"] is False
    assert export["authority_boundary"]["matched_values_included"] is False
    assert export["authority_boundary"]["memory_payload_included"] is False
    assert export["authority_boundary"]["local_paths_recorded"] is False
    assert {node["node_id"] for node in export["nodes"]} == {
        "wing.learning",
        "room.learning.cell_imaging",
        "wing.research",
        "room.research.pathology",
        "wing.system",
        "room.system.statistics",
    }
    shortcut_edges = [
        edge for edge in export["edges"] if edge["edge_kind"] == "shortcut"
    ]
    assert shortcut_edges[0]["source_node_id"] == "room.learning.cell_imaging"
    assert shortcut_edges[0]["target_node_id"] == "room.research.pathology"
    assert shortcut_edges[0]["shortcut_confidence"] == 0.9

    graph_encoded = json.dumps(
        {"nodes": export["nodes"], "edges": export["edges"]},
        sort_keys=True,
        allow_nan=False,
    )
    assert "selectors" not in graph_encoded
    assert "matched_values" not in graph_encoded
    assert "source_refs" not in graph_encoded
    assert "metadata" not in graph_encoded
    assert "C:\\" not in graph_encoded
    assert "/workspace" not in graph_encoded


def test_cli_json_exports_sample_projection_graph(tmp_path: Path) -> None:
    projection_path = tmp_path / "sample_projection.json"
    projection_path.write_text(
        json.dumps(build_memory_palace_sample_projection(), sort_keys=True),
        encoding="utf-8",
    )

    result = _run("--projection-json", str(projection_path), "--json")

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["aggregate"]["edge_count"] == 5
    assert payload["layout_hints"]["shortcut_edge_style"] == "dashed"
    assert str(tmp_path) not in result.stdout


def test_cli_markdown_output_is_inert(tmp_path: Path) -> None:
    projection_path = tmp_path / "sample_projection.json"
    projection_path.write_text(
        json.dumps(build_memory_palace_sample_projection(), sort_keys=True),
        encoding="utf-8",
    )

    result = _run("--projection-json", str(projection_path))

    assert result.returncode == 0, result.stderr
    assert "# Memory Palace Visualization Export" in result.stdout
    assert "shortcut_edge_count" in result.stdout
    assert "not a runtime route" in result.stdout
    assert "C:\\" not in result.stdout
    assert "/workspace" not in result.stdout


def test_duplicate_key_fails_closed_without_echoing_path(tmp_path: Path) -> None:
    projection_path = tmp_path / "source_txt.json"
    projection_path.write_text(
        '{"schema_version":"x","schema_version":"y","payload":"C:\\\\secret\\\\x.txt"}',
        encoding="utf-8",
    )

    result = _run("--projection-json", str(projection_path), "--json")

    assert result.returncode == 1
    assert "projection_json_duplicate_key" in result.stdout
    assert "schema_version:" not in result.stdout
    assert "source_txt" not in result.stdout
    assert "secret" not in result.stdout
    assert "C:\\" not in result.stdout


def test_path_like_duplicate_key_fails_closed_without_echoing_key(
    tmp_path: Path,
) -> None:
    projection_path = tmp_path / "projection.json"
    projection_path.write_text(
        (
            '{"schema_version":"memory_palace_projection.v1",'
            '"/workspace/wd/private/source.txt":"first",'
            '"/workspace/wd/private/source.txt":"second"}'
        ),
        encoding="utf-8",
    )

    result = _run("--projection-json", str(projection_path), "--json")

    assert result.returncode == 1
    assert "projection_json_duplicate_key" in result.stdout
    assert "workspace" not in result.stdout
    assert "private" not in result.stdout
    assert "source.txt" not in result.stdout
    assert "workspace" not in result.stderr
    assert "source.txt" not in result.stderr


def test_payload_or_path_markers_fail_closed_without_leakage() -> None:
    projection = build_memory_palace_sample_projection()
    projection["nodes"][0]["metadata"] = {"payload": "C:\\secret\\case.txt"}

    export = build_memory_palace_visualization_export(projection)

    assert export["ok"] is False
    assert export["nodes"] == []
    assert "projection_contains_forbidden_payload_or_path_marker" in export["blockers"]
    encoded = json.dumps(export, sort_keys=True)
    assert "secret" not in encoded
    assert "case.txt" not in encoded


def test_non_finite_projection_fails_closed() -> None:
    projection = build_memory_palace_sample_projection()
    projection["placements"][0]["confidence"] = float("nan")

    export = build_memory_palace_visualization_export(projection)

    assert export["ok"] is False
    assert "projection_contains_non_finite" in export["blockers"]
