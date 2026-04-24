# Cell manifest — `math`

- **Schema version:** 1
- **Generated:** 2026-04-24T05:51:14+00:00
- **Manifest hash:** `sha256:3aa39f0d04a552c9f9608ed2d9a2416e3c7e1859815b0c49525f0acf373a2b4b`
- **Level:** 0   **Parent:** —
- **Solver count:** 2
- **Gap score:** 0.170  *(0 = saturated, 1 = empty/failing)*
- **Siblings (ring-1):** energy, general, system
- **Neighbors (ring-2):** learning, safety, seasonal, thermal
- **Latency p50/p95:** None / None ms
- **LLM fallback rate:** None

## Existing solvers

| id | signature | domain | inputs | outputs | formulas | tier |
|---|---|---|---|---|---|---|
| `colony_food_reserves` | `baab261488f56e2f` | cottage | bee_cluster_kg, food_per_kg_bees, winter_months, food_available_kg | feeding_needed_kg, food_needed_kg, food_deficit_kg… | 4 | SILVER |
| `honey_yield` | `81de2d9eb1af86cd` | cottage | colony_strength, forager_ratio, nectar_load_mg, flights_per_day… | season_honey_kg, daily_foragers, daily_nectar_kg… | 4 | SILVER |

## Open gaps from production

- Unresolved queries matching cell vocabulary: **0** (shown) / total **0** matched

## Top LLM-fallback queries

*(none in scanned campaign data)*

## Teaching protocol

This is the ONLY context you see. Propose 1-3 new solvers or one improvement. Return YAML matching the schema of axiom files in configs/axioms/<domain>/*.yaml plus the proposal schema at schemas/solver_proposal.schema.json. Tests REQUIRED. The quality gate (tools/propose_solver.py) verifies schema, determinism, hash uniqueness, and in-cell contradictions before merging. Duplicates rejected by hash.
