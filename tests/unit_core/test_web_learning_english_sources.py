"""Tests for EnglishSourceLearner — allowlist, budget, translation, provenance."""

import pytest
from core.english_source_learner import EnglishSourceLearner, SourceConfig, LearnedFact


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sources():
    return [
        SourceConfig("https://extension.org/beekeeping", "bee_science"),
        SourceConfig("https://beeaware.org.au/facts", "bee_science"),
        SourceConfig("https://weather.gov/api", "weather", trust_level=0.8),
    ]


@pytest.fixture
def learner(sources):
    return EnglishSourceLearner(sources=sources, budget_per_cycle=5)


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------

def test_allowlist_check(learner):
    """Exact URL match is allowed."""
    assert learner.is_allowed("https://extension.org/beekeeping") is True


def test_allowlist_prefix_match(learner):
    """URL starting with an allowlisted prefix is allowed."""
    assert learner.is_allowed("https://extension.org/beekeeping/varroa") is True


def test_allowlist_rejects_unknown(learner):
    """URL not in allowlist is rejected."""
    assert learner.is_allowed("https://evil.example.com/hack") is False


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

def test_ingest_fact_success(learner):
    """Valid fact from allowed source is ingested."""
    fact = learner.ingest_fact(
        "Varroa mites are the biggest threat to honeybees.",
        "https://extension.org/beekeeping",
        domain="bee_science",
    )
    assert fact is not None
    assert isinstance(fact, LearnedFact)
    assert fact.text_en == "Varroa mites are the biggest threat to honeybees."
    assert fact.source_url == "https://extension.org/beekeeping"
    assert fact.domain == "bee_science"
    assert fact.confidence == 0.7


def test_ingest_fact_rejected_not_allowed(learner):
    """Fact from disallowed source returns None."""
    fact = learner.ingest_fact(
        "Some random fact from untrusted source.",
        "https://untrusted.example.com/page",
    )
    assert fact is None
    assert learner.total_ingested == 0


def test_empty_text_rejected(learner):
    """Empty or too-short text is rejected."""
    assert learner.ingest_fact("", "https://extension.org/beekeeping") is None
    assert learner.ingest_fact("short", "https://extension.org/beekeeping") is None
    assert learner.ingest_fact("   tiny   ", "https://extension.org/beekeeping") is None
    assert learner.total_ingested == 0


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------

def test_budget_enforcement(learner):
    """Once budget is exhausted, further ingests return None."""
    url = "https://extension.org/beekeeping"
    for i in range(5):
        result = learner.ingest_fact(f"Fact number {i} about bees and beekeeping.", url)
        assert result is not None

    # Budget is 5 — next ingest should be rejected
    over = learner.ingest_fact("One more fact about bee colonies.", url)
    assert over is None
    assert learner.budget_remaining == 0
    assert len(learner.facts_this_cycle) == 5


def test_budget_remaining(sources):
    """budget_remaining decreases with each ingest."""
    learner = EnglishSourceLearner(sources=sources, budget_per_cycle=3)
    url = "https://weather.gov/api"
    assert learner.budget_remaining == 3
    learner.ingest_fact("Temperature today is 15 degrees Celsius.", url)
    assert learner.budget_remaining == 2


# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------

def test_translate_called(sources):
    """When translator is provided, it is called on ingest."""
    translated = []

    def mock_translator(text: str) -> str:
        translated.append(text)
        return f"[FI] {text}"

    learner = EnglishSourceLearner(
        sources=sources, translator=mock_translator, budget_per_cycle=10
    )
    fact = learner.ingest_fact(
        "Bees need water in summer.",
        "https://extension.org/beekeeping",
    )
    assert fact is not None
    assert fact.text_fi == "[FI] Bees need water in summer."
    assert len(translated) == 1


def test_translate_fallback(sources):
    """When translator raises, text_fi falls back to English."""

    def failing_translator(text: str) -> str:
        raise RuntimeError("Translation service down")

    learner = EnglishSourceLearner(
        sources=sources, translator=failing_translator, budget_per_cycle=10
    )
    fact = learner.ingest_fact(
        "Bees communicate through waggle dance.",
        "https://beeaware.org.au/facts",
    )
    assert fact is not None
    assert fact.text_fi == "Bees communicate through waggle dance."


def test_no_translator_keeps_english(sources):
    """Without translator, text_fi is the same as text_en."""
    learner = EnglishSourceLearner(sources=sources, translator=None)
    fact = learner.ingest_fact(
        "Queen bees can live up to five years.",
        "https://extension.org/beekeeping",
    )
    assert fact is not None
    assert fact.text_fi == fact.text_en


# ---------------------------------------------------------------------------
# Cycle management
# ---------------------------------------------------------------------------

def test_reset_cycle(learner):
    """reset_cycle clears facts and returns count."""
    url = "https://extension.org/beekeeping"
    learner.ingest_fact("Honeybees pollinate many crops worldwide.", url)
    learner.ingest_fact("A single hive can house 60000 bees.", url)
    count = learner.reset_cycle()
    assert count == 2
    assert len(learner.facts_this_cycle) == 0
    assert learner.budget_remaining == 5  # full budget restored


def test_total_ingested(learner):
    """total_ingested accumulates across cycles."""
    url = "https://extension.org/beekeeping"
    learner.ingest_fact("Worker bees live about six weeks.", url)
    learner.reset_cycle()
    learner.ingest_fact("Drones are male honeybees in the colony.", url)
    assert learner.total_ingested == 2


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

def test_provenance_recorded(learner):
    """Each fact records its source URL and domain."""
    fact = learner.ingest_fact(
        "Oxalic acid treats varroa effectively.",
        "https://beeaware.org.au/facts",
        domain="bee_science",
        confidence=0.9,
    )
    assert fact is not None
    assert fact.source_url == "https://beeaware.org.au/facts"
    assert fact.domain == "bee_science"
    assert fact.confidence == 0.9
    assert fact.timestamp > 0


# ---------------------------------------------------------------------------
# Default allowlist
# ---------------------------------------------------------------------------

def test_default_allowlist():
    """With no sources arg, default allowlist is used."""
    learner = EnglishSourceLearner()
    assert learner.is_allowed("https://extension.org/beekeeping") is True
    assert learner.is_allowed("https://beeaware.org.au/facts") is True
    assert learner.is_allowed("https://random.example.com") is False
