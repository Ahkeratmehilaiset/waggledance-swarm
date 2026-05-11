"""Runtime lifespan wiring for the autogrowth background ticker."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI

from waggledance.adapters.http.api import lifespan


class _Ticker:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0

    async def start(self) -> bool:
        self.started += 1
        return True

    async def stop(self) -> bool:
        self.stopped += 1
        return True


class _LLM:
    pass


class _EventBus:
    async def publish(self, event) -> None:
        self.event = event


class _Container:
    def __init__(self) -> None:
        self.llm = _LLM()
        self.vector_store = object()
        self.memory_repository = object()
        self.trust_store = object()
        self.control_plane_db = None
        self.autogrowth_background_ticker = _Ticker()
        self.data_feed_scheduler = None
        self.event_bus = _EventBus()


def test_lifespan_starts_and_stops_autogrowth_background_ticker() -> None:
    app = FastAPI()
    container = _Container()
    app.state.container = container

    async def _run() -> None:
        async with lifespan(app):
            assert container.autogrowth_background_ticker.started == 1
            assert container.autogrowth_background_ticker.stopped == 0
        assert container.autogrowth_background_ticker.stopped == 1

    asyncio.run(_run())
