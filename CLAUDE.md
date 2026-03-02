# WAGGLEDANCE SWARM AI — MASTER SPECIFICATION (CONDENSED)
# v0.2.0 | Developer: Jani Korpi / JKH Service (Y-2828492-2)

## 1. PROJECT OVERVIEW

Local-first multi-agent AI for Finnish beekeeping. 50+ agents, HiveMind orchestrator,
translation pipeline, consciousness layer, autonomous learning.

**Developer:** Jani Korpi, ~300 colonies (Helsinki + Kouvola), ~10,000 kg honey/year.
**Key constraint:** User output = Finnish. Internal LLM = English. Translation = Opus-MT.

**Status (2026-03-01):**
- Phase 1-2: COMPLETE (consciousness v2, batch pipeline, 94% benchmark, 3147 facts)
- Phase 3: COMPLETE (Round Table, agent levels, night mode)
- Phase 4: COMPLETE (bilingual index, hot cache, enrichment, corrections, centroids, seasonal guard)
- Phase B: COMPLETE (pipeline wiring, priority lock, circuit breaker, error handling)
- Phase C: COMPLETE (HotCache auto-fill, LRU cache, batch pipeline, readiness check, structured logging)
- Phase D: COMPLETE (external source activation, convergence detection, meta-learning weekly report)
- Phase 5-11: SPECIFIED (sensors, voice, feeds, autonomous learning, scaling)

## 2. HARDWARE & GPU

### Development / Demo: HP ZBook Mobile Workstation
```
CPU:  Intel Core i7-12850HX 16C/24T, up to 4.8 GHz
RAM:  128 GB DDR5 4800 MHz (4×32 GB)
GPU:  NVIDIA RTX A2000 8 GB GDDR6 (Laptop), 2560 CUDA, 288 GB/s, ~8 TFLOPS FP16
SSD:  Samsung 2 TB NVMe
OS:   Windows 11 Enterprise
Power: ~150 W (adapter 230 W)
Price: ~€3,000–4,000
```

### Reference / Enterprise: NVIDIA DGX B200
```
CPU:  2× Intel Xeon Platinum 8570 (56C/112T each = 112C total)
RAM:  4,096 GB DDR5
GPU:  8× NVIDIA B200, 1,536 GB HBM3e total, 64 TB/s bandwidth, 72,000 TFLOPS FP16
      NVLink 5th gen 1.8 TB/s per GPU
SSD:  ~30 TB NVMe RAID
Power: 14,400 W (14.4 kW)
Price: ~€475,000
```

### Latency Comparison (per workload)
```
WORKLOAD                        ZBOOK           DGX B200        SPEEDUP
phi4-mini inference (1K tok)    800–1500 ms     5–15 ms         ~100×
llama 3.2 1B (heartbeat)       400–800 ms      2–5 ms          ~150×
nomic-embed-text (100 texts)   3–5 s           20–50 ms        ~100×
ChromaDB search (3147 facts)   15–30 ms        5–10 ms         ~3× (CPU-bound)
Opus-MT translation (single)   200–500 ms *    5–10 ms         ~50×
NightEnricher full cycle        5–10 s/fact     50–100 ms/fact  ~100×
50-agent Round Table            30–60 s         200–500 ms      ~100×

* Opus-MT first call ~60 s cold start (model load), then 200–500 ms warm
```

### AI Models (VRAM budget)
```
MODEL               VRAM    ROLE
phi4-mini           2.5G    Chat (English internally)
llama3.2:1b         0.7G    ALL background (heartbeat, learning, Round Table)
nomic-embed-text    0.3G    SEARCH embeddings (ChromaDB)
all-minilm          0.2G    EVAL embeddings (hallucination, dedup)
Opus-MT FI→EN       0.3G    Translation input
Opus-MT EN→FI       0.3G    Translation output
TOTAL:              4.3G / 8.0G (54%)

Ollama: OLLAMA_MAX_LOADED_MODELS=4, OLLAMA_KEEP_ALIVE=24h
```

## 3. SYSTEM ARCHITECTURE

```
User (Finnish) → HiveMind
  ├─ PRIORITY GATE: Chat → ALL GPU to chat, pause learning
  ├─ SmartRouter (confidence-based):
  │   >0.90 → MicroModel/Cache (0-5ms)
  │   >0.70 → ChromaDB + llama1b (500ms)
  │   >0.50 → phi4-mini + context (3000ms)
  │   <0.50 → Active Learning: ask user
  ├─ Translation: Opus-MT chat / dict heartbeat
  ├─ Consciousness:
  │   MathSolver(0ms) | ChromaDB(bilingual) | Dual embed(nomic+minilm)
  │   Batch ops | Hallucination detect | Hot Cache(500,5ms)
  │   Active Learning | Agent levels 1-5 | Embedding augmentation
  ├─ Swarm Learning (background):
  │   YAML Scanner | Heartbeat(llama1b) | Round Table(6 agents)
  │   Batch Pipeline(60-80% GPU) | Theater Pipe(300ms delays)
  │   Night Mode | Fact Enrichment | Web Learning | MicroModel | Meta-Learning
  ├─ Sensors (optional): Frigate MQTT | ESP32 Audio | Weather | Electricity
  ├─ Adaptive Throttle: startup benchmark, VRAM monitor, chat priority
  └─ Dashboard: FastAPI + WebSocket
```

### Design Principles
1. **Speed+Memory > Model Size** — phi4-mini+memory > 32B without. LLM shrinks: 100%→15% over 6mo
2. **Batch Background, Stream Foreground** (Theater Pipe) — batch hidden, 300ms delayed display
3. **Cross-Validation > Intelligence** — 6 agents checking > 1 large model
4. **Chat Always Wins** — PriorityLock pauses all background
5. **Learn From Everything** — YAML, chat, corrections, heartbeats, web, sensors
6. **Autonomous Evolution** — gap detection, enrichment, validation, micro-models
7. **Elastic Scaling** — same code on 4GB laptop to 160GB server

## 4. EXISTING CODEBASE

| File | Lines | Status | Notes |
|------|-------|--------|-------|
| hivemind.py | ~2700 | EXTEND | chat(), heartbeat, Round Table, PriorityLock, WebSocket |
| consciousness.py | ~2000 | v2 EXTEND | MathSolver, ChromaDB, dual embed, hallucination |
| translation_proxy.py | ~1600 | ADD batch | fi_to_en/en_to_fi, Voikko+Opus-MT. CRITICAL: use .text |
| core/fast_memory.py | ~1000 | WIRE UP | HotCache, BilingualMemoryStore, FiFastStore |
| core/night_enricher.py | ~1200 | WORKING | QualityGate, GapScheduler, AdaptiveTuner |
| core/swarm_scheduler.py | ~700 | WIRE UP | Agent routing, pheromones, load balance |
| core/learning_engine.py | ~850 | WIRE UP | QualityGate, PromptEvolver |
| core/micro_model.py | ~1100 | V1+V2 done | Pattern match + neural classifier |
| core/agent_levels.py | ~700 | WORKING | 5-level trust system |
| core/meta_learning.py | ~650 | FRAMEWORK | Weekly analysis |
| core/ops_agent.py | ~1200 | WORKING | Runtime monitoring |
| core/adaptive_throttle.py | ~550 | WORKING | VRAM/CPU management |
| web/dashboard.py | ~1000 | EXTEND | FastAPI + WebSocket |
| main.py | ~200 | WORKING | Entry point, auto-spawn 25 agents |

## 5. CRITICAL RULES

```
1.  All LLM internally in ENGLISH — Finnish only for user I/O
2.  Opus-MT for translation (force_opus=True for chat)
3.  nomic-embed-text: "search_document:"/"search_query:" prefixes REQUIRED
4.  all-minilm: NO prefix (symmetric model)
5.  ChromaDB cosine distance, score = 1.0 - (dist / 2.0)
6.  TranslationResult.text — NEVER concatenate TranslationResult directly
7.  phi4-mini ONLY for chat — never heartbeat/learning
8.  llama3.2:1b for ALL background — fast and cheap
9.  Batch where possible — GPU prefers batches
10. Chat ALWAYS wins — PriorityLock pauses all background
11. UTF-8 everywhere — Finnish ä, ö, å
12. Windows 11 compatibility required
13. Test every phase before next
14. Log everything → data/learning_metrics.jsonl
15. Graceful degradation — missing sensors/APIs → log warning, continue
16. All observations → ChromaDB with category tags
17. Never block main thread — asyncio + background threads
18. ISO 8601 dates, UTC internally
```

## 6. PHASE 1: Foundation (COMPLETE)

- YAML scanner: parse knowledge/ + agents/, embed+store, 600+ facts <5min
- SmartRouter: confidence tiers (0.90/0.70/0.50)
- Heartbeat split: phi4-mini=chat only, llama1b=all background
- PriorityLock: chat_enters/chat_exits/batch_checkpoint
- Hallucination: 0.3*embedding + 0.7*keyword, threshold 0.45, empty→0.0
- ChromaDB seeded: 3147 facts

## 7. PHASE 2: Batch Pipeline (COMPLETE, 94% benchmark)

### APIs
```python
embed_batch(texts: list[str], mode="document"|"query") -> list[list[float]]  # max 50
eval_embed_batch(texts: list[str]) -> list[list[float]]  # all-minilm, NO prefix
dual_embed_batch(texts: list[str]) -> tuple[list, list]  # concurrent
batch_fi_to_en(texts: list[str]) -> list[str]  # max 20
batch_en_to_fi(texts: list[str]) -> list[str]
```
- Learn queue: append → flush at >=10 items (batch translate, embed, store)
- Adaptive throttle: startup benchmark for optimal batch sizes

## 8. PHASE 3: Social Learning (COMPLETE)

### Agent Levels (core/agent_levels.py)
```
Level 1 NOVICE:     Memory-only, checked, max 200 tok
Level 2 APPRENTICE: +LLM+memory, read swarm_facts      (50 correct, halluc<15%, trust>0.3)
Level 3 JOURNEYMAN: +write swarm_facts, consult 1       (200 correct, halluc<8%, trust>0.6)
Level 4 EXPERT:     +consult 3, web search, skills      (500 correct, halluc<3%, trust>0.8)
Level 5 MASTER:     Full autonomy, teach others          (1000 correct, halluc<1%, trust>0.95)
DEMOTION: halluc exceeds threshold over 50-window → drop 1 level
```

### Round Table
```python
async def _round_table(self, topic, agent_count=6):
    # 1. Selection: agents by topic relevance + level
    # 2. Discussion: sequential, each sees previous (llama1b)
    # 3. Synthesis: Queen summarizes consensus
    # 4. Storage: confidence=0.85, source="round_table"
# Run every 20th heartbeat. Theater Pipe: 300ms delays to dashboard.
```

### Night Mode
- Idle >30min → heartbeat 10-15s, aggressive YAML scan + consolidation
- Progress: data/learning_progress.json. Stop: all done OR user returns OR 8h

## 9. PHASE 4: Advanced Learning (MOSTLY COMPLETE)

| Task | Status | Summary |
|------|--------|---------|
| 4a Contrastive | DONE | Corrections collection, inject context, trust -=0.05 |
| 4b Active Learning | DONE | score<0.50 → ask user, store confidence=0.9 |
| 4c Embedding Augment | DONE | dict_fi_en.json synonyms appended |
| 4d Multi-hop RAG | DONE | 2 hops max, cache intermediate |
| 4e Episodic Memory | DONE | "episodes" collection, session_id linked |
| 4f Seasonal Curriculum | DONE | Month-based 1.2x boost |
| 4g Distillation Prep | STUB | failed_queries.jsonl, placeholder API |
| 4h Feedback Endpoint | PARTIAL | GET /api/consciousness → JSON stats |
| 4i Bilingual Index | DONE | FI+EN ChromaDB, 355ms→55ms |
| 4j Hot Cache | DONE | Top 500, stemming, 5ms hits |
| 4k Fact Enrichment | DONE | llama1b generate → phi4-mini validate → store |

## 10. PHASE B: Pipeline Wiring & Reliability (COMPLETE)

**Files:** hivemind.py, consciousness.py

### B1: Pipeline Wiring
- system_prompt injection for all agents (knowledge context + date metadata)
- Translation pipeline connected end-to-end (FI→EN→LLM→EN→FI)

### B2: Memory Eviction
- TTL-based eviction per category (weather=1h, news=720h, etc.)
- Protected sources never evicted: user_teaching, user_correction, yaml_scan, round_table
- Eviction check every N flushes, batch size configurable

### B3: Circuit Breaker (consciousness.py)
```python
class CircuitBreaker:
    # States: CLOSED → OPEN → HALF_OPEN
    # Protects: ChromaDB, embedding, LLM calls
    # failure_threshold=3, window_s=60, recovery_s=30
    # HALF_OPEN: one test call → success=CLOSED, fail=re-OPEN
```

### B4: Error Handling
- LLM retry logic with exponential backoff
- Timeout boundaries for all external calls
- Graceful degradation: missing services → log warning, continue

### Tests: tests/test_b2_eviction.py (7), test_b3_circuit_breaker.py (10), test_b4_error_handling.py (9)

## 11. PHASE C: Cache & Observability (COMPLETE)

**Files:** hivemind.py, consciousness.py, core/meta_learning.py

### C1: HotCache Auto-fill
- Auto-populates from ALL successful chat paths (memory_fast, delegate, neg_kw, master)
- LRU eviction when full (max_size=500 from settings.yaml)
- Response time: 5ms for cache hits

### C2: Embedding Cache LRU
- OrderedDict replaces dict in EmbeddingEngine and EvalEmbeddingEngine
- move_to_end() on hit, popitem(last=False) on insert past limit
- Max 500 entries, prevents memory leak

### C3: Batch Embedding + Dedup
- Single `collection.query(query_embeddings=..., n_results=1)` replaces N individual searches
- 5x faster enrichment pipeline

### C4: Readiness Check
```python
async def _readiness_check(self):
    # Checks: Ollama /api/version, required models, ChromaDB, directories
    # Non-blocking: logs issues, never blocks startup
```

### C5: Structured Logging (StructuredLogger)
```python
class StructuredLogger:
    def log_chat(*, query, method, agent_id, response_time_ms, confidence,
                 was_hallucination, cache_hit, model_used, route, ...):
        # Writes JSON line to data/learning_metrics.jsonl
    def log_learning(*, event, count, duration_ms, source, ...):
        # Learning events (enrichment, web, distillation)
```
- 7 log points in _do_chat() covering all return paths
- Timer: time.perf_counter() at _do_chat start

### Tests: tests/test_c1-c5 (6+7+6+5+8 = 32 tests)

## 12. PHASE D: Autonomous External Learning (COMPLETE)

**Files:** core/night_enricher.py, core/meta_learning.py, hivemind.py

### D1: External Source Activation
```python
NightEnricher.set_external_sources(web_learner, distiller, rss_monitor)
# Wired in _night_learning_cycle() after _init_learning_engines()
# Cycle schedule: web every 10, distill every 20, RSS every 50 cycles
```

### D2: Convergence Detection
```python
class ConvergenceDetector:
    # Per-source rolling window of novelty scores
    # novelty < threshold (0.20) for patience (3) checks → pause source
    # pause_s = 1800 (30 min), then resume
    # all_converged() → stop entire enrichment session
```

### D3: Meta-Learning Weekly Report
```python
MetaLearningEngine.generate_weekly_report():
    # Reads data/learning_metrics.jsonl (C5)
    # Computes: total_queries, cache_hit_rate, avg_response_ms,
    #   hallucination_rate, route_breakdown, model_usage
    # Writes data/weekly_report.json
    # Memory-safe: deque(maxlen=10000)
```

### D4: LoRA Fine-tuning — DEFERRED (larger project)
### D5: Code Self-Review — DEFERRED (larger project)

### Tests: tests/test_d1_d2_d3_autonomy.py (15 tests)

## 13. PHASE 5: Frigate Cameras (SPECIFIED)

Files: integrations/frigate_mqtt.py, alert_dispatcher.py, ptz_controller.py, visual_learning.py

**Pipeline:** Cameras → Frigate(YOLO+Coral) → MQTT → WaggleDance → ChromaDB/Alerts/PTZ

**Tasks:**
- 5a: MQTT client (paho-mqtt, dedup, ChromaDB store, alert rules, graceful degradation)
- 5b: Alert dispatcher (Telegram/webhook/WS, severity: info/medium/high/critical)
  - Bear→CRITICAL, Night person→HIGH, Daytime visitor→INFO, Dog→MEDIUM
- 5c: PTZ controller (ONVIF, patrol presets, smart focus from sensor alerts)
- 5d: Visual learning (pattern baseline 7d, anomaly detection, daily summaries)
- 5e: Cross-sensor (camera+audio+weather fusion decisions)
- 5f: Environment context injection for all agents
- 5g: KameraAgent YAML (camera_monitoring, security_assessment)

```yaml
frigate: {enabled: false, mqtt: {host: "192.168.1.100", port: 1883}}
```

## 11. PHASE 6: Audio Sensors (SPECIFIED)

Files: integrations/audio_monitor.py, bee_audio.py, bird_monitor.py

**Pipeline:** ESP32+mic → WiFi/MQTT → WaggleDance → YAMNet/BeeMonitor/BirdNET

**Bee Audio Frequencies:**
- Normal hum: 200-500Hz (~250Hz fundamental)
- Queen piping: 400-500Hz pulsed
- Swarming: broad 200-600Hz, increased amplitude
- Stress: higher frequencies, increased amplitude
- Varroa-stressed: fundamental shifts ~+50Hz

**Tasks:** MQTT spectrum receiver, YAMNet classifier, BeeAudioAnalyzer (baseline+anomaly),
BirdNET integration, cross-sensor alerts, AarivahtiAgent YAML

```yaml
audio: {enabled: false, bee_audio: {baseline_days: 7, stress_threshold_hz_shift: 30}}
```

## 12. PHASE 7: Voice Interface (SPECIFIED)

Files: integrations/voice_interface.py, whisper_stt.py, piper_tts.py

**Pipeline:** Mic → Whisper(STT,fi) → HiveMind → Piper(TTS,fi) → Speaker
- Whisper-small on CPU (WER ~15% Finnish), Piper fi_FI-harri-medium
- Wake word: "Hei WaggleDance" (openWakeWord), VAD silence 1.5s
- Dashboard voice widget via WebSocket + MediaRecorder

```yaml
voice: {enabled: false, stt: {model: "whisper-small", device: "cpu"}}
```

## 13. PHASE 8: External Data Feeds (SPECIFIED)

Files: integrations/weather_feed.py, electricity_feed.py, rss_feed.py, data_scheduler.py

| Feed | API | Interval | Priority |
|------|-----|----------|----------|
| Weather | FMI Open Data (free) | 30min | high |
| Electricity | porssisahko.net (free) | 15min | medium |
| RSS | mehilaishoitajat.fi, ruokavirasto.fi | 2h | low |

- Weather: temp, humidity, wind, forecast → ChromaDB ttl_hours=1
- Electricity: spot price, cheapest 3h window → equipment scheduling
- RSS: disease alerts (esikotelomata, toukkamata) → CRITICAL Telegram alert
- DataFeedScheduler: background loop, respects PriorityLock

```yaml
feeds: {enabled: false, weather: {provider: "fmi", locations: ["Helsinki","Kouvola"]}}
```

## 14. PHASE 9: Autonomous Learning (6 Layers)

### SmartRouter Response Chain
```
Layer -1: Pattern Match (0.01ms) → regex templates
Layer  0: Hot Cache (0.1ms)      → pre-computed Finnish answers
Layer  1: MicroModel V2/V3 (1-50ms) → trained local
Layer  2: FI-direct ChromaDB (55ms)  → bilingual index
Layer  3: EN ChromaDB + llama1b (500ms) → standard
Layer  4: phi4-mini + context (3000ms) → complex
Layer  5: Active Learning (ask user)   → unknown
```

### Layer 2: Fact Enrichment
Night mode generates knowledge: find gap → llama1b generates → phi4-mini validates → store if both agree.
Gap strategies: weakest category, failed queries, seasonal, cross-agent.
Output: ~200 validated facts/night.

### Layer 3: Web Learning
DuckDuckGo/Wikipedia search, trusted domains (mehilaishoitajat.fi, ruokavirasto.fi).
Max 50 searches/day. Trusted=0.85, untrusted=0.65. Contradictions NOT stored.

### Layer 4: Knowledge Distillation
Claude Haiku for hard questions. ~5€/week budget. 100 questions/week → confidence=0.95.

### Layer 5: Meta-Learning
Weekly analysis: accuracy/category, hallucination/agent, response time distribution.
Safe auto-optimizations: synonyms, thresholds, cache size, batch sizes.

### Layer 6: Code Self-Review
Bottleneck + error pattern analysis. Suggestions to developer (NEVER auto-changes code).

### Combined Output: ~880 facts/night → ~26K/month → ~250K/year

## 15. PHASE 10: Micro-Models

**V1 PatternMatch (0.01ms):** Regex + lookup, 500+ patterns, zero computation
**V2 Classifier (1ms):** PyTorch Linear(768→256→128→N), 250K params, 2MB, CPU
**V3 LoRA (50ms):** smollm2:135m fine-tuned, 0.1-0.4GB VRAM, gen5+ joins Round Table

Evolution: day1(LLM only) → week2(V1+V2) → month1(+V3) → month6(V3 replaces llama1b for some)

Training data sources: round_table(0.90), user_accepted(0.85), distillation(0.95), chromadb(0.80)

## 16. PHASE 11: Elastic Scaling

```
TIER         CHAT MODEL      BG MODEL     AGENTS  VISION  MICRO
minimal      (none)          (none)       0       no      V1
light        qwen3:0.6b      smollm2:135m 2       no      V1+V2
standard     phi4-mini       llama3.2:1b  6       no      V1+V2+V3  ← CURRENT
professional phi4:14b        qwen3:4b     15      yes     V3
enterprise   llama3.3:70b    llama3.1:8b  50      yes     V3
```

Auto-detect GPU/RAM/CPU → classify tier → select models → configure agents.
Runtime: VRAM>90% → unload model, queue>10 → add agent, chat → pause background.

## 17. Dashboard

Backend: FastAPI + WebSocket. Frontend: Vanilla JS/HTMX + Chart.js.

**Panels:** Chat (confidence+source), Round Table (Theater Pipe), Agents (level+trust grid),
Learning (fact counter, radar chart, source pie), Cameras, Audio, Code Review

### WebSocket Events
```
heartbeat_insight, chat_response, round_table_start/insight/end,
agent_level_change, camera_event/alert, audio_event, learning_stats,
meta_report, code_suggestion
```

GET /api/consciousness → {memory_count, cache_hit_rate, hallucination_rate,
agent_levels, recent_corrections, seasonal_focus, learning_trend, top_queries}

## 18. FILE STRUCTURE

```
U:/project2/
├── main.py, hivemind.py, consciousness.py, translation_proxy.py
├── core/  (agent_levels, fast_memory, micro_model, night_enricher, swarm_scheduler,
│          learning_engine, meta_learning, ops_agent, adaptive_throttle, llm_provider,
│          normalizer, seasonal_guard, training_collector, web_learner,
│          knowledge_distiller, code_reviewer, yaml_bridge, knowledge_loader)
├── integrations/  (weather_feed, electricity_feed, rss_feed, data_scheduler)
├── agents/  (50 dirs with core.yaml + sources.yaml, base_agent.py, spawner.py)
├── knowledge/  (50 dirs mirroring agents/)
├── web/dashboard.py + static/
├── dashboard/  (React 18 + Vite showcase UI, port 5173)
├── backend/main.py + routes/  (standalone FastAPI stub, 1348 YAML facts)
├── configs/settings.yaml + bee_terms.yaml + seasonal_rules.yaml
├── tools/  (scan_knowledge, waggle_backup, waggle_restore, benchmark_v3, distill_from_opus)
├── tests/  (test_b2_eviction, test_b3_circuit_breaker, test_b4_error_handling,
│           test_c1_hotcache, test_c2_lru_cache, test_c3_batch_pipeline,
│           test_c4_readiness, test_c5_structured_logging, test_d1_d2_d3_autonomy)
├── data/  (waggle_dance.db, chroma_db/, finetune_*.jsonl, meta_reports.jsonl,
│          micromodel_v1/, scan_progress.json, learning_progress.json,
│          learning_metrics.jsonl)
└── memory/shared_memory.py (SQLite wrapper)
```

## 19. METRICS & MONITORING

```
PER RESPONSE: timestamp, agent_id, model, response_time_ms, confidence, hallucination, cache_hit, route
PER HOUR: facts_total/new/enriched, hallucination_rate, avg_response_ms, cache_hit_rate, vram_pct
PER DAY: queries, corrections, promotions/demotions, round_tables, enrichment_facts
PER WEEK: meta_report, code_suggestions, benchmark_score, gap_analysis
```

## APPENDIX A: GPU Budget

```
ZBook (current dev):  4.3G/8.0G GPU, 128 GB RAM, 2 TB SSD, ~150 W, €3–4K
  Standard tier: phi4-mini+llama1b+embeds+opus = 4.3G (54%)
  100K facts:    4.3G GPU, ~4.5G RAM, ~2G disk
  1M facts:      4.3G GPU, ~8G RAM (consider FAISS), ~8G disk

Professional (24GB desktop): phi4:14b(9G)+qwen3:4b(2.8G)+embeds+opus = 15.7G, 15 agents

DGX B200 (enterprise ref): 1536G HBM3e, 4096 GB RAM, 30 TB SSD, 14.4 kW, ~€475K
  llama3.3:70b+vision+50 micro, 8-way tensor parallel, ~100× faster than ZBook
```

## APPENDIX B: Dependencies

```
Phase 1 → 2 → 3 → 4 → 9 (autonomous needs Round Table)
Phase 5 → 6 (audio reuses MQTT), Phase 3 → 10 (micro-model needs RT data)
Independent: Phase 7, 8, 11
```

## APPENDIX C: Testing Checklist

```
P1: math 9/9, hallucination 4/4, search 4/5+, router, priority lock
P2: batch embed 10 <400ms, scan <30s, 94%+ benchmark, dual embed
P3: RT wisdom, agent levels, night mode, dashboard RT
P4: corrections, active learning, bilingual 55ms, hot cache 5ms, enrichment, /api/consciousness
PB: pipeline wiring(B1), eviction TTL(B2/7t), circuit breaker(B3/10t), error handling(B4/9t)
PC: hotcache auto-fill(C1/6t), LRU cache(C2/6t), batch dedup(C3/5t), readiness(C4/7t), structured log(C5/8t)
PD: external sources(D1), convergence detection(D2), weekly report(D3) — 15 tests total
P5: frigate off=ok, MQTT→ChromaDB, bear→Telegram, PTZ, visual baseline, camera context
P6: audio off=ok, spectrum→classify, bee stress→alert, BirdNET, audio context
P7: voice off=ok, Whisper FI, Piper FI, full loop
P8: feeds off=ok, weather, electricity, RSS, critical→alert
P9: enrichment 5 facts, dual validation, web trusted, distillation, meta report, code review
P10: V1 pattern, V2 train+predict, V3 LoRA, micro joins RT
P11: HW detect, tier classify, model selection fits VRAM, dynamic scale
```

## APPENDIX D: Finnish Beekeeping Glossary

```
mehilainen=bee  kuningatar/emo=queen  tyomehilainen=worker  kuhnuri=drone
parveilu=swarming  varroa=varroa mite  toukkamata=foulbrood  esikotelomata=AFB
nosema=nosema  pesa=hive  keha=frame  linkous=honey extraction  hunaja=honey
satokenha=super frame  hoitokenha=brood frame  tarha=apiary  mokki=cottage
lentoaukko=entrance  ruokinta=feeding  talvehtiminen=wintering
kevattarkastus=spring inspection  oksaalihappo=oxalic acid
muurahaishappo=formic acid  sikio=brood  siitepoly=pollen  propolis=propolis
emottaminen=requeening  mehilaishoitaja=beekeeper  ruokavirasto=Food Authority
SML=Suomen Mehilaishoitajien Liitto
```
