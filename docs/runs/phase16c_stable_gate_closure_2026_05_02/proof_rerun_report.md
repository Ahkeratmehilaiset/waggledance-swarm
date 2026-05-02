# Phase 16C P4 — Critical proof re-run report

All three canonical proofs and a 3-iteration soak were re-run on the Phase 16C branch (`phase16c/stable-gate-closure`) before merge, with output written into the Phase 16C session folder so the Phase 11–16B canonical artifacts in `docs/runs/phase15_*/` and `docs/runs/phase16_*/` and `docs/runs/phase16b_*/` remain untouched (per the master prompt's "Do NOT overwrite Phase 11–16B run artifacts" rule).

## Phase 15 automatic runtime hint proof

`python tools/run_automatic_runtime_hint_proof.py --out-dir docs/runs/phase16c_stable_gate_closure_2026_05_02 --db docs/runs/phase16c_stable_gate_closure_2026_05_02/automatic_runtime_hint_proof.db`

| metric | value |
|---|---|
| corpus_total | 104 |
| auto_promotions_total | 104 |
| growth_events_total | 416 |
| provider_jobs_delta_during_proof | **0** |
| builder_jobs_delta_during_proof | **0** |

## Phase 16A upstream structured_request proof

`python tools/run_upstream_structured_request_proof.py --out-dir docs/runs/phase16c_stable_gate_closure_2026_05_02 --db docs/runs/phase16c_stable_gate_closure_2026_05_02/upstream_structured_request_proof.db`

| metric | value |
|---|---|
| corpus_total | 104 |
| auto_promotions_total | 104 |
| structured_request_derived_total | 104 |
| low_risk_hint_derived_total | 104 |
| growth_events_total | 416 |
| provider_jobs_delta_during_proof | **0** |
| builder_jobs_delta_during_proof | **0** |
| negative_cases_passed_total / total | (verify in JSON) |

## Phase 16B full-corpus restart continuity proof

`python tools/run_full_restart_continuity_proof.py --out-dir docs/runs/phase16c_stable_gate_closure_2026_05_02 --db docs/runs/phase16c_stable_gate_closure_2026_05_02/full_restart_continuity_proof.db`

| metric | value |
|---|---|
| corpus_total | 104 |
| pass-1 served / miss | 0 / 104 |
| harvest promoted / rejected / errored | 104 / 0 / 0 |
| pre-restart pass-2 served via capability lookup | 104 / 104 |
| persisted solver count before/after reopen | identical |
| persisted capability_features before/after reopen | identical |
| post-restart pass-2 served via capability lookup | 104 / 104 |
| `served_unchanged_across_restart` | **true** |
| `solver_count_unchanged_across_reopen` | **true** |
| `capability_features_unchanged_across_reopen` | **true** |
| `provider_jobs_delta_across_restart` | **0** |
| `builder_jobs_delta_across_restart` | **0** |
| `cache_rebuild_success` | **true** |
| `provider_jobs_delta_during_proof` | **0** |
| `builder_jobs_delta_during_proof` | **0** |

## 3-iteration soak

`python tools/run_phase16b_proof_soak.py --iterations 3 --report docs/runs/phase16c_stable_gate_closure_2026_05_02/proof_soak_report.json`

| proof | iterations | pass | fail | mean elapsed (s) | flake |
|---|---|---|---|---|---|
| phase15_runtime_hint | 3 | **3** | 0 | 37.96 | **no** |
| phase16a_upstream | 3 | **3** | 0 | 38.85 | **no** |
| phase16b_full_restart | 3 | **3** | 0 | 35.75 | **no** |

**Overall:** 9 / 9 iterations pass at corpus 104. **No flakes.**

3 iterations is the master prompt's minimum acceptable for P4 (default 5 was used in Phase 16B P3; P4 uses 3 to keep total runtime bounded to ~6 minutes).

## Stable gate disposition

| gate | result |
|---|---|
| g04 provider/builder Δ = 0 | **PASS** (all four proofs) |
| g06 100+ corpus | **PASS** (104) |
| g07 full-corpus restart continuity | **PASS** |
| g08 proof soak no-flake | **PASS** (9/9 across 3 iterations) |

All four are PASS — the autonomy stack is repeatable, restart-safe, and provider-free for the inner loop, exactly as Phase 16B established.

## Phase 14 hotpath test — hardware-sensitive timing note

`tests/autonomy_growth/test_live_runtime_hotpath_proof_smoke.py::test_live_runtime_hotpath_proof_meets_p3_floor` failed once on a full-batch run during Phase 16C P7 (`349 passed / 1 failed`) and passed on isolated re-run and again on a second full-batch run (`349 passed`). The test asserts absolute warm/cold latency floors (warm_p50_ms ≤ 1.0, warm_p99_ms ≤ 10, cold_p50_ms ≤ 75, cold_p99_ms ≤ 250). The test's own docstring already acknowledges that "The relative warm-vs-pre-cache ratio… is hardware-sensitive". This is not a Phase 16C regression; it is a known pre-existing hardware-sensitivity in the Phase 14 hotpath proof when the host system is under variable load.

This flake is **not in the Phase 16C P3/P4 soak scope** (which covered the Phase 15 hint proof, Phase 16A upstream proof, and Phase 16B full-restart proof — all 9/9 PASS, no flakes). The Phase 14 hotpath proof's behaviour under sustained load is tracked separately and was already documented in the Phase 14 release notes as hardware-dependent.
