# Routing Hot-Path Micro-Benchmark — Results (2026-06-16)

**Status:** producer-lane measured results (fable-5, non-gate identity).
**Date:** 2026-06-16.
**Author:** fable-5.
**Task:** `fable-5/routing-hotpath-microbench-20260616` (fast-follow to the
findings report `ROUTING_HOTPATH_EFFICIENCY_FINDINGS_2026_06_16.md`).
**Harness:** `tools/run_routing_hotpath_microbench.py`.

Engineering record. Absolute numbers are host-dependent; this run reports what
executed locally and offline (no model or cloud calls). No claim of superiority
over any external system is made or implied.

## Reproduce

```
python tools/run_routing_hotpath_microbench.py \
    --profile apiary --repeats 50 \
    --scale 10 100 1000 --scale-repeats 20 \
    --out-dir docs/runs/routing_hotpath_microbench_2026_06_16
```

With `--out-dir` the harness writes the full JSON envelope to
`<out-dir>/routing_hotpath_microbench.json`. That raw run-artifact is
regenerable on demand and is **not committed in this PR** — `docs/runs/` is off
the autonomous-merge allowlist, and the measured numbers below are the durable
record. Regenerate it with the command above if you need the JSON.

## Representative run (profile `apiary`, 30-query corpus × 50 repeats = 1500 calls)

| metric | value |
|---|---:|
| routing latency p50 | **0.216 ms** |
| routing latency p95 | 0.298 ms |
| routing latency p99 | 0.360 ms |
| routing latency mean | 0.227 ms |
| cold (first call) | 0.321 ms |
| warm p50 | 0.215 ms |

**Resolved by step:** `capsule_decision_match` 800, `capsule_priority_fallback`
200, `keyword_classifier:*` 500 (retrieval 200 / math 100 / stat 100 / rule 50 /
seasonal 50). **Route distribution:** `model_based` 700, `retrieval` 250,
`llm_reasoning` 200, `statistical` 200, `rule_constraints` 150.

## Scaling track — `match_decision` worst case (synthetic capsule, non-matching keywords → full scan)

| K decisions | p50 (ms) | p95 (ms) | mean (ms) |
|---:|---:|---:|---:|
| 10 | 0.108 | 0.182 | 0.122 |
| 100 | 0.564 | 0.842 | 0.586 |
| 1000 | **5.394** | 7.768 | 5.445 |

p50 rises ~5× for each 10× increase in K → the worst-case `match_decision` cost
is **approximately linear, O(K)**.

## HotCache effectiveness track (C3) — route() short-circuit vs hit-rate

`route()` step 1 short-circuits on a `HotCache` hit. This track measures that
benefit with a **deterministic stub cache** (exact-query hits on a seeded
fraction of the corpus). The stub isolates the route()-level short-circuit; the
real `core.fast_memory.HotCache` adds Voikko-normalized key matching (a
host-dependent dependency) which is out of scope for an offline latency
measurement.

| seeded fraction | observed hit-rate | hit p50 (ms) | miss p50 (ms) | overall p50 (ms) |
|---:|---:|---:|---:|---:|
| 0.00 | 0.00 | — | 0.206 | 0.206 |
| 0.25 | 0.27 | 0.003 | 0.200 | 0.180 |
| 0.50 | 0.50 | 0.003 | 0.197 | 0.080 |
| 1.00 | 1.00 | 0.003 | — | 0.003 |

A cache hit resolves in ~0.003 ms vs a ~0.20 ms full route — roughly **65–80×
faster per hit** — and the overall p50 falls in step with the hit-rate. The
step-1 check adds negligible overhead on a miss.

## Findings (validating the candidates in the findings report)

* **C1 — routing is not the e2e bottleneck. CONFIRMED.** Representative routing
  is sub-millisecond (p50 0.216 ms, p99 0.360 ms) against a track-C e2e of
  ~11–16 ms (Phase-17B baseline). Routing is on the order of **~2 %** of e2e
  latency for a production-sized capsule. **Implication:** do not spend
  optimization budget on routing micro-latency for typical capsules — the e2e
  cost lives in retrieval/model layers, not routing.
* **C2 — `match_decision` scales O(K). CONFIRMED and quantified.** Worst-case
  routing climbs from 0.108 ms (K=10) to 5.394 ms (K=1000). For capsules that
  grow toward ~1000+ key decisions, `match_decision` becomes the dominant
  routing cost. **Implication:** if/when capsules reach that scale, replace the
  linear scan with a keyword→decision inverted index (or first-token bucket) to
  cut the per-query cost from O(K) toward O(matched). Until then it is not worth
  the change — current production profiles are far smaller.
* **C3 — HotCache value scales with query repetition. CONFIRMED.** A hit
  short-circuits to ~0.003 ms (vs ~0.20 ms full route, ~65–80× faster), and the
  overall p50 tracks the hit-rate (0.206 ms at 0 % → 0.003 ms at 100 %). The
  step-1 check is near-free on a miss. **Implication:** HotCache pays off in
  proportion to how repetitive the live query stream is; it is worth enabling
  for workloads with real query repetition and costs almost nothing when hits
  are rare. Since routing is already a small share of e2e (C1), this matters
  most as a way to skip the *whole* downstream path on a cached answer, not to
  shave routing itself.
* **Cold start is negligible.** cold 0.321 ms vs warm p50 0.215 ms — no
  meaningful first-call penalty; pre-compiled patterns already do their job.

## Net recommendation

Routing efficiency is healthy at current capsule sizes; the single
scale-sensitive lever is `match_decision`'s linear scan, and it only matters
past roughly a thousand decisions. Treat the inverted-index optimization (C2) as
**deferred until a capsule actually approaches that size** — measured here so the
trigger point is known, not guessed. No routing optimization is recommended for
merge today.

## References

* `tools/run_routing_hotpath_microbench.py` — this harness.
* `docs/benchmarks/ROUTING_HOTPATH_EFFICIENCY_FINDINGS_2026_06_16.md` — motivating findings + candidate list.
* `core/smart_router_v2.py`, `core/domain_capsule.py` — routing hot path under test.
* `configs/benchmarks.yaml` — 30-query corpus.
* `docs/benchmarks/LOCAL_EFFICIENCY_BENCHMARK_2026.md` — track-C e2e baseline (~11–16 ms).
