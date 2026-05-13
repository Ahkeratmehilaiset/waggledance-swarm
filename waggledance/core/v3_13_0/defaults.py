# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""v3.13.0 default constants.

The values in this module codify the DEF-001..006 defaults from
iterations/anchor_use_case/sprint_1/claude_lane/defaults_lock_in.md.
ProfileConfig and connector-specific overrides may replace them at
runtime; this module is the default floor shared by the runtime.
"""
from __future__ import annotations

from collections.abc import Iterable


# DEF-001: embedding model
DEFAULT_EMBEDDING_MODEL = "intfloat/multilingual-e5-small"
DEFAULT_VECTOR_DIMS = 384
DEFAULT_EMBEDDING_MODEL_SIZE_MB = 134
DEFAULT_EMBEDDING_RUNS_ON_CPU = True


# DEF-002: retrieval cutoffs
DEFAULT_CONTEXT_SIM_THRESHOLD = 0.58
DEFAULT_CONTEXT_TOP_N = 8


# DEF-003: action queue TTL and noise filters
DEFAULT_ACTION_TTL_DAYS = 14

DEFAULT_SKIP_SENDER_PATTERNS_UNIVERSAL = (
    "noreply",
    "no-reply",
    "newsletter",
    "notification",
    "donotreply",
    "mailer-daemon",
    "postmaster",
    "digest",
    "alert@",
    "updates@",
    "linkedin",
    "quora",
    "medium",
)

DEFAULT_SKIP_SENDER_PATTERNS_BY_LOCALE = {
    "fi": ("uutiskirje@", "automaatti@"),
    "sv": ("nyhetsbrev@", "automatiskt@"),
    "de": ("newsletter@", "automatisch@"),
}

DEFAULT_SKIP_SUBJECT_WORDS_UNIVERSAL = (
    "unsubscribe",
    "newsletter",
    "automatic reply",
    "out of office",
    "delivery status",
)

DEFAULT_SKIP_SUBJECT_WORDS_BY_LOCALE = {
    "fi": ("uutiskirje", "automaattinen vastaus", "lomalla"),
    "sv": ("nyhetsbrev", "automatiskt svar", "semester"),
    "de": ("newsletter", "automatische antwort", "urlaub"),
}


# DEF-004: sync overlap for incremental upstream reads
DEFAULT_SYNC_OVERLAP_DAYS = 7


# DEF-005: shared production upstream throttling
DEFAULT_MAX_WORKERS_SHARED_PROD = 3
DEFAULT_REQUEST_DELAY_S = 0.15


# DEF-006: database and date safety defaults
DEFAULT_DATE_FORMAT = "ISO-8601"
DEFAULT_DB_WRITE_MODE = "single-writer + WAL"
DEFAULT_UNPARSED_SENTINEL = "UNPARSED"
DEFAULT_DB_JOURNAL_MODE = "WAL"
DEFAULT_DB_FOREIGN_KEYS = True


def locale_key(locale: str | None) -> str:
    """Return the two-letter overlay key used by default pattern maps."""
    if not locale:
        return ""
    return locale.strip().lower().replace("_", "-").split("-", 1)[0]


def merge_default_patterns(
    universal: Iterable[str],
    by_locale: dict[str, Iterable[str]],
    *,
    locale: str | None = None,
    extra_patterns: Iterable[str] = (),
) -> tuple[str, ...]:
    """Merge universal, locale-specific, and profile-provided patterns.

    Ordering is stable and duplicates are removed case-insensitively.
    ProfileConfig extensions are additive instead of replacing defaults.
    """
    merged: list[str] = []
    seen: set[str] = set()
    for pattern in (
        tuple(universal)
        + tuple(by_locale.get(locale_key(locale), ()))
        + tuple(extra_patterns)
    ):
        normalized = pattern.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        merged.append(pattern)
    return tuple(merged)


def skip_sender_patterns(
    *,
    locale: str | None = None,
    extra_patterns: Iterable[str] = (),
) -> tuple[str, ...]:
    """Return default sender deny-patterns with locale/profile overlays."""
    return merge_default_patterns(
        DEFAULT_SKIP_SENDER_PATTERNS_UNIVERSAL,
        DEFAULT_SKIP_SENDER_PATTERNS_BY_LOCALE,
        locale=locale,
        extra_patterns=extra_patterns,
    )


def skip_subject_words(
    *,
    locale: str | None = None,
    extra_words: Iterable[str] = (),
) -> tuple[str, ...]:
    """Return default subject deny-words with locale/profile overlays."""
    return merge_default_patterns(
        DEFAULT_SKIP_SUBJECT_WORDS_UNIVERSAL,
        DEFAULT_SKIP_SUBJECT_WORDS_BY_LOCALE,
        locale=locale,
        extra_patterns=extra_words,
    )
