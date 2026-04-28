# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Scale-aware Reality View aggregator (Phase 10 P5).

The Phase 9 :mod:`waggledance.ui.hologram.reality_view` ships an
11-panel structure with a strict never-fabricate invariant
(``available=false`` + ``rationale_if_unavailable`` when an input is
missing). That contract is correct and is preserved unchanged here.

What Phase 10 adds is a **scale-aware** aggregation layer for two of
the panels — ``cell_topology`` and ``builder_lane_status`` plus a new
``solver_family_summary`` panel — that read from the Phase 10 P2
control plane via :class:`RegistryQueries`. At 10k+ solvers, listing
every solver as one item would (a) explode the response size and (b)
implicitly claim "one node per solver" which the Phase 10 prompt P5
forbids.

The aggregator therefore returns:

* per-cell counts, NOT per-solver lists;
* per-family rollups (total, by-status), NOT per-solver lists;
* provider / builder lane queue summaries (counts by status), NOT
  every job;
* an explicit ``"truncated"`` marker when a hard cap is reached.

If the control plane is empty or unavailable the aggregator returns
``available=false`` with a structured rationale — preserving the
never-fabricate invariant.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from waggledance.core.storage import (
    ControlPlaneDB,
    ControlPlaneError,
    RegistryQueries,
)
from .reality_view import RealityPanel


@dataclass(frozen=True)
class ScaleAwarePanels:
    """Bundle of panel constructions a caller can fold into a snapshot."""

    solver_family_summary: RealityPanel
    cell_topology: RealityPanel
    builder_lane_status: RealityPanel
    provider_queue_summary: RealityPanel


_HARD_CAP_PER_PANEL: int = 256


def build_scale_aware_panels(
    *,
    control_plane: Optional[ControlPlaneDB],
    cell_coords: Optional[Sequence[str]] = None,
) -> ScaleAwarePanels:
    """Construct the Phase 10 scale-aware panels from the control plane."""

    if control_plane is None:
        unavailable = "control_plane_db_not_attached"
        return ScaleAwarePanels(
            solver_family_summary=_unavailable("solver_family_summary", "Solver family summary", unavailable),
            cell_topology=_unavailable("cell_topology", "Cell topology", unavailable),
            builder_lane_status=_unavailable("builder_lane_status", "Builder lane status", unavailable),
            provider_queue_summary=_unavailable("provider_queue_summary", "Provider queue summary", unavailable),
        )

    queries = RegistryQueries(control_plane)
    return ScaleAwarePanels(
        solver_family_summary=_solver_family_panel(queries),
        cell_topology=_cell_topology_panel(control_plane, queries, cell_coords),
        builder_lane_status=_builder_lane_panel(control_plane),
        provider_queue_summary=_provider_queue_panel(control_plane),
    )


# -- panel builders -----------------------------------------------------


def _solver_family_panel(queries: RegistryQueries) -> RealityPanel:
    rollups = queries.family_rollups()
    if not rollups:
        return _unavailable(
            "solver_family_summary",
            "Solver family summary",
            "no_solver_families_registered",
        )
    items: list[dict] = []
    for r in rollups[:_HARD_CAP_PER_PANEL]:
        items.append(
            {
                "family": r.family.name,
                "version": r.family.version,
                "status": r.family.status,
                "total_solvers": r.total_solvers,
                "by_status": dict(r.by_status),
            }
        )
    if len(rollups) > _HARD_CAP_PER_PANEL:
        items.append({"truncated": True, "shown": _HARD_CAP_PER_PANEL, "total": len(rollups)})
    return RealityPanel(
        panel_id="solver_family_summary",
        title="Solver family summary",
        available=True,
        items=tuple(items),
    )


def _cell_topology_panel(
    cp: ControlPlaneDB,
    queries: RegistryQueries,
    cell_coords: Optional[Sequence[str]],
) -> RealityPanel:
    # Discover cells either from caller hints or from cell_membership.
    discovered: list[str] = list(cell_coords) if cell_coords else []
    if not discovered:
        with cp._lock:  # noqa: SLF001 — internal compose
            rows = cp._conn.execute(
                "SELECT DISTINCT cell_coord FROM cell_membership ORDER BY cell_coord"
            ).fetchall()
            discovered = [str(r["cell_coord"]) for r in rows]
    if not discovered:
        return _unavailable(
            "cell_topology",
            "Cell topology",
            "no_cells_in_cell_membership_and_no_caller_hints",
        )
    items: list[dict] = []
    for cell in discovered[:_HARD_CAP_PER_PANEL]:
        shards = queries.vector_shards_for_cell(cell)
        with cp._lock:  # noqa: SLF001
            cnt_row = cp._conn.execute(
                "SELECT COUNT(*) AS c FROM cell_membership WHERE cell_coord = ? AND status = 'active'",
                (cell,),
            ).fetchone()
        items.append(
            {
                "cell": cell,
                "active_members": int(cnt_row["c"]) if cnt_row else 0,
                "vector_shard_count": len(shards),
            }
        )
    if len(discovered) > _HARD_CAP_PER_PANEL:
        items.append({"truncated": True, "shown": _HARD_CAP_PER_PANEL, "total": len(discovered)})
    return RealityPanel(
        panel_id="cell_topology",
        title="Cell topology",
        available=True,
        items=tuple(items),
    )


def _builder_lane_panel(cp: ControlPlaneDB) -> RealityPanel:
    with cp._lock:  # noqa: SLF001
        rows = cp._conn.execute(
            "SELECT status, COUNT(*) AS c FROM builder_jobs GROUP BY status ORDER BY status"
        ).fetchall()
    if not rows:
        return _unavailable(
            "builder_lane_status",
            "Builder lane status",
            "no_builder_jobs_recorded_yet",
        )
    items = [{"status": str(r["status"]), "count": int(r["c"])} for r in rows]
    return RealityPanel(
        panel_id="builder_lane_status",
        title="Builder lane status",
        available=True,
        items=tuple(items),
    )


def _provider_queue_panel(cp: ControlPlaneDB) -> RealityPanel:
    with cp._lock:  # noqa: SLF001
        rows = cp._conn.execute(
            "SELECT provider, status, COUNT(*) AS c FROM provider_jobs "
            "GROUP BY provider, status ORDER BY provider, status"
        ).fetchall()
    if not rows:
        return _unavailable(
            "provider_queue_summary",
            "Provider queue summary",
            "no_provider_jobs_recorded_yet",
        )
    items = [
        {"provider": str(r["provider"]), "status": str(r["status"]), "count": int(r["c"])}
        for r in rows
    ]
    return RealityPanel(
        panel_id="provider_queue_summary",
        title="Provider queue summary",
        available=True,
        items=tuple(items),
    )


def _unavailable(panel_id: str, title: str, rationale: str) -> RealityPanel:
    return RealityPanel(
        panel_id=panel_id,
        title=title,
        available=False,
        items=(),
        rationale_if_unavailable=rationale,
    )
