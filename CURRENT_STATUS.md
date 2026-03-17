# Current Status — WaggleDance AI

**Updated:** 2026-03-17
**Version:** v2.0.0 (merged to master)

---

## Tri-Stack Architecture

WaggleDance now has three stacks:

| Stack | Entrypoint | Status | Notes |
|-------|-----------|--------|-------|
| **Legacy** | `start.py` / `main.py` | Production, running | `hivemind.py` + `core/*.py` monolith |
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
| Legacy test suite | 79 suites, 1470 tests, 0 failures, Health 100/100 |
| Big Sprint modules (v1.17.0) | 15 new core modules, 25 new test files |
| Production bug fixes (BUG 1-3) | Regression tests in place |
| **Autonomy runtime (v2.0)** | **504 tests (9 phases + 5 regression gates), all pass** |
| **Cutover validation** | **"FULL AUTONOMY MODE ENABLED"** |
| **Regression gates** | **migration, night_learning_v2, resource_kernel, specialist_models — 41 tests** |
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
| Autonomy unit tests | `pytest tests/autonomy/ -v` | 463 | Domain models, phases 1-9 |
| Regression gates | `pytest tests/migration/ tests/night_learning_v2/ tests/resource_kernel/ tests/specialist_models/ -v` | 41 | Alias migration, night pipeline, resource kernel, specialist models |
| Legacy suite | `python tools/waggle_backup.py --tests-only` | 1470 | Old stack regression (79 suites) |
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
