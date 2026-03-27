"""
Baseline Store — SQLite persistence for entity baseline values.

Baselines are the "expected normal" values for metrics:
  - hive_1.temperature → 35.0°C
  - outdoor.temperature → 15.0°C (seasonal)
  - energy.daily_kwh → 42.0

Residuals = current_value - baseline. Large residuals trigger alerts.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("waggledance.world.baselines")


@dataclass
class Baseline:
    entity_id: str
    metric_name: str
    baseline_value: float
    confidence: float
    sample_count: int
    last_updated: float
    source_type: str  # observed / inferred_by_solver / inferred_by_stats

    @property
    def key(self) -> str:
        return f"{self.entity_id}.{self.metric_name}"


class BaselineStore:
    """SQLite-backed baseline value storage with rolling updates."""

    def __init__(self, db_path: str = "data/world_baselines.db"):
        self._lock = threading.Lock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_table()

    def _create_table(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS baselines (
                entity_id       TEXT NOT NULL,
                metric_name     TEXT NOT NULL,
                baseline_value  REAL NOT NULL,
                confidence      REAL NOT NULL DEFAULT 0.5,
                sample_count    INTEGER NOT NULL DEFAULT 1,
                last_updated    REAL NOT NULL,
                source_type     TEXT NOT NULL DEFAULT 'observed',
                PRIMARY KEY (entity_id, metric_name)
            );
            CREATE INDEX IF NOT EXISTS idx_baselines_entity
                ON baselines(entity_id);
        """)
        self._conn.commit()

    def get(self, entity_id: str, metric_name: str) -> Optional[Baseline]:
        row = self._conn.execute(
            "SELECT * FROM baselines WHERE entity_id=? AND metric_name=?",
            (entity_id, metric_name),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_baseline(row)

    def get_all_for_entity(self, entity_id: str) -> List[Baseline]:
        rows = self._conn.execute(
            "SELECT * FROM baselines WHERE entity_id=?",
            (entity_id,),
        ).fetchall()
        return [self._row_to_baseline(r) for r in rows]

    def get_all(self) -> List[Baseline]:
        rows = self._conn.execute("SELECT * FROM baselines").fetchall()
        return [self._row_to_baseline(r) for r in rows]

    def upsert(
        self,
        entity_id: str,
        metric_name: str,
        value: float,
        source_type: str = "observed",
        confidence: float = 0.5,
    ) -> Baseline:
        """Insert or update a baseline with exponential moving average."""
        now = time.time()
        with self._lock:
            existing = self.get(entity_id, metric_name)
            if existing:
                # EMA with alpha=0.1 for smooth updates
                alpha = 0.1
                new_value = existing.baseline_value * (1 - alpha) + value * alpha
                new_count = existing.sample_count + 1
                new_conf = min(1.0, confidence * 0.3 + existing.confidence * 0.7)
                self._conn.execute(
                    "UPDATE baselines SET baseline_value=?, confidence=?, "
                    "sample_count=?, last_updated=?, source_type=? "
                    "WHERE entity_id=? AND metric_name=?",
                    (new_value, new_conf, new_count, now, source_type,
                     entity_id, metric_name),
                )
            else:
                new_value = value
                new_count = 1
                new_conf = confidence
                self._conn.execute(
                    "INSERT INTO baselines (entity_id, metric_name, baseline_value, "
                    "confidence, sample_count, last_updated, source_type) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (entity_id, metric_name, value, confidence, 1, now, source_type),
                )
            self._conn.commit()

        return Baseline(
            entity_id=entity_id,
            metric_name=metric_name,
            baseline_value=new_value,
            confidence=new_conf,
            sample_count=new_count,
            last_updated=now,
            source_type=source_type,
        )

    def compute_residual(self, entity_id: str, metric_name: str, current: float) -> Optional[float]:
        """Return current - baseline, or None if no baseline exists."""
        bl = self.get(entity_id, metric_name)
        if bl is None:
            return None
        return current - bl.baseline_value

    def get_baselines_dict(self) -> Dict[str, float]:
        """Return all baselines as {entity.metric: value} dict."""
        return {b.key: b.baseline_value for b in self.get_all()}

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM baselines").fetchone()
        return row[0] if row else 0

    def close(self):
        self._conn.close()

    @staticmethod
    def _row_to_baseline(row) -> Baseline:
        return Baseline(
            entity_id=row["entity_id"],
            metric_name=row["metric_name"],
            baseline_value=row["baseline_value"],
            confidence=row["confidence"],
            sample_count=row["sample_count"],
            last_updated=row["last_updated"],
            source_type=row["source_type"],
        )
