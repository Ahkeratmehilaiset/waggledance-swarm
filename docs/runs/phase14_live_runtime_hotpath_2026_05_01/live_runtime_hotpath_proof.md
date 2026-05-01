# Phase 14 — Live Runtime Hot-Path Proof

**Generated:** 2026-05-01 by `tools/run_live_runtime_hotpath_proof.py`
**Machine-readable artifact:** [`live_runtime_hotpath_proof.json`](live_runtime_hotpath_proof.json)
**Branch:** `phase14/live-runtime-hotpath-wiring` (off `origin/main` @ `8f07d8f`)
**Composes with:** Phase 13 runtime-router proof + Phase 12 self-starting + Phase 11 closed loop.

## What this proof shows

The first proof in the WaggleDance autonomy lineage that runs through the **production reasoning entrypoint** `waggledance.core.reasoning.solver_router.SolverRouter.route(intent, query, context)` rather than calling the autonomy router directly. The corpus is the canonical seed library (98 entries spanning all 6 allowlisted families and 8 hex cells); each entry is rendered as a structured `low_risk_autonomy_query` context hint and routed through `SolverRouter.route(...)`. Two `SolverRouter` instances are constructed against the same scratch control plane:

* `sr_no_cache` — `SolverRouter` whose autonomy consult points at a `RuntimeQueryRouter` *without* `HotPathCache`. This is the Phase 13 baseline: every dispatch performs a SQLite SELECT + JOIN, a `json.loads`, and an `execute_artifact`.
* `sr` — `SolverRouter` whose autonomy consult points at a `RuntimeQueryRouter` *with* `HotPathCache` (warm capability index + parsed-artifact cache + buffered miss sink).

```
PASS 1 (before harvest)
   sr.route(...) for 98 hints
   → built-in selection falls back (registry has no built-ins)
   → autonomy_consult invoked (RuntimeQueryRouter with hot_path)
   → no auto-promoted solvers exist yet → MISS
   → router enqueues runtime_gap_signal into BufferedSignalSink (no SQLite write)
   Result: 0 served, 98 misses, 98 signals enqueued, 98 flushed via force_flush.

HARVEST CYCLE
   digest_signals_into_intents → 98 growth_intents created, 98 enqueued
   AutogrowthScheduler.run_until_idle → 98 auto-promoted, 0 rejected, 0 errored
   AutoPromotionEngine.evaluate_candidate now also writes
       solver_capability_features rows for every promotion.

PASS pre_cache (with sr_no_cache)
   sr_no_cache.route(...) for 98 hints
   → SolverRouter falls back, autonomy_consult invoked
   → RuntimeQueryRouter (no hot_path) calls dispatch_by_features →
     SQLite find_auto_promoted_solvers_by_features + get_solver_artifact +
     json.loads + execute_artifact for every query.
   This is the Phase 13 cost. We measure latency here for the warm-vs-
   pre-cache comparison required by P3 acceptance thresholds.

PASS 2 (with sr; cold cache)
   sr.route(...) for 98 hints
   → autonomy_consult → RuntimeQueryRouter with hot_path
   → first call per (family, features) goes through warm_dispatch's cold
     branch: one SQLite call to populate WarmCapabilityIndex +
     ParsedArtifactCache, then execute_artifact.
   Result: 98 served, all via auto_promoted_solver source.

PASS 3 (warm only, 3 iterations)
   sr.route(...) for 98 hints × 3 = 294 calls
   → warm_dispatch hits the in-memory caches, executes, returns. No SQLite.
```

## Headline numbers

| metric | value |
|---|---:|
| corpus_total | 98 |
| pass 1: served | 0 |
| pass 1: fallback / miss | 98 |
| pass 1: signals enqueued (buffered sink, no sync write) | 98 |
| pass 1: signals flushed by `force_flush` | 98 |
| harvest: intents created | 98 |
| harvest: scheduler_promoted | 98 |
| harvest: rejected / errored | 0 / 0 |
| pass pre_cache: served via consult | 98 |
| pass 2 (cold cache): served | 98 |
| pass 2: served_via_capability_lookup | 98 |
| pass 3 (warm only, 3 iters): warm-cache hits | 300 |
| `solver_capability_features` rows after proof | 196 |
| `growth_events` total | 392 |
| **`provider_jobs` delta during proof** | **0** |
| **`builder_jobs` delta during proof** | **0** |
| buffered_sink max_unflushed_signals observed | 98 |
| documented hard-kill loss bound (signals) | 1000 |

## Latency

| measurement | p50 (ms) | p95 (ms) | p99 (ms) |
|---|---:|---:|---:|
| pass 1 buffered miss (no SQLite write) | 0.0977 | — | 0.20 |
| **pre-cache baseline (Phase 13 SQLite-bound dispatch)** | **0.4357** | 0.7341 | 0.9389 |
| cold cache (first hit through `HotPathCache.warm_dispatch`) | 0.4656 | 0.6985 | 0.9466 |
| **warm cache (memory-only)** | **0.0657** | 0.1140 | 0.1450 |

**Warm vs pre-cache ratio: 6.63× faster** (warm p50 0.0657 ms vs pre-cache p50 0.4357 ms).

### P3 acceptance threshold attainment

| threshold | floor target | stretch target | observed | floor met? | stretch met? |
|---|---:|---:|---:|---|---|
| warm_p50_ms | ≤ 1.0 | ≤ 0.5 | **0.0657** | ✅ | ✅ |
| warm_p99_ms | ≤ 10 | ≤ 5 | **0.1450** | ✅ | ✅ |
| cold_p50_ms | ≤ 75 | ≤ 50 | **0.4656** | ✅ | ✅ |
| cold_p99_ms | ≤ 250 | ≤ 200 | **0.9466** | ✅ | ✅ |
| warm_vs_pre_cache_ratio | ≥ 5× | ≥ 10× | **6.63×** | ✅ | ❌ |

**Floor: ALL MET (5/5).** **Stretch: 4 of 5 met; the warm-vs-pre-cache ratio missed.**

Why the ratio missed stretch: the pre-cache baseline runs against an isolated in-process WAL SQLite. Even at full Phase 13 cost, that baseline is ~0.44 ms — already quite fast. The warm path collapses it to ~0.07 ms (3.7 µs of feature-key normalization + dict lookup + 2-µs `execute_artifact` for these pure functions). On a production DB with concurrent write pressure or a non-WAL path, the pre-cache cost would rise and the ratio would naturally exceed 10×. The session does *not* claim stretch attainment for this metric.

## Per-family served (after harvest)

All six allowlisted families covered:

| family | served | seed library size |
|---|---:|---:|
| `scalar_unit_conversion` | 27 | 27 |
| `lookup_table` | 16 | 16 |
| `threshold_rule` | 16 | 16 |
| `interval_bucket_classifier` | 13 | 13 |
| `linear_arithmetic` | 13 | 13 |
| `bounded_interpolation` | 13 | 13 |

## Per-cell served (after harvest)

Eight distinct hex cells covered.

## Buffered signal sink — observed vs documented bound

* `max_unflushed_signals_configured` = 1000 (≤ RULE P3.3 invariant of 1000)
* `max_unflushed_age_ms_configured` = 500 (≤ RULE P3.3 invariant of 500)
* `max_unflushed_signals_observed` (proof workload) = 98
* `max_unflushed_age_ms_observed` (proof workload) = 0 (force-flush draining queue before age trip)
* `documented_hardkill_loss_bound_signals` = **≤ 1000** (the configured ceiling)

Truthful contract: under SIGKILL the sink may lose up to **1000** signals OR up to one **500 ms** window's worth of signals — whichever is tighter for the workload. Graceful shutdown / `atexit` perform a best-effort synchronous final flush. SIGKILL bypasses `atexit`; the documented bound applies.

## Hot-path cache stats

* `warm_hits` = 300 (pass 3 × 98 + leftover warmed-then-hit calls)
* `cold_hits_warmed` = 92 (first calls per unique `(family, features)`; some seeds share feature shapes so the unique-cold-fill count is below 98)
* `misses` = 98 (pass 1, before any promotions exist)

## Truthfulness boundary — what this proof does NOT show

* **Not Stage-2 cutover.** `core/faiss_store.py:26 _DEFAULT_FAISS_DIR=data/faiss/` unchanged.
* **Not full autonomy.** Six allowlisted families. The four excluded families remain human-gated (RULE 19 honoured: no widening this session).
* **Not a substitute for the Phase 9 14-stage promotion ladder.** Runtime-stage promotions still require `human_approval_id`.
* **Not factory-safe by default.** No capsule-aware overlay yet.
* **Not actuator-side autonomy.** Auto-promoted solvers remain *consultable*; built-in solvers retain precedence.
* **Not real Anthropic / OpenAI HTTP adapters.** Only `dry_run_stub` and `claude_code_builder_lane` are exercisable.
* **Not a runtime *call site* in production traffic yet.** The router is wired into `SolverRouter.route` as a backwards-compatible context-hint extension. Production callers (e.g. autonomy runtime hot paths) will start passing the hint in a follow-up phase. The proof exercises the same real handler function that production calls today; what's new is the consult lane it now invokes when fallback occurs.
* **Not a sharded or federated setup.** Single-process scope, RULE 20 honoured.
* **No consciousness claim.** Hot-path autonomy is an engineering mechanism — observable, reversible, audit-traceable.

## Reproducing the proof

```
python tools/run_live_runtime_hotpath_proof.py
```

The script deletes its scratch DB at the start of each run so pass 1 truthfully misses. Default outputs:

* `docs/runs/phase14_live_runtime_hotpath_2026_05_01/live_runtime_hotpath_proof.json` — committed canonical machine-readable evidence.
* `docs/runs/phase14_live_runtime_hotpath_2026_05_01/live_runtime_hotpath_proof.db` — scratch SQLite, regenerated each run; gitignored via the repo's `*.db` rule.

The script accepts `--out-dir` and `--db` to redirect outputs.

## Cross-references

* `docs/architecture/LOW_RISK_AUTOGROWTH_POLICY.md` — Phase 11 policy envelope.
* `docs/architecture/PROVIDER_PLANE_AND_BUILDER_LANES.md` — inner / outer loop distinction (Phase 12 + 13 updates).
* `docs/runs/phase11_autogrowth_2026_04_29/autonomy_proof.md` — Phase 11 manual proof.
* `docs/runs/phase12_self_starting_autogrowth_2026_04_30/autonomy_proof.md` — Phase 12 self-starting synthetic harness.
* `docs/runs/phase13_runtime_harvest_2026_04_30/runtime_harvest_proof.md` — Phase 13 runtime-router proof (still synthetic-script-driven).
* `docs/journal/2026-05-01_live_runtime_hotpath_bottlenecks.md` — Phase 14 P1 audit + scorecard.
* `tests/autonomy_growth/test_solver_router_autonomy_consult.py` — locks the SolverRouter ↔ autonomy-consult wiring.
* `tests/autonomy_growth/test_hot_path_cache.py` — locks WarmCapabilityIndex / ParsedArtifactCache / BufferedSignalSink invariants.
* `tests/autonomy_growth/test_live_runtime_hotpath_proof_smoke.py` — locks the before/after invariants of this proof.
