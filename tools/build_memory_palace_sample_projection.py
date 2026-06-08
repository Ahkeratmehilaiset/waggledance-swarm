# SPDX-License-Identifier: BUSL-1.1
"""Emit a read-only sample Memory Palace projection for operator demos.

The sample is intentionally synthetic and payload-free. It gives operators a
known-good projection JSON that can be passed directly to the Memory Palace
operator overview CLI. It does not read storage, mutate memory, dispatch
runtime routes, call solvers, enqueue scheduler work, append bridge events,
access the network, promote shortcuts, or grant gate authority.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.memory_palace import (  # noqa: E402
    MemoryPlacement,
    PalaceNode,
    PalaceShortcutHint,
    build_memory_palace_projection,
)


SAMPLE_VERSION = "wd.v12.memory_palace_sample_projection.v0"
SAMPLE_MEMORY_ID = "memory.learning.cell_imaging.1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the raw projection JSON accepted by operator overview.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    projection = build_memory_palace_sample_projection()
    if args.json:
        print(json.dumps(projection, indent=2, sort_keys=True, allow_nan=False))
    else:
        print(render_markdown(projection), end="")
    return 0


def build_memory_palace_sample_projection() -> dict[str, Any]:
    """Return a deterministic, authority-free sample projection."""

    projection = build_memory_palace_projection(
        _sample_nodes(),
        placements=[
            MemoryPlacement(
                memory_id=SAMPLE_MEMORY_ID,
                palace_node_id="room.learning.cell_imaging",
                confidence=0.8,
                placement_source="manual",
                vector_node_id="vector.memory.learning.cell_imaging.1",
                dedup_anchor="sha256:sample_memory_learning_cell_imaging_1",
            ),
        ],
        shortcuts=[
            PalaceShortcutHint(
                shortcut_id="shortcut.imaging.to.pathology",
                source_node_id="room.learning.cell_imaging",
                target_node_id="room.research.pathology",
                matched_selector_keys=("tags", "vector_kind"),
                matched_values={
                    "tags": ("segmentation",),
                    "vector_kind": ("claim",),
                },
                confidence=0.9,
                hierarchy_hops=3,
                rationale=(
                    "Synthetic sample: cell-imaging memories often need "
                    "pathology interpretation."
                ),
            ),
            PalaceShortcutHint(
                shortcut_id="shortcut.imaging.to.statistics",
                source_node_id="room.learning.cell_imaging",
                target_node_id="room.system.statistics",
                matched_selector_keys=("tags",),
                matched_values={"tags": ("segmentation",)},
                confidence=0.7,
                hierarchy_hops=3,
                rationale=(
                    "Synthetic sample: segmentation measurements can need "
                    "statistical checks."
                ),
            ),
        ],
    )
    json.dumps(projection, sort_keys=True, allow_nan=False)
    return projection


def render_markdown(projection: dict[str, Any]) -> str:
    authority = {
        "runtime_authority": projection.get("runtime_authority") is False,
        "storage_write_authority": projection.get("storage_write_authority") is False,
        "bridge_write_authority": projection.get("bridge_write_authority") is False,
        "gate_skip_authority": projection.get("gate_skip_authority") is False,
        "promotion_authority": projection.get("promotion_authority") is False,
    }
    lines = [
        "# Memory Palace Sample Projection",
        "",
        f"- sample_version: `{SAMPLE_VERSION}`",
        f"- schema_version: `{projection['schema_version']}`",
        f"- source_of_truth: `{projection['source_of_truth']}`",
        f"- memory_id: `{SAMPLE_MEMORY_ID}`",
        f"- node_count: `{len(projection['nodes'])}`",
        f"- placement_count: `{len(projection['placements'])}`",
        f"- shortcut_hint_count: `{len(projection['shortcuts'])}`",
        "",
        "## Operator Flow",
        "",
        "1. Emit projection JSON with `--json`.",
        "2. Pass that JSON to `tools/build_memory_palace_operator_overview.py`.",
        "3. Use the sample memory id above for the first overview row.",
        "",
        "## Authority Boundary",
        "",
    ]
    for key, ok in sorted(authority.items()):
        lines.append(f"- {key}_false: `{str(ok).lower()}`")
    lines.extend(
        [
            "",
            "This sample is projection-only and synthetic. It carries no memory "
            "payload values, local paths, runtime route changes, solver calls, "
            "storage writes, bridge appends, scheduler enqueues, promotions, "
            "gate skips, or network retrieval.",
            "",
        ]
    )
    return "\n".join(lines)


def _sample_nodes() -> tuple[PalaceNode, ...]:
    return (
        PalaceNode(
            node_id="wing.learning",
            kind="wing",
            label="Learning",
            selectors={"cell_id": ("learning",)},
            tags=("sample",),
        ),
        PalaceNode(
            node_id="room.learning.cell_imaging",
            kind="room",
            label="Cell imaging cases",
            parent_id="wing.learning",
            selectors={
                "tags": ("segmentation", "microscopy"),
                "vector_kind": ("claim",),
            },
            tags=("sample", "imaging"),
        ),
        PalaceNode(
            node_id="wing.research",
            kind="wing",
            label="Research",
            selectors={"cell_id": ("research",)},
            tags=("sample",),
        ),
        PalaceNode(
            node_id="room.research.pathology",
            kind="room",
            label="Pathology expertise",
            parent_id="wing.research",
            selectors={
                "tags": ("segmentation", "pathology"),
                "vector_kind": ("claim",),
            },
            tags=("sample", "pathology"),
        ),
        PalaceNode(
            node_id="wing.system",
            kind="wing",
            label="System",
            selectors={"cell_id": ("system",)},
            tags=("sample",),
        ),
        PalaceNode(
            node_id="room.system.statistics",
            kind="room",
            label="Statistics expertise",
            parent_id="wing.system",
            selectors={"tags": ("segmentation", "statistics")},
            tags=("sample", "statistics"),
        ),
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
