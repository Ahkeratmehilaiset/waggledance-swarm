"""Autonomy metrics — KPI tracking for route accuracy, LLM fallback, specialist accuracy.

Tracks the exact metrics defined in the mega-spec §21:
- Route accuracy (>90% target)
- Capability chain success rate (>75% target)
- Verifier pass rate (>85% target)
- LLM fallback rate (<30% target at 6mo)
- Specialist model accuracy (>85% per model)
- Proactive goal rate (>5/day target)
- Night learning gold rate (>40% target)
- Case trajectory coverage (>80% target)
"""

from __future__ import annotations

import logging
import time
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


@dataclass
class MetricSample:
    """Single metric observation."""
    name: str
    value: float
    timestamp: float = field(default_factory=time.time)
    tags: Dict[str, str] = field(default_factory=dict)


class AutonomyMetrics:
    """Centralized autonomy KPI tracker.

    Collects samples from across the autonomy runtime and provides
    real-time and aggregated KPI values.
    """

    # Spec targets
    TARGETS = {
        "route_accuracy": 0.90,
        "capability_chain_success_rate": 0.75,
        "verifier_pass_rate": 0.85,
        "llm_fallback_rate": 0.30,  # upper bound
        "specialist_accuracy": 0.85,
        "proactive_goal_rate": 5.0,  # per day
        "night_learning_gold_rate": 0.40,
        "case_trajectory_coverage": 0.80,
        "time_to_answer_solver_ms": 50.0,  # p50 target
        "time_to_answer_llm_ms": 2000.0,  # p50 target
        "abstain_correctness": 0.90,
        "residual_reduction": 0.0,  # positive trend
        "self_correction_rate": 0.0,  # positive trend
    }

    def __init__(self, max_samples: int = 5000):
        self._lock = threading.Lock()
        self._samples: Dict[str, List[MetricSample]] = {}
        self._counters: Dict[str, int] = {}
        self._max_samples = max_samples

    def record(self, name: str, value: float, **tags) -> None:
        """Record a metric observation."""
        sample = MetricSample(name=name, value=value, tags=tags)
        with self._lock:
            if name not in self._samples:
                self._samples[name] = []
            self._samples[name].append(sample)
            if len(self._samples[name]) > self._max_samples:
                self._samples[name] = self._samples[name][-self._max_samples:]

    def increment(self, counter: str, amount: int = 1) -> None:
        """Increment a counter."""
        with self._lock:
            self._counters[counter] = self._counters.get(counter, 0) + amount

    def record_route_decision(self, success: bool, was_fallback: bool = False,
                              route_type: str = "", latency_ms: float = 0.0) -> None:
        """Record a routing decision for accuracy tracking."""
        self.record("route_accuracy", 1.0 if success else 0.0, route_type=route_type)
        if was_fallback:
            self.record("llm_fallback", 1.0, route_type=route_type)
        else:
            self.record("llm_fallback", 0.0, route_type=route_type)
        self.record("response_latency_ms", latency_ms, route_type=route_type)

    def record_capability_chain(self, success: bool,
                                chain_length: int = 1) -> None:
        """Record capability chain outcome."""
        self.record("capability_chain_success", 1.0 if success else 0.0,
                    chain_length=str(chain_length))

    def record_verification(self, passed: bool, confidence: float = 0.0) -> None:
        """Record verifier result."""
        self.record("verifier_pass", 1.0 if passed else 0.0, confidence=str(confidence))

    def record_specialist_prediction(self, model_name: str,
                                     correct: bool) -> None:
        """Record specialist model prediction accuracy."""
        self.record("specialist_accuracy", 1.0 if correct else 0.0,
                    model=model_name)

    def record_case_grade(self, grade: str) -> None:
        """Record case trajectory quality grade."""
        self.increment(f"case_grade_{grade}")
        if grade == "gold":
            self.record("case_gold", 1.0)
        else:
            self.record("case_gold", 0.0)

    def record_proactive_goal(self) -> None:
        """Record a proactively generated goal."""
        self.increment("proactive_goals_today")

    def get_metric(self, name: str, since: float = None) -> float:
        """Get average value for a metric."""
        with self._lock:
            samples = self._samples.get(name, [])
        if since:
            samples = [s for s in samples if s.timestamp >= since]
        if not samples:
            return 0.0
        return sum(s.value for s in samples) / len(samples)

    def get_rate(self, name: str, window_seconds: float = 86400) -> float:
        """Get average rate over a time window."""
        since = time.time() - window_seconds
        return self.get_metric(name, since=since)

    def get_counter(self, counter: str) -> int:
        """Get counter value."""
        with self._lock:
            return self._counters.get(counter, 0)

    def get_kpis(self) -> Dict[str, Any]:
        """Get all KPIs with current values and targets."""
        day_ago = time.time() - 86400
        kpis = {}

        # Route accuracy
        route_acc = self.get_rate("route_accuracy")
        kpis["route_accuracy"] = {
            "value": round(route_acc, 4),
            "target": self.TARGETS["route_accuracy"],
            "meets_target": route_acc >= self.TARGETS["route_accuracy"],
        }

        # LLM fallback rate
        fallback = self.get_rate("llm_fallback")
        kpis["llm_fallback_rate"] = {
            "value": round(fallback, 4),
            "target": self.TARGETS["llm_fallback_rate"],
            "meets_target": fallback <= self.TARGETS["llm_fallback_rate"],
        }

        # Capability chain success
        chain_success = self.get_rate("capability_chain_success")
        kpis["capability_chain_success_rate"] = {
            "value": round(chain_success, 4),
            "target": self.TARGETS["capability_chain_success_rate"],
            "meets_target": chain_success >= self.TARGETS["capability_chain_success_rate"],
        }

        # Verifier pass rate
        verifier = self.get_rate("verifier_pass")
        kpis["verifier_pass_rate"] = {
            "value": round(verifier, 4),
            "target": self.TARGETS["verifier_pass_rate"],
            "meets_target": verifier >= self.TARGETS["verifier_pass_rate"],
        }

        # Specialist accuracy
        specialist = self.get_rate("specialist_accuracy")
        kpis["specialist_accuracy"] = {
            "value": round(specialist, 4),
            "target": self.TARGETS["specialist_accuracy"],
            "meets_target": specialist >= self.TARGETS["specialist_accuracy"],
        }

        # Night learning gold rate
        gold_rate = self.get_rate("case_gold")
        kpis["night_learning_gold_rate"] = {
            "value": round(gold_rate, 4),
            "target": self.TARGETS["night_learning_gold_rate"],
            "meets_target": gold_rate >= self.TARGETS["night_learning_gold_rate"],
        }

        # Quality grade distribution
        kpis["case_grades"] = {
            "gold": self.get_counter("case_grade_gold"),
            "silver": self.get_counter("case_grade_silver"),
            "bronze": self.get_counter("case_grade_bronze"),
            "quarantine": self.get_counter("case_grade_quarantine"),
        }

        # Counters
        kpis["proactive_goals_today"] = self.get_counter("proactive_goals_today")

        return kpis

    def get_per_specialist_accuracy(self) -> Dict[str, float]:
        """Get accuracy per specialist model."""
        with self._lock:
            samples = self._samples.get("specialist_accuracy", [])

        by_model: Dict[str, List[float]] = {}
        for s in samples:
            model = s.tags.get("model", "unknown")
            if model not in by_model:
                by_model[model] = []
            by_model[model].append(s.value)

        return {
            model: sum(vals) / len(vals)
            for model, vals in by_model.items()
            if vals
        }

    def reset_daily_counters(self) -> None:
        """Reset daily counters (call at midnight)."""
        with self._lock:
            for key in list(self._counters.keys()):
                if key.endswith("_today"):
                    self._counters[key] = 0

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            total_samples = sum(len(v) for v in self._samples.values())
            return {
                "metrics_tracked": len(self._samples),
                "total_samples": total_samples,
                "counters": dict(self._counters),
            }
