# Cell manifest — `system`

- **Schema version:** 1
- **Generated:** 2026-04-24T04:27:31+00:00
- **Manifest hash:** `sha256:9089a8cc18dd7b20804bea8bb21a80534cff350913dd847729fade459ae25666`
- **Level:** 0   **Parent:** —
- **Solver count:** 3
- **Gap score:** 0.000  *(0 = saturated, 1 = empty/failing)*
- **Siblings (ring-1):** learning, math, safety
- **Neighbors (ring-2):** energy, general, seasonal, thermal
- **Latency p50/p95:** None / None ms
- **LLM fallback rate:** None

## Existing solvers

| id | signature | domain | inputs | outputs | formulas | tier |
|---|---|---|---|---|---|---|
| `mtbf_prediction` | `a1a5097d9f7ed15d` | factory | total_operating_hours, number_of_failures, mission_time | recommended_pm_interval, mtbf, failure_rate… | 4 | SILVER |
| `oee_decomposition` | `c37db7a8ff6a336f` | factory | planned_time, downtime, actual_output, ideal_cycle_rate… | oee, availability, performance… | 4 | SILVER |
| `signal_propagation` | `60ca58fda041ad18` | gadget | distance_m, frequency_mhz, tx_power_dbm, tx_gain_dbi… | received_power_dbm, fspl_db | 2 | SILVER |

## Open gaps from production

- Unresolved queries matching cell vocabulary: **0** (shown) / total **0** matched

## Top LLM-fallback queries

*(none in scanned campaign data)*

## Teaching protocol

This is the ONLY context you see. Propose 1-3 new solvers or one improvement. Return YAML matching the schema of axiom files in configs/axioms/<domain>/*.yaml plus the proposal schema at schemas/solver_proposal.schema.json. Tests REQUIRED. The quality gate (tools/propose_solver.py) verifies schema, determinism, hash uniqueness, and in-cell contradictions before merging. Duplicates rejected by hash.
