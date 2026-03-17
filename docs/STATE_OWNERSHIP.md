# State Ownership — WaggleDance Refactor Sprint

**Version:** 1.0.0
**Created:** 2026-03-14
**Authority:** WAGGLEDANCE_REFACTOR_MASTER_v2.3.md

This document locks down who writes what persistent state.
Every persistent write path has exactly ONE owner. No exceptions.

---

## Write Ownership Table

| State Type | Owner (Single Writer) | Write Method | Notes |
|---|---|---|---|
| Persistent memory (`MemoryRecord`) | `MemoryService` | `MemoryRepositoryPort.store()` | Only service that calls `store()`. All other components go through `MemoryService.ingest()`. |
| Vector embeddings | `MemoryService` | `VectorStorePort.upsert()` | Only service that calls `upsert()`. Embedding happens inside the adapter. |
| Trust scores | `Orchestrator` | `TrustStorePort.update_trust()` | Only component that updates trust. Reads allowed from anywhere. |
| Hot cache writes | `ChatService` | `HotCachePort.set()` | Only service that writes to hot cache. `BootstrapService.warm_cache_candidates()` returns tuples but does NOT write — `ChatService` does the actual `set()`. |
| Corrections | `MemoryService` | `MemoryRepositoryPort.store_correction()` | Via `MemoryService.record_correction()`. |
| Night learning writes | `LearningService` | Calls `MemoryService.ingest()` | Never writes to `MemoryRepositoryPort` directly. Always goes through `MemoryService`. |
| Scheduler pheromone state | `Orchestrator` | Holds `SchedulerState` | `Scheduler` is stateless — receives state as argument, returns new state. `Orchestrator` owns the mutable reference. |
| Agent lifecycle (promote/demote) | `AgentLifecycleManager` | Returns new `AgentDefinition` | Read-only for all other components. Only `AgentLifecycleManager` may change `trust_level` or `active` status. |
| Shared memory SQLite | `SQLiteSharedMemory` adapter | Direct writes only from task tracking | Agent status, tasks, events, messages. |

---

## Forbidden Write Paths

These write paths are EXPLICITLY FORBIDDEN. Any code that violates
these rules is a contract breach.

| Rule | Rationale |
|------|-----------|
| HTTP routes must NEVER write to any store directly | Routes are thin wrappers. All business logic lives in services. |
| `Scheduler` must NEVER hold mutable state | Receives `SchedulerState` as argument, returns new state. Pure function pattern. Prevents hidden coupling. |
| `RoundTableEngine` must NEVER write memory or trust | Returns `ConsensusResult` for caller (`Orchestrator`) to act on. Engine is a pure computation unit. |
| `EventBus` handlers must NOT write persistent state | Handlers may only log metrics and update in-memory counters. No `store()`, `upsert()`, or `update_trust()` calls from handlers. |
| No component may read environment variables except `WaggleSettings` | All config access goes through `ConfigPort`. Direct `os.environ` / `os.getenv` / `dotenv` reads forbidden outside `WaggleSettings`. |
| `BootstrapService` must NOT write to hot cache | Returns `(key, value, ttl)` tuples. `ChatService` is the sole cache writer. |
| `Orchestrator` must NOT write memory directly | Delegates to `MemoryService` for all memory operations. |

---

## Read Access (unrestricted)

Any component may READ from any port. Only WRITES are restricted.

| Reader | May Read From |
|--------|--------------|
| `ChatService` | `HotCachePort.get()`, `MemoryService.retrieve_context()` |
| `Orchestrator` | `TrustStorePort.get_trust()`, `MemoryRepositoryPort.search()` |
| `RoundTableEngine` | `LLMPort.generate()` (read-only generation) |
| `ReadinessService` | All ports' health/status methods |
| HTTP routes | Service return values only |

---

## State Lifecycle

```
User query → ChatService
    ├── HotCachePort.get()           [READ — ChatService]
    ├── MemoryService.retrieve()     [READ — MemoryService reads MemoryRepositoryPort]
    ├── Orchestrator.handle_task()
    │   ├── Scheduler.select_agents()  [READ SchedulerState — Orchestrator passes in]
    │   ├── LLMPort.generate()         [READ — no state change]
    │   ├── TrustStorePort.update()    [WRITE — Orchestrator only]
    │   └── returns AgentResult
    ├── HotCachePort.set()           [WRITE — ChatService only]
    └── returns ChatResult

Night tick → LearningService
    ├── LLMPort.generate()           [READ]
    ├── MemoryService.ingest()       [WRITE — MemoryService only]
    │   ├── MemoryRepositoryPort.store()
    │   └── VectorStorePort.upsert()
    └── EventBusPort.publish()       [WRITE — event only, no persistent state]
```

---

*Generated from existing codebase analysis. Traced to `core/memory_engine.py`, `core/chat_handler.py`, `core/swarm_scheduler.py`, `hivemind.py`.*
