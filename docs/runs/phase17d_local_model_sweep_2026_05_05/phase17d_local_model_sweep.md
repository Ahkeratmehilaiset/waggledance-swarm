# Phase 17D - Local Ollama Multi-Model Sweep

**Benchmark version:** phase17d.v1
**Git SHA:** 4f8a9ea7774a9f4c862c8342dcc69ef714386b8f
**Python:** 3.13.7
**Platform:** Windows-11-10.0.22631-SP0
**Started UTC:** 2026-05-05T05:08:50Z
**Finished UTC:** 2026-05-05T05:17:03Z
**Duration (s):** 492.0913

## Policy declarations

**No cloud API calls were made.**
**No model was pulled or downloaded.**
**No cross-vendor ranking is implied.** Every per-model number is
reported in isolation as a measurement of that exact local model on
this exact prompt set on this exact host. Multi-model presentation is
side-by-side, not ordered.

## Selected models

* `gemma4:e4b`
* `gemma3:4b`
* `llama3.2:3b`
* `phi4-mini:latest`

## Deferred (too large by default)

* `gemma4:26b` - present locally, NOT exercised (size > 10 GB threshold; --prefer-larger-models to opt in)
* `qwen2.5:32b` - present locally, NOT exercised (size > 10 GB threshold; --prefer-larger-models to opt in)
* `osoderholm/poro:latest` - present locally, NOT exercised (size > 10 GB threshold; --prefer-larger-models to opt in)

## Per-model summary

Reported in selection order, not rank order:

| model | repeats | prompts ok | min ms | p50 ms | p95 ms | p99 ms | mean ms | stddev ms | CoV | claim |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `gemma4:e4b` | 3 | 90/90 | 700.70 | 784.70 | 18328.70 | 20415.80 | 2661.17 | 4888.19 | 0.03 | `MEASURED-FOR-THIS-MODEL-AND-PROMPT-SET` |
| `gemma3:4b` | 3 | 90/90 | 665.20 | 711.20 | 863.40 | 4777.10 | 776.00 | 427.89 | 0.00 | `MEASURED-FOR-THIS-MODEL-AND-PROMPT-SET` |
| `llama3.2:3b` | 3 | 90/90 | 452.50 | 526.60 | 2958.50 | 4072.50 | 986.19 | 876.76 | 0.00 | `MEASURED-FOR-THIS-MODEL-AND-PROMPT-SET` |
| `phi4-mini:latest` | 3 | 90/90 | 471.70 | 549.30 | 3411.20 | 6364.90 | 1038.72 | 1170.72 | 0.01 | `MEASURED-FOR-THIS-MODEL-AND-PROMPT-SET` |

## Claim labels

* `ollama_local_baseline`: **MEASURED-LOCAL-OLLAMA-PANEL**
* `competitive_evidence_axis_J`: **MEASURED-LOCAL-OLLAMA-PANEL**
* `no_cross_model_ranking`: **True**
* `no_cross_vendor_ranking`: **True**
* `no_cloud_api_comparison`: **True**
* `raw_intelligence_vs_frontier_moe`: **NOT_CLAIMED**

## What this measures

* Wall-clock latency of one local Ollama daemon answering a fixed
  30-prompt deterministic manifest, repeated R times, across N
  already-installed local models.
* Repeatability via per-repeat median latency mean + stddev +
  coefficient of variation. CoV close to 0 = stable; CoV > 0.3 =
  noisy on this host.

## What this does NOT measure

* Output correctness against ground truth. The harness only checks
  that each prompt produced a non-empty stdout with exit code 0.
* Cross-vendor or cross-architecture ranking. Different models have
  different parameter counts, training data, and quantizations; the
  numbers below are not directly comparable as 'which model is
  better'.
* Cloud LLM endpoints. Those slots remain documented NOT_RUN.

## Release gate

* `release_gate_pass`: **True**
* `forbidden_claims_absent`: **True**
* `provider_jobs_delta`: 0
* `builder_jobs_delta`: 0

