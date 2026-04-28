# 2026-04-28 — Storage / Runtime Truth Audit (Phase 10 P1)

> Confirms and extends `docs/journal/2026-04-27_atomic_flip_analysis.md`. All code citations are at `phase10/foundation-truth-builder-lane` ≡ `origin/main` ≡ `8bf1869`.

## TL;DR

| Layer                                | Truth                                                                                     |
|--------------------------------------|-------------------------------------------------------------------------------------------|
| Active FAISS read/write path         | `data/faiss/` (via `core/faiss_store.py:26 _DEFAULT_FAISS_DIR`)                           |
| `data/vector/`                       | Dormant. One declared default (`magma/vector_events.py:241`); not on autonomy hot path    |
| `data/faiss_staging/` + `_delta_ledger/` | Tools + tests only. **Zero** references in `core/`, `waggledance/`, `hivemind.py`, `web/` |
| `current/` symlink under `data/vector/<cell>/` | Does not exist. Only a `current.json` pointer file written by `tools/vector_indexer.py:355` (offline) |
| Runtime path resolver / vector registry | **Absent** in `waggledance/`. Path is hard-coded in `core/faiss_store.py:26`             |
| MAGMA append-only enforcement        | **Convention only** — no SQLite triggers, no `PRAGMA query_only`, raw `_conn` is shared   |
| MAGMA adapters used by autonomy      | Yes: `AuditProjector`, `EventLogAdapter`, `ProvenanceAdapter`, `ReplayAdapter`, `TrustAdapter` are wired in `waggledance/core/autonomy/runtime.py:176-199` |
| Vector events runtime producer       | **Tools only** (`tools/vector_indexer.py:613`, `tools/backfill_axioms_to_hex.py:472`). No autonomy runtime emitter. |

## Methodology

Three parallel Explore-agent passes over `core/`, `waggledance/`, `hivemind.py`, `web/`, `tools/`, `tests/`, plus root entrypoints. Grep targets: `_DEFAULT_FAISS_DIR`, `data/faiss`, `data/vector`, `faiss_staging`, `faiss_delta_ledger`, `current/`, `os\.replace`, `os\.symlink`, `cutover_state`, `path_resolver|PathResolver|VectorStoreRegistry`. All citations were quoted back to verify against the working tree.

## 1. Active FAISS read / write path

| Caller                                                       | Resolved path                |
|--------------------------------------------------------------|------------------------------|
| `core/faiss_store.py:26` `_DEFAULT_FAISS_DIR`                | `<repo>/data/faiss`          |
| `core/faiss_store.py:52` `FaissCollection._dir`              | `_DEFAULT_FAISS_DIR / name`  |
| `core/faiss_store.py:178` `FaissRegistry._base_dir`          | `_DEFAULT_FAISS_DIR`         |
| `hivemind.py:770-771` legacy bootstrap                       | `data/faiss` (explicit)      |
| `waggledance/bootstrap/container.py:335-338`                 | `FaissRegistry()` default → `data/faiss` |
| `waggledance/adapters/capabilities/vector_search_adapter.py:18-35` | Default `FaissRegistry()` → `data/faiss` |
| `web/dashboard.py:1461,1504`                                 | `data/faiss` (explicit)      |

**Read path:** `core/faiss_store.py:142-158` `_try_load()` reads `<dir>/index.faiss` + `<dir>/meta.json`.
**Write path:** `core/faiss_store.py:127-140` `save()` writes the same two files.
**Delete path:** **None.** `FaissCollection`/`FaissRegistry` expose no remove API; rebuilds are overwrites.

The hybrid retrieval service (`waggledance/application/services/hybrid_retrieval_service.py:114-127, 327, 356-410`) instantiates **no storage of its own**; it composes the container-built `FaissRegistry` (rooted at `data/faiss/`) plus the Chroma `VectorStorePort`.

## 2. `data/vector/` — dormant, one declared default

| Reference (runtime tree)                              | Effect                                                  |
|-------------------------------------------------------|---------------------------------------------------------|
| `waggledance/core/magma/vector_events.py:241`         | `DEFAULT_EVENT_LOG = Path("data") / "vector" / "events.jsonl"` (default for an emitter) |
| `waggledance/core/magma/vector_events.py:247-261`     | Docstring; module note "no active consumer"             |

No autonomy runtime caller imports `magma/vector_events.emit()` / `emit_many()` / `read_events()`. The only active emitters are `tools/vector_indexer.py:600-613` and `tools/backfill_axioms_to_hex.py:451-486`. Hot-path-cold.

**Update vs `MAGMA_FAISS_SCALING.md §0` claim "no runtime producer yet":** technically true for in-process autonomy runtime; **factually narrow** — offline tools (`vector_indexer`, `backfill_axioms_to_hex`) ARE producers today. The doc should be tightened (deferred to P6).

## 3. `data/faiss_staging/` and `data/faiss_delta_ledger/` — tools + tests only

Grep across `core/`, `waggledance/`, `hivemind.py`, `web/dashboard.py`, `main.py`, `start.py`, `start_waggledance.py` returns zero hits. References live in `tools/backfill_axioms_to_hex.py:58-60`, `tools/compute_cell_centroids.py:29-30`, `tools/hex_manifest.py:34-36`, `tools/migrate_to_vector_root.py:33-34`, `tools/shadow_route_three_way.py:46,277`, `tools/migrate_embedding_model.py:14`, `tools/vector_indexer.py:10`, and `tests/gates/test_disaster_recovery.py:29-31`.

**Conclusion:** the runtime never reads or writes these paths.

## 4. `current/` symlink — does not exist

Grep for `os\.symlink` over the entire tree: zero hits. Only a JSON pointer file:

- `tools/vector_indexer.py:355-367` `_swap_current_pointer()` writes `<cell_dir>/current.json` via `os.replace()` (atomic file swap, not symlink).
- `tools/vector_indexer.py:373-381` `read_current_pointer()` reads it.
- `docs/architecture/MAGMA_VECTOR_STAGE2.md:98-100` explains the choice: "Windows symlinks require administrator rights."

The runtime never reads `current.json`. Only `tools/vector_indexer.py` does.

## 5. Path resolver / vector store registry — absent

Grep for `path_resolver|PathResolver|VectorStoreRegistry|vector_store_registry|StoreRegistry` across `waggledance/` returns no matches. The runtime hard-codes the path through:

```
core/faiss_store.py:26 _DEFAULT_FAISS_DIR = Path(__file__).parent.parent / "data" / "faiss"
└─→ waggledance/bootstrap/container.py:338 FaissRegistry()  # no base_dir override
```

This is the substrate that **P2** must abstract behind a resolver / registry layer per RULE 4.

## 6. MAGMA wrappers and the audit/provenance plane

`waggledance/core/magma/*` is the **active autonomy audit/projection plane**:

| Module                       | Wraps legacy                          | Active in autonomy runtime?                                                            |
|------------------------------|---------------------------------------|----------------------------------------------------------------------------------------|
| `audit_projector.py`         | `core/audit_log.py AuditLog`          | YES — `runtime.py:176-177`, called at lines 419, 453, 457, 475, 535, 647, 652, 664, 670, 692, 731, 770, 775, 807, 886, 1219 |
| `event_log_adapter.py`       | `core/learning_ledger.py LearningLedger` | YES — `runtime.py:181-182`, called at 461, 539, 813, 1221                              |
| `provenance.py`              | `core/provenance.py ProvenanceTracker` | YES — `runtime.py:198-199`, called at 551, 817, 1227; HTTP `routes/cross_agent.py:19` |
| `replay_engine.py`           | optional `core/replay_engine.py`      | YES — `runtime.py:192-193`, called at 655, 673, 699, 740, 787, 823, 1225; HTTP `routes/magma.py:113-127` |
| `trust_adapter.py`           | `core/trust_engine.py TrustEngine`    | YES — `runtime.py:186-187`, called at 544, 735, 1223; HTTP `routes/trust.py:19`        |
| `confidence_decay.py`        | (pure function, no wrap)              | NO runtime callers — tests only (`tests/continuity/test_magma_expansion.py:413-511`)  |
| `vector_events.py`           | (event-name contract + JSONL emitter) | NO autonomy runtime caller; tools only                                                 |

The MAGMA layer does, today, function as the audit/provenance/history plane for the autonomy runtime, but the substrate is legacy `core/` modules that are still the actual SQL/JSONL writers (`data/audit_log.db` via `core/audit_log.py:23-30`, `data/learning_ledger.jsonl` via `core/learning_ledger.py`).

### Append-only — convention only

`core/audit_log.py:21` docstring claims "Immutable audit log — append only, no update/delete." but:

- No SQLite trigger blocks UPDATE/DELETE on the `audit` table.
- No `PRAGMA query_only` or read-only attach.
- `ProvenanceTracker._conn = audit_log._conn` (`core/provenance.py:21`) — raw connection is shared.
- `TrustEngine` opens its own handle to the same file (`core/trust_engine.py:119`).

This is a **truthfulness gap** worth noting: claiming an append-only foundation while not enforcing it. Hardening is out of scope for P1 (audit only); P2 may take the first step by exposing append-only as a `MAGMAAppendOnlyPort` contract that tests can assert against.

### `core/replay_engine.py` vs `waggledance/core/magma/replay_engine.py`

Both alive, different scopes:

- `core/replay_engine.py` `ReplayEngine` (line 16) — replays single ChromaDB memory ops via `MemoryWriteProxy`. Active only on the legacy `hivemind.py:578-585` path.
- `waggledance/core/magma/replay_engine.py` `ReplayAdapter` (line 95) — aggregates per-mission event chains, world snapshots, dream/counterfactual replays. Wired in `runtime.py:192-193`. Optionally delegates to legacy via `replay_legacy_session/replay_legacy_mission` (`magma/replay_engine.py:177-195`); default construction does not pass a legacy engine.

## 7. Storage — three-plane reality vs. RULE 4 target

| Plane               | RULE 4 target                                         | Today                                                                 | Gap                                                                    |
|---------------------|--------------------------------------------------------|-----------------------------------------------------------------------|------------------------------------------------------------------------|
| Audit / provenance  | Append-only MAGMA, enforced                           | MAGMA wrappers active; substrate is legacy `core/`; append-only by convention only | Append-only is a soft contract; co-located in single `audit_log.db`    |
| Retrieval / data    | FAISS / vector store layer behind a resolver/registry | `data/faiss/` hard-coded path; `FaissRegistry` instantiated bare      | No path resolver; physical root leaks through `_DEFAULT_FAISS_DIR`     |
| Control / metadata  | Scalable control-plane DB (registries, queries)        | None for solver/family/cell metadata. Each subsystem invents its own JSON or SQLite shape | This is the largest substrate gap; it's exactly what **P2** must build |

## 8. Bottom line for downstream phases

- **P2 (control-plane / data-plane foundation)** must introduce: `path_resolver`, `control_plane.py` (SQLite), `registry_queries`, schemas for solver_families/solvers/capabilities/vector_shards/etc. The substrate gap above is real and material.
- **P5 (Reality View truthfulness)** must not represent solver/family scale via "one node per solver" as long as the path resolver and registry are absent — there is currently no canonical source for that count to query.
- **P6 (README/state truthfulness)** must update `MAGMA_FAISS_SCALING.md §0` to reflect the tool-side producers and update `CURRENT_STATUS.md` to point at `main=8bf1869` (not `a1c4152`).
- **P11 (atomic flip prep)** has its classification grounded by this audit — see `2026-04-28_cutover_model_classification.md`.

## Cross-references

- `docs/journal/2026-04-27_atomic_flip_analysis.md` — original truth analysis (still valid, extended here).
- `docs/architecture/MAGMA_FAISS_SCALING.md` — design doc; §0 needs the producer-status correction noted above.
- `docs/architecture/MAGMA_VECTOR_STAGE2.md` — Stage-2 design (pointer file, not symlink).
- `docs/architecture/PROMPT_2_INPUTS_AND_CONTRACTS.md` — Prompt 2 contract (P11 will issue corrections grounded here).
