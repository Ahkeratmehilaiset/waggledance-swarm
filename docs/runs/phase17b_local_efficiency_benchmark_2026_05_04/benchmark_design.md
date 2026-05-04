# Phase 17B — Benchmark Harness Plan (P1)

**Date:** 2026-05-04
**Goal:** reproducible local AI efficiency benchmark harness that measures latency, throughput, correctness, fallback rate, provider/builder delta, and audit/provenance coverage across documented WaggleDance scenarios — with optional Ollama baseline if locally available, and explicit NOT_RUN slots for external competitors.

## Architectural decisions

1. **Aggregator, not re-implementation.** The harness is `tools/run_local_efficiency_benchmark.py`. It runs the existing Phase 11–17A proof scripts as subprocesses, parses their JSON outputs, and aggregates measured numbers into a single `benchmark.json`. It does not re-implement the proofs.
2. **One new probe.** A small Ollama latency probe (`F_ollama_baseline`) is the only new measurement code. Pure stdlib + the local `ollama` CLI — no `requests`, no provider HTTP adapter, no Anthropic / OpenAI cloud call.
3. **Explicit NOT_RUN slots for external competitors.** The harness writes documented `G_external_competitor_slots` entries with `status = "NOT_RUN"` and a reason field listing what evidence would be required to upgrade them to `MEASURED`.
4. **Honesty discipline.** Every WaggleDance scenario has its `provider_jobs_delta` and `builder_jobs_delta` recorded; the harness asserts both are 0 across A–E for the release decision to be A.
5. **Ollama-fallback-not-required.** Per master prompt rule 14, missing Ollama or missing model marks `ollama_baseline_status = "NOT_AVAILABLE_NOT_RUN"` and the harness still passes its required gates.
6. **Docker `--network none` compatibility.** Scenarios A–E remain `--network none` safe (carry-forward from v3.8.0 stable + v3.9.0-producer-fabric-alpha). Scenario F (Ollama) is automatically `NOT_AVAILABLE_NOT_RUN` inside `--network none` (the container can't reach the host's Ollama daemon at `host.docker.internal:11434`).

## Scenarios

| ID | name | source | new code? | reproduces |
|---|---|---|---|---|
| **A** | solver_hot_path | `tools/run_automatic_runtime_hint_proof.py` (Phase 15) | no | corpus 128 hint extractor; pass1/pass2/pass3 latency; auto_promotions; deltas |
| **B** | capability_lookup_10k | `tools/run_solver_scale_proof.py --descriptors 10000` (Phase 17A) | no | 10k descriptors / 1k lookups; capability hits; p50/p95/p99 |
| **C** | handle_query_e2e | `tools/run_upstream_structured_request_proof.py` (Phase 16A) | no | service.handle_query e2e; cold/warm latency; 7/7 negative cases |
| **D** | restart_continuity | `tools/run_full_restart_continuity_proof.py` (Phase 16B P2) | no | 128/128 served pre+post DB close+reopen; all 7 invariants True |
| **E** | producer_fabric | `tools/run_phase17a_producer_fabric_proof.py` (Phase 17A) | no | 68 IR objects across 6 kinds; 6/6 negative cases |
| **F** | ollama_baseline | new lightweight probe | **yes** | optional p50/p99 round-trip latency for 10 simple prompts against an already-present local model |
| **G** | external_competitor_slots | documented entries | no | NOT_RUN per slot with a one-line reason |

## Metrics per scenario

For each WaggleDance scenario A–E, the harness extracts from the proof JSON:

* `runtime_seconds` — wall-clock end-to-end duration of the proof
* `corpus_total` — number of items processed (where applicable)
* `served_total` / `auto_promotions_total` — successful capability dispatches
* `lookup_p50_ms` / `lookup_p95_ms` / `lookup_p99_ms` — where applicable (B, C)
* `provider_jobs_delta` — must be 0
* `builder_jobs_delta` — must be 0
* `passed` — boolean derived from the proof exit code

For F, the probe records:

* `model_name` (e.g., `phi4-mini:latest`)
* `prompt_count` — fixed at 10 deterministic prompts
* `latency_p50_ms` / `latency_p95_ms` / `latency_p99_ms`
* `total_seconds` — sum of all 10 round-trips
* `errors` — count of non-200 responses
* `status` = `AVAILABLE_RAN` or `NOT_AVAILABLE_NOT_RUN` or `ERRORED`

For G, each slot has:

* `name` (e.g., `gpt_4_turbo`, `claude_3_5_sonnet`, `gemini_1_5_pro`, `llama_cpp`, `vllm`, `mistral_rs`)
* `status = "NOT_RUN"`
* `reason_not_run` — exact reason (e.g., "would require Anthropic API call; master prompt rule 14 forbids cloud calls this session")
* `requirements_to_upgrade_to_measured` — list of evidence files that would need to land

## Pass criterion

The benchmark harness exit code is 0 (PASS) only if:

* Scenarios A–E all pass (their underlying proof scripts exit 0)
* Across A–E, total `provider_jobs_delta = 0` and total `builder_jobs_delta = 0`
* If F runs: `errors = 0`. If F is `NOT_AVAILABLE_NOT_RUN`: pass (does not affect required gates per rule 14)
* G slots are documented (presence of fields), not "passed" (they are NOT_RUN by definition)

If any of A–E fails, the harness exit code is 1 → release decision = B (no tag).

## Selected Ollama model for scenario F

`phi4-mini:latest` (2.5 GB):

* Already present locally per `ollama list` output at P0 (no pull required, master prompt rule 14 honored).
* Documented as the WaggleDance default in `waggledance/adapters/llm/ollama_adapter.py:Default model phi4-mini`.
* Smallest representative chat model on this host that's a "real" inference target (not an embedding model like `nomic-embed-text`).
* If the model is locally unavailable at run time (deleted between sessions), the probe falls through to `NOT_AVAILABLE_NOT_RUN`.

## Forbidden vocabulary check (master prompt rule 18)

The benchmark docs and JSON output must not include: `conscious`, `sentient`, `aware`, `alive`, `AGI`, `revolutionary`, `magical`, `human-like mind`, `self-aware`, `explosive intelligence`, `emergent`, `beats all competitors`, `world's best`, `world's fastest`. The harness emits only neutral measurement language; any superlative claim must be scoped to "fastest in this measured benchmark among the systems actually run" per rule 18.

## What this PR does NOT do

* Does NOT pull, download, or otherwise install any Ollama model.
* Does NOT call any cloud API (Anthropic / OpenAI / Gemini / etc).
* Does NOT modify v3.8.0 or v3.9.0-producer-fabric-alpha tags.
* Does NOT execute Stage-2 atomic flip.
* Does NOT collect HUMAN_APPROVAL.
* Does NOT widen the six-family allowlist.
* Does NOT add a Reflective Conductor / Episodic Replay Engine / new curiosity scheduler / new self-state updater / actuator autonomy / provider HTTP adapter / `/api/autonomy/query` route.
* Does NOT modify CURRENT_STATE.md manually.
* Does NOT make any consciousness, sentience, or "beats all competitors" claim.

## Wall-clock estimate

| phase | estimate |
|---|---|
| P0+P1 | done (~30 min) |
| P2 harness implementation | 1–1.5 h |
| P3 harness tests | 30 min |
| P4 run benchmark capture | 30–45 min (one full A–F run takes ~6–8 min of subprocess wall clock) |
| P5 evidence matrix update | 20 min |
| P6 targeted tests + Docker | 1 h |
| P7 release decision | 10 min |
| P8 commit + push + PR + CI | 30 min |
| P9 post-merge verify + tag + final report | 30 min |

**Sum: ~5–6 h.** Within the 10 h budget.
