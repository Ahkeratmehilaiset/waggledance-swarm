# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Event log adapter — wraps legacy LearningLedger for autonomy event logging.

Extends the learning ledger with structured event types for case trajectories,
specialist model training, and autonomy lifecycle events.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_LearningLedger = None


def _get_legacy_class():
    global _LearningLedger
    if _LearningLedger is None:
        try:
            from core.learning_ledger import LearningLedger
            _LearningLedger = LearningLedger
        except ImportError:
            _LearningLedger = None
    return _LearningLedger


@dataclass
class EventLogEntry:
    """Structured autonomy event log entry."""
    event_type: str
    source: str
    timestamp: float = field(default_factory=time.time)
    goal_id: str = ""
    capability_id: str = ""
    quality_grade: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


class EventLogAdapter:
    """Wraps legacy LearningLedger with structured autonomy event support.

    The legacy ledger stores JSONL entries with event_type/agent_id/details.
    This adapter maps autonomy events to that format while providing
    richer querying and aggregation.
    """

    def __init__(self, legacy_ledger=None, path: str = "data/learning_ledger.jsonl"):
        if legacy_ledger is not None:
            self._ledger = legacy_ledger
        else:
            cls = _get_legacy_class()
            self._ledger = cls(path=path) if cls else None
        self._lock = threading.Lock()
        self._buffer: List[EventLogEntry] = []
        self._max_buffer = 1000

    def log_event(self, event_type: str, source: str = "autonomy",
                  goal_id: str = "", capability_id: str = "",
                  quality_grade: str = "", **details) -> None:
        """Log an autonomy event to both the local buffer and legacy ledger."""
        entry = EventLogEntry(
            event_type=event_type,
            source=source,
            goal_id=goal_id,
            capability_id=capability_id,
            quality_grade=quality_grade,
            details=details,
        )

        with self._lock:
            self._buffer.append(entry)
            if len(self._buffer) > self._max_buffer:
                self._buffer = self._buffer[-self._max_buffer:]

        if self._ledger:
            try:
                self._ledger.log(
                    event_type=event_type,
                    agent_id=source,
                    goal_id=goal_id,
                    capability_id=capability_id,
                    quality_grade=quality_grade,
                    **details,
                )
            except Exception as exc:
                logger.debug("Legacy ledger write failed: %s", exc)

    def log_case_trajectory(self, case_id: str, quality_grade: str,
                            intent: str = "", capability: str = "",
                            goal_id: str = "") -> None:
        """Log a case trajectory creation event."""
        self.log_event(
            event_type=f"case:{quality_grade}",
            goal_id=goal_id,
            capability_id=capability,
            quality_grade=quality_grade,
            case_id=case_id,
            intent=intent,
        )
        if self._ledger and hasattr(self._ledger, "log_case_trajectory"):
            try:
                self._ledger.log_case_trajectory(case_id, quality_grade, intent, capability)
            except Exception:
                pass

    def log_specialist_training(self, model_name: str, accuracy: float,
                                sample_count: int, status: str = "trained") -> None:
        """Log a specialist model training event."""
        self.log_event(
            event_type="specialist_training",
            capability_id=model_name,
            accuracy=accuracy,
            sample_count=sample_count,
            status=status,
        )
        if self._ledger and hasattr(self._ledger, "log_specialist_training"):
            try:
                self._ledger.log_specialist_training(model_name, accuracy, sample_count)
            except Exception:
                pass

    def log_policy_decision(self, action_id: str, decision: str,
                            risk_score: float = 0.0, reason: str = "") -> None:
        """Log a policy engine decision."""
        self.log_event(
            event_type=f"policy:{decision}",
            action_id=action_id,
            risk_score=risk_score,
            reason=reason,
        )

    def query(self, event_type: str = None, since: float = None,
              limit: int = 100) -> List[EventLogEntry]:
        """Query local buffer for events."""
        with self._lock:
            results = list(reversed(self._buffer))
        if event_type:
            results = [e for e in results if e.event_type == event_type]
        if since:
            results = [e for e in results if e.timestamp >= since]
        return results[:limit]

    def count_by_type(self, since: float = None) -> Dict[str, int]:
        """Count events by type in the local buffer."""
        with self._lock:
            entries = self._buffer if not since else [
                e for e in self._buffer if e.timestamp >= since
            ]
        counts: Dict[str, int] = {}
        for e in entries:
            counts[e.event_type] = counts.get(e.event_type, 0) + 1
        return counts

    def get_quality_distribution(self, since: float = None) -> Dict[str, int]:
        """Count case events by quality grade."""
        with self._lock:
            entries = self._buffer if not since else [
                e for e in self._buffer if e.timestamp >= since
            ]
        dist: Dict[str, int] = {}
        for e in entries:
            if e.quality_grade:
                dist[e.quality_grade] = dist.get(e.quality_grade, 0) + 1
        return dist

    def stats(self) -> Dict[str, Any]:
        """Summary statistics."""
        with self._lock:
            total = len(self._buffer)
            entries = list(self._buffer)
        counts: Dict[str, int] = {}
        dist: Dict[str, int] = {}
        for e in entries:
            counts[e.event_type] = counts.get(e.event_type, 0) + 1
            if e.quality_grade:
                dist[e.quality_grade] = dist.get(e.quality_grade, 0) + 1
        return {
            "total_entries": total,
            "event_types": counts,
            "quality_distribution": dist,
            "legacy_ledger_available": self._ledger is not None,
        }
