# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ReviewToolInputBoundaryCase:
    tool_id: str
    cli_path: Path | None


REVIEW_TOOL_INPUT_BOUNDARY_CASES: tuple[ReviewToolInputBoundaryCase, ...] = (
    ReviewToolInputBoundaryCase(
        tool_id="tools/build_tool_similarity_index.py",
        cli_path=None,
    ),
    ReviewToolInputBoundaryCase(
        tool_id="tools/find_similar_tools.py",
        cli_path=ROOT / "tools" / "find_similar_tools.py",
    ),
)

