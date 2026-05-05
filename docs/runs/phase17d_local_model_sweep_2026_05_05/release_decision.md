# Phase 17D — Release Decision

**Decision:** **A — release `v3.9.3-local-model-sweep-alpha` PRERELEASE.**
**Date (UTC):** 2026-05-05
**Branch:** `phase17d/local-model-sweep`
**Base SHA:** `4f8a9ea7774a9f4c862c8342dcc69ef714386b8f` (Phase 17C post-release docs PR #76 merge)

## Gate evaluation

All Phase 17D release gates green:

| Gate | Result |
| --- | --- |
| `at_least_two_models_measured` | true (4 models MEASURED) |
| `selected_models` | 4: gemma4:e4b, gemma3:4b, llama3.2:3b, phi4-mini:latest |
| `total prompts` | 360 / 360 succeeded; 0 failed |
| `release_gate_pass` | `true` |
| `forbidden_claims_absent` | `true` |
| `no_model_pull_or_download` | `true` |
| `no_cloud_api_calls` | `true` |
| `no_pull_download_detected` | `true` |
| `provider_jobs_delta` / `builder_jobs_delta` | 0 / 0 |
| `no_cross_vendor_ranking` | `true` |
| `no_raw_intelligence_superiority_claim` | `true` |
| Targeted tests (autonomy_growth + phase10 + storage + ui_hologram + solver_router) | 164 / 164 PASS |
| Docker `--network none` proof (waggledance:phase17d) | PASS (`release_gate_pass=true`) |
| `git rev-parse v3.8.0^{}` | `824176ebf2a6b8debed41982090a125cbe2ddad1` (unchanged) |
| `git rev-parse v3.9.0-producer-fabric-alpha^{}` | `c726995c816ee4c09e031c2190c3de6592e82879` (unchanged) |
| `git rev-parse v3.9.1-local-efficiency-benchmark-alpha^{}` | `f4d0a4a4152ca74e98a8d7f7161c233075bf4111` (unchanged) |
| `git rev-parse v3.9.2-local-ollama-baseline-alpha^{}` | `db5d7db1ecb9ae6f17293f0bf7261f4c9d40e91c` (unchanged) |
| `gh release list` | v3.8.0 still **Latest** |

## Measured Ollama numbers (host run, 4 models × 3 repeats × 30 prompts)

| model | size | id | prompts ok | min ms | p50 ms | p95 ms | p99 ms | mean ms | stddev ms | CoV |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `gemma4:e4b` | 9.6 GB | `c6eb396dbd59` | 90/90 | 700.7 | 784.7 | 18328.7 | 20415.8 | 2661.2 | 4888.2 | 0.0285 |
| `gemma3:4b` | 3.3 GB | `a2af6cc3eb7f` | 90/90 | 665.2 | 711.2 | 863.4 | 4777.1 | 776.0 | 427.9 | 0.0022 |
| `llama3.2:3b` | 2.0 GB | `a80c4f17acd5` | 90/90 | 452.5 | 526.6 | 2958.5 | 4072.5 | 986.2 | 876.8 | 0.0038 |
| `phi4-mini:latest` | 2.5 GB | `78fad5d182a7` | 90/90 | 471.7 | 549.3 | 3411.2 | 6364.9 | 1038.7 | 1170.7 | 0.0140 |

* p50 panel spread: 526.6–784.7 ms.
* CoV across the 3 per-repeat medians: 0.0022–0.0285 (well below the 0.30 noise threshold the design doc set as "stable").
* Reported in selection order; no rank ordering is implied.
* Deferred (NOT exercised, present locally but > 10 GB): `gemma4:26b`, `qwen2.5:32b`, `osoderholm/poro:latest`.

## Tag plan

* Tag name: `v3.9.3-local-model-sweep-alpha`.
* `isPrerelease = true`. **NOT** `Latest`.
* Target: the squash-merge commit of the Phase 17D PR.
* GitHub release: created via `gh release create v3.9.3-local-model-sweep-alpha --prerelease --target <merge SHA>`.
* `v3.8.0` remains GitHub Latest. v3.9.0 + v3.9.1 + v3.9.2 + v3.9.3 alphas all Pre-release.

## What this release does NOT do

* Does NOT modify the `v3.8.0`, `v3.9.0-producer-fabric-alpha`, `v3.9.1-local-efficiency-benchmark-alpha`, or `v3.9.2-local-ollama-baseline-alpha` tags.
* Does NOT change autonomy code, the six-family low-risk allowlist, the canonical 128-seed corpus, the 10k synthetic-scale ceiling, or any runtime entrypoint.
* Does NOT execute Stage-2 atomic flip; does NOT collect HUMAN_APPROVAL.
* Does NOT touch `phase8.5/*` branches.
* Does NOT pull or download any Ollama model.
* Does NOT call any cloud LLM API.
* Does NOT widen forbidden-vocabulary surface.
* Does NOT make any cross-vendor ranking claim.
* Does NOT make any raw-intelligence superiority claim.
* Does NOT claim consciousness, sentience, AGI, or "beats all competitors" — those flags remain in `not_claimed[]` of the Phase 17D JSON.
