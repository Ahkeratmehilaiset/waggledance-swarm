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


# Polish item 14 -- profile-specific tuning resolver tests.


def test_resolve_retrieval_defaults_home_matches_universal() -> None:
    resolved = defaults.resolve_retrieval_defaults("home")
    assert resolved == {
        "context_sim_threshold": defaults.DEFAULT_CONTEXT_SIM_THRESHOLD,
        "context_top_n": defaults.DEFAULT_CONTEXT_TOP_N,
    }


def test_resolve_retrieval_defaults_cottage_tightens_threshold_and_lowers_top_n() -> None:
    resolved = defaults.resolve_retrieval_defaults("cottage")
    assert resolved["context_sim_threshold"] == 0.62
    assert resolved["context_top_n"] == 6
    assert resolved["context_sim_threshold"] > defaults.DEFAULT_CONTEXT_SIM_THRESHOLD
    assert resolved["context_top_n"] < defaults.DEFAULT_CONTEXT_TOP_N


def test_resolve_retrieval_defaults_remote_dwelling_matches_cottage() -> None:
    cottage = defaults.resolve_retrieval_defaults("cottage")
    remote = defaults.resolve_retrieval_defaults("remote_dwelling")
    assert remote == cottage


def test_resolve_retrieval_defaults_factory_broadens_top_n_and_lowers_threshold() -> None:
    resolved = defaults.resolve_retrieval_defaults("factory")
    assert resolved["context_sim_threshold"] == 0.55
    assert resolved["context_top_n"] == 12
    assert resolved["context_sim_threshold"] < defaults.DEFAULT_CONTEXT_SIM_THRESHOLD
    assert resolved["context_top_n"] > defaults.DEFAULT_CONTEXT_TOP_N


def test_resolve_retrieval_defaults_overrides_win_over_profile() -> None:
    resolved = defaults.resolve_retrieval_defaults(
        "cottage",
        overrides={"context_top_n": 4, "context_sim_threshold": 0.71},
    )
    assert resolved == {"context_sim_threshold": 0.71, "context_top_n": 4}


def test_resolve_retrieval_defaults_unknown_profile_falls_back_to_universal() -> None:
    resolved = defaults.resolve_retrieval_defaults("custom")
    assert resolved == {
        "context_sim_threshold": defaults.DEFAULT_CONTEXT_SIM_THRESHOLD,
        "context_top_n": defaults.DEFAULT_CONTEXT_TOP_N,
    }


def test_resolve_retrieval_defaults_none_profile_falls_back_to_universal() -> None:
    resolved = defaults.resolve_retrieval_defaults(None)
    assert resolved == {
        "context_sim_threshold": defaults.DEFAULT_CONTEXT_SIM_THRESHOLD,
        "context_top_n": defaults.DEFAULT_CONTEXT_TOP_N,
    }


def test_resolve_retrieval_defaults_normalizes_profile_kind_case_and_whitespace() -> None:
    a = defaults.resolve_retrieval_defaults("FACTORY")
    b = defaults.resolve_retrieval_defaults(" factory ")
    expected = defaults.resolve_retrieval_defaults("factory")
    assert a == b == expected


def test_resolve_retrieval_defaults_ignores_none_value_and_unknown_override_keys() -> None:
    resolved = defaults.resolve_retrieval_defaults(
        "factory",
        overrides={"context_top_n": None, "not_a_real_field": 999},
    )
    factory = defaults.resolve_retrieval_defaults("factory")
    assert resolved == factory


def test_resolve_embedding_defaults_all_profiles_share_universal_model_for_v1() -> None:
    expected = {
        "model_id": defaults.DEFAULT_EMBEDDING_MODEL,
        "dims": defaults.DEFAULT_VECTOR_DIMS,
    }
    for profile_kind in ("home", "cottage", "remote_dwelling", "factory"):
        assert defaults.resolve_embedding_defaults(profile_kind) == expected


def test_resolve_embedding_defaults_overrides_win_and_unknown_keys_dropped() -> None:
    resolved = defaults.resolve_embedding_defaults(
        "home",
        overrides={
            "model_id": "custom/embedding-v1",
            "dims": 1024,
            "context_top_n": 7,
        },
    )
    assert resolved == {"model_id": "custom/embedding-v1", "dims": 1024}


def test_resolve_embedding_defaults_unknown_profile_falls_back_to_universal() -> None:
    assert defaults.resolve_embedding_defaults("custom") == {
        "model_id": defaults.DEFAULT_EMBEDDING_MODEL,
        "dims": defaults.DEFAULT_VECTOR_DIMS,
    }
