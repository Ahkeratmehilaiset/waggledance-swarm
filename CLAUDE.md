# WAGGLEDANCE SWARM AI — COMPLETE MASTER SPECIFICATION
# v0.2.0 Architecture & Implementation Guide
# For Claude Code — English Internal Reference
# Date: 2026-02-24 | Developer: Jani Korpi / JKH Service (Y-2828492-2)

---

## TABLE OF CONTENTS

```
PART I — FOUNDATION
  1.  Project Overview & Developer Context
  2.  Hardware & GPU Allocation
  3.  Core Architecture & Design Principles
  4.  Existing Codebase (preserve these)
  5.  Critical Rules (apply to ALL phases)

PART II — CORE PHASES (v0.1.0)
  6.  Phase 1: Foundation (consciousness, router, priority)
  7.  Phase 2: Batch Pipeline (embed, translate, scan)
  8.  Phase 3: Social Learning (Round Table, agents, night mode)
  9.  Phase 4: Advanced Learning (contrastive, active, RAG, episodic)

PART III — SENSORS & PERCEPTION (v0.1.5)
  10. Phase 5: Frigate Camera Integration (MQTT, PTZ, visual learning)
  11. Phase 6: Environmental Audio Sensors (ESP32, BirdNET, BeeMonitor)
  12. Phase 7: Voice Interface (Whisper STT + Piper TTS)

PART IV — EXTERNAL DATA (v0.1.8)
  13. Phase 8: External Data Feeds (RSS, electricity, weather APIs)

PART V — AUTONOMOUS INTELLIGENCE (v0.2.0)
  14. Phase 9: Autonomous Learning Engine (6 layers)
  15. Phase 10: Micro-Model Training (local fine-tuning)
  16. Phase 11: Elastic Hardware Scaling (auto-config)

PART VI — USER INTERFACE & OPS
  17. Dashboard UI Specification
  18. Deployment, Backup & Monitoring

APPENDICES
  A. GPU/RAM Budget Calculator
  B. File Structure Map
  C. Testing Checklist (all phases)
  D. Glossary (Finnish beekeeping terms)
```

---

# PART I — FOUNDATION

---

## 1. PROJECT OVERVIEW & DEVELOPER CONTEXT

WaggleDance is a local-first, multi-agent AI system for Finnish beekeeping (mehiläishoito).
50+ specialized agents communicate through a HiveMind orchestrator with a translation
pipeline, consciousness layer, and autonomous learning capabilities.

**Developer:** Jani Korpi, 52-year-old Finnish beekeeper with 20+ years experience.
~300 bee colonies across Helsinki metro area and Kouvola/Huhdasjärvi.
Produces ~10,000 kg honey/year. Sells via Wolt, online, and direct sales.
Business: JKH Service (Y-tunnus: 2828492-2).

**Key constraint:** All user-facing output MUST be in Finnish.
All internal LLM processing is in English (better model performance).
Translation via Helsinki-NLP Opus-MT models (GPU-accelerated).

**Current status (2026-02-24):**
- Phase 1: ✅ Complete (consciousness v2, dual embed, smart router)
- Phase 2: ✅ Complete (batch pipeline, 94% benchmark, 3147 facts in ChromaDB)
- Phase 3: ⏳ In progress (Round Table, agent levels, night mode)
- Phases 4-11: 📋 Specified in this document

---

## 2. HARDWARE & GPU ALLOCATION

### Primary Development Machine
```
GPU:  NVIDIA RTX A2000 8GB VRAM (Ampere, FP16 capable)
CPU:  AMD/Intel (multi-core)
RAM:  128 GB
Disk: NVMe SSD
OS:   Windows 11
```

### GPU Model Allocation (6 models simultaneously)
```
MODEL               VRAM    ROLE
phi4-mini           2.5G    Chat responses (English internally)
llama3.2:1b         0.7G    ALL background work (heartbeat, learning, Round Table)
nomic-embed-text    0.3G    SEARCH embeddings (ChromaDB retrieval)
all-minilm          0.2G    EVAL embeddings (hallucination, dedup, clustering)
Opus-MT FI→EN       0.3G    Translation input (HuggingFace PyTorch)
Opus-MT EN→FI       0.3G    Translation output
───────────────────────────────────────────────
TOTAL:              4.3G / 8.0G (54%) | FREE: 3.7G
```

### Ollama Configuration
```
OLLAMA_MAX_LOADED_MODELS=4
OLLAMA_KEEP_ALIVE=24h
```

### Future Hardware (separate devices)
```
Frigate NVR:     Raspberry Pi 5 + Google Coral USB TPU (~110€)
Audio sensors:   ESP32-S3 boards with I2S MEMS microphones (~15€/each)
Cameras:         RTSP IP cameras, Reolink/Hikvision (~40-80€/each)
```

---

## 3. CORE ARCHITECTURE & DESIGN PRINCIPLES

### System Architecture
```
User (Finnish) → HiveMind
  ├─ PRIORITY GATE: Chat? → ALL GPU to chat, pause learning
  ├─ SmartRouter (confidence-based):
  │   >0.90 → MicroModel / Cache (0-5ms, no LLM)
  │   >0.70 → ChromaDB + llama1b (500ms)
  │   >0.50 → phi4-mini + context (3000ms)
  │   web   → llama1b + web results (1500ms)
  │   none  → Active Learning: ask user
  ├─ Translation: Opus-MT chat (quality) / dict heartbeat (speed)
  ├─ Consciousness Layer:
  │   ├─ MathSolver pre-filter (0ms, no LLM)
  │   ├─ ChromaDB vector memory (persistent, bilingual index)
  │   ├─ Dual embedding: nomic (search) + minilm (eval)
  │   ├─ Batch operations (embed, translate, store)
  │   ├─ Hallucination detection (contrastive + keyword + embedding)
  │   ├─ Hot Answer Cache (top 500, 5ms)
  │   ├─ Active Learning (ask when uncertain)
  │   ├─ Agent levels 1-5 (earned by performance)
  │   └─ Embedding augmentation (domain synonyms)
  ├─ Swarm Learning (background):
  │   ├─ YAML Scanner: bulk import (no LLM, embed only, 600 facts/30s)
  │   ├─ Heartbeat: guided tasks via llama1b ONLY
  │   ├─ Round Table: 6 agents discuss + cross-validate + synthesize
  │   ├─ Batch Pipeline: GPU at 60-80% utilization
  │   ├─ Theater Pipe: batch hidden, stream results with 300ms gaps
  │   ├─ Night Mode: aggressive learning when user idle
  │   ├─ Fact Enrichment: generate + validate new knowledge
  │   ├─ Web Learning: search web for knowledge gaps
  │   ├─ MicroModel Training: LoRA fine-tune on local data
  │   └─ Meta-Learning: optimize own performance
  ├─ Sensor Integration (optional):
  │   ├─ Frigate MQTT: camera events → ChromaDB
  │   ├─ PTZ Controller: AI-directed camera movement
  │   ├─ ESP32 Audio: hive sounds → stress detection
  │   ├─ Weather API: forecast → seasonal context
  │   └─ Electricity API: spot prices → cost optimization
  ├─ Adaptive Throttle:
  │   ├─ Startup benchmark (optimal batch sizes for THIS hardware)
  │   ├─ Runtime VRAM monitor + user activity detection
  │   ├─ Chat active → pause learning
  │   └─ Hardware profiler → elastic scaling
  └─ Dashboard: FastAPI + WebSocket (live stream)
```

### Design Principles

**1. Speed + Memory Replaces Model Size**
phi4-mini (3.8B) with good memory outperforms 32B without memory.
LLM role shrinks over time: 100% day1 → 15% month6 as memory grows.
llama1b handles 80% of responses (memory lookup + formatting).

**2. Batch Background, Stream Foreground (Theater Pipe)**
GPU does batch work invisibly (6 agents in 2 seconds).
Dashboard shows results one-by-one with 300ms delays.
Looks like agents chatting in real-time.

**3. Cross-Validation Over Model Intelligence**
6 agents checking each other > 1 large model guessing alone.
Round Table consensus (confidence 0.85) > single answer (0.50).

**4. Chat Always Wins (Priority Lock)**
User types → ALL background pauses → phi4-mini answers → learning resumes.

**5. Learn From Everything**
YAML files, conversations, corrections, heartbeats, Round Tables,
web search, night scanning, camera observations, audio sensors, user feedback.

**6. Autonomous Evolution**
System identifies knowledge gaps, fills them, validates results,
optimizes its own performance, trains micro-models, and suggests code improvements.

**7. Elastic Scaling**
Same code runs on 4GB laptop or 160GB server.
Hardware profiler auto-configures models, agents, and parallelism.

---

## 4. EXISTING CODEBASE (PRESERVE)

### hivemind.py (~1400 lines) — EXTEND, don't rewrite
- HiveMind class with chat(), _delegate_to_agent()
- Heartbeat: _agent_proactive_think(), _master_generate_insight()
- AdaptiveThrottle (EXTEND with batch benchmark)
- WebSocket: _notify_ws()
- Translation: self.translation_proxy

### consciousness.py (~500 lines, v2) — REWRITE to v3
Working: MathSolver, ChromaDB, nomic-embed, embedding cache
Broken: hallucination detection (false positives, wrong weights)
Missing: batch ops, dual embed, smart router, round table, agent levels

### translation_proxy.py (~400 lines) — ADD batch methods
Working: fi_to_en(), en_to_fi() with force_opus
Missing: batch_fi_to_en(), batch_en_to_fi()
CRITICAL: Returns TranslationResult objects — always use .text attribute

### Other files:
- core/yaml_bridge.py — YAML agent data loader
- core/llm_provider.py — Ollama API wrapper
- core/en_validator.py — English output validator
- web/dashboard.py — FastAPI + WebSocket dashboard
- configs/settings.yaml — System configuration
- main.py — Entry point

---

## 5. CRITICAL RULES (apply to ALL phases)

```
1.  All LLM internally in ENGLISH — Finnish only for user I/O
2.  Opus-MT for translation (force_opus=True for chat)
3.  nomic-embed-text: "search_document:" / "search_query:" prefixes
4.  all-minilm: NO prefix (symmetric model)
5.  ChromaDB cosine distance, score = 1.0 - (dist / 2.0)
6.  TranslationResult.text — never concatenate TranslationResult directly
7.  phi4-mini ONLY for chat — never heartbeat/learning
8.  llama3.2:1b for ALL background — fast and cheap
9.  Batch where possible — GPU likes batches not single calls
10. Chat ALWAYS wins — PriorityLock pauses all background work
11. UTF-8 everywhere — Finnish characters (ä, ö, å)
12. Windows 11 compatibility (all code must run on Windows)
13. Test every phase before proceeding to next
14. Log everything — data/learning_metrics.jsonl for analysis
15. Graceful degradation — missing sensors/cameras/APIs → log warning, continue
16. All observations → ChromaDB with appropriate category tags
17. Never block main thread — use asyncio, background threads
18. All dates in ISO 8601, all times in UTC internally
```

---

# PART II — CORE PHASES (v0.1.0)

---

## 6. PHASE 1: Foundation

**Status: ✅ COMPLETE**
**Files:** consciousness.py (rewrite), hivemind.py (extend), tools/scan_knowledge.py (new)

### Task 1a: YAML Knowledge Scanner (tools/scan_knowledge.py)
- Parse ALL yaml in knowledge/ and agents/ directories
- Extract facts as natural language Finnish sentences
- Store via consciousness.learn() — NO LLM, just embed+store
- Track progress in data/scan_progress.json (idempotent)
- Target: 600+ facts in <5 minutes
- Auto-run on first startup if consciousness.memory.count < 100

### Task 1b: Smart Router (consciousness.py / hivemind.py)
- Confidence-based model selection:
  - >0.90 validated → direct answer, no LLM
  - >0.70 → llama1b formats answer with context
  - >0.50 → phi4-mini with full context
  - <0.50 → active learning ("En tiedä, kerro minulle?")

### Task 1c: Split Heartbeat Roles (hivemind.py)
- phi4-mini: ONLY user chat (never heartbeat)
- llama3.2:1b: ALL heartbeat/learning/consolidation

### Task 1d: Chat Priority Lock (hivemind.py)
- PriorityLock class with chat_enters/chat_exits/batch_checkpoint
- Chat request → set flag → batch workers pause at checkpoints
- Chat done → clear flag → workers resume

### Task 1e: Fix Hallucination Detection (consciousness.py)
- Weights: 0.3 * embedding_similarity + 0.7 * keyword_overlap
- Threshold: 0.45 (was 0.30)
- Empty q_words → return 0.0 (was 1.0 — free pass bug)
- Hard gate: overlap==0 AND similarity<0.65 → suspicious

### Task 1f: Seed ChromaDB (automatic)
- Run scan_knowledge.py on first startup
- Result: 3147 facts in ChromaDB (achieved)

### Test Phase 1
```
python consciousness.py  → math 9/9, hallucination 4/4, search 4/5+
Ask: "mikä on varroa-kynnys" → correct answer from memory
Ask: "laske 2+2" → "4" (0ms, math pre-filter)
```

---

## 7. PHASE 2: Batch Pipeline

**Status: ✅ COMPLETE (94% benchmark, 3147 facts)**
**Files:** consciousness.py, translation_proxy.py, hivemind.py

### Task 2a: Batch Embedding (consciousness.py)
```python
embed_batch(texts: list[str], mode="document"|"query") -> list[list[float]]
# Ollama /api/embed with list input, max batch 50
eval_embed_batch(texts: list[str]) -> list[list[float]]
# all-minilm batch, NO prefix
dual_embed_batch(texts: list[str]) -> tuple[list, list]
# Both models concurrent via asyncio.to_thread
```

### Task 2b: Batch Translation (translation_proxy.py)
```python
batch_fi_to_en(texts: list[str]) -> list[str]
# HuggingFace batch tokenize+generate, max batch 20
batch_en_to_fi(texts: list[str]) -> list[str]
```

### Task 2c: Learn Queue with Batch Flush (consciousness.py)
- self._learn_queue: list of (text, metadata) tuples
- learn() appends to queue
- _flush_learn_queue() when queue >= 10: batch translate, embed, store
- flush() at shutdown or explicit call

### Task 2d: Dual Embedding Setup
- Auto-pull all-minilm if not installed
- Warmup both models at startup
- all-minilm for: hallucination check, dedup, clustering

### Task 2e: Adaptive Throttle Benchmark
- On startup: benchmark embed batch 1/5/10/20/50
- Benchmark translate batch 1/5/10/20
- Benchmark LLM parallel 1/2/3/4/6
- Store optimal values for runtime use

### Task 2f: Update scan_knowledge.py to Use Batching
- Collect 50 facts → batch operations → batch ChromaDB insert
- Result: entire knowledge/ directory in <30 seconds

### Test Phase 2
```
94% benchmark pass rate (60/64 questions)
3147 unique facts in ChromaDB (5522 scanned, 43% dedup filtered)
Embedding batch 10: <400ms
```

---

## 8. PHASE 3: Social Learning

**Status: ⏳ IN PROGRESS**
**Files:** consciousness.py, hivemind.py, core/agent_levels.py (new), web/dashboard.py

### Task 3a: Agent Level System (core/agent_levels.py)

AgentLevelManager class. Stats in ChromaDB "agent_stats" collection.

Per agent: {agent_id, level, total_responses, correct_responses,
  hallucination_count, user_corrections, trust_score,
  last_promoted, last_demoted, specialties[]}

```
Level 1 NOVICE:      Memory-only, all checked, max 200 tok
Level 2 APPRENTICE:  +LLM+memory, can read swarm_facts
  Earn: 50 correct, halluc <15%, trust >0.3
Level 3 JOURNEYMAN:  +write swarm_facts, consult 1 agent
  Earn: 200 correct, halluc <8%, trust >0.6
Level 4 EXPERT:      +consult 3 agents, web search, skills
  Earn: 500 correct, halluc <3%, trust >0.8
Level 5 MASTER:      Full autonomy, teach others
  Earn: 1000 correct, halluc <1%, trust >0.95
DEMOTION: halluc exceeds threshold over 50-window → drop 1 level
```

### Task 3b: Round Table Protocol (hivemind.py)

```python
async def _round_table(self, topic, agent_count=6):
    # Phase 1 — Selection: Pick agents by topic relevance + level
    # Phase 2 — Discussion: Each agent sees previous responses (sequential)
    # Phase 3 — Synthesis: Queen agent summarizes consensus
    # Phase 4 — Storage: Store as wisdom (confidence=0.85, source="round_table")
```
Run every 20th heartbeat cycle. Use llama1b for all agents (fast).

### Task 3c: Theater Pipe (hivemind.py)
Round Table computed in batch internally.
Results streamed to dashboard with 300ms delays between messages.

### Task 3d: Guided Heartbeat Tasks (consciousness.py)
LearningTaskQueue class: next_task() returns specific learning task.
Priority: 1) unread YAML, 2) low-coverage topics, 3) recent user Qs,
4) cross-agent gaps, 5) seasonal, 6) random exploration.

### Task 3e: Night Mode (hivemind.py)
- Detect user idle >30 minutes (no WebSocket messages)
- Heartbeat interval → 10-15 seconds
- Focus: systematic YAML scan, consolidation every 100 facts
- Progress: data/learning_progress.json
- Stop: all processed OR user returns OR 8 hours

### Task 3f: Dashboard Updates (web/dashboard.py)
WebSocket events: round_table_start, round_table_insight, round_table_end.
Agent levels visible. Swarm facts collection shared.

### Test Phase 3
```
Round Table produces cross-validated wisdom
Agent levels increment after correct responses
Night mode processes knowledge files systematically
Dashboard shows live Round Table conversations
```

---

## 9. PHASE 4: Advanced Learning

**Status: 📋 SPECIFIED**
**Files:** consciousness.py, hivemind.py, tools/distill_from_opus.py (new)

### Task 4a: Contrastive Learning
Detect user corrections → store in "corrections" collection.
Future similar query → inject correction context into prompt.
Agent trust -= 0.05 on correction.

### Task 4b: Active Learning
Memory search best score < 0.50 → don't hallucinate, ask user.
Return: "En ole varma. Tiedätkö mikä on [topic]?"
Detect user teaching → store confidence=0.9, source="user_teaching".

### Task 4c: Embedding Augmentation
On learn(): load dict_fi_en.json. Append English synonyms to domain terms.
"toukkamätä" → embed as "Foulbrood | AFB | Paenibacillus larvae"

### Task 4d: Multi-hop RAG
First search → extract entities → second search → deduplicate → top 5.
Only when first search < 0.70. Max 2 hops. Cache intermediate results.

### Task 4e: Episodic Memory
ChromaDB "episodes" collection. Conversation turns linked by session_id.
Search returns entire conversation chains when relevant.

### Task 4f: Seasonal/Curriculum Learning
Month-based keyword boosting (1.2x) in search results.
Jan: talvehtiminen, Feb: kevättarkastus, Jul: linkous, Sep: varroa...

### Task 4g: Knowledge Distillation Prep (tools/distill_from_opus.py)
Collect failed queries → data/failed_queries.jsonl.
Import function for expert answers → ChromaDB confidence=0.95.
Pipeline ready, actual API call is a placeholder.

### Task 4h: Feedback Dashboard Endpoint
GET /api/consciousness → JSON stats:
memory_count, cache_hit_rate, hallucination_rate, agent_levels,
recent_corrections, seasonal_focus, learning_trend, top_queries, weakest_agent.

### Task 4i: Bilingual ChromaDB Index (NEW)
Store facts in BOTH English and Finnish ChromaDB collections.
Finnish query → search Finnish index directly → skip translation.
Response time: 355ms → 55ms for high-confidence hits.

### Task 4j: Hot Answer Cache (NEW)
Top 500 most-asked questions cached in RAM with Finnish answers.
Finnish stemming normalization for cache key matching.
Response time: 5ms for cache hits. Zero GPU usage.

### Task 4k: Fact Enrichment Engine (NEW)
Night mode generates new knowledge: find gaps → generate with llama1b →
validate with phi4-mini → store if both agree. ~200 new facts/night.

### Test Phase 4
```
User correction → stored, prevents same mistake
Active learning → asks user, stores answer
Bilingual index → Finnish queries answered in 55ms
Hot cache → repeat questions answered in 5ms
Enrichment → autonomous fact generation validated
Full system running 24/7 autonomously
Dashboard shows comprehensive learning metrics
When all pass: "PHASE 4 COMPLETE — v0.1.0 READY"
```

---
# PART III — SENSORS & PERCEPTION (v0.1.5)

---

## 10. PHASE 5: Frigate Camera Integration

**Status: 📋 SPECIFIED**
**Files:** integrations/frigate_mqtt.py (new), integrations/alert_dispatcher.py (new),
integrations/ptz_controller.py (new), integrations/visual_learning.py (new),
agents/kameravahti.yaml (new)

### Overview

Frigate is an open-source NVR running YOLO object detection on camera feeds via
Google Coral TPU. Publishes events via MQTT. WaggleDance subscribes, stores
observations in ChromaDB, and injects camera context into agent prompts.

```
PIPELINE:
Cameras → Frigate (YOLO on Coral TPU) → MQTT → WaggleDance
                                                  ├─ Events → ChromaDB
                                                  ├─ Alerts → Telegram/webhook
                                                  ├─ PTZ AI Director → camera movement
                                                  ├─ Visual Learning → pattern/anomaly
                                                  └─ Cross-Sensor → audio+weather fusion
```

### Hardware (separate from WaggleDance PC)
- Raspberry Pi 5 + Google Coral USB TPU (~110€)
- PTZ cameras: Reolink RLC-823A (~100€) or Dahua SD22404DB-GNY (~200€)
- ONVIF + RTSP required for PTZ autotracking

### Task 5a: MQTT Client (integrations/frigate_mqtt.py)
- paho-mqtt subscription to frigate/events, frigate/+/motion, frigate/available
- Event deduplication via event_id cache (TTL 5 min)
- Store all detections in ChromaDB (category="camera_event")
- Alert rule evaluation (bear, night person, dog near hives)
- Graceful degradation if MQTT/Frigate unavailable
- Background thread, never blocks chat

### Task 5b: Alert Dispatcher (integrations/alert_dispatcher.py)
- Multi-channel routing: Telegram, webhook, dashboard WebSocket
- Severity levels: info, medium, high, critical
- Telegram sends text + snapshot image for critical/high alerts
- Alert rules (configurable in settings.yaml):
  - Bear detected → CRITICAL (immediate Telegram with snapshot)
  - Person at hive 22:00-05:00 → HIGH (suspicious)
  - Person at cottage daytime → INFO (visitor logged)
  - Dog near hives → MEDIUM
  - No motion >4h when temp>10°C → WARNING

### Task 5c: PTZ Intelligence Layer (integrations/ptz_controller.py)

TWO LAYERS of camera control:

**Layer 1: Frigate Autotracking (built-in, free)**
- Object enters zone → camera follows automatically
- Configured in frigate.yml autotracking section
- Works without WaggleDance running

**Layer 2: WaggleDance AI Director (this task)**
- Patrol mode: rotate through preset positions on schedule
- Smart focus: AI chooses what to look at based on context
  - Dawn/dusk → wildlife approach path
  - Summer midday → hive entrance (bee activity)
  - ESP32 hears stress sound → zoom to that hive
  - User asked about cottage → point camera there
- Event-driven: sensor alert → redirect camera
- Patrol PAUSES when Frigate autotracking is active
- Uses ONVIF protocol via onvif_zeep Python library

```python
class PTZController:
    async def goto_preset(camera, preset_name)     # Named positions
    async def look_at_position(camera, pan, tilt, zoom)  # Absolute
    async def patrol(camera, presets, loop=True)    # Cycle presets
    async def smart_focus(camera, reason, consciousness)  # AI-directed
```

### Task 5d: Visual Learning Engine (integrations/visual_learning.py)

THREE LEARNING MODES:

**1. Pattern Learning (passive, continuous)**
- What's normal for this camera at this time of day?
- How often do people/animals appear?
- Typical bee activity level by hour/season

**2. Anomaly Detection (real-time)**
- First bear ever seen → CRITICAL (new threat)
- Person at 3 AM → UNUSUAL (never seen at this time)
- No bee activity at 2 PM in July → CONCERNING
- 50 detections in 1 minute → possible false positive

**3. Insight Generation (periodic, via Round Table)**
- "Bee activity decreased 30% this week"
- "A person visits every Tuesday around 14:00"
- "Hive 27 entrance shows less traffic than hive 12"

Builds hourly baseline after 7+ days of data.
Generates daily summaries in Finnish → ChromaDB.
Weekly trends fed to Round Table for cross-validation.

### Task 5e: Cross-Sensor Intelligence
Camera + Audio + Weather combined decisions:
- Camera sees person + ESP32 hears speech → normal visitor
- Camera sees person + NO speech + night → suspicious, alert
- Camera sees high bee activity + audio stress frequency → swarming alert
- Many birds near hives → possibly dead bees (food source)

### Task 5f: Environment Context Integration (consciousness.py)
Update get_environment_context() to include camera observations:
- Recent events (24h): "📹 Camera tarha_pesa: person detected at 14:30 (95%)"
- Active alerts: "🚨 ALERT high: Person at hive area at night"
- Vision analysis: "👁️ Normal bee activity, 3 bees at entrance"
- ALL agents automatically see camera context in prompts

### Task 5g: KameraAgent (agents/kameravahti.yaml)
New agent: 📹 security monitoring specialist
Specialties: camera_monitoring, object_detection, security_assessment, wildlife_id
Participates in Round Table for camera-related topics.

### Task 5h: Settings Configuration
```yaml
frigate:
  enabled: false  # MUST be optional
  frigate_url: "http://192.168.1.100:5000"
  mqtt: {host: "192.168.1.100", port: 1883}
  cameras:
    tarha_pesa: {description: "Hive area overview"}
    mokki_piha: {description: "Cottage yard"}
  alerts:
    telegram: {bot_token: "", chat_id: ""}
  ptz:
    enabled: false
    patrol_presets: ["kotiasema", "lentoaukko_zoom", "polku"]
  vision:
    enabled: false
    scheduled_check_hours: [8, 12, 16, 20]
```

### Test Phase 5
```
1.  frigate.enabled=false → no errors, no camera data
2.  frigate.enabled=true, no MQTT → logs warning, continues
3.  Mock MQTT event → appears in ChromaDB
4.  Bear detection → Telegram alert fires
5.  Night person → alert with correct time check
6.  Activity spike (25/hour) → anomaly detected
7.  First-ever "cat" → first_sighting anomaly
8.  Daily summary generates Finnish text
9.  PTZ patrol cycles through presets
10. Smart focus triggered by audio alert → camera zooms
11. Round Table discusses camera observations
12. Dashboard shows camera events real-time
When all pass: "PHASE 5 COMPLETE"
```

---

## 11. PHASE 6: Environmental Audio Sensors

**Status: 📋 NEW SPECIFICATION**
**Files:** integrations/audio_monitor.py (new), integrations/bee_audio.py (new),
integrations/bird_monitor.py (new), agents/aarivahti.yaml (new)

### Overview

ESP32-S3 microcontrollers with MEMS microphones placed at hive entrances and
cottage areas. Audio data processed locally on ESP32 (simple FFT) or streamed
to WaggleDance for ML classification.

```
PIPELINE:
ESP32 + mic → WiFi/MQTT → WaggleDance
                             ├─ YAMNet (Google): general audio classification
                             ├─ BeeMonitor: hive health from sound frequency
                             ├─ BirdNET (Cornell): bird species identification
                             └─ Speech detection: visitor awareness
```

### Hardware Per Sensor Node (~15€ each)
```
ESP32-S3-DevKitC-1      ~8€   (WiFi + BLE + I2S audio)
INMP441 MEMS microphone ~3€   (I2S digital, -26dB SNR)
USB-C power supply       ~4€
Weatherproof enclosure   ~5€   (3D printed or IP65 box)
Total per node:         ~20€
Recommended: 5 nodes (3 hives + cottage + path) = ~100€
```

### Task 6a: ESP32 Audio Firmware (esp32/hive_audio.ino)
```
ESP32 firmware responsibilities:
1. Capture I2S audio at 16kHz, 16-bit mono
2. On-device FFT: compute frequency spectrum (256-point)
3. Detect events locally:
   - Loud sound (>threshold) → send alert immediately
   - Periodic spectrum → send every 60s as compressed payload
4. MQTT publish to waggledance/audio/{node_id}/event
5. Deep sleep between captures (battery mode option)
6. OTA firmware updates via WiFi

MQTT topics:
  waggledance/audio/{node_id}/spectrum   → 256-float FFT data
  waggledance/audio/{node_id}/event      → {type, confidence, timestamp}
  waggledance/audio/{node_id}/status     → {battery, uptime, temp}
```

### Task 6b: Audio Monitor Service (integrations/audio_monitor.py)
```python
class AudioMonitorService:
    """Receives audio data from ESP32 nodes via MQTT."""

    def __init__(self, mqtt_client, consciousness):
        self.nodes = {}  # node_id → config
        self.classifiers = {
            "yamnet": YAMNetClassifier(),      # General sounds
            "bee_audio": BeeAudioAnalyzer(),   # Hive-specific
            "birdnet": BirdNETClassifier(),    # Bird species
        }

    async def on_spectrum(self, node_id, fft_data):
        """Process incoming FFT spectrum from ESP32."""
        # 1. Classify with YAMNet
        yamnet_result = self.classifiers["yamnet"].classify(fft_data)
        # Results: "speech", "dog_bark", "chainsaw", "bird", "insect_buzz"

        # 2. If hive node → analyze bee audio
        if self.nodes[node_id]["type"] == "hive":
            bee_result = self.classifiers["bee_audio"].analyze(fft_data)
            # Results: "normal_hum", "queen_piping", "stress_buzz",
            #          "swarming_roar", "queenless_howl"

        # 3. If bird sound → identify species
        if "bird" in yamnet_result.labels:
            bird_result = self.classifiers["birdnet"].identify(fft_data)
            # Results: species name + confidence

        # 4. Store in ChromaDB
        self.consciousness.learn(
            f"Audio {node_id}: {result.label} detected (confidence {result.score:.0%})",
            category="audio_event",
            node_id=node_id,
        )

        # 5. Alert if critical
        if bee_result and bee_result.label in ("stress_buzz", "swarming_roar"):
            await self.alert_dispatcher.send(
                severity="high",
                message=f"Hive audio alert: {bee_result.label} at {node_id}",
            )
            # Also trigger PTZ camera focus
            await self.ptz_controller.smart_focus("tarha_pesa", "hive_stress")
```

### Task 6c: Bee Audio Analyzer (integrations/bee_audio.py)
```python
class BeeAudioAnalyzer:
    """
    Analyze hive sounds for health indicators.
    Based on published research on bee acoustics.

    KEY FREQUENCIES:
    - Normal colony hum: 200-500 Hz (fundamental ~250 Hz)
    - Queen piping: 400-500 Hz (distinctive pulsed pattern)
    - Swarming: broad 200-600 Hz with increased amplitude
    - Queenless: higher pitch, 300-600 Hz, irregular
    - Stress/disturbance: increased amplitude + higher frequencies
    - Varroa-stressed: subtle shift to higher fundamental (~300 Hz)

    METHOD:
    1. FFT spectrum → extract fundamental frequency
    2. Compare to baseline (built over 7+ days per hive)
    3. Detect patterns: piping, swarming roar, stress buzz
    4. Report changes: "Hive 12 fundamental shifted +30Hz in 3 days"
    """

    def analyze(self, fft_data: list[float]) -> AudioResult:
        fundamental = self._find_fundamental(fft_data)  # Peak frequency
        amplitude = self._total_energy(fft_data)
        pattern = self._detect_pattern(fft_data)        # Piping? Swarming?
        # Compare to baseline...
        return AudioResult(label=pattern, fundamental=fundamental, amplitude=amplitude)

    def build_baseline(self, node_id: str, historical_spectra: list):
        """Build normal sound profile for this specific hive."""
        # Average fundamental, amplitude range, by hour of day
        # Seasonal adjustment (winter quieter, summer louder)
```

### Task 6d: BirdNET Integration (integrations/bird_monitor.py)
- BirdNET-Analyzer (Cornell Lab): identifies 6000+ bird species from audio
- Runs as Python library or subprocess
- Many birds near hives = possible dead bee buffet → alert beekeeper
- Seasonal bird tracking for ecological awareness
- Store species observations in ChromaDB category="bird_sighting"

### Task 6e: Audio Agent (agents/aarivahti.yaml)
New agent: 🔊 Audio monitoring specialist
Specialties: hive_acoustics, bird_identification, environmental_sounds
Can interpret: "Pesä 12 kuulostaa stressaantuneelta" / "Lintuja enemmän kuin normaalisti"

### Task 6f: Environment Context Integration
Update get_environment_context():
```
🔊 Audio tarha_pesa12: normal hum (250Hz, amplitude normal)
🔊 Audio tarha_pesa27: STRESS detected (310Hz, +40% amplitude)
🐦 Birds: 3 species identified today (harakka, varis, västäräkki)
```

### Settings Configuration
```yaml
audio:
  enabled: false
  mqtt: {host: "192.168.1.100", port: 1883}
  nodes:
    pesa12_mic: {type: "hive", hive_id: 12}
    pesa27_mic: {type: "hive", hive_id: 27}
    piha_mic: {type: "area", location: "cottage_yard"}
  yamnet:
    enabled: true
    model_path: "models/yamnet.tflite"
  birdnet:
    enabled: true
    min_confidence: 0.7
  bee_audio:
    enabled: true
    baseline_days: 7
    stress_threshold_hz_shift: 30
    swarming_amplitude_multiplier: 2.0
  alerts:
    stress_buzz: "high"
    swarming_roar: "critical"
    queen_piping: "high"
    queenless_howl: "critical"
```

### Dependencies
```bash
pip install tflite-runtime  # YAMNet
pip install birdnetlib       # BirdNET
pip install paho-mqtt         # ESP32 communication
pip install scipy             # FFT analysis
```

### Test Phase 6
```
1.  audio.enabled=false → no errors
2.  Mock MQTT spectrum → YAMNet classifies correctly
3.  Bee audio stress pattern → alert fires
4.  BirdNET identifies test bird audio
5.  Environment context includes audio data
6.  Cross-sensor: audio stress + camera activity → combined alert
7.  Audio baseline builds after 7 simulated days
When all pass: "PHASE 6 COMPLETE"
```

---

## 12. PHASE 7: Voice Interface

**Status: 📋 NEW SPECIFICATION**
**Files:** integrations/voice_interface.py (new), integrations/whisper_stt.py (new),
integrations/piper_tts.py (new)

### Overview

Hands-free voice interaction for fieldwork. Beekeeper wearing gloves at the hive
can ask questions and receive spoken answers.

```
PIPELINE:
Microphone → Whisper (STT) → Finnish text → HiveMind → Finnish answer → Piper (TTS) → Speaker

Hardware options:
A) Phone app (most practical) — mic + speaker already built in
B) Raspberry Pi + USB mic + speaker at the hive
C) Bluetooth headset connected to phone/laptop
```

### Task 7a: Whisper STT (integrations/whisper_stt.py)
```python
class WhisperSTT:
    """
    Speech-to-text using OpenAI Whisper.
    Runs locally — no cloud API needed.

    Model options (by hardware):
    - whisper-tiny:  39M params, ~1GB RAM, real-time on CPU
    - whisper-base:  74M params, ~1.5GB RAM, good accuracy
    - whisper-small: 244M params, ~2GB RAM, best Finnish accuracy
    - whisper-large: 1.55B params, ~4GB RAM (too large for shared GPU)

    Recommended: whisper-small on CPU (leaves GPU for LLMs)
    Finnish accuracy: whisper-small WER ~15% on Finnish speech
    """

    def __init__(self, model_size="small"):
        import whisper
        self.model = whisper.load_model(model_size, device="cpu")

    async def transcribe(self, audio_path: str) -> str:
        """Transcribe Finnish speech to text."""
        result = self.model.transcribe(
            audio_path,
            language="fi",
            fp16=False,  # CPU mode
        )
        return result["text"]

    async def transcribe_stream(self, audio_stream) -> AsyncIterator[str]:
        """Real-time streaming transcription."""
        # Buffer 3 seconds of audio, transcribe, yield text
        # Overlap 0.5s for continuity
        buffer = AudioBuffer(duration_s=3.0, overlap_s=0.5)
        async for chunk in audio_stream:
            buffer.add(chunk)
            if buffer.is_full():
                text = await self.transcribe(buffer.get_audio())
                if text.strip():
                    yield text
                buffer.reset_with_overlap()
```

### Task 7b: Piper TTS (integrations/piper_tts.py)
```python
class PiperTTS:
    """
    Text-to-speech using Piper (local, fast, good Finnish voices).
    https://github.com/rhasspy/piper

    Finnish voices available:
    - fi_FI-harri-medium: male voice, natural sounding
    - fi_FI-harri-low: male voice, faster synthesis

    Synthesis speed: ~10x real-time on CPU (1s speech in 100ms)
    No GPU needed.
    """

    def __init__(self, voice="fi_FI-harri-medium"):
        self.voice = voice
        self.piper_path = "models/piper"

    async def speak(self, text_fi: str) -> bytes:
        """Convert Finnish text to speech audio (WAV bytes)."""
        import subprocess
        result = subprocess.run(
            [f"{self.piper_path}/piper",
             "--model", f"{self.piper_path}/{self.voice}.onnx",
             "--output-raw"],
            input=text_fi.encode("utf-8"),
            capture_output=True,
        )
        return result.stdout  # Raw PCM audio

    async def speak_to_file(self, text_fi: str, output_path: str):
        """Convert text to WAV file."""
        audio = await self.speak(text_fi)
        # Write WAV header + PCM data
        write_wav(output_path, audio, sample_rate=22050)
```

### Task 7c: Voice Interface Controller (integrations/voice_interface.py)
```python
class VoiceInterface:
    """
    Manages voice interaction loop.
    
    Modes:
    A) WebSocket streaming (phone/browser app)
    B) Local microphone (Raspberry Pi at hive)
    C) Push-to-talk (button/hotword activation)
    
    Wake word: "Hei WaggleDance" or "Hei mehiläinen"
    (via Porcupine or openWakeWord, runs on CPU)
    """

    def __init__(self, stt: WhisperSTT, tts: PiperTTS, hivemind):
        self.stt = stt
        self.tts = tts
        self.hivemind = hivemind
        self.is_listening = False

    async def voice_loop(self, audio_source):
        """Main voice interaction loop."""
        while True:
            # 1. Wait for wake word or push-to-talk
            await self._wait_for_activation(audio_source)

            # 2. Play acknowledgment beep
            await self._play_beep()

            # 3. Record until silence (VAD - Voice Activity Detection)
            audio = await self._record_until_silence(audio_source)

            # 4. Transcribe (Whisper)
            text_fi = await self.stt.transcribe(audio)
            log.info(f"Voice input: '{text_fi}'")

            # 5. Process through HiveMind (same as text chat)
            response_fi = await self.hivemind.chat(text_fi)
            log.info(f"Voice response: '{response_fi}'")

            # 6. Speak response (Piper)
            audio_out = await self.tts.speak(response_fi)
            await self._play_audio(audio_out, audio_source)

    async def _wait_for_activation(self, source):
        """Wait for wake word or push-to-talk signal."""
        # Option 1: openWakeWord (runs on CPU, 5MB model)
        # Option 2: Push-to-talk button (GPIO on Raspberry Pi)
        # Option 3: Always-on (for dedicated field devices)
        pass
```

### Task 7d: Dashboard Voice Widget
- Browser-based voice interaction via WebSocket
- MediaRecorder API captures audio in browser
- Stream to backend → Whisper → HiveMind → Piper → stream back
- Push-to-talk button in dashboard UI

### Settings Configuration
```yaml
voice:
  enabled: false
  stt:
    model: "whisper-small"   # tiny/base/small
    device: "cpu"            # Keep GPU free for LLMs
    language: "fi"
  tts:
    engine: "piper"
    voice: "fi_FI-harri-medium"
  activation:
    mode: "wake_word"        # wake_word / push_to_talk / always_on
    wake_word: "hei waggledance"
  vad:
    silence_threshold_ms: 1500  # Stop recording after 1.5s silence
    max_duration_s: 30          # Max single utterance
```

### Dependencies
```bash
pip install openai-whisper    # STT (or faster-whisper for speed)
pip install piper-tts         # TTS
pip install openwakeword      # Wake word detection
pip install webrtcvad         # Voice Activity Detection
```

### Test Phase 7
```
1.  voice.enabled=false → no errors
2.  Whisper transcribes Finnish test audio correctly
3.  Piper generates intelligible Finnish speech
4.  Full loop: speak → transcribe → HiveMind → speak response
5.  Wake word detection works
6.  Dashboard voice widget records and plays audio
When all pass: "PHASE 7 COMPLETE"
```

---

# PART IV — EXTERNAL DATA (v0.1.8)

---

## 13. PHASE 8: External Data Feeds

**Status: 📋 NEW SPECIFICATION**
**Files:** integrations/weather_feed.py (new), integrations/electricity_feed.py (new),
integrations/rss_feed.py (new), integrations/data_scheduler.py (new)

### Overview

WaggleDance connects to external APIs for real-time environmental and market data.
All data stored in ChromaDB and injected into agent context.

### Task 8a: Weather API (integrations/weather_feed.py)
```python
class WeatherFeed:
    """
    Finnish Meteorological Institute (FMI) Open Data API.
    Free, no API key required. XML/WFS format.
    https://en.ilmatieteenlaitos.fi/open-data

    Also: OpenWeatherMap as fallback (free tier: 1000 calls/day)

    Fetch: temperature, humidity, wind, precipitation, forecast
    Locations: Helsinki, Kouvola, Kuopio (all apiary sites)
    Interval: every 30 minutes
    """

    async def fetch_current(self, location: str) -> dict:
        """Fetch current weather for location."""
        # FMI WFS request for real-time observations
        # Returns: temp_c, humidity_pct, wind_ms, precipitation_mm, cloud_cover
        pass

    async def fetch_forecast(self, location: str, hours=48) -> list[dict]:
        """Fetch weather forecast."""
        # 3-hour intervals for next 48 hours
        pass

    async def update_context(self, consciousness):
        """Store weather in ChromaDB and update environment context."""
        for location in self.locations:
            weather = await self.fetch_current(location)
            consciousness.learn(
                f"Weather {location}: {weather['temp_c']}°C, "
                f"humidity {weather['humidity_pct']}%, "
                f"wind {weather['wind_ms']}m/s",
                category="weather",
                location=location,
                ttl_hours=1,  # Expire after 1 hour
            )
```

### Task 8b: Electricity Spot Price (integrations/electricity_feed.py)
```python
class ElectricityFeed:
    """
    Finnish electricity spot prices from:
    - Porssisähkö API (https://porssisahko.net/api)
    - ENTSO-E Transparency Platform (official)

    Free, no API key for Porssisähkö.
    Fetch: current price, today's prices, tomorrow's prices (after 14:00)
    Interval: every 15 minutes
    """

    async def fetch_current_price(self) -> dict:
        """Current electricity spot price (c/kWh including VAT)."""
        # GET https://api.porssisahko.net/v1/latest-prices.json
        pass

    async def fetch_today_prices(self) -> list[dict]:
        """All hourly prices for today."""
        pass

    async def find_cheapest_hours(self, hours_needed: int = 3) -> list[dict]:
        """Find cheapest consecutive hours for running equipment."""
        pass

    async def update_context(self, consciousness):
        """Store electricity data for agent context."""
        price = await self.fetch_current_price()
        consciousness.learn(
            f"Electricity spot price now: {price['price']:.2f} c/kWh "
            f"(avg today: {price['avg']:.2f})",
            category="electricity",
            ttl_hours=1,
        )

        # Cheapest hours for honey extraction equipment
        cheapest = await self.find_cheapest_hours(3)
        consciousness.learn(
            f"Cheapest 3h electricity window today: "
            f"{cheapest[0]['hour']}:00-{cheapest[-1]['hour']+1}:00 "
            f"({cheapest[0]['price']:.2f} c/kWh avg)",
            category="electricity_optimization",
            ttl_hours=6,
        )
```

### Task 8c: RSS/News Feeds (integrations/rss_feed.py)
```python
class RSSFeedMonitor:
    """
    Monitor beekeeping and agricultural news.

    Feeds:
    - mehilaishoitajat.fi/feed      (Finnish Beekeepers Association)
    - ruokavirasto.fi/rss           (Food Authority — disease alerts!)
    - ilmatieteenlaitos.fi/rss      (Weather warnings)
    - yle.fi/uutiset/rss/luonto    (Nature news)
    - scientificbeekeeping.com/feed (Randy Oliver — English)

    Critical: Ruokavirasto disease alerts (esikotelomätä, toukkamätä)
    should trigger IMMEDIATE notification to beekeeper.

    Interval: every 2 hours (RSS etiquette)
    """

    async def check_feeds(self):
        """Check all RSS feeds for new items."""
        import feedparser
        for feed_url in self.feeds:
            parsed = feedparser.parse(feed_url)
            for entry in parsed.entries:
                if entry.id not in self.seen_ids:
                    await self._process_entry(entry, feed_url)
                    self.seen_ids.add(entry.id)

    async def _process_entry(self, entry, source):
        """Process a new RSS entry."""
        # Extract key facts using llama1b
        summary = await llama1b.generate(
            f"Extract 1-3 key facts from this news article "
            f"relevant to Finnish beekeeping:\n{entry.summary[:500]}"
        )

        # Store in ChromaDB
        self.consciousness.learn(
            summary,
            category="news",
            source_url=entry.link,
            source_name=source,
        )

        # Check for critical alerts (disease outbreaks)
        critical_keywords = ["esikotelomätä", "toukkamätä", "foulbrood",
                           "AFB", "EFB", "hive beetle", "vespa velutina"]
        if any(kw.lower() in entry.title.lower() + entry.summary.lower()
               for kw in critical_keywords):
            await self.alert_dispatcher.send(
                severity="critical",
                message=f"⚠️ Disease alert: {entry.title}",
                url=entry.link,
            )
```

### Task 8d: Data Feed Scheduler (integrations/data_scheduler.py)
```python
class DataFeedScheduler:
    """Orchestrates all external data fetches."""

    SCHEDULE = {
        "weather":     {"interval_min": 30, "priority": "high"},
        "electricity": {"interval_min": 15, "priority": "medium"},
        "rss_feeds":   {"interval_min": 120, "priority": "low"},
    }

    async def run(self):
        """Background loop — respects PriorityLock."""
        while True:
            for feed_name, config in self.SCHEDULE.items():
                if self.priority_lock.chat_active:
                    await asyncio.sleep(1)
                    continue
                if self._is_due(feed_name):
                    try:
                        await self.feeds[feed_name].update_context(self.consciousness)
                    except Exception as e:
                        log.warning(f"Feed {feed_name} failed: {e}")
                    self._mark_done(feed_name)
            await asyncio.sleep(60)
```

### Settings Configuration
```yaml
feeds:
  enabled: false
  weather:
    enabled: true
    provider: "fmi"  # fmi or openweathermap
    locations: ["Helsinki", "Kouvola", "Kuopio"]
    interval_min: 30
  electricity:
    enabled: true
    provider: "porssisahko"
    interval_min: 15
  rss:
    enabled: true
    feeds:
      - url: "https://www.mehilaishoitajat.fi/feed"
        name: "SML"
        critical: false
      - url: "https://www.ruokavirasto.fi/rss"
        name: "Ruokavirasto"
        critical: true  # Disease alerts!
    interval_min: 120
```

### Dependencies
```bash
pip install feedparser        # RSS parsing
pip install aiohttp           # Async HTTP for APIs
pip install xmltodict         # FMI XML parsing
```

### Test Phase 8
```
1.  feeds.enabled=false → no errors
2.  Weather fetch returns valid data for Helsinki
3.  Electricity price fetch returns current spot price
4.  RSS feed detects new entries
5.  Critical RSS keyword triggers alert
6.  Environment context includes weather + electricity
7.  Agent can answer: "mikä on sähkön hinta nyt?"
8.  Agent can answer: "milloin on halvinta käyttää linkousta?"
When all pass: "PHASE 8 COMPLETE"
```

---
# PART V — AUTONOMOUS INTELLIGENCE (v0.2.0)

---

## 14. PHASE 9: Autonomous Learning Engine (6 Layers)

**Status: 📋 SPECIFIED**
**Files:** core/autonomous_learning.py (new), core/web_learner.py (new),
core/knowledge_distiller.py (new), core/meta_learning.py (new),
core/code_reviewer.py (new)

### Overview — The Self-Improving System

WaggleDance progressively builds autonomous learning capabilities in 6 layers,
each building on the previous. The system identifies knowledge gaps, fills them
from multiple sources, validates results, optimizes its own performance, and
suggests code improvements — all without human intervention.

### Layer 1: Bilingual Index + Hot Cache (core/fast_memory.py)

**Problem:** Current response path requires FI→EN translation (150ms each way).
**Solution:** Dual-language ChromaDB + pre-computed answer cache.

```python
class BilingualMemoryStore:
    """Two ChromaDB collections: EN (primary) + FI (fast lookup)."""
    # When learning: embed in both EN and FI
    # Finnish query → search FI index directly → skip translation
    # Response: 355ms → 55ms

class HotCache:
    """Top 500 most-asked questions cached in RAM with Finnish answers."""
    # Finnish stemming for cache key normalization
    # Auto-populated from successful high-confidence answers
    # Response: 5ms for cache hits, zero GPU
    # Eviction: LRU, max_size=500
```

**SmartRouter updated response chain:**
```
Layer -1: Pattern Match (0.01ms)  → regex templates for top questions
Layer  0: Hot Cache (0.1ms)       → pre-computed Finnish answers
Layer  1: MicroModel V2/V3 (1-50ms) → trained on local data
Layer  2: FI-direct ChromaDB (55ms)  → bilingual index
Layer  3: EN ChromaDB + llama1b (500ms) → standard path
Layer  4: phi4-mini + context (3000ms)  → complex questions
Layer  5: Active Learning (ask user)    → unknown territory
```

### Layer 2: Fact Enrichment Engine (core/autonomous_learning.py)

Night mode doesn't just scan YAMLs — it GENERATES new knowledge.

```python
class FactEnrichmentEngine:
    async def enrichment_cycle(self):
        """One enrichment cycle. Runs in night mode every 10-15s."""
        # 1. Find knowledge gap (weakest category, user's failed queries, seasonal)
        gap = await self.find_knowledge_gap()
        # 2. Generate 5 facts with llama1b
        new_facts = await self.generate_facts(gap)
        # 3. Cross-validate with phi4-mini (dual model consensus)
        validated = await self.cross_validate(new_facts)
        # 4. Store only if both models agree
        for fact in validated:
            self.consciousness.learn(fact, confidence=0.80,
                                     source="self_enrichment",
                                     validation="dual_model_consensus")
```

**Gap detection strategies:**
1. Category analysis: count facts per domain, fill weakest
2. User failure analysis: topics with low confidence answers
3. Seasonal gaps: month-specific beekeeping topics
4. Cross-agent gaps: knowledge one agent has but another lacks

**Output:** ~200 validated new facts per night

### Layer 3: Web Learning Agent (core/web_learner.py)

AI searches the web for knowledge gaps and learns from results.

```python
class WebLearningAgent:
    """Search web, extract facts, validate, store."""
    search_apis = ["duckduckgo", "wikipedia_fi", "google_scholar"]
    trusted_domains = [
        "mehilaishoitajat.fi", "ruokavirasto.fi",
        "scientificbeekeeping.com", "bee-health.extension.org",
        "ilmatieteenlaitos.fi", "fingrid.fi"
    ]
    daily_budget = 50  # Max 50 web searches/day

    async def learn_from_web(self, topic, reason):
        # 1. Search multiple sources
        # 2. Filter trusted vs untrusted (different confidence)
        # 3. Extract facts with llama1b
        # 4. Validate: check contradictions with existing knowledge
        # 5. Dual-model validation
        # 6. Store: trusted=0.85, untrusted=0.65
```

**Safety rules:**
- Max 50 searches/day (rate limiting)
- Trusted sources → confidence 0.85; untrusted → 0.65
- Contradictions with existing knowledge → NOT stored, logged for review
- All web facts tagged source="web_learning" (mass-deletable if quality drops)
- No politics, religion, adult content searched

**Output:** ~100 validated new facts per night

### Layer 4: Knowledge Distillation (core/knowledge_distiller.py)

Send hard questions to Claude API → store expert answers permanently.

```python
class KnowledgeDistiller:
    """Big model teaches small model. Once. Knowledge persists forever."""
    model = "claude-haiku-4-5-20251001"  # Cheapest, sufficient quality
    weekly_budget_eur = 5.0

    async def weekly_distillation(self):
        # 1. Collect failed queries (confidence < 0.5, user corrected)
        # 2. Prioritize: most-asked failures first
        # 3. Send to Claude with existing knowledge as context
        # 4. Parse response: FACT: lines + CORRECTION: lines
        # 5. Store facts confidence=0.95, corrections replace existing
```

**Cost:** ~0.15€/month (Haiku) or ~4€/month (Sonnet). 100 questions/week.
**Value:** Each answer serves FOREVER without API call.
**Output:** ~50 expert-level facts per week

### Layer 5: Meta-Learning (core/meta_learning.py)

System analyzes its own performance and optimizes itself.

```python
class MetaLearningEngine:
    """Analyze learning data, optimize system behavior."""

    async def weekly_analysis(self) -> dict:
        # 1. Search accuracy per category (varroa: 95%, sähkö: 62%)
        # 2. Hallucination rate per agent
        # 3. Response time distribution (cache/micro/chromadb/llm)
        # 4. Learning efficiency (new facts/week, dedup rate)
        # 5. User satisfaction signals (corrections, repeated Qs)
        # 6. Generate optimization suggestions

    async def auto_apply_safe_optimizations(self, suggestions):
        # Safe auto-optimizations (no code changes):
        # - Add synonyms to dict_fi_en.json for weak categories
        # - Adjust hallucination thresholds per agent/category
        # - Expand hot cache size
        # - Trigger bilingual index rebuild
        # - Adjust batch sizes based on performance data
```

**Weekly report to dashboard:**
```
WAGGLEDANCE WEEKLY REPORT — 2026-03-03
📊 Memory: 8,234 facts (+1,847 this week)
⚡ Response time: 89ms average (↓ 45% vs last week)
🎯 Search accuracy: 87% (↑ 4%)
🚫 Hallucinations: 2.1% (↓ 0.8%)
💬 User corrections: 3 (↓ 5 vs last week)
WEAKEST AREAS: sähkö (62%), tehdas (58%) → auto-fixing
AUTO-OPTIMIZATIONS APPLIED: 3 synonym expansions, cache increase
```

### Layer 6: Code Self-Review (core/code_reviewer.py)

System analyzes its own performance data and suggests code improvements.
NEVER changes code automatically — suggests to developer for approval.

```python
class CodeSelfReview:
    """Identify bottlenecks and error patterns, suggest fixes."""

    async def monthly_code_review(self):
        # 1. Bottleneck analysis (which operations are slowest?)
        # 2. Error pattern analysis (which query types cause hallucinations?)
        # 3. Configuration suggestions (thresholds, batch sizes)
        # 4. Test each suggestion in sandbox (copy config, run benchmark)
        # 5. Present suggestions with sandbox results in dashboard

    # Dashboard shows:
    # [🔴 HIGH] translation_proxy.py: "Translation takes 45% of response time.
    #   Suggestion: Bilingual index. Sandbox test: 350ms → 55ms ✅"
    #   [Accept] [Reject] [Edit]
```

### Combined Learning Output
```
AUTONOMOUS LEARNING RATE:

Source                      Facts/Night    Quality
YAML scanning               500           high (raw data)
Fact enrichment (gen+val)    200           medium-high (dual validated)
Web learning                 100           medium (source-dependent)
Claude distillation (weekly)  50/week      very high (expert)
Round Table wisdom            30           high (cross-validated)
User corrections              5            very high (ground truth)
─────────────────────────────────────────────────
TOTAL:                      ~880/night    mixed → validated

30 days  → ~26,000 new facts
6 months → ~160,000 facts
1 year   → ~250,000+ facts
```

---

## 15. PHASE 10: Micro-Model Training

**Status: 📋 SPECIFIED**
**Files:** core/micro_model.py (new), core/training_collector.py (new)

### Overview

Train progressively better local models on accumulated data.
The micro-model isn't an LLM — it's a specialized fast-lookup engine
trained on YOUR specific beekeeping operation.

### Three Micro-Model Variants

**V1: Pattern Match Engine (0.01ms)**
```python
class MicroModelV1:
    """Regex + lookup table. Not a model — optimized search script."""
    # 500+ regex patterns for common questions
    # Pre-computed Finnish answers
    # 0.01ms response, zero computation
    # Built automatically from high-confidence Q&A pairs
```

**V2: Trained Classifier (1ms)**
```python
class MicroModelV2:
    """Small PyTorch neural network trained on local data."""
    # Architecture: Linear(768,256) → ReLU → Linear(256,128) → Linear(128,N)
    # Input: nomic-embed vector (already computed)
    # Output: answer class ID → mapped to pre-stored answer
    # Parameters: ~250K (vs phi4-mini 3.8B = 15000x smaller)
    # Size: 2MB, runs on CPU in <1ms
    # Training: 5-15 min on CPU, runs in night mode
```

**V3: LoRA Fine-tuned Nano-LLM (50ms)**
```python
class MicroModelV3:
    """LoRA fine-tuned smollm2:135m on local beekeeping data."""
    # Base: smollm2:135m (or qwen3:0.6b)
    # LoRA adapters trained on validated Q&A pairs
    # VRAM: 0.1-0.4 GB (fits alongside other models)
    # Training: 2-4h on RTX A2000 (night mode)
    # Understands context, generates free text
    # After gen5+: JOINS Round Table as participant
```

### Evolution Cycle
```
Generation 0 (day 1):    No micro-model. All answers via LLM + ChromaDB.
Generation 1 (week 2):   V1 + V2 trained on 500 validated Q&A pairs.
                          200 common questions → 0.5ms response.
Generation 2 (month 1):  V2 improved + V3 (LoRA). V3 answers 500 topics.
                          V3 JOINS Round Table as "apprentice".
Generation 3 (month 3):  V3 gen3 trained on Round Table discussions too.
                          Learns REASONING, not just facts.
Generation 5 (month 6):  V3 gen5 BETTER than phi4-mini for beekeeping.
                          Replaces llama1b in some agent roles.
                          Round Table: 3×llama1b + 3×microV3 (0.3s vs 6s).
```

### Training Data Pipeline
```python
class TrainingDataCollector:
    """Collect and curate training data for micro-model."""

    async def collect_training_pair(self, question, answer, source, validated):
        # Sources and confidence:
        # round_table_consensus: 0.90
        # user_accepted: 0.85
        # claude_distillation: 0.95
        # chromadb_high_confidence: 0.80
        # web_trusted: 0.75
        # Only pairs with confidence >= 0.75 are used for training

    async def collect_from_round_table(self, rt_result):
        # Queen synthesis = high-quality training data
        # Individual agent responses cited by Queen = also valuable
```

---

## 16. PHASE 11: Elastic Hardware Scaling

**Status: 📋 SPECIFIED**
**Files:** core/hardware_profiler.py (new), core/elastic_scaler.py (new)

### Overview

Same code runs optimally on any hardware. Auto-detects GPU, RAM, TPU and
configures models, agents, parallelism accordingly.

### Hardware Profiler
```python
class HardwareProfiler:
    async def detect_hardware(self) -> dict:
        # Detect: GPU(s), VRAM, RAM, CPU cores, Apple Silicon, Coral TPU
        # Classify into tier: minimal/light/standard/professional/enterprise

    def classify_tier(self) -> str:
        # minimal:      <4GB VRAM (CPU only, micro-model only)
        # light:        4-8GB (qwen3:0.6b chat, 2 agents)
        # standard:     8-20GB (phi4-mini, 6 agents) ← CURRENT
        # professional: 20-48GB (phi4:14b, 15 agents, vision)
        # enterprise:   48GB+ (llama3.3:70b, 50 agents, everything)
```

### Tier Configuration
```
TIER         CHAT MODEL      BG MODEL     AGENTS  RT SIZE  VISION  MICRO
minimal      (none)          (none)       0       0        ❌      V1 only
light        qwen3:0.6b      smollm2:135m 2       3        ❌      V1+V2
standard     phi4-mini       llama3.2:1b  6       6        ❌      V1+V2+V3
professional phi4:14b        qwen3:4b     15      8+review ✅      V3
enterprise   llama3.3:70b    llama3.1:8b  50      12×5 par ✅      V3
```

### Dynamic Scaling
```python
class ElasticAgentManager:
    async def auto_configure(self):
        # 1. Detect hardware → classify tier
        # 2. Select models that fit in VRAM
        # 3. Configure agent count and quality
        # 4. Set parallelism levels
        # 5. Enable/disable optional features (vision, audio, voice)

    async def dynamic_scale(self, current_load):
        # Runtime adjustments:
        # VRAM > 90% → unload least-used model
        # Queue depth > 10 → load additional parallel agent
        # Chat active → ensure chat model loaded, pause background
```

### Agent Quality Scaling (not just quantity)
```
standard (8GB):
  tarhaaja: llama1b + ChromaDB → 500ms, quality 7/10

professional (24GB):
  tarhaaja: qwen3:4b + ChromaDB + web → 800ms, quality 9/10
  tarhaaja_reviewer: phi4:14b validates answers

enterprise (80GB):
  tarhaaja: llama3.1:8b + all tools → 600ms, quality 9.5/10
  tarhaaja_senior: llama3.3:70b expert analysis
  tarhaaja_reviewer: verifies all responses
```

### Startup Output
```
╔══════════════════════════════════════╗
║ WaggleDance v0.2.0                   ║
║ Hardware: standard                   ║
║ GPU: NVIDIA RTX A2000         8192MB ║
║ RAM: 128GB                           ║
╠══════════════════════════════════════╣
║ Chat model:    phi4-mini             ║
║ BG model:      llama3.2:1b           ║
║ Agents:        6                     ║
║ Round Table:   6 agents              ║
║ Vision:        ❌                     ║
║ MicroModel:    V2 (classifier)       ║
╚══════════════════════════════════════╝
```

---

# PART VI — USER INTERFACE & OPS

---

## 17. Dashboard UI Specification

**Status: 📋 NEW SPECIFICATION**
**Files:** web/dashboard.py (extend), web/static/ (frontend assets)

### Technology Stack
```
Backend:  FastAPI + WebSocket (existing)
Frontend: Vanilla JS + CSS (no React build step required)
          OR: HTMX + Alpine.js (progressive enhancement)
Charts:   Chart.js (CDN)
Icons:    Emoji-based agent avatars (no image assets needed)
```

### Dashboard Layout
```
┌──────────────────────────────────────────────────────────┐
│  🐝 WaggleDance Dashboard            [Voice 🎤] [⚙️]   │
├────────────┬─────────────────────────────────────────────┤
│            │                                             │
│  SIDEBAR   │  MAIN AREA                                 │
│            │                                             │
│  📊 Status │  [Chat] [Round Table] [Agents] [Learning]  │
│  💬 Chat   │  [Cameras] [Audio] [Sensors] [Code Review] │
│  🔔 Alerts │                                             │
│  🐝 Agents │  Currently visible panel here               │
│  📷 Cameras│                                             │
│  📈 Stats  │                                             │
│  ⚙️ Config │                                             │
│            │                                             │
├────────────┴─────────────────────────────────────────────┤
│  Status bar: Memory: 8234 | Response: 65ms | GPU: 54%   │
└──────────────────────────────────────────────────────────┘
```

### Panel Specifications

**Chat Panel:**
- Text input + send button
- Voice input button (if voice enabled)
- Message history with agent avatars
- Confidence indicator per message (✅/⚠️/❌)
- Source attribution: "From memory" / "Generated" / "Round Table"

**Round Table Panel:**
- Live conversation view with 300ms delays (Theater Pipe)
- Agent icons + names + level badges
- Queen synthesis highlighted separately
- "💾 Stored as wisdom" indicator

**Agents Panel:**
- Grid of agent cards: name, icon, level, trust score
- Sparkline charts: accuracy trend, response count
- Color coding: green (expert), yellow (apprentice), red (novice)

**Learning Panel:**
- Real-time fact counter with rate graph
- Knowledge gap visualization (radar chart per category)
- Source distribution pie chart (YAML/enriched/web/distilled/user)
- Weekly report (meta-learning output)

**Cameras Panel (if Frigate enabled):**
- Live snapshot per camera (proxied from Frigate)
- Recent detections timeline
- PTZ controls (preset buttons, manual pan/tilt)
- Alert history

**Audio Panel (if audio enabled):**
- Spectrogram visualization per node
- Bee health status indicators (green/yellow/red per hive)
- Bird species log
- Sound event timeline

**Code Review Panel:**
- Pending suggestions with sandbox test results
- Accept/Reject/Edit buttons
- History of applied and rejected suggestions

### WebSocket Events
```
EXISTING:
  heartbeat_insight  → learning activity
  chat_response      → user chat

NEW:
  round_table_start  → {topic, agents}
  round_table_insight → {agent, icon, text, quality, round}
  round_table_end    → {topic, wisdom_stored}
  agent_level_change → {agent, old_level, new_level}
  camera_event       → {camera, label, score, snapshot_url}
  camera_alert       → {severity, message, camera}
  audio_event        → {node, label, confidence}
  learning_stats     → {total_facts, rate, sources}
  meta_report        → {weekly analysis data}
  code_suggestion    → {type, file, description, sandbox_result}
```

---

## 18. Deployment, Backup & Monitoring

### Directory Structure
```
U:/project/
├── main.py                  # Entry point
├── hivemind.py              # Core orchestrator
├── consciousness.py         # Memory + learning engine
├── translation_proxy.py     # FI↔EN translation
├── core/
│   ├── agent_levels.py      # Agent promotion/demotion
│   ├── autonomous_learning.py # Fact enrichment
│   ├── code_reviewer.py     # Code self-review
│   ├── elastic_scaler.py    # Hardware-aware scaling
│   ├── fast_memory.py       # Bilingual index + hot cache
│   ├── hardware_profiler.py # GPU/RAM detection
│   ├── knowledge_distiller.py # Claude API distillation
│   ├── llm_provider.py      # Ollama API wrapper
│   ├── meta_learning.py     # Self-optimization
│   ├── micro_model.py       # V1/V2/V3 micro-models
│   ├── training_collector.py # Q&A pair collection
│   ├── web_learner.py       # Web knowledge acquisition
│   ├── yaml_bridge.py       # YAML agent data
│   └── en_validator.py      # English output validation
├── integrations/
│   ├── frigate_mqtt.py      # Camera events via MQTT
│   ├── alert_dispatcher.py  # Multi-channel alerts
│   ├── ptz_controller.py    # ONVIF PTZ camera control
│   ├── visual_learning.py   # Camera pattern learning
│   ├── audio_monitor.py     # ESP32 audio processing
│   ├── bee_audio.py         # Hive sound analysis
│   ├── bird_monitor.py      # BirdNET integration
│   ├── voice_interface.py   # Voice interaction controller
│   ├── whisper_stt.py       # Speech-to-text
│   ├── piper_tts.py         # Text-to-speech
│   ├── weather_feed.py      # FMI weather API
│   ├── electricity_feed.py  # Spot price API
│   ├── rss_feed.py          # News feed monitor
│   └── data_scheduler.py    # External data orchestrator
├── agents/                  # Agent YAML definitions
│   ├── tarhaaja.yaml
│   ├── tautivahti.yaml
│   ├── meteorologi.yaml
│   ├── kameravahti.yaml     # NEW
│   ├── aarivahti.yaml       # NEW (audio)
│   └── ... (50+ agents)
├── knowledge/               # Domain knowledge YAML files
│   ├── bee_biology.yaml
│   ├── diseases.yaml
│   ├── flora_calendar.yaml
│   └── ...
├── web/
│   ├── dashboard.py         # FastAPI + WebSocket
│   └── static/              # Frontend assets
├── configs/
│   ├── settings.yaml        # Master configuration
│   └── frigate.yml          # Frigate NVR config (reference)
├── tools/
│   ├── scan_knowledge.py    # YAML bulk scanner
│   ├── distill_from_opus.py # Claude API distillation
│   └── benchmark_v3.py      # Performance benchmark
├── data/
│   ├── chroma_db/           # ChromaDB persistent storage
│   ├── micromodel_v1/       # Pattern match data
│   ├── micromodel_v2.pt     # Trained classifier
│   ├── lora_adapters/       # LoRA weights per generation
│   ├── scan_progress.json   # YAML scan state
│   ├── learning_progress.json # Night mode state
│   ├── learning_metrics.jsonl # Performance logs
│   ├── failed_queries.jsonl # For distillation
│   ├── code_suggestions.jsonl # Self-review suggestions
│   └── web_learning.jsonl   # Web learning log
├── esp32/
│   └── hive_audio.ino       # ESP32 firmware
└── models/
    ├── piper/               # TTS voice models
    └── yamnet.tflite        # Audio classification
```

### Backup Strategy
```python
# tools/backup.py
class BackupManager:
    """Backup critical data. Run daily via Windows Task Scheduler."""

    BACKUP_ITEMS = [
        "data/chroma_db/",           # All knowledge (CRITICAL)
        "data/micromodel_*",          # Trained models
        "data/lora_adapters/",        # LoRA weights
        "data/learning_metrics.jsonl", # Performance history
        "data/scan_progress.json",    # Scan state
        "configs/settings.yaml",      # Configuration
        "agents/",                    # Agent definitions
        "knowledge/",                # Domain knowledge
    ]

    async def backup(self, destination: str):
        # 1. Create timestamped backup folder
        # 2. Copy all items
        # 3. Compress to .tar.gz
        # 4. Verify integrity
        # 5. Rotate: keep last 7 daily + 4 weekly

    async def restore(self, backup_path: str):
        # 1. Verify backup integrity
        # 2. Stop WaggleDance
        # 3. Restore files
        # 4. Restart WaggleDance
```

### Monitoring
```
Key metrics to track (data/learning_metrics.jsonl):

PER RESPONSE:
  timestamp, query_hash, agent_id, model_used, response_time_ms,
  confidence, was_hallucination, cache_hit, route (micro/cache/chromadb/llm)

PER HOUR:
  facts_total, facts_new, facts_enriched, facts_web, facts_distilled,
  hallucination_rate, avg_response_ms, cache_hit_rate, vram_usage_pct

PER DAY:
  total_queries, unique_queries, user_corrections, agent_promotions,
  agent_demotions, round_tables_run, enrichment_facts, web_facts

PER WEEK:
  meta_learning_report, code_suggestions, benchmark_score,
  knowledge_gap_analysis, trend_analysis
```

### Windows Service Setup
```powershell
# Run as background service on Windows 11
# Option A: NSSM (Non-Sucking Service Manager)
nssm install WaggleDance "python.exe" "U:\project\main.py"
nssm set WaggleDance AppDirectory "U:\project"
nssm set WaggleDance AppStdout "U:\project\logs\stdout.log"
nssm set WaggleDance AppStderr "U:\project\logs\stderr.log"
nssm start WaggleDance

# Option B: Windows Task Scheduler (auto-start on boot)
schtasks /create /tn "WaggleDance" /tr "python U:\project\main.py" /sc onstart
```

---

# APPENDICES

---

## Appendix A: GPU/RAM Budget Calculator

```
CURRENT (Phase 2, standard tier):
  GPU:  4.3G / 8.0G (54%)
  RAM:  ~2G / 128G (2%)
  Disk: ~12MB ChromaDB

v0.2.0 (all features, standard tier):
  GPU:  4.3G (same models)
  RAM:  ~3.5G (ChromaDB×2 + cache + buffers)
  Disk: ~1G (ChromaDB + logs + models)

100K facts:
  GPU:  4.3G (unchanged)
  RAM:  ~4.5G
  Disk: ~2G

1M facts (6-12 months):
  GPU:  4.3G (unchanged)
  RAM:  ~8G (→ consider FAISS migration)
  Disk: ~8G

PROFESSIONAL TIER (RTX 4090 24GB):
  GPU:  phi4:14b(9G) + qwen3:4b(2.8G) + embeds(0.5G) + opus(0.6G)
        + minicpm-v(2.8G) = 15.7G / 24G
  RAM:  ~6G
  Agents: 15, vision enabled, V3 micro-model

ENTERPRISE (2×A100 80GB each):
  GPU0: llama3.3:70b(42G) + embeds(0.5G) + opus(0.6G) = 43.1G
  GPU1: llama3.1:8b(5G) × 6 parallel = 30G
  RAM:  ~16G
  Agents: 50, everything enabled
```

## Appendix B: Implementation Priority

```
MUST HAVE (v0.1.0):
  ✅ Phase 1: Foundation
  ✅ Phase 2: Batch Pipeline
  ⏳ Phase 3: Round Table, Agent Levels, Night Mode
  📋 Phase 4: Contrastive, Active Learning, RAG

SHOULD HAVE (v0.1.5):
  📋 Phase 4i-k: Bilingual Index, Hot Cache, Enrichment
  📋 Phase 8: Weather + Electricity feeds (easy, high value)

NICE TO HAVE (v0.1.8):
  📋 Phase 5: Frigate Cameras (needs hardware purchase)
  📋 Phase 6: Audio Sensors (needs ESP32 hardware)
  📋 Phase 8c: RSS Feeds

FUTURE (v0.2.0):
  📋 Phase 7: Voice Interface
  📋 Phase 9: Full Autonomous Learning (6 layers)
  📋 Phase 10: Micro-Model Training
  📋 Phase 11: Elastic Scaling

DEPENDENCY CHAIN:
  Phase 1 → Phase 2 → Phase 3 → Phase 4
  Phase 4 → Phase 9 (autonomous learning needs Round Table)
  Phase 5 → Phase 6 (audio reuses MQTT infrastructure)
  Phase 3 → Phase 10 (micro-model needs Round Table training data)
  Phase 9 Layer 5 → Phase 16 Layer 6 (meta-learning → code review)
  NO dependencies: Phase 7, Phase 8, Phase 11
```

## Appendix C: Testing Checklist

```
PHASE 1: ✅
  [ ] math 9/9 pass
  [ ] hallucination 4/4 pass
  [ ] search 4/5+ pass
  [ ] smart router routes correctly
  [ ] priority lock pauses background

PHASE 2: ✅
  [ ] batch embed 10 < 400ms
  [ ] scan_knowledge < 30s
  [ ] 94%+ benchmark
  [ ] dual embedding working

PHASE 3:
  [ ] round table produces wisdom
  [ ] agent levels increment
  [ ] night mode activates on idle
  [ ] dashboard shows live RT

PHASE 4:
  [ ] user correction stored
  [ ] active learning asks user
  [ ] bilingual index 55ms
  [ ] hot cache 5ms
  [ ] enrichment generates facts
  [ ] /api/consciousness returns stats

PHASE 5:
  [ ] frigate disabled → no errors
  [ ] mock MQTT → ChromaDB event
  [ ] bear alert → Telegram
  [ ] PTZ patrol works
  [ ] visual learning baseline builds
  [ ] camera context in agents

PHASE 6:
  [ ] audio disabled → no errors
  [ ] mock spectrum → classified
  [ ] bee stress → alert
  [ ] BirdNET identifies species
  [ ] audio context in agents

PHASE 7:
  [ ] voice disabled → no errors
  [ ] Whisper transcribes Finnish
  [ ] Piper speaks Finnish
  [ ] full voice loop works

PHASE 8:
  [ ] feeds disabled → no errors
  [ ] weather fetch works
  [ ] electricity price works
  [ ] RSS detects new items
  [ ] critical RSS → alert

PHASE 9:
  [ ] enrichment generates 5 facts
  [ ] dual validation rejects bad facts
  [ ] web learning stores trusted facts
  [ ] distillation stores expert facts
  [ ] meta-learning generates report
  [ ] code review produces suggestions

PHASE 10:
  [ ] V1 pattern match works
  [ ] V2 classifier trains and predicts
  [ ] V3 LoRA trains overnight
  [ ] micro-model joins Round Table

PHASE 11:
  [ ] hardware detection correct
  [ ] tier classification correct
  [ ] model selection fits VRAM
  [ ] dynamic scaling responds to load
```

## Appendix D: Glossary (Finnish Beekeeping Terms)

```
FINNISH          ENGLISH              CONTEXT
mehiläinen       bee                  General term
kuningatar       queen                Colony leader
emo              queen (colloquial)   Same as kuningatar
työmehiläinen    worker bee           Female, non-reproductive
kuhnuri          drone                Male bee
parveilu         swarming             Colony reproduction
varroa           varroa mite          Varroa destructor
toukkamätä       foulbrood            AFB (American) or EFB (European)
esikotelomätä    American foulbrood   AFB, notifiable disease
nosema           nosema               Nosema ceranae/apis
pesä             hive/colony          Physical hive or colony
kehä             frame                Langstroth/Dadant frame
linkous          honey extraction     Spinning honey from frames
hunaja           honey                Product
satokehä         super frame          Honey super frame
hoitokehä        brood frame          Brood nest frame
tarha            apiary               Hive location
mökki            cottage              Summer cottage
lentoaukko       hive entrance        Where bees enter/exit
pöntön levy      bottom board         Hive floor
kansi            cover/lid            Hive top cover
ruokinta         feeding              Sugar syrup for winter
talvehtiminen    wintering            Winter survival
kevättarkastus   spring inspection    First check after winter
oksaalihappo     oxalic acid          Varroa treatment chemical
muurahaishappo   formic acid          Varroa treatment chemical
sikiö            brood                Eggs, larvae, pupae
siitepöly        pollen               Bee-collected pollen
propolis         propolis             Bee glue, antimicrobial
emottaminen      requeening           Replacing queen
mehiläishoitaja  beekeeper            Person who tends bees
mehiläishoito    beekeeping           The practice/art
ruokavirasto     Food Authority       Finnish food safety agency
SML              Finnish Beekeepers   Suomen Mehiläishoitajien Liitto
```

---

# END OF MASTER SPECIFICATION

```
DOCUMENT STATS:
  Total phases: 11
  Total tasks: ~60+
  Covering: foundation, learning, sensors, voice, data, autonomy, scaling
  Hardware: RTX A2000 8GB → scales to any GPU
  Languages: English internal, Finnish user-facing
  Generated: 2026-02-24
  Author: Claude (Anthropic) in collaboration with Jani Korpi
```
