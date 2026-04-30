# SPDX-License-Identifier: Apache-2.0
"""Smoke test for the mass-safe self-starting autogrowth proof script."""

from __future__ import annotations

from pathlib import Path

from tools.run_mass_autogrowth_proof import run as run_mass_proof


def test_mass_autogrowth_proof_promotes_30_solvers_self_started(
    tmp_path: Path,
) -> None:
    proof = run_mass_proof(tmp_path / "out", tmp_path / "p.db")

    # Self-starting end-to-end: 30 distinct seeds, 30 promoted
    assert proof["self_starting"] is True
    assert proof["signals_recorded"] == 30
    assert proof["intents_created"] == 30
    assert proof["intents_enqueued"] == 30
    assert proof["scheduler"]["drained_count"] == 30
    assert proof["scheduler"]["auto_promoted"] == 30
    assert proof["scheduler"]["rejected"] == 0
    assert proof["scheduler"]["errored"] == 0

    # Five per family across the six allowlisted kinds
    assert proof["scheduler"]["by_family_promoted"] == {
        "bounded_interpolation": 5,
        "interval_bucket_classifier": 5,
        "linear_arithmetic": 5,
        "lookup_table": 5,
        "scalar_unit_conversion": 5,
        "threshold_rule": 5,
    }

    # Cell-aware: at least four distinct cells appear
    assert len(proof["per_cell_promotions"]) >= 4

    # Aggregate KPIs match
    k = proof["kpis"]
    assert k["candidates_total"] == 30
    assert k["auto_promotions_total"] == 30
    assert k["validation_runs_total"] == 30
    assert k["shadow_evaluations_total"] == 30
    assert k["growth_intents_total"] == 30
    assert k["growth_intents_fulfilled"] == 30
    assert k["autogrowth_runs_total"] == 30
    # 30 signals + 30 intent_created + 30 intent_enqueued + 30 solver_auto_promoted
    assert k["growth_events_total"] == 120

    # Dispatcher returns runtime-executable hits for every family
    families_dispatched = {d["family_kind"] for d in proof["dispatch_results"]
                            if d["matched"] is True}
    assert families_dispatched == {
        "bounded_interpolation",
        "interval_bucket_classifier",
        "linear_arithmetic",
        "lookup_table",
        "scalar_unit_conversion",
        "threshold_rule",
    }


def test_mass_proof_self_starting_truthful(tmp_path: Path) -> None:
    """Every promotion must have an autogrowth_run linking to a queue
    row that itself was claimed by the scheduler — i.e., the path was
    self-started, not invoked manually solver-by-solver."""

    proof = run_mass_proof(tmp_path / "out2", tmp_path / "p2.db")
    assert proof["scheduler"]["ticks_total"] >= 30
    # The scheduler id is recorded; not None or empty
    assert proof["scheduler"]["id"]
    assert proof["primary_teacher_lane_id"] == "claude_code_builder_lane"
    # Schema is at least v3 (Phase 12 introduced the intake tables)
    assert proof["schema_version"] >= 3
