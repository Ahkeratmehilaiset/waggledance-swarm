# Hex-cell library index

**Generated:** 2026-04-23T02:35:47+00:00
**Total solvers:** 14
**Campaign queries analysed:** 33001

## Per-cell status

| cell | solvers | gap score | unresolved prod queries | siblings |
|---|---|---|---|---|
| `general` | 1 | 0.268 | 0 | learning, math, safety, seasonal |
| `thermal` | 4 | 0.000 | 0 | energy, safety, seasonal |
| `energy` | 3 | 0.000 | 0 | math, safety, thermal |
| `safety` | 2 | 0.000 | 0 | energy, general, system, thermal |
| `seasonal` | 0 | 0.625 | 0 | general, learning, thermal |
| `math` | 2 | 0.000 | 0 | energy, general, system |
| `system` | 2 | 0.000 | 0 | learning, math, safety |
| `learning` | 0 | 0.625 | 0 | general, seasonal, system |

## How to use

1. **Leadership/PM:** look at gap scores — high-score cells are where Claude-teacher should spend time next.
2. **Claude-teacher session (future `tools/propose_solver.py`):** feeds ONE cell's `manifest.json` at a time. Claude sees 20-50 solvers max.
3. **Regenerate this index:** `python tools/cell_manifest.py` rewrites `docs/cells/<cell>/{MANIFEST.md,manifest.json}` for all cells plus this index.

## Scaling math

- Level 0 (now): 8 cells. Target 50 solvers/cell = 400 solvers.
- Level 1 (3 mo): each cell → 6 sub-cells. 48 × 50 = 2400 solvers.
- Level 2 (12 mo): 288 cells × 30 = 8640 solvers.
- Level 3 (24 mo): 1728 cells × 20 = ~34k solvers.

Routing path length = log₆(n) ≈ 6 hops to 36k, per-hop ~3-5 ms embedding match → ~20-30 ms end-to-end for 34k-solver library.
