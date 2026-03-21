"""
WaggleDance Active Learning Scorer — Phase 4
Deterministic, auditable priority scorer for training data selection.

Scores queries based on 4 signals:
  low_confidence   — LLM confidence < 0.6        (weight 0.35)
  topic_gap        — topic has few examples       (weight 0.25)
  user_correction  — user corrected the answer    (weight 0.25)
  repeated_fallback — fell back to LLM 2+ times   (weight 0.15)
"""

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# WEIGHTS
# ═══════════════════════════════════════════════════════════════

W_LOW_CONFIDENCE = 0.35
W_TOPIC_GAP = 0.25
W_USER_CORRECTION = 0.25
W_REPEATED_FALLBACK = 0.15

CONFIDENCE_THRESHOLD = 0.6
TOPIC_GAP_CEILING = 10  # topics with >= this many examples get 0 gap signal


@dataclass
class LearningCandidate:
    """A single query that may be valuable for training."""

    query: str
    confidence: float = 1.0
    topic: str = ""
    was_corrected: bool = False
    fallback_count: int = 0


class ActiveLearningScorer:
    """Deterministic priority scorer for selecting training candidates.

    Usage::

        scorer = ActiveLearningScorer(topic_counts={"bee_health": 3, "heating": 50})
        cand = LearningCandidate(query="varroa treatment?", confidence=0.4,
                                  topic="bee_health", was_corrected=True)
        print(scorer.score(cand))  # high score — low confidence + gap + correction
    """

    def __init__(self, topic_counts: dict[str, int] | None = None):
        self.topic_counts = topic_counts or {}

    # ───────────────────────────────────────────────────────────
    # Individual signal scorers (each returns 0.0–1.0)
    # ───────────────────────────────────────────────────────────

    @staticmethod
    def _sig_low_confidence(confidence: float) -> float:
        """Linear ramp: 1.0 at confidence=0, 0.0 at confidence>=CONFIDENCE_THRESHOLD."""
        if confidence >= CONFIDENCE_THRESHOLD:
            return 0.0
        return max(0.0, min(1.0, 1.0 - confidence / CONFIDENCE_THRESHOLD))

    def _sig_topic_gap(self, topic: str) -> float:
        """1.0 for unknown topics, linearly decreasing to 0.0 at TOPIC_GAP_CEILING."""
        if not topic:
            return 0.0  # no topic info → cannot assess gap
        count = self.topic_counts.get(topic, 0)
        if count >= TOPIC_GAP_CEILING:
            return 0.0
        return 1.0 - count / TOPIC_GAP_CEILING

    @staticmethod
    def _sig_user_correction(was_corrected: bool) -> float:
        return 1.0 if was_corrected else 0.0

    @staticmethod
    def _sig_repeated_fallback(fallback_count: int) -> float:
        """0.0 for 0-1 fallbacks, linear ramp to 1.0 at 5+ fallbacks."""
        if fallback_count <= 1:
            return 0.0
        return min(1.0, (fallback_count - 1) / 4.0)

    # ───────────────────────────────────────────────────────────
    # Public API
    # ───────────────────────────────────────────────────────────

    def score(self, candidate: LearningCandidate) -> float:
        """Return priority score 0.0–1.0.  Higher = more useful for training."""
        s = (
            W_LOW_CONFIDENCE * self._sig_low_confidence(candidate.confidence)
            + W_TOPIC_GAP * self._sig_topic_gap(candidate.topic)
            + W_USER_CORRECTION * self._sig_user_correction(candidate.was_corrected)
            + W_REPEATED_FALLBACK * self._sig_repeated_fallback(candidate.fallback_count)
        )
        return round(min(1.0, max(0.0, s)), 6)

    def rank(
        self, candidates: list[LearningCandidate], top_k: int = 10
    ) -> list[tuple[float, LearningCandidate]]:
        """Return top-K candidates sorted by score descending."""
        scored = [(self.score(c), c) for c in candidates]
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:top_k]
