# V12 MAGMA Receipt Adoption Report

Date: 2026-05-20
Agent: Codex
Task: `v12-magma-receipt-adoption-report-2026-05-20`

## Scope

This slice adds a read-only static scanner:

- `tools/magma_receipt_adoption_report.py`
- `tests/tools/test_magma_receipt_adoption_report.py`

It does not change runtime behavior, schemas, DB writes, RCO decisions, or
promotion logic. It only reports whether selected critical paths contain direct
MAGMA receipt/evaluation/bundle hooks.

## Verification

Command:

```powershell
.\.venv\Scripts\python.exe -m pytest --basetemp .\temp\pytest-v12-adoption tests\tools\test_magma_receipt_adoption_report.py
```

Result:

```text
3 passed
```

## Current Main Adoption Snapshot

Command:

```powershell
.\.venv\Scripts\python.exe tools\magma_receipt_adoption_report.py --markdown
```

Observed status:

| status | criticality | path | label |
| --- | --- | --- | --- |
| not_receipt_bound | high | `waggledance/core/v3_13_0/write_rco_gate.py` | WriteRCOGate action authority |
| not_receipt_bound | high | `waggledance/core/autonomy_growth/auto_promotion_engine.py` | Autogrowth auto-promotion |
| magma_event_only | high | `waggledance/core/v3_13_0/solver_provenance.py` | Solver provenance |
| magma_event_only | medium | `waggledance/core/autonomy/runtime.py` | Autonomy runtime MAGMA append path |
| receipt_bound | medium | `tools/run_pdam_counterfactual_demo.py` | PDAM counterfactual demo |
| receipt_bound | medium | `tools/run_magma_composition_demo.py` | MAGMA composition demo |
| evaluation_only | medium | `tools/run_magma_adversarial_eval.py` | MAGMA adversarial eval |

Summary counts:

- `receipt_bound`: 2
- `evaluation_only`: 1
- `magma_event_only`: 2
- `not_receipt_bound`: 2
- high-criticality gaps: 3

## Interpretation

WD already has a real MAGMA receipt thin spine, but current adoption is still
concentrated in demos and evaluation surfaces.

Safe claim:

- WD has receipt-bound counterfactual/composition demo paths.
- WD can statically measure receipt adoption across critical paths.

Unsafe claim:

- WriteRCOGate decisions are receipt-bound.
- Autogrowth promotions are receipt-bound.
- Solver provenance is fully receipt-bound rather than MAGMA-event-only.
- Every authority path emits a verifier-checkable receipt bundle.

## Recommended Next Slice

The next implementation slice should be one high-criticality binding, not a
new broad abstraction:

1. Add an RCO decision artifact v0 that can be digested independently.
2. Bind `WriteRCOGate` decisions to MAGMA receipt v1 in a feature-flagged or
   explicit demo path first.
3. Add a regression where a tampered RCO decision artifact breaks receipt
   verification.

This is the shortest path from "MAGMA demo evidence" toward the stronger V12
claim: verifiable solver-growth substrate.
