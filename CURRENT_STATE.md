# WaggleDance Swarm — Project State (auto-generated)

**Generated**: 2026-06-11T23:35:18+0300
**Commit**: `0fdb4530` on `fable-5/generate-state-port-status-20260611`
**Generator**: `python tools/generate_state.py`

> This file is auto-generated from actual code. Do not edit manually.
> Re-run `python tools/generate_state.py` after any major change.

## Summary

- **Hexagonal runtime** (`waggledance/`): 302 core modules, 99,450 lines
- **Legacy core** (`core/`): 86 modules, 28,043 lines
- **Tests**: 756 files, 11307 test functions
- **Licensing**: 664 BUSL-protected files, 276 Apache files

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
| `waggledance/core/api_distillation/knowledge_extractor.py` | 134 | ExtractedFact, ExtractedSolverSpec, ExtractedLesson | Complete |
| `waggledance/core/api_distillation/offline_replay_engine.py` | 90 |  | Complete |
| `waggledance/core/autonomy/action_gate.py` | 236 | GateVerdict, GateBatchReport | Complete |
| `waggledance/core/autonomy/attention_allocator.py` | 115 | AttentionWeight | Complete |
| `waggledance/core/autonomy/attention_budget.py` | 228 | AttentionAllocation, AttentionBudget | Complete |
| `waggledance/core/autonomy/background_scheduler.py` | 138 | DispatchReport | Complete |
| `waggledance/core/autonomy/budget_engine.py` | 210 | BudgetViolation, BudgetReport | Complete |
| `waggledance/core/autonomy/circuit_breaker.py` | 241 | BreakerEvent | Complete |
| `waggledance/core/autonomy/compatibility.py` | 225 | LegacyResult, AutonomyResult, CompatibilityLayer | Complete |
| `waggledance/core/autonomy/governor.py` | 239 | ActionRecommendation, TickReport | Complete |
| `waggledance/core/autonomy/kernel_state.py` | 302 | TickIdentity, BudgetEntry, CircuitBreakerSnapshot +1 | Complete |
| `waggledance/core/autonomy/lifecycle.py` | 213 | RuntimeState, RuntimeMode, HealthCheck +1 | Complete |
| `waggledance/core/autonomy/metrics.py` | 242 | MetricSample, AutonomyMetrics | Complete |
| `waggledance/core/autonomy/micro_learning_lane.py` | 110 | PriorityHint | Complete |
| `waggledance/core/autonomy/mission_queue.py` | 326 | Mission | Complete |
| `waggledance/core/autonomy/policy_core.py` | 251 | PolicyRule, HardRule, PolicyEvaluation +1 | Complete |
| `waggledance/core/autonomy/resource_kernel.py` | 511 | LoadLevel, ResourceTier, ResourceSnapshot +5 | Complete |
| `waggledance/core/autonomy/runtime.py` | 1546 | AutonomyRuntime | Complete |
| `waggledance/core/autonomy_growth/auto_promotion_engine.py` | 961 | PromotionRequest, PromotionOutcome, AutoPromotionReceiptEmissionError +1 | Complete |
| `waggledance/core/autonomy_growth/autogrowth_scheduler.py` | 521 | TickResult, SchedulerStats, BackgroundTickerStats +2 | Complete |
| `waggledance/core/autonomy_growth/autonomy_consult_adapter.py` | 88 |  | Complete |
| `waggledance/core/autonomy_growth/counterfactual_replay.py` | 433 | CounterfactualReplayError | Complete |
| `waggledance/core/autonomy_growth/family_features.py` | 113 |  | Complete |
| `waggledance/core/autonomy_growth/family_oracles.py` | 136 |  | Complete |
| `waggledance/core/autonomy_growth/gap_candidate.py` | 98 | GapVerdict, GapCandidate, GapMiningResult | Complete |
| `waggledance/core/autonomy_growth/gap_intake.py` | 210 | GapSignal, IntakeStats, RuntimeGapDetector | Complete |
| `waggledance/core/autonomy_growth/gap_mining.py` | 419 | GapMiningConfig | Complete |
| `waggledance/core/autonomy_growth/hot_path_cache.py` | 470 | WarmDispatchResult, HotPathCacheStats, BufferedSinkStats +5 | Complete |
| `waggledance/core/autonomy_growth/incremental_gap_replay.py` | 638 | BridgeRejectionError, ReplayCursor, ReplayLock +3 | Complete |
| `waggledance/core/autonomy_growth/low_risk_grower.py` | 249 | GapInput, GapOutcome, LowRiskGrower | Complete |
| `waggledance/core/autonomy_growth/low_risk_policy.py` | 42 |  | Complete |
| `waggledance/core/autonomy_growth/low_risk_seed_library.py` | 652 |  | Complete |
| `waggledance/core/autonomy_growth/mined_solver_runtime.py` | 522 | RuntimeArtifactCompilationError, RegistrationSummary | Complete |
| `waggledance/core/autonomy_growth/operator_feedback_amplifier.py` | 892 | OperatorFeedbackValidationError, OperatorFeedbackPolicy, OperatorFeedbackActionPlan +1 | Complete |
| `waggledance/core/autonomy_growth/runtime_gap_replay.py` | 584 | GapEventSchemaError, PersistedGapEvent, GapPersistResult +1 | Complete |
| `waggledance/core/autonomy_growth/runtime_hint_extractor.py` | 439 | HintExtractionResult | Complete |
| `waggledance/core/autonomy_growth/runtime_query_router.py` | 302 | RuntimeQuery, RuntimeRouteResult, RouterStats +1 | Complete |
| `waggledance/core/autonomy_growth/shadow_evaluator.py` | 143 | ShadowOutcome | Complete |
| `waggledance/core/autonomy_growth/solver_dispatcher.py` | 316 | DispatchQuery, DispatchResult, DispatcherStats +1 | Complete |
| `waggledance/core/autonomy_growth/solver_executor.py` | 242 | ExecutorError, UnsupportedFamilyError | Complete |
| `waggledance/core/autonomy_growth/upstream_structured_request_extractor.py` | 502 | UpstreamExtractionResult | Complete |
| `waggledance/core/autonomy_growth/validation_runner.py` | 118 | ValidationOutcome | Complete |
| `waggledance/core/bridge_event_hmac.py` | 224 | BridgeEventHmacError | Complete |
| `waggledance/core/bridge_event_schema.py` | 454 | BridgeEvent, BridgeEventValidationIssue, BridgeEventValidationResult | Complete |
| `waggledance/core/bridge_identity_registry.py` | 98 |  | Complete |
| `waggledance/core/bridge_llm/ab_harness.py` | 171 | ABResult, ABHarness | Complete |
| `waggledance/core/bridge_llm/budget.py` | 159 | BudgetExhausted, BudgetState, BudgetConfig +1 | Complete |
| `waggledance/core/bridge_llm/client.py` | 279 | BridgeLLMClient | Complete |
| `waggledance/core/bridge_llm/providers/anthropic.py` | 159 | AnthropicProvider | Complete |
| `waggledance/core/bridge_llm/providers/base.py` | 58 | ProviderError, ProviderPlugin | Complete |
| `waggledance/core/bridge_llm/providers/cache.py` | 73 | ExactCacheProvider | Complete |
| `waggledance/core/bridge_llm/providers/cloud_stub.py` | 24 | CloudStubProvider | Complete |
| `waggledance/core/bridge_llm/providers/heuristic.py` | 42 | HeuristicProvider | Complete |
| `waggledance/core/bridge_llm/providers/ollama.py` | 90 | OllamaProvider | Complete |
| `waggledance/core/bridge_llm/redactor.py` | 248 | RedactionResult, BridgeLLMRedactor, BridgeLLMRehydrator | Complete |
| `waggledance/core/bridge_llm/telemetry.py` | 74 | TelemetryLogger | Complete |
| `waggledance/core/bridge_llm/types.py` | 101 | FallbackLevel, CallBudget, LLMRequest +1 | Complete |
| `waggledance/core/builder_lane/builder_lane_router.py` | 76 | BuilderRoutingDecision | Complete |
| `waggledance/core/builder_lane/builder_request_pack.py` | 116 | BuilderRequest | Complete |
| `waggledance/core/builder_lane/builder_result_pack.py` | 114 | BuilderArtifact, BuilderResult | Complete |
| `waggledance/core/builder_lane/mentor_forge.py` | 83 | MentorPrompt | Complete |
| `waggledance/core/builder_lane/repair_forge.py` | 71 | RepairContext | Complete |
| `waggledance/core/builder_lane/session_forge.py` | 52 | ForgePlan | Complete |
| `waggledance/core/builder_lane/worktree_allocator.py` | 133 | WorktreeAllocation, InvocationLogEntry | Complete |
| `waggledance/core/capabilities/aliasing.py` | 176 | AgentAlias, AliasRegistry | Complete |
| `waggledance/core/capabilities/registry.py` | 489 | CapabilityRegistry | Complete |
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
| `waggledance/core/domain/memory_record.py` | 18 | MemoryRecord | Complete |
| `waggledance/core/domain/task.py` | 26 | TaskRequest, TaskRoute | Complete |
| `waggledance/core/domain/trust_score.py` | 25 | TrustSignals, AgentTrust | Complete |
| `waggledance/core/dreaming/collapse.py` | 412 | CollapsedProposal, CollapseReport | Complete |
| `waggledance/core/dreaming/curriculum.py` | 485 | DreamableItem, DreamNight, DreamCurriculum | Complete |
| `waggledance/core/dreaming/meta_proposal.py` | 368 | DreamMetaProposal | Complete |
| `waggledance/core/dreaming/replay.py` | 368 | ReplayCase, CaseEvaluation, ReplayReport | Complete |
| `waggledance/core/dreaming/request_pack.py` | 292 | DreamRequestPack | Complete |
| `waggledance/core/dreaming/shadow_graph.py` | 220 | ShadowNode, ShadowEdge, ShadowGraph +1 | Complete |
| `waggledance/core/goals/goal_engine.py` | 276 | GoalEngine | Complete |
| `waggledance/core/goals/mission_store.py` | 99 | MissionStore | Complete |
| `waggledance/core/goals/motives.py` | 162 | MotiveConfig, ConflictResult, MotiveRegistry | Complete |
| `waggledance/core/hex_cell_topology.py` | 248 | CellAssignment, HexCellTopology | Complete |
| `waggledance/core/hex_topology/canary_mirror.py` | 227 | CanaryMirrorError | Complete |
| `waggledance/core/hex_topology/cell_local_state.py` | 51 | CellLocalState | Complete |
| `waggledance/core/hex_topology/cell_message_contract.py` | 78 | CellMessage | Complete |
| `waggledance/core/hex_topology/cell_runtime.py` | 112 | CellRuntime | Complete |
| `waggledance/core/hex_topology/express_lane.py` | 535 | HexExpressLaneError, ExpressLaneEdge, ExpressLaneRequest | Complete |
| `waggledance/core/hex_topology/parent_child_relations.py` | 61 |  | Complete |
| `waggledance/core/hex_topology/ring_messaging.py` | 152 | RingDelivery | Complete |
| `waggledance/core/hex_topology/subdivision_operator.py` | 125 | SubdivisionPlan | Complete |
| `waggledance/core/idle_consensus_charter.py` | 463 | IdleAutonomyCharter, GateDecision, _DiffFileSection | Complete |
| `waggledance/core/idle_daily_summary.py` | 330 | AutoMergeEntry, PendingDraftEntry, DailySummary +2 | Complete |
| `waggledance/core/idle_protocol.py` | 254 |  | Complete |
| `waggledance/core/idle_protocol_session.py` | 192 |  | Complete |
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
| `waggledance/core/leak_policy.py` | 202 |  | Complete |
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
| `waggledance/core/magma/adversarial_corpus_eval.py` | 219 | AdversarialCorpusEvalError | Complete |
| `waggledance/core/magma/adversarial_gate.py` | 260 | AdversarialGateResult | Complete |
| `waggledance/core/magma/audit_projector.py` | 235 | AuditEntry, AuditProjector | Projection (read-only) |
| `waggledance/core/magma/canonical.py` | 29 |  | Complete |
| `waggledance/core/magma/confidence_decay.py` | 52 |  | Complete |
| `waggledance/core/magma/consensus_receipt.py` | 229 |  | Complete |
| `waggledance/core/magma/demo_policy.py` | 202 |  | Complete |
| `waggledance/core/magma/evaluation_result.py` | 165 |  | Complete |
| `waggledance/core/magma/event_log_adapter.py` | 200 | EventLogEntry, EventLogAdapter | Complete |
| `waggledance/core/magma/provenance.py` | 201 | ProvenanceRecord, ProvenanceAdapter | Complete |
| `waggledance/core/magma/rco_decision_artifact.py` | 88 |  | Complete |
| `waggledance/core/magma/receipt.py` | 120 |  | Complete |
| `waggledance/core/magma/receipt_bundle.py` | 100 | ReceiptBundleEntry | Complete |
| `waggledance/core/magma/reflective_workspace.py` | 425 | Workspace | Complete |
| `waggledance/core/magma/replay_engine.py` | 336 | MissionReplayEntry, MissionReplay, ReplayAdapter | Complete |
| `waggledance/core/magma/runtime_summary_receipt.py` | 422 |  | Complete |
| `waggledance/core/magma/schema_validation.py` | 32 |  | Complete |
| `waggledance/core/magma/self_model.py` | 643 | CalibrationEvidence, ScorecardDimension, BlindSpot +9 | Complete |
| `waggledance/core/magma/share_manifest.py` | 1619 |  | Complete |
| `waggledance/core/magma/trust_adapter.py` | 257 | TrustRecord, TrustAdapter | Complete |
| `waggledance/core/magma/vector_events.py` | 404 | VectorEvent | Complete |
| `waggledance/core/memory/working_memory.py` | 204 | MemorySlot, WorkingMemory | Complete |
| `waggledance/core/memory_palace/projection.py` | 1280 | MemoryPalaceProjectionError, PalaceNode, MemoryPlacement +2 | Projection (read-only) |
| `waggledance/core/memory_tiers/access_pattern_tracker.py` | 47 | AccessRecord, AccessPatternTracker | Complete |
| `waggledance/core/memory_tiers/cold_tier.py` | 25 | ColdTier | Complete |
| `waggledance/core/memory_tiers/glacier_tier.py` | 23 | GlacierTier | Complete |
| `waggledance/core/memory_tiers/hot_tier.py` | 24 | HotTier | Complete |
| `waggledance/core/memory_tiers/invariant_extractor.py` | 90 | ExtractedInvariant, InvariantStore | Complete |
| `waggledance/core/memory_tiers/pinning_engine.py` | 73 | PinRecord, PinningEngine | Complete |
| `waggledance/core/memory_tiers/tier_manager.py` | 238 | TierAssignment, TierViolation, TierManager | Complete |
| `waggledance/core/memory_tiers/warm_tier.py` | 23 | WarmTier | Complete |
| `waggledance/core/meta/history.py` | 160 | HistoryEntry | Complete |
| `waggledance/core/meta/inputs.py` | 170 |  | Complete |
| `waggledance/core/meta/meta_learner.py` | 592 | EvidenceItem, MetaProposal, SynthesisResult | Complete |
| `waggledance/core/meta/review_bundle.py` | 292 |  | Complete |
| `waggledance/core/orchestration/lifecycle.py` | 52 | AgentLifecycleManager | Complete |
| `waggledance/core/orchestration/orchestrator.py` | 426 | Orchestrator | Complete |
| `waggledance/core/orchestration/prompt_builder.py` | 140 |  | Complete |
| `waggledance/core/orchestration/round_table.py` | 191 | ConsensusResult, RoundTableEngine | Complete |
| `waggledance/core/orchestration/routing_policy.py` | 162 | RoutingFeatures | Complete |
| `waggledance/core/orchestration/scheduler.py` | 179 | SchedulerState, Scheduler | Complete |
| `waggledance/core/pdam_close_solver.py` | 366 | LogbookEntry, ToolState, MesComment +2 | Complete |
| `waggledance/core/planning/planner.py` | 174 | Planner | Complete |
| `waggledance/core/policies/confidence_policy.py` | 22 |  | Complete |
| `waggledance/core/policies/escalation_policy.py` | 39 | EscalationPolicy | Complete |
| `waggledance/core/policies/fallback_policy.py` | 62 | FallbackChain | Complete |
| `waggledance/core/policy/approvals.py` | 172 | ApprovalRequest, ApprovalManager | Complete |
| `waggledance/core/policy/constitution.py` | 269 | ConstitutionRule, ProfileThresholds, Constitution | Complete |
| `waggledance/core/policy/policy_engine.py` | 317 | PolicyDecision, PolicyEngine | Complete |
| `waggledance/core/policy/risk_scoring.py` | 129 | RiskScorer | Complete |
| `waggledance/core/policy/safety_cases.py` | 214 | SafetyEvidence, SafetyCase, SafetyCaseBuilder | Complete |
| `waggledance/core/ports/config_port.py` | 13 | ConfigPort | Interface (Protocol) |
| `waggledance/core/ports/event_bus_port.py` | 17 | EventBusPort | Interface (Protocol) |
| `waggledance/core/ports/hot_cache_port.py` | 15 | HotCachePort | Interface (Protocol) |
| `waggledance/core/ports/llm_port.py` | 19 | LLMPort | Interface (Protocol) |
| `waggledance/core/ports/memory_repository_port.py` | 26 | MemoryRepositoryPort | Interface (Protocol) |
| `waggledance/core/ports/sensor_port.py` | 11 | SensorPort | Interface (Protocol) |
| `waggledance/core/ports/trust_store_port.py` | 34 | TrustStorePort | Interface (Protocol) |
| `waggledance/core/ports/vector_store_port.py` | 25 | VectorStorePort | Interface (Protocol) |
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
| `waggledance/core/providers/builder_job_queue.py` | 105 | BuilderJobSubmission, BuilderJobQueue | Complete |
| `waggledance/core/providers/builder_lane_router.py` | 61 | RouteWithJob, BuilderLaneRouter | Complete |
| `waggledance/core/providers/claude_code_builder.py` | 240 | ClaudeCodeBuilderUnavailable, _LaunchSpec, ClaudeCodeBuilder | Complete |
| `waggledance/core/providers/mentor_forge.py` | 130 | MentorAdvisoryPayload, MentorForge | Complete |
| `waggledance/core/providers/provider_contracts.py` | 260 | ProviderContractError, ProviderRequest, ProviderResponse | Complete |
| `waggledance/core/providers/provider_plane.py` | 277 | ProviderPlaneError, ProviderAdapter, ProviderDispatchResult +3 | Interface (Protocol) |
| `waggledance/core/providers/provider_registry.py` | 186 | ProviderConfig, ProviderPlaneRegistry | Complete |
| `waggledance/core/providers/repair_forge.py` | 84 | RepairForge | Complete |
| `waggledance/core/reasoning/anomaly_engine.py` | 216 | AnomalyResult, AnomalyEngine | Complete |
| `waggledance/core/reasoning/bee_domain_engine.py` | 346 | ColonyHealthResult, SwarmRiskResult, HoneyYieldResult +1 | Complete |
| `waggledance/core/reasoning/causal_engine.py` | 231 | CausalChain, ImpactEstimate, CausalEngine | Complete |
| `waggledance/core/reasoning/hybrid_observer.py` | 201 | HybridCandidateTrace, HybridObserver | Complete |
| `waggledance/core/reasoning/hybrid_router.py` | 163 |  | Complete |
| `waggledance/core/reasoning/optimization_engine.py` | 234 | OptimizationResult, OptimizationEngine | Complete |
| `waggledance/core/reasoning/question_frame.py` | 207 | Comparator, Negation, QuestionFrame | Complete |
| `waggledance/core/reasoning/route_engine.py` | 234 | RouteMetrics, RouteDecision, RouteEngine | Complete |
| `waggledance/core/reasoning/seasonal_engine.py` | 159 | SeasonalEngine | Complete |
| `waggledance/core/reasoning/solver_router.py` | 617 | AutonomyConsultOutcome, SolverRouteResult, SolverRouter | Complete |
| `waggledance/core/reasoning/stats_engine.py` | 205 | StatsResult, StatsEngine | Complete |
| `waggledance/core/reasoning/thermal_solver.py` | 256 | ThermalResult, ThermalSolver | Complete |
| `waggledance/core/reasoning/verifier.py` | 288 | VerifierResult, Verifier | Complete |
| `waggledance/core/solver_synthesis/bulk_rule_extractor.py` | 159 | FamilyMatch | Complete |
| `waggledance/core/solver_synthesis/cold_shadow_throttler.py` | 178 | ThrottleVerdict, _LaneState, ColdShadowThrottler | Complete |
| `waggledance/core/solver_synthesis/declarative_solver_spec.py` | 123 | SolverSpec, SpecValidationError | Complete |
| `waggledance/core/solver_synthesis/deterministic_solver_compiler.py` | 210 | CompiledSolver | Complete |
| `waggledance/core/solver_synthesis/gap_to_solver_spec.py` | 65 | GapRoutingDecision | Complete |
| `waggledance/core/solver_synthesis/hex_cell_competition.py` | 1538 | CandidateCompetitionScore, HexCellCompetitionResult, HexCellPromotionAcceptance +2 | Complete |
| `waggledance/core/solver_synthesis/llm_solver_generator.py` | 191 | GenerationRequest, GenerationResult | Complete |
| `waggledance/core/solver_synthesis/solver_bootstrap.py` | 305 | BootstrapDecision, SolverBootstrap | Complete |
| `waggledance/core/solver_synthesis/solver_candidate_store.py` | 221 | SolverCandidate, SolverCandidateStore | Complete |
| `waggledance/core/solver_synthesis/solver_family_registry.py` | 166 | SolverFamily, SolverFamilyRegistry | Complete |
| `waggledance/core/solver_synthesis/solver_quarantine.py` | 163 | QuotaState, AdmissionDecision | Complete |
| `waggledance/core/solver_synthesis/validators.py` | 216 | GateResult, CountedGateResult, ShadowEvalResult +1 | Complete |
| `waggledance/core/specialist_models/meta_optimizer.py` | 166 | CanaryRecord, HyperparameterProposal, MetaOptimizerState | Complete |
| `waggledance/core/specialist_models/model_store.py` | 236 | ModelStatus, ModelVersion, ModelStore | Complete |
| `waggledance/core/specialist_models/specialist_trainer.py` | 954 | TrainingResult, SpecialistTrainer | Complete |
| `waggledance/core/storage/control_plane.py` | 2774 | ControlPlaneError, SolverFamilyRecord, SolverRecord +22 | Complete |
| `waggledance/core/storage/control_plane_schema.py` | 605 |  | Complete |
| `waggledance/core/storage/path_resolver.py` | 227 | PathResolverError, LogicalPathKind, ResolvedPath +1 | Complete |
| `waggledance/core/storage/registry_queries.py` | 196 | FamilyRollup, CapabilityRollup, RegistryQueries | Complete |
| `waggledance/core/storage/retention_policy.py` | 204 | RetentionRule, PruneReport, RetentionPolicy | Complete |
| `waggledance/core/v3_13_0/acct01_unpaid_bill_reconciler.py` | 510 | Acct01UnpaidBillReconcilerError, _Invoice, _Transaction +2 | Complete |
| `waggledance/core/v3_13_0/air01_air_quality_advisor.py` | 510 | AirQualityThresholds, Air01AirQualityAdvisory, _Reading +1 | Complete |
| `waggledance/core/v3_13_0/air01_digheran_adapter.py` | 223 | Air01DigheranAdapterError | Complete |
| `waggledance/core/v3_13_0/air01_sensor_http_transport.py` | 288 | Air01SensorHttpResponse, Air01SensorHttpTransportError | Complete |
| `waggledance/core/v3_13_0/anti_pattern_catalog.py` | 618 | InvariantViolation, CredentialPatternHit | Complete |
| `waggledance/core/v3_13_0/auto_fix_loop.py` | 520 | RepairOutcome, RepairIntent, RepairResult +5 | Complete |
| `waggledance/core/v3_13_0/behavior_capture.py` | 448 | SensitiveClass, ToolInvocation, CapturedBehaviorRecord +3 | Complete |
| `waggledance/core/v3_13_0/credential_vault.py` | 656 | CredentialRef, CredentialMaterial, VaultMetadata +8 | Interface (Protocol) |
| `waggledance/core/v3_13_0/defaults.py` | 291 |  | Complete |
| `waggledance/core/v3_13_0/divergence_analyzer.py` | 865 | ComparisonResult, DivergenceCategory, DiffClass +6 | Complete |
| `waggledance/core/v3_13_0/doc_ingest.py` | 347 | DocIngestError, DocIngestProposal | Complete |
| `waggledance/core/v3_13_0/email01_inbox_priority_classifier.py` | 520 | Email01InboxPriorityClassifierError, _WatchRule, _Message +3 | Complete |
| `waggledance/core/v3_13_0/email02_vendor_email_indexer.py` | 482 | Email02VendorEmailIndexerError, _Vendor, _Message +2 | Complete |
| `waggledance/core/v3_13_0/eng01_advisory_card.py` | 200 | Eng01AdvisoryCardError | Complete |
| `waggledance/core/v3_13_0/eng01_price_feed_adapter.py` | 145 | Eng01PriceFeedAdapterError | Complete |
| `waggledance/core/v3_13_0/eng01_price_feed_http_transport.py` | 268 | Eng01PriceFeedHttpResponse, Eng01PriceFeedHttpTransportError | Complete |
| `waggledance/core/v3_13_0/eng01_price_feed_response_parser.py` | 158 | Eng01PriceFeedResponseParserError | Complete |
| `waggledance/core/v3_13_0/eng01_spot_electricity.py` | 271 | Eng01SpotElectricityError, Eng01FirstSliceResult, _PricePoint | Complete |
| `waggledance/core/v3_13_0/eng06_advisory_card.py` | 197 | Eng06AdvisoryCardError | Complete |
| `waggledance/core/v3_13_0/eng06_burn_log_adapter.py` | 193 | Eng06BurnLogAdapterError | Complete |
| `waggledance/core/v3_13_0/eng06_fireplace_advisor.py` | 324 | Eng06FireplaceSummary, _BurnLogDay, _InvalidLogFeed | Complete |
| `waggledance/core/v3_13_0/fin10_receipt_classifier.py` | 212 | Fin10ReceiptClassifierError, Fin10ReceiptClassificationResult | Complete |
| `waggledance/core/v3_13_0/pdf01_invoice_field_extractor.py` | 424 | Pdf01InvoiceFieldExtractorError, Pdf01InvoiceExtractionResult | Complete |
| `waggledance/core/v3_13_0/secret_markers.py` | 68 |  | Complete |
| `waggledance/core/v3_13_0/shadow_runner.py` | 383 | ShadowRunState, ShadowAbortReason, ShadowRunInput +8 | Complete |
| `waggledance/core/v3_13_0/solver_provenance.py` | 1160 | SigningRole, ActivationState, RevocationActor +5 | Complete |
| `waggledance/core/v3_13_0/solver_registry.py` | 355 | SolverRegistryError, SolverManifest | Complete |
| `waggledance/core/v3_13_0/solver_synthesizer.py` | 478 | SolverSynthesizerError, SolverTarget, SynthesizedSolverCandidate +3 | Complete |
| `waggledance/core/v3_13_0/sqlite_read_transport.py` | 252 | SqliteReadResult, SqliteReadTransportError | Complete |
| `waggledance/core/v3_13_0/write_rco_gate.py` | 1357 | WriteRiskClass, AuditEventType, StopCondition +10 | Complete |
| `waggledance/core/vector_identity/identity_anchor.py` | 127 | AnchorValidation | Complete |
| `waggledance/core/vector_identity/ingestion_dedup.py` | 143 | DedupResult | Complete |
| `waggledance/core/vector_identity/vector_provenance_graph.py` | 187 | LineageEdge, VectorNode, VectorProvenanceGraph | Complete |
| `waggledance/core/work_queue.py` | 581 | WorkQueueError, Claim, ReleaseRecord +1 | Complete |
| `waggledance/core/world/baseline_store.py` | 168 | Baseline, BaselineStore | Complete |
| `waggledance/core/world/entity_registry.py` | 103 | Entity, EntityRegistry | Complete |
| `waggledance/core/world/epistemic_uncertainty.py` | 362 | BaselineProvider, EntityProvider, GoalProvider +2 | Interface (Protocol) |
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
| `core/agent_group_call.py` | 442 | GroupAgentSlot, GroupAnswer, GroupCallResult +1 | Complete |
| `core/agent_levels.py` | 347 | AgentLevel, AgentStats, AgentLevelManager | Complete |
| `core/agent_rollback.py` | 89 | AgentRollback | Complete |
| `core/audit_log.py` | 176 | AuditLog | Complete |
| `core/auto_install.py` | 193 |  | Complete |
| `core/canary_promoter.py` | 157 | CanaryResult, CanaryPromoter | Complete |
| `core/causal_replay_api.py` | 55 | ReplayResult, CausalReplayService | Complete |
| `core/chat_delegation.py` | 214 | AgentDelegator | Complete |
| `core/chat_handler.py` | 384 | ChatHandler | Complete |
| `core/chat_history.py` | 199 | ChatHistory | Complete |
| `core/chat_preprocessing.py` | 210 | PreprocessResult, ChatPreprocessor | Complete |
| `core/chat_router.py` | 112 | ChatResult, ChatRouter | Complete |
| `core/chat_routing_engine.py` | 537 | ChatRoutingEngine | Complete |
| `core/chat_telemetry.py` | 96 | ChatTelemetry | Complete |
| `core/chromadb_adapter.py` | 187 | StoreAdapter, ChromaDBAdapter | Interface (Protocol) |
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
| `core/faiss_store.py` | 259 | SearchResult, FaissCollection, FaissRegistry | Complete |
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
| `core/math_solver.py` | 120 | MathSolver | Complete |
| `core/memory_engine.py` | 1345 | MemoryMatch, PreFilterResult, MemoryStore +1 | Complete |
| `core/memory_eviction.py` | 166 | MemoryEviction | Complete |
| `core/memory_overlay.py` | 327 | MemoryOverlay, OverlayRegistry, OverlayBranch +3 | Complete |
| `core/memory_proxy.py` | 167 | Role, WriteMode, MemoryWriteProxy | Complete |
| `core/meta_learning.py` | 605 | MetaLearningEngine, AgentOverlapDetector | Complete |
| `core/micro_model.py` | 1222 | PatternMatchEngine, ClassifierModel, LoRAModel +2 | Complete |
| `core/model_interface.py` | 142 | ModelResult, BaseModel | Complete |
| `core/mqtt_sensor_ingest.py` | 107 | SensorReading, MQTTSensorIngest | Complete |
| `core/night_enricher.py` | 1786 | EnrichmentCandidate, QualityVerdict, SourceMetrics +12 | Complete |
| `core/night_mode_controller.py` | 565 | NightModeController | Complete |
| `core/normalizer.py` | 407 |  | Complete |
| `core/observability.py` | 45 |  | Complete |
| `core/ops_agent.py` | 830 | ModelProfile, OllamaSnapshot, OpsDecision +1 | Complete |
| `core/opus_mt_adapter.py` | 91 | OpusMTAdapter | Complete |
| `core/prompt_experiment_status.py` | 51 | ExperimentSummary, ExperimentStatusFormatter | Complete |
| `core/provenance.py` | 130 | ProvenanceTracker | Complete |
| `core/rag_verifier.py` | 197 | Claim, VerificationResult, RAGVerifier | Complete |
| `core/replay_engine.py` | 271 | ReplayEngine | Complete |
| `core/replay_store.py` | 88 | ReplayStore | Complete |
| `core/resource_guard.py` | 122 | ResourceState, ResourceGuard | Complete |
| `core/round_table_controller.py` | 545 | RoundTableController | Complete |
| `core/route_explainability.py` | 87 | RouteExplanation | Complete |
| `core/route_telemetry.py` | 121 | RouteStats, RouteTelemetry | Complete |
| `core/safe_eval.py` | 120 | SafeEvalError | Complete |
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
| `core/translation_proxy.py` | 1693 | VoikkoEngine, OpusMTFallback, TranslationProxy +2 | Complete |
| `core/trust_engine.py` | 310 | TrustSignal, AgentReputation, TrustEngine | Complete |
| `core/web_learner.py` | 263 | WebLearningAgent | Complete |
| `core/whisper_protocol.py` | 447 | Whisper, WhisperProtocol | Complete |
| `core/yaml_bridge.py` | 820 | YAMLBridge | Complete |

## Verification Commands

```bash
# Clone and verify:
git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git
cd waggledance-swarm
git checkout 0fdb4530

# Count core modules (expect 40+):
find waggledance/core -name "*.py" -not -name "__init__.py" | wc -l

# Run tests:
pip install -r requirements.txt
pytest tests/ --collect-only -q | tail -1              # expect 11307+
```
