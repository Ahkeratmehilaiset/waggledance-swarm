# Phase 18F — Release Decision

**Decision:** **A — release `v3.10.4-incremental-gap-replay-alpha` PRERELEASE.**
**Date (UTC):** 2026-05-06
**Branch:** `phase18f/incremental-gap-replay`
**Base SHA:** `36ff4ec8d81e1addccd7025a71decbba25a73f05` (Phase 18E post-release docs PR #87 squash-merge).

## Gate evaluation

All Phase 18F release gates green:

| Gate | Result |
| --- | --- |
| P0 baseline + 9 prior tags unchanged | PASS |
| P0 token-hygiene count-only scan | 0 hits |
| Phase 18A bundle still validates (carry-forward) | PASS |
| Phase 18B proof still passes (carry-forward) | PASS |
| Phase 18C proof still passes (carry-forward) | PASS |
| Phase 18E proof still passes (carry-forward) | PASS |
| Inventory documents `runtime_gap_signals` table reuse + `schema_meta` for replay state | PASS |
| Design doc written before code | PASS |
| Mainline module implemented (`incremental_gap_replay.py`) | PASS |
| ControlPlaneDB helpers added (`set_meta`/`get_meta`/`delete_meta` + `after_id` filter) | PASS — no schema change |
| Phase 18C `_COMPILATION_TABLE` extended with 6 new strict per-family rules | PASS |
| **Schema unchanged** (no `ALTER TABLE`, no new column, no new table; `schema_version == 4`) | PASS |
| Phase 18E `persist_runtime_gap_events` defensive fix for type-confused historical rows | PASS |
| Proof harness implemented (10 stages: A–J) | PASS |
| `seed_inserted_event_count >= 30` | 32 ≥ 30 |
| `first_replay_new_event_count >= 30` | 32 ≥ 30 |
| `first_replay_registered_solver_count >= 6` | 6 ≥ 6 |
| `first_replay_families_covered == 6` | 6 == 6 |
| `first_replay_dispatch_case_count >= 18` | 18 == 18 |
| `first_replay_all_dispatch_succeeded` | failure_count == 0 |
| `first_replay_cursor_advanced` | True |
| `no_op_idempotency_pass` | True (0/0/0 extras) |
| `appended_event_count >= 12` | 12 == 12 |
| `appended_events_inserted >= 12` | 12 == 12 |
| `appended_allowlisted_family_coverage == 6` | 6 == 6 |
| `third_replay_new_event_count == appended_events_inserted` | 12 == 12 |
| `third_replay_registered_solver_count >= 6` | 6 ≥ 6 |
| `third_replay_families_covered == 6` | 6 == 6 |
| `third_replay_dispatch_case_count >= 18` | 18 == 18 |
| `third_replay_all_dispatch_succeeded` | failure_count == 0 |
| `total_registered_solver_count >= 12` | 12 ≥ 12 |
| `fourth_replay_no_op` | True |
| `type_confusion_rejected >= 3` | 3 == 3 |
| `malformed_rejected >= 1` | 4 ≥ 1 |
| `forbidden_field_rejected >= 1` | 1 ≥ 1 |
| `builder_handoff_executable_count == 0` | 0 |
| `high_risk_executable_count == 0` | 0 |
| `detector_path_identified` | True (gap_intake.RuntimeGapDetector) |
| `detector_bridge_strict_validation_pass` | True |
| `malformed_detector_row_rejected` | True |
| `lock_result == "LOCKED_NOT_RUN"` | True |
| `concurrent_replay_safety_pass` | True (0 duplicate solvers / artifacts) |
| `event_table_reused == "runtime_gap_signals"` | True |
| `no_parallel_event_table` | True |
| `provider_jobs_delta == 0` AND `builder_jobs_delta == 0` | yes |
| `allowlist_unchanged == true` | yes |
| `no_stage2_flip == true` | yes |
| `no_human_approval == true` | yes |
| `no_live_builder_execution == true` | yes |
| `no_high_risk_autonomy == true` | yes |
| `no_model_pull_or_download == true` | yes |
| `no_cloud_api_calls == true` | yes |
| `no_cross_vendor_ranking_claim == true` | yes |
| `no_raw_intelligence_superiority_claim == true` | yes |
| `no_new_pip_dependency == true` | yes |
| `forbidden_claims_absent == true` | yes |
| Phase 18F targeted tests | **46 / 46 PASS** in 5.29 s |
| Targeted carry-forward suite (Phase 18F 46 + 18E 48 + 18C 33 + 18B 19 + 18A 15 + phase10 14 + storage 50 + ui_hologram 22 + solver_router 50) | **297 / 297 PASS** in 26.55 s |
| Docker `--network none` Phase 18F proof | PASS |
| Docker `--network none` Phase 18E carry-forward | PASS |
| Docker `--network none` Phase 18C carry-forward | PASS |
| Docker `--network none` Phase 18B carry-forward | PASS |
| Docker `--network none` Phase 18A bundle validator | PASS |
| `git rev-parse v3.8.0^{}` | `824176eb...` (unchanged) |
| `git rev-parse v3.9.0-producer-fabric-alpha^{}` | `c726995c...` (unchanged) |
| `git rev-parse v3.9.1-local-efficiency-benchmark-alpha^{}` | `f4d0a4a4...` (unchanged) |
| `git rev-parse v3.9.2-local-ollama-baseline-alpha^{}` | `db5d7db1...` (unchanged) |
| `git rev-parse v3.9.3-local-model-sweep-alpha^{}` | `d0704efe...` (unchanged) |
| `git rev-parse v3.10.0-benchmark-schema-alpha^{}` | `4554b24a...` (unchanged) |
| `git rev-parse v3.10.1-gap-miner-feedback-alpha^{}` | `b408b14a...` (unchanged) |
| `git rev-parse v3.10.2-mined-solver-dispatch-alpha^{}` | `e9aa1de1...` (unchanged) |
| `git rev-parse v3.10.3-runtime-gap-replay-alpha^{}` | `6c6ca85...` (unchanged) |
| `gh release list` v3.8.0 status | **Latest** |
| `db_path_is_temp == true` | yes (proof temp dir) |
| `db_committed == false` | yes |
| No DB / SQLite / WAL / SHM file in committed tree | confirmed |

## Tag plan

* Tag name: `v3.10.4-incremental-gap-replay-alpha`.
* `isPrerelease = true`. **NOT** `Latest`.
* Target: the squash-merge SHA of the Phase 18F PR.
* `v3.8.0` remains GitHub Latest. v3.9.0 + v3.9.1 + v3.9.2 + v3.9.3 + v3.10.0 + v3.10.1 + v3.10.2 + v3.10.3 + v3.10.4 alphas all Pre-release.

## Cursor-incrementality status

Phase 18E delivered durable whole-corpus replay; Phase 18F closes the productionization gap by adding cursor-based incremental processing, no-op semantics, post-cursor learning per family, RuntimeGapDetector bridge, and concurrency lock. Replay state (`phase18f.replay_cursor.v1` + `phase18f.replay_lock.v1`) lives in the existing `schema_meta` table; events stay in `runtime_gap_signals`. Schema is unchanged. The Phase 12 detector write path is untouched.

## What this release does NOT do

* Does NOT modify any of the 9 prior tags.
* Does NOT introduce a stable-tagged release.
* Does NOT widen the six-family allowlist.
* Does NOT introduce a new dispatcher, executor, router, or promotion engine — Phase 18F reuses Phase 11–17A runtime path verbatim.
* Does NOT add any new high-risk autonomy mechanism.
* Does NOT execute Stage-2 atomic flip; does NOT collect HUMAN_APPROVAL.
* Does NOT pull or download any Ollama model; does NOT call any cloud LLM API; does NOT execute any live builder lane.
* Does NOT make any cross-vendor ranking claim or raw-intelligence superiority claim.
* Does NOT add any new pip dependency.
* Does NOT change `ControlPlaneDB` schema — no `ALTER TABLE`, no new column, no new table.
