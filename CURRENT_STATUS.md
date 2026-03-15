# Current Status — WaggleDance AI

**Updated:** 2026-03-15
**Version:** v1.17.0

---

## Dual-Stack Architecture

WaggleDance currently runs two parallel stacks:

| Stack | Entrypoint | Status | Notes |
|-------|-----------|--------|-------|
| **Legacy** | `start.py` / `main.py` | Production, running | `hivemind.py` + `core/*.py` monolith |
| **New (hexagonal)** | `waggledance.adapters.cli.start_runtime` | Integrated, not yet production | `waggledance/` package, ports & adapters |

The legacy stack serves all production traffic. The new stack is wired, tested, and bootable but has not yet run a 24h production validation.

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
| 8 port contracts | `PORT_CONTRACTS.md`, locked, 0 mismatches |
| State ownership rules | `STATE_OWNERSHIP.md`, 9 owners, 7 forbidden paths |
| Domain models (5 modules) | `tests/contracts/` — 22 tests |
| Orchestration (scheduler, routing, round table) | `tests/unit_core/` — 37 tests |
| Application services (chat, memory, learning) | `tests/unit_app/` — 16 tests |
| Adapter implementations (9 adapters) | `tests/unit/` — 97 tests |
| DI container (stub + production) | Smoke tests pass both modes |
| Legacy test suite | 72 suites, 946 tests, Health 100/100 |
| Production bug fixes (BUG 1-3) | Regression tests in place |
| Acceptance criteria | 27/27 GREEN |

---

## What Is Experimental / Future Work

| Item | Status | Blocker |
|------|--------|---------|
| MicroModel port + adapter | Not started | Fix `hivemind.py:1365` type mismatch first |
| Rule engine port + adapter | Not designed | No current requirement |
| Sensor/MQTT adapter | Stub only (`SensorPort`) | Legacy `integrations/` works |
| Persistent TrustStore (SQLite) | Not started | `InMemoryTrustStore` loses scores on restart |
| Voice adapters (Whisper/Piper) | Legacy only | Not ported to new architecture |
| Dashboard wiring | Static mount exists | React build separate process |
| Old code removal | Blocked | Needs 24h production validation first |
| `micromodel` + `rules` route types | Excluded | `ALLOWED_ROUTE_TYPES = {hotcache, memory, llm, swarm}` |

---

## Gate Tests

These test suites are the gatekeepers — all must pass before any change is merged.

| Gate | Command | Tests | What It Guards |
|------|---------|-------|---------------|
| Contract tests | `pytest tests/contracts/ -v` | 22 | Port signatures, DTOs, route types, event types |
| Core unit tests | `pytest tests/unit_core/ -v` | 37 | Scheduler, routing, fallback, escalation, task tracking |
| App unit tests | `pytest tests/unit_app/ -v` | 16 | ChatService, LearningService (BUG 3 regression) |
| Adapter unit tests | `pytest tests/unit/ -v` | 97 | All 9 adapters, container, event bus |
| Legacy suite | `python tools/waggle_backup.py --tests-only` | 946 | Old stack regression (72 suites) |
| Stub smoke | `Container(stub=True).build_app()` | 1 | DI wiring, no crash |
| Non-stub smoke | `Container(stub=False).memory_repository` | 1 | ChromaMemoryRepository, not InMemory |
| Compile check | `python -m compileall waggledance/ -q` | - | No syntax errors |

---

## Runtime Rules

1. **No silent fallbacks** — Non-stub mode must fail fast if a real adapter is unavailable; never silently fall back to in-memory stub.
2. **Single-writer ownership** — Every persistent state type has exactly one writing component (see `STATE_OWNERSHIP.md`).
3. **No fire-and-forget tasks** — Every `asyncio.create_task()` must use `_track_task()` or `TaskGroup` with error callbacks.
4. **No type mixing** — Never assign incompatible types to a typed attribute (prevents BUG 1).
5. **Event bus failure policy** — Log with `exc_info=True`, increment failure counter, never swallow, max 5s handler timeout.
6. **Env reads centralized** — Only `WaggleSettings` (in `settings_loader.py`) may read `os.environ`. All other config via `ConfigPort`.
7. **Route type whitelist** — `select_route()` may only return `{hotcache, memory, llm, swarm}`.
8. **HTTP routes are thin** — Routes delegate to services; no business logic, no direct store writes.
9. **Ollama timeout >= 120s** — Prevents embed timeouts under load (BUG 3 fix).
10. **Stall detection active** — `LearningService` resets after N consecutive empty cycles (`night_stall_threshold`, default 10).
