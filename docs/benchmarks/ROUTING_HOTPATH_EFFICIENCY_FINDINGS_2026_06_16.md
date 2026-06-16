# Routing Hot-Path Efficiency — Findings & Implementation Candidates (2026-06-16)

**Status:** producer-lane findings report (fable-5, non-gate identity).
**Date:** 2026-06-16.
**Author:** fable-5.
**Task:** `fable-5/industrial-efficiency-benchmark-slice-20260616` (lead handoff
toward the WaggleDanceSwarmAi industrial-grade efficiency goal).

This is an **engineering** record. It does not assert WaggleDance is faster,
more efficient, more correct, or superior to any named external system. The
numbers cited below are reproduced from the prior Phase 17B run and from the
live routing code as of HEAD `c675a65a`; they are context, not a new claim. The
implementation candidates in §6 are **hypotheses to validate by measurement**,
not asserted wins.

## 1. Purpose

Identify where the per-request **routing / classification hot path** is and is
not measured today, and propose a budget-cheap, measurement-first path to
characterize and (only if warranted) optimize it. This is a docs-only first
slice; the runnable micro-benchmark in §5 is the intended fast-follow.

## 2. Existing efficiency-measurement landscape (inventory)

| source | what it measures | measured baseline (2026-05-04) |
|---|---|---|
| `tools/run_phase17b_local_efficiency_benchmark.py` track **B** capability_lookup_10k | 10k descriptors, 1k lookups, p50/p95/p99 | **4.33 / 10.98 / 14.39 ms** |
| track **C** handle_query_e2e | `service.handle_query` end-to-end | cold p50 ~16 ms / warm p50 ~11 ms |
| tracks **A / D / E** | hint extraction / restart continuity / producer fabric | correctness-focused (latency not the focus) |
| `docs/benchmarks/FUTURE_SCALE_*.md` + `tools/run_future_scale_*` | latency, route_depth, composite_path, contradiction_rate, insight_score | future-scale specs |
| `configs/benchmarks.yaml` | canonical corpus: 30 queries across route types (model_based / retrieval / statistical / llm_reasoning) and languages (en/fi) | — |

## 3. The routing hot path (live code)

`core/smart_router_v2.py` → `SmartRouterV2.route(query)` runs on **every**
request. Priority order:

1. `HotCache.get(query)` — optional short-circuit (returns cached layer).
2. `capsule.match_decision(query)` — capsule key-decision match.
3. `_classify_keywords(query)` — module-level **pre-compiled** regexes
   (`_MATH_KEYWORDS`, `_RULE_KEYWORDS`, `_STAT_KEYWORDS`, `_RETRIEVAL_KEYWORDS`,
   `_SEASONAL_KEYWORDS`) over the query and an ASCII-normalized form
   (`_normalize_fi`) so Finnish diacritics match.
4. Capsule default fallback (`default_fallback`, else highest-priority layer).

The path is already instrumented: each branch records `routing_time_ms` via
`time.perf_counter()`, and `_record()` keeps a per-layer distribution
(`stats()`). `waggledance/core/reasoning/route_engine.py` wraps this with
telemetry/accuracy aggregation (bounded decision log, `_max_decisions = 2000`).

## 4. The gap (the finding)

`route()` **emits** per-call `routing_time_ms`, but there is **no aggregated,
corpus-driven, isolated micro-benchmark** for routing latency or its scaling.
Consequences:

* We cannot attribute how much of the track-C e2e latency (~11–16 ms) is
  **routing** vs retrieval vs model invocation.
* There is **no scaling curve** for step-2 `match_decision` as capsule
  decision-sets grow — the dimension that matters most at industrial scale.
* The existing tracks isolate capability lookup (B), e2e (C), restart (D), and
  producer fabric (E) — **none isolate routing**.

This is a **measurement** gap, not an instrumentation gap: the timing hooks
already exist; nothing aggregates them in isolation.

## 5. Proposed micro-benchmark design (fast-follow PR)

`tools/run_routing_hotpath_microbench.py` (proposed):

* **Offline, deterministic, no model/cloud calls.** Reuse the Phase-17B
  `FORBIDDEN_VOCABULARY` guard and a release-gate-style JSON envelope
  (`no_cloud_api_calls_this_session`, `forbidden_vocabulary_excluded`).
* **Driver:** build a `DomainCapsule` (or a deterministic stub), instantiate
  `SmartRouterV2`, run `route()` over the 30 `configs/benchmarks.yaml` queries
  × N repeats.
* **Metrics:** per-query `routing_time_ms` → p50/p95/p99/mean; **which step
  resolved** the route (cache / decision / keyword / fallback); classification
  stability across repeats; **cold vs warm** (first-call import + normalize cost
  vs steady state).
* **Scaling track:** synthetic capsules with K decisions (K = 10 / 100 / 1000)
  to plot `match_decision` cost vs K.
* **Output:** JSON artifact (per `docs/benchmarks/BENCHMARK_ARTIFACT_SCHEMA.md`)
  plus an MD summary.

## 6. Implementation candidates (ranked; measurement-first)

Each candidate is a hypothesis to confirm with the §5 micro-benchmark **before**
any optimization is merged.

| # | candidate | expected impact | risk | effort | how to validate |
|---|---|---|---|---|---|
| **C1** | Decompose track-C e2e into routing vs non-routing by aggregating the existing `routing_time_ms`. | Tells us whether routing is even worth optimizing (highest information value, near-zero cost). | none (read-only metric) | S | §5 routing numbers vs track-C e2e. |
| **C2** | Characterize & bound `match_decision` cost vs capsule decision-set size. | The main industrial-scale lever; step 2 runs per non-cached query. | low | M | §5 scaling track (K=10/100/1000). If superlinear → index/precompute capsule decisions. |
| **C3** | Quantify `HotCache` effectiveness and evaluate a default-on policy. | High under repeated/duplicate load; zero under all-unique. | staleness / memory | S–M | §5 hit-rate vs latency on a repeated-query corpus. |
| **C4** | Cache / short-circuit `_normalize_fi` for repeated queries. | Small per-call, compounds at volume. | low | S | §5 before/after on the keyword path. |
| **C5** | Keep telemetry recording O(1) and off the critical path. | Prevents `_record()` + the bounded decision log from becoming the bottleneck at scale. | low | S | §5 with telemetry on/off. |

## 7. Honest scope / non-claims

The cited numbers come from the prior Phase 17B run (2026-05-04) and the live
`route()` code at HEAD `c675a65a`. The §5 micro-benchmark will produce fresh,
routing-specific numbers. No claim of superiority over any external system is
made or implied. No candidate in §6 should be merged as an optimization until
the §5 measurement shows a real, reproducible win.

## 8. Mapping to the sprint plan

Consistent with the Claude-heavy, conserve-Lead/Tools sprint plan: this report
is **docs-only** (low CI cost, low review cost). The §5 micro-benchmark is the
next fast-follow producer slice. Optimizations (C1–C5) land only after the
micro-benchmark shows a reproducible win — no speculative refactors that spend
scarce Lead/Tools review budget.

## 9. References

* `core/smart_router_v2.py` — live `route()` hot path.
* `waggledance/core/reasoning/route_engine.py` — telemetry/accuracy wrapper.
* `tools/run_phase17b_local_efficiency_benchmark.py` — `FORBIDDEN_VOCABULARY`, track design, JSON envelope.
* `configs/benchmarks.yaml` — 30-query canonical corpus.
* `docs/benchmarks/LOCAL_EFFICIENCY_BENCHMARK_2026.md` — baseline numbers.
* `docs/benchmarks/BENCHMARK_ARTIFACT_SCHEMA.md` — artifact schema for the §5 fast-follow.
* `docs/benchmarks/FUTURE_SCALE_*_BENCHMARK.md` — future-scale specs.
