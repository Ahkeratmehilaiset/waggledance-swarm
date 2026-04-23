# Cell manifest — `safety`

**Generated:** 2026-04-23T02:35:43+00:00
**Solver count:** 2
**Gap score:** 0.000  *(0 = cell saturated, 1 = cell mostly empty or failing)*
**Siblings (ring-1):** energy, general, system, thermal

## Existing solvers

| model_id | domain | inputs | outputs | formulas |
|---|---|---|---|---|
| `swarm_risk` | cottage | empty_combs, total_combs, queen_age_years, queen_cells… | space_pressure, queen_age_factor, base_swarm_score… | 4 |
| `varroa_treatment` | cottage | combs_with_bees, dose_per_comb_ml, varroa_before, colony_strength… | oxalic_acid_dose_ml, varroa_after_treatment, days_to_reinfestation… | 4 |

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
