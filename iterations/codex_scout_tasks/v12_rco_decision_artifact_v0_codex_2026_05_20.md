# V12 RCO Decision Artifact v0

Date: 2026-05-20
Agent: Codex
Task: `v12-rco-decision-artifact-v0-2026-05-20`

## Scope

This slice adds a payload-free RCO decision artifact and a local receipt-binding
demo. It does not change `WriteRCOGate` runtime behavior, DB schemas,
promotion logic, or any external-effect execution path.

Files:

- `schemas/v3_13_0/rco_decision_artifact.v0.json`
- `waggledance/core/magma/rco_decision_artifact.py`
- `tools/run_rco_receipt_binding_demo.py`
- `tests/contracts/test_rco_decision_artifact_schema.py`
- `tests/tools/test_rco_receipt_binding_demo.py`

## What It Proves

The new artifact gives `magma.receipt.v1.rco_decision_digest` a concrete,
schema-valid target:

- `intent_digest`
- `write_payload_digest`
- `risk_class`
- `gate_decision`
- `approved`
- `operator_required`
- `policy_version`
- `charter_version`
- `scope_policy_decision`
- `peer_rco_verdict`
- `reason_codes`
- `audit_event_ids`
- `stop_condition`

The artifact intentionally does not store raw payloads.

## Demo

Command:

```powershell
.\.venv\Scripts\python.exe tools\run_rco_receipt_binding_demo.py --out-dir temp\rco-demo --json
```

The demo writes:

- `intent-001.json`
- `rco-decision-001.json`
- `evaluation-001.json`
- `receipt-001.json`
- `manifest.json`

The demo verifies:

1. normal MAGMA receipt manifest verification passes
2. `receipt.rco_decision_digest == sha256(rco-decision-001.json)`
3. `receipt.risk_class == rco_decision.risk_class`
4. `evaluation.actual_gate == rco_decision.gate_decision`

## Verification

Command:

```powershell
.\.venv\Scripts\python.exe -m pytest --basetemp .\temp\pytest-v12-rco tests\contracts\test_rco_decision_artifact_schema.py tests\tools\test_rco_receipt_binding_demo.py
```

Result:

```text
12 passed
```

Additional checks:

- `compileall` passed for the new Python files/tests
- `git diff --check` passed for the new files

## Tamper Regression

`tests/tools/test_rco_receipt_binding_demo.py` mutates
`rco-decision-001.json` after receipt emission. The binding verifier then
fails with:

- `rco_decision_digest mismatch`
- `evaluation actual_gate does not match RCO artifact`

This closes the immediate evidence gap from the adoption audit: the receipt can
now bind a concrete RCO decision artifact, not only an opaque caller-provided
digest.

## Remaining Gap

This is still a demo/helper slice. `WriteRCOGate` itself is not yet emitting
these artifacts on the live route path. The next implementation slice should
wire this helper into a feature-flagged or explicit opt-in WriteRCOGate path,
then require a receipt bundle for at least one real gate decision test.
