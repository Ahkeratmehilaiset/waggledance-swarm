# Local Efficiency Benchmark — 2026-Q2

**Status:** Phase 17B snapshot, derived from this session's reproducible artifact only.
**Date:** 2026-05-04
**Branch:** `phase17b/local-efficiency-benchmark`
**Anchor:** `v3.9.1-local-efficiency-benchmark-alpha` candidate (PRERELEASE only).

This document publishes the raw measured numbers produced by `tools/run_phase17b_local_efficiency_benchmark.py` on `phase17b/local-efficiency-benchmark`. It is an **engineering** record. It does not assert WaggleDance is faster, more efficient, more correct, or otherwise superior to any named external system. The harness measures only what runs locally on this host.

WaggleDance does **not** claim to be conscious, sentient, aware, alive, or AGI. The autonomy mechanisms in this release are bounded engineering primitives, each mapped to a code path, persisted event, replayable proof, metric delta, and regression test.

## Reproduce

```
git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git
cd waggledance-swarm
git checkout v3.9.1-local-efficiency-benchmark-alpha   # or stay on main
pip install -r requirements-ci.txt
python tools/run_phase17b_local_efficiency_benchmark.py \
    --out-dir docs/runs/phase17b_local_efficiency_benchmark_2026_05_04 \
    --canonical-repeat 1 \
    --scale-descriptors 10000 \
    --scale-lookups 1000 \
    --producer-repeat 1 \
    --skip-ollama
```

## What the harness measures

Five required tracks (A–E) drive the existing Phase 11–17A canonical proof scripts as subprocesses, parse each proof's JSON, and aggregate the measured numbers into a single artifact at `docs/runs/phase17b_local_efficiency_benchmark_2026_05_04/phase17b_local_efficiency_benchmark.json`. Track F is an **optional** Ollama probe (only if Ollama is locally installed AND a model is already present). Track G enumerates external competitor slots that this session does not run.

| track | source proof | what it measures |
|---|---|---|
| **A** solver_hot_path | `tools/run_automatic_runtime_hint_proof.py` (Phase 15) | corpus-128 hint extraction, auto-promotions, deltas |
| **B** capability_lookup_10k | `tools/run_solver_scale_proof.py --descriptors 10000` (Phase 17A) | 10000 synthetic descriptors, 1000 capability lookups, p50/p95/p99 latency |
| **C** handle_query_e2e | `tools/run_upstream_structured_request_proof.py` (Phase 16A) | service.handle_query e2e, 7/7 negative cases |
| **D** restart_continuity | `tools/run_full_restart_continuity_proof.py` (Phase 16B P2) | 128/128 served pre+post restart, all 7 invariants True |
| **E** producer_fabric | `tools/run_phase17a_producer_fabric_proof.py` (Phase 17A) | 68 IR objects across 6 kinds, 6/6 negative cases |
| **F** ollama_baseline | new probe (this PR) | optional p50/p99 round-trip latency for 10 prompts against an already-present local model |
| **G** external_competitor_slots | documented entries | NOT_RUN per slot with explicit reason |

## Measured numbers — Phase 17B local run (this session)

Run: 2026-05-04, branch tip `phase17b/local-efficiency-benchmark`, host `Windows 11 Enterprise / WSL2 / 24-CPU / 62 GiB / overlayfs`.

| track | correctness | latency p50 / p95 / p99 (ms) | fallback rate | provider Δ | builder Δ |
|---|---:|---|---:|---:|---:|
| A solver_hot_path | 128 / 128 | (extractor lane: < 1 ms; full handle_query latency captured in proof's `latency_ms` block) | 0 | 0 | 0 |
| B capability_lookup_10k | 1000 / 1000 | **4.33 / 10.98 / 14.39** | 0 | 0 | 0 |
| C handle_query_e2e | 128 / 128 | (cold p50 ~ 16 ms / warm p50 ~ 11 ms in proof) | 0 | 0 | 0 |
| D restart_continuity | 128 / 128 pre+post | (proof verifies all 7 invariants True; latency not the focus) | 0 | 0 | 0 |
| E producer_fabric | 68 / 68 IR objects | (deterministic offline; latency not the focus) | 0 | 0 | 0 |

| track | status |
|---|---|
| F ollama_baseline | `SKIPPED` (default; harness requires `--include-ollama`) |
| G external_competitor_slots | `NOT_RUN` (six slots documented per master prompt rule 14) |

**Top-level invariants from the JSON envelope:**

* `release_gate_pass = true`
* `provider_jobs_delta = builder_jobs_delta = 0`
* `no_consciousness_claim = no_beats_all_competitors_claim = true`
* `no_cloud_api_calls_this_session = no_pull_or_download_this_session = true`
* `forbidden_claims_absent = true`
* `forbidden_vocabulary_excluded` enumerates the 14 phrases the harness refuses to use in its rendered MD body.

## Honest scope of these numbers

What you can take from the table above:

* **The capability-lookup p50 of 4.33 ms** is the round-trip cost of `RuntimeQueryRouter.route()` against an in-process SQLite control plane backed by 10000 synthetic auto-promoted descriptors balanced across 6 families × 8 hex cells, on the host above. It is not a network-latency number, not a wall-clock-throughput number, and not a number measured against any external system.
* **The 1000 / 1000 capability hit rate** is the strict pass criterion of scenario B: every sampled query landed on the real `auto_promoted_solver` path, never on FIFO fallback or miss. If this dropped below 1000 / 1000 the harness exit code would be 1.
* **The 128 / 128 restart-continuity** is the carry-forward of v3.8.0's stable contract: persisted solver count and capability-feature count are byte-stable across DB close + reopen.
* **The 0 / 0 provider/builder delta** across all five tracks is the master prompt rule 13 invariant: WaggleDance's inner loop did not invoke a provider lane or a builder lane during any of the measured proofs.

What you cannot take from the table:

* **No claim about WaggleDance vs frontier MoE on free-form reasoning.** Track G slots for Anthropic / OpenAI / Google are NOT_RUN. No paired benchmark was collected. Raw intelligence comparison is **NOT CLAIMED** in the competitive evidence matrix.
* **No claim about WaggleDance vs llama.cpp / vLLM / mistral-rs.** Those slots are NOT_RUN because the binaries are not present on this host and master prompt rule 14 forbids download / pull this session.
* **No throughput claim under multi-tenant load.** The harness measures single-process latency per scenario; concurrent-load behavior is out of scope.
* **No edge-class claim.** ARM build / Pi 4 / Pi 5 measurements were not taken this session.

## How the harness keeps itself honest

* The output JSON includes `forbidden_vocabulary_excluded` so a substring regression test can guard the rendered Markdown for the master prompt rule 18 denylist.
* `release_gate_pass = false` is the only outcome if any of the five WaggleDance scenarios fails its proof, the provider/builder delta totals are non-zero, or the optional Ollama probe records errors.
* External competitor slots are emitted with `requirements_to_upgrade_to_measured` lists, so a future session knows exactly what evidence would be required to upgrade them to MEASURED.

## How to extend this benchmark

| To upgrade | You would need |
|---|---|
| F ollama_baseline | run with `--include-ollama` against an already-present model; record the result as a separate session JSON |
| G frontier_anthropic_claude / frontier_openai_gpt / frontier_google_gemini | a documented prompt template + sampling parameters + per-query route_source recording, plus a paired WaggleDance run on the same input set |
| G local_llama_cpp / local_vllm / local_mistral_rs | the binaries already present + model weights already downloaded at session start (master prompt rule 14 forbids download / pull within a session) |
| Edge fitness (Pi 4 / Pi 5) | a fresh `docker buildx build --platform linux/arm64` + the same harness rerun on the edge host, with hardware spec recorded |
| Multi-tenant throughput | a separate harness that drives `route()` from N concurrent threads and records per-thread latencies + global qps |

Each upgrade would land as a new artifact under `docs/runs/<phase>_<topic>_<date>/` and a new label on the corresponding axis of `COMPETITIVE_EVIDENCE_MATRIX_2026.md`. **No row's label changes without new evidence.**
