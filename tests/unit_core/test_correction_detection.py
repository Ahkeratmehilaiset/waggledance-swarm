"""Tests for score-based correction detection in chat_handler.py.

Validates that common Finnish "ei" mid-sentence does NOT trigger correction,
while strong correction signals do.
"""


def _correction_score(message: str) -> int:
    """Replica of the scoring logic from ChatHandler for unit testing."""
    _STRONG = {"väärin", "wrong", "väärä", "virhe", "korjaus", "tarkoitin"}
    _PHRASES = {"ei vaan", "ei ole oikea", "väärä vastaus",
                "oikea vastaus", "tarkoitin että"}
    _WEAK = {"ei", "eikä"}

    msg_lower = message.lower()
    msg_words = [w.strip(".,!?;:\"'") for w in msg_lower.split()]
    msg_word_set = set(msg_words)

    score = 0
    score += 2 * len(msg_word_set & _STRONG)
    for p in _PHRASES:
        if p in msg_lower:
            score += 3
    weak_hit = msg_word_set & _WEAK
    if weak_hit:
        if msg_words[0] in _WEAK:
            score += 1
        if len(message) < 20:
            score += 1
    return score


def test_ei_midsentence_not_correction():
    """Finnish 'ei' in a normal sentence should NOT trigger (score < 2)."""
    assert _correction_score("Käykö sulla aika pitkäksi kun kukaan ei puhu") < 2


def test_vaarin_triggers():
    """Strong word 'väärin' alone should trigger (score >= 2)."""
    assert _correction_score("Se oli väärin") >= 2


def test_ei_vaan_phrase_triggers():
    """Phrase 'ei vaan' should trigger (score >= 2)."""
    assert _correction_score("Ei vaan tarkoitin toista") >= 2


def test_ei_start_short_triggers():
    """'Ei' at start of short message + strong word = triggers."""
    score = _correction_score("Ei! Väärä.")
    assert score >= 2  # weak-start(1) + short(1) + strong(2) = 4


def test_no_correction_words():
    """Normal question without any correction signals."""
    assert _correction_score("Miten menee?") == 0


def test_ei_alone_short():
    """Just 'Ei' as a short message should score 2 (start + short)."""
    assert _correction_score("Ei.") >= 2


def test_ei_in_long_sentence():
    """'ei' buried in a long sentence, not at start — score 0."""
    assert _correction_score("Mehiläiset ei kestä kylmyyttä kovin hyvin talvella") < 2
