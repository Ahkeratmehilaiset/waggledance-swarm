"""Phase 5/6 release-polish tests: k8s-style health aliases.

Locks in that ``/healthz`` and ``/readyz`` work the same as their
unsuffixed siblings, and that both variants are auth-exempt.
"""

from starlette.testclient import TestClient

from waggledance.adapters.config.settings_loader import WaggleSettings
from waggledance.bootstrap.container import Container


_client = None


def _get_client():
    global _client
    if _client is None:
        settings = WaggleSettings.from_env()
        container = Container(settings=settings, stub=True)
        app = container.build_app()
        _client = TestClient(app, raise_server_exceptions=False)
    return _client


def _reset_rate_limit():
    client = _get_client()
    app = client.app
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


def test_healthz_alias_returns_200_without_auth():
    _reset_rate_limit()
    r = _get_client().get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_health_still_returns_200_without_auth():
    _reset_rate_limit()
    r = _get_client().get("/health")
    assert r.status_code == 200


def test_readyz_alias_returns_same_shape_as_ready():
    _reset_rate_limit()
    r_ready = _get_client().get("/ready")
    _reset_rate_limit()
    r_readyz = _get_client().get("/readyz")
    assert r_ready.status_code == r_readyz.status_code
    assert r_ready.status_code in (200, 503)
    for key in r_ready.json().keys():
        assert key in r_readyz.json()


def test_healthz_is_in_public_paths():
    from waggledance.adapters.http.middleware.auth import PUBLIC_PATHS
    assert "/healthz" in PUBLIC_PATHS
    assert "/readyz" in PUBLIC_PATHS
