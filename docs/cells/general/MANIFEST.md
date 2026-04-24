# Cell manifest — `general`

- **Schema version:** 1
- **Generated:** 2026-04-24T05:51:14+00:00
- **Manifest hash:** `sha256:5666f949fba8bb6a5578bb51066c644af05db5d7818b5c75354233cc87756b3f`
- **Level:** 0   **Parent:** —
- **Solver count:** 1
- **Gap score:** 0.398  *(0 = saturated, 1 = empty/failing)*
- **Siblings (ring-1):** learning, math, safety, seasonal
- **Neighbors (ring-2):** energy, system, thermal
- **Latency p50/p95:** None / None ms
- **LLM fallback rate:** None

## Existing solvers

| id | signature | domain | inputs | outputs | formulas | tier |
|---|---|---|---|---|---|---|
| `indoor_air_quality` | `fe09d1697927d366` | home | co2_ppm, voc_ppb, humidity_pct, temperature_c | iaq_score, co2_score, voc_score… | 5 | GOLD |

## Open gaps from production

- Unresolved queries matching cell vocabulary: **0** (shown) / total **0** matched

## Top LLM-fallback queries

*(none in scanned campaign data)*

## Teaching protocol

This is the ONLY context you see. Propose 1-3 new solvers or one improvement. Return YAML matching the schema of axiom files in configs/axioms/<domain>/*.yaml plus the proposal schema at schemas/solver_proposal.schema.json. Tests REQUIRED. The quality gate (tools/propose_solver.py) verifies schema, determinism, hash uniqueness, and in-cell contradictions before merging. Duplicates rejected by hash.
