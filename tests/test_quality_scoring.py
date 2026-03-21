"""Tests for composite quality scoring in LearningEngine."""
import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _make_engine():
    """Create a LearningEngine with mocked LLM."""
    from core.learning_engine import LearningEngine
    llm = MagicMock()
    memory = MagicMock()
    return LearningEngine(llm_evaluator=llm, memory=memory)


def test_composite_produces_non_binary():
    """Composite score should differentiate between different response qualities."""
    from core.learning_engine import QualityScore
    engine = _make_engine()

    # Short low-quality response
    score_low = QualityScore(
        agent_id="a1", agent_type="test", score=8.0,
        reasoning="ok", prompt_preview="Q", response_preview="yes")
    item_low = {"response": "ok"}  # Very short
    result_low = engine._composite_score(score_low, item_low)

    # Good response with domain terms and numbers
    score_high = QualityScore(
        agent_id="a2", agent_type="test", score=8.0,
        reasoning="ok", prompt_preview="Q", response_preview="good")
    item_high = {
        "response": "Mehiläispesässä tulisi olla noin 15-20 kg hunajaa "
                    "talvivarastoina. Varroapunkkien torjunta aloitetaan "
                    "oksaalihapolla lokakuussa."
    }
    result_high = engine._composite_score(score_high, item_high)

    # Both had same LLM score (8.0) but composite should differ
    assert result_high > result_low, (
        f"Detailed answer ({result_high:.2f}) should score higher than "
        f"short answer ({result_low:.2f})")


def test_composite_clamped_1_10():
    """Composite score is always within [1.0, 10.0]."""
    from core.learning_engine import QualityScore
    engine = _make_engine()

    # Very low LLM score with penalties
    score = QualityScore(
        agent_id="a", agent_type="t", score=1.0,
        reasoning="bad", prompt_preview="Q", response_preview="x")
    result = engine._composite_score(score, {"response": "x"})
    assert 1.0 <= result <= 10.0, f"Score out of range: {result}"

    # Very high LLM score with bonuses
    score2 = QualityScore(
        agent_id="a", agent_type="t", score=10.0,
        reasoning="great", prompt_preview="Q", response_preview="great")
    result2 = engine._composite_score(score2, {
        "response": "Mehiläisten 15 pesää varroa-hoito talvella 2025"})
    assert 1.0 <= result2 <= 10.0, f"Score out of range: {result2}"


def test_length_penalty_for_very_short():
    """Very short responses get a length penalty."""
    from core.learning_engine import QualityScore
    engine = _make_engine()

    score = QualityScore(
        agent_id="a", agent_type="t", score=8.0,
        reasoning="", prompt_preview="Q", response_preview="y")

    # < 20 chars: penalty
    r_short = engine._composite_score(score, {"response": "kyllä"})
    # 50-300 chars: bonus
    r_good = engine._composite_score(score, {
        "response": "Mehiläiset tarvitsevat puhdasta vettä erityisesti "
                    "keväällä kun poikastuotanto on käynnissä."})

    assert r_good > r_short


def test_specificity_bonus():
    """Responses with domain terms and numbers get bonus."""
    from core.learning_engine import QualityScore
    engine = _make_engine()

    score = QualityScore(
        agent_id="a", agent_type="t", score=6.0,
        reasoning="", prompt_preview="Q", response_preview="")

    r_generic = engine._composite_score(score, {
        "response": "This is a general answer about something without detail"
    })
    r_specific = engine._composite_score(score, {
        "response": "Mehiläispesässä on noin 50000 mehiläistä kesäkuussa"
    })

    assert r_specific > r_generic


def test_quality_eval_prompt_has_examples():
    """QUALITY_EVAL_PROMPT should contain score examples."""
    from core.learning_engine import QUALITY_EVAL_PROMPT
    assert "3/10" in QUALITY_EVAL_PROMPT
    assert "5/10" in QUALITY_EVAL_PROMPT
    assert "7/10" in QUALITY_EVAL_PROMPT
    assert "8.5/10" in QUALITY_EVAL_PROMPT


if __name__ == "__main__":
    tests = [
        test_composite_produces_non_binary,
        test_composite_clamped_1_10,
        test_length_penalty_for_very_short,
        test_specificity_bonus,
        test_quality_eval_prompt_has_examples,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS: {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL: {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
