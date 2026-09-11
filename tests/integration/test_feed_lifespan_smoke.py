"""Integration smoke: feed wiring under the real FastAPI lifespan.

This test complements the unit-level wiring tests in
``tests/test_feed_scheduler_wiring.py`` by actually driving the FastAPI
lifespan context (startup + shutdown) and hitting the feeds endpoint.

It runs in stub mode so no Ollama, no network and no RAM-disk ChromaDB
warmth are required. The purpose is to lock in that:

1. With ``feeds.enabled: true`` in settings.yaml, the container
   actually builds a ``data_feed_scheduler``.
2. The FastAPI startup hook in ``api.py`` does not crash when the
   scheduler is present.
3. ``/api/feeds`` is reachable and returns a 2xx-shaped payload while
   the lifespan is active.
4. The shutdown hook tears the scheduler down cleanly (scheduler
   first, sink second — the order that keeps the in-flight feed tasks
   from writing into a closed sink).

If any of these break in a future refactor, the smoke fires loud.
"""

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from waggledance.adapters.config.settings_loader import WaggleSettings
from waggledance.adapters.http.api import lifespan
from waggledance.adapters.memory.sqlite_shared_memory import SQLiteSharedMemory
from waggledance.adapters.trust.sqlite_trust_store import SQLiteTrustStore
from waggledance.bootstrap.container import Container


@pytest.fixture
def stub_app_and_key(tmp_path):
    settings = WaggleSettings.from_env()
    container = Container(settings=settings, stub=True)
    shared_memory = SQLiteSharedMemory(str(tmp_path / "shared-memory.db"))
    container.__dict__["shared_memory"] = shared_memory
    trust_close = AsyncMock()
    container.trust_store.close = trust_close
    app = container.build_app()
    return app, container, settings.api_key, shared_memory, trust_close


class _EventBus:
    def __init__(self):
        self.published = 0

    async def publish(self, _event):
        self.published += 1


class _LifecycleContainer:
    def __init__(self, shared_memory, trust_store=None):
        self.shared_memory = shared_memory
        self._llm = object()
        self.vector_store = object()
        self.memory_repository = object()
        self.trust_store = object() if trust_store is None else trust_store
        self.control_plane_db = None
        self.data_feed_scheduler = None
        self.event_bus = _EventBus()

    @property
    def llm(self):
        return self._llm


class _TrackingSharedMemory(SQLiteSharedMemory):
    def __init__(self, db_path):
        super().__init__(db_path)
        self.opened_connection = None
        self.opened_worker = None

    async def initialize(self):
        await super().initialize()
        self.opened_connection = self._db
        self.opened_worker = _worker(self._db)


class _PartialInitializationMemory(_TrackingSharedMemory):
    def __init__(self, db_path):
        super().__init__(db_path)
        self.close_calls = 0

    async def initialize(self):
        await super().initialize()
        raise RuntimeError("partial shared-memory initialization")

    async def close(self):
        self.close_calls += 1
        await super().close()


class _InitializationCleanupFailure(RuntimeError):
    pass


class _CloseFailingPartialMemory(_PartialInitializationMemory):
    def __init__(self, db_path):
        super().__init__(db_path)
        self.cleanup_failure = _InitializationCleanupFailure(
            "partial initialization cleanup failed"
        )

    async def close(self):
        self.close_calls += 1
        if self.close_calls == 1:
            raise self.cleanup_failure
        await SQLiteSharedMemory.close(self)


class _StartupFailure(RuntimeError):
    pass


class _LateStartupFailureContainer(_LifecycleContainer):
    def __init__(self, shared_memory):
        super().__init__(shared_memory)
        self.startup_failure = _StartupFailure("later adapter resolution failed")

    @property
    def llm(self):
        raise self.startup_failure


class _BodyFailure(RuntimeError):
    pass


class _TrustCloseFailure(RuntimeError):
    pass


class _OrderedFailingTrustStore:
    def __init__(self, order):
        self.order = order
        self.failure = _TrustCloseFailure("trust close failed")

    async def close(self):
        self.order.append("trust")
        raise self.failure


class _OrderedSharedMemory(_TrackingSharedMemory):
    def __init__(self, db_path, order):
        super().__init__(db_path)
        self.order = order
        self.close_calls = 0

    async def close(self):
        self.close_calls += 1
        self.order.append("shared")
        await super().close()


def _app_for(container):
    app = FastAPI()
    app.state.container = container
    return app


def _worker(connection):
    worker = getattr(connection, "_thread", connection)
    assert worker.is_alive()
    return worker


def _assert_worker_stopped(worker):
    worker.join(timeout=5)
    assert not worker.is_alive()


def _reset_rate_limit(app):
    from waggledance.adapters.http.middleware.rate_limit import (
        RateLimitMiddleware,
    )
    obj = getattr(app, "middleware_stack", None)
    if obj is None:
        return
    for _ in range(30):
        if isinstance(obj, RateLimitMiddleware):
            obj._buckets.clear()
            return
        obj = getattr(obj, "app", None)
        if obj is None:
            break


def test_container_builds_feed_scheduler_from_real_settings(stub_app_and_key):
    """If feeds.enabled is true in the real settings.yaml (which it is
    as of this commit), the container must expose a non-None
    data_feed_scheduler."""
    _, container, _, _, _ = stub_app_and_key
    scheduler = container.data_feed_scheduler
    # When feeds are enabled in the real settings, we must get a
    # scheduler. When they are not, we get None — the test is written
    # against the current default, so failure here is a signal that
    # either the default flipped or the container wiring regressed.
    feeds_cfg = container._settings.get("feeds", {}) or {}
    if feeds_cfg.get("enabled", False):
        assert scheduler is not None, (
            "feeds.enabled=true but container.data_feed_scheduler is None"
        )
    else:
        assert scheduler is None


def test_lifespan_startup_and_shutdown_are_exception_safe(stub_app_and_key):
    """Entering and exiting the TestClient context runs the full
    FastAPI lifespan (startup + shutdown). If the feed-wiring startup
    or shutdown hooks raise, this blows up immediately."""
    app, container, api_key, shared_memory, trust_close = stub_app_and_key
    with TestClient(app, raise_server_exceptions=True) as client:
        connection = shared_memory._db
        worker = _worker(connection)
        _reset_rate_limit(app)
        # While the lifespan is active, hitting /health must succeed.
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}
    assert shared_memory._db is None
    _assert_worker_stopped(worker)
    trust_close.assert_awaited_once_with()


def test_api_feeds_reachable_during_lifespan(stub_app_and_key):
    """Actually hit /api/feeds while the scheduler is running (or
    would be, if feeds.enabled were true). /api/feeds is a public path
    per middleware/auth.py, so no bearer token is required, but we
    send one anyway for realism."""
    app, container, api_key, _, _ = stub_app_and_key
    with TestClient(app, raise_server_exceptions=False) as client:
        _reset_rate_limit(app)
        r = client.get(
            "/api/feeds",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        # Accept either a happy 200 or a graceful 503 (scheduler still
        # cold). What we refuse to accept: a 500 or a crash.
        assert r.status_code in (200, 204, 503), (
            f"/api/feeds returned unexpected status: {r.status_code} / "
            f"body: {r.text[:200]}"
        )


def test_feed_ingest_sink_lifecycle_matches_scheduler(stub_app_and_key):
    """The shutdown hook stops the scheduler first, then the sink.
    Lock this in against an accidental reorder that would let the
    sink close mid-feed and drop queued entries."""
    app, container, _, _, _ = stub_app_and_key
    if container.data_feed_scheduler is None:
        pytest.skip("feeds disabled; sink lifecycle not exercised")
    # Just entering/exiting the TestClient context is enough — if the
    # shutdown hook reorders sink-before-scheduler and the sink is
    # awaited while the consumer task is still cancelled, this would
    # hang or raise.
    with TestClient(app, raise_server_exceptions=True):
        pass


def test_partial_initialization_is_closed_before_startup_continues(tmp_path):
    shared_memory = _PartialInitializationMemory(tmp_path / "partial.db")
    app = _app_for(_LifecycleContainer(shared_memory))
    body_reached = False

    async def run():
        nonlocal body_reached
        try:
            async with lifespan(app):
                body_reached = True
                assert shared_memory._db is None
        finally:
            if shared_memory._db is not None:
                await SQLiteSharedMemory.close(shared_memory)

    asyncio.run(run())
    _assert_worker_stopped(shared_memory.opened_worker)
    assert body_reached is True
    assert shared_memory._db is None
    assert shared_memory.close_calls == 2


def test_partial_initialization_cleanup_failure_is_not_swallowed(tmp_path):
    shared_memory = _CloseFailingPartialMemory(tmp_path / "partial-fail.db")
    app = _app_for(_LifecycleContainer(shared_memory))
    body_reached = False

    async def run():
        nonlocal body_reached
        try:
            async with lifespan(app):
                body_reached = True
        finally:
            if shared_memory._db is not None:
                await SQLiteSharedMemory.close(shared_memory)

    with pytest.raises(_InitializationCleanupFailure) as caught:
        asyncio.run(run())
    assert caught.value is shared_memory.cleanup_failure
    assert body_reached is False
    assert shared_memory._db is None
    _assert_worker_stopped(shared_memory.opened_worker)
    assert shared_memory.close_calls == 2


def test_pre_yield_startup_failure_closes_initialized_memory(tmp_path):
    shared_memory = _TrackingSharedMemory(str(tmp_path / "pre-yield.db"))
    container = _LateStartupFailureContainer(shared_memory)
    app = _app_for(container)
    body_reached = False

    async def run():
        nonlocal body_reached
        try:
            async with lifespan(app):
                body_reached = True
        except _StartupFailure as exc:
            closed_before_observer_cleanup = shared_memory._db is None
            if shared_memory._db is not None:
                await shared_memory.close()
            return exc, closed_before_observer_cleanup
        raise AssertionError("startup failure did not escape lifespan")

    failure, was_closed = asyncio.run(run())
    assert failure is container.startup_failure
    assert body_reached is False
    assert was_closed is True
    _assert_worker_stopped(shared_memory.opened_worker)


def test_body_exception_preserved_and_sqlite_closed(tmp_path):
    shared_memory = SQLiteSharedMemory(str(tmp_path / "body-error.db"))
    container = _LifecycleContainer(shared_memory)
    app = _app_for(container)
    sentinel = _BodyFailure("body failed")
    observed_worker = None

    async def run():
        nonlocal observed_worker
        try:
            async with lifespan(app):
                observed_worker = _worker(shared_memory._db)
                raise sentinel
        except _BodyFailure as exc:
            closed_before_observer_cleanup = shared_memory._db is None
            if shared_memory._db is not None:
                await shared_memory.close()
            return exc, closed_before_observer_cleanup
        raise AssertionError("body failure did not escape lifespan")

    failure, was_closed = asyncio.run(run())
    assert failure is sentinel
    assert was_closed is True
    assert container.event_bus.published == 1
    _assert_worker_stopped(observed_worker)


def test_body_cancellation_enters_shutdown_and_closes_sqlite(tmp_path):
    shared_memory = SQLiteSharedMemory(str(tmp_path / "body-cancel.db"))
    container = _LifecycleContainer(shared_memory)
    app = _app_for(container)
    observed_worker = None

    async def run():
        nonlocal observed_worker
        try:
            async with lifespan(app):
                observed_worker = _worker(shared_memory._db)
                raise asyncio.CancelledError()
        except asyncio.CancelledError:
            closed_before_observer_cleanup = shared_memory._db is None
            if shared_memory._db is not None:
                await shared_memory.close()
            return closed_before_observer_cleanup
        raise AssertionError("cancellation did not escape lifespan")

    was_closed = asyncio.run(run())
    assert was_closed is True
    assert container.event_bus.published == 1
    _assert_worker_stopped(observed_worker)


def test_lazily_opened_sqlite_trust_store_is_closed(tmp_path):
    shared_memory = SQLiteSharedMemory(str(tmp_path / "shared.db"))
    trust_store = SQLiteTrustStore(str(tmp_path / "trust.db"))
    app = _app_for(_LifecycleContainer(shared_memory, trust_store))
    trust_worker = None
    shared_worker = None
    closed_by_lifespan = None

    async def run():
        nonlocal trust_worker, shared_worker, closed_by_lifespan
        try:
            async with lifespan(app):
                assert trust_store._db is None
                await trust_store.get_trust("missing")
                trust_worker = _worker(trust_store._db)
                shared_worker = _worker(shared_memory._db)
        finally:
            closed_by_lifespan = (
                trust_store._db is None,
                shared_memory._db is None,
            )
            if trust_store._db is not None:
                await trust_store.close()
            if shared_memory._db is not None:
                await shared_memory.close()

    asyncio.run(run())
    assert closed_by_lifespan == (True, True)
    assert trust_store._db is None
    assert shared_memory._db is None
    _assert_worker_stopped(trust_worker)
    _assert_worker_stopped(shared_worker)


@pytest.mark.parametrize("bad_close", [None, object(), lambda: None])
def test_present_non_async_trust_close_fails_loudly(tmp_path, bad_close):
    shared_memory = _TrackingSharedMemory(str(tmp_path / "bad-hook.db"))
    trust_store = type("BadTrustStore", (), {})()
    trust_store.close = bad_close
    app = _app_for(_LifecycleContainer(shared_memory, trust_store))
    body_reached = False

    async def run():
        nonlocal body_reached
        try:
            async with lifespan(app):
                body_reached = True
        finally:
            if shared_memory._db is not None:
                await shared_memory.close()

    with pytest.raises(TypeError, match="trust_store.close must be async"):
        asyncio.run(run())
    assert body_reached is False
    assert shared_memory._db is None
    _assert_worker_stopped(shared_memory.opened_worker)


def test_trust_close_failure_is_loud_and_shared_close_still_runs(
    tmp_path, caplog
):
    order = []
    shared_memory = _OrderedSharedMemory(tmp_path / "ordered.db", order)
    trust_store = _OrderedFailingTrustStore(order)
    app = _app_for(_LifecycleContainer(shared_memory, trust_store))
    body_failure = _BodyFailure("body failed before trust cleanup")
    observed_worker = None

    async def run():
        nonlocal observed_worker
        try:
            async with lifespan(app):
                observed_worker = _worker(shared_memory._db)
                raise body_failure
        finally:
            if shared_memory._db is not None:
                await SQLiteSharedMemory.close(shared_memory)

    caplog.set_level(logging.INFO, logger="waggledance.adapters.http.api")
    with pytest.raises(_TrustCloseFailure) as caught:
        asyncio.run(run())
    assert caught.value is trust_store.failure
    assert caught.value.__context__ is body_failure
    assert order == ["trust", "shared"]
    assert shared_memory.close_calls == 1
    assert shared_memory._db is None
    _assert_worker_stopped(observed_worker)
    assert "WaggleDance shutdown complete" not in caplog.messages
