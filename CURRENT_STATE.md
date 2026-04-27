# WaggleDance Swarm — Project State (auto-generated)

**Generated**: 2026-04-27T16:28:29+0300
**Commit**: `a1c4152` on `feature/post-v3.6.0-truthfulness`
**Generator**: `python tools/generate_state.py`

> This file is auto-generated from actual code. Do not edit manually.
> Re-run `python tools/generate_state.py` after any major change.

## Summary

- **Hexagonal runtime** (`waggledance/`): 181 core modules, 47,010 lines
- **Legacy core** (`core/`): 85 modules, 27,332 lines
- **Tests**: 295 files, 5512 test functions
- **Licensing**: 138 BUSL-protected files, 105 Apache files

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
| `waggledance/core/api_distillation/api_consultant.py` | 191 | TrustGateResult, ConsultationRecord | Complete |
| `waggledance/core/api_distillation/knowledge_extractor.py` | 126 | ExtractedFact, ExtractedSolverSpec, ExtractedLesson | Complete |
| `waggledance/core/api_distillation/offline_replay_engine.py` | 90 |  | Complete |
| `waggledance/core/autonomy/action_gate.py` | 236 | GateVerdict, GateBatchReport | Complete |
| `waggledance/core/autonomy/attention_allocator.py` | 115 | AttentionWeight | Complete |
| `waggledance/core/autonomy/attention_budget.py` | 228 | AttentionAllocation, AttentionBudget | Complete |
| `waggledance/core/autonomy/background_scheduler.py` | 118 | DispatchReport | Complete |
| `waggledance/core/autonomy/budget_engine.py` | 210 | BudgetViolation, BudgetReport | Complete |
| `waggledance/core/autonomy/circuit_breaker.py` | 241 | BreakerEvent | Complete |
| `waggledance/core/autonomy/compatibility.py` | 225 | LegacyResult, AutonomyResult, CompatibilityLayer | Complete |
| `waggledance/core/autonomy/governor.py` | 239 | ActionRecommendation, TickReport | Complete |
| `waggledance/core/autonomy/kernel_state.py` | 302 | TickIdentity, BudgetEntry, CircuitBreakerSnapshot +1 | Complete |
| `waggledance/core/autonomy/lifecycle.py` | 213 | RuntimeState, RuntimeMode, HealthCheck +1 | Complete |
| `waggledance/core/autonomy/metrics.py` | 236 | MetricSample, AutonomyMetrics | Complete |
| `waggledance/core/autonomy/micro_learning_lane.py` | 110 | PriorityHint | Complete |
| `waggledance/core/autonomy/mission_queue.py` | 284 | Mission | Complete |
| `waggledance/core/autonomy/policy_core.py` | 251 | PolicyRule, HardRule, PolicyEvaluation +1 | Complete |
| `waggledance/core/autonomy/resource_kernel.py` | 511 | LoadLevel, ResourceTier, ResourceSnapshot +5 | Complete |
| `waggledance/core/autonomy/runtime.py` | 1240 | AutonomyRuntime | Complete |
| `waggledance/core/builder_lane/builder_lane_router.py` | 76 | BuilderRoutingDecision | Complete |
| `waggledance/core/builder_lane/builder_request_pack.py` | 116 | BuilderRequest | Complete |
| `waggledance/core/builder_lane/builder_result_pack.py` | 114 | BuilderArtifact, BuilderResult | Complete |
| `waggledance/core/builder_lane/mentor_forge.py` | 83 | MentorPrompt | Complete |
| `waggledance/core/builder_lane/repair_forge.py` | 71 | RepairContext | Complete |
| `waggledance/core/builder_lane/session_forge.py` | 52 | ForgePlan | Complete |
| `waggledance/core/builder_lane/worktree_allocator.py` | 121 | WorktreeAllocation, InvocationLogEntry | Complete |
| `waggledance/core/capabilities/aliasing.py` | 176 | AgentAlias, AliasRegistry | Complete |
| `waggledance/core/capabilities/registry.py` | 429 | CapabilityRegistry | Complete |
| `waggledance/core/capabilities/selector.py` | 313 | SelectionResult, CapabilitySelector | Complete |
| `waggledance/core/capsules/capsule_registry.py` | 148 | CapsuleValidationError, CapsuleManifest, CapsuleRegistry | Complete |
| `waggledance/core/capsules/capsule_resolver.py` | 104 | BlastRadiusViolation | Complete |
| `waggledance/core/conversation/context_synthesizer.py` | 214 | ContextBundle, PatternViolation | Complete |
| `waggledance/core/conversation/meta_dialogue.py` | 166 | MetaResponse | Complete |
| `waggledance/core/conversation/presence_log.py` | 146 | PresenceEntry | Complete |
| `waggledance/core/cross_capsule/abstract_pattern_registry.py` | 110 | AbstractPatternRegistryError, AbstractPatternRecord, AbstractPatternRegistry | Complete |
| `waggledance/core/cross_capsule/cross_capsule_observer.py` | 165 | CrossCapsuleObserverError, CapsuleSignalSummary, CrossCapsuleObservation +1 | Complete |
| `waggledance/core/domain/agent.py` | 30 | AgentDefinition, AgentResult | Complete |
| `waggledance/core/domain/autonomy.py` | 545 | GoalType, GoalStatus, ActionStatus +13 | Complete |
| `waggledance/core/domain/events.py` | 55 | EventType, DomainEvent | Complete |
| `waggledance/core/domain/hex_mesh.py` | 213 | HexCoord, HexCellDefinition, HexCellHealth +4 | Complete |
| `waggledance/core/domain/memory_record.py` | 18 | MemoryRecord | Stub |
| `waggledance/core/domain/task.py` | 26 | TaskRequest, TaskRoute | Complete |
| `waggledance/core/domain/trust_score.py` | 25 | TrustSignals, AgentTrust | Complete |
| `waggledance/core/goals/goal_engine.py` | 276 | GoalEngine | Complete |
| `waggledance/core/goals/mission_store.py` | 99 | MissionStore | Complete |
| `waggledance/core/goals/motives.py` | 162 | MotiveConfig, ConflictResult, MotiveRegistry | Complete |
| `waggledance/core/hex_cell_topology.py` | 242 | CellAssignment, HexCellTopology | Complete |
| `waggledance/core/hex_topology/cell_local_state.py` | 51 | CellLocalState | Complete |
| `waggledance/core/hex_topology/cell_message_contract.py` | 76 | CellMessage | Complete |
| `waggledance/core/hex_topology/cell_runtime.py` | 98 | CellRuntime | Complete |
| `waggledance/core/hex_topology/parent_child_relations.py` | 61 |  | Complete |
| `waggledance/core/hex_topology/ring_messaging.py` | 85 | RingDelivery | Complete |
| `waggledance/core/hex_topology/subdivision_operator.py` | 119 | SubdivisionPlan | Complete |
| `waggledance/core/ingestion/link_manager.py` | 115 | LinkRecord | Complete |
| `waggledance/core/ingestion/link_watcher.py` | 79 | LinkObservation | Complete |
| `waggledance/core/ingestion/universal_ingestor.py` | 215 | IngestionManifest | Complete |
| `waggledance/core/ir/adapters/from_curiosity.py` | 37 |  | Complete |
| `waggledance/core/ir/adapters/from_dream.py` | 77 |  | Complete |
| `waggledance/core/ir/adapters/from_hive.py` | 65 |  | Complete |
| `waggledance/core/ir/adapters/from_self_model.py` | 50 |  | Complete |
| `waggledance/core/ir/cognition_ir.py` | 219 | Dependency, Provenance, IRObject | Complete |
| `waggledance/core/ir/ir_compatibility.py` | 31 |  | Complete |
| `waggledance/core/ir/ir_translator.py` | 68 |  | Complete |
| `waggledance/core/ir/ir_validator.py` | 111 | IRValidationError | Complete |
| `waggledance/core/learning/capability_confidence.py` | 178 | ConfidenceEntry, CapabilityConfidenceTracker | Complete |
| `waggledance/core/learning/case_builder.py` | 258 | CaseTrajectoryBuilder | Complete |
| `waggledance/core/learning/composition_graph.py` | 512 | IOSig, SolverNode, SolverEdge +4 | Complete |
| `waggledance/core/learning/consolidator.py` | 169 | EpisodeRecord, ConsolidationResult | Complete |
| `waggledance/core/learning/dream_mode.py` | 414 | DreamCandidate, CounterfactualResult, DreamSession +1 | Complete |
| `waggledance/core/learning/embedding_cache.py` | 176 | EmbeddingCache | Complete |
| `waggledance/core/learning/legacy_converter.py` | 140 | LegacyRecord, LegacyConverter | Complete |
| `waggledance/core/learning/morning_report.py` | 263 | MorningReport, MorningReportBuilder | Complete |
| `waggledance/core/learning/night_learning_pipeline.py` | 308 | NightLearningResult, NightLearningPipeline | Complete |
| `waggledance/core/learning/prediction_error_ledger.py` | 281 | PredictionError, SolverErrorProfile, LedgerAnalysis +1 | Complete |
| `waggledance/core/learning/procedural_memory.py` | 204 | Procedure, ProceduralMemory | Complete |
| `waggledance/core/learning/quality_gate.py` | 170 | PromotionDecision, QualityGate | Complete |
| `waggledance/core/learning/solver_hash.py` | 265 | HashRegistry | Complete |
| `waggledance/core/learning/synthetic_accelerator.py` | 267 | AcceleratorMetrics, AcceleratorStatus, SyntheticTrainingAccelerator | Complete |
| `waggledance/core/local_intelligence/drift_detector.py` | 113 | _DriftDetectorError, DriftReport, DriftDetector | Complete |
| `waggledance/core/local_intelligence/fine_tune_pipeline.py` | 165 | FineTunePipelineError, FineTuneJobSpec, FineTuneJobReport +1 | Complete |
| `waggledance/core/local_intelligence/inference_router.py` | 182 | InferenceRouterError, InferenceDecision, InferenceRouter | Complete |
| `waggledance/core/local_intelligence/local_model_manager.py` | 177 | LocalModelManagerError, LocalModelRecord, LocalModelManager | Complete |
| `waggledance/core/local_intelligence/model_evaluator.py` | 84 | _ModelEvaluatorError, ModelEvaluationReport, ModelEvaluator | Complete |
| `waggledance/core/magma/audit_projector.py` | 233 | AuditEntry, AuditProjector | Projection (read-only) |
| `waggledance/core/magma/confidence_decay.py` | 52 |  | Complete |
| `waggledance/core/magma/event_log_adapter.py` | 189 | EventLogEntry, EventLogAdapter | Complete |
| `waggledance/core/magma/provenance.py` | 201 | ProvenanceRecord, ProvenanceAdapter | Complete |
| `waggledance/core/magma/replay_engine.py` | 274 | MissionReplayEntry, MissionReplay, ReplayAdapter | Complete |
| `waggledance/core/magma/trust_adapter.py` | 202 | TrustRecord, TrustAdapter | Complete |
| `waggledance/core/magma/vector_events.py` | 315 | VectorEvent | Complete |
| `waggledance/core/memory/working_memory.py` | 204 | MemorySlot, WorkingMemory | Complete |
| `waggledance/core/memory_tiers/access_pattern_tracker.py` | 47 | AccessRecord, AccessPatternTracker | Complete |
| `waggledance/core/memory_tiers/cold_tier.py` | 25 | ColdTier | Complete |
| `waggledance/core/memory_tiers/glacier_tier.py` | 23 | GlacierTier | Complete |
| `waggledance/core/memory_tiers/hot_tier.py` | 24 | HotTier | Complete |
| `waggledance/core/memory_tiers/invariant_extractor.py` | 90 | ExtractedInvariant, InvariantStore | Complete |
| `waggledance/core/memory_tiers/pinning_engine.py` | 73 | PinRecord, PinningEngine | Complete |
| `waggledance/core/memory_tiers/tier_manager.py` | 238 | TierAssignment, TierViolation, TierManager | Complete |
| `waggledance/core/memory_tiers/warm_tier.py` | 23 | WarmTier | Complete |
| `waggledance/core/orchestration/lifecycle.py` | 52 | AgentLifecycleManager | Complete |
| `waggledance/core/orchestration/orchestrator.py` | 300 | Orchestrator | Complete |
| `waggledance/core/orchestration/round_table.py` | 191 | ConsensusResult, RoundTableEngine | Complete |
| `waggledance/core/orchestration/routing_policy.py` | 162 | RoutingFeatures | Complete |
| `waggledance/core/orchestration/scheduler.py` | 173 | SchedulerState, Scheduler | Complete |
| `waggledance/core/planning/planner.py` | 174 | Planner | Complete |
| `waggledance/core/policies/confidence_policy.py` | 22 |  | Complete |
| `waggledance/core/policies/escalation_policy.py` | 39 | EscalationPolicy | Complete |
| `waggledance/core/policies/fallback_policy.py` | 62 | FallbackChain | Complete |
| `waggledance/core/policy/approvals.py` | 172 | ApprovalRequest, ApprovalManager | Complete |
| `waggledance/core/policy/constitution.py` | 269 | ConstitutionRule, ProfileThresholds, Constitution | Complete |
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
| `waggledance/core/priority_lock.py` | 23 | PriorityLock | Complete |
| `waggledance/core/projections/autobiographical_index.py` | 152 | EpisodeEntry, AutobiographicalSummary | Projection (read-only) |
| `waggledance/core/projections/introspection_view.py` | 127 | IntrospectionSnapshot | Projection (read-only) |
| `waggledance/core/projections/narrative_projector.py` | 266 | _CacheEntry | Projection (read-only) |
| `waggledance/core/projections/projection_validator.py` | 118 | ValidationResult | Projection (read-only) |
| `waggledance/core/promotion/ladder.py` | 151 | PromotionTransition, PromotionViolation | Complete |
| `waggledance/core/promotion/rollback_engine.py` | 81 | RollbackPlan, RollbackViolation | Complete |
| `waggledance/core/promotion/stage_validators.py` | 195 |  | Complete |
| `waggledance/core/proposal_compiler/acceptance_criteria_compiler.py` | 84 |  | Complete |
| `waggledance/core/proposal_compiler/affected_files_analyzer.py` | 57 |  | Complete |
| `waggledance/core/proposal_compiler/patch_generator.py` | 34 |  | Complete |
| `waggledance/core/proposal_compiler/pr_draft_compiler.py` | 91 | ProposalBundle | Complete |
| `waggledance/core/proposal_compiler/rollout_planner.py` | 52 |  | Complete |
| `waggledance/core/proposal_compiler/test_generator.py` | 54 |  | Complete |
| `waggledance/core/provider_plane/agent_pool_registry.py` | 74 | AgentRecord, AgentPoolRegistry | Complete |
| `waggledance/core/provider_plane/provider_budget_engine.py` | 66 | ProviderBudgetEntry, ProviderBudgetState | Complete |
| `waggledance/core/provider_plane/provider_registry.py` | 60 | ProviderRecord, ProviderRegistry | Complete |
| `waggledance/core/provider_plane/provider_router.py` | 86 | RoutingDecision | Complete |
| `waggledance/core/provider_plane/request_pack_router.py` | 104 | ProviderRequest | Complete |
| `waggledance/core/provider_plane/response_normalizer.py` | 73 | ProviderResponse | Complete |
| `waggledance/core/reasoning/anomaly_engine.py` | 216 | AnomalyResult, AnomalyEngine | Complete |
| `waggledance/core/reasoning/bee_domain_engine.py` | 346 | ColonyHealthResult, SwarmRiskResult, HoneyYieldResult +1 | Complete |
| `waggledance/core/reasoning/causal_engine.py` | 231 | CausalChain, ImpactEstimate, CausalEngine | Complete |
| `waggledance/core/reasoning/hybrid_observer.py` | 201 | HybridCandidateTrace, HybridObserver | Complete |
| `waggledance/core/reasoning/hybrid_router.py` | 163 |  | Complete |
| `waggledance/core/reasoning/optimization_engine.py` | 234 | OptimizationResult, OptimizationEngine | Complete |
| `waggledance/core/reasoning/question_frame.py` | 207 | Comparator, Negation, QuestionFrame | Complete |
| `waggledance/core/reasoning/route_engine.py` | 223 | RouteMetrics, RouteDecision, RouteEngine | Complete |
| `waggledance/core/reasoning/seasonal_engine.py` | 159 | SeasonalEngine | Complete |
| `waggledance/core/reasoning/solver_router.py` | 373 | SolverRouteResult, SolverRouter | Complete |
| `waggledance/core/reasoning/stats_engine.py` | 205 | StatsResult, StatsEngine | Complete |
| `waggledance/core/reasoning/thermal_solver.py` | 256 | ThermalResult, ThermalSolver | Complete |
| `waggledance/core/reasoning/verifier.py` | 288 | VerifierResult, Verifier | Complete |
| `waggledance/core/solver_synthesis/bulk_rule_extractor.py` | 159 | FamilyMatch | Complete |
| `waggledance/core/solver_synthesis/declarative_solver_spec.py` | 123 | SolverSpec, SpecValidationError | Complete |
| `waggledance/core/solver_synthesis/deterministic_solver_compiler.py` | 210 | CompiledSolver | Complete |
| `waggledance/core/solver_synthesis/gap_to_solver_spec.py` | 65 | GapRoutingDecision | Complete |
| `waggledance/core/solver_synthesis/solver_candidate_store.py` | 221 | SolverCandidate, SolverCandidateStore | Complete |
| `waggledance/core/solver_synthesis/solver_family_registry.py` | 166 | SolverFamily, SolverFamilyRegistry | Complete |
| `waggledance/core/solver_synthesis/solver_quarantine.py` | 163 | QuotaState, AdmissionDecision | Complete |
| `waggledance/core/solver_synthesis/validators.py` | 207 | GateResult, CountedGateResult, ShadowEvalResult +1 | Complete |
| `waggledance/core/specialist_models/meta_optimizer.py` | 166 | CanaryRecord, HyperparameterProposal, MetaOptimizerState | Complete |
| `waggledance/core/specialist_models/model_store.py` | 236 | ModelStatus, ModelVersion, ModelStore | Complete |
| `waggledance/core/specialist_models/specialist_trainer.py` | 944 | TrainingResult, SpecialistTrainer | Complete |
| `waggledance/core/vector_identity/identity_anchor.py` | 127 | AnchorValidation | Complete |
| `waggledance/core/vector_identity/ingestion_dedup.py` | 143 | DedupResult | Complete |
| `waggledance/core/vector_identity/vector_provenance_graph.py` | 187 | LineageEdge, VectorNode, VectorProvenanceGraph | Complete |
| `waggledance/core/world/baseline_store.py` | 163 | Baseline, BaselineStore | Complete |
| `waggledance/core/world/entity_registry.py` | 103 | Entity, EntityRegistry | Complete |
| `waggledance/core/world/epistemic_uncertainty.py` | 362 | BaselineProvider, EntityProvider, GoalProvider +2 | Complete |
| `waggledance/core/world/graph_builder.py` | 192 | GraphBuilder | Complete |
| `waggledance/core/world/world_model.py` | 317 | WorldModel | Complete |
| `waggledance/core/world_model/calibration_drift_detector.py` | 73 | DriftAlert | Complete |
| `waggledance/core/world_model/causal_engine.py` | 106 |  | Complete |
| `waggledance/core/world_model/external_evidence_collector.py` | 90 |  | Complete |
| `waggledance/core/world_model/prediction_calibrator.py` | 66 | CalibrationRecord | Complete |
| `waggledance/core/world_model/prediction_engine.py` | 69 |  | Complete |
| `waggledance/core/world_model/world_model_delta.py` | 85 | WorldModelDelta | Complete |
| `waggledance/core/world_model/world_model_snapshot.py` | 217 | ExternalFact, CausalRelation, Prediction +1 | Complete |

## Legacy Core Modules (`core/`)

| Module | Lines | Classes | Status |
|--------|------:|---------|--------|
| `core/active_learning.py` | 106 | LearningCandidate, ActiveLearningScorer | Complete |
| `core/adaptive_throttle.py` | 362 | ThrottleState, AdaptiveThrottle | Complete |
| `core/agent_channels.py` | 93 | AgentChannel, ChannelRegistry | Complete |
| `core/agent_levels.py` | 347 | AgentLevel, AgentStats, AgentLevelManager | Complete |
| `core/agent_rollback.py` | 89 | AgentRollback | Complete |
| `core/audit_log.py` | 176 | AuditLog | Complete |
| `core/auto_install.py` | 192 |  | Complete |
| `core/canary_promoter.py` | 157 | CanaryResult, CanaryPromoter | Complete |
| `core/causal_replay_api.py` | 55 | ReplayResult, CausalReplayService | Complete |
| `core/chat_delegation.py` | 214 | AgentDelegator | Complete |
| `core/chat_handler.py` | 384 | ChatHandler | Complete |
| `core/chat_history.py` | 199 | ChatHistory | Complete |
| `core/chat_preprocessing.py` | 210 | PreprocessResult, ChatPreprocessor | Complete |
| `core/chat_router.py` | 112 | ChatResult, ChatRouter | Complete |
| `core/chat_routing_engine.py` | 537 | ChatRoutingEngine | Complete |
| `core/chat_telemetry.py` | 96 | ChatTelemetry | Complete |
| `core/chromadb_adapter.py` | 187 | StoreAdapter, ChromaDBAdapter | Complete |
| `core/circuit_breaker.py` | 90 | CircuitBreaker | Complete |
| `core/code_reviewer.py` | 223 | CodeSelfReview | Complete |
| `core/cognitive_graph.py` | 303 | CognitiveGraph | Complete |
| `core/constraint_engine.py` | 257 | RuleResult, ConstraintResult, ConstraintEngine | Complete |
| `core/cross_agent_search.py` | 81 | CrossAgentSearch | Complete |
| `core/disk_guard.py` | 87 | DiskSpaceError | Complete |
| `core/domain_capsule.py` | 250 | DecisionMatch, LayerConfig, DomainCapsule | Complete |
| `core/domain_model_miner.py` | 291 | ColumnInfo, DocumentPattern, LayerScore +4 | Complete |
| `core/elastic_scaler.py` | 324 | HardwareProfile, TierConfig, ElasticScaler | Complete |
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
| `core/learning_engine.py` | 1144 | QualityScore, PromptExperiment, PromptWin +2 | Complete |
| `core/learning_ledger.py` | 140 | LedgerEntry, LearningLedger | Complete |
| `core/learning_task_queue.py` | 178 | LearningTaskQueue | Complete |
| `core/live_monitor.py` | 143 | EventCategory, MonitorEvent, LiveMonitor | Complete |
| `core/llm_provider.py` | 260 | LLMCircuitBreaker, LLMResponse, LLMProvider | Complete |
| `core/lora_readiness.py` | 109 | ReadinessCheck, ReadinessManifest, LoRAReadinessChecker | Complete |
| `core/math_solver.py` | 118 | MathSolver | Complete |
| `core/memory_engine.py` | 1345 | MemoryMatch, PreFilterResult, MemoryStore +1 | Complete |
| `core/memory_eviction.py` | 166 | MemoryEviction | Complete |
| `core/memory_overlay.py` | 327 | MemoryOverlay, OverlayRegistry, OverlayBranch +3 | Complete |
| `core/memory_proxy.py` | 167 | Role, WriteMode, MemoryWriteProxy | Complete |
| `core/meta_learning.py` | 605 | MetaLearningEngine, AgentOverlapDetector | Complete |
| `core/micro_model.py` | 1218 | PatternMatchEngine, ClassifierModel, LoRAModel +2 | Complete |
| `core/model_interface.py` | 142 | ModelResult, BaseModel | Complete |
| `core/mqtt_sensor_ingest.py` | 107 | SensorReading, MQTTSensorIngest | Complete |
| `core/night_enricher.py` | 1786 | EnrichmentCandidate, QualityVerdict, SourceMetrics +12 | Complete |
| `core/night_mode_controller.py` | 565 | NightModeController | Complete |
| `core/normalizer.py` | 360 |  | Complete |
| `core/observability.py` | 45 |  | Complete |
| `core/ops_agent.py` | 830 | ModelProfile, OllamaSnapshot, OpsDecision +1 | Complete |
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
| `core/safe_eval.py` | 118 | SafeEvalError | Complete |
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
git checkout a1c4152

# Count core modules (expect 40+):
find waggledance/core -name "*.py" -not -name "__init__.py" | wc -l

# Run tests:
pip install -r requirements.txt
pytest tests/ --collect-only -q | tail -1              # expect 5512+
```
