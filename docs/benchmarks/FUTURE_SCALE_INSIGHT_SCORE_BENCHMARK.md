# Future Scale Insight Score Benchmark

Status: local/offline artifact producer for the `insight_score` axis.

This benchmark defines and produces a versioned, offline, deterministic
`insight_score` benchmark artifact on the `future_scale` axis. The producer uses
the existing dream-mode `compute_insight_score` helper over fixed synthetic
outcome fixtures. It does not touch production dream sessions, mutate runtime
routing, update authority, call a network, pull models, or make a future-state
claim.

## Scope

The contract and producer surface is intentionally small:

- `tools/run_future_scale_insight_bench.py`
- `tests/tools/test_future_scale_insight_score_benchmark.py`
- `schemas/future_scale_insight_score_benchmark.v1.json`
- `tests/contracts/test_future_scale_insight_score_benchmark_schema.py`
- `docs/benchmarks/FUTURE_SCALE_INSIGHT_SCORE_BENCHMARK.md`

The WD Image #1 manifest consumes only the producer's sanitized aggregate
sample and keeps `claim_gate_satisfied=false`.

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
- raw model names, provider IDs, Hugging Face style identifiers, secrets, bearer
  tokens, and local filesystem paths in any scalar string;
- extra properties, malformed payloads, wrong scopes, and wrong numeric types.

The leak and finite-number walk lives in
`tools/future_scale_contract_safety.py`. The producer validates the artifact
against the schema, exact-false gates, finite-number policy, and scalar leak
walk before printing or writing any explicit `--out-dir` artifacts.

The three repro-oriented scalar fields are also positive allowlists in the
schema:

- `source_branch` must match the stable alias grammar
  `^[a-z][a-z0-9._-]{0,80}$`;
- `deterministic_seed` must match
  `^insight-bench-[0-9]{8}-seed-[0-9a-f]{6,}$`;
- `reproduce_command` must match the fixed offline template
  `python tools/run_future_scale_insight_bench.py --corpus <alias> --offline --deterministic`,
  where `<alias>` uses the same stable alias grammar.
- `not_claimed` is a fixed three-item disclaimer enum, not an open free-text
  notes field.

## Relation To WD

`insight_score` is the dream-mode projected value signal for candidate solver
trajectories. In this contract it is only a local benchmark signal over a
synthetic adversarial corpus alias. It is not a live production usefulness
metric and does not prove future safety, infinite scalability, or autonomous
runtime improvement.

The artifact is useful because it gives the `insight_score` future-scale axis a
strict, reviewable producer:

- composite-path evidence is covered by the landed `useful_composite_paths`
  benchmark;
- contradiction evidence is covered by the landed `contradiction_rate`
  benchmark;
- this producer covers local deterministic `insight_score` artifact generation.

Manifest aggregation now treats this as benchmark-contract evidence only. It
still does not satisfy required production runtime evidence.

## Reproduction

Run the producer from the repository root:

```bash
python tools/run_future_scale_insight_bench.py --corpus v12.a3.synth_adversarial.v0 --offline --deterministic --json
```

Run the producer and schema tests:

```bash
python -m pytest tests/tools/test_future_scale_insight_score_benchmark.py tests/contracts/test_future_scale_insight_score_benchmark_schema.py -q
```

## Limits

This slice produces local deterministic benchmark results only. It does not
claim a production trend, runtime coverage, production corpus binding,
receipt-bound execution, future safety, infinite scalability, or autonomous
runtime improvement.
