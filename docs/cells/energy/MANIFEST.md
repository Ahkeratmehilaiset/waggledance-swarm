# Cell manifest — `energy`

**Generated:** 2026-04-23T02:35:43+00:00
**Solver count:** 3
**Gap score:** 0.000  *(0 = cell saturated, 1 = cell mostly empty or failing)*
**Siblings (ring-1):** math, safety, thermal

## Existing solvers

| model_id | domain | inputs | outputs | formulas |
|---|---|---|---|---|
| `solar_yield` | cottage | panel_area_m2, panel_efficiency, sun_hours, cell_temp | temperature_derating, peak_power_w, daily_yield_kwh | 3 |
| `battery_discharge` | gadget | capacity_mah, I_active, I_sleep, duty_cycle | battery_life_hours | 1 |
| `comfort_energy_tradeoff` | home | T_day, T_night, T_outdoor, building_ua… | energy_saved_percent, cost_saved_per_night | 2 |

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
