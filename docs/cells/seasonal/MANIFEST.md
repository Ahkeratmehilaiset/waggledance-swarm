# Cell manifest — `seasonal`

- **Schema version:** 1
- **Generated:** 2026-04-24T05:51:14+00:00
- **Manifest hash:** `sha256:5ca3ef4c1fb68f5c39dd82db83213b320329cc6bfd867f74fcf2dafd4a368618`
- **Level:** 0   **Parent:** —
- **Solver count:** 5
- **Gap score:** 0.000  *(0 = saturated, 1 = empty/failing)*
- **Siblings (ring-1):** general, learning, thermal
- **Neighbors (ring-2):** energy, math, safety, system
- **Latency p50/p95:** None / None ms
- **LLM fallback rate:** None

## Existing solvers

| id | signature | domain | inputs | outputs | formulas | tier |
|---|---|---|---|---|---|---|
| `autumn_preparation` | `dc818f000be58cdf` | cottage | mite_treatment_done, current_reserves_kg, required_reserves_kg, roof_insulated… | readiness_score, mite_score, food_score… | 5 | SILVER |
| `nectar_flow_timing` | `afb4f2fdf87ee4f3` | cottage | current_gdd, base_temp_gdd, peak_gdd_threshold, avg_daily_gdd_rate… | days_until_peak_flow, growing_degree_days_accumulated, super_install_day… | 4 | GOLD |
| `spring_inspection_timing` | `b711e83df836dbff` | cottage | current_outdoor_c, avg_daily_warming_rate_c, colony_strength_fraction | days_until_safe, min_inspection_temperature_c, temperature_gap… | 4 | SILVER |
| `varroa_treatment_calendar` | `8eb9247d1ecd9dda` | cottage | mite_count_per_100_bees, days_since_last_treatment, standard_interval_days, brood_present | days_until_next_treatment, mite_growth_factor, urgency_score… | 4 | GOLD |
| `winter_feeding_decision` | `67c6f1f6a5e3ce24` | cottage | colony_size_k, daily_consumption_g_per_1000_bees, winter_days, current_reserves_kg… | feeding_needed_kg, estimated_consumption_kg, deficit_margin_kg… | 4 | GOLD |

## Open gaps from production

- Unresolved queries matching cell vocabulary: **0** (shown) / total **0** matched

## Top LLM-fallback queries

*(none in scanned campaign data)*

## Teaching protocol

This is the ONLY context you see. Propose 1-3 new solvers or one improvement. Return YAML matching the schema of axiom files in configs/axioms/<domain>/*.yaml plus the proposal schema at schemas/solver_proposal.schema.json. Tests REQUIRED. The quality gate (tools/propose_solver.py) verifies schema, determinism, hash uniqueness, and in-cell contradictions before merging. Duplicates rejected by hash.
