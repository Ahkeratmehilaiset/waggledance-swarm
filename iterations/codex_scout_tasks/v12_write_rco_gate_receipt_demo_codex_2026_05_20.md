# V12 WriteRCOGate Receipt Route Demo

Date: 2026-05-20
Agent: Codex
Task: `v12-write-rco-receipt-route-demo-2026-05-20`

## Scope

This slice adds a CLI-only proof path:

- `tools/run_write_rco_gate_receipt_demo.py`
- `tests/tools/test_write_rco_gate_receipt_demo.py`

The demo runs a real `WriteRCOGate.route()` local-artifact path, converts its
`Intent + GateOutcome` into `magma.rco_decision_artifact.v0`, and binds that
artifact into a MAGMA receipt bundle.

## Safety Boundary

The demo:

- requires explicit `--out-dir`
- refuses an existing output directory
- does not call `WriteRCOGate.execute()`
- does not write DB rows
- does not call external systems
- does not change runtime auto-emission behavior

## Emitted Bundle

The output directory contains:

- `intent-001.json`
- `rco-decision-001.json`
- `evaluation-001.json`
- `receipt-001.json`
- `audit-events.json`
- `manifest.json`

The binding verifier checks:

- base MAGMA receipt manifest verification
- `receipt.rco_decision_digest == sha256(rco-decision-001.json)`
- `receipt.risk_class == rco_decision.risk_class`
- `evaluation.actual_gate == rco_decision.gate_decision`

## Verification

Command:

```powershell
.\.venv\Scripts\python.exe -m pytest --basetemp .\temp\pytest-v12-write-rco-route-demo tests\tools\test_write_rco_gate_receipt_demo.py tests\v3_13_0\test_write_rco_gate.py tests\tools\test_rco_receipt_binding_demo.py
```

Result:

```text
93 passed
```

Smoke command:

```powershell
.\.venv\Scripts\python.exe tools\run_write_rco_gate_receipt_demo.py --out-dir temp\write-rco-demo-smoke --json
```

Observed:

```json
{
  "audit_event_count": 2,
  "binding_report": {
    "errors": [],
    "ok": true,
    "rco_artifact_count": 1,
    "receipt_count": 1
  },
  "gate_outcome": {
    "approved": true,
    "risk_class": "local_artifact"
  },
  "writes_applied": false
}
```

## Result

This closes the gap between a synthetic RCO receipt demo and a real
`WriteRCOGate.route()` decision. The next step should be policy/storage
selection before automatic runtime receipt emission is considered.
