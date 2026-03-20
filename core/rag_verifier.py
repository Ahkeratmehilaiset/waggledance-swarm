"""RAG-based fact verification — retrieves evidence and cross-checks claims.

Pipeline:
1. Extract claims from LLM answer (sentence-level)
2. For each claim: search memory + vector store for supporting evidence
3. Score each claim: supported / contradicted / unsupported
4. Aggregate → overall verification score

This is NOT a replacement for hallucination_checker.py — it's an additional
layer that runs AFTER the fast heuristic check when confidence is borderline.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

log = logging.getLogger("waggledance.rag_verifier")


@dataclass
class Claim:
    """A single factual claim extracted from an answer."""
    text: str
    source_sentence: str
    evidence: List[str] = field(default_factory=list)
    verdict: str = "unsupported"  # "supported" | "contradicted" | "unsupported"
    confidence: float = 0.0


@dataclass
class VerificationResult:
    """Result of RAG verification on a complete answer."""
    claims: List[Claim] = field(default_factory=list)
    supported_ratio: float = 0.0
    contradicted_count: int = 0
    unsupported_count: int = 0
    overall_score: float = 0.5  # 0.0 = all contradicted, 1.0 = all supported
    verified: bool = False  # True if verification actually ran


class RAGVerifier:
    """Retrieval-Augmented fact verification.

    Requires: memory_store (ChromaDB MemoryStore) for evidence retrieval.
    Optional: consciousness (for embedding computation).
    """

    def __init__(self, memory_store=None, consciousness=None,
                 min_claim_length: int = 15,
                 support_threshold: float = 0.55,
                 contradiction_threshold: float = 0.35):
        self._memory = memory_store
        self._consciousness = consciousness
        self._min_claim_length = min_claim_length
        self._support_threshold = support_threshold
        self._contradiction_threshold = contradiction_threshold

    def extract_claims(self, answer: str) -> List[Claim]:
        """Split answer into verifiable claims.

        Filters out: questions, greetings, meta-commentary, very short sentences.
        Keeps: declarative statements with factual content.
        """
        sentences = [s.strip() for s in re.split(r'[.!]\s+', answer) if s.strip()]
        claims = []
        skip_patterns = [
            r'^(hei|moi|terve|hello|hi)\b',
            r'\?$',
            r'^(kyllä|ei|joo|ok)\b',
            r'^(anteeksi|sorry|valitettavasti)',
        ]
        for sent in sentences:
            if len(sent) < self._min_claim_length:
                continue
            if any(re.search(p, sent, re.IGNORECASE) for p in skip_patterns):
                continue
            claims.append(Claim(text=sent, source_sentence=sent))
        return claims

    def verify_claim(self, claim: Claim, question_context: str = "") -> Claim:
        """Verify a single claim against memory store.

        1. Embed the claim
        2. Search memory for similar content
        3. Compare: overlap → supported, contradiction → contradicted
        """
        if not self._memory or not self._consciousness:
            claim.verdict = "unsupported"
            claim.confidence = 0.0
            return claim

        try:
            embedding = None
            if hasattr(self._consciousness, 'embed') and self._consciousness.embed.available:
                claim_text = claim.text
                if hasattr(self._consciousness, 'opus') and self._consciousness.opus.available:
                    translated = self._consciousness.opus.fi_to_en(claim_text)
                    if translated:
                        claim_text = translated
                embedding = self._consciousness.embed.embed_query(claim_text)

            if embedding is None:
                claim.verdict = "unsupported"
                return claim

            matches = self._memory.search(embedding, top_k=5, min_score=0.3)
            if not matches:
                claim.verdict = "unsupported"
                claim.confidence = 0.1
                return claim

            # Score against matches
            claim_words = set(re.findall(r'\b\w{4,}\b', claim.text.lower()))
            best_overlap = 0.0
            best_evidence = ""

            for match in matches:
                match_words = set(re.findall(r'\b\w{4,}\b', match.text.lower()))
                if not claim_words:
                    continue
                overlap = len(claim_words & match_words) / len(claim_words)
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_evidence = match.text

            has_contradiction = self._check_numeric_contradiction(claim.text, best_evidence)

            if has_contradiction:
                claim.verdict = "contradicted"
                claim.confidence = 0.7
                claim.evidence = [best_evidence]
            elif best_overlap >= self._support_threshold:
                claim.verdict = "supported"
                claim.confidence = min(0.95, best_overlap)
                claim.evidence = [best_evidence]
            elif best_overlap >= self._contradiction_threshold:
                claim.verdict = "supported"
                claim.confidence = best_overlap
                claim.evidence = [best_evidence]
            else:
                claim.verdict = "unsupported"
                claim.confidence = best_overlap

        except Exception as e:
            log.warning("Claim verification failed: %s", e)
            claim.verdict = "unsupported"
            claim.confidence = 0.0

        return claim

    def _check_numeric_contradiction(self, claim_text: str, evidence_text: str) -> bool:
        """Check if claim and evidence have conflicting numbers for same entity."""
        if not evidence_text:
            return False
        claim_nums = dict(re.findall(
            r'(\w{3,})\s+(?:on|is|=|oli|was)\s+(\d+(?:\.\d+)?)',
            claim_text.lower()))
        evidence_nums = dict(re.findall(
            r'(\w{3,})\s+(?:on|is|=|oli|was)\s+(\d+(?:\.\d+)?)',
            evidence_text.lower()))
        for entity, claim_val in claim_nums.items():
            if entity in evidence_nums and evidence_nums[entity] != claim_val:
                return True
        return False

    def verify(self, question: str, answer: str) -> VerificationResult:
        """Full verification pipeline: extract claims → verify each → aggregate."""
        claims = self.extract_claims(answer)

        if not claims:
            return VerificationResult(verified=True, overall_score=1.0)

        if not self._memory or not self._consciousness:
            return VerificationResult(
                claims=claims,
                unsupported_count=len(claims),
                verified=False)

        for claim in claims:
            self.verify_claim(claim, question_context=question)

        supported = sum(1 for c in claims if c.verdict == "supported")
        contradicted = sum(1 for c in claims if c.verdict == "contradicted")
        unsupported = sum(1 for c in claims if c.verdict == "unsupported")

        total = len(claims)
        score = (supported - contradicted * 2) / max(total, 1)
        score = max(0.0, min(1.0, (score + 1.0) / 2.0))

        return VerificationResult(
            claims=claims,
            supported_ratio=supported / total if total > 0 else 0.0,
            contradicted_count=contradicted,
            unsupported_count=unsupported,
            overall_score=score,
            verified=True)
