# Phase 16D P4 — Critical proof re-run (post-B324 cleanup)

All four canonical proofs re-run on the Phase 16D branch after the B324 cleanup, with output written into the Phase 16D session folder so Phase 11–16C canonical artifacts in `docs/runs/phase15_*/`, `docs/runs/phase16_*/`, `docs/runs/phase16b_*/`, and `docs/runs/phase16c_*/` remain untouched.

## Phase 15 automatic runtime hint proof

`python tools/run_automatic_runtime_hint_proof.py --out-dir docs/runs/phase16d_final_stable_gate_closure_2026_05_02 --db docs/runs/phase16d_final_stable_gate_closure_2026_05_02/automatic_runtime_hint_proof.db`

| metric | value |
|---|---|
| corpus_total | 104 |
| auto_promotions_total | 104 |
| growth_events_total | 416 |
| provider_jobs_delta_during_proof | **0** |
| builder_jobs_delta_during_proof | **0** |

## Phase 16A upstream structured_request proof

`python tools/run_upstream_structured_request_proof.py --out-dir docs/runs/phase16d_final_stable_gate_closure_2026_05_02 --db docs/runs/phase16d_final_stable_gate_closure_2026_05_02/upstream_structured_request_proof.db`

| metric | value |
|---|---|
| corpus_total | 104 |
| auto_promotions_total | 104 |
| structured_request_derived_total | 104 |
| low_risk_hint_derived_total | 104 |
| growth_events_total | 416 |
| provider_jobs_delta_during_proof | **0** |
| builder_jobs_delta_during_proof | **0** |

## Phase 16B full-corpus restart continuity proof

`python tools/run_full_restart_continuity_proof.py --out-dir docs/runs/phase16d_final_stable_gate_closure_2026_05_02 --db docs/runs/phase16d_final_stable_gate_closure_2026_05_02/full_restart_continuity_proof.db`

| metric | value |
|---|---|
| corpus_total | 104 |
| pass-1 served / miss | 0 / 104 |
| harvest promoted / rejected / errored | 104 / 0 / 0 |
| pre-restart pass-2 served via capability lookup | 104 / 104 |
| persisted solver count before/after reopen | 104 / 104 (identical) |
| persisted capability_features before/after reopen | 180 / 180 (identical) |
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

`python tools/run_phase16b_proof_soak.py --iterations 3 --report docs/runs/phase16d_final_stable_gate_closure_2026_05_02/proof_soak_report.json`

| proof | iterations | pass | fail | mean elapsed (s) | flake |
|---|---|---|---|---|---|
| phase15_runtime_hint | 3 | **3** | 0 | 36.99 | **no** |
| phase16a_upstream | 3 | **3** | 0 | 36.77 | **no** |
| phase16b_full_restart | 3 | **3** | 0 | 32.85 | **no** |

**Overall:** 9 / 9 iterations pass at corpus 104. **No flakes.**

## Stable gate disposition

| gate | result |
|---|---|
| g04 100+ corpus | **PASS** (104) |
| g06 provider/builder Δ = 0 | **PASS** (all four proofs) |
| g07 full-corpus restart continuity | **PASS** (104/104, all invariants true, 0/0 across restart) |
| g08 proof soak no-flake | **PASS** (9/9 at corpus 104) |

All four are PASS. The B324 cleanup did not regress any autonomy stack metric.
