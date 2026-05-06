# Current Status — WaggleDance AI

**Updated:** 2026-05-06
**Stable version:** **v3.8.0 stable** (released 2026-05-04 from Phase 16F, PR #68 squash-merge `824176eb`). Remains GitHub Latest.
**Most recent prerelease:** **v3.10.4-incremental-gap-replay-alpha** (released 2026-05-06T09:01:14Z from PR #88 squash-merge `c1ddded1`). Pre-release entry on GitHub.
**Phase 18D landed:** docs-only PR #85 squash-merged 2026-05-05T21:55:09Z (merge commit `7d1dedef`). No tag created.
**Released prerelease (Phase 18F):** **v3.10.4-incremental-gap-replay-alpha** (released 2026-05-06T09:01:14Z from PR #88 squash-merge `c1ddded1`). Pre-release entry on GitHub. Post-merge reproduction at origin/main `c1ddded1`: 46/46 tests + 18F/18E/18C/18B proofs + 18A validator all PASS. Phase 18F upgrades Phase 18E's whole-corpus replay into a cursor-based incremental learning loop. New mainline module `waggledance/core/autonomy_growth/incremental_gap_replay.py` (~580 LOC, stdlib + WaggleDance only): cursor + lock in `schema_meta`, strict + counted-skip loader, RuntimeGapDetector adapter, `run_incremental_gap_replay_once` orchestrator. Proof harness `tools/run_phase18f_incremental_gap_replay_proof.py` with 10 stages (seed → first replay → no-op → append → post-cursor replay → post-cursor no-op → strict load → detector bridge → concurrency lock); 46 unit tests. Phase 18C compile table extended with 6 new strict per-family rules (no allowlist widening, no new family_kind). `ControlPlaneDB` schema v4 unchanged — no `ALTER TABLE`, no new column, no new table. Host run: 32 seed events, first replay registers 6 solvers / 6 families / 18 dispatch hits / cursor advances; no-op replay 0 rows / 0 extras; 12 post-cursor events appended; third replay processes only those 12, registers 6 new solvers / 18 new dispatch hits; total 12 registered solvers; 4 type-confused payloads inserted, 3 type_confusion + 1 malformed rejected at load; detector bridge persists 1, rejects 2 malformed; held-lock test returns `lock_result = "LOCKED_NOT_RUN"` with 0 duplicate solvers. `release_gate_pass = true`, `forbidden_claims_absent = true`, provider/builder delta 0/0. Docker `--network none` PASS for Phase 18F proof + Phase 18E + 18C + 18B carry-forward + Phase 18A bundle validator (5/5 exit 0). Axis M on the competitive evidence matrix upgrades to "PROVEN with persisted, idempotent, cursor-incremental runtime-gap replay, RuntimeGapDetector bridge, measured feedback loop, and runtime dispatch of mined solver specs within six-family allowlist". v3.8.0 remains GitHub Latest; v3.9.0 + v3.9.1 + v3.9.2 + v3.9.3 + v3.10.0 + v3.10.1 + v3.10.2 + v3.10.3 + v3.10.4 alphas all Pre-release.
**Released prerelease (Phase 18E):** **v3.10.3-runtime-gap-replay-alpha** (released 2026-05-06T05:42:51Z from PR #86 squash-merge `6c6ca859`). Pre-release entry on GitHub. Phase 18E proves the durable autonomous-learning loop end-to-end: persisted runtime gap events (in the existing `runtime_gap_signals` table with `kind = phase18e.runtime_gap_event.v1`) → loaded by `load_runtime_gap_events` → mined by Phase 18B `mine_runtime_gaps` → registered by Phase 18C `register_mined_solver_specs` → dispatched through the real `LowRiskSolverDispatcher.dispatch_by_features`. New mainline module `waggledance/core/autonomy_growth/runtime_gap_replay.py` (~470 LOC, stdlib + WaggleDance only, no new pip dependency); proof harness `tools/run_phase18e_runtime_gap_replay_proof.py`; 48 unit tests. ControlPlaneDB schema v3 unchanged — no `ALTER TABLE`, no new column; one read-only helper (`list_runtime_gap_signals`) added. Host run: 32 events persisted (3 malformed + 1 forbidden-field rejected), 32 loaded, 13 candidates with 6/3/1/1/1/1 verdict distribution, 6 registered auto-promoted solvers, 18/18 dispatch cases hit via capability-aware path, 6/6 families covered, replay idempotent (second persist inserted 0, second replay added 0 extra rows). `release_gate_pass = true`, `forbidden_claims_absent = true`, provider/builder delta 0/0, `allowlist_unchanged = true`. Docker `--network none` PASS for Phase 18E proof + Phase 18C carry-forward + Phase 18B carry-forward + Phase 18A bundle validator (4/4 exit 0). Post-merge reproduction at origin/main `6c6ca859`: 48/48 tests + 18E/18C/18B proofs + 18A validator all PASS. Axis M on the competitive evidence matrix upgrades to "PROVEN with persisted runtime-gap replay, measured runtime-gap feedback loop, AND runtime dispatch of mined solver specs within six-family allowlist". v3.8.0 remains GitHub Latest; v3.9.0 + v3.9.1 + v3.9.2 + v3.9.3 + v3.10.0 + v3.10.1 + v3.10.2 + v3.10.3 alphas all Pre-release.
**Released prerelease (Phase 17A):** **v3.9.0-producer-fabric-alpha** (released 2026-05-04T18:32:47Z from PR #71 squash-merge `c726995c`). Pre-release entry on GitHub.
**Released prerelease (Phase 17B):** **v3.9.1-local-efficiency-benchmark-alpha** (released 2026-05-04T20:59:09Z from PR #73 squash-merge `f4d0a4a4`). Pre-release entry on GitHub. Adds a reproducible benchmark harness that aggregates the existing Phase 11–17A canonical proofs into a single artifact; documents NOT_RUN slots for six external competitors; introduces an optional Ollama latency probe (default skipped for `--network none` safety). Raw intelligence vs frontier MoE is **NOT CLAIMED**.
**Released prerelease (Phase 17C):** **v3.9.2-local-ollama-baseline-alpha** (released 2026-05-04T22:26:28Z from PR #75 squash-merge `db5d7db1`). Pre-release entry on GitHub. Upgrades the Phase 17B optional Ollama track from `SKIPPED_OPTIONAL` to a `MEASURED` 30-prompt deterministic baseline against one already-installed local model (`gemma4:e4b`, ollama 0.22.1).
**Released prerelease (Phase 17D):** **v3.9.3-local-model-sweep-alpha** (released 2026-05-05T06:05:30Z from PR #77 squash-merge `d0704efe`). Pre-release entry on GitHub. 4-model Ollama panel × 3 repeats × 30 prompts = 360/360 ok; CoV 0.002–0.029. Raw intelligence vs frontier MoE is **NOT CLAIMED**; cross-vendor ranking is **NOT CLAIMED**.
**Released prerelease (Phase 18A):** **v3.10.0-benchmark-schema-alpha** (released 2026-05-05T07:20:31Z from PR #79 squash-merge `4554b24a`). Pre-release entry on GitHub. Phase 18A externalizes Phase 17B / 17C / 17D benchmark artifacts as a versioned, validated, offline-exportable evidence bundle (7 JSON Schemas, 16-claim machine-readable ledger, SHA-256 checksums, stdlib-only validator).
**Released prerelease (Phase 18B):** **v3.10.1-gap-miner-feedback-alpha** (released 2026-05-05T14:32:46Z from PR #81 squash-merge `b408b14a`). Pre-release entry on GitHub. Closed the runtime-gap-mining feedback half of the autonomous learning loop with a six-element verdict enum over structured runtime gap signals.
**Released prerelease (Phase 18C):** **v3.10.2-mined-solver-dispatch-alpha** (released 2026-05-05T18:44:19Z from PR #83 squash-merge `e9aa1de1`). Pre-release entry on GitHub. Phase 18C closes the explicit Phase 18B gap (`capability_lookup_status = NOT_RUN_OUT_OF_PHASE18B_SCOPE`) by registering Phase 18B mined ALLOWLISTED low-risk solver specs into the **real** `ControlPlaneDB` via the canonical Phase 17A four-step pattern (`upsert_solver_family` → `upsert_solver(status='auto_promoted')` → `set_solver_capability_features` → `upsert_solver_artifact`) and dispatching them through the **real** `LowRiskSolverDispatcher.dispatch_by_features()`. New mainline module `waggledance/core/autonomy_growth/mined_solver_runtime.py` (~310 LOC, stdlib + WaggleDance only, no new pip dependency), proof harness `tools/run_phase18c_mined_solver_runtime_dispatch_proof.py`, 33 unit tests, `docs/benchmarks/MINED_SOLVER_RUNTIME_DISPATCH_2026.md`. Host run: 6 ALLOWLISTED candidates → 6 registered auto-promoted solvers; 18/18 deterministic dispatch cases hit (3 per family × 6 families) via capability-aware path with `reason="hit_by_features"`; 8 non-allowlisted verdicts rejected from registration; `release_gate_pass = true`, `forbidden_claims_absent = true`, `provider_jobs_delta = builder_jobs_delta = 0`, `allowlist_unchanged = true`. Builder handoff remains a quarantined contract with `no_auto_promotion = true`; zero solver rows for builder-handoff candidates; no live builder execution. Docker `--network none` PASS for Phase 18C proof + Phase 18B carry-forward + Phase 18A bundle validation. Axis M on the competitive evidence matrix upgrades to "PROVEN with measured runtime-gap feedback loop AND runtime dispatch of mined solver specs within six-family allowlist". v3.8.0 remains GitHub Latest; v3.9.0 + v3.9.1 + v3.9.2 + v3.9.3 + v3.10.0 + v3.10.1 + v3.10.2 alphas all Pre-release.
**Shipped branch:** `main` at `e9aa1de1...` (Phase 18C core PR #83 squash-merge); previous head `0c5335f9` was the Phase 18B post-release docs PR #82 squash-merge. Earlier history: Phase 18A post-release docs PR #80 at `2d32b9b2`; Phase 18A core PR #79 at `4554b24a`; Phase 17D post-release docs PR #78 at `b9dd6b7d`; Phase 17D core PR #77 at `d0704efe`; Phase 17C post-release docs PR #76 at `4f8a9ea7`; Phase 17C core PR #75 at `db5d7db1`; Phase 17B post-release docs PR #74 at `27b8175`; Phase 17B core PR #73 at `f4d0a4a4`; Phase 17A squash-merge of [PR #71](https://github.com/Ahkeratmehilaiset/waggledance-swarm/pull/71) at `c726995c`; Phase 16G post-stable CI fix at `48ac0b3` (PR #70) and `86cde94` (PR #67); Phase 16F squash-merge of [PR #68](https://github.com/Ahkeratmehilaiset/waggledance-swarm/pull/68) at `824176eb` (= v3.8.0 tag target); Phase 16D squash-merge of [PR #66](https://github.com/Ahkeratmehilaiset/waggledance-swarm/pull/66) at `7210a7e`; Phase 16C–A squash-merges; Phase 15 PR #62 at `2b9978d`; Phase 10 PR #54 at `08b7e8c` on top of `8bf1869` (post-v3.6.0 truthfulness commit) on top of `a1c4152` (PR #51 Phase 9 squash).
**All tags (most-recent first):** [`v3.10.2-mined-solver-dispatch-alpha`](https://github.com/Ahkeratmehilaiset/waggledance-swarm/releases/tag/v3.10.2-mined-solver-dispatch-alpha) Pre-release · [`v3.10.1-gap-miner-feedback-alpha`](https://github.com/Ahkeratmehilaiset/waggledance-swarm/releases/tag/v3.10.1-gap-miner-feedback-alpha) Pre-release · [`v3.10.0-benchmark-schema-alpha`](https://github.com/Ahkeratmehilaiset/waggledance-swarm/releases/tag/v3.10.0-benchmark-schema-alpha) Pre-release · [`v3.9.3-local-model-sweep-alpha`](https://github.com/Ahkeratmehilaiset/waggledance-swarm/releases/tag/v3.9.3-local-model-sweep-alpha) Pre-release · [`v3.9.2-local-ollama-baseline-alpha`](https://github.com/Ahkeratmehilaiset/waggledance-swarm/releases/tag/v3.9.2-local-ollama-baseline-alpha) Pre-release · [`v3.9.1-local-efficiency-benchmark-alpha`](https://github.com/Ahkeratmehilaiset/waggledance-swarm/releases/tag/v3.9.1-local-efficiency-benchmark-alpha) Pre-release · [`v3.9.0-producer-fabric-alpha`](https://github.com/Ahkeratmehilaiset/waggledance-swarm/releases/tag/v3.9.0-producer-fabric-alpha) Pre-release · [`v3.8.0`](https://github.com/Ahkeratmehilaiset/waggledance-swarm/releases/tag/v3.8.0) **Latest** · [`v3.7.8-docker-gate-alpha`](https://github.com/Ahkeratmehilaiset/waggledance-swarm/releases/tag/v3.7.8-docker-gate-alpha) Pre-release. v3.8.0 not moved or retagged by Phase 17A / 17B / 17C / 17D / 18A / 18B / 18C.
**CI status:** 🟢 main push-event CI green from PR #67 merge `86cde948` onward; PR #71 + PR #73 + PR #74 + PR #75 + PR #76 + PR #77 + PR #78 + PR #79 + PR #80 + PR #81 + PR #82 + PR #83 PR-level CI green. PR #67 added `fetch-depth: 0` to `actions/checkout` in both workflows; that fix carries forward.

### Phase 18C — Mined solver runtime dispatch (released 2026-05-05, PRERELEASE only)

Phase 18C closes the Phase 18B gap by wiring mined ALLOWLISTED specs through the real ControlPlaneDB / LowRiskSolverDispatcher path. Released as **`v3.10.2-mined-solver-dispatch-alpha`** PRERELEASE at 2026-05-05T18:44:19Z. v3.8.0 remains GitHub Latest.

* **`waggledance/core/autonomy_growth/mined_solver_runtime.py`** — `compile_mined_spec_to_runtime_artifact`, `register_mined_solver_specs`, `RegistrationSummary`. Per-family compilation table covering exactly the six Phase 18B fixture shapes; novel `(family_kind, feature_dict)` signatures fail closed.
* **`tools/run_phase18c_mined_solver_runtime_dispatch_proof.py`** — proof harness with 18 deterministic dispatch cases (3 per family × 6 families). Every case dispatched through `LowRiskSolverDispatcher.dispatch_by_features` (the same code path live runtime uses).
* **Tests:** `tests/autonomy_growth/test_phase18c_mined_solver_runtime_dispatch.py` (33 tests).
* **Measured numbers (host run):** 30 → 14 → 6 registered + 8 rejected. 18/18 dispatch cases hit. 0/0 deltas. release_gate_pass=true.
* **Docker `--network none`:** Phase 18C proof + Phase 18B carry-forward + Phase 18A bundle validator all exit 0.
* **Honesty:** no model pull, no cloud API, no live builder execution, no allowlist widening, no autonomy code change outside Phase 18C's module, no Stage-2 flip, no HUMAN_APPROVAL collected, no consciousness claim, no cross-vendor ranking, no raw-intelligence superiority claim, no new pip dependency, no DB/SQLite files committed, no token/secret exposure.

What did NOT change in Phase 18C: no autonomy code outside the new module, no allowlist, no canonical corpus size (still 128), no 10k synthetic-scale ceiling, no `v3.8.0` tag, no `v3.9.0/v3.9.1/v3.9.2/v3.9.3/v3.10.0/v3.10.1` alpha tags, no provider HTTP adapter, no `/api/autonomy/query` route, no new dispatcher/executor/router/promotion engine — Phase 18C reuses the existing Phase 11–17A runtime path verbatim.

### Phase 18B — Runtime gap miner + solver feedback loop (released 2026-05-05, PRERELEASE only)

Phase 18B closes the runtime-gap-mining feedback half of the autonomous learning loop on main inside the bounded six-family allowlist. Candidate tag: **`v3.10.1-gap-miner-feedback-alpha`** PRERELEASE only.

* **`waggledance/core/autonomy_growth/gap_candidate.py`** — `GapVerdict` enum (six elements) + `GapCandidate`/`GapMiningResult` frozen dataclasses.
* **`waggledance/core/autonomy_growth/gap_mining.py`** — `mine_runtime_gaps`, `candidate_to_solver_spec`, `build_quarantined_builder_handoff`. Six-family allowlist enforced fail-closed. Deterministic SHA-256-derived `candidate_id`. `cluster_window` field lets two waves of the same gap form two clusters with the same id, second of which is `DUPLICATE_SUPPRESSED`.
* **`tools/run_phase18b_gap_miner_feedback_proof.py`** — proof harness with deterministic 30-signal synthetic fixture covering every verdict.
* **Tests:** `tests/autonomy_growth/test_phase18b_gap_miner_feedback.py` (19 tests).
* **Measured numbers (host run):** 30 → 14 → 6+3+2+1+1+1; 6 solver specs; `release_gate_pass = true`; `provider_jobs_delta = builder_jobs_delta = 0`; `forbidden_claims_absent = true`; `allowlist_unchanged = true`; `no_stage2_flip = no_human_approval = true`; `capability_lookup_status = NOT_RUN_OUT_OF_PHASE18B_SCOPE` (recorded honestly).
* **Docker `--network none`:** `waggledance:phase18b` builds clean. Both proof + Phase 18A validator exit 0 inside `--network none`.
* **Honesty contracts:** no model pull, no cloud API call, no live builder execution, no allowlist widening, no autonomy code change outside Phase 18B's modules, no Stage-2 flip, no HUMAN_APPROVAL collected, no consciousness claim, no "beats all competitors" claim, no cross-vendor ranking, no raw-intelligence superiority claim, no new pip dependency.
* **Phase 18A bundle EOL fix:** Phase 18A exporter rewritten to write LF-only bytes; `.gitattributes` declares the bundle subtree `text eol=lf`; bundle re-exported; Phase 18A 15/15 tests still pass.

What did NOT change in Phase 18B: no autonomy code outside the new modules, no allowlist, no canonical corpus size (still 128), no 10k synthetic-scale ceiling, no `v3.8.0` tag, no `v3.9.0/v3.9.1/v3.9.2/v3.9.3/v3.10.0` alpha tags, no provider HTTP adapter, no `/api/autonomy/query` route.

### Phase 18A — Benchmark externalization + schema hardening (released 2026-05-05, PRERELEASE only)

Phase 18A externalizes Phase 17B / 17C / 17D benchmark artifacts as a versioned, validated, offline-exportable evidence bundle. Released as **`v3.10.0-benchmark-schema-alpha`** PRERELEASE at 2026-05-05T07:20:31Z. v3.8.0 remains GitHub Latest.

* **`schemas/benchmarks/v1/`** — 7 JSON Schema files (Draft 2020-12) for the bundle manifest, artifact index, claim ledger, release lineage, and the three sanitized source-artifact shapes.
* **`tools/validate_phase18a_benchmark_bundle.py`** (~330 LOC) — stdlib-only validator (no `jsonschema` dep). Implements a Draft 2020-12 subset plus RFC 6901 JSON Pointer resolution, SHA-256 checksum verification, sanitization scrub for `stdout`/`stderr` leakage, forbidden-vocabulary substring scan, and release-lineage hard-checks against `v3.8.0` Latest + the 4 v3.9.x alpha SHAs.
* **`tools/run_phase18a_benchmark_externalization.py`** (~470 LOC) — exporter that ingests Phase 17B/17C/17D committed JSONs, sanitizes them (per-prompt `stdout`/`stderr` → `{"redacted": true, "sha256": "...", "length": N}`), copies the 7 schemas into the bundle, writes manifest + artifact index + claim ledger + release lineage + checksums + Markdown reports.
* **16-claim machine-readable ledger** with explicit `NOT_CLAIMED` entries for raw-intelligence superiority and cross-vendor ranking.
* **Tests:** `tests/benchmarks/test_phase18a_benchmark_externalization.py` (15 tests) — happy path + adversarial fixtures + determinism.
* **Docker `--network none`:** combined export + validate inside `waggledance:phase18a` exits 0.
* **Honesty contracts:** no model pull, no cloud API call, no allowlist widening, no autonomy code change, no Stage-2 flip, no HUMAN_APPROVAL collected, no consciousness claim, no "beats all competitors" claim, no cross-vendor ranking, no raw-intelligence superiority claim, no new pip dependency.

What did NOT change in Phase 18A: no autonomy code, no allowlist, no canonical corpus size (still 128), no 10k-scale ceiling, no `v3.8.0` tag, no `v3.9.0-producer-fabric-alpha` tag, no `v3.9.1-local-efficiency-benchmark-alpha` tag, no `v3.9.2-local-ollama-baseline-alpha` tag, no `v3.9.3-local-model-sweep-alpha` tag, no provider HTTP adapter, no Stage-2 atomic flip. No new measurements — Phase 18A re-exports existing committed Phase 17B/17C/17D evidence.

### Phase 17D — Local Ollama multi-model sweep (released 2026-05-05, PRERELEASE only)

Phase 17D extends the Phase 17C single-model probe to a panel of N already-installed local models with R repeats per model. Released as **`v3.9.3-local-model-sweep-alpha`** PRERELEASE at 2026-05-05T06:05:30Z. v3.8.0 remains GitHub Latest.

* **`tools/run_phase17d_local_model_sweep.py`** (~470 LOC) — wraps the Phase 17C prompt manifest + decoder + forbidden-vocabulary scrub; adds pull/download abort gate (5 substring signatures) and ranking-guard scrub (8 substrings: `is faster than`, `outperforms`, etc.); emits per-model + per-repeat statistics with coefficient of variation across the 3 per-repeat medians.
* **Selected models:** `gemma4:e4b` (9.6 GB), `gemma3:4b` (3.3 GB), `llama3.2:3b` (2.0 GB), `phi4-mini:latest` (2.5 GB). Deferred (NOT exercised, present locally but > 10 GB): `gemma4:26b`, `qwen2.5:32b`, `osoderholm/poro:latest`.
* **Measured numbers (host run):** 360/360 prompts succeeded across 4 models × 3 repeats × 30 prompts. Per-repeat median CoV 0.0022–0.0285 (high stability). p50 panel spread: 526.6 ms (`llama3.2:3b`) to 784.7 ms (`gemma4:e4b`). Reported in selection order; no rank ordering is implied.
* **Tests:** `tests/autonomy_growth/test_phase17d_local_model_sweep.py` (13 tests) covering MEASURED-PANEL, NOT_AVAILABLE_NOT_RUN, TOO_FEW_MODELS, PULL_DETECTED, FAILED-FOR-MODEL, ranking-substring injection, override-with-absent-model, repeat-count semantics.
* **Docker `--network none`:** `waggledance:phase17d` builds clean and the Phase 17C carry-forward harness exits 0 with `release_gate_pass=true`. Detail: `docs/runs/phase17d_local_model_sweep_2026_05_05/docker_phase17d_verification.md`.
* **Honesty contracts:** no model pull, no cloud API call, no allowlist widening, no autonomy code change, no Stage-2 flip, no HUMAN_APPROVAL collected, no consciousness claim, no "beats all competitors" claim, no cross-vendor ranking.

What did NOT change in Phase 17D: no autonomy code, no allowlist, no canonical corpus size (still 128), no 10k-scale ceiling, no `v3.8.0` tag, no `v3.9.0-producer-fabric-alpha` tag, no `v3.9.1-local-efficiency-benchmark-alpha` tag, no `v3.9.2-local-ollama-baseline-alpha` tag, no provider HTTP adapter, no Stage-2 atomic flip.

### Phase 17C — Local Ollama baseline (released 2026-05-04, PRERELEASE only)

Phase 17C upgrades the Phase 17B optional Ollama track from `SKIPPED_OPTIONAL` to `MEASURED` for one already-installed local model. Released as **`v3.9.2-local-ollama-baseline-alpha`** PRERELEASE at 2026-05-04T22:26:28Z. v3.8.0 remains GitHub Latest.

* **`tools/run_phase17c_local_ollama_baseline.py`** (~580 LOC) — wraps the Phase 17B aggregator (`--skip-ollama` pass-through of Tracks A–E) and adds Track F: a 30-prompt deterministic Ollama probe against `gemma4:e4b` (rule-14 preference order). Bytes-mode subprocess output + UTF-8 decoding with `errors="replace"` so Windows cp1252 cannot crash the harness on stray bytes from the model.
* **30 deterministic prompts**, 5 per six-family low-risk allowlist (`scalar_unit_conversion`, `lookup_table`, `threshold_rule`, `interval_bucket_classifier`, `linear_arithmetic`, `bounded_interpolation`). SHA-256 prompt-hash + chained SHA-256 over all stdouts.
* **Measured numbers (host run):** `prompts_succeeded = 30 / 30`, `prompts_failed = 0`, `median_latency_seconds = 0.7866`, `p95_latency_seconds = 17.5538`, `mean_latency_seconds = 2.5539`, `total_seconds = 76.6193`, `hash_chain_sha256` head `3813e784f4ab42d9...`.
* **Tests:** `tests/autonomy_growth/test_phase17c_local_ollama_baseline.py` (15 tests) — fake-PATH ollama shim covering MEASURED, NOT_AVAILABLE_NOT_RUN, FAILED, override-model, forbidden-substring injection.
* **Docker `--network none`:** `waggledance:phase17c` builds clean and `python tools/run_phase17c_local_ollama_baseline.py --skip-ollama --allow-no-ollama-track` exits 0 with `release_gate_pass=true`. Detail: `docs/runs/phase17c_local_ollama_baseline_2026_05_04/docker_phase17c_verification.md`.
* **Honesty contracts:** no model pull, no cloud API call, no allowlist widening, no autonomy code change, no Stage-2 flip, no HUMAN_APPROVAL collected, no consciousness claim, no "beats all competitors" claim.

What did NOT change in Phase 17C: no autonomy code, no allowlist, no canonical corpus size (still 128), no 10k-scale ceiling, no `v3.8.0` tag, no `v3.9.0-producer-fabric-alpha` tag, no `v3.9.1-local-efficiency-benchmark-alpha` tag, no provider HTTP adapter, no Stage-2 atomic flip.

### Phase 17A — Producer fabric and 10k solver scale (released 2026-05-04, PRERELEASE only)

Phase 17A closes the producer-fabric and large-scale-solver gap on top of v3.8.0 stable. Released as **`v3.9.0-producer-fabric-alpha`** PRERELEASE at 2026-05-04T18:32:47Z. v3.8.0 remains GitHub Latest.

* **14 phase8.5 producer modules ported** to main (~4,511 LOC, all stdlib + waggledance siblings): `waggledance/core/dreaming/*` (curriculum, collapse, meta_proposal, replay, request_pack, shadow_graph, package init), `waggledance/core/magma/{self_model,reflective_workspace}.py`, `waggledance/core/meta/*` (meta_learner, history, inputs, review_bundle, package init). Source: `origin/phase8.5/hive-proposes`. SPDX BUSL-1.1 headers added at port time.
* **Producer fabric proof** (`tools/run_phase17a_producer_fabric_proof.py`): 68 IR objects emitted across 6 kinds (curiosity 30 + self_model 14 + dream_curriculum 6 + dream_meta_proposal 2 + hive_proposals 8 + review_bundle 8); 6/6 negative cases pass; `provider_jobs_delta = builder_jobs_delta = 0`. 18/18 integration tests.
* **10k synthetic solver capability scale proof** (`tools/run_solver_scale_proof.py`): 10000 deterministic descriptors balanced across 6 families × 8 hex cells, bulk-loaded into a fresh ControlPlaneDB. 1000/1000 capability hits via the real `RuntimeQueryRouter.route()` → `dispatch_by_features()` path. 0 FIFO fallback, 0 miss. Lookup p50/p95/p99 inside Docker `--network none` ≈ 0.47/0.94/1.17 ms. Honesty label: `is_synthetic_scale=true, not_canonical_corpus=true`.
* **Canonical seed corpus growth: 104 → 128** (+4 per family, no allowlist widening). New per-family floors: scalar 32, lookup 21, threshold 21, interval 18, linear 18, interp 18. Phase 15 / 16A / 16B P2 proofs all re-run cleanly at corpus 128 with `auto_promotions_total = 128` and `provider_jobs_delta = builder_jobs_delta = 0`. Soak 9/9 PASS at corpus 128.
* **Phase 8.5 branches preserved** on origin (closes CLAUDE.md golden rule #4 gap). All 5 `phase8.5/*` branches now visible on origin via 4 fast-forward pushes; no force-push, no history rewrite. Detail: `docs/runs/phase17a_producer_fabric_scale_2026_05_04/phase85_branch_preservation.md`.
* **Honest competitor evidence docs** (`docs/benchmarks/COMPETITIVE_EVIDENCE_MATRIX_2026.md` + `LOCAL_AI_RUNTIME_COMPARISON.md`): every axis labelled PROVEN / MEASURED / INFERRED / NOT CLAIMED. Raw intelligence vs frontier MoE: **NOT CLAIMED**. No "beats all competitors" language.
* **Docker `--network none` verified**: producer fabric + 10k scale + full restart all PASS in `waggledance:phase17a` and the post-merge `waggledance:v3.9.0-producer-fabric-alpha-rc`. Detail: `docs/runs/phase17a_producer_fabric_scale_2026_05_04/docker_phase17a_verification.md`.

What did NOT change in Phase 17A: no Stage-2 atomic flip executed; no HUMAN_APPROVAL collected; no allowlist widening; no new high-risk autonomy variant; no provider HTTP adapter; no `/api/autonomy/query` route; v3.8.0 tag and release unchanged (`824176eb` target, `isPrerelease=false`, GitHub Latest); no consciousness claim.

### Phase 16F — Docker stable-gate closure / v3.8.0 stable release (landed on main 2026-05-04, tag created 2026-05-04T07:13:27Z)

Phase 16F is the Docker stable-gate closure sprint that closes the single remaining v3.8.0 stable blocker (g01 + g19) from Phase 16D. It does not introduce any new autonomy mechanism, allowlist family, or runtime entrypoint.

* **Docker now available for the first time in any WaggleDance dev shell.** Docker Desktop 4.71.0, Engine 29.4.1 (linux/amd64), buildx v0.33.0, compose v5.1.3, runc 1.3.5. `docker run --rm hello-world` PASS. `docker version` shows full client + server presence.
* **`waggledance:phase16f` image built.** `python:3.13-slim` base + apt (`curl`, `git`, `libvoikko1`, `voikko-fi`) + `requirements-ci.txt` (cross-platform CI subset; the original `requirements.lock.txt` was generated against a Windows + CUDA 11.8 dev environment and pins Linux-incompatible packages — switching to the documented CI requirements is a small deterministic fix permitted by the master prompt). Image ID `7bbac5ee5c72`, size 3.09 GB. Build duration ~7 min on a 24-CPU / 62 GiB / overlayfs / WSL2 host.
* **All four canonical proofs PASS inside Docker `--network none`** at corpus 104, exactly matching local results:
  * Phase 15 hint: `auto_promotions_total = 104`, `provider_jobs_delta = builder_jobs_delta = 0`, 5/5 negative cases pass
  * Phase 16A upstream: `structured_request_derived_total = 104`, `low_risk_hint_derived_total = 104`, 7/7 negative cases pass
  * Phase 16B P2 full restart: 104/104 served pre and post DB close+reopen, all 7 restart invariants True, `provider_jobs_delta_across_restart = builder_jobs_delta_across_restart = 0`, persisted `solver_count = 104` and `capability_features = 180` identical across reopen
  * autonomy_growth smoke (4 files): **16 passed, 27 conditional skips, 0 failures** in ~125 s
* **Local 3-iteration proof soak: 9/9 PASS, no flakes**, mean ~38 s/iter (`phase15_runtime_hint`, `phase16a_upstream`, `phase16b_full_restart` × 3).
* **Targeted local test sweep (`tests/autonomy_growth/`, `tests/storage/`, `tests/ui_hologram/`, `tests/autonomy/test_solver_router.py`, `tests/phase10/`): 349 passed, 0 failures, 30 warnings** (Voikko `__del__` cleanup ResourceWarning + SwigPy DeprecationWarning — pre-existing, not Phase 16F regressions). 215 s wall.
* **Bandit + pip-audit carry-forward: PASS.** Bandit HIGH = 0 (B324 cleanup intact), MEDIUM = 28 (carry-forward `B615 huggingface_unsafe_download` outside inner loop), LOW = 226 (carry-forward defensive try/except). pip-audit: 32 CVEs in 14 packages, all `low`, none reachable from inner loop, all tracked by Dependabot PRs.
* **Stage-2 atomic flip: NOT executed** (carry-forward; `STAGE2_CUTOVER_RFC.md` still gates).
* **HUMAN_APPROVAL: NOT collected** (CLAUDE.md rule 10 honored — Phase 16F is a build/proof session).
* **Allowlist: unchanged.** Six families: `scalar_unit_conversion`, `lookup_table`, `threshold_rule`, `interval_bucket_classifier`, `linear_arithmetic`, `bounded_interpolation`. 104 canonical seeds.
* **Outcome: v3.8.0 stable released 2026-05-04.** PR #68 squash-merged at 2026-05-04T07:08:23Z (merge commit `824176eb`). Post-merge verification on `git checkout --detach origin/main` reproduced all four local proofs (corpus 104, 0/0 deltas, all invariants True). Post-merge Docker rebuild `waggledance:v3.8.0-rc` reproduced all three canonical proofs `--network none`. Post-merge fresh clone from `https://github.com/Ahkeratmehilaiset/waggledance-swarm.git` saw all expected tags (v3.6.x + v3.7.x + v3.8.0) and reproduced smoke + full restart proof. Annotated tag `v3.8.0` created on `824176eb`; GitHub release published with `isPrerelease=false` at 2026-05-04T07:13:27Z; `gh release list` confirms v3.8.0 is GitHub Latest. All 22 stable gates final = PASS.

What did NOT change: no new autonomy mechanisms; no provider HTTP wiring; Stage-2 atomic flip RFC unchanged; `_DEFAULT_FAISS_DIR` (still `data/faiss/`); `_DEFAULT_CONTROL_PLANE_DIR` (still `data/control_plane/`); HTTP `/api/autonomy/query` route absent (deliberate scope limit; v3.8.0 is library/service-layer-stable, not HTTP-API-stable); single-process scope (RULE 10); LICENSE-CORE.md (no new core files); no consciousness claim.

### Phase 16D — Final stable-gate closure: Docker + Bandit B324 + v3.8.0 release decision (landed on main 2026-05-02)

### Phase 16D — Final stable-gate closure: Docker + Bandit B324 + v3.8.0 release decision (landed on main 2026-05-02)

Phase 16D is the final stable-gate closure attempt. It does not introduce new autonomy mechanisms. It resolves all 16 Bandit B324 weak-hash findings inherited from Phase 16C while preserving persisted semantic fingerprint, and re-confirms every other stable gate. The single remaining stable blocker is Docker (CLI still unavailable in dev shell — same situation as Phase 16B and Phase 16C).

* **Bandit B324 cleanup completed.** `python -m bandit -r waggledance/ core/` HIGH count: **16 → 0**. All 16 weak-hash lints (12 `hashlib.md5(...)` + 2 `hashlib.sha1(...)` calls + 2 builder forms) silenced via `usedforsecurity=False`. Per Python 3.11+ semantics this is a metadata-only flag with byte-identical digest output (verified live by 33-sample parity test before any code change). 12 source files touched (6 `core/embedding_cache.py`, plus `core/hive_support.py`, `core/knowledge_loader.py`, `core/learning_engine.py`, `core/night_enricher.py`, `core/whisper_protocol.py` ×2, `waggledance/adapters/feeds/feed_ingest_sink.py`, `waggledance/adapters/memory/chroma_vector_store.py`, `waggledance/observatory/mama_events/consolidation.py`, `waggledance/observatory/mama_events/taxonomy.py`).
* **Persisted semantic fingerprint preservation verified per RULE 25.** Pre/post `tools/run_full_restart_continuity_proof.py` baselines compared field-by-field: 14 scalar fields identical, 7 restart invariants identical, 6 per-operation served counts identical (covers all six low-risk families). Raw SQLite DB SHA differs (expected — SQLite metadata/page layout is non-deterministic across runs and is audit-only per RULE 25). Full report at `docs/runs/phase16d_final_stable_gate_closure_2026_05_02/persisted_semantics_preservation.md`.
* **All four canonical proofs re-run on Phase 16D branch at corpus 104.** Phase 15 hint: 104 promotions, provider/builder Δ = 0. Phase 16A upstream: 104 derived structured_request, 104 derived low_risk_hint, 104 served via capability lookup. Phase 16B full-corpus restart: 104/104 served pre and post DB close+reopen, persisted state identical, all restart invariants true, 0/0 across restart. **3-iteration proof soak: 9/9 PASS, no flakes.**
* **Docker still unavailable in dev shell.** Same as Phase 16B and Phase 16C P3. `docker version` → command not found. `Dockerfile`, `docker-compose.yml`, `.dockerignore` unchanged. **g01 / g19 status unchanged: FAIL_NOT_VERIFIED.**
* **Stable v3.8.0 BLOCKED solely by Docker.** g05 Bandit moves from PARTIAL_IMPROVED (Phase 16C) to **PASS** (Phase 16D). g21 persisted semantic fingerprint preservation: PASS. The blocker set narrows from Phase 16C's "1 substantive (Docker) + 1 residual cleanup (Bandit B324)" to Phase 16D's **"1 substantive (Docker)" only**. v3.8.0 is one external Docker verification away.

What did NOT change: no new autonomy mechanisms; Phase 9 14-stage human-gated promotion ladder; Stage-2 atomic flip (`STAGE2_CUTOVER_RFC.md` still gates that, unexecuted); `_DEFAULT_FAISS_DIR` (still `data/faiss/`); real Anthropic/OpenAI HTTP adapters (still follow-up); HTTP `/api/autonomy/query` route (does not exist; deliberate scope limit); single-process scope (RULE 10); no consciousness claim. Bandit installed as audit-tooling only; NOT added to runtime requirements. `LICENSE-CORE.md` unchanged (no new core files; no new BUSL-1.1 declarations needed).

### Phase 16C — Stable-gate closure attempt: Bandit + Docker + v3.8.0 release decision (landed on main 2026-05-02)

Phase 16C is a stable-gate closure sprint. It does not introduce new autonomy mechanisms. It attempts to close the two remaining v3.8.0 stable blockers from Phase 16B (Docker end-to-end, Bandit security audit) and re-verifies all carry-forward gates.

* **Bandit installed and run for the first time.** `python -m pip install bandit` (audit-tooling only, NOT added to runtime requirements). `python -m bandit -r waggledance/ core/` against 74,639 lines of code. Result: 16 HIGH (all `B324` weak-hash lints in cache / dedup / content-addressing code), 28 MEDIUM (mostly `B615 huggingface_unsafe_download` model-loading lints in NLP/translation lanes), 226 LOW (defensive try/except/pass, asserts, `B105` false-positive matches against literal status / threshold values). **Zero HIGH or MEDIUM findings reachable from the autonomy inner loop.** All 9 inner-loop findings are LOW false-positives. Full audit at `docs/security/PHASE16C_SECURITY_AUDIT.md`. Bandit JSON at `docs/runs/phase16c_stable_gate_closure_2026_05_02/bandit_report.json`.
* **Docker still unavailable in dev shell.** `docker version` → command not found. Same situation as Phase 16B P6. `Dockerfile` and `docker-compose.yml` unchanged; documented commands in `DOCKER_QUICKSTART.md` remain "not tested in this session". An external operator with Docker installed must verify before stable v3.8.0.
* **All four canonical proofs re-run on Phase 16C branch at corpus 104.** Phase 15 hint: 104 promotions, provider/builder Δ = 0. Phase 16A upstream: 104 derived structured_request, 104 derived low_risk_hint, 104 served via capability lookup. Phase 16B full-corpus restart: 104 / 104 served pre and post DB close+reopen, persisted state identical, all restart invariants true, provider/builder Δ across restart = 0. **3-iteration proof soak: 9 / 9 iterations pass at corpus 104, no flakes.**
* **Stable v3.8.0 BLOCKED by Docker.** g05 Bandit improved from PARTIAL (Phase 16B) to PARTIAL_IMPROVED (Phase 16C — Bandit now run). g01 Docker remains FAIL_NOT_VERIFIED. Per fail-closed rule, the prerelease outcome is `v3.7.7-stable-gate-alpha`. The follow-up to unblock v3.8.0 is documented: install Docker on a verification machine; run the documented Docker proofs; optionally apply a `chore(security): silence Bandit B324 weak-hash lints` cleanup that adds `usedforsecurity=False` to the 16 affected `hashlib.md5(...)` / `hashlib.sha1(...)` calls.

What did NOT change: Phase 9 14-stage human-gated promotion ladder; Stage-2 atomic flip (`STAGE2_CUTOVER_RFC.md` still gates that, unexecuted); `_DEFAULT_FAISS_DIR` (still `data/faiss/`); real Anthropic/OpenAI HTTP adapters (still follow-up); HTTP `/api/autonomy/query` route (does not exist; deliberate scope limit); single-process scope (RULE 10); no consciousness claim. No new files in `waggledance/core/*`; no LICENSE-CORE.md additions needed.

### Phase 16B — Stabilization, full-corpus restart, proof soak, 100+ release gate, security self-audit (landed on main 2026-05-01)

Phase 16B is a stabilization and release-gate audit. It does not introduce new autonomy mechanisms; it hardens reproducibility, restart survival, repeatability, security auditability, and release-doc truth so an external GitHub reader can decide whether v3.8.0 stable is justified.

* **Full-corpus restart-continuity proof.** `tools/run_full_restart_continuity_proof.py` drives the full canonical corpus through `AutonomyService.handle_query` using flat domain context only, harvests, closes the control-plane SQLite DB, reopens, and re-serves the same flat upstream input. Result: 104 / 104 served via capability lookup before and after restart; persisted solver count and `solver_capability_features` count identical across reopen; `provider_jobs_delta_across_restart = builder_jobs_delta_across_restart = 0`. Smoke test `tests/autonomy_growth/test_full_restart_continuity_smoke.py` locks the proof JSON invariants.
* **Proof soak / repeatability.** `tools/run_phase16b_proof_soak.py --iterations 5` runs Phase 15, Phase 16A, and Phase 16B-full-restart proofs to unique temp dirs (per the soak artifact-isolation rule) and asserts JSON invariants per iteration. Result: **15 / 15 iterations pass, no flakes**, mean ~33 s / iteration.
* **100+ solver release gate.** `low_risk_seed_library.py` raised from 98 to 104 seeds (+1 per low-risk family, no allowlist widening): `watt_hours_to_joules` (energy / scalar_unit_conversion), `month_to_quarter` (seasonal / lookup_table), `solar_yield_above_50kwh` (energy / threshold_rule), `co2_band` (thermal / interval_bucket_classifier), `hex_neighbor_combine_4d` (general / linear_arithmetic), `noise_to_focus_curve` (general / bounded_interpolation). The Phase 15 / Phase 16A / Phase 16B canonical proofs all now report `corpus_total = 104` and `auto_promotions_total = 104`. New test `test_seed_library_meets_v3_8_0_release_gate_minimum` enforces the 100-solver minimum.
* **Bounded security self-audit.** `docs/security/PHASE16B_SECURITY_SELF_AUDIT.md`. CI grep clean (0 unauthorized eval()); manual grep on Phase 11+ files clean (no unauthorized network / subprocess / actuator writes); `pip-audit` reports 32 dependency CVEs across 14 packages, all classified `low` and not reachable from inner loop (active Dependabot PRs already track the upgrades). `bandit` not installed in this dev shell; `bandit` absence blocks v3.8.0 stable but not the prerelease.
* **Install / API truth.** Three install sources documented as a deliberate layering: `requirements.txt` (developer local), `requirements.lock.txt` (Docker + reproduce-from-clone), `requirements-ci.txt` (CI). HTTP/API stable-gate decision: **v3.8.0 is a library / service-layer stable release, not an HTTP-API stable release**; no `/api/autonomy/query` route is in scope. See `docs/runs/phase16b_stabilization_release_gate_2026_05_01/dependency_install_and_api_surface_truth.md`.
* **Stable gate ledger.** Machine-readable JSON + human-readable MD at `docs/runs/phase16b_stabilization_release_gate_2026_05_01/stable_gate_inventory.{json,md}`. Outcome: stable v3.8.0 remains blocked by Docker not-tested + remote/fresh-clone-against-post-merge-main + `bandit` not installed.

What did NOT change: Phase 9 14-stage human-gated promotion ladder; Stage-2 atomic flip (`STAGE2_CUTOVER_RFC.md` still gates that, unexecuted); `_DEFAULT_FAISS_DIR` (still `data/faiss/`); real Anthropic/OpenAI HTTP adapters (still follow-up); HTTP `/api/autonomy/query` route (does not exist; deliberate scope limit); single-process scope (RULE 10); no consciousness claim.

### Phase 16A — Upstream structured_request propagation + restart continuity (landed on main 2026-05-01)

Phase 16A lifts `structured_request` derivation up to the **service-layer caller** `AutonomyService.handle_query(query, context, priority)`. External callers no longer need to know about the autonomy lane *or* the nested `structured_request` grammar — they pass natural flat domain fields and the service layer derives the nested shape.

* **`upstream_structured_request_extractor.py`** — deterministic Python extractor; reads only flat `context` keys (`operation`, `from_unit`, `to_unit`, `value`, `key`, `domain`, `subject`, `x`, `operator`, `inputs`, `input_columns_signature`, `x_var`, `y_var`, optional `cell_coord` / `intent_seed` / `builtin_solver_succeeded`); zero provider calls, no LLM, no embeddings. Lifts flat-to-nested grammar; refuses to overwrite caller-supplied `structured_request` or `low_risk_autonomy_query` (rejected_ambiguous), strips them at the service layer to enforce the bypass-refusal contract. Six allowlisted operations, no widening.
* **`AutonomyService.handle_query` wiring** — backwards-compatible. The upstream extractor runs *after* admission control and *before* `compatibility.handle_query`. On derived structured_request, `context["structured_request"]` is set in place; the runtime layer's Phase 15 hint extractor then derives `context["low_risk_autonomy_query"]` exactly as before. Errors from the upstream extractor never break the production path; they are counted on `service.upstream_structured_request_stats()`.
* **Live before/after proof through the service-layer caller** (`tools/run_upstream_structured_request_proof.py`). 98-seed corpus passes through `AutonomyService.handle_query` using **flat domain context only** — no `structured_request`, no `low_risk_autonomy_query`. Pass 1: 0 served / 98 buffered miss signals; harvest: 98 promoted; pass 2 cold: 98 served via capability lookup; pass 3 warm: warm-cache hits dominate; provider/builder delta during proof = 0/0; negative corpus 7/7 passed (ambiguous, high-risk, missing-fields, malformed, free-text-skip, manual-hint-injection-refused, builtin-precedence).
* **Restart continuity smoke** (`tests/autonomy_growth/test_upstream_restart_continuity.py`). Six-seed corpus through service, harvest + auto-promote, **close** control plane, **reopen**, drive same flat upstream input through rebuilt service: 6/6 served via capability lookup; persisted solver count and capability-feature count identical across reopen; provider/builder delta across restart = 0/0.
* **Reality View** `autonomy_runtime_harvest_kpis` panel extended with `live_runtime_upstream_lift_signature_features_total` (no new panel — RULE P7). The existing Phase 15 metric `live_runtime_hint_aware_signals_total` post-Phase-16A reflects upstream-derived signals exclusively when the service layer is in the call path.
* **Truth map updated** — `docs/architecture/RUNTIME_ENTRYPOINT_TRUTH_MAP.md` includes a Phase 16A P1 inventory section selecting `AutonomyService.handle_query` and rejecting `CompatibilityLayer.handle_query` (thin pass-through), `ChatService.handle` (different lane), `AutonomyService.execute_mission` (mission-shaped), and the FastAPI route surface (no `/api/autonomy/query` route exists).

What did NOT change: production HTTP / FastAPI surface for query (still no `/api/autonomy/query`); six-family allowlist (RULE 13); Stage-2 atomic flip (`STAGE2_CUTOVER_RFC.md` still gates that); `_DEFAULT_FAISS_DIR` (still `data/faiss/`); real Anthropic/OpenAI HTTP adapters (still follow-up work); single-process scope (RULE 14); no consciousness claim.

### Phase 15 — Automatic runtime hints + alpha release readiness (landed on main 2026-05-01)

Phase 15 lifts autonomy-hint derivation up to the **production query handler** `AutonomyRuntime.handle_query(query, context)`. Callers no longer need to know about the autonomy lane.

* **`runtime_hint_extractor.py`** — deterministic Python extractor; reads only `context["structured_request"]`; supports six subkeys (one per allowlisted family); zero provider calls, no LLM, no embeddings. Explicit rejection kinds for ambiguous / high-risk-shaped / missing-fields / not-structured / malformed input.
* **`AutonomyRuntime.handle_query` wiring** — backwards-compatible context-hint injection. The hint extractor runs between context enrichment and `solver_router.route`; on derived hint, `context["low_risk_autonomy_query"]` is set automatically. Errors from the extractor never break the production path.
* **Live before/after proof through the production caller** (`tools/run_automatic_runtime_hint_proof.py`). 98-seed corpus passes through `AutonomyRuntime.handle_query`; pass 1: 0 served / 98 buffered miss signals; harvest: 98 promoted; pass 2: 98 served via capability lookup; provider/builder delta during proof = 0/0; negative corpus 5/5 passed.
* **Reality View** existing `autonomy_runtime_harvest_kpis` panel extended with `live_runtime_hint_aware_signals_total` (no new panel — RULE P7).
* **Release surface** — `docs/github/REPOSITORY_PRESENTATION.md` (external presentation text), `docs/release/RELEASE_READINESS.md` (alpha/release tag policy), `docs/deployment/DOCKER_QUICKSTART.md` (Docker contract; not tested in this session).
* **P5 self-state snapshot + P6 episodic continuity deferred to Phase 16.** They are observability primitives; deferred per session priority stack to focus on the P2–P4 release gate.

What did NOT change: production callers above `handle_query` do not yet emit `structured_request` (they can opt in with the natural payload); six-family allowlist (RULE 13); Stage-2 atomic flip (`STAGE2_CUTOVER_RFC.md` still gates that); `_DEFAULT_FAISS_DIR` (still `data/faiss/`); real Anthropic/OpenAI HTTP adapters (still follow-up work); single-process scope (RULE 14); no consciousness claim.

### Phase 14 — Live runtime hot-path wiring (landed on main 2026-05-01)

Phase 14 wires the autonomy lane into the **production reasoning entrypoint** and collapses the autonomy consult hot path into in-process caches.

* **`SolverRouter.route(...)` autonomy consult.** Backwards-compatible context-hint extension (`context["low_risk_autonomy_query"]`). When built-in selection falls back AND a hint is supplied, the router invokes the autonomy consult lane via `build_autonomy_consult(RuntimeQueryRouter)`. Existing callers see no behaviour change.
* **`HotPathCache`** — `WarmCapabilityIndex` + `ParsedArtifactCache` + `BufferedSignalSink`. Warm hits skip SQLite + JSON parse entirely. Miss-signal emission moves off the synchronous hot path with a bounded queue (≤ 1000 signals / ≤ 500 ms age). Documented hard-kill loss bound.
* **Canonical seed library expanded** to 98 entries (Phase 13 had 68). All 6 allowlisted families + 8 hex cells.
* **Live runtime proof** through `SolverRouter.route(...)`. 98 corpus → 0 misses after one harvest cycle. **Pre-cache p50 ≈ 0.39 ms; warm p50 ≈ 0.06 ms; warm-vs-pre-cache 6.17× faster** (P3 floor 5× met; stretch 10× missed and documented). Provider/builder delta during proof: 0.
* **Reality View** existing `autonomy_runtime_harvest_kpis` panel extended with Phase 14 capability-indexed solver counts (no new panel — RULE P7).
* **Inner-loop zero-provider invariant** locked by per-run delta test.

What did NOT change: built-in authoritative solvers (Layer 3 retains precedence), the Phase 9 promotion ladder (4 runtime stages still require `human_approval_id`), Stage-2 atomic flip (still gated by `STAGE2_CUTOVER_RFC.md`), `_DEFAULT_FAISS_DIR` (still `data/faiss/`), real Anthropic/OpenAI HTTP adapters (still follow-up work), six-family allowlist (RULE 19 honoured), single-process scope (RULE 20).

### Phase 13 — Runtime-integrated harvest + capability-aware uptake (landed on main 2026-04-30)

Phase 13 connects the autonomy loop to the **real runtime seam** and adds **capability-aware** dispatch so harvested structure stays useful at scale.

* **Schema v4** — new `solver_capability_features` table indexed on `(family_kind, feature_name, feature_value)`. Forward-only migration from v3.
* **Runtime query router (`runtime_query_router.py`).** Real runtime seam. `RuntimeQueryRouter.route(query)` dispatches via capability-aware lookup first (then family-FIFO fallback), and on miss emits a deduped `runtime_gap_signal` automatically with a `min_signal_interval_seconds` throttle.
* **Capability features (`family_features.py`).** Per-family structured feature extractors. Each promoted solver carries a small feature set (e.g. `from_unit`+`to_unit` for `scalar_unit_conversion`, `domain` for `lookup_table`, `subject`+`operator` for `threshold_rule`).
* **Capability-aware dispatch (`solver_dispatcher.dispatch_by_features`).** Indexed `solver_capability_features` lookup with ALL-features-match semantics; refuses unbounded scans on empty feature sets.
* **Canonical seed library (`low_risk_seed_library.py`).** 68 seeds across all 6 families and 8 cells. Local-first; no provider call.
* **End-to-end before/after proof.** `tools/run_runtime_harvest_proof.py` routes 68 structured runtime queries through the real router, harvests, then re-routes. Pass 1: 68 misses + 68 auto-emitted signals. Pass 2: 68 served via capability lookup. `provider_jobs` = 0.
* **Reality View `autonomy_runtime_harvest_kpis` panel.** Aggregate-only; per-family + per-cell signal counts; honest self-starting / teacher-assisted / human-gated split.

What did NOT change: built-in authoritative solvers (Layer 3 retains precedence), the Phase 9 promotion ladder (4 runtime stages still require `human_approval_id`), Stage-2 atomic flip (still gated by `STAGE2_CUTOVER_RFC.md`), `_DEFAULT_FAISS_DIR` (still `data/faiss/`), real Anthropic/OpenAI HTTP adapters (still follow-up work).

### Phase 12 — Self-starting local-first autogrowth loop (landed on main 2026-04-30)

Phase 12 closes the autonomy loop's missing left-hand side: a self-starting intake that converts runtime evidence into queued growth intents *without a human trigger*, plus a scheduler that drains the queue end-to-end at scale.

* **Schema v3** — five new normalized tables: `runtime_gap_signals`, `growth_intents`, `autogrowth_queue`, `autogrowth_runs`, `growth_events`. Plus indexes for `(kind, observed_at)`, `(family_kind, cell_coord)`, `(status, priority DESC)`, `(intent_id)`, `(backoff_until)`, `(event_kind, occurred_at)`. Forward-only migration from v2.
* **Self-starting intake (`gap_intake.py`)** — `RuntimeGapDetector` records signals; `digest_signals_into_intents` folds them into intents and enqueues them. Mirror events emit into `growth_events`.
* **Local-first scheduler (`autogrowth_scheduler.py`)** — `AutogrowthScheduler.run_until_idle()` claims queue rows atomically, dispatches to `LowRiskGrower`, records run outcomes, emits growth events. Concurrent schedulers do not double-claim.
* **Family reference oracles (`family_oracles.py`)** — pure-Python independent reference implementations for the six allowlisted families, used by the scheduler as the shadow oracle.
* **Mass-safe proof** — `tools/run_mass_autogrowth_proof.py` end-to-end self-starts 30 promotions across all 6 families and 8 hex cells. **0 rejections, 0 errors, 0 provider calls.** Artifact at `docs/runs/phase12_self_starting_autogrowth_2026_04_30/autonomy_proof.md`.
* **Reality View** — new `autonomy_self_starting_kpis` aggregate panel; Phase 11 `autonomy_low_risk_kpis` panel still works alongside it.
* **Inner-loop / outer-loop truth** — locked by `tests/autonomy_growth/test_outer_inner_loop_truthful.py`: the mass-safe inner loop runs with `provider_jobs` empty. Outer-loop teacher remains Claude Code Opus 4.7 (position 1 in the LLM solver generator priority list).

What did NOT change: built-in authoritative solvers (Layer 3 retains precedence), the Phase 9 promotion ladder (4 runtime stages still require `human_approval_id`), Stage-2 atomic flip (still gated by `STAGE2_CUTOVER_RFC.md`), `_DEFAULT_FAISS_DIR` (still `data/faiss/`), real Anthropic/OpenAI HTTP adapters (still follow-up work).

### Phase 11 — Autonomous low-risk solver growth lane (landed on main 2026-04-30)

Phase 11 closes the first end-to-end no-human autonomous solver growth loop, bounded by `docs/architecture/LOW_RISK_AUTOGROWTH_POLICY.md`. The bounded allowlist contains six side-effect-free deterministic families: `scalar_unit_conversion`, `lookup_table`, `threshold_rule`, `interval_bucket_classifier`, `linear_arithmetic`, `bounded_interpolation`. Outside the allowlist, growth remains human-gated via the existing Phase 9 promotion ladder.

* **`waggledance/core/autonomy_growth/`** — eight new BUSL-1.1 modules: `low_risk_policy`, `solver_executor`, `validation_runner`, `shadow_evaluator`, `solver_dispatcher`, `auto_promotion_engine`, `low_risk_grower`, plus the package init.
* **Control plane v2** — six new normalized tables (`solver_artifacts`, `family_policies`, `validation_runs`, `shadow_evaluations`, `promotion_decisions`, `autonomy_kpis`) plus their indexes. Forward-only migration from v1 to v2; existing tables unchanged.
* **Reality View** — new `autonomy_low_risk_kpis` aggregate panel; never-fabricate invariant preserved.
* **End-to-end proof** — `tools/run_autonomy_proof.py` grows three solvers (one each from three different families), runs through the closed loop, and dispatches them via runtime. Artifact at `docs/runs/phase11_autogrowth_2026_04_29/autonomy_proof.md` + `.json`.
* **No new SemVer tag at squash time.** Decision deferred to post-merge; an optional `v3.7.0-autogrowth-alpha` prerelease tag may be created.

What did NOT change: built-in authoritative solvers (Layer 3 retains precedence), the Phase 9 promotion ladder (4 runtime stages still require `human_approval_id`), Stage-2 atomic flip (still gated by `STAGE2_CUTOVER_RFC.md`), `_DEFAULT_FAISS_DIR` (still `data/faiss/`), real Anthropic/OpenAI HTTP adapters (still follow-up work).

### Phase 10 — Foundation, Truth, Builder Lane (landed on main 2026-04-28T12:14:15Z)

Phase 10 substrate landed via [PR #54](https://github.com/Ahkeratmehilaiset/waggledance-swarm/pull/54) (squash commit `08b7e8c`) after green CI. Branch `phase10/foundation-truth-builder-lane` (pre-squash tip `24ef97e`). Eight phases collapsed into one squash commit on `main`:

* **P0** — bootstrap reality + state file
* **P1** — storage / runtime / cutover truth audit (`docs/journal/2026-04-28_storage_runtime_truth.md` + `docs/journal/2026-04-28_cutover_model_classification.md`). Formal classification: v3.6.0 = `MODEL_C_NOOP_ALREADY_COMPLETE`; future Stage-2 flip = `MODEL_D_AMBIGUOUS` until an RFC defines the mechanism (now closed by `docs/architecture/STAGE2_CUTOVER_RFC.md`).
* **P2** — scale-safe control-plane / data-plane foundation (`waggledance/core/storage/`, 16-table SQLite control plane, path resolver with override + control-plane binding + static default precedence, drop-in compatible with the legacy `_DEFAULT_FAISS_DIR`).
* **P3** — provider plane execution layer (`waggledance/core/providers/`, JSON-schema-validated dispatch, `ClaudeCodeBuilder` subprocess lane with dry-run fallback, mentor-output advisory boundary enforced at the API surface).
* **P4** — solver bootstrap / synthesis foundation (`solver_synthesis/cold_shadow_throttler.py`, `llm_solver_generator.py`, `solver_bootstrap.py` orchestrator, `family_specs/`).
* **P5** — Reality View scale-aware aggregator (`ui/hologram/scale_aware_aggregator.py`, no per-solver lists at scale, control-plane-driven aggregations).
* **P6–P7** — README / CURRENT_STATUS / CHANGELOG / MAGMA truth fixes; targeted truth / regression / no-leak tests.
* **P8** — release bundle + merge readiness checklist + release notes draft.

All P2–P5 protected files use Change Date 2030-12-31 per Phase 10 RULE 6 (`LICENSE-CORE.md` updated). Phase 8.5 branches remain READ-ONLY per Phase 10 RULE 12. Atomic flip is **not executed** by Phase 10 — it remains a separate Prompt 2 risk domain (Phase 10 RULE 18) and is now formally specified in [`docs/architecture/STAGE2_CUTOVER_RFC.md`](docs/architecture/STAGE2_CUTOVER_RFC.md).

**Phase 10 test counts (landed):** 51 control-plane / provider / synthesis tests + 8 Reality-View scale tests + 13 truth / regression tests = 72 new targeted tests, all green at squash time.

### Latest release — v3.6.0 Phase 9 Autonomy Fabric (review-only)

PR [#51](https://github.com/Ahkeratmehilaiset/waggledance-swarm/pull/51) squash-merged into main at 2026-04-27T12:36:32Z. The release lands the **autonomy fabric scaffold**: 16 phases of architecture (F–Q) wiring together the always-on cognitive kernel, cognition IR, vector identity, world model, conversation layer, provider plane with 6-layer trust gate, builder/mentor lanes, autonomous solver synthesis with cold-shadow throttling, memory tiers, real hex runtime topology, 14-stage promotion ladder with human gate, proposal compiler, local model distillation safe scaffold, and cross-capsule observer.

**This is a review-only release.** The atomic runtime flip — pointing the live runtime read path at the new fabric — is intentionally deferred to a separate Prompt 2 session, gated on a signed human approval artifact (`docs/atomic_flip_prep/03_HUMAN_APPROVAL.yaml.draft`) and the completion of the 400h gauntlet campaign.

**Key numbers:**
- 657/657 Phase 9 targeted tests passing in ~7 s
- All 5 CI checks green (unified, test 3.11/3.12/3.13, security-scan)
- 147/147 Phase 9 .py files SPDX-tagged (107 BUSL-1.1 + 40 Apache-2.0)
- 4 real-data evidence artifacts under `docs/runs/phase9_*` (Reality View render, kernel tick, conversation probe, proposal compiler bundle)

See [`docs/architecture/PHASE_9_ROADMAP.md`](docs/architecture/PHASE_9_ROADMAP.md) for the full navigation surface.

### 400h UI Gauntlet Campaign — FINAL

Campaign concluded 2026-04-26 (`docs/runs/ui_gauntlet_400h_20260413_092800/final_400h_summary.md`):

| Mode | Cumulative | Target | Status |
|---|---|---|---|
| HOT | 207.11h | 80h | ✅ 258.9% — target exceeded |
| WARM | 120.18h | 120h | ✅ 100.1% — target met |
| COLD | 88.05h | 200h | ⚠️ 44.0% — partial |
| **Total** | **415.34h** | **400h** | **103.8% — campaign complete** |

- 60 807 queries total
- 0 XSS hits (zero-tolerance target met)
- 0 DOM breaks (zero-tolerance target met)
- Watchdog and auto-commit processes stopped
- `final_400h_*` summaries generated

### Phase 8.5 producer subsystems — deferred follow-up PRs

Phase 9 ships the IR adapter contracts (`from_curiosity.py`, `from_self_model.py`, `from_dream.py`, `from_hive.py`); the producer subsystems on `phase8.5/*` branches ship as separate follow-up PRs after this release. Per the local-only branch audit (`docs/runs/local_only_branch_audit.md`):

| Branch | Substantive commits | Status |
|---|---|---|
| `phase8.5/vector-chaos` | 4 | DEFER → follow-up PR (R7.5 — Vector Writer Resilience) |
| `phase8.5/curiosity-organ` | 10 | DEFER → follow-up PR (Session A — Curiosity Organ) |
| `phase8.5/self-model-layer` | 16 | DEFER → follow-up PR (Session B — Self-Model Layer) |
| `phase8.5/dream-curriculum` | 22 | DEFER → follow-up PR (Session C — Dream Pipeline) |
| `phase8.5/hive-proposes` | 21 | DEFER → follow-up PR (Session D — The Hive Proposes) |

**Next post-release step:** open the Phase 8.5 follow-up PRs in dependency order. After all five land, the Prompt 2 atomic runtime flip session can be scheduled (gated on signed `HUMAN_APPROVAL.yaml`).

---

## Tri-Stack Architecture

WaggleDance now has three stacks:

| Stack | Entrypoint | Status | Notes |
|-------|-----------|--------|-------|
| **Legacy** | `start.py` / `main.py` | Archived (`_archive/backend-legacy/`) | `hivemind.py` + `core/*.py` monolith |
| **Hexagonal** | `waggledance.adapters.cli.start_runtime` | Integrated | `waggledance/` package, ports & adapters |
| **Autonomy** | `waggledance.core.autonomy.runtime` | **Merged to master** | Solver-first, capability-driven |

The autonomy runtime is wired into the legacy stack. When `runtime.primary=waggledance`
and `compatibility_mode=false`, queries route through the autonomy runtime first.
See `docs/AUTONOMY_RUNTIME.md` for details.

---

## New Architecture Layers

| Layer | Path | Role | Deps |
|-------|------|------|------|
| **Domain** | `waggledance/core/domain/` | DTOs, enums, value objects | None |
| **Ports** | `waggledance/core/ports/` | 8 `typing.Protocol` interfaces | Domain only |
| **Orchestration** | `waggledance/core/orchestration/` | Scheduler, RoundTable, Orchestrator, routing | Domain + Ports |
| **Policies** | `waggledance/core/policies/` | FallbackChain, EscalationPolicy, confidence | Domain only |
| **Services** | `waggledance/application/services/` | ChatService, MemoryService, LearningService, etc. | Core |
| **Adapters** | `waggledance/adapters/` | Ollama, ChromaDB, HotCache, SQLite, FastAPI | External libs |
| **Bootstrap** | `waggledance/bootstrap/` | DI Container, EventBus | All layers |

Dependency rule: inner layers never import outer layers. `core/` has zero external dependencies.

---

## What Is Stable

| Component | Evidence |
|-----------|----------|
| 8 port contracts | `docs/PORT_CONTRACTS.md`, locked, 0 mismatches |
| State ownership rules | `docs/STATE_OWNERSHIP.md`, 9 owners, 7 forbidden paths |
| Domain models (5 modules) | `tests/contracts/` — 22 tests |
| Orchestration (scheduler, routing, round table, micromodel) | `tests/unit_core/` — 130+ tests |
| Application services (chat, memory, learning) | `tests/unit_app/` — 16 tests |
| Adapter implementations (9 adapters + SQLiteTrustStore) | `tests/unit/` — 300+ tests |
| DI container (stub + production) | Smoke tests pass both modes |
| Legacy test suite | 87 suites, 2754 tests, 0 failures, Health 84/100 |
| Big Sprint modules (v1.17.0) | 15 new core modules, 25 new test files |
| Production bug fixes (BUG 1-3) | Regression tests in place |
| **Autonomy runtime (v3.5.1)** | **4968 pytest tests (phases 1-9 + continuity + regression + user-model + hologram-v6 + hybrid + backfill + candidate-lab + accelerator + gemma + e2e), all pass** |
| **Gemma 4 dual-tier (v3.5.1)** | **Optional fast/heavy model profiles, 70 tests, 2h soak PASS, feature-flagged OFF by default** |
| **Cutover validation** | **"FULL AUTONOMY MODE ENABLED" — 42/42 modules** |
| **Regression gates** | **migration, night_learning_v2, resource_kernel, specialist_models — 41 tests** |
| **v3.2 self-entity** | **Epistemic uncertainty, motives, attention budget, dream mode, consolidator, meta-optimizer** |
| **v3.2 projections** | **Narrative (en/fi), introspection (profile-gated), autobiographical index, validator** |
| **v3.2 MAGMA expansion** | **Confidence decay, self_reflection/simulated events, dream replay, 9-tier provenance** |
| **v3.3 User Model Lite** | **User entity in CognitiveGraph, promise tracking from GoalEngine, verification fail counting, hologram MAGMA nodes** |
| **v3.3.3 Hologram v6** | **32 nodes (4 rings), node_meta contract, docked panels, 8 tabs + Chat, FI/EN i18n, 66 tests, no fake floors** |
| MicroModel V1 routing (restored v1.17.0) | End-to-end: routing_policy → chat_service → orchestrator |
| Persistent TrustStore (v1.17.0) | SQLiteTrustStore in container.py (prod=SQLite, stub=InMemory) |

---

## What Is Experimental / Future Work

| Item | Status | Notes |
|------|--------|-------|
| MicroModel V1 (PatternMatchEngine) | **WIRED** (v1.17.0) | Legacy: `shared_routing_helpers.probe_micromodel()`. Hex: routing_policy → chat_service. Cached singleton. |
| MicroModel V2 (ClassifierModel) | File exists, unwired | `data/micromodel_v2.pt` (3.8MB) exists but no runtime loading code. V1 patterns only. |
| MicroModel V3 (LoRA) | Framework only | `core/lora_readiness.py` checker, `tools/train_micromodel_v3.py` script. No trained adapter yet (needs 4h+ GPU). |
| Rule engine port + adapter | Not designed | No current requirement |
| Sensor/MQTT adapter | **WIRED** (v1.18.0) | `MQTTSensorIngest` → `SensorHub.start()` step 6, writes SharedMemory, dispatches alerts |
| Persistent TrustStore (SQLite) | **COMPLETE** (v1.17.0) | `SQLiteTrustStore` in container.py (prod=SQLite, stub=InMemory, graceful fallback) |
| Voice adapters (Whisper/Piper) | Legacy only | Not ported to new architecture |
| Dashboard wiring | **6 new endpoints** (v1.18.0) | route/explain, route/telemetry, experiments, graph/replay, graph/stats, learning/ledger |
| Old code removal | Blocked | Needs 24h production validation first |
| `micromodel` route type | **RESTORED** (v1.17.0) | `ALLOWED_ROUTE_TYPES = {hotcache, micromodel, memory, llm, swarm}` |
| `rules` route type | Excluded | Not in allowed types |
| Night learning loop wiring | **WIRED** (v1.18.0) | ActiveLearningScorer + LearningLedger in NightModeController/NightEnricher |
| Request loop telemetry | **WIRED** (v1.18.0) | RouteTelemetry + LearningLedger + RouteExplainability in chat_handler + chat_service |
| FallbackChain | Dead code | Defined and tested (10 tests) but never used in production |
| Runtime convergence | **Decided** (v1.18.0) | Legacy primary, hex = forward path, `core/shared_routing_helpers.py` convergence layer |

---

## Gate Tests

These test suites are the gatekeepers — all must pass before any change is merged.

| Gate | Command | Tests | What It Guards |
|------|---------|-------|---------------|
| Contract tests | `pytest tests/contracts/ -v` | 22 | Port signatures, DTOs, route types, event types |
| Core unit tests | `pytest tests/unit_core/ -v` | 130+ | Scheduler, routing, fallback, micromodel, active learning, telemetry, ledger |
| App unit tests | `pytest tests/unit_app/ -v` | 16 | ChatService, LearningService (BUG 3 regression) |
| Adapter unit tests | `pytest tests/unit/ -v` | 300+ | All adapters, container, event bus, SQLiteTrustStore |
| Integration tests | `pytest tests/integration/ -v` | 90 | Runtime CLI, smoke, user scenarios, benchmarks, shadow compare |
| Autonomy unit tests | `pytest tests/autonomy/ -v` | 1600 | Domain models, phases 1-9, runtime wiring, user-model |
| Regression gates | `pytest tests/migration/ tests/night_learning_v2/ tests/resource_kernel/ tests/specialist_models/ -v` | 41 | Alias migration, night pipeline, resource kernel, specialist models |
| Continuity tests | `pytest tests/continuity/ -v` | 171 | v3.2: self-entity, uncertainty, attention, projections, MAGMA |
| Night learning v2 | `pytest tests/night_learning_v2/ -v` | 60+ | Consolidator, dream mode, pipeline |
| Legacy suite | `python tools/waggle_backup.py --tests-only` | 2754 | Old stack regression (87 suites) |
| Stub smoke | `Container(stub=True).build_app()` | 1 | DI wiring, no crash |
| Non-stub smoke | `Container(stub=False).memory_repository` | 1 | ChromaMemoryRepository, not InMemory |
| Compile check | `python -m compileall waggledance/ core/ -q` | - | No syntax errors |

---

## Runtime Rules

1. **No silent fallbacks** — Non-stub mode must fail fast if a real adapter is unavailable; never silently fall back to in-memory stub.
2. **Single-writer ownership** — Every persistent state type has exactly one writing component (see `docs/STATE_OWNERSHIP.md`).
3. **No fire-and-forget tasks** — Every `asyncio.create_task()` must use `_track_task()` or `TaskGroup` with error callbacks.
4. **No type mixing** — Never assign incompatible types to a typed attribute (prevents BUG 1).
5. **Event bus failure policy** — Log with `exc_info=True`, increment failure counter, never swallow, max 5s handler timeout.
6. **Env reads centralized** — Only `WaggleSettings` (in `settings_loader.py`) may read `os.environ`. All other config via `ConfigPort`.
7. **Route type whitelist** — `select_route()` may only return `{hotcache, micromodel, memory, llm, swarm}`.
8. **HTTP routes are thin** — Routes delegate to services; no business logic, no direct store writes.
9. **Ollama timeout >= 120s** — Prevents embed timeouts under load (BUG 3 fix).
10. **Stall detection active** — `LearningService` resets after N consecutive empty cycles (`night_stall_threshold`, default 10).
11. **Shared ResourceKernel** — Single instance via DI container; no split-brain.
12. **Thread pool reuse** — Use module-level `_ASYNC_POOL` for async-to-sync bridging; never create per-call pools.
13. **SQLite safety** — All persistence stores have `__del__` safety nets for connection cleanup.

---

## v3.3.1 Overnight Production Validation (2026-03-21)

10-hour overnight production run completed successfully:

| Metric | Value |
|--------|-------|
| Cycles completed | 230/231 (99.6% uptime) |
| Agents active | 26 |
| Chat tests passed | 20/23 (5 timeouts during LLM contention) |
| Electricity tracked | 1.568 -> 0.91 c/kWh |
| Helsinki temp | 2.3 -> 1.3 C |

### Bugs Found & Fixed (30+ across 10 commits)

**P0 Critical:**
- settings.yaml duplicate keys causing silent override
- learning_engine score leak (unbounded list growth)

**P1 High:**
- ResourceKernel split-brain (3 instances -> 1 shared via DI)
- Thread-per-call leak in `_run_maybe_async` (shared ThreadPoolExecutor)
- Race condition in data_scheduler PriorityLock

**P2 Medium:**
- Dashboard XSS (15+ innerHTML sites -> `esc()` function)
- RSS feed URLs returning 404 (replaced with Yle feeds + bozo checking)
- 5x SQLite stores missing `__del__` safety nets
- Silent exception swallowing in data_scheduler
- Missing `_last_training_results` init in NightLearningPipeline
- Dead `get_resource_kernel()` code in elastic_scaler

**Tools Added:**
- `tools/night_monitor.py` — 10h sync HTTP-based production monitor

---

## v3.5.0 Proof Run (2026-04-02 to 2026-04-04)

Full autonomous proof run validating hybrid retrieval, candidate lab, and synthetic accelerator.

| Phase | Result | Detail |
|-------|--------|--------|
| P7A | PASS | Baseline benchmark (hybrid OFF), p50=9055ms |
| P7B | PASS | Hybrid ON before backfill, cells empty |
| P7C | PASS | Backfill: 4890/5000 indexed, 8/8 hex-cells |
| P7D | PASS | After backfill: p50=4231ms (-53%), LLM fallback 0% |
| P7E | PASS | Candidate lab: 2 compiled, 0 routed, AST 5/5 |
| P7F | PASS | Accelerator: 200->568 rows, perfect class balance |
| P7G | PASS | 30h soak: 750/750 cycles, 4500 req, 0 fail |
| P7H | PASS | Report finalized |

**Key metrics:**
- p50 latency: 9055ms -> 4231ms (-53%) after backfill
- LLM fallback: 75% -> 0%
- Local FAISS hits: 0% -> 75%
- Fix cycles: 1/3 used (backfill content extraction)
- Full report: `docs/PROOF_RUN_REPORT_v350.md`
