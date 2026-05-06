# Phase 18E - Persisted Runtime Gap Replay Proof

**Benchmark version:** phase18e.v1
**Source prerelease:** v3.10.2-mined-solver-dispatch-alpha
**Candidate prerelease:** v3.10.3-runtime-gap-replay-alpha
**Base main SHA:** 7d1dedef8e29482ff2f8938af484d681e10ca98f
**Started UTC:** 2026-05-06T05:21:42Z
**Finished UTC:** 2026-05-06T05:21:43Z

## Honesty declarations

* No cloud API calls were made.
* No model was pulled or downloaded.
* No live builder execution.
* No Stage-2 atomic flip.
* No HUMAN_APPROVAL collected.
* Six-family allowlist unchanged.
* Builder handoff quarantined; non-executable.
* Schema unchanged: Phase 18E reuses the existing `runtime_gap_signals` table with `kind = `phase18e.runtime_gap_event.v1`.
* Proof database is a temp file; not committed; not retained.

## Persistence

* persisted_event_count: **32**
* skipped_existing_on_first_persist: 0
* malformed_event_rejection_count: **3**
* forbidden_field_rejections: **1**
* loaded_event_count: **32**

## Phase 18B verdict counters (after replay)

* signals_total: **32**
* candidates_total: **13**
* allowlisted_candidate_count: 6
* insufficient_evidence_total: 3
* out_of_family_rejected_total: 1
* high_risk_rejected_total: 1
* builder_handoff_quarantine_count: 1
* duplicate_suppression_count: 1

## Phase 18C runtime registration (after replay)

* registered_solver_count: **6**
* non_allowlisted_rejected_count: 7

## Runtime dispatch

* dispatch_case_count: **18**
* dispatch_success_count: **18**
* dispatch_failure_count: 0
* families_covered: **6**

## Idempotency

* replay_idempotency_pass: **True**
* second_persist_inserted: 0
* second_persist_skipped_existing: 32
* second_replay_extra_solvers: 0
* second_replay_extra_capability_features: 0
* second_replay_extra_artifacts: 0

## Claim labels

* `builder_handoff`: **QUARANTINED-NOT-AUTOPROMOTED**
* `consciousness`: **NOT_CLAIMED**
* `cross_vendor_ranking`: **NOT_CLAIMED**
* `high_risk_families`: **NOT_CLAIMED**
* `raw_intelligence_vs_frontier_moe`: **NOT_CLAIMED**
* `runtime_dispatch_via_real_path`: **MEASURED-CAPABILITY-AWARE-HIT-BY-FEATURES**
* `runtime_gap_replay`: **PROVEN-DURABLE-PERSISTED-EVENT-REPLAY**
* `schema_change`: **NONE**

## Allowlist + provider/builder invariants

* `allowlist_unchanged`: **True**
* `provider_jobs_delta`: 0
* `builder_jobs_delta`: 0
* `no_stage2_flip`: True
* `no_human_approval`: True
* `no_high_risk_autonomy`: True
* `no_live_builder_execution`: True
* `no_model_pull_or_download`: True
* `no_cloud_api_calls`: True
* `db_path_is_temp`: True
* `db_committed`: False

## Release gate

* `release_gate_pass`: **True**
* `forbidden_claims_absent`: **True**

## What this proves

* Runtime gap events can be persisted as durable, content-keyed rows in the existing `runtime_gap_signals` table without any schema change, and replayed deterministically into the existing Phase 18B miner.
* Replayed allowlisted candidates register through the existing Phase 18C four-step pattern and serve via the real `LowRiskSolverDispatcher.dispatch_by_features` capability-aware path - the same code path live runtime queries use.
* Idempotent re-replay: persisting and replaying the same event set twice does not create extra solver, capability-feature, or artifact rows.
* Non-allowlisted (insufficient / out-of-family / high-risk / builder-handoff / duplicate) and malformed / forbidden-field events never become executable runtime solvers.

## What this does NOT prove

* That the same path scales to high-volume real production traffic (the proof fixture is intentionally small and deterministic).
* That high-risk family auto-promotion is safe (it is explicitly blocked).
* Cross-vendor ranking or raw-intelligence superiority (NOT_CLAIMED).

## Reproduce

```
python -X utf8 tools/run_phase18e_runtime_gap_replay_proof.py --out-dir <out>
```

