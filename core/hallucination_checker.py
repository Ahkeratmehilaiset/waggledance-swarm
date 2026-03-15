"""Hallucination checker — embedding + keyword overlap detection.

Extracted from memory_engine.py (v1.17.0).
Wraps the hallucination checking logic from Consciousness.check_hallucination()
into a standalone, testable class.
"""

import logging
import re
from dataclasses import dataclass
from typing import Optional, List

log = logging.getLogger("consciousness")


@dataclass
class HallucinationResult:
    relevance: float = 1.0
    keyword_overlap: float = 1.0
    is_suspicious: bool = False
    reason: str = ""


# English stopwords for keyword overlap calculation
_STOPWORDS = frozenset({
    "the", "and", "for", "are", "but", "not", "you",
    "all", "can", "her", "was", "one", "our", "out",
    "has", "have", "with", "this", "that", "from",
    "they", "been", "said", "each", "which", "their",
    "what", "how", "who", "when", "where", "why",
    "does", "did", "will", "would", "could", "should",
})


class HallucinationChecker:
    """Checks whether an LLM answer is relevant to the question.

    Uses two signals:
    1. Embedding cosine similarity (question vs answer) — weight 0.3
    2. Keyword overlap (stopword-filtered) — weight 0.7

    Suspicious if: combined < 0.45 OR (overlap=0 AND similarity<0.65)
    """

    def __init__(self, similarity_weight: float = 0.3,
                 overlap_weight: float = 0.7,
                 combined_threshold: float = 0.45,
                 hard_gate_similarity: float = 0.65):
        self.similarity_weight = similarity_weight
        self.overlap_weight = overlap_weight
        self.combined_threshold = combined_threshold
        self.hard_gate_similarity = hard_gate_similarity

    @staticmethod
    def cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
        """Compute cosine similarity between two vectors."""
        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = sum(x * x for x in vec_a) ** 0.5
        norm_b = sum(x * x for x in vec_b) ** 0.5
        if norm_a and norm_b:
            return dot / (norm_a * norm_b)
        return 0.0

    @staticmethod
    def keyword_overlap(question: str, answer: str,
                        stopwords: Optional[frozenset] = None) -> float:
        """Compute keyword overlap ratio (question words found in answer)."""
        stops = stopwords or _STOPWORDS
        q_words = set(re.findall(r'\b\w{3,}\b', question.lower())) - stops
        a_words = set(re.findall(r'\b\w{3,}\b', answer.lower())) - stops
        # Empty q_words after stopword removal → 0.0 (not free pass)
        return len(q_words & a_words) / len(q_words) if q_words else 0.0

    def check(self, question: str, answer: str,
              q_vec: Optional[List[float]] = None,
              a_vec: Optional[List[float]] = None) -> HallucinationResult:
        """Check if answer is suspicious given the question.

        Args:
            question: The original question text (preferably English)
            answer: The LLM answer text (preferably English, truncated to ~500 chars)
            q_vec: Pre-computed question embedding (optional)
            a_vec: Pre-computed answer embedding (optional)

        Returns:
            HallucinationResult with relevance, keyword_overlap, is_suspicious, reason
        """
        # Compute embedding similarity if vectors provided
        if q_vec is not None and a_vec is not None:
            similarity = self.cosine_similarity(q_vec, a_vec)
        else:
            similarity = 1.0  # No embedding available → don't penalize

        overlap = self.keyword_overlap(question, answer)

        combined = self.similarity_weight * similarity + self.overlap_weight * overlap
        is_suspicious = combined < self.combined_threshold

        # Hard gate — no keyword overlap + low similarity → always suspicious
        if overlap == 0.0 and similarity < self.hard_gate_similarity:
            is_suspicious = True

        reason = ""
        if is_suspicious:
            reason = f"embed={similarity:.0%}, keyword={overlap:.0%}, combined={combined:.0%}"
            log.warning(f"⚠️ Hallusinaatio? {reason}")

        return HallucinationResult(
            relevance=similarity,
            keyword_overlap=overlap,
            is_suspicious=is_suspicious,
            reason=reason,
        )
