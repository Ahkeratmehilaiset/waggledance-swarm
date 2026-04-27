# Phase 8 capability-growth report

- **Generated:** 2026-04-24T04:46:39+00:00
- **Axioms:** `configs/axioms`
- **Campaign rows scanned:** 10000
- **Campaign dir:** `docs/runs/ui_gauntlet_400h_20260413_092800`

## solver_count_by_cell

| cell | count |
|---|---|
| `general` | 1 |
| `thermal` | 4 |
| `energy` | 3 |
| `safety` | 2 |
| `seasonal` | 5 |
| `math` | 2 |
| `system` | 3 |
| `learning` | 2 |

## solver_route_depth

- p50 = 1.0
- p95 = 1.0
- samples = 10000

## solver_route_latency_ms (overall)

- p50 = 2527.0
- p95 = 10896.0
- samples = 10000

## solver_route_latency_ms — per cell

| cell | p50 | p95 |
|---|---|---|
| `general` | None | None |
| `thermal` | None | None |
| `energy` | None | None |
| `safety` | None | None |
| `seasonal` | None | None |
| `math` | None | None |
| `system` | None | None |
| `learning` | None | None |

## llm_fallback_rate_by_cell

| cell | rate |
|---|---|
| `general` | None |
| `thermal` | None |
| `energy` | None |
| `safety` | None |
| `seasonal` | None |
| `math` | None |
| `system` | None |
| `learning` | None |

## useful_composite_paths

- total = 72
- by_depth = {2: 22, 3: 26, 4: 24}

## dream_bridge_candidates: 38

## cell_gap_score

| cell | score |
|---|---|
| `general` | 0.398 |
| `thermal` | 0.0 |
| `energy` | 0.0 |
| `safety` | 0.17 |
| `seasonal` | 0.0 |
| `math` | 0.17 |
| `system` | 0.0 |
| `learning` | 0.17 |

## cell_entropy_score

| cell | distinct output units |
|---|---|
| `general` | 3 |
| `thermal` | 8 |
| `energy` | 6 |
| `safety` | 6 |
| `seasonal` | 11 |
| `math` | 3 |
| `system` | 6 |
| `learning` | 6 |

## Proposal-gate counters

*(empty until the teacher loop drives `tools/propose_solver.py`; schema present, no data yet)*
