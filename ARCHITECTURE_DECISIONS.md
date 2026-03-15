# Architecture Decisions — WaggleDance AI

**Updated:** 2026-03-14

Each decision includes: what was decided, why, and what it affects.

---

## AD-1: Hexagonal Architecture (Ports & Adapters)

**Decision:** Split monolith (`hivemind.py` + `core/*.py`) into `waggledance/core/` (pure domain), `waggledance/application/` (services), `waggledance/adapters/` (I/O).

**Rationale:** `hivemind.py` grew to 3700+ lines. Adapter swaps (e.g. ChromaDB to FAISS) required touching business logic. Unit tests needed live services.

**Impact:** Inner layers (`core/`, `application/`) have zero external deps. Adapters are swappable. All unit tests run without I/O (~0.5s for 172 tests).

---

## AD-2: typing.Protocol for All Ports

**Decision:** All 8 ports are `typing.Protocol` classes, not abstract base classes.

**Rationale:** Protocol enables structural subtyping — adapters don't inherit from ports, they just implement the same method signatures. No diamond inheritance. Works with duck typing.

**Impact:** Adapters are decoupled from core. No import of port into adapter is required (though it's allowed for type checking).

---

## AD-3: MemoryPort Split into MemoryRepositoryPort + HotCachePort

**Decision:** The old combined `MemoryPort` was split into `MemoryRepositoryPort` (store/search/corrections, async) and `HotCachePort` (get/set/evict, sync).

**Rationale:** Hot cache is in-process LRU (sync, ~1ms). Memory repository is persistent (async, ChromaDB/SQLite, ~50ms). Different ownership: `ChatService` owns cache writes, `MemoryService` owns memory writes.

**Impact:** `STATE_OWNERSHIP.md` enforces single-writer per store. Prevents race conditions between cache and persistence layers.

---

## AD-4: Stateless Scheduler

**Decision:** `Scheduler` receives `SchedulerState` as argument and returns new state. It holds no mutable fields between calls.

**Rationale:** Previous `SwarmScheduler` had hidden mutable state (pheromone scores, usage counts) that made testing and reasoning difficult. Functional-style state passing makes ownership explicit.

**Impact:** `Orchestrator` owns the `SchedulerState`. Scheduler is a pure computation — trivially testable, no concurrency issues.

---

## AD-5: Single-Writer State Ownership

**Decision:** Every persistent write path has exactly one owner component, documented in `STATE_OWNERSHIP.md`.

**Rationale:** The monolith had multiple components writing to the same stores concurrently (memory, trust, cache) with no coordination. This caused subtle race conditions.

**Impact:** 9 ownership rules + 7 forbidden write paths. HTTP routes, Scheduler, RoundTableEngine, and EventBus handlers may never write persistent state.

---

## AD-6: ALLOWED_ROUTE_TYPES Whitelist

**Decision:** `ALLOWED_ROUTE_TYPES = frozenset({"hotcache", "memory", "llm", "swarm"})`. `micromodel` and `rules` excluded.

**Rationale:** `micromodel` route had a P0 production bug (`hivemind.py:1365` type mixing caused 817 errors/night). `rules` route was never fully implemented. Excluding both prevents regression.

**Impact:** `select_route()` can only return these 4 types. Contract test enforces this. MicroModel and rules are future work after the type mismatch bug is fixed.

---

## AD-7: _track_task() for All Background Tasks (BUG 2 Fix)

**Decision:** Every `asyncio.create_task()` must go through `Orchestrator._track_task()` which adds done-callbacks for error logging and cleanup.

**Rationale:** Production saw 27 silent "Task exception never retrieved" failures per night because `asyncio.create_task()` was called without error handling.

**Impact:** Only one `asyncio.create_task()` in entire `waggledance/` — inside `_track_task()`. 4 regression tests verify the pattern. Fire-and-forget is forbidden.

---

## AD-8: Convergence Stall Detection (BUG 3 Fix)

**Decision:** `LearningService` tracks `_consecutive_empty_cycles`. After N empty cycles (default 10), it resets topic selection and emits `NIGHT_STALL_DETECTED` event.

**Rationale:** Production night learning generated 3979 facts then froze for 6 hours — cycling the same topics but producing nothing. No auto-recovery existed.

**Impact:** Stall threshold configurable via `WaggleSettings.night_stall_threshold`. 6 regression tests. Event bus notifies monitoring.

---

## AD-9: DI Container with Stub/Non-Stub Modes

**Decision:** `Container(settings, stub=True/False)` provides all dependencies. Stub mode uses in-memory adapters. Non-stub uses real adapters (Ollama, ChromaDB).

**Rationale:** Tests and local development need fast, no-dependency startup. Production needs real backends. A single toggle controls the entire wiring.

**Impact:** Non-stub mode may never use `InMemoryRepository` or `StubLLMAdapter` (acceptance criterion #23-24). Stub mode starts in <1s without Ollama/ChromaDB.

---

## AD-10: Event Bus Failure Policy

**Decision:** Event handler failures are logged with `exc_info=True`, counted per event type via `get_failure_count()`, never swallowed. Max handler timeout: 5s.

**Rationale:** Old code used `logging.warning(f"...")` which lost stack traces. Failures were invisible. No way to monitor handler health.

**Impact:** `InMemoryEventBus` tracks `_failure_counts` dict. Async handlers wrapped with `asyncio.wait_for(timeout=5.0)`. Both sync and async handlers supported.

---

## AD-11: WaggleSettings as Sole Env Reader

**Decision:** Only `WaggleSettings.from_env()` in `settings_loader.py` may read `os.environ`. All other config via `ConfigPort.get()`.

**Rationale:** Scattered `os.environ` reads across 15+ files made configuration unpredictable. Missing env vars caused silent defaults in some places, crashes in others.

**Impact:** `grep 'os.environ' waggledance/ | grep -v settings_loader` must return 0 matches (acceptance criterion #11).

---

## AD-12: HTTP Routes as Thin Wrappers

**Decision:** HTTP routes contain zero business logic. They convert Pydantic models to DTOs, call a service, and convert the result back.

**Rationale:** Business logic in routes is untestable without HTTP server. Routes that write to stores directly violate `STATE_OWNERSHIP.md`.

**Impact:** `chat.py` is 100 lines: Pydantic model, `chat_service.handle(dto)`, response model. All logic in `ChatService`.

---

## AD-13: Ollama Timeout 120s (BUG 3 Fix)

**Decision:** `OllamaAdapter` and `WaggleSettings` default to 120s timeout instead of the previous 30s.

**Rationale:** Production saw `Read timed out` errors when embedding calls competed with night learning generate calls on a single GPU (RTX A2000 8GB).

**Impact:** `WaggleSettings.ollama_timeout_seconds = 120.0`. Acceptance criterion #18 verifies this.

---

## AD-14: Contracts-First Development

**Decision:** `PORT_CONTRACTS.md`, `STATE_OWNERSHIP.md`, and `ACCEPTANCE_CRITERIA.md` were written before any code (Phase 0). Both implementation agents (Agent 1 + Agent 2) worked from these locked contracts.

**Rationale:** Two agents developing in parallel need a shared interface agreement. Without locked contracts, port signatures drift and integration fails.

**Impact:** 0 contract mismatches at integration. `tests/contracts/test_port_contracts.py` enforces signatures, DTO fields, route types, and event types at test time.

---

## AD-15: Legacy Stack Preserved (Non-Destructive Migration)

**Decision:** Old files (`hivemind.py`, `core/*.py`, `start.py`, `main.py`) remain unchanged. The new `waggledance/` package is additive.

**Rationale:** The old stack runs production. Deleting it before the new stack is validated risks downtime. Both entrypoints must work simultaneously.

**Impact:** Superseded files listed in `MIGRATION_STATUS.md`. Deletion blocked until 24h production validation of new stack.

---

## AD-16: FallbackChain Without MicroModel

**Decision:** Fallback order is `memory -> llm -> swarm`. No `micromodel` step.

**Rationale:** The micromodel route is broken in production (see AD-6). Including a broken step in the fallback chain would cause cascading errors.

**Impact:** `FallbackChain.steps = ["memory", "llm", "swarm"]`. When micromodel is fixed, it can be inserted as `["memory", "micromodel", "llm", "swarm"]`.

---

## AD-17: RoundTableEngine Returns, Never Writes

**Decision:** `RoundTableEngine.deliberate()` returns `ConsensusResult`. It never writes to memory, trust, or any store.

**Rationale:** The engine is a pure computation over agent responses. Letting it write would violate single-writer ownership and make testing require mocked stores.

**Impact:** `Orchestrator` receives the result and decides what to write. Engine can be tested with zero mocks except `LLMPort`.

---

## AD-18: asyncio.run() Test Pattern (No pytest-asyncio)

**Decision:** Async tests use `asyncio.run()` wrapping instead of `pytest-asyncio` plugin.

**Rationale:** `pytest-asyncio` was not installed in the project. Adding a dependency for test infrastructure was considered unnecessary when `asyncio.run()` works reliably.

**Impact:** All async tests follow the pattern: `def test_X(self): asyncio.run(async_inner())`. No `@pytest.mark.asyncio` decorators needed.
