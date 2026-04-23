# Phase C three-way shadow — 2026-04-23T102843Z

**Queries:** 30
**Source:** oracle

## Latency (ms)

| Architecture | p50 | p95 |
|---|---:|---:|
| keyword | 0.01 | 0.01 |
| flat | 0.07 | 0.08 |
| hex | 0.27 | 0.3 |

## Agreement

- Flat vs hex solver agreement: 100.0%
- Keyword vs hex cell agreement: 43.3%
- Keyword/centroid disagreement rate: 23.3%

## Oracle metrics

| Architecture | Overall precision@1 | Positive precision | Negative rejection |
|---|---:|---:|---:|
| keyword | 33.3% | 0.0% | 100.0% |
| flat | 53.3% | 80.0% | 0.0% |
| hex | 53.3% | 80.0% | 0.0% |
| hex_qf | 53.3% | 80.0% | 0.0% |

## Decision gates (v3 §1.5)

- If `hex` precision@1 ≥ `keyword` - 2pp AND `hex` recall@5 ≥ `keyword` + 5pp → proceed to Phase D-1
- If `flat` ties or beats `hex` → abandon hex topology, refactor to flat-only
- If both lose to `keyword` → do not enable

**Full per-query data:** `docs\runs\hybrid_shadow_three_way_2026-04-23T102843Z.json`