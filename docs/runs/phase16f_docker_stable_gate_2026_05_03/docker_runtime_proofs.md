# Phase 16F — Docker runtime proofs (`--network none`)

**Date:** 2026-05-04
**Image:** `waggledance:phase16f` (ID `7bbac5ee5c72`, 3.09 GB)
**Container runtime:** Docker Desktop 4.71.0, Engine 29.4.1, runc 1.3.5
**Network:** explicitly `--network none` for every proof and smoke run — no DNS, no outbound, no API keys reachable.

## Result: PASS — corpus 104 zero-provider end-to-end inside Docker

All three canonical proofs and all four targeted smoke tests pass inside the Docker container with the network completely disabled.

## Proof 1/3 — Phase 16B P2 full-corpus restart continuity

```bash
docker run --rm --network none waggledance:phase16f \
    python tools/run_full_restart_continuity_proof.py \
    --out-dir /tmp/p16f --db /tmp/p16f/full_restart.db
```

| field | value | required | met |
|---|---|---|---|
| `selected_upstream_caller` | `waggledance.application.services.autonomy_service.AutonomyService.handle_query` | service-layer caller | ✅ |
| `corpus_total` | 104 | ≥ 100 | ✅ |
| `manual_structured_in_input` | False | False | ✅ |
| `manual_hint_in_input` | False | False | ✅ |
| pass1 served / miss | 0 / 104 | 0 / N | ✅ |
| harvest intents / promoted / rejected / errored | 104 / 104 / 0 / 0 | N / N / 0 / 0 | ✅ |
| pre-restart pass2 served / via-capability-lookup / miss | 104 / 104 / 0 | N / N / 0 | ✅ |
| persisted solver_count before/after reopen | 104 / 104 | identical | ✅ |
| persisted capability_features before/after reopen | 180 / 180 | identical | ✅ |
| post-restart pass2 served / via-capability-lookup / miss | 104 / 104 / 0 | N / N / 0 | ✅ |
| served_unchanged_across_restart | True | True | ✅ |
| served_via_capability_unchanged_across_restart | True | True | ✅ |
| solver_count_unchanged_across_reopen | True | True | ✅ |
| capability_features_unchanged_across_reopen | True | True | ✅ |
| provider_jobs_delta_across_restart | 0 | 0 | ✅ |
| builder_jobs_delta_across_restart | 0 | 0 | ✅ |
| cache_rebuild_success | True | True | ✅ |
| provider_jobs_delta_during_proof | 0 | 0 | ✅ |
| builder_jobs_delta_during_proof | 0 | 0 | ✅ |

**Verdict:** g07 (Full-corpus restart proof) PASS in Docker. Carries forward Phase 16D Phase 16B P2 baseline.

## Proof 2/3 — Phase 16A upstream structured_request

```bash
docker run --rm --network none waggledance:phase16f \
    python tools/run_upstream_structured_request_proof.py \
    --out-dir /tmp/p16f --db /tmp/p16f/upstream.db
```

| field | value | required | met |
|---|---|---|---|
| `selected_upstream_caller` | `AutonomyService.handle_query` | service-layer caller | ✅ |
| `corpus_total` | 104 | ≥ 100 | ✅ |
| `manual_structured_in_input` | False | False | ✅ |
| `manual_low_risk_hint_in_input` | False | False | ✅ |
| `proof_built_runtime_q` | False | False | ✅ |
| `proof_bypassed_caller` | False | False | ✅ |
| `proof_bypassed_handle_query` | False | False | ✅ |
| `structured_request_derived_total` | 104 | ≥ 100 | ✅ |
| `low_risk_hint_derived_total` | 104 | ≥ 100 | ✅ |
| `rejected_total` | 5 | (negative corpus) | ✅ |
| `skipped_total` | 2 | (negative corpus) | ✅ |
| pass1 served / miss / buffered_flushed | 0 / 104 / 25 | 0 / N / N | ✅ |
| harvest intents_created / scheduler_drained / promoted / rejected / errored | 104 / 104 / 104 / 0 / 0 | N / N / N / 0 / 0 | ✅ |
| pass2 cold served / via-capability / miss | 104 / 104 / 0 | N / N / 0 | ✅ |
| `negative_cases_passed` | 7 / 7 | 7 / 7 | ✅ |
| `auto_promotions_total` | 104 | ≥ 100 | ✅ |
| `growth_events_total` | 416 | > 0 | ✅ |
| `provider_jobs_delta_during_proof` | 0 | 0 | ✅ |
| `builder_jobs_delta_during_proof` | 0 | 0 | ✅ |

Latency (informational, not a stable gate):
* pass1 service.handle_query p50 / p99 = 19.87 ms / 540.80 ms
* pass2 cold p50 / p99 = 16.04 ms / 29.88 ms
* pass3 warm p50 / p99 = 10.63 ms / 21.60 ms
* upstream extractor only p50 / p99 = 0.006 ms / 0.031 ms

Hot-path: warm_hits=318, cold_hits_warmed=98, misses=104.

**Verdict:** g06 (provider/builder delta = 0) + g04 (100+ solver gate) PASS in Docker. Carries forward Phase 16D Phase 16A baseline.

## Proof 3/3 — Phase 15 automatic runtime hint

```bash
docker run --rm --network none waggledance:phase16f \
    python tools/run_automatic_runtime_hint_proof.py \
    --out-dir /tmp/p16f --db /tmp/p16f/hint.db
```

| field | value | required | met |
|---|---|---|---|
| `selected_caller` | `waggledance.core.autonomy.runtime.AutonomyRuntime.handle_query` | runtime caller | ✅ |
| `corpus_total` | 104 | ≥ 100 | ✅ |
| `hints_derived_total` | 104 | ≥ 100 | ✅ |
| `manual_hint_in_input` | False | False | ✅ |
| `proof_built_runtime_q` | False | False | ✅ |
| pass1 served / miss / buffered_flushed | 0 / 104 / 0 | 0 / N / N | ✅ |
| harvest intents / scheduler_drained / promoted / rejected / errored | 104 / 104 / 104 / 0 / 0 | N / N / N / 0 / 0 | ✅ |
| pass2 cold served / via-capability / miss | 104 / 104 / 0 | N / N / 0 | ✅ |
| `negative_cases_passed` | 5 / 5 | 5 / 5 | ✅ |
| `auto_promotions_total` | 104 | ≥ 100 | ✅ |
| `growth_events_total` | 416 | > 0 | ✅ |
| `provider_jobs_delta_during_proof` | 0 | 0 | ✅ |
| `builder_jobs_delta_during_proof` | 0 | 0 | ✅ |

Latency: pass1 p50/p99 = 20.37/508.38 ms; pass2 cold p50/p99 = 17.02/28.38 ms; pass3 warm p50/p99 = 10.92/25.93 ms; hint extractor only p50/p99 = 0.015/0.067 ms.

**Verdict:** Phase 15 contract intact in Docker. Hot-path latency consistent with Phase 16D dev-shell baseline.

## Smoke tests (4 files, container --network none)

```bash
docker run --rm --network none waggledance:phase16f python -m pytest \
    tests/autonomy_growth/test_full_restart_continuity_smoke.py \
    tests/autonomy_growth/test_upstream_structured_request_proof_smoke.py \
    tests/autonomy_growth/test_automatic_runtime_hint_proof_smoke.py \
    tests/autonomy_growth/test_seed_library.py -q
```

Result: **16 passed, 27 skipped in 124.87s** (no failures, no errors).

The 27 skips are conditional skips inside the seed library tests (per-family expansion tests that are gated on test fixtures available only when the seed library is mutated; they pass trivially when the library is read-only at the v3.8.0 baseline). Zero unconditional skips on any of the three smoke files.

## Voikko fallback in container (informational)

Each proof emits two stderr lines:
```
Voikko-lataus epäonnistui (/usr/lib/voikko): No module named 'libvoikko'
Voikko ei saatavilla — käytetään suomi-suffix-stripperiä
```

This is the documented Finnish-morphology graceful-degradation path: `libvoikko1` and `voikko-fi` are installed at the OS level (apt) but the Python `libvoikko` binding is in `requirements.lock.txt` (not `requirements-ci.txt`) and therefore not in this image. The runtime falls back to the suffix-stripper. None of the autonomy proof outcomes depend on Voikko; the warning is harmless. If a future image needs full Voikko, restore the lock-file install path.

## Stable gate ledger updates

* **g01 Docker end-to-end**: PASS (was FAIL_NOT_VERIFIED in 16D)
* **g04 100+ solver release gate**: re-asserted PASS (corpus 104 across all 3 proofs)
* **g06 Provider/builder delta = 0**: re-asserted PASS (0/0 in all 3 proofs and across restart)
* **g07 Full-corpus restart proof**: re-asserted PASS in Docker
* **g19 Docker runtime no-network proof**: PASS (was FAIL_NOT_VERIFIED in 16D)

## What this proves

The autonomy inner loop genuinely runs offline — no provider in the call path (RULE 7), no API key reachable (`--network none`), no internet at runtime — and produces deterministic, reproducible structure: 104 seeds → 104 promotions → 104 served via capability lookup, with persisted state surviving a full DB close+reopen, all with 0/0 provider and builder deltas.

That is the v3.8.0 stable contract. Phase 16F closes it inside Docker for the first time.
