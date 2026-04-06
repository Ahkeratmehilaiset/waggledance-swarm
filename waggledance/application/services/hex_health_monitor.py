"""Hex Health Monitor — quarantine, cooldown, self-heal for hex cells."""

from __future__ import annotations

import logging
import time
import threading
from typing import Any

from waggledance.core.domain.hex_mesh import HexCellHealth

log = logging.getLogger(__name__)


class HexHealthMonitor:
    """Monitors cell health, quarantines unhealthy cells, probes for recovery."""

    def __init__(
        self,
        error_threshold: int = 3,
        timeout_threshold: int = 2,
        cooldown_s: float = 300.0,
        self_heal_probe_enabled: bool = True,
    ):
        self._error_threshold = error_threshold
        self._timeout_threshold = timeout_threshold
        self._cooldown_s = cooldown_s
        self._probe_enabled = self_heal_probe_enabled
        self._lock = threading.RLock()
        self._cells: dict[str, HexCellHealth] = {}

        # Counters
        self._quarantine_events = 0
        self._recovery_events = 0
        self._probe_events = 0

    def _ensure_cell(self, cell_id: str) -> HexCellHealth:
        if cell_id not in self._cells:
            self._cells[cell_id] = HexCellHealth(cell_id=cell_id)
        return self._cells[cell_id]

    def record_success(self, cell_id: str) -> None:
        with self._lock:
            h = self._ensure_cell(cell_id)
            h.recent_success_count += 1
            h.total_queries += 1
            h.last_success_ts = time.time()
            # Decay errors on success
            if h.recent_error_count > 0:
                h.recent_error_count = max(0, h.recent_error_count - 1)
            if h.recent_timeout_count > 0:
                h.recent_timeout_count = max(0, h.recent_timeout_count - 1)

    def record_error(self, cell_id: str) -> None:
        with self._lock:
            h = self._ensure_cell(cell_id)
            h.recent_error_count += 1
            h.total_queries += 1
            self._check_quarantine(h)

    def record_timeout(self, cell_id: str) -> None:
        with self._lock:
            h = self._ensure_cell(cell_id)
            h.recent_timeout_count += 1
            h.total_queries += 1
            self._check_quarantine(h)

    def _check_quarantine(self, h: HexCellHealth) -> None:
        """Quarantine cell if error/timeout thresholds exceeded."""
        if h.is_quarantined:
            return
        if (
            h.recent_error_count >= self._error_threshold
            or h.recent_timeout_count >= self._timeout_threshold
        ):
            h.quarantine_until = time.time() + self._cooldown_s
            h.cooldown_probe_pending = self._probe_enabled
            self._quarantine_events += 1
            log.warning(
                "Cell %s quarantined until %.0f (errors=%d, timeouts=%d)",
                h.cell_id,
                h.quarantine_until,
                h.recent_error_count,
                h.recent_timeout_count,
            )

    def is_healthy(self, cell_id: str) -> bool:
        with self._lock:
            h = self._cells.get(cell_id)
            if h is None:
                return True  # Unknown cells are assumed healthy
            return not h.is_quarantined

    def get_health(self, cell_id: str) -> HexCellHealth:
        with self._lock:
            return self._ensure_cell(cell_id)

    def probe_recovery(self, cell_id: str, success: bool) -> bool:
        """Probe a quarantined cell. Returns True if cell recovered."""
        with self._lock:
            h = self._cells.get(cell_id)
            if h is None:
                return False
            self._probe_events += 1
            if success:
                h.quarantine_until = 0.0
                h.cooldown_probe_pending = False
                h.recent_error_count = 0
                h.recent_timeout_count = 0
                h.recent_success_count = 1
                self._recovery_events += 1
                log.info("Cell %s recovered from quarantine", cell_id)
                return True
            # Extend quarantine
            h.quarantine_until = time.time() + self._cooldown_s
            return False

    def get_quarantined_cells(self) -> list[str]:
        with self._lock:
            return [
                cid for cid, h in self._cells.items() if h.is_quarantined
            ]

    def get_cells_needing_probe(self) -> list[str]:
        with self._lock:
            now = time.time()
            return [
                cid
                for cid, h in self._cells.items()
                if h.cooldown_probe_pending and h.quarantine_until <= now
            ]

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "tracked_cells": len(self._cells),
                "quarantined_cells": len(self.get_quarantined_cells()),
                "quarantine_events": self._quarantine_events,
                "recovery_events": self._recovery_events,
                "probe_events": self._probe_events,
            }
