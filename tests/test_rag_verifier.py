"""Tests for RAG-based fact verification."""
import pytest
from core.rag_verifier import RAGVerifier, Claim, VerificationResult


class TestClaimExtraction:
    def test_extracts_declarative_sentences(self):
        v = RAGVerifier()
        claims = v.extract_claims("Mehiläispesän lämpötila on 35 astetta. Talvella pesä tarvitsee eristyksen.")
        assert len(claims) == 2

    def test_skips_greetings(self):
        v = RAGVerifier()
        claims = v.extract_claims("Hei! Tässä vastaus. Pesän lämpötila on normaali.")
        assert all("Hei" not in c.text for c in claims)

    def test_skips_questions(self):
        v = RAGVerifier()
        claims = v.extract_claims("Onko pesä kunnossa? Lämpötila on 35 astetta.")
        assert len(claims) == 1
        assert "35" in claims[0].text

    def test_empty_answer(self):
        v = RAGVerifier()
        assert v.extract_claims("") == []


class TestNumericContradiction:
    def test_detects_contradiction(self):
        v = RAGVerifier()
        assert v._check_numeric_contradiction(
            "lämpötila on 45 astetta",
            "lämpötila on 35 astetta") is True

    def test_no_contradiction_same_value(self):
        v = RAGVerifier()
        assert v._check_numeric_contradiction(
            "lämpötila on 35 astetta",
            "lämpötila on 35 astetta") is False

    def test_no_contradiction_different_entity(self):
        v = RAGVerifier()
        assert v._check_numeric_contradiction(
            "lämpötila on 35 astetta",
            "kosteus on 80 prosenttia") is False


class TestVerificationWithoutMemory:
    def test_returns_unverified_without_memory(self):
        v = RAGVerifier()  # no memory store
        result = v.verify("kysymys", "Pesän lämpötila on 35 astetta.")
        assert result.verified is False

    def test_empty_claims_verified(self):
        v = RAGVerifier()
        result = v.verify("hei", "ok")
        assert result.verified is True
        assert result.overall_score == 1.0

    def test_claim_default_values(self):
        c = Claim(text="test claim", source_sentence="test claim")
        assert c.verdict == "unsupported"
        assert c.confidence == 0.0
        assert c.evidence == []

    def test_verification_result_defaults(self):
        vr = VerificationResult()
        assert vr.overall_score == 0.5
        assert vr.verified is False
        assert vr.contradicted_count == 0
