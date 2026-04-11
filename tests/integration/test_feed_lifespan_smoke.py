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

import pytest
from starlette.testclient import TestClient

from waggledance.adapters.config.settings_loader import WaggleSettings
from waggledance.bootstrap.container import Container


@pytest.fixture
def stub_app_and_key():
    settings = WaggleSettings.from_env()
    container = Container(settings=settings, stub=True)
    app = container.build_app()
    return app, container, settings.api_key


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
    _, container, _ = stub_app_and_key
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
    app, container, api_key = stub_app_and_key
    with TestClient(app, raise_server_exceptions=True) as client:
        _reset_rate_limit(app)
        # While the lifespan is active, hitting /health must succeed.
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


def test_api_feeds_reachable_during_lifespan(stub_app_and_key):
    """Actually hit /api/feeds while the scheduler is running (or
    would be, if feeds.enabled were true). /api/feeds is a public path
    per middleware/auth.py, so no bearer token is required, but we
    send one anyway for realism."""
    app, container, api_key = stub_app_and_key
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
    app, container, _ = stub_app_and_key
    if container.data_feed_scheduler is None:
        pytest.skip("feeds disabled; sink lifecycle not exercised")
    # Just entering/exiting the TestClient context is enough — if the
    # shutdown hook reorders sink-before-scheduler and the sink is
    # awaited while the consumer task is still cancelled, this would
    # hang or raise.
    with TestClient(app, raise_server_exceptions=True):
        pass
