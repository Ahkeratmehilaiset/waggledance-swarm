# Current Status — WaggleDance AI

**Updated:** 2026-04-27
**Version:** v3.6.0 (Phase 9 Autonomy Fabric scaffold, 2026-04-27)
**Shipped branch:** `main` at `a1c4152` (squash-merge of PR #51)
**Tag:** [`v3.6.0`](https://github.com/Ahkeratmehilaiset/waggledance-swarm/releases/tag/v3.6.0)
**CI status:** 🟢 green on main (Tests + WaggleDance CI, Python 3.11 | 3.12 | 3.13)

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
