# Competitive Evidence Matrix — 2026-Q2

**Status:** Phase 17A + 17B + 17C + 17D + 18A + 18B + 18C + 18E + 18F snapshot, derived from reproducible artifacts only.
**Evidence snapshot date:** 2026-05-06
**Freshness audit:** 2026-06-11 read-only audit, following the 2026-06-06 and 2026-05-27 read-only audits, found the dated PROVEN/MEASURED evidence older than the dream-mode <=14-day freshness target. On 2026-06-11, the referenced evidence bands were 36-38 days old. Labels below remain historical evidence labels until refreshed; these audits do not upgrade or invalidate any row.
**Freshness metadata:** `snapshot_date=2026-05-06`; `freshness_audit_date=2026-06-11`; `prior_freshness_audit_date=2026-06-06`; `max_age_days=14`; `status=historical_stale`; `fresh_for_planning=false`; `priority_rows=G,J,L`; `historical_labels_until_refreshed=true`.
**Branch lineage:** `phase18f/incremental-gap-replay` (Phase 18F at `c1ddded1`; 18E at `6c6ca859`; 18C at `e9aa1de1`; 18B at `b408b14a`; 18A at `4554b24a`; 17D at `d0704efe`; 17C at `db5d7db1`; 17B at `f4d0a4a4`; 17A on `main` at `c726995c`)
**Anchor:** `v3.10.4-incremental-gap-replay-alpha` candidate (PRERELEASE only; v3.8.0 remains GitHub Latest; v3.9.0/v3.9.1/v3.9.2/v3.9.3/v3.10.0/v3.10.1/v3.10.2/v3.10.3 alphas remain the previous Pre-releases).
**New evidence since the prior matrix header (Phase 18F):** the runtime-gap replay path is now cursor-incremental on the existing `runtime_gap_signals` table with replay state in `schema_meta`, no schema change, no allowlist widening, no new dispatcher, and no model/cloud/builder calls. The Phase 18F proof records cursor advancement, no-op replay idempotency, post-cursor learning in all six allowlisted families, strict malformed-row rejection, RuntimeGapDetector bridge validation, and `LOCKED_NOT_RUN` concurrency behavior. Release decision evidence reports Phase 18F targeted tests 46/46 PASS, targeted carry-forward suite 297/297 PASS, Docker `--network none` Phase 18F + 18E + 18C + 18B + 18A verification PASS, and `release_gate_pass = true`. Axis M remains the only row advanced by Phase 18E/18F. Detailed run reports: `docs/benchmarks/RUNTIME_GAP_REPLAY_2026.md`, `docs/benchmarks/INCREMENTAL_RUNTIME_GAP_REPLAY_2026.md`, and `docs/runs/phase18f_incremental_gap_replay_2026_05_06/release_decision.md`.

This is an **engineering** document. It does not market WaggleDance. It enumerates the comparison axes most often used to assess local-first cognitive runtimes, states one factual claim per axis, points to a reproducible artifact in this repo, and labels the claim with one of:

| Label | Meaning |
|---|---|
| **PROVEN** | A test or proof in this repo asserts the claim and exits 0. |
| **MEASURED** | A reproducible measurement was taken in this session. The number is real but the claim it supports is conditional. |
| **INFERRED** | The claim follows from architecture but no benchmark was run in this session. |
| **NOT CLAIMED** | This repo does not assert the claim. Any external comparison would need a benchmark we have not run. |

WaggleDance does **not** claim to be conscious, sentient, aware, alive, or AGI. The autonomy mechanisms in this release are bounded engineering primitives, each mapped to a code path, persisted event, replayable proof, metric delta, and regression test.

WaggleDance does **not** claim to "beat all competitors." This document does not rank rivals. It records what *we* have proven about *this* runtime, in this branch, in this session, against artifacts that anyone can reproduce.

## Freshness notes

The dream-mode agenda targets <=14-day staleness for PROVEN/MEASURED competitor-matrix rows. On the 2026-05-27 audit, the direct proof and measurement artifacts referenced here were 21-23 days old: Phase 17A/17B/17C mostly 2026-05-04, Phase 17D/18A/18B/18C mostly 2026-05-05, and Phase 18E/18F 2026-05-06.

The 2026-06-06 bridge audit rechecked the same snapshot and found those evidence bands 31-33 days old. The 2026-06-11 bridge audit rechecked the same snapshot and found those evidence bands 36-38 days old. These are staleness markers only, not reruns, carry-forward proofs, label upgrades, or row invalidations.

The 2026-06-11 audit also found current V12 non-upgrading refresh inputs outside this matrix: `tools/build_v12_ingredient_coverage_rollup.py` reports 4/4 ingredients ok with claim label `MEASURED_LOCAL_PARTIAL`; `tools/run_v12_memory_palace_shortcut_proof.py` reports `MEASURED_LOCAL_PROJECTION` as a projection-only, read-side shortcut candidate; and `tools/run_v12_competitive_triad_simulation.py` exits ok while keeping `consensus_grade=false` and explicitly not acting as a competitor benchmark. These artifacts can seed a future refresh PR, but they do not change the labels below.

Refresh priority is: measured rows G, J, and L first; then PROVEN rows A-H, M, N, and O via targeted reruns or explicit carry-forward evidence. The 2026-05-20/21 competitor-axis pilot artifacts are fresher but are not yet integrated into these matrix labels.

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
* **Evidence:** `waggledance/adapters/llm/ollama_adapter.py` (real adapter); `waggledance/adapters/llm/dry_run_stub.py` (always-on offline stub); the solver router has a documented fallback step *after* solvers + specialists. Phase 11–17A proofs explicitly do not invoke the LLM lane (provider delta = 0). Phase 17B introduced an optional Ollama latency probe (`scenario F`) but defaulted to `SKIPPED` for `--network none` safety. Phase 17C upgraded that probe to a measured 30-prompt deterministic baseline against **one** already-installed local model (`gemma4:e4b`). **Phase 17D extends the probe to a panel of N already-installed local models with R repeats per model** — see `tools/run_phase17d_local_model_sweep.py` and `docs/benchmarks/LOCAL_OLLAMA_MODEL_SWEEP_2026.md`. The probe still never pulls a model and never calls a cloud API; subprocess output is scanned for pull/download substrings and the harness aborts on a hit.
* **Measured Phase 17C (host run, gemma4:e4b only, 30 deterministic prompts):** `prompts_succeeded = 30 / 30`, `median_latency_seconds = 0.7866`, `p95_latency_seconds = 17.5538`, `mean_latency_seconds = 2.5539`, `total_seconds = 76.6193`.
* **Measured Phase 17D (host run, 4-model panel, 3 repeats, 30 prompts each = 360 prompts total):**
  * `gemma4:e4b` (9.6 GB): 90 / 90 ok, p50 / p95 / p99 = 784.7 / 18328.7 / 20415.8 ms, mean 2661.2 ms, stddev 4888.2 ms, CoV 0.0285.
  * `gemma3:4b` (3.3 GB): 90 / 90 ok, p50 / p95 / p99 = 711.2 / 863.4 / 4777.1 ms, mean 776.0 ms, stddev 427.9 ms, CoV 0.0022.
  * `llama3.2:3b` (2.0 GB): 90 / 90 ok, p50 / p95 / p99 = 526.6 / 2958.5 / 4072.5 ms, mean 986.2 ms, stddev 876.8 ms, CoV 0.0038.
  * `phi4-mini:latest` (2.5 GB): 90 / 90 ok, p50 / p95 / p99 = 549.3 / 3411.2 / 6364.9 ms, mean 1038.7 ms, stddev 1170.7 ms, CoV 0.0140.
  * Panel-level: `release_gate_pass = true`, `forbidden_claims_absent = true`, `provider_jobs_delta = builder_jobs_delta = 0`, `no_model_pull_or_download = true`, `no_cloud_api_calls = true`.
* **Label:** **INFERRED** for the architecture; **MEASURED-LOCAL-OLLAMA-PANEL** for the 4-model panel latency this session. The hybrid accuracy delta was not measured this session. **No cross-vendor ranking is implied** — every per-model number is reported in isolation; the harness's MD scrub blocks "is faster than" / "outperforms" / "beats" / "better than" / "ranks higher" substrings from the rendered prose.
* **Strengthening path:** run the same external reasoning benchmark twice — once with the LLM fallback enabled, once disabled — and publish both `coverage` (fraction served by deterministic solver) and the joint accuracy delta. Add the deferred large local models (`gemma4:26b`, `qwen2.5:32b`, `osoderholm/poro:latest`) to the sweep via `--prefer-larger-models`. Add ARM/edge measurements via `docker buildx build --platform linux/arm64`.

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
* **Evidence:** Phase 12 mass-safe proof (30 promotions / 6 families / 8 cells / 0 provider calls); Phase 13 runtime harvest proof; Phase 14 hot-path cache proof; Phase 15/16A live runtime hint and upstream structured-request proofs; Phase 16B full-corpus restart with auto_promotions=104; Phase 17A producer fabric + 128-seed corpus. **Phase 18B closes the runtime-gap-mining feedback half of the loop**: structured runtime gap signals → six-element verdict enum (allowlisted solver spec / insufficient evidence / out-of-family rejected / high-risk rejected / builder-handoff quarantined / duplicate suppressed). New mainline modules at `waggledance/core/autonomy_growth/gap_mining.py` + `gap_candidate.py`; proof harness at `tools/run_phase18b_gap_miner_feedback_proof.py`; 19 unit tests; Docker `--network none` PASS; `provider_jobs_delta = builder_jobs_delta = 0`; allowlist unchanged.
* **Reproduce:** `python tools/run_phase18b_gap_miner_feedback_proof.py`. Expected: 30 signals → 14 candidates → 6 allowlisted specs + 3 insufficient + 2 out-of-family + 1 high-risk + 1 builder-handoff + 1 duplicate; `release_gate_pass = true`.
* **Phase 18C closes the runtime-dispatch half:** mined ALLOWLISTED specs are registered into the real `ControlPlaneDB` via the canonical Phase 17A four-step pattern (`upsert_solver_family` → `upsert_solver(status='auto_promoted')` → `set_solver_capability_features` → `upsert_solver_artifact`) and served through the real `LowRiskSolverDispatcher.dispatch_by_features()`. New mainline module `waggledance/core/autonomy_growth/mined_solver_runtime.py`; proof harness `tools/run_phase18c_mined_solver_runtime_dispatch_proof.py`; 33 unit tests. Host run: 6 ALLOWLISTED candidates → 6 registered auto-promoted solvers → 18/18 dispatch cases hit (3 per family × 6 families) via capability-aware path. Non-allowlisted verdicts (insufficient evidence, out-of-family, high-risk, builder-handoff, duplicate) are rejected from registration; never become executable.
* **Reproduce (Phase 18C):** `python tools/run_phase18c_mined_solver_runtime_dispatch_proof.py`. Expected: `registered_solver_count = 6`, `dispatch_success_count = 18`, `dispatch_failure_count = 0`, `families_covered = 6`; `release_gate_pass = true`; `provider_jobs_delta = builder_jobs_delta = 0`.
* **Phase 18E/18F productionize replay:** Phase 18E persists content-keyed runtime-gap events into the existing `runtime_gap_signals` table and reuses the Phase 18B miner + Phase 18C registration/dispatch path. Phase 18F adds cursor-based incremental replay, no-op replay idempotency, post-cursor learning in all six allowlisted families, strict malformed-row rejection, RuntimeGapDetector bridging, and logical lock behavior. It keeps `schema_version = 4`, adds no event table, widens no allowlist, and does not execute builder handoff or high-risk variants.
* **Reproduce (Phase 18F):** `python -X utf8 tools/run_phase18f_incremental_gap_replay_proof.py --out-dir docs/runs/phase18f_incremental_gap_replay_2026_05_06`. Expected: first replay 32 new events and 6 registered solvers, no-op replay creates 0 extra rows, third replay processes 12 appended events and registers 6 more solvers, total registered solver count >=12, malformed/type-confused/forbidden rows rejected, `lock_result = "LOCKED_NOT_RUN"`, `release_gate_pass = true`, `provider_jobs_delta = builder_jobs_delta = 0`.
* **Label:** **PROVEN with persisted, idempotent, cursor-incremental runtime-gap replay, RuntimeGapDetector bridge, measured feedback loop, and runtime dispatch of mined solver specs within six-family allowlist** (Phase 18F). **NOT CLAIMED** for high-risk families. Builder-handoff lane is **PROVEN as a quarantined contract**, **NOT CLAIMED as automatic builder promotion**.

### N. High-risk safety gate

* **Claim:** Six high-risk autonomy variants (parallel ensembles, predictive cache preheat, unbounded micro-learning, canary auto-promotion, advanced local model escalation, generative memory compression) are explicitly blocked. Documented per-blocker conditions.
* **Evidence:** `docs/architecture/HIGH_RISK_VARIANTS_DEFERRED.md`; Phase 17A producer-fabric proof negative cases assert HUMAN_APPROVAL collection in offline build/proof is rejected, Stage-2 atomic flip in build/proof is rejected, family outside the six-allowlist is rejected.
* **Label:** **PROVEN** as a refusal contract; **NOT CLAIMED** that all conceivable risk modes are catalogued.

### O. Benchmark artifact externalization / schema validation

* **Claim:** Phase 17B / 17C / 17D benchmark artifacts are exportable as a versioned, machine-readable bundle with strict JSON Schemas, claim ledger, release lineage, SHA-256 checksums, and a stdlib-only validator.
* **Evidence:** Phase 18A bundle at `docs/runs/phase18a_benchmark_externalization_2026_05_05/export_bundle/`. 7 schema files under `schemas/benchmarks/v1/`. Exporter at `tools/run_phase18a_benchmark_externalization.py`. Validator at `tools/validate_phase18a_benchmark_bundle.py` (no `jsonschema` pip dependency). 15-test unit suite at `tests/benchmarks/test_phase18a_benchmark_externalization.py` covering happy path + adversarial fixtures (missing file, checksum mismatch, unknown label, unresolved evidence, raw stdout leakage, ranking-substring injection, provider-delta drift). Docker `--network none` export + validate exits 0.
* **Reproduce:** `python tools/run_phase18a_benchmark_externalization.py --validate` then `python tools/validate_phase18a_benchmark_bundle.py --bundle-dir docs/runs/phase18a_benchmark_externalization_2026_05_05/export_bundle`.
* **Label:** **PROVEN this session.**
* **Strengthening path:** publish the bundle as a release asset on the v3.10.0-benchmark-schema-alpha GitHub release; add a CI job that re-runs `--validate` on every PR.

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
| J. LLM / MoE hybrid | INFERRED (architecture); MEASURED-LOCAL-OLLAMA-PANEL (Phase 17D, 4-model panel × 3 repeats) |
| K. Industrial / factory readiness | INFERRED |
| L. Edge resource use | MEASURED (image size); INFERRED (Pi class) |
| M. Autonomous learning lane | PROVEN with persisted, idempotent, cursor-incremental runtime-gap replay, RuntimeGapDetector bridge, measured feedback loop, and runtime dispatch of mined solver specs within six-family allowlist (Phase 18F); NOT CLAIMED for high-risk families; builder-handoff PROVEN as quarantined contract, NOT CLAIMED as automatic builder promotion |
| N. High-risk safety gate | PROVEN as refusal contract |
| O. Benchmark artifact externalization / schema validation | PROVEN this session (Phase 18A bundle + stdlib-only validator + 15 tests + Docker `--network none`) |

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
6. If a PROVEN/MEASURED row is older than the dream-mode freshness target, refresh it with a rerun or mark the staleness explicitly before using it for planning.
