"""Dependency injection container -- wires everything together."""

import logging
from functools import cached_property

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

    @cached_property
    def orchestrator(self):
        """Orchestrator from core orchestration."""
        from waggledance.core.orchestration.orchestrator import Orchestrator
        from waggledance.core.orchestration.round_table import RoundTableEngine
        rt = RoundTableEngine(llm=self.llm, event_bus=self.event_bus)
        return Orchestrator(
            scheduler=self.scheduler,
            round_table=rt,
            memory=self.memory_repository,
            vector_store=self.vector_store,
            llm=self.llm,
            trust_store=self.trust_store,
            event_bus=self.event_bus,
            config=self.config,
            agents=[],
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
        """FaissRegistry — named FAISS collections for cell-local retrieval."""
        from core.faiss_store import FaissRegistry
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
        )

    @cached_property
    def storage_health(self):
        """StorageHealthService — DB size/WAL introspection."""
        from waggledance.application.services.storage_health_service import StorageHealthService
        import os
        data_dir = os.path.dirname(self._settings.db_path) or "data"
        return StorageHealthService(data_dir=data_dir)

    def build_app(self):
        """Build FastAPI application."""
        from waggledance.adapters.http.api import create_app
        return create_app(self)
