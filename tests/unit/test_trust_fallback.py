"""Tests for TrustStore fallback behavior in Container."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest


def _make_settings(db_path="data/test.db"):
    return type("S", (), {"db_path": db_path})()


def _make_container_no_cache(settings, stub=False):
    """Create a fresh Container (bypass cached_property for repeated tests)."""
    from waggledance.bootstrap.container import Container

    return Container(settings=settings, stub=stub)


class TestTrustStoreFallback:
    def test_broken_sqlite_falls_back_to_in_memory(self):
        """Container falls back to InMemoryTrustStore when SQLiteTrustStore raises."""
        c = _make_container_no_cache(_make_settings(), stub=False)

        with patch(
            "waggledance.adapters.trust.sqlite_trust_store.SQLiteTrustStore",
            side_effect=RuntimeError("disk full"),
        ):
            store = c.trust_store

        assert store is not None
        assert type(store).__name__ == "InMemoryTrustStore"

    def test_fallback_sets_flag(self):
        """Fallback InMemoryTrustStore has _fallback_active=True."""
        c = _make_container_no_cache(_make_settings(), stub=False)

        with patch(
            "waggledance.adapters.trust.sqlite_trust_store.SQLiteTrustStore",
            side_effect=RuntimeError("disk full"),
        ):
            store = c.trust_store

        assert getattr(store, "_fallback_active", False) is True

    def test_fallback_logs_error(self, caplog):
        """Fallback logs at ERROR level, not just WARNING."""
        c = _make_container_no_cache(_make_settings(), stub=False)

        with caplog.at_level(logging.ERROR, logger="waggledance.bootstrap.container"):
            with patch(
                "waggledance.adapters.trust.sqlite_trust_store.SQLiteTrustStore",
                side_effect=RuntimeError("disk full"),
            ):
                _ = c.trust_store

        error_msgs = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(error_msgs) >= 1
        assert "NOT persist" in error_msgs[0].message

    def test_stub_mode_returns_in_memory_without_flag(self):
        """Stub mode returns InMemoryTrustStore without fallback flag."""
        c = _make_container_no_cache(_make_settings(), stub=True)
        store = c.trust_store
        assert type(store).__name__ == "InMemoryTrustStore"
        assert getattr(store, "_fallback_active", False) is False
