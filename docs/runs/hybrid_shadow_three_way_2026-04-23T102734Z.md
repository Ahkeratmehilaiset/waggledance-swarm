# Phase C three-way shadow — 2026-04-23T102734Z

**Queries:** 420
**Source:** oracle

## Latency (ms)

| Architecture | p50 | p95 |
|---|---:|---:|
| keyword | 0.01 | 0.04 |
| flat | 0.09 | 0.16 |
| hex | 0.41 | 1.14 |

## Agreement

- Flat vs hex solver agreement: 97.1%
- Keyword vs hex cell agreement: 36.7%
- Keyword/centroid disagreement rate: 36.7%

## Oracle metrics

| Architecture | Overall precision@1 | Positive precision | Negative rejection |
|---|---:|---:|---:|
| keyword | 33.3% | 0.0% | 100.0% |
| flat | 36.9% | 55.4% | 0.0% |
| hex | 36.7% | 55.0% | 0.0% |
| hex_qf | 37.1% | 55.0% | 1.4% |

## Decision gates (v3 §1.5)

- If `hex` precision@1 ≥ `keyword` - 2pp AND `hex` recall@5 ≥ `keyword` + 5pp → proceed to Phase D-1
- If `flat` ties or beats `hex` → abandon hex topology, refactor to flat-only
- If both lose to `keyword` → do not enable

**Full per-query data:** `docs\runs\hybrid_shadow_three_way_2026-04-23T102734Z.json`