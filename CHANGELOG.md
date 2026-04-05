# WaggleDance Swarm AI — CHANGELOG

## [3.5.2] — 2026-04-05 — Bounded Parallel LLM Dispatch

### Added
- **ParallelLLMDispatcher**: Feature-flagged bounded concurrent LLM dispatch with global + per-model asyncio semaphores, SHA-256 prompt deduplication, timeout/cancellation, and runtime metrics
- **3 model lines**: fast (gemma4:e4b), heavy (gemma4:26b), default (phi4-mini) with independent inflight limits
- **Orchestrator parallel integration**: `run_round_table()` and `_execute_agents()` dispatch agent LLM calls concurrently when enabled
- **RoundTableEngine parallel first pass**: Optional parallel first-pass mode where all agents see initial responses instead of sequential sliding window
- **SolverCandidateLab batch dispatch**: Candidate enrichments dispatched in parallel when `candidate_lab_parallelism > 1`
- **Additive observability**: `/api/status` and `/api/ops` include `llm_parallel` metrics section (queue_depth, inflight counts, timeout/cancel/dedup counters)
- **28 new focused tests**: dispatcher unit tests, semaphore limits, timeout, dedup, fallback, settings, container wiring, orchestrator integration

### Changed
- `configs/settings.yaml`: additive `llm_parallel` section (default: disabled)
- `waggledance/bootstrap/container.py`: `parallel_dispatcher` cached property, wired to orchestrator/round_table/candidate_lab
- `waggledance/core/orchestration/orchestrator.py`: accepts `parallel_dispatcher` for batch dispatch
- `waggledance/core/orchestration/round_table.py`: accepts `parallel_dispatcher` for optional parallel first pass
- `waggledance/application/services/solver_candidate_lab.py`: accepts `parallel_dispatcher` for batch enrichment

### Security
- No new public endpoints — parallel metrics exposed through existing status/ops endpoints
- Feature-flagged OFF by default — no behavior change unless explicitly enabled
- Security audit: no secrets exposed, no auth bypass, bounded concurrency prevents resource exhaustion

### Verified
- Full pytest: 4996 passed, 3 skipped, 0 failures (28 new tests)
- Benchmark: 6 modes tested (A-F), all OK; +50-77% wall-time improvement with OLLAMA_NUM_PARALLEL=4
- Soak (dual_tier + parallel ON): 264 cycles, 1,320 queries, 0 fail, 0 5xx — PASS
- Default behavior unchanged when flag OFF: verified in unit tests

## [3.5.1] — 2026-04-05 — Gemma 4 Dual-Tier Fallback Evaluation

### Added
- **Gemma 4 dual-tier model profiles**: Optional fast (gemma4:e4b) and heavy (gemma4:26b) model support alongside existing phi4-mini default
- **GemmaProfileRouter**: Configurable dual-tier routing — fast model for general fallback, heavy model restricted to hard reasoning / candidate lab / verifier assist
- **Gemma-assisted candidate lab**: SolverCandidateLab enriches failure analysis via Gemma heavy model (proposal-only, never auto-modifies production routing)
- **GemmaVerifierAdvisor**: Advisory-only Gemma integration for deterministic verifier — provides opinions, never overrides
- **Benchmark harness**: 4-mode apples-to-apples comparison (baseline, gemma fast, gemma heavy, dual-tier)
- **Additive observability**: `/api/status` and `/api/ops` include `gemma_profiles` metrics section
- **70 new focused tests**: 34 router, 23 lab/verifier, 13 benchmark harness

### Changed
- `configs/settings.yaml`: additive `gemma_profiles` section (default: disabled)
- `/api/status`: additive `gemma_profiles` field (all original fields preserved)
- `/api/ops`: additive `gemma_profiles` field (all original fields preserved)

### Security
- No new public endpoints — Gemma metrics exposed through existing authenticated endpoints
- Feature-flagged OFF by default — no behavior change unless explicitly enabled

### Verified
- Full pytest: 4968 passed, 3 skipped, 0 failures
- Benchmark: Gemma fast -32% p50 latency, dual-tier -42% p50 vs baseline
- 2h soak (dual_tier): 120 cycles, 600/600 OK, 0 5xx, 0 restarts — PASS
- Model verification: gemma4:e4b (9.6GB, 20 tok/s) and gemma4:26b (17GB, 5-10 tok/s) both verified on RTX A2000 8GB

## [3.5.0] — 2026-04-02 — Hybrid Population, Solver Candidate Lab, Synthetic Accelerator

### Added
- **HybridBackfillService**: Idempotent cell-local FAISS population from trusted case trajectories. Manual/admin-triggered, NOT auto-run on boot. Supports dry-run mode with per-cell counts.
- **SolverCandidateLab**: Safe autonomy layer that analyzes failure patterns and generates structured solver candidate specs. Isolated from production routing — candidates are reviewable artifacts only. AST-validated TemplateCompiler with strict allowlist (no imports, no side effects).
- **SyntheticTrainingAccelerator**: Deterministic synthetic data augmentation for specialist training. Augments sparse classes with bounded perturbations. Logs provenance of synthetic vs real rows. Optional GPU (cuML/RAPIDS) with CPU fallback (default).
- **API endpoints**: `/api/hybrid/backfill/{status,run}`, `/api/candidate_lab/{status,recent}`, `/api/learning/accelerator` — all authenticated
- **Additive observability**: `/api/status` includes `backfill` and `candidate_lab` summaries; `/api/ops` includes `backfill` and `accelerator` metrics
- **DI container wiring**: `solver_candidate_lab` and `synthetic_accelerator` as cached properties
- **85 new focused tests**: 12 backfill, 42 candidate lab, 20 accelerator, 11 route/observability

### Changed
- `/api/status`: additive `backfill` and `candidate_lab` fields (all original fields preserved)
- `/api/ops`: additive `backfill` and `accelerator` fields (all original fields preserved)

### Security
- All new endpoints use `require_auth` consistently
- Candidate lab output is isolated — never auto-loads into production routing

### Verified
- Full pytest: 4898 passed, 3 skipped, 0 failures
- All new tests pass independently (85/85)
- 4h soak (hybrid ON, cells populated): 100 cycles, 400/400 OK, 0 5xx, 0 restarts — PASS
- Benchmark: p50 latency 9055ms → 4231ms (−53%), LLM fallback 75% → 0% after backfill

## [3.4.1] — 2026-04-02 — Hybrid Auth Hardening + Doc Corrections

### Security
- **Hybrid endpoint authentication**: Added `require_auth` to all 4 `/api/hybrid/*` routes (status, topology, cells, test-assign) — previously accessible without authentication

### Fixed
- `docs/API.md`: Chat request body field corrected from `message` to `query`
- `docs/API.md`: Added auth requirement note to hybrid retrieval section
- `CURRENT_STATUS.md`: Test count updated 4772→4813, legacy stack marked as archived
- `README.md`: Test badge count corrected to 4813 (v3.4.0 post-release fix)

### Verified
- 4h hybrid soak: 100 cycles, 400/400 OK, 0 5xx, 0 restarts — PASS
- Baseline vs hybrid benchmark: 0 errors, no regression, route distribution identical
- Full pytest: 4813 passed, 3 skipped, 0 failures

## [3.4.0] — 2026-04-01 — Hybrid FAISS + Hex-Cell Retrieval

### Added
- **Hybrid retrieval architecture**: cell-local FAISS indices + logical hex-cell overlay before global ChromaDB
- **HexCellTopology**: 8 logical cells (general, thermal, energy, safety, seasonal, math, system, learning) with deterministic intent/keyword-based assignment and bounded ring-1/ring-2 neighbor adjacency
- **HybridRetrievalService**: orchestrates local → neighbor → global retrieval with full telemetry trace
- **Feature flag**: `hybrid_retrieval.enabled` in `configs/settings.yaml` (default OFF)
- **API endpoints**: `/api/hybrid/status`, `/api/hybrid/topology`, `/api/hybrid/cells`, `/api/hybrid/test-assign`
- **Hologram overlay**: additive `hybrid` section in `/api/hologram/state`
- **41 focused regression tests** for cell assignment, neighbor correctness, feature flags, graceful fallback, telemetry
- **Benchmark harness**: `tools/hybrid_benchmark.py` for baseline vs hybrid comparison
- **Documentation**: `docs/HYBRID_RETRIEVAL.md` with full architecture reference

### Changed
- `ChatService.handle()`: hybrid retrieval step between solver and LLM when enabled
- `ChatRoutingEngine.try_retrieval()`: cell-local FAISS first when hybrid enabled
- `ChatRoutingEngine.enrich_with_faiss()`: cell-local enrichment before global
- `MemoryService.ingest()`: mirrors to cell-local FAISS when hybrid enabled
- `/api/status` and `/api/ops`: include hybrid_retrieval section

## [3.3.9] — 2026-04-01

### Storage Health Introspection (fix)
- New `StorageHealthService` reports per-database sizes, WAL sizes, row counts, and growth warnings
- New `/api/storage/health` endpoint returns full storage health snapshot (authenticated)
- New `/api/storage/wal-checkpoint` POST triggers WAL checkpoint on all databases
- Configurable size thresholds per database with WARNING-level logging when exceeded
- Discovers both `.db` and nested `.sqlite3` (Chroma) databases under data directory

### Ollama Degraded Mode Hardening (fix)
- `/api/status` now includes `degraded` flag and `degraded_components` list derived from circuit breaker state
- `/api/learning` includes `llm_degraded` flag when LLM circuit breaker is open
- OllamaAdapter.ConnectError downgraded from ERROR to WARNING (expected during cold start)
- New `is_degraded` property on OllamaAdapter for programmatic access to circuit state
- Solver/hotcache paths continue functioning when LLM is unavailable

### Windows Long-Run Reliability (fix)
- Soak harness defaults output to `C:\WaggleDance_Soak\<timestamp>` (not volatile U:)
- PID validation before kill prevents stale-PID accidents on recycled process IDs
- SIGTERM/SIGBREAK signal handlers for graceful shutdown on Windows
- Stderr categorization in final SOAK_REPORT.md (WinError 10054, sklearn, Ollama, etc.)
- Signal-based shutdown requested flag checked every loop iteration

### Soak Harness Lifecycle Hardening (fix)
- Orphan WD process detection at startup via `scan_wd_processes()` — reports but does not kill unowned processes
- Port 8000 closure verification at end of run
- Full lifecycle section in SOAK_REPORT.md: PIDs started/stopped, orphans, port status
- `stop_wd()` now logs outcome and verifies process is actually gone after kill
- Old WD PID explicitly stopped before restart to prevent zombie accumulation
- Restart success validation (health check after 30s) with logged warning on failure

### GitHub PR Utility (feat)
- New `tools/github_pr.py` for clean PR creation/update via Python — no bash quoting artifacts
- Uses git credential manager for PAT authentication
- Supports create, update, and status commands with file-based body input

### Verified
- Pre-merge soak: 209/209 OK, 0 restarts, 12 learning cycles, +167 cases
- 30h long soak: 3181/3181 OK, 0 restarts, 175 learning cycles, +2349 cases, 0 stderr
- Post-merge verify soak on merged main
- 61 targeted tests, 4772 full pytest (0 failures)

## [3.3.8] — 2026-03-29

### Windows Soak Hardening + Noise Reduction

#### Windows Runtime Hardening (fix)
- WinError 10054 suppressed via logging.Filter on asyncio logger (replaces broken
  event-loop policy that uvicorn bypasses)
- Only ConnectionResetError with "10054" is filtered; all other errors pass through
- All 7 cross_val_score callers guarded with `_safe_cv_splits()` — checks
  min(Counter(labels).values()) >= n_splits before using StratifiedKFold
- Specialist trainer guards R² scoring with `len(X_test) < 2` check — eliminates
  sklearn UndefinedMetricWarning on tiny sample sets
- Ollama embed timeout now logs at WARNING (not ERROR) for expected startup timeouts
- Rate limiter exempts localhost (127.0.0.1, ::1) — same-machine traffic is trusted

#### Soak Harness (feat)
- New `tools/soak_harness.py` with monotonic hard deadline enforcement
- Query cutoff 2 minutes before deadline — no more overshoot
- WD launched with CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS on Windows
- Always writes final report, even on crash/interrupt
- Configurable: --hours, --output, --api-key, --max-restarts

#### Verified
- Pre-merge soak: 206/206 OK, 0 stderr lines, 12 cycles, +180 cases
- Post-merge soak: 200/200 OK, 0 stderr lines, 12 cycles, +179 cases
- 25 targeted regression tests, 4717 full pytest (0 failures)

## [3.3.7] — 2026-03-28

### Night Pipeline Ingestion + Learning Scheduler

#### Night Pipeline Case Ingestion (fix)
- `run_learning_cycle()` now auto-loads pending cases from `SQLiteCaseStore` when
  `day_cases` is not explicitly provided
- Added `learning_watermark` table to track processed cases — prevents reprocessing
  on repeated triggers
- Added `CaseTrajectory.from_stored_dict()` for reconstructing domain objects from
  stored JSON
- Added `fetch_pending()`, `pending_count()`, `get/set_watermark()` to case store
- Root cause: trigger ran successfully but received `day_cases=None` because nobody
  read accumulated cases from the store

#### Learning Scheduler (feat)
- Background scheduler checks every 10 minutes for pending cases
- Triggers learning cycle when: pending >= 10, runtime idle/light, pipeline not running
- Auto-starts when WaggleDance boots
- `GET /api/autonomy/learning/status` now shows `pending_cases` and `scheduler` state
- Manual trigger via `POST /api/autonomy/learning/run` still works

#### Verified
- 2h bounded soak: 5000+ cases ingested, 10+ models trained per cycle
- Scheduler auto-fires after manual triggers, processing new cases continuously
- Watermark prevents case reprocessing across triggers

## [3.3.6] — 2026-03-27

### Chat-to-Case Funnel + Baseline Store Hardening

#### Chat Learning Funnel (fix)
- ChatService now records CaseTrajectory rows via `build_from_legacy()` for every non-cached chat response (solver + LLM routes)
- DI container shares case_builder/case_store/verifier_store from AutonomyRuntime to ChatService
- Hot-cache hits excluded; case recording errors caught silently to avoid breaking chat flow
- Root cause: ChatService used Orchestrator (lightweight) and bypassed AutonomyRuntime, so 100% of chat traffic skipped the learning funnel

#### Baseline Store Crash (fix)
- Hardened `fetchone()[0]` patterns across all SQLite persistence stores to return 0 when fetchone() returns None
- Affected: baseline_store, sqlite_world_store, sqlite_case_store, sqlite_verifier_store, sqlite_procedural_store, sqlite_working_memory
- Eliminates NoneType crash in `/api/status`, `/api/ops`, `/api/profile_impact`

## [3.3.5] — 2026-03-25

### FlexHW Runtime + Hologram UI Stabilization + Legacy Consolidation

#### FlexHW / Autoscaler / Throttle — Hexagonal Runtime
- Wired ElasticScaler, AdaptiveThrottle, and ResourceGuard into hexagonal runtime via DI container
- `/api/ops` returns truthful `flexhw` + `throttle` telemetry from live runtime state
- Hologram Ops tab shows FlexHW tier and AutoThrottle sections (Phase 2)

#### Introspection APIs — Hexagonal Port
- Ported MAGMA, graph, trust, cross-agent, and analytics APIs to hexagonal routes (`waggledance/adapters/http/routes/`)

#### Hologram UI Stabilization
- Chat input no longer resets while typing — focus guard skips innerHTML rebuild during active input
- Profile selector is truthful: shows `configured` profile from settings.yaml, not volatile runtime state; restart-only behavior with persistent hint when runtime ≠ configured
- `GET /api/profiles` returns `{active, configured, restart_required}` — server-derived, no frontend guessing
- Feeds tab shows truthful source visibility with separate stale thresholds (30s telemetry, 1800s feeds)
- 56 regression tests added (`test_hologram_ui_stabilization.py`)

#### Security Posture Preserved
- No browser-visible master key paths
- No localStorage token handling in hologram frontend
- No frontend Bearer header construction
- No `?token=` in frontend WebSocket connection

#### Legacy Archival
- Archived React dashboard to `_archive/dashboard-react-v0/`; removed `/api/auth/token` endpoints
- Archived legacy backend product line to `_archive/backend-legacy/` after hexagonal migration
- Removed residual live backend import paths and legacy auth leftovers

#### Metadata
- Bumped `pyproject.toml` and `waggledance/__init__.py` to 3.3.5
- **4649 pytest tests, 0 failures**

---

## [3.3.4] — 2026-03-23

### Secure Session Auth + Real Feeds for Unified Hologram View

#### Security — HttpOnly Session Cookie Auth
- API key (WAGGLE_API_KEY) never reaches the browser — no HTML injection, no localStorage, no Bearer in frontend JS
- New `auth_session.py`: in-memory session store with `create_session()`, `validate_session()`, `destroy_session()`
- Session cookie: HttpOnly, SameSite=Strict, 1h TTL, `secrets.token_urlsafe(32)`
- Dashboard sets cookie server-side; hologram uses it transparently
- Removed: `localStorage.setItem("WAGGLE_API_KEY")` injection in dashboard
- Removed: `localStorage.getItem('WAGGLE_API_KEY')` in hologram HTML (all 3 occurrences)
- Removed: Bearer header construction in frontend JS
- Removed: `?token=<master_key>` in frontend WS connection
- Server-side Bearer + WS `?token=` support retained for cURL/scripts/CI

#### Feeds — Config-Based Sources with State Derivation
- `/api/feeds` rewritten: returns 5 configured sources (FMI weather, Porssisahko electricity, 3 RSS) from `settings.yaml`
- State derivation per source: `idle`, `unwired`, `framework`, `failed`, `active`, `unavailable`
- Two-level model: public = source status, authenticated = ChromaDB-enriched (`latest_items`, `latest_value`, `freshness_s`)
- Per-source isolation: RSS sources get only their own ChromaDB entries (no cross-contamination)

#### Frontend
- `checkAuth()` on page load → `GET /api/auth/check` (public)
- `sendChat()` uses `credentials: 'same-origin'` (cookie auth)
- WebSocket connects without `?token=` (cookie sent on upgrade)
- `buildFeedsPanel()` renders source list with state dots, protocol, interval, enrichment
- Feeds i18n: EN + FI labels

#### Legacy Cleanup
- Deprecated `dashboard/src/` React dashboard (superseded by `/hologram`)
- Updated `docs/SECURITY.md` and `docs/API.md` with new auth model

#### Tests
- 31 new tests in `test_hologram_v6.py` (97 total in file)
- **4543 pytest tests total, 0 failures**

---

## [3.3.3] — 2026-03-22

### Hologram UI v2 — 32 Nodes, 4 Rings, Docked Panels, FI/EN i18n

Full hologram overhaul: concentric ring layout, node metadata contract, bilingual interface.

#### Backend (hologram.py + _capability_state.py)
- Removed 13 fake activation floors (0.1/0.5 fallbacks → 0.0)
- Rewrote 8 node formulas: activation = current activity (load/lifecycle), historical quality → `node_meta.quality`
- Added 8 system ring nodes: sys_auth, sys_policy, sys_api, sys_websocket, sys_feeds, sys_queues, sys_storage, sys_compute
- Added 4 learning ring nodes: learn_night, learn_dream, learn_training, learn_canary
- 5 existing micro nodes repositioned into learning ring (same IDs)
- Added `node_meta` dict with 32 entries: state (7-value enum), device, freshness_s, source_class (4-value enum), quality
- Created `_capability_state.py`: shared `derive_capability_state()` — single source of truth for hologram + capabilities endpoint
- Updated zero-state to 32 nodes (all 0.0, all state="unavailable")
- `_recent_activity()` helper for per-capability activity counters
- `_gpu_info_safe()` for nvidia-smi telemetry (never crashes)

#### New Endpoints
- `GET /api/profile/impact` — profile impact summary (target environment, enabled capabilities, risk mode)
- `GET /api/capabilities/state` — per-family capability state/device/quality/source_class (uses shared derivation)
- `GET /api/learning/state-machine` — current learning lifecycle state (awake/replay/dream/training/canary/morning_report)
- 3 new public paths in auth middleware

#### Frontend (hologram-brain-v6.html)
- CSS grid docked-panel layout (canvas left, panel right, responsive)
- 32 node definitions across 4 rings (core, MAGMA, system, learning)
- 8 tabs + Chat (Overview, Memory, Reasoning, Micromodels, Learning, Feeds, Ops, Chat)
- FI/EN i18n with `localizeProfile()` sanitization (no raw profile in DOM)
- Node state badges (active/idle/training/framework/unwired/unavailable/failed)
- Source class indicators (simulated = orange halo, stale = yellow dashed)
- Quality progress bars in inspector panel
- Chat tab: localStorage-based auth (no API key in HTML source), Bearer token from prior dashboard session
- 7-phase demo sequence for all 4 rings
- Ring orbital arcs (dashed, color-coded per ring)
- chat_route WS broadcast from chat.py for live route highlighting

#### Tests
- 66 new tests in `test_hologram_v6.py`
- **4512 pytest tests total, 0 failures**

#### Security
- Zero literal "APIARY" strings in frontend HTML/JS
- No `__WAGGLE_API_KEY__` server-side injection placeholder
- v5 HTML preserved as fallback
- *(Note: localStorage auth replaced by HttpOnly session cookie in v3.3.4)*

---

## [3.2.1] — 2026-03-19

### Real sklearn Training for All 8 Specialist Models

- All 8 specialist models now use real sklearn algorithms instead of simulated training
- `capability_selector`: LogisticRegression on encoded goal_type → primary capability
- `anomaly_detector`: IsolationForest on residual vectors (unsupervised)
- `baseline_scorer`: DecisionTreeClassifier on goal_type + profile → grade
- `approval_predictor`: LogisticRegression on goal_type + profile → approved (bool)
- `missing_var_predictor`: DecisionTreeClassifier on goal_type + profile → grade
- `verifier_prior`: LogisticRegression on capabilities → verifier_passed (bool)
- `domain_language_adapter`: LogisticRegression on profile → goal_type
- Added `_encode_categorical()` shared helper for string → integer encoding
- Dispatch dict replaces single `if model_id == "route_classifier"` branch
- Enriched feature extraction: capabilities, n_capabilities, has_world_snapshot
- `_simulate_training()` retained as fallback (sklearn unavailable / single class)
- 11 new tests in `test_real_sklearn_training.py`

---

## [3.2.0] — 2026-03-19

### v3.2 — Self-Entity, Dream Mode, MAGMA Expansion

Major extension adding self-entity capabilities, counterfactual dream mode, memory consolidation, and confidence decay.

#### Self-Entity & World Model
- Epistemic uncertainty tracking with observability gap detection
- Persistent motives with valence scoring and conflict resolution
- Curiosity-driven goal generation from uncertainty reports
- WorldSnapshot source_type extension (SELF_REFLECTION, SIMULATED)

#### Night Learning Enhancements
- Dream Mode: counterfactual simulation of failed/low-quality missions (nightly, max 20 sims)
- Memory Consolidator: Ebbinghaus retention curve, significance protection (>0.7 never consolidated)
- Meta-optimizer: hyperparameter learning from canary results (3-cycle activation gate)

#### Attention & Resource Management
- 4-bucket attention budget (critical/normal/background/reflection) with load-aware reallocation
- Continuity floor: hard minimums prevent starvation of any bucket

#### Projections (Read-Only Views)
- Narrative projector: bilingual en/fi self-narrative with 60s cache (no LLM in fast path)
- Introspection view: profile-gated (FACTORY=full, HOME=filtered, GADGET=counts)
- Autobiographical index: episodic memory query with significance ranking
- Projection validator: no-hidden-deps validation for all projection modules

#### MAGMA Expansion (Section 10)
- L1 AuditLog: `self_reflection` and `simulated` fields, 9 new event types
- L2 Replay: `is_simulation` flag, `record_dream_replay()`, `list_dream_replays()`
- L4 Provenance: `self_reflection` (weight 0.60) and `simulated` (weight 0.30) source types, 9-tier hierarchy
- L5 Trust: `context` field ("actual"|"simulated"), simulated observations at 50% weight
- `decayed_confidence()`: query-time exponential decay, verified facts decay slower (168h vs 84h half-life)

#### Rollback & Safety
- Rollback matrix: every v3.2 feature individually disableable via config
- Simulated trajectories never contaminate production (trajectory_origin="simulated", synthetic=True)
- Original confidence values never destructively modified

#### API Endpoints (6 new)
- GET /api/autonomy/epistemic-uncertainty
- GET /api/autonomy/attention-budget
- GET /api/autonomy/dream-mode/latest
- GET /api/autonomy/memory/consolidation-stats
- GET /api/autonomy/introspection
- GET /api/autonomy/narrative

#### Tests
- 55 new MAGMA expansion tests
- 171 total continuity tests (self-entity, uncertainty, attention, projections, MAGMA)
- **4129 pytest tests total, 0 failures**
- validate_cutover: 42/42 modules pass

---

## [2.0.0] — 2026-03-18

### Full Autonomy v3 — Solver-First Runtime

Major architectural release introducing a solver-first autonomy runtime with capability contracts, quality-gated learning, and full audit trail.

#### Autonomy Runtime
- Solver-first 3-layer architecture: solvers (authoritative) → specialist models (learned) → LLM (fallback)
- CapabilityRegistry with 29 builtin capabilities across 8 categories (solve, retrieve, sense, detect, verify, normalize, explain, optimize)
- 23 capability adapters with 20 executors bound at startup
- SolverRouter with intent classification and capability selection
- PolicyEngine with deny-by-default evaluation and safety cases
- SafeActionBus for policy-checked execution
- Verifier for outcome validation
- GoalEngine with full lifecycle: propose → accept → plan → execute → verify → archive
- WorldModel with baselines, residuals, entity registry, and CognitiveGraph integration
- ResourceKernel with AdmissionControl (accept/defer/reject based on system load)
- AutonomyLifecycle state machine with cutover validation
- CompatibilityLayer routing between autonomy and legacy runtimes

#### Night Learning v2
- NightLearningPipeline: grade → train → canary → procedural → report
- QualityGate: gold/silver/bronze/quarantine grading for case trajectories
- SpecialistTrainer: 8 specialist models with train → canary (48h) → promote/rollback lifecycle
- ProceduralMemory: gold cases → proven chains, quarantine → anti-patterns
- MorningReportBuilder: overnight learning summary
- LegacyConverter: converts legacy Q&A data to case trajectories
- Pipeline wired through hexagonal path: Container → AutonomyService.run_learning_cycle()

#### MAGMA Integration
- AuditProjector: structured autonomy audit events (goals, plans, actions, capabilities, verification)
- EventLogAdapter: policy decisions, case trajectories
- TrustAdapter: per-capability trust from execution outcomes
- ReplayAdapter: mission replay with world snapshots
- ProvenanceAdapter: fact provenance with source type and confidence
- All adapters optional and fail-safe (try/except, never break hot path)

#### Persistence
- SQLiteWorldStore: world model snapshots
- SQLiteProceduralStore: proven chains and anti-patterns
- SQLiteCaseStore: case trajectories with quality grades
- SQLiteVerifierStore: verification results per action

#### Capability Adapters (23 total)
- 5 legacy wrappers: math solver, symbolic solver, constraint engine, LLM explainer, micromodel (V1+V2)
- 6 reasoning engines: thermal, anomaly, stats, optimization, causal, route analysis
- 2 domain engines: domain-specific monitoring (health, anomaly, diagnostics), seasonal calendar
- 5 legacy retrieval/verification: hot cache, semantic search, vector search, hallucination checker, English validator
- 3 sensor adapters: MQTT, Frigate, Home Assistant + audio, fusion
- 2 normalization: Finnish, translation

#### Metrics & KPIs
- 13 autonomy KPIs with targets (route accuracy >90%, LLM fallback <30%, specialist accuracy >85%, etc.)
- Per-specialist-model accuracy tracking
- Proactive goal rate counter
- Night learning gold rate
- Daily counter reset

#### Proactive Goals
- AutonomyRuntime.check_proactive_goals() generates diagnostic goals from world model residual deviations
- Emits proactive_goal metric per proposed goal

#### Safety Cases
- SafetyCaseBuilder: evidence-based safety arguments with verdict (safe/needs_review/unsafe)
- Exposed via AutonomyService.get_safety_cases() and API endpoint
- Evidence from historical success rate, verifier pass rate, procedural memory, risk scores

#### API Endpoints (7 new)
- GET /api/autonomy/status — full runtime status
- GET /api/autonomy/kpis — 13 KPIs with targets
- POST /api/autonomy/learning/run — trigger learning cycle
- GET /api/autonomy/learning/status — pipeline status
- POST /api/autonomy/goals/check-proactive — proactive goal check
- GET /api/autonomy/safety-cases — recent safety cases
- GET /api/autonomy/safety-cases/stats — verdict distribution

#### Domain Configuration
- Profile-gated capability adapters (domain engines and seasonal active only with relevant profiles)
- Domain capsules: profile-specific reasoning config loaded at runtime
- Profile policies: per-profile policy rules in configs/policy/profile_policies/

#### Bug Fixes
- MicroModelAdapter: missing CAPABILITY_ID and execute() caused silent binding failure
- Container: hardware tier resolution used dot-notation key that never resolved
- AuditProjector: kwarg mismatch (detail vs details) caused every legacy write to silently fail
- 4 unused imports cleaned across 3 files

#### Testing
- 3836 pytest tests, 0 failures
- 79 legacy suites (1468 tests), 0 failures
- ~5300 total tests
- 40 new priority wiring tests covering all 5 cutover integrations
- Runtime start/stop verified clean in primary mode

#### Infrastructure
- FastAPI version string updated to 2.0.0
- Docker multi-stage build on master
- Capability YAML configs: reasoning_engines, sensors, solvers, retrievers, verifiers

---

## [1.18.0] — 2026-03-15

### Runtime Convergence & Wiring Sprint

#### Runtime Wiring
- Request loop: RouteTelemetry + LearningLedger + RouteExplainability wired into `chat_handler.py` and `chat_service.py`
- Night loop: ActiveLearningScorer + LearningLedger wired into `night_mode_controller.py` and `night_enricher.py`
- `core/shared_routing_helpers.py` — convergence layer (cached singletons for micromodel probe, telemetry, ledger)
- Runtime convergence decision: legacy = primary, hex = forward path, shared helpers avoid duplication

#### MQTT Bridge
- `MQTTSensorIngest` wired into `SensorHub.start()` step 6
- Writes validated readings to SharedMemory, dispatches anomaly alerts

#### Dashboard APIs (6 new endpoints)
- `GET /api/route/explain` — route explainability breakdown
- `GET /api/route/telemetry` — per-route telemetry stats
- `GET /api/experiments` — prompt experiment status
- `GET /api/graph/replay/{node_id}` — causal replay via CognitiveGraph
- `GET /api/graph/stats` — cognitive graph statistics
- `GET /api/learning/ledger` — recent learning ledger entries
- `GET /api/micromodel/status` — honest V1/V2/V3 micromodel status

#### memory_engine.py Split (continued)
- Extracted `core/memory_eviction.py` (MemoryEviction, ~170 lines)
- Extracted `core/opus_mt_adapter.py` (OpusMTAdapter, ~80 lines)
- Extracted `core/learning_task_queue.py` (LearningTaskQueue + constants, ~180 lines)
- memory_engine.py: 1928 → 1292 lines (below 1500 target)

#### Operational Hardening
- SQLiteTrustStore graceful degradation (falls back to InMemory on failure)
- trust_store.db already in backup via `data/*.db` glob
- Benchmark corpus expanded: 12 → 30 queries across all route types
- Shadow validation: 75-query corpus comparing legacy vs hex routing
- `tools/run_benchmark.py` — benchmark runner with JSON + markdown artifacts
- `tools/validate_all.py` — one-command full validation script
- CI updated with benchmark step + artifact upload

#### Documentation
- CURRENT_STATUS.md synced with v1.18.0 changes
- MIGRATION_STATUS.md updated with runtime convergence decision
- CONTRIBUTING.md expanded (benchmarks, shadow validation, stub mode)
- Honest micromodel V1/V2/V3 status in CURRENT_STATUS.md

## [1.17.0] — 2026-03-15

### Big Sprint — Deeper, Safer, More Testable

#### Workstream A — memory_engine.py Split
- Extracted `core/circuit_breaker.py` (CircuitBreaker)
- Extracted `core/embedding_cache.py` (EmbeddingEngine, EvalEmbeddingEngine)
- Extracted `core/hallucination_checker.py` (HallucinationChecker, HallucinationResult)
- Extracted `core/math_solver.py` (MathSolver)
- memory_engine.py: 2494 → ~1930 lines, backward-compatible re-exports

#### Workstream E — Persistent TrustStore
- `waggledance/adapters/trust/sqlite_trust_store.py` — SQLite WAL-mode trust store
- Same 6-signal composite weights as InMemoryTrustStore
- Contract tests verify both implementations

#### Workstream D — Micromodel Route Type Restored
- `"micromodel"` added to ALLOWED_ROUTE_TYPES (5 total)
- RoutingFeatures: has_micromodel_hit, micromodel_confidence, micromodel_enabled
- Routing: hotcache → micromodel (>0.85) → memory → llm → swarm
- FallbackChain default: memory → micromodel → llm → swarm

#### Workstream G — Learning Infrastructure
- `core/active_learning.py` — ActiveLearningScorer (4-signal priority scoring)
- `core/canary_promoter.py` — CanaryPromoter (gate promotions, auto-rollback)
- `core/learning_ledger.py` — LearningLedger (JSONL append-only audit log)
- `core/route_telemetry.py` — RouteTelemetry (per-route stats, bounded tuning ±0.05)
- `core/english_source_learner.py` — EnglishSourceLearner (night learning, allowlist, budget)
- `core/language_readiness.py` — LanguageReadiness (per-language capability report)
- `core/route_explainability.py` — explain_route() structured breakdown
- `core/prompt_experiment_status.py` — ExperimentStatusFormatter for API
- `core/causal_replay_api.py` — CausalReplayService for graph traversal
- `core/lora_readiness.py` — LoRAReadinessChecker (deps, GPU, data, disk)

#### Workstream C — MQTT End-to-End
- `core/mqtt_sensor_ingest.py` — SensorReading + MQTTSensorIngest
- Parse hive/+/temperature topics, validate -40..80°C, anomaly detection

#### Workstream B — Runtime Validation
- `tools/runtime_shadow_compare.py` — shadow/canary/validate modes
- `tools/benchmark_harness.py` — CLI benchmark with YAML queries
- `configs/benchmarks.yaml` — 30 predefined queries across 7 domains

#### Workstream F — Test Unification
- `tests/legacy_pytest_adapter/` — pytest plugin wrapping legacy tests
- `.github/workflows/tests.yml` — unified CI (legacy + pytest + integration)

#### Workstream H — Docs & Release
- `CONTRIBUTING.md` — setup, tests, runtime, PRs
- `examples/cottage_demo/` — demo queries
- `examples/custom_agent/` — YAML agent template
- Version bump to v1.17.0

#### New Tests
- ~25 new test files, ~200+ new tests
- Phase 1: test_circuit_breaker, test_embedding_cache, test_hallucination_checker, test_math_solver, test_memory_engine_orchestration
- Phase 2: test_sqlite_trust_store
- Phase 3: test_routing_policy_micromodel, test_fallback_policy_micromodel
- Phase 4: test_active_learning_priority, test_prompt_canary, test_micromodel_canary, test_learning_ledger
- Phase 5: test_benchmark_runner, test_route_telemetry, test_bounded_route_tuning
- Phase 6: test_mqtt_sensor_ingest, test_mqtt_to_shared_memory
- Phase 7: test_runtime_shadow_compare
- Phase 8: test_adapter_smoke
- Phase 9: test_web_learning_english_sources, test_language_capabilities
- Phase 10: test_route_explainability, test_prompt_experiment_status_payload, test_causal_replay_api, test_micromodel_v3_readiness

## [1.16.0] — 2026-03-15

### Safe Self-Improvement Sprint

#### Added
- **Prompt Evolution Loop** (`core/learning_engine.py`)
  - `PromptWin` dataclass: tracks experiment outcomes with apply/rollback status
  - Prompt win persistence: atomic JSON save/load to `data/prompt_wins.json`
  - `get_prompt_override(agent_id)`: returns active evolved prompt if applied
  - `_check_prompt_rollbacks()`: auto-rollback if post-apply quality drops >0.3 below original
  - 5 new config flags in `configs/settings.yaml` under `learning:`
  - `get_status()` now includes `prompt_wins` key
- **Prompt Override Wiring** (`core/chat_handler.py`)
  - Applied prompt wins injected before EN-prompt fallback in chat delegation
- **Micro-Model Eval Gate** (`core/micro_model.py`)
  - Deterministic holdout split (seeded `torch.Generator`, reproducible)
  - `_evaluate_holdout()`: structured eval result with accuracy, holdout_size, per_class
  - Eval gate in `maybe_train()`: blocks V2 if accuracy < threshold on sufficient holdout
  - `_save_eval_report()`: writes JSON to `data/micro_model_reports/`
  - `_save_active_manifest()`: writes `data/micro_model_active.json` (v1/v2/v3 status)
  - V3 `LoRAModel.stats` now includes `implementation_status`
  - 5 new config flags under `advanced_learning:`
- **Night Learning Source Visibility** (`core/night_enricher.py`)
  - `EnrichmentSource.implementation_status` property (default: "stub")
  - Overrides: SelfGenerate="implemented", RssFeed="partial"/"disabled"/"unavailable"
  - `SourceManager.get_capability_map()`: returns status/available/paused for all sources
  - `get_all_stats()` now includes `implementation_status` key
  - All 5 sources always registered (ChatHistory/RssFeed visible even when disabled)
- **Dashboard** (`web/dashboard.py`)
  - `/api/learning` response includes `source_capabilities` from night enricher
- **Tests**: 3 new test suites (75 total)
  - `test_learning_prompt_apply.py`: 10 tests (PromptWin lifecycle)
  - `test_micro_model_eval_gate.py`: 10 tests (deterministic holdout, eval gate)
  - `test_night_enricher_capabilities.py`: 8 tests (implementation_status, capability map)

#### Changed
- `_decide_experiment()`: uses configurable `min_delta` instead of hardcoded 0.5
- `_check_experiments()`: uses configurable `min_samples` instead of hardcoded 3
- V2 `ClassifierModel.train()`: returns structured eval dict instead of `True`
- `tools/waggle_backup.py`: registered 3 new suites (72 -> 75)

#### Limitations
- V3/LoRA training remains optional stub (framework-level when peft installed)
- Live prompt apply behind `prompt_live_apply: false` flag (safe default)
- CI workflow pytest job included but needs PAT `workflow` scope to push

## [1.15.0] — 2026-03-13

### Routing Keyword Transparency — matched_keywords in RouteResult

#### Added
- **`core/smart_router_v2.py`** `RouteResult.matched_keywords: list[str]`
  - Populated by all routing steps: capsule match, keyword classifier, fallback (empty)
  - Included in `to_dict()` — visible in `/api/route` API response
  - `_classify_keywords()` now returns `(layer, reason, matched_keywords)` tuple
    Uses `re.Match.group(0)` to capture the exact matched text
- **`core/domain_capsule.py`** `DecisionMatch.matched_keywords: list[str]`
  - `match_decision()` now tracks which YAML keywords triggered each decision
  - Also captures keywords matched via ASCII normalization (returns YAML form)
- **`tests/test_matched_keywords.py`**: 7 tests
  - Capsule diacritic match: matched_keywords contains YAML keyword "lämmitys"
  - Capsule ASCII match: matched_keywords still contains YAML form "lämmitys"
  - Seasonal: "maaliskuu" in matched_keywords
  - Rule: "pitaako" in matched_keywords
  - Statistical: "trendi" in matched_keywords
  - Retrieval: "mita on" in matched_keywords
  - to_dict() includes matched_keywords list

#### Changed
- `tools/waggle_backup.py`: registered `test_matched_keywords` — now **72 test suites**

**`/api/route` now shows exactly which keywords triggered each routing decision**

## [1.14.0] — 2026-03-13

### Router Performance + Reason Code Improvements

#### Fixed
- **`core/chat_handler.py`**: `_do_chat()` now calls `smart_router_v2.route()` **once**
  instead of 3–4 times per query (was called separately for each layer block).
  - Inserted a single routing block before all layer checks: `_route = smart_router_v2.route(message)`
  - Removed the incomplete `_last_route` hack in rule_constraints block (line 313)
  - All 4 layer blocks (model_based, rule_constraints, retrieval, statistical) now share `_route`
  - Saves ~1–3ms per query; eliminates redundant HotCache lookups + keyword scanning

#### Changed
- **`core/smart_router_v2.py`**: `_classify_keywords()` now returns `(layer, reason)` tuple
  - Reason codes are now category-specific instead of generic "keyword_classifier":
    - `"keyword_classifier:math"` — math/formula keywords
    - `"keyword_classifier:seasonal"` — Finnish/English month names
    - `"keyword_classifier:rule"` — rule/constraint keywords
    - `"keyword_classifier:stat"` — statistical keywords
    - `"keyword_classifier:retrieval"` — question/explain keywords
  - Improves debugging and API transparency
- `tools/waggle_backup.py`: registered `test_router_stats` — now **71 test suites**

#### Added
- **`tests/test_router_stats.py`**: 7 tests
  - stats() structure validation
  - total_routes increments by exactly 1 per route() call
  - Seasonal/rule/capsule reason codes verified
  - Routing determinism check
  - layer_distribution multi-layer tracking

**Each query now triggers exactly one routing decision instead of three or four**

## [1.13.0] — 2026-03-13

### Capsule Default Fallback — Unknown Queries Route to llm_reasoning

#### Problem
SmartRouterV2 Step 4 (priority fallback) was routing unknown queries to
`rule_constraints` (priority=1 in cottage.yaml). This is semantically wrong:
rule_constraints should be for safety checks, not general catch-all responses.
A query like "viita räme sammalpinta" had no keywords matching any layer but
still landed in rule_constraints.

#### Fixed
- **`core/smart_router_v2.py`** Step 4: uses `capsule.default_fallback` instead
  of `capsule.get_layers_by_priority()[0]`
  - If default_fallback layer is disabled, falls back to priority list as before
- **`core/domain_capsule.py`**: `DomainCapsule.default_fallback` attribute
  - Parsed from `default_fallback` field in capsule YAML
  - Defaults to `"llm_reasoning"` if field absent

#### Changed
- **`configs/capsules/cottage.yaml`**: added `default_fallback: llm_reasoning`
- `tools/waggle_backup.py`: registered `test_capsule_default_fallback` — now **70 test suites**

#### Added
- **`tests/test_capsule_default_fallback.py`**: 5 tests
  - capsule.default_fallback == "llm_reasoning" from YAML
  - Unknown query routes to llm_reasoning (not rule_constraints)
  - Known query ("pitaako") still routes to rule_constraints
  - Missing default_fallback field defaults to "llm_reasoning"
  - Disabled default_fallback layer falls through to priority list

**Unknown/off-topic queries now get a conversational LLM answer, not a rule check**

## [1.12.0] — 2026-03-13

### Capsule-Level Finnish Normalization

#### Fixed
- **`core/domain_capsule.py`**: `match_decision()` now matches ASCII queries against diacritic keywords
  - Pre-compiles normalized keyword patterns (`_normalize_fi()`) alongside raw patterns
  - Each keyword gets two patterns: raw (for diacritic queries) and normalized (for ASCII queries)
  - Only compiles normalized pattern when keyword actually contains diacritics (no-op otherwise)
  - Defined `_normalize_fi()` locally to avoid circular import with `smart_router_v2`
- **`configs/capsules/cottage.yaml`**: removed "mehiläi" from `honey_yield` keywords
  - Too generic: inflected form "mehilaisille" ("for bees") caused seasonal April queries to
    false-route to model_based (honey yield) instead of retrieval (seasonal calendar)
  - Remaining keywords (hunaja, honey, hunajasato, sato, harvest, yield, tuotos) are sufficient

#### Added
- **`tests/test_capsule_fi_normalization.py`**: 7 tests
  - ASCII "lammitys" matches diacritic keyword "lämmitys" -> heating_cost
  - ASCII "jaatya" matches "jäätyä" -> frost_protection
  - ASCII "laakitys" matches "lääkitys" -> varroa_treatment
  - ASCII "tehtava" matches "tehtävä" -> seasonal_task
  - ASCII "selviaa" matches "selviää" -> hive_survival
  - Regression: original diacritic queries unchanged
  - Full router: "paljonko lammitys maksaa" -> model_based/heating_cost

#### Changed
- `tools/waggle_backup.py`: registered `test_capsule_fi_normalization` — now **69 test suites**

**Finnish users typing ASCII queries now get correct capsule routing at Step 2, not just at Step 3**

## [1.11.0] — 2026-03-13

### Capsule Keyword Word-Boundary Fix

#### Fixed
- **`core/domain_capsule.py`**: keyword patterns now use `\b` word-start boundary
  - `re.compile(re.escape(kw))` -> `re.compile(r'\b' + re.escape(kw))`
  - Prevents "energi" keyword from matching inside "aurink**o**energi**a**" (mid-word)
  - Prevents any keyword from triggering on mid-word substring hits
- **`configs/capsules/cottage.yaml`**: removed "mite" from `varroa_treatment` keywords
  - "mite" was a false positive for the extremely common Finnish word "miten" (how/how much)
  - Remaining keywords (varroa, punkki, oksaalihappo, oxalic, lääkitys, treatment) are sufficient

#### Added
- **`tests/test_capsule_word_boundary.py`**: 6 tests
  - "energi" no longer matches mid-word "aurinkoenergia"
  - "mita on aurinkoenergia" routes to retrieval (not heating_cost)
  - "miten" no longer triggers varroa_treatment
  - "selita miten X" routes to retrieval
  - varroa/oxalic/treatment keywords still match correctly (no regression)
  - heating/cost/kwh keywords still match correctly (no regression)

#### Changed
- `tools/waggle_backup.py`: registered `test_capsule_word_boundary` — now **68 test suites**

**Capsule routing no longer produces false positives from mid-word substring matches**

## [1.10.0] — 2026-03-13

### Finnish ASCII Diacritics Normalization — SmartRouter v2

#### Added
- **`core/smart_router_v2.py`**: `_normalize_fi()` helper — maps ä→a, ö→o, å→a
  - Allows ASCII queries ("pitaako", "mita on", "selita") to match the same layer as diacritic queries
  - `_classify_keywords()` now matches both raw query and `_normalize_fi(query)`
  - `_RULE_KEYWORDS`: added "pitaako" ASCII variant
  - `_RETRIEVAL_KEYWORDS`: added "mita on", "mika on", "mita tarkoittaa", "selita" ASCII variants
- **`tests/test_fi_normalization.py`**: 8 tests
  - `_normalize_fi()` unit tests (ä->a, ö->o, å->a)
  - "pitaako" routes to rule_constraints like "pitääkö"
  - "mita on" routes to retrieval like "mitä on"
  - "selita" routes to retrieval like "selitä"
  - ASCII seasonal queries still route to retrieval
  - Statistical keywords route correctly
  - Regression check: original diacritics queries unchanged
  - Mixed ASCII+diacritics (varroa query) routes correctly via capsule Step 2

#### Changed
- `tools/waggle_backup.py`: registered `test_fi_normalization` — now **67 test suites**

**Finnish users can now type queries without diacritics and get correct routing**

## [1.9.0] — 2026-03-13

### Seasonal Routing Fix — SmartRouter v2 _SEASONAL_KEYWORDS

#### Fixed
- **`core/smart_router_v2.py`**: Added `_SEASONAL_KEYWORDS` pattern
  - Matches all 12 Finnish month names (tammikuu…joulukuu), kesäkuu/heinäkuu stems
  - Matches all 12 English month names
  - Matches "kauden"/"kuukau"/"seasonal"/"tehtav" stems
  - Checked **before** `_RULE_KEYWORDS` — "what should I do in august" no longer misroutes to rule_constraints
- **`configs/capsules/cottage.yaml`**:
  - `hive_survival` keywords: removed "winter" (too broad — "winter prep" hijacked seasonal queries)
  - `seasonal_task` keywords: reverted to 5 core keywords (month names handled by `_SEASONAL_KEYWORDS`)

#### Added
- **`tests/test_seasonal_routing.py`**: 10 tests — FI/EN month routing, "should" fix, varroa priority, survival/frost unchanged, bee_knowledge FAISS coverage

#### Changed
- `tools/waggle_backup.py`: registered `test_seasonal_routing` — now **66 test suites**

**Seasonal queries now reliably route to retrieval (bee_knowledge FAISS) for all month names in FI and EN**

## [1.8.0] — 2026-03-13

### Bee Seasonal Knowledge Base + Retrieval Wiring

#### Added
- **`configs/knowledge/cottage/seasonal_tasks.yaml`**: Finnish beekeeping calendar (12 months)
  - 129 FAISS chunks: month overviews, FI/EN task chunks, keyword chunks, general facts
  - Covers: winter rest, spring inspection, swarm prevention, honey harvest, varroa treatment, winterization
- **`tools/index_bee_knowledge.py`**: FAISS indexer for bee knowledge YAMLs
  - New `bee_knowledge` collection: 129 vectors
  - Incremental (skips existing doc_ids)
- **`tests/test_bee_knowledge_faiss.py`**: 8 tests — all 12 months, FI/EN tasks, facts, metadata, no duplicates

#### Changed
- **`core/chat_handler.py`**: retrieval layer now searches `bee_knowledge` first
  - Order: `bee_knowledge`, `axioms`, `agent_knowledge`, `training_pairs`
  - FAISS context enrichment also includes `bee_knowledge`
- `tools/waggle_backup.py`: registered `test_bee_knowledge_faiss` — now **65 test suites**

**Seasonal queries now return real Finnish beekeeping calendar content instead of LLM fallback**

## [1.7.0] — 2026-03-13

### Bee Axiom FAISS Indexing

#### Added
- **`tools/index_bee_axioms.py`**: Incremental FAISS indexer for bee-domain axioms
  - Checks `_doc_ids` before adding — skips already-indexed, no duplicates
  - Indexed 43 new vectors: honey_yield (15), varroa_treatment (12), swarm_risk (10), colony_food_reserves (6)
  - Axioms collection: 92 → 135 vectors
  - Verification search: honey_yield score 0.838 for "honey yield colony strength"
- **`tests/test_axiom_faiss.py`**: 8 tests verifying bee axiom FAISS state
  - Collection count >= 130, all 4 bee model main/variable/formula doc_ids present
  - Metadata correctness (type, model_id, domain), no duplicate doc_ids

#### Changed
- `tools/waggle_backup.py`: registered `test_axiom_faiss` — now **64 test suites**

**FAISS axioms collection: 135 vectors (92 base + 43 bee axioms)**

## [1.6.0] — 2026-03-13

### Colony Food Reserves + Full Bee-Domain Coverage

#### Added
- **`colony_food_reserves.yaml`** (`configs/axioms/cottage/`): winter food sufficiency model
  - Calculates food_needed, food_deficit, survival_weeks, feeding_needed_kg
  - `max(food_deficit, 0)` — no negative feeding values
  - Critical when feeding_needed > 5 kg, warning when > 0 kg
- **Cottage capsule `colony_food_reserves` key_decision** (9 keywords: ruoka/varasto/fondant/sokeriliu/tarpeeksi/riittää)
- **`test_bee_axioms.py` extended to 12 tests**: added colony_food_reserves adequate/insufficient store tests
- **`tests/test_capsule_routing.py`**: 16 integration tests covering all 8 cottage capsule key_decisions
  - All 8 routing decisions (FI + EN), cross-routing isolation, confidence threshold, router stats
  - Axiom file existence check, models list validation, non-bee query isolation

#### Fixed
- Cottage capsule `hive_survival` keywords: removed "mehiläi"/"bee" (too broad), added "talvehtimi"
- `honey_yield` keywords: added "mehiläi", "hunajasato" for better disambiguation
- `colony_food_reserves` keywords capped at 9 (1/9=0.11 > threshold 0.10)

**Bee-domain axiom coverage: 5 models — honey_yield, varroa_treatment, swarm_risk, colony_food_reserves, hive_thermal_balance**
**63 test suites, 880 tests**

## [1.5.0] — 2026-03-13

### Bee-Domain Axioms + Capsule Routing + FAISS Complete

#### Added
- **Bee axiom models** (`configs/axioms/cottage/`):
  - `honey_yield.yaml` — seasonal harvest estimation (colony strength × forager ratio × nectar flow × days)
  - `varroa_treatment.yaml` — OA dosage + mite load % + reinfestation rate + critical alerts
  - `swarm_risk.yaml` — swarming probability from space pressure + queen age + queen cells + season
- **Cottage capsule key_decisions** for all 3 bee models (keywords → model_based routing)
- **`tests/test_bee_axioms.py`**: 10 tests — yield ordering, mite load %, critical/warning risk levels, solve_for_chat NL, ModelResult.to_dict()
- **training_pairs FAISS collection**: 10,542 vectors embedded from `finetune_curated.jsonl` (score≥8.0, deduped)
  - **Total FAISS index: 10,784 vectors** (axioms=92, agent_knowledge=150, training_pairs=10,542)

#### Fixed
- **Capsule routing confidence threshold** (`core/smart_router_v2.py`): 0.2 → 0.1 (1 hit out of 7+ keywords now routes)
- **`hive_survival` keywords**: removed broad "mehiläi"/"bee" → added "talvehtimi" to prevent collisions
- **`honey_yield` keywords**: added "hunajasato", "mehiläi" for higher hit count in bee harvest queries

#### Changed
- `tools/waggle_backup.py`: registered `test_bee_axioms` — now **62 test suites**

## [1.4.0] — 2026-03-13

### Statistical Layer + Finnish Inflection Fix

#### Added
- **Statistical layer handler** (`core/chat_handler.py`): activates on trend/average/anomaly queries
  - Collects metrics from `consciousness.get_stats()` and `elastic_scaler.summary()`
  - Returns bilingual EN/FI system stats (memory entries, HW tier, CPU/RAM %)
  - Falls through to LLM when no subsystem stats available
  - Sets `_last_chat_method='statistical'`, `_last_explanation={method,stats}`
- **`tests/test_statistical_layer.py`**: 8 tests — SmartRouter routing EN/FI, no false positives, response format, explanation structure

#### Fixed
- **SmartRouter v2 `_STAT_KEYWORDS`**: removed trailing `\b` for Finnish stems
  - `anomaalia`, `poikkeamaa` (partitive) now correctly route to statistical
  - Added `anomaali` alongside `anomal`

#### Changed
- `tools/waggle_backup.py`: registered `test_statistical_layer` — now **61 test suites**

## [1.3.0] — 2026-03-13

### Retrieval Layer Handler + training_pairs Vectorization

#### Added
- **Retrieval layer handler** (`core/chat_handler.py`): new layer between rule_constraints and FI→EN translation
  - Activated when SmartRouter v2 routes to `retrieval` (what is/explain/mitä on/selitä keywords)
  - Embeds query via consciousness.embed, searches axioms + agent_knowledge + training_pairs FAISS collections
  - Filters score > 0.35, formats top-5 hits as numbered list (bilingual EN/FI header)
  - Falls through to LLM when no good FAISS hits available
  - Sets `_last_chat_method='retrieval'`, `_last_explanation={method,hits}` for ReasoningDashboard
- **`tests/test_retrieval_layer.py`**: 8 tests — SmartRouter routing EN/FI, FAISS ranking, score threshold, response headers, explanation structure, fall-through on empty

#### Changed
- `tools/waggle_backup.py`: registered `test_retrieval_layer` — now **60 test suites**

## [1.2.0] — 2026-03-13

### FAISS Wiring + Enriched Chat API

#### Added
- **FAISS API endpoints** (`web/dashboard.py`): `GET /api/faiss/stats` (collection counts + dims), `POST /api/faiss/search` (embed via nomic-embed-text + cosine search)
- **FAISS stats in ReasoningDashboard** (`dashboard/src/ReasoningDashboard.jsx`): Under the Hood panel shows per-collection vector counts
- **FAISS retrieval layer** (`core/smart_router_v2.py`): `_RETRIEVAL_KEYWORDS` pattern routes "what is/explain/mitä on/selitä…" to `retrieval` layer
- **FAISS context enrichment** (`core/chat_handler.py`): after memory context fetch, embeds query and prepends top-5 FAISS hits (score > 0.35) as DOMAIN KNOWLEDGE to LLM prompt
- **Enriched `/api/chat` response**: now includes `method`, `model_result`, `explanation` fields — feeds ReasoningDashboard Decision Trace
- **`_last_model_result` / `_last_explanation`** tracking in `ChatHandler._do_chat`: reset each request, set on model_based/rule_constraints paths
- **`tests/test_faiss_api.py`**: 8 tests — stats structure, search keys, score range [-1,1], empty collection
- **`tests/test_faiss_retrieval.py`**: 10 tests — keyword pattern, SmartRouter routing, SearchResult fields, ranking, threshold
- **`tests/test_chat_model_result.py`**: 8 tests — reset tracking, ConstraintResult/SolverResult/ModelResult to_dict(), ExplainabilityEngine steps

#### Changed
- `hivemind.py`: FaissRegistry initialised at startup (data/faiss/), logs collection stats
- `tools/waggle_backup.py`: registered 3 new suites — now **59 test suites**

## [1.1.0] — 2026-03-13

### FAISS Vector Store + ReasoningDashboard

#### Added
- **FAISS Store** (`core/faiss_store.py`): `FaissCollection` (IndexFlatIP + L2 normalisation = cosine similarity), `FaissRegistry`, `SearchResult`; persists to `data/faiss/{name}/{index.faiss,meta.json}`
- **Vectorize tool** (`tools/vectorize_all.py`): one-shot script that embeds axiom YAMLs, agent YAMLs (75 agents) and curated training pairs (score ≥ 8.0) into both FAISS and ChromaDB collections
- **ReasoningDashboard** (`dashboard/src/ReasoningDashboard.jsx`): production single-file React dashboard — dark theme, bilingual EN/FI, 5-layer animation on each query, solver predictions panel, 👍/👎 feedback with correction form, friendly error messages, RuntimeOps slide-out panel, MorningBrief
- **Dashboard toggle** (`dashboard/src/App.jsx`): `[CLASSIC | REASONING]` topbar button; `← Classic` back button in ReasoningDashboard
- **`/api/solve` endpoint** (`web/dashboard.py`): `POST {model_id, inputs}` → `SymbolicSolver.solve()` → JSON result
- **`tests/test_faiss_store.py`**: 10 tests — identical vector score ≥ 0.99, k-cap, add_batch, persist/reload, registry identity & stats, empty collection

#### Changed
- `tools/waggle_backup.py`: registered `test_faiss_store` — now **56 test suites**

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
