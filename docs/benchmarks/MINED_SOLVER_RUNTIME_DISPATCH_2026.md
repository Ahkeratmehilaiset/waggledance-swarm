# Mined Solver Runtime Dispatch — 2026-Q2

**Status:** Phase 18C snapshot, derived from this session's reproducible artifact only.
**Date:** 2026-05-05
**Branch:** `phase18c/mined-solver-runtime-dispatch`
**Anchor:** `v3.10.2-mined-solver-dispatch-alpha` candidate (PRERELEASE only). v3.8.0 remains GitHub Latest.

This document publishes the runtime dispatch integration for Phase 18B mined low-risk solver specs. It is an **engineering** record. It does not assert WaggleDance is faster, smarter, or otherwise superior to any external system. It records that mined six-family allowlisted specs are now registered into the real `ControlPlaneDB` and served through the real `LowRiskSolverDispatcher` capability lookup path.

## Reproduce

```
git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git
cd waggledance-swarm
git checkout v3.10.2-mined-solver-dispatch-alpha   # or stay on main
pip install -r requirements-ci.txt                  # nothing extra; runtime dispatch is stdlib + WaggleDance only
python tools/run_phase18c_mined_solver_runtime_dispatch_proof.py
python tools/run_phase18b_gap_miner_feedback_proof.py --out-dir /tmp/p18b_carry
python tools/validate_phase18a_benchmark_bundle.py \
    --bundle-dir docs/runs/phase18a_benchmark_externalization_2026_05_05/export_bundle
```

All three exit `0`.

## What the loop now does end-to-end (Phase 18A → 18B → 18C)

```
WaggleDance runtime emits structured gap signals
  → mine_runtime_gaps()  [Phase 18B]
    → 14 candidates with six-element verdict enum:
        ALLOWLISTED_SOLVER_SPEC × 6
        INSUFFICIENT_EVIDENCE   × 3
        OUT_OF_FAMILY_REJECTED  × 2
        HIGH_RISK_REJECTED      × 1
        BUILDER_HANDOFF_QUARANTINED × 1
        DUPLICATE_SUPPRESSED    × 1
  → register_mined_solver_specs()  [Phase 18C, NEW]
    → for each ALLOWLISTED candidate:
        compile_mined_spec_to_runtime_artifact() emits an executor-shaped artifact
        ControlPlaneDB.upsert_solver_family(name=family_kind, status='active')
        ControlPlaneDB.upsert_solver(status='auto_promoted', spec_hash=...)
        ControlPlaneDB.set_solver_capability_features(solver_id, family_kind, features)
        ControlPlaneDB.upsert_solver_artifact(solver_id, family_kind, artifact_id, ...)
    → non-ALLOWLISTED verdicts rejected; never executable
  → LowRiskSolverDispatcher.dispatch_by_features()  [pre-existing]
    → capability lookup hits the registered mined solver
    → execute_artifact() runs the per-family pure executor
    → returns the deterministic output
```

The dispatch path is the **same** code path the runtime router uses for all auto-promoted low-risk solvers. Phase 18C does not introduce a new dispatcher, executor, or promotion engine — it bridges Phase 18B's mining output into the existing four-step Phase 17A registration pattern.

## What's allowlisted, what's quarantined

| Verdict | Phase 18C action | Becomes executable? |
| --- | --- | --- |
| `ALLOWLISTED_SOLVER_SPEC` | registered as `auto_promoted` solver + capability features + executable artifact | yes |
| `INSUFFICIENT_EVIDENCE` | rejected from registration | no |
| `OUT_OF_FAMILY_REJECTED` | rejected from registration | no |
| `HIGH_RISK_REJECTED` | rejected from registration | no |
| `BUILDER_HANDOFF_QUARANTINED` | quarantined (Phase 18B contract preserved); zero solver rows created | no |
| `DUPLICATE_SUPPRESSED` | rejected from registration | no |

The compilation step is fail-closed: if a mined `(family_kind, feature_dict)` signature is not in the documented compilation table, `RuntimeArtifactCompilationError` is raised and the candidate is rejected. No runtime registration happens for unrecognized shapes.

## Six-family allowlist (unchanged)

```
scalar_unit_conversion
lookup_table
threshold_rule
interval_bucket_classifier
linear_arithmetic
bounded_interpolation
```

Phase 18C does not widen the allowlist. The compilation table covers exactly the six Phase 18B fixture shapes and one mined spec per family.

## Per-family dispatch coverage (this session)

| family | mined feature signature | dispatch cases tested | all hit? |
| --- | --- | ---: | --- |
| scalar_unit_conversion | km → miles, factor 0.621371 | 3 (x=10, x=0, x=100) | yes |
| lookup_table | chemical_symbols (Sn, Au, Na, Fe) | 3 (tin, gold, iron) | yes |
| threshold_rule | `> 30 → above / below` | 3 (37, 12, 30) | yes |
| interval_bucket_classifier | `[0,10),[10,20),[20,30)` | 3 (5, 17, 22) | yes |
| linear_arithmetic | `add: a + b` | 3 (14+9, 0+0, 5+7) | yes |
| bounded_interpolation | `(0,0)→(10,100)` linear | 3 (x=3, x=0, x=10) | yes |

Total: **18 / 18 dispatch cases succeeded.** Every case dispatched through `LowRiskSolverDispatcher.dispatch_by_features` and matched on the capability path (`reason="hit_by_features"`).

## Honest scope

What you can take from this document:

* The Phase 18B → Phase 18C path closes the explicit gap recorded in Phase 18B's proof JSON: `capability_lookup_status` is no longer `NOT_RUN_OUT_OF_PHASE18B_SCOPE`. Mined ALLOWLISTED specs are runtime-served.
* All six low-risk allowlist families have at least one registered mined solver and at least three deterministic dispatch test cases that hit through the real router/capability path.
* The full chain (mine → compile → register → dispatch → execute) is reproducible offline inside `--network none`.

What you cannot take from this document:

* This is **not** wiring Phase 18B's mining into the live runtime router that serves real user queries. The proof harness drives a synthetic deterministic fixture; integrating with `RuntimeQueryRouter.route()` for real user queries is a separate downstream job (the dispatch internals are exercised, but the upstream runtime-query feed is fixture-driven).
* This is **not** an autonomous high-risk family. Builder handoff is quarantined; high-risk candidates are rejected.
* This is **not** a raw-intelligence claim. The mined-solver compilation table is a small fixed lookup; no model is consulted.

## Numbers (this session)

| metric | value |
| --- | ---: |
| signals_total | 30 |
| candidates_total | 14 |
| allowlisted_candidate_count | 6 |
| registered_solver_count | 6 |
| rejected_registration_count | 8 |
| dispatch_case_count | 18 |
| dispatch_success_count | 18 |
| dispatch_failure_count | 0 |
| families_covered | 6 |
| provider_jobs_delta | 0 |
| builder_jobs_delta | 0 |
| `release_gate_pass` | true |
| `forbidden_claims_absent` | true |

The canonical artifact is `docs/runs/phase18c_mined_solver_runtime_dispatch_2026_05_05/mined_solver_runtime_dispatch_proof.{json,md}`.

## Honesty contracts

* `no_model_pull_or_download = true`
* `no_cloud_api_calls = true`
* `no_live_builder_execution = true`
* `no_high_risk_autonomy = true`
* `no_raw_intelligence_superiority_claim = true`
* `no_cross_vendor_ranking_claim = true`
* `allowlist_unchanged = true`
* `provider_jobs_delta = builder_jobs_delta = 0`
* `no_stage2_flip = true`, `no_human_approval = true`

## Position in the 2026-Q2 release line

| Tag | What it adds | Status |
|---|---|---|
| `v3.8.0` | stable release | **Latest** |
| `v3.9.0-producer-fabric-alpha` | Phase 17A producer fabric + 10k scale | Pre-release |
| `v3.9.1-local-efficiency-benchmark-alpha` | Phase 17B local efficiency benchmark harness | Pre-release |
| `v3.9.2-local-ollama-baseline-alpha` | Phase 17C local Ollama baseline (one model) | Pre-release |
| `v3.9.3-local-model-sweep-alpha` | Phase 17D 4-model panel + repeatability | Pre-release |
| `v3.10.0-benchmark-schema-alpha` | Phase 18A bundle export + schema validation | Pre-release |
| `v3.10.1-gap-miner-feedback-alpha` | Phase 18B runtime gap miner + solver feedback loop | Pre-release |
| `v3.10.2-mined-solver-dispatch-alpha` | Phase 18C runtime dispatch of mined solver specs (this PR's candidate) | Pre-release (candidate) |

Phase 18C does not modify any earlier tag. v3.8.0 remains GitHub Latest.
