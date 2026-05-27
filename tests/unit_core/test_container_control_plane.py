# SPDX-License-Identifier: BUSL-1.1
"""Audit H51 / D2.1 regression — Container exposes control_plane_db.

Before this fix, ControlPlaneDB was instantiated only by offline
tools and tests; no production code path created
``data/control_plane.db``. The R25 decision histogram, autogrowth
scheduler, and RuntimeGapDetector all need this DB before they can
write anything. These tests pin the wiring so the cached_property
stays in place.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from waggledance.bootstrap.container import Container


class _StubSettings:
    def get_profile(self) -> str:
        return "HOME"

    def get(self, key, default=None):
        return default

    db_path = "shared_memory.db"


class _AutogrowthDisabledSettings(_StubSettings):
    def get(self, key, default=None):
        if key == "autogrowth.background_tick_enabled":
            return False
        return super().get(key, default)


class _AutogrowthCustomTickSettings(_StubSettings):
    def get(self, key, default=None):
        if key == "autogrowth.background_interval_s":
            return 7.5
        if key == "autogrowth.background_max_ticks":
            return 3
        return super().get(key, default)


class TestControlPlaneDbWiring:
    def test_stub_mode_returns_none(self):
        """Stub mode (tests/CI without persistent data dir) returns None
        so consumers can degrade gracefully."""
        c = Container(settings=_StubSettings(), stub=True)
        assert c.control_plane_db is None

    def test_non_stub_creates_real_db(self, tmp_path, monkeypatch):
        """Non-stub mode constructs ControlPlaneDB at the default path."""
        monkeypatch.chdir(tmp_path)
        c = Container(settings=_StubSettings(), stub=False)
        db = c.control_plane_db
        assert db is not None
        assert db.db_path == Path("data") / "control_plane.db"
        assert db.schema_version() >= 1
        db.close()

    def test_cached_property_returns_same_instance(self, tmp_path, monkeypatch):
        """control_plane_db is a cached_property — same instance on
        repeated access (no schema-migrate per call)."""
        monkeypatch.chdir(tmp_path)
        c = Container(settings=_StubSettings(), stub=False)
        first = c.control_plane_db
        second = c.control_plane_db
        try:
            assert first is second
        finally:
            if first is not None:
                first.close()

    def test_construction_failure_returns_none(self, monkeypatch):
        """If ControlPlaneDB construction raises (disk full, perms,
        etc.) the cached_property logs and returns None instead of
        crashing the container."""
        def _boom(*args, **kwargs):
            raise RuntimeError("simulated disk failure")

        with patch(
            "waggledance.core.storage.control_plane.ControlPlaneDB",
            side_effect=_boom,
        ):
            c = Container(settings=_StubSettings(), stub=False)
            assert c.control_plane_db is None

    def test_stub_mode_autogrowth_ticker_returns_none(self):
        c = Container(settings=_StubSettings(), stub=True)
        assert c.autogrowth_scheduler is None
        assert c.autogrowth_background_ticker is None

    def test_non_stub_builds_autogrowth_background_ticker(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.chdir(tmp_path)
        c = Container(settings=_StubSettings(), stub=False)
        try:
            ticker = c.autogrowth_background_ticker
            assert ticker is not None
            assert ticker.is_running is False
            assert ticker.interval_seconds == 30.0
            assert ticker.max_ticks_per_wake == 20
        finally:
            if c.control_plane_db is not None:
                c.control_plane_db.close()

    def test_autogrowth_background_ticker_exposes_configured_runtime_bounds(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.chdir(tmp_path)
        c = Container(settings=_AutogrowthCustomTickSettings(), stub=False)
        try:
            ticker = c.autogrowth_background_ticker
            assert ticker is not None
            assert ticker.interval_seconds == 7.5
            assert ticker.max_ticks_per_wake == 3
            assert ticker.is_running is False
        finally:
            if c.control_plane_db is not None:
                c.control_plane_db.close()

    def test_autogrowth_background_ticker_respects_disabled_flag(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.chdir(tmp_path)
        c = Container(settings=_AutogrowthDisabledSettings(), stub=False)
        try:
            assert c.autogrowth_scheduler is not None
            assert c.autogrowth_background_ticker is None
        finally:
            if c.control_plane_db is not None:
                c.control_plane_db.close()
