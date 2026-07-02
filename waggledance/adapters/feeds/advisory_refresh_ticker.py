# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Background ticker that keeps the advisory snapshots live in-runtime.

The three advisory refreshers (`eng01_advisory_refresher`,
`air01_advisory_refresher`, `eng06_advisory_refresher`) close their verticals'
fetch -> solve -> write loops, but something must CALL them periodically for
the read routes and the dashboard to serve genuinely live data. This ticker is
that caller, inside the runtime — no external cron/scheduled-task dependency.
It mirrors the `AutogrowthBackgroundTicker` lifecycle pattern (async start/stop
wrapper started from the app lifespan).

Fail-closed and additive:
- disabled unless `advisory_refresh.enabled` is true in settings (default OFF);
- a vertical is scheduled ONLY when its source is configured (`eng01.url`,
  `air01.url`, `eng06.burn_log_path`) — no config, no job, no network;
- each refresh runs in a worker thread and failures are isolated per vertical
  per cycle: one failing feed never kills the ticker or the other verticals;
- all transport/SSRF/credential guards and the sandboxed atomic snapshot
  writes live in the reused refreshers, unchanged. This module adds no new
  authority.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional

log = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 900.0


@dataclass
class AdvisoryRefreshStats:
    """Introspection counters for the refresh loop."""

    cycles_total: int = 0
    refreshes_ok: int = 0
    refreshes_failed: int = 0
    last_markers: dict[str, str] = field(default_factory=dict)
    last_errors: dict[str, str] = field(default_factory=dict)


def _default_refreshers() -> dict[str, Callable[..., dict[str, Any]]]:
    # Imported lazily so constructing a disabled ticker never pulls transports.
    from waggledance.adapters.feeds.air01_advisory_refresher import (
        refresh_air01_latest_advisory,
    )
    from waggledance.adapters.feeds.eng01_advisory_refresher import (
        refresh_eng01_latest_advisory,
    )
    from waggledance.adapters.feeds.eng06_advisory_refresher import (
        refresh_eng06_latest_advisory,
    )

    return {
        "eng01": refresh_eng01_latest_advisory,
        "air01": refresh_air01_latest_advisory,
        "eng06": refresh_eng06_latest_advisory,
    }


def _build_jobs(
    config: Mapping[str, Any],
    refreshers: Mapping[str, Callable[..., dict[str, Any]]],
) -> dict[str, Callable[[], dict[str, Any]]]:
    """Build zero-arg refresh jobs for every configured vertical."""
    jobs: dict[str, Callable[[], dict[str, Any]]] = {}

    eng01_cfg = config.get("eng01")
    if isinstance(eng01_cfg, Mapping):
        url = eng01_cfg.get("url")
        if isinstance(url, str) and url.strip():
            kwargs = _optional_kwargs(eng01_cfg, ("allowed_private_hosts",))
            jobs["eng01"] = (
                lambda url=url, kwargs=kwargs: refreshers["eng01"](url=url, **kwargs)
            )

    air01_cfg = config.get("air01")
    if isinstance(air01_cfg, Mapping):
        url = air01_cfg.get("url")
        if isinstance(url, str) and url.strip():
            kwargs = _optional_kwargs(air01_cfg, ("allowed_private_hosts",))
            jobs["air01"] = (
                lambda url=url, kwargs=kwargs: refreshers["air01"](url=url, **kwargs)
            )

    eng06_cfg = config.get("eng06")
    if isinstance(eng06_cfg, Mapping):
        path = eng06_cfg.get("burn_log_path")
        if isinstance(path, str) and path.strip():
            jobs["eng06"] = (
                lambda path=path: refreshers["eng06"](burn_log_path=path)
            )

    return jobs


def _optional_kwargs(
    cfg: Mapping[str, Any], keys: tuple[str, ...]
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    for key in keys:
        value = cfg.get(key)
        if isinstance(value, (list, tuple)) and all(
            isinstance(item, str) for item in value
        ):
            kwargs[key] = tuple(value)
    return kwargs


class AdvisoryRefreshTicker:
    """Async lifecycle wrapper that periodically refreshes advisory snapshots."""

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        refreshers: Optional[Mapping[str, Callable[..., dict[str, Any]]]] = None,
    ) -> None:
        interval = config.get("interval_seconds", DEFAULT_INTERVAL_SECONDS)
        try:
            self._interval_seconds = max(1.0, float(interval))
        except (TypeError, ValueError):
            self._interval_seconds = DEFAULT_INTERVAL_SECONDS
        self._jobs = _build_jobs(
            config, refreshers if refreshers is not None else _default_refreshers()
        )
        self._stats = AdvisoryRefreshStats()
        self._task: Optional[asyncio.Task] = None

    @property
    def stats(self) -> AdvisoryRefreshStats:
        return self._stats

    @property
    def interval_seconds(self) -> float:
        return self._interval_seconds

    @property
    def configured_verticals(self) -> tuple[str, ...]:
        return tuple(sorted(self._jobs))

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> bool:
        """Start the refresh loop. No configured verticals -> stay stopped."""
        if self.is_running:
            return False
        if not self._jobs:
            log.info("AdvisoryRefreshTicker: no verticals configured; not started")
            return False
        self._task = asyncio.create_task(
            self._run_loop(), name="advisory-refresh-ticker"
        )
        return True

    async def stop(self) -> bool:
        """Cancel and await the refresh loop task."""
        task = self._task
        self._task = None
        if task is None:
            return False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return True

    async def refresh_once(self) -> int:
        """Run one refresh cycle across all configured verticals.

        Returns the number of successful refreshes. Failures are isolated
        per vertical: one failing feed never aborts the cycle.
        """
        self._stats.cycles_total += 1
        ok = 0
        for name, job in self._jobs.items():
            try:
                result = await asyncio.to_thread(job)
                marker = ""
                if isinstance(result, Mapping):
                    marker = str(result.get("result_marker", ""))
                self._stats.last_markers[name] = marker
                self._stats.last_errors.pop(name, None)
                self._stats.refreshes_ok += 1
                ok += 1
            except Exception as exc:  # noqa: BLE001 - loop must stay alive
                self._stats.refreshes_failed += 1
                self._stats.last_errors[name] = f"{type(exc).__name__}: {exc}"
                log.warning(
                    "Advisory refresh failed for %s: %s", name, exc, exc_info=True
                )
        return ok

    async def _run_loop(self) -> None:
        while True:
            await self.refresh_once()
            await asyncio.sleep(self._interval_seconds)


__all__ = [
    "DEFAULT_INTERVAL_SECONDS",
    "AdvisoryRefreshStats",
    "AdvisoryRefreshTicker",
]
