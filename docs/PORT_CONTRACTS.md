# Port Contracts — WaggleDance Refactor Sprint

**Version:** 1.0.0
**Created:** 2026-03-14
**Authority:** WAGGLEDANCE_REFACTOR_MASTER_v2.3.md

This is the single source of truth for all shared interfaces in the new architecture.
Both Agent 1 (core/application) and Agent 2 (adapters/bootstrap) must conform exactly.

---

## 1. Ports — Method Signatures

### 1.1 LLMPort

```python
from typing import Protocol

class LLMPort(Protocol):
    async def generate(
        self,
        prompt: str,
        model: str = "default",
        temperature: float = 0.7,
        max_tokens: int = 1000,
    ) -> str: ...

    async def is_available(self) -> bool: ...

    def get_active_model(self) -> str: ...
```

**Design decision:** `generate()` returns `str` (plain text), not `LLMResponse`.
The adapter internally handles retry, circuit breaker, and error mapping.
If the LLM is unavailable or returns an error, `generate()` returns `""` (empty string).
Callers must check for empty responses.

**Traced from:** `core/llm_provider.py` — `LLMProvider.generate()` returns `LLMResponse.content`.
The new port strips the wrapper; adapter owns error handling.

---

### 1.2 MemoryRepositoryPort

Replaces the old combined `MemoryPort`. Hot cache is a separate port (1.3).

```python
from typing import Protocol
from waggledance.core.domain.memory_record import MemoryRecord

class MemoryRepositoryPort(Protocol):
    async def search(
        self,
        query: str,
        limit: int = 5,
        language: str = "en",
        tags: list[str] | None = None,
    ) -> list[MemoryRecord]: ...

    async def store(self, record: MemoryRecord) -> None: ...

    async def store_correction(
        self,
        query: str,
        wrong: str,
        correct: str,
    ) -> None: ...
```

**Design decision:** `search()` combines vector similarity + keyword filtering.
The adapter decides whether to use ChromaDB, FAISS, or SQLite internally.
`store_correction()` is separate from `store()` because corrections have
different persistence semantics (they update existing knowledge, not append).

**Traced from:** `core/memory_engine.py` — `MemoryEngine.search()`, `.store()`, `.store_correction()`.

---

### 1.3 HotCachePort

```python
from typing import Protocol

class HotCachePort(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str, ttl: int | None = None) -> None: ...
    def evict_expired(self) -> int: ...
    def stats(self) -> dict: ...
```

**Design decision:** Synchronous interface — hot cache is always in-memory,
no I/O needed. `stats()` returns `{"size": int, "hits": int, "misses": int, "evictions": int}`.

**Traced from:** `core/memory_engine.py` — `HotCache` class (LRU dict with TTL).

---

### 1.4 VectorStorePort

```python
from typing import Protocol

class VectorStorePort(Protocol):
    async def upsert(
        self,
        id: str,
        text: str,
        metadata: dict,
        collection: str = "default",
    ) -> None: ...

    async def query(
        self,
        text: str,
        n_results: int = 5,
        collection: str = "default",
        where: dict | None = None,
    ) -> list[dict]: ...

    async def is_ready(self) -> bool: ...
```

**Design decision:** `query()` returns `list[dict]` with keys
`{"id": str, "text": str, "metadata": dict, "score": float}`.
The adapter handles embedding internally (calls Ollama embed).

**Traced from:** `core/memory_engine.py` — ChromaDB collection operations.
Also `core/faiss_store.py` — `FaissCollection.search()` returns `SearchResult`.

---

### 1.5 TrustStorePort

```python
from typing import Protocol
from waggledance.core.domain.trust_score import AgentTrust, TrustSignals

class TrustStorePort(Protocol):
    async def get_trust(self, agent_id: str) -> AgentTrust | None: ...

    async def update_trust(
        self,
        agent_id: str,
        signals: TrustSignals,
    ) -> AgentTrust: ...

    async def get_ranking(self, limit: int = 20) -> list[AgentTrust]: ...
```

**Design decision:** At least one implementation required: `InMemoryTrustStore`.
Trust scores are computed as weighted composite of 6 signals.

**Traced from:** `core/swarm_scheduler.py` — `AgentScore` fields map to `TrustSignals`.
`success_score → validation_rate`, `reliability_score → correction_rate`, etc.

---

### 1.6 EventBusPort

```python
from typing import Protocol, Callable
from waggledance.core.domain.events import DomainEvent, EventType

class EventBusPort(Protocol):
    async def publish(self, event: DomainEvent) -> None: ...

    def subscribe(
        self,
        event_type: EventType,
        handler: Callable,
    ) -> None: ...
```

**Design decision:** `publish()` is async to allow handlers to do async work.
Handler failures must be logged with full traceback, increment per-type failure
counter, and never block the publisher (max 5s timeout per handler).

**Failure policy (applies globally):**
- Log with `logger.error(..., exc_info=True)`
- Increment `failure_count[event_type]`
- Never swallow silently
- Never block main request path

---

### 1.7 ConfigPort

```python
from typing import Protocol, Any

class ConfigPort(Protocol):
    def get(self, key: str, default: Any = None) -> Any: ...
    def get_profile(self) -> str: ...
    def get_hardware_tier(self) -> str: ...
```

**Design decision:** `get()` supports dotted keys (`"llm.model"`, `"swarm.top_k"`).
`get_profile()` returns uppercase: `"GADGET"`, `"COTTAGE"`, `"HOME"`, `"FACTORY"`.
`get_hardware_tier()` returns: `"minimal"`, `"light"`, `"standard"`, `"professional"`, `"enterprise"`.

**Traced from:** `configs/settings.yaml` — `profile: cottage`.
`core/elastic_scaler.py` — hardware tier detection.

---

### 1.8 SensorPort

```python
from typing import Protocol

class SensorPort(Protocol):
    async def get_latest(self, sensor_id: str) -> dict | None: ...
    async def get_all_active(self) -> list[dict]: ...
```

**Design decision:** Stub-only in this sprint. No real adapter needed.
Returns empty data structures.

**Traced from:** `integrations/sensor_hub.py` — `SensorHub.get_status()`.

---

## 2. DTOs — Field Definitions

All DTOs are `@dataclass` classes. Field order is canonical.

### 2.1 Core Domain DTOs

```python
# waggledance/core/domain/agent.py
@dataclass
class AgentDefinition:
    id: str                        # unique agent identifier
    name: str                      # English display name
    domain: str                    # knowledge domain (e.g., "energy", "monitoring")
    tags: list[str]                # routing keywords
    skills: list[str]              # DECISION_METRICS keys from YAML
    trust_level: int               # 0-4: NOVICE, APPRENTICE, JOURNEYMAN, EXPERT, MASTER
    specialization_score: float    # 0.0-1.0, from calibration
    active: bool                   # currently spawned
    profile: str                   # "GADGET"|"COTTAGE"|"HOME"|"FACTORY"|"ALL"

@dataclass
class AgentResult:
    agent_id: str
    response: str
    confidence: float              # 0.0-1.0
    latency_ms: float
    source: str                    # MUST be one of ALLOWED_ROUTE_TYPES
    metadata: dict                 # free-form, adapter-specific
```

```python
# waggledance/core/domain/task.py
@dataclass
class TaskRequest:
    id: str                        # UUID
    query: str                     # user's original message
    language: str                  # "fi"|"en"|"auto"
    profile: str                   # "COTTAGE" etc.
    user_id: str | None
    context: list[dict]            # previous conversation turns
    timestamp: float               # time.time()

@dataclass
class TaskRoute:
    route_type: str                # MUST be one of ALLOWED_ROUTE_TYPES
    selected_agents: list[str]     # agent IDs
    confidence: float              # 0.0-1.0
    routing_latency_ms: float
```

```python
# waggledance/core/domain/memory_record.py
@dataclass
class MemoryRecord:
    id: str                        # UUID
    content: str                   # English content
    content_fi: str | None         # Finnish translation (optional)
    source: str                    # origin identifier
    confidence: float              # 0.0-1.0
    tags: list[str]
    agent_id: str | None
    created_at: float              # time.time()
    ttl_seconds: int | None        # None = permanent
```

```python
# waggledance/core/domain/trust_score.py
@dataclass
class TrustSignals:
    hallucination_rate: float      # 0.0-1.0, lower is better
    validation_rate: float         # 0.0-1.0, higher is better
    consensus_agreement: float     # 0.0-1.0
    correction_rate: float         # 0.0-1.0, lower is better
    fact_production_rate: float    # facts/hour
    freshness_score: float         # 0.0-1.0, recency weight

@dataclass
class AgentTrust:
    agent_id: str
    composite_score: float         # 0.0-1.0
    signals: TrustSignals
    updated_at: float              # time.time()
```

```python
# waggledance/core/domain/events.py
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

### 2.2 Orchestration DTOs

```python
# waggledance/core/orchestration/scheduler.py
@dataclass
class SchedulerState:
    pheromone_scores: dict[str, float]   # agent_id -> 0.0-1.0
    usage_counts: dict[str, int]         # agent_id -> total uses
    recent_successes: dict[str, float]   # agent_id -> last success timestamp

# waggledance/core/orchestration/routing_policy.py
@dataclass
class RoutingFeatures:
    query_length: int
    language: str
    has_hot_cache_hit: bool
    memory_score: float                  # best memory similarity score
    is_time_query: bool
    is_system_query: bool
    matched_keywords: list[str]
    profile: str

# waggledance/core/orchestration/round_table.py
@dataclass
class ConsensusResult:
    consensus: str
    confidence: float
    participating_agents: list[str]
    dissenting_views: list[str]
    latency_ms: float
```

### 2.3 Application DTOs

```python
# waggledance/application/dto/chat_dto.py
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
    source: str                          # which route served this
    confidence: float
    latency_ms: float
    agent_id: str | None
    round_table: bool
    cached: bool

# waggledance/application/dto/readiness_dto.py
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

---

## 3. Route Types

```python
ALLOWED_ROUTE_TYPES = frozenset({"hotcache", "memory", "llm", "swarm"})
```

**NOT implemented in this sprint:**
- `micromodel` — broken in production (see Known Issues #1)
- `rules` — deferred to future sprint

These may appear in comments or documentation as future work only.
No code path may return or accept these values.

---

## 4. Collection Names

```
ChromaDB collections: waggle_memory, swarm_facts, corrections, episodes
SQLite database:      shared_memory.db
```

**Traced from:**
- `core/memory_engine.py` — uses `waggle_memory` as primary collection
- `memory/shared_memory.py` — `shared_memory.db` with 7 tables

---

## 5. What May Be Stubbed

| Component | Stub Allowed | Notes |
|-----------|-------------|-------|
| `SensorPort` | Yes | No real adapter needed. Stub returns empty data. |
| Night learning | Partial | `LearningService` created, `run_night_tick()` may return empty stats |
| `MicroModel` routing | Skipped | Broken in production; excluded entirely from routing |
| `Rules` routing | Skipped | Not implemented in this sprint |

---

## 6. What Is NOT Implemented Yet

| Component | Reason | Future Sprint |
|-----------|--------|---------------|
| MicroModel inference port + adapter | P0 bug: `hivemind.py:1365` type mismatch | Fix type safety first |
| Rule engine port + adapter | Deferred | Future sprint |
| MQTT / Home Assistant sensor adapter | Stub only in this sprint | Sensor sprint |
| Whisper STT / Piper TTS voice adapters | Stub only | Voice sprint |
| Dashboard React build integration | Separate concern | Dashboard sprint |
| Multi-node distribution | Not designed yet | Scaling sprint |

---

## 7. Known Production Bugs — Appendix

| # | Bug | Severity | Root Cause | Fix in New Architecture |
|---|-----|----------|------------|------------------------|
| 1 | `PatternMatchEngine.is_training_due` — 817 errors/night | P0 | `hivemind.py:1365` overwrites `self.micro_model` (`MicroModelOrchestrator`) with `PatternMatchEngine` during cache warming. `NightModeController` expects `MicroModelOrchestrator` which has `is_training_due()`. | New `Orchestrator` never mixes types. `micromodel` route excluded from v1. Separate clearly-typed fields for different components. |
| 2 | `Task exception never retrieved` — 27/night | P1 | `asyncio.create_task()` without error handling in orchestration. Tasks fail silently. | All tasks use `_track_task()` or `asyncio.TaskGroup`. Fire-and-forget FORBIDDEN. Failed tasks logged with full traceback. |
| 3 | Ollama embed timeout (30s) under load | P2 | Single timeout for both generate and embed calls. Embed calls timeout when LLM is busy with night learning. | `OllamaAdapter` uses 120s default timeout. Embed-specific timeout configurable. |
| 4 | Night learning convergence stall | P2 | No auto-reset when fact generation produces 0 new facts for many cycles. `self_generate` converges and stops producing novelty. | `LearningService.run_night_tick()` tracks consecutive empty cycles. After 10 empty cycles: reset topic selection, emit `NIGHT_STALL_DETECTED` event, return `{"stall_detected": True}`. |

---

*Generated from existing codebase analysis. Every decision traced to source files.*
