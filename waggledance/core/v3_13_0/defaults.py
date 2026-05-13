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
from typing import Any


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


# Polish item 14: profile-specific tuning.
#
# Per-profile recommended overlay values for the override fields that
# ProfileConfig schema already exposes (retrieval_overrides,
# embedding_overrides). Sprint 2+ may add per-profile values for the
# other DEF-* areas; for v3.13.0 polish scope, only retrieval +
# embedding are differentiated.
#
# Rationale for the per-profile retrieval values:
#   home              -- universal defaults; mainstream interactive use.
#   cottage           -- tighter threshold (0.62) and smaller top_n (6)
#                        to keep retrieval cheap and noise-free on the
#                        low-power local hardware typical of cottage
#                        deployments; matches the COTTAGE dry-run
#                        runbook's lean-substrate assumption.
#   remote_dwelling   -- same as cottage; offline-first low-bandwidth
#                        deployment shares the COTTAGE cost profile.
#   factory           -- broader top_n (12) and lower threshold (0.55)
#                        to support production-decision workflows where
#                        recall matters more than per-call cost and the
#                        host typically has more compute headroom.
#
# Embedding overlays for v3.13.0 polish: all profiles share the
# DEF-001 universal embedding model. The resolver still threads
# embedding overrides for the override-merge pattern so future polish
# can differentiate without changing call sites.

PROFILE_RETRIEVAL_DEFAULTS: dict[str, dict[str, Any]] = {
    "home": {
        "context_sim_threshold": DEFAULT_CONTEXT_SIM_THRESHOLD,
        "context_top_n": DEFAULT_CONTEXT_TOP_N,
    },
    "cottage": {
        "context_sim_threshold": 0.62,
        "context_top_n": 6,
    },
    "remote_dwelling": {
        "context_sim_threshold": 0.62,
        "context_top_n": 6,
    },
    "factory": {
        "context_sim_threshold": 0.55,
        "context_top_n": 12,
    },
}

PROFILE_EMBEDDING_DEFAULTS: dict[str, dict[str, Any]] = {
    "home": {
        "model_id": DEFAULT_EMBEDDING_MODEL,
        "dims": DEFAULT_VECTOR_DIMS,
    },
    "cottage": {
        "model_id": DEFAULT_EMBEDDING_MODEL,
        "dims": DEFAULT_VECTOR_DIMS,
    },
    "remote_dwelling": {
        "model_id": DEFAULT_EMBEDDING_MODEL,
        "dims": DEFAULT_VECTOR_DIMS,
    },
    "factory": {
        "model_id": DEFAULT_EMBEDDING_MODEL,
        "dims": DEFAULT_VECTOR_DIMS,
    },
}

_RETRIEVAL_KEYS = frozenset({"context_sim_threshold", "context_top_n"})
_EMBEDDING_KEYS = frozenset({"model_id", "dims"})


def _normalize_profile_kind(profile_kind: str | None) -> str:
    """Lower/strip profile_kind; empty string means no per-profile overlay."""
    if not profile_kind:
        return ""
    return profile_kind.strip().lower()


def _resolve_with_overlay(
    universal: dict[str, Any],
    profile_map: dict[str, dict[str, Any]],
    allowed_keys: frozenset[str],
    profile_kind: str | None,
    overrides: dict[str, Any] | None,
) -> dict[str, Any]:
    profile_defaults = profile_map.get(_normalize_profile_kind(profile_kind), {})
    resolved = dict(universal)
    resolved.update(profile_defaults)
    if overrides:
        for key, value in overrides.items():
            if key not in allowed_keys:
                continue
            if value is None:
                continue
            resolved[key] = value
    return resolved


def resolve_retrieval_defaults(
    profile_kind: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve retrieval defaults for a profile_kind with caller overrides.

    Order of precedence (highest wins): overrides > per-profile defaults
    > universal DEF-002 defaults. Unknown profile_kind falls back to
    universal. Overrides whose value is ``None`` or whose key is outside
    the ProfileConfig.retrieval_overrides slot are silently ignored.
    """
    universal = {
        "context_sim_threshold": DEFAULT_CONTEXT_SIM_THRESHOLD,
        "context_top_n": DEFAULT_CONTEXT_TOP_N,
    }
    return _resolve_with_overlay(
        universal,
        PROFILE_RETRIEVAL_DEFAULTS,
        _RETRIEVAL_KEYS,
        profile_kind,
        overrides,
    )


def resolve_embedding_defaults(
    profile_kind: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve embedding defaults for a profile_kind with caller overrides.

    Order of precedence (highest wins): overrides > per-profile defaults
    > universal DEF-001 defaults. For v3.13.0 polish all profiles share
    the universal embedding model; the resolver threads the override
    pattern so future polish can differentiate without changing call
    sites. Overrides whose value is ``None`` or whose key is outside the
    ProfileConfig.embedding_overrides slot are silently ignored.
    """
    universal = {
        "model_id": DEFAULT_EMBEDDING_MODEL,
        "dims": DEFAULT_VECTOR_DIMS,
    }
    return _resolve_with_overlay(
        universal,
        PROFILE_EMBEDDING_DEFAULTS,
        _EMBEDDING_KEYS,
        profile_kind,
        overrides,
    )
