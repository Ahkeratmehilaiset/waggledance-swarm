"""Tests for LanguageReadiness — per-language capability reporting."""

import pytest
from core.language_readiness import LanguageReadiness, LanguageCapability


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def lr():
    return LanguageReadiness()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_register_language(lr):
    """Register a new language and retrieve it."""
    cap = lr.register("fi", embedding_available=True, tts_available=True)
    assert isinstance(cap, LanguageCapability)
    assert cap.language == "fi"
    assert cap.embedding_available is True
    assert cap.tts_available is True
    assert cap.translation_available is False  # not set


def test_update_existing(lr):
    """Registering same language again updates fields."""
    lr.register("en", embedding_available=False)
    cap = lr.register("en", embedding_available=True, stt_available=True)
    assert cap.embedding_available is True
    assert cap.stt_available is True


def test_get_returns_none_for_unknown(lr):
    """get() returns None for unregistered language."""
    assert lr.get("ja") is None


# ---------------------------------------------------------------------------
# Readiness score
# ---------------------------------------------------------------------------

def test_readiness_score_all_available(lr):
    """All capabilities enabled gives maximum score."""
    cap = lr.register(
        "fi",
        embedding_available=True,
        translation_available=True,
        tts_available=True,
        stt_available=True,
        knowledge_coverage=1.0,
    )
    # 0.3 + 0.2 + 0.15 + 0.15 + 0.2 = 1.0
    assert cap.readiness_score == pytest.approx(1.0)


def test_readiness_score_minimal(lr):
    """No capabilities gives zero score."""
    cap = lr.register("xx")
    assert cap.readiness_score == pytest.approx(0.0)


def test_readiness_score_partial(lr):
    """Partial capabilities give correct intermediate score."""
    cap = lr.register("en", embedding_available=True, knowledge_coverage=0.5)
    # 0.3 + 0.5*0.2 = 0.4
    assert cap.readiness_score == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# is_ready
# ---------------------------------------------------------------------------

def test_is_ready_threshold(lr):
    """is_ready compares readiness_score against threshold."""
    lr.register("fi", embedding_available=True, translation_available=True)
    # score = 0.3 + 0.2 = 0.5
    assert lr.is_ready("fi", min_score=0.5) is True
    assert lr.is_ready("fi", min_score=0.51) is False


def test_is_ready_unknown_language(lr):
    """Unknown language is never ready."""
    assert lr.is_ready("zz") is False


# ---------------------------------------------------------------------------
# all_languages
# ---------------------------------------------------------------------------

def test_all_languages_sorted(lr):
    """all_languages returns languages sorted by readiness_score descending."""
    lr.register("xx")  # score 0.0
    lr.register("en", embedding_available=True)  # score 0.3
    lr.register("fi", embedding_available=True, translation_available=True,
                tts_available=True)  # score 0.65

    langs = lr.all_languages()
    assert len(langs) == 3
    assert langs[0].language == "fi"
    assert langs[1].language == "en"
    assert langs[2].language == "xx"


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------

def test_summary_format(lr):
    """summary() returns dict with expected keys."""
    lr.register("fi", embedding_available=True, knowledge_coverage=0.8)
    lr.register("en", translation_available=True)

    s = lr.summary()
    assert "fi" in s
    assert "en" in s
    assert set(s["fi"].keys()) == {
        "readiness_score", "embedding", "translation", "tts", "stt",
        "knowledge_coverage",
    }
    assert s["fi"]["embedding"] is True
    assert s["fi"]["knowledge_coverage"] == 0.8
    assert s["fi"]["readiness_score"] == pytest.approx(0.46)  # 0.3 + 0.8*0.2


def test_summary_empty(lr):
    """summary() on empty registry returns empty dict."""
    assert lr.summary() == {}


# ---------------------------------------------------------------------------
# LanguageCapability notes
# ---------------------------------------------------------------------------

def test_capability_notes():
    """notes field works as expected."""
    cap = LanguageCapability(language="fi", notes=["Whisper small model"])
    assert cap.notes == ["Whisper small model"]
    cap.notes.append("Piper TTS fi_FI-harri")
    assert len(cap.notes) == 2
