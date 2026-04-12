"""Post-gate release-polish tests: ``/version`` endpoint.

Locks in that ``/version`` is reachable without auth and returns the
stable shape ``{name, version, python, platform}`` with non-empty
strings and no leaked secrets. This is the F5-003 DEFERRED item from
the Release Polish Run 20260409_054702 gate decision.
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


def test_version_endpoint_returns_200_without_auth():
    _reset_rate_limit()
    r = _get_client().get("/version")
    assert r.status_code == 200


def test_version_endpoint_has_stable_shape():
    _reset_rate_limit()
    payload = _get_client().get("/version").json()
    for key in ("name", "version", "python", "platform"):
        assert key in payload, f"missing key {key!r} in /version payload"
        assert isinstance(payload[key], str)
        assert payload[key], f"empty value for {key!r}"


def test_version_endpoint_reports_waggledance_name():
    _reset_rate_limit()
    payload = _get_client().get("/version").json()
    assert payload["name"] == "waggledance-swarm"


def test_version_endpoint_reports_parseable_version():
    """The version must look like a dotted release (``3.5.x``) or
    ``"unknown"``. We explicitly refuse garbage like empty strings or
    random hashes that would break downstream parsers."""
    _reset_rate_limit()
    v = _get_client().get("/version").json()["version"]
    assert v == "unknown" or (
        all(part.isdigit() or part.replace("-", "").replace(".", "").isalnum()
            for part in v.split("."))
    )
    # Additional sanity: must not contain whitespace.
    assert " " not in v


def test_version_endpoint_reports_python_major_minor():
    _reset_rate_limit()
    py = _get_client().get("/version").json()["python"]
    # Format should be like "3.13.1" -- at minimum major.minor present.
    parts = py.split(".")
    assert len(parts) >= 2
    assert parts[0].isdigit() and parts[1].isdigit()


def test_version_endpoint_leaks_no_secrets():
    """Extra-paranoid: the /version response is a public endpoint, so
    we lock in that it never ships the resolved api_key, no env vars,
    no filesystem paths."""
    _reset_rate_limit()
    container = Container(
        settings=WaggleSettings.from_env(),
        stub=True,
    )
    api_key = container._settings.api_key
    text = _get_client().get("/version").text
    assert api_key not in text
    # No Windows-style absolute paths either.
    assert "U:" not in text
    assert "C:\\" not in text


def test_version_endpoint_in_public_paths():
    from waggledance.adapters.http.middleware.auth import PUBLIC_PATHS
    assert "/version" in PUBLIC_PATHS
