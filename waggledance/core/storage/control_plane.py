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
