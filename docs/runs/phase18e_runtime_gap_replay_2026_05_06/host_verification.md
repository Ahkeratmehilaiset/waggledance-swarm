# Phase 18E — P5 Host Verification

**Date (UTC):** 2026-05-06
**Worktree:** `C:\Python\project2-phase18e-runtime-gap-replay`
**Branch:** `phase18e/runtime-gap-replay` (based on origin/main `7d1dede`)
**Python:** 3.13 (host default)

## Phase 18E targeted suite

```
python -X utf8 -m pytest tests/autonomy_growth/test_phase18e_runtime_gap_replay.py -x -q
→ 48 passed in 3.08s
```

Coverage areas in the 48-test suite:

* Normalization happy path (one parametrized test per allowlist family).
* Deterministic `event_id` and `provenance_hash` across calls.
* `cluster_window` distinguishes events with otherwise-identical content.
* Unsupported `schema_version` fail-closed.
* Missing required field fail-closed (parametrized over all 11 required fields).
* `feature_dict` must be a Mapping.
* `confidence_hint` out of [0,1] fail-closed.
* Forbidden field key (`token`, `password`, `api_key`) fail-closed.
* Non-mapping raw input fail-closed.
* Persist inserts each event; idempotent on repeat (skipped event ids returned).
* Persist rejects malformed batch; rejects forbidden-field event.
* Load returns only `phase18e.runtime_gap_event.v1` rows; Phase 12 detector rows untouched.
* Load round-trips canonical shape; filters by `source`.
* Replay calls real `mine_runtime_gaps`.
* Replay registers all 6 ALLOWLISTED families.
* Replay does not register out-of-family / high-risk / builder-handoff candidates.
* Replay through real `LowRiskSolverDispatcher.dispatch_by_features` returns hits with `reason in ("hit", "hit_by_features")` and correct outputs.
* Re-replay produces zero extra rows in `solvers`, `solver_capability_features`, `solver_artifacts`.
* Carry-forward import smokes for Phase 18A / 18B / 18C tools.
* Allowlist tuple invariant unchanged.
* `provider_jobs` and `builder_jobs` row totals stay zero.
* `runtime_gap_signals` schema v3 table still present.
* Phase 18E `kind` discriminator round-trips.
* Proof harness end-to-end `release_gate_pass = True`.
* No DB-shaped file under `waggledance/core/autonomy_growth/`.

## Phase 18E proof harness (host run)

```
python -X utf8 tools/run_phase18e_runtime_gap_replay_proof.py --out-dir docs/runs/phase18e_runtime_gap_replay_2026_05_06
```

| Counter | Value |
| --- | --- |
| persisted_event_count | **32** |
| loaded_event_count | **32** |
| malformed_event_rejection_count | 3 |
| forbidden_field_rejections | 1 |
| signals_total | 32 |
| candidates_total | 13 |
| allowlisted_candidate_count | 6 |
| insufficient_evidence_total | 3 |
| out_of_family_rejected_total | 1 |
| high_risk_rejected_total | 1 |
| builder_handoff_quarantine_count | 1 |
| duplicate_suppression_count | 1 |
| registered_solver_count | **6** |
| non_allowlisted_rejected_count | 7 |
| dispatch_case_count | **18** |
| dispatch_success_count | **18** |
| dispatch_failure_count | 0 |
| families_covered | **6** |
| replay_idempotency_pass | **True** |
| second_persist_inserted | 0 |
| second_persist_skipped_existing | 32 |
| second_replay_extra_solvers | 0 |
| second_replay_extra_capability_features | 0 |
| second_replay_extra_artifacts | 0 |
| provider_jobs_delta / builder_jobs_delta | 0 / 0 |
| forbidden_claims_absent | True |
| **release_gate_pass** | **True** |

## Targeted carry-forward suite

```
python -X utf8 -m pytest \
  tests/autonomy_growth/test_phase18e_runtime_gap_replay.py \
  tests/autonomy_growth/test_phase18c_mined_solver_runtime_dispatch.py \
  tests/autonomy_growth/test_phase18b_gap_miner_feedback.py \
  tests/benchmarks/test_phase18a_benchmark_externalization.py \
  tests/phase10/ \
  tests/storage/ \
  tests/ui_hologram/ \
  tests/autonomy/test_solver_router.py \
  -q
→ 251 passed in 18.29s
```

## Verdict

All host gates GREEN. The Phase 18E proof harness `release_gate_pass = True`. No regression in the carry-forward suite. The schema-unchanged claim holds (no `ALTER TABLE`, no new column). Phase 18E may proceed to P6 Docker `--network none` carry-forward.
