# Phase 18C — Release Decision

**Decision:** **A — release `v3.10.2-mined-solver-dispatch-alpha` PRERELEASE.**
**Date (UTC):** 2026-05-05
**Branch:** `phase18c/mined-solver-runtime-dispatch`
**Base SHA:** `0c5335f964fbab2a5f6c0cb74b7033ec8353b998` (Phase 18B post-release docs PR #82 merge)

## Gate evaluation

All Phase 18C release gates green:

| Gate | Result |
| --- | --- |
| P0 baseline + 7 prior tags unchanged | PASS |
| Phase 18A bundle still validates (carry-forward) | PASS |
| Phase 18B proof still passes (carry-forward) | PASS |
| Design doc (`runtime_dispatch_design.md`) written before code | PASS — uses real `RuntimeQueryRouter`/`LowRiskSolverDispatcher` path identified in inventory |
| Mainline module implemented | `waggledance/core/autonomy_growth/mined_solver_runtime.py` (~310 LOC, stdlib + WaggleDance only) |
| Proof harness implemented | `tools/run_phase18c_mined_solver_runtime_dispatch_proof.py` |
| Real `RuntimeQueryRouter`/`LowRiskSolverDispatcher` path used | yes; `LowRiskSolverDispatcher.dispatch_by_features` returns `reason="hit_by_features"` |
| ≥ 6 mined ALLOWLISTED specs register | 6 / 6 |
| Six-family allowlist covered | 6 / 6 (`scalar_unit_conversion`, `lookup_table`, `threshold_rule`, `interval_bucket_classifier`, `linear_arithmetic`, `bounded_interpolation`) |
| ≥ 18 dispatch cases | 18 |
| All dispatch cases hit | 18 / 18 |
| Non-allowlisted verdicts do not register | 8 rejected (3 INSUFFICIENT_EVIDENCE + 2 OUT_OF_FAMILY + 1 HIGH_RISK + 1 BUILDER_HANDOFF + 1 DUPLICATE) |
| Builder handoff remains quarantined | yes; 0 solver rows for `phase18c_builder_handoff_*` |
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
| Phase 18C tests | 33 / 33 PASS in 5.41 s |
| Carry-forward targeted suite (Phase 18C 33 + 18B 19 + 18A 15 + phase10 14 + storage 50 + ui_hologram 22 + solver_router 50) | 203 / 203 PASS in 16.37 s |
| Docker `--network none` Phase 18C proof | PASS |
| Docker `--network none` Phase 18B carry-forward | PASS |
| Docker `--network none` Phase 18A bundle validation | PASS |
| `git rev-parse v3.8.0^{}` | `824176eb...` (unchanged) |
| `git rev-parse v3.9.0-producer-fabric-alpha^{}` | `c726995c...` (unchanged) |
| `git rev-parse v3.9.1-local-efficiency-benchmark-alpha^{}` | `f4d0a4a4...` (unchanged) |
| `git rev-parse v3.9.2-local-ollama-baseline-alpha^{}` | `db5d7db1...` (unchanged) |
| `git rev-parse v3.9.3-local-model-sweep-alpha^{}` | `d0704efe...` (unchanged) |
| `git rev-parse v3.10.0-benchmark-schema-alpha^{}` | `4554b24a...` (unchanged) |
| `git rev-parse v3.10.1-gap-miner-feedback-alpha^{}` | `b408b14a...` (unchanged) |
| `gh release list` v3.8.0 status | **Latest** |
| No DB/SQLite/WAL/SHM files in committed tree | confirmed |
| No GitHub token / secret printed or committed | confirmed |
| No new pip dependency | confirmed |

## Capability lookup status

`capability_lookup_status` previously recorded as `NOT_RUN_OUT_OF_PHASE18B_SCOPE` in Phase 18B is now **closed**. Phase 18C wires Phase 18B mined ALLOWLISTED specs through the real runtime path:

* `register_mined_solver_specs` writes `solvers.status='auto_promoted'`, `solver_capability_features` rows, and `solver_artifacts` rows into the same `ControlPlaneDB` used by the live runtime.
* `LowRiskSolverDispatcher.dispatch_by_features` performs the SQL-backed capability superset lookup and returns the registered mined solver in every test case (`reason="hit_by_features"`, `output == expected`).

This is the same code path the Phase 17A 10k-scale proof exercises and the same path live runtime queries use. No fake standalone dispatcher was introduced.

## Tag plan

* Tag name: `v3.10.2-mined-solver-dispatch-alpha`.
* `isPrerelease = true`. **NOT** `Latest`.
* Target: the squash-merge commit of the Phase 18C PR.
* GitHub release: `gh release create v3.10.2-mined-solver-dispatch-alpha --prerelease --target <merge SHA> --notes-file <release_notes>`.
* `v3.8.0` remains GitHub Latest. v3.9.0 + v3.9.1 + v3.9.2 + v3.9.3 + v3.10.0 + v3.10.1 + v3.10.2 alphas all Pre-release.

## What this release does NOT do

* Does NOT modify any of the 7 prior tags.
* Does NOT introduce a stable-tagged release.
* Does NOT widen the six-family allowlist.
* Does NOT add a new high-risk autonomy mechanism.
* Does NOT execute Stage-2 atomic flip; does NOT collect HUMAN_APPROVAL.
* Does NOT pull or download any Ollama model; does NOT call any cloud LLM API; does NOT execute any live builder lane.
* Does NOT make any cross-vendor ranking claim or raw-intelligence superiority claim.
* Does NOT add any new pip dependency.
* Does NOT introduce a new dispatcher, executor, router, or promotion engine — Phase 18C reuses the existing Phase 11–17A runtime path verbatim.
