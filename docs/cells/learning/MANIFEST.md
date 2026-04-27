# Cell manifest — `learning`

- **Schema version:** 1
- **Generated:** 2026-04-24T05:51:14+00:00
- **Manifest hash:** `sha256:a7328947e8b9810e9662143eb1d3fb24113d74eba9de2b7ad7f0051c4836b5ee`
- **Level:** 0   **Parent:** —
- **Solver count:** 2
- **Gap score:** 0.170  *(0 = saturated, 1 = empty/failing)*
- **Siblings (ring-1):** general, seasonal, system
- **Neighbors (ring-2):** math, safety, thermal
- **Latency p50/p95:** None / None ms
- **LLM fallback rate:** None

## Existing solvers

| id | signature | domain | inputs | outputs | formulas | tier |
|---|---|---|---|---|---|---|
| `colony_growth_rate` | `19c5272cb18f8fa7` | cottage | population, eggs_per_day, worker_survival_fraction, daily_mortality_fraction… | projected_population_30d, daily_birth_rate, daily_death_rate… | 6 | GOLD |
| `queen_age_replacement` | `a21b90e3290fada3` | cottage | queen_age_months, laying_rate_fraction, drone_layer_observed, supersedure_cells | replacement_priority, age_factor, performance_deficit… | 4 | GOLD |

## Open gaps from production

- Unresolved queries matching cell vocabulary: **0** (shown) / total **0** matched

## Top LLM-fallback queries

*(none in scanned campaign data)*

## Teaching protocol

This is the ONLY context you see. Propose 1-3 new solvers or one improvement. Return YAML matching the schema of axiom files in configs/axioms/<domain>/*.yaml plus the proposal schema at schemas/solver_proposal.schema.json. Tests REQUIRED. The quality gate (tools/propose_solver.py) verifies schema, determinism, hash uniqueness, and in-cell contradictions before merging. Duplicates rejected by hash.
