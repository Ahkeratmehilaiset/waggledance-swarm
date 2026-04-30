# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Runtime dispatcher for the auto-promoted low-risk solver lane.

Precedence in the surrounding routing stack is:

    built-in authoritative solvers   (Layer 3 — unchanged)
            ↓
    auto-promoted low-risk solvers   (THIS DISPATCHER)
            ↓
    LLM fallback                     (Layer 1 — unchanged)

The dispatcher reads the control plane only; it never writes to it
during query handling. Every dispatch increments aggregate counters
through ``ControlPlaneDB.snapshot_autonomy_kpis`` (called periodically,
not per-query, to avoid write amplification at scale).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from waggledance.core.storage.control_plane import (
    ControlPlaneDB,
    SolverArtifactRecord,
)

from .low_risk_policy import LOW_RISK_FAMILY_KINDS, is_low_risk_family
from .solver_executor import (
    ExecutorError,
    UnsupportedFamilyError,
    execute_artifact,
)


@dataclass(frozen=True)
class DispatchQuery:
    family_kind: str
    inputs: Mapping[str, Any]
    solver_name: Optional[str] = None  # if None, pick first auto-promoted


@dataclass(frozen=True)
class DispatchResult:
    matched: bool
    family_kind: str
    solver_id: Optional[int] = None
    solver_name: Optional[str] = None
    artifact_id: Optional[str] = None
    output: Any = None
    reason: str = ""  # 'hit', 'miss_no_solver', 'miss_family_not_low_risk',
                     #  'miss_executor_error', 'miss_unknown_family',
                     #  'miss_solver_not_auto_promoted'
    error: Optional[str] = None


@dataclass
class DispatcherStats:
    hits: int = 0
    misses: int = 0
    miss_reasons: dict[str, int] = field(default_factory=dict)
    per_family_hits: dict[str, int] = field(default_factory=dict)


class LowRiskSolverDispatcher:
    """In-process dispatcher reading auto-promoted artifacts from the CP."""

    def __init__(self, control_plane: ControlPlaneDB) -> None:
        self._cp = control_plane
        self._stats = DispatcherStats()

    @property
    def stats(self) -> DispatcherStats:
        return self._stats

    def reset_stats(self) -> None:
        self._stats = DispatcherStats()

    def dispatch_by_features(
        self,
        family_kind: str,
        features: Mapping[str, Any],
        inputs: Mapping[str, Any],
    ) -> DispatchResult:
        """Capability-aware dispatch (Phase 13).

        Looks up auto-promoted solvers in the family whose recorded
        capability features include ALL the requested features, then
        executes the most recently promoted match. Falls through to a
        ``miss_no_solver`` result if no solver matches — the caller
        then typically falls back to family-FIFO :meth:`dispatch` or
        emits a runtime gap signal.

        Empty ``features`` is rejected explicitly to prevent an
        unbounded scan.
        """

        if not is_low_risk_family(family_kind):
            self._record_miss("miss_family_not_low_risk", family_kind)
            return DispatchResult(
                matched=False, family_kind=family_kind,
                reason="miss_family_not_low_risk",
            )
        if not features:
            self._record_miss("miss_no_features_supplied", family_kind)
            return DispatchResult(
                matched=False, family_kind=family_kind,
                reason="miss_no_features_supplied",
            )
        candidate_ids = self._cp.find_auto_promoted_solvers_by_features(
            family_kind=family_kind,
            features=features,
            limit=1,
        )
        if not candidate_ids:
            self._record_miss("miss_no_solver", family_kind)
            return DispatchResult(
                matched=False, family_kind=family_kind,
                reason="miss_no_solver",
            )
        solver_id = candidate_ids[0]
        artifact_record = self._cp.get_solver_artifact(solver_id)
        if artifact_record is None:
            self._record_miss("miss_no_solver", family_kind)
            return DispatchResult(
                matched=False, family_kind=family_kind,
                reason="miss_no_solver",
            )
        try:
            artifact = json.loads(artifact_record.artifact_json)
        except json.JSONDecodeError as exc:
            self._record_miss("miss_executor_error", family_kind)
            return DispatchResult(
                matched=False, family_kind=family_kind,
                solver_id=solver_id,
                artifact_id=artifact_record.artifact_id,
                reason="miss_executor_error",
                error=f"artifact_json malformed: {exc!s}",
            )
        try:
            output = execute_artifact(artifact, inputs)
        except UnsupportedFamilyError as exc:
            self._record_miss("miss_family_not_low_risk", family_kind)
            return DispatchResult(
                matched=False, family_kind=family_kind,
                solver_id=solver_id,
                artifact_id=artifact_record.artifact_id,
                reason="miss_family_not_low_risk",
                error=str(exc),
            )
        except ExecutorError as exc:
            self._record_miss("miss_executor_error", family_kind)
            return DispatchResult(
                matched=False, family_kind=family_kind,
                solver_id=solver_id,
                artifact_id=artifact_record.artifact_id,
                reason="miss_executor_error",
                error=str(exc),
            )
        solver_row = self._cp._conn.execute(  # type: ignore[attr-defined]
            "SELECT name FROM solvers WHERE id = ?",
            (solver_id,),
        ).fetchone()
        solver_name = (
            str(solver_row["name"]) if solver_row is not None else None
        )
        self._record_hit(family_kind)
        return DispatchResult(
            matched=True,
            family_kind=family_kind,
            solver_id=solver_id,
            solver_name=solver_name,
            artifact_id=artifact_record.artifact_id,
            output=output,
            reason="hit_by_features",
        )

    def dispatch(self, query: DispatchQuery) -> DispatchResult:
        family = query.family_kind
        if not is_low_risk_family(family):
            self._record_miss("miss_family_not_low_risk", family)
            return DispatchResult(
                matched=False,
                family_kind=family,
                reason="miss_family_not_low_risk",
            )

        artifact_record = self._select_artifact(query)
        if artifact_record is None:
            self._record_miss("miss_no_solver", family)
            return DispatchResult(
                matched=False,
                family_kind=family,
                reason="miss_no_solver",
            )

        try:
            artifact = json.loads(artifact_record.artifact_json)
        except json.JSONDecodeError as exc:
            self._record_miss("miss_executor_error", family)
            return DispatchResult(
                matched=False,
                family_kind=family,
                solver_id=artifact_record.solver_id,
                artifact_id=artifact_record.artifact_id,
                reason="miss_executor_error",
                error=f"artifact_json malformed: {exc!s}",
            )

        try:
            output = execute_artifact(artifact, query.inputs)
        except UnsupportedFamilyError as exc:
            self._record_miss("miss_family_not_low_risk", family)
            return DispatchResult(
                matched=False,
                family_kind=family,
                solver_id=artifact_record.solver_id,
                artifact_id=artifact_record.artifact_id,
                reason="miss_family_not_low_risk",
                error=str(exc),
            )
        except ExecutorError as exc:
            self._record_miss("miss_executor_error", family)
            return DispatchResult(
                matched=False,
                family_kind=family,
                solver_id=artifact_record.solver_id,
                artifact_id=artifact_record.artifact_id,
                reason="miss_executor_error",
                error=str(exc),
            )

        # Resolve solver_name for the result envelope (single fast lookup).
        solver_row = self._cp._conn.execute(  # type: ignore[attr-defined]
            "SELECT name FROM solvers WHERE id = ?",
            (artifact_record.solver_id,),
        ).fetchone()
        solver_name = (
            str(solver_row["name"]) if solver_row is not None else None
        )
        self._record_hit(family)
        return DispatchResult(
            matched=True,
            family_kind=family,
            solver_id=artifact_record.solver_id,
            solver_name=solver_name,
            artifact_id=artifact_record.artifact_id,
            output=output,
            reason="hit",
        )

    def flush_kpi_snapshot(self) -> int:
        """Write the dispatcher's running counters into autonomy_kpis.

        Returns the row id of the snapshot. The caller is responsible
        for deciding cadence — typically once per minute or per N
        dispatches, never per-query.
        """

        snap = self._cp.snapshot_autonomy_kpis(
            dispatcher_hits_total=self._stats.hits,
            dispatcher_misses_total=self._stats.misses,
            per_family_counts=dict(self._stats.per_family_hits),
        )
        return snap.id

    # -- internals -----------------------------------------------------

    def _select_artifact(
        self, query: DispatchQuery
    ) -> Optional[SolverArtifactRecord]:
        if query.solver_name is not None:
            row = self._cp._conn.execute(  # type: ignore[attr-defined]
                """
                SELECT a.*
                FROM solver_artifacts a
                JOIN solvers s ON s.id = a.solver_id
                WHERE s.name = ? AND s.status = 'auto_promoted'
                ORDER BY a.id DESC LIMIT 1
                """,
                (query.solver_name,),
            ).fetchone()
            if row is None:
                return None
            return ControlPlaneDB._row_to_solver_artifact(row)
        # Pick the most recently registered auto-promoted artifact for
        # the family. v1 dispatch is "any qualified solver wins"; future
        # versions can route by capability or capsule context.
        artifacts = self._cp.list_auto_promoted_artifacts_for_family(
            query.family_kind, limit=1
        )
        return artifacts[0] if artifacts else None

    def _record_hit(self, family: str) -> None:
        self._stats.hits += 1
        self._stats.per_family_hits[family] = (
            self._stats.per_family_hits.get(family, 0) + 1
        )

    def _record_miss(self, reason: str, family: str) -> None:
        self._stats.misses += 1
        self._stats.miss_reasons[reason] = (
            self._stats.miss_reasons.get(reason, 0) + 1
        )


__all__ = [
    "DispatchQuery",
    "DispatchResult",
    "DispatcherStats",
    "LowRiskSolverDispatcher",
    "LOW_RISK_FAMILY_KINDS",
]
