# Phase C three-way shadow — 2026-04-23T093413Z

**Queries:** 420
**Source:** oracle

## Latency (ms)

| Architecture | p50 | p95 |
|---|---:|---:|
| keyword | 0.01 | 0.02 |
| flat | 0.07 | 0.1 |
| hex | 0.28 | 0.4 |

## Agreement

- Flat vs hex solver agreement: 97.1%
- Keyword vs hex cell agreement: 36.7%
- Keyword/centroid disagreement rate: 36.7%

## Oracle precision@1

| Architecture | Precision |
|---|---:|
| keyword | 33.3% |
| flat | 36.9% |
| hex | 36.7% |

## Decision gates (v3 §1.5)

- If `hex` precision@1 ≥ `keyword` - 2pp AND `hex` recall@5 ≥ `keyword` + 5pp → proceed to Phase D-1
- If `flat` ties or beats `hex` → abandon hex topology, refactor to flat-only
- If both lose to `keyword` → do not enable

**Full per-query data:** `docs\runs\hybrid_shadow_three_way_2026-04-23T093413Z.json`