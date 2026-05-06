# Incremental Runtime Gap Replay — 2026 (Phase 18F)

**Status:** PRERELEASE candidate `v3.10.4-incremental-gap-replay-alpha`. Not GitHub Latest.
**Released as:** prerelease only. `v3.8.0` remains GitHub Latest. `v3.10.3-runtime-gap-replay-alpha` remains the previous prerelease.

## Why this phase exists

Phase 18E ships durable persisted replay: events are persisted into `runtime_gap_signals`, loaded, mined, registered, and dispatched idempotently — but each call replays the **whole** event log. Phase 18F upgrades this into a production-shaped **cursor-based incremental** primitive:

* `run_incremental_gap_replay_once(cp)` reads a cursor from `schema_meta`, loads only events with `id > cursor`, mines + registers + advances the cursor on success.
* No-op replay (no new events) does zero work and creates zero new solvers / capability features / artifacts.
* Appending new events advances the cursor by exactly the number of new rows.
* Concurrent replay returns `LOCKED_NOT_RUN` (logical lock in `schema_meta`).
* The Phase 12 `RuntimeGapDetector` signal shape is bridgeable into the Phase 18E event format via a strict, fail-closed adapter.

## What this proves

```
RuntimeGapDetector.record(GapSignal)                 (Phase 12, unchanged)
   |
   v   bridge_detector_signal_to_phase18e_event()    (Phase 18F, strict)
   v
runtime_gap_signals (kind = phase18e.runtime_gap_event.v1)
   |
   v   load_runtime_gap_events_after_id(cursor)      (Phase 18F, strict + counted-skip)
   v
mine_runtime_gaps                                    (Phase 18B, unchanged)
   v
register_mined_solver_specs                          (Phase 18C, unchanged)
   v
LowRiskSolverDispatcher.dispatch_by_features         (Phase 17A, unchanged)
```

* **Cursor-based incremental replay**: only rows with `id > last_processed_id` are processed. Cursor advances only after success.
* **No-op semantics**: empty cursor delta → zero solvers / capabilities / artifacts created.
* **Post-cursor learning**: appending new ALLOWLISTED feature_dicts produces new auto-promoted solvers in all six families.
* **Strict load**: malformed JSON, type-confused JSON (top-level array / string / null / number), missing required fields, forbidden keys, secret-shaped values are counted and skipped — never raised, never coerced.
* **Detector bridge**: Phase 12 `GapSignal` instances with `payload["feature_dict"]` adapt cleanly; signals missing payload, with non-Mapping payload, missing `feature_dict`, empty `family_kind`, or out-of-range `confidence_hint` are rejected with `BridgeRejectionError`.
* **Concurrency**: a held lock returns `LOCKED_NOT_RUN`; no double-registration.
* **No schema change**: existing `runtime_gap_signals` is the only event table; `schema_meta` is the only state table; `schema_version` remains 4.
* **No allowlist widening**: the six low-risk families (`scalar_unit_conversion`, `lookup_table`, `threshold_rule`, `interval_bucket_classifier`, `linear_arithmetic`, `bounded_interpolation`) remain exactly. Six new compile rules (one per family) extend coverage to a second feature_dict per family — strictly typed, hardcoded, no generic code generation.

## Measured proof (host run + Docker `--network none`)

| Stage | Counter | Value |
| --- | --- | --- |
| A | seed_inserted_event_count | 32 |
| A | seed_malformed_event_rejection_count | 3 |
| A | seed_forbidden_field_rejections | 1 |
| B | first_replay_new_event_count | 32 |
| B | first_replay_registered_solver_count | 6 |
| B | first_replay_families_covered | 6 |
| B | first_replay_dispatch_success_count | 18 |
| B | first_replay_cursor_advanced | True |
| C | second_replay_new_event_count | 0 |
| C | second_replay_extra_solvers/features/artifacts | 0/0/0 |
| C | no_op_idempotency_pass | True |
| D | appended_event_count | 12 |
| D | appended_events_inserted | 12 |
| D | appended_allowlisted_family_coverage | 6 |
| E | third_replay_new_event_count | 12 |
| E | third_replay_registered_solver_count | 6 |
| E | third_replay_families_covered | 6 |
| E | third_replay_dispatch_success_count | 18 |
| E | total_registered_solver_count | 12 |
| F | fourth_replay_new_event_count | 0 |
| F | fourth_replay_extra_solvers/features/artifacts | 0/0/0 |
| G | type_confusion_rejection_count | 3 |
| G | malformed_event_rejection_count | 4 |
| G | forbidden_field_rejections | 1 |
| G | builder_handoff_executable_count | 0 |
| G | high_risk_executable_count | 0 |
| H | detector_bridge_persisted_event_count | 1 |
| H | detector_bridge_strict_validation_pass | True |
| H | malformed_detector_row_rejected | True |
| I | lock_result | `LOCKED_NOT_RUN` |
| I | concurrent_replay_safety_pass | True |
| I | concurrent_duplicate_solver_count | 0 |
| | provider_jobs_delta / builder_jobs_delta | 0 / 0 |
| | event_table_reused | `runtime_gap_signals` |
| | no_parallel_event_table | True |
| | **release_gate_pass** | **True** |

## Storage layout

* **Events:** existing `runtime_gap_signals` table, `kind = phase18e.runtime_gap_event.v1`. No new event table. Same schema-v3 row layout as Phase 12 / 18E.
* **State:** existing `schema_meta` table, two new keys:
  * `phase18f.replay_cursor.v1` → JSON `{"last_processed_id": <int>, "advanced_at_utc": "<iso>"}`.
  * `phase18f.replay_lock.v1` → JSON `{"acquired_at_utc": "<iso>", "owner": "<pid:host>", "ttl_seconds": <int>}`.

`schema_version` remains 4. No `ALTER TABLE`. No `MIGRATIONS` entry. No new column.

## What Phase 18F does NOT claim

* No raw-intelligence superiority claim. **NOT_CLAIMED.**
* No cross-vendor ranking claim. **NOT_CLAIMED.**
* No high-risk family auto-promotion. **BLOCKED.**
* No allowlist widening. The six-family allowlist tuple is unchanged.
* No live builder execution; builder handoff remains a quarantined contract.
* No model pull / download. No cloud API call. No Stage-2 atomic flip. No HUMAN_APPROVAL collected.
* No production-certified factory deployment. No "beats all competitors" or "world fastest" language.
* No consciousness, sentience, awareness, or AGI claim.
* No new pip dependency.

## Reproduce

```
python -X utf8 tools/run_phase18f_incremental_gap_replay_proof.py \
    --out-dir docs/runs/phase18f_incremental_gap_replay_2026_05_06
```

Inside Docker `--network none`:

```
docker build -t waggledance:phase18f -f Dockerfile .
docker run --rm --network none waggledance:phase18f \
    python tools/run_phase18f_incremental_gap_replay_proof.py --out-dir /tmp/p18f
```

Tests:

```
python -X utf8 -m pytest tests/autonomy_growth/test_phase18f_incremental_gap_replay.py -q
→ 46 passed
```

Targeted carry-forward (Phase 18F + 18E + 18C + 18B + 18A + phase10 + storage + ui_hologram + solver_router):

```
python -X utf8 -m pytest \
    tests/autonomy_growth/test_phase18f_incremental_gap_replay.py \
    tests/autonomy_growth/test_phase18e_runtime_gap_replay.py \
    tests/autonomy_growth/test_phase18c_mined_solver_runtime_dispatch.py \
    tests/autonomy_growth/test_phase18b_gap_miner_feedback.py \
    tests/benchmarks/test_phase18a_benchmark_externalization.py \
    tests/phase10/ tests/storage/ tests/ui_hologram/ \
    tests/autonomy/test_solver_router.py -q
→ 297 passed
```
