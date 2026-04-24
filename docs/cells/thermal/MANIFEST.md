# Cell manifest — `thermal`

- **Schema version:** 1
- **Generated:** 2026-04-24T04:27:31+00:00
- **Manifest hash:** `sha256:7848bb48a09b70ae934f887e0a20a9fb8156c8471150b656e0ec7517cf43a318`
- **Level:** 0   **Parent:** —
- **Solver count:** 4
- **Gap score:** 0.000  *(0 = saturated, 1 = empty/failing)*
- **Siblings (ring-1):** energy, safety, seasonal
- **Neighbors (ring-2):** general, learning, math, system
- **Latency p50/p95:** None / None ms
- **LLM fallback rate:** None

## Existing solvers

| id | signature | domain | inputs | outputs | formulas | tier |
|---|---|---|---|---|---|---|
| `heat_pump_cop` | `0bd346ca32f26b85` | home | T_indoor, T_outdoor, efficiency_factor, building_ua… | hourly_cost_eur, carnot_cop, real_cop… | 5 | SILVER |
| `heating_cost` | `c8889bc1642b7bc7` | cottage | T_indoor, T_outdoor, area_m2, R_value… | monthly_cost, heat_loss_rate, daily_energy_kwh… | 4 | SILVER |
| `hive_thermal_balance` | `4c7c5e6f436559e6` | cottage | N_bees, q_per_bee, activity_factor, T_outside… | survival, Q_bees, Q_loss… | 4 | SILVER |
| `pipe_freezing` | `4241a763541181d8` | cottage | T_outdoor, T_water, pipe_length_m, pipe_diameter_m… | time_to_freeze_hours, heat_loss_rate | 2 | SILVER |

## Open gaps from production

- Unresolved queries matching cell vocabulary: **0** (shown) / total **0** matched

## Top LLM-fallback queries

*(none in scanned campaign data)*

## Teaching protocol

This is the ONLY context you see. Propose 1-3 new solvers or one improvement. Return YAML matching the schema of axiom files in configs/axioms/<domain>/*.yaml plus the proposal schema at schemas/solver_proposal.schema.json. Tests REQUIRED. The quality gate (tools/propose_solver.py) verifies schema, determinism, hash uniqueness, and in-cell contradictions before merging. Duplicates rejected by hash.
