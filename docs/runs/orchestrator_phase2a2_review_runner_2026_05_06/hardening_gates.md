# P8 -- Hardening gate driver

## Files added

| File | Purpose |
|---|---|
| `orchestrator/Run-WaggleHardeningGates.ps1` | Sequential driver for the 8 Phase 2A-1 + Phase 2A-2 gates. Stops on first failure (override with `-ContinueOnFailure`). Writes `hardening_gates.json` summary. PS 5.1 robust. |
| `orchestrator/Test-Phase2A2.ps1` | 53 integration assertions: required files, schema parse, schema enums, review config safe-profile keys, normal smoke still requires unique artifact, ReviewAdapter parses known-good stdout, dry-run works, no token patterns in committed templates, gitignore unignore policy works. |

## Run

```
powershell -NoProfile -ExecutionPolicy Bypass -File ".\orchestrator\Run-WaggleHardeningGates.ps1"
```

## Result

| Gate | Status | Seconds |
|---|---|---|
| Test-Syntax            | PASS | 0.85 |
| Test-Redaction         | PASS | 1.02 |
| Test-Redactor          | PASS | 0.95 |
| Test-SmokeValidation   | PASS | 1.02 |
| Test-ReviewSchema      | PASS | 1.24 |
| Test-ReviewAdapter     | PASS | 1.46 |
| Test-ReviewRunner      | PASS | 8.03 |
| Test-Phase2A2          | PASS | 1.86 |

**OVERALL: PASS (8/8 gates green)**

JSON summary: `hardening_gates.json` (in this run dir).

Test counts:

- Test-Syntax            -> 43/43 files parse clean
- Test-Redaction         -> 27/27 tests
- Test-Redactor          -> 26/26 tests
- Test-SmokeValidation   -> 16/16 tests
- Test-ReviewSchema      -> 16/16 tests
- Test-ReviewAdapter     -> 38/38 tests
- Test-ReviewRunner      -> 69/69 tests
- Test-Phase2A2          -> 53/53 tests

Total: **288/288 tests** across the 8 hardening gates.

P8 PASS.
