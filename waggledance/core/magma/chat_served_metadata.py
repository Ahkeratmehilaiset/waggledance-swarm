# SPDX-License-Identifier: BUSL-1.1
"""Pure metadata normalization for chat-served MAGMA receipts (P2 S1b Phase 2a, T1).

The merged builder validates profile/language/world_snapshot_ref/source/route_type/
agent_id as compact tokens (``^[A-Za-z0-9][A-Za-z0-9:._/-]{0,127}$``) and REJECTS
non-conformers. ChatService.handle passes RAW request-controlled values (req.profile
is user input, verified not normalized), so a raw odd value would fail the builder ->
a receipt gap -> and a metadata gap is USER-TRIGGERABLE (an adversarial user could
send odd profiles to deny claim_safe -- a DoS). These pure functions map every
metadata field to a CONFORMING, HONEST token BEFORE the build:

* a recognized value is recorded as its canonical form (what chat ran under);
* a non-conforming value becomes an honest marker (``unknown`` / ``other``) -- truthful
  that the value was unrecognized, NOT a fake known value, and NOT a gap;
* TRUE gaps are reserved for genuine sink WRITE failures (chat_served_sink), never
  metadata oddities;
* ``world_snapshot_ref`` is a FIXED honest marker -- chat genuinely has no world
  snapshot (verified: no such concept in chat_service), so any derived ref would
  masquerade a non-existent world state (the S1b rejection class).

Design confirmed with rco-1 + rco-2 (three-way profile/language, honest-unknown not
gap, fixed world-snapshot marker, validate-defensively internal tokens).
"""
from __future__ import annotations

import re
from collections.abc import Iterable

# The exact compact-token shape the merged builder enforces.
_METADATA_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._/-]{0,127}$")

# chat has no world snapshot -> a fixed marker is honest-by-construction.
WORLD_SNAPSHOT_NA_MARKER = "chat_service:no_world_snapshot:v0"

# Honest non-conforming markers (valid tokens; truthful, not fake-known values).
# Lower-case so they can never collide with an upper-cased known profile.
PROFILE_UNKNOWN = "unknown"
LANGUAGE_OTHER = "other"
TOKEN_UNKNOWN = "unknown"

# The chat pipeline's first-class languages; anything else -> LANGUAGE_OTHER.
DEFAULT_KNOWN_LANGUAGES = frozenset({"en", "fi"})


def is_conforming_token(value: object) -> bool:
    """True iff ``value`` is a string matching the builder's compact-token shape."""
    return isinstance(value, str) and _METADATA_TOKEN_RE.fullmatch(value) is not None


def normalize_profile(raw_profile: object, known_profiles: Iterable[str]) -> str:
    """Upcase + EXACT match against the CONFIGURED known-profile set.

    Exact (never substring/fuzzy) so an injection like ``"HOME<x>"`` upcased does
    NOT match ``"HOME"``. A recognized profile is recorded in its canonical
    upper-case form; anything else (odd chars, whitespace, over-length, non-str,
    unrecognized) is the honest ``PROFILE_UNKNOWN`` marker -- never a fake known
    profile, never a gap.
    """
    known = {p.upper() for p in known_profiles if isinstance(p, str)}
    if isinstance(raw_profile, str):
        candidate = raw_profile.strip().upper()
        if candidate in known:
            return candidate
    return PROFILE_UNKNOWN


def normalize_language(
    raw_language: object,
    known_languages: Iterable[str] = DEFAULT_KNOWN_LANGUAGES,
) -> str:
    """Lowercase + exact match against the known-language set; else ``LANGUAGE_OTHER``.

    Never records a raw/free-form language (which could be non-conforming or carry
    unexpected content).
    """
    known = {lang.lower() for lang in known_languages if isinstance(lang, str)}
    if isinstance(raw_language, str):
        candidate = raw_language.strip().lower()
        if candidate in known:
            return candidate
    return LANGUAGE_OTHER


def normalize_token(value: object) -> str:
    """Validate-defensively an INTERNAL token (source / route_type).

    A conforming value passes through; a non-conforming (drifted) internal value
    becomes the honest ``TOKEN_UNKNOWN`` marker rather than being trusted (which
    would fail the builder and manufacture a gap). Internal values are not
    user-controlled, but they can still drift, so we validate rather than trust.
    """
    if is_conforming_token(value):
        return value  # type: ignore[return-value]
    return TOKEN_UNKNOWN


def normalize_agent_id(value: object) -> str | None:
    """``agent_id`` may legitimately be ``None`` (the builder allows it). A present
    conforming value passes; a present non-conforming value -> ``TOKEN_UNKNOWN``."""
    if value is None:
        return None
    if is_conforming_token(value):
        return value  # type: ignore[return-value]
    return TOKEN_UNKNOWN


__all__ = [
    "WORLD_SNAPSHOT_NA_MARKER",
    "PROFILE_UNKNOWN",
    "LANGUAGE_OTHER",
    "TOKEN_UNKNOWN",
    "DEFAULT_KNOWN_LANGUAGES",
    "is_conforming_token",
    "normalize_profile",
    "normalize_language",
    "normalize_token",
    "normalize_agent_id",
]
