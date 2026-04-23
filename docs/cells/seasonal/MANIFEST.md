# Cell manifest — `seasonal`

**Generated:** 2026-04-23T02:35:44+00:00
**Solver count:** 0
**Gap score:** 0.625  *(0 = cell saturated, 1 = cell mostly empty or failing)*
**Siblings (ring-1):** general, learning, thermal

## Existing solvers

*(none — cell is empty; high-priority for teaching)*

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
