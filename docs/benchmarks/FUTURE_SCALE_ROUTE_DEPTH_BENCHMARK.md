# Future-Scale Route Depth Benchmark

Status: local/offline artifact producer for the `route_depth` axis.

`tools/run_future_scale_route_depth_benchmark.py` emits deterministic JSON and
Markdown artifacts with schema version `future_scale_route_depth_benchmark.v1`.
The tool measures only static route-stage trace fixtures after passing them
through `waggledance.adapters.http.routes.chat._sanitize_route_stage_trace`. It
does not call ChatService, network services, providers, live runtime routes, or
the `/metrics` endpoint.

## Scope

The benchmark is a local deterministic fixture harness:

- Runner: `tools/run_future_scale_route_depth_benchmark.py`
- Tests: `tests/tools/test_future_scale_route_depth_benchmark.py`
- JSON artifact name: `future_scale_route_depth_benchmark.json`
- Markdown artifact name: `future_scale_route_depth_benchmark.md`

It measures four sanitized fixture traces:

| Fixture | Route depth |
|---|---:|
| cache hit short route | `2` |
| deterministic solver route | `5` |
| hybrid retrieval then fallback route | `6` |
| hex neighbor assist long route | `7` |

The artifact reports nearest-rank `p50`, `p95`, and `p99` over those local
fixture depths. This is not a production baseline and not a runtime route-depth
histogram.

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
proven or safe claim by this slice. `no_cloud_api_calls=true` and
`no_model_pull_or_download=true` are explicit positive guardrails.

The WD Image1 manifest may bind this artifact as local benchmark evidence for
`route_depth`, but that binding still leaves the literal future-scale claim
unsafe. Production route-depth histograms, time-window baselines, and load
benchmark evidence remain required before stronger wording is safe.

## Reproduce

```powershell
python tools/run_future_scale_route_depth_benchmark.py --out-dir .codex-audit/future-scale-route-depth --now 2026-06-03T00:00:00Z --json
```

Expected local contract:

- `ok = true`
- `benchmark_result.trace_count = 4`
- `benchmark_result.route_depth_values = [2, 5, 6, 7]`
- `benchmark_result.p50_route_depth = 5`
- `benchmark_result.p95_route_depth = 7`
- `benchmark_result.p99_route_depth = 7`
- `benchmark_result.runtime_route_depth_histogram_exported = false`
- all claim and authority fields remain false

## Forge Coverage

The focused test suite mutates the generated report to verify fail-closed
behavior for:

- truthy non-bool claim gates such as `"false"` and `0`
- non-finite numeric benchmark fields
- claim-label upgrades such as `PROVEN`
- malformed route-depth counts, histograms, and percentiles
- provider/model-like strings and secret/path-like strings in scalar output
- accidental absolute path, bearer-token, or private marker leakage in emitted
  artifacts
