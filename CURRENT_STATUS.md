# Current Status — WaggleDance AI

**Updated:** 2026-05-01
**Version:** v3.6.0 + P10..P14 + P15 automatic runtime hints (alpha) on `main`
**Shipped branch:** `main` at `08b7e8c` (Phase 10 squash-merge of [PR #54](https://github.com/Ahkeratmehilaiset/waggledance-swarm/pull/54) on top of `8bf1869` post-v3.6.0 truthfulness commit on top of `a1c4152` PR #51 squash)
**Tag:** [`v3.6.0`](https://github.com/Ahkeratmehilaiset/waggledance-swarm/releases/tag/v3.6.0) — no new SemVer tag for Phase 10 (substrate, not runtime behaviour change). An optional `v3.6.1-substrate` prerelease tag may be added once post-merge truth/governance are clean.
**CI status:** 🟢 green on main (Tests + WaggleDance CI, Python 3.11 | 3.12 | 3.13)

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
