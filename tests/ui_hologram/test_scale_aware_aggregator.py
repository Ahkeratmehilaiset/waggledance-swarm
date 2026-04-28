"""Targeted tests for Phase 10 P5 scale-aware Reality View aggregator."""

from __future__ import annotations

from pathlib import Path

import pytest

from waggledance.core.storage import ControlPlaneDB
from waggledance.ui.hologram.scale_aware_aggregator import (
    build_scale_aware_panels,
)


@pytest.fixture()
def cp(tmp_path: Path) -> ControlPlaneDB:
    db = ControlPlaneDB(db_path=tmp_path / "cp.db")
    yield db
    db.close()


def test_unattached_control_plane_yields_unavailable_panels() -> None:
    panels = build_scale_aware_panels(control_plane=None)
    for p in (
        panels.solver_family_summary,
        panels.cell_topology,
        panels.builder_lane_status,
        panels.provider_queue_summary,
    ):
        assert p.available is False
        assert p.rationale_if_unavailable == "control_plane_db_not_attached"
        assert p.items == ()


def test_empty_control_plane_yields_specific_unavailable_rationales(cp: ControlPlaneDB) -> None:
    panels = build_scale_aware_panels(control_plane=cp)
    assert panels.solver_family_summary.available is False
    assert panels.solver_family_summary.rationale_if_unavailable == "no_solver_families_registered"
    assert panels.cell_topology.available is False
    assert panels.cell_topology.rationale_if_unavailable == (
        "no_cells_in_cell_membership_and_no_caller_hints"
    )
    assert panels.builder_lane_status.available is False
    assert panels.provider_queue_summary.available is False


def test_solver_family_summary_aggregates_counts(cp: ControlPlaneDB) -> None:
    cp.upsert_solver_family("thermal", "1.0.0", description="thermal solvers", status="active")
    for i in range(7):
        cp.upsert_solver(f"solver_t_{i:02d}", "0.1.0", family_name="thermal", status="draft")
    for i in range(3):
        cp.upsert_solver(f"solver_t_promoted_{i}", "0.1.0", family_name="thermal", status="promoted")

    panels = build_scale_aware_panels(control_plane=cp)
    fam = panels.solver_family_summary
    assert fam.available is True
    assert len(fam.items) == 1
    item = fam.items[0]
    assert item["family"] == "thermal"
    assert item["total_solvers"] == 10
    assert item["by_status"] == {"draft": 7, "promoted": 3}


def test_cell_topology_uses_caller_hints_when_membership_empty(cp: ControlPlaneDB) -> None:
    panels = build_scale_aware_panels(
        control_plane=cp,
        cell_coords=("thermal", "energy"),
    )
    ct = panels.cell_topology
    assert ct.available is True
    assert {it["cell"] for it in ct.items} == {"thermal", "energy"}
    for it in ct.items:
        assert it["active_members"] == 0
        assert it["vector_shard_count"] == 0


def test_builder_lane_status_groups_by_status(cp: ControlPlaneDB) -> None:
    for _ in range(3):
        cp.record_builder_job(worktree_path="/tmp/wt", branch="phase10-builder/abc", status="queued")
    for _ in range(2):
        cp.record_builder_job(worktree_path="/tmp/wt", branch="phase10-builder/def", status="completed")
    panels = build_scale_aware_panels(control_plane=cp)
    bl = panels.builder_lane_status
    assert bl.available is True
    counts = {it["status"]: it["count"] for it in bl.items}
    assert counts == {"queued": 3, "completed": 2}


def test_provider_queue_summary_groups_by_provider_and_status(cp: ControlPlaneDB) -> None:
    cp.record_provider_job(provider="claude_code_builder_lane", request_kind="code", status="started")
    cp.record_provider_job(provider="claude_code_builder_lane", request_kind="code", status="completed")
    cp.record_provider_job(provider="anthropic_api", request_kind="critique", status="completed")
    panels = build_scale_aware_panels(control_plane=cp)
    pq = panels.provider_queue_summary
    assert pq.available is True
    triples = {(it["provider"], it["status"], it["count"]) for it in pq.items}
    assert ("claude_code_builder_lane", "started", 1) in triples
    assert ("claude_code_builder_lane", "completed", 1) in triples
    assert ("anthropic_api", "completed", 1) in triples


def test_no_one_node_per_solver_at_500_solvers(cp: ControlPlaneDB) -> None:
    """RULE 7 cat 14 — aggregation/virtualization contract.

    With 500 solvers we must still get a single rollup item, not 500
    items in the panel.
    """

    cp.upsert_solver_family("scalar_unit_conversion", "1.0.0", status="active")
    for i in range(500):
        cp.upsert_solver(f"sv_{i:05d}", "0.1.0", family_name="scalar_unit_conversion")
    panels = build_scale_aware_panels(control_plane=cp)
    fam = panels.solver_family_summary
    assert fam.available is True
    assert len(fam.items) == 1
    assert fam.items[0]["total_solvers"] == 500
    # No item carries an individual solver name.
    assert "name" not in fam.items[0]
    assert "solver_id" not in fam.items[0]


def test_panels_serialise_via_to_dict(cp: ControlPlaneDB) -> None:
    cp.upsert_solver_family("thermal", "1.0.0")
    cp.upsert_solver("sv1", "0.1.0", family_name="thermal")
    panels = build_scale_aware_panels(control_plane=cp)
    d = panels.solver_family_summary.to_dict()
    assert d["available"] is True
    assert d["panel_id"] == "solver_family_summary"
    assert isinstance(d["items"], list)
