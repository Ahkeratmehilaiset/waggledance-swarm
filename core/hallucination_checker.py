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
    source_grounding: float = 1.0       # memory-backed verification
    self_consistency: float = 1.0        # internal contradiction check
    corrections_penalty: float = 1.0     # prior correction match
    combined_score: float = 1.0          # weighted aggregate
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
    """Checks whether an LLM answer is relevant and grounded.

    Uses five signals:
    1. Embedding cosine similarity (question vs answer) — weight 0.15
    2. Keyword overlap (stopword-filtered) — weight 0.25
    3. Source grounding (memory-backed verification) — weight 0.30
    4. Self-consistency (internal contradiction check) — weight 0.15
    5. Corrections penalty (prior correction match) — weight 0.15

    Suspicious if: combined < 0.45 OR (overlap=0 AND similarity<0.65)
                   OR (memory_matches provided AND grounding=0)
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

    def source_grounding(self, answer: str, memory_matches: list) -> float:
        """Check what fraction of answer claims are grounded in retrieved memories.

        Returns 0.0 if no memory matches (ungrounded), 1.0 if fully grounded.
        """
        if not memory_matches:
            return 0.0
        answer_sentences = [s.strip() for s in re.split(r'[.!?]', answer) if len(s.strip()) > 10]
        if not answer_sentences:
            return 1.0  # No substantial claims to check
        grounded = 0
        for sentence in answer_sentences:
            sent_words = set(re.findall(r'\b\w{4,}\b', sentence.lower())) - _STOPWORDS
            if not sent_words:
                grounded += 1
                continue
            for match in memory_matches:
                match_text = getattr(match, 'text', str(match))
                match_words = set(re.findall(r'\b\w{4,}\b', match_text.lower())) - _STOPWORDS
                if sent_words and len(sent_words & match_words) / len(sent_words) > 0.3:
                    grounded += 1
                    break
        return grounded / len(answer_sentences)

    def self_consistency(self, answer: str) -> float:
        """Check for internal numeric contradictions in the answer.

        Finds patterns like 'X is 20' and 'X is 35' in same answer.
        Returns 1.0 if consistent, 0.0 if contradictory.
        """
        numbers = re.findall(
            r'(\w{3,})\s+(?:on|is|=|oli|was|equals)\s+(\d+(?:\.\d+)?)',
            answer.lower())
        if not numbers:
            return 1.0
        seen = {}
        contradictions = 0
        for name, value in numbers:
            if name in seen and seen[name] != value:
                contradictions += 1
            seen[name] = value
        return max(0.0, 1.0 - contradictions / len(numbers))

    def corrections_penalty(self, question: str, corrections_matches: list) -> float:
        """Penalize if similar questions have been corrected before.

        Returns 1.0 if no prior corrections, lower if question matches known corrections.
        """
        if not corrections_matches:
            return 1.0
        max_score = 0.0
        for match in corrections_matches:
            score = getattr(match, 'score', 0.0)
            if score > max_score:
                max_score = score
        return max(0.0, 1.0 - max_score)

    def check(self, question: str, answer: str,
              q_vec: Optional[List[float]] = None,
              a_vec: Optional[List[float]] = None,
              memory_matches=None, corrections_matches=None) -> HallucinationResult:
        """Check if answer is suspicious — 5 signals (upgraded from 2).

        New optional params are backward-compatible: omitting them
        gives the same behavior as before (relevance + keyword only).
        """
        # Signal 1: Embedding similarity (existing)
        if q_vec is not None and a_vec is not None:
            similarity = self.cosine_similarity(q_vec, a_vec)
        else:
            similarity = 1.0  # No embedding available → don't penalize

        # Signal 2: Keyword overlap (existing)
        overlap = self.keyword_overlap(question, answer)

        # Signal 3: Source grounding (NEW)
        grounding = self.source_grounding(answer, memory_matches or [])

        # Signal 4: Self-consistency (NEW)
        consistency = self.self_consistency(answer)

        # Signal 5: Corrections penalty (NEW)
        corr_penalty = self.corrections_penalty(question, corrections_matches or [])

        # Weighted combination — grounding is heaviest signal
        combined = (0.15 * similarity + 0.25 * overlap + 0.30 * grounding
                    + 0.15 * consistency + 0.15 * corr_penalty)

        is_suspicious = combined < self.combined_threshold

        # Hard gate — no keyword overlap + low similarity → always suspicious
        if overlap == 0.0 and similarity < self.hard_gate_similarity:
            is_suspicious = True

        # Hard gate — zero grounding with actual memory available
        if memory_matches and grounding == 0.0:
            is_suspicious = True

        reason = ""
        if is_suspicious:
            reason = (f"embed={similarity:.0%}, keyword={overlap:.0%}, "
                      f"grounding={grounding:.0%}, consistency={consistency:.0%}, "
                      f"corrections={corr_penalty:.0%}, combined={combined:.0%}")
            log.warning(f"⚠️ Hallusinaatio? {reason}")

        return HallucinationResult(
            relevance=similarity,
            keyword_overlap=overlap,
            source_grounding=grounding,
            self_consistency=consistency,
            corrections_penalty=corr_penalty,
            combined_score=combined,
            is_suspicious=is_suspicious,
            reason=reason,
        )
