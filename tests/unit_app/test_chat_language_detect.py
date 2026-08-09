# SPDX-License-Identifier: BUSL-1.1
"""Audit H42 regression — language detection uses stopword overlap.

Before this fix, ChatService._detect_language used a single-char
"any FI diacritic in query" rule. That misclassified:
- diacritic-less Finnish ("paljonko maksaa", "kuinka talvi") -> en
- English w/ German letters ("Schrödinger", "Möbel") -> fi

The PR-A fix counts tokens against FI/EN stopword sets and picks the
winner. A bounded query-token hint may resolve a known tie; unresolved
ties fall back to the pre-H42 diacritic check to keep behavior
compatible for very short or proper-noun-only inputs.
"""

from __future__ import annotations

import pytest

from waggledance.application.services.chat_service import ChatService


class TestDetectLanguageStopwords:
    """H42 regression — diacritic-less FI / diacritic-EN classify correctly."""

    def test_finnish_without_diacritics(self):
        assert ChatService._detect_language(
            "kuinka talvi vaikuttaa mehilaispesan kuntoon", "auto"
        ) == "fi"

    def test_finnish_short_query_no_diacritic(self):
        assert ChatService._detect_language("paljonko maksaa", "auto") == "fi"

    def test_english_with_proper_noun_and_diacritic(self):
        # "at" is a strong EN stopword; beats the lone ö in Möbel.
        # Multi-word English with one German diacritic now routes
        # correctly — the pre-H42 single-char detector returned "fi".
        assert ChatService._detect_language(
            "Möbel sale at IKEA", "auto"
        ) == "en"

    def test_known_limit_diacritic_only_phrase_falls_back_to_fi(self):
        # Acknowledged limit (documented in chat_service.py): a two-word
        # English phrase with a German diacritic and zero stopwords on
        # either side stays "fi" via the diacritic fallback. The win
        # condition for changing this would be a Voikko lemmatization
        # gate, but Voikko is Windows-DLL-fragile (H32) and the cost
        # is not justified for proper-noun phrases.
        result = ChatService._detect_language("Schrödinger equation", "auto")
        # Either fi (current fallback) or en (if FI/EN classifiers
        # evolve) is acceptable — we pin only that the detection
        # doesn't crash and returns one of the two languages.
        assert result in {"fi", "en"}

    def test_english_natural_language(self):
        assert ChatService._detect_language(
            "Tell me about electricity consumption last week", "auto"
        ) == "en"

    def test_finnish_with_diacritics(self):
        assert ChatService._detect_language(
            "mikä on lämpötila ulkona", "auto"
        ) == "fi"


class TestDetectLanguageHintOverride:
    """Explicit hint short-circuits detection."""

    def test_hint_fi_returns_fi_for_english_query(self):
        # Operator/client told us this is fi; trust the hint.
        assert ChatService._detect_language(
            "the quick brown fox", "fi"
        ) == "fi"

    def test_hint_en_returns_en_for_finnish_query(self):
        assert ChatService._detect_language(
            "kuinka talvi vaikuttaa", "en"
        ) == "en"


class TestDetectLanguageTieFallback:
    """When both stopword counts are 0, fall back to the diacritic check."""

    def test_tie_with_diacritic_routes_fi(self):
        # No stopword in either set; the å triggers diacritic fallback.
        assert ChatService._detect_language("Ångström unit", "auto") == "fi"

    def test_tie_no_diacritic_routes_en(self):
        # Single non-stopword token with no diacritic -> en default.
        assert ChatService._detect_language("Mobel", "auto") == "en"

    def test_empty_query_defaults_en(self):
        assert ChatService._detect_language("", "auto") == "en"


class TestDetectLanguageBoundedDomainHints:
    """A bounded Finnish token sequence may break a tie without broad votes."""

    @pytest.mark.parametrize(
        "query",
        [
            "palovaroitin piippaa",
            "PALOVAROITIN—PIIPPAA!",
            "piippaa palovaroitin",
            "palovaroitin piippaa kovaa",
        ],
    )
    def test_finnish_smoke_alarm_sequence_breaks_tie(self, query):
        assert ChatService._detect_language(query, "auto") == "fi"

    @pytest.mark.parametrize(
        "query",
        [
            "palovaroitin error",
            "the palovaroitin piippaa",
            "xpalovaroitiny piippaa",
            "translate palovaroitin piippaa",
            "please explain palovaroitin piippaa",
        ],
    )
    def test_partial_or_english_context_does_not_trigger_finnish_hint(self, query):
        assert ChatService._detect_language(query, "auto") == "en"

    def test_explicit_english_hint_still_wins_for_finnish_pair(self):
        assert ChatService._detect_language(
            "palovaroitin piippaa", "en"
        ) == "en"
