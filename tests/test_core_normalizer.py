"""Finnish Normalizer core module — test suite (~12 tests).

Tests: normalize_fi() basic words, stopword removal, sort_words,
empty/punctuation edge-cases, STOP_FI set, fallback suffix-stripping,
Levenshtein distance, autocorrect fallback (no Voikko needed).
Voikko-dependent tests skip gracefully when Voikko unavailable.
"""
import sys, os, ast
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Helpers ─────────────────────────────────────────────────────────────

def _voikko_available() -> bool:
    """Return True if libvoikko + dictionary are installed."""
    try:
        from core import normalizer as _nm
        _nm._init_voikko()
        return _nm._voikko is not None
    except Exception:
        return False


# ── 1. Syntax ────────────────────────────────────────────────────────────

def test_syntax_normalizer():
    path = os.path.join(os.path.dirname(__file__), "..", "core", "normalizer.py")
    with open(path, "r", encoding="utf-8") as f:
        ast.parse(f.read())
    print("  [PASS] core/normalizer.py syntax valid")


# ── 2. Module-level constants ────────────────────────────────────────────

def test_stop_fi_set_populated():
    from core.normalizer import STOP_FI
    assert isinstance(STOP_FI, set)
    assert len(STOP_FI) > 10
    assert "miten" in STOP_FI   # question word
    assert "onko" in STOP_FI    # question verb
    assert "ja" in STOP_FI      # conjunction
    assert "tai" in STOP_FI     # disjunction
    print("  [PASS] STOP_FI set populated with Finnish stopwords")


def test_fi_suffixes_list():
    from core.normalizer import _FI_SUFFIXES
    assert isinstance(_FI_SUFFIXES, list)
    assert len(_FI_SUFFIXES) > 5
    # Common Finnish case suffixes must be present
    assert "ssa" in _FI_SUFFIXES or "ssä" in _FI_SUFFIXES
    assert "lla" in _FI_SUFFIXES or "llä" in _FI_SUFFIXES
    print("  [PASS] _FI_SUFFIXES list contains Finnish case endings")


# ── 3. normalize_fi — basic behaviour ────────────────────────────────────

def test_normalize_empty_string():
    from core.normalizer import normalize_fi
    assert normalize_fi("") == ""
    assert normalize_fi("   ") == ""
    print("  [PASS] normalize_fi returns empty string for empty input")


def test_normalize_strips_trailing_punctuation():
    from core.normalizer import normalize_fi
    r1 = normalize_fi("hunaja?")
    r2 = normalize_fi("hunaja.")
    r3 = normalize_fi("hunaja!")
    r4 = normalize_fi("hunaja")
    # All should produce the same tokens (punctuation stripped)
    assert r1 == r2 == r3 == r4
    print("  [PASS] normalize_fi strips trailing punctuation")


def test_normalize_lowercase():
    from core.normalizer import normalize_fi
    r1 = normalize_fi("Hunaja")
    r2 = normalize_fi("hunaja")
    r3 = normalize_fi("HUNAJA")
    # All forms should normalise to same tokens
    assert r1 == r2 == r3
    print("  [PASS] normalize_fi lowercases input")


def test_normalize_removes_stopwords():
    from core.normalizer import normalize_fi
    # "miten" is a stopword — should be removed
    result = normalize_fi("miten hunaja")
    tokens = result.split()
    # "miten" should be absent; "hunaja" (or its lemma) should remain
    assert "miten" not in tokens
    assert len(tokens) >= 1
    print("  [PASS] normalize_fi removes Finnish stopwords")


def test_normalize_sort_words():
    from core.normalizer import normalize_fi
    r1 = normalize_fi("hunaja varroa pesä", sort_words=False)
    r2 = normalize_fi("pesä varroa hunaja", sort_words=True)
    r3 = normalize_fi("hunaja varroa pesä", sort_words=True)
    # With sort_words=True, order should be consistent regardless of input order
    assert r2 == r3
    print("  [PASS] normalize_fi sort_words produces consistent keys")


def test_normalize_returns_string():
    from core.normalizer import normalize_fi
    result = normalize_fi("mehiläinen")
    assert isinstance(result, str)
    # Should not be empty for a content word
    assert len(result) > 0
    print("  [PASS] normalize_fi returns non-empty string for content word")


# ── 4. Fallback suffix-stripping (_lemmatize_word) ───────────────────────

def test_lemmatize_suffix_stripping():
    from core.normalizer import _lemmatize_word
    # Force fallback path: temporarily hide Voikko
    import core.normalizer as nm
    orig_voikko = nm._voikko
    nm._voikko = None
    try:
        # "hunajassa" has suffix "ssa" -> "hunaj" (stripped)
        result = _lemmatize_word("hunajassa")
        assert isinstance(result, str)
        assert len(result) >= 2
        # The stem should be shorter than the original
        assert len(result) <= len("hunajassa")
    finally:
        nm._voikko = orig_voikko
    print("  [PASS] _lemmatize_word suffix-stripping fallback works")


# ── 5. Levenshtein distance ──────────────────────────────────────────────

def test_levenshtein_identical():
    from core.normalizer import _levenshtein
    assert _levenshtein("hunaja", "hunaja") == 0
    print("  [PASS] Levenshtein distance = 0 for identical strings")


def test_levenshtein_one_edit():
    from core.normalizer import _levenshtein
    # "cat" vs "bat" = 1 substitution
    assert _levenshtein("cat", "bat") == 1
    print("  [PASS] Levenshtein distance = 1 for one substitution")


def test_levenshtein_empty():
    from core.normalizer import _levenshtein
    assert _levenshtein("", "abc") == 3
    assert _levenshtein("abc", "") == 3
    assert _levenshtein("", "") == 0
    print("  [PASS] Levenshtein handles empty strings")


# ── 6. autocorrect fallback (no Voikko) ─────────────────────────────────

def test_autocorrect_no_voikko_passthrough():
    """When Voikko is unavailable, autocorrect returns input unchanged."""
    import core.normalizer as nm
    orig_voikko = nm._voikko
    orig_loaded = nm._voikko_loaded
    nm._voikko = None
    # Make _init_voikko a no-op to prevent re-loading during the test
    nm._voikko_loaded = True
    try:
        from core.normalizer import autocorrect_fi
        text = "hunaja on makeaa"
        result = autocorrect_fi(text)
        assert result == text
    finally:
        nm._voikko = orig_voikko
        nm._voikko_loaded = orig_loaded
    print("  [PASS] autocorrect_fi returns input unchanged when Voikko unavailable")


# ── Runner ──────────────────────────────────────────────────────────────

ALL_TESTS = [
    test_syntax_normalizer,
    test_stop_fi_set_populated,
    test_fi_suffixes_list,
    test_normalize_empty_string,
    test_normalize_strips_trailing_punctuation,
    test_normalize_lowercase,
    test_normalize_removes_stopwords,
    test_normalize_sort_words,
    test_normalize_returns_string,
    test_lemmatize_suffix_stripping,
    test_levenshtein_identical,
    test_levenshtein_one_edit,
    test_levenshtein_empty,
    test_autocorrect_no_voikko_passthrough,
]


if __name__ == "__main__":
    passed = 0
    failed = 0
    errors = []

    print("\n" + "=" * 60)
    print("core/normalizer.py -- {0} tests".format(len(ALL_TESTS)))
    print("=" * 60 + "\n")

    for test in ALL_TESTS:
        try:
            test()
            passed += 1
        except Exception as e:
            failed += 1
            errors.append((test.__name__, str(e)))
            print("  [FAIL] {0}: {1}".format(test.__name__, e))

    print("\n" + "=" * 60)
    print("Result: {0}/{1} passed, {2} failed".format(passed, passed + failed, failed))
    if errors:
        print("\nFailed tests:")
        for name, err in errors:
            print("  - {0}: {1}".format(name, err))
    print("=" * 60)

    sys.exit(0 if failed == 0 else 1)
