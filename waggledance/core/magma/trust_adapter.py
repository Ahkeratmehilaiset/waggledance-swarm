# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Trust adapter — wraps legacy TrustEngine with multi-dimensional trust targets.

Extends L5 trust from agent-only to also cover:
- capability trust
- solver trust
- route trust
- profile trust
- baseline trust
- action executor trust
- specialist model trust
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_TrustEngine = None


def _get_legacy_class():
    global _TrustEngine
    if _TrustEngine is None:
        try:
            from core.trust_engine import TrustEngine
            _TrustEngine = TrustEngine
        except ImportError:
            _TrustEngine = None
    return _TrustEngine


@dataclass
class TrustRecord:
    """Trust observation for any target (capability, route, specialist, etc.)."""
    target_type: str   # capability, solver, route, specialist, baseline
    target_id: str
    success: bool
    confidence: float = 0.0
    latency_ms: float = 0.0
    quality_path: str = ""
    timestamp: float = field(default_factory=time.time)
    context: str = "actual"  # "actual" | "simulated" — v3.2 MAGMA expansion


class TrustAdapter:
    """Multi-dimensional trust extending legacy TrustEngine.

    While the legacy engine tracks per-agent trust via 6 signals,
    this adapter adds trust tracking for capabilities, routes,
    solvers, and specialist models — all first-class trust targets
    in the autonomy core.
    """

    VALID_TARGET_TYPES = frozenset({
        "capability", "solver", "route", "specialist",
        "baseline", "executor", "profile",
    })

    def __init__(self, legacy_engine=None):
        self._engine = legacy_engine
        self._lock = threading.Lock()
        self._observations: Dict[str, List[TrustRecord]] = {}
        self._score_totals: Dict[str, tuple[float, float, float]] = {}
        self._max_per_target = 200

    @staticmethod
    def _observation_weight(record: TrustRecord) -> float:
        return 0.5 if record.context == "simulated" else 1.0

    @classmethod
    def _totals_from_observations(
        cls,
        observations: List[TrustRecord],
    ) -> tuple[float, float, float]:
        weighted_sum = 0.0
        weight_total = 0.0
        last_ts = 0.0
        for record in observations:
            context_weight = cls._observation_weight(record)
            if last_ts:
                decay = 0.95 ** ((record.timestamp - last_ts) / 3600)
                weighted_sum *= decay
                weight_total *= decay
            weighted_sum += context_weight * (1.0 if record.success else 0.0)
            weight_total += context_weight
            last_ts = record.timestamp
        return weighted_sum, weight_total, last_ts

    @staticmethod
    def _score_from_totals(weighted_sum: float, weight_total: float) -> float:
        return weighted_sum / weight_total if weight_total > 0 else 0.5

    def record_observation(self, target_type: str, target_id: str,
                           success: bool, confidence: float = 0.0,
                           latency_ms: float = 0.0,
                           quality_path: str = "",
                           context: str = "actual") -> None:
        """Record a trust observation for any target type."""
        if target_type not in self.VALID_TARGET_TYPES:
            raise ValueError(f"Invalid target_type: {target_type}")

        record = TrustRecord(
            target_type=target_type,
            target_id=target_id,
            success=success,
            confidence=confidence,
            latency_ms=latency_ms,
            quality_path=quality_path,
            context=context,
        )

        key = f"{target_type}:{target_id}"
        with self._lock:
            if key not in self._observations:
                self._observations[key] = []
            obs = self._observations[key]
            obs.append(record)
            if len(obs) > self._max_per_target:
                self._observations[key] = obs[-self._max_per_target:]
                self._score_totals[key] = self._totals_from_observations(
                    self._observations[key]
                )
            else:
                current = self._score_totals.get(key)
                context_weight = self._observation_weight(record)
                add_success = context_weight * (1.0 if success else 0.0)
                if current is None:
                    self._score_totals[key] = (
                        add_success, context_weight, record.timestamp,
                    )
                else:
                    weighted_sum, weight_total, last_ts = current
                    decay = 0.95 ** ((record.timestamp - last_ts) / 3600)
                    self._score_totals[key] = (
                        (weighted_sum * decay) + add_success,
                        (weight_total * decay) + context_weight,
                        record.timestamp,
                    )

        if self._engine and target_type == "capability":
            try:
                self._engine.record_autonomy_trust(
                    capability_id=target_id,
                    quality_path=quality_path,
                    verified=success,
                    execution_ms=latency_ms,
                )
            except Exception as exc:
                logger.debug("Legacy trust write failed: %s", exc)

    def get_trust_score(self, target_type: str, target_id: str) -> float:
        """Compute trust score for a target based on recent observations.

        Uses exponentially weighted success rate with recency bias.
        """
        key = f"{target_type}:{target_id}"
        with self._lock:
            obs = self._observations.get(key, [])
            totals = self._score_totals.get(key)

        if not obs:
            if self._engine and target_type == "capability":
                try:
                    return self._engine.get_capability_trust(target_id)
                except Exception:
                    pass
            return 0.5  # neutral prior

        if totals is None:
            totals = self._totals_from_observations(obs)
        return self._score_from_totals(totals[0], totals[1])

    def get_all_scores(self, target_type: str = None) -> Dict[str, float]:
        """Get trust scores for all tracked targets, optionally filtered by type.

        Scores are maintained incrementally when observations arrive,
        so ranking reads one precomputed total per target instead of
        rescanning every retained observation.
        """
        with self._lock:
            # Snapshot the score totals dict ONCE under the lock.
            if target_type:
                prefix = f"{target_type}:"
                totals_snapshot = [
                    (k, v) for k, v in self._score_totals.items()
                    if k.startswith(prefix)
                ]
            else:
                totals_snapshot = list(self._score_totals.items())

        scores: Dict[str, float] = {}
        for key, (weighted_sum, weight_total, _last_ts) in totals_snapshot:
            scores[key] = self._score_from_totals(weighted_sum, weight_total)
        return scores

    def get_ranking(self, target_type: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get ranked list of targets by trust score."""
        scores = self.get_all_scores(target_type=target_type)
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [
            {"target": k, "trust_score": round(v, 4)}
            for k, v in ranked[:limit]
        ]

    def get_trend(self, target_type: str, target_id: str,
                  window_size: int = 20) -> str:
        """Determine trust trend: improving, stable, or declining."""
        key = f"{target_type}:{target_id}"
        with self._lock:
            obs = self._observations.get(key, [])

        if len(obs) < window_size * 2:
            return "insufficient_data"

        recent = obs[-window_size:]
        older = obs[-(window_size * 2):-window_size]

        recent_rate = sum(1 for r in recent if r.success) / len(recent)
        older_rate = sum(1 for r in older if r.success) / len(older)

        diff = recent_rate - older_rate
        if diff > 0.05:
            return "improving"
        elif diff < -0.05:
            return "declining"
        return "stable"

    def stats(self) -> Dict[str, Any]:
        """Summary statistics."""
        with self._lock:
            by_type: Dict[str, int] = {}
            total_obs = 0
            for key, obs in self._observations.items():
                t_type = key.split(":")[0]
                by_type[t_type] = by_type.get(t_type, 0) + len(obs)
                total_obs += len(obs)
            return {
                "total_targets": len(self._observations),
                "total_observations": total_obs,
                "by_type": by_type,
                "legacy_engine_available": self._engine is not None,
            }
