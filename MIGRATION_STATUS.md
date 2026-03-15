# Migration Status — WaggleDance Hexagonal Refactor

**Updated:** 2026-03-15
**Version:** v1.18.0 (in progress)

---

## Overall Status: INTEGRATION COMPLETE

All 4 phases of WAGGLEDANCE_REFACTOR_MASTER_v2.3.md are finished:

| Phase | Status | Agent |
|-------|--------|-------|
| Phase 0 — Contract Freeze | COMPLETE | Opus 4 |
| Agent 1 — Core + Application | COMPLETE | Opus 4 |
| Agent 2 — Adapters + Bootstrap + Tests | COMPLETE | Sonnet |
| Integration Phase | COMPLETE | Opus 4 |

---

## Test Results (as of v1.17.0)

| Suite | Tests | Status |
|-------|-------|--------|
| `tests/unit/` (adapters + trust + container) | 300+ | PASS |
| `tests/unit_core/` (core modules + Big Sprint) | 130+ | PASS |
| `tests/unit_app/` (service tests) | 16 | PASS |
| `tests/contracts/` (contract validation) | 22 | PASS |
| **New architecture subtotal** | **~469** | **ALL PASS** |
| `tests/integration/` (runtime, smoke, scenarios) | 90 | PASS |
| `tools/waggle_backup.py --tests-only` | 2427 (72 suites) | PASS |
| **Grand total** | **~2986** | **ALL PASS** |

---

## What Was Created

### Agent 1: Core + Application (`waggledance/core/`, `waggledance/application/`)

| Layer | Directory | Files | Source |
|-------|-----------|-------|--------|
| Domain models | `core/domain/` | 5 dataclass modules | hivemind.py, core/swarm_scheduler.py |
| Ports | `core/ports/` | 8 Protocol interfaces | New (contracts-first) |
| Orchestration | `core/orchestration/` | 5 modules | hivemind.py, core/swarm_scheduler.py, core/round_table_controller.py |
| Policies | `core/policies/` | 3 modules | core/chat_handler.py |
| DTOs | `application/dto/` | 2 modules | backend/routes/chat.py |
| Services | `application/services/` | 5 modules | core/chat_handler.py, core/night_enricher.py, core/memory_engine.py |
| Use cases | `application/use_cases/` | 4 thin wrappers | New |

### Agent 2: Adapters + Bootstrap (`waggledance/adapters/`, `waggledance/bootstrap/`)

| New File | Port Implemented | Source Ported From |
|----------|-----------------|-------------------|
| llm/ollama_adapter.py | LLMPort | core/llm_provider.py |
| llm/stub_llm_adapter.py | LLMPort | (new) |
| memory/chroma_vector_store.py | VectorStorePort | core/memory_engine.py (ChromaDB ops) |
| memory/in_memory_vector_store.py | VectorStorePort | (new, stub) |
| memory/in_memory_repository.py | MemoryRepositoryPort | (new, stub) |
| memory/chroma_memory_repository.py | MemoryRepositoryPort | core/memory_engine.py (store/search) |
| memory/hot_cache.py | HotCachePort | core/memory_engine.py (HotCache) |
| memory/sqlite_shared_memory.py | (standalone) | memory/shared_memory.py |
| trust/in_memory_trust_store.py | TrustStorePort | (new) |
| config/settings_loader.py | ConfigPort | configs/settings.yaml + env vars |
| http/api.py | (FastAPI factory) | web/dashboard.py |
| http/middleware/auth.py | (middleware) | backend/auth.py |
| http/middleware/rate_limit.py | (middleware) | backend/routes/chat.py |
| http/routes/chat.py | (route) | backend/routes/chat.py |
| http/routes/status.py | (route) | web/dashboard.py |
| http/routes/memory.py | (route) | (new) |
| bootstrap/container.py | DI container | New |
| bootstrap/event_bus.py | InMemoryEventBus | New |

### Integration Phase

| File | Purpose |
|------|---------|
| `adapters/cli/start_runtime.py` | Clean entry point (replaces start.py) |
| `pyproject.toml` [project.scripts] | `waggledance` command |

---

## Existing Files Now Superseded

These files are functionally replaced but remain intact for backward compatibility.
**Do NOT delete until full production validation (24h+).**

| Old File | Superseded By |
|----------|--------------|
| core/swarm_scheduler.py | waggledance/core/orchestration/scheduler.py |
| core/round_table_controller.py | waggledance/core/orchestration/round_table.py |
| core/chat_handler.py | waggledance/application/services/chat_service.py |
| core/night_enricher.py | waggledance/application/services/learning_service.py |
| core/memory_engine.py | waggledance/application/services/memory_service.py + adapters/memory/ |
| core/llm_provider.py | waggledance/adapters/llm/ollama_adapter.py |
| backend/routes/chat.py | waggledance/adapters/http/routes/chat.py |
| backend/auth.py | waggledance/adapters/http/middleware/auth.py |
| memory/shared_memory.py | waggledance/adapters/memory/sqlite_shared_memory.py |

---

## Entrypoints

| Entrypoint | Mode | Status |
|------------|------|--------|
| `start.py` (old) | Interactive menu | Works (unchanged) |
| `main.py` (old) | Direct run | Works (unchanged) |
| `python -m waggledance.adapters.cli.start_runtime` (new) | `--stub` or production | Works |
| `pyproject.toml` [project.scripts] `waggledance` | CLI command | Configured |

---

## Production Bug Fix Status

| Bug | Fixed | Tests |
|-----|-------|-------|
| BUG 1 (P0): Type mixing in `self.micro_model` | YES | `test_port_contracts.py` |
| BUG 2 (P1): Untracked `asyncio.create_task()` | YES | `test_orchestrator_task_tracking.py` (4 tests) |
| BUG 3 (P2): 30s Ollama timeout | YES — 120s default | Container smoke test |
| BUG 3b (P2): Night learning stall | YES | `test_learning_service.py` (6 tests) |

---

## Port Contract Mismatches

**NONE.** All implementations match PORT_CONTRACTS.md exactly.

---

## v1.17.0 Big Sprint Additions

15 new core modules and 25 new test files were added:

| Module | Purpose | Status |
|--------|---------|--------|
| `core/circuit_breaker.py` | Extracted from memory_engine.py | Stable |
| `core/embedding_cache.py` | Extracted from memory_engine.py | Stable |
| `core/hallucination_checker.py` | Extracted from memory_engine.py | Stable |
| `core/math_solver.py` | Extracted from memory_engine.py | Stable |
| `core/active_learning.py` | ActiveLearningScorer for prioritization | Exists, not wired to runtime |
| `core/canary_promoter.py` | CanaryPromoter for safe prompt/model promotion | Exists, not wired to runtime |
| `core/learning_ledger.py` | JSONL append-only audit log | Exists, not wired to runtime |
| `core/route_telemetry.py` | Per-route stats (count, latency, fallback) | Exists, not wired to runtime |
| `core/route_explainability.py` | Route decision breakdown | Exists, not wired to runtime |
| `core/causal_replay_api.py` | Causal chain query API | Exists, not wired to runtime |
| `core/mqtt_sensor_ingest.py` | MQTT payload → SharedMemory | Exists, not wired to SensorHub |
| `core/english_source_learner.py` | Night learning from English sources | Exists, not wired to NightEnricher |
| `core/language_readiness.py` | Per-language capability report | Exists, standalone |
| `core/lora_readiness.py` | LoRA V3 readiness checker | Exists, standalone |
| `waggledance/adapters/trust/sqlite_trust_store.py` | Persistent TrustStore | **WIRED** in container.py |

## v1.18.0 Runtime Convergence Decision

**Legacy runtime** (`start.py` → `hivemind.py`) remains the **primary production path**.
**Hexagonal runtime** (`start_runtime.py` → Container → ChatService) is the **forward path** for new deployments.

Strategy: shared helper layer for duplicated logic, no third runtime, gradual migration.

## What's Next (v1.18.0 Sprint)

1. **Wire Sprint 17 modules into runtime loops** — telemetry, ledger, explainability in request path; ActiveLearningScorer in night path
2. **MQTT bridge via SensorHub** — hive/+/temperature end-to-end
3. **Explainability + causal replay APIs** — dashboard endpoints
4. **memory_engine.py split** — bilingual_store.py extraction
5. **Operational hardening** — backup, graceful degradation, EventBus wiring
6. **Shadow validation** — 50-100 query corpus
7. **Benchmark evidence** — release artifacts
8. **V1/V2/V3 micromodel clarity** — honest status in docs and code
9. **Production validation** — Run new stack 24h alongside old (blocked on convergence)
10. **Old code cleanup** — Remove superseded files after validation
