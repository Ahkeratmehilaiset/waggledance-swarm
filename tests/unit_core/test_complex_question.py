"""Tests for _is_complex_question gate in memory_engine.py."""
from core.memory_engine import Consciousness


class _Stub:
    """Minimal stub to call _is_complex_question without full Consciousness init."""
    _is_complex_question = Consciousness._is_complex_question


def _is_complex(msg: str) -> bool:
    return _Stub()._is_complex_question(msg)


def test_simple_factual_not_complex():
    """Short factual question should stay on fast path."""
    assert _is_complex("Mitä on varroa?") is False


def test_miksi_is_complex():
    """'Miksi' (why) always needs phi4-mini."""
    assert _is_complex("Miksi mehiläiset kuolevat?") is True


def test_miten_is_complex():
    """'Miten' (how) always needs phi4-mini."""
    assert _is_complex("Miten varroa leviää?") is True


def test_selita_is_complex():
    """'Selitä' (explain) always needs phi4-mini."""
    assert _is_complex("Selitä miten hunajaa kerätään") is True


def test_why_english_is_complex():
    assert _is_complex("Why do bees die in winter?") is True


def test_short_stays_fast():
    """Short factual question without complex markers."""
    assert _is_complex("Paljonko kello?") is False


def test_long_mita_is_complex():
    """'Mitä' with >= 6 words is complex."""
    assert _is_complex("Mitä kaikkea pitää tehdä kun mehiläiset sairastuvat talvella") is True


def test_short_mita_not_complex():
    """'Mitä' with < 6 words is NOT complex (stays fast)."""
    assert _is_complex("Mitä on varroa?") is False
