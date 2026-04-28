# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Pure SQL schema and migrations for the control-plane database.

The control plane is a single SQLite database. Its purpose is to be the
authoritative metadata store for solvers, families, capabilities, vector
shards, provider/builder jobs, promotion ladder, and runtime path
bindings. It is **not** an audit log (that role belongs to MAGMA in
``waggledance/core/magma/``) and **not** a vector store (that role
belongs to FAISS / Chroma in ``data/faiss/`` and the existing adapters).

Design constraints:

* Every entity has an integer primary key + a ``logical_name`` /
  human-readable identifier. Foreign keys are enforced.
* All ``created_at`` / ``updated_at`` columns store ISO-8601 UTC strings.
* The schema is designed for 10k+ solvers and 100k+ capability edges
  without partitioning. SQLite handles that fine for control metadata.
* Migrations are forward-only. Each migration is a list of SQL
  statements applied in a transaction inside ``ControlPlaneDB.migrate``.
* Schema version is held in ``schema_meta``. Bumping ``SCHEMA_VERSION``
  without adding a corresponding ``MIGRATIONS`` entry is a bug.
"""

from __future__ import annotations

from typing import Dict, List

SCHEMA_VERSION: int = 1

INITIAL_SCHEMA_SQL: List[str] = [
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key         TEXT PRIMARY KEY,
        value       TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS solver_families (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        name         TEXT NOT NULL UNIQUE,
        version      TEXT NOT NULL,
        description  TEXT,
        status       TEXT NOT NULL DEFAULT 'draft',
        spec_path    TEXT,
        created_at   TEXT NOT NULL,
        updated_at   TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS solvers (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        family_id     INTEGER REFERENCES solver_families(id) ON DELETE SET NULL,
        name          TEXT NOT NULL UNIQUE,
        version       TEXT NOT NULL,
        status        TEXT NOT NULL DEFAULT 'draft',
        spec_hash     TEXT,
        spec_path     TEXT,
        created_at    TEXT NOT NULL,
        updated_at    TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS capabilities (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        name         TEXT NOT NULL UNIQUE,
        version      TEXT NOT NULL,
        description  TEXT,
        created_at   TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS capability_dependencies (
        id                          INTEGER PRIMARY KEY AUTOINCREMENT,
        capability_id               INTEGER NOT NULL REFERENCES capabilities(id) ON DELETE CASCADE,
        depends_on_capability_id    INTEGER NOT NULL REFERENCES capabilities(id) ON DELETE CASCADE,
        relation                    TEXT NOT NULL DEFAULT 'requires',
        created_at                  TEXT NOT NULL,
        UNIQUE (capability_id, depends_on_capability_id, relation)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS solver_capabilities (
        solver_id      INTEGER NOT NULL REFERENCES solvers(id) ON DELETE CASCADE,
        capability_id  INTEGER NOT NULL REFERENCES capabilities(id) ON DELETE CASCADE,
        relation       TEXT NOT NULL DEFAULT 'provides',
        confidence     REAL NOT NULL DEFAULT 1.0,
        created_at     TEXT NOT NULL,
        PRIMARY KEY (solver_id, capability_id, relation)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS vector_shards (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        logical_name    TEXT NOT NULL UNIQUE,
        physical_path   TEXT NOT NULL,
        format          TEXT NOT NULL DEFAULT 'faiss',
        embedding_model TEXT,
        dimension       INTEGER,
        status          TEXT NOT NULL DEFAULT 'active',
        size_bytes      INTEGER,
        cell_coord      TEXT,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS vector_indexes (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        shard_id    INTEGER NOT NULL REFERENCES vector_shards(id) ON DELETE CASCADE,
        index_kind  TEXT NOT NULL,
        index_path  TEXT NOT NULL,
        dimension   INTEGER,
        status      TEXT NOT NULL DEFAULT 'active',
        created_at  TEXT NOT NULL,
        UNIQUE (shard_id, index_kind)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS identity_anchors (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        anchor_kind       TEXT NOT NULL,
        anchor_value      TEXT NOT NULL,
        vector_shard_id   INTEGER REFERENCES vector_shards(id) ON DELETE SET NULL,
        created_at        TEXT NOT NULL,
        UNIQUE (anchor_kind, anchor_value)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS provider_jobs (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        provider        TEXT NOT NULL,
        request_kind    TEXT NOT NULL,
        request_hash    TEXT,
        request_path    TEXT,
        result_path     TEXT,
        status          TEXT NOT NULL DEFAULT 'queued',
        cost_estimate   REAL,
        cost_actual     REAL,
        started_at      TEXT,
        completed_at    TEXT,
        error           TEXT,
        section         TEXT,
        purpose         TEXT,
        created_at      TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS builder_jobs (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        parent_provider_job_id  INTEGER REFERENCES provider_jobs(id) ON DELETE SET NULL,
        worktree_path           TEXT NOT NULL,
        branch                  TEXT NOT NULL,
        status                  TEXT NOT NULL DEFAULT 'queued',
        invocation_log_path     TEXT,
        started_at              TEXT,
        completed_at            TEXT,
        error                   TEXT,
        created_at              TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS promotion_states (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        target_kind  TEXT NOT NULL,
        target_id    INTEGER NOT NULL,
        stage        INTEGER NOT NULL,
        state        TEXT NOT NULL DEFAULT 'pending',
        decided_by   TEXT,
        decided_at   TEXT,
        evidence     TEXT,
        created_at   TEXT NOT NULL,
        updated_at   TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_promotion_states_target
        ON promotion_states(target_kind, target_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS cutover_states (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        scope        TEXT NOT NULL,
        from_value   TEXT,
        to_value     TEXT,
        status       TEXT NOT NULL DEFAULT 'pending',
        executed_at  TEXT,
        evidence     TEXT,
        created_at   TEXT NOT NULL,
        updated_at   TEXT NOT NULL,
        UNIQUE (scope, status, executed_at)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runtime_path_bindings (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        logical_name   TEXT NOT NULL,
        path_kind      TEXT NOT NULL,
        physical_path  TEXT NOT NULL,
        is_active      INTEGER NOT NULL DEFAULT 1,
        bound_at       TEXT NOT NULL,
        rebound_at     TEXT,
        UNIQUE (logical_name, path_kind, is_active)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_runtime_path_bindings_kind
        ON runtime_path_bindings(path_kind, is_active)
    """,
    """
    CREATE TABLE IF NOT EXISTS capsule_registry_bindings (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        capsule_id     TEXT NOT NULL,
        capability_id  INTEGER REFERENCES capabilities(id) ON DELETE SET NULL,
        binding_kind   TEXT NOT NULL DEFAULT 'provides',
        status         TEXT NOT NULL DEFAULT 'active',
        created_at     TEXT NOT NULL,
        UNIQUE (capsule_id, capability_id, binding_kind)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cell_membership (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        cell_coord   TEXT NOT NULL,
        member_kind  TEXT NOT NULL,
        member_id    INTEGER NOT NULL,
        status       TEXT NOT NULL DEFAULT 'active',
        joined_at    TEXT NOT NULL,
        left_at      TEXT,
        UNIQUE (cell_coord, member_kind, member_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_cell_membership_kind
        ON cell_membership(member_kind, member_id)
    """,
]


# Forward-only migrations indexed by target schema version.
# Migration N is applied when current schema_version < N. Each migration
# is a list of SQL statements applied in a single transaction.
MIGRATIONS: Dict[int, List[str]] = {
    1: INITIAL_SCHEMA_SQL,
}


def all_table_names() -> List[str]:
    """Return the canonical list of control-plane table names."""

    return [
        "schema_meta",
        "solver_families",
        "solvers",
        "capabilities",
        "capability_dependencies",
        "solver_capabilities",
        "vector_shards",
        "vector_indexes",
        "identity_anchors",
        "provider_jobs",
        "builder_jobs",
        "promotion_states",
        "cutover_states",
        "runtime_path_bindings",
        "capsule_registry_bindings",
        "cell_membership",
    ]
