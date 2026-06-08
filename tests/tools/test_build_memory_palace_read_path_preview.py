# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from tools.build_memory_palace_read_path_preview import (
    PREVIEW_VERSION,
    build_memory_palace_read_path_preview,
    render_markdown,
)
from waggledance.core.memory_palace import (
    MemoryPlacement,
    PalaceNode,
    PalaceShortcutHint,
    build_memory_palace_projection,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "build_memory_palace_read_path_preview.py"
MEMORY_ID = "memory.learning.cell_imaging.1"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_builds_product_read_path_preview_without_authority() -> None:
    preview = build_memory_palace_read_path_preview(_projection(), MEMORY_ID)

    assert preview["ok"] is True
    assert preview["preview_version"] == PREVIEW_VERSION
    assert preview["claim_label"] == "READ_ONLY_MEMORY_PALACE_READ_PATH"
    assert preview["source_of_truth"] == "projection_only"
    assert preview["memory_id"] == MEMORY_ID
    assert preview["source"]["node_id"] == "room.learning.cell_imaging"
    assert preview["source"]["path_node_ids"] == [
        "wing.learning",
        "room.learning.cell_imaging",
    ]
    assert preview["summary"] == {
        "placement_count_for_memory": 1,
        "candidate_count": 2,
        "top_target_node_id": "room.research.pathology",
        "top_rank_score": 0.72,
    }

    top = preview["ranked_read_paths"][0]
    assert top["rank"] == 1
    assert top["target_node_id"] == "room.research.pathology"
    assert top["target"]["path_node_ids"] == [
        "wing.research",
        "room.research.pathology",
    ]
    assert top["rank_score"] == 0.72
    assert top["placement_confidence"] == 0.8
    assert top["shortcut_confidence"] == 0.9
    assert top["hierarchy_hops"] == 3
    assert top["projected_shortcut_hops"] == 1
    assert top["intermediate_hops_skipped"] == 2
    assert top["matched_selector_keys"] == ["tags", "vector_kind"]
    assert top["matched_value_count_by_key"] == {"tags": 1, "vector_kind": 1}
    assert "matched_values" not in top

    boundary = preview["authority_boundary"]
    assert boundary["read_side_projection_only"] is True
    assert boundary["projection_authority_flags_false"] is True
    assert boundary["navigation_authority_flags_false"] is True
    assert boundary["candidate_authority_flags_false"] is True
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


def test_preview_allows_placed_memory_without_shortcuts() -> None:
    projection = build_memory_palace_projection(
        [
            PalaceNode(node_id="wing.learning", kind="wing", label="Learning"),
            PalaceNode(
                node_id="room.learning.notes",
                kind="room",
                label="Notes",
                parent_id="wing.learning",
            ),
        ],
        placements=[
            MemoryPlacement(
                memory_id=MEMORY_ID,
                palace_node_id="room.learning.notes",
                confidence=0.7,
                placement_source="manual",
            )
        ],
    )

    preview = build_memory_palace_read_path_preview(projection, MEMORY_ID)

    assert preview["ok"] is True
    assert preview["source"]["node_id"] == "room.learning.notes"
    assert preview["summary"]["candidate_count"] == 0
    assert preview["ranked_read_paths"] == []


def test_preview_fails_closed_on_missing_or_unsafe_input_without_echo() -> None:
    assert build_memory_palace_read_path_preview(["bad"], MEMORY_ID)["blockers"] == [
        "projection_not_object"
    ]
    assert build_memory_palace_read_path_preview(
        _projection(),
        "/workspace/waggledance/private/memory.json",
    )["blockers"] == ["memory_id_contains_path_marker"]

    unsafe_projection = _projection()
    unsafe_projection["runtime_authority"] = True
    preview = build_memory_palace_read_path_preview(unsafe_projection, MEMORY_ID)
    assert preview["ok"] is False
    assert "authority_effect_flag_not_false:$.runtime_authority" in preview[
        "blockers"
    ]
    assert preview["memory_id"] == ""

    path_projection = _projection()
    path_projection["nodes"][0]["metadata"] = {
        "safe": True,
        "payload_path": "/workspace/waggledance/private/source.txt",
    }
    preview = build_memory_palace_read_path_preview(path_projection, MEMORY_ID)
    assert preview["ok"] is False
    assert "projection_contains_forbidden_payload_or_path_marker" in preview[
        "blockers"
    ]
    serialized = json.dumps(preview, sort_keys=True)
    assert "workspace" not in serialized
    assert "source.txt" not in serialized


def test_preview_fails_closed_on_missing_memory_and_bad_candidate_options() -> None:
    missing = build_memory_palace_read_path_preview(_projection(), "memory.missing")
    assert missing["ok"] is False
    assert missing["blockers"] == ["memory_id_not_placed"]

    bad_max = build_memory_palace_read_path_preview(
        _projection(),
        MEMORY_ID,
        max_candidates=0,
    )
    assert bad_max["ok"] is False
    assert any(
        "projection_validation_failed:max_candidates_must_be_at_least_1"
        in blocker
        for blocker in bad_max["blockers"]
    )

    bad_threshold = build_memory_palace_read_path_preview(
        _projection(),
        MEMORY_ID,
        min_rank_score=float("nan"),
    )
    assert bad_threshold["ok"] is False
    assert any(
        "projection_validation_failed:min_rank_score_must_be_0..1" in blocker
        for blocker in bad_threshold["blockers"]
    )


def test_render_markdown_shows_preview_scope_without_values() -> None:
    preview = build_memory_palace_read_path_preview(_projection(), MEMORY_ID)

    markdown = render_markdown(preview)

    assert "# Memory Palace Read Path Preview" in markdown
    assert "ok: `true`" in markdown
    assert "room.research.pathology" in markdown
    assert "skipped intermediate hops" in markdown
    assert "runtime_route_changed: `false`" in markdown
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
    assert payload["summary"]["top_target_node_id"] == "room.research.pathology"
    assert payload["ranked_read_paths"][0]["intermediate_hops_skipped"] == 2
    assert "matched_values" not in payload["ranked_read_paths"][0]

    markdown_result = _run(
        "--projection-json",
        str(projection_path),
        "--memory-id",
        MEMORY_ID,
    )

    assert markdown_result.returncode == 0, markdown_result.stderr
    assert "# Memory Palace Read Path Preview" in markdown_result.stdout
    assert "room.research.pathology" in markdown_result.stdout


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
        '{"schema_version":"a","schema_version":"b"}',
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
    assert any(
        blocker.startswith("projection_json_duplicate_key:")
        for blocker in payload["blockers"]
    )


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
