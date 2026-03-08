"""
MAGMA Layer 5: Trust & Reputation Engine
==========================================
Multi-signal trust scoring that aggregates hallucination rate, validation ratio,
consensus participation, correction rate, fact production, and temporal freshness
into a unified composite reputation score per agent.

Storage: SQLite table `trust_signals` in audit_log.db (same DB as provenance).
"""

import logging
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

log = logging.getLogger("waggledance.trust")


@dataclass
class TrustSignal:
    name: str
    value: float
    weight: float = 1.0
    timestamp: float = field(default_factory=time.time)


class AgentReputation:
    """Per-agent reputation container with multi-dimensional scoring."""

    SIGNAL_WEIGHTS = {
        "hallucination_rate": 0.30,
        "validation_ratio": 0.20,
        "consensus_participation": 0.15,
        "correction_rate": 0.15,
        "fact_production": 0.10,
        "temporal_freshness": 0.10,
    }

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.signals: Dict[str, List[TrustSignal]] = {}

    def add_signal(self, signal: TrustSignal):
        if signal.name not in self.signals:
            self.signals[signal.name] = []
        lst = self.signals[signal.name]
        lst.append(signal)
        # Rolling window: keep last 200 per type
        if len(lst) > 200:
            self.signals[signal.name] = lst[-200:]

    @property
    def composite_score(self) -> float:
        if not self.signals:
            return 0.0
        total_weight = 0.0
        weighted_sum = 0.0
        for name, sigs in self.signals.items():
            if not sigs:
                continue
            avg = sum(s.value for s in sigs) / len(sigs)
            w = self.SIGNAL_WEIGHTS.get(name, 0.10)
            weighted_sum += avg * w
            total_weight += w
        if total_weight == 0:
            return 0.0
        return weighted_sum / total_weight

    @property
    def domain_scores(self) -> Dict[str, float]:
        result = {}
        for name, sigs in self.signals.items():
            if sigs:
                result[name] = sum(s.value for s in sigs) / len(sigs)
        return result

    @property
    def trend(self) -> str:
        # Use all signals combined, compare last 50 vs previous 50
        all_sigs = []
        for sigs in self.signals.values():
            all_sigs.extend(sigs)
        all_sigs.sort(key=lambda s: s.timestamp)
        if len(all_sigs) < 20:
            return "stable"
        mid = len(all_sigs) // 2
        recent_avg = sum(s.value for s in all_sigs[mid:]) / len(all_sigs[mid:])
        older_avg = sum(s.value for s in all_sigs[:mid]) / len(all_sigs[:mid])
        diff = recent_avg - older_avg
        if diff > 0.05:
            return "improving"
        elif diff < -0.05:
            return "declining"
        return "stable"

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "composite_score": round(self.composite_score, 4),
            "domain_scores": {k: round(v, 4) for k, v in self.domain_scores.items()},
            "trend": self.trend,
            "signal_counts": {k: len(v) for k, v in self.signals.items()},
        }


class TrustEngine:
    """Multi-signal trust and reputation scoring engine."""

    def __init__(self, audit_log, provenance=None, agent_levels=None):
        self._audit = audit_log
        self._provenance = provenance
        self._agent_levels = agent_levels
        self._conn = sqlite3.connect(audit_log.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_table()
        self._cache: Dict[str, AgentReputation] = {}

    def _create_table(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS trust_signals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id    TEXT    NOT NULL,
                signal_name TEXT    NOT NULL,
                value       REAL   NOT NULL,
                timestamp   REAL   NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_trust_agent ON trust_signals(agent_id);
            CREATE INDEX IF NOT EXISTS idx_trust_name ON trust_signals(signal_name);
        """)
        self._conn.commit()

    def record_signal(self, agent_id: str, signal_name: str, value: float):
        """Append a trust signal to SQLite."""
        ts = time.time()
        self._conn.execute(
            "INSERT INTO trust_signals (agent_id, signal_name, value, timestamp) "
            "VALUES (?, ?, ?, ?)",
            (agent_id, signal_name, value, ts)
        )
        self._conn.commit()
        # Update cache
        if agent_id in self._cache:
            self._cache[agent_id].add_signal(
                TrustSignal(signal_name, value, timestamp=ts))

    def compute_reputation(self, agent_id: str) -> AgentReputation:
        """Build full reputation from stored signals + live data."""
        rep = AgentReputation(agent_id)

        # Load stored signals (last 200 per type)
        rows = self._conn.execute(
            "SELECT signal_name, value, timestamp FROM trust_signals "
            "WHERE agent_id=? ORDER BY timestamp DESC LIMIT 1200",
            (agent_id,)
        ).fetchall()
        for r in reversed(rows):
            rep.add_signal(TrustSignal(r[0], r[1], timestamp=r[2]))

        # Enrich from agent_levels if available
        if self._agent_levels:
            try:
                stats = self._agent_levels.get_stats_for_trust(agent_id)
                if stats:
                    hr = stats.get("hallucination_rate", 0.0)
                    # Invert: low hallucination = high trust signal
                    rep.add_signal(TrustSignal("hallucination_rate", 1.0 - hr))
                    cr = stats.get("correction_rate", 0.0)
                    rep.add_signal(TrustSignal("correction_rate", 1.0 - cr))
            except Exception:
                pass

        # Enrich from provenance if available
        if self._provenance:
            try:
                contribs = self._provenance.get_agent_contributions(agent_id)
                validations = contribs.get("validated", [])
                if validations:
                    agrees = sum(1 for v in validations if v.get("verdict") == "agree")
                    ratio = agrees / len(validations) if validations else 0.0
                    rep.add_signal(TrustSignal("validation_ratio", ratio))
            except Exception:
                pass

        self._cache[agent_id] = rep
        return rep

    def get_all_reputations(self) -> Dict[str, float]:
        """Return composite scores for all known agents."""
        agents = self._conn.execute(
            "SELECT DISTINCT agent_id FROM trust_signals"
        ).fetchall()
        result = {}
        for (aid,) in agents:
            rep = self.compute_reputation(aid)
            result[aid] = round(rep.composite_score, 4)
        return result

    def get_ranking(self) -> List[dict]:
        """Agents sorted by composite score descending."""
        scores = self.get_all_reputations()
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [{"agent_id": aid, "composite_score": sc} for aid, sc in ranked]

    def get_domain_experts(self, domain: str) -> List[dict]:
        """Agents ranked by a specific signal domain."""
        agents = self._conn.execute(
            "SELECT DISTINCT agent_id FROM trust_signals WHERE signal_name=?",
            (domain,)
        ).fetchall()
        result = []
        for (aid,) in agents:
            rows = self._conn.execute(
                "SELECT value FROM trust_signals "
                "WHERE agent_id=? AND signal_name=? ORDER BY timestamp DESC LIMIT 50",
                (aid, domain)
            ).fetchall()
            if rows:
                avg = sum(r[0] for r in rows) / len(rows)
                result.append({"agent_id": aid, "domain": domain,
                               "score": round(avg, 4)})
        result.sort(key=lambda x: x["score"], reverse=True)
        return result

    def decay_inactive(self, threshold_hours: float = 72):
        """Apply temporal decay for agents inactive beyond threshold."""
        cutoff = time.time() - threshold_hours * 3600
        agents = self._conn.execute(
            "SELECT DISTINCT agent_id FROM trust_signals"
        ).fetchall()
        decayed = []
        for (aid,) in agents:
            last = self._conn.execute(
                "SELECT MAX(timestamp) FROM trust_signals WHERE agent_id=?",
                (aid,)
            ).fetchone()
            if last and last[0] and last[0] < cutoff:
                hours_inactive = (time.time() - last[0]) / 3600
                decay_value = max(0.0, 1.0 - (hours_inactive - threshold_hours) / 720)
                self.record_signal(aid, "temporal_freshness", decay_value)
                decayed.append(aid)
                log.info(f"Trust decay: {aid} inactive {hours_inactive:.0f}h, "
                         f"freshness={decay_value:.2f}")
        return decayed

    def get_signal_history(self, agent_id: str, limit: int = 100) -> List[dict]:
        """Raw signal history for an agent."""
        rows = self._conn.execute(
            "SELECT signal_name, value, timestamp FROM trust_signals "
            "WHERE agent_id=? ORDER BY timestamp DESC LIMIT ?",
            (agent_id, limit)
        ).fetchall()
        return [{"signal": r[0], "value": r[1], "timestamp": r[2]} for r in rows]
