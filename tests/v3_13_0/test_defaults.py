# SPDX-License-Identifier: BUSL-1.1
"""Tests for v3.13.0 default constants."""
from __future__ import annotations

from waggledance.core.v3_13_0 import defaults


def test_embedding_defaults_are_locked() -> None:
    assert defaults.DEFAULT_EMBEDDING_MODEL == "intfloat/multilingual-e5-small"
    assert defaults.DEFAULT_VECTOR_DIMS == 384
    assert defaults.DEFAULT_EMBEDDING_MODEL_SIZE_MB == 134
    assert defaults.DEFAULT_EMBEDDING_RUNS_ON_CPU is True


def test_context_retrieval_defaults_are_locked() -> None:
    assert defaults.DEFAULT_CONTEXT_SIM_THRESHOLD == 0.58
    assert defaults.DEFAULT_CONTEXT_TOP_N == 8


def test_action_queue_defaults_are_locked() -> None:
    assert defaults.DEFAULT_ACTION_TTL_DAYS == 14
    assert "noreply" in defaults.DEFAULT_SKIP_SENDER_PATTERNS_UNIVERSAL
    assert "updates@" in defaults.DEFAULT_SKIP_SENDER_PATTERNS_UNIVERSAL
    assert "unsubscribe" in defaults.DEFAULT_SKIP_SUBJECT_WORDS_UNIVERSAL
    assert "delivery status" in defaults.DEFAULT_SKIP_SUBJECT_WORDS_UNIVERSAL


def test_locale_specific_noise_filters_are_available() -> None:
    assert "uutiskirje@" in defaults.DEFAULT_SKIP_SENDER_PATTERNS_BY_LOCALE["fi"]
    assert "automaatti@" in defaults.DEFAULT_SKIP_SENDER_PATTERNS_BY_LOCALE["fi"]
    assert "nyhetsbrev@" in defaults.DEFAULT_SKIP_SENDER_PATTERNS_BY_LOCALE["sv"]
    assert "automatisch@" in defaults.DEFAULT_SKIP_SENDER_PATTERNS_BY_LOCALE["de"]
    assert "uutiskirje" in defaults.DEFAULT_SKIP_SUBJECT_WORDS_BY_LOCALE["fi"]
    assert "automaattinen vastaus" in (
        defaults.DEFAULT_SKIP_SUBJECT_WORDS_BY_LOCALE["fi"]
    )


def test_sync_and_shared_upstream_defaults_are_locked() -> None:
    assert defaults.DEFAULT_SYNC_OVERLAP_DAYS == 7
    assert defaults.DEFAULT_MAX_WORKERS_SHARED_PROD == 3
    assert defaults.DEFAULT_REQUEST_DELAY_S == 0.15


def test_database_safety_defaults_are_locked() -> None:
    assert defaults.DEFAULT_DATE_FORMAT == "ISO-8601"
    assert defaults.DEFAULT_DB_WRITE_MODE == "single-writer + WAL"
    assert defaults.DEFAULT_UNPARSED_SENTINEL == "UNPARSED"
    assert defaults.DEFAULT_DB_JOURNAL_MODE == "WAL"
    assert defaults.DEFAULT_DB_FOREIGN_KEYS is True


def test_locale_key_accepts_profile_locale_forms() -> None:
    assert defaults.locale_key("fi-FI") == "fi"
    assert defaults.locale_key("sv_SE") == "sv"
    assert defaults.locale_key(" DE ") == "de"
    assert defaults.locale_key(None) == ""


def test_skip_sender_patterns_merge_locale_and_profile_additions() -> None:
    merged = defaults.skip_sender_patterns(
        locale="fi-FI",
        extra_patterns=("custom-noise@", "noreply"),
    )

    assert merged[0] == "noreply"
    assert "uutiskirje@" in merged
    assert "automaatti@" in merged
    assert merged[-1] == "custom-noise@"
    assert merged.count("noreply") == 1


def test_skip_subject_words_merge_locale_and_profile_additions() -> None:
    merged = defaults.skip_subject_words(
        locale="fi",
        extra_words=("poissa toimistolta", "newsletter"),
    )

    assert "unsubscribe" in merged
    assert "uutiskirje" in merged
    assert "automaattinen vastaus" in merged
    assert merged[-1] == "poissa toimistolta"
    assert merged.count("newsletter") == 1


def test_unknown_locale_keeps_universal_and_profile_additions() -> None:
    assert defaults.skip_sender_patterns(locale="zz", extra_patterns=("local",)) == (
        *defaults.DEFAULT_SKIP_SENDER_PATTERNS_UNIVERSAL,
        "local",
    )
