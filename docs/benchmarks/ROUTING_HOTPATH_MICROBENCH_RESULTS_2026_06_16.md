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

Artifact: `docs/runs/routing_hotpath_microbench_2026_06_16/routing_hotpath_microbench.json`.

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
