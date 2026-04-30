# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Control-plane database wrapper.

The :class:`ControlPlaneDB` is a thin SQLite wrapper. It owns:

* schema creation and forward-only migrations,
* high-level CRUD helpers for the entities defined in
  :mod:`waggledance.core.storage.control_plane_schema`,
* connection lifecycle (one connection per instance, closed via
  :py:meth:`close` or context-manager exit).

Design rules:

* The control plane is **not** an audit log. It records *current state*
  (with ``created_at`` / ``updated_at`` columns) but does not promise a
  permanent change history. MAGMA owns history.
* All write methods return the inserted/updated record so callers can
  observe the assigned id and timestamps.
* All read methods return dataclasses, not raw rows.
* Connections are configured with ``foreign_keys=ON`` and WAL journal
  mode so two readers + one writer can coexist.
* No subsystem is required to use the control plane today — this is the
  **substrate**, populated incrementally by P3/P4 and onward.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, List, Mapping, Optional, Sequence

from .control_plane_schema import MIGRATIONS, SCHEMA_VERSION, all_table_names


class ControlPlaneError(RuntimeError):
    """Raised for unrecoverable control-plane errors (RULE 14: fail-loud)."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class SolverFamilyRecord:
    id: int
    name: str
    version: str
    description: Optional[str]
    status: str
    spec_path: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SolverRecord:
    id: int
    family_id: Optional[int]
    name: str
    version: str
    status: str
    spec_hash: Optional[str]
    spec_path: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class CapabilityRecord:
    id: int
    name: str
    version: str
    description: Optional[str]
    created_at: str


@dataclass(frozen=True)
class VectorShardRecord:
    id: int
    logical_name: str
    physical_path: str
    format: str
    embedding_model: Optional[str]
    dimension: Optional[int]
    status: str
    size_bytes: Optional[int]
    cell_coord: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class VectorIndexRecord:
    id: int
    shard_id: int
    index_kind: str
    index_path: str
    dimension: Optional[int]
    status: str
    created_at: str


@dataclass(frozen=True)
class ProviderJobRecord:
    id: int
    provider: str
    request_kind: str
    request_hash: Optional[str]
    request_path: Optional[str]
    result_path: Optional[str]
    status: str
    cost_estimate: Optional[float]
    cost_actual: Optional[float]
    started_at: Optional[str]
    completed_at: Optional[str]
    error: Optional[str]
    section: Optional[str]
    purpose: Optional[str]
    created_at: str


@dataclass(frozen=True)
class BuilderJobRecord:
    id: int
    parent_provider_job_id: Optional[int]
    worktree_path: str
    branch: str
    status: str
    invocation_log_path: Optional[str]
    started_at: Optional[str]
    completed_at: Optional[str]
    error: Optional[str]
    created_at: str


@dataclass(frozen=True)
class PromotionStateRecord:
    id: int
    target_kind: str
    target_id: int
    stage: int
    state: str
    decided_by: Optional[str]
    decided_at: Optional[str]
    evidence: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class CutoverStateRecord:
    id: int
    scope: str
    from_value: Optional[str]
    to_value: Optional[str]
    status: str
    executed_at: Optional[str]
    evidence: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class RuntimePathBinding:
    id: int
    logical_name: str
    path_kind: str
    physical_path: str
    is_active: bool
    bound_at: str
    rebound_at: Optional[str]


# -- schema v3 — Phase 12 self-starting autogrowth intake -----------


@dataclass(frozen=True)
class RuntimeGapSignalRecord:
    id: int
    kind: str
    family_kind: Optional[str]
    cell_coord: Optional[str]
    signal_payload: Optional[str]
    weight: float
    observed_at: str
    created_at: str


@dataclass(frozen=True)
class GrowthIntentRecord:
    id: int
    family_kind: str
    cell_coord: Optional[str]
    intent_key: str
    priority: int
    status: str
    signal_count: int
    last_signal_id: Optional[int]
    spec_seed_json: Optional[str]
    notes: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class AutogrowthQueueRecord:
    id: int
    intent_id: int
    priority: int
    status: str
    claimed_by: Optional[str]
    claimed_at: Optional[str]
    attempt_count: int
    last_error: Optional[str]
    backoff_until: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class AutogrowthRunRecord:
    id: int
    queue_row_id: Optional[int]
    intent_id: Optional[int]
    outcome: str
    promotion_decision_id: Optional[int]
    validation_run_id: Optional[int]
    shadow_evaluation_id: Optional[int]
    family_kind: str
    cell_coord: Optional[str]
    solver_id: Optional[int]
    error: Optional[str]
    evidence: Optional[str]
    started_at: str
    completed_at: str
    created_at: str


@dataclass(frozen=True)
class GrowthEventRecord:
    id: int
    event_kind: str
    entity_kind: Optional[str]
    entity_id: Optional[int]
    family_kind: Optional[str]
    cell_coord: Optional[str]
    payload: Optional[str]
    occurred_at: str
    created_at: str


# -- schema v2 — Phase 11 autonomous low-risk solver growth ---------


@dataclass(frozen=True)
class SolverArtifactRecord:
    id: int
    solver_id: int
    family_kind: str
    artifact_id: str
    spec_canonical_json: str
    artifact_json: str
    created_at: str


@dataclass(frozen=True)
class FamilyPolicyRecord:
    id: int
    family_kind: str
    is_low_risk: bool
    max_auto_promote: int
    min_validation_pass_rate: float
    min_shadow_samples: int
    min_shadow_agreement_rate: float
    notes: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ValidationRunRecord:
    id: int
    solver_id: Optional[int]
    family_kind: str
    spec_hash: Optional[str]
    case_count: int
    pass_count: int
    fail_count: int
    status: str
    evidence: Optional[str]
    started_at: str
    completed_at: Optional[str]
    created_at: str


@dataclass(frozen=True)
class ShadowEvaluationRecord:
    id: int
    solver_id: Optional[int]
    family_kind: str
    spec_hash: Optional[str]
    sample_count: int
    agree_count: int
    disagree_count: int
    agreement_rate: Optional[float]
    oracle_kind: Optional[str]
    status: str
    evidence: Optional[str]
    started_at: str
    completed_at: Optional[str]
    created_at: str


@dataclass(frozen=True)
class PromotionDecisionRecord:
    id: int
    solver_id: int
    family_kind: str
    decision: str
    decided_by: str
    validation_run_id: Optional[int]
    shadow_evaluation_id: Optional[int]
    invariant_failed: Optional[str]
    rollback_reason: Optional[str]
    evidence: Optional[str]
    created_at: str


@dataclass(frozen=True)
class AutonomyKPISnapshot:
    id: int
    snapshot_at: str
    candidates_total: int
    validations_pass_total: int
    validations_fail_total: int
    shadows_pass_total: int
    shadows_fail_total: int
    auto_promotions_total: int
    rejections_total: int
    rollbacks_total: int
    dispatcher_hits_total: int
    dispatcher_misses_total: int
    per_family_counts_json: Optional[str]
    created_at: str


@dataclass
class ControlPlaneStats:
    """Lightweight snapshot used by Reality View / status endpoints."""

    table_counts: Mapping[str, int] = field(default_factory=dict)
    schema_version: int = 0


class ControlPlaneDB:
    """Thin SQLite wrapper for the control-plane database."""

    DEFAULT_DB_PATH: Path = Path("data") / "control_plane.db"

    def __init__(self, db_path: Optional[Path | str] = None) -> None:
        self._db_path: Path = (
            Path(db_path) if db_path is not None else self.DEFAULT_DB_PATH
        )
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self._db_path), check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self.migrate()

    # -- lifecycle -------------------------------------------------------

    def __enter__(self) -> "ControlPlaneDB":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    @property
    def db_path(self) -> Path:
        return self._db_path

    # -- migrations ------------------------------------------------------

    def migrate(self) -> int:
        """Bring the schema up to :data:`SCHEMA_VERSION`. Returns final version."""

        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_meta (
                        key         TEXT PRIMARY KEY,
                        value       TEXT NOT NULL,
                        updated_at  TEXT NOT NULL
                    )
                    """
                )
                row = self._conn.execute(
                    "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                ).fetchone()
                current = int(row["value"]) if row else 0
                target = SCHEMA_VERSION
                if current < target:
                    for version in sorted(v for v in MIGRATIONS if v > current and v <= target):
                        for stmt in MIGRATIONS[version]:
                            self._conn.execute(stmt)
                    now = _utcnow()
                    self._conn.execute(
                        "INSERT OR REPLACE INTO schema_meta(key, value, updated_at) VALUES (?, ?, ?)",
                        ("schema_version", str(target), now),
                    )
                self._conn.execute("COMMIT")
                return target
            except Exception as exc:  # noqa: BLE001 — fail-loud per RULE 14
                self._conn.execute("ROLLBACK")
                raise ControlPlaneError(f"schema migration failed: {exc!r}") from exc

    def schema_version(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            return int(row["value"]) if row else 0

    def stats(self) -> ControlPlaneStats:
        counts: dict[str, int] = {}
        with self._lock:
            for table in all_table_names():
                row = self._conn.execute(
                    f"SELECT COUNT(*) AS c FROM {table}"  # table names are static
                ).fetchone()
                counts[table] = int(row["c"])
            return ControlPlaneStats(table_counts=counts, schema_version=self.schema_version())

    # -- solver families -------------------------------------------------

    def upsert_solver_family(
        self,
        name: str,
        version: str,
        *,
        description: Optional[str] = None,
        status: str = "draft",
        spec_path: Optional[str] = None,
    ) -> SolverFamilyRecord:
        now = _utcnow()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO solver_families(name, version, description, status, spec_path, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    version=excluded.version,
                    description=excluded.description,
                    status=excluded.status,
                    spec_path=excluded.spec_path,
                    updated_at=excluded.updated_at
                """,
                (name, version, description, status, spec_path, now, now),
            )
            return self._fetch_one_solver_family(name)

    def get_solver_family(self, name: str) -> Optional[SolverFamilyRecord]:
        with self._lock:
            return self._fetch_one_solver_family(name, raise_if_missing=False)

    def list_solver_families(self) -> List[SolverFamilyRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM solver_families ORDER BY name"
            ).fetchall()
            return [self._row_to_solver_family(r) for r in rows]

    # -- solvers ---------------------------------------------------------

    def upsert_solver(
        self,
        name: str,
        version: str,
        *,
        family_name: Optional[str] = None,
        status: str = "draft",
        spec_hash: Optional[str] = None,
        spec_path: Optional[str] = None,
    ) -> SolverRecord:
        now = _utcnow()
        with self._lock:
            family_id: Optional[int] = None
            if family_name is not None:
                family = self._fetch_one_solver_family(family_name, raise_if_missing=False)
                if family is None:
                    raise ControlPlaneError(
                        f"unknown solver family {family_name!r} — register it first"
                    )
                family_id = family.id
            self._conn.execute(
                """
                INSERT INTO solvers(family_id, name, version, status, spec_hash, spec_path, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    family_id=excluded.family_id,
                    version=excluded.version,
                    status=excluded.status,
                    spec_hash=excluded.spec_hash,
                    spec_path=excluded.spec_path,
                    updated_at=excluded.updated_at
                """,
                (family_id, name, version, status, spec_hash, spec_path, now, now),
            )
            row = self._conn.execute(
                "SELECT * FROM solvers WHERE name = ?", (name,)
            ).fetchone()
            assert row is not None
            return self._row_to_solver(row)

    def get_solver(self, name: str) -> Optional[SolverRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM solvers WHERE name = ?", (name,)
            ).fetchone()
            return self._row_to_solver(row) if row else None

    def count_solvers(self, *, status: Optional[str] = None) -> int:
        with self._lock:
            if status is None:
                row = self._conn.execute("SELECT COUNT(*) AS c FROM solvers").fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS c FROM solvers WHERE status = ?", (status,)
                ).fetchone()
            return int(row["c"])

    # -- capabilities ----------------------------------------------------

    def upsert_capability(
        self,
        name: str,
        version: str,
        *,
        description: Optional[str] = None,
    ) -> CapabilityRecord:
        now = _utcnow()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO capabilities(name, version, description, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    version=excluded.version,
                    description=excluded.description
                """,
                (name, version, description, now),
            )
            row = self._conn.execute(
                "SELECT * FROM capabilities WHERE name = ?", (name,)
            ).fetchone()
            assert row is not None
            return self._row_to_capability(row)

    def add_capability_dependency(
        self,
        capability: str,
        depends_on: str,
        *,
        relation: str = "requires",
    ) -> None:
        now = _utcnow()
        with self._lock:
            cap = self._conn.execute(
                "SELECT id FROM capabilities WHERE name = ?", (capability,)
            ).fetchone()
            dep = self._conn.execute(
                "SELECT id FROM capabilities WHERE name = ?", (depends_on,)
            ).fetchone()
            if cap is None or dep is None:
                raise ControlPlaneError(
                    f"capability_dependencies: unknown capability "
                    f"{capability if cap is None else depends_on!r}"
                )
            self._conn.execute(
                """
                INSERT OR IGNORE INTO capability_dependencies(capability_id, depends_on_capability_id, relation, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (int(cap["id"]), int(dep["id"]), relation, now),
            )

    def link_solver_capability(
        self,
        solver_name: str,
        capability_name: str,
        *,
        relation: str = "provides",
        confidence: float = 1.0,
    ) -> None:
        now = _utcnow()
        with self._lock:
            sv = self._conn.execute(
                "SELECT id FROM solvers WHERE name = ?", (solver_name,)
            ).fetchone()
            cap = self._conn.execute(
                "SELECT id FROM capabilities WHERE name = ?", (capability_name,)
            ).fetchone()
            if sv is None or cap is None:
                raise ControlPlaneError(
                    f"link_solver_capability: unknown solver or capability "
                    f"{solver_name!r} / {capability_name!r}"
                )
            self._conn.execute(
                """
                INSERT OR REPLACE INTO solver_capabilities(solver_id, capability_id, relation, confidence, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (int(sv["id"]), int(cap["id"]), relation, float(confidence), now),
            )

    # -- vector shards / indexes ----------------------------------------

    def register_vector_shard(
        self,
        logical_name: str,
        physical_path: str,
        *,
        format: str = "faiss",
        embedding_model: Optional[str] = None,
        dimension: Optional[int] = None,
        status: str = "active",
        size_bytes: Optional[int] = None,
        cell_coord: Optional[str] = None,
    ) -> VectorShardRecord:
        now = _utcnow()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO vector_shards(logical_name, physical_path, format, embedding_model, dimension,
                                          status, size_bytes, cell_coord, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(logical_name) DO UPDATE SET
                    physical_path=excluded.physical_path,
                    format=excluded.format,
                    embedding_model=excluded.embedding_model,
                    dimension=excluded.dimension,
                    status=excluded.status,
                    size_bytes=excluded.size_bytes,
                    cell_coord=excluded.cell_coord,
                    updated_at=excluded.updated_at
                """,
                (
                    logical_name,
                    physical_path,
                    format,
                    embedding_model,
                    dimension,
                    status,
                    size_bytes,
                    cell_coord,
                    now,
                    now,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM vector_shards WHERE logical_name = ?", (logical_name,)
            ).fetchone()
            assert row is not None
            return self._row_to_vector_shard(row)

    def get_vector_shard(self, logical_name: str) -> Optional[VectorShardRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM vector_shards WHERE logical_name = ?", (logical_name,)
            ).fetchone()
            return self._row_to_vector_shard(row) if row else None

    def register_vector_index(
        self,
        shard_logical_name: str,
        index_kind: str,
        index_path: str,
        *,
        dimension: Optional[int] = None,
        status: str = "active",
    ) -> VectorIndexRecord:
        now = _utcnow()
        with self._lock:
            shard = self._conn.execute(
                "SELECT id FROM vector_shards WHERE logical_name = ?", (shard_logical_name,)
            ).fetchone()
            if shard is None:
                raise ControlPlaneError(
                    f"register_vector_index: unknown vector_shard {shard_logical_name!r}"
                )
            self._conn.execute(
                """
                INSERT INTO vector_indexes(shard_id, index_kind, index_path, dimension, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(shard_id, index_kind) DO UPDATE SET
                    index_path=excluded.index_path,
                    dimension=excluded.dimension,
                    status=excluded.status
                """,
                (int(shard["id"]), index_kind, index_path, dimension, status, now),
            )
            row = self._conn.execute(
                "SELECT * FROM vector_indexes WHERE shard_id = ? AND index_kind = ?",
                (int(shard["id"]), index_kind),
            ).fetchone()
            assert row is not None
            return self._row_to_vector_index(row)

    # -- provider / builder jobs ----------------------------------------

    def record_provider_job(
        self,
        provider: str,
        request_kind: str,
        *,
        request_hash: Optional[str] = None,
        request_path: Optional[str] = None,
        result_path: Optional[str] = None,
        status: str = "queued",
        cost_estimate: Optional[float] = None,
        section: Optional[str] = None,
        purpose: Optional[str] = None,
    ) -> ProviderJobRecord:
        now = _utcnow()
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO provider_jobs(
                    provider, request_kind, request_hash, request_path, result_path,
                    status, cost_estimate, section, purpose, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    provider,
                    request_kind,
                    request_hash,
                    request_path,
                    result_path,
                    status,
                    cost_estimate,
                    section,
                    purpose,
                    now,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM provider_jobs WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
            assert row is not None
            return self._row_to_provider_job(row)

    def update_provider_job(
        self,
        job_id: int,
        *,
        status: Optional[str] = None,
        cost_actual: Optional[float] = None,
        result_path: Optional[str] = None,
        error: Optional[str] = None,
        started_at: Optional[str] = None,
        completed_at: Optional[str] = None,
    ) -> ProviderJobRecord:
        sets: List[str] = []
        params: List[object] = []
        for col, val in (
            ("status", status),
            ("cost_actual", cost_actual),
            ("result_path", result_path),
            ("error", error),
            ("started_at", started_at),
            ("completed_at", completed_at),
        ):
            if val is not None:
                sets.append(f"{col} = ?")
                params.append(val)
        if not sets:
            with self._lock:
                row = self._conn.execute(
                    "SELECT * FROM provider_jobs WHERE id = ?", (job_id,)
                ).fetchone()
                if row is None:
                    raise ControlPlaneError(f"unknown provider_job {job_id}")
                return self._row_to_provider_job(row)
        params.append(job_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE provider_jobs SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            row = self._conn.execute(
                "SELECT * FROM provider_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise ControlPlaneError(f"unknown provider_job {job_id}")
            return self._row_to_provider_job(row)

    def record_builder_job(
        self,
        worktree_path: str,
        branch: str,
        *,
        parent_provider_job_id: Optional[int] = None,
        invocation_log_path: Optional[str] = None,
        status: str = "queued",
    ) -> BuilderJobRecord:
        now = _utcnow()
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO builder_jobs(
                    parent_provider_job_id, worktree_path, branch,
                    status, invocation_log_path, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    parent_provider_job_id,
                    worktree_path,
                    branch,
                    status,
                    invocation_log_path,
                    now,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM builder_jobs WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
            assert row is not None
            return self._row_to_builder_job(row)

    # -- promotion & cutover --------------------------------------------

    def record_promotion_state(
        self,
        target_kind: str,
        target_id: int,
        stage: int,
        *,
        state: str = "pending",
        decided_by: Optional[str] = None,
        evidence: Optional[Mapping[str, object]] = None,
    ) -> PromotionStateRecord:
        now = _utcnow()
        evidence_json = json.dumps(evidence, sort_keys=True) if evidence is not None else None
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO promotion_states(
                    target_kind, target_id, stage, state, decided_by, decided_at, evidence,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_kind,
                    target_id,
                    stage,
                    state,
                    decided_by,
                    now if state in {"approved", "rejected"} else None,
                    evidence_json,
                    now,
                    now,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM promotion_states WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
            assert row is not None
            return self._row_to_promotion_state(row)

    def record_cutover_state(
        self,
        scope: str,
        *,
        from_value: Optional[str] = None,
        to_value: Optional[str] = None,
        status: str = "pending",
        evidence: Optional[Mapping[str, object]] = None,
    ) -> CutoverStateRecord:
        now = _utcnow()
        evidence_json = json.dumps(evidence, sort_keys=True) if evidence is not None else None
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO cutover_states(
                    scope, from_value, to_value, status, executed_at, evidence,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scope,
                    from_value,
                    to_value,
                    status,
                    now if status == "executed" else None,
                    evidence_json,
                    now,
                    now,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM cutover_states WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
            assert row is not None
            return self._row_to_cutover_state(row)

    # -- runtime path bindings ------------------------------------------

    def bind_runtime_path(
        self,
        logical_name: str,
        path_kind: str,
        physical_path: str,
    ) -> RuntimePathBinding:
        """Bind a logical name to a physical path. Deactivates any previous binding."""

        now = _utcnow()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """
                    UPDATE runtime_path_bindings
                       SET is_active = 0, rebound_at = ?
                     WHERE logical_name = ? AND path_kind = ? AND is_active = 1
                    """,
                    (now, logical_name, path_kind),
                )
                cursor = self._conn.execute(
                    """
                    INSERT INTO runtime_path_bindings(
                        logical_name, path_kind, physical_path, is_active, bound_at, rebound_at
                    )
                    VALUES (?, ?, ?, 1, ?, NULL)
                    """,
                    (logical_name, path_kind, physical_path, now),
                )
                row = self._conn.execute(
                    "SELECT * FROM runtime_path_bindings WHERE id = ?", (cursor.lastrowid,)
                ).fetchone()
                assert row is not None
                self._conn.execute("COMMIT")
                return self._row_to_runtime_path_binding(row)
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def get_active_runtime_path(self, logical_name: str, path_kind: str) -> Optional[RuntimePathBinding]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM runtime_path_bindings
                 WHERE logical_name = ? AND path_kind = ? AND is_active = 1
                """,
                (logical_name, path_kind),
            ).fetchone()
            return self._row_to_runtime_path_binding(row) if row else None

    def list_active_runtime_paths(self) -> List[RuntimePathBinding]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM runtime_path_bindings
                 WHERE is_active = 1
                 ORDER BY path_kind, logical_name
                """
            ).fetchall()
            return [self._row_to_runtime_path_binding(r) for r in rows]

    # -- schema v2: solver artifacts (executable compiled form) --------

    def upsert_solver_artifact(
        self,
        solver_id: int,
        family_kind: str,
        artifact_id: str,
        spec_canonical_json: str,
        artifact_json: str,
    ) -> SolverArtifactRecord:
        now = _utcnow()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO solver_artifacts(
                    solver_id, family_kind, artifact_id,
                    spec_canonical_json, artifact_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(solver_id, artifact_id) DO UPDATE SET
                    family_kind=excluded.family_kind,
                    spec_canonical_json=excluded.spec_canonical_json,
                    artifact_json=excluded.artifact_json
                """,
                (
                    int(solver_id), family_kind, artifact_id,
                    spec_canonical_json, artifact_json, now,
                ),
            )
            row = self._conn.execute(
                """
                SELECT * FROM solver_artifacts
                WHERE solver_id = ? AND artifact_id = ?
                """,
                (int(solver_id), artifact_id),
            ).fetchone()
        return self._row_to_solver_artifact(row)

    def get_solver_artifact(
        self, solver_id: int
    ) -> Optional[SolverArtifactRecord]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM solver_artifacts
                WHERE solver_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (int(solver_id),),
            ).fetchone()
        return None if row is None else self._row_to_solver_artifact(row)

    def list_auto_promoted_artifacts_for_family(
        self, family_kind: str, *, limit: int = 100
    ) -> List[SolverArtifactRecord]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT a.*
                FROM solver_artifacts a
                JOIN solvers s ON s.id = a.solver_id
                WHERE a.family_kind = ?
                  AND s.status = 'auto_promoted'
                ORDER BY a.id DESC
                LIMIT ?
                """,
                (family_kind, int(limit)),
            ).fetchall()
        return [self._row_to_solver_artifact(r) for r in rows]

    # -- schema v2: family policies ------------------------------------

    def upsert_family_policy(
        self,
        family_kind: str,
        *,
        is_low_risk: bool = False,
        max_auto_promote: int = 100,
        min_validation_pass_rate: float = 1.0,
        min_shadow_samples: int = 5,
        min_shadow_agreement_rate: float = 1.0,
        notes: Optional[str] = None,
    ) -> FamilyPolicyRecord:
        now = _utcnow()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO family_policies(
                    family_kind, is_low_risk, max_auto_promote,
                    min_validation_pass_rate, min_shadow_samples,
                    min_shadow_agreement_rate, notes,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(family_kind) DO UPDATE SET
                    is_low_risk=excluded.is_low_risk,
                    max_auto_promote=excluded.max_auto_promote,
                    min_validation_pass_rate=excluded.min_validation_pass_rate,
                    min_shadow_samples=excluded.min_shadow_samples,
                    min_shadow_agreement_rate=excluded.min_shadow_agreement_rate,
                    notes=excluded.notes,
                    updated_at=excluded.updated_at
                """,
                (
                    family_kind, 1 if is_low_risk else 0, max_auto_promote,
                    float(min_validation_pass_rate), int(min_shadow_samples),
                    float(min_shadow_agreement_rate), notes, now, now,
                ),
            )
            return self._fetch_family_policy(family_kind)

    def get_family_policy(self, family_kind: str) -> Optional[FamilyPolicyRecord]:
        with self._lock:
            return self._fetch_family_policy(family_kind, raise_if_missing=False)

    def list_family_policies(
        self, *, low_risk_only: bool = False
    ) -> List[FamilyPolicyRecord]:
        sql = "SELECT * FROM family_policies"
        params: tuple = ()
        if low_risk_only:
            sql += " WHERE is_low_risk = 1"
        sql += " ORDER BY family_kind"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_family_policy(r) for r in rows]

    # -- schema v2: validation runs ------------------------------------

    def record_validation_run(
        self,
        family_kind: str,
        *,
        solver_id: Optional[int] = None,
        spec_hash: Optional[str] = None,
        case_count: int = 0,
        pass_count: int = 0,
        fail_count: int = 0,
        status: str = "completed",
        evidence: Optional[str] = None,
        started_at: Optional[str] = None,
        completed_at: Optional[str] = None,
    ) -> ValidationRunRecord:
        now = _utcnow()
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO validation_runs(
                    solver_id, family_kind, spec_hash,
                    case_count, pass_count, fail_count,
                    status, evidence,
                    started_at, completed_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    solver_id, family_kind, spec_hash,
                    int(case_count), int(pass_count), int(fail_count),
                    status, evidence,
                    started_at or now, completed_at or now, now,
                ),
            )
            new_id = cursor.lastrowid
            row = self._conn.execute(
                "SELECT * FROM validation_runs WHERE id = ?", (new_id,)
            ).fetchone()
            return self._row_to_validation_run(row)

    def get_validation_run(self, run_id: int) -> Optional[ValidationRunRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM validation_runs WHERE id = ?", (run_id,)
            ).fetchone()
        return None if row is None else self._row_to_validation_run(row)

    # -- schema v2: shadow evaluations ----------------------------------

    def record_shadow_evaluation(
        self,
        family_kind: str,
        *,
        solver_id: Optional[int] = None,
        spec_hash: Optional[str] = None,
        sample_count: int = 0,
        agree_count: int = 0,
        disagree_count: int = 0,
        oracle_kind: Optional[str] = None,
        status: str = "completed",
        evidence: Optional[str] = None,
        started_at: Optional[str] = None,
        completed_at: Optional[str] = None,
    ) -> ShadowEvaluationRecord:
        now = _utcnow()
        rate = (agree_count / sample_count) if sample_count > 0 else None
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO shadow_evaluations(
                    solver_id, family_kind, spec_hash,
                    sample_count, agree_count, disagree_count,
                    agreement_rate, oracle_kind, status, evidence,
                    started_at, completed_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    solver_id, family_kind, spec_hash,
                    int(sample_count), int(agree_count), int(disagree_count),
                    rate, oracle_kind, status, evidence,
                    started_at or now, completed_at or now, now,
                ),
            )
            new_id = cursor.lastrowid
            row = self._conn.execute(
                "SELECT * FROM shadow_evaluations WHERE id = ?", (new_id,)
            ).fetchone()
            return self._row_to_shadow_evaluation(row)

    def get_shadow_evaluation(
        self, eval_id: int
    ) -> Optional[ShadowEvaluationRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM shadow_evaluations WHERE id = ?", (eval_id,)
            ).fetchone()
        return None if row is None else self._row_to_shadow_evaluation(row)

    # -- schema v2: promotion decisions ---------------------------------

    def record_promotion_decision(
        self,
        solver_id: int,
        family_kind: str,
        decision: str,
        decided_by: str,
        *,
        validation_run_id: Optional[int] = None,
        shadow_evaluation_id: Optional[int] = None,
        invariant_failed: Optional[str] = None,
        rollback_reason: Optional[str] = None,
        evidence: Optional[str] = None,
    ) -> PromotionDecisionRecord:
        now = _utcnow()
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO promotion_decisions(
                    solver_id, family_kind, decision, decided_by,
                    validation_run_id, shadow_evaluation_id,
                    invariant_failed, rollback_reason, evidence,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(solver_id), family_kind, decision, decided_by,
                    validation_run_id, shadow_evaluation_id,
                    invariant_failed, rollback_reason, evidence, now,
                ),
            )
            new_id = cursor.lastrowid
            row = self._conn.execute(
                "SELECT * FROM promotion_decisions WHERE id = ?", (new_id,)
            ).fetchone()
            return self._row_to_promotion_decision(row)

    def list_promotion_decisions(
        self,
        *,
        solver_id: Optional[int] = None,
        family_kind: Optional[str] = None,
        decision: Optional[str] = None,
        limit: int = 100,
    ) -> List[PromotionDecisionRecord]:
        wheres: List[str] = []
        params: List[object] = []
        if solver_id is not None:
            wheres.append("solver_id = ?")
            params.append(int(solver_id))
        if family_kind is not None:
            wheres.append("family_kind = ?")
            params.append(family_kind)
        if decision is not None:
            wheres.append("decision = ?")
            params.append(decision)
        sql = "SELECT * FROM promotion_decisions"
        if wheres:
            sql += " WHERE " + " AND ".join(wheres)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_promotion_decision(r) for r in rows]

    def count_auto_promoted_for_family(self, family_kind: str) -> int:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM solvers s
                JOIN solver_families f ON f.id = s.family_id
                WHERE f.name = ? AND s.status = 'auto_promoted'
                """,
                (family_kind,),
            ).fetchone()
        return int(row["c"]) if row else 0

    # -- schema v2: autonomy KPIs ---------------------------------------

    def snapshot_autonomy_kpis(
        self,
        *,
        candidates_total: int = 0,
        validations_pass_total: int = 0,
        validations_fail_total: int = 0,
        shadows_pass_total: int = 0,
        shadows_fail_total: int = 0,
        auto_promotions_total: int = 0,
        rejections_total: int = 0,
        rollbacks_total: int = 0,
        dispatcher_hits_total: int = 0,
        dispatcher_misses_total: int = 0,
        per_family_counts: Optional[Mapping[str, int]] = None,
    ) -> AutonomyKPISnapshot:
        now = _utcnow()
        per_family_json: Optional[str] = (
            json.dumps(dict(sorted(per_family_counts.items())))
            if per_family_counts else None
        )
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO autonomy_kpis(
                    snapshot_at,
                    candidates_total,
                    validations_pass_total, validations_fail_total,
                    shadows_pass_total, shadows_fail_total,
                    auto_promotions_total, rejections_total, rollbacks_total,
                    dispatcher_hits_total, dispatcher_misses_total,
                    per_family_counts_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    int(candidates_total),
                    int(validations_pass_total), int(validations_fail_total),
                    int(shadows_pass_total), int(shadows_fail_total),
                    int(auto_promotions_total), int(rejections_total),
                    int(rollbacks_total),
                    int(dispatcher_hits_total),
                    int(dispatcher_misses_total),
                    per_family_json, now,
                ),
            )
            new_id = cursor.lastrowid
            row = self._conn.execute(
                "SELECT * FROM autonomy_kpis WHERE id = ?", (new_id,)
            ).fetchone()
        return self._row_to_autonomy_kpi(row)

    def latest_autonomy_kpi(self) -> Optional[AutonomyKPISnapshot]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM autonomy_kpis ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return None if row is None else self._row_to_autonomy_kpi(row)

    # -- schema v3: runtime gap signals --------------------------------

    def record_runtime_gap_signal(
        self,
        kind: str,
        *,
        family_kind: Optional[str] = None,
        cell_coord: Optional[str] = None,
        signal_payload: Optional[str] = None,
        weight: float = 1.0,
        observed_at: Optional[str] = None,
    ) -> RuntimeGapSignalRecord:
        now = _utcnow()
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO runtime_gap_signals(
                    kind, family_kind, cell_coord, signal_payload,
                    weight, observed_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    kind, family_kind, cell_coord, signal_payload,
                    float(weight), observed_at or now, now,
                ),
            )
            new_id = cursor.lastrowid
            row = self._conn.execute(
                "SELECT * FROM runtime_gap_signals WHERE id = ?", (new_id,)
            ).fetchone()
        return self._row_to_runtime_gap_signal(row)

    def count_runtime_gap_signals(
        self,
        *,
        kind: Optional[str] = None,
        family_kind: Optional[str] = None,
        cell_coord: Optional[str] = None,
    ) -> int:
        wheres: List[str] = []
        params: List[object] = []
        if kind is not None:
            wheres.append("kind = ?")
            params.append(kind)
        if family_kind is not None:
            wheres.append("family_kind = ?")
            params.append(family_kind)
        if cell_coord is not None:
            wheres.append("cell_coord = ?")
            params.append(cell_coord)
        sql = "SELECT COUNT(*) AS c FROM runtime_gap_signals"
        if wheres:
            sql += " WHERE " + " AND ".join(wheres)
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return int(row["c"]) if row else 0

    # -- schema v3: growth intents -------------------------------------

    def upsert_growth_intent(
        self,
        family_kind: str,
        intent_key: str,
        *,
        cell_coord: Optional[str] = None,
        priority: int = 0,
        status: str = "pending",
        signal_count: int = 0,
        last_signal_id: Optional[int] = None,
        spec_seed_json: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> GrowthIntentRecord:
        now = _utcnow()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO growth_intents(
                    family_kind, cell_coord, intent_key,
                    priority, status, signal_count,
                    last_signal_id, spec_seed_json, notes,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(intent_key) DO UPDATE SET
                    family_kind=excluded.family_kind,
                    cell_coord=excluded.cell_coord,
                    priority=MAX(growth_intents.priority, excluded.priority),
                    signal_count=growth_intents.signal_count + 1,
                    last_signal_id=COALESCE(excluded.last_signal_id, growth_intents.last_signal_id),
                    spec_seed_json=COALESCE(excluded.spec_seed_json, growth_intents.spec_seed_json),
                    notes=COALESCE(excluded.notes, growth_intents.notes),
                    updated_at=excluded.updated_at
                """,
                (
                    family_kind, cell_coord, intent_key,
                    int(priority), status, int(signal_count),
                    last_signal_id, spec_seed_json, notes,
                    now, now,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM growth_intents WHERE intent_key = ?",
                (intent_key,),
            ).fetchone()
        return self._row_to_growth_intent(row)

    def set_growth_intent_status(
        self, intent_id: int, status: str
    ) -> Optional[GrowthIntentRecord]:
        now = _utcnow()
        with self._lock:
            self._conn.execute(
                "UPDATE growth_intents SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, int(intent_id)),
            )
            row = self._conn.execute(
                "SELECT * FROM growth_intents WHERE id = ?", (int(intent_id),)
            ).fetchone()
        return None if row is None else self._row_to_growth_intent(row)

    def get_growth_intent(
        self, intent_id: int
    ) -> Optional[GrowthIntentRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM growth_intents WHERE id = ?", (int(intent_id),)
            ).fetchone()
        return None if row is None else self._row_to_growth_intent(row)

    def count_growth_intents(
        self, *, status: Optional[str] = None,
        family_kind: Optional[str] = None,
        cell_coord: Optional[str] = None,
    ) -> int:
        wheres: List[str] = []
        params: List[object] = []
        if status is not None:
            wheres.append("status = ?")
            params.append(status)
        if family_kind is not None:
            wheres.append("family_kind = ?")
            params.append(family_kind)
        if cell_coord is not None:
            wheres.append("cell_coord = ?")
            params.append(cell_coord)
        sql = "SELECT COUNT(*) AS c FROM growth_intents"
        if wheres:
            sql += " WHERE " + " AND ".join(wheres)
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return int(row["c"]) if row else 0

    # -- schema v3: autogrowth queue -----------------------------------

    def enqueue_growth_intent(
        self, intent_id: int, *, priority: int = 0
    ) -> AutogrowthQueueRecord:
        now = _utcnow()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                # Refuse to enqueue twice for the same intent if a
                # queued/claimed row already exists.
                existing = self._conn.execute(
                    """
                    SELECT id FROM autogrowth_queue
                    WHERE intent_id = ? AND status IN ('queued', 'claimed')
                    LIMIT 1
                    """,
                    (int(intent_id),),
                ).fetchone()
                if existing is not None:
                    row = self._conn.execute(
                        "SELECT * FROM autogrowth_queue WHERE id = ?",
                        (existing["id"],),
                    ).fetchone()
                    self._conn.execute("COMMIT")
                    return self._row_to_autogrowth_queue(row)
                cursor = self._conn.execute(
                    """
                    INSERT INTO autogrowth_queue(
                        intent_id, priority, status,
                        attempt_count, created_at, updated_at
                    ) VALUES (?, ?, 'queued', 0, ?, ?)
                    """,
                    (int(intent_id), int(priority), now, now),
                )
                new_id = cursor.lastrowid
                self._conn.execute(
                    "UPDATE growth_intents SET status = 'enqueued', updated_at = ? WHERE id = ?",
                    (now, int(intent_id)),
                )
                row = self._conn.execute(
                    "SELECT * FROM autogrowth_queue WHERE id = ?", (new_id,)
                ).fetchone()
                self._conn.execute("COMMIT")
            except Exception as exc:  # noqa: BLE001
                self._conn.execute("ROLLBACK")
                raise ControlPlaneError(
                    f"enqueue_growth_intent failed: {exc!r}"
                ) from exc
        return self._row_to_autogrowth_queue(row)

    def claim_next_queue_row(
        self, claimer: str, *, now_iso: Optional[str] = None
    ) -> Optional[AutogrowthQueueRecord]:
        """Atomically claim the next 'queued' row whose backoff has expired."""

        now = now_iso or _utcnow()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    """
                    SELECT * FROM autogrowth_queue
                    WHERE status = 'queued'
                      AND (backoff_until IS NULL OR backoff_until <= ?)
                    ORDER BY priority DESC, id ASC
                    LIMIT 1
                    """,
                    (now,),
                ).fetchone()
                if row is None:
                    self._conn.execute("COMMIT")
                    return None
                self._conn.execute(
                    """
                    UPDATE autogrowth_queue
                    SET status = 'claimed',
                        claimed_by = ?,
                        claimed_at = ?,
                        attempt_count = attempt_count + 1,
                        updated_at = ?
                    WHERE id = ? AND status = 'queued'
                    """,
                    (claimer, now, now, int(row["id"])),
                )
                refreshed = self._conn.execute(
                    "SELECT * FROM autogrowth_queue WHERE id = ?",
                    (int(row["id"]),),
                ).fetchone()
                self._conn.execute("COMMIT")
            except Exception as exc:  # noqa: BLE001
                self._conn.execute("ROLLBACK")
                raise ControlPlaneError(
                    f"claim_next_queue_row failed: {exc!r}"
                ) from exc
        return self._row_to_autogrowth_queue(refreshed)

    def complete_queue_row(
        self,
        queue_row_id: int,
        *,
        status: str,
        last_error: Optional[str] = None,
        backoff_seconds: Optional[float] = None,
    ) -> AutogrowthQueueRecord:
        now = _utcnow()
        backoff_iso: Optional[str] = None
        if backoff_seconds is not None and backoff_seconds > 0:
            from datetime import timedelta
            backoff_iso = (
                datetime.now(timezone.utc)
                + timedelta(seconds=float(backoff_seconds))
            ).isoformat(timespec="seconds")
        with self._lock:
            self._conn.execute(
                """
                UPDATE autogrowth_queue
                SET status = ?,
                    last_error = ?,
                    backoff_until = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (status, last_error, backoff_iso, now, int(queue_row_id)),
            )
            row = self._conn.execute(
                "SELECT * FROM autogrowth_queue WHERE id = ?", (int(queue_row_id),)
            ).fetchone()
        return self._row_to_autogrowth_queue(row)

    def list_autogrowth_queue(
        self, *, status: Optional[str] = None, limit: int = 200
    ) -> List[AutogrowthQueueRecord]:
        sql = "SELECT * FROM autogrowth_queue"
        params: List[object] = []
        if status is not None:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY priority DESC, id ASC LIMIT ?"
        params.append(int(limit))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_autogrowth_queue(r) for r in rows]

    def count_queue_rows(self, *, status: Optional[str] = None) -> int:
        sql = "SELECT COUNT(*) AS c FROM autogrowth_queue"
        params: List[object] = []
        if status is not None:
            sql += " WHERE status = ?"
            params.append(status)
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return int(row["c"]) if row else 0

    # -- schema v3: autogrowth runs ------------------------------------

    def record_autogrowth_run(
        self,
        *,
        family_kind: str,
        outcome: str,
        intent_id: Optional[int] = None,
        queue_row_id: Optional[int] = None,
        promotion_decision_id: Optional[int] = None,
        validation_run_id: Optional[int] = None,
        shadow_evaluation_id: Optional[int] = None,
        cell_coord: Optional[str] = None,
        solver_id: Optional[int] = None,
        error: Optional[str] = None,
        evidence: Optional[str] = None,
        started_at: Optional[str] = None,
        completed_at: Optional[str] = None,
    ) -> AutogrowthRunRecord:
        now = _utcnow()
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO autogrowth_runs(
                    queue_row_id, intent_id, outcome,
                    promotion_decision_id, validation_run_id,
                    shadow_evaluation_id,
                    family_kind, cell_coord, solver_id,
                    error, evidence,
                    started_at, completed_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    queue_row_id, intent_id, outcome,
                    promotion_decision_id, validation_run_id,
                    shadow_evaluation_id,
                    family_kind, cell_coord, solver_id,
                    error, evidence,
                    started_at or now, completed_at or now, now,
                ),
            )
            new_id = cursor.lastrowid
            row = self._conn.execute(
                "SELECT * FROM autogrowth_runs WHERE id = ?", (new_id,)
            ).fetchone()
        return self._row_to_autogrowth_run(row)

    def list_autogrowth_runs(
        self,
        *,
        outcome: Optional[str] = None,
        family_kind: Optional[str] = None,
        limit: int = 200,
    ) -> List[AutogrowthRunRecord]:
        wheres: List[str] = []
        params: List[object] = []
        if outcome is not None:
            wheres.append("outcome = ?")
            params.append(outcome)
        if family_kind is not None:
            wheres.append("family_kind = ?")
            params.append(family_kind)
        sql = "SELECT * FROM autogrowth_runs"
        if wheres:
            sql += " WHERE " + " AND ".join(wheres)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_autogrowth_run(r) for r in rows]

    # -- schema v3: growth events (append-only audit mirror) ----------

    def emit_growth_event(
        self,
        event_kind: str,
        *,
        entity_kind: Optional[str] = None,
        entity_id: Optional[int] = None,
        family_kind: Optional[str] = None,
        cell_coord: Optional[str] = None,
        payload: Optional[str] = None,
        occurred_at: Optional[str] = None,
    ) -> GrowthEventRecord:
        now = _utcnow()
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO growth_events(
                    event_kind, entity_kind, entity_id,
                    family_kind, cell_coord, payload,
                    occurred_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_kind, entity_kind, entity_id,
                    family_kind, cell_coord, payload,
                    occurred_at or now, now,
                ),
            )
            new_id = cursor.lastrowid
            row = self._conn.execute(
                "SELECT * FROM growth_events WHERE id = ?", (new_id,)
            ).fetchone()
        return self._row_to_growth_event(row)

    def count_growth_events(
        self, *, event_kind: Optional[str] = None,
        family_kind: Optional[str] = None,
        cell_coord: Optional[str] = None,
    ) -> int:
        wheres: List[str] = []
        params: List[object] = []
        if event_kind is not None:
            wheres.append("event_kind = ?")
            params.append(event_kind)
        if family_kind is not None:
            wheres.append("family_kind = ?")
            params.append(family_kind)
        if cell_coord is not None:
            wheres.append("cell_coord = ?")
            params.append(cell_coord)
        sql = "SELECT COUNT(*) AS c FROM growth_events"
        if wheres:
            sql += " WHERE " + " AND ".join(wheres)
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return int(row["c"]) if row else 0

    # -- iteration -------------------------------------------------------

    def iter_solvers(
        self,
        *,
        status: Optional[str] = None,
        family_name: Optional[str] = None,
        batch_size: int = 1000,
    ) -> Iterator[SolverRecord]:
        sql = ["SELECT s.* FROM solvers s"]
        params: List[object] = []
        joins: List[str] = []
        wheres: List[str] = []
        if family_name is not None:
            joins.append("JOIN solver_families f ON s.family_id = f.id")
            wheres.append("f.name = ?")
            params.append(family_name)
        if status is not None:
            wheres.append("s.status = ?")
            params.append(status)
        if joins:
            sql.append(" ".join(joins))
        if wheres:
            sql.append("WHERE " + " AND ".join(wheres))
        sql.append("ORDER BY s.id")
        with self._lock:
            cursor = self._conn.execute(" ".join(sql), params)
            while True:
                rows = cursor.fetchmany(batch_size)
                if not rows:
                    return
                for r in rows:
                    yield self._row_to_solver(r)

    # -- internal helpers ------------------------------------------------

    def _fetch_one_solver_family(
        self,
        name: str,
        *,
        raise_if_missing: bool = True,
    ) -> Optional[SolverFamilyRecord]:
        row = self._conn.execute(
            "SELECT * FROM solver_families WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            if raise_if_missing:
                raise ControlPlaneError(f"unknown solver_family {name!r}")
            return None
        return self._row_to_solver_family(row)

    @staticmethod
    def _row_to_solver_family(row: sqlite3.Row) -> SolverFamilyRecord:
        return SolverFamilyRecord(
            id=int(row["id"]),
            name=str(row["name"]),
            version=str(row["version"]),
            description=row["description"],
            status=str(row["status"]),
            spec_path=row["spec_path"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _row_to_solver(row: sqlite3.Row) -> SolverRecord:
        return SolverRecord(
            id=int(row["id"]),
            family_id=int(row["family_id"]) if row["family_id"] is not None else None,
            name=str(row["name"]),
            version=str(row["version"]),
            status=str(row["status"]),
            spec_hash=row["spec_hash"],
            spec_path=row["spec_path"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _row_to_capability(row: sqlite3.Row) -> CapabilityRecord:
        return CapabilityRecord(
            id=int(row["id"]),
            name=str(row["name"]),
            version=str(row["version"]),
            description=row["description"],
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _row_to_vector_shard(row: sqlite3.Row) -> VectorShardRecord:
        return VectorShardRecord(
            id=int(row["id"]),
            logical_name=str(row["logical_name"]),
            physical_path=str(row["physical_path"]),
            format=str(row["format"]),
            embedding_model=row["embedding_model"],
            dimension=int(row["dimension"]) if row["dimension"] is not None else None,
            status=str(row["status"]),
            size_bytes=int(row["size_bytes"]) if row["size_bytes"] is not None else None,
            cell_coord=row["cell_coord"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _row_to_vector_index(row: sqlite3.Row) -> VectorIndexRecord:
        return VectorIndexRecord(
            id=int(row["id"]),
            shard_id=int(row["shard_id"]),
            index_kind=str(row["index_kind"]),
            index_path=str(row["index_path"]),
            dimension=int(row["dimension"]) if row["dimension"] is not None else None,
            status=str(row["status"]),
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _row_to_provider_job(row: sqlite3.Row) -> ProviderJobRecord:
        return ProviderJobRecord(
            id=int(row["id"]),
            provider=str(row["provider"]),
            request_kind=str(row["request_kind"]),
            request_hash=row["request_hash"],
            request_path=row["request_path"],
            result_path=row["result_path"],
            status=str(row["status"]),
            cost_estimate=row["cost_estimate"],
            cost_actual=row["cost_actual"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            error=row["error"],
            section=row["section"],
            purpose=row["purpose"],
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _row_to_builder_job(row: sqlite3.Row) -> BuilderJobRecord:
        return BuilderJobRecord(
            id=int(row["id"]),
            parent_provider_job_id=(
                int(row["parent_provider_job_id"])
                if row["parent_provider_job_id"] is not None
                else None
            ),
            worktree_path=str(row["worktree_path"]),
            branch=str(row["branch"]),
            status=str(row["status"]),
            invocation_log_path=row["invocation_log_path"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            error=row["error"],
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _row_to_promotion_state(row: sqlite3.Row) -> PromotionStateRecord:
        return PromotionStateRecord(
            id=int(row["id"]),
            target_kind=str(row["target_kind"]),
            target_id=int(row["target_id"]),
            stage=int(row["stage"]),
            state=str(row["state"]),
            decided_by=row["decided_by"],
            decided_at=row["decided_at"],
            evidence=row["evidence"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _row_to_cutover_state(row: sqlite3.Row) -> CutoverStateRecord:
        return CutoverStateRecord(
            id=int(row["id"]),
            scope=str(row["scope"]),
            from_value=row["from_value"],
            to_value=row["to_value"],
            status=str(row["status"]),
            executed_at=row["executed_at"],
            evidence=row["evidence"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _row_to_runtime_path_binding(row: sqlite3.Row) -> RuntimePathBinding:
        return RuntimePathBinding(
            id=int(row["id"]),
            logical_name=str(row["logical_name"]),
            path_kind=str(row["path_kind"]),
            physical_path=str(row["physical_path"]),
            is_active=bool(int(row["is_active"])),
            bound_at=str(row["bound_at"]),
            rebound_at=row["rebound_at"],
        )

    # -- v2 row converters ---------------------------------------------

    @staticmethod
    def _row_to_solver_artifact(row: sqlite3.Row) -> SolverArtifactRecord:
        return SolverArtifactRecord(
            id=int(row["id"]),
            solver_id=int(row["solver_id"]),
            family_kind=str(row["family_kind"]),
            artifact_id=str(row["artifact_id"]),
            spec_canonical_json=str(row["spec_canonical_json"]),
            artifact_json=str(row["artifact_json"]),
            created_at=str(row["created_at"]),
        )

    def _fetch_family_policy(
        self,
        family_kind: str,
        *,
        raise_if_missing: bool = True,
    ) -> Optional[FamilyPolicyRecord]:
        row = self._conn.execute(
            "SELECT * FROM family_policies WHERE family_kind = ?",
            (family_kind,),
        ).fetchone()
        if row is None:
            if raise_if_missing:
                raise ControlPlaneError(
                    f"unknown family_policy {family_kind!r}"
                )
            return None
        return self._row_to_family_policy(row)

    @staticmethod
    def _row_to_family_policy(row: sqlite3.Row) -> FamilyPolicyRecord:
        return FamilyPolicyRecord(
            id=int(row["id"]),
            family_kind=str(row["family_kind"]),
            is_low_risk=bool(int(row["is_low_risk"])),
            max_auto_promote=int(row["max_auto_promote"]),
            min_validation_pass_rate=float(row["min_validation_pass_rate"]),
            min_shadow_samples=int(row["min_shadow_samples"]),
            min_shadow_agreement_rate=float(row["min_shadow_agreement_rate"]),
            notes=row["notes"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _row_to_validation_run(row: sqlite3.Row) -> ValidationRunRecord:
        return ValidationRunRecord(
            id=int(row["id"]),
            solver_id=(
                int(row["solver_id"]) if row["solver_id"] is not None else None
            ),
            family_kind=str(row["family_kind"]),
            spec_hash=row["spec_hash"],
            case_count=int(row["case_count"]),
            pass_count=int(row["pass_count"]),
            fail_count=int(row["fail_count"]),
            status=str(row["status"]),
            evidence=row["evidence"],
            started_at=str(row["started_at"]),
            completed_at=row["completed_at"],
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _row_to_shadow_evaluation(row: sqlite3.Row) -> ShadowEvaluationRecord:
        return ShadowEvaluationRecord(
            id=int(row["id"]),
            solver_id=(
                int(row["solver_id"]) if row["solver_id"] is not None else None
            ),
            family_kind=str(row["family_kind"]),
            spec_hash=row["spec_hash"],
            sample_count=int(row["sample_count"]),
            agree_count=int(row["agree_count"]),
            disagree_count=int(row["disagree_count"]),
            agreement_rate=(
                float(row["agreement_rate"])
                if row["agreement_rate"] is not None else None
            ),
            oracle_kind=row["oracle_kind"],
            status=str(row["status"]),
            evidence=row["evidence"],
            started_at=str(row["started_at"]),
            completed_at=row["completed_at"],
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _row_to_promotion_decision(row: sqlite3.Row) -> PromotionDecisionRecord:
        return PromotionDecisionRecord(
            id=int(row["id"]),
            solver_id=int(row["solver_id"]),
            family_kind=str(row["family_kind"]),
            decision=str(row["decision"]),
            decided_by=str(row["decided_by"]),
            validation_run_id=(
                int(row["validation_run_id"])
                if row["validation_run_id"] is not None else None
            ),
            shadow_evaluation_id=(
                int(row["shadow_evaluation_id"])
                if row["shadow_evaluation_id"] is not None else None
            ),
            invariant_failed=row["invariant_failed"],
            rollback_reason=row["rollback_reason"],
            evidence=row["evidence"],
            created_at=str(row["created_at"]),
        )

    # -- v3 row converters ---------------------------------------------

    @staticmethod
    def _row_to_runtime_gap_signal(
        row: sqlite3.Row,
    ) -> RuntimeGapSignalRecord:
        return RuntimeGapSignalRecord(
            id=int(row["id"]),
            kind=str(row["kind"]),
            family_kind=row["family_kind"],
            cell_coord=row["cell_coord"],
            signal_payload=row["signal_payload"],
            weight=float(row["weight"]),
            observed_at=str(row["observed_at"]),
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _row_to_growth_intent(row: sqlite3.Row) -> GrowthIntentRecord:
        return GrowthIntentRecord(
            id=int(row["id"]),
            family_kind=str(row["family_kind"]),
            cell_coord=row["cell_coord"],
            intent_key=str(row["intent_key"]),
            priority=int(row["priority"]),
            status=str(row["status"]),
            signal_count=int(row["signal_count"]),
            last_signal_id=(
                int(row["last_signal_id"])
                if row["last_signal_id"] is not None else None
            ),
            spec_seed_json=row["spec_seed_json"],
            notes=row["notes"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _row_to_autogrowth_queue(row: sqlite3.Row) -> AutogrowthQueueRecord:
        return AutogrowthQueueRecord(
            id=int(row["id"]),
            intent_id=int(row["intent_id"]),
            priority=int(row["priority"]),
            status=str(row["status"]),
            claimed_by=row["claimed_by"],
            claimed_at=row["claimed_at"],
            attempt_count=int(row["attempt_count"]),
            last_error=row["last_error"],
            backoff_until=row["backoff_until"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _row_to_autogrowth_run(row: sqlite3.Row) -> AutogrowthRunRecord:
        return AutogrowthRunRecord(
            id=int(row["id"]),
            queue_row_id=(
                int(row["queue_row_id"])
                if row["queue_row_id"] is not None else None
            ),
            intent_id=(
                int(row["intent_id"])
                if row["intent_id"] is not None else None
            ),
            outcome=str(row["outcome"]),
            promotion_decision_id=(
                int(row["promotion_decision_id"])
                if row["promotion_decision_id"] is not None else None
            ),
            validation_run_id=(
                int(row["validation_run_id"])
                if row["validation_run_id"] is not None else None
            ),
            shadow_evaluation_id=(
                int(row["shadow_evaluation_id"])
                if row["shadow_evaluation_id"] is not None else None
            ),
            family_kind=str(row["family_kind"]),
            cell_coord=row["cell_coord"],
            solver_id=(
                int(row["solver_id"]) if row["solver_id"] is not None else None
            ),
            error=row["error"],
            evidence=row["evidence"],
            started_at=str(row["started_at"]),
            completed_at=str(row["completed_at"]),
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _row_to_growth_event(row: sqlite3.Row) -> GrowthEventRecord:
        return GrowthEventRecord(
            id=int(row["id"]),
            event_kind=str(row["event_kind"]),
            entity_kind=row["entity_kind"],
            entity_id=(
                int(row["entity_id"]) if row["entity_id"] is not None else None
            ),
            family_kind=row["family_kind"],
            cell_coord=row["cell_coord"],
            payload=row["payload"],
            occurred_at=str(row["occurred_at"]),
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _row_to_autonomy_kpi(row: sqlite3.Row) -> AutonomyKPISnapshot:
        return AutonomyKPISnapshot(
            id=int(row["id"]),
            snapshot_at=str(row["snapshot_at"]),
            candidates_total=int(row["candidates_total"]),
            validations_pass_total=int(row["validations_pass_total"]),
            validations_fail_total=int(row["validations_fail_total"]),
            shadows_pass_total=int(row["shadows_pass_total"]),
            shadows_fail_total=int(row["shadows_fail_total"]),
            auto_promotions_total=int(row["auto_promotions_total"]),
            rejections_total=int(row["rejections_total"]),
            rollbacks_total=int(row["rollbacks_total"]),
            dispatcher_hits_total=int(row["dispatcher_hits_total"]),
            dispatcher_misses_total=int(row["dispatcher_misses_total"]),
            per_family_counts_json=row["per_family_counts_json"],
            created_at=str(row["created_at"]),
        )
