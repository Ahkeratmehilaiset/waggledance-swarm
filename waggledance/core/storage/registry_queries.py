# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Read-side helpers for the control-plane database.

:class:`RegistryQueries` collects the read patterns the rest of the
codebase needs against the control plane: "give me all solvers in
family X", "give me capabilities depended on by solver Y", "what
vector shards exist for cell Z". These are intentionally separated
from :class:`ControlPlaneDB` so that:

* the write API stays focused on shape and integrity,
* read patterns can be optimised independently,
* tests / Reality View / status endpoints can swap a stub or
  read-only proxy in place of the full control-plane.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Mapping, Optional, Sequence

from .control_plane import (
    ControlPlaneDB,
    SolverRecord,
    SolverFamilyRecord,
    CapabilityRecord,
    VectorShardRecord,
    PromotionStateRecord,
    ProviderJobRecord,
)


@dataclass(frozen=True)
class FamilyRollup:
    """Aggregated counts for a solver family — Reality View friendly."""

    family: SolverFamilyRecord
    total_solvers: int
    by_status: Mapping[str, int]


@dataclass(frozen=True)
class CapabilityRollup:
    capability: CapabilityRecord
    providing_solver_count: int
    requires: Sequence[str]


class RegistryQueries:
    def __init__(self, control_plane: ControlPlaneDB) -> None:
        self._cp = control_plane

    # -- solver / family -------------------------------------------------

    def solvers_in_family(self, family_name: str) -> List[SolverRecord]:
        return list(self._cp.iter_solvers(family_name=family_name))

    def family_rollups(self) -> List[FamilyRollup]:
        rollups: List[FamilyRollup] = []
        for family in self._cp.list_solver_families():
            by_status: dict[str, int] = {}
            total = 0
            for sv in self._cp.iter_solvers(family_name=family.name):
                total += 1
                by_status[sv.status] = by_status.get(sv.status, 0) + 1
            rollups.append(
                FamilyRollup(family=family, total_solvers=total, by_status=by_status)
            )
        return rollups

    def total_solver_count(self, *, status: Optional[str] = None) -> int:
        return self._cp.count_solvers(status=status)

    # -- capability ------------------------------------------------------

    def capabilities_provided_by(self, solver_name: str) -> List[CapabilityRecord]:
        with self._cp._lock:  # noqa: SLF001 — internal helper composition
            rows = self._cp._conn.execute(
                """
                SELECT c.* FROM capabilities c
                  JOIN solver_capabilities sc ON sc.capability_id = c.id
                  JOIN solvers s ON s.id = sc.solver_id
                 WHERE s.name = ? AND sc.relation = 'provides'
                 ORDER BY c.name
                """,
                (solver_name,),
            ).fetchall()
            return [self._cp._row_to_capability(r) for r in rows]

    def capability_dependencies(self, capability_name: str) -> List[str]:
        with self._cp._lock:  # noqa: SLF001
            rows = self._cp._conn.execute(
                """
                SELECT cd_target.name AS depends_on
                  FROM capabilities c
                  JOIN capability_dependencies dep ON dep.capability_id = c.id
                  JOIN capabilities cd_target ON cd_target.id = dep.depends_on_capability_id
                 WHERE c.name = ? AND dep.relation = 'requires'
                 ORDER BY cd_target.name
                """,
                (capability_name,),
            ).fetchall()
            return [str(r["depends_on"]) for r in rows]

    def capability_rollups(self) -> List[CapabilityRollup]:
        with self._cp._lock:  # noqa: SLF001
            rows = self._cp._conn.execute(
                """
                SELECT c.*,
                       COALESCE(prov.cnt, 0) AS providing_solver_count
                  FROM capabilities c
                  LEFT JOIN (
                       SELECT capability_id, COUNT(*) AS cnt
                         FROM solver_capabilities
                        WHERE relation = 'provides'
                        GROUP BY capability_id
                  ) prov ON prov.capability_id = c.id
                 ORDER BY c.name
                """
            ).fetchall()
        rollups: List[CapabilityRollup] = []
        for row in rows:
            cap = self._cp._row_to_capability(row)  # noqa: SLF001
            rollups.append(
                CapabilityRollup(
                    capability=cap,
                    providing_solver_count=int(row["providing_solver_count"]),
                    requires=tuple(self.capability_dependencies(cap.name)),
                )
            )
        return rollups

    # -- vector shards ---------------------------------------------------

    def vector_shards_for_cell(self, cell_coord: str) -> List[VectorShardRecord]:
        with self._cp._lock:  # noqa: SLF001
            rows = self._cp._conn.execute(
                """
                SELECT * FROM vector_shards WHERE cell_coord = ? ORDER BY logical_name
                """,
                (cell_coord,),
            ).fetchall()
            return [self._cp._row_to_vector_shard(r) for r in rows]

    def all_vector_shards(self) -> List[VectorShardRecord]:
        with self._cp._lock:  # noqa: SLF001
            rows = self._cp._conn.execute(
                "SELECT * FROM vector_shards ORDER BY logical_name"
            ).fetchall()
            return [self._cp._row_to_vector_shard(r) for r in rows]

    # -- promotion / cutover --------------------------------------------

    def promotion_history(
        self,
        target_kind: str,
        target_id: int,
    ) -> List[PromotionStateRecord]:
        with self._cp._lock:  # noqa: SLF001
            rows = self._cp._conn.execute(
                """
                SELECT * FROM promotion_states
                 WHERE target_kind = ? AND target_id = ?
                 ORDER BY stage, id
                """,
                (target_kind, target_id),
            ).fetchall()
            return [self._cp._row_to_promotion_state(r) for r in rows]

    # -- provider jobs ---------------------------------------------------

    def recent_provider_jobs(
        self,
        *,
        provider: Optional[str] = None,
        section: Optional[str] = None,
        limit: int = 50,
    ) -> List[ProviderJobRecord]:
        wheres: List[str] = []
        params: List[object] = []
        if provider is not None:
            wheres.append("provider = ?")
            params.append(provider)
        if section is not None:
            wheres.append("section = ?")
            params.append(section)
        sql = "SELECT * FROM provider_jobs"
        if wheres:
            sql += " WHERE " + " AND ".join(wheres)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        with self._cp._lock:  # noqa: SLF001
            rows = self._cp._conn.execute(sql, params).fetchall()
            return [self._cp._row_to_provider_job(r) for r in rows]
