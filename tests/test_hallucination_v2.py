"""Tests for upgraded hallucination checker (5 signals)."""
import pytest
from dataclasses import dataclass
from core.hallucination_checker import HallucinationChecker, HallucinationResult


@dataclass
class FakeMemoryMatch:
    text: str
    score: float = 0.9


class TestSourceGrounding:
    def test_grounded_answer(self):
        hc = HallucinationChecker()
        matches = [FakeMemoryMatch("Mehiläispesän lämpötila on 35 astetta")]
        score = hc.source_grounding("Pesän lämpötila on 35 astetta.", matches)
        assert score > 0.5

    def test_ungrounded_answer(self):
        hc = HallucinationChecker()
        score = hc.source_grounding("Jupiter has 95 known moons.", [])
        assert score == 0.0

    def test_empty_answer(self):
        hc = HallucinationChecker()
        score = hc.source_grounding("", [FakeMemoryMatch("something")])
        assert score == 1.0


class TestSelfConsistency:
    def test_consistent(self):
        hc = HallucinationChecker()
        assert hc.self_consistency("Lämpötila on 35 astetta.") == 1.0

    def test_contradiction(self):
        hc = HallucinationChecker()
        score = hc.self_consistency("lämpötila on 20 astetta. lämpötila on 35 astetta.")
        assert score < 1.0

    def test_no_numbers(self):
        hc = HallucinationChecker()
        assert hc.self_consistency("Mehiläiset lentävät kukkien luo.") == 1.0


class TestCorrectionsPenalty:
    def test_no_corrections(self):
        hc = HallucinationChecker()
        assert hc.corrections_penalty("kysymys", []) == 1.0

    def test_high_similarity_correction(self):
        hc = HallucinationChecker()
        matches = [FakeMemoryMatch("sama kysymys", score=0.95)]
        score = hc.corrections_penalty("kysymys", matches)
        assert score < 0.1

    def test_low_similarity_correction(self):
        hc = HallucinationChecker()
        matches = [FakeMemoryMatch("eri asia", score=0.2)]
        score = hc.corrections_penalty("kysymys", matches)
        assert score > 0.7


class TestBackwardCompatibility:
    def test_old_signature_still_works(self):
        """check() with only question + answer must not crash."""
        hc = HallucinationChecker()
        result = hc.check("Mikä on pesän lämpötila?", "Se on 35 astetta.")
        assert isinstance(result, HallucinationResult)
        assert hasattr(result, 'source_grounding')

    def test_old_signature_with_vectors(self):
        hc = HallucinationChecker()
        fake_vec = [0.1] * 10
        result = hc.check("test", "test answer", q_vec=fake_vec, a_vec=fake_vec)
        assert isinstance(result, HallucinationResult)

    def test_new_fields_present(self):
        hc = HallucinationChecker()
        result = hc.check("test", "test answer")
        assert hasattr(result, 'combined_score')
        assert hasattr(result, 'self_consistency')
        assert hasattr(result, 'corrections_penalty')
