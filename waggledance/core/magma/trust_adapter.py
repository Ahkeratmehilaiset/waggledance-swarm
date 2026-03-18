# BUSL-1.1 - see LICENSE-CORE.md
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
        self._max_per_target = 200

    def record_observation(self, target_type: str, target_id: str,
                           success: bool, confidence: float = 0.0,
                           latency_ms: float = 0.0,
                           quality_path: str = "") -> None:
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
        )

        key = f"{target_type}:{target_id}"
        with self._lock:
            if key not in self._observations:
                self._observations[key] = []
            obs = self._observations[key]
            obs.append(record)
            if len(obs) > self._max_per_target:
                self._observations[key] = obs[-self._max_per_target:]

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

        if not obs:
            if self._engine and target_type == "capability":
                try:
                    return self._engine.get_capability_trust(target_id)
                except Exception:
                    pass
            return 0.5  # neutral prior

        now = time.time()
        weighted_sum = 0.0
        weight_total = 0.0
        for r in obs:
            age_hours = (now - r.timestamp) / 3600
            decay = 0.95 ** age_hours  # half-life ~14 hours
            w = decay
            weighted_sum += w * (1.0 if r.success else 0.0)
            weight_total += w

        return weighted_sum / weight_total if weight_total > 0 else 0.5

    def get_all_scores(self, target_type: str = None) -> Dict[str, float]:
        """Get trust scores for all tracked targets, optionally filtered by type."""
        with self._lock:
            keys = list(self._observations.keys())

        scores = {}
        for key in keys:
            t_type, t_id = key.split(":", 1)
            if target_type and t_type != target_type:
                continue
            scores[key] = self.get_trust_score(t_type, t_id)
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
