# SPDX-License-Identifier: BUSL-1.1
"""Tests for the P2 S1b Phase 2a T1 metadata normalization (pure functions).

Covers the liveness+privacy safeguard: request-controlled metadata is mapped to
conforming HONEST tokens, so an adversarial value becomes an "unknown"/"other"
marker (never a user-triggerable gap, never a fake known value), and matching is
EXACT (no substring injection).
"""
from __future__ import annotations

import pytest

from waggledance.core.magma.chat_served_metadata import (
    LANGUAGE_OTHER,
    PROFILE_UNKNOWN,
    TOKEN_UNKNOWN,
    WORLD_SNAPSHOT_NA_MARKER,
    is_conforming_token,
    normalize_agent_id,
    normalize_language,
    normalize_profile,
    normalize_token,
)

_KNOWN_PROFILES = frozenset({"GADGET", "COTTAGE", "HOME", "FACTORY"})


def test_fixed_markers_are_conforming_tokens() -> None:
    for marker in (WORLD_SNAPSHOT_NA_MARKER, PROFILE_UNKNOWN, LANGUAGE_OTHER, TOKEN_UNKNOWN):
        assert is_conforming_token(marker), marker


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HOME", "HOME"),
        ("home", "HOME"),          # upcased
        (" Cottage ", "COTTAGE"),  # trimmed + upcased
        ("FACTORY", "FACTORY"),
    ],
)
def test_normalize_profile_recognized(raw: str, expected: str) -> None:
    assert normalize_profile(raw, _KNOWN_PROFILES) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "HOME<inj>",         # EXACT match, not substring -> unknown
        "my profile",        # whitespace
        "HOME HOME",         # not a single known token
        "x" * 200,           # over-length
        "éè",      # unicode
        "",                  # empty
        "   ",               # whitespace-only
        123,                 # non-str
        None,                # non-str
        "unrecognized",      # simply not a known profile
    ],
)
def test_normalize_profile_non_conforming_is_honest_unknown_not_gap(raw: object) -> None:
    out = normalize_profile(raw, _KNOWN_PROFILES)
    assert out == PROFILE_UNKNOWN
    assert is_conforming_token(out)  # always builder-safe -> no gap
    # honest, not a fake known profile
    assert out.upper() not in _KNOWN_PROFILES


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("en", "en"), ("fi", "fi"), ("EN", "en"), (" Fi ", "fi")],
)
def test_normalize_language_known(raw: str, expected: str) -> None:
    assert normalize_language(raw) == expected


@pytest.mark.parametrize("raw", ["de", "custom", "raw language text", "", 42, None])
def test_normalize_language_unknown_is_other(raw: object) -> None:
    out = normalize_language(raw)
    assert out == LANGUAGE_OTHER
    assert is_conforming_token(out)


def test_normalize_token_defensive() -> None:
    assert normalize_token("solver") == "solver"
    assert normalize_token("hotcache") == "hotcache"
    for bad in ("has space", "x" * 200, "bad\nid", "", 5, None):
        out = normalize_token(bad)
        assert out == TOKEN_UNKNOWN
        assert is_conforming_token(out)


def test_normalize_agent_id_allows_none_but_guards_present() -> None:
    assert normalize_agent_id(None) is None
    assert normalize_agent_id("round_table") == "round_table"
    assert normalize_agent_id("bad id") == TOKEN_UNKNOWN
    assert normalize_agent_id(7) == TOKEN_UNKNOWN


def test_no_raw_text_survives_normalization() -> None:
    # An adversarial value carrying raw text can never reach the receipt as-is:
    # every normalizer returns either a conforming token or (agent_id) None.
    secret = "SECRET raw user text with spaces"
    assert secret not in normalize_profile(secret, _KNOWN_PROFILES)
    assert secret not in normalize_language(secret)
    assert secret not in normalize_token(secret)
    assert (normalize_agent_id(secret) or "") != secret
