# V12 WriteRCOGate Artifact Adapter

Date: 2026-05-20
Agent: Codex
Task: `v12-write-rco-artifact-adapter-2026-05-20`

## Scope

This slice adds an opt-in adapter:

- `build_rco_decision_artifact_for_gate(intent, outcome, ...)`

It lives in:

- `waggledance/core/v3_13_0/write_rco_gate.py`

Tests were added in:

- `tests/v3_13_0/test_write_rco_gate.py`

The adapter converts a `WriteRCOGate` `Intent + GateOutcome` pair into the new
`magma.rco_decision_artifact.v0` shape.

## Non-Changes

This does not change:

- `WriteRCOGate.route`
- `WriteRCOGate.execute`
- audit emission behavior
- DB schemas
- MAGMA receipt writing
- external-effect execution

The adapter is explicit and caller-driven.

## Behavior

The adapter:

- hashes the full intent summary into `intent_digest`
- hashes `intent.payload` into `write_payload_digest`
- maps approved outcomes to `gate_decision=allow`
- maps policy denials to `gate_decision=refuse`
- maps stop conditions to `gate_decision=review`
- sets `operator_required=True` for `external_effect`
- carries sanitized audit event refs
- emits no raw payload in the artifact body

## Verification

Command:

```powershell
.\.venv\Scripts\python.exe -m pytest --basetemp .\temp\pytest-v12-write-rco-adapter tests\v3_13_0\test_write_rco_gate.py tests\contracts\test_rco_decision_artifact_schema.py tests\tools\test_rco_receipt_binding_demo.py
```

Result:

```text
97 passed
```

Additional checks:

- `compileall` passed for touched Python files
- `git diff --check` passed

## Result

The V12 evidence chain now has a progression:

1. RCO decision artifact schema/helper
2. RCO decision artifact receipt-binding demo
3. WriteRCOGate opt-in adapter producing the artifact from real gate data

The next safe slice is an opt-in receipt bundle builder for one
`WriteRCOGate.route` test path. That should still avoid automatic runtime
emission until operator policy confirms where receipt bundles should be stored.
