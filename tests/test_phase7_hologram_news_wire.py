"""Phase 7 regression tests for HOLO-001, NEWS-001/002/003, and WIRE-001.

These tests were reconstructed from PHASE7_FIXES.md in the release-final
reports (C:\\WaggleDance_ReleaseFinalRun\\20260410_031819\\reports\\) after the
original ``test_phase7_hologram_news_wire.py`` file was lost together with
the U:\\project2 working tree on 2026-04-11. They lock in:

  HOLO-001  hologram.py::_legacy_view synthesises the legacy keys
            (api_key_configured, auth_middleware_active, lifecycle,
             policy_engine, admission, persistence, feeds) that the node-meta
             derivation reads, so the hologram shows real health instead of
             "unwired" even when the hex runtime returns modern-shape stats.

  NEWS-001  compat_dashboard.py::_enrich_from_chroma fetches a real tail
            window (limit=500), not the insertion-order first-5 slice.

  NEWS-002  _enrich_from_chroma computes items_count from the full result
            set and freshness_s from the NEWEST item, not max() of a stale
            window.

  NEWS-003  _enrich_from_chroma promotes source["state"] idle → active/stale
            based on freshness vs interval, without ever overwriting terminal
            upstream states (unwired/framework/failed).

  WIRE-001  hologram.py::hologram_view handles /hologram?token=<api_key> by
            minting a waggle_session cookie and 303-redirecting to /hologram,
            so the browser never sees the master key.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from waggledance.adapters.http.routes import auth_session, hologram
from waggledance.adapters.http.routes.compat_dashboard import _enrich_from_chroma
from waggledance.adapters.http.routes.hologram import _legacy_view


# ═══════════════════════════════════════════════════════════════
# Fixtures & helpers
# ═══════════════════════════════════════════════════════════════


def _fake_container(api_key: str = "TEST_API_KEY_123",
                    feeds_status: dict | None = None) -> Any:
    """Minimal container that exposes _settings.api_key and data_feed_scheduler."""
    settings = MagicMock()
    settings.api_key = api_key

    scheduler = None
    if feeds_status is not None:
        scheduler = MagicMock()
        scheduler.get_status.return_value = feeds_status

    container = MagicMock()
    container._settings = settings
    container.data_feed_scheduler = scheduler
    return container


def _hex_runtime_stats(*, running: bool = True) -> dict:
    """Return a stats dict shaped like the real hex AutonomyRuntime.stats()."""
    return {
        "running": running,
        "policy": {"constitution_version": "v1", "pending_approval": 0},
        "admission_control": {"queue_depth": 0},
        "action_bus": {"pending": 0},
        "goals": {"active": 1},
        "solver_router": {"active_count": 0, "recent_activity": {}},
        "verifier": {"recent_checks": 0},
        "working_memory": {"size": 0, "capacity": 32},
        "world_model": {"graph": {"nodes": 10, "edges": 20}},
        "capabilities": {"count": 5},
        "persist_world": {"ok": True, "io_in_flight": 0},
        "persist_cases": {"ok": True, "io_in_flight": 0},
        "persist_verifier": {"ok": True, "io_in_flight": 0},
    }


def _chroma_mock(entries: list[tuple[str, str, str]]):
    """Build a ChromaDB mock. Each entry = (doc, feed_id, timestamp_iso)."""
    col = MagicMock()
    docs = [e[0] for e in entries]
    metas = [
        {"agent_id": "rss_feed", "feed_id": e[1], "timestamp": e[2]}
        for e in entries
    ]
    col.get.return_value = {"documents": docs, "metadatas": metas}
    return col


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


# ═══════════════════════════════════════════════════════════════
# HOLO-001 — _legacy_view synthesises legacy keys (10 tests)
# ═══════════════════════════════════════════════════════════════


class TestHOLO001LegacyView:
    """HOLO-001: the hologram node-meta derivation reads legacy keys the
    hex-shape runtime.stats() no longer emits. _legacy_view() must synthesise
    them so the hologram shows real health instead of universal 'unwired'."""

    def test_api_key_configured_reflects_container_settings(self):
        container = _fake_container(api_key="secret")
        view = _legacy_view(_hex_runtime_stats(), container, MagicMock())
        assert view["api_key_configured"] is True
        assert view["auth_middleware_active"] is True

    def test_api_key_configured_false_when_empty(self):
        container = _fake_container(api_key="")
        view = _legacy_view(_hex_runtime_stats(), container, MagicMock())
        assert view["api_key_configured"] is False
        assert view["auth_middleware_active"] is False

    def test_lifecycle_state_running(self):
        view = _legacy_view(_hex_runtime_stats(running=True),
                            _fake_container(), MagicMock())
        assert view["lifecycle"]["state"] == "RUNNING"

    def test_lifecycle_state_stopped(self):
        view = _legacy_view(_hex_runtime_stats(running=False),
                            _fake_container(), MagicMock())
        assert view["lifecycle"]["state"] == "STOPPED"

    def test_lifecycle_healthy_components_counts_canonical_blocks(self):
        view = _legacy_view(_hex_runtime_stats(), _fake_container(), MagicMock())
        lc = view["lifecycle"]
        # all 8 canonical blocks present in the fake stats
        assert lc["total_components"] == 8
        assert lc["healthy_components"] == 8

    def test_lifecycle_healthy_components_drops_on_missing_block(self):
        rs = _hex_runtime_stats()
        del rs["admission_control"]
        del rs["world_model"]
        view = _legacy_view(rs, _fake_container(), MagicMock())
        assert view["lifecycle"]["healthy_components"] == 6
        assert view["lifecycle"]["total_components"] == 8

    def test_policy_engine_aliases_policy(self):
        rs = _hex_runtime_stats()
        view = _legacy_view(rs, _fake_container(), MagicMock())
        assert view["policy_engine"] == rs["policy"]
        assert view["policy_engine"]["constitution_version"] == "v1"

    def test_admission_aliases_admission_control(self):
        rs = _hex_runtime_stats()
        view = _legacy_view(rs, _fake_container(), MagicMock())
        assert view["admission"] == rs["admission_control"]

    def test_persistence_rollup_counts_persist_blocks(self):
        view = _legacy_view(_hex_runtime_stats(), _fake_container(), MagicMock())
        persist = view["persistence"]
        # persist_world / persist_cases / persist_verifier all healthy
        assert persist["total_stores"] == 3
        assert persist["healthy_stores"] == 3
        assert persist["io_in_flight"] == 0

    def test_feeds_view_uses_scheduler_get_status(self):
        feeds_status = {"enabled": True, "running": True, "feeds": {
            "weather": {"active": True, "error_count": 0},
        }}
        container = _fake_container(feeds_status=feeds_status)
        view = _legacy_view(_hex_runtime_stats(), container, MagicMock())
        assert view["feeds"] == feeds_status
        # And sys_feeds derivation must see something non-unwired.
        assert view["feeds"]["feeds"]["weather"]["active"] is True


# ═══════════════════════════════════════════════════════════════
# NEWS-001/002/003 — _enrich_from_chroma rewrite (6 tests)
# ═══════════════════════════════════════════════════════════════


class TestNEWSEnrichFromChroma:
    """NEWS-001/002/003: the feeds enrichment must use a real tail window,
    real freshness, real items_count, and real state promotion."""

    def _rss_source(self, interval_min: int = 60) -> dict:
        return {
            "id": "rss_test",
            "name": "Test RSS",
            "type": "rss",
            "state": "idle",
            "source_class": "live",
            "interval_min": interval_min,
            "freshness_s": None,
            "last_success_at": None,
            "last_error": None,
            "items_count": 0,
            "latest_value": None,
            "latest_items": [],
        }

    def test_news_001_fetches_large_window(self):
        """NEWS-001: enrichment must request > 5 rows so items_count is real."""
        now = time.time()
        entries = [
            (f"doc {i}", "rss_test", _iso(now - i * 60))
            for i in range(50)
        ]
        col = _chroma_mock(entries)
        source = self._rss_source()

        _enrich_from_chroma(source, col)

        # The call must have used a large limit — never 5.
        call = col.get.call_args
        limit = call.kwargs.get("limit", call.args[0] if call.args else None)
        assert limit is not None and limit >= 100, \
            f"NEWS-001: limit must be >= 100, got {limit}"
        assert source["items_count"] == 50

    def test_news_002_freshness_from_newest_item_not_max(self):
        """NEWS-002: freshness must come from the newest item after desc sort,
        not from max() of a stale insertion-order window."""
        now = time.time()
        # Insert in wrong (old first) order: a naive max() over the first
        # limit=5 slice would miss the newer row at the end.
        entries = [
            ("old1", "rss_test", _iso(now - 3600)),
            ("old2", "rss_test", _iso(now - 3000)),
            ("old3", "rss_test", _iso(now - 2400)),
            ("old4", "rss_test", _iso(now - 1800)),
            ("old5", "rss_test", _iso(now - 1200)),
            ("newest", "rss_test", _iso(now - 10)),
        ]
        col = _chroma_mock(entries)
        source = self._rss_source(interval_min=60)

        _enrich_from_chroma(source, col)

        assert source["items_count"] == 6
        assert source["freshness_s"] is not None
        assert source["freshness_s"] < 60, \
            f"freshness must reflect the 10-second-old newest item, got {source['freshness_s']}"
        # latest_items must start with the newest doc, not an old one.
        assert source["latest_items"][0]["title"] == "newest"

    def test_news_002_items_count_not_capped_at_five(self):
        now = time.time()
        entries = [
            (f"doc {i}", "rss_test", _iso(now - i * 10))
            for i in range(12)
        ]
        col = _chroma_mock(entries)
        source = self._rss_source()

        _enrich_from_chroma(source, col)

        assert source["items_count"] == 12, \
            f"items_count must reflect full result, got {source['items_count']}"

    def test_news_003_promotes_idle_to_active_when_fresh(self):
        now = time.time()
        entries = [("fresh", "rss_test", _iso(now - 30))]
        col = _chroma_mock(entries)
        source = self._rss_source(interval_min=60)  # interval_s = 3600

        _enrich_from_chroma(source, col)

        assert source["state"] == "active", \
            f"fresh row with 60m interval should promote to active, got {source['state']}"

    def test_news_003_promotes_to_stale_when_too_old(self):
        now = time.time()
        # interval_min=5 → interval_s=300 → stale threshold = 600s.
        # 2000s old → must become stale.
        entries = [("old", "rss_test", _iso(now - 2000))]
        col = _chroma_mock(entries)
        source = self._rss_source(interval_min=5)

        _enrich_from_chroma(source, col)

        assert source["state"] == "stale", \
            f"2000s-old row with 5m interval should be stale, got {source['state']}"

    def test_news_003_terminal_states_are_preserved(self):
        """NEWS-003: unwired/framework/failed must never be overwritten."""
        now = time.time()
        entries = [("fresh", "rss_test", _iso(now - 10))]
        col = _chroma_mock(entries)

        for terminal in ("unwired", "framework", "failed"):
            source = self._rss_source(interval_min=60)
            source["state"] = terminal

            _enrich_from_chroma(source, col)

            assert source["state"] == terminal, \
                f"terminal state {terminal} must not be overwritten"


# ═══════════════════════════════════════════════════════════════
# WIRE-001 — /hologram?token=<api_key> bootstrap (4 tests)
# ═══════════════════════════════════════════════════════════════


class TestWIRE001TokenBootstrap:
    """WIRE-001: /hologram?token=<api_key> must mint a waggle_session cookie
    and 303-redirect to /hologram. Wrong/missing token must not leak."""

    def _make_app_client(self, api_key: str = "TEST_API_KEY_123"):
        """Build a minimal FastAPI app carrying only the hologram router,
        with a fake container override for get_container."""
        from fastapi import FastAPI
        from waggledance.adapters.http.deps import get_container

        app = FastAPI()
        app.include_router(hologram.router)

        container = _fake_container(api_key=api_key)
        app.dependency_overrides[get_container] = lambda: container
        return TestClient(app, follow_redirects=False)

    def test_wire_001_right_token_sets_cookie_and_redirects(self):
        client = self._make_app_client(api_key="SECRET_TOKEN_1234")
        r = client.get("/hologram?token=SECRET_TOKEN_1234")

        assert r.status_code == 303, \
            f"right token must return 303 redirect, got {r.status_code}"
        assert r.headers.get("location") == "/hologram"
        set_cookie = r.headers.get("set-cookie", "")
        assert "waggle_session=" in set_cookie, \
            f"right token must mint waggle_session cookie, got {set_cookie!r}"
        assert "HttpOnly" in set_cookie
        assert "strict" in set_cookie.lower()
        assert "Path=/" in set_cookie

    def test_wire_001_wrong_token_serves_html_without_leak(self):
        client = self._make_app_client(api_key="SECRET_TOKEN_1234")
        r = client.get("/hologram?token=WRONG_VALUE")

        # Must NOT redirect, must NOT leak whether the token was wrong,
        # must serve the HTML page exactly like no token at all.
        assert r.status_code == 200
        assert "waggle_session=" not in r.headers.get("set-cookie", "")
        assert r.headers.get("content-type", "").startswith("text/html")

    def test_wire_001_missing_token_serves_html(self):
        client = self._make_app_client()
        r = client.get("/hologram")

        assert r.status_code == 200
        assert "waggle_session=" not in r.headers.get("set-cookie", "")
        assert r.headers.get("content-type", "").startswith("text/html")

    def test_wire_001_cookie_is_valid_for_session_validation(self):
        """End-to-end: cookie minted by WIRE-001 must actually pass
        auth_session.validate_session afterwards."""
        client = self._make_app_client(api_key="SECRET_TOKEN_1234")
        r = client.get("/hologram?token=SECRET_TOKEN_1234")
        assert r.status_code == 303

        # Extract the session id from the Set-Cookie header.
        import re
        m = re.search(r"waggle_session=([^;]+)", r.headers.get("set-cookie", ""))
        assert m, "waggle_session cookie missing from response"
        sid = m.group(1)
        assert auth_session.validate_session(sid) is True, \
            "cookie minted by WIRE-001 must validate as a live session"
