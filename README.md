# WaggleDance AI

[![Tests](https://github.com/Ahkeratmehilaiset/waggledance-swarm/actions/workflows/tests.yml/badge.svg)](https://github.com/Ahkeratmehilaiset/waggledance-swarm/actions/workflows/tests.yml)
![Python](https://img.shields.io/badge/python-3.13%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS%20%7C%20Docker-lightgrey)
![Version](https://img.shields.io/badge/version-1.17.0-green)

**A local-first, auditable AI runtime that routes each task to the right reasoning layer — memory, rules, micromodels, model-based inference, or LLM reasoning.**

WaggleDance is built for real-world environments where answers should be grounded in local data, structured rules, explicit models, and auditable decision paths. It runs on your own hardware and can combine documents, sensors, time-series data, and domain memory without relying on cloud-first assumptions.

Instead of treating every task as a chatbot problem, WaggleDance selects the best available method for the job: memory for recall, rules for constraints, micromodels for fast low-cost inference, model-based inference for calculable systems, and LLMs for language, ambiguity, and explanation.

The system was originally developed in a demanding real-world field environment and is being generalized into a broader runtime for sensor-rich, domain-specific applications.

## Deployment Profiles

| Profile | Environment | Example use cases |
|---------|-------------|-------------------|
| **GADGET** | IoT, edge | Battery life, signal quality, sensor calibration |
| **COTTAGE** | Off-grid property | Heating cost, frost protection, seasonal tasks |
| **HOME** | Smart home | Energy optimization, safety alerts, comfort |
| **FACTORY** | Industrial | OEE analysis, SPC monitoring, predictive maintenance |

![WaggleDance Dashboard](docs/images/dashboard-cottage.png)

Validated with 76 legacy suites (2427 tests), pytest unit/core/app/contracts (469 tests), and integration tests (90 tests) — 2986 total, 0 failures. No subscription, no API keys required.

---

## Key Features

- **75 specialized agents** communicating through HiveMind orchestrator (5 trust levels: NOVICE → MASTER, persistent trust via SQLite)
- **Elastic scaling** — auto-detects GPU/RAM/CPU at startup, selects optimal model tier (minimal → enterprise)
- **SmartRouter** — routes queries through cache → micromodel → memory → LLM layers, response time improves with use
- **Finnish NLP** — Voikko lemmatization + compound splitting + Opus-MT translation (language-swappable for other languages)
- **Vector memory** — ChromaDB with bilingual FI+EN index (55ms retrieval, measured on RTX A2000)
- **Round Table consensus** — up to 6 agents debate and cross-validate answers
- **MicroModel routing** — V1 (pattern match, 0.01ms) + V2 (neural classifier, 1ms) with end-to-end route selection, canary promotion, and auto-rollback
- **Smart home sensors** — MQTT hub, Frigate NVR cameras, Home Assistant bridge, Telegram/webhook alerts
- **Audio sensors** — ESP32 bee audio analysis (stress/swarming/queen piping detection), BirdNET integration
- **Voice interface** — Whisper STT (Finnish) + Piper TTS, wake word activation
- **External data feeds** — FMI weather, electricity spot prices, RSS disease alerts
- **MAGMA memory architecture** — append-only audit log, selective replay, write proxy, agent rollback, overlay branches, A/B testing, mood presets
- **Cognitive Graph** — NetworkX-based knowledge graph with causal/semantic edges, dependency traversal, causal replay
- **Trust & Reputation** — 6-signal agent trust scoring with persistent SQLite trust store (hallucination, validation, consensus, correction, fact production, freshness)
- **Cross-agent memory** — agent channels, provenance tracking, consensus records, filtered overlay views
- **Production hardening** — CircuitBreaker, graceful degradation, structured logging, TTL eviction
- **Night mode** — autonomous learning when idle (fact enrichment, Round Table debates, MicroModel retraining)
- **Persistent chat history** — conversations stored in SQLite, survive page refresh
- **User feedback system** — thumbs up/down on every AI response, feeds into corrections memory
- **Model status dashboard** — real-time VRAM usage, loaded models, role mapping, utilization
- **Active profile switching** — GADGET/COTTAGE/HOME/FACTORY tabs change agent behavior in real-time
- **Stub / Production mode** — develop dashboard without Ollama (`python start.py --stub`)
- **4 deployment profiles** — GADGET / COTTAGE / HOME / FACTORY

---

## Limitations & Known Issues

- **Single developer project** — not yet validated by external users
- **Finnish beekeeping focus** — adapting to other domains requires new YAML agent definitions
- **Bearer token authentication** — auto-generated `WAGGLE_API_KEY` on first startup; `/health` and `/api/status` are public
- **No TLS** — use nginx/Caddy if exposing beyond localhost
- **Single-node only** — no clustering or distributed deployment
- **MAGMA memory layers** — fully wired (Layers 1-5 + Cognitive Graph), but not production-tested at scale
- **MicroModel V3 (LoRA)** — Phi-3.5-mini pipeline validated (2.92GB VRAM), full training deferred
- **CI runs basic test suite** — full 76-suite validation still requires local `tools/waggle_backup.py`
- **Web learning & Claude distillation** — disabled by design (offline-first), code ready but untested in production
- **ESP32/GADGET tier** — theoretical, not tested on actual ESP32 hardware
- **Performance numbers** — self-measured with internal test suites, not independently verified

---

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/ARCHITECTURE.md) | System overview, components, data flow |
| [API Reference](docs/API.md) | All ~70 REST endpoints + WebSocket protocol |
| [Deployment](docs/DEPLOYMENT.md) | Docker, native install, profiles, hardware requirements |
| [Security](docs/SECURITY.md) | Threat model, mitigations, secrets management |
| [Data & Migrations](docs/MIGRATIONS.md) | Database schemas, backup/restore, data layout |
| [Sensors & Integrations](docs/SENSORS.md) | MQTT, Frigate, HA, audio, voice, alerts |

---

## Language Architecture

WaggleDance processes non-English input through a morphological engine before the LLM sees it:

```
Your Language → Morphological Engine → Lemmatization → Compound Splitting
    → Spell Correction → Translation → LLM (English) → Back-Translation → Your Language
```

For Finnish, Voikko resolves grammar, inflections, and compound words. For example, "mehiläispesässä" (= "in the beehive") is decomposed into clean concepts the LLM can reason about.

| Language | Engine | Notes |
|----------|--------|-------|
| Finnish | **Voikko** (bundled) | Lemmatization, compound splitting, spell correction |
| German, Spanish, French | Hunspell | Widely available, many dictionaries |
| Japanese | MeCab | Morphological analysis + tokenization |
| **English** | **None needed** | Direct LLM path, no translation overhead |

English queries skip the translation step entirely for faster response.

---

## How Self-Learning Works

WaggleDance gets smarter over time by recording every Q&A interaction as a training pair:

```
               ┌─────────────────────────────────────────────────┐
               │          CONTINUOUS SELF-LEARNING LOOP           │
               │                                                  │
  SOURCES      │   PROCESS              VALIDATION    STORAGE     │
  ──────       │   ───────              ──────────    ───────     │
  YAML files ──┤                                                  │
  User chat  ──┤→  Extract facts ──→ Embed (nomic) ──→ ChromaDB  │
  Corrections──┤   (no LLM needed)    768-dim vectors   (FI+EN)  │
  Round Table──┤                                                  │
  Enrichment ──┤                                                  │
               │                                                  │
               │   MicroModel trains on accumulated pairs.        │
               │   When accuracy exceeds LLM for a topic,        │
               │   that topic is promoted to MicroModel-only.     │
               │   Response time drops from ~3,000ms to <1ms.    │
               └─────────────────────────────────────────────────┘
```

**What's implemented:**
- V1 Pattern Match — regex + lookup table, ~0.01ms
- V2 Neural Classifier — PyTorch 768→256→128→N, ~1ms, trains on collected pairs
- Topic auto-promotion — 200+ pairs + <3% error → promoted
- Training collector — records every Q&A with source and confidence
- V3 LoRA nano-LLM — Phi-3.5-mini pipeline validated (4-bit NF4, 2.92GB VRAM), full training deferred

### Round Table — Agent Consensus

Every 20 heartbeats, 6 agents hold a structured debate:

1. **Selection** — pick agents by topic relevance + level
2. **Discussion** — each agent responds, seeing previous answers (sequential, llama1b)
3. **Synthesis** — Queen agent summarizes consensus
4. **Storage** — consensus stored as high-confidence fact (0.85)

### Agent Levels — Earned Trust

```
Level 1 NOVICE:      Memory-only, all answers checked
Level 2 APPRENTICE:  +LLM access, can read shared facts     (50 correct, <15% halluc)
Level 3 JOURNEYMAN:  +write shared facts, consult 1 agent   (200 correct, <8% halluc)
Level 4 EXPERT:      +consult 3 agents, web search           (500 correct, <3% halluc)
Level 5 MASTER:      Full autonomy, can teach other agents   (1000 correct, <1% halluc)
```

Demotion is automatic if hallucination rate exceeds threshold over a 50-response window.

### Night Mode

When idle for 30+ minutes, the system shifts to aggressive learning:
- Fact enrichment (generate with llama1b → validate with phi4-mini → store if both agree)
- Round Table debates on queued topics
- MicroModel retraining on accumulated pairs
- Pauses instantly when user returns (chat priority lock)

---

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for full system diagrams.

```
User (Finnish / English) → FastAPI (port 8000)
  │
  ├── Language Detection (auto FI/EN)
  │
  ├── SmartRouter
  │   ├── Layer 0: HotCache (~0.5ms, pre-computed answers)
  │   ├── Layer 1: Native-language index (~18ms, skip translation)
  │   ├── Layer 2: YAML eval_questions (confidence matching)
  │   └── Layer 3: Full LLM pipeline (55ms+)
  │
  ├── HiveMind Orchestrator (hivemind.py)
  │   ├── Priority Lock (chat pauses all background tasks)
  │   ├── Round Table (up to 6 agents debate + cross-validate)
  │   ├── Heartbeat (continuous background learning)
  │   ├── Seasonal Guard (rejects out-of-season claims)
  │   └── CircuitBreaker (auto-disable failing subsystems, self-heal)
  │
  ├── Memory Engine (core/memory_engine.py)
  │   ├── Hot Cache (top queries in RAM, auto-fill)
  │   ├── LRU Cache (smart eviction, memory-bounded)
  │   ├── Bilingual Index (FI+EN vectors in ChromaDB)
  │   ├── Dual Embedding (nomic for search + minilm for eval)
  │   ├── Corrections Memory (prevents repeat mistakes)
  │   ├── Hallucination Detection (contrastive + keyword)
  │   └── Eviction TTL (stale facts auto-expire)
  │
  ├── MAGMA Memory Architecture (core/)
  │   ├── L1: Audit Log + Write Proxy + Agent Rollback
  │   ├── L2: Replay Store + Replay Engine (time/session/causal)
  │   ├── L3: Overlay Networks + API endpoints
  │   ├── L4: Agent Channels + Provenance + Cross-Agent Search
  │   ├── L5: Trust Engine (6-signal reputation)
  │   └── Cognitive Graph (NetworkX, causal edges)
  │
  ├── Sensors & Integrations
  │   ├── MQTT Hub (paho-mqtt, reconnect, dedup)
  │   ├── Frigate NVR (camera events, severity alerts)
  │   ├── Home Assistant Bridge (REST poll)
  │   ├── Audio Monitor (ESP32 bee audio + BirdNET)
  │   ├── Voice Interface (Whisper STT + Piper TTS)
  │   ├── Data Feeds (FMI weather, electricity, RSS)
  │   └── Alert Dispatcher (Telegram + webhook)
  │
  ├── Production Hardening
  │   ├── CircuitBreaker — auto-disable, self-heal after 3 failures
  │   ├── Structured Logging — JSON to learning_metrics.jsonl
  │   ├── ConvergenceDetector — pauses learning when plateauing
  │   └── Graceful Degradation — missing deps → warning, never crash
  │
  ├── Translation Pipeline
  │   ├── Opus-MT fi↔en (on-device)
  │   └── Auto-skip when input is English
  │
  └── Dashboard (Vite + React, port 5173)
      ├── 3D neural brain visualization
      ├── Real-time agent heartbeat feed
      ├── CPU / GPU / VRAM gauges
      ├── Interactive chat (FI/EN)
      ├── Analytics, Round Table, Agent Grid panels
      ├── Profile switching (GADGET/COTTAGE/HOME/FACTORY — changes active agents)
      ├── Model status panel (VRAM usage, loaded models, utilization)
      ├── Persistent chat history (SQLite, survives refresh)
      └── Feedback system (thumbs up/down on AI responses)
```

---

## Hardware Scaling

WaggleDance auto-detects hardware at startup and selects the appropriate tier:

```
VRAM < 2GB   → MINIMAL:       No LLM, V1 pattern match only
VRAM 2-4GB   → LIGHT:         qwen3:0.6b + smollm2:135m, 2 agents
VRAM 4-16GB  → STANDARD:      phi4-mini + llama3.2:1b, 6 agents
VRAM 16-48GB → PROFESSIONAL:  phi4:14b + qwen3:4b, 15 agents + vision
VRAM 48GB+   → ENTERPRISE:    llama3.3:70b + llama3.1:8b, 75 agents
```

**Development hardware:** NVIDIA RTX A2000 8GB + 128GB RAM → STANDARD tier (4.3G / 8.0G VRAM used).

**Note:** The EDGE/ESP32 and ENTERPRISE/DGX tiers are theoretical — they have not been tested on actual hardware. The STANDARD tier is the only configuration tested in production.

---

## Deployment Profiles

### GADGET — Edge Intelligence (3 agents)
ESP32, Raspberry Pi, wearables. TinyML classifiers, sensor fusion. *(Theoretical — not tested on hardware.)*

### COTTAGE — Off-Grid Intelligence (46 agents)
Purpose-built for Finnish beekeeping. Hive monitoring, varroa detection, weather integration, acoustic analysis.

### HOME — Smart Living (43 agents)
Mac Mini, NUC, any decent PC. Home Assistant integration, voice control, energy optimization.

### FACTORY — Industrial Scale (28 agents)
Server rack / DGX. Predictive maintenance, monitoring, shift handover. *(Theoretical — not tested on hardware.)*

Switching profiles:
- Changes which agents are active
- Adjusts Round Table size to available agents
- Persists across server restarts (stored in settings.yaml)

---

## Installation

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for detailed instructions.

### Option A: Docker (recommended)

```bash
git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git
cd waggledance-swarm
cp .env.example .env    # Edit with your secrets
docker compose up -d
```

Pull required models (one-time, ~3GB total):

```bash
docker compose exec ollama ollama pull phi4-mini
docker compose exec ollama ollama pull llama3.2:1b
docker compose exec ollama ollama pull nomic-embed-text   # CRITICAL — must be running
docker compose exec ollama ollama pull all-minilm
```

> **Critical dependency:** `nomic-embed-text` is required for all memory operations (search, learn, dedup). WaggleDance will **refuse to start** if this model is not available. Ensure Ollama is running and the model is pulled before starting WaggleDance. The system monitors nomic health every 10 heartbeats and sends a WebSocket alert if it goes down.

Open **http://localhost:8000**.

> **GPU note:** Requires [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) on Linux, or Docker Desktop with WSL2 GPU support on Windows. No GPU? Remove the `deploy.resources` block from `docker-compose.yml`.

### Option B: Native Install

```bash
# 1. Install Ollama (https://ollama.ai)
# 2. Pull models
ollama pull phi4-mini && ollama pull llama3.2:1b
ollama pull nomic-embed-text   # CRITICAL — required for all memory operations
ollama pull all-minilm

# 3. Clone and install
git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git
cd waggledance-swarm
pip install -r requirements.txt

# 4. Run
python main.py
# → http://localhost:8000
```

### Launcher

```bash
python start.py              # Interactive menu (legacy + new runtime options)
python start.py --stub       # No Ollama needed — stub backend + React dashboard
python start.py --production  # Full HiveMind (requires Ollama + 4 models)
```

### New Runtime (Recommended for New Deployments)

```bash
python -m waggledance.adapters.cli.start_runtime --stub        # Stub mode
python -m waggledance.adapters.cli.start_runtime               # Production
python -m waggledance.adapters.cli.start_runtime --port 9000   # Custom port
```

See [ENTRYPOINTS.md](ENTRYPOINTS.md) for details on primary vs legacy entrypoints.

### Verify Installation

```bash
python tools/waggle_backup.py --tests-only
```

Expected: **72/76 suites GREEN, 2427+ tests, 0 failures** (4 suites skipped without Ollama). Additional pytest suites: `pytest tests/ -q`.

---

## Project Structure

```
waggledance-swarm/
├── agents/              # 75 YAML agent knowledge bases
├── knowledge/           # Domain knowledge bases
├── core/                # Core modules (memory_engine, translation_proxy, models, scheduling)
│   ├── memory_engine.py #   Memory, search, embedding, caching
│   ├── chat_history.py  #   SQLite chat storage + feedback
│   ├── cognitive_graph.py#  NetworkX knowledge graph
│   ├── trust_engine.py  #   6-signal agent reputation
│   ├── night_enricher.py#   Night learning + convergence
│   └── elastic_scaler.py#   Hardware detection + tier selection
├── integrations/        # External systems (MQTT, Frigate, HA, audio, voice, feeds)
├── backend/             # Standalone stub backend (no Ollama needed)
│   └── routes/
│       ├── models.py    #   Ollama model status endpoint
│       └── ...          #   16 API route modules
├── web/                 # Production FastAPI app (dashboard.py)
├── dashboard/           # Vite + React UI (port 5173)
├── tests/               # 76 legacy suites + pytest unit/core/integration (~2986 tests)
├── tools/               # Backup, restore, benchmarks, night shift
├── configs/             # settings.yaml, bee_terms.yaml, seasonal_rules.yaml
├── docs/                # Architecture, API, deployment, security, sensors
├── .github/
│   └── workflows/
│       └── tests.yml    #   CI test runner
├── data/                # Runtime data (ChromaDB, SQLite, JSONL, JSON — not in git)
├── waggledance/         # New hexagonal architecture (ports & adapters)
│   ├── core/            #   Domain, ports (8 Protocol), orchestration, policies
│   ├── application/     #   Services (chat, memory, learning, readiness)
│   ├── adapters/        #   Ollama, ChromaDB, HotCache, HTTP, CLI
│   └── bootstrap/       #   DI container, event bus
├── hivemind.py          # HiveMind orchestrator (~1382 lines + 4 controllers)
├── main.py              # Legacy entry point (see ENTRYPOINTS.md)
├── start.py             # Launcher (--stub / --production / --new-runtime)
├── Dockerfile           # Python 3.13 + Voikko + healthcheck
└── docker-compose.yml   # Ollama + WaggleDance stack
```

---

## Security

See [docs/SECURITY.md](docs/SECURITY.md) for full threat model.

- **Localhost-only** — Bearer token auth for API, single-user design
- **No TLS** — use a reverse proxy for network exposure
- **Rate limiting** — 20 req/min on `/api/chat`
- **Input validation** — Pydantic models, size limits on all inputs
- **CORS** — restricted to GET/POST, configurable origins
- **Generic errors** — stack traces never leaked to clients
- **Secrets in .env** — git-ignored, never committed

---

## Current Status

- **Phase 1:** Foundation — memory engine, dual embedding, smart router
- **Phase 2:** Batch Pipeline — batch embedding/translation, 3,147+ facts in ChromaDB
- **Phase 3:** Social Learning — Round Table, agent levels, night mode
- **Phase 4:** Advanced Learning — bilingual index, hot cache, fact enrichment, corrections, MicroModel V1+V2
- **Phase 5:** Smart Home Sensors — MQTT hub, Frigate NVR, Home Assistant, Telegram alerts
- **Phase 6:** Audio Sensors — bee audio analysis, BirdNET bird detection, ESP32 MQTT
- **Phase 7:** Voice Interface — Whisper STT (Finnish) + Piper TTS, wake word
- **Phase 8:** External Data Feeds — FMI weather, electricity spot price, RSS disease alerts
- **Phase 9:** Documentation Overhaul — architecture, API, deployment, security guides
- **Phase B/C/D:** Production Hardening — CircuitBreaker, caching, structured logging, convergence detection
- **Phase 11:** Elastic Scaling — auto-detect GPU/RAM/CPU, 5-tier classification
- **MAGMA L1-L3:** Memory Architecture — audit log, replay store, write proxy, overlay networks, agent rollback
- **MAGMA L4:** Cross-Agent Memory — agent channels, provenance tracking, consensus records
- **MAGMA L5:** Trust & Reputation — 6-signal trust scoring, domain experts, temporal decay
- **Cognitive Graph:** NetworkX knowledge graph — causal/semantic edges, dependency traversal, causal replay
- **Overlay Expansion:** Branchable memory — A/B testing, mood presets, agent contribution transforms
- **Profile switching** — frontend tabs wired to backend, agent filtering active
- **Model status display** — Ollama integration, VRAM monitoring
- **Chat history persistence** — SQLite storage, conversation replay
- **User feedback** — thumbs up/down, corrections memory integration
- **Critical bug fixes (v0.7.0)** — 31 bugs fixed: race conditions in concurrent chat, resource leak prevention, CORS middleware, async nvidia-smi, WebSocket fixes, embedding dimension correction, shutdown ordering, bounded growth for all runtime data
- **Security + stability fixes (v0.8.0)** — 12 fixes: async safety, SQL injection prevention, SQLite write locks, WS callback leak, deprecated API cleanup, metrics rotation
- **Major refactor (v0.9.0)** — hivemind.py 3321→1382 lines, 4 controller modules extracted, 12 Sonnet review fixes, Phi-3.5-mini LoRA pipeline validated
- **GitHub Actions CI** — unified test runner (legacy + pytest)
- **SmartRouter v2 (v1.6–v1.15)** — capsule routing, Finnish normalization, word boundaries, matched_keywords transparency
- **Hexagonal refactor** — `waggledance/` package with ports & adapters, DI container, 172 new tests
- **New runtime (v1.15+)** — `start_runtime.py` with argparse, UTF-8, Ollama check
- **Safe self-improvement (v1.16)** — prompt evolution with rollback, micro-model eval gate, night learning source visibility
- **Big Sprint (v1.17)** — memory_engine split (4 extracted modules), persistent SQLite TrustStore, micromodel route restored end-to-end, active learning/canary/telemetry, MQTT ingest, runtime shadow-compare, test unification (76 suites + 559 pytest)

---

## Performance

All measurements taken on HP ZBook with NVIDIA RTX A2000 8GB + 128GB RAM, using internal test suites.

| Metric | Value | Notes |
|--------|-------|-------|
| Test suites | 72/76 GREEN + 559 pytest | 2986 total tests, 4 skipped without Ollama |
| Agent routing accuracy | 97.7% | 1,235 internal test questions across 75 agents |
| Hot Cache response | ~0.5ms | Previously seen queries, in-memory lookup |
| Bilingual ChromaDB search | ~55ms | FI+EN vector search |
| Full LLM response (phi4-mini) | 500-3,000ms | Depends on query complexity and context |
| MicroModel V1 (pattern match) | ~0.01ms | Regex + lookup table |
| MicroModel V2 (classifier) | ~1ms | PyTorch 250K params on CPU |
| Hallucination rate | ~1.8% | Contrastive + keyword detection on internal test set |
| Night learning rate | 50-200 facts/night | Varies with hardware and convergence |
| Chat history storage | SQLite (local) | Persistent across page refresh |
| Feedback → corrections | Automatic | Thumbs down triggers correction memory |
| CI pipeline | GitHub Actions | Legacy + pytest, 2986 total tests |

---

## API Reference

See [docs/API.md](docs/API.md) for complete endpoint documentation (~70 endpoints).

### Core
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Liveness probe |
| `/ready` | GET | Readiness probe |
| `/api/status` | GET | System status, uptime, metrics |
| `/api/chat` | POST | Chat with HiveMind (auto FI/EN) |
| `/api/heartbeat` | GET | Agent activity feed |
| `/api/hardware` | GET | Live CPU/GPU/VRAM stats |

### Sensors & Voice
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/sensors` | GET | Sensor hub overview |
| `/api/sensors/home` | GET | Home Assistant entities |
| `/api/sensors/camera/events` | GET | Frigate camera events |
| `/api/sensors/audio` | GET | Audio monitor status + events |
| `/api/sensors/audio/bee` | GET | Bee audio analysis |
| `/api/voice/status` | GET | Voice interface status |
| `/api/voice/text` | POST | Send text for TTS |
| `/api/voice/audio` | POST | Send audio for STT |

### Analytics & Configuration
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/analytics/trends` | GET | 7-day performance trends |
| `/api/round-table/recent` | GET | Latest Round Table transcripts |
| `/api/agents/levels` | GET | All 75 agents with levels/trust |
| `/api/settings` | GET | Feature toggles |
| `/api/settings/toggle` | POST | Toggle a feature on/off |

### Dashboard Features
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/models` | GET | Ollama model status, VRAM, roles |
| `/api/history` | GET | Conversation history |
| `/api/history/recent/messages` | GET | Recent chat messages |
| `/api/feedback` | POST | User feedback collection (thumbs up/down) |
| `/api/profile` | GET/POST | Get or switch deployment profile |

### MAGMA Memory Architecture
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/magma/stats` | GET | Audit log + replay store status |
| `/api/magma/audit` | GET | Last 24h audit entries |
| `/api/magma/branches` | GET | Overlay branches and status |
| `/api/trust/ranking` | GET | All agents ranked by reputation |
| `/api/trust/agent/{id}` | GET | Full reputation breakdown |
| `/api/cross/channels` | GET | Agent communication channels |
| `/api/graph/stats` | GET | Cognitive graph node/edge counts |

### WebSocket
| Endpoint | Description |
|----------|-------------|
| `ws://localhost:8000/ws` | Real-time event stream (heartbeat, chat, alerts) |

---

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Run tests: `python tools/waggle_backup.py --tests-only`
4. Ensure all suites pass
5. Submit a pull request

### Development Setup

```bash
python start.py --stub    # Dashboard dev without Ollama
cd dashboard && npm run dev  # React dev server (hot reload)
```

### Code Style
- Python: standard library conventions, type hints where useful
- Finnish UI output, English internal processing
- All new features must include test suite

---

## Credits

Created by **Jani Korpi** (JKH Service / Ahkerat Mehiläiset, Helsinki).
Built with significant assistance from [Claude](https://claude.ai) (Anthropic) as AI coding partner.

---

## License

MIT — Free to use, modify, distribute.

---

**Disclaimer:** This system is provided as-is under the MIT license. Use at your own risk. Not intended for critical infrastructure without human oversight.

---

<p align="center">
  <strong>Ahkerat Mehiläiset · Helsinki, Finland · 2024–2026</strong>
</p>
