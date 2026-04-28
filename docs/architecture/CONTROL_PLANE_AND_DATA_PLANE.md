# Control Plane and Data Plane — WaggleDance Storage Foundation

**Status:** new in Phase 10 (P2). Substrate; nothing in this design mutates runtime behavior on import. Population and migration of existing data is explicitly follow-up work.

## Why this exists

Per Phase 10 RULE 4 and RULE 16:

> The moving MAGMA / FAISS / control-plane substrate is the foundation of WD intelligence. No architectural conclusion, cutover conclusion, builder-lane design, synthesis design, Reality View claim, or README claim is valid unless it preserves and extends the actual storage foundation.

> Do not finalize any large solver-growth work before the control-plane DB foundation is at minimum schema-complete and migration-ready. JSON-only manifests are not the long-term control plane for 10k+ solvers.

The 2026-04-28 storage / runtime truth audit (`docs/journal/2026-04-28_storage_runtime_truth.md`) found three concrete substrate gaps:

1. **No path resolver / vector store registry abstraction.** The runtime hard-codes `data/faiss/` via `core/faiss_store.py:26 _DEFAULT_FAISS_DIR`.
2. **No control-plane DB.** Each subsystem invents its own JSON or per-feature SQLite shape for solver / family / cell metadata.
3. **MAGMA append-only is convention only.** No SQLite triggers, no `PRAGMA query_only`, raw `_conn` shared across `AuditLog`, `ProvenanceTracker`, `TrustEngine`.

P2 closes gap (1) and (2). Gap (3) is a separate hardening pass; this document records the boundary so a later pass can attach.

## The three planes

```
+---------------------------------------------------------------+
|                      Audit / provenance plane                 |
|                                                               |
|   Append-only MAGMA wrappers (waggledance/core/magma/*)       |
|     - AuditProjector  -> core/audit_log.py        SQLite      |
|     - EventLogAdapter -> core/learning_ledger.py  JSONL       |
|     - ProvenanceAdapter -> core/provenance.py     SQLite      |
|     - ReplayAdapter   -> in-process aggregator                |
|     - TrustAdapter    -> core/trust_engine.py     SQLite      |
|     - vector_events.py -> data/vector/events.jsonl (tools)    |
|                                                               |
|   Owns: history. Cannot be queried for "current state".       |
+---------------------------------------------------------------+

+---------------------------------------------------------------+
|                  Control / metadata plane (NEW — P2)          |
|                                                               |
|   waggledance/core/storage/control_plane.py (SQLite)          |
|     Tables:                                                   |
|       solver_families, solvers,                               |
|       capabilities, capability_dependencies,                  |
|       solver_capabilities, vector_shards, vector_indexes,     |
|       identity_anchors, provider_jobs, builder_jobs,          |
|       promotion_states, cutover_states,                       |
|       runtime_path_bindings, capsule_registry_bindings,       |
|       cell_membership                                         |
|                                                               |
|   waggledance/core/storage/path_resolver.py                   |
|     Logical -> physical resolution with override + control-   |
|     plane binding + static default.                           |
|                                                               |
|   waggledance/core/storage/registry_queries.py                |
|     Read patterns and rollups (Reality-View friendly).        |
|                                                               |
|   Owns: current state of solvers/capabilities/shards/jobs.    |
|   Does NOT own history. MAGMA owns history.                   |
+---------------------------------------------------------------+

+---------------------------------------------------------------+
|                       Retrieval / data plane                  |
|                                                               |
|   FAISS  (core/faiss_store.py, FaissRegistry)                 |
|   Chroma (waggledance/adapters/.../chroma)                    |
|   Vector shards on disk (data/faiss/, data/vector/...)        |
|                                                               |
|   Owns: vector content + similarity search.                   |
|   Does NOT own metadata. The control plane points HERE.       |
+---------------------------------------------------------------+
```

The fundamental rule: **MAGMA is history, control-plane is current state, FAISS/Chroma is vector content.** A query about *what solvers exist* goes to the control plane. A query about *what happened in mission X* goes to MAGMA. A query about *which vectors are similar to V* goes to FAISS/Chroma.

## What P2 adds in code

| Module | Role | Lines (approx) |
|---|---|---|
| `waggledance/core/storage/__init__.py` | Public surface | 70 |
| `waggledance/core/storage/control_plane_schema.py` | SQL DDL + migration registry | 220 |
| `waggledance/core/storage/control_plane.py` | `ControlPlaneDB` wrapper + dataclasses | 700 |
| `waggledance/core/storage/path_resolver.py` | `PathResolver`, `LogicalPathKind`, `ResolvedPath` | 200 |
| `waggledance/core/storage/registry_queries.py` | Read-side helpers + rollups | 175 |

All five files are licensed under BUSL-1.1 with `# BUSL-Change-Date: 2030-12-31` per RULE 6 of the Phase 10 master prompt and listed in `LICENSE-CORE.md` under "v3.6.x / Phase 10 protected files".

## What P2 does NOT do

- It does not migrate the existing `data/faiss/` collections into `vector_shards`. That migration is a follow-up commit (`tools/populate_control_plane_from_legacy.py` is unwritten).
- It does not change `core/faiss_store.py:26`. The runtime still resolves through the legacy constant. Rerouting the runtime through `PathResolver` is a follow-up step that lands when subsystems opt in.
- It does not enforce MAGMA append-only. That hardening pass is separate.
- It does not perform any cutover. `cutover_states` is just a table.

## Path resolver semantics

Resolution order, deterministic:

1. **Explicit override** — `resolver.register_override(...)`. Highest precedence. Used by tests and by the operator for ad-hoc redirection.
2. **Active control-plane binding** — `runtime_path_bindings` row with `is_active=1` for the requested `(logical_name, path_kind)`.
3. **Static default** — built-in mapping in `path_resolver.py:_DEFAULT_RELATIVE_PATHS`, matched to the legacy hard-coded relative paths so an unconfigured resolver returns exactly today's runtime layout.

Each `ResolvedPath` carries its `source` (`override` / `control_plane` / `default`). Reality View and status endpoints can show whether a path is currently legacy-default or explicitly bound.

## How a future cutover would use this

Once a subsystem reads its root through `PathResolver.resolve(LogicalPathKind.FAISS_ROOT)`, an atomic flip becomes:

```python
control_plane.bind_runtime_path(
    logical_name="default",
    path_kind=LogicalPathKind.FAISS_ROOT.value,
    physical_path="data/vector",
)
```

That's a single transactional row update in SQLite. The runtime restart picks up the new resolution; in-process subscribers can be told to re-resolve. There is no `os.replace()`, no symlink rotation, no per-cell loop.

This is the seam the future Stage-2 cutover (currently MODEL_D_AMBIGUOUS per `2026-04-28_cutover_model_classification.md`) needs. P2 ships the seam; the cutover itself remains out of scope (RULE 18).

## Schema highlights

* **Solver / capability separation.** A *capability* is what someone can do (`reason.causal`, `solver.thermal.basic`). A *solver* is a concrete implementation. The many-to-many `solver_capabilities` table lets a single solver provide multiple capabilities and a capability be provided by multiple solvers. `capability_dependencies` carries the DAG of "X requires Y".
* **Vector shards vs vector indexes.** A *shard* is a logical bucket of embeddings (`agent_knowledge`, `cell_3.axioms`, `entity.alpha-anchor`). An *index* is a physical FAISS / IVF / HNSW build over that shard. One shard can have multiple indexes (e.g. flat for cold path, IVF for hot path).
* **Provider / builder jobs.** Every Claude Code / Anthropic / GPT / local-model invocation is recorded with cost estimate, status, request hash, and section/purpose tags. P3 will write to these tables. Builder jobs link to the provider job that spawned them and the worktree where they ran.
* **Promotion states.** Stage-N promotion ladder transitions per (target_kind, target_id). MAGMA still records the *event*; this table records the *current state*.
* **Cutover states.** A future-flip-friendly table: scope ("autonomy_runtime", "faiss_root", "stage2_durable_bus"), `from_value`, `to_value`, `status`. Today rows can be written for audit; the runtime does not yet branch on this table (per the storage truth audit, no runtime code branches on cutover state today — making such a branch is opt-in for future flips).
* **Runtime path bindings.** Single source of truth for "where is shard X right now". Activating a new binding deactivates the previous one transactionally.
* **Cell membership.** Generic many-to-many of cells to (solvers / shards / capabilities / capsules) without committing to the 8-cell topology — coords are strings.

## Scalability targets

* `solvers`: tested to 50k rows in 5 seconds insertion + index lookup under 5ms (see `tests/storage/test_control_plane_scale.py`).
* `capability_dependencies`: graph with ~10 deps per capability and 5k capabilities = 50k edges; transitive-closure queries stay sub-second on SQLite WAL.
* `provider_jobs`: append-mostly; rotate to a separate file annually if needed (one log file per year is sufficient for any reasonable workload).
* `runtime_path_bindings`: tiny by definition; the unique index on `(logical_name, path_kind, is_active)` keeps deactivate-then-insert correct.

## Cross-references

- `docs/journal/2026-04-28_storage_runtime_truth.md` — the truth audit that motivated this design.
- `docs/journal/2026-04-28_cutover_model_classification.md` — formal classification that this design is the seam future flips need.
- `docs/architecture/MAGMA_FAISS_SCALING.md` — Stage-1/2 design (this doc complements it).
- `docs/architecture/PROMPT_2_INPUTS_AND_CONTRACTS.md` — Prompt 2 contract (P11 will reflect this seam).
