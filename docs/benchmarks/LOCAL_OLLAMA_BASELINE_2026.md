# Local Ollama Baseline — 2026-Q2

**Status:** Phase 17C snapshot, derived from this session's reproducible artifact only.
**Date:** 2026-05-04
**Branch:** `phase17c/local-ollama-baseline`
**Anchor:** `v3.9.2-local-ollama-baseline-alpha` candidate (PRERELEASE only). v3.8.0 remains GitHub Latest. v3.9.0-producer-fabric-alpha and v3.9.1-local-efficiency-benchmark-alpha remain Pre-release.

This document publishes the raw measured numbers produced by `tools/run_phase17c_local_ollama_baseline.py` on `phase17c/local-ollama-baseline`. It is an **engineering** record. It does not assert WaggleDance is faster, more efficient, more correct, or otherwise superior to any named external system. The harness measures only what runs locally on this host with one already-installed Ollama model.

WaggleDance does **not** claim to be conscious, sentient, aware, alive, or AGI. The autonomy mechanisms in this release are bounded engineering primitives, each mapped to a code path, persisted event, replayable proof, metric delta, and regression test.

## Reproduce

```
git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git
cd waggledance-swarm
git checkout v3.9.2-local-ollama-baseline-alpha   # or stay on main
pip install -r requirements-ci.txt
# Ollama must already be installed locally with at least one of the
# rule-14 preferred models present. The harness never pulls a model
# and never calls a cloud API.
python tools/run_phase17c_local_ollama_baseline.py
```

## What the harness measures

The Phase 17C harness runs the Phase 17B aggregator as a subprocess (which produces the WaggleDance tracks A–E unchanged) and adds a 30-prompt deterministic probe against one already-installed Ollama model.

| track | source | what it measures |
|---|---|---|
| **A–E** WaggleDance | `tools/run_phase17b_local_efficiency_benchmark.py --skip-ollama` | the same Phase 11–17A canonical proofs, pass-through verbatim |
| **F** Ollama probe | new in Phase 17C | 30 deterministic prompts against `gemma4:e4b` (rule-14 preference order) |

The 30 prompts cover the six low-risk family vocabulary (5 each): `scalar_unit_conversion`, `lookup_table`, `threshold_rule`, `interval_bucket_classifier`, `linear_arithmetic`, `bounded_interpolation`. Each is short and factoid-style.

## Selection rule (rule 14)

The harness picks the first match it finds in `ollama list`, walking this preference order:

1. `gemma4:e4b`
2. `gemma4:26b`
3. `gemma3:4b`
4. `qwen2.5:7b`
5. `phi4-mini:latest`
6. `llama3.2:3b`

If none are present, the Ollama track is recorded as `NOT_AVAILABLE_NOT_RUN` and the harness will not pull a model. The CLI override `--ollama-model NAME` takes the named model if it is already present locally; if it is not, the harness fails closed.

## Honest scope

What you can take from this document:

* The Phase 17B WaggleDance tracks A–E pass-through verbatim — they remain `release_gate_pass=true` per the Phase 17B contract.
* The Ollama probe records wall-clock latency for 30 deterministic prompts against one local model. It is a smoke test of local LLM round-trip cost on this hardware, not a quality benchmark.

What you cannot take from this document:

* No comparison of WaggleDance routing accuracy vs the Ollama model. The harness does not score the model's outputs against ground truth. Output correctness is the WaggleDance routing job, not the local LLM probe's.
* No cross-model ranking. Only one local model is exercised per run.
* No cloud LLM comparison. Anthropic / OpenAI / Gemini slots remain `NOT_RUN` per Phase 17B Track G.

## Numbers (this session)

The canonical artifact lives at `docs/runs/phase17c_local_ollama_baseline_2026_05_04/phase17c_local_ollama_baseline.json` with a sibling `.md`. Track-level numbers are pulled from there; this document is a thin index, not a source of truth.

The Ollama track is recorded with the master-prompt-mandated metric set:

* `model`, `model_id`, `ollama_version`
* `prompt_count`, `prompts_succeeded`, `prompts_failed`
* `median_latency_seconds`, `p95_latency_seconds`, `mean_latency_seconds`
* `total_seconds`
* `hash_chain_sha256` (chained SHA-256 of all prompt stdouts)
* full `per_prompt` array with per-prompt latency, exit code, prompt-hash, stdout-hash

**Phase 17C host run (this session):**

Run: 2026-05-04, branch tip `phase17c/local-ollama-baseline`, host `Windows 11 Enterprise / 24-CPU / overlayfs` with Ollama 0.22.1 and `gemma4:e4b` (id `c6eb396dbd59`, 9.6 GB on disk).

| metric | value |
| --- | --- |
| `selected_ollama_model` | `gemma4:e4b` |
| `ollama_version` | `ollama version is 0.22.1` |
| `prompt_count` | 30 |
| `prompts_succeeded` | 30 |
| `prompts_failed` | 0 |
| `median_latency_seconds` | **0.7866** |
| `p95_latency_seconds` | **17.5538** |
| `mean_latency_seconds` | 2.5539 |
| `total_seconds` | 76.6193 |
| `hash_chain_sha256` (head) | `3813e784f4ab42d9...` |
| `release_gate_pass` | **true** |
| `forbidden_claims_absent` | true |
| `provider_jobs_delta` / `builder_jobs_delta` | 0 / 0 |

The p95 is dominated by two cold-token-emission outliers (prompt 0 at 5.06 s for the first model touch and prompts 25/29 at 17.55 s and 19.40 s respectively, both interpolation prompts where the model emits a longer reasoning preamble). Steady-state per-prompt cost on this host is ~0.7-1.0 s. The harness deliberately does not strip outliers — every per-prompt latency is preserved verbatim in `per_prompt[]` so a future session can re-window or filter as needed.

**Phase 17B WaggleDance tracks pass-through (this session):**

| track | numbers |
| --- | --- |
| A solver_hot_path | corpus 128 / 128, auto_promotions 128, served-via-capability 128 |
| B capability_lookup_10k | p50 / p95 / p99 = **4.09 / 10.49 / 12.93 ms**, 1000/1000 hits, 0 fallback |
| C handle_query_e2e | 128 / 128, 7/7 negative cases pass |
| D restart_continuity | 128 / 128 pre+post, 7/7 invariants True |
| E producer_fabric | 68 IR objects, 6/6 negative cases pass |

## Honesty contracts

* `no_model_pull_or_download = true` — invariant.
* `no_cloud_api_calls = true` — invariant.
* `forbidden_claims_absent` — checked at the rendered-bytes level for the 14 denylist substrings (master prompt rule 18).
* `provider_jobs_delta = builder_jobs_delta = 0` — pass-through from Phase 17B.
* `release_gate_pass = false` if any of: WaggleDance scenarios fail, the Ollama probe records non-zero failures (without `--allow-no-ollama-track`), forbidden substrings appear, or provider/builder deltas are non-zero.

## Position in the 2026-Q2 release line

| Tag | What it adds | Status |
|---|---|---|
| `v3.8.0` | stable release | **Latest** |
| `v3.9.0-producer-fabric-alpha` | Phase 17A producer fabric + 10k scale | Pre-release |
| `v3.9.1-local-efficiency-benchmark-alpha` | Phase 17B local efficiency benchmark harness | Pre-release |
| `v3.9.2-local-ollama-baseline-alpha` | Phase 17C local Ollama baseline (Track F MEASURED, one model) | Pre-release |
| `v3.9.3-local-model-sweep-alpha` | Phase 17D local Ollama panel + repeatability (this PR's candidate) | Pre-release (candidate) |

Phase 17C does not modify any earlier tag. v3.8.0 remains GitHub Latest.

## Phase 17D successor

Phase 17D extends this single-model baseline to a panel of N already-installed local models with R repeats per model. See `docs/benchmarks/LOCAL_OLLAMA_MODEL_SWEEP_2026.md` for the panel report and `tools/run_phase17d_local_model_sweep.py` for the harness.
