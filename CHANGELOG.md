# WaggleDance Swarm AI — CHANGELOG

## [1.0.0] — 2026-03-13

### Symbolic Layer — Domain Capsule Architecture

#### Added
- **Domain Capsule system** (`core/domain_capsule.py`): profile-based reasoning layer configuration (gadget/cottage/home/factory) with YAML capsules
- **SmartRouter v2** (`core/smart_router_v2.py`): 4-step query routing (HotCache -> capsule match -> keyword classifier -> priority fallback)
- **Model-based interface** (`core/model_interface.py`): ModelResult dataclass with `to_dict()` and `to_natural_language(fi/en)`
- **Symbolic Solver** (`core/symbolic_solver.py`): axiom-YAML-driven formula evaluation with safe eval, input extraction from natural language
- **Constraint Engine** (`core/constraint_engine.py`): capsule rule evaluation with expression-based conditions, bilingual messages
- **Explainability Engine** (`core/explainability.py`): step-by-step decision traces with NL output
- **Domain Model Miner** (`core/domain_model_miner.py`): stub for SchemaInspector, DocumentReader, LayerRecommender
- **10 axiom YAMLs** across 4 domains (heating_cost, pipe_freezing, battery_discharge, OEE, etc.)
- **API endpoints**: `/api/capsule`, `/api/route?q=...`
- **Chat pipeline**: model-based solver intercept (query -> router -> solver -> NL response)
- **Chat pipeline**: rule_constraints intercept (query -> router -> constraint engine -> NL response)

#### Changed
- UI terminology: CONSCIOUSNESS -> RUNTIME STATE, NEURAL ACTIVITY -> RUNTIME ACTIVITY
- README rewritten: domain-agnostic runtime description, deployment profiles table
- Version bumped to v1.0.0

#### Tests
- 53 new tests: test_domain_capsule (34), test_model_e2e (41), test_symbolic_solver (12), test_constraint_engine (9), test_explainability (6)

## [0.9.2] — 2026-03-11

### Production Hardening & Live Optimization

#### Fixed
- **V1 API wiring:** `self.micro_model = v1` in `_warm_caches()` — API now exposes V1 stats (was always 0)
- **Agent rotation bias:** GapWeightedScheduler `max()` → sum for fact counts, category rotation 33%→50%
- **Seasonal boost:** Conditional on knowledge gap + reduced 1.5x→1.3x (prevents beekeeper domination)
- **6 production bugs:** Silent exceptions, WebSocket callback leak, throttle miscalibration, queue cap
- **CogGraph RT edges:** `_fact_id` NameError before assignment
- **Round Table API:** Dashboard-compatible fields (agreement, discussion)
- **TrustEngine API:** `_cache` not `_scores` for agents_tracked; stats from SQLite
- **Unicode chat bug:** cp1252 encoding crash on Windows console
- **Novelty collapse:** Night learning convergence detector reset fix
- **Agent field:** Chat API + metrics `model_used` in enrichment logs
- **QualityGate:** Silent novelty check exception now logged
- **AgentGridPanel:** Data format + finetune rotation + periodic checks scheduling
- **Rejection category:** `not_curated` → `not_curated_quality`
- **Test alignment:** idle 30→15min, novelty 0.85→0.80, `_run_periodic_checks` refactor

#### Improved
- **Domain-aware night learning:** Agents generate content in their own domain
- **Night learning params:** Batch 3→8, benchmark 5→2, idle 30→15min, novelty 0.85→0.80
- **Composite scoring:** Anti-ceiling bias (10.0→9.2), 50/50 weights, generic penalty
- **MicroModel V1 fallback:** `memory_engine.py` uses `_v1_engine` in prefilter route
- **Batch embedding:** GapWeightedScheduler embed scan performance
- **LLM validation prompt:** Less strict ("When unsure, prefer VALID")
- **Self-generate candidates:** 3→5 per cycle, rotating prompt templates

#### Added
- **MAGMA deep wiring:** Cross-agent search, provenance tracker, boilerplate filter
- **CognitiveGraph enrichment:** Round Table edges + MAGMA stats API
- **Anti-repetition API:** Production endpoints for enrichment stats
- `tools/live_monitor.py` — continuous monitoring (5-min intervals, JSONL output)

#### Test Results
- **50/50 suites, 698 ok, 0 fail, 15 warn, Health 100/100**

## [0.9.1] — 2026-03-10

### Week 11 Sprint — Data Quality & Learning Pipeline

#### Fixed
- **BUG-6:** V1 PatternMatchEngine trained with 7912 curated Q/A pairs (was 0 patterns + 1 test junk entry)
  - 2294 lookups, 2293 regex patterns from `finetune_curated.jsonl`
- **Hash-dedup:** QualityGate now has Step 0 MD5 hash-based dedup before LLM call (saves ~30% LLM calls on duplicates)
- **Boilerplate:** System prompt boilerplate ("OLETUKSET JA KONTEKSTI") stripped from 2700 curated entries
- **model_used:** All 8 `log_chat()` calls in `chat_handler.py` now include `model_used` field
- **Rejection reasons:** Auto-generated rejection categories (very_low_quality/below_average/not_curated_quality)

#### Improved
- **QUALITY_EVAL_PROMPT:** Added score examples (3/10, 5/10, 7/10, 8.5/10) to encourage full 1-10 scale usage
- **Composite scoring:** LLM score (60%) + length bonus + domain specificity bonus for non-binary quality distribution

#### Added
- **RSS feeds activated:** `rss_feed_enabled: true`, 8 feeds configured (SML, Ruokavirasto, FMI, BeeHealth, HA-blog, etc.)
- `tools/train_v1_now.py` — one-off V1 training script
- `tools/clean_curated.py` — one-off curated data cleanup
- `configs/boilerplate_blacklist.txt` — boilerplate pattern list
- 5 new test suites (26 tests): V1 Training, Dedup Gate, Metrics Fields, Quality Scoring, RSS Activation

## [0.9.0] — 2026-03-09

### Major Refactor
- **hivemind.py refactored:** 3321 → 1382 lines (58% reduction)
- **4 new controller modules extracted:**
  - `core/chat_handler.py` (716 lines) — chat routing, swarm routing, multi-agent collaboration
  - `core/night_mode_controller.py` (455 lines) — night learning cycle, learning engines, weekly report
  - `core/round_table_controller.py` (476 lines) — Round Table debates, agent selection, theater streaming
  - `core/heartbeat_controller.py` (662 lines) — heartbeat loop, proactive thinking, idle research, whisper cycle

### Added
- **Phi-3.5-mini LoRA pipeline** — 4-bit NF4 quantization, CPU offloading, 2.92GB VRAM validated
  - Auto-detection of LoRA target modules (fused qkv_proj vs separate q/v)
  - Paged AdamW 8-bit optimizer, gradient checkpointing
  - `--load-test` flag for VRAM validation before training
- Training report JSON output (`models/micromodel_v3_phi35/training_report_v3.json`)

### Fixed (Sonnet Review — 12 fixes)
- **C2:** `_write_lock` moved from class-level to instance-level in AuditLog and TrustEngine
- **C3:** `/api/voice/audio` — added 10MB body size limit
- **H2:** `recall()` LIKE wildcard escaping in SharedMemory (SQL injection prevention)
- **H3:** `/api/profile` — atomic settings.yaml write via `os.replace()`
- **H4:** `/api/chat` — JSON parse errors return 400 instead of 500
- **H5:** MAGMA route errors logged instead of silently swallowed
- **H6:** `/api/history` query param validation with 400 on bad input
- **L1:** Duplicate import removed in spawner.py
- **L6:** confusion_memory.json uses `os.replace()` for atomic writes
- **M2:** `_notify_ws` iterates snapshot copy of callbacks
- **M6:** BFS in CognitiveGraph uses `deque` instead of `list.pop(0)`
- **M7:** nvidia-smi uses `asyncio.create_subprocess_exec()` instead of blocking `subprocess.run()`

### Changed
- `datetime.utcnow()` deprecation fixes across codebase
- Agent field added to `/api/chat` response
- TrustEngine wiring fixes
- NightEnricher wiring fixes

## [0.8.0] — 2026-03-09

### Fixed (Critical)
- **FIX-1:** Blocking `requests.get` in async `_readiness_check` — wrapped with `asyncio.to_thread()`
- **FIX-2:** `/api/auth/token` endpoint restricted to localhost only (127.0.0.1, ::1)
- **FIX-3:** SQLite concurrent write corruption — added `threading.Lock` to AuditLog and TrustEngine
- **FIX-4:** Replaced deprecated `asyncio.get_event_loop()` with `get_running_loop()` in 6 modules
- **FIX-5:** SQL injection in `SharedMemory.update_task` — added column name whitelist

### Fixed (High Priority)
- **FIX-6:** WebSocket callbacks never unregistered — added unregister on disconnect + 50-entry cap
- **FIX-7:** `_whisper_cycle` race condition — uses enriched copies instead of direct prompt mutation
- **FIX-8:** Double `KnowledgeLoader` instantiation at startup — deduplicated to single instance
- **FIX-9:** Non-atomic writes to `learning_progress.json` — uses temp file + `os.replace()`
- **FIX-10:** Bare `except Exception: pass` replaced with logged warnings in production code
- **FIX-11:** Whisper cycle now respects PriorityLock for chat contention
- **FIX-12:** `learning_metrics.jsonl` unbounded growth — added rotation (10MB limit, 30-day retention)

### Changed
- LiveMonitor and OpsAgent now have `unregister_callback` / `unregister_decision_callback` methods
- StructuredLogger rotates metrics file when exceeding 10MB
- All `asyncio.get_event_loop()` calls updated to `get_running_loop()` (Python 3.12+ compatible)

## [0.7.0] — 2026-03-08

### Added
- Active profile switching — GADGET/COTTAGE/HOME/FACTORY tabs now change agent behavior in real-time
- Model status dashboard — real-time VRAM usage, loaded models, role mapping via /api/models
- Persistent chat history — SQLite storage, conversations survive page refresh
- User feedback system — thumbs up/down on AI responses, feeds into corrections memory
- GitHub Actions CI — automated test runner on every push
- Test runner wrapper (tests/run_all.py) for standalone test execution
- pyproject.toml with proper metadata
- CORS middleware for React dev server compatibility
- Body size limit on /api/chat (100KB)
- docs/DASHBOARD_IMPROVEMENTS.md — improvement plan and investigation

### Fixed
- /ready endpoint NameError (hive → hivemind)
- /api/history route conflict (specific routes before parameterized)
- Dashboard auth headers missing on /api/language and /api/hardware calls
- Voice chat TypeError — _do_chat now accepts source parameter
- Weekly report crash — _init_learning_engines sync/async mismatch
- Audio MQTT handlers — sync handlers registered as async callbacks
- Bee alert dispatch — raw dict replaced with proper Alert dataclass
- Causal replay embedding dimension mismatch (8-dim → 768-dim)
- Race condition: per-request state moved from instance vars to locals in _do_chat
- Race condition: _enriched_prompt now yields copy, original agent untouched
- Race condition: TrustEngine gets own SQLite connection instead of sharing AuditLog's
- Race condition: semaphore scaling without replacing active semaphore
- Resource leak: _whisper_task now cancelled on stop()
- Resource leak: VoiceInterface properly stopped before disposal
- Shutdown order: background tasks cancelled before database close
- Unbounded growth: _task_history capped with deque(maxlen=10000)
- Unbounded growth: replay JSONL rotation at 50MB
- Unbounded growth: RSS _seen_ids capped at 50,000 entries
- Synchronous nvidia-smi replaced with async subprocess
- WebSocket monitor callback now properly awaits send_json
- Polling interval reduced from 2s to 5s (360→72 req/min)
- Stub/production URL mismatch for agent levels endpoint
- NightEnricher config flags now actually control feature activation
- networkx added to requirements.txt
- chromadb version constraint tightened (>=1.0.0)
- Python version requirement corrected (>=3.13)
- Version number synchronized across main.py and pyproject.toml (0.7.0)
- Duplicate regex key removed from confusion_memory.json

### Changed
- consciousness.py renamed to core/memory_engine.py (done in earlier session)
- Internal docs moved to docs/internal/
- Test files organized into tests/ directory
- README rewritten — removed superlatives, added limitations, added measurement context
- Static test badge replaced with live GitHub Actions badge

## v0.2.0 (2026-03-07) — Release: All Phases Complete

Production-ready release. All planned phases implemented and tested (45/45 suites, 700 tests).

### Phases completed in v0.1.3–v0.2.0:
- **Phases 1–4:** Consciousness engine, batch pipeline, Round Table consensus, advanced learning
- **Phase 5:** Smart home sensors (MQTT, Frigate NVR, Home Assistant, alert dispatcher)
- **Phase 6:** Audio sensors (BeeAudioAnalyzer, BirdMonitor, AudioMonitor)
- **Phase 7:** Voice interface (Whisper STT, Piper TTS, VoiceInterface)
- **Phase B–C:** Pipeline wiring, priority lock, circuit breaker, HotCache, structured logging
- **Phase D1–D3:** External sources, convergence detection, weekly report
- **Phase 11:** ElasticScaler (hardware auto-detection, tier classification)
- **MAGMA L1–L5:** Audit log, replay engine, overlays, cross-agent search, trust engine
- **Cognitive Graph:** NetworkX-based knowledge graph with causal replay
- **Hardening:** Atomic writes, input validation, rate limiting, CORS, graceful shutdown
- **Phase 9:** Documentation overhaul (6 docs, README rewrite, Docker healthcheck)
- **Agent YAML Tests:** 380 validation tests across 75 agents
- **Disk Guard:** Write-path protection with dashboard indicator
- **Schema Migration:** Versioned SQLite migrations with CLI tool
- **API Authentication:** Bearer token middleware with auto-key generation
- **LoRA Tooling:** Training data collector + Unsloth/PEFT training scripts

### Test results: 45/45 suites, 700 ok, 13 warn, Health Score 100/100

---

## v0.1.9 (2026-03-07) — Schema Migration, API Auth, LoRA Training

### Phase 6: Schema Migration
- `tools/migrate_db.py` — schema version tracking for all SQLite DBs
- `schema_version` table with version + updated_at
- Migrations: audit_log.db v1→v2 (content_preview column), waggle_dance.db v1→v2 (performance indexes)
- CLI: `--check` reports versions, `--migrate` applies pending
- Updated `docs/MIGRATIONS.md` with schema versioning docs

### Phase 7: API Authentication
- `backend/auth.py` — BearerAuthMiddleware + auto-key generation
- Bearer token required for all `/api/*` routes
- Exempt: `/health`, `/ready`, `/api/status`
- WebSocket auth via `?token=` query param
- Auto-generates `WAGGLE_API_KEY` on first startup, saves to `.env`
- Dashboard sends token from `localStorage.WAGGLE_API_KEY`
- Updated `docs/SECURITY.md`, `.env.example`

### Phase 8: MicroModel V3 LoRA Tooling
- `tools/collect_training_data.py` — CLI to extract Q&A pairs from finetune_live.jsonl + curated
- `tools/train_micromodel_v3.py` — LoRA training with Unsloth or PEFT fallback
- Supports TinyLlama, Phi-3.5, smollm2 base models
- `--check` flag to verify dependencies without training

### Tests
- `tests/test_migrate_db.py` — 20+ tests: migration, auth, training tools (suite #45)

## v0.1.8 (2026-03-07) — Phases 1-5 Completion: Tests, Disk Guard, Dashboard, Refactor

### Phase 3: Agent YAML Validation (NEW)
- `tests/test_agent_yaml_validation.py` — 380 tests: core.yaml exists, parseable, header.agent_id matches dir, valid profiles, non-empty IDs, unique IDs, UTF-8, no case-insensitive duplicates
- Registered as suite #44 in `waggle_backup.py`

### Phase 4: Disk Space Monitoring (NEW)
- `core/disk_guard.py` — `check_disk_space()` (warn <500MB, refuse <100MB), `get_disk_status()` for API
- Guards added to: `memory_engine.py` (store), `audit_log.py` (record), `replay_store.py` (store), `cognitive_graph.py` (save), `waggle_backup.py` (before zip creation)
- `hivemind.py`: `disk_space` field added to `get_status()` response
- `dashboard/src/App.jsx`: Disk space indicator in status bar (free GB, color-coded)

### Phase 2 Gaps: Dashboard Improvements
- Nomic-embed-text alert banner: red fixed banner when nomic unavailable (bilingual FI/EN)
- Last-updated timestamp in bottom bar (bilingual)
- Test suite count updated: 36/36 → 44/44

### Phase 1 Gap: Backup Restore Test
- `waggle_restore.py`: `--test-restore ZIPFILE` flag — extracts to temp dir, validates key files/dirs/agents/settings, cleans up (non-destructive)

### Phase 5: hivemind.py Refactoring
- `core/hive_routing.py` (407 lines) — extracted WEIGHTED_ROUTING (72 agents), PRIMARY_WEIGHT, SECONDARY_WEIGHT, MASTER_NEGATIVE_KEYWORDS, DATE_HALLUCINATION_RULE, AGENT_EN_PROMPTS
- `core/hive_support.py` (107 lines) — extracted PriorityLock, StructuredLogger
- hivemind.py: 3,728 → 3,321 lines (-407 lines, -11%)

---

## v0.1.7 (2026-03-07) — Phase 9: Documentation Overhaul

### New documentation (6 guides)
- `docs/ARCHITECTURE.md` — system overview, ASCII diagrams, component descriptions, data flow, storage layout
- `docs/API.md` — complete endpoint reference (~45 endpoints), request/response examples, WebSocket protocol
- `docs/DEPLOYMENT.md` — Docker Compose, native install (Win/Linux/Mac), 4 profiles, hardware tiers, env vars
- `docs/SECURITY.md` — threat model (localhost-only), CORS, rate limiting, input validation, secrets management
- `docs/MIGRATIONS.md` — SQLite schemas, ChromaDB collections, backup/restore, data directory layout
- `docs/SENSORS.md` — MQTT, Frigate NVR, Home Assistant, bee audio, BirdNET, voice interface, alerts

### README.md rewrite
- Version bump to v0.1.7
- Added Security section with threat model summary
- Added Contributing section with dev setup
- Added Documentation table linking all new guides
- Complete API reference (health/ready, voice POST, audio/bee, WebSocket)
- Updated Limitations (added: no auth, no TLS, single-node)
- Updated Project Structure (core/ and docs/ details)
- Updated Current Status (Phase 9 added)

### Docker improvements
- `Dockerfile`: added `HEALTHCHECK` (curl /health every 30s)
- `docker-compose.yml`: added `healthcheck` + `env_file: .env` to waggledance service

---

## v0.1.6 (2026-03-06) — Remaining Hardening (M-SQL, M-Path, M-Atomic)

### SQL LIKE wildcard escaping
- `core/audit_log.py`: `query_spawn_tree()` now escapes `%` and `_` in agent IDs before LIKE pattern

### Path parameter length limits
- `backend/routes/graph.py`: node_id, source, target max 256 chars
- `backend/routes/trust.py`: agent_id, domain max 128 chars

### Settings atomic writes
- `backend/routes/settings.py`: Settings toggle now uses temp file + `os.replace()` to prevent YAML corruption

---

## v0.1.5 (2026-03-06) — Security Hardening (H1–H4)

### H1: Input size limits
- `backend/routes/chat.py`: `ChatRequest.message` max 10,000 chars (Pydantic `Field(max_length=)`)
- `backend/routes/voice.py`: `VoiceTextRequest.text` max 5,000, `VoiceAudioRequest.audio_base64` max 10MB

### H2: Rate limiting on chat endpoint
- `backend/routes/chat.py`: In-process per-IP token bucket rate limiter (20 req/min)
- Lightweight, no external dependencies

### H3: CORS tightening
- `backend/main.py`: `allow_methods` restricted to `["GET", "POST"]`, `allow_headers` to `["Content-Type"]`, `allow_credentials=False`

### H4: Generic error responses
- `web/dashboard.py`: All `return {"error": str(e)}` replaced with `log.error()` + generic `{"error": "Internal error"}`
- Stack traces no longer leaked to clients

### M3 fix: Embedding fallback dimension safety
- Disabled cross-model fallback (768d nomic → 384d all-minilm would corrupt ChromaDB collections)
- Fallback infrastructure retained for future same-dimension models

### Test summary
- 42/43 suites, 590 ok, 0 failures (1 timeout: phase4 Ollama cold start, passes standalone)

---

## v0.1.4 (2026-03-06) — Low Priority Hardening (L1–L4)

### L1: Health & readiness endpoints
- `backend/main.py`: `GET /health` (liveness) + `GET /ready` (readiness) probes
- `web/dashboard.py`: Same endpoints for production dashboard (includes HiveMind running state)

### L2: Settings toggle input validation
- `backend/routes/settings.py`: `SettingsToggleRequest(BaseModel)` Pydantic model replaces raw dict

### L3: urllib download timeout
- `core/auto_install.py`: Voikko dictionary download now uses `urlopen(timeout=60)` instead of `urlretrieve`

### L4: Shutdown logging cleanup
- `hivemind.py`: `stop()` method print() → log.info/log.warning (4 calls)

### Test summary
- 43/43 suites, 676 ok, 0 failures (1 timeout: seasonal_guard Ollama cold start, passes standalone 78/78)

---

## v0.1.3 (2026-03-06) — Medium Priority Hardening (M1–M7)

### M1: Atomic writes for cognitive_graph.json
- `core/cognitive_graph.py`: temp file + `os.replace()` + fsync prevents corruption on crash

### M2: ReplayStore corruption resilience
- `core/replay_store.py`: try/except per JSONL line in `_iter_all()`, skip and log bad records

### M3: Embedding fallback chain
- `core/memory_engine.py`: primary model (nomic-embed-text) → fallback (all-minilm) on failure or circuit open

### M4: Settings validation (Pydantic)
- NEW `core/settings_validator.py`: Pydantic models for settings.yaml, fail-fast on invalid config
- `hivemind.py`: `_load_config()` calls `validate_settings()` before returning

### M5: Graceful shutdown
- `hivemind.py`: `_track_task()` helper + `_background_tasks` set, all fire-and-forget tasks tracked and cancelled in `stop()`

### M6: Shared state locks
- `core/memory_engine.py`: `threading.Lock` for `_learn_queue` append/flush and counter mutations

### M7: Secrets out of config
- `integrations/alert_dispatcher.py`, `integrations/home_assistant.py`, `hivemind.py`: env var fallback for all secrets (`WAGGLEDANCE_TELEGRAM_BOT_TOKEN`, `WAGGLEDANCE_HA_TOKEN`, `WAGGLEDANCE_DISTILLATION_API_KEY`)
- NEW `.env.example` with all secret env var descriptions

### Test summary
- 43/43 suites GREEN, 700 tests, 0 failures, Health Score 100/100

---

## v0.1.2 (2026-03-05) — Cognitive Graph, Overlay Expansion, Causal Replay

### New: Cognitive Graph (NetworkX)
- `core/cognitive_graph.py` — NetworkX DiGraph with typed edges (causal, derived_from, input_to, semantic), JSON persistence, BFS traversal for dependents/ancestors, shortest path queries
- `backend/routes/graph.py` — 3 API endpoints: `/api/graph/node/{id}`, `/api/graph/path/{a}/{b}`, `/api/graph/stats`
- `core/memory_engine.py`: `wire_graph()` method + automatic node/edge creation in `_learn_single()` (derived_from edges for enrichment-sourced facts)
- `hivemind.py`: CognitiveGraph wired after Layer 5, stats in `get_status()`
- `tests/test_cognitive_graph.py` — 23 tests (suite #42)

### New: Overlay System Expansion (A/B testing, Mood Presets)
- `core/memory_overlay.py` expanded (+130 lines): `OverlayBranch` (named replacement sets, apply to search results), `BranchManager` (activate/deactivate, A/B compare, create_from_agent_data), `MoodPreset` (cautious, verified_only, concise transforms)
- `backend/routes/magma.py` — 3 new endpoints: `/api/magma/branches`, `/api/magma/branches/{name}/activate`, `/api/magma/branches/deactivate`
- `hivemind.py`: BranchManager instantiated in Layer 4 wiring
- `tests/test_overlay_system.py` — 25 tests (suite #43)
- Existing MemoryOverlay + OverlayRegistry unchanged — expansion is fully additive

### Enhanced: Causal Replay (CognitiveGraph-powered)
- `core/replay_engine.py` (+70 lines): `preview_causal(node_id)` shows downstream dependency chain, `replay_causal(node_id, proxy, dry_run)` re-evaluates all causally dependent nodes
- Uses CognitiveGraph `find_dependents()` to walk causal/derived_from edges
- Optional — works without graph (time/session replay unaffected), causal methods guarded by `self._graph`
- `tests/test_replay_engine.py` — +5 causal tests (27→32 total)

### Resolved blueprint gaps
- **Cognitive Graph (NetworkX)** — was listed as "Missing" in guide v1.1, now implemented
- **Full Overlay System** — was "Simplified" (agent-filtered views only), now includes branch/mood/A-B testing
- **Causal Selective Replay** — was "Different design" (time-based only), now supports graph-based causal traversal

### Test summary
- 43/43 suites GREEN, 700 tests, Health Score 100/100

---

## v0.1.1 (2026-03-05) — MAGMA Layers 4-5: Cross-Agent Memory + Trust Engine

### New: MAGMA Layer 4 — Cross-Agent Memory Sharing
- `core/agent_channels.py` — AgentChannel + ChannelRegistry (role/domain channels, in-memory history cap 200)
- `core/provenance.py` — ProvenanceTracker: validations + consensus tables in audit_log.db
- `core/cross_agent_search.py` — CrossAgentSearch: role/channel/provenance-filtered queries
- `backend/routes/cross_agent.py` — 5 API endpoints for channels, provenance, contributions, consensus
- `hivemind.py`: Layer 4 wired after MAGMA audit, auto_create_role_channels(), Round Table consensus feedback
- `core/night_enricher.py`: cross-source context hints before generate_candidates()
- `core/memory_engine.py`: `_provenance_id` added to meta dict in _learn_single()
- `tests/test_layer4_cross_agent.py` — 28 tests (suite #40)

### New: MAGMA Layer 5 — Trust & Reputation Engine
- `core/trust_engine.py` — TrustSignal, AgentReputation, TrustEngine: 6-dimensional trust scoring (hallucination_rate, validation_ratio, consensus_participation, correction_rate, fact_production, temporal_freshness)
- `core/agent_levels.py` — added `get_stats_for_trust()` public API for TrustEngine integration
- `backend/routes/trust.py` — 4 API endpoints: `/api/trust/ranking`, `/agent/{id}`, `/domain/{d}`, `/signals/{id}`
- `hivemind.py`: TrustEngine wired after Layer 4, consensus participation signals in Round Table, trust summary (top 5) in `get_status()`
- `core/night_enricher.py`: `fact_production` signal recorded on validated fact storage
- `web/dashboard.py`: trust routes mounted
- `tests/test_layer5_trust.py` — 27 tests (suite #41)

### Test summary
- 41/41 suites GREEN, 700 tests, Health Score 100/100

---

## v0.1.0 (2026-03-04) — Dashboard Analytics + Runtime API + Agent Visualization

### New: Backend Analytics API (4 endpoints)
- `backend/routes/analytics.py`: `/api/analytics/trends` (7-day halluc/cache/RT trend), `/api/analytics/routes` (route breakdown), `/api/analytics/models` (model usage %), `/api/analytics/facts` (fact growth timeline)
- Reads `data/learning_metrics.jsonl` and `data/morning_reports.jsonl` for real data

### New: Round Table Transcript API (2 endpoints)
- `backend/routes/round_table.py`: `/api/round-table/recent` (latest discussions with agent dialogue), `/api/round-table/stats` (aggregate stats, most active agents)
- Finnish beekeeping-themed demo discussions (varroa treatment, spring inspection, energy optimization)

### New: Agent Levels API (2 endpoints)
- `backend/routes/agents.py`: `/api/agents/levels` (all 75 agents with level/trust/halluc), `/api/agents/leaderboard` (top by trust, queries, reliability)
- Level distribution: NOVICE→MASTER with simulated trust scores from agents/ directory

### New: Runtime Settings API (2 endpoints)
- `backend/routes/settings.py`: `GET /api/settings` (feature toggles from settings.yaml), `POST /api/settings/toggle` (toggle feeds/sensors/voice/audio on/off)
- 13 toggleable features with safe key validation

### Dashboard: 3 new panels in right column
- **AnalyticsPanel**: 7-day trend bar chart (response time), halluc/cache/RT live stats
- **RoundTablePanel**: latest discussion transcript, agent dialogue with consensus highlight
- **AgentGridPanel**: 75-agent colored grid (M/E/J/A/N) with level distribution summary
- `dashboard/src/hooks/useApi.js`: polls 4 new endpoints (analytics, round-table, agents, settings)
- `dashboard/src/App.jsx`: 3 new components + Tests indicator 35→36

### Backend wiring
- `backend/main.py`: 4 new routers wired (analytics, round_table, agents, settings)

---

## v0.0.9 (2026-03-04) — Phase 8: Data Feeds + Knowledge Enrichment + Backend Improvements

### New: Phase 8 — External Data Feeds test suite
- `tests/test_phase8_feeds.py` — 26 tests (6 groups): syntax, WeatherFeed, ElectricityFeed, RSSFeedMonitor, DataFeedScheduler, settings.yaml integration
- Registered as suite #36 in `waggle_backup.py`

### Backend: status.py real data
- `backend/routes/status.py`: reads `data/learning_metrics.jsonl` → cache_hit_rate, avg_response_ms, total_queries, hallucination_rate
- Reads `data/weekly_report.json` if present
- Changed `"mode": "stub"` → `"mode": "production"`

### New: backend/routes/code_review.py
- `GET /api/code-review` — code self-review status + recent suggestions count
- `GET /api/code-review/suggestions` — full suggestions list from `data/code_suggestions.jsonl`
- Wired to `backend/main.py`

### Knowledge Enrichment: 5 thin agents expanded
- `knowledge/quality_inspector/core.yaml`: +honey water content (18%), HMF limit (40 mg/kg), diastase, pollen count, failure modes, process flow
- `knowledge/supply_chain/core.yaml`: +beekeeping supply chain (honey packaging, jar stock weeks, wax recovery), seasonal ordering rules
- `knowledge/firewood/core.yaml`: +drying times (birch 18mo, spruce 12mo), bee hive heating threshold, seasonal pest protection for hives
- `knowledge/energy_manager/core.yaml`: +spot-price thresholds (cheap <5 snt/kWh), linkouskone consumption (0.5 kWh/h), winter heating optimization
- `knowledge/maintenance_planner/core.yaml`: +hive frame replacement (3y), extractor service (5y/500kg), smoker cleaning (10 uses), seasonal maintenance calendar

### NightEnricher: March boost strengthened
- Month 3 SEASONAL_BOOSTS expanded: `["beekeeper", "disease_monitor", "swarm_watcher", "horticulturist", "equipment_tech", "maintenance_planner"]` (6 agents for critical spring inspection month)

---

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
