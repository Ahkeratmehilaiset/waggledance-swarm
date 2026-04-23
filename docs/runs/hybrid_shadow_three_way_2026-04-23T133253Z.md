# Phase C three-way shadow — 2026-04-23T133253Z

**Queries:** 420
**Source:** oracle

## Latency (ms)

| Architecture | p50 | p95 |
|---|---:|---:|
| keyword | 0.02 | 0.04 |
| flat | 0.11 | 0.18 |
| hex | 0.65 | 1.47 |

## Agreement

- Flat vs hex solver agreement: 96.0%
- Keyword vs hex cell agreement: 33.8%
- Keyword/centroid disagreement rate: 39.0%

## Oracle metrics

| Architecture | Overall precision@1 | Positive precision | Negative rejection |
|---|---:|---:|---:|
| keyword | 33.3% | 0.0% | 100.0% |
| flat | 32.9% | 49.3% | 0.0% |
| hex | 32.6% | 48.9% | 0.0% |
| hex_qf | 33.1% | 48.9% | 1.4% |

## Decision gates (v3 §1.5)

- If `hex` precision@1 ≥ `keyword` - 2pp AND `hex` recall@5 ≥ `keyword` + 5pp → proceed to Phase D-1
- If `flat` ties or beats `hex` → abandon hex topology, refactor to flat-only
- If both lose to `keyword` → do not enable

**Full per-query data:** `docs\runs\hybrid_shadow_three_way_2026-04-23T133253Z.json`