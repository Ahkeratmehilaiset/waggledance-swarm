# Future-Scale Route Depth Benchmark

This document records the benchmark artifact contract for the `route_depth`
axis from `docs/architecture/HONEYCOMB_SOLVER_SCALING.md`. The axis is defined
there as the median number of hops from query to final answer.

## Scope

The benchmark is a local deterministic fixture harness:

- Runner: `tools/run_future_scale_route_depth_benchmark.py`
- Tests: `tests/tools/test_future_scale_route_depth_benchmark.py`
- JSON artifact name: `future_scale_route_depth_benchmark.json`
- Markdown artifact name: `future_scale_route_depth_benchmark.md`

It measures route depth as the count of allowlisted stage names in sanitized
`route_stage_trace` fixtures. The fixtures cover representative local routes:
hot-cache hit, deterministic solver answer, authoritative hybrid answer,
hex-neighbor answer, and orchestrator fallback answer.

This is not a production baseline. It does not read raw query text, route
payloads, arbitrary local paths, provider names, or model IDs. The emitted
source metadata is limited to an allowlisted set of repo-relative provenance
paths; injected path-like scalars are contract violations.
The fixture traces are sanitized through
`waggledance.adapters.http.routes.chat._sanitize_route_stage_trace` before the
stage sequence is counted.
The report validator also imports `tools.future_scale_contract_safety` so
provider/model aliases, secrets, non-finite numbers, and disallowed path-like
scalars are checked with the same policy as sibling future-scale contracts.

## Claim Guardrails

The artifact must keep these fields false:

- `claim_gate_satisfied`
- `claim_safe`
- `literal_future_claim_safe`
- `required_runtime_evidence_present`
- `runtime_authority_changed`
- `runtime_authority_granted`
- `controls_present`
- `operator_gate_required`
- `external_writes_applied`

The measurement label is `MEASURED_LOCAL_ONLY`; it must not be upgraded to a
proven or safe claim by this slice. Runtime route-depth histograms, production
trace windows, and trend baselines remain separate future work.

## Reproduce

```powershell
python tools/run_future_scale_route_depth_benchmark.py --out-dir .codex-audit/future-scale-route-depth --now 2026-06-02T20:55:00Z --json
```

Expected local contract:

- `ok = true`
- `benchmark_result.sample_count = 5`
- `benchmark_result.min_depth = 2`
- `benchmark_result.max_depth = 8`
- `benchmark_result.p50_depth = 6.0`
- `benchmark_result.p95_depth = 7.8`
- `benchmark_result.p99_depth = 7.96`
- all claim and authority fields remain false

## Forge Coverage

The focused test suite mutates the generated report to verify fail-closed
behavior for:

- truthy non-bool claim gates such as `"false"` and `0`
- non-finite numeric benchmark fields
- malformed case-level depth fields
- inconsistent depth histograms
- accidental absolute path, traversal, or secret-like string leakage
- unknown route-stage and private trace fields being ignored before export
