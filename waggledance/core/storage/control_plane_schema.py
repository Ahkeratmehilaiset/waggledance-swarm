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

SCHEMA_VERSION: int = 4

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


# --------------------------------------------------------------------------
# Schema v2 — Phase 11 autonomous low-risk solver growth
# --------------------------------------------------------------------------
# Adds five normalized tables for the autonomy lane defined in
# ``docs/architecture/LOW_RISK_AUTOGROWTH_POLICY.md``. No ad hoc JSON
# system-of-record (RULE 10): every autonomy current-state lives here,
# and every promotion / rollback decision is auditable from this schema
# alone.
PHASE11_AUTOGROWTH_SCHEMA_SQL: List[str] = [
    """
    CREATE TABLE IF NOT EXISTS solver_artifacts (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        solver_id           INTEGER NOT NULL REFERENCES solvers(id) ON DELETE CASCADE,
        family_kind         TEXT NOT NULL,
        artifact_id         TEXT NOT NULL,
        spec_canonical_json TEXT NOT NULL,
        artifact_json       TEXT NOT NULL,
        created_at          TEXT NOT NULL,
        UNIQUE (solver_id, artifact_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_solver_artifacts_family
        ON solver_artifacts(family_kind)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_solver_artifacts_artifact_id
        ON solver_artifacts(artifact_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS family_policies (
        id                          INTEGER PRIMARY KEY AUTOINCREMENT,
        family_kind                 TEXT NOT NULL UNIQUE,
        is_low_risk                 INTEGER NOT NULL DEFAULT 0,
        max_auto_promote            INTEGER NOT NULL DEFAULT 100,
        min_validation_pass_rate    REAL NOT NULL DEFAULT 1.0,
        min_shadow_samples          INTEGER NOT NULL DEFAULT 5,
        min_shadow_agreement_rate   REAL NOT NULL DEFAULT 1.0,
        notes                       TEXT,
        created_at                  TEXT NOT NULL,
        updated_at                  TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_family_policies_low_risk
        ON family_policies(is_low_risk)
    """,
    """
    CREATE TABLE IF NOT EXISTS validation_runs (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        solver_id           INTEGER REFERENCES solvers(id) ON DELETE SET NULL,
        family_kind         TEXT NOT NULL,
        spec_hash           TEXT,
        case_count          INTEGER NOT NULL DEFAULT 0,
        pass_count          INTEGER NOT NULL DEFAULT 0,
        fail_count          INTEGER NOT NULL DEFAULT 0,
        status              TEXT NOT NULL DEFAULT 'running',
        evidence            TEXT,
        started_at          TEXT NOT NULL,
        completed_at        TEXT,
        created_at          TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_validation_runs_solver
        ON validation_runs(solver_id, completed_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_validation_runs_family
        ON validation_runs(family_kind, status)
    """,
    """
    CREATE TABLE IF NOT EXISTS shadow_evaluations (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        solver_id               INTEGER REFERENCES solvers(id) ON DELETE SET NULL,
        family_kind             TEXT NOT NULL,
        spec_hash               TEXT,
        sample_count            INTEGER NOT NULL DEFAULT 0,
        agree_count             INTEGER NOT NULL DEFAULT 0,
        disagree_count          INTEGER NOT NULL DEFAULT 0,
        agreement_rate          REAL,
        oracle_kind             TEXT,
        status                  TEXT NOT NULL DEFAULT 'running',
        evidence                TEXT,
        started_at              TEXT NOT NULL,
        completed_at            TEXT,
        created_at              TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_shadow_evaluations_solver
        ON shadow_evaluations(solver_id, completed_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_shadow_evaluations_family
        ON shadow_evaluations(family_kind, status)
    """,
    """
    CREATE TABLE IF NOT EXISTS promotion_decisions (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        solver_id               INTEGER NOT NULL REFERENCES solvers(id) ON DELETE CASCADE,
        family_kind             TEXT NOT NULL,
        decision                TEXT NOT NULL,
        decided_by              TEXT NOT NULL,
        validation_run_id       INTEGER REFERENCES validation_runs(id) ON DELETE SET NULL,
        shadow_evaluation_id    INTEGER REFERENCES shadow_evaluations(id) ON DELETE SET NULL,
        invariant_failed        TEXT,
        rollback_reason         TEXT,
        evidence                TEXT,
        created_at              TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_promotion_decisions_solver
        ON promotion_decisions(solver_id, decision)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_promotion_decisions_family
        ON promotion_decisions(family_kind, decision)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_promotion_decisions_decided_by
        ON promotion_decisions(decided_by, created_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS autonomy_kpis (
        id                          INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_at                 TEXT NOT NULL,
        candidates_total            INTEGER NOT NULL DEFAULT 0,
        validations_pass_total      INTEGER NOT NULL DEFAULT 0,
        validations_fail_total      INTEGER NOT NULL DEFAULT 0,
        shadows_pass_total          INTEGER NOT NULL DEFAULT 0,
        shadows_fail_total          INTEGER NOT NULL DEFAULT 0,
        auto_promotions_total       INTEGER NOT NULL DEFAULT 0,
        rejections_total            INTEGER NOT NULL DEFAULT 0,
        rollbacks_total             INTEGER NOT NULL DEFAULT 0,
        dispatcher_hits_total       INTEGER NOT NULL DEFAULT 0,
        dispatcher_misses_total     INTEGER NOT NULL DEFAULT 0,
        per_family_counts_json      TEXT,
        created_at                  TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_autonomy_kpis_snapshot_at
        ON autonomy_kpis(snapshot_at)
    """,
]


# --------------------------------------------------------------------------
# Schema v3 — Phase 12 self-starting local-first autogrowth loop
# --------------------------------------------------------------------------
# Adds the missing intake / queue / run-log layer between runtime
# evidence and the Phase 11 auto-promotion engine. Plus an append-only
# ``growth_events`` mirror so the audit trail has a history-plane
# representation alongside the current-state rows in v2.
PHASE12_AUTOGROWTH_INTAKE_SCHEMA_SQL: List[str] = [
    """
    CREATE TABLE IF NOT EXISTS runtime_gap_signals (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        kind            TEXT NOT NULL,
        family_kind     TEXT,
        cell_coord      TEXT,
        signal_payload  TEXT,
        weight          REAL NOT NULL DEFAULT 1.0,
        observed_at     TEXT NOT NULL,
        created_at      TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_runtime_gap_signals_kind
        ON runtime_gap_signals(kind, observed_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_runtime_gap_signals_family_cell
        ON runtime_gap_signals(family_kind, cell_coord)
    """,
    """
    CREATE TABLE IF NOT EXISTS growth_intents (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        family_kind         TEXT NOT NULL,
        cell_coord          TEXT,
        intent_key          TEXT NOT NULL UNIQUE,
        priority            INTEGER NOT NULL DEFAULT 0,
        status              TEXT NOT NULL DEFAULT 'pending',
        signal_count        INTEGER NOT NULL DEFAULT 0,
        last_signal_id      INTEGER REFERENCES runtime_gap_signals(id) ON DELETE SET NULL,
        spec_seed_json      TEXT,
        notes               TEXT,
        created_at          TEXT NOT NULL,
        updated_at          TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_growth_intents_status
        ON growth_intents(status, priority DESC, id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_growth_intents_family_cell
        ON growth_intents(family_kind, cell_coord, status)
    """,
    """
    CREATE TABLE IF NOT EXISTS autogrowth_queue (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        intent_id       INTEGER NOT NULL REFERENCES growth_intents(id) ON DELETE CASCADE,
        priority        INTEGER NOT NULL DEFAULT 0,
        status          TEXT NOT NULL DEFAULT 'queued',
        claimed_by      TEXT,
        claimed_at      TEXT,
        attempt_count   INTEGER NOT NULL DEFAULT 0,
        last_error      TEXT,
        backoff_until   TEXT,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_autogrowth_queue_pending
        ON autogrowth_queue(status, priority DESC, id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_autogrowth_queue_intent
        ON autogrowth_queue(intent_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_autogrowth_queue_backoff
        ON autogrowth_queue(backoff_until)
    """,
    """
    CREATE TABLE IF NOT EXISTS autogrowth_runs (
        id                          INTEGER PRIMARY KEY AUTOINCREMENT,
        queue_row_id                INTEGER REFERENCES autogrowth_queue(id) ON DELETE SET NULL,
        intent_id                   INTEGER REFERENCES growth_intents(id) ON DELETE SET NULL,
        outcome                     TEXT NOT NULL,
        promotion_decision_id       INTEGER REFERENCES promotion_decisions(id) ON DELETE SET NULL,
        validation_run_id           INTEGER REFERENCES validation_runs(id) ON DELETE SET NULL,
        shadow_evaluation_id        INTEGER REFERENCES shadow_evaluations(id) ON DELETE SET NULL,
        family_kind                 TEXT NOT NULL,
        cell_coord                  TEXT,
        solver_id                   INTEGER REFERENCES solvers(id) ON DELETE SET NULL,
        error                       TEXT,
        evidence                    TEXT,
        started_at                  TEXT NOT NULL,
        completed_at                TEXT NOT NULL,
        created_at                  TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_autogrowth_runs_intent
        ON autogrowth_runs(intent_id, completed_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_autogrowth_runs_outcome
        ON autogrowth_runs(outcome, completed_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_autogrowth_runs_family_cell
        ON autogrowth_runs(family_kind, cell_coord)
    """,
    """
    CREATE TABLE IF NOT EXISTS growth_events (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        event_kind      TEXT NOT NULL,
        entity_kind     TEXT,
        entity_id       INTEGER,
        family_kind     TEXT,
        cell_coord      TEXT,
        payload         TEXT,
        occurred_at     TEXT NOT NULL,
        created_at      TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_growth_events_entity
        ON growth_events(entity_kind, entity_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_growth_events_kind
        ON growth_events(event_kind, occurred_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_growth_events_family_cell
        ON growth_events(family_kind, cell_coord)
    """,
]


# --------------------------------------------------------------------------
# Schema v4 — Phase 13 capability-aware solver lookup
# --------------------------------------------------------------------------
# Adds ``solver_capability_features`` so the runtime dispatcher can match
# auto-promoted solvers by structured features (family-specific) instead
# of exact name or family-FIFO. One row per (solver_id, feature_name).
PHASE13_CAPABILITY_LOOKUP_SCHEMA_SQL: List[str] = [
    """
    CREATE TABLE IF NOT EXISTS solver_capability_features (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        solver_id     INTEGER NOT NULL REFERENCES solvers(id) ON DELETE CASCADE,
        family_kind   TEXT NOT NULL,
        feature_name  TEXT NOT NULL,
        feature_value TEXT NOT NULL,
        created_at    TEXT NOT NULL,
        UNIQUE (solver_id, feature_name)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_solver_capability_features_lookup
        ON solver_capability_features(family_kind, feature_name, feature_value)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_solver_capability_features_solver
        ON solver_capability_features(solver_id)
    """,
]


# Forward-only migrations indexed by target schema version.
# Migration N is applied when current schema_version < N. Each migration
# is a list of SQL statements applied in a single transaction.
MIGRATIONS: Dict[int, List[str]] = {
    1: INITIAL_SCHEMA_SQL,
    2: PHASE11_AUTOGROWTH_SCHEMA_SQL,
    3: PHASE12_AUTOGROWTH_INTAKE_SCHEMA_SQL,
    4: PHASE13_CAPABILITY_LOOKUP_SCHEMA_SQL,
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
        # schema v2 — Phase 11 autonomous low-risk solver growth
        "solver_artifacts",
        "family_policies",
        "validation_runs",
        "shadow_evaluations",
        "promotion_decisions",
        "autonomy_kpis",
        # schema v3 — Phase 12 self-starting local-first autogrowth loop
        "runtime_gap_signals",
        "growth_intents",
        "autogrowth_queue",
        "autogrowth_runs",
        "growth_events",
        # schema v4 — Phase 13 capability-aware solver lookup
        "solver_capability_features",
    ]
