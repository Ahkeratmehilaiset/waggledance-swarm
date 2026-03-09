# WaggleDance Architecture

*Arkkitehtuuri — System Overview*

## High-Level Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                        CLIENT LAYER                              │
│  Browser (React Dashboard :5173)  │  Voice (Whisper/Piper)       │
│  WebSocket (real-time feed)       │  MQTT Sensors                │
└──────────────────┬───────────────────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────────────────┐
│                     FastAPI  (port 8000)                          │
│  REST endpoints (~45)  │  WebSocket /ws  │  Health/Ready probes  │
│  Rate limiting (20/min)│  CORS (GET/POST)│  Input validation     │
└──────────────────┬───────────────────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────────────────┐
│                    SMARTROUTER                                    │
│  Layer 0: HotCache         (~0.5ms, RAM)                         │
│  Layer 1: Native-lang idx  (~18ms, skip translation)             │
│  Layer 2: YAML eval_questions (confidence match)                 │
│  Layer 3: Full LLM pipeline (55ms–3000ms)                        │
└──────────────────┬───────────────────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────────────────┐
│                    HIVEMIND ORCHESTRATOR                          │
│                    (hivemind.py ~1382 + 4 controllers)            │
│                                                                   │
│  ┌─────────────┐ ┌──────────────┐ ┌────────────────┐            │
│  │ 75 Agents   │ │ Round Table  │ │ Night Mode     │            │
│  │ (YAML defs) │ │ (6-agent     │ │ (idle learning │            │
│  │ 5 trust lvl │ │  consensus)  │ │  + enrichment) │            │
│  └─────────────┘ └──────────────┘ └────────────────┘            │
│                                                                   │
│  ┌─────────────┐ ┌──────────────┐ ┌────────────────┐            │
│  │ Priority    │ │ Circuit      │ │ Seasonal       │            │
│  │ Lock (chat  │ │ Breaker      │ │ Guard          │            │
│  │ > background│ │ (auto-heal)  │ │ (month rules)  │            │
│  └─────────────┘ └──────────────┘ └────────────────┘            │
└──────────────────┬───────────────────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────────────────┐
│                    MEMORY & KNOWLEDGE                             │
│                                                                   │
│  ┌──────────────────┐  ┌─────────────────┐  ┌────────────────┐  │
│  │ MemoryEngine     │  │ MAGMA L1-L5     │  │ Cognitive      │  │
│  │ (ChromaDB,       │  │ (audit, replay, │  │ Graph          │  │
│  │  bilingual FI+EN,│  │  write proxy,   │  │ (NetworkX,     │  │
│  │  dual embedding) │  │  overlays,      │  │  causal edges, │  │
│  │                  │  │  trust engine)  │  │  JSON persist) │  │
│  └──────────────────┘  └─────────────────┘  └────────────────┘  │
│                                                                   │
│  ┌──────────────────┐  ┌─────────────────┐                      │
│  │ MicroModel       │  │ Training        │                      │
│  │ V1 (pattern,0.01)│  │ Collector       │                      │
│  │ V2 (neural, 1ms) │  │ (Q&A pairs)     │                      │
│  └──────────────────┘  └─────────────────┘                      │
└──────────────────┬───────────────────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────────────────┐
│                    LLM LAYER (Ollama)                             │
│  phi4-mini (chat, GPU)  │  llama3.2:1b (heartbeat, CPU)          │
│  nomic-embed-text (768d)│  all-minilm (384d, eval)               │
└──────────────────────────────────────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────────────────┐
│                    INTEGRATIONS                                   │
│  SensorHub: MQTT Hub, Frigate NVR, Home Assistant, Audio Monitor │
│  Voice: Whisper STT (Finnish) + Piper TTS                        │
│  Feeds: FMI weather, electricity spot, RSS disease alerts         │
│  Alerts: Telegram + webhook dispatcher                            │
└──────────────────────────────────────────────────────────────────┘
```

## Core Components

### HiveMind Orchestrator (`hivemind.py` + 4 controllers)

The central coordinator (~1382 lines), with major subsystems extracted into dedicated controllers:

- `core/chat_handler.py` (716 lines) — chat routing, swarm routing, multi-agent collaboration
- `core/night_mode_controller.py` (455 lines) — night learning, convergence, weekly report
- `core/round_table_controller.py` (476 lines) — Round Table debates, agent selection, streaming
- `core/heartbeat_controller.py` (662 lines) — heartbeat loop, proactive thinking, idle research

Key responsibilities:
- Agent spawn/despawn and trust level management
- Chat routing via SmartRouter → SwarmScheduler → keyword scoring
- Heartbeat loop (proactive thinking, idle research, whisper cycle)
- Night mode activation after 30min idle
- Priority lock: chat always preempts background tasks

### Memory Engine (`core/memory_engine.py`)

Manages all knowledge storage and retrieval:
- **ChromaDB** — bilingual FI+EN vector store (nomic-embed-text, 768d)
- **HotCache** — top queries in RAM (~0.5ms)
- **LRU Cache** — bounded eviction cache
- **Corrections Memory** — prevents repeat mistakes
- **Hallucination Detection** — contrastive + keyword methods
- **TTL Eviction** — stale facts auto-expire

### SmartRouter

Multi-layer query resolution with progressive fallback:
1. HotCache (pre-computed, ~0.5ms)
2. Native-language ChromaDB index (~18ms)
3. YAML eval_questions confidence match
4. Full LLM pipeline (phi4-mini, 55ms–3000ms)

### 75 Specialized Agents (`agents/`)

Each agent is a YAML definition with:
- Domain knowledge and eval_questions
- Trust level (NOVICE → MASTER, earned through performance)
- Skills/tags for SwarmScheduler routing
- Finnish keyword weights for routing

### MAGMA Memory Architecture (`core/`)

Five layers of memory management:
- **L1:** AuditLog (append-only SQLite) + ChromaDBAdapter + MemoryWriteProxy + AgentRollback
- **L2:** ReplayStore (JSONL) + ReplayEngine (time/session/causal replay)
- **L3:** Proxy wiring + overlay networks + API endpoints
- **L4:** AgentChannels + ProvenanceTracker + CrossAgentSearch
- **L5:** TrustEngine (6-signal reputation scoring with temporal decay)

### Cognitive Graph (`core/cognitive_graph.py`)

NetworkX DiGraph with typed edges (causal, derived_from, input_to, semantic). Enables dependency traversal, shortest path queries, and causal replay of downstream facts.

## Data Flow: Query → Response

```
1. User sends message (Finnish or English)
2. Language detection (auto FI/EN)
3. SmartRouter checks layers 0-3:
   - HotCache hit? → return immediately
   - ChromaDB match? → return with confidence
   - YAML eval match? → return
   - Else → full LLM pipeline
4. Full LLM path:
   a. Finnish → Voikko lemmatization → compound splitting → Opus-MT → English
   b. SwarmScheduler selects candidate agents (6-12)
   c. Keyword scoring narrows to best agent
   d. Agent queries phi4-mini with enriched context
   e. Round Table validation (if enabled, up to 6 agents)
   f. Response → back-translation → Finnish
   g. Store Q&A pair for MicroModel training
   h. Update agent trust/reputation signals
```

## Background Processes

### Heartbeat Loop
Runs continuously, triggers:
- `proactive_think` — agents generate insights
- `idle_research` — explore knowledge gaps
- `whisper_cycle` — inter-agent communication
- Weekly report (every 50 heartbeats)

### Night Mode
Activates after 30min idle:
- Fact enrichment (llama1b generate → phi4-mini validate → store)
- Round Table debates on queued topics
- MicroModel retraining on accumulated pairs
- Pauses instantly on user chat (priority lock)

### Convergence Detection
Monitors learning novelty. Pauses sources that plateau (threshold 0.10, patience 5 cycles, 900s pause). Never pauses the last active source.

## Storage

| Store | Type | Purpose |
|-------|------|---------|
| `data/chroma_db/` | ChromaDB | Vector memory (FI+EN embeddings) |
| `data/waggle_dance.db` | SQLite | Agent levels, training pairs, metadata |
| `data/audit_log.db` | SQLite | MAGMA audit log, provenance, consensus |
| `data/cognitive_graph.json` | JSON | NetworkX graph (nodes + edges) |
| `data/learning_metrics.jsonl` | JSONL | Structured learning metrics |
| `data/replay_store.jsonl` | JSONL | MAGMA replay events |
| `data/learning_progress.json` | JSON | Night mode progress state |
| `data/weekly_report.json` | JSON | Latest weekly meta-report |
| `configs/settings.yaml` | YAML | Runtime configuration |

## Key Files

| File | Lines | Role |
|------|-------|------|
| `hivemind.py` | ~1382 | Main orchestrator (delegates to 4 controllers) |
| `core/chat_handler.py` | ~716 | Chat routing, swarm routing, collaboration |
| `core/night_mode_controller.py` | ~455 | Night learning cycle, convergence |
| `core/round_table_controller.py` | ~476 | Round Table debates, agent selection |
| `core/heartbeat_controller.py` | ~662 | Heartbeat loop, proactive thinking |
| `core/memory_engine.py` | ~1500 | Memory, search, embedding |
| `core/night_enricher.py` | ~600 | Night learning + convergence |
| `core/meta_learning.py` | ~400 | Weekly report generation |
| `core/elastic_scaler.py` | ~300 | Hardware detection + tier selection |
| `core/learning_engine.py` | ~560 | Quality gate, prompt evolution |
| `core/cognitive_graph.py` | ~250 | NetworkX knowledge graph |
| `core/trust_engine.py` | ~300 | 6-signal agent reputation |
| `web/dashboard.py` | ~1100 | Production FastAPI + endpoints |
| `start.py` | ~200 | Launcher (stub/production) |
