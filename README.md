# WaggleDance

> Local-first deterministic-solver runtime with a bounded six-family auto-growth lane. Alpha. No cloud, no provider in the inner loop.

**v3.8.0 stable (Phase 16F, released 2026-05-04):** the first stable release of WaggleDance. Docker stable-gate verified end-to-end with `--network none` at corpus 104; all four canonical proofs and the 4-file autonomy_growth smoke suite pass inside the rebuilt `waggledance:v3.8.0-rc` image and on a post-merge fresh clone from GitHub HTTPS. `provider_jobs_delta = builder_jobs_delta = 0` across every proof and across the full DB close+reopen restart. Bandit HIGH = 0; pip-audit: 32 CVEs in 14 packages, all `low`, none reachable from inner loop. Local 3-iter proof soak: 9/9, no flakes. The image is `python:3.13-slim` + `requirements-ci.txt` (3.09 GB). **GitHub Latest is v3.8.0**.

**v3.9.0-producer-fabric-alpha (Phase 17A, PRERELEASE):** producer fabric on main + 10k synthetic capability lookup proven `--network none`. 14 producer modules ported from Phase 8.5 (4,511 LOC stdlib only); orchestrator emits 68 IR objects across 6 kinds; 10000 descriptors / 1000 capability hits / 0 FIFO fallback / 0 miss; canonical seed corpus grew 104 → 128. Pre-release entry on GitHub; v3.8.0 unchanged. Released by PR #71 (squash-merge `c726995c`).

**v3.9.1-local-efficiency-benchmark-alpha (Phase 17B, PRERELEASE):** reproducible local AI efficiency benchmark harness `tools/run_phase17b_local_efficiency_benchmark.py` aggregating the existing Phase 11–17A canonical proofs into a single artifact with the master-prompt-mandated metric set (correctness, latency p50/p95/p99, fallback rate, provider/builder delta, audit/provenance coverage, claim labels). Optional Ollama latency probe (default skipped for `--network none` safety; opt-in via `--include-ollama` against an already-present model). Documented NOT_RUN slots for six external competitors (Anthropic, OpenAI, Gemini, llama.cpp, vLLM, mistral-rs). On the host run: `release_gate_pass = true`, capability-lookup p50/p95/p99 = 4.33 / 10.98 / 14.39 ms, 1000/1000 hits via the real `auto_promoted_solver` source, provider/builder delta totals = 0/0. Raw intelligence vs frontier MoE is **NOT CLAIMED**. Released by PR #73 (squash-merge `f4d0a4a4`). Pre-release entry on GitHub; v3.8.0 remains GitHub Latest. v3.9.0-producer-fabric-alpha remains the previous Pre-release.

**v3.9.2-local-ollama-baseline-alpha (Phase 17C, PRERELEASE):** upgrades the Phase 17B optional Ollama track from `SKIPPED_OPTIONAL` to a **MEASURED** 30-prompt deterministic baseline against one already-installed local model. New harness `tools/run_phase17c_local_ollama_baseline.py` wraps the Phase 17B aggregator (Tracks A–E pass-through) and adds Track F. Selection follows rule-14 preference order (`gemma4:e4b` first); the harness never pulls a model and never calls a cloud API. Host run: model `gemma4:e4b`, ollama 0.22.1, `prompts_succeeded = 30 / 30`, `median_latency_seconds = 0.7866`, `p95_latency_seconds = 17.5538`, `mean_latency_seconds = 2.5539`, `total_seconds = 76.6193`, `release_gate_pass = true`, `forbidden_claims_absent = true`, provider/builder delta = 0/0. Docker `--network none` exits 0 with `NOT_AVAILABLE_NOT_RUN` under `--allow-no-ollama-track`. Competitive evidence matrix axis J upgrades to `MEASURED-LOCAL-OLLAMA-ONE-MODEL`; raw intelligence vs frontier MoE remains **NOT CLAIMED**. Released by PR #75 (squash-merge `db5d7db1`); GitHub release published with `isPrerelease=true` at 2026-05-04T22:26:28Z. Pre-release entry on GitHub; v3.8.0 remains GitHub Latest. v3.9.0 + v3.9.1 alphas remain Pre-release.

**v3.9.3-local-model-sweep-alpha (Phase 17D, PRERELEASE):** extends the Phase 17C single-model probe to a **panel** of N already-installed local models with R repeats per model. New harness `tools/run_phase17d_local_model_sweep.py` reuses Phase 17C primitives (prompt manifest, decoder, forbidden-vocabulary scrub) and adds a pull/download abort gate (5 substring signatures) plus a ranking-guard scrub (8 substrings: "is faster than", "outperforms", etc.). Host run: 4 models × 3 repeats × 30 prompts = **360 / 360 prompts succeeded**, coefficient of variation across per-repeat medians 0.002–0.029 (high stability), p50 panel spread 526.6–784.7 ms, `release_gate_pass = true`, `forbidden_claims_absent = true`, `provider_jobs_delta = builder_jobs_delta = 0`, `no_model_pull_or_download = true`, `no_cloud_api_calls = true`. Docker `--network none` carry-forward exits 0. Competitive evidence matrix axis J upgrades to `MEASURED-LOCAL-OLLAMA-PANEL`; raw intelligence vs frontier MoE remains **NOT CLAIMED**; **no cross-vendor ranking is implied** — every per-model number is reported in selection order. Released by PR #77 (squash-merge `d0704efe`); GitHub release published with `isPrerelease=true` at 2026-05-05T06:05:30Z. Pre-release entry on GitHub; v3.8.0 remains GitHub Latest. v3.9.0 + v3.9.1 + v3.9.2 alphas remain Pre-release.

**v3.10.0-benchmark-schema-alpha (Phase 18A, PRERELEASE):** externalizes Phase 17B / 17C / 17D benchmark artifacts as a versioned, validated, offline-exportable evidence bundle. Adds **7 JSON Schema files** under `schemas/benchmarks/v1/`, a **stdlib-only validator** (`tools/validate_phase18a_benchmark_bundle.py`, no `jsonschema` pip dependency, ~330 LOC), an **exporter** (`tools/run_phase18a_benchmark_externalization.py`, ~470 LOC), a **16-claim machine-readable ledger** with explicit `NOT_CLAIMED` entries for raw-intelligence superiority and cross-vendor ranking, **release lineage** hard-coded to `v3.8.0` GitHub Latest + the 4 v3.9.x alpha prereleases, **SHA-256 checksums** for every bundle file, and **15 tests** covering happy path + adversarial fixtures (missing file, checksum mismatch, unknown label, unresolved evidence, raw stdout leakage, ranking-substring injection, provider-delta drift, deterministic export). Host run: bundle exported, `release_gate_pass = true`, validator PASS, all gates green. Docker `--network none` export + validate exits 0. Competitive evidence matrix gains new axis O "Benchmark artifact externalization / schema validation" labelled **PROVEN this session**. Phase 18A re-exports existing committed evidence — no new measurements, no model pull/download, no cloud API call, no allowlist widening, no autonomy code change. Released by PR #79 (squash-merge `4554b24a`); GitHub release published with `isPrerelease=true` at 2026-05-05T07:20:31Z. Pre-release entry on GitHub; v3.8.0 remains GitHub Latest. v3.9.0 + v3.9.1 + v3.9.2 + v3.9.3 alphas remain Pre-release.

**v3.10.1-gap-miner-feedback-alpha (Phase 18B, PRERELEASE):** closes the runtime-gap-mining feedback half of the autonomous learning loop on main inside the bounded six-family allowlist. Adds **`waggledance/core/autonomy_growth/gap_mining.py`** + **`gap_candidate.py`** (stdlib-only, no new pip dependency) implementing a six-element verdict enum (`ALLOWLISTED_SOLVER_SPEC`, `INSUFFICIENT_EVIDENCE`, `OUT_OF_FAMILY_REJECTED`, `HIGH_RISK_REJECTED`, `BUILDER_HANDOFF_QUARANTINED`, `DUPLICATE_SUPPRESSED`) over structured runtime gap signals. Deterministic SHA-256-derived `candidate_id` from family + canonical features. New proof harness `tools/run_phase18b_gap_miner_feedback_proof.py` drives a 30-signal synthetic fixture: 30 → 14 candidates → 6 allowlisted solver specs + 3 insufficient + 2 out-of-family + 1 high-risk + 1 builder-handoff + 1 duplicate. 19 unit tests. Builder handoff is a quarantined contract with `no_auto_promotion = true`, `no_provider_call = true`, `no_builder_call_in_proof = true`, `no_cloud_api = true`, `promotion_allowed = false` — no live builder execution, no autonomy of high-risk families. `release_gate_pass = true`, `forbidden_claims_absent = true`, `provider_jobs_delta = builder_jobs_delta = 0`, `allowlist_unchanged = true`, `no_stage2_flip = no_human_approval = true`. Docker `--network none` PASS for both the gap-miner proof and the carry-forward Phase 18A validator. Also fixes a Phase 18A bundle EOL portability defect: exporter writes LF-only bytes; `.gitattributes` declares the bundle subtree `binary`; validator normalizes CRLF→LF before SHA-256 hashing — so checksums verify on any platform regardless of working-tree EOL state. Competitive evidence matrix axis M upgrades to "PROVEN with measured runtime-gap feedback loop within six-family allowlist"; raw intelligence vs frontier MoE remains **NOT CLAIMED**; cross-vendor ranking remains **NOT CLAIMED**. Released by PR #81 (squash-merge `b408b14a`); GitHub release published with `isPrerelease=true` at 2026-05-05T14:32:46Z. Pre-release entry on GitHub; v3.8.0 remains GitHub Latest. v3.9.0 + v3.9.1 + v3.9.2 + v3.9.3 + v3.10.0 alphas remain Pre-release.

**v3.10.4-incremental-gap-replay-alpha (Phase 18F, CANDIDATE):** upgrades Phase 18E's whole-corpus durable replay into a **cursor-based incremental** learning loop. New mainline module `waggledance/core/autonomy_growth/incremental_gap_replay.py` (~580 LOC, stdlib + WaggleDance only) ships `read_replay_cursor` / `write_replay_cursor` / `acquire_replay_lock` / `release_replay_lock` / `load_runtime_gap_events_after_id` (strict + counted-skip) / `bridge_detector_signal_to_phase18e_event` / `persist_detector_gap_signals_as_replay_events` / `run_incremental_gap_replay_once`. Cursor + lock live in the existing `schema_meta` table with phase18f-prefixed keys; events stay in `runtime_gap_signals` under `kind = phase18e.runtime_gap_event.v1`. **No schema change** — no `ALTER TABLE`, no new column, no new table. The Phase 12 `RuntimeGapDetector` write path is untouched; Phase 18F adds a strict adapter on top. New proof harness `tools/run_phase18f_incremental_gap_replay_proof.py` runs 10 stages (seed → first replay → no-op → append → post-cursor replay → post-cursor no-op → malformed/type-confused → detector bridge → concurrency lock → release gate). 46 unit tests. Phase 18C `_COMPILATION_TABLE` extended with 6 new strict per-family rules (one new feature_dict per family) so post-cursor events register as new solvers — **no allowlist widening, no new family_kind**. Host run: 32 seed events; first replay 32→6 solvers/6 families/18 dispatch hits/cursor advances; no-op 0/0/0; 12 post-cursor events appended; third replay 12→6 new solvers/18 new dispatch hits; total 12 registered solvers; 4 type-confused payloads injected, 3 type_confusion + 1 malformed rejected at load; detector bridge persists 1, rejects 2; held-lock test returns `LOCKED_NOT_RUN` with 0 duplicates. `release_gate_pass = true`, `forbidden_claims_absent = true`, `provider_jobs_delta = builder_jobs_delta = 0`, `allowlist_unchanged = true`. Docker `--network none` PASS for Phase 18F + Phase 18E + 18C + 18B + 18A validator (5/5 exit 0). Competitive evidence matrix axis M upgrades to "PROVEN with persisted, idempotent, cursor-incremental runtime-gap replay, RuntimeGapDetector bridge, measured feedback loop, and runtime dispatch of mined solver specs within six-family allowlist"; raw intelligence vs frontier MoE remains **NOT CLAIMED**; cross-vendor ranking remains **NOT CLAIMED**. Candidate to be tagged from the Phase 18F PR squash-merge SHA. v3.8.0 remains GitHub Latest; v3.9.0 + v3.9.1 + v3.9.2 + v3.9.3 + v3.10.0 + v3.10.1 + v3.10.2 + v3.10.3 alphas remain Pre-release.

**v3.10.3-runtime-gap-replay-alpha (Phase 18E, PRERELEASE):** released 2026-05-06T05:42:51Z from PR #86 (squash-merge `6c6ca859`). GitHub release `isPrerelease=true`. Proves the **durable** autonomous-learning loop end-to-end. New mainline module `waggledance/core/autonomy_growth/runtime_gap_replay.py` persists Phase 18B-shaped runtime gap events as content-keyed rows in the existing `runtime_gap_signals` table with `kind = phase18e.runtime_gap_event.v1` (no schema change, no new column, no `ALTER TABLE`); `load_runtime_gap_events` reads them back; `replay_persisted_gap_events` runs them through Phase 18B `mine_runtime_gaps` and Phase 18C `register_mined_solver_specs` verbatim; the registered solvers serve through the real `LowRiskSolverDispatcher.dispatch_by_features` with `reason = "hit_by_features"`. New proof harness `tools/run_phase18e_runtime_gap_replay_proof.py`; 48 unit tests. Host run: 32 events persisted (3 malformed + 1 forbidden-field rejected at normalization), 32 loaded back, 13 candidates with the canonical 6/3/1/1/1/1 verdict distribution, **6 registered auto-promoted solvers**, **18/18 deterministic dispatch cases hit** through the capability-aware path, **6/6 families covered**. Idempotent re-replay: second persist inserted 0 (skipped 32 existing); second replay added 0 extra solvers / 0 capability features / 0 artifacts. `release_gate_pass = true`, `forbidden_claims_absent = true`, `provider_jobs_delta = builder_jobs_delta = 0`, `allowlist_unchanged = true`. Docker `--network none` PASS for Phase 18E proof + Phase 18C / 18B / 18A carry-forward (4/4 exit 0). Builder handoff still quarantined. Competitive evidence matrix axis M upgrades to "PROVEN with persisted runtime-gap replay, measured runtime-gap feedback loop, AND runtime dispatch of mined solver specs within six-family allowlist"; raw intelligence vs frontier MoE remains **NOT CLAIMED**; cross-vendor ranking remains **NOT CLAIMED**. v3.8.0 remains GitHub Latest; v3.9.0 + v3.9.1 + v3.9.2 + v3.9.3 + v3.10.0 + v3.10.1 + v3.10.2 + v3.10.3 alphas remain Pre-release.

**v3.10.2-mined-solver-dispatch-alpha (Phase 18C, PRERELEASE):** closes the explicit Phase 18B gap (`capability_lookup_status = NOT_RUN_OUT_OF_PHASE18B_SCOPE`) by registering Phase 18B mined ALLOWLISTED low-risk solver specs into the **real** `ControlPlaneDB` via the canonical Phase 17A four-step pattern (`upsert_solver_family` → `upsert_solver(status='auto_promoted')` → `set_solver_capability_features` → `upsert_solver_artifact`) and dispatching them through the **real** `LowRiskSolverDispatcher.dispatch_by_features()`. New mainline module **`waggledance/core/autonomy_growth/mined_solver_runtime.py`** (~310 LOC, stdlib + WaggleDance only, no new pip dependency) with `compile_mined_spec_to_runtime_artifact(spec)`, `register_mined_solver_specs(*, candidates, control_plane)`, and `RegistrationSummary`. Per-family compilation table covers exactly the six Phase 18B fixture shapes; novel `(family_kind, feature_dict)` signatures fail closed with `RuntimeArtifactCompilationError`. New proof harness `tools/run_phase18c_mined_solver_runtime_dispatch_proof.py`; 33 unit tests. Host run: 6 ALLOWLISTED candidates → **6 registered auto-promoted solvers**, **18/18 deterministic dispatch cases hit** (3 per family × 6 families) via capability-aware path with `reason="hit_by_features"`, **8 non-allowlisted verdicts rejected** from registration. `release_gate_pass = true`, `forbidden_claims_absent = true`, `provider_jobs_delta = builder_jobs_delta = 0`, `allowlist_unchanged = true`, `no_stage2_flip = no_human_approval = no_high_risk_autonomy = no_live_builder_execution = true`. Docker `--network none` PASS for Phase 18C proof + Phase 18B carry-forward + Phase 18A bundle validation. Builder handoff remains quarantined; zero solver rows for builder-handoff candidates; no live builder execution. Competitive evidence matrix axis M upgrades to "PROVEN with measured runtime-gap feedback loop AND runtime dispatch of mined solver specs within six-family allowlist"; raw intelligence vs frontier MoE remains **NOT CLAIMED**; cross-vendor ranking remains **NOT CLAIMED**. Released by PR #83 (squash-merge `e9aa1de1`); GitHub release published with `isPrerelease=true` at 2026-05-05T18:44:19Z. Pre-release entry on GitHub; v3.8.0 remains GitHub Latest. v3.9.0 + v3.9.1 + v3.9.2 + v3.9.3 + v3.10.0 + v3.10.1 alphas remain Pre-release.

**Still alpha / not implemented:** real Anthropic / OpenAI HTTP adapters (only `dry_run_stub` and `claude_code_builder_lane` exercisable end-to-end); Stage-2 atomic flip (specified in `docs/architecture/STAGE2_CUTOVER_RFC.md` but not executed); actuator-side autonomy; federation; high-risk family auto-promotion; HTTP `/api/autonomy/query` route (the FastAPI route surface for query is not exposed; v3.8.0 is library/service-layer-stable, not HTTP-API-stable); production-grade Docker deployment story (see `docs/deployment/DOCKER_QUICKSTART.md`).

**Consciousness?** No. The autonomy mechanisms here are engineering primitives (auto-growth, runtime harvest, capability-aware dispatch, hot-path cache), each mapped to a code path, persisted event, and regression test. WaggleDance does not claim to be conscious, sentient, aware, alive, or AGI. See `docs/github/REPOSITORY_PRESENTATION.md` for the external presentation summary and `docs/release/RELEASE_READINESS.md` for the alpha/release tag policy.

[![Tests](https://img.shields.io/badge/tests-Phase%2016F%20targeted%20green-brightgreen)]()
[![Docker](https://img.shields.io/badge/docker-Phase%2016F%20stable--gate%20PASS%20(--network%20none)-brightgreen)]()
[![CI](https://github.com/Ahkeratmehilaiset/waggledance-swarm/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Ahkeratmehilaiset/waggledance-swarm/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)]()
[![License](https://img.shields.io/badge/license-Apache%202.0%20%2B%20BUSL%201.1-orange)]()
[![Version](https://img.shields.io/badge/version-v3.8.0%20stable-brightgreen)]()

## What this is

A locally-running cognitive runtime that decides things deterministically when it can, and consults learning components only when it has to.

- **Solver-first routing.** Every query is dispatched to a deterministic solver before any learned model or LLM is consulted. The solver layer is authoritative; everything above it is advisory.
- **Builder and mentor lanes (Phase 9).** Two safe lanes for the system to grow new capability without touching live runtime: a builder lane that drafts proposals, a mentor lane that supplies advisory context. Both produce review artifacts; neither auto-applies.
- **Vector provenance with identity anchors.** Every ingested artifact carries a 4-level dedup signature (exact / semantic / sibling / contradiction-or-extension), an identity anchor, and an append-only provenance graph.
- **Multimodal ingestion.** A single ingestion contract accepts files, folders, FAISS databases, and live streams in copy / link / stream modes.
- **Capsule-aware deployment.** Use cases are represented as capsules (factory, cottage, home, gadget, personal, research) so the core treats them as data, not hardcoded business logic.
- **Reality View.** A 11-panel structured view of the system's current state — never fabricates values; missing data shows up as `available=false` with a structured rationale, not as zero or as a guess.
- **Promotion ladder with human gate.** A 14-stage ladder from curiosity through tension, dream target, meta-proposal, human review, post-campaign runtime candidate, canary cell, limited runtime, full runtime. Four runtime stages require an explicit `human_approval_id`. No auto-promotion.
- **Provider plane.** Multi-provider routing (Claude, GPT, local Ollama, etc.) gated by a 6-layer trust gate before any provider response can influence self-model or world-model state.

## What this is not

To keep the project honest:

- **Not a chatbot wrapper.** A language model is consulted only when solvers and specialist models cannot resolve the query.
- **Not auto-merging or auto-deploying.** No code path on a release branch performs `git push origin main`, `merge_to_main(...)`, or `promote_to_runtime(...)`. The atomic runtime flip is a separate, explicitly human-gated session.
- **Not pretending the producer-side is on main.** The Phase 8.5 producer subsystems (curiosity organ, self-model snapshot, dream curriculum, hive proposes) are real but ship as separate PRs after the Phase 9 scaffold lands. The Phase 9 release contains the contracts and consumers, not the producers.
- **Not finished.** This release lands the autonomy fabric scaffold + 16 phases of architecture, all green. Generative memory compression, parallel provider ensembles, predictive cache preheating, and a few other speculative variants are explicitly deferred to Phase 12+ with documented blockers.

## Architecture

```
Query → Solver Router → Solver Engines (Layer 3, authoritative)
                     → Specialist Models (Layer 2, sklearn, 14 models with canary lifecycle)
                     → LLM fallback (Layer 1, Ollama or stub)
                     ↓
                     Verifier (checks against World Model)
                     ↓
                     CaseTrajectory → MAGMA Audit Trail (Audit / Replay / Overlay / Provenance / Trust)
                     ↓
                     Night Learning  /  Dream Mode (counterfactual sims)
```

The runtime is built around a hexagonal layout: `core/` is the domain, `adapters/http/routes/` and `adapters/llm/` are ports, `bootstrap/` is the DI container, `application/` holds DTOs/services. Hex-cell FAISS retrieval is keyed by `core/hex_cell_topology` — solvers organize into 8 cells (`general`, `thermal`, `energy`, `safety`, `seasonal`, `math`, `system`, `learning`).

### Layers

| Layer | Role | Examples |
|-------|------|----------|
| **3 — Authoritative** | Decides | Solver engines, World Model, Policy Engine, Verifier |
| **2 — Learned** | Adapts | 14 specialist models with canary lifecycle |
| **1 — Fallback** | Explains | LLM — only when solvers and specialists cannot handle it |
| **1b — Optional** | Gemma 4 | Optional dual-tier Gemma 4 profiles: fast (e4b) for general fallback, heavy (26b) for hard reasoning |

## Phase 15 — Automatic runtime hints and alpha release readiness

After Phase 14 wired the autonomy consult lane into `SolverRouter.route` behind an explicit context hint, Phase 15 lifts hint derivation up to the **production query handler** `AutonomyRuntime.handle_query(query, context)`. Callers no longer need to know about the autonomy lane — they pass a natural `context["structured_request"]` payload (the same open `context` dict the production API has always accepted), and a deterministic Python extractor derives the autonomy hint internally.

What is real now:

* **`AutonomyRuntime.handle_query(...)` derives the autonomy hint automatically.** Backwards-compatible: callers without `structured_request` see Phase 14 behaviour. Hint extractor errors never break the production path (recorded as `low_risk_autonomy_hint_kind="extractor_error"` and execution continues).
* **`runtime_hint_extractor.py`** — deterministic Python; zero provider calls; no LLM, no embedding lookup. Reads `context["structured_request"]` only. Six supported subkeys: `unit_conversion`, `lookup`, `threshold_check`, `bucket_check`, `linear_eval`, `interpolation`. Explicit rejection kinds: `rejected_ambiguous`, `rejected_family_not_low_risk`, `rejected_missing_fields`, `rejected_not_structured`, `rejected_malformed`, `skipped`.
* **Live before/after proof through the production caller.** `tools/run_automatic_runtime_hint_proof.py` routes a 98-seed corpus through `AutonomyRuntime.handle_query` using only `context["structured_request"]`. Pass 1: 0 served / 98 misses / signals buffered. Harvest. Pass 2: 98 served via capability lookup. **`provider_jobs` delta during proof: 0; `builder_jobs` delta during proof: 0.** Negative corpus: 5/5 expected outcomes (ambiguous, high-risk, missing fields, free-text-only, malformed).
* **Reality View** existing `autonomy_runtime_harvest_kpis` panel **extended** (no new panel — RULE P7) with `live_runtime_hint_aware_signals_total`.
* **Release surface.** `docs/github/REPOSITORY_PRESENTATION.md` (text prepared, not applied), `docs/release/RELEASE_READINESS.md` (alpha tag policy + reproduce-from-clone steps), `docs/deployment/DOCKER_QUICKSTART.md` (Docker contract; not tested in this session).

What is still NOT real (truth boundary):

* **Production callers above `handle_query` do not yet emit `structured_request`.** The production wiring sits in `handle_query`; callers can opt in with the natural payload, but no in-tree caller does today.
* **Six-family allowlist preserved.** RULE 13 — no widening this session.
* **No Stage-2 cutover.** `core/faiss_store.py:26 _DEFAULT_FAISS_DIR=data/faiss/` unchanged.
* **Self-state snapshot + episodic continuity (P5/P6) deferred to Phase 16.** They are observability primitives, not learning mechanisms; deferred per session priority stack to keep scope tight on the P2–P4 release gate.
* No actuator-side autonomy. No real Anthropic / OpenAI HTTP adapters. No federation. **No consciousness claim.**

## Phase 14 — Live runtime hot-path wiring and low-latency autonomy

After Phase 13 added the runtime seam (`RuntimeQueryRouter`) and capability-aware dispatch, Phase 14 wires that seam into the **production reasoning entrypoint** (`SolverRouter.route(...)`), collapses the warm path's SQLite + JSON cost into in-process caches, and moves miss-signal emission off the synchronous hot path with a bounded buffered sink.

What is real now:

* **`SolverRouter.route(...)` autonomy consult.** A backwards-compatible context-hint extension: when the built-in capability selection falls back AND the caller passes `context["low_risk_autonomy_query"]`, the router invokes the autonomy consult lane via `build_autonomy_consult(RuntimeQueryRouter)`. Existing callers without the hint see no behaviour change. Locked by 7 unit tests in `tests/autonomy_growth/test_solver_router_autonomy_consult.py`.
* **`HotPathCache` (in-process).** `WarmCapabilityIndex` keyed on `(family_kind, frozenset(features))` skips the SQLite `find_auto_promoted_solvers_by_features` call on warm hits. `ParsedArtifactCache` skips the `json.loads` of `artifact_json`. Both invalidate on solver promotion / deactivation.
* **`BufferedSignalSink` (bounded).** Runtime miss signals enqueue into an in-memory bounded queue (≤ 1000 signals OR ≤ 500 ms age). Explicit `flush()` and `atexit` hook for graceful shutdown. Documented hard-kill loss bound: ≤ 1000 signals or one 500 ms window's worth, whichever is tighter for the workload.
* **Canonical seed library expanded.** 68 → **98** entries across all 6 families and 8 cells (Phase 14 P4; well above the 96 stretch goal).
* **Live runtime before/after proof.** `tools/run_live_runtime_hotpath_proof.py` routes 98 structured queries through `SolverRouter.route(...)` against an isolated scratch DB. Pass 1: 0 served / 98 misses / 98 buffered signals. Harvest. Pass 2: 98 served via capability lookup. **Pre-cache p50 ≈ 0.39 ms; warm p50 ≈ 0.06 ms — >5× faster.** P3 floor met (5/5). P3 stretch met for 4/5 absolute thresholds; warm-vs-pre-cache ratio missed stretch (10× target, 6.17× observed) due to in-process WAL SQLite already being fast — documented.
* **Reality View.** Existing `autonomy_runtime_harvest_kpis` panel extended with Phase 14 capability-indexed solver counts (no new panel — RULE P7 panel discipline).
* **Inner-loop / outer-loop truth, locked by per-run delta test.** `tests/autonomy_growth/test_live_runtime_hotpath_proof_smoke.py::test_live_runtime_hotpath_proof_zero_provider_delta` runs the proof and asserts `provider_jobs_delta == 0` and `builder_jobs_delta == 0` for the proof window — robust against pre-existing rows in the DB.

What is still NOT real:

* The router is wired into `SolverRouter.route` as a backwards-compatible context-hint extension. Production callers (autonomy runtime hot paths) start passing the hint in a follow-up phase.
* High-risk families remain human-gated. Allowlist still six families (RULE 19).
* Stage-2 atomic flip is unchanged. `core/faiss_store.py:26 _DEFAULT_FAISS_DIR=data/faiss/`.
* No real Anthropic / OpenAI HTTP adapters.
* No actuator-side autonomy.
* Single-process scope (RULE 20: no sharding/federation this session).
* No consciousness claim.

## Phase 13 — Runtime-integrated harvest and capability-aware uptake

After Phase 12's self-starting loop, Phase 13 connects the loop to the **real runtime seam** and adds **capability-aware** dispatch so harvested structure stays useful at scale.

What is real now:

* **Runtime query router (`runtime_query_router.py`).** A real seam: callers invoke `RuntimeQueryRouter.route(query)` after their authoritative built-in solvers. The router dispatches to auto-promoted solvers (capability-aware first, family-FIFO fallback) and, on miss, automatically emits a bounded, deduped `runtime_gap_signal`. No human trigger.
* **Capability-aware dispatch (schema v4).** New `solver_capability_features` table indexed on `(family_kind, feature_name, feature_value)`. Each promoted solver carries a small structured feature set (e.g. `scalar_unit_conversion` → `{from_unit, to_unit}`; `lookup_table` → `{domain, default_present}`; `threshold_rule` → `{subject, operator}`). Runtime queries match by structured equality, not by insertion order.
* **Canonical seed library (`low_risk_seed_library.py`).** 68 curated seeds across all six families and 8 hex cells (20 unit conversions, 12 lookup tables, 12 threshold rules, 8 interval bands, 8 linear forms, 8 interpolation curves). Local-first; no provider call in the common path.
* **End-to-end before/after proof.** `tools/run_runtime_harvest_proof.py` routes 68 structured runtime queries through the real router, then re-routes the same queries after one harvest cycle. **Pass 1: 0 served, 68 misses, 68 signals emitted automatically. Pass 2: 68 served via capability lookup, 0 misses. `provider_jobs` = 0.** Artifact at [`docs/runs/phase13_runtime_harvest_2026_04_30/runtime_harvest_proof.md`](docs/runs/phase13_runtime_harvest_2026_04_30/runtime_harvest_proof.md).
* **Reality View `autonomy_runtime_harvest_kpis` panel.** Surfaces runtime-harvested signal counts split by family and cell, plus the truthful self-starting / teacher-assisted / human-gated split.
* **Inner-loop / outer-loop truth, locked by test.** `tests/autonomy_growth/test_runtime_harvest_proof_smoke.py::test_runtime_harvest_proof_zero_provider_calls` runs the proof and asserts `provider_jobs_total == 0` and `builder_jobs_total == 0`.

What is still NOT real:

* The router is a real seam, but it is exercised by the proof and tests in this PR. Wiring the router into a specific runtime call site (e.g. a hot path inside the autonomy runtime) is a separate session's work.
* High-risk families remain human-gated.
* Stage-2 atomic flip is unchanged. `core/faiss_store.py:26 _DEFAULT_FAISS_DIR=data/faiss/`.
* No real Anthropic / OpenAI HTTP adapters.
* No actuator-side autonomy.
* No consciousness claim.

## Phase 12 — Self-starting local-first autogrowth loop

After Phase 11's closed no-human auto-promotion loop, Phase 12 closes the missing left-hand side: a **self-starting** intake that turns runtime evidence into queued growth intents *without a human trigger*, and a local-first scheduler that drains the queue end-to-end with **zero provider calls** in the inner loop.

What is real now (post-Phase-12):

* **Self-starting gap intake.** `RuntimeGapDetector.record(GapSignal)` writes evidence into `runtime_gap_signals`; `digest_signals_into_intents` aggregates into `growth_intents` and enqueues into `autogrowth_queue` — all in the control plane, no JSON-on-disk.
* **Append-only history mirror.** Every intake / intent / enqueue / promotion event emits a `growth_events` row. The control plane keeps current state; `growth_events` keeps history.
* **Self-starting scheduler.** `AutogrowthScheduler.run_until_idle()` claims queue rows atomically (no double-claim across concurrent schedulers), dispatches each to `LowRiskGrower.grow_from_gap`, records `autogrowth_runs`, and emits growth events. Rejection is terminal (deterministic given the seed).
* **Mass-safe proof at scale.** `tools/run_mass_autogrowth_proof.py` runs the full loop end-to-end, growing **30 deterministic low-risk solvers across all 6 allowlisted families and 8 hex cells** with **0 rejections, 0 errors, 0 provider calls**. Reproducible artifact at [`docs/runs/phase12_self_starting_autogrowth_2026_04_30/autonomy_proof.md`](docs/runs/phase12_self_starting_autogrowth_2026_04_30/autonomy_proof.md).
* **Reality View.** New `autonomy_self_starting_kpis` aggregate panel surfaces queue / intent / promotion counts split by family and cell. The Phase 11 `autonomy_low_risk_kpis` panel still works alongside it; never-fabricate invariant preserved.
* **Inner-loop / outer-loop truth (locked by test).** `tests/autonomy_growth/test_outer_inner_loop_truthful.py::test_mass_autogrowth_zero_provider_calls` runs the mass proof and asserts the resulting `provider_jobs` table is empty. If a future change quietly routes inner-loop common-path growth through a paid provider, the test fails.

What is still NOT real:

* High-risk families (`weighted_aggregation`, `temporal_window_rule`, `structured_field_extractor`, `deterministic_composition_wrapper`) remain human-gated through the Phase 9 promotion ladder.
* The Phase 9 14-stage promotion ladder is unchanged. Runtime-stage promotions still require a real `human_approval_id`.
* The Stage-2 atomic flip is unchanged. `core/faiss_store.py:26 _DEFAULT_FAISS_DIR=data/faiss/`. The Stage-2 RFC still gates that mechanism.
* No real Anthropic / OpenAI HTTP adapters. Only `dry_run_stub` and `claude_code_builder_lane` are exercisable end-to-end. Phase 12 does not change that.
* No actuator / policy / production-runtime-config writes. Auto-promoted low-risk solvers are *consultable*, not *authoritative*.
* No consciousness claim. Self-starting growth is an engineering mechanism — observable, reversible, audit-traceable. See `docs/journal/2026-04-30_autonomy_trajectory_and_gap_analysis.md` for the explicit non-claim.

## Phase 11 — Autonomous low-risk solver growth lane

After v3.6.0 + Phase 10 substrate, Phase 11 lands the first **closed no-human autonomous solver growth lane** for a bounded allowlist of deterministic, side-effect-free families. The lane is bounded by [`docs/architecture/LOW_RISK_AUTOGROWTH_POLICY.md`](docs/architecture/LOW_RISK_AUTOGROWTH_POLICY.md).

What is real now:

* Six allowlisted families can be auto-promoted by the system without a human in the inner loop: `scalar_unit_conversion`, `lookup_table`, `threshold_rule`, `interval_bucket_classifier`, `linear_arithmetic`, `bounded_interpolation`.
* `waggledance/core/autonomy_growth/` ships the closed loop: `LowRiskGrower` (gap → spec → compile), `AutoPromotionEngine` (validate → shadow → I1–I9 invariants → atomic decision write), `LowRiskSolverDispatcher` (runtime executor between built-in solvers and LLM fallback), and `solver_executor.py` (six pure executors).
* Control-plane schema v2 adds five new normalized tables — `family_policies`, `validation_runs`, `shadow_evaluations`, `promotion_decisions`, `autonomy_kpis` — plus a `solver_artifacts` table for the executable compiled form. No JSON-on-disk system-of-record (RULE 10).
* Reality View gains an `autonomy_low_risk_kpis` aggregate panel that surfaces auto-promotions / rejections / rollbacks / dispatcher hits without listing per-solver state at scale. The never-fabricate invariant is preserved: an empty autonomy lane returns `available=false` with `rationale_if_unavailable="no_autonomy_activity_recorded_yet"`.
* `tools/run_autonomy_proof.py` produces a reproducible end-to-end proof artifact at [`docs/runs/phase11_autogrowth_2026_04_29/autonomy_proof.md`](docs/runs/phase11_autogrowth_2026_04_29/autonomy_proof.md) and a machine-readable [`autonomy_proof.json`](docs/runs/phase11_autogrowth_2026_04_29/autonomy_proof.json).

What is **not** real:

* High-risk families (`weighted_aggregation`, `temporal_window_rule`, `structured_field_extractor`, `deterministic_composition_wrapper`) remain human-gated through the existing Phase 9 promotion ladder.
* The 14-stage Phase 9 promotion ladder is unchanged — runtime-stage promotions still require a real `human_approval_id`. Auto-promotion in this lane is a *separate status flag* (`solvers.status='auto_promoted'`), not a runtime-stage promotion.
* The Stage-2 atomic flip is unchanged. `core/faiss_store.py:26 _DEFAULT_FAISS_DIR=data/faiss/` is the runtime read path until subsystems opt into `PathResolver`. The mechanism is specified in [`docs/architecture/STAGE2_CUTOVER_RFC.md`](docs/architecture/STAGE2_CUTOVER_RFC.md) and remains unexecuted.
* Real Anthropic / OpenAI HTTP adapters are still follow-up work. Only `dry_run_stub` and `claude_code_builder_lane` are exercisable end-to-end today; Phase 11 does not change that.
* No actuator-side autonomy. Auto-promoted low-risk solvers are *consultable*, not *authoritative*; built-in solvers retain precedence.

## Phase 10 — Foundation, Truth, Builder Lane (substrate landed on main)

After v3.6.0 shipped the Phase 9 autonomy fabric scaffold, Phase 10 added the substrate for tens of thousands of solvers, made the Claude Code / Anthropic / GPT / local-model lanes first-class, and tightened truthfulness across docs and Reality View.

Phase 10 substrate landed on `main` via [PR #54](https://github.com/Ahkeratmehilaiset/waggledance-swarm/pull/54) — squash-merged 2026-04-28T12:14:15Z as commit `08b7e8c`. No new SemVer tag was minted because Phase 10 adds substrate, not runtime hot-path behaviour change. A future runtime-affecting release picks the next version; an optional `v3.6.1-substrate` prerelease tag may be added when post-merge truth/governance are clean.

* **Storage substrate.** A 16-table SQLite control plane (`waggledance/core/storage/`) that owns *current state* of solvers, families, capabilities, vector shards, provider/builder jobs, promotion ladder, runtime path bindings. MAGMA still owns *history*; FAISS / Chroma still own *vector content*. A new `PathResolver` is the seam future cutovers can use without code changes — it is drop-in compatible with the legacy `_DEFAULT_FAISS_DIR` so nothing in the runtime moves until a subsystem opts in.
* **Provider plane execution layer.** `waggledance/core/providers/` adds JSON-schema-validated request/response dispatch on top of the Phase 9 routing scaffold. `ClaudeCodeBuilder` is the only authorised subprocess (isolated worktree, bounded timeout, JSONL invocation log, dry-run fallback when CLI absent). The mentor-output advisory boundary is enforced at the API surface: mentor notes are IR `learning_suggestion` objects with `lifecycle_status='advisory'`, never directly mutating runtime.
* **Solver bootstrap orchestrator.** `solver_synthesis/solver_bootstrap.py` implements the U1→U3 escalation rule on top of the Phase 9 declarative pipeline + Phase 10 LLM generator (provider-plane backed) + cold/shadow throttler. Family-first growth: high-confidence gaps go declarative; low-confidence gaps escalate to free-form via Claude Code or Anthropic.
* **Scale-aware Reality View.** `ui/hologram/scale_aware_aggregator.py` aggregates per-family rollups, per-cell counts, and queue summaries from the control plane, so the Reality View never claims "one node per solver" at 10k+ scale. The Phase 9 11-panel structure and never-fabricate invariant are unchanged.
* **Storage / cutover truth audit.** `docs/journal/2026-04-28_storage_runtime_truth.md` and `2026-04-28_cutover_model_classification.md` ground the runtime claims with file:line citations. The v3.6.0 atomic flip is formally classified `MODEL_C_NOOP_ALREADY_COMPLETE`; future Stage-2 flip is `MODEL_D_AMBIGUOUS` until an RFC defines the mechanism.

What Phase 10 explicitly does **not** do: execute the runtime cutover, ship real Anthropic/OpenAI HTTP adapters, replace any Phase 9 module, or claim the autonomy runtime emits vector events at runtime (offline tools do; the autonomy runtime does not yet). The future Stage-2 cutover mechanism is specified in [`docs/architecture/STAGE2_CUTOVER_RFC.md`](docs/architecture/STAGE2_CUTOVER_RFC.md) and remains explicitly *not executed* until a fresh one-shot human approval is collected against that RFC.

## Phase 9 — Autonomy Fabric (v3.6.0 release)

The 16 phases of the autonomy fabric ship in this release as a self-contained scaffold:

| Phase | Module | Purpose |
|---|---|---|
| F | `waggledance/core/autonomy/` | Always-on cognitive kernel (10 sub-components: kernel state, governor, mission queue, budget engine, policy core, action gate, attention allocator, background scheduler, micro-learning lane, circuit breaker) |
| G | `core/ir/` + `core/capsules/` | Cognition IR + Capsule Registry with blast-radius enforcement |
| H | `core/vector_identity/` + `core/ingestion/` | Vector provenance graph, identity anchors, universal ingestion |
| I | `core/world_model/` | Calibrated world model, drift detection |
| P | `ui/hologram/reality_view.py` | 11-panel Reality View (never-fabricate invariant) |
| V | `core/conversation/` + `core/identity/` | Presence log, meta-dialogue, forbidden-pattern scanning |
| J | `core/provider_plane/` + `core/api_distillation/` | Multi-provider routing + 6-layer distillation trust gate |
| U1 | `core/solver_synthesis/` (declarative) | 10 default solver families |
| U2 | `core/builder_lane/` | Builder, repair forge, mentor forge |
| U3 | `core/solver_synthesis/` (gap-driven) | Autonomous solver synthesis with cold-shadow throttling |
| L | `core/memory_tiers/` | Hot/warm/cold/glacier with pinning + invariant extraction |
| K | `core/hex_topology/` | Real hex runtime topology (4 live states, 4 subdivision states) |
| M | `core/promotion/` | 14-stage promotion ladder (4 runtime stages require human approval) |
| O | `core/proposal_compiler/` | Meta-proposal → engineering bundle (patch skeleton, affected files, test spec, rollout plan, rollback plan, acceptance criteria, review checklist, PR draft) |
| N | `core/local_intelligence/` | Local model distillation **safe scaffold** (advisory-only, 6 critical task kinds refused, no auto-promotion) |
| Q | `core/cross_capsule/` | Cross-capsule observer (redacted summaries in, redacted observations out) |

All Phase 9 core modules are BUSL-1.1 protected. Tools, tests, and UI compatibility code are Apache 2.0. Schemas are public-interface artifacts.

See [`docs/architecture/PHASE_9_ROADMAP.md`](docs/architecture/PHASE_9_ROADMAP.md) for the full navigation surface.

## Builder and Mentor Lanes

Two complementary safe lanes for capability growth (`waggledance/core/builder_lane/`):

- **Builder lane.** Allocates a worktree, packages a request (`builder_request_pack`), and hands it to a session forge or repair forge. Returns a result pack. Never modifies the live runtime path.
- **Mentor lane.** Produces advisory context (`mentor_forge`) — notes, alternatives, hints — that travel as advisory IR (`lifecycle_status: advisory`). The lane never claims authority; it just informs the proposal compiler.

Subprocess invocation is gated for human review by default. The CLI emits an `advisory_only` outcome unless explicitly run with operator approval.

## Memory and Identity

- **Vector identity** (`core/vector_identity/`): every persistent artifact has a 4-level dedup signature, an identity anchor (foundational anchors enter a candidate state first), and a chained provenance graph.
- **Memory tiers** (`core/memory_tiers/`): hot / warm / cold / glacier with an access pattern tracker that counts uses but never rewrites meaning. Pinning engine auto-pins foundational entries; demoting a pinned entry to cold or glacier raises `TierViolation`.
- **Invariant extractor** runs BEFORE deep tiering: extracts constraints, schemas, and relations so they survive demotion.
- **Self-model layer** (Phase 8.5, ships as separate follow-up PR): scorecards, blind spots, workspace tensions, attention focus.

## Capsules and deployment

The system is configured by capsule manifests, not hardcoded business logic. Active capsules:

- `factory_v1` — industrial telemetry, OEE/SPC, predictive maintenance
- `cottage_v1` — off-grid heating, frost protection, energy management
- `home_v1` — comfort automation, safety, energy optimization
- `gadget_v1` — edge / IoT (RPi, Jetson)
- `personal_v1` — single-user assistant
- `research_v1` — exploratory experiments

Each capsule declares its blast radius, rate limits, sensors, and forbidden actions. The core treats capsule context as a typed value (`capsule_context`), never as branching business rules.

## Quick start

### Docker (recommended)

```bash
git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git
cd waggledance-swarm
docker compose up -d
```

Dashboard: http://localhost:8000 | Reality View: http://localhost:8000/hologram

### Native

```bash
pip install -r requirements.txt

# Requires Ollama running locally (ollama serve)
python start_waggledance.py

# Stub mode — no Ollama needed
python start_waggledance.py --stub

# Capsule preset
python start_waggledance.py --preset=cottage-full
```

## Reality View

The `/hologram` page renders an 11-panel structured operator view. Each panel is one of:

- `available=true` with real items, OR
- `available=false` with a structured `rationale_if_unavailable` string (e.g., `"vector_graph snapshot missing"`)

The never-fabricate invariant means a missing data source is reported as missing — not papered over with default values or zero. See `waggledance/ui/hologram/reality_view.py`.

A real evidence render against Session B `self_model_snapshot` is committed at [`docs/runs/phase9_reality_view_render.json`](docs/runs/phase9_reality_view_render.json) — 5/11 panels populated, 6/11 honestly unavailable.

## MAGMA Memory Architecture

| Layer | Component | Role |
|-------|-----------|------|
| **L1** | AuditLog | Append-only event log — goals, plans, actions, verifications |
| **L2** | ReplayEngine | Mission-level replay with chronological step reconstruction |
| **L3** | MemoryOverlay | Filtered views by profile, mission, entity |
| **L4** | Provenance | 9-tier source tracking (verifier → observed → solver → rule → stats → case → reflection → LLM → simulated) |
| **L5** | TrustEngine | Multi-dimensional scoring for agents, capabilities, solvers, routes, specialists |

## Promotion ladder (the human gate)

The 14-stage promotion ladder at `waggledance/core/promotion/`:

```
curiosity → tension → dream_target → meta_proposal → human_review
  → post_campaign_runtime_candidate → canary_cell → limited_runtime → full_runtime
                                              ↘ archived (allowed from anywhere)
```

The four runtime stages (post-campaign-runtime-candidate / canary-cell / limited-runtime / full-runtime) require a non-empty `human_approval_id` of the form `human:<reviewer>:<utc-iso>`. The promotion engine refuses transitions that don't carry one. `detect_bypass()` flags multi-step skips. `rollback_engine` requires the same human id when rolling back from a runtime stage.

## Final atomic runtime flip — separate session

The Phase 9 release lands the scaffold. The actual atomic runtime flip — pointing the live runtime read path at the new code — is **not** part of this PR. It runs as a separate prompt (`Prompt 2`, see [`docs/architecture/PROMPT_2_INPUTS_AND_CONTRACTS.md`](docs/architecture/PROMPT_2_INPUTS_AND_CONTRACTS.md)) after:

1. all Phase 8.5 follow-up PRs land on main
2. the 400h gauntlet campaign is finished or frozen
3. a signed approval artifact has been authored by a human reviewer

The flip is a fast-forward `git push <release_branch>:main` with head-SHA protection, never a force-push.

## API

REST + WebSocket on port 8000. Key groups:

| Group | Examples |
|-------|----------|
| Core | `POST /api/chat`, `GET /api/status`, `GET /api/heartbeat` |
| Ops | `GET /api/ops` — live FlexHW tier + AutoThrottle telemetry |
| Autonomy | `/api/autonomy/status`, `/api/autonomy/kpis`, `/api/autonomy/learning/run` |
| Hologram / Reality View | `GET /api/hologram/state`, `GET /hologram` |
| Storage | `GET /api/storage/health`, `POST /api/storage/wal-checkpoint` |
| Introspection | `/api/magma/*`, `/api/graph/*`, `/api/trust/*`, `/api/cross-agent/*`, `/api/analytics/*` |
| Profiles | `GET /api/profiles` — `{active, configured, restart_required}` |
| Feeds | `GET /api/feeds` — config-based sources with per-source freshness |
| Learning | `/api/learning/state-machine`, `/api/capabilities/state` |
| Sensors | `/api/sensors`, `/api/sensors/home`, `/api/sensors/camera/events` |
| Hybrid | `/api/hybrid/status`, `/api/hybrid/topology`, `/api/hybrid/cells` — hex-cell FAISS retrieval |

WebSocket at `ws://localhost:8000/ws` for real-time brain updates, chat streaming, alerts.

See [`docs/API.md`](docs/API.md) for the full reference.

## Security

- **Auth** — HttpOnly session cookie (SameSite=Strict, 1 h TTL) for the browser; Bearer token for cURL/scripts/CI. API key auto-generated on first start.
- **No browser-visible secrets** — master key never appears in served HTML, inline JS, localStorage, or sessionStorage.
- **No frontend Bearer construction** — all browser fetches use `credentials: 'same-origin'`.
- **No `?token=` in frontend WebSocket** — browser WS connects clean; token parameter is accepted server-side for scripts only.
- **No eval()** — AST-based whitelist expression evaluator (`core/safe_eval.py`).
- **Safe Action Bus** — all write operations go through policy → risk → approval chain.
- **OOM protection** — ResourceGuard with adaptive throttling and emergency GC.
- **MQTT TLS** — enabled by default (port 8883).

## Phase 8 — Honeycomb Solver Scaling (still scaffolding)

Phase 8 is the substrate Phase 9 builds on. It adds planning, hashing, and gating tools for safe solver-library growth without flipping any runtime switch:

- `tools/cell_manifest.py` — deterministic per-cell state cards
- `waggledance/core/learning/solver_hash.py` — strict `solver_hash()` + dedup scanner
- `schemas/solver_proposal.schema.json` + `tools/propose_solver.py` — 12-gate quality review, never auto-merges
- `waggledance/core/learning/composition_graph.py` — typed DAG over the existing library
- `tools/run_honeycomb_400h_campaign.py` — segment-aware campaign scaffolding; never auto-starts without `--confirm-start`

Design: [`docs/architecture/HONEYCOMB_SOLVER_SCALING.md`](docs/architecture/HONEYCOMB_SOLVER_SCALING.md).

## Testing

```bash
# Phase 9 targeted suite (~7 s, 657 tests)
python -m pytest tests/test_phase9_*.py -q

# Full suite
python -m pytest -q

# Subsystems
python -m pytest tests/autonomy/ -v
python -m pytest tests/contracts/ -v
python -m pytest tests/continuity/ -v
```

The Phase 9 GLOBAL PROPERTY tests at `tests/test_phase9_global_properties.py` enforce: no silent failures, no auto-enactment to main/live runtime, no constitution self-mutation, no foundational auto-promotion without human approval, no capsule blast-radius leakage, deterministic builder request/result IDs, no absolute path leakage, no secret literals, domain-neutrality.

## Why the name "WaggleDance"?

The name comes from honeybee waggle dances — a real-world example of a collective intelligence system where a single discovery doesn't become a decision until peer feedback validates it. A scout's dance encodes direction (angle), distance (duration), and quality (vigor); experienced nestmates touch the dancer with antennae and provide live feedback; a stop signal can shut the dance down entirely.

The codebase historically used bee/hive metaphors throughout. New code (Phase 9 onward) is **domain-neutral**: terms like Cognitive Fabric, Reality View, Capsule, Cell, Runtime Topology, Provenance, Distillation, Builder Lane, and Mentor Lane replace bee/swarm/honeycomb/factory metaphors in core modules. Legacy paths and product names remain for compatibility.

## License

**Dual-licensed:**

| Component | License | File |
|-----------|---------|------|
| Open core (infrastructure, adapters, tests, API, tools) | Apache 2.0 | [`LICENSE`](LICENSE) |
| Protected modules (autonomy fabric, dream mode, consolidator, meta-optimizer, projections, MAGMA core) | BUSL 1.1 | [`LICENSE-BUSL.txt`](LICENSE-BUSL.txt) |

Protected module list: [`LICENSE-CORE.md`](LICENSE-CORE.md). Phase 9 SPDX coverage: 147/147 source files tagged (107 BUSL-1.1 + 40 Apache-2.0).

BUSL change date: **2030-03-19** — protected modules become Apache 2.0 automatically on this date.

**Personal non-commercial use of protected modules is permitted.**
Commercial licensing: see [`COMMERCIAL-USE.md`](COMMERCIAL-USE.md) or contact janikorpi@hotmail.com.

## Credits

Built by **Jani Korpi** ([Ahkerat Mehilaiset](https://github.com/Ahkeratmehilaiset), Helsinki) with [Claude Code](https://claude.ai/claude-code).

---

*WaggleDance — Local. Auditable. Human-gated.*
