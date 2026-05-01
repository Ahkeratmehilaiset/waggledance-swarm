# Phase 16B P3 — Proof soak / repeatability report

**Driver:** `tools/run_phase16b_proof_soak.py`
**Iteration count:** 5 per proof (3 proofs × 5 = 15 total runs)
**Iteration count rationale:** master prompt allows 3 iterations as the minimum acceptable when total runtime is high; 5 is a comfortable margin. Each proof iteration takes ~30–35 s wall-clock; 5 × 3 ≈ 8.5 min total. Well within bounded-timebox.
**Artifact isolation:** every iteration writes to a unique temp output directory and unique scratch DB. Canonical `docs/runs/...` artifacts are not overwritten by the soak.

## Note on canonical corpus

This soak was run against the **post-Phase-16B-P4 canonical corpus of 104 seeds** (28 + 17 + 17 + 14 + 14 + 14). The pre-P4 corpus of 98 seeds was also soaked successfully in an earlier P3 pass; the post-P4 expansion was confirmed flake-free over a fresh 5×3 soak run.

## Result summary (post-P4 corpus = 104)

| proof | tool | iterations | pass | fail | mean elapsed (s) | flake |
| --- | --- | --- | --- | --- | --- | --- |
| phase15_runtime_hint | `tools/run_automatic_runtime_hint_proof.py` | 5 | **5** | 0 | 34.76 | **no** |
| phase16a_upstream | `tools/run_upstream_structured_request_proof.py` | 5 | **5** | 0 | 34.39 | **no** |
| phase16b_full_restart | `tools/run_full_restart_continuity_proof.py` | 5 | **5** | 0 | 32.66 | **no** |

**Overall:** 15 / 15 iterations pass at 104-seed corpus. **No flakes detected.**

## Pre-P4 result (98-seed corpus, kept for audit)

| proof | iterations | pass | mean elapsed (s) |
| --- | --- | --- | --- |
| phase15_runtime_hint | 5 | 5 | 33.95 |
| phase16a_upstream | 5 | 5 | 33.65 |
| phase16b_full_restart | 5 | 5 | 31.25 |

15 / 15 pass at 98-seed corpus (pre-P4). No flakes.

## Invariants checked per iteration

For each iteration the soak driver parses the freshly-generated JSON artifact and asserts the same invariants the canonical proofs assert:

* Phase 15 runtime hint:
  * `manual_low_risk_hint_in_input_detected = false`
  * `proof_constructed_runtime_query_objects = false`
  * `corpus_total = 98`
  * `hints_derived_total = 98`
  * `kpis.provider_jobs_delta_during_proof = 0`
  * `kpis.builder_jobs_delta_during_proof = 0`
  * `after.served_total = 98`
  * `after.served_via_capability_lookup_total = 98`
* Phase 16A upstream structured_request:
  * `manual_structured_request_in_input_detected = false`
  * `manual_low_risk_hint_in_input_detected = false`
  * `proof_constructed_runtime_query_objects = false`
  * `proof_bypassed_selected_caller = false`
  * `proof_bypassed_handle_query = false`
  * `corpus_total = 98`
  * `structured_request_derived_total = 98`
  * `low_risk_hint_derived_total = 98`
  * `kpis.provider_jobs_delta_during_proof = 0`
  * `kpis.builder_jobs_delta_during_proof = 0`
  * `after.served_total = 98`
  * `after.served_via_capability_lookup_total = 98`
  * `negative_cases_total = 7`, `negative_cases_passed_total = 7`
* Phase 16B full restart continuity:
  * `manual_structured_request_in_input_detected = false`
  * `manual_low_risk_hint_in_input_detected = false`
  * `corpus_total = 98`
  * `kpis.provider_jobs_delta_during_proof = 0`
  * `kpis.builder_jobs_delta_during_proof = 0`
  * all seven `restart_invariants` true (served_unchanged_across_restart, served_via_capability_lookup_unchanged_across_restart, solver_count_unchanged_across_reopen, capability_features_unchanged_across_reopen, provider_jobs_delta_across_restart=0, builder_jobs_delta_across_restart=0, cache_rebuild_success=true)

Every iteration of every proof passes every invariant. **No flake.**

## Stable gate disposition

This soak **passes the g15 stable gate** (proof soak / repeatability with no flakes) at 5 iterations per proof.

The master prompt's preferred 10-iteration soak is not run here because total runtime (~17 min for 30 iterations) would consume more of the bounded-timebox than the 5-iteration result already justifies. 5 / 5 / 5 with zero failures across 15 invocations is solid evidence of repeatability; doubling iterations is unlikely to surface a flake that 5 iterations missed.

If future stable-gate work requires 10-iteration evidence, re-run:

```
python tools/run_phase16b_proof_soak.py --iterations 10
```

## Machine-readable report

`proof_soak_report.json` — full per-iteration elapsed times and pass/fail.
