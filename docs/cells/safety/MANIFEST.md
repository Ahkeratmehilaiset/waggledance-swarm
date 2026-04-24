# Cell manifest — `safety`

- **Schema version:** 1
- **Generated:** 2026-04-24T04:27:31+00:00
- **Manifest hash:** `sha256:ce912a9d222ddafa46d013fe150f7759b9f46df028fea8b1d06003328226e882`
- **Level:** 0   **Parent:** —
- **Solver count:** 2
- **Gap score:** 0.170  *(0 = saturated, 1 = empty/failing)*
- **Siblings (ring-1):** energy, general, system, thermal
- **Neighbors (ring-2):** learning, math, seasonal
- **Latency p50/p95:** None / None ms
- **LLM fallback rate:** None

## Existing solvers

| id | signature | domain | inputs | outputs | formulas | tier |
|---|---|---|---|---|---|---|
| `swarm_risk` | `069b1bd6e09d6001` | cottage | empty_combs, total_combs, queen_age_years, queen_cells… | swarm_probability_pct, space_pressure, queen_age_factor… | 4 | SILVER |
| `varroa_treatment` | `82b5676474833d8e` | cottage | combs_with_bees, dose_per_comb_ml, varroa_before, colony_strength… | mite_load_pct, oxalic_acid_dose_ml, varroa_after_treatment… | 4 | SILVER |

## Open gaps from production

- Unresolved queries matching cell vocabulary: **0** (shown) / total **0** matched

## Top LLM-fallback queries

*(none in scanned campaign data)*

## Teaching protocol

This is the ONLY context you see. Propose 1-3 new solvers or one improvement. Return YAML matching the schema of axiom files in configs/axioms/<domain>/*.yaml plus the proposal schema at schemas/solver_proposal.schema.json. Tests REQUIRED. The quality gate (tools/propose_solver.py) verifies schema, determinism, hash uniqueness, and in-cell contradictions before merging. Duplicates rejected by hash.
