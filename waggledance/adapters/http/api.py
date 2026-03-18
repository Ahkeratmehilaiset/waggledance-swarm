"""FastAPI application factory."""

import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI

from waggledance.adapters.http.middleware.auth import BearerAuthMiddleware
from waggledance.adapters.http.middleware.rate_limit import RateLimitMiddleware
from waggledance.adapters.http.routes.autonomy import router as autonomy_router
from waggledance.adapters.http.routes.chat import router as chat_router
from waggledance.adapters.http.routes.memory import router as memory_router
from waggledance.adapters.http.routes.status import router as status_router
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

    logger.info("WaggleDance startup complete")

    yield  # application is running

    # ---- SHUTDOWN ----
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
        version="2.0.0",
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
    # Status routes at root level (/health, /ready)
    app.include_router(status_router)
    # Chat route under /api prefix (/api/chat)
    app.include_router(chat_router, prefix="/api")
    # Memory routes under /api prefix (/api/memory/ingest, /api/memory/search)
    app.include_router(memory_router, prefix="/api")
    # Autonomy routes under /api prefix (/api/autonomy/*)
    app.include_router(autonomy_router, prefix="/api")

    # ---- Static files ---- CONDITIONAL mount
    # Missing dashboard/dist must NOT crash the application
    dashboard_dir = "dashboard/dist"
    if os.path.isdir(dashboard_dir):
        try:
            from fastapi.staticfiles import StaticFiles

            app.mount("/", StaticFiles(directory=dashboard_dir, html=True))
            logger.info("Dashboard static files mounted from %s", dashboard_dir)
        except Exception as exc:
            logger.warning("Could not mount dashboard static files: %s", exc)
    else:
        logger.info(
            "Dashboard directory '%s' not found -- static files not mounted",
            dashboard_dir,
        )

    return app
