# Phase 18F — P5/P6 Host Verification

**Date (UTC):** 2026-05-06
**Worktree:** `C:\Python\project2-phase18f-incremental-gap-replay`
**Branch:** `phase18f/incremental-gap-replay` (based on origin/main `36ff4ec`)
**Python:** 3.13 (host default)

## Phase 18F targeted suite

```
python -X utf8 -m pytest tests/autonomy_growth/test_phase18f_incremental_gap_replay.py -x -q
→ 46 passed in 5.29s
```

Coverage areas in the 46-test suite:

* Cursor state initially zero; `write_replay_cursor` persists; cursor lives in `schema_meta` (no new table); cursor survives DB close/reopen.
* First incremental replay processes seed rows; advances cursor; dispatch hits all six families with correct outputs.
* No-op replay processes zero rows; creates zero new solvers / capability features / artifacts.
* Post-cursor incremental replay processes only the appended rows; registers six new phase18f-extended solvers; total registered = 12 (6 + 6); dispatch hits all six new solvers with new feature_dicts.
* Strict load rejects malformed JSON, JSON arrays, JSON strings, JSON nulls, JSON numbers, empty payload, missing required field, forbidden field key, secret-shaped value (Bearer pattern); accepts well-formed.
* Verdict invariants: high-risk events do not register; out-of-family events do not register; builder-handoff events quarantined; duplicate cluster suppressed.
* RuntimeGapDetector bridge accepts compatible signal; rejects no-payload, non-Mapping payload, missing `feature_dict`, empty `family_kind`, out-of-range confidence; round-trips valid + rejects malformed via `persist_detector_gap_signals_as_replay_events`.
* Concurrency: held lock returns `LOCKED_NOT_RUN`; lock released after successful replay; `skip_lock=True` works; second replay does not re-process completed work.
* Allowlist tuple invariant; `provider_jobs` and `builder_jobs` totals stay zero.
* `runtime_gap_signals` is the only event table; schema_version still 4 (no new table, no migration).
* `schema_meta` cursor row holds only `last_processed_id` + `advanced_at_utc` (no event data leak).
* Carry-forward imports for Phase 18A / 18B / 18C / 18E tools.
* End-to-end harness `release_gate_pass = True`.
* No DB-shaped file under `waggledance/core/autonomy_growth/` source.

## Phase 18F proof harness (host run)

```
python -X utf8 tools/run_phase18f_incremental_gap_replay_proof.py \
    --out-dir docs/runs/phase18f_incremental_gap_replay_2026_05_06
```

| Counter | Value |
| --- | --- |
| seed_inserted_event_count | **32** |
| seed_malformed_event_rejection_count | 3 |
| seed_forbidden_field_rejections | 1 |
| **Stage B — first incremental replay** | |
| first_replay_status | OK |
| first_replay_new_event_count | **32** |
| first_replay_registered_solver_count | **6** |
| first_replay_families_covered | **6** |
| first_replay_dispatch_case_count | 18 |
| first_replay_dispatch_success_count | **18** |
| first_replay_dispatch_failure_count | 0 |
| first_replay_cursor_advanced | True |
| **Stage C — no-op replay** | |
| second_replay_new_event_count | 0 |
| second_replay_extra_solvers/features/artifacts | 0/0/0 |
| no_op_idempotency_pass | **True** |
| **Stage D — append post-cursor events** | |
| appended_event_count | 12 |
| appended_events_inserted | 12 |
| appended_allowlisted_family_coverage | **6** |
| **Stage E — post-cursor replay** | |
| third_replay_new_event_count | **12** |
| third_replay_registered_solver_count | **6** |
| third_replay_families_covered | **6** |
| third_replay_dispatch_success_count | **18** |
| third_replay_dispatch_failure_count | 0 |
| total_registered_solver_count | **12** |
| **Stage F — post-cursor no-op** | |
| fourth_replay_new_event_count | 0 |
| fourth_replay_extra_solvers/features/artifacts | 0/0/0 |
| **Stage G — strict rejection** | |
| type_confused_rows_inserted | 4 |
| type_confusion_rejection_count | 3 |
| malformed_event_rejection_count | 4 |
| forbidden_field_rejections | 1 |
| builder_handoff_executable_count | 0 |
| high_risk_executable_count | 0 |
| **Stage H — RuntimeGapDetector bridge** | |
| detector_path_identified | True |
| detector_bridge_persisted_event_count | 1 |
| detector_bridge_strict_validation_pass | True |
| malformed_detector_row_rejected | True |
| **Stage I — concurrency** | |
| lock_result | **LOCKED_NOT_RUN** |
| concurrent_replay_safety_pass | True |
| concurrent_duplicate_solver_count | 0 |
| concurrent_duplicate_artifact_count | 0 |
| **Honesty / invariants** | |
| event_table_reused | runtime_gap_signals |
| no_parallel_event_table | True |
| provider_jobs_delta / builder_jobs_delta | 0 / 0 |
| forbidden_claims_absent | True |
| **release_gate_pass** | **True** |

## Targeted carry-forward suite

```
python -X utf8 -m pytest \
  tests/autonomy_growth/test_phase18f_incremental_gap_replay.py \
  tests/autonomy_growth/test_phase18e_runtime_gap_replay.py \
  tests/autonomy_growth/test_phase18c_mined_solver_runtime_dispatch.py \
  tests/autonomy_growth/test_phase18b_gap_miner_feedback.py \
  tests/benchmarks/test_phase18a_benchmark_externalization.py \
  tests/phase10/ tests/storage/ tests/ui_hologram/ \
  tests/autonomy/test_solver_router.py -q
→ 297 passed in 26.55s
```

## Verdict

All host gates GREEN. Phase 18F may proceed to Docker `--network none` carry-forward.
