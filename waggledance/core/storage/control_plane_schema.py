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

SCHEMA_VERSION: int = 6

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


# --------------------------------------------------------------------------
# Schema v5 — scoped activation snapshot pointers
# --------------------------------------------------------------------------
# The control plane stores only immutable, content-addressed pointer metadata.
# Full activation bundles and their audit history remain in MAGMA.  A current
# pointer is the greatest ``store_revision`` for one verified deployment/cell
# scope; no mutable "current" row is needed.
SCOPED_ACTIVATION_SNAPSHOT_SCHEMA_SQL: List[str] = [
    """
    CREATE TABLE IF NOT EXISTS activation_scopes (
        activation_scope_digest  TEXT PRIMARY KEY,
        deployment_scope_digest  TEXT NOT NULL,
        cell_id                  TEXT NOT NULL,
        created_at               TEXT NOT NULL,
        UNIQUE (deployment_scope_digest, cell_id),
        CHECK (
            typeof(activation_scope_digest) = 'text'
            AND length(activation_scope_digest) = 71
            AND substr(activation_scope_digest, 1, 7) = 'sha256:'
            AND substr(activation_scope_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            typeof(deployment_scope_digest) = 'text'
            AND length(deployment_scope_digest) = 71
            AND substr(deployment_scope_digest, 1, 7) = 'sha256:'
            AND substr(deployment_scope_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            typeof(cell_id) = 'text'
            AND length(cell_id) = 71
            AND substr(cell_id, 1, 7) = 'sha256:'
            AND substr(cell_id, 8) NOT GLOB '*[^0-9a-f]*'
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS activation_scope_tombstones (
        activation_scope_digest  TEXT PRIMARY KEY
            REFERENCES activation_scopes(activation_scope_digest)
            ON DELETE RESTRICT,
        reason_digest            TEXT NOT NULL,
        retired_at               TEXT NOT NULL,
        CHECK (
            typeof(activation_scope_digest) = 'text'
            AND length(activation_scope_digest) = 71
            AND substr(activation_scope_digest, 1, 7) = 'sha256:'
            AND substr(activation_scope_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            typeof(reason_digest) = 'text'
            AND length(reason_digest) = 71
            AND substr(reason_digest, 1, 7) = 'sha256:'
            AND substr(reason_digest, 8) NOT GLOB '*[^0-9a-f]*'
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS activation_snapshot_pointers (
        id                               INTEGER PRIMARY KEY AUTOINCREMENT,
        activation_scope_digest          TEXT NOT NULL
            REFERENCES activation_scopes(activation_scope_digest)
            ON DELETE RESTRICT,
        bundle_digest                    TEXT NOT NULL UNIQUE,
        store_revision                   INTEGER NOT NULL,
        previous_bundle_digest           TEXT NOT NULL,
        activation_head_digest           TEXT NOT NULL,
        previous_activation_head_digest  TEXT NOT NULL,
        expression_context_digest        TEXT NOT NULL,
        expected_profile_head_digest     TEXT NOT NULL,
        expected_policy_head_digest      TEXT NOT NULL,
        expected_resource_head_digest    TEXT NOT NULL,
        expected_domain_head_digest      TEXT NOT NULL,
        expected_environment_head_digest TEXT NOT NULL,
        charter_ceiling_digest           TEXT NOT NULL,
        expressed_ceiling_digest         TEXT NOT NULL,
        created_at                       TEXT NOT NULL,
        UNIQUE (activation_scope_digest, store_revision),
        UNIQUE (activation_scope_digest, activation_head_digest),
        CHECK (
            typeof(store_revision) = 'integer'
            AND store_revision BETWEEN 0 AND 9223372036854775807
        ),
        CHECK (
            typeof(activation_scope_digest) = 'text'
            AND length(activation_scope_digest) = 71
            AND substr(activation_scope_digest, 1, 7) = 'sha256:'
            AND substr(activation_scope_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            typeof(bundle_digest) = 'text'
            AND length(bundle_digest) = 71
            AND substr(bundle_digest, 1, 7) = 'sha256:'
            AND substr(bundle_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            typeof(previous_bundle_digest) = 'text'
            AND length(previous_bundle_digest) = 71
            AND substr(previous_bundle_digest, 1, 7) = 'sha256:'
            AND substr(previous_bundle_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            typeof(activation_head_digest) = 'text'
            AND length(activation_head_digest) = 71
            AND substr(activation_head_digest, 1, 7) = 'sha256:'
            AND substr(activation_head_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            typeof(previous_activation_head_digest) = 'text'
            AND length(previous_activation_head_digest) = 71
            AND substr(previous_activation_head_digest, 1, 7) = 'sha256:'
            AND substr(previous_activation_head_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            typeof(expression_context_digest) = 'text'
            AND length(expression_context_digest) = 71
            AND substr(expression_context_digest, 1, 7) = 'sha256:'
            AND substr(expression_context_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            typeof(expected_profile_head_digest) = 'text'
            AND length(expected_profile_head_digest) = 71
            AND substr(expected_profile_head_digest, 1, 7) = 'sha256:'
            AND substr(expected_profile_head_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            typeof(expected_policy_head_digest) = 'text'
            AND length(expected_policy_head_digest) = 71
            AND substr(expected_policy_head_digest, 1, 7) = 'sha256:'
            AND substr(expected_policy_head_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            typeof(expected_resource_head_digest) = 'text'
            AND length(expected_resource_head_digest) = 71
            AND substr(expected_resource_head_digest, 1, 7) = 'sha256:'
            AND substr(expected_resource_head_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            typeof(expected_domain_head_digest) = 'text'
            AND length(expected_domain_head_digest) = 71
            AND substr(expected_domain_head_digest, 1, 7) = 'sha256:'
            AND substr(expected_domain_head_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            typeof(expected_environment_head_digest) = 'text'
            AND length(expected_environment_head_digest) = 71
            AND substr(expected_environment_head_digest, 1, 7) = 'sha256:'
            AND substr(expected_environment_head_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            typeof(charter_ceiling_digest) = 'text'
            AND length(charter_ceiling_digest) = 71
            AND substr(charter_ceiling_digest, 1, 7) = 'sha256:'
            AND substr(charter_ceiling_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            typeof(expressed_ceiling_digest) = 'text'
            AND length(expressed_ceiling_digest) = 71
            AND substr(expressed_ceiling_digest, 1, 7) = 'sha256:'
            AND substr(expressed_ceiling_digest, 8) NOT GLOB '*[^0-9a-f]*'
        )
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_activation_scopes_refuse_collision
    BEFORE INSERT ON activation_scopes
    WHEN EXISTS (
        SELECT 1
        FROM activation_scopes
        WHERE activation_scope_digest = NEW.activation_scope_digest
           OR (
               deployment_scope_digest = NEW.deployment_scope_digest
               AND cell_id = NEW.cell_id
           )
    )
    BEGIN
        SELECT RAISE(ABORT, 'activation_scopes immutable key collision');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS
        trg_activation_scope_tombstones_refuse_collision
    BEFORE INSERT ON activation_scope_tombstones
    WHEN EXISTS (
        SELECT 1
        FROM activation_scope_tombstones
        WHERE activation_scope_digest = NEW.activation_scope_digest
    )
    BEGIN
        SELECT RAISE(
            ABORT,
            'activation_scope_tombstones immutable key collision'
        );
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS
        trg_activation_snapshot_pointers_refuse_collision
    BEFORE INSERT ON activation_snapshot_pointers
    WHEN EXISTS (
        SELECT 1
        FROM activation_snapshot_pointers
        WHERE id = NEW.id
           OR bundle_digest = NEW.bundle_digest
           OR (
               activation_scope_digest = NEW.activation_scope_digest
               AND store_revision = NEW.store_revision
           )
           OR (
               activation_scope_digest = NEW.activation_scope_digest
               AND activation_head_digest = NEW.activation_head_digest
           )
    )
    BEGIN
        SELECT RAISE(
            ABORT,
            'activation_snapshot_pointers immutable key collision'
        );
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_activation_scopes_refuse_update
    BEFORE UPDATE ON activation_scopes
    BEGIN
        SELECT RAISE(ABORT, 'activation_scopes rows are immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_activation_scopes_refuse_delete
    BEFORE DELETE ON activation_scopes
    BEGIN
        SELECT RAISE(ABORT, 'activation_scopes rows are immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_activation_scope_tombstones_refuse_update
    BEFORE UPDATE ON activation_scope_tombstones
    BEGIN
        SELECT RAISE(ABORT, 'activation_scope_tombstones rows are immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_activation_scope_tombstones_refuse_delete
    BEFORE DELETE ON activation_scope_tombstones
    BEGIN
        SELECT RAISE(ABORT, 'activation_scope_tombstones rows are immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_activation_snapshot_pointers_refuse_update
    BEFORE UPDATE ON activation_snapshot_pointers
    BEGIN
        SELECT RAISE(ABORT, 'activation_snapshot_pointers rows are immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_activation_snapshot_pointers_refuse_delete
    BEFORE DELETE ON activation_snapshot_pointers
    BEGIN
        SELECT RAISE(ABORT, 'activation_snapshot_pointers rows are immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_activation_snapshot_pointers_validate_insert
    BEFORE INSERT ON activation_snapshot_pointers
    BEGIN
        SELECT CASE
            WHEN EXISTS (
                SELECT 1
                FROM activation_scope_tombstones
                WHERE activation_scope_digest = NEW.activation_scope_digest
            )
            THEN RAISE(ABORT, 'activation scope is retired')
        END;

        SELECT CASE
            WHEN NOT EXISTS (
                SELECT 1
                FROM activation_snapshot_pointers
                WHERE activation_scope_digest = NEW.activation_scope_digest
            ) AND (
                NEW.store_revision <> 0
                OR NEW.previous_bundle_digest <>
                    'sha256:0000000000000000000000000000000000000000000000000000000000000000'
                OR NEW.previous_activation_head_digest <>
                    'sha256:0000000000000000000000000000000000000000000000000000000000000000'
            )
            THEN RAISE(ABORT, 'activation snapshot genesis mismatch')

            WHEN EXISTS (
                SELECT 1
                FROM activation_snapshot_pointers
                WHERE activation_scope_digest = NEW.activation_scope_digest
            ) AND (
                NEW.store_revision <> (
                    SELECT store_revision + 1
                    FROM activation_snapshot_pointers
                    WHERE activation_scope_digest = NEW.activation_scope_digest
                    ORDER BY store_revision DESC
                    LIMIT 1
                )
                OR NEW.previous_bundle_digest <> (
                    SELECT bundle_digest
                    FROM activation_snapshot_pointers
                    WHERE activation_scope_digest = NEW.activation_scope_digest
                    ORDER BY store_revision DESC
                    LIMIT 1
                )
                OR NEW.previous_activation_head_digest <> (
                    SELECT activation_head_digest
                    FROM activation_snapshot_pointers
                    WHERE activation_scope_digest = NEW.activation_scope_digest
                    ORDER BY store_revision DESC
                    LIMIT 1
                )
            )
            THEN RAISE(ABORT, 'activation snapshot chain mismatch')
        END;
    END
    """,
]


# --------------------------------------------------------------------------
# Schema v6 — immutable, scope-local attested-consensus expectation pins
# --------------------------------------------------------------------------
# Each row embeds the exact canonical pin consumed by the off-path runtime
# observer.  The extracted chain fields let SQLite enforce a strict append
# order and, critically, reject a challenge reused anywhere in one scope's
# history (including non-adjacent A -> B -> A replay).  Application code still
# re-verifies the canonical bytes and independently binds them to the source
# admission intent, closed attestation-log head, cell scope, and current
# activation pointer before insertion.
ATTESTED_CONSENSUS_EXPECTATION_SCHEMA_SQL: List[str] = [
    """
    CREATE TABLE IF NOT EXISTS attested_consensus_expectations (
        id                                   INTEGER PRIMARY KEY,
        activation_scope_digest              TEXT NOT NULL
            REFERENCES activation_scopes(activation_scope_digest)
            ON DELETE RESTRICT,
        generation                           INTEGER NOT NULL,
        previous_expectation_head_digest     TEXT NOT NULL,
        expectation_head_digest              TEXT NOT NULL UNIQUE,
        admission_challenge_digest           TEXT NOT NULL,
        expected_consensus_policy_digest     TEXT NOT NULL,
        expected_query_digest                TEXT NOT NULL,
        expected_current_bundle_digest       TEXT NOT NULL,
        expected_current_activation_head_digest TEXT NOT NULL,
        expected_current_store_revision      INTEGER NOT NULL,
        expected_proposed_bundle_digest      TEXT NOT NULL,
        expected_proposed_activation_head_digest TEXT NOT NULL,
        expected_proposed_store_revision     INTEGER NOT NULL,
        expected_trust_registry_head_digest  TEXT NOT NULL,
        expected_attestation_log_base_head_digest TEXT NOT NULL,
        expected_attestation_log_closed_head_digest TEXT NOT NULL,
        canonical_expectation                BLOB NOT NULL,
        created_at                           TEXT NOT NULL,
        UNIQUE (activation_scope_digest, generation),
        UNIQUE (activation_scope_digest, admission_challenge_digest),
        CHECK (typeof(id) = 'integer' AND id > 0),
        CHECK (
            typeof(generation) = 'integer'
            AND generation BETWEEN 0 AND 9223372036854775807
        ),
        CHECK (
            typeof(expected_current_store_revision) = 'integer'
            AND expected_current_store_revision
                BETWEEN 0 AND 9223372036854775806
        ),
        CHECK (
            typeof(expected_proposed_store_revision) = 'integer'
            AND expected_proposed_store_revision
                BETWEEN 0 AND 9223372036854775807
            AND expected_proposed_store_revision =
                expected_current_store_revision + 1
        ),
        CHECK (
            (
                generation = 0
                AND previous_expectation_head_digest =
                    'sha256:0000000000000000000000000000000000000000000000000000000000000000'
            ) OR (
                generation > 0
                AND previous_expectation_head_digest <>
                    'sha256:0000000000000000000000000000000000000000000000000000000000000000'
            )
        ),
        CHECK (expectation_head_digest <> previous_expectation_head_digest),
        CHECK (
            expected_proposed_bundle_digest <>
                expected_current_bundle_digest
        ),
        CHECK (
            expected_proposed_activation_head_digest <>
                expected_current_activation_head_digest
        ),
        CHECK (
            expected_attestation_log_closed_head_digest <>
                expected_attestation_log_base_head_digest
        ),
        CHECK (
            typeof(canonical_expectation) = 'blob'
            AND length(canonical_expectation) BETWEEN 1 AND 65536
        ),
        CHECK (
            typeof(activation_scope_digest) = 'text'
            AND length(activation_scope_digest) = 71
            AND substr(activation_scope_digest, 1, 7) = 'sha256:'
            AND substr(activation_scope_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            typeof(previous_expectation_head_digest) = 'text'
            AND length(previous_expectation_head_digest) = 71
            AND substr(previous_expectation_head_digest, 1, 7) = 'sha256:'
            AND substr(previous_expectation_head_digest, 8)
                NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            typeof(expectation_head_digest) = 'text'
            AND length(expectation_head_digest) = 71
            AND substr(expectation_head_digest, 1, 7) = 'sha256:'
            AND substr(expectation_head_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            typeof(admission_challenge_digest) = 'text'
            AND length(admission_challenge_digest) = 71
            AND substr(admission_challenge_digest, 1, 7) = 'sha256:'
            AND substr(admission_challenge_digest, 8)
                NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            typeof(expected_consensus_policy_digest) = 'text'
            AND length(expected_consensus_policy_digest) = 71
            AND substr(expected_consensus_policy_digest, 1, 7) = 'sha256:'
            AND substr(expected_consensus_policy_digest, 8)
                NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            typeof(expected_query_digest) = 'text'
            AND length(expected_query_digest) = 71
            AND substr(expected_query_digest, 1, 7) = 'sha256:'
            AND substr(expected_query_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            typeof(expected_current_bundle_digest) = 'text'
            AND length(expected_current_bundle_digest) = 71
            AND substr(expected_current_bundle_digest, 1, 7) = 'sha256:'
            AND substr(expected_current_bundle_digest, 8)
                NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            typeof(expected_current_activation_head_digest) = 'text'
            AND length(expected_current_activation_head_digest) = 71
            AND substr(expected_current_activation_head_digest, 1, 7) =
                'sha256:'
            AND substr(expected_current_activation_head_digest, 8)
                NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            typeof(expected_proposed_bundle_digest) = 'text'
            AND length(expected_proposed_bundle_digest) = 71
            AND substr(expected_proposed_bundle_digest, 1, 7) = 'sha256:'
            AND substr(expected_proposed_bundle_digest, 8)
                NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            typeof(expected_proposed_activation_head_digest) = 'text'
            AND length(expected_proposed_activation_head_digest) = 71
            AND substr(expected_proposed_activation_head_digest, 1, 7) =
                'sha256:'
            AND substr(expected_proposed_activation_head_digest, 8)
                NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            typeof(expected_trust_registry_head_digest) = 'text'
            AND length(expected_trust_registry_head_digest) = 71
            AND substr(expected_trust_registry_head_digest, 1, 7) = 'sha256:'
            AND substr(expected_trust_registry_head_digest, 8)
                NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            typeof(expected_attestation_log_base_head_digest) = 'text'
            AND length(expected_attestation_log_base_head_digest) = 71
            AND substr(expected_attestation_log_base_head_digest, 1, 7) =
                'sha256:'
            AND substr(expected_attestation_log_base_head_digest, 8)
                NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            typeof(expected_attestation_log_closed_head_digest) = 'text'
            AND length(expected_attestation_log_closed_head_digest) = 71
            AND substr(expected_attestation_log_closed_head_digest, 1, 7) =
                'sha256:'
            AND substr(expected_attestation_log_closed_head_digest, 8)
                NOT GLOB '*[^0-9a-f]*'
        )
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS
        trg_attested_consensus_expectations_refuse_collision
    BEFORE INSERT ON attested_consensus_expectations
    WHEN EXISTS (
        SELECT 1
        FROM attested_consensus_expectations
        WHERE id = NEW.id
           OR expectation_head_digest = NEW.expectation_head_digest
           OR (
               activation_scope_digest = NEW.activation_scope_digest
               AND generation = NEW.generation
           )
           OR (
               activation_scope_digest = NEW.activation_scope_digest
               AND admission_challenge_digest =
                   NEW.admission_challenge_digest
           )
    )
    BEGIN
        SELECT RAISE(
            ABORT,
            'attested consensus expectation immutable key collision'
        );
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS
        trg_attested_consensus_expectations_refuse_update
    BEFORE UPDATE ON attested_consensus_expectations
    BEGIN
        SELECT RAISE(
            ABORT,
            'attested consensus expectation rows are immutable'
        );
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS
        trg_attested_consensus_expectations_refuse_delete
    BEFORE DELETE ON attested_consensus_expectations
    BEGIN
        SELECT RAISE(
            ABORT,
            'attested consensus expectation rows are immutable'
        );
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS
        trg_attested_consensus_expectations_validate_insert
    BEFORE INSERT ON attested_consensus_expectations
    BEGIN
        SELECT CASE
            WHEN EXISTS (
                SELECT 1
                FROM attested_consensus_expectations
                WHERE id = NEW.id
                   OR expectation_head_digest =
                       NEW.expectation_head_digest
                   OR (
                       activation_scope_digest =
                           NEW.activation_scope_digest
                       AND generation = NEW.generation
                   )
                   OR (
                       activation_scope_digest =
                           NEW.activation_scope_digest
                       AND admission_challenge_digest =
                           NEW.admission_challenge_digest
                   )
            )
            THEN RAISE(
                ABORT,
                'attested consensus expectation immutable key collision'
            )
        END;

        SELECT CASE
            WHEN NOT EXISTS (
                SELECT 1
                FROM activation_scopes
                WHERE activation_scope_digest = NEW.activation_scope_digest
            )
            THEN RAISE(
                ABORT,
                'attested consensus expectation activation scope missing'
            )
        END;

        SELECT CASE
            WHEN EXISTS (
                SELECT 1
                FROM activation_scope_tombstones
                WHERE activation_scope_digest = NEW.activation_scope_digest
            )
            THEN RAISE(ABORT, 'activation scope is retired')
        END;

        SELECT CASE
            WHEN NOT EXISTS (
                SELECT 1
                FROM activation_snapshot_pointers
                WHERE activation_scope_digest = NEW.activation_scope_digest
            )
            THEN RAISE(
                ABORT,
                'attested consensus expectation requires activation pointer'
            )
        END;

        SELECT CASE
            WHEN NOT EXISTS (
                SELECT 1
                FROM activation_snapshot_pointers
                WHERE activation_scope_digest = NEW.activation_scope_digest
                  AND bundle_digest =
                      NEW.expected_current_bundle_digest
                  AND activation_head_digest =
                      NEW.expected_current_activation_head_digest
                  AND store_revision =
                      NEW.expected_current_store_revision
                  AND store_revision = (
                      SELECT MAX(store_revision)
                      FROM activation_snapshot_pointers
                      WHERE activation_scope_digest =
                          NEW.activation_scope_digest
                  )
            )
            THEN RAISE(
                ABORT,
                'attested consensus expectation current activation mismatch'
            )
        END;

        SELECT CASE
            WHEN NOT EXISTS (
                SELECT 1
                FROM attested_consensus_expectations
                WHERE activation_scope_digest = NEW.activation_scope_digest
            ) AND (
                NEW.generation <> 0
                OR NEW.previous_expectation_head_digest <>
                    'sha256:0000000000000000000000000000000000000000000000000000000000000000'
            )
            THEN RAISE(
                ABORT,
                'attested consensus expectation genesis mismatch'
            )

            WHEN EXISTS (
                SELECT 1
                FROM attested_consensus_expectations
                WHERE activation_scope_digest = NEW.activation_scope_digest
            ) AND (
                NEW.generation <> (
                    SELECT generation + 1
                    FROM attested_consensus_expectations
                    WHERE activation_scope_digest = NEW.activation_scope_digest
                    ORDER BY generation DESC
                    LIMIT 1
                )
                OR NEW.previous_expectation_head_digest <> (
                    SELECT expectation_head_digest
                    FROM attested_consensus_expectations
                    WHERE activation_scope_digest = NEW.activation_scope_digest
                    ORDER BY generation DESC
                    LIMIT 1
                )
            )
            THEN RAISE(
                ABORT,
                'attested consensus expectation chain mismatch'
            )
        END;
    END
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
    5: SCOPED_ACTIVATION_SNAPSHOT_SCHEMA_SQL,
    6: ATTESTED_CONSENSUS_EXPECTATION_SCHEMA_SQL,
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
        # schema v5 — immutable scoped activation snapshot pointers
        "activation_scopes",
        "activation_scope_tombstones",
        "activation_snapshot_pointers",
        # schema v6 — immutable scoped consensus-expectation pin chain
        "attested_consensus_expectations",
    ]
