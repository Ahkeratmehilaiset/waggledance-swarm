# Cell manifest — `energy`

- **Schema version:** 1
- **Generated:** 2026-04-24T04:27:31+00:00
- **Manifest hash:** `sha256:5d70ea20a8e1a73ec8b17ba9e61996f156c68e36416d2321b39ae38dbda4d1bc`
- **Level:** 0   **Parent:** —
- **Solver count:** 3
- **Gap score:** 0.000  *(0 = saturated, 1 = empty/failing)*
- **Siblings (ring-1):** math, safety, thermal
- **Neighbors (ring-2):** general, seasonal, system
- **Latency p50/p95:** None / None ms
- **LLM fallback rate:** None

## Existing solvers

| id | signature | domain | inputs | outputs | formulas | tier |
|---|---|---|---|---|---|---|
| `battery_discharge` | `4edcb668d2cdc697` | gadget | capacity_mah, I_active, I_sleep, duty_cycle | battery_life_hours | 1 | SILVER |
| `comfort_energy_tradeoff` | `70190e926b1860af` | home | T_day, T_night, T_outdoor, building_ua… | cost_saved_per_night, energy_saved_percent | 2 | SILVER |
| `solar_yield` | `64667493ec6c2242` | cottage | panel_area_m2, panel_efficiency, sun_hours, cell_temp | daily_yield_kwh, temperature_derating, peak_power_w | 3 | SILVER |

## Open gaps from production

- Unresolved queries matching cell vocabulary: **0** (shown) / total **0** matched

## Top LLM-fallback queries

*(none in scanned campaign data)*

## Teaching protocol

This is the ONLY context you see. Propose 1-3 new solvers or one improvement. Return YAML matching the schema of axiom files in configs/axioms/<domain>/*.yaml plus the proposal schema at schemas/solver_proposal.schema.json. Tests REQUIRED. The quality gate (tools/propose_solver.py) verifies schema, determinism, hash uniqueness, and in-cell contradictions before merging. Duplicates rejected by hash.
