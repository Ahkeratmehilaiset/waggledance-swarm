# Phase 17C — Release Decision

**Decision:** **A — release `v3.9.2-local-ollama-baseline-alpha` PRERELEASE.**
**Date (UTC):** 2026-05-04
**Branch:** `phase17c/local-ollama-baseline`
**Base SHA:** `27b8175efe0f75088bf9f3771d54f713c3c3133c` (Phase 17B post-release docs PR #74 merge)

## Gate evaluation

All Phase 17C release gates green:

| Gate | Result |
| --- | --- |
| `ollama_baseline_status` | `MEASURED` |
| `prompts_run / prompts_succeeded / prompts_failed` | 30 / 30 / 0 |
| `release_gate_pass` | `true` |
| `forbidden_claims_absent` | `true` |
| `provider_jobs_delta` / `builder_jobs_delta` | 0 / 0 |
| Phase 17B aggregator clean exit | `true` |
| Targeted tests (autonomy_growth + phase10 + storage + ui_hologram + solver_synthesis + ollama_probe) | 156 / 156 PASS |
| Docker `--network none` proof (waggledance:phase17c) | PASS (`release_gate_pass=true`) |
| `git rev-parse v3.8.0^{}` | `824176ebf2a6b8debed41982090a125cbe2ddad1` (unchanged) |
| `git rev-parse v3.9.0-producer-fabric-alpha^{}` | `c726995c816ee4c09e031c2190c3de6592e82879` (unchanged) |
| `git rev-parse v3.9.1-local-efficiency-benchmark-alpha^{}` | `f4d0a4a4152ca74e98a8d7f7161c233075bf4111` (unchanged) |
| `gh release list` | v3.8.0 still **Latest** |

## Measured Ollama numbers (host run)

* Selected model: `gemma4:e4b` (id `c6eb396dbd59`, 9.6 GB on disk).
* Ollama version: `0.22.1`.
* 30 deterministic prompts (5 each across the six low-risk families).
* `median_latency_seconds = 0.7866`.
* `p95_latency_seconds = 17.5538` (dominated by two cold-emission outliers — the harness preserves all per-prompt latencies verbatim).
* `mean_latency_seconds = 2.5539`.
* `total_seconds = 76.6193`.
* `hash_chain_sha256` head: `3813e784f4ab42d9...`.

## Tag plan

* Tag name: `v3.9.2-local-ollama-baseline-alpha`.
* `isPrerelease = true`. **NOT** `Latest`.
* Target: the squash-merge commit of the Phase 17C PR.
* GitHub release: created via `gh release create v3.9.2-local-ollama-baseline-alpha --prerelease --target <merge SHA>`.
* `v3.8.0` remains GitHub Latest. v3.9.0 + v3.9.1 alphas remain Pre-release.

## What this release does NOT do

* Does NOT modify the `v3.8.0`, `v3.9.0-producer-fabric-alpha`, or `v3.9.1-local-efficiency-benchmark-alpha` tags.
* Does NOT change autonomy code, the six-family low-risk allowlist, the canonical 128-seed corpus, the 10k synthetic-scale ceiling, or any runtime entrypoint.
* Does NOT execute Stage-2 atomic flip; does NOT collect HUMAN_APPROVAL.
* Does NOT touch `phase8.5/*` branches.
* Does NOT pull or download any Ollama model.
* Does NOT call any cloud LLM API.
* Does NOT widen any forbidden-vocabulary surface.
* Does NOT claim consciousness, sentience, AGI, or "beats all competitors" — those flags remain in `not_claimed[]` of every benchmark JSON Phase 17C emits.
