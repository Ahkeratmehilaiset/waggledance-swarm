# MAGMA vector — Stage 2 design

- **Status:** design + writer skeleton. Runtime reads are NOT repointed here; `core/faiss_store` still reads the legacy `data/faiss_staging/` tree.
- **Scope of this commit:** event contract freeze, durable checkpoint, atomic writer with apply/rollback semantics, `vector.commit_applied` emission, strong tests.
- **Out of scope for this commit:** JetStream, runtime repoint, FAISS index rebuild, Prometheus additions, multi-node federation.
- **Parent doc:** [`MAGMA_FAISS_SCALING.md`](./MAGMA_FAISS_SCALING.md) — staged migration recommendation from GPT R5.

## 0. Why another stage here

Stage 1 (commit `28dff51` + `167702c`) established:
- `data/vector/` exists as an additive snapshot
- four event names are defined and emittable
- a stub consumer builds per-cell projections from the event log

The stub does NOT write anything. Stage 2 replaces it with a real writer that consumes events, materializes per-cell artifacts, commits them atomically, and advances a durable checkpoint — all while the live `ui_gauntlet_400h` campaign keeps reading from the legacy paths.

When the campaign completes, a small separate commit repoints `core/faiss_store` at `data/vector/<cell>/current/`. That repoint is deliberately NOT part of this stage.

## 1. Event contract (frozen here)

The contract is backward-compatible with Stage 1. Old rows still parse.

```
VectorEvent {
  event           : string   # one of the 4 names
  cell_id         : string
  solver_id       : string | null
  ts              : ISO-8601 utc string
  payload         : object   # event-type-specific, see below
  schema_version  : int      # 1 today
  source          : string | null   # optional provenance (NEW in Stage 2)
}
```

`source` is additive: old rows written by Stage 1 backfill have no source field; readers default to `None`. New Stage-2 writers tag their emissions explicitly (e.g. `source="indexer"` on `vector.commit_applied`).

`commit_ref` linkage lives **inside `payload`** — `vector.commit_applied` carries `payload.faiss_commit_id`. Other events may optionally carry `payload.commit_ref` when they were produced in the context of a specific commit; no consumer enforces this.

### Per-event payload requirements

| Event | Required payload keys | Optional |
|---|---|---|
| `solver.upserted` | `model_id`, `signature`, `source_path` | — |
| `vector.upsert_requested` | `model_id`, `signature` | `reason` |
| `vector.delete_requested` | `model_id` | `reason` |
| `vector.commit_applied` | `faiss_commit_id`, `artifact_path`, `vector_count`, `checksum` | `source_events`, `input_event_range` |

## 2. Checkpoint format

A single JSON file at `data/vector/checkpoints/vector_indexer.json`. CLI and env var overrides (`--checkpoint-path` / `WAGGLE_VECTOR_CHECKPOINT`) make it tmp-path-friendly in tests.

```json
{
  "schema_version": 1,
  "global_last_applied_event_id": "evt_abc",
  "last_applied_ts": "2026-04-24T10:00:00+00:00",
  "per_cell": {
    "thermal": {
      "last_applied_event_id": "evt_abc",
      "commit_id": "faiss_thermal_001",
      "applied_ts": "2026-04-24T09:58:00+00:00"
    }
  }
}
```

### Advance rules

- `per_cell[cell].last_applied_event_id` advances **only** after a successful atomic apply for that cell.
- `global_last_applied_event_id` is the maximum across all cells AFTER an apply pass completes — it represents "every cell has seen at least this event".
- Failed apply for any cell: that cell's entry stays untouched; other cells in the same pass may advance independently.
- Atomic save: checkpoint writer uses `tempfile → os.replace()` so a crash mid-write never leaves a half-written file.

## 3. On-disk layout

Stage 2 layout sits *underneath* each cell's Stage-1 snapshot, not replacing it:

```
data/vector/
├── <cell_id>/
│   ├── index.faiss              ← Stage 1 snapshot (frozen reference)
│   ├── meta.json
│   ├── manifest.json
│   ├── commit.json
│   ├── current.json             ← Stage 2 pointer: {"commit_id": "..."}
│   └── commits/
│       └── <commit_id>/
│           ├── manifest.json
│           ├── commit.json
│           └── vectors.jsonl    ← placeholder payload
├── checkpoints/
│   └── vector_indexer.json
└── events.jsonl
```

The Stage-1 files stay in place. Stage 2 writes only into `commits/<commit_id>/` and swaps the `current.json` pointer atomically.

### Why a pointer file instead of a `current/` symlink

Windows symlinks require administrator rights. Renaming a non-empty directory atomically is not portable. A small JSON pointer file flipped with `os.replace()` is byte-atomic on both platforms and doesn't depend on OS-level linking primitives.

### commit_id

Deterministic: `sha256(canonical_json({cell_id, signatures[solver_id→signature], vector_count}))[:16]` prefixed with `faiss_`. Same cell projection always produces the same commit_id, which makes rerun idempotent and lets consumers verify integrity.

## 4. Atomic write flow (apply pass)

For each cell with pending events:

1. **Compute** desired projection from events since `per_cell[cell].last_applied_event_id`.
2. **Short-circuit**: if the new commit_id equals the cell's current commit_id, skip — no write, no commit_applied event, no checkpoint change. ("No-change cell no bogus commit" per R6 §6.)
3. **Stage** the commit directory: `data/vector/<cell>/commits/<commit_id>/` with manifest.json + commit.json + vectors.jsonl (placeholder in Stage 2).
4. **Compute checksum** over the staged files; store it in manifest.
5. **Swap** the pointer: write `current.json.tmp` with `{"commit_id": "..."}`, then `os.replace(current.json.tmp, current.json)`. This is the only non-reversible step and it is atomic.
6. **Emit** `vector.commit_applied` to the event log with full audit payload (faiss_commit_id, artifact_path, vector_count, checksum, source_events, input_event_range).
7. **Update checkpoint** `per_cell[cell]` entry.
8. **Save checkpoint** (atomic tmp + replace) after all cells in the pass are processed.

If any step 3-5 fails for a cell: that cell's `current.json` still points at the PRIOR commit, and `per_cell[cell]` in the checkpoint still names the prior commit. The partial staged directory under `commits/<commit_id>/` may linger but is harmless — the next apply run either finishes it or overwrites it (commits are content-addressed, so content determines identity).

## 5. Rollback / failure behavior

| Failure | Impact | Recovery |
|---|---|---|
| Crash between stage and swap | Staged dir exists but `current.json` unchanged | Next apply re-stages (idempotent) and swaps |
| Crash between swap and commit_applied emit | `current.json` points at new commit; no audit event | Next apply sees checkpoint prior-commit and re-applies. The duplicate commit_applied has the same event_id (dedup-friendly) |
| Crash between commit_applied emit and checkpoint save | Event emitted, checkpoint not advanced | Next apply sees unchanged checkpoint → re-stages → short-circuits because commit_id matches current.json → no extra emit |
| Crash mid-checkpoint-save | Checkpoint `.tmp` orphan, real file unchanged | Next apply reads prior checkpoint and re-applies the window |
| Disk full during stage | Staged dir partial | Partial staging dir left behind; next run overwrites |
| Checksum mismatch on read | Exception raised, apply aborts | Checkpoint not advanced; operator inspects the cell |

**Invariant:** `current.json` never points at an absent or partial staging dir. The pointer is only swapped after the staging dir is fully written.

## 6. Idempotency model

- **Event_id** is a sha256 over the event minus `ts`, so the same logical event always gets the same id.
- **Checkpoint** records per-cell last applied event id; rerunning the same window is a no-op after the first successful pass.
- **commit_id** is content-addressed; same projection always produces the same id, so the staging path is predictable and rewriting it is harmless.
- **vector.commit_applied** events carry `event_id` that depends on the payload; if the indexer emits the same commit twice (recovery case), both emissions share the same event_id and can be deduped by consumers.

## 7. Emitter of `vector.commit_applied`

Exactly one component: **the vector-indexer writer** (`tools/vector_indexer.py`). Nothing else in the codebase is allowed to emit this event. Other tools may emit `solver.upserted` / `vector.upsert_requested` / `vector.delete_requested` on the producing side (e.g. `tools/backfill_axioms_to_hex.py`) — those drive the writer's input, never its output.

## 8. Relation to MAGMA

Today MAGMA has 28 autonomy event types (`audit_projector.py`). The vector events are a **separate log** (`data/vector/events.jsonl`) until JetStream lands in Stage 2.5+. Why separate:

- MAGMA SQLite WAL is single-writer — vector events would contend with autonomy events.
- Vector events are high-frequency bursts during backfill; MAGMA events are steady-state.
- Keeping them separate during Stage 2 lets the switchover to JetStream carry each stream on its own subject without coupling.

When JetStream lands, both streams move to `magma.events.<cell>` subjects and the JSONL file retires.

## 9. What is explicitly NOT in this commit

- No change to `core/faiss_store` or any runtime adapter
- No change to `start_waggledance.py`
- No Prometheus / `/metrics` additions
- No JetStream adapter
- No FAISS index training (the staged `vectors.jsonl` is a placeholder payload; a later commit replaces it with real faiss-cpu output once the ingestion path is trusted)
- No repointing of runtime reads
- No removal of the Stage-1 snapshot files at `data/vector/<cell>/{index.faiss, meta.json, manifest.json, commit.json}`

## 10. Why runtime is not repointed here

Two reasons:

1. The live `ui_gauntlet_400h` campaign is running against the legacy `data/faiss_staging/` tree and must not be disturbed mid-campaign.
2. The Stage-2 staged payload (`vectors.jsonl`) is a placeholder — repointing runtime to it before real FAISS-compatible index writes are in place would drop retrieval accuracy. Runtime repoint is a separate reviewed commit that pairs with real index generation.

The writer, checkpoint, and atomic apply semantics land first so the repoint commit can be as small as "change one path constant + add a compatibility shim."

## 11. CLI (tools/vector_indexer.py)

| Flag | Effect |
|---|---|
| (none) | Dry-run report — reads events, describes what would apply |
| `--apply` | Performs the atomic write + checkpoint advance + commit_applied emit |
| `--event-log PATH` | Override event log path (default: `data/vector/events.jsonl` or env) |
| `--vector-root PATH` | Override vector tree root |
| `--checkpoint-path PATH` | Override checkpoint file |
| `--since EVT_ID` | Replay starting after a given event id |
| `--cell CELL` | Restrict apply to a specific cell |
| `--json` | Machine-readable output |
| `--force` | Re-apply a cell even if commit_id matches current |

Default posture is **dry-run**. `--apply` is required for any write.

## 12. Migration from Stage 1 stub

The Stage-1 stub exposed `replay(path, since_event_id)` returning a `ReplayReport`. Both symbols are preserved as thin wrappers over the Stage-2 dry-run path so external callers (including existing tests) keep working. The new `apply()` function and checkpoint handling are additive.

## 13. Test checklist covered in `tests/test_vector_indexer.py`

Enumerated per R6 §6:
- old event rows remain readable after schema extension (source=None default)
- full replay equals checkpointed replay
- checkpoint does NOT advance on failed apply
- rerun is idempotent
- `vector.commit_applied` is emitted once per successful apply
- partial failure leaves prior committed artifact intact
- per-cell isolation
- unknown informational events do not corrupt projection
- atomic apply semantics
- event ordering assumptions are explicit and tested
- manifest/checksum mismatch is caught
- no-change cell emits no bogus commit
- `--dry-run` default stays safe
