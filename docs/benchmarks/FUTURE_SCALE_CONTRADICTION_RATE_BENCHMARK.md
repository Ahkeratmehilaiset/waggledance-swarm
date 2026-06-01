# Future-Scale Contradiction Rate Benchmark

This document records the Slice 2 benchmark artifact for the
`contradiction_rate` axis from `docs/architecture/HONEYCOMB_SOLVER_SCALING.md`.
The axis is defined there as the fraction of proposals rejected because they
conflict with an existing in-cell solver.

## Scope

The benchmark is a local deterministic fixture harness:

- Runner: `tools/run_future_scale_contradiction_rate_benchmark.py`
- Tests: `tests/tools/test_future_scale_contradiction_rate_benchmark.py`
- JSON artifact name: `future_scale_contradiction_rate_benchmark.json`
- Markdown artifact name: `future_scale_contradiction_rate_benchmark.md`

It exercises the existing proposal-gate path in `tools/propose_solver.py` with
three synthetic local fixtures:

| Fixture | Expected result |
|---|---|
| same-cell opposing invariant | `REJECT_CONTRADICTION` |
| same-cell compatible invariant | accepted by the proposal gate |
| opposing invariant in another cell | accepted by the proposal gate |

The resulting fixture rate is intentionally small and local-only. It is not a
production baseline.

## Claim Guardrails

The artifact must keep these fields false:

- `claim_gate_satisfied`
- `claim_safe`
- `literal_future_claim_safe`
- `runtime_authority_changed`
- `runtime_authority_granted`
- `controls_present`
- `operator_gate_required`
- `external_writes_applied`

The measurement label is `MEASURED_LOCAL_ONLY`; it must not be upgraded to a
proven or safe claim by this slice. Shared manifest aggregation is intentionally
deferred to a later serialized change.

## Reproduce

```powershell
python tools/run_future_scale_contradiction_rate_benchmark.py --out-dir .codex-audit/future-scale-contradiction-rate --now 2026-06-01T20:55:00Z --json
```

Expected local contract:

- `ok = true`
- `benchmark_result.proposal_count = 3`
- `benchmark_result.contradiction_rejections = 1`
- `benchmark_result.false_positive_count = 0`
- `benchmark_result.false_negative_count = 0`
- all claim and authority fields remain false

## Forge Coverage

The focused test suite mutates the generated report to verify fail-closed
behavior for:

- truthy non-bool claim gates such as `"false"` and `0`
- non-finite numeric benchmark fields
- claim-label upgrades such as `PROVEN`
- malformed case-level booleans
- accidental absolute path or secret-like string leakage in emitted artifacts
