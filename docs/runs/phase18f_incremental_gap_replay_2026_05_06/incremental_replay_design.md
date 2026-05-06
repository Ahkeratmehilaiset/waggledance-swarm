# Phase 18F — Incremental Replay Design (BEFORE-CODE)

**Status:** design — written 2026-05-06 from `runtime_gap_path_inventory.md`. Code may begin only after this doc is committed.
**Phase tag:** `phase18f`.
**Candidate prerelease tag:** `v3.10.4-incremental-gap-replay-alpha`.
**Source-prerelease anchor:** `v3.10.3-runtime-gap-replay-alpha` (`6c6ca85...`).

## 1. Purpose

Phase 18E delivered a **whole-corpus** persisted replay: load every Phase 18E event, mine, register, dispatch, idempotent on full re-run. Phase 18F upgrades this into a **cursor-based incremental** replay suitable for production-style autonomous runtime learning:

* Replay processes only rows after the last successful cursor.
* Replay state is durable in `schema_meta`.
* No-op replay does no work.
* New rows arriving after cursor get processed in the next replay call only.
* RuntimeGapDetector signals can be bridged into Phase 18E events.
* Concurrent replay attempts cannot double-register.

The full Phase 18B / 18C / 18E pipeline (`mine_runtime_gaps` → `register_mined_solver_specs` → `LowRiskSolverDispatcher.dispatch_by_features`) is reused unchanged.

## 2. Real data path

```
RuntimeGapDetector.record(GapSignal)        (Phase 12, unchanged)
      |  (writes to runtime_gap_signals with free-form `kind`)
      v
bridge_detector_signal_to_phase18e_event()   (Phase 18F, new, strict)
      |  (returns Phase 18E event mapping; rejects malformed)
      v
persist_runtime_gap_events()                (Phase 18E, unchanged)
      |  (writes runtime_gap_signals row with kind = phase18e.runtime_gap_event.v1)
      v
load_runtime_gap_events_after_id(cursor)    (Phase 18F, new)
      |  (yields PersistedGapEvent records strictly after cursor.last_processed_id)
      v
mine_runtime_gaps(signals)                  (Phase 18B, unchanged)
      v
register_mined_solver_specs()               (Phase 18C, unchanged)
      v
LowRiskSolverDispatcher.dispatch_by_features (Phase 17A, unchanged)
```

## 3. Storage decisions

### 3.1 Event table

**REUSE `runtime_gap_signals`** with `kind = phase18e.runtime_gap_event.v1` (Phase 18E discriminator). No new event table. No `ALTER TABLE`. No new column.

### 3.2 Replay state

**REUSE `schema_meta`** with two phase18f-prefixed keys:

* `phase18f.replay_cursor.v1` → `{"last_processed_id": <int>, "advanced_at_utc": "<iso>"}`
* `phase18f.replay_lock.v1` → `{"acquired_at_utc": "<iso>", "owner": "<pid:host>", "ttl_seconds": <int>}`

No new state table is required. `schema_meta` is the existing key/value/timestamp metadata table installed at schema-v0; using it is consistent with how schema-version itself is stored. Any future state needs (e.g., last failure note) reuse the same table with new phase18f-prefixed keys; no new table.

### 3.3 Compile rules

**EXTEND** the static `_COMPILATION_TABLE` in `mined_solver_runtime.py` with six new strictly-typed entries — one per allowlist family — keyed by new (family_kind, _canonical_features_key) tuples. Each entry is a hardcoded executor artifact dictionary; there is no generic code generation, no allowlist widening, no new family_kind. Total compile-rule count after Phase 18F: 12 (6 original + 6 phase18f).

### 3.4 Schema migration

**ZERO `ALTER TABLE`.** No new column on `runtime_gap_signals`. No new column on `schema_meta`. No new tables. No `MIGRATIONS` entry. The existing schema v4 is unchanged.

## 4. Cursor strategy

### 4.1 Anchor

Use `runtime_gap_signals.id` (autoincrement INTEGER PRIMARY KEY). SQLite guarantees:

* monotonic per-table id assignment for INSERTs (with `AUTOINCREMENT` semantics);
* gap-free within a single insertion path under the per-connection lock.

### 4.2 State shape

```json
{
  "last_processed_id": 0,
  "advanced_at_utc": "2026-05-06T05:42:51Z"
}
```

`last_processed_id = 0` is the sentinel for "no cursor; process all phase18e rows". `last_processed_id = N` means "next replay processes rows with id > N filtered to `kind = phase18e.runtime_gap_event.v1`".

### 4.3 Advancement

Cursor advances **only after** the full mine + register + dispatch pipeline returns successfully. Specifically:

```python
state_before = read_replay_state(cp)
new_events   = list_runtime_gap_signals_after_id(cp, kind=PHASE18E_KIND, after_id=state_before.last_processed_id)
mining       = mine_runtime_gaps([ev.to_phase18b_signal() for ev in new_events])
registration = register_mined_solver_specs(candidates=mining.candidates, control_plane=cp)
# Cursor only advances on success — wrap in try/except; on exception, do NOT update state.
new_max_id   = max(row.id for row in <fetched rows>) if new_events else state_before.last_processed_id
write_replay_state(cp, last_processed_id=new_max_id)
```

If `register_mined_solver_specs` raises, the cursor is left at `state_before.last_processed_id` and the next replay call will retry the same range. (Failure path tested explicitly in P5.)

### 4.4 No-op semantics

If `new_events` is empty, the function:

* does NOT call `mine_runtime_gaps` (or calls it with an empty list, which yields zero candidates);
* does NOT call `register_mined_solver_specs`;
* does NOT advance the cursor;
* returns a `IncrementalReplayResult` with `loaded_event_count = 0` and `cursor_advanced = false`.

## 5. Concurrency lock strategy

### 5.1 Logical lock

Stored in `schema_meta` under `phase18f.replay_lock.v1`. Acquisition path:

```python
existing = get_meta(cp, "phase18f.replay_lock.v1")
if existing is None or _stale(existing):
    set_meta(cp, "phase18f.replay_lock.v1", json.dumps({...}))
    return AcquiredLock(acquired_at_utc, owner)
else:
    return None  # caller returns LOCKED_NOT_RUN
```

`set_meta` uses `INSERT OR REPLACE` under the per-connection lock; SQLite serializes the operation. The TTL guards against stale locks left by a crashed replay attempt; default TTL = 30 s.

### 5.2 Replay outcomes under contention

`run_incremental_gap_replay_once(...)` returns one of:

| Outcome | Meaning |
| --- | --- |
| `IncrementalReplayResult(status="OK", ...)` | Lock acquired, replay completed (possibly 0 events). Cursor advanced if any events processed successfully. |
| `IncrementalReplayResult(status="LOCKED_NOT_RUN", ...)` | Existing non-stale lock present. No replay performed. No cursor change. |
| `IncrementalReplayResult(status="FAILED_NO_ADVANCE", ...)` | Lock acquired but replay raised; cursor unchanged; lock released. |

The proof Stage I demonstrates option `LOCKED_NOT_RUN` by holding the lock manually and asserting the second call returns it.

## 6. Strict event loading

Phase 18F's `load_runtime_gap_events_after_id()`:

1. Query: `SELECT * FROM runtime_gap_signals WHERE kind = 'phase18e.runtime_gap_event.v1' AND id > ? ORDER BY id ASC`.
2. For each row:
   * Empty / null `signal_payload` → counted as `malformed_event_rejection_count`, row skipped.
   * `json.loads(signal_payload)` raises → counted as `malformed_event_rejection_count`, row skipped.
   * Parsed value is not a JSON object (i.e. is array / string / number / null / bool) → counted as `type_confusion_rejection_count`, row skipped.
   * Successful parse → call `normalize_runtime_gap_event()` (Phase 18E):
     * `GapEventSchemaError` whose message contains `"forbidden"` → counted as `forbidden_field_rejections`, row skipped;
     * other `GapEventSchemaError` → counted as `malformed_event_rejection_count`, row skipped;
     * success → append to result list.
3. Return `(events, max_id_seen, rejection_counters)`.

The function is **strict** in what it accepts (Phase 18E shape exactly) and **lenient** about historical row corruption (counts and skips rather than raising). This means a single corrupted historical row does not break ongoing replay.

## 7. RuntimeGapDetector bridge

```python
def bridge_detector_signal_to_phase18e_event(
    signal: GapSignal,
    *,
    raw_query: str,
    miss_reason: str,
    confidence_hint: float,
    risk_label: str,
    evidence_ref: str,
    cluster_window: str = "",
    occurred_at_utc: str | None = None,
) -> dict[str, Any]:
    """Adapter: detector signal -> Phase 18E event dict."""
```

* `signal.payload` MUST be a Mapping containing `feature_dict` (also Mapping). If absent or wrong type → `BridgeRejectionError` (subclass of `ValueError`).
* `signal.family_kind` MUST be a non-empty string. (The miner will assign verdict — out-of-family signals still bridge but won't register.)
* `confidence_hint` clamped to [0, 1]; out-of-range raises `BridgeRejectionError`.
* `evidence_ref` becomes the audit anchor; default to `"audit:phase18f:bridge:<signal.family_kind>:<signal_kind>"`.

A second public helper, `persist_detector_gap_signals_as_replay_events(cp, items)`, takes a sequence of `(GapSignal, kwargs_dict)` tuples and round-trips each through `bridge_detector_signal_to_phase18e_event` + Phase 18E `persist_runtime_gap_events`. It returns a result dataclass counting bridged / persisted / rejected / forbidden / malformed.

The existing `RuntimeGapDetector.record()` write path is **untouched** by Phase 18F. Phase 18F only **reads** existing detector-shaped rows or **wraps** new ones via the adapter.

## 8. Six-family post-cursor capability proof

Phase 18F's proof harness Stage D appends six events (one per family) using new feature_dicts that the Phase 18F-extended compilation table supports. After Stage E (post-cursor replay), the DB holds:

* 6 original phase18e auto-promoted solvers (from Stage A seed).
* 6 new phase18f-extended auto-promoted solvers.
* Total = 12 unique solver rows; `families_covered = 6` for both the seed-replay and the post-cursor replay.

Stage E's dispatch fixture has 18 cases — three per family, each using the **post-cursor** feature_dict so the dispatcher returns the new solver, not the original one. Total dispatch hit count over the run: 18 (Stage B) + 18 (Stage E) = 36. Each individual stage asserts 18/18 to satisfy the master-prompt gate `dispatch_case_count >= 18` per replay.

## 9. Files to add / change

| File | Action |
| --- | --- |
| `waggledance/core/autonomy_growth/incremental_gap_replay.py` | **NEW** — incremental loader, replay-state store, lock, run_once. |
| `waggledance/core/autonomy_growth/runtime_gap_replay.py` | minor: re-export `PHASE18E_RUNTIME_GAP_EVENT_KIND` (already exported); no behavior change. |
| `waggledance/core/autonomy_growth/mined_solver_runtime.py` | extend `_COMPILATION_TABLE` with 6 new entries (one per family). |
| `waggledance/core/storage/control_plane.py` | add `set_meta` / `get_meta` / `delete_meta` helpers; add `list_runtime_gap_signals_after_id`. |
| `tools/run_phase18f_incremental_gap_replay_proof.py` | **NEW** — 10-stage proof harness. |
| `tests/autonomy_growth/test_phase18f_incremental_gap_replay.py` | **NEW** — ≥35 tests. |
| `.dockerignore` | add carve-out for the new proof harness. |
| `docs/benchmarks/INCREMENTAL_RUNTIME_GAP_REPLAY_2026.md` | **NEW** — public report. |
| `docs/benchmarks/COMPETITIVE_EVIDENCE_MATRIX_2026.md` | axis M wording bumped. |
| `CHANGELOG.md`, `CURRENT_STATUS.md`, `README.md`, `docs/release/RELEASE_READINESS.md` | candidate-state entries. |
| `docs/runs/phase18f_incremental_gap_replay_2026_05_06/*` | session folder. |

**Files NOT changed:**

* `waggledance/core/autonomy_growth/gap_intake.py` (RuntimeGapDetector unchanged).
* `waggledance/core/autonomy_growth/gap_mining.py` (Phase 18B unchanged).
* `waggledance/core/autonomy_growth/gap_candidate.py`.
* `waggledance/core/autonomy_growth/runtime_query_router.py`.
* `waggledance/core/autonomy_growth/solver_dispatcher.py`.
* `waggledance/core/storage/control_plane_schema.py` (no schema migration).
* All Phase 18A bundle / 18B proof / 18C proof / 18E proof files.
* All `phase8.5/*` branches.

## 10. Tests strategy

≥35 tests in `tests/autonomy_growth/test_phase18f_incremental_gap_replay.py` covering the master-prompt-listed scenarios. Plus targeted carry-forward (Phase 18F + 18E + 18C + 18B + 18A + phase10 + storage + ui_hologram + solver_router). See P5 master-prompt list.

## 11. Docker `--network none` strategy

Build `waggledance:phase18f` (one new `.dockerignore` carve-out for the Phase 18F harness). Run 5 offline invocations: 18F + 18E + 18C + 18B + 18A validator. All must exit 0.

## 12. Public claim boundaries

**PROVEN (after Phase 18F):**

* incremental cursor replay processes only rows after the last successful cursor;
* no-op replay processes zero rows and creates zero new solvers / capability features / artifacts;
* post-cursor allowlisted events register as new runtime-dispatchable solvers;
* RuntimeGapDetector signals can be bridged into Phase 18E events with strict validation;
* concurrent replay returns LOCKED_NOT_RUN (or serialized no-op);
* `runtime_gap_signals` reused; no parallel event table;
* schema unchanged: no `ALTER TABLE`, no new column, no new table.

**NOT_CLAIMED:**

* high-risk autonomy;
* new family creation;
* live builder execution (still quarantined);
* cloud intelligence;
* raw intelligence superiority;
* cross-vendor ranking;
* "world fastest" / "beats all competitors";
* consciousness, sentience, awareness, AGI;
* production-certified factory deployment.

## 13. Release gates

See P4 master-prompt list. Decision A only if every gate passes.

## 14. Rollback

* If host carry-forward / Phase 18F proof / Docker / fresh-clone fails, hold branch locally; switch to Decision B; do not push or PR.
* If CI fails, do not merge; push fixes (still no tag) or close PR.
* If post-merge tag-time reproduction fails, do not tag; revert PR; do not move v3.8.0 / v3.10.3.
* Schema additions are zero ⇒ rollback = revert PR; no DB migration to undo.

## 15. Stop / abort triggers

Phase 18F switches to Decision B and stops fail-closed if any master-prompt STOP trigger fires. Each is implemented as a release-gate boolean in the proof JSON; the gate's failure is the abort signal. See `release_decision.md` (P9).
