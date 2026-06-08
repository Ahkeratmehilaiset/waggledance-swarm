# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from tools.build_memory_palace_operator_overview import (
    build_memory_palace_operator_overview,
)
from tools.build_memory_palace_sample_projection import (
    SAMPLE_MEMORY_ID,
    SAMPLE_VERSION,
    build_memory_palace_sample_projection,
    render_markdown,
)
from waggledance.core.memory_palace import (
    MEMORY_PALACE_PROJECTION_SCHEMA_VERSION,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "build_memory_palace_sample_projection.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_builds_projection_only_sample_document() -> None:
    projection = build_memory_palace_sample_projection()

    assert projection["schema_version"] == MEMORY_PALACE_PROJECTION_SCHEMA_VERSION
    assert projection["source_of_truth"] == "projection_only"
    assert projection["runtime_authority"] is False
    assert projection["storage_write_authority"] is False
    assert projection["bridge_write_authority"] is False
    assert projection["gate_skip_authority"] is False
    assert projection["promotion_authority"] is False
    assert len(projection["nodes"]) == 6
    assert len(projection["placements"]) == 1
    assert len(projection["shortcuts"]) == 2
    assert projection["placements"][0]["memory_id"] == SAMPLE_MEMORY_ID

    json.dumps(projection, sort_keys=True, allow_nan=False)


def test_sample_projection_feeds_operator_overview_without_payload_values() -> None:
    projection = build_memory_palace_sample_projection()

    overview = build_memory_palace_operator_overview(projection, [SAMPLE_MEMORY_ID])

    assert overview["ok"] is True
    assert overview["memory_ids"] == [SAMPLE_MEMORY_ID]
    assert overview["hierarchy"]["node_count"] == 6
    assert overview["aggregate"]["total_candidate_count"] == 2
    assert overview["read_path_overview"][0]["top_target_node_id"] == (
        "room.research.pathology"
    )
    target = overview["read_path_overview"][0]["ranked_targets"][0]
    assert target["matched_value_count_by_key"] == {"tags": 1, "vector_kind": 1}
    assert "matched_values" not in target
    assert overview["authority_boundary"]["memory_payload_included"] is False
    assert overview["authority_boundary"]["local_paths_recorded"] is False


def test_markdown_explains_operator_flow_without_local_paths() -> None:
    markdown = render_markdown(build_memory_palace_sample_projection())

    assert "# Memory Palace Sample Projection" in markdown
    assert SAMPLE_VERSION in markdown
    assert SAMPLE_MEMORY_ID in markdown
    assert "build_memory_palace_operator_overview.py" in markdown
    assert "runtime route changes" in markdown
    assert "C:\\" not in markdown
    assert "/workspace" not in markdown


def test_cli_json_outputs_projection_for_operator_overview(tmp_path: Path) -> None:
    result = _run("--json")

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert "C:\\" not in result.stdout
    assert "/workspace" not in result.stdout

    projection = json.loads(result.stdout)
    projection_path = tmp_path / "sample_projection.json"
    projection_path.write_text(result.stdout, encoding="utf-8")
    assert projection["schema_version"] == MEMORY_PALACE_PROJECTION_SCHEMA_VERSION

    overview = build_memory_palace_operator_overview(projection, [SAMPLE_MEMORY_ID])
    assert overview["ok"] is True
    assert str(tmp_path) not in json.dumps(overview, sort_keys=True)


def test_cli_markdown_output_is_inert() -> None:
    result = _run()

    assert result.returncode == 0, result.stderr
    assert "# Memory Palace Sample Projection" in result.stdout
    assert "projection-only and synthetic" in result.stdout
    assert "bridge appends" in result.stdout
    assert "C:\\" not in result.stdout
    assert "/workspace" not in result.stdout
