"""FastAPI application factory."""

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI

from waggledance.adapters.http.middleware.auth import BearerAuthMiddleware
from waggledance.adapters.http.middleware.rate_limit import RateLimitMiddleware
from waggledance.adapters.http.routes.auth_session import router as auth_router
from waggledance.adapters.http.routes.autonomy import router as autonomy_router
from waggledance.adapters.http.routes.chat import router as chat_router
from waggledance.adapters.http.routes.compat_dashboard import router as compat_router
from waggledance.adapters.http.routes.cross_agent import router as cross_agent_router
from waggledance.adapters.http.routes.eng01_advisory import router as eng01_advisory_router
from waggledance.adapters.http.routes.air01_advisory import router as air01_advisory_router
from waggledance.adapters.http.routes.eng06_advisory import router as eng06_advisory_router
from waggledance.adapters.http.routes.graph import router as graph_router
from waggledance.adapters.http.routes.hologram import router as hologram_router
from waggledance.adapters.http.routes.candidate_lab import router as candidate_lab_router
from waggledance.adapters.http.routes.hybrid import router as hybrid_router
from waggledance.adapters.http.routes.magma import router as magma_router
from waggledance.adapters.http.routes.memory import router as memory_router
from waggledance.adapters.http.routes.metrics import router as metrics_router
from waggledance.adapters.http.routes.status import router as status_router
from waggledance.adapters.http.routes.storage import router as storage_router
from waggledance.adapters.http.routes.trust import router as trust_router
from waggledance.core.domain.events import DomainEvent, EventType

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle: startup verification and graceful shutdown."""
    container = app.state.container

    # ---- STARTUP ----
    # Initialize shared memory if the adapter is wired
    if hasattr(container, "shared_memory"):
        try:
            await container.shared_memory.initialize()
        except Exception as exc:
            logger.error("SharedMemory initialization failed: %s", exc, exc_info=True)

    # Verify required adapters are constructible (fail-fast)
    _verify = container.llm
    _verify = container.vector_store
    _verify = container.memory_repository
    _verify = container.trust_store

    # Audit D2.1: warm ControlPlaneDB so data/control_plane.db exists
    # before any autonomy-growth consumer tries to write to it. The
    # cached_property runs ControlPlaneDB.__init__ which opens the
    # connection, sets WAL, and calls migrate().
    if hasattr(container, "control_plane_db"):
        try:
            cp_db = container.control_plane_db
            if cp_db is not None:
                logger.info(
                    "ControlPlaneDB warmed: %s (schema v%d)",
                    cp_db.db_path, cp_db.schema_version(),
                )
        except Exception as exc:
            logger.error(
                "ControlPlaneDB warmup failed; autonomy-growth track "
                "inactive: %s", exc, exc_info=True,
            )

    # Start low-risk autogrowth queue drain if the runtime wired it.
    autogrowth_ticker = getattr(container, "autogrowth_background_ticker", None)
    if autogrowth_ticker is not None:
        try:
            await autogrowth_ticker.start()
            logger.info("AutogrowthBackgroundTicker started")
        except Exception as exc:
            logger.warning("AutogrowthBackgroundTicker start failed: %s", exc)

    # Start autonomy runtime if available
    if hasattr(container, "autonomy_service"):
        try:
            container.autonomy_service.start()
            logger.info("AutonomyService started")
        except Exception as exc:
            logger.warning("AutonomyService start failed: %s", exc)

    # Start DataFeedScheduler (weather / electricity / RSS) if wired + enabled
    scheduler = getattr(container, "data_feed_scheduler", None)
    if scheduler is not None:
        try:
            ready = True
            try:
                ready = await container.vector_store.is_ready()
            except Exception as exc:
                logger.warning("vector_store readiness probe failed: %s", exc)
                ready = False
            if not ready:
                logger.warning(
                    "Skipping DataFeedScheduler start: vector_store not ready"
                )
            else:
                await container.feed_ingest_sink.start()
                await scheduler.start()
                logger.info(
                    "DataFeedScheduler started (%d feeds)",
                    len(getattr(scheduler, "_feeds", {}) or {}),
                )
        except Exception as exc:
            logger.warning("DataFeedScheduler start failed: %s", exc)

    logger.info("WaggleDance startup complete")

    yield  # application is running

    # ---- SHUTDOWN ----
    # Stop autogrowth ticker before closing ControlPlaneDB.
    autogrowth_ticker = getattr(container, "autogrowth_background_ticker", None)
    if autogrowth_ticker is not None:
        try:
            await autogrowth_ticker.stop()
            logger.info("AutogrowthBackgroundTicker stopped")
        except Exception as exc:
            logger.warning("AutogrowthBackgroundTicker stop failed: %s", exc)

    # Stop DataFeedScheduler first so in-flight feed tasks can drain into the sink
    scheduler = getattr(container, "data_feed_scheduler", None)
    if scheduler is not None:
        try:
            await scheduler.stop()
            await container.feed_ingest_sink.stop()
            logger.info("DataFeedScheduler stopped")
        except Exception as exc:
            logger.warning("DataFeedScheduler stop failed: %s", exc)

    # Stop autonomy runtime
    if hasattr(container, "autonomy_service"):
        try:
            container.autonomy_service.stop()
            logger.info("AutonomyService stopped")
        except Exception as exc:
            logger.warning("AutonomyService stop failed: %s", exc)

    # Audit D2.1: close ControlPlaneDB. Calling close() on an
    # already-closed handle is safe (idempotent).
    if hasattr(container, "control_plane_db"):
        try:
            cp_db = container.control_plane_db
            if cp_db is not None:
                cp_db.close()
                logger.info("ControlPlaneDB closed")
        except Exception as exc:
            logger.warning("ControlPlaneDB close failed: %s", exc)

    # Close OllamaAdapter httpx client if present
    llm = container.llm
    if hasattr(llm, "close"):
        try:
            await llm.close()
        except Exception as exc:
            logger.warning("Error closing LLM client: %s", exc)
    elif hasattr(llm, "_client"):
        try:
            await llm._client.aclose()
        except Exception as exc:
            logger.warning("Error closing LLM httpx client: %s", exc)

    # Emit shutdown event
    try:
        await container.event_bus.publish(
            DomainEvent(
                type=EventType.APP_SHUTDOWN,
                payload={},
                timestamp=time.time(),
                source="lifespan",
            )
        )
    except Exception as exc:
        logger.warning("Error publishing shutdown event: %s", exc)

    logger.info("WaggleDance shutdown complete")


def create_app(container) -> FastAPI:
    """Build and return a configured FastAPI application.

    Args:
        container: The bootstrap Container providing all dependencies.

    Returns:
        A fully configured FastAPI application instance.
    """
    app = FastAPI(
        title="WaggleDance AI",
        version="3.2.0",
        lifespan=lifespan,
    )
    app.state.container = container

    # ---- Middleware ----
    # Auth middleware gets api_key from container settings, NOT from env
    app.add_middleware(
        BearerAuthMiddleware,
        api_key=container._settings.api_key,
    )
    app.add_middleware(RateLimitMiddleware, requests_per_minute=60)

    # ---- Routes ----
    # Auth session routes (/api/auth/session, /api/auth/check)
    app.include_router(auth_router)
    # Status routes at root level (/health, /ready, /version)
    app.include_router(status_router)
    # Prometheus text-format metrics at /metrics (public, text/plain)
    app.include_router(metrics_router)
    # Chat route under /api prefix (/api/chat)
    app.include_router(chat_router, prefix="/api")
    # Memory routes under /api prefix (/api/memory/ingest, /api/memory/search)
    app.include_router(memory_router, prefix="/api")
    # Autonomy routes under /api prefix (/api/autonomy/*)
    app.include_router(autonomy_router, prefix="/api")
    # Hologram brain visualization (/hologram + /api/hologram/state)
    app.include_router(hologram_router)
    # Legacy dashboard compat endpoints for hologram menus + /ws
    app.include_router(compat_router)
    # MAGMA introspection (/api/magma/*)
    app.include_router(magma_router)
    # Cognitive graph (/api/graph/*)
    app.include_router(graph_router)
    # Trust engine (/api/trust/*)
    app.include_router(trust_router)
    # Cross-agent memory (/api/cross/*)
    app.include_router(cross_agent_router)
    # Storage health introspection (/api/storage/*)
    app.include_router(storage_router)
    # Hybrid retrieval (/api/hybrid/*)
    app.include_router(hybrid_router)
    # Candidate lab observability (/api/candidate_lab/*, /api/learning/accelerator)
    app.include_router(candidate_lab_router)
    # ENG-01 advisory snapshot (/api/eng01/advisory/latest)
    app.include_router(eng01_advisory_router)
    # AIR-01 advisory snapshot (/api/air01/advisory/latest)
    app.include_router(air01_advisory_router)
    # ENG-06 advisory snapshot (/api/eng06/advisory/latest)
    app.include_router(eng06_advisory_router)

    return app
