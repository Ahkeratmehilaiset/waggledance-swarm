"""Stats engine — statistical analysis and aggregation for the autonomy core.

Provides time-series analysis, summary statistics, percentile computation,
and trend detection for metric data from the WorldModel and sensors.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class StatsResult:
    """Statistical summary of a metric."""
    metric: str
    count: int = 0
    mean: float = 0.0
    median: float = 0.0
    std: float = 0.0
    min_val: float = 0.0
    max_val: float = 0.0
    p25: float = 0.0
    p75: float = 0.0
    p95: float = 0.0
    p99: float = 0.0
    trend_slope: float = 0.0
    trend_direction: str = "stable"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metric": self.metric,
            "count": self.count,
            "mean": round(self.mean, 4),
            "median": round(self.median, 4),
            "std": round(self.std, 4),
            "min": round(self.min_val, 4),
            "max": round(self.max_val, 4),
            "p25": round(self.p25, 4),
            "p75": round(self.p75, 4),
            "p95": round(self.p95, 4),
            "p99": round(self.p99, 4),
            "trend_slope": round(self.trend_slope, 6),
            "trend_direction": self.trend_direction,
        }


class StatsEngine:
    """Statistical analysis engine for time-series metric data.

    Tracks metric values over time and provides summary statistics,
    percentile computation, trend detection, and comparison analysis.
    """

    def __init__(self, max_samples: int = 1000):
        self._data: Dict[str, List[Tuple[float, float]]] = {}  # metric -> [(timestamp, value)]
        self._max_samples = max_samples

    def record(self, metric: str, value: float,
               timestamp: float = None) -> None:
        """Record a metric observation."""
        ts = timestamp or time.time()
        if metric not in self._data:
            self._data[metric] = []
        self._data[metric].append((ts, value))
        if len(self._data[metric]) > self._max_samples:
            self._data[metric] = self._data[metric][-self._max_samples:]

    def summarize(self, metric: str,
                  since: float = None) -> StatsResult:
        """Compute full statistical summary for a metric."""
        entries = self._data.get(metric, [])
        if since:
            entries = [(t, v) for t, v in entries if t >= since]
        if not entries:
            return StatsResult(metric=metric)

        values = [v for _, v in entries]
        n = len(values)
        mean = sum(values) / n
        sorted_vals = sorted(values)
        median = sorted_vals[n // 2]
        variance = sum((v - mean) ** 2 for v in values) / n
        std = math.sqrt(variance)

        result = StatsResult(
            metric=metric,
            count=n,
            mean=mean,
            median=median,
            std=std,
            min_val=sorted_vals[0],
            max_val=sorted_vals[-1],
            p25=self._percentile(sorted_vals, 0.25),
            p75=self._percentile(sorted_vals, 0.75),
            p95=self._percentile(sorted_vals, 0.95),
            p99=self._percentile(sorted_vals, 0.99),
        )

        if n >= 5:
            slope = self._compute_slope(entries)
            result.trend_slope = slope
            if abs(slope) < std * 0.01:
                result.trend_direction = "stable"
            elif slope > 0:
                result.trend_direction = "increasing"
            else:
                result.trend_direction = "decreasing"

        return result

    def compare(self, metric: str, window_a: int = 50,
                window_b: int = 50) -> Dict[str, Any]:
        """Compare recent window_a values against preceding window_b values."""
        entries = self._data.get(metric, [])
        if len(entries) < window_a + window_b:
            return {"error": "Insufficient data", "metric": metric}

        recent = [v for _, v in entries[-window_a:]]
        older = [v for _, v in entries[-(window_a + window_b):-window_a]]

        mean_recent = sum(recent) / len(recent)
        mean_older = sum(older) / len(older)
        change_pct = ((mean_recent - mean_older) / (abs(mean_older) + 0.001)) * 100

        return {
            "metric": metric,
            "recent_mean": round(mean_recent, 4),
            "older_mean": round(mean_older, 4),
            "change_pct": round(change_pct, 2),
            "direction": "up" if change_pct > 1 else ("down" if change_pct < -1 else "stable"),
        }

    def moving_average(self, metric: str, window: int = 10) -> List[float]:
        """Compute simple moving average."""
        entries = self._data.get(metric, [])
        values = [v for _, v in entries]
        if len(values) < window:
            return values
        result = []
        for i in range(len(values) - window + 1):
            result.append(sum(values[i:i + window]) / window)
        return result

    def correlation(self, metric_a: str, metric_b: str) -> Optional[float]:
        """Compute Pearson correlation between two metrics.

        Aligns by index (not timestamp) — assumes similar recording frequency.
        """
        a_vals = [v for _, v in self._data.get(metric_a, [])]
        b_vals = [v for _, v in self._data.get(metric_b, [])]
        n = min(len(a_vals), len(b_vals))
        if n < 5:
            return None

        a = a_vals[-n:]
        b = b_vals[-n:]
        mean_a = sum(a) / n
        mean_b = sum(b) / n

        cov = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n)) / n
        std_a = math.sqrt(sum((v - mean_a) ** 2 for v in a) / n)
        std_b = math.sqrt(sum((v - mean_b) ** 2 for v in b) / n)

        if std_a < 1e-10 or std_b < 1e-10:
            return 0.0
        return cov / (std_a * std_b)

    def list_metrics(self) -> List[str]:
        """List all tracked metrics."""
        return list(self._data.keys())

    def _percentile(self, sorted_vals: List[float], p: float) -> float:
        idx = p * (len(sorted_vals) - 1)
        low = int(math.floor(idx))
        high = min(low + 1, len(sorted_vals) - 1)
        frac = idx - low
        return sorted_vals[low] * (1 - frac) + sorted_vals[high] * frac

    def _compute_slope(self, entries: List[Tuple[float, float]]) -> float:
        """Simple linear regression slope."""
        n = len(entries)
        if n < 2:
            return 0.0
        t0 = entries[0][0]
        xs = [(t - t0) for t, _ in entries]
        ys = [v for _, v in entries]
        x_mean = sum(xs) / n
        y_mean = sum(ys) / n
        num = sum((xs[i] - x_mean) * (ys[i] - y_mean) for i in range(n))
        den = sum((xs[i] - x_mean) ** 2 for i in range(n))
        return num / den if den > 0 else 0.0

    def stats(self) -> Dict[str, Any]:
        total_samples = sum(len(v) for v in self._data.values())
        return {
            "metrics_tracked": len(self._data),
            "total_samples": total_samples,
            "max_samples_per_metric": self._max_samples,
        }
