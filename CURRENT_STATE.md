# WaggleDance Swarm — Project State (auto-generated)

**Generated**: 2026-03-20T15:14:24+0200
**Commit**: `ad5cbf0` on `main`
**Generator**: `python tools/generate_state.py`

> This file is auto-generated from actual code. Do not edit manually.
> Re-run `python tools/generate_state.py` after any major change.

## Summary

- **Hexagonal runtime** (`waggledance/`): 80 core modules, 24,863 lines
- **Legacy core** (`core/`): 85 modules, 27,204 lines
- **Tests**: 198 files, 3339 test functions
- **Licensing**: 24 BUSL-protected files, 90 Apache files

## Security Invariants

- [PASS] raw eval in solver
- [PASS] safe eval exists
- [PASS] ci pipeline
- [PASS] resource guard
- [PASS] otel tracing

## Hardware Presets

| Preset | Profile | Max Agents | Model |
|--------|---------|--------:|-------|
| `cottage-full` | cottage | 30 | llama3.2:3b |
| `factory-production` | factory | 75 | llama3.2:3b |
| `raspberry-pi-iot` | gadget | 5 | phi4-mini |

## Hexagonal Core Modules (`waggledance/core/`)

| Module | Lines | Classes | Status |
|--------|------:|---------|--------|
| `waggledance/core/actions/action_bus.py` | 240 | ActionResult, SafeActionBus | Complete |
| `waggledance/core/autonomy/attention_budget.py` | 228 | AttentionAllocation, AttentionBudget | Complete |
| `waggledance/core/autonomy/compatibility.py` | 224 | LegacyResult, AutonomyResult, CompatibilityLayer | Complete |
| `waggledance/core/autonomy/lifecycle.py` | 212 | RuntimeState, RuntimeMode, HealthCheck +1 | Complete |
| `waggledance/core/autonomy/metrics.py` | 235 | MetricSample, AutonomyMetrics | Complete |
| `waggledance/core/autonomy/resource_kernel.py` | 510 | LoadLevel, ResourceTier, ResourceSnapshot +5 | Complete |
| `waggledance/core/autonomy/runtime.py` | 950 | AutonomyRuntime | Complete |
| `waggledance/core/capabilities/aliasing.py` | 176 | AgentAlias, AliasRegistry | Complete |
| `waggledance/core/capabilities/registry.py` | 429 | CapabilityRegistry | Complete |
| `waggledance/core/capabilities/selector.py` | 313 | SelectionResult, CapabilitySelector | Complete |
| `waggledance/core/domain/agent.py` | 30 | AgentDefinition, AgentResult | Complete |
| `waggledance/core/domain/autonomy.py` | 493 | GoalType, GoalStatus, ActionStatus +13 | Complete |
| `waggledance/core/domain/events.py` | 55 | EventType, DomainEvent | Complete |
| `waggledance/core/domain/memory_record.py` | 18 | MemoryRecord | Stub |
| `waggledance/core/domain/task.py` | 26 | TaskRequest, TaskRoute | Complete |
| `waggledance/core/domain/trust_score.py` | 25 | TrustSignals, AgentTrust | Complete |
| `waggledance/core/goals/goal_engine.py` | 265 | GoalEngine | Complete |
| `waggledance/core/goals/mission_store.py` | 99 | MissionStore | Complete |
| `waggledance/core/goals/motives.py` | 162 | MotiveConfig, ConflictResult, MotiveRegistry | Complete |
| `waggledance/core/learning/capability_confidence.py` | 178 | ConfidenceEntry, CapabilityConfidenceTracker | Complete |
| `waggledance/core/learning/case_builder.py` | 254 | CaseTrajectoryBuilder | Complete |
| `waggledance/core/learning/consolidator.py` | 169 | EpisodeRecord, ConsolidationResult | Complete |
| `waggledance/core/learning/dream_mode.py` | 367 | DreamCandidate, CounterfactualResult, DreamSession +1 | Complete |
| `waggledance/core/learning/legacy_converter.py` | 140 | LegacyRecord, LegacyConverter | Complete |
| `waggledance/core/learning/morning_report.py` | 251 | MorningReport, MorningReportBuilder | Complete |
| `waggledance/core/learning/night_learning_pipeline.py` | 296 | NightLearningResult, NightLearningPipeline | Complete |
| `waggledance/core/learning/prediction_error_ledger.py` | 233 | PredictionError, SolverErrorProfile, LedgerAnalysis +1 | Complete |
| `waggledance/core/learning/procedural_memory.py` | 204 | Procedure, ProceduralMemory | Complete |
| `waggledance/core/learning/quality_gate.py` | 170 | PromotionDecision, QualityGate | Complete |
| `waggledance/core/magma/audit_projector.py` | 233 | AuditEntry, AuditProjector | Projection (read-only) |
| `waggledance/core/magma/confidence_decay.py` | 52 |  | Complete |
| `waggledance/core/magma/event_log_adapter.py` | 189 | EventLogEntry, EventLogAdapter | Complete |
| `waggledance/core/magma/provenance.py` | 201 | ProvenanceRecord, ProvenanceAdapter | Complete |
| `waggledance/core/magma/replay_engine.py` | 274 | MissionReplayEntry, MissionReplay, ReplayAdapter | Complete |
| `waggledance/core/magma/trust_adapter.py` | 202 | TrustRecord, TrustAdapter | Complete |
| `waggledance/core/memory/working_memory.py` | 204 | MemorySlot, WorkingMemory | Complete |
| `waggledance/core/orchestration/lifecycle.py` | 52 | AgentLifecycleManager | Complete |
| `waggledance/core/orchestration/orchestrator.py` | 268 | Orchestrator | Complete |
| `waggledance/core/orchestration/round_table.py` | 150 | ConsensusResult, RoundTableEngine | Complete |
| `waggledance/core/orchestration/routing_policy.py` | 157 | RoutingFeatures | Complete |
| `waggledance/core/orchestration/scheduler.py` | 173 | SchedulerState, Scheduler | Complete |
| `waggledance/core/planning/planner.py` | 174 | Planner | Complete |
| `waggledance/core/policies/confidence_policy.py` | 22 |  | Complete |
| `waggledance/core/policies/escalation_policy.py` | 39 | EscalationPolicy | Complete |
| `waggledance/core/policies/fallback_policy.py` | 62 | FallbackChain | Complete |
| `waggledance/core/policy/approvals.py` | 172 | ApprovalRequest, ApprovalManager | Complete |
| `waggledance/core/policy/constitution.py` | 267 | ConstitutionRule, ProfileThresholds, Constitution | Complete |
| `waggledance/core/policy/policy_engine.py` | 317 | PolicyDecision, PolicyEngine | Complete |
| `waggledance/core/policy/risk_scoring.py` | 129 | RiskScorer | Complete |
| `waggledance/core/policy/safety_cases.py` | 214 | SafetyEvidence, SafetyCase, SafetyCaseBuilder | Complete |
| `waggledance/core/ports/config_port.py` | 13 | ConfigPort | Stub |
| `waggledance/core/ports/event_bus_port.py` | 17 | EventBusPort | Stub |
| `waggledance/core/ports/hot_cache_port.py` | 15 | HotCachePort | Stub |
| `waggledance/core/ports/llm_port.py` | 19 | LLMPort | Stub |
| `waggledance/core/ports/memory_repository_port.py` | 26 | MemoryRepositoryPort | Complete |
| `waggledance/core/ports/sensor_port.py` | 11 | SensorPort | Stub |
| `waggledance/core/ports/trust_store_port.py` | 19 | TrustStorePort | Stub |
| `waggledance/core/ports/vector_store_port.py` | 25 | VectorStorePort | Complete |
| `waggledance/core/projections/autobiographical_index.py` | 152 | EpisodeEntry, AutobiographicalSummary | Projection (read-only) |
| `waggledance/core/projections/introspection_view.py` | 127 | IntrospectionSnapshot | Projection (read-only) |
| `waggledance/core/projections/narrative_projector.py` | 228 | _CacheEntry | Projection (read-only) |
| `waggledance/core/projections/projection_validator.py` | 118 | ValidationResult | Projection (read-only) |
| `waggledance/core/reasoning/anomaly_engine.py` | 216 | AnomalyResult, AnomalyEngine | Complete |
| `waggledance/core/reasoning/bee_domain_engine.py` | 346 | ColonyHealthResult, SwarmRiskResult, HoneyYieldResult +1 | Complete |
| `waggledance/core/reasoning/causal_engine.py` | 231 | CausalChain, ImpactEstimate, CausalEngine | Complete |
| `waggledance/core/reasoning/optimization_engine.py` | 234 | OptimizationResult, OptimizationEngine | Complete |
| `waggledance/core/reasoning/route_engine.py` | 223 | RouteMetrics, RouteDecision, RouteEngine | Complete |
| `waggledance/core/reasoning/seasonal_engine.py` | 159 | SeasonalEngine | Complete |
| `waggledance/core/reasoning/solver_router.py` | 370 | SolverRouteResult, SolverRouter | Complete |
| `waggledance/core/reasoning/stats_engine.py` | 205 | StatsResult, StatsEngine | Complete |
| `waggledance/core/reasoning/thermal_solver.py` | 256 | ThermalResult, ThermalSolver | Complete |
| `waggledance/core/reasoning/verifier.py` | 288 | VerifierResult, Verifier | Complete |
| `waggledance/core/specialist_models/meta_optimizer.py` | 166 | CanaryRecord, HyperparameterProposal, MetaOptimizerState | Complete |
| `waggledance/core/specialist_models/model_store.py` | 236 | ModelStatus, ModelVersion, ModelStore | Complete |
| `waggledance/core/specialist_models/specialist_trainer.py` | 890 | TrainingResult, SpecialistTrainer | Complete |
| `waggledance/core/world/baseline_store.py` | 162 | Baseline, BaselineStore | Complete |
| `waggledance/core/world/entity_registry.py` | 103 | Entity, EntityRegistry | Complete |
| `waggledance/core/world/epistemic_uncertainty.py` | 204 | BaselineProvider, EntityProvider, GoalProvider +2 | Complete |
| `waggledance/core/world/graph_builder.py` | 192 | GraphBuilder | Complete |
| `waggledance/core/world/world_model.py` | 268 | WorldModel | Complete |

## Legacy Core Modules (`core/`)

| Module | Lines | Classes | Status |
|--------|------:|---------|--------|
| `core/active_learning.py` | 106 | LearningCandidate, ActiveLearningScorer | Complete |
| `core/adaptive_throttle.py` | 362 | ThrottleState, AdaptiveThrottle | Complete |
| `core/agent_channels.py` | 93 | AgentChannel, ChannelRegistry | Complete |
| `core/agent_levels.py` | 347 | AgentLevel, AgentStats, AgentLevelManager | Complete |
| `core/agent_rollback.py` | 89 | AgentRollback | Complete |
| `core/audit_log.py` | 170 | AuditLog | Complete |
| `core/auto_install.py` | 192 |  | Complete |
| `core/canary_promoter.py` | 157 | CanaryResult, CanaryPromoter | Complete |
| `core/causal_replay_api.py` | 55 | ReplayResult, CausalReplayService | Complete |
| `core/chat_delegation.py` | 214 | AgentDelegator | Complete |
| `core/chat_handler.py` | 380 | ChatHandler | Complete |
| `core/chat_history.py` | 199 | ChatHistory | Complete |
| `core/chat_preprocessing.py` | 210 | PreprocessResult, ChatPreprocessor | Complete |
| `core/chat_router.py` | 112 | ChatResult, ChatRouter | Complete |
| `core/chat_routing_engine.py` | 458 | ChatRoutingEngine | Complete |
| `core/chat_telemetry.py` | 96 | ChatTelemetry | Complete |
| `core/chromadb_adapter.py` | 187 | StoreAdapter, ChromaDBAdapter | Complete |
| `core/circuit_breaker.py` | 90 | CircuitBreaker | Complete |
| `core/code_reviewer.py` | 223 | CodeSelfReview | Complete |
| `core/cognitive_graph.py` | 257 | CognitiveGraph | Complete |
| `core/constraint_engine.py` | 257 | RuleResult, ConstraintResult, ConstraintEngine | Complete |
| `core/cross_agent_search.py` | 81 | CrossAgentSearch | Complete |
| `core/disk_guard.py` | 87 | DiskSpaceError | Complete |
| `core/domain_capsule.py` | 250 | DecisionMatch, LayerConfig, DomainCapsule | Complete |
| `core/domain_model_miner.py` | 291 | ColumnInfo, DocumentPattern, LayerScore +4 | Complete |
| `core/elastic_scaler.py` | 340 | HardwareProfile, TierConfig, ElasticScaler | Complete |
| `core/embedding_cache.py` | 399 | EmbeddingEngine, EvalEmbeddingEngine | Complete |
| `core/en_validator.py` | 553 | WordNetLayer, ValidationResult, ENValidator | Complete |
| `core/english_source_learner.py` | 103 | SourceConfig, LearnedFact, EnglishSourceLearner | Complete |
| `core/explainability.py` | 192 | ExplanationStep, Explanation, ExplainabilityEngine | Complete |
| `core/faiss_store.py` | 199 | SearchResult, FaissCollection, FaissRegistry | Complete |
| `core/fast_memory.py` | 826 | HotCache, BilingualMemoryStore, FiFastStore +1 | Complete |
| `core/hallucination_checker.py` | 212 | HallucinationResult, HallucinationChecker | Complete |
| `core/heartbeat_controller.py` | 697 | HeartbeatController | Complete |
| `core/hive_routing.py` | 538 |  | Complete |
| `core/hive_support.py` | 141 | PriorityLock, StructuredLogger | Complete |
| `core/knowledge_distiller.py` | 280 | KnowledgeDistiller | Complete |
| `core/knowledge_loader.py` | 410 | KnowledgeLoader | Complete |
| `core/language_readiness.py` | 75 | LanguageCapability, LanguageReadiness | Complete |
| `core/learning_engine.py` | 1143 | QualityScore, PromptExperiment, PromptWin +2 | Complete |
| `core/learning_ledger.py` | 140 | LedgerEntry, LearningLedger | Complete |
| `core/learning_task_queue.py` | 178 | LearningTaskQueue | Complete |
| `core/live_monitor.py` | 143 | EventCategory, MonitorEvent, LiveMonitor | Complete |
| `core/llm_provider.py` | 260 | LLMCircuitBreaker, LLMResponse, LLMProvider | Complete |
| `core/lora_readiness.py` | 109 | ReadinessCheck, ReadinessManifest, LoRAReadinessChecker | Complete |
| `core/math_solver.py` | 113 | MathSolver | Complete |
| `core/memory_engine.py` | 1345 | MemoryMatch, PreFilterResult, MemoryStore +1 | Complete |
| `core/memory_eviction.py` | 166 | MemoryEviction | Complete |
| `core/memory_overlay.py` | 327 | MemoryOverlay, OverlayRegistry, OverlayBranch +3 | Complete |
| `core/memory_proxy.py` | 167 | Role, WriteMode, MemoryWriteProxy | Complete |
| `core/meta_learning.py` | 609 | MetaLearningEngine, AgentOverlapDetector | Complete |
| `core/micro_model.py` | 1218 | PatternMatchEngine, ClassifierModel, LoRAModel +2 | Complete |
| `core/model_interface.py` | 142 | ModelResult, BaseModel | Complete |
| `core/mqtt_sensor_ingest.py` | 107 | SensorReading, MQTTSensorIngest | Complete |
| `core/night_enricher.py` | 1786 | EnrichmentCandidate, QualityVerdict, SourceMetrics +12 | Complete |
| `core/night_mode_controller.py` | 565 | NightModeController | Complete |
| `core/normalizer.py` | 360 |  | Complete |
| `core/observability.py` | 45 |  | Complete |
| `core/ops_agent.py` | 826 | ModelProfile, OllamaSnapshot, OpsDecision +1 | Complete |
| `core/opus_mt_adapter.py` | 85 | OpusMTAdapter | Complete |
| `core/prompt_experiment_status.py` | 51 | ExperimentSummary, ExperimentStatusFormatter | Complete |
| `core/provenance.py` | 130 | ProvenanceTracker | Complete |
| `core/rag_verifier.py` | 197 | Claim, VerificationResult, RAGVerifier | Complete |
| `core/replay_engine.py` | 271 | ReplayEngine | Complete |
| `core/replay_store.py` | 88 | ReplayStore | Complete |
| `core/resource_guard.py` | 122 | ResourceState, ResourceGuard | Complete |
| `core/round_table_controller.py` | 545 | RoundTableController | Complete |
| `core/route_explainability.py` | 87 | RouteExplanation | Complete |
| `core/route_telemetry.py` | 121 | RouteStats, RouteTelemetry | Complete |
| `core/safe_eval.py` | 115 | SafeEvalError | Complete |
| `core/seasonal_guard.py` | 272 | SeasonalViolation, SeasonalGuard | Complete |
| `core/settings_validator.py` | 101 | LLMConfig, LearningConfig, HiveMindConfig +3 | Complete |
| `core/shared_routing_helpers.py` | 69 |  | Complete |
| `core/smart_router_v2.py` | 257 | RouteResult, SmartRouterV2 | Complete |
| `core/structured_logging.py` | 80 |  | Complete |
| `core/swarm_scheduler.py` | 518 | AgentScore, TaskBid, SwarmScheduler | Complete |
| `core/symbolic_solver.py` | 366 | SolverResult, ModelRegistry, SymbolicSolver | Complete |
| `core/token_economy.py` | 142 | TokenEconomy | Complete |
| `core/tracing.py` | 81 | _NoOpTracer, _NoOpSpan | Complete |
| `core/training_collector.py` | 443 | TrainingDataCollector | Complete |
| `core/translation_proxy.py` | 1687 | VoikkoEngine, OpusMTFallback, TranslationProxy +2 | Complete |
| `core/trust_engine.py` | 310 | TrustSignal, AgentReputation, TrustEngine | Complete |
| `core/web_learner.py` | 263 | WebLearningAgent | Complete |
| `core/whisper_protocol.py` | 446 | Whisper, WhisperProtocol | Complete |
| `core/yaml_bridge.py` | 680 | YAMLBridge | Complete |

## Verification Commands

```bash
# Clone and verify:
git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git
cd waggledance-swarm
git checkout ad5cbf0

# Count core modules (expect 40+):
find waggledance/core -name "*.py" -not -name "__init__.py" | wc -l

# Run tests:
pip install -r requirements.txt
pytest tests/ --collect-only -q | tail -1              # expect 3339+
```
