"""Dependency injection container -- wires everything together."""

import logging
from functools import cached_property
from pathlib import Path

log = logging.getLogger(__name__)


class Container:
    """One Container instance = one running WaggleDance system."""

    def __init__(self, settings, stub: bool = False):
        self._settings = settings
        self._stub = stub

    # --- Adapters ---

    @cached_property
    def llm(self):
        """LLMPort implementation."""
        if self._stub:
            from waggledance.adapters.llm.stub_llm_adapter import StubLLMAdapter
            return StubLLMAdapter()
        from waggledance.adapters.llm.ollama_adapter import OllamaAdapter
        return OllamaAdapter(
            base_url=self._settings.ollama_host,
            default_model=self._settings.chat_model,
            timeout_seconds=self._settings.ollama_timeout_seconds,
        )

    @cached_property
    def gemma_router(self):
        """GemmaProfileRouter — optional dual-tier Gemma 4 model routing."""
        from waggledance.application.services.gemma_profile_router import GemmaProfileRouter
        return GemmaProfileRouter(settings=self._settings, default_llm=self.llm)

    @cached_property
    def vector_store(self):
        """VectorStorePort implementation."""
        if self._stub:
            from waggledance.adapters.memory.in_memory_vector_store import InMemoryVectorStore
            return InMemoryVectorStore()
        from waggledance.adapters.memory.chroma_vector_store import ChromaVectorStore
        return ChromaVectorStore(
            persist_directory=self._settings.chroma_dir,
            embedding_model=self._settings.embed_model,
        )

    @cached_property
    def memory_repository(self):
        """MemoryRepositoryPort. Stub -> InMemoryRepository. Non-stub -> ChromaMemoryRepository."""
        if self._stub:
            from waggledance.adapters.memory.in_memory_repository import InMemoryRepository
            return InMemoryRepository()
        from waggledance.adapters.memory.chroma_memory_repository import ChromaMemoryRepository
        return ChromaMemoryRepository(
            vector_store=self.vector_store,
            collection="waggle_memory",
        )

    @cached_property
    def trust_store(self):
        """TrustStorePort -- stub=InMemory, production=SQLite with InMemory fallback.

        WARNING: If SQLite fails, falls back to InMemory (data lost on restart).
        Check logs for ERROR level.
        """
        if self._stub:
            from waggledance.adapters.trust.in_memory_trust_store import InMemoryTrustStore
            return InMemoryTrustStore()
        try:
            import os
            from waggledance.adapters.trust.sqlite_trust_store import SQLiteTrustStore
            db_dir = os.path.dirname(self._settings.db_path) or "."
            return SQLiteTrustStore(db_path=os.path.join(db_dir, "trust_store.db"))
        except Exception as exc:
            import logging
            log = logging.getLogger(__name__)
            log.error(
                "SQLiteTrustStore failed — trust data will NOT persist across restarts: %s", exc)
            from waggledance.adapters.trust.in_memory_trust_store import InMemoryTrustStore
            store = InMemoryTrustStore()
            store._fallback_active = True
            return store

    @cached_property
    def shared_memory(self):
        """SQLiteSharedMemory adapter."""
        from waggledance.adapters.memory.sqlite_shared_memory import SQLiteSharedMemory
        return SQLiteSharedMemory(db_path=self._settings.db_path)

    @cached_property
    def hot_cache(self):
        """HotCachePort implementation."""
        from waggledance.adapters.memory.hot_cache import HotCache
        return HotCache(max_size=self._settings.hot_cache_size)

    @cached_property
    def config(self):
        """ConfigPort -- settings object itself implements get/get_profile/get_hardware_tier."""
        return self._settings

    @cached_property
    def event_bus(self):
        """EventBusPort implementation."""
        from waggledance.bootstrap.event_bus import InMemoryEventBus
        return InMemoryEventBus()

    # --- Core (lazy imports -- Agent 1 may still be running) ---

    @cached_property
    def scheduler(self):
        """Scheduler from core orchestration."""
        from waggledance.core.orchestration.scheduler import Scheduler
        return Scheduler(config=self.config)

    def _load_agents(self) -> list:
        """Load agent definitions from YAML files for the current profile.

        Synchronous — called once during orchestrator construction.
        Returns activated AgentDefinition list (empty on error).
        """
        try:
            import yaml
            from waggledance.core.domain.agent import AgentDefinition

            agents_dir = Path("agents")
            if not agents_dir.exists():
                log.warning("Agents directory not found: %s", agents_dir)
                return []

            profile = self._settings.get_profile().upper()
            agents = []
            for yaml_file in sorted(agents_dir.rglob("*.yaml")):
                try:
                    with open(yaml_file, encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                    if not data or not isinstance(data, dict):
                        continue
                    header = data.get("header", {})
                    if not header.get("agent_id"):
                        continue
                    profiles = [p.upper() for p in data.get("profiles", ["ALL"])]
                    if profile not in profiles and "ALL" not in profiles:
                        continue
                    agent = AgentDefinition(
                        id=header["agent_id"],
                        name=header.get("agent_name", header["agent_id"]),
                        domain=header.get("domain", "general"),
                        tags=data.get("tags", []),
                        skills=list(
                            data.get("DECISION_METRICS_AND_THRESHOLDS", {}).keys()
                        ),
                        trust_level=0,
                        specialization_score=0.0,
                        active=True,
                        profile=profiles[0] if profiles else "ALL",
                    )
                    agents.append(agent)
                except Exception as e:
                    log.warning("Failed to load agent %s: %s", yaml_file, e)

            log.info("Loaded %d agents for profile %s", len(agents), profile)
            return agents
        except Exception as e:
            log.warning("Agent loading failed, orchestrator will run with 0 agents: %s", e)
            return []

    @cached_property
    def orchestrator(self):
        """Orchestrator from core orchestration."""
        from waggledance.core.orchestration.orchestrator import Orchestrator
        from waggledance.core.orchestration.round_table import RoundTableEngine
        rt = RoundTableEngine(
            llm=self.llm, event_bus=self.event_bus,
            parallel_dispatcher=self.parallel_dispatcher,
        )
        return Orchestrator(
            scheduler=self.scheduler,
            round_table=rt,
            memory=self.memory_repository,
            vector_store=self.vector_store,
            llm=self.llm,
            trust_store=self.trust_store,
            event_bus=self.event_bus,
            config=self.config,
            agents=self._load_agents(),
            parallel_dispatcher=self.parallel_dispatcher,
        )

    # --- Application Services (lazy imports) ---

    @cached_property
    def memory_service(self):
        """MemoryService from application layer."""
        from waggledance.application.services.memory_service import MemoryService
        return MemoryService(
            vector_store=self.vector_store,
            memory=self.memory_repository,
            event_bus=self.event_bus,
            hybrid_retrieval=self.hybrid_retrieval,
        )

    @cached_property
    def chat_service(self):
        """ChatService from application layer.

        Shares case_builder/case_store/verifier_store from AutonomyRuntime
        so chat traffic feeds into the learning funnel.
        """
        from waggledance.application.services.chat_service import ChatService
        from waggledance.core.orchestration.routing_policy import select_route

        # Get learning stores from autonomy runtime (shared instances)
        rt = self.autonomy_service._runtime
        return ChatService(
            orchestrator=self.orchestrator,
            memory_service=self.memory_service,
            hot_cache=self.hot_cache,
            routing_policy_fn=select_route,
            config=self.config,
            case_builder=rt.case_builder,
            case_store=rt.case_store,
            verifier_store=rt.verifier_store,
            hybrid_retrieval=self.hybrid_retrieval,
            hex_neighbor_assist=self.hex_neighbor_assist,
        )

    @cached_property
    def learning_service(self):
        """LearningService from application layer."""
        from waggledance.application.services.learning_service import LearningService
        return LearningService(
            orchestrator=self.orchestrator,
            memory_service=self.memory_service,
            llm=self.llm,
            event_bus=self.event_bus,
            stall_threshold=self._settings.night_stall_threshold,
        )

    @cached_property
    def readiness_service(self):
        """ReadinessService from application layer."""
        from waggledance.application.services.readiness_service import ReadinessService
        return ReadinessService(
            orchestrator=self.orchestrator,
            vector_store=self.vector_store,
            llm=self.llm,
        )

    @cached_property
    def night_pipeline(self):
        """NightLearningPipeline from core learning."""
        from waggledance.core.learning.night_learning_pipeline import NightLearningPipeline
        return NightLearningPipeline(profile=self._settings.get_profile())

    # --- Infrastructure (HW detection, throttle, OOM guard) ---

    @cached_property
    def elastic_scaler(self):
        """ElasticScaler — single source of truth for hardware detection."""
        from core.elastic_scaler import ElasticScaler
        scaler = ElasticScaler()
        scaler.detect()
        return scaler

    @cached_property
    def adaptive_throttle(self):
        """AdaptiveThrottle — dynamic load management."""
        from core.adaptive_throttle import AdaptiveThrottle
        return AdaptiveThrottle()

    @cached_property
    def resource_guard(self):
        """ResourceGuard — OOM protection."""
        from core.resource_guard import ResourceGuard
        return ResourceGuard()

    @cached_property
    def autonomy_service(self):
        """AutonomyService — wires runtime mode from settings."""
        from waggledance.application.services.autonomy_service import AutonomyService
        from waggledance.core.autonomy.compatibility import CompatibilityLayer
        from waggledance.core.autonomy.lifecycle import AutonomyLifecycle
        from waggledance.core.autonomy.resource_kernel import ResourceKernel
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        profile = self._settings.get_profile()

        # Tier: use ElasticScaler as single source of truth when "auto",
        # otherwise use the explicitly configured tier.
        tier_setting = self._settings.get_hardware_tier()
        if tier_setting == "auto":
            tier = self.elastic_scaler.tier.tier
        else:
            tier = tier_setting

        resource_kernel = ResourceKernel(
            tier=tier,
            elastic_scaler=self.elastic_scaler,
            adaptive_throttle=self.adaptive_throttle,
        )
        resource_kernel.resource_guard = self.resource_guard

        runtime = AutonomyRuntime(profile=profile, resource_kernel=resource_kernel)
        lifecycle = AutonomyLifecycle(
            primary=self._settings.runtime_primary,
            compatibility_mode=self._settings.compatibility_mode,
            profile=profile,
        )
        compatibility = CompatibilityLayer(
            runtime=runtime,
            compatibility_mode=self._settings.compatibility_mode,
        )

        return AutonomyService(
            runtime=runtime,
            lifecycle=lifecycle,
            resource_kernel=resource_kernel,
            compatibility=compatibility,
            profile=profile,
            night_pipeline=self.night_pipeline,
        )

    @cached_property
    def faiss_registry(self):
        """FaissRegistry — named FAISS collections for cell-local retrieval.

        Returns None if faiss-cpu is not installed (optional dependency).
        HybridRetrievalService treats None as "disabled" and falls back to
        ChromaDB-only retrieval.
        """
        try:
            from core.faiss_store import FaissRegistry
        except ImportError:
            return None
        return FaissRegistry()

    @cached_property
    def hex_cell_topology(self):
        """HexCellTopology — logical cell assignment and neighbor mapping."""
        from waggledance.core.hex_cell_topology import HexCellTopology
        return HexCellTopology()

    @cached_property
    def hybrid_retrieval(self):
        """HybridRetrievalService — cell-local FAISS + global ChromaDB orchestration.

        Feature-flagged via hybrid_retrieval.enabled in settings.yaml.
        """
        from waggledance.application.services.hybrid_retrieval_service import (
            HybridRetrievalService,
        )

        enabled = bool(self._settings.get("hybrid_retrieval.enabled", False))
        ring2 = bool(self._settings.get("hybrid_retrieval.ring2_enabled", False))
        mode = str(self._settings.get("hybrid_retrieval.mode", "shadow"))
        min_score = float(self._settings.get("hybrid_retrieval.min_score", 0.35))

        # Embedding function: reuse Ollama embed via vector_store's _embed_text if available
        embed_fn = None
        try:
            vs = self.vector_store
            if hasattr(vs, "_embed_text"):
                import numpy as np

                def _embed(text: str):
                    raw = vs._embed_text(text, prefix="search_query: ")
                    return np.array(raw, dtype=np.float32) if raw else None

                embed_fn = _embed
        except Exception:
            pass

        return HybridRetrievalService(
            faiss_registry=self.faiss_registry,
            topology=self.hex_cell_topology,
            vector_store=self.vector_store,
            embed_fn=embed_fn,
            enabled=enabled,
            ring2_enabled=ring2,
            mode=mode,
            min_score=min_score,
        )

    @cached_property
    def hybrid_observer(self):
        """Phase D-2 — observer that records hybrid's would-be solver decision
        alongside keyword's actual decision in MAGMA trace.

        Per v3 §1.1 candidate mode: hybrid traces both, production uses keyword.
        Output: docs/runs/magma_hybrid_candidate_trace.jsonl
        """
        from waggledance.core.reasoning.hybrid_observer import HybridObserver
        from pathlib import Path
        import yaml

        # Load all axiom solver_output_schemas
        specs = {}
        axioms_dir = Path("configs/axioms")
        if axioms_dir.exists():
            for axiom_path in axioms_dir.rglob("*.yaml"):
                try:
                    with open(axiom_path, encoding="utf-8") as f:
                        axiom = yaml.safe_load(f) or {}
                    if axiom.get("model_id"):
                        specs[axiom["model_id"]] = axiom.get("solver_output_schema", {})
                except Exception:
                    pass

        return HybridObserver(
            hybrid_retrieval_service=self.hybrid_retrieval,
            solver_specs=specs,
        )

    @cached_property
    def hybrid_backfill(self):
        """HybridBackfillService — idempotent cell-local FAISS population."""
        from waggledance.application.services.hybrid_backfill_service import (
            HybridBackfillService,
        )
        # Get case store from autonomy runtime if available
        case_store = None
        try:
            rt = self.autonomy_service._runtime
            case_store = rt.case_store
        except Exception:
            pass

        # Reuse same embed_fn as hybrid_retrieval
        embed_fn = getattr(self.hybrid_retrieval, '_embed_fn', None)

        return HybridBackfillService(
            hybrid_retrieval=self.hybrid_retrieval,
            case_store=case_store,
            embed_fn=embed_fn,
        )

    @cached_property
    def parallel_dispatcher(self):
        """ParallelLLMDispatcher — bounded concurrent LLM dispatch.

        Feature-flagged via llm_parallel.enabled in settings.yaml.
        When disabled, dispatch() is a zero-overhead passthrough.
        """
        from waggledance.application.services.parallel_llm_dispatcher import (
            ParallelLLMDispatcher,
        )
        return ParallelLLMDispatcher(
            settings=self._settings,
            llm=self.llm,
            gemma_router=self.gemma_router,
        )

    @cached_property
    def hex_topology_registry(self):
        """HexTopologyRegistry — loads hex cell topology and maps cells to agents."""
        from waggledance.application.services.hex_topology_registry import HexTopologyRegistry
        config_path = self._settings.get("hex_mesh.cell_config_path", "configs/hex_cells.yaml")
        return HexTopologyRegistry(
            config_path=config_path,
            agents=self._load_agents(),
        )

    @cached_property
    def hex_health_monitor(self):
        """HexHealthMonitor — cell quarantine, cooldown, self-heal."""
        from waggledance.application.services.hex_health_monitor import HexHealthMonitor
        return HexHealthMonitor(
            error_threshold=int(self._settings.get("hex_mesh.health_quarantine_error_threshold", 3)),
            timeout_threshold=int(self._settings.get("hex_mesh.health_quarantine_timeout_threshold", 2)),
            cooldown_s=float(self._settings.get("hex_mesh.health_cooldown_s", 300)),
            self_heal_probe_enabled=bool(self._settings.get("hex_mesh.self_heal_probe_enabled", True)),
        )

    @cached_property
    def hex_neighbor_assist(self):
        """HexNeighborAssist — local->neighbor->global resolution coordinator.

        Feature-flagged via hex_mesh.enabled in settings.yaml.
        """
        from waggledance.application.services.hex_neighbor_assist import HexNeighborAssist
        enabled = bool(self._settings.get("hex_mesh.enabled", False))

        magma_audit = None
        try:
            rt = self.autonomy_service._runtime
            magma_audit = rt.audit
        except Exception:
            pass

        return HexNeighborAssist(
            topology_registry=self.hex_topology_registry,
            health_monitor=self.hex_health_monitor,
            llm_service=self.llm,
            parallel_dispatcher=self.parallel_dispatcher,
            magma_audit=magma_audit,
            enabled=enabled,
            local_threshold=float(self._settings.get("hex_mesh.local_confidence_threshold", 0.72)),
            neighbor_threshold=float(self._settings.get("hex_mesh.neighbor_confidence_threshold", 0.82)),
            global_threshold=float(self._settings.get("hex_mesh.global_escalation_threshold", 0.90)),
            ttl_default=int(self._settings.get("hex_mesh.ttl_default", 2)),
            max_neighbors_per_hop=int(self._settings.get("hex_mesh.max_neighbors_per_hop", 2)),
            parallel_neighbor=bool(self._settings.get("hex_mesh.parallel_neighbor_assist", True)),
            merge_policy=str(self._settings.get("hex_mesh.neighbor_merge_policy", "weighted_confidence")),
            magma_trace_enabled=bool(self._settings.get("hex_mesh.magma_trace_enabled", True)),
            allow_neighbor_llm=bool(self._settings.get("hex_mesh.allow_neighbor_llm", True)),
            # v3.5.6: efficiency settings
            local_budget_ms=float(self._settings.get("hex_mesh.local_budget_ms", 15000)),
            neighbor_budget_ms=float(self._settings.get("hex_mesh.neighbor_budget_ms", 10000)),
            total_hex_budget_ms=float(self._settings.get("hex_mesh.total_hex_budget_ms", 25000)),
            skip_low_value_neighbor_when_sequential=bool(
                self._settings.get("hex_mesh.skip_low_value_neighbor_when_sequential", True)),
            preflight_min_score=float(self._settings.get("hex_mesh.preflight_min_score", 0.3)),
        )

    @cached_property
    def solver_candidate_lab(self):
        """SolverCandidateLab — safe solver candidate generation (isolated from production)."""
        from waggledance.application.services.solver_candidate_lab import SolverCandidateLab
        return SolverCandidateLab(
            llm=self.llm if not self._stub else None,
            gemma_router=self.gemma_router,
            parallel_dispatcher=self.parallel_dispatcher,
        )

    @cached_property
    def gemma_verifier_advisor(self):
        """GemmaVerifierAdvisor — optional advisory for deterministic verifier."""
        from waggledance.application.services.solver_candidate_lab import GemmaVerifierAdvisor
        return GemmaVerifierAdvisor(gemma_router=self.gemma_router)

    @cached_property
    def synthetic_accelerator(self):
        """SyntheticTrainingAccelerator — deterministic synthetic data augmentation."""
        from waggledance.core.learning.synthetic_accelerator import SyntheticTrainingAccelerator
        gpu_enabled = bool(self._settings.get("learning.gpu_enabled", False))
        return SyntheticTrainingAccelerator(gpu_enabled=gpu_enabled)

    @cached_property
    def storage_health(self):
        """StorageHealthService — DB size/WAL introspection."""
        from waggledance.application.services.storage_health_service import StorageHealthService
        import os
        data_dir = os.path.dirname(self._settings.db_path) or "data"
        return StorageHealthService(data_dir=data_dir)

    # --- Data feeds (weather / electricity / RSS) ---

    @cached_property
    def priority_lock(self):
        """PriorityLock — async gate used by background feeds."""
        from waggledance.core.priority_lock import PriorityLock
        return PriorityLock()

    @cached_property
    def feed_ingest_sink(self):
        """FeedIngestSink — bridges legacy learn(...) into VectorStorePort.upsert."""
        from waggledance.adapters.feeds.feed_ingest_sink import FeedIngestSink
        return FeedIngestSink(vector_store=self.vector_store)

    @cached_property
    def data_feed_scheduler(self):
        """DataFeedScheduler — returns None when feeds.enabled is false."""
        feeds_cfg = self._settings.get("feeds", {}) or {}
        if not feeds_cfg.get("enabled", False):
            return None
        # integrations.* is deliberate: legacy feed modules live there and
        # the legacy-import-freeze guard only blocks ``core.*`` imports.
        from integrations.data_scheduler import DataFeedScheduler
        return DataFeedScheduler(
            config=feeds_cfg,
            consciousness=self.feed_ingest_sink,
            priority_lock=self.priority_lock,
        )

    def build_app(self):
        """Build FastAPI application."""
        from waggledance.adapters.http.api import create_app
        return create_app(self)
