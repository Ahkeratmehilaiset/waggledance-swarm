# WAGGLEDANCE SWARM AI — PROJECT BRIEF FOR AI AGENTS
# Auto-generated: 2026-02-25 12:13
# Purpose: Paste this into any AI prompt to get improvement suggestions.

## WHAT IS THIS
WaggleDance is a local-first, self-improving multi-agent AI system for Finnish
beekeeping (mehiläishoito). 100 specialized agents communicate through a
HiveMind orchestrator with translation pipeline, consciousness layer, and
autonomous learning. All user I/O in Finnish, all LLM processing in English.

## HARDWARE
- GPU: NVIDIA RTX A2000 8GB Laptop GPU (8.0 GB VRAM)
- RAM: 127.2 GB
- OS: Windows 11
- Python: 3.13.7

## CODEBASE STATS
- Files: 304 (82 .py, 157 .yaml, 3 .jsx) — 125,551 lines of code
- Agents: 100 (core_dispatcher, elokuva_asiantuntija, entomologi, erakokki, fenologi, hortonomi, ilmanlaatu, inventaariopaallikko, jaaasiantuntija, kalantunnistaja, ...)
- ChromaDB facts: 3363
- Ollama models: claude-haiku-4-5-20251001, llama3.2:1b, phi4-mini
- Python deps: aiohttp, aiosqlite, anthropic, chromadb, distill_from_opus, duckduckgo_search, fastapi, feedparser, fitz, httpx, libvoikko, nltk, peft, psutil, pydantic, requests, routes, starlette, torch, transformers

## ARCHITECTURE
```
User (Finnish) -> FastAPI backend (port 8000)
  +-- Chat Router: 3-layer matching system
  |   Layer 2A: YAML eval_questions (high confidence, F1 >= 0.5, overlap >= 2)
  |   Layer 1:  Hand-crafted Finnish keyword matching (47 entries)
  |   Layer 2B: YAML eval_questions (lower threshold, F1 >= 0.4)
  |   Layer 3:  Fallback messages
  +-- HiveMind orchestrator (hivemind.py ~1400 lines)
  |   +-- Priority Lock: chat always wins, pauses background
  |   +-- Heartbeat: llama3.2:1b guided learning tasks
  |   +-- Round Table: 6 agents discuss + cross-validate
  +-- Consciousness v2 (consciousness.py ~500 lines)
  |   +-- MathSolver pre-filter (0ms)
  |   +-- ChromaDB vector memory (bilingual)
  |   +-- Dual embedding: nomic (search) + minilm (eval)
  |   +-- Hallucination detection (contrastive + keyword)
  +-- Translation: Opus-MT fi<->en (force_opus for chat)
  +-- Dashboard: Vite + React (port 5173)
  +-- 50 YAML agent knowledge bases (knowledge/ + agents/)
```

## CURRENT ROUTING PERFORMANCE
3-layer routing (Layer2A YAML high-conf -> Layer1 keywords -> Layer2B YAML low-thresh -> fallback), bidirectional F1 scoring, agent name bonus, 97.7% routing accuracy across 50 agents

## COMPLETED PHASES
- Phase 1: Foundation (consciousness v2, dual embed, smart router)
- Phase 2: Batch Pipeline (94% benchmark, 3147+ facts in ChromaDB)
- Chat routing: 97.7% accuracy across 1235 eval_questions from 50 agents

## IN PROGRESS
- Phase 3: Social Learning (Round Table, agent levels, night mode)
- Phase 4: Advanced Learning (contrastive, active, RAG, episodic)

## PLANNED (not started)
- Phase 5: Frigate Camera Integration (MQTT, PTZ, visual learning)
- Phase 6: Environmental Audio (ESP32, BirdNET, BeeMonitor)
- Phase 7: Voice Interface (Whisper STT + Piper TTS)
- Phase 8: External Data Feeds (FMI weather, electricity spot, RSS)
- Phase 9: Autonomous Learning Engine (6 layers: cache, enrichment, web, distill, meta, code-review)
- Phase 10: Micro-Model Training (pattern match -> classifier -> LoRA fine-tune)
- Phase 11: Elastic Hardware Scaling (auto-detect GPU/RAM, tier configuration)

## KEY DESIGN PRINCIPLES
1. Speed + Memory replaces model size (phi4-mini 3.8B with good memory > 32B without)
2. Batch background, stream foreground (Theater Pipe: batch hidden, 300ms delays)
3. Cross-validation > model intelligence (6 agents checking each other)
4. Chat always wins (PriorityLock pauses all background)
5. Learn from everything (YAML, conversations, corrections, web, cameras, audio)
6. Autonomous evolution (identify gaps, fill, validate, optimize, suggest code changes)

## KEY FILES
- main.py: Entry point
- hivemind.py: Core orchestrator (~1400 lines)
- consciousness.py: Memory + learning engine (~500 lines)
- translation_proxy.py: FI<->EN translation (~400 lines)
- backend/routes/chat.py: Chat endpoint with 3-layer routing
- dashboard/src/App.jsx: React dashboard UI
- configs/settings.yaml: System configuration
- core/yaml_bridge.py, core/llm_provider.py, core/en_validator.py
- agents/*/core.yaml: Agent knowledge bases (50 agents)
- knowledge/*/core.yaml: Domain knowledge bases

## RECENT IMPROVEMENTS (latest session)
- Chat routing accuracy: 73.5% -> 80.6% -> 92.3% -> 97.7% (4 rounds)
- Bidirectional F1 scoring with agent name bonus and depth bonus
- _resolve_ref fallback: tries parent paths and alternative fields
- Dedup fix: only mark question as seen after valid answer resolves
- Generic cross-agent question filtering (skip identical questions)
- Concurrent mass testing (10 workers, 5 q/s)
- YAML index: 5-tuple with agent name tokens for better matching
- Stop words expanded for Finnish action verbs

## CONSTRAINTS
- All LLM internally ENGLISH, Finnish only for user I/O
- Opus-MT for translation (force_opus=True for chat)
- nomic-embed-text: "search_document:"/"search_query:" prefixes
- phi4-mini ONLY for chat, llama3.2:1b for ALL background
- TranslationResult.text — never concatenate directly
- UTF-8 everywhere, Windows 11 compatible
- GPU budget: 4.3G/8.0G (54%) — 3.7G free

## WHAT WOULD MAKE THIS BETTER?
Think about:
1. How to improve agent routing beyond 97.7%? (remaining failures are shared-knowledge overlaps)
2. How to make the Round Table protocol produce higher-quality cross-validated wisdom?
3. How to implement autonomous fact enrichment that generates AND validates new knowledge?
4. How to make the system learn from its own mistakes (contrastive learning from corrections)?
5. How to reduce response latency (currently: cache 5ms, chromadb 55ms, llm 500-3000ms)?
6. How to implement micro-model training that progressively replaces LLM calls?
7. What architectural changes would enable true autonomous self-improvement?
8. How to handle Finnish morphology better in keyword matching (14 cases, compound words)?

Suggest specific code changes, new algorithms, or architectural improvements.
