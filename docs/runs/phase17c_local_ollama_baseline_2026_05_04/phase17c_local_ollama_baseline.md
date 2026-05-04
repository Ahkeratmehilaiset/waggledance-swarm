# Phase 17C - Local Ollama Baseline

**Benchmark version:** phase17c.v1
**Git SHA:** 27b8175efe0f75088bf9f3771d54f713c3c3133c
**Python:** 3.13.7
**Platform:** Windows-11-10.0.22631-SP0
**Started UTC:** 2026-05-04T21:58:37Z
**Finished UTC:** 2026-05-04T22:03:45Z
**Duration (s):** 307.9258

## Policy declarations

**No cloud API calls were made.**
**No model was pulled or downloaded.**
**No widening of the six-family low-risk allowlist.**
**No Stage-2 atomic flip; no HUMAN_APPROVAL collected.**

## WaggleDance tracks (A-E, pass-through from Phase 17B)

- WaggleDance scenarios pass: **True**
- Provider jobs delta total: 0
- Builder jobs delta total: 0
- Phase 17B overall pass: True

## Track F - Local Ollama probe (one model)

- Status: **MEASURED**
- Model: `gemma4:e4b`
- Model ID: `None`
- Ollama version: `ollama version is 0.22.1`
- Prompt count: 30
- Prompts succeeded: 30
- Prompts failed: 0
- Median latency (s): 0.7866
- p95 latency (s): 17.5538
- Mean latency (s): 2.5539066666666668
- Total seconds: 76.6193
- Hash-chain head: `3813e784f4ab42d951469cb17097b3b2675e72b1cbc2953c48a22cca3c98f2ab`

## Claim labels

- `ollama_local_baseline`: **MEASURED-LOCAL-OLLAMA-ONE-MODEL**
- `competitive_evidence_axis_J`: **MEASURED-LOCAL-OLLAMA-ONE-MODEL**
- `no_cross_model_ranking`: **True**
- `no_cloud_api_comparison`: **True**
- `raw_intelligence_vs_frontier_moe`: **NOT_CLAIMED**

## What this measures

- The Phase 17B WaggleDance tracks A-E, pass-through verbatim.
- One local Ollama model latency profile against 30 deterministic
  factoid prompts derived from the six-family allowlist.

## What this does NOT measure

- Output correctness against ground truth (that is the WaggleDance
  routing job, not the local LLM probe).
- Cross-model ranking (only one local model is exercised).
- Cloud LLM endpoints (Anthropic / OpenAI / Gemini etc are NOT
  contacted; their slots remain documented NOT_RUN).

## Release gate

- `release_gate_pass`: **True**
- `forbidden_claims_absent`: **True**
- `provider_jobs_delta`: 0
- `builder_jobs_delta`: 0

