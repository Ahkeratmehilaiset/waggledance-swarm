# NEW_ARCHITECTURE.md — WaggleDance Hexagonal + Autonomy Architecture

## Overview

WaggleDance has been migrated from a monolithic architecture (`hivemind.py` + `core/*.py`) to a hexagonal (ports & adapters) architecture under the `waggledance/` package, with a full autonomy runtime added in v2.0.

**Hexagonal refactor** built the inner hexagon: domain models, ports, orchestration, policies, and application services.
**Full Autonomy v3** added the solver-first runtime: goals, planning, policy, actions, world model, capabilities, night learning v2, resource kernel, and lifecycle management.

## Package Structure

```
waggledance/
  __init__.py                         # __version__ = "1.15.0"
  core/                               # PURE DOMAIN — zero external deps
    domain/
      agent.py                        # AgentDefinition, AgentResult
      task.py                         # TaskRequest, TaskRoute
      memory_record.py                # MemoryRecord
      trust_score.py                  # TrustSignals, AgentTrust
      events.py                       # EventType (16 values), DomainEvent
    ports/
      llm_port.py                     # LLMPort(Protocol)
      memory_repository_port.py       # MemoryRepositoryPort(Protocol)
      hot_cache_port.py               # HotCachePort(Protocol)
      vector_store_port.py            # VectorStorePort(Protocol)
      trust_store_port.py             # TrustStorePort(Protocol)
      event_bus_port.py               # EventBusPort(Protocol)
      config_port.py                  # ConfigPort(Protocol)
      sensor_port.py                  # SensorPort(Protocol)
    orchestration/
      scheduler.py                    # Scheduler (Top-K, pheromone EMA, exploration)
      round_table.py                  # RoundTableEngine (sequential discussion + synthesis)
      routing_policy.py               # select_route() pure function, ALLOWED_ROUTE_TYPES
      orchestrator.py                 # Orchestrator (task execution, trust updates, _track_task)
      lifecycle.py                    # AgentLifecycleManager (spawn, promote)
    policies/
      confidence_policy.py            # should_escalate_to_swarm(), should_cache_result()
      fallback_policy.py              # FallbackChain (memory -> llm -> swarm)
      escalation_policy.py            # EscalationPolicy (needs_round_table())
  application/                        # SERVICES — orchestrate core, no I/O
    dto/
      chat_dto.py                     # ChatRequest, ChatResult
      readiness_dto.py                # ComponentStatus, ReadinessStatus
    services/
      chat_service.py                 # ChatService (owns hot cache per STATE_OWNERSHIP)
      memory_service.py               # MemoryService (sole writer of persistent memory)
      learning_service.py             # LearningService (BUG 3 stall detection)
      readiness_service.py            # ReadinessService (component health checks)
      bootstrap_service.py            # BootstrapService (YAML agent loading)
    use_cases/
      handle_chat.py                  # Thin wrapper -> ChatService.handle()
      store_memory.py                 # Thin wrapper -> MemoryService.ingest()
      run_night_mode_tick.py          # Thin wrapper -> LearningService.run_night_tick()
      spawn_profile_agents.py         # Thin wrapper -> BootstrapService.load_agents()
  adapters/                           # AGENT 2 — I/O implementations
    llm/                              # OllamaAdapter, StubLLMAdapter
    memory/                           # InMemoryRepository, ChromaDB, HotCache, SQLite
    trust/                            # InMemoryTrustStore
    config/                           # SettingsLoader
    http/                             # FastAPI routes, middleware
  bootstrap/                          # AGENT 2 — DI container, event bus wiring
    container.py                      # Wire adapters -> ports -> services
    event_bus.py                      # InProcessEventBus
```

## Port Interfaces (8 ports)

| Port | Methods | Constraint |
|------|---------|-----------|
| LLMPort | `generate()`, `is_available()`, `get_active_model()` | Async |
| MemoryRepositoryPort | `search()`, `store()`, `store_correction()` | Async, search has language/tags params |
| HotCachePort | `get()`, `set()`, `evict_expired()`, `stats()` | Sync (in-process LRU) |
| VectorStorePort | `upsert()`, `query()`, `is_ready()` | Async |
| TrustStorePort | `get_trust()`, `update_trust()`, `get_ranking()` | Async |
| EventBusPort | `publish()`, `subscribe()` | Async publish, sync subscribe |
| ConfigPort | `get()`, `get_profile()`, `get_hardware_tier()` | Sync |
| SensorPort | `get_latest()`, `get_all_active()` | Async |

## Route Types

```python
ALLOWED_ROUTE_TYPES = frozenset({"hotcache", "memory", "llm", "swarm"})
```

Excluded: `micromodel` (P0 production bug), `rules` (not in current architecture).

## Migration Map (old -> new)

| Old File | New Location |
|----------|-------------|
| `core/swarm_scheduler.py` | `waggledance/core/orchestration/scheduler.py` |
| `core/round_table_controller.py` | `waggledance/core/orchestration/round_table.py` |
| `core/chat_handler.py` (routing) | `waggledance/core/orchestration/routing_policy.py` |
| `hivemind.py` (task dispatch) | `waggledance/core/orchestration/orchestrator.py` |
| `core/chat_handler.py` (handle) | `waggledance/application/services/chat_service.py` |
| `core/night_enricher.py` | `waggledance/application/services/learning_service.py` |
| `core/memory_engine.py` | `waggledance/application/services/memory_service.py` |
| `hivemind.py` (_readiness) | `waggledance/application/services/readiness_service.py` |

## Production Bugs Addressed

| Bug | Description | Fix |
|-----|-------------|-----|
| BUG 1 | `is_training_due` mixed `bool/datetime` in typed attribute | All domain DTOs use explicit types, no mixed attributes |
| BUG 2 | `asyncio.create_task()` without error callbacks | All tasks via `Orchestrator._track_task()` with `.add_done_callback()` |
| BUG 3 | Night learning stall (3979 facts stuck for 6h) | `LearningService._consecutive_empty_cycles` counter + `NIGHT_STALL_DETECTED` event |

## Full Autonomy Runtime (v2.0)

The autonomy runtime adds solver-first, capability-driven decision-making:

```
waggledance/core/
  autonomy/
    runtime.py              # AutonomyRuntime (main orchestrator)
    lifecycle.py            # State machine (RUNNING/DEGRADED/STOPPED)
    resource_kernel.py      # Load management + admission control
    compatibility.py        # Legacy/autonomy bridge
  goals/
    goal_engine.py          # Goal lifecycle management
    mission_store.py        # Goal persistence
  planning/
    planner.py              # Capability chain builder
  policy/
    policy_engine.py        # Deny-by-default evaluation
    risk_scoring.py         # Risk assessment
    constitution.py         # Immutable safety rules
    approvals.py            # Approval workflow
  actions/
    action_bus.py           # Safe execution with rollback
  reasoning/
    solver_router.py        # 3-tier solver-first routing
    verifier.py             # Outcome validation
  capabilities/
    registry.py             # Capability registry
    selector.py             # Capability selection
  world/
    world_model.py          # Unified situation picture
    entity_registry.py      # Named entities
    baseline_store.py       # Normal baselines for anomaly detection
  memory/
    working_memory.py       # Short-term query context
  learning/
    case_builder.py         # Case trajectory builder
    quality_gate.py         # Grading (gold/silver/bronze/quarantine)
    procedural_memory.py    # Proven action chains
    night_learning_pipeline.py  # Night learning orchestrator
    morning_report.py       # End-of-night summary
    legacy_converter.py     # Q&A → case trajectory bridge
  specialist_models/
    specialist_trainer.py   # Model training + canary lifecycle
    model_store.py          # Model persistence
  domain/
    autonomy.py             # DTOs, enums, dataclasses
    alias_registry.py       # Canonical ID + alias resolution
```

See `docs/AUTONOMY_RUNTIME.md` for full details.

## What's NOT Done Yet

- **Old code removal**: `hivemind.py`, `core/chat_handler.py`, etc. remain (wrapped, not rewritten)
- **Dashboard autonomy views**: No autonomy-specific dashboard components yet
- **24h production validation**: Autonomy runtime tested but not yet run in production
