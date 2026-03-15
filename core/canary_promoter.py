"""
WaggleDance Canary Promoter — Phase 4
Gates prompts and micromodels through canary validation before promotion.

A canary experiment collects evaluation samples and compares them against
a baseline score.  Once enough samples are collected, the experiment is
either promoted (improvement >= min_improvement) or rolled back.
"""

import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# If the canary mean drops this far below the baseline, flag for rollback.
ROLLBACK_THRESHOLD = -0.05


@dataclass
class CanaryResult:
    """Outcome of a canary evaluation check."""

    promoted: bool = False
    samples_seen: int = 0
    improvement: float = 0.0
    reason: str = ""


class CanaryPromoter:
    """Gates prompts / micromodels through canary validation.

    Usage::

        cp = CanaryPromoter(min_samples=50, min_improvement=0.05)
        cp.start_canary("exp-1", baseline_score=0.72)
        for s in eval_scores:
            cp.record_sample("exp-1", s)
        result = cp.evaluate("exp-1")
        if result.promoted:
            deploy()
    """

    def __init__(
        self,
        min_samples: int = 50,
        min_improvement: float = 0.05,
        enabled: bool = True,
    ):
        self.min_samples = max(1, min_samples)
        self.min_improvement = min_improvement
        self.enabled = enabled
        self._experiments: dict[str, dict] = {}

    # ───────────────────────────────────────────────────────────
    # Experiment lifecycle
    # ───────────────────────────────────────────────────────────

    def start_canary(self, experiment_id: str, baseline_score: float) -> None:
        """Register a new canary experiment."""
        if experiment_id in self._experiments:
            log.warning("Canary %s already exists — resetting", experiment_id)
        self._experiments[experiment_id] = {
            "baseline": baseline_score,
            "scores": [],
            "started_at": time.time(),
        }
        log.info(
            "Canary started: %s (baseline=%.4f)", experiment_id, baseline_score
        )

    def record_sample(self, experiment_id: str, score: float) -> None:
        """Record a single evaluation sample for the experiment."""
        exp = self._experiments.get(experiment_id)
        if exp is None:
            log.warning("record_sample: unknown experiment %s", experiment_id)
            return
        exp["scores"].append(score)

    # ───────────────────────────────────────────────────────────
    # Evaluation
    # ───────────────────────────────────────────────────────────

    def _mean(self, scores: list[float]) -> float:
        if not scores:
            return 0.0
        return sum(scores) / len(scores)

    def evaluate(self, experiment_id: str) -> CanaryResult:
        """Check if experiment should be promoted, rolled back, or needs more samples."""
        if not self.enabled:
            return CanaryResult(
                promoted=False, samples_seen=0, reason="canary_disabled"
            )

        exp = self._experiments.get(experiment_id)
        if exp is None:
            return CanaryResult(
                promoted=False, samples_seen=0, reason="unknown_experiment"
            )

        scores = exp["scores"]
        n = len(scores)
        baseline = exp["baseline"]

        if n < self.min_samples:
            return CanaryResult(
                promoted=False,
                samples_seen=n,
                improvement=0.0,
                reason=f"need_more_samples ({n}/{self.min_samples})",
            )

        canary_mean = self._mean(scores)
        improvement = canary_mean - baseline

        if improvement >= self.min_improvement:
            log.info(
                "Canary %s PROMOTED (improvement=%.4f, n=%d)",
                experiment_id,
                improvement,
                n,
            )
            return CanaryResult(
                promoted=True,
                samples_seen=n,
                improvement=round(improvement, 6),
                reason="promoted",
            )

        reason = "insufficient_improvement"
        if improvement < ROLLBACK_THRESHOLD:
            reason = "regression_detected"
        log.info(
            "Canary %s NOT promoted: %s (improvement=%.4f, n=%d)",
            experiment_id,
            reason,
            improvement,
            n,
        )
        return CanaryResult(
            promoted=False,
            samples_seen=n,
            improvement=round(improvement, 6),
            reason=reason,
        )

    def should_rollback(self, experiment_id: str) -> bool:
        """True if canary performance is significantly worse than baseline."""
        exp = self._experiments.get(experiment_id)
        if exp is None:
            return False
        scores = exp["scores"]
        if not scores:
            return False
        canary_mean = self._mean(scores)
        return (canary_mean - exp["baseline"]) < ROLLBACK_THRESHOLD
