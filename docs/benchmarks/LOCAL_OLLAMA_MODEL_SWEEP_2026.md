# Local Ollama Model Sweep — 2026-Q2

**Status:** Phase 17D snapshot, derived from this session's reproducible artifact only.
**Date:** 2026-05-05
**Branch:** `phase17d/local-model-sweep`
**Anchor:** `v3.9.3-local-model-sweep-alpha` candidate (PRERELEASE only). v3.8.0 remains GitHub Latest. v3.9.0-producer-fabric-alpha, v3.9.1-local-efficiency-benchmark-alpha, v3.9.2-local-ollama-baseline-alpha all remain Pre-release.

This document publishes the raw measured numbers produced by `tools/run_phase17d_local_model_sweep.py` on `phase17d/local-model-sweep`. It is an **engineering** record. It does not assert any model is faster, better, or more correct than any other model. The harness measures wall-clock latency and parse-success rate per model, side-by-side, never with rank ordering.

WaggleDance does **not** claim to be conscious, sentient, aware, alive, or AGI. The autonomy mechanisms in this release are bounded engineering primitives.

## Reproduce

```
git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git
cd waggledance-swarm
git checkout v3.9.3-local-model-sweep-alpha   # or stay on main
pip install -r requirements-ci.txt
# Ollama must already be installed locally with at least 2 of the rule-14
# preferred models present. The harness never pulls a model and never
# calls a cloud API. Subprocess output is scanned for pull/download
# substrings; a hit aborts the harness immediately.
python tools/run_phase17d_local_model_sweep.py
```

## What the harness measures

A panel of N (default 4) already-installed local Ollama models, each driven through R repeats (default 3) of a P-prompt deterministic manifest (default 30 prompts, imported verbatim from the Phase 17C harness).

| component | reused from | what it measures |
|---|---|---|
| 30-prompt manifest | Phase 17C (`PROBE_PROMPTS`) | 5 prompts × 6 low-risk allowlist families, factoid-style |
| Bytes-mode subprocess + UTF-8 errors=replace | Phase 17C (`_decode_safely`) | resilient to non-cp1252 model output |
| Pull/download abort gate | new in Phase 17D | scrubs every Ollama subprocess stdout+stderr for pull substrings |
| Cross-vendor ranking guard | new in Phase 17D | scans rendered MD for "is faster than", "outperforms", etc. |

## Selection rule

The harness picks the first N already-installed models from this preference order, walking it top-down:

1. `gemma4:e4b`
2. `gemma3:4b`
3. `llama3.2:3b`
4. `phi4-mini:latest`
5. `qwen2.5:7b` (rank-5 spillover; in panel only with `--max-models 5`)

Models > 10 GB (`gemma4:26b`, `qwen2.5:32b`, `osoderholm/poro:latest`) are NOT in the auto panel. They surface in the JSON's `deferred_too_large_by_default[]` field. Operator can opt in via `--prefer-larger-models` or `--models <list>`.

The CLI override `--models a,b,c` accepts any explicit comma-separated list. Every named model must be already-installed; if any is missing, the harness fails closed and refuses to proceed.

## Honest scope

What you can take from this document:

* The Phase 17D harness, given an already-installed local Ollama daemon and ≥ 2 of the rule-14 preferred models, will MEASURE per-model wall-clock latency and parse-success rate across 30 prompts × 3 repeats per model.
* Each per-model number is `MEASURED-FOR-THIS-MODEL-AND-PROMPT-SET`. The panel-level claim is `MEASURED-LOCAL-OLLAMA-PANEL`.
* The 14-substring forbidden-vocabulary scrub from Phase 17C plus 8 additional ranking-guard substrings keep the prose honest.

What you cannot take from this document:

* Output correctness against ground truth. The harness only checks `exit_code == 0` with non-empty stdout. Whether the model said "23" or "twenty-three" or hallucinated "47" for `14 + 9` is not graded here.
* **Cross-vendor ranking.** Different models have different parameter counts, training data, and quantizations. The numbers are reported per model in selection order, side-by-side. Inferring "Gemma is faster than Llama" or "Phi outperforms Qwen" is not supported.
* Cloud LLM endpoints. Anthropic, OpenAI, and Gemini slots remain `NOT_RUN` per Phase 17B Track G.

## Numbers (this session)

The canonical artifact lives at `docs/runs/phase17d_local_model_sweep_2026_05_05/phase17d_local_model_sweep.json` with a sibling `.md`. The summary table below is regenerated from that JSON; it is not a source of truth.

**Phase 17D host run (this session):** Windows 11 Enterprise / 24-CPU host, ollama 0.22.1, 4 models × 3 repeats × 30 prompts = 360 prompts total.

| model | size | id | prompts ok | min ms | p50 ms | p95 ms | p99 ms | mean ms | stddev ms | CoV |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `gemma4:e4b` | 9.6 GB | `c6eb396dbd59` | 90/90 | 700.7 | 784.7 | 18328.7 | 20415.8 | 2661.2 | 4888.2 | 0.0285 |
| `gemma3:4b` | 3.3 GB | `a2af6cc3eb7f` | 90/90 | 665.2 | 711.2 | 863.4 | 4777.1 | 776.0 | 427.9 | 0.0022 |
| `llama3.2:3b` | 2.0 GB | `a80c4f17acd5` | 90/90 | 452.5 | 526.6 | 2958.5 | 4072.5 | 986.2 | 876.8 | 0.0038 |
| `phi4-mini:latest` | 2.5 GB | `78fad5d182a7` | 90/90 | 471.7 | 549.3 | 3411.2 | 6364.9 | 1038.7 | 1170.7 | 0.0140 |

**Side-by-side observations (no ranking implied):**

* All four panel models completed all 360 prompts (90 per model). 0 failures; 0 timeouts; 0 pull/download triggers.
* p50 latency spread across the panel: 526.6 ms (`llama3.2:3b`) to 784.7 ms (`gemma4:e4b`). Each model is reported in its own row; the spread is a population statistic, not a quality judgement.
* Coefficient of variation across the 3 per-repeat medians: 0.0022 (`gemma3:4b`) to 0.0285 (`gemma4:e4b`). All four are far below the 0.30 noise threshold the design doc set as "stable" — repeatability across the panel is high on this host.
* p95 / p99 are dominated by cold-start outliers on the first repeat; the harness preserves every per-prompt latency in `per_repeat[i].per_prompt[]` so a future session can re-window or filter.
* Models > 10 GB present locally (`gemma4:26b`, `qwen2.5:32b`, `osoderholm/poro:latest`) are listed in the JSON's `deferred_too_large_by_default[]`. They are present on disk and were NOT exercised in the auto sweep; opt in via `--prefer-larger-models` or `--models <list>`.

**Top-level invariants from the JSON envelope:**

* `release_gate_pass = true`
* `forbidden_claims_absent = true`
* `provider_jobs_delta = builder_jobs_delta = 0`
* `no_model_pull_or_download = true`
* `no_cloud_api_calls = true`
* `claim_labels.ollama_local_baseline = "MEASURED-LOCAL-OLLAMA-PANEL"`
* `claim_labels.competitive_evidence_axis_J = "MEASURED-LOCAL-OLLAMA-PANEL"`
* `claim_labels.raw_intelligence_vs_frontier_moe = "NOT_CLAIMED"`
* `claim_labels.no_cross_vendor_ranking = true`

## Position in the 2026-Q2 release line

| Tag | What it adds | Status |
|---|---|---|
| `v3.8.0` | stable release | **Latest** |
| `v3.9.0-producer-fabric-alpha` | Phase 17A producer fabric + 10k scale | Pre-release |
| `v3.9.1-local-efficiency-benchmark-alpha` | Phase 17B local efficiency benchmark harness | Pre-release |
| `v3.9.2-local-ollama-baseline-alpha` | Phase 17C local Ollama baseline (Track F MEASURED, one model) | Pre-release |
| `v3.9.3-local-model-sweep-alpha` | Phase 17D local Ollama panel + repeatability | Pre-release (candidate) |

Phase 17D does not modify any earlier tag. v3.8.0 remains GitHub Latest.
