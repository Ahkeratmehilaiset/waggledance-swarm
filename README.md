# 🐝 WaggleDance SWARM AI

> Local-first self-learning multi-agent AI system.
> 100 agents. Vector memory. Autonomous evolution. No cloud. No limits.

## What is this?

WaggleDance is an on-premise AI that runs on YOUR hardware, learns YOUR domain, and gets smarter every day — without ever sending data to the cloud. Originally built for Finnish beekeeping (300 hives), it scales to smart homes, factories, and IoT edge devices.

## Key Features

- 🧠 **100 specialized agents** with HiveMind orchestrator
- 🔄 **6-layer autonomous learning** — learns 24/7 without human input
- 🇫🇮 **Bilingual** — Finnish I/O, English LLM processing
- 📊 **Vector memory** — ChromaDB with bilingual index (55ms)
- ⚡ **MicroModel evolution** — 3,000ms → 0.3ms response over time
- 🎯 **97.7% routing accuracy** across 50 agent specializations
- 🔒 **Zero cloud** — everything local, your data stays yours
- 📡 **4 deployment profiles** — GADGET / HOME / COTTAGE / FACTORY

## Architecture

```
User (Finnish) → FastAPI (port 8000)
├── 3-Layer Smart Router (97.7% accuracy)
├── HiveMind Orchestrator
│   ├── 100 YAML Agents (agents/)
│   ├── Round Table Consensus
│   └── Priority Lock (chat always wins)
├── Consciousness Engine
│   ├── ChromaDB Vector Memory
│   ├── Dual Embedding (nomic + minilm)
│   ├── Bilingual Index (FI+EN, 55ms)
│   └── Hallucination Detection
├── Translation (Opus-MT fi↔en)
└── Dashboard (Vite + React, port 5173)
```

## Hardware Scaling

| Tier | Hardware | Cost | Tok/s | Facts/Year |
|------|----------|------|-------|------------|
| EDGE | ESP32-S3 | €8 | 5 | 105K |
| LIGHT | Intel NUC 13 | €650 | 15 | 569K |
| PRO | Mac Mini M4 | €2,200 | 42 | 1.9M |
| ENTERPRISE | DGX B200 | €400K | 380 | 24.5M |

## Quick Start

```bash
# Backend
pip install -r requirements.txt
uvicorn backend.main:app --port 8000 &

# Dashboard
cd dashboard && npm install && npm run dev
# → http://localhost:5173
```

## Project Structure

```
├── agents/          # 100 YAML agent knowledge bases
├── knowledge/       # Domain knowledge bases
├── core/            # Core modules (normalizer, learning, etc.)
├── backend/         # FastAPI routes
├── dashboard/       # Vite + React UI
├── configs/         # Settings, seasonal rules
├── consciousness.py # Memory + learning engine
├── hivemind.py      # Orchestrator (~1400 lines)
├── translation_proxy.py # FI↔EN translation
└── main.py          # Entry point
```

## Current Status

- ✅ Phase 1: Foundation (consciousness v2, dual embed, smart router)
- ✅ Phase 2: Batch Pipeline (94% benchmark, 3,148 facts)
- ✅ Phase 3: Social Learning (Round Table, agent levels)
- 🔄 Phase 4: Advanced Learning (contrastive, active, bilingual index)
- 📋 Phase 5-11: Camera, Audio, Voice, Weather, Auto-learning, MicroModel, Scaling

## Credits

- 99% — **Claude OPUS 4.6** (Anthropic) — architecture, code, agents
- 1% — **Jani Korpi** 🐝 — vision, direction, domain expertise

## License

MIT — Free to use, modify, distribute.

⚠️ **DISCLAIMER:** This self-evolving AI is provided AS-IS. The developer assumes zero responsibility for any consequences. Use at your own risk in a controlled environment.

---
*Ahkerat Mehiläiset • Helsinki, Finland • 2024-2026*
