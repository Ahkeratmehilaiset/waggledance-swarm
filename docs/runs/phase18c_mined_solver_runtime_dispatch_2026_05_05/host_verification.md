# Phase 18C — Host Verification

**Date (UTC):** 2026-05-05
**Branch:** `phase18c/mined-solver-runtime-dispatch`
**Worktree:** `C:/Python/project2-phase18c-mined-solver-dispatch`

## Commands

```
python tools/run_phase18c_mined_solver_runtime_dispatch_proof.py \
    --out-dir docs/runs/phase18c_mined_solver_runtime_dispatch_2026_05_05

python tools/run_phase18b_gap_miner_feedback_proof.py \
    --out-dir /tmp/p18b_carry_p18c_v2

python tools/validate_phase18a_benchmark_bundle.py \
    --bundle-dir docs/runs/phase18a_benchmark_externalization_2026_05_05/export_bundle
```

All three exit 0.

## Phase 18C results

```
signals_total                  : 30
candidates_total               : 14
allowlisted_candidate_count    : 6
registered_solver_count        : 6
rejected_registration_count    : 8
dispatch_case_count            : 18
dispatch_success_count         : 18
dispatch_failure_count         : 0
families_covered               : 6
provider/builder delta         : 0/0
forbidden_claims_absent        : True
release_gate_pass              : True
```

The committed canonical proof artifact lives at `docs/runs/phase18c_mined_solver_runtime_dispatch_2026_05_05/mined_solver_runtime_dispatch_proof.{json,md}`.

### Per-family dispatch counts

* `bounded_interpolation`: 3 cases
* `interval_bucket_classifier`: 3 cases
* `linear_arithmetic`: 3 cases
* `lookup_table`: 3 cases
* `scalar_unit_conversion`: 3 cases
* `threshold_rule`: 3 cases

Total: 18/18 succeed. All cases dispatched through `LowRiskSolverDispatcher.dispatch_by_features` (the same code path live runtime uses).

### Verdict accounting

| Phase 18B verdict | count | Phase 18C action |
| --- | ---: | --- |
| `ALLOWLISTED_SOLVER_SPEC` | 6 | **registered** as auto_promoted solvers in ControlPlaneDB |
| `INSUFFICIENT_EVIDENCE` | 3 | rejected from registration |
| `OUT_OF_FAMILY_REJECTED` | 2 | rejected from registration |
| `HIGH_RISK_REJECTED` | 1 | rejected from registration |
| `BUILDER_HANDOFF_QUARANTINED` | 1 | quarantined; non-executable |
| `DUPLICATE_SUPPRESSED` | 1 | rejected from registration (also Phase 18B already suppressed) |

`registered_solver_count = 6` matches `allowlisted_candidate_count = 6` exactly. `rejected_registration_count = 8` matches the sum of the five non-ALLOWLISTED counts.

## Phase 18B carry-forward (regression gate)

```
provider/builder delta : 0/0
forbidden_claims_absent: True
release_gate_pass      : True
```

## Phase 18A carry-forward (regression gate)

```
Phase 18A bundle validation: PASS  (docs/.../export_bundle)
```

## Targeted suite (this session)

```
tests/autonomy_growth/test_phase18c_mined_solver_runtime_dispatch.py    33 passed
tests/autonomy_growth/test_phase18b_gap_miner_feedback.py               19 passed
tests/benchmarks/test_phase18a_benchmark_externalization.py             15 passed
tests/phase10/                                                           14 passed
tests/storage/                                                           50 passed
tests/ui_hologram/                                                       22 passed
tests/autonomy/test_solver_router.py                                     50 passed
                                                                        ----
                                                                       203 passed in 13.54s
```

## Honesty contracts

* `no_model_pull_or_download = true`
* `no_cloud_api_calls = true`
* `no_live_builder_execution = true`
* `no_high_risk_autonomy = true`
* `no_raw_intelligence_superiority_claim = true`
* `no_cross_vendor_ranking_claim = true`
* `allowlist_unchanged = true`
* `provider_jobs_delta = 0`, `builder_jobs_delta = 0`
* `no_stage2_flip = true`, `no_human_approval = true`
* `forbidden_claims_absent = true`

## Token / secret hygiene

No `GH_TOKEN`, push-URL with embedded token, or other secret material has been printed to stdout, written to disk, or committed during this session.

## What this run proves

* Phase 18B mined low-risk solver specs are registered into the **real** `ControlPlaneDB` via the canonical Phase 17A four-step pattern (`upsert_solver_family` → `upsert_solver(status="auto_promoted")` → `set_solver_capability_features` → `upsert_solver_artifact`).
* Dispatch goes through the **real** `LowRiskSolverDispatcher.dispatch_by_features` — the same code path live runtime uses — and capability lookup hits the registered mined solver in every six allowlist family.
* Non-allowlisted verdicts (insufficient evidence, out-of-family, high-risk, builder-handoff, duplicate) are rejected from registration; they never become executable runtime solvers.
* Builder handoff remains quarantined: zero solver rows for `phase18c_builder_handoff_*` exist after registration.
* All three regression gates remain green: Phase 18A bundle validates, Phase 18B proof passes, Phase 10 truth-regression tests pass.

## What this run does NOT prove

* Does NOT prove arbitrary mined feature_dicts compile to runtime artifacts. The compilation table covers exactly the six Phase 18B fixture shapes; a novel `feature_dict` fails closed via `RuntimeArtifactCompilationError` and requires operator review.
* Does NOT exercise the Phase 9 14-stage promotion ladder. Phase 18C uses direct `auto_promoted` registration, identical to the Phase 17A 10k-scale proof pattern.
* Does NOT make any cross-vendor ranking, raw-intelligence superiority, or consciousness claim.
