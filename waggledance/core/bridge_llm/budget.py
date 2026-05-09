# SPDX-License-Identifier: Apache-2.0
"""R20.2 — per-day + per-injection-point budget tracking.

Reads ``<AGENT_BRIDGE_RUNTIME_ROOT>/llm_budget.json`` if present,
otherwise applies safe defaults from the active solver profile.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class BudgetExhausted(Exception):
    """Raised when a call would exceed configured budget."""


@dataclass
class BudgetState:
    """Live counters reset daily."""

    day_start_ts: float
    calls_today: int = 0
    cents_today: int = 0
    by_injection_point: dict[str, dict[str, int]] = field(
        default_factory=dict
    )


@dataclass
class BudgetConfig:
    """R20 master prompt §2.5 fields."""

    max_calls_per_day: int = 10000
    max_cents_per_day: int = 0
    max_calls_per_injection_point: int = 0  # 0 = no limit
    max_cents_per_injection_point: int = 0
    degradation_strategy_when_budget_low: str = "heuristic-only"
    telemetry_path: str | None = None
    alert_thresholds: dict[str, int] = field(default_factory=dict)


def _seconds_to_midnight_utc(now: float) -> float:
    """Number of seconds until next UTC midnight."""
    secs_since_midnight = now % 86400
    return 86400 - secs_since_midnight


class BudgetTracker:
    """Thread-safe budget counter.

    The tracker is intentionally process-local — daily counters reset
    on the next midnight UTC after the process started, NOT a persisted
    counter. For a multi-process deployment a follow-up will move
    state to a JSON file under AGENT_BRIDGE_RUNTIME_ROOT.
    """

    def __init__(self, config: BudgetConfig | None = None):
        self._lock = threading.Lock()
        self._config = config or BudgetConfig()
        self._state = BudgetState(day_start_ts=time.time())

    @property
    def config(self) -> BudgetConfig:
        return self._config

    def state_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "day_start_ts": self._state.day_start_ts,
                "calls_today": self._state.calls_today,
                "cents_today": self._state.cents_today,
                "by_injection_point": {
                    k: dict(v) for k, v in self._state.by_injection_point.items()
                },
            }

    def _maybe_roll_day(self, now: float) -> None:
        """If we've crossed UTC midnight since day_start, reset."""
        if now - self._state.day_start_ts > 86400:
            self._state.day_start_ts = now
            self._state.calls_today = 0
            self._state.cents_today = 0
            self._state.by_injection_point = {}

    def can_spend(self, injection_point: str, cents: int = 0) -> bool:
        """Read-only check — does NOT increment counters."""
        with self._lock:
            self._maybe_roll_day(time.time())
            cfg = self._config
            if cfg.max_calls_per_day and \
                    self._state.calls_today >= cfg.max_calls_per_day:
                return False
            if cfg.max_cents_per_day and \
                    self._state.cents_today + cents > cfg.max_cents_per_day:
                return False
            ip = self._state.by_injection_point.get(injection_point, {})
            calls_ip = ip.get("calls", 0)
            cents_ip = ip.get("cents", 0)
            if cfg.max_calls_per_injection_point and \
                    calls_ip >= cfg.max_calls_per_injection_point:
                return False
            if cfg.max_cents_per_injection_point and \
                    cents_ip + cents > cfg.max_cents_per_injection_point:
                return False
        return True

    def record(self, injection_point: str, cents: int = 0) -> None:
        """Mark a call done; raises BudgetExhausted if it shouldn't have happened."""
        with self._lock:
            self._maybe_roll_day(time.time())
            self._state.calls_today += 1
            self._state.cents_today += cents
            ip = self._state.by_injection_point.setdefault(
                injection_point, {"calls": 0, "cents": 0}
            )
            ip["calls"] += 1
            ip["cents"] += cents
            cfg = self._config
            if cfg.max_calls_per_day and \
                    self._state.calls_today > cfg.max_calls_per_day:
                raise BudgetExhausted(
                    f"calls_today={self._state.calls_today} exceeded "
                    f"max_calls_per_day={cfg.max_calls_per_day}"
                )
            if cfg.max_cents_per_day and \
                    self._state.cents_today > cfg.max_cents_per_day:
                raise BudgetExhausted(
                    f"cents_today={self._state.cents_today} exceeded "
                    f"max_cents_per_day={cfg.max_cents_per_day}"
                )


def load_budget_config(path: Path | str | None = None) -> BudgetConfig:
    """Load BudgetConfig from JSON. Returns safe defaults if no path."""
    if path is None:
        return BudgetConfig()
    p = Path(path)
    if not p.is_file():
        return BudgetConfig()
    raw = json.loads(p.read_text(encoding="utf-8"))
    return BudgetConfig(
        max_calls_per_day=int(raw.get("max_calls_per_day", 10000)),
        max_cents_per_day=int(raw.get("max_cents_per_day", 0)),
        max_calls_per_injection_point=int(
            raw.get("max_calls_per_injection_point", 0)
        ),
        max_cents_per_injection_point=int(
            raw.get("max_cents_per_injection_point", 0)
        ),
        degradation_strategy_when_budget_low=str(
            raw.get("degradation_strategy_when_budget_low", "heuristic-only")
        ),
        telemetry_path=raw.get("telemetry_path"),
        alert_thresholds=dict(raw.get("alert_thresholds") or {}),
    )
