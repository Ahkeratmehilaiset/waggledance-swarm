# Cell manifest — `thermal`

**Generated:** 2026-04-23T02:35:43+00:00
**Solver count:** 4
**Gap score:** 0.000  *(0 = cell saturated, 1 = cell mostly empty or failing)*
**Siblings (ring-1):** energy, safety, seasonal

## Existing solvers

| model_id | domain | inputs | outputs | formulas |
|---|---|---|---|---|
| `heating_cost` | cottage | T_indoor, T_outdoor, area_m2, R_value… | heat_loss_rate, daily_energy_kwh, daily_cost… | 4 |
| `hive_thermal_balance` | cottage | N_bees, q_per_bee, activity_factor, T_outside… | Q_bees, Q_loss, steady_state_temp… | 4 |
| `pipe_freezing` | cottage | T_outdoor, T_water, pipe_length_m, pipe_diameter_m… | heat_loss_rate, time_to_freeze_hours | 2 |
| `heat_pump_cop` | home | T_indoor, T_outdoor, efficiency_factor, building_ua… | carnot_cop, real_cop, heating_power_w… | 5 |

## Open gaps from production

- Unresolved queries matching this cell's vocabulary: **0**
- Total HOT queries in campaign: 33001

## Teaching protocol reminder

This is the ONLY context you see. Propose 1-3 new solvers OR 1 improvement. Return YAML matching the schema of axiom files in configs/axioms/<domain>/*.yaml. Tests REQUIRED. The quality gate (tools/propose_solver.py) will verify determinism, contradictions with existing solvers, and insight score before merging. Duplicates rejected by hash.

## Schema reference

New solvers must be YAML matching this shape (see
`configs/axioms/cottage/honey_yield.yaml` for a complete example):

```yaml
model_id: <unique_snake_case>
model_name: "<Human Readable Name>"
description: "<one sentence>"
formulas:
  - name: <formula_name>
    formula: "<python-expression>"
    description: "<what it computes>"
    output_unit: "<unit>"
variables:
  <var_name>:
    description: "<description>"
    unit: "<unit>"
    range: [<min>, <max>]
    default: <default>
```
