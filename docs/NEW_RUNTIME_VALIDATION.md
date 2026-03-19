# New Runtime Validation

This document describes the shadow comparison and benchmark tooling for validating the new hexagonal runtime against the legacy runtime.

## Shadow Compare Tool

`tools/runtime_shadow_compare.py` runs queries through both old and new runtimes simultaneously and compares results.

### Modes

| Mode | Description |
|------|-------------|
| `shadow` | Run both, compare results, new runtime output is discarded |
| `canary` | 10% of traffic goes to new runtime, 90% to old |
| `validate` | Run a predefined test corpus through both |

### Usage

```python
from tools.runtime_shadow_compare import RuntimeShadowCompare, CompareRequest

compare = RuntimeShadowCompare(mode="validate")
report = await compare.run_corpus(queries, old_handler, new_handler)
compare.save_report(report, "data/shadow_report.json")
```

### Report Fields

- `total_requests`: number of queries processed
- `route_matches`: queries where both runtimes chose the same route
- `response_matches`: queries with identical responses
- `route_match_rate`: fraction of matching routes
- `route_distribution`: count per route type

## Benchmark Harness

`tools/benchmark_harness.py` runs predefined queries and measures performance.

### Predefined Queries

Located in `configs/benchmarks.yaml`, organized by domain:

- **domain_qa** — domain-specific queries (monitoring, diagnostics, scheduling)
- **bilingual** — Finnish and English queries
- **correction** — testing correction recovery
- **sensor** — temperature and humidity queries
- **fallback** — unknown topic handling

### Usage

```python
from tools.benchmark_harness import BenchmarkHarness

harness = BenchmarkHarness.from_yaml("configs/benchmarks.yaml")
report = await harness.run(handler)
print(f"Pass rate: {report.pass_rate():.0%}")
harness.save_report(report, "data/benchmark.json")
```

### Before/After Comparison

```python
diff = BenchmarkHarness.compare(before_report, after_report)
print(f"Pass rate delta: {diff['pass_rate_delta']:+.1%}")
print(f"Latency delta: {diff['latency_delta_ms']:+.0f}ms")
```

## Validation Procedure

1. Run benchmark against current production
2. Deploy new runtime in shadow mode
3. Run benchmark against shadow pair
4. Compare reports
5. If pass_rate_delta >= 0 and latency_delta <= +100ms, promote
