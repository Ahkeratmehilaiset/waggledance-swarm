# WaggleDance Refactor — Master Sprint Plan v2.3

## AUTHORITATIVE SOURCE NOTICE

**This file is the single source of truth for the refactor sprint.**

It supersedes and invalidates:
- `AGENT1_CORE_APPLICATION.md`
- `AGENT2_ADAPTERS_BOOTSTRAP_TESTS.md`
- `INTEGRATION_PHASE.md`

Those files are **OBSOLETE** and must not be used for implementation.
Move them to `docs/archive/obsolete_prompts/` or rename with
`_OBSOLETE_DO_NOT_USE.md` before starting the sprint.

**Only this master plan may be used.** If any old prompt conflicts
with this file, this file wins.

---

## BRANCHING AND ROLLBACK RULES

- Create a safety tag before sprint start: `git tag pre_refactor_sprint_v2.3`
- Phase 0 commits to branch `refactor/phase0-contracts`
- Agent 1 commits to branch `refactor/core-app`
- Agent 2 commits to branch `refactor/adapters-bootstrap`
- Integration Phase merges all into `refactor/integration`
- If contract tests fail after merge, revert integration branch — not agent branches
- Final merge to `main` only after all `ACCEPTANCE_CRITERIA.md` items are green

---

## NO SILENT FALLBACKS RULE (applies to ALL phases)

In non-stub mode, **no adapter may silently fall back** to an
in-memory, stub, or reduced-capability implementation.

If a required real adapter is unavailable, startup must **fail fast**
or readiness must return `ready=False` with a precise error message.

This means:
- `InMemoryRepository` in non-stub `Container` → **forbidden**
- `StubLLMAdapter` in non-stub `Container` → **forbidden**
- `InMemoryVectorStore` in non-stub `Container` → **forbidden**
- Missing `dashboard/dist` → **allowed** (graceful degradation, not silent fallback)

---

## EVENT BUS FAILURE POLICY (applies to ALL phases)

`EventBus` handler failures must:
- Be logged with full traceback (not just the message)
- Increment an in-memory `failure_count` counter per event type
- Never be silently swallowed (the old `logging.warning(f"...")` pattern is insufficient — use `logger.error(..., exc_info=True)`)
- Never block the main request path (max handler timeout: 5s)
- Be testable: unit tests must verify that a failing handler produces an observable failure count

---

> **Tämä on yksi tiedosto joka sisältää kaikki neljä vaihetta.**
> Leikkaa oikea osio irti kun ajat sen Claude Codessa.
>
> **Ajojärjestys:**
> 1. Phase 0 — Contract Freeze (Opus 4, kerran)
> 2. Agent 1 — Core + Application (Opus 4, rinnakkain Agent 2:n kanssa)
> 3. Agent 2 — Adapters + Bootstrap + Tests (Sonnet, rinnakkain Agent 1:n kanssa)
> 4. Integration Phase (Opus 4, vasta kun 1 ja 2 valmiit)

---
---

# ═══════════════════════════════════════════════════════════════
# PHASE 0 — CONTRACT FREEZE
# ═══════════════════════════════════════════════════════════════

> **Aja tämä ENSIN, ennen Agent 1:tä ja Agent 2:ta.**
> **Aja tämä: Claude Code (Opus 4)**
> Tuottaa kolme sopimustiedostoa, joihin molemmat agentit sitoutuvat.

---

You are preparing a large refactor of the WaggleDance AI codebase.
Before any implementation begins, you must create three contract
documents that lock down every shared interface. Do not ask questions.
Read the existing code, make decisions, and document them.

---

## STEP 1 — Read the existing codebase

Read these files to understand the current architecture:

```
core/swarm_scheduler.py        — agent scoring, pheromone, Top-K
core/chat_handler.py           — chat routing, keyword logic (first 200 lines)
core/memory_engine.py          — memory, vector, corrections (first 300 lines)
core/llm_provider.py           — LLM abstraction, circuit breaker
core/round_table_controller.py — consensus logic
hivemind.py                    — main orchestration (first 400 lines)
backend/routes/chat.py         — HTTP routes, rate limiting
backend/auth.py                — authentication middleware
memory/shared_memory.py        — shared memory SQLite
configs/settings.yaml          — configuration structure
```

Also read the overnight production reports to understand known bugs:
```
data/overnight_report_20260314.md
docs/SESSION_SUMMARY_20260314.md
```

---

## STEP 2 — Create `PORT_CONTRACTS.md`

Create `PORT_CONTRACTS.md` at repo root. This is the single source
of truth for all shared interfaces in the new architecture.

### Required sections:

#### 2.1 — All Ports (name, methods, signatures)

Define exactly these ports. No more, no fewer:

| Port | Methods |
|------|---------|
| `LLMPort` | `generate(prompt, model, temperature, max_tokens) → str`, `is_available() → bool`, `get_active_model() → str` |
| `MemoryRepositoryPort` | `search(query, limit, language, tags) → list[MemoryRecord]`, `store(record) → None`, `store_correction(query, wrong, correct) → None` |
| `VectorStorePort` | `upsert(id, text, metadata, collection) → None`, `query(text, n_results, collection, where) → list[dict]`, `is_ready() → bool` |
| `HotCachePort` | `get(key) → str | None`, `set(key, value, ttl) → None`, `evict_expired() → int`, `stats() → dict` |
| `TrustStorePort` | `get_trust(agent_id) → AgentTrust | None`, `update_trust(agent_id, signals) → AgentTrust`, `get_ranking(limit) → list[AgentTrust]` |
| `EventBusPort` | `publish(event) → None`, `subscribe(event_type, handler) → None` |
| `ConfigPort` | `get(key, default) → Any`, `get_profile() → str`, `get_hardware_tier() → str` |
| `SensorPort` | `get_latest(sensor_id) → dict | None`, `get_all_active() → list[dict]` |

**Critical rules:**
- `MemoryPort` from v1 prompt is SPLIT into `MemoryRepositoryPort` (store/search/corrections) and `HotCachePort` (get/set/evict/stats). The old combined `MemoryPort` does not exist.
- `TrustStorePort` MUST have at least one implementation: `InMemoryTrustStore`.
- No port named `RuleEnginePort` or `MicroModelPort` in this sprint.

#### 2.2 — All DTOs (name, fields, types)

```
AgentDefinition: id, name, domain, tags, skills, trust_level, specialization_score, active, profile
AgentResult: agent_id, response, confidence, latency_ms, source, metadata
TaskRequest: id, query, language, profile, user_id, context, timestamp
TaskRoute: route_type, selected_agents, confidence, routing_latency_ms
MemoryRecord: id, content, content_fi, source, confidence, tags, agent_id, created_at, ttl_seconds
TrustSignals: hallucination_rate, validation_rate, consensus_agreement, correction_rate, fact_production_rate, freshness_score
AgentTrust: agent_id, composite_score, signals, updated_at
DomainEvent: type, payload, timestamp, source
SchedulerState: pheromone_scores, usage_counts, recent_successes
RoutingFeatures: query_length, language, has_hot_cache_hit, memory_score, is_time_query, is_system_query, matched_keywords, profile
ConsensusResult: consensus, confidence, participating_agents, dissenting_views, latency_ms
ChatRequest: query, language, profile, user_id, session_id, context_turns
ChatResult: response, language, source, confidence, latency_ms, agent_id, round_table, cached
ComponentStatus: name, ready, message, latency_ms
ReadinessStatus: ready, components, uptime_seconds
```

#### 2.3 — Route types (allowed values in this sprint)

```python
ALLOWED_ROUTE_TYPES = {"hotcache", "memory", "llm", "swarm"}
```

`micromodel` and `rules` are **NOT** implemented in this sprint.
They may appear in comments/docs as future work only.

**Production context:** The `micromodel` route is currently broken
in production — `hivemind.py:1365` overwrites `self.micro_model`
with a `PatternMatchEngine` during cache warming, causing 817
`is_training_due` errors per 7h night run. This is one reason
we exclude it from this sprint.

#### 2.4 — Collection names

```
ChromaDB collections: waggle_memory, swarm_facts, corrections, episodes
SQLite DB: shared_memory.db
```

#### 2.5 — What may be stubbed in this sprint

- `SensorPort` — no real adapter needed, stub only
- Night learning — `LearningService` created but `run_night_tick` may return empty stats
- `MicroModel` routing step — skipped entirely (broken in production, see 2.3)
- `Rules` routing step — skipped entirely

#### 2.6 — What is NOT implemented yet

- MicroModel inference port and adapter (fix `hivemind.py:1365` type mismatch first)
- Rule engine port and adapter
- MQTT/Home Assistant sensor adapter
- Whisper/Piper voice adapters
- Dashboard React build integration
- Multi-node distribution

#### 2.7 — Known production bugs to fix in this refactor

Document these in `PORT_CONTRACTS.md` as a "Known Issues" appendix:

| Bug | Severity | Root Cause | Fix in new architecture |
|-----|----------|------------|------------------------|
| `PatternMatchEngine.is_training_due` — 817 errors/night | P0 | `hivemind.py:1365` overwrites `self.micro_model` with wrong type during cache warming | New `Orchestrator` never mixes types; `micromodel` route excluded from v1 |
| `Task exception never retrieved` — 27/night | P1 | `asyncio.create_task()` without error handling in orchestration | All tasks must use `TaskGroup` or explicit error callbacks |
| Ollama embed timeout (30s) under load | P2 | Single timeout for both generate and embed calls | `OllamaAdapter` uses 120s default; embed-specific timeout configurable |
| Night learning convergence stall | P2 | No auto-reset when fact generation produces 0 new facts for many cycles | `LearningService.run_night_tick()` tracks consecutive empty cycles |

---

## STEP 3 — Create `STATE_OWNERSHIP.md`

Create `STATE_OWNERSHIP.md` at repo root. This locks down who
writes what persistent state.

### Required content:

| State type | Owner (single writer) | Notes |
|---|---|---|
| Persistent memory (MemoryRecord) | `MemoryService` | Only service that calls `MemoryRepositoryPort.store()` |
| Vector embeddings | `MemoryService` | Only service that calls `VectorStorePort.upsert()` |
| Trust scores | `Orchestrator` | Only component that calls `TrustStorePort.update_trust()` |
| Hot cache writes | `ChatService` | Only service that calls `HotCachePort.set()` |
| Corrections | `MemoryService` | Via `store_correction()` |
| Night learning writes | `LearningService` | Calls `MemoryService.ingest()` — never writes directly |
| Scheduler pheromone state | `Orchestrator` | Holds `SchedulerState`, passes to `Scheduler` |
| Agent lifecycle (promote/demote) | `AgentLifecycleManager` | Read-only for all other components |
| Shared memory SQLite | `SQLiteSharedMemory` adapter | Direct writes only from task tracking |

### Forbidden write paths:

- HTTP routes must NEVER write to any store directly
- `Scheduler` must NEVER hold mutable state — receives `SchedulerState` as argument, returns new state
- `RoundTableEngine` must NEVER write memory or trust — returns `ConsensusResult` for caller to act on
- `EventBus` handlers must NOT write persistent state (logging/metrics only)
- No component may read environment variables except `WaggleSettings`

---

## STEP 4 — Create `ACCEPTANCE_CRITERIA.md`

Create `ACCEPTANCE_CRITERIA.md` at repo root with these exact criteria:

```markdown
# Acceptance Criteria — WaggleDance Refactor Sprint

All criteria must be GREEN before the refactor is considered complete.

## Automated checks

1. [ ] `python -m pytest tests/unit/ -v` — all green
2. [ ] `python -m pytest tests/unit_core/ -v` — all green
3. [ ] `python -m pytest tests/unit_app/ -v` — all green
4. [ ] `python -m pytest tests/contracts/ -v` — all green
5. [ ] `Container(settings=WaggleSettings.from_env(), stub=True).build_app()` — no crash
6. [ ] New `/api/chat` returns response in stub mode
7. [ ] `/ready` works even if `dashboard/dist` does not exist
8. [ ] Old `main.py` still works (non-destructive smoke)
9. [ ] Old `start.py` still works (non-destructive smoke)
10. [ ] `tools/waggle_backup.py --tests-only` — all 72 suites green

## Architecture checks

11. [ ] No `os.environ` / `os.getenv` / `dotenv` reads outside `WaggleSettings`
12. [ ] No forbidden imports in `waggledance/core/` or `waggledance/application/`
13. [ ] Every persistent write path has exactly one owner per `STATE_OWNERSHIP.md`
14. [ ] All port implementations match `PORT_CONTRACTS.md` signatures exactly
15. [ ] `routing_policy.select_route()` only returns route types from `ALLOWED_ROUTE_TYPES`

## Production bug regression checks

16. [ ] No code path can assign incompatible type to a typed attribute (prevents `is_training_due` bug)
17. [ ] Every `asyncio.create_task()` in new code has error handling (prevents silent task failures)
18. [ ] `OllamaAdapter` timeout is ≥120s (prevents embed timeout under load)
19. [ ] `LearningService` has convergence stall detection (prevents infinite empty cycles)

## Code quality gates

20. [ ] `python -m compileall waggledance/` — all green (no syntax errors)
21. [ ] `grep -rn 'TODO\|FIXME\|\.\.\.' waggledance/ --include='*.py'` returns zero matches in implementation files (stubs in Protocol classes excluded)
22. [ ] No raw `asyncio.create_task(` without `_track_task` or `TaskGroup` wrapper in new code
23. [ ] Non-stub `Container(settings, stub=False)` constructs all required adapters without in-memory fallbacks
24. [ ] `memory_repository` in non-stub mode is NOT `InMemoryRepository`
25. [ ] New app startup hook initializes required resources without crash
26. [ ] New app shutdown hook cleanly closes external clients/resources
27. [ ] Event bus failure counter is incremented when a handler raises
```

---

## STEP 5 — Verify and commit

Run:
```bash
# Verify all three files exist and are non-empty
wc -l PORT_CONTRACTS.md STATE_OWNERSHIP.md ACCEPTANCE_CRITERIA.md
```

Write a summary of what was created.

---

## RULES

1. Do not create any implementation code. Only documentation.
2. Do not modify any existing files.
3. Every decision must be traceable to existing code you read.
4. If two existing modules disagree on an interface, choose the
   simpler one and document the decision.


---
---

# ═══════════════════════════════════════════════════════════════
# AGENT 1 — CORE + APPLICATION LAYERS (v2.3)
# ═══════════════════════════════════════════════════════════════

> **Aja tämä: Claude Code (Opus 4)**
> Tämä agentti rakentaa `waggledance/core/` ja `waggledance/application/`.
> Ei koske vanhoihin tiedostoihin. Ei koske Agent 2:n hakemistoihin.
> **Vaatimus:** `PORT_CONTRACTS.md`, `STATE_OWNERSHIP.md` ja `ACCEPTANCE_CRITERIA.md`
> on oltava valmiina repo-juuressa ennen tämän ajoa (Phase 0).

---

You are working on the WaggleDance AI codebase. Your task is to
create the new layered architecture from scratch — new files only,
zero modifications to any existing files. Do not ask questions,
do not request confirmation. If something is ambiguous, make the
most reasonable engineering decision and document it in a comment.

## MISSION

Create two new top-level packages:
- `waggledance/core/` — pure runtime, zero external deps
- `waggledance/application/` — use cases and services

These packages must contain NO imports of:
```
fastapi, chromadb, ollama, aiosqlite, sqlite3, httpx,
pydantic (ok in domain models only), dotenv, uvicorn,
mqtt, homeassistant, whisper, piper, torch, react
```

If you need to reference something from the existing codebase,
READ the existing file first, extract the pure logic, and
rewrite it clean in the new location.

---

## PHASE 0 — CONTRACT COMPLIANCE

**Before writing any code, read these files:**

- `PORT_CONTRACTS.md`
- `STATE_OWNERSHIP.md`
- `ACCEPTANCE_CRITERIA.md`

**Obey them exactly.**

- Do NOT invent any new route types, DTO fields, or port methods
  beyond what `PORT_CONTRACTS.md` specifies.
- Do NOT create any persistent write path that is not listed in
  `STATE_OWNERSHIP.md`.
- If a required legacy behavior cannot fit the contract, document
  it in `CONTRACT_MISMATCHES.md` at repo root and use the
  narrowest conservative implementation.

---

## KNOWN PRODUCTION BUGS — READ BEFORE CODING

These bugs were found during 7h overnight production monitoring
on 2026-03-14. The new architecture must prevent all of them.

**BUG 1 (P0): `hivemind.py:1365` overwrites `self.micro_model`
with a `PatternMatchEngine` instance during cache warming, but
`NightModeController` expects `MicroModelOrchestrator` which has
`is_training_due()`. This caused 817 errors in one night.**

→ The new `Orchestrator` must NEVER mix `PatternMatchEngine`
and `MicroModelOrchestrator` in the same attribute. In this
sprint, `micromodel` route is excluded entirely. Do not replicate
the pattern of overwriting typed attributes with incompatible types.

**BUG 2 (P1): `asyncio.create_task()` without error handling
caused 27 silent `Task exception never retrieved` failures.**

→ Every `asyncio.create_task()` in new code MUST be wrapped or
tracked. Fire-and-forget tasks are FORBIDDEN. Use `asyncio.TaskGroup`
(Python 3.11+) or explicit error callbacks. If a background task
fails, it must be logged with full traceback.

**BUG 3 (P2): Night learning converged and stopped producing new
facts, but kept cycling indefinitely without auto-reset.**

→ `LearningService.run_night_tick()` MUST track consecutive
zero-new-fact cycles. After N empty cycles (configurable, default
10), it must reset topic selection to avoid convergence stall.
Return `{"stall_detected": True, "reset": True}` in tick stats.

---

## PHASE 1 — CORE DOMAIN MODELS
**Create: `waggledance/core/domain/`**

Read existing: `core/swarm_scheduler.py`, `hivemind.py` (first 200
lines), `agents/` directory (pick 3–4 YAML files to understand
agent structure).

### `waggledance/core/domain/__init__.py`
*(empty)*

### `waggledance/core/domain/agent.py`
```python
@dataclass
class AgentDefinition:
    id: str
    name: str
    domain: str
    tags: list[str]
    skills: list[str]
    trust_level: int        # 0-4: NOVICE→MASTER
    specialization_score: float
    active: bool
    profile: str            # GADGET/COTTAGE/HOME/FACTORY/ALL

@dataclass
class AgentResult:
    agent_id: str
    response: str
    confidence: float
    latency_ms: float
    source: str             # "hotcache"|"memory"|"llm"|"swarm" ONLY
    metadata: dict
```

**NOTE:** `source` field ONLY allows values from `ALLOWED_ROUTE_TYPES`
in `PORT_CONTRACTS.md`: `hotcache`, `memory`, `llm`, `swarm`.

### `waggledance/core/domain/task.py`
```python
@dataclass
class TaskRequest:
    id: str
    query: str
    language: str           # "fi"|"en"|"auto"
    profile: str
    user_id: str | None
    context: list[dict]     # previous turns
    timestamp: float

@dataclass
class TaskRoute:
    route_type: str         # "hotcache"|"memory"|"llm"|"swarm" ONLY
    selected_agents: list[str]
    confidence: float
    routing_latency_ms: float
```

### `waggledance/core/domain/memory_record.py`
```python
@dataclass
class MemoryRecord:
    id: str
    content: str
    content_fi: str | None
    source: str
    confidence: float
    tags: list[str]
    agent_id: str | None
    created_at: float
    ttl_seconds: int | None
```

### `waggledance/core/domain/trust_score.py`
```python
@dataclass
class TrustSignals:
    hallucination_rate: float
    validation_rate: float
    consensus_agreement: float
    correction_rate: float
    fact_production_rate: float
    freshness_score: float

@dataclass
class AgentTrust:
    agent_id: str
    composite_score: float  # 0.0-1.0
    signals: TrustSignals
    updated_at: float
```

### `waggledance/core/domain/events.py`
```python
from enum import Enum

class EventType(Enum):
    TASK_RECEIVED = "task_received"
    ROUTE_SELECTED = "route_selected"
    AGENT_STARTED = "agent_started"
    AGENT_COMPLETED = "agent_completed"
    ROUND_TABLE_STARTED = "round_table_started"
    ROUND_TABLE_COMPLETED = "round_table_completed"
    MEMORY_STORED = "memory_stored"
    MEMORY_RETRIEVED = "memory_retrieved"
    NIGHT_MODE_STARTED = "night_mode_started"
    NIGHT_MODE_TICK = "night_mode_tick"
    NIGHT_STALL_DETECTED = "night_stall_detected"
    CORRECTION_RECORDED = "correction_recorded"
    CIRCUIT_OPEN = "circuit_open"
    CIRCUIT_CLOSED = "circuit_closed"
    TASK_ERROR = "task_error"
    APP_SHUTDOWN = "app_shutdown"

@dataclass
class DomainEvent:
    type: EventType
    payload: dict
    timestamp: float
    source: str
```

---

## PHASE 2 — PORTS (Pure Python Protocols)
**Create: `waggledance/core/ports/`**

All ports use `typing.Protocol`. No implementations here.
**These MUST match `PORT_CONTRACTS.md` exactly.**

### `waggledance/core/ports/llm_port.py`
```python
class LLMPort(Protocol):
    async def generate(
        self,
        prompt: str,
        model: str = "default",
        temperature: float = 0.7,
        max_tokens: int = 1000
    ) -> str: ...

    async def is_available(self) -> bool: ...

    def get_active_model(self) -> str: ...
```

### `waggledance/core/ports/memory_repository_port.py`

**NOTE:** This replaces the old `MemoryPort`. Hot cache is a separate port.

```python
class MemoryRepositoryPort(Protocol):
    async def search(
        self,
        query: str,
        limit: int = 5,
        language: str = "en",
        tags: list[str] | None = None
    ) -> list[MemoryRecord]: ...

    async def store(self, record: MemoryRecord) -> None: ...

    async def store_correction(
        self,
        query: str,
        wrong: str,
        correct: str
    ) -> None: ...
```

### `waggledance/core/ports/hot_cache_port.py`
```python
class HotCachePort(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str, ttl: int | None = None) -> None: ...
    def evict_expired(self) -> int: ...
    def stats(self) -> dict: ...
```

### `waggledance/core/ports/vector_store_port.py`
```python
class VectorStorePort(Protocol):
    async def upsert(
        self,
        id: str,
        text: str,
        metadata: dict,
        collection: str = "default"
    ) -> None: ...

    async def query(
        self,
        text: str,
        n_results: int = 5,
        collection: str = "default",
        where: dict | None = None
    ) -> list[dict]: ...

    async def is_ready(self) -> bool: ...
```

### `waggledance/core/ports/trust_store_port.py`
```python
class TrustStorePort(Protocol):
    async def get_trust(self, agent_id: str) -> AgentTrust | None: ...
    async def update_trust(
        self,
        agent_id: str,
        signals: TrustSignals
    ) -> AgentTrust: ...
    async def get_ranking(self, limit: int = 20) -> list[AgentTrust]: ...
```

### `waggledance/core/ports/sensor_port.py`
```python
class SensorPort(Protocol):
    async def get_latest(self, sensor_id: str) -> dict | None: ...
    async def get_all_active(self) -> list[dict]: ...
```

### `waggledance/core/ports/event_bus_port.py`
```python
class EventBusPort(Protocol):
    async def publish(self, event: DomainEvent) -> None: ...
    def subscribe(
        self,
        event_type: EventType,
        handler: Callable
    ) -> None: ...
```

### `waggledance/core/ports/config_port.py`
```python
class ConfigPort(Protocol):
    def get(self, key: str, default: Any = None) -> Any: ...
    def get_profile(self) -> str: ...         # GADGET/COTTAGE/HOME/FACTORY
    def get_hardware_tier(self) -> str: ...   # minimal/light/standard/professional/enterprise
```

---

## PHASE 3 — ORCHESTRATION
**Create: `waggledance/core/orchestration/`**

Read existing: `core/swarm_scheduler.py` FULLY before writing
`scheduler.py`. Read `hivemind.py` lines 1–400 before writing
`orchestrator.py`. Extract pure logic, leave all I/O behind.

### `waggledance/core/orchestration/scheduler.py`
Port the core scoring logic from `core/swarm_scheduler.py`.

**Keep:** Top-K selection, exploration quota, load penalty,
newcomer boost, pheromone/EMA update, tag/skill matching.

**Remove:** Any direct file I/O, env reads, SQLite calls.

The scheduler receives agents as `AgentDefinition` list and
returns ranked candidates. Fully synchronous and stateless
between calls (state passed in as argument).

```python
@dataclass
class SchedulerState:
    pheromone_scores: dict[str, float]  # agent_id -> score
    usage_counts: dict[str, int]
    recent_successes: dict[str, float]

class Scheduler:
    def __init__(self, config: ConfigPort): ...

    def select_agents(
        self,
        task: TaskRequest,
        candidates: list[AgentDefinition],
        state: SchedulerState,
        trust_scores: dict[str, AgentTrust],
        n: int = 6
    ) -> list[AgentDefinition]:
        # Top-K with exploration, load penalty, trust boost
        ...

    def update_state(
        self,
        state: SchedulerState,
        agent_id: str,
        result: AgentResult
    ) -> SchedulerState:
        # EMA pheromone update, immutable style — returns NEW state
        ...
```

### `waggledance/core/orchestration/round_table.py`
Port consensus logic from `core/round_table_controller.py`.
Remove all HiveMind proxy references.
All LLM calls go through `LLMPort`.

```python
@dataclass
class ConsensusResult:
    consensus: str
    confidence: float
    participating_agents: list[str]
    dissenting_views: list[str]
    latency_ms: float

class RoundTableEngine:
    def __init__(self, llm: LLMPort, event_bus: EventBusPort): ...

    async def deliberate(
        self,
        task: TaskRequest,
        agents: list[AgentDefinition],
        agent_responses: list[AgentResult]
    ) -> ConsensusResult: ...
```

### `waggledance/core/orchestration/routing_policy.py`
Extract routing decision logic from `backend/routes/chat.py`
and `hivemind.py`. Pure function — no I/O.

**CRITICAL:** In this sprint, routing policy may ONLY return these
route types: `hotcache`, `memory`, `llm`, `swarm`. No `micromodel`,
no `rules`. Those are future work.

```python
@dataclass
class RoutingFeatures:
    query_length: int
    language: str
    has_hot_cache_hit: bool
    memory_score: float
    is_time_query: bool
    is_system_query: bool
    matched_keywords: list[str]
    profile: str

ALLOWED_ROUTE_TYPES = frozenset({"hotcache", "memory", "llm", "swarm"})

def select_route(
    features: RoutingFeatures,
    config: ConfigPort
) -> TaskRoute:
    """
    Pure routing decision.
    Order: hotcache → memory → llm → swarm
    Only returns route_type from ALLOWED_ROUTE_TYPES.
    """
    ...
```

### `waggledance/core/orchestration/orchestrator.py`
The Orchestrator is the new HiveMind — small, injectable,
no God-Object. All I/O through ports.

**NOTE:** Orchestrator receives `MemoryRepositoryPort`, NOT
the old combined `MemoryPort`. Hot cache access happens at the
`ChatService` level, not here.

**WARNING:** `hivemind.py:1365` overwrites `self.micro_model` with
a `PatternMatchEngine` during cache warming — a known P0 bug.
The new `Orchestrator` must NEVER assign incompatible types to
any typed attribute. Use separate, clearly-typed fields.

```python
class Orchestrator:
    def __init__(
        self,
        scheduler: Scheduler,
        round_table: RoundTableEngine,
        memory: MemoryRepositoryPort,
        vector_store: VectorStorePort,
        llm: LLMPort,
        trust_store: TrustStorePort,
        event_bus: EventBusPort,
        config: ConfigPort,
        agents: list[AgentDefinition],
    ):
        self._scheduler_state = SchedulerState(
            pheromone_scores={}, usage_counts={}, recent_successes={}
        )
        # Track background tasks to prevent silent failures (BUG 2 fix)
        self._background_tasks: set[asyncio.Task] = set()
        ...

    async def handle_task(self, task: TaskRequest) -> AgentResult:
        """Main entry point. Routes and executes a task."""
        ...

    async def run_round_table(
        self, task: TaskRequest
    ) -> ConsensusResult: ...

    async def is_ready(self) -> bool:
        """Check all ports are healthy."""
        ...

    def _track_task(self, coro) -> asyncio.Task:
        """Create a tracked background task with error logging.
        Prevents 'Task exception never retrieved' (BUG 2)."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        task.add_done_callback(self._log_task_error)
        return task

    def _log_task_error(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            import logging
            logging.getLogger(__name__).error(
                f"Background task failed: {exc}", exc_info=exc
            )
```

### `waggledance/core/orchestration/lifecycle.py`
```python
class AgentLifecycleManager:
    def spawn_for_profile(
        self,
        all_agents: list[AgentDefinition],
        profile: str
    ) -> list[AgentDefinition]: ...

    def promote_trust_level(
        self,
        agent: AgentDefinition,
        trust: AgentTrust
    ) -> AgentDefinition: ...
```

---

## PHASE 4 — POLICIES
**Create: `waggledance/core/policies/`**

### `waggledance/core/policies/confidence_policy.py`
```python
def should_escalate_to_swarm(
    result: AgentResult,
    threshold: float = 0.7
) -> bool: ...

def should_cache_result(
    result: AgentResult,
    query_frequency: int
) -> bool: ...
```

### `waggledance/core/policies/fallback_policy.py`

**NOTE:** Fallback chain in this sprint is: `memory → llm → swarm`.
No `micromodel` step.

```python
class FallbackChain:
    """
    Ordered fallback: memory → llm → swarm.
    Returns first result exceeding min_confidence.
    """
    def __init__(
        self,
        steps: list[str] | None = None,
        min_confidence: float = 0.6
    ):
        self.steps = steps or ["memory", "llm", "swarm"]
        self.min_confidence = min_confidence

    async def execute(
        self,
        task: TaskRequest,
        handlers: dict[str, Callable]
    ) -> AgentResult: ...
```

### `waggledance/core/policies/escalation_policy.py`
```python
class EscalationPolicy:
    """
    When does a single-agent answer get escalated to Round Table?
    Criteria: low confidence, hallucination signal, user correction.
    """
    def needs_round_table(
        self,
        result: AgentResult,
        task: TaskRequest
    ) -> bool: ...
```

---

## PHASE 5 — APPLICATION LAYER
**Create: `waggledance/application/`**

### DTOs — `waggledance/application/dto/chat_dto.py`
```python
@dataclass
class ChatRequest:
    query: str
    language: str = "auto"
    profile: str = "COTTAGE"
    user_id: str | None = None
    session_id: str | None = None
    context_turns: int = 5

@dataclass
class ChatResult:
    response: str
    language: str
    source: str             # which route served this
    confidence: float
    latency_ms: float
    agent_id: str | None
    round_table: bool
    cached: bool
```

### DTOs — `waggledance/application/dto/readiness_dto.py`
```python
@dataclass
class ComponentStatus:
    name: str
    ready: bool
    message: str
    latency_ms: float | None

@dataclass
class ReadinessStatus:
    ready: bool
    components: list[ComponentStatus]
    uptime_seconds: float
```

### `waggledance/application/services/chat_service.py`

Read `backend/routes/chat.py` and `core/chat_handler.py`,
extract ALL decision-making logic, place it here.
The HTTP route must become a thin wrapper after this.

**NOTE:** `ChatService` owns hot cache reads/writes per `STATE_OWNERSHIP.md`.
It uses `HotCachePort` for cache and `MemoryService` for memory retrieval.
It must NOT depend on `MemoryRepositoryPort` directly — all memory
access goes through `MemoryService.retrieve_context()`.

```python
class ChatService:
    def __init__(
        self,
        orchestrator: Orchestrator,
        memory_service: MemoryService,
        hot_cache: HotCachePort,
        routing_policy_fn: Callable,    # select_route
        config: ConfigPort,
    ): ...

    async def handle(self, req: ChatRequest) -> ChatResult:
        """
        1. Detect language
        2. Normalize query (Voikko via translation, if Finnish)
        3. Check hot cache (via HotCachePort)
        4. Fetch memory context (via MemoryService.retrieve_context())
        5. Extract routing features
        6. Select route
        7. Execute via orchestrator
        8. Apply escalation policy if needed
        9. Store result if worth caching (via HotCachePort)
        10. Return ChatResult
        """
        ...
```

### `waggledance/application/services/memory_service.py`

Read `core/memory_engine.py`, extract service-level operations
(not the ChromaDB implementation).

**NOTE:** `MemoryService` receives a `MemoryRepositoryPort` (never None)
and a `VectorStorePort`. It is the ONLY component that writes
persistent memory per `STATE_OWNERSHIP.md`.

```python
class MemoryService:
    def __init__(
        self,
        vector_store: VectorStorePort,
        memory: MemoryRepositoryPort,
        event_bus: EventBusPort,
    ):
        assert memory is not None, "MemoryRepositoryPort must not be None"
        ...

    async def ingest(
        self,
        content: str,
        source: str,
        tags: list[str] = [],
        agent_id: str | None = None
    ) -> MemoryRecord: ...

    async def retrieve_context(
        self,
        query: str,
        language: str = "en",
        limit: int = 5
    ) -> list[MemoryRecord]: ...

    async def record_correction(
        self,
        query: str,
        wrong: str,
        correct: str
    ) -> None: ...
```

### `waggledance/application/services/learning_service.py`

**CRITICAL:** Must include convergence stall detection (BUG 3 fix).

```python
class LearningService:
    # Max consecutive empty cycles before topic reset
    STALL_THRESHOLD: int = 10

    def __init__(
        self,
        orchestrator: Orchestrator,
        memory_service: MemoryService,
        llm: LLMPort,
        event_bus: EventBusPort,
        stall_threshold: int = 10,
    ):
        self._consecutive_empty_cycles: int = 0
        self._stall_threshold = stall_threshold
        ...

    async def run_night_tick(self) -> dict:
        """
        One learning cycle:
        1. Generate fact candidates (llama1b)
        2. Validate candidates (phi4-mini)
        3. Store validated facts via memory_service.ingest()
        4. Track consecutive empty cycles
        5. If stall detected: reset topic selection, emit event
        6. Return tick stats including stall_detected flag
        """
        # ... implementation ...
        new_facts = ...  # count of newly stored facts

        if new_facts == 0:
            self._consecutive_empty_cycles += 1
        else:
            self._consecutive_empty_cycles = 0

        stall_detected = self._consecutive_empty_cycles >= self._stall_threshold
        if stall_detected:
            self._reset_topic_selection()
            self._consecutive_empty_cycles = 0
            await self._event_bus.publish(DomainEvent(
                type=EventType.NIGHT_STALL_DETECTED,
                payload={"threshold": self._stall_threshold},
                timestamp=time.time(),
                source="learning_service",
            ))

        return {
            "new_facts": new_facts,
            "stall_detected": stall_detected,
            "consecutive_empty": self._consecutive_empty_cycles,
        }

    async def enrich_gap(self, topic: str) -> list[MemoryRecord]: ...
    async def retrain_micromodel_if_due(self) -> bool: ...

    def _reset_topic_selection(self) -> None:
        """Reset topic pool to break convergence stall."""
        ...
```

### `waggledance/application/services/readiness_service.py`
```python
class ReadinessService:
    def __init__(
        self,
        orchestrator: Orchestrator,
        vector_store: VectorStorePort,
        llm: LLMPort,
    ): ...

    async def check(self) -> ReadinessStatus:
        """Check all components. Never raises, always returns status."""
        ...

    async def wait_until_ready(
        self,
        timeout_seconds: float = 30.0
    ) -> bool: ...
```

### `waggledance/application/services/bootstrap_service.py`

Replaces startup logic currently in `main.py` and `hivemind.py`.
Does NOT install packages, does NOT write `.env` files.

```python
class BootstrapService:
    def __init__(
        self,
        config: ConfigPort,
        lifecycle: AgentLifecycleManager,
    ): ...

    async def load_agents(
        self,
        agents_dir: Path
    ) -> list[AgentDefinition]:
        """Load all YAML agent definitions for current profile."""
        ...

    async def warm_cache_candidates(
        self,
        memory_service: MemoryService,
    ) -> list[tuple[str, str, int | None]]:
        """Return (key, value, ttl) tuples for cache preloading.
        Does NOT write to hot cache directly — ChatService is the
        sole hot cache writer per STATE_OWNERSHIP.md.
        Caller must pass these to ChatService for cache population."""
        ...
```

### Use Cases — `waggledance/application/use_cases/`

Thin files — one use case each, call services only:

```python
# handle_chat.py
async def handle_chat(req: ChatRequest, chat_service: ChatService) -> ChatResult: ...

# store_memory.py
async def store_memory(content: str, source: str, memory_service: MemoryService, tags: list[str] = []) -> MemoryRecord: ...

# run_night_mode_tick.py
async def run_night_mode_tick(learning_service: LearningService) -> dict: ...

# spawn_profile_agents.py
async def spawn_profile_agents(profile: str, bootstrap_service: BootstrapService, lifecycle: AgentLifecycleManager) -> list[AgentDefinition]: ...
```

---

## PHASE 6 — CORE/APPLICATION UNIT TESTS

**Create fast tests using only mocks and fakes. Zero I/O.**

### `tests/unit_core/conftest.py`
```python
import pytest
from unittest.mock import AsyncMock, MagicMock
# Create mock fixtures for all ports
```

### `tests/unit_core/test_scheduler.py`
Write at least **10 tests** covering:
- Top-K selection returns correct number of agents
- Exploration quota includes low-usage agents
- Load penalty reduces score for recently used agents
- Newcomer boost elevates new agents
- Pheromone/EMA update increases score on success
- Pheromone/EMA update decreases score on failure
- Empty candidate list returns empty
- Trust score boost works correctly
- Tag matching filters agents properly
- Profile filtering excludes wrong-profile agents

### `tests/unit_core/test_routing_policy.py`
Write at least **8 tests** covering:
- Hot cache hit returns `hotcache` route
- High memory score returns `memory` route
- Time query with no cache/memory returns `llm` route
- Complex multi-keyword query routes to `swarm`
- Default fallback is `llm`
- Return type is always `TaskRoute`
- `route_type` is always in `ALLOWED_ROUTE_TYPES`
- System query routes correctly

### `tests/unit_core/test_fallback_policy.py`
Write at least **6 tests** covering:
- Returns first result above min_confidence
- Falls through all steps if all below threshold
- Handles handler raising exception gracefully
- Custom step list works
- Empty handlers dict raises or returns sensible default
- Order of execution matches step order

### `tests/unit_core/test_escalation_policy.py`
Write at least **4 tests** covering:
- Low confidence triggers round table
- High confidence does not trigger
- Hallucination signal triggers
- User correction history triggers

### `tests/unit_core/test_orchestrator_task_tracking.py`

**NEW — regression tests for BUG 2 (silent task failures).**

Write at least **4 tests** covering:
- `_track_task` adds task to `_background_tasks` set
- Completed task is removed from set via done callback
- Failed task logs error via `_log_task_error`
- Cancelled task does not log error

### `tests/unit_app/conftest.py`
```python
import pytest
from unittest.mock import AsyncMock, MagicMock
# Create mock fixtures for services
```

### `tests/unit_app/test_chat_service.py`
Write at least **10 tests** with mocked ports covering:
- Hot cache hit returns cached response
- Cache miss falls through to orchestrator
- Language detection sets correct language
- Result is cached when policy says yes
- ChatResult fields are correctly populated
- Error in orchestrator is handled gracefully
- Profile is forwarded to routing
- Escalation triggers round table
- Round table result replaces initial result
- Session context is passed correctly

### `tests/unit_app/test_learning_service.py`

**NEW — regression tests for BUG 3 (convergence stall).**

Write at least **6 tests** covering:
- `run_night_tick` returns `new_facts` count
- Consecutive empty cycles increment counter
- Stall detected after N empty cycles
- Stall resets topic selection and counter
- `NIGHT_STALL_DETECTED` event is published on stall
- Non-empty cycle resets counter to 0

### `tests/contracts/test_port_contracts.py`
Write at least **8 tests** that validate:
- All ports are `typing.Protocol` subclasses
- All DTO fields match `PORT_CONTRACTS.md`
- `ALLOWED_ROUTE_TYPES` contains exactly `{hotcache, memory, llm, swarm}`
- `EventType` enum has all expected values (including `NIGHT_STALL_DETECTED`, `TASK_ERROR`, `APP_SHUTDOWN`)
- `AgentResult.source` type annotation is `str`
- `TaskRoute.route_type` type annotation is `str`
- All port methods have correct signatures (use `inspect`)
- `MemoryRepositoryPort` and `HotCachePort` are separate protocols

---

## PHASE 7 — PACKAGE FILES

Create `waggledance/__init__.py`:
```python
__version__ = "1.15.0"
__description__ = "Local-first AI runtime"
```

Create `NEW_ARCHITECTURE.md` at repo root documenting:
- New package structure and 3 layers
- Port interfaces (referencing `PORT_CONTRACTS.md`)
- Migration map (old file → new location)
- "What's NOT done yet" section (including micromodel, rules, sensors)
- "Production bugs addressed" section listing BUGs 1–3

---

## RULES

1. Read existing files before porting their logic. Never guess.
2. If an existing function is >50 lines, summarize it in a docstring comment before reimplementing.
3. Every class must have a one-line docstring.
4. Use Python 3.13 syntax. `dataclasses` for data models. `typing.Protocol` for all ports.
5. No `try/except` blocks that swallow exceptions silently.
6. **In this sprint, routing policy may return ONLY these route types: `hotcache`, `memory`, `llm`, `swarm` unless `PORT_CONTRACTS.md` explicitly adds more.**
7. **No component may write persistent state unless it is the designated owner in `STATE_OWNERSHIP.md`.**
8. **Every `asyncio.create_task()` MUST use `_track_task()` or `TaskGroup`. Fire-and-forget is FORBIDDEN.**
9. **Never assign an incompatible type to a typed attribute. Use separate fields if multiple types are needed.**
10. When done, run and fix any errors:
    ```bash
    python -c "import waggledance.core; import waggledance.application; print('OK')"
    python -m pytest tests/unit_core/ tests/unit_app/ tests/contracts/ -v
    # No TODOs or stubs left:
    grep -rn 'TODO\|FIXME' waggledance/core/ waggledance/application/ --include='*.py' && echo "FAIL: unresolved TODOs" && exit 1 || echo "OK"
    # No untracked create_task:
    grep -rn 'asyncio.create_task' waggledance/core/ waggledance/application/ --include='*.py' | grep -v '_track_task\|TaskGroup' && echo "FAIL: raw create_task" && exit 1 || echo "OK"
    # Compiles:
    python -m compileall waggledance/core/ waggledance/application/
    ```
11. Write a final summary of what you created, what you read, and what Agent 2 needs to do.
12. **Write `SPRINT_REPORT_AGENT1.md`** at repo root with:
    - Every file created (full paths)
    - Every directory created
    - Every test file created
    - Any contract mismatch encountered
    - Any deliberate stub left in place (must be zero in implementation files)
    - Any risk that must be handled in integration


---
---

# ═══════════════════════════════════════════════════════════════
# AGENT 2 — ADAPTERS + BOOTSTRAP + TESTS (v2.3)
# ═══════════════════════════════════════════════════════════════

> **Aja tämä: Claude Code (Sonnet)**
> Tämä agentti rakentaa `waggledance/adapters/`, `waggledance/bootstrap/` ja `tests/unit/`.
> Ei koske vanhoihin tiedostoihin. Ei koske Agent 1:n hakemistoihin (`waggledance/core/`, `waggledance/application/`).
> **Vaatimus:** `PORT_CONTRACTS.md`, `STATE_OWNERSHIP.md` ja `ACCEPTANCE_CRITERIA.md`
> on oltava valmiina repo-juuressa ennen tämän ajoa (Phase 0).

---

You are working on the WaggleDance AI codebase. Your task is to
create adapter implementations and test infrastructure.
Do not ask questions, do not request confirmation.
Make the best engineering decision when ambiguous and document it.

**IMPORTANT:** Agent 1 is creating `waggledance/core/` and
`waggledance/application/` in parallel. You do NOT touch those
directories. You create `waggledance/adapters/`, `waggledance/bootstrap/`
and `tests/unit/`.

---

## PHASE 0 — CONTRACT COMPLIANCE

**Before writing any code, read these files:**

- `PORT_CONTRACTS.md`
- `STATE_OWNERSHIP.md`

**Obey them exactly.**

- Every adapter class MUST implement the exact method signatures
  from `PORT_CONTRACTS.md`. Do not add extra public methods.
- Do not invent new port interfaces. If you need something not
  in the contract, document it in `CONTRACT_MISMATCHES.md`.

---

## KNOWN PRODUCTION BUGS — READ BEFORE CODING

**BUG 3 (P2): Ollama embed timeout (30s) under load.**
Production saw `Read timed out` errors when embedding calls
competed with night learning generate calls.

→ `OllamaAdapter` must use `timeout_seconds=120.0` as default.
Consider separate timeout for `/api/embed` vs `/api/generate`
if both are exposed in future.

**BUG 2 (P1): Task exception never retrieved — 27 occurrences.**
→ Even in adapters, if you create any async tasks, they must
have error callbacks. No fire-and-forget.

---

## MISSION

Create:
- `waggledance/adapters/` — all external integrations
- `waggledance/bootstrap/` — dependency wiring
- `tests/unit/` — fast tests using mocks only

---

## PHASE 1 — LLM ADAPTER
**Read `core/llm_provider.py` FULLY before starting.**
**Create: `waggledance/adapters/llm/`**

### `waggledance/adapters/llm/ollama_adapter.py`

Port from `core/llm_provider.py`.

**Keep:** circuit breaker, httpx async client, model switching,
response dataclass, health check.

**Implements:** `LLMPort`

**NOTE:** Default timeout is 120s (not 30s) to prevent embed
timeouts under load (BUG 3 fix).

```python
# implements LLMPort

OPEN_THRESHOLD = 3       # failures before circuit opens
RECOVERY_SECONDS = 30    # seconds before retry

class OllamaAdapter:
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        default_model: str = "phi4-mini",
        timeout_seconds: float = 120.0,   # Was 30s — caused timeouts under load
    ):
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds, connect=10.0),
        )
        self._circuit_failures = 0
        self._circuit_open_since: float | None = None
        self._active_model = default_model

    async def generate(self, prompt: str, **kwargs) -> str:
        """Generate with circuit breaker protection."""
        ...

    async def is_available(self) -> bool:
        """HEAD /api/tags — fast health check."""
        ...

    def get_active_model(self) -> str:
        return self._active_model

    def _is_circuit_open(self) -> bool:
        if self._circuit_open_since is None:
            return False
        if time.monotonic() - self._circuit_open_since > RECOVERY_SECONDS:
            self._circuit_failures = 0
            self._circuit_open_since = None
            return False
        return True
```

### `waggledance/adapters/llm/stub_llm_adapter.py`

Fast in-process stub for `--stub` mode and tests.

```python
# implements LLMPort

CANNED_RESPONSES = {
    "time": "The current time is {time}.",
    "weather": "Weather data unavailable in stub mode.",
    "default": "Stub response: I received your query about '{query}'.",
}

class StubLLMAdapter:
    async def generate(self, prompt: str, **kwargs) -> str:
        # Return canned response based on keywords
        ...
    async def is_available(self) -> bool:
        return True
    def get_active_model(self) -> str:
        return "stub-v1"
```

---

## PHASE 2 — MEMORY ADAPTERS
**Read `core/memory_engine.py` FULLY (skim class structure, read
`store`/`search`/`corrections` methods carefully).**
**Create: `waggledance/adapters/memory/`**

### `waggledance/adapters/memory/chroma_vector_store.py`

Extracts ChromaDB logic from `memory_engine.py`.

**Implements:** `VectorStorePort`

Collections: `waggle_memory`, `swarm_facts`, `corrections`, `episodes`.

Keep the `disk_guard` and circuit breaker logic.
Keep the scoring formula: `1.0 - (dist / 2.0)`

```python
# implements VectorStorePort

class ChromaVectorStore:
    def __init__(
        self,
        persist_directory: str = "./chroma_data",
        embedding_model: str = "nomic-embed-text",
    ): ...

    async def upsert(
        self, id, text, metadata, collection="waggle_memory"
    ): ...

    async def query(
        self, text, n_results=5, collection="waggle_memory", where=None
    ) -> list[dict]:
        # Returns: [{"id": ..., "content": ..., "metadata": ..., "score": ...}]
        ...

    async def is_ready(self) -> bool: ...
```

### `waggledance/adapters/memory/in_memory_vector_store.py`

Simple in-process store for stub mode and unit tests —
no ChromaDB dependency.

```python
# implements VectorStorePort

class InMemoryVectorStore:
    def __init__(self):
        self._records: dict[str, dict] = {}

    async def upsert(self, id, text, metadata, collection="default"):
        self._records[f"{collection}:{id}"] = {
            "id": id, "text": text, "metadata": metadata,
            "collection": collection
        }

    async def query(
        self, text, n_results=5, collection="default", where=None
    ) -> list[dict]:
        # Simple substring matching — good enough for unit tests
        ...

    async def is_ready(self) -> bool:
        return True
```

### `waggledance/adapters/memory/in_memory_repository.py`

**Implements `MemoryRepositoryPort` for stub mode and tests ONLY.**

```python
# implements MemoryRepositoryPort

class InMemoryRepository:
    """In-memory implementation of MemoryRepositoryPort for testing."""

    def __init__(self):
        self._records: list[MemoryRecord] = []
        self._corrections: list[dict] = []

    async def search(
        self, query: str, limit: int = 5,
        language: str = "en", tags: list[str] | None = None
    ) -> list[MemoryRecord]:
        # Simple substring search
        ...

    async def store(self, record: MemoryRecord) -> None:
        self._records.append(record)

    async def store_correction(
        self, query: str, wrong: str, correct: str
    ) -> None:
        self._corrections.append({"query": query, "wrong": wrong, "correct": correct})
```

### `waggledance/adapters/memory/chroma_memory_repository.py`

**NEW — Implements `MemoryRepositoryPort` for production (non-stub) mode.**
**Using `InMemoryRepository` in non-stub mode is FORBIDDEN.**

Read `core/memory_engine.py` store/search/corrections methods
and port the repository-level logic here.

```python
# implements MemoryRepositoryPort

class ChromaMemoryRepository:
    """Production MemoryRepositoryPort backed by ChromaDB via VectorStorePort."""

    def __init__(
        self,
        vector_store: VectorStorePort,
        collection: str = "waggle_memory",
    ):
        self._vector_store = vector_store
        self._collection = collection

    async def search(
        self, query: str, limit: int = 5,
        language: str = "en", tags: list[str] | None = None
    ) -> list[MemoryRecord]:
        """Search via vector store, convert results to MemoryRecord."""
        where = None
        if tags:
            where = {"tags": {"$in": tags}}
        results = await self._vector_store.query(
            text=query,
            n_results=limit,
            collection=self._collection,
            where=where,
        )
        return [self._to_memory_record(r) for r in results]

    async def store(self, record: MemoryRecord) -> None:
        """Persist a MemoryRecord to the vector store."""
        metadata = {
            "source": record.source,
            "confidence": record.confidence,
            "tags": record.tags,
            "agent_id": record.agent_id or "",
            "created_at": record.created_at,
            "language": "fi" if record.content_fi else "en",
        }
        if record.ttl_seconds is not None:
            metadata["ttl_seconds"] = record.ttl_seconds
        await self._vector_store.upsert(
            id=record.id,
            text=record.content,
            metadata=metadata,
            collection=self._collection,
        )

    async def store_correction(
        self, query: str, wrong: str, correct: str
    ) -> None:
        """Store a correction record in the corrections collection."""
        import time, uuid
        await self._vector_store.upsert(
            id=str(uuid.uuid4()),
            text=f"Q: {query}\nWrong: {wrong}\nCorrect: {correct}",
            metadata={
                "type": "correction",
                "query": query,
                "created_at": time.time(),
            },
            collection="corrections",
        )

    def _to_memory_record(self, result: dict) -> MemoryRecord:
        """Convert a vector store result dict to MemoryRecord."""
        meta = result.get("metadata", {})
        return MemoryRecord(
            id=result.get("id", ""),
            content=result.get("content", result.get("text", "")),
            content_fi=None,
            source=meta.get("source", "vector_store"),
            confidence=meta.get("confidence", result.get("score", 0.0)),
            tags=meta.get("tags", []),
            agent_id=meta.get("agent_id") or None,
            created_at=meta.get("created_at", 0.0),
            ttl_seconds=meta.get("ttl_seconds"),
        )
```

### `waggledance/adapters/memory/sqlite_shared_memory.py`

Port from `memory/shared_memory.py`.

**Keep:** aiosqlite, WAL mode, whitelist-based `update_task`,
schema initialization.

**Add:** explicit migration version tracking.

```python
SCHEMA_VERSION = 1

ALLOWED_UPDATE_COLUMNS = frozenset({
    "status", "result", "error", "completed_at",
    "agent_id", "confidence", "latency_ms"
})

class SQLiteSharedMemory:
    def __init__(self, db_path: str = "./shared_memory.db"): ...

    async def initialize(self) -> None:
        """Create tables if not exist, check schema version."""
        ...

    async def create_task(self, task_id: str, task_type: str) -> None: ...

    async def update_task(self, task_id: str, updates: dict) -> None:
        """Only allows whitelisted columns."""
        forbidden = set(updates.keys()) - ALLOWED_UPDATE_COLUMNS
        if forbidden:
            raise ValueError(f"Update of columns not allowed: {forbidden}")
        ...

    async def get_task(self, task_id: str) -> dict | None: ...
```

### `waggledance/adapters/memory/hot_cache.py`

In-process hot cache extracted from `consciousness.py` / `memory_engine.py`.
LRU with TTL. Thread-safe. Pure Python, no external deps.

**Implements:** `HotCachePort`

```python
# implements HotCachePort

class HotCache:
    def __init__(self, max_size: int = 1000, default_ttl: int = 3600): ...

    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str, ttl: int | None = None) -> None: ...

    def evict_expired(self) -> int:
        """Returns number evicted."""
        ...

    def stats(self) -> dict:
        return {
            "size": len(self._cache),
            "hits": self._hits,
            "misses": self._misses
        }
```

---

## PHASE 3 — TRUST ADAPTER
**Create: `waggledance/adapters/trust/`**

### `waggledance/adapters/trust/in_memory_trust_store.py`

**Implements `TrustStorePort`. Required by `PORT_CONTRACTS.md`.**

```python
# implements TrustStorePort

import time
from waggledance.core.domain.trust_score import AgentTrust, TrustSignals

class InMemoryTrustStore:
    """In-memory trust store. Production may use SQLite later."""

    def __init__(self):
        self._scores: dict[str, AgentTrust] = {}

    async def get_trust(self, agent_id: str) -> AgentTrust | None:
        return self._scores.get(agent_id)

    async def update_trust(
        self, agent_id: str, signals: TrustSignals
    ) -> AgentTrust:
        composite = self._compute_composite(signals)
        trust = AgentTrust(
            agent_id=agent_id,
            composite_score=composite,
            signals=signals,
            updated_at=time.time(),
        )
        self._scores[agent_id] = trust
        return trust

    async def get_ranking(self, limit: int = 20) -> list[AgentTrust]:
        ranked = sorted(
            self._scores.values(),
            key=lambda t: t.composite_score,
            reverse=True,
        )
        return ranked[:limit]

    def _compute_composite(self, s: TrustSignals) -> float:
        return (
            (1.0 - s.hallucination_rate) * 0.25
            + s.validation_rate * 0.20
            + s.consensus_agreement * 0.20
            + (1.0 - s.correction_rate) * 0.15
            + s.fact_production_rate * 0.10
            + s.freshness_score * 0.10
        )
```

---

## PHASE 4 — HTTP ADAPTER (thin FastAPI layer)
**Read `backend/routes/chat.py` and `backend/auth.py`.**
**Create: `waggledance/adapters/http/`**

The HTTP adapter is a **THIN SHELL**. No business logic here.
Every route does: parse → call service → format response.

### `waggledance/adapters/http/deps.py`

**FastAPI dependency providers. All `Depends(...)` resolve here.**

```python
from fastapi import Request

def get_container(request: Request) -> "Container":
    return request.app.state.container

def get_chat_service(request: Request):
    return get_container(request).chat_service

def get_readiness_service(request: Request):
    return get_container(request).readiness_service

def get_memory_service(request: Request):
    return get_container(request).memory_service

def require_auth(request: Request):
    """Placeholder — actual auth done by middleware."""
    pass
```

### `waggledance/adapters/http/api.py`

**CRITICAL RULES:**
- `api.py` must NOT include any router that has not been created
- `StaticFiles` mount must be **conditional** — missing `dashboard/dist`
  must NOT crash app creation
- Container is stored on `app.state.container`

```python
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle: startup and shutdown."""
    container = app.state.container
    # --- STARTUP ---
    if hasattr(container, 'shared_memory'):
        await container.shared_memory.initialize()
    # Verify required adapters are constructible
    _ = container.llm
    _ = container.vector_store
    _ = container.memory_repository
    _ = container.trust_store
    import logging
    logging.getLogger(__name__).info("WaggleDance startup complete")

    yield  # app is running

    # --- SHUTDOWN ---
    # Close OllamaAdapter httpx client if present
    if hasattr(container.llm, '_client'):
        await container.llm._client.aclose()
    # Emit shutdown event
    from waggledance.core.domain.events import DomainEvent, EventType
    import time
    await container.event_bus.publish(DomainEvent(
        type=EventType.APP_SHUTDOWN,
        payload={},
        timestamp=time.time(),
        source="lifespan",
    ))
    logging.getLogger(__name__).info("WaggleDance shutdown complete")

def create_app(container: "Container") -> FastAPI:
    app = FastAPI(
        title="WaggleDance AI",
        version="1.15.0",
        lifespan=lifespan,
    )
    app.state.container = container

    # Middleware — auth gets api_key from settings, NOT from env
    app.add_middleware(
        BearerAuthMiddleware,
        api_key=container._settings.api_key,
    )
    app.add_middleware(RateLimitMiddleware, requests_per_minute=60)

    # Routes — status at root, others under /api
    app.include_router(status_router)                  # /health, /ready
    app.include_router(chat_router, prefix="/api")     # /api/chat
    app.include_router(memory_router, prefix="/api")   # /api/memory/*

    # Static files — conditional mount
    dashboard_dir = "dashboard/dist"
    if os.path.isdir(dashboard_dir):
        app.mount("/", StaticFiles(directory=dashboard_dir, html=True))

    return app
```

### `waggledance/adapters/http/routes/chat.py`

**THIN WRAPPER. No retrieval logic, no keyword matching,
no YAML loading, no rate limiting business logic here.**

```python
router = APIRouter()

@router.post("/chat")
async def chat_endpoint(
    request: ChatHttpRequest,
    chat_service: ChatService = Depends(get_chat_service),
) -> ChatHttpResponse:
    result = await chat_service.handle(request.to_dto())
    return ChatHttpResponse.from_result(result)

class ChatHttpRequest(BaseModel):
    query: str
    language: str = "auto"
    profile: str = "COTTAGE"

    def to_dto(self) -> ChatRequest:
        ...

class ChatHttpResponse(BaseModel):
    response: str
    source: str
    confidence: float
    latency_ms: float
    cached: bool

    @classmethod
    def from_result(cls, r: ChatResult) -> "ChatHttpResponse":
        ...
```

### `waggledance/adapters/http/routes/status.py`
```python
router = APIRouter()

@router.get("/health")
async def health():
    return {"status": "ok"}

@router.get("/ready")
async def ready(svc: ReadinessService = Depends(get_readiness_service)):
    status = await svc.check()
    code = 200 if status.ready else 503
    return JSONResponse(status_code=code, content=asdict(status))
```

### `waggledance/adapters/http/routes/memory.py`

**Memory route that `api.py` includes.**

```python
router = APIRouter()

@router.post("/memory/ingest")
async def ingest_memory(
    request: IngestRequest,
    memory_service = Depends(get_memory_service),
):
    record = await memory_service.ingest(
        content=request.content,
        source=request.source,
        tags=request.tags,
    )
    return {"id": record.id, "status": "stored"}

@router.post("/memory/search")
async def search_memory(
    request: SearchRequest,
    memory_service = Depends(get_memory_service),
):
    results = await memory_service.retrieve_context(
        query=request.query,
        language=request.language,
        limit=request.limit,
    )
    return {"results": [asdict(r) for r in results]}

class IngestRequest(BaseModel):
    content: str
    source: str = "api"
    tags: list[str] = []

class SearchRequest(BaseModel):
    query: str
    language: str = "en"
    limit: int = 5
```

### `waggledance/adapters/http/middleware/auth.py`

Port from `backend/auth.py`.

**CRITICAL AUTH OWNERSHIP RULE:**
`WaggleSettings.from_env()` is the ONLY place allowed to generate
a default API key if none is provided. `BearerAuthMiddleware` must
NEVER generate, mutate, or read API keys from env. It only receives
`api_key` via constructor and validates it. If `api_key` is empty
when middleware is constructed, raise `ValueError`.

```python
PUBLIC_PATHS = frozenset({
    "/health", "/ready",
    "/docs", "/openapi.json",
})

class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, api_key: str):
        super().__init__(app)
        if not api_key:
            raise ValueError(
                "api_key must be resolved before middleware setup. "
                "Use WaggleSettings.from_env() to generate a default key."
            )
        self._api_key = api_key

    async def dispatch(self, request, call_next):
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)
        # Check Bearer token
        ...
```

### `waggledance/adapters/http/middleware/rate_limit.py`

Extract rate limiting currently in `backend/routes/chat.py`.
Simple token bucket per IP. No Redis dependency.

```python
class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, requests_per_minute: int = 60): ...
```

---

## PHASE 5 — CONFIG ADAPTER
**Create: `waggledance/adapters/config/`**

### `waggledance/adapters/config/settings_loader.py`

Centralize all environment variable reading.
This is the **ONLY** place that reads `.env` / `os.environ`.

```python
@dataclass
class WaggleSettings:
    # Core
    profile: str = "COTTAGE"
    ollama_host: str = "http://localhost:11434"
    chroma_dir: str = "./chroma_data"
    db_path: str = "./shared_memory.db"

    # Models
    chat_model: str = "phi4-mini"
    learning_model: str = "llama3.2:1b"
    embed_model: str = "nomic-embed-text"

    # Hardware tier (auto-detected if not set)
    hardware_tier: str = "auto"

    # Auth
    api_key: str = ""   # auto-generated by from_env() if empty

    # Limits
    max_agents: int = 75
    round_table_size: int = 6
    hot_cache_size: int = 1000
    night_mode_idle_minutes: int = 30

    # Timeouts (BUG 3 fix: was 30s, caused embed failures under load)
    ollama_timeout_seconds: float = 120.0

    # Night learning (BUG 3 fix: convergence stall threshold)
    night_stall_threshold: int = 10

    @classmethod
    def from_env(cls) -> "WaggleSettings":
        """Load from environment + .env file. ONLY place that reads env.
        ONLY place that generates a default API key if none is provided."""
        settings = cls(...)
        # ... load from env ...
        if not settings.api_key:
            import secrets
            settings.api_key = secrets.token_urlsafe(32)
            import logging
            logging.getLogger(__name__).warning(
                f"No API key configured — auto-generated: {settings.api_key}"
            )
        return settings

    def get(self, key: str, default=None):
        return getattr(self, key, default)

    def get_profile(self) -> str:
        return self.profile

    def get_hardware_tier(self) -> str:
        if self.hardware_tier != "auto":
            return self.hardware_tier
        return self._detect_hardware_tier()

    def _detect_hardware_tier(self) -> str:
        # Read GPU VRAM from nvidia-smi or fallback
        # Return: minimal|light|standard|professional|enterprise
        ...
```

---

## PHASE 6 — BOOTSTRAP (Dependency Injection Container)
**Create: `waggledance/bootstrap/`**

### `waggledance/bootstrap/container.py`

Wires everything together.
One `Container` instance = one running WaggleDance system.
No singletons, no global state.

**CRITICAL RULES:**
- `Container` MUST provide a working `trust_store` — using
  `self.trust_store` without an implementation is FORBIDDEN.
- `memory_service` must receive a real `MemoryRepositoryPort`,
  NEVER `None`.
- All `Depends(...)` providers are defined in `adapters/http/deps.py`.
- `OllamaAdapter` receives `timeout_seconds` from settings (BUG 3 fix).

```python
class Container:
    def __init__(self, settings: WaggleSettings, stub: bool = False):
        self._settings = settings
        self._stub = stub

    # --- Adapters ---

    @cached_property
    def llm(self) -> LLMPort:
        if self._stub:
            return StubLLMAdapter()
        return OllamaAdapter(
            base_url=self._settings.ollama_host,
            default_model=self._settings.chat_model,
            timeout_seconds=self._settings.ollama_timeout_seconds,
        )

    @cached_property
    def vector_store(self) -> VectorStorePort:
        if self._stub:
            return InMemoryVectorStore()
        return ChromaVectorStore(
            persist_directory=self._settings.chroma_dir,
            embedding_model=self._settings.embed_model,
        )

    @cached_property
    def memory_repository(self) -> MemoryRepositoryPort:
        """Stub → InMemoryRepository. Non-stub → ChromaMemoryRepository.
        Using InMemoryRepository in non-stub mode is FORBIDDEN."""
        if self._stub:
            from waggledance.adapters.memory.in_memory_repository import InMemoryRepository
            return InMemoryRepository()
        from waggledance.adapters.memory.chroma_memory_repository import ChromaMemoryRepository
        return ChromaMemoryRepository(
            vector_store=self.vector_store,
            collection="waggle_memory",
        )

    @cached_property
    def trust_store(self) -> TrustStorePort:
        """Always returns a real implementation."""
        from waggledance.adapters.trust.in_memory_trust_store import InMemoryTrustStore
        return InMemoryTrustStore()

    @cached_property
    def shared_memory(self) -> SQLiteSharedMemory:
        return SQLiteSharedMemory(db_path=self._settings.db_path)

    @cached_property
    def hot_cache(self) -> HotCachePort:
        return HotCache(max_size=self._settings.hot_cache_size)

    @cached_property
    def config(self) -> ConfigPort:
        return self._settings

    @cached_property
    def event_bus(self):
        return InMemoryEventBus()

    # --- Core (lazy imports — Agent 1 may still be running) ---

    @cached_property
    def scheduler(self):
        from waggledance.core.orchestration.scheduler import Scheduler
        return Scheduler(config=self.config)

    @cached_property
    def orchestrator(self):
        from waggledance.core.orchestration.orchestrator import Orchestrator
        from waggledance.core.orchestration.round_table import RoundTableEngine
        rt = RoundTableEngine(llm=self.llm, event_bus=self.event_bus)
        return Orchestrator(
            scheduler=self.scheduler,
            round_table=rt,
            memory=self.memory_repository,
            vector_store=self.vector_store,
            llm=self.llm,
            trust_store=self.trust_store,
            event_bus=self.event_bus,
            config=self.config,
            agents=[],  # loaded by bootstrap_service
        )

    # --- Application Services (lazy imports) ---

    @cached_property
    def memory_service(self):
        from waggledance.application.services.memory_service import MemoryService
        return MemoryService(
            vector_store=self.vector_store,
            memory=self.memory_repository,  # NEVER None
            event_bus=self.event_bus,
        )

    @cached_property
    def chat_service(self):
        from waggledance.application.services.chat_service import ChatService
        from waggledance.core.orchestration.routing_policy import select_route
        return ChatService(
            orchestrator=self.orchestrator,
            memory_service=self.memory_service,
            hot_cache=self.hot_cache,
            routing_policy_fn=select_route,
            config=self.config,
        )

    @cached_property
    def learning_service(self):
        from waggledance.application.services.learning_service import LearningService
        return LearningService(
            orchestrator=self.orchestrator,
            memory_service=self.memory_service,
            llm=self.llm,
            event_bus=self.event_bus,
            stall_threshold=self._settings.night_stall_threshold,
        )

    @cached_property
    def readiness_service(self):
        from waggledance.application.services.readiness_service import ReadinessService
        return ReadinessService(
            orchestrator=self.orchestrator,
            vector_store=self.vector_store,
            llm=self.llm,
        )

    def build_app(self):
        from waggledance.adapters.http.api import create_app
        return create_app(self)
```

### `waggledance/bootstrap/event_bus.py`

Simple in-process event bus. No external deps.

```python
class InMemoryEventBus:  # implements EventBusPort
    def __init__(self):
        self._handlers: dict[EventType, list[Callable]] = {}
        self._failure_counts: dict[EventType, int] = {}

    async def publish(self, event: DomainEvent) -> None:
        for handler in self._handlers.get(event.type, []):
            try:
                await handler(event) if asyncio.iscoroutinefunction(handler) \
                    else handler(event)
            except Exception as e:
                self._failure_counts[event.type] = \
                    self._failure_counts.get(event.type, 0) + 1
                logging.getLogger(__name__).error(
                    f"Event handler error for {event.type}: {e}",
                    exc_info=True,
                )

    def subscribe(self, event_type: EventType, handler: Callable) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    def get_failure_count(self, event_type: EventType) -> int:
        """Observable failure count for testing and monitoring."""
        return self._failure_counts.get(event_type, 0)
```

---

## PHASE 7 — UNIT TESTS (mock-only, zero I/O)
**Create: `tests/unit/`**

### `tests/unit/conftest.py`
```python
import pytest
from waggledance.adapters.llm.stub_llm_adapter import StubLLMAdapter
from waggledance.adapters.memory.in_memory_vector_store import InMemoryVectorStore
from waggledance.adapters.memory.in_memory_repository import InMemoryRepository
from waggledance.adapters.memory.hot_cache import HotCache
from waggledance.adapters.trust.in_memory_trust_store import InMemoryTrustStore
from waggledance.bootstrap.event_bus import InMemoryEventBus

@pytest.fixture
def stub_llm(): return StubLLMAdapter()

@pytest.fixture
def stub_vector_store(): return InMemoryVectorStore()

@pytest.fixture
def stub_memory_repo(): return InMemoryRepository()

@pytest.fixture
def stub_trust_store(): return InMemoryTrustStore()

@pytest.fixture
def hot_cache(): return HotCache(max_size=100)

@pytest.fixture
def event_bus(): return InMemoryEventBus()
```

### `tests/unit/test_hot_cache.py`
Write at least **10 tests** covering:
- basic get/set
- TTL expiration
- LRU eviction at max_size
- `evict_expired()` returns correct count
- `stats()` accuracy
- thread safety documented

### `tests/unit/test_rate_limit_middleware.py`
Write at least **6 tests** covering:
- allows requests under limit
- blocks at limit
- resets after window
- different IPs tracked separately

### `tests/unit/test_stub_llm_adapter.py`
Write at least **5 tests**:
- `is_available()` returns `True`
- `generate()` returns non-empty string
- `get_active_model()` returns `"stub-v1"`
- time-related prompt returns time info
- does not raise on empty prompt

### `tests/unit/test_in_memory_vector_store.py`
Write at least **8 tests**:
- upsert + query returns the item
- n_results limit respected
- different collections are isolated
- `is_ready()` returns `True`
- query on empty store returns `[]`
- upsert overwrites on same id
- metadata is preserved
- where filter (basic substring) works

### `tests/unit/test_in_memory_repository.py`
Write at least **6 tests**:
- store + search returns the record
- search with tags filters correctly
- store_correction stores entry
- search on empty returns `[]`
- limit parameter respected
- language parameter does not crash

### `tests/unit/test_chroma_memory_repository.py`

**NEW — tests for production `MemoryRepositoryPort` implementation.**
Use a mocked `VectorStorePort` (no real ChromaDB).

Write at least **6 tests**:
- `store()` calls `vector_store.upsert()` with correct collection
- `search()` calls `vector_store.query()` and converts results to `MemoryRecord`
- `store_correction()` upserts to `corrections` collection
- metadata fields (source, confidence, tags, agent_id, created_at) are preserved through store→search roundtrip
- `search()` with tags passes `where` filter to vector store
- empty query results return empty `list[MemoryRecord]`

### `tests/unit/test_in_memory_trust_store.py`
Write at least **6 tests**:
- `get_trust` on unknown agent returns `None`
- `update_trust` stores and returns `AgentTrust`
- `get_ranking` returns sorted by composite score
- `get_ranking` limit is respected
- composite score is between 0.0 and 1.0
- updating same agent overwrites previous trust

### `tests/unit/test_settings_loader.py`
Write at least **8 tests** using `monkeypatch`:
- default values are correct
- `WAGGLE_PROFILE` env overrides profile
- `OLLAMA_HOST` env overrides ollama_host
- api_key auto-generation works
- hardware tier detection runs without crashing
- `from_env()` does not raise on clean environment
- `ollama_timeout_seconds` default is 120.0 (BUG 3 regression)
- `night_stall_threshold` default is 10 (BUG 3 regression)

### `tests/unit/test_event_bus.py`
Write at least **8 tests**:
- subscribe + publish triggers handler
- multiple subscribers all called
- handler error does not crash publisher
- async handlers work
- sync handlers work
- unregistered event type publishes silently
- failing handler increments `get_failure_count()` for that event type
- `get_failure_count()` returns 0 for event types with no failures

### `tests/unit/test_ollama_timeout.py`

**NEW — regression test for BUG 3 (Ollama timeout).**

Write at least **3 tests**:
- Default timeout is ≥120 seconds
- Custom timeout is passed to httpx client
- Circuit breaker activates after OPEN_THRESHOLD failures

---

## PHASE 8 — VERIFY EVERYTHING

Run these commands and fix any failures before finishing:

```bash
# Imports work
python -c "
from waggledance.adapters.llm.ollama_adapter import OllamaAdapter
from waggledance.adapters.llm.stub_llm_adapter import StubLLMAdapter
from waggledance.adapters.memory.in_memory_vector_store import InMemoryVectorStore
from waggledance.adapters.memory.in_memory_repository import InMemoryRepository
from waggledance.adapters.memory.hot_cache import HotCache
from waggledance.adapters.trust.in_memory_trust_store import InMemoryTrustStore
from waggledance.adapters.config.settings_loader import WaggleSettings
from waggledance.adapters.http.deps import get_chat_service, get_readiness_service, get_memory_service
from waggledance.bootstrap.event_bus import InMemoryEventBus
from waggledance.bootstrap.container import Container
print('All imports OK')
"

# Unit tests pass (zero I/O, should be instant)
python -m pytest tests/unit/ -v

# Stub mode container builds without error
python -c "
from waggledance.adapters.config.settings_loader import WaggleSettings
from waggledance.bootstrap.container import Container
s = WaggleSettings.from_env()
c = Container(settings=s, stub=True)
print('LLM:', c.llm.get_active_model())
print('Trust store:', type(c.trust_store).__name__)
print('Memory repo:', type(c.memory_repository).__name__)
print('Hot cache:', type(c.hot_cache).__name__)
assert s.ollama_timeout_seconds >= 120.0, 'Timeout must be >= 120s (BUG 3)'
print('Stub container OK')
"
```

---

## RULES

1. Read existing source files before porting. Never guess.
2. Do **not** modify files in: `core/`, `hivemind.py`, `main.py`, `backend/`, `memory/`, `consciousness.py`
3. All adapter classes must have port listed in a comment: `# implements LLMPort`
4. No `try/except` that silently swallows errors.
5. If Agent 1's `waggledance/core/` doesn't exist yet when running verification, use `TYPE_CHECKING` imports or lazy imports in `container.py` so verification still passes.
6. **`BearerAuthMiddleware` must NOT read env directly. It receives `api_key` from `WaggleSettings`.**
7. **`StaticFiles` mount must be conditional; missing `dashboard/dist` must NOT crash.**
8. **`Container` must provide working `trust_store` and `memory_repository`; `None` values are forbidden.**
9. **`OllamaAdapter` default timeout must be ≥120s (not 30s).**
10. **No fire-and-forget `asyncio.create_task()` — all tasks must have error handling.**
11. Write **`MIGRATION_STATUS.md`** at repo root when done:
    - What you created
    - What existing files are now superseded by new adapters
    - What still needs wiring in integration phase
    - Any port contract mismatches noticed
12. **Write `SPRINT_REPORT_AGENT2.md`** at repo root with:
    - Every file created (full paths)
    - Every directory created
    - Every test file created
    - Any contract mismatch encountered
    - Any deliberate stub left in place
    - Any risk that must be handled in integration
13. **`BearerAuthMiddleware` must NEVER generate an API key. If `api_key` is empty, raise `ValueError`.**
14. **`Container` non-stub mode must use `ChromaMemoryRepository`, NOT `InMemoryRepository`.**
15. Before finishing, run these additional checks:
    ```bash
    # No TODOs or stubs in implementation files:
    grep -rn 'TODO\|FIXME' waggledance/adapters/ waggledance/bootstrap/ --include='*.py' && echo "FAIL" && exit 1 || echo "OK"
    # No untracked create_task:
    grep -rn 'asyncio.create_task' waggledance/adapters/ waggledance/bootstrap/ --include='*.py' | grep -v '_track_task\|TaskGroup\|add_done_callback' && echo "FAIL" && exit 1 || echo "OK"
    # Compiles:
    python -m compileall waggledance/adapters/ waggledance/bootstrap/
    ```


---
---

# ═══════════════════════════════════════════════════════════════
# INTEGRATION PHASE (v2.3)
# ═══════════════════════════════════════════════════════════════

> **Aja tämä VASTA kun Agent 1 ja Agent 2 ovat molemmat valmiit.**
> **Aja tämä: Claude Code (Opus 4)**

---

## INTEGRATION ENTRY GATE

**Integration Phase MUST NOT start unless ALL of these are true.**
Check each one before proceeding. If any fails, STOP and report.

```bash
# 1. Contract docs exist
test -f PORT_CONTRACTS.md && test -f STATE_OWNERSHIP.md && test -f ACCEPTANCE_CRITERIA.md \
    && echo "OK: contracts exist" || echo "FAIL: missing contract docs — run Phase 0 first"

# 2. Agent sprint reports exist
test -f SPRINT_REPORT_AGENT1.md && test -f SPRINT_REPORT_AGENT2.md \
    && echo "OK: sprint reports exist" || echo "FAIL: agents not finished"

# 3. All tests green
python -m pytest tests/unit/ tests/unit_core/ tests/unit_app/ tests/contracts/ -v \
    && echo "OK: all tests green" || echo "FAIL: tests failing — do not proceed"

# 4. No unresolved contract mismatches
if [ -f CONTRACT_MISMATCHES.md ]; then
    echo "WARNING: CONTRACT_MISMATCHES.md exists — resolve all items before proceeding"
    cat CONTRACT_MISMATCHES.md
else
    echo "OK: no contract mismatches"
fi

# 5. No TODOs in implementation
TODOS=$(grep -rn 'TODO\|FIXME' waggledance/ --include='*.py' | grep -v __pycache__ | wc -l)
if [ "$TODOS" -gt 0 ]; then
    echo "FAIL: $TODOS unresolved TODO/FIXME found"
    grep -rn 'TODO\|FIXME' waggledance/ --include='*.py' | grep -v __pycache__
else
    echo "OK: no TODOs"
fi

# 6. Code compiles
python -m compileall waggledance/ && echo "OK: compiles" || echo "FAIL: compilation errors"
```

**If ANY check shows FAIL, do not proceed.** Fix the issue or
report back that the entry gate did not pass.

---

Both Agent 1 (`waggledance/core/`, `waggledance/application/`) and
Agent 2 (`waggledance/adapters/`, `waggledance/bootstrap/`, `tests/unit/`)
have completed their work.

Your job is the integration phase.

---

## STEP 1 — Read context and validate contracts

Read these files first:
- `NEW_ARCHITECTURE.md`
- `MIGRATION_STATUS.md`
- `PORT_CONTRACTS.md`
- `STATE_OWNERSHIP.md`
- `ACCEPTANCE_CRITERIA.md`

**Validate contracts before proceeding:**

1. Check that every port implementation in `waggledance/adapters/`
   matches the method signatures in `PORT_CONTRACTS.md`. If not,
   fix the implementation (not the contract).

2. Check that every persistent write in `waggledance/core/` and
   `waggledance/application/` has the correct owner per
   `STATE_OWNERSHIP.md`. If not, fix the implementation.

3. Check that `routing_policy.select_route()` only returns
   route types from `ALLOWED_ROUTE_TYPES`. If not, fix it.

4. Check that no code outside `WaggleSettings` reads `os.environ`
   or `os.getenv`. If found, fix it.

5. Check that no `asyncio.create_task()` is fire-and-forget.
   All must use `_track_task()` or `TaskGroup`. If found, fix it.

If `CONTRACT_MISMATCHES.md` exists, read it and resolve each
mismatch by fixing the implementation.

---

## STEP 2 — Run unit tests and fix failures

```bash
# Agent 2 tests
python -m pytest tests/unit/ -v

# Agent 1 tests
python -m pytest tests/unit_core/ -v
python -m pytest tests/unit_app/ -v

# Contract tests
python -m pytest tests/contracts/ -v
```

Fix any failures before proceeding.

---

## STEP 3 — Stub smoke test

```bash
python -c "
from waggledance.adapters.config.settings_loader import WaggleSettings
from waggledance.bootstrap.container import Container
import asyncio

s = WaggleSettings.from_env()
c = Container(settings=s, stub=True)

# Verify critical components are not None
assert c.trust_store is not None, 'trust_store is None!'
assert c.memory_repository is not None, 'memory_repository is None!'
assert c.hot_cache is not None, 'hot_cache is None!'
assert s.ollama_timeout_seconds >= 120.0, 'Timeout too low (BUG 3)'

app = c.build_app()
print('App created:', app.title)
print('Trust store:', type(c.trust_store).__name__)
print('Memory repo:', type(c.memory_repository).__name__)
print('Hot cache:', type(c.hot_cache).__name__)
print('Timeout:', s.ollama_timeout_seconds)
"
```

Fix any failures before proceeding.

---

## STEP 4 — Create clean entry point

Create `waggledance/adapters/cli/start_runtime.py`.

This replaces `start.py` as the clean entry point:
- Imports `Container`, builds the app, runs uvicorn
- **No** `ensure_dependencies()`
- **No** `subprocess.Popen` for the backend
- Dashboard stays as separate npm process

```python
# waggledance/adapters/cli/start_runtime.py

import asyncio
import uvicorn
from waggledance.adapters.config.settings_loader import WaggleSettings
from waggledance.bootstrap.container import Container

def main():
    settings = WaggleSettings.from_env()
    stub = "--stub" in __import__("sys").argv
    container = Container(settings=settings, stub=stub)
    app = container.build_app()
    uvicorn.run(app, host="0.0.0.0", port=8000)

if __name__ == "__main__":
    main()
```

---

## STEP 5 — Update pyproject.toml

Add to `pyproject.toml`:

```toml
[project]
dependencies = [
    # List every package from requirements.txt here
]

[project.scripts]
waggledance = "waggledance.adapters.cli.start_runtime:main"
```

---

## STEP 6 — Run the full existing test suite

```bash
python tools/waggle_backup.py --tests-only
```

All 72 suites must remain GREEN. If any fail, fix the regression
before proceeding. Do NOT modify the existing tests — fix the
integration instead.

---

## STEP 7 — Verify old and new entrypoints

Both old and new entrypoints must work. Run non-destructive smoke
tests for each:

```bash
# Old entrypoints — must not crash
python main.py --help 2>&1 || python -c "
import main; print('main.py importable')
" 2>&1 | head -5

python start.py --help 2>&1 || python -c "
import importlib.util
spec = importlib.util.spec_from_file_location('start', 'start.py')
print('start.py loadable')
" 2>&1 | head -5

# New entrypoint — stub boot smoke test
timeout 10 python -m waggledance.adapters.cli.start_runtime --stub 2>&1 || true
```

If any old entrypoint breaks, the issue is in the integration
wiring. Fix the new code, not the old.

---

## STEP 8 — Production bug regression verification

Verify that all three production bugs are addressed:

```bash
echo "=== BUG 1: No type mixing in Orchestrator ==="
grep -rn "self\.micro_model\s*=" waggledance/ --include='*.py' \
    | grep -v __pycache__ \
    || echo "OK: no micro_model attribute reassignment"

echo "=== BUG 2: No fire-and-forget tasks ==="
grep -rn "create_task" waggledance/ --include='*.py' \
    | grep -v __pycache__ \
    | grep -v "_track_task\|TaskGroup\|add_done_callback" \
    && echo "FAIL: found untracked create_task" \
    || echo "OK: all tasks tracked"

echo "=== BUG 3: Timeout >= 120s ==="
python -c "
from waggledance.adapters.config.settings_loader import WaggleSettings
s = WaggleSettings.from_env()
assert s.ollama_timeout_seconds >= 120.0, f'FAIL: {s.ollama_timeout_seconds}'
print('OK: timeout =', s.ollama_timeout_seconds)
"

echo "=== BUG 3b: Stall detection ==="
python -c "
from waggledance.adapters.config.settings_loader import WaggleSettings
s = WaggleSettings.from_env()
assert s.night_stall_threshold >= 1, f'FAIL: {s.night_stall_threshold}'
print('OK: stall threshold =', s.night_stall_threshold)
"
```

---

## STEP 9 — Write INTEGRATION_COMPLETE.md

Create `INTEGRATION_COMPLETE.md` at repo root summarizing:

1. **What was migrated** — list of new files and what old code they replace
2. **Old files now superseded** — files that can be deleted once verified
3. **Recommended deletion list** — which files to remove in the next PR
4. **Remaining tech debt** — what still needs work, including:
   - MicroModel port and adapter (fix `hivemind.py:1365` type mismatch first)
   - Rule engine port and adapter
   - Sensor/MQTT adapter
   - SQLite-backed `TrustStorePort` for persistence
   - SQLite-backed `MemoryRepositoryPort` (current `ChromaMemoryRepository` delegates to `VectorStorePort`)
5. **Production bugs addressed** — BUG 1 (type safety), BUG 2 (task tracking), BUG 3 (timeout + stall)
6. **Verification checklist** — steps to confirm the new stack is fully functional

---

## STEP 10 — Final acceptance check

Run through the `ACCEPTANCE_CRITERIA.md` checklist. For each item,
verify it passes and check it off. If any item fails, fix it
before finishing.

```bash
# Quick automated sweep
echo "=== Unit tests ==="
python -m pytest tests/unit/ tests/unit_core/ tests/unit_app/ tests/contracts/ -v

echo "=== Stub container ==="
python -c "
from waggledance.adapters.config.settings_loader import WaggleSettings
from waggledance.bootstrap.container import Container
c = Container(settings=WaggleSettings.from_env(), stub=True)
app = c.build_app()
print('OK: stub container builds')
"

echo "=== Old test suite ==="
python tools/waggle_backup.py --tests-only

echo "=== Env read audit ==="
grep -rn 'os\.environ\|os\.getenv\|load_dotenv' waggledance/ \
    --include='*.py' \
    | grep -v settings_loader.py \
    | grep -v __pycache__ \
    || echo 'OK: no env reads outside settings_loader'

echo "=== Forbidden imports in core/application ==="
grep -rn 'import fastapi\|import chromadb\|import httpx\|import uvicorn\|import dotenv\|import aiosqlite' \
    waggledance/core/ waggledance/application/ \
    --include='*.py' \
    | grep -v __pycache__ \
    || echo 'OK: no forbidden imports'

echo "=== Code compiles ==="
python -m compileall waggledance/

echo "=== No TODOs in implementation ==="
grep -rn 'TODO\|FIXME' waggledance/ --include='*.py' \
    | grep -v __pycache__ \
    || echo 'OK: no unresolved TODOs'

echo "=== No raw create_task ==="
grep -rn 'asyncio.create_task' waggledance/ --include='*.py' \
    | grep -v __pycache__ \
    | grep -v '_track_task\|TaskGroup\|add_done_callback' \
    && echo 'FAIL: raw create_task found' \
    || echo 'OK: all tasks tracked'

echo "=== Non-stub container smoke ==="
python -c "
from waggledance.adapters.config.settings_loader import WaggleSettings
from waggledance.bootstrap.container import Container
s = WaggleSettings.from_env()
c = Container(settings=s, stub=False)
repo = c.memory_repository
assert type(repo).__name__ != 'InMemoryRepository', \
    f'FAIL: non-stub uses {type(repo).__name__}'
print('OK: non-stub memory_repository is', type(repo).__name__)
"

echo "=== Event bus failure counter ==="
python -c "
from waggledance.bootstrap.event_bus import InMemoryEventBus
from waggledance.core.domain.events import EventType
bus = InMemoryEventBus()
assert hasattr(bus, 'get_failure_count'), 'FAIL: no get_failure_count method'
assert bus.get_failure_count(EventType.TASK_RECEIVED) == 0
print('OK: event bus has failure counter')
"
```

---

## RULES

1. Do not delete any existing files. Only create and modify.
2. If `tools/waggle_backup.py --tests-only` fails, the issue
   is in the integration wiring — fix the new code, not the old.
3. The old entrypoint `start.py` and `main.py` must still work
   after this step. The new `start_runtime.py` is additive.
4. No new persistent write path may be created without updating
   `STATE_OWNERSHIP.md`.
5. All contract mismatches must be resolved — implementations
   conform to `PORT_CONTRACTS.md`, never the other way around.
6. All three production bugs (type mixing, untracked tasks,
   timeout/stall) must be verified as fixed before completion.
