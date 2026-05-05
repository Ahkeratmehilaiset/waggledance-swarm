# Claim Evidence Ledger — 2026-Q2

**Status:** Phase 18A snapshot, derived from this session's reproducible bundle only.
**Date:** 2026-05-05
**Branch:** `phase18a/benchmark-externalization-schema`
**Bundle:** `docs/runs/phase18a_benchmark_externalization_2026_05_05/export_bundle/`

This document is a human-readable reading of `claim_evidence_ledger.json`. The JSON is the source of truth; this document is regenerated from it (and from the same data committed under the bundle's `reports/claim_evidence_ledger.md`).

## Allowed labels

| label | meaning |
|---|---|
| `PROVEN` | A test or proof in this repo asserts the claim and exits 0. |
| `MEASURED` | A reproducible measurement was taken in this session. The number is real but the claim it supports is conditional. |
| `INFERRED` | The claim follows from architecture but no benchmark was run in this session. |
| `NOT_CLAIMED` | This repo does not assert the claim. Surfaced explicitly so a future reader knows the gap. |
| `NOT_RUN` | A planned slot was deliberately skipped this session (typically because it would require a cloud API or a model download). |
| `MEASURED_LOCAL_ONLY` | Reserved for future single-host measurements. |
| `MEASURED_LOCAL_OLLAMA_ONE_MODEL` | Phase 17C single-model Ollama probe. |
| `MEASURED_LOCAL_OLLAMA_PANEL` | Phase 17D 4-model panel + repeatability. |

## Claims (16, in canonical order)

| # | claim_id | label | evidence_artifact |
|---:|---|---|---|
| 1 | `docker_offline_proven` | **PROVEN** | `phase17b_local_efficiency_benchmark.sanitized.json` |
| 2 | `producer_fabric_proven` | **PROVEN** | `phase17b_local_efficiency_benchmark.sanitized.json` |
| 3 | `capability_lookup_10k_measured` | **MEASURED** | `phase17b_local_efficiency_benchmark.sanitized.json` |
| 4 | `canonical_corpus_128_proven` | **PROVEN** | `phase17b_local_efficiency_benchmark.sanitized.json` |
| 5 | `local_efficiency_harness_proven` | **PROVEN** | `phase17b_local_efficiency_benchmark.sanitized.json` |
| 6 | `local_ollama_one_model_measured` | **MEASURED_LOCAL_OLLAMA_ONE_MODEL** | `phase17c_local_ollama_baseline.sanitized.json` |
| 7 | `local_ollama_panel_measured` | **MEASURED_LOCAL_OLLAMA_PANEL** | `phase17d_local_model_sweep.sanitized.json` |
| 8 | `raw_intelligence_vs_frontier_moe_not_claimed` | **NOT_CLAIMED** | `phase17d_local_model_sweep.sanitized.json` |
| 9 | `cross_vendor_ranking_not_claimed` | **NOT_CLAIMED** | `phase17d_local_model_sweep.sanitized.json` |
| 10 | `no_model_pull_or_download` | **PROVEN** | `phase17d_local_model_sweep.sanitized.json` |
| 11 | `no_cloud_api_calls` | **PROVEN** | `phase17d_local_model_sweep.sanitized.json` |
| 12 | `provider_builder_delta_zero` | **PROVEN** | `phase17b_local_efficiency_benchmark.sanitized.json` |
| 13 | `no_stage2_flip` | **PROVEN** | `phase17d_local_model_sweep.sanitized.json` |
| 14 | `no_human_approval_collected` | **PROVEN** | `phase17d_local_model_sweep.sanitized.json` |
| 15 | `no_allowlist_widening` | **PROVEN** | `phase17b_local_efficiency_benchmark.sanitized.json` |
| 16 | `benchmark_artifact_externalization` | **PROVEN** | `phase17d_local_model_sweep.sanitized.json` |

## Negative claims (the things we deliberately do NOT assert)

* **Raw intelligence vs frontier MoE.** WaggleDance does not claim its routing produces better answers than any frontier MoE model. No paired benchmark was run. Surfaced as `claim_id = raw_intelligence_vs_frontier_moe_not_claimed` with label `NOT_CLAIMED`.
* **Cross-vendor ranking among local models.** The Phase 17D 4-model panel reports per-model numbers in selection order, side-by-side. Inferring "Gemma is faster than Llama" or "Phi outperforms Qwen" is not supported. Surfaced as `claim_id = cross_vendor_ranking_not_claimed` with label `NOT_CLAIMED`.

## Honesty contracts (asserted as PROVEN above)

* `no_model_pull_or_download` — the Phase 17C/17D harnesses scan every Ollama subprocess output for pull/download substrings and abort on a hit; no `ollama pull` is ever invoked.
* `no_cloud_api_calls` — Anthropic/OpenAI/Gemini/llama.cpp/vLLM/mistral-rs slots all `NOT_RUN`; Docker proofs run with `--network none`.
* `provider_builder_delta_zero` — every Phase 11–17A proof JSON records `provider_jobs_delta_during_proof = builder_jobs_delta_during_proof = 0`.
* `no_stage2_flip`, `no_human_approval_collected`, `no_allowlist_widening` — session_state.json invariants under each `docs/runs/phase1*_*/`.

## How to extend

To add a claim, append to `_build_claim_ledger()` in `tools/run_phase18a_benchmark_externalization.py`, add the `claim_id` to `REQUIRED_CLAIM_IDS` in `tools/validate_phase18a_benchmark_bundle.py` if it must be present in every bundle, and re-run the exporter + validator. The schema is defined in `schemas/benchmarks/v1/claim_evidence_ledger.schema.json`.
