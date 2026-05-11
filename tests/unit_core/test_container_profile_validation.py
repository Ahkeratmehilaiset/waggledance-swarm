# SPDX-License-Identifier: BUSL-1.1
"""Audit H30 regression: Container validates WAGGLE_PROFILE.

A misconfigured WAGGLE_PROFILE (typo, unknown value) used to silently
boot WaggleDance with 0 agents. Container._load_agents now refuses
unknown profiles at the front of the load and logs the expected set.
"""

from __future__ import annotations

import logging

import pytest

from waggledance.bootstrap.container import KNOWN_PROFILES, Container


class _StubSettings:
    def __init__(self, profile: str) -> None:
        self._profile = profile

    def get_profile(self) -> str:
        return self._profile


def _make_container(profile: str) -> Container:
    # Container.__init__ keeps the settings reference verbatim; we don't
    # need a full settings_loader fixture for the _load_agents check.
    return Container(settings=_StubSettings(profile), stub=True)


class TestContainerProfileValidation:
    def test_known_profile_loads_normally(self, caplog):
        """A canonical profile (HOME) loads from agents/ as before."""
        c = _make_container("HOME")
        with caplog.at_level(logging.ERROR):
            agents = c._load_agents()
        # Live agents/ dir has 75 YAMLs in the production repo; in CI
        # without the dir we may load 0, but importantly the error path
        # below should NOT fire.
        assert not any("Unknown WAGGLE_PROFILE" in r.message for r in caplog.records)
        assert isinstance(agents, list)

    def test_unknown_profile_returns_empty_and_logs_error(self, caplog):
        c = _make_container("BOGUS_PROFILE_XYZ")
        with caplog.at_level(logging.ERROR):
            agents = c._load_agents()
        assert agents == []
        assert any(
            "Unknown WAGGLE_PROFILE" in r.message for r in caplog.records
        ), "expected a clear ERROR log naming the bad profile"

    def test_lowercase_known_profile_is_normalized(self, caplog):
        """get_profile().upper() inside Container — lowercase input still maps."""
        c = _make_container("home")
        with caplog.at_level(logging.ERROR):
            agents = c._load_agents()
        # No "Unknown" error for lowercase "home" — the .upper() at call
        # site normalizes to "HOME" which is in KNOWN_PROFILES.
        assert not any("Unknown WAGGLE_PROFILE" in r.message for r in caplog.records)

    def test_known_profiles_set_pins_canonical_four(self):
        """Pin the four shipped profiles; a fifth should be a deliberate addition."""
        assert KNOWN_PROFILES == frozenset({"GADGET", "COTTAGE", "HOME", "FACTORY"})
