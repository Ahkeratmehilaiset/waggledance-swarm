# SPDX-License-Identifier: Apache-2.0
"""Tests for the in-runtime advisory snapshot refresh ticker."""
from __future__ import annotations

import asyncio

from waggledance.adapters.feeds.advisory_refresh_ticker import (
    DEFAULT_INTERVAL_SECONDS,
    AdvisoryRefreshTicker,
)


def _recording_refreshers(calls: dict, *, fail: set[str] = frozenset()):
    def make(name):
        def refresher(**kwargs):
            if name in fail:
                raise RuntimeError(f"{name} boom")
            calls.setdefault(name, []).append(kwargs)
            return {"result_marker": "OK", "vertical": name}
        return refresher
    return {name: make(name) for name in ("eng01", "air01", "eng06")}


def _full_config() -> dict:
    return {
        "enabled": True,
        "interval_seconds": 1,
        "eng01": {"url": "https://prices.example.test/feed.json",
                  "allowed_private_hosts": ["prices.example.test"]},
        "air01": {"url": "https://aq.example.test/air"},
        "eng06": {"burn_log_path": "data/eng06/burn_log.json"},
    }


def test_unconfigured_ticker_does_not_start():
    ticker = AdvisoryRefreshTicker({}, refreshers=_recording_refreshers({}))

    assert ticker.configured_verticals == ()
    assert asyncio.run(ticker.start()) is False
    assert not ticker.is_running


def test_refresh_once_runs_every_configured_vertical():
    calls: dict = {}
    ticker = AdvisoryRefreshTicker(
        _full_config(), refreshers=_recording_refreshers(calls)
    )

    ok = asyncio.run(ticker.refresh_once())

    assert ok == 3
    assert ticker.configured_verticals == ("air01", "eng01", "eng06")
    assert ticker.stats.last_markers == {
        "eng01": "OK", "air01": "OK", "eng06": "OK",
    }
    assert ticker.stats.refreshes_failed == 0


def test_each_job_binds_its_own_source():
    # Regression: closure late-binding would make eng01 fetch air01's url.
    calls: dict = {}
    ticker = AdvisoryRefreshTicker(
        _full_config(), refreshers=_recording_refreshers(calls)
    )

    asyncio.run(ticker.refresh_once())

    assert calls["eng01"][0]["url"] == "https://prices.example.test/feed.json"
    assert calls["eng01"][0]["allowed_private_hosts"] == ("prices.example.test",)
    assert calls["air01"][0]["url"] == "https://aq.example.test/air"
    assert calls["eng06"][0]["burn_log_path"] == "data/eng06/burn_log.json"


def test_one_failing_vertical_never_aborts_the_cycle():
    calls: dict = {}
    ticker = AdvisoryRefreshTicker(
        _full_config(), refreshers=_recording_refreshers(calls, fail={"eng01"})
    )

    ok = asyncio.run(ticker.refresh_once())

    assert ok == 2
    assert "eng01" in ticker.stats.last_errors
    assert "RuntimeError" in ticker.stats.last_errors["eng01"]
    assert set(calls) == {"air01", "eng06"}
    assert ticker.stats.refreshes_failed == 1
    assert ticker.stats.refreshes_ok == 2


def test_start_stop_lifecycle_runs_cycles():
    calls: dict = {}
    config = _full_config()
    config["interval_seconds"] = 0.01

    async def scenario() -> int:
        ticker = AdvisoryRefreshTicker(
            config, refreshers=_recording_refreshers(calls)
        )
        # interval clamps to >= 1s, so wait on the FIRST immediate cycle only.
        assert await ticker.start() is True
        assert ticker.is_running
        assert await ticker.start() is False  # already running
        for _ in range(100):
            if ticker.stats.cycles_total >= 1:
                break
            await asyncio.sleep(0.01)
        assert await ticker.stop() is True
        assert not ticker.is_running
        assert await ticker.stop() is False  # already stopped
        return ticker.stats.cycles_total

    cycles = asyncio.run(scenario())
    assert cycles >= 1
    assert calls["eng01"]


def test_garbage_interval_falls_back_to_default():
    ticker = AdvisoryRefreshTicker(
        {"interval_seconds": "not-a-number"},
        refreshers=_recording_refreshers({}),
    )
    assert ticker.interval_seconds == DEFAULT_INTERVAL_SECONDS


def test_container_gating_disabled_by_default():
    from waggledance.bootstrap.container import Container

    class Settings(dict):
        def get(self, key, default=None):  # dict-style settings shim
            return super().get(key, default)

    container = object.__new__(Container)
    container.__dict__["_settings"] = Settings()
    assert container.advisory_refresh_ticker is None

    container2 = object.__new__(Container)
    container2.__dict__["_settings"] = Settings(
        advisory_refresh={"enabled": False}
    )
    assert container2.advisory_refresh_ticker is None

    container3 = object.__new__(Container)
    container3.__dict__["_settings"] = Settings(
        advisory_refresh={"enabled": True,
                          "eng06": {"burn_log_path": "data/eng06/log.json"}}
    )
    ticker = container3.advisory_refresh_ticker
    assert ticker is not None
    assert ticker.configured_verticals == ("eng06",)
