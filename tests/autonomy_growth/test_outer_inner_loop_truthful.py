# SPDX-License-Identifier: Apache-2.0
"""Inner-loop = local-first truth (Phase 12 P5).

The Phase 12 mass-safe proof grows 30 deterministic low-risk solvers
end-to-end. The truthful claim from
``docs/architecture/PROVIDER_PLANE_AND_BUILDER_LANES.md`` is that the
*inner growth loop* is local-first: zero provider calls. This test
locks that claim down by running the proof and asserting that the
``provider_jobs`` table stayed empty.

If a future change silently routes inner-loop common-path growth
through ``claude_code_builder_lane`` / Anthropic / OpenAI, this test
fires and the doc claim has to be reconsidered.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.run_mass_autogrowth_proof import run as run_mass_proof
from waggledance.core.solver_synthesis.llm_solver_generator import (
    _DEFAULT_PRIORITY_LIST,
)
from waggledance.core.storage.control_plane import ControlPlaneDB


def test_mass_autogrowth_zero_provider_calls(tmp_path: Path) -> None:
    db_path = tmp_path / "p.db"
    proof = run_mass_proof(tmp_path / "out", db_path)
    assert proof["scheduler"]["auto_promoted"] == 30

    # Inspect the resulting control plane: provider_jobs must be empty
    cp = ControlPlaneDB(db_path)
    try:
        counts = cp.stats().table_counts
        assert counts["provider_jobs"] == 0, (
            "inner-loop must be local-first; a non-zero provider_jobs "
            "count means the mass-safe path now goes through a provider"
        )
        # builder_jobs likewise: no Claude Code subprocess invocations
        assert counts["builder_jobs"] == 0
    finally:
        cp.close()


def test_outer_loop_teacher_lane_remains_claude_code_first() -> None:
    """The outer-loop spec-invention path keeps Claude Code as
    position-1 preferred lane. This test pairs with the inner-loop
    test above: inner is local; outer is Claude-Code-first."""

    assert _DEFAULT_PRIORITY_LIST[0] == "claude_code_builder_lane"
    # The other peer lanes are listed but only dry_run_stub and
    # claude_code_builder_lane are exercisable end-to-end today.
    assert "anthropic_api" in _DEFAULT_PRIORITY_LIST
    assert "gpt_api" in _DEFAULT_PRIORITY_LIST
    assert "local_model_service" in _DEFAULT_PRIORITY_LIST
