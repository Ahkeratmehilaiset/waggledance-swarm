# Future Scale Latency Benchmark

Status: Option A round 2, latency contract plus deterministic local producer.

This slice defines a versioned, offline, deterministic contract for a
`latency` benchmark artifact on the `future_scale` axis. The follow-up
producer emits that artifact from deterministic local latency fixtures. It
does not touch the image capability manifest, does not read production metrics,
and does not aggregate the metric into any runtime claim.

## Scope

The latency contract surface is intentionally small:

- `schemas/future_scale_latency_benchmark.v1.json`
- `tools/run_future_scale_latency_bench.py`
- `tests/contracts/test_future_scale_latency_benchmark_schema.py`
- `tests/tools/test_future_scale_latency_benchmark.py`
- `tools/future_scale_contract_safety.py`
- `docs/benchmarks/FUTURE_SCALE_LATENCY_BENCHMARK.md`

The schema and contract test define the latency artifact. The producer builds
the artifact from an embedded, deterministic fixture set and validates the
result through the same schema and shared scalar-safety checks before printing
or writing it. The shared safety utility is imported by this contract and by
sibling future-scale validators so provider/model/path scalar checks do not
drift across slices.

## Safety Contract

The artifact carries no runtime or future-scaling authority:

- `claim_gate_satisfied=false`
- `claim_safe=false`
- `literal_future_claim_safe=false`
- `controls_present=false`
- `runtime_authority_granted=false`
- `external_writes_applied=false`
- `required_runtime_evidence_present=false`
- `measurement_scope=local`
- `no_cloud_api_calls=true`
- `no_model_pull_or_download=true`

The executable contract also rejects:

- any claim-gate upgrade or type confusion such as `"false"`;
- non-finite numeric values such as `NaN`, `Infinity`, and `-Infinity`;
- negative latency measurements in p50/p95/p99, aggregate latency, and control
  latency fields;
- raw model names, provider IDs, Hugging Face style identifiers, secrets, bearer
  tokens, and local filesystem paths in any scalar string;
- extra properties, malformed payloads, wrong scopes, and wrong numeric types.

The leak and finite-number walk lives in
`tools/future_scale_contract_safety.py`. Producer PRs should import that module
instead of copying an equivalent validator.

The three repro-oriented scalar fields are also positive allowlists in the
schema:

- `source_branch` must match the stable alias grammar
  `^[a-z][a-z0-9._-]{0,80}$`;
- `deterministic_seed` must match
  `^latency-bench-[0-9]{8}-seed-[0-9a-f]{6,}$`;
- `reproduce_command` must match the fixed offline template
  `python tools/run_future_scale_latency_bench.py --fixtures <alias> --offline --deterministic`,
  where `<alias>` uses the same stable alias grammar.
- `not_claimed` is a fixed three-item disclaimer enum, not an open free-text
  notes field.

Latency metrics are constrained to use the existing
`waggledance_route_stage_request_latency_histogram_ms` (and child
_bucket / _sum / _count) names via enum fields in the schema. Stage aliases
are allowlisted with the same stable grammar used for solvers/corpus in the
insight slice. The executable walk rejects raw model names, provider IDs, and
path-like strings that still satisfy the stable alias grammar.

## Relation To WD

`route_latency_ms` (p50/p95/p99) is one of the measurable proxies for the
future swarm scalability axis in HONEYCOMB_SOLVER_SCALING.md. In this contract
it is only a local benchmark signal over a synthetic offline fixtures alias
for the stages instrumented by `waggledance_route_stage_request_latency_histogram_ms`.
It is not a live production usefulness metric and does not prove future safety,
infinite scalability, or autonomous runtime latency improvement.

The artifact is useful because it gives the latency future-scale axis a
strict, reviewable contract (sibling to composite-path, contradiction-rate,
and insight_score).

Manifest aggregation is a later serialized round after all contracts have
landed independently.

## Producer

Run the deterministic producer from the repository root:

```bash
python tools/run_future_scale_latency_bench.py --fixtures v3.latency_fixtures.local.v1 --offline --deterministic --json
```

The `--offline --deterministic` flags are required. The default embedded
fixture set measures stable aliases only:

- `language_detection`
- `hot_cache`
- `deterministic_solver`
- `hybrid_retrieval_8_cell`

With `--out-dir <dir>`, the tool writes only:

- `future_scale_latency_benchmark.json`
- `future_scale_latency_benchmark.md`

The output records the fixture alias and digest, but not the local output
directory or any production path.

## Reproduction

Run the contract test from the repository root:

```bash
python -m pytest tests/contracts/test_future_scale_latency_benchmark_schema.py -q
```

Run the focused producer tests:

```bash
python -m pytest tests/tools/test_future_scale_latency_benchmark.py -q
```

## Limits

This slice produces only local fixture benchmark results. It does not claim a
trend, runtime coverage, production corpus binding, receipt-bound execution, or
future-scaling safety. Production latency evidence still needs a separately
reviewed capture-window/import path before any runtime claim can change.
