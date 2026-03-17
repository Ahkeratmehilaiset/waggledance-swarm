"""
WaggleDance Learning Ledger — Phase 4
Append-only JSONL log for all learning events.

Every mutation in the learning pipeline (fact stored, correction, canary
promotion, rollback) is recorded as a timestamped entry.
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class LedgerEntry:
    """A single immutable learning event."""

    event_type: str  # "fact_stored", "correction", "promotion", "canary", "rollback"
    timestamp: float
    agent_id: str = ""
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "agent_id": self.agent_id,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LedgerEntry":
        return cls(
            event_type=d.get("event_type", ""),
            timestamp=d.get("timestamp", 0.0),
            agent_id=d.get("agent_id", ""),
            details=d.get("details", {}),
        )


class LearningLedger:
    """Append-only JSONL ledger for learning events.

    Usage::

        ledger = LearningLedger("data/learning_ledger.jsonl")
        ledger.log("fact_stored", agent_id="bee_health", fact="varroa threshold=3%")
        entries = ledger.query(event_type="fact_stored", limit=10)
    """

    def __init__(self, path: str = "data/learning_ledger.jsonl"):
        self._path = path

    # ───────────────────────────────────────────────────────────
    # Write
    # ───────────────────────────────────────────────────────────

    def log(self, event_type: str, agent_id: str = "", **details) -> LedgerEntry:
        """Append an event to the ledger.  Returns the entry."""
        entry = LedgerEntry(
            event_type=event_type,
            timestamp=time.time(),
            agent_id=agent_id,
            details=details,
        )
        self._append(entry)
        return entry

    def _append(self, entry: LedgerEntry) -> None:
        parent = os.path.dirname(self._path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")

    # ───────────────────────────────────────────────────────────
    # Read
    # ───────────────────────────────────────────────────────────

    def _read_all(self) -> list[LedgerEntry]:
        if not os.path.isfile(self._path):
            return []
        entries: list[LedgerEntry] = []
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(LedgerEntry.from_dict(json.loads(line)))
                except (json.JSONDecodeError, KeyError) as exc:
                    log.warning("Skipping corrupt ledger line: %s", exc)
        return entries

    def query(
        self,
        event_type: str | None = None,
        since: float | None = None,
        limit: int = 100,
    ) -> list[LedgerEntry]:
        """Read entries filtered by type and/or time."""
        entries = self._read_all()
        if event_type is not None:
            entries = [e for e in entries if e.event_type == event_type]
        if since is not None:
            entries = [e for e in entries if e.timestamp >= since]
        return entries[:limit]

    def count(self, event_type: str | None = None) -> int:
        """Count entries, optionally filtered by type."""
        if event_type is None:
            return len(self._read_all())
        return sum(1 for e in self._read_all() if e.event_type == event_type)

    # ── v2.0: Autonomy learning events ──────────────────────────

    def log_case_trajectory(self, case_id: str, quality_grade: str,
                             intent: str, capability: str = "") -> None:
        """Log a case trajectory grading event."""
        self.log(
            event_type=f"case:{quality_grade}",
            agent_id=capability or "autonomy",
            case_id=case_id,
            intent=intent,
            quality_grade=quality_grade,
        )

    def log_specialist_training(self, model_name: str, accuracy: float,
                                 sample_count: int) -> None:
        """Log a specialist model training event."""
        self.log(
            event_type="specialist_training",
            agent_id=model_name,
            accuracy=accuracy,
            sample_count=sample_count,
        )
