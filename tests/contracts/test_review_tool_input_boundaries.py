# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from pathlib import Path

import pytest

from .review_tool_input_boundaries_registry import (
    discover_tools_importing_confinement_helpers,
    REVIEW_TOOL_INPUT_BOUNDARY_CASES,
)


@pytest.mark.parametrize("case", REVIEW_TOOL_INPUT_BOUNDARY_CASES, ids=lambda case: case.tool_id)
def test_review_tool_input_boundaries_are_enforced(tmp_path: Path, case) -> None:
    case.validate(tmp_path)


def test_review_tool_input_boundary_registry_covers_all_confinement_importers() -> None:
    registered = {case.tool_id for case in REVIEW_TOOL_INPUT_BOUNDARY_CASES}
    discovered = set(discover_tools_importing_confinement_helpers())
    missing = sorted(discovered - registered)
    assert not missing, (
        "Fail-closed registry is incomplete. Register these modules: "
        + ", ".join(missing)
    )
