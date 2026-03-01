"""
WaggleDance -- Normalizer Unit Tests
======================================
Tests for core.normalizer.normalize_fi() -- Voikko-powered Finnish text normalizer.

NOTE: Test INPUT strings use proper Finnish chars (a-umlaut, o-umlaut).
      Print labels use ASCII-safe descriptions for Windows cp1252 console.
"""
import time
import sys

PASS = 0
FAIL = 0


def check(name: str, got, expected):
    """Assert helper: got == expected."""
    global PASS, FAIL
    if got == expected:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")
        print(f"        expected: {expected!r}")
        print(f"        got:      {got!r}")


def check_true(name: str, condition: bool):
    """Assert helper: condition is True."""
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")
        print(f"        condition was False")


def test_basic_import():
    """Can we import normalize_fi without errors?"""
    from core.normalizer import normalize_fi
    check_true("import normalize_fi", callable(normalize_fi))
    return normalize_fi


def test_empty_string(normalize_fi):
    """Empty / whitespace input returns empty string."""
    check("empty string", normalize_fi(""), "")
    check("whitespace only", normalize_fi("   "), "")
    check("just punctuation", normalize_fi("???"), "")


def test_bee_terms_compound(normalize_fi):
    """Bee-domain compound words are expanded via bee_terms.yaml."""
    # NOTE: bee_terms.yaml keys use Finnish chars, so input must too
    result = normalize_fi("pes\u00e4kortti")  # pesäkortti
    check("pesakortti -> pesa kortti", result, "pes\u00e4 kortti")

    result = normalize_fi("hunajalinko")
    check("hunajalinko -> hunaja linko", result, "hunaja linko")

    result = normalize_fi("varroapudotus")
    check("varroapudotus -> varroa pudotus", result, "varroa pudotus")

    result = normalize_fi("s\u00e4hk\u00f6aita")  # sähköaita
    check("sahkoaita -> sahko aita", result, "s\u00e4hk\u00f6 aita")


def test_bee_terms_in_sentence(normalize_fi):
    """Bee terms expand correctly inside a larger sentence."""
    result = normalize_fi("mehil\u00e4ishoitaja")  # mehiläishoitaja
    # Should expand to "mehiläinen hoitaja" via bee_terms
    check_true("mehilaishoitaja compound has mehilainen",
               "mehil\u00e4inen" in result)
    check_true("mehilaishoitaja compound has hoitaja",
               "hoitaja" in result)


def test_stopword_removal(normalize_fi):
    """Finnish stopwords are removed."""
    result = normalize_fi("mik\u00e4 on varroa")  # mikä on varroa
    # "mikä" and "on" are stopwords, "varroa" should remain
    tokens = result.split()
    check_true("stopword 'mika' removed", "mik\u00e4" not in tokens)
    check_true("content word 'varroa' kept", "varroa" in tokens)


def test_stopword_heavy(normalize_fi):
    """A query dominated by stopwords still produces content tokens."""
    result = normalize_fi("milloin pit\u00e4\u00e4 tehd\u00e4 varroapudotus")
    # milloin, pitää, tehdä are stopwords; varroapudotus expands to varroa + pudotus
    check_true("stopword-heavy: varroa present", "varroa" in result)
    check_true("stopword-heavy: pudotus present", "pudotus" in result)
    check_true("stopword 'milloin' gone", "milloin" not in result)


def test_finnish_chars_preserved(normalize_fi):
    """Finnish characters are preserved in output."""
    # Use bee_terms which have Finnish chars in their expansion
    result = normalize_fi("pes\u00e4kortti")  # pesäkortti
    check_true("a-umlaut preserved in pesa", "pes\u00e4" in result)

    result = normalize_fi("p\u00f6rssis\u00e4hk\u00f6")  # pörssisähkö
    check_true("o-umlaut preserved in porssi",
               "p\u00f6rssi" in result or "p\u00f6rssis\u00e4hk\u00f6" in result)


def test_sort_words(normalize_fi):
    """sort_words=True produces alphabetically sorted tokens."""
    result_sorted = normalize_fi("hunaja pes\u00e4 varroa", sort_words=True)
    tokens_sorted = result_sorted.split()
    check("sort_words=True is sorted", tokens_sorted, sorted(tokens_sorted))


def test_sort_words_order_independent(normalize_fi):
    """Same words in different order produce same key when sorted."""
    key1 = normalize_fi("varroa pes\u00e4 hoito", sort_words=True)
    key2 = normalize_fi("pes\u00e4 hoito varroa", sort_words=True)
    check("order-independent cache key", key1, key2)


def test_trailing_punctuation(normalize_fi):
    """Trailing ?!. is stripped before processing."""
    r1 = normalize_fi("varroa?")
    r2 = normalize_fi("varroa!")
    r3 = normalize_fi("varroa.")
    r4 = normalize_fi("varroa")
    check("trailing ? stripped", r1, r4)
    check("trailing ! stripped", r2, r4)
    check("trailing . stripped", r3, r4)


def test_unknown_word_kept(normalize_fi):
    """Unknown/nonsense words are kept as-is (not dropped)."""
    result = normalize_fi("xyzzyplugh")
    check("unknown word kept", result, "xyzzyplugh")


def test_voikko_lemmatization(normalize_fi):
    """If Voikko is available, inflected forms get lemmatized."""
    from core.normalizer import _voikko
    result = normalize_fi("pes\u00e4ss\u00e4")  # pesässä
    if _voikko is not None:
        # With Voikko: "pesässä" should become "pesä" (inessive stripped)
        check_true("Voikko: pesassa lemmatized",
                   result != "pes\u00e4ss\u00e4")
        print(f"        (Voikko active, got: {result!r})")
    else:
        # Without Voikko: suffix-stripping fallback
        check_true("Fallback: pesassa processed", len(result) > 0)
        print(f"        (Voikko not available, suffix fallback, got: {result!r})")


def test_case_insensitive(normalize_fi):
    """Input is lowercased before processing."""
    r1 = normalize_fi("VARROA")
    r2 = normalize_fi("varroa")
    check("case insensitive", r1, r2)


def test_autocorrect_basic():
    """Correctly spelled words pass through unchanged."""
    from core.normalizer import autocorrect_fi
    result = autocorrect_fi("varroa")
    check("autocorrect passes correct word", result, "varroa")


def test_autocorrect_typo():
    """Misspelled word gets corrected if distance <= 2."""
    from core.normalizer import autocorrect_fi, _voikko
    if _voikko is not None:
        # With Voikko: "mehil\u00e4ien" -> "mehil\u00e4inen" (distance 1)
        result = autocorrect_fi("mehil\u00e4ien")
        check_true("autocorrect fixes close typo", result != "mehil\u00e4ien")
    else:
        # Without Voikko: returns original unchanged
        result = autocorrect_fi("mehil\u00e4ien")
        check("autocorrect fallback keeps original", result, "mehil\u00e4ien")


def test_autocorrect_preserves_sentence():
    """Autocorrect preserves spacing and punctuation."""
    from core.normalizer import autocorrect_fi
    result = autocorrect_fi("varroa on loinen")
    check_true("autocorrect preserves spaces", " " in result)
    check_true("autocorrect keeps word count", len(result.split()) == 3)


def test_dynamic_stopwords_content_kept(normalize_fi):
    """Content words (nouns, verbs, adjectives) are kept."""
    from core.normalizer import _voikko
    if _voikko is not None:
        result = normalize_fi("varroa hoito")
        check_true("dynamic stopwords keep nouns", "varroa" in result)
    else:
        result = normalize_fi("varroa hoito")
        check_true("fallback keeps content words", "varroa" in result)


def test_dynamic_stopwords_function_removed(normalize_fi):
    """Function words (conjunctions, prepositions) are removed."""
    from core.normalizer import _voikko
    result = normalize_fi("varroa ja hoito")
    # "ja" (and) should be removed -- it's in STOP_FI AND is a 'sidesana'
    tokens = result.split()
    check_true("stopwords remove 'ja'", "ja" not in tokens)


def test_dynamic_stopwords_fallback(normalize_fi):
    """STOP_FI fallback works when Voikko unavailable."""
    # "mik\u00e4" is in STOP_FI -- should be removed regardless of Voikko
    result = normalize_fi("mik\u00e4 on varroa")
    tokens = result.split()
    check_true("STOP_FI fallback removes mik\u00e4", "mik\u00e4" not in tokens)


def test_voikko_compound_splitting(normalize_fi):
    """Voikko WORDBASES auto-splits compounds not in bee_terms.

    These words are NOT in bee_terms.yaml, so they rely on Voikko WORDBASES.
    When Voikko is unavailable, they stay as-is (suffix-stripped at most).
    """
    from core.normalizer import _voikko

    if _voikko is not None:
        # --- With Voikko: WORDBASES should split compounds ---
        # lumikuorma = lumi + kuorma (snow load)
        r1 = normalize_fi("lumikuorma")
        check_true("WORDBASES: lumikuorma splits (lumi)",
                   "lumi" in r1)
        check_true("WORDBASES: lumikuorma splits (kuorma)",
                   "kuorma" in r1)

        # vesijohto = vesi + johto (water pipe)
        r2 = normalize_fi("vesijohto")
        check_true("WORDBASES: vesijohto splits (vesi)",
                   "vesi" in r2)

        # talvipakkanen = talvi + pakkanen (winter frost)
        r3 = normalize_fi("talvipakkanen")
        check_true("WORDBASES: talvipakkanen splits (talvi)",
                   "talvi" in r3)

        print(f"        (Voikko active: lumikuorma={r1!r}, "
              f"vesijohto={r2!r}, talvipakkanen={r3!r})")
    else:
        # --- Without Voikko: fallback, word stays as-is or suffix-stripped ---
        r1 = normalize_fi("lumikuorma")
        check_true("Fallback: lumikuorma processed", len(r1) > 0)

        r2 = normalize_fi("vesijohto")
        check_true("Fallback: vesijohto processed", len(r2) > 0)

        r3 = normalize_fi("talvipakkanen")
        check_true("Fallback: talvipakkanen processed", len(r3) > 0)

        print(f"        (Voikko not available, fallback: lumikuorma={r1!r}, "
              f"vesijohto={r2!r}, talvipakkanen={r3!r})")


def test_performance(normalize_fi):
    """100 normalize_fi calls in <50ms (generous budget including Voikko init)."""
    queries = [
        "miten k\u00e4sittelen varroapudotusta",
        "mik\u00e4 on varroa-kynnys",
        "pes\u00e4kortti puuttuu",
        "s\u00e4hk\u00f6aita karhuille",
        "milloin kev\u00e4ttarkastus",
    ] * 20  # 100 queries

    t0 = time.perf_counter()
    for q in queries:
        normalize_fi(q)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    check_true(f"100 queries in {elapsed_ms:.1f}ms (<50ms)", elapsed_ms < 50)


def main():
    print("=" * 60)
    print("  WaggleDance -- Normalizer Tests")
    print("=" * 60)

    normalize_fi = test_basic_import()

    print("\n--- Empty/edge cases ---")
    test_empty_string(normalize_fi)

    print("\n--- Bee terms compound expansion ---")
    test_bee_terms_compound(normalize_fi)
    test_bee_terms_in_sentence(normalize_fi)

    print("\n--- Stopword removal ---")
    test_stopword_removal(normalize_fi)
    test_stopword_heavy(normalize_fi)

    print("\n--- Finnish character preservation ---")
    test_finnish_chars_preserved(normalize_fi)

    print("\n--- Sort words ---")
    test_sort_words(normalize_fi)
    test_sort_words_order_independent(normalize_fi)

    print("\n--- Punctuation stripping ---")
    test_trailing_punctuation(normalize_fi)

    print("\n--- Unknown words ---")
    test_unknown_word_kept(normalize_fi)

    print("\n--- Voikko lemmatization ---")
    test_voikko_lemmatization(normalize_fi)

    print("\n--- Voikko WORDBASES compound splitting ---")
    test_voikko_compound_splitting(normalize_fi)

    print("\n--- Case insensitivity ---")
    test_case_insensitive(normalize_fi)

    print("\n--- Autocorrect ---")
    test_autocorrect_basic()
    test_autocorrect_typo()
    test_autocorrect_preserves_sentence()

    print("\n--- Dynamic stopwords ---")
    test_dynamic_stopwords_content_kept(normalize_fi)
    test_dynamic_stopwords_function_removed(normalize_fi)
    test_dynamic_stopwords_fallback(normalize_fi)

    print("\n--- Performance ---")
    test_performance(normalize_fi)

    print("\n" + "=" * 60)
    total = PASS + FAIL
    print(f"  RESULTS: {PASS}/{total} PASS, {FAIL} FAIL")
    if FAIL == 0:
        print("  ALL TESTS PASSED")
    print("=" * 60)
    return FAIL


if __name__ == "__main__":
    sys.exit(main())
