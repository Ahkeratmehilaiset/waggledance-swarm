"""Dependency injection container -- wires everything together."""

from functools import cached_property


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
        """ChatService from application layer."""
        from waggledance.application.services.chat_service import ChatService
        from waggledance.core.orchestration.routing_policy import select_route
        return ChatService(
            orchestrator=self.orchestrator,
            memory_service=self.memory_service,
            hot_cache=self.hot_cache,
            routing_policy_fn=select_route,
            config=self.config,
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

    @cached_property
    def autonomy_service(self):
        """AutonomyService — wires runtime mode from settings."""
        from waggledance.application.services.autonomy_service import AutonomyService
        from waggledance.core.autonomy.compatibility import CompatibilityLayer
        from waggledance.core.autonomy.lifecycle import AutonomyLifecycle
        from waggledance.core.autonomy.resource_kernel import ResourceKernel
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        profile = self._settings.get_profile()
        runtime = AutonomyRuntime(profile=profile)
        lifecycle = AutonomyLifecycle(
            primary=self._settings.runtime_primary,
            compatibility_mode=self._settings.compatibility_mode,
            profile=profile,
        )
        compatibility = CompatibilityLayer(
            runtime=runtime,
            compatibility_mode=self._settings.compatibility_mode,
        )

        # Build ResourceKernel with tier from settings
        tier = self._settings.get_hardware_tier()
        resource_kernel = ResourceKernel(tier=tier)

        return AutonomyService(
            runtime=runtime,
            lifecycle=lifecycle,
            resource_kernel=resource_kernel,
            compatibility=compatibility,
            profile=profile,
            night_pipeline=self.night_pipeline,
        )

    def build_app(self):
        """Build FastAPI application."""
        from waggledance.adapters.http.api import create_app
        return create_app(self)
