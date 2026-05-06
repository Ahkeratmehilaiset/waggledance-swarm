# Phase 18E — Release Decision

**Decision:** **A — release `v3.10.3-runtime-gap-replay-alpha` PRERELEASE.**
**Date (UTC):** 2026-05-06
**Branch:** `phase18e/runtime-gap-replay`
**Base SHA:** `7d1dedef8e29482ff2f8938af484d681e10ca98f` (Phase 18D PR #85 squash-merge).

## Gate evaluation

All Phase 18E release gates green:

| Gate | Result |
| --- | --- |
| P0 baseline + 8 prior tags unchanged | PASS |
| P0 secret hygiene (9 stale token-bearing branch upstreams cleaned) | PASS |
| Phase 18A bundle still validates (carry-forward) | PASS |
| Phase 18B proof still passes (carry-forward) | PASS |
| Phase 18C proof still passes (carry-forward) | PASS |
| Inventory identifies existing `runtime_gap_signals` (Phase 12) | PASS — design doc justifies REUSE not parallel table |
| Design doc written before code | PASS — `runtime_gap_replay_design.md` |
| Mainline module implemented | `waggledance/core/autonomy_growth/runtime_gap_replay.py` |
| Read-only DB helper added | `ControlPlaneDB.list_runtime_gap_signals` (no schema change) |
| Proof harness implemented | `tools/run_phase18e_runtime_gap_replay_proof.py` |
| `runtime_gap_signals` schema unchanged (no `ALTER TABLE`, no new column) | PASS |
| Persisted event uses Phase 18E discriminator (`kind = phase18e.runtime_gap_event.v1`) | PASS |
| Replay calls real `mine_runtime_gaps` | PASS |
| Replay calls real `register_mined_solver_specs` | PASS |
| Real `LowRiskSolverDispatcher.dispatch_by_features` | PASS — `reason="hit_by_features"` for each registered solver |
| `persisted_event_count >= 30` | 32 ≥ 30 |
| `loaded_event_count >= 30` | 32 ≥ 30 |
| `allowlisted_candidate_count >= 6` | 6 ≥ 6 |
| `registered_solver_count >= 6` | 6 ≥ 6 |
| `families_covered == 6` | 6 == 6 |
| `dispatch_case_count >= 18` | 18 == 18 |
| `dispatch_success_count == dispatch_case_count` | 18 == 18 |
| `dispatch_failure_count == 0` | 0 |
| `replay_idempotency_pass == true` | True |
| Second persist inserted == 0; skipped == 32 | PASS |
| Second replay extra solvers / capability features / artifacts | 0 / 0 / 0 |
| `non_allowlisted_rejected_count >= 5` | 7 ≥ 5 (3 INSUFFICIENT + 1 OUT_OF_FAMILY + 1 HIGH_RISK + 1 BUILDER_HANDOFF + 1 DUPLICATE) |
| `malformed_event_rejection_count >= 1` | 3 ≥ 1 |
| `forbidden_field_rejections >= 1` | 1 ≥ 1 |
| Builder handoff remains quarantined | PASS — 0 builder-handoff candidates registered |
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
| `forbidden_claims_absent == true` | yes |
| Phase 18E targeted tests | **48 / 48 PASS** in 3.08 s |
| Targeted carry-forward suite (Phase 18E 48 + 18C 33 + 18B 19 + 18A 15 + phase10 14 + storage 50 + ui_hologram 22 + solver_router 50) | **251 / 251 PASS** in 18.29 s |
| Docker `--network none` Phase 18E proof | PASS |
| Docker `--network none` Phase 18C carry-forward | PASS |
| Docker `--network none` Phase 18B carry-forward | PASS |
| Docker `--network none` Phase 18A bundle validation | PASS |
| `git rev-parse v3.8.0^{}` | `824176eb...` (unchanged) |
| `git rev-parse v3.9.0-producer-fabric-alpha^{}` | `c726995c...` (unchanged) |
| `git rev-parse v3.9.1-local-efficiency-benchmark-alpha^{}` | `f4d0a4a4...` (unchanged) |
| `git rev-parse v3.9.2-local-ollama-baseline-alpha^{}` | `db5d7db1...` (unchanged) |
| `git rev-parse v3.9.3-local-model-sweep-alpha^{}` | `d0704efe...` (unchanged) |
| `git rev-parse v3.10.0-benchmark-schema-alpha^{}` | `4554b24a...` (unchanged) |
| `git rev-parse v3.10.1-gap-miner-feedback-alpha^{}` | `b408b14a...` (unchanged) |
| `git rev-parse v3.10.2-mined-solver-dispatch-alpha^{}` | `e9aa1de1...` (unchanged) |
| `gh release list` v3.8.0 status | **Latest** |
| `db_path_is_temp == true` | yes (proof temp dir) |
| `db_committed == false` | yes |
| No DB / SQLite / WAL / SHM file in committed tree | confirmed |
| No `.env` / token / secret / credential file in stage | confirmed |
| Local Git config token-bearing entry count | 0 (P0 cleanup) |
| `.dockerignore` extension scoped to Phase 18E proof carve-out only | confirmed |
| No new pip dependency | confirmed |

## Tag plan

* Tag name: `v3.10.3-runtime-gap-replay-alpha`.
* `isPrerelease = true`. **NOT** `Latest`.
* Target: the squash-merge SHA of the Phase 18E PR.
* GitHub release: `gh release create v3.10.3-runtime-gap-replay-alpha --prerelease --target <merge SHA> --notes-file <release_notes>`.
* `v3.8.0` remains GitHub Latest. `v3.9.0` + `v3.9.1` + `v3.9.2` + `v3.9.3` + `v3.10.0` + `v3.10.1` + `v3.10.2` + `v3.10.3` alphas all Pre-release.

## Capability replay status

The Phase 18C `capability_lookup_status` was already `PROVEN-BY-RUNTIME-PATH-WITH-MINED-SPECS`. Phase 18E adds the **durability** axis: the same proof now starts from a persisted, content-keyed audit log rather than an in-memory fixture. This closes the "audit-replayable autonomous learning loop" maturity step explicitly identified by the operator.

## What this release does NOT do

* Does NOT modify any of the 8 prior tags.
* Does NOT introduce a stable-tagged release.
* Does NOT widen the six-family allowlist.
* Does NOT add any new high-risk autonomy mechanism.
* Does NOT execute Stage-2 atomic flip; does NOT collect HUMAN_APPROVAL.
* Does NOT pull or download any Ollama model; does NOT call any cloud LLM API; does NOT execute any live builder lane.
* Does NOT make any cross-vendor ranking claim or raw-intelligence superiority claim.
* Does NOT add any new pip dependency.
* Does NOT change `ControlPlaneDB` schema v3 — no `ALTER TABLE`, no new column. The new module REUSES the existing `runtime_gap_signals` table.
* Does NOT introduce a new dispatcher, executor, router, or promotion engine.
