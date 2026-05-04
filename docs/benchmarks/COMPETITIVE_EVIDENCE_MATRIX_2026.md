# Competitive Evidence Matrix — 2026-Q2

**Status:** Phase 17A + Phase 17B snapshot, derived from this session's reproducible artifacts only.
**Date:** 2026-05-04
**Branch:** `phase17b/local-efficiency-benchmark` (Phase 17A is on `main` at `c726995c`)
**Anchor:** `v3.9.1-local-efficiency-benchmark-alpha` candidate (PRERELEASE only; v3.8.0 remains GitHub Latest, v3.9.0-producer-fabric-alpha remains the previous Pre-release).
**New evidence this PR:** Phase 17B's `tools/run_phase17b_local_efficiency_benchmark.py` aggregates the existing Phase 11–17A proof outputs into a single benchmark JSON + MD with the master-prompt-mandated metric set (correctness, latency p50/p95/p99, fallback rate, provider/builder delta, audit/provenance coverage, claim labels). Detailed run report: `docs/benchmarks/LOCAL_EFFICIENCY_BENCHMARK_2026.md`.

This is an **engineering** document. It does not market WaggleDance. It enumerates the comparison axes most often used to assess local-first cognitive runtimes, states one factual claim per axis, points to a reproducible artifact in this repo, and labels the claim with one of:

| Label | Meaning |
|---|---|
| **PROVEN** | A test or proof in this repo asserts the claim and exits 0. |
| **MEASURED** | A reproducible measurement was taken in this session. The number is real but the claim it supports is conditional. |
| **INFERRED** | The claim follows from architecture but no benchmark was run in this session. |
| **NOT CLAIMED** | This repo does not assert the claim. Any external comparison would need a benchmark we have not run. |

WaggleDance does **not** claim to be conscious, sentient, aware, alive, or AGI. The autonomy mechanisms in this release are bounded engineering primitives, each mapped to a code path, persisted event, replayable proof, metric delta, and regression test.

WaggleDance does **not** claim to "beat all competitors." This document does not rank rivals. It records what *we* have proven about *this* runtime, in this branch, in this session, against artifacts that anyone can reproduce.

## Axes

### A. Deterministic routing — solver-first, LLM-last

* **Claim:** Every query in the inner loop is dispatched first to a deterministic solver. LLM fallback is a documented later layer, not the default.
* **Evidence artifacts:** `waggledance/core/autonomy_growth/runtime_query_router.py` (`route()`); existing main proofs (Phase 15 hint, Phase 16A upstream, Phase 16B P2 full restart) all complete with `provider_jobs_delta = 0` and `builder_jobs_delta = 0`. Phase 17A's solver scale proof asserts every sampled query hits the capability-lookup path (`auto_promoted_solver` source); zero FIFO fallback; zero gap-emitted misses.
* **Reproduce:** `python tools/run_solver_scale_proof.py --descriptors 10000 --lookup-pass-count 1000`
* **Label:** **PROVEN.**
* **Strengthening path:** add a Reality View histogram of `lookup_by_source` over 1 hour of synthetic traffic; publish.

### B. Audit / provenance / replay

* **Claim:** The runtime persists every solver call into MAGMA / control-plane SQLite tables (autonomy_runs, growth_events, runtime_gap_signals, solver_capability_features), with deterministic IDs and replayable trajectories.
* **Evidence artifacts:** `waggledance/core/storage/control_plane.py` schema (28+ tables); Phase 16B P2 full-restart-continuity proof closes DB → reopens → re-serves identical state; Phase 16D B324 cleanup verified persisted-semantic-fingerprint preservation field-by-field (14 scalar + 7 invariant + 6 per-op counts identical).
* **Reproduce:** `python tools/run_full_restart_continuity_proof.py --out-dir <out>`
* **Label:** **PROVEN.**
* **Strengthening path:** add a per-call MAGMA event count to the proof JSON; expose via Reality View.

### C. Zero-provider inner loop

* **Claim:** The autonomy inner loop runs without any provider call, builder call, or LLM consult. Provider/builder delta = 0 across all critical proofs.
* **Evidence artifacts:** Every Phase 11–17A proof JSON includes `provider_jobs_delta_during_proof = 0` and `builder_jobs_delta_during_proof = 0`. Asserted by `tests/autonomy_growth/test_outer_inner_loop_truthful.py` (Phase 12) and re-asserted by Phase 17A producer-fabric and scale-proof tests.
* **Reproduce:** any of `python tools/run_*_proof.py`; check the resulting JSON.
* **Label:** **PROVEN.**

### D. Docker offline `--network none`

* **Claim:** The autonomy inner loop and producer fabric run end-to-end inside a Docker container with the network disabled (`docker run --rm --network none`). No DNS, no API key reachable.
* **Evidence artifacts:** Phase 16F `docker_runtime_proofs.md` (3 canonical proofs + smoke suite passed `--network none` at corpus 104 in `waggledance:phase16f`). Phase 17A reruns the producer fabric proof + 10k scale proof + restart proof in `waggledance:phase17a` `--network none`.
* **Reproduce:** `docker build -t waggledance:phase17a . && docker run --rm --network none waggledance:phase17a python tools/run_phase17a_producer_fabric_proof.py --out-dir /tmp/p17a`
* **Label:** **PROVEN** for the Phase 11–16F runtime stack. **PROVEN this session** for Phase 17A producer fabric and 10k scale.

### E. Restart continuity

* **Claim:** The autonomy state survives a full DB close + reopen with byte-stable solver and capability-feature counts; provider/builder delta across restart = 0.
* **Evidence artifacts:** Phase 16B P2 / Phase 16D / Phase 17A `full_restart_continuity_proof.json`. With Phase 17A's 128-seed corpus, the proof now reports `solver_count_before/after = 128/128` and `capability_features = 220/220`, identical pre and post reopen.
* **Reproduce:** `python tools/run_full_restart_continuity_proof.py`
* **Label:** **PROVEN.**

### F. Producer fabric (curiosity / self-model / dream / hive)

* **Claim:** Four producer planes (curiosity gap mining, self-model snapshot, dream curriculum, hive proposes) run end-to-end offline against deterministic fixtures and emit JSON consumed without modification by the four IR consumer adapters that have shipped on `main` since Phase 9.
* **Evidence artifacts:** `tools/run_phase17a_producer_fabric_proof.py` + `producer_fabric_proof.json` + `tests/autonomy_growth/test_phase17a_producer_fabric_proof.py` (18/18 PASS). Ported producer modules: `waggledance/core/dreaming/*` (7 files, ~2.2K LOC), `waggledance/core/magma/{self_model.py, reflective_workspace.py}` (~1.1K LOC), `waggledance/core/meta/*` (5 files, ~1.3K LOC). Total ported = 4,511 LOC, all stdlib + waggledance only.
* **Reproduce:** `python tools/run_phase17a_producer_fabric_proof.py --out-dir <out>`; expect 68 IR objects emitted, 6/6 negative cases pass, 0/0 deltas.
* **Label:** **PROVEN this session.**
* **Strengthening path:** drive the producer fabric with real ingestion logs (not synthetic fixtures) and report whether the IR object count changes meaningfully.

### G. 10,000-solver capability scale

* **Claim:** The capability-aware lookup path scales to ≥10,000 auto-promoted solver descriptors balanced across 6 families × 8 hex cells, with low-ms p50 lookup latency, while keeping provider/builder delta = 0.
* **Evidence artifacts:** `tools/run_solver_scale_proof.py --descriptors 10000` produces `solver_scale_proof.json` showing `synthetic_solver_descriptors_total = 10000`, `lookup_capability_hits_total = 1000`, `lookup_fifo_fallback_total = 0`, `lookup_miss_total = 0`. Phase 17B aggregates this into `phase17b_local_efficiency_benchmark.json` track B with the master-prompt metric set. `tests/autonomy_growth/test_solver_scale_proof.py` (21/21 PASS) and `tests/autonomy_growth/test_phase17b_local_efficiency_benchmark.py` both assert strict pass criteria.
* **Measured Phase 17A (host run):** build 147.25 s (~68 descriptors/s), lookup p50 = 4.24 ms, p95 = 10.78 ms, p99 = 14.10 ms on the host.
* **Measured Phase 17A (Docker `--network none` run):** lookup p50 = 0.47 ms, p95 = 0.94 ms, p99 = 1.17 ms on the same host inside `waggledance:phase17a` (`v3.9.0-producer-fabric-alpha-rc`).
* **Measured Phase 17B (host run, this session):** lookup p50 = 4.33 ms, p95 = 10.98 ms, p99 = 14.39 ms; `release_gate_pass = true`; 1000/1000 hits via `auto_promoted_solver` source.
* **Honesty caveat:** these are **synthetic** descriptors used only to exercise the data path at scale. The canonical proof corpus is the 128-seed library (Phase 17A P5 expansion). The synthetic descriptors and the canonical corpus are clearly separated in the proof artifacts and labelled `is_synthetic_scale=true, not_canonical_corpus=true`.
* **Reproduce:** `python tools/run_solver_scale_proof.py --descriptors 10000 --lookup-pass-count 1000` or via the aggregator `python tools/run_phase17b_local_efficiency_benchmark.py --skip-ollama --scale-descriptors 10000 --scale-lookups 1000`.
* **Label:** **MEASURED this session.** **PROVEN** that the data path works (no FIFO fallback, no miss, capability-lookup-only). The 10,000 number itself is a measured ceiling, not an architectural maximum.
* **Strengthening path:** rerun on a fresh GitHub Actions Linux runner (different hardware) and publish the spread; rerun at 50,000 descriptors and report the build/lookup curve.

### H. Canonical seed corpus size

* **Claim:** The canonical seed library carries 128 deterministic seeds across the six low-risk families (32 + 21 + 21 + 18 + 18 + 18), grown materially without widening the allowlist.
* **Evidence artifacts:** `waggledance/core/autonomy_growth/low_risk_seed_library.py`; `tests/autonomy_growth/test_seed_library.py::test_seed_library_meets_phase17a_material_growth_minimum` (>=128) + `test_seed_library_per_family_minimum_after_phase17a_growth` (per-family floor 32/21/21/18/18/18).
* **Reproduce:** `pytest tests/autonomy_growth/test_seed_library.py -q`
* **Label:** **PROVEN.**

### I. Raw intelligence vs frontier MoE / GPT-class models

* **Claim:** WaggleDance is faster on deterministic structured queries, slower on free-form open-ended reasoning. (This is a directional intuition, not a benchmark.)
* **Evidence:** none in this repo. We have not run BBH, MMLU, GSM8K, HumanEval, ARC, GPQA, or any other public reasoning benchmark against frontier MoE models.
* **Label:** **NOT CLAIMED.**
* **Strengthening path:** add `tools/run_external_benchmark.py` that drives the runtime through a chosen public benchmark; publish the score with full methodology including where LLM fallback was invoked and how often.

### J. LLM / MoE fallback as a hybrid

* **Claim:** The deterministic solver-first runtime can interoperate with an external LLM (Ollama, Anthropic, OpenAI) as a fallback layer. The architecture is provider-pluggable.
* **Evidence:** `waggledance/adapters/llm/ollama_adapter.py` (real adapter); `waggledance/adapters/llm/dry_run_stub.py` (always-on offline stub); the solver router has a documented fallback step *after* solvers + specialists. Phase 11–17A proofs explicitly do not invoke the LLM lane (provider delta = 0). Phase 17B harness includes an **optional** Ollama latency probe (`scenario F`) for the local-only LLM round-trip; default behavior is `SKIPPED` for `--network none` safety. To measure the local hybrid one explicitly opts in via `--include-ollama`. The probe never pulls or downloads a model (master prompt rule 14).
* **Label:** **INFERRED** for the architecture; **MEASURED-IF-OPTED-IN-LOCALLY** for Ollama latency. The hybrid accuracy delta was not measured this session.
* **Strengthening path:** run the same external reasoning benchmark twice — once with the LLM fallback enabled, once disabled — and publish both `coverage` (fraction served by deterministic solver) and the joint accuracy delta.

### K. Industrial / factory / capsule readiness

* **Claim:** The capsule layer carries factory-class profiles (factory, cottage, home, gadget, personal, research) and the runtime is configurable for resource-constrained edges.
* **Evidence:** `agents/*` capsule definitions; `waggledance/core/capsules/capsule_registry.py`; hardware presets table in `CURRENT_STATE.md`. No external industrial deployment has been benchmarked in this session.
* **Label:** **INFERRED.** The architecture supports the claim; no production deployment data has been published.
* **Strengthening path:** publish a deployment report from any operational fleet running these capsules.

### L. Edge resource use

* **Claim:** The autonomy inner loop has no torch / faiss / playwright dependency at runtime (the Phase 16F image dropped them); the production image is `python:3.13-slim` + `requirements-ci.txt`, ~3.09 GB.
* **Evidence:** `Dockerfile` (Phase 16F switch to `requirements-ci.txt`); `docker images waggledance:phase16f` reports 3.09 GB. CPU-only Linux torch is still pulled because `chromadb`/`transformers` need it; for true edge deploys these can be dropped further. Inference latency on a 24-CPU host: p50 lookup ~4 ms at 10k descriptors.
* **Label:** **MEASURED** image size; **INFERRED** edge fitness.
* **Strengthening path:** publish a Raspberry Pi 4 / Pi 5 build measurement with the same proofs running.

### M. Autonomous learning lane

* **Claim:** The autogrowth scheduler runs end-to-end without a human trigger across the six low-risk families: gap signal → growth intent → autogrowth queue → low-risk grower → auto-promotion → capability-aware dispatch.
* **Evidence:** Phase 12 mass-safe proof (30 promotions / 6 families / 8 cells / 0 provider calls); Phase 13 runtime harvest proof; Phase 14 hot-path cache proof; Phase 15/16A live runtime hint and upstream structured-request proofs; Phase 16B full-corpus restart with auto_promotions=104; Phase 17A producer fabric + 128-seed corpus.
* **Label:** **PROVEN** within the bounded six-family allowlist; **NOT CLAIMED** for high-risk families.

### N. High-risk safety gate

* **Claim:** Six high-risk autonomy variants (parallel ensembles, predictive cache preheat, unbounded micro-learning, canary auto-promotion, advanced local model escalation, generative memory compression) are explicitly blocked. Documented per-blocker conditions.
* **Evidence:** `docs/architecture/HIGH_RISK_VARIANTS_DEFERRED.md`; Phase 17A producer-fabric proof negative cases assert HUMAN_APPROVAL collection in offline build/proof is rejected, Stage-2 atomic flip in build/proof is rejected, family outside the six-allowlist is rejected.
* **Label:** **PROVEN** as a refusal contract; **NOT CLAIMED** that all conceivable risk modes are catalogued.

## Summary

| Axis | Label |
|---|---|
| A. Deterministic routing | PROVEN |
| B. Audit / provenance / replay | PROVEN |
| C. Zero-provider inner loop | PROVEN |
| D. Docker offline `--network none` | PROVEN |
| E. Restart continuity | PROVEN |
| F. Producer fabric | PROVEN this session |
| G. 10k solver capability scale | MEASURED + PROVEN-data-path (Phase 17A + 17B re-measure) |
| H. Canonical seed corpus size | PROVEN (128) |
| I. Raw intelligence vs frontier MoE | **NOT CLAIMED** |
| J. LLM / MoE hybrid | INFERRED |
| K. Industrial / factory readiness | INFERRED |
| L. Edge resource use | MEASURED (image size); INFERRED (Pi class) |
| M. Autonomous learning lane | PROVEN within six-family allowlist |
| N. High-risk safety gate | PROVEN as refusal contract |

## What is NOT in this matrix (master prompt rule 16)

* No "WaggleDance beats GPT-X / Claude-X / Gemini-X on benchmark Y." We have not run such a benchmark.
* No comparison of frontier MoE accuracy on free-form reasoning. NOT CLAIMED.
* No claim that the inner loop *replaces* an LLM. The inner loop is solver-first; the LLM is a documented fallback for queries the solver path cannot answer.
* No marketing language: "revolutionary", "magical", "human-like mind", "self-aware", "explosive intelligence", "emergent" — none used.
* No consciousness claim. None.

## How to extend

For any axis currently labelled NOT CLAIMED or INFERRED:

1. Add a benchmark or proof script under `tools/` that produces a JSON artifact.
2. Add an integration test under `tests/` that asserts the artifact's invariants.
3. Update this matrix to upgrade the label to MEASURED or PROVEN.
4. Reference the reproduction command in the matrix entry.
5. Open a PR. Do not change another row's label without adding new evidence.
