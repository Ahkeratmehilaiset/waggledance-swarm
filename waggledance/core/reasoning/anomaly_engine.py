"""Anomaly engine — statistical anomaly detection for sensor and baseline data.

Detects anomalies using z-score, IQR, and trend analysis against
baseline values from the WorldModel. Feeds into the autonomy core's
detect.anomaly capability.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class AnomalyResult:
    """Result of anomaly detection on a metric."""
    metric: str
    is_anomaly: bool
    severity: float = 0.0  # 0-1
    z_score: float = 0.0
    residual: float = 0.0
    baseline: float = 0.0
    current: float = 0.0
    method: str = ""
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metric": self.metric,
            "is_anomaly": self.is_anomaly,
            "severity": round(self.severity, 4),
            "z_score": round(self.z_score, 4),
            "residual": round(self.residual, 4),
            "baseline": round(self.baseline, 4),
            "current": round(self.current, 4),
            "method": self.method,
            "message": self.message,
        }


class AnomalyEngine:
    """Statistical anomaly detection engine.

    Methods:
    - Z-score: detects values >N standard deviations from mean
    - IQR: detects values outside Q1-1.5×IQR..Q3+1.5×IQR
    - Residual: detects large deviations from baseline values
    - Trend: detects sustained directional changes
    """

    def __init__(self, z_threshold: float = 2.5, iqr_factor: float = 1.5,
                 residual_threshold: float = 0.3):
        self._z_threshold = z_threshold
        self._iqr_factor = iqr_factor
        self._residual_threshold = residual_threshold
        self._history: Dict[str, List[float]] = {}
        self._max_history = 500
        self._results: List[AnomalyResult] = []

    def record_value(self, metric: str, value: float) -> None:
        """Record a new observation for a metric."""
        if metric not in self._history:
            self._history[metric] = []
        self._history[metric].append(value)
        if len(self._history[metric]) > self._max_history:
            self._history[metric] = self._history[metric][-self._max_history:]

    def check_z_score(self, metric: str, current: float) -> AnomalyResult:
        """Detect anomaly using z-score method."""
        values = self._history.get(metric, [])
        if len(values) < 10:
            return AnomalyResult(
                metric=metric, is_anomaly=False, current=current,
                method="z_score", message="Insufficient data (<10 samples)",
            )
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        std = math.sqrt(variance) if variance > 0 else 0.001
        z = (current - mean) / std
        is_anomaly = abs(z) > self._z_threshold
        severity = min(abs(z) / (self._z_threshold * 2), 1.0) if is_anomaly else 0.0

        result = AnomalyResult(
            metric=metric,
            is_anomaly=is_anomaly,
            severity=severity,
            z_score=z,
            baseline=mean,
            current=current,
            method="z_score",
            message=f"Z-score {z:.2f} {'exceeds' if is_anomaly else 'within'} ±{self._z_threshold}",
        )
        self._results.append(result)
        return result

    def check_iqr(self, metric: str, current: float) -> AnomalyResult:
        """Detect anomaly using IQR method."""
        values = self._history.get(metric, [])
        if len(values) < 20:
            return AnomalyResult(
                metric=metric, is_anomaly=False, current=current,
                method="iqr", message="Insufficient data (<20 samples)",
            )
        sorted_vals = sorted(values)
        n = len(sorted_vals)
        q1 = sorted_vals[n // 4]
        q3 = sorted_vals[3 * n // 4]
        iqr = q3 - q1
        lower = q1 - self._iqr_factor * iqr
        upper = q3 + self._iqr_factor * iqr
        is_anomaly = current < lower or current > upper

        if is_anomaly:
            distance = max(lower - current, current - upper, 0)
            severity = min(distance / (iqr + 0.001), 1.0)
        else:
            severity = 0.0

        result = AnomalyResult(
            metric=metric,
            is_anomaly=is_anomaly,
            severity=severity,
            baseline=(q1 + q3) / 2,
            current=current,
            method="iqr",
            message=f"Value {'outside' if is_anomaly else 'within'} [{lower:.2f}, {upper:.2f}]",
        )
        self._results.append(result)
        return result

    def check_residual(self, metric: str, current: float,
                       baseline: float) -> AnomalyResult:
        """Detect anomaly based on deviation from a known baseline."""
        residual = current - baseline
        relative = abs(residual) / (abs(baseline) + 0.001)
        is_anomaly = relative > self._residual_threshold
        severity = min(relative / (self._residual_threshold * 2), 1.0) if is_anomaly else 0.0

        result = AnomalyResult(
            metric=metric,
            is_anomaly=is_anomaly,
            severity=severity,
            residual=residual,
            baseline=baseline,
            current=current,
            method="residual",
            message=f"Deviation {relative:.1%} {'exceeds' if is_anomaly else 'within'} {self._residual_threshold:.0%}",
        )
        self._results.append(result)
        return result

    def check_trend(self, metric: str, window: int = 10) -> AnomalyResult:
        """Detect sustained directional trend in recent values."""
        values = self._history.get(metric, [])
        if len(values) < window:
            return AnomalyResult(
                metric=metric, is_anomaly=False,
                method="trend", message=f"Insufficient data (<{window} samples)",
            )
        recent = values[-window:]
        diffs = [recent[i + 1] - recent[i] for i in range(len(recent) - 1)]
        positive = sum(1 for d in diffs if d > 0)
        negative = sum(1 for d in diffs if d < 0)
        total = len(diffs)

        trend_strength = max(positive, negative) / total
        is_anomaly = trend_strength > 0.8
        slope = sum(diffs) / total if total else 0

        result = AnomalyResult(
            metric=metric,
            is_anomaly=is_anomaly,
            severity=trend_strength if is_anomaly else 0.0,
            current=recent[-1],
            baseline=recent[0],
            residual=slope,
            method="trend",
            message=f"{'Upward' if slope > 0 else 'Downward'} trend ({trend_strength:.0%} directional)",
        )
        self._results.append(result)
        return result

    def check_all(self, metric: str, current: float,
                  baseline: Optional[float] = None) -> List[AnomalyResult]:
        """Run all applicable detection methods on a metric."""
        self.record_value(metric, current)
        results = [
            self.check_z_score(metric, current),
            self.check_iqr(metric, current),
            self.check_trend(metric),
        ]
        if baseline is not None:
            results.append(self.check_residual(metric, current, baseline))
        return results

    def get_anomalies(self, min_severity: float = 0.3) -> List[AnomalyResult]:
        """Return all detected anomalies above severity threshold."""
        return [r for r in self._results if r.is_anomaly and r.severity >= min_severity]

    def stats(self) -> Dict[str, Any]:
        total = len(self._results)
        anomalies = sum(1 for r in self._results if r.is_anomaly)
        return {
            "total_checks": total,
            "anomalies_detected": anomalies,
            "anomaly_rate": anomalies / total if total else 0.0,
            "metrics_tracked": len(self._history),
            "z_threshold": self._z_threshold,
            "iqr_factor": self._iqr_factor,
            "residual_threshold": self._residual_threshold,
        }
