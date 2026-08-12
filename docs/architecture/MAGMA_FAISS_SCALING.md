# MAGMA / FAISS storage layer — staged scaling plan

- **Status:** design document. Nothing in the production runtime changes as a result of this document landing. Each stage lands through its own reviewed commit and its own migration commit.
- **Source of advice:** GPT R5 architectural review (see `docs/plans/GPT_response5.txt`).
- **Companion docs:** `HONEYCOMB_SOLVER_SCALING.md` (why solver scaling needs this), `PHASE8_METRICS.md` (what we measure).

## 0. Current state (truth refreshed 2026-08-12)

Captured to keep this doc honest.

| Layer | Today |
|---|---|
| MAGMA hot store | Single SQLite file `data/audit_log.db` in WAL mode (`core/audit_log.py`). `waggledance.core.magma.audit_projector.AuditProjector` currently recognizes 37 autonomy event types. Append-only. Capability/goal-level granularity, not per-solver-call. |
| FAISS indices — runtime | `core.faiss_store.FaissRegistry()` defaults to `data/faiss/`; the production container supplies no alternate root. `data/faiss_staging/` and `data/faiss_delta_ledger/` are offline legacy materialization inputs, not runtime reads. |
| FAISS Stage-1 migration output | `tools/migrate_to_vector_root.py --apply` can generate an ignored local `data/vector/` tree. The 2026-04-24 checkpoint recorded one such snapshot, but it was not tracked in Git and is absent from this checkout. Runtime does not read that tree. A future reviewed logical-path cutover retains both `data/faiss/` and `data/vector/`; it does not delete or rename either root. |
| Vector event contract | `waggledance/core/magma/vector_events.py` exports the four event names (`solver.upserted`, `vector.upsert_requested`, `vector.delete_requested`, `vector.commit_applied`) plus constructor helpers. Current offline producers include `backfill_axioms_to_hex._build_projection_upsert_event` and its `solver_upserted` batch; `vector_indexer` consumes the stream and emits `vector_commit_applied`. These tools remain off the runtime read path. No in-process autonomy-runtime producer was found in this truth refresh. |
| Embedding cache | `data/embedding_cache.sqlite` — LRU for query embeddings, pure cache. |
| Chroma | News feeds, facts with TTL — separate from FAISS, retrieval-only. |
| EventBus | `InMemoryEventBus` only. No durable bus. Events lost on shutdown. |
| MAGMA write rate | Not measured. Inferable upper bound is "tens per second" during burst; campaign sustained rate is lower. |

The verified candidate snapshot used by the 2026-08-12 benchmark contains 22
solver vectors over 8 flat cells. It is separate from the legacy and Stage-1
trees described above. A separate autonomy-growth proof has exercised 104
promoted solver descriptors, but that does not establish that all 104 are
eligible for this vector projection. Do not use these counts interchangeably.

### Candidate evidence update (2026-08-12)

Commit `3202d5ce` adds a bounded synthetic scale forecast to
`tools/benchmark_magma_faiss_candidate_latency.py`. The forecast is anchored
to one verified candidate snapshot: 22 vectors, 8 cells, dimension 768,
normalized cosine/IP, `faiss.IndexFlatIP`, and FAISS 1.13.2 AVX2. A different
snapshot, topology, per-cell solver set, projection source, embedding
dimension, or FAISS build fails closed before allocation.

The deterministic allocation plans are:

| Scale | Distribution | Total vectors | Largest leaf | Raw f32 payload |
|---|---|---:|---:|---:|
| 10x | uniform | 220 | 28 | 0.645 MiB |
| 10x | observed proportional | 220 | 50 | 0.645 MiB |
| 100x | uniform | 2,200 | 275 | 6.445 MiB |
| 100x | observed proportional | 2,200 | 500 | 6.445 MiB |
| 1000x | uniform | 22,000 | 2,750 | 64.453 MiB |
| 1000x | observed proportional | 22,000 | 5,000 | 64.453 MiB |

`observed proportional` preserves the current 22-vector per-cell ratios; it
is not a worst-case-skew claim. The byte values are only
`N * 768 * sizeof(float32)`. They exclude FAISS metadata, Python/NumPy
temporaries, allocator overhead, and peak process RSS.

The forecast reuses the candidate tool's all-cell merge algorithm, including
local `k + 1` search and cutoff-tie expansion. It does **not** execute the
production read path (`HybridRetrievalService` through
`core.faiss_store.FaissCollection.search`), real multi-scale snapshots,
routing recall, solver quality, or the adversarial cutoff-tie worst case.
Its timings are host/build observations, never a hard gate. Measured latency
values cannot change the current candidate benchmark result, pruning decision,
CLI exit status, runtime authority, or promotion state; a malformed evidence
structure still fails closed at the report boundary.

## 1. Scaling trajectory the storage layer must absorb

| Milestone | Solvers | Hex cells | Raw f32 payload (768-dim) |
|---|---|---|---|
| Candidate anchor | 22 | 8 flat | 0.064 MiB |
| 3 mo | ~400 | 8 flat | 1.172 MiB |
| 6 mo | ~2 400 | 48 (subdivided) | 7.031 MiB |
| 12 mo | ~8 640 | 288 | 25.313 MiB |
| 24 mo | ~34 000 | 1 728 | 99.609 MiB |

These are raw vector payloads, not process-memory estimates. `IndexIVFFlat`
adds index structures and IDs; `IndexIVFPQ` is lossy and can reduce the vector
payload. Neither has a defensible fixed overhead multiplier across builds and
workloads. Even at 24 months the raw vectors fit in hundreds of MB, so
quantization remains a measured RAM/latency/recall tradeoff rather than a
roadmap-date requirement.

## 2. Staged migration — historical target architecture

This is the staged GPT R5 target adopted in April 2026, not a statement that
every named adapter or materializer exists today. Each future stage still
requires its own reviewed implementation and tests. Do not jump stages.

### Stage 1 — original 2026-04-24 scope · no data-model break

This subsection preserves the initial recommendation. Section 7 records what
landed and what changed afterward.

**Original source/tooling changes:**
- Add a migration tool that materializes an ignored local `data/vector/` copy from `data/faiss_staging/` + `data/faiss_delta_ledger/`, physically separate from the audit store.
- Introduce a small manifest per per-cell index: `data/vector/<cell>/index.faiss`, `manifest.json`, `commit.json`.
- Keep MAGMA on SQLite/WAL — **do not migrate yet**.
- Introduce four MAGMA event names for later producers and the Stage-2 indexer:
  - `solver.upserted` — axiom YAML was added or updated
  - `vector.upsert_requested` — re-embed + upsert the vector for solver X
  - `vector.delete_requested` — remove vector for solver X
  - `vector.commit_applied` — projection commit applied; carries `faiss_commit_id`, artifact path, vector count, checksum
- Document the conversion path from the current delta ledger to the new event schema so Stage 2 is mechanical.

**What did not change in that initial slice:**
- Port 8002 runtime behavior
- The audit SQLite schema
- The existing FAISS rebuild job (still nightly)
- The EventBus (still in-process)

**Why first:** GPT R5: "make FAISS state derivable from MAGMA-style delta events" + "add per-cell projections before you add a distributed bus". Stage 1 costs are low: a rename and a documented event contract. Everything after Stage 1 flows from this separation.

### Stage 2 — target durable write path · NATS JetStream

This remains a target. The current `tools/vector_indexer.py` is a
projection-only skeleton: its strict path writes commit metadata/documents and
`current.json` with `index_kind=none`; it does not create searchable FAISS.
The `vector-indexer` name below means the future searchable materializer.

**Triggers to start Stage 2:**
- Campaign `ui_gauntlet_400h` completes its 400 h budget
- One clean 12 h preventive-restart cycle under the current zombie-reap code
- MAGMA sustained write rate crosses ~50 events/s or p95 SQLite commit latency crosses ~20 ms

**What changes in code:**
- Introduce `waggledance/adapters/bus/jetstream.py` alongside the existing in-memory bus. Interface stays identical; runtime decides which to use by config.
- JetStream subjects: `magma.events.<cell>`, publishers are the existing code paths that write to MAGMA.
- Durable consumers:
  - `audit-projector` → writes to `data/magma/hot/<cell>.sqlite`
  - future searchable `vector-indexer` → reads `vector.*` events, writes `data/vector/<cell>/index.faiss`, emits `vector.commit_applied`
  - `cold-archiver` → streams into `data/cold/parquet/YYYY/MM/DD/`
- SQLite hot projections shrink to "recent events + operator views", not the permanent record.

**What does not change in the target transition:**
- Searchable projection files live under the target `data/vector/` root
- Audit semantics
- Per-cell boundaries

**Why second:** GPT R5 explicitly: "JetStream gives you persisted streams, replay, durable consumers, subject partitioning, and replication, which is exactly what you want if MAGMA events need to drive multiple materializers." Redis Pub/Sub deliberately not chosen because fire-and-forget violates audit-completeness.

### Stage 3 — analytics + multi-writer · conditional

**Triggers to start Stage 3 (any one):**
- Multiple concurrent writer processes
- Analytics workload heavier than DuckDB-over-Parquet comfortably handles
- Need for multi-region replication

**What changes in code:**
- Postgres for control-plane SQL (not for hot MAGMA writes — those stay on JetStream + local SQLite projection)
- ClickHouse or keep DuckDB-over-Parquet for cold analytics
- Nothing else moves

**What does NOT change:**
- MAGMA write path (still JetStream)
- FAISS layout (still per-cell files)
- Event-sourcing semantics

**Why last:** GPT R5 is explicit that Postgres/ClickHouse only belong in the picture when the actual pressure arrives. Adopting them earlier creates operational weight the project cannot afford.

## 3. Physical layout — canonical target

This is the layout Stage 2 should end up with. Stage 1 gets us partway (the `data/vector/` root).

```
data/
├── magma/
│   ├── hot/
│   │   └── <cell>.sqlite          # recent events, operator views
│   └── snapshot/
│       └── YYYY-MM-DD.parquet     # periodic dumps
├── vector/
│   └── <leaf-cell>/
│       ├── index.faiss
│       ├── manifest.json          # dim, count, index_type, training_id
│       └── commit.json            # faiss_commit_id, produced_at, source_events[]
├── cold/
│   └── parquet/
│       └── YYYY/MM/DD/
│           └── events_<cell>_<segment>.parquet
└── audit_log.db                   # legacy during Stage 1; retired in Stage 2
```

Subdivision to leaf cells is straightforward under this layout: each new leaf gets its own directory, its own FAISS file, its own commit history.

Parent-summary indices (Phase 7 roadmap) go under `data/vector/_parents/<cell>/index.faiss` so they don't collide with leaf names.

## 4. FAISS index choice by library size — review heuristics

These are conservative review boundaries, not automatic cutovers or runtime
authority. The current synthetic forecast refuses any plan with a leaf count
of 10,000 or more and reports `index_tier_transition_required`; it does not
select, train, or authorize an IVF index.

Ordered rules for the current normalized cosine/IP embedding contract:

1. **< 10 k vectors per leaf:** flat `IndexFlatIP` over validated
   L2-normalized float32 vectors. This is exact cosine/IP search for the
   indexed corpus. Re-measure build/search latency and memory on each frozen
   snapshot/build instead of assuming they are negligible.
2. **10 k – 100 k:** evaluate `IndexIVFFlat` with inner-product metric.
   `nlist = sqrt(N)` is only a starting candidate; tune `nlist` and `nprobe`
   against a frozen per-cell oracle so recall@10 and latency both meet their
   preregistered criteria.
3. **100 k – 1 M:** evaluate inner-product `IndexIVFPQ`. Treat `m = dim/4`
   and `nlist = sqrt(N)` as candidates, not constants. Validate recall and PQ
   error per cell before accepting lossy compression.
4. **> 1 M per leaf:** the leaf is too large. Phase 7 subdivision should fire before we get here. If it doesn't, that's a topology bug, not a vector-store bug.

Binary vectors (e.g. `IndexBinaryFlat`, `IndexBinaryHNSW`) enter the picture only when:
- Embeddings themselves are binary-trained (we don't produce those today), OR
- RAM budget hits the edge of the class (~ < 1 GB total), not for "intelligence at scale" reasons

Per GPT R5 §3 on quantization: **don't cut over just because the roadmap says 34 k solvers. Cut over when leaf counts or parent summaries actually multiply memory.**

## 5. Event sourcing with materialized projections — the pattern

GPT R5: "The pattern you are asking about at the end is called event sourcing with materialized projections."

In this architecture:
- **Source of truth:** MAGMA events in JetStream (Stage 2) or SQLite WAL (Stage 1)
- **Projection A:** FAISS per-cell indices under `data/vector/`
- **Projection B:** SQLite hot views for operator reads
- **Projection C:** Parquet/DuckDB cold analytics
- **Projection D (future):** graph export to Neo4j / similar for topology analysis

Each projection tracks its own position in the stream. Rebuilding any projection is a matter of replaying from an offset — the FAISS files become disposable derived state, which is exactly what they should be.

This pattern explicitly avoids:
- "Audit trail that ALSO stores vectors inline" (makes MAGMA a blob store)
- "Vectors as the system of record" (vectors are derived, not authoritative)
- "Global index with payload filters at scale" (couples cells, rebuilds, and corruption domains)

## 6. What is NOT in this plan

- CRDTs. GPT R5: "not yet. I would keep MAGMA single-writer per cell with idempotent event IDs and async backup/replication. CRDT-shaping the whole audit layer is overkill unless you truly need concurrent disconnected writers into the same cell's authoritative stream."
- External vector DB (Milvus / Qdrant / Weaviate / pgvector). Local-first design with plain FAISS files stays simpler at the current and projected scale.
- Replacing the Chroma news-feed store. That's a separate retrieval-context layer and does not scale with the solver library; leave it alone.
- Real-time streaming to clients. Out of scope for the audit/vector scaling question.

## 7. Stage 1 — historical 2026-04-24 checkpoint and later status

The initial Stage 1 source commit was **additive**: it added the event contract,
migration tool, and tests. Its contemporaneous report recorded a generated
local `data/vector/` snapshot of offline staging material. `data/` is ignored,
so that output was not committed and is absent from this checkout. Nothing in
the runtime read path changed. Runtime still defaults to `data/faiss/`. The
later `STAGE2_CUTOVER_RFC.md` describes a possible reviewed logical-path
binding change after its prerequisites; both physical roots remain on disk.

Tracked source that shipped in that initial commit:

1. `waggledance/core/magma/vector_events.py` — the four event-name
   constants, a frozen `VectorEvent` dataclass, per-event payload
   validation, and helper constructors. `event_id()` excludes `ts`
   so replay-time dedup is cheap. All 16 `tests/test_vector_events.py`
   cases pass.
2. `tools/migrate_to_vector_root.py` — three modes:
   - dry-run (default): describes what would happen
   - `--apply`: materializes `data/vector/`, idempotent; `--force`
     overwrites an existing target
   - `--verify`: byte-compares legacy and snapshot, reports drift
3. `tests/test_migrate_to_vector_root.py` (13 cases) — apply
   preserves index bytes exactly, legacy tree untouched after
   migration, `--apply` is idempotent, `--force` restores target,
   per-cell manifest has required fields, commit_id is deterministic
   across runs, verify detects source drift, ledger-missing cell
   still migrates.

The checkpoint reported this generated, ignored local output:

- `data/vector/<cell>/` for each of the 8 cells:
  - `index.faiss` — byte-identical copy of `data/faiss_staging/<cell>/index.faiss`
  - `meta.json` — byte-identical copy
  - `manifest.json` — per-cell descriptor (schema_version, dim,
    doc_count, canonical_solver_count, source_version + checksum,
    ledger_hwm + entry count, index_checksum)
  - `commit.json` — MAGMA-facing projection pointer with a
    deterministic `faiss_commit_id` derived from the source
    checksum + ledger hwm + index checksum
- Top-level `data/vector/manifest.json` and
  `data/vector/cell_centroids.json` copied from staging.

What did not ship in that initial commit (historical record):

- Emitting the four vector events from existing FAISS write paths
  (`tools/backfill_axioms_to_hex.py` and similar).
- Repointing runtime FAISS reads at `data/vector/`.
- Consumers (`vector-indexer`, `cold-archiver`). Those are Stage 2.

Subsequent status as of 2026-08-12:

- `backfill_axioms_to_hex` now emits `solver.upserted` and
  `vector.upsert_requested` events.
- `vector_indexer` now consumes vector events, writes projection-only commit
  artifacts, and emits `vector.commit_applied`. It still does not build the
  production searchable index or join the runtime read path.
- Production `FaissRegistry` still defaults to `data/faiss/`; no cutover to
  `data/vector/` is claimed here.

## 8. Decision log (for future readers)

| Date | Decision | Reason |
|---|---|---|
| 2026-04-24 | Keep SQLite/WAL for MAGMA through Stage 1 | GPT R5: no current pressure; single-writer WAL handles present volume |
| 2026-04-24 | Separate FAISS root to `data/vector/` in Stage 1 | GPT R5: rebuild blast radius must be cell-local |
| 2026-04-24 | Make FAISS state derivable from MAGMA events | Event-sourced projections pattern; vector files become disposable |
| 2026-04-24 | Pick JetStream for Stage 2 durable bus | GPT R5: persisted streams + replay + durable consumers; Redis Pub/Sub fire-and-forget disqualified |
| 2026-04-24 | Defer quantization (PQ/IVFPQ) until actual RAM pressure | GPT R5: 99.609 MiB raw at 34k @ 768-dim is not a memory problem yet |
| 2026-04-24 | No CRDTs | GPT R5: single-writer per cell + idempotent event IDs is enough |
| 2026-04-24 | No external vector DB | Local-first design + plain FAISS stays simpler at projected scale |
| 2026-08-12 | Bind the current flat tier to normalized `IndexFlatIP` | Candidate snapshots and search use cosine/IP; the prior `IndexFlatL2` wording diverged from executable behavior |
| 2026-08-12 | Treat 10k/leaf as a review boundary only | The bounded forecast stops before allocation at the boundary and grants no index-tier, pruning, runtime, or promotion authority |
