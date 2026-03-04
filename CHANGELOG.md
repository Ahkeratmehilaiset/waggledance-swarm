# WaggleDance Swarm AI — CHANGELOG

## v0.0.8 (2026-03-04) — Phase 6+7+11: Audio, Voice, Elastic Scaling

### New: Phase 6 — Audio Sensors (3 new modules, ~650 lines)
- `integrations/bee_audio.py` — BeeAudioAnalyzer: FFT spectrum analysis, 7-day rolling baseline per hive, stress detection (>30 Hz shift), swarming (broad 200-600 Hz), queen piping (400-500 Hz peak), Finnish status descriptions
- `integrations/bird_monitor.py` — BirdMonitor: BirdNET-Lite stub with graceful degradation, predator detection (bear/wolf/eagle/hawk/owl/fox), Finnish species name mapping
- `integrations/audio_monitor.py` — AudioMonitor orchestrator: MQTT spectrum/event/status subscriptions, BeeAudioAnalyzer + BirdMonitor routing, ChromaDB (category=audio_event), AlertDispatcher for bee stress/swarming
- `integrations/sensor_hub.py`: AudioMonitor wired (init after MQTT, before Frigate), stop/status/context
- `web/dashboard.py`: `GET /api/sensors/audio`, `GET /api/sensors/audio/bee`
- `backend/routes/audio.py` — stub endpoints with Finnish demo data
- `configs/settings.yaml`: audio section (bee_audio, bird_monitor, mqtt_topics — all disabled by default)
- `dashboard/src/hooks/useApi.js`: `audioStatus` polling
- `dashboard/src/App.jsx`: Audio awareness indicator in status bar

### New: Phase 7 — Voice Interface (3 new modules, ~550 lines)
- `integrations/whisper_stt.py` — WhisperSTT: Whisper-small on CPU, Finnish, wake word "hei waggledance", VAD 1.5s silence
- `integrations/piper_tts.py` — PiperTTS: fi_FI-harri-medium voice, WAV output, async synthesis
- `integrations/voice_interface.py` — VoiceInterface orchestrator: audio + text pipelines, graceful degradation without model
- `hivemind.py`: VoiceInterface wired (init/stop/status), follows SensorHub pattern
- `web/dashboard.py`: `GET /api/voice/status`, `POST /api/voice/text`, `POST /api/voice/audio`
- `backend/routes/voice.py` — stub endpoints for dashboard dev
- `dashboard/src/hooks/useApi.js`: `voiceStatus` polling
- `dashboard/src/App.jsx`: STT/TTS awareness indicators in status bar

### New: Phase 11 — ElasticScaler Wired
- `hivemind.py`: ElasticScaler imported, `detect()` called in `start()`, `summary()` in `get_status()`
- `dashboard/src/App.jsx`: Tier indicator in status bar (reads `elastic_scaler.tier` from API)

### New: Test Suites — 35/35 GREEN (700+ assertions)
- `tests/test_phase6_audio.py` — 25 tests: syntax, BeeAudioAnalyzer, BirdMonitor, AudioMonitor, integration, backend stub, dashboard
- `tests/test_phase7_voice.py` — 22 tests: WhisperSTT, PiperTTS, VoiceInterface, sensor_hub wiring, settings.yaml, dashboard
- `tests/test_elastic_scaler.py` — 15 tests: HardwareProfile, TierConfig, TIERS, detect(), summary(), ZBook tier classification
- `tests/test_training_collector.py` — 15 tests: collect_training_pair (accept/reject/dedup), export_for_v1/v2, save_pairs, reset
- `tests/test_swarm_routing.py` — 12 tests: register_agent, tags/skills, select_candidates, record_task_result, AgentScore defaults
- `tests/test_smart_home.py`: +4 AudioMonitor integration functions (34/34 total)

### Code Quality: print() → log.* (38 calls across 6 files)
- `core/adaptive_throttle.py`: 14 → logger.* (benchmark progress, scale_up/down box prints)
- `core/yaml_bridge.py`: 5 → log.* + `import logging` added
- `core/learning_engine.py`: 3 → logger.* (startup duplicate, prompt evolution)
- `core/ops_agent.py`: 2 → logger.* (startup duplicate, _log_decision)
- `core/knowledge_loader.py`: 2 → log.* + `import logging` added
- `hivemind.py`: 12 runtime → log.* (heartbeat skip/status, think/oracle/whisper errors, set_language)
- Registered as suite #32 in `waggle_backup.py`

---

## v0.0.6 (2026-03-04) — Phase 5 + 7: Smart Home Sensors & Voice Interface

### New: Voice Interface (Phase 7, 3 existing + 4 new files)
- `integrations/whisper_stt.py` — WhisperSTT: Whisper-small on CPU, Finnish, wake word "hei waggledance", VAD 1.5s
- `integrations/piper_tts.py` — PiperTTS: fi_FI-harri-medium voice, WAV output, async synthesis
- `integrations/voice_interface.py` — VoiceInterface orchestrator: audio+text pipelines, graceful degradation
- `hivemind.py`: VoiceInterface init in `start()`, clear in `stop()`, status in `get_status()`
- `web/dashboard.py`: 3 new API endpoints — `GET /api/voice/status`, `POST /api/voice/text`, `POST /api/voice/audio`
- `backend/routes/voice.py` — stub endpoints for dashboard dev (mock STT/TTS responses)
- `dashboard/src/hooks/useApi.js`: `voiceStatus` polling added
- `dashboard/src/App.jsx`: STT/TTS awareness indicators in status bar
- `tests/test_phase7_voice.py` — 20 tests across 6 groups (syntax, STT, TTS, VoiceInterface, dataclasses, integration)
- Registered as suite #31 in `waggle_backup.py`

---

### Phase 5: Smart Home Sensor Integration

### New: Sensor Hub (5 new modules, ~1200 lines)
- `integrations/mqtt_hub.py` — MQTTHub: paho-mqtt client, background thread with asyncio bridge, MD5 dedup (configurable window), exponential reconnect backoff (1s...60s)
- `integrations/alert_dispatcher.py` — AlertDispatcher: async queue + consumer loop, Telegram (HTML + emoji) + Webhook (3x retry), sliding window rate limiting (default 5/min/source)
- `integrations/home_assistant.py` — HomeAssistantBridge: REST API poll, auto entity discovery by domain, significance filter (numeric >1.0, binary any, brightness >20%), Finnish state formatting ("Olohuoneen valo: paalla (80%)")
- `integrations/frigate_mqtt.py` — FrigateIntegration: Frigate NVR event parsing, severity classification (bear/wolf=CRITICAL, person night=HIGH, dog/cat=MEDIUM, person/car day=INFO), Finnish label translations, 60s dedup per label+camera
- `integrations/sensor_hub.py` — SensorHub orchestrator: init order Alert->MQTT->Frigate->HA, aggregated status, Finnish sensor context for agents, WebSocket broadcast

### Wiring
- `hivemind.py`: SensorHub init in `start()`, stop in `stop()`, status in `get_status()`
- `web/dashboard.py`: 3 new API endpoints — `GET /api/sensors`, `GET /api/sensors/home`, `GET /api/sensors/camera/events`
- `configs/settings.yaml`: +mqtt, home_assistant, frigate, alerts sections (all disabled by default); +home_state/sensor_reading TTL rules

### Dependencies
- `requirements.txt`: `paho-mqtt>=2.0.0` (was commented out)

### Tests
- `tests/test_smart_home.py` — 30 tests across 7 groups (syntax, MQTT, HA, Frigate, alerts, SensorHub, integration)
- Registered as suite #23 in `waggle_backup.py`
- Full suite: 23/23 GREEN, Health Score 100/100

### Knowledge Expansion
- 21 thin agent `core.yaml` files expanded from 21-29 lines to 148-207 lines each:
  apartment_board, child_safety, commute_planner, compliance, delivery_tracker,
  forklift_fleet, indoor_garden, lab_analyst, noise_monitor, pet_care, smart_home,
  waste_handler, budget_tracker, energy_manager, hvac_industrial, maintenance_planner,
  production_line, quality_inspector, safety_officer, shift_manager, supply_chain
  Each: 5-8 DECISION_METRICS, 4 SEASONAL_RULES, 3 FAILURE_MODES, PROCESS_FLOWS,
  SOURCE_REGISTRY, 15-25 eval_questions (all Finnish)
- 28 thin `knowledge/*.yaml` files expanded from 4-5 facts to 18-20 facts each
  with Finnish thresholds, legal references, and procedures

### Core Module Test Suites (suites 24-30)
- `tests/test_core_token_economy.py` — 13 tests (TokenEconomy, ORACLE_PRICES, leaderboard)
- `tests/test_core_llm_provider.py` — 15 tests (LLMResponse, CircuitBreaker, JSON parsing)
- `tests/test_core_normalizer.py` — 14 tests (FinnishNormalizer, lemmatize, Levenshtein)
- `tests/test_core_yaml_bridge.py` — 14 tests (YAMLBridge, ROUTING_KEYWORDS, AGENT_GLYPH_MAP)
- `tests/test_core_fast_memory.py` — 17 tests (HotCache put/get/LRU, stats, clear)
- `tests/test_core_ops_agent.py` — 12 tests (ModelProfile, OpsDecision, thresholds)
- `tests/test_core_learning_engine.py` — 18 tests (QualityScore, AgentPerformance, leaderboard)
- Full suite: **30/30 GREEN, 700 ok, 0 fail, Health Score 100/100**

### Dashboard (App.jsx)
- EN: added v0.0.6 Phase 5 Smart Home Sensors section (MQTT Hub, Frigate NVR, Home Assistant, Alert Dispatcher)
- FI: added v0.0.6 Vaihe 5 Kodin Sensorit section
- Test suite count 22 -> 23 in tech arch (EN+FI), boot animation, status bar

### GitHub
- Added topic: `smart-home`

---

## v0.0.5d (2026-03-03) — Codebase Audit: Critical Fixes

### Critical: Hardcoded Paths (backend/routes/status.py)
- `"U:/project"` → dynamic `Path(__file__)` relative paths
- ChromaDB client now uses correct project root
- `agents_active` now dynamically counts agent directories (was hardcoded 6)

### Critical: Voikko Search Paths (3 files)
- `core/normalizer.py`, `translation_proxy.py`, `core/auto_install.py`
- `r"U:\project\voikko"` → `r"U:\project2\voikko"` (correct project root)

### Medium: Outdated Facts/Agent Counts (chat.py — 11 locations)
- `"1,348+"` → `"3,147+"` facts (EN fallbacks, system responses)
- `"1 348+"` → `"3 147+"` facts (FI system responses)
- `"50 agents"` / `"50 agenttia"` → `"75"` everywhere
- Docstring: "50 agents" → "75 agents"

### Medium: Agent Count Comments
- `backend/routes/heartbeat.py`: "50-agent" → "75-agent" (docstring + comment)
- `core/swarm_scheduler.py`: "50 agentin" → "75 agentin" (docstring)

### Medium: CORS Configuration (backend/main.py)
- Hardcoded `localhost:5173` → env variable `CORS_ORIGINS` (default: 5173+3000)

### Low: Version Strings (6 core modules)
- `token_economy.py`, `live_monitor.py`, `adaptive_throttle.py`, `yaml_bridge.py`,
  `whisper_protocol.py`, `llm_provider.py` — all `v0.0.1` → `v0.0.5`

---

## v0.0.5c (2026-03-03) — Language Architecture + Compelling Product Text

### Language Architecture (App.jsx EN + FI)
- New LANGUAGE ARCHITECTURE section in tech arch info (both languages)
- Explains morphological NLP pipeline: Your Language → Engine → Lemmatization → Compound Splitting → Translation → LLM
- Language engine swap table: Voikko (FI), Hunspell (DE/ES/FR), MeCab (JA), KoNLPy (KO), jieba (ZH), spaCy (any)
- English = fastest path (no translation overhead, direct LLM access)

### README.md — Rewritten for AI Scene
- Hero tagline: "Works in any language. English natively. Non-English through deep morphological NLP."
- Added MicroModel self-training pitch: "trains its own models from your data"
- New section: **Language Architecture** — full table of 7 language engines
- Key Features: bilingual → language-agnostic, added Night Shift automation
- Health Score 100/100 in header

### GitHub About + Topics
- Description: "75 self-learning agents that debate, evolve, and train their own models -- on any hardware, in any language. 3000ms to 0.3ms. Zero cloud."
- Topics (14): +swarm-intelligence, +nlp, +morphological-analysis, +language-agnostic, +smart-home, +digital-twin

### GitHub PAT
- Stored in `.env` (git-ignored), configured in git credential manager

---

## v0.0.5b (2026-03-03) — UI + Docs: Bilingual update, Phase 8 active, v0.0.5 highlights

### Dashboard UI (App.jsx — EN + FI)
- Agent count: "50 CUSTOM AGENTS" / "50 MUKAUTETTUA AGENTTIA" → **75**
- Cottage guide: "Day 1: 1,348 base facts" → **3,147+** (both languages)
- Codebase stats: 50 agents → **75** in tech arch section
- Tech arch info added: Phase 7 (voice), Phase 8 (feeds active), v0.0.5 section
  - Voikko bundled, Night Shift automation, fact persistence, Health Score 100/100

### README.md
- Phase 8: 📋 specified → **✅ active** (DataFeedScheduler wired in hivemind.py:920-941)
- v0.0.5 milestone added to Current Status list
- Performance table: night learning note added

---

## v0.0.5 (2026-03-03) — 5 Fixes: Health Score, Voikko, Counters, Night Shift

### Fix 1: Health Score 95→100
- **Root cause:** `waggle_backup.py:print_test_summary()` hardcoded `mass_test: None` → always -5
- **Fix:** Pass real `data_stats` to health score calculation
- **Result:** Health Score now correctly reflects actual test + routing data

### Fix 2: Voikko Runtime + Portability
- **Root cause:** `voikko/5/mor-standard/` had only `index.txt` — missing `mor.vfst` (3.9MB) + `libvoikko-1.dll` (1.2MB)
- **Downloaded** complete Voikko dictionary (VFST format) + bundled DLL in `voikko/`
- **Finnish lemmatization working:** `pesässä → pesä`, `mehiläispesässä → mehiläinen pesä`
- **`waggle_restore.py`**: now validates DLL + .vfst files + actual `Voikko("fi")` init
- **`core/auto_install.py`**: auto-downloads dictionary if .vfst files missing
- **Portability:** voikko/ dir (dict + DLL) travels with git repo and backup zip

### Fix 3: Phase 8 External Data Feeds — verified
- Already fully wired in `hivemind.py:920-941` (DataFeedScheduler)
- No code changes needed — weather, electricity, RSS all configured

### Fix 4: Fact Counter Persistence
- **Root cause:** `_night_mode_facts_learned` reset to 0 on every startup AND on night mode activation
- **Fix:** Load persisted counter from `learning_progress.json` at startup
- **Fix:** Removed counter reset on night mode re-activation
- **Added** `enricher_session_stored` to progress JSON for transparency

### Fix 5: Night Shift Automation
- **New:** `tools/night_shift.py` — standalone headless night monitor
- Starts WaggleDance as subprocess, health check every 5 min
- Watchdog: 3 consecutive failures → restart (max 5 restarts)
- Hallucination alert: >5% → logged as error
- Writes morning report at shutdown: `data/night_shift_report_YYYYMMDD.md`
- Windows Task Scheduler compatible
- `--report-only` mode for generating reports from existing data

### Test Results
- **22/22 suites GREEN**, 700 assertions, 0 failures
- **Health Score: 100/100**
- **Voikko:** libvoikko + DLL + dictionary OK (2 .vfst)
- **Restore:** 13/13 OK

---

## v0.0.4 (2026-03-02) — 4-Area Improvement: Observability, Learning, Agents, New Features

### Docs & GitHub
- **README.md**: all "50 agents" → 75, facts 1,348+ → 3,147+, Phase 7+11 marked complete, tier names aligned with ElasticScaler
- **CHANGELOG.md**: v0.0.4 entry added
- **GitHub About**: repo description updated via API (50 → 75 agents)

### Area 1: Observability Pipeline Fix
- **Weekly report auto-trigger**: `_maybe_weekly_report()` runs every 50 heartbeats, no longer depends on night mode
- **StructuredLogger**: verified 7 `log_chat()` call sites in `_do_chat()`, metrics accumulate correctly
- **meta_reports.jsonl**: cleaned stale entries, fresh reports append correctly

### Area 2: Autonomous Learning Restart
- **ConvergenceDetector tuning**: threshold 0.20→0.10, patience 3→5, pause 1800s→900s
- **AdaptiveTuner**: throttle_pause_seconds 600→300
- **Category rotation**: 10 agent groups cycled during enrichment (bee, weather, garden, etc.)
- **Seasonal boosts**: month-based 1.5x weight for relevant agents (March = spring inspection)
- **Enrichment cycle logging**: each cycle logs to `learning_metrics.jsonl` via StructuredLogger

### Area 3: Agent Enrichment + Routing
- **12 thin agents expanded**: apartment_board, child_safety, commute_planner, compliance, delivery_tracker, forklift_fleet, indoor_garden, lab_analyst, noise_monitor, smart_home, waste_handler, pet_care — each 21→72-81 lines
- **WEIGHTED_ROUTING expanded**: 14→72 agents with Finnish stem keywords
- **MASTER_NEGATIVE_KEYWORDS**: +14 specialist terms for better delegation
- **fishing_guide "verkko" fix**: demoted to secondary keyword, "kalaverkko" as primary (no more zigbee misrouting)

### Area 4: New Features
- **Phase 7 — Voice Interface** (integrations/):
  - `whisper_stt.py`: Whisper-small STT, Finnish, wake word "Hei WaggleDance", VAD 1.5s
  - `piper_tts.py`: Piper fi_FI-harri-medium TTS, WAV output
  - `voice_interface.py`: full pipeline Mic→STT→Chat→TTS→Speaker, WebSocket streaming
  - Graceful degradation: `voice: {enabled: false}` → system runs normally
- **Phase 11 — Elastic Scaling** (core/elastic_scaler.py):
  - Auto-detects GPU VRAM, RAM, CPU cores via nvidia-smi/wmic
  - 5 tiers: minimal / light / standard / professional / enterprise
  - Runtime scaling: VRAM >90% → unload model, queue >10 → spawn agent
  - ZBook correctly detected as "standard" tier (8GB VRAM, 127GB RAM)

### Prior Audit Fixes (included in this commit)
- **9-fix agent audit**: test_pipeline.py 8 fail→0, test_phase10.py 3 fail→0
- **Night learning 4 root causes fixed**: init time, guarded bypass, last-source protection, attr name
- **waggle_backup.py**: ChromaDB no longer excluded from backups
- **waggle_restore.py**: new environment validator (13 checks)

### Test Results
- **22/22 suites GREEN**, 700 assertions, 0 failures
- **Health Score: 95/100**
- 5 Finnish chat queries verified: correct routing, metrics growing

---

## v0.0.3 (2026-02-22) — Audit Fix + Timeout-esto + Dashboard + OpsAgent + Learning

### FIX-14: Kuolemanspiraali-esto + Chat-prioriteetti + CPU-pakotus
**Juurisyy**: Heartbeat (7b) ja Chat (32b) MOLEMMAT GPU:lla!
Ollama lataa oletuksena kaiken GPU:lle → 7b+32b=25GB > 24GB VRAM
→ Ollama vaihtaa mallia joka kerta (60-120s) → OpsAgent hätäjarru.

**Korjaukset:**

1. **num_gpu: 0** (llm_provider.py + settings.yaml):
   → Pakottaa heartbeat-mallin CPU:lle Ollama API:n kautta
   → 32b pysyy GPU:lla AINA, 7b pyörii CPU:lla rinnakkain
   → Ei mallinvaihtoa, ei 60-120s viivettä!

2. **Chat-prioriteetti** (hivemind.py):
   → `_chat_active` flag pysäyttää heartbeat-loopin chatin ajaksi
   → 30s cooldown chatin jälkeen

3. **OpsAgent kuolemanspiraali-esto** (ops_agent.py):
   → Suodattaa >60s latenssit pois (mallinvaihto, ei ylikuorma)
   → Emergency ei toista jos jo minimissä (1→1 spämmi pois)
   → AUTO-RECOVERY: 3min minimissä → kokeilee varovasti conc→2
   → Kynnykset nostettu: 15s→30s kriittinen, 8s→12s varoitus

4. **Insight-spämmi** (hivemind.py): Max 2 vastaanottajaa per insight

5. **Dashboard-layout** (dashboard.py): Token Economy vasemmalle

6. **Mallisuositukset** CPU:lle suomeksi (settings.yaml):
   aya-expanse:8b, gemma2:9b, qwen2.5:7b

### FIX-6: UTF-8 encoding (Windows)
- `hivemind._load_config()`: `open(path, encoding="utf-8")`
- `main.py`: `sys.stdout.reconfigure(encoding="utf-8")` Windows-konsolille
- `dashboard.py`: `<meta charset="utf-8">` HTML:ään → ääkköset näkyvät selaimessa

### FIX-7: Timeout-tulva esto (heartbeat task gate)
**Ongelma**: Heartbeat laukaisi 6-10 rinnakkaista `asyncio.create_task()` LLM-kutsua.
Ollama (qwen2.5:32b) ei pysy perässä → timeout-kaskadi.

**Ratkaisu**:
- `_guarded()` wrapper: pending task counter, max 3 rinnakkaista
- Jos `_pending >= 3` → koko heartbeat-kierros SKIPATAAN
- Adaptiivinen intervalli: `throttle.state.heartbeat_interval` (ei kiinteä 30s)
- idle_research: max 1 agentti kerrallaan (oli 2)
- Priority invite: max 1 kerrallaan (oli 2), harvemmin (joka 15. HB)

### FIX-8: Audit — päivämääräharha-esto
Kaikkiin system prompteihin injektoidaan:
```
AIKA:
- Käytä VAIN järjestelmän sinulle antamaa päivämäärää.
- ÄLÄ koskaan päättele nykyistä päivää itse.
- Jos aikaa ei anneta, vastaa: "Ajankohta ei tiedossa."
```

### FIX-9: Audit — Swarm Queen tyhmä reititin
Master/Queen system prompt päivitetty:
- "SINÄ ET analysoi sisältöä, tee johtopäätöksiä"
- "SINÄ SAAT tunnistaa agentin, välittää kysymyksen"
- MASTER_NEGATIVE_KEYWORDS: varroa, afb, karhu, sähkö... → pakko-delegointi

### FIX-10: Dashboard v0.0.3
- Header: Chat-malli (GPU) + Heartbeat-malli (CPU) + dynaamiset palkit
- Title: "WaggleDance Swarm AI" kirkas + "(on-prem)" himmeä
- `/api/system` endpoint: psutil CPU% + nvidia-smi GPU%
- Throttle-tilastot dashboardissa
- Timeout-laskuri Live Feedissä
- Swarm ENABLED/DISABLED badge

### FIX-11: AdaptiveThrottle ei toiminut — benchmark() kutsumatta (JUURISYY)
**Ongelma**: `benchmark()` EI kutsuttu `start()`:ssa → semafori=None → ei rajoitusta.

**Ratkaisu**:
- `start()`: kutsutaan `await throttle.benchmark(llm_heartbeat)`
- `__init__`: semafori luodaan AINA (fallback)
- `record_error()`: reagoi 2 peräkkäiseen virheeseen HETI
- `_scale_down/up()`: print() konsoliin
- `settings.yaml`: heartbeat timeout 60→180s (model switch delay)

### FIX-12: OpsAgent — reaaliaikainen järjestelmävalvonta-agentti (UUSI)
**Idea**: Dedikoitu agentti monitoroi Ollaman todellista tilaa, säätää throttle-arvoja
mittausten perusteella ja arvioi mallien suorituskykyä eri kuormituksilla.

**Toteutus** (`core/ops_agent.py`, 480 riviä):
- **Ei käytä LLM:ää** monitorointiin — puhdas mittaus + heuristiikka
- Oma 15s sykli (irrallaan heartbeatista)
- Monitoroi: Ollama `/api/ps`, nvidia-smi GPU, psutil CPU, latenssitrendi
- Säätää: max_concurrent, heartbeat_interval, idle_every_n_heartbeat
- Hätäjarru: kriittisellä latenssilla → concurrent=1, idle=OFF
- Palautus: stabiili jakso → palauttaa idle-tutkimuksen
- **ModelProfile**: jokaiselle mallille oma suoritusprofiili
  - avg_latency, p95_latency, error_rate, quality_score, efficiency_score
  - Laadun arviointi LLM:llä harvoin (~joka 7.5min, 2 testikysymystä)
- **Mallisuositukset**: vertailee malleja efficiency = laatu×nopeus×luotettavuus
- Dashboard: OpsAgent-kortti malliprofiileilla + päätöshistorialla
- `/api/ops` endpoint: status + recommendation
- WebSocket: päätökset reaaliajassa Live Feediin
- `settings.yaml`: konfiguroitavat kynnysarvot

**Hierarkia**:
```
OpsAgent (portinvartija)
  └→ AdaptiveThrottle (säädettävät parametrit)
       └→ heartbeat_loop (_guarded gate)
            └→ proactive_think, idle_research, whisper...
```

### FIX-13: LearningEngine — suljettu oppimissilmukka (UUSI)
**Ongelma**: Dataa kerättiin (finetune_live.jsonl, token_economy, pheromone)
mutta mikään ei oppinut — ei palautesilmukkaa.

**Toteutus** (`core/learning_engine.py`, 560 riviä):

**Käyttää 7b (CPU/heartbeat-mallia)** arviointiin:
- Arviointi on yksinkertainen (1-10) → 7b riittää
- Ei kilpaile 32b:n kanssa GPU:sta
- Pyörii taustalla 30s syklillä

**1. QualityGate** — jokainen vastaus arvioidaan:
- 7b pisteyttää 1-10 (relevanssi, tarkkuus, hyödyllisyys, selkeys)
- Pisteet → SwarmScheduler pheromone (hyvä=success, huono=correction)
- Pisteet → TokenEconomy (9+/10 = +15 tokenia, <3/10 = -3 tokenia)
- Hyvät (7+) → `data/finetune_curated.jsonl` (puhdas finetune-data)
- Huonot → `data/finetune_rejected.jsonl` (negatiiviset esimerkit)

**2. PromptEvolver** — heikot agentit kehittyvät:
- Seuraa jokaisen agentin laatutrendiä
- Jos avg <5.0 ja laskeva trendi → 7b ehdottaa uutta system promptia
- A/B-testi: vanha vs uusi (min 3 arviointia kummallekin)
- Parempi jää voimaan, kaikki muutokset audit-lokiin
- `auto_evolve: false` oletuksena — kytke päälle kun dataa riittävästi

**3. InsightDistiller** — parhaat oivallukset tiivistyvät:
- Joka 15min: kerää 15 viimeisintä insightia
- 7b tiivistää → 1 strateginen fakta
- Tallennetaan "distilled"-tyyppinä (importance=0.9)
- Leviää kontekstina relevanteille agenteille

**4. PerformanceTracker** — kuka paranee, kuka huononee:
- Agenttikohtainen profiili: avg, trendi, good_rate
- Dashboard: laatutaulukko + ⚠️ avun tarve
- `/api/learning` endpoint: status + leaderboard

**Tiedostot**:
```
data/finetune_live.jsonl      ← kaikki (raaka, kuten ennenkin)
data/finetune_curated.jsonl   ← vain 7+/10 (puhdas finetune-data)
data/finetune_rejected.jsonl  ← alle 7/10 (negatiiviset)
data/learning_audit.jsonl     ← kaikki prompt-muutokset
```

## v0.0.2 (2026-02-22 18:00 EET)

### FIX-1: Prompt-restore bugi korjattu
**Ongelma**: `chat()`:n finally-lohkossa `_orig_kb` ylikirjoitti `_orig_prompt`:n,
jolloin agentin system_prompt jäi väärään tilaan (sisälsi date prefix:n).
Samankaltainen ongelma `_agent_proactive_think`:ssä ja `_idle_research`:ssä.

**Ratkaisu**: Uusi `_enriched_prompt()` context manager joka:
1. Tallentaa ALKUPERÄISEN system_promptin KERRAN
2. Injektoi date + knowledge
3. `finally:` palauttaa AINA alkuperäiseen
4. Käytetään kaikissa poluissa (chat, proactive_think, idle_research)

**Vanhat `_inject_knowledge()` ja `_restore_prompt()` säilytetään** backward-compat:ille
(whisper_cycle käyttää niitä edelleen).

### FIX-2: Swarm Scheduler kytketty chat()-reititykseen
**Ongelma**: Scheduler oli luotu mutta sitä EI käytetty reitityksessä.
Kaikki 50 agenttia evaluoitiin keyword-scorella joka viestille.

**Ratkaisu**: Uusi `_swarm_route()` pipeline:
- (A) Poimi task_tags viestistä
- (B) `scheduler.select_candidates()` → Top-K shortlist (6-12 agenttia)
- (C) Keyword-score VAIN shortlistatuille
- (D) Jos tyhjä → fallback `_legacy_route()` (vanha käytös)

### FIX-3: Feature flag `swarm.enabled`
**Ongelma**: Ei tapaa kytkeä scheduler pois käytöstä.

**Ratkaisu**: `settings.yaml`:iin `swarm.enabled: true/false`.
Kun `false`, käytetään vanhaa `_legacy_route()` -polkua (100% yhteensopiva).

### FIX-4: Auto-register hook
**Ongelma**: Scheduler tunsi vain main.py:n AUTO_SPAWN-listalla olevat agentit.

**Ratkaisu**:
- `register_agent_to_scheduler(agent)` — kutsutaan spawnin yhteydessä
- `bulk_register_agents_to_scheduler()` — rekisteröi kaikki kerralla
- `scheduler.register_from_yaml_bridge()` — oppii kaikki 50 agenttia YAMLBridgestä
- Ei vaadi muutoksia agents/-kansioon

### FIX-5: Backward-compat wrappers
**Ongelma**: `get_knowledge()` puuttui knowledge_loaderista (vanha API).

**Ratkaisu**:
- `get_knowledge()` wrapper → delegoi `get_knowledge_summary()`:iin
- `get_agent_metadata()` → palauttaa skills/tags schedulerille
- Kaikki vanhat importit ja funktiot toimivat edelleen

### Muut muutokset:
- `swarm_scheduler.py`: `agent_count` property, tags AgentScoreen, `register_from_yaml_bridge()`
- `settings.yaml`: `swarm.enabled: true` lisätty
- `SCHEDULER_INTEGRATION.md`: Dokumentaatio agenttiintegratiosta

---

## v0.0.1 (2026-02-22 14:37 EET)

### Bugikorjaukset (analyysidokumentista):
- K1: Triple `_read_yaml()` → yksi
- K4: Colony count 202 (ei 300)
- K5: Painotettu reititys (PRIMARY_WEIGHT=5)
- K6: Knowledge injection laajennettu (2000 chars)
- K7: idle_research max 2 agenttia
- K8: LLM retry logic + error handling
- K9: Kontekstuaalinen routing scoring
- K10: Response validation (`_is_valid_response`)

### Uudet ominaisuudet:
- Swarm Scheduler (8 parannusta)
- Date injection kaikkiin agentteihin
- Dashboard swarm stats
