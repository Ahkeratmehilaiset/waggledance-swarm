# WaggleDance SWARM AI

[![Tests](https://github.com/Ahkeratmehilaiset/waggledance-swarm/actions/workflows/tests.yml/badge.svg)](https://github.com/Ahkeratmehilaiset/waggledance-swarm/actions/workflows/tests.yml)
![Python](https://img.shields.io/badge/python-3.13%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS%20%7C%20Docker-lightgrey)

![WaggleDance Dashboard](docs/images/dashboard-cottage.png)

A local-first multi-agent AI system with 75 specialized agents, Finnish NLP via Voikko,
and autonomous learning. Designed for domain-specific knowledge (originally beekeeping,
adaptable to other domains). Runs entirely on your hardware — no cloud, no API keys,
no data leaves your network.

---

## What is this?

WaggleDance is a local-first AI system where 75 specialized agents communicate through a HiveMind orchestrator, debate in Round Table consensus, and learn from conversations, sensors, and domain data. It uses small LLMs (phi4-mini, llama3.2:1b) enhanced by a growing vector memory (ChromaDB) and a Finnish NLP pipeline (Voikko + Opus-MT).

**Key ideas:**
- **Multi-agent consensus** — up to 6 agents cross-validate each answer via Round Table, reducing hallucination
- **MicroModel auto-promotion** — topics that accumulate enough validated Q&A pairs graduate from LLM to a local classifier, reducing response time from seconds to sub-millisecond
- **Finnish NLP pipeline** — Voikko morphological analysis handles Finnish compound words and 14 noun cases before translation, enabling accurate domain reasoning in Finnish
- **Domain-agnostic architecture** — originally built for Finnish beekeeping (300 hives, 10,000 kg honey/year), but the agent/knowledge YAML structure can be adapted to other domains

**Response time improves over use:** ~3,000ms (cold, full LLM path) → ~55ms (bilingual ChromaDB) → ~0.5ms (Hot Cache hit for previously seen queries).

36/36 test suites pass (700+ assertions, measured locally). No subscription, no API keys required.

---

## Key Features

- **75 specialized agents** communicating through HiveMind orchestrator (5 trust levels: NOVICE → MASTER)
- **Elastic scaling** — auto-detects GPU/RAM/CPU at startup, selects optimal model tier (minimal → enterprise)
- **SmartRouter** — routes queries through cache → memory → LLM layers, response time improves with use
- **Finnish NLP** — Voikko lemmatization + compound splitting + Opus-MT translation (language-swappable for other languages)
- **Vector memory** — ChromaDB with bilingual FI+EN index (55ms retrieval, measured on RTX A2000)
- **Round Table consensus** — up to 6 agents debate and cross-validate answers
- **MicroModel evolution** — V1 (pattern match, 0.01ms) + V2 (neural classifier, 1ms) auto-promote from LLM
- **Smart home sensors** — MQTT hub, Frigate NVR cameras, Home Assistant bridge, Telegram/webhook alerts
- **Audio sensors** — ESP32 bee audio analysis (stress/swarming/queen piping detection), BirdNET integration
- **Voice interface** — Whisper STT (Finnish) + Piper TTS, wake word activation
- **External data feeds** — FMI weather, electricity spot prices, RSS disease alerts
- **Production hardening** — CircuitBreaker, graceful degradation, structured logging, TTL eviction
- **Night mode** — autonomous learning when idle (fact enrichment, Round Table debates, MicroModel retraining)
- **Stub / Production mode** — develop dashboard without Ollama (`python start.py --stub`)
- **4 deployment profiles** — GADGET / COTTAGE / HOME / FACTORY

---

## Limitations & Known Issues

- **Single developer project** — not yet validated by external users
- **Finnish beekeeping focus** — adapting to other domains requires new YAML agent definitions
- **Autonomous learning layers 3-6** — code structure exists but not production-tested
- **MicroModel V3 (LoRA)** — architecture defined, training pipeline not yet implemented
- **CI runs basic test suite** — full 36-suite validation still requires local `tools/waggle_backup.py`
- **Web learning & Claude distillation** — disabled by design (offline-first), code ready but untested in production
- **ESP32/GADGET tier** — theoretical, not tested on actual ESP32 hardware
- **Performance numbers** — self-measured with internal test suites, not independently verified

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
- ✅ V1 Pattern Match — regex + lookup table, ~0.01ms
- ✅ V2 Neural Classifier — PyTorch 768→256→128→N, ~1ms, trains on collected pairs
- ✅ Topic auto-promotion — 200+ pairs + <3% error → promoted
- ✅ Training collector — records every Q&A with source and confidence
- 🔲 V3 LoRA nano-LLM — architecture defined, training pipeline not yet implemented

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
      └── 4 domain tabs: GADGET / COTTAGE / HOME / FACTORY
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

### GADGET — Edge Intelligence
ESP32, Raspberry Pi, wearables. TinyML classifiers, sensor fusion. *(Theoretical — not tested on hardware.)*

### COTTAGE — Off-Grid Intelligence
Purpose-built for Finnish beekeeping. Hive monitoring, varroa detection, weather integration, acoustic analysis.

### HOME — Smart Living
Mac Mini, NUC, any decent PC. Home Assistant integration, voice control, energy optimization.

### FACTORY — Industrial Scale
Server rack / DGX. Predictive maintenance, monitoring, shift handover. *(Theoretical — not tested on hardware.)*

---

## Installation

### Option A: Docker (recommended)

```bash
git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git
cd waggledance-swarm
docker compose up -d
```

Pull required models (one-time, ~3GB total):

```bash
docker compose exec ollama ollama pull phi4-mini
docker compose exec ollama ollama pull llama3.2:1b
docker compose exec ollama ollama pull nomic-embed-text
docker compose exec ollama ollama pull all-minilm
```

Open **http://localhost:8000**.

> **GPU note:** Requires [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) on Linux, or Docker Desktop with WSL2 GPU support on Windows. No GPU? Remove the `deploy.resources` block from `docker-compose.yml`.

### Option B: Native Install

```bash
# 1. Install Ollama (https://ollama.ai)
# 2. Pull models
ollama pull phi4-mini && ollama pull llama3.2:1b && ollama pull nomic-embed-text && ollama pull all-minilm

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
python start.py              # Interactive menu
python start.py --stub       # No Ollama needed — stub backend + React dashboard
python start.py --production  # Full HiveMind (requires Ollama + 4 models)
```

### Verify Installation

```bash
python tools/waggle_backup.py --tests-only
```

Expected: **36/36 suites GREEN, 700+ assertions, 0 failures.**

---

## Project Structure

```
waggledance-swarm/
├── agents/              # 75 YAML agent knowledge bases
├── knowledge/           # Domain knowledge bases
├── core/                # Core modules (memory_engine, translation_proxy, models, scheduling)
├── integrations/        # External systems (MQTT, Frigate, HA, audio, voice, feeds)
├── backend/             # Standalone stub backend (no Ollama needed)
│   └── routes/          #   12 API route modules
├── dashboard/           # Vite + React UI (port 5173)
├── tests/               # 36 test suites (700+ assertions)
├── tools/               # Backup, restore, benchmarks, scanners
├── configs/             # settings.yaml, bee_terms.yaml, seasonal_rules.yaml
├── docs/                # Documentation and images
├── hivemind.py          # HiveMind orchestrator
├── main.py              # Production entry point
├── start.py             # Launcher (--stub / --production)
├── Dockerfile           # Python 3.13 + Voikko
└── docker-compose.yml   # Ollama + WaggleDance stack
```

---

## Current Status

- ✅ **Phase 1:** Foundation — memory engine, dual embedding, smart router
- ✅ **Phase 2:** Batch Pipeline — batch embedding/translation, 3,147+ facts in ChromaDB
- ✅ **Phase 3:** Social Learning — Round Table, agent levels, night mode
- ✅ **Phase 4:** Advanced Learning — bilingual index, hot cache, fact enrichment, corrections, MicroModel V1+V2
- ✅ **Phase 5:** Smart Home Sensors — MQTT hub, Frigate NVR, Home Assistant, Telegram alerts
- ✅ **Phase 6:** Audio Sensors — bee audio analysis, BirdNET bird detection, ESP32 MQTT
- ✅ **Phase 7:** Voice Interface — Whisper STT (Finnish) + Piper TTS, wake word
- ✅ **Phase 8:** External Data Feeds — FMI weather, electricity spot price, RSS disease alerts
- ✅ **Phase B/C/D:** Production Hardening — CircuitBreaker, caching, structured logging, convergence detection
- ✅ **Phase 11:** Elastic Scaling — auto-detect GPU/RAM/CPU, 5-tier classification
- 🔲 **Phase 9:** Autonomous Learning Layers 3-6 — code exists, disabled (offline-first by design)
- 🔲 **Phase 10:** MicroModel V3 LoRA — architecture defined, training pipeline pending

---

## Performance

All measurements taken on HP ZBook with NVIDIA RTX A2000 8GB + 128GB RAM, using internal test suites.

| Metric | Value | Notes |
|--------|-------|-------|
| Test suites | 36/36 GREEN | 700+ assertions, measured locally |
| Agent routing accuracy | 97.7% | 1,235 internal test questions across 75 agents |
| Hot Cache response | ~0.5ms | Previously seen queries, in-memory lookup |
| Bilingual ChromaDB search | ~55ms | FI+EN vector search |
| Full LLM response (phi4-mini) | 500-3,000ms | Depends on query complexity and context |
| MicroModel V1 (pattern match) | ~0.01ms | Regex + lookup table |
| MicroModel V2 (classifier) | ~1ms | PyTorch 250K params on CPU |
| Hallucination rate | ~1.8% | Contrastive + keyword detection on internal test set |
| Night learning rate | 50-200 facts/night | Varies with hardware and convergence |

---

## API Reference

### Core
| Endpoint | Method | Description |
|----------|--------|-------------|
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
| `/api/voice/status` | GET | Voice interface status |

### Analytics & Configuration
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/analytics/trends` | GET | 7-day performance trends |
| `/api/round-table/recent` | GET | Latest Round Table transcripts |
| `/api/agents/levels` | GET | All 75 agents with levels/trust |
| `/api/settings` | GET | Feature toggles |
| `/api/settings/toggle` | POST | Toggle a feature on/off |

---

## Credits

Created by **Jani Korpi** (JKH Service / Ahkerat Mehiläiset, Helsinki).
Built with significant assistance from [Claude](https://claude.ai) (Anthropic) as AI coding partner.

---

## License

MIT — Free to use, modify, distribute.

---

**Disclaimer:** This system is provided as-is under the MIT license. The author assumes no responsibility for actions, decisions, or consequences arising from its operation. Keep hardware power limited and run in a controlled environment. Do not connect to critical infrastructure without human oversight.

---

<p align="center">
  <strong>Ahkerat Mehiläiset · Helsinki, Finland · 2024–2026</strong>
</p>
