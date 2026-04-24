# Solver composition report

- **Generated:** 2026-04-24T06:03:35+00:00
- **Axiom dir:** `configs/axioms`
- **Axioms scanned:** 22
- **Solver nodes:** 22
- **Valid typed edges:** 22
- **Rejected edges:** 440
- **Paths depth 2 / 3 / 4:** 22 / 26 / 24
- **Bridge candidates:** 38

## Rejected-edge reasons

- `non_adjacent_cell`: 230
- `unit_mismatch`: 210

## Top bridge candidates

| score | from | to | unit | path |
|---|---|---|---|---|
| 2.75 | `learning` | `general` | `count` | colony_growth_rate → varroa_treatment_calendar → autumn_preparation → indoor_air_quality |
| 2.75 | `learning` | `general` | `count` | colony_growth_rate → winter_feeding_decision → autumn_preparation → indoor_air_quality |
| 2.75 | `system` | `seasonal` | `count` | oee_decomposition → colony_growth_rate → varroa_treatment_calendar → autumn_preparation |
| 2.75 | `system` | `seasonal` | `count` | oee_decomposition → colony_growth_rate → varroa_treatment_calendar → nectar_flow_timing |
| 2.75 | `system` | `seasonal` | `count` | oee_decomposition → colony_growth_rate → varroa_treatment_calendar → winter_feeding_decision |
| 2.75 | `system` | `seasonal` | `count` | oee_decomposition → colony_growth_rate → winter_feeding_decision → autumn_preparation |
| 2.5 | `learning` | `seasonal` | `count` | colony_growth_rate → varroa_treatment_calendar → nectar_flow_timing → autumn_preparation |
| 2.5 | `learning` | `seasonal` | `count` | colony_growth_rate → varroa_treatment_calendar → nectar_flow_timing → winter_feeding_decision |
| 2.5 | `learning` | `seasonal` | `count` | colony_growth_rate → varroa_treatment_calendar → winter_feeding_decision → autumn_preparation |
| 2.5 | `seasonal` | `general` | `days` | nectar_flow_timing → varroa_treatment_calendar → autumn_preparation → indoor_air_quality |
| 2.5 | `seasonal` | `general` | `days` | nectar_flow_timing → winter_feeding_decision → autumn_preparation → indoor_air_quality |
| 2.5 | `seasonal` | `general` | `days` | spring_inspection_timing → nectar_flow_timing → autumn_preparation → indoor_air_quality |
| 2.5 | `seasonal` | `general` | `days` | spring_inspection_timing → varroa_treatment_calendar → autumn_preparation → indoor_air_quality |
| 2.5 | `seasonal` | `general` | `days` | spring_inspection_timing → winter_feeding_decision → autumn_preparation → indoor_air_quality |
| 2.5 | `seasonal` | `general` | `days` | varroa_treatment_calendar → nectar_flow_timing → autumn_preparation → indoor_air_quality |
| 2.5 | `seasonal` | `general` | `days` | varroa_treatment_calendar → winter_feeding_decision → autumn_preparation → indoor_air_quality |
| 2.25 | `system` | `seasonal` | `count` | oee_decomposition → colony_growth_rate → varroa_treatment_calendar |
| 2.25 | `system` | `seasonal` | `count` | oee_decomposition → colony_growth_rate → winter_feeding_decision |
| 2.0 | `learning` | `seasonal` | `count` | colony_growth_rate → varroa_treatment_calendar → autumn_preparation |
| 2.0 | `learning` | `seasonal` | `count` | colony_growth_rate → varroa_treatment_calendar → nectar_flow_timing |

## Cells with bridge potential

- `seasonal`: 27 bridge endpoints
- `learning`: 15 bridge endpoints
- `system`: 15 bridge endpoints
- `general`: 14 bridge endpoints
- `math`: 2 bridge endpoints
- `safety`: 2 bridge endpoints

## Cells flagged for high entropy

A cell with many distinct output units may be heterogeneous
enough to warrant subdivision (Phase 7 input).

- `energy`
- `learning`
- `safety`
- `seasonal`
- `system`
- `thermal`

## Advisory rescale-possible edges (NOT runtime-valid)

These edges share a unit family (power, energy, length, …)
but differ in unit (e.g. W ↔ kW, factor 0.001). They would
chain if an explicit rescale step were inserted. The runtime
router never follows these. They are suggestions only.

- **Total:** 1
- By family:
  - `mass`: 1

| family | src | src_unit | dst | dst_unit | factor |
|---|---|---|---|---|---|
| `mass` | `colony_food_reserves` | `kg` | `honey_yield` | `mg` | 1e+06 |
